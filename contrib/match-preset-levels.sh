#!/bin/bash
# Measure every Mustang Micro Plus preset with a test tone at the guitar input and
# import the level corrections into ChordsPlay. Afterwards a tone built on any preset
# starts at the same measured output level; the tone panel's level slider and its
# -6 -3 0 +3 +6 buttons then give exact variants.
#
# Needs (see docs/spec-amp.md, "Tones"):
#   * the amp on, connected by Bluetooth and USB, the amp agent installed;
#   * ChordsPlay closed (only one program may hold the amp's connection);
#   * a playing-level signal at the guitar input, one of:
#     - signal method (default): an adapter cable (3.5 mm stereo plug -> 6.35 mm
#       socket) from the computer's headphone output into the amp's plug;
#     - played method (PLAYED=1): the guitar, and you play the same short riff for
#       every preset when prompted (4 s each, about +-1 dB). Tip: --slots in
#       MEASURE_SLOTS to do it in parts, e.g. MEASURE_SLOTS=1-20.
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
if [ -n "${PLAYED:-}" ]; then
    echo "== Measuring every preset while you play the same riff (4 s each, prompted)"
    "$PYTHON" mmp_level.py --played survey "$BACKUP" --slots "${MEASURE_SLOTS:-1-99}"
    METHOD=played
else
    echo "== Measuring every preset with the test tone (about 5 s each)"
    "$PYTHON" mmp_level.py --signal-sink "${SIGNAL_SINK:-auto}" survey "$BACKUP" --slots "${MEASURE_SLOTS:-1-99}"
    METHOD=signal
fi
echo "== Importing the corrections into ChordsPlay"
"$HERE/venv/bin/python" "$HERE/chordsplay.py" --import-levels "$BACKUP/levels-$METHOD.json"
echo "Done. Backup and measurements: $BACKUP"
