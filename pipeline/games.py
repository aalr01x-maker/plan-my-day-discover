"""Gaming: 10 genre tabs x Best of All Time / Best Recent (12 months) / Trending (7 days), Top 20.

Scores are shown separately and never invented:
  critic  Metacritic score from the Steam store or RAWG (absent when neither has one)
  user    Steam user reviews, % positive, Bayesian-weighted toward 70% for games with
          few reviews (prior 70%, 500 reviews) so 40 glowing reviews can't top the list
  confidence  high / medium / low from the number of reviews behind the user score

Ranking (owner's weights):
  all-time   85% quality + 15% popularity
  recent     75% quality + 25% popularity   (released in the last 12 months)
  trending   30% quality + 70% popularity now (Steam players yesterday, 2-week
             player activity, and growth between our daily snapshots)
  quality = mean of the critic and user scores that exist.

Sources: SteamSpy (genre tag lists, review counts, players), Steam store
(release dates, Metacritic, platforms, trailers, store links), RAWG with
RAWG_API_KEY (console platforms incl. PS5, Metacritic, more trailers).
Indie / Survival / Multiplayer are filter tags, not tabs.

A game belongs to a genre tab only when that genre is among the top 7 player tags
of the game on Steam (SteamSpy tag lists alone are too loose: any game tagged
"Horror" by a few players would appear), and software/utilities are excluded.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

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
VIDEOS_PER_RUN = 25


def _spy_tag(tag: str) -> dict:
    time.sleep(1.1)
    try:
        return common.get(SPY, params={"request": "tag", "tag": tag}, timeout=90) or {}
    except common.FetchError as exc:
        common.log(f"games: steamspy tag {tag!r}: {exc}")
        return {}


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


def _tags(appid: str, cache: dict, budget: list[int]) -> list[str] | None:
    """The game's top player tags (most votes first), cached 30 days."""
    hit = cache.get(appid)
    if hit and common.within(common.parse_time(hit["at"]), 30 * 24):
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
    cache[appid] = {"at": common.iso(common.now_utc()), "t": top[:12]}
    return top[:12]


def _rawg(key: str, params: dict, extra: dict) -> list[dict]:
    try:
        data = common.get(f"{RAWG}/games", params={"key": key, "page_size": 40, **params, **extra})
    except common.FetchError as exc:
        common.log(f"games: rawg {params}: {exc}")
        return []
    return data.get("results") or []


def _rawg_card(g: dict, weight_q: float, max_added: int, flags_from: list) -> dict | None:
    """A card for a game found only on RAWG (no Steam page): RAWG user rating + Metacritic."""
    n = g.get("ratings_count") or 0
    if n < 30 and not g.get("metacritic"):
        return None
    user = round(common.bayes_rating((g.get("rating") or 0) * 20, n, PRIOR, 200), 1) if n >= 30 else None
    scores = [x for x in (user, g.get("metacritic")) if x]
    quality = sum(scores) / len(scores) / 100
    score = weight_q * quality + (1 - weight_q) * common.log_scale(g.get("added") or 0, max_added)
    tags = {t.get("slug") for t in flags_from}
    platforms = sorted({p["platform"]["name"] for p in g.get("platforms") or []})
    return {
        "id": f"rawg-{g['id']}", "rank": 0, "title": g["name"], "developer": None, "released": g.get("released"),
        "image": g.get("background_image"),
        "critic": {"score": g["metacritic"], "source": "Metacritic", "url": None} if g.get("metacritic") else None,
        "user": {"score": user, "reviews": n, "source": "RAWG user ratings", "confidence": _confidence(n * 10)} if user else
                {"score": None, "reviews": 0, "source": "RAWG user ratings", "confidence": "low"},
        "players_yesterday": None, "platforms": platforms,
        "flags": [f for f in ("indie", "survival", "multiplayer") if f in tags],
        "genres": [x["name"] for x in g.get("genres") or []], "score": round(score, 4),
        "why": (f"Metacritic {g['metacritic']}; " if g.get("metacritic") else "")
               + (f"{user:.0f}% weighted RAWG user rating from {n:,} ratings" if user else "no user rating yet")
               + "; not on Steam, so console data from RAWG.",
        "video": None, "trailer": None,
        "links": [{"name": "RAWG", "url": f"https://rawg.io/games/{g['slug']}"}]
                 + ([{"name": "PlayStation Store (search)", "url": "https://store.playstation.com/en-us/search/" + g["name"].replace("/", " ")}]
                    if any("PlayStation" in p for p in platforms) else []),
    }


def build(yt: YouTube | None = None) -> dict:
    now = common.now_utc()
    today = now.strftime("%Y-%m-%d")
    year_ago = (now - timedelta(days=365)).strftime("%Y-%m-%d")
    state = common.load_json(STATE, {"details": {}, "ccu": {}, "videos": {}})
    state.setdefault("tags", {})
    flags = {f: set(_spy_tag(tag)) for f, tag in FLAG_TAGS.items()}
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
    ccu_hist = state["ccu"]

    genres = {}
    for gid, (name, tags, rawg_filter) in GENRES.items():
        apps: dict[str, dict] = {}
        for tag in tags:
            for appid, a in _spy_tag(tag).items():
                apps[appid] = a
        if not apps:
            common.log(f"games {gid}: no SteamSpy data")
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

        def detail(r):
            top = _tags(r["appid"], state["tags"], tbudget)
            if not top or not any(t in top[:TOP_TAGS] for t in tags) or NOT_GAMES & set(top[:3]):
                return None  # not really this genre (or not a game)
            d = _details(r["appid"], state["details"], budget)
            if not d or d.get("type") != "game":
                return None
            return d

        def quality(r, d):
            scores = [r["user"]] + ([d["metacritic"]] if d and d.get("metacritic") else [])
            return sum(scores) / len(scores) / 100

        # pre-rank on review data, then confirm with store details (dates, critic score, type)
        pre_all = sorted([r for r in rows if r["reviews"] >= 2000], key=lambda r: 0.85 * r["user"] / 100 + 0.15 * pop_all(r),
                         reverse=True)[:70]
        # Recent: store pages created in the last few years (higher app ids), most-reviewed first;
        # release dates are then checked against the store.
        charts = set(most_played) | set(two_weeks)
        pre_recent = sorted([r for r in rows if r["reviews"] >= 150 and (int(r["appid"]) >= 2_500_000 or r["appid"] in charts)],
                            key=lambda r: -r["reviews"])[:200]
        pre_trend = sorted(rows, key=lambda r: -(r["ccu"] + (20000 if r["appid"] in most_played else 0)
                                                 + (5000 if r["appid"] in two_weeks else 0)))[:50]

        def ranked(pre, weight_q, extra_filter=None, popularity=pop_all):
            out = []
            for r in pre:
                d = detail(r)
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
            "trending": ranked(pre_trend, 0.30, popularity=trend_pop),
        }

        rawg_by_name, rawg_lists = {}, {"all_time": [], "recent": []}
        if rawg_key:
            for ranking, params in (("all_time", {"ordering": "-metacritic", "metacritic": "80,100"}),
                                    ("recent", {"dates": f"{year_ago},{today}", "ordering": "-added"}),
                                    ("recent", {"dates": f"{year_ago},{today}", "ordering": "-metacritic", "metacritic": "70,100"})):
                for g in _rawg(rawg_key, params, rawg_filter):
                    rawg_by_name[common.norm(g["name"]).strip()] = g
                    rawg_lists[ranking].append(g)

        def card(rank, score, r, d, why):
            rawg = rawg_by_name.get(common.norm(r["name"]).strip())
            critic = d.get("metacritic") or (rawg or {}).get("metacritic")
            platforms = ["PC"] + (["Mac"] if "mac" in d["platforms"] else []) + (["Linux"] if "linux" in d["platforms"] else [])
            if rawg:
                platforms = sorted({p["platform"]["name"] for p in rawg.get("platforms") or []} | {"PC"})
            video = state["videos"].get(r["appid"])
            if video is None and yt and yt.available and rank <= 5 and vbudget[0] > 0:
                vbudget[0] -= 1
                found = yt.search(f"{r['name']} official trailer", max_age_days=3650)
                state["videos"][r["appid"]] = video = embed(best_match(f"{r['name']} official trailer", None, found,
                                                                       min_similarity=0.3)) or {}
            return {
                "id": r["appid"], "rank": rank, "title": d.get("name") or r["name"], "developer": r["developer"],
                "released": d.get("released"), "image": d.get("image"),
                "critic": {"score": critic, "source": "Metacritic", "url": d.get("metacritic_url")} if critic else None,
                "user": {"score": r["user"], "reviews": r["reviews"], "source": "Steam reviews",
                         "confidence": _confidence(r["reviews"])},
                "players_yesterday": r["ccu"], "platforms": platforms,
                "flags": [f for f, ids in flags.items() if r["appid"] in ids],
                "genres": d.get("genres"), "score": round(score, 4), "why": why,
                "video": video or None, "trailer": d.get("trailer"),
                "links": [{"name": "Steam", "url": f"https://store.steampowered.com/app/{r['appid']}/"}]
                         + ([{"name": "PlayStation Store (search)",
                              "url": "https://store.playstation.com/en-us/search/" + r["name"].replace("/", " ")}]
                            if any("PlayStation" in p for p in platforms) else [])
                         + ([{"name": "RAWG", "url": f"https://rawg.io/games/{rawg['slug']}"}] if rawg else []),
            }

        def why_all(r, d):
            c = f"Metacritic {d['metacritic']}, " if d.get("metacritic") else ""
            return f"{c}{r['user']:.0f}% weighted Steam user score from {r['reviews']:,} reviews."

        out = {
            "all_time": [card(i + 1, s, r, d, why_all(r, d)) for i, (s, r, d) in enumerate(lists["all_time"])],
            "recent": [card(i + 1, s, r, d, f"Released {d['released']}; " + why_all(r, d)) for i, (s, r, d) in enumerate(lists["recent"])],
            "trending": [card(i + 1, s, r, d, f"{r['ccu']:,} peak players yesterday on Steam"
                              + (f"; #{most_played[r['appid']]['rank']} on Steam's most-played chart"
                                 + (f" (was #{most_played[r['appid']]['last_week_rank']} last week)"
                                    if most_played[r['appid']].get('last_week_rank') else "")
                                 if r["appid"] in most_played else "")
                              + ("; in Steam's 2-week top 100" if r["appid"] in two_weeks else "")
                              + (f"; players {ccu_growth(r['appid']):+.0%} this week" if ccu_growth(r["appid"]) else "") + ".")
                         for i, (s, r, d) in enumerate(lists["trending"])],
        }
        # Games RAWG knows but Steam doesn't (console exclusives incl. PS5), ranked by the same weights
        steam_names = {common.norm(c["title"]).strip() for lst in out.values() for c in lst}
        for ranking, weight_q in (("all_time", 0.85), ("recent", 0.75)):
            extra, seen = [], set()
            max_added = max((g.get("added") or 0 for g in rawg_lists[ranking]), default=1) or 1
            for g in rawg_lists[ranking]:
                key = common.norm(g["name"]).strip()
                if key in steam_names or key in seen or not g.get("released"):
                    continue
                seen.add(key)
                card = _rawg_card(g, weight_q, max_added, flags_from=g.get("tags") or [])
                if card:
                    extra.append(card)
            merged = sorted(out[ranking] + extra, key=lambda c: -c["score"])[:20]
            for i, c in enumerate(merged):
                c["rank"] = i + 1
            out[ranking] = merged

        notes = {}
        if not rawg_key:
            notes["platforms"] = "Console platforms (incl. PS5) appear once the RAWG key is added; until then only Steam data is used."
        genres[gid] = {"name": name, **out, "notes": notes}
        common.log(f"games {gid}: {len(rows)} games; {len(out['all_time'])}/{len(out['recent'])}/{len(out['trending'])}")

    # bound state: ccu history 10 days
    cut = (now - timedelta(days=10)).strftime("%Y-%m-%d")
    state["ccu"] = {a: {d: v for d, v in h.items() if d >= cut} for a, h in ccu_hist.items() if any(d >= cut for d in h)}
    common.save_json(STATE, state)
    if not genres:
        raise RuntimeError("no game data fetched")
    return {"genres": genres, "flags": list(FLAG_TAGS), "sources": ["SteamSpy", "Steam store"] + (["RAWG"] if rawg_key else [])}
