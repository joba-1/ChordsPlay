'''
Created on 25.02.2016

@author: joba1
'''

import tkinter as tk
from tkinter import ttk
from tkinter import font
from subprocess import call
import ChordsPlay.mclist
import logging

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
        tk.Tk.__init__(self, *args)
        '''
        Constructor for main window
        '''
        
        pad = 10
        width = 1
        height = 1
        
        # frames with 0..3 text areas and fonts. If there is no text, then isEmpty
        self.frames = []
        self.isEmpty = True
        self.fonts = []
        font_template = "Lyrics{}"
        
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
        self.mclist = ChordsPlay.mclist.MCList(('Title', 'Artist', 'Album', 'Capo', 'Chords'), [], playlist)
        self.mclist.grid(row=0, column=0, sticky=tk.NSEW)
        playlist.columnconfigure(0, weight=1)
        playlist.rowconfigure(0, weight=1)
                
        buttons = [
            #("Quit", self.quit),
            ("Pause", None),
            ("Next", None),
            ("Prev", None),
            ("Shuffle", None),
            #("Frame", self.nextFrame),
            ("Filter", None)
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
            self.fonts.append(lyrics_font)
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
        
        self.currFrame = -1
        self.nextFrame()
        
        self.fullscreenState = True
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
        try:
            call(["xset", "s", "reset"])
            self.after(10000, self.inhibitBlank)
        except FileNotFoundError:
            log.warn("Failed to disable screensaver with xset")

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

    def nextFrame(self):
        self.currFrame += 1
        if self.currFrame == len(self.frames):
            self.currFrame = 0
        self.frames[self.currFrame].lift()
        for w in self.frames[self.currFrame].winfo_children():
            log.debug("nextFrame {}: w={}/{} h={}/{}".format(str(w), w.winfo_width(), w.winfo_reqwidth(), w.winfo_height(), w.winfo_reqheight()))

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
        self.after_idle(self.resize)
    
    def resize(self, event=None):
        # todo: resize using font measure of longest line
        log.debug("resize")
        if self.isEmpty == True:
            return
        self.numResize += 1 
        numResize = self.numResize
        i = max([self.currFrame, 1])
        size, diff = self.resizeFrameFont(i, numResize)
        if size == None:
            return
        best_size = size
        best_diff = min(diff, 0)
        best_frame = i
        while i > 1 and size >= best_size:
            i -= 1
            size, diff = self.resizeFrameFont(i, numResize)
            if size == None:
                return
            if size > best_size or (size == best_size and min(diff, 0) > best_diff):
                best_frame = i
                best_size = size
                best_diff = min(diff, 0)
        if self.currFrame in [0, best_frame]:
            size = best_size
            i = self.currFrame + 1
            while i < len(self.frames) and size >= best_size:
                size, diff = self.resizeFrameFont(i, numResize)
                if size == None:
                    return
                if size > best_size or (size == best_size and min(diff, 0) > best_diff):
                    best_frame = i
                    best_size = size
                    best_diff = min(diff, 0)
                i += 1
        self.frames[best_frame].lift()
        if best_frame != self.currFrame:
            self.currFrame = best_frame
            log.debug("resize: Switched to frame {} with font size {}".format(best_frame, best_size))

    def resizeFrameFont(self, frameIndex, numResize):
        log.debug("resizeFrameFont({})".format(frameIndex))
        f = self.frames[frameIndex]
        frame_font = self.fonts[frameIndex]
        size = old_size = frame_font.actual()["size"]
        # log.debug("resizeFrameFont: Frame {} current size is {}".format(frameIndex, size))

        self.update_idletasks()
        if numResize < self.numResize:
            log.debug("resizeFrameFont: canceled")
            return None, None
        while min_diff(min(f.winfo_children(), key=min_diff)) > 0 and size < 50:
            size += 5
            # log.debug("resizeFrameFont: Frame {} test bigger fontsize {}".format(frameIndex, size))
            frame_font.configure(size=size)
            self.update_idletasks()
            if numResize < self.numResize:
                log.debug("resizeFrameFont: canceled")
                return None, None
         
        w = min(f.winfo_children(), key=min_diff)   
        while min_diff(w) < 0 and size > 6:
            size -= 1
            # log.debug("resizeFrameFont: Frame {} test smaller fontsize {}".format(frameIndex, size))
            frame_font.configure(size=size)
            self.update_idletasks()
            if numResize < self.numResize:
                log.debug("resizeFrameFont: canceled")
                return None, None
            w = min(f.winfo_children(), key=min_diff)   

        if size != old_size:
            log.debug("resizeFrameFont: Frame {} fontsize now {}".format(frameIndex, size))
                
        return size, min_diff(w)
