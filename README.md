# ChordsPlay

Play your music collection while showing the lyrics and guitar chords,
fetched automatically from Ultimate Guitar.

---

## Install

```bash
pip install git+ssh://git@github.com/joba-1/ChordsPlay.git
chordsplay.py /path/to/your/music
```

Dependencies are pulled automatically: `mutagen`, `python-vlc`, `curl_cffi`.

The first run creates `~/.chordsplay.py.db` (a SQLite database) and starts
scanning. Each new song is matched against Ultimate Guitar in the
background; lyrics and chords appear as soon as a match is found. You can
play music while the scan runs.

To start without arguments later (the music directory is remembered):

```bash
chordsplay.py
```

To add another music directory, pass it as an argument:

```bash
chordsplay.py /more/music
```

---

## Keyboard shortcuts

Most of the UI is keyboard-driven.

### Playback
| Key | Action |
|---|---|
| `Space` / `m` | Pause / resume |
| `Enter` / `n` / right-click | Next song |
| `Backspace` / `p` / middle-click | Previous song |
| `s` | Shuffle playlist |
| `+` / `-` | Seek +1s / -1s |
| `0` | Restart current song |
| Double-click song | Jump to that song |

### Dialogs
| Key | Action |
|---|---|
| `f` | Filter (choose what to play) |
| `t` | Tags (label the current song) |
| `a` | Audio output device |
| `A` | Cycle to next audio output |
| `g` | Guitar tone: Mustang Micro Plus presets for this song ([see below](#guitar-tone-mustang-micro-plus)) |
| `G` | Store the amp's current preset for the song, from the current position |
| `l` | Toggle the playlist panel |
| `F11` | Toggle fullscreen |
| `Escape` | Leave fullscreen |
| `q` | Quit (same as closing the window) |

---

## Filter dialog

Press `f`. Every field is a Python regular expression (or empty to match
anything). The preview list updates when you tab between fields.

| Field | Matches against |
|---|---|
| Titles | song title |
| Artists | artist name |
| Albums | album name |
| Capos | capo fret number as text (e.g. `0` for no capo, `3` for capo 3) |
| Required Chords | every chord listed here must be in the song |
| Accepted Chords | the song may use only these chords (combined with Required, the song must use only required + accepted) |
| Tag Keys | tag key character (e.g. `f` for Favourites, `[fl]` for Favourite or Easy) |

Example: songs with no capo that contain only easy chords:
- Capos: `^0$`
- Accepted Chords: `^(C|D|E|G|Am|Em)$`

Dialog buttons:
- **Ok** — apply this filter as the current playlist
- **Cancel** — close without changing anything
- **Save** / **Save As** — store this filter under a name
- **Load** — pick a previously saved filter
- **Import** / **Export** — write/read a `.pat` JSON file

Whatever you last applied with **Ok** is auto-saved as `__previous` and
reloaded next time you open the dialog.

---

## Tags

Press `t` to tag the *currently playing* song. The dialog shows each tag
with a single-letter shortcut. Pressing the letter toggles that tag.

Default tags (German names, originally):

| Key | Name | Meaning |
|---|---|---|
| `f` | Favorit | Favourite |
| `s` | Schwer | Hard to play |
| `l` | Leicht | Easy to play |
| `u` | Unfertig | Incomplete tab |
| `c` | Capo | Needs a capo |
| `x` | Fixiert | **Locked — do not re-scan** |

The `x` (Fixiert) tag is special: it prevents the background scanner
from ever overwriting that song's lyrics/chords. Use it once you've
hand-edited a song you want to keep.

You can add or remove tags from the tag dialog itself.

---

## Guitar tone (Mustang Micro Plus)

ChordsPlay can switch a Fender Mustang Micro Plus to the preset you stored for
a song. A song can have **several presets at different times**, for example a
quiet acoustic intro and a loud chorus. Design: [docs/spec-amp.md](docs/spec-amp.md).

**Store a tone:** set it on the amp (buttons or the Fender Tone app), then
press `G`. Under 3 s into the song it becomes the song's start preset;
later, it applies from the current position on. A stored preset within 2 s is
replaced.

**The `g` dialog** (or the *Tone* button) lists the song's cues, a position
slider and the amp's 100 presets (current one marked ◀, *Used* = how many songs
and artists use it). Pick a preset, then:

| Key | Button | Action |
|---|---|---|
| `s` | Try now | Switch the amp now, to hear the preset. **Nothing is saved.** |
| `b` | Save from start | Save it as the song's start preset |
| `h` | Save from m:ss | Save it from the current position on (shown on the button) |
| `a` | Save for artist | Save it for every song of this artist without its own start preset |

*Try now* changes only the amp, and only until the next cue. *Save from …* stores
a cue, so the amp switches there every time the song plays.

**Setting several switches in one go:** drag the *Position* slider or press
`+`/`-` (1 s), which moves the song itself. Then pick a preset and press `h`, and
repeat. Double-click a cue to play from 2 s before it and hear the switch.

For a selected cue: `t` changes its time (`m:ss`), `Del` deletes it, and `r`
accepts a new preset name. If a slot's name changed since you stored it, the
preset there was probably replaced, so ChordsPlay doesn't switch and shows a
warning in the window title until you accept the new name. The title also
shows the amp's state, e.g. `[amp 7 Hey Jimi]`.

Which preset plays: the song's cues; before the first one, the artist's preset,
else `amp_default_slot`; with none of these the amp is left alone.

**Tones (the panel on the right of the `g` dialog).** A tone is one of the amp's
presets with your own level and knob settings, for example "Echo Dome +3 dB with
less reverb" for a solo.
* **Nothing is written to the amp.** ChordsPlay plays a tone as an *audition* on a
  reserved slot (`amp_live_slot`, default 100). The amp's 100 presets and their names
  stay as they are, and switching tones costs no flash writes.
* **Editing:**
  1. Select a preset and press `e` (Edit as tone).
  2. Move the level slider (−12 to +12 dB; quick buttons −6, −3, 0, +3, +6) and the
     knobs: amp gain, bass, mid, treble, and for each effect on/off, mix, decay and
     so on.
  3. With *Play while adjusting* on, you hear each change on the amp a moment later.
* **Using it:**
  * `B` / `H` (*Tone from start* / *Tone from here*) save the tone and make it a cue
    of the song.
  * *Save tone…* keeps it under a name; *Load* and *Delete* manage saved tones.
  * In the cue list a tone shows as `tone`.

**Setup** (Linux, once):

1. BlueZ 5.84+ with `Experimental = true` and the amp paired over Bluetooth LE
   as well as for audio: maneline's `docs/MUSTANG_MICRO_PLUS_LINUX.md`.
2. `contrib/install-amp-agent.sh` installs the amp agent (maneline's
   `mmp_web.py`) as the systemd user unit `chordsplay-amp-agent.service`.
3. Add `amp_url` and `amp_unit` to the configuration file (below).

ChordsPlay never talks Bluetooth itself: the agent owns the amp's connection.
ChordsPlay starts the agent while the amp is connected as an audio output and
stops it when the amp is gone or ChordsPlay quits. The first switch after the
amp appears takes a few seconds while the agent connects; during a song,
switching takes well under a second.

---

## Configuration file

`~/.chordsplay.py.cfg` (named after the program, `-c` to use another) holds
defaults so you don't have to pass them every time:

```ini
[chordsplay.py]
database  = /path/to/your/songs.db
user      = yourname
verbosity = 0

# central chord sync (sync_service/README.md)
sync_url   = http://host6:8615
sync_token = <token>

# Mustang Micro Plus guitar tone (see "Guitar tone" above)
amp_url  = http://localhost:8081
amp_unit = chordsplay-amp-agent.service
# amp_sink = Mustang Micro Plus      text in the amp's audio output name
# amp_default_slot = 1               preset for songs without one
# amp_lead_ms = 300                  switch this much before a cue
# amp_live_slot = 100                reserved slot for tones (auditions)
```

The file holds the sync token, so keep it private: `chmod 600 ~/.chordsplay.py.cfg`.

### Quitting

`q`, the window's close button, closing the terminal ChordsPlay was started from,
`kill` and Ctrl-C all end it the same way:
* the music stops at once;
* the window closes;
* the amp agent is stopped if ChordsPlay started it;
* the scanner and the database are closed.

If the scanner is stuck in a slow request to Ultimate Guitar, ChordsPlay waits at
most 3 s for it; that song is simply fetched again next time.

While ChordsPlay runs it keeps the screen and the computer awake (a logind
inhibitor). The inhibitor ends with ChordsPlay, even after a crash.

---

## Command-line options

| Option | Purpose |
|---|---|
| `-c FILE` / `--config` | Use a different config file (default: `~/.chordsplay.py.cfg`) |
| `-d FILE` / `--database` | Use a different database file |
| `-u NAME` / `--user` | Database user (default: your OS username) |
| `-l FILE` / `--logfile` | Write a full DEBUG log to a file (console stays at INFO) |
| `-v` / `--verbose` | Verbose console output |
| `-t FILE` / `--testfile` | Run one test song and dump HTML to file (for debugging chord scraper) |
| `-f` / `--fix` | Run one-off database fix-ups and exit |
| `--sync-url URL` / `--sync-token T` | Central chord-sync service (prefer the config file) |
| `--sync-seed` | Push all local chord data to the sync service once, then exit |

---

## Troubleshooting

**Songs never get lyrics.** Check the log (`-l chordsplay.log`). The most
common cause is Ultimate Guitar returning *No chords for song …* —
either the song genuinely isn't on UG, or the search title differs from
the file's ID3 tag. You can `t` → mark the song with `x` to stop the
scanner retrying it.

**Wrong capo detected.** Mark the song with the `x` (Fixiert) tag and
manually clear the value:

```bash
sqlite3 ~/.chordsplay.py.db "UPDATE songs SET capo = NULL WHERE title = '...' AND artist = '...';"
```

**Database locked errors during scan.** Should not happen in current
versions — but if you see them, share the log line so it can be fixed.

**Force a rescan of a song:**

```bash
sqlite3 ~/.chordsplay.py.db "UPDATE songs SET chord_update = NULL WHERE title = '...' AND artist = '...';"
```

**Find songs the scanner hasn't reached yet:**

```bash
sqlite3 ~/.chordsplay.py.db "SELECT count(*) FROM songs WHERE chord_update IS NULL;"
```

---

## Desktop launcher

`~/.local/share/applications/ChordsPlay.desktop`:

```
[Desktop Entry]
Type=Application
Name=ChordsPlay
Exec=chordsplay.py /path/to/music
Icon=/usr/share/icons/Adwaita/scalable/places/folder-music-symbolic.svg
Terminal=true
StartupNotify=true
```

---

## Developer notes

* Source layout: main entry point `chordsplay.py`, package modules in
  `ChordsPlay/` (`db.py`, `scan.py`, `chords.py`, `play.py`, `gui.py`,
  `filter.py`, `dialog.py`, `selector.py`, `mclist.py`, `song.py`,
  `playlist.py`, `sync.py`, `amp.py`, `ampdialog.py`).
* Tests: `venv/bin/python -m unittest discover -s tests` (no amp, display or
  network needed). The hardware test with the real amp is described in
  [docs/test.md](docs/test.md).
* Database: SQLite in WAL mode at `~/.chordsplay.py.db`. Schema is
  created/upgraded on connect.
* Background scan thread re-checks each song's lyrics roughly once a
  month, unless tagged with `x`.
* Chord scraper: `ChordsPlay/chords.py`. Run `python3 ChordsPlay/chords.py`
  to test against a single hardcoded song without going through the GUI.

License: GPL 2.0.
