'''
Created on 09.04.2016

@author: joba1
'''

import tkinter as tk
from tkinter import ttk
import logging

log = logging.getLogger(__name__)

class MCList(ttk.Frame):
    '''
    Scrolled list of column tuples
    Register callback on selected entries
    '''

    def __init__(self, headings, data, master=None, **kw):
        ttk.Frame.__init__(self, master, **kw)

        self.headings = headings
        self.tree = ttk.Treeview(self, columns=headings, show='headings', selectmode='browse')

        # add scroll bar and link to tree         
        yscrollbar = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree['yscroll'] = yscrollbar.set
         
        # position tree and scroll bars in frame
        self.tree.grid(row=0, column=0, sticky=tk.NSEW)
        yscrollbar.grid(row=0, column=1, sticky=tk.NS)
         
        # tree resizes with frame
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        
        # configure headings
        self.font = tk.font.Font()
        self.ascending = True
        for column in headings:
            self.tree.heading(column, text=column.title(),
                command=lambda c=column: self.sort(c, self.ascending))           
            self.tree.column(column, width=self.font.measure(column.title()))
             
        # add data columns
        for row in data:
            self.tree.insert('', 'end', values=row)
             
            # adjust column widths
            for index, value in enumerate(row):
                width = tk.font.Font().measure(value)
                if self.tree.column(headings[index], 'width') < width:
                    self.tree.column(headings[index], width=width)
     
    def set(self, data):   
        # configure headings
        self.ascending = True
        for column in self.headings:
            self.tree.column(column, width=self.font.measure(column.title()))
             
        # remove old rows
        for child in self.tree.get_children(''):
            self.tree.delete(child)

        # add data columns
        for row in data:
            self.tree.insert('', 'end', values=row)
             
            # adjust column widths
            for index, value in enumerate(row):
                # log.debug("set: index {}: '{}'".format(index, value))
                width = self.font.measure(value)
                if self.tree.column(self.headings[index], 'width') < width:
                    self.tree.column(self.headings[index], width=width)
        
    def sort(self, column, ascending=True):

        # list of column values with column id          
        data = [(self.tree.set(child, column), child) for child in self.tree.get_children('')]
        data.sort(reverse=not ascending)
        
        for index, pair in enumerate(data):
            self.tree.move(pair[1], '', index)   # pair[1] = column id
         
        # next time reverse sort
        self.ascending = not ascending
