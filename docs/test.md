# Test plan

## Unit tests (before every commit)

```bash
venv/bin/python -m unittest discover -s tests
```

No amp, display or network needed; `test_web.py` is skipped when Flask isn't installed.
The package builds run them too.

* `tests/test_web.py`: the web app and the amp bridge against it (docs/web.md, "Tests").
* `tests/test_textfit.py`: the wrapping of long comment lines.
* `tests/test_exit.py`: the scanner stops between songs when asked, and the DB is
  committed and closed.
* `tests/test_amp.py`: the Mustang preset logic (docs/spec-amp.md). The agent is a fake
  HTTP server.
* `tests/test_tones.py`: building a tone's preset JSON (level, correction, knobs), the
  knobs offered, tones and tone cues in the DB, level corrections used only from the
  signal or played method, auditioning and fetching presets (fake agent), tone cues
  waiting for their base preset.

| Area (`test_amp.py`) | What is checked |
|---|---|
| Cue logic | base preset fills the song start, active cue with lead time, renamed-slot detection |
| DB | cues stored/replaced/moved/deleted, slot range enforced, artist presets, rename, usage, re-opening an existing DB |
| `AmpCues` | each cue requested once, seek back / song repeat re-applies, artist → default fallback, no cue leaves the amp alone, renamed slot not switched |
| `AmpClient` | same-origin header, only the newest request is sent, a superseded request is never sent, retry after 503, "already there" not sent, invalid slots, agent not running |
| `AgentControl` | unit started with the amp's sink and stopped without it, a foreign agent left alone, text fallback when `pactl` JSON is broken |

## In a browser (after changing the page)

On a desktop browser and a phone (1600×900, 1920×1080, 390×844):

* play, pause, next/previous, reload resumes (Space), played count after the end;
* filter with live count and a regex error; tags by key;
* a display page next to the controller follows next/pause/play within a second and shows
  the expired message after *End all display links*; a paired TV follows;
* PDF tab: `v`, page turns, the display shows the same pages.
* Tone dialog (`g`): the waveform appears; ✎ on a cue, steps, typed position and a click
  into the waveform move the position; *▶ Around* pauses 2 s after it; *Update cue* moves
  the cue and keeps or changes its sound.

## With the amp (after changing the bridge, the agent or the packages)

On the amp computer, amp on and connected by Bluetooth audio (and USB for takes):

| What | Expected |
|---|---|
| Bridge and agent | `systemctl --user status chordsplay-amp-bridge`: the bridge follows the server and starts the agent when the amp appears; the page shows `Mustang <slot> <name>` |
| Switching | a song with a cue at 0, one with an artist preset, one with a cue in the middle: the amp switches at the start and at the cue, while the song plays |
| Tone dialog | *Try now*, level −6 … +6, knobs with *Play while adjusting*, *Save as tone…* |
| Takes | `r`, play a few seconds, `r`; `R` plays it back with song and guitar |
| Levels | `chordsplay-match-levels` (signal cable, or `PLAYED=1`): the corrections arrive on the server |
| Amp check | `mmp-backup`, then `mmp-check <backup dir> --all`: all checks pass |
| Tone levels | `mmp/manual_tone_level_test.py` (amp on Bluetooth and USB, instrument at volume zero, bridge stopped): every offset (+3, +6, −3, −6 dB) within 1 dB, the amp ends on its starting preset |

## After a Mustang firmware update

`mmp-check <backup dir> --all` (docs/web.md, "Operating it").
