'''
Created on 19.02.2016

@author: joba1
'''

import threading
import sqlite3
import os
import ChordsPlay.db
import ChordsPlay.chords
import logging

log = logging.getLogger(__name__)

MAX_LOCK_FAILURES = 5

def _is_lock_error(exc):
    return isinstance(exc, sqlite3.OperationalError) and 'locked' in str(exc).lower()


class Scan(threading.Thread):
    '''
    Scan song lists (directory and files) and add songs to the database
    Since sqlite3 does not support connection sharing, a separate connection is used
    '''

    def __init__(self, connector, user, toFile=None, ug_user=None, ug_pass=None):
        '''
        Constructor
        '''
        threading.Thread.__init__(self, name='scan')
        self.quit = threading.Event()
        self.connector = connector
        self.user = user
        self.db = None
        self.fileName = toFile
        self.chords = ChordsPlay.chords.Chords()
        if ug_user and ug_pass:
            self.chords.login(ug_user, ug_pass)
        self.lock_failures = 0

    def _aborted(self):
        return self.lock_failures >= MAX_LOCK_FAILURES

    def updateLyrics(self, rowid, title, artist, toFile=None):
        '''
        get and update lyrics
        return True on success
        return False on errors
        return None if challenge detected or scan aborted
        '''
        ch = self.chords
        try:
            status = ch.request(title, artist, toFile)
        except Exception:
            log.error("fetching lyrics failed for {} by {}".format(title, artist))
            import traceback
            traceback.print_exc()
            return False

        if status is None or status == False:
            return status

        if toFile != None:
            return True

        try:
            self.db.updateLyrics(rowid, ch.lyrics, ch.capo, ch.url)
            if ch.lyrics != None:
                self.db.updateChords(rowid, ch.chords)
            self.db.commit()
        except Exception as e:
            try:
                self.db.rollback()
            except Exception:
                pass
            if _is_lock_error(e):
                self.lock_failures += 1
                log.error("DB locked ({}/{}) while saving {} by {}".format(
                    self.lock_failures, MAX_LOCK_FAILURES, title, artist))
            else:
                log.error("updating lyrics failed for {} by {}: {}".format(title, artist, e))
            return False

        log.info("saved lyrics for {} by {}".format(title, artist))
        self.lock_failures = 0
        return True
        
    def _safe_db(self, func, *args, **kwargs):
        '''Call a db method, catching lock errors. Returns True on success.'''
        try:
            func(*args, **kwargs)
            self.lock_failures = 0
            return True
        except Exception as e:
            try:
                self.db.rollback()
            except Exception:
                pass
            if _is_lock_error(e):
                self.lock_failures += 1
                log.error("DB locked ({}/{}) in {}".format(
                    self.lock_failures, MAX_LOCK_FAILURES, func.__name__))
            else:
                log.error("DB error in {}: {}".format(func.__name__, e))
            return False

    def run(self):
        log.info("Starting")
        self.db = ChordsPlay.db.Db(self.connector, self.user)
        try:
            log.debug("Connected")
            if self.quit.is_set():
                return

            if self.fileName != None:
                log.debug("Test song")
                self.updateLyrics(None, "Pride And Joy", "Stevie Ray Vaughan", toFile=self.fileName)

            song_lists = self.db.getSongLists()
            log.debug("{} song lists".format(len(song_lists)))
            if self.quit.is_set():
                return

            for song_list, in song_lists:
                if self._aborted():
                    log.error("aborting scan: DB locked too many times in a row")
                    return
                log.debug('song list "{}"'.format(song_list))
                if song_list == '':
                    pass

                elif os.path.isfile(song_list):
                    log.debug('add single file "{}"'.format(song_list))
                    self._safe_db(self.db.addListSong, '', song_list)
                    self._safe_db(self.db.commit)
                    if self.quit.is_set():
                        return

                elif os.path.isdir(song_list):
                    count = 0
                    for path, _, files in os.walk(song_list):
                        rel_path = os.path.relpath(path, song_list)
                        for file in files:
                            rel_file = os.path.join(rel_path, file)
                            self._safe_db(self.db.addListSong, song_list, rel_file)
                            count += 1
                            if count % 100 == 0:
                                self._safe_db(self.db.commit)
                            if self._aborted():
                                log.error("aborting scan: DB locked too many times in a row")
                                return
                            if self.quit.is_set():
                                self._safe_db(self.db.commit)
                                return
                    self._safe_db(self.db.commit)
                else:
                    log.info('ignoring song list "{}"'.format(song_list))

            log.debug("unchecked songs")
            for rowid, title, artist in self.db.uncheckedSongs():
                if self._aborted():
                    log.error("aborting scan: DB locked too many times in a row")
                    return
                log.debug("check {} by {}".format(title, artist))
                if self.updateLyrics(rowid, title, artist) is None:
                    return

            log.debug("recheck songs")
            for rowid, title, artist in self.db.recheckSongs():
                if self._aborted():
                    log.error("aborting scan: DB locked too many times in a row")
                    return
                log.debug("recheck {} by {}".format(title, artist))
                if self.updateLyrics(rowid, title, artist) is None:
                    return

        finally:
            log.info("Finished (lock failures: {})".format(self.lock_failures))
            self.db.conn.close()
