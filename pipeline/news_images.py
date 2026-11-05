"""Pictures for news cards that have none (owner, 4 Nov 2026).

Cards that already have a picture are never touched. For the rest: walk the story's outlets from the
best-ranked down and use the first picture that outlet's own article declares (og:image / twitter:image).
Google News search links are traced back to the real article first. Outlets that refuse (Reuters and AP
answer 401/403 outside browsers) simply pass to the next outlet. When no outlet has one, Bing News is asked
for the headline and its picture is kept only if Bing's headline closely matches ours (a wrong picture is
worse than the site's placeholder tile). Every answer is cached in state.
"""
from __future__ import annotations

import html
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import requests

from . import common

CACHE = common.STATE / "news_images.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
DECODE = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
KEEP_DAYS = 35
RETRY_MISS_DAYS = 3
DEADLINE_SECONDS = 480       # whatever is left is picked up by the next run
BING = "https://www.bing.com/news/search"
BING_MIN_MATCH = 0.55   # on real stories: 0.58+ was the same story, 0.36 and below were different articles
NOT_A_PHOTO = re.compile(r"logo|placeholder|default[-_./]|favicon|/icons?/", re.I)


class Temporary(RuntimeError):
    """Worth trying again next run (rate limits, server errors, timeouts): never cached as a miss."""


def page_image(text: str) -> str | None:
    """The picture a page declares for sharing, skipping logos and placeholders."""
    found = {}
    for tag in re.findall(r"<meta\b[^>]*>", text[:400_000], re.I):
        name = re.search(r"""(?:property|name)\s*=\s*["'](og:image|twitter:image|twitter:image:src)["']""", tag, re.I)
        content = re.search(r"""content\s*=\s*(?:"([^"]+)"|'([^']+)')""", tag, re.I)
        if name and content:
            found.setdefault(name.group(1).lower(), html.unescape(content.group(1) or content.group(2)).strip())
    for key in ("og:image", "twitter:image", "twitter:image:src"):
        url = found.get(key, "")
        if url.startswith("http") and not NOT_A_PHOTO.search(url):
            return "https://" + url[7:] if url.startswith("http://") else url
    return None


def _fetch(url: str) -> requests.Response:
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
    except requests.RequestException as exc:
        raise Temporary(str(exc)) from exc
    if r.status_code == 429 or r.status_code >= 500:
        raise Temporary(f"HTTP {r.status_code}")
    return r


def article_url(link: str) -> str | None:
    """The real article behind a Google News link (other links are already the article)."""
    if "news.google.com" not in link:
        return link
    page = _fetch(link).text
    sg, ts = re.search(r'data-n-a-sg="([^"]+)"', page), re.search(r'data-n-a-ts="([^"]+)"', page)
    if not sg or not ts or "/articles/" not in link:
        return None
    article = link.split("/articles/")[1].split("?")[0]
    inner = ('["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],"X","X",1,'
             f'[1,1,1],1,1,null,0,0,null,0],"{article}",{ts.group(1)},"{sg.group(1)}"]')
    try:
        r = requests.post(DECODE, headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
                          data={"f.req": json.dumps([[["Fbv4je", inner, None, "generic"]]])}, timeout=15)
    except requests.RequestException as exc:
        raise Temporary(str(exc)) from exc
    if r.status_code == 429 or r.status_code >= 500:
        raise Temporary(f"HTTP {r.status_code}")
    found = re.search(r'garturlres\\",\\"(https?:[^\\]+)', r.text)
    return found.group(1) if found else None


def image_from_link(link: str) -> str | None:
    url = article_url(link)
    if not url:
        return None
    r = _fetch(url)
    return page_image(r.text) if r.ok else None


def bing_image(headline: str) -> str | None:
    try:
        r = requests.get(BING, params={"q": headline, "format": "rss"}, headers=HEADERS, timeout=12)
    except requests.RequestException as exc:
        raise Temporary(str(exc)) from exc
    if r.status_code == 429 or r.status_code >= 500:
        raise Temporary(f"HTTP {r.status_code}")
    best, best_match = None, 0.0
    for item in re.findall(r"<item>(.*?)</item>", r.text, re.S) if r.ok else []:
        title, image = re.search(r"<title>(.*?)</title>", item, re.S), re.search(r"<News:Image>([^<]+)", item)
        if title and image:
            match = common.similarity(headline, html.unescape(title.group(1)))
            if match >= BING_MIN_MATCH and match > best_match:
                best, best_match = html.unescape(image.group(1)).strip(), match
    if best and best.startswith("http://"):
        best = "https://" + best[7:]
    return best


def fill(regions: dict) -> dict:
    """Fills `image` on picture-less cards in place; returns counts for the log."""
    now = common.now_utc()
    cache = {k: v for k, v in common.load_json(CACHE, {}).items()
             if common.within(common.parse_time(v["at"]), KEEP_DAYS * 24, now)}
    stories: dict[str, list[dict]] = {}
    for tab in regions.values():
        for period in ("today", "week", "month"):
            for card in tab[period]:
                if not card.get("image"):
                    stories.setdefault(card["id"], []).append(card)
    started = time.monotonic()

    def lookup(link: str, find=None) -> str | None:
        hit = cache.get(link)
        if hit and (hit["image"] or common.within(common.parse_time(hit["at"]), RETRY_MISS_DAYS * 24, now)):
            return hit["image"]
        if time.monotonic() - started > DEADLINE_SECONDS:
            raise Temporary("out of time")
        image = (find or image_from_link)(link)   # Temporary leaves no cache entry, so the next run tries again
        cache[link] = {"image": image, "at": common.iso(now)}
        return image

    def resolve(cards: list[dict]) -> str | None:
        tried = set()
        for source in cards[0]["sources"]:   # already best outlet first
            if source["name"] in tried:
                continue
            tried.add(source["name"])
            try:
                image = lookup(source["url"])
            except Exception:   # one bad page must never stop the update
                continue
            if image:
                return image
        headline = cards[0].get("headline")
        if headline:
            try:
                return lookup("bing:" + headline, lambda _: bing_image(headline))
            except Exception:
                return None
        return None

    groups = list(stories.values())
    with ThreadPoolExecutor(max_workers=6) as pool:
        images = list(pool.map(resolve, groups))
    filled = 0
    for cards, image in zip(groups, images):
        if image:
            filled += 1
            for card in cards:
                card["image"] = image
    common.save_json(CACHE, cache)
    common.log(f"news pictures: {filled} of {len(groups)} picture-less stories filled")
    return {"stories": len(groups), "filled": filled}
