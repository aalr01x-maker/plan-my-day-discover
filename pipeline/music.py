"""Music: the owner's genres, three lists each: Top 30 for All and the 14 broad genres, Top 20 for the niche ones.

Every list is ranked by YouTube alone (owner, 31 Oct 2026); tags only decide which
genre a song belongs to:

  Best of All Time   YouTube views of the song's official upload
  (Owner, 2 Nov 2026: the two rankings below are SWAPPED on the site -- "recent" holds the weekly-gain
  ranking and "trending" the 12-month total-views ranking.)
  Best Recent        released in the last 12 months; YouTube views
  Trending (7 days)  YouTube views gained per week (between our daily counts), else
                     views per week since the video went up (under 2 years old) -- 100%
                     (owner, 2 Nov 2026: total views removed). Each card says which one ranked it.

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

import html
import math
import re
import time
from datetime import timedelta
from urllib.parse import quote_plus

from . import common
from . import music_catalog
from .youtube import YouTube, best_match, embed

LB = "https://api.listenbrainz.org/1"
LASTFM = "https://ws.audioscrobbler.com/2.0/"
ITUNES = "https://itunes.apple.com/search"
MB = "https://musicbrainz.org/ws/2/recording"
STATE = common.STATE / "music.json"

GENRES = {
    # "All" first: every genre's songs together (built from the others, no tags of its own);
    # then broad genres (owner, 31 Oct 2026), then the owner's original, more niche ones
    "all": ("All", []),
    "rap": ("Rap", ["hip hop", "rap", "east coast hip hop", "west coast hip hop"]),
    "rock": ("Rock", ["rock", "classic rock", "hard rock", "alternative rock"]),
    "rnb": ("R&B", ["r&b", "contemporary r&b", "neo soul", "rhythm and blues"]),
    "edm-techno": ("EDM & Techno", ["edm", "techno", "electro house", "big room", "melodic techno"]),
    "country": ("Country", ["country", "country pop", "contemporary country", "americana"]),
    "jazz": ("Jazz", ["jazz", "vocal jazz", "cool jazz", "bebop", "smooth jazz"]),
    "classical": ("Classical", ["classical", "baroque", "modern classical", "contemporary classical", "romantic classical"]),
    "blues": ("Blues", ["blues", "blues rock", "electric blues", "delta blues"]),
    "reggae": ("Reggae", ["reggae", "roots reggae", "dancehall", "dub"]),
    "indie": ("Indie", ["indie", "indie rock", "indie pop", "indie folk"]),
    "alternative": ("Alternative", ["alternative rock", "alternative", "alternative pop", "grunge"]),
    "house": ("House", ["house", "deep house", "tech house", "french house"]),
    "trap": ("Trap", ["trap", "trap rap", "drill"]),
    "arabic": ("Arabic", ["arabic", "arabic pop", "khaleeji", "rai", "egyptian", "lebanese"]),
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
    "rap": r"hip ?hop|rap",
    "rock": r"rock|grunge|punk|metal",
    "rnb": r"r&b|rnb|soul|rhythm and blues",
    "edm-techno": r"edm|techno|house|electro|dance|trance|dubstep",
    "country": r"country|americana|bluegrass",
    "jazz": r"jazz|swing|bebop|bop",
    "classical": r"classical|baroque|orchestra|\bopera\b|composer|chamber music|symphon",
    "blues": r"blues",
    "reggae": r"reggae|dancehall|\bdub\b|\bska\b|rocksteady",
    "indie": r"indie",
    "alternative": r"alternative|grunge|indie|post punk|rock",
    "house": r"house",
    "trap": r"trap|drill|hip ?hop|rap",
    "arabic": r"arab|khaleeji|\brai\b|egypt|leban|middle east|shaabi|mahraganat|levant|syria|iraq|saudi|kuwait|morocc|algeri|tunisi",
}
STRONG_FIT = 0.8
# genres where a stray tag let other artists in: the genre must be one of the artist's top 5 tags
CORE_TAGS = {"rap": r"hip ?hop|rap|trap|drill|grime", "trap": r"trap|drill|hip ?hop|rap",
             "conscious-rap": r"hip ?hop|rap"}

# owner, 2 Nov 2026: Top 30 for All and the 14 broad genres, Top 20 for the 8 niche ones
BIG_GENRES = {"all", "rap", "rock", "rnb", "edm-techno", "country", "jazz", "classical", "blues", "reggae", "indie",
              "alternative", "house", "trap", "arabic"}


def list_size(gid: str) -> int:
    return 30 if gid in BIG_GENRES else 20


PER_ARTIST = 2            # a list shows at most 2 songs by one artist
TAG_LEVELS = {"recording": 1.0, "release_group": 0.8, "artist": 0.6}
ITUNES_PER_RUN = 250      # Apple asks for ~20 requests/minute; the cache fills over a few days


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
            for page in (1, 2, 3):   # Last.fm's top 300 for the tag (millions of listeners)
                try:
                    data = common.get(LASTFM, params={"method": "tag.gettoptracks", "tag": tag, "limit": 100,
                                                      "page": page, "api_key": key, "format": "json"})
                except common.FetchError as exc:
                    common.log(f"music: last.fm {tag!r}: {exc}")
                    break
                tracks = (data.get("tracks") or {}).get("track") or []
                for rank, t in enumerate(tracks, start=(page - 1) * 100):
                    if t.get("mbid"):
                        pool[t["mbid"]] = max(pool.get(t["mbid"], 0), 1 - rank / 600)
                if len(tracks) < 100:
                    break
        out[gid] = pool
    return out


# Deezer's official streaming charts add each broad genre's current hits (Deezer's own genre, so no
# tags are needed). Arabic: Deezer's charts for Arab countries, keeping songs in Arabic script or by
# artists who chart across Arab countries but not globally.
DEEZER = "https://api.deezer.com"
DEEZER_GENRES = {"rap": 116, "rock": 152, "rnb": 165, "edm-techno": 106, "country": 84, "jazz": 129,
                 "classical": 98, "blues": 153, "reggae": 144, "alternative": 85}
DEEZER_ARAB_CHARTS = {"Egypt": 1362501615, "Saudi Arabia": 1362521285, "Morocco": 1362512715,
                      "United Arab Emirates": 1362491345, "Lebanon": 1362511155, "Algeria": 1362501015}
ARABIC_SCRIPT = re.compile(r"[\u0600-\u06FF]")


LASTFM_ARTIST_TAGS_PER_RUN = 1500


def _artist_tags_lastfm(artist: str, key: str | None, cache: dict, budget: list[int]) -> str | None:
    """An artist's top Last.fm tags joined by " | " (cached 60 days); None if unknown."""
    hit = cache.get(artist)
    if hit and common.within(common.parse_time(hit["at"]), 60 * 24):
        return hit["t"]
    if not key or budget[0] <= 0:
        return None
    budget[0] -= 1
    try:
        data = common.get(LASTFM, params={"method": "artist.gettoptags", "artist": artist, "api_key": key, "format": "json"})
    except common.FetchError:
        return None
    tags = [t.get("name", "").lower().replace("-", " ") for t in ((data.get("toptags") or {}).get("tag") or [])[:12]]
    cache[artist] = {"at": common.iso(common.now_utc()), "t": " | ".join(tags)}
    return cache[artist]["t"]


def _deezer_song(t: dict, albums: dict) -> dict:
    album = t.get("album") or {}
    released = albums.get(str(album.get("id")))
    if album.get("id") and released is None:
        try:
            released = albums[str(album["id"])] = common.get(f"{DEEZER}/album/{album['id']}").get("release_date") or ""
        except common.FetchError:
            released = ""
    return {"title": t.get("title_short") or t.get("title"), "artist": (t.get("artist") or {}).get("name"),
            "album": album.get("title"), "released": released or None, "cover": album.get("cover_medium"),
            "artist_youtube": None, "deezer": t.get("link"), "rank": t.get("rank") or 0}


def _deezer_candidates(albums: dict) -> dict[str, dict]:
    """genre -> {"dz:<id>": song} from Deezer's charts."""
    out: dict[str, dict] = {}
    for gid, genre_id in DEEZER_GENRES.items():
        try:
            tracks = common.get(f"{DEEZER}/chart/{genre_id}/tracks", params={"limit": 100}).get("data") or []
        except common.FetchError as exc:
            common.log(f"music: deezer {gid}: {exc}")
            continue
        out[gid] = {f"dz:{t['id']}": _deezer_song(t, albums) for t in tracks if t.get("id")}
    try:
        global_artists = {(t.get("artist") or {}).get("name") for t in
                          common.get(f"{DEEZER}/chart/0/tracks", params={"limit": 100}).get("data") or []}
    except common.FetchError:
        global_artists = set()
    seen, charts = {}, {}
    for country, pid in DEEZER_ARAB_CHARTS.items():
        try:
            tracks = common.get(f"{DEEZER}/playlist/{pid}/tracks", params={"limit": 100}).get("data") or []
        except common.FetchError as exc:
            common.log(f"music: deezer chart {country}: {exc}")
            continue
        for t in tracks:
            artist = (t.get("artist") or {}).get("name") or ""
            charts.setdefault(artist, set()).add(country)
            seen[t["id"]] = t
    arabic = {}
    for tid, t in seen.items():
        artist = (t.get("artist") or {}).get("name") or ""
        if ARABIC_SCRIPT.search(artist + (t.get("title") or "")) or (len(charts.get(artist, ())) >= 1 and artist not in global_artists):
            # candidates only: the artist check in build() keeps just Arabic artists
            arabic[f"dz:{tid}"] = _deezer_song(t, albums)
    out["arabic"] = arabic
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


YOUTUBE_LOOKUPS = 20      # YouTube searches per day (100 of the 10,000 free daily units each): a last resort; songs are found on the artist's channel instead
NOT_OFFICIAL = re.compile(r"\b(cover|karaoke|reaction|tutorial|lesson|instrumental version|8d|slowed|sped up|nightcore)\b", re.I)


# "anniversary" (owner, 14 Nov 2026): Faded matched a 2025 "10th Anniversary" re-upload (1.5M views) instead of the
# official 2015 video (3B+)
SKIP_VERSION = re.compile(r"\b(live|remix|edit|teaser|trailer|behind the scenes|interview|making of|snippet|visualiser|visualizer|"
                          r"anniversary)\b", re.I)
CHANNELS_PER_RUN = 2000   # artist channels read per day (1-6 quota units each; the ceiling still applies)
YOUTUBE_CEILING = 9300     # music runs last, so it may use the daily allowance up to here (10,000 total; YouTube sells no extra)


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


def _late_upload(entry: dict, song: dict) -> bool:
    """The matched video went up 3+ years after a song released in the YouTube era (2009 on): likely a
    re-upload or anniversary edition, not the official video (Faded 2015 -> a 2025 upload)."""
    rel, pub = (song.get("released") or "")[:4], (entry.get("published") or "")[:4]
    return rel.isdigit() and pub.isdigit() and int(rel) >= 2009 and int(pub) - int(rel) >= 3


def _todo(entry: dict | None, now, song: dict | None = None) -> bool:
    """Not looked up yet, or found nothing more than 30 days ago (worth another try), or matched to a
    late re-upload that hasn't been re-checked yet (queued: waits for the daily search budget)."""
    if entry is None:
        return True
    if entry.get("id"):
        return bool(song) and not entry.get("rechecked") and _late_upload(entry, song)
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


def _vevo_channel(yt: YouTube, artist: str, cache: dict) -> str | None:
    """The artist's VEVO channel (where most classic music videos live), by its @handle: 1 unit."""
    key = "vevo:" + common.norm(artist)
    if key in cache:
        return cache[key]
    base = re.sub(r"[^0-9A-Za-z]", "", artist)
    cid = None
    if len(base) >= 3:
        try:
            data = yt.get("channels", {"part": "snippet", "forHandle": f"@{base}VEVO"})
        except common.FetchError:
            return None      # not cached: tried again on a later day
        yt.units += 1
        item = (data.get("items") or [None])[0]
        title = common.norm((item or {}).get("snippet", {}).get("title", "")).replace(" ", "")
        if item and "vevo" in title and common.norm(artist).replace(" ", "") in title:
            cid = item["id"]
    cache[key] = cid
    return cid


def _channel_videos(yt: YouTube, cid: str, channel_budget: list[int], pages: int) -> list[dict] | None:
    """A channel's uploads, read once per run (None when YouTube's quota is used up)."""
    videos = _RUN_UPLOADS.get(cid)
    if videos is None:
        channel_budget[0] -= 1
        vids = yt.uploads(cid, pages=pages)
        if yt.exhausted:
            return None
        # kept for this run only (saving them grew the state file by MBs a day)
        videos = _RUN_UPLOADS[cid] = [{"id": v["id"], "title": v["title"], "channel": v.get("channel"),
                                       "published": v.get("published")} for v in vids]
    return videos


def _youtube_video(yt: YouTube, song: dict, state: dict, search_budget: list[int], channel_budget: list[int]) -> dict | None:
    """The song's most-viewed official upload: its music video, official audio or the artist's
    auto-generated "Topic" track. Covers, reactions and fan re-uploads don't count.
    Looks on the artist's own channel (listed by MusicBrainz, else found by its @handle) and their
    VEVO channel (1-6 units each). A YouTube search (100 units) is used when neither has the song,
    or when the best find has under 1M views for a song over a year old (big artists' old videos are
    often beyond the uploads list), and the most-viewed official upload wins."""
    candidates = []
    url = song.get("artist_youtube")
    if channel_budget[0] > 0 and yt.units < YOUTUBE_CEILING:
        ids = state.setdefault("yt_channel_ids", {})
        for cid, pages in ((_artist_channel(yt, url, ids) if url else _guess_channel(yt, song["artist"], ids), 4),
                           (_vevo_channel(yt, song["artist"], ids), 6)):
            if cid:
                videos = _channel_videos(yt, cid, channel_budget, pages)
                if videos is None:
                    return "later"
                candidates += [v for v in _match(song, videos) if v["id"] not in {c["id"] for c in candidates}]
    stats = _youtube_views(yt, [v["id"] for v in candidates]) if candidates else {}
    if candidates and yt.exhausted and not stats:
        return "later"
    best_views = max((stats.get(v["id"], 0) for v in candidates), default=0)
    old = (song.get("released") or "0") < (common.now_utc() - timedelta(days=365)).strftime("%Y-%m-%d")
    best_c = max(candidates, key=lambda v: stats.get(v["id"], 0)) if candidates else None
    if not candidates or (best_views < 1_000_000 and old) or (best_c and _late_upload(best_c, song)):
        if search_budget[0] > 0 and not yt.exhausted and yt.units < YOUTUBE_CEILING:
            search_budget[0] -= 1
            found = yt.search(f"{song['artist']} {song['title']}", max_age_days=36500)
            if yt.exhausted and not candidates:
                return "later"
            artist = common.norm(song["artist"])
            extra = []
            for v in _match(song, found):
                channel = common.norm(v.get("channel") or "")
                if ((artist and (artist in channel or channel.replace(" topic", "") in artist)) or "vevo" in channel) \
                        and v["id"] not in {c["id"] for c in candidates}:
                    extra.append(v)
            if extra:
                stats.update(_youtube_views(yt, [v["id"] for v in extra]))
                candidates += extra
        elif not candidates:
            return "later"
    if not candidates:
        return None
    best = max(candidates, key=lambda v: stats.get(v["id"], 0))
    return {"id": best["id"], "title": best["title"], "channel": best.get("channel"), "published": best.get("published"),
            "rechecked": True}


RECENT_SEARCHES_PER_RUN = 18  # 100 units each; each search is reused for 7 days, so a genre refreshes weekly
JUNK = re.compile(r"\b(official (music )?video|official audio|official visualizer|official lyric video|lyric video|lyrics|"
                  r"visualizer|audio|video|clip officiel|m/v|mv|4k|hd|explicit|clean)\b", re.I)


def _youtube_recent(yt: YouTube, gid: str, name: str, after, key: str | None, artist_tags: dict,
                    tag_budget: list[int], budget: list[int]) -> list[dict]:
    """The genre's most-viewed official music videos of the last 12 months, straight from YouTube
    (owner, 2 Nov 2026: Best Recent missed the year's big hits that the music databases hadn't linked).
    Official = an artist channel, VEVO or the auto-generated "Topic" channel; the artist must belong to
    the genre on Last.fm."""
    # broad genres get three wordings (owner, 2 Nov 2026: a hit titled "Artist - Song" or posted as
    # official audio was missed by the single "official music video" search); niche genres one
    base = name.split(" /")[0].split(" &")[0]
    alt = (GENRES[gid][1] or [base])[0]
    queries = [f"{base} official music video"]
    if gid in BIG_GENRES:
        queries += [f"{alt if alt.lower() != base.lower() else base + ' song'} official video", f"{base} official audio"]
    found, seen_ids = [], set()
    for query in queries:
        cache_key = f"{query}|None|50|viewCount|10"
        cached = (yt.cache.get("search") or {}).get(cache_key)
        if not (cached and common.within(common.parse_time(cached["at"]), 7 * 24)):
            if budget[0] <= 0 or yt.exhausted or yt.units >= YOUTUBE_CEILING:
                continue
            budget[0] -= 1
        for v in yt.search(query, after=after, max_age_days=7, max_results=50, order="viewCount", category="10"):
            if v["id"] not in seen_ids:
                seen_ids.add(v["id"])
                found.append(v)
    out = []
    for v in found:
        title, channel = html.unescape(v.get("title") or ""), html.unescape(v.get("channel") or "")
        raw_title = title
        title = re.sub(r"#\S+", "", title)               # "#desihiphop #viralsong"
        title = title.split(" | ")[0].strip()             # "DELHI SE MANALI | BADSHAH X ..." -> the song part
        official = channel.endswith("VEVO") or channel.endswith(" - Topic") or "official" in raw_title.lower()
        if not official:
            continue
        if " - " in title:
            artist, song = title.split(" - ", 1)
        else:
            artist, song = re.sub(r"(VEVO| - Topic)$", "", channel).strip(), title
        song = re.sub(r"[\(\[][^\)\]]*[\)\]]", "", song)   # "(Official Video)", "[Explicit]"
        song = JUNK.sub("", song).strip(" -|\"'")
        artist = re.sub(r"\s*(ft\.?|feat\.?|\bx\b|&|,).*$", "", artist, flags=re.I).strip()
        song = re.sub(r"\s+(ft\.?|feat\.?)\s.*$", "", song, flags=re.I).strip()
        if " - " not in title and channel:   # "BANGER Badshah x Sharvi ..." with no separator: drop the trailing names
            song = re.split(r"\s+[A-Z][\w'.]*\s+x\s+", song)[0].strip()
        if not song or not artist:
            continue
        # the artist name must match the channel (no fan or label re-uploads of someone else's song)
        ch = common.norm(re.sub(r"(VEVO| - Topic)$", "", channel)).replace(" ", "")
        an = common.norm(artist).replace(" ", "")   # "KendrickLamarVEVO" vs "Kendrick Lamar"
        if not an or not ch or not (an in ch or ch in an):
            continue
        tags = _artist_tags_lastfm(artist, key, artist_tags, tag_budget)
        # the genre must be among the artist's top 5 tags (a stray tag lets Viking or pop acts in)
        if tags is None or not re.search(FAMILY[gid], " | ".join(tags.split(" | ")[:5])):
            continue
        out.append({"title": song, "artist": artist, "album": None, "released": (v.get("published") or "")[:10] or None,
                    "cover": v.get("thumb"), "artist_youtube": None,
                    "video": {"id": v["id"], "title": title, "channel": channel, "published": v.get("published")}})
    return out


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


def _earliest(*dates: str | None) -> str | None:
    """The earliest full date (YYYY-MM-DD) among the sources, else the first partial one: a reissue or
    live album must never make an old song look new."""
    full = sorted(d for d in dates if d and len(d) == 10)
    return full[0] if full else next((d for d in dates if d), None)


def _posted_since(video: dict, cut) -> bool:
    up = common.parse_time(video.get("published"))
    return bool(up and up >= cut)


def _renumber(cards: list[dict], keep_why: bool = False) -> list[dict]:
    for i, c in enumerate(cards):
        c["rank"] = i + 1
        if not keep_why:
            c["why"] = f"Released {c['released']}; {c['metrics']['views']:,} views on YouTube."
    return cards


KIDS = re.compile(r"cocomelon|pinkfong|baby shark|chuchu|looloo|little baby bum|super simple|bounce patrol|nursery|"
                  r"\bkids\b|kidi|for children|wheels on the bus|johny johny|toddler|infantil|bebefinn|masha|enfants|"
                  r"apprendre|ni[ñn]os|kinder|abc song|phonics|learn colou?rs|learn english|vocabulary|vocabulaire", re.I)
BRACKETS = re.compile(r"\s*[\(\[][^\)\]]*(official|video|audio|lyric|visuali[sz]er|mv|m/v|4k|hd|remaster)[^\)\]]*[\)\]]", re.I)


# Best of All Time per genre from YouTube alone (owner, 9 Nov 2026): YouTube's most-viewed music videos
# for the genre, kept only when YouTube's own topic labels on the video name that genre.
YT_GENRE = {
    "rap": ("hip hop rap", "Hip_hop_music"), "trap": ("trap music", "Hip_hop_music"),
    "conscious-rap": ("conscious rap", "Hip_hop_music"), "rock": ("rock", "Rock_music"),
    "alternative": ("alternative rock", "Rock_music"), "rnb": ("r&b", "Rhythm_and_blues|Soul_music"),
    "edm-techno": ("edm techno", "Electronic_music"), "house": ("house music", "Electronic_music"),
    "progressive-house": ("progressive house", "Electronic_music"), "indie-electro": ("indie electronic", "Electronic_music"),
    "nu-disco": ("nu disco", "Electronic_music"), "retrowave": ("retrowave synthwave", "Electronic_music"),
    "chillwave-trip-hop": ("chillwave trip hop", "Electronic_music"), "country": ("country", "Country_music"),
    "jazz": ("jazz", "Jazz"), "electro-jazz-soul": ("electro soul jazz", "Jazz|Soul_music|Electronic_music"),
    "classical": ("classical music", "Classical_music"), "blues": ("blues", "Rhythm_and_blues|Soul_music|Rock_music"),
    "reggae": ("reggae", "Reggae"), "indie": ("indie", "Independent_music"), "ambient-pop": ("ambient pop", "Pop_music|Electronic_music"),
    "arabic": ("arabic music", None),   # YouTube has no Arabic topic: Arabic script in the title or channel instead
}
ARABIC = re.compile(r"[\u0600-\u06FF]")
# Songs that come only from YouTube's most-viewed lists (no music database put them in the genre) must
# have the genre itself among the ARTIST's top 5 Last.fm tags. YouTube's topic labels are too coarse
# alone: "Electronic_music" let Despacito/Axel F into House and Retrowave, R&B/Rock let Shape of You into
# Blues (owner, 14 Nov 2026: "do whatever's most accurate").
GENRE_CORE = {
    "rap": r"hip ?hop|\brap\b", "trap": r"\btrap\b|drill", "conscious-rap": r"conscious|jazz rap|alternative hip ?hop|underground hip ?hop",
    "rock": r"rock|metal|grunge|punk", "alternative": r"alternative|grunge|post punk", "rnb": r"r&b|\brnb\b|soul|rhythm and blues",
    "edm-techno": r"\bedm\b|techno|electro house|trance|dubstep|eurodance|big room|electronic dance",
    "house": r"\bhouse\b", "progressive-house": r"progressive house|melodic house|organic house|deep house",
    "indie-electro": r"indietronica|indie electronic|indie electronica|electroclash",
    "nu-disco": r"nu ?disco|disco house|\bdisco\b", "retrowave": r"synthwave|retrowave|outrun|darksynth",
    "chillwave-trip-hop": r"chillwave|trip ?hop|downtempo", "country": r"country|americana|bluegrass",
    "jazz": r"jazz|swing|bebop", "electro-jazz-soul": r"nu jazz|electro swing|future soul|acid jazz|neo soul",
    "classical": r"classical|baroque|composer|orchestral|\bopera\b|romantic|piano", "blues": r"blues",
    "reggae": r"reggae|dancehall|\bdub\b|\bska\b", "indie": r"indie", "ambient-pop": r"dream ?pop|ambient|ethereal|shoegaze",
}
# compilations, study/sleep mixes and talent-show clips aren't songs (Classical/Jazz All Time had them)
COMPILATION = re.compile(r"\b(best of|greatest hits|compilation|full album|playlist|hours of|\d+ ?(hours?|hrs?)|non ?stop|"
                         r"medley|relaxing|background|for (study|studying|sleep|sleeping|work|relax)|chill ?out music|"
                         r"prodigy|year old|got talent|audition)\b", re.I)
GENRE_TOP_PER_RUN = [44]   # genre search pages per run (100 units each, reused for a week)


def _topics(yt: YouTube, ids: list[str]) -> dict[str, str]:
    """YouTube's topic labels per video (1 unit per 50 videos)."""
    out = {}
    for i in range(0, len(ids), 50):
        try:
            data = yt.get("videos", {"part": "topicDetails", "id": ",".join(ids[i:i + 50])})
            yt.units += 1
        except common.FetchError as exc:
            common.log(f"youtube topics: {exc}")
            continue
        for item in data.get("items", []):
            out[item["id"]] = " ".join((item.get("topicDetails") or {}).get("topicCategories") or [])
    return out


def _genre_fits(gid: str, topics: str | None, v: dict) -> bool:
    if gid == "all":
        return True
    if topics is None:   # YouTube's labels couldn't be read: no evidence, the song stays out
        return False
    q, topic = YT_GENRE.get(gid, (None, None))
    if gid == "arabic":
        return bool(ARABIC.search(f"{v.get('title')} {v.get('channel')}"))
    return bool(topic and re.search(topic, topics or ""))


WIKI_TOP = "https://en.wikipedia.org/w/api.php"
_WIKI_CACHE: dict = {}   # fetched once per run


def _wiki_top(yt: YouTube) -> list[dict]:
    """The videos on Wikipedia's "List of most-viewed YouTube videos" (Despacito, Shape of You, ...), music
    category only, with YouTube's own title and channel. YouTube's search sorted by views returns kids'
    clips and Shorts instead of these (owner, 9 Nov 2026: Despacito was missing from All). Wikipedia only
    names the videos; views and ranking still come from YouTube."""
    try:
        text = common.get(WIKI_TOP, params={"action": "parse", "page": "List of most-viewed YouTube videos", "prop": "wikitext",
                                            "format": "json", "formatversion": 2},
                          headers={"User-Agent": "PlanMyDayDiscover/1.0 (personal schedule site)"})["parse"]["wikitext"]
    except Exception as exc:
        common.log(f"music: wikipedia top videos: {exc}")
        return []
    ids = list(dict.fromkeys(re.findall(r"(?:watch\?v=|youtu\.be/|\|id=|\{\{YouTube\|)([A-Za-z0-9_-]{11})", text)))
    out = []
    for i in range(0, len(ids), 50):
        try:
            data = yt.get("videos", {"part": "snippet", "id": ",".join(ids[i:i + 50])})
            yt.units += 1
        except common.FetchError as exc:
            common.log(f"music: top video details: {exc}")
            continue
        for item in data.get("items", []):
            sn = item.get("snippet") or {}
            if sn.get("categoryId") == "10":   # Music
                out.append({"id": item["id"], "title": html.unescape(sn.get("title") or ""), "published": sn.get("publishedAt"),
                            "channel": html.unescape(sn.get("channelTitle") or ""), "channel_id": sn.get("channelId")})
    return out


def _split_artist_title(name: str, channel: str) -> tuple[str, str]:
    """ "Artist - Title" (hyphen, en or em dash) or "Title - Artist" when the second half is the channel's
    name ("Numb - Linkin Park" on Linkin Park's channel); else the channel is the artist."""
    parts = re.split(r"\s[-\u2013\u2014]\s", name, maxsplit=1)
    if len(parts) < 2:
        return channel, name
    a, b = parts[0].strip(), parts[1].strip()
    ch = common.norm(channel).replace(" ", "")
    if ch and common.norm(b).replace(" ", "") == ch and common.norm(a).replace(" ", "") != ch:
        return b, a
    return a, b


MB_DATES_PER_RUN = 400   # MusicBrainz asks for 1 request/s; cached 120 days when found, 30 when not


def _mb_first_release(artist: str, title: str, cache: dict, budget: list[int]) -> str | None:
    """The song's original release date from MusicBrainz: the earliest first-release-date among recordings
    with the same title by the same artist (owner, 14 Nov 2026: Axel F showed 2000 and Numb 2007; they are
    2005 and 2003). iTunes and YouTube give reissue and upload dates, so MusicBrainz decides when it knows."""
    main = re.sub(r"\s*(\bft\.?|\bfeat\.?|\bfeaturing\b|,| x | & ).*$", "", artist, flags=re.I).strip() or artist
    name = re.split(r"\s+(?:ft\.?|feat\.?|featuring)\s|[\(\[|/]|\s[-\u2013]\s", title, flags=re.I)[0].strip(" -\"'") or title
    k = common.norm(f"{main} {name}").strip()
    hit = cache.get(k)
    if hit and common.within(common.parse_time(hit["at"]), (120 if hit.get("d") else 30) * 24):
        return hit.get("d")
    if budget[0] <= 0:
        return hit.get("d") if hit else None
    budget[0] -= 1
    time.sleep(1.1)
    q = 'recording:"{}" AND artist:"{}"'.format(name.replace('"', ""), main.replace('"', ""))
    try:
        data = common.get(MB, params={"query": q, "fmt": "json", "limit": 50}, tries=2)
    except common.FetchError as exc:
        common.log(f"music: musicbrainz date {main} - {name}: {exc}")
        return hit.get("d") if hit else None
    want_t, want_a = common.norm(name).strip(), common.norm(main).replace(" ", "")
    dates = []
    for r in data.get("recordings") or []:
        # the song itself: exact title (a "(feat. ...)" aside is fine), no disambiguation -- remixes, radio
        # mixes, bootlegs, karaoke and DJ-mix parts carry one or a bracket and often wrong early dates
        # (Shape of You "DJ Inko remix" 2015, Axel F "radio mix" 2004)
        rt = common.norm(re.sub(r"\s*[\(\[](feat|ft)\.?[^\)\]]*[\)\]]", "", r.get("title") or "", flags=re.I)).strip(" .")
        credit = common.norm("".join((c.get("name") or "") + (c.get("joinphrase") or "") for c in r.get("artist-credit") or [])).replace(" ", "")
        if rt == want_t and not r.get("disambiguation") and want_a and (credit.startswith(want_a) or want_a.startswith(credit or "~")) \
                and r.get("first-release-date"):
            dates.append(r["first-release-date"])
    d = None
    if dates:
        # the earliest year at least two recordings agree on (one stray mis-dated entry can't decide)
        years = sorted({x[:4] for x in dates})
        year = next((y for y in years if sum(x[:4] == y for x in dates) >= 2), years[0])
        full = sorted(x for x in dates if x[:4] == year and len(x) == 10)
        d = full[0] if full else min(x for x in dates if x[:4] == year)
    cache[k] = {"at": common.iso(common.now_utc()), "d": d}
    return d


def original_date(mb: str | None, source: str | None, itunes: str | None, video: str | None) -> str | None:
    """MusicBrainz's first release when known (never later than the official video); otherwise the
    earliest of the other sources."""
    v = video if video and len(video) == 10 else None
    if mb:
        if len(mb) == 4:   # year only: a full date from another source in that year is more precise
            same = sorted(x for x in (source, itunes, v) if x and len(x) == 10 and x[:4] == mb)
            mb = same[0] if same else mb
        if v and (v[:4] < mb[:4] or (len(mb) == 10 and v < mb)):
            return v
        return mb
    return _earliest(source, itunes, v)


def _global_top(yt: YouTube | None, gid: str = "all", key: str | None = None, artist_tags: dict | None = None,
                tag_budget: list[int] | None = None) -> list[dict]:
    """YouTube's most-viewed music videos of all time (for a genre: its search, kept only when YouTube's
    topics name the genre) as songs (artist from "Artist - Title", else the channel name), official
    uploads only; kids' nursery channels left out."""
    if not yt or not yt.available:
        return []
    if gid == "all":
        found = _WIKI_CACHE.setdefault("top", _wiki_top(yt))
    else:
        q = YT_GENRE.get(gid, (None,))[0]
        if not q:
            return []
        key = f"__top_music__|2|{q}"
        cached = yt.cache["search"].get(key)
        fresh = cached and common.within(common.parse_time(cached["at"]), 7 * 24)
        if not fresh and GENRE_TOP_PER_RUN[0] <= 0:
            found = cached["items"] if cached else []   # this week's searches are used up: last week's list
        else:
            if not fresh:
                GENRE_TOP_PER_RUN[0] -= 2
            found = yt.top_music(pages=2, query=q)
    if gid != "all":   # the all-time biggest music videos count for their genres too (YouTube's topics decide)
        found = found + _WIKI_CACHE.setdefault("top", _wiki_top(yt))
    found = [{**v, "title": html.unescape(v["title"]), "channel": html.unescape(v.get("channel") or "")} for v in found]
    # kids' videos and hashtag-stuffed uploads (#apprendre #enfants ...) aren't songs
    top = [v for v in found if not KIDS.search(f"{v.get('channel')} {v['title']}") and not NOT_OFFICIAL.search(v["title"])
           and v["title"].count("#") < 2 and not COMPILATION.search(v["title"])]
    topics = _topics(yt, [v["id"] for v in top]) if top and gid != "all" else {}
    top = [v for v in top if _genre_fits(gid, topics.get(v["id"]), v)]
    stats = _youtube_views(yt, [v["id"] for v in top]) if top else {}
    out = []
    for v in top:
        if not stats.get(v["id"]):
            continue
        name = BRACKETS.sub("", v["title"]).strip()
        channel = re.sub(r"(?i)vevo$|\s*-\s*topic$|\s*official$", "", (v.get("channel") or "").strip()).strip()
        artist, title = _split_artist_title(name, channel)
        if gid not in ("all", "arabic"):
            tags = _artist_tags_lastfm(artist.strip(), key, artist_tags if artist_tags is not None else {}, tag_budget or [0])
            if not tags or not re.search(GENRE_CORE[gid], " | ".join(tags.split(" | ")[:5])):
                continue   # the artist isn't known for this genre
        out.append({"mbid": f"yt:{v['id']}", "artist": artist.strip(), "title": title.strip(), "fit": 1.0,
                    "released": (v.get("published") or "")[:10],
                    "yt": {"id": v["id"], "title": v["title"], "channel": v.get("channel"), "published": v.get("published"),
                           "views": stats[v["id"]]}})
    return out


def core_artist(gid: str, x: dict, key: str | None, artist_tags: dict, tag_budget: list[int]) -> bool:
    """Owner, 2 + 14 Nov 2026: the genre must be among the artist's TOP 5 Last.fm tags (MGMT/Viking music
    were in Rap, Uptown Funk in Rap, Pompeii in House, AC/DC in Blues)."""
    if gid in ("all", "arabic") or gid not in GENRE_CORE:
        return True
    tags = _artist_tags_lastfm(x["artist"], key, artist_tags, tag_budget)
    if tags is None:
        # unknown on Last.fm: YouTube-search finds stay out; database-sourced songs keep the earlier checks' verdict
        return not str(x.get("mbid") or "").startswith("yt:")
    if not tags:
        return False
    if not re.search(CORE_TAGS.get(gid) or GENRE_CORE[gid], " | ".join(tags.split(" | ")[:5])):
        return False
    # Conscious Rap: a hip-hop artist AND known for the conscious / jazz-rap side (any of their tags)
    return gid != "conscious-rap" or bool(re.search(GENRE_CORE[gid], tags))


def _incorrect_upload(e: dict) -> str | None:
    """Why a catalogue entry's video isn't the song's official upload, or None. A word that is in the
    song's own title doesn't count against it ("Live Forever", "24 Hours")."""
    t, song = e["video"].get("title") or "", e.get("title") or ""
    if KIDS.search(f"{e['video'].get('channel')} {t}") and not KIDS.search(song):
        return "incorrect upload: a kids' channel or video"
    for rx, why in ((NOT_OFFICIAL, "a cover, karaoke, reaction or edited version"),
                    (COMPILATION, "a compilation, mix or talent-show clip, not a song"),
                    (SKIP_VERSION, "a live, remix, teaser or anniversary version, not the original")):
        if rx.search(t) and not rx.search(song):
            return f"incorrect upload: {why}"
    return None


LAST_MUSIC = common.STATE / "last" / "music.json"


def _seed_catalog(cat: dict, today: str) -> None:
    """First run of the catalogue: the songs on the lists published last time (verified then)."""
    data = common.load_json(LAST_MUSIC, {})
    for gid, g in (data.get("genres") or {}).items():
        for lk in ("all_time", "trending", "recent"):
            for c in g.get(lk) or []:
                v = c.get("video") or {}
                if not v.get("youtube_id") or not (c.get("metrics") or {}).get("views"):
                    continue
                row = {"mbid": c.get("id"), "artist": c["artist"], "title": c["title"], "album": c.get("album"),
                       "cover": c.get("artwork"), "fit": (c.get("metrics") or {}).get("genre_fit") or 1.0, "released": c.get("released"),
                       "yt": {"id": v["youtube_id"], "title": v.get("title"), "channel": v.get("channel"),
                              "published": v.get("published"), "views": c["metrics"]["views"]}}
                music_catalog.upsert(cat, row, gid, "lists published before the catalogue", today)
    common.log(f"music catalogue: seeded {len(cat)} songs from the last published lists")


def _lists_from_last() -> dict:
    """Yesterday's lists in the change report's shape, from the last published file."""
    out: dict = {}
    for gid, g in (common.load_json(LAST_MUSIC, {}).get("genres") or {}).items():
        for lk in ("all_time", "trending", "recent"):
            out.setdefault(gid, {})[lk] = [{"vid": (c.get("video") or {}).get("youtube_id"), "title": c["title"],
                                            "artist": c["artist"], "rank": c["rank"]} for c in g.get(lk) or []]
    return out


def _links(song: dict, apple: dict | None) -> list[dict]:
    q = quote_plus(f"{song['artist']} {song['title']}")
    links = []
    if apple and apple.get("apple_url"):
        links.append({"name": "Apple Music", "url": apple["apple_url"]})
    links += [
        {"name": "Spotify (search)", "url": f"https://open.spotify.com/search/{q}"},
        {"name": "YouTube Music (search)", "url": f"https://music.youtube.com/search?q={q}"},
    ]
    if song.get("deezer"):
        links.append({"name": "Deezer", "url": song["deezer"]})
    elif not str(song["mbid"]).startswith(("dz:", "yt:")):
        links.append({"name": "ListenBrainz", "url": f"https://listenbrainz.org/player/?recording_mbids={song['mbid']}"})
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
    # Deezer chart songs: no MusicBrainz id; Deezer's genre is the membership, its rank the pre-sort
    dz_meta: dict[str, dict] = {}
    artist_tags, tag_budget = state.setdefault("lfm_artist_tags", {}), [LASTFM_ARTIST_TAGS_PER_RUN]
    for gid, songs in _deezer_candidates(state.setdefault("deezer_albums", {})).items():
        for sid, song in songs.items():
            # Deezer's genre labels are loose: the artist must be in the genre's family on Last.fm too
            # (Arabic-script names count as Arabic)
            if not (gid == "arabic" and ARABIC_SCRIPT.search(song["artist"] or "")):
                tags = _artist_tags_lastfm(song["artist"], key, artist_tags, tag_budget)
                if tags is None or not re.search(FAMILY[gid], tags):
                    continue
            dz_meta[sid] = song
            pools.setdefault(gid, {})[sid] = 1.0
    # YouTube's own most-viewed new music videos per genre (Best Recent's big hits)
    yt_meta: dict[str, dict] = {}
    if yt and yt.available:
        rbudget = [RECENT_SEARCHES_PER_RUN]
        for gid, (gname, _) in GENRES.items():
            if gid == "all":
                continue
            for song in _youtube_recent(yt, gid, gname, now - timedelta(days=365), key, artist_tags, tag_budget, rbudget):
                sid = "yt:" + song["video"]["id"]
                yt_meta[sid] = song
                pools.setdefault(gid, {})[sid] = 1.0
    common.log(f"music: {sum(len(p) for p in pools.values())} genre candidates (ListenBrainz, MusicBrainz, Last.fm, "
               f"Deezer {len(dz_meta)}, YouTube new releases {len(yt_meta)})")
    week_chart = _week_chart()
    common.log(f"music: {len(every)} candidate songs, popularity for {len(pop)}")

    itunes_budget, lastfm_budget = [ITUNES_PER_RUN], [LASTFM_PER_RUN]
    state.setdefault("lastfm", {})
    state.setdefault("yt", {})        # song -> its YouTube video (kept for good), or {"none": date} retried after 30 days
    state.setdefault("yt_views", {})  # video id -> {date: views}, last 10 days
    if not state.get("yt_rechecked"):
        # one-time (31 Oct 2026): matches under 1M views were often a minor upload of a big song (the
        # official video sat beyond the uploads list or on a VEVO channel); look those up again
        for k, v in list(state["yt"].items()):
            h = state["yt_views"].get(v.get("id") or "", {})
            if v.get("id") and (h[max(h)] if h else 0) < 1_000_000:
                del state["yt"][k]
        state["yt_rechecked"] = True
    year_ago = (now - timedelta(days=365)).strftime("%Y-%m-%d")
    video_cut = now - timedelta(days=395)
    two_years = (now - timedelta(days=730)).strftime("%Y-%m-%d")

    def skey(s):
        return common.norm(f"{s['artist']} {s['title']}").strip()

    # 1. Each genre's songs and its candidates for the three lists. ListenBrainz/MusicBrainz say which
    #    songs belong to the genre; Last.fm's audience pre-sorts them. Neither number is shown:
    #    the lists are ranked and labelled by YouTube views.
    prepared = {}
    for gid in [g for g in GENRES if g != "all"] + ["all"]:   # "All" is built from the others' songs
        name, tags = GENRES[gid]
        seen: dict[str, dict] = {}
        if gid == "all":
            for other in prepared.values():
                for song in other[2]:
                    same = common.norm(song["artist"] + " " + song["title"].split(" (")[0]).strip()
                    if same not in seen or seen[same]["users"] < song["users"]:
                        seen[same] = song
        for mbid in (pools.get(gid) or {}) if gid != "all" else ():
            if mbid in yt_meta:   # YouTube new-release hit: its video is already known; views pre-sort
                meta = {k: v for k, v in yt_meta[mbid].items() if k != "video"}
                state["yt"].setdefault(skey(meta), yt_meta[mbid]["video"])
                vh = state["yt_views"].get(yt_meta[mbid]["video"]["id"]) or {}
                p = {"users": max(1, (vh[max(vh)] if vh else 1_000_000) // 50), "listens": 0}
                fit = 1.0
            elif mbid in dz_meta:   # Deezer chart song: Deezer's own genre decides; its rank pre-sorts
                meta = dz_meta[mbid]
                p = {"users": max(1, meta["rank"] // 2000), "listens": 0}
                fit = 1.0
            else:
                # found by any source counts; ListenBrainz listen data is optional (pre-sort only)
                meta, p = state["meta"].get(mbid), pop.get(mbid) or {"users": 0, "listens": 0}
                if not meta or not meta.get("title") or not meta.get("artist"):
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
            wanted = (sorted(songs, key=lambda x: -x["users"])[:150] + [x for x in songs if (x.get("released") or "") >= two_years]
                      + [x for x in songs if x["users"] == 0][:100])   # found by Last.fm/MusicBrainz but not on ListenBrainz
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
        at = capped(sorted(songs, key=lambda x: -(0.85 * common.log_scale(internal(x), top_internal) + 0.15 * x["fit"])), 2 * list_size(gid), 3)
        rc = capped(sorted([x for x in songs if len(x.get("released") or "") == 10 and x["released"] >= year_ago],
                           key=lambda x: -internal(x)), 2 * list_size(gid), 3)
        tr = capped(sorted([x for x in songs if (x.get("released") or "") >= two_years
                            or week_chart.get(x["mbid"]) or week_chart.get(skey(x))], key=lambda x: -internal(x)), list_size(gid) + 10, 3)
        prepared[gid] = (name, tags, songs, at, rc, tr)
    prepared = {g: prepared[g] for g in GENRES}   # "All" back to the front

    # 2. Find each candidate's official YouTube video, at most YOUTUBE_LOOKUPS new songs a day,
    #    taking genres in turn so every genre fills at the same pace.
    lookups = [0]
    search_budget, channel_budget = [YOUTUBE_LOOKUPS], [CHANNELS_PER_RUN]
    if yt and yt.available:
        queues = [[x for trio in zip(at, rc + [None] * 40, tr + [None] * 40) for x in trio if x]
                  for (_, _, _, at, rc, tr) in prepared.values()]
        for i in range(max((len(q) for q in queues), default=0)):
            for q in queues:
                if i < len(q) and _todo(state["yt"].get(skey(q[i])), now, q[i]):
                    found = _youtube_video(yt, q[i], state, search_budget, channel_budget)
                    if found != "later":   # "later": needs a search, and today's searches are used up
                        lookups[0] += 1
                        state["yt"][skey(q[i])] = found or {"none": today}

    # 3. Today's view counts for every known video (1 quota unit per 50 videos)
    cat = state.setdefault("catalog", {})
    ids = sorted({v["id"] for v in state["yt"].values() if v.get("id")} | set(cat))
    views = _youtube_views(yt, ids) if yt and yt.available else {}
    for vid, n in views.items():
        h = state["yt_views"].setdefault(vid, {})
        h[today] = n
    cut = (now - timedelta(days=10)).strftime("%Y-%m-%d")

    def yt_of(x):
        v = state["yt"].get(skey(x)) or {}
        if not v.get("id"):
            return None
        n = views.get(v["id"]) or max((state["yt_views"].get(v["id"]) or {"": 0}).values())
        return {**v, "views": n} if n else None

    pending = sum(1 for (_, _, _, at, rc, tr) in prepared.values() for x in at + rc + tr if _todo(state["yt"].get(skey(x)), now, x))

    # 4. Discovery -> catalogue (owner, 14 Nov 2026). Today's verified songs per genre are ADDED to the
    #    persistent catalogue; nothing found earlier is dropped because today's fetches came up short.
    today_views = dict(views)
    removed: dict[tuple, str] = {}   # (genre, video id) -> documented reason it left
    why_new: dict[tuple, str] = {}   # (genre, video id) -> why it is new in that genre
    if not cat:
        _seed_catalog(cat, today)
    for gid, (name, tags, songs, at, rc, tr) in prepared.items():
        def with_yt(cands):
            return [{**x, "yt": yt_of(x)} for x in cands if yt_of(x) and core_artist(gid, x, key, artist_tags, tag_budget)]

        pool = {}
        for x in at + rc + tr:
            pool.setdefault(skey(x), x)
        a_rows = with_yt(pool.values())
        if gid != "all" and yt and yt.available and not yt.exhausted:
            tp = _topics(yt, [x["yt"]["id"] for x in a_rows])
            # songs the music databases put in the genre stay unless YouTube's labels clearly name other genres
            a_rows = [x for x in a_rows if not tp.get(x["yt"]["id"]) or _genre_fits(gid, tp[x["yt"]["id"]], x["yt"])]
        g_rows = _global_top(yt, gid, key, artist_tags, tag_budget)
        for rows, via in ((a_rows, "music databases + official YouTube video"), (g_rows, "YouTube's most-viewed music videos"),
                          (with_yt(rc), "new releases"), (with_yt(tr), "this week's charts")):
            for x in rows:
                if not x.get("yt") or not x["yt"].get("id"):
                    continue
                if x["yt"].get("views"):
                    today_views.setdefault(x["yt"]["id"], x["yt"]["views"])
                new, new_g = music_catalog.upsert(cat, x, gid, via, today)
                if new_g:
                    why_new[(gid, x["yt"]["id"])] = f"newly found today ({via})" if new else f"newly verified for this genre ({via})"

    # 5. Re-check the catalogue: today's views (last known kept when YouTube can't answer) and the
    #    documented reasons a song may leave.
    for vid, n in today_views.items():
        state["yt_views"].setdefault(vid, {})[today] = n
    got_views = bool(views)
    for vid, e in list(cat.items()):
        if vid in today_views:
            e["views"], e["views_at"] = today_views[vid], today
            e.pop("missing", None)
        elif got_views and vid in ids:
            e["missing"] = e.get("missing", 0) + 1   # YouTube answered but not for this video
        bad = _incorrect_upload(e)
        if not bad and e.get("missing", 0) >= 3:
            bad = "video no longer on YouTube (missing 3 runs in a row)"
        if bad:
            for g in list(e["genres"]) + ["all"]:
                removed[(g, vid)] = bad
            del cat[vid]
            continue
        for g in list(e["genres"]):
            if g in ("all", "arabic") or g not in GENRE_CORE:
                continue
            t = _artist_tags_lastfm(e["artist"], key, artist_tags, tag_budget)
            if t and not re.search(CORE_TAGS.get(g) or GENRE_CORE[g], " | ".join(t.split(" | ")[:5])):
                removed[(g, vid)] = f"ineligible genre: {e['artist']}'s top 5 Last.fm tags don't include {GENRES[g][0]}"
                del e["genres"][g]
        if not e["genres"]:
            removed[("all", vid)] = removed.get(("all", vid)) or "no genre left"
            del cat[vid]

    # 6. Rank: every eligible song in the catalogue, sorted by the list's own number, top N shown.
    mb_cache, mb_budget = state.setdefault("mb_dates", {}), [MB_DATES_PER_RUN]
    week_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d")

    def release_of(vid: str, e: dict) -> str | None:
        apple = _itunes(music_catalog.as_row(vid, e), state["itunes"], itunes_budget)
        mb = _mb_first_release(e["artist"], e["title"], mb_cache, mb_budget)
        e["released"] = original_date(mb, e.get("released_src"), (apple or {}).get("released"),
                                      (e["video"].get("published") or "")[:10] or None)
        e["date_source"] = "MusicBrainz" if mb else "earliest of Apple, the music databases and the video"
        return e["released"]

    def is_new(d: str | None) -> bool:
        return bool(d) and (d >= year_ago if len(d) == 10 else d[:4] > year_ago[:4])

    def card(vid: str, e: dict, rank: int, why: str) -> dict:
        x = music_catalog.as_row(vid, e)
        apple = _itunes(x, state["itunes"], itunes_budget)
        v = x["yt"]
        return {
            "id": x["mbid"], "rank": rank, "title": x["title"], "artist": x["artist"], "album": x.get("album"),
            "released": release_of(vid, e),
            "artwork": (apple or {}).get("artwork") or x.get("cover"),
            "preview": (apple or {}).get("preview"),
            "metrics": {"views": v["views"], "source": "YouTube", "channel": v.get("channel"), "genre_fit": round(x["fit"], 2),
                        "views_at": e.get("views_at")},
            "why": why, "video": embed({**v, "published": v.get("published")}),
            "links": [{"name": "YouTube", "url": f"https://www.youtube.com/watch?v={vid}"}] + _links(x, apple),
        }

    prev_lists = state.get("last_lists") or _lists_from_last()
    genres, changes, violations, last_lists = {}, {}, [], {}
    entries = list(cat.items())
    gains = {vid: music_catalog.weekly_gain(state["yt_views"].get(vid) or {}, today, week_ago) for vid, _ in entries}
    for gid, (name, tags) in GENRES.items():
        n = list_size(gid)
        elig = entries if gid == "all" else [(v, e) for v, e in entries if gid in e["genres"]]
        reasons: dict[str, dict] = {"all_time": {}, "trending": {}, "recent": {}}

        def views_of(e):
            return e.get("views") or 0

        # Most Viewed Ever: lifetime views
        at_shown, at_skip, at_order = music_catalog.rank(elig, views_of, n, PER_ARTIST)
        # Most Viewed New Releases: released in the last 12 months (original date), total views
        nr = []
        for v, e in elig:
            if not _posted_since(e["video"], video_cut):
                reasons["trending"][v] = "not a new release (official video over 13 months old)"
            elif not is_new(release_of(v, e)):
                reasons["trending"][v] = f"not a new release (originally released {e['released']})"
            else:
                nr.append((v, e))
        floor = max(50_000, 0.02 * max((views_of(e) for _, e in nr), default=0))
        for v, e in nr:
            if views_of(e) < floor:
                reasons["trending"][v] = f"under the new-release minimum of {floor:,.0f} views"
        nr = [(v, e) for v, e in nr if views_of(e) >= floor]
        nr_shown, nr_skip, nr_order = music_catalog.rank(nr, views_of, n, PER_ARTIST)
        # Trending This Week: views gained per week from valid dated snapshots
        wk = []
        for v, e in elig:
            if gains.get(v):
                wk.append((v, {**e, "_gain": gains[v][0]}))
            else:
                reasons["recent"][v] = "no valid weekly measurement (needs rising daily counts at least a day apart in the last 7 days)"
        wk_shown, wk_skip, wk_order = music_catalog.rank(wk, lambda e: e["_gain"], n, PER_ARTIST)

        lists = {
            "all_time": [card(v, e, i + 1, f"{views_of(e):,} views on YouTube ({e['video'].get('channel')}).")
                         for i, (v, e) in enumerate(at_shown)],
            "trending": [card(v, e, i + 1, f"Released {e.get('released')}; {views_of(e):,} views on YouTube.")
                         for i, (v, e) in enumerate(nr_shown)],
            "recent": [card(v, cat[v], i + 1, f"+{gains[v][0]:,.0f} YouTube views per week (measured over {gains[v][1]} "
                            f"day{'s' if gains[v][1] != 1 else ''}); {views_of(e):,} views in total.")
                       for i, (v, e) in enumerate(wk_shown)],
        }
        for lk, (shown, skip, order, score) in {"all_time": (at_shown, at_skip, at_order, views_of),
                                                 "trending": (nr_shown, nr_skip, nr_order, views_of),
                                                 "recent": (wk_shown, wk_skip, wk_order, lambda e: e["_gain"])}.items():
            violations += [f"{gid}/{lk}: {x}" for x in music_catalog.verify(shown, skip, order, score)]
            reasons[lk].update(skip)
            full_rank = {v: i + 1 for i, (v, _) in enumerate(order)}
            for (g, v), r in removed.items():
                if g == gid or gid == "all":
                    reasons[lk].setdefault(v, r)
            new_rows = [{"vid": v, "title": e["title"], "artist": e["artist"], "rank": i + 1} for i, (v, e) in enumerate(shown)]
            wn = {v: r for (g, v), r in why_new.items() if g == gid or gid == "all"}
            ch = music_catalog.diff((prev_lists.get(gid) or {}).get(lk) or [], new_rows, full_rank, reasons[lk], wn, n)
            if ch:
                changes.setdefault(gid, {})[lk] = ch
            last_lists.setdefault(gid, {})[lk] = new_rows
        notes = {}
        for lk, lst in lists.items():
            if len(lst) < n:
                notes[lk] = (f"{len(lst)} songs in the catalogue qualify so far. The catalogue keeps every verified song and "
                             f"grows daily (YouTube's free limit allows about {YOUTUBE_LOOKUPS} new searches a day).")
        genres[gid] = {"name": name, "tags": tags, **lists, "notes": notes}
        common.progress("music", len(genres), len(GENRES), gid)
        common.log(f"music {gid}: {len(elig)} in catalogue; {len(lists['all_time'])}/{len(lists['trending'])}/{len(lists['recent'])}")

    n_changes = sum(len(c) for g in changes.values() for c in g.values())
    common.log(f"music catalogue: {len(cat)} songs; {n_changes} list changes; {len(violations)} ranking violations")
    for x in violations[:20]:
        common.log(f"music VERIFY: {x}")
    state["last_lists"] = last_lists
    log = [x for x in state.get("music_changelog") or [] if x.get("date") != today][-13:]
    state["music_changelog"] = log + [{"date": today, "changes": changes}]
    per_genre = {g: sum(1 for e in cat.values() if g in e["genres"]) for g in GENRES if g != "all"}
    coverage = {"catalogue_songs": len(cat), "per_genre": per_genre,
                "dates_from_musicbrainz": sum(1 for e in cat.values() if e.get("date_source") == "MusicBrainz"),
                "views_fresh_today": sum(1 for e in cat.values() if e.get("views_at") == today),
                "youtube_lookups_pending": pending, "youtube_quota_ran_out": bool(yt and yt.exhausted),
                "youtube_units_used": yt.units if yt else 0}
    state["yt_views"] = {v: {d: c for d, c in h.items() if d >= cut} for v, h in state["yt_views"].items()
                         if v in ids or v in cat}

    common.log(f"music: {lookups[0]} songs looked up ({YOUTUBE_LOOKUPS - search_budget[0]} by search), "
               f"{len(ids)} videos tracked, {pending} songs still to look up")
    state.pop("yt_uploads", None)    # no longer saved (see _RUN_UPLOADS)
    if len(state["meta"]) > 30000:   # MusicBrainz details (~0.5 KB each): keep the newest 30,000, about 15 MB
        state["meta"] = dict(list(state["meta"].items())[-30000:])
    if len(state["itunes"]) > 6000:
        state["itunes"] = dict(list(state["itunes"].items())[-6000:])
    state.pop("history", None)
    common.save_json(STATE, state)
    return {"genres": genres, "changes": changes, "coverage": coverage,
            "verification": {"lists_checked": 3 * len(genres), "violations": violations},
            "sources": ["YouTube (views, the only ranking)", "Last.fm, Deezer charts, MusicBrainz, ListenBrainz (genre membership)",
                                          "iTunes (artwork, previews)"]}
