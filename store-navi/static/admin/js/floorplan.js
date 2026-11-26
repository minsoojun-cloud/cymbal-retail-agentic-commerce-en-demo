/* Cymbal Retail Shelf Manager — floor plan canvas (CONTRACT v12).
   Draws the store (walls, entrance, exit, checkout, pharmacy counter, zones, shelves) in metres with an SVG viewBox and handles
   selection, drawing new shelves, moving shelves, pan and zoom. All hit-testing is geometric (no per-element
   listeners), so the SVG can be re-rendered freely during gestures. */
(function () {
  "use strict";
  const SNAP = 0.3;
  const FLAT = new Set(["flat_table", "chest", "deli_table", "flower"]);
  const COLD = new Set(["multideck", "reach_in", "chest"]);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const snap = (v) => Math.round(v / SNAP) * SNAP;
  const r2 = (v) => Math.round(v * 100) / 100;
  const textW = (t, fs) => [...String(t)].reduce((w, c) => w + (/[\x00-\x7f]/.test(c) ? fs * 0.62 : fs), 0);

  class FloorPlan {
    /** opts: { getState(), onSelect(uid|null, additive), onDrawn(rect), onMoved(), onMoveStart() } */
    constructor(wrap, svg, opts) {
      this.wrap = wrap; this.svg = svg; this.o = opts;
      this.vb = { x: 0, y: 0, w: 70, h: 42 };
      this.tool = "select";
      this.ptrs = new Map();
      this.g = null; // active gesture
      this._bind();
      new ResizeObserver(() => this._resize()).observe(wrap);
    }

    // ------------------------------------------------------------ view
    get scale() { const cw = this.wrap.clientWidth || 1; return cw / this.vb.w; }
    _resize() {
      const cw = this.wrap.clientWidth, ch = this.wrap.clientHeight;
      if (!cw || !ch) return;
      if (!this._fitted) { this.fit(); return; }
      const cx = this.vb.x + this.vb.w / 2, cy = this.vb.y + this.vb.h / 2;
      this.vb.h = this.vb.w * ch / cw;
      this.vb.x = cx - this.vb.w / 2; this.vb.y = cy - this.vb.h / 2;
      this.render();
    }
    fit() {
      const st = this.o.getState(); const lay = st.layout;
      const cw = this.wrap.clientWidth, ch = this.wrap.clientHeight;
      if (!lay || !cw || !ch) return;
      const W = lay.width + 2, H = lay.height + 3;
      const s = Math.min(cw / W, ch / H);
      this.vb.w = cw / s; this.vb.h = ch / s;
      this.vb.x = -1 - (this.vb.w - W) / 2; this.vb.y = -1 - (this.vb.h - H) / 2;
      this._fitted = true;
      this.render();
    }
    zoom(f, cx, cy) {
      const lay = this.o.getState().layout; if (!lay) return;
      const nw = Math.min(Math.max(this.vb.w / f, 4), (lay.width + 2) * 3);
      const k = nw / this.vb.w;
      if (cx === undefined) { cx = this.vb.x + this.vb.w / 2; cy = this.vb.y + this.vb.h / 2; }
      this.vb.x = cx - (cx - this.vb.x) * k; this.vb.y = cy - (cy - this.vb.y) * k;
      this.vb.w = nw; this.vb.h *= k;
      this.render();
    }
    centerOn(f) {
      if (!f) return;
      const minW = 18;
      if (this.vb.w > minW * 1.6) { const k = minW / this.vb.w; this.vb.w *= k; this.vb.h *= k; }
      this.vb.x = f.x + f.w / 2 - this.vb.w / 2; this.vb.y = f.y + f.h / 2 - this.vb.h / 2;
      this.render();
    }
    setTool(t) {
      this.tool = t;
      this.wrap.classList.toggle("tool-draw", t === "draw");
      this.wrap.classList.toggle("tool-move", t === "move");
    }
    toM(e) {
      const rc = this.svg.getBoundingClientRect(), s = this.scale;
      return { x: this.vb.x + (e.clientX - rc.left) / s, y: this.vb.y + (e.clientY - rc.top) / s };
    }

    // ------------------------------------------------------------ hit test
    hit(p) {
      const st = this.o.getState(); const fx = (st.layout && st.layout.fixtures) || [];
      const tol = Math.max(0.12, 6 / this.scale);
      let best = null, bestA = Infinity;
      for (const f of fx) {
        if (p.x >= f.x - tol && p.x <= f.x + f.w + tol && p.y >= f.y - tol && p.y <= f.y + f.h + tol) {
          const a = f.w * f.h;
          if (a < bestA) { best = f; bestA = a; }
        }
      }
      return best;
    }

    // ------------------------------------------------------------ input
    _bind() {
      const sv = this.svg;
      sv.addEventListener("pointerdown", (e) => this._down(e));
      sv.addEventListener("pointermove", (e) => this._move(e));
      sv.addEventListener("pointerup", (e) => this._up(e));
      sv.addEventListener("pointercancel", (e) => { this.ptrs.delete(e.pointerId); this.g = null; this.wrap.classList.remove("panning"); this.render(); });
      sv.addEventListener("wheel", (e) => {
        e.preventDefault();
        const p = this.toM(e);
        this.zoom(e.deltaY < 0 ? 1.15 : 1 / 1.15, p.x, p.y);
      }, { passive: false });
    }
    _down(e) {
      this.svg.setPointerCapture(e.pointerId);
      this.ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (this.ptrs.size === 2) { // pinch
        const [a, b] = [...this.ptrs.values()];
        this.g = { type: "pinch", d: Math.hypot(a.x - b.x, a.y - b.y) };
        clearTimeout(this._lp);
        return;
      }
      const p = this.toM(e);
      const st = this.o.getState();
      const lay = st.layout; if (!lay) return;
      const base = { sx: e.clientX, sy: e.clientY, p0: p, moved: false };
      if (this.tool === "draw") {
        const sp = { x: snap(p.x), y: snap(p.y) };
        this.g = Object.assign(base, { type: "draw", a: sp, b: sp });
        return;
      }
      const f = this.hit(p);
      if (this.tool === "move" && f) {
        if (!st.selected.has(f.uid)) this.o.onSelect(f.uid, false);
        const sel = this.o.getState().selected;
        const items = lay.fixtures.filter((x) => sel.has(x.uid)).map((x) => ({ f: x, x: x.x, y: x.y }));
        this.g = Object.assign(base, { type: "drag", items, started: false });
        return;
      }
      this.g = Object.assign(base, { type: f ? "click" : "pan", f, vb0: Object.assign({}, this.vb) });
      if (f && e.pointerType === "touch") {
        this._lp = setTimeout(() => { if (this.g && this.g.type === "click" && !this.g.moved) { this.g.type = "done"; this.o.onSelect(f.uid, true); } }, 520);
      }
    }
    _move(e) {
      if (!this.ptrs.has(e.pointerId)) return;
      this.ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY });
      const g = this.g; if (!g) return;
      if (g.type === "pinch" && this.ptrs.size === 2) {
        const [a, b] = [...this.ptrs.values()];
        const d = Math.hypot(a.x - b.x, a.y - b.y);
        const mid = this.toM({ clientX: (a.x + b.x) / 2, clientY: (a.y + b.y) / 2 });
        if (g.d > 0) this.zoom(d / g.d, mid.x, mid.y);
        g.d = d;
        return;
      }
      const dxp = e.clientX - g.sx, dyp = e.clientY - g.sy;
      if (Math.hypot(dxp, dyp) > 5) { g.moved = true; clearTimeout(this._lp); }
      const lay = this.o.getState().layout;
      if (g.type === "draw") {
        const p = this.toM(e);
        g.b = { x: Math.min(Math.max(snap(p.x), 0), lay.width), y: Math.min(Math.max(snap(p.y), 0), lay.height) };
        this.render();
      } else if (g.type === "drag" && g.moved) {
        if (!g.started) { g.started = true; this.o.onMoveStart && this.o.onMoveStart(); }
        const p = this.toM(e);
        const dx = snap(p.x - g.p0.x), dy = snap(p.y - g.p0.y);
        g.items.forEach((it) => {
          it.f.x = r2(Math.min(Math.max(it.x + dx, 0), lay.width - it.f.w));
          it.f.y = r2(Math.min(Math.max(it.y + dy, 0), lay.height - it.f.h));
        });
        this.render();
      } else if ((g.type === "pan" || (g.type === "click" && g.moved)) && g.vb0) {
        g.type = "pan";
        this.wrap.classList.add("panning");
        const s = this.scale;
        this.vb.x = g.vb0.x - dxp / s; this.vb.y = g.vb0.y - dyp / s;
        this.render();
      }
    }
    _up(e) {
      this.ptrs.delete(e.pointerId);
      clearTimeout(this._lp);
      const g = this.g;
      if (!g) return;
      if (g.type === "pinch") { if (this.ptrs.size === 0) this.g = null; return; }
      this.g = null;
      this.wrap.classList.remove("panning");
      if (g.type === "draw") {
        const x = Math.min(g.a.x, g.b.x), y = Math.min(g.a.y, g.b.y);
        const w = Math.abs(g.a.x - g.b.x), h = Math.abs(g.a.y - g.b.y);
        this.render();
        if (w >= SNAP - 1e-6 && h >= SNAP - 1e-6) this.o.onDrawn({ x: r2(x), y: r2(y), w: r2(w), h: r2(h) });
        return;
      }
      if (g.type === "drag") { if (g.started) this.o.onMoved(); return; }
      if (g.type === "click" && !g.moved) {
        this.o.onSelect(g.f.uid, e.shiftKey || e.ctrlKey || e.metaKey);
        return;
      }
      if (g.type === "pan" && !g.moved) this.o.onSelect(null, false);
    }

    // ------------------------------------------------------------ render
    render() {
      const st = this.o.getState(); const lay = st.layout;
      if (!lay) { this.svg.innerHTML = ""; return; }
      const s = this.scale, px = (n) => n / s;
      const W = lay.width, H = lay.height, vb = this.vb;
      const out = [];
      out.push(`<rect x="${vb.x}" y="${vb.y}" width="${vb.w}" height="${vb.h}" fill="#eef1f5"/>`);
      out.push(`<rect class="fp-floor" x="0" y="0" width="${W}" height="${H}" rx="0.15"/>`);
      // 1 m grid when zoomed in
      if (s > 22) {
        let gp = "";
        for (let x = Math.max(1, Math.ceil(vb.x)); x < Math.min(W, vb.x + vb.w); x++) gp += `M${x} 0V${H}`;
        for (let y = Math.max(1, Math.ceil(vb.y)); y < Math.min(H, vb.y + vb.h); y++) gp += `M0 ${y}H${W}`;
        out.push(`<path d="${gp}" stroke="#eceff3" stroke-width="1" vector-effect="non-scaling-stroke" fill="none"/>`);
      }
      // zones
      (st.zoneRects || []).forEach((z) => {
        if (!z.rect) return;
        const [x, y, w, h] = z.rect;
        out.push(`<rect class="fp-zone" x="${x}" y="${y}" width="${w}" height="${h}" rx="0.2" fill="${esc(z.color || "#f1f3f4")}"/>`);
      });
      // register / counter
      const misc = (r, label) => {
        if (!r) return;
        out.push(`<rect class="fp-misc" x="${r.x}" y="${r.y}" width="${r.w}" height="${r.h}" rx="0.1"/>`);
        const fs = Math.min(px(12), r.h * 0.7, r.w * 0.7);
        if (textW(label, fs) < Math.max(r.w, r.h)) {
          const vert = r.h > r.w * 1.5;
          out.push(`<text class="fp-misc-label" x="${r.x + r.w / 2}" y="${r.y + r.h / 2}" style="font-size:${fs}px"${vert ? ` transform="rotate(-90 ${r.x + r.w / 2} ${r.y + r.h / 2})"` : ""}>${esc(label)}</text>`);
        }
      };
      misc(lay.register, "Checkout");
      if (lay.counter) misc(lay.counter, lay.counter.label || "Pharmacy counter");
      // fixtures
      const fx = lay.fixtures || [];
      const labels = [];
      fx.forEach((f) => {
        const ann = st.annot(f);
        const cls = ["fp-fx"];
        if (FLAT.has(f.type)) cls.push("flat");
        if (COLD.has(f.type)) cls.push("cold");
        if (ann.none) cls.push("none");
        else if (ann.err) cls.push("err");
        else if (ann.changed) cls.push("chg");
        if (ann.sel) cls.push("sel");
        out.push(`<rect class="${cls.join(" ")}" x="${f.x}" y="${f.y}" width="${f.w}" height="${f.h}" rx="0.04"/>`);
        // face line (customer side)
        const fc = f.face || "S";
        const ln = fc === "S" ? [f.x, f.y + f.h, f.x + f.w, f.y + f.h] : fc === "N" ? [f.x, f.y, f.x + f.w, f.y]
          : fc === "E" ? [f.x + f.w, f.y, f.x + f.w, f.y + f.h] : [f.x, f.y, f.x, f.y + f.h];
        out.push(`<line class="fp-face" x1="${ln[0]}" y1="${ln[1]}" x2="${ln[2]}" y2="${ln[3]}"${ann.sel ? ' style="stroke:#1a73e8"' : ""}/>`);
        // label (level of detail)
        const vert = f.h > f.w;
        const long = vert ? f.h : f.w, short = vert ? f.w : f.h;
        const text = ann.none ? "Not set" : f.id;
        let fsPx = Math.min(14, Math.max(0, short * s * 0.92));
        if (fsPx < 6) return;
        let fs = px(fsPx);
        let t = text;
        if (textW(t, fs) > long * 0.96) { if (ann.none) t = "?"; if (textW(t, fs) > long * 0.96) return; }
        const cx = f.x + f.w / 2, cy = f.y + f.h / 2;
        const lc = ["fp-label"]; if (ann.none) lc.push("none"); else if (ann.err) lc.push("err"); if (ann.sel) lc.push("sel");
        labels.push(`<text class="${lc.join(" ")}" x="${cx}" y="${cy}" style="font-size:${fs}px"${vert ? ` transform="rotate(-90 ${cx} ${cy})"` : ""}>${esc(t)}</text>`);
        if (ann.unassigned) {
          const rr = Math.max(px(7), 0.2);
          labels.push(`<circle class="fp-badge" cx="${f.x + f.w}" cy="${f.y}" r="${rr}"/><text class="fp-badge-t" x="${f.x + f.w}" y="${f.y}" style="font-size:${rr * 1.4}px">!</text>`);
        }
      });
      out.push(...labels);
      // zone names (overview)
      const zfs = px(12), placed = [];
      const hitB = (a, b) => a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
      (st.zoneRects || []).forEach((z) => {
        if (!z.rect || !z.label) return;
        const [x, y, w] = z.rect;
        const tw = textW(z.label, zfs);
        if (tw > w + 3) return;
        const box = { x: x + 0.1, y: y - px(3) - zfs, w: tw, h: zfs * 1.1 };
        if (placed.some((o) => hitB(o, box))) return;
        placed.push(box);
        out.push(`<text class="fp-zlabel" x="${box.x}" y="${y - px(3)}" style="font-size:${zfs}px">${esc(z.label)}</text>`);
      });
      // doors
      const door = (d, label) => {
        if (!d) return;
        const r = Math.max(0.35, px(7));
        out.push(`<circle class="fp-door" cx="${d.x}" cy="${d.y}" r="${r}"/>`);
        out.push(`<text class="fp-door-label" x="${d.x}" y="${d.y + r + px(14)}" style="font-size:${px(13)}px">${label}</text>`);
      };
      door(lay.entrance, "Entrance");
      door(lay.exit, "Exit");
      // drawing preview
      const g = this.g;
      if (g && g.type === "draw") {
        const x = Math.min(g.a.x, g.b.x), y = Math.min(g.a.y, g.b.y), w = Math.abs(g.a.x - g.b.x), h = Math.abs(g.a.y - g.b.y);
        out.push(`<rect class="fp-draw" x="${x}" y="${y}" width="${w}" height="${h}"/>`);
        out.push(`<text class="fp-draw-label" x="${x}" y="${y - px(6)}" style="font-size:${px(13)}px">${(w * 100).toFixed(0)}cm × ${(h * 100).toFixed(0)}cm</text>`);
      }
      this.svg.setAttribute("viewBox", `${vb.x} ${vb.y} ${vb.w} ${vb.h}`);
      this.svg.innerHTML = out.join("");
    }
  }
  window.FloorPlan = FloorPlan;
  window.FloorPlan.SNAP = SNAP;
  window.FloorPlan.FLAT = FLAT;
})();
