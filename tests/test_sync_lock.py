'''
A song locked with tag x ("Fixiert") keeps its text when the sync pulls a newer
version (until 2026-11-10 the sync overwrote it: last write won).
'''

import os
import tempfile
import unittest

import ChordsPlay.db


class SyncKeepsLockedText(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = ChordsPlay.db.Db(os.path.join(self.tmp.name, 'cp.db'), 'jo')
        self.db.conn.execute("INSERT INTO songs (title, artist, lyrics_chords, modified) "
                             "VALUES ('Rockstar', 'Nickelback', 'my own text', '2026-01-01 10:00:00')")
        self.sid = self.db.conn.execute("SELECT id FROM songs").fetchone()[0]
        self.db.conn.commit()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def remote(self, text, tags=()):
        return {'title': 'Rockstar', 'artist': 'Nickelback', 'lyrics_chords': text,
                'modified': '2026-11-09 16:56:26', 'tags': list(tags), 'chords': ['G']}

    def row(self):
        return self.db.conn.execute("SELECT lyrics_chords, dirty, modified FROM songs WHERE id = ?",
                                    (self.sid,)).fetchone()

    def test_unlocked_song_takes_the_newer_text(self):
        self.assertTrue(self.db.upsertRemoteSong(self.remote('fetched again')))
        self.assertEqual(self.row()[0], 'fetched again')

    def test_locked_song_keeps_its_text_and_goes_back_to_central(self):
        self.db.setUserTag(self.sid, 6)                     # x, Fixiert
        self.assertFalse(self.db.upsertRemoteSong(self.remote('fetched again', [('jo', 'Favorit')])))
        text, dirty, modified = self.row()
        self.assertEqual(text, 'my own text')
        self.assertEqual(dirty, 1)                          # pushed next, with its lock
        self.assertGreater(modified, '2026-11-09 16:56:26')  # and newer than the overwrite
        self.assertIn('f', ''.join(k for _, k, on, _ in self.db.getUserTags(self.sid) if on == 'X'))

    def test_locked_on_both_sides_keeps_ours_without_ping_pong(self):
        self.db.setUserTag(self.sid, 6)
        self.db.conn.execute("UPDATE songs SET dirty = 0 WHERE id = ?", (self.sid,))
        self.db.upsertRemoteSong(self.remote('their locked text', [('ca', 'Fixiert')]))
        self.assertEqual(self.row()[:2], ('my own text', 0))


if __name__ == '__main__':
    unittest.main()
