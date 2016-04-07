'''
Created on 01.04.2016

@author: joba1
'''

import tkinter as tk
from tkinter import ttk

class Dialog(tk.Toplevel):
    '''
    A modal dialog window.
    Derive from it, implement body(), apply() and optionaly validate() and buttonbox()
    Copy from effbot.org
    '''
    def __init__(self, parent, title=None, label=None):

        tk.Toplevel.__init__(self, parent)
        self.transient(parent)

        if title:
            self.title(title)

        self.parent = parent

        self.result = None

        body = ttk.Frame(self)
        self.initial_focus = self.body(body, label=label)
        body.grid(row=0, column=0, padx=5, pady=5, sticky=tk.NSEW)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self.buttonbox()

        self.grab_set()

        if not self.initial_focus:
            self.initial_focus = self

        self.protocol("WM_DELETE_WINDOW", self.cancel)

        self.geometry("+%d+%d" % (parent.winfo_rootx()+50,
                                  parent.winfo_rooty()+50))

        self.initial_focus.focus_set()

        self.wait_window(self)

    #
    # construction hooks

    def body(self, master, label=None):
        # create dialog body.  return widget that should have
        # initial focus.  this method should be overridden
        
        ttk.Label(master, text=label).grid(row=0, column=0, sticky=tk.W)
        self.entry = ttk.Entry(master)
        self.entry.grid(row=0, column=1, sticky=tk.EW)
        
        return self.entry

    def buttonbox(self):
        # add standard button box. override if you don't want the
        # standard buttons

        box = ttk.Frame(self)

        w = ttk.Button(box, text="OK", width=10, command=self.ok, default=tk.ACTIVE)
        w.grid(row=0, sticky=tk.W, padx=5, pady=5)
        w = ttk.Button(box, text="Cancel", width=10, command=self.cancel)
        w.grid(row=0, side=tk.LEFT, padx=5, pady=5)

        self.bind("<Return>", self.ok)
        self.bind("<Escape>", self.cancel)

        box.grid()

    #
    # standard button semantics

    def ok(self, event=None):

        if not self.validate():
            self.initial_focus.focus_set() # put focus back
            return

        self.withdraw()
        self.update_idletasks()

        self.apply()

        self.cancel()

    def cancel(self, event=None):

        # put focus back to the parent window
        self.parent.focus_set()
        self.destroy()

    #
    # command hooks

    def validate(self):

        return 1 # override

    def apply(self):
        
        self.result = self.entry.get()
