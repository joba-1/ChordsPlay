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

// the controller's view: the song text, or one of its PDFs from a page on
let shownPdf = null;    // 'pdf/page' rendered last
function render(force = true) {
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

async function loadState() {
  const res = await fetch('state');
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
      setTimeout(() => loadState().then(() => { if (!expired) follow(); }).catch(() => setTimeout(follow, 5000)), 3000);
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
document.addEventListener('click', toggleFull);
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

loadState().then(() => { if (!expired) follow(); })
  .catch(() => { fail('Cannot reach the server.'); setTimeout(() => location.reload(), 10000); });
keepAwake();
