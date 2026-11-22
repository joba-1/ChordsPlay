# Practice mix: the song's instruments at your own levels

Status: 2.1.0 (beats with beat_this, lead-in); 1.7.0 first version; 1.8.0 (2026-11-20): the guitar as one track again with a lead
timeline, practice sections with a count-in, splitting by tag at night; 1.9.0: the original
file while not mixing, waveforms of the song and its instruments to find and mark a
section, a stricter lead rule; 2.0.0: practice mode complete enough for a major
version (the seek bar shows the played part of the lead marks darker).

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
* **M** mutes, **S** solos (with any instrument soloed, only the soloed ones play);
  *Reset* sets all to 100 %, nothing muted or soloed. 1.7.0–1.8.0 had quick mixes
  (*Everything*, *Without guitar*, *Guitar only*), replaced in 1.8.1 by solo.
* **Play the instruments** (a switch): off, the page plays the **original file**,
  untouched; on, the split file through the mixer. A slider, M or S switches it on by
  itself, *Prepare this song* too. History: 1.7.0 had the switch (*Practice mix on*);
  1.8.1 dropped it (a split song always through the mixer); 1.8.2 put the original song
  into the split file as a seventh pair, played while the mix was untouched, because the
  stems add up to the song only nearly (the model's stems leave a residual 26–36 dB
  below the music, and every pair adds its own Opus noise: summed, *Lakeside Park*
  differs from the MP3 by −26.7 dB with the music at −13.9 dB). Listening (2026-11-20)
  heard no difference between that pair and the instruments at 105 %; what is wanted
  while not practising is the file itself, not an Opus copy of it (−30.7 dB from the
  MP3). So 1.9.0 dropped the pair and brought the switch back, now named for what it
  does. Switching keeps the position (a new source: a short gap, no crossfade).
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
(~2.3 MB per minute). Older files still play: 1.7.0's guitar as three parts by stereo
position (16 channels; the parts get sliders under the guitar's), 1.8.2's extra original
pair (14 channels; not played).

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
  `ChannelSplitter(channels)` → per pair a `ChannelMerger(2)` and a `GainNode` → the output.
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
{"version": 4, "model": "BS-Roformer-SW", "channels": 12,
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
  image (0–1); `lead`: 1 for a lead step, which the page colours. A step is lead when the
  guitar is within 15 dB of its loudest and its centre share (averaged over 2 s) is above
  0.5 and at least 0.10 above the song's own median, for at least 4 s (gaps up to 1 s
  bridged). The steps are cut by time; 1.8.0–2.0.1 put a whole number of STFT frames into
  a step (22 for 21.5), so the marks drifted 2 % early: the solo of *Closer To The Heart*
  at 1:24 instead of 1:26, seen in the guitar's waveform. 1.8.0 took the top quarter of
  each song instead: a song with one guitar always
  in the centre (*Hardest Button To Button*) then showed short marks everywhere. With
  the 1.9.0 rule: *Closer To The Heart* 1:24–1:50 (the solo), *Lakeside Park* the fills
  and the outro solo, *Hardest Button* nothing.
* `tempo` (beats per minute, from the median beat interval), `beats` (seconds) and
  `beat_tracker`: see *Finding the beats*.

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

### Waveforms

The practice dialog has two tabs: *Mix* (the sliders) and *Waves*: the song and the
instruments you choose as waveforms over one time axis, one lane each (the shape
matters, not the amplitude). The dialog is as tall as the window; both tabs share one
area, as tall as the taller one needs at least, so switching them moves nothing below
(2.1.14), and the lanes share what the window leaves for it (they were 34 px each); the dialog is as wide as the window, the sliders as wide as it
and their rows spread over the tab's height (2.1.15), with the beats on the ruler, the lead marks on the
guitar's lane, the marked section and the playing position. Click: go there; drag: mark
a section (on the beat); wheel: zoom around the pointer; Shift+wheel: scroll; *−*, *+*,
*Fit*. The marked section can be resized (drag within 6 px of its start or end; the pointer
shows ↔) and moved (drag inside it; its length kept), on the beat, with Shift held freely
(2.1.13); a loop of it follows at once. *Follow* (2.1.12; on by default, kept in the browser) keeps the playing position in
the centre while the waveform moves under it; scrolling by hand switches it off and the
view stays where it was put (before, it paged after the song a few seconds after a
scroll: "do not jump if scrolled out"); zooming while following keeps the playing
position in the centre; while a section is dragged the view holds still.

2.1.5, after "no waveforms any more" at the Mustang (not reproduced with the same data):
Shift+wheel arrives as `deltaX` in Chromium, which the scrolling ignored; an impossible
view (NaN) drew nothing, ruler included, for good; an error while drawing stopped the
loop. Now the scroll takes whichever delta is larger, the view resets itself, and the
loop draws on. The split's files have the split's time in their address (`?v=`): the
browser kept a split file for an hour, so after *Split it again* it could play the old
file against the new manifest.

The browser doesn't compute the waveforms: decoding six stereo stems of a 4-minute song
would take ~550 MB. The splitter writes them: per track (the song and each stem) the peak
level per 10 ms as one byte, base64 in `<song>.peaks.json` (~60 kB per minute for 7
tracks). Each lane is scaled to its own loudest peak, so a quiet stem shows its shape.
Splits from before 1.9.0 have none: *Split it again*.

### Finding the beats

2.1.0 finds the beats with **beat_this** (JKU Linz, ISMIR 2024; MIT licence; a transformer
on the spectrogram, no tempo or metre model behind it, so tempo changes are followed) on
a **rhythm mix**: drums and bass at full level, the other stems 6 dB quieter
(`--rest-db`). Without beat_this installed the splitter falls back to librosa on the whole
song (one tempo for all of it; 1.8.0–2.0.x did that).

Why, from listening to click tracks (2026-11-20; a click on every beat mixed into the song):

* The whole mix misleads in places: in the intro of *Subdivisions* a synthesizer pattern
  made the tracker insert half beats; drums and bass alone gave a steady pulse there.
* Drums and bass alone miss passages without them: voice and guitar in *Freedom At 21*,
  all of *Love Interruption* (no drums).
* Filling such gaps with the whole mix's beats (tried as v4) found a slow false beat in
  the rhythmless intro of *Sixteen Saltines*. The quieter rest (the user's idea) leaves
  that intro quiet and catches the beat where it starts: at −12 dB "perfect", at −6 dB
  better still (its one early beat is where quiet music starts). On *Freedom At 21*,
  *Sixteen Saltines* and *Love Interruption* the beats were judged right, including a
  tempo change in *Love Interruption*.
* **Bars and time signatures are not used.** beat_this also estimates bar starts; in
  steady 4/4 they were right, in Rush's metre changes (*Subdivisions*) "all over the
  place", even after a Viterbi pass that allowed only common metres (3/4 … 13/8, on an
  eighth-note grid) and made changes rare. The tracker's own confidence was ~1.0 also where
  it was wrong, so the page can't tell good from bad passages either. So there is no stress
  on a supposed "1", and the lead-in (below) is the way in that needs no beats at all.
  Worth another look later: models aimed at odd metres (e.g. *Skip That Beat*, 2025;
  *Explore This!*, a learned tatum grid, 2026).

### Sections and count-in

* A **section** is a start and an end in the song. The page has one unnamed A–B section
  (keys **b** begin and **e** end at the playing position, **o** loop on/off) and the song's
  named sections, kept on the server per user and song (table `practice_sections`), so
  every device has them.
* Marks snap to the nearest beat when the manifest has beats (within 0.25 s), so a loop
  repeats in time.
* **The way in** (a setting): *Clicks* or *Lead-in*, each 2, 4 or 8 beats (or off).
  *Lead-in* plays the song itself from N beats (at the local beat interval) before the
  section's start: it needs no right beat, so it suits any metre. *Clicks*: before a
  section plays, the page pauses at its start and clicks N beats in the song's tempo
  (the median beat interval around the start), the first higher, no bar stress, then plays.
  The clicks (2.1.2) sound like a woodblock: two square tones together, 0.2 s, their level
  a setting in the dialog (a sample click plays when it is changed). History: the sine
  clicks (60 ms) and then square clicks (0.12 s, fast decay) were reported too quiet
  through the Mustang, with the first one "too loud". Measured at the Mustang: the page's
  single output stream went to the Mustang throughout, and a test sequence played straight
  to the Mustang sounded equally quiet: no level control on the way, the clicks were simply
  too short for the ear (it sums loudness over ~200 ms). The loud "first click" was the
  crack of the song stopped mid-wave; now the song fades out over 25 ms before it pauses,
  and in again when it plays.
  Then, at the Mustang with 2.1.1: the first click or two sometimes swallowed, sometimes
  loud, the rest silent; "clearly clicks, not cracks". Chromium pauses ("corks") an output
  stream that stays silent for a moment (seen in PipeWire: `corked=True`), and during a
  count-in the song is paused: each click had to wake the stream and the Bluetooth link.
  2.1.3 keeps the stream busy with a constant 0.001 (−60 dBFS DC, inaudible) while the
  page plays through Web Audio, and starts the clicks 0.3 s after the pause. Result at the
  Mustang: all clicks audible, but far too loud: 2.1.4 scales the level control to a third
  (100 % = a peak of 0.15; default 50 %). The keep-alive also keeps the amp's Bluetooth link
  streaming, so after 5 minutes of pause it rests (the next play or count-in wakes it).
* **The loop's seam**: shortly before the section's end the song fades out, jumps to the
  start and fades in (a gain on the song's way out, the *master*), instead of a hard cut:
  25 ms each in 2.1.2 ("softer, could be a bit more soft"), 60 ms since 2.1.4. Starting from the dialog leaves it open (2.1.1), so the level
  or the way in can be changed and tried again. You can't start the song and
  play at the same moment, so the count-in gives the time to get the hands on the guitar.
  The clicks come from a Web Audio oscillator in the song's own `AudioContext`. Since
  2.1.10 the song runs **silently** from N beats (+0.3 s) before the start, the clicks are
  placed on its own beat times (from its playing position) and it fades in at the start:
  one clock, one way out, so the song comes in exactly a beat after the last click
  (measured: 214 ms after it at 210 ms per beat, at the section start ±2 ms). Before, the
  song was started by a timer after the clicks: `play()` takes ~0.2 s to sound, a whole
  beat at 286 bpm ("8 clicks, a pause, then the guitar", at the Mustang). At the very start
  of a song (nothing before it) the timer stays, started 0.2 s early. With a splitter
  set up, the page's sound goes through Web Audio from the first play on (2.0.2): one
  output stream for song, instruments and clicks. Before, the clicks had a context of
  their own while the original played: a new output stream, which starts on the system's
  default output, and the amp bridge moves new browser streams to the Mustang only every
  2 s, after the count-in (found at the Mustang, 2026-11-20).
* Nothing marked: the dialog offers the whole song as a section (▶, ⟳), and `o` loops it;
  its end is 50 ms before the song's, so the loop and its count-in come before the
  song's own repeat at the end.
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
  About 2.3 MB per minute of music, plus `<song id>.peaks.json` (waveforms, ~60 kB per
  minute). Kept in the server's data folder, not next to the
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
* See also docs/plans.md, *Practice mode*.
* Removing old stems when the folder grows (they are a cache: delete any time).
