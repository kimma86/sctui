"""Spectrum visualizer: taps only mpv's audio stream via `parec --monitor-stream`."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time

import numpy as np
from rich.text import Text
from textual.widget import Widget

RATE = 44100
FFT_SIZE = 4096  # ~10.8 Hz per bin, enough to separate the narrow bass bars
CHUNK = 512  # samples per read (~12 ms)
CLIENT_NAME = f"sctui-{os.getpid()}"  # mpv is started with --audio-client-name=<this>


class AudioTap:
    """Keeps a rolling buffer of the most recent samples from mpv's sink input."""

    def __init__(self, client_name: str = CLIENT_NAME):
        # Unique per sctui instance (see Player), so a second instance isn't picked up.
        self.client_name = client_name
        self.buf = np.zeros(FFT_SIZE, dtype=np.float32)
        self.last_data = 0.0
        self.available = True
        self._proc: asyncio.subprocess.Process | None = None
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._supervise())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._kill()

    def _kill(self) -> None:
        if self._proc and self._proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self._proc.kill()
        self._proc = None

    async def _find_sink_input(self) -> int | None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "pactl", "-f", "json", "list", "sink-inputs",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
        except FileNotFoundError:
            self.available = False
            return None
        out, _ = await proc.communicate()
        try:
            inputs = json.loads(out or b"[]")
        except json.JSONDecodeError:
            return None
        for si in inputs:
            props = si.get("properties", {})
            if self.client_name in (props.get("application.name"), props.get("node.name")):
                return si.get("index")
        return None

    async def _supervise(self) -> None:
        """Attach parec to mpv's stream, and re-attach whenever mpv recreates it."""
        current: int | None = None
        reader: asyncio.Task | None = None
        try:
            while True:
                idx = await self._find_sink_input()
                if idx != current or (reader and reader.done()):
                    if reader:
                        reader.cancel()
                    self._kill()
                    current = idx
                    reader = asyncio.create_task(self._read(idx)) if idx is not None else None
                await asyncio.sleep(1.0)
        finally:
            if reader:
                reader.cancel()

    async def _read(self, index: int) -> None:
        try:
            self._proc = await asyncio.create_subprocess_exec(
                "parec", "--raw", f"--monitor-stream={index}", "--format=s16le",
                f"--rate={RATE}", "--channels=1", "--latency-msec=20",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
        except FileNotFoundError:
            self.available = False
            return
        assert self._proc.stdout
        nbytes = CHUNK * 2
        while True:
            data = await self._proc.stdout.read(nbytes)
            if not data:
                return
            samples = np.frombuffer(data[: len(data) - len(data) % 2], dtype="<i2").astype(np.float32) / 32768.0
            n = len(samples)
            self.buf = np.concatenate((self.buf[n:], samples)) if n < FFT_SIZE else samples[-FFT_SIZE:]
            self.last_data = time.monotonic()


class Spectrum:
    """Turns raw samples into smoothed 0..1 band levels with an adaptive ceiling."""

    def __init__(self) -> None:
        self.window = np.hanning(FFT_SIZE).astype(np.float32)
        self.freqs = np.fft.rfftfreq(FFT_SIZE, 1 / RATE)
        self.nbands = 0
        self.ceiling = -20.0

    def _setup(self, nbands: int) -> None:
        self.nbands = nbands
        edges = np.geomspace(45, 16000, nbands + 1)
        bins = np.searchsorted(self.freqs, edges)
        self.lo = bins[:-1]
        self.hi = np.maximum(bins[1:], self.lo + 1)
        centers = np.sqrt(edges[:-1] * edges[1:])
        self.centers = centers
        # Bands narrower than ~2 FFT bins would repeat the same bin; interpolate those instead.
        self.narrow = (self.hi - self.lo) <= 2
        self.tilt = 3.0 * np.log2(centers / 200.0)  # lift highs (pink-noise-ish weighting)

    def levels(self, samples: np.ndarray, nbands: int) -> np.ndarray:
        if nbands != self.nbands:
            self._setup(nbands)
        spec = np.abs(np.fft.rfft(samples * self.window)) / (FFT_SIZE / 4)
        mags = np.array([spec[a:b].max() for a, b in zip(self.lo, self.hi)])
        mags[self.narrow] = np.interp(self.centers[self.narrow], self.freqs, spec)
        db = 20 * np.log10(mags + 1e-9) + self.tilt
        peak = float(db.max())
        # Adaptive gain: jump up fast, drift down slowly; never amplify near-silence.
        self.ceiling = max(peak, self.ceiling - 0.25, -35.0)
        floor = self.ceiling - 42.0
        return np.clip((db - floor) / (self.ceiling - floor), 0.0, 1.0)


BLOCKS = " ▁▂▃▄▅▆▇█"
# ANSI colour names, so the gradient follows the terminal (Omarchy) palette. Bottom → top.
GRADIENT = ["blue", "bright_blue", "cyan", "bright_cyan", "magenta", "bright_magenta"]


class Visualizer(Widget):
    DEFAULT_CSS = """
    Visualizer { height: 8; margin: 0 1; }
    """

    BAR_W = 1
    GAP = 1

    def __init__(self, tap: AudioTap, **kwargs) -> None:
        super().__init__(**kwargs)
        self.tap = tap
        self.spectrum = Spectrum()
        self.bars = np.zeros(0)
        self.peaks = np.zeros(0)
        self.peak_hold = np.zeros(0)

    def on_mount(self) -> None:
        self.set_interval(1 / 30, self.tick)

    def tick(self) -> None:
        if not self.display:
            return
        n = max((self.size.width + self.GAP) // (self.BAR_W + self.GAP), 1)
        if len(self.bars) != n:
            self.bars, self.peaks, self.peak_hold = np.zeros(n), np.zeros(n), np.zeros(n)
        if time.monotonic() - self.tap.last_data < 0.15:
            target = self.spectrum.levels(self.tap.buf, n)
        else:
            target = np.zeros(n)  # paused / stopped: let everything fall
        # Fast attack, gravity-like release.
        rising = target > self.bars
        self.bars = np.where(rising, self.bars * 0.35 + target * 0.65, np.maximum(self.bars - 0.045, target))
        # Peak caps hold briefly, then fall.
        new_peak = self.bars >= self.peaks
        self.peaks = np.where(new_peak, self.bars, self.peaks)
        self.peak_hold = np.where(new_peak, 12, self.peak_hold - 1)
        self.peaks = np.where(self.peak_hold <= 0, np.maximum(self.peaks - 0.025, self.bars), self.peaks)
        self.refresh()

    def render(self) -> Text:
        h = self.size.height
        n = len(self.bars)
        if h <= 0 or n == 0:
            return Text("")
        if not self.tap.available:
            return Text("visualizer needs pactl + parec (libpulse)", style="dim")
        used = n * self.BAR_W + (n - 1) * self.GAP
        pad = " " * max((self.size.width - used) // 2, 0)
        eighths = self.bars * h * 8
        cap_row = np.minimum((self.peaks * h).astype(int), h - 1)
        text = Text(no_wrap=True, overflow="crop")
        for row in range(h - 1, -1, -1):  # top row first
            color = GRADIENT[min(int(row / h * len(GRADIENT)), len(GRADIENT) - 1)]
            text.append(pad)
            for i in range(n):
                fill = int(min(max(eighths[i] - row * 8, 0), 8))
                if fill == 0 and cap_row[i] == row and self.peaks[i] > 0.02:
                    text.append("▔" * self.BAR_W, style=GRADIENT[-1])
                else:
                    text.append(BLOCKS[fill] * self.BAR_W, style=color)
                if i < n - 1:
                    text.append(" " * self.GAP)
            if row:
                text.append("\n")
        return text
