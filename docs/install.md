# Installing ChordsPlay

Two parts, installed separately:

* **The web server** (package `chordsplay`): everything except amp control. One computer
  that can read the music; any browser then plays.
* **The Mustang parts** (package `chordsplay-mmp`, which needs `maneline` or `maneline-joba1`, see
  [below](#maneline-or-maneline-joba1)): only for a
  Fender Mustang Micro Plus, on the computer the amp is paired with (the server or
  another one).

Packages are the normal way. Installing from a git checkout, for the current user and
without root, is described [at the end](#from-a-git-checkout) for developers.

## Adding the repository

The packages are built on the openSUSE Build Service, project
[home:joba1:chordsplay](https://build.opensuse.org/project/show/home:joba1:chordsplay).
The commands below start by setting two variables: `OBS_REPO`, where the project's
repositories are published, and `DIST`, the repository for your system from this table:

| System | `DIST` |
|---|---|
| openSUSE Tumbleweed | `openSUSE_Tumbleweed` |
| openSUSE Leap 16.0, 16.1 | `16.0`, `16.1` |
| Arch, CachyOS, Omarchy | `Arch` |
| Debian 13, Raspberry Pi OS 13 64-bit (default on Pi 4 and 5, possible on Pi 3 and Zero 2) | `Debian_13` |
| Raspberry Pi OS 13 32-bit | `Raspbian_13` |
| Debian testing | `Debian_Testing` |
| Ubuntu 26.04 (64-bit, amd64 and arm64) | `Ubuntu_26.04` |

openSUSE:

```bash
OBS_REPO=https://download.opensuse.org/repositories/home:/joba1:/chordsplay
DIST=openSUSE_Tumbleweed     # or 16.0, 16.1
sudo zypper addrepo -f $OBS_REPO/$DIST/home:joba1:chordsplay.repo
```

Arch: add to `/etc/pacman.conf`

```ini
[home_joba1_chordsplay_Arch]
Server = https://download.opensuse.org/repositories/home:/joba1:/chordsplay/Arch/$arch
```

then trust its key:

```bash
OBS_REPO=https://download.opensuse.org/repositories/home:/joba1:/chordsplay
KEY_URL=$OBS_REPO/Arch/x86_64/home_joba1_chordsplay_Arch.key
curl -fsSL $KEY_URL | sudo pacman-key --add -
sudo pacman-key --lsign-key "$(curl -fsSL $KEY_URL | gpg --show-keys --with-colons | awk -F: '/^fpr/ {print $10; exit}')"
```

Debian, Ubuntu and Raspberry Pi OS:

```bash
OBS_REPO=https://download.opensuse.org/repositories/home:/joba1:/chordsplay
DIST=Debian_13               # or Raspbian_13, Debian_Testing, Ubuntu_26.04
curl -fsSL $OBS_REPO/$DIST/Release.key | gpg --dearmor | sudo tee /etc/apt/keyrings/chordsplay.gpg > /dev/null
echo "deb [signed-by=/etc/apt/keyrings/chordsplay.gpg] $OBS_REPO/$DIST/ /" | sudo tee /etc/apt/sources.list.d/chordsplay.list
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

## The practice mix (optional)

The practice mix needs a program that splits songs into instruments. It isn't part of
the packages: it runs a neural network (PyTorch, several GB) in its own Python
environment. The package brings the script, `/usr/share/chordsplay/stems/chordsplay-stems`;
it uses [python-audio-separator](https://github.com/nomadkaraoke/python-audio-separator)
and its model `BS-Roformer-SW` (downloaded at the first split, ~700 MB). An NVIDIA GPU with
about 4 GB free makes it take a minute per song; on the CPU it takes several.

1. The environment, here in `/opt/chordsplay-stems` with [uv](https://docs.astral.sh/uv/)
   (PyTorch for CUDA 12.8; leave out `--index-url …` for a computer without an NVIDIA GPU):

   ```bash
   # the Python itself under /opt too: uv's default is root's home, which the service can't reach
   sudo env UV_PYTHON_INSTALL_DIR=/opt/chordsplay-stems-python uv venv -p 3.12 --managed-python /opt/chordsplay-stems
   sudo uv pip install -p /opt/chordsplay-stems/bin/python torch torchaudio --index-url https://download.pytorch.org/whl/cu128
   sudo uv pip install -p /opt/chordsplay-stems/bin/python "audio-separator[gpu]" audioread beat-this
   ```

   It also needs `ffmpeg` with Opus. `beat-this` finds the beats (its checkpoint, 77 MB,
   is downloaded at the first split into the model folder); without it the splitter uses
   librosa, which assumes one tempo for the whole song.
2. In `/etc/chordsplay/chordsplay.env`:

   ```
   CHORDSPLAY_STEMS_COMMAND="/opt/chordsplay-stems/bin/python /usr/share/chordsplay/stems/chordsplay-stems --free-ollama http://127.0.0.1:11434 --model-dir /var/lib/chordsplay/stems-model"
   ```

   The quotes matter: without them a shell that reads the file (`. chordsplay.env`)
   runs the splitter instead of setting the variable; systemd takes either.

   `--device gpu` (the default) refuses to start when the GPU is busy (the page shows
   *GPU busy*); `--device auto` then uses the CPU; `--device cpu` never uses the GPU.
   `--free-ollama URL`: when the GPU is busy, the splitter first asks that Ollama server
   to unload its models (it loads them again by itself on its next request). Leave it out
   if a model must stay loaded.
3. With a GPU: the service hides devices from itself (`PrivateDevices=yes`), and its user
   may not open the GPU's devices (on openSUSE they belong to the group `video`). Let it:
   `sudo systemctl edit chordsplay` and add

   ```
   [Service]
   PrivateDevices=no
   SupplementaryGroups=video
   ```
4. `sudo systemctl restart chordsplay`: the page shows *Practice*.

**Splitting your favourites at night** (or any tag): a timer that queues the songs a user
has tagged and waits until they are split, e.g. at 02:30. `/etc/systemd/system/chordsplay-stems.service`:

```
[Unit]
Description=ChordsPlay: split the tagged songs for the practice mix
After=chordsplay.service
[Service]
Type=oneshot
User=chordsplay
EnvironmentFile=/etc/chordsplay/chordsplay.env
ExecStart=/usr/bin/chordsplay stems queue --user me --tag f
ExecStart=/usr/bin/chordsplay stems wait --timeout 14400
```

and `/etc/systemd/system/chordsplay-stems.timer`:

```
[Timer]
OnCalendar=*-*-* 02:30
Persistent=true
[Install]
WantedBy=timers.target
```

then `sudo systemctl daemon-reload && sudo systemctl enable --now chordsplay-stems.timer`.
The server's worker does the splitting, one song after the other; the timer only queues
and waits.

Try the splitter alone first: `sudo -u chordsplay -g video /opt/chordsplay-stems/bin/python
/usr/share/chordsplay/stems/chordsplay-stems --model-dir /var/lib/chordsplay/stems-model
song.mp3 /tmp/song.webm`.

## The Mustang parts

On the computer the amp is paired with, after adding the repository:

```bash
sudo zypper install chordsplay-mmp  # Arch: sudo pacman -Syu chordsplay-mmp   Debian: sudo apt install chordsplay-mmp
```

This brings maneline (package `maneline`, see
[below](#maneline-or-maneline-joba1)), whose Mustang Micro Plus programs talk to the amp.
They need **BlueZ 5.86 or newer** and a one-time setup; both are described in maneline's
[package README][maneline-readme], which ChordsPlay follows:

* **BlueZ:** section "BlueZ" there. Tumbleweed, Arch and Debian testing have a new enough
  one. On Leap the same repository as ChordsPlay has it (zypper asks to change the vendor of
  `bluez`: accept). Debian 13, Raspberry Pi OS 13 and Ubuntu 26.04 need the opt-in BlueZ
  repository described there; add it only on the amp computer, and remove it once the
  system ships 5.86 or newer.
* **The one-time setup for the amp:** section "Installing", the paragraph after the
  install commands (`Experimental = true`, then `mmp-bluez-setup` with the amp's address).
  Pair the amp for audio as usual first.

Then, for ChordsPlay:

1. Open the ChordsPlay page, press `g`, tab *Amp*, *Set up amp control…*, and put the line it shows
   into `~/.config/chordsplay/bridge.env` (mode 600: the token in it is the bridge's key).
2. `systemctl --user enable --now chordsplay-amp-bridge`. It runs as **your** user, the
   one whose session has the amp's Bluetooth audio, and starts the amp agent
   (`chordsplay-amp-agent.service`) whenever the amp is connected. The page's top right
   shows the amp's state.

* Equal loudness of all presets needs one measurement: `chordsplay-match-levels`
  ([web.md](web.md), "Guitar tone").
* Port 8081 taken on that computer: `AMP_AGENT_PORT=8091` in
  `~/.config/chordsplay/amp-agent.env`.
* Recording takes needs `opusenc`. openSUSE Leap has it only in the openSUSE multimedia
  project:

  ```bash
  DIST=16.0                    # or 16.1
  sudo zypper addrepo -f https://download.opensuse.org/repositories/multimedia:/libs/$DIST/multimedia:libs.repo
  sudo zypper install opus-tools
  ```

### maneline or maneline-joba1

Two packages carry maneline's Mustang Micro Plus programs (`mmp-web`, `mmp-ble`,
`mmp-backup`, `mmp-restore`, `mmp-audition`, `mmp-check`, `mmp-level`, `mmp-bluez-setup`),
with the same paths and commands, so only one can be installed at a time:

| Package | Built from |
|---|---|
| `maneline` | the original `tim-littlefair/maneline`, branch `main` |
| `maneline-joba1` | the fork `joba-1/maneline`, branch `main`: the original plus what is on its way there |

`maneline-joba1` provides `maneline` and conflicts with it, so `chordsplay-mmp` (which
depends on `maneline`) works with either and stays installed when you switch. Installing
`chordsplay-mmp` on its own pulls in `maneline`; for the fork, install `maneline-joba1`
first. `maneline-joba1` also replaces `maneline-mmp`, the fork's former package.

Stop the amp agent before, and start it again after:

```bash
systemctl --user stop chordsplay-amp-bridge
# to the original:
sudo zypper install --force-resolution maneline       # Arch: sudo pacman -S maneline         Debian/Ubuntu: sudo apt install maneline
# to the fork:
sudo zypper install --force-resolution maneline-joba1 # Arch: sudo pacman -S maneline-joba1   Debian/Ubuntu: sudo apt install maneline-joba1
systemctl --user start chordsplay-amp-bridge
```

Settings, the amp's pairing and data in `~/.config/chordsplay` and `~/.local/share/chordsplay`
stay, and so do the backups `mmp-backup` makes in `~/.local/share/maneline` (both packages
use it). Backups of the former `maneline-mmp` are in `~/.local/share/maneline-mmp`; move them
over with `mkdir -p ~/.local/share/maneline && mv ~/.local/share/maneline-mmp/backups ~/.local/share/maneline/`.

## Removing

```bash
sudo zypper remove chordsplay chordsplay-mmp maneline-joba1 maneline   # Arch: sudo pacman -Rns …   Debian: sudo apt purge …
```

The data stays, so a later install picks it up again. To remove it as well:

| What | Where |
|---|---|
| database, takes, PDF tabs | `/var/lib/chordsplay` |
| settings | `/etc/chordsplay` (openSUSE keeps a changed file as `.rpmsave`, Arch as `.pacsave`; `apt purge` removes it) |
| service user | `chordsplay` (`sudo userdel chordsplay`) |
| amp computer, per user | `~/.config/chordsplay`, `~/.local/share/chordsplay/levels` (level measurements and preset backups), `~/.local/share/maneline` |
| BlueZ | Leap: `sudo zypper removerepo home_joba1_chordsplay`, then `sudo zypper install --oldpackage "bluez<5.86"`; Debian: remove the opt-in repository's file in `/etc/apt/sources.list.d/` (`maneline-bluez.list` when set up as in maneline's README), `sudo apt update`, then `sudo apt install --allow-downgrades bluez/trixie` |

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
  there). With the line from step 1 above in `~/.config/chordsplay/bridge.env`, run it
  again: it installs and starts the bridge. Another agent port: `AMP_PORT=8091` once.
* **Update:** `git pull`, then the same command again.
* **Remove:** `contrib/install.sh --uninstall web` (and/or `mmp`); data and settings stay,
  the script says where.
* `-n` shows what the script would do.

[maneline-readme]: https://github.com/tim-littlefair/maneline/blob/main/deployment/packages/maneline/README.md
