'''
Created on 18.02.2016

@author: joba1
'''

import sqlite3
import ChordsPlay.song
import logging
# import os

log = logging.getLogger(__name__)

class Db(object):
    '''
    implement all database access for ChordsPlay
    '''

    def __init__(self, connector, user):
        '''
        Constructor.
        Connect to a database identified by connector
        If the database does not exist, create it
        '''
        log.debug('connect with "{}" as "{}"'.format(connector, user))
        self.conn = sqlite3.connect(connector)
        log.debug('Init db')
        self.conn.executescript("""
            create table if not exists song_lists ( 
                id            integer not null primary key autoincrement,
                name          text not null unique
            );
            create table if not exists users ( 
                id            integer not null primary key autoincrement,
                name          text not null unique,
                playlist_pos  int default null,
                filter_id     int default null references filters on delete restrict,
                foreign key   (id, playlist_pos) references playlist on delete restrict
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
            create table if not exists user_songs ( 
                user_id       int not null references users on delete restrict,
                song_id       int not null references songs on delete restrict,
                last_played   char(10),
                num_played    int not null default 0,
                primary key   (user_id, song_id)
            );
            create table if not exists song_chords ( 
                song_id       int not null references songs on delete cascade,
                chord_id      int not null references chords on delete restrict,
                primary key   (song_id, chord_id)
            );
            create table if not exists list_songs ( 
                song_lists_id int not null references song_lists on delete cascade,
                filename      text not null,
                song_id       int not null references songs on delete restrict,
                primary key   (song_lists_id, song_id)
            );
            create table if not exists chords ( 
                id            integer not null primary key autoincrement,
                name          char(15) not null unique,
                pattern       char(6)
            );
            create table if not exists chord_aliases ( 
                chord_id      int not null references chords on delete cascade,
                alias         char(5) not null unique
            );
            create table if not exists tags ( 
                id            integer not null primary key autoincrement,
                name          text not null unique,
                key           text
            );
            create table if not exists song_tags ( 
                user_id       int not null references users on delete cascade,
                song_id       int not null references songs on delete cascade,
                tag_id        int not null references tags on delete restrict,
                primary key   (user_id, song_id, tag_id)
            );
            create table if not exists playlists ( 
                user_id       int not null references users on delete cascade,
                position      int not null unique,
                song_id       int not null references songs on delete restrict,
                primary key   (user_id, position)
            );
            create table if not exists filters ( 
                id            integer not null primary key autoincrement,
                name          text not null unique,
                order_by      char(1) check(order_by in ('r', 'o', 'm', 'f'))
            );
            create table if not exists filter_patterns ( 
                filter_id     int not null references filters on delete cascade,
                type          char(1) not null check (type in ('a', 't', 'A')),
                pattern       text not null,
                unique        (filter_id, type, pattern)
            );
            create table if not exists filter_song_lists ( 
                filter_id     int not null references filters on delete cascade,
                song_list_id  int not null references song_lists on delete restrict,
                primary key   (filter_id, song_list_id)
            );
            create table if not exists filter_chords ( 
                filter_id     int not null references filters on delete cascade,
                chord_id      int not null references chords on delete restrict,
                type          char(1) not null check (type in ('r', 'o', 'x')),
                primary key   (filter_id, chord_id)
            );
            create table if not exists filter_tags ( 
                filter_id     int not null references filters on delete cascade,
                tag_id        int not null references tags on delete restrict,
                type          char(1) not null check (type in ('r', 'x')),
                primary key   (filter_id, tag_id)
            );
        """)
        try:
            self.conn.execute("insert into users (name) values (?)", (user,))
            log.info("created user {}".format(user))
        except sqlite3.IntegrityError:
            log.debug("found user {}".format(user))
            pass
        try:
            self.conn.execute("insert into song_lists (name) values (?)", ('',))
            log.info("db initialized")
        except sqlite3.IntegrityError:
            log.debug("db opened")
            pass
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
        # filename = os.path.join(song_list, song_file)
        filename = song_list + song_file
        # log.debug('{} + {} = {}'.format(song_list, song_file, filename))
        song = ChordsPlay.song.Song(filename)
        if song.title == None:
            log.debug("file without title ignored: {}".format(filename))
            return
            
        try:
            self.conn.execute('INSERT INTO songs (title, artist, album) VALUES (?, ?, ?)', song.tags())
            self.conn.execute('INSERT INTO list_songs (song_lists_id, filename, song_id) SELECT id, ?, last_insert_rowid() FROM song_lists WHERE name = ?', (song_file, song_list))
            self.conn.commit()
            log.info("added song {}".format(','.join(song.tags())))
        except sqlite3.IntegrityError:
            log.debug("duplicate song {}".format(','.join(song.tags())))
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

    def getBigPlaylist(self):
        '''
        return all songs with lyrics and filename
        '''
        cur = self.conn.cursor()
        cur.execute("""
            SELECT title, artist, album, lyrics_chords, filename, name
            FROM songs
            INNER JOIN list_songs ON songs.id = list_songs.song_id
            INNER JOIN song_lists ON list_songs.song_lists_id = song_lists.id
            WHERE lyrics_chords IS NOT NULL
            AND (capo IS NULL OR capo = 0)
            ORDER BY artist, album, songs.id
        """)
        ret = list(cur)
        cur.close()
        return ret
