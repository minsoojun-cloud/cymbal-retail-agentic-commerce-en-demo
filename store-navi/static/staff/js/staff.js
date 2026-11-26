/* Cymbal Staff — touch-first staff app (Register / Stock / History). Vanilla JS, no build. CONTRACT v9 slot ids. */
(() => {
  "use strict";
  const CFG = window.SN_CONFIG || {};
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const yen = (n) => (n || n === 0 ? Number(n).toLocaleString("en-US", { style: "currency", currency: "JPY" }) : "");
  const LS = { store: "sn_staff_store", name: "sn_staff_name", zoom: "sn_staff_zoom" };
  const STATUS_EN = { in_stock: "In stock", low_stock: "Low stock", out_of_stock: "Out of stock", not_carried: "Not carried" };
  const DRUG = {
    "Pharmacist-only (Class 1)": ["drug--1", "Pharmacist-only (Class 1): a pharmacist must explain this product at purchase. Not available when no pharmacist is on duty."],
    "Designated Class 2": ["drug--s2", "Designated Class 2: always check the warnings (contraindications) before use."],
    "Class 2": ["drug--2", "Class 2: ask a pharmacist or registered seller if you have any questions."],
    "Class 3": ["drug--3", ""],
    "Quasi-drug": ["drug--q", ""],
  };
  const S = {
    stores: [], store: localStorage.getItem(LS.store) || "", staff: localStorage.getItem(LS.name) || "",
    role: localStorage.getItem("sn_role") || "staff",
    layouts: {}, photos: [], pick: null, analysis: null, cands: [], selProduct: null, sel: [], ai: [], grid: null, step: 1,
    stock: [], stockSel: 0, zoom: Number(localStorage.getItem(LS.zoom) || 1), isReassign: false,
  };
  const GRID = {};

  // ---------------------------------------------------------------- utils
  async function api(path, opts = {}) {
    const o = { headers: { "Content-Type": "application/json" }, ...opts };
    if (o.body && typeof o.body !== "string") o.body = JSON.stringify(o.body);
    const r = await fetch(path, o);
    let data = null;
    try { data = await r.json(); } catch { /* empty */ }
    if (!r.ok) throw new Error((data && (data.detail || data.error)) || `HTTP ${r.status}`);
    return data;
  }
  let toastT;
  function toast(msg, err = false) {
    const t = $("#toast");
    t.textContent = msg; t.classList.remove("toast--rich", "warn"); t.classList.toggle("err", err); t.classList.add("show");
    clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove("show"), 2800);
    if (navigator.vibrate) try { navigator.vibrate(err ? [30, 40, 30] : 15); } catch { /* ignore */ }
  }
  /** Toast after a shelf registration: Retail API addLocalInventories (Commerce Search Local Inventory) result. */
  function syncToast(li, loc) {
    if (!li) return;
    const p = (loc && loc.product) || S.selProduct || {};
    const where = `${esc(storeName(li.place_id || (loc && loc.store_id)))} (${esc(li.place_id || "")})`;
    const what = esc(p.title || li.product_id || "");
    let head, cls = "";
    if (li.status === "synced" || li.status === "submitted") head = "Synced to Commerce Search Local Inventory";
    else if (li.status === "skipped") { head = "Saved — Commerce Search Local Inventory sync skipped"; cls = "warn"; }
    else { head = "Saved — Commerce Search Local Inventory sync failed"; cls = "err"; }
    const t = $("#toast");
    t.innerHTML = `<span class="toast__icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M19.35 10.04A7.49 7.49 0 0 0 12 4C9.11 4 6.6 5.64 5.35 8.04A5.994 5.994 0 0 0 0 14c0 3.31 2.69 6 6 6h13c2.76 0 5-2.24 5-5 0-2.64-2.05-4.78-4.65-4.96zM10 17l-3.5-3.5 1.41-1.41L10 14.17 15.18 9l1.41 1.41L10 17z"/></svg></span>`
      + `<span class="toast__txt"><b>${head}</b><small>${what} • ${where}${li.reason ? ` • ${esc(li.reason)}` : ""}</small></span>`;
    t.classList.remove("err", "warn"); if (cls) t.classList.add(cls);
    t.classList.add("show", "toast--rich");
    clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove("show", "toast--rich"), 5200);
    const el = $("#done-sync");
    if (el) { el.className = `done__sync ${cls}`; el.innerHTML = `<b>${head}</b> — placeId ${esc(li.place_id || "")}, product ${esc(li.product_id || "")}`; }
  }
  const drugBadge = (p) => (p && p.drug_class && DRUG[p.drug_class] ? `<span class="drug ${DRUG[p.drug_class][0]}">${esc(p.drug_class)}</span>` : "");
  function drugNote(p) {
    const n = p && p.drug_class && DRUG[p.drug_class] && DRUG[p.drug_class][1];
    let s = n ? `<div class="drug-note">${esc(n)}</div>` : "";
    if (p && p.abuse_control) s += `<div class="drug-note">Abuse-control medicine: kept at and handed over from the pharmacy counter (RX). Limit 1 per customer.</div>`;
    return s;
  }
  const storeName = (id) => (S.stores.find((s) => s.store_id === id) || {}).short_name || id;
  const imgOf = (p) => (p && p.image) || "";
  const fmtTime = (iso) => { const d = new Date(iso); return isNaN(d) ? (iso || "") : d.toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZone: "Asia/Tokyo" }); };
  const parseSlot = (sid) => { const m = /^(RX|[A-Z]{1,2}\d{2})-(\d)(\d{2})$/.exec(sid || ""); return m ? { fid: m[1], tier: +m[2], idx: +m[3] } : null; };
  const slotId = (fid, tier, idx) => `${fid}-${tier}${String(idx).padStart(2, "0")}`;
  function slotLabel(ids) {
    const ps = (ids || []).map(parseSlot).filter(Boolean).sort((a, b) => a.idx - b.idx);
    if (!ps.length) return "";
    const f = ps[0], l = ps[ps.length - 1];
    return `${slotId(f.fid, f.tier, f.idx)}${ps.length > 1 ? `–${l.tier}${String(l.idx).padStart(2, "0")}` : ""} (tier ${f.tier}, ${ps.length > 1 ? `positions ${f.idx}–${l.idx}` : `position ${f.idx}`} from left)`;
  }
  function shelfText(sh) {
    if (!sh) return "";
    if (sh.slot_label) return sh.slot_label;
    if (sh.slot_ids && sh.slot_ids.length) return slotLabel(sh.slot_ids);
    if (sh.label) return sh.label;
    return `${sh.fixture_id || sh.gondola_id || ""}${sh.tier ? ` tier ${sh.tier}` : ""}`;
  }

  // ---------------------------------------------------------------- sheets / menu
  let openSheetEl = null;
  function openSheet(id) {
    closeSheet();
    openSheetEl = $(id); openSheetEl.hidden = false; $("#scrim").hidden = false;
    const f = openSheetEl.querySelector("input[type=search], input:not([type=radio])");
    if (f && matchMedia("(min-width: 600px)").matches) setTimeout(() => f.focus(), 60);
  }
  function closeSheet() {
    if (openSheetEl) openSheetEl.hidden = true;
    openSheetEl = null; $("#scrim").hidden = true;
  }
  $("#scrim").addEventListener("click", () => { if (!S.mustPickWho) closeSheet(); });
  $$("[data-close]").forEach((b) => b.addEventListener("click", closeSheet));
  // swipe-down to close sheets
  $$(".sheet").forEach((sh) => {
    let y0 = null;
    sh.addEventListener("touchstart", (e) => { if (sh.scrollTop <= 0 && e.target.closest(".sheet__grip, .sheet__hd, h2")) y0 = e.touches[0].clientY; }, { passive: true });
    sh.addEventListener("touchend", (e) => { if (y0 !== null && e.changedTouches[0].clientY - y0 > 70 && !S.mustPickWho) closeSheet(); y0 = null; });
  });
  const menu = $("#more-menu");
  $("#more-btn").addEventListener("click", (e) => { e.stopPropagation(); menu.hidden = !menu.hidden; });
  document.addEventListener("click", (e) => { if (!menu.hidden && !e.target.closest("#more-menu")) menu.hidden = true; });
  $("#menu-switch").addEventListener("click", () => { menu.hidden = true; openWho(); });
  $("#menu-reload").addEventListener("click", () => { menu.hidden = true; Object.keys(GRID).forEach((k) => delete GRID[k]); loadPhotos(); if (activeTab() === "history") loadHistory(); toast("Refreshed"); });

  // ---------------------------------------------------------------- tabs / steps / back
  const activeTab = () => ($(".nav__item.active") || {}).dataset?.tab || "register";
  function showTab(name) {
    $$(".nav__item").forEach((t) => { t.classList.toggle("active", t.dataset.tab === name); t.setAttribute("aria-selected", t.dataset.tab === name); });
    $$(".panel").forEach((p) => p.classList.toggle("active", p.dataset.panel === name));
    if (location.hash !== `#${name}`) history.replaceState(null, "", `#${name}`);
    $("#topbar-title").textContent = { register: "Register shelf", stock: "Stock lookup", history: "History" }[name] || "Staff";
    updateChrome();
    window.scrollTo({ top: 0 });
    if (name === "history") loadHistory();
    if (window.ShelfAdmin) window.ShelfAdmin.refreshUnassigned();
  }
  $$(".nav__item").forEach((t) => t.addEventListener("click", () => showTab(t.dataset.tab)));
  function setStep(n) {
    S.step = n;
    $$(".stage").forEach((s) => s.classList.toggle("active", s.id === `stage-${n}`));
    updateChrome();
    window.scrollTo({ top: 0 });
  }
  function updateChrome() {
    const flow = activeTab() === "register" && S.step > 1;
    document.body.classList.toggle("flow", flow);
    $("#back-btn").hidden = !flow;
    $("#progress").hidden = !flow;
    $("#progress i").style.width = `${S.step * 25}%`;
    if (activeTab() === "register") $("#topbar-title").textContent = ["Register shelf", "Analyzing…", "Confirm", "Registered"][S.step - 1];
  }
  $("#back-btn").addEventListener("click", () => { if (S.step === 3 || S.step === 2) resetRegister(); else if (S.step === 4) resetRegister(); });
  window.addEventListener("popstate", () => { if (S.step > 1) resetRegister(); });

  // ---------------------------------------------------------------- who (store + staff + role)
  function setRole(role) {
    S.role = role === "manager" ? "manager" : "staff";
    localStorage.setItem("sn_role", S.role);
    document.cookie = `sn_role=${S.role}; path=/; max-age=604800`;
    updateRoleUI();
  }
  function updateRoleUI() {
    const isMgr = S.role === "manager";
    $("#btn-role-staff")?.classList.toggle("btn--primary", !isMgr);
    $("#btn-role-staff")?.classList.toggle("btn--tonal", isMgr);
    $("#btn-role-manager")?.classList.toggle("btn--primary", isMgr);
    $("#btn-role-manager")?.classList.toggle("btn--tonal", !isMgr);
  }
  function renderWho() {
    $("#who-store").textContent = S.store ? storeName(S.store) : "Select store";
    $("#who-name").textContent = S.staff || "Staff member";
    updateRoleUI();
  }
  function openWho(must = false) {
    S.mustPickWho = must;
    updateRoleUI();
    $("#store-pick").innerHTML = S.stores.map((s) => `
      <label class="store-opt"><input type="radio" name="store" value="${esc(s.store_id)}" ${s.store_id === (S.store || "S01") ? "checked" : ""}/>
        <div><b>${esc(s.name || s.short_name)}</b><span>${esc(s.address || "")}</span></div></label>`).join("");
    $("#staff-name").value = S.staff || "";
    $("#name-chips").innerHTML = ["Sato", "Suzuki", "Takahashi", "Tanaka"].map((n) => `<button type="button">${n}</button>`).join("");
    $$("#name-chips button").forEach((b) => b.addEventListener("click", () => { $("#staff-name").value = b.textContent; }));
    openSheet("#who-sheet");
  }
  $("#btn-role-staff")?.addEventListener("click", () => setRole("staff"));
  $("#btn-role-manager")?.addEventListener("click", () => setRole("manager"));
  $("#store-chip").addEventListener("click", () => openWho());
  $("#staff-chip").addEventListener("click", () => openWho());
  $("#who-ok").addEventListener("click", () => {
    const sel = $("input[name=store]:checked"); const name = $("#staff-name").value.trim();
    if (!sel || !name) { toast("Select a store and enter the staff name", true); return; }
    const changed = sel.value !== S.store;
    S.store = sel.value; S.staff = name; S.mustPickWho = false;
    localStorage.setItem(LS.store, S.store); localStorage.setItem(LS.name, S.staff);
    renderWho(); closeSheet(); renderStoreBanner();
    if (changed) { resetRegister(); renderGallery(); ensureLayout(S.store); if (window.ShelfAdmin) window.ShelfAdmin.loadStore(S.store); }
    updateAdminLinks();
  });

  // ---------------------------------------------------------------- step 1: capture / gallery
  function resetRegister() {
    S.pick = null; S.analysis = null; S.selProduct = null; S.sel = []; S.ai = []; S.grid = null; S.isReassign = false;
    $("#file-input").value = ""; $("#f-note").value = ""; $(".memo").open = false;
    setStep(1);
  }

  window.startReassignmentFlow = function(item) {
    resetRegister();
    S.isReassign = true;
    S.selProduct = { id: item.product_id, title: item.title, image: item.thumbnail };
    S.cands = [S.selProduct];
    $("#confirm-img").src = item.thumbnail || "/static/shared/img/no-image.png";
    $("#detect").innerHTML = `
      <div class="detect__item"><b>Product</b><span>${esc(item.title)}</span></div>
      <div class="detect__item"><b>Previous slot</b><span>${(item.previous_slot_ids || []).join(', ') || 'Not set'} (${esc(item.previous_fixture_id || 'Not set')})</span></div>
      <div class="small muted" style="margin-top:6px;">Reassigning a product without a shelf number (no photo needed). Pick the new fixture and slots on the right.</div>
    `;
    $("#extra").innerHTML = `<div class="tag-row"><span class="tag tag--ocr">Reassign mode</span></div>`;
    renderCands();
    showTab("register");
    setStep(3);
    if (item.previous_fixture_id) {
      selectFixture(item.previous_fixture_id);
    }
  };
  async function loadPhotos() {
    try { S.photos = (await api("/api/staff/demo-photos")).photos || []; } catch (e) { S.photos = []; toast(`Could not load demo photos: ${e.message}`, true); }
    renderGallery();
  }
  function renderGallery() {
    const all = $("#demo-all").checked;
    const list = S.photos.filter((p) => all || !S.store || p.store_id === S.store);
    const g = $("#gallery");
    if (!list.length) { g.innerHTML = `<div class="empty" style="grid-column:1/-1"><p>${S.photos.length ? "No demo photos for this store (turn on \"All stores\")" : "No demo photos"}</p></div>`; return; }
    g.innerHTML = list.map((p) => `
      <button type="button" class="gitem" data-id="${esc(p.id)}" aria-label="${esc(p.product_title || p.hint || p.id)}">
        <div class="gitem__img"><img src="${esc(p.url)}" alt="" loading="lazy"/><span class="gitem__tag">${esc(storeName(p.store_id))}</span></div>
        <div class="gitem__meta"><div class="gitem__title">${esc(p.product_title || p.hint || p.id)}</div></div>
      </button>`).join("");
    $$(".gitem", g).forEach((b) => b.addEventListener("click", () => {
      const p = S.photos.find((x) => x.id === b.dataset.id);
      if (p && S.store && p.store_id !== S.store) toast(`This photo is from ${storeName(p.store_id)} (analyzing with the ${storeName(S.store)} layout)`);
      startAnalyze({ photo_id: p.id, url: p.url });
    }));
  }
  $("#demo-all").addEventListener("change", renderGallery);
  $("#file-input").addEventListener("change", async (e) => {
    const f = e.target.files && e.target.files[0]; if (!f) return;
    try { const d = await downscale(f, 1600); startAnalyze({ image_base64: d, url: d }); }
    catch (err) { toast(`Could not read the image: ${err.message}`, true); }
  });
  function downscale(file, max) {
    return new Promise((resolve, reject) => {
      const img = new Image(); const url = URL.createObjectURL(file);
      img.onload = () => {
        const s = Math.min(1, max / Math.max(img.width, img.height));
        const c = document.createElement("canvas"); c.width = Math.round(img.width * s); c.height = Math.round(img.height * s);
        c.getContext("2d").drawImage(img, 0, 0, c.width, c.height); URL.revokeObjectURL(url); resolve(c.toDataURL("image/jpeg", 0.86));
      };
      img.onerror = () => reject(new Error("decode error")); img.src = url;
    });
  }

  // ---------------------------------------------------------------- step 2: analyze
  async function startAnalyze(pick) {
    if (!S.store) { openWho(true); return; }
    S.pick = pick;
    $("#scan-img").src = pick.url; $("#confirm-img").src = pick.url;
    history.pushState({ step: 2 }, "", location.hash);
    setStep(2);
    try {
      const body = { store_id: S.store, ...(pick.photo_id ? { photo_id: pick.photo_id } : { image_base64: pick.image_base64 }) };
      const [res] = await Promise.all([api("/api/staff/analyze-photo", { method: "POST", body }), new Promise((r) => setTimeout(r, 900))]);
      S.analysis = res;
      await ensureLayout(S.store);
      await syncLayout(true);
      await renderConfirm();
      setStep(3);
    } catch (e) { toast(`Analysis failed: ${e.message}`, true); setStep(1); }
  }
  async function ensureLayout(id) {
    if (S.layouts[id]) return S.layouts[id];
    try { S.layouts[id] = await api(`/api/stores/${encodeURIComponent(id)}/layout`); } catch { S.layouts[id] = { fixtures: [] }; }
    return S.layouts[id];
  }
  function fixturesOf(id) {
    const lay = S.layouts[id] || {};
    const fs = [...(lay.fixtures || lay.gondolas || [])].map((f) => ({
      ...f, tiers: Number(f.tiers || 5), face: f.face || (f.faces || [])[0] || "",
      slots_per_tier: Number(f.slots_per_tier || Math.max(1, Math.round((f.length_cm || Math.max(f.w || 0.9, f.h || 0.9) * 100) / (f.slot_width_cm || 30)))),
    }));
    if (lay.counter && !fs.some((f) => f.id === (lay.counter.id || "RX"))) fs.push({ ...lay.counter, id: lay.counter.id || "RX", type: "counter", tiers: lay.counter.tiers || 1, slots_per_tier: lay.counter.slots_per_tier || 18 });
    return fs;
  }
  async function loadGrid(store, fid) {
    const key = `${store}:${fid}`;
    if (GRID[key]) return GRID[key];
    let g = null;
    try { g = await api(`/api/stores/${encodeURIComponent(store)}/fixtures/${encodeURIComponent(fid)}`); } catch { g = null; }
    return (GRID[key] = normGrid(g, fixturesOf(store).find((f) => f.id === fid)));
  }
  function normGrid(g, fx) {
    const fixture = { ...(fx || {}), ...((g && g.fixture) || {}) };
    const rows = [];
    for (const t of (g && (g.tiers || g.rows)) || []) {
      const tier = Number(t.tier);
      rows.push({ tier, slots: (t.slots || []).map((sl, i) => ({
        slot_id: sl.slot_id || slotId(fixture.id, tier, sl.index || i + 1), index: Number(sl.index || i + 1),
        products: (sl.products || sl.product_ids || []).map((x) => (typeof x === "string" ? { id: x } : { ...x, id: x.id || x.product_id })),
      })) });
    }
    if (!rows.length && fixture.id) for (let t = fixture.tiers || 1; t >= 1; t--) rows.push({ tier: t, slots: Array.from({ length: fixture.slots_per_tier || 1 }, (_, i) => ({ slot_id: slotId(fixture.id, t, i + 1), index: i + 1, products: [] })) });
    rows.sort((a, b) => b.tier - a.tier); rows.forEach((r) => r.slots.sort((a, b) => a.index - b.index));
    return { fixture, rows };
  }
  function elevHTML(grid, opt = {}) {
    let h = "";
    const tag = opt.readonly ? "div" : "button";
    for (const r of grid.rows) {
      h += `<div class="elev__row"><div class="elev__tier">T${r.tier}</div>`;
      for (const sl of r.slots) {
        const cls = ["slot"];
        if ((opt.sel || []).includes(sl.slot_id)) cls.push("sel");
        if ((opt.ai || []).includes(sl.slot_id)) cls.push("ai");
        if (opt.pid && sl.products.some((p) => p.id === opt.pid)) cls.push("cur");
        const imgs = opt.readonly ? "" : sl.products.slice(0, 4).map((p) => (p.image ? `<img src="${esc(p.image)}" alt="" loading="lazy"/>` : "")).join("");
        const more = !opt.readonly && (sl.products.length > 4 || (sl.products.length && !sl.products[0].image)) ? `<span class="more">${sl.products.length} items</span>` : "";
        h += `<${tag} ${tag === "button" ? 'type="button"' : ""} class="${cls.join(" ")}" data-slot="${esc(sl.slot_id)}"
          aria-label="${esc(sl.slot_id)} ${sl.products.length} items" title="${esc(sl.slot_id)}${sl.products.length ? " • " + esc(sl.products.map((p) => p.title || p.id).join(" / ")) : ""}">${imgs}${more}<span class="slot__id">${esc(sl.slot_id.split("-")[1] || sl.slot_id)}</span></${tag}>`;
      }
      h += `</div>`;
    }
    const n = Math.max(1, ...grid.rows.map((r) => r.slots.length));
    return { html: h, cols: `auto repeat(${n}, var(--cell-w))` };
  }

  // ---------------------------------------------------------------- step 3: confirm
  async function renderConfirm() {
    const a = S.analysis || {};
    const chips = [];
    if (a.slot_ids && a.slot_ids.length) chips.push(`<span class="chip chip--ok">Slot <b>${esc(a.slot_ids[0])}</b></span>`);
    else if (a.fixture_id) chips.push(`<span class="chip chip--ok">Fixture <b>${esc(a.fixture_id)}</b></span><span class="chip chip--warn">Slot label not found</span>`);
    else chips.push(`<span class="chip chip--warn">Could not read the shelf number${a.slot_label_text || a.plate_text ? ` (${esc(a.slot_label_text || a.plate_text)})` : ""}</span>`);
    if (a.tier_guess) chips.push(`<span class="chip chip--ai">Tier ${esc(a.tier_guess)}${a.facings_guess > 1 ? ` • ${a.facings_guess} slots` : ""}</span>`);
    if (a.confidence != null) chips.push(`<span class="chip">Confidence ${Math.round((a.confidence || 0) * 100)}%</span>`);
    $("#detect").innerHTML = chips.join("");
    const extra = [];
    if (a.ocr_text) extra.push(`<div class="note"><b>Package text (OCR)</b><br>${esc(a.ocr_text)}</div>`);
    if (a.existing_products && a.existing_products.length) extra.push(`<div class="note"><b>Products registered in this slot</b><br>${a.existing_products.map((p) => esc(p.title || p.id)).join(" / ")}</div>`);
    if (a.vision_error) extra.push(`<div class="note">AI analysis is unavailable. Please select manually.</div>`);
    $("#extra").innerHTML = extra.join("");
    S.cands = a.candidates || [];
    S.selProduct = S.cands[0] || null;
    renderCands();
    $("#cand-q").value = a.search_query || "";
    S.sel = [...(a.slot_ids || [])]; S.ai = [...(a.slot_ids || [])];
    await selectFixture(a.fixture_id || a.gondola_id || "");
  }
  $("#confirm-img").addEventListener("click", () => $(".detect-card").classList.toggle("big"));
  function candHTML(p, i, sel) {
    return `<button type="button" class="cand ${sel ? "sel" : ""}" data-i="${i}" role="radio" aria-checked="${sel}">
      <img src="${esc(imgOf(p))}" alt="" loading="lazy"/>
      <div><div class="cand__title">${esc(p.title)}</div>
        <div class="cand__sub">${i === 0 && (p.match === "demo_hint" || S.analysis) ? `<span class="badge-ai">AI match</span>` : ""}${drugBadge(p)}<span>${yen(p.price)}</span></div>
        ${sel ? drugNote(p) : ""}</div>
      <span class="cand__radio"></span></button>`;
  }
  function renderCands() {
    const box = $("#cands");
    if (!S.cands.length) { box.innerHTML = `<div class="muted small">No candidates found. Search by product name.</div>`; $("#cand-sub").textContent = ""; return; }
    let top = S.cands.slice(0, 3);
    if (S.selProduct && !top.some((p) => p.id === S.selProduct.id)) top = [S.selProduct, ...top.slice(0, 2)];
    $("#cand-sub").textContent = `${S.cands.length} candidates`;
    box.innerHTML = top.map((p) => candHTML(p, S.cands.indexOf(p), S.selProduct && p.id === S.selProduct.id)).join("");
    $$(".cand", box).forEach((el) => el.addEventListener("click", () => chooseCand(S.cands[Number(el.dataset.i)])));
    $("#more-cands").firstChild.nextSibling && ($("#more-cands").lastChild.textContent = S.cands.length > 3 ? ` ${S.cands.length - 3} more candidates / search products` : " Search products");
  }
  function chooseCand(p) {
    if (!p) return;
    if (!S.cands.some((c) => c.id === p.id)) S.cands.push(p);
    S.selProduct = p; renderCands(); if (S.grid) renderElev(); renderSheetCands();
  }
  function renderSheetCands(list = S.cands) {
    const box = $("#cands-all");
    box.innerHTML = list.length ? list.map((p) => candHTML(p, S.cands.indexOf(p) >= 0 ? S.cands.indexOf(p) : -1, S.selProduct && p.id === S.selProduct.id)).join("") : `<div class="empty"><p>No matches</p></div>`;
    $$(".cand", box).forEach((el, k) => el.addEventListener("click", () => { chooseCand(list[k]); closeSheet(); }));
  }
  $("#more-cands").addEventListener("click", () => { renderSheetCands(); openSheet("#cand-sheet"); });
  $("#cand-search-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const q = $("#cand-q").value.trim(); if (!q) return;
    $("#cands-all").innerHTML = `<div class="empty"><span class="spinner"></span></div>`;
    try {
      const r = await api(`/api/search?q=${encodeURIComponent(q)}&store_id=${encodeURIComponent(S.store)}&page_size=12`);
      renderSheetCands((r.results || []).map((x) => x.product || x));
    } catch (err) { toast(`Search failed: ${err.message}`, true); }
  });

  // fixture picker sheet
  function renderFxList() {
    const q = $("#fx-q").value.trim().toUpperCase();
    const cur = S.grid && S.grid.fixture.id;
    const fs = fixturesOf(S.store).filter((f) => !q || f.id.includes(q) || (f.label || "").toUpperCase().includes(q)).slice(0, 200);
    $("#fx-list").innerHTML = fs.map((f) => `<button type="button" class="fx-item ${f.id === cur ? "on" : ""}" data-id="${esc(f.id)}">
      <b>${esc(f.id)}</b><span>${esc(f.label || "")}</span><small>${f.tiers} tiers × ${f.slots_per_tier}</small></button>`).join("") || `<div class="empty"><p>No matches</p></div>`;
    $$(".fx-item").forEach((b) => b.addEventListener("click", () => { S.sel = []; selectFixture(b.dataset.id); closeSheet(); }));
  }
  $("#fixture-btn").addEventListener("click", () => { $("#fx-q").value = ""; renderFxList(); openSheet("#fixture-sheet"); syncLayout(true); });
  $("#fx-q").addEventListener("input", renderFxList);
  async function selectFixture(fid) {
    const fx = fixturesOf(S.store).find((f) => f.id === fid);
    const hint = $("#fixture-hint");
    S.sel = (S.sel || []).filter((x) => (parseSlot(x) || {}).fid === fid);
    if (!fx) {
      $("#fixture-val").textContent = "Select a fixture";
      hint.className = "hint warn"; hint.textContent = "Check the slot label / fixture number in the photo and select the fixture.";
      S.grid = null; $("#f-elev").innerHTML = ""; renderSelSum(); renderConfirmMap(); return;
    }
    $("#fixture-val").textContent = `${fx.id} • ${fx.label || ""}`;
    hint.className = "hint ok"; hint.textContent = `${fx.tiers} tiers × ${fx.slots_per_tier} slots (${fx.slot_width_cm || 30} cm each)`;
    S.grid = await loadGrid(S.store, fid);
    renderElev();
  }
  function renderElev() {
    const box = $("#f-elev");
    const { html, cols } = elevHTML(S.grid, { sel: S.sel, ai: S.ai, pid: S.selProduct && S.selProduct.id });
    box.style.gridTemplateColumns = cols; box.innerHTML = html;
    $$(".slot", box).forEach((b) => b.addEventListener("click", () => tapSlot(b.dataset.slot)));
    applyZoom(); scrollToSel(box); renderSelSum();
  }
  function scrollToSel(box) {
    const el = box.querySelector(".slot.sel") || box.querySelector(".slot.ai") || box.querySelector(".slot.cur");
    const wrap = box.parentElement;
    if (el && wrap) wrap.scrollLeft = Math.max(0, el.offsetLeft - wrap.clientWidth / 2 + el.offsetWidth / 2);
  }
  function tapSlot(sid) {
    const p = parseSlot(sid); if (!p) return;
    const cur = S.sel.map(parseSlot).filter(Boolean);
    if (S.sel.includes(sid)) {
      const idxs = cur.map((c) => c.idx).sort((a, b) => a - b);
      S.sel = (p.idx === idxs[0] || p.idx === idxs[idxs.length - 1]) ? S.sel.filter((x) => x !== sid) : [sid];
    } else if (cur.length && cur[0].tier === p.tier && cur.length < 3 && cur.some((c) => Math.abs(c.idx - p.idx) === 1)) {
      S.sel = [...S.sel, sid];
    } else {
      if (cur.length >= 3 && cur[0].tier === p.tier) toast("Up to 3 slots per product");
      S.sel = [sid];
    }
    S.sel.sort((a, b) => parseSlot(a).idx - parseSlot(b).idx);
    if (navigator.vibrate) try { navigator.vibrate(8); } catch { /* ignore */ }
    const left = $("#elev-wrap").scrollLeft;
    renderElev(); $("#elev-wrap").scrollLeft = left;
  }
  function renderSelSum() {
    renderConfirmMap();
    const el = $("#sel-sum");
    if (!S.sel.length) { el.className = "sel-sum empty"; el.textContent = "Tap slots to choose the shelf location"; return; }
    const sl = (S.grid && S.grid.rows.flatMap((r) => r.slots).filter((x) => S.sel.includes(x.slot_id))) || [];
    const others = [...new Set(sl.flatMap((x) => x.products).filter((p) => !S.selProduct || p.id !== S.selProduct.id).map((p) => p.title || p.id))];
    el.className = "sel-sum";
    el.innerHTML = `${esc(slotLabel(S.sel))} • ${S.sel.length} slot${S.sel.length > 1 ? "s" : ""}` +
      (others.length ? `<small>Same slot: ${others.slice(0, 3).map(esc).join(" / ")}${others.length > 3 ? ` +${others.length - 3} more` : ""}</small>` : "");
  }
  // zoom (buttons + pinch)
  function applyZoom() {
    const z = Math.max(0.6, Math.min(1.8, S.zoom));
    $("#f-elev").style.setProperty("--cell-w", `${Math.round(56 * z)}px`);
    $("#f-elev").style.setProperty("--cell-h", `${Math.round(60 * Math.max(0.85, z))}px`);
  }
  function setZoom(z) { S.zoom = Math.max(0.6, Math.min(1.8, z)); localStorage.setItem(LS.zoom, String(S.zoom)); applyZoom(); }
  $("#zoom-in").addEventListener("click", () => setZoom(S.zoom + 0.2));
  $("#zoom-out").addEventListener("click", () => setZoom(S.zoom - 0.2));
  (() => {
    const wrap = $("#elev-wrap"); const pts = new Map(); let d0 = 0, z0 = 1;
    const dist = () => { const [a, b] = [...pts.values()]; return Math.hypot(a.x - b.x, a.y - b.y); };
    wrap.addEventListener("pointerdown", (e) => { pts.set(e.pointerId, { x: e.clientX, y: e.clientY }); if (pts.size === 2) { d0 = dist(); z0 = S.zoom; } });
    wrap.addEventListener("pointermove", (e) => { if (!pts.has(e.pointerId)) return; pts.set(e.pointerId, { x: e.clientX, y: e.clientY }); if (pts.size === 2 && d0) setZoom(z0 * dist() / d0); });
    const up = (e) => { pts.delete(e.pointerId); if (pts.size < 2) d0 = 0; };
    wrap.addEventListener("pointerup", up); wrap.addEventListener("pointercancel", up);
  })();
  $("#btn-back").addEventListener("click", resetRegister);

  $("#reg-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!S.selProduct) return toast("Select a product", true);
    if (!S.grid) return toast("Select a fixture", true);
    if (!S.sel.length) return toast("Select slots", true);
    const btn = $("#btn-register"); btn.disabled = true; btn.innerHTML = `<span class="spinner"></span> Registering…`;
    try {
      const body = {
        store_id: S.store,
        product_id: S.selProduct.id,
        slot_ids: S.sel,
        note: $("#f-note").value.trim() || (S.isReassign ? "Shelf reassigned (manager layout change)" : ""),
        staff_name: S.staff || "Staff",
        reassign: !!S.isReassign,
        ...(S.pick?.photo_id ? { photo_id: S.pick.photo_id } : S.pick?.image_base64 ? { image_base64: S.pick.image_base64 } : {})
      };
      const r = await api("/api/staff/shelf-locations", { method: "POST", body });
      delete GRID[`${S.store}:${r.location.fixture_id}`];
      await renderDone(r.location);
      setStep(4);
      syncToast(r.local_inventory, r.location);
      if (window.ShelfAdmin) window.ShelfAdmin.refreshUnassigned();
      if (navigator.vibrate) try { navigator.vibrate([20, 60, 20]); } catch { /* ignore */ }
    } catch (err) { toast(`Registration failed: ${err.message}`, true); }
    finally { btn.disabled = false; btn.textContent = "Register location"; }
  });

  // ---------------------------------------------------------------- step 4: done
  async function renderDone(loc) {
    const p = loc.product || S.selProduct || {};
    const sids = loc.slot_ids || [];
    $("#done-sub").textContent = `${storeName(loc.store_id)} • ${loc.registered_by} • ${fmtTime(loc.registered_at)}`;
    $("#done-prod").innerHTML = `<img src="${esc(imgOf(p))}" alt=""/><div><div class="cand__title">${esc(p.title)}</div><div class="cand__sub">${drugBadge(p)}<span>JAN ${esc(p.id)}</span></div></div>`;
    $("#done-loc").innerHTML = `<div class="loc-big"><small>${esc(loc.label || loc.fixture_id)}</small><b>${esc(sids[0] || loc.fixture_id)}${sids.length > 1 ? `<span style="font-size:.55em">–${esc(sids[sids.length - 1].split("-")[1])}</span>` : ""}</b>
      Tier ${esc(loc.tier)} • position ${esc(loc.position)} from left • ${sids.length} slot${sids.length > 1 ? "s" : ""}</div>
      ${loc.photo_url ? `<div class="loc-photo"><img src="${esc(loc.photo_url)}" alt="Registered photo"/></div>` : ""}`;
    const open = $("#btn-open-customer");
    if (CFG.customerUrl) { open.hidden = false; open.href = CFG.customerUrl; } else open.hidden = true;
    $("#map-store").textContent = storeName(loc.store_id);
    const lay = await ensureLayout(loc.store_id);
    $("#minimap").innerHTML = storeMapSVG(lay, { hit: loc.fixture_id || loc.gondola_id, slots: sids, mode: "all" });
    const g = await loadGrid(loc.store_id, loc.fixture_id);
    const m = elevHTML(g, { sel: sids, pid: p.id, readonly: true });
    $("#done-elev").style.gridTemplateColumns = m.cols; $("#done-elev").innerHTML = m.html;
    requestAnimationFrame(() => scrollToSel($("#done-elev")));
  }
  // ---------------------------------------------------------------- floor map (CONTRACT v13)
  function fixturesAll(lay) {
    const all = [...((lay && (lay.fixtures || lay.gondolas)) || [])];
    if (lay && lay.counter && !all.some((f) => f.id === (lay.counter.id || "RX"))) all.push({ ...lay.counter, id: lay.counter.id || "RX", type: "counter" });
    return all;
  }
  function slotPt(f, idx, n) {
    const t = (idx - 0.5) / Math.max(1, n), { x, y, w, h } = f;
    switch (f.face || "S") {
      case "S": return [x + w * t, y + h];          // dot on the customer-facing edge
      case "N": return [x + w * (1 - t), y];
      case "W": return [x, y + h * t];
      default: return [x + w, y + h * (1 - t)];
    }
  }
  const txtW = (t, fs) => [...String(t)].reduce((w, c) => w + (/[\x00-\x7f]/.test(c) ? fs * 0.62 : fs), 0);
  /** Store floor plan (walls, entrance/exit, checkout, zones, every shelf) with the target shelf highlighted.
      o: { hit: fixture id, slots: [slot ids], mode: "near" (≈22 m around the shelf, every shelf numbered) | "all" } */
  function storeMapSVG(lay, o = {}) {
    if (!lay || !lay.width) return `<div class="muted small" style="padding:12px">No layout information</div>`;
    const r = (n) => Math.round(n * 100) / 100;
    const W = lay.width, H = lay.height, all = fixturesAll(lay);
    const g = o.hit ? all.find((f) => f.id === o.hit) : null;
    const near = o.mode === "near" && g;
    let vb = [-2, -1.2, W + 4, H + 4.4];  // room for the target halo on wall shelves
    if (near) {
      const vw = Math.min(W + 1.6, 24), vh = Math.min(H + 2.6, vw * 0.62);
      const cx = g.x + g.w / 2, cy = g.y + g.h / 2;
      vb = [r(Math.min(Math.max(cx - vw / 2, -0.8), W + 0.8 - vw)), r(Math.min(Math.max(cy - vh / 2, -0.8), H + 1.8 - vh)), vw, vh];
    }
    const k = vb[2] / 24; // label scale (1 at the near zoom)
    const kd = Math.min(k, 1.5); // decorations (zone names, doors) stay modest on the whole-store view
    let s = `<svg viewBox="${vb.join(" ")}" role="img" aria-label="Floor map${o.hit ? ` ${esc(o.hit)}` : ""}">`;
    s += `<rect class="fm-floor" x="0" y="0" width="${W}" height="${H}" rx=".25"/>`;
    for (const z of lay.zones || []) { const [x, y, w, h] = z.rect || []; if (w) s += `<rect class="fm-zone" x="${x}" y="${y}" width="${w}" height="${h}" fill="${esc(z.color || "#e8f0fe")}" rx=".2"/>`; }
    if (lay.register) { const q = lay.register; s += `<rect class="fm-misc" x="${q.x}" y="${q.y}" width="${q.w}" height="${q.h}" rx=".1"/><text class="fm-misc-t" x="${r(q.x + q.w / 2)}" y="${r(q.y + q.h / 2)}" style="font-size:${r(Math.min(q.h * 0.75, 0.9 * kd))}px">Checkout</text>`; }
    let labels = "";
    for (const f of all) {
      const on = g && f.id === g.id;
      s += `<rect class="fm-fx${on ? " hit" : ""}${o.clickable ? " tap" : ""}" data-fid="${esc(f.id)}" x="${f.x}" y="${f.y}" width="${f.w}" height="${f.h}" rx=".06"/>`;
      if (!near && !on) continue;
      const vert = f.h > f.w, long = vert ? f.h : f.w, short = vert ? f.w : f.h;
      let fs = on ? Math.max(0.55 * k, Math.min(short * 0.85, 0.7 * k)) : Math.min(short * 0.85, 0.55);
      if (!on && (fs < 0.28 || txtW(f.id, fs) > long * 0.95)) continue;
      const cx = r(f.x + f.w / 2), cy = r(f.y + f.h / 2);
      labels += `<text class="fm-t${on ? " hit" : ""}" x="${cx}" y="${cy}" style="font-size:${r(fs)}px"${vert ? ` transform="rotate(-90 ${cx} ${cy})"` : ""}>${esc(f.id)}</text>`;
    }
    s += labels;
    // zone names
    const zfs = near ? 0.6 : 1.1 * kd, placed = [];
    for (const z of lay.zones || []) {
      const [x, y, w] = z.rect || []; if (!w || !z.label) continue;
      const tw = txtW(z.label, zfs); if (tw > w + 2) continue;
      const inside = y - zfs * 1.05 < vb[1];  // zone touches the top edge of the view → put its name inside the zone
      const b = { x: x + 0.05, y: inside ? y + 0.05 : y - zfs * 1.05, w: tw, h: zfs };
      if (placed.some((p) => b.x < p.x + p.w && b.x + b.w > p.x && b.y < p.y + p.h && b.y + b.h > p.y)) continue;
      placed.push(b);
      s += `<text class="fm-z" x="${r(b.x)}" y="${r(inside ? y + zfs * 0.85 : y - zfs * 0.2)}" style="font-size:${r(zfs)}px">${esc(z.label)}</text>`;
    }
    const door = (d, t, c) => { if (d) s += `<circle cx="${d.x}" cy="${d.y}" r="${r(0.45 * kd + 0.15)}" fill="${c}"/><text class="fm-door" x="${d.x}" y="${r(d.y + 1.2 * kd + 0.3)}" fill="${c}" style="font-size:${r(0.9 * kd)}px">${t}</text>`; };
    door(lay.entrance, "Entrance", "#188038"); door(lay.exit, "Exit", "#5f6368");
    if (g) {
      const n = Number(g.slots_per_tier) || 1;
      const idxs = [...new Set((o.slots || []).map((x) => (parseSlot(x) || {}).idx).filter(Boolean))];
      for (const i of idxs) { const [px, py] = slotPt(g, i, n); s += `<circle class="fm-slot" cx="${r(px)}" cy="${r(py)}" r="${r(Math.max(0.18, 0.22 * k))}"/>`; }
      s += `<circle class="fm-pulse" cx="${r(g.x + g.w / 2)}" cy="${r(g.y + g.h / 2)}" r="${r(1.6 * k)}"><animate attributeName="r" values="${r(1 * k)};${r(2.6 * k)};${r(1 * k)}" dur="1.6s" repeatCount="indefinite"/></circle>`;
    }
    return s + `</svg>`;
  }
  function renderFmap(root, lay, opt) {
    if (!root) return;
    root._lay = lay; root._opt = opt;
    const mode = opt.hit ? (root.dataset.mode || "near") : "all";
    $$("[data-fz]", root).forEach((b) => { b.classList.toggle("on", b.dataset.fz === mode); b.disabled = !opt.hit; });
    root.querySelector(".fmap__svg").innerHTML = storeMapSVG(lay, { ...opt, mode });
  }
  document.addEventListener("click", (e) => {
    const b = e.target.closest(".fmap [data-fz]"); if (!b) return;
    const root = b.closest(".fmap"); root.dataset.mode = b.dataset.fz; renderFmap(root, root._lay, root._opt || {});
  });
  function renderConfirmMap() {
    const root = $("#confirm-map"); if (!root) return;
    $("#confirm-map-store").textContent = storeName(S.store);
    const fid = S.grid && S.grid.fixture && S.grid.fixture.id;
    const fx = fid && fixturesOf(S.store).find((f) => f.id === fid);
    $("#confirm-map-cap").innerHTML = fx ? `<b>${esc(fx.id)}</b> ${esc(fx.label || "")}${S.sel.length ? ` • <b>${esc(S.sel[0])}</b>${S.sel.length > 1 ? `–${esc(S.sel[S.sel.length - 1].split("-")[1])}` : ""}` : ""} <span class="muted">(you can also tap a shelf on the map)</span>`
      : "Tap a shelf on the map to select the fixture";
    renderFmap(root, S.layouts[S.store], { hit: fid || null, slots: S.sel, clickable: true });
  }
  $("#confirm-map-svg").addEventListener("click", (e) => {
    const el = e.target.closest("[data-fid]"); if (!el) return;
    const fid = el.dataset.fid;
    if (S.grid && S.grid.fixture && S.grid.fixture.id === fid) return;
    S.sel = []; selectFixture(fid);
  });
  /** map sheet: where is this product's shelf (stock search / history) */
  async function openMapSheet(storeId, shelf, product) {
    const fid = shelf && (shelf.fixture_id || shelf.gondola_id);
    const sids = (shelf && shelf.slot_ids) || [];
    $("#ms-title").textContent = "Shelf location";
    $("#ms-prod").innerHTML = product ? `<img src="${esc(imgOf(product))}" alt=""/><div><b>${esc(product.title || product.id)}</b><span>${esc(shelfText(shelf))}</span></div>` : "";
    $("#ms-store").textContent = storeName(storeId);
    $("#ms-slot").textContent = sids[0] ? `${sids[0]}${sids.length > 1 ? `–${sids[sids.length - 1].split("-")[1]}` : ""}` : (fid || "");
    $("#ms-svg").innerHTML = `<div class="skeleton" style="aspect-ratio:16/9"></div>`;
    $("#ms-elev").innerHTML = "";
    openSheet("#map-sheet");
    const lay = await ensureLayout(storeId);
    renderFmap($("#ms-map"), lay, { hit: fid, slots: sids });
    if (fid) {
      const gr = await loadGrid(storeId, fid);
      const m = elevHTML(gr, { sel: sids, pid: product && product.id, readonly: true });
      $("#ms-elev").style.gridTemplateColumns = m.cols; $("#ms-elev").innerHTML = m.html;
      requestAnimationFrame(() => scrollToSel($("#ms-elev")));
    }
  }
  /** register screen: which store am I registering for */
  async function renderStoreBanner() {
    if (!S.store) return;
    const st = S.stores.find((x) => x.store_id === S.store) || {};
    $("#reg-store-name").textContent = st.name || storeName(S.store);
    $("#reg-store-sub").textContent = st.address || "";
    const lay = await ensureLayout(S.store);
    $("#reg-store-map").innerHTML = storeMapSVG(lay, { mode: "all" });
  }
  $("#reg-store-switch").addEventListener("click", () => openWho());

  // ---------------------------------------------------------------- live layout sync with the Shelf Manager (CONTRACT v13)
  let syncing = false;
  async function syncLayout(force = false) {
    if (syncing || !S.store || (document.hidden && !force)) return;
    syncing = true;
    try {
      const sid = S.store;
      const v = await api(`/api/stores/${encodeURIComponent(sid)}/layout/version`);
      const cur = S.layouts[sid];
      if (!cur || Number(cur.version || 0) !== Number(v.version || 0)) {
        const curFid = S.grid && S.grid.fixture && S.grid.fixture.id;
        const curUid = curFid && ((cur && (cur.fixtures || []).find((f) => f.id === curFid)) || {}).uid;
        const lay = await api(`/api/stores/${encodeURIComponent(sid)}/layout`);
        const had = !!cur;
        S.layouts[sid] = lay;
        Object.keys(GRID).filter((k) => k.startsWith(`${sid}:`)).forEach((k) => delete GRID[k]);
        if (had) {
          toast(`Shelf Manager changes applied (v${lay.version})`);
          renderStoreBanner();
          if (!$("#fixture-sheet").hidden) renderFxList();
          if (S.step === 3) {
            const fid = curFid;
            if (fid) {
              let next = fixturesOf(sid).some((f) => f.id === fid) ? fid : null;
              if (!next && curUid) {   // renamed in the Shelf Manager → follow the same shelf (uid) and carry the selected slots
                const nf = (lay.fixtures || []).find((f) => f.uid === curUid);
                if (nf) {
                  next = nf.id;
                  S.sel = S.sel.map((x) => { const q = parseSlot(x); return q ? slotId(nf.id, q.tier, q.idx) : x; });
                  S.ai = S.ai.map((x) => { const q = parseSlot(x); return q ? slotId(nf.id, q.tier, q.idx) : x; });
                  toast(`Shelf number changed in the Shelf Manager: ${fid} → ${nf.id}`);
                }
              }
              if (!next) toast(`Fixture ${fid} was removed in the Shelf Manager. Please select a fixture again`, true);
              await selectFixture(next || "");
            } else renderConfirmMap();
          }
          if (window.ShelfAdmin) window.ShelfAdmin.refreshUnassigned();
        }
      }
    } catch { /* offline / transient */ } finally { syncing = false; }
  }
  setInterval(() => syncLayout(), 10000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) syncLayout(true); });
  window.addEventListener("focus", () => syncLayout(true));
  $("#btn-again").addEventListener("click", resetRegister);

  // ---------------------------------------------------------------- stock lookup
  let acT, acIdx = -1, acItems = [];
  const acBox = $("#stock-ac");
  $("#stock-q").addEventListener("input", (e) => {
    clearTimeout(acT);
    const q = e.target.value.trim();
    if (!q) { acBox.hidden = true; return; }
    acT = setTimeout(async () => {
      try {
        const r = await api(`/api/autocomplete?q=${encodeURIComponent(q)}`);
        acItems = (r.suggestions || []).slice(0, 8); acIdx = -1;
        if (!acItems.length || $("#stock-q").value.trim() !== q) { acBox.hidden = true; return; }
        acBox.innerHTML = acItems.map((s, i) => `<li role="option" data-i="${i}">${esc(s)}</li>`).join("");
        acBox.hidden = false;
        $$("li", acBox).forEach((li) => li.addEventListener("pointerdown", (ev) => { ev.preventDefault(); pickAc(Number(li.dataset.i)); }));
      } catch { acBox.hidden = true; }
    }, 150);
  });
  $("#stock-q").addEventListener("keydown", (e) => {
    if (acBox.hidden) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); acIdx = (acIdx + (e.key === "ArrowDown" ? 1 : -1) + acItems.length) % acItems.length; $$("li", acBox).forEach((li, i) => li.classList.toggle("on", i === acIdx)); }
    else if (e.key === "Enter" && acIdx >= 0) { e.preventDefault(); pickAc(acIdx); }
    else if (e.key === "Escape") acBox.hidden = true;
  });
  $("#stock-q").addEventListener("blur", () => setTimeout(() => { acBox.hidden = true; }, 150));
  function pickAc(i) { $("#stock-q").value = acItems[i]; acBox.hidden = true; stockSearch(); }
  $("#stock-form").addEventListener("submit", (e) => { e.preventDefault(); acBox.hidden = true; $("#stock-q").blur(); stockSearch(); });

  async function stockSearch(q = $("#stock-q").value.trim()) {
    if (!q) return;
    $("#stock-results").innerHTML = `<div class="skeleton" style="aspect-ratio:auto;height:180px"></div><div class="skeleton" style="aspect-ratio:auto;height:180px"></div>`;
    try {
      const r = await api(`/api/staff/stock-search?q=${encodeURIComponent(q)}`);
      S.stock = r.results || []; S.stockSel = 0; renderStock();
    } catch (e) { $("#stock-results").innerHTML = `<div class="empty"><p>Search failed: ${esc(e.message)}</p></div>`; }
  }
  function storeRows(r, ri) {
    return r.stores.map((s) => {
      const here = s.store_id === S.store;
      return `<div class="srow ${here ? "here" : ""}">
        <div class="srow__name">${esc(s.short_name)}${here ? `<span class="srow__here">This store</span>` : ""}</div>
        <div class="srow__qty"><b>${s.quantity == null ? "—" : s.quantity}</b><small>${s.quantity == null ? "" : "units"}</small></div>
        <div class="srow__meta"><span class="st st--${esc(s.status)}">${STATUS_EN[s.status] || esc(s.status)}</span>
          ${s.shelf && !s.shelf.unassigned && (s.shelf.fixture_id || s.shelf.gondola_id) ? `<span>Shelf <b>${esc(shelfText(s.shelf))}</b></span><button type="button" class="map-btn" data-map="${ri}" data-s="${esc(s.store_id)}" aria-label="Show the ${esc(s.short_name)} shelf on the floor map"><svg viewBox="0 0 24 24"><path d="M20.5 3l-.16.03L15 5.1 9 3 3.36 4.9c-.21.07-.36.25-.36.48V20.5c0 .28.22.5.5.5l.16-.03L9 18.9l6 2.1 5.64-1.9c.21-.07.36-.25.36-.48V3.5c0-.28-.22-.5-.5-.5zM15 19l-6-2.11V5l6 2.11V19z"/></svg>Map</button>` : s.shelf && s.shelf.unassigned ? `<span class="st st--low_stock">Location pending (no shelf number)</span>` : s.status === "not_carried" ? "" : "<span>No shelf registered</span>"}</div>
        ${s.status !== "not_carried" ? `<div class="srow__adj">
          <span class="stepper"><button type="button" data-adj="-1" data-r="${ri}" data-s="${esc(s.store_id)}" aria-label="${esc(s.short_name)} decrease by 1">−</button>
          <output>${s.quantity ?? 0}</output>
          <button type="button" data-adj="1" data-r="${ri}" data-s="${esc(s.store_id)}" aria-label="${esc(s.short_name)} increase by 1">+</button></span>
          <button type="button" class="btn btn--zero" data-zero="1" data-r="${ri}" data-s="${esc(s.store_id)}">Set to 0</button></div>` : ""}
      </div>`;
    }).join("");
  }
  function stockCard(r, ri, sel) {
    const p = r.product || {};
    const here = r.stores.find((s) => s.store_id === S.store);
    const others = r.stores.filter((s) => s.store_id !== S.store && (s.status === "in_stock" || s.status === "low_stock"));
    const hint = here && (here.status === "out_of_stock" || here.status === "not_carried") && others.length
      ? `<div class="sr__hint">In stock at other stores: ${others.map((s) => `${esc(s.short_name)} ${s.quantity}`).join(" / ")}</div>` : "";
    const dots = r.stores.map((s) => `<span class="st st--${esc(s.status)}" title="${esc(s.short_name)}">${esc(s.short_name)} ${s.quantity ?? "—"}</span>`).join("");
    return `<article class="sr ${sel ? "sel" : ""}" data-r="${ri}">
      <button type="button" class="sr__hd" data-pick="${ri}"><img src="${esc(imgOf(p))}" alt="" loading="lazy"/>
        <div><div class="sr__title">${esc(p.title)}</div><div class="sr__sub">${drugBadge(p)}<span>JAN ${esc(p.id)}</span><span>${yen(p.price)}</span></div>
        <div class="sr__dots">${dots}</div></div></button>
      ${drugNote(p)}${hint}${storeRows(r, ri)}</article>`;
  }
  function renderStock() {
    const box = $("#stock-results"), det = $("#stock-detail");
    if (!S.stock.length) { box.innerHTML = `<div class="empty"><p>No matching products</p></div>`; det.innerHTML = ""; return; }
    box.innerHTML = S.stock.map((r, ri) => stockCard(r, ri, ri === S.stockSel)).join("");
    det.innerHTML = stockCard(S.stock[S.stockSel], S.stockSel, false) + `<div class="fmap fmap--detail" id="stock-map">
      <div class="fmap__hd"><span class="fmap__store"><svg viewBox="0 0 24 24"><path d="M20 4H4v2h16V4zm1 10v-2l-1-5H4l-1 5v2h1v6h10v-6h4v6h2v-6h1zm-9 4H6v-4h6v4z"/></svg><b id="stock-map-store"></b></span>
      <span class="fmap__title" id="stock-map-slot"></span>
      <span class="fmap__seg"><button type="button" data-fz="near" class="on">Nearby</button><button type="button" data-fz="all">Whole store</button></span></div>
      <div class="fmap__svg"></div></div>`;
    renderStockMap(S.stock[S.stockSel], S.stockMapStore || S.store);
    $$("[data-pick]", box).forEach((b) => b.addEventListener("click", () => { S.stockSel = Number(b.dataset.pick); S.stockMapStore = null; renderStock(); }));
    $$("[data-map]", $("#panel-stock")).forEach((b) => b.addEventListener("click", () => {
      const r = S.stock[Number(b.dataset.map)]; const st = r.stores.find((x) => x.store_id === b.dataset.s);
      if (b.closest("#stock-detail") && matchMedia("(min-width: 900px)").matches) { S.stockMapStore = st.store_id; renderStockMap(r, st.store_id); return; }
      openMapSheet(st.store_id, st.shelf, r.product);
    }));
    $$("[data-adj],[data-zero]", $("#panel-stock")).forEach((b) => b.addEventListener("click", () => {
      const r = S.stock[Number(b.dataset.r)]; const s = r.stores.find((x) => x.store_id === b.dataset.s);
      if (b.dataset.zero) { confirmZero(r, s); return; }
      setQty(r, s, Math.max(0, (s.quantity || 0) + Number(b.dataset.adj)));
    }));
  }
  async function renderStockMap(r, storeId) {
    const root = $("#stock-map"); if (!root || !r) return;
    let st = r.stores.find((x) => x.store_id === storeId && x.shelf && !x.shelf.unassigned && (x.shelf.fixture_id || x.shelf.gondola_id));
    if (!st) st = r.stores.find((x) => x.shelf && !x.shelf.unassigned && (x.shelf.fixture_id || x.shelf.gondola_id));
    if (!st) { root.hidden = true; return; }
    const sids = st.shelf.slot_ids || [];
    $("#stock-map-store").textContent = st.short_name;
    $("#stock-map-slot").textContent = sids[0] ? `Shelf ${sids[0]}${sids.length > 1 ? `–${sids[sids.length - 1].split("-")[1]}` : ""}` : (st.shelf.fixture_id || "");
    const lay = await ensureLayout(st.store_id);
    renderFmap(root, lay, { hit: st.shelf.fixture_id || st.shelf.gondola_id, slots: sids });
  }
  async function setQty(r, s, qty) {
    try {
      const res = await api("/api/staff/inventory", { method: "PATCH", body: { store_id: s.store_id, product_id: r.product.id, quantity: qty, staff_name: S.staff } });
      s.quantity = res.quantity; s.status = res.status;
      toast(`${s.short_name}: ${res.quantity} units (${STATUS_EN[res.status] || res.status})`);
      renderStock();
    } catch (e) { toast(`Stock update failed: ${e.message}`, true); }
  }
  let zeroTarget = null;
  function confirmZero(r, s) {
    zeroTarget = [r, s];
    $("#cf-body").textContent = `Set stock of "${r.product.title}" at ${s.short_name} to 0 (demo stock adjustment).`;
    openSheet("#confirm-sheet");
  }
  $("#cf-ok").addEventListener("click", () => { closeSheet(); if (zeroTarget) setQty(zeroTarget[0], zeroTarget[1], 0); zeroTarget = null; });

  // ---------------------------------------------------------------- history
  async function loadHistory() {
    const box = $("#history");
    if (!box.children.length) box.innerHTML = `<div class="skeleton" style="aspect-ratio:auto;height:86px"></div><div class="skeleton" style="aspect-ratio:auto;height:86px"></div>`;
    const all = $("#hist-all").checked;
    try {
      const r = await api(`/api/staff/registrations${all || !S.store ? "" : `?store_id=${encodeURIComponent(S.store)}`}`);
      const rows = r.registrations || [];
      S.histRows = rows;
      box.innerHTML = rows.length ? rows.map((x) => `
        <div class="hrow">
          ${x.photo_url ? `<img class="hrow__photo" src="${esc(x.photo_url)}" alt="" loading="lazy"/>` : `<img class="hrow__photo" src="${esc(imgOf(x.product))}" alt="" loading="lazy"/>`}
          <div><div class="hrow__title">${esc((x.product && x.product.title) || x.product_id)}</div>
            <span class="hrow__loc">${esc(x.slot_ids && x.slot_ids.length ? slotLabel(x.slot_ids).split(" (")[0] : `${x.fixture_id || x.gondola_id} tier ${x.tier}`)}</span>${(x.fixture_id || x.gondola_id) ? `<button type="button" class="map-btn" data-hmap="${esc(JSON.stringify({ s: x.store_id, f: x.fixture_id || x.gondola_id, ids: x.slot_ids || [], p: x.product_id }))}"><svg viewBox="0 0 24 24"><path d="M20.5 3l-.16.03L15 5.1 9 3 3.36 4.9c-.21.07-.36.25-.36.48V20.5c0 .28.22.5.5.5l.16-.03L9 18.9l6 2.1 5.64-1.9c.21-.07.36-.25.36-.48V3.5c0-.28-.22-.5-.5-.5zM15 19l-6-2.11V5l6 2.11V19z"/></svg>Map</button>` : ""}
            <div class="hrow__meta">${esc(storeName(x.store_id))} • ${esc(x.registered_by || "")} • ${esc(fmtTime(x.registered_at))}${x.note ? ` • ${esc(x.note)}` : ""}</div></div>
        </div>`).join("") : `<div class="empty"><p>No registrations yet</p></div>`;
    } catch (e) { box.innerHTML = `<div class="empty"><p>Could not load: ${esc(e.message)}</p></div>`; }
  }
  $("#hist-all").addEventListener("change", loadHistory);
  $("#history").addEventListener("click", (e) => {
    const b = e.target.closest("[data-hmap]"); if (!b) return;
    const d = JSON.parse(b.dataset.hmap);
    const row = (S.histRows || []).find((x) => x.product_id === d.p && (x.fixture_id || x.gondola_id) === d.f);
    openMapSheet(d.s, { fixture_id: d.f, slot_ids: d.ids, slot_label: d.ids.length ? slotLabel(d.ids) : d.f }, (row && row.product) || { id: d.p, title: d.p });
  });
  // pull-to-refresh on the history list
  (() => {
    let y0 = null; const ptr = $("#ptr");
    $("#panel-history").addEventListener("touchstart", (e) => { if (window.scrollY <= 0) y0 = e.touches[0].clientY; }, { passive: true });
    $("#panel-history").addEventListener("touchmove", (e) => { if (y0 !== null && e.touches[0].clientY - y0 > 60) ptr.classList.add("on"); }, { passive: true });
    $("#panel-history").addEventListener("touchend", async () => { if (ptr.classList.contains("on")) { await loadHistory(); ptr.classList.remove("on"); } y0 = null; });
  })();

  // ---------------------------------------------------------------- init
  async function init() {
    const cl = $("#customer-link");
    if (CFG.customerUrl && /^https?:\/\//.test(CFG.customerUrl)) { cl.href = CFG.customerUrl; cl.hidden = false; }
    // cross links (CONTRACT v14): Store App ↔ Online Store ↔ Data Enrichment Studio (36px round icon + tooltip)
    [["#xl-store", CFG.customerUrl], ["#xl-online", CFG.onlineStoreUrl], ["#xl-enrich", CFG.enrichUrl]].forEach(([sel, u]) => {
      const a = $(sel); if (a && u && /^https?:\/\//.test(u)) { a.href = u; a.hidden = false; }
    });
    try { S.stores = (await api("/api/stores")).stores || []; } catch { S.stores = []; }
    if (!S.stores.length) S.stores = ["S01", "S02", "S03"].map((id) => ({ store_id: id, short_name: id, name: id }));
    if (S.store && !S.stores.some((s) => s.store_id === S.store)) S.store = "";
    const qp = new URLSearchParams(location.search);
    if (qp.get("store")) S.store = qp.get("store");
    if (qp.get("staff")) S.staff = qp.get("staff");
    S.fromAdmin = qp.get("from") === "admin" || !!qp.get("reassign");
    if (S.store) localStorage.setItem(LS.store, S.store);
    // v12: never downgrade the manager cookie that the Shelf Manager screen relies on
    if (/(?:^|; )sn_role=manager/.test(document.cookie)) S.role = "manager";
    if (qp.get("role")) setRole(qp.get("role")); else updateRoleUI();
    renderWho(); loadPhotos();
    const tab = (location.hash || "#register").slice(1);
    showTab(["register", "stock", "history"].includes(tab) ? tab : "register");
    if (!S.store || !S.staff) openWho(true); else ensureLayout(S.store).then(() => { renderStoreBanner(); syncLayout(true); });
    updateAdminLinks();
    if (window.ShelfAdmin) { window.ShelfAdmin.loadStore(S.store || "S01"); window.ShelfAdmin.openFromQuery(); }
  }
  function updateAdminLinks() {
    const href = `/admin/?store=${encodeURIComponent(S.store || "S01")}`;
    const a = $("#admin-link"); if (a) a.href = href;
    const b = $("#btn-back-admin"); if (b) { b.href = href; b.hidden = !S.fromAdmin; }
  }
  if ("serviceWorker" in navigator && location.protocol !== "file:") {
    window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
  }
  init();
})();
