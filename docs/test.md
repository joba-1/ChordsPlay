# Test plan

## Unit tests (before every commit)

```bash
venv/bin/python -m unittest discover -s tests
```

`tests/test_amp.py` covers the Mustang preset feature (docs/spec-amp.md) without
hardware, display or network. The agent is a fake HTTP server.

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

Last run 2026-11-09 (firmware 1.1.11, BlueZ 5.87): all as expected; G 0.13 s.

## Still to test by hand

* Choose a preset with the **amp's own buttons** while the agent is connected:
  does the `g` dialog's ◀ (and `G`) follow? `G` depends on it.
* Can Fender Tone connect while the agent holds the link?
