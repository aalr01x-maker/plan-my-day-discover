"""Live scores, every 15 minutes (owner, 9 Nov 2026).

Writes site/data/scores.json: every ESPN game or event that started in the last 30 hours, with its real
state (live with the game clock, final with the score, or postponed). ESPN refuses requests from a
browser, so the site can't ask ESPN itself; this small job runs on GitHub (free for a public repo) and
the site reads the file. The daily run's sports.json stays the source for everything still to come.

    python -m pipeline.scores            -> writes site/data/scores.json, prints "changed" or "same"
"""
from __future__ import annotations

import json
import sys

from . import common, upcoming


def build(now=None) -> dict:
    now = now or common.now_utc()
    games, failed = [], []
    for league, icon, source, ref, kind in upcoming.LEAGUES:
        if source != "espn":
            continue
        try:
            got = upcoming._espn_recent(league, ref, now)
        except Exception as exc:   # one league failing never blanks the others
            common.log(f"scores {league}: {exc}")
            failed.append(league)
            continue
        games += [{**g, "icon": icon, "group": upcoming._group(league, ref)} for g in got]
    return {"built_at": common.iso(now), "games": games, "failed": failed}


def main() -> int:
    out = build()
    path = common.SITE_DATA / "scores.json"
    old = None
    try:
        old = json.loads(path.read_text())
    except Exception:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    same = old is not None and old.get("games") == out["games"]
    print("same" if same else "changed")
    common.log(f"scores: {len(out['games'])} games, {sum(1 for g in out['games'] if not g['done'])} live, failed={out['failed']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
