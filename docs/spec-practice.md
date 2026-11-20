# Practice mix: the song's instruments at your own levels

Status: first version in 1.7.0 (2026-11-20).

Play along with a song while hearing less (or none) of its guitar, or only the guitar to
learn the part: the server splits a song once into its instruments ("stems"), and the
page mixes them while it plays, each at its own level (e.g. guitar 20 %, drums 100 %,
the rest 70 %).

## What the user sees

* **Practice** in the top bar (key **x**) opens the practice mix for the playing song.
* Not split yet: *Prepare this song* starts the split on the server (about a minute with
  a GPU); the dialog shows *waiting*, *splitting*, then the mixer.
* The mixer: one slider per instrument (vocals, drums, bass, guitar, keys, other), 0 to
  150 %, with a mute button each. The guitar has three more sliders under it for the
  parts by stereo position (left, centre, right): on many records the rhythm guitars are
  panned to the sides and the solo sits in the centre (see *Guitar by position*).
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
mapping family 255): six instruments, the guitar as three stereo parts, so 8 stereo
pairs = 16 channels, ~96 kbit/s per pair (~3 MB per minute).

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
{"version": 1, "model": "BS-Roformer-SW", "channels": 16,
 "stems": [{"name": "vocals", "pair": 0}, {"name": "drums", "pair": 1},
           {"name": "bass", "pair": 2}, {"name": "piano", "pair": 3},
           {"name": "other", "pair": 4},
           {"name": "guitar-left", "pair": 5, "group": "guitar"},
           {"name": "guitar-center", "pair": 6, "group": "guitar"},
           {"name": "guitar-right", "pair": 7, "group": "guitar"}]}
```

A stem without `group` is an instrument with one slider; stems with the same `group`
are parts of one instrument (its slider, plus one per part). A splitter without the pan
step lists `guitar` as one stem; the page handles both.

### Guitar by position

The guitar stem is split by where each sound sits in the stereo image, per frequency
and moment (short-time Fourier transform with soft masks; centre zone ±0.35 of the
pan range). This costs a second or two per song and nothing at playback. Tried on two
Rush songs (2026-11-20): the centre part rises by 6–10 dB exactly where the solos are,
while the sides keep the rhythm guitars; an acoustic intro panned left lands on the
left. It is a heuristic: on a mono or live recording, or with the solo panned like the
rhythm guitar, the parts don't separate, and the guitar slider alone is the useful one.
The three parts add up to the guitar stem, so nothing is lost by the split.

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
  About 3 MB per minute of music.
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
* Prepare the next songs of the playlist in the background; a command to split a whole
  folder at night.
* Removing old stems when the folder grows (they are a cache: delete any time).
