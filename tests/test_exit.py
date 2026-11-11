# SPDX-FileCopyrightText: 2016-2026 joba1
# SPDX-License-Identifier: AGPL-3.0-or-later
'''
Unit tests for a clean exit: the scanner stops between songs when asked,
and the DB is committed and closed.
'''

import os
import sqlite3
import tempfile
import time
import unittest

import ChordsPlay.db
import ChordsPlay.scan


class ScannerQuits(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, 't.db')
        db = ChordsPlay.db.Db(self.path, 'tester')
        db.conn.executemany("INSERT INTO songs (title, artist) VALUES (?, 'Band')",
                            [('Song {}'.format(i),) for i in range(50)])   # all unchecked
        db.close()

    def tearDown(self):
        self.dir.cleanup()

    def test_stops_between_songs(self):
        scanner = ChordsPlay.scan.Scan(self.path, 'tester')
        self.assertTrue(scanner.daemon)         # a hung network request can't block exit
        fetched = []

        def slow_request(title, artist, to_file=None):
            fetched.append(title)
            time.sleep(0.1)                     # a song fetch from Ultimate Guitar
            return False                        # "no chords": nothing to store
        scanner.chords.request = slow_request
        scanner.start()
        time.sleep(0.35)
        t0 = time.monotonic()
        scanner.quit.set()
        scanner.join(2)
        self.assertFalse(scanner.is_alive())
        self.assertLess(time.monotonic() - t0, 0.5)
        self.assertLess(len(fetched), 10)       # before the fix it went through all 50


class DbClose(unittest.TestCase):

    def test_close_commits(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 't.db')
            db = ChordsPlay.db.Db(path, 'tester')
            db.conn.execute("INSERT INTO songs (title, artist) VALUES ('Kept', 'Band')")
            db.close()                          # no explicit commit before
            with self.assertRaises(sqlite3.ProgrammingError):
                db.conn.execute("SELECT 1")
            check = sqlite3.connect(path)
            self.assertEqual(check.execute("SELECT count(*) FROM songs WHERE title = 'Kept'")
                             .fetchone()[0], 1)
            check.close()


if __name__ == '__main__':
    unittest.main()
