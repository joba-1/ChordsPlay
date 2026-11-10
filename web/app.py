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
import secrets
import sys
import threading
import time

from flask import Flask, Response, abort, jsonify, redirect, request, send_file, send_from_directory

import ChordsPlay.db
import ChordsPlay.scan
from ChordsPlay.textfit import wrap_comments

__version__ = '0.2'

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
    if request.path.startswith('/d/'):
        return None  # display pages: no login, a pairing code instead (display_user())
    if request.headers.get(PROXY_HEADER) and not request.headers.get(USER_HEADER, '').strip():
        return jsonify(error='login required'), 401


def get_db(user=None):
    '''One ChordsPlay.db.Db per thread and user (sqlite connections are per thread).'''
    dbs = getattr(_local, 'dbs', None)
    if dbs is None:
        dbs = _local.dbs = {}
    user = user or user_name()
    db = dbs.get(user)
    if db is None:
        db = dbs[user] = ChordsPlay.db.Db(DB_PATH, user)
        db.conn.executescript("""
            CREATE TABLE IF NOT EXISTS web_state (
                user_id int not null references users(id) on delete cascade,
                key     text not null,
                value   text,
                primary key (user_id, key));
            CREATE TABLE IF NOT EXISTS display_codes (
                code    text not null primary key,
                user_id int not null references users(id) on delete cascade,
                expires real not null);""")
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
    data = song_data(get_db(), song_id)
    if data is None:
        abort(404)
    return jsonify(data)


def song_data(db, song_id):
    row = db.conn.execute(
        "SELECT title, artist, album, coalesce(capo, 0), lyrics_chords, lyrics_url FROM songs WHERE id = ?",
        (song_id,)).fetchone()
    if row is None:
        return None
    title, artist, album, capo, text, url = row
    text = (text or '').expandtabs()
    tags = ''.join(key for _, key, on, _ in db.getUserTags(song_id) if on == 'X')
    return dict(id=song_id, title=title, artist=artist, album=album, capo=capo, url=url,
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
    '''The controller's state: current song, playlist position, playing or not and
    where in the song (t seconds). Remembered to resume on any device, and passed
    on to the displays that follow this user.'''
    data = request.get_json(silent=True) or {}
    if not isinstance(data.get('id'), int):
        abort(400)
    db = get_db()
    state = {'id': data['id'], 'pos': data.get('pos'), 'at': time.time(),
             't': float(data.get('t') or 0), 'playing': bool(data.get('playing'))}
    previous = hub.get(db.user)
    if previous is None or previous.get('id') != state['id']:
        set_state(db, 'now', {'id': state['id'], 'pos': state['pos'], 'at': state['at']})
    hub.publish(db.user, state)
    return jsonify(ok=True)


# --- following the controller (displays) ---------------------------------------

class Hub(object):
    '''The latest controller state per user; waiters wake up on every change.'''

    def __init__(self):
        self.cond = threading.Condition()
        self.states = {}
        self.rev = 0
        self.ended = {}     # user -> count of "end all display links"

    def publish(self, user, state):
        with self.cond:
            self.rev += 1
            self.states[user] = dict(state, rev=self.rev)
            self.cond.notify_all()

    def get(self, user):
        with self.cond:
            return self.states.get(user)

    def wait(self, user, rev, timeout, ended=None):
        '''The state once its rev differs from rev (or display links were ended),
        or the unchanged one after timeout.'''
        with self.cond:
            self.cond.wait_for(lambda: (self.states.get(user) or {}).get('rev') != rev
                               or (ended is not None and self.ended.get(user, 0) != ended), timeout)
            return self.states.get(user)

    def end_displays(self, user):
        with self.cond:
            self.ended[user] = self.ended.get(user, 0) + 1
            self.cond.notify_all()

    def ended_count(self, user):
        with self.cond:
            return self.ended.get(user, 0)


hub = Hub()
KEEPALIVE_S = 20        # SSE comment this often, so proxies don't close an idle stream
DISPLAY_CODE_HOURS = 12
CODE_ALPHABET = 'abcdefghjkmnpqrstuvwxyz23456789'  # no 0/o, 1/l/i: typed on a TV remote
CODE_LENGTH = 10                                   # ~49 bits


def current_state(db):
    '''The live state, or after a server restart the remembered song (not playing).'''
    state = hub.get(db.user)
    if state is None:
        saved = get_state(db, 'now')
        if saved:
            state = {'id': saved['id'], 'pos': saved.get('pos'), 't': 0, 'playing': False,
                     'at': saved.get('at'), 'rev': 0}
    return state


def event_stream(user, state, expires=None):
    '''Server-sent events: the state now and after every change. A display's
    stream (expires set) ends with its code; the browser's reconnect then fails.'''
    ended = hub.ended_count(user)
    yield 'retry: 3000\n\n'
    while True:
        yield 'data: {}\n\n'.format(json.dumps(dict(state or {}, server_time=time.time())))
        rev = (state or {}).get('rev')
        while True:
            new = hub.wait(user, rev, KEEPALIVE_S, ended)
            if expires is not None and (time.time() > expires or hub.ended_count(user) != ended):
                return
            if new is not None and new.get('rev') != rev:
                state = new
                break
            yield ': keepalive\n\n'


def sse(db, expires=None):
    # the first state is read here, in the request's thread: the generator runs
    # later, and must not touch the database (its thread may differ)
    return Response(event_stream(db.user, current_state(db), expires), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.route('/api/events')
def events():
    return sse(get_db())


@app.route('/api/display', methods=['POST', 'DELETE'])
def display_code():
    '''POST: a new pairing code for a display (read-only, no login, expires).
    DELETE: end all of this user's display codes.'''
    db = get_db()
    uid = user_id(db)
    if request.method == 'DELETE':
        db.conn.execute("DELETE FROM display_codes WHERE user_id = ?", (uid,))
        db.conn.commit()
        hub.end_displays(db.user)
        return jsonify(ok=True)
    db.conn.execute("DELETE FROM display_codes WHERE expires < ?", (time.time(),))
    code = ''.join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
    expires = time.time() + DISPLAY_CODE_HOURS * 3600
    db.conn.execute("INSERT INTO display_codes (code, user_id, expires) VALUES (?, ?, ?)",
                    (code, uid, expires))
    db.conn.commit()
    return jsonify(code=code, expires=expires)


@app.route('/api/qr.svg')
def qr_svg():
    '''A QR code of text (the display link), so a phone or TV can open it by camera.'''
    import qrcode
    import qrcode.image.svg
    text = request.args.get('text', '')
    if not text or len(text) > 500:
        abort(400)
    image = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, border=2)
    return Response(image.to_string(), mimetype='image/svg+xml')


def display_user(code):
    '''(user, expires) of a display code; 404 for unknown or expired codes.'''
    db = get_db(DEFAULT_USER)
    row = db.conn.execute("""SELECT u.name, d.expires FROM display_codes d JOIN users u ON u.id = d.user_id
                             WHERE d.code = ? AND d.expires > ?""", (code.lower(), time.time())).fetchone()
    if row is None:
        time.sleep(1)   # slows down guessing
        abort(404)
    return row


@app.route('/d/<code>')
def display_redirect(code):
    return redirect(code + '/')


@app.route('/d/<code>/')
def display_page(code):
    # for any code: the page asks for the state, and says what to do if the code is unknown
    response = send_from_directory(STATIC, 'display.html')
    response.headers['Cache-Control'] = 'no-cache'
    return response


@app.route('/d/static/<name>')
def display_static(name):
    if name not in ('display.js', 'fit.js', 'style.css', 'icon.svg'):
        abort(404)
    return static_file(name)


@app.route('/d/<code>/state')
def display_state(code):
    '''The followed controller's state and its song (text only, nothing to change).'''
    db = get_db(display_user(code)[0])
    state = current_state(db)
    song = song_data(db, state['id']) if state else None
    if song:
        song = {key: song[key] for key in ('id', 'title', 'artist', 'capo', 'text', 'wrapped')}
    return jsonify(state=state, song=song, server_time=time.time())


@app.route('/d/<code>/events')
def display_events(code):
    user, expires = display_user(code)
    return sse(get_db(user), expires)


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
    # each open event stream (controller, display) holds one of the threads
    serve(app, host=host, port=port, threads=32, channel_timeout=300)


if __name__ == '__main__':
    sys.exit(main())
