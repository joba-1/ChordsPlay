#!/usr/bin/env python
# encoding: utf-8
'''
ChordsPlay.chordsplay -- ChordsPlay main entry point

ChordsPlay.chordsplay supports guitar players by showing lyrics and chords while playing songs

It defines main

@author:     joba1

@copyright:  2016 joba1. All rights reserved.

@license:    GPL 2.0

@contact:    joba-1@users.noreply.github.com
@deffield    updated: Updated
'''

import sys
import os
import logging
import traceback

from optparse import OptionParser
from configparser import SafeConfigParser

import ChordsPlay.db
import ChordsPlay.scan
import ChordsPlay.gui
import ChordsPlay.play
import ChordsPlay.playlist
import ChordsPlay.filter

__all__ = []
__version__ = 1.0
__date__ = '2016-02-18'
__updated__ = '2016-04-08'

DEBUG = 0
TESTRUN = 0
PROFILE = 0

# log init
logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)
log.info('Started')

def play(pos, song_id):
    log.debug("play id {} @ {}".format(song_id, pos))
    title, artist, text, path, songfile = db.getPlayInfo(song_id)
    player.play(os.path.join(path, songfile))
    gui.setText(text)
    gui.setTitle("{}: {} - {}".format(pos, title, artist))
    gui.mclist.tree.selection_clear()
    item_id = gui.mclist.get()[pos]
    log.debug("select item id {} @ {}".format(item_id, pos))
    gui.mclist.tree.selection_set([item_id])
    gui.mclist.tree.see(item_id)

def playlistDialog(playlist):
    dlg = ChordsPlay.filter.Filter(gui, db)
    if dlg.result != None:
        log.debug(str(dlg.result))
        gui.mclist.set(dlg.titles)
        playlist.new(gui.mclist)

def onClick(event):
    tree = event.widget
    item = tree.identify('item', event.x, event.y)
    pos = tree.index(item)
    log.debug("onClick: jump to pos {}".format(pos))
    playlist.jump(pos)
        
def main(argv=None):
    global db, gui, player, playlist
    
    # Command line options
    program_name = os.path.basename(sys.argv[0])
    program_version = "v1.0"
    program_build_date = "%s" % __updated__

    program_version_string = '%%prog %s (%s)' % (program_version, program_build_date)
    program_longdesc = '''supports guitar players by showing lyrics and chords while playing songs'''
    program_license = "Copyright 2016 joba1. Licensed under the GPL 2.0"

    if argv is None:
        argv = sys.argv[1:]
    try:
        # setup option parser
        parser = OptionParser(version=program_version_string, epilog=program_longdesc, description=program_license)
        parser.add_option("-c", "--config", dest="cfgfile", help="config file [default: %default]", metavar="FILE")
        parser.add_option("-v", "--verbose", dest="verbose", action="count", help="set verbosity level [default: %default]")

        # set defaults
        parser.set_defaults(cfgfile=os.path.expanduser("~/.{}.cfg".format(program_name)))

        # process options
        (opts, args) = parser.parse_args(argv)

        if opts.verbose != None:
            log.debug("verbosity level = %d" % opts.verbose)
        if opts.cfgfile:
            log.debug("cfgfile = %s" % opts.cfgfile)

        # MAIN BODY #
        
        # configuration #
        defaults = {
            "user": os.getenv('USER', os.getenv('USERNAME', 'anonymous')),
            "database": os.path.expanduser("~/.{}.db".format(program_name))
            }
        config = SafeConfigParser(defaults)
        config.add_section(program_name)
        config.read(os.path.expanduser(opts.cfgfile))
        
        # db connection #
        connector = config.get(program_name, 'database')
        user = config.get(program_name, 'user')
        db = ChordsPlay.db.Db(connector, user)
        
        # media locations #
        locations = db.getSongLists()
        new_locations = set([(os.path.normpath(os.path.join(*arg.split('\\'))),) for arg in args]) - locations
        if len(new_locations) > 0:
            db.addSongLists(new_locations)
            locations = db.getSongLists()

        # media scanner #
        log.debug("Starting scanner thread")
        scanner = ChordsPlay.scan.Scan(connector, user)
        scanner.start()
        
        # gui main loop #
        log.debug("Starting main loop")
        
        gui = ChordsPlay.gui.Gui(title=program_name)
        
        player = ChordsPlay.play.Play(gui)
        titles = db.getFirstPlaylist()
        gui.mclist.set(titles)
        playlist = ChordsPlay.playlist.Playlist(db, play, gui.mclist)

        gui.mclist.tree.bind("<Double-Button-1>", onClick)

        gui.onButton('Pause', player.pause)
        gui.onButton('Next', playlist.next)
        # todo: select matching item in gui songlist (onMove(func)?)
        gui.onButton('Prev', playlist.prev)
        gui.onButton('Shuffle', gui.mclist.shuffle)
        gui.onButton('Filter', lambda: playlistDialog(playlist))

        gui.mainloop()

        # quit #
        log.debug("Stopping scanner thread")
        scanner.quit.set()
        scanner.join()
        
    except Exception:
        traceback.print_exc()
        return 2
    
    finally:
        log.info('Finished')


if __name__ == "__main__":
    if DEBUG:
        sys.argv.append("-h")
    if TESTRUN:
        import doctest
        doctest.testmod()
    if PROFILE:
        import cProfile
        import pstats
        profile_filename = 'ChordsPlay.main_profile.txt'
        cProfile.run('main()', profile_filename)
        statsfile = open("profile_stats.txt", "wb")
        p = pstats.Stats(profile_filename, stream=statsfile)
        stats = p.strip_dirs().sort_stats('cumulative')
        stats.print_stats()
        statsfile.close()
        sys.exit(0)
    sys.exit(main())