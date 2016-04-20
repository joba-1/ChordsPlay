'''
Created on 30.03.2016

@author: joba1
'''

import logging

log = logging.getLogger(__name__)

class Playlist(object):
    '''
    manage playlist
    '''

    def __init__(self, db, cmd, mclist):
        self.db = db
        self.mclist = mclist
        self.pos = self.mclist.len()
        self.onPlayCommand = cmd
        log.debug("playlist len = {}".format(self.pos))

    def new(self, mclist):
        newPos = mclist.len()
        if newPos > 0:
            self.mclist = mclist
            self.pos = newPos
            self.db.updatePlaylist(self.mclist.get())            
        
    def jump(self, pos):
        if pos >= 0 and pos < self.mclist.len():
            self.pos = pos
            self.onPlayCommand(pos, self.mclist.get()[pos])
        
    def next(self):
        length = self.mclist.len()
        if length == 0:
            return
        
        self.pos += 1
        if self.pos >= length:
            self.pos = 0
        
        self.onPlayCommand(self.pos, self.mclist.get()[self.pos])
            
    def prev(self):
        length = self.mclist.len()
        if length == 0:
            return
        
        self.pos -= 1
        if self.pos < 0:
            self.pos = length - 1
        
        self.onPlayCommand(self.pos, self.mclist.get()[self.pos])
