'''
Created on 19.02.2016

@author: joba1
'''

import threading
import os
import ChordsPlay.db
import ChordsPlay.chords

class Scan(threading.Thread):
    '''
    Scan song lists (directory and files) and add songs to the database
    Since sqlite3 does not support connection sharing, a separate connection is used
    '''

    def __init__(self, connector, user):
        '''
        Constructor
        '''
        threading.Thread.__init__(self, name='scan')
        self.quit = threading.Event()
        self.connector = connector
        self.user = user

    def run(self):
        db = ChordsPlay.db.Db(self.connector, self.user)
        try:
            if self.quit.is_set(): 
                return
            
            song_lists = db.getSongLists()
            if self.quit.is_set(): 
                return
                
            for song_list in song_lists:
                if song_list == '':
                    pass
                
                elif os.path.isfile(song_list):
                    db.addListSong('', song_list)
                    if self.quit.is_set(): 
                        return

                elif os.path.isdir(song_list):
                    for path, _, files in os.walk(song_list):
                        for file in files:
                            db.addListSong(song_list, os.path.join(path, file))
                            if self.quit.is_set(): 
                                return

            for rowid, title, artist in db.uncheckedSongs():
                chords = ChordsPlay.chords.Chords().request(title, artist)
                # todo capo, chord list
                db.updateLyrics(rowid, chords.lyrics, chords.url)
            
            for rowid, title, artist in db.recheckSongs():
                chords = ChordsPlay.chords.Chords().request(title, artist)
                # todo capo, chord list
                db.updateLyrics(rowid, chords.lyrics, chords.url)
                
        finally:    
            db.conn.close()
