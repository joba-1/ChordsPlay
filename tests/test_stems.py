# SPDX-FileCopyrightText: 2026 joba1
# SPDX-License-Identifier: AGPL-3.0-or-later
'''
Tests for the practice mix's stems (web/stems.py) and its API in web/app.py. A small
Python script stands in for the splitter (contrib/stems/chordsplay-stems), so no model
or GPU is needed.
'''

import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest

from tests.test_web import Api, flask, make_db
from web import stems as stemsmod

FAKE = r'''
import json, os, sys
song, out = sys.argv[-2], sys.argv[-1]
mode = os.environ.get('FAKE_STEMS', 'ok')
if mode == 'fail':
    print('chordsplay-stems: GPU busy: 900 MB free, 4000 MB needed', file=sys.stderr)
    sys.exit(3)
with open(out, 'wb') as f:
    f.write(b'webm of ' + os.path.basename(song).encode())
manifest = {'version': 1, 'channels': 4, 'stems': [{'name': 'drums', 'pair': 0},
                                                   {'name': 'guitar', 'pair': 1}]}
if mode == 'bad':
    manifest['stems'][1]['pair'] = 2      # beyond the channels
with open(out[:-5] + '.json', 'w') as f:
    json.dump(manifest, f)
'''


class Base(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, 'web.db')
        make_db(self.path, library=self.tmp.name).close()
        self.fake = os.path.join(self.tmp.name, 'fake-split.py')
        with open(self.fake, 'w') as f:
            f.write(FAKE)
        self.stems = stemsmod.Stems(self.path, '"{}" "{}" --device gpu'.format(sys.executable, self.fake),
                                    os.path.join(self.tmp.name, 'stems'))
        os.makedirs(self.stems.folder, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.executescript(stemsmod.SCHEMA)
        os.environ.pop('FAKE_STEMS', None)

    def tearDown(self):
        self.conn.close()
        os.environ.pop('FAKE_STEMS', None)
        self.tmp.cleanup()

    def song(self, title='Alpha'):
        return self.conn.execute("SELECT id FROM songs WHERE title = ?", (title,)).fetchone()[0]


class Worker(Base):

    def test_not_split_then_queued_then_ready(self):
        sid = self.song()
        self.assertEqual(self.stems.status(self.conn, sid)['state'], 'none')
        self.assertEqual(self.stems.request(self.conn, sid)['state'], 'queued')
        self.assertTrue(self.stems.work_one())
        st = self.stems.status(self.conn, sid)
        self.assertEqual(st['state'], 'ready')
        self.assertEqual([s['name'] for s in st['manifest']['stems']], ['drums', 'guitar'])
        webm, manifest = self.stems.paths(sid)
        with open(webm, 'rb') as f:
            self.assertEqual(f.read(), b'webm of a.mp3')     # the command got the song's file
        self.assertFalse(any('.part.' in n for n in os.listdir(self.stems.folder)))
        self.assertFalse(self.stems.work_one())              # nothing left

    def test_request_twice_queues_once(self):
        sid = self.song()
        self.stems.request(self.conn, sid)
        self.stems.request(self.conn, sid)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM stems").fetchone()[0], 1)
        self.stems.work_one()
        self.assertEqual(self.stems.request(self.conn, sid)['state'], 'ready')   # ready: not again

    def test_oldest_first(self):
        a, b = self.song('Alpha'), self.song('Beta')
        self.stems.request(self.conn, b)
        time.sleep(0.01)
        self.stems.request(self.conn, a)
        self.stems.work_one()
        self.assertEqual(self.stems.status(self.conn, b)['state'], 'ready')
        self.assertEqual(self.stems.status(self.conn, a)['state'], 'queued')

    def test_failure_keeps_the_message_and_can_be_retried(self):
        sid = self.song()
        os.environ['FAKE_STEMS'] = 'fail'
        self.stems.request(self.conn, sid)
        self.stems.work_one()
        st = self.stems.status(self.conn, sid)
        self.assertEqual(st['state'], 'failed')
        self.assertIn('GPU busy', st['error'])
        os.environ['FAKE_STEMS'] = 'ok'
        self.assertEqual(self.stems.request(self.conn, sid)['state'], 'queued')
        self.stems.work_one()
        self.assertEqual(self.stems.status(self.conn, sid)['state'], 'ready')

    def test_bad_manifest_fails(self):
        sid = self.song()
        os.environ['FAKE_STEMS'] = 'bad'
        self.stems.request(self.conn, sid)
        self.stems.work_one()
        st = self.stems.status(self.conn, sid)
        self.assertEqual(st['state'], 'failed')
        self.assertFalse(os.path.exists(self.stems.paths(sid)[0]))

    def test_changed_song_file_counts_as_not_split(self):
        sid = self.song()
        self.stems.request(self.conn, sid)
        self.stems.work_one()
        song = os.path.join(self.tmp.name, 'a.mp3')
        with open(song, 'ab') as f:
            f.write(b'more')
        self.assertEqual(self.stems.status(self.conn, sid)['state'], 'none')
        self.assertEqual(self.stems.request(self.conn, sid)['state'], 'queued')

    def test_deleted_files_count_as_not_split(self):
        sid = self.song()
        self.stems.request(self.conn, sid)
        self.stems.work_one()
        os.remove(self.stems.paths(sid)[0])
        self.assertEqual(self.stems.status(self.conn, sid)['state'], 'none')

    def test_missing_command_fails_cleanly(self):
        stems = stemsmod.Stems(self.path, '/nonexistent/split', self.stems.folder)
        sid = self.song()
        stems.request(self.conn, sid)
        stems.work_one()
        self.assertIn('cannot run', stems.status(self.conn, sid)['error'])

    def test_timeout(self):
        stems = stemsmod.Stems(self.path, '"{}" -c "import time; time.sleep(5)"'.format(sys.executable),
                               self.stems.folder, timeout=1)
        sid = self.song()
        stems.request(self.conn, sid)
        stems.work_one()
        self.assertIn('timed out', stems.status(self.conn, sid)['error'])

    def test_restart_requeues_a_running_split(self):
        sid = self.song()
        self.stems.request(self.conn, sid)
        self.conn.execute("UPDATE stems SET state = 'running'")
        self.conn.commit()
        self.stems.wake.set()
        self.stems.loop = lambda: None          # no worker thread: only the recovery
        self.stems.start()
        self.assertEqual(self.stems.status(self.conn, sid)['state'], 'queued')

    def test_not_offered_without_command(self):
        stems = stemsmod.Stems(self.path, '', self.stems.folder)
        self.assertFalse(stems.offered)
        self.assertFalse(stems.status(self.conn, self.song())['offered'])

    def test_manifest_check(self):
        good = os.path.join(self.tmp.name, 'm.json')
        for doc, ok in [({'channels': 2, 'stems': [{'name': 'x', 'pair': 0}]}, True),
                        ({'channels': 2, 'stems': [{'name': 'x', 'pair': 1}]}, False),
                        ({'channels': 2, 'stems': []}, False),
                        ({'stems': [{'name': 'x', 'pair': 0}]}, False),
                        ([1, 2], False)]:
            with open(good, 'w') as f:
                json.dump(doc, f)
            self.assertEqual(stemsmod.read_manifest(good) is not None, ok, doc)


@unittest.skipIf(flask is None, 'flask not installed')
class StemsApi(Api):

    def setUp(self):
        super().setUp()
        self.fake = os.path.join(self.tmp.name, 'fake-split.py')
        with open(self.fake, 'w') as f:
            f.write(FAKE)
        self.saved = self.app.STEMS
        self.app.STEMS = stemsmod.Stems(self.path, '"{}" "{}"'.format(sys.executable, self.fake),
                                        os.path.join(self.tmp.name, 'stems'))
        os.makedirs(self.app.STEMS.folder, exist_ok=True)
        self.sid = self.client.get('/api/state').get_json()['playlist'][0]['id']

    def tearDown(self):
        self.app.STEMS = self.saved
        super().tearDown()

    def test_state_offers_practice(self):
        self.assertTrue(self.client.get('/api/state').get_json()['practice'])

    def test_queue_split_and_serve(self):
        r = self.client.get('/api/song/{}/stems'.format(self.sid)).get_json()
        self.assertEqual((r['offered'], r['state']), (True, 'none'))
        self.assertEqual(self.client.get('/api/stems/{}.webm'.format(self.sid)).status_code, 404)
        r = self.client.post('/api/song/{}/stems'.format(self.sid)).get_json()
        self.assertEqual(r['state'], 'queued')
        self.app.STEMS.work_one()
        r = self.client.get('/api/song/{}/stems'.format(self.sid)).get_json()
        self.assertEqual(r['state'], 'ready')
        self.assertEqual(r['manifest']['channels'], 4)
        audio = self.client.get('/api/stems/{}.webm'.format(self.sid))
        self.assertEqual(audio.status_code, 200)
        self.assertEqual(audio.mimetype, 'audio/webm')
        self.assertEqual(audio.data, b'webm of a.mp3')
        audio.close()
        part = self.client.get('/api/stems/{}.webm'.format(self.sid), headers={'Range': 'bytes=0-3'})
        self.assertEqual((part.status_code, part.data), (206, b'webm'))
        part.close()

    def test_unknown_song(self):
        self.assertEqual(self.client.get('/api/song/9999/stems').status_code, 404)
        self.assertEqual(self.client.post('/api/song/9999/stems').status_code, 404)

    def test_not_offered(self):
        self.app.STEMS = stemsmod.Stems(self.path, '', self.app.STEMS.folder)
        self.assertFalse(self.client.get('/api/state').get_json()['practice'])
        self.assertEqual(self.client.post('/api/song/{}/stems'.format(self.sid)).status_code, 409)


class QueueByTag(Base):

    def tag(self, title, key='f', user='jo'):
        conn = self.conn
        conn.execute("INSERT OR IGNORE INTO tags (name, key) VALUES ('Fav', ?)", (key,))
        conn.execute("""INSERT INTO song_tags (user_id, song_id, tag_id)
                        SELECT u.id, ?, t.id FROM users u, tags t WHERE u.name = ? AND t.key = ?""",
                     (self.song(title), user, key))
        conn.commit()

    def test_tagged_songs_only_and_not_twice(self):
        self.tag('Alpha')
        self.tag('Gamma')
        ids = stemsmod.tagged_songs(self.conn, 'jo', 'f')
        self.assertEqual(sorted(ids), sorted([self.song('Alpha'), self.song('Gamma')]))
        self.assertEqual(stemsmod.tagged_songs(self.conn, 'someone', 'f'), [])
        self.assertEqual(stemsmod.queue_many(self.stems, self.conn, ids), 2)
        self.assertEqual(stemsmod.queue_many(self.stems, self.conn, ids), 0)   # already queued
        self.assertEqual(stemsmod.pending(self.conn), 2)
        while self.stems.work_one():
            pass
        self.assertEqual(stemsmod.pending(self.conn), 0)
        self.assertEqual(stemsmod.queue_many(self.stems, self.conn, ids), 0)   # ready

    def test_failed_ones_are_queued_again(self):
        self.tag('Alpha')
        os.environ['FAKE_STEMS'] = 'fail'
        stemsmod.queue_many(self.stems, self.conn, [self.song('Alpha')])
        self.stems.work_one()
        self.assertEqual(stemsmod.queue_many(self.stems, self.conn, [self.song('Alpha')]), 1)

    def test_command_line(self):
        import contextlib
        import io
        self.tag('Beta')
        os.environ['CHORDSPLAY_WEB_DB'] = self.path
        os.environ['CHORDSPLAY_STEMS_DIR'] = self.stems.folder
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(stemsmod.main(['queue', '--user', 'jo', '--tag', 'f']), 0)
            self.assertIn('1 queued', out.getvalue())
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(stemsmod.main(['wait', '--timeout', '0.05', '--every', '0.01']), 1)
            self.stems.work_one()
            self.assertEqual(stemsmod.main(['wait', '--timeout', '1']), 0)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                stemsmod.main(['list'])
            self.assertIn('ready', out.getvalue())
        finally:
            os.environ.pop('CHORDSPLAY_WEB_DB')
            os.environ.pop('CHORDSPLAY_STEMS_DIR')


@unittest.skipIf(flask is None, 'flask not installed')
class Sections(Api):

    def setUp(self):
        super().setUp()
        self.sid = self.client.get('/api/state').get_json()['playlist'][0]['id']
        self.url = '/api/song/{}/sections'.format(self.sid)

    def test_add_list_change_delete(self):
        self.assertEqual(self.client.get(self.url).get_json()['sections'], [])
        r = self.client.post(self.url, json={'name': ' Solo ', 'start': 84.5, 'end': 111}).get_json()
        self.client.post(self.url, json={'name': 'Intro', 'start': 0, 'end': 12.25})
        r = self.client.get(self.url).get_json()['sections']
        self.assertEqual([(x['name'], x['start'], x['end']) for x in r], [('Intro', 0, 12.25), ('Solo', 84.5, 111)])
        solo = r[1]['id']
        r = self.client.put('/api/sections/{}'.format(solo), json={'end': 112.5}).get_json()['sections']
        self.assertEqual(r[1]['end'], 112.5)
        self.assertEqual(r[1]['name'], 'Solo')
        r = self.client.delete('/api/sections/{}'.format(solo)).get_json()['sections']
        self.assertEqual([x['name'] for x in r], ['Intro'])

    def test_invalid(self):
        for body in [{'name': '', 'start': 0, 'end': 1}, {'name': 'x', 'start': 5, 'end': 5},
                     {'name': 'x', 'start': -1, 'end': 5}, {'name': 'x' * 61, 'start': 0, 'end': 1},
                     {'name': 'x', 'start': '1', 'end': 5}, {'name': 'x', 'start': True, 'end': 5}, {}]:
            self.assertEqual(self.client.post(self.url, json=body).status_code, 400, body)
        self.assertEqual(self.client.post('/api/song/9999/sections', json={'name': 'x', 'start': 0, 'end': 1}).status_code, 404)

    def test_only_own_sections(self):
        r = self.client.post(self.url, json={'name': 'Solo', 'start': 1, 'end': 2}).get_json()['sections']
        other = {'X-Remote-User': 'flo'}
        self.assertEqual(self.client.get(self.url, headers=other).get_json()['sections'], [])
        self.assertEqual(self.client.delete('/api/sections/{}'.format(r[0]['id']), headers=other).status_code, 404)
        self.assertEqual(len(self.client.get(self.url).get_json()['sections']), 1)


if __name__ == '__main__':
    unittest.main()
