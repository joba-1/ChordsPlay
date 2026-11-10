// Show a song text as large as the box allows, in 1..MAX_COLUMNS columns.
// Shared by the controller page (app.js) and the display page (display.js).
'use strict';

const ChordsFit = (() => {
  const LINE_HEIGHT = 1.15;   // must match #sheet pre in style.css
  const MAX_COLUMNS = 4;
  const COLUMN_GAP = 3;       // characters between columns

  // width of one character of the sheet font, per pixel of font size
  let charRatio = null;
  function measureChar(box) {
    const probe = document.createElement('pre');
    probe.style.cssText = 'position:absolute;visibility:hidden;font-size:100px;margin:0';
    probe.textContent = 'M'.repeat(50);
    box.appendChild(probe);
    const w = probe.getBoundingClientRect().width;
    probe.remove();
    return w / 50 / 100;
  }

  function trimBlank(lines) {
    let a = 0, b = lines.length;
    while (a < b && lines[a].trim() === '') a++;
    while (b > a && lines[b - 1].trim() === '') b--;
    return lines.slice(a, b);
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
        if (cut - d > start && lines[cut - d - 1].trim() === '') { cut -= d; break; }
        if (cut + d < lines.length && lines[cut + d - 1].trim() === '') { cut += d; break; }
      }
      cols.push(lines.slice(start, cut));
      start = cut;
      while (start < lines.length && lines[start].trim() === '') start++;  // no blank line on top
    }
    if (start < lines.length) cols.push(lines.slice(start));
    return cols.map(trimBlank).filter((col) => col.length);
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

  function message(box, html) {
    box.textContent = '';
    box.insertAdjacentHTML('beforeend', `<div class="empty">${html}</div>`);
  }

  // song: {text, wrapped} from the server; wrapped has long comment lines wrapped
  function render(box, song, emptyHtml) {
    box.textContent = '';
    if (!song) { message(box, emptyHtml); return; }
    if (!song.text.trim()) { message(box, 'No chords for this song yet.'); return; }
    if (charRatio === null) charRatio = measureChar(box);
    const style = getComputedStyle(box);
    const width = box.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight) - 2;
    const height = box.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom) - 2;
    if (width <= 0 || height <= 0) return;
    // the original text or the one with comments wrapped: whichever fits bigger
    let best = null;
    for (const text of new Set([song.text, song.wrapped || song.text])) {
      const layout = bestLayout(trimBlank(text.split('\n')), width, height);
      if (!best || layout.size > best.size * 1.02) best = layout;
    }
    const size = Math.max(4, Math.floor(best.size * 10) / 10);
    box.style.gap = (COLUMN_GAP * charRatio * size) + 'px';
    for (const col of best.cols) {
      const pre = document.createElement('pre');
      pre.style.fontSize = size + 'px';
      pre.textContent = col.join('\n');
      box.appendChild(pre);
    }
  }

  function esc(s) {
    return String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }

  return { render, esc };
})();
