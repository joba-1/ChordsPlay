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
| d | show the song on a TV or tablet (display link, see below) |
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
filter import/export to files.

## A TV or tablet as display

Press `d` (or *Display*). The dialog shows a link, its QR code and the short code in it.
Open the link on the TV's browser, a tablet on the music stand or a second screen: it
shows the song this page plays, as large as that screen allows, with its title, capo
and the time in the song, and follows every change within about a second. Click it,
or press Enter or OK on the remote, for full screen; it keeps the screen on.

* The display plays no sound and can change nothing; the audio stays on the controller
  (the device paired with the Mustang).
* A link is valid for 12 hours. *End all display links* stops every open display at once.
* Several displays can follow the same controller.
* The display follows the user, not the browser tab: whatever page of yours plays last
  is what the displays show.
* The display needs no login (a TV, a friend's TV): the code in the link is the key.
  Everything else under `/chordsplay/` still needs the Authelia login.

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
* Following: the controller reports song, position and play/pause (`PUT api/now`, at
  every change and every 15 s while playing). The server keeps the latest state per user
  in memory (and the song in `web_state`) and pushes it as server-sent events to
  `api/events` and `d/<code>/events`. Display codes are in the table `display_codes`
  (10 characters from a 31-letter alphabet, ~49 bits; an unknown code costs a 1 s wait).
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
| Apache | `/etc/apache2/public.d/locations/chordsplay.conf` (= `contrib/apache-chordsplay-web.conf`, with `flushpackets=on` for the event streams) and `<Location "/chordsplay/">` in `/etc/apache2/homeauth.conf` |
| Displays without login | `<Location "/chordsplay/d/">` with `AuthType None` / `Require all granted` after the `/chordsplay/` block in `homeauth.conf` (since 2026-11-10). Everything below `/d/` needs only the pairing code; the app serves nothing else there. |

## Tests

```bash
python3 -m venv /tmp/wv && /tmp/wv/bin/pip install -r web/requirements.txt
/tmp/wv/bin/python -m unittest discover -s tests     # test_web.py is skipped without Flask
```

`tests/test_textfit.py` covers the comment wrapping, `tests/test_web.py` the API (filter,
order, saved filters, song text, audio ranges, played, tags, current song, proxy login),
the displays (code, state, event stream, unknown and ended codes, no way from `/d/` to the
controller API, QR code, hub wake-up) and the migration (plays and tags merged once, newer
central chords win).

Manual check in a browser (headless Chromium with Playwright did this for 0.1): play,
pause, next/previous, filter with live count and regex errors, tags by key, played count
after the end, 1600×900, 1920×1080 and 390×844 layouts, reload resumes. For 0.2 also a
controller and a display page side by side: the display follows next/pause/play within a
second, shows the expired message after *End all display links* (open display: ~8 s).
