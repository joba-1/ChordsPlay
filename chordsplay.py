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
import ChordsPlay.selector

from tkinter import messagebox

__all__ = []
__version__ = '3.9'  # update also in setup.py
__date__ = '2016-02-18'
__updated__ = '2018-07-29'

DEBUG = 0
TESTRUN = 0
PROFILE = 0

def play(pos, song_id):
    log.debug("play id {} @ {}".format(song_id, pos))
    title, artist, text, path, songfile = db.getPlayInfo(song_id)
    player.play(os.path.join(path, songfile), song_id)
    gui.setText(text)
    gui.setTitle("{}: {} - {}".format(pos, title, artist))
    gui.mclist.tree.selection_clear()
    item_id = gui.mclist.get()[pos]
    log.debug("select item id {} @ {}".format(item_id, pos))
    gui.mclist.tree.selection_set(item_id)
    gui.mclist.tree.see(item_id)

def playlistDialog(playlist):
    dlg = ChordsPlay.filter.Filter(gui, db)
    if dlg.result != None:
        log.debug(str(dlg.result))
        gui.mclist.set(dlg.titles)
        playlist.new(gui.mclist)

def audioDialog(player):
    title = 'Select Audio Output'
    audioIds = []
    audioNames = []
    idx = 0
    modules = player.getAudioOut()
    for module in modules:
        idx += 1
        audioIds.append((module["name"], None))
        audioNames.append((idx, module["description"], "default"))
        for device in module["devices"]:
            idx += 1
            audioIds.append((module["name"], device["device"]))
            audioNames.append((idx, module["description"], device["description"]))

    if idx > 0:    
        dlg = ChordsPlay.selector.Selector(gui, title, ("Module", "Device"), audioNames)
        if dlg.result != None:
            log.debug("Audio out now module '{}' device '{}'".format(audioNames[dlg.result][1], audioNames[dlg.result][2]))
            player.setAudioOut(audioIds[dlg.result][0], audioIds[dlg.result][1])
    else:
        messagebox.showerror(title, 'No audio devices found!')

def tagDialog(song_id):
    if song_id == None:
        messagebox.showinfo("Tags", 'Please start a song first')
        return
    title, artist, _, _, _ = db.getPlayInfo(song_id)
    log.debug("tag song {}: {} - {}".format(song_id, title, artist))
    if title == None:
        messagebox.showerror("Tags", 'No song information available')
        return
    tags = db.getUserTags(song_id)
    dlg = ChordsPlay.selector.TagSelector(gui, "Tag {} - {}".format(title, artist), ("Key", "On", "Tag"), tags)
    if dlg.result != None:
        if tags[dlg.result][2] == ' ':
            status = "set"
            db.setUserTag(song_id, tags[dlg.result][0])
        else:
            status = "unset"
            db.delUserTag(song_id, tags[dlg.result][0])
        log.info("tag song '{}': {} '{}'".format(title, status, tags[dlg.result][3]))

def onClick(event):
    tree = event.widget
    item = tree.identify('item', event.x, event.y)
    pos = tree.index(item)
    log.debug("onClick: jump to pos {}".format(pos))
    playlist.jump(pos)
        
def main(argv=None):
    global db, gui, player, playlist, log
    
    # Command line options
    program_name = os.path.basename(sys.argv[0])
    program_version = __version__
    program_build_date = "%s" % __updated__

    program_version_string = '%s %s (%s)' % (program_name, program_version, program_build_date)
    program_longdesc = '''supports guitar players by showing lyrics and chords while playing songs'''
    program_license = "Copyright 2016 joba1. Licensed under the GPL 2.0"

    if argv is None:
        argv = sys.argv[1:]
    try:
        # setup option parser
        parser = OptionParser(version=program_version_string, epilog=program_longdesc, description=program_license)
        parser.add_option("-c", "--config", dest="cfgfile", help="config file [default: %default]", metavar="FILE")
        parser.add_option("-t", "--testfile", dest="testfile", help="dump html to this file", metavar="FILE")
        parser.add_option("-v", "--verbose", dest="verbose", action="count", help="set verbosity level [default: %default]")

        # set defaults
        parser.set_defaults(cfgfile=os.path.expanduser("~/.{}.cfg".format(program_name)))

        # process options
        (opts, args) = parser.parse_args(argv)

        # MAIN BODY #
        
        # configuration #
        defaults = {
            "user": os.getenv('USER', os.getenv('USERNAME', 'anonymous')),
            "database": os.path.expanduser("~/.{}.db".format(program_name)),
            "verbosity": '0'
            }
        config = SafeConfigParser(defaults)
        config.add_section(program_name)
        config.read(os.path.expanduser(opts.cfgfile))

        if opts.verbose == None:
            opts.verbose = int(config.get(program_name, 'verbosity'))
        if opts.verbose == None:
            opts.verbose = 0
            
        # log init
        if opts.verbose != 0:        
            logging.basicConfig(level=logging.DEBUG)
        else:
            logging.basicConfig(level=logging.INFO)
            
        log = logging.getLogger(__name__)
        log.info('Started {}'.format(program_version_string))

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
        if opts.testfile != None:
            log.debug("Scanner test file {}".format(opts.testfile))
        scanner = ChordsPlay.scan.Scan(connector, user, toFile=opts.testfile)
        scanner.start()
        
        # gui main loop #
        log.debug("Starting main loop")
        
        gui = ChordsPlay.gui.Gui(title=program_name)
        
        player = ChordsPlay.play.Play(gui, db, opts.verbose)
        titles = db.getFirstPlaylist()
        gui.mclist.set(titles)
        playlist = ChordsPlay.playlist.Playlist(db, play, gui.mclist)

        gui.mclist.tree.bind("<Double-Button-1>", onClick)
        gui.bind("A", lambda x: player.setNextAudioOut())

        gui.onButton('Pause', player.pause)
        gui.onButton('Next', playlist.next)
        gui.onButton('Prev', playlist.prev)
        gui.onButton('Shuffle', gui.mclist.shuffle)
        gui.onButton('Filter', lambda: playlistDialog(playlist))
        gui.onButton('Tags', lambda: tagDialog(player.song_id))
        gui.onButton('Audio', lambda: audioDialog(player))

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
        sys.argv.append("-v")
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
