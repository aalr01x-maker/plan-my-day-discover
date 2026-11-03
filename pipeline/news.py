"""Optimal News: Top 10 for World and Business, Top 3 per region, Today (24 h), This Week (7 days) and
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
    # Oceania's own public broadcasters + Guardian Australia (the global outlets cover the region thinly)
    ("ABC News (Australia)", "https://www.abc.net.au/news/feed/51120/rss.xml", "oceania"),
    ("RNZ", "https://www.rnz.co.nz/rss/national.xml", "oceania"),
    ("RNZ", "https://www.rnz.co.nz/rss/pacific.xml", "oceania"),
    ("The Guardian", "https://www.theguardian.com/australia-news/rss", "oceania"),
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
TIER = {"Reuters": 1.0, "AP": 1.0, "BBC News": 0.95, "Al Jazeera": 0.85, "DW": 0.85, "France 24": 0.85, "CNBC": 0.85,
        "ABC News (Australia)": 0.9, "RNZ": 0.9, "The Guardian": 0.85}

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
    # the three weeks before that, one search per week, so This Month sees the whole month
    today = common.now_utc().date()
    for k in range(1, 4):
        after, before = today - timedelta(days=7 * k + 7), today - timedelta(days=7 * k)
        feeds += [(None, GNEWS + f"(site:reuters.com OR site:apnews.com) {q} after:{after} before:{before}", None, False)
                  for q in WIRE_QUERIES.values()]
    feeds += [(o, u, None, True) for o, u in BUSINESS_FEEDS]
    feeds.append((None, GNEWS + f"(site:reuters.com OR site:apnews.com) {BUSINESS_QUERY} when:30d", None, True))
    for k in range(1, 4):
        after, before = today - timedelta(days=7 * k + 7), today - timedelta(days=7 * k)
        feeds.append((None, GNEWS + f"(site:reuters.com OR site:apnews.com) {BUSINESS_QUERY} after:{after} before:{before}", None, True))
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


ARCHIVE = common.STATE / "news_archive.json"


def _parts(ev: dict) -> dict:
    """What a score is made of, kept so archived stories can be re-scored later."""
    return {"outlets": len(ev["outlets"]),
            "sig": SIGNIFICANCE[ev["ai"]["significance"]] if ev.get("ai") else ev["keyword_severity"],
            "latest": common.iso(ev["latest"]), "tier": max(TIER.get(s, 0.5) for s in ev["outlets"]),
            "days": len({a["published"].date() for a in ev["articles"]})}


def _score_parts(parts: dict, now, window_hours: float) -> float:
    corroboration = min(1.0, math.log1p(parts["outlets"]) / math.log1p(6))
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


def _score(ev: dict, now, window_hours: float) -> float:
    return _score_parts(_parts(ev), now, window_hours)


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
    items = [i for i in _collect() if common.within(i["published"], 30 * 24, now)]
    if not items:
        raise RuntimeError("no news articles fetched")
    events = _cluster(items)

    # Candidates for AI: the best keyword-scored events per region and window.
    for window in (24, 168, 720):
        for tab in TABS:
            pool = [ev for ev in events if common.within(ev["latest"], window, now)
                    and (tab == "world" or (ev["business"] if tab == "business" else tab in ev["regions"]))]
            pool.sort(key=lambda e: _score(e, now, window), reverse=True)
            for ev in pool[:15 if tab in ("world", "business") else 8]:   # World + Business show 10 (owner, 2-3 Nov 2026)
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

    def size(tab: str) -> int:   # owner: World Top 10 (2 Nov), Business Top 10 (3 Nov); every region keeps Top 3
        return 10 if tab in ("world", "business") else 3

    def in_tab(ev: dict, tab: str) -> bool:
        ai_row = ev.get("ai") or {}
        if tab == "world":
            return True
        if tab == "business":  # the AI's judgement wins once it has seen the event
            return ai_row["business"] if "business" in ai_row else ev["business"]
        if ai_row.get("region"):
            return tab == ai_row["region"]
        return tab in ev["regions"]

    # Earlier daily rankings: stories whose articles have left the feeds still count for the month
    archive = {k: v for k, v in common.load_json(ARCHIVE, {}).items()
               if common.within(common.parse_time(v["parts"]["latest"]), 30 * 24, now)}
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
        "3 different stories, not two developments of the same story. For a Business "
        "section only choose stories that are mainly about business, markets or the economy. Use only the given text. "
        "Return the ids of your 3 picks, most significant first.")
    MONTH_SCHEMA = {"type": "object", "properties": {"picks": {"type": "array", "items": {"type": "string"}}},
                    "required": ["picks"], "additionalProperties": False}

    def month_top3(region: str) -> list[dict]:
        n = size(region)
        pool = [(ev, _score(ev, now, 720)) for ev in events if in_tab(ev, region)]
        pool += [(a, _score_parts(a["parts"], now, 720)) for k, a in archive.items()
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
                               f"Section: {TAB_NAMES[region]}\n{listing}", MONTH_SCHEMA, max_tokens=300)
            chosen = [int(i) for i in (res or {}).get("picks", []) if i in ids]
            chosen = list(dict.fromkeys(chosen))[:n]
            picked = [picked[i] for i in chosen] + [x for i, x in enumerate(picked) if i not in chosen]
            picked = picked[:n]
        out = []
        for item in picked:
            if "parts" in item:
                out.append({**item["card"], "score": _score_parts(item["parts"], now, 720), "older": False})
            else:
                out.append({**card(item, 720), "older": False})
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
            pool.sort(key=lambda e: _score(e, now, window), reverse=True)
            n = size(region)
            picked = top3(pool, n)
            if label == "today" and len(picked) < n:
                # a quiet day for this region: fill Today with its most recent stories (dated on the card)
                older = sorted((ev for ev in events if in_tab(ev, region) and ev not in picked
                                and common.within(ev["latest"], 72, now)), key=lambda e: e["latest"], reverse=True)
                for ev in older:
                    if len(picked) >= n:
                        break
                    if not any(same_story(ev, p) for p in picked):
                        picked.append(ev)
                        ev["_older"] = True
            out[label] = [{**card(ev, window), "older": bool(ev.get("_older")) and not common.within(ev["latest"], 24, now)}
                          for ev in picked]
        out["month"] = month_top3(region)
        regions[region] = {"name": TAB_NAMES[region], **out}
        # save this region's 10 strongest stories of the week for future month rankings
        week_pool = sorted((ev for ev in events if in_tab(ev, region) and common.within(ev["latest"], 168, now)),
                           key=lambda e: _score(e, now, 168), reverse=True)[:10]
        for ev in week_pool:
            entry = new_archive.get(ev["id"]) or {"tabs": []}
            entry.update(card=card(ev, 168), parts=_parts(ev), story=(ev.get("ai") or {}).get("story") or "")
            entry["tabs"] = sorted(set(entry["tabs"]) | {region})
            new_archive[ev["id"]] = entry
    common.save_json(ARCHIVE, new_archive)
    return {"regions": regions, "article_count": len(items), "event_count": len(events),
            "outlets": sorted({i["source"] for i in items})}
