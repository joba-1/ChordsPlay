# SPDX-FileCopyrightText: 2016-2026 joba1
# SPDX-License-Identifier: AGPL-3.0-or-later
'''
Take over data of the former desktop app (ChordsPlay 3.x, branch "legacy") into the
web app's database. Not needed for a new installation: the server creates its own
database at the first start.

  python -m web.migrate init  LOCAL_DB      create the web DB from a desktop database (once)
  python -m web.migrate merge OTHER_DB      add another database's history

init copies a desktop database that knows the library on this host (song lists
with paths valid here).

merge adds play history, tags, saved filters and the amp's cues, artist
presets, tones and level corrections from any other ChordsPlay database (e.g.
a notebook's ~/.chordsplay.py.db), matched by (title, artist) and user name.
Run it as often as you like: nothing is counted twice.

Source databases are only read. The target is CHORDSPLAY_WEB_DB (see app.py)
or --db.
'''

import argparse
import logging
import os
import sqlite3
import sys
from urllib.parse import quote

import ChordsPlay.db

log = logging.getLogger(__name__)


def open_ro(path):
    # immutable: never touch the source's -wal/-shm (another program may be using it,
    # or the directory may not be ours). A not yet checkpointed WAL is then not seen,
    # so the desktop app should not be running while its database is read.
    con = sqlite3.connect('file:{}?immutable=1'.format(quote(os.path.abspath(path))), uri=True)
    con.row_factory = sqlite3.Row
    return con


def init(target, local, user=None):
    if os.path.exists(target):
        raise SystemExit('{} exists already; use "merge" to add more data'.format(target))
    os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
    # a plain copy of the local database (with its WAL folded in) via the backup API
    src = sqlite3.connect('file:{}?mode=ro'.format(quote(os.path.abspath(local))), uri=True)
    dst = sqlite3.connect(target)
    src.backup(dst)
    src.close()
    dst.close()
    ChordsPlay.db.Db(target, user or os.environ.get('USER', 'anonymous')).close()


def merge(db, other):
    '''Add plays, tags and filters of another ChordsPlay database.'''
    con = open_ro(other)
    conn = db.conn
    songs = {}

    def song_id(title, artist):
        key = (title, artist)
        if key not in songs:
            row = conn.execute("SELECT id FROM songs WHERE title = ? AND artist = ?", key).fetchone()
            songs[key] = row[0] if row else None
        return songs[key]

    def uid(name):
        conn.execute("INSERT OR IGNORE INTO users (name) VALUES (?)", (name,))
        return conn.execute("SELECT id FROM users WHERE name = ?", (name,)).fetchone()[0]

    stats = {'plays': 0, 'tags': 0, 'filters': 0, 'unknown songs': 0}
    for r in con.execute("""SELECT u.name AS user, s.title, s.artist, us.played
                            FROM user_songs us JOIN users u ON u.id = us.user_id
                            JOIN songs s ON s.id = us.song_id"""):
        sid = song_id(r['title'], r['artist'])
        if sid is None:
            stats['unknown songs'] += 1
            continue
        u = uid(r['user'])
        if conn.execute("SELECT 1 FROM user_songs WHERE user_id = ? AND song_id = ? AND played = ?",
                        (u, sid, r['played'])).fetchone() is None:
            conn.execute("INSERT INTO user_songs (user_id, song_id, played) VALUES (?, ?, ?)",
                         (u, sid, r['played']))
            stats['plays'] += 1
    for r in con.execute("""SELECT u.name AS user, s.title, s.artist, t.name AS tag
                            FROM song_tags st JOIN users u ON u.id = st.user_id
                            JOIN songs s ON s.id = st.song_id JOIN tags t ON t.id = st.tag_id"""):
        sid = song_id(r['title'], r['artist'])
        tag = conn.execute("SELECT id FROM tags WHERE name = ?", (r['tag'],)).fetchone()
        if sid is None or tag is None:
            continue
        cur = conn.execute("INSERT OR IGNORE INTO song_tags (user_id, song_id, tag_id) VALUES (?, ?, ?)",
                           (uid(r['user']), sid, tag[0]))
        stats['tags'] += cur.rowcount
    for r in con.execute("""SELECT u.name AS user, f.name, f.json_dict FROM user_filters f
                            JOIN users u ON u.id = f.user_id WHERE f.name != '__previous'"""):
        cur = conn.execute("INSERT OR IGNORE INTO user_filters (user_id, name, json_dict) VALUES (?, ?, ?)",
                           (uid(r['user']), r['name'], r['json_dict']))
        stats['filters'] += cur.rowcount
    stats.update(merge_amp(con, conn, song_id))
    stats['locked texts'] = merge_locked(con, conn, song_id)
    conn.commit()
    con.close()
    return stats


def merge_locked(con, conn, song_id):
    '''Songs locked in the other database (tag x, "Fixiert"): their text is the one
    the user fixed, so it replaces ours (the lock comes with the tags above).'''
    taken = 0
    for r in con.execute("""SELECT DISTINCT s.id, s.title, s.artist, s.lyrics_chords, s.lyrics_url, s.capo
                            FROM songs s JOIN song_tags st ON st.song_id = s.id
                            JOIN tags t ON t.id = st.tag_id WHERE t.name = 'Fixiert'"""):
        sid = song_id(r['title'], r['artist'])
        if sid is None or r['lyrics_chords'] is None:
            continue
        mine = conn.execute("SELECT lyrics_chords, lyrics_url, capo FROM songs WHERE id = ?", (sid,)).fetchone()
        if tuple(mine) == (r['lyrics_chords'], r['lyrics_url'], r['capo']):
            continue
        conn.execute("UPDATE songs SET lyrics_chords = ?, lyrics_url = ?, capo = ?, modified = datetime('now') "
                     "WHERE id = ?", (r['lyrics_chords'], r['lyrics_url'], r['capo'], sid))
        conn.execute("DELETE FROM song_chords WHERE song_id = ?", (sid,))
        for (name,) in con.execute("SELECT c.name FROM song_chords sc JOIN chords c ON c.id = sc.chord_id "
                                   "WHERE sc.song_id = ?", (r['id'],)):
            conn.execute("INSERT OR IGNORE INTO chords (name) VALUES (?)", (name,))
            conn.execute("INSERT OR IGNORE INTO song_chords (song_id, chord_id) "
                         "SELECT ?, id FROM chords WHERE name = ?", (sid, name))
        log.info('locked text taken over: %s - %s', r['artist'], r['title'])
        taken += 1
    return taken


def tables(con):
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def merge_amp(con, conn, song_id):
    '''Amp cues, artist presets, tones and level corrections (3.34/3.35). What the
    target has already wins: a cue at the same time, an artist, a tone of that name.'''
    stats = {'cues': 0, 'artist presets': 0, 'tones': 0, 'levels': 0}
    have = tables(con)
    tone_ids = {}       # source tone id -> target tone id
    if 'tones' in have:
        for r in con.execute("SELECT * FROM tones"):
            cur = conn.execute("INSERT OR IGNORE INTO tones (name, base_slot, base_name, level_db, params) "
                               "VALUES (?, ?, ?, ?, ?)",
                               (r['name'], r['base_slot'], r['base_name'], r['level_db'], r['params']))
            stats['tones'] += cur.rowcount
            tone_ids[r['id']] = conn.execute("SELECT id FROM tones WHERE name = ?", (r['name'],)).fetchone()[0]
    if 'song_presets' in have:
        tone_column = 'tone_id' in {r[1] for r in con.execute("PRAGMA table_info(song_presets)")}
        for r in con.execute("SELECT s.title, s.artist, p.start_ms, p.slot, p.slot_name{} "
                             "FROM song_presets p JOIN songs s ON s.id = p.song_id".format(
                                 ', p.tone_id' if tone_column else '')):
            sid = song_id(r['title'], r['artist'])
            if sid is None:
                continue
            cur = conn.execute("INSERT OR IGNORE INTO song_presets (song_id, start_ms, slot, slot_name, tone_id) "
                               "VALUES (?, ?, ?, ?, ?)", (sid, r['start_ms'], r['slot'], r['slot_name'],
                                                          tone_ids.get(r['tone_id']) if tone_column else None))
            stats['cues'] += cur.rowcount
    if 'artist_presets' in have:
        for r in con.execute("SELECT * FROM artist_presets"):
            cur = conn.execute("INSERT OR IGNORE INTO artist_presets (artist, slot, slot_name) VALUES (?, ?, ?)",
                               (r['artist'], r['slot'], r['slot_name']))
            stats['artist presets'] += cur.rowcount
    if 'preset_levels' in have:
        for r in con.execute("SELECT * FROM preset_levels"):
            cur = conn.execute("INSERT OR IGNORE INTO preset_levels (slot, name, correction_db, method, measured_at) "
                               "VALUES (?, ?, ?, ?, ?)",
                               (r['slot'], r['name'], r['correction_db'], r['method'], r['measured_at']))
            stats['levels'] += cur.rowcount
    return stats


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--db', default=os.environ.get(
        'CHORDSPLAY_WEB_DB', os.path.expanduser('~/.local/share/chordsplay-web/chordsplay.db')))
    sub = parser.add_subparsers(dest='cmd', required=True)
    p_init = sub.add_parser('init', help='create the web database from a local one')
    p_init.add_argument('local')
    p_merge = sub.add_parser('merge', help='add play history, tags and filters of another database')
    p_merge.add_argument('other')
    args = parser.parse_args(argv)
    if args.cmd == 'init':
        init(args.db, args.local)
        log.info('created %s', args.db)
    else:
        db = ChordsPlay.db.Db(args.db, os.environ.get('USER', 'anonymous'))
        log.info('merged: %s', merge(db, args.other))
        db.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
