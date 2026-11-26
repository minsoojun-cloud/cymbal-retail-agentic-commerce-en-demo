/* Floor map (CONTRACT v9): fixtures (gondola / wall / endcap / multideck / reach_in / chest / flat_table / deli_table /
   flower / counter) with zone colours, fixture-id badges (level-of-detail by zoom), target highlight + pulse, numbered
   pins, animated dashed route from the entrance, auto-zoom to the target fixture, pinch / wheel zoom + pan.
   Layout of the view: product photo + name pinned at the top, map, front-elevation diagram (tiers × 30 cm slots with the
   product's slot(s) highlighted), sticky bottom bar "<- Back" (+ Prev / Next in multi mode). Reads v2 `fixtures` or v1 `gondolas`. */
(function () {
  'use strict';
  const { esc, fetchJSON, postJSON, drugBadges, img } = window.SN;
  const NS = 'http://www.w3.org/2000/svg';
  const SIDE = { N: 'Facing north', S: 'Facing south', E: 'Facing east', W: 'Facing west' };
  const TYPE_JA = { gondola: 'Gondola', wall: 'Wall shelf', endcap: 'Endcap', multideck: 'Refrigerated multideck', reach_in: 'Freezer reach-in',
    chest: 'Chest freezer', flat_table: 'Display table', deli_table: 'Deli & bakery table', flower: 'Flowers', counter: 'Counter' };
  const NUM = ['①', '②', '③', '④', '⑤', '⑥', '⑦', '⑧', '⑨', '⑩'];
  const layouts = {};
  const grids = {};
  let M = null;

  async function getLayout(storeId) {
    if (!layouts[storeId]) layouts[storeId] = await fetchJSON('/api/stores/' + encodeURIComponent(storeId) + '/layout');
    return layouts[storeId];
  }
  async function getGrid(storeId, fid) {
    const k = storeId + '/' + fid;
    if (!grids[k]) grids[k] = fetchJSON('/api/stores/' + encodeURIComponent(storeId) + '/fixtures/' + encodeURIComponent(fid)).catch(() => null);
    return grids[k];
  }
  function fixturesOf(lay) {
    if (lay.fixtures) return lay.fixtures.filter((f) => f.id !== 'RX');
    return (lay.gondolas || []).map((g) => Object.assign({ type: 'gondola', face: (g.faces || ['S'])[0] }, g));
  }
  function fixtureById(lay, id) {
    if (id === 'RX' && lay.counter) return Object.assign({ type: 'counter', tiers: 1, slots_per_tier: 1 }, lay.counter, { id: 'RX' });
    return fixturesOf(lay).find((f) => f.id === id) || null;
  }
  function shelfFid(sh) { return sh ? (sh.fixture_id || sh.gondola_id) : null; }

  function legs(route) {
    const pts = route.polyline || [];
    const out = [];
    let prev = 0;
    (route.stops || []).forEach((s) => {
      let idx = -1;
      for (let i = prev; i < pts.length; i++) { if (Math.abs(pts[i][0] - s.x) < 0.02 && Math.abs(pts[i][1] - s.y) < 0.02) { idx = i; break; } }
      if (idx < 0) idx = prev;
      out.push(pts.slice(prev, idx + 1));
      prev = idx;
    });
    out.push(pts.slice(prev));
    return out;
  }
  const pathD = (pts) => pts.map((p, i) => (i ? 'L' : 'M') + p[0] + ' ' + p[1]).join(' ');
  const textW = (t, fs) => [...String(t)].reduce((w, c) => w + (/[\x00-\x7f]/.test(c) ? fs * 0.6 : fs), 0);
  const isReg = (s) => /^(registers?|checkout)$/i.test(String(s).trim()) || s === '\u30ec\u30b8';  // register zone (EN or legacy JP data)
  const hit = (a, b) => a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;

  function buildSVG(lay, st, base) {
    const W = lay.width, H = lay.height;
    // label sizes target the auto-zoom level (≈14 m across the screen); overview items scale with the fit size
    const kf = Math.max(1, (base * (W + 1.2)) / (14 * base));
    const u = Math.max(1, 9 / (base * kf));
    const ov = (px) => px / base;                           // metres that render as `px` at the overview (fit) zoom
    const targets = new Set(st.items.map((it) => shelfFid(it.shelf)).filter(Boolean));
    const cur = st.items[st.step] || null;
    const curG = cur ? shelfFid(cur.shelf) : null;
    const fx = fixturesOf(lay);
    let h = '<svg class="floor" xmlns="' + NS + '" viewBox="-0.6 -0.6 ' + (W + 1.2) + ' ' + (H + 1.6) + '" role="img" aria-label="Store map">';
    h += '<rect x="0" y="0" width="' + W + '" height="' + H + '" rx=".3" class="floor-bg"/>';
    // zones
    (lay.zones || []).forEach((z) => {
      const [x, y, w, hh] = z.rect;
      h += '<rect class="zone-r" x="' + x + '" y="' + y + '" width="' + w + '" height="' + hh + '" fill="' + esc(z.color || '#f1f3f4') + '"/>';
    });
    // fixtures + badges (badge rects reserved for label collision)
    const occupied = [];
    const fs = 0.5 * Math.min(u, 1.6);
    let fixSVG = '', badgeSVG = '', tgtSVG = '';
    const bodies = {};
    fx.forEach((f) => { const b = f.body_id || f.id; (bodies[b] = bodies[b] || []).push(f.id); });
    const pinOf = {};
    st.items.forEach((it) => { if (it.pin && it.shelf) pinOf[shelfFid(it.shelf)] = it.pin; });
    fx.forEach((f) => {
      const on = targets.has(f.id), isCur = f.id === curG;
      const sib = bodies[f.body_id || f.id] || [f.id], si = sib.indexOf(f.id), vertical = f.h >= f.w;
      const cls = 'fx fx--' + esc(f.type || 'gondola') + (on ? ' is-target' : '') + (isCur ? ' is-cur' : '');
      fixSVG += '<rect class="' + cls + '" data-f="' + esc(f.id) + '" x="' + f.x + '" y="' + f.y + '" width="' + f.w + '" height="' + f.h + '" rx=".06"/>';
      if (isCur) fixSVG += '<rect class="pulse" x="' + f.x + '" y="' + f.y + '" width="' + f.w + '" height="' + f.h + '" rx=".1"/>';
      occupied.push({ x: f.x, y: f.y, w: f.w, h: f.h });
      let cx = f.x + f.w / 2, cy = f.y + f.h / 2;
      if (on) {
        const tfs = 0.62 * u, bw = textW(f.id, tfs) + 0.4 * u, bh = tfs * 1.5;
        const pin = pinOf[f.id];       // keep the target badge away from its pin (opposite end of the fixture)
        if (pin && vertical) cy = pin.y > cy ? f.y - bh * 0.65 : f.y + f.h + bh * 0.65;      // just beyond the far end
        else if (pin) cx = pin.x > cx ? f.x - bw * 0.6 : f.x + f.w + bw * 0.6;
        tgtSVG += '<g class="tbadge' + (isCur ? ' is-cur' : '') + '"><rect x="' + (cx - bw / 2) + '" y="' + (cy - bh / 2) + '" width="' + bw + '" height="' + bh + '" rx="' + bh / 2 + '"/>' +
          '<text x="' + cx + '" y="' + cy + '" style="font-size:' + tfs + 'px">' + esc(f.id) + '</text></g>';
        occupied.push({ x: cx - bw / 2, y: cy - bh / 2, w: bw, h: bh });
      } else {
        const bw = textW(f.id, fs) + 0.2, bh = fs * 1.35;
        if (sib.length > 1) {          // double-sided body: stagger the two face badges along the body
          const off = (si - (sib.length - 1) / 2) * (vertical ? bh * 1.15 : bw * 1.1);
          if (vertical) cy += off; else cx += off;
        }
        badgeSVG += '<g class="badge"><rect x="' + (cx - bw / 2) + '" y="' + (cy - bh / 2) + '" width="' + bw + '" height="' + bh + '" rx="' + bh / 2 + '"/>' +
          '<text x="' + cx + '" y="' + cy + '" style="font-size:' + fs + 'px">' + esc(f.id) + '</text></g>';
      }
    });
    h += fixSVG;
    ['register', 'counter'].forEach((k) => {
      const r = lay[k];
      if (!r) return;
      const on = k === 'counter' && targets.has('RX');
      h += '<g class="fixed' + (k === 'counter' ? ' rx' : '') + (on ? ' is-target' : '') + '"><rect x="' + r.x + '" y="' + r.y + '" width="' + r.w + '" height="' + r.h + '" rx=".15"/>' +
        '<text x="' + (r.x + r.w / 2) + '" y="' + (r.y + r.h / 2) + '" style="font-size:' + Math.min(0.45 * u, r.h * 0.6) + 'px">' + esc(k === 'register' ? 'Registers' : (r.label || 'Pharmacy counter')) + '</text>' +
        (on && curG === 'RX' ? '<rect class="pulse" x="' + r.x + '" y="' + r.y + '" width="' + r.w + '" height="' + r.h + '" rx=".15"/>' : '') + '</g>';
      occupied.push({ x: r.x, y: r.y, w: r.w, h: r.h });
    });
    // overview zone labels (shown when zoomed out): zone centres, collisions only among themselves
    const ofs = ov(10), oplaced = [];
    let zlo = '';
    (lay.zones || []).forEach((z) => {
      if (!z.label || isReg(z.label)) return;
      const lbl = String(z.label).split(' (')[0];              // overview: short form ("Produce (Cut Vegetables…)" → "Produce")
      const [x, y, w, hh] = z.rect, tw = textW(lbl, ofs) * 1.08, th = ofs * 1.2;
      const bx = Math.max(0.05, Math.min(W - tw - 0.05, x + (w - tw) / 2));   // keep inside the floor (no clipping)
      const box = { x: bx, y: y + (hh - th) / 2, w: tw, h: th };
      if (oplaced.some((o) => hit(o, box))) return;
      oplaced.push(box);
      zlo += '<text class="zlbl zl-low" x="' + box.x + '" y="' + (box.y + ofs) + '" style="font-size:' + ofs + 'px">' + esc(lbl) + '</text>';
    });
    // detail zone labels: free corner / just outside, skipped on collision
    const zfs = 0.42 * u, placed = [];
    let zl = '';
    (lay.zones || []).forEach((z) => {
      if (!z.label || isReg(z.label) || (lay.counter && z.label === lay.counter.label)) return;
      const [x, y, w, hh] = z.rect, tw = textW(z.label, zfs), th = zfs * 1.2, mid = x + (w - tw) / 2;
      const cands = [[x + 0.1, y + 0.1], [x + 0.1, y + hh - th - 0.1], [x + w - tw - 0.1, y + 0.1], [mid, y + hh + 0.06], [mid, y - th - 0.06], [mid, y + (hh - th) / 2]];
      for (const [cx, cy] of cands) {
        const box = { x: cx, y: cy, w: tw, h: th };
        if (cx < 0.05 || cy < 0.05 || cx + tw > W - 0.05 || cy + th > H - 0.05) continue;
        if (occupied.some((o) => hit(o, box)) || placed.some((o) => hit(o, box))) continue;
        placed.push(box);
        zl += '<text class="zlbl" x="' + cx + '" y="' + (cy + zfs) + '" style="font-size:' + zfs + 'px">' + esc(z.label) + '</text>';
        break;
      }
    });
    h += '<g class="zlbls zl-high">' + zl + '</g><g class="zlbls-low">' + zlo + '</g><g class="badges">' + badgeSVG + '</g>';
    // route
    if (st.route && (st.route.polyline || []).length > 1) {
      const rw = Math.max(0.16 * u, ov(1.6));
      h += '<path class="route-bg" style="stroke-width:' + rw * 2.3 + 'px" d="' + pathD(st.route.polyline) + '"/>';
      st.legs.forEach((pts, i) => {
        if (pts.length < 2) return;
        const active = st.multi ? i === st.step : i === 0;
        h += '<path class="route' + (active ? '' : ' is-faint') + '" style="stroke-width:' + rw + 'px;stroke-dasharray:' + rw * 2.2 + ' ' + rw * 1.9 + '" d="' + pathD(pts) + '"/>';
      });
    }
    h += tgtSVG;
    if (lay.entrance) {
      const e = lay.entrance, ey = Math.min(e.y, H - 0.3);
      const yr = Math.max(0.28 * u, ov(3));
      h += '<g class="you"><circle class="halo" cx="' + e.x + '" cy="' + ey + '" r="' + yr * 2.5 + '"/><circle class="dot" cx="' + e.x + '" cy="' + ey + '" r="' + yr + '"/></g>' +
        '<text class="lbl" x="' + e.x + '" y="' + (H + 0.45 + 0.4 * u) + '" style="font-size:' + 0.44 * u + 'px">Entrance' + (window.SNC.state.inStore ? ' (you are here)' : '') + '</text>';
    }
    st.items.forEach((it, i) => {
      if (!it.pin) return;
      const cls = st.multi ? (i === st.step ? ' is-cur' : (i < st.step ? ' is-dim' : '')) : ' is-cur';
      const r = Math.max(0.42 * u, ov(4));
      h += '<g class="pin' + cls + '"><circle cx="' + it.pin.x + '" cy="' + it.pin.y + '" r="' + r + '"/><text x="' + it.pin.x + '" y="' + (it.pin.y + 0.02) + '" style="font-size:' + r * 1.2 + 'px">' +
        (st.multi ? (it.no || i + 1) : '★') + '</text></g>';
    });
    return h + '</svg>';
  }

  // ---- top product bar / elevation / footer
  function productBar(st) {
    if (st.overview) return '<div class="mpb mpb--overview"><div class="mpb__body"><div class="mpb__title">Store map</div>' +
      '<div class="mpb__loc">Search a product or ask the AI Concierge — its shelf and the best route will appear here.</div></div></div>';
    const it = st.items[st.step];
    if (!it) return '<div class="mpb"><div class="mpb__done">✓ You found everything. Please head to the register to check out.</div></div>';
    const p = it.product || {}, sh = it.shelf || {};
    const isRx = sh.counter || shelfFid(sh) === 'RX';
    const step = st.multi ? '<span class="mpb__no">' + (it.no || st.step + 1) + '</span>' : '';
    const loc = isRx ? 'Pharmacy counter (handed over at the register)' : (sh.slot_label || sh.label || shelfFid(sh) || '');
    return '<div class="mpb">' + img(p.image, p.title, 'mpb__img') + '<div class="mpb__body">' +
      '<div class="mpb__meta">' + step + (st.multi ? '<span>' + (st.step + 1) + ' / ' + st.items.length + (it.role ? ' • ' + esc(it.role) : '') + '</span>' : '') + drugBadges(p) + '</div>' +
      '<div class="mpb__title">' + esc(p.title || '') + '</div>' +
      '<div class="mpb__loc"><b>' + esc(shelfFid(sh) || '') + '</b> ' + esc(isRx ? loc : (sh.fixture_label ? sh.fixture_label + ' • ' : '') + (sh.slot_label || '')) + '</div></div></div>';
  }

  async function renderElevation(st) {
    const box = document.getElementById('map-elev');
    const it = st.items[st.step];
    if (!it || !it.shelf) { box.innerHTML = ''; box.hidden = true; return; }
    box.hidden = false;
    const sh = it.shelf, fid = shelfFid(sh);
    if (sh.counter || fid === 'RX') {
      box.innerHTML = '<div class="elev__rx"><svg viewBox="0 0 24 24"><path d="M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm-2 11h-3v3h-4v-3H7v-4h3V7h4v3h3v4z"/></svg>' +
        '<span>This item is handed over at the register (pharmacy counter). Please ask a staff member. Limit 1 per customer.' + (sh.primary_slot ? ' (' + esc(sh.primary_slot) + ')' : '') + '</span></div>';
      return;
    }
    const mySeq = (M && M.seq) || 0;
    box.innerHTML = '<div class="elev__loading sn-skel"></div>';
    const g = await getGrid(M.storeId, fid);
    if (!M || M.seq !== mySeq) return;
    if (!g) { box.innerHTML = ''; box.hidden = true; return; }
    const f = g.fixture, mine = new Set(sh.slot_ids || []);
    const n = f.slots_per_tier;
    let rows = '';
    g.tiers.forEach((t) => {
      rows += '<div class="elev__row"><span class="elev__tier">shelf ' + t.tier + '</span>' + t.slots.map((s) => {
        const on = mine.has(s.slot_id);
        const filled = (s.products || []).length;
        return '<span class="elev__cell' + (on ? ' is-on' : filled ? ' is-filled' : '') + '" title="' + esc(s.slot_id + (filled ? ': ' + s.products.map((p) => p.title).slice(0, 3).join(' / ') : '')) + '">' +
          (on ? esc(s.slot_id.split('-').pop()) : '') + '</span>';
      }).join('') + '</div>';
    });
    const axis = '<div class="elev__row elev__axis"><span class="elev__tier"></span>' + Array.from({ length: n }, (_, i) => '<span class="elev__cell">' + (i + 1) + '</span>').join('') + '</div>';
    box.innerHTML = '<div class="elev__head"><b>' + esc(f.id) + '</b> ' + esc(f.label || '') + ' • ' + esc(TYPE_JA[f.type] || f.type || '') +
      ' • ' + Math.round((f.length_cm || n * f.slot_width_cm) / 10) / 10 + ' m wide • ' + f.slot_width_cm + ' cm per slot<span class="elev__hint">Front view of the shelf (left to right)</span></div>' +
      '<div class="elev__scroll"><div class="elev__grid" style="--n:' + n + '">' + rows + axis + '</div></div>' +
      (sh.photo_url ? '<div class="elev__photo"><img src="' + esc(sh.photo_url) + '" alt="Shelf photo"><span>Shelf photo registered by staff' + (sh.registered_at ? '<br>' + esc(String(sh.registered_at).slice(0, 16).replace('T', ' ')) : '') + '</span></div>' : '');
    const on = box.querySelector('.elev__cell.is-on');
    if (on) { const sc = box.querySelector('.elev__scroll'); sc.scrollLeft = Math.max(0, on.offsetLeft - sc.clientWidth / 2 + on.offsetWidth / 2); }
  }

  function footer(st) {
    const prev = document.getElementById('mp-prev'), next = document.getElementById('mp-next');
    prev.hidden = next.hidden = !st.multi;
    if (!st.multi) return;
    prev.disabled = st.step === 0;
    const total = st.items.length;
    next.textContent = st.step >= total ? 'Done' : (st.step === total - 1 ? 'To register' : 'Next ' + (NUM[st.step + 1] || ''));
  }

  // ---- pan / zoom
  function canvasEl() { return document.getElementById('map-canvas'); }
  function fit(anim) {
    const canvas = canvasEl(), svg = canvas.querySelector('svg.floor');
    if (!svg || !M) return;
    const vb = svg.viewBox.baseVal, cw = canvas.clientWidth, ch = canvas.clientHeight;
    const s = Math.min(cw / vb.width, ch / vb.height);
    svg.setAttribute('width', vb.width * s); svg.setAttribute('height', vb.height * s);
    M.base = s; M.vb = vb; M.k = 1; M.tx = (cw - vb.width * s) / 2; M.ty = (ch - vb.height * s) / 2;
    apply(anim);
  }
  function apply(anim) {
    const svg = canvasEl().querySelector('svg.floor');
    if (!svg || !M) return;
    svg.style.transition = anim ? 'transform .45s cubic-bezier(.2,0,0,1)' : 'none';
    svg.style.transform = 'translate(' + M.tx + 'px,' + M.ty + 'px) scale(' + M.k + ')';
    // level of detail: fixture-id badges only when they render ≥ 8px
    svg.classList.toggle('lod-low', M.base * M.k * 0.5 < 8);
  }
  function zoomAt(f, cx, cy, anim) {
    const k2 = Math.max(0.8, Math.min(8, M.k * f)), r = k2 / M.k;
    M.tx = cx - (cx - M.tx) * r; M.ty = cy - (cy - M.ty) * r; M.k = k2;
    apply(anim);
  }
  function focusTarget() {
    const it = M && M.items[M.step];
    if (!it || !M.vb) { fit(true); return; }
    const f = it.shelf && fixtureById(M.lay, shelfFid(it.shelf));
    const canvas = canvasEl();
    const px = it.pin ? it.pin.x : (f ? f.x + f.w / 2 : M.vb.width / 2);
    const py = it.pin ? it.pin.y : (f ? f.y + f.h / 2 : M.vb.height / 2);
    const span = 14;                                      // show ≈14 m around the target
    const k = Math.max(1, Math.min(8, canvas.clientWidth / (span * M.base)));
    M.k = k;
    M.tx = canvas.clientWidth / 2 - (px - M.vb.x) * M.base * k;
    M.ty = canvas.clientHeight / 2 - (py - M.vb.y) * M.base * k;
    apply(true);
  }
  function initGestures() {
    const canvas = canvasEl(), pts = new Map();
    let last = null;
    canvas.addEventListener('pointerdown', (e) => {
      if (e.target.closest('.mapv__zoom')) return;
      canvas.setPointerCapture(e.pointerId); pts.set(e.pointerId, { x: e.clientX, y: e.clientY }); last = null;
    });
    canvas.addEventListener('pointermove', (e) => {
      if (!pts.has(e.pointerId) || !M) return;
      const rect = canvas.getBoundingClientRect();
      pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
      const arr = [...pts.values()];
      if (arr.length === 1) {
        const p = arr[0];
        if (last && last.n === 1) { M.tx += p.x - last.x; M.ty += p.y - last.y; apply(false); }
        last = { n: 1, x: p.x, y: p.y };
      } else if (arr.length >= 2) {
        const [a, b] = arr;
        const d = Math.hypot(a.x - b.x, a.y - b.y), mx = (a.x + b.x) / 2 - rect.left, my = (a.y + b.y) / 2 - rect.top;
        if (last && last.n === 2) { M.tx += mx - last.mx; M.ty += my - last.my; zoomAt(d / last.d, mx, my, false); }
        last = { n: 2, d, mx, my };
      }
    });
    const up = (e) => { pts.delete(e.pointerId); last = null; };
    canvas.addEventListener('pointerup', up); canvas.addEventListener('pointercancel', up);
    canvas.addEventListener('wheel', (e) => {
      if (!M) return;
      e.preventDefault();
      const rect = canvas.getBoundingClientRect();
      zoomAt(e.deltaY < 0 ? 1.15 : 1 / 1.15, e.clientX - rect.left, e.clientY - rect.top, false);
    }, { passive: false });
    const center = () => [canvas.clientWidth / 2, canvas.clientHeight / 2];
    document.getElementById('map-zoom-in').addEventListener('click', () => zoomAt(1.35, ...center(), true));
    document.getElementById('map-zoom-out').addEventListener('click', () => zoomAt(1 / 1.35, ...center(), true));
    document.getElementById('map-zoom-reset').addEventListener('click', () => fit(true));
    window.addEventListener('resize', () => { if (M) fit(false); });
  }

  function draw(keepView) {
    const canvas = canvasEl();
    const old = canvas.querySelector('svg.floor');
    if (old) old.remove();
    M.seq = (M.seq || 0) + 1;
    document.getElementById('map-product').innerHTML = productBar(M);
    footer(M);
    const W = M.lay.width + 1.2, H = M.lay.height + 1.6;
    const base = Math.min(canvas.clientWidth / W, canvas.clientHeight / H) || 8;
    canvas.insertAdjacentHTML('beforeend', buildSVG(M.lay, M, base));
    const svg = canvas.querySelector('svg.floor');
    if (keepView && M.vb) { svg.setAttribute('width', M.vb.width * M.base); svg.setAttribute('height', M.vb.height * M.base); apply(false); } else fit(false);
    renderElevation(M);
    const len = document.getElementById('map-len');
    len.textContent = M.route && M.route.length_m ? (M.multi ? 'Full route: about ' : 'About ') + Math.round(M.multi ? M.route.length_m : legLen(M.legs[0])) + ' m' + (M.multi ? '' : ' from the entrance') : '';
  }
  const legLen = (pts) => (pts || []).reduce((a, p, i) => i ? a + Math.hypot(p[0] - pts[i - 1][0], p[1] - pts[i - 1][1]) : 0, 0);

  function step(d) {
    if (!M) return;
    const total = M.items.length;
    if (d > 0 && M.step >= total) { close(); return; }
    M.step = Math.max(0, Math.min(total, M.step + d));
    draw(true);
    // layout height may change (elevation) → refit size then focus
    requestAnimationFrame(() => { if (M.step < total) focusTarget(); else fit(true); });
  }

  /** opts: {storeId, items:[{product, availability, role?, no?}], route?} */
  async function open(opts) {
    const storeId = opts.storeId;
    const lay = await getLayout(storeId);
    const items = (opts.items || []).map((it) => {
      const cur = (it.availability || {}).current || {};
      return { product: it.product, role: it.role, no: it.no, shelf: cur.shelf || null, pin: null };
    }).filter((it) => it.shelf);
    if (!items.length) { window.SN.toast('No shelf information found for this store'); return; }
    let route = opts.route;
    if (!route || !(route.stops || []).length) {
      route = await postJSON('/api/route', { store_id: storeId, product_ids: items.map((it) => it.product.id) });
    }
    const byPid = {};
    (route.stops || []).forEach((s) => { byPid[s.product_id] = s; });
    items.forEach((it) => { const s = byPid[it.product.id]; if (s) { it.pin = s.pin; it.no = s.no; } });
    items.sort((a, b) => (a.no || 99) - (b.no || 99));
    const store = window.SNC.storeById(storeId) || {};
    M = { storeId, lay, items, route, legs: legs(route), step: 0, multi: items.length > 1, k: 1, tx: 0, ty: 0 };
    document.getElementById('map-title').textContent = M.multi ? 'Find all on the map' : 'Find on map';
    document.getElementById('map-sub').textContent = (store.name || '') + (M.multi ? ' • Route order for ' + items.length.toLocaleString('en-US') + ' items' : '');
    document.getElementById('mapv').hidden = false;
    document.getElementById('mapv').classList.remove('is-overview');
    draw(false);
    setTimeout(() => { if (M) focusTarget(); }, M.multi ? 650 : 120);
  }
  // Tablet (>=768px): the map is a persistent pane → "close" returns to the store overview instead of hiding it.
  const isPane = () => false;   // redesign: the floor map is an on-demand overlay on every device
  async function overview(storeId) {
    storeId = storeId || (window.SNC && (window.SNC.state.storeId || window.SNC.state.nearestId || (window.SNC.state.stores[0] || {}).store_id));
    if (!storeId) return;
    let lay;
    try { lay = await getLayout(storeId); } catch (e) { return; }
    if (M && !M.overview) return;      // a product route is on screen
    const store = window.SNC.storeById(storeId) || {};
    M = { storeId, lay, items: [], route: null, legs: [], step: 0, multi: false, overview: true, k: 1, tx: 0, ty: 0 };
    document.getElementById('map-title').textContent = 'Store map';
    document.getElementById('map-sub').textContent = store.name || '';
    document.getElementById('mapv').hidden = false;
    document.getElementById('mapv').classList.add('is-overview');
    draw(false);
  }
  function close() {
    M = null;
    if (isPane()) { overview(); return; }
    document.getElementById('mapv').hidden = true;
  }

  function init() {
    document.getElementById('map-close').addEventListener('click', close);
    document.getElementById('map-back').addEventListener('click', close);
    document.getElementById('mp-prev').addEventListener('click', () => step(-1));
    document.getElementById('mp-next').addEventListener('click', () => step(1));
    initGestures();
  }

  window.FloorMap = { init, open, close, overview, isPane, _legs: legs, state: () => M };
})();
