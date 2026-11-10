'''
Tests for the web app (web/app.py) and the database migration (web/migrate.py).

Run from the repo root:  <venv with web/requirements.txt>/bin/python -m unittest discover -s tests
Skipped when Flask is not installed (the Tk app's venv does not need it).
'''

import json
import os
import sqlite3
import tempfile
import threading
import unittest

try:
    import flask  # noqa: F401
except ImportError:
    flask = None

import ChordsPlay.db


def make_db(path, user='jo', plays=(), library=None):
    '''A ChordsPlay DB with two songs with chords, one without, and audio files in library.'''
    db = ChordsPlay.db.Db(path, user)
    if library:
        db.addSongLists([(library,)])
        for name in ('a.mp3', 'b.mp3', 'c.mp3'):
            with open(os.path.join(library, name), 'wb') as f:
                f.write(b'ID3' + bytes(2000))
    conn = db.conn
    lib_id = conn.execute("SELECT id FROM song_lists WHERE name = ?", (library or '',)).fetchone()[0]
    for i, (title, artist, text) in enumerate([
            ('Alpha', 'Band', 'Am   C\nwords of alpha\n' + 'a long comment ' * 10),
            ('Beta', 'Band', 'G  D\nwords of beta'),
            ('Gamma', 'Other', None)]):
        conn.execute("INSERT INTO songs (title, artist, album, lyrics_chords) VALUES (?, ?, 'LP', ?)",
                     (title, artist, text))
        sid = conn.execute("SELECT id FROM songs WHERE title = ?", (title,)).fetchone()[0]
        conn.execute("INSERT INTO list_songs (song_lists_id, filename, song_id) VALUES (?, ?, ?)",
                     (lib_id, 'abc'[i] + '.mp3', sid))
    for title, when in plays:
        sid = conn.execute("SELECT id FROM songs WHERE title = ?", (title,)).fetchone()[0]
        conn.execute("INSERT INTO user_songs (user_id, song_id, played) "
                     "SELECT id, ?, ? FROM users WHERE name = ?", (sid, when, user))
    conn.commit()
    return db


@unittest.skipIf(flask is None, 'flask not installed')
class Api(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, 'web.db')
        make_db(self.path, library=self.tmp.name).close()
        import web.app
        self.app = web.app
        self.app.DB_PATH = self.path
        self.app.DEFAULT_USER = 'jo'
        self.app._local.__dict__.clear()
        self.app.hub = self.app.Hub()
        self.client = web.app.app.test_client()

    def tearDown(self):
        for db in getattr(self.app._local, 'dbs', {}).values():
            db.close()
        self.app._local.__dict__.clear()
        self.tmp.cleanup()

    def ids(self, rows):
        return [r['title'] for r in rows]

    def test_state_has_songs_with_chords(self):
        r = self.client.get('/api/state').get_json()
        self.assertEqual(r['user'], 'jo')
        self.assertEqual(self.ids(r['playlist']), ['Alpha', 'Beta'])

    def test_filter_preview_apply_and_errors(self):
        r = self.client.get('/api/filter', query_string={'Titles': 'Bet'}).get_json()
        self.assertEqual(self.ids(r['playlist']), ['Beta'])
        r = self.client.get('/api/filter', query_string={'Artists': '('})
        self.assertEqual(r.status_code, 400)
        self.assertIn('Artists', r.get_json()['errors'])
        self.client.post('/api/filter', json={'Titles': 'Bet'})
        state = self.client.get('/api/state').get_json()
        self.assertEqual(self.ids(state['playlist']), ['Beta'])
        self.assertEqual(state['filter']['Titles'], 'Bet')

    def test_order_and_named_filters(self):
        rows = self.client.get('/api/state').get_json()['playlist']
        self.client.post('/api/playlist/order', json={'ids': [rows[1]['id'], rows[0]['id']]})
        self.assertEqual(self.ids(self.client.get('/api/state').get_json()['playlist']), ['Beta', 'Alpha'])
        self.client.put('/api/filters/band', json={'Artists': 'Band'})
        self.assertEqual(self.client.get('/api/filters').get_json()['names'], ['band'])
        self.assertEqual(self.client.get('/api/filters/band').get_json()['filter']['Artists'], 'Band')

    def test_song_text_wrapped_and_audio_ranges(self):
        sid = self.client.get('/api/state').get_json()['playlist'][0]['id']
        song = self.client.get('/api/song/{}'.format(sid)).get_json()
        self.assertEqual(song['title'], 'Alpha')
        self.assertGreater(max(map(len, song['text'].splitlines())), 100)
        self.assertLessEqual(max(map(len, song['wrapped'].splitlines())), 30)
        r = self.client.get('/api/audio/{}'.format(sid), headers={'Range': 'bytes=0-99'})
        self.assertEqual(r.status_code, 206)
        self.assertEqual(len(r.data), 100)
        self.assertEqual(r.mimetype, 'audio/mpeg')
        r.close()

    def test_played_tags_and_now(self):
        sid = self.client.get('/api/state').get_json()['playlist'][0]['id']
        self.assertEqual(self.client.post('/api/played/{}'.format(sid)).get_json()['played'], 1)
        tags = self.client.post('/api/song/{}/tags'.format(sid), json={'id': 1, 'on': True}).get_json()['tags']
        self.assertTrue([t for t in tags if t['id'] == 1][0]['on'])
        self.assertEqual(self.client.get('/api/song/{}'.format(sid)).get_json()['tags'], 'f')
        self.client.put('/api/now', json={'id': sid, 'pos': 0})
        self.assertEqual(self.client.get('/api/state').get_json()['now']['id'], sid)

    def test_proxy_user(self):
        self.app.USER_MAP['user'] = 'jo'
        r = self.client.get('/api/state', headers={'X-Remote-User': 'user'}).get_json()
        self.assertEqual(r['user'], 'jo')
        r = self.client.get('/api/state', headers={'X-Remote-User': 'caro'}).get_json()
        self.assertEqual(r['user'], 'caro')
        self.app.USER_MAP.clear()
        # over the proxy only with a login
        self.assertEqual(self.client.get('/api/state', headers={'X-ChordsPlay-Proxy': '1'}).status_code, 401)
        r = self.client.get('/api/state', headers={'X-ChordsPlay-Proxy': '1', 'X-Remote-User': 'jo'})
        self.assertEqual(r.status_code, 200)


@unittest.skipIf(flask is None, 'flask not installed')
class Display(Api):
    '''A display follows the controller of the user whose code it has.'''

    def first_event(self, response):
        chunks = iter(response.response)
        self.assertEqual(next(chunks), b'retry: 3000\n\n')
        data = next(chunks)
        response.close()
        return json.loads(data[len(b'data: '):])

    def test_code_state_and_events(self):
        sid = self.client.get('/api/state').get_json()['playlist'][1]['id']
        code = self.client.post('/api/display', headers={'X-ChordsPlay-Proxy': '1', 'X-Remote-User': 'jo'}).get_json()['code']
        self.assertEqual(len(code), 10)
        # nothing played yet
        self.assertIsNone(self.client.get('/d/{}/state'.format(code)).get_json()['song'])
        self.client.put('/api/now', json={'id': sid, 'pos': 1, 't': 12.5, 'playing': True})
        r = self.client.get('/d/{}/state'.format(code), headers={'X-ChordsPlay-Proxy': '1'}).get_json()
        self.assertEqual(r['song']['title'], 'Beta')
        self.assertNotIn('played', r['song'])          # text only
        self.assertTrue(r['state']['playing'])
        self.assertEqual(r['state']['t'], 12.5)
        event = self.first_event(self.client.get('/d/{}/events'.format(code)))
        self.assertEqual(event['id'], sid)
        self.assertEqual(self.client.get('/d/{}/'.format(code)).status_code, 200)

    def test_unknown_and_ended_codes(self):
        self.assertEqual(self.client.get('/d/nonsense/state').status_code, 404)
        code = self.client.post('/api/display').get_json()['code']
        self.assertEqual(self.client.get('/d/{}/state'.format(code)).status_code, 200)
        self.client.delete('/api/display')
        self.assertEqual(self.client.get('/d/{}/state'.format(code)).status_code, 404)

    def test_display_cannot_reach_controller_api(self):
        # the display bypasses the login only below /d/
        self.assertEqual(self.client.get('/api/state', headers={'X-ChordsPlay-Proxy': '1'}).status_code, 401)
        self.assertEqual(self.client.get('/d/static/app.js').status_code, 404)

    def test_qr(self):
        r = self.client.get('/api/qr.svg', query_string={'text': 'https://example.org/d/abc/'})
        self.assertEqual(r.mimetype, 'image/svg+xml')
        self.assertIn(b'<svg', r.data)

    def test_hub_wakes_waiters(self):
        hub = self.app.Hub()
        hub.publish('jo', {'id': 1})
        rev = hub.get('jo')['rev']
        threading.Timer(0.05, hub.publish, ('jo', {'id': 2})).start()
        self.assertEqual(hub.wait('jo', {'now': rev}, 2)['now']['id'], 2)
        self.assertEqual(hub.wait('jo', {'now': hub.get('jo')['rev']}, 0.05, hub.ended_count('jo')), {})  # timeout
        # another channel wakes only its own waiters
        threading.Timer(0.05, hub.publish, ('jo', {'preset': 3}, 'amp')).start()
        changed = hub.wait('jo', {'now': hub.get('jo')['rev'], 'amp': None}, 2)
        self.assertEqual(list(changed), ['amp'])

    def test_display_gets_no_amp_events(self):
        code = self.client.post('/api/display').get_json()['code']
        self.app.hub.publish('jo', {'state': {'preset': 3}, 'at': 0}, 'amp')
        response = self.client.get('/d/{}/events'.format(code))
        chunks = iter(response.response)
        next(chunks)
        self.assertNotIn(b'event: amp', next(chunks))
        response.close()


class FakeAgentClient(object):
    '''ChordsPlay.amp.AmpClient without an agent: records what it is asked to do.'''

    def __init__(self, names=None, preset=1):
        self.requests = []
        self.state = {'connected': True, 'preset': preset, 'name': 'Driven Deluxe',
                      'presets': [{'preset': i + 1, 'name': n} for i, n in enumerate(names or [])]}
        self.base = {'audioGraph': {'nodes': [{'nodeId': 'amp', 'dspUnitParameters': {'volume': -5.0}}]}}

    def request(self, slot):
        self.requests.append(('slot', slot))

    def audition(self, doc):
        self.requests.append(('tone', doc['info']['displayName'], doc['audioGraph']['nodes'][0]
                              ['dspUnitParameters']['volume']))

    def preset(self, slot):
        return self.base

    def names(self):
        return [p['name'] for p in self.state['presets']]

    def snapshot(self):
        return {'state': dict(self.state), 'error': None, 'pending': None, 'busy': None, 'tone': None}


@unittest.skipIf(flask is None, 'flask not installed')
class Amp(Api):
    '''Cues per song in the central DB, and the amp bridge that plays them.'''

    LOGIN = {'X-ChordsPlay-Proxy': '1', 'X-Remote-User': 'jo'}

    def song_ids(self):
        return [r['id'] for r in self.client.get('/api/state').get_json()['playlist']]

    def bridge(self):
        '''A bridge token, and an opener that sends the bridge's requests to the test client.'''
        path = self.client.post('/api/bridge').get_json()['path']
        client = self.client

        class Reply(object):
            def __init__(self, response):
                self.response = response
                if response.status_code >= 400:
                    import urllib.error
                    raise urllib.error.HTTPError(response.request.url, response.status_code, 'x', {}, None)

            def read(self):
                return self.response.data

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self.response.close()

        def opener(request, timeout=None):
            if isinstance(request, str):
                return Reply(client.get(request.replace('http://localhost', '')))
            return Reply(client.open(request.full_url.replace('http://localhost', ''), method=request.get_method(),
                                     data=request.data, headers=dict(request.header_items())))
        return '/' + path, opener

    def test_cues_put_delete_and_artist(self):
        alpha = self.song_ids()[0]
        r = self.client.put('/api/song/{}/cues/0'.format(alpha), json={'slot': 7, 'name': 'Twin Trem'}).get_json()
        self.assertEqual(r['cues'], [{'start': 0, 'slot': 7, 'name': 'Twin Trem', 'tone_id': None, 'tone': None,
                                      'level': None, 'params': None}])
        self.assertEqual(self.client.put('/api/song/{}/cues/0'.format(alpha), json={'slot': 101}).status_code, 400)
        self.client.put('/api/artist-preset', json={'artist': 'Band', 'slot': 19})
        r = self.client.get('/api/song/{}/cues'.format(alpha)).get_json()
        self.assertEqual(r['artist_preset']['slot'], 19)
        r = self.client.delete('/api/song/{}/cues/0'.format(alpha)).get_json()
        self.assertEqual(r['cues'], [])
        # every edit tells the bridge to reload
        self.assertIsNotNone(self.app.hub.get('jo', 'plan'))

    def test_learn_needs_a_bridge_and_a_connected_amp(self):
        alpha = self.song_ids()[0]
        r = self.client.post('/api/amp/learn', json={'id': alpha, 't': 65.7})
        self.assertEqual(r.status_code, 409)
        bridge, _ = self.bridge()
        self.client.put(bridge + 'amp', json={'state': {'connected': True, 'preset': 12, 'name': 'Hey Jimi'}})
        r = self.client.post('/api/amp/learn', json={'id': alpha, 't': 65.7}).get_json()
        self.assertEqual((r['start'], r['slot']), (65500, 12))     # rounded down to 0.5 s
        r = self.client.post('/api/amp/learn', json={'id': alpha, 't': 2.0}).get_json()
        self.assertEqual(r['start'], 0)                             # early: the start preset
        cues = self.client.get('/api/song/{}/cues'.format(alpha)).get_json()['cues']
        self.assertEqual([(c['start'], c['slot'], c['name']) for c in cues], [(0, 12, 'Hey Jimi'), (65500, 12, 'Hey Jimi')])
        self.client.put(bridge + 'amp', json={'state': {'connected': True, 'preset': 100, 'audition': {'name': 'x'}}})
        self.assertEqual(self.client.post('/api/amp/learn', json={'id': alpha, 't': 9}).status_code, 409)

    def test_bridge_token(self):
        bridge, _ = self.bridge()
        self.assertEqual(self.client.get(bridge.replace('/d/bridge/', '/d/bridge/x') + 'song/1').status_code, 404)
        # a new token ends the old one
        newer, _ = self.bridge()
        self.assertEqual(self.client.get(bridge + 'song/{}'.format(self.song_ids()[0])).status_code, 404)
        self.assertEqual(self.client.get(newer + 'song/{}'.format(self.song_ids()[0])).status_code, 200)
        # below /d/ without a login, but the token is needed
        self.assertEqual(self.client.put(newer + 'amp', json={'state': {}}, headers={'X-ChordsPlay-Proxy': '1'}).status_code, 200)
        self.assertEqual(self.client.post('/api/bridge', headers={'X-ChordsPlay-Proxy': '1'}).status_code, 401)

    def test_bridge_plays_the_cues(self):
        from web import bridge as bridgemod
        alpha, beta = self.song_ids()
        self.client.put('/api/song/{}/cues/0'.format(alpha), json={'slot': 7})
        self.client.put('/api/song/{}/cues/30000'.format(alpha), json={'slot': 19})
        self.client.put('/api/artist-preset', json={'artist': 'Band', 'slot': 41})
        path, opener = self.bridge()
        agent = FakeAgentClient(names=['n{}'.format(i) for i in range(1, 101)])
        b = bridgemod.Bridge('http://localhost' + path, agent, lead_ms=300, opener=opener)
        # the controller plays Alpha at 29.8 s: within the lead of the cue at 30 s
        b.session.update('now', {'id': alpha, 't': 29.8, 'playing': True, 'at': 100.0, 'server_time': 100.0})
        b.step()
        self.assertEqual(agent.requests, [('slot', 19)])
        # Beta has no cue: the artist preset
        b.session.update('now', {'id': beta, 't': 1, 'playing': True, 'at': 1, 'server_time': 1})
        b.step()
        self.assertEqual(agent.requests[-1], ('slot', 41))
        # the bridge reported the amp's state
        self.assertEqual(self.client.get('/api/amp').get_json()['amp']['state']['preset'], 1)
        self.assertTrue(self.client.get('/api/amp').get_json()['amp']['online'])
        # an edited cue is played after the plan event
        self.client.put('/api/song/{}/cues/0'.format(beta), json={'slot': 5})
        b.session.update('plan', self.app.hub.get('jo', 'plan'))
        b.step()
        self.assertEqual(agent.requests[-1], ('slot', 5))
        # try now: a slot, and a tone at its level
        self.client.post('/api/amp/try', json={'slot': 33})
        b.session.update('cmd', self.app.hub.get('jo', 'cmd'))
        b.step()
        self.assertEqual(agent.requests[-1], ('slot', 33))
        db = self.app.get_db('jo')
        tone = db.saveTone('Soft', 7, 'n7', -3.0, {})
        self.client.post('/api/amp/try', json={'tone_id': tone})
        b.session.update('cmd', self.app.hub.get('jo', 'cmd'))
        b.step()
        self.assertEqual(agent.requests[-1], ('tone', 'Soft', -8.0))
        # an old command (e.g. from before the bridge connected) is ignored
        b.session.update('cmd', {'slot': 2, 'at': 0})
        b.step()
        self.assertEqual(agent.requests[-1], ('tone', 'Soft', -8.0))

    def test_try_wins_over_a_cue_edit_in_the_same_tick(self):
        from web import bridge as bridgemod
        alpha = self.song_ids()[0]
        self.client.put('/api/song/{}/cues/0'.format(alpha), json={'slot': 7})
        path, opener = self.bridge()
        agent = FakeAgentClient()
        b = bridgemod.Bridge('http://localhost' + path, agent, opener=opener)
        b.session.update('now', {'id': alpha, 't': 5, 'playing': True, 'at': 1, 'server_time': 1})
        b.step()
        # key G stores a cue (the bridge reloads and re-applies), then "try now" right after
        self.client.put('/api/song/{}/cues/0'.format(alpha), json={'slot': 8})
        self.client.post('/api/amp/try', json={'slot': 33})
        b.session.update('plan', self.app.hub.get('jo', 'plan'))
        b.session.update('cmd', self.app.hub.get('jo', 'cmd'))
        b.step()
        self.assertEqual(agent.requests, [('slot', 7), ('slot', 8), ('slot', 33)])

    def test_levels_play_level_matched(self):
        '''A cue, an artist preset or "try now" with a level plays the slot by audition,
        with the slot's measured correction plus the level; without one, the slot as stored.'''
        from web import bridge as bridgemod
        alpha, beta = self.song_ids()
        db = self.app.get_db('jo')
        db.importPresetLevels({'method': 'played', 'presets': [{'slot': 7, 'correction_db': 5.5},
                                                                 {'slot': 41, 'correction_db': -0.5}]})
        self.client.put('/api/song/{}/cues/0'.format(alpha), json={'slot': 7, 'level': 3})
        self.client.put('/api/song/{}/cues/30000'.format(alpha), json={'slot': 19})        # as stored
        self.client.put('/api/artist-preset', json={'artist': 'Band', 'slot': 41, 'level': -6})
        cues = self.client.get('/api/song/{}/cues'.format(alpha)).get_json()
        self.assertEqual([(c['slot'], c['level']) for c in cues['cues']], [(7, 3.0), (19, None)])
        self.assertEqual((cues['artist_preset']['slot'], cues['artist_preset']['level']), (41, -6.0))
        self.assertEqual(sorted(cues['measured']), [7, 41])
        path, opener = self.bridge()
        agent = FakeAgentClient()       # its stored presets have the amp at -5 dB
        b = bridgemod.Bridge('http://localhost' + path, agent, opener=opener)
        b.session.update('now', {'id': alpha, 't': 1, 'playing': True, 'at': 1, 'server_time': 1})
        b.step()
        self.assertEqual(agent.requests[-1], ('tone', 'slot 7 +3 dB', -5 + 5.5 + 3))
        b.session.update('now', {'id': alpha, 't': 31, 'playing': True, 'at': 1, 'server_time': 1})
        b.step()
        self.assertEqual(agent.requests[-1], ('slot', 19))
        # Beta has no cue: the artist preset at its level, level-matched
        b.session.update('now', {'id': beta, 't': 1, 'playing': True, 'at': 1, 'server_time': 1})
        b.step()
        self.assertEqual(agent.requests[-1], ('tone', 'slot 41 -6 dB', -5 - 0.5 - 6))
        # try now: matched (0) and as stored
        self.client.post('/api/amp/try', json={'slot': 7, 'level': 0})
        b.session.update('cmd', self.app.hub.get('jo', 'cmd')); b.step()
        self.assertEqual(agent.requests[-1], ('tone', 'slot 7 matched', -5 + 5.5))
        self.client.post('/api/amp/try', json={'slot': 7})
        b.session.update('cmd', self.app.hub.get('jo', 'cmd')); b.step()
        self.assertEqual(agent.requests[-1], ('slot', 7))
        self.assertEqual(self.client.put('/api/song/{}/cues/0'.format(alpha), json={'slot': 7, 'level': 40}).status_code, 400)

    def test_knobs_fetched_through_the_bridge(self):
        from web import bridge as bridgemod
        path, opener = self.bridge()
        agent = FakeAgentClient()
        agent.base = {'info': {'displayName': 'Twin'}, 'audioGraph': {'nodes': [
            {'nodeId': 'amp', 'FenderId': 'ACD_Twin', 'dspUnitParameters': {'volume': -5.0, 'gain': 0.4, 'bass': 0.5}},
            {'nodeId': 'reverb', 'FenderId': 'ACD_Spring', 'dspUnitParameters': {'bypass': False, 'wetlvl': 0.3, 'decay': 0.6}},
            {'nodeId': 'delay', 'FenderId': 'DUBS_Unknown', 'dspUnitParameters': {}}]}}
        b = bridgemod.Bridge('http://localhost' + path, agent, opener=opener)
        r = self.client.get('/api/amp/preset/7')
        self.assertEqual((r.status_code, r.get_json()['pending']), (202, True))
        b.session.update('cmd', self.app.hub.get('jo', 'cmd'))
        b.step()                                    # the bridge reads slot 7 and sends it
        r = self.client.get('/api/amp/preset/7').get_json()
        self.assertEqual([k['key'] for k in r['knobs']],
                         ['amp.gain', 'amp.bass', 'reverb.bypass', 'reverb.wetlvl', 'reverb.decay'])   # empty block: none
        # the amp's name for slot 7 changed: read again
        self.client.put(path + 'amp', json={'state': {'connected': True, 'preset': 1,
                                                     'presets': [{'preset': i, 'name': 'Other' if i == 7 else ''}
                                                                 for i in range(1, 101)]}})
        self.assertEqual(self.client.get('/api/amp/preset/7').status_code, 202)

    def test_original_with_knobs_keeps_the_presets_volume(self):
        from web import bridge as bridgemod
        alpha = self.song_ids()[0]
        self.app.get_db('jo').importPresetLevels({'method': 'played', 'presets': [{'slot': 7, 'correction_db': 5.5}]})
        self.client.put('/api/song/{}/cues/0'.format(alpha), json={'slot': 7, 'params': {'reverb.wetlvl': 0.8}})
        self.client.put('/api/song/{}/cues/20000'.format(alpha), json={'slot': 7, 'level': 3, 'params': {'reverb.wetlvl': 0.8}})
        path, opener = self.bridge()
        agent = FakeAgentClient()
        b = bridgemod.Bridge('http://localhost' + path, agent, opener=opener)
        b.session.update('now', {'id': alpha, 't': 1, 'playing': True, 'at': 1, 'server_time': 1}); b.step()
        self.assertEqual(agent.requests[-1], ('tone', 'slot 7 tuned', -5.0))              # own volume, knobs changed
        b.session.update('now', {'id': alpha, 't': 21, 'playing': True, 'at': 1, 'server_time': 1}); b.step()
        self.assertEqual(agent.requests[-1], ('tone', 'slot 7 +3 dB tuned', -5 + 5.5 + 3))
        # live while fiddling: try now with knobs
        self.client.post('/api/amp/try', json={'slot': 7, 'level': 0, 'params': {'amp.gain': 0.9}})
        b.session.update('cmd', self.app.hub.get('jo', 'cmd')); b.step()
        self.assertEqual(agent.requests[-1], ('tone', 'slot 7 matched tuned', -5 + 5.5))

    def test_take_recorded_uploaded_and_played(self):
        from web import bridge as bridgemod
        alpha = self.song_ids()[0]
        self.app.TAKES_DIR = os.path.join(self.tmp.name, 'takes')
        self.assertEqual(self.client.post('/api/takes', json={'song_id': alpha, 't': 12}).status_code, 409)   # no bridge
        path, opener = self.bridge()
        b = bridgemod.Bridge('http://localhost' + path, FakeAgentClient(), opener=opener)
        b.report()                                      # the bridge is there
        started = []

        class Proc(object):
            def send_signal(self, sig): pass
            def wait(self, timeout=None): return 0

        def start(source, wav):
            started.append(source)
            open(wav, 'wb').write(b'RIFF')
            return Proc()

        done = threading.Event()

        def encode(wav, out):
            open(out, 'wb').write(b'OggS take')
        upload = b.recorder.upload
        b.recorder = bridgemod.Recorder(lambda *a: (upload(*a), done.set()), find_source=lambda: 'amp-usb',
                                        start=start, encode=encode)
        take = self.client.post('/api/takes', json={'song_id': alpha, 't': 12}).get_json()['id']
        b.session.update('cmd', self.app.hub.get('jo', 'cmd')); b.step()
        self.assertEqual(started, ['amp-usb'])
        self.assertEqual(self.app.hub.get('jo', 'amp')['recording']['take'], take)
        self.client.post('/api/takes/{}/stop'.format(take))
        b.session.update('cmd', self.app.hub.get('jo', 'cmd')); b.step()
        self.assertTrue(done.wait(5))
        takes = self.client.get('/api/song/{}/takes'.format(alpha)).get_json()['takes']
        self.assertEqual([(t['id'], t['state'], t['song_t']) for t in takes], [(take, 'ready', 12.0)])
        r = self.client.get('/api/takes/{}'.format(take))
        self.assertEqual((r.mimetype, r.data), ('audio/ogg', b'OggS take'))
        r.close()
        self.client.delete('/api/takes/{}'.format(take))
        self.assertEqual(self.client.get('/api/song/{}/takes'.format(alpha)).get_json()['takes'], [])
        self.assertEqual(os.listdir(self.app.TAKES_DIR), [])

    def test_take_without_amp_usb(self):
        from web import bridge as bridgemod
        rec = bridgemod.Recorder(lambda *a: None, find_source=lambda: None)
        self.assertFalse(rec.start(1))
        self.assertIn('USB', rec.error)
        self.assertIsNone(rec.state())

    def test_paused_controller_switches_nothing(self):
        from web import bridge as bridgemod
        alpha = self.song_ids()[0]
        self.client.put('/api/song/{}/cues/0'.format(alpha), json={'slot': 7})
        path, opener = self.bridge()
        agent = FakeAgentClient()
        b = bridgemod.Bridge('http://localhost' + path, agent, opener=opener)
        b.session.update('now', {'id': alpha, 't': 0, 'playing': False, 'at': 1, 'server_time': 1})
        b.step()
        self.assertEqual(agent.requests, [])

    def test_event_stream_parsing(self):
        from web import bridge as bridgemod
        seen = []
        bridgemod.read_events([b'retry: 3000\n', b'\n', b'data: {"id": 1}\n', b'\n', b': keepalive\n', b'\n',
                               b'event: plan\n', b'data: {"rev": 4}\n', b'\n'], lambda c, d: seen.append((c, d)))
        self.assertEqual(seen, [('now', {'id': 1}), ('plan', {'rev': 4})])

    def test_position_runs_on_while_playing(self):
        from web import bridge as bridgemod
        session = bridgemod.Session()
        session.update('now', {'id': 3, 't': 10.0, 'playing': True, 'at': 50.0, 'server_time': 51.5})
        song_id, ms, playing = session.position()
        self.assertEqual(song_id, 3)
        self.assertGreaterEqual(ms, 11500)      # 1.5 s on its way from the controller
        session.update('now', {'id': 3, 't': 10.0, 'playing': False, 'at': 50.0, 'server_time': 60})
        self.assertEqual(session.position()[1], 10000)


@unittest.skipIf(flask is None, 'flask not installed')
class Pdfs(Api):
    '''PDF tabs: found in folders, linked to songs by name or by hand, shown instead of the text.'''

    def setUp(self):
        super().setUp()
        self.pdfdir = os.path.join(self.tmp.name, 'pdf')
        os.makedirs(os.path.join(self.pdfdir, '.@__thumb'))
        for name in ('Band - Alpha.pdf', 'Band - Alpha - Solo.pdf', 'Band - Beta Fingerpicking.pdf',
                     'HellsBells1.pdf', '.@__thumb/Band - Alpha.pdf'):
            with open(os.path.join(self.pdfdir, name), 'wb') as f:
                f.write(b'%PDF-1.4 ' + name.encode())
        self.app.PDF_DIRS = [self.pdfdir]
        self.alpha, self.beta = [r['id'] for r in self.client.get('/api/state').get_json()['playlist']]

    def pdfs(self):
        return {p['name']: p for p in self.client.get('/api/pdfs').get_json()['pdfs']}

    def test_names_link_songs(self):
        found = self.pdfs()
        self.assertEqual(sorted(found), ['Band - Alpha - Solo.pdf', 'Band - Alpha.pdf', 'Band - Beta Fingerpicking.pdf',
                                         'HellsBells1.pdf'])        # hidden folders are skipped
        self.assertEqual(found['Band - Alpha.pdf']['song']['id'], self.alpha)
        self.assertEqual(found['Band - Alpha - Solo.pdf']['part'], 'Solo')
        self.assertEqual((found['Band - Beta Fingerpicking.pdf']['song']['id'], found['Band - Beta Fingerpicking.pdf']['part']),
                         (self.beta, 'Fingerpicking'))
        self.assertIsNone(found['HellsBells1.pdf']['song'])
        song = self.client.get('/api/song/{}'.format(self.alpha)).get_json()
        self.assertEqual([p['part'] for p in song['pdfs']], [None, 'Solo'])
        self.assertIsNone(song['view'])

    def test_assign_by_hand_survives_a_scan(self):
        bells = self.pdfs()['HellsBells1.pdf']['id']
        self.assertEqual(self.client.put('/api/pdfs/{}'.format(bells), json={'song_id': self.beta, 'part': 'Riff'}).status_code, 200)
        self.assertEqual(self.pdfs()['HellsBells1.pdf']['song']['id'], self.beta)
        # "no song" also sticks, even for a name that matches
        alpha_pdf = self.pdfs()['Band - Alpha.pdf']['id']
        self.client.put('/api/pdfs/{}'.format(alpha_pdf), json={'song_id': None})
        self.assertIsNone(self.pdfs()['Band - Alpha.pdf']['song'])
        self.assertEqual(self.client.put('/api/pdfs/{}'.format(alpha_pdf), json={'song_id': 99999}).status_code, 400)

    def test_view_file_and_display(self):
        found = self.pdfs()
        main, solo = found['Band - Alpha.pdf']['id'], found['Band - Alpha - Solo.pdf']['id']
        self.assertEqual(self.client.put('/api/song/{}/view'.format(self.alpha), json={'pdf': main}).get_json()['view'], main)
        self.assertEqual(self.client.get('/api/song/{}'.format(self.alpha)).get_json()['view'], main)
        self.assertEqual(self.client.put('/api/song/{}/view'.format(self.beta), json={'pdf': main}).status_code, 400)
        r = self.client.get('/api/pdf/{}'.format(main))
        self.assertEqual((r.mimetype, r.data), ('application/pdf', b'%PDF-1.4 Band - Alpha.pdf'))
        r.close()
        # a display gets the PDFs of the song the controller is on, and the page
        code = self.client.post('/api/display').get_json()['code']
        self.client.put('/api/now', json={'id': self.alpha, 'pdf': solo, 'page': 3})
        state = self.client.get('/d/{}/state'.format(code)).get_json()
        self.assertEqual((state['state']['pdf'], state['state']['page']), (solo, 3))
        self.assertEqual(len(state['song']['pdfs']), 2)
        r = self.client.get('/d/{}/pdf/{}'.format(code, solo))
        self.assertEqual(r.status_code, 200)
        r.close()
        beta_pdf = found['Band - Beta Fingerpicking.pdf']['id']
        self.assertEqual(self.client.get('/d/{}/pdf/{}'.format(code, beta_pdf)).status_code, 404)
        self.assertEqual(self.client.get('/d/static/vendor/pdfjs/pdf.min.js').status_code, 200)
        self.assertEqual(self.client.get('/d/static/vendor/pdfjs/../app.js').status_code, 404)
        # back to the text
        self.client.put('/api/song/{}/view'.format(self.alpha), json={'pdf': None})
        self.assertIsNone(self.client.get('/api/song/{}'.format(self.alpha)).get_json()['view'])

    def test_removed_file_is_forgotten(self):
        self.pdfs()
        os.remove(os.path.join(self.pdfdir, 'Band - Alpha - Solo.pdf'))
        self.assertNotIn('Band - Alpha - Solo.pdf', self.pdfs())
        self.assertEqual(len(self.client.get('/api/song/{}'.format(self.alpha)).get_json()['pdfs']), 1)

    def test_pdf_next_to_the_song(self):
        # a.mp3 is Alpha (make_db): a.pdf and "a - Solo.pdf" beside it belong to Alpha
        for name in ('a.pdf', 'a - Solo.pdf', 'b - x.pdf'):
            with open(os.path.join(self.tmp.name, name), 'wb') as f:
                f.write(b'%PDF-1.4')
        found = self.pdfs()
        self.assertEqual((found['a.pdf']['song']['id'], found['a.pdf']['part']), (self.alpha, None))
        self.assertEqual((found['a - Solo.pdf']['song']['id'], found['a - Solo.pdf']['part']), (self.alpha, 'Solo'))
        self.assertEqual((found['b - x.pdf']['song']['id'], found['b - x.pdf']['part']), (self.beta, 'x'))
        self.assertEqual(len([n for n in found if n == 'Band - Alpha.pdf']), 1)    # found once, though in two folders

    def test_song_search(self):
        r = self.client.get('/api/songs/search', query_string={'q': 'band'}).get_json()
        self.assertEqual([x['title'] for x in r['songs']], ['Alpha', 'Beta'])     # Gamma has no chords


@unittest.skipIf(flask is None, 'flask not installed')
class Suggest(Amp):
    '''Suggested presets: a sound category per song, played only when the model knows the song.'''

    def suggest(self, answers):
        from web import suggest
        db = self.app.get_db('jo')
        asked = []

        def asker(title, artist, genres):
            asked.append((title, genres))
            return answers[title]
        n = suggest.run(db.conn, asker=asker, genres_of=lambda conn, artist: ['rock'])
        return n, asked

    def test_run_once_and_what_is_played(self):
        alpha, beta = self.song_ids()
        n, asked = self.suggest({'Alpha': ('hard_rock', True), 'Beta': ('jazz', False)})
        self.assertEqual((n, sorted(asked)), (2, [('Alpha', ['rock']), ('Beta', ['rock'])]))   # Gamma has no chords
        self.assertEqual(self.suggest({})[0], 0)                                                  # nothing new
        from web import suggest
        conn = self.app.get_db('jo').conn
        self.assertEqual(suggest.applied_slot(conn, alpha), 19)     # hard rock -> 80S Rock (draft)
        self.assertIsNone(suggest.applied_slot(conn, beta))         # a guess is only shown
        r = self.client.get('/api/song/{}/cues'.format(beta)).get_json()['suggestion']
        self.assertEqual((r['category'], r['known'], r['slot']), ('jazz', False, 41))

    def test_precedence_cue_artist_suggestion(self):
        from web import bridge as bridgemod
        alpha, beta = self.song_ids()
        self.suggest({'Alpha': ('hard_rock', True), 'Beta': ('metal', True)})
        self.assertFalse(self.client.get('/api/amp').get_json()['suggest_auto'])     # off until chosen
        self.client.put('/api/amp/suggest-auto', json={'on': True})
        path, opener = self.bridge()
        agent = FakeAgentClient()
        b = bridgemod.Bridge('http://localhost' + path, agent, opener=opener)
        play = lambda sid: (b.session.update('now', {'id': sid, 't': 1, 'playing': True, 'at': 1, 'server_time': 1}), b.step())
        play(alpha)
        self.assertEqual(agent.requests[-1], ('slot', 19))          # the suggestion
        self.client.put('/api/artist-preset', json={'artist': 'Band', 'slot': 41})
        b.session.update('plan', self.app.hub.get('jo', 'plan')); b.step()
        self.assertEqual(agent.requests[-1], ('slot', 41))          # the artist's preset wins
        self.client.put('/api/song/{}/cues/0'.format(alpha), json={'slot': 7})
        b.session.update('plan', self.app.hub.get('jo', 'plan')); b.step()
        self.assertEqual(agent.requests[-1], ('slot', 7))           # the song's own cue wins

    def test_reject_and_sound_table(self):
        alpha = self.song_ids()[0]
        self.suggest({'Alpha': ('hard_rock', True), 'Beta': ('jazz', False)})
        from web import suggest
        conn = self.app.get_db('jo').conn
        r = self.client.post('/api/song/{}/suggestion'.format(alpha), json={'rejected': True}).get_json()
        self.assertTrue(r['suggestion']['rejected'])
        self.assertIsNone(suggest.applied_slot(conn, alpha))
        self.client.post('/api/song/{}/suggestion'.format(alpha), json={'rejected': False})
        sounds = self.client.put('/api/amp/sounds/hard_rock', json={'slot': 33}).get_json()['sounds']
        self.assertIn({'category': 'hard_rock', 'slot': 33, 'level': None}, sounds)
        self.assertEqual(suggest.applied_slot(conn, alpha), 33)
        self.client.put('/api/amp/sounds/hard_rock', json={'slot': None})
        self.assertIsNone(suggest.applied_slot(conn, alpha))        # no preset for the sound: nothing switched
        self.client.put('/api/amp/sounds/hard_rock', json={'slot': 19, 'level': 3})
        self.assertEqual(suggest.applied(conn, alpha), (19, 3.0))
        self.assertEqual(self.client.put('/api/amp/sounds/polka', json={'slot': 3}).status_code, 400)
        self.assertEqual(len(self.client.get('/api/amp').get_json()['sounds']), 16)


class Migrate(unittest.TestCase):

    def test_init_and_merge_plays_once(self):
        from web import migrate
        with tempfile.TemporaryDirectory() as tmp:
            local = os.path.join(tmp, 'local.db')
            other = os.path.join(tmp, 'other.db')
            target = os.path.join(tmp, 'web', 'web.db')
            make_db(local, plays=[('Alpha', '2026-01-01 10:00:00')]).close()
            db = make_db(other, plays=[('Alpha', '2026-01-01 10:00:00'), ('Beta', '2026-02-02 10:00:00')])
            beta = db.conn.execute("SELECT id FROM songs WHERE title='Beta'").fetchone()[0]
            db.setUserTag(beta, 1)
            db.setSongPreset(beta, 0, 7, 'Twin Trem')
            db.setSongTone(beta, 30000, db.saveTone('Solo', 2, 'Stadium Lead', 3.0, {}), 100)
            db.setArtistPreset('Band', 19, '80S Rock')
            db.importPresetLevels({'method': 'played', 'presets': [{'slot': 2, 'correction_db': -1.5}]})
            db.close()
            migrate.init(target, local, user='jo')
            db = ChordsPlay.db.Db(target, 'jo')
            first = migrate.merge(db, other)
            again = migrate.merge(db, other)
            self.assertEqual(first['plays'], 1)   # Alpha's play was there already
            self.assertEqual(first['tags'], 1)
            self.assertEqual(again['plays'], 0)
            self.assertEqual(db.conn.execute("SELECT count(*) FROM user_songs").fetchone()[0], 2)
            self.assertEqual((first['cues'], first['tones'], first['artist presets'], first['levels']), (2, 1, 1, 1))
            self.assertEqual((again['cues'], again['tones']), (0, 0))
            beta = db.conn.execute("SELECT id FROM songs WHERE title='Beta'").fetchone()[0]
            cues = db.getSongPresets(beta)
            self.assertEqual(cues[0], (0, 7, 'Twin Trem', None))
            self.assertEqual(db.getTone(cues[1][3])['name'], 'Solo')    # the tone id is the target's
            self.assertEqual(db.getPresetLevel(2)[0], -1.5)
            db.close()
            with self.assertRaises(SystemExit):
                migrate.init(target, local)

    def test_locked_text_taken_over(self):
        from web import migrate
        with tempfile.TemporaryDirectory() as tmp:
            target, other = os.path.join(tmp, 'web.db'), os.path.join(tmp, 'host4.db')
            make_db(target).close()
            db = make_db(other)
            beta = db.conn.execute("SELECT id FROM songs WHERE title='Beta'").fetchone()[0]
            db.conn.execute("UPDATE songs SET lyrics_chords = ? WHERE id = ?", ('Em  C\nmy fixed beta', beta))
            db.conn.execute("INSERT INTO chords (name) VALUES ('Em')")
            db.conn.execute("INSERT INTO song_chords (song_id, chord_id) SELECT ?, id FROM chords WHERE name = 'Em'",
                            (beta,))
            db.conn.commit()
            db.setUserTag(beta, 6)          # x, Fixiert
            db.close()
            db = ChordsPlay.db.Db(target, 'jo')
            self.assertEqual(migrate.merge(db, other)['locked texts'], 1)
            sid = db.conn.execute("SELECT id FROM songs WHERE title='Beta'").fetchone()[0]
            self.assertEqual(db.conn.execute("SELECT lyrics_chords, dirty FROM songs WHERE id = ?", (sid,)).fetchone(),
                             ('Em  C\nmy fixed beta', 1))
            self.assertTrue(db.isLocked(sid))
            self.assertEqual([c for (c,) in db.conn.execute(
                "SELECT c.name FROM song_chords sc JOIN chords c ON c.id = sc.chord_id WHERE sc.song_id = ?", (sid,))],
                ['Em'])
            self.assertEqual(migrate.merge(db, other)['locked texts'], 0)    # once
            db.close()

    def test_central_newer_wins(self):
        from web import migrate
        with tempfile.TemporaryDirectory() as tmp:
            local = os.path.join(tmp, 'local.db')
            central = os.path.join(tmp, 'central.db')
            target = os.path.join(tmp, 'web.db')
            make_db(local).close()
            con = sqlite3.connect(central)
            con.executescript("""
                CREATE TABLE songs (id integer primary key, title text, artist text, album text,
                    lyrics_chords text, lyrics_url text, capo int, chord_update text, modified text);
                CREATE TABLE song_chords (song_id int, name text);
                CREATE TABLE song_tags (song_id int, user text, name text);
                INSERT INTO songs VALUES (1, 'Gamma', 'Other', 'LP', 'E A\nnew words', 'u', 0, '2026-03-03', '2026-03-03');
                INSERT INTO song_chords VALUES (1, 'E'), (1, 'A');""")
            con.commit()
            con.close()
            migrate.init(target, local, central, user='jo')
            con = sqlite3.connect(target)
            self.assertEqual(con.execute("SELECT lyrics_chords FROM songs WHERE title='Gamma'").fetchone()[0],
                             'E A\nnew words')
            con.close()


if __name__ == '__main__':
    unittest.main()
