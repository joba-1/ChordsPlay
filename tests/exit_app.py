'''
Helper for tests/manual_exit_test.py: runs the real chordsplay.py main() with a
fetch from Ultimate Guitar that hangs (so the scanner is busy, like on a slow
network), starts playing and prints READY once the scanner hangs in a fetch. With CP_TRIGGER set it then exits by
itself: "q" (key), "wmclose" (window close button), "dialog-wmclose" (close the
window while the tag dialog is open). Without it, the driver sends a signal.

Usage: exit_app.py <config> <database>
'''

import importlib.util
import os
import sys
import threading
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import ChordsPlay.chords   # noqa: E402
import ChordsPlay.gui      # noqa: E402


fetching = threading.Event()


def hanging_request(self, title, artist, toFile=None):
    fetching.set()
    time.sleep(20)          # a network request that takes forever
    return False


ChordsPlay.chords.Chords.request = hanging_request

spec = importlib.util.spec_from_file_location('cp', os.path.join(REPO, 'chordsplay.py'))
cp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cp)

trigger = os.environ.get('CP_TRIGGER')
orig = ChordsPlay.gui.Gui.mainloop


def mainloop(gui, *a):
    def start():
        cp.playlist.jump(0)
        gui.after(1500, ready)

    def ready():
        # wait until the scanner is stuck in a fetch: the hardest case for exiting
        # (it first walks the music folders, which can take a while over NFS)
        if not fetching.is_set():
            gui.after(500, ready)
            return
        print('READY', cp.playPosition(), flush=True)
        if trigger == 'q':
            gui.after(500, lambda: gui.event_generate('<KeyPress-q>'))
        elif trigger == 'wmclose':
            gui.after(500, lambda: gui.tk.call(gui.protocol('WM_DELETE_WINDOW')))
        elif trigger == 'dialog-wmclose':
            gui.after(500, open_dialog)

    def open_dialog():
        # the window is closed from inside the dialog's own (modal) wait loop
        gui.after(800, lambda: gui.tk.call(gui.protocol('WM_DELETE_WINDOW')))
        print('DIALOG', flush=True)
        cp.tagDialog(cp.player.song_id)

    gui.after(1000, start)
    return orig(gui, *a)


ChordsPlay.gui.Gui.mainloop = mainloop
sys.argv[0] = 'chordsplay.py'           # the config section is named after the program
sys.exit(cp.main(['-c', sys.argv[1], '-d', sys.argv[2]]))
