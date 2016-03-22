'''
Created on 06.03.2016

@author: joba1
'''

import vlc
import logging
import random

log = logging.getLogger(__name__)

class Play(object):
    '''
    play list of songs
    '''

    def __init__(self, root, playlist):
        '''
        init player (currently a python wrapper of libvlc)
        '''
        # VLC player controls
        self.instance = vlc.Instance()
        
        self.player = self.instance.media_player_new()

        self.root = root        
        self.event_manager = self.player.event_manager()
        self.event_manager.event_attach(vlc.EventType.MediaPlayerEndReached, self.onEnd)

        self.setList(playlist)

    @vlc.callbackmethod
    def onEnd(self, event):
        self.root.after_idle(self.restart)
        
    def setList(self, playlist):
        self.medialist = self.instance.media_list_new(playlist)
        self.medialistindex = 0
        try:
            current_media = self.medialist[self.medialistindex]
            if current_media != None:
                self.player.set_media(current_media)
                self.play()
        except IndexError:
            pass
        
        log.debug('len(medialist) = {}'.format(len(self.medialist)))
        for m in self.medialist:
            log.debug('media[] = {}'.format(m.get_mrl()))   
    
    def shuffle(self):
        log.debug('shuffle')
        random.shuffle(self.medialist)
        self.medialistindex = 0
        self.player.stop()
        try:
            current_media = self.medialist[self.medialistindex]
            if current_media != None:
                self.player.set_media(current_media)
                self.play()
        except IndexError:
            pass
    
    def play(self):
        log.debug('play')
        if self.player.get_media() != None:
            rc = self.player.play()
            if rc != 0:
                log.warn("play returned {}: {}".format(rc, vlc.libvlc_errmsg()))
        else:
            log.warn("No song to play")
            
    def pause(self):
        if self.player.get_state() == vlc.State.Paused:
            log.debug('continue')
        else:
            log.debug('pause')
        self.player.pause()
    
    def next(self):
        log.debug('next')
        self.player.stop()
        self.medialistindex += 1
        try:
            current_media = self.medialist[self.medialistindex]
            if current_media != None:
                self.player.set_media(current_media)
                self.play()
            else:
                self.medialistindex -= 1
        except IndexError:
            self.medialistindex -= 1
   
    def prev(self):
        log.debug('prev')
        self.player.stop()
        self.medialistindex -= 1
        try:
            current_media = self.medialist[self.medialistindex]
            if current_media != None:
                self.player.set_media(current_media)
                self.play()
            else:
                self.medialistindex += 1
        except IndexError:
            self.medialistindex += 1
    
    def restart(self):
        log.debug('restart')
        self.player.stop()
        self.play()
