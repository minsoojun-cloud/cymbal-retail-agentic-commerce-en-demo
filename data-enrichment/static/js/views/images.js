// (7) Seasonal Image Studio — product image + verified specs + seasonal theme → Gemini generates promotional images, saved to GCS / BigQuery / Cymbal source catalog
import { api, isMock } from '../api.js?v=20261111q';
import { esc, $, $$, fmtInt, fmtDate, toast, emptyState, errorState, productList, openModal, state } from '../ui.js?v=20261111q';

const BUCKET_DEFAULT = 'products_images_demo';
const SEASON_ORDER = ['spring', 'rainy', 'summer', 'autumn', 'winter', 'all'];
const SEASON_META = {
  spring: { label: 'Spring', icon: '🌸' }, rainy: { label: 'Rainy season', icon: '☔' }, summer: { label: 'Summer', icon: '☀️' },
  autumn: { label: 'Fall', icon: '🍁' }, winter: { label: 'Winter', icon: '❄️' }, all: { label: 'Year-round', icon: '✨' },
};
const BASE_MODES = [
  ['registered', '🖼', 'Catalog image', 'Default'], ['gcs', '☁️', 'GCS path', ''], ['upload', '⬆️', 'Upload', ''],
  ['web', '🔍', 'Web search', ''], ['none', '✏️', 'No base image', ''],
];
const UPLOAD_TYPES = ['image/jpeg', 'image/png', 'image/webp'];
const MAX_UPLOAD = 10 * 1024 * 1024;

export async function render(root, params, stale) {
  const sku = params[0];
  let list = [];
  try { list = await productList(); } catch (e) { /* keep empty */ }
  if (stale()) return;
  if (!sku) {
    if (!list.length) { root.innerHTML = emptyState('📦', 'No products', 'Load data and try again'); return; }
    const p = list.find((x) => x.sku === '4925319234365') || list.find((x) => x.group === 'fresh') || list[0];
    location.replace('#/images/' + encodeURIComponent(p.sku));
    return;
  }

  const ctx = {
    root, sku, list, stale, product: null, presets: [], options: {},
    base: { mode: 'registered', gcs: '', upload: null, uploadPct: null, localUrl: '', web: null, webLoading: false, webPick: null },
    sel: new Set(), extra: '', n: 3, size: 300, aspect: '1:1', text: 'none', model: '',
    running: false, ctrl: null, timer: null, gen: null,
    selected: new Set(), primary: null, saving: false, saveResult: null, updateAics: false,
    gallery: null, galleryLoading: false, objectUrls: [], uploadCtrl: null, disposed: false,
  };

  root.innerHTML = `
    <div class="im-hero">
      <div class="im-hero-txt">
        <h1>🎨 Seasonal Image Studio<span class="h1-sub">(product photo × seasonal theme → auto-generated promotional images)</span></h1>
        <p>Gemini creates seasonal promotional images from the product's <b>catalog image</b> and <b>verified specs</b>.
        Only <b>features the product actually has</b> can be chosen as themes, so there's no risk of overstated claims.</p>
      </div>
      <div class="im-hero-flow" aria-hidden="true">
        <span>1. Base image</span><i>›</i><span>2. Choose theme</span><i>›</i><span>3. Generate</span><i>›</i><span>4. Save</span>
      </div>
    </div>
    <div class="panel im-head" id="im-head"><div class="skel" style="height:64px"></div></div>
    <div id="im-body"><div class="skel skel-panel"></div></div>`;

  // Delegated handlers (survive partial repaints)
  // NOTE: #view persists across routes → listeners must be removed in cleanup.
  const hClick = (e) => onClick(ctx, e);
  const hChange = (e) => onChange(ctx, e);
  const hInput = (e) => onInput(ctx, e);
  root.addEventListener('click', hClick);
  root.addEventListener('change', hChange);
  root.addEventListener('input', hInput);
  const onKey = (e) => { if (e.key === 'Escape') closeLightbox(); };
  document.addEventListener('keydown', onKey);
  const unbind = () => {
    root.removeEventListener('click', hClick); root.removeEventListener('change', hChange); root.removeEventListener('input', hInput);
    document.removeEventListener('keydown', onKey);
  };

  paintHead(ctx);
  let pres; let imgs;
  try {
    [pres, imgs] = await Promise.all([
      api.imagePresets(sku),
      api.productImages(sku).catch((err) => ({ registered: [], images: [], error: err })),
    ]);
  } catch (e) {
    if (stale()) { unbind(); return; }
    $('#im-body', root).innerHTML = errorState(e, 'im-retry');
    const r = $('#im-retry', root); if (r) r.onclick = () => { location.reload(); };
    return unbind;
  }
  if (stale()) { unbind(); return; }
  ctx.product = pres.product || { sku };
  ctx.presets = pres.presets || [];
  const o = ctx.options = pres.options || {};
  ctx.n = o.default_count || 3;
  ctx.size = o.default_size ?? 300;
  ctx.aspect = o.default_aspect || '1:1';
  ctx.text = o.default_text_mode || 'none';
  ctx.model = o.default_model || ((o.models || [])[0] || {}).id || '';
  if (ctx.product.has_image === false) ctx.base.mode = 'web';
  ctx.gallery = imgs;

  $('#im-body', root).innerHTML = `
    <div class="panel im-base" id="im-base"></div>
    <div class="im-main">
      <section class="panel im-preview" id="im-preview"></section>
      <section class="panel im-settings" id="im-settings"></section>
    </div>
    <div id="im-results"></div>
    <div id="im-saveres"></div>
    <div class="panel im-gallery" id="im-gallery"></div>
    <div class="im-savebar" id="im-savebar" hidden></div>`;
  paintHead(ctx);
  paintBase(ctx);
  paintPreview(ctx);
  paintSettings(ctx);
  paintResults(ctx);
  paintGallery(ctx);
  paintSavebar(ctx);
  if (imgs && imgs.error) toast('Could not load the image gallery: ' + imgs.error.message, 'warn', 5000);

  return () => {
    ctx.disposed = true;
    unbind();
    if (ctx.ctrl) ctx.ctrl.abort();
    if (ctx.uploadCtrl) ctx.uploadCtrl.abort();
    clearInterval(ctx.timer);
    closeLightbox();
    ctx.objectUrls.forEach((u) => URL.revokeObjectURL(u));
  };
}

// ── Helpers ───────────────────────────────────────────────────────────────
const bucketOf = (ctx) => (ctx.options && ctx.options.bucket) || (state.config && state.config.image_bucket) || BUCKET_DEFAULT;
function gcsPreviewUrl(ctx, uri) {
  if (isMock) return '';
  const m = String(uri || '').match(/^gs:\/\/([^/]+)\/(.+)$/);
  if (!m || m[1] !== bucketOf(ctx)) return '';
  return '/api/img/' + m[2].split('/').map(encodeURIComponent).join('/');
}
function presetById(ctx, id) { return ctx.presets.find((p) => p.id === id); }
function seasonIcon(ctx, row) {
  const p = row.preset && presetById(ctx, row.preset);
  return (p && p.icon) || (SEASON_META[row.season] || SEASON_META.all).icon;
}
function baseInfo(ctx) {
  const b = ctx.base; const p = ctx.product || {};
  switch (b.mode) {
    case 'registered': return p.has_image === false ? { url: '', label: 'No catalog image', ok: false } : { url: p.image_url, label: 'Catalog image', ok: !!p.image_url, src: p.image_url };
    case 'gcs': return { url: gcsPreviewUrl(ctx, b.gcs), label: 'GCS path', ok: /^gs:\/\/[^/]+\/.+/.test(b.gcs), src: b.gcs };
    case 'upload': return { url: b.localUrl || (b.upload && b.upload.url) || '', label: 'Uploaded image', ok: !!(b.upload && b.upload.uri), src: b.upload ? b.upload.uri : '' };
    case 'web': { const c = b.web && b.web.candidates ? b.web.candidates[b.webPick] : null; return { url: c ? c.url : '', label: 'Image found via web search', ok: !!c, src: c ? c.page_url || c.url : '', web: c }; }
    default: return { url: '', label: 'No base image (text only)', ok: true, none: true };
  }
}
function baseBody(ctx) {
  const b = ctx.base;
  switch (b.mode) {
    case 'registered': return { type: 'registered' };
    case 'gcs': return { type: 'gcs', uri: b.gcs.trim() };
    case 'upload': return { type: 'upload', uri: b.upload.uri };
    case 'web': return { type: 'web', uri: b.web.candidates[b.webPick].url };
    default: return { type: 'none' };
  }
}
const fidelityBadge = (s, note) => {
  if (s === null || s === undefined || isNaN(s)) return '';
  const pct = Math.round(Number(s) * 100);
  return Number(s) >= 0.7
    ? `<span class="fid fid-ok" title="${esc(note || 'Matches the original product')}">✔ Product match ${pct}%</span>`
    : `<span class="fid fid-warn" title="${esc(note || 'May differ from the original product')}">⚠ Needs review ${pct}%</span>`;
};
const sizeLabel = (r) => (r.width && r.height ? `${r.width}×${r.height}` : '');
async function copyText(text) {
  try { await navigator.clipboard.writeText(text); toast('Copied', 'ok', 1600); } catch (_) {
    const ta = document.createElement('textarea'); ta.value = text; document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); toast('Copied', 'ok', 1600); } catch (e) { toast('Could not copy', 'warn'); }
    ta.remove();
  }
}
const uriLine = (uri) => (uri ? `<div class="ic-uri"><span class="mono" title="${esc(uri)}">${esc(uri)}</span><button class="icon-btn ic-copy" data-act="copy" data-copy="${esc(uri)}" title="Copy GCS path" aria-label="Copy">⧉</button></div>` : '');

// ── Painting ──────────────────────────────────────────────────────────────
function paintHead(ctx) {
  const el = $('#im-head', ctx.root); if (!el) return;
  const fromList = ctx.list.find((x) => x.sku === ctx.sku) || {};
  const p = { ...fromList, ...(ctx.product || {}) };
  el.innerHTML = `
    <div class="imh-prod">
      <div class="imh-img">${p.image_url && p.has_image !== false ? `<img src="${esc(p.image_url)}" alt="" onerror="this.style.visibility='hidden'">` : '<span class="imh-noimg">No image</span>'}</div>
      <div class="imh-txt">
        <div class="imh-title" title="${esc(p.title || '')}">${esc(p.title || 'SKU ' + ctx.sku)}</div>
        <div class="imh-meta">${p.group_label ? `<span class="cat-pill">${esc(p.group_label)}</span>` : ''}${p.brand ? `<span>${esc(p.brand)}</span>` : ''}<span class="mono muted">SKU ${esc(ctx.sku)}</span>
          ${p.has_image === false ? '<span class="chip chip-warn">No catalog image</span>' : ''}</div>
      </div>
    </div>
    <div class="imh-pick">
      <label class="imh-lab" for="im-pick">Switch product</label>
      <div class="search small imh-search"><span class="search-ic">🔍</span>
        <input id="im-pick" list="im-plist" type="search" placeholder="Search by product name, brand or SKU" autocomplete="off"></div>
      <datalist id="im-plist">${ctx.list.map((x) => `<option value="${esc(x.sku)}" label="${esc(x.group_label || '')} | ${esc(x.title)}">${esc(x.group_label || '')} | ${esc(x.title)}</option>`).join('')}</datalist>
      <a class="btn btn-ghost btn-sm" href="#/studio/${encodeURIComponent(ctx.sku)}" title="Review attributes in Enrichment Studio">✨ Open in Studio</a>
    </div>`;
}

function paintBase(ctx) {
  const el = $('#im-base', ctx.root); if (!el) return;
  const b = ctx.base; const p = ctx.product || {};
  const seg = BASE_MODES.map(([k, ic, lab, note]) => `<button class="seg-btn ${b.mode === k ? 'on' : ''}" data-act="base-mode" data-mode="${k}" ${ctx.running ? 'disabled' : ''}>
      <span class="seg-ic">${ic}</span>${esc(lab)}${note ? `<span class="seg-note">${esc(note)}</span>` : ''}</button>`).join('');
  let body = '';
  if (b.mode === 'registered') {
    body = p.has_image === false
      ? `<div class="im-note warn">⚠️ This product has no catalog image. Find one with <b>Web search</b> or <b>Upload</b> one. <button class="btn btn-outline btn-sm" data-act="base-mode" data-mode="web">🔍 Switch to Web search</button></div>`
      : `<div class="im-note">✅ Uses the product image registered in the catalog (recommended). Only the background and styling change; the product's shape, color and logo are preserved.</div>`;
  } else if (b.mode === 'gcs') {
    body = `<div class="im-gcs-row"><input class="input mono" id="im-gcs" type="text" spellcheck="false" placeholder="gs://${esc(bucketOf(ctx))}/${esc(ctx.sku)}/base.jpg" value="${esc(b.gcs)}">
      <span class="muted small">Enter an image path in Cloud Storage (a location the app's service account can read)</span></div>`;
  } else if (b.mode === 'upload') {
    const pct = b.uploadPct;
    body = `<label class="dropzone im-drop" id="im-drop" for="im-file">
        <input type="file" id="im-file" accept=".jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp" hidden>
        <div class="dz-ic">🖼</div><div class="dz-title">Drag &amp; drop an image</div>
        <div class="dz-sub">or click to browse (JPG / PNG / WebP, up to 10 MB)</div>
        ${pct !== null ? `<div class="up-bar"><div class="up-fill" id="im-up-fill" style="width:${Math.round(pct * 100)}%"></div></div><div class="muted small" id="im-up-txt">Uploading… ${Math.round(pct * 100)}%</div>` : ''}
        ${b.upload && pct === null ? `<div class="dz-file">✓ ${esc(b.upload.name || 'Uploaded')}</div>${uriLine(b.upload.uri)}` : ''}
      </label>`;
  } else if (b.mode === 'web') {
    const w = b.web;
    const cands = (w && w.candidates) || [];
    body = `${p.has_image === false ? '<div class="im-note warn">⚠️ There is no catalog image, so we recommend finding a product image with Web search.</div>' : ''}
      <div class="im-web-act"><button class="btn btn-primary btn-sm ${b.webLoading ? 'loading' : ''}" data-act="web-search" ${b.webLoading || ctx.running ? 'disabled' : ''}>🔍 ${w ? 'Search again' : 'Find product images on the web'}</button>
        <span class="muted small">${w && w.query ? `Query: <b>${esc(w.query)}</b>` : 'Gemini uses Google Search to find product images on official manufacturer pages and similar sources (a few seconds to 20 s)'}</span></div>
      ${b.webLoading ? `<div class="im-web-grid">${Array.from({ length: 4 }, () => '<div class="skel im-web-skel"></div>').join('')}</div>` : ''}
      ${!b.webLoading && w ? (cands.length ? `<div class="im-web-grid">${cands.slice(0, 6).map((c, i) => `
        <div class="im-web-card ${b.webPick === i ? 'on' : ''}" data-act="web-pick" data-i="${i}" role="button" tabindex="0" title="Use this image as the base">
          <div class="iwc-img"><img src="${esc(c.url)}" alt="" loading="lazy" onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'muted small',textContent:'Could not load'}))"></div>
          <div class="iwc-t" title="${esc(c.title || '')}">${esc(c.title || c.url)}</div>
          <div class="iwc-m">${c.width && c.height ? `<span class="mono">${fmtInt(c.width)}×${fmtInt(c.height)}</span>` : ''}${c.page_url ? `<a href="${esc(c.page_url)}" target="_blank" rel="noopener noreferrer">Source page ↗</a>` : ''}</div>
          ${b.webPick === i ? '<span class="iwc-check">✓ Selected</span>' : ''}
        </div>`).join('')}</div>
        <div class="im-note rights">⚖️ <b>Rights check required</b>: confirm in advance that web images (such as manufacturer images) may be reused. The source URL is recorded when you save.</div>`
        : `<div class="im-note warn">No candidate images found. ${esc(w.message || '')} You can upload one or generate with "No base image".</div>`) : ''}`;
  } else {
    body = '<div class="im-note">✏️ Generates from product info and the theme (text) only, without a product photo. Generated images are labeled <b>"Illustrative"</b>.</div>';
  }
  el.innerHTML = `<div class="im-sec-h"><span class="im-step">1</span><h2>Choose a base image</h2><span class="muted small">The product photo used as the starting point</span></div>
    <div class="seg im-seg">${seg}</div><div class="im-base-body">${body}</div>`;
  // drag & drop
  const dz = $('#im-drop', el);
  if (dz) {
    ['dragenter', 'dragover'].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add('over'); }));
    ['dragleave', 'drop'].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove('over'); }));
    dz.addEventListener('drop', (e) => uploadBase(ctx, e.dataTransfer.files[0]));
  }
}

function paintPreview(ctx) {
  const el = $('#im-preview', ctx.root); if (!el) return;
  const bi = baseInfo(ctx);
  let inner;
  if (bi.url) inner = `<img src="${esc(bi.url)}" alt="Base image" data-act="zoom" data-src="${esc(bi.url)}" data-cap="${esc(bi.label)}" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'imp-ph',textContent:'Could not load the image'}))">`;
  else if (bi.none) inner = '<div class="imp-ph"><div class="imp-ph-ic">✏️</div>Generating from text only<br><span class="small">(generated images are labeled "Illustrative")</span></div>';
  else if (ctx.base.mode === 'gcs' && bi.ok) inner = `<div class="imp-ph"><div class="imp-ph-ic">☁️</div>Using the image in GCS<br><span class="small mono">${esc(ctx.base.gcs)}</span><br><span class="small">(preview available for the ${esc(bucketOf(ctx))} bucket only)</span></div>`;
  else inner = `<div class="imp-ph"><div class="imp-ph-ic">🖼</div>${ctx.base.mode === 'web' ? 'Pick one image from the search results' : ctx.base.mode === 'upload' ? 'Upload an image' : ctx.base.mode === 'gcs' ? 'Enter a gs:// path' : 'No base image'}</div>`;
  el.innerHTML = `<div class="im-sec-h"><h2>Base image</h2>${bi.ok ? '<span class="chip chip-ok">Ready</span>' : '<span class="chip chip-warn">Not selected</span>'}</div>
    <div class="imp-box">${inner}</div>
    <div class="imp-cap"><b>${esc(bi.label)}</b>${bi.web && bi.web.page_url ? ` • <a href="${esc(bi.web.page_url)}" target="_blank" rel="noopener noreferrer">Source ↗</a>` : ''}</div>
    ${bi.src && !bi.none ? `<div class="imp-src mono" title="${esc(bi.src)}">${esc(bi.src)}</div>` : ''}`;
}

function paintSettings(ctx) {
  const el = $('#im-settings', ctx.root); if (!el) return;
  const o = ctx.options;
  const segs = (act, items, cur) => items.map(([v, lab]) => `<button class="seg-btn sm ${String(v) === String(cur) ? 'on' : ''}" data-act="${act}" data-v="${esc(v)}">${esc(lab)}</button>`).join('');
  el.innerHTML = `
    <div class="im-sec-h"><span class="im-step">2</span><h2>Choose promotional themes</h2><span class="muted small" id="im-psel-count"></span></div>
    <div class="im-presets" id="im-presets"></div>
    <div class="im-psel" id="im-psel"></div>
    <div class="im-sec-h mt"><span class="im-step">3</span><h2>Fine-tune the output</h2></div>
    <label class="im-lab" for="im-extra">Additional instructions <span class="muted small">(optional)</span></label>
    <textarea class="input im-extra" id="im-extra" rows="2" placeholder="e.g. On a wooden dining table with plates for a family of four, in warm evening light">${esc(ctx.extra)}</textarea>
    <div class="im-opts">
      <div class="im-opt"><span class="im-lab">Images</span><div class="seg">${segs('count', (o.counts || [2, 3, 4]).map((n) => [n, String(n)]), ctx.n)}</div></div>
      <div class="im-opt"><label class="im-lab" for="im-size">Output size</label>
        <select class="select" id="im-size">${(o.sizes || [{ value: 300, label: '300×300' }]).map((s) => `<option value="${esc(s.value)}" ${Number(s.value) === Number(ctx.size) ? 'selected' : ''}>${esc(s.label)}</option>`).join('')}</select></div>
      <div class="im-opt"><span class="im-lab">Aspect ratio</span><div class="seg">${segs('aspect', (o.aspects || ['1:1']).map((a) => [a, a]), ctx.aspect)}</div></div>
      <div class="im-opt"><span class="im-lab">Text overlay</span><div class="seg">${segs('text', (o.text_modes || [{ id: 'none', label: 'No text' }]).map((t) => [t.id, t.label]), ctx.text)}</div></div>
      <div class="im-opt wide"><span class="im-lab">Model</span><div class="seg">${segs('model', (o.models || []).map((m) => [m.id, m.label]), ctx.model)}</div></div>
    </div>
    <div class="im-gen-row">
      <div class="im-gen-sum" id="im-gen-sum"></div>
      <button class="btn btn-primary btn-live im-gen-btn" data-act="generate" id="im-gen-btn"><span class="live-ic">✨</span><span class="live-txt">Generate images</span></button>
    </div>`;
  paintPresets(ctx);
  paintGenSummary(ctx);
}

function paintPresets(ctx) {
  const el = $('#im-presets', ctx.root); if (!el) return;
  const groups = {};
  ctx.presets.forEach((p) => { (groups[p.season || 'all'] = groups[p.season || 'all'] || []).push(p); });
  const order = Object.keys(groups).sort((a, b) => (SEASON_ORDER.indexOf(a) + 99) % 99 - (SEASON_ORDER.indexOf(b) + 99) % 99);
  el.innerHTML = order.map((s) => {
    const m = SEASON_META[s] || { label: groups[s][0].season_label || s, icon: '✨' };
    return `<div class="pz-group"><div class="pz-season s-${esc(s)}"><span>${m.icon}</span>${esc(groups[s][0].season_label || m.label)}</div>
      <div class="pz-list">${groups[s].map((p) => {
        const on = ctx.sel.has(p.id);
        if (!p.available) {
          return `<div class="pz na" title="${esc(p.reason || 'Not available for this product')}" aria-disabled="true">
            <div class="pz-top"><span class="pz-ic">${esc(p.icon || '')}</span><span class="pz-lab">${esc(p.label)}</span><span class="pz-lock">🔒</span></div>
            <div class="pz-sub">${esc(p.reason || 'Not available for this product')}</div></div>`;
        }
        return `<button class="pz ${on ? 'on' : ''}" data-act="preset" data-id="${esc(p.id)}" aria-pressed="${on}" ${ctx.running ? 'disabled' : ''}>
          <div class="pz-top"><span class="pz-ic">${esc(p.icon || '')}</span><span class="pz-lab">${esc(p.label)}</span><span class="pz-chk">${on ? '✓' : ''}</span></div>
          <div class="pz-sub">${p.evidence ? `Evidence: ${esc(p.evidence)}` : 'Available for all products'}</div></button>`;
      }).join('')}</div></div>`;
  }).join('') || emptyState('🎨', 'No themes available for this product', '');
  const nAvail = ctx.presets.filter((p) => p.available).length;
  const cnt = $('#im-psel-count', ctx.root);
  if (cnt) cnt.innerHTML = `Available themes <b>${nAvail}</b> / ${ctx.presets.length} (🔒 = the product data does not list that feature)`;
  // selected detail
  const ps = $('#im-psel', ctx.root);
  const sel = ctx.presets.filter((p) => ctx.sel.has(p.id));
  ps.innerHTML = sel.length
    ? sel.map((p) => `<div class="psel-item"><div class="psel-h">${esc(p.icon || '')} <b>${esc(p.label)}</b>${p.evidence ? `<span class="psel-ev">Evidence: ${esc(p.evidence)}</span>` : ''}</div>
        ${(p.facts || []).length ? `<div class="psel-facts">Specs reflected in the image: ${p.facts.map((f) => `<span class="kvp"><b>${esc(f.label)}</b> ${esc(f.value)}</span>`).join('')}</div>` : ''}</div>`).join('')
    : '<div class="psel-empty">👆 Choose at least one theme (multiple selections allowed)</div>';
}

function paintGenSummary(ctx) {
  const el = $('#im-gen-sum', ctx.root);
  const btn = $('#im-gen-btn', ctx.root);
  if (!el || !btn) return;
  const o = ctx.options;
  const sizeLab = ((o.sizes || []).find((s) => Number(s.value) === Number(ctx.size)) || {}).label || ctx.size;
  const mLab = ((o.models || []).find((m) => m.id === ctx.model) || {}).label || ctx.model;
  el.innerHTML = `<b>${ctx.n}</b> ${ctx.n === 1 ? 'image' : 'images'} • ${esc(sizeLab)} • ${esc(ctx.aspect)} • ${esc(String(mLab).replace(/\s*\(.*/, ''))}${ctx.sel.size ? ` • Themes <b>${ctx.sel.size}</b>` : ''}`;
  btn.disabled = ctx.running || !ctx.sel.size;
  btn.classList.toggle('running', ctx.running);
  $('.live-txt', btn).textContent = ctx.running ? 'Generating…' : 'Generate images';
}

function paintResults(ctx) {
  const el = $('#im-results', ctx.root); if (!el) return;
  const g = ctx.gen;
  if (!g) { el.innerHTML = ''; return; }
  const keys = [...g.cards.keys()].sort((a, b) => a - b);
  const done = keys.filter((k) => ['done', 'error', 'deleted'].includes(g.cards.get(k).state)).length;
  const head = g.error ? `<span class="chip chip-bad">Error</span> ${esc(g.error)}`
    : g.complete ? `✅ Generation complete — <b>${keys.filter((k) => g.cards.get(k).state === 'done').length}</b> images (${(g.complete.elapsed_ms / 1000).toFixed(1)} s)`
      : `<span class="live-dot"></span> Generating… <b>${done}</b> / ${keys.length} images`;
  el.innerHTML = `<div class="panel im-res">
    <div class="im-sec-h"><span class="im-step">4</span><h2>Results</h2><span class="im-res-st">${head}</span><span class="spacer"></span><span class="muted small mono" id="im-gen-elapsed"></span></div>
    <div class="im-res-meta">${g.model ? `<span class="kvp">Model <b>${esc(g.model)}</b></span>` : ''}<span class="kvp">Base <b>${esc(g.baseLabel)}</b></span>${(g.facts || []).map((f) => `<span class="kvp">${esc(f.label)} <b>${esc(f.value)}</b></span>`).join('')}</div>
    ${g.prompt ? `<details class="jsonview im-prompt"><summary>📝 Prompt used <span class="muted small">(only verified specs are passed)</span></summary><pre class="im-prompt-pre">${esc(g.prompt)}</pre></details>` : ''}
    <div class="ic-grid" id="im-res-grid">${keys.map((k) => resultCard(ctx, k)).join('')}</div>
    <div class="muted small im-res-hint">Click an image to enlarge it. Save the images you "Select" with the button below to register them as product images.</div>
  </div>`;
}
function paintCard(ctx, k) {
  const old = $(`#im-res-grid [data-k="${k}"]`, ctx.root);
  if (!old) { paintResults(ctx); return; }
  old.outerHTML = resultCard(ctx, k);
  const st = $('.im-res-st', ctx.root);
  if (st && !ctx.gen.complete) {
    const keys = [...ctx.gen.cards.keys()];
    const done = keys.filter((x) => ['done', 'error', 'deleted'].includes(ctx.gen.cards.get(x).state)).length;
    st.innerHTML = `<span class="live-dot"></span> Generating… <b>${done}</b> / ${keys.length} images`;
  }
}
function resultCard(ctx, k) {
  const c = ctx.gen.cards.get(k);
  if (c.state === 'deleted') return `<div class="ic-card gone" data-k="${k}"><div class="ic-gone">🗑 Deleted</div></div>`;
  if (c.state === 'done' && c.image) return imageCard(ctx, c.image, { k, imagine: ctx.gen.baseNone });
  if (c.state === 'error') return `<div class="ic-card err" data-k="${k}"><div class="ic-img ph"><div class="ic-err">⚠️<div>${esc(c.message || 'Generation failed')}</div></div></div><div class="ic-body"><span class="muted small">#${k}</span></div></div>`;
  const txt = c.state === 'checking' ? '🔎 Checking product match…' : c.state === 'generating' ? '🎨 Generating…' : '⏳ Waiting';
  return `<div class="ic-card pending" data-k="${k}"><div class="ic-img ph skel"><div class="ic-prog">${txt}</div></div>
    <div class="ic-body"><div class="skel" style="height:12px;width:60%;border-radius:6px"></div><div class="skel" style="height:10px;width:40%;border-radius:6px;margin-top:6px"></div></div></div>`;
}
function imageCard(ctx, r, { k = null, gallery = false, imagine = false } = {}) {
  const id = r.image_id;
  const isCand = r.status === 'candidate';
  const sel = ctx.selected.has(id);
  const prim = ctx.primary ? ctx.primary === id : !!r.is_primary;
  const cap = `${r.preset_label || ''} ${sizeLabel(r)}`;
  return `<div class="ic-card ${sel ? 'sel' : ''} ${prim ? 'prim' : ''}" ${k !== null ? `data-k="${k}"` : ''} data-id="${esc(id)}">
    <div class="ic-img" data-act="zoom" data-src="${esc(r.url)}" data-cap="${esc(cap)}" data-id="${esc(id)}" title="Click to enlarge">
      <img src="${esc(r.url)}" alt="${esc(r.preset_label || '')}" loading="lazy" onerror="this.style.opacity=.15">
      ${sizeLabel(r) ? `<span class="ic-size">${esc(sizeLabel(r))}</span>` : ''}
      ${imagine || (r.base_image_uri === '' && r.kind === 'generated') ? '<span class="ic-imagine">Illustrative</span>' : ''}
      ${prim ? '<span class="ic-star-b">⭐ Primary</span>' : ''}
    </div>
    <div class="ic-body">
      <div class="ic-row"><span class="ic-preset">${esc(seasonIcon(ctx, r))} ${esc(r.preset_label || r.preset || 'Generated image')}</span>${fidelityBadge(r.fidelity_score, r.fidelity_note)}</div>
      ${gallery ? `<div class="ic-row small">${r.status === 'saved' ? '<span class="chip chip-ok">Saved</span>' : '<span class="chip chip-warn">Candidate</span>'}<span class="muted">${fmtDate(r.created_at)}</span></div>` : ''}
      <div class="ic-acts">
        ${isCand ? `<label class="ic-chk"><input type="checkbox" data-sel="${esc(id)}" ${sel ? 'checked' : ''}> Select</label>` : '<span class="ic-saved">✓ Saved</span>'}
        <label class="ic-prim" title="Set as the product's primary image"><input type="radio" name="im-primary" data-prim="${esc(id)}" ${prim ? 'checked' : ''}> ⭐ Set as primary</label>
        <button class="icon-btn del" data-act="del" data-id="${esc(id)}" title="Delete" aria-label="Delete">🗑</button>
      </div>
      ${uriLine(r.gcs_uri)}
    </div>
  </div>`;
}

function paintSavebar(ctx) {
  const el = $('#im-savebar', ctx.root); if (!el) return;
  const anyCand = (ctx.gen && [...ctx.gen.cards.values()].some((c) => c.state === 'done' && c.image && c.image.status === 'candidate'))
    || ((ctx.gallery && ctx.gallery.images) || []).some((r) => r.status === 'candidate');
  el.hidden = !(anyCand || ctx.selected.size || ctx.primary);
  const n = ctx.selected.size;
  const prim = ctx.primary ? findRow(ctx, ctx.primary) : null;
  el.innerHTML = `<div class="isb-txt"><b>${n}</b> selected${prim ? ` • Primary: <b>${esc(prim.preset_label || prim.image_id)}</b>` : ''}</div>
    <label class="chk isb-chk"><input type="checkbox" id="im-aics" ${ctx.updateAics ? 'checked' : ''}><span>Also add to product images in the Cymbal source catalog (<span class="mono">${esc(((state.config && state.config.source_table) || 'cymbal.products').split('.').slice(-2).join('.'))}</span>) <span class="muted small">• daily backup created automatically</span></span></label>
    <button class="btn btn-primary btn-lg ${ctx.saving ? 'loading' : ''}" data-act="save" ${ctx.saving || ctx.running || (!n && !ctx.primary) ? 'disabled' : ''}>💾 Save selected images (${n})</button>`;
}

function paintSaveResult(ctx) {
  const el = $('#im-saveres', ctx.root); if (!el) return;
  const r = ctx.saveResult;
  if (!r) { el.innerHTML = ''; return; }
  const a = r.aics;
  el.innerHTML = `<div class="bq-card im-saved">
    <div class="bq-head"><span class="bq-ic">💾</span><div><div class="bq-title">Saved (${fmtInt((r.saved || []).length)} images)</div>
      <div class="bq-sub">Written to Cloud Storage, BigQuery${a && a.updated ? ' and the Cymbal source catalog' : ''}</div></div><span class="spacer"></span><button class="icon-btn" data-act="close-saveres" aria-label="Close">✕</button></div>
    <div class="isr-grid">
      <div class="isr-box"><div class="isr-k">☁️ Cloud Storage</div>${(r.saved || []).map((s) => uriLine(s.gcs_uri)).join('') || '<span class="muted small">—</span>'}</div>
      <div class="isr-box"><div class="isr-k">🗄 BigQuery</div>${r.bq ? `<div class="bq-n">${fmtInt(r.bq.rows)}<span>rows</span></div><div class="mono small isr-t" title="${esc(r.bq.table)}">${esc(r.bq.table)}</div>` : '<span class="muted small">—</span>'}</div>
      <div class="isr-box"><div class="isr-k">🛍 Cymbal source catalog</div>${a ? (a.updated ? `<div class="bq-n">${fmtInt(a.images_total)}<span>images (total for this product)</span></div><div class="mono small isr-t" title="${esc(a.table)}">${esc(a.table)}</div>${a.backup ? `<div class="small muted">Backup: <span class="mono">${esc(a.backup)}</span></div>` : ''}` : `<span class="chip chip-warn">Not updated</span> <span class="small">${esc(a.message || '')}</span>`) : '<span class="muted small">Update disabled</span>'}</div>
    </div></div>`;
}

function paintGallery(ctx) {
  const el = $('#im-gallery', ctx.root); if (!el) return;
  const g = ctx.gallery || { registered: [], images: [] };
  const shown = new Set(ctx.gen ? [...ctx.gen.cards.values()].filter((c) => c.image).map((c) => c.image.image_id) : []);
  const rows = (g.images || []).filter((r) => r.status !== 'deleted' && !shown.has(r.image_id));
  const nSaved = (g.images || []).filter((r) => r.status === 'saved').length;
  const nCand = (g.images || []).filter((r) => r.status === 'candidate').length;
  const reg = (g.registered || []).map((r) => `<div class="ic-card reg">
      <div class="ic-img" data-act="zoom" data-src="${esc(r.url)}" data-cap="${esc(r.label || 'Catalog image')}"><img src="${esc(r.url)}" alt="" loading="lazy" onerror="this.style.opacity=.15"><span class="ic-reg-b">Catalog</span></div>
      <div class="ic-body"><div class="ic-row"><span class="ic-preset">🖼 ${esc(r.label || 'Catalog image')}</span></div><div class="ic-uri"><span class="mono" title="${esc(r.url)}">${esc(r.url)}</span></div></div></div>`).join('');
  el.innerHTML = `<div class="im-sec-h"><h2>🗂 Image gallery for this product</h2>
      <span class="muted small">Catalog <b>${(g.registered || []).length}</b> • Saved <b>${nSaved}</b> • Candidates <b>${nCand}</b></span><span class="spacer"></span>
      <button class="btn btn-ghost btn-sm ${ctx.galleryLoading ? 'loading' : ''}" data-act="reload-gallery">↻ Refresh</button></div>
    ${reg || rows.length ? `<div class="ic-grid">${reg}${rows.map((r) => imageCard(ctx, r, { gallery: true })).join('')}</div>`
      : emptyState('🖼', 'No images yet', 'Generate and save images above and they will appear here')}`;
}

function findRow(ctx, id) {
  if (ctx.gen) for (const c of ctx.gen.cards.values()) if (c.image && c.image.image_id === id) return c.image;
  return ((ctx.gallery && ctx.gallery.images) || []).find((r) => r.image_id === id) || null;
}

// ── Lightbox ──────────────────────────────────────────────────────────────
let lightboxEl = null;
function openLightbox(ctx, src, cap, id) {
  closeLightbox();
  const r = id ? findRow(ctx, id) : null;
  lightboxEl = document.createElement('div');
  lightboxEl.className = 'lightbox';
  lightboxEl.innerHTML = `<div class="lb-inner"><button class="lb-x" aria-label="Close">✕</button>
    <img src="${esc(src)}" alt="">
    <div class="lb-cap"><b>${esc(cap || '')}</b>${r ? ` ${fidelityBadge(r.fidelity_score, r.fidelity_note)}${r.fidelity_note ? `<span class="lb-note">${esc(r.fidelity_note)}</span>` : ''}` : ''}</div></div>`;
  document.body.appendChild(lightboxEl);
  requestAnimationFrame(() => lightboxEl && lightboxEl.classList.add('show'));
  lightboxEl.addEventListener('click', (e) => { if (e.target === lightboxEl || e.target.closest('.lb-x')) closeLightbox(); });
}
function closeLightbox() { if (lightboxEl) { lightboxEl.remove(); lightboxEl = null; } }

// ── Events ────────────────────────────────────────────────────────────────
function onClick(ctx, e) {
  const t = e.target.closest('[data-act]');
  if (!t || !ctx.root.contains(t) || t.disabled) return;
  const act = t.dataset.act;
  switch (act) {
    case 'base-mode':
      if (ctx.running) return;
      ctx.base.mode = t.dataset.mode; paintBase(ctx); paintPreview(ctx); break;
    case 'web-search': webSearch(ctx); break;
    case 'web-pick': ctx.base.webPick = Number(t.dataset.i); paintBase(ctx); paintPreview(ctx); break;
    case 'preset': {
      const id = t.dataset.id;
      if (ctx.sel.has(id)) ctx.sel.delete(id); else ctx.sel.add(id);
      paintPresets(ctx); paintGenSummary(ctx); break;
    }
    case 'count': ctx.n = Number(t.dataset.v); segOn(t); paintGenSummary(ctx); break;
    case 'aspect': ctx.aspect = t.dataset.v; segOn(t); paintGenSummary(ctx); break;
    case 'text': ctx.text = t.dataset.v; segOn(t); break;
    case 'model': ctx.model = t.dataset.v; segOn(t); paintGenSummary(ctx); break;
    case 'generate': generate(ctx); break;
    case 'zoom': if (!e.target.closest('a')) openLightbox(ctx, t.dataset.src, t.dataset.cap, t.dataset.id); break;
    case 'del': confirmDelete(ctx, t.dataset.id); break;
    case 'save': save(ctx); break;
    case 'copy': e.preventDefault(); copyText(t.dataset.copy); break;
    case 'close-saveres': ctx.saveResult = null; paintSaveResult(ctx); break;
    case 'reload-gallery': reloadGallery(ctx, true); break;
    default: break;
  }
}
function segOn(btn) { $$('.seg-btn', btn.parentElement).forEach((b) => b.classList.toggle('on', b === btn)); }

function onChange(ctx, e) {
  const t = e.target;
  if (t.id === 'im-pick') {
    const v = t.value.trim();
    const hit = ctx.list.find((x) => x.sku === v) || ctx.list.find((x) => v && (x.title.includes(v) || `${x.group_label} | ${x.title}` === v));
    if (hit) location.hash = '#/images/' + encodeURIComponent(hit.sku);
    else if (v) toast('No matching product found', 'warn');
  } else if (t.id === 'im-gcs') { ctx.base.gcs = t.value.trim(); paintPreview(ctx); }
  else if (t.id === 'im-file') uploadBase(ctx, t.files[0]);
  else if (t.id === 'im-size') { ctx.size = Number(t.value); paintGenSummary(ctx); }
  else if (t.id === 'im-aics') ctx.updateAics = t.checked;
  else if (t.dataset.sel) {
    const id = t.dataset.sel;
    if (t.checked) ctx.selected.add(id); else ctx.selected.delete(id);
    $$(`[data-sel="${CSS.escape(id)}"]`, ctx.root).forEach((x) => { x.checked = t.checked; });
    $$(`.ic-card[data-id="${CSS.escape(id)}"]`, ctx.root).forEach((c) => c.classList.toggle('sel', t.checked));
    paintSavebar(ctx);
  } else if (t.dataset.prim) {
    ctx.primary = t.dataset.prim;
    refreshCards(ctx);
    paintSavebar(ctx);
  }
}
function onInput(ctx, e) {
  const t = e.target;
  if (t.id === 'im-extra') ctx.extra = t.value;
  else if (t.id === 'im-gcs') ctx.base.gcs = t.value.trim();
}
function refreshCards(ctx) {
  if (ctx.gen) [...ctx.gen.cards.keys()].forEach((k) => { if (ctx.gen.cards.get(k).state === 'done') paintCard(ctx, k); });
  paintGallery(ctx);
}

// ── Actions ───────────────────────────────────────────────────────────────
async function webSearch(ctx) {
  if (ctx.base.webLoading) return;
  ctx.base.webLoading = true; ctx.base.webPick = null; paintBase(ctx); paintPreview(ctx);
  try {
    const d = await api.searchImages(ctx.sku);
    if (ctx.disposed) return;
    ctx.base.web = d;
    if (!(d.candidates || []).length) toast('No candidate images found', 'warn');
  } catch (e) {
    if (ctx.disposed) return;
    toast('Web search failed: ' + e.message, 'err', 6000);
  } finally {
    if (!ctx.disposed) { ctx.base.webLoading = false; paintBase(ctx); paintPreview(ctx); }
  }
}

async function uploadBase(ctx, file) {
  if (!file) return;
  if (!UPLOAD_TYPES.includes(file.type) && !/\.(jpe?g|png|webp)$/i.test(file.name)) { toast('Choose a JPG, PNG or WebP image', 'warn'); return; }
  if (file.size > MAX_UPLOAD) { toast('File is too large (10 MB max)', 'warn'); return; }
  const local = URL.createObjectURL(file);
  ctx.objectUrls.push(local);
  ctx.base.localUrl = local; ctx.base.upload = null; ctx.base.uploadPct = 0;
  paintBase(ctx); paintPreview(ctx);
  ctx.uploadCtrl = new AbortController();
  try {
    const d = await api.uploadBaseImage(ctx.sku, file, (p) => {
      ctx.base.uploadPct = p;
      const f = $('#im-up-fill', ctx.root); if (f) f.style.width = Math.round(p * 100) + '%';
      const tx = $('#im-up-txt', ctx.root); if (tx) tx.textContent = `Uploading… ${Math.round(p * 100)}%`;
    }, ctx.uploadCtrl.signal);
    if (ctx.disposed) return;
    ctx.base.upload = { ...(d.base || {}), name: file.name };
    toast('Base image uploaded', 'ok');
  } catch (e) {
    if (e.name === 'AbortError' || ctx.disposed) return;
    ctx.base.localUrl = '';
    toast('Upload failed: ' + e.message, 'err', 6000);
  } finally {
    if (!ctx.disposed) { ctx.base.uploadPct = null; paintBase(ctx); paintPreview(ctx); }
  }
}

async function generate(ctx) {
  if (ctx.running) return;
  if (!ctx.sel.size) { toast('Choose at least one theme', 'warn'); return; }
  const bi = baseInfo(ctx);
  if (!bi.ok) {
    const msg = { registered: 'No catalog image. Choose Web search or Upload', gcs: 'Enter a GCS path that starts with gs://', upload: 'Upload an image', web: 'Pick one image from the web search results' }[ctx.base.mode];
    toast(msg || 'Choose a base image', 'warn', 5000);
    $('#im-base', ctx.root).scrollIntoView({ behavior: 'smooth', block: 'center' });
    return;
  }
  const body = {
    base: baseBody(ctx), presets: [...ctx.sel], extra_prompt: ctx.extra.trim(),
    n: ctx.n, aspect: ctx.aspect, text_mode: ctx.text, model: ctx.model, size: ctx.size,
  };
  ctx.running = true;
  ctx.ctrl = new AbortController();
  const cards = new Map();
  for (let i = 1; i <= ctx.n; i++) cards.set(i, { state: 'wait' });
  ctx.gen = { t0: performance.now(), cards, prompt: '', facts: [], model: '', complete: null, error: null, zero: null, baseLabel: bi.label, baseNone: !!bi.none };
  ctx.saveResult = null; paintSaveResult(ctx);
  paintResults(ctx); paintGenSummary(ctx); paintPresets(ctx); paintBase(ctx); paintGallery(ctx); paintSavebar(ctx);
  $('#im-results', ctx.root).scrollIntoView({ behavior: 'smooth', block: 'start' });
  ctx.timer = setInterval(() => {
    const el = $('#im-gen-elapsed', ctx.root);
    if (el && ctx.gen) el.textContent = `${((performance.now() - ctx.gen.t0) / 1000).toFixed(1)} s`;
  }, 200);
  const keyOf = (i) => {
    const g = ctx.gen;
    if (g.zero === null) g.zero = Number(i) === 0;
    const k = g.zero ? Number(i) + 1 : Number(i);
    if (!g.cards.has(k)) { g.cards.set(k, { state: 'wait' }); paintResults(ctx); }
    return k;
  };
  const onEvent = (ev) => {
    if (ctx.disposed) return;
    const g = ctx.gen;
    switch (ev.event) {
      case 'start': {
        g.prompt = ev.prompt || ''; g.facts = ev.facts || []; g.model = ev.model || body.model;
        if (ev.n && ev.n !== g.cards.size) { g.cards.clear(); for (let i = 1; i <= ev.n; i++) g.cards.set(i, { state: 'wait' }); }
        paintResults(ctx);
        break;
      }
      case 'progress': { const k = keyOf(ev.index); const c = g.cards.get(k); if (c.state !== 'done') { c.state = ev.status === 'checking' ? 'checking' : 'generating'; paintCard(ctx, k); } break; }
      case 'image': {
        const k = keyOf(ev.index);
        g.cards.set(k, { state: 'done', image: ev.image });
        if (ev.image && ev.image.fidelity_score >= 0.7) ctx.selected.add(ev.image.image_id); // pre-select good ones
        paintCard(ctx, k); paintSavebar(ctx);
        break;
      }
      case 'image_error': { const k = keyOf(ev.index); g.cards.set(k, { state: 'error', message: ev.message }); paintCard(ctx, k); break; }
      case 'complete': g.complete = ev; break;
      case 'error': g.error = ev.message || 'An error occurred'; toast(g.error, 'err', 7000); break;
      default: break;
    }
  };
  try {
    await api.generateImages(ctx.sku, body, onEvent, ctx.ctrl.signal);
    if (ctx.disposed) return;
    const ok = [...ctx.gen.cards.values()].filter((c) => c.state === 'done').length;
    if (ctx.gen.complete) toast(`Generated ${ok} ${ok === 1 ? 'image' : 'images'}. Select the best ones and save`, ok ? 'ok' : 'warn', 5000);
    else if (!ctx.gen.error) toast('Did not receive generation results', 'warn');
  } catch (e) {
    if (e.name === 'AbortError' || ctx.disposed) return;
    ctx.gen.error = e.message;
    toast('Image generation failed: ' + e.message, 'err', 7000);
  } finally {
    clearInterval(ctx.timer);
    if (!ctx.disposed) {
      ctx.running = false;
      ctx.gen.cards.forEach((c) => { if (!['done', 'error', 'deleted'].includes(c.state)) { c.state = 'error'; c.message = c.message || 'No response'; } });
      if (!ctx.gen.complete) ctx.gen.complete = ctx.gen.complete || { elapsed_ms: performance.now() - ctx.gen.t0 };
      paintResults(ctx); paintGenSummary(ctx); paintPresets(ctx); paintBase(ctx); paintSavebar(ctx);
      const el = $('#im-gen-elapsed', ctx.root); if (el) el.textContent = '';
      reloadGallery(ctx);
    }
  }
}

async function reloadGallery(ctx, notify) {
  ctx.galleryLoading = true; paintGallery(ctx);
  try {
    ctx.gallery = await api.productImages(ctx.sku);
  } catch (e) {
    if (!ctx.disposed && notify) toast('Could not load the gallery: ' + e.message, 'err');
  } finally {
    if (!ctx.disposed) { ctx.galleryLoading = false; paintGallery(ctx); paintSavebar(ctx); }
  }
}

function confirmDelete(ctx, id) {
  const r = findRow(ctx, id);
  if (!r) return;
  openModal('Delete this image?', `<div class="im-del">
      <img src="${esc(r.url)}" alt="">
      <div><b>${esc(r.preset_label || r.image_id)}</b><div class="small muted">${r.status === 'saved' ? 'This image is saved. It will be deleted from Cloud Storage and removed from the Cymbal source catalog images.' : 'The candidate image will be deleted from Cloud Storage.'}</div>
      <div class="mono small muted im-del-uri">${esc(r.gcs_uri || '')}</div></div></div>`, {
    okLabel: '🗑 Delete',
    onOk: async () => {
      const res = await api.deleteImage(ctx.sku, id);
      ctx.selected.delete(id);
      if (ctx.primary === id) ctx.primary = null;
      if (ctx.gen) ctx.gen.cards.forEach((c) => { if (c.image && c.image.image_id === id) c.state = 'deleted'; });
      if (ctx.gallery && ctx.gallery.images) ctx.gallery.images = ctx.gallery.images.filter((x) => x.image_id !== id);
      if (ctx.gen) paintResults(ctx);
      paintGallery(ctx); paintSavebar(ctx);
      toast(`Image deleted${res && res.aics && res.aics.updated ? ' (also removed from the Cymbal source catalog)' : ''}`, 'ok');
    },
  });
}

async function save(ctx) {
  if (ctx.saving || (!ctx.selected.size && !ctx.primary)) return;
  ctx.saving = true; paintSavebar(ctx);
  const body = { image_ids: [...ctx.selected], primary_id: ctx.primary || null, update_aics: ctx.updateAics };
  try {
    const res = await api.saveImages(ctx.sku, body);
    if (ctx.disposed) return;
    ctx.saveResult = res;
    const savedIds = new Set((res.saved || []).map((s) => s.image_id));
    if (ctx.gen) ctx.gen.cards.forEach((c) => {
      if (c.image) {
        const s = (res.saved || []).find((x) => x.image_id === c.image.image_id);
        if (s) c.image = s;
        if (body.primary_id) c.image.is_primary = c.image.image_id === body.primary_id;
      }
    });
    savedIds.forEach((id) => ctx.selected.delete(id));
    body.image_ids.forEach((id) => ctx.selected.delete(id));
    ctx.primary = null;
    paintSaveResult(ctx);
    if (ctx.gen) paintResults(ctx);
    toast(`Saved ${(res.saved || []).length} images${res.aics && res.aics.updated ? ' (Cymbal source catalog also updated)' : ''}`, 'ok', 5000);
    $('#im-saveres', ctx.root).scrollIntoView({ behavior: 'smooth', block: 'center' });
    reloadGallery(ctx);
  } catch (e) {
    if (!ctx.disposed) toast('Save failed: ' + e.message, 'err', 7000);
  } finally {
    if (!ctx.disposed) { ctx.saving = false; paintSavebar(ctx); }
  }
}
