'''
Client side of central chord-data sync.

Talks to the ChordsPlay sync service over HTTP and keeps the local SQLite DB's
shared tables (songs / chords / song_chords / song_tags) in step with central,
matched by the natural key (title, artist) - never by local file path. Local
path tables (song_lists / list_songs) are never touched.

Enabled only when sync_url is configured; otherwise the app runs stand-alone.
'''
from curl_cffi import requests as curl_requests
import logging

log = logging.getLogger(__name__)


class Sync(object):
    '''Bidirectional pull/push against the central sync service.'''

    def __init__(self, db, url, token=None, connect_timeout=5, read_timeout=60):
        self.db = db
        self.url = url.rstrip('/')
        self.token = token
        # split timeout: a short connect timeout means a sleeping/unreachable
        # host gives up in seconds instead of hanging; the longer read timeout
        # still allows a big initial pull to complete
        self.timeout = (connect_timeout, read_timeout)
        self.session = curl_requests.Session()

    def _headers(self):
        h = {"Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = "Bearer " + self.token
        return h

    def pull(self):
        '''Pull songs changed on central since our watermark and upsert them
        locally (last-write-wins). Returns the number of songs received.'''
        since = self.db.getSyncState('pull_since') or ''
        r = self.session.get(self.url + '/songs', params={'since': since},
                             headers=self._headers(), timeout=self.timeout)
        r.raise_for_status()
        songs = r.json().get('songs', [])
        applied = 0
        max_mod = since
        for s in songs:
            if self.db.upsertRemoteSong(s):
                applied += 1
            mod = s.get('modified')
            if mod and mod > max_mod:
                max_mod = mod
        self.db.commit()
        if max_mod:
            self.db.setSyncState('pull_since', max_mod)
        log.info("sync pull: %d received, %d applied", len(songs), applied)
        return len(songs)

    def push(self, batch=500):
        '''Push locally changed (dirty) songs to central and clear their dirty
        flag on success. Returns the number of songs pushed.'''
        total = 0
        while True:
            dirty = self.db.getDirtySongs(limit=batch)
            if not dirty:
                break
            r = self.session.post(self.url + '/songs', json={'songs': dirty},
                                  headers=self._headers(), timeout=self.timeout)
            r.raise_for_status()
            self.db.clearDirty([(s['title'], s['artist']) for s in dirty])
            total += len(dirty)
            if len(dirty) < batch:
                break
        if total:
            log.info("sync push: %d sent", total)
        return total

    def sync(self):
        '''Full bidirectional sync (pull then push). Never raises - a sync
        failure must not abort scanning/playback; it just logs and is retried
        next time.'''
        try:
            self.pull()
            self.push()
            return True
        except Exception:
            log.warning("sync failed (continuing offline)", exc_info=True)
            return False

    def seed(self):
        '''Mark all already-checked local songs dirty and push them - used to
        populate an empty central store from the most complete installation.'''
        self.db.seedModified()
        return self.push()
