'''
Created on 19.02.2016

@author: joba1
'''

import os
import mutagen.easyid3 
import mutagen.id3
import mutagen.easymp4
import mutagen.mp4
import logging

log = logging.getLogger(__name__)

class Song(object):
    '''
    Info about a song (path, title, artist, album, chords, lyrics, ...)
    '''

    def __init__(self, filename = None):
        '''
        Constructor
        * check if file is a song
        * get id3 tags for title, artist and album
        * set default values for other attributes
        '''
        self.filename = filename
        self.title = None
        self.artist = None
        self.album = None
        self.lyrics_chords = None
        self.uses_capo = None
        self.chord_update = None
        
        if self.filename == None:
            return None
        
        if os.path.isfile(self.filename) == False:
            return None
        
        try:
            id3 = mutagen.easyid3.EasyID3(self.filename)
            self.title = id3['title'][0]
            self.artist = id3['artist'][0]
            self.album = id3['album'][0]
        
            log.info('id3 file "{}" is title "{}" by "{}" on "{}"'.format(self.filename, self.title, self.artist, self.album))
            return
        except mutagen.id3.ID3NoHeaderError:
            pass

        try:
            mp4 = mutagen.easymp4.EasyMP4(self.filename)
            self.title = mp4['title'][0]
            self.artist = mp4['artist'][0]
            self.album = mp4['album'][0]
            
            log.info('mp4 file "{}" is title "{}" by "{}" on "{}"'.format(self.filename, self.title, self.artist, self.album))
        except Exception:
            log.debug('format of "{}" not supported'.format(self.filename))
        
    def tags(self):
        return (self.title, self.artist, self.album)
    