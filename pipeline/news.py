"""Optimal News: Top 12 on every tab -- seven topics (Business, Tech, Entertainment, Celebrities, Science, Health, Climate & Weather), World and seven regions; Today (24 h), This Week (7 days) and
This Month (30 days). The month view also draws on every earlier daily ranking (saved in state),
so stories that dominated two weeks ago still count after their articles drop out of the feeds.

Sources (all free, publisher feeds): BBC (world + regional), Al Jazeera, DW,
France 24, and Reuters / AP via Google News search feeds. Articles about the same
event are grouped across outlets ("events"), then ranked by:

  corroboration  0.35  distinct independent outlets reporting it (log scale)
  significance   0.30  AI class from the article text (major/notable/minor), or
                       a keyword severity estimate when AI is unavailable
  recency        0.20  newer within the window scores higher
  source tier    0.15  wire services / public broadcasters first

so a sensational one-outlet story can't outrank a widely confirmed major event.
AI (Claude Haiku) only summarizes the given article snippets and classifies
significance; every fact shown comes from the linked articles.
"""
from __future__ import annotations

import math
import re
from datetime import timedelta

import feedparser

from . import common, geo, news_images
from .youtube import YouTube, best_match, embed

FEEDS = [
    # (outlet, url, region or None for "decide from the text")
    ("BBC News", "https://feeds.bbci.co.uk/news/world/rss.xml", None),
    ("BBC News", "https://feeds.bbci.co.uk/news/world/africa/rss.xml", "africa"),
    ("BBC News", "https://feeds.bbci.co.uk/news/world/asia/rss.xml", "asia"),
    ("BBC News", "https://feeds.bbci.co.uk/news/world/europe/rss.xml", "europe"),
    ("BBC News", "https://feeds.bbci.co.uk/news/world/latin_america/rss.xml", "south-america"),
    ("BBC News", "https://feeds.bbci.co.uk/news/world/middle_east/rss.xml", "middle-east"),
    ("BBC News", "https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml", "north-america"),
    ("BBC News", "https://feeds.bbci.co.uk/news/world/australia/rss.xml", "oceania"),
    ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml", None),
    ("DW", "https://rss.dw.com/rdf/rss-en-world", None),
    ("France 24", "https://www.france24.com/en/rss", None),
    # Oceania's own public broadcasters + Guardian Australia (the global outlets cover the region thinly)
    ("ABC News (Australia)", "https://www.abc.net.au/news/feed/51120/rss.xml", "oceania"),
    ("RNZ", "https://www.rnz.co.nz/rss/national.xml", "oceania"),
    ("RNZ", "https://www.rnz.co.nz/rss/pacific.xml", "oceania"),
    ("The Guardian", "https://www.theguardian.com/australia-news/rss", "oceania"),
]
# Topic tabs (owner: Business 31 Oct 2026; Tech, Entertainment, Science, Health 3 Nov 2026 -- the
# Google News topic sections). Same ranking rules as the regions. Each topic gets its own desk feeds
# from the trusted outlets plus a Google News search limited to those outlets (Google News' own
# topic sections are mostly other sites, so they are not used directly).
TOPICS = {
    "business": {
        "name": "Business",
        "feeds": [("BBC News", "https://feeds.bbci.co.uk/news/business/rss.xml"),
                  ("DW", "https://rss.dw.com/rdf/rss-en-bus"),
                  ("France 24", "https://www.france24.com/en/business-tech/rss"),
                  ("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html")],
        "query": "(business OR markets OR economy OR earnings OR inflation)",
        "words": "economy economic markets market stocks shares earnings profit revenue inflation interest rates "
                 "central bank fed ecb tariffs trade merger acquisition takeover deal bankruptcy layoffs jobs gdp "
                 "recession oil prices investors ipo company companies ceo bank banks currency dollar bond bonds",
        "ai": "the story is mainly about business, the economy, markets, trade, companies or finance (wars, arms "
              "sales, diplomacy and politics are false unless the story is about their economic or market effect)",
    },
    "tech": {
        "name": "Tech",
        "feeds": [("BBC News", "https://feeds.bbci.co.uk/news/technology/rss.xml"),
                  ("CNBC", "https://www.cnbc.com/id/19854910/device/rss/rss.html"),
                  ("The Guardian", "https://www.theguardian.com/uk/technology/rss")],
        "query": "(technology OR AI OR software OR smartphone OR cybersecurity OR chips OR apple OR google OR microsoft)",
        "words": "technology tech ai artificial intelligence software app apps smartphone smartphones iphone android "
                 "apple google microsoft meta amazon openai chatgpt chip chips semiconductor semiconductors nvidia cyber "
                 "cyberattack hack hacked hackers data internet startup robot robots robotics chatbot computing cloud "
                 "quantum spacex social media platform platforms online gadget",
        "ai": "the story is mainly about technology in any form (tech companies, products, AI, software, the "
              "internet, cybersecurity, platforms and more)",
    },
    "entertainment": {
        "name": "Entertainment",
        "feeds": [("BBC News", "https://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml"),
                  ("DW", "https://rss.dw.com/rdf/rss-en-cul"),
                  ("The Guardian", "https://www.theguardian.com/culture/rss")],
        "query": "(film OR movie OR music OR celebrity OR television OR streaming OR \"box office\")",
        "words": "film films movie movies actor actress director music singer album song songs band concert tour "
                 "celebrity hollywood oscar oscars grammy grammys emmy emmys television tv series netflix disney "
                 "streaming box office festival award awards star rapper comedian theatre broadway musician",
        "ai": "the story is mainly about entertainment or culture in any form (film, TV, music, celebrities, "
              "streaming, the arts, awards and more)",
    },
    # Owner, 4 Nov 2026: right after Entertainment. The trade press and People carry this beat; the
    # wires barely do, so those outlets count ONLY here (see CELEB_OUTLETS).
    "celebrities": {
        "name": "Celebrities",
        "feeds": [("Variety", "https://variety.com/feed/"),
                  ("The Hollywood Reporter", "https://www.hollywoodreporter.com/feed/"),
                  ("Deadline", "https://deadline.com/feed/"),
                  ("Billboard", "https://www.billboard.com/feed/")],
        "sites": "(site:people.com OR site:variety.com OR site:hollywoodreporter.com OR site:deadline.com "
                 "OR site:billboard.com OR site:apnews.com OR site:bbc.com OR site:theguardian.com)",
        "query": "(celebrity OR actor OR actress OR singer OR rapper OR \"reality star\" OR divorce OR engaged OR dies)",
        "words": "celebrity celebrities actor actress singer rapper comedian supermodel popstar hollywood divorce "
                 "engaged engagement pregnant dating rehab kardashian paparazzi",
        "ai": "the story is mainly about a famous person from entertainment or popular culture (actors, singers, "
              "rappers, TV personalities, models, influencers): their life, relationships, health, death, legal "
              "trouble, public statements or career moves (film or TV releases, box office, deals, casting notices and "
              "industry business are false unless the person is the story; politicians, business leaders and athletes "
              "are false)",
    },
    "science": {
        "name": "Science",
        "feeds": [("BBC News", "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml"),
                  ("The Guardian", "https://www.theguardian.com/science/rss"),
                  ("DW", "https://rss.dw.com/rdf/rss-en-sci")],
        "query": "(science OR scientists OR space OR NASA OR physics OR researchers OR discovery)",
        "words": "science scientists scientific research researchers study studies space nasa astronaut astronauts "
                 "rocket moon mars planet planets telescope species fossil fossils discovery discoveries physics biology "
                 "experiment universe galaxy asteroid satellite archaeologists archaeology dinosaur breakthrough genome "
                 "particle quantum evolution",
        "ai": "the main subject is science: discoveries, scientific research, space, physics, biology or major "
              "breakthroughs (weather, disasters, climate policy and environment news are false; climate stories only "
              "when they report a significant scientific discovery)",
    },
    "health": {
        "name": "Health",
        "feeds": [("BBC News", "https://feeds.bbci.co.uk/news/health/rss.xml"),
                  ("The Guardian", "https://www.theguardian.com/society/health/rss")],
        "query": "(health OR disease OR vaccine OR hospital OR medical OR WHO OR outbreak)",
        "words": "health medical medicine doctors doctor hospital hospitals patients disease diseases virus vaccine "
                 "vaccines cancer drug drugs treatment nhs outbreak covid flu mental obesity diabetes surgery clinical "
                 "trial heart dementia nurses healthcare infection infections",
        "ai": "the main subject is strictly medicine, diseases, treatments, medical research or public health (politics, "
              "taxes, crime, violence and disasters are false unless the story is mainly about a disease or medical care)",
    },
}
TOPICS["climate"] = {
    "name": "Climate & Weather",   # owner, 3 Nov 2026
    "feeds": [("BBC News", "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml"),
              ("The Guardian", "https://www.theguardian.com/environment/rss")],
    "query": "(climate OR weather OR hurricane OR typhoon OR cyclone OR flood OR floods OR earthquake OR wildfire "
             "OR drought OR heatwave OR storm)",
    "words": "climate weather hurricane hurricanes typhoon cyclone flood floods flooding earthquake quake tsunami "
             "wildfire wildfires drought heatwave heat storm storms tornado rain rainfall snow blizzard landslide "
             "volcano eruption emissions carbon warming cop30 cop environment environmental pollution deforestation "
             "glacier glaciers sea level",
    "ai": "the main subject is weather or climate: major weather events, hurricanes, floods, earthquakes and other "
          "natural disasters, climate change, environmental developments or climate policy",
}
# Celebrity outlets count only in the Celebrities tab: every other tab ranks as if they did not exist.
CELEB_TAB = "celebrities"
CELEB_OUTLETS = {"Variety", "The Hollywood Reporter", "Deadline", "Billboard", "People"}
TOPIC_WORDS = {t: set(v["words"].split()) for t, v in TOPICS.items()}
TOPIC_SITES = "(site:reuters.com OR site:apnews.com OR site:bbc.com OR site:theguardian.com OR site:cnbc.com)"
WAR_WORDS = set("war missile missiles strikes troops drone drones killed attack military army".split())
# Tab order (owner, 3 Nov 2026): the five topics, then World, then the regions.
TABS = list(TOPICS) + ["world"] + [r for r in geo.REGIONS if r != "world"]
TAB_NAMES = {**geo.REGION_NAMES, **{t: v["name"] for t, v in TOPICS.items()}}
# Reuters and AP through Google News search feeds (title links go via Google News).
GNEWS = "https://news.google.com/rss/search?hl=en-US&gl=US&ceid=US:en&q="
WIRE_QUERIES = {
    "world": "world", "asia": "Asia", "africa": "Africa", "north-america": "(United States OR Canada OR Mexico)",
    "south-america": "(Brazil OR Argentina OR Colombia OR Venezuela OR Chile OR Peru)", "middle-east": "Middle East",
    "europe": "Europe", "oceania": "(Australia OR New Zealand OR Pacific)",
}
WIRES = {"reuters.com": "Reuters", "apnews.com": "AP", "bbc.com": "BBC News", "bbc.co.uk": "BBC News",
         "theguardian.com": "The Guardian", "cnbc.com": "CNBC", "people.com": "People", "variety.com": "Variety",
         "hollywoodreporter.com": "The Hollywood Reporter", "deadline.com": "Deadline", "billboard.com": "Billboard"}
TIER = {"Reuters": 1.0, "AP": 1.0, "BBC News": 0.95, "Al Jazeera": 0.85, "DW": 0.85, "France 24": 0.85, "CNBC": 0.85,
        "ABC News (Australia)": 0.9, "RNZ": 0.9, "The Guardian": 0.85, "Variety": 0.8, "The Hollywood Reporter": 0.8,
        "Deadline": 0.8, "Billboard": 0.8, "People": 0.75}

# Official news channels for report videos (matched from their latest uploads).
NEWS_CHANNELS = {
    "BBC News": "UC16niRr50-MSBwiO3YDb3RA",
    "Al Jazeera English": "UCNye-wNBqNL5ZzHSJj3l8Bg",
    "Reuters": "UChqUTb7kYRX8-EiaN3XFrSQ",
    "DW News": "UCknLrEdhRCp1aegoMqRaCZg",
    "FRANCE 24 English": "UCQfwfsi5VrQ8yKZ-UWmAEFg",
}

SEVERE = ("killed dead deaths death toll war invasion attack strike strikes missile bombing earthquake tsunami hurricane "
          "cyclone typhoon flood floods wildfire famine epidemic outbreak pandemic coup ceasefire election elections "
          "president prime minister resigns resignation sanctions nuclear genocide massacre hostages crash collapse "
          "emergency treaty summit default recession tariffs bankruptcy layoffs crisis plunge plunges "
          "soar soars record merger acquisition rate cut rate hike inflation").split()
SOFT = ("celebrity actor actress singer fashion royal wedding viral quiz recipe review horoscope football match "
        "transfer").split()


# Opinion columns, reviews, podcasts and quizzes are not news events (topic desks carry many).
NOT_NEWS_URL = ("/commentisfree/", "/podcasts/", "/podcast/", "/news/av/", "/sounds/")
NOT_NEWS_TITLE = re.compile(r"(\u2013|-|:)\s*(podcast|review|quiz)\b|\breview\s*(\u2013|-|:)|\b(podcast|quiz of the week|a job that changed me|"
                            r"my week|what i'm really thinking|opinion)\b", re.I)


def _not_news(title: str, url: str) -> bool:
    return any(p in url for p in NOT_NEWS_URL) or bool(NOT_NEWS_TITLE.search(title))


def _collect() -> list[dict]:
    items = []
    # Search feeds are not a reliable region label (an "Australia" search also returns
    # other stories), so only BBC's regional feeds count as a region source.
    feeds = [(o, u, r, None) for o, u, r in FEEDS]
    feeds += [(None, GNEWS + f"(site:reuters.com OR site:apnews.com) {q} when:7d", None, None)
              for q in WIRE_QUERIES.values()]
    # the three weeks before that, one search per week, so This Month sees the whole month
    today = common.now_utc().date()
    for k in range(1, 4):
        after, before = today - timedelta(days=7 * k + 7), today - timedelta(days=7 * k)
        feeds += [(None, GNEWS + f"(site:reuters.com OR site:apnews.com) {q} after:{after} before:{before}", None, None)
                  for q in WIRE_QUERIES.values()]
    for topic, t in TOPICS.items():
        feeds += [(o, u, None, topic) for o, u in t["feeds"]]
        feeds.append((None, GNEWS + f"{t.get('sites', TOPIC_SITES)} {t['query']} when:7d", None, topic))
        for k in range(1, 4):
            after, before = today - timedelta(days=7 * k + 7), today - timedelta(days=7 * k)
            feeds.append((None, GNEWS + f"{t.get('sites', TOPIC_SITES)} {t['query']} after:{after} before:{before}", None, topic))
    for outlet, url, region, topic in feeds:
        try:
            parsed = feedparser.parse(common.get(url, as_json=False))
        except common.FetchError as exc:
            common.log(f"news feed failed ({outlet or 'wires'} {region or ''}): {exc}")
            continue
        for e in parsed.entries:
            title = common.strip_html(e.get("title", ""))
            source = outlet
            link = e.get("link")
            if outlet is None:  # Google News: "Title - Reuters"
                src = (e.get("source") or {}).get("href", "")
                source = next((name for dom, name in WIRES.items() if dom in src), None)
                if not source:
                    continue
                title = title.rsplit(" - ", 1)[0]
            published = common.parse_time(e.get("published_parsed") or e.get("updated_parsed") or e.get("published"))
            if not title or not link or not published:
                continue
            if _not_news(title, link):
                continue
            image = None
            for key in ("media_thumbnail", "media_content"):
                media = e.get(key) or []
                if media and media[0].get("url"):
                    image = media[0]["url"]
                    break
            items.append({
                "title": title,
                # Google News snippets only repeat the headline
                "summary": "" if outlet is None else common.strip_html(e.get("summary", ""))[:500],
                "url": link,
                "source": source,
                "published": published,
                "feed_region": region,
                "topic_feed": topic,
                "image": image,
            })
    common.log(f"news: {len(items)} articles from {len({i['source'] for i in items})} outlets")
    return items


def _cluster(items: list[dict]) -> list[dict]:
    """Groups articles about the same event (title similarity, within 4 days)."""
    events: list[dict] = []
    for item in sorted(items, key=lambda i: i["published"]):
        best, best_sim = None, 0.0
        for ev in events:
            if abs(item["published"] - ev["latest"]) > timedelta(days=4):
                continue
            sim = max(max(common.similarity(item["title"], a["title"]),
                          # headline + summary together: same event under different headlines
                          common.similarity(item["title"] + " " + item["summary"], a["title"] + " " + a["summary"]) * 1.35)
                      for a in ev["articles"][-6:])
            if sim > best_sim:
                best, best_sim = ev, sim
        if best is not None and best_sim >= 0.34:
            best["articles"].append(item)
            best["latest"] = max(best["latest"], item["published"])
        else:
            events.append({"articles": [item], "first": item["published"], "latest": item["published"]})
    for ev in events:
        arts = ev["articles"]
        # one article per outlet counts toward corroboration (dedupe syndication)
        ev["outlets"] = sorted({a["source"] for a in arts})
        ev["celeb_only"] = set(ev["outlets"]) <= CELEB_OUTLETS
        text = " ".join(a["title"] + " " + a["summary"] for a in arts)
        labelled = sorted({a["feed_region"] for a in arts if a["feed_region"]})
        lead_title = max(arts, key=lambda a: (TIER.get(a["source"], 0.5), len(a["summary"])))["title"]
        guessed = geo.guess_regions(lead_title)  # the lead headline names the place most reliably
        # BBC regional feeds also carry other regions' top stories: the headline's place wins.
        ev["regions"] = guessed[:1] or labelled[:1]
        words = set(common.norm(text).split())
        # Topics: carried by that topic's desk, or clearly about the topic (desks also carry
        # general headlines, so the text must agree).
        war = len(words & WAR_WORDS)
        ev["topics"] = {t: len(words & TOPIC_WORDS[t]) - war >= (2 if any(a["topic_feed"] == t for a in arts) else 4)
                        for t in TOPICS}
        severe = sum(1 for w in SEVERE if w in words)
        soft = sum(1 for w in SOFT if w in words)
        ev["keyword_severity"] = max(0.0, min(1.0, severe / 4 - soft / 4))
        lead = max(arts, key=lambda a: (TIER.get(a["source"], 0.5), len(a["summary"])))
        ev["lead"] = lead
        ev["id"] = common.fingerprint(common.norm(lead["title"])[:80], lead["published"].strftime("%Y%m%d"))
    return events


AI_SYSTEM = (
    "You assist a news-ranking pipeline. For each event you get headlines and short snippets from named outlets. "
    "Use ONLY that text: never add facts, numbers, names, quotes or sources that are not in it. "
    "For each event return: a neutral 1-2 sentence factual summary; the main country (or 'International'); "
    "the region (one of world, asia, africa, north-america, south-america, middle-east, europe, oceania -- 'world' "
    "only for genuinely global events); significance: 'major' (large loss of life, war, disaster, national leadership "
    "change, global economic or diplomatic impact), 'notable' (important national/regional development), or 'minor' "
    "(local, human-interest, entertainment, sport); is_update: true if it is a new development of an ongoing story; "
    "disputed: a short note if the snippets present claims as unverified or contested, else empty string; story: a "
    "3-6 word lowercase label of the underlying story, IDENTICAL for events in this batch that are the same story "
    "(e.g. 'spain train crash minister'); and one true/false per news category -- judge from what the story is "
    "mainly about, not from keywords; a story can belong to several categories or none; true only if " +
    "; ".join(f"{t}: {v['ai']}" for t, v in TOPICS.items()) + ". For every category you mark true, <category>_why: "
    "if a reader could wonder why the story is in that category (the link is indirect, e.g. a company's earnings "
    "in Health because a food outbreak hurt them), one short plain sentence under 20 words saying why it belongs; "
    "else empty string. Keep each summary under 45 words."
)
AI_SCHEMA = {
    "type": "object",
    "properties": {"events": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "id": {"type": "string"}, "summary": {"type": "string"}, "country": {"type": "string"},
            "region": {"type": "string", "enum": geo.REGIONS},
            "significance": {"type": "string", "enum": ["major", "notable", "minor"]},
            "is_update": {"type": "boolean"}, "disputed": {"type": "string"},
            "story": {"type": "string"}, **{t: {"type": "boolean"} for t in TOPICS},
            **{t + "_why": {"type": "string"} for t in TOPICS},
        },
        "required": ["id", "summary", "country", "region", "significance", "is_update", "disputed", "story", *TOPICS,
                     *(t + "_why" for t in TOPICS)],
        "additionalProperties": False,
    }}},
    "required": ["events"], "additionalProperties": False,
}
SIGNIFICANCE = {"major": 1.0, "notable": 0.6, "minor": 0.15}


def _ai_enrich(events: list[dict], ai) -> None:
    """Summaries + classification for candidate events, 10 per call, cached by event content."""
    todo = [ev for ev in events if ev.get("candidate") and not ev.get("ai") and not ev.get("ai_tried")]
    for ev in todo:
        ev["ai_tried"] = True
    for i in range(0, len(todo), 10):
        batch = todo[i : i + 10]
        lines = []
        for ev in batch:
            arts = sorted(ev["articles"], key=lambda a: -TIER.get(a["source"], 0.5))[:4]
            snippets = " || ".join(f"[{a['source']}] {a['title']}: {a['summary'][:260]}" for a in arts)
            lines.append(f"id={ev['id']} :: {snippets}")
        key = "news-topics4:" + common.fingerprint(*lines)  # new key: rows carry every topic + <topic>_why notes
        result = ai.json_call(key, AI_SYSTEM, "\n".join(lines), AI_SCHEMA, max_tokens=8000) if ai else None
        for row in (result or {}).get("events", []):
            ev = next((e for e in batch if e["id"] == row["id"]), None)
            if ev:
                ev["ai"] = row


ARCHIVE = common.STATE / "news_archive.json"


def _parts(ev: dict) -> dict:
    """What a score is made of, kept so archived stories can be re-scored later."""
    return {"outlets": len(set(ev["outlets"]) - CELEB_OUTLETS) or len(ev["outlets"]), "outlets_all": len(ev["outlets"]),
            "sig": SIGNIFICANCE[ev["ai"]["significance"]] if ev.get("ai") else ev["keyword_severity"],
            "latest": common.iso(ev["latest"]), "tier": max(TIER.get(s, 0.5) for s in ev["outlets"]),
            "days": len({a["published"].date() for a in ev["articles"]})}


def _score_parts(parts: dict, now, window_hours: float, tab: str | None = None) -> float:
    outlets = parts.get("outlets_all", parts["outlets"]) if tab == CELEB_TAB else parts["outlets"]
    corroboration = min(1.0, math.log1p(outlets) / math.log1p(6))
    age_h = (now - common.parse_time(parts["latest"])).total_seconds() / 3600
    recency = max(0.0, 1 - age_h / window_hours)
    # how many days the story stayed in the news: a week or month list favours lasting stories
    persistence = min(1.0, (parts.get("days", 1) - 1) / (3 if window_hours <= 168 else 7))
    if window_hours > 24 * 14:
        return round(0.30 * corroboration + 0.30 * parts["sig"] + 0.20 * persistence + 0.05 * recency
                     + 0.15 * parts["tier"], 4)
    if window_hours > 24:
        return round(0.30 * corroboration + 0.25 * parts["sig"] + 0.15 * persistence + 0.15 * recency
                     + 0.15 * parts["tier"], 4)
    return round(0.35 * corroboration + 0.30 * parts["sig"] + 0.20 * recency + 0.15 * parts["tier"], 4)


def _score(ev: dict, now, window_hours: float, tab: str | None = None) -> float:
    return _score_parts(_parts(ev), now, window_hours, tab)


def _why(ev: dict) -> str:
    parts = [f"Reported by {len(ev['outlets'])} outlet{'s' if len(ev['outlets']) != 1 else ''} ({', '.join(ev['outlets'])})"]
    if ev.get("ai"):
        parts.append(f"rated {ev['ai']['significance']} significance")
        if ev["ai"]["is_update"]:
            parts.append("new development in an ongoing story")
    return "; ".join(parts) + "."


def _distinct_first(articles: list[dict]) -> list[dict]:
    """One article per outlet first (best outlets first), then the rest."""
    ranked = sorted(articles, key=lambda a: (-TIER.get(a["source"], 0.5), a["published"]))
    first, rest, seen = [], [], set()
    for a in ranked:
        (rest if a["source"] in seen else first).append(a)
        seen.add(a["source"])
    return first + rest


def _maybe_topic(ev: dict, topic: str) -> bool:
    """Wide net for the AI: carried by the topic's feeds or naming one topic word (short summaries
    often name only one, e.g. 'Flash flooding triggers evacuations'). The AI then decides."""
    if ev["topics"][topic] or any(a["topic_feed"] == topic for a in ev["articles"]):
        return True
    words = set(common.norm(" ".join(a["title"] + " " + a["summary"] for a in ev["articles"])).split())
    return bool(words & TOPIC_WORDS[topic])


def build(ai=None, yt: YouTube | None = None) -> dict:
    now = common.now_utc()
    items = [i for i in _collect() if common.within(i["published"], 30 * 24, now)]
    if not items:
        raise RuntimeError("no news articles fetched")
    events = _cluster(items)

    # Candidates for AI: the best keyword-scored events per region and window.
    for window in (24, 168, 720):
        for tab in TABS:
            pool = [ev for ev in events if common.within(ev["latest"], window, now)
                    and (tab == CELEB_TAB or not ev["celeb_only"])
                    and (tab == "world" or (_maybe_topic(ev, tab) if tab in TOPICS else tab in ev["regions"]))]
            pool.sort(key=lambda e: _score(e, now, window, tab), reverse=True)
            # every tab shows 12 (owner, 4 Nov 2026). The biggest stories overall also go to the AI, which
            # decides each story's categories itself -- keywords only widen the net, they never decide.
            for ev in pool[:40 if tab == "world" else 60 if tab == CELEB_TAB else 30 if tab in TOPICS else 15]:   # trade feeds are mostly industry news the AI rejects
                ev["candidate"] = True
    _ai_enrich(events, ai)

    videos = []
    if yt and yt.available:
        for name, cid in NEWS_CHANNELS.items():
            videos += yt.uploads(cid)

    def with_note(c: dict, note: str) -> dict:
        """Owner, 3 Nov 2026: when a story's place in a topic tab isn't obvious, the Why line says why."""
        return {**c, "why": f"{c['why']} {note.rstrip('.')}."} if note else c

    def note_for(ev: dict, tab: str) -> str:
        return ((ev.get("ai") or {}).get(tab + "_why") or "").strip() if tab in TOPICS else ""

    def card(ev: dict, window: float, tab: str | None = None) -> dict:
        lead = ev["lead"]
        ai_row = ev.get("ai") or {}
        video = best_match(lead["title"], ev["latest"], videos, min_similarity=0.32, max_days=3) if videos else None
        return {
            "id": ev["id"],
            "headline": lead["title"],
            "summary": ai_row.get("summary") or lead["summary"] or next((a["summary"] for a in ev["articles"] if a["summary"]), ""),
            "summary_by": "ai" if ai_row.get("summary") else "source",
            "country": ai_row.get("country"),
            "region": ai_row.get("region") or (ev["regions"][0] if ev["regions"] else "world"),
            "published": common.iso(ev["first"]),
            "updated": common.iso(ev["latest"]),
            "significance": ai_row.get("significance"),
            "disputed": ai_row.get("disputed") or "",
            "score": _score(ev, now, window, tab),
            "why": _why(ev),
            "image": next((a["image"] for a in ev["articles"] if a["image"]), None),
            "sources": [{"name": a["source"], "title": a["title"], "url": a["url"], "published": common.iso(a["published"])}
                        for a in _distinct_first(ev["articles"])[:6]],
            "video": embed(video),
        }

    # Distinctive words (rare across today's headlines) identify one story told in
    # several headlines -- each Top 3 shows a story once.
    df: dict[str, int] = {}
    for ev in events:
        for w in {w for a in ev["articles"] for w in common.tokens(a["title"])}:
            df[w] = df.get(w, 0) + 1
    rare_cut = max(3, len(events) // 25)

    def signature(ev: dict) -> set[str]:
        return {w for a in ev["articles"] for w in common.tokens(a["title"]) if df.get(w, 0) <= rare_cut}

    def gist(ev: dict) -> str:
        return ev["lead"]["title"] + " " + ((ev.get("ai") or {}).get("summary") or ev["lead"]["summary"] or "")

    def same_story(a: dict, b: dict) -> bool:
        if a.get("ai") and b.get("ai") and a["ai"].get("story") and a["ai"]["story"] == b["ai"].get("story"):
            return True
        # the same event under different headlines ("ECB chief steps down" / "European Central Bank head resigns")
        if common.similarity(gist(a), gist(b)) >= 0.28:
            return True
        return len(signature(a) & signature(b)) >= 2

    def top3(pool: list[dict], n: int = 3) -> list[dict]:
        picked: list[dict] = []
        for ev in pool:
            if not any(same_story(ev, p) for p in picked):
                picked.append(ev)
            if len(picked) == n:
                break
        return picked

    def size(tab: str) -> int:   # owner, 4 Nov 2026: Top 12 on every tab (3 full lines of 4 on a wide screen)
        return 12

    def in_tab(ev: dict, tab: str) -> bool:
        ai_row = ev.get("ai") or {}
        if ev["celeb_only"] and tab != CELEB_TAB:
            return False
        if tab == "world":
            return True
        if tab in TOPICS:  # the AI's judgement wins once it has seen the event
            return ai_row[tab] if tab in ai_row else ev["topics"][tab]
        if ai_row.get("region"):
            return tab == ai_row["region"]
        return tab in ev["regions"]

    # Tabs fill up with stories below the first AI cut when the AI rejects stories. Nothing is shown
    # unjudged: whatever would appear (or reach the month editor) goes to the AI, until nothing new does.
    def unjudged_on_show() -> list[dict]:
        found = []
        for tab in TABS:
            for window, n in ((24, size(tab)), (168, size(tab)), (720, 25 + 2 * size(tab))):
                pool = [ev for ev in events if in_tab(ev, tab) and common.within(ev["latest"], window, now)]
                pool.sort(key=lambda e: _score(e, now, window, tab), reverse=True)
                found += top3(pool, n)
                if window == 168:   # Today's quiet-day filler: newest of the week
                    found += sorted(pool, key=lambda e: e["latest"], reverse=True)[:n]
        return [ev for ev in found if not ev.get("ai") and not ev.get("ai_tried")]

    while ai and ai.available:
        waiting = unjudged_on_show()
        if not waiting:
            break
        for ev in waiting:
            ev["candidate"] = True
        _ai_enrich(events, ai)

    # Earlier daily rankings: stories whose articles have left the feeds still count for the month.
    # Stories saved without an AI check (AI outages) stay out: they cannot be judged any more.
    archive = {k: v for k, v in common.load_json(ARCHIVE, {}).items()
               if common.within(common.parse_time(v["parts"]["latest"]), 30 * 24, now)
               and v["card"].get("summary_by") == "ai"}
    live_ids = {ev["id"] for ev in events}

    def same_as_archived(entry: dict, picked: list) -> bool:
        for p in picked:
            if isinstance(p, dict) and "parts" in p:
                other_title, other_story = p["card"]["headline"], p.get("story")
            else:
                other_title, other_story = p["lead"]["title"], (p.get("ai") or {}).get("story")
            if entry.get("story") and entry["story"] == other_story:
                return True
            if common.similarity(entry["card"]["headline"], other_title) >= 0.45:
                return True
            mine = entry["card"]["headline"] + " " + (entry["card"].get("summary") or "")
            theirs = (p["card"]["headline"] + " " + (p["card"].get("summary") or "")) if "parts" in p else \
                (p["lead"]["title"] + " " + ((p.get("ai") or {}).get("summary") or ""))
            if common.similarity(mine, theirs) >= 0.28:
                return True
        return False

    MONTH_SYSTEM = (
        "You are a news editor. From the numbered stories below (all from the last 30 days, for the section named), "
        "choose the 3 most significant: lasting impact, scale (lives, countries, economies affected), and importance "
        "beyond the day. Prefer reported events (disasters, conflicts, elections, deaths, major decisions) over opinion, "
        "analysis or commentary pieces, and over routine forecasts, previews or minor incidents. The 3 picks must be "
        "3 different stories, not two developments of the same story. For a topic section (Business, Tech, "
        "Entertainment, Celebrities, Science, Health or Climate & Weather) only choose stories that are mainly about that topic, and judge significance "
        "within the topic. Use only the given text. "
        "Return the ids of your 3 picks, most significant first.")
    MONTH_SCHEMA = {"type": "object", "properties": {"picks": {"type": "array", "items": {"type": "string"}}},
                    "required": ["picks"], "additionalProperties": False}

    def month_top3(region: str) -> list[dict]:
        n = size(region)
        pool = [(ev, _score(ev, now, 720, region)) for ev in events if in_tab(ev, region)]
        pool += [(a, _score_parts(a["parts"], now, 720, region)) for k, a in archive.items()
                 if k not in live_ids and region in a["tabs"]]
        pool.sort(key=lambda x: -x[1])
        picked = []
        limit = (25 + 2 * n if n > 3 else 25) if ai and ai.available else n   # the AI editor picks n from the strongest
        for item, sc in pool:
            if "parts" in item:
                if same_as_archived(item, picked):
                    continue
            elif any(same_story(item, p) for p in picked if "parts" not in p) or \
                    same_as_archived({"card": {"headline": item["lead"]["title"], "summary": (item.get("ai") or {}).get("summary")},
                                      "story": (item.get("ai") or {}).get("story")},
                                     [p for p in picked if "parts" in p]):
                continue
            picked.append(item)
            if len(picked) == limit:
                break
        if len(picked) > n:
            def text(item):
                if "parts" in item:
                    return f"{item['card']['headline']}: {(item['card'].get('summary') or '')[:220]}"
                return f"{item['lead']['title']}: {((item.get('ai') or {}).get('summary') or item['lead']['summary'] or '')[:220]}"
            ids = [str(i) for i in range(len(picked))]
            listing = "\n".join(f"id={i} :: {text(it)}" for i, it in zip(ids, picked))
            res = ai.json_call("news-month:" + common.fingerprint(region, listing, str(n)), MONTH_SYSTEM.replace("the 3 most", f"the {n} most")
                               .replace("The 3 picks must be 3 different", f"The {n} picks must be {n} different")
                               .replace("ids of your 3 picks", f"ids of your {n} picks"),
                               f"Section: {TAB_NAMES[region]}\n{listing}", MONTH_SCHEMA, max_tokens=1500)
            chosen = [int(i) for i in (res or {}).get("picks", []) if i in ids]
            chosen = list(dict.fromkeys(chosen))[:n]
            picked = [picked[i] for i in chosen] + [x for i, x in enumerate(picked) if i not in chosen]
            picked = picked[:n]
        out = []
        for item in picked:
            if "parts" in item:
                out.append(with_note({**item["card"], "score": _score_parts(item["parts"], now, 720, region), "older": False},
                                     (item.get("notes") or {}).get(region, "")))
            else:
                out.append(with_note({**card(item, 720, region), "older": False}, note_for(item, region)))
        return out

    regions = {}
    new_archive = dict(archive)
    for region in TABS:
        out = {}
        for label, window in (("today", 24), ("week", 168)):
            pool = []
            for ev in events:
                if not common.within(ev["latest"], window, now):
                    continue
                if in_tab(ev, region):
                    pool.append(ev)
            pool.sort(key=lambda e: _score(e, now, window, region), reverse=True)
            n = size(region)
            picked = top3(pool, n)
            if label == "today" and len(picked) < n:
                # a quiet day for this region: fill Today with its most recent stories (dated on the card)
                older = sorted((ev for ev in events if in_tab(ev, region) and ev not in picked
                                and common.within(ev["latest"], 168, now)), key=lambda e: e["latest"], reverse=True)   # up to 7 days so every list reaches 12
                for ev in older:
                    if len(picked) >= n:
                        break
                    if not any(same_story(ev, p) for p in picked):
                        picked.append(ev)
                        ev["_older"] = True
            out[label] = [with_note({**card(ev, window, region), "older": bool(ev.get("_older")) and not common.within(ev["latest"], 24, now)},
                                    note_for(ev, region)) for ev in picked]
        out["month"] = month_top3(region)
        regions[region] = {"name": TAB_NAMES[region], **out}
        # save this region's strongest stories of the week for future month rankings
        week_pool = sorted((ev for ev in events if in_tab(ev, region) and common.within(ev["latest"], 168, now)),
                           key=lambda e: _score(e, now, 168, region), reverse=True)[:size(region)]
        for ev in week_pool:
            entry = new_archive.get(ev["id"]) or {"tabs": []}
            entry.update(card=card(ev, 168), parts=_parts(ev), story=(ev.get("ai") or {}).get("story") or "")
            entry["tabs"] = sorted(set(entry["tabs"]) | {region})
            if note_for(ev, region):
                entry["notes"] = {**(entry.get("notes") or {}), region: note_for(ev, region)}
            new_archive[ev["id"]] = entry
    common.save_json(ARCHIVE, new_archive)
    try:
        news_images.fill(regions)   # only cards without a picture
    except Exception as exc:  # pictures must never stop the news update
        common.log(f"news pictures failed: {exc}")
    return {"regions": regions, "article_count": len(items), "event_count": len(events),
            "outlets": sorted({i["source"] for i in items})}
