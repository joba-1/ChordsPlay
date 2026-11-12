# Installing ChordsPlay

Two parts, installed separately:

* **The web server** (package `chordsplay`): everything except amp control. One computer
  that can read the music; any browser then plays.
* **The Mustang parts** (package `chordsplay-mmp`, which brings `maneline-mmp`): only for a
  Fender Mustang Micro Plus, on the computer the amp is paired with (the server or
  another one).

Packages are the normal way. Installing from a git checkout, for the current user and
without root, is described [at the end](#from-a-git-checkout) for developers.

## Adding the repository

The packages are built on the openSUSE Build Service, project
[home:joba1:chordsplay](https://build.opensuse.org/project/show/home:joba1:chordsplay).
In the commands, `R=https://download.opensuse.org/repositories/home:/joba1:/chordsplay`.

| System | Repository name |
|---|---|
| openSUSE Tumbleweed | `openSUSE_Tumbleweed` |
| openSUSE Leap 16.0, 16.1 | `16.0`, `16.1` |
| Arch, CachyOS, Omarchy | `Arch` |
| Debian 13, Raspberry Pi OS 13 64-bit (default on Pi 4 and 5, possible on Pi 3 and Zero 2) | `Debian_13` |
| Raspberry Pi OS 13 32-bit | `Raspbian_13` |
| Debian testing | `Debian_Testing` |

openSUSE:

```bash
sudo zypper addrepo -f $R/openSUSE_Tumbleweed/home:joba1:chordsplay.repo
```

Arch: add to `/etc/pacman.conf`

```ini
[home_joba1_chordsplay_Arch]
Server = https://download.opensuse.org/repositories/home:/joba1:/chordsplay/Arch/$arch
```

then trust its key:

```bash
curl -fsSL $R/Arch/x86_64/home_joba1_chordsplay_Arch.key | sudo pacman-key --add -
sudo pacman-key --lsign-key "$(curl -fsSL $R/Arch/x86_64/home_joba1_chordsplay_Arch.key | gpg --show-keys --with-colons | awk -F: '/^fpr/ {print $10; exit}')"
```

Debian and Raspberry Pi OS:

```bash
curl -fsSL $R/Debian_13/Release.key | gpg --dearmor | sudo tee /etc/apt/keyrings/chordsplay.gpg > /dev/null
echo "deb [signed-by=/etc/apt/keyrings/chordsplay.gpg] $R/Debian_13/ /" | sudo tee /etc/apt/sources.list.d/chordsplay.list
sudo apt update
```

## The web server

```bash
sudo zypper install chordsplay      # Arch: sudo pacman -Syu chordsplay   Debian: sudo apt install chordsplay
```

On Arch install with `-Syu`: Arch does not support installing onto a partly updated system.

1. Set `CHORDSPLAY_MUSIC_DIRS` (the music folders, `:`-separated) in
   `/etc/chordsplay/chordsplay.env`. The user `chordsplay` must be able to read them; the
   music are files with title and artist tags (MP3, M4A, Ogg).
2. `sudo systemctl enable --now chordsplay`, then open <http://127.0.0.1:8616/> on that
   computer. The first start creates the database and scans the music; songs appear as
   their chords arrive from Ultimate Guitar (a few seconds per song).
   `journalctl -u chordsplay -f` shows the progress.

| What | Where |
|---|---|
| settings | `/etc/chordsplay/chordsplay.env` (every setting with its default: `chordsplay.env.example` in the docs) |
| database, takes, PDF tabs | `/var/lib/chordsplay` |
| program | `/usr/share/chordsplay`, command `chordsplay` |
| Apache example, these docs | `/usr/share/doc/packages/chordsplay` (Arch, Debian: `/usr/share/doc/chordsplay`) |

* **From other devices:** the server has no login of its own and listens on its own
  computer only. Put a reverse proxy with a login in front of it:
  `apache-chordsplay-web.conf` in the docs is an example for Apache
  ([web.md](web.md), "Operating it"). Displays and the amp bridge reach the server
  through the same address as the browser.
* **A network share** that is mounted late at boot: `sudo systemctl edit chordsplay` and
  add `[Unit]` with `RequiresMountsFor=/mnt/music` (your mount point).
* **Updating** comes with the system's package updates; the service is restarted,
  settings and data are kept.

## The Mustang parts

On the computer the amp is paired with, after adding the repository:

```bash
sudo zypper install chordsplay-mmp  # Arch: sudo pacman -Syu chordsplay-mmp   Debian: sudo apt install chordsplay-mmp
```

**BlueZ 5.86 or newer** is needed:

| System | BlueZ |
|---|---|
| Tumbleweed, Arch, Debian testing | the system's own is new enough |
| Leap 16.0, 16.1 | our repository has a current `bluez` (openSUSE's own development package); zypper asks to change the vendor of `bluez`: accept |
| Debian 13, Raspberry Pi OS 13 | add the opt-in repository below |

The opt-in repository for Debian 13 and Raspberry Pi OS 13 has Debian testing's BlueZ 5.88
with Raspberry Pi OS's own audio patch. It replaces the system's Bluetooth stack: add it
only on the amp computer, and remove it once the system ships 5.86 or newer. It is not
official and has not yet run on a real Pi. 32-bit Raspberry Pi OS: `Raspbian_13`.

```bash
B=https://download.opensuse.org/repositories/home:/joba1:/chordsplay:/bluez/Debian_13
curl -fsSL $B/Release.key | gpg --dearmor | sudo tee /etc/apt/keyrings/chordsplay-bluez.gpg > /dev/null
echo "deb [signed-by=/etc/apt/keyrings/chordsplay-bluez.gpg] $B/ /" | sudo tee /etc/apt/sources.list.d/chordsplay-bluez.list
sudo apt update && sudo apt upgrade && sudo reboot
```

Then, once:

1. Put `Experimental = true` into the `[General]` section of `/etc/bluetooth/main.conf`
   (openSUSE has no such file: create it with these two lines), then
   `sudo systemctl restart bluetooth`. Pair the amp for audio as usual.
2. With the amp switched on: `sudo mmp-bluez-setup AA:BB:CC:DD:EE:FF` (the amp's address,
   `bluetoothctl devices`). It pairs the amp over Bluetooth LE as well and removes an entry
   from BlueZ's cache that the amp does not answer; Bluetooth restarts once. What it does,
   and the same by hand: `MUSTANG_MICRO_PLUS_LINUX.md` in the docs of `maneline-mmp`.
3. Open the ChordsPlay page, press `g`, *Set up amp control…*, and put the line it shows
   into `~/.config/chordsplay/bridge.env` (mode 600: the token in it is the bridge's key).
4. `systemctl --user enable --now chordsplay-amp-bridge`. It runs as **your** user, the
   one whose session has the amp's Bluetooth audio, and starts the amp agent
   (`chordsplay-amp-agent.service`) whenever the amp is connected. The page's top right
   shows the amp's state.

* Equal loudness of all presets needs one measurement: `chordsplay-match-levels`
  ([web.md](web.md), "Guitar tone").
* Port 8081 taken on that computer: `AMP_AGENT_PORT=8091` in
  `~/.config/chordsplay/amp-agent.env`.
* Recording takes needs `opusenc`. openSUSE Leap has it only in the openSUSE multimedia
  project: `sudo zypper addrepo -f https://download.opensuse.org/repositories/multimedia:/libs/16.0/multimedia:libs.repo`
  (or `16.1`), then `sudo zypper install opus-tools`.

## Removing

```bash
sudo zypper remove chordsplay chordsplay-mmp maneline-mmp     # Arch: sudo pacman -Rns …   Debian: sudo apt purge …
```

The data stays, so a later install picks it up again. To remove it as well:

| What | Where |
|---|---|
| database, takes, PDF tabs | `/var/lib/chordsplay` |
| settings | `/etc/chordsplay` (openSUSE keeps a changed file as `.rpmsave`, Arch as `.pacsave`; `apt purge` removes it) |
| service user | `chordsplay` (`sudo userdel chordsplay`) |
| amp computer, per user | `~/.config/chordsplay`, `~/.local/share/chordsplay/levels` (level measurements and preset backups), `~/.local/share/maneline-mmp` |
| BlueZ | Leap: `sudo zypper removerepo home_joba1_chordsplay`, then `sudo zypper install --oldpackage "bluez<5.86"`; Debian: remove `/etc/apt/sources.list.d/chordsplay-bluez.list`, `sudo apt update`, then `sudo apt install --allow-downgrades bluez/trixie` |

## From a git checkout

For developers: the same two parts as systemd user units of the current user, without
root, with the Python packages from PyPI in a venv.

Needs Linux with systemd, Python 3.11 or newer with `venv` (Debian: `python3-venv`), `git`
and `rsync`. For the Mustang parts also PipeWire (`pactl`, `pw-record`), `opusenc` and the
BlueZ setup above.

```bash
git clone https://github.com/joba-1/ChordsPlay.git && cd ChordsPlay
contrib/install.sh --music /path/to/your/music web     # the web server
contrib/install.sh mmp                                 # the Mustang parts, on the amp computer
```

* **web** copies the program to `~/.local/share/chordsplay-web/app` with a venv, creates
  `~/.config/chordsplay/web.env` from `contrib/chordsplay.env.example` and starts the user
  unit `chordsplay-web.service`. Logs: `journalctl --user -u chordsplay-web`. Without being
  logged in (a server): `loginctl enable-linger $USER`. A network share that is mounted
  late: `CHORDSPLAY_MUSIC_MOUNT` in `web.env`.
* **mmp** installs maneline's agent from the fork `joba-1/maneline` with a venv in
  `~/.local/share/chordsplay/` (its setup script is `maneline/scripts/bluez_setup.sh`
  there). With the line from step 3 above in `~/.config/chordsplay/bridge.env`, run it
  again: it installs and starts the bridge. Another agent port: `AMP_PORT=8091` once.
* **Update:** `git pull`, then the same command again.
* **Remove:** `contrib/install.sh --uninstall web` (and/or `mmp`); data and settings stay,
  the script says where.
* `-n` shows what the script would do.
