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
        self.db = None

    def updateLyrics(self, rowid, title, artist):
        ch = ChordsPlay.chords.Chords()
        try:
            ch.request(title, artist)
        except Exception:
            log.error("fetching lyrics failed for {} by {}".format(title, artist))
            self.db.rollback()
            import traceback
            traceback.print_exc()
            return
        
        try:
            self.db.updateLyrics(rowid, ch.lyrics, ch.capo, ch.url)
        except Exception:
            log.error("updating lyrics failed for {} by {}".format(title, artist))
            self.db.rollback()
            return
        
        if ch.lyrics != None:
            try:
                self.db.updateChords(rowid, ch.chords)
            except Exception:
                log.error("updating chords failed for {} by {}".format(title, artist))
                self.db.rollback()
                return

        self.db.commit()
        
    def run(self):
        log.info("Starting")
        self.db = ChordsPlay.db.Db(self.connector, self.user)
        try:
            log.debug("Connected")
            if self.quit.is_set(): 
                return
            
            song_lists = self.db.getSongLists()
            log.debug("{} song lists".format(len(song_lists)))
            if self.quit.is_set(): 
                return
                
            for song_list, in song_lists:
                log.debug('song list "{}"'.format(song_list))
                if song_list == '':
                    pass
                
                elif os.path.isfile(song_list):
                    log.debug('add single file "{}"'.format(song_list))
                    self.db.addListSong('', song_list)
                    if self.quit.is_set(): 
                        return

                elif os.path.isdir(song_list):
                    for path, _, files in os.walk(song_list):
                        rel_path = os.path.relpath(path, song_list)
                        for file in files:
                            rel_file = os.path.join(rel_path, file)
                            # log.debug('add file "{}"'.format(rel_file))
                            self.db.addListSong(song_list, rel_file)
                            if self.quit.is_set(): 
                                return
                else:
                    log.info('ignoring song list "{}"'.format(song_list))

            log.debug("unchecked songs")
            for rowid, title, artist in self.db.uncheckedSongs():
                log.debug("check {} by {}".format(title, artist))
                self.updateLyrics(rowid, title, artist)
            
            log.debug("recheck songs")
            for rowid, title, artist in self.db.recheckSongs():
                log.debug("recheck {} by {}".format(title, artist))
                self.updateLyrics(rowid, title, artist)
                
        finally:    
            log.info("Finished")
            self.db.conn.close()
