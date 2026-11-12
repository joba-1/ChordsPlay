# ChordsPlay

Play your music collection in the browser while it shows each song's lyrics and guitar
chords, fetched automatically from Ultimate Guitar and drawn as large as the screen
allows.

* One server holds the songs, chords, tags, filters and play history; any browser is the
  player.
* A TV, tablet or second screen can show the playing song (a link or QR code, no login).
* PDF tabs next to the song files are shown instead of the text, cropped and paged.
* Optional, with a **Fender Mustang Micro Plus**: the amp switches to the preset stored
  for a song (and at set times within it), all presets equally loud, knobs per song,
  recording takes.

Installing it as a service, with or without the Mustang parts, updating and removing:
[docs/install.md](docs/install.md). Using and operating it: [docs/web.md](docs/web.md).

## Try it from a checkout

Needs Python 3.11 or newer.

```bash
git clone https://github.com/joba-1/ChordsPlay.git && cd ChordsPlay
python3 -m venv venv
venv/bin/pip install -r web/requirements.txt
CHORDSPLAY_MUSIC_DIRS=/path/to/your/music venv/bin/python -m web.app
```

Open <http://127.0.0.1:8616/>. The first start creates the database
(`~/.local/share/chordsplay-web/chordsplay.db`) and scans the music folder: every file
with a title tag (MP3, M4A, Ogg) becomes a song, and each new song is looked up at
Ultimate Guitar in the background. Songs appear in the list as their chords arrive;
press `f` to choose what to play and `?` for the keys.

Settings are environment variables (the music folders, the database file, the port,
users behind a reverse proxy …): see the head of [web/app.py](web/app.py).

## What is where

| Path | What |
|---|---|
| `web/` | the server (`app.py`, Flask served by waitress) and the page (`static/`) |
| `ChordsPlay/` | shared modules: database (`db.py`), library scan (`scan.py`, `song.py`), Ultimate Guitar fetch (`chords.py`, `http.py`), text layout (`textfit.py`), amp logic (`amp.py`) |
| `mmp/` | the optional Mustang Micro Plus parts for the computer the amp is paired with: the amp bridge (`bridge.py`), the units, the level-matching script. Design: [docs/spec-amp.md](docs/spec-amp.md) |
| `contrib/` | install script (`install.sh`), service unit, settings template, Apache example |
| `packaging/` | what the distribution packages install: system unit, service user, settings file, the `chordsplay` command; RPM spec in `rpm/`, Arch `PKGBUILD` in `arch/`, Debian files in `debian/` |
| `tests/` | unit tests; hardware tests are described in [docs/test.md](docs/test.md) |

## Tests

```bash
venv/bin/python -m unittest discover -s tests
```

No amp, display or network needed.

## The desktop app

ChordsPlay began as a desktop (Tk) program with a database per computer and a sync
service between them. Both are retired in favour of the web app; their last state is on
the branch `legacy`. `python -m web.migrate` takes a desktop database over into the web
app's (see its `--help`).

## Licence

ChordsPlay is free software under the GNU Affero General Public License, version 3 or
later (`AGPL-3.0-or-later`): see [LICENSE](LICENSE). If you run a modified version for
others, they have the right to its source: the page's help carries a *Source* link, set
it with `CHORDSPLAY_SOURCE_URL`.

The bundled PDF viewer, [PDF.js](https://mozilla.github.io/pdf.js/)
(`web/static/vendor/pdfjs/`), is under the Apache License 2.0, see its `LICENSE` there.
