"""Optimal News: Top 3 per region (plus a Business tab), Today (24 h) and This Week (7 days).

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
from datetime import timedelta

import feedparser

from . import common, geo
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
]
# Business tab (owner, 31 Oct 2026): a topic tab beside the regions, same ranking rules.
BUSINESS_FEEDS = [
    ("BBC News", "https://feeds.bbci.co.uk/news/business/rss.xml"),
    ("DW", "https://rss.dw.com/rdf/rss-en-bus"),
    ("France 24", "https://www.france24.com/en/business-tech/rss"),
    ("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
]
BUSINESS_QUERY = "(business OR markets OR economy OR earnings OR inflation)"
BUSINESS_WORDS = set(("economy economic markets market stocks shares earnings profit revenue inflation interest rates "
                      "central bank fed ecb tariffs trade merger acquisition takeover deal bankruptcy layoffs jobs gdp "
                      "recession oil prices investors ipo company companies ceo bank banks currency dollar bond bonds").split())
WAR_WORDS = set("war missile missiles strikes troops drone drones killed attack military army".split())
TABS = ["world", "business"] + [r for r in geo.REGIONS if r != "world"]
TAB_NAMES = {**geo.REGION_NAMES, "business": "Business"}
# Reuters and AP through Google News search feeds (title links go via Google News).
GNEWS = "https://news.google.com/rss/search?hl=en-US&gl=US&ceid=US:en&q="
WIRE_QUERIES = {
    "world": "world", "asia": "Asia", "africa": "Africa", "north-america": "(United States OR Canada OR Mexico)",
    "south-america": "(Brazil OR Argentina OR Colombia OR Venezuela OR Chile OR Peru)", "middle-east": "Middle East",
    "europe": "Europe", "oceania": "(Australia OR New Zealand OR Pacific)",
}
WIRES = {"reuters.com": "Reuters", "apnews.com": "AP"}
TIER = {"Reuters": 1.0, "AP": 1.0, "BBC News": 0.95, "Al Jazeera": 0.85, "DW": 0.85, "France 24": 0.85, "CNBC": 0.85}

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


def _collect() -> list[dict]:
    items = []
    # Search feeds are not a reliable region label (an "Australia" search also returns
    # other stories), so only BBC's regional feeds count as a region source.
    feeds = [(o, u, r, False) for o, u, r in FEEDS]
    feeds += [(None, GNEWS + f"(site:reuters.com OR site:apnews.com) {q} when:7d", None, False)
              for q in WIRE_QUERIES.values()]
    feeds += [(o, u, None, True) for o, u in BUSINESS_FEEDS]
    feeds.append((None, GNEWS + f"(site:reuters.com OR site:apnews.com) {BUSINESS_QUERY} when:7d", None, True))
    for outlet, url, region, business in feeds:
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
                "business_feed": business,
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
        text = " ".join(a["title"] + " " + a["summary"] for a in arts)
        labelled = sorted({a["feed_region"] for a in arts if a["feed_region"]})
        lead_title = max(arts, key=lambda a: (TIER.get(a["source"], 0.5), len(a["summary"])))["title"]
        guessed = geo.guess_regions(lead_title)  # the lead headline names the place most reliably
        # BBC regional feeds also carry other regions' top stories: the headline's place wins.
        ev["regions"] = guessed[:1] or labelled[:1]
        words = set(common.norm(text).split())
        # Business: carried by a business desk, or clearly about the economy / companies.
        # (CNBC's and others' business desks also carry general headlines, so the text must agree.)
        hits = len(words & BUSINESS_WORDS) - len(words & WAR_WORDS)
        ev["business"] = hits >= (2 if any(a["business_feed"] for a in arts) else 4)
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
    "(e.g. 'spain train crash minister'); business: true only if the main angle is business, the economy, "
    "markets, trade, companies or finance (wars, arms sales, diplomacy and politics are false unless the story is "
    "about their economic or market effect). Keep each summary under 45 words."
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
            "story": {"type": "string"}, "business": {"type": "boolean"},
        },
        "required": ["id", "summary", "country", "region", "significance", "is_update", "disputed", "story", "business"],
        "additionalProperties": False,
    }}},
    "required": ["events"], "additionalProperties": False,
}
SIGNIFICANCE = {"major": 1.0, "notable": 0.6, "minor": 0.15}


def _ai_enrich(events: list[dict], ai) -> None:
    """Summaries + classification for candidate events, 10 per call, cached by event content."""
    todo = [ev for ev in events if ev.get("candidate")]
    for i in range(0, len(todo), 10):
        batch = todo[i : i + 10]
        lines = []
        for ev in batch:
            arts = sorted(ev["articles"], key=lambda a: -TIER.get(a["source"], 0.5))[:4]
            snippets = " || ".join(f"[{a['source']}] {a['title']}: {a['summary'][:260]}" for a in arts)
            lines.append(f"id={ev['id']} :: {snippets}")
        key = "news:" + common.fingerprint(*lines)
        result = ai.json_call(key, AI_SYSTEM, "\n".join(lines), AI_SCHEMA, max_tokens=5000) if ai else None
        for row in (result or {}).get("events", []):
            ev = next((e for e in batch if e["id"] == row["id"]), None)
            if ev:
                ev["ai"] = row


def _score(ev: dict, now, window_hours: float) -> float:
    corroboration = min(1.0, math.log1p(len(ev["outlets"])) / math.log1p(6))
    sig = SIGNIFICANCE[ev["ai"]["significance"]] if ev.get("ai") else ev["keyword_severity"]
    age_h = (now - ev["latest"]).total_seconds() / 3600
    recency = max(0.0, 1 - age_h / window_hours)
    tier = max(TIER.get(s, 0.5) for s in ev["outlets"])
    return round(0.35 * corroboration + 0.30 * sig + 0.20 * recency + 0.15 * tier, 4)


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


def build(ai=None, yt: YouTube | None = None) -> dict:
    now = common.now_utc()
    items = [i for i in _collect() if common.within(i["published"], 7 * 24, now)]
    if not items:
        raise RuntimeError("no news articles fetched")
    events = _cluster(items)

    # Candidates for AI: the best keyword-scored events per region and window.
    for window in (24, 168):
        for tab in TABS:
            pool = [ev for ev in events if common.within(ev["latest"], window, now)
                    and (tab == "world" or (ev["business"] if tab == "business" else tab in ev["regions"]))]
            pool.sort(key=lambda e: _score(e, now, window), reverse=True)
            for ev in pool[:8]:
                ev["candidate"] = True
    _ai_enrich(events, ai)

    videos = []
    if yt and yt.available:
        for name, cid in NEWS_CHANNELS.items():
            videos += yt.uploads(cid)

    def card(ev: dict, window: float) -> dict:
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
            "score": _score(ev, now, window),
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

    def same_story(a: dict, b: dict) -> bool:
        if a.get("ai") and b.get("ai") and a["ai"].get("story") and a["ai"]["story"] == b["ai"].get("story"):
            return True
        return len(signature(a) & signature(b)) >= 2

    def top3(pool: list[dict]) -> list[dict]:
        picked: list[dict] = []
        for ev in pool:
            if not any(same_story(ev, p) for p in picked):
                picked.append(ev)
            if len(picked) == 3:
                break
        return picked

    def in_tab(ev: dict, tab: str) -> bool:
        ai_row = ev.get("ai") or {}
        if tab == "world":
            return True
        if tab == "business":  # the AI's judgement wins once it has seen the event
            return ai_row["business"] if "business" in ai_row else ev["business"]
        if ai_row.get("region"):
            return tab == ai_row["region"]
        return tab in ev["regions"]

    regions = {}
    for region in TABS:
        out = {}
        for label, window in (("today", 24), ("week", 168)):
            pool = []
            for ev in events:
                if not common.within(ev["latest"], window, now):
                    continue
                if in_tab(ev, region):
                    pool.append(ev)
            pool.sort(key=lambda e: _score(e, now, window), reverse=True)
            out[label] = [card(ev, window) for ev in top3(pool)]
        regions[region] = {"name": TAB_NAMES[region], **out}
    return {"regions": regions, "article_count": len(items), "event_count": len(events),
            "outlets": sorted({i["source"] for i in items})}
