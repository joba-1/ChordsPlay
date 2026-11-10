'''
ChordsPlay web app: one server (host6) with the central database, any browser
as the player. Design: Trac page ChordsPlay, docs/spec-web.md.

The server keeps the songs, chords, tags, filters and play history, serves the
audio files and the page. The browser plays the audio (it is the device paired
with the Mustang) and reports finished songs back. Displays (TV, tablet) and the
amp bridge (web/bridge.py, switches the Mustang's presets) follow the browser
over server-sent events.

Reuses the Tk app's modules unchanged: ChordsPlay.db for all queries,
ChordsPlay.scan for the library walk and the Ultimate Guitar fetch.

Config via env:
  CHORDSPLAY_WEB_DB      database file (default ~/.local/share/chordsplay-web/chordsplay.db)
  CHORDSPLAY_USER        database user for requests without a proxy login (default $USER)
  CHORDSPLAY_USER_MAP    proxy name -> database user, e.g. "jo=user,caro=florence"
  CHORDSPLAY_SCAN_HOURS  rescan the library and fetch new chords every N hours (0: never; default 24)
  CHORDSPLAY_SYNC_URL, CHORDSPLAY_SYNC_TOKEN   optional: keep the old sync service up to date
  CHORDSPLAY_HOST, CHORDSPLAY_PORT   bind address (default 127.0.0.1:8616; HOST is often set by the shell)
  CHORDSPLAY_PDF_DIRS    more folders with PDF tabs, ":"-separated (default ~/.local/share/chordsplay-web/pdf);
                         the library's folders are always scanned (tabs next to their songs); see web/pdfs.py
'''

import hashlib
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
from web import pdfs as pdfmod

__version__ = '0.4'

log = logging.getLogger(__name__)

DB_PATH = os.environ.get('CHORDSPLAY_WEB_DB',
                         os.path.expanduser('~/.local/share/chordsplay-web/chordsplay.db'))
PDF_DIRS = [os.path.expanduser(d) for d in os.environ.get(
    'CHORDSPLAY_PDF_DIRS', '~/.local/share/chordsplay-web/pdf').split(':') if d]
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
                expires real not null);
            CREATE TABLE IF NOT EXISTS bridge_tokens (
                hash    text not null primary key,
                user_id int not null references users(id) on delete cascade,
                created real not null);""" + pdfmod.SCHEMA)
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
                played=db.numPlayed(song_id), last=db.lastPlayed(song_id),
                pdfs=pdfmod.song_pdfs(db.conn, song_id), view=pdfmod.song_view(db.conn, song_id))


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
             't': float(data.get('t') or 0), 'playing': bool(data.get('playing')),
             # what the song shows: its text (pdf None) or a PDF, and the PDF's first page shown
             'pdf': data.get('pdf') if isinstance(data.get('pdf'), int) else None,
             'page': int(data.get('page') or 1)}
    previous = hub.get(db.user)
    if previous is None or previous.get('id') != state['id']:
        set_state(db, 'now', {'id': state['id'], 'pos': state['pos'], 'at': state['at']})
    hub.publish(db.user, state)
    return jsonify(ok=True)


# --- following the controller (displays) ---------------------------------------

class Hub(object):
    '''The latest state per user and channel; waiters wake up on every change.
    Channels: 'now' (the controller), 'amp' (what the amp bridge reports), 'plan'
    (cues edited: the bridge reloads), 'cmd' (a command for the bridge).'''

    def __init__(self):
        self.cond = threading.Condition()
        self.states = {}    # (user, channel) -> state
        self.rev = 0
        self.ended = {}     # user -> count of "end all display links"

    def publish(self, user, state, channel='now'):
        with self.cond:
            self.rev += 1
            self.states[(user, channel)] = dict(state, rev=self.rev)
            self.cond.notify_all()

    def get(self, user, channel='now'):
        with self.cond:
            return self.states.get((user, channel))

    def _changed(self, user, revs):
        return {channel: state for channel, state in
                ((channel, self.states.get((user, channel))) for channel in revs)
                if state is not None and state['rev'] != revs[channel]}

    def wait(self, user, revs, timeout, ended=None):
        '''{channel: state} of the channels whose rev differs from revs ({channel: rev}),
        once there is one (or display links were ended); {} after timeout.'''
        with self.cond:
            self.cond.wait_for(lambda: self._changed(user, revs)
                               or (ended is not None and self.ended.get(user, 0) != ended), timeout)
            return self._changed(user, revs)

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


def event_stream(user, first, expires=None):
    '''Server-sent events: the states now ({channel: state}) and after every change.
    'now' goes as a plain message, the other channels as named events. A display's
    stream (expires set) ends with its code; the browser's reconnect then fails.'''
    ended = hub.ended_count(user)
    revs = {channel: (state or {}).get('rev') for channel, state in first.items()}
    send = {channel: state for channel, state in first.items() if state is not None or channel == 'now'}
    yield 'retry: 3000\n\n'
    while True:
        for channel, state in send.items():
            yield '{}data: {}\n\n'.format('' if channel == 'now' else 'event: {}\n'.format(channel),
                                          json.dumps(dict(state or {}, server_time=time.time())))
        while True:
            send = hub.wait(user, revs, KEEPALIVE_S, ended)
            if expires is not None and (time.time() > expires or hub.ended_count(user) != ended):
                return
            if send:
                revs.update((channel, state['rev']) for channel, state in send.items())
                break
            yield ': keepalive\n\n'


def sse(db, channels=('now',), expires=None):
    # the first state is read here, in the request's thread: the generator runs
    # later, and must not touch the database (its thread may differ)
    first = {channel: current_state(db) if channel == 'now' else hub.get(db.user, channel)
             for channel in channels}
    return Response(event_stream(db.user, first, expires), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.route('/api/events')
def events():
    '''The controller page: other controllers' state and the amp's.'''
    return sse(get_db(), ('now', 'amp'))


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
    if name not in ('display.js', 'fit.js', 'pdfview.js', 'style.css', 'icon.svg'):
        abort(404)
    return static_file(name)


@app.route('/d/static/vendor/pdfjs/<name>')
def display_pdfjs(name):
    if name not in ('pdf.min.js', 'pdf.worker.min.js'):
        abort(404)
    return static_file('vendor/pdfjs/' + name)


@app.route('/d/<code>/state')
def display_state(code):
    '''The followed controller's state and its song (text only, nothing to change).'''
    db = get_db(display_user(code)[0])
    state = current_state(db)
    song = song_data(db, state['id']) if state else None
    if song:
        song = {key: song[key] for key in ('id', 'title', 'artist', 'capo', 'text', 'wrapped', 'pdfs')}
    return jsonify(state=state, song=song, server_time=time.time())


@app.route('/d/<code>/pdf/<int:pdf_id>')
def display_pdf(code, pdf_id):
    '''A PDF of the song the controller is on (a display gets nothing else).'''
    db = get_db(display_user(code)[0])
    state = current_state(db)
    if not state or pdf_id not in [p['id'] for p in pdfmod.song_pdfs(db.conn, state['id'])]:
        abort(404)
    return pdf_file(db, pdf_id)


@app.route('/d/<code>/events')
def display_events(code):
    user, expires = display_user(code)
    return sse(get_db(user), expires=expires)


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


# --- amp: cues per song, played by the amp bridge (web/bridge.py) ----------------
#
# The bridge runs next to the amp agent (maneline's mmp_web.py) on the machine the
# amp is paired with. It follows the user's 'now' state over an outbound event
# stream, like a display, switches the amp through the agent and reports the amp's
# state back. Its URL holds a token made on the logged-in page (POST api/bridge);
# below /d/ it needs no login, and the token allows nothing but this.

LIVE_SLOT = 100         # tones are auditioned on this slot (as in the Tk app)
LEARN_START_MS = 3000   # key G earlier than this stores the song's start preset
LEARN_REPLACE_MS = 2000
BRIDGE_SEEN_S = 30      # a bridge reports at least this often


def amp_state(user):
    '''What the bridge reported last, and whether it is still there.'''
    amp = hub.get(user, 'amp')
    if amp is None:
        return None
    return dict(amp, online=time.time() - amp.get('at', 0) < BRIDGE_SEEN_S)


def amp_names(user):
    '''The amp's 100 preset names as far as the bridge reported them.'''
    state = (hub.get(user, 'amp') or {}).get('state') or {}
    return [p.get('name') or '' for p in state.get('presets') or []]


def plan_changed(user, song_id=None):
    '''Cues were edited: the bridge reloads (song_id None: whatever plays).'''
    hub.publish(user, {'song': song_id, 'at': time.time()}, 'plan')


def song_cues(db, song_id):
    tones = {tone['id']: tone for tone in db.getTones()}
    info = db.getPlayInfo(song_id)
    artist = info[1] if info else None
    base = db.getArtistPreset(artist) if artist is not None else None
    return {'cues': [{'start': start, 'slot': slot, 'name': name, 'tone_id': tone_id,
                      'tone': tones[tone_id]['name'] if tone_id in tones else None}
                     for start, slot, name, tone_id in db.getSongPresets(song_id)],
            'artist': artist, 'artist_preset': {'slot': base[0], 'name': base[1]} if base else None}


@app.route('/api/amp')
def amp():
    '''The amp's state (from the bridge) and the saved tones.'''
    db = get_db()
    return jsonify(amp=amp_state(db.user), tones=db.getTones(), live_slot=LIVE_SLOT)


@app.route('/api/song/<int:song_id>/cues')
def cues(song_id):
    db = get_db()
    if db.getPlayInfo(song_id) is None:
        abort(404)
    return jsonify(song_cues(db, song_id))


@app.route('/api/song/<int:song_id>/cues/<int:start>', methods=['PUT', 'DELETE'])
def cue(song_id, start):
    '''PUT {slot} or {tone_id}: from start (ms) on the song uses this preset or tone.'''
    db = get_db()
    if db.getPlayInfo(song_id) is None:
        abort(404)
    if request.method == 'DELETE':
        db.delSongPreset(song_id, start)
    else:
        data = request.get_json(silent=True) or {}
        replace = int(data.get('replace_within_ms') or 0)
        if data.get('tone_id') is not None:
            if db.getTone(data['tone_id']) is None:
                abort(400)
            db.setSongTone(song_id, start, data['tone_id'], LIVE_SLOT, replace)
        elif type(data.get('slot')) is int and 1 <= data['slot'] <= 100:
            names = amp_names(db.user)
            name = names[data['slot'] - 1] if len(names) >= data['slot'] else data.get('name')
            db.setSongPreset(song_id, start, data['slot'], name or None, replace)
        else:
            abort(400)
    plan_changed(db.user, song_id)
    return jsonify(song_cues(db, song_id))


@app.route('/api/artist-preset', methods=['PUT', 'DELETE'])
def artist_preset():
    '''The preset of every song of an artist without a cue at its start.'''
    db = get_db()
    data = request.get_json(silent=True) or {}
    artist = data.get('artist')
    if not isinstance(artist, str):
        abort(400)
    if request.method == 'DELETE':
        db.delArtistPreset(artist)
    else:
        if type(data.get('slot')) is not int or not 1 <= data['slot'] <= 100:
            abort(400)
        names = amp_names(db.user)
        db.setArtistPreset(artist, data['slot'],
                           names[data['slot'] - 1] if len(names) >= data['slot'] else data.get('name'))
    plan_changed(db.user)
    return jsonify(ok=True)


@app.route('/api/amp/learn', methods=['POST'])
def learn():
    '''Key G: the amp's current preset as a cue of the song at t (seconds).'''
    db = get_db()
    data = request.get_json(silent=True) or {}
    song_id = data.get('id')
    if not isinstance(song_id, int) or db.getPlayInfo(song_id) is None:
        abort(400)
    amp = amp_state(db.user)
    state = (amp or {}).get('state') or {}
    if not amp or not amp['online']:
        return jsonify(error='No amp bridge is running, so the amp\'s preset is not known. '
                             'Press g to choose a preset instead.'), 409
    if state.get('connected') and state.get('audition'):
        return jsonify(error='A tone is playing, not a preset: press g and choose the tone.'), 409
    if not state.get('connected') or not state.get('preset'):
        return jsonify(error='The amp is not connected, so its current preset is not known. '
                             'Press g to choose a preset instead.'), 409
    pos = int(float(data.get('t') or 0) * 1000)
    start = 0 if pos < LEARN_START_MS else pos - pos % 500
    db.setSongPreset(song_id, start, state['preset'], state.get('name'), replace_within_ms=LEARN_REPLACE_MS)
    plan_changed(db.user, song_id)
    return jsonify(start=start, slot=state['preset'], name=state.get('name'))


@app.route('/api/amp/try', methods=['POST'])
def try_preset():
    '''Switch the amp now, without storing anything: {slot} or {tone_id}.'''
    db = get_db()
    data = request.get_json(silent=True) or {}
    if data.get('tone_id') is not None:
        tone = db.getTone(data['tone_id'])
        if tone is None:
            abort(400)
        command = {'tone': tone, 'level': db.getPresetLevel(tone['base_slot'])}
    elif type(data.get('slot')) is int and 1 <= data['slot'] <= 100:
        command = {'slot': data['slot']}
    else:
        abort(400)
    hub.publish(db.user, dict(command, at=time.time()), 'cmd')
    amp = amp_state(db.user)
    return jsonify(ok=True, bridge=bool(amp and amp['online']))


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


@app.route('/api/bridge', methods=['POST'])
def new_bridge_token():
    '''A new token for this user's amp bridge; an older one stops working.'''
    db = get_db()
    uid = user_id(db)
    token = secrets.token_urlsafe(24)
    db.conn.execute("DELETE FROM bridge_tokens WHERE user_id = ?", (uid,))
    db.conn.execute("INSERT INTO bridge_tokens (hash, user_id, created) VALUES (?, ?, ?)",
                    (token_hash(token), uid, time.time()))
    db.conn.commit()
    return jsonify(token=token, path='d/bridge/{}/'.format(token))


def bridge_user(token):
    db = get_db(DEFAULT_USER)
    row = db.conn.execute("SELECT u.name FROM bridge_tokens b JOIN users u ON u.id = b.user_id "
                          "WHERE b.hash = ?", (token_hash(token),)).fetchone()
    if row is None:
        time.sleep(1)   # slows down guessing
        abort(404)
    return row[0]


@app.route('/d/bridge/<token>/events')
def bridge_events(token):
    return sse(get_db(bridge_user(token)), ('now', 'plan', 'cmd'))


@app.route('/d/bridge/<token>/song/<int:song_id>')
def bridge_song(token, song_id):
    '''What the bridge needs to play a song's cues: like the Tk app's database calls.'''
    db = get_db(bridge_user(token))
    info = db.getPlayInfo(song_id)
    if info is None:
        abort(404)
    presets = db.getSongPresets(song_id)
    tones = {tone_id: db.getTone(tone_id) for _, _, _, tone_id in presets if tone_id is not None}
    levels = {tone['base_slot']: db.getPresetLevel(tone['base_slot']) for tone in tones.values() if tone}
    return jsonify(id=song_id, title=info[0], artist=info[1], cues=presets,
                   artist_preset=db.getArtistPreset(info[1]),
                   tones={str(k): v for k, v in tones.items() if v},
                   levels={str(k): v for k, v in levels.items() if v})


@app.route('/d/bridge/<token>/amp', methods=['PUT'])
def bridge_amp(token):
    '''The amp's state as the bridge sees it; shown on the controller page.'''
    user = bridge_user(token)
    if request.content_length and request.content_length > 64 * 1024:
        abort(413)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400)
    report = {key: data.get(key) for key in ('state', 'error', 'busy', 'pending', 'warning', 'tone')}
    # every report, also an unchanged one (at most every REPORT_S in the bridge): the
    # pages see from it that the bridge is still there
    hub.publish(user, dict(report, at=time.time()), 'amp')
    return jsonify(ok=True)


# --- PDF tabs (web/pdfs.py) ------------------------------------------------------

def pdf_file(db, pdf_id):
    row = db.conn.execute("SELECT path FROM pdf_files WHERE id = ?", (pdf_id,)).fetchone()
    if row is None or not os.path.isfile(row[0]):
        abort(404)
    return send_file(row[0], mimetype='application/pdf', conditional=True, max_age=600)


@app.route('/api/pdf/<int:pdf_id>')
def pdf(pdf_id):
    return pdf_file(get_db(), pdf_id)


@app.route('/api/song/<int:song_id>/view', methods=['PUT'])
def view(song_id):
    '''What the song shows from now on: {pdf: id} or {pdf: null} for its text.'''
    db = get_db()
    pdf_id = (request.get_json(silent=True) or {}).get('pdf')
    if pdf_id is None:
        db.conn.execute("DELETE FROM song_view WHERE song_id = ?", (song_id,))
    elif pdf_id in [p['id'] for p in pdfmod.song_pdfs(db.conn, song_id)]:
        db.conn.execute("INSERT OR REPLACE INTO song_view (song_id, pdf_id) VALUES (?, ?)", (song_id, pdf_id))
    else:
        abort(400)
    db.conn.commit()
    return jsonify(view=pdfmod.song_view(db.conn, song_id))


@app.route('/api/pdfs')
def pdf_list():
    '''All PDFs found (scanned now), with the song each one belongs to.'''
    db = get_db()
    pdfmod.scan(db.conn, PDF_DIRS)
    rows = db.conn.execute("""SELECT f.id, f.path, p.song_id, p.part, coalesce(p.manual, 0), s.title, s.artist
                              FROM pdf_files f LEFT JOIN song_pdfs p ON p.pdf_id = f.id
                              LEFT JOIN songs s ON s.id = p.song_id ORDER BY f.path""").fetchall()
    return jsonify(folders=PDF_DIRS, pdfs=[
        {'id': pid, 'name': os.path.basename(path), 'folder': os.path.dirname(path), 'part': part,
         'manual': bool(manual), 'song': {'id': sid, 'title': title, 'artist': artist} if sid else None}
        for pid, path, sid, part, manual, title, artist in rows])


@app.route('/api/pdfs/<int:pdf_id>', methods=['PUT'])
def assign_pdf(pdf_id):
    '''Assign by hand: {song_id, part} links the PDF to a song, {song_id: null} to none.
    A later scan leaves it so.'''
    db = get_db()
    if db.conn.execute("SELECT 1 FROM pdf_files WHERE id = ?", (pdf_id,)).fetchone() is None:
        abort(404)
    data = request.get_json(silent=True) or {}
    song_id = data.get('song_id')
    if song_id is not None and (not isinstance(song_id, int) or db.getPlayInfo(song_id) is None):
        abort(400)
    part = (data.get('part') or '').strip() or None
    db.conn.execute("INSERT OR REPLACE INTO song_pdfs (pdf_id, song_id, part, manual) VALUES (?, ?, ?, 1)",
                    (pdf_id, song_id, part))
    db.conn.execute("DELETE FROM song_view WHERE pdf_id = ? AND song_id IS NOT ?", (pdf_id, song_id))
    db.conn.commit()
    return jsonify(ok=True)


@app.route('/api/songs/search')
def song_search():
    '''Songs whose title or artist contains q (for assigning a PDF).'''
    q = '%{}%'.format(request.args.get('q', '').strip())
    rows = get_db().conn.execute("""SELECT id, title, artist FROM songs WHERE (title LIKE ? OR artist LIKE ?)
                                    AND lyrics_chords IS NOT NULL ORDER BY artist, title LIMIT 30""", (q, q))
    return jsonify(songs=[{'id': i, 'title': t, 'artist': a} for i, t, a in rows])


# --- library scan -------------------------------------------------------------

def scan_loop(hours, sync_url=None, sync_token=None):
    '''Walk the song lists and fetch missing chords, now and every `hours`.'''
    while True:
        scanner = ChordsPlay.scan.Scan(DB_PATH, DEFAULT_USER, sync_url=sync_url, sync_token=sync_token)
        scanner.start()
        scanner.join()
        scan_pdfs()
        time.sleep(hours * 3600)


def scan_pdfs():
    db = ChordsPlay.db.Db(DB_PATH, DEFAULT_USER)
    try:
        db.conn.executescript(pdfmod.SCHEMA)
        found, linked = pdfmod.scan(db.conn, PDF_DIRS)
        log.info('PDF tabs: %d found in %s, %d newly linked', found, ', '.join(PDF_DIRS), linked)
    except OSError:
        log.warning('PDF scan failed', exc_info=True)
    finally:
        db.close()


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
    scan_pdfs()
    start_scanner()
    from waitress import serve
    host = os.environ.get('CHORDSPLAY_HOST', '127.0.0.1')
    port = int(os.environ.get('CHORDSPLAY_PORT', '8616'))
    log.info('ChordsPlay web %s on %s:%d, db %s', __version__, host, port, DB_PATH)
    # each open event stream (controller, display) holds one of the threads
    serve(app, host=host, port=port, threads=32, channel_timeout=300)


if __name__ == '__main__':
    sys.exit(main())
