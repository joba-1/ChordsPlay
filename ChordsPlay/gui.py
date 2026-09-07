'''
Created on 25.02.2016

@author: joba1
'''

import os
import tkinter as tk
from tkinter import ttk
from tkinter import font
from subprocess import call, Popen, DEVNULL
import ChordsPlay.mclist
import logging
import ctypes
import shutil
import sys

log = logging.getLogger(__name__)

def min_diff(widget):
    return min([
        widget.winfo_width() - widget.winfo_reqwidth(), 
        widget.winfo_height() - widget.winfo_reqheight()])
    
class Gui(tk.Tk):
    '''
    Create GUI to display chords
    '''

    def __init__(self, *args, title=None):
        # className setzt die WM_CLASS des Fensters. Ohne sie kann die
        # Taskleiste das Fenster keinem .desktop zuordnen und zeigt statt des
        # Symbols einen Platzhalter - Tk verwendet sonst den Skriptnamen.
        tk.Tk.__init__(self, *args, className="ChordsPlay")
        '''
        Constructor for main window
        '''
        self._set_window_icon()
        
        pad = 10
        width = 1
        height = 1
        
        # frames with 0..3 text areas. If there is no text, then isEmpty
        self.frames = []
        self.isEmpty = True
        font_template = "Lyrics{}"
        self.pad = pad
        
        if title != None:
            self.basetitle = title
        else:
            self.basetitle = ''
            
        self.setTitle(None)
        
        # paned window using all of toplevels space and resizing with it
        self.panes = tk.PanedWindow(self, showhandle=True, sashrelief=tk.SUNKEN, sashpad=5)
        self.panes.grid(row=0, column=0, sticky=tk.NSEW)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        
        # left pane with playlist and buttons
        playlist = ttk.Frame(self.panes)
        self.panes.add(playlist, stretch="always", sticky=tk.NSEW)
        self.mclist = ChordsPlay.mclist.MCList(('Title', 'Artist', 'Album', 'Capo', 'Chords', 'Tags', 'Played', 'Last'), [], playlist)
        self.mclist.grid(row=0, column=0, sticky=tk.NSEW)
        playlist.columnconfigure(0, weight=1)
        playlist.rowconfigure(0, weight=1)
                
        buttons = [
            #("Quit", self.quit),
            ("Pause", None),
            ("Next", None),
            ("Prev", None),
            ("Shuffle", None),
            ("Filter", None),
            ("Tags", None),
            ("Audio", None)
        ]
        self.buttons = {}       
        buttonFrame = ttk.Frame(playlist)
        buttonFrame.grid(row=1, column=0, sticky=tk.SW)
        for b in range(len(buttons)):
            button = ttk.Button(buttonFrame, text=buttons[b][0], command=buttons[b][1])
            button.grid(row=1, column=b, padx=pad, pady=pad)
            self.buttons[buttons[b][0]] = button

        # text pane to the right
        textFrame = ttk.Frame(self.panes)
        self.panes.add(textFrame, stretch="always", sticky=tk.NSEW)
        
        self.sashPos = textFrame.winfo_screenwidth() // 3
        
        for f in range(5):
            frame = ttk.Frame(textFrame, width=textFrame.winfo_screenwidth() // 2, 
                              height=textFrame.winfo_screenheight() // 2)
            frame.grid(row=0, column=0, sticky=tk.NSEW)
            font_name = font_template.format(f)
            lyrics_font = font.Font(name=font_name)
            lyrics_font.configure(**font.nametofont("TkFixedFont").configure())
            frame.font = lyrics_font
            for t in range(f):
                text = tk.Text(frame, wrap=tk.NONE, width=width, height=height, 
                               font=font_name, state='disabled', padx=pad, pady=pad)
                text.grid(row=0, column=t, padx=pad, pady=pad, sticky=tk.NSEW)
                frame.columnconfigure(t, weight=1)
            frame.rowconfigure(0, weight=1)
            frame.grid_propagate(False)
            self.frames.append(frame)
                    
        textFrame.columnconfigure(0, weight=1)
        textFrame.rowconfigure(0, weight=1)
        
        textFrame.bind("<Configure>", self.resize)
        self.numResize = 0
        
        self.currFrame = self.frames[0]
        self.currFrame.lift()
        
        self.fullscreenState = False
        self.attributes("-fullscreen", self.fullscreenState)
        self.bind("<F11>", self.toggleFullscreen)
        self.bind("<Escape>", self.endFullscreen)
        
        self.bind("<space>", lambda x: self.buttons["Pause"].invoke())
        self.bind("<Return>", lambda x: self.buttons["Next"].invoke())
        self.bind("<Button-3>", lambda x: self.buttons["Next"].invoke())
        self.bind("<BackSpace>", lambda x: self.buttons["Prev"].invoke())
        self.bind("<Button-2>", lambda x: self.buttons["Prev"].invoke())
        self.bind("q", lambda x: self.quit())
        self.bind("f", lambda x: self.buttons["Filter"].invoke())
        self.bind("n", lambda x: self.buttons["Next"].invoke())
        self.bind("p", lambda x: self.buttons["Prev"].invoke())
        self.bind("s", lambda x: self.buttons["Shuffle"].invoke())
        self.bind("m", lambda x: self.buttons["Pause"].invoke())
        self.bind("t", lambda x: self.buttons["Tags"].invoke())
        self.bind("a", lambda x: self.buttons["Audio"].invoke())
        self.bind("l", self.toggleList)
        
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        self.inhibitBlank()

    def toggleList(self, _):
        x, y = self.panes.sash_coord(0)
        if x < 50:
            self.panes.sash_place(0, self.sashPos, y)
        else:
            self.sashPos = x
            self.panes.sash_place(0, 0, y)
    
    def inhibitBlank(self):
        '''
        Keep the screen awake while ChordsPlay runs. Auto-detect the platform:
          - Linux/BSD with systemd: hold a logind "idle" inhibitor. This is
            desktop-agnostic and honoured by KDE Plasma and GNOME alike, on
            both Wayland and X11. Released again in destroy().
          - plain X11 without systemd: poke the X screensaver via xset (polled).
          - Windows: Win32 SetThreadExecutionState.
        '''
        if sys.platform == "win32":
            self.inhibitBlankWin()
            return

        systemd_inhibit = shutil.which("systemd-inhibit")
        if systemd_inhibit:
            try:
                self.inhibitor = Popen(
                    [systemd_inhibit, "--what=idle:sleep", "--who=ChordsPlay",
                     "--why=Playing music", "--mode=block", "sleep", "infinity"],
                    stdout=DEVNULL, stderr=DEVNULL)
                log.info("Screen blanking inhibited via systemd-inhibit")
                return
            except OSError as e:
                log.warning("systemd-inhibit failed (%s), falling back to xset", e)

        # Fallback for plain X11 without systemd: reset the screensaver timer
        try:
            call(["xset", "s", "reset"])
            self.after(10000, self.inhibitBlank)
        except FileNotFoundError:
            log.warning("Failed to disable screensaver: no systemd-inhibit and no xset")

    def inhibitBlankWin(self):
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000002)
        except:
            log.warning("Failed to disable screensaver")

    def destroy(self):
        inhibitor = getattr(self, "inhibitor", None)
        if inhibitor is not None:
            inhibitor.terminate()
            self.inhibitor = None
        super().destroy()

    def toggleFullscreen(self, event=None):
        self.fullscreenState = not self.fullscreenState  # Just toggling the boolean
        self.attributes("-fullscreen", self.fullscreenState)
        return "break"

    def endFullscreen(self, event=None):
        self.fullscreenState = False
        self.attributes("-fullscreen", False)
        return "break"

    def onButton(self, label, runnable):
        self.buttons[label].configure(command=runnable)

    def setTitle(self, title):
        if title == None:
            self.title(self.basetitle)
        else:
            self.title("{} {}".format(self.basetitle, title))

    def setText(self, text):
        log.debug("setText")
        self.isEmpty = True
        lines = text.splitlines()
        num_lines = len(lines)
        self.frames[0].lift()
        self.currFrame = self.frames[0]
        for f in self.frames:
            texts = f.winfo_children()
            if len(texts) > 0:
                widths = []
                num_rows = int((num_lines + len(texts) - 1) / len(texts))
                for w in range(len(texts)):
                    w_lines = lines[num_rows * w:num_rows * (w + 1)]
                    texts[w]['state'] = 'normal'
                    texts[w].delete('1.0', tk.END)
                    texts[w].insert('1.0', '\n'.join(w_lines))
                    texts[w]['state'] = 'disabled'
                    width = len(max(w_lines, key=len))
                    widths.append(width)
                    texts[w].configure(width=width, height=num_rows)
                    if width > 0:
                        self.isEmpty = False 
                log.debug("setText {}: rows={} widths={}".format(str(f), num_rows, str(widths)))
        self.resize()

    def resize(self, event=None):
        log.debug("resize")

        if self.isEmpty == True:
            return

        maxSize = 50
        minSize = 6
        
        bestSize = minSize
        bestFrame = None
        
        frame_font = self.frames[0].font
        frames = self.frames[1:]
        
        self.update_idletasks()
        
        while maxSize >= minSize:
            # we still have font sizes to check
            currSize = (maxSize + minSize) // 2
            # log.debug("resize: min/max = {}/{}: try {}".format(minSize, maxSize, currSize))
            frame_font.configure(size=currSize)
            bestDiff = -100000
            
            for frame in frames:
                # for all frames
                for text in frame.winfo_children():
                    # for all text boxes in the current frame
                    haveWidth = text.winfo_width() - 2 * (self.pad + 2)
                    haveHeight = text.winfo_height() - 2 * (self.pad + 2)
                    chars = text["width"]
                    lines = text["height"]
                    wantWidth = frame_font.measure("g" * chars)
                    wantHeight = frame_font.metrics("linespace") * lines
                    minDiff = min(haveHeight - wantHeight, haveWidth - wantWidth)
                    # log.debug("resize: chars = {}, lines = {}, have/want width = {}/{}, have/want height = {}/{} -> Diff = {}".format(chars, lines, haveWidth, wantWidth, haveHeight, wantHeight, minDiff))
                    if (currSize > bestSize and minDiff >= 0) or (currSize == bestSize and minDiff > bestDiff):
                        # better than all we've seen so far
                        bestSize = currSize
                        bestDiff = minDiff
                        bestFrame = frame
                        # log.debug("resize: better {} with diff {}".format(currSize, minDiff))
            
            if bestDiff >= 0:
                # we found a font that fits, might be even bigger
                minSize = currSize + 1
            else:
                # we found no font that fits, try smaller 
                maxSize = currSize - 1

        if bestFrame == None:
            log.debug("resize: nothing found, use last frame")
            bestFrame = self.frames[-1:][0]

        log.debug("resize: best {}".format(bestSize))
        bestFrame.font.configure(size=bestSize)
            
        if bestFrame != self.currFrame:
            log.debug("resize: switch to frame {}".format(len(bestFrame.winfo_children())))
            self.currFrame = bestFrame
            bestFrame.lift()
    def _set_window_icon(self):
        """Fenstersymbol setzen, in mehreren Groessen.

        Tk waehlt aus den uebergebenen Bildern selbst die passende Groesse;
        eine einzelne kleine Grafik wuerde in der Taskleiste hochskaliert.
        Schlaegt es fehl, laeuft das Programm ohne Symbol weiter - ein
        fehlendes Icon ist kein Grund, die Anwendung nicht zu starten.
        """
        try:
            here = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "data")
            imgs = [tk.PhotoImage(file=os.path.join(here,
                                                    "chordsplay-%d.png" % s))
                    for s in (16, 32, 48, 64, 128)
                    if os.path.exists(os.path.join(here,
                                                   "chordsplay-%d.png" % s))]
            if imgs:
                self._icon_refs = imgs      # Referenz halten, sonst GC
                self.iconphoto(True, *imgs)
        except Exception:
            logging.debug("Fenstersymbol konnte nicht gesetzt werden",
                          exc_info=True)


