'''
Created on 19.02.2016

@author: joba1
'''

import os
import mutagen.easyid3 

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
        
        id3 = mutagen.easyid3.EasyID3(self.filename)
        self.title = id3['title']
        self.artist = id3['artist']
        self.album = id3['album']
        # todo: what if file has no tags?
        # todo: what if file has no title tag?
        
    def tags(self):
        return (self.title, self.artist, self.album)
    