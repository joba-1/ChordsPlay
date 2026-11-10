'''
Guitar-tone dialog (key g): the amp's presets and the playing song's cues.

Upper list: when the song switches to which slot or tone (cues). A position slider
seeks the song, so several cues can be set in one go. Lower list: the amp's 100
presets, the current one marked. Changes are stored at once and take effect
immediately; the dialog has only a Close button.

"Try" only switches the amp now (nothing is stored); "Save from …" stores a cue,
so the amp switches there every time the song plays.

Right: the tone panel ("audition fiddling"). A tone is a base preset with a level
offset and knob settings, played by audition on the amp's live slot: nothing is
written to the amp, and tones are kept in ChordsPlay's database.
'''

import time
import tkinter as tk
from tkinter import ttk, messagebox
import logging

import ChordsPlay.dialog
import ChordsPlay.mclist
from ChordsPlay.amp import (build_cues, build_tone, describe, fmt_ms, parse_ms, name_mismatch,
                            tone_knobs)

log = logging.getLogger(__name__)

BASE = 'base'   # list id of the start cue that comes from the artist or the default
TICK_MS = 250   # refresh of position, slider and amp state
SEEK_HOLD = 0.8  # s after a user seek before the slider follows the player again
PREVIEW_MS = 2000  # double-click on a cue plays from this much before it
HELP = ('Try now: switch the amp to hear the selected preset; nothing is saved.\n'
        'Save from …: store it for this song from that time on; the amp then switches '
        'there every time the song plays.\n'
        'The slider or +/− (1 s) moves the song, to set several switches in one go. '
        'Double-click a cue to play from 2 s before it.\n'
        'Edit as tone (e): adjust level and knobs of the selected preset on the right; it plays '
        'on the live slot while you adjust. Tone from start/here (B/H) saves it as a cue.')
LEVEL_STEPS = (-6, -3, 0, 3, 6)     # quick level buttons, dB
PLAY_DELAY_MS = 300                 # wait this long after the last change before auditioning


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
                   ttk.Button(preset_buttons, text='Save for artist (a)', command=self.set_artist),
                   ttk.Button(preset_buttons, text='Edit as tone (e)', command=self.edit_tone))
        for col, button in enumerate(buttons):
            button.grid(row=0, column=col, padx=5)

        self.help = ttk.Label(master, text=HELP, foreground='#555555', justify=tk.LEFT)
        # spans both columns: wrapped to the window width, it must not widen the left one
        self.help.grid(row=8, column=0, columnspan=2, sticky=tk.EW, pady=(0, 5))

        self.tone = TonePanel(master, self)
        self.tone.grid(row=1, column=1, rowspan=7, sticky=tk.NSEW, padx=(15, 0))

        master.columnconfigure(0, weight=1)
        master.rowconfigure(6, weight=1)

        for key, command in (('t', self.set_time), ('<Delete>', self.delete), ('r', self.accept_name),
                             ('s', self.switch_now), ('b', self.add_start), ('h', self.add_here),
                             ('a', self.set_artist), ('+', lambda: self.step(1000)),
                             ('-', lambda: self.step(-1000)), ('e', self.edit_tone),
                             ('B', lambda: self.tone.cue(0)), ('H', lambda: self.tone.cue(self.here_ms())),
                             ('p', lambda: self.tone.play())):
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

    def grow_to_fit(self):
        '''Make the window larger if new content (tone knobs) needs it; never shrink.'''
        self.update_idletasks()
        width = max(self.winfo_width(), self.winfo_reqwidth())
        height = max(self.winfo_height(), self.winfo_reqheight())
        if (width, height) != (self.winfo_width(), self.winfo_height()):
            self.geometry('{}x{}'.format(width, height))

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
        for start, slot, slot_name, source, tone_id in self.song_cues():
            notes = [] if source == 'song' else [source]
            if tone_id is not None:
                tone = self.db.getTone(tone_id)
                notes.append('tone on {}'.format(tone['base_name']) if tone else 'deleted tone')
                renamed = name_mismatch(tone['base_slot'], tone['base_name'], names) if tone else None
                shown_slot = 'tone'
            else:
                renamed = name_mismatch(slot, slot_name, names)
                shown_slot = slot
            if renamed is not None:
                notes.append('now "{}"'.format(renamed))
            iid = str(start) if source == 'song' else BASE
            rows.append((iid, fmt_ms(start), shown_slot, slot_name or '', ', '.join(notes)))
        self.cue_list.set(rows)

        usage = self.db.presetUsage()
        current = state.get('preset')
        self.shown = (current, state.get('connected'))
        mark = '◀' if state.get('connected') else '(last)'
        selected = self.selected(self.preset_list)
        live = self.client.live_slot
        rows = [(str(slot), slot, names[slot - 1] if slot <= len(names) else '',
                 (mark if slot == current else '') + (' live' if slot == live else ''),
                 usage.get(slot, '') if slot != live else '')
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
            self.tone.tick()
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
        starts = {cue[0] for cue in song}
        return [(start, slot, name, 'song' if start in starts else source, tone_id)
                for start, slot, name, tone_id in build_cues(song, base)]

    @staticmethod
    def selected(mclist):
        items = mclist.tree.selection() or (mclist.tree.focus(),)
        return items[0] if items and items[0] else None

    def selected_slot(self, allow_live=False):
        item = self.selected(self.preset_list)
        slot = int(item) if item else None
        if slot is not None and slot == self.client.live_slot and not allow_live:
            messagebox.showinfo('Guitar tone', 'Slot {} is reserved for tones (auditions). '
                                'Choose another preset.'.format(slot), parent=self)
            return None
        return slot

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
        for cue_start, slot, slot_name, _, tone_id in self.song_cues():
            if cue_start == start:
                if tone_id is not None:
                    tone = self.db.getTone(tone_id)
                    current = tone and name_mismatch(tone['base_slot'], tone['base_name'],
                                                     self.client.names())
                    if current is not None and tone:
                        self.db.saveTone(tone['name'], tone['base_slot'], current, tone['level_db'],
                                         tone['params'])
                        self.changed()
                    return
                current = name_mismatch(slot, slot_name, self.client.names())
                if current is not None:
                    self.db.renamePresetSlot(slot, current)
                    self.changed()
                return

    def edit_tone(self):
        slot = self.selected_slot()
        if slot:
            self.tone.set_base(slot, self.slot_name(slot))


class TonePanel(ttk.Frame):
    '''
    Audition fiddling: a base preset with a level offset and knob settings, played
    on the live slot while you adjust it. Saved tones live in ChordsPlay's database.
    '''

    def __init__(self, master, dialog):
        ttk.Frame.__init__(self, master)
        self.dialog = dialog
        self.db = dialog.db
        self.client = dialog.client
        self.base_slot = None
        self.base_name = None
        self.base_doc = None
        self.params = {}
        self.tone_id = None             # the saved tone being edited, if any
        self.modified = False
        self.pending = None             # after() id of a scheduled audition
        self.level = tk.DoubleVar(value=0)
        self.live = tk.BooleanVar(value=True)

        ttk.Label(self, text='Tone: plays on slot {},\nnothing is saved on the amp'.format(
            self.client.live_slot), justify=tk.LEFT).grid(row=0, column=0, columnspan=3, sticky=tk.W)
        self.base_label = ttk.Label(self, text='Select a preset, then Edit as tone (e)',
                                    foreground='#555555', wraplength=330)
        self.base_label.grid(row=1, column=0, columnspan=3, sticky=tk.W, pady=(0, 5))

        saved = ttk.Frame(self)
        saved.grid(row=2, column=0, columnspan=3, sticky=tk.W, pady=(0, 5))
        ttk.Label(saved, text='Saved tones').grid(row=0, column=0, padx=(0, 5))
        self.tones = ttk.Combobox(saved, state='readonly', width=24)
        self.tones.grid(row=0, column=1)
        ttk.Button(saved, text='Load', command=self.load).grid(row=0, column=2, padx=5)
        ttk.Button(saved, text='Delete', command=self.delete_tone).grid(row=0, column=3)

        level = ttk.Frame(self)
        level.grid(row=3, column=0, columnspan=3, sticky=tk.EW)
        ttk.Label(level, text='Level dB').grid(row=0, column=0, padx=(0, 5))
        tk.Scale(level, from_=-12, to=12, resolution=1, orient=tk.HORIZONTAL, length=200,
                 variable=self.level, command=lambda _: self.on_change()).grid(row=0, column=1)
        steps = ttk.Frame(level)
        steps.grid(row=1, column=1, sticky=tk.W)
        for col, step in enumerate(LEVEL_STEPS):
            ttk.Button(steps, text='{:+d}'.format(step) if step else '0', width=3,
                       command=lambda v=step: self.set_level(v)).grid(row=0, column=col, padx=1)

        self.knob_frame = ttk.Frame(self)
        self.knob_frame.grid(row=4, column=0, columnspan=3, sticky=tk.NSEW, pady=5)

        controls = ttk.Frame(self)
        controls.grid(row=5, column=0, columnspan=3, sticky=tk.W)
        ttk.Checkbutton(controls, text='Play while adjusting', variable=self.live).grid(row=0, column=0)
        ttk.Button(controls, text='Play (p)', command=self.play).grid(row=0, column=1, padx=5)
        ttk.Button(controls, text='Reset', command=self.reset).grid(row=0, column=2)

        cue = ttk.Frame(self)
        cue.grid(row=6, column=0, columnspan=3, sticky=tk.W, pady=(5, 0))
        ttk.Button(cue, text='Save tone…', command=self.save).grid(row=0, column=0, sticky=tk.W)
        ttk.Button(cue, text='Tone from start (B)', command=lambda: self.cue(0)).grid(
            row=1, column=0, sticky=tk.W, pady=(5, 0))
        ttk.Button(cue, text='Tone from here (H)',
                   command=lambda: self.cue(self.dialog.here_ms())).grid(row=1, column=1, padx=5, pady=(5, 0))
        self.columnconfigure(0, weight=1)
        self.refresh_tones()

    # -- state

    def refresh_tones(self):
        self.saved = {t['name']: t for t in self.db.getTones()}
        self.tones.configure(values=sorted(self.saved))

    def set_base(self, slot, name, level=0.0, params=None, tone_id=None):
        self.base_slot, self.base_name = slot, name
        self.base_doc = self.client.preset(slot)        # None until fetched; see tick()
        self.params = dict(params or {})
        self.tone_id = tone_id
        self.modified = False
        self.level.set(level)
        self.build_knobs()
        if self.base_doc is not None:
            self.on_change(force=True)

    def tick(self):
        if self.base_slot is not None and self.base_doc is None:
            self.base_doc = self.client.preset(self.base_slot)
            if self.base_doc is not None:
                self.build_knobs()
                self.on_change(force=True)

    def name(self):
        if self.tone_id is not None and not self.modified:
            tone = self.db.getTone(self.tone_id)
            if tone:
                return tone['name']
        level = int(round(self.level.get()))
        return '{}{}{}'.format(self.base_name or 'Slot {}'.format(self.base_slot),
                               ' {:+d} dB'.format(level) if level else '', ' *' if self.params else '')

    def doc(self):
        if self.base_doc is None:
            return None
        level = self.db.getPresetLevel(self.base_slot)
        return build_tone(self.base_doc, self.level.get(), self.params, name=self.name()[:30],
                          correction_db=level[0] if level else 0.0)

    # -- knobs

    def build_knobs(self):
        for child in self.knob_frame.winfo_children():
            child.destroy()
        if self.base_slot is None:
            return
        if self.base_doc is None:
            self.base_label.configure(text='Base {} {}: loading…'.format(self.base_slot, self.base_name or ''))
            return
        level = self.db.getPresetLevel(self.base_slot)
        self.base_label.configure(text='Base {} {}{}'.format(
            self.base_slot, self.base_name or '',
            ' (level matched {:+.1f} dB)'.format(level[0]) if level else ''))
        for row, knob in enumerate(tone_knobs(self.base_doc)):
            key = knob['key']
            if knob['kind'] == 'switch':
                param = key
                var = tk.BooleanVar(value=not self.params[param] if param in self.params else knob['value'])
                ttk.Checkbutton(self.knob_frame, text=knob['label'], variable=var,
                                command=lambda k=param, v=var: self.set_param(k, not v.get())
                                ).grid(row=row, column=0, columnspan=2, sticky=tk.W)
            else:
                var = tk.DoubleVar(value=self.params.get(key, knob['value']))
                ttk.Label(self.knob_frame, text=knob['label']).grid(row=row, column=0, sticky=tk.W)
                tk.Scale(self.knob_frame, from_=0, to=1, resolution=0.01, orient=tk.HORIZONTAL,
                         length=160, variable=var, showvalue=True,
                         command=lambda value, k=key: self.set_param(k, round(float(value), 3))
                         ).grid(row=row, column=1, sticky=tk.W)
        self.dialog.grow_to_fit()

    def set_param(self, key, value):
        if self.params.get(key) == value:
            return
        self.params[key] = value
        self.on_change()

    def set_level(self, value):
        self.level.set(value)
        self.on_change()

    def reset(self):
        if self.base_slot is not None:
            self.set_base(self.base_slot, self.base_name)

    # -- playing

    def on_change(self, force=False):
        if not force:
            self.modified = True
        if self.live.get() or force:
            if self.pending is not None:
                self.after_cancel(self.pending)
            self.pending = self.after(PLAY_DELAY_MS, self.play)

    def play(self):
        self.pending = None
        doc = self.doc()
        if doc is not None:
            self.client.audition(doc)

    # -- saving

    def save(self):
        if self.base_slot is None:
            messagebox.showinfo('Guitar tone', 'Select a preset and press Edit as tone first',
                                parent=self.dialog)
            return None
        dlg = ChordsPlay.dialog.Dialog(self.dialog, 'Save tone', 'Name')
        if dlg.result is None or not dlg.result.strip():
            return None
        self.tone_id = self.db.saveTone(dlg.result.strip()[:30], self.base_slot, self.base_name,
                                        self.level.get(), self.params)
        self.modified = False
        self.refresh_tones()
        self.tones.set(dlg.result.strip()[:30])
        return self.tone_id

    def cue(self, start_ms):
        if not self.dialog.need_song():
            return
        tone_id = self.tone_id if self.tone_id is not None and not self.modified else self.save()
        if tone_id is None:
            return
        self.db.setSongTone(self.dialog.song_id, start_ms, tone_id, self.client.live_slot)
        log.info('song %s: tone %s from %s', self.dialog.song_id, tone_id, fmt_ms(start_ms))
        self.dialog.changed()

    def load(self):
        tone = self.saved.get(self.tones.get())
        if tone:
            self.set_base(tone['base_slot'], tone['base_name'], tone['level_db'], tone['params'], tone['id'])

    def delete_tone(self):
        tone = self.saved.get(self.tones.get())
        if not tone:
            return
        used = self.db.toneUsage().get(tone['id'], 0)
        if messagebox.askyesno('Guitar tone', 'Delete tone "{}"{}?'.format(
                tone['name'], ' and its {} cue(s)'.format(used) if used else ''), parent=self.dialog):
            self.db.delTone(tone['id'])
            if self.tone_id == tone['id']:
                self.tone_id = None
            self.refresh_tones()
            self.tones.set('')
            self.dialog.changed()
