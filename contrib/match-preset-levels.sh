#!/bin/bash
# Measure every Mustang Micro Plus preset with a test tone at the guitar input and
# import the level corrections into ChordsPlay. Afterwards a tone built on any preset
# starts at the same measured output level; the tone panel's level slider and its
# -6 -3 0 +3 +6 buttons then give exact variants.
#
# Needs (see docs/spec-amp.md, "Tones"):
#   * the amp on, connected by Bluetooth and USB, the amp agent installed;
#   * an adapter cable (3.5 mm stereo plug -> 6.35 mm socket) from the computer's
#     headphone output into the amp's plug, instead of the guitar;
#   * ChordsPlay closed (only one program may hold the amp's connection).
#
# Usage: contrib/match-preset-levels.sh [output dir]
#   SIGNAL_SINK=<PipeWire sink> if the headphone output isn't found automatically.
# Nothing is written to the amp: presets are read and auditioned on slot 100 only.
set -euo pipefail

HERE=$(cd "$(dirname "$0")/.." && pwd)
AGENT=$HOME/.local/share/chordsplay/maneline
PYTHON=$AGENT-venv/bin/python
OUT=${1:-$HOME/.local/share/chordsplay/levels}

if systemctl --user is-active --quiet chordsplay-amp-agent.service; then
    echo "ChordsPlay's amp agent is running: close ChordsPlay first." >&2
    exit 1
fi
mkdir -p "$OUT"
BACKUP=$OUT/backup-$(date +%Y%m%d-%H%M%S)

cd "$AGENT/scripts"
echo "== Reading the presets as they are now"
"$PYTHON" mmp_backup.py --out "$BACKUP"
echo "== Measuring every preset with the test tone (about 5 s each)"
"$PYTHON" mmp_level.py --signal-sink "${SIGNAL_SINK:-auto}" survey "$BACKUP" --slots 1-99
echo "== Importing the corrections into ChordsPlay"
"$HERE/venv/bin/python" "$HERE/chordsplay.py" --import-levels "$BACKUP/levels-signal.json"
echo "Done. Backup and measurements: $BACKUP"
