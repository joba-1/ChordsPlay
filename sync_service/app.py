'''
ChordsPlay central sync service.

Holds the shared chord data (songs / chords / tags) for all ChordsPlay
installations, keyed by the natural key (title, artist) - never by any local
file path. Clients (ChordsPlay/sync.py) pull changes since a watermark and push
their locally-changed rows; merges are last-write-wins by the `modified`
timestamp, tags are unioned.

Store: a service-owned SQLite file (this process is the only writer, so WAL +
per-request connections are safe). Swap for MariaDB later if needed.

Config via env:
  CHORDSPLAY_CENTRAL_DB   path to the SQLite file (default ~/chordsplay-central.db)
  CHORDSPLAY_SYNC_TOKEN   optional bearer token; if set, all /songs calls need it
  HOST, PORT              dev-server bind (default 127.0.0.1:8615)
'''
import os
import sqlite3
import functools
from flask import Flask, request, jsonify, g

DB_PATH = os.environ.get("CHORDSPLAY_CENTRAL_DB", os.path.expanduser("~/chordsplay-central.db"))
TOKEN = os.environ.get("CHORDSPLAY_SYNC_TOKEN")  # optional shared secret

SCHEMA = """
CREATE TABLE IF NOT EXISTS songs (
    id            integer primary key autoincrement,
    title         text not null,
    artist        text not null default '',
    album         text,
    lyrics_chords text,
    lyrics_url    text,
    capo          int,
    chord_update  text,
    modified      text,
    unique(title, artist)
);
CREATE INDEX IF NOT EXISTS songs_modified_ix ON songs(modified);
CREATE TABLE IF NOT EXISTS song_chords (
    song_id int  not null references songs(id) on delete cascade,
    name    char(15) not null,
    primary key (song_id, name)
);
CREATE TABLE IF NOT EXISTS song_tags (
    song_id int  not null references songs(id) on delete cascade,
    user    text not null,
    name    text not null,
    primary key (song_id, user, name)
);
"""

app = Flask(__name__)


def init_db():
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)
    con.commit()
    con.close()


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH, timeout=30)
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


@app.teardown_appcontext
def _close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def require_token(f):
    @functools.wraps(f)
    def wrap(*a, **k):
        if TOKEN and request.headers.get("Authorization", "") != "Bearer " + TOKEN:
            return jsonify(error="unauthorized"), 401
        return f(*a, **k)
    return wrap


def _merge_tags(db, song_id, tags):
    for pair in tags or []:
        try:
            user, name = pair
        except (ValueError, TypeError):
            continue
        db.execute("INSERT OR IGNORE INTO song_tags (song_id, user, name) VALUES (?, ?, ?)",
                   (song_id, user, name))


@app.route("/health")
def health():
    return jsonify(status="ok")


@app.route("/songs", methods=["GET"])
@require_token
def get_songs():
    # watermark: return everything strictly newer than `since` (string compare on
    # 'YYYY-MM-DD HH:MM:SS'); empty since returns all
    since = request.args.get("since", "") or ""
    db = get_db()
    rows = db.execute(
        "SELECT id, title, artist, album, lyrics_chords, lyrics_url, capo, chord_update, modified "
        "FROM songs WHERE modified > ? ORDER BY modified", (since,)).fetchall()
    out = []
    for sid, title, artist, album, lyrics, url, capo, cu, mod in rows:
        chords = [r[0] for r in db.execute("SELECT name FROM song_chords WHERE song_id = ?", (sid,))]
        tags = [[r[0], r[1]] for r in db.execute("SELECT user, name FROM song_tags WHERE song_id = ?", (sid,))]
        out.append(dict(title=title, artist=artist, album=album, lyrics_chords=lyrics,
                        lyrics_url=url, capo=capo, chord_update=cu, modified=mod,
                        chords=chords, tags=tags))
    return jsonify(songs=out)


@app.route("/songs", methods=["POST"])
@require_token
def post_songs():
    payload = request.get_json(force=True, silent=True) or {}
    db = get_db()
    upserted = 0
    for s in payload.get("songs", []):
        title = s.get("title")
        artist = s.get("artist", "") or ""
        if not title:
            continue
        rmod = s.get("modified")
        row = db.execute("SELECT id, modified FROM songs WHERE title = ? AND artist = ?",
                         (title, artist)).fetchone()
        if row:
            sid, cmod = row
            if cmod is not None and rmod is not None and cmod >= rmod:
                _merge_tags(db, sid, s.get("tags"))   # older/equal: still union tags
                continue
            db.execute("UPDATE songs SET album=?, lyrics_chords=?, lyrics_url=?, capo=?, "
                       "chord_update=?, modified=? WHERE id=?",
                       (s.get("album"), s.get("lyrics_chords"), s.get("lyrics_url"),
                        s.get("capo"), s.get("chord_update"), rmod, sid))
        else:
            cur = db.execute("INSERT INTO songs (title, artist, album, lyrics_chords, lyrics_url, "
                             "capo, chord_update, modified) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                             (title, artist, s.get("album"), s.get("lyrics_chords"),
                              s.get("lyrics_url"), s.get("capo"), s.get("chord_update"), rmod))
            sid = cur.lastrowid
        db.execute("DELETE FROM song_chords WHERE song_id = ?", (sid,))
        for name in s.get("chords", []):
            db.execute("INSERT OR IGNORE INTO song_chords (song_id, name) VALUES (?, ?)", (sid, name))
        _merge_tags(db, sid, s.get("tags"))
        upserted += 1
    db.commit()
    return jsonify(upserted=upserted)


init_db()

if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"),
            port=int(os.environ.get("PORT", "8615")))
