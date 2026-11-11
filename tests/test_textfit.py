# SPDX-FileCopyrightText: 2016-2026 joba1
# SPDX-License-Identifier: AGPL-3.0-or-later
'''
Unit tests for wrapping comment lines (ChordsPlay/textfit.py).

Run from the repo root:  venv/bin/python -m unittest discover -s tests
'''

import unittest

from ChordsPlay.textfit import is_chord_line, is_tab_line, song_lines, wrap_comments


class ChordLines(unittest.TestCase):

    def test_chord_lines(self):
        for line in ['Am    F     C', 'C#m(add9)      C#m      B', 'Intro:  Ab7sus4     Ab7     Ab6sus4',
                     'Em  D  C  x2', '| G  | D/F# | Em |', 'Am F Dsus2/4 Am7 Fmaj9 (Use D the second time)',
                     'N.C.   E5   D5']:
            self.assertTrue(is_chord_line(line), line)

    def test_text_lines(self):
        for line in ['A man walks down the street', 'I am a man', 'And I guess that is why',
                     'Note: recorded version is 30 cents sharp', '', '[Chorus]']:
            self.assertFalse(is_chord_line(line), line)

    def test_tab_lines(self):
        self.assertTrue(is_tab_line('e|-----0---3---|'))
        self.assertTrue(is_tab_line('|--3--2--0-----|'))
        self.assertFalse(is_tab_line('Well - this is - a comment'))

    def test_line_below_chords_is_song_text(self):
        lines = ['Some intro remark', 'Am        C', 'Words under the chords', '', 'free text']
        self.assertEqual(song_lines(lines), {1, 2})


class Wrap(unittest.TestCase):

    SONG = '\n'.join([
        'For the Bm chord you can choose not to strum the low E string and only bar the A, D, G, B strings',
        '',
        'Am            C          G',
        'Here are the words of the song',
        'More words without chords',
        'G         D',
        'And another line of the song',
    ])

    def test_comment_wrapped_to_song_width(self):
        out = wrap_comments(self.SONG).splitlines()
        width = max(len(line) for line in out)
        self.assertLessEqual(width, 30)
        self.assertIn('Here are the words of the song', out)
        self.assertEqual(' '.join(out[:out.index('')]),
                         self.SONG.splitlines()[0])  # nothing lost

    def test_song_text_never_changed(self):
        long_chords = 'A' + ' ' * 80 + 'B'
        text = 'Long comment line ' * 8 + '\n' + long_chords + '\nwords\n'
        out = wrap_comments(text).splitlines()
        self.assertIn(long_chords, out)
        self.assertLessEqual(max(len(line) for line in out), len(long_chords))

    def test_typical_lyrics_kept(self):
        # most lines are 40 wide: plain lyrics of that width stay whole although
        # the only chord line is short
        lyrics = ['These are the plain lyrics, forty chars'] * 12
        text = '\n'.join(['C   G'] + lyrics + ['x' * 10 + ' comment' * 10])
        out = wrap_comments(text).splitlines()
        self.assertEqual(out[1:13], lyrics)
        self.assertLessEqual(max(len(line) for line in out), 39)

    def test_without_song_text_unchanged(self):
        text = 'Only prose here, ' * 10
        self.assertEqual(wrap_comments(text), text.rstrip())


if __name__ == '__main__':
    unittest.main()
