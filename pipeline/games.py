"""Gaming: 10 genre tabs x Best of All Time / Best Recent (12 months) / Trending (7 days), Top 20.

Steam only (owner, 31 Oct 2026): every game is ranked by its Steam player reviews; games not
on Steam (Nintendo, PlayStation exclusives) are left out, and critic scores aren't used.
  user    Steam user reviews, % positive, Bayesian-weighted toward 70% for games with
          few reviews (prior 70%, 500 reviews) so 40 glowing reviews can't top the list
  confidence  high / medium / low from the number of reviews behind the user score

Ranking (owner's weights):
  all-time   85% quality + 15% popularity
  recent     75% quality + 25% popularity   (released in the last 12 months)
  trending   30% quality + 70% popularity now (Steam players yesterday, 2-week
             player activity, and growth between our daily snapshots)
  quality = the Steam user score.

Sources: RAWG (genre lists to find candidates, release dates, platforms), Steam's official activity charts (most played
with last week's rank; 2-week top 100) for Trending, Steam store (release dates,
trailers, store links) and SteamSpy per-game data (Steam review counts,
player tags, cached 30 days). SteamSpy's genre lists are no longer used: that
endpoint stopped answering on 31 Oct 2026.
Indie / Survival / Multiplayer are filter tags, not tabs.

A game belongs to a genre tab only when that genre is among the top 7 player tags
of the game on Steam (SteamSpy tag lists alone are too loose: any game tagged
"Horror" by a few players would appear), and software/utilities are excluded.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta

import requests

from . import common
from .youtube import YouTube, best_match, embed

SPY = "https://steamspy.com/api.php"
STORE = "https://store.steampowered.com/api/appdetails"
RAWG = "https://api.rawg.io/api"
STATE = common.STATE / "games.json"

GENRES = {
    "action": ("Action", ["Action"], {"genres": "action"}),
    "adventure": ("Adventure", ["Adventure"], {"genres": "adventure"}),
    "rpg": ("RPG", ["RPG"], {"genres": "role-playing-games-rpg"}),
    "shooters": ("Shooters", ["Shooter", "FPS"], {"genres": "shooter"}),
    "open-world": ("Open World", ["Open World"], {"tags": "open-world"}),
    "racing": ("Racing", ["Racing"], {"genres": "racing"}),
    "sports": ("Sports", ["Sports"], {"genres": "sports"}),
    "strategy": ("Strategy", ["Strategy"], {"genres": "strategy"}),
    "simulation": ("Simulation", ["Simulation"], {"genres": "simulation"}),
    "horror": ("Horror", ["Horror"], {"tags": "horror"}),
}
FLAG_TAGS = {"indie": "Indie", "survival": "Survival", "multiplayer": "Multiplayer"}
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
            "trailer": {"hls": movie.get("hls_h264"), "thumb": movie.get("thumbnail"), "name": movie.get("name")} if movie else None,
            "adult": str(s.get("required_age", "0")) in ("18",),
        }
    cache[appid] = {"at": common.iso(common.now_utc()), "d": d}
    return d


_STEAM_REVIEWS = {"blocked": False, "used": 0}


def _steam_reviews(appid: str) -> tuple[int, int] | None:
    """Positive/negative review totals from Steam's own review endpoint. SteamSpy no longer counts
    reviews for new games (they show 0), so this fills them in. Stops for the run if Steam refuses."""
    if _STEAM_REVIEWS["blocked"]:
        return None
    status, q = "error", None
    try:
        r = requests.get(f"{STORE.rsplit('/api', 1)[0]}/appreviews/{appid}",
                         params={"json": 1, "language": "all", "purchase_type": "all", "num_per_page": 0, "filter": "summary"},
                         headers=common.UA, timeout=20, allow_redirects=False)
        status = r.status_code
        q = r.json().get("query_summary") if r.status_code == 200 else None
    except (requests.RequestException, ValueError):
        pass
    if not q:
        _STEAM_REVIEWS["blocked"] = True
        common.log(f"games: Steam review counts unavailable (HTTP {status}); new games keep SteamSpy's")
        return None
    _STEAM_REVIEWS["used"] += 1
    time.sleep(0.4)
    return int(q.get("total_positive") or 0), int(q.get("total_negative") or 0)


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
    ccu_hist = state["ccu"]

    genres = {}
    for gid, (name, tags, rawg_filter) in GENRES.items():
        # Steam side: the games on Steam's own activity charts (genre verified per game below).
        # SteamSpy's genre-list endpoint stopped answering (31 Oct 2026), so it isn't used;
        # RAWG supplies the genre lists for All-time and Recent.
        apps: dict[str, dict] = {}
        for appid in set(most_played) | set(two_weeks):
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
            top = _tags(r["appid"], state["tags"], tbudget)
            if not top or not any(t in top[:depth] for t in tags) or NOT_GAMES & set(top[:3]):
                return None  # not really this genre (or not a game)
            d = _details(r["appid"], state["details"], budget)
            if not d or d.get("type") != "game":
                return None
            return d

        def quality(r, d):
            return r["user"] / 100   # Steam player reviews only

        # pre-rank on review data, then confirm with store details (dates, type)
        pre_all = sorted([r for r in rows if r["reviews"] >= 2000], key=lambda r: 0.85 * r["user"] / 100 + 0.15 * pop_all(r),
                         reverse=True)[:70]
        # Recent: store pages created in the last few years (higher app ids), most-reviewed first;
        # release dates are then checked against the store.
        charts = set(most_played) | set(two_weeks)
        pre_recent = sorted([r for r in rows if r["reviews"] >= 50 and (int(r["appid"]) >= 2_500_000 or r["appid"] in charts)],
                            key=lambda r: -r["reviews"])[:200]
        pre_trend = sorted(rows, key=lambda r: -(r["ccu"] + (20000 if r["appid"] in most_played else 0)
                                                 + (5000 if r["appid"] in two_weeks else 0)))[:150]

        def ranked(pre, weight_q, extra_filter=None, popularity=pop_all, depth=TOP_TAGS):
            out = []
            for r in pre:
                d = detail(r, depth)
                if d is None or (extra_filter and not extra_filter(d)):
                    continue
                out.append((weight_q * quality(r, d) + (1 - weight_q) * popularity(r), r, d))
            out.sort(key=lambda x: -x[0])
            return out[:20]

        def trend_pop(r):
            mp = most_played.get(r["appid"])
            climbing = 1.0 if mp and mp.get("last_week_rank") and mp["rank"] < mp["last_week_rank"] else 0.0
            return (0.5 * common.log_scale(r["ccu"], max_ccu) + 0.15 * (1 if r["appid"] in two_weeks else 0)
                    + 0.15 * climbing + 0.2 * max(0.0, ccu_growth(r["appid"])) / 3)

        lists = {
            "all_time": ranked(pre_all, 0.85),
            "recent": ranked(pre_recent, 0.75, lambda d: (d.get("released") or "") >= year_ago and not d.get("coming_soon"),
                             popularity=lambda r: common.log_scale(r["reviews"], max(x["reviews"] for x in pre_recent) or 1)),
            # Trending only covers the ~130 games on Steam's activity charts, so a genre tag anywhere
            # in a game's top 12 counts (top 7 elsewhere)
            "trending": ranked(pre_trend, 0.30, popularity=trend_pop, depth=12),
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
                "flags": [f for f, tag in FLAG_TAGS.items() if tag in ((state["tags"].get(r["appid"]) or {}).get("t") or [])],
                "genres": d.get("genres"), "score": round(score, 4), "why": why,
                "video": video or None, "trailer": d.get("trailer"),
                "links": [{"name": "Steam", "url": f"https://store.steampowered.com/app/{r['appid']}/"}]
                         + ([{"name": "PlayStation Store", "url": ps_url}] if ps_url else
                            [{"name": "PlayStation Store (search)",
                              "url": "https://store.playstation.com/en-us/search/" + r["name"].replace("/", " ")}]
                            if any("PlayStation" in p for p in platforms) else [])
                         + ([{"name": "RAWG", "url": f"https://rawg.io/games/{rawg['slug']}"}] if rawg else []),
            }

        def why_all(r, d):
            return f"{r['user']:.0f}% positive Steam reviews (weighted) from {r['reviews']:,} reviews."

        out = {
            "all_time": [card(i + 1, s, r, d, why_all(r, d)) for i, (s, r, d) in enumerate(lists["all_time"])],
            "recent": [card(i + 1, s, r, d, f"Released {d['released']}; " + why_all(r, d)) for i, (s, r, d) in enumerate(lists["recent"])],
            "trending": [card(i + 1, s, r, d, f"{r['ccu']:,} peak players in a day on Steam (SteamSpy)"
                              + (f"; #{most_played[r['appid']]['rank']} on Steam's most-played chart"
                                 + (f" (was #{most_played[r['appid']]['last_week_rank']} last week)"
                                    if most_played[r['appid']].get('last_week_rank') else "")
                                 if r["appid"] in most_played else "")
                              + ("; in Steam's 2-week top 100" if r["appid"] in two_weeks else "")
                              + (f"; players {ccu_growth(r['appid']):+.0%} this week" if ccu_growth(r["appid"]) else "") + ".")
                         for i, (s, r, d) in enumerate(lists["trending"])],
        }
        # RAWG's genre lists add games beyond Steam's charts. A game that is on Steam (RAWG gives its
        # exact Steam link) is scored with its Steam reviews like every other Steam game; games not on
        # Steam are left out (Steam only).
        steam_ids = {c["id"] for lst in out.values() for c in lst}
        steam_names = {common.norm(c["title"]).strip() for lst in out.values() for c in lst}
        for ranking, weight_q in (("all_time", 0.85), ("recent", 0.75)):
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
                        r = {"appid": appid, "name": a.get("name") or g["name"], "developer": a.get("developer"),
                             "user": user, "reviews": n, "ccu": int(a.get("ccu") or 0), "owners": a.get("owners")}
                        sc = weight_q * quality(r, d) + (1 - weight_q) * common.log_scale(n, max_reviews)
                        why = (f"Released {d.get('released') or g.get('released')}; " if ranking == "recent" else "") + why_all(r, d)
                        extra.append(card(0, sc, r, d, why, rawg=g))
                        steam_ids.add(appid)
                        continue
            merged = sorted(out[ranking] + extra, key=lambda c: -c["score"])[:20]
            for i, c in enumerate(merged):
                c["rank"] = i + 1
            out[ranking] = merged

        # Trending covers Steam's activity charts; niche genres are topped up to 20 with this genre's
        # other Steam games, ranked by Steam's official live player count (70%) and reviews (30%)
        if len(out["trending"]) < 20:
            have = {c["id"] for c in out["trending"]}
            pool = {c["id"]: c for lst in (out["all_time"], out["recent"]) for c in lst if c["id"] not in have and c.get("user")}
            for c in pool.values():
                if c.get("players_now") is None:
                    c["players_now"] = _players_now(c["id"], players_now)
            live = [c for c in pool.values() if (c.get("players_now") or 0) >= 50]
            top_live = max((c["players_now"] for c in live), default=1)
            live.sort(key=lambda c: -(0.7 * common.log_scale(c["players_now"], top_live) + 0.3 * c["user"]["score"] / 100))
            for c in live[: 20 - len(out["trending"])]:
                out["trending"].append({**c, "why": f"{c['players_now']:,} people playing on Steam right now; "
                                                     f"{c['user']['score']:.0f}% positive from {c['user']['reviews']:,} reviews."})
            for i, c in enumerate(out["trending"]):
                c["rank"] = i + 1

        notes = {}
        if len(out["recent"]) < 20:
            notes["recent"] = (f"Only {len(out['recent'])} games in this genre were confirmed as released in the last 12 months"
                               + ("." if rawg_key else "; the full list (incl. PS5) arrives with the RAWG key."))
        if len(out["trending"]) < 20:
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
    return {"genres": genres, "flags": list(FLAG_TAGS), "sources": (["RAWG"] if rawg_key else []) + ["Steam charts", "Steam store", "SteamSpy (per-game)"]}
