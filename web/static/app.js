// SPDX-FileCopyrightText: 2016-2026 joba1
// SPDX-License-Identifier: AGPL-3.0-or-later
// ChordsPlay web page: the controller. Plays the audio, shows the song text
// as large as the screen allows, and keeps the playlist, filter and tags in
// step with the server. No build step, no libraries.
'use strict';

const $ = (sel) => document.querySelector(sel);
const player = $('#player');
const sheet = $('#sheet');
const tbody = $('#list tbody');

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

// --- the song text (fit.js) -----------------------------------------------------

let pdfPage = 1;            // first PDF page shown
let pdfShown = null;        // {pages, first, count} of the PDF on screen

function render() {
  if (song?.view) {
    const view = song.view;
    PdfView.render(sheet, 'api/pdf/' + view, pdfPage).then((shown) => {
      if (!shown || song?.view !== view) return;
      pdfShown = shown;
      pdfPage = shown.first;
      showTitle();
    }).catch((e) => {
      toast('Cannot show the PDF: ' + e.message);
      ChordsFit.render(sheet, song, '');
    });
    return;
  }
  pdfShown = null;
  ChordsFit.render(sheet, song, 'Choose a song: double-click it in the list, or press <b>n</b>.');
}

let resizeTimer = null;
new ResizeObserver(() => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(render, song?.view ? 150 : 0);   // a PDF page takes longer to draw
}).observe(sheet);

// --- playlist -----------------------------------------------------------------

const esc = ChordsFit.esc;

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
  const source = await songSource(row.id);
  endRun();
  ab = { a: null, b: null };
  player.src = source.url;
  routeAudio(source.manifest);
  song = await api('song/' + row.id);
  loadSections();
  drawSeekMarks();
  pdfPage = 1;
  showTitle();
  render();
  pushNow();
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
  if (sectionPause()) return;
  if (player.paused) player.play().catch((e) => toast('Cannot play: ' + e.message));
  else player.pause();
}

function seek(seconds) {
  if (!player.src) return;
  player.currentTime = Math.max(0, Math.min((player.duration || 0) - 0.1, player.currentTime + seconds));
}

// a song repeats until you go on, every end counts as played
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

// drawn, not as characters: where no font has ⏸ ▶ the colour emoji font draws blue squares
const ICON_PLAY = '<svg class="icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M4 2.5v11L13.5 8z"/></svg>';
const ICON_PAUSE = '<svg class="icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 2.5h3v11h-3zM9.5 2.5h3v11h-3z"/></svg>';
player.addEventListener('play', () => { $('#b-pause').innerHTML = ICON_PAUSE; keepAwake(); pushNow(); });
player.addEventListener('pause', () => { $('#b-pause').innerHTML = ICON_PLAY; pushNow(); });
player.addEventListener('seeked', pushNow);

// tell the server what plays and where: it remembers the song for every device,
// and displays (key d) follow it
// this page among the open controller pages: a paired TV's remote goes to the one that played last
const ctlId = Math.random().toString(36).slice(2, 12);

// the audio output's name for the displays (a browser that can't choose: the system's default)
let sinkLabels = {};
function sinkName() {
  const id = player.sinkId || 'default';
  return sinkLabels[id] || (id === 'default' ? 'Default output' : '');
}
async function learnSinks() {
  try {
    sinkLabels = Object.fromEntries((await outputs()).filter((d) => d.label).map((d) => [d.deviceId, d.label]));
    pushNow();
  } catch (e) { /* no device list */ }
}

function pushNow() {
  if (!song) return;
  api('now', { method: 'PUT', body: { id: song.id, pos, t: player.currentTime || 0, playing: !player.paused,
                                     pdf: song.view || null, page: pdfPage, ctl: ctlId, out: sinkName() } })
    .catch(() => {});
}
learnSinks();
setInterval(() => { if (!player.paused) pushNow(); }, 15000);   // displays correct their clock
player.addEventListener('timeupdate', showTime);

// --- the song's position as a thin bar under the top bar: click or drag to go there ---

const seekBar = $('#seek');
let seekDrag = false;

// the played part: the fill, and the lead marks in a darker colour up to there
function setSeekFill(pct) {
  $('#seek-fill').style.width = pct + '%';
  $('#seek-lead-played').style.clipPath = `inset(0 ${100 - pct}% 0 0)`;
}

function showSeek() {
  const d = player.duration;
  seekBar.hidden = !player.src;
  setSeekFill(isFinite(d) && d > 0 ? 100 * player.currentTime / d : 0);
}

function seekAt(e) {     // the time under the pointer, in seconds
  const r = seekBar.getBoundingClientRect();
  const f = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
  return { t: f * (player.duration || 0), x: f * r.width };
}

function showSeekTip(e) {
  if (!isFinite(player.duration)) return;
  const { t, x } = seekAt(e);
  $('#seek-tip').textContent = fmt(t);
  $('#seek-tip').style.left = Math.min(Math.max(x, 24), seekBar.clientWidth - 24) + 'px';
}

seekBar.addEventListener('pointermove', (e) => {
  showSeekTip(e);
  if (seekDrag) setSeekFill(100 * seekAt(e).t / player.duration);
});
seekBar.addEventListener('pointerdown', (e) => {
  if (!isFinite(player.duration)) return;
  seekDrag = true;
  seekBar.classList.add('drag');
  seekBar.setPointerCapture(e.pointerId);
  showSeekTip(e);
  setSeekFill(100 * seekAt(e).t / player.duration);
});
seekBar.addEventListener('pointerup', (e) => {
  if (!seekDrag) return;
  seekDrag = false;
  seekBar.classList.remove('drag');
  player.currentTime = seekAt(e).t;       // one jump when released: the amp follows once
});
seekBar.addEventListener('pointercancel', () => { seekDrag = false; seekBar.classList.remove('drag'); showSeek(); });
['timeupdate', 'durationchange', 'loadedmetadata', 'emptied', 'seeked'].forEach((ev) =>
  player.addEventListener(ev, () => { if (!seekDrag) showSeek(); }));
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
  if (song.view && pdfShown) {
    const last = pdfShown.first + pdfShown.count - 1;
    meta.push(`PDF page ${pdfShown.first}${last > pdfShown.first ? '–' + last : ''} of ${pdfShown.pages}`);
  } else if (song.pdfs?.length) {
    meta.push(`${song.pdfs.length} PDF – v`);
  }
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
  await fillSavedFilters();
  previewFilter();
  d.showModal();
  d.querySelector('input').focus();
}

// the saved filters; the first entry is only the label of the closed list (hidden)
async function fillSavedFilters() {
  const sel = $('#filter-load');
  sel.length = 1;
  try {
    for (const name of (await api('filters')).names) sel.add(new Option(name, name));
  } catch (e) { /* list stays empty */ }
  sel.value = '';
  sel.disabled = sel.length === 1;
  sel.options[0].textContent = sel.disabled ? 'No saved filters' : 'Load…';
  sel.title = sel.disabled ? 'Filters stored with "Save as…" appear here' : 'Load a saved filter';
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
    fillSavedFilters();
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

// the element's output, and the practice mix's (Web Audio) once that exists
async function setOutput(id) {
  await player.setSinkId(id);
  if (graph?.ctx.setSinkId) await graph.ctx.setSinkId(id === 'default' ? '' : id);
}

$('#audio-list').addEventListener('change', async (e) => {
  try { await setOutput(e.target.value); toast('Audio output changed'); learnSinks(); } catch (err) { toast('Switching failed: ' + err.message); }
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
  await setOutput(out.deviceId);
  learnSinks();
  toast('Audio: ' + (out.label || out.deviceId));
}

// --- practice mix: the song split into stems, each at its own level (docs/spec-practice.md) ---

const STEM_NAMES = { vocals: 'Vocals', drums: 'Drums', bass: 'Bass', piano: 'Keys', other: 'Other', guitar: 'Guitar',
                     'guitar-left': 'left', 'guitar-center': 'centre', 'guitar-right': 'right' };
const MIX_MAX = 1.5;
let practiceOffered = false;
let practiceStatus = null;  // the current song's split: {state, error, manifest, ...}
let practicePoll = null;
// on: play the instruments (the split file) instead of the original file
let mix = { on: false, levels: {}, muted: {}, solo: {} };
try { Object.assign(mix, JSON.parse(localStorage.getItem('practiceMix') || '{}')); mix.solo ||= {}; } catch (e) { /* defaults */ }
// the Web Audio graph, made the first time a split song plays: an element can't leave it again
let graph = null;           // {ctx, src, nodes, gains: {stem: {gain, group}}}

function saveMix() {
  try { localStorage.setItem('practiceMix', JSON.stringify(mix)); } catch (e) { /* not kept */ }
}

// where the song's sound comes from: the split file if the instruments are on and the song
// is split, else the original file untouched (the instruments add up to it only nearly);
// the split's manifest serves either way: the lead timeline and the beats
async function songSource(id) {
  practiceStatus = null;
  if (practiceOffered) {
    try { practiceStatus = await api(`song/${id}/stems`); } catch (e) { /* the original then */ }
    if (mix.on && practiceStatus?.state === 'ready') {
      return { url: `api/stems/${id}.webm?v=${Math.round(practiceStatus.finished || 0)}`, manifest: practiceStatus.manifest };
    }
  }
  return { url: 'api/audio/' + id, manifest: null };
}

function routeAudio(manifest, force) {
  $('#b-practice').classList.toggle('on', !!manifest);
  if (!manifest && !graph && !force) return;          // the original, straight from the element
  if (!graph) {
    const ctx = new AudioContext();
    // master: the song's way out, for short fades (no crack at a stop, a loop's seam)
    const master = ctx.createGain();
    master.connect(ctx.destination);
    // keep-alive: a constant 0.001 (−60 dBFS DC, inaudible). Chromium pauses ("corks") an
    // output that has been silent for a moment; during a count-in the song is paused, and
    // each click then had to wake the stream and the Bluetooth link: swallowed, cut or full.
    const keep = ctx.createConstantSource();
    keep.offset.value = 0.001;
    keep.connect(ctx.destination);
    keep.start();
    graph = { ctx, src: ctx.createMediaElementSource(player), master, keep, nodes: [], gains: {} };
    if (ctx.setSinkId && player.sinkId) ctx.setSinkId(player.sinkId === 'default' ? '' : player.sinkId).catch(() => {});
  }
  const { ctx, src } = graph;
  src.disconnect();
  graph.nodes.forEach((n) => n.disconnect());
  graph.nodes = [];
  graph.gains = {};
  if (!manifest) { src.connect(graph.master); return; }
  const split = ctx.createChannelSplitter(manifest.channels);
  src.connect(split);
  graph.nodes.push(split);
  for (const stem of manifest.stems) {     // (1.8.2's files also carry the original: not played)
    const pair = ctx.createChannelMerger(2);
    split.connect(pair, 2 * stem.pair, 0);
    split.connect(pair, 2 * stem.pair + 1, 1);
    const gain = ctx.createGain();
    pair.connect(gain).connect(graph.master);
    graph.nodes.push(pair, gain);
    graph.gains[stem.name] = { gain, group: stem.group };
  }
  applyMix(true);
}
// With a splitter, the page's sound goes through Web Audio from the first play on: one output
// stream for the song, the instruments and the count-in clicks. A second stream for the clicks
// would start on the system's default output, and an amp bridge moves new streams only every
// few seconds: the clicks would be over by then.
// the keep-alive rests after a long pause (it keeps the amp's Bluetooth link streaming)
let keepRest = null;
function keepAlive(on) {
  clearTimeout(keepRest);
  if (!graph) return;
  if (on) graph.keep.offset.value = 0.001;
  else keepRest = setTimeout(() => { if (player.paused && !counting && graph) graph.keep.offset.value = 0; }, KEEP_ALIVE_S * 1000);
}
player.addEventListener('pause', () => keepAlive(false));

player.addEventListener('play', () => {
  if (practiceOffered && !graph) routeAudio(null, true);
  keepAlive(true);
  fadeTo(1);
  if (graph?.ctx.state === 'suspended') graph.ctx.resume();
});

function stemLevel(name) {
  return mix.muted[name] ? 0 : (mix.levels[name] ?? 1);
}

// with any instrument soloed, only the soloed ones play (a part counts with its instrument)
function soloed(name, group) {
  const names = graph ? Object.entries(graph.gains).flatMap(([n, g]) => [n, g.group]) : mixRows().map((r) => r.name);
  if (!names.some((n) => n && mix.solo[n])) return true;
  return !!(mix.solo[name] || (group && mix.solo[group]));
}

function applyMix(now) {
  if (!graph) return;
  for (const [name, { gain, group }] of Object.entries(graph.gains)) {
    const v = soloed(name, group) ? stemLevel(name) * (group ? stemLevel(group) : 1) : 0;
    if (now) gain.gain.value = v;
    else gain.gain.setTargetAtTime(v, graph.ctx.currentTime, 0.02);
  }
}

// a change in the mixer while the original plays: over to the instruments
async function mixTouched() {
  saveMix();
  if (!mix.on) {
    mix.on = true;
    saveMix();
    if (practiceStatus?.state === 'ready') { await reloadSource(); toast('Playing the instruments'); }
    showPractice();
  }
  applyMix();
}

// go over to the original or the split file where the song is, playing on if it played
async function reloadSource() {
  if (!song) return;
  const source = await songSource(song.id);
  const url = new URL(source.url, location.href).href;
  if (player.src === url) { routeAudio(source.manifest); return; }
  const t = player.currentTime, playing = !player.paused;
  player.src = source.url;
  routeAudio(source.manifest);
  player.addEventListener('loadedmetadata', () => {
    player.currentTime = t;
    if (playing) player.play().catch(() => {});
  }, { once: true });
}

// the mixer's rows: an instrument, or a part of one (stems with a group)
function mixRows() {
  const stems = practiceStatus?.manifest?.stems ||
    ['vocals', 'drums', 'bass', 'piano', 'other', 'guitar'].map((name) => ({ name }));
  const rows = [];
  for (const s of stems) {
    if (s.group && !rows.some((r) => r.name === s.group)) rows.push({ name: s.group });
    rows.push({ name: s.name, part: !!s.group });
  }
  // the guitar first: it's what you practise
  const g = rows.filter((r) => r.name === 'guitar' || r.name.startsWith('guitar-'));
  return g.concat(rows.filter((r) => !g.includes(r)));
}

function renderMix() {
  $('#pr-mix').innerHTML = mixRows().map(({ name, part }) => {
    const pct = Math.round(100 * (mix.levels[name] ?? 1));
    const label = STEM_NAMES[name] || name;
    return `<span class="${part ? 'part' : 'name'}">${esc(label)}</span>` +
      `<input type="range" min="0" max="${100 * MIX_MAX}" step="5" value="${pct}" data-stem="${esc(name)}" aria-label="${esc(label)}">` +
      `<output>${pct} %</output>` +
      `<button type="button" class="mute" data-mute="${esc(name)}" aria-pressed="${!!mix.muted[name]}" title="Mute ${esc(label)}">M</button>` +
      `<button type="button" class="solo" data-solo="${esc(name)}" aria-pressed="${!!mix.solo[name]}" title="Solo: only the soloed instruments play">S</button>`;
  }).join('');
}

function showPractice() {
  const st = practiceStatus;
  const playing = $('#b-practice').classList.contains('on');
  let text;
  if (!song) text = 'Choose a song first.';
  else if (!st) text = 'Checking…';
  else if (st.state === 'none') text = 'This song isn\'t split yet.';
  else if (st.state === 'queued') text = 'Waiting for the splitter (it does one song at a time)…';
  else if (st.state === 'running') text = 'Splitting the song, about a minute…';
  else if (st.state === 'failed') text = 'Splitting failed: ' + (st.error || 'unknown error');
  else if (playing) text = 'Split: playing the instruments, at the levels below.';
  else text = 'Split: playing the original file. Switch the instruments on (or move a slider) to mix.';
  $('#pr-on').checked = mix.on;
  $('#pr-title').textContent = song ? `Practice mix: ${song.title}` : 'Practice mix';
  $('#pr-status').textContent = text;
  $('#pr-prepare').hidden = !song || !st || !['none', 'failed'].includes(st.state);
  $('#pr-prepare').textContent = st?.state === 'failed' ? 'Try again' : 'Prepare this song';
}

async function refreshPractice() {
  clearTimeout(practicePoll);
  if (!song) { showPractice(); return; }
  const id = song.id;
  try { practiceStatus = await api(`song/${id}/stems`); } catch (e) { practiceStatus = null; }
  if (song?.id !== id) return;
  showPractice();
  renderMix();
  renderSections();
  if (practiceStatus?.state === 'ready') { drawSeekMarks(); loadPeaks(); }
  showWaveState();
  if (['queued', 'running'].includes(practiceStatus?.state) && $('#d-practice').open) {
    practicePoll = setTimeout(async () => {
      await refreshPractice();
      if (practiceStatus?.state === 'ready' && mix.on) { await reloadSource(); showPractice(); }
    }, 2000);
  }
}

function openPractice() {
  if (!practiceOffered) { toast('No practice mix on this server'); return; }
  renderMix();
  showPractice();
  let tab = 'mix';
  try { tab = localStorage.getItem('practiceTab') || 'mix'; } catch (e) { /* the first */ }
  $('#d-practice').showModal();
  $(`#d-practice [data-prtab="${tab === 'waves' ? 'waves' : 'mix'}"]`).click();
  refreshPractice();
}

$('#d-practice').addEventListener('close', () => clearTimeout(practicePoll));

$('#pr-on').addEventListener('change', async (e) => {
  mix.on = e.target.checked;
  saveMix();
  await reloadSource();
  showPractice();
});

$('#pr-prepare-all').addEventListener('click', async () => {
  let n = 0;
  for (const row of playlist) {
    try {
      const st = await api(`song/${row.id}/stems`);
      if (['none', 'failed'].includes(st.state)) { await api(`song/${row.id}/stems`, { method: 'POST' }); n++; }
    } catch (e) { toast('Cannot split: ' + e.message); return; }
  }
  toast(n ? `${n} songs queued: about a minute each` : 'Every song of the playlist is split or queued');
  refreshPractice();
});

$('#pr-prepare').addEventListener('click', async () => {
  try { practiceStatus = await api(`song/${song.id}/stems`, { method: 'POST' }); } catch (e) { toast('Cannot split: ' + e.message); }
  if (!mix.on) { mix.on = true; saveMix(); }    // split to practise: the instruments when it's done
  refreshPractice();
});

$('#pr-again').addEventListener('click', async () => {
  try { practiceStatus = await api(`song/${song.id}/stems`, { method: 'POST', body: { again: true } }); } catch (e) { toast('Cannot split: ' + e.message); }
  peaks = null;
  refreshPractice();
});

$('#pr-mix').addEventListener('input', (e) => {
  const name = e.target.dataset.stem;
  if (!name) return;
  mix.levels[name] = Number(e.target.value) / 100;
  e.target.nextElementSibling.textContent = e.target.value + ' %';
  mixTouched();
});

$('#pr-mix').addEventListener('click', (e) => {
  const { mute, solo } = e.target.dataset;
  const set = mute ? mix.muted : solo ? mix.solo : null;
  const name = mute || solo;
  if (!set) return;
  set[name] = !set[name];
  e.target.setAttribute('aria-pressed', set[name]);
  mixTouched();
});

$('#pr-reset').addEventListener('click', () => {
  mix = { on: mix.on, levels: {}, muted: {}, solo: {} };
  saveMix();
  renderMix();
  applyMix();
});

// --- waveforms of the song and its instruments, to find and mark a section ---------------

const LANE_H = 34, RULER_H = 18, MIN_SPAN_S = 2;
const LANE_NAMES = { song: 'Song', ...STEM_NAMES };
let peaks = null;           // {id, rate, tracks: {name: Uint8Array}, max: {name}}
let lanes = ['song', 'guitar', 'drums'];
try { lanes = JSON.parse(localStorage.getItem('practiceLanes') || 'null') || lanes; } catch (e) { /* defaults */ }
let prView = { start: 0, span: 0 };   // seconds shown
let prViewHeld = 0;                   // the user scrolled or zoomed: don't follow the playhead until then
let prDrag = null;
let waveFrame = null;

function prTab() { return $('#d-practice [data-prtab][aria-selected=true]').dataset.prtab; }

$('#d-practice .tabs').addEventListener('click', (e) => {
  const tab = e.target.dataset.prtab;
  if (!tab) return;
  for (const b of $('#d-practice .tabs').children) b.setAttribute('aria-selected', b.dataset.prtab === tab);
  $('#pr-tab-mix').hidden = tab !== 'mix';
  $('#pr-tab-waves').hidden = tab !== 'waves';
  try { localStorage.setItem('practiceTab', tab); } catch (err) { /* not kept */ }
  showWaveState();
});

async function loadPeaks() {
  if (!song || !practiceStatus?.peaks) { if (peaks?.id !== song?.id) peaks = null; showWaveState(); return; }
  if (peaks?.id === song.id) return;
  const id = song.id;
  try {
    const r = await fetch(`api/stems/${id}.peaks.json?v=${Math.round(practiceStatus.finished || 0)}`);
    if (!r.ok) throw new Error(r.statusText);
    const d = await r.json();
    const tracks = {}, max = {};
    for (const [name, b64] of Object.entries(d.tracks)) {
      const bin = atob(b64);
      const a = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) a[i] = bin.charCodeAt(i);
      tracks[name] = a;
      max[name] = Math.max(1, ...a);
    }
    if (song?.id === id) { peaks = { id, rate: d.rate, tracks, max }; prView = { start: 0, span: 0 }; }
  } catch (e) { peaks = null; }
  showWaveState();
}

function showWaveState() {
  const waves = $('#d-practice').open && prTab() === 'waves';
  const have = !!peaks && peaks.id === song?.id;
  $('#pr-wave').hidden = !have;
  $('#pr-wave-hint').hidden = !have;
  $('#pr-wave-none').hidden = have || practiceStatus?.state !== 'ready';
  $('#pr-lanes').innerHTML = ['song', ...mixRows().map((r) => r.name)]
    .filter((n) => !have || peaks.tracks[n])
    .map((n) => `<label><input type="checkbox" data-lane="${esc(n)}" ${lanes.includes(n) ? 'checked' : ''}>${esc(LANE_NAMES[n] || n)}</label>`).join('');
  cancelAnimationFrame(waveFrame);
  if (waves && have) waveFrame = requestAnimationFrame(waveLoop);
}

$('#pr-lanes').addEventListener('change', (e) => {
  const n = e.target.dataset.lane;
  if (!n) return;
  lanes = e.target.checked ? [...new Set([...lanes, n])] : lanes.filter((x) => x !== n);
  try { localStorage.setItem('practiceLanes', JSON.stringify(lanes)); } catch (err) { /* not kept */ }
});

function songLength() {
  if (isFinite(player.duration) && player.duration > 0) return player.duration;
  return peaks ? Math.max(...Object.values(peaks.tracks).map((a) => a.length)) / peaks.rate : 0;
}

function clampView() {
  const d = songLength();
  if (!isFinite(prView.start)) prView.start = 0;      // never stuck on an impossible view
  if (!isFinite(prView.span) || !prView.span || prView.span > d) prView.span = d;
  prView.span = Math.max(Math.min(MIN_SPAN_S, d), prView.span);
  prView.start = Math.min(Math.max(0, prView.start), Math.max(0, d - prView.span));
}

function waveLoop() {
  if (!$('#d-practice').open || prTab() !== 'waves' || !peaks) return;
  const t = player.currentTime;
  if (!player.paused && Date.now() > prViewHeld && prView.span && (t < prView.start || t > prView.start + prView.span)) {
    prView.start = t - prView.span * 0.1;   // page along with the song
  }
  try {
    drawWaves();
  } catch (e) {
    console.error('waves:', e);                         // draw on: the next frame may do
    prView = { start: 0, span: 0 };
  }
  waveFrame = requestAnimationFrame(waveLoop);
}

function niceStep(span, width) {
  for (const s of [0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120]) if (width * s / span >= 70) return s;
  return 300;
}

function drawWaves() {
  const c = $('#pr-wave');
  const shown = ['song', ...mixRows().map((r) => r.name)].filter((n) => lanes.includes(n) && peaks.tracks[n]);
  const w = c.clientWidth, h = RULER_H + Math.max(1, shown.length) * LANE_H;
  const dpr = window.devicePixelRatio || 1;
  if (c.width !== Math.round(w * dpr) || c.height !== Math.round(h * dpr)) {
    c.style.height = h + 'px';
    c.width = Math.round(w * dpr);
    c.height = Math.round(h * dpr);
  }
  const g = c.getContext('2d');
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, h);
  clampView();
  const { start, span } = prView;
  if (!span) return;
  const X = (t) => (t - start) / span * w;
  const css = getComputedStyle(document.documentElement);
  const col = (v, f) => css.getPropertyValue(v).trim() || f;
  const fg = col('--fg', '#222'), muted = col('--muted', '#888'), line = col('--line', '#ccc');
  const accent = col('--accent', '#2f6fdf'), lead = col('--lead', 'orange');
  // the marked section
  if (ab.a !== null && ab.b !== null) {
    g.fillStyle = accent; g.globalAlpha = 0.18;
    g.fillRect(X(ab.a), 0, X(ab.b) - X(ab.a), h);
    g.globalAlpha = 1;
  } else if (ab.a !== null) {
    g.fillStyle = accent; g.fillRect(X(ab.a) - 1, 0, 2, h);
  }
  // ruler: time and the beats
  g.font = '11px sans-serif'; g.textBaseline = 'top';
  const beats = manifest()?.beats || [];
  if (beats.length > 1 && w * (beats[1] - beats[0]) / span >= 5) {
    g.fillStyle = line;
    for (const b of beats) if (b >= start && b <= start + span) g.fillRect(X(b), RULER_H - 5, 1, 5);
  }
  // the beats as faint lines through the lanes, when they are far enough apart to tell
  if (beats.length > 1 && w * (beats[1] - beats[0]) / span >= 12) {
    g.fillStyle = line; g.globalAlpha = 0.9;
    for (const b of beats) if (b >= start && b <= start + span) g.fillRect(Math.round(X(b)), RULER_H, 1, h - RULER_H);
    g.globalAlpha = 1;
  }
  const step = niceStep(span, w);
  g.fillStyle = muted;
  for (let t = Math.ceil(start / step) * step; t <= start + span; t += step) {
    g.fillRect(X(t), 0, 1, 4);
    g.fillText(step < 1 ? fmtPrecise(t) : fmt(t), X(t) + 3, 2);
  }
  // the lanes
  const tl = manifest()?.timeline;
  shown.forEach((name, k) => {
    const y0 = RULER_H + k * LANE_H, mid = y0 + LANE_H / 2;
    g.fillStyle = line; g.fillRect(0, y0, w, 1);
    if (name === 'guitar' && tl?.lead) {
      g.fillStyle = lead; g.globalAlpha = 0.35;
      tl.lead.forEach((on, i) => { if (on) g.fillRect(X(i * tl.step), y0 + 1, w * tl.step / span + 0.5, LANE_H - 1); });
      g.globalAlpha = 1;
    }
    const a = peaks.tracks[name], top = peaks.max[name], rate = peaks.rate;
    g.fillStyle = name === 'song' ? fg : accent;
    for (let x = 0; x < w; x++) {
      const i0 = Math.floor((start + x / w * span) * rate);
      const i1 = Math.max(i0 + 1, Math.floor((start + (x + 1) / w * span) * rate));
      let v = 0;
      for (let i = i0; i < i1 && i < a.length; i++) if (a[i] > v) v = a[i];
      const half = (v / top) * (LANE_H / 2 - 3);
      if (half > 0.3) g.fillRect(x, mid - half, 1, 2 * half);
    }
    const label = LANE_NAMES[name] || name;
    g.fillStyle = col('--bg', '#fff'); g.globalAlpha = 0.8;
    g.fillRect(2, y0 + 2, g.measureText(label).width + 6, 14);
    g.globalAlpha = 1; g.fillStyle = fg;
    g.fillText(label, 5, y0 + 3);
  });
  // where the song is
  g.fillStyle = '#d63a3a';
  g.fillRect(X(player.currentTime) - 0.5, 0, 1.5, h);
}

function waveTime(e) {
  const r = $('#pr-wave').getBoundingClientRect();
  return Math.min(songLength(), Math.max(0, prView.start + (e.clientX - r.left) / r.width * prView.span));
}

$('#pr-wave').addEventListener('pointerdown', (e) => {
  prDrag = { x: e.clientX, t: waveTime(e), moved: false };
  $('#pr-wave').setPointerCapture(e.pointerId);
});
$('#pr-wave').addEventListener('pointermove', (e) => {
  if (!prDrag) return;
  if (Math.abs(e.clientX - prDrag.x) > 4) prDrag.moved = true;
  if (!prDrag.moved) return;
  const t = waveTime(e);
  ab = { a: snap(Math.min(t, prDrag.t)), b: snap(Math.max(t, prDrag.t)) };
  prViewHeld = Date.now() + 4000;
});
$('#pr-wave').addEventListener('pointerup', (e) => {
  if (!prDrag) return;
  if (!prDrag.moved) player.currentTime = prDrag.t;
  else if (!(ab.b > ab.a)) ab = { a: null, b: null };
  prDrag = null;
  showAb();
});
$('#pr-wave').addEventListener('wheel', (e) => {
  e.preventDefault();
  prViewHeld = Date.now() + 4000;
  clampView();
  if (e.shiftKey || Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
    // Shift+wheel arrives as deltaX in Chromium (deltaY 0), in others as deltaY
    const d = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY;
    prView.start += d / $('#pr-wave').clientWidth * prView.span;
  } else {
    const t = waveTime(e), f = e.deltaY > 0 ? 1.25 : 0.8;
    const span = prView.span * f;
    prView.start = t - (t - prView.start) * f;
    prView.span = span;
  }
  clampView();
}, { passive: false });

$('#pr-tab-waves').addEventListener('click', (e) => {
  const z = e.target.dataset.zoom;
  if (!z) return;
  clampView();
  const mid = !player.paused || (player.currentTime >= prView.start && player.currentTime <= prView.start + prView.span)
    ? player.currentTime : prView.start + prView.span / 2;
  if (z === 'fit') prView = { start: 0, span: songLength() };
  else {
    const f = z === 'in' ? 0.5 : 2;
    prView.span *= f;
    prView.start = mid - prView.span / 2;
  }
  prViewHeld = Date.now() + 2000;
  clampView();
});

// --- sections: a part of the song once or looped, after a count-in (docs/spec-practice.md) ---

const COUNT_DEFAULT_BPM = 100;          // without beats from the split
const SNAP_S = 0.25;                    // a mark this close to a beat goes onto it
let sections = [];                      // the song's named sections: {id, name, start, end}
let ab = { a: null, b: null };          // the unnamed section (keys b, e)
let run = null;                         // the playing section: {a, b, loop}; null: plain playing
let counting = null;                    // the count-in under way: {timer}
let runWatch = null;
let count = { beats: 4, each: false, factor: 1, mode: 'clicks', level: 0.5 };   // mode: clicks or lead (the song itself)
try { Object.assign(count, JSON.parse(localStorage.getItem('practiceCount') || '{}')); } catch (e) { /* defaults */ }

function saveCount() {
  try { localStorage.setItem('practiceCount', JSON.stringify(count)); } catch (e) { /* not kept */ }
}

function manifest() { return practiceStatus?.state === 'ready' ? practiceStatus.manifest : null; }

function fmtPrecise(t) { return fmt(t) + '.' + Math.floor((t % 1) * 10); }

function snap(t) {
  const beats = manifest()?.beats;
  if (!beats?.length) return t;
  let best = beats[0];
  for (const b of beats) if (Math.abs(b - t) < Math.abs(best - t)) best = b;
  return Math.abs(best - t) <= SNAP_S ? best : t;
}

// seconds per count-in beat around t: from the song's beats, else its tempo, else a default
function beatAt(t) {
  const m = manifest();
  let iv = 60 / (m?.tempo || COUNT_DEFAULT_BPM);
  const beats = m?.beats || [];
  const k = beats.findIndex((b) => b >= t);
  if (k > 0) {
    const near = beats.slice(Math.max(0, k - 3), k + 3);
    const gaps = near.slice(1).map((b, i) => b - near[i]).sort((x, y) => x - y);
    if (gaps.length) iv = gaps[Math.floor(gaps.length / 2)];
  }
  return iv / count.factor;
}

function tempoText() {
  const m = manifest();
  const bpm = (m?.tempo || COUNT_DEFAULT_BPM) * count.factor;
  return `Tempo ${Math.round(bpm)} bpm` + (m?.tempo ? '' : ' (not known: split the song)');
}

function clicks() {
  if (!graph) routeAudio(null, true);       // (normally made by the first play)
  return graph.ctx;
}

const FADE_S = 0.025;        // a fade this long: no crack, no audible gap
const SEAM_S = 0.06;         // the loop's seam: fade out and in this long (25 ms was still a little hard)
const CLICK_PEAK = 0.15;     // the clicks' level at 100 % (at the Mustang 0.45 was three times too loud)
const KEEP_ALIVE_S = 300;    // paused this long: let the output rest (the amp's battery)

// the song's level through the master gain (no graph: nothing to fade)
function fadeTo(v, s = FADE_S) {
  if (!graph) return;
  const g = graph.master.gain, now = graph.ctx.currentTime;
  g.cancelScheduledValues(now);
  g.setValueAtTime(g.value, now);
  g.linearRampToValueAtTime(v, now + s);
}

// fade out, then pause (a hard stop in the middle of the music cracks)
function pauseSoftly() {
  if (!graph || player.paused) { player.pause(); return Promise.resolve(); }
  fadeTo(0);
  return new Promise((done) => setTimeout(() => { player.pause(); done(); }, FADE_S * 1000 + 10));
}

// a count-in click like a woodblock: two square tones together, 0.2 s, so the ear hears it
// as loud as the music (a click of a few ms sounds far quieter than its level); the level
// is a setting (count.level, 0..1)
function click(ctx, when, first) {
  const env = ctx.createGain();
  const peak = Math.max(0.0002, CLICK_PEAK * (count.level ?? 0.5));
  env.gain.setValueAtTime(0.0001, when);
  env.gain.exponentialRampToValueAtTime(peak, when + 0.003);
  env.gain.setValueAtTime(peak, when + 0.04);
  env.gain.exponentialRampToValueAtTime(0.0001, when + 0.2);
  env.connect(ctx.destination);
  for (const f of first ? [1200, 1800] : [800, 1200]) {
    const osc = ctx.createOscillator();
    osc.type = 'square';
    osc.frequency.value = f;
    osc.connect(env);
    osc.start(when);
    osc.stop(when + 0.22);
  }
}

function stopCount() {
  if (counting) { clearTimeout(counting.timer); counting = null; }
}

// pause at t, count in, then play
// the way in to a section at t: clicks in the song's tempo while it waits, or the song
// itself from n beats before t (no beat detection to trust: right in any metre)
async function countInAt(t) {
  stopCount();
  await pauseSoftly();
  const n = Number(count.beats) || 0;
  if (n && count.mode === 'lead') {
    const from = Math.max(0, t - n * beatAt(t));
    if (run) run.from = from;
    player.currentTime = from;
    player.play().catch(() => {});
    return;
  }
  player.currentTime = t;
  if (!n) { player.play().catch(() => {}); return; }
  const ctx = clicks();
  if (ctx.state === 'suspended') await ctx.resume();
  keepAlive(true);
  const iv = beatAt(t);
  const t0 = ctx.currentTime + 0.3;
  for (let i = 0; i < n; i++) click(ctx, t0 + i * iv, i === 0);   // no bar stress: the "1" isn't known
  const startIn = t0 + n * iv - ctx.currentTime - 0.02;     // play() takes a moment to sound
  counting = { timer: setTimeout(() => { counting = null; player.play().catch(() => {}); }, startIn * 1000) };
  toast(`Count-in: ${n} beats`, Math.min(4000, startIn * 1000));
}

// the whole song as a section: ends just before the song does, so the loop (and its
// count-in) comes before the song's own repeat at its end
function wholeSong() { return [0, Math.max(0.1, (player.duration || 0) - 0.05)]; }

function startRun(a, b, loop, mark = true) {
  if (!song || !(b > a)) return;
  run = { a, b, loop };
  if (mark) ab = { a, b };
  showAb();
  countInAt(a);
  clearInterval(runWatch);
  runWatch = setInterval(() => {
    if (!run || player.paused || counting || run.seam) return;
    const t = player.currentTime;
    if (t < (run.from ?? run.a) - 0.5) { run.from = null; player.currentTime = run.a; return; }   // moved away
    if (t >= run.b - SEAM_S - 0.02) {          // the seam: fade out to the end, jump, fade in
      run.seam = true;
      fadeTo(0, SEAM_S);
      setTimeout(() => {
        run && (run.seam = false);
        if (!run) return;
        if (!run.loop) { player.pause(); player.currentTime = run.b; endRun(); return; }
        if (count.each) { countInAt(run.a); return; }
        run.from = null;
        player.currentTime = run.a;
        fadeTo(1, SEAM_S);
      }, SEAM_S * 1000 + 10);
    }
  }, 20);
}

function endRun() {
  run = null;
  stopCount();
  clearInterval(runWatch);
  showAb();
}

function markA() {
  if (!song) return;
  ab.a = snap(player.currentTime);
  if (ab.b !== null && ab.b <= ab.a) ab.b = null;
  showAb();
  toast(`Section from ${fmtPrecise(ab.a)}` + (ab.b ? ` to ${fmtPrecise(ab.b)}` : ': e marks the end'));
}

function markB() {
  if (!song) return;
  const t = snap(player.currentTime);
  if (ab.a === null || t <= ab.a) { toast('Mark the beginning first (b)'); return; }
  ab.b = t;
  showAb();
  toast(`Section ${fmtPrecise(ab.a)} – ${fmtPrecise(ab.b)}: o loops it`);
}

function toggleLoop() {
  if (run) { endRun(); toast('Loop off'); return; }
  if (ab.a === null || ab.b === null) {           // nothing marked: the whole song
    if (!isFinite(player.duration)) return;
    startRun(...wholeSong(), true, false);
    toast('Looping the whole song: b and e mark a part');
    return;
  }
  startRun(ab.a, ab.b, true);
}

// play/pause during a section: a start always comes after a count-in at the section's beginning
function sectionPause() {
  if (counting) { stopCount(); toast('Count-in stopped'); return true; }
  if (!run) return false;
  if (player.paused) countInAt(run.a);
  else player.pause();
  return true;
}

function showAb() {
  const box = $('#seek-ab'), d = player.duration;
  const on = ab.a !== null && ab.b !== null && isFinite(d) && d > 0;
  box.hidden = !on;
  if (on) { box.style.left = (100 * ab.a / d) + '%'; box.style.width = (100 * (ab.b - ab.a) / d) + '%'; }
  if ($('#d-practice').open) renderSections();
}

// the lead timeline as colour in the seek bar
function drawSeekMarks() {
  const tl = manifest()?.timeline, d = player.duration;
  const lead = $('#seek-lead'), played = $('#seek-lead-played');
  seekBar.classList.toggle('rich', !!(tl?.lead || (ab.a !== null && ab.b !== null)));
  if (!tl?.lead || !isFinite(d) || d <= 0) { lead.style.background = played.style.background = 'none'; showAb(); return; }
  const stops = [];
  let i = 0;
  while (i < tl.lead.length) {
    if (!tl.lead[i]) { i++; continue; }
    let j = i;
    while (j < tl.lead.length && tl.lead[j]) j++;
    const x0 = (100 * i * tl.step / d).toFixed(2), x1 = (100 * Math.min(d, j * tl.step) / d).toFixed(2);
    stops.push(`transparent ${x0}%, COLOR ${x0}%, COLOR ${x1}%, transparent ${x1}%`);
    i = j;
  }
  const grad = (c) => stops.length ? `linear-gradient(to right, ${stops.join(', ').replaceAll('COLOR', c)})` : 'none';
  lead.style.background = grad('var(--lead)');
  played.style.background = grad('var(--lead-played)');
  showAb();
}
['durationchange', 'loadedmetadata'].forEach((ev) => player.addEventListener(ev, drawSeekMarks));

async function loadSections() {
  sections = [];
  if (!song || !practiceOffered) return;
  const id = song.id;
  try {
    const r = await api(`song/${id}/sections`);
    if (song?.id === id) sections = r.sections;
  } catch (e) { /* none */ }
}

function sectionRow(label, a, b, extra) {
  return `<span>${label}</span><span class="times">${fmtPrecise(a)} – ${fmtPrecise(b)}</span>` +
    `<button type="button" data-run="once" data-a="${a}" data-b="${b}" title="Play it once, after the count-in">▶</button>` +
    `<button type="button" data-run="loop" data-a="${a}" data-b="${b}" title="Loop it, after the count-in">⟳</button>` + extra;
}

function renderSections() {
  const abSet = ab.a !== null && ab.b !== null;
  $('#pr-ab').innerHTML = abSet
    ? sectionRow('<i>Marked</i>', ab.a, ab.b, '<button type="button" id="pr-save" title="Keep it for this song">Save as…</button>')
    : ab.a !== null
      ? `<span class="hint" style="grid-column: 1 / -1">From ${fmtPrecise(ab.a)}: press e at the end.</span>`
      : isFinite(player.duration)
        ? sectionRow('<i>Whole song</i> <span class="hint">(nothing marked: b and e, or drag in Waves)</span>', ...wholeSong(),
                     '<span></span>').replaceAll('data-run=', 'data-whole="1" data-run=')
        : '';
  $('#pr-sections').innerHTML = sections.map((x) =>
    sectionRow(esc(x.name), x.start, x.end, `<button type="button" data-del="${x.id}" title="Delete it">✕</button>`)).join('');
  $('#pr-count').value = String(count.beats);
  $('#pr-count-mode').value = count.mode === 'lead' ? 'lead' : 'clicks';
  $('#pr-click-level').value = String(Math.round(100 * (count.level ?? 0.5)));
  $('#pr-count-each').checked = !!count.each;
  $('#pr-tempo').textContent = tempoText();
}

$('#d-practice').addEventListener('click', async (e) => {
  const t = e.target;
  if (t.dataset.run) { startRun(Number(t.dataset.a), Number(t.dataset.b), t.dataset.run === 'loop', !t.dataset.whole); return; }
  if (t.dataset.tempo) {
    count.factor = Math.min(4, Math.max(0.25, count.factor * Number(t.dataset.tempo)));
    saveCount();
    renderSections();
    return;
  }
  if (t.id === 'pr-save') {
    const name = prompt('Name of the section (e.g. Solo, Bridge):');
    if (!name?.trim()) return;
    try { sections = (await api(`song/${song.id}/sections`, { method: 'POST', body: { name, start: ab.a, end: ab.b } })).sections; } catch (err) { toast('Saving failed: ' + err.message); }
    renderSections();
    return;
  }
  if (t.dataset.del) {
    try { sections = (await api('sections/' + t.dataset.del, { method: 'DELETE' })).sections; } catch (err) { toast('Deleting failed: ' + err.message); }
    renderSections();
  }
});

$('#pr-count').addEventListener('change', (e) => { count.beats = Number(e.target.value); saveCount(); });
$('#pr-count-mode').addEventListener('change', (e) => { count.mode = e.target.value; saveCount(); });
$('#pr-click-level').addEventListener('change', (e) => {
  count.level = Number(e.target.value) / 100;
  saveCount();
  const ctx = clicks();                               // a sample at the new level
  if (ctx.state === 'suspended') ctx.resume();
  keepAlive(true);
  if (player.paused) keepAlive(false);                 // (the rest timer again)
  click(ctx, ctx.currentTime + 0.3, true);
});
$('#pr-count-each').addEventListener('change', (e) => { count.each = e.target.checked; saveCount(); });

// --- display link (a TV or tablet shows the song, following this page) --------

async function openDisplay() {
  let r;
  try {
    r = await api('display', { method: 'POST' });
  } catch (e) { toast('No display link: ' + e.message); return; }
  const url = new URL('d/' + r.code + '/', location.href).href;
  $('#display-url').textContent = url;
  $('#display-url').href = url;
  $('#display-code').textContent = r.code;
  $('#display-expires').textContent = new Date(r.expires * 1000).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' });
  $('#display-qr').src = 'api/qr.svg?text=' + encodeURIComponent(url);
  $('#tv-url').textContent = new URL('d/tv/', location.href).href;
  renderTvs((await api('tv').catch(() => ({ tvs: [] }))).tvs);
  $('#d-display').showModal();
}

function renderTvs(tvs) {
  const when = (t) => t ? new Date(t * 1000).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' }) : 'not yet';
  $('#tv-list').innerHTML = tvs.map((t) => `<tr><td>${esc(t.name)}</td><td class="none">paired ${when(t.created)},` +
    ` last seen ${when(t.seen)}</td><td><button type="button" data-tv="${t.id}" title="Remove: it has to pair again">✕</button></td></tr>`).join('');
}

$('#tv-pair').addEventListener('click', async () => {
  try {
    renderTvs((await api('tv', { method: 'POST', body: { code: $('#tv-code').value, name: $('#tv-name').value } })).tvs);
    $('#tv-code').value = '';
    toast('TV paired: it shows the song in a moment');
  } catch (e) { toast(e.message, 5000); }
});
$('#tv-list').addEventListener('click', async (e) => {
  if (!e.target.dataset.tv) return;
  try {
    await api('tv/' + e.target.dataset.tv, { method: 'DELETE' });
    renderTvs((await api('tv')).tvs);
  } catch (err) { toast('Failed: ' + err.message); }
});

$('#display-end').addEventListener('click', async () => {
  try {
    await api('display', { method: 'DELETE' });
    toast('All display links ended');
    $('#d-display').close();
  } catch (e) { toast('Failed: ' + e.message); }
});

// --- guitar tone (key g / G): cues per song, played by the amp bridge ------------

let amp = null;             // what the amp bridge reported last: {state, error, busy, warning, ...}
let ampSeen = 0;            // when (ms): a bridge reports at least every 10 s
let ampTones = [];
let ampSounds = [];         // [{category, slot}]: the preset per suggested sound
let suggestAuto = false;    // play suggestions without asking
let audioAuto = false;      // the bridge routes the song to the Mustang whenever one plays
let cueList = null;         // the song's cues: {cues, artist, artist_preset}
const BRIDGE_GONE_MS = 30000;

// a cue's time: m:ss, with hundredths when it is not on a whole second
function fmtMs(ms) {
  const cs = Math.round(ms / 10) % 100;
  return fmt(Math.floor(Math.round(ms / 10) / 100)) + (cs ? '.' + String(cs).padStart(2, '0') : '');
}

function ampOnline() { return amp && Date.now() - ampSeen < BRIDGE_GONE_MS; }

// the amp's errors in plain words (the bridge reports ChordsPlay/amp.py's texts)
function plainAmpError(e) {
  if (!e) return '';
  if (/agent not running|agent unreachable/.test(e)) return 'Mustang not connected: switch it on and connect it by Bluetooth';
  return e;
}

// the amp's state in plain words (compare ChordsPlay/amp.py describe())
function ampText() {
  if (!amp) return '';
  if (!ampOnline()) return 'amp control off';
  const st = amp.state || {};
  if (amp.busy) return `Mustang → ${amp.busy}…`;
  if (amp.error && !Object.keys(st).length) return plainAmpError(amp.error);
  let text;
  if (st.connected && st.audition) text = `Mustang ▶ ${st.audition.name || 'tone'}`;
  else if (st.connected) text = st.preset ? `Mustang ${st.preset} ${st.name || ''}` : 'Mustang connected';
  // the agent drops the Bluetooth control link after 5 min without a switch (battery);
  // sound is a separate connection, and the next switch wakes the link
  else if (st.asleep) text = st.preset ? `Mustang idle · ${st.preset} ${st.name || ''}` : 'Mustang idle';
  else text = st.phase === 'connecting' ? 'Mustang: connecting…' : 'Mustang not connected';
  if (amp.warning || amp.error) text += ' – ' + (amp.warning || plainAmpError(amp.error));
  const au = amp.audio;
  if (au && !au.error) {
    if (au.busy) text += ' · ♪ connecting…';
    else if (au.routed) text += ' · ♪ via Mustang';
    else if (au.browser) text += ' · ♪ not via Mustang';
  }
  return text.trim();
}

// where the song goes, as the bridge sees it on the amp computer (tone dialog)
function audioText(au) {
  if (!au) return '';
  if (au.busy) return 'Connecting the Mustang\'s Bluetooth audio…';
  let t = au.routed ? 'The song goes through the Mustang (Bluetooth).'
    : !au.bluetooth ? 'The Mustang\'s Bluetooth audio is not connected on the amp computer.'
    : au.browser ? 'A browser plays on the amp computer, but not to the Mustang.'
    : 'The Mustang is connected for audio; no browser plays on the amp computer right now.';
  if (au.error) t += ` Last try: ${au.error}.`;
  return t;
}

function showAmp() {
  const rec = ampOnline() && amp.recording;
  $('#amp').textContent = rec ? `● REC ${fmt(amp.recording.seconds)}` : ampText();
  $('#amp').classList.toggle('rec', !!rec);
  $('#amp').classList.toggle('warn', !rec && !!(amp && (amp.warning || !ampOnline())));
  if (amp?.record_error && amp.record_error !== showAmp.lastError) toast(amp.record_error, 6000);
  const w = amp?.written;
  if (w && w.at !== showAmp.lastWrite?.at && showAmp.lastWrite !== undefined) {
    toast(w.ok ? `Written into slot ${w.slot}: "${w.name}"` : `Writing slot ${w.slot} failed: ${w.error}`, 6000);
  }
  showAmp.lastWrite = w || null;
  showAmp.lastError = amp?.record_error;
  // a take just ended: it shows up in the list once compressed and uploaded
  if (showAmp.wasRecording && !rec && $('#d-takes').open) setTimeout(openTakes, 4000);
  showAmp.wasRecording = !!rec;
  if ($('#d-amp').open) renderAmp();
}
setInterval(showAmp, 5000);     // notices a bridge that went away

function followAmp() {
  const events = new EventSource('api/events');
  // a paired TV's remote: only for this page, and only fresh (a reconnect repeats the last one)
  events.addEventListener('remote', (e) => {
    let r;
    try { r = JSON.parse(e.data); } catch (err) { return; }
    if (r.ctl !== ctlId || (r.server_time || 0) - (r.at || 0) > 10) return;
    const act = { prev, next, toggle: togglePause }[r.action];
    if (!act) return;
    act();
    toast({ prev: 'TV: previous song', next: 'TV: next song', toggle: 'TV: play/pause' }[r.action]);
  });
  events.addEventListener('amp', (e) => {
    try { amp = JSON.parse(e.data); } catch (err) { return; }
    ampSeen = Date.now();
    showAmp();
  });
}

function presetNames() { return (amp?.state?.presets || []).map((p) => p.name || ''); }

// A level: null plays the preset as stored ("original"), a number level-matched plus that
// many dB. Offered in steps of LEVEL_STEP up to ±LEVEL_MAX (the server takes up to ±12).
const LEVEL_STEP = 0.5, LEVEL_MAX = 12;
const LEVELS = Array.from({ length: 2 * LEVEL_MAX / LEVEL_STEP + 1 }, (_, i) => -LEVEL_MAX + i * LEVEL_STEP);

function dbText(level) { return level === 0 ? 'matched' : `${level > 0 ? '+' : '−'}${Math.abs(level)} dB`; }

function levelText(level) {
  return level === null || level === undefined ? '' : ' · ' + dbText(level);
}

// the options of a level list; a stored level between the steps is kept as an option of its own
function levelOptions(selected) {
  const values = selected === null || selected === undefined || LEVELS.includes(selected)
    ? LEVELS : [...LEVELS, selected].sort((a, b) => a - b);
  return '<option value="">original</option>' + values.map((v) =>
    `<option value="${v}"${v === selected ? ' selected' : ''}>${v === 0 ? 'matched (0 dB)' : dbText(v)}</option>`).join('');
}

// a level list's value; a level between the steps gets its own option first
function setLevel(sel, level) {
  if (level !== null && level !== undefined && !LEVELS.includes(level)) $(sel).innerHTML = levelOptions(level);
  $(sel).value = level === null || level === undefined ? '' : String(level);
}
for (const sel of ['#cue-level', '#tone-level']) $(sel).innerHTML = levelOptions(null);

function levelOf(sel) { return $(sel).value === '' ? null : Number($(sel).value); }

// The tone dialog has three parts, each with its own choices: the song's cues (a preset or a
// saved tone per cue), tones (a preset with level and knobs, kept under a name) and the amp's setup.
let ampTab = 'cues';
let cueKeep = null;         // the edited cue's knob settings {slot, params}: kept while its preset stays chosen
let toneFrom = null;        // the saved tone loaded into the tone editor
let toneUsage = {};         // tone id -> number of cues that play it

// the cue part's choice: {slot, level, params} or {tone_id}
function cueChoice(quiet = false) {
  const v = $('#cue-sound').value;
  if (v[0] === 't') return { tone_id: Number(v.slice(1)) };
  if (v[0] === 'p') {
    const slot = Number(v.slice(1));
    return { slot, level: levelOf('#cue-level'), params: cueKeep?.slot === slot ? { ...cueKeep.params } : null };
  }
  if (!quiet) toast('Choose a preset or a tone first');
  return null;
}

// the tone part's sound: {slot, level, params}
function toneChoice(quiet = false) {
  const slot = Number($('#tone-presets').value);
  if (!slot) { if (!quiet) toast('Choose a preset or a saved tone to start from'); return null; }
  return { slot, level: levelOf('#tone-level'),
           params: slot === knobSlot && Object.keys(knobParams).length ? { ...knobParams } : null };
}

// A list box is rebuilt only when its content changed (the dialog redraws on every report
// of the amp), and then keeps its choice and where it is scrolled to.
function fillList(list, html) {
  if (list.dataset.html === html) return;
  const value = list.value, top = list.scrollTop;
  list.innerHTML = html;
  list.dataset.html = html;
  list.value = value;
  list.scrollTop = top;
}

function showTab(name) {
  if (name === 'cues' && !song) name = 'tones';
  ampTab = name;
  for (const b of document.querySelectorAll('#d-amp [data-tab]')) b.setAttribute('aria-selected', String(b.dataset.tab === name));
  for (const p of document.querySelectorAll('#d-amp .tab-page')) p.hidden = p.dataset.page !== name;
  try { localStorage.setItem('ampTab', name); } catch (e) { /* ignore */ }
  if (name === 'tones') {
    loadKnobs();
    // how many cues play each tone: cues may have changed in the other part
    api('amp').then((s) => { ampTones = s.tones; toneUsage = s.tone_usage || {}; renderAmp(); }).catch(() => {});
  }
  drawWave();
}

function levelHint(hint, slot, sel) {
  // the level needs a measured preset; else it plays the preset's own volume plus the offset
  $(hint).textContent = slot && $(sel).value !== '' && cueList && !(cueList.measured || []).includes(slot)
    ? 'not measured: its own volume plus this' : '';
}

function presetOptions(names, st, prefix = '') {
  return Array.from({ length: 100 }, (_, i) =>
    `<option value="${prefix}${i + 1}">${i + 1} ${esc(names[i] || '')}${st.preset === i + 1 && !st.audition ? ' ◀' : ''}</option>`).join('');
}

function toneLabel(t) {
  const lv = t.level === null || t.level === undefined ? 'original' : dbText(t.level);
  return `${esc(t.name)} (${t.base_slot} ${esc(t.base_name || '')}, ${lv})`;
}

function renderAmp() {
  const names = presetNames();
  const st = amp?.state || {};
  $('#amp-status-line').textContent = amp ? ampText() : 'amp control not set up';
  $('#d-amp [data-tab="cues"]').disabled = !song;
  renderSetup(st);
  renderTones(names, st);
  if (song && cueList) renderCues(names, st);
}

function renderSetup(st) {
  const au = ampOnline() ? amp.audio : null;
  $('#amp-audio').hidden = !au;
  $('#amp-audio-text').textContent = audioText(au);
  $('#amp-audio-go').disabled = !!(au && (au.busy || au.routed));
  $('#amp-audio-auto').checked = audioAuto;
  $('#amp-status').textContent = amp ? ampText() + (!ampOnline()
    ? ' – amp control isn\'t running on the computer the Mustang is connected to (see Help, Guitar tone)'
    : st.asleep ? ' – the control link sleeps after 5 minutes without a switch; sound is not affected, ' +
      'and the next switch wakes it (a few seconds)' : '') :
    'Amp control isn\'t set up yet: see "Set up amp control…" below. You can choose and save sounds without it; ' +
    'the Mustang switches once amp control runs.';
  const names = presetNames();
  // rebuilt only when not in use: the dialog redraws every few seconds with the amp's state
  if (!$('#amp-sounds').contains(document.activeElement)) $('#amp-sounds').innerHTML = ampSounds.map((s) => `<tr><td>${esc(s.category.replace('_', ' '))}</td><td>` +
    `<select data-sound="${s.category}"><option value="">no preset</option>` +
    Array.from({ length: 100 }, (_, i) => `<option value="${i + 1}" ${s.slot === i + 1 ? 'selected' : ''}>${i + 1} ${esc(names[i] || '')}</option>`).join('') +
    '</select></td><td>' +
    `<select data-sound-level="${s.category}">${levelOptions(s.level)}</select></td></tr>`).join('');
}

function renderTones(names, st) {
  fillList($('#tone-presets'), presetOptions(names, st));
  fillList($('#tone-saved'), ampTones.length ? ampTones.map((t) => `<option value="${t.id}">${toneLabel(t)}` +
    `${toneUsage[t.id] ? ` · ${toneUsage[t.id]} cue${toneUsage[t.id] > 1 ? 's' : ''}` : ''}</option>`).join('')
    : '<option disabled>No tones saved yet</option>');
  if (toneFrom && !ampTones.some((t) => t.id === toneFrom.id)) toneFrom = null;      // deleted meanwhile
  $('#tone-del').hidden = !toneFrom;
  levelHint('#tone-level-hint', Number($('#tone-presets').value), '#tone-level');
}

function renderCues(names, st) {
  const cues = cueList.cues || [];
  $('#amp-cues').innerHTML = cues.length ? cues.map((c) => {
    const what = c.tone_id ? `tone ${esc(c.tone || '(deleted)')}`
      : `${c.slot} ${esc(c.name || '')}${levelText(c.level)}${c.params ? ' · tuned' : ''}`;
    const now = !c.tone_id && names[c.slot - 1] && c.name && names[c.slot - 1].trim() !== c.name.trim()
      ? ` <span class="err">slot is now "${esc(names[c.slot - 1])}": save it again</span>` : '';
    return `<tr${c.start === cueSel ? ' class="sel"' : ''}><td class="t">${fmtMs(c.start)}</td><td>${what}${now}</td>` +
      `<td><button type="button" data-play="${c.start}" title="Play from before it">▶</button>` +
      `<button type="button" data-edit="${c.start}" title="Edit: its position and sound go into the editor below">✎</button>` +
      `<button type="button" data-store="${c.start}" title="Store the chosen sound in this cue">⤓</button>` +
      `<button type="button" data-del="${c.start}" title="Delete this cue">✕</button></td></tr>`;
  }).join('') : '<tr><td class="hint">No cues for this song yet.</td></tr>';
  const base = cueList.artist_preset;
  $('#amp-artist').innerHTML = base
    ? `Artist preset (${esc(cueList.artist)}): ${base.slot} ${esc(base.name || '')}${levelText(base.level)} ` +
      '<button type="button" id="amp-artist-del" title="Delete the artist preset">✕</button>'
    : `No artist preset for ${esc(cueList.artist || '')}.`;
  const sg = cueList.suggestion;
  const slotName = (slot) => slot ? `${slot} ${names[slot - 1] || ''}`.trim() : 'no preset';
  $('#amp-suggest').innerHTML = !sg ? 'No suggestion yet (made in the background for new songs).' :
    `Suggested sound: <b>${esc(sg.category.replace('_', ' '))}</b> → ${esc(slotName(sg.slot))}${levelText(sg.level)} · ` +
    (sg.rejected ? 'rejected, not played' : !sg.known ? 'a guess: not played'
      : suggestAuto ? 'played when there is no other preset' : 'shown only (automatic suggestions are off, see Amp)') +
    (sg.genres?.length ? ` <span class="none">(${esc(sg.genres.join(', '))})</span>` : '') +
    (sg.slot ? '<button type="button" id="amp-suggest-use" title="Store it as this song\'s start preset">Use from start</button>' : '') +
    `<button type="button" id="amp-suggest-reject">${sg.rejected ? 'Allow again' : 'Reject'}</button>`;
  fillList($('#cue-sound'), `<optgroup label="Presets">${presetOptions(names, st, 'p')}</optgroup>` +
    (ampTones.length ? `<optgroup label="Tones">${ampTones.map((t) => `<option value="t${t.id}">${toneLabel(t)}</option>`).join('')}</optgroup>` : ''));
  const v = $('#cue-sound').value;
  $('#cue-level').disabled = v[0] === 't';
  levelHint('#cue-level-hint', v[0] === 'p' ? Number(v.slice(1)) : 0, '#cue-level');
  const keep = cueKeep && v === 'p' + cueKeep.slot;
  $('#cue-tuned').hidden = !keep;
  $('#cue-tuned').textContent = keep ? 'This cue\'s own knob settings are kept with this preset. To change them, ' +
    'make a tone of it in Tones and choose that tone here.' : '';
  $('#amp-here').textContent = `Save as new cue at ${fmtMs(cuePos)}`;
  if (cueSel !== null && !cues.some((c) => c.start === cueSel)) cueSel = null;     // deleted meanwhile
  $('#amp-update').hidden = cueSel === null;
  $('#amp-update').textContent = cueSel === null ? 'Update cue'
    : cueSel === cuePos ? `Update cue at ${fmtMs(cueSel)}` : `Move cue ${fmtMs(cueSel)} → ${fmtMs(cuePos)}`;
  $('#amp-pos-what').textContent = cueSel === null ? 'New cue at' : `Cue ${fmtMs(cueSel)}, position`;
  $('#amp-pos-new').hidden = cueSel === null;
  drawWave();
}

async function openAmp() {
  try {
    const [state, cues] = await Promise.all([api('amp'), song ? api(`song/${song.id}/cues`) : null]);
    if (state.amp) { amp = state.amp; ampSeen = state.amp.online ? Date.now() : 0; }
    ampTones = state.tones;
    toneUsage = state.tone_usage || {};
    ampSounds = state.sounds;
    suggestAuto = state.suggest_auto;
    audioAuto = state.audio_auto;
    $('#amp-suggest-auto').checked = suggestAuto;
    cueList = cues;
  } catch (e) { toast('Loading failed: ' + e.message); return; }
  $('#amp-title').textContent = song ? `Guitar tone: ${song.title} – ${song.artist}` : 'Guitar tone';
  $('#cue-sound').value = '';
  $('#amp-bridge-box').hidden = true;
  cueSel = null;
  cueKeep = null;
  if (song) setPos((player.currentTime || 0) * 1000, false);
  let tab = ampTab;
  try { tab = localStorage.getItem('ampTab') || tab; } catch (e) { /* ignore */ }
  renderAmp();
  $('#d-amp').showModal();
  showTab(tab);
  if (song) { loadWave(); startWaveTick(); }
}

async function saveCue(start) {
  const what = cueChoice();
  if (!what) return;
  try {
    cueList = await api(`song/${song.id}/cues/${start}`, { method: 'PUT', body: what });
    cueSel = start;
    renderAmp();
    toast(`Stored from ${fmtMs(start)}`);
  } catch (e) { toast('Saving failed: ' + e.message); }
}

// --- cue position editor: the song's waveform around a position, exact to 10 ms ---

let cuePos = 0;             // ms: where a new cue goes, or where the edited cue moves to
let cueSel = null;          // start (ms) of the cue being edited, null: a new cue
let wave = null;            // {id, step: ms per bucket, peak, rms (Float32Array, 0..1)}
let waveDrag = null;        // pointer drag on the waveform: {x, pos, moved}
let listenTo = null;        // ms: pause there (▶ Around)
const WAVE_STEP = 5;        // ms per bucket

function songMs() { return isFinite(player.duration) ? Math.round(player.duration * 1000) : 0; }

function setPos(ms, redraw = true) {
  const end = songMs();
  cuePos = Math.max(0, Math.round(ms / 10) * 10);
  if (end) cuePos = Math.min(cuePos, Math.floor(end / 10) * 10);
  $('#amp-pos-range').max = String(end || 1000);
  $('#amp-pos-range').value = String(cuePos);
  if (document.activeElement !== $('#amp-pos-input')) $('#amp-pos-input').value = fmtPos(cuePos);
  if (redraw) renderAmp();
}

function fmtPos(ms) {     // always with hundredths, for the editor
  const cs = Math.round(ms / 10);
  return fmt(Math.floor(cs / 100)) + '.' + String(cs % 100).padStart(2, '0');
}

function parsePos(text) {   // "1:23.45", "83.45", "1:23"
  const m = /^\s*(?:(\d+):)?(\d+(?:[.,]\d*)?)\s*$/.exec(text);
  if (!m) return null;
  return Math.round(((Number(m[1] || 0) * 60) + Number(m[2].replace(',', '.'))) * 1000);
}

// The song's audio decoded once in the browser, reduced to peak and loudness per 5 ms.
async function loadWave() {
  if (!song || wave?.id === song.id) { drawWave(); return; }
  const id = song.id;
  wave = { id, step: WAVE_STEP, peak: null, rms: null };
  $('#amp-wave-hint').textContent = 'Loading the waveform…';
  try {
    const data = await (await fetch('api/audio/' + id)).arrayBuffer();
    const Ctx = window.OfflineAudioContext || window.webkitOfflineAudioContext;
    const audio = await new Ctx(1, 1, 44100).decodeAudioData(data);
    const per = Math.max(1, Math.round(audio.sampleRate * WAVE_STEP / 1000));
    const n = Math.ceil(audio.length / per);
    const peak = new Float32Array(n), rms = new Float32Array(n);
    const chans = Array.from({ length: audio.numberOfChannels }, (_, c) => audio.getChannelData(c));
    for (let b = 0; b < n; b++) {
      let p = 0, sum = 0;
      const end = Math.min(audio.length, (b + 1) * per);
      for (let i = b * per; i < end; i++) {
        let v = 0;
        for (const ch of chans) v += ch[i];
        v /= chans.length;
        const a = Math.abs(v);
        if (a > p) p = a;
        sum += v * v;
      }
      peak[b] = p;
      rms[b] = Math.sqrt(sum / Math.max(1, end - b * per));
    }
    if (wave?.id !== id) return;          // another song meanwhile
    wave = { id, step: WAVE_STEP, peak, rms };
    $('#amp-wave-hint').textContent = 'Light: peaks, dark: loudness, red: the position, orange: the other cues, ' +
      'blue: where the song is. Put a cue where the part starts as you hear it: the switch is timed for ' +
      'the song\'s delay over Bluetooth.';
  } catch (e) {
    if (wave?.id === id) wave = { id, step: WAVE_STEP, peak: null, rms: null, error: true };
    $('#amp-wave-hint').textContent = 'No waveform: this browser cannot decode the song (' + e.message + '). ' +
      'The position still works by ear.';
  }
  drawWave();
}

function waveFollow() { return $('#amp-follow').checked && !!player.src; }

// the window shown: around the position, or around the song's position while following it
function waveWindow() {
  const span = Number($('#amp-zoom').value);
  const center = waveFollow() ? player.currentTime * 1000 : cuePos;
  return { span, from: center - span / 2 };
}

function stopFollowing() {
  if (!$('#amp-follow').checked) return null;
  const { span, from } = waveWindow();
  $('#amp-follow').checked = false;
  try { localStorage.setItem('waveFollow', ''); } catch (e) { /* ignore */ }
  return from + span / 2;
}

function drawWave() {
  const cv = $('#amp-wave');
  if (!$('#d-amp').open || !cv.clientWidth) return;
  const dpr = window.devicePixelRatio || 1;
  const w = Math.round(cv.clientWidth * dpr), h = Math.round(cv.clientHeight * dpr);
  if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
  const g = cv.getContext('2d');
  const css = getComputedStyle(document.documentElement);
  const color = (name) => css.getPropertyValue(name).trim();
  g.clearRect(0, 0, w, h);
  const { span, from } = waveWindow();
  const x = (ms) => (ms - from) / span * w;
  const mid = h / 2 - 7 * dpr, amp = mid - 2 * dpr;
  // seconds (and tenths when the window is short)
  g.font = `${10 * dpr}px system-ui, sans-serif`;
  g.fillStyle = color('--muted');
  const tick = span <= 2000 ? 100 : span <= 10000 ? 1000 : 5000;
  for (let t = Math.ceil(from / tick) * tick; t <= from + span; t += tick) {
    if (t < 0) continue;
    const whole = t % 1000 === 0;
    g.fillRect(Math.round(x(t)), h - (whole ? 12 : 8) * dpr, dpr, (whole ? 12 : 8) * dpr);
    if (whole && (tick >= 1000 || span <= 2000)) g.fillText(fmt(t / 1000), x(t) + 3 * dpr, h - 2 * dpr);
  }
  // the waveform: square root, so quiet parts are visible too
  if (wave?.peak) {
    const n = wave.peak.length;
    for (let px = 0; px < w; px++) {
      const a = Math.floor((from + px / w * span) / wave.step), b = Math.max(a + 1, Math.floor((from + (px + 1) / w * span) / wave.step));
      if (b <= 0 || a >= n) continue;
      let p = 0, r = 0;
      for (let i = Math.max(0, a); i < Math.min(n, b); i++) { if (wave.peak[i] > p) p = wave.peak[i]; if (wave.rms[i] > r) r = wave.rms[i]; }
      const ph = Math.sqrt(p) * amp, rh = Math.sqrt(r) * amp;
      g.fillStyle = color('--line');
      g.fillRect(px, mid - ph, 1, 2 * ph || 1);
      g.fillStyle = color('--muted');
      g.fillRect(px, mid - rh, 1, 2 * rh || 1);
    }
  } else if (!wave?.error) {
    g.fillStyle = color('--muted');
    g.fillText('loading…', 8 * dpr, mid);
  }
  // the other cues, the song's position, the position being set
  const line = (ms, c, width) => { const X = x(ms); if (X >= -width && X <= w + width) { g.fillStyle = c; g.fillRect(Math.round(X - width / 2), 0, width, h - 14 * dpr); } };
  for (const c of cueList?.cues || []) if (c.start !== cueSel) line(c.start, '#e08a1e', 2 * dpr);
  if (player.src) line(player.currentTime * 1000, color('--accent'), 2 * dpr);
  line(cuePos, color('--error'), 2 * dpr);
}

// the song's position moves the blue line; ▶ Around pauses at its end
let waveTicking = false;
function waveTick() {
  if (!$('#d-amp').open) { waveTicking = false; return; }
  if (listenTo !== null && player.currentTime * 1000 >= listenTo) { player.pause(); listenTo = null; }
  drawWave();
  waveTicking = !player.paused;
  if (waveTicking) requestAnimationFrame(waveTick);
}
function startWaveTick() {
  if (!waveTicking) { waveTicking = true; requestAnimationFrame(waveTick); }
}
player.addEventListener('play', startWaveTick);
player.addEventListener('seeked', () => drawWave());

function listen(stop) {
  if (!player.src) return;
  const pre = Number($('#amp-preroll').value);
  player.currentTime = Math.max(0, cuePos - pre) / 1000;
  listenTo = stop ? cuePos + 2000 : null;
  player.play().then(startWaveTick).catch(() => {});
}

$('#amp-pos-range').addEventListener('input', (e) => setPos(Number(e.target.value)));
$('#amp-pos-input').addEventListener('change', (e) => {
  const ms = parsePos(e.target.value);
  if (ms === null) { toast('Position as m:ss.cc, e.g. 1:23.45'); e.target.value = fmtPos(cuePos); return; }
  setPos(ms);
  e.target.value = fmtPos(cuePos);
});
$('#amp-pos-input').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); e.target.blur(); } });
$('#amp-pos').addEventListener('click', (e) => {
  const step = e.target.dataset.step;
  if (step !== undefined) setPos(cuePos + Number(step));
});
$('#amp-pos-now').addEventListener('click', () => setPos((player.currentTime || 0) * 1000));
$('#amp-pos-new').addEventListener('click', () => { cueSel = null; cueKeep = null; renderAmp(); });
$('#amp-zoom').addEventListener('change', () => drawWave());
$('#amp-listen').addEventListener('click', () => listen(true));
$('#amp-listen-on').addEventListener('click', () => listen(false));
$('#amp-listen-stop').addEventListener('click', () => { listenTo = null; player.pause(); });
$('#amp-follow').addEventListener('change', (e) => {
  try { localStorage.setItem('waveFollow', e.target.checked ? '1' : ''); } catch (err) { /* ignore */ }
  drawWave();
});
try { $('#amp-follow').checked = !!localStorage.getItem('waveFollow'); } catch (e) { /* ignore */ }
$('#amp-wave').addEventListener('pointerdown', (e) => {
  waveDrag = { x: e.clientX, pos: cuePos, moved: false };
  e.target.setPointerCapture(e.pointerId);
});
$('#amp-wave').addEventListener('pointermove', (e) => {
  if (!waveDrag) return;
  const dx = e.clientX - waveDrag.x;
  if (Math.abs(dx) > 3 && !waveDrag.moved) {
    waveDrag.moved = true;
    const center = stopFollowing();       // dragging scrolls by hand from what is shown
    if (center !== null) waveDrag.pos = center;
  }
  if (waveDrag.moved) setPos(waveDrag.pos - dx / e.target.clientWidth * waveWindow().span);
});
$('#amp-wave').addEventListener('pointerup', (e) => {
  if (waveDrag && !waveDrag.moved) {
    const r = e.target.getBoundingClientRect();
    const { span, from } = waveWindow();
    setPos(from + (e.clientX - r.left) / r.width * span);
  }
  waveDrag = null;
});
$('#amp-wave').addEventListener('wheel', (e) => {
  e.preventDefault();
  const center = stopFollowing();
  setPos((center ?? cuePos) + Math.sign(e.deltaY || e.deltaX) * waveWindow().span / 25);
}, { passive: false });
$('#amp-wave').addEventListener('keydown', (e) => {
  if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
  e.preventDefault();
  e.stopPropagation();
  const by = e.shiftKey ? 1000 : e.altKey ? 10 : 100;
  setPos(cuePos + (e.key === 'ArrowLeft' ? -by : by));
});
window.addEventListener('resize', () => drawWave());

$('#amp-update').addEventListener('click', async () => {
  if (cueSel === null) return;
  const what = cueChoice(true) || {};
  try {
    cueList = await api(`song/${song.id}/cues/${cueSel}`, { method: 'PUT', body: { ...what, to: cuePos } });
    toast(cueSel === cuePos ? `Cue at ${fmtMs(cuePos)} updated` : `Cue moved to ${fmtMs(cuePos)}`);
    cueSel = cuePos;
    renderAmp();
  } catch (e) { toast('Saving failed: ' + e.message); }
});

$('#d-amp .tabs').addEventListener('click', (e) => {
  const tab = e.target.closest('[data-tab]');
  if (tab && !tab.disabled) showTab(tab.dataset.tab);
});

async function trySound(what) {
  if (!what) return;
  try {
    const r = await api('amp/try', { method: 'POST', body: what });
    if (!r.bridge) toast('Amp control isn\'t running: the Mustang was not switched');
  } catch (e) { toast('Failed: ' + e.message); }
}

// --- cues: the sound of each cue is a preset (with a level) or a saved tone

$('#cue-sound').addEventListener('change', () => renderAmp());
$('#cue-level').addEventListener('change', () => renderAmp());
$('#cue-sound').addEventListener('dblclick', () => trySound(cueChoice()));
$('#cue-try').addEventListener('click', () => trySound(cueChoice()));
$('#amp-start').addEventListener('click', () => saveCue(0));
$('#amp-here').addEventListener('click', () => saveCue(cuePos));

$('#amp-artist-save').addEventListener('click', async () => {
  const what = cueChoice();
  if (!what) return;
  if (!what.slot) { toast('For the artist, choose a preset (tones are for cues)'); return; }
  try {
    await api('artist-preset', { method: 'PUT', body: { artist: song.artist, ...what } });
    cueList = await api(`song/${song.id}/cues`);
    renderAmp();
    toast(`Preset ${what.slot} for every song of ${song.artist}`);
  } catch (e) { toast('Saving failed: ' + e.message); }
});

$('#d-amp').addEventListener('click', async (e) => {
  const play = e.target.dataset.play, del = e.target.dataset.del;
  const editAt = e.target.dataset.edit, storeAt = e.target.dataset.store;
  try {
    if (editAt !== undefined) {
      const c = cueList.cues.find((x) => x.start === Number(editAt));
      cueSel = c.start;
      setPos(c.start, false);
      if (c.tone_id) {
        $('#cue-sound').value = 't' + c.tone_id;
        cueKeep = null;
      } else {
        $('#cue-sound').value = 'p' + c.slot;
        setLevel('#cue-level', c.level);
        cueKeep = c.params ? { slot: c.slot, params: { ...c.params } } : null;
      }
      renderAmp();
      $('#cue-sound').selectedOptions[0]?.scrollIntoView({ block: 'nearest' });
      toast(`Editing the cue at ${fmtMs(c.start)}: change position or sound, then "Update cue"`);
    } else if (storeAt !== undefined) {
      const what = cueChoice();
      if (!what) return;
      cueList = await api(`song/${song.id}/cues/${storeAt}`, { method: 'PUT', body: what });
      renderAmp();
      toast(`Stored at ${fmtMs(Number(storeAt))}`);
    } else if (play !== undefined) {
      player.currentTime = Math.max(0, Number(play) - Number($('#amp-preroll').value)) / 1000;
      listenTo = null;
      player.play().catch(() => {});
    } else if (del !== undefined) {
      cueList = await api(`song/${song.id}/cues/${del}`, { method: 'DELETE' });
      renderAmp();
    } else if (e.target.id === 'amp-artist-del') {
      await api('artist-preset', { method: 'DELETE', body: { artist: cueList.artist } });
      cueList = await api(`song/${song.id}/cues`);
      renderAmp();
    }
  } catch (err) { toast('Failed: ' + err.message); }
});

$('#amp-suggest').addEventListener('click', async (e) => {
  try {
    if (e.target.id === 'amp-suggest-use') {
      cueList = await api(`song/${song.id}/cues/0`, { method: 'PUT', body: { slot: cueList.suggestion.slot,
                                                                            level: cueList.suggestion.level } });
      toast('Stored as the start preset');
    } else if (e.target.id === 'amp-suggest-reject') {
      cueList = await api(`song/${song.id}/suggestion`, { method: 'POST', body: { rejected: !cueList.suggestion.rejected } });
    } else return;
    renderAmp();
  } catch (err) { toast('Failed: ' + err.message); }
});

// --- tones: a preset with its level and knobs, adjusted while the song plays and kept under a name

let knobSlot = null;        // the preset the knobs belong to
let knobParams = {};        // only what differs from the preset ('reverb.wetlvl': 0.4, 'delay.bypass': true)
let knobList = [];

async function loadKnobs(tries = 0) {
  const slot = Number($('#tone-presets').value);
  if (!$('#d-amp').open || ampTab !== 'tones') return;
  if (!slot) { $('#amp-knobs').innerHTML = ''; $('#amp-knobs-hint').textContent = 'Choose a preset or a saved tone: its knobs show here.'; return; }
  if (slot !== knobSlot) { knobSlot = slot; knobParams = {}; }
  let r;
  try {
    const res = await fetch('api/amp/preset/' + slot);
    r = await res.json();
    if (res.status === 202) {
      $('#amp-knobs').innerHTML = '';
      $('#amp-knobs-hint').textContent = r.bridge ? 'Reading the preset from the amp…'
        : 'This sound is not known yet, and amp control isn\'t running to read it from the Mustang.';
      if (r.bridge && tries < 12 && Number($('#tone-presets').value) === slot) setTimeout(() => loadKnobs(tries + 1), 1500);
      return;
    }
  } catch (e) { $('#amp-knobs-hint').textContent = 'Loading failed: ' + e.message; return; }
  if (Number($('#tone-presets').value) !== slot) return;
  knobList = r.knobs;
  $('#amp-knobs-hint').textContent = `Knobs of ${slot} ${r.name}` + (toneFrom ? ` in the tone "${toneFrom.name}"` : '') +
    ': a changed knob is bold.' + ($('#tone-level').value === '' ? ' With "original" the preset keeps its own volume.' : '');
  renderKnobs();
}

// the knobs grouped by the amp's blocks, in the order the signal passes them
const KNOB_GROUPS = [['stomp', 'Stomp'], ['amp', 'Amp'], ['mod', 'Modulation'], ['delay', 'Delay'], ['reverb', 'Reverb']];

function knobHtml(k, label) {
  const changed = k.key in knobParams;
  if (k.kind === 'switch') {
    const on = changed ? !knobParams[k.key] : k.value;     // stored as "bypass"
    return `<label class="switch${changed ? ' changed' : ''}"><input type="checkbox" data-knob="${k.key}" ${on ? 'checked' : ''}>${esc(label)}</label>`;
  }
  const v = changed ? knobParams[k.key] : k.value;
  return `<label${changed ? ' class="changed"' : ''}>${esc(label)}<input type="range" min="0" max="1" step="0.01" ` +
    `value="${v}" data-knob="${k.key}"><output>${v.toFixed(2)}</output></label>`;
}

function renderKnobs() {
  const groups = KNOB_GROUPS.map(([id, name]) => {
    const ks = knobList.filter((k) => k.key.split('.')[0] === id);
    if (!ks.length) return '';
    const sw = ks.find((k) => k.kind === 'switch');
    // "Reverb (FenderLargeHall) on" -> "Reverb (FenderLargeHall)"; "Reverb mix" -> "Mix"
    const head = sw ? knobHtml(sw, sw.label.replace(/ on$/, '').replace(/^\w+/, name))
      : `<span class="knob-head">${esc(name)}</span>`;
    const rest = ks.filter((k) => k !== sw).map((k) => {
      const short = k.label.replace(/^\S+\s+/, '');
      return knobHtml(k, short.charAt(0).toUpperCase() + short.slice(1));
    }).join('');
    return `<fieldset class="knob-group"><legend>${head}</legend>${rest}</fieldset>`;
  }).join('');
  $('#amp-knobs').innerHTML = groups || '<span class="hint">This preset has no adjustable knobs.</span>';
}

function tryTone() { trySound(toneChoice()); }
function tryLive() { if ($('#amp-live').checked) tryTone(); }

// a preset starts a new sound; a saved tone loads its preset, level and knobs
$('#tone-presets').addEventListener('change', () => {
  $('#tone-saved').value = '';
  toneFrom = null;
  knobSlot = Number($('#tone-presets').value);
  knobParams = {};
  renderAmp();
  loadKnobs();
});
$('#tone-saved').addEventListener('change', () => {
  const t = ampTones.find((x) => x.id === Number($('#tone-saved').value));
  if (!t) return;
  toneFrom = t;
  $('#tone-presets').value = String(t.base_slot);
  $('#tone-presets').selectedOptions[0]?.scrollIntoView({ block: 'nearest' });
  setLevel('#tone-level', t.level);
  knobSlot = t.base_slot;
  knobParams = { ...(t.params || {}) };
  renderAmp();
  loadKnobs();
});
$('#tone-level').addEventListener('change', () => { renderAmp(); tryLive(); });
$('#tone-presets').addEventListener('dblclick', tryTone);
$('#tone-saved').addEventListener('dblclick', tryTone);
$('#tone-try').addEventListener('click', tryTone);

let knobTimer = null;
$('#amp-knobs').addEventListener('input', (e) => {
  const key = e.target.dataset.knob;
  if (!key) return;
  const knob = knobList.find((k) => k.key === key);
  if (knob.kind === 'switch') {
    const bypass = !e.target.checked;
    if (bypass === !knob.value) delete knobParams[key]; else knobParams[key] = bypass;
  } else {
    const v = Math.round(Number(e.target.value) * 1000) / 1000;
    e.target.nextElementSibling.value = v.toFixed(2);
    if (Math.abs(v - knob.value) < 0.005) delete knobParams[key]; else knobParams[key] = v;
  }
  e.target.closest('label').classList.toggle('changed', key in knobParams);
  if ($('#amp-live').checked) { clearTimeout(knobTimer); knobTimer = setTimeout(tryTone, 250); }
});
$('#amp-knobs-reset').addEventListener('click', () => {
  knobParams = {};
  renderKnobs();
  tryLive();
});

function soundName(what) {
  const names = presetNames();
  const base = names[what.slot - 1] || 'Slot ' + what.slot;
  return `${base}${what.level === null ? '' : what.level === 0 ? ' matched' : ` ${what.level > 0 ? '+' : '-'}${Math.abs(what.level)}`}` +
    (what.params ? ' tuned' : '');
}

$('#amp-tone-save').addEventListener('click', async () => {
  const what = toneChoice();
  if (!what) return;
  const name = prompt('Save this sound as a tone named (the same name replaces that tone)',
    toneFrom ? toneFrom.name : song ? `${song.title} – ${soundName(what)}` : soundName(what));
  if (!name) return;
  try {
    const r = await api('tones', { method: 'POST', body: { ...what, name } });
    ampTones = r.tones;
    toneFrom = ampTones.find((t) => t.id === r.id) || null;
    renderAmp();
    $('#tone-saved').value = String(r.id);
    toast(`Tone "${name}" saved: it can be chosen for any cue now`);
  } catch (e) { toast('Saving failed: ' + e.message); }
});

$('#tone-del').addEventListener('click', async () => {
  if (!toneFrom) return;
  const n = toneUsage[toneFrom.id] || 0;
  if (!confirm(`Delete the tone "${toneFrom.name}"?` + (n ? ` The ${n} cue${n > 1 ? 's' : ''} playing it are deleted too.` : ''))) return;
  try {
    const r = await api('tones/' + toneFrom.id, { method: 'DELETE' });
    ampTones = r.tones;
    toneUsage = r.tone_usage || {};
    toast(`Tone "${toneFrom.name}" deleted`);
    toneFrom = null;
    $('#tone-saved').value = '';
    if (song) cueList = await api(`song/${song.id}/cues`);
    renderAmp();
  } catch (e) { toast('Deleting failed: ' + e.message); }
});

$('#amp-write').addEventListener('click', async () => {
  const what = toneChoice();
  if (!what) return;
  const names = presetNames();
  const free = Array.from({ length: 28 }, (_, i) => i + 72);
  const list = free.map((s) => `${s} ${names[s - 1] || '?'}`).join(', ');
  const answer = prompt(`Write it into which amp slot (72–99)? The factory presets (1–71) stay as they are.\n${list}`,
    String(free.find((s) => (names[s - 1] || '').trim() === 'Empty') || 99));
  if (!answer) return;
  const target = Number(answer);
  if (!free.includes(target)) { toast('Only slots 72 to 99'); return; }
  const now = (names[target - 1] || '').trim();
  if (now && now !== 'Empty' && !confirm(`Slot ${target} holds "${now}". Overwrite it?`)) return;
  const name = prompt('Name on the amp (at most 16 characters)', (toneFrom ? toneFrom.name : soundName(what)).slice(0, 16));
  if (!name) return;
  if (name.trim().length > 16) { toast('The amp keeps names of at most 16 characters'); return; }
  try {
    await api('amp/write', { method: 'POST', body: { ...what, target, name } });
    toast(`Writing slot ${target}…`);
  } catch (e) { toast(e.message, 6000); }
});

// --- the amp's setup

$('#amp-audio-go').addEventListener('click', async () => {
  try {
    const r = await api('amp/audio', { method: 'POST' });
    toast(r.bridge ? 'Amp control: connecting the Mustang\'s audio and moving the browser\'s sound there…'
      : 'Amp control isn\'t running on the amp computer');
  } catch (e) { toast('Failed: ' + e.message); }
});

$('#amp-audio-auto').addEventListener('change', async (e) => {
  try {
    audioAuto = (await api('amp/audio-auto', { method: 'PUT', body: { on: e.target.checked } })).audio_auto;
    renderAmp();
  } catch (err) { toast('Saving failed: ' + err.message); e.target.checked = audioAuto; }
});

$('#amp-suggest-auto').addEventListener('change', async (e) => {
  try {
    suggestAuto = (await api('amp/suggest-auto', { method: 'PUT', body: { on: e.target.checked } })).suggest_auto;
    renderAmp();
  } catch (err) { toast('Saving failed: ' + err.message); e.target.checked = suggestAuto; }
});

$('#amp-sounds').addEventListener('change', async (e) => {
  const cat = e.target.dataset.sound || e.target.dataset.soundLevel;
  if (!cat) return;
  const row = e.target.closest('tr');
  const slot = row.querySelector('[data-sound]').value, level = row.querySelector('[data-sound-level]').value;
  try {
    ampSounds = (await api('amp/sounds/' + cat, { method: 'PUT', body: {
      slot: slot ? Number(slot) : null, level: level === '' ? null : Number(level) } })).sounds;
    if (song) cueList = await api(`song/${song.id}/cues`);
    renderAmp();
  } catch (err) { toast('Saving failed: ' + err.message); }
});

$('#amp-bridge').addEventListener('click', async () => {
  if (!confirm('Make a new line for amp control? Amp control with the previous line stops until it gets the new one.')) return;
  try {
    const r = await api('bridge', { method: 'POST' });
    $('#amp-bridge-line').textContent = 'CHORDSPLAY_BRIDGE_URL=' + new URL(r.path, location.href).href;
    $('#amp-bridge-box').hidden = false;
  } catch (e) { toast('Failed: ' + e.message); }
});

async function learnTone() {
  if (!song) { toast('Start a song first'); return; }
  try {
    const r = await api('amp/learn', { method: 'POST', body: { id: song.id, t: player.currentTime || 0 } });
    toast(`Stored: ${r.slot} ${r.name || ''} from ${fmtMs(r.start)}`);
  } catch (e) { toast(e.message, 6000); }
}

// --- PDF tabs (key v / V) -----------------------------------------------------------

// a PDF's name for the user; the folder too when the song has two files of that name
function pdfLabel(p) {
  const name = p.part || p.name.replace(/\.pdf$/i, '');
  const twin = song?.pdfs?.some((o) => o.id !== p.id && (o.part || o.name) === (p.part || p.name));
  return twin ? `${name} (${p.folder})` : name;
}

async function setView(pdfId) {
  try {
    song.view = (await api(`song/${song.id}/view`, { method: 'PUT', body: { pdf: pdfId } })).view;
  } catch (e) { toast('Switching failed: ' + e.message); return; }
  pdfPage = 1;
  render();
  showTitle();
  pushNow();
}

// v: text -> first PDF -> next PDF ... -> text
function cycleView() {
  if (!song) { toast('Start a song first'); return; }
  if (!song.pdfs?.length) { toast('This song has no PDF – V lists all PDFs'); return; }
  const order = [null, ...song.pdfs.map((p) => p.id)];
  const next = order[(order.indexOf(song.view ?? null) + 1) % order.length];
  setView(next);
  toast(next ? 'PDF: ' + pdfLabel(song.pdfs.find((p) => p.id === next)) : 'Song text');
}

function turn(step) {
  if (!song?.view || !pdfShown) return;
  const first = step > 0 ? pdfShown.first + pdfShown.count : pdfShown.first - pdfShown.count;
  if (first > pdfShown.pages || pdfShown.first === 1 && step < 0) return;
  pdfPage = Math.max(1, first);
  render();
  pushNow();
}

let pdfAll = [];

async function openPdfs() {
  let r;
  try { r = await api('pdfs'); } catch (e) { toast('Loading failed: ' + e.message); return; }
  pdfAll = r.pdfs;
  $('#pdf-folders').textContent = r.folders.join(', ');
  if (song) {   // the song's PDFs may have changed by the scan
    try { Object.assign(song, await api('song/' + song.id)); } catch (e) { /* keep */ }
  }
  renderPdfs();
  if (!$('#d-pdf').open) $('#d-pdf').showModal();
}

function renderPdfs() {
  $('#pdf-title').textContent = song ? `PDF tabs: ${song.title} – ${song.artist}` : 'PDF tabs';
  $('#pdf-views-head').hidden = !song;
  const open = pdfAll.filter((p) => !p.song && !p.manual).length;
  $('#pdf-unlinked').textContent = open ? `${open} not linked to a song yet` : '';
  // not linked first, then those marked "no song", then by song
  const rank = (p) => (p.song ? 2 : p.manual ? 1 : 0);
  const key = (p) => (p.song ? `${p.song.artist} ${p.song.title}` : p.name).toLowerCase();
  pdfAll = [...pdfAll].sort((a, b) => rank(a) - rank(b) || key(a).localeCompare(key(b)));
  $('#pdf-views').innerHTML = song ? [`<label><input type="radio" name="view" value="" ${song.view ? '' : 'checked'}> Song text</label>`,
    ...(song.pdfs || []).map((p) => `<label><input type="radio" name="view" value="${p.id}" ${song.view === p.id ? 'checked' : ''}> ` +
      `PDF: ${esc(pdfLabel(p))}</label>`)].join('') : '';
  $('#pdf-list').innerHTML = pdfAll.map((p) => `<tr data-id="${p.id}"><td>${esc(p.name)}</td>` +
    `<td>${p.song ? esc(`${p.song.artist} – ${p.song.title}`) + (p.part ? ` <span class="none">(${esc(p.part)})</span>` : '')
                 : `<span class="none">${p.manual ? 'no song' : 'not matched'}</span>`}</td>` +
    `<td><button type="button" data-assign="${p.id}">${p.song ? 'Change…' : 'Choose song…'}</button></td></tr>`).join('') ||
    '<tr><td colspan="3" class="none">No PDFs found.</td></tr>';
}

$('#pdf-views').addEventListener('change', (e) => setView(e.target.value ? Number(e.target.value) : null));

$('#pdf-list').addEventListener('click', async (e) => {
  const id = Number(e.target.dataset.assign || e.target.dataset.save || e.target.dataset.nosong);
  if (!id) return;
  const pdfRow = pdfAll.find((p) => p.id === id);
  if (e.target.dataset.assign) {
    $('#pdf-list').querySelector('.pdf-assign')?.closest('tr').remove();
    e.target.closest('tr').insertAdjacentHTML('afterend', `<tr><td colspan="3"><div class="pdf-assign">
      <input id="pdf-q" placeholder="Search title or artist" value="${esc(pdfRow.song?.title || pdfRow.name.split(' - ')[1] || '')}">
      <select id="pdf-song"></select>
      <input id="pdf-part" placeholder="Part (e.g. Solo)" value="${esc(pdfRow.part || '')}">
      <button type="button" data-save="${id}">Save</button>
      <button type="button" data-nosong="${id}" title="Not a tab of any song">No song</button></div></td></tr>`);
    const search = async () => {
      const r = await api('songs/search?q=' + encodeURIComponent($('#pdf-q').value));
      $('#pdf-song').innerHTML = r.songs.map((s) => `<option value="${s.id}">${esc(s.artist)} – ${esc(s.title)}</option>`).join('');
      if (pdfRow.song) $('#pdf-song').value = pdfRow.song.id;
    };
    $('#pdf-q').addEventListener('input', () => { clearTimeout(search.timer); search.timer = setTimeout(search, 250); });
    search();
    $('#pdf-q').focus();
    return;
  }
  const songId = e.target.dataset.save ? Number($('#pdf-song').value) || null : null;
  if (e.target.dataset.save && !songId) { toast('Choose a song first'); return; }
  try {
    await api('pdfs/' + id, { method: 'PUT', body: { song_id: songId, part: e.target.dataset.save ? $('#pdf-part').value : '' } });
    await openPdfs();
    showTitle();
  } catch (err) { toast('Saving failed: ' + err.message); }
});

// --- takes (key r / R): recordings of the amp's output, made by the amp bridge -----

let takeId = null;          // the take being recorded

async function toggleRecord() {
  if (!song) { toast('Start a song first'); return; }
  try {
    if (takeId || amp?.recording) {
      await api(`takes/${takeId || amp.recording.take}/stop`, { method: 'POST' });
      toast('Take stopped: it appears under R in a few seconds');
      takeId = null;
    } else {
      takeId = (await api('takes', { method: 'POST', body: { song_id: song.id, t: player.currentTime || 0 } })).id;
      toast('Recording – r stops');
    }
  } catch (e) { toast(e.message, 6000); }
}

let takeList = [];
async function openTakes() {
  if (!song) { toast('Start a song first'); return; }
  try { takeList = (await api(`song/${song.id}/takes`)).takes; } catch (e) { toast('Loading failed: ' + e.message); return; }
  $('#takes-title').textContent = `Takes: ${song.title} – ${song.artist}`;
  const when = (t) => new Date(t * 1000).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' });
  $('#takes-list').innerHTML = takeList.map((t) => `<tr><td>${when(t.created)}</td>` +
    `<td class="t">${t.seconds ? fmt(t.seconds) : ''}</td><td class="t">from ${fmt(t.song_t)}</td><td>` +
    (t.state === 'ready' ? `<button type="button" data-take="${t.id}">▶</button>` : '<span class="none">recording…</span>') +
    `<button type="button" data-deltake="${t.id}" title="Delete this take">✕</button></td></tr>`).join('') ||
    '<tr><td class="hint">No takes of this song yet: r records one.</td></tr>';
  $('#take-record').textContent = takeId || amp?.recording ? '■ Stop take' : '● New take';
  if (!$('#d-takes').open) $('#d-takes').showModal();
}

$('#takes-list').addEventListener('click', async (e) => {
  // a take holds the whole mix (the song over Bluetooth and the guitar, measured
  // 2026-11-10), so it plays alone: the song is paused
  const { take, deltake } = e.target.dataset;
  const tp = $('#take-player');
  if (take) {
    tp.src = `api/takes/${take}`;
    player.pause();
    tp.play().catch((err) => toast('Cannot play: ' + err.message));
  } else if (deltake) {
    await api('takes/' + deltake, { method: 'DELETE' }).catch((err) => toast('Deleting failed: ' + err.message));
    openTakes();
  }
});
$('#take-record').addEventListener('click', async () => { await toggleRecord(); setTimeout(openTakes, 500); });
$('#d-takes').addEventListener('close', () => { $('#take-player').pause(); });

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
  a: openAudio, A: nextOutput, x: openPractice, b: markA, e: markB, o: toggleLoop,
  '0': () => { player.currentTime = 0; },
  '+': () => seek(1), '-': () => seek(-1),
  ArrowRight: () => seek(5), ArrowLeft: () => seek(-5),
  F11: toggleFull, '?': () => $('#d-help').showModal(), d: openDisplay,
  g: openAmp, G: learnTone,
  v: cycleView, V: openPdfs, r: toggleRecord, R: openTakes, PageDown: () => turn(1), PageUp: () => turn(-1),
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
  '#b-shuffle': shuffle, '#b-filter': openFilter, '#b-tags': openTags, '#b-audio': openAudio, '#b-practice': openPractice,
  '#b-full': toggleFull, '#b-help': () => $('#d-help').showModal(), '#b-display': openDisplay,
  '#b-tone': openAmp, '#amp': openAmp, '#b-pdf': openPdfs, '#b-takes': openTakes,
};
for (const [sel, fn] of Object.entries(buttons)) {
  $(sel).addEventListener('click', (e) => { e.currentTarget.blur(); fn(); });
}

// a dialog's button stays highlighted while the dialog is open
const DIALOG_BUTTONS = { 'd-filter': 'b-filter', 'd-tags': 'b-tags', 'd-audio': 'b-audio', 'd-practice': 'b-practice',
                         'd-pdf': 'b-pdf', 'd-takes': 'b-takes', 'd-display': 'b-display', 'd-amp': 'b-tone',
                         'd-help': 'b-help' };
const dialogWatch = new MutationObserver((changes) => {
  for (const c of changes) {
    document.getElementById(DIALOG_BUTTONS[c.target.id])?.classList.toggle('open', c.target.open);
    // closed: the focus mustn't stay on a field in it, or the keys (Space, o, ...) go nowhere
    if (!c.target.open && c.target.contains(document.activeElement)) document.activeElement.blur();
  }
});
for (const id of Object.keys(DIALOG_BUTTONS)) dialogWatch.observe(document.getElementById(id), { attributes: true, attributeFilter: ['open'] });

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
  $('#help-version').textContent = state.version;
  practiceOffered = !!state.practice;
  $('#b-practice').hidden = !practiceOffered;
  $('#version').textContent = `Logged in as ${state.user}`;
  if (/^https?:\/\//.test(state.source || '')) $('#help-source').href = state.source;
  $('#help-tv-url').textContent = new URL('d/tv/', location.href).href;
  followAmp();
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
