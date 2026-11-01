"""Sports -> Upcoming (owner, 1 Nov 2026): the next big events of the major leagues, top 3 each.

Team leagues (ESPN's public scoreboard, one request per day for the next 3 weeks): the 3 most important
games coming up, ranked by how strong both teams are this season (each side's record) with a bonus
for a national TV broadcast, inside the next 7 days (widened to 21 when fewer than 3 games).
Event series (F1, NASCAR, UFC): the next 3 events in date order (F1/NASCAR timed by the race itself).
Boxing, snooker, pool and the IPL aren't on ESPN: TheSportsDB (free key), a couple of requests a day.
A league with nothing announced says so (off-season) instead of being dropped.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from . import common

ESPN = "https://site.api.espn.com/apis/site/v2/sports/{}/scoreboard"
TSDB = "https://www.thesportsdb.com/api/v1/json/123/"
# ESPN's edge refuses the site's usual User-Agent (it carries a URL); this plain one is accepted
ESPN_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; PlanMyDayDiscover/1.0)"}

# (league label, sport icon, source, path or ids, kind)
LEAGUES = [
    ("Premier League", "⚽", "espn", "soccer/eng.1", "games"),
    ("La Liga", "⚽", "espn", "soccer/esp.1", "games"),
    ("Bundesliga", "⚽", "espn", "soccer/ger.1", "games"),
    ("Champions League", "⚽", "espn", "soccer/uefa.champions", "games"),
    ("Serie A", "⚽", "espn", "soccer/ita.1", "games"),
    ("NBA", "🏀", "espn", "basketball/nba", "games"),
    ("NFL", "🏈", "espn", "football/nfl", "games"),
    ("IPL", "🏏", "tsdb", "4460", "events"),            # the biggest cricket league
    ("UFC", "🥋", "espn", "mma/ufc", "events"),
    ("Formula 1", "🏎️", "espn", "racing/f1", "events"),
    ("NASCAR", "🏁", "espn", "racing/nascar-premier", "events"),
    ("MLB", "⚾", "espn", "baseball/mlb", "games"),
    ("NHL", "🏒", "espn", "hockey/nhl", "games"),
    ("Boxing", "🥊", "tsdb", "4445", "events"),
    ("Snooker", "🎱", "tsdb", "4555", "events"),
    ("Pool / Billiards", "🎱", "tsdb", "search:Mosconi Cup|World Pool Championship|9-Ball", "events"),
]
PER_LEAGUE = 3


def _strength(record: str | None, soccer: bool) -> float | None:
    """Share of the points a team has won this season (soccer W-D-L as points per game)."""
    try:
        parts = [int(x) for x in (record or "").split("-")]
    except ValueError:
        return None
    if soccer and len(parts) == 3:
        w, d, l = parts
        games = w + d + l
        return (3 * w + d) / (3 * games) if games else None
    if len(parts) >= 2:
        w, rest = parts[0], sum(parts[1:])
        return w / (w + rest) if w + rest else None
    return None


def _espn_day(path: str, day: str) -> list[dict]:
    try:
        return (common.get(ESPN.format(path), params={"dates": day}, headers=ESPN_HEADERS) or {}).get("events") or []
    except common.FetchError as exc:
        common.log(f"upcoming {path} {day}: {exc}")
        return []


def _espn_games(league: str, path: str, now) -> list[dict]:
    soccer = path.startswith("soccer/")
    games, seen = [], set()
    for offset in range(22):
        day = (now + timedelta(days=offset)).strftime("%Y%m%d")
        for e in _espn_day(path, day):
            if e.get("id") in seen or ((e.get("status") or {}).get("type") or {}).get("state") != "pre":
                continue
            seen.add(e.get("id"))
            c = (e.get("competitions") or [{}])[0]
            teams = c.get("competitors") or []
            strengths = [_strength(((t.get("records") or [{}])[0]).get("summary"), soccer) for t in teams]
            known = [s for s in strengths if s is not None]
            national = any(((b.get("market") or {}).get("type") or "").lower() == "national"
                           for b in c.get("geoBroadcasts") or [])
            tv = sorted({n for b in c.get("broadcasts") or [] for n in b.get("names") or []})
            # soccer: ESPN lists a US streamer for nearly every match, so TV says nothing about importance there
            tv_bonus = 0.15 if national and not soccer else 0
            score = (sum(known) / len(known) + 0.5 * min(known) if known else 0.4) + tv_bonus
            home = next((t for t in teams if t.get("homeAway") == "home"), {})
            away = next((t for t in teams if t.get("homeAway") == "away"), {})
            recs = [f"{(t.get('team') or {}).get('shortDisplayName') or (t.get('team') or {}).get('displayName')} "
                    f"{((t.get('records') or [{}])[0]).get('summary')}" for t in (away, home) if (t.get("records") or [{}])[0].get("summary")]
            games.append({
                "league": league, "title": e.get("name") or e.get("shortName"), "when": e.get("date"),
                "venue": (c.get("venue") or {}).get("fullName"),
                "image": ((home.get("team") or {}).get("logo")) or ((away.get("team") or {}).get("logo")),
                "image2": (away.get("team") or {}).get("logo"),
                "url": ((e.get("links") or [{}])[0]).get("href"), "score": score, "offset": offset,
                "why": ("Season records: " + ", ".join(recs) if recs else "Next scheduled game")
                       + (f"; on {', '.join(tv)}" if tv else "") + (" (national TV)" if national and not soccer else "") + ".",
            })
        time.sleep(0.2)
    window = [g for g in games if g["offset"] < 7]
    if len(window) < PER_LEAGUE:
        window = games
    return sorted(sorted(window, key=lambda g: -g["score"])[:PER_LEAGUE], key=lambda g: g["when"])   # the 3 biggest, soonest first


def _espn_events(league: str, path: str, now) -> list[dict]:
    end = now + timedelta(days=120)
    try:
        events = (common.get(ESPN.format(path), params={"dates": f"{now:%Y%m%d}-{end:%Y%m%d}"}, headers=ESPN_HEADERS) or {}).get("events") or []
    except common.FetchError as exc:
        common.log(f"upcoming {path}: {exc}")
        return []
    out = []
    for e in events:
        comps = e.get("competitions") or []
        race = next((c for c in comps if ((c.get("type") or {}).get("abbreviation") or "").lower() == "race"), None)
        main = race or (comps[-1] if path.startswith("racing") and comps else comps[0] if comps else {})
        when = (main or {}).get("date") or e.get("date")
        start = common.parse_time(when)
        if not start or start < now:
            continue
        venue = (e.get("circuit") or {}).get("fullName") or ((main or {}).get("venue") or {}).get("fullName")
        if path == "mma/ufc":   # the main event is the card's last bout
            bouts = [c for c in comps if len(c.get("competitors") or []) == 2]
            top = bouts[-1] if bouts else None
            names = [((x.get("athlete") or {}).get("displayName")) for x in (top or {}).get("competitors", [])]
            why = f"Main event: {' vs '.join(n for n in names if n)}." if all(names) and names else "Next UFC card."
        else:
            why = "Race day" + (f" at {venue}" if venue else "") + "."
        out.append({"league": league, "title": e.get("name"), "when": when, "venue": venue, "image": None,
                    "url": ((e.get("links") or [{}])[0]).get("href"), "why": why})
    return sorted(out, key=lambda x: x["when"])[:PER_LEAGUE]


def _tsdb(league: str, ids: str, now) -> list[dict]:
    """TheSportsDB: next events of a league (by id), or of named competitions (search:)."""
    rows = []
    try:
        if ids.startswith("search:"):
            for name in ids[7:].split("|"):
                data = common.get(TSDB + "searchevents.php", params={"e": name}) or {}
                rows += data.get("event") or []
                time.sleep(2.5)
        else:
            data = common.get(TSDB + "eventsnextleague.php", params={"id": ids}) or {}
            rows += data.get("events") or []
            time.sleep(2.5)
            season = (common.get(TSDB + "lookupleague.php", params={"id": ids}) or {}).get("leagues") or [{}]
            time.sleep(2.5)
            if season[0].get("strCurrentSeason"):
                data = common.get(TSDB + "eventsseason.php", params={"id": ids, "s": season[0]["strCurrentSeason"]}) or {}
                rows += data.get("events") or []
                time.sleep(2.5)
    except common.FetchError as exc:
        common.log(f"upcoming {league} (TheSportsDB): {exc}")
    out, seen = [], set()
    for r in rows:
        ts = r.get("strTimestamp") or (f"{r['dateEvent']}T{r.get('strTime') or '00:00:00'}" if r.get("dateEvent") else "")
        try:   # TheSportsDB times are UTC
            start = datetime.fromisoformat(ts[:19]).replace(tzinfo=timezone.utc) if ts else None
        except ValueError:
            start = None
        if not start or start < now - timedelta(hours=12) or r.get("idEvent") in seen:
            continue
        seen.add(r.get("idEvent"))
        venue = ", ".join(x for x in (r.get("strVenue"), r.get("strCity") or r.get("strCountry")) if x)
        out.append({"league": league, "title": r.get("strEvent"), "when": common.iso(start), "venue": venue or None,
                    "date_only": start.hour == 0 and start.minute == 0,   # TheSportsDB leaves unknown start times at 00:00
                    "image": r.get("strThumb") or r.get("strPoster"),
                    "url": f"https://www.thesportsdb.com/event/{r.get('idEvent')}",
                    "why": f"{r.get('strLeague') or league}" + (f", {r.get('strSeason')} season" if r.get("strSeason") else "") + "."})
    return sorted(out, key=lambda x: x["when"])[:PER_LEAGUE]


def build(now=None) -> dict:
    now = now or common.now_utc()
    events, empty = [], []
    for league, icon, source, ref, kind in LEAGUES:
        if source == "espn":
            got = _espn_games(league, ref, now) if kind == "games" else _espn_events(league, ref, now)
        else:
            got = _tsdb(league, ref, now)
        for i, g in enumerate(got):
            g.pop("score", None)
            g.pop("offset", None)
            events.append({**g, "id": common.fingerprint(f"{league}|{g['title']}|{g['when']}"), "icon": icon,
                           "rank": i + 1, "source": "ESPN" if source == "espn" else "TheSportsDB"})
        if not got:
            empty.append(league)
        common.log(f"upcoming {league}: {len(got)}")
    return {"icon": "📅", "name": "Upcoming", "leagues": [l[0] for l in LEAGUES], "events": events,
            "off_season": empty, "built_at": common.iso(now)}
