'''
Created on 09.04.2016

@author: joba1
'''

import tkinter as tk
from tkinter import ttk
import random
import re
import logging

log = logging.getLogger(__name__)

class MCList(ttk.Frame):
    '''
    Scrolled list of column tuples
    Register callback on selected entries
    '''

    def __init__(self, headings, data, master=None, **kw):
        ttk.Frame.__init__(self, master, **kw)

        # adjust row height in tree view
        font_height = tk.font.Font(font='TkDefaultFont').metrics('linespace') * 1.0
        style = ttk.Style()
        style.configure("Treeview.Heading", rowheight=int(font_height))
        style.configure("Treeview", rowheight=int(font_height))

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
        widths = {column: 0 for column in headings}
        for row in data:
            item = self.tree.insert('', 'end', iid=row[0], values=row[1:])
            # adjust column widths
            for column in headings:
                value = self.tree.set(item, column=column)
                widths[column] += self.font.measure(value)

        if len(data) > 0:
            for column in headings:
                width = widths[column]//len(data)*120//100
                if self.tree.column(column, 'width') < width:
                    self.tree.column(column, width=width)
     
    def len(self):
        return len(self.tree.get_children(''))
    
    def get(self, pos=None):
        items = self.tree.get_children('')
        if pos == None:
            return items
        return self.tree.set(items[pos])
    
    def set(self, data):   
        # configure headings
        self.ascending = True
        for column in self.headings:
            self.tree.column(column, width=self.font.measure(column.title()))
             
        # remove old rows
        children = self.tree.get_children('')
        if len(children) > 0:
            log.debug("set: first child and len to delete: {}, {}".format(children[0], len(children)))
            self.tree.delete(*children)

        # add data columns
        widths = {column: 0 for column in self.headings}
        for row in data:
            item = self.tree.insert('', 'end', iid=row[0], values=row[1:])
            # adjust column widths
            for column in self.headings:
                value = self.tree.set(item, column=column)
                widths[column] += self.font.measure(value)

        if len(data) > 0:
            for column in self.headings:
                width = widths[column]//len(data)*120//100
                if self.tree.column(column, 'width') < width:
                    self.tree.column(column, width=width)
        
    def sort(self, column, ascending=True):

        def atoi(text):
            return int(text) if text.isdigit() else text

        def natural(pair):
            return [ atoi(text) for text in re.split('(\d+)', pair[0]) ]
        
        # list of column values with column id          
        data = [(self.tree.set(child, column), child) for child in self.tree.get_children('')]
        data.sort(key=natural, reverse=not ascending)
        
        for index, pair in enumerate(data):
            self.tree.move(pair[1], '', index)   # pair[1] = column id
         
        # next time reverse sort
        self.ascending = not ascending
        
    def shuffle(self):

        # list of column values with column id          
        items = list(self.tree.get_children(''))
        random.shuffle(items)
        
        for index, item in enumerate(items):
            self.tree.move(item, '', index)   # pair[1] = column id

        # next time ascending sort
        self.ascending = True
