'''
Created on 18.02.2016

@author: joba1
'''

import sqlite3
import ChordsPlay.song
import logging

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
                name          text not null
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
                artist        text,
                album         text,
                lyrics_chords text default null,
                lyrics_url    text default null,
                uses_capo     char(1) default null check(uses_capo in ('y', 'n')),
                chord_update  char(10) default null
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
                name          char(5) not null unique,
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
            self.conn.execute("insert into users (name) values (?)", [user])
        except sqlite3.IntegrityError:
            pass
        try:
            self.conn.execute("insert into song_lists (name) values (?)", [''])
        except sqlite3.IntegrityError:
            pass
        self.conn.commit()
        
    def getSongLists(self):
        '''
        return the current set of song_lists (paths or files) containing songs
        '''
        cur = self.conn.cursor()
        cur.execute('SELECT name FROM song_lists')
        return set(cur)
        
    def addSongLists(self, lists):
        '''
        add a set of song_lists (paths or files) containing songs
        '''
        try:
            self.conn.executemany('INSERT INTO song_lists (name) VALUES (?)', lists)
            self.conn.commit();
        except sqlite3.IntegrityError:
            self.conn.rollback();
        
    def addListSong(self, song_list, song_file):
        '''
        add a song and add this to list_songs
        '''
        song = ChordsPlay.song.Song(song_file)
        try:
            self.conn.execute('INSERT INTO songs (title, artist, album) VALUES (?, ?, ?)', song.tags())
            self.conn.execute('INSERT INTO list_songs (song_lists_id, filename, song_id) SELECT id, ?, last_insert_rowid() FROM song_lists WHERE name = ?', song_file, song_list)
            self.conn.commit();
        except sqlite3.IntegrityError:
            self.conn.rollback();

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
    
    def updateLyrics(self, rowid, lyrics, url):
        if lyrics == None:
            log.warn("no lyrics at {}".format(url))
            self.conn.execute('UPDATE songs SET chord_update = date(now) WHERE rowid = ?', rowid)
        else:
            log.info("save lyrics of {}".format(url))
            self.conn.execute('UPDATE songs SET chord_update = date(now), lyrics_url = ?, lyrics_chords = ? WHERE rowid = ?', url, lyrics, rowid)
