"""Official YouTube videos for news reports and sports highlights.

YouTube's per-channel RSS feeds no longer work, so this uses the YouTube Data API
(key YOUTUBE_API_KEY) in the cheapest way: one playlistItems call (1 quota unit)
returns a channel's latest 50 uploads; matching to stories happens locally. Search
(100 units) is used only sparingly by callers, with results cached.

A video is attached only when its title clearly matches the item and it was
published close to the event -- never an unrelated clip to fill space.
"""
from __future__ import annotations

from datetime import timedelta

from . import common

API = "https://www.googleapis.com/youtube/v3"
CACHE = common.STATE / "youtube_cache.json"


class YouTube:
    def __init__(self):
        self.key = common.env("YOUTUBE_API_KEY")
        self.cache = common.load_json(CACHE, {"search": {}})
        self.units = 0

    @property
    def available(self) -> bool:
        return bool(self.key)

    def uploads(self, channel_id: str, pages: int = 1) -> list[dict]:
        """Latest uploads of an official channel: [{id, title, published, channel}]."""
        if not self.key:
            return []
        playlist = "UU" + channel_id[2:]
        out, token = [], None
        for _ in range(pages):
            params = {"part": "snippet", "playlistId": playlist, "maxResults": 50, "key": self.key}
            if token:
                params["pageToken"] = token
            try:
                data = common.get(f"{API}/playlistItems", params=params)
            except common.FetchError as exc:
                common.log(f"youtube uploads {channel_id}: {exc}")
                break
            self.units += 1
            for item in data.get("items", []):
                sn = item["snippet"]
                vid = sn.get("resourceId", {}).get("videoId")
                if vid and sn.get("title") not in ("Private video", "Deleted video"):
                    out.append({"id": vid, "title": sn["title"], "published": sn.get("publishedAt"),
                                "channel": sn.get("channelTitle"), "thumb": (sn.get("thumbnails", {}).get("high") or {}).get("url")})
            token = data.get("nextPageToken")
            if not token:
                break
        return out

    def search(self, query: str, channel_id: str | None = None, after=None, max_age_days: int = 30) -> list[dict]:
        """Cached search (100 units). Used only where uploads lists can't cover it."""
        if not self.key:
            return []
        key = f"{query}|{channel_id}"
        hit = self.cache["search"].get(key)
        if hit and common.within(common.parse_time(hit["at"]), max_age_days * 24):
            return hit["items"]
        params = {"part": "snippet", "q": query, "type": "video", "maxResults": 5, "key": self.key, "videoEmbeddable": "true"}
        if channel_id:
            params["channelId"] = channel_id
        if after:
            params["publishedAfter"] = common.iso(after)
        try:
            data = common.get(f"{API}/search", params=params)
        except common.FetchError as exc:
            common.log(f"youtube search: {exc}")
            return []
        self.units += 100
        items = [{"id": i["id"]["videoId"], "title": i["snippet"]["title"], "published": i["snippet"].get("publishedAt"),
                  "channel": i["snippet"].get("channelTitle"), "channel_id": i["snippet"].get("channelId"),
                  "thumb": (i["snippet"].get("thumbnails", {}).get("high") or {}).get("url")}
                 for i in data.get("items", []) if i.get("id", {}).get("videoId")]
        self.cache["search"][key] = {"at": common.iso(common.now_utc()), "items": items}
        return items

    def save(self) -> None:
        searches = self.cache["search"]
        if len(searches) > 3000:
            self.cache["search"] = dict(list(searches.items())[-3000:])
        common.save_json(CACHE, self.cache)


def best_match(title: str, when, videos: list[dict], min_similarity: float = 0.3, max_days: float = 3) -> dict | None:
    """The best title match published within `max_days` of `when` (None if nothing is close enough)."""
    best, best_score = None, min_similarity
    for v in videos:
        published = common.parse_time(v.get("published"))
        if when and published and abs(published - when) > timedelta(days=max_days):
            continue
        score = common.similarity(title, v["title"])
        if score > best_score:
            best, best_score = v, score
    return best


def embed(video: dict | None) -> dict | None:
    if not video:
        return None
    return {"youtube_id": video["id"], "title": video["title"], "channel": video.get("channel"),
            "published": video.get("published"), "url": f"https://www.youtube.com/watch?v={video['id']}"}
