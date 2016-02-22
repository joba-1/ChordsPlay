'''
Created on 21.02.2016

@author: joba1
'''

import requests;
import re;

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

    def request(self, title, artist):
        '''
        get lyrics and chords from the web and store them
        '''
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
        rq_chords = requests.get(url, params=params, headers=headers)
        # print("Url:", rq_chords.url, file=sys.stderr)
        # print("Text:", rq_chords.text, file=sys.stderr)
        songlist = r'<a href="([^"]*?)" class="song result-link".+?<span class="r_([0-9])"></span>.+?<b class="ratdig">([0-9]+)</b>'
        rx_chords = re.compile(songlist, re.DOTALL)
        max_rating = 0
        max_ratings = 0
        best_link = None
        for match in rx_chords.finditer(rq_chords.text):
            link, str_rating, str_ratings = match.groups()
            rating = int(str_rating)
            ratings = int(str_ratings)
            # print("{0}*{1:2d}: {2}".format(rating, ratings, link), file=sys.stderr)
            if rating >= max_rating:
                if rating > max_rating or ratings > max_ratings:
                    max_rating = rating
                    max_ratings = ratings
                    best_link = link
                    # print("new best link", file=sys.stderr)
        if best_link == None:
            # sys.exit('No chords for song "{}" from artist "{}"'.format(song, artist))
            self.url = rq_chords.url
            return False
        rq_chord = requests.get(best_link, headers=headers)
        chord=r'.*?(<pre>.*</pre>).*/print/'
        rx_chord = re.compile(chord, re.DOTALL)
        match = re.match(rx_chord, rq_chord.text)
        self.url = rq_chord.url
        if match == None:
            # sys.exit('No chords at "{}"'.format(rq_chord.url))
            return False
        lyrics = match.group(1)
        self.lyrics = re.sub(r'<.*?>', '', lyrics)
        return True
