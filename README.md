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
| `l` | Toggle the playlist panel |
| `F11` | Toggle fullscreen |
| `Escape` | Leave fullscreen |
| `q` | Quit |

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

## Configuration file

`~/.chordsplay.cfg` (auto-detected) can hold defaults so you don't have
to pass them every time:

```ini
[chordsplay]
database = /path/to/your/songs.db
user     = yourname
ug_user  = your_ultimate_guitar_username
ug_pass  = your_ultimate_guitar_password
verbosity = 0
```

If `ug_pass` is present, **lock the file down**:

```bash
chmod 600 ~/.chordsplay.cfg
```

ChordsPlay warns at startup if the file is readable by others.

Logging in to Ultimate Guitar is optional, but helps when the site
returns a bot-challenge page.

---

## Command-line options

| Option | Purpose |
|---|---|
| `-c FILE` / `--config` | Use a different config file (default: `~/.chordsplay.cfg`) |
| `-d FILE` / `--database` | Use a different database file |
| `-u NAME` / `--user` | Database user (default: your OS username) |
| `-l FILE` / `--logfile` | Write a full DEBUG log to a file (console stays at INFO) |
| `-v` / `--verbose` | Verbose console output |
| `-t FILE` / `--testfile` | Run one test song and dump HTML to file (for debugging chord scraper) |
| `-f` / `--fix` | Run one-off database fix-ups and exit |
| `--ug-user` / `--ug-pass` | Ultimate Guitar credentials (prefer config file) |

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
  `playlist.py`).
* Database: SQLite in WAL mode at `~/.chordsplay.py.db`. Schema is
  created/upgraded on connect.
* Background scan thread re-checks each song's lyrics roughly once a
  month, unless tagged with `x`.
* Chord scraper: `ChordsPlay/chords.py`. Run `python3 ChordsPlay/chords.py`
  to test against a single hardcoded song without going through the GUI.

License: GPL 2.0.
