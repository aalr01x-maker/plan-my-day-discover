"""The daily "your picks are ready" email (owner, 2 Nov 2026): a short, friendly newsletter with one
highlight per section and a link -- nothing about accounts or payment. Printed as Markdown (the body of
a GitHub issue, which GitHub emails to the owner); the first line is the subject.

  python scripts/daily_email.py > note.md   (run after the datasets are built)
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

SITE = "https://plan-my-day-discover.web.app"
DATA = Path(__file__).resolve().parent.parent / "site" / "data"
KUWAIT = timezone(timedelta(hours=3))


def load(name: str) -> dict:
    try:
        return json.loads((DATA / f"{name}.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def first(lst):
    return lst[0] if lst else None


def when(iso: str | None) -> str:
    try:
        t = datetime.fromisoformat((iso or "").replace("Z", "+00:00")).astimezone(KUWAIT)
        return t.strftime("%a %d %b, %H:%M") + " Kuwait"
    except ValueError:
        return ""


def main() -> None:
    now = datetime.now(KUWAIT)
    lines: list[str] = []

    news = (load("news").get("regions") or {}).get("world") or {}
    top = first(news.get("today") or [])
    if top:
        lines.append(f"📰 **Top story today:** {top.get('headline')}  \n[See the top 10 world stories →]({SITE}/#/news/world/today)")

    tv = ((load("movies").get("tv") or {}).get("all") or {})
    show = first(tv.get("trending") or [])
    if show:
        r = show.get("rating") or {}
        lines.append(f"📺 **Trending show:** {show.get('title')} (IMDb {r.get('value')})  \n"
                     f"[What everyone's watching →]({SITE}/#/movies/tv/all/trending)")

    games = ((load("games").get("genres") or {}).get("all") or {})
    game = first(games.get("trending") or [])
    if game:
        live = game.get("players_now")
        lines.append(f"🎮 **Trending game:** {game.get('title')}" + (f" ({live:,} playing right now)" if live else "")
                     + f"  \n[Top games today →]({SITE}/#/gaming/all/trending)")

    music = ((load("music").get("genres") or {}).get("all") or {})
    song = first(music.get("recent") or []) or first(music.get("trending") or [])
    if song:
        lines.append(f"🎵 **Song of the moment:** {song.get('title')} by {song.get('artist')}  \n"
                     f"[Hear what's climbing →]({SITE}/#/music/all/recent)")

    sports = load("sports")
    events = sorted(((sports.get("upcoming") or {}).get("events") or []), key=lambda e: e.get("when") or "")
    soon = [e for e in events if (e.get("when") or "") >= datetime.now(timezone.utc).isoformat()[:16]]
    ev = first(soon) or first(events)
    if ev:
        lines.append(f"🏆 **Coming up:** {ev.get('icon', '')} {ev.get('league')}: {ev.get('title')}, {when(ev.get('when'))}  \n"
                     f"[All the big games this week →]({SITE}/#/sports/upcoming)")

    print(f"✨ Your Discover picks for {now:%A %d %B} are ready")
    print()
    print(f"Good afternoon! 👋 Today's lists are fresh: news, shows, games, music and sports, all updated at noon.")
    print()
    print("\n\n".join(lines) if lines else "Everything has been refreshed for today.")
    print()
    print(f"👉 [Open Discover]({SITE})")
    print()
    print("Enjoy your day! 🌟")


if __name__ == "__main__":
    main()
