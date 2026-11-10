'''
Tests for the web app (web/app.py) and the database migration (web/migrate.py).

Run from the repo root:  <venv with web/requirements.txt>/bin/python -m unittest discover -s tests
Skipped when Flask is not installed (the Tk app's venv does not need it).
'''

import os
import sqlite3
import tempfile
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


class Migrate(unittest.TestCase):

    def test_init_and_merge_plays_once(self):
        from web import migrate
        with tempfile.TemporaryDirectory() as tmp:
            local = os.path.join(tmp, 'local.db')
            other = os.path.join(tmp, 'other.db')
            target = os.path.join(tmp, 'web', 'web.db')
            make_db(local, plays=[('Alpha', '2026-01-01 10:00:00')]).close()
            db = make_db(other, plays=[('Alpha', '2026-01-01 10:00:00'), ('Beta', '2026-02-02 10:00:00')])
            db.setUserTag(db.conn.execute("SELECT id FROM songs WHERE title='Beta'").fetchone()[0], 1)
            db.close()
            migrate.init(target, local, user='jo')
            db = ChordsPlay.db.Db(target, 'jo')
            first = migrate.merge(db, other)
            again = migrate.merge(db, other)
            self.assertEqual(first['plays'], 1)   # Alpha's play was there already
            self.assertEqual(first['tags'], 1)
            self.assertEqual(again['plays'], 0)
            self.assertEqual(db.conn.execute("SELECT count(*) FROM user_songs").fetchone()[0], 2)
            db.close()
            with self.assertRaises(SystemExit):
                migrate.init(target, local)

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
