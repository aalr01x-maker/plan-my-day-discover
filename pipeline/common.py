"""Shared helpers for the daily refresh: HTTP with retries, JSON files, time, text matching.

Everything here is stdlib + requests. Secrets come only from environment variables
(GitHub Actions secrets) and are never written to the published site.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
SITE_DATA = ROOT / "site" / "data"          # what gets published
STATE = ROOT / "state"                       # kept between runs (state branch), never published
UA = {"User-Agent": "PlanMyDayDiscover/1.0 (+https://plan-my-day-discover.web.app; personal discovery site)"}

_log: list[str] = []


def log(msg: str) -> None:
    line = f"{datetime.now(timezone.utc).strftime('%H:%M:%SZ')} {msg}"
    print(line, flush=True)
    _log.append(line)


def run_log() -> list[str]:
    return list(_log)


def env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds") if dt else None


def parse_time(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, time.struct_time):
        return datetime(*value[:6], tzinfo=timezone.utc)
    text = str(value).strip()
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%d%H%M%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def within(dt: datetime | None, hours: float, now: datetime | None = None) -> bool:
    return bool(dt) and (now or now_utc()) - dt <= timedelta(hours=hours)


# ---------- HTTP ----------

class FetchError(RuntimeError):
    pass


def get(url: str, *, params=None, headers=None, timeout=30, tries=3, as_json=True):
    """GET with retries: 429/5xx/network errors back off (2s, 6s, 18s); 4xx fails at once."""
    last = None
    for attempt in range(tries):
        try:
            r = requests.get(url, params=params, headers={**UA, **(headers or {})}, timeout=timeout)
            if r.status_code == 429 or r.status_code >= 500:
                last = FetchError(f"HTTP {r.status_code} from {url.split('?')[0]}")
                wait = float(r.headers.get("Retry-After", 0) or 0) or 2 * 3 ** attempt
                time.sleep(min(wait, 30))
                continue
            if r.status_code >= 400:
                raise FetchError(f"HTTP {r.status_code} from {url.split('?')[0]}: {r.text[:160]}")
            return r.json() if as_json else r.text
        except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError,
                requests.exceptions.ContentDecodingError, ValueError) as exc:   # a cut-off response is retried too
            last = FetchError(f"{type(exc).__name__} for {url.split('?')[0]}: {str(exc)[:120]}")
            time.sleep(2 * 3 ** attempt)
    raise last or FetchError(f"failed: {url}")


def post_json(url: str, payload, *, timeout=30, tries=3):
    """POST JSON with the same retry rules as get()."""
    last = None
    for attempt in range(tries):
        try:
            r = requests.post(url, json=payload, headers=UA, timeout=timeout)
            if r.status_code == 429 or r.status_code >= 500:
                last = FetchError(f"HTTP {r.status_code} from {url}")
                time.sleep(min(float(r.headers.get("Retry-After", 0) or 0) or 2 * 3 ** attempt, 30))
                continue
            if r.status_code >= 400:
                raise FetchError(f"HTTP {r.status_code} from {url}: {r.text[:160]}")
            return r.json()
        except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError,
                requests.exceptions.ContentDecodingError, ValueError) as exc:   # a cut-off response is retried too
            last = FetchError(f"{type(exc).__name__} for {url}: {str(exc)[:120]}")
            time.sleep(2 * 3 ** attempt)
    raise last or FetchError(f"failed: {url}")


# ---------- files ----------

def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)


# ---------- text ----------

STOP = set(
    "a an the of to in on for and or but with at by from as is are was were be been it its this that these those "
    "after over into amid says said say new news live latest update video watch how why what who when where will "
    "has have had not no up out about more than his her their they he she we you i".split()
)


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower())


def tokens(text: str) -> set[str]:
    return {w for w in norm(text).split() if len(w) > 2 and w not in STOP}


def similarity(a: str, b: str) -> float:
    """Token overlap (Jaccard) of two titles -- 0..1."""
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"&nbsp;|&#160;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&quot;|&#34;", '"', text)
    text = re.sub(r"&#39;|&apos;", "'", text)
    return re.sub(r"\s+", " ", text).strip()


def fingerprint(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def bayes_rating(rating: float, votes: int, prior: float, min_votes: int) -> float:
    """IMDb-style weighted rating: few votes pull a score toward the prior, so a
    title with 12 votes can't outrank one with 50,000 on raw rating alone."""
    return (votes / (votes + min_votes)) * rating + (min_votes / (votes + min_votes)) * prior


def log_scale(x: float, cap: float) -> float:
    """0..1 on a log scale (popularity counts are heavy-tailed)."""
    if x <= 0:
        return 0.0
    return min(1.0, math.log1p(x) / math.log1p(cap))
