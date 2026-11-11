'''
Created on 21.02.2016

@author: joba1
'''

import ChordsPlay.http
import re
import logging
import json
import html
import traceback
import os
from datetime import datetime
import time


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
        self.request_time = datetime.now()
        self.request_interval = 2
        self.session = ChordsPlay.http.session(browser=True)

    def wait(self):
        now = datetime.now()
        elapsed_time = now - self.request_time
        waiting_time = self.request_interval - elapsed_time.total_seconds()
        self.request_time = now
        if waiting_time > 0:
            time.sleep(waiting_time)


    def unify(self, string):
        string = string.lower()
        string = string.replace("&", " and ")
        if string.startswith("the "):
            string = string[4:]
        return ''.join(filter(lambda ch: ch.isalpha(), string.lower()))


    def is_match(self, title1, title2, artist1, artist2):
        str1 = self.unify(title1)
        str2 = self.unify(title2)
        str3 = self.unify(artist1)
        str4 = self.unify(artist2)
        common1 = os.path.commonprefix([str1, str2])
        common2 = os.path.commonprefix([str3, str4])
        if (common1 == str1 or common1 == str2) and (common2 == str3 or common2 == str4):
            return True
        common3 = os.path.commonprefix([str3+str1, str2])
        if common3 == str1+str3 or common3 == str2:
            return True
        return False


    def is_title_match(self, title1, title2):
        '''
        True if the (unified) titles match by prefix, ignoring the artist.
        Used for the cross-artist fallback (covers / live / compilations).
        '''
        str1 = self.unify(title1)
        str2 = self.unify(title2)
        common = os.path.commonprefix([str1, str2])
        return common == str1 or common == str2


    def is_distinctive_title(self, title):
        '''
        True if the title is specific enough (>= 3 words) that a same-title tab
        by a different artist is very unlikely to be an unrelated song. Gates the
        cross-artist fallback so generic titles like "Gone" are not matched to a
        different song of the same name.
        '''
        words = [w for w in re.split(r'\s+', title.strip()) if w not in ('', '-')]
        return len(words) >= 3


    def request(self, title, artist, toFile=None):
        '''
        get lyrics and chords from the web and store them
        return True on success
        return False on errors
        return None if challenged
        '''
        # reset extracted state so values from a previous song never leak through
        self.lyrics = None
        self.chords = None
        self.capo = None
        self.patterns = None

        title = re.sub(r'[\[({].*?[\])}] *', '', title)
        
        # generic request parameters
        url = "https://www.ultimate-guitar.com/search.php"
        params = {
            'type':       300,
            'rating[]':   [5,4],
            'title':      title
        }

        # request list of lyrics with chords
        self.url = url
        self.wait()
        rq_chords = self.session.get(url, params=params)
        log.info("Songs Url: {}".format(rq_chords.url))
        log.debug("Text: {}".format(rq_chords.text))
        if toFile != None:
            f = open(toFile + '_ChordsPlay_List.txt', 'w');
            f.write("List Url: {}\n".format(rq_chords.url))
            f.write(rq_chords.text)
            f.close()
        
        # extract url and ranking of lyrics with chords
        try:
            js_songpage = json.loads(html.unescape(re.search('data-content="(.*)"', rq_chords.text).group(1)))
        except Exception:
            log.warning("request(): Song {} from {} ranking page not found or has malformed json".format(title, artist))
            if rq_chords.text.find("challenge-platform") >= 0:
                log.error(f"abort challenge")
                return None
            log.warning(f"page text: '{rq_chords.text}'")
            traceback.print_exc()
            return False

        # try:
        #     js_songpage = json.loads(re.search('window.UGAPP.store.page = (.*);', rq_chords.text).group(1))
        # except Exception:
        #     log.warning("Song {} from {} ranking page not found or has malformed json".format(title, artist))
        #     traceback.print_exc()
            
        max_rating = -1
        max_votes = -1
        best_link = None
        # Fallback for covers / live / compilation tracks: remember the most
        # popular same-title tab from *any* artist, in case our exact artist has
        # no tab. Only applied to distinctive (multi-word) titles below, so a
        # generic title does not grab a different song of the same name.
        fallback_votes = -1
        fallback_link = None
        fallback_artist = None
        try:
            for song in js_songpage['store']['page']['data']['results']:
                if song.get('type') != 'Chords':
                    continue
                if self.is_match(title, song['song_name'], artist, song['artist_name']):
                    if song['rating'] > max_rating or (song['rating'] == max_rating and song['votes'] > max_votes):
                        max_rating = song['rating']
                        max_votes = song['votes']
                        best_link = song['tab_url']
                        log.debug("new best link {}".format(song.get('id')))
                elif self.is_title_match(title, song['song_name']):
                    if song['votes'] > fallback_votes:
                        fallback_votes = song['votes']
                        fallback_link = song['tab_url']
                        fallback_artist = song['artist_name']
        except KeyError:
            log.debug("Song page data invalid")
            log.debug('List JSON (html decoded):"{}"'.format(json.dumps(js_songpage, indent=4)))
        except Exception:
            log.warning("Song page parser failed")
            log.debug('List JSON (html decoded):"{}"'.format(json.dumps(js_songpage, indent=4)))
            traceback.print_exc()

        if best_link is None and fallback_link is not None and self.is_distinctive_title(title):
            log.info('No tab by "{}"; using same-title tab by "{}" ({} votes) for "{}"'.format(
                artist, fallback_artist, fallback_votes, title))
            best_link = fallback_link

        if best_link is None:
            log.warning('No chords for song "{}" from artist "{}"'.format(title, artist))
            try:
                log.debug('List JSON (results): "{}" for "{}" from "{}"'.format(json.dumps(js_songpage['store']['page']['data']['results'], indent=4), title, artist))
            except Exception:
                log.debug('List JSON (html decoded):"{}" for "{}" from "{}"'.format(json.dumps(js_songpage, indent=4), title, artist))
            self.url = rq_chords.url
            return False
        
        # request best lyrics with chords
        self.url = best_link
        self.wait()
        rq_chord = self.session.get(best_link)
        log.info("Song Url: {}".format(rq_chord.url))
        log.debug("Text: {}".format(rq_chord.text))
        if toFile != None:
            f = open(toFile + '_ChordsPlay_Best.txt', 'w');
            f.write("Best Url: {}\n".format(rq_chords.url))
            f.write(rq_chords.text)
            f.close()

        # extract plain lyrics with chords from html page
        try:
            js_chordspage = json.loads(html.unescape(re.search('data-content="(.*)"', rq_chord.text).group(1)))
        except Exception:
            log.warning("Chord page {} not found or has malformed json".format(best_link))
            traceback.print_exc()

        try:
            self.lyrics = js_chordspage['store']['page']['data']['tab_view']['wiki_tab']['content']
        except KeyError:
            log.debug("Chord page data invalid")
            log.debug('Chords JSON (html decoded):"{}"'.format(json.dumps(js_chordspage, indent=4)))
        except Exception:
            log.warning("Chord page parser failed")
            log.debug('Chords JSON (html decoded):"{}"'.format(json.dumps(js_chordspage, indent=4)))
            traceback.print_exc()
        
        # extract chords from lyrics. They look like [ch]chordname[/ch]
        r_chordname = r'\[ch\](.*?)\[/ch\]'
        rx_chordname = re.compile(r_chordname)
        self.chords = set()
        for match in rx_chordname.finditer(self.lyrics):
            self.chords.add(match.group(1))

        if self.chords == set():            
            log.info("no chords found")
            log.debug('Chords JSON (html decoded):"{}"'.format(json.dumps(js_chordspage, indent=4)))
        else:
            log.info("chords: {}".format(', '.join(sorted(self.chords))))
            
        # remove chords "markup" [ch], [/ch], [tab] and [/tab] from lyrics
        self.lyrics = self.lyrics.replace('[ch]', '').replace('[/ch]', '').replace('[tab]', '').replace('[/tab]', '')
        
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
        r_capo = r'(?<![Nn][Oo] )(?<![Ww][Ii][Tt][Hh][Oo][Uu][Tt] )\bcapo\b.{0,9} [-+]?([0-9]{1,2}|[ivx]{1,4}|fir|seco|thi|four|fif|six|seven|eigh|nine|ten)(st|nd|rd|th)?\b'
        rx_capo = re.compile(r_capo, re.IGNORECASE)
        match = rx_capo.search(self.lyrics)
        if match != None:
            self.capo = capos.get(match.group(1).upper())
    
        # extract chord fingering patterns from lyrics, e.g. a line like
        # "D/F# 2 0 0 2 3 2" or "Cmaj7 x32000" -> {'D/F#': ' 2 0 0 2 3 2', ...}
        # (still works? Better use json result)
        r_note = r'[A-H][#b]{0,2}'                             # root + up to 2 accidentals (H = German B)
        r_name = (r'(?P<name>' + r_note
            + r'(?:maj|min|aug|dim|sus|add|dom|M|m|-|\+|°|o|ø)?'          # primary quality
            + r'(?:sus[24]?|add|maj|M|/[69]|[#b+-]?(?:1[0-3]|[2-9]))*'    # stacked extensions/alterations (7, m7b5, 7#9, 6/9, maj7#11, 7sus4, ...)
            + r'(?:/' + r_note + r')?'                                   # optional slash bass note (e.g. /F#)
            + r')')
        r_frets = r'(?P<frets>[x0-9]{6}|(?:\W+[x0-9]{1,2}){6})'          # 6 frets, compact (x32010) or separated (2 0 0 2 3 2)
        rx_pattern = re.compile(r'\W*\b' + r_name + r'\W*' + r_frets + r'\b')
        self.patterns = {}
        for line in self.lyrics.splitlines():
            match = rx_pattern.search(line)
            if match != None:
                self.patterns[match.group('name')] = match.group('frets')
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
