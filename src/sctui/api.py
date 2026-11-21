"""Small client for SoundCloud's web API (api-v2): feed, likes, playlists.

Uses the same public client_id the soundcloud.com web app uses, plus your
OAuth token for account endpoints.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .source import Track

API = "https://api-v2.soundcloud.com"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "sctui"
CLIENT_ID_FILE = CACHE_DIR / "client_id"


class APIError(RuntimeError):
    pass


def _http(url: str, headers: dict | None = None, timeout: float = 15) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def artwork_url(raw: str | None) -> str | None:
    """SoundCloud artwork URLs default to 100x100 ("-large"); ask for 500x500."""
    return raw.replace("-large.", "-t500x500.") if raw else None


def to_track(obj: dict, repost_by: str | None = None) -> Track:
    is_playlist = obj.get("kind") == "playlist"
    meta = obj.get("publisher_metadata") or {}
    user = obj.get("user") or {}
    dur = obj.get("duration")
    return Track(
        url=obj.get("permalink_url") or "",
        title=obj.get("title") or "?",
        artist=(meta.get("artist") if not is_playlist else None) or user.get("username") or "",
        duration=dur / 1000 if dur else None,
        kind="playlist" if is_playlist else "track",
        artwork=artwork_url(obj.get("artwork_url") or user.get("avatar_url")),
        repost_by=repost_by,
        track_count=obj.get("track_count") if is_playlist else None,
    )


class SoundCloudAPI:
    def __init__(self, oauth_token: str | None):
        self.token = oauth_token
        self._client_id: str | None = None
        self._me: dict | None = None

    # ---------- client_id ----------

    def client_id(self, refresh: bool = False) -> str:
        if self._client_id and not refresh:
            return self._client_id
        if not refresh and CLIENT_ID_FILE.exists() and time.time() - CLIENT_ID_FILE.stat().st_mtime < 86400:
            self._client_id = CLIENT_ID_FILE.read_text().strip()
            return self._client_id
        html = _http("https://soundcloud.com/").decode("utf-8", "replace")
        scripts = re.findall(r'<script[^>]+src="(https://a-v2\.sndcdn\.com/assets/[^"]+\.js)"', html)
        for src in reversed(scripts):  # the client_id lives in one of the last bundles
            m = re.search(r'client_id\s*[:=]\s*"([A-Za-z0-9]{32})"', _http(src).decode("utf-8", "replace"))
            if m:
                self._client_id = m.group(1)
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                CLIENT_ID_FILE.write_text(self._client_id)
                return self._client_id
        raise APIError("Could not find SoundCloud's client_id")

    # ---------- requests ----------

    def get(self, path_or_url: str, params: dict | None = None, _retry: bool = True) -> dict:
        url = path_or_url if path_or_url.startswith("http") else API + path_or_url
        query = {"client_id": self.client_id(), **(params or {})}
        sep = "&" if "?" in url else "?"
        url = url + sep + urllib.parse.urlencode(query)
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"OAuth {self.token}"
        try:
            return json.loads(_http(url, headers))
        except urllib.error.HTTPError as e:
            if e.code in (401, 403) and _retry:
                self.client_id(refresh=True)  # client_id rotates every so often
                return self.get(path_or_url, params, _retry=False)
            if e.code == 401:
                raise APIError("SoundCloud rejected the OAuth token — copy a fresh one into the config") from e
            raise APIError(f"SoundCloud API error {e.code} for {path_or_url.split('?')[0]}") from e

    def paginate(self, path: str, limit: int, page: int = 50) -> list[dict]:
        items: list[dict] = []
        data = self.get(path, {"limit": page, "linked_partitioning": 1})
        while True:
            items.extend(data.get("collection") or [])
            nxt = data.get("next_href")
            if not nxt or len(items) >= limit:
                return items[:limit]
            # next_href already carries the cursor/limit; get() adds client_id.
            data = self.get(nxt)

    # ---------- account ----------

    def me(self) -> dict:
        if not self.token:
            raise APIError("Add oauth_token to ~/.config/sctui/config.toml for your feed and likes")
        if self._me is None:
            self._me = self.get("/me")
        return self._me

    def feed(self, limit: int = 150) -> list[Track]:
        self.me()
        tracks = []
        for item in self.paginate("/stream", limit):
            kind = item.get("type", "")
            obj = item.get("track") or item.get("playlist")
            if not obj:
                continue
            repost_by = (item.get("user") or {}).get("username") if kind.endswith("repost") else None
            tracks.append(to_track(obj, repost_by))
        return tracks

    def likes(self, limit: int = 500) -> list[Track]:
        uid = self.me()["id"]
        return [to_track(i.get("track") or i.get("playlist"))
                for i in self.paginate(f"/users/{uid}/likes", limit)
                if i.get("track") or i.get("playlist")]

    def playlists(self, limit: int = 200) -> list[Track]:
        uid = self.me()["id"]
        return [to_track(p) for p in self.paginate(f"/users/{uid}/playlists_without_albums", limit)]

    # ---------- playlists ----------

    def playlist_tracks(self, url: str) -> list[Track]:
        pl = self.get("/resolve", {"url": url})
        if pl.get("kind") != "playlist":
            raise APIError("Not a playlist")
        raw = pl.get("tracks") or []
        # Only the first few tracks come back complete; fetch the rest by id.
        missing = [t["id"] for t in raw if "title" not in t]
        full = {t["id"]: t for t in raw if "title" in t}
        for i in range(0, len(missing), 50):
            ids = ",".join(map(str, missing[i : i + 50]))
            for t in self.get("/tracks", {"ids": ids}):
                full[t["id"]] = t
        return [to_track(full[t["id"]]) for t in raw if t["id"] in full]
