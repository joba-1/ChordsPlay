#!/usr/bin/env python
# encoding: utf-8
'''
ChordsPlay.main -- ChordsPlay main entry point

ChordsPlay.main supports guitar players by showing lyrics and chords while playing songs

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

from optparse import OptionParser
from configparser import SafeConfigParser

import ChordsPlay.db
import ChordsPlay.scan
import ChordsPlay.gui

__all__ = []
__version__ = 0.1
__date__ = '2016-02-18'
__updated__ = '2016-02-18'

DEBUG = 0
TESTRUN = 0
PROFILE = 0

# log init
logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)
log.info('Started')


def main(argv=None):

    # Command line options
    program_name = os.path.basename(sys.argv[0])
    program_version = "v0.1"
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
        parser.set_defaults(cfgfile=os.path.expanduser("~/.ChordsPlay.cfg"))

        # process options
        (opts, args) = parser.parse_args(argv)

        if opts.verbose != None:
            log.debug("verbosity level = %d" % opts.verbose)
        if opts.cfgfile:
            log.debug("cfgfile = %s" % opts.cfgfile)

        # MAIN BODY #
        
        # configuration #
        defaults = {
                    "user": os.getenv('USER', 'anonymous'),
                    "database": os.path.expanduser("~/.ChordsPlay.db")
                    }
        config = SafeConfigParser(defaults)
        config.add_section('ChordsPlay')
        config.read(os.path.expanduser(opts.cfgfile))
        
        # db connection #
        connector = config.get('ChordsPlay', 'database')
        user = config.get('ChordsPlay', 'user')
        db = ChordsPlay.db.Db(connector, user)
        
        # media locations #
        locations = db.getSongLists()
        if len(args) > 0:
            new_locations = set(args) - locations
            if len(new_locations) > 0:
                db.addSongLists(new_locations)
                locations = db.getSongLists()

        # media scanner #
        log.debug("Starting scanner thread")
        scanner = ChordsPlay.scan.Scan(connector, user)
        scanner.start()
        
        # gui main loop #
        log.debug("Starting main loop")
        gui = ChordsPlay.gui.Gui('ChordsPlay')
        gui.mainloop()

        # quit #
        log.debug("Stopping scanner thread")
        scanner.quit.set()
        scanner.join()
        
    except Exception as e:
        indent = len(program_name) * " "
        sys.stderr.write(program_name + ": " + repr(e) + "\n")
        sys.stderr.write(indent + "  for help use --help\n")
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