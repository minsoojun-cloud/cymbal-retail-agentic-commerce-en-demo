// ① Dashboard
import { api } from '../api.js?v=20261111q';
import { esc, fmtInt, fmtPct, fmtUsd, usdOf, tip, skeleton, errorState, state, setReviewBadge, stepper } from '../ui.js?v=20261111q';

export async function render(root, _params, stale) {
  root.innerHTML = `
    <div class="page-head"><div><h1>Dashboard</h1><p class="sub">See the results of Gemini product attribute enrichment at a glance</p></div>
    <button class="btn btn-outline" id="dash-reload">↻ Refresh</button></div>
    <div class="kpis">${skeleton(4, 'skel-kpi')}</div>
    <div class="grid-2">${skeleton(2, 'skel-panel')}</div>`;
  const load = async () => {
    let d;
    try { d = await api.dashboard(); } catch (e) {
      if (stale()) return;
      root.querySelector('.kpis').outerHTML = errorState(e, 'dash-retry');
      const r = root.querySelector('#dash-retry'); if (r) r.onclick = () => render(root, _params, stale);
      return;
    }
    if (stale()) return;
    state.dashboard = d;
    setReviewBadge(d.totals.needs_review);
    if (!stepper.live) stepper.setGlobal(d.funnel);
    paint(root, d);
  };
  root.querySelector('#dash-reload').onclick = load;
  await load();
}

function paint(root, d) {
  const fr = d.fill_rate || {};
  const delta = (fr.after || 0) - (fr.before || 0);
  const cost = d.cost || {};
  const t = d.totals || {};
  const f = d.funnel || {};
  const thr = (state.config && state.config.threshold) || 0.9;

  const kpis = `
  <div class="kpis">
    <div class="kpi kpi-hero">
      <div class="kpi-label">${tip('Attribute fill rate', 'Share of required attributes per product (width, weight, volume, etc.) that have a value')}</div>
      <div class="kpi-ba">
        <span class="kpi-before">${t.processed ? fmtPct(fr.before) : '—'}</span>
        <span class="kpi-arrow">➜</span>
        <span class="kpi-after">${t.processed ? fmtPct(fr.after) : '—'}</span>
      </div>
      <div class="kpi-foot">${t.processed ? `<span class="delta up">▲ ${(delta * 100).toFixed(0)} pt improvement</span>` : '<span>Shown after enrichment</span>'}<span class="muted">${fmtInt(t.processed)} / ${fmtInt(t.skus)} SKUs processed</span></div>
    </div>
    <div class="kpi">
      <div class="kpi-label">${tip('Auto-approved rate', `Share of attributes with an AI score of ${thr.toFixed(2)} or higher, accepted as-is without human review`)}</div>
      <div class="kpi-big">${fmtPct(d.auto_approve_rate)}</div>
      <div class="donut-row">${donut(d.auto_approve_rate)}<div class="kpi-foot col"><span><b>${fmtInt(t.auto_approved)}</b> auto-approved</span><span class="muted">of ${fmtInt(t.attributes)} attributes + ${fmtInt(t.tags)} tags</span></div></div>
    </div>
    <a class="kpi kpi-link" href="#/reviews">
      <div class="kpi-label">${tip('Needs review', 'Items the AI was not confident about. A person (HITL) reviews them to approve, edit or reject.')}</div>
      <div class="kpi-big warn-text">${fmtInt(t.needs_review)}<span class="kpi-unit">items</span></div>
      <div class="kpi-foot"><span class="muted">Human-approved ${fmtInt(t.human_approved)} / rejected ${fmtInt(t.human_rejected)}</span><span class="go">Review now →</span></div>
    </a>
    <div class="kpi">
      <div class="kpi-label">${tip('Cost per SKU (list price)', 'Official Vertex AI standard (online) list price × measured tokens (3 calls: attributes, tags, GMC; thinking tokens included). Batch discounts are not included.')}</div>
      <div class="kpi-big">${usdOf(cost, 'per_sku') != null ? fmtUsd(usdOf(cost, 'per_sku')) : '<span class="muted">Not measured</span>'}</div>
      <div class="kpi-foot col"><span class="nowrap mono small">${esc(cost.model || '')}</span>
      <span class="muted">${cost.skus_measured ? `Average of ${fmtInt(cost.skus_measured)} measured SKUs` : 'Shown after a live run or sample measurement'}</span><a class="go" href="#/batch">Estimate by model →</a></div>
    </div>
  </div>`;
  const banner = !t.processed ? `<div class="banner">🚀 <div><b>No enrichment results yet.</b> Click "▶ Run live with Gemini" in Enrichment Studio, or submit a bulk job from Batch & Cost.</div><a class="btn btn-primary btn-sm" href="#/studio">Open Studio</a></div>` : '';

  // Funnel
  const fmax = Math.max(f.extracted || 0, 1);
  const frow = (label, n, cls, sub = '') => `<div class="fn-row"><div class="fn-lab">${label}</div><div class="fn-track">${n ? `<div class="fn-bar ${cls}" style="width:${Math.max(2, (n / fmax) * 100)}%"><span>${fmtInt(n)}</span></div>` : '<div class="fn-zero">0</div>'}</div><div class="fn-sub">${sub}</div></div>`;
  const funnel = `
  <div class="panel">
    <div class="panel-head"><h2>Decision funnel</h2><span class="muted">What happened to attributes extracted from ${fmtInt(f.source_skus)} SKUs</span></div>
    <div class="funnel">
      ${frow('② Extracted attributes', f.extracted, 'fn-blue', '')}
      ${frow('③ Normalized OK', f.normalized, 'fn-blue2', fmtPct(f.normalized / fmax))}
      ${frow('④ Passed Gate 1', f.gate1_passed, 'fn-blue3', fmtPct(f.gate1_passed / fmax))}
      <div class="fn-split">
        <div class="fn-split-lab">Gate 2 results</div>
        <div class="fn-stack">
          ${(f.auto_approved || f.needs_review || f.rejected) ? seg(f.auto_approved, 'ok', 'Auto-approved') + seg(f.needs_review, 'warn', 'Needs review') + seg(f.rejected, 'bad', 'Rejected') : '<div class="fn-seg seg-empty">No decision data yet</div>'}
        </div>
        <div class="fn-legend">
          <span><i class="lg ok"></i>🟢 Auto-approved <b>${fmtInt(f.auto_approved)}</b></span>
          <a href="#/reviews"><i class="lg warn"></i>🟡 Needs review <b>${fmtInt(f.needs_review)}</b></a>
          <span><i class="lg bad"></i>🔴 Rejected <b>${fmtInt(f.rejected)}</b></span>
        </div>
      </div>
      <div class="fn-golden">⑤ Golden records <b>${fmtInt(f.golden_skus)}</b> SKUs <span class="muted">(${fmtPct((f.golden_skus || 0) / Math.max(f.source_skus || 1, 1))})</span></div>
    </div>
  </div>`;

  function seg(n, cls, lab) {
    const tot = (f.auto_approved || 0) + (f.needs_review || 0) + (f.rejected || 0) || 1;
    const w = (n || 0) / tot * 100;
    if (!n) return '';
    return `<div class="fn-seg seg-${cls}" style="flex:${Math.max(w, 0.8)}" title="${esc(lab)} ${fmtInt(n)}">${w > 7 ? fmtInt(n) : ''}</div>`;
  }

  // By group
  const groups = (d.by_group || []).map((g) => `
    <div class="bg-row">
      <div class="bg-name"><b>${esc(g.label)}</b><span class="muted">${fmtInt(g.processed)}/${fmtInt(g.skus)} SKU</span></div>
      <div class="bg-bars">
        <div class="bg-bar"><div class="bg-fill before" style="width:${(g.fill_before || 0) * 100}%"></div><span>${fmtPct(g.fill_before)}</span></div>
        <div class="bg-bar"><div class="bg-fill after" style="width:${(g.fill_after || 0) * 100}%"></div><span class="strong">${fmtPct(g.fill_after)}</span></div>
      </div>
      <div class="bg-meta">${g.processed ? `<span class="delta up">+${(((g.fill_after || 0) - (g.fill_before || 0)) * 100).toFixed(0)}pt</span>` : '<span class="muted">Not processed</span>'}<span class="muted">Auto-approved ${fmtPct(g.auto_rate)} • tags ${Number(g.avg_tags_after || 0).toFixed(1)}/SKU</span></div>
    </div>`).join('');
  const byGroup = `
  <div class="panel">
    <div class="panel-head"><h2>Attribute fill rate by category</h2><span class="legend"><i class="lg before"></i>Before (source) <i class="lg after"></i>After (enriched)</span></div>
    <div class="bygroup">${groups || '<div class="muted">No data</div>'}</div>
  </div>`;

  const gates = `
  <div class="panel gates-panel">
    <div class="panel-head"><h2>3-stage gates <span class="muted small">— quality control that doesn't leave everything to AI</span></h2></div>
    <div class="gates">
      <div class="gate-card g1"><div class="gate-no">Gate 1</div><div class="gate-title">Rule checks</div><div class="gate-desc">Catches physically impossible values (e.g., a 50 kg pack of fish fillets) and contradictions (title says "Frozen" but storage is refrigerated)</div><div class="gate-stat">Rejected <b>${fmtInt(f.rejected)}</b></div></div>
      <div class="gate-arrow">➜</div>
      <div class="gate-card g2"><div class="gate-no">Gate 2</div><div class="gate-title">AI score ≥ ${thr.toFixed(2)}</div><div class="gate-desc">Auto-approves using a combined score of evidence match, AI self-confidence and token probability</div><div class="gate-stat">Auto-approved <b>${fmtInt(f.auto_approved)}</b></div></div>
      <div class="gate-arrow">➜</div>
      <a class="gate-card g3" href="#/reviews"><div class="gate-no">Gate 3</div><div class="gate-title">Human-in-the-loop (HITL) review</div><div class="gate-desc">Reviewers check only items below the score threshold or without evidence in the source</div><div class="gate-stat">Needs review <b>${fmtInt(f.needs_review)}</b></div></a>
    </div>
  </div>`;

  root.innerHTML = root.querySelector('.page-head').outerHTML + banner + kpis + `<div class="grid-2">${funnel}${byGroup}</div>` + gates;
  root.querySelector('#dash-reload').onclick = () => render(root, [], () => false);
  requestAnimationFrame(() => root.classList.add('ready'));
}

function donut(r) {
  const v = Math.max(0, Math.min(1, r || 0));
  const C = 2 * Math.PI * 22;
  return `<svg class="donut" viewBox="0 0 56 56"><circle cx="28" cy="28" r="22" class="d-bg"/><circle cx="28" cy="28" r="22" class="d-fg" stroke-dasharray="${(C * v).toFixed(1)} ${C.toFixed(1)}" transform="rotate(-90 28 28)"/></svg>`;
}
