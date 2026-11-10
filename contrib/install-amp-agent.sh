#!/bin/bash
# Install the Mustang Micro Plus amp agent (maneline mmp_web.py) for ChordsPlay
# as a systemd user unit. Re-run to update. See docs/spec-amp.md.
#
# Defaults (override via environment):
#   MANELINE_REPO    git URL       https://github.com/joba-1/maneline.git
#   MANELINE_BRANCH  branch/commit micro-plus-idle-disconnect  (Linux LE bearer + idle disconnect,
#                                  upstream PRs #139/#140; switch to main once merged)
#   AMP_PORT         agent port    8081, or what an earlier run stored in $ENV_FILE
set -euo pipefail

REPO=${MANELINE_REPO:-https://github.com/joba-1/maneline.git}
BRANCH=${MANELINE_BRANCH:-micro-plus-idle-disconnect}
BASE=$HOME/.local/share/chordsplay
DEST=$BASE/maneline
VENV=$BASE/maneline-venv
UNIT=chordsplay-amp-agent.service
UNIT_DIR=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
HERE=$(cd "$(dirname "$0")" && pwd)
# Read by the unit (EnvironmentFile=%h/.config/...), so not XDG_CONFIG_HOME
ENV_FILE=$HOME/.config/chordsplay/amp-agent.env

PORT=${AMP_PORT:-$(sed -n 's/^AMP_AGENT_PORT=//p' "$ENV_FILE" 2>/dev/null || true)}
PORT=${PORT:-8081}
# The agent listens on all interfaces, so any listener on the port blocks it
if ! systemctl --user is-active --quiet "$UNIT" && [ -n "$(ss -ltnH "sport = :$PORT")" ]; then
    echo "Port $PORT is already in use; re-run with AMP_PORT=<free port>:" >&2
    ss -ltnH "sport = :$PORT" >&2
    exit 1
fi
if [ -n "${AMP_PORT:-}" ]; then
    install -D -m 644 /dev/null "$ENV_FILE"
    echo "AMP_AGENT_PORT=$AMP_PORT" > "$ENV_FILE"
fi

mkdir -p "$BASE"
if [ -d "$DEST/.git" ]; then
    git -C "$DEST" fetch --quiet "$REPO" "$BRANCH"
    git -C "$DEST" checkout --quiet --detach FETCH_HEAD
else
    git clone --quiet --branch "$BRANCH" "$REPO" "$DEST"
fi
echo "maneline: $(git -C "$DEST" log --oneline -1)"

# Versions tested with the Micro Plus on firmware 1.1.11 and BlueZ 5.87
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet --upgrade 'bleak==3.0.2' 'dbus-fast==5.2.0'

install -D -m 644 "$HERE/$UNIT" "$UNIT_DIR/$UNIT"
systemctl --user daemon-reload
if systemctl --user is-active --quiet "$UNIT"; then
    systemctl --user restart "$UNIT"
fi

cat <<MSG
Installed $UNIT (not enabled: ChordsPlay starts it while the amp is connected).

One-time BlueZ setup (Experimental = true, pairing over LE as well):
  $DEST/docs/MUSTANG_MICRO_PLUS_LINUX.md

Add to ~/.chordsplay.py.cfg, section [chordsplay.py]:
  amp_url  = http://localhost:$PORT
  amp_unit = $UNIT
MSG
