"""Config loading: ~/.config/sctui/config.toml plus environment overrides."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "sctui" / "config.toml"


@dataclass
class Config:
    username: str | None = None
    oauth_token: str | None = None
    volume: int = 80
    # "ansi-dark" uses your terminal colors, so it follows the Omarchy theme.
    theme: str = "ansi-dark"
    visualizer: bool = True
    notifications: bool = True


def load() -> Config:
    data: dict = {}
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("rb") as f:
            data = tomllib.load(f)
    return Config(
        username=os.environ.get("SC_USERNAME") or data.get("username") or None,
        oauth_token=os.environ.get("SC_OAUTH_TOKEN") or data.get("oauth_token") or None,
        volume=int(data.get("volume", 80)),
        theme=data.get("theme", "ansi-dark"),
        visualizer=bool(data.get("visualizer", True)),
        notifications=bool(data.get("notifications", True)),
    )
