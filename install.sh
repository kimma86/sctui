#!/usr/bin/env bash
# Install sctui as a uv tool plus a launcher entry and icon.
set -euo pipefail
cd "$(dirname "$0")"
uv tool install --editable . --force
install -Dm644 share/sctui.desktop "$HOME/.local/share/applications/sctui.desktop"
install -Dm644 share/sctui.svg "$HOME/.local/share/icons/hicolor/scalable/apps/sctui.svg"
gtk-update-icon-cache -q "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
update-desktop-database -q "$HOME/.local/share/applications" 2>/dev/null || true
echo "Installed. Launch 'SoundCloud' from the app launcher, or run: sc"
