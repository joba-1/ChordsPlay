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
  player.src = 'api/audio/' + row.id;
  song = await api('song/' + row.id);
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

player.addEventListener('play', () => { $('#b-pause').textContent = '⏸'; keepAwake(); pushNow(); });
player.addEventListener('pause', () => { $('#b-pause').textContent = '▶'; pushNow(); });
player.addEventListener('seeked', pushNow);

// tell the server what plays and where: it remembers the song for every device,
// and displays (key d) follow it
function pushNow() {
  if (!song) return;
  api('now', { method: 'PUT', body: { id: song.id, pos, t: player.currentTime || 0, playing: !player.paused,
                                     pdf: song.view || null, page: pdfPage } })
    .catch(() => {});
}
setInterval(() => { if (!player.paused) pushNow(); }, 15000);   // displays correct their clock
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
  events.addEventListener('amp', (e) => {
    try { amp = JSON.parse(e.data); } catch (err) { return; }
    ampSeen = Date.now();
    showAmp();
  });
}

function presetNames() { return (amp?.state?.presets || []).map((p) => p.name || ''); }

// a cue's level: null plays the preset as stored, a number level-matched plus that many dB
function levelText(level) {
  return level === null || level === undefined ? '' : level === 0 ? ' · matched' : ` · ${level > 0 ? '+' : ''}${level} dB`;
}

function chosen(quiet = false) {
  const slot = Number($('#amp-presets').value), tone = Number($('#amp-tones').value);
  const level = $('#amp-level').value === '' ? null : Number($('#amp-level').value);
  const params = slot && slot === knobSlot && Object.keys(knobParams).length ? { ...knobParams } : null;
  if (slot) return { slot, level, params };
  if (tone) return { tone_id: tone };
  if (!quiet) toast('Choose a preset or a tone first');
  return null;
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

function renderAmp() {
  const names = presetNames();
  const st = amp?.state || {};
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
  const cues = cueList?.cues || [];
  $('#amp-cues').innerHTML = cues.length ? cues.map((c) => {
    const what = c.tone_id ? `tone ${esc(c.tone || '(deleted)')}`
      : `${c.slot} ${esc(c.name || '')}${levelText(c.level)}${c.params ? ' · tuned' : ''}`;
    const now = !c.tone_id && names[c.slot - 1] && c.name && names[c.slot - 1].trim() !== c.name.trim()
      ? ` <span class="err">slot is now "${esc(names[c.slot - 1])}": save it again</span>` : '';
    return `<tr${c.start === cueSel ? ' class="sel"' : ''}><td class="t">${fmtMs(c.start)}</td><td>${what}${now}</td>` +
      `<td><button type="button" data-play="${c.start}" title="Play from before it">▶</button>` +
      `<button type="button" data-edit="${c.start}" title="Edit: its position and sound go into the editor below">✎</button>` +
      (c.tone_id ? '' : `<button type="button" data-store="${c.start}" title="Store the current choice in this cue">⤓</button>`) +
      `<button type="button" data-del="${c.start}" title="Delete this cue">✕</button></td></tr>`;
  }).join('') : '<tr><td class="hint">No cues for this song yet.</td></tr>';
  const base = cueList?.artist_preset;
  $('#amp-artist').innerHTML = base
    ? `Artist preset (${esc(cueList.artist)}): ${base.slot} ${esc(base.name || '')}${levelText(base.level)} ` +
      '<button type="button" id="amp-artist-del" title="Delete the artist preset">✕</button>'
    : `No artist preset for ${esc(cueList?.artist || '')}.`;
  const sg = cueList?.suggestion;
  const slotName = (slot) => slot ? `${slot} ${names[slot - 1] || ''}`.trim() : 'no preset';
  $('#amp-suggest').innerHTML = !sg ? 'No suggestion yet (made in the background for new songs).' :
    `Suggested sound: <b>${esc(sg.category.replace('_', ' '))}</b> → ${esc(slotName(sg.slot))}${levelText(sg.level)} · ` +
    (sg.rejected ? 'rejected, not played' : !sg.known ? 'a guess: not played'
      : suggestAuto ? 'played when there is no other preset' : 'shown only (automatic suggestions are off, see below)') +
    (sg.genres?.length ? ` <span class="none">(${esc(sg.genres.join(', '))})</span>` : '') +
    (sg.slot ? '<button type="button" id="amp-suggest-use" title="Store it as this song\'s start preset">Use from start</button>' : '') +
    `<button type="button" id="amp-suggest-reject">${sg.rejected ? 'Allow again' : 'Reject'}</button>`;
  // rebuilt only when not in use: the dialog redraws every few seconds with the amp's state
  if (!$('#amp-sounds').contains(document.activeElement)) $('#amp-sounds').innerHTML = ampSounds.map((s) => `<tr><td>${esc(s.category.replace('_', ' '))}</td><td>` +
    `<select data-sound="${s.category}"><option value="">no preset</option>` +
    Array.from({ length: 100 }, (_, i) => `<option value="${i + 1}" ${s.slot === i + 1 ? 'selected' : ''}>${i + 1} ${esc(names[i] || '')}</option>`).join('') +
    '</select></td><td>' +
    `<select data-sound-level="${s.category}"><option value="">original</option>` +
    Array.from({ length: 13 }, (_, i) => i - 6).map((v) => `<option value="${v}" ${s.level === v ? 'selected' : ''}>` +
      `${v === 0 ? 'matched' : (v > 0 ? '+' : '') + v + ' dB'}</option>`).join('') + '</select></td></tr>').join('');
  // the level needs a measured preset; else it plays the preset's own volume plus the offset
  const pick = Number($('#amp-presets').value);
  $('#amp-level-hint').textContent = pick && $('#amp-level').value !== '' && !(cueList?.measured || []).includes(pick)
    ? 'not measured: its own volume plus this' : '';
  fillList($('#amp-presets'), Array.from({ length: 100 }, (_, i) =>
    `<option value="${i + 1}">${i + 1} ${esc(names[i] || '')}${st.preset === i + 1 && !st.audition ? ' ◀' : ''}</option>`).join(''));
  fillList($('#amp-tones'), ampTones.map((t) =>
    `<option value="${t.id}">${esc(t.name)} (${t.base_slot} ${esc(t.base_name || '')}, ` +
    `${t.level_db >= 0 ? '+' : ''}${t.level_db} dB)</option>`).join(''));
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
  if (!song) { toast('Start a song first'); return; }
  try {
    const [state, cues] = await Promise.all([api('amp'), api(`song/${song.id}/cues`)]);
    if (state.amp) { amp = state.amp; ampSeen = state.amp.online ? Date.now() : 0; }
    ampTones = state.tones;
    ampSounds = state.sounds;
    suggestAuto = state.suggest_auto;
    audioAuto = state.audio_auto;
    $('#amp-suggest-auto').checked = suggestAuto;
    cueList = cues;
  } catch (e) { toast('Loading failed: ' + e.message); return; }
  $('#amp-title').textContent = `Guitar tone: ${song.title} – ${song.artist}`;
  $('#amp-presets').value = '';
  $('#amp-tones').value = '';
  $('#amp-bridge-box').hidden = true;
  cueSel = null;
  setPos((player.currentTime || 0) * 1000, false);
  renderAmp();
  $('#d-amp').showModal();
  loadWave();
  startWaveTick();
}

async function saveCue(start) {
  const what = chosen();
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
$('#amp-pos-new').addEventListener('click', () => { cueSel = null; renderAmp(); });
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
  const what = chosen(true) || {};
  try {
    cueList = await api(`song/${song.id}/cues/${cueSel}`, { method: 'PUT', body: { ...what, to: cuePos } });
    toast(cueSel === cuePos ? `Cue at ${fmtMs(cuePos)} updated` : `Cue moved to ${fmtMs(cuePos)}`);
    cueSel = cuePos;
    renderAmp();
  } catch (e) { toast('Saving failed: ' + e.message); }
});

// one of the two lists: choosing in one clears the other
$('#amp-presets').addEventListener('change', () => { $('#amp-tones').value = ''; renderAmp(); loadKnobs(); });
$('#amp-level').addEventListener('change', () => renderAmp());
$('#amp-tones').addEventListener('change', () => { $('#amp-presets').value = ''; });
$('#amp-presets').addEventListener('dblclick', () => $('#amp-try').click());
$('#amp-tones').addEventListener('dblclick', () => $('#amp-try').click());

// --- knobs: adjust the chosen preset while the song plays, stored with the cue

let knobSlot = null;        // the preset the knobs belong to
let knobParams = {};        // only what differs from the preset ('reverb.wetlvl': 0.4, 'delay.bypass': true)
let knobList = [];

async function loadKnobs(tries = 0) {
  const slot = Number($('#amp-presets').value);
  if (!$('#amp-knobs-box').open) return;
  if (!slot) { $('#amp-knobs').innerHTML = ''; $('#amp-knobs-hint').textContent = 'Choose a preset first.'; return; }
  if (slot !== knobSlot) { knobSlot = slot; knobParams = {}; }
  let r;
  try {
    const res = await fetch('api/amp/preset/' + slot);
    r = await res.json();
    if (res.status === 202) {
      $('#amp-knobs').innerHTML = '';
      $('#amp-knobs-hint').textContent = r.bridge ? 'Reading the preset from the amp…'
        : 'This sound is not known yet, and amp control isn\'t running to read it from the Mustang.';
      if (r.bridge && tries < 12 && Number($('#amp-presets').value) === slot) setTimeout(() => loadKnobs(tries + 1), 1500);
      return;
    }
  } catch (e) { $('#amp-knobs-hint').textContent = 'Loading failed: ' + e.message; return; }
  if (Number($('#amp-presets').value) !== slot) return;
  knobList = r.knobs;
  $('#amp-knobs-hint').textContent = `${slot} ${r.name}: a changed knob is bold.` +
    ($('#amp-level').value === '' ? ' With "original" the preset keeps its own volume.' : '');
  renderKnobs();
}

function renderKnobs() {
  $('#amp-knobs').innerHTML = knobList.map((k) => {
    const changed = k.key in knobParams;
    if (k.kind === 'switch') {
      const on = changed ? !knobParams[k.key] : k.value;     // stored as "bypass"
      return `<label class="switch${changed ? ' changed' : ''}"><input type="checkbox" data-knob="${k.key}" ${on ? 'checked' : ''}>${esc(k.label)}</label>`;
    }
    const v = changed ? knobParams[k.key] : k.value;
    return `<label${changed ? ' class="changed"' : ''}>${esc(k.label)}<input type="range" min="0" max="1" step="0.01" ` +
      `value="${v}" data-knob="${k.key}"><output>${v.toFixed(2)}</output></label>`;
  }).join('') || '<span class="hint">This preset has no adjustable knobs.</span>';
}

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
  if ($('#amp-live').checked) { clearTimeout(knobTimer); knobTimer = setTimeout(() => $('#amp-try').click(), 250); }
});
$('#amp-knobs-box').addEventListener('toggle', () => loadKnobs());
$('#amp-knobs-reset').addEventListener('click', () => {
  knobParams = {};
  renderKnobs();
  if ($('#amp-live').checked) $('#amp-try').click();
});

$('#amp-try').addEventListener('click', async () => {
  const what = chosen();
  if (!what) return;
  try {
    const r = await api('amp/try', { method: 'POST', body: what });
    if (!r.bridge) toast('Amp control isn\'t running: the Mustang was not switched');
  } catch (e) { toast('Failed: ' + e.message); }
});
$('#amp-start').addEventListener('click', () => saveCue(0));
$('#amp-here').addEventListener('click', () => saveCue(cuePos));
function soundName(what) {
  const names = presetNames();
  const base = names[what.slot - 1] || 'Slot ' + what.slot;
  return `${base}${what.level === null ? '' : what.level === 0 ? ' matched' : ` ${what.level > 0 ? '+' : ''}${what.level}`}` +
    (what.params ? ' tuned' : '');
}

$('#amp-tone-save').addEventListener('click', async () => {
  const what = chosen();
  if (!what || !what.slot) { if (what) toast('Choose a preset first'); return; }
  const name = prompt('Save this sound as a tone named', song ? `${song.title} – ${soundName(what)}` : soundName(what));
  if (!name) return;
  try {
    const r = await api('tones', { method: 'POST', body: { ...what, name } });
    ampTones = r.tones;
    renderAmp();
    toast(`Tone "${name}" saved: it is in the Tones list now`);
  } catch (e) { toast('Saving failed: ' + e.message); }
});

$('#amp-write').addEventListener('click', async () => {
  const what = chosen();
  if (!what || !what.slot) { if (what) toast('Choose a preset first'); return; }
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
  const name = prompt('Name on the amp (at most 16 characters)', soundName(what).slice(0, 16));
  if (!name) return;
  if (name.trim().length > 16) { toast('The amp keeps names of at most 16 characters'); return; }
  try {
    await api('amp/write', { method: 'POST', body: { ...what, target, name } });
    toast(`Writing slot ${target}…`);
  } catch (e) { toast(e.message, 6000); }
});

$('#amp-artist-save').addEventListener('click', async () => {
  const slot = Number($('#amp-presets').value);
  if (!slot) { toast('Choose a preset (tones are per song)'); return; }
  const { level, params } = chosen();
  try {
    await api('artist-preset', { method: 'PUT', body: { artist: song.artist, slot, level, params } });
    cueList = await api(`song/${song.id}/cues`);
    renderAmp();
    toast(`Preset ${slot} for every song of ${song.artist}`);
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
        $('#amp-presets').value = '';
        $('#amp-tones').value = c.tone_id;
      } else {
        $('#amp-presets').value = c.slot;
        $('#amp-tones').value = '';
        $('#amp-level').value = c.level === null ? '' : String(c.level);
        knobSlot = c.slot;
        knobParams = { ...(c.params || {}) };
        $('#amp-knobs-box').open = true;
      }
      renderAmp();
      if (!c.tone_id) loadKnobs();
      toast(`Editing the cue at ${fmtMs(c.start)}: change position or sound, then "Update cue"`);
    } else if (storeAt !== undefined) {
      const what = chosen();
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
    cueList = await api(`song/${song.id}/cues`);
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
  $('#take-record').textContent = takeId || amp?.recording ? '■ Stop' : '● Record';
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
  a: openAudio, A: nextOutput,
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
  '#b-shuffle': shuffle, '#b-filter': openFilter, '#b-tags': openTags, '#b-audio': openAudio,
  '#b-full': toggleFull, '#b-help': () => $('#d-help').showModal(), '#b-display': openDisplay,
  '#b-tone': openAmp, '#amp': openAmp, '#b-pdf': openPdfs, '#b-takes': openTakes,
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
  $('#help-version').textContent = state.version;
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
