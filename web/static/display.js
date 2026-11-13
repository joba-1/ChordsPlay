// SPDX-FileCopyrightText: 2016-2026 joba1
// SPDX-License-Identifier: AGPL-3.0-or-later
// ChordsPlay display: shows the song the controller plays, nothing else.
// Opened by a pairing link (…/d/<code>/) without a login; read-only.
'use strict';

const $ = (sel) => document.querySelector(sel);
const sheet = $('#sheet');
const esc = ChordsFit.esc;

let song = null;        // {id, title, artist, capo, text, wrapped}
let state = null;       // controller state: {id, t, playing, at}
let clockOffset = 0;    // server time - local time, seconds
let wakeLock = null;
let expired = false;    // the pairing code is unknown or expired
let pairCode = null;    // a TV (d/tv/) not paired yet: the code it shows
let pairTimer = null;

// the controller's view: the song text, or one of its PDFs from a page on
let shownPdf = null;    // 'pdf/page' rendered last
function render(force = true) {
  if (pairCode || expired) return;      // the pairing code or the "expired" message stays
  const pdf = state?.pdf && song?.pdfs?.some((p) => p.id === state.pdf) ? state.pdf : null;
  if (pdf) {
    const key = pdf + '/' + (state.page || 1);
    if (!force && key === shownPdf) return;
    shownPdf = key;
    PdfView.render(sheet, 'pdf/' + pdf, state.page || 1).catch(() => {
      shownPdf = null;
      ChordsFit.render(sheet, song, '');
    });
    return;
  }
  if (!force && shownPdf === null) return;
  shownPdf = null;
  ChordsFit.render(sheet, song, 'Waiting for a song on the controller…');
}

function showTitle() {
  if (!song) { $('#title').textContent = ''; return; }
  const capo = song.capo ? `<span class="meta">capo ${esc(song.capo)}</span>` : '';
  $('#title').innerHTML = `${esc(song.title)} – ${esc(song.artist)}${capo}`;
  document.title = `${song.title} – ChordsPlay`;
}

function songTime() {
  if (!state) return 0;
  const now = Date.now() / 1000 + clockOffset;
  return state.t + (state.playing ? Math.max(0, now - state.at) : 0);
}

function fmt(t) {
  const s = Math.floor(t);
  return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
}

// progress without the song's length: the bar only shows that time goes on
function tick() {
  $('#state').textContent = state ? (state.playing ? fmt(songTime()) : 'paused ' + fmt(songTime())) : '';
}
setInterval(tick, 500);

function fail(text) {
  song = null;
  sheet.textContent = '';
  sheet.insertAdjacentHTML('beforeend', `<div class="empty">${text}</div>`);
  $('#title').textContent = '';
}

// a TV at d/tv/: shows a code until the controller enters it (key d, "Pair a TV"),
// then it has its token as a cookie and follows like a display
function showPairing(code) {
  pairCode = code;
  fail('Pair this TV with your ChordsPlay:<br>on the controller press <b>d</b> and enter under <i>Pair a TV</i>' +
       `<p class="code">${esc(code)}</p><small>The code is valid for 10 minutes.</small>`);
  clearTimeout(pairTimer);
  let polls = 0;
  const poll = async () => {
    try {
      if ((await (await fetch('pair?code=' + encodeURIComponent(code))).json()).paired) {
        pairCode = null;
        await loadState();
        follow();
        return;
      }
    } catch (e) { /* the server is back in a moment */ }
    if (++polls % 30 === 0) { loadState().catch(() => {}); return; }    // a new code after 10 minutes
    pairTimer = setTimeout(poll, 2000);
  };
  pairTimer = setTimeout(poll, 2000);
}

async function loadState() {
  const res = await fetch('state' + (pairCode ? '?code=' + encodeURIComponent(pairCode) : ''));
  if (res.status === 401) {
    events?.close();
    showPairing((await res.json()).pair);
    return;
  }
  if (res.status === 404) {
    expired = true;
    fail('This display link has expired or was ended.<br>Make a new one on the controller: key <b>d</b>.');
    events?.close();
    return;
  }
  const data = await res.json();
  clockOffset = data.server_time - Date.now() / 1000;
  state = data.state;
  song = data.song;
  showTitle();
  render();
}

let events = null;
function follow() {
  events = new EventSource('events');
  events.onmessage = (e) => {
    const next = JSON.parse(e.data);
    clockOffset = next.server_time - Date.now() / 1000;
    const changed = !song || next.id !== song.id;
    state = next;
    $('#bar').classList.remove('offline');
    if (changed && next.id !== undefined) loadState().catch(() => {});
    else render(false);     // the PDF or its page may have changed
    tick();
  };
  events.onerror = () => {
    $('#bar').classList.add('offline');
    // a closed stream (expired code) is not retried by the browser: check why
    if (events.readyState === EventSource.CLOSED) {
      setTimeout(() => loadState().then(() => { if (!expired && !pairCode) follow(); }).catch(() => setTimeout(follow, 5000)), 3000);
    }
  };
}

new ResizeObserver(() => render()).observe(sheet);

// keep the screen on (TVs and tablets would dim while you play)
async function keepAwake() {
  if (!('wakeLock' in navigator) || wakeLock || document.visibilityState !== 'visible') return;
  try {
    wakeLock = await navigator.wakeLock.request('screen');
    wakeLock.addEventListener('release', () => { wakeLock = null; });
  } catch (e) { /* not allowed (no https, or no user activation yet) */ }
}
document.addEventListener('visibilitychange', keepAwake);

// full screen on click, tap, Enter or OK on a TV remote
function toggleFull() {
  keepAwake();
  if (document.fullscreenElement) document.exitFullscreen();
  else document.documentElement.requestFullscreen().catch(() => {});
}
document.addEventListener('click', (e) => { if (!e.target.closest('#remote')) toggleFull(); });
document.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' || e.key === 'f' || e.key === 'F11') { e.preventDefault(); toggleFull(); }
});

// hide the mouse pointer when it rests
let hideTimer = null;
document.addEventListener('mousemove', () => {
  document.body.classList.remove('idle');
  clearTimeout(hideTimer);
  hideTimer = setTimeout(() => document.body.classList.add('idle'), 3000);
});

// --- a paired TV's remote: previous, play/pause, next for the controller page -------
// Only on d/tv/ (a TV paired once), not on a code link. TV Bro steers with a cursor that
// the D-pad moves: big buttons that appear when it moves (and with any key), clicked with
// OK. In browsers that pass the keys on: ← →, play/pause and the media keys.
const paired = /\/d\/tv\/?$/.test(location.pathname);
let remoteTimer = null;

function remoteShow() {
  if (!paired || pairCode || expired) return;
  $('#remote').classList.add('show');
  clearTimeout(remoteTimer);
  remoteTimer = setTimeout(() => {
    if (!$('#remote').matches(':hover')) $('#remote').classList.remove('show');
    else remoteShow();
  }, 5000);
}

async function remote(action) {
  remoteShow();
  const msg = $('#remote-msg');
  msg.textContent = '…';
  try {
    const res = await fetch('remote', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                        body: JSON.stringify({ action }) });
    const r = await res.json().catch(() => ({}));
    msg.textContent = res.ok && r.ok ? { prev: '⏮', next: '⏭', toggle: r.playing ? '⏸' : '▶' }[action]
      : r.error || 'Not possible (' + res.status + ')';
  } catch (e) { msg.textContent = 'Cannot reach the server.'; }
  clearTimeout(remote.clear);
  remote.clear = setTimeout(() => { msg.textContent = ''; }, 2500);
}

if (paired) {
  document.body.insertAdjacentHTML('beforeend', '<div id="remote">' +
    '<button type="button" data-act="prev" title="Previous song">⏮</button>' +
    '<button type="button" data-act="toggle" title="Play / pause">⏯</button>' +
    '<button type="button" data-act="next" title="Next song">⏭</button>' +
    '<span id="remote-msg"></span></div>');
  $('#remote').addEventListener('click', (e) => {
    const act = e.target.closest('button')?.dataset.act;
    if (act) remote(act);
  });
  document.addEventListener('mousemove', remoteShow);
  document.addEventListener('keydown', (e) => {
    const act = { ArrowLeft: 'prev', ArrowRight: 'next', MediaTrackPrevious: 'prev', MediaTrackNext: 'next',
                  MediaPlayPause: 'toggle', MediaPlay: 'toggle', MediaPause: 'toggle', ' ': 'toggle' }[e.key];
    remoteShow();
    // on a focused button Space (and OK/Enter) press that button; the other keys act everywhere
    if (act && !(e.key === ' ' && e.target.closest('#remote button'))) { e.preventDefault(); remote(act); }
  });
}
loadState().then(() => { if (!expired && !pairCode) follow(); })
  .catch(() => { fail('Cannot reach the server.'); setTimeout(() => location.reload(), 10000); });
keepAwake();
