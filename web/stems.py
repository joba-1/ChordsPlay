# SPDX-FileCopyrightText: 2026 joba1
# SPDX-License-Identifier: AGPL-3.0-or-later
'''
Stems for the practice mix: a song split into its instruments, once, by an external
command (CHORDSPLAY_STEMS_COMMAND, e.g. contrib/stems/chordsplay-stems), kept as one
multichannel Opus file per song with a manifest. See docs/spec-practice.md.

One worker thread runs one split at a time (the GPU is the bottleneck), oldest request
first. The table `stems` holds the state per song; the files are a cache that can be
deleted any time.
'''

import json
import logging
import os
import shlex
import subprocess
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
    for s in stems:
        if not (isinstance(s, dict) and isinstance(s.get('name'), str) and isinstance(s.get('pair'), int)
                and 0 <= s['pair'] * 2 + 1 < doc['channels']):
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

    def status(self, conn, song_id):
        '''{offered, state, error, seconds, manifest}; state 'none' when not split (or the
        song's file changed since).'''
        out = {'offered': self.offered, 'state': 'none', 'error': None, 'seconds': None, 'manifest': None}
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
        out.update(state=state, error=error, seconds=seconds)
        return out

    def request(self, conn, song_id):
        '''Queue the split unless it is ready, queued or running. Returns the status.'''
        current = self.status(conn, song_id)
        if current['state'] in ('ready', 'queued', 'running'):
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
            return None
        finally:
            for f in (tmp_webm, tmp_manifest):
                if os.path.exists(f):
                    os.remove(f)
