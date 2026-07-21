'''
Created on 19.02.2016

@author: joba1
'''

import threading
import sqlite3
import time
import os
import ChordsPlay.db
import ChordsPlay.chords
import logging

log = logging.getLogger(__name__)

MAX_WRITE_RETRIES = 6  # 1, 2, 4, 8, 16, 32s ≈ 1 minute of retrying

def _is_lock_error(exc):
    return isinstance(exc, sqlite3.OperationalError) and 'locked' in str(exc).lower()


class Scan(threading.Thread):
    '''
    Scan song lists (directory and files) and add songs to the database
    Since sqlite3 does not support connection sharing, a separate connection is used
    '''

    def __init__(self, connector, user, toFile=None):
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
        self.aborted = False

    def _save_with_retry(self, rowid, title, artist, lyrics, capo, url, chords):
        '''Write lyrics+chords to db with exponential backoff on lock errors.
        Returns True on success, False if non-lock error, sets self.aborted on
        too many lock retries.'''
        delay = 1
        for attempt in range(1, MAX_WRITE_RETRIES + 1):
            try:
                self.db.updateLyrics(rowid, lyrics, capo, url)
                if lyrics is not None:
                    self.db.updateChords(rowid, chords)
                self.db.commit()
                if attempt > 1:
                    log.info("saved {} by {} after {} attempt(s)".format(title, artist, attempt))
                return True
            except Exception as e:
                try:
                    self.db.rollback()
                except Exception:
                    pass
                if not _is_lock_error(e):
                    log.error("updating lyrics failed for {} by {}: {}".format(title, artist, e))
                    return False
                if attempt >= MAX_WRITE_RETRIES:
                    log.error("DB locked after {} retries for {} by {}; aborting scan".format(
                        attempt, title, artist))
                    self.aborted = True
                    return False
                log.warning("DB locked (attempt {}/{}), retrying {} by {} in {}s".format(
                    attempt, MAX_WRITE_RETRIES, title, artist, delay))
                if self.quit.wait(delay):
                    return False
                delay *= 2
        return False

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

        if not self._save_with_retry(rowid, title, artist, ch.lyrics, ch.capo, ch.url, ch.chords):
            return None if self.aborted else False

        log.info("saved lyrics for {} by {}".format(title, artist))
        return True
        
    def _safe_db(self, func, *args, **kwargs):
        '''Call a db method, retrying on lock errors with exponential backoff.'''
        delay = 1
        for attempt in range(1, MAX_WRITE_RETRIES + 1):
            try:
                func(*args, **kwargs)
                return True
            except Exception as e:
                try:
                    self.db.rollback()
                except Exception:
                    pass
                if not _is_lock_error(e):
                    log.error("DB error in {}: {}".format(func.__name__, e))
                    return False
                if attempt >= MAX_WRITE_RETRIES:
                    log.error("DB locked after {} retries in {}; aborting scan".format(
                        attempt, func.__name__))
                    self.aborted = True
                    return False
                log.warning("DB locked (attempt {}/{}) in {}, retrying in {}s".format(
                    attempt, MAX_WRITE_RETRIES, func.__name__, delay))
                if self.quit.wait(delay):
                    return False
                delay *= 2
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
                if self.aborted:
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
                            if self.aborted:
                                return
                            if self.quit.is_set():
                                self._safe_db(self.db.commit)
                                return
                    self._safe_db(self.db.commit)
                else:
                    log.info('ignoring song list "{}"'.format(song_list))

            log.debug("unchecked songs")
            for rowid, title, artist in self.db.uncheckedSongs():
                if self.aborted:
                    return
                log.debug("check {} by {}".format(title, artist))
                if self.updateLyrics(rowid, title, artist) is None:
                    return

            log.debug("recheck songs")
            for rowid, title, artist in self.db.recheckSongs():
                if self.aborted:
                    return
                log.debug("recheck {} by {}".format(title, artist))
                if self.updateLyrics(rowid, title, artist) is None:
                    return

        finally:
            log.info("Finished (aborted={})".format(self.aborted))
            self.db.conn.close()
