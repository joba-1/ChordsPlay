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
#       every preset when prompted (two takes of 4 s each, averaged; a reference
#       preset shows your drift).
#
# Enter pauses after the current preset (the amp stays connected); Enter again goes
# on, with the practice takes first. Ctrl+C stops; run the script again later and it
# goes on with the same backup where it stopped. After a pause or a stop it first
# measures the last 2 presets again (OVERLAP=1 for one) and takes the difference in
# your playing off the new levels. The corrections are imported once every preset is measured.
# FRESH=1 starts over with a new backup.
#
# Usage: contrib/match-preset-levels.sh [output dir]
#   SIGNAL_SINK=<PipeWire sink> if the headphone output isn't found automatically.
#   MEASURE_SLOTS=1-20 measures only these slots.
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
if [ -n "${PLAYED:-}" ]; then
    METHOD=played
    MEASURE=(--played)
else
    METHOD=signal
    MEASURE=(--signal-sink "${SIGNAL_SINK:-auto}")
fi
# the backup of a survey that was stopped, until its levels are imported
CURRENT=$OUT/current-$METHOD
if [ -n "${FRESH:-}" ]; then
    rm -f "$CURRENT"
fi

cd "$AGENT/scripts"
if [ -s "$CURRENT" ] && [ -d "$(cat "$CURRENT")" ]; then
    BACKUP=$(cat "$CURRENT")
    echo "== Going on with $BACKUP (FRESH=1 starts over)"
else
    BACKUP=$OUT/backup-$(date +%Y%m%d-%H%M%S)
    echo "== Reading the presets as they are now"
    "$PYTHON" mmp_backup.py --out "$BACKUP"
    echo "$BACKUP" > "$CURRENT"
fi
if [ "$METHOD" = played ]; then
    echo "== Measuring every preset while you play the same riff (4 s each, prompted)"
else
    echo "== Measuring every preset with the test tone (about 5 s each)"
fi
status=0
"$PYTHON" mmp_level.py "${MEASURE[@]}" survey "$BACKUP" --slots "${MEASURE_SLOTS:-1-99}" \
    --overlap "${OVERLAP:-2}" || status=$?
if [ $status -eq 3 ]; then
    echo "Stopped. Run $0 again to go on; nothing is imported yet."
    exit 3
elif [ ! -f "$BACKUP/levels-$METHOD.json" ]; then
    exit $status
fi
echo "== Importing the corrections into ChordsPlay"
"$HERE/venv/bin/python" "$HERE/chordsplay.py" --import-levels "$BACKUP/levels-$METHOD.json"
rm -f "$CURRENT"
echo "Done. Backup and measurements: $BACKUP"
exit $status
