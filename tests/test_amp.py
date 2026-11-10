'''
Unit tests for Mustang preset switching (docs/spec-amp.md).

Run from the repo root:  venv/bin/python -m unittest discover -s tests
No amp, Bluetooth or display needed: the agent is a fake HTTP server.
'''

import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import ChordsPlay.db
from ChordsPlay.amp import (AgentControl, AmpClient, AmpCues, active_index, build_cues,
                            describe, fmt_ms, name_mismatch, parse_ms)


class TimeFormat(unittest.TestCase):

    def test_fmt(self):
        self.assertEqual(fmt_ms(0), '0:00')
        self.assertEqual(fmt_ms(83500), '1:23.5')
        self.assertEqual(fmt_ms(-5), '0:00')

    def test_parse(self):
        self.assertEqual(parse_ms('1:23'), 83000)
        self.assertEqual(parse_ms('1:23.5'), 83500)
        self.assertEqual(parse_ms(' 45 '), 45000)
        for bad in ('', 'x', '1:75', '-3'):
            with self.assertRaises(ValueError, msg=bad):
                parse_ms(bad)


class CueLogic(unittest.TestCase):

    def test_base_fills_start_only(self):
        self.assertEqual(build_cues([(60000, 5, 'Loud', None)], (2, 'Clean')),
                         [(0, 2, 'Clean', None), (60000, 5, 'Loud', None)])
        self.assertEqual(build_cues([(0, 7, 'Acoustic', None)], (2, 'Clean')), [(0, 7, 'Acoustic', None)])
        self.assertEqual(build_cues([(60000, 5, None)], None), [(60000, 5, None, None)])   # old 3-tuples

    def test_active_index_with_lead(self):
        cues = [(0, 1, None), (60000, 2, None)]
        self.assertEqual(active_index(cues, -1), 0)
        self.assertEqual(active_index(cues, 59600, lead_ms=300), 0)
        self.assertEqual(active_index(cues, 59700, lead_ms=300), 1)
        self.assertIsNone(active_index([(10000, 3, None)], 5000))
        self.assertIsNone(active_index([], 5000))

    def test_name_mismatch(self):
        names = ['A', 'Twin Trem ']
        self.assertIsNone(name_mismatch(2, 'Twin Trem', names))
        self.assertEqual(name_mismatch(2, 'Octobot', names), 'Twin Trem ')
        self.assertIsNone(name_mismatch(2, None, names))     # name unknown when stored
        self.assertIsNone(name_mismatch(2, 'Octobot', []))   # agent never connected


class TempDb(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = ChordsPlay.db.Db(os.path.join(self.dir.name, 't.db'), 'tester')
        self.db.conn.execute("INSERT INTO songs (title, artist) VALUES ('Song', 'Band')")
        self.song = self.db.conn.execute("SELECT id FROM songs").fetchone()[0]
        # getPlayInfo needs a file for the song
        self.db.conn.execute("INSERT INTO song_lists (name) VALUES ('/music')")
        self.db.conn.execute("INSERT INTO list_songs (song_lists_id, filename, song_id) "
                             "SELECT id, 'a.mp3', ? FROM song_lists WHERE name = '/music'", (self.song,))
        self.db.commit()

    def tearDown(self):
        self.db.conn.close()
        self.dir.cleanup()


class DbPresets(TempDb):

    def test_song_cues(self):
        db, song = self.db, self.song
        db.setSongPreset(song, 0, 3, 'Clean')
        db.setSongPreset(song, 60000, 9, 'Lead')
        self.assertEqual(db.getSongPresets(song), [(0, 3, 'Clean', None), (60000, 9, 'Lead', None)])
        db.setSongPreset(song, 61500, 10, 'Lead 2', replace_within_ms=2000)   # replaces 60000
        self.assertEqual(db.getSongPresets(song), [(0, 3, 'Clean', None), (61500, 10, 'Lead 2', None)])
        self.assertTrue(db.moveSongPreset(song, 61500, 30000))
        self.assertFalse(db.moveSongPreset(song, 99999, 1))
        db.delSongPreset(song, 0)
        self.assertEqual(db.getSongPresets(song), [(30000, 10, 'Lead 2', None)])

    def test_slot_range_enforced(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.setSongPreset(self.song, 0, 101)
        self.db.rollback()
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.setArtistPreset('Band', 0)

    def test_artist_rename_usage(self):
        db = self.db
        db.setArtistPreset('Band', 4, 'Twin Trem')
        db.setSongPreset(self.song, 0, 4, 'Twin Trem')
        self.assertEqual(db.getArtistPreset('Band'), (4, 'Twin Trem'))
        self.assertEqual(db.presetUsage(), {4: 2})
        db.renamePresetSlot(4, 'Octobot')
        self.assertEqual(db.getArtistPreset('Band'), (4, 'Octobot'))
        self.assertEqual(db.getSongPresets(self.song), [(0, 4, 'Octobot', None)])
        db.delArtistPreset('Band')
        self.assertIsNone(db.getArtistPreset('Band'))

    def test_existing_db_gets_tables(self):
        '''Opening an existing DB again must not fail on the new tables.'''
        again = ChordsPlay.db.Db(self.db.conn.execute("PRAGMA database_list").fetchone()[2], 'tester')
        self.assertEqual(again.getSongPresets(self.song), [])
        again.conn.close()


class FakeClient(object):
    def __init__(self, names=()):
        self.requests = []
        self._names = list(names)

    def request(self, slot):
        self.requests.append(slot)

    def names(self):
        return self._names


class Cues(TempDb):

    def test_switches_at_cues_and_on_restart(self):
        self.db.setSongPreset(self.song, 0, 3, None)
        self.db.setSongPreset(self.song, 60000, 9, None)
        client = FakeClient()
        cues = AmpCues(self.db, client, lead_ms=300)
        for pos in (0, 200, 30000, 59800, 61000, 90000):
            cues.tick(self.song, pos)
        self.assertEqual(client.requests, [3, 9])      # each cue once
        cues.tick(self.song, 0)                         # song repeats or seek back
        self.assertEqual(client.requests, [3, 9, 3])

    def test_artist_then_default(self):
        client = FakeClient()
        cues = AmpCues(self.db, client, default_slot=12)
        cues.tick(self.song, 0)
        self.assertEqual(client.requests, [12])
        self.db.setArtistPreset('Band', 5, None)
        cues.reload()
        cues.tick(self.song, 1000)
        self.assertEqual(client.requests, [12, 5])

    def test_no_cue_leaves_amp_alone(self):
        client = FakeClient()
        AmpCues(self.db, client).tick(self.song, 0)
        self.assertEqual(client.requests, [])

    def test_renamed_slot_not_switched(self):
        self.db.setSongPreset(self.song, 0, 2, 'Twin Trem')
        client = FakeClient(names=['A', 'Octobot'])
        cues = AmpCues(self.db, client)
        cues.tick(self.song, 0)
        self.assertEqual(client.requests, [])
        self.assertIn('Octobot', cues.warning)


class FakeAgent(object):
    '''Minimal stand-in for maneline's mmp_web.py API.'''

    def __init__(self, switch_delay=0.0):
        self.state = {'connected': True, 'asleep': False, 'phase': 'connected', 'preset': 1,
                      'name': 'P1', 'presets': [{'preset': i, 'name': 'P{}'.format(i)}
                                                for i in range(1, 101)]}
        self.switch_delay = switch_delay
        self.posts = []
        self.auditions = []     # (name, slot) of every audition POST
        self.preset_gets = []
        self.origins = []
        self.fail = 0           # answer this many POSTs with 503
        agent = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, obj):
                body = json.dumps(obj).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path.startswith('/api/micro-plus/preset?slot='):
                    slot = int(self.path.split('=')[1])
                    agent.preset_gets.append(slot)
                    return self.reply(200, {'info': {'displayName': 'P{}'.format(slot)},
                                            'audioGraph': {'nodes': [{'nodeId': 'amp', 'FenderId': 'ACD_Test',
                                                                      'dspUnitParameters': {'volume': -10.0, 'gain': 0.5}}]}})
                self.reply(200, agent.state)

            def do_POST(self):
                agent.origins.append(self.headers.get('Origin'))
                if self.headers.get('Origin') != 'http://' + self.headers.get('Host'):
                    return self.reply(403, {'error': 'origin'})
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if self.path == '/api/micro-plus/audition':
                    doc, slot = request['preset'], request.get('slot')
                    agent.auditions.append((doc['info']['displayName'], slot))
                    agent.state.update(preset=slot, name=doc['info']['displayName'],
                                       audition={'name': doc['info']['displayName'], 'slot': slot})
                    return self.reply(200, agent.state)
                slot = request['preset']
                agent.posts.append(slot)
                agent.state['audition'] = None
                time.sleep(agent.switch_delay)
                if agent.fail:
                    agent.fail -= 1
                    return self.reply(503, {'error': 'The amp did not confirm the selected preset.'})
                agent.state.update(preset=slot, name='P{}'.format(slot))
                self.reply(200, agent.state)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.url = 'http://127.0.0.1:{}'.format(self.server.server_address[1])
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def wait_until(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class Client(unittest.TestCase):

    def setUp(self):
        self.agent = FakeAgent()
        self.client = AmpClient(self.agent.url, poll=0.1, timeout=5).start()

    def tearDown(self):
        self.client.stop()
        self.agent.close()

    def test_switch_with_own_origin(self):
        self.client.request(7)
        self.assertTrue(wait_until(lambda: self.client.snapshot()['state'].get('preset') == 7))
        self.assertEqual(self.agent.origins, [self.agent.url])
        self.assertEqual(self.client.names()[6], 'P7')
        self.assertEqual(describe(self.client.snapshot()), 'amp 7 P7')

    def test_only_newest_request_is_sent(self):
        self.agent.switch_delay = 0.3
        self.client.request(2)
        self.assertTrue(wait_until(lambda: self.agent.posts == [2]))
        for slot in (3, 4, 5):          # arrive while 2 is still switching
            self.client.request(slot)
        self.assertTrue(wait_until(lambda: self.client.snapshot()['state'].get('preset') == 5))
        self.assertEqual(self.agent.posts, [2, 5])

    def test_retry_after_failure(self):
        self.agent.fail = 2
        self.client.request(9)
        self.assertTrue(wait_until(lambda: self.client.snapshot()['state'].get('preset') == 9))
        self.assertEqual(self.agent.posts, [9, 9, 9])
        self.assertIsNone(self.client.snapshot()['error'])

    def test_already_there_is_not_sent(self):
        self.assertTrue(wait_until(lambda: self.client.snapshot()['state'].get('preset') == 1))
        self.client.request(1)
        time.sleep(0.3)
        self.assertEqual(self.agent.posts, [])

    def test_superseded_request_not_sent(self):
        '''A worker that picked slot 3 must not send it once a newer request arrived.'''
        idle = AmpClient(self.agent.url)        # not started: drive _send by hand
        idle.request(3)
        picked = idle.target                     # what the worker took
        idle.request(1)                          # arrives before the worker sends 3
        idle._send(picked)
        self.assertEqual(self.agent.posts, [])
        self.assertEqual(idle.snapshot()['pending'], 1)

    def test_invalid_slot(self):
        for bad in (0, 101, '3', None):
            with self.assertRaises(ValueError):
                self.client.request(bad)

    def test_bad_url(self):
        with self.assertRaises(ValueError):
            AmpClient('localhost:8081')


class ClientWithoutAgent(unittest.TestCase):

    def test_agent_not_running(self):
        agent = FakeAgent()
        url = agent.url
        agent.close()                   # port now refuses connections
        client = AmpClient(url, poll=0.05, timeout=2).start()
        try:
            self.assertTrue(wait_until(lambda: client.snapshot()['error'] == 'amp agent not running'))
            self.assertEqual(describe(client.snapshot()), 'amp: amp agent not running')
            client.request(3)
            self.assertTrue(wait_until(lambda: client.snapshot()['pending'] is None, timeout=5))
        finally:
            client.stop()


class Control(unittest.TestCase):

    def runner(self, sinks, active):
        calls = []

        def run(args):
            calls.append(args)
            if args[0] == 'pactl':
                return SimpleNamespace(returncode=0, stdout=json.dumps(
                    [{'description': d} for d in sinks()]))
            if 'is-active' in args:
                return SimpleNamespace(returncode=0 if active() else 3, stdout='')
            return SimpleNamespace(returncode=0, stdout='')
        return run, calls

    def test_start_and_stop_with_sink(self):
        state = {'sinks': ['Speaker'], 'active': False}
        run, calls = self.runner(lambda: state['sinks'], lambda: state['active'])
        control = AgentControl('agent.service', runner=run)
        control.check()
        self.assertNotIn(['systemctl', '--user', 'start', 'agent.service'], calls)
        state['sinks'].append('Mustang Micro Plus')
        control.check()
        self.assertIn(['systemctl', '--user', 'start', 'agent.service'], calls)
        state['active'] = True
        state['sinks'] = ['Speaker']
        control.check()
        self.assertIn(['systemctl', '--user', 'stop', 'agent.service'], calls)

    def test_sink_text_fallback(self):
        def run(args):
            if args == ['pactl', '-f', 'json', 'list', 'sinks']:
                return SimpleNamespace(returncode=0, stdout='[{"description": "Spea')  # broken
            return SimpleNamespace(returncode=0, stdout='Sink #5\n\tDescription: Mustang Micro Plus\n')
        self.assertTrue(AgentControl('agent.service', runner=run).sink_present())

    def test_foreign_agent_left_alone(self):
        run, calls = self.runner(lambda: [], lambda: True)
        control = AgentControl('agent.service', runner=run)
        control.check()
        control.shutdown()
        self.assertFalse(any('stop' in c or 'start' in c for c in calls))


if __name__ == '__main__':
    unittest.main()
