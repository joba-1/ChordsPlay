'''
Created on 29.04.2016

@author: joba1
'''

import tkinter as tk
import ChordsPlay.dialog
import ChordsPlay.mclist
import logging

log = logging.getLogger(__name__)

class Selector(ChordsPlay.dialog.Dialog):
    '''
    Selection dialog for multi column lists
    Result is the index of the selected entry or None
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
        self.headings = headings
        self.items = items
        ChordsPlay.dialog.Dialog.__init__(self, parent, title)

    def body(self, master, label=None):
        # create dialog body.  
        # return widget that should have initial focus.
        
        self.mclist = ChordsPlay.mclist.MCList(self.headings, self.items, master)
        self.mclist.grid(column=0, row=0, sticky=tk.NSEW)
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)

        self.mclist.tree.bind("<Double-Button-1>", self.ok)
        self.mclist.tree["takefocus"] = True
        if len(self.items) > 0:
            self.mclist.tree.focus([self.items[0][0]])

        return self.mclist
    
    def apply(self):
        
        self.result = self.mclist.tree.index(self.mclist.tree.focus())
