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
from datetime import timedelta
import re
import time

import feedparser

from . import common
from .youtube import YouTube, embed

SPORTS = {
    "soccer": {"name": "Soccer", "icon": "⚽",
               "leagues": {"Premier League": "premier league, epl, arsenal, liverpool, man city, manchester city, man utd, manchester united, chelsea, tottenham, newcastle, aston villa", "La Liga": "la liga, laliga, real madrid, barcelona, atletico madrid",
                           "Bundesliga": "bundesliga, bayern munich, dortmund, leverkusen", "Champions League": "champions league, ucl",
                           "Serie A": "serie a, inter milan, juventus, napoli, ac milan"},
               "feeds": ["https://feeds.bbci.co.uk/sport/football/rss.xml", "https://www.espn.com/espn/rss/soccer/news"],
               "channels": ["@premierleague=Premier League", "@LaLiga=La Liga", "@bundesliga=Bundesliga", "@SerieA=Serie A",
                            # official US broadcaster of the Champions League and Serie A: league read from the title
                            "@cbssportsgolazo=*"]},
    "basketball": {"name": "Basketball", "icon": "🏀",
                   "leagues": {"NBA": "nba, lakers, celtics, warriors, knicks, thunder, nuggets", "EuroLeague": "euroleague", "CBA": "cba, chinese basketball association"},
                   "feeds": ["https://www.espn.com/espn/rss/nba/news", "https://feeds.bbci.co.uk/sport/basketball/rss.xml",
                             "https://sports.yahoo.com/nba/rss.xml", "https://www.cbssports.com/rss/headlines/nba/"],
                   "channels": ["@NBA=NBA", "@Euroleague=EuroLeague"]},
    "american-football": {"name": "American Football", "icon": "🏈",
                          "leagues": {"NFL": "nfl, packers, chiefs, eagles, cowboys, patriots, 49ers, super bowl", "CFL": "cfl, grey cup", "UFL": "ufl"},
                          "feeds": ["https://www.espn.com/espn/rss/nfl/news", "https://feeds.bbci.co.uk/sport/american-football/rss.xml",
                                    "https://sports.yahoo.com/nfl/rss.xml", "https://www.cbssports.com/rss/headlines/nfl/"],
                          "channels": ["@NFL=NFL", "@CFL=CFL", "@theufl=UFL"]},
    "cricket": {"name": "Cricket", "icon": "🏏",
                "leagues": {"IPL": "ipl, indian premier league", "BBL": "bbl, big bash", "PSL": "psl, pakistan super league",
                            "International": "test match, odi, t20 world cup, ashes, world cup, series"},
                "feeds": ["https://feeds.bbci.co.uk/sport/cricket/rss.xml"],
                "channels": ["@IPL=IPL", "@cricketcomau", "@ICC=International"]},
    "mma": {"name": "MMA", "icon": "🥋",
            "leagues": {"UFC": "ufc", "ONE Championship": "one championship", "PFL": "pfl, bellator"},
            "feeds": ["https://feeds.bbci.co.uk/sport/mixed-martial-arts/rss.xml", "https://www.espn.com/espn/rss/mma/news",
                      "https://sports.yahoo.com/mma/rss.xml", "https://www.cbssports.com/rss/headlines/mma/"],
            "channels": ["@UFC=UFC", "@ONEChampionship=ONE Championship", "@PFLMMA=PFL"]},
    "motorsports": {"name": "Motorsports", "icon": "🏎️",
                    "leagues": {"Formula 1": "f1, formula one, formula 1, grand prix", "NASCAR": "nascar", "IndyCar": "indycar, indy 500"},
                    "feeds": ["https://feeds.bbci.co.uk/sport/formula1/rss.xml", "https://www.espn.com/espn/rss/rpm/news",
                              "https://www.motorsport.com/rss/f1/news/"],
                    "channels": ["@Formula1=Formula 1", "@NASCAR=NASCAR", "@IndyCar=IndyCar"]},
    "baseball": {"name": "Baseball", "icon": "⚾",
                 "leagues": {"MLB": "mlb, world series, yankees, dodgers, red sox", "NPB": "npb, japan series, nippon professional baseball, hanshin tigers, yomiuri giants, softbank hawks, nippon-ham fighters", "KBO": "kbo, korean series, kia tigers, lg twins, samsung lions, doosan bears, ssg landers, kt wiz"},
                 "feeds": ["https://www.espn.com/espn/rss/mlb/news", "https://sports.yahoo.com/mlb/rss.xml", "https://www.cbssports.com/rss/headlines/mlb/"],
                 "channels": ["@MLB=MLB"]},
    "esports": {"name": "Esports", "icon": "🎮",
                "leagues": {"League of Legends": "league of legends, lol worlds, lck, lpl, lec, lta", "Counter-Strike": "counter strike, cs2, blast, iem, esl pro league, pgl, major",
                            "Valorant": "valorant, vct", "Overwatch": "overwatch, owcs"},
                "feeds": ["https://www.hltv.org/rss/news"],
                "channels": ["@lolesports=League of Legends", "@ESLCS=Counter-Strike", "@BLASTPremier=Counter-Strike", "@VALORANTEsports=Valorant", "@ow_esports=Overwatch"]},
    "hockey": {"name": "Hockey", "icon": "🏒",
               "leagues": {"NHL": "nhl, stanley cup", "KHL": "khl", "IIHF": "iihf, ice hockey world championship"},
               "feeds": ["https://www.espn.com/espn/rss/nhl/news", "https://feeds.bbci.co.uk/sport/ice-hockey/rss.xml",
                         "https://sports.yahoo.com/nhl/rss.xml", "https://www.cbssports.com/rss/headlines/nhl/"],
               "channels": ["@NHL=NHL", "@IIHFHockey=IIHF", "@KHL=KHL"]},
    "boxing": {"name": "Boxing", "icon": "🥊",
               "leagues": {"World titles": "wbc, wba, ibf, wbo, world title, undisputed, title fight"},
               "feeds": ["https://feeds.bbci.co.uk/sport/boxing/rss.xml", "https://www.espn.com/espn/rss/boxing/news",
                         "https://sports.yahoo.com/boxing/rss.xml", "https://www.cbssports.com/rss/headlines/boxing/"],
               "channels": ["@MatchroomBoxing", "@TopRankBoxing", "@DAZNBoxing", "@QueensberryPromotions"]},
    "snooker": {"name": "Snooker / Billiards", "icon": "🎱",
                "leagues": {"World Snooker Tour": "snooker, wst, open title, masters, uk championship",
                            "Pool": "mosconi, world pool, pool championship"},
                "feeds": ["https://feeds.bbci.co.uk/sport/snooker/rss.xml", "https://www.theguardian.com/sport/snooker/rss"],
                "channels": ["@WorldSnookerTour=World Snooker Tour", "@MatchroomMultiSport=*"],
                # WST titles its match videos "A vs B | Event" / "FULL MATCH", not "highlights"
                "highlight_extra": r" vs | v | full match|final|decider|147|maximum"},
}

# Per-league news (Google News search feeds), so every league has its own results and latest news
GNEWS = "https://news.google.com/rss/search?hl=en-US&gl=US&ceid=US:en&q="
LEAGUE_NEWS = {
    "soccer": {"Premier League": '"Premier League"', "La Liga": '"LaLiga" OR "La Liga"', "Bundesliga": '"Bundesliga"',
               "Champions League": '"Champions League"', "Serie A": '"Serie A"'},
    "basketball": {"NBA": '"NBA"', "EuroLeague": '"EuroLeague"', "CBA": '"Chinese Basketball Association" OR "CBA" basketball'},
    "american-football": {"NFL": '"NFL"', "CFL": '"CFL"', "UFL": '"UFL" football'},
    "cricket": {"IPL": '"IPL"', "BBL": '"Big Bash"', "PSL": '"Pakistan Super League"',
                "International": '"Test match" OR ODI OR T20I cricket'},
    "mma": {"UFC": '"UFC"', "ONE Championship": '"ONE Championship"', "PFL": '"PFL" MMA'},
    "motorsports": {"Formula 1": '"Formula 1" OR F1', "NASCAR": '"NASCAR"', "IndyCar": '"IndyCar"'},
    "baseball": {"MLB": '"MLB"', "NPB": '"Nippon Professional Baseball" OR "Japan Series" OR NPB', "KBO": '"KBO"'},
    "esports": {"League of Legends": '"League of Legends" esports OR LCK OR "LoL Worlds"',
                "Counter-Strike": '"Counter-Strike" OR CS2 esports', "Valorant": '"Valorant" esports OR VCT',
                "Overwatch": '"OWCS" OR "Overwatch Champions Series"'},
    "hockey": {"NHL": '"NHL"', "KHL": '"KHL"', "IIHF": '"IIHF"'},
    "boxing": {"World titles": '"world title" boxing'},
    "snooker": {"World Snooker Tour": '"snooker"', "Pool": '"Mosconi Cup" OR "World Pool Championship" OR "9-ball"'},
}
# extra YouTube searches for leagues whose generic "<league> highlights" search finds little
HIGHLIGHT_QUERIES = {"NPB": ["Japan Series highlights", "NPB highlights"],
                     "Pool": ["Mosconi Cup highlights", "World Pool Championship highlights", "UK Open Pool highlights"],
                     "UFL": ["UFL football highlights"], "Overwatch": ["OWCS highlights", "Overwatch Champions Series highlights"],
                     "BBL": ["Big Bash League highlights", "BBL highlights cricket"],
                     "KBO": ["KBO highlights", "KBO League highlights"],
                     "IIHF": ["IIHF World Championship highlights", "IIHF highlights"],
                     "World titles": ["world title fight highlights boxing", "boxing world championship full highlights"]}
# League news only from established sports outlets (Google News search can otherwise return small blogs)
REPUTABLE = {
    "_all": ["espn.com", "espn.co.uk", "bbc.co.uk", "bbc.com", "skysports.com", "nytimes.com", "reuters.com", "apnews.com",
             "cbssports.com", "cbsnews.com", "yahoo.com", "si.com", "nbcsports.com", "nbcnews.com", "foxsports.com",
             "theguardian.com", "independent.co.uk", "telegraph.co.uk", "usatoday.com", "sportingnews.com",
             "washingtonpost.com", "latimes.com", "theathletic.com", "eurosport.com", "dazn.com"],
    "soccer": ["goal.com", "uefa.com", "premierleague.com", "laliga.com", "bundesliga.com", "legaseriea.it",
               "football-italia.net", "marca.com", "as.com", "football-espana.net"],
    "basketball": ["nba.com", "euroleaguebasketball.net", "eurohoops.net", "scmp.com", "sixthtone.com", "basketnews.com"],
    "american-football": ["nfl.com", "cfl.ca", "theufl.com", "tsn.ca", "sportsnet.ca", "profootballtalk.nbcsports.com"],
    "cricket": ["espncricinfo.com", "cricbuzz.com", "icc-cricket.com", "cricket.com.au", "iplt20.com", "dawn.com",
                "wisden.com", "ndtv.com", "hindustantimes.com", "indiatimes.com", "thehindu.com", "indianexpress.com",
                "geo.tv", "smh.com.au", "abc.net.au"],
    "mma": ["ufc.com", "mmafighting.com", "mmajunkie.usatoday.com", "onefc.com", "pflmma.com", "sherdog.com",
            "mmamania.com", "bloodyelbow.com"],
    "motorsports": ["formula1.com", "motorsport.com", "autosport.com", "the-race.com", "nascar.com", "indycar.com",
                    "racer.com", "planetf1.com"],
    "baseball": ["mlb.com", "mlbtraderumors.com", "japantimes.co.jp", "kyodonews.net", "nikkei.com", "koreaherald.com",
                 "yna.co.kr", "joins.com", "koreatimes.co.kr", "koreajoongangdaily.joins.com"],
    "esports": ["hltv.org", "dotesports.com", "dexerto.com", "lolesports.com", "esportsinsider.com", "theverge.com",
                "esports.gg", "liquipedia.net", "valorantesports.com"],
    "hockey": ["nhl.com", "tsn.ca", "sportsnet.ca", "iihf.com", "khl.ru", "thehockeynews.com", "si.com"],
    "boxing": ["ringmagazine.com", "boxingscene.com", "boxingnews24.com", "boxingnewsonline.net", "badlefthook.com"],
    "snooker": ["wst.tv", "matchroompool.com", "azbilliards.com", "wpapool.com", "matchroom.com"],
}
MIN_SUBSCRIBERS = 100_000   # highlight videos found by search must come from a large (broadcaster/league) channel
LOOKBACK_DAYS = 60   # when a league had nothing this week, its most recent items (up to 60 days) are shown, dated

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
            if not published or not title or not common.within(published, LOOKBACK_DAYS * 24, now):
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


def _league_news(sport_id: str, league: str, query: str, now) -> list[dict]:
    """A league's own news from Google News (publisher named on each item)."""
    from urllib.parse import quote
    try:
        parsed = feedparser.parse(common.get(GNEWS + quote(f"{query} when:30d"), as_json=False))
    except common.FetchError as exc:
        common.log(f"sports league news failed ({sport_id}/{league}): {exc}")
        return []
    out = []
    for e in parsed.entries[:40]:
        published = common.parse_time(e.get("published_parsed") or e.get("published"))
        title = common.strip_html(e.get("title", ""))
        source = (e.get("source") or {}).get("title") or "News"
        host = re.sub(r"^https?://(www\.)?", "", (e.get("source") or {}).get("href") or "").split("/")[0]
        trusted = REPUTABLE["_all"] + REPUTABLE.get(sport_id, [])
        if not any(host == d or host.endswith("." + d) for d in trusted):
            continue  # only established outlets
        title = title.rsplit(" - ", 1)[0] if " - " in title else title
        if not published or not title or not common.within(published, LOOKBACK_DAYS * 24, now):
            continue
        # the headline itself must name this league (or one of its teams/events): search feeds drift
        if _league(title, SPORTS[sport_id]) != league:
            continue
        words = set(common.norm(title).split())
        weight = min(1.0, sum(1 for w in MAJOR if w in words) / 3) - 0.4 * sum(1 for w in NOISE if w in words)
        out.append({"title": title, "summary": "", "url": e.get("link"), "source": source, "published": published,
                    "league": league, "weight": weight, "image": None})
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


def _big_channels(yt: YouTube, videos: list[dict]) -> list[dict]:
    """Only videos from channels with MIN_SUBSCRIBERS+ subscribers (1 quota unit per 50 channels)."""
    cache = yt.cache.setdefault("channel_subs", {})
    todo = sorted({v["channel_id"] for v in videos if v.get("channel_id") and v["channel_id"] not in cache})
    for i in range(0, len(todo), 50):
        try:
            data = yt.get("channels", {"part": "statistics", "id": ",".join(todo[i:i + 50])})
            yt.units += 1
        except common.FetchError as exc:
            common.log(f"youtube channels: {exc}")
            continue
        for item in data.get("items", []):
            cache[item["id"]] = int(item.get("statistics", {}).get("subscriberCount") or 0)
    return [v for v in videos if cache.get(v.get("channel_id"), 0) >= MIN_SUBSCRIBERS]


def _channel_ids(yt: YouTube, handles: list[str]) -> dict[str, str]:
    """@handle -> channel id (1 unit each, cached for good)."""
    cache = yt.cache.setdefault("handles", {})
    out = {}
    for h in handles:
        if h not in cache:
            try:
                data = yt.get("channels", {"part": "id", "forHandle": h})
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
            data = yt.get("videos", {"part": "statistics", "id": ",".join(ids[i:i + 50])})
            yt.units += 1
        except common.FetchError as exc:
            common.log(f"youtube stats: {exc}")
            continue
        for item in data.get("items", []):
            views[item["id"]] = int(item.get("statistics", {}).get("viewCount", 0))
    return views


# promo and media-day uploads that use the word "highlights" but show no action
NOT_HIGHLIGHT = re.compile(r"weigh[- ]?in|press conference|presser|face[- ]?off|media day|open workout|interview|countdown|"
                           r"preview|trailer|promo|ticket|watch the full fight now", re.I)
VERSUS = re.compile(r"(.+?)\s+(?:vs\.?|v\.?|versus)\s+(.+)", re.I)


def _league_from_news(title: str, events: list[dict]) -> str | None:
    """A fight video whose title doesn't name the belt ("Smith vs Puello Highlights"): the league that
    trusted outlets' headlines give for those same two fighters, if any."""
    m = VERSUS.search(title.split("|")[0])
    if not m:
        return None
    sides = []
    for side in m.groups():   # each fighter's names, e.g. {"canelo", "alvarez"}
        words = {w for w in common.norm(side).split()[-3:] if len(w) > 2} - {"highlights", "full", "fight", "the", "and"}
        if not words:
            return None
        sides.append(words)
    for ev in events:
        for a in ev["articles"]:
            t = set(common.norm(a["title"]).split())
            if a["league"] and all(side & t for side in sides):   # both fighters named in one headline
                return a["league"]
    return None


HIGHLIGHT = re.compile(r"highlight|best moments|top \d+|top plays|goals|knockout|every |full fight|extended|recap|finish|resumen|обзор|лучшие моменты", re.I)
SEASON = re.compile(r"\b(?:19|20)(\d\d)/(\d\d)\b")   # "2019/20": an archive replay, unless it's this season


def _archive(title: str, now) -> bool:
    m = SEASON.search(title)
    if not m:
        return False
    start = int(m.group(1))
    return start not in (now.year % 100, (now.year - 1) % 100)


def build(ai=None, yt: YouTube | None = None) -> dict:
    now = common.now_utc()
    out = {}
    for sport_id, sport in SPORTS.items():
        items = _items(sport_id, sport, now)
        for league, query in LEAGUE_NEWS.get(sport_id, {}).items():
            items += _league_news(sport_id, league, query, now)
            time.sleep(0.5)
        events = _group(items)

        def score(ev, window_h):
            corroboration = min(1.0, math.log1p(len(ev["outlets"])) / math.log1p(3))
            age = (now - ev["latest"]).total_seconds() / 3600
            return 0.4 * max(0.0, ev["weight"]) + 0.3 * corroboration + 0.3 * max(0.0, 1 - age / window_h)

        this_week = [e for e in events if common.within(e["latest"], 7 * 24, now)]
        week = sorted((e for e in this_week if e["weight"] > -0.1), key=lambda e: score(e, 168), reverse=True)[:12]
        latest = sorted((e for e in events if common.within(e["latest"], 24, now) and e["weight"] > -0.2),
                        key=lambda e: e["latest"], reverse=True)[:10]

        videos, views = [], {}
        if yt and yt.available:
            # "@handle=League": everything on that channel is that league; "=*": a broadcaster covering
            # several competitions, kept only when the title names one of ours; bare: league from title
            channel_league = {e.split("=")[0]: (e.split("=", 1)[1] if "=" in e else None) for e in sport["channels"]}
            ids = _channel_ids(yt, list(channel_league))
            for handle, cid in ids.items():
                for v in yt.uploads(cid):
                    published = common.parse_time(v["published"])
                    if not (published and common.within(published, LOOKBACK_DAYS * 24, now)) or _archive(v["title"], now):
                        continue
                    if not (HIGHLIGHT.search(v["title"]) or (
                            sport.get("highlight_extra") and re.search(sport["highlight_extra"], v["title"], re.I))):
                        continue
                    if NOT_HIGHLIGHT.search(v["title"]):
                        continue
                    fixed = channel_league[handle]
                    league = fixed if fixed and fixed != "*" else (_league(v["title"], sport) or _league_from_news(v["title"], events))
                    if fixed == "*" and not league:
                        continue
                    videos.append({**v, "league": league, "when": published})
            views = _views(yt, [v["id"] for v in videos])

        def hl_card(v, why=None):
            card = embed(v)
            card.update(views=views.get(v["id"]), league=v["league"],
                        why=why or f"{views.get(v['id'], 0):,} views on the official {v['channel']} channel")
            return card

        recent_videos = [v for v in videos if common.within(v["when"], 7 * 24, now)]
        by_views = sorted(recent_videos, key=lambda v: views.get(v["id"], 0), reverse=True)
        highlights, per = [], {}
        for v in by_views:  # the 12 most watched per league this week
            if per.get(v["league"], 0) < 12:
                per[v["league"]] = per.get(v["league"], 0) + 1
                highlights.append(hl_card(v, f"{views.get(v['id'], 0):,} views on the official {v['channel']} channel this week"))

        # Every league always has all three views: this week's items, or else its most recent ones (dated)
        by_league = {}
        for league in sport["leagues"]:
            evs = [e for e in events if e["league"] == league]
            res = sorted((e for e in evs if common.within(e["latest"], 7 * 24, now) and e["weight"] > -0.1),
                         key=lambda e: score(e, 168), reverse=True)[:8]
            res_old = not res
            if res_old:
                res = sorted((e for e in evs if e["weight"] > -0.1), key=lambda e: e["latest"], reverse=True)[:5]
            lat = sorted((e for e in evs if common.within(e["latest"], 24, now)), key=lambda e: e["latest"], reverse=True)[:8]
            lat_old = not lat
            if lat_old:
                lat = sorted(evs, key=lambda e: e["latest"], reverse=True)[:5]
            hl = [c for c in highlights if c["league"] == league]
            hl_old = False
            if not hl:
                older = sorted((v for v in videos if v["league"] == league), key=lambda v: v["when"], reverse=True)[:6]
                if older:
                    hl, hl_old = [hl_card(v) for v in older], "older"
                elif yt and yt.available:
                    found = []
                    for q in HIGHLIGHT_QUERIES.get(league, []) + [f"{LEAGUE_NEWS.get(sport_id, {}).get(league, league)} highlights"]:
                        # search results are dated on the card; up to a year back for leagues between seasons;
                        # each search is reused for 7 days (100 quota units each)
                        found = _big_channels(yt, [v for v in yt.search(q, after=now - timedelta(days=365), max_age_days=7,
                                                                        max_results=10)
                                                   if HIGHLIGHT.search(v["title"])])
                        if found:
                            break
                    more = _views(yt, [v["id"] for v in found])
                    views.update(more)
                    hl = [hl_card({**v, "league": league}, f"{more.get(v['id'], 0):,} views; found by YouTube search, "
                                                           f"uploaded by {v.get('channel')}") for v in found[:6]]
                    hl_old = "search" if hl else False
            by_league[league] = {
                "highlights": hl, "results": [_event_card(e, score(e, 168)) for e in res],
                "latest": [_event_card(e, score(e, 24)) for e in lat],
                "older": {"highlights": hl_old, "results": res_old and bool(res), "latest": lat_old and bool(lat)},
            }

        out[sport_id] = {
            "name": sport["name"], "icon": sport["icon"], "leagues": list(sport["leagues"]),
            "highlights": highlights,
            "results": [_event_card(e, score(e, 168)) for e in week],
            "latest": [_event_card(e, score(e, 24)) for e in latest],
            "by_league": by_league,
        }
        empty = [f"{lg}/{k}" for lg, b in by_league.items() for k in ("highlights", "results", "latest") if not b[k]]
        common.log(f"sports {sport_id}: {len(highlights)} highlights, {len(week)} events, {len(latest)} latest"
                   + (f"; still empty: {', '.join(empty)}" if empty else ""))
    return {"sports": out}
