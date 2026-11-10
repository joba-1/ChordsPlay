'''
Build and fill the web app's central database from existing ChordsPlay data.

  python -m web.migrate init  LOCAL_DB [--central CENTRAL_DB]   create the web DB (once)
  python -m web.migrate merge OTHER_DB                          add another host's history

init copies a Tk app database that knows the library on this host (song
lists with paths valid here), then folds in the old sync service's database
(chords fetched on other hosts; newer wins, tags are joined).

merge adds play history, tags and saved filters from any other ChordsPlay
database (e.g. the notebook's ~/.chordsplay.py.db), matched by (title, artist)
and user name. Run it as often as you like: nothing is counted twice.

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
    # so the Tk app should not be running while its database is read.
    con = sqlite3.connect('file:{}?immutable=1'.format(quote(os.path.abspath(path))), uri=True)
    con.row_factory = sqlite3.Row
    return con


def init(target, local, central=None, user=None):
    if os.path.exists(target):
        raise SystemExit('{} exists already; use "merge" to add more data'.format(target))
    os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
    # a plain copy of the local database (with its WAL folded in) via the backup API
    src = sqlite3.connect('file:{}?mode=ro'.format(quote(os.path.abspath(local))), uri=True)
    dst = sqlite3.connect(target)
    src.backup(dst)
    src.close()
    dst.close()
    db = ChordsPlay.db.Db(target, user or os.environ.get('USER', 'anonymous'))
    if central:
        n = merge_central(db, central)
        log.info('central: %d songs taken over', n)
    db.close()


def merge_central(db, central):
    '''Fold in the sync service's database, last write wins (like a sync pull).'''
    con = open_ro(central)
    taken = 0
    for row in con.execute("SELECT * FROM songs ORDER BY modified"):
        song = dict(row)
        song['chords'] = [r[0] for r in con.execute(
            "SELECT name FROM song_chords WHERE song_id = ?", (row['id'],))]
        song['tags'] = [(r[0], r[1]) for r in con.execute(
            "SELECT user, name FROM song_tags WHERE song_id = ?", (row['id'],))]
        if db.upsertRemoteSong(song):
            taken += 1
    db.commit()
    con.close()
    return taken


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
    conn.commit()
    con.close()
    return stats


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--db', default=os.environ.get(
        'CHORDSPLAY_WEB_DB', os.path.expanduser('~/.local/share/chordsplay-web/chordsplay.db')))
    sub = parser.add_subparsers(dest='cmd', required=True)
    p_init = sub.add_parser('init', help='create the web database from a local one')
    p_init.add_argument('local')
    p_init.add_argument('--central', help="the sync service's database")
    p_merge = sub.add_parser('merge', help='add play history, tags and filters of another database')
    p_merge.add_argument('other')
    args = parser.parse_args(argv)
    if args.cmd == 'init':
        init(args.db, args.local, args.central)
        log.info('created %s', args.db)
    else:
        db = ChordsPlay.db.Db(args.db, os.environ.get('USER', 'anonymous'))
        log.info('merged: %s', merge(db, args.other))
        db.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
