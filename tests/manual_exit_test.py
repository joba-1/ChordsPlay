'''
Manual test: ChordsPlay exits cleanly on every way out (docs/test.md).

For each exit path, run the real program (tests/exit_app.py) on a copy of your
database while the scanner hangs in a network request, then check:
  * the process ends within EXIT_LIMIT seconds (so playback stops),
  * no screen-saver inhibitor and no "tail --pid" helper is left over,
  * the amp agent unit is not left running (if amp_unit is configured and the amp
    is connected, ChordsPlay starts it),
  * the database copy is intact.

Nothing is played aloud (null sink), nothing is fetched from Ultimate Guitar and
nothing is synced; stored amp presets are removed from the copy, so the amp is
not switched. Needs Xvfb.

  venv/bin/python tests/manual_exit_test.py [part of a case name ...]
'''

import os
import pty
import select
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON = sys.executable
UNIT = 'chordsplay-amp-agent.service'
EXIT_LIMIT = 5.0        # s until the process is gone (the scanner hangs: up to SCAN_JOIN_S = 3 s)
SOUND_LIMIT = 1.0       # s until the song's audio stream is gone
CASES = [('q key', 'q', None), ('window close', 'wmclose', None),
         ('window close with dialog open', 'dialog-wmclose', None),
         ('SIGHUP (terminal closed)', None, signal.SIGHUP),
         ('SIGTERM', None, signal.SIGTERM), ('SIGINT (Ctrl-C)', None, signal.SIGINT),
         ('terminal window closed (pty)', 'pty', None)]


def run(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, **kw)


def processes():
    '''{pid: args} of this user's processes.'''
    out = run(['ps', '-u', str(os.getuid()), '-o', 'pid=,args=']).stdout
    result = {}
    for line in out.splitlines():
        pid, _, args = line.strip().partition(' ')
        result[int(pid)] = args
    return result


def leftovers(app_pid):
    marker = '--pid={}'.format(app_pid)
    return [args for args in processes().values() if marker in args]


def playing(app_pid):
    '''True while the app has an audio stream (sink input) open.'''
    out = run(['pactl', 'list', 'sink-inputs']).stdout
    return 'application.process.id = "{}"'.format(app_pid) in out


def unit_active():
    return run(['systemctl', '--user', 'is-active', '--quiet', UNIT]).returncode == 0


def prepare(work):
    db_path = os.path.join(work, 'test.db')
    src = sqlite3.connect('file:' + os.path.expanduser('~/.chordsplay.py.db') + '?mode=ro', uri=True)
    dst = sqlite3.connect(db_path)
    src.backup(dst)
    src.close()
    for table in ('song_presets', 'artist_presets'):
        try:
            dst.execute('DELETE FROM ' + table)
        except sqlite3.OperationalError:
            pass
    # some songs unchecked, so the scanner starts fetching (hanging_request)
    dst.execute("UPDATE songs SET chord_update = datetime('now')")
    dst.execute("UPDATE songs SET chord_update = NULL WHERE id IN (SELECT id FROM songs LIMIT 20)")
    dst.commit()
    dst.close()
    cfg_path = os.path.join(work, 'test.cfg')
    with open(cfg_path, 'w') as f:
        f.write('[chordsplay.py]\namp_url = http://localhost:8081\namp_unit = {}\n'.format(UNIT))
    return cfg_path, db_path


def pty_case(name, display, cfg, db):
    '''Run the app on a pseudo-terminal and close it, like closing a terminal window:
    the kernel sends SIGHUP, and from then on every write to the terminal fails.'''
    pid, master = pty.fork()
    if pid == 0:                                    # child: session leader on the pty
        env = dict(os.environ, DISPLAY=display, PULSE_SINK='cptest')
        env.pop('WAYLAND_DISPLAY', None)
        os.execve(PYTHON, [PYTHON, os.path.join(REPO, 'tests', 'exit_app.py'), cfg, db], env)
    out = b''
    deadline = time.monotonic() + 300
    while b'READY' not in out and time.monotonic() < deadline:
        if select.select([master], [], [], 1)[0]:
            try:
                out += os.read(master, 4096)
            except OSError:
                break
    if b'READY' not in out:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
        return name, False, 'never got READY'
    inhibitors = leftovers(pid)
    time.sleep(1)
    was_playing = playing(pid)
    t0 = time.monotonic()
    os.close(master)                                # the terminal window closes
    silent = None
    status = None
    while time.monotonic() < t0 + EXIT_LIMIT + 10:
        done, status = os.waitpid(pid, os.WNOHANG)
        if done:
            break
        if silent is None and not playing(pid):
            silent = time.monotonic() - t0
        time.sleep(0.05)
    took = time.monotonic() - t0
    if status is None or not done:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
        return name, False, 'did not exit'
    if silent is None:
        silent = took
    time.sleep(1.5)
    problems = []
    if took > EXIT_LIMIT:
        problems.append('exit took {:.1f} s'.format(took))
    if not was_playing:
        problems.append('was not playing before the exit')
    elif silent > SOUND_LIMIT:
        problems.append('sound stopped only after {:.1f} s'.format(silent))
    if not inhibitors:
        problems.append('no inhibitor was running')
    if leftovers(pid):
        problems.append('left over: {}'.format(leftovers(pid)))
    if unit_active():
        problems.append('amp agent unit still active')
    check = sqlite3.connect(db).execute('PRAGMA quick_check').fetchone()[0]
    if check != 'ok':
        problems.append('db: ' + check)
    detail = 'silent after {:.1f} s, process gone after {:.1f} s, exit status {}'.format(
        silent, took, os.waitstatus_to_exitcode(status))
    if problems:
        detail += ' | ' + '; '.join(problems)
    return name, not problems, detail


def one_case(name, trigger, signum, display, cfg, db):
    if trigger == 'pty':
        return pty_case(name, display, cfg, db)
    env = dict(os.environ, DISPLAY=display, PULSE_SINK='cptest')
    env.pop('WAYLAND_DISPLAY', None)
    if trigger:
        env['CP_TRIGGER'] = trigger
    app = subprocess.Popen([PYTHON, os.path.join(REPO, 'tests', 'exit_app.py'), cfg, db],
                           env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    lines = []
    ready = None
    deadline = time.monotonic() + 300     # the folder walk comes first, slow over NFS
    while time.monotonic() < deadline:
        line = app.stdout.readline()
        if not line:
            break
        lines.append(line)
        if line.startswith('READY'):
            ready = time.monotonic()
            break
    if ready is None:
        app.kill()
        return name, False, 'never got READY:\n' + ''.join(lines[-15:])
    inhibitors = leftovers(app.pid)
    if signum is not None:
        time.sleep(1)           # let it play a moment
        t0 = time.monotonic()
        app.send_signal(signum)
    else:
        # the trigger fires 0.5 s after READY; closing with the dialog open 0.8 s later
        t0 = ready + 0.5 + (0.8 if trigger == 'dialog-wmclose' else 0)
    was_playing = playing(app.pid)
    silent = None
    while app.poll() is None and time.monotonic() < t0 + EXIT_LIMIT + 10:
        if silent is None and time.monotonic() >= t0 and not playing(app.pid):
            silent = time.monotonic() - t0
        time.sleep(0.05)
    try:
        rest, _ = app.communicate(timeout=1)
    except subprocess.TimeoutExpired:
        app.kill()
        rest, _ = app.communicate()
        return name, False, 'did not exit\n' + ''.join(lines[-10:]) + rest[-2000:]
    took = time.monotonic() - t0
    lines.append(rest)
    time.sleep(1.5)             # "tail --pid" checks about once a second
    left = leftovers(app.pid)
    problems = []
    if took > EXIT_LIMIT:
        problems.append('exit took {:.1f} s'.format(took))
    if not was_playing:
        problems.append('was not playing before the exit')
    elif silent is None or silent > SOUND_LIMIT:
        problems.append('sound stopped only after {}'.format(
            '{:.1f} s'.format(silent) if silent is not None else 'the process ended'))
    if not inhibitors:
        problems.append('no inhibitor was running')
    if left:
        problems.append('left over: {}'.format(left))
    if unit_active():
        problems.append('amp agent unit still active')
    check = sqlite3.connect(db).execute('PRAGMA quick_check').fetchone()[0]
    if check != 'ok':
        problems.append('db: ' + check)
    log = ''.join(lines)
    detail = 'silent after {}, process gone after {:.1f} s, rc {}{}'.format(
        '{:.1f} s'.format(silent) if silent is not None else '?', took, app.returncode,
        ', scanner left hanging' if 'scanner still busy' in log else '')
    if problems:
        detail += ' | ' + '; '.join(problems) + '\n' + log[-1500:]
    return name, not problems, detail


def main():
    if unit_active():
        sys.exit('{} is running; stop it first so the test can see who starts it'.format(UNIT))
    work = tempfile.mkdtemp(prefix='cp-exit-')
    xvfb = subprocess.Popen(['Xvfb', ':97', '-screen', '0', '1280x800x24'],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    sink = run(['pactl', 'load-module', 'module-null-sink', 'sink_name=cptest']).stdout.strip()
    results = []
    try:
        time.sleep(1)
        wanted = sys.argv[1:]       # optional: only cases whose name contains one of these
        for name, trigger, signum in CASES:
            if wanted and not any(w in name for w in wanted):
                continue
            cfg, db = prepare(work)
            results.append(one_case(name, trigger, signum, ':97', cfg, db))
            print('{:32s} {} {}'.format(results[-1][0], 'ok  ' if results[-1][1] else 'FAIL',
                                        results[-1][2]), flush=True)
    finally:
        if sink:
            run(['pactl', 'unload-module', sink])
        xvfb.terminate()
        xvfb.wait()
        for f in os.listdir(work):
            os.remove(os.path.join(work, f))
        os.rmdir(work)
    sys.exit(0 if all(ok for _, ok, _ in results) else 1)


if __name__ == '__main__':
    main()
