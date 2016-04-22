'''
Created on 06.03.2016

@author: joba1
'''

import vlc
import logging

log = logging.getLogger(__name__)

class Play(object):
    '''
    play songs
    TODO: implement playlist class with next/prev/shuffle setting gui.text and player.song
    '''

    def __init__(self, widget, db):
        '''
        init player (currently a python wrapper of libvlc)
        '''
        # VLC player controls
        self.instance = vlc.Instance()
        
        self.player = self.instance.media_player_new()

        self.tk = widget        
        self.event_manager = self.player.event_manager()
        self.event_manager.event_attach(vlc.EventType.MediaPlayerEndReached, self.onEnd)

        self.url = None
        self.song_id = None        
        self.db = db
        
    @vlc.callbackmethod
    def onEnd(self, event):
        self.tk.after_idle(self.restart)

    def play(self, url, song_id):
        log.debug('play {}: {}'.format(song_id, url))
        current_media = self.instance.media_new(url)
        if current_media != None:
            log.info('play: {}'.format(current_media.get_mrl()))   
            self.player.stop()
            self.player.set_media(current_media)
            rc = self.player.play()
            if rc != 0:
                log.warn("play failed: {} = {}".format(rc, vlc.libvlc_errmsg()))
            else:
                self.url = url
                self.song_id = song_id
        else:
            log.warn("play failed: No valid media {}".format(url))
            
    def pause(self):
        if self.player.get_state() == vlc.State.Paused:
            log.debug('continue')
        else:
            log.debug('pause')
        self.player.pause()
    
    def restart(self):
        log.debug('restart')
        if self.song_id != None and self.db != None:
            log.debug('update played')
            self.db.songPlayed(self.song_id)
        
        self.play(self.url, self.song_id)
