"""Daily refresh: python -m pipeline [section ...]

Each section is built independently. A section's new data is published only if it
passes validation; otherwise the last valid dataset stays (kept in state/last/,
which persists between runs) and meta.json marks it stale with the reason.
Nothing here runs when someone visits the site -- it's all precomputed.
"""
from __future__ import annotations

import shutil
import sys
import traceback

from . import common, games, movies, music, news, sports
from .ai import AI
from .youtube import YouTube

LAST = common.STATE / "last"
ORDER = ["news", "movies", "games", "sports", "music"]  # sports first: its few YouTube searches must not wait on music's daily budget


def _count(data: dict, section: str) -> int:
    if section == "news":
        return sum(len(r["today"]) + len(r["week"]) + len(r.get("month", [])) for r in data["regions"].values())
    if section == "movies":
        return sum(len(t[k]) for media in ("movies", "tv") for t in data[media].values() for k in ("all_time", "recent", "trending"))
    if section in ("games", "music"):
        return sum(len(g["all_time"]) for g in data["genres"].values())
    if section == "sports":
        return sum(len(s["results"]) + len(s["latest"]) for s in data["sports"].values())
    return 0


MINIMUM = {"news": 30, "movies": 100, "games": 60, "music": 16, "sports": 40}  # music: YouTube lookups fill over the first days


def main(argv: list[str]) -> int:
    wanted = [s for s in argv if s in ORDER] or ORDER
    ai, yt = AI(), YouTube()
    meta = common.load_json(LAST / "meta.json", {"sections": {}})
    started = common.now_utc()
    builders = {
        "news": lambda: news.build(ai, yt),
        "movies": movies.build,
        "games": lambda: games.build(yt),
        "music": lambda: music.build(yt),
        "sports": lambda: sports.build(ai, yt),
    }
    failures = 0
    for section in wanted:
        entry = meta["sections"].setdefault(section, {})
        entry["last_attempt"] = common.iso(common.now_utc())
        try:
            data = builders[section]()
            n = _count(data, section)
            if n < MINIMUM[section]:
                raise RuntimeError(f"validation failed: only {n} items (minimum {MINIMUM[section]})")
            data["generated_at"] = common.iso(common.now_utc())
            common.save_json(LAST / f"{section}.json", data)
            entry.update({"updated_at": data["generated_at"], "ok": True, "error": None, "items": n})
            common.log(f"{section}: OK ({n} items)")
        except Exception as exc:  # keep going: one broken source must not stop the others
            failures += 1
            entry.update({"ok": False, "error": str(exc)[:300]})
            common.log(f"{section}: FAILED -- {exc}; keeping the last valid data")
            traceback.print_exc()
        finally:
            ai.save()
            yt.save()
    meta["run"] = {"started": common.iso(started), "finished": common.iso(common.now_utc()),
                   "ai": ai.status(), "youtube_units": yt.units, "failures": failures}
    meta["order"] = ORDER
    meta["schedule"] = {"tz": "Asia/Kuwait", "hour": 12}   # fixed daily update time, shown on the site
    common.save_json(LAST / "meta.json", meta)

    # publish: copy every last-valid dataset into site/data (each file replaced atomically)
    common.SITE_DATA.mkdir(parents=True, exist_ok=True)
    for f in LAST.glob("*.json"):
        tmp = common.SITE_DATA / (f.name + ".tmp")
        shutil.copyfile(f, tmp)
        tmp.replace(common.SITE_DATA / f.name)
    common.save_json(common.STATE / "last_run_log.json", common.run_log())
    # fail the workflow only when nothing at all could be published
    published = [s for s in ORDER if (LAST / f"{s}.json").exists()]
    return 0 if published else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
