"""Gaming: 11 genre tabs x Best of All Time / Best Recent (12 months) / Trending, Top 30 shown.

Steam only (owner, 31 Oct 2026): every game is ranked by its Steam player reviews; games not
on Steam (Nintendo, PlayStation exclusives) are left out, and critic scores aren't used.
  user    Steam user reviews, % positive, Bayesian-weighted toward 70% for games with
          few reviews (prior 70%, 500 reviews) so 40 glowing reviews can't top the list
  confidence  high / medium / low from the number of reviews behind the user score

Ranking (owner's weights):
  Owner's formulas (2 Nov 2026), Steam data only, counts log-scaled so giants don't win by size alone:
  all-time   33.3% review score + 33.3% players right now + 33.4% number of reviews (2 Nov 2026, replaces
             45% quality + 35% review volume + 20% owners)
  recent     65% review score + 35% number of reviews (2 Nov 2026; retention dropped)
             (released in the last 12 months)
  trending   50% players right now + 50% review score (2 Nov 2026, replaces 7-day growth/average formula).  (Before 2 Nov: 85/15, 75/25, 30/70.)
  Old notes: trending used Steam players yesterday, 2-week
             player activity, and growth between our daily snapshots)
  quality = the Steam user score.

Sources: RAWG (genre lists to find candidates, release dates, platforms), Steam's official activity charts (most played
with last week's rank; 2-week top 100) for Trending, Steam store (release dates,
trailers, store links) and SteamSpy per-game data (Steam review counts,
player tags, cached 30 days). SteamSpy's genre lists are no longer used: that
endpoint stopped answering on 31 Oct 2026.
Story / Online modes are still computed (flags) but the site no longer offers them as filters (owner, 2 Nov 2026: inaccurate). Originally (owner, 1 Nov 2026): Story = has a single-player/story mode,
Online = has online multiplayer; a game with both shows under each, a story-only game never under Online.
Each list ships up to LIST_SIZE games so a filtered view still gets its own full top 20.

A game belongs to a genre tab only when that genre is among the top 7 player tags
of the game on Steam (SteamSpy tag lists alone are too loose: any game tagged
"Horror" by a few players would appear), and software/utilities are excluded.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta


from . import common
from .youtube import YouTube, best_match, embed

SPY = "https://steamspy.com/api.php"
STORE = "https://store.steampowered.com/api/appdetails"
RAWG = "https://api.rawg.io/api"
STATE = common.STATE / "games.json"

GENRES = {
    "all": ("All", [], {}),   # every genre (owner: first tab)
    "action": ("Action", ["Action"], {"genres": "action"}),
    "adventure": ("Adventure", ["Adventure"], {"genres": "adventure"}),
    "rpg": ("RPG", ["RPG"], {"genres": "role-playing-games-rpg"}),
    "shooters": ("Shooters", ["Shooter", "FPS"], {"genres": "shooter"}),
    "open-world": ("Open World", ["Open World"], {"tags": "open-world"}),
    "sandbox": ("Sandbox", ["Sandbox"], {"tags": "sandbox"}),   # owner, 1 Nov 2026
    "racing": ("Racing", ["Racing"], {"genres": "racing"}),
    "sports": ("Sports", ["Sports"], {"genres": "sports"}),
    "strategy": ("Strategy", ["Strategy"], {"genres": "strategy"}),
    "simulation": ("Simulation", ["Simulation"], {"genres": "simulation"}),
    "horror": ("Horror", ["Horror"], {"tags": "horror"}),
}
MODES = ("story", "online")
LOCAL_ONLY = {"Shared/Split Screen", "Shared/Split Screen PvP", "Shared/Split Screen Co-op", "LAN PvP", "LAN Co-op"}
LIST_SIZE = 60   # the site shows the top 30 (owner, 2 Nov 2026)


def _modes(categories: list[str] | None, tags: list[str] | None) -> list[str]:
    """Story/online from Steam's official store categories; Steam player tags until those are cached."""
    out = []
    if categories is not None:
        cats = set(categories)
        if "Single-player" in cats:
            out.append("story")
        explicit = cats & {"Online PvP", "Online Co-op", "MMO", "Cross-Platform Multiplayer"}
        # a bare "Multi-player" counts as online unless Steam only lists local/LAN ways to play together
        if explicit or ("Multi-player" in cats and not cats & LOCAL_ONLY):
            out.append("online")
        return out
    t = set(tags or [])
    if t & {"Singleplayer", "Story Rich"}:
        out.append("story")
    if t & {"Online Co-Op", "PvP", "Massively Multiplayer", "Multiplayer", "MMORPG", "Battle Royale"}:
        out.append("online")
    return out
MIN_REVIEWS = 500          # Bayesian weight
PRIOR = 70.0
DETAILS_PER_RUN = 900      # Steam store asks for <= ~200 requests / 5 minutes (1.5 s apart)
TAGS_PER_RUN = 900         # SteamSpy asks for 1 request / second
TOP_TAGS = 7
NOT_GAMES = {"Software", "Utilities", "Design & Illustration", "Animation & Modeling", "Video Production",
             "Audio Production", "Photo Editing", "Game Development"}
VIDEOS_PER_RUN = 5


def _user_score(pos: int, neg: int) -> tuple[float | None, int]:
    n = pos + neg
    if n < 20:
        return None, n
    return round(common.bayes_rating(100 * pos / n, n, PRIOR, MIN_REVIEWS), 1), n


def _confidence(n: int) -> str:
    return "high" if n >= 5000 else "medium" if n >= 500 else "low"


def _details(appid: str, cache: dict, budget: list[int]) -> dict | None:
    hit = cache.get(appid)
    if hit and common.within(common.parse_time(hit["at"]), 14 * 24):
        return hit["d"]
    if budget[0] <= 0:
        return hit["d"] if hit else None
    budget[0] -= 1
    time.sleep(1.5)
    try:
        data = common.get(STORE, params={"appids": appid, "l": "english", "cc": "us"})
    except common.FetchError as exc:
        common.log(f"games: appdetails {appid}: {exc}")
        return hit["d"] if hit else None
    row = (data or {}).get(appid) or {}
    d = None
    if row.get("success"):
        s = row["data"]
        released = None
        try:
            released = datetime.strptime(s.get("release_date", {}).get("date", ""), "%d %b, %Y").strftime("%Y-%m-%d")
        except ValueError:
            try:
                released = datetime.strptime(s.get("release_date", {}).get("date", ""), "%b %d, %Y").strftime("%Y-%m-%d")
            except ValueError:
                pass
        movie = next((m for m in s.get("movies") or [] if m.get("highlight")), (s.get("movies") or [None])[0])
        d = {
            "type": s.get("type"), "name": s.get("name"), "released": released,
            "coming_soon": (s.get("release_date") or {}).get("coming_soon", False),
            "metacritic": (s.get("metacritic") or {}).get("score"),
            "metacritic_url": (s.get("metacritic") or {}).get("url"),
            "platforms": [p for p, ok in (s.get("platforms") or {}).items() if ok],
            "image": s.get("header_image"),
            "genres": [g["description"] for g in s.get("genres") or []],
            "categories": [c["description"] for c in s.get("categories") or []],
            "trailer": {"hls": movie.get("hls_h264"), "thumb": movie.get("thumbnail"), "name": movie.get("name")} if movie else None,
            "adult": str(s.get("required_age", "0")) in ("18",),
        }
    cache[appid] = {"at": common.iso(common.now_utc()), "d": d}
    return d


_STEAM_REVIEWS = {"blocked": False, "used": 0}


def _steam_reviews(appid: str) -> tuple[int, int] | None:
    """Positive/negative review totals from Steam's official store API (IStoreBrowseService, no key).
    SteamSpy no longer counts reviews for new games (they show 0), so this fills them in."""
    if _STEAM_REVIEWS["blocked"]:
        return None
    payload = json.dumps({"ids": [{"appid": int(appid)}], "context": {"language": "english", "country_code": "US"},
                          "data_request": {"include_reviews": True}})
    try:
        data = common.get("https://api.steampowered.com/IStoreBrowseService/GetItems/v1/", params={"input_json": payload})
        items = (data.get("response") or {}).get("store_items") or []
        summary = ((items[0].get("reviews") or {}).get("summary_filtered") or {}) if items else {}
    except (common.FetchError, ValueError, AttributeError) as exc:
        _STEAM_REVIEWS["blocked"] = True
        common.log(f"games: Steam review counts unavailable ({exc}); new games keep SteamSpy's")
        return None
    n = int(summary.get("review_count") or 0)
    if not n:
        return None
    _STEAM_REVIEWS["used"] += 1
    positive = round(n * int(summary.get("percent_positive") or 0) / 100)
    return positive, n - positive


STEAM_TAGS: dict[str, list[str]] = {}   # appid -> Steam's own tags (this run), for games SteamSpy hasn't tagged yet
_TAG_NAMES: dict[int, str] = {}


def _steam_tag_names() -> dict[int, str]:
    if not _TAG_NAMES:
        try:
            for t in common.get("https://api.steampowered.com/IStoreService/GetTagList/v1/", params={"language": "english"})["response"]["tags"]:
                _TAG_NAMES[int(t["tagid"])] = t["name"]
        except (common.FetchError, KeyError, TypeError, ValueError) as exc:
            common.log(f"games: Steam tag list unavailable: {exc}")
    return _TAG_NAMES


def _steam_reviews_many(appids: list[str], cache: dict) -> None:
    """Steam's own review totals for many games at once (IStoreBrowseService, 50 per request), into
    cache[appid] = (percent positive, review count, still sold). Owner, 2 Nov 2026: every number from
    Steam itself; games no longer sold on Steam (FIFA 22, delisted) aren't recommended."""
    todo = [a for a in dict.fromkeys(appids) if a not in cache]
    for i in range(0, len(todo), 50):
        batch = todo[i:i + 50]
        payload = json.dumps({"ids": [{"appid": int(a)} for a in batch], "context": {"language": "english", "country_code": "US"},
                              "data_request": {"include_reviews": True, "include_basic_info": True,
                                               "include_purchase_options": True, "include_tag_count": 20}})
        try:
            data = common.get("https://api.steampowered.com/IStoreBrowseService/GetItems/v1/", params={"input_json": payload})
        except (common.FetchError, ValueError) as exc:
            common.log(f"games: Steam review totals batch failed: {exc}")
            continue
        for it in (data.get("response") or {}).get("store_items") or []:
            summ = ((it.get("reviews") or {}).get("summary_filtered") or {})
            if it.get("appid") and it.get("tags"):   # Steam's own player tags, most votes first
                names = _steam_tag_names()
                STEAM_TAGS[str(it["appid"])] = [names[t["tagid"]] for t in sorted(it["tags"], key=lambda t: -t.get("weight", 0))
                                                if t.get("tagid") in names]
            if it.get("appid") and summ.get("review_count"):
                sold = bool(it.get("is_free") or it.get("best_purchase_option") or it.get("purchase_options"))
                cache[str(it["appid"])] = (int(summ.get("percent_positive") or 0), int(summ["review_count"]), sold)
        time.sleep(0.3)


# Steam store tag ids per genre tab, for Steam's own "top sellers" list of that tag
STEAM_TAG_IDS = {"all": None, "action": 19, "adventure": 21, "rpg": 122, "shooters": 1774, "open-world": 1695,
                 "sandbox": 3810, "racing": 699, "sports": 701, "strategy": 9, "simulation": 599, "horror": 1667}


def _steam_top_sellers(tag_id: int | None, cache: dict, pages: int = 2) -> list[str]:
    """The top ~100 sellers on Steam for a tag (Steam's own store search), cached 1 day. Owner, 2 Nov 2026:
    Sports Trending missed NBA 2K, FC, Madden, Football Manager -- the activity charts alone are too thin."""
    k = str(tag_id)
    hit = cache.get(k)
    if hit and common.within(common.parse_time(hit["at"]), 24):
        return hit["ids"]
    ids: list[str] = []
    for page in range(pages):
        params = {"start": page * 50, "count": 50, "category1": 998, "filter": "topsellers", "infinite": 1,
                  "cc": "us", "l": "english"}
        if tag_id:
            params["tags"] = tag_id
        try:
            html = (common.get("https://store.steampowered.com/search/results/", params=params) or {}).get("results_html") or ""
        except (common.FetchError, ValueError):
            break
        ids += [i for i in re.findall(r'data-ds-appid="(\d+)"', html) if i not in ids]
        time.sleep(1.0)
    if ids:
        cache[k] = {"at": common.iso(common.now_utc()), "ids": ids}
    return ids or (hit["ids"] if hit else [])


SPY_TAG_NAMES = {"action": "Action", "adventure": "Adventure", "rpg": "RPG", "shooters": "Shooter",
                 "open-world": "Open World", "sandbox": "Sandbox", "racing": "Racing", "sports": "Sports",
                 "strategy": "Strategy", "simulation": "Simulation", "horror": "Horror"}


def _spy_tag_top(tag: str | None, cache: dict, n: int = 150) -> dict[str, dict]:
    """Every Steam game with this tag (SteamSpy's tag list), keeping the n most played -- only to decide
    WHICH games are considered; their ranking uses Steam's own live players and reviews. Cached 1 day;
    SteamSpy asks for at most one such list request a minute."""
    if not tag:
        return {}
    hit = cache.get(tag)
    if hit and common.within(common.parse_time(hit["at"]), 24):
        return hit["apps"]
    time.sleep(61 if cache.get("_called_this_run") else 0)
    cache["_called_this_run"] = True
    try:
        data = common.get(SPY, params={"request": "tag", "tag": tag}, timeout=90) or {}
    except common.FetchError as exc:
        common.log(f"games: steamspy tag {tag}: {exc}")
        return hit["apps"] if hit else {}
    top = sorted(data.values(), key=lambda a: -int(a.get("ccu") or 0))[:n]
    apps = {str(a["appid"]): {f: a.get(f) for f in ("name", "developer", "positive", "negative", "ccu", "owners")} for a in top}
    cache[tag] = {"at": common.iso(common.now_utc()), "apps": apps}
    return apps


def _tags(appid: str, cache: dict, budget: list[int]) -> list[str] | None:
    """The game's top player tags (most votes first) and review numbers, cached 30 days, or 5 days
    for games with under 5,000 reviews (new games' review counts grow fast)."""
    hit = cache.get(appid)
    a = (hit or {}).get("a") or {}
    n = int(a.get("positive") or 0) + int(a.get("negative") or 0)
    ttl = 30 if n >= 5000 else 5 if n else 0   # 0 reviews: SteamSpy's gap for new games, ask again
    # entries cached before review numbers were kept ("a") are refreshed
    if hit and "a" in hit and common.within(common.parse_time(hit["at"]), ttl * 24):
        return hit["t"]
    if budget[0] <= 0:
        return hit["t"] if hit else None
    budget[0] -= 1
    time.sleep(1.05)
    try:
        data = common.get(SPY, params={"request": "appdetails", "appid": appid}, timeout=60) or {}
    except common.FetchError as exc:
        common.log(f"games: steamspy appdetails {appid}: {exc}")
        return hit["t"] if hit else None
    tags = data.get("tags") or {}
    top = [t for t, _ in sorted(tags.items(), key=lambda kv: -kv[1])] if isinstance(tags, dict) else []
    stats = {f: data.get(f) for f in ("name", "developer", "positive", "negative", "ccu", "owners")}
    if data.get("name") and not (int(stats.get("positive") or 0) + int(stats.get("negative") or 0)):
        counts = _steam_reviews(appid)   # SteamSpy shows 0 reviews for new games
        if counts:
            stats["positive"], stats["negative"] = counts
    cache[appid] = {"at": common.iso(common.now_utc()), "t": top[:12], "a": stats if data.get("name") else None}
    return top[:12]


def _rawg(key: str, params: dict, extra: dict, pages: int = 2) -> list[dict]:
    out = []
    for page in range(1, pages + 1):
        try:
            data = common.get(f"{RAWG}/games", params={"key": key, "page_size": 40, "page": page, **params, **extra})
        except common.FetchError as exc:
            common.log(f"games: rawg {params}: {exc}")
            break
        out += data.get("results") or []
        if not data.get("next"):
            break
    return out


RAWG_DATES_PER_RUN = 200
STEAM_RELEASE_PER_RUN = 300


def _steam_release(appid: str, cache: dict, budget: list[int]) -> dict | None:
    """Steam's own release record (IStoreBrowseService): steam_release_date, original_steam_release_date
    (Early Access launch) and release_from_early_access_date. Cached 30 days."""
    hit = cache.get(appid)
    if hit and common.within(common.parse_time(hit["at"]), 30 * 24):
        return hit["r"]
    if budget[0] <= 0:
        return hit["r"] if hit else None
    budget[0] -= 1
    payload = json.dumps({"ids": [{"appid": int(appid)}], "context": {"language": "english", "country_code": "US"},
                          "data_request": {"include_release": True}})
    try:
        data = common.get("https://api.steampowered.com/IStoreBrowseService/GetItems/v1/", params={"input_json": payload})
        items = (data.get("response") or {}).get("store_items") or []
        rel = (items[0].get("release") or {}) if items else {}
    except (common.FetchError, ValueError, AttributeError):
        return hit["r"] if hit else None
    out = {k: datetime.utcfromtimestamp(v).strftime("%Y-%m-%d") for k, v in rel.items()
           if k.endswith("_date") and isinstance(v, int) and v > 0}
    cache[appid] = {"at": common.iso(common.now_utc()), "r": out}
    return out


def _first_release(key: str | None, name: str, cache: dict, budget: list[int]) -> str | None:
    """The game's ORIGINAL release date per RAWG (Early Access launch included). Steam's own date is
    the 1.0 launch, so Valheim (2021) or Palworld (2024) looked new in 2026 (owner, 2 Nov 2026).
    Cached 60 days; None when unknown."""
    k = common.norm(name).strip()
    hit = cache.get(k)
    if hit and common.within(common.parse_time(hit["at"]), 60 * 24):
        return hit["d"]
    if not key or budget[0] <= 0:
        return hit["d"] if hit else None
    budget[0] -= 1
    try:
        res = (common.get(f"{RAWG}/games", params={"key": key, "search": name, "search_exact": "true", "page_size": 5}) or {}).get("results") or []
    except common.FetchError:
        return hit["d"] if hit else None
    best = next((g for g in res if common.norm(g.get("name") or "").strip() == k), None)
    cache[k] = {"at": common.iso(common.now_utc()), "d": (best or {}).get("released")}
    return cache[k]["d"]


def _rawg_stores(key: str, game_id: int, cache: dict) -> dict:
    """A RAWG game's exact store pages: {"steam": appid or None, "ps": PlayStation Store url or None}.
    Store links never change, so they are cached for good."""
    hit = cache.get(str(game_id))
    if hit is not None:
        return hit
    try:
        data = common.get(f"{RAWG}/games/{game_id}/stores", params={"key": key})
    except common.FetchError as exc:
        common.log(f"games: rawg stores {game_id}: {exc}")
        return {"steam": None, "ps": None}
    out = {"steam": None, "ps": None}
    for row in data.get("results") or []:
        url = row.get("url") or ""
        m = re.search(r"store\.steampowered\.com/app/(\d+)", url)
        if m:
            out["steam"] = m.group(1)
        elif "store.playstation.com" in url:
            out["ps"] = url
    cache[str(game_id)] = out
    return out


def _players_now(appid: str, cache: dict) -> int | None:
    """Players in the game right now, from Steam's own official API."""
    if appid not in cache:
        try:
            data = common.get("https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/",
                              params={"appid": appid}, tries=2)
            cache[appid] = (data.get("response") or {}).get("player_count")
        except common.FetchError:
            cache[appid] = None
        time.sleep(0.15)
    return cache[appid]


def build(yt: YouTube | None = None) -> dict:
    now = common.now_utc()
    today = now.strftime("%Y-%m-%d")
    year_ago = (now - timedelta(days=365)).strftime("%Y-%m-%d")
    state = common.load_json(STATE, {"details": {}, "ccu": {}, "videos": {}})
    state.setdefault("tags", {})
    state.setdefault("rawg_stores", {})
    time.sleep(1.1)
    try:
        two_weeks = common.get(SPY, params={"request": "top100in2weeks"}, timeout=90) or {}
    except common.FetchError:
        two_weeks = {}
    try:  # Steam's own most-played chart with last week's rank
        ranks = common.get("https://api.steampowered.com/ISteamChartsService/GetMostPlayedGames/v1/")["response"]["ranks"]
        most_played = {str(r["appid"]): r for r in ranks}
    except (common.FetchError, KeyError, TypeError):
        most_played = {}
    rawg_key = common.env("RAWG_API_KEY")
    budget, vbudget, tbudget = [DETAILS_PER_RUN], [VIDEOS_PER_RUN], [TAGS_PER_RUN]
    players_now: dict = {}
    steam_totals: dict = {}   # appid -> (percent positive, review count), Steam's own, this run
    spy_cache = state.setdefault("spy_tag_top", {})
    spy_cache.pop("_called_this_run", None)
    ccu_hist = state["ccu"]

    genres = {}
    for gid, (name, tags, rawg_filter) in GENRES.items():
        # Steam side: the games on Steam's own activity charts (genre verified per game below).
        # SteamSpy's genre-list endpoint stopped answering (31 Oct 2026), so it isn't used;
        # RAWG supplies the genre lists for All-time and Recent.
        apps: dict[str, dict] = {}
        sellers = _steam_top_sellers(STEAM_TAG_IDS.get(gid), state.setdefault("top_sellers", {}))
        spy_top = _spy_tag_top(SPY_TAG_NAMES.get(gid), spy_cache)
        for appid, a in spy_top.items():
            apps[appid] = a
        for appid in (set(most_played) | set(two_weeks) | set(sellers)) - set(spy_top):
            a = two_weeks.get(appid)
            if not a:
                _tags(appid, state["tags"], tbudget)
                a = (state["tags"].get(appid) or {}).get("a")
            if a:
                apps[appid] = a
        if not apps:
            previous = (common.load_json(common.STATE / "last" / "games.json", {}).get("genres") or {}).get(gid)
            if previous:
                previous.setdefault("notes", {})["stale"] = f"Steam data unavailable today; showing lists from {previous.get('built_at', 'the last update')}."
                genres[gid] = previous
            common.log(f"games {gid}: no SteamSpy data" + ("; kept previous lists" if previous else ""))
            continue
        rows = []
        for appid, a in apps.items():
            user, n = _user_score(int(a.get("positive") or 0), int(a.get("negative") or 0))
            if user is None:
                continue
            ccu = int(a.get("ccu") or 0)
            h = ccu_hist.setdefault(appid, {})
            h[today] = ccu
            rows.append({"appid": appid, "name": a.get("name"), "developer": a.get("developer"), "user": user,
                         "reviews": n, "ccu": ccu, "owners": a.get("owners")})
        max_reviews = max(r["reviews"] for r in rows)
        max_ccu = max((r["ccu"] for r in rows), default=1) or 1

        def pop_all(r):
            return common.log_scale(r["reviews"], max_reviews)

        def ccu_growth(appid: str) -> float:
            h = ccu_hist.get(appid) or {}
            week_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d")
            days = sorted(d for d in h if d >= week_ago)
            if len(days) < 2 or not h[days[0]]:
                return 0.0
            return max(-1.0, min(3.0, (h[days[-1]] - h[days[0]]) / h[days[0]]))

        def detail(r, depth=TOP_TAGS):
            # SteamSpy's tags, or Steam's own when SteamSpy hasn't tagged a brand-new game (NBA 2K27, FM 27)
            top = _tags(r["appid"], state["tags"], tbudget) or STEAM_TAGS.get(r["appid"])
            if not top or (tags and not any(t in top[:depth] for t in tags)) or NOT_GAMES & set(top[:3]):
                return None  # not really this genre (or not a game)
            d = _details(r["appid"], state["details"], budget)
            if not d or d.get("type") != "game":
                return None
            # apps Steam files as software/utilities (crosshair overlays, wallpaper tools) aren't games
            if set(d.get("genres") or []) & (NOT_GAMES | {"Software Training", "Utilities", "Web Publishing", "Education"}):
                return None
            return d

        def quality(r, d=None):
            return r["user"] / 100   # Steam player reviews only (Bayesian-weighted)

        def owners_mid(r) -> float:   # SteamSpy owners "1,000,000 .. 2,000,000" -> 1,500,000
            nums = [int(x.replace(",", "")) for x in re.findall(r"[\d,]+", str(r.get("owners") or "")) if x.replace(",", "")]
            return sum(nums[:2]) / len(nums[:2]) if nums else 0.0

        max_owners = max((owners_mid(r) for r in rows), default=1) or 1

        def longterm(r):   # lasting popularity: how many people own it on Steam
            return common.log_scale(owners_mid(r), max_owners)

        def retention(r):  # still being played: Steam players now per Steam review (5% = full marks)
            return min(1.0, r["ccu"] / max(1, r["reviews"]) * 20)

        def week_ccu(appid: str) -> tuple[float, float, float]:
            """(average players over 7 days, % change, players gained) from our daily snapshots."""
            h = ccu_hist.get(appid) or {}
            week_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d")
            days = sorted(d for d in h if d >= week_ago)
            if not days:
                return 0.0, 0.0, 0.0
            avg = sum(h[d] for d in days) / len(days)
            first, last = h[days[0]], h[days[-1]]
            pct = max(-1.0, min(3.0, (last - first) / first)) if len(days) >= 2 and first else 0.0
            return avg, pct, max(0.0, last - first)

        max_gain = max((week_ccu(r["appid"])[2] for r in rows), default=1) or 1
        max_avg = max((week_ccu(r["appid"])[0] for r in rows), default=1) or 1

        def score_all(r, d=None):
            # owner, 2 Nov 2026: equal thirds -- review score, players right now, number of reviews (log-scaled)
            return 0.333 * quality(r) + 0.333 * common.log_scale(r["ccu"], max_ccu) + 0.334 * pop_all(r)

        def score_recent_factory(pool):
            top = max((x["reviews"] for x in pool), default=1) or 1
            # owner, 2 Nov 2026: retention dropped (the players-per-review stand-in rewarded tiny games)
            return lambda r, d=None: 0.65 * quality(r) + 0.35 * common.log_scale(r["reviews"], top)

        def score_trend(r, d=None):
            # owner, 2 Nov 2026: half players right now (log-scaled), half Steam review score
            return 0.5 * common.log_scale(r["ccu"], max_ccu) + 0.5 * quality(r)

        # pre-rank on review data, then confirm with store details (dates, type)
        pre_all = sorted([r for r in rows if r["reviews"] >= 2000], key=score_all, reverse=True)[:90]
        # Recent: store pages created in the last few years (higher app ids), most-reviewed first;
        # release dates are then checked against the store.
        charts = set(most_played) | set(two_weeks)
        pre_recent = sorted([r for r in rows if r["reviews"] >= 50 and (int(r["appid"]) >= 2_500_000 or r["appid"] in charts)],
                            key=lambda r: -r["reviews"])[:200]
        pre_trend = sorted(rows, key=lambda r: -(r["ccu"] + (20000 if r["appid"] in most_played else 0)
                                                 + (5000 if r["appid"] in two_weeks else 0)))[:150]
        # Steam's current top sellers always get considered (SteamSpy hasn't caught up with brand-new games)
        seller_set, have = set(sellers[:60]), {r["appid"] for r in pre_trend}
        pre_trend += [r for r in rows if r["appid"] in seller_set and r["appid"] not in have]

        # Steam only (owner, 2 Nov 2026): review score/count and players now come from Steam itself for
        # every game that can make a list; SteamSpy only helps find candidates and their genre tags
        cands = {r["appid"]: r for r in pre_all + pre_recent + pre_trend}
        _steam_reviews_many(list(cands), steam_totals)
        for r in cands.values():
            if r["appid"] in steam_totals:
                pct, n, sold = steam_totals[r["appid"]]
                pos = round(n * pct / 100)
                r["user"], r["reviews"] = _user_score(pos, n - pos)
                if not sold:
                    r["user"] = None   # no longer sold on Steam: dropped from every list below
            live = _players_now(r["appid"], players_now)
            r["ccu"] = int(live or 0)
        for lst in (pre_all, pre_recent, pre_trend):
            lst[:] = [r for r in lst if r["user"] is not None]
        max_ccu = max((r["ccu"] for r in cands.values()), default=1) or 1
        max_reviews = max((r["reviews"] for r in cands.values()), default=1) or 1
        score_recent = score_recent_factory(pre_recent)
        rawg_dates, rawg_budget = state.setdefault("rawg_first_release", {}), [RAWG_DATES_PER_RUN]

        steam_rel, steam_rel_budget = state.setdefault("steam_release", {}), [STEAM_RELEASE_PER_RUN]

        def _was_out_before(d, appid=None) -> bool:
            """Best Recent: a 1.0 launch of a game that was already out in Early Access over a year ago.
            Steam's own record first (original Early Access date; a game marked as having left Early
            Access without one counts as old), RAWG's original date as a fallback."""
            rel = _steam_release(appid, steam_rel, steam_rel_budget) if appid else None
            if rel:
                orig = rel.get("original_steam_release_date")
                if orig:
                    return orig < year_ago
                if rel.get("release_from_early_access_date"):
                    return True
            first = _first_release(rawg_key, d.get("name") or "", rawg_dates, rawg_budget)
            return bool(first and first < year_ago and first < (d.get("released") or "9999"))

        def ranked(pre, scorer, extra_filter=None, depth=TOP_TAGS):
            out = []
            for r in pre:
                d = detail(r, depth)
                if d is None or (extra_filter and not extra_filter(d, r["appid"])):
                    continue
                out.append((scorer(r, d), r, d))
            out.sort(key=lambda x: -x[0])
            return out[:LIST_SIZE]

        lists = {
            "all_time": ranked(pre_all, score_all),
            "recent": ranked(pre_recent, score_recent, lambda d, appid: (d.get("released") or "") >= year_ago and not d.get("coming_soon")
                             and not _was_out_before(d, appid)),
            # Trending only covers the ~130 games on Steam's activity charts, so a genre tag anywhere
            # in a game's top 12 counts (top 7 elsewhere)
            "trending": ranked(pre_trend, score_trend, depth=12),
        }

        rawg_by_name, rawg_lists = {}, {"all_time": [], "recent": []}
        if rawg_key:
            # RAWG only finds candidates (most-played first); Steam reviews decide the ranking
            for ranking, params, pages in (("all_time", {"ordering": "-added"}, 2),
                                           ("recent", {"dates": f"{year_ago},{today}", "ordering": "-added"}, 5)):
                for g in _rawg(rawg_key, params, rawg_filter, pages=pages):
                    rawg_by_name[common.norm(g["name"]).strip()] = g
                    rawg_lists[ranking].append(g)

        def card(rank, score, r, d, why, rawg=None):
            rawg = rawg or rawg_by_name.get(common.norm(r["name"]).strip())
            ps_url = _rawg_stores(rawg_key, rawg["id"], state["rawg_stores"]).get("ps") if rawg and rawg_key else None
            platforms = ["PC"] + (["Mac"] if "mac" in d["platforms"] else []) + (["Linux"] if "linux" in d["platforms"] else [])
            if rawg:
                platforms = sorted({p["platform"]["name"] for p in rawg.get("platforms") or []} | {"PC"})
            video = state["videos"].get(r["appid"])
            if video is None and yt and yt.available and not yt.exhausted and rank <= 5 and vbudget[0] > 0:
                vbudget[0] -= 1
                found = yt.search(f"{r['name']} official trailer", max_age_days=3650)
                video = embed(best_match(f"{r['name']} official trailer", None, found, min_similarity=0.3)) or {}
                if not yt.exhausted:   # a refused search isn't "no trailer"; try again another day
                    state["videos"][r["appid"]] = video
            return {
                "id": r["appid"], "rank": rank, "title": d.get("name") or r["name"], "developer": r["developer"],
                "released": d.get("released"), "image": d.get("image"),
                "critic": None,   # Steam only: critic scores aren't used
                "user": {"score": r["user"], "reviews": r["reviews"], "source": "Steam reviews",
                         "confidence": _confidence(r["reviews"])},
                "players_now": _players_now(r["appid"], players_now), "platforms": platforms,
                "flags": _modes(d.get("categories"), (state["tags"].get(r["appid"]) or {}).get("t")),
                "genres": d.get("genres"), "score": round(score, 4), "why": why,
                "video": video or None, "trailer": d.get("trailer"),
                "links": [{"name": "Steam", "url": f"https://store.steampowered.com/app/{r['appid']}/"}]
                         + ([{"name": "PlayStation Store", "url": ps_url}] if ps_url else
                            [{"name": "PlayStation Store (search)",
                              "url": "https://store.playstation.com/en-us/search/" + r["name"].replace("/", " ")}]
                            if any("PlayStation" in p for p in platforms) else [])
                         + ([{"name": "RAWG", "url": f"https://rawg.io/games/{rawg['slug']}"}] if rawg else []),
            }

        def why_recent(r):   # exactly what Best Recent ranks by
            return f"{r['user']:.0f}% positive Steam reviews (weighted) from {r['reviews']:,} reviews."

        def why_all(r, d):
            return (f"{r['user']:.0f}% positive Steam reviews (weighted) from {r['reviews']:,} reviews; "
                    f"{r['ccu']:,} playing on Steam right now.")

        out = {
            "all_time": [card(i + 1, s, r, d, why_all(r, d)) for i, (s, r, d) in enumerate(lists["all_time"])],
            "recent": [card(i + 1, s, r, d, f"Released {d['released']}; " + why_recent(r)) for i, (s, r, d) in enumerate(lists["recent"])],
            "trending": [card(i + 1, s, r, d, "")   # why-text set below with the final Trending ranking
                         for i, (s, r, d) in enumerate(lists["trending"])],
        }
        # RAWG's genre lists add games beyond Steam's charts. A game that is on Steam (RAWG gives its
        # exact Steam link) is scored with its Steam reviews like every other Steam game; games not on
        # Steam are left out (Steam only).
        steam_ids = {c["id"] for lst in out.values() for c in lst}
        steam_names = {common.norm(c["title"]).strip() for lst in out.values() for c in lst}
        for ranking, scorer in (("all_time", score_all), ("recent", score_recent)):
            extra, seen = [], set()
            for g in rawg_lists[ranking]:
                key = common.norm(g["name"]).strip()
                if key in steam_names or key in seen or not g.get("released"):
                    continue
                seen.add(key)
                appid = _rawg_stores(rawg_key, g["id"], state["rawg_stores"]).get("steam")
                if appid:
                    if appid in steam_ids:
                        continue
                    _tags(appid, state["tags"], tbudget)
                    a = (state["tags"].get(appid) or {}).get("a") or {}
                    user, n = _user_score(int(a.get("positive") or 0), int(a.get("negative") or 0))
                    d = _details(appid, state["details"], budget)
                    if user is not None and d and d.get("type") == "game":
                        if ranking == "recent" and not ((d.get("released") or g.get("released") or "") >= year_ago):
                            continue
                        if ranking == "recent" and ((g.get("released") or "9999") < year_ago or _was_out_before(d, appid)):
                            continue   # first released (Early Access) over a year ago
                        if set(d.get("genres") or []) & (NOT_GAMES | {"Software Training", "Utilities", "Education"}):
                            continue
                        r = {"appid": appid, "name": a.get("name") or g["name"], "developer": a.get("developer"),
                             "user": user, "reviews": n, "ccu": int(a.get("ccu") or 0), "owners": a.get("owners")}
                        _steam_reviews_many([appid], steam_totals)   # Steam's own numbers (owner, 2 Nov 2026)
                        if appid in steam_totals:
                            pct, tot, sold = steam_totals[appid]
                            pos = round(tot * pct / 100)
                            r["user"], r["reviews"] = _user_score(pos, tot - pos)
                            if not sold:
                                continue
                        r["ccu"] = int(_players_now(appid, players_now) or 0)
                        if r["user"] is None:
                            continue
                        sc = scorer(r, d)
                        why = (f"Released {d.get('released') or g.get('released')}; " + why_recent(r)) if ranking == "recent" else why_all(r, d)
                        extra.append(card(0, sc, r, d, why, rawg=g))
                        steam_ids.add(appid)
                        continue
            merged = sorted(out[ranking] + extra, key=lambda c: -c["score"])[:LIST_SIZE]
            for i, c in enumerate(merged):
                c["rank"] = i + 1
            out[ranking] = merged

        # Trending (owner, 2 Nov 2026): ONE ranking for the whole list -- 50% players on Steam right now
        # (Steam's live count, the number shown on the card; log-scaled) + 50% Steam review score. The
        # genre's other well-known games join the charted ones and everything is ranked together.
        have = {c["id"] for c in out["trending"]}
        pool = list(out["trending"]) + [c for c in {c["id"]: c for lst in (out["all_time"], out["recent"]) for c in lst
                                                    if c["id"] not in have and c.get("user")}.values()]
        for c in pool:
            if c.get("players_now") is None:
                c["players_now"] = _players_now(c["id"], players_now)
        pool = [c for c in pool if (c.get("players_now") or 0) >= 50 and c.get("user")]

        # players: rank within this genre's pool (most played = 1.0, least = ~0), so the players half really
        # separates a 20,000-player game from a 320-player one (owner, 2 Nov 2026; the log scale squeezed it)
        by_players = sorted(pool, key=lambda c: c["players_now"])
        prank = {c["id"]: (i + 1) / len(by_players) for i, c in enumerate(by_players)}

        def live_score(c):
            return 0.5 * prank[c["id"]] + 0.5 * c["user"]["score"] / 100
        pool.sort(key=live_score, reverse=True)
        out["trending"] = [{**c, "rank": i + 1, "score": round(live_score(c), 4),
                            "why": f"{c['players_now']:,} playing on Steam right now; "
                                   f"{c['user']['score']:.0f}% positive from {c['user']['reviews']:,} reviews."}
                           for i, c in enumerate(pool[:LIST_SIZE])]

        notes = {}
        if len(out["recent"]) < 30:
            notes["recent"] = (f"Only {len(out['recent'])} games in this genre were confirmed as released in the last 12 months"
                               + ("." if rawg_key else "; the full list (incl. PS5) arrives with the RAWG key."))
        if len(out["trending"]) < 30:
            notes["trending"] = (f"Only {len(out['trending'])} {name.lower()} game{'s' if len(out['trending']) != 1 else ''} "
                                 f"have 50+ people playing on Steam right now.")
        if not rawg_key:
            notes["platforms"] = "Console platforms (incl. PS5) appear once the RAWG key is added; until then only Steam data is used."
        genres[gid] = {"name": name, **out, "notes": notes, "built_at": common.iso(now)}
        common.log(f"games {gid}: {len(rows)} games; {len(out['all_time'])}/{len(out['recent'])}/{len(out['trending'])}")

    # bound state: ccu history 10 days
    cut = (now - timedelta(days=10)).strftime("%Y-%m-%d")
    state["ccu"] = {a: {d: v for d, v in h.items() if d >= cut} for a, h in ccu_hist.items() if any(d >= cut for d in h)}
    common.save_json(STATE, state)
    if not genres:
        raise RuntimeError("no game data fetched")
    return {"genres": genres, "flags": list(MODES), "sources": (["RAWG"] if rawg_key else []) + ["Steam charts", "Steam store", "SteamSpy (per-game)"]}
