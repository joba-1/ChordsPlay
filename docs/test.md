# Test plan

## Unit tests (before every commit)

```bash
venv/bin/python -m unittest discover -s tests
```

No amp, display or network needed; `test_web.py` is skipped when Flask isn't installed.

* `tests/test_web.py`: the web app and the amp bridge against it (docs/web.md, "Tests").
* `tests/test_textfit.py`: the wrapping of long comment lines.
* `tests/test_exit.py`: the scanner stops between songs when asked (it used to go through
  every remaining song first), and the DB is committed and closed.
* `tests/test_amp.py`: the Mustang preset logic (docs/spec-amp.md). The agent is a fake
  HTTP server.
* `tests/test_tones.py`: tones:
  * building a tone's preset JSON (level, correction, knobs, ignored empty blocks);
  * the knobs offered;
  * tones and tone cues in the DB;
  * level corrections used only from the signal or played method;
  * auditioning on the live slot and fetching presets over HTTP (fake agent);
  * tone cues waiting for their base preset.

| Area (`test_amp.py`) | What is checked |
|---|---|
| Cue logic | base preset fills the song start, active cue with lead time, renamed-slot detection |
| DB | cues stored/replaced/moved/deleted, slot range enforced, artist presets, rename, usage, re-opening an existing DB |
| `AmpCues` | each cue requested once, seek back / song repeat re-applies, artist → default fallback, no cue leaves the amp alone, renamed slot not switched |
| `AmpClient` | same-origin header, only the newest request is sent, a superseded request is never sent, retry after 503, "already there" not sent, invalid slots, agent not running |
| `AgentControl` | unit started with the amp's sink and stopped without it, a foreign agent left alone, text fallback when `pactl` JSON is broken |

## Manual tone level test (after changing tones, the client or the agent)

`mmp/manual_tone_level_test.py` runs the whole path:
1. ChordsPlay's `AmpClient` builds tones from a stored preset.
2. The installed agent auditions them on slot 100.
3. The amp's USB output is measured with maneline's `mmp_level`.

Needs the amp on Bluetooth **and** USB, plugged into an instrument with its volume
at zero, and the amp bridge stopped. Run it with the agent's venv, which has numpy:

```bash
~/.local/share/chordsplay/maneline-venv/bin/python mmp/manual_tone_level_test.py [slot]
```

Expected: every level offset (+3, +6, −3, −6 dB) measures within 1 dB, and the amp
ends on the preset it started on.

Last run 2026-11-10 with 80S Rock: +2.9/+5.8/−3.0/−5.8 dB, reference drift −0.08 dB.

## After a Mustang firmware update

maneline's `mmp_check.py <backup> --all` (docs/web.md, "Operating it"): connection,
firmware against the backup, names, presets, switching, audition.

## Manual check in a browser

See docs/web.md, "Tests".

## Done by hand

* 2026-11-09: a preset chosen with the **amp's own buttons** while the agent is connected
  moves the ◀ marker in the tone dialog, so `G` also learns presets set on the amp.

## Still to test by hand

* Can Fender Tone connect while the agent holds the link?
* Not yet run with the amp since the Mustang parts moved to `mmp/` (2026-11-11): the
  bridge started as `python3 -m mmp.bridge` by its unit, and `mmp/match-preset-levels.sh`
  sending the corrections over the bridge's link.
