'''
Created on 18.02.2016

@author: joba1
'''

import sqlite3
import ChordsPlay.song
import logging
import re
import os

log = logging.getLogger(__name__)

def regexp(expr, item):
    try:
        return re.search(expr, item) is not None
    except Exception:
        return False

class Db(object):
    '''
    implement all database access for ChordsPlay
    REGEXP example:
    cursor.execute('SELECT bar FROM foo WHERE bar REGEXP ?', ['"test"'])
    '''

    def __init__(self, connector, user):
        '''
        Constructor.
        Connect to a database identified by connector
        If the database does not exist, create it
        '''
        self.user = user
        log.debug('connect with "{}" as "{}"'.format(connector, user))
        self.conn = sqlite3.connect(connector)
        log.debug('Init db')
        self.conn.create_function("REGEXP", 2, regexp)
        self.conn.executescript("""
            create table if not exists song_lists ( 
                id            integer not null primary key autoincrement,
                name          text not null unique
            );
            create table if not exists users ( 
                id            integer not null primary key autoincrement,
                name          text not null unique,
                playlist_pos  int default null,
                filter_id     int default null references filters(id) on delete restrict,
                foreign key   (id, playlist_pos) references playlists(user_id, position) on delete restrict
            );
            create table if not exists songs ( 
                id            integer not null primary key autoincrement,
                title         text not null,
                artist        text not null default '',
                album         text,
                lyrics_chords text default null,
                lyrics_url    text default null,
                capo          int default null check(capo is null or (capo >= 0 and capo <= 11)),
                chord_update  char(10) default null,
                unique        (title, artist)
            );
            create index if not exists songs_ix1 on songs (
                lyrics_chords, id, title, artist, album, capo 
            );
            create table if not exists user_songs ( 
                user_id      int not null references users(id) on delete restrict,
                song_id      int not null references songs(id) on delete restrict,
                played       char(19) not null
            );
            create index if not exists user_songs_ix1 on user_songs (
                user_id, song_id, played
            );
            create table if not exists song_chords ( 
                song_id       int not null references songs(id) on delete cascade,
                chord_id      int not null references chords(id) on delete restrict,
                primary key   (song_id, chord_id)
            );
            create table if not exists list_songs ( 
                song_lists_id int not null references song_lists(id) on delete cascade,
                filename      text not null,
                song_id       int not null references songs(id) on delete restrict,
                primary key   (song_lists_id, song_id)
            );
            create table if not exists chords ( 
                id            integer not null primary key autoincrement,
                name          char(15) not null unique,
                pattern       char(6)
            );
            create index if not exists chords_ix1 on chords (
                name, id
            );
            drop table if exists chord_aliases;
            create table if not exists tags ( 
                id            integer not null primary key autoincrement,
                name          text not null unique,
                key           text
            );
            create table if not exists song_tags ( 
                user_id       int not null references users(id) on delete cascade,
                song_id       int not null references songs(id) on delete cascade,
                tag_id        int not null references tags(id) on delete restrict,
                primary key   (user_id, song_id, tag_id)
            );
            create table if not exists playlists ( 
                user_id       int not null references users(id) on delete cascade,
                position      int not null,
                song_id       int not null references songs(id) on delete restrict,
                primary key   (user_id, position)
            );
            create table if not exists user_filters ( 
                user_id       int not null references users(id) on delete cascade,
                name          text not null,
                json_dict     text not null,
                primary key   (user_id, name)
            );
            drop table if exists filters;
            drop table if exists filter_patterns;
            drop table if exists filter_song_lists;
            drop table if exists filter_chords;
            drop table if exists filter_tags;
        """)
        try:
            self.conn.execute("insert into users (name) values (?)", (user,))
            log.info("created user {}".format(user))
        except sqlite3.IntegrityError:
            log.debug("found user {}".format(user))
        
        try:
            tags = [
                (1, 'Favorit', 'f'),
                (2, 'Schwer', 's'),
                (3, 'Leicht', 'l'),
                (4, 'Unfertig', 'u'),
                (5, 'Capo', 'c')
            ]
            self.conn.executemany("insert into tags (id, name, key) values (?, ?, ?)", tags)
            log.info("tags initialized")
        except sqlite3.IntegrityError:
            log.debug("found tags")

        try:
            self.conn.execute("insert into song_lists (name) values (?)", ('',))
            log.info("db initialized")
        except sqlite3.IntegrityError:
            log.debug("db opened")
        
        self.conn.commit()
    
    def commit(self):
        self.conn.commit()
        
    def rollback(self):
        self.conn.rollback()
        
    def getSongLists(self):
        '''
        return the current set of song_lists (paths or files) containing songs
        '''
        cur = self.conn.cursor()
        cur.execute('SELECT name FROM song_lists')
        ret = set(cur)
        cur.close()
        return ret
        
    def addSongLists(self, lists):
        '''
        add a set of song_lists (paths or files) containing songs
        '''
        try:
            self.conn.executemany('INSERT INTO song_lists (name) VALUES (?)', lists)
            self.conn.commit()
            log.info("created song lists {}".format(repr(lists)))
        except sqlite3.IntegrityError:
            log.error("duplicate song list in {}".format(repr(lists)))
            self.conn.rollback()
        except sqlite3.Error:
            log.error("creating song lists failed: {}".format(repr(lists)))
            self.conn.rollback()
        
    def addListSong(self, song_list, song_file):
        '''
        add a song and add this to list_songs
        '''
        filename = os.path.join(song_list, song_file)
        # log.debug('{} + {} = {}'.format(song_list, song_file, filename))
        song = ChordsPlay.song.Song(filename)
        if song == None or song.title == None:
            # log.debug("file without title ignored: {}".format(filename))
            return
            
        try:
            self.conn.execute('INSERT INTO songs (title, artist, album) VALUES (?, ?, ?)', song.tags())
            self.conn.execute('INSERT INTO list_songs (song_lists_id, filename, song_id) SELECT id, ?, last_insert_rowid() FROM song_lists WHERE name = ?', (song_file, song_list))
            self.conn.commit()
            log.info("added song {}".format(','.join(song.tags())))
        except sqlite3.IntegrityError:
            # log.debug("duplicate song {}".format(','.join(song.tags())))
            self.conn.rollback()
        except sqlite3.InterfaceError as e:
            log.error("adding song {} failed:".format(','.join(song.tags())))
            self.conn.rollback()
            e.data = song.tags()
            raise

    def uncheckedSongs(self):
        stmt = '''
            SELECT 
                rowid, 
                title, 
                artist
            FROM 
                songs
            WHERE
                chord_update is NULL
        '''
        for row in self.conn.execute(stmt):
            yield row

    def recheckSongs(self):
        stmt = '''
            SELECT 
                rowid, 
                title, 
                artist
            FROM 
                songs
            WHERE
                date(chord_update) < date('now', '-1 month')
        '''
        for row in self.conn.execute(stmt):
            yield row
    
    def updateLyrics(self, rowid, lyrics, capo, url):
        try:
            if lyrics == None:
                log.warn("no lyrics at {}".format(url))
                self.conn.execute("UPDATE songs SET chord_update = date('now') WHERE rowid = ?", (rowid,))
            else:
                log.info("save lyrics of {}".format(url))
                self.conn.execute("UPDATE songs SET chord_update = date('now'), lyrics_url = ?, lyrics_chords = ?, capo = ? WHERE rowid = ?", (url, lyrics, capo, rowid))
        except:
            import traceback
            traceback.print_exc()
            raise

    def updateChords(self, rowid, chords):
        try:
            self.conn.execute("DELETE FROM song_chords WHERE song_id = ?", (rowid,))
          
            if chords != None:
                for chord in chords:
                    try:
                        self.conn.execute("INSERT INTO chords (name) VALUES (?)", (chord,))
                    except sqlite3.IntegrityError:
                        pass
                    
                    self.conn.execute('INSERT INTO song_chords (song_id, chord_id) SELECT ?, id from chords where name = ?', (rowid, chord))

        except:
            import traceback
            traceback.print_exc()
            raise
    
    def getPlayInfo(self, song_id):
        cur = self.conn.cursor()
        cur.execute("""
            SELECT s.title, s.artist, s.lyrics_chords, l.name, ls.filename
            FROM songs s
            INNER JOIN list_songs ls ON s.id = ls.song_id
            INNER JOIN song_lists l ON ls.song_lists_id = l.id
            WHERE s.id = ?
        """, (song_id,))
        ret = cur.fetchone()
        cur.close()
        return ret

    def getFirstPlaylist(self):
        '''
        return all id's of songs with lyrics and no capo
        '''
        cur = self.conn.cursor()
        cur.execute("""
            SELECT s.id, s.title, s.artist, s.album, 
                cast(coalesce(s.capo, 0) as text), 
                coalesce(group_concat(distinct c.name), ''),
                coalesce(group_concat(distinct t.key), ''),
                count(distinct played) as played,
                coalesce(max(played), 'never') as last
            FROM songs s
            INNER JOIN playlists l ON s.id = l.song_id
            LEFT OUTER JOIN users u ON u.id = l.user_id
            LEFT OUTER JOIN song_chords sc ON sc.song_id = s.id
            LEFT OUTER JOIN song_tags st ON st.user_id = u.id and st.song_id = s.id
            LEFT OUTER JOIN tags t ON t.id = st.tag_id
            LEFT OUTER JOIN chords c ON sc.chord_id = c.id
            LEFT OUTER JOIN user_songs us ON us.song_id = s.id AND us.user_id = u.id
            WHERE u.name = ?
            GROUP BY s.id, s.title, s.artist, s.album, cast(coalesce(s.capo, 0) as text)
            ORDER BY l.user_id, l.position
        """, (self.user,))
        ret = list(cur)
        if len(ret) == 0:
            cur.execute("""
                SELECT s.id, s.title, s.artist, s.album, 
                    cast(coalesce(s.capo, 0) as text), 
                    coalesce(group_concat(distinct c.name), ''),
                    coalesce(group_concat(distinct t.key), ''),
                    count(distinct played) as played,
                    coalesce(max(played), 'never') as last
                FROM songs s
                INNER JOIN list_songs ls ON s.id = ls.song_id
                INNER JOIN song_lists l ON ls.song_lists_id = l.id
                LEFT OUTER JOIN users u ON u.name = ?
                LEFT OUTER JOIN song_chords sc ON sc.song_id = s.id
                LEFT OUTER JOIN song_tags st ON st.user_id = u.id and st.song_id = s.id
                LEFT OUTER JOIN tags t ON t.id = st.tag_id
                LEFT OUTER JOIN chords c ON sc.chord_id = c.id
                LEFT OUTER JOIN user_songs us ON us.song_id = s.id AND us.user_id = u.id
                WHERE s.lyrics_chords IS NOT NULL
                AND (s.capo IS NULL OR s.capo = 0)
                GROUP BY s.id, s.title, s.artist, s.album, cast(coalesce(s.capo, 0) as text)
                ORDER BY s.artist, s.album, s.id
            """, (self.user,))
            ret = list(cur)
        log.debug('getFirstPlaylist: {} entries found'.format(len(ret)))
        cur.close()
        return ret

    def getPatternPlaylist(self, patterns):
        '''
        return all id's of songs with matching patterns
        '''
        havingClause = 'HAVING count(*) > 0\n'
        predicateClause = ''
        requiredClause = ''
        acceptedClause = ''
        predicateValues = [self.user]
        predicateSwitch = {
            "Titles":   "AND s.title  REGEXP ?\n",
            "Artists":  "AND s.artist REGEXP ?\n",
            "Albums":   "AND s.album  REGEXP ?\n",
            "Capos":    "AND cast(coalesce(s.capo, 0) as text) REGEXP ?\n",
            }
        tags = patterns.get("Tag Keys", '')
        required = patterns.get("Required Chords", '')
        if required != '':
            predicateValues.append(required)
            requiredClause = 'LEFT OUTER JOIN chords rc ON sc.chord_id = rc.id AND rc.name REGEXP ?\n'
        accepted = patterns.get("Accepted Chords", '')
        if accepted != '':
            if required != '':
                predicateValues.append("({})|({})".format(required, accepted))
            else:
                predicateValues.append(accepted)
            acceptedClause = ' AND ac.name REGEXP ?'
            
        for key in patterns:
            pattern = patterns[key]
            if pattern != '':
                predicate = predicateSwitch.get(key, None)
                if predicate != None:
                    predicateClause += predicate
                    if required == '' or key != "Accepted Chords":
                        predicateValues.append(pattern)
                    else:
                        predicateValues.append("({})|({})".format(required, pattern))

        
        if required != '':
            havingClause += "AND count(rc.name) = (SELECT count(*) FROM chords WHERE name REGEXP ?)\n"
            predicateValues.append(required)
        if accepted != '':
            havingClause += "AND count(*) = count(ac.name)\n"
        if tags != '':
            havingClause += "AND coalesce(group_concat(distinct t.key), '') REGEXP ?\n"
            predicateValues.append(tags)

        cur = self.conn.cursor()
        stmt = """
SELECT s.id, s.title, s.artist, s.album, 
    cast(coalesce(s.capo, 0) as text), 
    coalesce(group_concat(distinct ac.name), ''),
    coalesce(group_concat(distinct t.key), ''),
    count(distinct played) as played,
    coalesce(max(played), 'never') as last
FROM songs s
INNER JOIN list_songs ls ON s.id = ls.song_id
INNER JOIN song_lists l ON ls.song_lists_id = l.id
LEFT OUTER JOIN users u ON u.name = ?
LEFT OUTER JOIN song_chords sc ON sc.song_id = s.id
LEFT OUTER JOIN song_tags st ON st.user_id = u.id and st.song_id = s.id
LEFT OUTER JOIN tags t ON t.id = st.tag_id
{}LEFT OUTER JOIN chords ac ON sc.chord_id = ac.id{}
LEFT OUTER JOIN user_songs us ON us.song_id = s.id AND us.user_id = u.id
WHERE s.lyrics_chords IS NOT NULL
{}GROUP BY s.id, s.title, s.artist, s.album, cast(coalesce(s.capo, 0) as text)
{}ORDER BY s.artist, s.album, s.id""".format(requiredClause, acceptedClause, predicateClause, havingClause)
        log.debug('getPatternPlaylist: Statement {}'.format(stmt))
        log.debug('getPatternPlaylist: Parameters {}'.format(str(predicateValues)))
        cur.execute(stmt, predicateValues)
        ret = list(cur)
        log.debug('getPatternPlaylist: {} entries found'.format(len(ret)))
        cur.close()
        return ret
    
    def updatePlaylist(self, song_ids):
        cur = self.conn.cursor()
        cur.execute("SELECT id FROM users WHERE name = ?", (self.user,))
        user_id = cur.fetchone()
        cur.close()
        self.conn.execute("DELETE FROM playlists WHERE user_id = ?", user_id)
        rows = [(user_id[0], pos, song_id) for pos, song_id in enumerate(song_ids)]
        log.debug(str(rows))
        self.conn.executemany("INSERT INTO playlists (user_id, position, song_id) VALUES(?, ?, ?)", rows)
        self.conn.commit()

    def songPlayed(self, song_id):
        log.debug('songPlayed: {}'.format(song_id))
        cur = self.conn.cursor()
        cur.execute("SELECT id FROM users WHERE name = ?", (self.user,))
        user_id = cur.fetchone()
        cur.close()
        row = (user_id[0], song_id, 'now')
        self.conn.execute("INSERT INTO user_songs (user_id, song_id, played) VALUES(?, ?, datetime(?))", row)
        self.conn.commit()
    
    def numPlayed(self, song_id):
        cur = self.conn.cursor()
        cur.execute("""
            SELECT count(*) 
            FROM users u
            INNER JOIN user_songs s ON u.id = s.user_id
            WHERE u.name = ?
            AND s.song_id = ?""", (self.user, song_id))
        num = cur.fetchone()
        cur.close()
        return num[0]
    
    def lastPlayed(self, song_id):
        cur = self.conn.cursor()
        cur.execute("""
            SELECT max(played) 
            FROM users u
            INNER JOIN user_songs s ON u.id = s.user_id
            WHERE u.name = ?
            AND s.song_id = ?""", (self.user, song_id))
        last = cur.fetchone()
        cur.close()
        return last[0]
    
    def getUserFilters(self):
        cur = self.conn.cursor()
        cur.execute("""
            SELECT f.name
            FROM users u
            INNER JOIN user_filters f ON u.id = f.user_id
            WHERE u.name = ?""", (self.user,))
        rows = list(cur)
        cur.close()
        log.debug('getUserFilters: len = {}'.format(len(rows)))
        return rows
        
    def getUserFilter(self, name):
        cur = self.conn.cursor()
        cur.execute("""
            SELECT f.json_dict
            FROM users u
            INNER JOIN user_filters f ON u.id = f.user_id
            WHERE u.name = ?
            AND f.name = ?""", (self.user, name))
        row = cur.fetchone()
        cur.close()
        log.debug('getUserFilter: {} is {}'.format(name, row[0]))
        return row[0]
        
    def setUserFilter(self, name, jsonString):
        log.debug('setUserFilter: {} as {}'.format(name, jsonString))
        cur = self.conn.cursor()
        cur.execute("""
            SELECT u.id, f.name FROM users u
            LEFT OUTER JOIN user_filters f ON u.id = f.user_id AND f.name = ?
            WHERE u.name = ?""", (name, self.user))
        row = cur.fetchone()
        cur.close()
        parms = (jsonString, row[0], name)
        if row[1] == None:
            self.conn.execute("INSERT INTO user_filters (json_dict, user_id, name) VALUES(?, ?, ?)", parms)
        else:
            self.conn.execute("UPDATE user_filters set json_dict = ? WHERE user_id = ? and name = ?", parms)            
        self.conn.commit()
        
    def getUserTags(self, song_id):
        log.debug('getUserTags(song_id={})'.format(song_id)) 
        # TODO tags act strange (existing song tags not always displayed in tagging dialog, filter not finding all songs) 
        cur = self.conn.cursor()
        cur.execute("""
            SELECT 
                t.id, 
                coalesce(t.key, ''), 
                CASE WHEN s.song_id IS NULL THEN ' ' ELSE 'X' END,
                t.name 
            FROM tags t
            LEFT OUTER JOIN users u ON u.name = ?
            LEFT OUTER JOIN song_tags s ON s.tag_id = t.id AND s.user_id = u.id AND s.song_id = ?
            ORDER BY t.key, t.name""", 
            (self.user, song_id))
        rows = list(cur)
        cur.close()
        return rows
    
    def setUserTag(self, song_id, tag_id):
        log.debug('setUserTag {} for {}'.format(tag_id, song_id))
        cur = self.conn.cursor()
        cur.execute("SELECT id FROM users WHERE name = ?", (self.user,))
        user_id = cur.fetchone()
        cur.close()
        row = (user_id[0], song_id, tag_id)
        self.conn.execute("INSERT INTO song_tags (user_id, song_id, tag_id) VALUES(?, ?, ?)", row)
        self.conn.commit()
    
    def delUserTag(self, song_id, tag_id):
        log.debug('delUserTag {} for {}'.format(tag_id, song_id))
        cur = self.conn.cursor()
        cur.execute("SELECT id FROM users WHERE name = ?", (self.user,))
        user_id = cur.fetchone()
        cur.close()
        row = (user_id[0], song_id, tag_id)
        self.conn.execute("DELETE FROM song_tags WHERE user_id = ? AND song_id = ? AND tag_id = ?", row)
        self.conn.commit()

    def fixTabTags(self):
        log.info('fixTabTags')
        cur = self.conn.cursor()
        cur.execute("SELECT id, title, artist, lyrics_chords FROM songs WHERE lyrics_chords IS NOT NULL")
        rows = cur.fetchall()
        cur.close()
        for row in rows:
            log.info("Fix {} {}".format(row[2], row[1]))
            lyrics = row[3].replace('[tab]', '')
            lyrics = lyrics.replace('[/tab]', '')
            parms = (lyrics, row[0])
            self.conn.execute("UPDATE songs set lyrics_chords = ? WHERE id = ?", parms)            
        self.conn.commit()
