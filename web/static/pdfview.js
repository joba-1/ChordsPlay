// SPDX-FileCopyrightText: 2016-2026 joba1
// SPDX-License-Identifier: AGPL-3.0-or-later
// Show a PDF tab as large as the box allows: white margins cropped, one page or two
// side by side (two when they are nearly as large as one), turned in steps of what
// is shown. Shared by the controller page (app.js) and the display page (display.js).
// Needs pdf.js (static/vendor/pdfjs, legacy build: also old TV browsers).
'use strict';

const PdfView = (() => {
  const TWO_PAGES = 0.8;      // two pages side by side if each is at least 80 % of one alone
  const GAP = 12;             // px between two pages
  const PROBE_SCALE = 0.5;    // render scale to find the margins
  const INK = 235;            // a pixel darker than this (0..255) is content
  const PAD = 6;              // keep this many PDF points around the content
  const EDGE_ZONE = 0.06;     // a print header/footer starts within 6 % of the page's edge,
  const EDGE_BAND = 0.025;    // is at most 2.5 % of its height
  const EDGE_GAP = 0.01;      // and has at least 1 % blank below (above) it

  pdfjsLib.GlobalWorkerOptions.workerSrc = document.querySelector('script[src$="pdf.min.js"]').src
    .replace('pdf.min.js', 'pdf.worker.min.js');

  const docs = new Map();     // url -> Promise of {doc, crops: [page -> Promise of crop]}

  function open(url) {
    if (!docs.has(url)) {
      docs.set(url, pdfjsLib.getDocument(url).promise.then((doc) => ({ doc, crops: [] })));
      if (docs.size > 4) docs.delete(docs.keys().next().value);
    }
    return docs.get(url);
  }

  // the content box of a page in PDF points: {x, y, w, h}
  function crop(entry, n) {
    if (!entry.crops[n]) {
      entry.crops[n] = entry.doc.getPage(n).then(async (page) => {
        const vp = page.getViewport({ scale: PROBE_SCALE });
        const c = document.createElement('canvas');
        c.width = Math.ceil(vp.width); c.height = Math.ceil(vp.height);
        const ctx = c.getContext('2d', { willReadFrequently: true });
        ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, c.width, c.height);
        await page.render({ canvasContext: ctx, viewport: vp }).promise;
        const px = ctx.getImageData(0, 0, c.width, c.height).data;
        const ink = (x, y) => { const i = (y * c.width + x) * 4; return px[i] < INK || px[i + 1] < INK || px[i + 2] < INK; };
        const rows = [];
        for (let y = 0; y < c.height; y++) {
          let any = false;
          for (let x = 0; x < c.width && !any; x++) any = ink(x, y);
          rows.push(any);
        }
        let top = rows.indexOf(true), bottom = rows.lastIndexOf(true);
        // a browser's print header/footer (date and title, URL and page number): a thin
        // band at the very edge, apart from the content; it would keep all the margin
        const band = (from, step) => {
          let y = from; while (y >= 0 && y < c.height && rows[y]) y += step;          // the band
          const end = y; while (y >= 0 && y < c.height && !rows[y]) y += step;        // the gap after it
          const thin = Math.abs(end - from) < EDGE_BAND * c.height, gap = Math.abs(y - end) > EDGE_GAP * c.height;
          return thin && gap && y >= 0 && y < c.height ? y : from;
        };
        if (top >= 0 && top < EDGE_ZONE * c.height) top = band(top, 1);
        if (bottom >= 0 && bottom > (1 - EDGE_ZONE) * c.height) bottom = band(bottom, -1);
        let x0 = c.width, y0 = top, x1 = -1, y1 = bottom;
        for (let y = Math.max(0, top); y <= bottom; y++) {
          for (let x = 0; x < c.width; x++) {
            if (ink(x, y)) { if (x < x0) x0 = x; if (x > x1) x1 = x; }
          }
        }
        const full = page.getViewport({ scale: 1 });
        if (x1 < 0) return { x: 0, y: 0, w: full.width, h: full.height, page };   // empty page
        const s = 1 / PROBE_SCALE;
        const x = Math.max(0, x0 * s - PAD), y = Math.max(0, y0 * s - PAD);
        return { x, y, w: Math.min(full.width, x1 * s + PAD) - x, h: Math.min(full.height, y1 * s + PAD) - y, page };
      });
    }
    return entry.crops[n];
  }

  // how many pages fit side by side from `first`, and at which scale
  function layout(box, crops) {
    const one = Math.min(box.w / crops[0].w, box.h / crops[0].h);
    if (crops.length > 1) {
      const two = Math.min((box.w - GAP) / (crops[0].w + crops[1].w), box.h / Math.max(crops[0].h, crops[1].h));
      if (two >= TWO_PAGES * one) return { count: 2, scale: two };
    }
    return { count: 1, scale: one };
  }

  async function drawPage(c, scale) {
    const ratio = window.devicePixelRatio || 1;
    const vp = c.page.getViewport({ scale: scale * ratio });
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(c.w * scale * ratio);
    canvas.height = Math.round(c.h * scale * ratio);
    canvas.style.width = Math.round(c.w * scale) + 'px';
    canvas.style.height = Math.round(c.h * scale) + 'px';
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.translate(-c.x * scale * ratio, -c.y * scale * ratio);
    await c.page.render({ canvasContext: ctx, viewport: vp }).promise;
    return canvas;
  }

  // Render `url` from page `first` into box. Returns {pages, first, count}: the
  // page count, and which pages are shown (for turning and for the display).
  let generation = 0;
  async function render(box, url, first = 1) {
    const mine = ++generation;
    const entry = await open(url);
    const pages = entry.doc.numPages;
    first = Math.min(Math.max(1, first), pages);
    const crops = [await crop(entry, first)];
    if (first < pages) crops.push(await crop(entry, first + 1));
    const cs = getComputedStyle(box);
    const size = { w: box.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight),
                   h: box.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom) };
    const { count, scale } = layout(size, crops);
    const canvases = await Promise.all(crops.slice(0, count).map((c) => drawPage(c, scale)));
    if (mine !== generation) return null;       // a newer render started meanwhile
    const row = document.createElement('div');
    row.className = 'pdf-pages';
    row.style.gap = GAP + 'px';
    canvases.forEach((c) => row.appendChild(c));
    box.replaceChildren(row);
    return { pages, first, count };
  }

  return { render };
})();
