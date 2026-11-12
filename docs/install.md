# Installing ChordsPlay

Two parts, installed separately:

* **The web server** (`web`): everything except amp control. One computer that can
  read the music; any browser then plays.
* **The Mustang parts** (`mmp`): only for a Fender Mustang Micro Plus, on the computer
  the amp is paired with (it may be the server or another one): the amp agent and the
  amp bridge.

There are two ways to install: from a git checkout, for the current user and without
root (most of this page), or the web server [from a package](#the-web-server-from-a-package)
as a system service, with the Mustang parts [from packages](#the-mustang-parts-from-packages) too.

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
  from GitHub, `dbus-fast` from PyPI).
* A web server installed as above, reachable from the amp computer.

## The web server from a checkout

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

## The web server from a package

The package `chordsplay` (openSUSE: `packaging/rpm/`; Arch: `packaging/arch/`; Debian, Raspberry Pi OS: `packaging/debian/`) installs the
server as the system service `chordsplay.service` with a user of its own, and takes
Flask, waitress, mutagen and qrcode from the distribution: no venv, no PyPI.

| What | Where |
|---|---|
| settings | `/etc/chordsplay/chordsplay.env` |
| database, takes, PDF tabs | `/var/lib/chordsplay` (`chordsplay.db`, `takes/`, `pdf/`) |
| program | `/usr/share/chordsplay`, command `chordsplay` |
| every setting, Apache example, these docs | `/usr/share/doc/packages/chordsplay` (Arch, Debian: `/usr/share/doc/chordsplay`) |

The packages are built on the openSUSE Build Service, project
[home:joba1:chordsplay](https://build.opensuse.org/project/show/home:joba1:chordsplay).
`R` below is `https://download.opensuse.org/repositories/home:/joba1:/chordsplay`.

openSUSE Tumbleweed (for Leap 16.0 put `16.0` in place of `openSUSE_Tumbleweed`):

```bash
sudo zypper addrepo -f $R/openSUSE_Tumbleweed/home:joba1:chordsplay.repo
sudo zypper install chordsplay
```

Arch, CachyOS, Omarchy: add to `/etc/pacman.conf`

```ini
[home_joba1_chordsplay_Arch]
Server = https://download.opensuse.org/repositories/home:/joba1:/chordsplay/Arch/$arch
```

then

```bash
curl -fsSL $R/Arch/x86_64/home_joba1_chordsplay_Arch.key | sudo pacman-key --add -
sudo pacman-key --lsign-key "$(curl -fsSL $R/Arch/x86_64/home_joba1_chordsplay_Arch.key | gpg --show-keys --with-colons | awk -F: '/^fpr/ {print $10; exit}')"
sudo pacman -Sy chordsplay
```

Debian 13 and Raspberry Pi OS based on it (there `Raspbian_13` in place of `Debian_13`):

```bash
curl -fsSL $R/Debian_13/Release.key | gpg --dearmor | sudo tee /etc/apt/keyrings/chordsplay.gpg > /dev/null
echo "deb [signed-by=/etc/apt/keyrings/chordsplay.gpg] $R/Debian_13/ /" | sudo tee /etc/apt/sources.list.d/chordsplay.list
sudo apt update && sudo apt install chordsplay
```

After installing the package:

1. Set `CHORDSPLAY_MUSIC_DIRS` in `/etc/chordsplay/chordsplay.env`. The user `chordsplay`
   must be able to read the folders.
2. `sudo systemctl enable --now chordsplay`, then open <http://127.0.0.1:8616/>.
   `journalctl -u chordsplay -f` shows the progress of the first scan.

* **A network share** for the music that is mounted late: `sudo systemctl edit chordsplay`
  and add `[Unit]` with `RequiresMountsFor=/mnt/music` (your mount point).
* **From other devices:** a reverse proxy with a login, as for the checkout install below.
* **Updating:** with the system's package updates; the service is restarted, settings and
  data are kept.
* **Removing** the package keeps `/var/lib/chordsplay` and a changed settings file.
* **Moving from a checkout install** to the package: stop both services, copy
  `~/.local/share/chordsplay-web/chordsplay.db`, `takes/` and `pdf/` to `/var/lib/chordsplay`,
  give them to the user `chordsplay` (`chown -R chordsplay:chordsplay /var/lib/chordsplay`),
  carry your settings over from `~/.config/chordsplay/web.env`, and set `CHORDSPLAY_USER`
  to your former user name: that is the database user the server used so far. Then
  `contrib/install.sh --uninstall web` and start `chordsplay`.

## The Mustang parts from packages

The same repositories as for [the web server](#the-web-server-from-a-package) have
`chordsplay-mmp` (the amp bridge, its units, the level-matching command) and
`maneline-mmp` (maneline's Micro Plus programs, which it needs). On the computer the
amp is paired with, after adding the repository as described there:

```bash
sudo zypper install chordsplay-mmp        # Arch: pacman -S, Debian: apt install
```

Both parts run as systemd units of **your** user, the one whose session has the amp's
Bluetooth audio:

1. Pair the amp for audio and over Bluetooth LE, with `Experimental = true` in BlueZ's
   `main.conf`: `MUSTANG_MICRO_PLUS_LINUX.md` in the documentation of `maneline-mmp`.
2. Open the ChordsPlay page, press `g`, *Set up amp control…*, and put the line it shows
   into `~/.config/chordsplay/bridge.env` (mode 600: the token in it is the bridge's key).
3. `systemctl --user enable --now chordsplay-amp-bridge`. The bridge starts the amp
   agent (`chordsplay-amp-agent.service`) whenever the amp is connected.

If port 8081 is taken on that computer, put `AMP_AGENT_PORT=8091` into
`~/.config/chordsplay/amp-agent.env`. Equal loudness of all presets needs one
measurement: `chordsplay-match-levels` (docs/web.md, "Guitar tone").

**openSUSE Leap 16.0** itself has no `opus-tools` (`opusenc`, needed for recording takes;
everything else works without). The openSUSE multimedia project builds it for Leap 16.0:

```bash
sudo zypper addrepo -f https://download.opensuse.org/repositories/multimedia:/libs/16.0/multimedia:libs.repo
sudo zypper install opus-tools
```

**BlueZ 5.84 or newer** is needed by the amp agent. Tumbleweed and Arch have it. openSUSE
Leap 16.0 ships 5.79, Debian 13 and Raspberry Pi OS 13 ship 5.82: the packages install
there, but the agent cannot connect to the amp until BlueZ is newer.

## The Mustang parts from a checkout

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
