// ⑥ CSV live test — CSV upload → real-time Gemini enrichment → save to BigQuery
import { api, sampleCsvUrl } from '../api.js?v=20261111q';
import {
  esc, $, $$, fmtInt, fmtPct, fmtJpy, fmtUsd, usdOf, fmtValue, chip, confBar, tip, emptyState, toast, state, stepper, refreshShell, STATUS,
  selectedModel, modelLabel, modelInfo, modelOverrideHtml, runModel,
} from '../ui.js?v=20261111q';

const datasetRef = () => (state.config && state.config.dataset) || (S.bq && S.bq.dataset) || 'product_enrichment';
const BQ_TABLES = [
  ['source_products', 'Source catalog data'],
  ['enriched_attributes', 'Extracted attributes & tags (with decisions)'],
  ['sku_extras', 'Search keywords & AI summary'],
  ['golden_records', 'Golden records'],
  ['gmc_conversational', 'GMC conversational attributes'],
];
const GROUP_SHORT = new Proxy({}, { get: (_, k) => ((state.config && state.config.group_short) || {})[k] });

// Session survives navigation (a running job keeps streaming in the background).
const S = {
  items: [], columns: [], csvText: '', rows: {}, running: false, ctrl: null, t0: 0, tin: 0, tout: 0,
  current: null, doneSkus: [], bq: null, bqSaving: false, complete: null, gmc: {}, open: new Set(), paint: null, showText: false,
};

export const modelPretty = (m) => modelLabel(m || selectedModel());
// list price (standard online, USD) of the model in use; output includes thinking. null when not on the official page.
const estCost = (tin, tout, m) => {
  const i = modelInfo(m || selectedModel());
  if (!i || i.price_in == null || i.price_out == null) return null;
  return (tin * i.price_in + tout * i.price_out) / 1e6;
};

export async function render(root, _params, stale) {
  const model = selectedModel();
  root.innerHTML = `
    <div class="csv-hero">
      <div class="csv-hero-txt">
        <h1>CSV live test<span class="h1-sub">(real-time enrichment &amp; BigQuery save)</span></h1>
        <p>Upload a CSV file (or load the sample CSV) and <b class="mono">${esc(model)}</b> will run
        <b>attribute extraction, unit normalization, 3-stage gate decisions and GMC conversational attribute generation</b> in real time, then save instantly to BigQuery (all 5 tables in the <span class="mono">${esc(datasetRef().split('.').pop())}</span> dataset).</p>
      </div>
      <div class="csv-hero-act">
        <button class="btn btn-primary" id="btn-csv-sample">⚡ Load sample CSV (3 products) in one click</button>
        <a class="btn btn-outline" href="${sampleCsvUrl}" download>⬇ Download sample CSV</a>
      </div>
    </div>

    <div class="panel csv-upload">
      <div class="csv-up-grid">
        <label class="dropzone" id="dropzone" for="csv-file">
          <input type="file" id="csv-file" accept=".csv,text/csv" hidden>
          <div class="dz-ic">📄</div>
          <div class="dz-title">Drag &amp; drop a CSV file</div>
          <div class="dz-sub">or click to choose a file (UTF-8, up to 50 rows)</div>
          <div class="dz-file" id="dz-file"></div>
        </label>
        <div class="csv-help">
          <div class="sec-lab">Supported columns ${tip('?', 'Common column names and aliases are recognized automatically. Any unknown column is treated as a source attribute and normalized by Gemini.')}</div>
          <div class="col-pills">${['sku / product_code', 'title / name', 'brand / manufacturer', 'category', 'description', 'price', 'image_url', 'product_url', '+ any attribute columns (dimensions, weight …)'].map((c) => `<span class="kvp">${esc(c)}</span>`).join('')}</div>
          <button class="btn btn-ghost btn-sm" id="btn-toggle-text">✎ Paste / edit CSV text directly</button>
        </div>
      </div>
      <div class="csv-text-wrap" id="csv-text-wrap" ${S.showText ? '' : 'hidden'}>
        <textarea class="input csv-text" id="csv-text" spellcheck="false" placeholder="sku,title,brand,category,description,price,size,origin,storage_type,allergens&#10;...">${esc(S.csvText)}</textarea>
        <div class="csv-text-act"><button class="btn btn-outline btn-sm" id="btn-reparse">↻ Re-parse CSV</button><span class="muted small" id="csv-cols"></span></div>
      </div>
      <div class="csv-controls">
        <label class="chk"><input type="checkbox" id="chk-save-bq" checked><span>Automatically save to BigQuery (<span class="mono">${esc(datasetRef())}</span>) when enrichment finishes</span></label>
        <span class="spacer"></span>
        <button class="btn btn-outline" id="btn-csv-save-bq" disabled>☁️ Save to BigQuery now</button>
        ${modelOverrideHtml('csv-model')}
        <button class="btn btn-primary btn-live" id="btn-csv-run" disabled><span class="live-ic">▶</span><span class="live-txt">Run in real time with ${esc(modelPretty())} (0 items)</span></button>
      </div>
    </div>

    <div id="csv-progress"></div>
    <div id="csv-bq"></div>
    <div class="panel csv-table-panel">
      <div class="panel-head"><h2>Products <span class="muted small" id="csv-count"></span></h2>
        <span class="muted small">Click a row to expand Before / After and GMC Q&amp;A</span></div>
      <div id="csv-rows"></div>
    </div>`;

  // ── handlers
  const loadText = async (text, label) => {
    try {
      const d = await api.csvParse({ csv_text: text });
      setItems(d.items || [], d.columns || [], text);
      toast(`${label}: loaded ${d.count} items`, 'ok');
    } catch (e) { toast('Could not parse CSV: ' + e.message, 'err', 6000); }
  };
  const setItems = (items, cols, text) => {
    if (S.running) { toast('Cannot load while a run is in progress', 'warn'); return; }
    S.items = items; S.columns = cols; S.csvText = text; S.rows = {}; S.doneSkus = []; S.bq = null; S.complete = null; S.gmc = {}; S.open.clear();
    const ta = $('#csv-text', root); if (ta) ta.value = text;
    paint();
  };
  $('#btn-csv-sample', root).onclick = async (e) => {
    const b = e.currentTarget; b.disabled = true; b.classList.add('loading');
    try {
      const d = await api.csvSample();
      setItems(d.items || [], d.columns || [], d.csv_text || '');
      $('#dz-file', root).textContent = '✓ Sample CSV (aics_live_test_sample.csv)';
      toast(`Loaded sample CSV with ${d.count} products`, 'ok');
    } catch (err) { toast('Could not fetch sample: ' + err.message, 'err'); } finally { b.disabled = false; b.classList.remove('loading'); }
  };
  const readFile = async (file) => {
    if (!file) return;
    if (file.size > 5 * 1024 * 1024) { toast('File is too large (5 MB max)', 'warn'); return; }
    const buf = await file.arrayBuffer();
    let text;
    try { text = new TextDecoder('utf-8', { fatal: true }).decode(buf); } catch (_) { text = new TextDecoder('shift_jis').decode(buf); }
    $('#dz-file', root).textContent = `✓ ${file.name} (${fmtInt(file.size / 1024)} KB)`;
    loadText(text, file.name);
  };
  $('#csv-file', root).addEventListener('change', (e) => readFile(e.target.files[0]));
  const dz = $('#dropzone', root);
  ['dragenter', 'dragover'].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add('over'); }));
  ['dragleave', 'drop'].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove('over'); }));
  dz.addEventListener('drop', (e) => readFile(e.dataTransfer.files[0]));
  $('#btn-toggle-text', root).onclick = () => { S.showText = !S.showText; $('#csv-text-wrap', root).hidden = !S.showText; };
  $('#btn-reparse', root).onclick = () => loadText($('#csv-text', root).value, 'Re-parse');
  $('#btn-csv-run', root).onclick = () => run();
  $('#csv-model', root).addEventListener('change', () => S.paint && S.paint.all());
  const onModel = () => { if (!root.isConnected || !$('#csv-model', root)) { window.removeEventListener('enrich:model', onModel); return; } if (S.paint) S.paint.all(); };
  window.addEventListener('enrich:model', onModel);
  $('#btn-csv-save-bq', root).onclick = () => saveBq();
  $('#csv-rows', root).addEventListener('click', (e) => {
    if (e.target.closest('a, button')) return;
    const row = e.target.closest('.csv-row'); if (!row) return;
    const sku = row.dataset.sku;
    if (S.open.has(sku)) S.open.delete(sku); else { S.open.add(sku); loadGmc(sku); }
    paintRows();
  });

  // ── painting
  function paint() {
    if (stale()) return;
    paintControls(); paintProgress(); paintBq(); paintRows();
  }
  function paintControls() {
    const run = $('#btn-csv-run', root); if (!run) return;
    run.disabled = S.running || !S.items.length;
    run.classList.toggle('running', S.running);
    $('.live-txt', run).textContent = S.running ? `Running… ${S.current ? S.current.index + 1 : 0}/${S.items.length}` : `Run in real time with ${modelPretty(runModel(root, 'csv-model'))} (${S.items.length} items)`;
    const sb = $('#btn-csv-save-bq', root);
    sb.disabled = S.running || S.bqSaving || !S.doneSkus.length;
    sb.classList.toggle('loading', S.bqSaving);
    sb.textContent = S.bq ? '☁️ Save to BigQuery again' : '☁️ Save to BigQuery now';
    $('#chk-save-bq', root).disabled = S.running;
    const cc = $('#csv-cols', root); if (cc) cc.textContent = S.columns.length ? `${S.columns.length} columns: ${S.columns.slice(0, 8).join(', ')}${S.columns.length > 8 ? ' …' : ''}` : '';
    $('#csv-count', root).textContent = S.items.length ? `${S.items.length} items` : '';
  }
  function paintProgress() {
    const box = $('#csv-progress', root); if (!box) return;
    if (!S.running && !S.complete) { box.innerHTML = ''; return; }
    const total = S.items.length || 1;
    const done = Object.values(S.rows).filter((r) => r.state === 'done' || r.state === 'error').length;
    const pct = S.complete ? 100 : Math.round(done / total * 100);
    const el = ((S.complete ? S.complete.elapsed_ms : performance.now() - S.t0) / 1000).toFixed(1);
    const cost = S.complete ? usdOf(S.complete, 'cost') : estCost(S.tin, S.tout, S.model);
    const cur = S.bqSaving ? `☁️ Saving to BigQuery (${datasetRef()})…` : S.complete ? `✅ Done — processed ${S.complete.processed}/${S.complete.total} SKUs` : S.current ? `⚙️ Item ${S.current.index + 1}/${total}: ${S.current.title}` : 'Starting…';
    box.innerHTML = `<div class="panel csv-prog ${S.complete ? 'done' : ''}">
      <div class="cp-top"><div class="cp-cur">${S.running ? '<span class="live-dot"></span>' : ''}<span>${esc(cur)}</span></div><div class="cp-pct">${pct}%</div></div>
      <div class="cp-bar"><div class="cp-fill" style="width:${pct}%"></div></div>
      <div class="cp-stats">
        <div><span class="k">Elapsed</span><b>${el}</b> s</div>
        <div><span class="k">Processed</span><b>${done}</b> / ${S.items.length} SKUs</div>
        <div><span class="k">Tokens (in/out)</span><b>${fmtInt(S.tin)}</b> / <b>${fmtInt(S.tout)}</b></div>
        <div><span class="k">${S.complete ? 'Cost (list price, online)' : 'Estimated cost (list price, online)'}</span><b>${cost != null ? fmtUsd(cost) : '<span class="muted small">See official pricing page</span>'}</b></div>
        <div><span class="k">Model</span><b class="mono" data-used-model="${esc((S.complete && S.complete.model) || S.model || '')}">${esc((S.complete && S.complete.model) || S.model || selectedModel())}</b></div>
      </div></div>`;
  }
  function paintBq() {
    const box = $('#csv-bq', root); if (!box) return;
    if (!S.bq) { box.innerHTML = ''; return; }
    // Backend keys may be dataset-qualified ("project.dataset.table") — normalize to the bare table name.
    const t = {};
    Object.entries(S.bq.tables || {}).forEach(([k, v]) => { t[String(k).split('.').pop()] = v; });
    box.innerHTML = `<div class="bq-card">
      <div class="bq-head"><span class="bq-ic">☁️</span><div><div class="bq-title">Saved to BigQuery</div>
        <div class="bq-sub">Wrote <b>${fmtInt(S.bq.skus)}</b> SKUs to dataset <span class="mono">${esc(datasetRef())}</span></div></div></div>
      <div class="bq-tables">${BQ_TABLES.map(([k, lab]) => `<div class="bq-t"><div class="bq-n">${fmtInt(t[k] ?? 0)}<span>rows</span></div><div class="bq-k mono">${esc(k)}</div><div class="bq-l">${esc(lab)}</div></div>`).join('')}</div>
    </div>`;
  }
  function paintRows() {
    const box = $('#csv-rows', root); if (!box) return;
    if (!S.items.length) { box.innerHTML = emptyState('📥', 'No CSV loaded yet', 'Click "⚡ Load sample CSV in one click" or drop a CSV file'); return; }
    box.innerHTML = `<div class="csv-head"><span></span><span>Product</span><span>Category</span><span class="r">Price</span><span>Source attributes</span><span>Fill rate Before ➜ After</span><span>Results</span><span>Status</span><span></span></div>` +
      S.items.map((p) => rowHtml(p)).join('');
  }
  function rowHtml(p) {
    const r = S.rows[p.sku] || { state: 'wait' };
    const open = S.open.has(p.sku);
    const keys = Object.keys(p.raw_attributes || {});
    const fb = r.fill_before ?? p.fill_before ?? 0;
    const fa = r.fill_after;
    const badge = r.state === 'run' ? '<span class="chip chip-info pulse">Running...</span>'
      : r.state === 'error' ? `<span class="chip chip-bad" title="${esc(r.message)}">Error</span>`
        : r.state === 'done' ? chip(r.status === 'completed' ? 'completed' : r.status) : '<span class="chip chip-mute">Waiting</span>';
    return `<div class="csv-row st-${r.state} ${open ? 'open' : ''}" data-sku="${esc(p.sku)}">
      <span class="cr-chev">${open ? '▾' : '▸'}</span>
      <div class="cr-prod"><div class="cr-title" title="${esc(p.title)}">${esc(p.title)}</div><div class="muted small"><span class="mono">${esc(p.sku)}</span>${p.brand ? ' • ' + esc(p.brand) : ''}</div></div>
      <span><span class="cat-pill">${esc(GROUP_SHORT[p.group] || p.group_label || p.group)}</span></span>
      <span class="r nowrap">${p.price ? fmtJpy(p.price) : '—'}</span>
      <div class="cr-raw"><b>${keys.length}</b> fields<div class="cr-raw-pills">${keys.slice(0, 3).map((k) => `<span class="kvp" title="${esc(k)}: ${esc(p.raw_attributes[k])}">${esc(k)}</span>`).join('')}${keys.length > 3 ? `<span class="kvp more">+${keys.length - 3}</span>` : ''}</div></div>
      <div class="cr-fill">
        <div class="fb-row"><span class="fb-lab">Before</span><div class="fb-track"><div class="fb-fill fb-before" style="width:${fb * 100}%"></div></div><span class="fb-num">${fmtPct(fb)}</span></div>
        <div class="fb-row"><span class="fb-lab">After</span><div class="fb-track">${fa != null ? `<div class="fb-fill fb-after" style="width:${fa * 100}%"></div>` : ''}</div><span class="fb-num fb-num-after">${fa != null ? fmtPct(fa) : '—'}</span></div>
        ${fa != null ? `<span class="delta up">+${Math.round((fa - fb) * 100)} pt</span>` : ''}
      </div>
      <div class="cr-metrics">${r.state === 'done' ? `<span title="Normalized attributes">🧩 ${fmtInt(r.attr_count)}</span><span title="Feature tags">🏷 ${fmtInt(r.tag_count)}</span><span title="GMC Q&A">💬 ${fmtInt(r.qa_count)}</span><span class="muted mono" title="Latency">${fmtInt(r.latency_ms)}ms</span>` : r.state === 'run' ? '<span class="muted">Gemini processing…</span>' : '<span class="muted">—</span>'}</div>
      <span>${badge}</span>
      <div class="cr-act">${r.state === 'done' ? `<a class="btn btn-ghost btn-sm" href="#/studio/${encodeURIComponent(p.sku)}" title="View details in Enrichment Studio">✨ Studio</a><a class="btn btn-ghost btn-sm" href="#/gmc/${encodeURIComponent(p.sku)}" title="View GMC conversational attributes">🛍 GMC</a>` : ''}</div>
    </div>${open ? `<div class="csv-detail">${detailHtml(p, r)}</div>` : ''}`;
  }
  function detailHtml(p, r) {
    const keys = Object.keys(p.raw_attributes || {});
    const before = `<div class="cd-col"><div class="sec-lab"><span class="ba-tag before">Before</span> Source</div>
      <div class="cd-desc">${esc(p.description || '(no description)')}</div>
      <table class="raw-table small"><tbody>${keys.map((k) => `<tr><th>${esc(k)}</th><td>${esc(p.raw_attributes[k])}</td></tr>`).join('') || '<tr><td class="muted">No source attributes</td></tr>'}</tbody></table></div>`;
    if (r.state !== 'done' || !r.detail) {
      return before + `<div class="cd-col"><div class="sec-lab"><span class="ba-tag after">After</span> Enriched</div>${emptyState('⏳', r.state === 'run' ? 'Gemini is processing…' : 'Not run yet', 'Click "Run in real time" to see results here')}</div>`;
    }
    const recs = r.detail.records || [];
    const attrs = recs.filter((x) => x.kind === 'attribute');
    const tags = recs.filter((x) => x.kind === 'tag');
    const g = S.gmc[p.sku];
    const qas = g && g.gmc ? g.gmc.questions_and_answers || [] : null;
    return before + `<div class="cd-col"><div class="sec-lab"><span class="ba-tag after">After</span> Enriched <span class="muted small">${attrs.length} attributes • ${tags.length} tags</span></div>
      <div class="cd-attrs">${attrs.map((a) => `<span class="attr-pill st-${(STATUS[a.gate_status] || {}).cls || 'mute'}" title="raw: ${esc(a.raw_value)} / score ${a.score}"><span class="ap-k">${esc(a.label)}</span><b>${esc(a.value === null ? 'Not convertible' : fmtValue(a.value))}</b>${esc(a.unit || '')}</span>`).join('') || '<span class="muted">Nothing extracted</span>'}</div>
      <div class="tags" style="margin-top:8px">${tags.map((t) => `<span class="tag">${esc(t.label)}</span>`).join('')}</div>
      ${r.detail.extras && r.detail.extras.ai_summary ? `<div class="summary" style="margin-top:10px">🤖 ${esc(r.detail.extras.ai_summary)}</div>` : ''}
      <div class="sec-lab" style="margin-top:12px">🛍 GMC conversational attributes Q&amp;A <span class="muted small">question_and_answer</span></div>
      ${qas === null ? '<div class="muted small">Loading…</div>' : qas.length ? `<div class="qa-mini">${qas.map((q) => `<div class="qm"><div class="qm-q">Q. ${esc(q.question)}</div><div class="qm-a">A. ${esc(q.answer)} ${q.evidence_match > 0 ? '<span class="chip chip-ok">Evidence verified</span>' : '<span class="chip chip-warn">Needs review</span>'}</div></div>`).join('')}</div>` : '<div class="muted small">No Q&A</div>'}
    </div>`;
  }
  async function loadGmc(sku) {
    const r = S.rows[sku];
    if (!r || r.state !== 'done' || S.gmc[sku]) return;
    try { S.gmc[sku] = await api.gmcDetail(sku); } catch (e) { S.gmc[sku] = { gmc: { questions_and_answers: [] } }; }
    if (S.paint) S.paint.rows();
  }

  // ── run
  async function run() {
    if (S.running || !S.items.length) return;
    const save = $('#chk-save-bq', root).checked;
    Object.assign(S, { running: true, t0: performance.now(), tin: 0, tout: 0, current: null, doneSkus: [], bq: null, complete: null, rows: {}, gmc: {}, ctrl: new AbortController(), model: runModel(root, 'csv-model') });
    S.items.forEach((p) => { S.rows[p.sku] = { state: 'wait' }; });
    stepper.startLive(`CSV ${S.items.length} items`);
    const agg = { attrs: 0, tags: 0, ok: 0, auto: 0, review: 0, rejected: 0 };
    const tick = setInterval(() => { if (S.paint) S.paint.progress(); }, 200);
    S.paint && S.paint.all();
    const onEvent = (ev) => {
      switch (ev.event) {
        case 'start':
          stepper.setState(0, 'done'); stepper.setCount(0, `<b>${fmtInt(ev.total)}</b> SKUs loaded`);
          break;
        case 'sku_start':
          S.current = ev;
          if (S.rows[ev.sku]) S.rows[ev.sku].state = 'run';
          stepper.setState(1, 'running'); stepper.setCount(1, `Extracting ${ev.index + 1}/${ev.total}`);
          break;
        case 'sku_done': {
          S.rows[ev.sku] = { ...ev, state: 'done' };
          S.tin += ev.input_tokens || 0; S.tout += ev.output_tokens || 0;
          S.doneSkus.push(ev.sku);
          const recs = (ev.detail && ev.detail.records) || [];
          agg.attrs += ev.attr_count || 0; agg.tags += ev.tag_count || 0;
          agg.ok += recs.filter((x) => x.kind === 'attribute' && x.value !== null).length;
          agg.auto += recs.filter((x) => x.gate_status === 'auto_approved').length;
          agg.review += recs.filter((x) => x.gate_status === 'needs_review').length;
          agg.rejected += recs.filter((x) => x.gate_status === 'rejected').length;
          const last = ev.index + 1 >= ev.total;
          stepper.setState(1, last ? 'done' : 'running'); stepper.setCount(1, `<b>${fmtInt(agg.attrs)}</b> attributes + <b>${fmtInt(agg.tags)}</b> tags`);
          stepper.setState(2, 'done'); stepper.setCount(2, `<b>${fmtInt(agg.ok)}</b>/${fmtInt(agg.attrs)} OK`);
          stepper.setState(3, 'done'); stepper.setCount(3, `<span class="sc-ok">Auto <b>${fmtInt(agg.auto)}</b></span> <span class="sc-warn">Review <b>${fmtInt(agg.review)}</b></span>`);
          if (S.open.has(ev.sku)) loadGmc(ev.sku);
          break;
        }
        case 'sku_error':
          S.rows[ev.sku] = { state: 'error', message: ev.message };
          toast(`${ev.sku}: ${ev.message}`, 'err', 6000);
          break;
        case 'bq_saving':
          S.bqSaving = true;
          stepper.setState(4, 'running'); stepper.setCount(4, 'Saving to BigQuery…');
          break;
        case 'complete':
          S.complete = ev; S.bqSaving = false;
          if (ev.input_tokens != null) { S.tin = ev.input_tokens; S.tout = ev.output_tokens; }
          if (ev.bq_result) {
            S.bq = ev.bq_result;
            stepper.setState(4, 'done'); stepper.setCount(4, `<b>${fmtInt(ev.bq_result.skus)}</b> SKU → BigQuery`);
          } else {
            stepper.setState(4, ev.processed ? 'done' : 'error'); stepper.setCount(4, `<b>${fmtInt(ev.processed)}</b> SKUs (not saved to BQ)`);
          }
          break;
        case 'error':
          S.bqSaving = false;
          toast(ev.message || 'An error occurred', 'err', 7000);
          break;
        default: break;
      }
      if (S.paint) S.paint.all();
    };
    try {
      await api.csvEnrich({ products: S.items, csv_text: S.csvText, save_to_bq: save }, onEvent, S.ctrl.signal, S.model);
      if (S.complete) toast(`Enrichment complete for ${S.complete.processed}/${S.complete.total} SKUs${S.bq ? ' (saved to BigQuery)' : ''}`, 'ok', 5000);
      else toast('No results received', 'warn');
    } catch (e) {
      if (e.name !== 'AbortError') toast('Run failed: ' + e.message, 'err', 7000);
    } finally {
      clearInterval(tick);
      S.running = false; S.bqSaving = false; S.current = null;
      Object.values(S.rows).forEach((r) => { if (r.state === 'run') r.state = 'error'; });
      state.products = null; // new SKUs → reload product list (studio / products)
      refreshShell();
      stepper.endLive(12000);
      if (S.paint) S.paint.all();
    }
  }

  async function saveBq() {
    if (!S.doneSkus.length || S.bqSaving) return;
    S.bqSaving = true; paint();
    stepper.startLive('BigQuery');
    stepper.setState(4, 'running'); stepper.setCount(4, 'Saving to BigQuery…');
    try {
      const res = await api.csvSaveBq(S.doneSkus);
      S.bq = { skus: res.skus, tables: res.tables };
      stepper.setState(4, 'done'); stepper.setCount(4, `<b>${fmtInt(res.skus)}</b> SKU → BigQuery`);
      toast(`Saved ${res.skus} SKUs to BigQuery`, 'ok');
    } catch (e) {
      stepper.setState(4, 'error');
      toast('BigQuery save failed: ' + e.message, 'err', 7000);
    } finally { S.bqSaving = false; stepper.endLive(8000); paint(); }
  }

  S.paint = { all: paint, progress: paintProgress, rows: paintRows };
  paint();
  return () => { S.paint = null; };
}
