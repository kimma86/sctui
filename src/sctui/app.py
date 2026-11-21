"""sctui — a full-screen SoundCloud player for the terminal."""

from __future__ import annotations

import asyncio
from typing import Callable

from rich.markup import escape
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import DataTable, Footer, Input, Static

from . import config
from .mpris import MprisServer
from .notify import Notifier, cache_artwork
from .player import Player
from .source import SoundCloud, Track, fmt_time
from .visualizer import AudioTap, Visualizer


class NowPlaying(Static):
    """The now-playing panel; clicking the progress bar seeks there."""

    async def on_click(self, event) -> None:
        offset = event.get_content_offset(self)
        if offset is None or offset.y != 1:  # line 1 of the panel is the progress bar
            return
        width = max(self.content_size.width - 1, 1)
        percent = min(max(offset.x / width, 0.0), 1.0) * 100
        await self.app.action_seek_percent(percent)


class SCApp(App):
    TITLE = "sctui"
    ENABLE_COMMAND_PALETTE = False

    CSS = """
    Screen { layout: vertical; }
    #search { margin: 0 1; }
    #main { height: 1fr; }
    DataTable { border: round $primary-darken-2; height: 1fr; }
    DataTable:focus { border: round $accent; }
    #results { width: 3fr; }
    #queue { width: 2fr; }
    * { scrollbar-size-vertical: 1; scrollbar-size-horizontal: 1; }
    #now { width: 1fr; height: 5; border: round $primary-darken-2; padding: 0 1; }
    """

    BINDINGS = [
        Binding("slash", "focus_search", "Search"),
        Binding("escape", "focus_results", "Results", show=False),
        Binding("space", "toggle_pause", "Play/Pause"),
        Binding("n", "next", "Next"),
        Binding("p", "prev", "Prev", show=False),
        # Arrows are priority bindings so the tables don't swallow them; check_action
        # hands them back to the search box while it has focus.
        Binding("left", "seek(-5)", "Seek", key_display="←→", priority=True),
        Binding("right", "seek(5)", "+5s", show=False, priority=True),
        Binding("shift+left", "seek(-30)", "-30s", show=False, priority=True),
        Binding("shift+right", "seek(30)", "+30s", show=False, priority=True),
        *[Binding(str(d), f"seek_percent({d * 10})", f"{d * 10}%", show=False) for d in range(10)],
        Binding("comma", "seek(-10)", "-10s", show=False),
        Binding("full_stop", "seek(10)", "+10s", show=False),
        Binding("minus", "volume(-5)", "Vol-", show=False),
        Binding("plus,equals_sign", "volume(5)", "Vol+", show=False),
        Binding("a", "enqueue", "Add"),
        Binding("d,delete", "dequeue", "Remove", show=False),
        Binding("f", "feed", "Feed"),
        Binding("l", "likes", "Likes"),
        Binding("s", "sets", "Lists"),
        Binding("v", "toggle_viz", "Viz"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.cfg = config.load()
        self.sc = SoundCloud(self.cfg.oauth_token, self.cfg.username)
        self.results: list[Track] = []
        self.queue: list[Track] = []
        self.current = -1
        self.gen = 0  # bumps on every play request; stale resolves are dropped
        self.loading = False
        self.pos: float | None = None
        self.dur: float | None = None
        self.paused = False
        self.idle = True
        self.volume = self.cfg.volume
        self.player = Player(self.on_mpv_event, [f"--volume={self.cfg.volume}"])
        self.tap = AudioTap()
        self.art_path: str | None = None
        self.mpris = MprisServer(self)
        self.notifier = Notifier()

    # ---------- layout ----------

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Search SoundCloud or paste a track / playlist / user URL…  (press / )", id="search")
        with Horizontal(id="main"):
            yield DataTable(id="results", cursor_type="row", zebra_stripes=True)
            yield DataTable(id="queue", cursor_type="row")
        yield Visualizer(self.tap, id="viz")
        yield NowPlaying(id="now")
        yield Footer(compact=True)

    async def on_mount(self) -> None:
        if self.cfg.theme and self.cfg.theme in self.available_themes:
            self.theme = self.cfg.theme
        self.query_one("#results", DataTable).border_title = "Results"
        self.query_one("#queue", DataTable).border_title = "Queue"
        self.call_after_refresh(self.relayout_tables)
        self.query_one("#now", Static).border_title = "Now playing"
        self.update_now()
        try:
            await self.player.start()
        except (OSError, RuntimeError) as e:
            self.notify(f"Could not start mpv: {e}", severity="error", timeout=10)
        self.query_one("#viz", Visualizer).display = self.cfg.visualizer
        self.tap.start()
        await self.mpris.start()
        self.query_one("#search", Input).focus()
        if self.cfg.oauth_token:
            self.action_feed()
        elif self.cfg.username:
            self.notify(f"Hi {self.cfg.username} — press l for likes, s for playlists")

    def on_resize(self) -> None:
        self.update_now()
        self.call_after_refresh(self.relayout_tables)

    def relayout_tables(self) -> None:
        self.render_results()
        self.refresh_queue()

    # ---------- mpv events ----------

    def on_mpv_event(self, msg: dict) -> None:
        ev = msg.get("event")
        if ev == "property-change":
            name, data = msg.get("name"), msg.get("data")
            if name == "time-pos":
                self.pos = data
            elif name == "duration":
                self.dur = data
            elif name == "pause":
                self.paused = bool(data)
            elif name == "volume" and data is not None:
                self.volume = int(data)
            elif name == "idle-active":
                self.idle = bool(data)
            self.update_now()
        elif ev == "playback-restart":
            self.mpris.seeked()
        elif ev == "end-file":
            reason = msg.get("reason")
            if reason == "eof":
                self.advance()
            elif reason == "error":
                self.notify(f"Playback error: {msg.get('file_error', 'unknown')}", severity="warning")
                self.advance()

    def advance(self) -> None:
        if self.current + 1 < len(self.queue):
            self.play_index(self.current + 1)
        else:
            self.current = -1
            self.pos = self.dur = None
            self.refresh_queue()
            self.update_now()

    # ---------- now-playing panel ----------

    def update_now(self) -> None:
        try:
            now = self.query_one("#now", Static)
        except Exception:
            return
        width = max((now.content_size.width or 60) - 1, 20)
        if 0 <= self.current < len(self.queue):
            t = self.queue[self.current]
            icon = "⏳" if self.loading else ("⏸" if self.paused else "▶")
            title = f"{icon}  [b]{escape(t.title)}[/b]  [dim]{escape(t.artist)}[/dim]"
        else:
            title = "[dim]Nothing playing — search with / and press Enter on a track[/dim]"
        dur = self.dur or (self.queue[self.current].duration if 0 <= self.current < len(self.queue) else None)
        frac = (self.pos / dur) if (self.pos is not None and dur) else 0.0
        filled = int(width * min(max(frac, 0.0), 1.0))
        bar = f"[$accent]{'━' * filled}[/][dim]{'─' * (width - filled)}[/dim]"
        qinfo = f"{self.current + 1}/{len(self.queue)}" if self.current >= 0 else f"–/{len(self.queue)}"
        info = f"{fmt_time(self.pos)} / {fmt_time(dur)}    vol {self.volume}%    queue {qinfo}"
        now.update(f"{title}\n{bar}\n[dim]{info}[/dim]")
        self.mpris.update()

    # ---------- tables ----------

    @staticmethod
    def _cell(text: str, style: str = "") -> Text:
        return Text(text, style=style, no_wrap=True, overflow="ellipsis")

    @staticmethod
    def _avail(table: DataTable, fixed: list[int]) -> int:
        """Width left for flexible columns (each column also gets 1 cell of padding per side)."""
        ncols = len(fixed) + 1
        return max(table.size.width - 1 - sum(fixed) - 2 * ncols, 10)

    def show_results(self, title: str, tracks: list[Track]) -> None:
        self.results = tracks
        table = self.query_one("#results", DataTable)
        table.border_title = f"{escape(title)} ({len(tracks)})"
        self.render_results()
        if not tracks:
            self.notify("No results")
        table.focus()

    def render_results(self) -> None:
        table = self.query_one("#results", DataTable)
        row = table.cursor_row
        flex = self._avail(table, [1, 6]) - 2  # title + artist share this (artist has its own padding)
        artist_w = max(min(26, flex * 2 // 5), 8)
        table.clear(columns=True)
        table.add_column(" ", width=1)
        table.add_column("Title", width=flex - artist_w)
        table.add_column("Artist", width=artist_w)
        table.add_column("Time", width=6)
        for t in self.results:
            icon = "▤" if t.kind == "playlist" else ("↻" if t.repost_by else "♪")
            length = f"{t.track_count} tr" if t.kind == "playlist" and t.track_count else fmt_time(t.duration)
            artist = self._cell(t.artist)
            if t.repost_by:
                artist.append(f" ↻ {t.repost_by}", style="dim")
            table.add_row(icon, self._cell(t.title), artist, length)
        if self.results:
            table.move_cursor(row=min(row, len(self.results) - 1))

    def refresh_queue(self) -> None:
        table = self.query_one("#queue", DataTable)
        row = table.cursor_row
        table.clear(columns=True)
        table.add_column(" ", width=1)
        table.add_column("Queue", width=self._avail(table, [1, 6]))
        table.add_column("Time", width=6)
        for i, t in enumerate(self.queue):
            table.add_row("▶" if i == self.current else "", self._cell(t.label), fmt_time(t.duration))
        if self.queue:
            table.move_cursor(row=min(row, len(self.queue) - 1))
        table.border_title = f"Queue ({len(self.queue)})"

    # ---------- background work ----------

    @work(thread=True, exclusive=True, group="fetch")
    def load_results(self, title: str, fetch: Callable[[], list[Track]]) -> None:
        self.call_from_thread(self.notify, f"Loading {title}…", timeout=2)
        try:
            tracks = fetch()
        except Exception as e:  # yt-dlp raises many types
            self.call_from_thread(self.notify, f"{title}: {e}", severity="error", timeout=8)
            return
        self.call_from_thread(self.show_results, title, tracks)

    @work(thread=True, group="enqueue")
    def enqueue_playlist(self, track: Track) -> None:
        try:
            tracks = [t for t in self.sc.fetch(track.url) if t.kind == "track"]
        except Exception as e:
            self.call_from_thread(self.notify, f"{track.title}: {e}", severity="error")
            return
        self.call_from_thread(self.add_to_queue, tracks)

    def play_index(self, index: int) -> None:
        if not 0 <= index < len(self.queue):
            return
        self.current = index
        self.gen += 1
        self.loading = True
        self.pos = self.dur = None
        self.refresh_queue()
        self.update_now()
        self.resolve_and_play(self.gen, self.queue[index])

    @work(thread=True, group="resolve")
    def resolve_and_play(self, gen: int, track: Track) -> None:
        try:
            stream, headers, enriched = self.sc.resolve(track)
        except Exception as e:
            self.call_from_thread(self._resolve_failed, gen, track, str(e))
            return
        art = cache_artwork(enriched.artwork)
        self.call_from_thread(self._start_stream, gen, stream, headers, enriched, art)

    async def _start_stream(self, gen: int, stream: str, headers: dict, enriched: Track, art: str | None) -> None:
        if gen != self.gen:
            return  # user already skipped elsewhere
        self.queue[self.current] = enriched
        self.art_path = art
        self.loading = False
        self.refresh_queue()
        await self.player.load(stream, headers)
        self.update_now()
        if self.cfg.notifications:
            body = enriched.artist + (f"  ·  ↻ {enriched.repost_by}" if enriched.repost_by else "")
            await self.notifier.track(enriched.title, body, art)

    def _resolve_failed(self, gen: int, track: Track, err: str) -> None:
        if gen != self.gen:
            return
        self.loading = False
        self.notify(f"Can't play {track.title}: {err}", severity="warning", timeout=6)
        self.advance()

    def add_to_queue(self, tracks: list[Track]) -> None:
        start_now = self.current == -1
        first_new = len(self.queue)
        self.queue.extend(tracks)
        self.refresh_queue()
        self.notify(f"Added {len(tracks)} to queue" if len(tracks) != 1 else f"Queued: {tracks[0].title}")
        if start_now and tracks:
            self.play_index(first_new)
        else:
            self.update_now()

    # ---------- events ----------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        q = event.value.strip()
        if q:
            self.load_results(q, lambda: self.sc.fetch(q))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        row = event.cursor_row
        if event.data_table.id == "results":
            if not 0 <= row < len(self.results):
                return
            t = self.results[row]
            if t.kind == "playlist":
                self.load_results(t.title, lambda: self.sc.fetch(t.url))
                return
            # Play the chosen track and queue the rest of the results after it.
            tracks = [x for x in self.results if x.kind == "track"]
            self.queue = tracks
            self.play_index(tracks.index(t))
        else:
            self.play_index(row)

    # ---------- actions ----------

    def action_focus_search(self) -> None:
        search = self.query_one("#search", Input)
        search.focus()
        search.action_select_all()

    def action_focus_results(self) -> None:
        self.query_one("#results", DataTable).focus()

    async def action_toggle_pause(self) -> None:
        if self.current == -1 and self.queue:
            self.play_index(0)
        else:
            await self.player.toggle_pause()

    async def action_play(self) -> None:
        if self.current == -1:
            if self.queue:
                self.play_index(0)
        elif self.paused:
            await self.player.toggle_pause()

    async def action_pause(self) -> None:
        if self.current != -1 and not self.paused:
            await self.player.toggle_pause()

    async def action_stop(self) -> None:
        """Winamp-style stop: pause and rewind to the start of the track."""
        if self.current != -1:
            await self.action_pause()
            await self.player.seek(0, "absolute")

    async def action_show_now(self) -> None:
        if 0 <= self.current < len(self.queue):
            t = self.queue[self.current]
            state = "⏸ Paused" if self.paused else "▶ Playing"
            await self.notifier.track(t.title, f"{t.artist}\n{state} · {fmt_time(self.pos)} / {fmt_time(self.dur)}", self.art_path)
        else:
            await self.notifier.track("sctui", "Nothing playing", None)

    def action_next(self) -> None:
        if self.current + 1 < len(self.queue):
            self.play_index(self.current + 1)

    async def action_prev(self) -> None:
        if (self.pos or 0) > 3 or self.current <= 0:
            await self.player.seek(0, "absolute")
        else:
            self.play_index(self.current - 1)

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        if action == "seek" and isinstance(self.focused, Input):
            return False  # let the search box use the arrow keys
        return True

    async def action_seek(self, seconds: int) -> None:
        if self.current != -1 and not self.loading:
            await self.player.seek(seconds)

    async def action_seek_percent(self, percent: float) -> None:
        if self.current != -1 and not self.loading:
            await self.player.seek(percent, "absolute-percent")

    async def action_volume(self, delta: int) -> None:
        await self.player.add_volume(delta)
        if not self.app_focus:  # changed via global hotkey: show feedback on the desktop
            await asyncio.sleep(0.05)  # let mpv report the new volume
            await self.notifier.volume(self.volume)

    def action_enqueue(self) -> None:
        table = self.query_one("#results", DataTable)
        row = table.cursor_row
        if not 0 <= row < len(self.results):
            return
        t = self.results[row]
        if t.kind == "playlist":
            self.notify(f"Queuing playlist {t.title}…", timeout=2)
            self.enqueue_playlist(t)
        else:
            self.add_to_queue([t])

    async def action_dequeue(self) -> None:
        table = self.query_one("#queue", DataTable)
        if not table.has_focus or not self.queue:
            return
        row = table.cursor_row
        if not 0 <= row < len(self.queue):
            return
        del self.queue[row]
        if row < self.current:
            self.current -= 1
            self.refresh_queue()
        elif row == self.current:
            if row < len(self.queue):
                self.play_index(row)
            else:
                self.current = -1
                self.gen += 1
                await self.player.stop_playback()
                self.refresh_queue()
        else:
            self.refresh_queue()
        self.update_now()

    def action_feed(self) -> None:
        self.load_results("Your feed", self.sc.feed)

    def action_likes(self) -> None:
        self.load_results("Your likes", self.sc.likes)

    def action_sets(self) -> None:
        self.load_results("Your playlists", self.sc.playlists)

    def action_toggle_viz(self) -> None:
        viz = self.query_one("#viz", Visualizer)
        viz.display = not viz.display

    async def action_quit(self) -> None:
        await self.tap.stop()
        self.mpris.stop()
        await self.player.quit()
        self.exit()


def main() -> None:
    SCApp().run()


if __name__ == "__main__":
    main()
