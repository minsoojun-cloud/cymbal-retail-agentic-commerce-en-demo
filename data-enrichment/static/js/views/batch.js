// ⑤ Batch & Cost — Vertex AI Batch jobs + per-model cost estimate at list price (standard online)
import { api, goldenCsvUrl, isMock, PRICING_URL } from '../api.js?v=20261111q';
import {
  esc, $, $$, fmtInt, fmtPct, fmtDate, fmtValue, skeleton, emptyState, errorState, toast, openModal, state, tip,
  refreshShell, selectedModel, modelLabel, modelInfo, fmtUsd, usdOf,
} from '../ui.js?v=20261111q';

const JOB_STATE = {
  JOB_STATE_SUCCEEDED: ['SUCCEEDED', 'ok', 'Completed'],
  JOB_STATE_RUNNING: ['RUNNING', 'run', 'Running'],
  JOB_STATE_PENDING: ['PENDING', 'pend', 'Pending'],
  JOB_STATE_QUEUED: ['QUEUED', 'pend', 'Queued'],
  JOB_STATE_FAILED: ['FAILED', 'bad', 'Failed'],
  JOB_STATE_CANCELLED: ['CANCELLED', 'mute', 'Cancelled'],
  JOB_STATE_EXPIRED: ['EXPIRED', 'mute', 'Expired'],
};
const MIN = 100, MAX = 1000000;
const toSkus = (pos) => { const v = Math.pow(10, Math.log10(MIN) + (Math.log10(MAX) - Math.log10(MIN)) * pos / 1000); return roundNice(v); };
const toPos = (n) => Math.max(0, Math.min(1000, Math.round((Math.log10(Math.max(n, 1)) - Math.log10(MIN)) / (Math.log10(MAX) - Math.log10(MIN)) * 1000)));
function roundNice(v) { const p = Math.pow(10, Math.max(0, Math.floor(Math.log10(v)) - 1)); return Math.round(v / p) * p; }
const BASIS_KEY = 'cymbal.enrich.pricebasis.v2';   // 'list' (default) | 'promo' (reference only)
let simSkus = 8807;
const autoTried = new Set();         // models auto-measured in this session (auto on first view)
const measuring = {};                // model -> { stages: {stage1,stage3,gmc}, sku, title, error }
const STAGE_LABEL = { stage1: 'Attributes', stage3: 'Tags', gmc: 'GMC' };

const basisPref = () => (localStorage.getItem(BASIS_KEY) === 'promo' ? 'promo' : 'list');
const priceUrl = () => (state.config && state.config.pricing_url) || PRICING_URL;

export async function render(root, _params, stale) {
  const retrieved = (state.config && state.config.price_retrieved_at) || '';
  root.innerHTML = `
    <div class="page-head"><div><h1>Batch &amp; Cost</h1><p class="sub">Batch Prediction status and per-model cost estimates at <b>list price (standard online)</b> — measured tokens × official Vertex AI pricing</p></div>
      <div class="head-actions"><button class="btn btn-outline" id="b-refresh">↻ Refresh status</button><button class="btn btn-primary" id="b-new">+ New batch job</button></div></div>

    <div class="panel cost" id="cost-panel">
      <div class="panel-head cost-head">
        <h2>Cost estimate by model (list price) ${tip('?', 'Average tokens of products actually processed by each model (3 calls: attributes, tags, GMC; thinking tokens included) × official Vertex AI standard (online) rates. Excludes Batch / Flex / Priority pricing, discounts, cache discounts and taxes.')}</h2>
        <div class="cost-meta">
          <span class="chip chip-info" id="basis-chip">List price (no promotions) • no Batch discount</span>
          <a class="price-link hdr" href="${esc(priceUrl())}" target="_blank" rel="noopener" id="price-link-hdr">Official pricing page ↗</a>
          <span class="muted small" id="price-date">Prices retrieved <b>${esc(retrieved)}</b></span>
        </div>
      </div>
      <div class="cost-ctrl">
        <div class="cc-skus">
          <div class="cc-lab">SKUs to estimate</div>
          <div class="sim-skus"><input class="input sim-num" id="sim-num" type="text" inputmode="numeric" value="${fmtInt(simSkus)}" aria-label="Number of SKUs"><span>SKUs</span></div>
          <input type="range" min="0" max="1000" step="1" id="sim-range" value="${toPos(simSkus)}" aria-label="SKU count slider">
          <div class="sim-scale"><span>100</span><span>1K</span><span>10K</span><span>100K</span><span>1M</span></div>
          <div class="sim-presets" id="sim-presets"></div>
        </div>
        <div class="cc-opts">
          <div class="cc-lab">Pricing basis ${tip('?', 'By default we use the list price from the official Standard pricing table, with no limited-time promotions (credit-backs or other discounts). Turn on "Reference: limited-time promo pricing" to recalculate with the promo (discounted) prices listed on the official page as of today.')}</div>
          <div class="basis-row"><span class="basis-list" id="basis-list">List price (no promotions)</span>
            <label class="chk promo-toggle" title="Reference estimate at discounted prices (off by default)"><input type="checkbox" id="promo-toggle"><span>Reference: limited-time promo pricing <span class="mb mb-warn">Discount</span></span></label></div>
        </div>
      </div>
      <div id="cost-hero" class="cost-hero">${skeleton(1, 'skel-row')}</div>
      <div id="cost-table">${skeleton(4, 'skel-row')}</div>
      <div id="cost-notes" class="cost-notes"></div>
    </div>

    <div class="grid-batch one">
      <div class="panel"><div class="panel-head"><h2>Batch jobs ${tip('?', 'Vertex AI Batch Prediction processes large numbers of products asynchronously. Jobs are submitted with the model selected in the header (supported models only). The cost column uses the same list price (standard online) as the estimate above and does not reflect Batch discounts.')}</h2><span class="muted small" id="b-upd"></span></div>
        <div id="b-jobs">${skeleton(3, 'skel-row')}</div></div>
    </div>
    <div class="panel"><div class="panel-head"><h2>Golden records ${tip('?', 'Approved attributes, tags, search keywords and summaries combined into data ready for your product master and search.')}</h2>
      <div class="head-actions">${goldenCsvUrl ? `<a class="btn btn-primary btn-sm" href="${goldenCsvUrl}" download>⬇ Export CSV</a>` : '<button class="btn btn-primary btn-sm" disabled title="Disabled in mock mode">⬇ Export CSV</button>'}</div></div>
      <div id="golden">${skeleton(3, 'skel-row')}</div></div>`;

  let cost = null;            // last /api/cost/models response
  // Legacy backends return JPY amounts; convert with the response's rate (never displayed).
  const fx = () => Number(cost && cost.usd_jpy) || Number(state.config && state.config.usd_jpy) || 150;
  const usd = (o, base) => (o ? usdOf({ usd_jpy: fx(), ...o }, base) : null);
  const partUsd = (p) => (p ? (p.usd ?? (p.jpy != null ? p.jpy / fx() : 0)) : 0);
  const listCost = (j) => {    // list-price cost (USD) of a finished job from its tokens (output incl. thinking)
    const i = modelInfo(j.model);
    if (!i || i.price_in == null || !(j.input_tokens > 0)) return null;
    return ((j.input_tokens || 0) * i.price_in + (j.output_tokens || 0) * i.price_out) / 1e6;
  };

  // ── Jobs
  const paintJobs = (jobs) => {
    $('#b-upd', root).textContent = 'Last updated ' + fmtDate(new Date().toISOString());
    const box = $('#b-jobs', root);
    if (!jobs.length) { box.innerHTML = emptyState('🗂️', 'No batch jobs yet', 'Start one with "New batch job"'); return; }
    box.innerHTML = `<table class="table"><thead><tr><th>Job</th><th>Model</th><th>Status</th><th class="r">SKUs</th><th class="r">Tokens (in/out)</th><th class="r">Cost (list price)</th><th>Created</th></tr></thead><tbody>
      ${jobs.map((j) => {
        const st = JOB_STATE[j.state] || [String(j.state || '').replace('JOB_STATE_', '') || '—', 'mute', ''];
        const c = listCost(j);
        return `<tr><td><div class="job-name">${esc(j.display_name || '—')}</div><div class="muted small mono" title="${esc(j.job_id)}">${esc(String(j.job_id || '').split('/').pop())}</div></td>
          <td class="mono small">${esc(j.model || '—')}</td>
          <td><span class="jstate js-${st[1]}">${st[1] === 'run' ? '<span class="spin"></span>' : ''}${esc(st[0])}</span><div class="muted small">${esc(st[2])}${j.ingested ? ' • Ingested' : ''}${j.error ? ` • <span class="dc-err" title="${esc(j.error)}">Error</span>` : ''}</div></td>
          <td class="r"><b>${fmtInt(j.sku_count)}</b></td>
          <td class="r mono small">${fmtInt(j.input_tokens)} / ${fmtInt(j.output_tokens)}</td>
          <td class="r"><b>${c != null ? fmtUsd(c) : '—'}</b>${c != null && j.sku_count ? `<div class="muted small">${fmtUsd(c / j.sku_count)}/SKU</div>` : ''}</td>
          <td class="small">${fmtDate(j.created_at)}</td></tr>`;
      }).join('')}</tbody></table>`;
  };
  const loadJobs = async (refresh = false) => {
    const btn = $('#b-refresh', root);
    if (refresh) { btn.disabled = true; btn.classList.add('loading'); }
    try {
      const d = refresh ? await api.batchRefresh() : await api.batchJobs();
      if (stale()) return;
      paintJobs(d.jobs || []);
      if (refresh) { toast('Job status updated', 'ok'); refreshShell(); loadGolden(); }
    } catch (e) {
      if (stale()) return;
      if (refresh) toast('Refresh failed: ' + e.message, 'err');
      else $('#b-jobs', root).innerHTML = errorState(e);
    } finally { if (btn) { btn.disabled = false; btn.classList.remove('loading'); } }
  };
  $('#b-refresh', root).onclick = () => loadJobs(true);
  $('#b-new', root).onclick = () => {
    const groups = (state.config && state.config.groups) || {};
    const model = selectedModel();
    const info = modelInfo(model) || {};
    const reason = info.batch ? '' : (info.batch_note || 'This model does not support Batch Prediction');
    const m = openModal('New batch job', `
      <p class="muted">Submits a job to Vertex AI Batch Prediction. It can take from a few minutes to tens of minutes to finish.</p>
      <div class="nb-model"><span class="muted small">Model (selected in header)</span><b class="mono" id="nb-model">${esc(model)}</b>${info.batch ? '<span class="mb mb-info">Batch supported</span>' : '<span class="mb mb-bad">Batch not supported</span>'}</div>
      ${reason ? `<div class="banner warn nb-reason">⚠️ <div>${esc(modelLabel(model))}: ${esc(reason)}<br><span class="small">Switch to a Batch-supported model in the header, or use a live run (Enrichment Studio / CSV live test).</span></div></div>` : ''}
      <label class="field"><span>Category</span><select class="select" id="nb-group"><option value="">All</option>${Object.entries(groups).map(([k, v]) => `<option value="${esc(k)}">${esc(v)}</option>`).join('')}</select></label>
      <label class="field"><span>Max items (SKUs)</span><input class="input" id="nb-limit" type="number" min="1" max="100000" value="10"></label>
      <div class="field-note" id="nb-est"></div>`, {
      okLabel: 'Submit',
      onOk: async (el) => {
        const limit = parseInt($('#nb-limit', el).value, 10);
        if (!(limit > 0)) { toast('Enter a valid number of items', 'warn'); return false; }
        const group = $('#nb-group', el).value || null;
        const res = await api.batchSubmit({ group, limit }, model);
        toast(`Batch job submitted: ${res.job ? res.job.display_name : ''} (${model})`, 'ok');
        loadJobs(false);
        return true;
      },
    });
    if (reason) { const ok = $('.modal-ok', m.el); ok.disabled = true; ok.title = reason; }
    const est = () => {
      const n = parseInt($('#nb-limit', m.el).value, 10) || 0;
      const el = $('#nb-est', m.el); if (!el || n <= 0) return;
      const row = cost && cost.models.find((r) => r.id === model);
      const ms = row && row.measurement;
      const pr = row && row.price;
      if (!ms) { el.textContent = 'No token data for this model yet (use "Measure 1 sample" in the table below)'; return; }
      if (!pr || !pr.available) { el.innerHTML = 'List price: <b>See official pricing page</b>'; return; }
      const usdTotal = n * (ms.input_tokens * pr.input_usd_per_mtok + (ms.output_tokens + ms.thinking_tokens) * pr.output_usd_per_mtok) / 1e6;
      el.innerHTML = `At list price (standard online): <b>${fmtUsd(usdTotal)}</b> • measured ${fmtInt(ms.input_tokens + ms.output_tokens + ms.thinking_tokens)} tokens/SKU`;
    };
    $('#nb-limit', m.el).addEventListener('input', est); est();
  };

  // ── List-price cost table
  let costSeq = 0;
  const presets = () => {
    const cat = cost && cost.catalog_count;
    const list = [[100, '100'], [1000, '1,000'], [8807, '8,807']];
    if (cat && cat !== 8807) list.push([cat, `Full catalog ${fmtInt(cat)}`]);
    else if (cat) list[2] = [8807, '8,807 (full catalog)'];
    list.push([100000, '100,000']);
    return list;
  };
  const paintPresets = () => {
    $('#sim-presets', root).innerHTML = presets().map(([n, l]) => `<button class="fchip ${n === simSkus ? 'on' : ''}" data-n="${n}">${esc(l)}</button>`).join('')
      + (cost && cost.catalog_count ? `<span class="muted small cat-src" title="Row count of BigQuery cymbal.products">BQ cymbal.products: ${fmtInt(cost.catalog_count)} SKUs</span>` : '');
    $$('#sim-presets .fchip', root).forEach((b) => b.addEventListener('click', () => setSkus(+b.dataset.n)));
  };
  const paintHero = () => {
    const box = $('#cost-hero', root);
    const sel = selectedModel();
    const row = cost.models.find((r) => r.id === sel);
    const ms = row && row.measurement;
    const e = row && row.estimate;
    if (!row) { box.innerHTML = `<div class="ch-main muted">The model selected in the header (${esc(sel)}) is not included in the estimate</div>`; return; }
    if (!ms) { box.innerHTML = `<div class="ch-main">Measuring token usage for <span class="ch-model">${esc(row.label)}</span>…</div>`; return; }
    const base = ms.only_sample ? `Based on token usage for this product (${esc(ms.sample_title || ms.sample_sku)})` : `Based on average tokens from ${fmtInt(ms.skus)} measured SKUs (${fmtInt(ms.runs)} runs)`;
    const promo = cost.basis === 'promo';
    const basisTag = promo ? '<span class="mb mb-warn" title="Reference estimate at limited-time promo (discounted) prices">Reference: promo pricing (discount)</span>' : '<span class="mb mb-blue">List price • no discounts</span>';
    box.innerHTML = `<div class="ch-main" data-hero-model="${esc(row.id)}" data-basis="${esc(cost.basis)}">${basisTag}<span class="ch-model">${esc(row.label)}</span>
      ${e && e.price_available
        ? `<span>${base}, <b>${fmtInt(cost.skus)} SKUs</b> would cost about <b class="ch-big">${fmtUsd(usd(e, 'total'))}</b> ${promo ? '(at promo pricing)' : 'at list price'} <span class="muted">(${fmtUsd(usd(e, 'per_sku'))} per SKU)</span></span>`
        : `<span>${base}: ${fmtInt(ms.input_tokens + ms.output_tokens + ms.thinking_tokens)} tokens per SKU • <b>See official pricing page</b> (<a href="${esc(priceUrl())}" target="_blank" rel="noopener">official pricing page</a>)</span>`}</div>`;
  };
  const measureCell = (r) => {
    const m = measuring[r.id];
    if (m && !m.done) {
      return `<div class="measure-prog" data-model="${esc(r.id)}"><span class="spin"></span> Measuring…<div class="mp-stages">${Object.entries(STAGE_LABEL).map(([k, l]) => `<span class="mps ${m.stages[k] ? 'done' : ''}">${m.stages[k] ? '✓' : '…'} ${l}</span>`).join('')}</div>${m.title ? `<div class="muted small ellipsis" title="${esc(m.title)}">${esc(m.title)}</div>` : ''}</div>`;
    }
    return `<button class="btn btn-outline btn-xs btn-measure" data-model="${esc(r.id)}" title="Run one representative SKU with this model to measure tokens (saved to token_usage; enrichment results are not overwritten)">${r.measurement ? '↻ Re-measure' : 'Measure 1 sample'}</button>${m && m.error ? `<div class="dc-err small" title="${esc(m.error)}">Failed: ${esc(m.error.slice(0, 60))}</div>` : ''}`;
  };
  const paintTable = () => {
    const box = $('#cost-table', root);
    const sel = selectedModel();
    const rows = cost.models;
    const priced = rows.filter((r) => r.estimate && r.estimate.price_available);
    const max = Math.max(...priced.map((r) => usd(r.estimate, 'total') || 0), 1e-9);
    box.innerHTML = `<div class="cost-scroll"><table class="table cost-table" id="cost-tbl"><thead><tr>
        <th>Model</th><th class="r">Tokens / SKU<div class="th-sub">input • output • thinking</div></th><th>Measurement basis</th>
        <th class="r">${cost.basis === 'promo' ? 'Reference: promo rate (discount)' : 'Official list price'} (USD / 1M tokens)<div class="th-sub">input • output (incl. thinking)</div></th>
        <th class="r">Cost for ${fmtInt(cost.skus)} SKUs<div class="th-sub">input • output • thinking</div></th><th></th></tr></thead><tbody>
      ${rows.map((r) => {
        const ms = r.measurement, e = r.estimate, pr = r.price;
        const tok = ms ? `<b>${fmtInt(ms.input_tokens + ms.output_tokens + ms.thinking_tokens)}</b><div class="tok3 mono small">${fmtInt(ms.input_tokens)} • ${fmtInt(ms.output_tokens)} • ${fmtInt(ms.thinking_tokens)}</div>` : '<span class="muted">Not measured</span>';
        const basis = ms ? (ms.only_sample
          ? `<span class="mb mb-warn">1 sample</span><div class="small ellipsis" title="${esc(ms.sample_title)}">${esc(ms.sample_title || '')}</div><div class="muted small mono">SKU ${esc(ms.sample_sku)}</div>`
          : `<span class="mb mb-ok">Measured ${fmtInt(ms.skus)} SKUs</span><div class="muted small">Avg of ${fmtInt(ms.runs)} runs • latest ${esc(ms.sample_title || ms.sample_sku)}</div>`) : '<span class="muted small">—</span>';
        const price = pr && pr.available
          ? `<div class="mono"><b>$${pr.input_usd_per_mtok}</b> • <b>$${pr.output_usd_per_mtok}</b></div>${pr.promo ? '<span class="mb mb-warn">Reference: promo pricing (discount)</span>' : ''}${pr.note ? `<div class="muted small price-note">${esc(pr.note)}</div>` : ''}`
          : '<div class="price-missing">See official pricing page</div><div class="muted small">Excluded from totals</div>';
        let costCell = '<span class="muted">—</span>';
        if (e && e.price_available) {
          const b = e.breakdown;
          const bi = partUsd(b.input), bo = partUsd(b.output), bt = partUsd(b.thinking);
          costCell = `<div class="cost-big">${fmtUsd(usd(e, 'total'))}</div><div class="muted small">${fmtUsd(usd(e, 'per_sku'))}/SKU</div>
            <div class="cbar" title="Input ${fmtUsd(bi)} / Output ${fmtUsd(bo)} / Thinking ${fmtUsd(bt)}"><i class="ci" style="width:${bi / max * 100}%"></i><i class="co" style="width:${bo / max * 100}%"></i><i class="ct" style="width:${bt / max * 100}%"></i></div>
            <div class="cbd small mono">${fmtUsd(bi)} • ${fmtUsd(bo)} • ${fmtUsd(bt)}</div>
            <div class="muted small mono">${fmtInt(e.tokens.input)} • ${fmtInt(e.tokens.output)} • ${fmtInt(e.tokens.thinking)} tokens</div>`;
        } else if (e) {
          costCell = `<div class="price-missing">See official pricing page</div><div class="muted small mono">${fmtInt(e.tokens.input)} • ${fmtInt(e.tokens.output)} • ${fmtInt(e.tokens.thinking)} tokens</div>`;
        }
        return `<tr data-model="${esc(r.id)}" class="${r.id === sel ? 'sel' : ''} ${r.id === cost.cheapest ? 'cheap' : ''}">
          <td><div class="cm-name">${esc(r.label)}${r.id === sel ? '<span class="mb mb-blue">Selected</span>' : ''}${r.default ? '<span class="mb mb-mute">Default</span>' : ''}${r.id === cost.cheapest ? '<span class="mb mb-ok">Lowest cost</span>' : ''}</div>
            <div class="muted small mono">${esc(r.id)}</div><div class="cm-badges">${r.batch ? '<span class="mb mb-info">Batch supported</span>' : `<span class="mb mb-mute" title="${esc(r.batch_note || '')}">Batch unavailable</span>`}${r.reason ? `<span class="mb mb-warn">${esc(r.reason)}</span>` : ''}</div></td>
          <td class="r">${tok}</td><td class="basis">${basis}</td>
          <td class="r">${price}<a class="price-link" href="${esc((pr && pr.url) || priceUrl())}" target="_blank" rel="noopener">Official pricing page ↗</a></td>
          <td class="r">${costCell}</td>
          <td class="r act">${measureCell(r)}</td></tr>`;
      }).join('')}</tbody></table></div>
      <div class="cost-legend small"><span><i class="ci"></i>Input</span><span><i class="co"></i>Output</span><span><i class="ct"></i>Thinking</span><span class="muted">Bars are relative to the highest cost in the table</span></div>`;
    $$('.btn-measure', box).forEach((b) => b.addEventListener('click', () => measure(b.dataset.model, true)));
  };
  const paintNotes = () => {
    const sp = cost.sample_sku;
    const un = cost.unavailable || [];
    const excluded = cost.models.filter((r) => !(r.price && r.price.available));
    $('#cost-notes', root).innerHTML = `<div class="note-title">Assumptions &amp; notes</div><ul>
      <li>Token counts <b>vary with the length of the product description</b>. Values are the average of products actually processed by each model (or a single representative SKU if none), scaled linearly to N SKUs.</li>
      <li>1 SKU = <b>3 calls</b> (attribute extraction / tags &amp; summary / GMC conversational attributes). Thinking tokens are billed at the <b>output rate</b>, as stated on the official page (thinking level: LOW).</li>
      <li>Representative SKU for "Measure 1 sample": ${sp ? `<b>${esc(sp.title)}</b> (SKU <span class="mono">${esc(sp.sku)}</span> • ${fmtInt(sp.chars)} source characters = median of the ${fmtInt(cost.demo_skus)} demo SKUs)` : '—'}. Every model is measured on the same product, so results are comparable.</li>
      <li>Pricing: <a href="${esc(cost.pricing_url)}" target="_blank" rel="noopener">Vertex AI generative AI pricing (official)</a>, <b>Standard • Global • input up to 200K tokens</b>, ${cost.basis === 'promo' ? '<b>limited-time promo pricing (discounted, for reference)</b>' : '<b>list price (no limited-time promotions, credit-backs or other discounts)</b>'}. Prices retrieved <b>${esc(cost.price_retrieved_at)}</b>, effective ${esc(cost.as_of)}. Excludes Batch / Flex / Priority pricing, cache discounts and taxes.</li>
      ${excluded.length ? `<li>Models without published input and output prices on the official page show "See official pricing page" and are excluded from totals and comparisons: ${excluded.map((r) => `<span class="mono">${esc(r.id)}</span>`).join(', ')}</li>` : ''}
      ${un.length ? `<li>Models unavailable in this project and removed from the list: ${un.map((m) => `<span class="mono" title="${esc(m.reason || '')}">${esc(m.id)}</span>`).join(', ')}</li>` : ''}
    </ul>`;
  };
  const loadCost = async () => {
    const my = ++costSeq;
    let d;
    try { d = await api.costModels({ skus: simSkus, basis: basisPref() }); } catch (e) {
      if (my === costSeq && !stale()) $('#cost-table', root).innerHTML = errorState(e);
      return;
    }
    if (my !== costSeq || stale()) return;
    cost = d;
    paintPresets(); paintHero(); paintTable(); paintNotes();
    autoMeasure();
  };
  const setSkus = (n) => {
    simSkus = Math.max(1, Math.min(10000000, Math.round(n)));
    $('#sim-num', root).value = fmtInt(simSkus);
    $('#sim-range', root).value = toPos(simSkus);
    loadCost();
  };
  let simT;
  $('#sim-range', root).addEventListener('input', (e) => {
    const n = toSkus(+e.target.value);
    $('#sim-num', root).value = fmtInt(n);
    clearTimeout(simT); simT = setTimeout(() => setSkus(n), 150);
  });
  $('#sim-num', root).addEventListener('change', (e) => setSkus(parseInt(String(e.target.value).replace(/[^\d]/g, ''), 10) || MIN));
  const paintBasis = () => {
    const promo = basisPref() === 'promo';
    $('#promo-toggle', root).checked = promo;
    $('#basis-list', root).classList.toggle('off', promo);
    $('#basis-chip', root).textContent = promo ? 'Showing reference: limited-time promo pricing (discount)' : 'List price (no promotions) • no Batch discount';
    $('#basis-chip', root).className = 'chip ' + (promo ? 'chip-warn' : 'chip-info');
    $('#cost-panel', root).classList.toggle('promo', promo);
  };
  $('#promo-toggle', root).addEventListener('change', (e) => { localStorage.setItem(BASIS_KEY, e.target.checked ? 'promo' : 'list'); paintBasis(); loadCost(); });
  paintBasis();

  // "Measure 1 sample" (button + auto on first view; at most 2 in parallel)
  const queue = [];
  let active = 0;
  const pump = () => {
    while (active < 2 && queue.length) {
      const id = queue.shift();
      active++;
      runMeasure(id).finally(() => { active--; pump(); });
    }
  };
  async function runMeasure(id) {
    measuring[id] = { stages: {}, done: false };
    if (!stale() && cost) paintTable();
    try {
      await api.costMeasure(id, (ev) => {
        const m = measuring[id];
        if (ev.event === 'start') { m.sku = ev.sku; m.title = ev.title; }
        else if (ev.event === 'stage') m.stages[ev.stage] = ev;
        else if (ev.event === 'error') m.error = ev.message || 'error';
        else if (ev.event === 'result') m.result = ev;
        if (!stale() && cost) { const row = $(`tr[data-model="${CSS.escape(id)}"] .act`, root); if (row) row.innerHTML = measureCell({ id, measurement: null }); }
      });
      if (measuring[id].error) toast(`Measurement failed for ${modelLabel(id)}: ${measuring[id].error}`, 'err', 6000);
      else toast(`${modelLabel(id)}: measured tokens for 1 sample`, 'ok');
    } catch (e) {
      measuring[id].error = e.message || String(e);
      toast(`Measurement failed for ${modelLabel(id)}: ${e.message}`, 'err', 6000);
    } finally {
      measuring[id].done = true;
      if (!stale()) loadCost();
    }
  }
  function measure(id, manual = false) {
    if (measuring[id] && !measuring[id].done) return;
    if (queue.includes(id)) return;
    if (manual) autoTried.add(id);
    queue.push(id);
    measuring[id] = { stages: {}, done: false, queued: true };
    if (cost) paintTable();
    pump();
  }
  function autoMeasure() {
    if (isMock && !cost) return;
    cost.models.filter((r) => !r.measurement && r.status === 'available' && !autoTried.has(r.id)).forEach((r) => { autoTried.add(r.id); measure(r.id); });
  }
  const onModel = () => { if (!root.isConnected || !$('#cost-panel', root)) { window.removeEventListener('enrich:model', onModel); return; } if (cost) { paintHero(); paintTable(); } };
  window.addEventListener('enrich:model', onModel);

  // Golden
  const loadGolden = async () => {
    const box = $('#golden', root);
    let d;
    try { d = await api.golden({}); } catch (e) { if (!stale()) box.innerHTML = errorState(e); return; }
    if (stale()) return;
    const items = d.items || [];
    if (!items.length) { box.innerHTML = emptyState('⭐', 'No golden records yet', 'Products appear here once enrichment and approval are complete'); return; }
    const groups = (state.config && state.config.groups) || {};
    const show = items.slice(0, 8);
    box.innerHTML = `<div class="muted small gold-cnt">${fmtInt(items.length)} SKUs${items.length > show.length ? ` (showing first ${show.length})` : ''}</div>
      <table class="table gold"><thead><tr><th>Product</th><th>Key attributes</th><th>Feature tags</th><th class="r">Fill rate</th><th>Updated</th></tr></thead><tbody>
      ${show.map((g) => {
        const attrs = Object.entries(g.attributes || {}).slice(0, 5);
        const more = Object.keys(g.attributes || {}).length - attrs.length;
        return `<tr><td><a href="#/studio/${encodeURIComponent(g.sku)}" class="gold-title">${esc(g.title)}</a><div class="muted small">${esc(groups[g.group] || g.group)} • ${esc(g.sku)}</div></td>
          <td><div class="kv">${attrs.map(([k, v]) => `<span class="kvp" title="${esc(k)}">${esc(label(k))} <b>${esc(fmtValue(v && v.value))}${esc(v && v.unit || '')}</b></span>`).join('')}${more > 0 ? `<span class="kvp more">+${more}</span>` : ''}</div></td>
          <td><div class="tags">${(g.feature_tags || []).slice(0, 4).map((t) => `<span class="tag">${esc(t)}</span>`).join('')}</div></td>
          <td class="r nowrap"><span class="muted">${fmtPct(g.fill_rate_before)}</span> ➜ <b class="after-text">${fmtPct(g.fill_rate_after)}</b></td>
          <td class="small nowrap">${fmtDate(g.updated_at)}</td></tr>`;
      }).join('')}</tbody></table>`;
  };

  await Promise.all([loadCost().then(() => loadJobs(false)), loadGolden()]);
  return () => { clearTimeout(simT); window.removeEventListener('enrich:model', onModel); };
}

const label = (k) => ((state.config && state.config.attr_labels) || {})[k] || k;
