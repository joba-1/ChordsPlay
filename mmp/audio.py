# SPDX-FileCopyrightText: 2016-2026 joba1
# SPDX-License-Identifier: AGPL-3.0-or-later
'''
The song through the Mustang: the amp bridge can connect the amp's Bluetooth audio and
move the browser's playback there (PipeWire), on the computer the amp is paired with.

A browser cannot do this itself: a page may only choose among outputs the system already
has (and only in some browsers). The bridge runs on that computer as the same user, so it
can connect the amp with bluetoothctl and move the browser's stream with pactl. A browser
plays all its tabs through one stream, so the browser's whole sound moves; WirePlumber
then remembers the amp as the browser's output while the amp is there.

Standard library only; the commands run through run(args, timeout) so tests can fake them.
'''

import json
import logging
import subprocess
import threading
import time

log = logging.getLogger('bridge')

# programs whose playback counts as "the browser" (application.process.binary)
BROWSERS = {'chromium', 'chromium-browser', 'chrome', 'google-chrome', 'google-chrome-stable', 'brave',
            'brave-browser', 'msedge', 'microsoft-edge', 'vivaldi', 'vivaldi-bin', 'opera',
            'firefox', 'firefox-bin', 'firefox-esr', 'librewolf', 'floorp', 'zen', 'zen-bin'}
CONNECT_WAIT_S = 15         # how long a new Bluetooth audio link may take to show up as an output
ERROR_KEEP_S = 60           # a failed try is reported this long (or until the song reaches the amp)
AUTO_RETRY_S = 60           # "always": don't try again sooner after a failure


def run(args, timeout=20):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout).stdout


class AmpAudio(object):
    '''State of the song's way to the amp, and routing it there (in a thread).'''

    def __init__(self, name='Mustang Micro Plus', runner=run):
        self.name = name            # text in the amp's Bluetooth output and device name
        self.run = runner
        self.lock = threading.Lock()
        self.busy = False
        self.error = None
        self.error_at = 0.0         # monotonic, when error was set
        self.tried = None           # monotonic, last routing attempt (None: never; the clock
                                    # starts at boot, so 0 would mean "just now" on a fresh machine)

    def _json(self, kind):
        try:
            return json.loads(self.run(['pactl', '-f', 'json', 'list', kind], 10) or '[]')
        except (OSError, ValueError, subprocess.SubprocessError):
            return []

    def sink(self):
        '''The amp's Bluetooth output: (index, name), or None.'''
        for s in self._json('sinks'):
            if s.get('name', '').startswith('bluez_output') and self.name in (s.get('description') or ''):
                return s.get('index'), s['name']
        return None

    def streams(self):
        '''The browsers' playback streams: [(index, output index)].'''
        return [(s.get('index'), s.get('sink')) for s in self._json('sink-inputs')
                if (s.get('properties') or {}).get('application.process.binary') in BROWSERS]

    def state(self):
        '''For the page: bluetooth (the amp is an output), browser (a browser plays here),
        routed (every browser stream goes to the amp), busy, error.'''
        sink, streams = self.sink(), self.streams()
        routed = bool(sink and streams and all(out == sink[0] for _, out in streams))
        with self.lock:
            if self.error and (routed or time.monotonic() - self.error_at > ERROR_KEEP_S):
                self.error = None       # an old failure is no news any more
            return {'bluetooth': sink is not None, 'browser': bool(streams), 'routed': routed,
                    'busy': self.busy, 'error': self.error}

    def address(self):
        '''The amp's Bluetooth address among the paired devices, or None.'''
        for line in self.run(['bluetoothctl', 'devices', 'Paired'], 10).splitlines():
            parts = line.split(' ', 2)
            if len(parts) == 3 and parts[0] == 'Device' and self.name in parts[2]:
                return parts[1]
        return None

    def route(self):
        '''Connect the amp's Bluetooth audio if needed, then move the browsers' playback
        there. Returns an error text or None.'''
        sink = self.sink()
        if sink is None:
            address = self.address()
            if address is None:
                return 'the Mustang is not paired with this computer'
            log.info('audio: connecting the Mustang\'s Bluetooth audio (%s)', address)
            try:
                self.run(['bluetoothctl', 'connect', address], CONNECT_WAIT_S + 5)
            except (OSError, subprocess.SubprocessError) as e:
                return 'connecting failed: {}'.format(e)
            deadline = time.monotonic() + CONNECT_WAIT_S
            while sink is None and time.monotonic() < deadline:
                time.sleep(0.5)
                sink = self.sink()
            if sink is None:
                return 'the Mustang\'s Bluetooth audio did not connect (is it on?)'
        streams = self.streams()
        if not streams:
            return 'no browser plays on this computer (start the song here, then try again)'
        for index, out in streams:
            if out != sink[0]:
                self.run(['pactl', 'move-sink-input', str(index), sink[1]], 10)
                log.info('audio: browser stream %s moved to %s', index, sink[1])
        return None

    def route_async(self):
        '''route() in a thread; a second call while one runs does nothing.'''
        with self.lock:
            if self.busy:
                return False
            self.busy = True
            self.tried = time.monotonic()

        def work():
            try:
                error = self.route()
            except Exception as e:      # noqa: BLE001 - reported to the page, never kills the bridge
                error = str(e)
            with self.lock:
                self.busy = False
                self.error = error
                self.error_at = time.monotonic()
            if error:
                log.warning('audio: %s', error)
        threading.Thread(target=work, name='audio', daemon=True).start()
        return True

    def keep(self, state):
        '''"Always": route again when a browser plays here and its sound doesn't go to the
        amp, at most every AUTO_RETRY_S after a failed try.'''
        if state['browser'] and not state['routed'] and not state['busy'] \
                and (self.tried is None or time.monotonic() - self.tried > AUTO_RETRY_S):
            self.route_async()
