# SPDX-FileCopyrightText: 2016-2026 joba1
# SPDX-License-Identifier: AGPL-3.0-or-later
'''
Suggested amp presets: a default sound for songs without their own preset.

A local LLM (Ollama, CHORDSPLAY_OLLAMA_URL) puts each
song into one of a few sound categories and says whether it knows the song or
guesses. A table maps each category to an amp slot, set once by ear (the draft
below goes by the names of the factory presets).
The artist's genres from MusicBrainz go into the question as context.

Precedence when a song plays: its own cues > artist preset > suggestion (only one
the model knows, not rejected) > amp_default_slot. Guesses are shown, not applied.

  python -m web.suggest [--limit N] [--redo] [--song ID]    fill song_suggestions
'''

import argparse
import json
import logging
import os
import sys
import time
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

OLLAMA_URL = os.environ.get('CHORDSPLAY_OLLAMA_URL', 'http://127.0.0.1:11434')
MODEL = os.environ.get('CHORDSPLAY_SUGGEST_MODEL', 'qwen3.6:latest')
MB_URL = 'https://musicbrainz.org/ws/2/'
MB_AGENT = 'ChordsPlayWeb/0.5'      # generic: no personal data (MusicBrainz asks for a name)
MB_INTERVAL = 1.1                   # MusicBrainz allows one request per second, else 503
MB_MIN_SCORE = 90                   # trust an artist search hit only this sure

# category -> draft slot (by name on the amp, 2026-11-09); edited in the page's tone dialog
CATEGORIES = [
    ('acoustic', 14), ('clean', 48), ('clean_chorus', 40), ('clean_tremolo', 7), ('jazz', 41),
    ('country', 31), ('funk', 56), ('blues_crunch', 29), ('crunch', 1), ('classic_rock', 18),
    ('hard_rock', 19), ('fuzz', 6), ('grunge_alt', 20), ('metal', 4), ('lead_solo', 2), ('none', None)]

SCHEMA = """
CREATE TABLE IF NOT EXISTS song_suggestions (
    song_id  int not null primary key references songs(id) on delete cascade,
    category text not null,
    known    int not null,
    genres   text,
    model    text,
    created  text default (datetime('now')),
    rejected int not null default 0);
CREATE TABLE IF NOT EXISTS sound_presets (
    category text not null primary key,
    slot     int check(slot between 1 and 100),
    level_db real default null);
CREATE TABLE IF NOT EXISTS artist_genres (
    artist   text not null primary key,
    genres   text not null,
    fetched  text default (datetime('now')));
"""


def setup(conn):
    conn.executescript(SCHEMA)
    conn.executemany("INSERT OR IGNORE INTO sound_presets (category, slot) VALUES (?, ?)", CATEGORIES)
    conn.commit()


def sound_presets(conn):
    order = [c for c, _ in CATEGORIES]
    rows = {c: (slot, level) for c, slot, level in conn.execute("SELECT category, slot, level_db FROM sound_presets")}
    return [{'category': c, 'slot': rows.get(c, (None, None))[0], 'level': rows.get(c, (None, None))[1]} for c in order]


def suggestion(conn, song_id):
    '''{category, known, rejected, genres, slot} or None.'''
    row = conn.execute("""SELECT s.category, s.known, s.rejected, s.genres, p.slot, p.level_db FROM song_suggestions s
                          LEFT JOIN sound_presets p ON p.category = s.category WHERE s.song_id = ?""",
                       (song_id,)).fetchone()
    if row is None:
        return None
    return {'category': row[0], 'known': bool(row[1]), 'rejected': bool(row[2]),
            'genres': json.loads(row[3] or '[]'), 'slot': row[4], 'level': row[5]}


def applied(conn, song_id):
    '''(slot, level_db) a suggestion puts at the song's start, or None (unknown, guessed,
    rejected, "none"). level_db None: the slot as stored.'''
    s = suggestion(conn, song_id)
    if s and s['known'] and not s['rejected'] and s['slot']:
        return s['slot'], s['level']
    return None


def applied_slot(conn, song_id):
    hit = applied(conn, song_id)
    return hit[0] if hit else None


# --- MusicBrainz --------------------------------------------------------------------

_last_mb = [0.0]


def _mb(path, **params):
    wait = _last_mb[0] + MB_INTERVAL - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    url = MB_URL + path + '?' + urllib.parse.urlencode(dict(params, fmt='json'))
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': MB_AGENT}), timeout=20) as r:
            return json.loads(r.read())
    finally:
        _last_mb[0] = time.monotonic()


def artist_genres(conn, artist):
    '''The artist's top genres from MusicBrainz, cached; [] if not found.'''
    row = conn.execute("SELECT genres FROM artist_genres WHERE artist = ?", (artist,)).fetchone()
    if row:
        return json.loads(row[0])
    genres = []
    try:
        hits = _mb('artist', query='artist:"{}"'.format(artist.replace('"', ' ')), limit=1).get('artists', [])
        if hits and int(hits[0].get('score', 0)) >= MB_MIN_SCORE:
            found = _mb('artist/' + hits[0]['id'], inc='genres').get('genres', [])
            genres = [g['name'] for g in sorted(found, key=lambda g: -g.get('count', 0))[:5]]
    except (OSError, ValueError) as e:
        log.warning('MusicBrainz: %s: %s', artist, e)
        return []                   # not cached: tried again next run
    conn.execute("INSERT OR REPLACE INTO artist_genres (artist, genres) VALUES (?, ?)", (artist, json.dumps(genres)))
    conn.commit()
    return genres


# --- the model ------------------------------------------------------------------------

def ask(title, artist, genres, url=OLLAMA_URL, model=MODEL):
    '''(category, known) for a song.'''
    cats = [c for c, _ in CATEGORIES]
    prompt = ('Song: "{}" by {}. Artist genres (MusicBrainz): {}.\n'
              'Which guitar sound fits playing along to the start of this song on a modelling amp? '
              'Answer with one category: {} ("none": no guitar part worth an amp sound). '
              'Set known to true only if you actually know this song and how its guitar sounds; '
              'if you are guessing from the artist or genres, set known to false.').format(
                  title, artist, ', '.join(genres) or 'unknown', ', '.join(cats))
    body = {'model': model, 'stream': False, 'think': False, 'options': {'temperature': 0},
            'format': {'type': 'object', 'required': ['category', 'known'],
                       'properties': {'category': {'type': 'string', 'enum': cats}, 'known': {'type': 'boolean'}}},
            'messages': [{'role': 'user', 'content': prompt}]}
    req = urllib.request.Request(url + '/api/chat', data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json', 'X-Ollama-Client': 'chordsplay-suggest'})
    with urllib.request.urlopen(req, timeout=600) as r:
        answer = json.loads(json.loads(r.read())['message']['content'])
    if answer.get('category') not in cats:
        raise ValueError('unknown category {!r}'.format(answer.get('category')))
    return answer['category'], bool(answer.get('known'))


def run(conn, limit=None, redo=False, song_ids=None, asker=ask, genres_of=artist_genres):
    '''Suggest for songs with chords that have none yet. Returns the number done.'''
    setup(conn)
    sql = "SELECT id, title, artist FROM songs s WHERE lyrics_chords IS NOT NULL"
    if song_ids:
        sql += " AND id IN ({})".format(','.join(str(int(i)) for i in song_ids))
    elif not redo:
        sql += " AND NOT EXISTS (SELECT 1 FROM song_suggestions g WHERE g.song_id = s.id)"
    rows = conn.execute(sql + " ORDER BY artist, title").fetchall()[:limit]
    done = 0
    for sid, title, artist in rows:
        genres = genres_of(conn, artist or '')
        try:
            category, known = asker(title, artist, genres)
        except (OSError, ValueError, KeyError) as e:
            log.warning('suggest: %s - %s: %s', artist, title, e)
            continue
        # a new suggestion keeps an earlier "rejected"
        conn.execute("""INSERT INTO song_suggestions (song_id, category, known, genres, model) VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(song_id) DO UPDATE SET category = excluded.category, known = excluded.known,
                        genres = excluded.genres, model = excluded.model, created = datetime('now')""",
                     (sid, category, int(known), json.dumps(genres), MODEL))
        conn.commit()
        done += 1
        log.info('suggest: %s - %s: %s%s', artist, title, category, '' if known else ' (guess)')
    return done


def main(argv=None):
    import ChordsPlay.db
    from web import app
    parser = argparse.ArgumentParser(description='Suggest amp sounds for songs (see the module docstring).')
    parser.add_argument('--db', default=app.DB_PATH)
    parser.add_argument('--limit', type=int)
    parser.add_argument('--redo', action='store_true', help='ask again for songs that have a suggestion')
    parser.add_argument('--song', type=int, action='append', help='only this song (repeatable)')
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')
    db = ChordsPlay.db.Db(args.db, app.DEFAULT_USER)
    try:
        print('suggested:', run(db.conn, args.limit, args.redo, args.song))
    finally:
        db.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
