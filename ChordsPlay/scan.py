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
import ChordsPlay.sync
import logging

log = logging.getLogger(__name__)

MAX_WRITE_RETRIES = 6  # 1, 2, 4, 8, 16, 32s ≈ 1 minute of retrying
COMMIT_INTERVAL = 0.2  # s; longest the walk keeps the write lock (see run())

def _is_lock_error(exc):
    return isinstance(exc, sqlite3.OperationalError) and 'locked' in str(exc).lower()


class Scan(threading.Thread):
    '''
    Scan song lists (directory and files) and add songs to the database
    Since sqlite3 does not support connection sharing, a separate connection is used
    '''

    def __init__(self, connector, user, toFile=None, sync_url=None, sync_token=None):
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
        self.sync = None
        self.sync_url = sync_url
        self.sync_token = sync_token
        self.aborted = False

    def _sync(self, method):
        '''Call a Sync method guarded - sync must never abort a scan.'''
        if self.sync is None:
            return
        try:
            getattr(self.sync, method)()
        except Exception:
            log.warning("sync %s failed (continuing)", method, exc_info=True)

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

            # Pull central chord data first: songs already fetched on another
            # installation arrive with chord_update set and drop out of
            # uncheckedSongs() below, so they are never re-fetched from UG.
            if self.sync_url:
                self.sync = ChordsPlay.sync.Sync(self.db, self.sync_url, self.sync_token)
                log.info("central sync enabled: {}".format(self.sync_url))
                self._sync('pull')

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
                    last_commit = time.monotonic()
                    for path, _, files in os.walk(song_list):
                        rel_path = os.path.relpath(path, song_list)
                        for file in files:
                            rel_file = os.path.join(rel_path, file)
                            self._safe_db(self.db.addListSong, song_list, rel_file)
                            # The open transaction holds SQLite's write lock while the
                            # next files' tags are read (slow over NFS). Commit often, so
                            # writes from the GUI (tags, guitar-tone cues) wait at most
                            # COMMIT_INTERVAL instead of seconds.
                            if time.monotonic() - last_commit >= COMMIT_INTERVAL:
                                self._safe_db(self.db.commit)
                                last_commit = time.monotonic()
                            if self.aborted:
                                return
                            if self.quit.is_set():
                                self._safe_db(self.db.commit)
                                return
                    self._safe_db(self.db.commit)
                else:
                    log.info('ignoring song list "{}"'.format(song_list))

            checked = 0
            log.debug("unchecked songs")
            for rowid, title, artist in self.db.uncheckedSongs():
                if self.aborted:
                    return
                log.debug("check {} by {}".format(title, artist))
                if self.updateLyrics(rowid, title, artist) is None:
                    return
                checked += 1
                if self.sync and checked % 50 == 0:
                    self._sync('push')   # publish progress so others dedup

            log.debug("recheck songs")
            for rowid, title, artist in self.db.recheckSongs():
                if self.aborted:
                    return
                log.debug("recheck {} by {}".format(title, artist))
                if self.updateLyrics(rowid, title, artist) is None:
                    return
                checked += 1
                if self.sync and checked % 50 == 0:
                    self._sync('push')

        finally:
            self._sync('push')   # push anything fetched this run to central
            log.info("Finished (aborted={})".format(self.aborted))
            self.db.conn.close()
