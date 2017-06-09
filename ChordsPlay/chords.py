'''
Created on 21.02.2016

@author: joba1
'''

import requests
import re
import logging

log = logging.getLogger(__name__)

class Chords(object):
    '''
    handle lyrics and chords
    '''

    def __init__(self):
        '''
        Constructor
        '''
        self.lyrics = None
        self.url = None
        self.chords = None
        self.capo = None

    def request(self, title, artist, toFile=None):
        '''
        get lyrics and chords from the web and store them
        '''
        
        # generic request parameters
        url = "https://www.ultimate-guitar.com/search.php"
        params = {
            'view_state': 'advanced',
            'type':       300,
            'type2':      40000,
            'rating[]':   [5,4],
            'song_name':  title,
            'band_name':  artist
        }
        headers = {
            'user-agent': 'ChordsPlay/0.1'
        }
        
        # request list of lyrics with chords
        rq_chords = requests.get(url, params=params, headers=headers)
        log.debug("Url: {}".format(rq_chords.url))
        log.debug("Text: {}".format(rq_chords.text))
        if toFile != None:
            f = open(toFile + '_ChordsPlay_List.txt', 'w');
            f.write("List Url: {}\n".format(rq_chords.url))
            f.write(rq_chords.text)
            f.close()
        
        # extract url and ranking of lyrics with chords
        songlist = r'<a onclick[^>]*? href="([^"]*?)"[\t\n ]*class="song result-link.+?<span class="rating" title="([0-9.]+).+?</span>.+?<b class="ratdig">([0-9]+)</b>.+?<strong>(.)'
        rx_chords = re.compile(songlist, re.DOTALL)
        max_rating = 0
        max_ratings = 0
        best_link = None
        for match in rx_chords.finditer(rq_chords.text):
            link, str_rating, str_ratings, link_type = match.groups()
            rating = int(float(str_rating)*2)
            ratings = int(str_ratings)
            log.debug("{0}*{1:2d}: {2}".format(rating, ratings, link))
            if rating >= max_rating and link_type == 'c':
                if rating > max_rating or ratings > max_ratings:
                    max_rating = rating
                    max_ratings = ratings
                    best_link = link
                    log.debug("new best link")
        if best_link == None:
            log.warn('No chords for song "{}" from artist "{}"'.format(title, artist))
            self.url = rq_chords.url
            return False
        
        # request best lyrics with chords
        rq_chord = requests.get(best_link, headers=headers)
        log.debug("Url: {}".format(rq_chord.url))
        log.debug("Text: {}".format(rq_chord.text))
        if toFile != None:
            f = open(toFile + '_ChordsPlay_Best.txt', 'w');
            f.write("Best Url: {}\n".format(rq_chords.url))
            f.write(rq_chords.text)
            f.close()

        # extract plain lyrics with chords from html page
        r_chord=r'.*<pre[ >].*</pre>.*(<pre[ >].*</pre>).*/print/'
        rx_chord = re.compile(r_chord, re.DOTALL)
        match = re.match(rx_chord, rq_chord.text)
        self.url = rq_chord.url
        if match == None:
            log.warn('No chords at "{}"'.format(rq_chord.url))
            return False
        lyrics = match.group(1)
        self.lyrics = re.sub(r'<.*?>', '', lyrics).strip()
        
        # extract chords from lyrics
        # heuristic is: chords are within [brackets] or chords are on lines with equal or more spaces than non chord chars
        r_chordname=r'(\[)?\b([A-H][#b]{0,2}(maj|M|min|m|-|aug|\+|dim|sus([24]sus)?|add|dom)?([245679]|1[13])?(/[A-H][b#]{0-2})?)([] ])'
        rx_chordname = re.compile(r_chordname)
        rx_braced = re.compile(r'\([a-z]+\)')
        self.chords = set()
        for line in self.lyrics.splitlines():
            line += ' '
            line_without_chords = rx_chordname.sub('', line)
            line_without_chords_or_spaces = rx_braced.sub('', line_without_chords.replace(' ', ''))
            if len(line) > len(line_without_chords) and len(line_without_chords) > 2 * len(line_without_chords_or_spaces):
                is_chord_line = True
            else:
                is_chord_line = False
            #log.debug("Lyrics: '{}'".format(line))
            #log.debug("no cho: '{}'".format(line_without_chords))
            #log.debug("no csp: '{}'".format(line_without_chords_or_spaces))
            #log.debug("chords: {}".format(is_chord_line))
            for match in rx_chordname.finditer(line):
                log.debug("match: {}".format(match.group(0)))
                if is_chord_line or match.group(1) == '[':
                    self.chords.add(match.group(2))
        if self.chords == set():            
            log.info("no chords found")
        else:
            log.info("chords: {}".format(', '.join(self.chords)))
            
        # extract capo from lyrics
        capos = {
            '1':   1,  'I':    1,  'FIR':     1,
            '2':   2,  'II':   2,  'SECO':    2,
            '3':   3,  'III':  3,  'THI':     3,
            '4':   4,  'IV':   4,  'FOUR':    4,
            '5':   5,  'V':    5,  'FIF':     5,
            '6':   6,  'VI':   6,  'SIX':     6,
            '7':   7,  'VII':  7,  'SEVEN':   7,
            '8':   8,  'VIII': 8,  'EIGH':    8,
            '9':   9,  'IX':   9,  'NINE':    9,
            '10': 10,  'X':   10,  'TEN':    10,
            '11': 11,  'XI':  11,  'ELEVEN': 11 }
        r_capo = r'\bcapo\b.{0,9} [-+]?([0-9]{1,2}|[ivx]{1,4}|fir|seco|thi|four|fif|six|seven|eigh|nine|ten)(st|nd|rd|th)?\b'
        rx_capo = re.compile(r_capo, re.IGNORECASE)
        match = rx_capo.search(self.lyrics)
        if match != None:
            self.capo = capos.get(match.group(1).upper())
    
        # extract patterns from lyrics
        r_pattern=r'\W*\b([A-H][#b]{0,2}(maj|M|min|m|-|aug|\+|dim|sus([24]sus)?|add|dom)?([245679]|1[13])?(/[A-H][b#]{0-2})?)\b\W*([x0-9]{6}|(\W+[x0-9]{1,2}){6})\b'
        rx_pattern = re.compile(r_pattern)
        self.patterns = {}
        for line in self.lyrics.splitlines():
            match = rx_pattern.search(line)
            if match != None:
                self.patterns[match.group(1)] = match.group(7)
        log.info("patterns: {}".format(str(self.patterns)))
        
        return True
