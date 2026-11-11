#!/bin/bash
# SPDX-FileCopyrightText: 2016-2026 joba1
# SPDX-License-Identifier: AGPL-3.0-or-later
# Install the amp bridge of the ChordsPlay web app (web/bridge.py) as a systemd user
# unit on the computer the Mustang Micro Plus is paired with, next to the amp agent
# (mmp/install-amp-agent.sh). It follows the web app over HTTPS and switches the
# amp's presets for the playing song. Re-run to update. See docs/web.md.
#
# Needs the line from the web app's tone dialog (key g, "Set up amp control…") in
#   ~/.config/chordsplay/bridge.env:  CHORDSPLAY_BRIDGE_URL=https://.../chordsplay/d/bridge/<token>/
# Optional there: AMP_URL (default: the agent's port from its install), AMP_UNIT
# (the agent's unit, started while the amp's audio sink exists), AMP_SINK,
# AMP_DEFAULT_SLOT, AMP_LEAD_MS.
set -euo pipefail

HERE=$(cd "$(dirname "$0")/.." && pwd)
DEST=$HOME/.local/share/chordsplay/bridge
UNIT=chordsplay-amp-bridge.service
# Read by the unit (EnvironmentFile=%h/.config/...), so not XDG_CONFIG_HOME
ENV_FILE=$HOME/.config/chordsplay/bridge.env

if ! grep -qE '^CHORDSPLAY_BRIDGE_URL=https?://.+/d/bridge/[^/]+/?$' "$ENV_FILE" 2>/dev/null; then
    echo "Put the line from the web app's tone dialog (g, \"Set up amp control…\") into $ENV_FILE first." >&2
    exit 1
fi
chmod 600 "$ENV_FILE"       # the token is the bridge's only key

# standard library only: the bridge and the amp module, no venv
mkdir -p "$DEST/ChordsPlay" "$DEST/mmp"
rm -f "$DEST/bridge.py"     # where it was before the Mustang parts moved to mmp/
install -m 644 "$HERE/ChordsPlay/__init__.py" "$HERE/ChordsPlay/amp.py" "$DEST/ChordsPlay/"
install -m 644 "$HERE/mmp/__init__.py" "$HERE/mmp/bridge.py" "$DEST/mmp/"
install -D -m 644 "$HERE/mmp/$UNIT" "$HOME/.config/systemd/user/$UNIT"

systemctl --user daemon-reload
systemctl --user enable --quiet "$UNIT"
systemctl --user restart "$UNIT"
sleep 2
systemctl --user --no-pager --lines=5 status "$UNIT" | tail -6
