// ChordsPlay web page: the controller. Plays the audio, shows the song text
// as large as the screen allows, and keeps the playlist, filter and tags in
// step with the server. No build step, no libraries.
'use strict';

const $ = (sel) => document.querySelector(sel);
const player = $('#player');
const sheet = $('#sheet');
const tbody = $('#list tbody');

const LINE_HEIGHT = 1.15;   // must match #sheet pre in style.css
const MAX_COLUMNS = 4;
const COLUMN_GAP = 3;       // characters between columns

let playlist = [];          // rows from the server: {id, title, artist, ...}
let pos = -1;               // index in playlist of the current song
let song = null;            // the current song from /api/song
let sortKey = null, sortUp = true;
let wakeLock = null;

// --- server -------------------------------------------------------------------

async function api(path, options = {}) {
  const init = { ...options, headers: { 'Content-Type': 'application/json' } };
  if (init.body !== undefined && typeof init.body !== 'string') init.body = JSON.stringify(init.body);
  const res = await fetch('api/' + path, init);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.error || res.statusText);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

function toast(text, ms = 2500) {
  const t = $('#toast');
  t.textContent = text;
  t.classList.add('show');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.remove('show'), ms);
}

// --- fitting the text ---------------------------------------------------------

// width of one character of the sheet font, per pixel of font size
let charRatio = null;
function measureChar() {
  const probe = document.createElement('pre');
  probe.style.cssText = 'position:absolute;visibility:hidden;font-size:100px;margin:0';
  probe.textContent = 'M'.repeat(50);
  sheet.appendChild(probe);
  const w = probe.getBoundingClientRect().width;
  probe.remove();
  return w / 50 / 100;
}

// Split lines into n columns of about equal length; a column preferably
// starts after an empty line (a new verse) if one is near the even split.
function splitColumns(lines, n) {
  if (n === 1) return [lines];
  const target = Math.ceil(lines.length / n);
  const slack = Math.max(2, Math.round(target * 0.2));
  const cols = [];
  let start = 0;
  for (let c = 1; c < n && start < lines.length; c++) {
    let cut = Math.min(lines.length, start + target);
    for (let d = 0; d <= slack; d++) {
      if (cut - d > start && lines[cut - d - 1] !== undefined && lines[cut - d - 1].trim() === '') { cut -= d; break; }
      if (cut + d < lines.length && lines[cut + d - 1].trim() === '') { cut += d; break; }
    }
    cols.push(lines.slice(start, cut));
    start = cut;
    while (start < lines.length && lines[start].trim() === '') start++;  // no blank line on top
  }
  if (start < lines.length) cols.push(lines.slice(start));
  return cols.map(trimBlank).filter((col) => col.length);
}

function trimBlank(lines) {
  let a = 0, b = lines.length;
  while (a < b && lines[a].trim() === '') a++;
  while (b > a && lines[b - 1].trim() === '') b--;
  return lines.slice(a, b);
}

// Largest font size (px) for the lines in the box, over 1..MAX_COLUMNS columns.
function bestLayout(lines, width, height) {
  let best = null;
  for (let n = 1; n <= MAX_COLUMNS; n++) {
    const cols = splitColumns(lines, n);
    if (n > 1 && cols.length < n) break;
    const chars = cols.reduce((sum, col) => sum + Math.max(1, ...col.map((l) => l.length)), 0)
      + COLUMN_GAP * (cols.length - 1);
    const rows = Math.max(...cols.map((col) => col.length));
    const size = Math.min(width / (chars * charRatio), height / (rows * LINE_HEIGHT));
    if (!best || size > best.size) best = { size, cols };
  }
  return best;
}

function render() {
  sheet.textContent = '';
  if (!song) {
    sheet.innerHTML = '<div class="empty">Choose a song: double-click it in the list, or press <b>n</b>.</div>';
    return;
  }
  if (!song.text.trim()) {
    sheet.innerHTML = '<div class="empty">No chords for this song yet.</div>';
    return;
  }
  if (charRatio === null) charRatio = measureChar();
  const style = getComputedStyle(sheet);
  const width = sheet.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight) - 2;
  const height = sheet.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom) - 2;
  if (width <= 0 || height <= 0) return;
  // the original text, or with long comment lines wrapped (server heuristic): whichever fits bigger
  let best = null;
  for (const text of new Set([song.text, song.wrapped])) {
    const layout = bestLayout(trimBlank(text.split('\n')), width, height);
    if (!best || layout.size > best.size * 1.02) best = layout;
  }
  const size = Math.max(4, Math.floor(best.size * 10) / 10);
  sheet.style.gap = (COLUMN_GAP * charRatio * size) + 'px';
  for (const col of best.cols) {
    const pre = document.createElement('pre');
    pre.style.fontSize = size + 'px';
    pre.textContent = col.join('\n');
    sheet.appendChild(pre);
  }
}

new ResizeObserver(() => render()).observe(sheet);

// --- playlist -----------------------------------------------------------------

function esc(s) {
  return String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function renderList() {
  tbody.innerHTML = playlist.map((r, i) =>
    `<tr data-i="${i}"><td>${esc(r.title)}</td><td>${esc(r.artist)}</td><td>${esc(r.album)}</td>` +
    `<td>${esc(r.capo)}</td><td>${esc(r.chords)}</td><td>${esc(r.tags)}</td>` +
    `<td>${esc(r.played)}</td><td>${esc(r.last)}</td></tr>`).join('');
  $('#count').textContent = `${playlist.length} songs`;
  markCurrent();
}

function markCurrent() {
  tbody.querySelector('tr.current')?.classList.remove('current');
  const row = tbody.children[pos];
  if (row) {
    row.classList.add('current');
    row.scrollIntoView({ block: 'nearest' });
  }
}

tbody.addEventListener('dblclick', (e) => {
  const tr = e.target.closest('tr');
  if (tr) playAt(Number(tr.dataset.i));
});

$('#list thead').addEventListener('click', (e) => {
  const key = e.target.dataset.key;
  if (!key) return;
  sortUp = sortKey === key ? !sortUp : true;
  sortKey = key;
  const current = playlist[pos]?.id;
  const num = key === 'played' || key === 'capo';
  playlist.sort((a, b) => {
    const x = a[key], y = b[key];
    const c = num ? Number(x) - Number(y) : String(x).localeCompare(String(y), undefined, { sensitivity: 'base' });
    return sortUp ? c : -c;
  });
  setPlaylist(playlist, current, true);
});

function setPlaylist(rows, currentId, store) {
  playlist = rows;
  pos = playlist.findIndex((r) => r.id === currentId);
  renderList();
  if (store) api('playlist/order', { method: 'POST', body: { ids: playlist.map((r) => r.id) } })
    .catch((e) => toast('Saving the order failed: ' + e.message));
}

function shuffle() {
  const current = playlist[pos]?.id;
  for (let i = playlist.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [playlist[i], playlist[j]] = [playlist[j], playlist[i]];
  }
  sortKey = null;
  setPlaylist(playlist, current, true);
  toast('Shuffled');
}

// --- playing ------------------------------------------------------------------

async function loadSong(i) {
  const row = playlist[i];
  pos = i;
  markCurrent();
  player.src = 'api/audio/' + row.id;
  song = await api('song/' + row.id);
  showTitle();
  render();
  api('now', { method: 'PUT', body: { id: row.id, pos: i } }).catch(() => {});
  if ('mediaSession' in navigator) {
    navigator.mediaSession.metadata = new MediaMetadata({ title: song.title, artist: song.artist, album: song.album || '' });
  }
}

async function playAt(i) {
  if (!playlist.length) return;
  i = ((i % playlist.length) + playlist.length) % playlist.length;
  try {
    await loadSong(i);
    await player.play();
  } catch (e) {
    if (e.name === 'NotAllowedError') toast('Press Space or ▶ to start playing');
    else toast('Cannot play: ' + e.message);
  }
}

function next() { playAt(pos + 1); }
function prev() { playAt(pos < 0 ? 0 : pos - 1); }

function togglePause() {
  if (pos < 0) { next(); return; }
  if (player.paused) player.play().catch((e) => toast('Cannot play: ' + e.message));
  else player.pause();
}

function seek(seconds) {
  if (!player.src) return;
  player.currentTime = Math.max(0, Math.min((player.duration || 0) - 0.1, player.currentTime + seconds));
}

// like the desktop app: a song repeats until you go on, every end counts as played
player.addEventListener('ended', async () => {
  const id = song?.id;
  player.currentTime = 0;
  player.play().catch(() => {});
  if (id === undefined) return;
  try {
    const r = await api('played/' + id, { method: 'POST' });
    const row = playlist.find((x) => x.id === id);
    if (row) { row.played = r.played; row.last = r.last; renderList(); }
    if (song?.id === id) { song.played = r.played; showTitle(); }
  } catch (e) {
    toast('Recording the play failed: ' + e.message);
  }
});

player.addEventListener('play', () => { $('#b-pause').textContent = '⏸'; keepAwake(); });
player.addEventListener('pause', () => { $('#b-pause').textContent = '▶'; });
player.addEventListener('timeupdate', showTime);
player.addEventListener('error', () => { if (player.src) toast('Audio error: the file cannot be played'); });

function fmt(t) {
  if (!isFinite(t)) return '0:00';
  const s = Math.floor(t);
  return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
}

function showTime() {
  $('#time').textContent = player.src ? `${fmt(player.currentTime)} / ${fmt(player.duration)}` : '';
}

function showTitle() {
  if (!song) { $('#title').textContent = ''; return; }
  const meta = [];
  if (song.capo) meta.push('capo ' + song.capo);
  if (song.tags) meta.push('tags ' + song.tags);
  meta.push(`${song.played}× played`);
  $('#title').innerHTML = `${pos + 1}: ${esc(song.title)} – ${esc(song.artist)}<span class="meta">${esc(meta.join(' · '))}</span>`;
  document.title = `${song.title} – ChordsPlay`;
}

// keep the screen on while a song is shown (needs https or localhost)
async function keepAwake() {
  if (!('wakeLock' in navigator) || wakeLock) return;
  try {
    wakeLock = await navigator.wakeLock.request('screen');
    wakeLock.addEventListener('release', () => { wakeLock = null; });
  } catch (e) { /* not allowed now, e.g. tab hidden */ }
}
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible' && !player.paused) keepAwake();
});

if ('mediaSession' in navigator) {
  navigator.mediaSession.setActionHandler('nexttrack', next);
  navigator.mediaSession.setActionHandler('previoustrack', prev);
  navigator.mediaSession.setActionHandler('play', () => player.play());
  navigator.mediaSession.setActionHandler('pause', () => player.pause());
}

// --- filter dialog ------------------------------------------------------------

const FILTER_KEYS = ['Titles', 'Artists', 'Albums', 'Capos', 'Required Chords', 'Accepted Chords', 'Tag Keys'];
let filterValues = Object.fromEntries(FILTER_KEYS.map((k) => [k, '']));

function buildFilterFields() {
  const box = $('#filter-fields');
  for (const key of FILTER_KEYS) {
    const id = 'f-' + key.replace(/\s/g, '-');
    box.insertAdjacentHTML('beforeend',
      `<label for="${id}">${key}</label><input id="${id}" data-key="${key}" spellcheck="false" autocomplete="off">` +
      `<div class="err" data-err="${key}" hidden></div>`);
  }
  box.addEventListener('input', () => {
    clearTimeout(buildFilterFields.timer);
    buildFilterFields.timer = setTimeout(previewFilter, 250);
  });
}

function filterForm() {
  return Object.fromEntries([...document.querySelectorAll('#filter-fields input')].map((i) => [i.dataset.key, i.value]));
}

function setFilterForm(values) {
  for (const input of document.querySelectorAll('#filter-fields input')) input.value = values[input.dataset.key] || '';
}

function showFilterErrors(errors) {
  for (const input of document.querySelectorAll('#filter-fields input')) {
    const msg = errors[input.dataset.key];
    input.classList.toggle('bad', !!msg);
    const err = document.querySelector(`[data-err="${input.dataset.key}"]`);
    err.hidden = !msg;
    err.textContent = msg || '';
  }
}

async function previewFilter() {
  const q = new URLSearchParams(filterForm());
  try {
    const r = await api('filter?' + q);
    showFilterErrors({});
    $('#filter-count').textContent = `${r.playlist.length} songs match` +
      (r.playlist.length ? '' : ' – Ok keeps the current playlist');
  } catch (e) {
    showFilterErrors(e.data?.errors || {});
    $('#filter-count').textContent = '';
  }
}

async function openFilter() {
  const d = $('#d-filter');
  setFilterForm(filterValues);
  showFilterErrors({});
  const sel = $('#filter-load');
  sel.length = 1;
  try {
    for (const name of (await api('filters')).names) sel.add(new Option(name, name));
  } catch (e) { /* list stays empty */ }
  previewFilter();
  d.showModal();
  d.querySelector('input').focus();
}

$('#filter-load').addEventListener('change', async (e) => {
  if (!e.target.value) return;
  try {
    setFilterForm((await api('filters/' + encodeURIComponent(e.target.value))).filter);
    previewFilter();
  } catch (err) { toast('Loading failed: ' + err.message); }
  e.target.value = '';
});

$('#filter-save').addEventListener('click', async () => {
  const name = prompt('Save filter as');
  if (!name) return;
  try {
    await api('filters/' + encodeURIComponent(name), { method: 'PUT', body: filterForm() });
    toast(`Saved filter "${name}"`);
  } catch (e) { showFilterErrors(e.data?.errors || {}); toast('Saving failed: ' + e.message); }
});

$('#d-filter').addEventListener('close', async () => {
  if ($('#d-filter').returnValue !== 'ok') return;
  const values = filterForm();
  try {
    const r = await api('filter', { method: 'POST', body: values });
    filterValues = values;
    if (r.playlist.length) {
      sortKey = null;
      setPlaylist(r.playlist, song?.id, false);
      toast(`${r.playlist.length} songs`);
    } else {
      toast('No song matches – playlist unchanged');
    }
  } catch (e) {
    toast('Filter failed: ' + Object.values(e.data?.errors || { e: e.message }).join('; '));
  }
});

// --- tags dialog --------------------------------------------------------------

let tagList = [];

async function openTags() {
  if (!song) { toast('Start a song first'); return; }
  $('#tags-title').textContent = `Tags: ${song.title} – ${song.artist}`;
  try {
    tagList = (await api(`song/${song.id}/tags`)).tags;
  } catch (e) { toast('Loading tags failed: ' + e.message); return; }
  renderTags();
  $('#d-tags').showModal();
}

function renderTags() {
  $('#tags-list').innerHTML = tagList.map((t) =>
    `<label><input type="checkbox" data-id="${t.id}" ${t.on ? 'checked' : ''}><kbd>${esc(t.key)}</kbd> ${esc(t.name)}</label>`).join('');
}

async function setTag(id, on) {
  try {
    tagList = (await api(`song/${song.id}/tags`, { method: 'POST', body: { id, on } })).tags;
    renderTags();
    song.tags = tagList.filter((t) => t.on).map((t) => t.key).join('');
    const row = playlist.find((r) => r.id === song.id);
    if (row) { row.tags = song.tags; renderList(); }
    showTitle();
  } catch (e) { toast('Tagging failed: ' + e.message); }
}

$('#tags-list').addEventListener('change', (e) => setTag(Number(e.target.dataset.id), e.target.checked));
// on the document: re-rendering the list drops the focus out of the dialog
document.addEventListener('keydown', (e) => {
  if (!$('#d-tags').open) return;
  const tag = tagList.find((t) => t.key && t.key === e.key);
  if (tag && !e.ctrlKey && !e.altKey && !e.metaKey) {
    e.preventDefault();
    setTag(tag.id, !tag.on);
  }
});

// --- audio output (Chrome: setSinkId) -------------------------------------------

async function outputs() {
  return (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === 'audiooutput');
}

async function openAudio() {
  if (!player.setSinkId) { toast('This browser plays to the system’s default output'); return; }
  const list = await outputs();
  const named = list.some((d) => d.label);
  $('#audio-list').innerHTML = list.map((d) =>
    `<label><input type="radio" name="out" value="${esc(d.deviceId)}" ${d.deviceId === (player.sinkId || 'default') ? 'checked' : ''}>` +
    `${esc(d.label || (d.deviceId === 'default' ? 'Default' : 'Output ' + d.deviceId.slice(0, 6)))}</label>`).join('');
  $('#audio-hint').textContent = named ? '' : 'The browser shows device names only after you allow the microphone once.';
  $('#audio-names').hidden = named;
  $('#d-audio').showModal();
}

$('#audio-list').addEventListener('change', async (e) => {
  try { await player.setSinkId(e.target.value); toast('Audio output changed'); } catch (err) { toast('Switching failed: ' + err.message); }
});

$('#audio-names').addEventListener('click', async () => {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    stream.getTracks().forEach((t) => t.stop());
  } catch (e) { toast('Not allowed'); }
  $('#d-audio').close();
  openAudio();
});

async function nextOutput() {
  if (!player.setSinkId) return;
  const list = await outputs();
  if (list.length < 2) { toast('Only one audio output'); return; }
  const i = list.findIndex((d) => d.deviceId === (player.sinkId || 'default'));
  const out = list[(i + 1) % list.length];
  await player.setSinkId(out.deviceId);
  toast('Audio: ' + (out.label || out.deviceId));
}

// --- keys and buttons -----------------------------------------------------------

function toggleList() {
  document.body.classList.toggle('no-list');
  try { localStorage.setItem('noList', document.body.classList.contains('no-list') ? '1' : ''); } catch (e) { /* ignore */ }
}

function toggleFull() {
  if (document.fullscreenElement) document.exitFullscreen();
  else document.documentElement.requestFullscreen().catch(() => {});
}

const keys = {
  ' ': togglePause, m: togglePause,
  Enter: next, n: next,
  Backspace: prev, p: prev,
  s: shuffle, f: openFilter, t: openTags, l: toggleList,
  a: openAudio, A: nextOutput,
  '0': () => { player.currentTime = 0; },
  '+': () => seek(1), '-': () => seek(-1),
  ArrowRight: () => seek(5), ArrowLeft: () => seek(-5),
  F11: toggleFull, '?': () => $('#d-help').showModal(),
  g: () => toast('Guitar tone comes to the web app with the amp bridge (phase B)'),
  G: () => toast('Guitar tone comes to the web app with the amp bridge (phase B)'),
};

document.addEventListener('keydown', (e) => {
  if (document.querySelector('dialog[open]') || e.ctrlKey || e.altKey || e.metaKey) return;
  if (e.target.matches('input, select, textarea')) return;
  const action = keys[e.key];
  if (action) {
    e.preventDefault();
    action();
  }
});

const buttons = {
  '#b-list': toggleList, '#b-prev': prev, '#b-pause': togglePause, '#b-next': next,
  '#b-shuffle': shuffle, '#b-filter': openFilter, '#b-tags': openTags, '#b-audio': openAudio,
  '#b-full': toggleFull, '#b-help': () => $('#d-help').showModal(),
};
for (const [sel, fn] of Object.entries(buttons)) {
  $(sel).addEventListener('click', (e) => { e.currentTarget.blur(); fn(); });
}

// --- start ----------------------------------------------------------------------

async function start() {
  buildFilterFields();
  try { if (localStorage.getItem('noList')) document.body.classList.add('no-list'); } catch (e) { /* ignore */ }
  let state;
  try {
    state = await api('state');
  } catch (e) {
    sheet.innerHTML = `<div class="empty">Cannot reach the server: ${esc(e.message)}</div>`;
    return;
  }
  $('#version').textContent = `ChordsPlay web ${state.version} · user ${state.user}`;
  filterValues = state.filter;
  playlist = state.playlist;
  renderList();
  const i = state.now ? playlist.findIndex((r) => r.id === state.now.id) : -1;
  if (i >= 0) {
    await loadSong(i);   // ready to go on where you stopped; the browser needs a key press to play
    toast('Press Space to play');
  } else {
    render();
  }
}

start();
