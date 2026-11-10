'''
PDF tabs for the web app: find PDF files, link each to a song.

The place for a tab is next to its song, like a hand-saved song text: the song file's
name with .pdf ("13 Cruz.mp3" -> "13 Cruz.pdf"), or with a part after " - "
("13 Cruz - Solo.pdf"). The library's folders are always scanned for these.

Elsewhere (CHORDSPLAY_PDF_DIRS) a file named "Artist - Title.pdf" or "Artist - Title -
Part.pdf" ("Queen - Hammer To Fall - Solo 1.pdf") is linked by its name; case,
punctuation and a leading "The" don't matter, and a title may continue after the
song's title ("House Of The Rising Sun Fingerpicking": part "Fingerpicking"). What
doesn't match waits for the user to pick the song in the page (an assignment by
hand is never changed by a later scan).

Tables (in the web DB):
  pdf_files   one row per file found: id, path, size, mtime
  song_pdfs   pdf -> song (song_id NULL: not a song's tab), part, manual
  song_view   what a song shows: its text (no row) or one of its PDFs
'''

import os
import re
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS pdf_files (
    id      integer primary key autoincrement,
    path    text not null unique,
    size    int,
    mtime   real,
    seen    real);
CREATE TABLE IF NOT EXISTS song_pdfs (
    pdf_id  int not null primary key references pdf_files(id) on delete cascade,
    song_id int references songs(id) on delete cascade,
    part    text,
    manual  int not null default 0);
CREATE TABLE IF NOT EXISTS song_view (
    song_id int not null primary key references songs(id) on delete cascade,
    pdf_id  int not null references pdf_files(id) on delete cascade);
"""


def norm(text):
    '''For comparing names: lower case, "&" as "and", no punctuation, no leading "the".'''
    text = text.casefold().replace('&', ' and ').replace('⧸', '/')
    words = re.sub(r'[^\w]+', ' ', text).split()
    if words and words[0] == 'the':
        words = words[1:]
    return ' '.join(words)


def split_name(path):
    '''(artist, title, part) from "Artist - Title[ - Part].pdf", or None.'''
    stem = os.path.splitext(os.path.basename(path))[0]
    pieces = [p.strip() for p in re.split(r'\s+-\s+', stem) if p.strip()]
    if len(pieces) < 2:
        return None
    return pieces[0], pieces[1], ' - '.join(pieces[2:]) or None


def match(path, songs):
    '''(song_id, part) for a file, or None. songs: [(id, title, artist)].'''
    parts = split_name(path)
    if parts is None:
        return None
    artist, title, part = parts
    a, t = norm(artist), norm(title)
    best = None
    for sid, s_title, s_artist in songs:
        sa, st = norm(s_artist or ''), norm(s_title or '')
        if not st or not (sa == a or (a and sa and (a in sa or sa in a))):
            continue
        if t == st:
            score, extra = (2, len(st)), None
        elif t.startswith(st + ' '):
            # the words after the song's title, as written in the file name
            score, extra = (1, len(st)), ' '.join(title.split()[len(st.split()):]) or None
        else:
            continue
        if best is None or score > best[0]:
            best = (score, sid, ' - '.join(x for x in (extra, part) if x) or None)
    return (best[1], best[2]) if best else None


def beside(path, song_files):
    '''(song_id, part) for a PDF next to a song file of the same name, or None.
    song_files: {(folder, name without extension, case-folded): song_id}.'''
    folder, stem = os.path.dirname(path), os.path.splitext(os.path.basename(path))[0]
    pieces = re.split(r'(\s+-\s+)', stem)
    # the longest song name first: "A - B - Solo" is song "A - B", part "Solo"
    for n in range(len(pieces), 0, -2):
        sid = song_files.get((folder, ''.join(pieces[:n]).casefold()))
        if sid is not None:
            return sid, ''.join(pieces[n + 1:]).strip() or None
    return None


def song_files(conn):
    files = {}
    for sid, folder, name in conn.execute("SELECT ls.song_id, l.name, ls.filename FROM list_songs ls "
                                         "JOIN song_lists l ON l.id = ls.song_lists_id WHERE ls.song_id IS NOT NULL"):
        path = os.path.join(folder, name)
        files[(os.path.dirname(path), os.path.splitext(os.path.basename(path))[0].casefold())] = sid
    return files


def library_folders(conn):
    return [name for (name,) in conn.execute("SELECT name FROM song_lists") if name and os.path.isdir(name)]


def find_files(folders):
    seen = set()
    for folder in folders:
        for root, dirs, files in os.walk(folder):
            dirs[:] = [d for d in dirs if not d.startswith('.')]     # .@__thumb and the like
            for name in files:
                path = os.path.join(root, name)
                if name.lower().endswith('.pdf') and not name.startswith('.') and path not in seen:
                    seen.add(path)
                    yield path


def scan(conn, folders):
    '''Bring pdf_files and the automatic links up to date: the library's folders and
    `folders`. Returns (found, linked now).'''
    now = time.time()
    songs = conn.execute("SELECT id, title, artist FROM songs").fetchall()
    files = song_files(conn)
    found = linked = 0
    for path in find_files(library_folders(conn) + list(folders)):
        found += 1
        st = os.stat(path)
        conn.execute("INSERT INTO pdf_files (path, size, mtime, seen) VALUES (?, ?, ?, ?) "
                     "ON CONFLICT(path) DO UPDATE SET size = excluded.size, mtime = excluded.mtime, seen = excluded.seen",
                     (path, st.st_size, st.st_mtime, now))
        pdf_id = conn.execute("SELECT id FROM pdf_files WHERE path = ?", (path,)).fetchone()[0]
        row = conn.execute("SELECT song_id, manual, part FROM song_pdfs WHERE pdf_id = ?", (pdf_id,)).fetchone()
        if row and row[1]:
            continue                # assigned by hand
        hit = beside(path, files) or match(path, songs)
        if hit and (row is None or (row[0], row[2]) != hit):
            conn.execute("INSERT OR REPLACE INTO song_pdfs (pdf_id, song_id, part, manual) VALUES (?, ?, ?, 0)",
                         (pdf_id, hit[0], hit[1]))
            linked += 1
    # files that are gone: forget them (their links and views go with them)
    conn.execute("DELETE FROM pdf_files WHERE seen < ?", (now,))
    conn.commit()
    return found, linked


def label(path, part):
    return part or os.path.splitext(os.path.basename(path))[0]


def song_pdfs(conn, song_id):
    '''The song's PDFs as [{id, name, part}], in name order.'''
    rows = conn.execute("""SELECT f.id, f.path, p.part FROM song_pdfs p JOIN pdf_files f ON f.id = p.pdf_id
                           WHERE p.song_id = ? ORDER BY p.part IS NOT NULL, p.part, f.path""", (song_id,))
    return [{'id': pid, 'name': os.path.basename(path), 'folder': os.path.basename(os.path.dirname(path)), 'part': part}
            for pid, path, part in rows]


def song_view(conn, song_id):
    row = conn.execute("SELECT pdf_id FROM song_view WHERE song_id = ?", (song_id,)).fetchone()
    return row[0] if row else None
