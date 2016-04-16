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
    '''

    def __init__(self, db, cmd, song_ids):
        self.db = db
        self.playlist = song_ids
        self.pos = len(self.playlist)
        self.onPlayCommand = cmd
        log.debug("{} = {}".format(self.pos, str(self.playlist)))

    def new(self, song_ids):
        newPos = len(song_ids)
        if newPos > 0:
            self.playlist = song_ids
            self.pos = newPos
            self.db.updatePlaylist(song_ids)            
        
    def shuffle(self):
        random.shuffle(self.playlist)
        
    def jump(self, pos):
        if pos >= 0 and pos < len(self.playlist):
            self.pos = pos
            self.onPlayCommand(self.pos, self.playlist[self.pos])
        
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
