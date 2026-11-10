'''
ChordsPlay web app: one server (host6) with the central database, any browser
as the player. Design: Trac page ChordsPlay, docs/spec-web.md.

The server keeps the songs, chords, tags, filters and play history, serves the
audio files and the page. The browser plays the audio (it is the device paired
with the Mustang) and reports finished songs back.

Reuses the Tk app's modules unchanged: ChordsPlay.db for all queries,
ChordsPlay.scan for the library walk and the Ultimate Guitar fetch.

Config via env:
  CHORDSPLAY_WEB_DB      database file (default ~/.local/share/chordsplay-web/chordsplay.db)
  CHORDSPLAY_USER        database user for requests without a proxy login (default $USER)
  CHORDSPLAY_USER_MAP    proxy name -> database user, e.g. "jo=user,caro=florence"
  CHORDSPLAY_SCAN_HOURS  rescan the library and fetch new chords every N hours (0: never; default 24)
  CHORDSPLAY_SYNC_URL, CHORDSPLAY_SYNC_TOKEN   optional: keep the old sync service up to date
  CHORDSPLAY_HOST, CHORDSPLAY_PORT   bind address (default 127.0.0.1:8616; HOST is often set by the shell)
'''

import json
import logging
import mimetypes
import os
import re
import sys
import threading
import time

from flask import Flask, abort, jsonify, request, send_file, send_from_directory

import ChordsPlay.db
import ChordsPlay.scan
from ChordsPlay.textfit import wrap_comments

__version__ = '0.1'

log = logging.getLogger(__name__)

DB_PATH = os.environ.get('CHORDSPLAY_WEB_DB',
                         os.path.expanduser('~/.local/share/chordsplay-web/chordsplay.db'))
DEFAULT_USER = os.environ.get('CHORDSPLAY_USER', os.environ.get('USER', 'anonymous'))
USER_MAP = dict(pair.split('=', 1) for pair in
                os.environ.get('CHORDSPLAY_USER_MAP', '').split(',') if '=' in pair)
# Apache sets this after the Authelia login and removes what a client sends itself
USER_HEADER = 'X-Remote-User'
PROXY_HEADER = 'X-ChordsPlay-Proxy'

FILTER_KEYS = ["Titles", "Artists", "Albums", "Capos", "Required Chords", "Accepted Chords", "Tag Keys"]
FILTER_TYPE = "Playlist patterns"   # same JSON format as the Tk filter dialog
FILTER_VERSION = 1
PREVIOUS_FILTER = '__previous'

ROW_KEYS = ('id', 'title', 'artist', 'album', 'capo', 'chords', 'tags', 'played', 'last')

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')

mimetypes.add_type('audio/mp4', '.m4a')
mimetypes.add_type('audio/ogg', '.ogg')

app = Flask(__name__, static_folder=None)

_local = threading.local()


def user_name():
    name = request.headers.get(USER_HEADER, '').strip()
    if not name:
        return DEFAULT_USER
    return USER_MAP.get(name, name)


@app.before_request
def require_login_via_proxy():
    '''Through Apache only with a login: a request that came over the proxy
    (Apache always sets PROXY_HEADER) without a user would otherwise get
    DEFAULT_USER. Requests made on this host directly (tests, scripts) need no
    login. (X-Forwarded-For can't serve here: waitress drops it.)'''
    if request.headers.get(PROXY_HEADER) and not request.headers.get(USER_HEADER, '').strip():
        return jsonify(error='login required'), 401


def get_db():
    '''One ChordsPlay.db.Db per thread and user (sqlite connections are per thread).'''
    dbs = getattr(_local, 'dbs', None)
    if dbs is None:
        dbs = _local.dbs = {}
    user = user_name()
    db = dbs.get(user)
    if db is None:
        db = dbs[user] = ChordsPlay.db.Db(DB_PATH, user)
        db.conn.execute("""CREATE TABLE IF NOT EXISTS web_state (
            user_id int not null references users(id) on delete cascade,
            key     text not null,
            value   text,
            primary key (user_id, key))""")
        db.conn.commit()
    return db


def user_id(db):
    return db.conn.execute("SELECT id FROM users WHERE name = ?", (db.user,)).fetchone()[0]


def rows_json(rows):
    return [dict(zip(ROW_KEYS, row)) for row in rows]


def filter_patterns(source):
    '''The filter fields from a dict, plus {field: error} for invalid regexes.'''
    patterns = {key: (source.get(key) or '').strip() for key in FILTER_KEYS}
    errors = {}
    for key, pattern in patterns.items():
        if pattern:
            try:
                re.compile(pattern)
            except re.error as e:
                errors[key] = str(e)
    return patterns, errors


def filter_json(patterns):
    content = {'type': FILTER_TYPE, 'version': FILTER_VERSION}
    content.update(patterns)
    return json.dumps(content, sort_keys=True, indent=2)


def load_filter(db, name):
    text = db.getUserFilter(name)
    if text is None:
        return None
    try:
        content = json.loads(text)
    except ValueError:
        return None
    if not isinstance(content, dict) or content.get('type') != FILTER_TYPE \
            or content.get('version') != FILTER_VERSION:
        return None
    return {key: content.get(key, '') for key in FILTER_KEYS}


def get_state(db, key):
    row = db.conn.execute("SELECT value FROM web_state WHERE user_id = ? AND key = ?",
                          (user_id(db), key)).fetchone()
    return json.loads(row[0]) if row else None


def set_state(db, key, value):
    db.conn.execute("INSERT OR REPLACE INTO web_state (user_id, key, value) VALUES (?, ?, ?)",
                    (user_id(db), key, json.dumps(value)))
    db.conn.commit()


# --- page -------------------------------------------------------------------

@app.route('/')
def index():
    response = send_from_directory(STATIC, 'index.html')
    response.headers['Cache-Control'] = 'no-cache'
    return response


@app.route('/static/<path:name>')
def static_file(name):
    response = send_from_directory(STATIC, name)
    response.headers['Cache-Control'] = 'no-cache'
    return response


@app.route('/health')
def health():
    return jsonify(status='ok', version=__version__)


# --- playlist ---------------------------------------------------------------

@app.route('/api/state')
def state():
    '''Everything the page needs at start: user, playlist, last song, filter.'''
    db = get_db()
    return jsonify(user=db.user, version=__version__,
                   playlist=rows_json(db.getFirstPlaylist()),
                   now=get_state(db, 'now'),
                   filter=load_filter(db, PREVIOUS_FILTER) or {key: '' for key in FILTER_KEYS})


@app.route('/api/filter', methods=['GET', 'POST'])
def apply_filter():
    '''GET: preview the songs a filter selects. POST: make them the playlist.'''
    db = get_db()
    source = request.args if request.method == 'GET' else (request.get_json(silent=True) or {})
    patterns, errors = filter_patterns(source)
    if errors:
        return jsonify(errors=errors), 400
    rows = db.getPatternPlaylist(patterns)
    if request.method == 'POST':
        db.setUserFilter(PREVIOUS_FILTER, filter_json(patterns))
        if rows:  # like the Tk app: an empty result leaves the playlist alone
            db.updatePlaylist([row[0] for row in rows])
    return jsonify(playlist=rows_json(rows))


@app.route('/api/playlist/order', methods=['POST'])
def playlist_order():
    '''Store a new order of the playlist (shuffle, sort).'''
    db = get_db()
    ids = (request.get_json(silent=True) or {}).get('ids')
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        abort(400)
    db.updatePlaylist(ids)
    return jsonify(ok=True)


@app.route('/api/filters')
def filters():
    db = get_db()
    names = sorted(row[0] for row in db.getUserFilters() if row[0] != PREVIOUS_FILTER)
    return jsonify(names=names)


@app.route('/api/filters/<name>', methods=['GET', 'PUT'])
def named_filter(name):
    db = get_db()
    if name == PREVIOUS_FILTER:
        abort(400)
    if request.method == 'GET':
        patterns = load_filter(db, name)
        if patterns is None:
            abort(404)
        return jsonify(filter=patterns)
    patterns, errors = filter_patterns(request.get_json(silent=True) or {})
    if errors:
        return jsonify(errors=errors), 400
    db.setUserFilter(name, filter_json(patterns))
    return jsonify(ok=True)


# --- songs ------------------------------------------------------------------

@app.route('/api/song/<int:song_id>')
def song(song_id):
    db = get_db()
    row = db.conn.execute(
        "SELECT title, artist, album, coalesce(capo, 0), lyrics_chords, lyrics_url FROM songs WHERE id = ?",
        (song_id,)).fetchone()
    if row is None:
        abort(404)
    title, artist, album, capo, text, url = row
    text = (text or '').expandtabs()
    tags = ''.join(key for _, key, on, _ in db.getUserTags(song_id) if on == 'X')
    return jsonify(id=song_id, title=title, artist=artist, album=album, capo=capo, url=url,
                   text=text, wrapped=wrap_comments(text), tags=tags,
                   played=db.numPlayed(song_id), last=db.lastPlayed(song_id))


@app.route('/api/audio/<int:song_id>')
def audio(song_id):
    '''The song's file, with HTTP range requests (seeking).'''
    info = get_db().getPlayInfo(song_id)
    if info is None:
        abort(404)
    path = os.path.join(info[3], info[4])
    if not os.path.isfile(path):
        abort(404)
    return send_file(path, conditional=True, max_age=3600)


@app.route('/api/played/<int:song_id>', methods=['POST'])
def played(song_id):
    '''The song played to the end (the Tk app counts the same moment).'''
    db = get_db()
    db.songPlayed(song_id)
    return jsonify(played=db.numPlayed(song_id), last=db.lastPlayed(song_id))


@app.route('/api/now', methods=['PUT'])
def now():
    '''Remember the current song and playlist position, to resume on any device.'''
    data = request.get_json(silent=True) or {}
    if not isinstance(data.get('id'), int):
        abort(400)
    set_state(get_db(), 'now', {'id': data['id'], 'pos': data.get('pos'), 'at': time.time()})
    return jsonify(ok=True)


@app.route('/api/song/<int:song_id>/tags', methods=['GET', 'POST'])
def tags(song_id):
    db = get_db()
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        tag_id = data.get('id')
        current = {tid: on == 'X' for tid, _, on, _ in db.getUserTags(song_id)}
        if tag_id not in current:
            abort(400)
        if data.get('on') and not current[tag_id]:
            db.setUserTag(song_id, tag_id)
        elif not data.get('on') and current[tag_id]:
            db.delUserTag(song_id, tag_id)
    return jsonify(tags=[dict(id=tid, key=key, on=on == 'X', name=name)
                         for tid, key, on, name in db.getUserTags(song_id)])


# --- library scan -------------------------------------------------------------

def scan_loop(hours, sync_url=None, sync_token=None):
    '''Walk the song lists and fetch missing chords, now and every `hours`.'''
    while True:
        scanner = ChordsPlay.scan.Scan(DB_PATH, DEFAULT_USER, sync_url=sync_url, sync_token=sync_token)
        scanner.start()
        scanner.join()
        time.sleep(hours * 3600)


def start_scanner():
    hours = float(os.environ.get('CHORDSPLAY_SCAN_HOURS', '24'))
    if hours > 0:
        threading.Thread(target=scan_loop, name='scan-loop', daemon=True,
                         args=(hours, os.environ.get('CHORDSPLAY_SYNC_URL'),
                               os.environ.get('CHORDSPLAY_SYNC_TOKEN'))).start()


def main():
    logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')
    if not os.path.exists(DB_PATH):
        # an empty database would know no songs; it is made from an existing one
        log.error('%s missing: create it with "python -m web.migrate init ~/.chordsplay.py.db"', DB_PATH)
        return 1
    ChordsPlay.db.Db(DB_PATH, DEFAULT_USER).close()  # migrate the schema before serving
    start_scanner()
    from waitress import serve
    host = os.environ.get('CHORDSPLAY_HOST', '127.0.0.1')
    port = int(os.environ.get('CHORDSPLAY_PORT', '8616'))
    log.info('ChordsPlay web %s on %s:%d, db %s', __version__, host, port, DB_PATH)
    serve(app, host=host, port=port, threads=8, channel_timeout=300)


if __name__ == '__main__':
    sys.exit(main())
