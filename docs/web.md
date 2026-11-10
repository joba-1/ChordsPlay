# ChordsPlay web app

ChordsPlay in the browser: <https://example.org/chordsplay/> (Authelia login).
Plan and reasons: Trac page `ChordsPlay` (phase 1: web app core). The Tk app stays usable.

## Using it

The page looks like the desktop app: the playlist on the left, the song's chords and
lyrics on the right, as large as the window allows (1–4 columns). The browser plays the
song, so connect the Mustang (or any output) to the device the browser runs on.

Most keys work as in the desktop app:

| Key | Action |
|---|---|
| Space, m | play / pause |
| Enter, n | next song |
| Backspace, p | previous song |
| 0 | back to the start of the song |
| + / −, → / ← | 1 s / 5 s forward or back |
| s | shuffle the playlist |
| f | filter dialog: same fields, saved filters and live count as in the desktop app |
| t | tags of the song; press a tag's key to switch it |
| l | show or hide the playlist |
| a / A | choose / switch the audio output (Chrome only) |
| F11 | full screen |
| ? | key overview |

Double-click a song in the list to play it; click a column heading to sort.
A song repeats until you go on, and each time it ends it counts as played (as in the
desktop app). The page remembers the song you were at, on every device: after a reload
press Space to go on. Hardware media keys and Bluetooth headset buttons work too
(play/pause, next, previous).

Browsers need one key press or tap before they play sound; after a reload the page
shows "Press Space to play".

**Long comment lines.** Chord sheets often carry comments in long lines ("Note: the
recorded version is 30 cents sharp ..."), and the widest line decides the font size.
The page shows such lines wrapped to the width of the song text whenever that makes the
text larger. Chord lines, the lines under them and tab lines are never changed, and
lines no longer than 90 % of the song's lines stay whole. Heuristic:
`ChordsPlay/textfit.py`.

**Not yet in the web app:** guitar tone (`g`/`G`; comes with the amp bridge, phase B),
filter import/export to files, a display that follows another device (phase 2).

## How it works

```
browser (controller)  --https-->  Apache :443 /chordsplay/ (Authelia)  -->  web/app.py 127.0.0.1:8616 (host6)
                                                                              |-- ~/.local/share/chordsplay-web/chordsplay.db
                                                                              '-- music: /srv/music (NFS)
```

* `web/app.py` (Flask, served by waitress): JSON API, the page (`web/static/`), the audio
  files with range requests. All queries go through `ChordsPlay/db.py`, unchanged.
* One database for all devices: play history, tags, filters and the playlist order
  are no longer per device. It has the Tk app's schema plus a table `web_state`
  (the current song per user).
* The library scan and the Ultimate Guitar fetch run in the server (`ChordsPlay/scan.py`),
  at start and every 24 hours.
* User: Apache passes the Authelia name as `X-Remote-User` (and drops what a client
  sends). Requests through Apache without a login are refused. Direct requests on host6
  (`curl 127.0.0.1:8616`) use `CHORDSPLAY_USER`.
* Audio comes from the NFS mount, not from media-find as the plan suggested: the scanner
  needs the files anyway (tags), and no media-find service account is needed. The unit
  mounts the share if it is missing (the fstab entry has `users`).

## Operating it (host6)

| What | How |
|---|---|
| Install / update | `contrib/install-web.sh` (copies the checkout to `~/.local/share/chordsplay-web/app`, venv, restarts the user unit `chordsplay-web.service`) |
| First database | `cd ~/.local/share/chordsplay-web/app && ../venv/bin/python -m web.migrate init ~/.chordsplay.py.db --central /opt/chordsplay-sync/chordsplay-central.db` |
| Add another host's history | copy its `~/.chordsplay.py.db` over (Tk app closed), then `../venv/bin/python -m web.migrate merge <file>`; plays, tags and saved filters are added, nothing twice |
| Logs | `journalctl --user -u chordsplay-web` |
| Settings | `~/.config/chordsplay/web.env`: `CHORDSPLAY_USER_MAP=authelia=dbuser,...`, `CHORDSPLAY_SCAN_HOURS`, `CHORDSPLAY_SYNC_URL` + `CHORDSPLAY_SYNC_TOKEN` (push new chords to the old sync service, so the Tk apps get them) |
| Apache | `/etc/apache2/public.d/locations/chordsplay.conf` (= `contrib/apache-chordsplay-web.conf`) and `<Location "/chordsplay/">` in `/etc/apache2/homeauth.conf` |

## Tests

```bash
python3 -m venv /tmp/wv && /tmp/wv/bin/pip install -r web/requirements.txt
/tmp/wv/bin/python -m unittest discover -s tests     # test_web.py is skipped without Flask
```

`tests/test_textfit.py` covers the comment wrapping, `tests/test_web.py` the API (filter,
order, saved filters, song text, audio ranges, played, tags, current song, proxy login) and
the migration (plays and tags merged once, newer central chords win).

Manual check in a browser (headless Chromium with Playwright did this for 0.1): play,
pause, next/previous, filter with live count and regex errors, tags by key, played count
after the end, 1600×900, 1920×1080 and 390×844 layouts, reload resumes.
