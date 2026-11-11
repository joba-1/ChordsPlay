#!/bin/bash
# Install or update the ChordsPlay web app as a systemd user unit (on host6).
# Copies this checkout's ChordsPlay/ and web/ to ~/.local/share/chordsplay-web/app,
# so later edits in the checkout don't reach the running service until the next
# install. See docs/web.md.
#
# First install: put CHORDSPLAY_MUSIC_DIRS=/path/to/music into
# ~/.config/chordsplay/web.env; the database is created at the first start.
set -euo pipefail

BASE=$HOME/.local/share/chordsplay-web
APP=$BASE/app
VENV=$BASE/venv
UNIT=chordsplay-web.service
UNIT_DIR=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(dirname "$HERE")

mkdir -p "$APP"
rsync -a --delete --exclude __pycache__ "$REPO/ChordsPlay" "$REPO/web" "$APP/"
git -C "$REPO" describe --always --dirty --tags > "$APP/VERSION" 2>/dev/null || true

[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet --upgrade -r "$REPO/web/requirements.txt"

install -D -m 644 "$HERE/$UNIT" "$UNIT_DIR/$UNIT"
systemctl --user daemon-reload
systemctl --user enable --quiet "$UNIT"
systemctl --user restart "$UNIT"
sleep 2
systemctl --user --no-pager --lines=5 status "$UNIT" || true
echo "Installed $(cat "$APP/VERSION" 2>/dev/null) - check: curl -s http://127.0.0.1:8616/health"
