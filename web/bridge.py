'''
Amp bridge: plays the web app's cues on the Mustang Micro Plus.

Runs next to the amp agent (maneline's mmp_web.py) on the machine the amp is
paired with, as a systemd user unit. It follows the user's web session over an
outbound event stream, like a display, picks the cue for the playing position
with the Tk app's own logic (ChordsPlay.amp.AmpCues) and switches through the
agent (ChordsPlay.amp.AmpClient). The amp's state goes back to the server, so the
controller page can show it and learn from it (key G).

Standard library only, plus ChordsPlay/amp.py.

Config via env:
  CHORDSPLAY_BRIDGE_URL  https://.../chordsplay/d/bridge/<token>/ (from the page's Amp dialog)
  AMP_URL                the agent (default http://localhost:8081)
  AMP_UNIT               start the agent's user unit while the amp's audio sink exists
                         (default chordsplay-amp-agent.service; empty: never)
  AMP_SINK               the amp's audio sink name (default "Mustang Micro Plus")
  AMP_DEFAULT_SLOT, AMP_LEAD_MS   as amp_default_slot / amp_lead_ms in the Tk app
'''

import json
import logging
import os
import sys
import threading
import time
import urllib.error
import urllib.request

from ChordsPlay import amp as ampmod

log = logging.getLogger('bridge')

TICK_S = 0.2            # as often as the Tk app checks the cue
REPORT_S = 10           # report the amp's state at least this often (server: BRIDGE_SEEN_S 30)
COMMAND_MAX_AGE_S = 10  # a "try now" older than this (e.g. from before a reconnect) is ignored
RECONNECT_S = (3, 60)   # wait before reconnecting the event stream, growing to the max


class SongPlan(object):
    '''The database calls AmpCues makes, answered from the server's /song/<id> reply.'''

    def __init__(self, fetch):
        self.fetch = fetch      # song_id -> dict from the server
        self.song = None

    def load(self, song_id):
        self.song = self.fetch(song_id)

    def _for(self, song_id):
        if self.song is None or self.song['id'] != song_id:
            self.load(song_id)
        return self.song

    def getSongPresets(self, song_id):
        return [tuple(cue) for cue in self._for(song_id)['cues']]

    def getPlayInfo(self, song_id):
        song = self._for(song_id)
        return (song['title'], song['artist'])

    def getArtistPreset(self, artist):
        base = self.song.get('artist_preset') if self.song else None
        return tuple(base) if base else None

    def getTone(self, tone_id):
        return (self.song or {}).get('tones', {}).get(str(tone_id))

    def getPresetLevel(self, slot, methods=('signal', 'played')):
        level = (self.song or {}).get('levels', {}).get(str(slot))
        return tuple(level) if level else None


class Session(object):
    '''The followed controller: song and position, extrapolated between events.'''

    def __init__(self):
        self.lock = threading.Lock()
        self.now = None         # (song_id, t at `since`, playing, since (monotonic))
        self.plan_rev = None
        self.commands = []      # try now / fetch a preset, in order

    def update(self, channel, data):
        with self.lock:
            if channel == 'now':
                if not isinstance(data.get('id'), int):
                    self.now = None
                    return
                t = float(data.get('t') or 0)
                if data.get('playing'):
                    # the state is as of `at`; the event left the server at server_time
                    t += max(0.0, float(data.get('server_time', 0)) - float(data.get('at') or 0))
                self.now = (data['id'], t, bool(data.get('playing')), time.monotonic())
            elif channel == 'plan':
                self.plan_rev = data.get('rev')
            elif channel == 'cmd':
                if time.time() - float(data.get('at') or 0) < COMMAND_MAX_AGE_S:
                    self.commands.append(data)

    def position(self):
        '''(song_id, ms, playing) or None.'''
        with self.lock:
            if self.now is None:
                return None
            song_id, t, playing, since = self.now
            if playing:
                t += time.monotonic() - since
            return song_id, int(t * 1000), playing

    def take_commands(self):
        with self.lock:
            commands, self.commands = self.commands, []
            return commands


def read_events(response, on_event):
    '''Parse a text/event-stream response; on_event(channel, data) per event.'''
    channel, data = 'now', []
    for raw in response:
        line = raw.decode('utf-8').rstrip('\r\n')
        if not line:
            if data:
                try:
                    on_event(channel, json.loads('\n'.join(data)))
                except ValueError:
                    log.warning('bridge: bad event %r', data)
            channel, data = 'now', []
        elif line.startswith('event:'):
            channel = line[6:].strip()
        elif line.startswith('data:'):
            data.append(line[5:].strip())


class Bridge(object):

    def __init__(self, url, client, lead_ms=300, default_slot=None, opener=urllib.request.urlopen):
        self.url = url.rstrip('/') + '/'
        self.client = client
        self.open = opener
        self.session = Session()
        self.plan = SongPlan(self.fetch_song)
        self.cues = ampmod.AmpCues(self.plan, client, lead_ms=lead_ms, default_slot=default_slot)
        self.plan_rev = None
        self.trying = None          # (command, until): a tone waits for its base preset
        self.fetching = {}          # slot -> until: presets the server asked for (knobs)
        self.reported = (None, 0)   # (report, monotonic)
        self.connected = False
        self.quit = threading.Event()

    def fetch_song(self, song_id):
        with self.open(self.url + 'song/{}'.format(song_id), timeout=15) as response:
            return json.loads(response.read())

    def follow(self):
        '''The event stream, reconnected for ever (in a thread).'''
        wait = RECONNECT_S[0]
        while not self.quit.is_set():
            try:
                with self.open(self.url + 'events', timeout=3 * 20) as response:
                    log.info('bridge: following %s', self.url.split('/d/bridge/')[0])
                    self.connected = True
                    wait = RECONNECT_S[0]
                    read_events(response, self.session.update)
            except urllib.error.HTTPError as e:
                log.error('bridge: the server says %s%s', e.code,
                          ' - the token is not valid (any more): make a new one in the Amp dialog'
                          if e.code == 404 else '')
                wait = RECONNECT_S[1]
            except (urllib.error.URLError, OSError, ValueError) as e:
                log.warning('bridge: event stream lost (%s)', getattr(e, 'reason', e))
            self.connected = False
            self.quit.wait(wait)
            wait = min(RECONNECT_S[1], wait * 2)

    def step(self):
        '''One tick: cues, then commands (a "try now" wins over a cue re-applied after an
        edit in the same tick), then the report. Never raises for the network.'''
        if self.session.plan_rev != self.plan_rev:
            self.plan_rev = self.session.plan_rev
            self.plan.song = None
            self.cues.reload()
        where = self.session.position()
        if where is not None and where[2]:
            song_id, ms, _ = where
            try:
                self.cues.tick(song_id, ms)
            except (urllib.error.URLError, OSError, ValueError) as e:
                # the song's cues could not be fetched: try again on the next tick
                log.warning('bridge: cues of song %s not loaded (%s)', song_id, getattr(e, 'reason', e))
                self.cues.song_id = None
                self.plan.song = None
        for command in self.session.take_commands():
            if 'fetch' in command:
                self.fetching[int(command['fetch'])] = time.monotonic() + 30
            else:
                self.trying = (command, time.monotonic() + 30)
        if self.trying is not None:
            self.try_now()
        self.send_presets()
        self.report()

    def send_presets(self):
        '''Presets the server asked for, as soon as the agent has read them.'''
        for slot, until in list(self.fetching.items()):
            doc = self.client.preset(slot)
            if doc is None:
                if time.monotonic() > until:
                    log.warning('bridge: slot %d could not be read', slot)
                    del self.fetching[slot]
                continue
            request = urllib.request.Request(self.url + 'preset/{}'.format(slot), data=json.dumps(doc).encode(),
                                             method='PUT', headers={'Content-Type': 'application/json'})
            try:
                with self.open(request, timeout=15) as response:
                    response.read()
                del self.fetching[slot]
            except (urllib.error.URLError, OSError) as e:
                log.warning('bridge: sending slot %d failed (%s)', slot, getattr(e, 'reason', e))

    def try_now(self):
        command, until = self.trying
        if 'slot' in command:
            self.client.request(command['slot'])
            self.trying = None
            return
        tone, level = command.get('tone'), command.get('level')
        base = self.client.preset(tone['base_slot'])
        if base is None:
            if time.monotonic() > until:
                log.warning('bridge: base preset of tone %s not available', tone.get('name'))
                self.trying = None
            return      # being fetched: next tick
        self.client.audition(ampmod.build_tone(base, tone['level_db'], tone['params'], name=tone['name'],
                                               correction_db=level[0] if level else 0.0))
        self.trying = None

    def report(self):
        snap = self.client.snapshot()
        report = dict(snap, warning=self.cues.warning)
        last, when = self.reported
        if report == last and time.monotonic() - when < REPORT_S:
            return
        request = urllib.request.Request(self.url + 'amp', data=json.dumps(report).encode(), method='PUT',
                                         headers={'Content-Type': 'application/json'})
        try:
            with self.open(request, timeout=10) as response:
                response.read()
            self.reported = (report, time.monotonic())
        except (urllib.error.URLError, OSError) as e:
            log.debug('bridge: report failed (%s)', getattr(e, 'reason', e))
            self.reported = (last, time.monotonic())    # don't retry on every tick

    def run(self):
        threading.Thread(target=self.follow, name='events', daemon=True).start()
        while not self.quit.is_set():
            self.step()
            self.quit.wait(TICK_S)


def main():
    logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')
    url = os.environ.get('CHORDSPLAY_BRIDGE_URL')
    if not url or '/d/bridge/' not in url:
        log.error('CHORDSPLAY_BRIDGE_URL missing: make a bridge link in the web app\'s Amp dialog (key g)')
        return 1
    unit = os.environ.get('AMP_UNIT', 'chordsplay-amp-agent.service')
    control = ampmod.AgentControl(unit, os.environ.get('AMP_SINK', 'Mustang Micro Plus')) if unit else None
    client = ampmod.AmpClient(os.environ.get('AMP_URL', 'http://localhost:8081'), control=control).start()
    default = os.environ.get('AMP_DEFAULT_SLOT')
    bridge = Bridge(url, client, lead_ms=int(os.environ.get('AMP_LEAD_MS', '300')),
                    default_slot=int(default) if default else None)
    import signal
    signal.signal(signal.SIGTERM, lambda *_: bridge.quit.set())
    try:
        bridge.run()
    except KeyboardInterrupt:
        pass
    finally:
        client.stop()
    return 0


if __name__ == '__main__':
    sys.exit(main())
