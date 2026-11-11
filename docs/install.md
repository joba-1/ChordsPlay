# Installing ChordsPlay

Two parts, installed separately:

* **The web server** (`web`): everything except amp control. One computer that can
  read the music; any browser then plays.
* **The Mustang parts** (`mmp`): only for a Fender Mustang Micro Plus, on the computer
  the amp is paired with (it may be the server or another one): the amp agent and the
  amp bridge.

This page is the install from a git checkout, for the current user, without root.
Distribution packages are planned but not available yet.

## What you need

For the web server:

* Linux with systemd (the service runs as a systemd user unit).
* Python 3.11 or newer with the `venv` module (Debian and Raspberry Pi OS:
  `python3-venv`), `git`, `rsync`.
* Internet access at install time (Flask, waitress, mutagen and qrcode come from PyPI
  into a venv of their own) and later for the chords from Ultimate Guitar.
* The music as files with title and artist tags (MP3, M4A, Ogg), readable by your user.

For the Mustang parts, on the amp computer:

* BlueZ 5.84 or newer with `Experimental = true`, and the amp paired for audio and over
  Bluetooth LE: see `docs/MUSTANG_MICRO_PLUS_LINUX.md` in maneline (the installer says
  where it put it).
* PipeWire with `pactl` (finding the amp) and, for recording takes, `pw-record` and
  `opusenc` (package `opus-tools`).
* Python 3.11 or newer with `venv`, `git`; internet access at install time (maneline
  from GitHub, `bleak` and `dbus-fast` from PyPI).
* A web server installed as above, reachable from the amp computer.

## The web server

```bash
git clone https://github.com/joba-1/ChordsPlay.git
cd ChordsPlay
contrib/install.sh --music /path/to/your/music web
```

Several folders: `--music /one:/two`. The script

1. copies the program to `~/.local/share/chordsplay-web/app` and makes a venv next to it;
2. creates the settings file `~/.config/chordsplay/web.env` from the template
   `contrib/chordsplay.env.example`, if there is none yet;
3. installs, enables and starts the user unit `chordsplay-web.service`.

Then open <http://127.0.0.1:8616/> on that computer. The first start creates the database
and scans the music; songs appear in the list as their chords arrive (a large library
takes hours, a few seconds per song). `journalctl --user -u chordsplay-web -f` shows
the progress.

* **Running without being logged in** (a server): `loginctl enable-linger $USER`.
* **From other devices:** the server has no login of its own and listens on this computer
  only. Put a reverse proxy with a login in front of it: `contrib/apache-chordsplay-web.conf`
  is an example for Apache (see docs/web.md, "Operating it"). Displays and the amp
  bridge reach the server through the same address as the browser.
* **Settings:** edit `~/.config/chordsplay/web.env` (every setting is explained there),
  then `systemctl --user restart chordsplay-web`.
* **A network share** for the music that isn't always mounted at boot: set
  `CHORDSPLAY_MUSIC_MOUNT` there.

To see what the script would do, without doing it: `contrib/install.sh -n web`.

## The Mustang parts

On the computer the amp is paired with, from a checkout of this repo:

```bash
contrib/install.sh mmp
```

The first run installs the amp agent (maneline's `mmp_web.py`, with a venv, in
`~/.local/share/chordsplay/`) and tells you the one step left by hand:

1. Open the ChordsPlay page, press `g`, *Set up amp control…*, and put the line it shows
   into `~/.config/chordsplay/bridge.env`.
2. Run `contrib/install.sh mmp` again: it installs and starts the amp bridge
   (`chordsplay-amp-bridge.service`).

The page's top right then shows the amp's state. If port 8081 is taken on that computer,
run `AMP_PORT=8091 contrib/install.sh mmp` once; the port is kept for later runs.

Equal loudness of all presets needs one measurement: `mmp/match-preset-levels.sh`
(docs/web.md, "Guitar tone").

## Updating

```bash
cd ChordsPlay
git pull
contrib/install.sh web        # and/or mmp, on the computer that has it
```

The same command as the install: it replaces the program, updates the dependencies and
restarts the service. The settings file and the database are kept; the database's
schema is brought up to date at the start.

## Removing

```bash
contrib/install.sh --uninstall web     # and/or mmp
```

stops and removes the service and the program. It keeps your data and settings and
says where they are: the database, takes and PDFs in `~/.local/share/chordsplay-web`,
level measurements in `~/.local/share/chordsplay/levels`, the settings in
`~/.config/chordsplay`. Delete those by hand if you want them gone.

## Coming from the desktop app

A database of the former desktop app (`~/.chordsplay.py.db`) can be taken over instead
of starting empty: docs/web.md, "Operating it", *Data of the former desktop app*.
