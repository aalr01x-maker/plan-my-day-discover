"""Best Movie Kinds: Movies and TV Shows, by genre tab, three separate rankings.

Source: TMDB (TMDB_TOKEN, the v4 "API Read Access Token"). Rankings are never mixed:

  Best of All Time   85% quality + 15% popularity
  Best Recent        75% quality + 25% popularity -- movies released in the last
                     12 months; TV shows with episodes aired in the last 12 months
                     (so new seasons of older shows count)
  Trending (7 days)  30% quality + 70% popularity (TMDB weekly trending + popularity)

quality = Bayesian-weighted TMDB vote average (pulled toward the genre's mean when a
title has few votes); popularity = vote count (all-time) or TMDB popularity (others),
log scaled. Trailers are the official YouTube trailers TMDB lists; streaming shows
Kuwait availability (JustWatch data via TMDB), else says it isn't listed for Kuwait.
TV cards add season ratings and the highest-rated episodes when enough votes exist.
"""
from __future__ import annotations

from datetime import timedelta

from . import common

API = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p/w342"
STATE = common.STATE / "movies.json"
REGION = "KW"
FALLBACK_REGION = "US"

# tab -> (name, movie filter, tv filter). Filters are TMDB discover params; "kw:<word>"
# values are resolved to TMDB keyword ids at run time (no hard-coded guesses).
TABS = {
    "action": ("Action", {"with_genres": "28", "without_genres": "16"}, {"with_genres": "10759", "without_genres": "16"}),
    "anime": ("Anime", {"with_genres": "16", "with_original_language": "ja"}, {"with_genres": "16", "with_original_language": "ja"}),
    "comedy": ("Comedy", {"with_genres": "35", "without_genres": "16,99"}, {"with_genres": "35", "without_genres": "16,99,10767,10764"}),
    "crime-drama": ("Crime & Drama", {"with_genres": "80|18", "without_genres": "16,99"}, {"with_genres": "80|18", "without_genres": "16,99,10766"}),
    "horror": ("Horror", {"with_genres": "27", "without_genres": "16"}, {"with_keywords": "kw:horror", "without_genres": "16"}),
    "thriller": ("Thriller", {"with_genres": "53", "without_genres": "16"}, {"with_genres": "9648", "without_genres": "16"}),
    "scifi-fantasy": ("Sci-Fi & Fantasy", {"with_genres": "878|14", "without_genres": "16"}, {"with_genres": "10765", "without_genres": "16"}),
    "romance": ("Romance", {"with_genres": "10749", "without_genres": "16"}, {"with_keywords": "kw:romance", "without_genres": "16"}),
    "western-historical": ("Western & Historical", {"with_genres": "37|36|10752", "without_genres": "16"},
                           {"with_genres": "37|10768", "without_genres": "16"}),
    "documentary": ("Documentary", {"with_genres": "99"}, {"with_genres": "99"}),
    "experimental": ("Experimental & Other", {"with_keywords": "kw:experimental film|kw:surrealism|kw:avant-garde"},
                     {"with_keywords": "kw:surrealism|kw:anthology", "without_genres": "16"}),
    "classic": ("Classic / OG", {"primary_release_date.lte": "1989-12-31"}, {"first_air_date.lte": "1999-12-31"}),
    "adult-animation": ("Adult Animation", {"with_genres": "16", "with_keywords": "kw:adult animation"},
                        {"with_genres": "16", "with_keywords": "kw:adult animation", "without_original_language": "ja"}),
}
MIN_VOTES = {"movie": {"all_time": 1500, "recent": 40, "trending": 50},
             "tv": {"all_time": 300, "recent": 15, "trending": 20}}
BAYES_M = {"movie": 2000, "tv": 500}
LIMIT = {"movie": 20, "tv": 20}
WEIGHTS = {"all_time": 0.85, "recent": 0.75, "trending": 0.30}


DETAIL_FIELDS = ("id", "title", "name", "release_date", "first_air_date", "overview", "poster_path", "genres",
                 "number_of_seasons", "status", "seasons", "videos", "watch/providers")
EPISODE_FIELDS = ("episode_number", "name", "vote_average", "vote_count", "air_date")


def _slim(path: str, data: dict) -> dict:
    """Keep only the fields the site uses (full TMDB responses carry cast/crew and are huge)."""
    if "/season/" in path:
        return {"episodes": [{k: e.get(k) for k in EPISODE_FIELDS} for e in data.get("episodes") or []]}
    if path.startswith(("/movie/", "/tv/")):
        out = {k: data.get(k) for k in DETAIL_FIELDS if k in data}
        if out.get("seasons"):
            out["seasons"] = [{k: x.get(k) for k in ("season_number", "episode_count", "air_date")} for x in out["seasons"]]
        if out.get("watch/providers"):
            results = out["watch/providers"].get("results") or {}
            out["watch/providers"] = {"results": {r: results[r] for r in ("KW", "US") if r in results}}
        return out
    return data


class TMDB:
    def __init__(self, token: str, cache: dict):
        self.h = {"Authorization": f"Bearer {token}", "accept": "application/json"}
        self.cache = cache
        self.calls = 0

    def get(self, path: str, params: dict | None = None, ttl_h: float = 0) -> dict:
        key = path + "?" + "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
        hit = self.cache.get(key)
        if ttl_h and hit and common.within(common.parse_time(hit["at"]), ttl_h):
            return hit["d"]
        self.calls += 1
        data = common.get(API + path, params=params, headers=self.h)
        if ttl_h:
            data = _slim(path, data)
            self.cache[key] = {"at": common.iso(common.now_utc()), "d": data}
        return data

    def keyword(self, word: str) -> str | None:
        data = self.get("/search/keyword", {"query": word}, ttl_h=24 * 30)
        exact = [k for k in data.get("results", []) if k["name"].lower() == word.lower()]
        return str(exact[0]["id"]) if exact else None


def _resolve(tmdb: TMDB, params: dict) -> dict | None:
    out = {}
    for k, v in params.items():
        if isinstance(v, str) and "kw:" in v:
            ids = [tmdb.keyword(part[3:]) for part in v.split("|")]
            ids = [i for i in ids if i]
            if not ids:
                return None
            v = "|".join(ids)
        out[k] = v
    return out


def _discover(tmdb: TMDB, media: str, params: dict, pages: int) -> list[dict]:
    out = []
    for page in range(1, pages + 1):
        try:
            data = tmdb.get(f"/discover/{media}", {**params, "page": page, "include_adult": "false"})
        except common.FetchError as exc:
            common.log(f"movies: discover {media} {params}: {exc}")
            break
        out += data.get("results", [])
        if page >= data.get("total_pages", 1):
            break
    return out


def _providers(block: dict) -> dict:
    results = (block or {}).get("results") or {}
    region = REGION if REGION in results else (FALLBACK_REGION if FALLBACK_REGION in results else None)
    if not region:
        return {"region": None, "stream": [], "rent": [], "buy": [], "link": None}
    r = results[region]
    names = lambda key: [p["provider_name"] for p in r.get(key) or []][:6]
    return {"region": region, "stream": names("flatrate") + names("free") + names("ads"), "rent": names("rent"),
            "buy": names("buy"), "link": r.get("link")}


def _trailer(videos: dict) -> dict | None:
    vids = [v for v in (videos or {}).get("results", []) if v.get("site") == "YouTube" and v.get("type") in ("Trailer", "Teaser")]
    vids.sort(key=lambda v: (v.get("type") != "Trailer", not v.get("official"), v.get("published_at") or ""))
    if not vids:
        return None
    v = vids[0]
    return {"youtube_id": v["key"], "title": v.get("name"), "channel": "Official" if v.get("official") else None,
            "published": v.get("published_at"), "url": f"https://www.youtube.com/watch?v={v['key']}"}


def _tv_extras(tmdb: TMDB, show: dict) -> dict:
    """Season averages and highest-rated episodes, only from episodes with enough votes."""
    seasons, episodes = [], []
    for s in show.get("seasons") or []:
        n = s.get("season_number")
        if not n or not s.get("episode_count"):
            continue
        try:
            data = tmdb.get(f"/tv/{show['id']}/season/{n}", ttl_h=24 * 14)
        except common.FetchError:
            continue
        rated = [e for e in data.get("episodes") or [] if (e.get("vote_count") or 0) >= 10]
        if len(rated) >= max(2, len(data.get("episodes") or []) // 2):
            seasons.append({"season": n, "rating": round(sum(e["vote_average"] for e in rated) / len(rated), 2),
                            "rated_episodes": len(rated), "year": (s.get("air_date") or "")[:4]})
        for e in rated:
            if (e.get("vote_count") or 0) >= 20:
                episodes.append({"season": n, "episode": e["episode_number"], "name": e.get("name"),
                                 "rating": e["vote_average"], "votes": e["vote_count"], "aired": e.get("air_date")})
    episodes.sort(key=lambda e: (-common.bayes_rating(e["rating"], e["votes"], 7.5, 50), -e["votes"]))
    return {"seasons": seasons, "top_episodes": episodes[:5]}


def build() -> dict:
    token = common.env("TMDB_TOKEN")
    if not token:
        raise RuntimeError("TMDB_TOKEN not set")
    now = common.now_utc()
    year_ago = (now - timedelta(days=365)).strftime("%Y-%m-%d")
    today = now.strftime("%Y-%m-%d")
    state = common.load_json(STATE, {"tmdb": {}})
    tmdb = TMDB(token, state["tmdb"])

    trending = {m: [] for m in ("movie", "tv")}
    for media in trending:
        for page in range(1, 6):
            try:
                trending[media] += tmdb.get(f"/trending/{media}/week", {"page": page}).get("results", [])
            except common.FetchError as exc:
                common.log(f"movies: trending {media}: {exc}")
                break

    out = {"movie": {}, "tv": {}}
    for tab, (name, movie_f, tv_f) in TABS.items():
        for media, raw in (("movie", movie_f), ("tv", tv_f)):
            base = _resolve(tmdb, raw)
            if base is None:
                common.log(f"movies: {media}/{tab} keywords not found on TMDB; tab skipped")
                continue
            date_from = "primary_release_date.gte" if media == "movie" else "air_date.gte"
            date_to = "primary_release_date.lte" if media == "movie" else "air_date.lte"
            pools = {
                "all_time": _discover(tmdb, media, {**base, "sort_by": "vote_average.desc",
                                                   "vote_count.gte": MIN_VOTES[media]["all_time"]}, 3),
                "recent": _discover(tmdb, media, {**base, date_from: year_ago, date_to: today, "sort_by": "popularity.desc",
                                                 "vote_count.gte": MIN_VOTES[media]["recent"]}, 3),
                "trending": _discover(tmdb, media, {**base, "sort_by": "popularity.desc",
                                                   "vote_count.gte": MIN_VOTES[media]["trending"]}, 2),
            }
            # TMDB's weekly trending list, kept only where it matches this tab's discover results
            tab_ids = {t["id"] for p in pools.values() for t in p}
            week_rank = {t["id"]: i for i, t in enumerate(trending[media])}
            pools["trending"] = [t for t in trending[media] if t["id"] in tab_ids] + pools["trending"]

            lists = {}
            for ranking, pool in pools.items():
                seen, rows = set(), []
                for t in pool:
                    if t["id"] in seen or (t.get("vote_count") or 0) < MIN_VOTES[media][ranking]:
                        continue
                    seen.add(t["id"])
                    rows.append(t)
                if not rows:
                    lists[ranking] = []
                    continue
                prior = sum(t["vote_average"] for t in rows) / len(rows)
                max_votes = max(t["vote_count"] for t in rows)
                max_pop = max(t.get("popularity") or 0 for t in rows) or 1
                w = WEIGHTS[ranking]

                def score(t):
                    q = common.bayes_rating(t["vote_average"], t["vote_count"], prior, BAYES_M[media]) / 10
                    if ranking == "all_time":
                        p = common.log_scale(t["vote_count"], max_votes)
                    elif ranking == "trending":
                        p = 0.6 * common.log_scale(t.get("popularity") or 0, max_pop) + \
                            0.4 * (1 - week_rank[t["id"]] / 100 if t["id"] in week_rank else 0)
                    else:
                        p = common.log_scale(t.get("popularity") or 0, max_pop)
                    return w * q + (1 - w) * p

                rows.sort(key=score, reverse=True)
                lists[ranking] = [(score(t), t) for t in rows[: LIMIT[media]]]

            cards = {}
            for ranking, rows in lists.items():
                cards[ranking] = []
                for rank, (sc, t) in enumerate(rows, 1):
                    try:
                        d = tmdb.get(f"/{media}/{t['id']}", {"append_to_response": "videos,watch/providers"}, ttl_h=24 * 6)
                    except common.FetchError as exc:
                        common.log(f"movies: details {media} {t['id']}: {exc}")
                        continue
                    title = d.get("title") or d.get("name")
                    released = d.get("release_date") or d.get("first_air_date")
                    why = (f"TMDB {t['vote_average']:.1f}/10 from {t['vote_count']:,} votes (weighted for vote count)"
                           + {"all_time": "", "recent": f"; released {released}" if media == "movie"
                              else f"; episodes aired since {year_ago}",
                              "trending": f"; #{week_rank[t['id']] + 1} on TMDB's weekly trending {media} list"
                              if t["id"] in week_rank else "; high TMDB popularity this week"}[ranking] + ".")
                    card = {
                        "id": f"{media}-{t['id']}", "rank": rank, "title": title, "year": (released or "")[:4],
                        "released": released, "overview": d.get("overview"),
                        "poster": IMG + d["poster_path"] if d.get("poster_path") else None,
                        "genres": [g["name"] for g in d.get("genres") or []],
                        "rating": {"tmdb": t["vote_average"], "votes": t["vote_count"], "source": "TMDB"},
                        "score": round(sc, 4), "why": why, "trailer": _trailer(d.get("videos")),
                        "watch": _providers(d.get("watch/providers")),
                        "links": [{"name": "TMDB", "url": f"https://www.themoviedb.org/{media}/{t['id']}"}],
                    }
                    if media == "tv":
                        card["seasons_count"] = d.get("number_of_seasons")
                        card["status"] = d.get("status")
                        if ranking == "all_time" and rank <= 10 or rank <= 3:
                            card.update(_tv_extras(tmdb, d))
                    cards[ranking].append(card)
            out[media][tab] = {"name": name, **cards}
            common.log(f"movies {media}/{tab}: {len(cards['all_time'])}/{len(cards['recent'])}/{len(cards['trending'])}")

    # bound cache
    if len(state["tmdb"]) > 12000:
        state["tmdb"] = dict(list(state["tmdb"].items())[-12000:])
    common.save_json(STATE, state)
    common.log(f"movies: {tmdb.calls} TMDB calls")
    return {"movies": out["movie"], "tv": out["tv"], "watch_region": REGION, "sources": ["TMDB", "JustWatch (via TMDB)"]}
