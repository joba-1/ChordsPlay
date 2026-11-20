# Practice mix: the song's instruments at your own levels

Status: 1.7.0 first version; 1.8.0 (2026-11-20): the guitar as one track again with a lead
timeline, practice sections with a count-in, splitting by tag at night.

Play along with a song while hearing less (or none) of its guitar, or only the guitar to
learn the part: the server splits a song once into its instruments ("stems"), and the
page mixes them while it plays, each at its own level (e.g. guitar 20 %, drums 100 %,
the rest 70 %).

## What the user sees

* **Practice** in the top bar (key **x**) opens the practice mix for the playing song.
* Not split yet: *Prepare this song* starts the split on the server (about a minute with
  a GPU); the dialog shows *waiting*, *splitting*, then the mixer.
* The mixer: one slider per instrument (vocals, drums, bass, guitar, keys, other), 0 to
  150 %, with a mute button each.
* The seek bar is coloured where the guitar is loud and sits in the centre of the stereo
  image: on many records that is the lead or solo, while the rhythm guitars are panned to
  the sides (see *Guitar by position*).
* **Sections** for practising a part (see *Sections and count-in*): mark a start and an end
  (keys **b** and **e**, snapped to the song's beats when they are known), play it once or
  loop it (**o**), each start after a count-in in the song's tempo; name a section to keep
  it for the song.
* Quick mixes: *Everything*, *Without guitar*, *Guitar only*. They change only what is
  muted, so the levels you set stay.
* *Practice mix on* switches between the original file and the mix. With all sliders at
  100 % the mix should sound like the original: the model's stems add up to it (residual
  26–36 dB below the music, measured with MP3 stems); in the Opus file each pair
  adds its own coding noise, together ~13 dB below the music on *Fly By Night*, about
  what Opus at 96 kbit/s does to the original alone (−32 dB) summed over 8 pairs. Opus
  is perceptual: this is a waveform difference, not necessarily an audible one. If it
  is audible, the helper's `--kbits` raises the rate.
* The levels belong to the browser (kept in `localStorage`): the amp computer keeps its
  mix, a phone its own. They apply to every song; per-song mixes are a later step.

## Decisions and why

### Split on the server, once, ahead of time; mix live in the browser

* **Splitting** needs a neural network that is too big and too slow for a browser
  (BS-Roformer: ~700 MB of weights; ~45 s per 3-minute song on a 16 GB GPU, minutes on a
  CPU). The server has the files and, ideally, a GPU; the result is kept, so each song is
  split once.
* **Mixing** is cheap: a gain per stem. Doing it in the browser makes a slider act at
  once, without a new file from the server.
* *Splitting while it plays* (live) would need the GPU for the whole song and a stream
  protocol; with ~4–5× real time on the GPU the split is done before you've tuned. Not
  worth the complexity. What the page can offer later instead: prepare the next songs of
  the playlist in the background.

### One multichannel file per song

All stems go into **one Opus file in WebM** with independent channels (Opus channel
mapping family 255): six instruments, 6 stereo pairs = 12 channels, ~96 kbit/s per pair
(~2.3 MB per minute). 1.7.0 split the guitar into three parts by stereo position (16
channels); the page still plays such files (the parts get sliders under the guitar's).

* One `<audio>` element plays it, so the stems **cannot drift apart**, seeking and
  streaming work as for the original (range requests), and the browser holds only what
  it buffers. The alternatives were worse: one `<audio>` per stem drifts by tens of ms
  after seeks; decoding every stem into `AudioBuffer`s costs ~550 MB of RAM for a 4-minute
  song (16 channels of float32 at 48 kHz) and a full download before the first note.
* Checked in Chromium 149 (2026-11-20): both `<audio>` and `decodeAudioData` deliver all
  16 channels separately (a test file with a different tone and level per channel).
  Firefox and Safari: not checked. A browser that gives fewer channels would play a
  wrong mix; the page can't tell (Web Audio doesn't report the element's channel count).
* The page routes the element through Web Audio: `MediaElementSource` →
  `ChannelSplitter(16)` → per pair a `ChannelMerger(2)` and a `GainNode` → the output.
  The Web Audio graph is created the first time the practice mix is used (an element
  can't be detached from it again); from then on the page's sound goes through the
  `AudioContext`, and choosing the audio output sets the context's output
  (`AudioContext.setSinkId`, Chromium 110+) as well as the element's.

### The splitter is a separate program

ChordsPlay calls a command (`CHORDSPLAY_STEMS_COMMAND`) and doesn't import the model.

* The model needs PyTorch with CUDA: several GB, its own Python and release cycle, not
  something the distribution packages can carry. Without the setting the feature is
  simply not offered.
* The command gets two arguments, the song's file and the output file (`….webm`), and
  writes the output and a manifest next to it (`….json`). Any program that keeps this
  contract will do; the one in the repo is `contrib/stems/chordsplay-stems` (Python,
  [python-audio-separator](https://github.com/nomadkaraoke/python-audio-separator),
  model `BS-Roformer-SW`).
* Model choice (2026-11-20, by listening to three songs with htdemucs_6s and
  BS-Roformer-SW): BS-Roformer-SW sounds better; it is about twice as slow (40–60 s per
  song on an RTX 5060 Ti against 18–27 s; on the CPU htdemucs_6s takes 64–105 s). Both
  give the same six stems. The weights' licence is listed as unknown: fine for private
  use, and the reason why ChordsPlay ships no weights and doesn't depend on a model.
* The GPU is shared (an LLM may hold most of its memory). The helper checks free GPU
  memory first (~4 GB needed) and with `--device gpu` (default) fails with *GPU busy*,
  which the page shows; `--device auto` falls back to the CPU, `--device cpu` always
  uses it.

Manifest (`<song>.json`):

```json
{"version": 2, "model": "BS-Roformer-SW", "channels": 12,
 "stems": [{"name": "vocals", "pair": 0}, {"name": "drums", "pair": 1},
           {"name": "bass", "pair": 2}, {"name": "piano", "pair": 3},
           {"name": "other", "pair": 4}, {"name": "guitar", "pair": 5}],
 "timeline": {"step": 0.5, "guitar_db": [-41, -38, ...], "guitar_center": [0.2, 0.7, ...]},
 "tempo": 139.7, "beats": [0.42, 0.85, ...]}
```

A stem without `group` is an instrument with one slider; stems with the same `group`
are parts of one instrument (its slider, plus one per part; 1.7.0's guitar parts).
`timeline` and `tempo`/`beats` are optional: without them there is no lead colouring and
the count-in uses a default tempo.

* `timeline.guitar_db`: the guitar's level per step (0.5 s), dB relative to its loudest
  step; `guitar_center`: the share of the guitar's energy in the centre of the stereo
  image (0–1). The page colours a step as lead when the guitar is within 15 dB of its
  loudest and more than half of it is in the centre.
* `tempo` (beats per minute) and `beats` (seconds) from librosa's beat tracker on the
  drum stem (on the whole mix when the drums are nearly silent).

### Guitar by position

Where each sound of the guitar stem sits in the stereo image, per frequency and moment
(short-time Fourier transform; centre zone ±0.35 of the pan range), tells lead from
rhythm on many records. Tried on two Rush songs (2026-11-20): the centre rises by 6–10
dB exactly where the solos are, while the sides keep the rhythm guitars.

1.7.0 played the three zones as separate audio. Listening showed what the numbers
didn't: the parts are clearly guitar but badly distorted, because soft masks per
frequency bin leave "musical noise". 1.8.0 keeps the measurement and drops the audio:
the guitar is one clean stem again, and the zones only give the lead timeline in the
manifest. (Mid/side sliders would be clean but separate far less; not built.) It stays a
heuristic: on a mono or live recording, or with the solo panned like the rhythm guitar,
the timeline shows nothing useful.

### Sections and count-in

* A **section** is a start and an end in the song. The page has one unnamed A–B section
  (keys **b** begin and **e** end at the playing position, **o** loop on/off) and the song's
  named sections, kept on the server per user and song (table `practice_sections`), so
  every device has them.
* Marks snap to the nearest beat when the manifest has beats (within 0.25 s), so a loop
  repeats in time.
* **Count-in:** before a section plays, the page pauses at its start and clicks N beats
  (2, 4 or 8; default 4) in the song's tempo, then plays. You can't start the song and
  play at the same moment, so the count-in gives the time to get the hands on the guitar.
  The clicks come from a Web Audio oscillator on the same output as the song; the song
  starts by a timer (~10–30 ms accuracy), enough to come in on the beat.
* Loop: at the section's end the song jumps back to its start, with a count-in each time
  or straight on (a setting). The end is checked every 20 ms, not by `timeupdate` (only
  ~4 times a second).
* The seek bar shows the A–B section; the named ones are listed in the practice dialog
  with ▶ (once), ⟳ (loop) and ✕.
* API: `GET/POST api/song/<id>/sections` (`{name, start, end}` in seconds),
  `PUT/DELETE api/sections/<id>`; a user sees and changes only their own.

### Splitting by tag, at night

The GPU is shared: an LLM (Ollama) may hold most of it, and splitting then fails with
*GPU busy*. Two answers, both wanted:

* **At once, for the song you start on** (*Prepare this song*): with `--free-ollama URL`
  the splitter asks Ollama to unload its models when the GPU is busy, waits until the
  memory is free (up to 30 s) and splits. Ollama loads a model again by itself on its
  next request, so nothing has to be given back; the cost is a slower first answer for
  whoever uses the LLM next. Decided 2026-11-20: a few minutes without the LLM are
  always acceptable.
* **In bulk, at a quiet time**, for the songs you mark (a tag):

* `chordsplay stems queue --user NAME --tag KEY` queues every song that user has tagged
  with KEY and that isn't split (failed ones again); `chordsplay stems wait` returns when
  nothing is queued or running (or after `--timeout`); `chordsplay stems list` shows the
  states. The server's worker does the splitting: one worker, so never two splits at
  once. It looks for new rows every minute.
* A timer on the server queues and waits (example in install.md: site configuration,
  not part of the package: the user, the tag and the time are the site's). Freeing the
  GPU is the splitter's job (`--free-ollama`), so the timer needs no rights beyond the
  service user's.
* By hand: *Prepare this song*, and *Prepare the playlist* (every song of the playlist
  that isn't split).

### Server side

* Table `stems` (one row per song): `song_id`, `source` (size and mtime of the song's
  file when it was split), `state` (`queued`, `running`, `ready`, `failed`), `error`,
  `queued`, `finished`, `seconds`. A song whose file changed since counts as not split.
* One worker thread, one split at a time (the GPU is the bottleneck), oldest first; a
  restart puts a `running` job back to `queued`. The command runs without a shell (its
  words split with `shlex`), with a timeout (`CHORDSPLAY_STEMS_TIMEOUT`, default 1800 s),
  into a temporary name that is renamed when done.
* Files: `CHORDSPLAY_STEMS_DIR/<song id>.webm` and `.json` (default
  `~/.local/share/chordsplay-web/stems`; the package: `/var/lib/chordsplay/stems`).
  About 2.3 MB per minute of music. Kept in the server's data folder, not next to the
  songs (decided 2026-11-20): no write access to the music share needed, and other
  players don't see files they can't use.
* API (with login, like the rest of `api/`):
  * `GET api/song/<id>/stems`: `{offered, state, error, manifest, seconds}`;
    `offered` is false without a command.
  * `POST api/song/<id>/stems`: queue the split (nothing happens if it is ready or
    queued); 409 when not offered.
  * `GET api/stems/<id>.webm`: the file, with range requests.
* The split's progress isn't pushed: the dialog asks every 2 s while it is open and the
  song is queued or running.

### Not affected

The waveform in the tone dialog, cues, the amp's timing, takes and the displays use the
original song and its timeline; the mix has the same length and timing. The bridge moves
the browser's playback to the Mustang by the browser's program name, which the Web Audio
output keeps.

## Later

* Per-song mixes (stored on the server per user), and a mix per cue (quieter guitar in
  the solo only).
* Removing old stems when the folder grows (they are a cache: delete any time).
