# Spec: Mustang Micro Plus presets per song (phase A)

Status: phase A, the Tk app on the notebook. The web-app phase (B) is planned on the
Trac page `ChordsPlay`, section "Mustang Micro Plus: preset per song".

## Goal

When a song plays, the Fender Mustang Micro Plus switches to the tone stored for it, without a hand
on the amp. A song can have **several presets at different times**, for example a quiet acoustic
intro and a loud chorus. Storing a tone must be quick: set it on the amp, press one key.

## Architecture

```
ChordsPlay (Tk)                          amp agent (maneline mmp_web.py)        Mustang
 AmpCues (Tk thread, every 200 ms)  ---> AmpClient thread --HTTP localhost:8081--> BLE (LE bearer) --> amp
 AgentControl: start/stop the agent's systemd user unit when the amp's audio sink appears/vanishes
```

* **ChordsPlay never speaks Bluetooth.** The amp agent is maneline's `mmp_web.py`, running as a
  systemd user unit. It owns the single LE connection to the amp.
  * **Why:** the amp needs a heartbeat at least every 0.5 s, and only one program may hold the link
    (with two subscribers BlueZ duplicates notifications and replies get garbled).
  * The connection should also outlive ChordsPlay restarts, because a reconnect costs about 3 s.
  * maneline already solves BlueZ LE-bearer control without dropping A2DP audio, plus reconnects,
    battery saving (`--idle-disconnect`) and single ownership. Re-implementing that would mean
    keeping a second copy of the same code.
* **HTTP over localhost** is the boundary: `GET /api/micro-plus` returns the state, and
  `POST /api/micro-plus {"preset": n}` switches and waits for the amp's confirmation.
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
    switch takes 0.15–0.7 s (measured 2026-11-09).
* **GUI writes vs. the scanner:** cue writes happen in the Tk thread. The startup scan used to hold
  SQLite's write lock for 100 files at a time (seconds over NFS), which froze `G` for about 3 s.
  The scan now commits at least every 0.2 s (`scan.py`, `COMMIT_INTERVAL`).

## Data model (SQLite, local DB)

```sql
song_presets (song_id → songs.id, start_ms ≥ 0, slot 1..100, slot_name, PK (song_id, start_ms))
artist_presets (artist PK, slot 1..100, slot_name)
```

* A **cue** is `(start_ms, slot)`: from `start_ms` on, the song uses this slot.
* `slot_name` is the preset name at assignment time.
  * If the amp now has a different name in that slot (the preset was replaced in Fender Tone),
    the cue is **not** applied and a warning is shown.
  * **Why:** switching to a slot whose tone was changed is worse than not switching.
  * The `g` dialog can accept the new name.
* No user column: the amp's slots belong to one physical amp, not to a ChordsPlay user.
* Not synced to central yet: the sync service doesn't carry it. In phase B it moves to the central DB.

## Which slot plays when

1. `cues` = the song's cues, ordered by `start_ms`.
2. `base` = the artist's slot, else `amp_default_slot` from the config, else none.
3. If there's a base and no cue at 0 ms, the base becomes the cue at 0 ms.
4. Every 200 ms the active cue is the last one with `start_ms - lead_ms <= position`.
   * `lead_ms` (default 300) makes up for the 0.1–0.6 s a switch takes while connected.
5. When the active cue `(song, index)` changes, the cue's slot is requested.
   * Seeking backwards, restarting a song (the player repeats a song when it ends) or skipping
     all switch correctly, because the active cue is always derived from the position.
6. No cue applies → the amp is left as it is.

## Keys (main window)

| Key | Action |
|---|---|
| `g` | Guitar-tone dialog: the song's cues, a position slider that seeks the song, and the amp's 100 presets (current one marked). *Try now* switches the amp without saving. *Save from start / from m:ss / for artist* store a cue. Cues can be retimed, deleted, or have a renamed slot accepted. The slider and `+`/`-` let you set several cues in one go; double-click a cue to play from 2 s before it. The window has a fixed size after the first layout, so changing texts don't resize it. |
| `G` | Learn: store the amp's **current** slot as a cue for the playing song. The cue goes at 0 if the position is under 3 s, otherwise at the current position, rounded down to 0.5 s. A cue within 2 s is replaced. Needs a connected agent: while it's asleep, a preset changed with the amp's buttons isn't known. |

`g`/`G` follow the existing pattern `a`/`A`: the lower-case key opens the dialog, the upper-case key is the quick action.

## Configuration (`~/.chordsplay.py.cfg`, section `[chordsplay.py]`)

| Key | Default | Meaning |
|---|---|---|
| `amp_url` | (unset = feature off) | Agent base URL, e.g. `http://localhost:8081` |
| `amp_unit` | (unset = don't manage) | systemd user unit to start/stop, e.g. `chordsplay-amp-agent.service` |
| `amp_sink` | `Mustang Micro Plus` | Text in the audio sink's description that means "amp is connected" |
| `amp_default_slot` | (unset) | Slot for songs without cues and without an artist slot |
| `amp_lead_ms` | `300` | Switch this many ms before a cue |
| `amp_live_slot` | `100` | Reserved slot for tones (auditions); never offered as a normal preset |

## Security

* The agent listens on port 8081 (or `AMP_AGENT_PORT` from `~/.config/chordsplay/amp-agent.env`,
  written by the installer's `AMP_PORT`) on all interfaces. Its host check allows only localhost and
  this machine's hostname, and its Origin check blocks other web pages.
* ChordsPlay talks to `localhost` only and validates the replies.
* Slot numbers are validated in the DB (`CHECK`) and in the client (1–100).

## Tones: audition fiddling (added 2026-11-10)

A **tone** is a base preset (a slot), a **level offset** in dB, and **knob settings**:
amp gain/bass/mid/treble/presence, and per effect block on/off, mix, depth, decay
and feedback.
* **Stored in ChordsPlay** (table `tones`), not on the amp.
* **A cue can play a tone** (`song_presets.tone_id`). ChordsPlay then builds the
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

**The g dialog's tone panel:**
* "Edit as tone (e)" takes the selected preset as the base.
* A level slider from −12 to +12 dB, with quick buttons −6 −3 0 +3 +6.
* The knobs; "Play while adjusting" auditions 300 ms after the last change.
* "Save tone…", "Tone from start/here (B/H)" (stores a cue), and loading or deleting
  saved tones.

**Level matching across presets** (`preset_levels`, imported from maneline's
`mmp_level.py survey`):
* Only corrections measured with the **signal** method are used: a test tone at the
  guitar input.
* The **noise** method (guitar connected at volume zero, gate off) is exact for level
  *changes* within a preset. But it is wrong across presets: on the 71 factory
  presets it spans 59 dB, because noise doesn't drive distortion and compressors like
  a guitar does. So noise corrections are stored but ignored.
* Measuring with the signal method needs an adapter cable (3.5 mm plug to 6.35 mm
  socket) from the computer's headphone output to the amp's plug. See maneline's
  `docs/MUSTANG_MICRO_PLUS_LEVELS.md`.
* Import a survey with `chordsplay.py --import-levels <backup>/levels-signal.json`.
  A noise survey is stored, with a warning, but not used.
* **Played method (no cable):** `PLAYED=1 contrib/match-preset-levels.sh`. For every
  preset you play the same short riff when prompted, and the level of its loudest half
  of 100 ms frames is measured, so pauses don't count. It is about ±1 dB from take to
  take. The noise floor is measured once with muted strings; a first take less than
  15 dB above it aborts the run.
* **One step, once the cable is there:** `contrib/match-preset-levels.sh` backs up the
  presets, measures every slot 1–99 with the test tone (headphone output at a fixed
  40%), and imports the corrections.
  * The measurement aborts before writing anything if the tone doesn't arrive (less
    than 15 dB above the amp's level without it).
  * A dry run without the cable on 2026-11-10 stopped there as intended.
* **No software-only alternative:** a fuzz at full gain in front of the amp lifts the
  input noise by only 9–27 dB, far from playing level. USB and Bluetooth playback don't
  pass the amp model.

**Verified 2026-11-10 on the amp:**
* Tones built by ChordsPlay from 80S Rock at +3/+6/−3/−6 dB measured +2.9/+5.8/−3.0/−5.8
  dB at the amp's USB output (`tests/manual_tone_level_test.py`).
* A tone cue at 14 s played in the real app as an audition on slot 100
  (`tests/manual_e2e_amp.py`).

## Agent installation

`contrib/install-amp-agent.sh` installs the agent:
* clones maneline (fork branch `micro-plus-preset-backup`: Linux LE bearer, idle disconnect,
  audition and stored-preset API) to `~/.local/share/chordsplay/maneline`; `MANELINE_REPO`
  may be a local checkout
* creates a venv with pinned `bleak` and `dbus-fast`
* installs the user unit `chordsplay-amp-agent.service` (not enabled: ChordsPlay starts it)

One-time BlueZ setup (`Experimental = true`, LE pairing): maneline's `docs/MUSTANG_MICRO_PLUS_LINUX.md`.

## Open points

* ~~Does the amp report a preset chosen with its own buttons while connected?~~ Yes (tested 2026-11-09):
  the ◀ marker follows, so `G` also learns presets set on the amp.
* Can Fender Tone connect while the agent holds the link, or only while the agent sleeps?
