#!/home/user/python-3.10.10/venv/base/bin/python3
# encoding: utf-8
'''
ChordsPlay.chordsplay -- ChordsPlay main entry point

ChordsPlay.chordsplay supports guitar players by showing lyrics and chords while playing songs

It defines main

@author:     joba1

@copyright:  2016-25 joba1. All rights reserved.

@license:    GPL 2.0

@contact:    joba-1@users.noreply.github.com
@deffield    updated: Updated
'''

import sys
import os
import logging
import traceback
import time
import signal

from optparse import OptionParser
from configparser import ConfigParser

import ChordsPlay.db
import ChordsPlay.scan
import ChordsPlay.sync
import ChordsPlay.gui
import ChordsPlay.play
import ChordsPlay.playlist
import ChordsPlay.filter
import ChordsPlay.selector
import ChordsPlay.amp
import ChordsPlay.ampdialog

from tkinter import messagebox

__all__ = []
__version__ = '3.35'  # update also in setup.py
__date__ = '2016-02-18'
__updated__ = '2026-11-10'

DEBUG = 0
TESTRUN = 0
PROFILE = 0

# set in main(); None until created, so shutdown() can run at any stage
db = None
gui = None
player = None
exit_signal = None          # signal that asked us to exit, if any
SCAN_JOIN_S = 3             # wait at most this long (in all) for the scanner to stop
HEARTBEAT_MS = 250

# Mustang presets (docs/spec-amp.md); amp stays None unless amp_url is configured
amp = None
cues = None
song_text = ''
title_text = None
amp_flash = None            # (text, until) shown in the title for a few seconds
AMP_TICK_MS = 200

def play(pos, song_id):
    log.debug("play id {} @ {}".format(song_id, pos))
    title, artist, text, path, songfile = db.getPlayInfo(song_id)
    player.play(os.path.join(path, songfile), song_id)
    gui.setText(text)
    global song_text
    song_text = "{}: {} - {}".format(pos, title, artist)
    updateTitle()
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

def updateTitle():
    text = song_text
    if amp is not None:
        if amp_flash and time.monotonic() < amp_flash[1]:
            status = amp_flash[0]
        elif cues.warning:
            status = 'amp: ' + cues.warning
        else:
            status = ChordsPlay.amp.describe(amp.snapshot())
        text = '{}   [{}]'.format(text, status) if text else '[{}]'.format(status)
    global title_text
    if text != title_text:
        title_text = text
        gui.setTitle(text)

def playPosition():
    pos = player.player.get_time()
    return pos if pos and pos > 0 else 0

def ampTick():
    '''Every AMP_TICK_MS: apply the playing song's cue, refresh the amp status.'''
    try:
        if player.song_id is not None:
            cues.tick(player.song_id, playPosition())
        updateTitle()
    except Exception:
        log.warning("amp tick failed", exc_info=True)
    gui.after(AMP_TICK_MS, ampTick)

def ampMissing():
    messagebox.showinfo("Guitar tone", 'Set amp_url in the config file to switch Mustang presets '
                        '(see docs/spec-amp.md)')

def ampDialog():
    if amp is None:
        ampMissing()
        return
    ChordsPlay.ampdialog.AmpDialog(gui, db, amp, cues, player.song_id, playPosition,
                                   seek=lambda ms: player.player.set_time(int(ms)),
                                   length=lambda: max(0, player.player.get_length()))
    cues.reload()

def learnPreset():
    '''Key G: store the amp's current slot as a cue for the playing song.'''
    global amp_flash
    if amp is None:
        ampMissing()
        return
    if player.song_id is None:
        messagebox.showinfo("Guitar tone", 'Please start a song first')
        return
    state = amp.snapshot()['state']
    slot = state.get('preset')
    if state.get('connected') and state.get('audition'):
        messagebox.showinfo("Guitar tone", 'A tone is playing (an audition on slot {}), not a preset. '
                            'Press g and use "Tone from here" in the tone panel to store it.'.format(slot))
        return
    if not state.get('connected') or not slot:
        messagebox.showinfo("Guitar tone", 'The amp is not connected, so its current preset is '
                            'not known. Press g to choose a preset instead.')
        return
    pos = playPosition()
    start = 0 if pos < 3000 else pos - pos % 500
    db.setSongPreset(player.song_id, start, slot, state.get('name'), replace_within_ms=2000)
    cues.reload()
    amp_flash = ('stored: {} {} from {}'.format(slot, state.get('name') or '',
                                                ChordsPlay.amp.fmt_ms(start)), time.monotonic() + 5)
    log.info("song %s: slot %d from %s (learned)", player.song_id, slot, ChordsPlay.amp.fmt_ms(start))

def startAmp(config, section):
    '''Create the amp client and cue logic if amp_url is configured.'''
    global amp, cues
    url = config.get(section, 'amp_url', fallback=None)
    if not url:
        return
    unit = config.get(section, 'amp_unit', fallback=None)
    control = None
    if unit:
        control = ChordsPlay.amp.AgentControl(
            unit, config.get(section, 'amp_sink', fallback='Mustang Micro Plus'))
    default_slot = config.getint(section, 'amp_default_slot', fallback=None)
    if default_slot is not None and not ChordsPlay.amp.valid_slot(default_slot):
        log.warning("amp_default_slot %s ignored, must be 1..100", default_slot)
        default_slot = None
    live_slot = config.getint(section, 'amp_live_slot', fallback=100)
    amp = ChordsPlay.amp.AmpClient(url, control=control, live_slot=live_slot).start()
    cues = ChordsPlay.amp.AmpCues(db, amp, lead_ms=config.getint(section, 'amp_lead_ms', fallback=300),
                                  default_slot=default_slot)
    log.info("amp agent %s%s", url, " (unit {})".format(unit) if unit else "")

def requestExit(signum, frame):
    '''SIGHUP (terminal closed), SIGTERM (kill, logout), SIGINT (Ctrl-C): exit cleanly.'''
    global exit_signal
    exit_signal = signum
    if signum == signal.SIGHUP:
        # the terminal is gone: send further output (log lines, Python's final flush)
        # to /dev/null instead of failing on every write (exit status 120)
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        os.dup2(devnull, sys.stderr.fileno())
        os.close(devnull)
    log.info("%s received, exiting", signal.Signals(signum).name)
    if gui is not None:
        try:
            gui.close()
        except Exception:
            pass

def heartbeat():
    '''Python runs signal handlers only while executing Python code; Tk's main loop is C.
    This timer gives requestExit() a chance to run within HEARTBEAT_MS.'''
    gui.after(HEARTBEAT_MS, heartbeat)

def shutdown(scanner):
    '''Every way out ends here: q, closing the window, a signal or an error.
    The scanner is told first, so its stop overlaps the rest; then what you hear
    and see goes, then the background work. Each step runs even if an earlier
    one failed.'''
    log.info("shutting down")
    t0 = time.monotonic()
    steps = []
    if scanner is not None:
        steps.append(("tell scanner to stop", scanner.quit.set))
    if player is not None:
        steps.append(("stop playback", player.close))
    if gui is not None:
        steps.append(("close window", gui.destroy))
    if amp is not None:
        steps.append(("stop amp client and agent", amp.stop))
    if scanner is not None:
        def waitScanner():
            # a scan step (file, DB write) ends quickly; a fetch from Ultimate Guitar
            # can hang in the network, then exit without it (it is a daemon thread,
            # and the fetch is simply repeated next time)
            scanner.join(max(0.1, SCAN_JOIN_S - (time.monotonic() - t0)))
            if scanner.is_alive():
                log.warning("scanner still busy (network request?), exiting without it")
        steps.append(("wait for scanner", waitScanner))
    if db is not None:
        steps.append(("close database", db.close))
    for name, step in steps:
        try:
            step()
            log.debug("shutdown: %s done after %.2f s", name, time.monotonic() - t0)
        except Exception:
            log.warning("shutdown: %s failed", name, exc_info=True)
    log.info("shut down in %.1f s", time.monotonic() - t0)

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
    program_license = "Copyright 2016-2025 joba1. Licensed under the GPL 2.0"

    # program options are taken from defaults dict, then configParser ini file, then main argv or sys.argv
    # opts contains the merged result where argv overrides ini file which overrides defaults
    # complication: program arguments can contain the config file to use so merge to opts is done manually
 
    if argv is None:
        argv = sys.argv[1:]
    try:
        print("parser setup")

        # setup option parser
        parser = OptionParser(version=program_version_string, epilog=program_longdesc, description=program_license)
        parser.add_option("-c", "--config", dest="cfgfile", help="config file [default: %default]", metavar="FILE")
        parser.add_option("-d", "--database", dest="database", help="use this database file", metavar="FILE")
        parser.add_option("-u", "--user", dest="user", help="database user")
        parser.add_option("-t", "--testfile", dest="testfile", help="dump html to this file", metavar="FILE")
        parser.add_option("-v", "--verbose", dest="verbose", action="count", help="set verbosity level [default: %default]")
        parser.add_option("-f", "--fix", dest="fix", action="count", help="fix database issues")
        parser.add_option("-l", "--logfile", dest="logfile", help="write DEBUG log to this file (console stays at INFO)", metavar="FILE")
        parser.add_option("--sync-url", dest="sync_url", help="central chord-sync service base URL", metavar="URL")
        parser.add_option("--sync-token", dest="sync_token", help="bearer token for the sync service")
        parser.add_option("--sync-seed", dest="sync_seed", action="store_true", help="push all local chord data to central once, then exit")

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
        config = ConfigParser(defaults)
        config.add_section(program_name)
        config.read(os.path.expanduser(opts.cfgfile))

        # merge config file to program arguments
        if opts.verbose == None:
            opts.verbose = int(config.get(program_name, 'verbosity'))
        if opts.verbose == None:
            opts.verbose = 0

        if opts.testfile is None:
            opts.testfile = config.get(program_name, 'testfile', fallback=None)
            
        if opts.user is None:
            opts.user = config.get(program_name, 'user')
            
        if opts.database is None:
            opts.database = config.get(program_name, 'database')

        if opts.sync_url is None:
            opts.sync_url = config.get(program_name, 'sync_url', fallback=None)
        if opts.sync_token is None:
            opts.sync_token = config.get(program_name, 'sync_token', fallback=None)

        # log init
        if opts.verbose != 0:
            logging.basicConfig(level=logging.DEBUG)
        else:
            logging.basicConfig(level=logging.INFO)

        if opts.logfile:
            file_handler = logging.FileHandler(opts.logfile)
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s:%(name)s:%(message)s'))
            logging.getLogger().addHandler(file_handler)

        log = logging.getLogger(__name__)
        log.info('Started {}'.format(program_version_string))
        for signum in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
            signal.signal(signum, requestExit)

        # db connection #
        connector = opts.database
        user = opts.user
        db = ChordsPlay.db.Db(connector, user)

        if opts.sync_seed:
            # one-time: publish all local chord data to central, then exit
            if not opts.sync_url:
                log.error("--sync-seed needs --sync-url (or sync_url in the config)")
                return 2
            try:
                n = ChordsPlay.sync.Sync(db, opts.sync_url, opts.sync_token).seed()
                log.info("seeded {} songs to {}".format(n, opts.sync_url))
            except Exception as e:
                # sync host unreachable/asleep: fail cleanly (no traceback), just re-run later
                log.error("seed failed - is {} reachable? ({})".format(opts.sync_url, e))
                return 2
        elif opts.fix != None:
            # add/remove code to fix stuff as needed
            db.fixTabTags()
        else:
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
            scanner = ChordsPlay.scan.Scan(connector, user, toFile=opts.testfile,
                                           sync_url=opts.sync_url, sync_token=opts.sync_token)
            scanner.start()
            
            # gui main loop (only if not a test run with one fixed song) #
            if opts.testfile != None:
                scanner.join()
                return
            try:
                log.debug("Starting main loop")
                
                gui = ChordsPlay.gui.Gui(title=program_name)
                
                player = ChordsPlay.play.Play(gui, db, opts.verbose)
                titles = db.getFirstPlaylist()
                gui.mclist.set(titles)
                playlist = ChordsPlay.playlist.Playlist(db, play, gui.mclist)

                gui.mclist.tree.bind("<Double-Button-1>", onClick)
                gui.bind("A", lambda x: player.setNextAudioOut())
                gui.bind("0", lambda x: player.position(0.0))  # 0.0-1.0
                gui.bind("+", lambda x: player.inc(1000))  # ms
                gui.bind("-", lambda x: player.dec(1000))  # ms

                gui.onButton('Pause', player.pause)
                gui.onButton('Next', playlist.next)
                gui.onButton('Prev', playlist.prev)
                gui.onButton('Shuffle', gui.mclist.shuffle)
                gui.onButton('Filter', lambda: playlistDialog(playlist))
                gui.onButton('Tags', lambda: tagDialog(player.song_id))
                gui.onButton('Audio', lambda: audioDialog(player))
                gui.onButton('Tone', ampDialog)
                gui.bind("G", lambda x: learnPreset())

                startAmp(config, program_name)
                if amp is not None:
                    gui.after(AMP_TICK_MS, ampTick)
                gui.after(HEARTBEAT_MS, heartbeat)

                if exit_signal is None:     # a signal may have come during startup
                    gui.mainloop()
            finally:
                shutdown(scanner)
        
    except Exception:
        traceback.print_exc()
        return 2
    
    finally:
        try:
            if log:
                pass
        except:
            log = logging.getLogger(__name__)
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
        statsfile = open("profile_stats.txt", "w", encoding="utf-8")
        p = pstats.Stats(profile_filename, stream=statsfile)
        stats = p.strip_dirs().sort_stats('cumulative')
        stats.print_stats()
        statsfile.close()
        sys.exit(0)
    sys.exit(main())
