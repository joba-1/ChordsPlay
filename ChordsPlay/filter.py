'''
Created on 03.04.2016

@author: joba1
'''

import tkinter as tk
from tkinter import ttk
from tkinter import filedialog
from tkinter import messagebox
import ChordsPlay.dialog
import ChordsPlay.mclist
import os
import re
import json
import logging

log = logging.getLogger(__name__)

class Filter(ChordsPlay.dialog.Dialog):
    '''
    Filter dialog for songs
    Result is a list of regular expressions to assemble a playlist
    The list can be saved
    '''

    def __init__(self, parent, db):
        self.name = None
        self.db = db
        self.patterns = None
        ChordsPlay.dialog.Dialog.__init__(self, parent, "Playlist Filter")
        
    def onFocus(self):
        if self.validate():
            self.update()
            return True
        return False
        
    def body(self, master, label=None):
        # create dialog body.  
        # return widget that should have initial focus.
        
        labels = ["Titles", "Artists", "Albums", "Capos", "Required Chords", "Accepted Chords", "Tags"]

        self.entries = {}
        self.errors = {}
        
        onFocusCmd = self.register(self.onFocus)
        
        for row in range(len(labels)):
            ttk.Label(master, text=labels[row]).grid(row=row*2, column=0, sticky=tk.W)
            self.entries[labels[row]] = ttk.Entry(master, validate='focusin', validatecommand=(onFocusCmd))
            self.entries[labels[row]].grid(row=row*2, column=1, sticky=tk.EW, padx=5, pady=5)
            self.errors[labels[row]] = ttk.Label(master)
            self.errors[labels[row]].grid(row=row*2+1, column=1, sticky=tk.EW)
            master.columnconfigure(1, weight=1)

        self.mclist = ChordsPlay.mclist.MCList(('Title', 'Artist', 'Album', 'Capo', 'Chords', 'Played', 'Last'), [], master)
        self.mclist.grid(row=2*len(labels), column=0, columnspan=2, sticky=tk.NSEW)
        master.rowconfigure(2*len(labels), weight=1)

        style = ttk.Style()
        # No one would ever have guessed this is necessary! Thanks stackoverflow :)
        if "plain.field" not in style.element_names():
            style.element_create("plain.field", "from", "clam")
            style.layout("Pink.TEntry", [('Entry.plain.field', {
                'children': [('Entry.background', {
                    'children': [('Entry.padding', {
                        'children': [('Entry.textarea', {
                            'sticky': 'nswe'
                            })],
                        'sticky':   'nswe'
                        })], 
                    'sticky': 'nswe'
                    })],
                'border': '2', 
                'sticky': 'nswe'
                })])
        style.configure('Pink.TEntry', fieldbackground='pink')
            
        return self.entries[labels[0]]

    def buttonbox(self):

        box = ttk.Frame(self)
        box.grid(column=0, sticky=tk.W, padx=5, pady=5)
        
        buttons = [
            ("Ok",      self.ok), 
            ("Cancel",  self.cancel),
            ("Load",    self.load), 
            ("Save",    self.save), 
            ("Save As", self.saveAs), 
            ("Import",  self.importFile), 
            ("Export",  self.exportFile)
            ]

        for col in range(len(buttons)):
            button = ttk.Button(box, text=buttons[col][0], width=10, command=buttons[col][1])
            button.grid(row=0, column=col, padx=5, pady=5)
            if buttons[col][0] == "Ok":
                button["default"] = tk.ACTIVE
        
        self.bind("<Return>", self.ok)
        self.bind("<Escape>", self.cancel)

    def load(self):
        # load pattern names from database and display selection list (allow delete)
        if self.validate():
            self.update()
        self.initial_focus.focus_set()
    
    def save(self):
        if self.name == None:
            self.saveAs()
        else:
            # save filter patterns in db
            pass
    
    def saveAs(self):
        # maybe load names from db and allow selection for overwrite?
        dialog = ChordsPlay.dialog.Dialog(self, "Save Filter as", "Name")
        if dialog.result != None:
            self.name = dialog.result
            self.save()
        if self.validate():
            self.update()
        self.initial_focus.focus_set()
    
    def importFile(self):
        title = "Import Playlist Pattern"
        desc = "Playlist patterns"
        vers = 1
        filename = filedialog.askopenfilename(
            title = title,
            defaultextension = ".pat",
            filetypes = (
                (desc, "*.pat"),
                ("All files", "*")
                )
            )
        if filename != None:
            try:
                with open(filename, 'r', encoding='utf-8') as infile:
                    content = json.load(infile)
                    if type(content) == type({}):
                        if content.get("type") == desc and content.get("version") == vers:
                            for key in self.entries:
                                pattern = content.get(key, '')
                                entry = self.entries[key]
                                entry.delete(0, tk.END)
                                entry.insert(0, pattern)
                            if self.validate():
                                self.update()
            except Exception as e:
                messagebox.showerror(title, 'Import failed:\n{}'.format(str(e)))
        self.initial_focus.focus_set()
    
    def exportFile(self):
        title = "Export Playlist Pattern"
        desc = "Playlist patterns"
        vers = 1
        filename = filedialog.asksaveasfilename(
            title = title,
            filetypes = (
                (desc, "*.pat"),
                ("All files", "*")
                )
            )
        if filename != None:
            (name, ext) = os.path.splitext(filename)
            if ext == '':
                filename = name + '.pat'
            try:
                with open(filename, 'w', encoding='utf-8') as outfile:
                    content = { 
                        'type':   desc,
                        'version': vers
                        }
                    for key in self.entries:
                        content[key] = self.entries[key].get()
                    json.dump(content, outfile, sort_keys=True, indent=2)
            except Exception as e:
                messagebox.showerror(title, 'Export failed:\n{}'.format(str(e)))
        self.initial_focus.focus_set()
                
    def validate(self):
        # check if all entries are empty or valid regexes (compile them)
        rc = 1 # assume ok
        for key in self.entries:
            entry = self.entries[key]
            error = self.errors[key]
            try:
                regex = entry.get()
                if regex != None and regex != "":
                    re.compile(regex)
                entry['style'] = 'TEntry'
                error['text'] = ''
            except Exception as e:
                rc = 0 # not ok
                entry['style'] = 'Pink.TEntry'
                error['text'] = str(e)
                self.initial_focus = entry
        return rc
    
    def update(self):
        # update preview list
        patterns = {key: self.entries[key].get() for key in self.entries}
        
        if patterns != self.patterns:
            self.titles = self.db.getPatternPlaylist(patterns)
            self.mclist.set(self.titles)
            self.patterns = patterns
    
    def apply(self):
        # put the regexes in result dict
        self.update()
        self.result = self.patterns
