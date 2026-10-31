"""IMDb ratings from IMDb's official daily datasets (https://datasets.imdbws.com).

IMDb has no free API, but publishes these files daily for personal, non-commercial
use, which this site is. TMDB still finds titles and supplies posters, trailers and
streaming; IMDb supplies the ratings people know (far more votes than TMDB):

  title.ratings.tsv.gz   rating + vote count for every title and episode (~9 MB)
  title.basics.tsv.gz    type, titles, years and genres of every title (~230 MB)
  title.episode.tsv.gz   episode -> show, season and episode number (~55 MB)

Each is streamed once per run; nothing is stored between runs.
"""
from __future__ import annotations

import gzip
import io
import re
import unicodedata

import requests

from . import common

BASE = "https://datasets.imdbws.com"
MIN_VOTES = 20          # ignore near-empty entries (keeps memory small)


def _lines(name: str):
    r = requests.get(f"{BASE}/{name}", headers=common.UA, timeout=300, stream=True)
    r.raise_for_status()
    with gzip.GzipFile(fileobj=r.raw) as gz:
        reader = io.TextIOWrapper(gz, encoding="utf-8")
        next(reader)  # header
        for line in reader:
            yield line.rstrip("\n").split("\t")


class IMDb:
    def __init__(self):
        self.ratings: dict[str, tuple[float, int]] = {}
        try:
            for tconst, rating, votes in _lines("title.ratings.tsv.gz"):
                v = int(votes)
                if v >= MIN_VOTES:
                    self.ratings[tconst] = (float(rating), v)
            common.log(f"imdb: {len(self.ratings):,} rated titles")
        except (requests.RequestException, OSError, ValueError) as exc:
            common.log(f"imdb: ratings unavailable ({exc}); TMDB ratings are used instead")
            self.ratings = {}

    @property
    def available(self) -> bool:
        return bool(self.ratings)

    def rating(self, tconst: str | None) -> tuple[float, int] | None:
        return self.ratings.get(tconst) if tconst else None

    def episodes(self, shows: set[str]) -> dict[str, list[dict]]:
        """show tconst -> [{season, episode, rating, votes}] for rated episodes."""
        out: dict[str, list[dict]] = {s: [] for s in shows}
        if not shows or not self.ratings:
            return out
        try:
            for tconst, parent, season, episode in _lines("title.episode.tsv.gz"):
                if parent in out and season.isdigit() and episode.isdigit():
                    r = self.ratings.get(tconst)
                    if r:
                        out[parent].append({"season": int(season), "episode": int(episode), "tconst": tconst,
                                            "rating": r[0], "votes": r[1]})
        except (requests.RequestException, OSError, ValueError) as exc:
            common.log(f"imdb: episode list unavailable ({exc})")
        return out


# Anime: IMDb has no "anime" genre, but an anime's original title is Japanese in Hepburn romaji
# ("Shingeki no kyojin" for Attack on Titan, "Sen to Chihiro no kamikakushi" for Spirited Away),
# unlike Toy Story or Frozen, whose original titles are English.
_SYLLABLE = re.compile(r"^(?:(?:ky|gy|sh|ch|ny|hy|by|py|my|ry|j|ts|[kgsztdnhbpmyrwfv])?[aiueo]|n)+$")
PARTICLES = {"no", "wa", "ga", "to", "ni", "de", "wo", "o", "e", "mo", "ka", "na"}


def _plain(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def looks_japanese(original: str, primary: str) -> bool:
    words = [w for w in re.split(r"[^a-z]+", _plain(original)) if w]
    if not words:
        return False
    # double consonants (small tsu: "Shippuden", "bibappu") and "tch" read as single ones
    romaji = sum(1 for w in words if _SYLLABLE.match(re.sub(r"([bcdfghjkmprstvwz])\1", r"\1", w.replace("tch", "ch")).replace("nn", "n")))
    if romaji / len(words) < 0.6:
        return False
    # a different original title, or unmistakably Japanese (long-vowel marks or particles)
    return original.strip().lower() != primary.strip().lower() or bool(re.search(r"[ôûâêî]", original)) \
        or (len(words) >= 2 and any(w in PARTICLES for w in words))


SKIP_GENRES = {"Adult", "Talk-Show", "Reality-TV", "Game-Show", "News", "Short"}


def titles(ratings: dict[str, tuple[float, int]], min_votes: int, recent_from_year: int) -> tuple[dict, dict]:
    """Rated movies and series from IMDb's title list.

    Returns ({tconst: {type, title, year, end, genres, japanese}}, {series tconst: latest episode year})
    for titles with at least `min_votes` votes. The second map only covers episodes from
    `recent_from_year` on (for "episodes in the last 12 months")."""
    out, recent_eps = {}, {}
    for row in _lines("title.basics.tsv.gz"):
        if len(row) < 9:
            continue
        tconst, kind, primary, original, adult, start, end, _, genres = row[:9]
        if kind == "tvEpisode":
            if start.isdigit() and int(start) >= recent_from_year:
                recent_eps[tconst] = int(start)
            continue
        if kind not in ("movie", "tvSeries", "tvMiniSeries") or adult == "1":
            continue
        r = ratings.get(tconst)
        if not r or r[1] < min_votes or not start.isdigit():
            continue
        g = set(genres.split(",")) if genres != "\\N" else set()
        if g & SKIP_GENRES:
            continue
        out[tconst] = {"type": "movie" if kind == "movie" else "tv", "title": primary, "year": int(start),
                       "end": int(end) if end.isdigit() else None, "genres": g,
                       "japanese": "Animation" in g and looks_japanese(original, primary)}
    latest = {}
    for tconst, parent, _, _ in _lines("title.episode.tsv.gz"):
        y = recent_eps.get(tconst)
        if y and parent in out:
            latest[parent] = max(latest.get(parent, 0), y)
    common.log(f"imdb: {len(out):,} rated movies and series; {len(latest):,} series with recent episodes")
    return out, latest
