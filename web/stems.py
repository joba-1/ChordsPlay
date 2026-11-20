# SPDX-FileCopyrightText: 2026 joba1
# SPDX-License-Identifier: AGPL-3.0-or-later
'''
Stems for the practice mix: a song split into its instruments, once, by an external
command (CHORDSPLAY_STEMS_COMMAND, e.g. contrib/stems/chordsplay-stems), kept as one
multichannel Opus file per song with a manifest. See docs/spec-practice.md.

One worker thread runs one split at a time (the GPU is the bottleneck), oldest request
first. The table `stems` holds the state per song; the files are a cache that can be
deleted any time.

  python -m web.stems queue --user NAME --tag KEY   queue the user's songs with that tag
  python -m web.stems wait [--timeout S]            until nothing is queued or running
  python -m web.stems list                          the state of every split song

The server's worker does the splitting (it looks for new rows every minute); these only
queue and watch. Settings as for the server: CHORDSPLAY_WEB_DB, CHORDSPLAY_STEMS_DIR.
'''

import argparse
import json
import logging
import os
import shlex
import subprocess
import sys
import threading
import time

import sqlite3

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS stems (
    song_id  int not null primary key references songs(id) on delete cascade,
    source   text,
    state    text not null,
    error    text,
    queued   real,
    finished real,
    seconds  real);
"""

STATES = ('queued', 'running', 'ready', 'failed')
ERROR_MAX = 300         # characters of the command's message kept as the error


def song_file(conn, song_id):
    '''The song's audio file, or None.'''
    row = conn.execute("""SELECT l.name, ls.filename FROM list_songs ls
                          JOIN song_lists l ON ls.song_lists_id = l.id
                          WHERE ls.song_id = ? LIMIT 1""", (song_id,)).fetchone()
    return os.path.join(row[0], row[1]) if row else None


def source_sig(path):
    '''Size and mtime of a file: a change means the song must be split again.'''
    try:
        st = os.stat(path)
    except (OSError, TypeError):
        return None
    return '{}:{}'.format(st.st_size, int(st.st_mtime))


def read_manifest(path):
    '''The manifest if it is usable: {channels, stems: [{name, pair, group?}], ...}, else None.'''
    try:
        with open(path) as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None
    stems = doc.get('stems') if isinstance(doc, dict) else None
    if not isinstance(stems, list) or not stems or not isinstance(doc.get('channels'), int):
        return None
    def pair_ok(p):
        return isinstance(p, int) and not isinstance(p, bool) and 0 <= p * 2 + 1 < doc['channels']
    for s in stems:
        if not (isinstance(s, dict) and isinstance(s.get('name'), str) and pair_ok(s.get('pair'))):
            return None
    if 'original' in doc and not (isinstance(doc['original'], dict) and pair_ok(doc['original'].get('pair'))):
        return None
    return doc


class Stems:
    def __init__(self, db_path, command, folder, timeout=1800):
        self.db_path = db_path
        self.command = shlex.split(command) if command else []
        self.folder = os.path.abspath(os.path.expanduser(folder))
        self.timeout = timeout
        self.wake = threading.Event()
        self.thread = None

    @property
    def offered(self):
        return bool(self.command)

    def paths(self, song_id):
        base = os.path.join(self.folder, str(int(song_id)))
        return base + '.webm', base + '.json'

    def peaks_path(self, song_id):
        '''The waveforms (optional: splitters before 1.9.0 wrote none).'''
        return os.path.join(self.folder, str(int(song_id)) + '.peaks.json')

    def status(self, conn, song_id):
        '''{offered, state, error, seconds, manifest, peaks}; state 'none' when not split (or
        the song's file changed since); peaks: the waveforms exist.'''
        out = {'offered': self.offered, 'state': 'none', 'error': None, 'seconds': None, 'manifest': None,
               'peaks': False}
        row = conn.execute("SELECT source, state, error, seconds FROM stems WHERE song_id = ?",
                           (song_id,)).fetchone()
        if row is None:
            return out
        source, state, error, seconds = row
        if state == 'ready':
            webm, manifest = self.paths(song_id)
            doc = read_manifest(manifest) if os.path.isfile(webm) else None
            if doc is None or source != source_sig(song_file(conn, song_id)):
                return out
            out['manifest'] = doc
            out['peaks'] = os.path.isfile(self.peaks_path(song_id))
        out.update(state=state, error=error, seconds=seconds)
        return out

    def request(self, conn, song_id, again=False):
        '''Queue the split unless it is ready (again: even then), queued or running.
        Returns the status.'''
        current = self.status(conn, song_id)
        if current['state'] in ('queued', 'running') or (current['state'] == 'ready' and not again):
            return current
        if song_file(conn, song_id) is None:
            raise KeyError(song_id)
        conn.execute("""INSERT OR REPLACE INTO stems (song_id, source, state, error, queued)
                        VALUES (?, NULL, 'queued', NULL, ?)""", (song_id, time.time()))
        conn.commit()
        self.wake.set()
        return self.status(conn, song_id)

    # --- the worker -------------------------------------------------------------

    def start(self):
        if not self.offered or self.thread:
            return
        os.makedirs(self.folder, exist_ok=True)
        conn = self.connect()
        try:
            conn.execute("UPDATE stems SET state = 'queued' WHERE state = 'running'")   # cut off by a restart
            conn.commit()
        finally:
            conn.close()
        self.thread = threading.Thread(target=self.loop, name='stems', daemon=True)
        self.thread.start()

    def connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.executescript(SCHEMA)
        return conn

    def loop(self):
        while True:
            try:
                if not self.work_one():
                    self.wake.wait(60)
                    self.wake.clear()
            except Exception:
                log.exception('stems worker')
                time.sleep(10)

    def work_one(self):
        '''Split the oldest queued song. False when there was none.'''
        conn = self.connect()
        try:
            row = conn.execute("SELECT song_id FROM stems WHERE state = 'queued' ORDER BY queued LIMIT 1").fetchone()
            if row is None:
                return False
            song_id = row[0]
            path = song_file(conn, song_id)
            sig = source_sig(path)
            conn.execute("UPDATE stems SET state = 'running', source = ? WHERE song_id = ?", (sig, song_id))
            conn.commit()
            started = time.time()
            error = self.run(song_id, path) if sig else 'the song\'s file is missing'
            state = 'failed' if error else 'ready'
            conn.execute("UPDATE stems SET state = ?, error = ?, finished = ?, seconds = ? WHERE song_id = ?",
                         (state, error, time.time(), round(time.time() - started, 1), song_id))
            conn.commit()
            log.info('stems for song %d: %s in %.0f s%s', song_id, state, time.time() - started,
                     ': ' + error if error else '')
            return True
        finally:
            conn.close()

    def run(self, song_id, path):
        '''Run the command into temporary names, rename when done. Returns an error or None.'''
        webm, manifest = self.paths(song_id)
        tmp_webm = webm[:-len('.webm')] + '.part.webm'
        tmp_manifest = tmp_webm[:-len('.webm')] + '.json'
        tmp_peaks = tmp_webm[:-len('.webm')] + '.peaks.json'
        try:
            done = subprocess.run(self.command + [path, tmp_webm], capture_output=True, text=True,
                                  timeout=self.timeout, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return 'timed out after {} s'.format(self.timeout)
        except OSError as e:
            return 'cannot run the command: {}'.format(e)
        try:
            if done.returncode != 0:
                lines = [line for line in done.stderr.splitlines() if line.strip()]
                return (lines[-1] if lines else 'exit code {}'.format(done.returncode))[-ERROR_MAX:]
            if not os.path.isfile(tmp_webm) or read_manifest(tmp_manifest) is None:
                return 'the command wrote no usable file and manifest'
            os.replace(tmp_webm, webm)
            os.replace(tmp_manifest, manifest)
            if os.path.isfile(tmp_peaks):
                os.replace(tmp_peaks, self.peaks_path(song_id))
            elif os.path.exists(self.peaks_path(song_id)):
                os.remove(self.peaks_path(song_id))      # an older split's: not this one's
            return None
        finally:
            for f in (tmp_webm, tmp_manifest, tmp_peaks):
                if os.path.exists(f):
                    os.remove(f)


# --- queueing many songs, from the command line ---------------------------------------

def tagged_songs(conn, user, key):
    """Ids of the songs `user` has tagged with the tag whose key is `key`."""
    return [r[0] for r in conn.execute("""
        SELECT DISTINCT st.song_id FROM song_tags st
        JOIN users u ON u.id = st.user_id JOIN tags t ON t.id = st.tag_id
        WHERE u.name = ? AND t.key = ? ORDER BY st.song_id""", (user, key))]


def queue_many(stems, conn, song_ids):
    """Queue every song that isn't split, queued or running (failed ones again).
    Returns the number queued."""
    queued = 0
    for song_id in song_ids:
        before = stems.status(conn, song_id)['state']
        if before in ('ready', 'queued', 'running'):
            continue
        try:
            stems.request(conn, song_id)
            queued += 1
        except KeyError:
            pass        # no file in the library
    return queued


def pending(conn):
    return conn.execute("SELECT count(*) FROM stems WHERE state IN ('queued', 'running')").fetchone()[0]


def main(argv=None):
    parser = argparse.ArgumentParser(prog='chordsplay stems', description='Queue and watch splits for the practice mix.')
    sub = parser.add_subparsers(dest='cmd', required=True)
    q = sub.add_parser('queue', help="queue a user's songs with a tag")
    q.add_argument('--user', required=True)
    q.add_argument('--tag', required=True, help='the tag\'s key, e.g. f')
    w = sub.add_parser('wait', help='until nothing is queued or running')
    w.add_argument('--timeout', type=float, default=0, help='seconds (0: no limit)')
    w.add_argument('--every', type=float, default=10, help=argparse.SUPPRESS)
    sub.add_parser('list', help='the state of every split song')
    args = parser.parse_args(argv)
    db = os.path.expanduser(os.environ.get('CHORDSPLAY_WEB_DB', '~/.local/share/chordsplay-web/chordsplay.db'))
    folder = os.environ.get('CHORDSPLAY_STEMS_DIR', '~/.local/share/chordsplay-web/stems')
    # queueing needs no command: the server's worker runs it
    stems = Stems(db, 'unused', folder)
    conn = stems.connect()
    try:
        if args.cmd == 'queue':
            ids = tagged_songs(conn, args.user, args.tag)
            n = queue_many(stems, conn, ids)
            print('{} songs tagged {!r} by {}, {} queued'.format(len(ids), args.tag, args.user, n))
        elif args.cmd == 'wait':
            end = time.monotonic() + args.timeout if args.timeout else None
            while pending(conn):
                if end and time.monotonic() > end:
                    print('still {} queued or running'.format(pending(conn)), file=sys.stderr)
                    return 1
                time.sleep(args.every)
        else:
            for song_id, state, error, seconds in conn.execute(
                    "SELECT song_id, state, error, seconds FROM stems ORDER BY song_id"):
                print('{}\t{}\t{}\t{}'.format(song_id, state, seconds or '', error or ''))
    finally:
        conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
