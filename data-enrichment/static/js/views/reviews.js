// (4) Human-in-the-loop (HITL) review
import { api } from '../api.js?v=20261111q';
import { esc, $, $$, fmtInt, fmtScore, fmtValue, chip, confBar, skeleton, emptyState, errorState, toast, setReviewBadge, refreshShell, findEvidence, markText, tip, state } from '../ui.js?v=20261111q';
import { coerce } from './studio.js?v=20261111q';

function reasonKind(rec) {
  const r = rec.reason || '';
  if (/food[- ]safety rule/i.test(r)) return { icon: '🛡️', title: rec.value == null ? 'No allergen statement found (AI does not guess)' : 'Allergen evidence needs verification', cls: 'bad' };
  if (/title (says|contains|mentions).{0,6}frozen|implausible|unusual|exceed|does not match|doesn't match|mismatch/i.test(r)) return { icon: '📏', title: 'Possible rule conflict', cls: 'bad' };
  if ((rec.evidence_match ?? 1) === 0 || /not found|evidence/i.test(r)) return { icon: '🔍', title: 'Evidence not found in source', cls: 'bad' };
  if (/normaliz/i.test(r) || rec.value === null || rec.value === undefined) return { icon: '📐', title: 'Unit normalization (conversion to standard unit) failed', cls: 'warn' };
  if (/physical|limit|out of range|consisten/i.test(r)) return { icon: '📏', title: 'Possible rule conflict', cls: 'bad' };
  return { icon: '🤔', title: 'AI confidence below threshold', cls: 'warn' };
}

export async function render(root, _params, stale) {
  root.innerHTML = `
    <div class="page-head"><div><h1>HITL review ${tip('?', 'Human-in-the-loop: a person checks only the items the AI was not confident about')}</h1>
      <p class="sub">Review only the items the AI was unsure about. <span class="kbd">A</span> Approve <span class="kbd">E</span> Edit <span class="kbd">R</span> Reject <span class="kbd">J</span>/<span class="kbd">K</span> Navigate</p></div>
      <div class="rv-counter" id="rv-counter"></div></div>
    <div class="reviews"><aside class="panel rq" id="rq">${skeleton(6, 'skel-row')}</aside><section class="panel rv" id="rv">${skeleton(1, 'skel-panel')}</section></div>`;

  const ctx = { items: [], i: 0, busy: false, editing: false };
  try {
    const d = await api.reviews({});
    ctx.items = d.items || [];
  } catch (e) {
    if (stale()) return;
    $('.reviews', root).innerHTML = errorState(e, 'rv-retry');
    $('#rv-retry', root).onclick = () => render(root, _params, stale);
    return;
  }
  if (stale()) return;
  setReviewBadge(ctx.items.length);

  const paintQueue = () => {
    $('#rv-counter', root).innerHTML = `<b>${fmtInt(ctx.items.length)}</b> remaining`;
    const rq = $('#rq', root);
    if (!ctx.items.length) { rq.innerHTML = `<div class="rq-head">Review queue</div>` + emptyState('🎉', 'Nothing to review', 'Every item has been decided'); return; }
    rq.innerHTML = `<div class="rq-head">Review queue <span class="muted small">least confident first</span></div><div class="rq-list">` + ctx.items.map((it, i) => `
      <button class="rq-item ${i === ctx.i ? 'on' : ''}" data-i="${i}">
        <img src="${esc(it.product.image_url)}" alt="" onerror="this.style.visibility='hidden'">
        <div class="rq-txt"><div class="rq-attr">${esc(it.rec.label)} <span class="rq-val">${esc(fmtValue(it.rec.value))}${esc(it.rec.unit || '')}</span></div>
          <div class="rq-prod">${esc(it.product.title)}</div></div>
        <span class="rq-score conf-${it.rec.score >= 0.7 ? 'warn' : 'bad'}-text">${fmtScore(it.rec.score)}</span>
      </button>`).join('') + '</div>';
    $$('.rq-item', rq).forEach((b) => b.addEventListener('click', () => { ctx.i = +b.dataset.i; paint(); }));
    const on = $('.rq-item.on', rq); if (on) on.scrollIntoView({ block: 'nearest' });
  };

  const paintCard = () => {
    const rv = $('#rv', root);
    ctx.editing = false;
    if (!ctx.items.length) {
      rv.innerHTML = emptyState('✅', 'All caught up!', 'Every item in the review queue has been processed. See the results on the dashboard') + '<div class="center"><a class="btn btn-primary" href="#/dashboard">Go to dashboard</a></div>';
      return;
    }
    const it = ctx.items[ctx.i];
    const r = it.rec;
    const rk = reasonKind(r);
    const range = findEvidence(it.source_text || '', r.evidence);
    let srcHtml; let evNote = '';
    if (range) srcHtml = markText(it.source_text, range);
    else {
      const vr = r.raw_value ? findEvidence(it.source_text || '', String(r.raw_value)) : null;
      srcHtml = markText(it.source_text || '', vr, 'ev-mark weak');
      evNote = `<div class="ev-note inline ${vr ? 'warn' : 'bad'}">⚠ Evidence "${esc(r.evidence || '—')}" was not found in the source${vr ? ` (only the value "${esc(r.raw_value)}" matched)` : ''}</div>`;
    }
    rv.innerHTML = `
      <div class="rv-prod"><img src="${esc(it.product.image_url)}" alt="" onerror="this.style.visibility='hidden'">
        <div><div class="muted small">${esc(it.product.group_label)} • SKU ${esc(r.sku)} • ${ctx.i + 1} / ${ctx.items.length}</div>
        <a class="rv-title" href="#/studio/${encodeURIComponent(r.sku)}">${esc(it.product.title)}</a></div></div>
      <div class="rv-reason ${rk.cls}"><span class="rr-ic">${rk.icon}</span><div><div class="rr-title">Why it needs review: ${esc(rk.title)}</div><div class="rr-sub">${esc(r.reason || '')}</div></div></div>
      <div class="rv-main">
        <div class="rv-ai">
          <div class="sec-lab">${esc(r.kind === 'tag' ? 'Feature tag' : 'Attribute')}: <b>${esc(r.label)}</b> <span class="muted small">${esc(r.attr_key)}</span></div>
          <div class="rv-value"><span class="v ${r.value === null || r.value === undefined ? 'nullv' : ''}">${r.value === null || r.value === undefined ? (/food[- ]safety rule/i.test(r.reason || '') ? 'Not stated (verify)' : 'Could not convert') : esc(fmtValue(r.value))}</span><span class="u">${esc(r.unit || '')}</span></div>
          <div class="ac-raw">raw: ${esc(r.raw_value ?? '—')}${r.norm_note ? ` • ${esc(r.norm_note)}` : ''}</div>
          <div class="rv-score"><span class="muted small">Composite confidence</span>${confBar(r.score)}</div>
          <div class="rv-sub3"><span>Evidence match <b>${fmtScore(r.evidence_match)}</b></span><span>Self-reported confidence <b>${fmtScore(r.self_conf)}</b></span><span>Token probability <b>${fmtScore(r.token_conf)}</b></span></div>
          <div class="pop-ev"><span class="k">AI evidence</span>"${esc(r.evidence || '—')}"</div>
        </div>
        <div class="rv-src"><div class="sec-lab">Source text <span class="muted small">evidence highlighted</span></div>${evNote}<pre class="src-text" id="rv-src">${srcHtml}</pre></div>
      </div>
      <div class="rv-actions">
        <button class="btn btn-ok btn-lg" data-act="approve">✓ Approve <span class="kbd">A</span></button>
        <button class="btn btn-outline btn-lg" data-act="edit">✎ Edit &amp; approve <span class="kbd">E</span></button>
        <button class="btn btn-bad btn-lg" data-act="reject">✕ Reject <span class="kbd">R</span></button>
        <span class="spacer"></span>
        <button class="btn btn-ghost" data-act="prev">‹ Prev <span class="kbd">K</span></button>
        <button class="btn btn-ghost" data-act="next">Next › <span class="kbd">J</span></button>
      </div>
      <div class="edit-row big" id="rv-edit" hidden><span class="muted">Correct value:</span><input class="input" id="rv-edit-in" value="${esc(r.value ?? '')}"><span class="muted">${esc(r.unit || '')}</span>
        <button class="btn btn-primary" data-act="edit-save">Save &amp; approve <span class="kbd">Enter</span></button><button class="btn btn-ghost" data-act="edit-cancel">Cancel <span class="kbd">Esc</span></button></div>`;
    const m = $('#rv-src mark', rv); if (m) m.scrollIntoView({ block: 'center' });
    $$('[data-act]', rv).forEach((b) => b.addEventListener('click', () => handle(b.dataset.act)));
    $('#rv-edit-in', rv).addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); handle('edit-save'); }
      if (e.key === 'Escape') { e.preventDefault(); handle('edit-cancel'); }
    });
  };

  const paint = () => { paintQueue(); paintCard(); };

  async function act(action, value) {
    if (ctx.busy || !ctx.items.length) return;
    ctx.busy = true;
    const it = ctx.items[ctx.i];
    $$('#rv [data-act]', root).forEach((b) => { b.disabled = true; });
    try {
      const body = { action, comment: '' };
      if (action === 'edit') body.value = value;
      const res = await api.review(it.rec.sku, it.rec.attr_key, body);
      const lab = { approve: 'Approved', edit: 'Edited and approved', reject: 'Rejected' }[action];
      toast(`${lab} "${it.rec.label}"`, action === 'reject' ? 'warn' : 'ok');
      ctx.items.splice(ctx.i, 1);
      if (ctx.i >= ctx.items.length) ctx.i = Math.max(0, ctx.items.length - 1);
      setReviewBadge(typeof res.remaining === 'number' ? res.remaining : ctx.items.length);
      state.products = null;
      $('#rv', root).classList.add('swipe');
      setTimeout(() => { $('#rv', root)?.classList.remove('swipe'); }, 250);
      paint();
      refreshShell();
    } catch (e) {
      toast(e.message, 'err');
      $$('#rv [data-act]', root).forEach((b) => { b.disabled = false; });
    } finally { ctx.busy = false; }
  }

  function handle(a) {
    if (!ctx.items.length) return;
    const rv = $('#rv', root);
    if (a === 'approve') act('approve');
    else if (a === 'reject') act('reject');
    else if (a === 'edit') { ctx.editing = true; $('#rv-edit', rv).hidden = false; const inp = $('#rv-edit-in', rv); inp.focus(); inp.select(); }
    else if (a === 'edit-cancel') { ctx.editing = false; $('#rv-edit', rv).hidden = true; }
    else if (a === 'edit-save') act('edit', coerce($('#rv-edit-in', rv).value, ctx.items[ctx.i].rec.value));
    else if (a === 'next') { ctx.i = Math.min(ctx.items.length - 1, ctx.i + 1); paint(); }
    else if (a === 'prev') { ctx.i = Math.max(0, ctx.i - 1); paint(); }
  }

  const onKey = (e) => {
    if (e.target.matches('input, textarea, select') || e.metaKey || e.ctrlKey || e.altKey) return;
    if (document.querySelector('.modal-back')) return;
    const k = e.key.toLowerCase();
    const map = { a: 'approve', e: 'edit', r: 'reject', j: 'next', k: 'prev', arrowdown: 'next', arrowup: 'prev' };
    if (map[k]) { e.preventDefault(); handle(map[k]); }
  };
  document.addEventListener('keydown', onKey);
  paint();
  return () => document.removeEventListener('keydown', onKey);
}
