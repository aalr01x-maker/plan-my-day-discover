"""IMDb ratings from IMDb's official daily datasets (https://datasets.imdbws.com).

IMDb has no free API, but publishes these files daily for personal, non-commercial
use, which this site is. TMDB still finds titles and supplies posters, trailers and
streaming; IMDb supplies the ratings people know (far more votes than TMDB):

  title.ratings.tsv.gz   rating + vote count for every title and episode (~9 MB)
  title.episode.tsv.gz   episode -> show, season and episode number (~55 MB)

Both are streamed once per run; nothing is stored between runs.
"""
from __future__ import annotations

import gzip
import io

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
