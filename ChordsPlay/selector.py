'''
Created on 29.04.2016

@author: joba1
'''

import tkinter as tk
from tkinter import ttk
import ChordsPlay.dialog
import ChordsPlay.mclist
import logging

log = logging.getLogger(__name__)

class Selector(ChordsPlay.dialog.Dialog):
    '''
    Selection dialog for multi column lists
    Result is the index of the selected entry or None
    '''

    def __init__(self, parent, title, headings, items, key_pos=None):
        '''
        Show dialog to select one (multi column) item from a list
        @param parent: Parent widget
        @param title: Dialog title
        @param headings: Tuple or similar of column names
        @param items: List of tuples or similar of items to select from
        @param key_pos: Optional position of shortcut keys in items tuple 
        @return: Index of selected item or None
        '''
        log.debug("__init__(items={}, key_pos={})".format(items, key_pos))
        self.headings = headings
        self.items = items
        self.key_pos = key_pos
        ChordsPlay.dialog.Dialog.__init__(self, parent, title)

    def select(self, item):
        log.debug("select item {}".format(item))
        self.mclist.tree.focus([item])
        self.ok()
        
    def body(self, master, label=None):
        # create dialog body.  
        # return widget that should have initial focus.
        
        self.mclist = ChordsPlay.mclist.MCList(self.headings, self.items, master)
        self.mclist.grid(column=0, row=0, sticky=tk.NSEW)
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)

        self.mclist.tree.bind("<Double-Button-1>", self.ok)
#        self.mclist.tree["takefocus"] = True
#        if len(self.items) > 0:
#            self.mclist.tree.focus([self.items[0][0]])

        if self.key_pos != None:
            for item in self.items:
                key = item[self.key_pos]
                if key != None and key != '':
                    log.debug("register key '{}' for item id '{}'".format(key, item[0]))
                    self.bind(key, lambda e, i=item[0]: self.select(i))
            
        return self.mclist
    
    def apply(self):
        
        self.result = self.mclist.tree.index(self.mclist.tree.focus())


class TagSelector(Selector):
    '''
    Selection dialog for tags
    Result is the index of the selected tag or None
    It is possible to add or remove tags
    '''

    def __init__(self, parent, title, headings, items):
        '''
        Show dialog to select one (multi column) item from a list
        @param parent: Parent widget
        @param title: Dialog title
        @param headings: Tuple or similar of column names
        @param items: List of tuples or similar of items to select from
        @return: Index of selected item or None
        '''
        log.debug("__init__")
        ChordsPlay.selector.Selector.__init__(self, parent, title, headings, items, key_pos=1)

    def buttonbox(self):
        # add standard button box. override if you don't want the
        # standard buttons

        box = ttk.Frame(self)

        w = ttk.Button(box, text="OK", width=10, command=self.ok, default=tk.ACTIVE)
        w.grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        w = ttk.Button(box, text="Cancel", width=10, command=self.cancel)
        w.grid(row=0, column=1, sticky=tk.W, padx=5, pady=5)

        w = ttk.Button(box, text="New", width=10, command=self.insert)
        w.grid(row=0, column=3, sticky=tk.W, padx=5, pady=5)
        w = ttk.Button(box, text="Delete", width=10, command=self.delete)
        w.grid(row=0, column=4, sticky=tk.W, padx=5, pady=5)

        self.bind("<Return>", self.ok)
        self.bind("<Escape>", self.cancel)
        self.bind("<Insert>", self.insert)
        self.bind("<Delete>", self.delete)

        box.grid()
        
    def insert(self):
        log.debug("insert")
        dlg = ChordsPlay.dialog.Dialog(self, 'Add new tag', 'Tag[, key]')
        if dlg.result != None:
            tokens = dlg.result.split(',')
            if len(tokens) > 1:
                self.db.addTag(tokens[0].strip(), tokens[1].strip())
            else:
                self.db.addTag(tokens[0].strip(), None)
    
    def delete(self):
        log.debug("delete")
        # ask