"""SoundCloud access via yt-dlp (search, URLs, playlists, likes, stream resolution)."""

from __future__ import annotations

from dataclasses import dataclass

import yt_dlp


@dataclass
class Track:
    url: str
    title: str
    artist: str = ""
    duration: float | None = None
    kind: str = "track"  # "track" or "playlist"
    artwork: str | None = None
    repost_by: str | None = None
    track_count: int | None = None

    @property
    def label(self) -> str:
        return f"{self.artist} — {self.title}" if self.artist else self.title


def fmt_time(seconds: float | None) -> str:
    if seconds is None:
        return "--:--"
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


def is_url(text: str) -> bool:
    t = text.strip().lower()
    return t.startswith(("http://", "https://")) or t.startswith(("soundcloud.com/", "on.soundcloud.com/"))


class SoundCloud:
    def __init__(self, oauth_token: str | None = None, username: str | None = None):
        from .api import SoundCloudAPI  # local import: api imports Track from here

        self.oauth_token = oauth_token
        self.username = username
        self.api = SoundCloudAPI(oauth_token)

    def _ydl(self, flat: bool) -> yt_dlp.YoutubeDL:
        opts: dict = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": False,
            "format": "bestaudio/best",
        }
        if flat:
            opts["extract_flat"] = "in_playlist"
        if self.oauth_token:
            # yt-dlp's SoundCloud extractor accepts an OAuth token this way.
            opts["username"] = "oauth"
            opts["password"] = self.oauth_token
        return yt_dlp.YoutubeDL(opts)

    @staticmethod
    def _to_track(e: dict) -> Track | None:
        url = e.get("webpage_url") or e.get("url")
        if not url:
            return None
        kind = "playlist" if ("/sets/" in url or e.get("_type") == "playlist") else "track"
        title = e.get("title") or url.rstrip("/").rsplit("/", 1)[-1]
        artist = e.get("uploader") or e.get("artist") or ""
        return Track(url=url, title=title, artist=artist, duration=e.get("duration"), kind=kind)

    def fetch(self, query: str, limit: int = 40) -> list[Track]:
        """Search, or resolve a track / playlist / user URL into a list of tracks."""
        query = query.strip()
        if is_url(query) and "/sets/" in query:
            try:
                return self.api.playlist_tracks(query if query.startswith("http") else f"https://{query}")
            except Exception:
                pass  # fall back to yt-dlp below
        if is_url(query):
            target = query if query.startswith("http") else f"https://{query}"
        else:
            target = f"scsearch{limit}:{query}"
        with self._ydl(flat=True) as ydl:
            info = ydl.extract_info(target, download=False)
        if not info:
            return []
        if info.get("_type") in ("playlist", "multi_video"):
            entries = [e for e in (info.get("entries") or []) if e]
        else:
            entries = [info]
        return [t for t in map(self._to_track, entries) if t]

    def feed(self) -> list[Track]:
        return self.api.feed()

    def likes(self) -> list[Track]:
        if self.oauth_token:
            return self.api.likes()
        if not self.username:
            raise ValueError("Set `username` in ~/.config/sctui/config.toml to load your likes.")
        return self.fetch(f"https://soundcloud.com/{self.username}/likes")

    def playlists(self) -> list[Track]:
        if not self.username and not self.oauth_token:
            raise ValueError("Set `username` in ~/.config/sctui/config.toml to load your playlists.")
        if self.oauth_token:
            return self.api.playlists()
        tracks = self.fetch(f"https://soundcloud.com/{self.username}/sets")
        for t in tracks:
            t.kind = "playlist"
        return tracks

    def resolve(self, track: Track) -> tuple[str, dict, Track]:
        """Return (stream_url, http_headers, enriched_track) for playback."""
        with self._ydl(flat=False) as ydl:
            info = ydl.extract_info(track.url, download=False)
        stream = info.get("url")
        if not stream and info.get("requested_formats"):
            stream = info["requested_formats"][0]["url"]
        if not stream:
            raise RuntimeError("No playable stream found (track may be Go+ only or region-blocked).")
        enriched = Track(
            url=track.url,
            title=info.get("title") or track.title,
            artist=track.artist or info.get("uploader") or "",
            duration=info.get("duration") or track.duration,
            artwork=track.artwork or self._pick_artwork(info),
            repost_by=track.repost_by,
        )
        return stream, info.get("http_headers") or {}, enriched

    @staticmethod
    def _pick_artwork(info: dict) -> str | None:
        for t in info.get("thumbnails") or []:
            if t.get("id") in ("t500x500", "t300x300"):
                return t.get("url")
        return info.get("thumbnail")
