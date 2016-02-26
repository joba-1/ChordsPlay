'''
Created on 25.02.2016

@author: joba1
'''

import tkinter as tk


class Gui(object):
    '''
    Create GUI to display chords
    '''

    def __init__(self, title):
        '''
        Constructor
        '''
        self.root = tk.Tk()
        self.root.title(title)
        
        b_quit = tk.Button(self.root, text="Quit", command=lambda: self.root.quit())
        b_quit.grid(row=0, column=0, padx=15, pady=15)
    
    
    def mainloop(self):
        self.root.mainloop()
        