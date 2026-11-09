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
    song_cues: [(start_ms, slot, slot_name)], base_slot: (slot, slot_name) or None.
    The base (artist slot or default) fills the start if the song has no cue at 0.
    '''
    cues = sorted(song_cues)
    if base_slot is not None and not any(start == 0 for start, _, _ in cues):
        cues.insert(0, (0, base_slot[0], base_slot[1]))
    return cues


def active_index(cues, pos_ms, lead_ms=0):
    '''Index of the cue that applies at pos_ms, or None before the first cue.'''
    pos_ms = max(0, pos_ms)
    index = None
    for i, (start, _, _) in enumerate(cues):
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


# ---------------------------------------------------------------- agent HTTP

class AmpClient(object):
    '''
    Talks to the amp agent in a worker thread. request(slot) never blocks: only the
    newest wanted slot is sent, and a failed request is retried a few times.
    snapshot() returns the last known agent state for the Tk thread.
    '''

    RETRIES = 6

    def __init__(self, url, poll=5.0, timeout=45.0, control=None):
        self.url = url.rstrip('/')
        parts = urlsplit(self.url)
        if parts.scheme not in ('http', 'https') or not parts.netloc:
            raise ValueError('amp_url must be like http://localhost:8081, not {!r}'.format(url))
        self.api = self.url + '/api/micro-plus'
        # The agent only accepts POSTs whose Origin matches its own host (it guards against
        # other web pages). We are not a browser, so we state the agent's own origin.
        self.origin = '{}://{}'.format(parts.scheme, parts.netloc)
        self.poll = poll
        self.timeout = timeout
        self.control = control
        self.cond = threading.Condition()
        self.target = None          # slot still to switch to
        self.tries = 0
        self.busy = None            # slot being switched right now
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
            self.thread.join(timeout=2)
        if self.control:
            self.control.shutdown()

    def request(self, slot):
        '''Ask for slot; replaces any older request that hasn't been sent yet.'''
        if not valid_slot(slot):
            raise ValueError('slot must be 1..100, not {!r}'.format(slot))
        with self.cond:
            self.target = slot
            self.tries = 0
            self.cond.notify()

    def snapshot(self):
        with self.cond:
            return {'state': dict(self.state), 'error': self.error,
                    'pending': self.target, 'busy': self.busy}

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
                slot = self.target
            if self.control:
                try:
                    self.control.check()
                except Exception:
                    log.warning('amp agent control failed', exc_info=True)
            if slot is not None:
                self._switch(slot)
                if self._retry_pending(slot):
                    retry_at = time.monotonic() + self.poll
                    continue
            else:
                self._fetch()
            next_poll = time.monotonic() + self.poll

    def _retry_pending(self, slot):
        '''After a failed switch: keep slot pending for a later retry, or give up.'''
        with self.cond:
            if self.target != slot:
                return False            # done, or a newer request is waiting
            self.tries += 1
            if self.tries >= self.RETRIES:
                log.warning('amp: giving up switching to slot %d', slot)
                self.target = None
                return False
            return True

    def _switch(self, slot):
        with self.cond:
            known = self.state
            log.debug('amp: want %d, known connected=%s preset=%s', slot,
                      known.get('connected'), known.get('preset'))
            if self.target != slot:
                return                  # superseded by a newer request: never send an old one
            if known.get('connected') and known.get('preset') == slot:
                self.target = None      # already there, nothing to send
                return
            self.busy = slot
        log.info('amp: switch to slot %d', slot)
        body = json.dumps({'preset': slot}).encode()
        req = urllib.request.Request(self.api, data=body, method='POST', headers={
            'Content-Type': 'application/json', 'Origin': self.origin})
        state, error = self._call(req)
        with self.cond:
            self.busy = None
            if state is not None:
                self.state = state
                self.error = None
                if self.target == slot:
                    self.target = None
            else:
                self.error = error

    def _fetch(self):
        state, error = self._call(urllib.request.Request(self.api))
        with self.cond:
            if state is not None:
                self.state = state
                self.error = None
            else:
                self.state = {}
                self.error = error

    def _call(self, req):
        '''Return (state, None) or (None, error text).'''
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return self._parse(resp.read()), None
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
    if state.get('connected'):
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

class AmpCues(object):
    '''
    Decides which slot the playing song wants at its current position and asks the
    client for it when that changes. Call tick() regularly from the Tk thread.
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
        self.applied = key
        start, slot, slot_name = self.cues[index]
        renamed = name_mismatch(slot, slot_name, self.client.names())
        if renamed is not None:
            self.warning = 'slot {} is now "{}", was "{}" – not switched (g to fix)'.format(
                slot, renamed, slot_name)
            log.warning('amp: %s', self.warning)
            return
        self.warning = None
        log.debug('amp cue %s @ %s -> slot %d', song_id, fmt_ms(start), slot)
        self.client.request(slot)
