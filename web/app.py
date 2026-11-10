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
  CHORDSPLAY_OLLAMA_URL, CHORDSPLAY_SUGGEST_MODEL   suggested presets (web/suggest.py)
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
from web import presets as presetmod
from web import suggest as suggestmod

__version__ = '0.9'

log = logging.getLogger(__name__)

DB_PATH = os.environ.get('CHORDSPLAY_WEB_DB',
                         os.path.expanduser('~/.local/share/chordsplay-web/chordsplay.db'))
TAKES_DIR = os.path.expanduser(os.environ.get('CHORDSPLAY_TAKES_DIR', '~/.local/share/chordsplay-web/takes'))
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
            CREATE TABLE IF NOT EXISTS takes (
                id       integer primary key autoincrement,
                user_id  int not null references users(id) on delete cascade,
                song_id  int not null references songs(id) on delete cascade,
                created  real not null,
                song_t   real not null default 0,
                seconds  real,
                file     text,
                state    text not null default 'recording');
            CREATE TABLE IF NOT EXISTS bridge_tokens (
                hash    text not null primary key,
                user_id int not null references users(id) on delete cascade,
                created real not null);""" + pdfmod.SCHEMA + presetmod.SCHEMA)
        suggestmod.setup(db.conn)
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


LEVEL_RANGE_DB = 12     # a cue's level offset; the page offers -6..+6


def sound_from(data):
    '''(level_db, params) of a request: level None (or missing) plays the slot as stored;
    a number plays it level-matched plus that many dB; params are knob settings.'''
    level, params = data.get('level'), data.get('params')
    if level is not None and (type(level) not in (int, float) or abs(level) > LEVEL_RANGE_DB):
        abort(400)
    if params is not None and not (isinstance(params, dict) and all(isinstance(k, str) for k in params)):
        abort(400)
    return (None if level is None else float(level)), (params or None)


def sound_tone(db, tone_id, slot, slot_name, level, params):
    '''A cue's own sound as a tone the bridge plays by audition. The bridge adds the
    slot's measured correction to a tone's level (ChordsPlay.amp.tone_document), so
    "original" (level None) with changed knobs subtracts it again: the preset's own
    volume, as with a plain switch.'''
    label = slot_name or 'slot {}'.format(slot)
    if level is None:
        measured = db.getPresetLevel(slot)
        level_db, name = -(measured[0] if measured else 0.0), label + ' tuned'
    else:
        level_db = level
        name = '{} {:+g} dB'.format(label, level) if level else label + ' matched'
    if params and level is not None:
        name += ' tuned'
    return {'id': tone_id, 'name': name, 'base_slot': slot, 'base_name': slot_name,
            'level_db': level_db, 'params': params or {}}


def song_cues(db, song_id):
    tones = {tone['id']: tone for tone in db.getTones()}
    info = db.getPlayInfo(song_id)
    artist = info[1] if info else None
    base = db.getArtistPreset(artist) if artist is not None else None
    base_level, base_params = db.getArtistSound(artist) if base else (None, None)
    cues = []
    for start, slot, name, tone_id in db.getSongPresets(song_id):
        level, params = db.getCueSound(song_id, start)
        cues.append({'start': start, 'slot': slot, 'name': name, 'tone_id': tone_id, 'level': level, 'params': params,
                     'tone': tones[tone_id]['name'] if tone_id in tones else None})
    return {'cues': cues, 'measured': [slot for (slot,) in db.conn.execute(
                "SELECT slot FROM preset_levels WHERE method IN ('signal', 'played')")],
            'artist': artist, 'artist_preset': {'slot': base[0], 'name': base[1], 'level': base_level,
                                                'params': base_params} if base else None,
            'suggestion': suggestmod.suggestion(db.conn, song_id)}


@app.route('/api/amp')
def amp():
    '''The amp's state (from the bridge) and the saved tones.'''
    db = get_db()
    return jsonify(amp=amp_state(db.user), tones=db.getTones(), live_slot=LIVE_SLOT,
                   sounds=suggestmod.sound_presets(db.conn), suggest_auto=bool(get_state(db, 'suggest_auto')))


@app.route('/api/amp/suggest-auto', methods=['PUT'])
def suggest_auto():
    '''{on: true}: songs without their own or their artist's preset play the suggested one.
    Off by default: the model says it "knows" nearly every song (2026-11-10: 98 %), so the
    suggestions are shown, and played only once you choose to (after setting the presets by ear).'''
    db = get_db()
    set_state(db, 'suggest_auto', bool((request.get_json(silent=True) or {}).get('on')))
    plan_changed(db.user)
    return jsonify(suggest_auto=bool(get_state(db, 'suggest_auto')))


@app.route('/api/song/<int:song_id>/suggestion', methods=['POST'])
def suggestion(song_id):
    '''{rejected: true}: never play the suggested preset for this song (false: allow again).'''
    db = get_db()
    data = request.get_json(silent=True) or {}
    db.conn.execute("UPDATE song_suggestions SET rejected = ? WHERE song_id = ?", (int(bool(data.get('rejected'))), song_id))
    db.conn.commit()
    plan_changed(db.user, song_id)
    return jsonify(song_cues(db, song_id))


@app.route('/api/amp/sounds/<category>', methods=['PUT'])
def sound_preset(category):
    '''The amp slot for a sound category: {slot: 1..100, level} or {slot: null} (switch nothing).'''
    db = get_db()
    data = request.get_json(silent=True) or {}
    slot = data.get('slot')
    if category not in [c for c, _ in suggestmod.CATEGORIES] or not (slot is None or type(slot) is int and 1 <= slot <= 100):
        abort(400)
    level, _ = sound_from(data)
    db.conn.execute("UPDATE sound_presets SET slot = ?, level_db = ? WHERE category = ?", (slot, level, category))
    db.conn.commit()
    plan_changed(db.user)
    return jsonify(sounds=suggestmod.sound_presets(db.conn))


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
            level, params = sound_from(data)
            names = amp_names(db.user)
            name = names[data['slot'] - 1] if len(names) >= data['slot'] else data.get('name')
            db.setSongPreset(song_id, start, data['slot'], name or None, replace)
            db.setCueSound(song_id, start, level, params)
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
        level, params = sound_from(data)
        names = amp_names(db.user)
        db.setArtistPreset(artist, data['slot'],
                           names[data['slot'] - 1] if len(names) >= data['slot'] else data.get('name'))
        db.setArtistSound(artist, level, params)
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
    '''Switch the amp now, without storing anything: {slot}, {slot, level, params} or {tone_id}.'''
    db = get_db()
    data = request.get_json(silent=True) or {}
    if data.get('tone_id') is not None:
        tone = db.getTone(data['tone_id'])
        if tone is None:
            abort(400)
        command = {'tone': tone, 'level': db.getPresetLevel(tone['base_slot'])}
    elif type(data.get('slot')) is int and 1 <= data['slot'] <= 100:
        level, params = sound_from(data)
        if level is None and not params:
            command = {'slot': data['slot']}
        else:
            names = amp_names(db.user)
            name = names[data['slot'] - 1] if len(names) >= data['slot'] else None
            command = {'tone': sound_tone(db, 0, data['slot'], name, level, params),
                       'level': db.getPresetLevel(data['slot'])}
    else:
        abort(400)
    hub.publish(db.user, dict(command, at=time.time()), 'cmd')
    amp = amp_state(db.user)
    return jsonify(ok=True, bridge=bool(amp and amp['online']))


FREE_SLOTS = range(72, 100)     # the factory presets are 1-71, 100 is the audition slot
AMP_NAME_MAX = 16               # a longer preset name the amp ignores without a reply


@app.route('/api/tones', methods=['POST'])
def save_tone():
    '''A tuned sound as a named tone: {name, slot, level, params}; a tone of that name is replaced.'''
    db = get_db()
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    if not name or type(data.get('slot')) is not int or not 1 <= data['slot'] <= 100:
        abort(400)
    level, params = sound_from(data)
    names = amp_names(db.user)
    tone = sound_tone(db, 0, data['slot'], names[data['slot'] - 1] if len(names) >= data['slot'] else None,
                      level, params)
    tone_id = db.saveTone(name, data['slot'], tone['base_name'], tone['level_db'], params or {})
    plan_changed(db.user)
    return jsonify(id=tone_id, tones=db.getTones())


@app.route('/api/amp/write', methods=['POST'])
def write_slot():
    '''Write a sound into a free amp slot (72-99): {target, name, slot, level, params}.
    The factory presets (1-71) and the audition slot (100) are never written.'''
    db = get_db()
    data = request.get_json(silent=True) or {}
    target, name = data.get('target'), (data.get('name') or '').strip()
    if type(target) is not int or target not in FREE_SLOTS or not name or len(name) > AMP_NAME_MAX \
            or type(data.get('slot')) is not int or not 1 <= data['slot'] <= 100:
        abort(400)
    amp = amp_state(db.user)
    if not amp or not amp['online']:
        return jsonify(error='No amp bridge is running, so nothing can be written to the amp.'), 409
    level, params = sound_from(data)
    names = amp_names(db.user)
    tone = sound_tone(db, 0, data['slot'], names[data['slot'] - 1] if len(names) >= data['slot'] else None,
                      level, params)
    hub.publish(db.user, {'write': {'slot': target, 'name': name}, 'tone': tone,
                          'level': db.getPresetLevel(data['slot']), 'at': time.time()}, 'cmd')
    return jsonify(ok=True)


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
    # A cue with its own sound (level, knobs) goes to the bridge as a tone of its own
    # (negative id): the bridge auditions it like a saved tone, with the slot's correction.
    cues, tones = [], {}

    def own_tone(slot, name, level, params):
        tone_id = -(len(tones) + 1)
        tones[tone_id] = sound_tone(db, tone_id, slot, name, level, params)
        return tone_id

    for start, slot, name, tone_id in db.getSongPresets(song_id):
        if tone_id is None:
            level, params = db.getCueSound(song_id, start)
            if level is not None or params:
                tone_id = own_tone(slot, name, level, params)
        elif db.getTone(tone_id):
            tones[tone_id] = db.getTone(tone_id)
        cues.append([start, slot, name, tone_id])
    # the song's base: the artist's preset, else a suggestion the model is sure of
    base = db.getArtistPreset(info[1])
    level, params = db.getArtistSound(info[1]) if base else (None, None)
    if base is None and get_state(db, 'suggest_auto') and suggestmod.applied(db.conn, song_id):
        slot, level = suggestmod.applied(db.conn, song_id)
        base = (slot, None)
    if base and (level is not None or params) and not any(c[0] == 0 for c in cues):
        cues.insert(0, [0, base[0], base[1], own_tone(base[0], base[1], level, params)])
        base = None
    levels = {t['base_slot']: db.getPresetLevel(t['base_slot']) for t in tones.values()}
    return jsonify(id=song_id, title=info[0], artist=info[1], cues=cues,
                   artist_preset=list(base) if base else None,
                   tones={str(k): v for k, v in tones.items()},
                   levels={str(k): v for k, v in levels.items() if v})


# --- preset contents (for the knobs) ---------------------------------------------

@app.route('/api/amp/preset/<int:slot>')
def amp_preset(slot):
    '''The knobs of the preset in a slot. If it isn't known (or the amp's name for the
    slot changed), the bridge is asked to read it: 202, ask again in a moment.'''
    if not 1 <= slot <= 100:
        abort(404)
    db = get_db()
    cached = presetmod.cached(db.conn, slot)
    names = amp_names(db.user)
    current = names[slot - 1].strip() if len(names) >= slot else None
    if cached is None or (current and current != cached[0].strip()):
        hub.publish(db.user, {'fetch': slot, 'at': time.time()}, 'cmd')
        amp = amp_state(db.user)
        return jsonify(slot=slot, pending=True, bridge=bool(amp and amp['online'])), 202
    import ChordsPlay.amp
    return jsonify(slot=slot, name=cached[0], knobs=ChordsPlay.amp.tone_knobs(cached[1]))


@app.route('/d/bridge/<token>/preset/<int:slot>', methods=['PUT'])
def bridge_preset(token, slot):
    '''A slot's stored preset as the bridge read it from the amp.'''
    db = get_db(bridge_user(token))
    if not 1 <= slot <= 100 or (request.content_length or 0) > 64 * 1024:
        abort(400)
    doc = request.get_json(silent=True)
    if not isinstance(doc, dict) or not isinstance(doc.get('audioGraph'), dict):
        abort(400)
    names = amp_names(db.user)
    presetmod.store(db.conn, slot, names[slot - 1] if len(names) >= slot else doc.get('info', {}).get('displayName', ''), doc)
    return jsonify(ok=True)


@app.route('/d/bridge/<token>/amp', methods=['PUT'])
def bridge_amp(token):
    '''The amp's state as the bridge sees it; shown on the controller page.'''
    user = bridge_user(token)
    if request.content_length and request.content_length > 64 * 1024:
        abort(413)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400)
    report = {key: data.get(key) for key in ('state', 'error', 'busy', 'pending', 'warning', 'tone',
                                             'recording', 'record_error', 'written')}
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


# --- takes: recordings of the amp's output over USB, by the bridge -----------------

def take_json(row):
    tid, song_id, created, song_t, seconds, state = row
    return {'id': tid, 'song_id': song_id, 'created': created, 'song_t': song_t, 'seconds': seconds, 'state': state}


TAKE_COLUMNS = "id, song_id, created, song_t, seconds, state"


@app.route('/api/takes', methods=['POST'])
def take_start():
    '''Start recording a take of the song, which plays from song position t (seconds).'''
    db = get_db()
    data = request.get_json(silent=True) or {}
    if not isinstance(data.get('song_id'), int) or db.getPlayInfo(data['song_id']) is None:
        abort(400)
    amp = amp_state(db.user)
    if not amp or not amp['online']:
        return jsonify(error='No amp bridge is running: it records on the computer the amp is connected to by USB.'), 409
    db.conn.execute("UPDATE takes SET state = 'failed' WHERE user_id = ? AND state = 'recording'", (user_id(db),))
    tid = db.conn.execute("INSERT INTO takes (user_id, song_id, created, song_t) VALUES (?, ?, ?, ?)",
                          (user_id(db), data['song_id'], time.time(), float(data.get('t') or 0))).lastrowid
    db.conn.commit()
    hub.publish(db.user, {'record': 'start', 'take': tid, 'at': time.time()}, 'cmd')
    return jsonify(id=tid)


@app.route('/api/takes/<int:take_id>/stop', methods=['POST'])
def take_stop(take_id):
    db = get_db()
    hub.publish(db.user, {'record': 'stop', 'take': take_id, 'at': time.time()}, 'cmd')
    return jsonify(ok=True)


@app.route('/api/song/<int:song_id>/takes')
def song_takes(song_id):
    db = get_db()
    rows = db.conn.execute("SELECT {} FROM takes WHERE user_id = ? AND song_id = ? AND state != 'failed' "
                           "ORDER BY created DESC".format(TAKE_COLUMNS), (user_id(db), song_id))
    return jsonify(takes=[take_json(r) for r in rows])


def own_take(db, take_id):
    row = db.conn.execute("SELECT file FROM takes WHERE id = ? AND user_id = ?", (take_id, user_id(db))).fetchone()
    if row is None:
        abort(404)
    return row[0]


@app.route('/api/takes/<int:take_id>', methods=['GET', 'DELETE'])
def take(take_id):
    '''GET: the recording (Ogg Opus). DELETE: remove it.'''
    db = get_db()
    name = own_take(db, take_id)
    path = os.path.join(TAKES_DIR, name) if name else None
    if request.method == 'DELETE':
        db.conn.execute("DELETE FROM takes WHERE id = ?", (take_id,))
        db.conn.commit()
        if path and os.path.exists(path):
            os.remove(path)
        return jsonify(ok=True)
    if not path or not os.path.isfile(path):
        abort(404)
    return send_file(path, mimetype='audio/ogg', conditional=True)


@app.route('/d/bridge/<token>/take/<int:take_id>', methods=['PUT'])
def bridge_take(token, take_id):
    '''The bridge's recording of a take (Ogg Opus), with its length in X-Take-Seconds.'''
    db = get_db(bridge_user(token))
    row = db.conn.execute("SELECT song_id FROM takes WHERE id = ? AND user_id = ? AND state = 'recording'",
                          (take_id, user_id(db))).fetchone()
    if row is None:
        abort(404)
    os.makedirs(TAKES_DIR, exist_ok=True)
    name = 'take-{}-song-{}.opus'.format(take_id, row[0])
    with open(os.path.join(TAKES_DIR, name), 'wb') as f:
        while True:
            chunk = request.stream.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
    seconds = float(request.headers.get('X-Take-Seconds') or 0)
    db.conn.execute("UPDATE takes SET file = ?, seconds = ?, state = 'ready' WHERE id = ?", (name, seconds, take_id))
    db.conn.commit()
    plan_changed(db.user)       # the controller pages hear of it with the next amp report
    return jsonify(ok=True)


# --- library scan -------------------------------------------------------------

def scan_loop(hours, sync_url=None, sync_token=None):
    '''Walk the song lists and fetch missing chords, now and every `hours`.'''
    while True:
        scanner = ChordsPlay.scan.Scan(DB_PATH, DEFAULT_USER, sync_url=sync_url, sync_token=sync_token)
        scanner.start()
        scanner.join()
        scan_pdfs()
        suggest_sounds()
        time.sleep(hours * 3600)


def suggest_sounds():
    '''Suggested presets for new songs (local LLM, background priority at ollama-gate).'''
    db = ChordsPlay.db.Db(DB_PATH, DEFAULT_USER)
    try:
        log.info('suggested sounds for %d songs', suggestmod.run(db.conn))
    except Exception:
        log.warning('suggesting sounds failed', exc_info=True)
    finally:
        db.close()


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
