// (3) Enrichment Studio — compare source (Before) vs. enriched (After), evidence highlighting, live run
import { api } from '../api.js?v=20261111q';
import {
  esc, $, $$, fmtInt, fmtPct, fmtScore, fmtValue, chip, confBar, scoreCls, tip, skeleton, errorState, emptyState,
  toast, state, productList, stepper, refreshShell, findEvidence, markText, STATUS, fmtJpy, fmtUsd, usdOf,
  modelOverrideHtml, runModel, modelLabel,
} from '../ui.js?v=20261111q';

const FIELD_LABEL = { title: 'Title', description: 'Description', attributes: 'Source attributes', '': '—' };

export async function render(root, params, stale) {
  let sku = params[0];
  let list = [];
  try { list = await productList(); } catch (e) { /* keep empty */ }
  if (stale()) return;
  if (!sku) {
    if (!list.length) { root.innerHTML = emptyState('📦', 'No products yet', 'Load catalog data and try again'); return; }
    location.replace('#/studio/' + encodeURIComponent(list[0].sku));
    return;
  }
  const idx = list.findIndex((p) => p.sku === sku);
  const prev = idx > 0 ? list[idx - 1] : null;
  const next = idx >= 0 && idx < list.length - 1 ? list[idx + 1] : null;

  root.innerHTML = `
    <div class="studio-top">
      <div class="st-nav">
        <a class="btn btn-ghost btn-sm ${prev ? '' : 'disabled'}" ${prev ? `href="#/studio/${encodeURIComponent(prev.sku)}"` : ''} title="Previous product">‹ Prev</a>
        <select id="st-select" class="select" aria-label="Select a product">
          ${list.map((p) => `<option value="${esc(p.sku)}" ${p.sku === sku ? 'selected' : ''}>${esc(p.group_label)} | ${esc(p.title)}</option>`).join('')}
          ${idx < 0 ? `<option selected value="${esc(sku)}">SKU ${esc(sku)}</option>` : ''}
        </select>
        <a class="btn btn-ghost btn-sm ${next ? '' : 'disabled'}" ${next ? `href="#/studio/${encodeURIComponent(next.sku)}"` : ''} title="Next product">Next ›</a>
        <span class="muted small">${idx >= 0 ? `${idx + 1} / ${list.length}` : ''}</span>
      </div>
      <div class="st-actions">
        <span id="st-status"></span>
        <a class="btn btn-outline btn-sm" href="#/images/${encodeURIComponent(sku)}" title="Generate seasonal campaign images for this product">🎨 Seasonal images</a>
        <a class="btn btn-outline btn-sm" href="#/gmc/${encodeURIComponent(sku)}" title="View GMC conversational attributes">🛍 GMC attributes</a>
        ${modelOverrideHtml('st-model', { compact: true })}
        <button class="btn btn-primary btn-live" id="btn-live"><span class="live-ic">▶</span><span class="live-txt">Run live with Gemini</span></button>
      </div>
    </div>
    <div class="studio">
      <section class="panel src" id="src">${skeleton(1, 'skel-panel')}</section>
      <section class="panel aft" id="aft">${skeleton(1, 'skel-panel')}</section>
    </div>`;
  $('#st-select', root).addEventListener('change', (e) => { location.hash = '#/studio/' + encodeURIComponent(e.target.value); });

  const ctx = { root, sku, detail: null, pinned: null, ctrl: null, running: false, popover: null, timer: null };

  try {
    ctx.detail = await api.product(sku);
  } catch (e) {
    if (stale()) return;
    $('.studio', root).innerHTML = errorState(e, 'st-retry');
    const r = $('#st-retry', root); if (r) r.onclick = () => render(root, params, stale);
    return;
  }
  if (stale()) return;
  paintAll(ctx);
  $('#btn-live', root).addEventListener('click', () => liveRun(ctx));

  const onDocClick = (e) => {
    if (ctx.popover && !ctx.popover.contains(e.target) && !e.target.closest('.acard, .tagpill')) closePopover(ctx);
  };
  const onKey = (e) => { if (e.key === 'Escape') closePopover(ctx); };
  document.addEventListener('mousedown', onDocClick);
  document.addEventListener('keydown', onKey);
  const onScroll = () => closePopover(ctx);
  $('#aft', root).addEventListener('scroll', onScroll, { passive: true });

  return () => {
    document.removeEventListener('mousedown', onDocClick);
    document.removeEventListener('keydown', onKey);
    closePopover(ctx);
    if (ctx.ctrl) ctx.ctrl.abort();
    clearInterval(ctx.timer);
    if (ctx.running || stepper.live) stepper.endLive();
  };
}

// ── Painting ───────────────────────────────────────────────────────────────
function paintAll(ctx) {
  paintSource(ctx);
  paintAfter(ctx);
  paintStatus(ctx);
}

function paintStatus(ctx) {
  const d = ctx.detail;
  $('#st-status', ctx.root).innerHTML = `${chip(d.status)} <span class="ba-mini">Fill rate <b class="b">${fmtPct(d.fill_before)}</b> ➜ <b class="a">${fmtPct(d.fill_after)}</b></span>`;
}

function paintSource(ctx) {
  const { product: p } = ctx.detail;
  const attrs = p.raw_attributes || {};
  const keys = Object.keys(attrs);
  $('#src', ctx.root).innerHTML = `
    <div class="panel-head sticky"><h2><span class="ba-tag before">Before</span> Source catalog</h2>
      <span class="muted small">Product master (raw)</span></div>
    <div class="ev-note" id="ev-note" hidden></div>
    <div class="src-scroll" id="src-scroll">
      <div class="src-prod">
        <div class="src-img"><img src="${esc(p.image_url)}" alt="" onerror="this.style.visibility='hidden'"></div>
        <div class="src-info">
          <div class="src-title" data-src="title">${esc(p.title)}</div>
          <div class="src-meta">
            <span><span class="k">Brand</span>${esc(p.brand || '—')}</span>
            <span><span class="k">Price</span>${p.price ? fmtJpy(p.price) : '—'}</span>
            <span><span class="k">SKU</span>${esc(p.sku)}</span>
          </div>
          <div class="src-cat">${esc(p.category || p.group_label || '')}</div>
          ${p.product_url && p.product_url !== '#' ? `<a class="ext" href="${esc(p.product_url)}" target="_blank" rel="noopener">View product page ↗</a>` : ''}
        </div>
      </div>
      <div class="src-sec"><div class="src-lab">Description <span class="muted small">description</span></div>
        <div class="src-desc" data-src="description">${esc(p.description || '(no description)')}</div></div>
      <div class="src-sec"><div class="src-lab">Source attributes <span class="muted small">raw_attributes • ${keys.length} ${keys.length === 1 ? 'field' : 'fields'}</span></div>
        ${keys.length ? `<table class="raw-table"><tbody>${keys.map((k) => `<tr data-row="${esc(k)}"><th>${esc(k)}</th><td data-src="attributes" data-key="${esc(k)}">${esc(fmtRaw(attrs[k]))}</td></tr>`).join('')}</tbody></table>`
          : '<div class="muted">No source attributes</div>'}
      </div>
    </div>`;
  // Register raw texts for highlight re-rendering
  ctx.srcEls = $$('[data-src]', $('#src', ctx.root)).map((el) => ({ el, field: el.dataset.src, text: el.dataset.src === 'description' && !p.description ? '' : el.textContent }));
}

function fmtRaw(v) {
  if (v === null || v === undefined) return '';
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

function recordsByKey(d) {
  const m = {};
  (d.records || []).forEach((r) => { if (r.kind !== 'tag') m[r.attr_key] = r; });
  return m;
}

function paintAfter(ctx, { flash = false } = {}) {
  const d = ctx.detail;
  const byKey = recordsByKey(d);
  const targets = d.targets || [];
  const seen = new Set(targets.map((t) => t.key));
  const extra = Object.values(byKey).filter((r) => !seen.has(r.attr_key)).map((r) => ({ key: r.attr_key, label: r.label, unit: r.unit, type: typeof r.value }));
  const all = [...targets, ...extra];
  const tags = (d.records || []).filter((r) => r.kind === 'tag');
  const cnt = { ok: 0, warn: 0, bad: 0 };
  (d.records || []).forEach((r) => { const c = (STATUS[r.gate_status] || {}).cls; if (c === 'ok' || c === 'info') cnt.ok++; else if (c === 'warn') cnt.warn++; else if (c === 'bad' || c === 'dark') cnt.bad++; });
  const filled = all.filter((t) => byKey[t.key]).length;
  const ex = d.extras || {};

  $('#aft', ctx.root).innerHTML = `
    <div class="panel-head sticky"><h2><span class="ba-tag after">After</span> Enriched</h2>
      <div class="aft-sum"><span class="cnt ok">🟢 ${cnt.ok}</span><span class="cnt warn">🟡 ${cnt.warn}</span><span class="cnt bad">🔴 ${cnt.bad}</span>
      <span class="muted small">${filled}/${all.length} attributes extracted</span></div></div>
    <div class="aft-scroll" id="aft-scroll">
      <div class="livelog" id="livelog" hidden></div>
      <div class="hint">💡 Hover over a card to highlight the source text the AI used as evidence in <mark class="ev-mark">yellow</mark>. Click to see the confidence breakdown.</div>
      <div class="agrid ${flash ? 'flash' : ''}">
        ${all.map((t) => attrCard(t, byKey[t.key])).join('') || emptyState('🧩', 'No target attributes')}
      </div>
      <div class="aft-sec"><div class="sec-lab">Feature tags ${tip('?', 'Only features with evidence in the source are tagged, using the standard tag dictionary. Useful for search filters')}</div>
        <div class="tags big">${tags.length ? tags.map((r) => `<button class="tagpill st-${(STATUS[r.gate_status] || {}).cls || 'mute'}" data-key="${esc(r.attr_key)}"><span>${esc(r.label)}</span><span class="tp-score">${fmtScore(r.score)}</span></button>`).join('') : '<span class="muted">No tags</span>'}</div></div>
      <div class="aft-sec two">
        <div><div class="sec-lab">Search keywords <span class="muted small">AI-generated</span></div>
          <div class="kw">${(ex.search_keywords || []).map((k) => `<span class="kwpill">🔍 ${esc(k)}</span>`).join('') || '<span class="muted">—</span>'}</div></div>
        <div><div class="sec-lab">AI summary <span class="muted small">AI-generated</span></div>
          <div class="summary">${ex.ai_summary ? esc(ex.ai_summary) : '<span class="muted">—</span>'}</div></div>
      </div>
      <details class="jsonview">
        <summary>{ } Generated JSON ${tip('responseSchema', 'Tells Gemini to answer in a JSON shape you specify. Instead of free text, you get structured data that can be ingested into systems as is')} <span class="muted small">${(d.records || []).length} records</span></summary>
        <div class="json-actions"><button class="btn btn-ghost btn-sm" id="copy-json">Copy</button></div>
        <pre class="json" id="json-pre">${jsonHtml(d.records || [])}</pre>
      </details>
    </div>`;
  bindAfter(ctx);
}

function attrCard(t, r) {
  if (!r) {
    return `<div class="acard missing" data-key="${esc(t.key)}"><div class="ac-head"><span class="ac-label">${esc(t.label)}</span>${chip('missing')}</div>
      <div class="ac-val"><span class="v">—</span><span class="u">${esc(t.unit || '')}</span></div><div class="ac-raw">Not in source</div><div class="conf"><div class="conf-track"></div><span class="conf-num">—</span></div></div>`;
  }
  const cls = (STATUS[r.gate_status] || {}).cls || 'mute';
  return `<div class="acard st-${cls}" data-key="${esc(r.attr_key)}" tabindex="0">
    <div class="ac-head"><span class="ac-label">${esc(r.label || t.label)}</span>${chip(r.gate_status)}</div>
    <div class="ac-val ${r.gate_status === 'rejected' || r.gate_status === 'human_rejected' ? 'struck' : ''}"><span class="v">${r.value === null || r.value === undefined ? '<span class="nullv">Could not convert</span>' : esc(fmtValue(r.value, t.type))}</span><span class="u">${esc(r.unit || t.unit || '')}</span></div>
    <div class="ac-raw" title="${esc(r.raw_value)}">raw: ${esc(r.raw_value ?? '—')}</div>
    ${confBar(r.score)}
  </div>`;
}

function jsonHtml(obj) {
  const s = JSON.stringify(obj, null, 2);
  return esc(s)
    .replace(/(&quot;[^&]*?&quot;)(\s*:)/g, '<span class="jk">$1</span>$2')
    .replace(/(:\s*)(&quot;.*?&quot;)/g, '$1<span class="js">$2</span>')
    .replace(/(:\s*)(-?\d+(?:\.\d+)?|true|false|null)/g, '$1<span class="jn">$2</span>');
}

function findRec(ctx, key) { return (ctx.detail.records || []).find((r) => r.attr_key === key); }

function bindAfter(ctx) {
  const aft = $('#aft', ctx.root);
  $$('.acard:not(.missing), .tagpill', aft).forEach((el) => {
    const key = el.dataset.key;
    el.addEventListener('mouseenter', () => { if (!ctx.pinned) highlight(ctx, findRec(ctx, key)); });
    el.addEventListener('focus', () => { if (!ctx.pinned) highlight(ctx, findRec(ctx, key)); });
    el.addEventListener('mouseleave', () => { if (!ctx.pinned) clearHighlight(ctx); });
    el.addEventListener('click', () => {
      if (ctx.pinned === key) { closePopover(ctx); return; }
      pin(ctx, key, el);
    });
    el.addEventListener('keydown', (e) => { if (e.key === 'Enter') el.click(); });
  });
  $$('.acard.missing', aft).forEach((el) => el.addEventListener('mouseenter', () => { if (!ctx.pinned) clearHighlight(ctx); }));
  const cp = $('#copy-json', aft);
  if (cp) cp.onclick = (e) => {
    e.preventDefault();
    const txt = JSON.stringify(ctx.detail.records || [], null, 2);
    (navigator.clipboard ? navigator.clipboard.writeText(txt) : Promise.reject()).then(() => toast('JSON copied', 'ok')).catch(() => toast('Could not copy', 'warn'));
  };
}

// ── Evidence highlight ────────────────────────────────────────────────────
function clearHighlight(ctx) {
  (ctx.srcEls || []).forEach((s) => { if (s.marked) { s.el.innerHTML = esc(s.text); s.marked = false; } s.el.closest('tr')?.classList.remove('row-hit'); });
  const n = $('#ev-note', ctx.root); if (n) n.hidden = true;
}

function highlight(ctx, rec) {
  clearHighlight(ctx);
  if (!rec) return;
  const note = $('#ev-note', ctx.root);
  const els = [...(ctx.srcEls || [])].sort((a, b) => (b.field === rec.source_field) - (a.field === rec.source_field));
  const hits = [];
  let mode = null;
  for (const s of els) {
    const r = findEvidence(s.text, rec.evidence);
    if (r) { s.el.innerHTML = markText(s.text, r); s.marked = true; hits.push(s); mode = mode || r.mode; }
  }
  let valueOnly = false;
  if (!hits.length && rec.raw_value !== null && rec.raw_value !== undefined && String(rec.raw_value).length >= 2) {
    for (const s of els) {
      const r = findEvidence(s.text, String(rec.raw_value));
      if (r) { s.el.innerHTML = markText(s.text, r, 'ev-mark weak'); s.marked = true; hits.push(s); valueOnly = true; }
    }
  }
  hits.forEach((s) => s.el.closest('tr')?.classList.add('row-hit'));
  if (hits.length) {
    const first = hits[0];
    const mark = first.el.querySelector('mark') || first.el;
    const sc = $('#src-scroll', ctx.root);
    if (sc) {
      const mr = mark.getBoundingClientRect(); const cr = sc.getBoundingClientRect();
      if (mr.top < cr.top + 20 || mr.bottom > cr.bottom - 20) sc.scrollTo({ top: sc.scrollTop + (mr.top - cr.top) - cr.height / 3, behavior: 'smooth' });
    }
    if (valueOnly) {
      note.className = 'ev-note warn';
      note.innerHTML = `⚠ Evidence "${esc(rec.evidence || '—')}" was not found in the source. Only the value "${esc(rec.raw_value)}" matched (${esc(FIELD_LABEL[first.field] || first.field)})`;
    } else {
      note.className = 'ev-note ok';
      note.innerHTML = `📍 <b>${esc(rec.label)}</b> evidence: ${esc(FIELD_LABEL[first.field] || first.field)} • ${mode === 'exact' ? 'exact match' : 'fuzzy match (spelling variants normalized)'}`;
    }
  } else {
    note.className = 'ev-note bad';
    note.innerHTML = `⚠ Evidence not found in source — the AI's evidence "${esc(rec.evidence || '(none)')}" does not exist in the source, so human review is required`;
  }
  note.hidden = false;
}

function pin(ctx, key, el) {
  closePopover(ctx, { keepHighlight: true });
  ctx.pinned = key;
  $$('.acard.pinned, .tagpill.pinned', ctx.root).forEach((x) => x.classList.remove('pinned'));
  el.classList.add('pinned');
  const rec = findRec(ctx, key);
  highlight(ctx, rec);
  openPopover(ctx, rec, el);
}

function closePopover(ctx, { keepHighlight = false } = {}) {
  if (ctx.popover) { ctx.popover.remove(); ctx.popover = null; }
  if (!keepHighlight) {
    ctx.pinned = null;
    $$('.acard.pinned, .tagpill.pinned', ctx.root).forEach((x) => x.classList.remove('pinned'));
    clearHighlight(ctx);
  }
}

// ── Detail popover (score breakdown + HITL) ────────────────────────────────
function openPopover(ctx, rec, anchor) {
  if (!rec) return;
  const w = (state.config && state.config.weights) || { evidence: 0.5, self: 0.3, token: 0.2 };
  const noTok = rec.token_conf === null || rec.token_conf === undefined;
  const wn = (state.config && state.config.weights_no_token) || { evidence: 0.6, self: 0.4 };
  const we = noTok ? wn.evidence : w.evidence, ws = noTok ? wn.self : w.self, wt = noTok ? 0 : w.token;
  const thr = (state.config && state.config.threshold) || 0.9;
  const comp = [
    ['Evidence match', 'evidence_match', rec.evidence_match, we, 'Whether the evidence text cited by the AI actually exists in the source (1.0 exact / 0.8 spelling variant / 0.5 value only / 0 none)'],
    ['Self-reported confidence', 'self_conf', rec.self_conf, ws, 'Confidence reported by Gemini itself'],
    ['Token probability', 'token_conf', noTok ? null : rec.token_conf, wt, 'Computed from per-token probabilities (logprobs) during generation. If unavailable, the weight is redistributed'],
  ];
  const rows = comp.map(([lab, key, v, wt2, help]) => {
    const has = v !== null && v !== undefined;
    return `<div class="sb-row"><div class="sb-lab">${tip(lab, help)}<span class="muted small">${key}</span></div>
      <div class="sb-bar"><div class="sb-fill conf-${scoreCls(v)}" style="width:${has ? v * 100 : 0}%"></div></div>
      <div class="sb-v">${has ? fmtScore(v) : '—'}</div><div class="sb-w">×${wt2 ? wt2.toFixed(1) : '—'}</div><div class="sb-c">${has && wt2 ? '+' + (v * wt2).toFixed(3) : '—'}</div></div>`;
  }).join('');
  const gateLab = rec.gate === 'gate1' ? 'Gate 1 (rules)' : rec.gate === 'gate2' ? 'Gate 2 (AI confidence)' : (rec.gate || '—');
  const needs = rec.gate_status === 'needs_review';
  const pop = document.createElement('div');
  pop.className = 'popover';
  pop.innerHTML = `
    <div class="pop-head"><div><div class="pop-title">${esc(rec.label)}</div><div class="muted small">${esc(rec.attr_key)}</div></div>${chip(rec.gate_status)}<button class="icon-btn pop-x" aria-label="Close">✕</button></div>
    <div class="pop-val"><span class="v">${esc(fmtValue(rec.value))}</span><span class="u">${esc(rec.unit || '')}</span><span class="pop-raw">← raw "${esc(rec.raw_value ?? '—')}"</span></div>
    ${rec.norm_note ? `<div class="pop-note">🔧 Normalized: ${esc(rec.norm_note)}</div>` : ''}
    <div class="pop-ev"><span class="k">Evidence</span>"${esc(rec.evidence || '—')}"<span class="muted small">(${esc(FIELD_LABEL[rec.source_field] || rec.source_field || '—')})</span></div>
    <div class="pop-sec">Confidence breakdown ${noTok ? `<span class="muted small">(no token probability → weights redistributed to ${we.toFixed(1)} / ${ws.toFixed(1)})</span>` : ''}</div>
    <div class="sb">${rows}
      <div class="sb-total"><span>Composite confidence</span>${confBar(rec.score)}<span class="muted small">Threshold ${thr.toFixed(2)}</span></div></div>
    <div class="pop-gate gate-${(STATUS[rec.gate_status] || {}).cls || 'mute'}"><b>${esc(gateLab)}</b> ${esc(rec.reason || '')}</div>
    ${rec.reviewed ? `<div class="muted small">Human review: ${esc(rec.reviewed.action)} ${esc(rec.reviewed.comment || '')}</div>` : ''}
    ${needs ? `<div class="pop-hitl"><div class="pop-sec">Review this item</div>
      <div class="hitl-btns"><button class="btn btn-ok btn-sm" data-act="approve">✓ Approve</button><button class="btn btn-outline btn-sm" data-act="edit">✎ Edit &amp; approve</button><button class="btn btn-bad btn-sm" data-act="reject">✕ Reject</button></div>
      <div class="edit-row" hidden><input class="input" id="pop-edit" value="${esc(rec.value ?? '')}"><span class="muted">${esc(rec.unit || '')}</span><button class="btn btn-primary btn-sm" data-act="edit-save">Save</button></div></div>` : ''}
    <div class="pop-foot muted small">model ${esc(rec.model || '—')} • job ${esc(rec.job_id || '—')}</div>`;
  document.body.appendChild(pop);
  ctx.popover = pop;
  // position: keep the popover over the After panel so the highlighted evidence (left) stays visible
  const r = anchor.getBoundingClientRect();
  const aft = $('#aft', ctx.root).getBoundingClientRect();
  const pw = pop.offsetWidth, ph = pop.offsetHeight;
  const minLeft = Math.min(aft.left + 8, window.innerWidth - pw - 8);
  let left;
  if (r.right + 12 + pw <= window.innerWidth - 8) left = r.right + 12;
  else if (r.left - 12 - pw >= minLeft) left = r.left - 12 - pw;
  else left = Math.max(minLeft, Math.min(r.left, window.innerWidth - pw - 8));
  const top = Math.max(70, Math.min(r.top - 10, window.innerHeight - ph - 12));
  pop.style.left = left + 'px';
  pop.style.top = top + 'px';
  requestAnimationFrame(() => pop.classList.add('show'));
  $('.pop-x', pop).onclick = () => closePopover(ctx);
  if (needs) {
    const act = async (action, value) => {
      $$('.hitl-btns button, [data-act="edit-save"]', pop).forEach((b) => { b.disabled = true; });
      try {
        const body = { action, comment: 'studio' };
        if (action === 'edit') body.value = value;
        const res = await api.review(rec.sku, rec.attr_key, body);
        Object.assign(rec, res.record || {});
        toast(`${{ approve: 'Approved', edit: 'Edited and approved', reject: 'Rejected' }[action]} "${rec.label}" (${fmtInt(res.remaining)} remaining)`, 'ok');
        closePopover(ctx);
        paintAfter(ctx);
        state.products = null;
        refreshShell();
      } catch (e) {
        toast(e.message, 'err');
        $$('.hitl-btns button, [data-act="edit-save"]', pop).forEach((b) => { b.disabled = false; });
      }
    };
    $$('[data-act]', pop).forEach((b) => b.addEventListener('click', () => {
      const a = b.dataset.act;
      if (a === 'edit') { $('.edit-row', pop).hidden = false; $('#pop-edit', pop).focus(); return; }
      if (a === 'edit-save') { act('edit', coerce($('#pop-edit', pop).value, rec.value)); return; }
      act(a);
    }));
    $('#pop-edit', pop).addEventListener('keydown', (e) => { if (e.key === 'Enter') act('edit', coerce(e.target.value, rec.value)); });
  }
}

export function coerce(v, orig) {
  const s = String(v).trim();
  if (typeof orig === 'number') { const n = Number(s.replace(/,/g, '')); return isNaN(n) ? s : n; }
  if (typeof orig === 'boolean') return /^(true|1|y|yes|on)$/i.test(s);
  return s;
}

// ── Live run (POST + SSE) ──────────────────────────────────────────────────
async function liveRun(ctx) {
  if (ctx.running) return;
  closePopover(ctx);
  ctx.running = true;
  const btn = $('#btn-live', ctx.root);
  btn.disabled = true; btn.classList.add('running');
  const t0 = performance.now();
  const setBtn = () => { $('.live-txt', btn).textContent = `Running Gemini… ${((performance.now() - t0) / 1000).toFixed(1)}s`; };
  setBtn();
  ctx.timer = setInterval(setBtn, 100);
  const log = $('#livelog', ctx.root);
  log.hidden = false;
  log.innerHTML = `<div class="ll-head"><span class="live-dot"></span>Live run log <span class="muted small">SKU ${esc(ctx.sku)}</span></div><div class="ll-body" id="ll-body"></div>`;
  $('#aft-scroll', ctx.root).scrollTop = 0;
  const body = $('#ll-body', log);
  const line = (html, cls = '') => {
    const t = ((performance.now() - t0) / 1000).toFixed(1).padStart(5, ' ');
    body.insertAdjacentHTML('beforeend', `<div class="ll ${cls}"><span class="ll-t">+${t}s</span><span class="ll-m">${html}</span></div>`);
    body.scrollTop = body.scrollHeight;
  };
  stepper.startLive(ctx.sku);
  $$('.agrid, .tags.big', ctx.root).forEach((x) => x.classList.add('stale'));
  const done = { stage1: null, stage3: null };
  let gotResult = false;
  let current = 0;
  ctx.ctrl = new AbortController();
  const model = runModel(ctx.root, 'st-model');
  const onEvent = (ev) => {
    if (!ctx.running) return;
    switch (ev.event) {
      case 'start':
        stepper.setState(0, 'done'); stepper.setCount(0, '<b>1</b> SKU loaded');
        line(`▶ Started • model <b class="mono" data-start-model>${esc(ev.model || '')}</b> (${esc(modelLabel(ev.model))})`);
        break;
      case 'stage': {
        const s = ev.stage;
        if ((s === 'stage1' || s === 'stage3') && ev.status === 'running') {
          current = 1; stepper.setState(1, 'running'); stepper.setCount(1, 'Extracting…');
          line(s === 'stage1' ? '(2) Stage 1: extracting attributes (with responseSchema)…' : '(2) Stage 3: assigning feature tags…', 'run');
        } else if ((s === 'stage1' || s === 'stage3') && ev.status === 'done') {
          done[s] = ev;
          line(s === 'stage1'
            ? `✓ Stage 1 done • <b>${fmtInt(ev.count)}</b> attributes • ${fmtInt(ev.latency_ms)} ms • input ${fmtInt(ev.input_tokens)} / output ${fmtInt(ev.output_tokens)} tokens`
            : `✓ Stage 3 done • <b>${fmtInt(ev.count)}</b> tags • ${fmtInt(ev.latency_ms)} ms`, 'ok');
          if (done.stage1 && done.stage3) {
            stepper.setState(1, 'done'); stepper.setCount(1, `<b>${fmtInt(done.stage1.count)}</b> attributes + <b>${fmtInt(done.stage3.count)}</b> tags`);
            current = 2; stepper.setState(2, 'running'); stepper.setCount(2, 'Normalizing…');
          }
        } else if (s === 'normalize') {
          if (!(done.stage1 && done.stage3)) { stepper.setState(1, 'done'); }
          stepper.setState(2, 'done');
          const ok = (ev.count || 0) - (ev.failed || 0);
          stepper.setCount(2, `<b>${fmtInt(ok)}</b>/${fmtInt(ev.count)} OK`);
          line(`✓ (3) Unit normalization • ${fmtInt(ok)}/${fmtInt(ev.count)} OK${ev.failed ? ` • <span class="warn-text">${fmtInt(ev.failed)} could not be converted</span>` : ''}`, 'ok');
          current = 3; stepper.setState(3, 'running'); stepper.setCount(3, 'Scoring…');
        } else if (s === 'gate') {
          const m = ev.summary || {};
          stepper.setState(2, 'done');
          stepper.setState(3, 'done');
          stepper.setCount(3, `<span class="sc-ok">Auto <b>${fmtInt(m.auto_approved)}</b></span> <span class="sc-warn">Review <b>${fmtInt(m.needs_review)}</b></span>${m.rejected ? ` <span class="sc-bad">Rejected <b>${fmtInt(m.rejected)}</b></span>` : ''}`);
          line(`✓ (4) Gating • 🟢 Auto-approved ${fmtInt(m.auto_approved)} • 🟡 Needs review ${fmtInt(m.needs_review)} • 🔴 Rejected ${fmtInt(m.rejected)}`, 'ok');
          current = 4; stepper.setState(4, 'running'); stepper.setCount(4, 'Saving…');
        }
        break;
      }
      case 'result':
        gotResult = true;
        STEPS_DONE();
        stepper.setCount(4, '<b>1</b> SKU updated');
        line(`🏁 (5) Golden record updated (total ${((performance.now() - t0) / 1000).toFixed(1)} s)`, 'ok strong');
        if (ev.model) {
          line(`🤖 Model used <b class="mono" data-used-model="${esc(ev.model)}">${esc(ev.model)}</b>${ev.fallback ? ` <span class="warn-text">(fell back because ${esc(ev.requested_model)} is busy)</span>` : ''} • 3 calls total: input ${fmtInt(ev.input_tokens)} / output ${fmtInt(ev.output_tokens)} / thinking ${fmtInt(ev.thinking_tokens)} tokens • list-price cost ${usdOf(ev, 'cost') != null ? fmtUsd(usdOf(ev, 'cost')) : 'see the official pricing page'}`, 'ok');
        }
        if (ev.detail) {
          ctx.detail = ev.detail;
          const keepLog = log.innerHTML;
          paintSource(ctx);
          paintAfter(ctx, { flash: true });
          paintStatus(ctx);
          const nl = $('#livelog', ctx.root); nl.hidden = false; nl.innerHTML = keepLog; nl.classList.add('finished'); const nb = $('.ll-body', nl); if (nb) nb.scrollTop = nb.scrollHeight;
        }
        break;
      case 'error':
        stepper.setState(current, 'error');
        line(`⛔ Error: ${esc(ev.message || '')}`, 'err');
        toast('Live run error: ' + (ev.message || ''), 'err', 6000);
        break;
      default:
        break;
    }
  };
  function STEPS_DONE() { [0, 1, 2, 3, 4].forEach((i) => stepper.setState(i, 'done')); }
  try {
    await api.enrich(ctx.sku, onEvent, ctx.ctrl.signal, model);
    if (gotResult) toast('Live run complete', 'ok');
    else if (ctx.running) { stepper.setState(current, 'error'); line('⚠ No result received', 'err'); }
  } catch (e) {
    if (e.name !== 'AbortError' && ctx.running) {
      stepper.setState(current, 'error');
      line(`⛔ ${esc(e.message)}`, 'err');
      toast('Live run failed: ' + e.message, 'err', 6000);
    }
  } finally {
    clearInterval(ctx.timer);
    $$('.agrid.stale, .tags.stale', ctx.root).forEach((x) => x.classList.remove('stale'));
    if (ctx.running) {
      ctx.running = false;
      const b = $('#btn-live', ctx.root);
      if (b) { b.disabled = false; b.classList.remove('running'); $('.live-txt', b).textContent = 'Run live again'; }
      const ll = $('.ll-head .live-dot', ctx.root); if (ll) ll.classList.add('off');
      state.products = null;
      refreshShell();
      stepper.endLive(10000);
    }
  }
}
