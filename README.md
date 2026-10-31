# Plan My Day Discover

Static discovery site (https://plan-my-day-discover.web.app): News, Movies & TV, Gaming, Music, Sports,
rebuilt every day at 12:00 Kuwait time (09:00 UTC) by GitHub Actions. Fully separate from the Planner:
own repo, own Firebase Hosting site (`plan-my-day-discover`), no Firestore / Functions / user data.

## Layout
- `pipeline/` – Python 3.12 builders, one per section (`news.py`, `movies.py`, `games.py`, `music.py`, `sports.py`),
  plus `ai.py` (Claude Haiku 5.5, hard $15/month cap), `youtube.py`, `geo.py`, `common.py`.
- `python -m pipeline [sections…]` builds, validates and writes `site/data/*.json`. A failed or invalid section keeps
  its last valid dataset (`state/last/`) and is marked stale on the site.
- `site/` – the SPA (`index.html`, `app.js`, `style.css`). Deep links: `#/news/africa/week`, `#/news/business`,
  `#/movies/tv/anime/trending`, `#/gaming/rpg/recent?f=indie`, `#/music/retrowave/all_time`,
  `#/sports/soccer/results?league=Premier%20League`. Add `?item=<id>` to open one item.
- `state/` – caches, listen/player history, AI ledger; persisted on the `state` branch (never published).

## Secrets (GitHub → Settings → Secrets and variables → Actions)
| Secret | Used for | Without it |
|---|---|---|
| `TMDB_TOKEN` | Movies & TV (TMDB "API Read Access Token") | section stays on last data / empty |
| `YOUTUBE_API_KEY` | sports highlights, news/music/game videos | no videos (TMDB trailers still work) |
| `RAWG_API_KEY` | console platforms (PS5), extra critic scores | Steam data only |
| `LASTFM_API_KEY` | extra music candidates | ListenBrainz/MusicBrainz only |
| `ANTHROPIC_API_KEY` | news summaries + significance | source snippets + keyword ranking |
| `FIREBASE_SERVICE_ACCOUNT` | deploy (JSON key, role Firebase Hosting Admin) | build runs, no deploy |

Run manually: Actions → Daily refresh → Run workflow (optionally list sections).
