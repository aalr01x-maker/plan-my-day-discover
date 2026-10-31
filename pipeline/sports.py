"""Sports: per sport (the owner's 11 from Plan My Day), with league filters --
  * Top Highlights (7 days): official highlight videos from league/promotion YouTube
    channels, ranked by their real view counts.
  * Major Results & Events (7 days): results, titles, records, injuries, bans,
    controversies -- from BBC Sport / ESPN / HLTV, ranked by corroboration across
    outlets, weight of the event and recency.
  * Latest (24 h): the newest meaningful items.
Outdated competitions are mapped to current ones (XFL -> UFL, Bellator -> PFL,
Overwatch League -> OWCS, LCS -> LTA).
"""
from __future__ import annotations

import math
import re

import feedparser

from . import common
from .youtube import YouTube, embed

SPORTS = {
    "soccer": {"name": "Soccer", "icon": "⚽",
               "leagues": {"Premier League": "premier league, epl, arsenal, liverpool, man city, manchester city, man utd, manchester united, chelsea, tottenham, newcastle, aston villa", "La Liga": "la liga, laliga, real madrid, barcelona, atletico madrid",
                           "Bundesliga": "bundesliga, bayern munich, dortmund, leverkusen", "Champions League": "champions league, ucl",
                           "Serie A": "serie a, inter milan, juventus, napoli, ac milan"},
               "feeds": ["https://feeds.bbci.co.uk/sport/football/rss.xml", "https://www.espn.com/espn/rss/soccer/news"],
               "channels": ["@premierleague", "@LaLiga", "@bundesliga", "@ChampionsLeague"]},
    "basketball": {"name": "Basketball", "icon": "🏀",
                   "leagues": {"NBA": "nba, lakers, celtics, warriors, knicks, thunder, nuggets", "EuroLeague": "euroleague", "CBA": "cba, chinese basketball association"},
                   "feeds": ["https://www.espn.com/espn/rss/nba/news", "https://feeds.bbci.co.uk/sport/basketball/rss.xml",
                             "https://sports.yahoo.com/nba/rss.xml", "https://www.cbssports.com/rss/headlines/nba/"],
                   "channels": ["@NBA", "@Euroleague"]},
    "american-football": {"name": "American Football", "icon": "🏈",
                          "leagues": {"NFL": "nfl, packers, chiefs, eagles, cowboys, patriots, 49ers, super bowl", "CFL": "cfl, grey cup", "UFL": "ufl"},
                          "feeds": ["https://www.espn.com/espn/rss/nfl/news", "https://feeds.bbci.co.uk/sport/american-football/rss.xml",
                                    "https://sports.yahoo.com/nfl/rss.xml", "https://www.cbssports.com/rss/headlines/nfl/"],
                          "channels": ["@NFL", "@CFL"]},
    "cricket": {"name": "Cricket", "icon": "🏏",
                "leagues": {"IPL": "ipl, indian premier league", "BBL": "bbl, big bash", "PSL": "psl, pakistan super league",
                            "International": "test match, odi, t20 world cup, ashes, world cup, series"},
                "feeds": ["https://feeds.bbci.co.uk/sport/cricket/rss.xml"],
                "channels": ["@IPL", "@cricketcomau", "@ICC"]},
    "mma": {"name": "MMA", "icon": "🥋",
            "leagues": {"UFC": "ufc", "ONE Championship": "one championship", "PFL": "pfl, bellator"},
            "feeds": ["https://feeds.bbci.co.uk/sport/mixed-martial-arts/rss.xml", "https://www.espn.com/espn/rss/mma/news",
                      "https://sports.yahoo.com/mma/rss.xml", "https://www.cbssports.com/rss/headlines/mma/"],
            "channels": ["@UFC", "@ONEChampionship", "@PFLMMA"]},
    "motorsports": {"name": "Motorsports", "icon": "🏎️",
                    "leagues": {"Formula 1": "f1, formula one, formula 1, grand prix", "NASCAR": "nascar", "IndyCar": "indycar, indy 500"},
                    "feeds": ["https://feeds.bbci.co.uk/sport/formula1/rss.xml", "https://www.espn.com/espn/rss/rpm/news",
                              "https://www.motorsport.com/rss/f1/news/"],
                    "channels": ["@Formula1", "@NASCAR", "@IndyCar"]},
    "baseball": {"name": "Baseball", "icon": "⚾",
                 "leagues": {"MLB": "mlb, world series, yankees, dodgers, red sox", "NPB": "npb, japan series", "KBO": "kbo, korean series"},
                 "feeds": ["https://www.espn.com/espn/rss/mlb/news", "https://sports.yahoo.com/mlb/rss.xml", "https://www.cbssports.com/rss/headlines/mlb/"],
                 "channels": ["@MLB"]},
    "esports": {"name": "Esports", "icon": "🎮",
                "leagues": {"League of Legends": "league of legends, lol worlds, lck, lpl, lec, lta", "Counter-Strike": "counter strike, cs2, blast, iem, esl pro league, pgl, major",
                            "Valorant": "valorant, vct", "Overwatch": "overwatch, owcs"},
                "feeds": ["https://www.hltv.org/rss/news"],
                "channels": ["@lolesports", "@ESLCS", "@VALORANTEsports"]},
    "hockey": {"name": "Hockey", "icon": "🏒",
               "leagues": {"NHL": "nhl, stanley cup", "KHL": "khl", "IIHF": "iihf, ice hockey world championship"},
               "feeds": ["https://www.espn.com/espn/rss/nhl/news", "https://feeds.bbci.co.uk/sport/ice-hockey/rss.xml",
                         "https://sports.yahoo.com/nhl/rss.xml", "https://www.cbssports.com/rss/headlines/nhl/"],
               "channels": ["@NHL", "@IIHFHockey"]},
    "boxing": {"name": "Boxing", "icon": "🥊",
               "leagues": {"World titles": "wbc, wba, ibf, wbo, world title, undisputed, title fight"},
               "feeds": ["https://feeds.bbci.co.uk/sport/boxing/rss.xml", "https://www.espn.com/espn/rss/boxing/news",
                         "https://sports.yahoo.com/boxing/rss.xml", "https://www.cbssports.com/rss/headlines/boxing/"],
               "channels": ["@MatchroomBoxing", "@TopRankBoxing", "@DAZNBoxing"]},
    "snooker": {"name": "Snooker / Billiards", "icon": "🎱",
                "leagues": {"World Snooker Tour": "snooker, wst, open title, masters, uk championship",
                            "Pool": "mosconi, world pool, pool championship"},
                "feeds": ["https://feeds.bbci.co.uk/sport/snooker/rss.xml", "https://www.theguardian.com/sport/snooker/rss"],
                "channels": ["@WorldSnookerTour", "@MatchroomPool"],
                # WST titles its match videos "A vs B | Event" / "FULL MATCH", not "highlights"
                "highlight_extra": r" vs | v | full match|final|decider|147|maximum"},
}

MAJOR = ("wins win won beat beats beaten victory defeat final title champion champions championship crowned record "
         "knockout ko tko submission retires retirement injury injured ban banned suspended suspension red card "
         "controversy fine fined sacked fired trade signs signing undefeated upset stuns hat trick century "
         "pole podium crash disqualified penalty").split()
NOISE = ("preview predictions picks odds fantasy rankings podcast quiz how to watch betting mock draft").split()
SOURCE_TIER = {"BBC Sport": 0.95, "ESPN": 0.95, "HLTV": 0.85}


SOURCES = {"bbc": "BBC Sport", "espn": "ESPN", "hltv": "HLTV", "yahoo": "Yahoo Sports", "cbssports": "CBS Sports",
           "motorsport.com": "Motorsport.com", "theguardian": "The Guardian"}


def _source(url: str) -> str:
    return next((name for key, name in SOURCES.items() if key in url), "Source")


def _league(text: str, sport: dict, default: str | None = None) -> str | None:
    """The competition named in the text (whole phrases only), else `default`."""
    t = " " + common.norm(text) + " "
    for league, phrases in sport["leagues"].items():
        for phrase in [league] + phrases.split(","):
            if f" {common.norm(phrase).strip()} " in t:
                return league
    return default


def _items(sport_id: str, sport: dict, now) -> list[dict]:
    out = []
    for url in sport["feeds"]:
        try:
            parsed = feedparser.parse(common.get(url, as_json=False))
        except common.FetchError as exc:
            common.log(f"sports feed failed ({sport_id}): {exc}")
            continue
        if not parsed.entries:  # some publishers answer cloud servers with an empty page
            common.log(f"sports feed empty ({sport_id}): {_source(url)}")
        for e in parsed.entries:
            published = common.parse_time(e.get("published_parsed") or e.get("published"))
            title = common.strip_html(e.get("title", ""))
            if not published or not title or not common.within(published, 7 * 24, now):
                continue
            summary = common.strip_html(e.get("summary", ""))[:400]
            text = f"{title} {summary}"
            words = set(common.norm(text).split())
            noise = sum(1 for w in NOISE if w in words)
            weight = min(1.0, sum(1 for w in MAJOR if w in words) / 3) - 0.4 * noise
            image = None
            for key in ("media_thumbnail", "media_content"):
                media = e.get(key) or []
                if media and media[0].get("url"):
                    image = media[0]["url"]
                    break
            source = _source(url)
            out.append({"title": title, "summary": summary, "url": e.get("link"), "source": source,
                        "published": published, "league": _league(text, sport, "Counter-Strike" if source == "HLTV" else None),
                        "weight": weight, "image": image})
    return out


def _group(items: list[dict]) -> list[dict]:
    events = []
    for it in sorted(items, key=lambda i: i["published"]):
        for ev in events:
            if any(common.similarity(it["title"], a["title"]) >= 0.4 for a in ev["articles"]):
                ev["articles"].append(it)
                break
        else:
            events.append({"articles": [it]})
    for ev in events:
        arts = ev["articles"]
        lead = max(arts, key=lambda a: (SOURCE_TIER.get(a["source"], 0.5), len(a["summary"])))
        ev.update(lead=lead, outlets=sorted({a["source"] for a in arts}),
                  latest=max(a["published"] for a in arts), weight=max(a["weight"] for a in arts),
                  league=next((a["league"] for a in arts if a["league"]), None))
    return events


def _event_card(ev: dict, score: float) -> dict:
    lead = ev["lead"]
    return {
        "id": common.fingerprint(common.norm(lead["title"])), "title": lead["title"], "summary": lead["summary"],
        "league": ev["league"],
        "published": common.iso(ev["latest"]), "score": round(score, 4),
        "why": f"Reported by {', '.join(ev['outlets'])}" + ("; a result, title, record or major incident" if ev["weight"] > 0.3 else ""),
        "image": next((a["image"] for a in ev["articles"] if a["image"]), None),
        "sources": [{"name": a["source"], "title": a["title"], "url": a["url"]} for a in ev["articles"][:4]],
    }


def _channel_ids(yt: YouTube, handles: list[str]) -> dict[str, str]:
    """@handle -> channel id (1 unit each, cached for good)."""
    cache = yt.cache.setdefault("handles", {})
    out = {}
    for h in handles:
        if h not in cache:
            try:
                data = common.get("https://www.googleapis.com/youtube/v3/channels",
                                  params={"part": "id", "forHandle": h, "key": yt.key})
                yt.units += 1
                cache[h] = (data.get("items") or [{}])[0].get("id")
            except common.FetchError as exc:
                common.log(f"youtube handle {h}: {exc}")
                continue
        if cache.get(h):
            out[h] = cache[h]
    return out


def _views(yt: YouTube, ids: list[str]) -> dict[str, int]:
    views = {}
    for i in range(0, len(ids), 50):
        try:
            data = common.get("https://www.googleapis.com/youtube/v3/videos",
                              params={"part": "statistics", "id": ",".join(ids[i:i + 50]), "key": yt.key})
            yt.units += 1
        except common.FetchError as exc:
            common.log(f"youtube stats: {exc}")
            continue
        for item in data.get("items", []):
            views[item["id"]] = int(item.get("statistics", {}).get("viewCount", 0))
    return views


HIGHLIGHT = re.compile(r"highlight|best moments|top \d+|top plays|goals|knockout|every |full fight|extended|recap|finish", re.I)


def build(ai=None, yt: YouTube | None = None) -> dict:
    now = common.now_utc()
    out = {}
    for sport_id, sport in SPORTS.items():
        events = _group(_items(sport_id, sport, now))

        def score(ev, window_h):
            corroboration = min(1.0, math.log1p(len(ev["outlets"])) / math.log1p(3))
            age = (now - ev["latest"]).total_seconds() / 3600
            return 0.4 * max(0.0, ev["weight"]) + 0.3 * corroboration + 0.3 * max(0.0, 1 - age / window_h)

        week = sorted((e for e in events if e["weight"] > -0.1), key=lambda e: score(e, 168), reverse=True)[:12]
        latest = sorted((e for e in events if common.within(e["latest"], 24, now) and e["weight"] > -0.2),
                        key=lambda e: e["latest"], reverse=True)[:10]

        highlights = []
        if yt and yt.available:
            ids = _channel_ids(yt, sport["channels"])
            videos = []
            for handle, cid in ids.items():
                for v in yt.uploads(cid):
                    published = common.parse_time(v["published"])
                    if published and common.within(published, 7 * 24, now) and (HIGHLIGHT.search(v["title"]) or (
                            sport.get("highlight_extra") and re.search(sport["highlight_extra"], v["title"], re.I))):
                        videos.append(v)
            views = _views(yt, [v["id"] for v in videos])
            for v in sorted(videos, key=lambda v: views.get(v["id"], 0), reverse=True)[:12]:
                card = embed(v)
                card.update(views=views.get(v["id"]), league=_league(v["title"], sport),
                            why=f"{views.get(v['id'], 0):,} views on the official {v['channel']} channel this week")
                highlights.append(card)

        out[sport_id] = {
            "name": sport["name"], "icon": sport["icon"], "leagues": list(sport["leagues"]),
            "highlights": highlights,
            "results": [_event_card(e, score(e, 168)) for e in week],
            "latest": [_event_card(e, score(e, 24)) for e in latest],
        }
        common.log(f"sports {sport_id}: {len(highlights)} highlights, {len(week)} events, {len(latest)} latest")
    return {"sports": out}
