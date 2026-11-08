"""Sports -> Upcoming. Owner, 8 Nov 2026: TWO lists, every game, no top-3 cut -- Football (all football leagues
together) and Other sports (everything else together), each in time order. Team leagues: every game in the next
7 days (a league on a break shows its next round, up to 21 days). Event series: every event in the next 30 days
(at least the next one). The text below describes the original top-3 design; ranking now only orders nothing.

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
    ("Boxing", "🥊", "espnbox", "12508267", "events"),   # ESPN's weekly-updated boxing schedule (all promoters)
    ("Snooker", "🎱", "tsdb", "4555", "events"),
    ("Pool / Billiards", "🎱", "tsdb", "search:Mosconi Cup|World Pool Championship|9-Ball", "events"),
]
PER_LEAGUE = 3

# Big clubs (owner, 8 Nov 2026): Man Utd v Aston Villa was left out because Man Utd's poor season record
# ranked it below smaller fixtures. A game with one of a league's traditionally biggest clubs gets a boost
# on top of the season records, so the matches people actually look for make the top 3.
BIG_CLUBS = {
    "soccer/eng.1": {"Manchester United", "Liverpool", "Arsenal", "Chelsea", "Manchester City", "Tottenham Hotspur"},
    "soccer/esp.1": {"Real Madrid", "Barcelona", "Atlético Madrid"},
    "soccer/ger.1": {"Bayern Munich", "Borussia Dortmund"},
    "soccer/ita.1": {"Juventus", "Internazionale", "AC Milan", "Napoli", "AS Roma"},
}
BIG_CLUBS["soccer/uefa.champions"] = set().union(*BIG_CLUBS.values()) | {"Paris Saint-Germain"}
BIG_CLUB_BONUS = 0.5

# Where to buy (owner, 8 Nov 2026): the most official seller per league. Soccer uses the HOME club's own
# website when it can be found (clubs sell their own tickets); otherwise these league pages.
TICKETS = {
    "Premier League": ("Premier League", "https://www.premierleague.com/en/tickets"),
    "Bundesliga": ("Bundesliga", "https://www.bundesliga.com/en/bundesliga/tickets"),
    "La Liga": None, "Serie A": None, "Champions League": None,   # home club only
    "NBA": ("Ticketmaster (official NBA partner)", "https://www.ticketmaster.com/nba"),
    "NFL": ("NFL Tickets", "https://www.nfl.com/tickets/"),
    "NHL": ("NHL Tickets", "https://www.nhl.com/tickets/"),
    "MLB": ("MLB Tickets", "https://www.mlb.com/tickets"),
    "UFC": ("UFC Tickets", "https://www.ufc.com/tickets"),
    "Formula 1": ("F1 Tickets", "https://tickets.formula1.com/en"),
    "NASCAR": ("NASCAR Tickets", "https://www.nascar.com/tickets/"),
    "IPL": ("IPL (iplt20.com)", "https://www.iplt20.com/"),
    "Boxing": None,   # every card has its own promoter; no single seller
    "Snooker": ("World Snooker Tour", "https://www.wst.tv/tickets/"),
    "Pool / Billiards": ("World Nine-ball Tour", "https://worldnineballtour.com/tickets/"),
}
# Where to watch (owner, 8 Nov 2026): each league's official watch / broadcasters-by-country page where one
# exists (rights differ by country); La Liga and Serie A have none, so LiveSoccerTV's per-country listings.
WATCH = {
    "Premier League": ("Premier League broadcasters by country", "https://www.premierleague.com/en/media/broadcasters"),
    "La Liga": ("LiveSoccerTV (by country)", "https://www.livesoccertv.com/competitions/spain/primera-division/"),
    "Bundesliga": ("Bundesliga: where to watch", "https://www.bundesliga.com/en/faq/where-to-watch-bundesliga"),
    "Champions League": ("UEFA broadcasters by country", "https://www.uefa.com/uefachampionsleague/news/0253-0d82037aaedd-f371c464f919-1000--where-to-watch-the-uefa-champions-league-tv-broadcast-partners-streams/"),
    "Serie A": ("LiveSoccerTV (by country)", "https://www.livesoccertv.com/competitions/italy/serie-a/"),
    "NBA": ("NBA League Pass", "https://www.nba.com/watch/league-pass-stream"),
    "NFL": ("NFL+ / Game Pass", "https://www.nfl.com/plus/"),
    "NHL": ("NHL: how to watch", "https://www.nhl.com/info/how-to-watch-and-stream-nhl-games"),
    "MLB": ("MLB.TV", "https://www.mlb.com/tv"),
    "UFC": ("UFC Fight Pass", "https://ufcfightpass.com/"),
    "Formula 1": ("F1 TV", "https://f1tv.formula1.com/"),
    "NASCAR": ("NASCAR: where to watch", "https://www.nascar.com/where-to-watch/"),
    "IPL": ("IPL (iplt20.com)", "https://www.iplt20.com/"),
    "Snooker": ("WST: where to watch", "https://www.wst.tv/where-to-watch"),
    "Pool / Billiards": ("World Nine-ball Tour", "https://worldnineballtour.com/"),
}
# Boxing: the channel ESPN names for each card, linked when it's one we know the official site of
BOX_CHANNELS = {"ESPN": "https://www.espn.com/watch/", "DAZN": "https://www.dazn.com/", "Netflix": "https://www.netflix.com/",
                "Paramount+": "https://www.paramountplus.com/", "Prime Video": "https://www.primevideo.com/"}
MONTHS = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def _espn_boxing(league: str, story_id: str, now) -> list[dict]:
    """ESPN's boxing schedule article (all promoters, updated weekly), read from ESPN's news API: each card is
    "Nov. 14: Carson, California (TNT and DAZN)" followed by its fights, title fights marked."""
    import html as _html
    import re
    try:
        d = common.get(f"https://now.core.api.espn.com/v1/sports/news/{story_id}", headers=ESPN_HEADERS) or {}
    except common.FetchError as exc:
        common.log(f"upcoming boxing: {exc}")
        return []
    story = ((d.get("headlines") or [{}])[0]).get("story") or ""
    lines = [l.strip() for l in _html.unescape(re.sub(r"<[^>]+>", "\n", story)).splitlines() if l.strip()]
    if "Full schedule:" in lines:
        lines = lines[lines.index("Full schedule:") + 1:]
    head = re.compile(r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sept?|Oct|Nov|Dec)\.? (\d{1,2}): (.+?)(?: \(([^)]+)\))?$")
    cards, cur, title_next = [], None, False
    for l in lines:
        m = head.match(l)
        if m:
            mon, day = MONTHS[m.group(1)[:3]], int(m.group(2))
            year = now.year + (1 if mon < now.month - 2 else 0)
            try:
                when = datetime(year, mon, day, tzinfo=timezone.utc)
            except ValueError:
                cur = None
                continue
            cur = {"when": when, "place": m.group(3).strip(), "tv": (m.group(4) or "").strip(), "fights": []}
            cards.append(cur)
            continue
        if cur is None:
            continue
        if l.rstrip(":").lower() == "title fight":
            title_next = True
            continue
        if " vs. " in l:
            cur["fights"].append({"text": l, "title": title_next or " for " in l and "title" in l})
        title_next = False
    out = []
    for c in cards:
        if c["when"] + timedelta(days=1) < now or not c["fights"]:   # dates only: today's card stays all day
            continue
        main = c["fights"][0]["text"]
        bout = main.split(",")[0]
        titles = [f["text"] for f in c["fights"] if f["title"]]
        why = f"Main event: {main}." + (f" Title fights on the card: {len(titles)}." if titles else "") + f" {len(c['fights'])} fight{'s' if len(c['fights']) != 1 else ''} in all."
        tv = c["tv"]
        first = next((n for n in BOX_CHANNELS if n.lower() in tv.lower()), None)
        ev = {"league": league, "title": bout, "when": common.iso(c["when"]), "venue": c["place"], "date_only": True,
              "image": None, "url": "https://www.espn.com/boxing/story/_/id/12508267/boxing-schedule", "why": why}
        ev["watch"] = {"name": tv, "url": BOX_CHANNELS[first] if first else None} if tv else {"name": "not announced yet", "url": None}
        out.append(ev)
    return _next_30_days(out, now, days=60)   # big fights are announced weeks ahead


WIKIDATA = "https://www.wikidata.org/w/api.php"
CLUB_SITES = common.STATE / "club_sites.json"
_club_cache: dict | None = None


def _club_site(name: str) -> str | None:
    """A football club's official website from Wikidata (P856), cached between runs."""
    global _club_cache
    if _club_cache is None:
        _club_cache = common.load_json(CLUB_SITES, {})
    if name in _club_cache:
        return _club_cache[name]
    url = None
    try:
        found = (common.get(WIKIDATA, params={"action": "wbsearchentities", "search": name, "language": "en",
                                               "type": "item", "limit": 5, "format": "json"}) or {}).get("search") or []
        kinds = ("football club", "sports club", "football team", "soccer club", "multi-sport club")
        club = next((x for x in found if any(k in (x.get("description") or "").lower() for k in kinds)), None)
        if club:
            ent = (common.get(WIKIDATA, params={"action": "wbgetentities", "ids": club["id"], "props": "claims",
                                                 "format": "json"}) or {}).get("entities", {}).get(club["id"], {})
            sites = [c["mainsnak"]["datavalue"]["value"] for c in ent.get("claims", {}).get("P856", [])
                     if c.get("mainsnak", {}).get("datavalue")]
            url = sites[0] if sites else None
    except (common.FetchError, KeyError, TypeError) as exc:
        common.log(f"club site {name}: {exc}")
        return None   # not cached: try again next run
    _club_cache[name] = url
    common.save_json(CLUB_SITES, _club_cache)
    time.sleep(0.5)
    return url


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
            home = next((t for t in teams if t.get("homeAway") == "home"), {})
            away = next((t for t in teams if t.get("homeAway") == "away"), {})
            names = {((t.get("team") or {}).get("displayName") or "") for t in (home, away)}
            big = names & BIG_CLUBS.get(path, set())
            score = (sum(known) / len(known) + 0.5 * min(known) if known else 0.4) + tv_bonus + BIG_CLUB_BONUS * len(big)
            recs = [f"{(t.get('team') or {}).get('shortDisplayName') or (t.get('team') or {}).get('displayName')} "
                    f"{((t.get('records') or [{}])[0]).get('summary')}" for t in (away, home) if (t.get("records") or [{}])[0].get("summary")]
            games.append({
                "league": league, "title": e.get("name") or e.get("shortName"), "when": e.get("date"),
                "venue": (c.get("venue") or {}).get("fullName"),
                "image": ((home.get("team") or {}).get("logo")) or ((away.get("team") or {}).get("logo")),
                "image2": (away.get("team") or {}).get("logo"),
                "url": ((e.get("links") or [{}])[0]).get("href"), "score": score, "offset": offset,
                "home": (home.get("team") or {}).get("displayName"),
                "why": ("Season records: " + ", ".join(recs) if recs else "Next scheduled game")
                       + (f"; on {', '.join(tv)}" if tv else "") + (" (national TV)" if national and not soccer else "")
                       + (f"; big club{'s' if len(big) > 1 else ''}: {', '.join(sorted(big))}" if big else "") + ".",
            })
        time.sleep(0.2)
    window = [g for g in games if g["offset"] < 7]
    if not window and games:   # a break (e.g. international week): the league's next round
        first = min(g["offset"] for g in games)
        window = [g for g in games if g["offset"] < first + 4]
    return sorted(window, key=lambda g: g["when"])   # every game, soonest first (owner, 8 Nov 2026)


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
    return _next_30_days(out, now)


def _next_30_days(rows: list[dict], now, days: int = 30) -> list[dict]:
    """Every event in the next 30 days (boxing: 60), soonest first; at least the next one when none is that close."""
    rows = sorted(rows, key=lambda x: x["when"])
    soon = [r for r in rows if (common.parse_time(r["when"]) or now) <= now + timedelta(days=days)]
    return soon or rows[:1]


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
    return _next_30_days(out, now)


# Upcoming tabs (owner, 8 Nov 2026): football together, each big US league on its own (each is about
# football-sized in season), everything else together.
GROUP_OF = {"NBA": "nba", "NHL": "nhl", "NFL": "nfl", "MLB": "mlb"}


def _group(league: str, ref: str) -> str:
    return "football" if ref.startswith("soccer/") else GROUP_OF.get(league, "more")


def build(now=None) -> dict:
    now = now or common.now_utc()
    events, empty = [], []
    for league, icon, source, ref, kind in LEAGUES:
        if source == "espn":
            got = _espn_games(league, ref, now) if kind == "games" else _espn_events(league, ref, now)
        elif source == "espnbox":
            got = _espn_boxing(league, ref, now) or _tsdb(league, "4445", now)   # TheSportsDB if ESPN's page changes
        else:
            got = _tsdb(league, ref, now)
        for i, g in enumerate(got):
            g.pop("score", None)
            g.pop("offset", None)
            home = g.pop("home", None)
            site = _club_site(home) if home and ref.startswith("soccer/") else None
            if site:
                g["tickets"] = {"name": f"{home} official site", "url": site}
            elif TICKETS.get(league):
                g["tickets"] = {"name": TICKETS[league][0], "url": TICKETS[league][1]}
            if "watch" not in g and WATCH.get(league):
                g["watch"] = {"name": WATCH[league][0], "url": WATCH[league][1]}
            events.append({**g, "id": common.fingerprint(f"{league}|{g['title']}|{g['when']}"), "icon": icon,
                           "rank": i + 1, "source": "ESPN" if source in ("espn", "espnbox") else "TheSportsDB",
                           "group": _group(league, ref)})
        if not got:
            empty.append(league)
        common.log(f"upcoming {league}: {len(got)}")
    return {"icon": "📅", "name": "Upcoming", "leagues": [l[0] for l in LEAGUES], "events": events,
            "groups": {g: [l[0] for l in LEAGUES if _group(l[0], l[3]) == g]
                       for g in ("football", "nba", "nhl", "nfl", "mlb", "more")},
            "tickets": {k: {"name": v[0], "url": v[1]} for k, v in TICKETS.items() if v},
            "watch": {k: {"name": v[0], "url": v[1]} for k, v in WATCH.items()},
            "off_season": empty, "built_at": common.iso(now)}
