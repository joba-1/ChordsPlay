'''
Mustang Micro Plus preset switching (see docs/spec-amp.md).

ChordsPlay never speaks Bluetooth itself. The amp agent (maneline's mmp_web.py,
run as a systemd user unit) owns the single LE connection to the amp; this module
talks to it over HTTP on localhost:

  AmpClient     worker thread: POST the newest wanted slot, poll the agent's state
  AgentControl  start/stop the agent's unit while the amp's audio sink exists
  AmpCues       runs in the Tk thread: picks the cue for the playing position

Nothing here touches Tk, so it can be tested without a display.
'''

import hashlib
import json
import logging
import subprocess
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

SLOTS = range(1, 101)


def valid_slot(slot):
    return type(slot) is int and slot in SLOTS


def fmt_ms(ms):
    '''Position in ms as m:ss (or m:ss.5 for half seconds).'''
    seconds, rest = divmod(max(0, int(ms)), 1000)
    text = '{}:{:02d}'.format(seconds // 60, seconds % 60)
    if rest >= 500:
        text += '.5'
    return text


def parse_ms(text):
    '''Parse "m:ss", "m:ss.5", "ss" or "ss.5" into ms. Raises ValueError.'''
    text = text.strip()
    if not text:
        raise ValueError('empty time')
    minutes, _, seconds = text.rpartition(':')
    value = float(seconds) + 60 * (int(minutes) if minutes else 0)
    if value < 0 or (minutes and not 0 <= float(seconds) < 60):
        raise ValueError('invalid time {}'.format(text))
    return int(round(value * 1000))


# ---------------------------------------------------------------- cue logic

def build_cues(song_cues, base_slot):
    '''
    Cues to apply for a song, sorted by start.
    song_cues: [(start_ms, slot, slot_name, tone_id)], base_slot: (slot, slot_name) or None.
    The base (artist slot or default) fills the start if the song has no cue at 0.
    '''
    cues = sorted(tuple(c) + (None,) * (4 - len(c)) for c in song_cues)
    if base_slot is not None and not any(c[0] == 0 for c in cues):
        cues.insert(0, (0, base_slot[0], base_slot[1], None))
    return cues


def active_index(cues, pos_ms, lead_ms=0):
    '''Index of the cue that applies at pos_ms, or None before the first cue.'''
    pos_ms = max(0, pos_ms)
    index = None
    for i, cue in enumerate(cues):
        start = cue[0]
        if start - lead_ms <= pos_ms:
            index = i
        else:
            break
    return index


def name_mismatch(slot, slot_name, names):
    '''
    The amp's current name for slot if it differs from the name stored with the cue,
    else None. Unknown names (agent never connected) never count as a mismatch.
    '''
    if not slot_name or not names or slot > len(names):
        return None
    current = names[slot - 1]
    if current.strip() == slot_name.strip():
        return None
    return current


# ---------------------------------------------------------------- tones

# Effect blocks in a preset, and the parameters offered as knobs when present with a
# value in 0..1 (Fender's normalized range). 'bypass' becomes an on/off switch.
TONE_NODES = ('stomp', 'mod', 'delay', 'reverb')
AMP_KNOBS = (('gain', 'Gain'), ('bass', 'Bass'), ('mid', 'Mid'), ('treb', 'Treble'),
             ('presence', 'Presence'))
EFFECT_KNOBS = {'stomp': (('level', 'level'), ('gain', 'gain'), ('mix', 'mix')),
                'mod': (('wetlvl', 'mix'), ('depth', 'depth'), ('intensity', 'intensity'), ('level', 'level')),
                'delay': (('wetlvl', 'mix'), ('mix', 'mix'), ('level', 'level'), ('feedback', 'feedback')),
                'reverb': (('wetlvl', 'mix'), ('decay', 'decay'))}
EMPTY_BLOCK = 'DUBS_Unknown'


def nodes(doc):
    return {n.get('nodeId'): n for n in doc.get('audioGraph', {}).get('nodes', []) if isinstance(n, dict)}


def build_tone(base, level_db=0.0, params=None, name=None, correction_db=0.0):
    '''
    A preset JSON document for a tone: a copy of the base preset with the amp volume
    shifted by correction_db (measured level matching) plus level_db, and the knob
    settings in params ({'reverb.wetlvl': 0.3, 'delay.bypass': True, ...}) applied.
    Parameters of blocks the base preset doesn't have are ignored.
    '''
    doc = json.loads(json.dumps(base))
    graph = nodes(doc)
    amp = graph.get('amp')
    if amp is None:
        raise ValueError('preset has no amp node')
    p = amp.setdefault('dspUnitParameters', {})
    p['volume'] = round(float(p.get('volume', 0.0)) + float(correction_db) + float(level_db), 3)
    for key, value in (params or {}).items():
        node_id, _, param = key.partition('.')
        node = graph.get(node_id)
        if node is None or not param or (node.get('FenderId') == EMPTY_BLOCK and node_id != 'amp'):
            continue
        node.setdefault('dspUnitParameters', {})[param] = value
    if name is not None:
        doc.setdefault('info', {})['displayName'] = name
    return doc


def tone_knobs(base):
    '''Knobs for a base preset: [{'key', 'label', 'value', 'kind': 'level'|'switch'}].'''
    graph = nodes(base)
    knobs = []
    amp = graph.get('amp', {}).get('dspUnitParameters', {})
    for param, label in AMP_KNOBS:
        value = amp.get(param)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1:
            knobs.append({'key': 'amp.' + param, 'label': 'Amp ' + label, 'value': float(value),
                          'kind': 'level'})
    for node_id in TONE_NODES:
        node = graph.get(node_id)
        if not node or node.get('FenderId') == EMPTY_BLOCK:
            continue
        p = node.get('dspUnitParameters', {})
        model = node.get('FenderId', '').replace('ACD_', '')
        knobs.append({'key': node_id + '.bypass', 'label': '{} ({}) on'.format(node_id.title(), model),
                      'value': not p.get('bypass', False), 'kind': 'switch'})
        used = set()
        for param, label in EFFECT_KNOBS.get(node_id, ()):
            value = p.get(param)
            if label in used or not isinstance(value, (int, float)) or isinstance(value, bool) \
                    or not 0 <= value <= 1:
                continue
            used.add(label)
            knobs.append({'key': '{}.{}'.format(node_id, param),
                          'label': '{} {}'.format(node_id.title(), label), 'value': float(value),
                          'kind': 'level'})
    return knobs


def tone_key(doc):
    '''Identity of a tone's content: auditioning the same content twice is skipped.'''
    return hashlib.sha1(json.dumps(doc, sort_keys=True).encode()).hexdigest()[:16]


# ---------------------------------------------------------------- agent HTTP

class AmpClient(object):
    '''
    Talks to the amp agent in a worker thread. request(slot) and audition(doc) never
    block: only the newest wish (a slot or a tone) is sent, and a failed one is
    retried a few times. snapshot() returns the last known agent state for the Tk
    thread; preset(slot) returns a stored preset once the worker has fetched it.
    '''

    RETRIES = 6

    def __init__(self, url, poll=5.0, timeout=45.0, control=None, live_slot=100):
        self.url = url.rstrip('/')
        parts = urlsplit(self.url)
        if parts.scheme not in ('http', 'https') or not parts.netloc:
            raise ValueError('amp_url must be like http://localhost:8081, not {!r}'.format(url))
        self.api = self.url + '/api/micro-plus'
        if not valid_slot(live_slot):
            raise ValueError('live slot must be 1..100')
        self.live_slot = live_slot          # tones are auditioned on this reserved slot
        # The agent only accepts POSTs whose Origin matches its own host (it guards against
        # other web pages). We are not a browser, so we state the agent's own origin.
        self.origin = '{}://{}'.format(parts.scheme, parts.netloc)
        self.poll = poll
        self.timeout = timeout
        self.control = control
        self.cond = threading.Condition()
        self.target = None          # ('slot', n) or ('tone', key, doc) still to send
        self.tries = 0
        self.busy = None            # what is being sent right now (for display)
        self.current_tone = None    # key of the tone the amp plays now, if any
        self.presets = {}           # slot -> stored preset JSON (fetched on demand)
        self.wanted = []            # slots to fetch
        self.connections = None     # agent connection count when presets were fetched
        self.state = {}             # last agent snapshot
        self.error = None           # last problem, for display
        self.quit = False
        self.thread = threading.Thread(target=self._run, name='amp', daemon=True)

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        with self.cond:
            self.quit = True
            self.cond.notify()
        if self.thread.is_alive():
            # the worker may hang in an HTTP request (a switch can wait up to 45 s for
            # the agent); it is a daemon thread, so don't hold up the exit for it
            self.thread.join(timeout=0.5)
        if self.control:
            self.control.shutdown()

    def request(self, slot):
        '''Ask for slot; replaces any older request that hasn't been sent yet.'''
        if not valid_slot(slot):
            raise ValueError('slot must be 1..100, not {!r}'.format(slot))
        with self.cond:
            self.target = ('slot', slot)
            self.tries = 0
            self.cond.notify()

    def audition(self, doc):
        '''Play a tone (preset JSON) on the live slot, without saving it; replaces any
        older request that hasn't been sent yet.'''
        if 'amp' not in nodes(doc):
            raise ValueError('a tone needs an amp node')
        with self.cond:
            self.target = ('tone', tone_key(doc), doc)
            self.tries = 0
            self.cond.notify()

    def preset(self, slot):
        '''The preset stored in slot as JSON, or None while it is being fetched.'''
        if not valid_slot(slot):
            raise ValueError('slot must be 1..100, not {!r}'.format(slot))
        with self.cond:
            if slot in self.presets:
                return self.presets[slot]
            if slot not in self.wanted:
                self.wanted.append(slot)
                self.cond.notify()
            return None

    def snapshot(self):
        with self.cond:
            pending = self.target[1] if self.target and self.target[0] == 'slot' else \
                (self.target[2].get('info', {}).get('displayName') if self.target else None)
            return {'state': dict(self.state), 'error': self.error,
                    'pending': pending, 'busy': self.busy, 'tone': self.current_tone}

    def names(self):
        '''The amp's 100 preset names as far as known (may be empty).'''
        with self.cond:
            return [p.get('name') or '' for p in self.state.get('presets') or []]

    # -- worker

    def _due(self, next_poll, retry_at):
        '''Seconds to wait before the next action (0 = act now). Holds self.cond.'''
        now = time.monotonic()
        if self.target is not None:
            # a fresh request goes out at once, a failed one only when its retry is due
            return 0 if self.tries == 0 else max(0, retry_at - now)
        if self.wanted:
            return 0
        return max(0, next_poll - now)

    def _run(self):
        next_poll = 0
        retry_at = 0
        while True:
            with self.cond:
                while not self.quit:
                    wait = self._due(next_poll, retry_at)
                    if wait <= 0:
                        break
                    self.cond.wait(timeout=wait)
                if self.quit:
                    return
                target = self.target
                wanted = self.wanted[0] if self.wanted and target is None else None
            if wanted is not None:
                self._fetch_preset(wanted)
                continue
            if self.control:
                try:
                    self.control.check()
                except Exception:
                    log.warning('amp agent control failed', exc_info=True)
            if target is not None:
                self._send(target)
                if self._retry_pending(target):
                    retry_at = time.monotonic() + self.poll
                    continue
            else:
                self._fetch()
            next_poll = time.monotonic() + self.poll

    def _retry_pending(self, target):
        '''After a failed request: keep it pending for a later retry, or give up.'''
        with self.cond:
            if self.target is not target:
                return False            # done, or a newer request is waiting
            self.tries += 1
            if self.tries >= self.RETRIES:
                log.warning('amp: giving up on %s', target[1] if target[0] == 'slot' else 'the tone')
                self.target = None
                return False
            return True

    def _send(self, target):
        with self.cond:
            known = self.state
            if self.target is not target:
                return                  # superseded by a newer request: never send an old one
            playing = known.get('connected') and known.get('audition')
            if target[0] == 'slot':
                if known.get('connected') and known.get('preset') == target[1] and not playing:
                    self.target = None  # already there, nothing to send
                    return
                url, body, label = self.api, {'preset': target[1]}, str(target[1])
            else:
                if playing and self.current_tone == target[1]:
                    self.target = None  # this tone is already playing
                    return
                url = self.api + '/audition'
                body = {'preset': target[2], 'slot': self.live_slot}
                label = target[2].get('info', {}).get('displayName') or 'tone'
            self.busy = label
        log.info('amp: %s %s', 'switch to slot' if target[0] == 'slot' else 'audition', label)
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method='POST', headers={
            'Content-Type': 'application/json', 'Origin': self.origin})
        state, error = self._call(req)
        with self.cond:
            self.busy = None
            if state is not None:
                self.state = state
                self.error = None
                self.current_tone = target[1] if target[0] == 'tone' else None
                if self.target is target:
                    self.target = None
            else:
                self.error = error

    def _fetch_preset(self, slot):
        req = urllib.request.Request('{}/preset?slot={}'.format(self.api, slot))
        doc, error = self._call(req, parse=self._parse_preset)
        with self.cond:
            if slot in self.wanted:
                self.wanted.remove(slot)
            if doc is not None:
                self.presets[slot] = doc
            else:
                self.error = error

    def _fetch(self):
        state, error = self._call(urllib.request.Request(self.api))
        with self.cond:
            if state is not None:
                if state.get('connections') != self.connections:
                    # a new connection: presets may have been edited meanwhile
                    self.connections = state.get('connections')
                    self.presets = {}
                if not state.get('audition'):
                    self.current_tone = None
                self.state = state
                self.error = None
            else:
                self.state = {}
                self.error = error

    def _call(self, req, parse=None):
        '''Return (parsed reply, None) or (None, error text).'''
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return (parse or self._parse)(resp.read()), None
        except urllib.error.HTTPError as e:
            try:
                message = json.loads(e.read()).get('error')
            except Exception:
                message = None
            finally:
                e.close()
            return None, message or 'agent error {}'.format(e.code)
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
            reason = getattr(e, 'reason', e)
            if isinstance(reason, ConnectionRefusedError):
                return None, 'amp agent not running'
            return None, 'amp agent unreachable ({})'.format(reason)
        except ValueError as e:
            return None, 'amp agent sent an invalid reply ({})'.format(e)

    @staticmethod
    def _parse_preset(raw):
        doc = json.loads(raw)
        if not isinstance(doc, dict) or 'amp' not in nodes(doc):
            raise ValueError('not a preset with an amp')
        return doc

    @staticmethod
    def _parse(raw):
        state = json.loads(raw)
        if not isinstance(state, dict):
            raise ValueError('not an object')
        presets = state.get('presets')
        if presets is not None and not isinstance(presets, list):
            raise ValueError('presets is not a list')
        preset = state.get('preset')
        if preset is not None and not valid_slot(preset):
            raise ValueError('preset {!r} out of range'.format(preset))
        return state


def describe(snap):
    '''Short status text for the window title.'''
    state, error = snap['state'], snap['error']
    if snap.get('busy'):
        return 'amp → {}…'.format(snap['busy'])
    if error and not state:
        return 'amp: {}'.format(error)
    preset, name = state.get('preset'), state.get('name')
    if state.get('connected') and state.get('audition'):
        text = 'amp ▶ {}'.format(state['audition'].get('name') or 'tone')
    elif state.get('connected'):
        text = 'amp {} {}'.format(preset, name or '') if preset else 'amp connected'
    elif state.get('asleep'):
        text = 'amp asleep (last {} {})'.format(preset, name or '') if preset else 'amp asleep'
    elif state:
        text = 'amp {}'.format(state.get('phase') or 'disconnected')
    else:
        text = 'amp ?'
    if error:
        text += ' – ' + error
    return text.strip()


# ---------------------------------------------------------------- agent unit

class AgentControl(object):
    '''
    Start the agent's systemd user unit while the amp's Bluetooth audio sink exists,
    stop it when the sink is gone. A unit that was already running when we first
    looked was started by someone else and is never stopped by us.
    '''

    def __init__(self, unit, sink='Mustang Micro Plus', runner=None):
        self.unit = unit
        self.sink = sink.lower()
        self.run = runner or self._run_cmd
        self.started = False

    @staticmethod
    def _run_cmd(args):
        return subprocess.run(args, capture_output=True, text=True, timeout=10)

    def sink_present(self):
        out = self.run(['pactl', '-f', 'json', 'list', 'sinks'])
        if out.returncode == 0:
            try:
                sinks = json.loads(out.stdout)
                return any(self.sink in (s.get('description') or '').lower() for s in sinks)
            except (ValueError, AttributeError):
                pass    # pactl 17 complains about non-ASCII properties; use the text form
        out = self.run(['pactl', 'list', 'sinks'])
        return out.returncode == 0 and any(
            line.strip().lower().startswith('description:') and self.sink in line.lower()
            for line in out.stdout.splitlines())

    def active(self):
        return self.run(['systemctl', '--user', 'is-active', '--quiet', self.unit]).returncode == 0

    def check(self):
        present = self.sink_present()
        if present and not self.active():
            log.info('amp sink present, starting %s', self.unit)
            if self.run(['systemctl', '--user', 'start', self.unit]).returncode == 0:
                self.started = True
            else:
                log.warning('could not start %s', self.unit)
        elif not present and self.started:
            log.info('amp sink gone, stopping %s', self.unit)
            self.shutdown()

    def shutdown(self):
        if self.started:
            self.run(['systemctl', '--user', 'stop', self.unit])
            self.started = False


# ---------------------------------------------------------------- Tk side

def tone_document(db, client, tone):
    '''
    The preset JSON for a saved tone, or None while its base preset is still being
    fetched. Measured level corrections are used only if measured with a real playing
    signal (signal or played method): the noise method is not valid across presets
    (see maneline's docs/MUSTANG_MICRO_PLUS_LEVELS.md).
    '''
    base = client.preset(tone['base_slot'])
    if base is None:
        return None
    level = db.getPresetLevel(tone['base_slot'])
    return build_tone(base, tone['level_db'], tone['params'], name=tone['name'],
                      correction_db=level[0] if level else 0.0)


class AmpCues(object):
    '''
    Decides which slot or tone the playing song wants at its current position and
    asks the client for it when that changes. Call tick() regularly from the Tk thread.
    '''

    def __init__(self, db, client, lead_ms=300, default_slot=None):
        self.db = db
        self.client = client
        self.lead_ms = lead_ms
        self.default_slot = default_slot if valid_slot(default_slot) else None
        self.song_id = None
        self.cues = []
        self.applied = None         # (song_id, cue index) last requested
        self.warning = None

    def load(self, song_id):
        self.song_id = song_id
        self.applied = None
        self.cues = []
        if song_id is None:
            return
        song_cues = self.db.getSongPresets(song_id)
        info = self.db.getPlayInfo(song_id)
        base = self.db.getArtistPreset(info[1]) if info else None
        if base is None and self.default_slot:
            base = (self.default_slot, None)
        self.cues = build_cues(song_cues, base)

    def reload(self):
        '''After cues were edited: re-read and re-apply at the current position.'''
        self.load(self.song_id)

    def tick(self, song_id, pos_ms):
        if song_id != self.song_id:
            self.load(song_id)
        index = active_index(self.cues, pos_ms, self.lead_ms)
        if index is None:
            return
        key = (song_id, index)
        if key == self.applied:
            return
        start, slot, slot_name, tone_id = self.cues[index]
        if tone_id is not None:
            self._tone(key, start, tone_id)
            return
        self.applied = key
        renamed = name_mismatch(slot, slot_name, self.client.names())
        if renamed is not None:
            self.warning = 'slot {} is now "{}", was "{}" – not switched (g to fix)'.format(
                slot, renamed, slot_name)
            log.warning('amp: %s', self.warning)
            return
        self.warning = None
        log.debug('amp cue %s @ %s -> slot %d', key[0], fmt_ms(start), slot)
        self.client.request(slot)

    def _tone(self, key, start, tone_id):
        tone = self.db.getTone(tone_id)
        if tone is None:
            self.applied = key
            self.warning = 'a cue at {} plays a deleted tone'.format(fmt_ms(start))
            return
        renamed = name_mismatch(tone['base_slot'], tone['base_name'], self.client.names())
        if renamed is not None:
            self.applied = key
            self.warning = 'tone "{}": base slot {} is now "{}", was "{}" – not played'.format(
                tone['name'], tone['base_slot'], renamed, tone['base_name'])
            log.warning('amp: %s', self.warning)
            return
        doc = tone_document(self.db, self.client, tone)
        if doc is None:
            return                  # base preset not fetched yet: try again next tick
        self.applied = key
        self.warning = None
        log.debug('amp cue %s @ %s -> tone %s', key[0], fmt_ms(start), tone['name'])
        self.client.audition(doc)
