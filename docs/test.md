# Test plan

## Unit tests (before every commit)

```bash
venv/bin/python -m unittest discover -s tests
```

`tests/test_amp.py` covers the Mustang preset feature (docs/spec-amp.md) without
hardware, display or network. The agent is a fake HTTP server.
`tests/test_exit.py` checks that the scanner stops between songs when asked (it
used to go through every remaining song first) and that the DB is committed and closed.
`tests/test_sync_lock.py` checks that the sync keeps a locked song's text (tag x).
`tests/test_textfit.py` and `tests/test_web.py` belong to the web app (docs/web.md,
"Tests"); `test_web.py` is skipped when Flask isn't installed.
`tests/test_tones.py` covers tones:
* building a tone's preset JSON (level, correction, knobs, ignored empty blocks);
* the knobs offered;
* tones and tone cues in the DB;
* level corrections used only from the signal or played method;
* auditioning on the live slot and fetching presets over HTTP (fake agent);
* tone cues waiting for their base preset.

| Area | What is checked |
|---|---|
| Cue logic | base preset fills the song start, active cue with lead time, renamed-slot detection |
| DB | cues stored/replaced/moved/deleted, slot range enforced, artist presets, rename, usage, re-opening an existing DB |
| `AmpCues` | each cue requested once, seek back / song repeat re-applies, artist → default fallback, no cue leaves the amp alone, renamed slot not switched |
| `AmpClient` | same-origin header, only the newest request is sent, a superseded request is never sent, retry after 503, "already there" not sent, invalid slots, agent not running |
| `AgentControl` | unit started with the amp's sink and stopped without it, a foreign agent left alone, text fallback when `pactl` JSON is broken |

## Manual hardware test (Mustang preset feature, on minor releases)

Needs the amp on and connected, and the agent installed but not running.
`tests/manual_e2e_amp.py` runs the real program on a copy of the database: no
Ultimate Guitar requests, no central sync. The song's audio goes into a null
sink, so nothing plays.

```bash
MOD=$(pactl load-module module-null-sink sink_name=cptest)
PULSE_SINK=cptest xvfb-run -a venv/bin/python tests/manual_e2e_amp.py
pactl unload-module $MOD
```

Expected:
* "amp sink present, starting chordsplay-amp-agent.service" in the log
* amp on slot 2 at the start (after the agent connected) and slot 3 from 8 s
* "G took" well under a second (no GUI stall while the scanner runs)
* the amp back on slot 1, and `systemctl --user is-active chordsplay-amp-agent` → inactive

Last run 2026-11-09 (firmware 1.0.29, BlueZ 5.87): all as expected; G 0.13 s.

## Manual tone level test (after changing tones, the client or the agent)

`tests/manual_tone_level_test.py` runs the whole path:
1. ChordsPlay's `AmpClient` builds tones from a stored preset.
2. The installed agent auditions them on slot 100.
3. The amp's USB output is measured with maneline's `mmp_level`.

Needs the amp on Bluetooth **and** USB, and plugged into an instrument with its volume
at zero. Run it with the agent's venv, which has numpy:

```bash
~/.local/share/chordsplay/maneline-venv/bin/python tests/manual_tone_level_test.py [slot]
```

Expected: every level offset (+3, +6, −3, −6 dB) measures within 1 dB, and the amp
ends on the preset it started on.

Last run 2026-11-10 with 80S Rock: +2.9/+5.8/−3.0/−5.8 dB, reference drift −0.08 dB.
`tests/manual_e2e_amp.py` now also plays a tone cue at 14 s. It reads the starting
preset first and returns to it.

## Manual exit test (on minor releases, and after changing start-up or exit code)

`tests/manual_exit_test.py` runs the real program on a copy of the database for each
way out:
* `q`
* the window's close button
* the close button while a dialog is open
* SIGHUP, SIGTERM and SIGINT
* a real terminal that is closed (pseudo-terminal)

The scanner is held in a hanging fetch from Ultimate Guitar, which is the hardest case.
Nothing plays aloud, nothing is fetched or synced, and stored presets are removed
from the copy. Needs Xvfb.

```bash
venv/bin/python tests/manual_exit_test.py
```

Expected for every case:
* the sound stops within 1 s;
* the process is gone within 5 s (about 3 s when the scanner hangs);
* no `systemd-inhibit`/`tail --pid` is left;
* the amp agent unit is inactive;
* the DB copy passes `quick_check`.

Last run 2026-11-09: sound stopped after 0.0–0.2 s, process gone after 3.1–3.3 s.

## Done by hand

* 2026-11-09: a preset chosen with the **amp's own buttons** while the agent is connected
  moves the ◀ marker in the `g` dialog, so `G` also learns presets set on the amp.

## Still to test by hand

* Can Fender Tone connect while the agent holds the link?
