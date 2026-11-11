# ChordsPlay central sync service

Holds the shared chord data for all ChordsPlay installations, keyed by
`(title, artist)` — independent of any local music mount path. Clients pull
changes and push their locally-fetched/edited songs; merges are
last-write-wins by `modified`, tags are unioned.

**Locked songs (tag `x`, Fixiert) are the exception** (since 2026-11-10, in the
client, `Db.upsertRemoteSong`): a client that has a song locked keeps its text
when a newer one arrives, and pushes its own text back so it wins at the service.
The service itself knows nothing of locks. Tags are only ever added: removing a
lock (or any tag) at one client does not remove it at the service or elsewhere.

The web app (`web/`, docs/web.md) is a client too: its scanner pulls and pushes
with `CHORDSPLAY_SYNC_URL` / `CHORDSPLAY_SYNC_TOKEN`.

## What syncs
- `songs` (lyrics_chords, lyrics_url, capo, chord_update), chord names, and
  user tags — matched by `(title, artist)`.
- **Never** synced: `song_lists` / `list_songs` (local paths), playlists,
  filters, play history, amp cues, artist presets, tones and level corrections.

## API
- `GET  /health` → `{"status":"ok"}`
- `GET  /songs?since=<YYYY-MM-DD HH:MM:SS>` → `{"songs":[…]}` changed since watermark
- `POST /songs` `{"songs":[…]}` → upsert (LWW), returns `{"upserted":n}`
- `/songs` requires `Authorization: Bearer <token>` when `CHORDSPLAY_SYNC_TOKEN` is set.

## Deploy (current host: host6)

Runs as a dedicated system user, from `/opt`, exposed on a firewalled port
(LAN + VPN only). No web server needed.

```bash
# 1. dedicated system user (no login, no home of its own)
sudo useradd --system --no-create-home --home-dir /opt/chordsplay-sync \
     --shell /usr/sbin/nologin chordsync

# 2. code + venv under /opt (avoids a 0700 home that systemd User= can't traverse)
sudo mkdir -p /opt/chordsplay-sync
sudo cp app.py requirements.txt /opt/chordsplay-sync/
sudo python3 -m venv /opt/chordsplay-sync/venv
sudo /opt/chordsplay-sync/venv/bin/pip install -r /opt/chordsplay-sync/requirements.txt
sudo chown -R chordsync:chordsync /opt/chordsplay-sync

# 3. service (edit the token first!)
sudo cp chordsplay-sync.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now chordsplay-sync
curl -s localhost:8615/health          # -> {"status":"ok"}

# 4. firewall: open 8615 to LAN + VPN only (adjust subnets), token auth on top
sudo firewall-cmd --permanent --zone=internal --add-port=8615/tcp     # VPN (10.8.0.0/24)
sudo firewall-cmd --permanent --zone=public \
     --add-rich-rule='rule family=ipv4 source address=10.0.1.0/24 port port=8615 protocol=tcp accept'
sudo firewall-cmd --reload
```

The service binds `0.0.0.0:8615`; the firewall (not the bind) restricts who can
reach it. **Alternative:** front it with Apache `mod_proxy`
(`apache-chordsplay.conf`) and bind `127.0.0.1` instead — useful on a host
that already exposes Apache to the LAN.

## Point clients at it
In each `~/.chordsplay.py.cfg` (chmod 600 — holds the token):
```ini
[chordsplay.py]
sync_url   = http://host6:8615
sync_token = <same token as the service>
```
Seed central once from the most complete installation:
```bash
chordsplay.py --sync-seed
```
Thereafter every scan pulls before fetching (so a song fetched anywhere is
fetched from UG only once) and pushes what it fetched/edited. Sync failures
never block scanning or playback.
