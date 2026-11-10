#!/bin/bash
# Install the amp bridge of the ChordsPlay web app (web/bridge.py) as a systemd user
# unit on the computer the Mustang Micro Plus is paired with, next to the amp agent
# (contrib/install-amp-agent.sh). It follows the web app over HTTPS and switches the
# amp's presets for the playing song. Re-run to update. See docs/web.md.
#
# Needs the line from the web app's tone dialog (key g, "Bridge link…") in
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
AGENT_ENV=$HOME/.config/chordsplay/amp-agent.env

if ! grep -qE '^CHORDSPLAY_BRIDGE_URL=https?://.+/d/bridge/[^/]+/?$' "$ENV_FILE" 2>/dev/null; then
    echo "Put the line from the web app's tone dialog (g, \"Bridge link…\") into $ENV_FILE first." >&2
    exit 1
fi
chmod 600 "$ENV_FILE"       # the token is the bridge's only key

# standard library only: the bridge and the Tk app's amp module, no venv
mkdir -p "$DEST/ChordsPlay" "$HOME/.config/systemd/user"
install -m 644 "$HERE/ChordsPlay/__init__.py" "$HERE/ChordsPlay/amp.py" "$DEST/ChordsPlay/"
install -m 644 "$HERE/web/bridge.py" "$DEST/"

PORT=$(sed -n 's/^AMP_AGENT_PORT=//p' "$AGENT_ENV" 2>/dev/null || true)
cat > "$HOME/.config/systemd/user/$UNIT" <<EOF
[Unit]
Description=ChordsPlay amp bridge (web app -> amp agent)
After=network-online.target

[Service]
WorkingDirectory=%h/.local/share/chordsplay/bridge
Environment=AMP_URL=http://localhost:${PORT:-8081}
EnvironmentFile=%h/.config/chordsplay/bridge.env
ExecStart=/usr/bin/env python3 bridge.py
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --quiet "$UNIT"
systemctl --user restart "$UNIT"
sleep 2
systemctl --user --no-pager --lines=5 status "$UNIT" | tail -6
