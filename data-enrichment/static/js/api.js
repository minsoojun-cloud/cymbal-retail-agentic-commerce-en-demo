// API client. Real backend by default; `?mock=1` serves fixtures from /static/mock/*.json.
const MOCK = new URLSearchParams(location.search).has('mock');
const MOCK_BASE = (() => {
  // Works both under FastAPI (/static/...) and python http.server (/static/index.html).
  const m = location.pathname.match(/^(.*\/static)\//);
  return (m ? m[1] : '/static') + '/mock/';
})();

export const isMock = MOCK;
// Storefront base URL (rendered server-side into the header #store-link from env STORE_URL).
export const STORE_URL = (() => {
  const a = typeof document !== 'undefined' ? document.getElementById('store-link') : null;
  const h = (a && a.getAttribute('href')) || '';
  return /^https?:\/\//.test(h) ? h.replace(/\/+$/, '') : '';
})();
// Mock fixtures carry storefront-relative uris ("/img/products/<file>", "/?product=<id>") → absolute storefront URLs.
const absUris = (o) => {
  if (typeof o === 'string') return STORE_URL && (o.startsWith('/img/') || o.startsWith('/?product=')) ? STORE_URL + o : o;
  if (Array.isArray(o)) return o.map(absUris);
  if (o && typeof o === 'object') { Object.keys(o).forEach((k) => { o[k] = absUris(o[k]); }); }
  return o;
};
const fmtYen = (n) => Math.round(Number(n) || 0).toLocaleString('en-US', { style: 'currency', currency: 'JPY' });

// ── Selected Gemini model (header picker, persisted) — sent with every request as X-Enrich-Model ──
const MODEL_KEY = 'cymbal.enrich.model';
export const modelPref = {
  get() { try { return localStorage.getItem(MODEL_KEY) || ''; } catch (_) { return ''; } },
  set(m) {
    try { if (m) localStorage.setItem(MODEL_KEY, m); else localStorage.removeItem(MODEL_KEY); } catch (_) { /* ignore */ }
    window.dispatchEvent(new CustomEvent('enrich:model', { detail: m }));
  },
};
const modelHeaders = (override) => { const m = override || modelPref.get(); return m ? { 'X-Enrich-Model': m } : {}; };

export class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

async function http(method, url, body, model) {
  const opt = { method, headers: { ...modelHeaders(model) } };
  if (body !== undefined) {
    opt.headers['Content-Type'] = 'application/json';
    opt.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(url, opt);
  } catch (e) {
    throw new ApiError('Cannot connect to the server', 0);
  }
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      if (j && j.detail) msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail);
    } catch (_) { /* ignore */ }
    throw new ApiError(msg, res.status);
  }
  return res.json();
}

// Multipart upload (field `file`). XHR so the UI can show upload progress.
function httpForm(url, file, onProgress, signal) {
  return new Promise((resolve, reject) => {
    const fd = new FormData();
    fd.append('file', file, file.name);
    const xhr = new XMLHttpRequest();
    xhr.open('POST', url);
    xhr.responseType = 'text';
    if (onProgress && xhr.upload) xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
    xhr.onload = () => {
      let j = null;
      try { j = JSON.parse(xhr.responseText || 'null'); } catch (_) { /* ignore */ }
      if (xhr.status >= 200 && xhr.status < 300) { resolve(j); return; }
      const msg = j && j.detail ? (typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)) : `${xhr.status} ${xhr.statusText}`;
      reject(new ApiError(msg, xhr.status));
    };
    xhr.onerror = () => reject(new ApiError('Cannot connect to the server', 0));
    xhr.onabort = () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' }));
    if (signal) { if (signal.aborted) { reject(Object.assign(new Error('aborted'), { name: 'AbortError' })); return; } signal.addEventListener('abort', () => xhr.abort(), { once: true }); }
    xhr.send(fd);
  });
}

// ── Mock layer ────────────────────────────────────────────────────────────
const mockCache = {};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function fixture(name) {
  if (!mockCache[name]) {
    const res = await fetch(MOCK_BASE + name + '?v=20261111q');
    if (!res.ok) throw new ApiError(`mock ${name} not found`, 404);
    mockCache[name] = absUris(await res.json());
  }
  return mockCache[name];
}
const clone = (o) => JSON.parse(JSON.stringify(o));

const mock = {
  async config() { const c = await fixture('config.json'); if (!c.store_url) c.store_url = STORE_URL; return c; },
  async dashboard() {
    const d = clone(await fixture('dashboard.json'));
    const rv = await fixture('reviews.json');
    // keep the badge consistent with the (small) mock review queue
    const remaining = rv.items.filter((x) => !mockState.done.has(x.rec.sku + '|' + x.rec.attr_key)).length;
    d.totals.needs_review = remaining;
    d.funnel.needs_review = remaining;
    d.totals.human_approved += mockState.done.size;
    return d;
  },
  async products(p = {}) {
    const d = clone(await fixture('products.json'));
    let items = [...Object.values(mockState.csvItems).map(clone), ...d.items.filter((i) => !mockState.csvItems[i.sku])];
    // SKUs with a full detail fixture first, so the studio opens on a rich example
    items = [...items.filter((i) => FIXTURE_SKUS.has(i.sku)), ...items.filter((i) => !FIXTURE_SKUS.has(i.sku))];
    if (p.group) items = items.filter((i) => i.group === p.group);
    if (p.status) items = items.filter((i) => i.status === p.status);
    if (p.q) items = items.filter((i) => (i.title + i.brand + i.sku).toLowerCase().includes(p.q.toLowerCase()));
    return { items, total: items.length };
  },
  async product(sku) {
    if (mockState.csvDetails[sku]) return clone(mockState.csvDetails[sku]);
    try { if (!FIXTURE_SKUS.has(sku)) throw new Error('no fixture'); return clone(await fixture(`product_${sku}.json`)); } catch (e) {
      const list = await fixture('products.json');
      const it = list.items.find((i) => i.sku === sku);
      if (!it) throw new ApiError('Product not found', 404);
      // Minimal detail for SKUs without a dedicated fixture
      return {
        product: { ...it, category: it.group_label, description: '(Mock: no detail fixture for this product)', product_url: '#', raw_attributes: {} },
        source_text: `[Title] ${it.title}`, targets: [], records: [], extras: { search_keywords: [], ai_summary: '' },
        status: it.status, fill_before: it.fill_before, fill_after: it.fill_after,
      };
    }
  },
  async reviews(p = {}) {
    const d = await fixture('reviews.json');
    let items = d.items.filter((x) => !mockState.done.has(x.rec.sku + '|' + x.rec.attr_key));
    if (p.group) items = items.filter((x) => x.product.group_label === p.group);
    return clone({ items, count: items.length });
  },
  async review(sku, attrKey, body) {
    await sleep(250);
    const d = await fixture('reviews.json');
    const it = d.items.find((x) => x.rec.sku === sku && x.rec.attr_key === attrKey);
    if (!it) throw new ApiError('Review item not found', 404);
    mockState.done.add(sku + '|' + attrKey);
    mockState.reviewed = mockState.done.size;
    const map = { approve: 'approved', edit: 'edited', reject: 'human_rejected' };
    const rec = clone(it.rec);
    rec.gate_status = map[body.action];
    if (body.action === 'edit') rec.value = body.value;
    rec.reviewed = { action: body.action, comment: body.comment || '', at: new Date().toISOString() };
    // reflect into product fixture
    const pd = mockCache[`product_${sku}.json`];
    if (pd) {
      const r = pd.records.find((x) => x.attr_key === attrKey);
      if (r) Object.assign(r, rec);
    }
    return { record: rec, remaining: d.items.length - mockState.done.size };
  },
  async batchJobs() { return clone(await fixture('batch_jobs.json')); },
  async batchSubmit(body) {
    await sleep(600);
    const d = await fixture('batch_jobs.json');
    const now = new Date().toISOString();
    const job = {
      job_id: `projects/123/locations/global/batchPredictionJobs/${Math.floor(Math.random() * 9000 + 1000)}`,
      display_name: `enrich-${now.slice(0, 16).replace(/[-:T]/g, '')}${body.group ? '-' + body.group : ''}`,
      model: 'gemini-3.8-flash', state: 'JOB_STATE_PENDING', sku_count: body.limit, request_count: body.limit * 2,
      input_tokens: 0, output_tokens: 0, cost_usd: 0, cost_jpy: 0, created_at: now, updated_at: now, ingested: false,
    };
    d.jobs.unshift(job);
    return { job: clone(job) };
  },
  async batchRefresh() {
    await sleep(700);
    const d = await fixture('batch_jobs.json');
    d.jobs.forEach((j) => {
      if (j.state === 'JOB_STATE_PENDING') j.state = 'JOB_STATE_RUNNING';
      else if (j.state === 'JOB_STATE_RUNNING') {
        j.state = 'JOB_STATE_SUCCEEDED'; j.input_tokens = j.sku_count * 1523; j.output_tokens = j.sku_count * 582;
        j.cost_usd = +(j.sku_count * 0.052 / 150).toFixed(5); j.cost_jpy = +(j.sku_count * 0.052).toFixed(2); j.ingested = true;
      }
      j.updated_at = new Date().toISOString();
    });
    return clone(d);
  },
  async costEstimate(skus) { const d = await mock.costModels({ skus }); const r = d.models[0]; return { skus, model: r.id, total_usd: r.estimate && r.estimate.total_usd, per_sku_usd: r.estimate && r.estimate.per_sku_usd, total_jpy: r.estimate && r.estimate.total_jpy, per_sku_jpy: r.estimate && r.estimate.per_sku_jpy, price_available: true }; },
  // Mock model registry / list-price table (prices = the official values recorded in app/pricing.py; tokens are mock).
  async models() { return { models: MOCK_MODELS.map((m) => ({ ...m })), default: 'gemini-3.8-flash', selected: modelPref.get() || 'gemini-3.8-flash' }; },
  async costModels(p = {}) {
    const skus = +p.skus || 8807, rate = +p.usd_jpy || 150;
    const rows = MOCK_MODELS.map((m) => {
      const pr = MOCK_PRICES[m.id];
      const meas = mockState.measured[m.id] || null;
      let est = null;
      if (meas) {
        const tin = meas.input_tokens * skus, tout = meas.output_tokens * skus, tth = meas.thinking_tokens * skus;
        est = { skus, tokens: { input: tin, output: tout, thinking: tth }, price_available: !!pr };
        if (pr) {
          const bi = tin / 1e6 * pr[0], bo = tout / 1e6 * pr[1], bt = tth / 1e6 * pr[1], tot = bi + bo + bt;
          Object.assign(est, { usd_jpy: rate, total_usd: tot, total_jpy: tot * rate, per_sku_usd: tot / skus, per_sku_jpy: tot * rate / skus,
            breakdown: { input: { usd: bi, jpy: bi * rate }, output: { usd: bo, jpy: bo * rate }, thinking: { usd: bt, jpy: bt * rate } } });
        }
      }
      return { ...m, measurement: meas, estimate: est,
        price: { available: !!pr, input_usd_per_mtok: pr ? pr[0] : null, output_usd_per_mtok: pr ? pr[1] : null, note: pr ? (pr[2] || '') : 'See the official pricing page', periods: [], url: PRICING_URL } };
    });
    return { skus, usd_jpy: rate, basis: p.basis || 'current', as_of: '2026-11-11', pricing_url: PRICING_URL, price_retrieved_at: '2026-11-11',
      catalog_count: 8807, demo_skus: 42, sample_sku: { sku: '4909270706758', title: 'Shizuoka Swordfish Fillets, 3 fillets (about 240g)', chars: 182 }, unavailable: [], models: rows, cheapest: null };
  },
  async costMeasure(model, onEvent) {
    onEvent({ event: 'start', model, sku: '4909270706758', title: 'Shizuoka Swordfish Fillets, 3 fillets (about 240g)', chars: 182 });
    for (const st of ['stage1', 'stage3', 'gmc']) { await sleep(350); onEvent({ event: 'stage', stage: st, status: 'done', input_tokens: 700, response_tokens: 200, thinking_tokens: 80 }); }
    mockState.measured[model] = { runs: 1, skus: 1, input_tokens: 2100, output_tokens: 600, thinking_tokens: 240, only_sample: true, sample_sku: '4909270706758', sample_title: 'Shizuoka Swordfish Fillets, 3 fillets (about 240g)', source_chars: 182, sources: ['sample'] };
    onEvent({ event: 'result', measurement: mockState.measured[model] });
  },
  async golden() { return clone(await fixture('golden.json')); },
  ...mockCsvGmc(),
  async enrich(sku, onEvent) {
    const detail = await mock.product(sku);
    const recs = detail.records;
    const nAttr = recs.filter((r) => r.kind === 'attribute').length;
    const nTag = recs.filter((r) => r.kind === 'tag').length;
    const summ = { auto_approved: 0, needs_review: 0, rejected: 0 };
    recs.forEach((r) => { if (summ[r.gate_status] !== undefined) summ[r.gate_status] += 1; });
    const evs = [
      [200, { event: 'start', sku, model: 'gemini-3.8-flash' }],
      [300, { event: 'stage', stage: 'stage1', status: 'running' }],
      [100, { event: 'stage', stage: 'stage3', status: 'running' }],
      [1800, { event: 'stage', stage: 'stage3', status: 'done', latency_ms: 1840, count: nTag }],
      [700, { event: 'stage', stage: 'stage1', status: 'done', latency_ms: 2620, input_tokens: 1523, output_tokens: 611, count: nAttr }],
      [600, { event: 'stage', stage: 'normalize', status: 'done', count: nAttr, failed: Math.min(1, nAttr) }],
      [700, { event: 'stage', stage: 'gate', status: 'done', summary: summ }],
      [400, { event: 'result', detail }],
    ];
    for (const [ms, ev] of evs) { await sleep(ms); onEvent(ev); }
  },
};
const mockState = { done: new Set(), reviewed: 0, csvItems: {}, csvDetails: {}, gmc: {}, measured: {} };
export const PRICING_URL = 'https://cloud.google.com/vertex-ai/generative-ai/pricing';
const MOCK_MODELS = [
  ['gemini-3.8-flash', 'Gemini 3.8 Flash', true], ['gemini-3.5-flash', 'Gemini 3.5 Flash', true], ['gemini-3.5-flash-lite', 'Gemini 3.5 Flash-Lite', true],
  ['gemini-3.1-flash-lite', 'Gemini 3.1 Flash-Lite', null], ['gemini-3-flash-preview', 'Gemini 3 Flash Preview', null],
].map(([id, label, batch]) => ({ id, label, status: 'available', reason: '', default: id === 'gemini-3.8-flash', batch, batch_note: batch ? '' : 'Disabled: Batch Prediction support not verified (use a live run)' }));
const MOCK_PRICES = { 'gemini-3.8-flash': [1.5, 7.5, 'List price (no promotion applied)'], 'gemini-3.5-flash': [1.5, 9.0], 'gemini-3.5-flash-lite': [0.3, 2.5], 'gemini-3.1-flash-lite': [0.25, 1.5] };

// ── SSE over POST (fetch + ReadableStream) ─────────────────────────────────
async function postStream(url, body, onEvent, signal, model) {
  let res;
  const headers = { Accept: 'text/event-stream', ...modelHeaders(model) };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  try {
    res = await fetch(url, { method: 'POST', headers, body: body !== undefined ? JSON.stringify(body) : undefined, signal });
  } catch (e) {
    if (e.name === 'AbortError') throw e;
    throw new ApiError('Cannot connect to the server', 0);
  }
  if (!res.ok || !res.body) {
    let msg = `${res.status} ${res.statusText}`;
    try { const j = await res.json(); if (j && j.detail) msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail); } catch (_) { /* ignore */ }
    throw new ApiError(msg, res.status);
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder('utf-8');
  let buf = '';
  const flush = (chunk) => {
    const lines = chunk.split(/\r?\n/).filter((l) => l.startsWith('data:'));
    if (!lines.length) return;
    const data = lines.map((l) => l.slice(5).replace(/^ /, '')).join('\n');
    try { onEvent(JSON.parse(data)); } catch (e) { console.warn('bad SSE payload', data); }
  };
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let idx;
    while ((idx = buf.search(/\r?\n\r?\n/)) >= 0) {
      const m = buf.match(/\r?\n\r?\n/);
      flush(buf.slice(0, idx));
      buf = buf.slice(idx + m[0].length);
    }
  }
  if (buf.trim()) flush(buf);
}

const enrichStream = (sku, onEvent, signal, model) => postStream(`/api/products/${encodeURIComponent(sku)}/enrich`, undefined, onEvent, signal, model);

const qs = (p) => {
  const u = new URLSearchParams();
  Object.entries(p || {}).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== '') u.set(k, v); });
  const s = u.toString();
  return s ? '?' + s : '';
};

const real = {
  config: () => http('GET', '/api/config'),
  dashboard: () => http('GET', '/api/dashboard'),
  products: (p) => http('GET', '/api/products' + qs(p)),
  product: (sku) => http('GET', `/api/products/${encodeURIComponent(sku)}`),
  reviews: (p) => http('GET', '/api/reviews' + qs(p)),
  review: (sku, attrKey, body) => http('POST', `/api/reviews/${encodeURIComponent(sku)}/${encodeURIComponent(attrKey)}`, body),
  batchJobs: () => http('GET', '/api/batch/jobs'),
  batchSubmit: (body, model) => http('POST', '/api/batch/jobs', body, model),
  batchRefresh: () => http('POST', '/api/batch/jobs/refresh'),
  costEstimate: (skus) => http('GET', '/api/cost/estimate' + qs({ skus })),
  models: (refresh) => http('GET', '/api/models' + qs({ refresh: refresh ? 1 : '' })),
  costModels: (p) => http('GET', '/api/cost/models' + qs(p)),
  costMeasure: (model, onEvent, signal) => postStream('/api/cost/measure', { model }, onEvent, signal, model),
  golden: (p) => http('GET', '/api/golden' + qs(p)),
  enrich: enrichStream,
  csvSample: () => http('GET', '/api/csv/sample'),
  csvParse: (body) => http('POST', '/api/csv/parse', body),
  csvEnrich: (body, onEvent, signal, model) => postStream('/api/csv/enrich', body, onEvent, signal, model),
  csvSaveBq: (skus) => http('POST', '/api/csv/save-bq', { skus }),
  gmcList: (p) => http('GET', '/api/gmc' + qs(p)),
  gmcDetail: (sku) => http('GET', `/api/gmc/${encodeURIComponent(sku)}`),
  gmcGenerate: (sku, model) => http('POST', `/api/gmc/${encodeURIComponent(sku)}/generate`, undefined, model),
  gmcUpdate: (sku, body) => http('PUT', `/api/gmc/${encodeURIComponent(sku)}`, body),
  // (1) Seasonal images
  imagePresets: (sku) => http('GET', '/api/images/presets' + qs({ sku })),
  productImages: (sku) => http('GET', `/api/products/${encodeURIComponent(sku)}/images`),
  uploadBaseImage: (sku, file, onProgress, signal) => httpForm(`/api/products/${encodeURIComponent(sku)}/images/upload`, file, onProgress, signal),
  searchImages: (sku) => http('POST', `/api/products/${encodeURIComponent(sku)}/images/search`, {}),
  generateImages: (sku, body, onEvent, signal) => postStream(`/api/products/${encodeURIComponent(sku)}/images/generate`, body, onEvent, signal),
  saveImages: (sku, body) => http('POST', `/api/products/${encodeURIComponent(sku)}/images/save`, body),
  deleteImage: (sku, imageId) => http('DELETE', `/api/products/${encodeURIComponent(sku)}/images/${encodeURIComponent(imageId)}`),
  // (2) Extract from documents
  docsUpload: (file, onProgress, signal) => httpForm('/api/docs/upload', file, onProgress, signal),
  docsList: (gcsUri) => http('POST', '/api/docs/list', { gcs_uri: gcsUri }),
  docsExtract: (body, onEvent, signal, model) => postStream('/api/docs/extract', body, onEvent, signal, model),
  docsPreview: (body) => http('POST', '/api/docs/preview', body),
  docsApply: (body, onEvent, signal, model) => postStream('/api/docs/apply', body, onEvent, signal, model),
};
Object.assign(mock, mockImagesDocs());

export const api = MOCK ? mock : real;
export const goldenCsvUrl = MOCK ? null : '/api/golden/export.csv';
export const sampleCsvUrl = '/api/csv/sample.csv';
export const gmcTsvUrl = '/api/gmc/export.tsv';
export const gmcCsvUrl = '/api/gmc/export.csv';
export const GMC_SPEC_URL = 'https://support.google.com/merchants/answer/17085370?hl=en';

// ── Mock: CSV live test + GMC conversational attributes ─────────────────────
const MOCK_MODEL = 'gemini-3.8-flash';
const HEADER_ALIASES = {
  sku: ['sku', 'id', 'p_cd', 'product_id', 'item_id', 'product_code', 'item_code', 'item_number', 'jan', 'gtin', 'upc'],
  title: ['title', 'name', 'product_name', 'item_name', 'product_title'],
  brand: ['brand', 'brands', 'maker', 'manufacturer'],
  category: ['category', 'categories', 'category_path', 'product_category', 'department'],
  description: ['description', 'desc', 'body', 'product_description', 'long_description', 'details'],
  price: ['price', 'sale_price', 'price_jpy', 'retail_price', 'price_tax_included'],
  product_url: ['product_url', 'uri', 'url', 'link', 'product_link'],
  image_url: ['image_url', 'image', 'image_link', 'thumbnail', 'product_image'],
  group: ['group', 'category_group'],
};
// Cymbal Retail category groups (mirrors app/schema.py; refreshed from mock/config.json `groups` / `group_short`).
const GROUP_LABELS = { fresh: 'Fresh (produce, meat, seafood)', prepared: 'Deli, frozen & staples', grocery: 'Grocery & beverages', kitchen: 'Kitchen & cookware', living: 'Household & home goods', beauty: 'Beauty & personal care', pet: 'Pet supplies' };
const GROUP_SHORT = { fresh: 'Fresh', prepared: 'Deli & frozen', grocery: 'Grocery', kitchen: 'Kitchen', living: 'Household', beauty: 'Beauty', pet: 'Pet' };
async function syncGroupLabels() {
  try { const c = await fixture('config.json'); Object.assign(GROUP_LABELS, c.groups || {}); Object.assign(GROUP_SHORT, c.group_short || {}); } catch (_) { /* keep defaults */ }
}
const FOOD_G = ['fresh', 'prepared', 'grocery'];
const GM_G = ['kitchen', 'living', 'beauty', 'pet'];
const ALL_G = [...FOOD_G, ...GM_G];
// [key, label, type, unit, groups, raw_keys] — mirrors app/schema.py ATTRS
const MOCK_ATTRS = [
  ['net_weight_g', 'Net weight', 'number', 'g', [...FOOD_G, 'pet'], ['size']],
  ['net_volume_ml', 'Volume / capacity', 'number', 'ml', ['grocery', 'prepared', 'kitchen', 'living', 'beauty'], ['size', 'capacity']],
  ['pack_count', 'Pack count', 'number', 'pcs', ALL_G, ['size']],
  ['servings', 'Servings', 'number', 'servings', ['prepared', 'grocery'], []],
  ['origin', 'Origin / country of origin', 'text', '', ALL_G, ['origin']],
  ['storage_method', 'Storage', 'text', '', FOOD_G, ['storage_type']],
  ['allergens', 'Allergens', 'text', '', [...FOOD_G, 'pet'], ['allergens']],
  ['best_before_days', 'Best before (shelf life)', 'number', 'days', ['prepared', 'grocery'], []],
  ['cook_time_min', 'Cooking time', 'number', 'min', FOOD_G, []],
  ['cooking_method', 'Cooking method', 'text', '', ['prepared', 'grocery', 'fresh'], []],
  ['dishes', 'Suggested dishes / uses', 'text', '', FOOD_G, ['dishes']],
  ['season', 'Peak season', 'text', '', ['fresh'], ['season_months']],
  ['flavor', 'Flavor / spiciness', 'text', '', ['prepared', 'grocery'], []],
  ['material', 'Material', 'text', '', GM_G, ['material']],
  ['width_cm', 'Width', 'number', 'cm', ['kitchen', 'living', 'pet'], ['size']],
  ['depth_cm', 'Depth', 'number', 'cm', ['kitchen', 'living', 'pet'], ['size']],
  ['height_cm', 'Height', 'number', 'cm', ['kitchen', 'living', 'pet'], ['size']],
  ['diameter_cm', 'Diameter', 'number', 'cm', ['kitchen'], ['size']],
  ['color', 'Color', 'text', '', GM_G, ['color']],
  ['ih_compatible', 'Induction (IH) compatible', 'bool', '', ['kitchen'], []],
  ['dishwasher_safe', 'Dishwasher safe', 'bool', '', ['kitchen'], []],
  ['microwave_safe', 'Microwave safe', 'bool', '', ['kitchen'], []],
  ['target_pet', 'Pet type', 'text', '', ['pet'], []],
  ['target_age', 'Life stage / age', 'text', '', ['pet'], []],
  ['scent', 'Scent', 'text', '', ['beauty', 'living'], []],
  ['skin_type', 'Skin / hair type', 'text', '', ['beauty'], []],
];
const ATTR_LABEL = Object.fromEntries(MOCK_ATTRS.map((a) => [a[0], a[1]]));
const ATTR_UNIT = Object.fromEntries(MOCK_ATTRS.map((a) => [a[0], a[3]]));
// raw catalog key → golden attribute keys it can carry
const RAW_TO_ATTR = {
  size: ['net_weight_g', 'net_volume_ml', 'pack_count', 'diameter_cm', 'width_cm', 'depth_cm', 'height_cm'], capacity: ['net_volume_ml'],
  origin: ['origin'], storage_type: ['storage_method'], allergens: ['allergens'], material: ['material'], color: ['color'], dishes: ['dishes'], season_months: ['season'],
};
// app/schema.py TOP_CATEGORY_GROUP + category_group() keyword fallbacks
const TOP_CATEGORY_GROUP = {
  Produce: 'fresh', Meat: 'fresh', Seafood: 'fresh', Flowers: 'fresh',
  Frozen: 'prepared', 'Deli & Meal Kits': 'prepared', 'Rice & Noodles': 'prepared',
  'Seasonings & Sauces': 'grocery', Pantry: 'grocery', 'Snacks & Sweets': 'grocery', 'Eggs & Dairy': 'grocery',
  'Tofu & Natto': 'grocery', Beverages: 'grocery', Bakery: 'grocery', Alcohol: 'grocery', Baby: 'grocery',
  'Health & Medicine': 'beauty',
  Kitchen: 'kitchen', Household: 'living', 'Home & Living': 'living',
  'Beauty & Personal Care': 'beauty', Pet: 'pet',
};
const GROUP_KEYWORDS = [
  ['pet', ['pet', 'dog', 'cat food', 'cat ', 'puppy', 'kitten']],
  ['beauty', ['cosmetic', 'skin care', 'skincare', 'shampoo', 'bath salt', 'hand cream', 'makeup', 'body wash', 'lotion']],
  ['kitchen', ['frying pan', 'fry pan', 'skillet', 'pot', 'knife', 'cutting board', 'food container', 'cookware', 'kitchen tool', 'tableware', 'kettle']],
  ['living', ['detergent', 'tissue', 'sponge', 'umbrella', 'towel', 'storage box', 'home goods', 'household']],
  ['prepared', ['frozen', 'deli', 'bento', 'meal kit', 'retort', 'cup noodle', 'instant', 'rice']],
  ['fresh', ['fresh fish', 'fillet', 'vegetable', 'fruit', 'beef', 'pork', 'chicken', 'sashimi']],
];
// app/schema.py ALLERGEN_VARIANTS (lowercase spelling variants → canonical), canonical order = ALLERGENS_28
const ALLERGENS_28 = ['shrimp', 'crab', 'walnut', 'wheat', 'buckwheat', 'egg', 'milk', 'peanut', 'almond', 'abalone', 'squid', 'salmon roe', 'orange', 'cashew nut',
  'kiwifruit', 'beef', 'sesame', 'salmon', 'mackerel', 'soybean', 'chicken', 'banana', 'pork', 'matsutake', 'peach', 'yam', 'apple', 'gelatin'];
const ALLERGEN_CANON = {
  shrimps: 'shrimp', prawn: 'shrimp', prawns: 'shrimp', crabs: 'crab', walnuts: 'walnut', 'wheat flour': 'wheat', 'soba flour': 'buckwheat', eggs: 'egg',
  dairy: 'milk', 'milk ingredients': 'milk', 'milk products': 'milk', peanuts: 'peanut', groundnut: 'peanut', almonds: 'almond', ikura: 'salmon roe',
  oranges: 'orange', 'cashew nuts': 'cashew nut', cashew: 'cashew nut', cashews: 'cashew nut', 'kiwi fruit': 'kiwifruit', kiwi: 'kiwifruit',
  'sesame seeds': 'sesame', soybeans: 'soybean', soy: 'soybean', soya: 'soybean', bananas: 'banana', 'matsutake mushroom': 'matsutake',
  peaches: 'peach', yams: 'yam', nagaimo: 'yam', apples: 'apple', gelatine: 'gelatin',
};
// 8 mandatory allergens: product-name mentions that must appear in the allergen statement
const ALLERGEN_TITLE_HINT = { shrimp: /shrimp|prawn/i, crab: /\bcrab/i, walnut: /walnut/i, wheat: /wheat|udon|pasta|bread/i, buckwheat: /buckwheat|\bsoba\b/i, egg: /\begg/i, peanut: /peanut/i };
// feature-tag evidence hints (tag → regex over title + description); default: literal tag text
const TAG_HINTS = {
  Microwavable: /microwave/i, 'Microwave cooking': /microwave/i, 'Pan cooking': /frying pan|skillet|pan-fr/i, 'Good for hot pot': /hot ?pot|nabe|shabu|oden/i, 'Snack with drinks': /snack|with drinks|appetizer/i, 'Frying pan': /frying pan|skillet/i, 'Boil-in-bag': /boil[- ]in[- ]bag|in boiling water/i, 'Just heat': /just heat|heat and serve|heat & serve/i,
  'Freezer storage': /frozen|freezer|-18/i, 'Time-saver': /quick|just heat|just grill|ready in|easy|time[- ]sav/i, 'Good for bento': /bento|lunch ?box/i,
  'Domestic (Japan)': /\bjapan\b|domestic/i, 'Domestic ingredients': /\bjapan(?:ese)?\b|domestic/i, 'Great for hot pot': /hot ?pot|nabe|shabu|oden/i,
  'In season': /in season|peak season|season:/i, 'Individually wrapped': /individually wrapped|single[- ]serve/i, 'Value pack / bulk': /value pack|bulk|family size/i,
  'Shelf-stable': /room temperature|shelf[- ]stable|ambient/i, 'Non-stick': /non[- ]stick|fluororesin/i, Lightweight: /lightweight|light weight/i,
  'Dishwasher safe': /dishwasher[- ]safe/i, 'Induction compatible': /induction|\bih\b/i, 'Gas compatible': /gas (?:stove|flame|burner)|gas[- ]compatible/i,
  'Microwave safe': /microwave[- ]safe/i, 'Freezer safe': /freezer[- ]safe/i, 'Pre-cut / prepped': /pre-?cut|prepped|ready to cook/i, 'Boneless / easy to eat': /boneless|deboned/i,
  Refill: /refill/i, 'For sensitive skin': /sensitive skin/i, 'For dogs': /\bdogs?\b|puppy/i, 'For cats': /\bcats?\b|kitten/i, 'For seniors': /senior|7\+|7 years/i,
  'Snack / appetizer': /snack|appetizer|with drinks/i, 'Kid favorite': /kids?\b|children/i, Organic: /organic/i, Party: /party/i, Entertaining: /entertain|guests/i,
};

function parseCsvRows(text) {
  const rows = []; let row = []; let f = ''; let q = false;
  text = String(text || '').replace(/^\ufeff/, '');
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) {
      if (c === '"') { if (text[i + 1] === '"') { f += '"'; i++; } else q = false; } else f += c;
    } else if (c === '"') q = true;
    else if (c === ',') { row.push(f); f = ''; }
    else if (c === '\n' || c === '\r') { if (c === '\r' && text[i + 1] === '\n') i++; row.push(f); rows.push(row); row = []; f = ''; }
    else f += c;
  }
  if (f !== '' || row.length) { row.push(f); rows.push(row); }
  return rows.filter((r) => r.some((x) => String(x).trim()));
}
// Port of app/schema.py category_group(): top category name → keyword fallbacks → grocery
function detectGroup(s) {
  s = String(s || '');
  const top = s.split('>')[0].trim();
  if (TOP_CATEGORY_GROUP[top]) return TOP_CATEGORY_GROUP[top];
  for (const [k, g] of Object.entries(TOP_CATEGORY_GROUP)) if (s.includes(k)) return g;
  for (const [g, words] of GROUP_KEYWORDS) if (words.some((w) => s.includes(w))) return g;
  return 'grocery';
}
function mockTargets(group) {
  return MOCK_ATTRS.filter((a) => a[4].includes(group)).map(([key, label, type, unit, , rawKeys]) => ({ key, label, unit, type, raw_keys: [...rawKeys] }));
}

// ── mock extraction helpers (regex only; the real backend uses Gemini + Python normalizers) ──
const nfkc = (s) => String(s == null ? '' : s).normalize('NFKC');
const toNum = (s) => Number(String(s).replace(/,/g, ''));
const r2 = (n) => Math.round(n * 100) / 100;
// '450 g' / '1.2 kg' / '200 ml x 3' / '10 pcs' / '3 fillets (approx. 240 g)' / '28 cm' / 'diameter 26 x depth 7.5 cm' / '15 x 18 cm'
const COUNT_WORDS = 'pcs|pieces|piece|pc|bottles|bottle|sheets|sheet|fillets|fillet|slices|slice|bags|bag|servings|serving|packs|pack|bunches|bunch|cans|can|rolls|roll|count|ct';
function parseSize(v, group) {
  const raw = String(v || ''); const s = nfkc(raw);
  const out = []; let m;
  if ((m = s.match(/(?:(?:approx\.?|about)\s*)?([\d.,]+)\s*kg(?![a-z])/i))) out.push({ key: 'net_weight_g', value: r2(toNum(m[1]) * 1000), unit: 'g', note: 'Converted kg → g', ev: m[0].trim() });
  else if ((m = s.match(/(?:(?:approx\.?|about)\s*)?([\d.,]+)\s*g(?![a-z])(\s*[×x]\s*(\d+))?/i))) {
    out.push({ key: 'net_weight_g', value: r2(toNum(m[1]) * (m[3] ? Number(m[3]) : 1)), unit: 'g', note: m[3] ? 'Summed "x count" notation' : '', ev: m[0].trim() });
  }
  if ((m = s.match(/(?:(?:approx\.?|about)\s*)?([\d.,]+)\s*(ml|l)(?![a-z])(\s*[×x]\s*(\d+))?/i))) {
    const isL = /^l$/i.test(m[2]);
    out.push({ key: 'net_volume_ml', value: r2(toNum(m[1]) * (isL ? 1000 : 1) * (m[4] ? Number(m[4]) : 1)), unit: 'ml', note: isL ? 'Converted L → ml' : m[4] ? 'Summed "x count" notation' : '', ev: m[0].trim() });
  }
  if ((m = s.match(new RegExp(`(\\d+)\\s*(${COUNT_WORDS})\\b`, 'i'))) || (m = s.match(/(?:pack|set|box) of (\d+)/i))) {
    const w = (m[2] || 'pcs').toLowerCase();
    out.push({ key: 'pack_count', value: Number(m[1]), unit: 'pcs', note: /^(pcs|pieces|piece|pc|count|ct)$/.test(w) ? '' : `Counted "${w}" as pack count`, ev: m[0] });
  }
  if ((m = s.match(/diameter\s*([\d.]+)\s*(?:cm)?\s*[×x]\s*depth\s*([\d.]+)\s*cm/i)) || (m = s.match(/([\d.]+)\s*cm\s*(?:in\s*)?diameter\s*[×x]\s*([\d.]+)\s*cm\s*deep/i))) {
    out.push({ key: 'diameter_cm', value: Number(m[1]), unit: 'cm', note: '', ev: m[0] });
    out.push({ key: 'height_cm', value: Number(m[2]), unit: 'cm', note: 'Recorded depth as height', ev: m[0] });
  } else if ((m = s.match(/(?:width|W)\s*([\d.]+)\s*(?:cm)?\s*[×x]\s*(?:depth|D)\s*([\d.]+)\s*(?:cm)?\s*[×x]\s*(?:height|H)\s*([\d.]+)\s*cm/i))) {
    [['width_cm', m[1]], ['depth_cm', m[2]], ['height_cm', m[3]]].forEach(([key, n]) => out.push({ key, value: Number(n), unit: 'cm', note: 'Split W x D x H notation', ev: m[0] }));
  } else if ((m = s.match(/([\d.]+)\s*[×x]\s*([\d.]+)\s*cm/i))) {
    out.push({ key: 'width_cm', value: Number(m[1]), unit: 'cm', note: 'Interpreted "A x B" as width x depth', ev: m[0] });
    out.push({ key: 'depth_cm', value: Number(m[2]), unit: 'cm', note: 'Interpreted "A x B" as width x depth', ev: m[0] });
  } else if (group === 'kitchen' && (m = s.match(/(?:diameter\s*)?([\d.]+)\s*cm/i))) {
    out.push({ key: 'diameter_cm', value: Number(m[1]), unit: 'cm', note: /diameter/i.test(m[0]) ? '' : 'Interpreted single dimension as diameter', ev: m[0] });
  }
  // evidence must stay a verbatim slice of the original (pre-NFKC) text
  out.forEach((o) => { if (!raw.includes(o.ev)) o.ev = raw.slice(0, 40); });
  return out;
}
function normStorage(v) {
  const s = nfkc(v);
  if (/frozen|freez|-1[58]\s*(?:℃|°C)/i.test(s)) return 'Frozen'; // NFKC turns ℃ into °C
  if (/chill|refrigerat|10\s*(?:℃|°C)\s*or below/i.test(s)) return 'Chilled';
  if (/ambient|room temperature|direct sunlight|shelf[- ]stable/i.test(s)) return 'Ambient';
  return null;
}
function normAllergens(v) {
  const s = String(v || '').trim();
  if (/^(none|no allergens|n\/a|not applicable)$/i.test(s)) return 'none';
  const items = s.toLowerCase().split(/\s*(?:[,;/]|\band\b)\s*/).map((x) => x.replace(/\(.*?\)/g, '').replace(/^contains\s+/, '').trim()).filter(Boolean).map((x) => ALLERGEN_CANON[x] || x);
  const uniq = [...new Set(items)];
  const rank = (a) => { const i = ALLERGENS_28.indexOf(a); return i < 0 ? 99 : i; };
  return uniq.sort((a, b) => rank(a) - rank(b)).join(', ');
}
function toDays(v) {
  const m = nfkc(v).match(/(\d+)\s*(days?|months?|years?)/i);
  if (!m) return null;
  const n = Number(m[1]);
  return /^day/i.test(m[2]) ? n : /^year/i.test(m[2]) ? n * 365 : n * 30;
}
// "Net weight: 450 g / Origin: Japan / Storage: Frozen / Allergens: shrimp, wheat, milk" → [{label, value, seg}]
function descPairs(desc) {
  return String(desc || '').split(/\n|\s+\/\s+|\.\s+(?=[A-Z])/).map((seg) => seg.trim()).filter(Boolean).map((seg) => {
    const m = seg.match(/^([^:]{1,28}):\s*(.+)$/);
    return m ? { label: m[1].trim(), value: m[2].trim().replace(/\.$/, ''), seg } : null;
  }).filter(Boolean);
}
// verbatim snippet (≤40 chars, single line) of `text` around a regex match
function snippetAt(text, m) {
  const ls = text.lastIndexOf('\n', m.index) + 1;
  let le = text.indexOf('\n', m.index); if (le < 0) le = text.length;
  const st = Math.max(ls, m.index - 8);
  return text.slice(st, Math.min(le, st + Math.max(m[0].length + 16, 24), st + 40)).trim();
}
// Regex-based mock of Stage 1 (structured attributes → description "Label: value" → free text).
// Returns fact objects; `rawOnly` limits to structured catalog attributes (used for fill_before).
function mockExtract(p, rawOnly = false) {
  const group = p.group;
  const targets = new Set(mockTargets(group).map((t) => t.key));
  const raw = p.raw_attributes || {};
  const facts = []; const seen = new Set();
  const add = (f) => { if (!targets.has(f.key) || seen.has(f.key)) return; seen.add(f.key); facts.push({ unit: ATTR_UNIT[f.key] || '', note: '', ...f }); };
  const STD_NOTE = 'Standardized to Chilled / Frozen / Ambient';
  // 1) structured catalog attributes (evidence = the "key: value" line of the source text)
  Object.entries(raw).forEach(([k, v0]) => {
    const v = String(v0 == null ? '' : v0).trim(); if (!v) return;
    const base = { raw: v, ev: `${k}: ${v}`, field: 'attributes', sc: /approx|about/i.test(v) ? 0.92 : 1.0, em: 1 };
    if (k === 'size' || k === 'capacity') parseSize(v, group).forEach((o) => add({ ...base, key: o.key, value: o.value, unit: o.unit, note: o.note }));
    else if (k === 'origin') add({ ...base, key: 'origin', value: v });
    else if (k === 'storage_type') { const st = normStorage(v); add({ ...base, key: 'storage_method', value: st, note: st ? STD_NOTE : 'Could not determine storage type', fail: !st }); }
    else if (k === 'material' || k === 'color') add({ ...base, key: k, value: v });
    else if (k === 'dishes') add({ ...base, key: 'dishes', value: v.split(/\s*[/,;]\s*/).filter(Boolean).join(', '), note: 'Unified list format' });
    else if (k === 'season_months') add({ ...base, key: 'season', value: v });
  });
  if (!rawOnly) {
    // 2) description "Label: value" statements
    descPairs(p.description).forEach(({ label, value, seg }) => {
      const base = { raw: value, ev: seg.slice(0, 40), field: 'description', sc: 0.97, em: 1 };
      if (/^(net weight|net contents?|contents?|size|weight|capacity|volume|quantity|pack size|count)$/i.test(label)) parseSize(value, group).forEach((o) => add({ ...base, key: o.key, value: o.value, unit: o.unit, note: o.note }));
      else if (/origin|made in|country/i.test(label)) add({ ...base, key: 'origin', value });
      else if (/storage|store/i.test(label)) { const st = normStorage(value); add({ ...base, key: 'storage_method', value: st, note: st ? STD_NOTE : 'Could not determine storage type', fail: !st }); }
      else if (/best before|use by|shelf life|expir/i.test(label)) { const d = toDays(value); add({ ...base, key: 'best_before_days', value: d, note: d ? 'Converted to days' : 'Could not interpret the period', fail: d == null }); }
      else if (/^(material|materials)/i.test(label)) add({ ...base, key: 'material', value });
      else if (/^(colou?r)$/i.test(label)) add({ ...base, key: 'color', value });
      else if (/season/i.test(label)) add({ ...base, key: 'season', value });
      else if (/^(flavou?r|taste|spic)/i.test(label)) add({ ...base, key: 'flavor', value });
      else if (/scent|fragrance/i.test(label)) add({ ...base, key: 'scent', value });
    });
    // 3) free text (title + description)
    const text = `${p.title || ''}\n${p.description || ''}`;
    const tl = String(p.title || '').length;
    parseSize(p.title, group).forEach((o) => add({ key: o.key, raw: o.ev, value: o.value, unit: o.unit, note: o.note, ev: o.ev, field: 'title', sc: 0.95, em: 1 }));
    parseSize(p.description, group).forEach((o) => add({ key: o.key, raw: o.ev, value: o.value, unit: o.unit, note: o.note, ev: o.ev, field: 'description', sc: 0.9, em: 1 }));
    const rx = (key, re, fn, sc = 0.92) => {
      if (seen.has(key) || !targets.has(key)) return;
      const m = text.match(re); if (!m) return;
      const r = fn ? fn(m) : { value: m[0] };
      if (!r || r.value === undefined) return;
      add({ key, raw: m[0], value: r.value, note: r.note || '', ev: snippetAt(text, m), field: m.index < tl ? 'title' : 'description', sc, em: 1 });
    };
    rx('origin', /\b(?:Made in [A-Z][a-z]+|(?:Hokkaido|Aomori|Iwate|Miyagi|Ibaraki|Chiba|Shizuoka|Nagano|Niigata|Kagoshima|Miyazaki|Kumamoto|Fukuoka|Kochi|Ehime|Wakayama|Yamagata)(?:, Japan)?|Domestic \(Japan\)|Product of [A-Z][a-z]+)\b/);
    rx('storage_method', /keep refrigerated|keep frozen|frozen food|-18\s*°C or below|store at room temperature|\(frozen\)|chilled/i, (m) => ({ value: normStorage(m[0]), note: STD_NOTE }));
    rx('servings', /(?:serves\s*(\d+))|(?:(\d+)\s*servings?)/i, (m) => ({ value: Number(m[1] || m[2]) }));
    rx('cook_time_min', /(?:microwave|\d{3}\s*W|heat|grill|boil|simmer|bake|fry)[^\n]{0,40}?(?:approx\.?\s*|about\s*)?(\d+)\s*min(?:utes?)?(?:\s*(\d+)\s*s(?:ec(?:onds?)?)?)?/i,
      (m) => ({ value: r2(Number(m[1]) + (m[2] ? Number(m[2]) / 60 : 0)), note: m[2] ? 'Converted min + sec → min' : '' }));
    if (!seen.has('cooking_method') && targets.has('cooking_method')) {
      const METHODS = [['microwave', 'Microwave'], ['frying pan', 'Frying pan'], ['boil-in-bag', 'Boil-in-bag'], ['toaster', 'Toaster'], ['oven', 'Oven'], ['pot', 'Pot']];
      const hits = METHODS.filter(([w]) => new RegExp(`\\b${w}\\b`, 'i').test(String(p.description || '')));
      if (hits.length) rx('cooking_method', new RegExp(`\\b${hits[0][0]}\\b`, 'i'), () => ({ value: hits.map((h) => h[1]).join(', '), note: 'Classified into standard cooking methods' }), 0.95);
    }
    rx('flavor', /\b(?:mild|medium[- ]hot|medium|hot|extra hot|salted|lightly salted|soy sauce|miso|butter)\b(?= flavou?r| taste|\b)/i);
    rx('season', /(?:in season|peak season)[:\s]*((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*[-–to ]+\s*(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*)/i, (m) => ({ value: m[1] }));
    rx('dishwasher_safe', /not dishwasher[- ]safe|do not (?:use|put) in (?:the )?dishwasher|dishwasher:\s*not/i, () => ({ value: false }));
    rx('dishwasher_safe', /dishwasher[- ]safe|dishwasher compatible/i, () => ({ value: true }));
    rx('ih_compatible', /gas only|not induction|not (?:IH|induction)[- ]compatible/i, () => ({ value: false }));
    rx('ih_compatible', /induction[^.\n]{0,12}(?:compatible|ready|ok)|IH[^.\n]{0,12}compatible/i, () => ({ value: true }));
    rx('microwave_safe', /not microwave[- ]safe|do not microwave/i, () => ({ value: false }));
    rx('microwave_safe', /microwave[- ]safe/i, () => ({ value: true }));
    rx('target_pet', /dogs? (?:and|&) cats?|for dogs?|dog food|puppy|for cats?|cat food|kitten|small animals?/i, (m) => ({ value: /dog.*cat/i.test(m[0]) ? 'Dog & Cat' : /dog|puppy/i.test(m[0]) ? 'Dog' : /cat|kitten/i.test(m[0]) ? 'Cat' : 'Small animal' }));
    rx('target_age', /\d+\+?\s*years?(?: and (?:up|older))?|adult (?:dogs?|cats?)|puppy|kitten|senior (?:dogs?|cats?)|all life stages/i);
    rx('scent', /unscented|fragrance[- ]free|[a-z]+ scent(?:ed)?/i);
    rx('skin_type', /sensitive skin|dry skin|oily skin|combination skin|all skin types|damaged hair/i);
    rx('color', /\b(?:matte white|navy|gray|grey|brown|white|black|beige|pink|red|blue|green|silver|ivory)\b/i, null, 0.9);
    rx('material', /aluminum alloy|stainless steel|aluminum|copper|cast iron|polypropylene|silicone|heat[- ]resistant glass|wood(?:en)?|cotton|polyester/i, null, 0.88);
  }
  // Allergens: auto-approve only with an explicit "Allergens:" statement (mirrors app/rules.py food-safety rule)
  if (targets.has('allergens')) {
    const m = rawOnly ? null : String(p.description || '').match(/(?:allergens?|contains|allergy information)[^:\n]{0,12}:\s*([^/\n]+?)(?:\.\s|\s\/|$|\n)/i);
    const titleCheck = (val) => {
      const items = val.split(', ');
      const extra = Object.entries(ALLERGEN_TITLE_HINT).filter(([a, re]) => re.test(p.title || '') && !items.includes(a)).map(([a]) => a);
      return extra.length ? `The product name mentions "${extra.join(', ')}" but the allergen statement does not list it (needs review)` : null;
    };
    if (m) {
      const val = normAllergens(m[1]);
      facts.push({ key: 'allergens', raw: m[1].trim(), value: val, unit: '', note: 'Unified to the standard 28-allergen names', ev: m[0].trim().slice(0, 40), field: 'description', sc: 0.97, em: 1, safety: val === 'none' ? null : titleCheck(val) });
    } else if (String(raw.allergens || '').trim()) {
      const v = String(raw.allergens).trim(); const val = normAllergens(v);
      facts.push({ key: 'allergens', raw: v, value: val, unit: '', note: 'Unified to the standard 28-allergen names', ev: `allergens: ${v}`, field: 'attributes', sc: 1.0, em: 1,
        safety: val === 'none' ? '"none" appears only in the structured attributes. Check the package label' : 'The source has no "Allergens:" statement; it appears only in the structured attributes. Check the package label' });
    } else if (FOOD_G.includes(group)) facts.push({ key: 'allergens', placeholder: true });
  }
  return facts;
}
function mockFillBefore(p) {
  const t = mockTargets(p.group);
  const have = new Set(mockExtract(p, true).filter((f) => !f.placeholder && !f.fail && f.value != null).map((f) => f.key));
  return +(t.filter((x) => have.has(x.key)).length / Math.max(t.length, 1)).toFixed(3);
}
function csvToProducts(text) {
  const rows = parseCsvRows(text);
  if (!rows.length) throw new ApiError('The CSV data is empty', 400);
  const headers = rows[0].map((h) => String(h).trim());
  const map = headers.map((h) => {
    const n = h.toLowerCase().replace(/[\s\-]+/g, '_');
    const hit = Object.entries(HEADER_ALIASES).find(([, al]) => al.includes(n) || al.includes(h));
    return hit ? hit[0] : 'raw:' + h;
  });
  const items = rows.slice(1).map((r, idx) => {
    const p = { sku: '', title: '', brand: '', category: '', description: '', price: null, product_url: '', image_url: '', group: '', raw_attributes: {} };
    r.forEach((v, i) => {
      v = String(v || '').trim(); if (!v || !headers[i]) return;
      const t = map[i];
      if (t === 'price') p.price = Number(v.replace(/[^\d.]/g, '')) || null;
      else if (t.startsWith('raw:')) p.raw_attributes[t.slice(4)] = v;
      else p[t] = v;
    });
    p.sku = p.sku || `CSV-${String(idx + 1).padStart(4, '0')}`;
    if (!GROUP_LABELS[p.group]) p.group = detectGroup(`${p.category} ${p.title}`);
    if (!p.category) p.category = GROUP_LABELS[p.group];
    p.group_label = GROUP_LABELS[p.group];
    p.fill_before = mockFillBefore(p);
    p.raw_attr_count = Object.keys(p.raw_attributes).length;
    return p;
  });
  return { items, count: items.length, columns: headers };
}
function mockFactRec(p, f, jobId) {
  const now = new Date().toISOString();
  if (f.placeholder) {
    // == app/pipeline.py missing_allergen_record()
    return { sku: p.sku, kind: 'attribute', attr_key: 'allergens', label: ATTR_LABEL.allergens, raw_value: '', value: null, unit: '', norm_note: 'Not stated in the source (never guessed)',
      evidence: '', source_field: '', self_conf: 0.0, evidence_match: 0.0, token_conf: null, score: 0.0, gate_status: 'needs_review', gate: 'gate1',
      reason: 'Food-safety rule: the source has no allergen statement. Do not fill by guessing; check the package label', job_id: jobId, model: MOCK_MODEL, created_at: now };
  }
  const score = +(0.6 * f.em + 0.4 * f.sc).toFixed(3);
  const failed = f.fail || f.value == null;
  let st = 'needs_review', gate = 'gate2', reason;
  if (failed) { gate = 'gate1'; reason = `Unit normalization failed: ${f.note}`; }
  else if (f.safety) { gate = 'gate1'; reason = `Food-safety rule: ${f.safety}`; }
  else if (f.em > 0 && score >= 0.9) { st = 'auto_approved'; reason = `Composite score ${score.toFixed(2)} ≥ 0.90`; }
  else reason = `Composite score ${score.toFixed(2)} < 0.90 (low confidence)`;
  return { sku: p.sku, kind: 'attribute', attr_key: f.key, label: ATTR_LABEL[f.key] || f.key, raw_value: f.raw, value: failed ? null : f.value, unit: f.unit || '', norm_note: f.note || '',
    evidence: f.ev, source_field: f.field, self_conf: f.sc, evidence_match: f.em, token_conf: null, score, gate_status: st, gate, reason, job_id: jobId, model: MOCK_MODEL, created_at: now };
}
function mockTagRec(p, tag, ev, field, jobId) {
  const sc = 0.95, score = +(0.6 + 0.4 * sc).toFixed(3);
  return { sku: p.sku, kind: 'tag', attr_key: 'tag:' + tag, label: tag, raw_value: '', value: tag, unit: '', norm_note: '', evidence: ev, source_field: field, self_conf: sc,
    evidence_match: 1.0, token_conf: null, score, gate_status: 'auto_approved', gate: 'gate2', reason: `Composite score ${score.toFixed(2)} ≥ 0.90`, job_id: jobId, model: MOCK_MODEL, created_at: new Date().toISOString() };
}
const MOCK_COLORS = /\s*\b(matte white|navy|gray|grey|brown|white|black|beige|pink|red|blue|green|silver|ivory)\b\s*/gi;
// 'Cymbal Shrimp Pilaf 450 g' → 'Cymbal Shrimp Pilaf' (drop amount / count / size / color)
function mockItemGroupTitle(t) {
  const s = String(t || '').replace(/\([^)]*\)/g, ' ')
    .replace(/(?:approx\.?\s*)?[\d.,]+\s*(?:kg|g|ml|mL|L|cm)\b(?:\s*[×x]\s*\d+\s*\S*)?/g, ' ')
    .replace(new RegExp(`\\b\\d+\\s*(?:${COUNT_WORDS})\\b`, 'gi'), ' ')
    .replace(MOCK_COLORS, ' ').replace(/[,\s]+$/, '').replace(/\s+/g, ' ').trim();
  return s || String(t || '');
}
function mockSourceText(p) {
  return `[Title] ${p.title}\n[Brand] ${p.brand || ''}\n[Category] ${p.category || ''}\n[Description] ${p.description || ''}\n[Attributes]\n`
    + Object.entries(p.raw_attributes || {}).map(([k, v]) => `${k}: ${v}`).join('\n');
}
const GMC_SECTION = {
  net_weight_g: 'Size & contents', net_volume_ml: 'Size & contents', pack_count: 'Size & contents', servings: 'Size & contents', width_cm: 'Size & contents', depth_cm: 'Size & contents',
  height_cm: 'Size & contents', diameter_cm: 'Size & contents', origin: 'Food labeling & storage', storage_method: 'Food labeling & storage', allergens: 'Food labeling & storage', best_before_days: 'Food labeling & storage',
  cook_time_min: 'Cooking & serving ideas', cooking_method: 'Cooking & serving ideas', dishes: 'Cooking & serving ideas', flavor: 'Cooking & serving ideas', season: 'Cooking & serving ideas',
};
async function mockEnrichProduct(p, jobId) {
  const cfg = await fixture('config.json');
  await syncGroupLabels();
  const targets = mockTargets(p.group);
  const recs = mockExtract(p).map((f) => mockFactRec(p, f, jobId));
  // Stage 3: standard feature tags (only when the text carries evidence)
  const text = `${p.title || ''}\n${p.description || ''}`;
  const tl = String(p.title || '').length;
  ((cfg.feature_tags && cfg.feature_tags[p.group]) || []).forEach((t) => {
    if (recs.filter((r) => r.kind === 'tag').length >= 5) return;
    const re = TAG_HINTS[t] || new RegExp(t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\s*\/\s*/g, '|'), 'i');
    const m = text.match(re);
    if (m) recs.push(mockTagRec(p, t, m[0], m.index < tl ? 'title' : 'description', jobId));
  });
  const src = mockSourceText(p);
  const attrs = recs.filter((r) => r.kind === 'attribute');
  const fill_after = +(new Set(attrs.filter((r) => r.value !== null && r.gate_status !== 'rejected').map((r) => r.attr_key)).size / Math.max(targets.length, 1)).toFixed(3);
  const status = recs.some((r) => r.gate_status === 'needs_review') ? 'needs_review' : 'completed';
  const sentences = String(p.description || '').split('\n')[0].split(/(?<=\.)\s+/).map((x) => x.trim().replace(/\.$/, '')).filter((x) => x && !/:/.test(x));
  const core = mockItemGroupTitle(p.title);
  const tagNames = recs.filter((r) => r.kind === 'tag').map((r) => r.label);
  const groupLabel = GROUP_LABELS[p.group];
  const detail = {
    product: { sku: p.sku, title: p.title, brand: p.brand, category: p.category, description: p.description, product_url: p.product_url,
      image_url: p.image_url, price: p.price, group: p.group, group_label: groupLabel, raw_attributes: p.raw_attributes, doc_sources: [], source: 'csv', images: [] },
    source_text: src, targets, records: recs,
    extras: { search_keywords: [core, ...tagNames.slice(0, 3).map((t) => `${t} ${(GROUP_SHORT[p.group] || groupLabel).toLowerCase()}`)], ai_summary: sentences[0] ? sentences[0] + '.' : '' },
    status, fill_before: p.fill_before ?? mockFillBefore(p), fill_after,
  };
  // GMC conversational attributes (facts from auto-approved records only)
  const ok = Object.fromEntries(attrs.filter((r) => r.gate_status === 'auto_approved' && r.value !== null).map((r) => [r.attr_key, r]));
  const qas = [];
  const amt = ok.net_weight_g || ok.net_volume_ml;
  const isGM = GM_G.includes(p.group);
  if (amt) qas.push({ question: 'How much does it contain?', answer: `It contains ${amt.value} ${amt.unit}${ok.pack_count ? ` (${ok.pack_count.value} pcs)` : ''}.`, evidence: amt.evidence, confidence: 0.97 });
  else if (ok.pack_count) qas.push({ question: 'How many pieces are in a pack?', answer: `Each pack contains ${ok.pack_count.value} pieces.`, evidence: ok.pack_count.evidence, confidence: 0.95 });
  if (ok.storage_method) qas.push({ question: 'How should I store it?', answer: `Store it ${String(ok.storage_method.value).toLowerCase() === 'ambient' ? 'at room temperature' : String(ok.storage_method.value).toLowerCase() === 'frozen' ? 'frozen' : 'chilled (refrigerated)'}.`, evidence: ok.storage_method.evidence, confidence: 0.97 });
  if (ok.allergens && ok.allergens.value !== 'none') qas.push({ question: 'Which allergens does it contain?', answer: `It contains ${ok.allergens.value}.`, evidence: ok.allergens.evidence, confidence: 0.97 });
  if (ok.cooking_method) qas.push({ question: 'How do I cook it?', answer: `Cooking method: ${ok.cooking_method.value}.${ok.cook_time_min ? ` It takes about ${ok.cook_time_min.value} minutes.` : ''}`, evidence: ok.cooking_method.evidence, confidence: 0.95 });
  if (ok.ih_compatible || ok.dishwasher_safe) {
    const parts = [ok.ih_compatible && (ok.ih_compatible.value ? 'it works on induction (IH) cooktops' : 'it is not induction (IH) compatible'), ok.dishwasher_safe && (ok.dishwasher_safe.value ? 'it is dishwasher safe' : 'it is not dishwasher safe')].filter(Boolean);
    qas.push({ question: 'Is it induction and dishwasher compatible?', answer: `${parts.join(', and ').replace(/^./, (c) => c.toUpperCase())}.`, evidence: (ok.ih_compatible || ok.dishwasher_safe).evidence, confidence: 0.96 });
  }
  if (ok.material) qas.push({ question: 'What is it made of?', answer: `It is made of ${ok.material.value}.`, evidence: ok.material.evidence, confidence: 0.95 });
  if (ok.origin) qas.push({ question: isGM ? 'Where is it made?' : 'Where does it come from?', answer: `${ok.origin.value}.`, evidence: ok.origin.evidence, confidence: 0.96 });
  if (ok.dishes) qas.push({ question: 'What dishes is it good for?', answer: `It is great for ${ok.dishes.value}.`, evidence: ok.dishes.evidence, confidence: 0.92 });
  const gmcQas = qas.slice(0, 5).map((q) => { const em = src.includes(q.evidence) ? 1 : 0; const sc = +(0.6 * em + 0.4 * q.confidence).toFixed(3); return { ...q, evidence_match: em, score: sc, status: em && sc >= 0.9 ? 'auto_approved' : 'needs_review' }; });
  const vo = (name, rec, value) => ({ name, value, evidence: rec.evidence, evidence_match: src.includes(rec.evidence) ? 1 : 0 });
  const vos = [];
  if (amt) vos.push(vo('Size', amt, `${amt.value} ${amt.unit}`));
  if (ok.pack_count) vos.push(vo('Pack count', ok.pack_count, `${ok.pack_count.value} pcs`));
  if (ok.diameter_cm) vos.push(vo('Diameter', ok.diameter_cm, `${ok.diameter_cm.value} cm`));
  if (ok.color) vos.push(vo('Color', ok.color, String(ok.color.value)));
  const fmtVal = (r) => (typeof r.value === 'boolean' ? (r.value ? 'Yes' : 'No') : `${r.value} ${r.unit || ''}`.trim());
  const gmc = mockGmcStrings({
    sku: p.sku, item_group_id: String((p.raw_attributes || {}).base_id || p.sku), item_group_title: core,
    variant_options: vos, questions_and_answers: gmcQas,
    product_highlights: sentences.slice(0, 3).map((x) => (x.length > 80 ? x.slice(0, 80) + '…' : x)),
    product_details: Object.values(ok).map((r) => ({ section: GMC_SECTION[r.attr_key] || 'General specs', attribute: r.label, value: fmtVal(r) })),
    related_products: [], popularity_rank: 50 + (p.sku.length * 7) % 40, document_link: '', // never invent document URLs
    model: MOCK_MODEL, job_id: jobId, updated_at: new Date().toISOString(),
  });
  return { detail, gmc, src };
}
function mockGmcStrings(g) {
  g.variant_option = (g.variant_options || []).filter((v) => v.name && v.value).map((v) => `${v.name}:${v.value}`).join(',');
  g.question_and_answer = (g.questions_and_answers || []).filter((q) => q.question && q.answer).map((q) => `"${q.question}":"${q.answer}"`).join(', ');
  g.product_highlight = (g.product_highlights || []).filter(Boolean).map((h) => `"${h}"`).join(', ');
  g.product_detail = (g.product_details || []).map((d) => `${d.section}:${d.attribute}:"${d.value}"`).join(', ');
  g.related_product = (g.related_products || []).filter((r) => r.identifier).map((r) => `${r.relationship_type}:${r.identifier_type}:${r.identifier}`).join(',');
  return g;
}
function mockFeedRow(p, g) {
  return { id: p.sku, item_group_id: g.item_group_id || '', title: p.title, item_group_title: g.item_group_title || '', variant_option: g.variant_option || '',
    question_and_answer: g.question_and_answer || '', product_highlight: g.product_highlight || '', product_detail: g.product_detail || '',
    related_product: g.related_product || '', popularity_rank: g.popularity_rank ?? '', document_link: g.document_link || '', brand: p.brand || '',
    price: p.price ? `${Math.round(p.price)} JPY` : '', link: p.product_url || '', image_link: p.image_url || '' };
}
// SKUs that have dedicated fixture files (avoids 404 noise for the rest)
const FIXTURE_SKUS = new Set(['4913067859176', '4946311911337']);
async function mockGmcGet(sku) {
  if (mockState.gmc[sku]) return mockState.gmc[sku];
  if (FIXTURE_SKUS.has(sku)) { try { mockState.gmc[sku] = clone(await fixture(`gmc_${sku}.json`)); return mockState.gmc[sku]; } catch (e) { /* fallthrough */ } }
  const d = await mock.product(sku);
  const g = mockGmcStrings({ sku, item_group_id: '', item_group_title: '', variant_options: [], questions_and_answers: [], product_highlights: [], product_details: [], related_products: [], popularity_rank: null, document_link: '', model: MOCK_MODEL, job_id: '', updated_at: '' });
  mockState.gmc[sku] = { product: d.product, source_text: d.source_text, gmc: g, feed_row: mockFeedRow(d.product, g) };
  return mockState.gmc[sku];
}
function mockCsvGmc() {
  return {
    async csvSample() {
      await syncGroupLabels();
      const s = await fixture('csv_sample.json');
      return { ...csvToProducts(s.csv_text), csv_text: s.csv_text };
    },
    async csvParse(body) { await sleep(150); await syncGroupLabels(); return csvToProducts(body.csv_text || ''); },
    async csvEnrich(body, onEvent, signal) {
      await syncGroupLabels();
      const prods = body.products && body.products.length ? body.products : csvToProducts(body.csv_text).items;
      const jobId = `csv-live-${new Date().toISOString().replace(/\D/g, '').slice(0, 14)}`;
      const t0 = Date.now();
      const chk = () => { if (signal && signal.aborted) throw Object.assign(new Error('aborted'), { name: 'AbortError' }); };
      onEvent({ event: 'start', job_id: jobId, model: MOCK_MODEL, total: prods.length, save_to_bq: !!body.save_to_bq, dataset: 'demo-project.product_enrichment' });
      let tin = 0, tout = 0; const done = [];
      for (let i = 0; i < prods.length; i++) {
        const p = prods[i];
        chk();
        onEvent({ event: 'sku_start', index: i, total: prods.length, sku: p.sku, title: p.title, group: p.group, group_label: GROUP_LABELS[p.group] });
        await sleep(1400 + Math.random() * 600); chk();
        const { detail, gmc, src } = await mockEnrichProduct(p, jobId);
        const it = { sku: p.sku, title: p.title, brand: p.brand, group: p.group, group_label: GROUP_LABELS[p.group], image_url: p.image_url, price: p.price,
          status: detail.status, fill_before: detail.fill_before, fill_after: detail.fill_after, attr_count: detail.records.filter((r) => r.kind === 'attribute').length,
          auto_approved: detail.records.filter((r) => r.gate_status === 'auto_approved' && r.kind === 'attribute').length,
          needs_review: detail.records.filter((r) => r.gate_status === 'needs_review').length, rejected: 0, tags: detail.records.filter((r) => r.kind === 'tag').map((r) => r.label) };
        mockState.csvItems[p.sku] = it; mockState.csvDetails[p.sku] = detail;
        mockState.gmc[p.sku] = { product: detail.product, source_text: src, gmc, feed_row: mockFeedRow(detail.product, gmc) };
        const ti = 1500 + Math.round(Math.random() * 300), to = 900 + Math.round(Math.random() * 300);
        tin += ti; tout += to; done.push(p.sku);
        onEvent({ event: 'sku_done', index: i, total: prods.length, sku: p.sku, latency_ms: 1400 + Math.round(Math.random() * 900), input_tokens: ti, output_tokens: to,
          attr_count: it.attr_count, tag_count: it.tags.length, qa_count: gmc.questions_and_answers.length, fill_before: detail.fill_before, fill_after: detail.fill_after, status: detail.status, detail });
      }
      let bq = null;
      if (body.save_to_bq && done.length) {
        onEvent({ event: 'bq_saving', skus: done.length, dataset: 'demo-project.product_enrichment' });
        await sleep(900);
        bq = await mock.csvSaveBq(done, true);
        delete bq.ok;
      }
      const costUsd = +((tin * 0.3 + tout * 2.5) / 1e6).toFixed(6);
      onEvent({ event: 'complete', job_id: jobId, model: MOCK_MODEL, processed: done.length, total: prods.length, save_to_bq: !!body.save_to_bq, bq_result: bq,
        input_tokens: tin, output_tokens: tout, cost_usd: costUsd, cost_jpy: +(costUsd * 150).toFixed(3), elapsed_ms: Date.now() - t0 });
    },
    async csvSaveBq(skus, quiet) {
      if (!quiet) await sleep(700);
      const recs = skus.reduce((a, s) => a + ((mockState.csvDetails[s] || {}).records || []).length, 0);
      const DS = 'demo-project.product_enrichment'; // backend returns dataset-qualified table keys
      return { ok: true, skus: skus.length, tables: { [`${DS}.source_products`]: skus.length, [`${DS}.enriched_attributes`]: recs, [`${DS}.sku_extras`]: skus.length, [`${DS}.golden_records`]: skus.length, [`${DS}.gmc_conversational`]: skus.length } };
    },
    async gmcList(p = {}) {
      const prods = (await mock.products({})).items;
      const all = [];
      for (const it of prods) {
        const d = await mockGmcGet(it.sku); const g = d.gmc;
        const qas = g.questions_and_answers || [];
        all.push({ sku: it.sku, title: it.title, brand: it.brand, group: it.group, group_label: it.group_label, image_url: it.image_url, price: it.price,
          item_group_id: g.item_group_id, item_group_title: g.item_group_title, variant_option: g.variant_option, variant_options: g.variant_options || [],
          qa_count: qas.length, qa_auto_approved: qas.filter((q) => q.status === 'auto_approved').length, qa_needs_review: qas.filter((q) => q.status === 'needs_review').length,
          highlight_count: (g.product_highlights || []).length, detail_count: (g.product_details || []).length, related_count: (g.related_products || []).length,
          popularity_rank: g.popularity_rank, document_link: g.document_link, model: MOCK_MODEL, job_id: g.job_id, updated_at: g.updated_at });
      }
      const tq = all.reduce((a, i) => a + i.qa_count, 0);
      const stats = { total_skus: all.length, total_qas: tq, avg_qas_per_sku: +(tq / Math.max(all.length, 1)).toFixed(1),
        variant_option_skus: all.filter((i) => i.variant_options.length).length, related_product_skus: all.filter((i) => i.related_count).length,
        highlight_skus: all.filter((i) => i.highlight_count).length, auto_approved_qas: all.reduce((a, i) => a + i.qa_auto_approved, 0), needs_review_qas: all.reduce((a, i) => a + i.qa_needs_review, 0) };
      let items = all;
      if (p.group) items = items.filter((i) => i.group === p.group);
      if (p.q) { const q = p.q.toLowerCase(); items = items.filter((i) => (i.title + i.brand + i.sku + i.item_group_title).toLowerCase().includes(q)); }
      return { spec_url: 'https://support.google.com/merchants/answer/17085370?hl=en', spec_url_ja: 'https://support.google.com/merchants/answer/17085370?hl=ja', model: MOCK_MODEL, stats, items, total: items.length };
    },
    async gmcDetail(sku) { return clone(await mockGmcGet(sku)); },
    async gmcGenerate(sku) {
      await sleep(1800);
      const d = await mockGmcGet(sku);
      d.gmc.updated_at = new Date().toISOString(); d.gmc.job_id = `gmc-live-${Date.now()}`;
      d.gmc.questions_and_answers.forEach((q) => { q.confidence = Math.min(0.99, +(q.confidence + 0.01).toFixed(2)); });
      return { ...clone(d), latency_ms: 1780, input_tokens: 1320, output_tokens: 640 };
    },
    async gmcUpdate(sku, body) {
      await sleep(400);
      const d = await mockGmcGet(sku);
      const g = d.gmc;
      ['item_group_id', 'item_group_title', 'document_link'].forEach((k) => { if (body[k] != null) g[k] = String(body[k]).trim(); });
      if (body.popularity_rank != null) g.popularity_rank = +Number(body.popularity_rank).toFixed(1);
      if (Array.isArray(body.variant_options)) g.variant_options = body.variant_options.filter((v) => v.name && v.value).map((v) => ({ ...v, evidence: v.evidence || v.value, evidence_match: d.source_text.includes(v.evidence || v.value) ? 1 : 0 }));
      if (Array.isArray(body.questions_and_answers)) g.questions_and_answers = body.questions_and_answers.filter((q) => q.question && q.answer).map((q) => {
        const em = q.evidence ? (d.source_text.includes(q.evidence) ? 1 : 0) : 1; const conf = q.confidence || 0.95; const sc = +(0.6 * em + 0.4 * conf).toFixed(3);
        return { ...q, confidence: conf, evidence_match: em, score: sc, status: q.status || (sc >= 0.9 ? 'auto_approved' : 'needs_review') };
      });
      if (Array.isArray(body.product_highlights)) g.product_highlights = body.product_highlights.map((h) => String(h).trim()).filter(Boolean);
      if (Array.isArray(body.related_products)) g.related_products = body.related_products.filter((r) => r.identifier);
      mockGmcStrings(g);
      d.feed_row = mockFeedRow(d.product, g);
      return clone(d);
    },
  };
}

// ── Mock: (1) Seasonal Image Studio + (2) Extract from Documents ─────────────────────────────
// Everything is evaluated lazily inside the methods (module-level consts below this point are in TDZ at Object.assign time).
function mockImagesDocs() {
  const BUCKET_FALLBACK = 'products_images_demo-project';
  const imgState = {}; // sku -> ImageRow[]
  const SEASON_COL = { spring: ['#fde2ec', '#f7a8c4'], summer: ['#d8f1ff', '#3fa9f5'], autumn: ['#fdebd3', '#e58a3a'], winter: ['#eef4ff', '#9db7e8'],
    yearend: ['#fff1ef', '#d93025'], newlife: ['#eef7e4', '#8bc34a'], all: ['#ffffff', '#cfd6e0'] };
  const SEASON_LABEL = { spring: 'Spring', summer: 'Summer', autumn: 'Autumn', winter: 'Winter', yearend: 'Year-end & New Year', newlife: 'New Life', all: 'All year' };
  const FOOD = ['fresh', 'prepared', 'grocery'];
  const ALLG = [...FOOD, 'kitchen', 'living', 'beauty', 'pet'];
  // Mirrors app/image_presets.py PRESETS (availability decided by rules: approved tags / attrs, raw attrs, season months, keyword evidence)
  const CP = (id, label, season, icon, groups, prompt, o = {}) => ({ id, label, season, icon, groups, prompt, tags: [], months: [], re: null, attrMax: null, rawTrue: [], rawKeys: [], facts: [], need: '', always: false, ...o });
  const PRESETS = [
    CP('spring-sakura', 'Spring: Cherry Blossom', 'spring', '🌸', FOOD, 'A cheerful spring cherry-blossom picnic: pink sakura petals, a picnic blanket and bento boxes under blossoming trees, soft pastel pink and fresh green tones, bright natural daylight.',
      { months: [3, 4], tags: ['Lunchbox-friendly', 'Bento-friendly', 'Good for bento', 'Picnic', 'Entertaining'], re: /cherry blossom|sakura|hanami|picnic|bento|lunch ?box|spring/i, need: 'spring / cherry-blossom picnic use' }),
    CP('newlife', 'Spring: New Life', 'newlife', '🏡', ['kitchen', 'living', 'prepared', 'grocery', 'beauty'], 'A fresh start in spring: a bright, tidy studio apartment or new-home kitchen, light green and white tones, airy and optimistic.',
      { tags: ['Space-saving', 'Stackable', 'Quick prep', 'Time-saving', 'Time-saver', 'Single-serve size', 'Single-use size', 'Lunchbox-friendly', 'Good for bento'], re: /new life|living alone|single[- ]person|one[- ]person|studio|lunch ?box|bento|meal prep|storage|space[- ]saving|compact|stackable/i, need: 'new life / living alone / lunchbox use' }),
    CP('summer-cool', 'Summer: Cool Treats', 'summer', '🧊', [...FOOD, 'kitchen'], 'A cool, refreshing summer table: glassware, ice cubes and water droplets, crisp blue and white tones, bright summer sunlight.',
      { months: [6, 7, 8], tags: ['Cooling', 'Serve chilled', 'Cool'], re: /chilled|serve cold|ice[d ]|iced|cold brew|somen|cold noodle|shaved ice|popsicle|ice cream|barley tea|cooling|summer/i, need: 'serving chilled / summer use' }),
    CP('summer-bbq', 'Summer: BBQ', 'summer', '🍖', [...FOOD, 'kitchen'], 'A lively summer backyard barbecue: a charcoal grill with gentle smoke, picnic table, friends and family outdoors at golden hour, warm and appetizing.',
      { tags: ['BBQ', 'Yakiniku & BBQ', 'Great for BBQ', 'Grilling'], re: /\bbbq\b|barbecue|barbeque|grill|yakiniku|skewer|kebab|steak|sausage|charcoal/i, need: 'barbecue / grilling use' }),
    CP('autumn-taste', 'Autumn Harvest', 'autumn', '🍁', FOOD, 'An autumn harvest table: wooden table with fall leaves, chestnuts and mushrooms as props, warm orange and brown tones. A photo that conveys the flavor of seasonal ingredients.',
      { months: [9, 10, 11], tags: ['In season', 'Seasonal'], re: /autumn|\bfall\b|harvest|new[- ]crop rice|chestnut|saury|sanma|salmon|mushroom|shimeji|maitake|sweet potato|pumpkin|kabocha|persimmon|pear|apple|grape/i, facts: ['season', 'origin'], need: 'autumn seasonality / harvest' }),
    CP('halloween', 'Halloween', 'autumn', '🎃', [...FOOD, 'kitchen', 'living'], "A playful Halloween party table: carved jack-o'-lanterns, candles, orange and purple accents, fun but family-friendly, warm evening light.",
      { tags: ['Party', "Kids' favorite", 'Kid favorite', 'Popular with kids'], re: /halloween|pumpkin|kabocha|jack-o|trick or treat|candy|party/i, need: 'Halloween / party use' }),
    CP('winter-nabe', 'Winter Hot Pot', 'winter', '🍲', [...FOOD, 'kitchen'], 'A cozy winter evening: a family gathered around a steaming clay hot pot (nabe) on the dining table, rising steam, soft warm-white light, comforting mood.',
      { tags: ['Hot pot', 'Great for hot pot', 'Good for hot pot', 'Nabe'], re: /hot ?pot|\bnabe\b|shabu|sukiyaki|oden|mizutaki|chanko|donabe|stew/i, facts: ['net_weight_g', 'origin'], need: 'hot pot cooking' }),
    CP('christmas', 'Christmas', 'winter', '🎄', [...FOOD, 'kitchen', 'living', 'beauty'], 'A festive Christmas table: a decorated tree with twinkling lights in the background, red and green accents with gold ornaments, a warm family holiday celebration.',
      { tags: ['Party', 'Entertaining', 'Gift'], re: /christmas|x-?mas|holiday|festive|roast chicken|turkey|party|gift/i, need: 'Christmas / party / gift use' }),
    CP('yearend', 'New Year', 'yearend', '🎍', [...FOOD, 'beauty', 'living'], 'A celebratory Japanese New Year table: red, white and gold accents, pine and mizuhiki cord decorations, family and relatives gathering on a special day.',
      { months: [12, 1], tags: ['Entertaining', 'Gift', 'Party'], re: /new year|year[- ]end|osechi|ozoni|toshikoshi|year-end gift|oseibo|christmas|holiday|gift/i, need: 'New Year / gift / seasonal events' }),
    CP('quick-dinner', 'Weeknight: Quick Dinner', 'all', '⏱️', FOOD, 'A weekday evening with a quickly prepared, well-presented dinner on the table: a busy two-income family kitchen, bright and clean.',
      { tags: ['Quick prep', 'Time-saving', 'Time-saver', 'Microwavable', 'Microwave', 'Microwave cooking', 'Heat & serve', 'Just heat'], attrMax: ['cook_time_min', 15], re: /quick|time[- ]saving|microwave|just heat|heat (?:and|&) serve|ready in|easy|pre-?cut/i, facts: ['cook_time_min'], need: 'quick cooking (cooking time / microwave)' }),
    CP('ec-white', 'E-commerce: White Background', 'all', '⬜', ALLG, 'Pure white background, product large and centered, soft contact shadow. A clean product photo for an e-commerce product page.', { always: true }),
    CP('lifestyle', 'Lifestyle', 'all', '🏠', ALLG, "The product used naturally in an ordinary Japanese home's dining room, kitchen or living room. Bright and clean photo.", { always: true }),
    CP('sale', 'Sale', 'all', '🏷️', ALLG, 'A bright, energetic supermarket flyer / special-offer corner background (Cymbal blue #1a73e8 with red and yellow accents). Product large in the center. Do not draw any price numbers.',
      { rawTrue: ['on_sale'], rawKeys: ['promo_text'], re: /\bsale\b|special offer|deal|discount|bargain/i, need: 'sale / special offer (on_sale / promo text)' }),
  ];
  const FINAL_OK = ['auto_approved', 'approved', 'edited', 'human_approved'];
  const INTERNAL_RAW = ['base_id', 'top_category', 'price_tax_excluded', 'original_price_tax_excluded', 'discount_rate', 'is_food', 'is_seasonal'];
  const MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'];
  const parseMonths = (s) => {
    s = String(s || '').normalize('NFKC').toLowerCase();
    const MRE = /\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?/g;
    const named = [...s.matchAll(MRE)].map((x) => MONTHS.indexOf(x[1]) + 1);
    const nums = named.length ? named : [...s.matchAll(/\d{1,2}/g)].map((x) => +x[0]).filter((m) => m >= 1 && m <= 12);
    if (nums.length === 2 && /[-–~]|\bto\b|through/.test(s)) { const [a, b] = nums; const out = []; for (let m = a, i = 0; i < 12; i++, m = m % 12 + 1) { out.push(m); if (m === b) break; } return out; }
    return nums;
  };
  const MON_ABBR = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const normTag = (t) => String(t || '').toLowerCase().replace(/[^a-z0-9]/g, '');
  const fmt = (r) => (typeof r.value === 'boolean' ? (r.value ? 'Yes' : 'No') : `${r.value} ${r.unit || ''}`.trim());
  const evaluateOne = (pr, product, recs) => {
    const attrs = {}; const tags = new Set();
    recs.forEach((r) => {
      if (!FINAL_OK.includes(r.gate_status)) return;
      if (r.kind === 'tag') tags.add(normTag(r.value || r.label));
      else if (r.kind === 'attribute' && r.value !== null && r.value !== '' && r.value !== false) attrs[r.attr_key] = r;
    });
    const raw = product.raw_attributes || {};
    let ok = pr.always, evidence = '';
    if (!ok) {
      const hitTag = pr.tags.find((t) => tags.has(normTag(t)));
      const hitTrue = pr.rawTrue.find((k) => String(raw[k] || '').trim().toLowerCase() === 'true');
      const months = pr.months.length ? (attrs.season ? parseMonths(attrs.season.value) : raw.season_months ? parseMonths(raw.season_months) : []) : [];
      const hitMonth = months.filter((m) => pr.months.includes(m));
      const hitMax = pr.attrMax && attrs[pr.attrMax[0]] && typeof attrs[pr.attrMax[0]].value === 'number' && attrs[pr.attrMax[0]].value <= pr.attrMax[1] ? pr.attrMax[0] : null;
      const hitRaw = pr.rawKeys.find((k) => String(raw[k] || '').trim());
      if (hitTag) { ok = true; evidence = `Approved tag "${hitTag}"`; }
      else if (hitTrue) { ok = true; evidence = `${hitTrue}: true` + (raw.promo_text ? ` (${String(raw.promo_text).slice(0, 30)})` : ''); }
      else if (hitMonth.length) { ok = true; evidence = `Peak season ${months.map((m) => MON_ABBR[m - 1]).join(', ')} (matches ${hitMonth.map((m) => MON_ABBR[m - 1]).join(', ')})`; }
      else if (hitMax) { ok = true; evidence = `${attrs[hitMax].label} ${fmt(attrs[hitMax])}`; }
      else if (hitRaw) { ok = true; evidence = `${hitRaw}: ${String(raw[hitRaw]).slice(0, 40)}`; }
      else if (pr.re) {
        // category paths / internal catalog keys are not product evidence
        const src = [`[Title] ${product.title || ''}`, `[Brand] ${product.brand || ''}`, `[Description] ${product.description || ''}`,
          ...Object.entries(raw).filter(([k]) => !INTERNAL_RAW.includes(k)).map(([k, v]) => `${k}: ${v}`)].join('\n');
        const m = src.match(pr.re);
        if (m) { ok = true; evidence = src.slice(Math.max(0, m.index - 12), Math.min(src.length, m.index + m[0].length + 12)).replace(/\n/g, ' ').trim(); }
      }
    }
    return { id: pr.id, label: pr.label, season: pr.season, season_label: SEASON_LABEL[pr.season] || '', icon: pr.icon, available: ok,
      reason: ok ? '' : `No evidence of ${pr.need} for this product`, evidence,
      facts: pr.facts.filter((k) => attrs[k]).map((k) => ({ label: attrs[k].label, value: fmt(attrs[k]) })), prompt: pr.prompt };
  };
  const svgUri = (w, h, icon, label, sub, season) => {
    const [c1, c2] = SEASON_COL[season] || SEASON_COL.all;
    const s = `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="${c1}"/><stop offset="1" stop-color="${c2}"/></linearGradient></defs><rect width="100%" height="100%" fill="url(#g)"/><rect x="${w * 0.22}" y="${h * 0.3}" width="${w * 0.56}" height="${h * 0.3}" rx="${w * 0.04}" fill="#fff" stroke="#cfd6e0"/><text x="50%" y="${h * 0.22}" font-size="${w * 0.12}" text-anchor="middle">${icon}</text><text x="50%" y="${h * 0.48}" font-size="${w * 0.06}" text-anchor="middle" fill="#1a73e8" font-family="sans-serif" font-weight="bold">${label.replace(/[<&>]/g, '')}</text><text x="50%" y="${h * 0.78}" font-size="${w * 0.045}" text-anchor="middle" fill="#1f2328" font-family="sans-serif">${sub.replace(/[<&>]/g, '')}</text></svg>`;
    return 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(s);
  };
  const productOf = async (sku) => {
    const list = (await mock.products({})).items;
    const p = list.find((x) => x.sku === sku) || list[0];
    return { sku: p.sku, title: p.title, brand: p.brand, group: p.group, group_label: p.group_label, image_url: p.image_url, has_image: !!p.image_url && !/noimage/i.test(p.image_url) };
  };
  // product + records used for preset evidence (full detail when a fixture / CSV result exists, else list tags only)
  const evidenceOf = async (sku) => {
    const list = (await mock.products({})).items;
    const it = list.find((x) => x.sku === sku) || list[0];
    let product = { ...it, description: '', raw_attributes: {} };
    let recs = (it.tags || []).map((t) => ({ kind: 'tag', value: t, label: t, gate_status: 'auto_approved' }));
    if (mockState.csvDetails[it.sku] || FIXTURE_SKUS.has(it.sku)) {
      try { const d = await mock.product(it.sku); product = { ...it, ...d.product }; recs = d.records || []; } catch (_) { /* list-only evidence */ }
    }
    return { product, recs };
  };
  const presetsFor = async (sku) => {
    const { product, recs } = await evidenceOf(sku);
    const g = product.group || 'grocery';
    return PRESETS.filter((p) => p.groups.includes(g)).map((p) => evaluateOne(p, product, recs));
  };
  const bucketOf = async () => { try { return (await fixture('config.json')).image_bucket || BUCKET_FALLBACK; } catch (_) { return BUCKET_FALLBACK; } };
  const MODEL_LABELS = { 'gemini-3.1-flash-image': 'Flash (fast, standard)', 'gemini-3-pro-image': 'Pro (high quality, best for text)', 'gemini-2.5-flash-image': '2.5 Flash Image (stable)' };
  const ts = () => new Date().toISOString().replace(/\D/g, '').slice(0, 14).replace(/^(\d{8})/, '$1-');
  const abortErr = () => Object.assign(new Error('aborted'), { name: 'AbortError' });
  const DOCS = {};
  // (2) sample documents (Cymbal): spec sheet PDF / winter nabe price list / frozen-food label photo
  const MIME = { pdf: 'application/pdf', png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', webp: 'image/webp', csv: 'text/csv',
    xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' };
  const mimeOf = (name) => MIME[(String(name).split('.').pop() || '').toLowerCase()] || 'application/octet-stream';
  const SAMPLE_DOCS = [
    { name: 'spec_cymbal_frypan_20cm.pdf', size: 1284221 },
    { name: 'price_list_2026winter_nabe.xlsx', size: 38211 },
    { name: 'label_cymbal_carbonara.png', size: 742113 },
    { name: 'price_list_2026winter_nabe.csv', size: 4120 },
  ];
  const A = (key, label, value, evidence, page = null) => ({ key, label, value, evidence, page });
  return {
    async imagePresets(sku) {
      await sleep(250);
      const product = await productOf(sku);
      const cfg = await fixture('config.json');
      const ids = (cfg.image_models && cfg.image_models.length) ? cfg.image_models : Object.keys(MODEL_LABELS);
      return {
        sku: product.sku, product, presets: await presetsFor(product.sku),
        options: {
          models: ids.map((id) => ({ id, label: MODEL_LABELS[id] || id })),
          default_model: ids[0],
          sizes: [{ value: 300, label: '300×300' }, { value: 600, label: '600×600' }, { value: 1024, label: '1024×1024' }, { value: 0, label: 'Original size' }],
          default_size: 600, aspects: ['1:1', '4:3', '3:4', '16:9'], default_aspect: '1:1',
          text_modes: [{ id: 'none', label: 'No text' }, { id: 'copy', label: 'Tagline only' }, { id: 'spec', label: 'With specs' }], default_text_mode: 'none',
          counts: [2, 3, 4], default_count: 3, bucket: await bucketOf(),
        },
      };
    },
    async productImages(sku) {
      await sleep(200);
      const p = await productOf(sku);
      return { sku, registered: p.has_image ? [{ url: p.image_url, kind: 'original', label: 'Registered image (catalog)' }] : [], images: clone((imgState[sku] || []).filter((r) => r.status !== 'deleted')) };
    },
    async uploadBaseImage(sku, file, onProgress) {
      for (let i = 1; i <= 5; i++) { await sleep(120); if (onProgress) onProgress(i / 5); }
      const name = `${sku}_base_${ts()}.jpg`;
      return { base: { type: 'upload', uri: `gs://${await bucketOf()}/${sku}/base/${name}`, url: URL.createObjectURL(file) } };
    },
    async searchImages(sku) {
      await sleep(1500);
      const p = await productOf(sku);
      const core = mockItemGroupTitle(p.title);
      const cands = [['eef2f7', 'Manufacturer site'], ['f7f1e8', 'Online retailer'], ['eaf6ee', 'Catalog PDF']].map(([bg, src], i) => ({
        url: `https://placehold.co/800x800/${bg}/1a73e8?text=JAN%20${encodeURIComponent(p.sku)}%0A${i + 1}`, page_url: '',
        title: `${core} | ${src}`, width: 800, height: 800, via: 'grounding' }));
      return { query: `${p.brand || ''} ${core}`.trim(), candidates: cands, message: '' };
    },
    async generateImages(sku, body, onEvent, signal) {
      const chk = () => { if (signal && signal.aborted) throw abortErr(); };
      const p = await productOf(sku);
      const all = await presetsFor(p.sku);
      const presets = all.filter((x) => (body.presets || []).includes(x.id));
      const pr = presets[0] || all.find((x) => x.id === 'ec-white') || all[0];
      const bucket = await bucketOf();
      const n = body.n || 3;
      const size = body.size || 1024;
      const [aw, ah] = String(body.aspect || '1:1').split(':').map(Number);
      const w = aw >= ah ? size : Math.round(size * aw / ah), h = ah >= aw ? size : Math.round(size * ah / aw);
      const t0 = Date.now();
      const prompt = `[Product] ${p.title} (${p.brand || ''})\n[Verified specs] ${presets.flatMap((x) => x.facts.map((f) => `${f.label}: ${f.value}`)).join(' / ') || '—'}\n[Scene] ${presets.map((x) => x.prompt).join(' ')}\n[Extra instructions] ${body.extra_prompt || 'none'}\n[Constraints] Do not change the product shape, color, packaging or logo. Do not depict features, origins or certifications that are not in the source. ${body.text_mode === 'none' ? 'Do not add any text.' : ''}`;
      await sleep(400); chk();
      onEvent({ event: 'start', n, model: body.model, base_url: p.image_url, prompt, facts: presets.flatMap((x) => x.facts) });
      for (let i = 1; i <= n; i++) onEvent({ event: 'progress', index: i, status: 'generating' });
      for (let i = 1; i <= n; i++) {
        await sleep(900 + Math.random() * 700); chk();
        onEvent({ event: 'progress', index: i, status: 'checking' });
        await sleep(350); chk();
        const ps = presets.length ? presets[(i - 1) % presets.length] : pr;
        if (i === 3 && n === 4) { onEvent({ event: 'image_error', index: i, message: 'Blocked by the safety filter (mock)' }); continue; }
        const id = `${sku}_${ps.id}_${ts()}_${i}`;
        const score = i === 2 ? 0.64 : +(0.86 + Math.random() * 0.1).toFixed(2);
        const row = { image_id: id, sku, gcs_uri: `gs://${bucket}/${sku}/${id}.jpg`, url: svgUri(w || 600, h || 600, ps.icon, ps.label, p.title.length > 30 ? p.title.slice(0, 29) + '…' : p.title, ps.season), kind: 'generated', status: 'candidate', is_primary: false,
          preset: ps.id, preset_label: ps.label, season: ps.season, prompt, extra_prompt: body.extra_prompt || '', base_image_uri: (body.base && body.base.uri) || p.image_url, source_url: '',
          model: body.model, width: w || 1024, height: h || 1024, aspect: body.aspect || '1:1', fidelity_score: score, fidelity_note: score >= 0.7 ? 'Same product. Packaging and colors match.' : 'Package text or logo placement may differ from the original image.',
          created_at: new Date().toISOString(), updated_at: new Date().toISOString() };
        (imgState[sku] = imgState[sku] || []).unshift(row);
        onEvent({ event: 'image', index: i, image: clone(row) });
      }
      onEvent({ event: 'complete', count: n, elapsed_ms: Date.now() - t0 });
    },
    async saveImages(sku, body) {
      await sleep(700);
      const rows = imgState[sku] || [];
      const saved = [];
      rows.forEach((r) => {
        if (body.primary_id) r.is_primary = r.image_id === body.primary_id;
        if ((body.image_ids || []).includes(r.image_id)) { r.status = 'saved'; r.updated_at = new Date().toISOString(); saved.push(clone(r)); }
      });
      const total = rows.filter((r) => r.status === 'saved').length + 1;
      return { saved, bq: { table: 'demo-project.product_enrichment.product_images', rows: saved.length },
        aics: body.update_aics ? { table: 'demo-project.ai_commerce_search_dataset.products_aics', updated: true, images_total: total, backup: `products_aics_backup_${ts().slice(0, 8)}`, message: '' } : null };
    },
    async deleteImage(sku, imageId) {
      await sleep(300);
      const r = (imgState[sku] || []).find((x) => x.image_id === imageId);
      if (!r) throw new ApiError('Image not found', 404);
      const wasSaved = r.status === 'saved';
      r.status = 'deleted';
      return { deleted: true, image_id: imageId, gcs_deleted: true, aics: wasSaved ? { table: 'demo-project.ai_commerce_search_dataset.products_aics', updated: true, images_total: (imgState[sku] || []).filter((x) => x.status === 'saved').length + 1 } : null };
    },
    // ── (2) docs
    async docsUpload(file, onProgress) {
      for (let i = 1; i <= 6; i++) { await sleep(90 + Math.random() * 80); if (onProgress) onProgress(i / 6); }
      const uri = `gs://${await bucketOf()}/_docs_uploads/${ts().slice(0, 8)}/${Math.random().toString(16).slice(2, 10)}_${file.name}`;
      const f = { uri, name: file.name, mime: file.type || mimeOf(file.name), size: file.size, url: '' };
      DOCS[uri] = f;
      return { file: f };
    },
    async docsList(gcsUri) {
      await sleep(600);
      if (!/^gs:\/\//.test(gcsUri || '')) throw new ApiError('Enter a path that starts with gs://', 400);
      const base = gcsUri.endsWith('/') ? gcsUri : gcsUri.replace(/[^/]+$/, '');
      if (/\.(pdf|png|jpe?g|webp|xlsx|csv)$/i.test(gcsUri)) { const name = gcsUri.split('/').pop(); return { files: [{ uri: gcsUri, name, mime: mimeOf(name), size: 820000 }], skipped: 0 }; }
      return { files: SAMPLE_DOCS.map((d) => ({ uri: base + d.name, name: d.name, mime: mimeOf(d.name), size: d.size })), skipped: 1 };
    },
    async docsExtract(body, onEvent, signal) {
      const chk = () => { if (signal && signal.aborted) throw abortErr(); };
      const list = (await mock.products({})).items;
      // match catalog SKUs by JAN (Cymbal SKU == JAN)
      const byJan = (jan) => {
        const hit = list.find((x) => x.sku === jan);
        return hit ? { matches: [{ sku: hit.sku, title: hit.title, image_url: hit.image_url, score: 0.99, why: 'JAN match' }], suggested_sku: hit.sku } : { matches: [], suggested_sku: null };
      };
      const files = body.files || [];
      onEvent({ event: 'start', count: files.length, model: MOCK_MODEL });
      let np = 0;
      for (let i = 0; i < files.length; i++) {
        const f = files[i];
        chk(); onEvent({ event: 'file_start', index: i, name: f.name });
        await sleep(1300 + Math.random() * 900); chk();
        if (/fail|broken/i.test(f.name)) { onEvent({ event: 'file_error', index: i, name: f.name, message: 'Could not read the file (mock)' }); continue; }
        const isList = /\.(xlsx|csv)$/i.test(f.name) || /price|list/i.test(f.name);
        const isLabel = !isList && (/\.(png|jpe?g|webp)$/i.test(f.name) || /label/i.test(f.name));
        const mk = (idx, o) => ({ idx, model_number: '', jan: '', price: null, confidence: 0.9, ...o, ...byJan(o.jan) });
        let products, docType;
        if (isList) {
          docType = 'price_list';
          const row = (idx, jan, title, category, price, attrs, extra = {}) => mk(idx, { jan, title, brand: extra.brand || '', category, price, description: extra.description || 'From the Winter Hot Pot Fair price list (Winter 2026).',
            attributes: [A('price_campaign', 'Hot pot fair price', fmtYen(price), String(price)), ...attrs], confidence: extra.confidence || 0.93 });
          products = [
            row(0, '4913667472959', 'Ibaraki Napa Cabbage, Half Cut', 'Produce', 198, [A('origin', 'Origin', 'Ibaraki', 'Ibaraki')]),
            row(1, '4958688424846', 'Miyagi Shucked Oysters for Raw Eating, 150g', 'Seafood', 798, [A('size', 'Net weight', '150g', 'Shucked Oysters 150g'), A('origin', 'Origin', 'Miyagi', 'Miyagi')]),
            row(2, '4922835139112', 'Hokkaido Boiled Hairy Crab, Set of 2 (about 800g)', 'Seafood', 6980, [A('size', 'Net weight', 'about 800g', 'Boiled Hairy Crab about 800g'), A('origin', 'Origin', 'Hokkaido', 'Hokkaido')]),
            row(3, '4978886449342', 'Domestic Bunashimeji Mushrooms, 3 packs (about 300g)', 'Produce', 258, [A('size', 'Pack count', '3 packs', 'Bunashimeji Mushrooms 3 packs')]),
            row(4, '4990000000017', 'Cymbal Hot Pot Soup Base, Yosenabe Soy Sauce, 750g', 'Seasonings & Sauces', 298, [A('size', 'Net weight', '750g', 'Yosenabe Soy Sauce 750g'), A('flavor', 'Flavor', 'Soy sauce', 'Yosenabe Soy Sauce')],
              { brand: 'Cymbal', description: 'New item registered from the Winter Hot Pot Fair price list (Winter 2026). Ready-to-use soy-sauce soup base for yosenabe hot pot.', confidence: 0.86 }),
          ];
        } else if (isLabel) {
          docType = 'label_photo';
          products = [mk(0, { jan: '4911478160614', title: 'Cymbal Basic Frozen Carbonara 260g', brand: 'Cymbal Basic', category: 'Frozen',
            description: 'Creamy carbonara with rich cheese and bacon. Just microwave straight from frozen.\nNet weight: 260g / Storage: keep at -18°C or below / Allergens: wheat, egg, milk, soybean, pork / Best before: 12 months from manufacture',
            attributes: [A('size', 'Net weight', '260g', 'Net weight 260g', 1), A('allergens', 'Allergens', 'wheat, egg, milk, soybean, pork', 'Allergens: wheat, egg, milk, soybean, pork', 1),
              A('storage_type', 'Storage', 'Frozen (keep at -18°C or below)', 'Keep at -18°C or below', 1), A('cooking_instructions', 'Cooking method', 'Microwave 600W about 5 min 30 sec', 'Microwave 600W about 5 min 30 sec', 1),
              A('best_before', 'Best before', '12 months from manufacture', 'Best before 12 months from manufacture', 1)],
            confidence: 0.95 })];
        } else {
          docType = 'spec_sheet';
          products = [mk(0, { jan: '4954950777214', title: 'Cymbal Basic Non-Stick Frying Pan 20cm', brand: 'Cymbal Basic', category: 'Kitchen',
            description: 'Frying pan with an aluminum alloy body and fluororesin non-stick coating. Works on induction (100V/200V) and gas. Not dishwasher safe.',
            attributes: [A('material', 'Material', 'Aluminum alloy, fluororesin coating', 'Material Body: aluminum alloy Surface: fluororesin coating', 1),
              A('heat_source', 'Heat sources', 'Induction (100V/200V), gas', 'Induction 100V/200V, gas compatible', 1), A('dishwasher', 'Dishwasher', 'Not dishwasher safe', 'Not dishwasher safe', 2),
              A('size', 'Size', 'Diameter 20 x depth 5.0cm', 'Diameter 20 x depth 5.0cm', 1), A('weight', 'Weight', 'about 480g', 'Weight about 480g', 1), A('origin', 'Country of origin', 'Made in China', 'Country of origin: China', 2)],
            confidence: 0.94 })];
        }
        np += products.length;
        const ti = 2400 + Math.round(Math.random() * 1500), to = 600 + Math.round(Math.random() * 400);
        onEvent({ event: 'file_done', index: i, name: f.name, uri: f.uri, mime: f.mime, document_type: docType, latency_ms: 1300 + Math.round(Math.random() * 2000), input_tokens: ti, output_tokens: to, products });
      }
      onEvent({ event: 'complete', files: files.length, products: np });
    },
    async docsPreview(body) {
      await sleep(450);
      const prod = body.product || {};
      let cur = { description: '', raw_attributes: {} };
      if (body.sku) { try { cur = (await mock.product(body.sku)).product; } catch (_) { /* new-ish */ } }
      const raw = cur.raw_attributes || {};
      const diff = [];
      const put = (field, label, before, after) => {
        before = before == null ? '' : String(before); after = after == null ? '' : String(after);
        let action = body.new_sku ? 'new' : !before ? 'add' : before === after ? 'keep' : body.mode === 'overwrite' ? 'overwrite' : 'keep';
        if (!after) action = 'keep';
        diff.push({ field, label, before, after: action === 'keep' && before ? before : after, action });
      };
      if (body.new_sku) { put('title', 'Title', '', prod.title); put('brand', 'Brand', '', prod.brand); put('category', 'Category', '', prod.category); }
      put('description', 'Description', body.new_sku ? '' : cur.description, prod.description);
      (prod.attributes || []).forEach((a) => put('attr:' + a.key, a.label || a.key, body.new_sku ? '' : raw[a.key], a.value));
      return { sku: body.sku || body.new_sku, is_new: !!body.new_sku, diff, changes: diff.filter((d) => d.action !== 'keep').length };
    },
    async docsApply(body, onEvent, signal) {
      const chk = () => { if (signal && signal.aborted) throw abortErr(); };
      const t0 = Date.now();
      const items = body.items || [];
      onEvent({ event: 'start', count: items.length });
      const results = [];
      for (let i = 0; i < items.length; i++) {
        const it = items[i];
        const sku = it.sku || it.new_sku;
        chk(); onEvent({ event: 'item_start', index: i, sku });
        await sleep(600); chk();
        const pv = await mock.docsPreview(it);
        onEvent({ event: 'merged', index: i, sku, changes: pv.changes, diff: pv.diff });
        if (body.enrich) {
          await sleep(1500); chk();
          const fb = it.new_sku ? 0 : 0.42 + Math.random() * 0.2;
          onEvent({ event: 'enriched', index: i, sku, attr_count: 9 + i, tag_count: 3, status: i % 2 ? 'needs_review' : 'completed', fill_before: +fb.toFixed(3), fill_after: +(0.82 + Math.random() * 0.15).toFixed(3), latency_ms: 2100 + Math.round(Math.random() * 900) });
        }
        results.push({ sku, status: 'ok', changes: pv.changes });
      }
      await sleep(700); chk();
      const DS = 'demo-project.product_enrichment';
      onEvent({ event: 'saved', bq: { dataset: DS, tables: { [`${DS}.source_products`]: items.length, [`${DS}.enriched_attributes`]: items.length * 11, [`${DS}.sku_extras`]: items.length, [`${DS}.golden_records`]: items.length, [`${DS}.doc_extractions`]: items.length } } });
      if (body.update_aics) {
        await sleep(600); chk();
        onEvent({ event: 'aics', result: { table: 'demo-project.ai_commerce_search_dataset.products_aics', updated: items.filter((x) => x.sku).length, inserted: items.filter((x) => x.new_sku).length, backup: `products_aics_backup_${ts().slice(0, 8)}`, message: '' } });
      }
      onEvent({ event: 'complete', results, elapsed_ms: Date.now() - t0 });
    },
  };
}
