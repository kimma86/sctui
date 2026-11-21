"""Minimal async controller for an mpv process over its JSON IPC socket."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from typing import Any, Callable

from .visualizer import CLIENT_NAME

OBSERVED = ["time-pos", "duration", "pause", "volume", "idle-active"]


class Player:
    def __init__(self, on_event: Callable[[dict], None], extra_args: list[str] | None = None):
        self.on_event = on_event
        self.extra_args = extra_args or []
        self.sock_path = os.path.join(tempfile.gettempdir(), f"sctui-{os.getpid()}.sock")
        self.proc: asyncio.subprocess.Process | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task | None = None

    async def start(self) -> None:
        if os.path.exists(self.sock_path):
            os.unlink(self.sock_path)
        self.proc = await asyncio.create_subprocess_exec(
            "mpv",
            "--idle=yes",
            "--no-video",
            "--no-terminal",
            "--audio-display=no",
            f"--audio-client-name={CLIENT_NAME}",  # lets the visualizer find our stream
            "--load-scripts=no",  # skip mpv-mpris etc.; sctui provides its own MPRIS
            f"--input-ipc-server={self.sock_path}",
            *self.extra_args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        for _ in range(100):
            try:
                reader, self._writer = await asyncio.open_unix_connection(self.sock_path)
                break
            except (FileNotFoundError, ConnectionRefusedError):
                await asyncio.sleep(0.05)
        else:
            raise RuntimeError("Could not connect to mpv IPC socket")
        self._reader_task = asyncio.create_task(self._read_loop(reader))
        for i, prop in enumerate(OBSERVED, start=1):
            await self.command("observe_property", i, prop)

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        while line := await reader.readline():
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "event" in msg:
                self.on_event(msg)

    async def command(self, *args: Any) -> None:
        if not self._writer:
            return
        self._writer.write((json.dumps({"command": list(args)}) + "\n").encode())
        await self._writer.drain()

    async def load(self, url: str, headers: dict | None = None) -> None:
        headers = headers or {}
        ua = headers.get("User-Agent")
        if ua:
            await self.command("set_property", "user-agent", ua)
        await self.command("loadfile", url, "replace")
        await self.command("set_property", "pause", False)

    async def toggle_pause(self) -> None:
        await self.command("cycle", "pause")

    async def seek(self, seconds: float, mode: str = "relative") -> None:
        await self.command("seek", seconds, mode)

    async def add_volume(self, delta: int) -> None:
        await self.command("add", "volume", delta)

    async def stop_playback(self) -> None:
        await self.command("stop")

    async def quit(self) -> None:
        try:
            await self.command("quit")
        except (ConnectionError, RuntimeError):
            pass
        if self.proc:
            try:
                await asyncio.wait_for(self.proc.wait(), 2)
            except asyncio.TimeoutError:
                self.proc.kill()
        if self._reader_task:
            self._reader_task.cancel()
        if os.path.exists(self.sock_path):
            os.unlink(self.sock_path)
