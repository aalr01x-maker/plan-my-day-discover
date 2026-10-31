"""Daily update at 12:00 noon in the owner's time zone.

GitHub's cron runs in UTC, so refresh.yml has one trigger per hour and runs only on
the one stored in repo variable UPDATE_CRON. This script works that value out:

  python scripts/schedule.py valid  Europe/London            -> exit 0 if a real IANA zone
  python scripts/schedule.py cron   Europe/London [--tomorrow] -> "0 11 * * *"
  python scripts/schedule.py due    Europe/London meta.json  -> "yes" if today's noon there
                                                                has passed with no update since

Zones with a half-hour offset (India, parts of Australia) round to the nearest hour,
because the triggers are on the hour.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

NAME = re.compile(r"^[A-Za-z_]+(/[A-Za-z0-9_+\-]+){0,2}$")
NOON = 12


def zone(name: str) -> ZoneInfo:
    if not NAME.match(name or ""):
        raise ValueError(f"not a time zone name: {name!r}")
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        raise ValueError(f"unknown time zone: {name!r}") from None


def noon_utc(name: str, day_offset: int = 0, now: datetime | None = None) -> datetime:
    tz = zone(name)
    local = (now or datetime.now(timezone.utc)).astimezone(tz) + timedelta(days=day_offset)
    noon = local.replace(hour=NOON, minute=0, second=0, microsecond=0)
    return noon.astimezone(timezone.utc)


def cron(name: str, tomorrow: bool = False, now: datetime | None = None) -> str:
    utc = noon_utc(name, 1 if tomorrow else 0, now)
    hour = (utc + timedelta(minutes=30)).hour if utc.minute >= 30 else utc.hour
    return f"0 {hour} * * *"


def due(name: str, meta: dict, now: datetime | None = None) -> bool:
    """Today's noon in that zone has passed and no section was updated after it."""
    now = now or datetime.now(timezone.utc)
    noon = noon_utc(name, 0, now)
    if now < noon:
        return False
    times = [datetime.fromisoformat(m["updated_at"]) for m in (meta.get("sections") or {}).values() if m.get("updated_at")]
    return not times or max(times) < noon


def main(argv: list[str]) -> int:
    cmd, name = argv[0], argv[1]
    try:
        if cmd == "valid":
            zone(name)
        elif cmd == "cron":
            print(cron(name, "--tomorrow" in argv))
        elif cmd == "due":
            with open(argv[2]) as f:
                print("yes" if due(name, json.load(f)) else "no")
        else:
            raise ValueError(f"unknown command {cmd}")
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
