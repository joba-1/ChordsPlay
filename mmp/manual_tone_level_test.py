# SPDX-FileCopyrightText: 2016-2026 joba1
# SPDX-License-Identifier: AGPL-3.0-or-later
'''
Manual hardware test: ChordsPlay's tones change the amp's output level as intended
(docs/test.md). The whole path: ChordsPlay's AmpClient builds tones from a stored
preset (fetched through the agent), the agent auditions them on the live slot,
and the amp's USB output is measured (maneline's mmp_level).

Needs:
* the amp on, connected by Bluetooth and USB;
* the amp plugged into an instrument with its volume at zero (the noise method);
* the agent installed (mmp/install-amp-agent.sh).

Nothing is saved on the amp; at the end it selects the preset it started on.
Needs numpy (with the package maneline-mmp: python3-numpy / python-numpy):

  python3 mmp/manual_tone_level_test.py [slot]

Expected: every level offset measures within 1 dB of what was set.
'''

import os
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# maneline's scripts: the checkout mmp/install-amp-agent.sh made, else the package maneline-mmp
AGENT = os.path.expanduser('~/.local/share/chordsplay/maneline/scripts')
if not os.path.isdir(AGENT):
    AGENT = '/usr/share/maneline-mmp/scripts'
sys.path.insert(0, REPO)
sys.path.insert(0, AGENT)
from ChordsPlay.amp import AgentControl, AmpClient, build_tone   # noqa: E402
from mmp_level import level_db, record                            # noqa: E402

URL = 'http://localhost:8081'
UNIT = 'chordsplay-amp-agent.service'
OFFSETS = (0, 3, 6, -3, -6, 0)
TOLERANCE = 1.0


def wait(predicate, timeout, what):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(0.1)
    sys.exit('timed out waiting for ' + what)


def main():
    base_slot = int(sys.argv[1]) if len(sys.argv) > 1 else 19
    control = AgentControl(UNIT)
    client = AmpClient(URL, poll=0.5, control=control, live_slot=100).start()
    try:
        first = wait(lambda: client.snapshot()['state'] or None, 15, 'the agent')
        # slot 100 is where tones are auditioned: not a preset to go back to
        original = first.get('preset') if first.get('preset') not in (None, 100) else base_slot
        if not first.get('connected'):
            client.request(original)        # the agent sleeps after 5 idle minutes; a switch wakes it
        wait(lambda: client.snapshot()['state'].get('connected'), 60, 'the agent to connect to the amp')
        base = wait(lambda: client.preset(base_slot), 30, 'the base preset')
        print('Base {} "{}", amp on preset {}'.format(base_slot, base['info']['displayName'], original))
        levels = []
        for offset in OFFSETS:
            # gate off: with the instrument volume at zero only the amp's noise passes it
            tone = build_tone(base, offset, {'amp.gatePreset': 'off'}, name='CP test {:+d}'.format(offset))
            client.audition(tone)
            wait(lambda: (client.snapshot()['state'].get('audition') or {}).get('name') == tone['info']['displayName']
                 and client.snapshot()['busy'] is None, 30, 'the audition')
            time.sleep(0.7)
            level = level_db(record(3.0))
            levels.append((offset, level))
            print('  tone {:+d} dB -> {:7.1f} dBFS'.format(offset, level), flush=True)
        reference = levels[0][1]
        ok = True
        for offset, level in levels[1:-1]:
            delta = level - reference
            passed = abs(delta - offset) <= TOLERANCE
            ok &= passed
            print('  {:+d} dB: measured {:+.1f} dB {}'.format(offset, delta, 'ok' if passed else 'FAIL'))
        drift = levels[-1][1] - reference
        ok &= abs(drift) <= TOLERANCE
        print('  reference drift {:+.2f} dB'.format(drift))
        client.request(original)
        wait(lambda: client.snapshot()['state'].get('preset') == original
             and not client.snapshot()['state'].get('audition'), 30, 'the original preset')
        print('Back on preset {}: {}'.format(original, 'PASS' if ok else 'FAIL'))
        return 0 if ok else 1
    finally:
        client.stop()       # stops the agent unit if this test started it


if __name__ == '__main__':
    sys.exit(main())
