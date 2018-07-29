'''
Created on 21.02.2016

@author: joba1
'''

import requests
import re
import logging
import json
import traceback


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)


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
        
        title = re.sub('[[({].*?[]})][ ]*', '', title)
        
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
        self.url = url
        rq_chords = requests.get(url, params=params, headers=headers)
        log.debug("Url: {}".format(rq_chords.url))
        log.debug("Text: {}".format(rq_chords.text))
        if toFile != None:
            f = open(toFile + '_ChordsPlay_List.txt', 'w');
            f.write("List Url: {}\n".format(rq_chords.url))
            f.write(rq_chords.text)
            f.close()
        
        # extract url and ranking of lyrics with chords
        try:
            js_songpage = json.loads(re.search('window.UGAPP.store.page = (.*);', rq_chords.text).group(1))
        except Exception:
            log.warning("Song {} from {} ranking page not found or has malformed json".format(title, artist))
            traceback.print_exc()
            
        max_rating = 0
        max_votes = 0
        best_link = None
        try:
            for song in js_songpage['data']['results']:
                if song.get('type') == 'Chords':
                    if song['rating'] >= max_rating:
                        if song['rating'] > max_rating or song['votes'] > max_votes:
                            max_rating = song['rating']
                            max_votes = song['votes']
                            best_link = song['tab_url']
                            log.debug("new best link {}".format(song.get('id')))
        except KeyError:
            log.debug("Song page data invalid")
        except Exception:
            log.warning("Song page parser failed")
            traceback.print_exc()

        if best_link == None:
            log.warn('No chords for song "{}" from artist "{}"'.format(title, artist))
            self.url = rq_chords.url
            return False
        
        # request best lyrics with chords
        self.url = best_link
        rq_chord = requests.get(best_link, headers=headers)
        log.debug("Url: {}".format(rq_chord.url))
        log.debug("Text: {}".format(rq_chord.text))
        if toFile != None:
            f = open(toFile + '_ChordsPlay_Best.txt', 'w');
            f.write("Best Url: {}\n".format(rq_chords.url))
            f.write(rq_chords.text)
            f.close()

        # extract plain lyrics with chords from html page
        try:
            js_chordspage = json.loads(re.search('window.UGAPP.store.page = (.*);', rq_chord.text).group(1))
        except Exception:
            log.warning("Chord page {} not found or has malformed json".format(best_link))
            traceback.print_exc()

        try:
            self.lyrics = js_chordspage['data']['tab_view']['wiki_tab']['content']
        except KeyError:
            log.debug("Chord page data invalid")
        except Exception:
            log.warning("Chord page parser failed")
            traceback.print_exc()
        
        # extract chords from lyrics. They look like [ch]chordname[/ch]
        r_chordname = r'[[]ch[]](.*?)[[]\/ch[]]'
        rx_chordname = re.compile(r_chordname)
        self.chords = set()
        for match in rx_chordname.finditer(self.lyrics):
            self.chords.add(match.group(1))

        if self.chords == set():            
            log.info("no chords found")
        else:
            log.info("chords: {}".format(', '.join(sorted(self.chords))))
            
        # remove chords "markup" [ch] and [/ch] from lyrics
        self.lyrics = self.lyrics.replace('[ch]', '').replace('[/ch]', '')
        
        # extract capo from lyrics (still works?)
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
    
        # extract patterns from lyrics (still works? Better use json result)
        r_pattern=r'\W*\b([A-H][#b]{0,2}(maj|M|min|m|-|aug|\+|dim|sus([24]sus)?|add|dom)?([245679]|1[13])?(/[A-H][b#]{0-2})?)\b\W*([x0-9]{6}|(\W+[x0-9]{1,2}){6})\b'
        rx_pattern = re.compile(r_pattern)
        self.patterns = {}
        for line in self.lyrics.splitlines():
            match = rx_pattern.search(line)
            if match != None:
                self.patterns[match.group(1)] = match.group(7)
        log.info("patterns: {}".format(str(self.patterns)))
        
        return True


if __name__ == "__main__":
    log.info('Started {}'.format("Chords Debug"))
    ch = Chords()
    ch.request("House of the rising sun", "The Animals")
    print("Lyrics: [{}]".format(ch.lyrics))
    print("Capo: [{}]".format(ch.capo))
    print("URL: [{}]".format(ch.url))
    print("Chords: [{}]".format(ch.chords))
