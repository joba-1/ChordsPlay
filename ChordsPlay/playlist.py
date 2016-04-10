'''
Created on 30.03.2016

@author: joba1
'''

import random
import logging

log = logging.getLogger(__name__)

class Playlist(object):
    '''
    manage playlist
    for now: just load all available songs
    '''

    def __init__(self, db, cmd):
        self.db = db
        self.playlist = db.getFirstPlaylist()
        self.pos = len(self.playlist)
        self.onPlayCommand = cmd
        log.debug("{} = {}".format(self.pos, str(self.playlist)))

    def new(self, patterns):
        newList = self.db.getPatternPlaylistIds(patterns)
        newPos = len(newList)
        if newPos > 0:
            self.playlist = newList
            self.pos = newPos            
        
    def shuffle(self):
        random.shuffle(self.playlist)
        
    def next(self):
        if len(self.playlist) == 0:
            return
        
        self.pos += 1
        if self.pos >= len(self.playlist):
            self.pos = 0
        
        self.onPlayCommand(self.pos, self.playlist[self.pos])
            
    def prev(self):
        if len(self.playlist) == 0:
            return
        
        self.pos -= 1
        if self.pos < 0:
            self.pos = len(self.playlist) - 1
        
        self.onPlayCommand(self.pos, self.playlist[self.pos])
