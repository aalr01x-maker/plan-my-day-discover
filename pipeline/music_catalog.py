"""The music catalogue (owner, 14 Nov 2026): every verified song is kept between runs.

Before, each run rebuilt every list from that run's fetches, so when YouTube's daily quota ran out or
MusicBrainz returned 503s, songs such as Despacito silently fell off. Now:

  * every song verified once (official YouTube upload + genre check) stays in state "catalog", keyed by
    its YouTube video id, with the genres it belongs to and its last known view count;
  * a song leaves a genre only for a documented reason: incorrect upload (cover, kids', compilation,
    anniversary re-upload), the video gone from YouTube, or an ineligible genre (the artist's Last.fm
    tags, when known, lack the genre);
  * each list sorts EVERY eligible song in the catalogue by its metric and shows the top N, so a new
    song lands at its rank and pushes the lower ones down;
  * every addition and removal is reported with its reason and its before/after rank.

These are pure functions; music.build() feeds them.
"""
from __future__ import annotations

import re

from . import common


def same_song_key(artist: str, title: str) -> str:
    """One key per song across uploads: first word of the artist + the title before any feat., brackets
    or separator ("Bob Marley" and "Bob Marley & The Wailers" are one act; "Nano" and "Nano I <Arabic> - <Arabic>" one song)."""
    first = (common.norm(artist).split() or [""])[0]
    head = re.split(r"[\(\[]| ft\.| feat|\s\|\s?|\s/\s|\s[-–—]\s|\sI\s(?=.*[؀-ۿ])", title)[0]
    return common.norm(first + " " + head).strip()


def upsert(cat: dict, row: dict, gid: str, via: str, today: str) -> tuple[bool, bool]:
    """Adds a verified song (row with "yt": {id, title, channel, published, views}) to the catalogue
    and to genre `gid`. Returns (new to the catalogue, new to this genre)."""
    v = row["yt"]
    e = cat.get(v["id"])
    new = e is None
    if new:
        e = cat[v["id"]] = {"artist": row["artist"], "title": row["title"], "album": row.get("album"),
                            "mbid": row.get("mbid"), "cover": row.get("cover"), "fit": round(row.get("fit") or 1.0, 2),
                            "released_src": row.get("released"), "first_seen": today, "genres": {},
                            "video": {"id": v["id"], "title": v.get("title"), "channel": v.get("channel"),
                                      "published": v.get("published")}}
    if v.get("views"):
        e["views"], e["views_at"] = v["views"], today
    if not e.get("released_src") and row.get("released"):
        e["released_src"] = row["released"]
    in_genre = gid in e["genres"]
    if not in_genre:
        e["genres"][gid] = {"since": today, "via": via}
    return new, not in_genre


def as_row(vid: str, e: dict) -> dict:
    """A catalogue entry in the shape music.build()'s cards use."""
    return {"mbid": e.get("mbid") or f"yt:{vid}", "artist": e["artist"], "title": e["title"], "album": e.get("album"),
            "cover": e.get("cover"), "fit": e.get("fit") or 1.0, "released": e.get("released") or e.get("released_src"),
            "yt": {**e["video"], "views": e.get("views") or 0}}


def weekly_gain(history: dict, today_str: str, week_ago_str: str) -> tuple[float, int] | None:
    """Views gained per 7 days from dated snapshots {YYYY-MM-DD: views} within the last week. Valid only
    with two or more snapshots at least a day apart that never go down (a drop means YouTube recounted,
    so the measurement is void)."""
    days = sorted(d for d in history if week_ago_str <= d <= today_str)
    if len(days) < 2:
        return None
    counts = [history[d] for d in days]
    if any(b < a for a, b in zip(counts, counts[1:])):
        return None
    span = (common.parse_time(days[-1]) - common.parse_time(days[0])).days
    if span < 1:
        return None
    gain = (counts[-1] - counts[0]) * 7 / span
    return (gain, span) if gain > 0 else None


def rank(entries: list[tuple[str, dict]], score, n: int, per_artist: int) -> tuple[list[tuple[str, dict]], dict, list]:
    """Sorts ALL eligible entries by score (high first) and takes the top n: one upload per song (the most
    scored one), at most `per_artist` songs per artist. Returns (shown, skipped {vid: reason}, full order)."""
    order = sorted(entries, key=lambda t: score(t[1]), reverse=True)
    shown, skipped, seen_song, per = [], {}, {}, {}
    for vid, e in order:
        if len(shown) >= n:
            break
        k = same_song_key(e["artist"], e["title"])
        if k in seen_song:
            skipped[vid] = f"duplicate upload of the same song (kept the higher one, video {seen_song[k]})"
            continue
        seen_song[k] = vid
        a = common.norm(e["artist"])
        if per.get(a, 0) >= per_artist:
            skipped[vid] = f"artist limit: at most {per_artist} songs per artist in a list"
            continue
        per[a] = per.get(a, 0) + 1
        shown.append((vid, e))
    return shown, skipped, order


def verify(shown: list[tuple[str, dict]], skipped: dict, order: list[tuple[str, dict]], score) -> list[str]:
    """Owner's check: no higher-scoring eligible song may be missing while a lower one is shown, unless it
    was skipped for a documented reason. Returns the violations (should be none)."""
    if not shown:
        return []
    floor = min(score(e) for _, e in shown)
    ids = {vid for vid, _ in shown}
    return [f"{e['artist']} - {e['title']} ({score(e):,.0f}) missing above the lowest shown ({floor:,.0f})"
            for vid, e in order if score(e) > floor and vid not in ids and vid not in skipped]


def diff(prev: list[dict], new: list[dict], full_rank: dict, reasons: dict, why_new: dict, n: int) -> list[dict]:
    """Additions and removals between yesterday's and today's list, each with its reason and ranks.
    prev/new: [{"vid", "title", "artist", "rank"}]; full_rank: vid -> rank among all eligible songs;
    reasons: vid -> documented reason it left; why_new: vid -> why it is newly eligible."""
    before = {x["vid"]: x for x in prev}
    after = {x["vid"]: x for x in new}
    out = []
    for x in new:
        if x["vid"] not in before:
            out.append({"change": "added", "title": x["title"], "artist": x["artist"], "before": None, "after": x["rank"],
                        "reason": why_new.get(x["vid"]) or f"rose into the top {n} on its numbers"})
    for x in prev:
        if x["vid"] not in after:
            r = reasons.get(x["vid"])
            if not r:
                k = full_rank.get(x["vid"])
                r = f"fell below rank {n} (now #{k})" if k else "no longer eligible"
            out.append({"change": "removed", "title": x["title"], "artist": x["artist"], "before": x["rank"],
                        "after": full_rank.get(x["vid"]), "reason": r})
    return out
