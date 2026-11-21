"""Desktop notifications (notify-send) and cover-art caching."""

from __future__ import annotations

import asyncio
import hashlib
import urllib.request

from .api import CACHE_DIR, UA

ART_DIR = CACHE_DIR / "art"


def cache_artwork(url: str | None) -> str | None:
    """Download cover art once and return the local path (blocking; call from a thread)."""
    if not url:
        return None
    path = ART_DIR / (hashlib.sha1(url.encode()).hexdigest() + ".jpg")
    if path.exists():
        return str(path)
    try:
        ART_DIR.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=4) as r:
            data = r.read()
        tmp = path.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(path)
        return str(path)
    except Exception:
        return None


class Notifier:
    def __init__(self) -> None:
        self._id: str | None = None

    async def track(self, title: str, body: str, icon: str | None) -> None:
        await self._send(["-i", icon or "audio-x-generic", "-t", "5000", title, body])

    async def _send(self, extra: list[str]) -> None:
        args = ["notify-send", "-a", "sctui", "-u", "low", "-p",
                "-h", "string:x-canonical-private-synchronous:sctui"]
        if self._id:
            args += ["-r", self._id]  # replace the previous sctui toast instead of stacking
        args += extra
        try:
            proc = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out, _ = await asyncio.wait_for(proc.communicate(), 3)
            self._id = out.decode().strip() or None
        except (FileNotFoundError, asyncio.TimeoutError):
            pass

    async def volume(self, level: int) -> None:
        bar = "█" * (level // 10) + "░" * max(10 - level // 10, 0)
        await self._send(["-i", "audio-volume-high", "-t", "1500", "-h", f"int:value:{min(level, 100)}",
                          "sctui volume", f"{bar}  {level}%"])
