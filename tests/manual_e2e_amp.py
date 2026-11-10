'''
Manual end-to-end test of Mustang preset switching with the REAL amp (docs/test.md).

Runs the real chordsplay.py main() against a copy of your database, with the amp
agent unit managed by ChordsPlay. It switches the amp (slots 2 and 3) and puts it
back on slot 1 at the end. Not run by `unittest discover` (file name).

Prerequisites: the amp is on and connected (its audio sink exists), the agent is
installed (contrib/install-amp-agent.sh) and not running.

Run from the repo root; the song's audio goes into a null sink, the GUI into Xvfb:

  MOD=$(pactl load-module module-null-sink sink_name=cptest)
  PULSE_SINK=cptest xvfb-run -a venv/bin/python tests/manual_e2e_amp.py
  pactl unload-module $MOD

KEEP=1 keeps the temporary DB copy for inspection.

Expected: the agent starts by itself, the amp goes to slot 2 at the start and slot 3
at 8 s, "G" stores slot 3 at 0:11.5 instantly (no GUI stall), the tone cue at 14 s
plays "CP e2e Dome +3" as an audition on the live slot, the amp ends on the preset it
started on,
and the agent unit is inactive afterwards.
'''

import faulthandler
import importlib.util
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import ChordsPlay.gui   # noqa: E402

AGENT = 'http://localhost:8081/api/micro-plus'
SLOT_A = (2, 'Stadium Lead')        # adjust to your amp's preset names
SLOT_B = (3, 'Swirling Echoes')
TONE = ('CP e2e Dome +3', 21, 'Echo Dome', 3.0)     # name, base slot, base name, level dB


def main():
    work = tempfile.mkdtemp(prefix='cp-e2e-')
    db_path, cfg_path = os.path.join(work, 'test.db'), os.path.join(work, 'test.cfg')
    # consistent copy of the real DB; mark every song as scanned so nothing goes to Ultimate Guitar
    src = sqlite3.connect('file:' + os.path.expanduser('~/.chordsplay.py.db') + '?mode=ro', uri=True)
    dst = sqlite3.connect(db_path)
    src.backup(dst)
    dst.execute("UPDATE songs SET chord_update = datetime('now')")
    dst.commit()
    dst.close()
    src.close()
    # no sync_url: nothing is pushed to the central service
    with open(cfg_path, 'w') as f:
        f.write('[chordsplay.py]\namp_url = http://localhost:8081\n'
                'amp_unit = chordsplay-amp-agent.service\n')

    spec = importlib.util.spec_from_file_location('cp', os.path.join(REPO, 'chordsplay.py'))
    cp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cp)
    logging.getLogger('ChordsPlay.amp').setLevel(logging.DEBUG)
    events = []
    # the preset the amp is on before the test, to return to it at the end; read before
    # the app starts its agent (only one program may hold the amp's connection)
    agent = os.path.expanduser('~/.local/share/chordsplay/maneline')
    status = subprocess.run([agent + '-venv/bin/python', agent + '/scripts/mmp_ble.py', 'status'],
                            capture_output=True, text=True, timeout=60).stdout.strip().splitlines()
    results = {'original': json.loads(status[-1])['preset'] if status else 1}
    print('amp starts on preset', results['original'])
    t0 = time.monotonic()

    def note(*a):
        events.append('%5.1fs %s | title=%r' % (time.monotonic() - t0, ' '.join(map(str, a)),
                                               cp.title_text))

    def agent_preset():
        try:
            return json.load(urllib.request.urlopen(AGENT, timeout=3)).get('preset')
        except Exception as e:
            return 'n/a ({})'.format(e.__class__.__name__)

    orig = ChordsPlay.gui.Gui.mainloop

    def mainloop(gui, *a):
        def start():
            cp.playlist.jump(0)
            song = cp.player.song_id
            cp.db.setSongPreset(song, 0, *SLOT_A)
            cp.db.setSongPreset(song, 8000, *SLOT_B)
            tone_id = cp.db.saveTone(TONE[0], TONE[1], TONE[2], TONE[3], {})
            cp.db.setSongTone(song, 14000, tone_id, cp.amp.live_slot)
            cp.cues.reload()
            note('playing song', song)

        def check(label):
            note(label, 'pos=%dms' % cp.playPosition(), 'amp=%s' % agent_preset())

        def learn():
            t = time.monotonic()
            cp.learnPreset()
            note('G took %.2fs' % (time.monotonic() - t), cp.db.getSongPresets(cp.player.song_id))

        def tone_check():
            state = cp.amp.snapshot()['state']
            note('tone cue', 'pos=%dms' % cp.playPosition(), 'audition=%s' % state.get('audition'))
            results['tone'] = (state.get('audition') or {}).get('name') == TONE[0]

        steps = ((1000, start), (6000, lambda: check('after start')), (9000, lambda: check('t~8s')),
                 (12500, lambda: check('t~11.5s')), (13000, learn),
                 (16000, lambda: check('after learn')), (19000, tone_check),
                 (19500, lambda: cp.amp.request(results.get('original', 1))),
                 (23000, lambda: check('restored')),
                 (23500, gui.quit))
        for delay, step in steps:
            gui.after(delay, step)
        return orig(gui, *a)

    ChordsPlay.gui.Gui.mainloop = mainloop
    sys.argv[0] = 'chordsplay.py'       # the config section is named after the program
    faulthandler.dump_traceback_later(90, exit=True)
    rc = cp.main(['-c', cfg_path, '-d', db_path])
    note('main returned', rc)
    print('\n'.join(events))
    print('tone cue played as audition:', 'PASS' if results.get('tone') else 'FAIL')
    if os.environ.get('KEEP'):
        print('work dir (test DB copy):', work)
    else:
        shutil.rmtree(work)


if __name__ == '__main__':
    main()
