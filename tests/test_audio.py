# SPDX-FileCopyrightText: 2016-2026 joba1
# SPDX-License-Identifier: AGPL-3.0-or-later
'''The song through the Mustang (mmp/audio.py): pactl and bluetoothctl faked.'''

import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from mmp import audio as audiomod          # noqa: E402


class FakeSystem(object):
    def __init__(self, sinks=(), streams=(), paired='Device 84:17:15:2B:2A:FA Mustang Micro Plus\n',
                 connect_adds=None):
        self.sinks = list(sinks)
        self.streams = list(streams)
        self.paired = paired
        self.connect_adds = connect_adds    # a sink that appears when connected
        self.calls = []

    def __call__(self, args, timeout=20):
        self.calls.append(args)
        if args[:4] == ['pactl', '-f', 'json', 'list']:
            return json.dumps(self.sinks if args[4] == 'sinks' else self.streams)
        if args[:2] == ['bluetoothctl', 'devices']:
            return self.paired
        if args[:2] == ['bluetoothctl', 'connect'] and self.connect_adds:
            self.sinks.append(self.connect_adds)
            return 'Connection successful\n'
        if args[:2] == ['pactl', 'move-sink-input']:
            for s in self.streams:
                if str(s['index']) == args[2]:
                    s['sink'] = next(k['index'] for k in self.sinks if k['name'] == args[3])
        return ''


AMP = {'index': 70, 'name': 'bluez_output.84_17_15_2B_2A_FA.1', 'description': 'Mustang Micro Plus'}
USB = {'index': 59, 'name': 'alsa_output.usb-Fender_Mustang_Micro_Plus-00.analog-stereo',
       'description': 'Mustang Micro Plus Analog Stereo'}


def stream(index, sink, binary='chromium'):
    return {'index': index, 'sink': sink, 'properties': {'application.process.binary': binary}}


class AmpAudio(unittest.TestCase):

    def test_state(self):
        sys_ = FakeSystem(sinks=[USB, AMP], streams=[stream(5, 59), stream(6, 59, 'spotify')])
        a = audiomod.AmpAudio(runner=sys_)
        self.assertEqual(a.state(), {'bluetooth': True, 'browser': True, 'routed': False, 'busy': False, 'error': None})
        self.assertEqual(a.sink(), (70, AMP['name']))                 # the Bluetooth output, not USB
        self.assertEqual(a.streams(), [(5, 59)])                       # only the browser
        sys_.streams[0]['sink'] = 70
        self.assertTrue(a.state()['routed'])

    def test_route_connects_and_moves_only_the_browser(self):
        sys_ = FakeSystem(sinks=[USB], streams=[stream(5, 59, 'firefox'), stream(6, 59, 'spotify')], connect_adds=AMP)
        a = audiomod.AmpAudio(runner=sys_)
        self.assertIsNone(a.route())
        self.assertIn(['bluetoothctl', 'connect', '84:17:15:2B:2A:FA'], sys_.calls)
        self.assertIn(['pactl', 'move-sink-input', '5', AMP['name']], sys_.calls)
        self.assertNotIn(['pactl', 'move-sink-input', '6', AMP['name']], sys_.calls)
        self.assertTrue(a.state()['routed'])

    def test_route_errors(self):
        a = audiomod.AmpAudio(runner=FakeSystem(sinks=[USB], paired=''))
        self.assertIn('not paired', a.route())
        a = audiomod.AmpAudio(runner=FakeSystem(sinks=[AMP], streams=[]))
        self.assertIn('no browser', a.route())

    def test_async_and_always(self):
        sys_ = FakeSystem(sinks=[AMP], streams=[stream(5, 59)])
        a = audiomod.AmpAudio(runner=sys_)
        self.assertTrue(a.route_async())
        deadline = time.monotonic() + 2
        while a.busy and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(a.state()['routed'])
        # "always": no new try within AUTO_RETRY_S after one
        sys_.streams[0]['sink'] = 59
        calls = len(sys_.calls)
        a.keep(a.state())
        self.assertFalse(a.busy)
        self.assertFalse(any(c[:2] == ['pactl', 'move-sink-input'] for c in sys_.calls[calls:]))
        a.tried = None
        a.keep(a.state())
        deadline = time.monotonic() + 2
        while a.busy and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(a.state()['routed'])


if __name__ == '__main__':
    unittest.main()
