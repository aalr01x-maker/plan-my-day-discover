"""Musica: the owner's 8 genres, three Top 20 lists each.

Songs have no reliable public "quality" rating, so the weights apply only to what
can be measured (owner's rule):

  Best of All Time   listeners (distinct users, log scale) 85% + genre fit 15%
  Best Recent        released in the last 12 months; listeners 75% + genre fit 25%
  Trending (7 days)  growth in listens between our own daily snapshots 70% +
                     listeners 30%. Needs history: until two snapshots exist the
                     list says so instead of guessing.

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
    "indie-electro": ("Indie Electro", ["indietronica", "indie electronic", "electropop"]),
    "ambient-pop": ("Ambient Pop / Ethereal Vocals", ["dream pop", "ambient pop", "ethereal"]),
    "electro-jazz-soul": ("Electro-Jazz / Electro Soul", ["nu jazz", "electro soul", "future soul", "electro swing"]),
    "retrowave": ("Retrowave", ["synthwave", "retrowave", "outrun"]),
    "nu-disco": ("Nu-Disco / Chill Dance", ["nu disco", "nu-disco", "disco house"]),
    "conscious-rap": ("Conscious Rap / Mellow Vocal Hip-Hop", ["conscious hip hop", "jazz rap", "alternative hip hop"]),
}
PER_ARTIST = 2            # a list shows at most 2 songs by one artist
TAG_LEVELS = {"recording": 1.0, "release_group": 0.8, "artist": 0.6}
ITUNES_PER_RUN = 150      # Apple asks for ~20 requests/minute; the cache fills over a few days
VIDEOS_PER_RUN = 15       # YouTube search = 100 quota units each (10,000/day); cached for good


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
    todo = [m for m in mbids if m not in cache]
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
    common.log(f"music: {len(every)} candidate songs, popularity for {len(pop)}")

    # Daily listen snapshots -> 7-day growth for Trending.
    hist = state["history"]
    for mbid, p in pop.items():
        h = hist.setdefault(mbid, {})
        h[today] = p["listens"]
        for day in [d for d in h if d < (now - timedelta(days=10)).strftime("%Y-%m-%d")]:
            del h[day]

    def growth(mbid: str) -> tuple[float, int] | None:
        h = hist.get(mbid) or {}
        days = sorted(d for d in h if d >= (now - timedelta(days=7)).strftime("%Y-%m-%d"))
        if len(days) < 2:
            return None
        span = (common.parse_time(days[-1]) - common.parse_time(days[0])).days or 1
        return (h[days[-1]] - h[days[0]]) * 7 / span, span  # listens per 7 days

    itunes_budget, video_budget = [ITUNES_PER_RUN], [VIDEOS_PER_RUN]
    genres = {}
    for gid, (name, tags) in GENRES.items():
        songs = []
        seen: dict[str, dict] = {}
        for mbid in pools[gid]:
            meta, p = state["meta"].get(mbid), pop.get(mbid)
            if not meta or not meta.get("title") or not meta.get("artist") or not p or p["users"] < 1:
                continue
            fit = _fit(meta, tags)
            if fit <= 0:  # not actually tagged with this genre
                continue
            song = {"mbid": mbid, "fit": fit, **meta, **p}
            # one entry per song (remasters, instrumentals and re-releases are separate recordings)
            same = common.norm(meta["artist"] + " " + meta["title"].split(" (")[0]).strip()
            if same not in seen or seen[same]["users"] < song["users"]:
                seen[same] = song
        songs = list(seen.values())
        max_users = max((s["users"] for s in songs), default=1)

        def listeners(s):
            return common.log_scale(s["users"], max_users)

        year_ago = (now - timedelta(days=365)).strftime("%Y-%m-%d")

        def capped(ranked: list[dict]) -> list[dict]:
            per: dict[str, int] = {}
            out = []
            for s in ranked:
                if per.get(s["artist"], 0) < PER_ARTIST:
                    per[s["artist"]] = per.get(s["artist"], 0) + 1
                    out.append(s)
            return out

        all_time = capped(sorted([s for s in songs if s["users"] >= 2],
                                 key=lambda s: 0.85 * listeners(s) + 0.15 * s["fit"], reverse=True))
        recent = sorted([s for s in songs if (s.get("released") or "") >= year_ago and len(s.get("released") or "") == 10],
                        key=lambda s: 0.75 * listeners(s) + 0.25 * s["fit"], reverse=True)
        recent = capped(recent)
        growing = [(s, growth(s["mbid"])) for s in songs]
        growing = [(s, g) for s, g in growing if g and g[0] > 0]
        max_g = max((g[0] for _, g in growing), default=1)
        trending = capped([s for s, g in sorted(growing, key=lambda sg: 0.7 * common.log_scale(sg[1][0], max_g) + 0.3 * listeners(sg[0]),
                                         reverse=True)])
        history_days = max((g[1] for _, g in growing), default=0)

        def card(s: dict, rank: int, why: str) -> dict:
            apple = _itunes(s, state["itunes"], itunes_budget)
            vkey = s["mbid"]
            video = state["videos"].get(vkey)
            if video is None and yt and yt.available and rank <= 5 and video_budget[0] > 0:
                video_budget[0] -= 1
                found = yt.search(f"{s['artist']} {s['title']} official", max_age_days=3650)
                pick = best_match(f"{s['artist']} {s['title']}", None, found, min_similarity=0.34)
                state["videos"][vkey] = video = embed(pick) or {}
            return {
                "id": s["mbid"], "rank": rank, "title": s["title"], "artist": s["artist"], "album": s.get("album"),
                "released": (apple or {}).get("released") or s.get("released"),
                "artwork": (apple or {}).get("artwork") or s.get("cover"),
                "preview": (apple or {}).get("preview"),
                "metrics": {"listeners": s["users"], "listens": s["listens"], "genre_fit": round(s["fit"], 2),
                            "source": "ListenBrainz"},
                "why": why, "video": video or None, "links": _links(s, apple),
            }

        lists = {
            "all_time": [card(s, i + 1, f"{s['users']:,} ListenBrainz listeners; MusicBrainz genre-tag fit {s['fit']:.0%}.")
                         for i, s in enumerate(all_time[:20])],
            "recent": [card(s, i + 1, f"Released {s['released']}; {s['users']:,} listener{'s' if s['users'] != 1 else ''} so far on ListenBrainz.")
                       for i, s in enumerate(recent[:20])],
            "trending": [card(s, i + 1, f"+{growth(s['mbid'])[0]:,.0f} listens per week on ListenBrainz; "
                                         f"{s['users']:,} listeners.") for i, s in enumerate(trending[:20])],
        }
        notes = {}
        if history_days < 7:
            notes["trending"] = (f"Trending compares daily listen counts; history covers {history_days} day(s) so far "
                                 "and reaches the full 7 days after a week of daily updates." if history_days
                                 else "Trending starts after the second daily update (it compares listen counts between days).")
        if len(lists["recent"]) < 20:
            notes["recent"] = f"{len(lists['recent'])} songs in this genre were released in the last 12 months in the open data."
        genres[gid] = {"name": name, "tags": tags, **lists, "notes": notes}
        common.log(f"music {gid}: {len(songs)} songs; {len(lists['all_time'])}/{len(lists['recent'])}/{len(lists['trending'])}")

    # keep state bounded
    state["history"] = {m: h for m, h in hist.items() if m in every}
    if len(state["itunes"]) > 6000:
        state["itunes"] = dict(list(state["itunes"].items())[-6000:])
    common.save_json(STATE, state)
    return {"genres": genres, "sources": ["ListenBrainz", "MusicBrainz", "iTunes Search"] + (["Last.fm"] if key else [])}
