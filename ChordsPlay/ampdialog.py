'''
Guitar-tone dialog (key g): the amp's presets and the playing song's cues.

Upper list: when the song switches to which slot (cues). A position slider seeks
the song, so several cues can be set in one go. Lower list: the amp's 100 presets,
the current one marked. Changes are stored at once and take effect immediately;
the dialog has only a Close button.

"Try" only switches the amp now (nothing is stored); "Save from …" stores a cue,
so the amp switches there every time the song plays.
'''

import time
import tkinter as tk
from tkinter import ttk, messagebox
import logging

import ChordsPlay.dialog
import ChordsPlay.mclist
from ChordsPlay.amp import build_cues, describe, fmt_ms, parse_ms, name_mismatch

log = logging.getLogger(__name__)

BASE = 'base'   # list id of the start cue that comes from the artist or the default
TICK_MS = 250   # refresh of position, slider and amp state
SEEK_HOLD = 0.8  # s after a user seek before the slider follows the player again
PREVIEW_MS = 2000  # double-click on a cue plays from this much before it
HELP = ('Try now: switch the amp to hear the selected preset; nothing is saved.\n'
        'Save from …: store it for this song from that time on; the amp then switches '
        'there every time the song plays.\n'
        'The slider or +/− (1 s) moves the song, to set several switches in one go. '
        'Double-click a cue to play from 2 s before it.')


class AmpDialog(ChordsPlay.dialog.Dialog):

    def __init__(self, parent, db, client, cues, song_id, position, seek=None, length=None):
        '''
        @param client: ChordsPlay.amp.AmpClient
        @param cues: ChordsPlay.amp.AmpCues (reloaded after every change)
        @param song_id: playing song or None
        @param position: callable returning the playing position in ms
        @param seek: callable(ms) moving playback, or None (no slider)
        @param length: callable returning the song length in ms (0 = unknown)
        '''
        self.db = db
        self.client = client
        self.cues = cues
        self.song_id = song_id
        self.position = position
        self.seek = seek if song_id is not None else None
        self.length = length or (lambda: 0)
        self.pos_ms = position() if song_id is not None else 0
        self.hold_until = 0         # monotonic time until which the slider ignores the player
        self.updating = False       # True while the slider is moved by code, not the user
        info = db.getPlayInfo(song_id) if song_id is not None else None
        self.song_title = info[0] if info else None
        self.artist = info[1] if info else None
        ChordsPlay.dialog.Dialog.__init__(self, parent, 'Guitar tone (Mustang presets)')

    # -- layout

    def body(self, master, label=None):
        self.status = ttk.Label(master, text='')
        self.status.grid(row=0, column=0, sticky=tk.W, pady=(0, 5))

        song = '{} - {}'.format(self.song_title, self.artist) if self.song_title else 'no song playing'
        ttk.Label(master, text='Cues for ' + song).grid(row=1, column=0, sticky=tk.W)
        self.cue_list = ChordsPlay.mclist.MCList(('Start', 'Slot', 'Preset', 'Note'), [], master)
        self.cue_list.tree.configure(height=5)
        self.cue_list.grid(row=2, column=0, sticky=tk.NSEW)
        self.cue_list.tree.bind('<Double-Button-1>', self.preview_cue)

        cue_buttons = ttk.Frame(master)
        cue_buttons.grid(row=3, column=0, sticky=tk.W, pady=5)
        for col, (text, command) in enumerate((
                ('Time… (t)', self.set_time),
                ('Delete (Del)', self.delete),
                ('Accept new name (r)', self.accept_name))):
            ttk.Button(cue_buttons, text=text, command=command).grid(row=0, column=col, padx=5)

        # song position: seeking here moves playback, and "Save from" uses it
        position = ttk.Frame(master)
        position.grid(row=4, column=0, sticky=tk.EW, pady=(5, 10))
        ttk.Label(position, text='Position').grid(row=0, column=0, padx=(0, 5))
        self.pos_var = tk.DoubleVar(value=self.pos_ms)
        self.scale = ttk.Scale(position, from_=0, to=max(1, self.length()), orient=tk.HORIZONTAL,
                               variable=self.pos_var, command=self.on_scale)
        self.scale.grid(row=0, column=1, sticky=tk.EW)
        self.pos_label = ttk.Label(position, width=len('59:59.5 / 59:59.5'), anchor=tk.W)
        self.pos_label.grid(row=0, column=2, padx=(5, 0))
        position.columnconfigure(1, weight=1)
        if self.seek is None:
            self.scale.state(['disabled'])

        ttk.Label(master, text='Amp presets').grid(row=5, column=0, sticky=tk.W)
        self.preset_list = ChordsPlay.mclist.MCList(('Slot', 'Preset', 'Now', 'Used'), [], master)
        self.preset_list.tree.configure(height=12)
        self.preset_list.grid(row=6, column=0, sticky=tk.NSEW)
        self.preset_list.tree.bind('<Double-Button-1>', lambda e: self.switch_now())

        preset_buttons = ttk.Frame(master)
        preset_buttons.grid(row=7, column=0, sticky=tk.W, pady=5)
        # fixed width: the label shows the position and must not resize the dialog
        self.here_button = ttk.Button(preset_buttons, command=self.add_here,
                                      width=len('Save from 59:59.5 (h)') + 1)
        buttons = (ttk.Button(preset_buttons, text='Try now (s)', command=self.switch_now),
                   ttk.Button(preset_buttons, text='Save from start (b)', command=self.add_start),
                   self.here_button,
                   ttk.Button(preset_buttons, text='Save for artist (a)', command=self.set_artist))
        for col, button in enumerate(buttons):
            button.grid(row=0, column=col, padx=5)

        self.help = ttk.Label(master, text=HELP, foreground='#555555', justify=tk.LEFT)
        self.help.grid(row=8, column=0, sticky=tk.EW, pady=(0, 5))

        master.columnconfigure(0, weight=1)
        master.rowconfigure(6, weight=1)

        for key, command in (('t', self.set_time), ('<Delete>', self.delete), ('r', self.accept_name),
                             ('s', self.switch_now), ('b', self.add_start), ('h', self.add_here),
                             ('a', self.set_artist), ('+', lambda: self.step(1000)),
                             ('-', lambda: self.step(-1000))):
            self.bind(key, lambda e, c=command: c())

        self.refresh()
        self.show_position()
        self.after_idle(self.fix_size)
        self.after(TICK_MS, self.tick)
        return self.preset_list.tree

    def fix_size(self):
        '''Freeze the window size after the first layout: changing texts and column widths
        must not make the dialog grow and shrink. The user can still resize it.'''
        # measure without the help text (one long line until it has a wrap length),
        # then wrap it to the width of the rest
        self.help.grid_remove()
        self.update_idletasks()
        width = self.winfo_reqwidth()
        self.help.configure(wraplength=width - 30)
        self.help.grid()
        self.update_idletasks()
        height = self.winfo_reqheight()
        self.geometry('{}x{}'.format(width, height))
        self.minsize(width, height // 2)

    def buttonbox(self):
        box = ttk.Frame(self)
        ttk.Button(box, text='Close', width=10, command=self.cancel,
                   default=tk.ACTIVE).grid(row=0, column=0, padx=5, pady=5)
        self.bind('<Escape>', self.cancel)
        self.bind('<Return>', self.cancel)
        box.grid()

    # -- content

    def refresh(self):
        snap = self.client.snapshot()
        state = snap['state']
        names = self.client.names()
        self.status.configure(text=describe(snap))

        rows = []
        for start, slot, slot_name, source in self.song_cues():
            notes = [] if source == 'song' else [source]
            renamed = name_mismatch(slot, slot_name, names)
            if renamed is not None:
                notes.append('now "{}"'.format(renamed))
            iid = str(start) if source == 'song' else BASE
            rows.append((iid, fmt_ms(start), slot, slot_name or '', ', '.join(notes)))
        self.cue_list.set(rows)

        usage = self.db.presetUsage()
        current = state.get('preset')
        self.shown = (current, state.get('connected'))
        mark = '◀' if state.get('connected') else '(last)'
        selected = self.selected(self.preset_list)
        rows = [(str(slot), slot, names[slot - 1] if slot <= len(names) else '',
                 mark if slot == current else '', usage.get(slot, ''))
                for slot in range(1, 101)]
        self.preset_list.set(rows)
        focus = selected or (str(current) if current else '1')
        self.preset_list.tree.selection_set(focus)
        self.preset_list.tree.focus(focus)
        self.preset_list.tree.see(focus)

    def tick(self):
        try:
            if not self.winfo_exists():
                return
            self.follow_player()
            state = self.client.snapshot()['state']
            if (state.get('preset'), state.get('connected')) != self.shown:
                self.refresh()      # the amp changed slot: move the marker
            else:
                self.status.configure(text=describe(self.client.snapshot()))
            self.after(TICK_MS, self.tick)
        except tk.TclError:
            pass    # dialog closed

    # -- helpers

    def song_cues(self):
        '''This dialog's song cues as (start, slot, slot_name, source), source song/artist/default.

        Computed here rather than taken from AmpCues, which follows the playing song
        and may move on while the dialog is open.'''
        if self.song_id is None:
            return []
        song = self.db.getSongPresets(self.song_id)
        base, source = self.db.getArtistPreset(self.artist), 'artist'
        if base is None and self.cues.default_slot:
            base, source = (self.cues.default_slot, None), 'default'
        starts = {start for start, _, _ in song}
        return [(start, slot, name, 'song' if start in starts else source)
                for start, slot, name in build_cues(song, base)]

    @staticmethod
    def selected(mclist):
        items = mclist.tree.selection() or (mclist.tree.focus(),)
        return items[0] if items and items[0] else None

    def selected_slot(self):
        item = self.selected(self.preset_list)
        return int(item) if item else None

    def slot_name(self, slot):
        names = self.client.names()
        return names[slot - 1] if slot <= len(names) and names[slot - 1] else None

    def here_ms(self):
        return max(0, self.pos_ms - self.pos_ms % 500)

    # -- position

    def show_position(self):
        length = self.length()
        text = fmt_ms(self.pos_ms) + (' / ' + fmt_ms(length) if length > 0 else '')
        self.pos_label.configure(text=text)
        self.here_button.configure(text='Save from {} (h)'.format(fmt_ms(self.here_ms())))

    def set_slider(self, ms):
        self.updating = True
        try:
            self.pos_var.set(ms)
        finally:
            self.updating = False

    def follow_player(self):
        '''Move the slider with playback, except right after the user moved it (playback
        needs a moment to report the new position).'''
        length = self.length()
        if length > 0 and float(self.scale.cget('to')) != length:
            self.updating = True        # a new range may move the value: not a user seek
            try:
                self.scale.configure(to=length)
            finally:
                self.updating = False
        if self.song_id is not None and time.monotonic() >= self.hold_until:
            self.pos_ms = self.position()
            self.set_slider(self.pos_ms)
        self.show_position()

    def seek_to(self, ms):
        length = self.length()
        ms = int(max(0, min(ms, length - 500 if length > 1000 else ms)))
        self.pos_ms = ms
        self.hold_until = time.monotonic() + SEEK_HOLD
        if self.seek:
            self.seek(ms)
        self.set_slider(ms)
        self.show_position()

    def on_scale(self, value):
        if not self.updating and self.seek:
            self.seek_to(float(value))

    def step(self, delta_ms):
        if self.seek:
            self.seek_to(self.pos_ms + delta_ms)

    def preview_cue(self, event=None):
        item = self.cue_list.tree.identify('item', event.x, event.y) if event else None
        item = item or self.selected(self.cue_list)
        if item and self.seek:
            start = 0 if item == BASE else int(item)
            self.seek_to(start - PREVIEW_MS)

    def need_song(self):
        if self.song_id is None:
            messagebox.showinfo('Guitar tone', 'Please start a song first', parent=self)
            return False
        return True

    def changed(self):
        self.cues.reload()
        self.refresh()

    # -- preset actions

    def switch_now(self):
        slot = self.selected_slot()
        if slot:
            self.client.request(slot)

    def add_start(self):
        self.add_cue(0)

    def add_here(self):
        self.add_cue(self.here_ms())

    def add_cue(self, start_ms):
        slot = self.selected_slot()
        if slot and self.need_song():
            self.db.setSongPreset(self.song_id, start_ms, slot, self.slot_name(slot))
            log.info('song %s: slot %d from %s', self.song_id, slot, fmt_ms(start_ms))
            self.changed()

    def set_artist(self):
        slot = self.selected_slot()
        if slot and self.need_song():
            self.db.setArtistPreset(self.artist, slot, self.slot_name(slot))
            log.info('artist %s: slot %d', self.artist, slot)
            self.changed()

    # -- cue actions

    def selected_cue(self):
        item = self.selected(self.cue_list)
        if item is None:
            messagebox.showinfo('Guitar tone', 'Select a cue first', parent=self)
        return item

    def set_time(self):
        item = self.selected_cue()
        if item is None:
            return
        if item == BASE:
            messagebox.showinfo('Guitar tone', 'This start cue comes from the artist or the default. '
                                'Add a song cue instead.', parent=self)
            return
        dlg = ChordsPlay.dialog.Dialog(self, 'Cue time', 'Start (m:ss)')
        if dlg.result is None:
            return
        try:
            new_ms = parse_ms(dlg.result)
        except ValueError:
            messagebox.showerror('Guitar tone', 'Use m:ss, for example 1:23 or 1:23.5', parent=self)
            return
        self.db.moveSongPreset(self.song_id, int(item), new_ms)
        self.changed()

    def delete(self):
        item = self.selected_cue()
        if item is None:
            return
        if item == BASE:
            if self.db.getArtistPreset(self.artist) and messagebox.askyesno(
                    'Guitar tone', 'Remove the slot for all songs of {}?'.format(self.artist),
                    parent=self):
                self.db.delArtistPreset(self.artist)
        else:
            self.db.delSongPreset(self.song_id, int(item))
        self.changed()

    def accept_name(self):
        item = self.selected_cue()
        if item is None:
            return
        start = 0 if item == BASE else int(item)
        for cue_start, slot, slot_name, _ in self.song_cues():
            if cue_start == start:
                current = name_mismatch(slot, slot_name, self.client.names())
                if current is not None:
                    self.db.renamePresetSlot(slot, current)
                    self.changed()
                return
