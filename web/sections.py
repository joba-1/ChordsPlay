# SPDX-FileCopyrightText: 2026 joba1
# SPDX-License-Identifier: AGPL-3.0-or-later
'''
Practice sections: named parts of a song (start and end in seconds) to play once or loop
with a count-in, per user and song. See docs/spec-practice.md.
'''

SCHEMA = """
CREATE TABLE IF NOT EXISTS practice_sections (
    id      integer primary key autoincrement,
    user_id int not null references users(id) on delete cascade,
    song_id int not null references songs(id) on delete cascade,
    name    text not null,
    start   real not null,
    "end"   real not null);
"""

NAME_MAX = 60


def check(data, old=None):
    '''(name, start, end) from a request body, filled from old; ValueError if invalid.'''
    old = old or {}
    name = data.get('name', old.get('name'))
    start = data.get('start', old.get('start'))
    end = data.get('end', old.get('end'))
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > NAME_MAX:
        raise ValueError('a name of 1 to {} characters'.format(NAME_MAX))
    if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, (int, float)) \
            or not isinstance(end, (int, float)) or not 0 <= start < end <= 24 * 3600:
        raise ValueError('a start before the end, in seconds')
    return name.strip(), round(float(start), 3), round(float(end), 3)


def rows(conn, user_id, song_id):
    return [dict(id=r[0], name=r[1], start=r[2], end=r[3]) for r in conn.execute(
        'SELECT id, name, start, "end" FROM practice_sections WHERE user_id = ? AND song_id = ? '
        'ORDER BY start, id', (user_id, song_id))]


def get(conn, user_id, section_id):
    r = conn.execute('SELECT id, name, start, "end", song_id FROM practice_sections WHERE id = ? AND user_id = ?',
                     (section_id, user_id)).fetchone()
    return dict(id=r[0], name=r[1], start=r[2], end=r[3], song_id=r[4]) if r else None


def add(conn, user_id, song_id, name, start, end):
    cur = conn.execute('INSERT INTO practice_sections (user_id, song_id, name, start, "end") VALUES (?, ?, ?, ?, ?)',
                       (user_id, song_id, name, start, end))
    conn.commit()
    return cur.lastrowid


def update(conn, section_id, name, start, end):
    conn.execute('UPDATE practice_sections SET name = ?, start = ?, "end" = ? WHERE id = ?',
                 (name, start, end, section_id))
    conn.commit()


def delete(conn, section_id):
    conn.execute('DELETE FROM practice_sections WHERE id = ?', (section_id,))
    conn.commit()
