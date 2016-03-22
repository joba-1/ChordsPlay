'''
Created on 25.02.2016

@author: joba1
'''

import tkinter as tk

import ChordsPlay.play

class Gui(object):
    '''
    Create GUI to display chords
    '''

    def __init__(self, title):
        '''
        Constructor
        '''
        # debug
        songlist = [
            r'C:\Users\d064412\workspace\chordsplay\tests\Woodkid\The Golden Age\02 - Run Boy Run.ogg', 
            r'C:\Users\d064412\workspace\chordsplay\tests\Kansas\Leftoverture\09 Carry on Wayward Son.m4a',
            r'C:\Users\d064412\workspace\chordsplay\tests\Cake\Short Skirt Long Jacket.mp3']

        self.root = tk.Tk()
        self.root.title(title)
        self.player = ChordsPlay.play.Play(self.root, songlist)
        
        b_quit = tk.Button(self.root, text="Quit", command=self.quit)
        b_quit.grid(row=0, column=0, padx=15, pady=15)

        b_play = tk.Button(self.root, text="Play", command=self.player.play)
        b_play.grid(row=0, column=1, padx=15, pady=15)

        b_pause = tk.Button(self.root, text="Pause", command=self.player.pause)
        b_pause.grid(row=0, column=2, padx=15, pady=15)

        b_next = tk.Button(self.root, text="Next", command=self.player.next)
        b_next.grid(row=0, column=3, padx=15, pady=15)

        b_prev = tk.Button(self.root, text="Prev", command=self.player.prev)
        b_prev.grid(row=0, column=4, padx=15, pady=15)

    
    def mainLoop(self):
        self.root.mainloop()
        
    def quit(self):
        self.root.quit()
        self.root.destroy()