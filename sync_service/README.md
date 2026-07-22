# ChordsPlay central sync service

Holds the shared chord data for all ChordsPlay installations, keyed by
`(title, artist)` — independent of any local music mount path. Clients pull
changes and push their locally-fetched/edited songs; merges are
last-write-wins by `modified`, tags are unioned.

## What syncs
- `songs` (lyrics_chords, lyrics_url, capo, chord_update), chord names, and
  user tags — matched by `(title, artist)`.
- **Never** synced: `song_lists` / `list_songs` (local paths), playlists,
  filters, play history.

## API
- `GET  /health` → `{"status":"ok"}`
- `GET  /songs?since=<YYYY-MM-DD HH:MM:SS>` → `{"songs":[…]}` changed since watermark
- `POST /songs` `{"songs":[…]}` → upsert (LWW), returns `{"upserted":n}`
- If `CHORDSPLAY_SYNC_TOKEN` is set, `/songs` needs `Authorization: Bearer <token>`.

## Deploy on host4

```bash
# 1. code + venv
mkdir -p ~/chordsplay-sync && cp app.py ~/chordsplay-sync/
python3 -m venv ~/chordsplay-sync/venv
~/chordsplay-sync/venv/bin/pip install -r requirements.txt

# 2. service (edit the token first!)
sudo cp chordsplay-sync.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now chordsplay-sync
curl -s localhost:8615/health          # -> {"status":"ok"}

# 3. Apache reverse proxy
sudo a2enmod proxy proxy_http           # if not already
# add apache-chordsplay.conf into the serving VirtualHost, then:
sudo systemctl reload apache2           # (or httpd)
curl -s https://host4/chordsplay/health  # -> {"status":"ok"}
```

## Point clients at it
In each `~/.chordsplay.py.cfg`:
```ini
[chordsplay.py]
sync_url   = https://host4/chordsplay
sync_token = <same token as the service>
```
Seed central once from the most complete installation:
```bash
chordsplay.py --sync-seed
```
Thereafter every scan pulls before fetching (so a song fetched anywhere is
fetched from UG only once) and pushes what it fetched/edited.
