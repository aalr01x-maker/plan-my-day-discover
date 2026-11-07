"""Movies & TV Shows from IMDb: by genre tab, three separate rankings (owner, 31 Oct 2026: IMDb only).

Which titles exist, their genres, years, ratings and votes all come from IMDb's official daily
datasets. TMDB is used only for each title's poster, overview, trailer and where to stream (matched
by IMDb id), and to say which titles are anime / superhero / experimental, labels IMDb's data lacks;
IMDb still rates and orders them. Every list shows titles rated 7.5+ only.

  Best of All Time   IMDb rating (vote-weighted), like IMDb's Top Rated
  Best Recent        released this year or last year (TV: premiered in the last 5 years with
                     episodes this year or last year); 75% rating + 25% votes
  Trending           IMDb votes gained per day over the last week (from our daily counts) 70%
                     + rating 30%; until a day of history exists, the most-voted recent titles

Airing This Week is one separate list, movies only: see airing.py. TMDB plays the same small part
there (poster, overview, trailer, streaming) and does not decide which movies are listed.
"""
from __future__ import annotations

from datetime import timedelta

from . import airing, common
from .imdb import IMDb

API = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p/w342"
STATE = common.STATE / "movies.json"
REGION = "KW"
FALLBACK_REGION = "US"

# tab -> (name, movie filter, tv filter). Filters are TMDB discover params; "kw:<word>"
# values are resolved to TMDB keyword ids at run time (no hard-coded guesses).
# Tabs from IMDb's genres. Animation is left out of live-action tabs. "member" tabs use a TMDB list
# only for membership (IMDb has no such label); anime is recognised by its Japanese original title
# on IMDb, plus TMDB's Japanese-original animation list for anime with English titles.
TABS = {
    "all": ("All", {"all": True}),   # every genre (owner: first tab)
    "action": ("Action", {"any": {"Action"}}),
    "adventure": ("Adventure", {"any": {"Adventure"}}),
    "anime": ("Anime", {"anime": True}),
    "comedy": ("Comedy", {"any": {"Comedy"}, "none": {"Documentary"}}),
    "crime-drama": ("Crime & Drama", {"any": {"Crime", "Drama"}, "none": {"Documentary"}}),
    "horror-thriller": ("Horror & Thriller", {"any": {"Horror", "Thriller"}}),
    "superhero": ("Superhero", {"member": ({"with_keywords": "kw:superhero"}, {"with_keywords": "kw:superhero"}),
                                "animation_ok": True}),
    "biography": ("Biography", {"any": {"Biography"}, "none": {"Documentary"}}),
    "scifi-fantasy": ("Sci-Fi & Fantasy", {"any": {"Sci-Fi", "Fantasy"}}),
    "romance": ("Romance", {"any": {"Romance"}}),
    "western-historical": ("Western & Historical", {"any": {"Western", "History", "War"}, "none": {"Documentary"}}),
    "documentary": ("Documentary", {"any": {"Documentary"}, "animation_ok": True}),
    "experimental": ("Experimental & Other", {"member": ({"with_keywords": "kw:experimental film|kw:surrealism|kw:avant-garde"},
                                                         {"with_keywords": "kw:surrealism|kw:anthology"})}),
    "classic": ("Classic / OG", {"classic": True, "animation_ok": True}),
    "adult-animation": ("Adult Animation", {"adult_animation": True,
                                            "member": ({"with_genres": "16", "with_keywords": "kw:adult animation"},
                                                       {"with_genres": "16", "with_keywords": "kw:adult animation"})}),
}
ANIME_TMDB = {"with_genres": "16", "with_original_language": "ja"}
# vote minimums on IMDb's scale; niche tabs need fewer, and every list steps them down (never the
# 7.5 rating floor) until it has 20 titles
IMDB_MIN_VOTES = {"movie": {"all_time": 25000, "recent": 1000, "trending": 500},
                  "tv": {"all_time": 5000, "recent": 300, "trending": 200}}
IMDB_BAYES_M = {"movie": 25000, "tv": 5000}
MIN_RATING = {"movie": 7.5, "tv": 7.5}
NICHE = {"anime", "experimental", "documentary", "adult-animation"}
NICHE_VOTES = {"recent": {"movie": 200, "tv": 100}, "all_time": {"movie": 5000, "tv": 1000}}
LADDER = (1.0, 0.5, 0.2, 0.08, 0.03)
FLOOR_VOTES = 50
TRACK_VOTES = 1000   # titles whose daily vote counts are kept for Trending


def _min_votes(media: str, ranking: str, tab: str, factor: float = 1.0) -> int:
    base = NICHE_VOTES[ranking][media] if tab in NICHE and ranking in NICHE_VOTES else IMDB_MIN_VOTES[media][ranking]
    return max(FLOOR_VOTES, int(base * factor)) if factor < 1 else base


NO_RECENT = {"classic"}   # pre-1990 / pre-2000 by definition: no releases in the last 12 months
LIMIT = {"movie": 20, "tv": 20}
WEIGHTS = {"all_time": 1.0, "recent": 0.75, "trending": 0.30}   # all time: rating only, like IMDb Top Rated (owner)


DETAIL_FIELDS = ("id", "title", "name", "release_date", "first_air_date", "overview", "poster_path", "genres",
                 "number_of_seasons", "status", "seasons", "videos", "watch/providers")
EPISODE_FIELDS = ("episode_number", "name", "vote_average", "vote_count", "air_date")


def _slim(path: str, data: dict) -> dict:
    """Keep only the fields the site uses (full TMDB responses carry cast/crew and are huge)."""
    if path.startswith("/find/"):
        return {k: [{"id": r.get("id")} for r in data.get(k) or []][:1] for k in ("movie_results", "tv_results")}
    if path.endswith("/external_ids"):
        return {"imdb_id": data.get("imdb_id")}
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


def _imdb_id(tmdb: TMDB, media: str, tid: int) -> str | None:
    try:
        return tmdb.get(f"/{media}/{tid}/external_ids", ttl_h=24 * 90).get("imdb_id")
    except common.FetchError:
        return None


def _imdb_extras(tmdb: TMDB, show: dict, eps: list[dict]) -> dict | None:
    """Season averages and best episodes from IMDb ratings; names from TMDB's season lists."""
    if not eps:
        return None
    seasons = []
    for n in sorted({e["season"] for e in eps if e["season"] > 0}):
        rated = [e for e in eps if e["season"] == n and e["votes"] >= 50]
        if len(rated) >= 2:
            seasons.append({"season": n, "rating": round(sum(e["rating"] for e in rated) / len(rated), 2),
                            "rated_episodes": len(rated), "year": ""})
    pool = [e for e in eps if e["season"] > 0 and e["votes"] >= 100]
    if not pool:
        return {"seasons": seasons, "top_episodes": [], "ratings_source": "IMDb"} if seasons else None
    mean = sum(e["rating"] for e in pool) / len(pool)
    pool.sort(key=lambda e: (-common.bayes_rating(e["rating"], e["votes"], mean, 1000), -e["votes"]))
    top = []
    for e in pool[:5]:
        name, aired = None, None
        try:
            season = tmdb.get(f"/tv/{show['id']}/season/{e['season']}", ttl_h=24 * 14)
            match = next((x for x in season.get("episodes") or [] if x.get("episode_number") == e["episode"]), None)
            if match:
                name, aired = match.get("name"), match.get("air_date")
        except common.FetchError:
            pass
        top.append({"season": e["season"], "episode": e["episode"], "name": name or f"Episode {e['episode']}",
                    "rating": e["rating"], "votes": e["votes"], "aired": aired,
                    "url": f"https://www.imdb.com/title/{e['tconst']}/"})
    years = {s.get("season_number"): (s.get("air_date") or "")[:4] for s in show.get("seasons") or []}
    for s in seasons:
        s["year"] = years.get(s["season"], "")
    return {"seasons": seasons, "top_episodes": top, "ratings_source": "IMDb"}


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


def _tmdb_id(tmdb: TMDB, iid: str, media: str) -> int | None:
    """TMDB's id for an IMDb title (for its poster, trailer and streaming), cached 90 days."""
    try:
        data = tmdb.get(f"/find/{iid}", {"external_source": "imdb_id"}, ttl_h=24 * 90)
    except common.FetchError:
        return None
    hits = data.get("movie_results" if media == "movie" else "tv_results") or []
    return hits[0]["id"] if hits else None


def _members(tmdb: TMDB, media: str, raw: dict, pages: int = 10) -> set[str]:
    """IMDb ids of the titles on a TMDB list (membership only: anime, superhero, experimental)."""
    params = _resolve(tmdb, raw)
    if params is None:
        return set()
    ids = set()
    # the most-voted titles plus what's popular now (so new releases are included too)
    seen = {t["id"]: t for sort in ("vote_count.desc", "popularity.desc")
            for t in _discover(tmdb, media, {**params, "sort_by": sort}, pages if sort == "vote_count.desc" else 5)}
    for t in seen.values():
        iid = _imdb_id(tmdb, media, t["id"])
        if iid:
            ids.add(iid)
    return ids


def _airing_card(tmdb: TMDB, ratings: dict, titles: dict, row: dict) -> dict:
    iid, d = row.get("imdb"), {}
    tid = _tmdb_id(tmdb, iid, "movie") if iid else None
    if tid:
        try:
            d = tmdb.get(f"/movie/{tid}", {"append_to_response": "videos,watch/providers"}, ttl_h=24)
        except common.FetchError as exc:
            common.log(f"movies: airing details {tid}: {exc}")
    rating, votes = ratings.get(iid) or (None, 0)
    genres = titles.get(iid, {}).get("genres") or {g["name"] for g in d.get("genres") or []}
    wiki = row.get("page") or airing.page_name(int(row["date"][:4]))
    return {"id": f"airing-{iid or row['date'] + '-' + row['title']}", "airing": True, "title": row["title"], "year": row["date"][:4],
            "released": row["date"], "overview": d.get("overview") or "",
            "poster": IMG + d["poster_path"] if d.get("poster_path") else None, "genres": sorted(genres),
            "rating": {"value": rating, "votes": votes, "source": "IMDb"},
            "why": f"Opens {row['date']} on Wikipedia's list of American film releases.",
            "trailer": _trailer(d.get("videos")), "watch": _providers(d.get("watch/providers")),
            "links": ([{"name": "IMDb", "url": f"https://www.imdb.com/title/{iid}/"}] if iid else [])
                     + [{"name": "Wikipedia", "url": "https://en.wikipedia.org/wiki/" + wiki.replace(" ", "_")}]}


def _airing_rows(state: dict, first_day) -> list[dict]:
    """This week's films; if Wikipedia can't be read, what's left of the last list that was."""
    try:
        rows = airing.this_week(first_day)
        state["airing_last"] = rows
        return rows
    except (common.FetchError, ValueError, KeyError, TypeError) as exc:
        common.log(f"movies: airing list unavailable: {exc}")
        return airing.in_window(state.get("airing_last") or [], first_day)


def build() -> dict:
    token = common.env("TMDB_TOKEN")
    if not token:
        raise RuntimeError("TMDB_TOKEN not set")
    now = common.now_utc()
    this_year = now.year
    today = now.strftime("%Y-%m-%d")
    state = common.load_json(STATE, {"tmdb": {}})
    tmdb = TMDB(token, state["tmdb"])
    imdb = IMDb()
    if not imdb.available:
        raise RuntimeError("IMDb ratings unavailable")
    from .imdb import titles as imdb_titles
    titles, latest_episode = imdb_titles(imdb.ratings, FLOOR_VOTES, this_year - 1)

    # daily vote counts for Trending (votes gained per day), last 8 days
    history = state.setdefault("votes", {})
    history[today] = {i: imdb.ratings[i][1] for i, t in titles.items()
                      if imdb.ratings[i][1] >= TRACK_VOTES and imdb.ratings[i][0] >= MIN_RATING[t["type"]]}
    for day in sorted(history)[:-8]:
        del history[day]
    past = [d for d in sorted(history) if d < today]
    base_day = past[0] if past else None

    def gain(iid):   # votes gained per day since the oldest kept day
        if not base_day or iid not in history[base_day] or iid not in history[today]:
            return 0.0
        days = max(1, (now.date() - common.parse_time(base_day + "T00:00:00Z").date()).days)
        return max(0, history[today][iid] - history[base_day][iid]) / days

    anime_ids = {m: _members(tmdb, m, ANIME_TMDB, 15) for m in ("movie", "tv")}
    tv_extras_todo = []
    by_media = {m: [(i, t) for i, t in titles.items() if t["type"] == m] for m in ("movie", "tv")}

    out = {"movie": {}, "tv": {}}
    for tab, (name, rule) in TABS.items():
        for mi, media in enumerate(("movie", "tv")):
            members = _members(tmdb, media, rule["member"][mi]) if "member" in rule else None

            def belongs(iid, t):
                if rule.get("all"):
                    return True
                g = t["genres"]
                anime = t["japanese"] or iid in anime_ids[media]
                if rule.get("anime"):
                    return "Animation" in g and anime
                if rule.get("adult_animation"):   # IMDb has no "adult" label for animation: TMDB's list decides
                    return "Animation" in g and not anime and "Family" not in g and iid in (members or set())
                if "Animation" in g and not rule.get("animation_ok"):
                    return False
                if members is not None:
                    return iid in members
                if rule.get("classic"):
                    return t["year"] <= (1989 if media == "movie" else 1999)
                return bool(g & rule["any"]) and not (g & rule.get("none", set()))

            pool = [(i, t) for i, t in by_media[media] if belongs(i, t)]

            def recent(i, t):
                if media == "movie":
                    return t["year"] >= this_year - 1
                return t["year"] >= this_year - 5 and latest_episode.get(i, 0) >= this_year - 1

            lists, relaxed = {}, {}
            for ranking in ("all_time", "recent", "trending"):
                if ranking == "recent" and tab in NO_RECENT:
                    lists[ranking] = []
                    continue
                rows = []
                for factor in LADDER:
                    need = _min_votes(media, ranking, tab, factor)
                    rows = [(i, t) for i, t in pool if imdb.ratings[i][1] >= need and imdb.ratings[i][0] >= MIN_RATING[media]
                            and (ranking != "recent" or recent(i, t))]
                    if ranking == "trending" and base_day:
                        rows = [(i, t) for i, t in rows if gain(i) > 0]
                    elif ranking == "trending":   # no history yet: recent titles stand in
                        rows = [(i, t) for i, t in rows if t["year"] >= this_year - 2 or latest_episode.get(i, 0) >= this_year - 1]
                    if len(rows) >= LIMIT[media]:
                        break
                if factor != LADDER[0] and rows:
                    relaxed[ranking] = need
                if not rows:
                    lists[ranking] = []
                    continue
                prior = sum(imdb.ratings[i][0] for i, _ in rows) / len(rows)
                max_votes = max(imdb.ratings[i][1] for i, _ in rows)
                max_gain = max((gain(i) for i, _ in rows), default=0) or 1
                w = WEIGHTS[ranking]

                def score(row):
                    rating, votes = imdb.ratings[row[0]]
                    q = common.bayes_rating(rating, votes, prior, IMDB_BAYES_M[media]) / 10
                    if ranking == "trending" and base_day:
                        p = common.log_scale(gain(row[0]), max_gain)
                    else:
                        p = common.log_scale(votes, max_votes)
                    return w * q + (1 - w) * p

                rows.sort(key=score, reverse=True)
                lists[ranking] = [(score(r), r) for r in rows[: LIMIT[media]]]

            cards = {}
            for ranking, rows in lists.items():
                cards[ranking] = []
                for rank, (sc, (iid, t)) in enumerate(rows, 1):
                    rating, votes = imdb.ratings[iid]
                    tid = _tmdb_id(tmdb, iid, media)
                    d = {}
                    if tid:
                        try:
                            d = tmdb.get(f"/{media}/{tid}", {"append_to_response": "videos,watch/providers"}, ttl_h=24 * 6)
                        except common.FetchError as exc:
                            common.log(f"movies: details {media} {tid}: {exc}")
                    released = d.get("release_date") or d.get("first_air_date") or str(t["year"])
                    why = (f"IMDb {rating:.1f}/10 from {votes:,} votes (weighted for vote count)"
                           + {"all_time": "",
                              "recent": f"; released {released}" if media == "movie"
                              else f"; premiered {released}, new episodes in {latest_episode.get(iid, '')}",
                              "trending": f"; +{gain(iid):,.0f} IMDb votes a day this week" if base_day
                              else "; one of the most-voted recent titles"}[ranking] + ".")
                    card = {
                        "id": f"{media}-{iid}", "rank": rank, "title": t["title"], "year": str(t["year"]),
                        "released": released, "overview": d.get("overview"),
                        "poster": IMG + d["poster_path"] if d.get("poster_path") else None,
                        "genres": sorted(t["genres"]),
                        "rating": {"value": rating, "votes": votes, "source": "IMDb"},
                        "score": round(sc, 4), "why": why, "trailer": _trailer(d.get("videos")),
                        "watch": _providers(d.get("watch/providers")),
                        "links": [{"name": "IMDb", "url": f"https://www.imdb.com/title/{iid}/"}]
                                 + ([{"name": "TMDB", "url": f"https://www.themoviedb.org/{media}/{tid}"}] if tid else []),
                    }
                    if media == "tv":
                        card["seasons_count"] = d.get("number_of_seasons")
                        card["status"] = d.get("status")
                        if d and (ranking == "all_time" and rank <= 10 or rank <= 3):
                            tv_extras_todo.append((card, d, iid))
                    cards[ranking].append(card)

            notes = {}
            kind = "movies" if media == "movie" else "shows"
            if tab in NO_RECENT:
                notes["recent"] = "Classics are older titles by definition, so there are no recent releases here. Try Best of All Time or Trending."
            if not base_day:
                notes["trending"] = ("Trending measures IMDb votes gained per day; it starts once a day of counts exists. "
                                     "Until then it shows the most-voted recent titles.")
            for ranking, what in (("all_time", "of all time"), ("recent", "from this year or last year"), ("trending", "trending now")):
                if ranking in cards and len(cards[ranking]) < LIMIT[media] and not (ranking == "recent" and tab in NO_RECENT):
                    msg = (f"Only {len(cards[ranking])} {name} {kind} {what} are rated {MIN_RATING[media]:.1f}+ on IMDb, "
                           f"so the list is shorter instead of padded with weaker titles.")
                    notes[ranking] = (notes[ranking] + " " + msg) if ranking in notes else msg
            for ranking, n in relaxed.items():
                extra = f"To fill the list it includes less-known titles (at least {n:,} IMDb votes); ratings are still weighted by vote count."
                notes[ranking] = (notes[ranking] + " " + extra) if ranking in notes else extra
            out[media][tab] = {"name": name, **cards, "notes": notes}
            common.log(f"movies {media}/{tab}: {len(pool)} on IMDb; {len(cards['all_time'])}/{len(cards['recent'])}/{len(cards['trending'])}")

    airing_items = [_airing_card(tmdb, imdb.ratings, titles, row) for row in _airing_rows(state, now.date())]
    common.log(f"movies: {len(airing_items)} airing this week")

    # TV season ratings and best episodes: IMDb (one pass over its episode file), else TMDB
    episodes = imdb.episodes({i for _, _, i in tv_extras_todo if i})
    for card, show, iid in tv_extras_todo:
        card.update(_imdb_extras(tmdb, show, episodes.get(iid) or []) or _tv_extras(tmdb, show))

    if len(state["tmdb"]) > 12000:
        state["tmdb"] = dict(list(state["tmdb"].items())[-12000:])
    common.save_json(STATE, state)
    common.log(f"movies: {tmdb.calls} TMDB calls")
    return {"movies": out["movie"], "tv": out["tv"], "airing": airing_items, "watch_region": REGION,
            "sources": ["IMDb (titles, genres, ratings, votes)", "TMDB (posters, trailers, anime/superhero lists)",
                        "JustWatch (via TMDB)", "Wikipedia + Wikidata (Airing This Week dates and IMDb ids)"]}
