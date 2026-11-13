#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2016-2026 joba1
# SPDX-License-Identifier: AGPL-3.0-or-later
'''
How late does the Mustang Micro Plus switch, compared with the song you hear?

Plays a test song with a short beep every few seconds to the amp over Bluetooth, the
way the page plays a song, and switches presets at the beeps the way the amp bridge
does (its cue logic, its 0.2 s tick, its lead). The amp's USB output is recorded: it
carries the song as you hear it (so the beeps show when each song moment reaches you)
and the guitar path. Nothing needs to be played: the presets used differ strongly in
the noise of the idle input (a clean one is nearly silent, a driven one is not), so
each switch shows as a step in the noise. Lag = step minus beep, for every switch.

Needs, on the computer the amp is paired with: the amp on, connected by Bluetooth audio
and USB, the amp agent running, the amp bridge stopped (it would switch too), numpy,
pactl, pw-play and pw-record.

  python3 mmp/measure_switch_lag.py [--via bluetooth|usb] [--lead MS] [--switches 24] [--quiet 41] [--noisy 1]

Writes the test song, the recording and the results to
~/.local/share/chordsplay/lagtest-<time>/ and prints the summary.
'''

import argparse
import json
import math
import os
import subprocess
import sys
import time
import urllib.request
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
for base in (os.path.dirname(HERE), '/usr/share/chordsplay-mmp'):
    if os.path.isfile(os.path.join(base, 'ChordsPlay', 'amp.py')):
        sys.path.insert(0, base)
        break
from ChordsPlay.amp import LEAD_MS, AmpClient, AmpCues          # noqa: E402

RATE = 48000
TICK_S = 0.2                # as the bridge (mmp/bridge.py): every 0.2 s, and when a cue is due
DUE_MARGIN_S = 0.002
LEAD_IN_MS = 2000
BEEP_MS = 15
FRAME_MS = 2                # resolution of the noise level


def pactl_json(kind):
    out = subprocess.run(['pactl', '-f', 'json', 'list', kind], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def find_devices(name, via):
    '''(sink for the song: Bluetooth or USB, USB source) of the amp, by their names.'''
    if via == 'usb':
        sink = next((s['name'] for s in pactl_json('sinks')
                     if name.replace(' ', '_') in s['name'] and s['name'].startswith('alsa_output')), None)
    else:
        sink = next((s['name'] for s in pactl_json('sinks')
                     if name in (s.get('description') or '') and s['name'].startswith('bluez_output')), None)
    source = next((s['name'] for s in pactl_json('sources')
                   if name.replace(' ', '_') in s['name'] and s['name'].startswith('alsa_input')), None)
    return sink, source


def agent_state(url):
    with urllib.request.urlopen(url + '/api/micro-plus', timeout=10) as r:
        return json.loads(r.read())


def test_song(path, switches, period_ms):
    '''Mono 48 kHz: silence with a 2 kHz beep of 15 ms at every switch time.'''
    total = LEAD_IN_MS + switches * period_ms + 1500
    x = np.zeros(int(RATE * total / 1000), dtype=np.float64)
    n = int(RATE * BEEP_MS / 1000)
    t = np.arange(n) / RATE
    fade = np.minimum(1, np.minimum(t, t[::-1]) / 0.002)
    beep = 0.5 * np.sin(2 * math.pi * 2000 * t) * fade
    times = [LEAD_IN_MS + k * period_ms for k in range(switches)]
    for ms in times:
        i = int(RATE * ms / 1000)
        x[i:i + n] = beep
    with wave.open(path, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((x * 32767).astype('<i2').tobytes())
    return times


class Plan(object):
    '''The test song's cues for AmpCues, in the form of the server's database.'''

    def __init__(self, cues):
        self.cues = cues

    def getSongPresets(self, song_id):
        return self.cues

    def getPlayInfo(self, song_id):
        return ('lagtest', 'lagtest')

    def getArtistPreset(self, artist):
        return None


def analyse(rec_path, times, expect_noisy, offset_ms):
    with wave.open(rec_path) as w:
        raw = np.frombuffer(w.readframes(w.getnframes()), dtype='<i2').astype(np.float64) / 32768
        x = raw.reshape(-1, w.getnchannels()).mean(axis=1)
    # beeps: the song as it arrives at the amp's output. The amp mixes Bluetooth audio
    # quietly into its USB output, near the noise of a driven preset, so look at 2 kHz
    # only: the signal shifted down by 2 kHz and averaged over the beep's length is
    # large only during a beep. Its average rises linearly over one beep length, so
    # the beep starts half a beep length before it reaches half its height.
    w = int(RATE * BEEP_MS / 1000)
    t = np.arange(len(x)) / RATE
    z = x * np.exp(-2j * math.pi * 2000 * t)
    c = np.concatenate([[0], np.cumsum(z)])
    mag = np.abs(c[w:] - c[:-w]) / w                  # mag[i]: average over x[i:i+w]
    mag = np.concatenate([np.zeros(w - 1), mag])      # ... so that mag[i] covers x[i-w+1:i+1]
    # The beeps are exactly where the test song has them, all shifted by one delay: find
    # the delay at which the 2 kHz energy at all beeps together is largest (0 to 3 s after
    # the playback started), then time each beep within 30 ms of there.
    pos = np.array([int(RATE * (offset_ms + ms) / 1000) for ms in times])
    shifts = np.arange(0, int(RATE * 3), int(RATE / 1000))
    shifts = shifts[pos[-1] + shifts + 2 * w < len(mag)]
    score = np.array([mag[pos + d + w].sum() for d in shifts])
    delay = int(shifts[int(np.argmax(score))])
    onsets, weak = [], 0
    for p in pos + delay:
        a, b = p - int(RATE * 0.03), p + w + int(RATE * 0.03)
        top = a + int(np.argmax(mag[a:b]))
        floor = np.median(mag[max(0, p - RATE // 2):p - int(RATE * 0.05)])
        if mag[top] < 2.5 * floor:
            weak += 1
        half = top - w + int(np.flatnonzero(mag[top - w:top + 1] >= mag[top] / 2)[0])
        onsets.append(int(half - w / 2))
    if weak > len(times) // 2:
        sys.exit('{} of {} beeps hardly stand out of the noise: turn the amp\'s volume up, or choose a '
                 'less noisy --noisy preset'.format(weak, len(times)))
    # noise level per 2 ms frame, smoothed over 10 ms (centred, so the timing doesn't
    # shift: single frames of noise dip). The beeps are taken out by removing 1.4 to
    # 2.6 kHz: the noise of a driven preset is broadband, so the step stays, and a
    # switch that falls onto a beep is still seen.
    spectrum = np.fft.rfft(x)
    freq = np.fft.rfftfreq(len(x), 1 / RATE)
    spectrum[(freq > 1400) & (freq < 2600)] = 0
    y = np.fft.irfft(spectrum, len(x))
    f = int(RATE * FRAME_MS / 1000)
    frames = len(y) // f
    power = np.mean(y[:frames * f].reshape(frames, f) ** 2, axis=1)
    power = np.convolve(power, np.ones(5) / 5, mode='same')
    level = 10 * np.log10(power + 1e-20)
    valid = level[~np.isnan(level)]
    low, high = np.percentile(valid, 10), np.percentile(valid, 90)
    if high - low < 15:
        sys.exit('the presets differ by only {:.0f} dB in noise ({:.0f} / {:.0f} dB): choose others '
                 '(--quiet, --noisy) or unplug what is in the guitar input'.format(high - low, low, high))
    mid = (low + high) / 2
    hold = int(30 / FRAME_MS)              # the new state for 30 ms, the old one before

    def side(seg, noisy):
        seg = seg[~np.isnan(seg)]
        return len(seg) > 0 and np.mean(seg > mid if noisy else seg < mid) >= 0.9

    lags = []
    for k, onset in enumerate(onsets):
        noisy = expect_noisy(k)
        a = max(hold, (onset - int(RATE * 1.4)) // f)
        b = min(frames - hold, (onset + int(RATE * 1.4)) // f)
        found = next((i for i in range(a, b) if side(level[i:i + hold], noisy) and side(level[i - hold:i], not noisy)), None)
        lags.append(None if found is None else round((found * f - onset) / RATE * 1000, 1))
    audio_delay = [round((o / RATE) * 1000 - offset_ms - t, 1) for o, t in zip(onsets, times)]
    return lags, audio_delay, (round(low, 1), round(high, 1))


def stats(values):
    v = np.array([x for x in values if x is not None], dtype=float)
    if not len(v):
        return None
    return {'n': int(len(v)), 'mean': round(float(v.mean()), 1), 'median': round(float(np.median(v)), 1),
            'std': round(float(v.std()), 1), 'min': round(float(v.min()), 1), 'max': round(float(v.max()), 1),
            'p10': round(float(np.percentile(v, 10)), 1), 'p90': round(float(np.percentile(v, 90)), 1)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--switches', type=int, default=24, help='number of switches (default 24)')
    ap.add_argument('--period', type=int, default=3000, help='ms between switches (default 3000)')
    ap.add_argument('--quiet', type=int, default=41, help='a preset with a nearly silent idle input (default 41)')
    ap.add_argument('--noisy', type=int, default=1, help='a preset with audible idle noise (default 1)')
    ap.add_argument('--lead', type=int, default=int(os.environ.get('AMP_LEAD_MS', str(LEAD_MS))),
                    help='switch this many ms before a cue (negative: after it), as the bridge '
                         '(default AMP_LEAD_MS or {})'.format(LEAD_MS))
    ap.add_argument('--agent', default='http://localhost:{}'.format(os.environ.get('AMP_AGENT_PORT') or '8081'))
    ap.add_argument('--name', default='Mustang Micro Plus', help='text in the amp\'s audio device names')
    ap.add_argument('--via', choices=('bluetooth', 'usb'), default='bluetooth',
                    help='how the song reaches the amp, as the page plays it (default bluetooth)')
    args = ap.parse_args()

    if subprocess.run(['systemctl', '--user', 'is-active', '--quiet', 'chordsplay-amp-bridge']).returncode == 0:
        sys.exit('the amp bridge is running and would switch too: '
                 'systemctl --user stop chordsplay-amp-bridge (start it again afterwards)')
    try:
        state = agent_state(args.agent)
    except OSError as e:
        sys.exit('no amp agent at {} ({}): systemctl --user start chordsplay-amp-agent'.format(args.agent, e))
    sink, source = find_devices(args.name, args.via)
    if not sink:
        sys.exit('the amp is not connected for Bluetooth audio: connect it (bluetoothctl connect <address>)'
                 if args.via == 'bluetooth' else 'the amp\'s USB output is not there: connect the amp by USB')
    if not source:
        sys.exit('the amp\'s USB input is not there: connect the amp by USB')
    started_on = state.get('preset')

    out = os.path.expanduser(time.strftime('~/.local/share/chordsplay/lagtest-%Y%m%d-%H%M%S'))
    os.makedirs(out)
    song, rec = os.path.join(out, 'song.wav'), os.path.join(out, 'recording.wav')
    times = test_song(song, args.switches, args.period)
    noisy_at = lambda k: k % 2 == 0           # noqa: E731
    cues = [(0, args.quiet, None, None)] + [(t, args.noisy if noisy_at(k) else args.quiet, None, None)
                                            for k, t in enumerate(times)]

    client = AmpClient(args.agent).start()
    sent = []                                 # (ms in the song when asked, ms the agent took)
    original_send = client._send

    def timed_send(target):
        t0 = time.monotonic()
        try:
            return original_send(target)
        finally:
            sent.append(((t0 - clock[0]) * 1000 if clock[0] else None, (time.monotonic() - t0) * 1000))
    client._send = timed_send
    clock = [None]

    volume = subprocess.run(['pactl', 'get-sink-volume', sink], capture_output=True, text=True).stdout
    volume = next((p.strip() for p in volume.split('/') if p.strip().endswith('%')), None)
    subprocess.run(['pactl', 'set-sink-volume', sink, '100%'], check=False)
    print('Starting on preset {} (quiet), amp sink {} at 100 % for the test'.format(args.quiet, sink))
    client.request(args.quiet)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and client.snapshot()['state'].get('preset') != args.quiet:
        time.sleep(0.2)
    sent.clear()
    time.sleep(1.0)

    recorder = subprocess.Popen(['pw-record', '--target', source, '--rate', str(RATE), '--channels', '2',
                                 '--format', 's16', rec], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rec_started = time.monotonic()
    time.sleep(1.0)
    player = subprocess.Popen(['pw-play', '--target', sink, song], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    clock[0] = time.monotonic()
    cue_logic = AmpCues(Plan(cues), client, lead_ms=args.lead)
    print('{} switches between presets {} and {}, every {:.1f} s, lead {} ms: {:.0f} s'.format(
        args.switches, args.quiet, args.noisy, args.period / 1000, args.lead,
        (LEAD_IN_MS + args.switches * args.period + 1500) / 1000))
    while player.poll() is None:
        pos = (time.monotonic() - clock[0]) * 1000
        cue_logic.tick(1, pos)
        due = cue_logic.due_in(1, pos)
        time.sleep(TICK_S if due is None else min(TICK_S, due / 1000 + DUE_MARGIN_S))
    time.sleep(1.0)
    recorder.terminate()
    recorder.wait(timeout=5)
    if volume:
        subprocess.run(['pactl', 'set-sink-volume', sink, volume], check=False)
    if started_on:
        client.request(started_on)
        time.sleep(2)
    client.stop()

    lags, audio_delay, levels = analyse(rec, times, noisy_at, (clock[0] - rec_started) * 1000)
    agent_ms = [round(d, 1) for _, d in sent]
    result = {'via': args.via, 'lead_ms': args.lead, 'tick_ms': TICK_S * 1000, 'quiet': args.quiet, 'noisy': args.noisy,
              'noise_db': levels, 'lag_ms': lags, 'lag': stats(lags), 'agent_ms': agent_ms,
              'agent': stats(agent_ms), 'song_delay_ms': audio_delay, 'song_delay': stats(audio_delay)}
    with open(os.path.join(out, 'result.json'), 'w') as f:
        json.dump(result, f, indent=1)

    # the new sound starts when the noisy preset's noise comes up (the amp mutes ~50 ms
    # before, which a switch to the quiet preset shows); the first switch is left out, so a
    # glitch right after the playback starts cannot shift the result
    starts = [v for k, v in enumerate(lags) if k > 0 and noisy_at(k)]
    stops = [v for k, v in enumerate(lags) if not noisy_at(k)]
    result['new_sound'] = stats(starts)
    result['old_sound_stops'] = stats(stops)
    with open(os.path.join(out, 'result.json'), 'w') as f:
        json.dump(result, f, indent=1)
    s = result['lag']
    print()
    print('Noise of the idle input: {} dB (quiet) / {} dB (noisy)'.format(*levels))
    print('Lag per switch (ms, negative: before the beep):')
    print('  ' + ' '.join('—' if v is None else '{:.0f}'.format(v) for v in lags))
    if s:
        print('Lag: median {median} ms, mean {mean} ms, spread {min} … {max} ms '
              '(10–90 %: {p10} … {p90}, std {std}), {n} of {total} switches found'.format(total=len(lags), **s))
        print('Agent: a switch took {median} ms (median), {min} … {max} ms, until the amp confirmed'.format(
            **result['agent']))
        print('Song: reaches the amp\'s output about {median} ms after it is played ({via}; '
              'the recorder start adds a little)'.format(via=args.via, **result['song_delay']))
        n, o = result['new_sound'], result['old_sound_stops']
        if n and o:
            print('New sound starts: median {median} ms, {min} … {max} ms (10–90 %: {p10} … {p90}); '
                  'the old one stops about {stop} ms before'.format(stop=round(n['median'] - o['median']), **n))
            print('With lead {} ms the new sound comes {} ms {} the cue; a lead of about {} ms would put it on '
                  'the cue (AMP_LEAD_MS in the bridge settings).'.format(
                      args.lead, abs(n['median']), 'after' if n['median'] > 0 else 'before',
                      int(round(args.lead + n['median'], -1))))
    print('Files: ' + out)


if __name__ == '__main__':
    main()
