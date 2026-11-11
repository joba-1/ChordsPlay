'''
Prepare a song text for "as big as fits" display.

Chord sheets from Ultimate Guitar often carry comments in long prose lines
("Note: the recorded version is 30 cents sharp ..."). The widest line sets the
font size, so one such comment can shrink the whole song. This module wraps
lines that are not song text to the width the song text itself needs.

Song text is a chord line, the line right below a chord line (chords are
aligned to it by column), or a tab line. Those are never changed. Every other
line (comments, headers, lyrics without chords) may be word-wrapped.

Pure functions, no GUI.
'''

import re
import textwrap

# A chord name like C, F#m7, Bb/F, Dsus2/4, C#m(add9), Ab7sus4, Fmaj9, A6.
CHORD = re.compile(
    r'^\(?[A-H][#b]?'
    r'(?:maj|min|aug|dim|sus|add|M|m|\+|°|ø|-)?'
    r'(?:\(?(?:maj|sus|add|M|[#b+-])?\d{0,2}\)?)*'
    r'(?:/[A-H0-9][#b]?\d?)?\)?[*,.]?$')

# Tokens that may stand between chords without making a line prose.
NOISE = re.compile(r'^(?:[|/\\.~:*-]+|[x*]\d+|\(?x?\d+x?\)?|N\.?C\.?|%)$', re.IGNORECASE)

# Tab staff lines: e|---0---| or |--3--| or lines that are mostly dashes and digits.
TAB = re.compile(r'(?:^\s*[A-Ga-g]?[#b]?\s*[|:]-)|(?:-{3,}.*\|)|(?:\|.*-{3,})')

MIN_WIDTH = 30  # never wrap narrower than this
TYPICAL_SHARE = 0.9  # lines up to the length 90 % of the lines have are never wrapped


def is_chord_line(line):
    '''True if the line consists mostly of chord names.

    A leading label like "Intro:" and trailing remarks like "(use D the second
    time)" are allowed, as long as chords make up at least half of the words.
    '''
    tokens = line.split()
    if not tokens:
        return False
    if tokens[0].endswith(':') and not CHORD.match(tokens[0][:-1]):
        tokens = tokens[1:]  # label
    chords = 0
    words = 0
    for token in tokens:
        if CHORD.match(token):
            chords += 1
            words += 1
        elif not NOISE.match(token):
            words += 1
    return chords > 0 and chords * 2 >= words


def is_tab_line(line):
    return TAB.search(line) is not None


def song_lines(lines):
    '''Indexes of the lines that are song text and must not be changed.'''
    keep = set()
    for i, line in enumerate(lines):
        if is_chord_line(line) or is_tab_line(line):
            keep.add(i)
            below = i + 1
            if below < len(lines) and lines[below].strip() and not is_chord_line(lines[below]):
                keep.add(below)
    return keep


def wrap_comments(text, min_width=MIN_WIDTH):
    '''Word-wrap lines that are not song text to the width of the song text.

    The limit is the longest song text line, but at least the length most
    lines stay within (TYPICAL_SHARE of them), so plain lyrics without chords
    above them are not broken up; and at least min_width. Without any
    recognisable song text nothing is changed: there is no width to aim for.
    '''
    lines = [line.rstrip() for line in text.expandtabs().splitlines()]
    keep = song_lines(lines)
    if not keep:
        return '\n'.join(lines)
    lengths = sorted(len(line) for line in lines if line.strip())
    typical = lengths[int((len(lengths) - 1) * TYPICAL_SHARE)]
    limit = max(min_width, typical, max(len(lines[i]) for i in keep))
    out = []
    for i, line in enumerate(lines):
        if i in keep or len(line) <= limit:
            out.append(line)
        else:
            indent = line[:len(line) - len(line.lstrip())]
            if len(indent) > limit // 2:
                indent = ''  # right-aligned credits: no point keeping the indent
            out.extend(textwrap.wrap(line.strip(), width=limit, initial_indent=indent,
                                     subsequent_indent=indent, break_long_words=True,
                                     break_on_hyphens=False) or [''])
    return '\n'.join(out)
