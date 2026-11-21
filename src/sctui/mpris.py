"""MPRIS2 D-Bus service, so media keys and the Omarchy bar control sctui.

Note: no `from __future__ import annotations` here — dbus-fast reads the
D-Bus type signatures from the annotation strings.
"""

import asyncio
import logging
from typing import TYPE_CHECKING

from dbus_fast import Variant
from dbus_fast.aio import MessageBus
from dbus_fast.constants import NameFlag, PropertyAccess
from dbus_fast.service import ServiceInterface, dbus_property, method, signal

if TYPE_CHECKING:
    from .app import SCApp

log = logging.getLogger(__name__)
PATH = "/org/mpris/MediaPlayer2"
BUS_NAME = "org.mpris.MediaPlayer2.sctui"


class Root(ServiceInterface):
    def __init__(self, app: "SCApp"):
        super().__init__("org.mpris.MediaPlayer2")
        self.app = app

    @method()
    def Raise(self):
        pass

    @method()
    def Quit(self):
        asyncio.get_running_loop().create_task(self.app.run_action("quit"))

    @dbus_property(access=PropertyAccess.READ)
    def CanQuit(self) -> "b":
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanRaise(self) -> "b":
        return False

    @dbus_property(access=PropertyAccess.READ)
    def HasTrackList(self) -> "b":
        return False

    @dbus_property(access=PropertyAccess.READ)
    def Identity(self) -> "s":
        return "SoundCloud (sctui)"

    @dbus_property(access=PropertyAccess.READ)
    def DesktopEntry(self) -> "s":
        return "sctui"

    @dbus_property(access=PropertyAccess.READ)
    def SupportedUriSchemes(self) -> "as":
        return ["https"]

    @dbus_property(access=PropertyAccess.READ)
    def SupportedMimeTypes(self) -> "as":
        return []


class Player(ServiceInterface):
    def __init__(self, app: "SCApp"):
        super().__init__("org.mpris.MediaPlayer2.Player")
        self.app = app

    def _run(self, action: str) -> None:
        asyncio.get_running_loop().create_task(self.app.run_action(action))

    # --- methods ---
    @method()
    def Next(self):
        self._run("next")

    @method()
    def Previous(self):
        self._run("prev")

    @method()
    def PlayPause(self):
        self._run("toggle_pause")

    @method()
    def Play(self):
        if self.app.paused or self.app.current == -1:
            self._run("toggle_pause")

    @method()
    def Pause(self):
        if not self.app.paused and self.app.current != -1:
            self._run("toggle_pause")

    @method()
    def Stop(self):
        if not self.app.paused:
            self._run("toggle_pause")

    @method()
    def Seek(self, offset: "x"):
        self._run(f"seek({offset / 1_000_000})")

    @method()
    def SetPosition(self, track_id: "o", position: "x"):
        asyncio.get_running_loop().create_task(self.app.player.seek(position / 1_000_000, "absolute"))

    @method()
    def OpenUri(self, uri: "s"):
        self.app.load_results(uri, lambda: self.app.sc.fetch(uri))

    @signal()
    def Seeked(self) -> "x":
        return int((self.app.pos or 0) * 1_000_000)

    # --- properties ---
    @dbus_property(access=PropertyAccess.READ)
    def PlaybackStatus(self) -> "s":
        if self.app.current == -1:
            return "Stopped"
        return "Paused" if self.app.paused else "Playing"

    @dbus_property(access=PropertyAccess.READ)
    def Metadata(self) -> "a{sv}":
        return metadata(self.app)

    @dbus_property(access=PropertyAccess.READ)
    def Position(self) -> "x":
        return int((self.app.pos or 0) * 1_000_000)

    @dbus_property(access=PropertyAccess.READ)
    def Volume(self) -> "d":
        return self.app.volume / 100

    @dbus_property(access=PropertyAccess.READ)
    def Rate(self) -> "d":
        return 1.0

    @dbus_property(access=PropertyAccess.READ)
    def MinimumRate(self) -> "d":
        return 1.0

    @dbus_property(access=PropertyAccess.READ)
    def MaximumRate(self) -> "d":
        return 1.0

    @dbus_property(access=PropertyAccess.READ)
    def CanGoNext(self) -> "b":
        return self.app.current + 1 < len(self.app.queue)

    @dbus_property(access=PropertyAccess.READ)
    def CanGoPrevious(self) -> "b":
        return self.app.current > 0

    @dbus_property(access=PropertyAccess.READ)
    def CanPlay(self) -> "b":
        return bool(self.app.queue)

    @dbus_property(access=PropertyAccess.READ)
    def CanPause(self) -> "b":
        return self.app.current != -1

    @dbus_property(access=PropertyAccess.READ)
    def CanSeek(self) -> "b":
        return self.app.current != -1

    @dbus_property(access=PropertyAccess.READ)
    def CanControl(self) -> "b":
        return True


# Commands accepted by `sc ctl` (via org.sctui.Control.Command) → app action.
CONTROL_COMMANDS = {
    "play": "play",
    "pause": "pause",
    "toggle": "toggle_pause",
    "stop": "stop",
    "next": "next",
    "prev": "prev",
    "vol+": "volume(5)",
    "vol-": "volume(-5)",
    "seek+": "seek(5)",
    "seek-": "seek(-5)",
    "show": "show_now",
}


class Control(ServiceInterface):
    """sctui's own interface, for global hotkeys (`sc ctl <command>`)."""

    def __init__(self, app: "SCApp"):
        super().__init__("org.sctui.Control")
        self.app = app

    @method()
    def Command(self, command: "s") -> "s":
        action = CONTROL_COMMANDS.get(command)
        if not action:
            return "unknown command: " + command
        asyncio.get_running_loop().create_task(self.app.run_action(action))
        return "ok"


def metadata(app: "SCApp") -> dict:
    if not 0 <= app.current < len(app.queue):
        return {"mpris:trackid": Variant("o", "/org/mpris/MediaPlayer2/TrackList/NoTrack")}
    t = app.queue[app.current]
    md = {
        "mpris:trackid": Variant("o", f"/org/sctui/track/{app.gen}"),
        "xesam:title": Variant("s", t.title),
        "xesam:artist": Variant("as", [t.artist] if t.artist else []),
        "xesam:url": Variant("s", t.url),
    }
    dur = app.dur or t.duration
    if dur:
        md["mpris:length"] = Variant("x", int(dur * 1_000_000))
    art = app.art_path or t.artwork
    if art:
        md["mpris:artUrl"] = Variant("s", f"file://{art}" if art.startswith("/") else art)
    return md


class MprisServer:
    def __init__(self, app: "SCApp"):
        self.app = app
        self.bus: MessageBus | None = None
        self.player: Player | None = None
        self._last: dict = {}

    async def start(self) -> None:
        try:
            self.bus = await MessageBus().connect()
            self.player = Player(self.app)
            self.bus.export(PATH, Root(self.app))
            self.bus.export(PATH, self.player)
            self.bus.export(PATH, Control(self.app))
            # Newest instance wins (like Winamp's single-instance mode): take the name
            # from an older sctui, and let a newer one take it from us.
            await self.bus.request_name(
                BUS_NAME, NameFlag.REPLACE_EXISTING | NameFlag.ALLOW_REPLACEMENT)
        except Exception as e:  # no session bus etc. — media keys just won't work
            log.warning("MPRIS unavailable: %s", e)
            self.bus = None

    def update(self) -> None:
        """Emit PropertiesChanged for whatever changed (cheap to call often)."""
        if not self.bus or not self.player:
            return
        app = self.app
        state = {
            "PlaybackStatus": self.player.PlaybackStatus,
            "Metadata": (app.gen, app.current, app.dur, app.art_path, len(app.queue)),
            "CanGoNext": self.player.CanGoNext,
            "CanGoPrevious": self.player.CanGoPrevious,
            "Volume": app.volume,
        }
        changed = {}
        for k, v in state.items():
            if self._last.get(k) != v:
                changed[k] = getattr(self.player, k)
                if k == "Metadata":
                    changed[k] = metadata(app)
                elif k == "Volume":
                    changed[k] = app.volume / 100
        self._last = state
        if changed:
            self.player.emit_properties_changed(changed)

    def seeked(self) -> None:
        if self.player:
            self.player.Seeked()

    def stop(self) -> None:
        if self.bus:
            self.bus.disconnect()
