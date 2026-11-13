# Design: Mustang Micro Plus presets per song

How ChordsPlay switches a Fender Mustang Micro Plus: the amp agent, the amp bridge
(`mmp/bridge.py`) and the logic they share (`ChordsPlay/amp.py`: `AmpCues`, `AmpClient`,
`AgentControl`). Using it (the tone dialog, levels, knobs, writing an amp slot, takes) is
in [web.md](web.md).

## Goal

When a song plays, the Fender Mustang Micro Plus switches to the tone stored for it, without a hand
on the amp. A song can have **several presets at different times**, for example a quiet acoustic
intro and a loud chorus. Storing a tone must be quick: set it on the amp, press one key.

## Architecture

```mermaid
flowchart LR
  server(["ChordsPlay server<br>cues, tones, levels"])
  subgraph app["amp bridge (mmp/bridge.py)"]
    cues["AmpCues<br>every 200 ms:<br>which cue applies now"]
    client["AmpClient<br>worker thread:<br>newest wish only"]
    control["AgentControl<br>starts/stops the unit"]
  end
  subgraph unit["systemd user unit chordsplay-amp-agent"]
    agent["amp agent<br>maneline mmp-web<br>heartbeat every 0.25 s"]
  end
  sink(["amp's Bluetooth audio sink<br>(pactl)"])
  mmp["Mustang Micro Plus"]
  server -- "song, position, cues<br>(outbound event stream)" --> cues
  cues -- "request(slot) / audition(tone)" --> client
  client -- "HTTP localhost:8081" --> agent
  sink -. "appears / vanishes" .-> control
  control -. "systemctl --user start / stop" .-> agent
  agent -. "Bluetooth LE (LE bearer only)" .-> mmp
```

The bridge and the agent run on the computer the amp is paired with. The bridge follows
the user's session on the server (which song, where in it) and feeds `AmpCues` from the
server's reply for the song (`SongPlan` in `mmp/bridge.py`).

* **ChordsPlay never speaks Bluetooth.** The amp agent is maneline's `mmp-web` (package
  `maneline-mmp`), running as a systemd user unit. It owns the single LE connection to the amp.
  * **Why:** the amp needs a heartbeat at least every 0.5 s, and only one program may hold the link
    (with two subscribers BlueZ duplicates notifications and replies get garbled).
  * The connection should also outlive restarts of the bridge, because a reconnect costs about 3 s.
  * maneline already solves BlueZ LE-bearer control without dropping A2DP audio, plus reconnects,
    battery saving (`--idle-disconnect`) and single ownership. Re-implementing that would mean
    keeping a second copy of the same code.
* **HTTP over localhost** is the boundary:
  * `GET /api/micro-plus` returns the state;
  * `POST /api/micro-plus {"preset": n}` switches and waits for the amp's confirmation;
  * `POST /api/micro-plus/audition {"preset": {…}, "slot": 100}` plays a preset without
    saving it and `GET /api/micro-plus/preset?slot=n` reads a stored one (both for tones);
  * `POST /api/micro-plus/save` writes a slot. Only *Write to amp slot…* calls it.
  * The agent rejects a POST without a same-origin `Origin` header (protection against other web pages).
  * The client is not a browser, so it sends `Origin: <agent base URL>`.
* **Playback never waits for the amp.** Switching runs in the `AmpClient` worker thread.
  * Only the newest requested slot counts. If the agent is waking the amp (3–25 s) and the song
    moves on, the older request is dropped.
* **The agent runs only while it's useful.** `AgentControl` starts the unit when the amp's
  Bluetooth audio sink exists (amp on and connected to this computer) and stops it when the sink is gone.
  * **Why:** while the amp is off, the agent scans for it 10 s out of every 12 s.
  * It only stops a unit it started itself, so an agent started by hand is left alone.
  * **Cold start:** the first switch after the amp appears waits until the agent has connected
    (measured 3–8 s). After that the connection stays up while audio streams to the amp, and a
    switch takes 0.15–0.7 s.
  * **Idle:** after 5 minutes without a switch and without Bluetooth audio streaming, the agent
    drops the control link (`--idle-disconnect 300`); the next switch reconnects first (about
    3 s). The state then says `asleep`: the page shows "Mustang idle".
* **Cue writes vs. the scanner:** the library scan commits at least every 0.2 s (`scan.py`,
  `COMMIT_INTERVAL`), so storing a cue never waits long for SQLite's write lock (a scan over
  NFS is slow).

## Data model (SQLite, the server's database)

```sql
song_presets   (song_id → songs.id, start_ms ≥ 0, slot 1..100, slot_name,
                tone_id → tones.id, level_db, params, PK (song_id, start_ms))
artist_presets (artist PK, slot 1..100, slot_name, level_db, params)
tones          (id, name unique, base_slot 1..100, base_name, level_db, params, created)
preset_levels  (slot PK, name, correction_db, method, measured_at)
```

* A **cue** is `(start_ms, slot)`: from `start_ms` on, the song uses this slot.
* `slot_name` is the preset name at assignment time.
  * If the amp now has a different name in that slot (the preset was replaced in Fender Tone),
    the cue is **not** applied and a warning is shown.
  * **Why:** switching to a slot whose tone was changed is worse than not switching.
  * Saving the cue again accepts the new name.
* `tone_id`: the cue plays a tone instead of the plain slot (see "Tones" below).
* `level_db` and `params` on a cue or an artist preset: level "original" (NULL) or
  level-matched plus an offset, and knobs per cue. The server hands such a cue to the bridge
  as a tone of its own.
* No user column: the amp's slots belong to one physical amp, not to a ChordsPlay user.

## Which slot plays when

1. `cues` = the song's cues, ordered by `start_ms`.
2. `base` = the artist's slot, else a suggested preset (if switched on), else
   `AMP_DEFAULT_SLOT` of the bridge, else none.
3. If there's a base and no cue at 0 ms, the base becomes the cue at 0 ms.
4. The active cue is the last one with `start_ms - lead_ms <= position` (a cue at 0 applies from
   the start). The bridge checks every 200 ms, and in between exactly when the next cue becomes
   due, so a switch is not up to a tick late.
   * `lead_ms` (default −220: 220 ms *after* the cue's time) is measured
     (`mmp/measure_switch_lag.py`): a song played to the amp over Bluetooth reaches its output
     270–320 ms after the player's position; the amp has a switch about 120 ms after it is asked
     and mutes for about 50 ms before the new sound. Asked 220 ms after the cue's time, the new
     sound starts when the cue's moment is heard, within about ±40 ms. Over USB the song is about
     115 ms late: there about −20 is right.
5. When the active cue `(song, index)` changes, the cue's slot is requested.
   * Seeking backwards, restarting a song (the player repeats a song when it ends) or skipping
     all switch correctly, because the active cue is always derived from the position.
6. No cue applies → the amp is left as it is.

## Keys and settings

The page's keys are in [web.md](web.md): `g` opens the tone dialog, `G` learns the amp's
**current** slot as a cue for the playing song (at 0 if the position is under 3 s,
otherwise at the current position; needs a connected agent: while it's asleep, a preset
changed with the amp's buttons isn't known).

The bridge's settings (`~/.config/chordsplay/bridge.env`):

| Variable | Default | Meaning |
|---|---|---|
| `CHORDSPLAY_BRIDGE_URL` | (required) | the line from the page's tone dialog, *Set up amp control…* |
| `AMP_URL` | `http://localhost:8081` (or the agent's `AMP_AGENT_PORT`) | agent base URL |
| `AMP_UNIT` | `chordsplay-amp-agent.service` | systemd user unit to start/stop; empty: don't manage |
| `AMP_SINK` | `Mustang Micro Plus` | text in the audio sink's description that means "amp is connected" |
| `AMP_DEFAULT_SLOT` | (unset) | slot for songs without cues, artist slot or suggestion |
| `AMP_LEAD_MS` | `-220` | switch this many ms before a cue's time; negative: after it (see "Which slot plays when") |

Slot 100 is reserved for tones (auditions) and never offered as a normal preset.

## Security

* The agent listens on port 8081 (or `AMP_AGENT_PORT` from `~/.config/chordsplay/amp-agent.env`)
  on all interfaces. Its host check allows only localhost and
  this machine's hostname, and its Origin check blocks other web pages.
* The bridge talks to the agent on `localhost` only and validates the replies. It needs no open
  port itself: it connects out to the server, and its token (in the link from the page's tone
  dialog, stored on the server only as a hash) is its only key.
* Slot numbers are validated in the DB (`CHECK`) and in the client (1–100).

## Tones: audition fiddling

A **tone** is a base preset (a slot), a **level offset** in dB, and **knob settings**:
amp gain/bass/mid/treble/presence, and per effect block on/off, mix, depth, decay
and feedback.
* **Stored in ChordsPlay** (table `tones`), not on the amp.
* **A cue can play a tone** (`song_presets.tone_id`). The bridge then builds the
  preset JSON and sends it to the agent's audition endpoint, which plays it on the
  live slot (100).
* **Nothing is written to the amp's preset memory,** so there is no flash wear on an
  everyday task. A power cycle forgets the audition.

**Why audition instead of saving presets:**
* Unlimited tones per song.
* Your 100 slots and their names stay untouched.
* Variants such as "80S Rock +3 dB" cost nothing.

**Building the preset JSON** (`amp.build_tone`):
1. Fetch the base preset from the agent (`GET /api/micro-plus/preset?slot=n`); it
   is cached until the agent reconnects.
2. Add a measured level correction for the base slot (table `preset_levels`, see
   below) plus the tone's level offset to the amp volume.
3. Apply the knob settings and name it after the tone.

The cue logic waits for the base preset before it marks a tone cue as applied, so
the first tone after start-up comes a moment later.

**The client** (`AmpClient`) treats slots and tones alike:
* The newest wish wins, and a superseded one is never sent.
* A tone whose content is already playing is not sent again (key = hash of the JSON).
* Selecting a real slot ends the audition.

**In the page's tone dialog:** the level next to the presets (*original*, or matched plus
−6 … +6 dB), the knobs with *Play while adjusting*, and *Save as tone…* (see web.md).

**Level matching across presets** (`preset_levels`, imported from maneline's
`mmp_level.py survey`):
* Only corrections measured with a playing-level signal at the guitar input are used:
  the **signal** method (a test tone through a cable) or the **played** method (you play
  the same riff for every preset).
* The **noise** method (guitar connected at volume zero, gate off) is exact for level
  *changes* within a preset. But it is wrong across presets: on the 71 factory
  presets it spans 59 dB, because noise doesn't drive distortion and compressors like
  a guitar does. So noise corrections are stored but ignored.
* Measuring with the signal method needs an adapter cable (3.5 mm plug to 6.35 mm
  socket) from the computer's headphone output to the amp's plug. See maneline's
  `docs/MUSTANG_MICRO_PLUS_LEVELS.md`.
* A survey (`<backup>/levels-signal.json` or `levels-played.json`) reaches the server
  over the bridge's link, which `chordsplay-match-levels` does at its end (by hand:
  `python3 -m mmp.bridge levels FILE` with the bridge's settings); or
  `chordsplay presets levels FILE` on the server. A noise survey is stored but not used.
* **Played method (no cable):** `PLAYED=1 chordsplay-match-levels`. For every
  preset you play the same short riff when prompted, and the level of its loudest half
  of 100 ms frames is measured, so pauses don't count. It is about ±1 dB from take to
  take. The noise floor is measured once with muted strings; a first take less than
  15 dB above it aborts the run.
* **In sessions:** Enter pauses after the current preset (amp connected); Enter again
  goes on with the practice takes and starts a new session. Every measured preset is saved at once in
  `<backup>/survey-<method>.json`, so Ctrl+C loses at most the preset being played. The
  script remembers the unfinished backup in `~/.local/share/chordsplay/levels/current-<method>`
  and goes on with it (`FRESH=1` starts over). A new session first measures the last
  `OVERLAP` presets (default 2) again; their mean difference to last time is that
  session's offset and is taken off all its levels, so every session is on the scale of
  the first. If the repeats disagree by more than 1.5 dB the script suggests stopping and
  warming up. The levels are sent only when every preset is measured.
* **Signal method (with the cable):** `chordsplay-match-levels` backs up the
  presets, measures every slot 1–99 with the test tone (headphone output at a fixed
  40%), and sends the corrections.
  * The measurement aborts before writing anything if the tone doesn't arrive (less
    than 15 dB above the amp's level without it).
* **No software-only alternative:** a fuzz at full gain in front of the amp lifts the
  input noise by only 9–27 dB, far from playing level. USB and Bluetooth playback don't
  pass the amp model.

**Measured on the amp:** tones built by ChordsPlay from 80S Rock at +3/+6/−3/−6 dB came out
at +2.9/+5.8/−3.0/−5.8 dB at the amp's USB output (`mmp/manual_tone_level_test.py`).

## Installation

The packages `chordsplay-mmp` (bridge, user units, `chordsplay-match-levels`) and
`maneline-mmp` (the agent `mmp-web` and the other `mmp-*` tools); the one-time BlueZ setup
(`Experimental = true`, pairing over LE as well, `mmp-bluez-setup`) and the bridge's line:
[install.md](install.md), "The Mustang parts". maneline's Micro Plus programs come from the
fork `joba-1/maneline` until upstream has them.

## Open question

* Can Fender Tone connect while the agent holds the link, or only while the agent sleeps?
