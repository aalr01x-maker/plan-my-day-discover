"""Musica: the owner's 8 genres, three Top 20 lists each.

Songs have no reliable public "quality" rating, so the weights apply only to what
can be measured (owner's rule):

  Best of All Time   listeners (Last.fm's all-time listeners where known, log scale)
                     85% + genre fit 15%
  Best Recent        released in the last 12 months; listeners 75% + genre fit 25%
  Trending (7 days)  the most recent measured momentum 70% + listeners 30%:
                     listen growth between our daily snapshots when we have it,
                     else listens on ListenBrainz's weekly chart, else listeners
                     per week since release. Each card says which one ranked it.

A song counts for a genre only if MusicBrainz tags the recording, its release or
its artist with one of the genre's tags (checked, not assumed from search).

Sources (no scraping): ListenBrainz tag radio and MusicBrainz search (genre
candidates; MusicBrainz also finds releases of the last 12 months), ListenBrainz
metadata (names, release dates, cover art ids) and popularity (listen / user
counts); Last.fm tag charts add candidates when LASTFM_API_KEY is set; iTunes
Search adds artwork, 30 s previews, release dates and Apple Music links; YouTube
official videos are matched for the top songs within a small daily quota.
"""
from __future__ import annotations

import math
import re
import time
from datetime import timedelta
from urllib.parse import quote_plus

from . import common
from .youtube import YouTube, best_match, embed

LB = "https://api.listenbrainz.org/1"
LASTFM = "https://ws.audioscrobbler.com/2.0/"
ITUNES = "https://itunes.apple.com/search"
MB = "https://musicbrainz.org/ws/2/recording"
STATE = common.STATE / "music.json"

GENRES = {
    "progressive-house": ("Progressive / Organic House", ["progressive house", "organic house", "melodic house"]),
    "chillwave-trip-hop": ("Uplifting Chillwave / Trip Hop", ["chillwave", "trip hop", "downtempo"]),
    "indie-electro": ("Indie Electro", ["indietronica", "indie electronic", "indie electronica"]),
    "ambient-pop": ("Ambient Pop / Ethereal Vocals", ["dream pop", "ambient pop", "ethereal"]),
    "electro-jazz-soul": ("Electro-Jazz / Electro Soul", ["nu jazz", "electro soul", "future soul", "electro swing"]),
    "retrowave": ("Retrowave", ["synthwave", "retrowave", "outrun"]),
    "nu-disco": ("Nu-Disco / Chill Dance", ["nu disco", "nu-disco", "disco house"]),
    "conscious-rap": ("Conscious Rap / Mellow Vocal Hip-Hop", ["conscious hip hop", "jazz rap", "alternative hip hop"]),
}
# The artist must belong to the genre's family too (their own MusicBrainz tags), so one stray tag
# can't put a pop song under Progressive House. Artists with no tags at all need a strong song tag.
FAMILY = {
    "progressive-house": r"house|progressive|melodic techno|trance",
    "chillwave-trip-hop": r"chillwave|trip ?hop|downtempo|chill|lo ?fi|electronica|ambient|nu jazz",
    "indie-electro": r"indie|electro|synth|electronic",
    "ambient-pop": r"dream ?pop|ambient|ethereal|shoegaze|art pop|chamber pop|indie pop|electropop",
    "electro-jazz-soul": r"jazz|soul|swing|funk|neo soul",
    "retrowave": r"synthwave|retrowave|outrun|darksynth|vaporwave|synth",
    "nu-disco": r"disco|house|funk|dance|electro ?pop",
    "conscious-rap": r"hip ?hop|rap",
}
STRONG_FIT = 0.8

PER_ARTIST = 2            # a list shows at most 2 songs by one artist
TAG_LEVELS = {"recording": 1.0, "release_group": 0.8, "artist": 0.6}
ITUNES_PER_RUN = 150      # Apple asks for ~20 requests/minute; the cache fills over a few days


def _candidates() -> dict[str, dict]:
    """genre -> {mbid: fit}; fit = best tag match strength 0..1 across the genre's tags."""
    out: dict[str, dict] = {}
    for gid, (_, tags) in GENRES.items():
        pool: dict[str, float] = {}
        for tag in tags:
            for band in ((0, 50), (50, 100)):  # popular and less-known halves, so both appear
                try:
                    rows = common.get(f"{LB}/lb-radio/tags", params={
                        "tag": tag, "operator": "OR", "pop_begin": band[0], "pop_end": band[1], "count": 100})
                except common.FetchError as exc:
                    common.log(f"music: tag {tag!r} failed: {exc}")
                    continue
                for r in rows or []:
                    fit = min(1.0, (r.get("percent") or 0) / 100 * (0.6 + 0.4 * min(r.get("tag_count") or 1, 5) / 5))
                    pool[r["recording_mbid"]] = max(pool.get(r["recording_mbid"], 0), fit)
        out[gid] = pool
    return out


def _recent_candidates(since: str, until: str) -> dict[str, set]:
    """genre -> mbids of recordings first released in the window (MusicBrainz search, 1 request/s)."""
    out: dict[str, set] = {}
    for gid, (_, tags) in GENRES.items():
        found: set[str] = set()
        for tag in tags:
            for offset in (0, 100):
                time.sleep(1.1)
                try:
                    data = common.get(MB, params={"query": f'tag:"{tag}" AND firstreleasedate:[{since} TO {until}]',
                                                  "fmt": "json", "limit": 100, "offset": offset})
                except common.FetchError as exc:
                    common.log(f"music: musicbrainz {tag!r}: {exc}")
                    break
                recs = data.get("recordings") or []
                found.update(r["id"] for r in recs)
                if len(recs) < 100:
                    break
        out[gid] = found
    return out


def _lastfm_candidates(key: str) -> dict[str, dict]:
    """genre -> {mbid: fit} from Last.fm tag charts (tracks with a MusicBrainz id only)."""
    out: dict[str, dict] = {}
    for gid, (_, tags) in GENRES.items():
        pool: dict[str, float] = {}
        for tag in tags:
            try:
                data = common.get(LASTFM, params={"method": "tag.gettoptracks", "tag": tag, "limit": 100,
                                                  "api_key": key, "format": "json"})
            except common.FetchError as exc:
                common.log(f"music: last.fm {tag!r}: {exc}")
                continue
            tracks = (data.get("tracks") or {}).get("track") or []
            for rank, t in enumerate(tracks):
                if t.get("mbid"):
                    pool[t["mbid"]] = max(pool.get(t["mbid"], 0), 1 - rank / 200)
        out[gid] = pool
    return out


def _chunks(seq, n):
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def _metadata(mbids: set[str], cache: dict) -> None:
    todo = [m for m in mbids if m not in cache or "atags" not in cache[m]]   # "atags" added 31 Oct 2026
    for batch in _chunks(todo, 50):
        try:
            data = common.get(f"{LB}/metadata/recording/", params={"recording_mbids": ",".join(batch), "inc": "artist release tag"})
        except common.FetchError as exc:
            common.log(f"music: metadata batch failed: {exc}")
            continue
        for mbid, m in (data or {}).items():
            rec, rel, art = m.get("recording") or {}, m.get("release") or {}, m.get("artist") or {}
            yt_channel = next((a.get("rels", {}).get("youtube") for a in art.get("artists", []) if a.get("rels", {}).get("youtube")), None)
            cache[mbid] = {
                "title": rec.get("name"), "artist": art.get("name"), "album": rel.get("name"),
                "released": rec.get("first_release_date") or (str(rel["year"]) if rel.get("year") else None),
                "cover": f"https://coverartarchive.org/release/{rel['caa_release_mbid']}/front-250"
                         if rel.get("caa_release_mbid") and rel.get("caa_id") else None,
                "artist_youtube": yt_channel,
                # {tag: strength} -- recording tags count most, then release, then artist
                "tags": _tag_strengths(m.get("tag") or {}),
                "atags": sorted({(t.get("tag") or "").lower().replace("-", " ") for t in (m.get("tag") or {}).get("artist") or []}),
            }


def _tag_strengths(tags: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    for level, weight in TAG_LEVELS.items():
        for t in tags.get(level) or []:
            name = (t.get("tag") or "").lower().replace("-", " ")
            strength = weight * min(1.0, 0.5 + 0.1 * (t.get("count") or 1))
            out[name] = max(out.get(name, 0), round(strength, 3))
    return out


def _fit(meta: dict, tags: list[str]) -> float:
    strengths = meta.get("tags") or {}
    return max((strengths.get(t.replace("-", " "), 0) for t in tags), default=0)


def _popularity(mbids: set[str]) -> dict[str, dict]:
    out = {}
    for batch in _chunks(mbids, 400):
        try:
            rows = common.post_json(f"{LB}/popularity/recording", {"recording_mbids": batch})
        except common.FetchError as exc:
            common.log(f"music: popularity batch failed: {exc}")
            continue
        for r in rows or []:
            if r.get("total_listen_count") is not None:
                out[r["recording_mbid"]] = {"listens": r["total_listen_count"], "users": r.get("total_user_count") or 0}
    return out


LASTFM_PER_RUN = 2500    # track.getInfo calls (Last.fm allows ~5/s); cached 7 days


def _lastfm_info(song: dict, key: str, cache: dict, budget: list[int]) -> dict | None:
    """Last.fm's all-time listeners and plays for a song (millions of users since 2002)."""
    ck = common.norm(f"{song['artist']} {song['title']}").strip()
    hit = cache.get(ck)
    if hit and common.within(common.parse_time(hit["at"]), 7 * 24):
        return hit["d"]
    if budget[0] <= 0:
        return hit["d"] if hit else None
    budget[0] -= 1
    time.sleep(0.22)
    try:
        data = common.get(LASTFM, params={"method": "track.getInfo", "artist": song["artist"], "track": song["title"],
                                          "autocorrect": 1, "api_key": key, "format": "json"})
    except common.FetchError as exc:
        common.log(f"music: last.fm info: {exc}")
        return hit["d"] if hit else None
    t = (data or {}).get("track") or {}
    d = {"listeners": int(t.get("listeners") or 0), "plays": int(t.get("playcount") or 0), "url": t.get("url")} \
        if t.get("listeners") else None
    cache[ck] = {"at": common.iso(common.now_utc()), "d": d}
    return d


YOUTUBE_LOOKUPS = 10      # YouTube searches per day (100 of the 10,000 free daily units each): a last resort; songs are found on the artist's channel instead
NOT_OFFICIAL = re.compile(r"\b(cover|karaoke|reaction|tutorial|lesson|instrumental version|8d|slowed|sped up|nightcore)\b", re.I)


SKIP_VERSION = re.compile(r"\b(live|remix|edit|teaser|trailer|behind the scenes|interview|making of|snippet|visualiser|visualizer)\b", re.I)
CHANNELS_PER_RUN = 500    # artist channels read per day (1-4 quota units each)
YOUTUBE_CEILING = 7000     # music stops new lookups once the run has used this many of the 10,000 daily units (YouTube sells no extra quota)


def _artist_channel(yt: YouTube, url: str, cache: dict) -> str | None:
    """The artist's official channel id from the YouTube link MusicBrainz lists for them."""
    if url in cache:
        return cache[url]
    path = url.split("youtube.com/")[-1].strip("/")
    cid = None
    try:
        if path.startswith("channel/"):
            cid = path.split("/")[1]
        else:
            params = {"part": "id", "key": yt.key}
            if path.startswith("user/"):
                params["forUsername"] = path.split("/")[1]
            else:
                params["forHandle"] = path.split("/")[0] if path.startswith("@") else "@" + path.split("/")[-1]
            data = yt.get("channels", params)
            yt.units += 1
            cid = ((data.get("items") or [{}])[0]).get("id")
    except common.FetchError:
        return None          # not cached: tried again on a later day
    cache[url] = cid
    return cid


def _guess_channel(yt: YouTube, artist: str, cache: dict) -> str | None:
    """The artist's channel when MusicBrainz lists none: try @Name, @NameOfficial and @NameVEVO
    (1 quota unit each, instead of a 100-unit search) and accept one only if its title is the artist
    and it has 10,000+ subscribers (not a fan or impostor account)."""
    key = "guess:" + common.norm(artist)
    if key in cache:
        return cache[key]
    base = re.sub(r"[^0-9A-Za-z]", "", artist)
    want = common.norm(artist).replace(" ", "")
    cid = None
    for handle in ([f"@{base}", f"@{base}Official", f"@{base}VEVO"] if len(base) >= 3 else []):
        try:
            data = yt.get("channels", {"part": "snippet,statistics", "forHandle": handle})
        except common.FetchError:
            return None      # not cached: tried again on a later day
        yt.units += 1
        item = (data.get("items") or [None])[0]
        title = common.norm((item or {}).get("snippet", {}).get("title", "")).replace(" ", "")
        stats = (item or {}).get("statistics", {})
        big = stats.get("hiddenSubscriberCount") or int(stats.get("subscriberCount", 0)) >= 10_000
        if item and big and title.replace("official", "").replace("vevo", "") == want:
            cid = item["id"]
            break
    cache[key] = cid
    return cid


def _todo(entry: dict | None, now) -> bool:
    """Not looked up yet, or found nothing more than 30 days ago (worth another try)."""
    if entry is None:
        return True
    if entry.get("id"):
        return False
    return entry.get("none", "") < (now - timedelta(days=30)).strftime("%Y-%m-%d")


def _match(song: dict, videos: list[dict]) -> list[dict]:
    """Uploads that are this song (not a live, remix, cover or teaser version)."""
    title = common.norm(song["title"].split(" (")[0]).strip()
    wants_version = SKIP_VERSION.search(song["title"])
    out = []
    for v in videos:
        vt = common.norm(v["title"])
        if not title or f" {title} " not in f" {vt} ":
            continue
        if NOT_OFFICIAL.search(v["title"]) or (SKIP_VERSION.search(v["title"]) and not wants_version):
            continue
        out.append(v)
    return out


_RUN_UPLOADS: dict[str, list[dict]] = {}   # channel id -> its uploads, this run only


def _youtube_video(yt: YouTube, song: dict, state: dict, search_budget: list[int], channel_budget: list[int]) -> dict | None:
    """The song's most-viewed official upload: its music video, official audio or the artist's
    auto-generated "Topic" track. Covers, reactions and fan re-uploads don't count.
    First the artist's own channel (listed by MusicBrainz, else found by its @handle; reading it
    costs 1-4 quota units), then a YouTube search (100 units) only if that finds nothing."""
    candidates = []
    url = song.get("artist_youtube")
    if channel_budget[0] > 0 and yt.units < YOUTUBE_CEILING:
        ids = state.setdefault("yt_channel_ids", {})
        cid = _artist_channel(yt, url, ids) if url else _guess_channel(yt, song["artist"], ids)
        if cid:
            # upload lists are kept for this run only (saving them grew the state file by MBs a day);
            # re-reading a channel on a later day costs just 1-4 units
            videos = _RUN_UPLOADS.get(cid)
            if videos is None:
                channel_budget[0] -= 1
                vids = yt.uploads(cid, pages=4)
                if yt.exhausted:
                    return "later"
                videos = _RUN_UPLOADS[cid] = [{"id": v["id"], "title": v["title"], "channel": v.get("channel"),
                                               "published": v.get("published")} for v in vids]
            candidates = _match(song, videos)
    if not candidates:
        if search_budget[0] <= 0 or yt.exhausted or yt.units >= YOUTUBE_CEILING:
            return "later"
        search_budget[0] -= 1
        found = yt.search(f"{song['artist']} {song['title']}", max_age_days=36500)
        if yt.exhausted:
            return "later"
        artist = common.norm(song["artist"])
        for v in _match(song, found):
            channel = common.norm(v.get("channel") or "")
            if (artist and (artist in channel or channel.replace(" topic", "") in artist)) or "vevo" in channel:
                candidates.append(v)
    if not candidates:
        return None
    stats = _youtube_views(yt, [v["id"] for v in candidates])
    if yt.exhausted and not stats:
        return "later"
    best = max(candidates, key=lambda v: stats.get(v["id"], 0))
    return {"id": best["id"], "title": best["title"], "channel": best.get("channel"), "published": best.get("published")}


def _youtube_views(yt: YouTube, ids: list[str]) -> dict[str, int]:
    out = {}
    for i in range(0, len(ids), 50):
        try:
            data = yt.get("videos", {"part": "statistics", "id": ",".join(ids[i:i + 50])})
            yt.units += 1
        except common.FetchError as exc:
            common.log(f"music: youtube stats: {exc}")
            continue
        for item in data.get("items", []):
            out[item["id"]] = int(item.get("statistics", {}).get("viewCount", 0))
    return out


def _week_chart() -> dict:
    """This week's ListenBrainz top 1,000 recordings: {mbid or 'artist title': listens this week}."""
    try:
        recs = common.get(f"{LB}/stats/sitewide/recordings", params={"range": "week", "count": 1000})["payload"]["recordings"]
    except (common.FetchError, KeyError, TypeError) as exc:
        common.log(f"music: weekly chart unavailable: {exc}")
        return {}
    out = {}
    for r in recs:
        if r.get("recording_mbid"):
            out[r["recording_mbid"]] = r["listen_count"]
        out[common.norm(f"{r.get('artist_name', '')} {r.get('track_name', '')}").strip()] = r["listen_count"]
    return out


def _itunes(song: dict, cache: dict, budget: list[int]) -> dict | None:
    key = common.norm(f"{song['artist']} {song['title']}").strip()
    if key in cache:
        return cache[key]
    if budget[0] <= 0:
        return None
    budget[0] -= 1
    time.sleep(3.1)
    try:
        data = common.get(ITUNES, params={"term": f"{song['artist']} {song['title']}", "entity": "song", "limit": 5})
    except common.FetchError as exc:
        common.log(f"music: itunes: {exc}")
        return None
    hit = None
    for r in data.get("results", []):
        # the same song by the same artist -- not a cover or a different track
        if common.similarity(r.get("trackName", ""), song["title"]) >= 0.5 and \
                common.similarity(r.get("artistName", ""), song["artist"]) >= 0.5:
            hit = {"apple_url": r.get("trackViewUrl"), "preview": r.get("previewUrl"),
                   "artwork": (r.get("artworkUrl100") or "").replace("100x100", "300x300") or None,
                   "released": (r.get("releaseDate") or "")[:10] or None, "genre": r.get("primaryGenreName")}
            break
    cache[key] = hit
    return hit


def _links(song: dict, apple: dict | None) -> list[dict]:
    q = quote_plus(f"{song['artist']} {song['title']}")
    links = []
    if apple and apple.get("apple_url"):
        links.append({"name": "Apple Music", "url": apple["apple_url"]})
    links += [
        {"name": "Spotify (search)", "url": f"https://open.spotify.com/search/{q}"},
        {"name": "YouTube Music (search)", "url": f"https://music.youtube.com/search?q={q}"},
        {"name": "ListenBrainz", "url": f"https://listenbrainz.org/player/?recording_mbids={song['mbid']}"},
    ]
    return links


def build(yt: YouTube | None = None) -> dict:
    now = common.now_utc()
    today = now.strftime("%Y-%m-%d")
    state = common.load_json(STATE, {"meta": {}, "itunes": {}, "videos": {}, "history": {}})
    pools = _candidates()
    key = common.env("LASTFM_API_KEY")
    if key:
        for gid, extra in _lastfm_candidates(key).items():
            for mbid, fit in extra.items():
                pools[gid][mbid] = max(pools[gid].get(mbid, 0), fit)
    recent_pools = _recent_candidates((now - timedelta(days=365)).strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d"))
    for gid, mbids in recent_pools.items():
        for mbid in mbids:
            pools[gid].setdefault(mbid, 0)
    every = set().union(*pools.values())
    if not every:
        raise RuntimeError("no music candidates fetched")
    _metadata(every, state["meta"])
    pop = _popularity(every)
    week_chart = _week_chart()
    common.log(f"music: {len(every)} candidate songs, popularity for {len(pop)}")

    itunes_budget, lastfm_budget = [ITUNES_PER_RUN], [LASTFM_PER_RUN]
    state.setdefault("lastfm", {})
    state.setdefault("yt", {})        # song -> its YouTube video (kept for good), or {"none": date} retried after 30 days
    state.setdefault("yt_views", {})  # video id -> {date: views}, last 10 days
    year_ago = (now - timedelta(days=365)).strftime("%Y-%m-%d")
    two_years = (now - timedelta(days=730)).strftime("%Y-%m-%d")

    def skey(s):
        return common.norm(f"{s['artist']} {s['title']}").strip()

    # 1. Each genre's songs and its candidates for the three lists. ListenBrainz/MusicBrainz say which
    #    songs belong to the genre; Last.fm's audience pre-sorts them. Neither number is shown:
    #    the lists are ranked and labelled by YouTube views.
    prepared = {}
    for gid, (name, tags) in GENRES.items():
        seen: dict[str, dict] = {}
        for mbid in pools[gid]:
            meta, p = state["meta"].get(mbid), pop.get(mbid)
            if not meta or not meta.get("title") or not meta.get("artist") or not p or p["users"] < 1:
                continue
            fit = _fit(meta, tags)
            if fit <= 0:  # not actually tagged with this genre
                continue
            atags = " | ".join(meta.get("atags") or [])
            if not (re.search(FAMILY[gid], atags) if atags else fit >= STRONG_FIT):
                continue   # the artist isn't in this genre's family
            song = {"mbid": mbid, "fit": fit, **meta, **p}
            # one entry per song (remasters, instrumentals and re-releases are separate recordings)
            same = common.norm(meta["artist"] + " " + meta["title"].split(" (")[0]).strip()
            if same not in seen or seen[same]["users"] < song["users"]:
                seen[same] = song
        songs = list(seen.values())
        if key:
            wanted = sorted(songs, key=lambda x: -x["users"])[:150] + [x for x in songs if (x.get("released") or "") >= two_years]
            for x in {id(w): w for w in wanted}.values():
                info = _lastfm_info(x, key, state["lastfm"], lastfm_budget)
                if info:
                    x["lfm"] = info

        def internal(x):  # pre-sort only: Last.fm listeners, else ListenBrainz users on a similar scale
            return x["lfm"]["listeners"] if x.get("lfm") else x["users"] * 50

        def capped(ranked, n, per=PER_ARTIST):
            count: dict[str, int] = {}
            out = []
            for x in ranked:
                if count.get(x["artist"], 0) < per:
                    count[x["artist"]] = count.get(x["artist"], 0) + 1
                    out.append(x)
                if len(out) >= n:
                    break
            return out

        top_internal = max((internal(x) for x in songs), default=1) or 1
        at = capped(sorted(songs, key=lambda x: -(0.85 * common.log_scale(internal(x), top_internal) + 0.15 * x["fit"])), 40, 3)
        rc = capped(sorted([x for x in songs if len(x.get("released") or "") == 10 and x["released"] >= year_ago],
                           key=lambda x: -internal(x)), 40, 3)
        tr = capped(sorted([x for x in songs if (x.get("released") or "") >= two_years
                            or week_chart.get(x["mbid"]) or week_chart.get(skey(x))], key=lambda x: -internal(x)), 30, 3)
        prepared[gid] = (name, tags, songs, at, rc, tr)

    # 2. Find each candidate's official YouTube video, at most YOUTUBE_LOOKUPS new songs a day,
    #    taking genres in turn so every genre fills at the same pace.
    lookups = [0]
    search_budget, channel_budget = [YOUTUBE_LOOKUPS], [CHANNELS_PER_RUN]
    if yt and yt.available:
        queues = [[x for trio in zip(at, rc + [None] * 40, tr + [None] * 40) for x in trio if x]
                  for (_, _, _, at, rc, tr) in prepared.values()]
        for i in range(max((len(q) for q in queues), default=0)):
            for q in queues:
                if i < len(q) and _todo(state["yt"].get(skey(q[i])), now):
                    found = _youtube_video(yt, q[i], state, search_budget, channel_budget)
                    if found != "later":   # "later": needs a search, and today's searches are used up
                        lookups[0] += 1
                        state["yt"][skey(q[i])] = found or {"none": today}

    # 3. Today's view counts for every known video (1 quota unit per 50 videos)
    ids = sorted({v["id"] for v in state["yt"].values() if v.get("id")})
    views = _youtube_views(yt, ids) if yt and yt.available else {}
    for vid, n in views.items():
        h = state["yt_views"].setdefault(vid, {})
        h[today] = n
    cut = (now - timedelta(days=10)).strftime("%Y-%m-%d")
    state["yt_views"] = {v: {d: n for d, n in h.items() if d >= cut} for v, h in state["yt_views"].items() if v in ids}

    def yt_of(x):
        v = state["yt"].get(skey(x)) or {}
        if not v.get("id"):
            return None
        n = views.get(v["id"]) or max((state["yt_views"].get(v["id"]) or {"": 0}).values())
        return {**v, "views": n} if n else None

    def growth(vid):
        h = state["yt_views"].get(vid) or {}
        days = sorted(d for d in h if d >= (now - timedelta(days=7)).strftime("%Y-%m-%d"))
        if len(days) < 2:
            return None
        span = (common.parse_time(days[-1]) - common.parse_time(days[0])).days or 1
        return (h[days[-1]] - h[days[0]]) * 7 / span, span   # views per 7 days

    pending = sum(1 for (_, _, _, at, rc, tr) in prepared.values() for x in at + rc + tr if _todo(state["yt"].get(skey(x)), now))

    # 4. Rank by YouTube views
    genres = {}
    for gid, (name, tags, songs, at, rc, tr) in prepared.items():
        def with_yt(cands):
            out = []
            for x in cands:
                v = yt_of(x)
                if v:
                    out.append({**x, "yt": v})
            return out

        def top(rows, score, n=20):
            per: dict[str, int] = {}
            out = []
            for x in sorted(rows, key=score, reverse=True):
                if per.get(x["artist"], 0) < PER_ARTIST:
                    per[x["artist"]] = per.get(x["artist"], 0) + 1
                    out.append(x)
                if len(out) >= n:
                    break
            return out

        a_rows, r_rows = with_yt(at), with_yt(rc)
        max_a = max((x["yt"]["views"] for x in a_rows), default=1)
        max_r = max((x["yt"]["views"] for x in r_rows), default=1)
        all_time = top(a_rows, lambda x: 0.85 * common.log_scale(x["yt"]["views"], max_a) + 0.15 * x["fit"])
        recent = top(r_rows, lambda x: 0.75 * common.log_scale(x["yt"]["views"], max_r) + 0.25 * x["fit"])

        # Trending: views gained per week (between our daily counts), else views per week since the
        # video went up when it is under 2 years old
        def momentum(x):
            g = growth(x["yt"]["id"])
            if g and g[0] > 0:
                return g[0], f"+{g[0]:,.0f} YouTube views per week (measured over {g[1]} day{'s' if g[1] != 1 else ''})"
            up = common.parse_time(x["yt"].get("published"))
            if up and (now - up).days <= 730:
                weeks = max(1.0, (now - up).days / 7)
                return x["yt"]["views"] / weeks, f"{x['yt']['views'] / weeks:,.0f} YouTube views per week since it was posted ({up:%b %Y})"
            return None

        t_rows = []
        for x in with_yt({id(y): y for y in tr + at + rc}.values()):
            m = momentum(x)
            if m:
                t_rows.append({**x, "mom": m})
        max_m = max((x["mom"][0] for x in t_rows), default=1)
        max_t = max((x["yt"]["views"] for x in t_rows), default=1)
        trending = top(t_rows, lambda x: 0.7 * common.log_scale(x["mom"][0], max_m) + 0.3 * common.log_scale(x["yt"]["views"], max_t))

        def card(x: dict, rank: int, why: str) -> dict:
            apple = _itunes(x, state["itunes"], itunes_budget)
            v = x["yt"]
            return {
                "id": x["mbid"], "rank": rank, "title": x["title"], "artist": x["artist"], "album": x.get("album"),
                "released": (apple or {}).get("released") or x.get("released"),
                "artwork": (apple or {}).get("artwork") or x.get("cover"),
                "preview": (apple or {}).get("preview"),
                "metrics": {"views": v["views"], "source": "YouTube", "channel": v.get("channel"), "genre_fit": round(x["fit"], 2)},
                "why": why, "video": embed({**v, "published": v.get("published")}),
                "links": [{"name": "YouTube", "url": f"https://www.youtube.com/watch?v={v['id']}"}] + _links(x, apple),
            }

        lists = {
            "all_time": [card(x, i + 1, f"{x['yt']['views']:,} views on YouTube ({x['yt'].get('channel')}); "
                                        f"genre-tag fit {x['fit']:.0%}.") for i, x in enumerate(all_time)],
            "recent": [card(x, i + 1, f"Released {x['released']}; {x['yt']['views']:,} views on YouTube.") for i, x in enumerate(recent)],
            "trending": [card(x, i + 1, f"{x['mom'][1]}; {x['yt']['views']:,} views in total.") for i, x in enumerate(trending)],
        }
        notes = {}
        for k, lst in lists.items():
            if len(lst) < 20 and pending:
                notes[k] = (f"{len(lst)} songs verified on YouTube so far; more are looked up every day "
                            f"(YouTube's free limit allows about {YOUTUBE_LOOKUPS} a day), so this list grows over the first days.")
        if len(lists["recent"]) < 20 and not notes.get("recent"):
            notes["recent"] = f"{len(lists['recent'])} songs in this genre from the last 12 months have an official YouTube video."
        genres[gid] = {"name": name, "tags": tags, **lists, "notes": notes}
        common.log(f"music {gid}: {len(songs)} songs; {len(lists['all_time'])}/{len(lists['recent'])}/{len(lists['trending'])}")

    common.log(f"music: {lookups[0]} songs looked up ({YOUTUBE_LOOKUPS - search_budget[0]} by search), "
               f"{len(ids)} videos tracked, {pending} songs still to look up")
    state.pop("yt_uploads", None)    # no longer saved (see _RUN_UPLOADS)
    if len(state["meta"]) > 30000:   # MusicBrainz details (~0.5 KB each): keep the newest 30,000, about 15 MB
        state["meta"] = dict(list(state["meta"].items())[-30000:])
    if len(state["itunes"]) > 6000:
        state["itunes"] = dict(list(state["itunes"].items())[-6000:])
    state.pop("history", None)
    common.save_json(STATE, state)
    return {"genres": genres, "sources": ["YouTube (views)", "ListenBrainz / MusicBrainz (genre membership)", "iTunes (artwork, previews)"]}
