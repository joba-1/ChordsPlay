'''
Created on 19.02.2016

@author: joba1
'''

import threading
import os
import ChordsPlay.db
import ChordsPlay.chords
import logging

log = logging.getLogger(__name__)

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
        log.info("Starting")
        db = ChordsPlay.db.Db(self.connector, self.user)
        try:
            log.debug("Connected")
            if self.quit.is_set(): 
                return
            
            song_lists = db.getSongLists()
            log.debug("{} song lists".format(len(song_lists)))
            if self.quit.is_set(): 
                return
                
            for song_list, in song_lists:
                log.debug('song list "{}"'.format(song_list))
                if song_list == '':
                    pass
                
                elif os.path.isfile(song_list):
                    log.debug('add single file "{}"'.format(song_list))
                    db.addListSong('', song_list)
                    if self.quit.is_set(): 
                        return

                elif os.path.isdir(song_list):
                    for path, _, files in os.walk(song_list):
                        for file in files:
                            log.debug('add file "{}/{}"'.format(path, file))
                            db.addListSong(song_list, os.path.join(path, file))
                            if self.quit.is_set(): 
                                return

            log.debug("unchecked songs")
            for rowid, title, artist in db.uncheckedSongs():
                chords = ChordsPlay.chords.Chords().request(title, artist)
                # todo capo, chord list
                db.updateLyrics(rowid, chords.lyrics, chords.url)
            
            log.debug("recheck songs")
            for rowid, title, artist in db.recheckSongs():
                chords = ChordsPlay.chords.Chords().request(title, artist)
                # todo capo, chord list
                db.updateLyrics(rowid, chords.lyrics, chords.url)
                
        finally:    
            log.info("Finished")
            db.conn.close()
