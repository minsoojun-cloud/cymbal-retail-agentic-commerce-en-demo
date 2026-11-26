/* Shared helpers, product card + detail modal, view host. window.CR */
(function () {
  'use strict';
  const CFG = window.CR_CONFIG || {};

  const PLACEHOLDER_IMG = 'data:image/svg+xml;utf8,' + encodeURIComponent(
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><rect width='100' height='100' fill='#f1f3f4'/>" +
    "<circle cx='50' cy='46' r='14' fill='none' stroke='#bdc1c6' stroke-width='3'/></svg>");
  const SPARK = '<svg viewBox="0 0 24 24" aria-hidden="true"><defs><linearGradient id="crsg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#4285f4"/><stop offset=".6" stop-color="#9b72cb"/><stop offset="1" stop-color="#d96570"/></linearGradient></defs><path fill="url(#crsg)" d="M12 2c.4 4.9 2.9 8.5 9 10-6.1 1.5-8.6 5.1-9 10-.4-4.9-2.9-8.5-9-10 6.1-1.5 8.6-5.1 9-10z"/></svg>';

  const yen = (n) => Math.round(Number(n) || 0).toLocaleString('en-US', { style: 'currency', currency: 'JPY' });
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  function getCookie(name) {
    const m = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
    return m ? decodeURIComponent(m[1]) : null;
  }
  function getCustomer() {
    const id = getCookie('cr_customer') || 'CR-0001';
    const c = (CFG.customers || {})[id] || { customer_id: id, name: 'Sakura' };
    return { customer_id: id, name: c.name };
  }
  function getVisitorId() {
    let v = getCookie('cr_visitor');
    if (!v) {
      try { v = localStorage.getItem('cr_visitor_id'); } catch (e) { /* ignore */ }
      if (!v) { v = 'v-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8); }
      try { localStorage.setItem('cr_visitor_id', v); } catch (e) { /* ignore */ }
    }
    return v;
  }

  let toastTimer = null;
  function toast(msg, opts) {
    opts = opts || {};
    const el = document.getElementById('cr-toast');
    if (!el) return;
    el.textContent = msg;
    el.classList.toggle('is-success', !!opts.success);
    el.classList.add('is-show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove('is-show'), opts.ms || 3200);
  }

  async function fetchJSON(url, init) {
    const r = await fetch(url, Object.assign({ headers: { Accept: 'application/json', 'Content-Type': 'application/json' } }, init || {}));
    if (!r.ok) throw new Error('HTTP ' + r.status + ' ' + url);
    return r.json();
  }

  // ---- product cache ----
  const productCache = new Map();
  /** CONTRACT v6: each site serves its own product images → absolute catalog URIs (…/img/products/<file>) become relative */
  function localImg(u) {
    if (!u || typeof u !== 'string') return u || '';
    const m = u.split(/[?#]/)[0].match(/\/img\/products\/([^/]+)$/);
    return m ? '/img/products/' + m[1] : u;
  }
  function remember(list) { (list || []).forEach((p) => { if (p && p.id) { if (p.image) p.image = localImg(p.image); productCache.set(p.id, p); } }); return list; }
  async function fetchProducts(ids) {
    ids = (ids || []).map(String).filter(Boolean);
    const missing = ids.filter((i) => !productCache.has(i));
    if (missing.length) {
      const res = await fetchJSON('/api/products?ids=' + encodeURIComponent(missing.join(',')));
      remember(res.products || []);
    }
    return ids.map((i) => productCache.get(i)).filter(Boolean);
  }
  async function getProduct(id) { return (await fetchProducts([id]))[0] || null; }

  // ---- feature status (✨ labels) ----
  let featureStatus = {};
  function featureLabel(name, key) {
    const st = featureStatus[key] || '';
    const tip = { live: 'Vertex AI Search for Commerce (live)', fallback: 'Fallback results', mock: 'Local mode (mock)' }[st] || '';
    return '<span class="cr-feature" data-feature="' + esc(key) + '" data-status="' + esc(st) + '" title="' + esc(tip) + '">' + esc(name) + '</span>';
  }
  function setFeatureStatus(map) {
    featureStatus = Object.assign({}, featureStatus, map || {});
    $$('.cr-feature[data-feature]').forEach((el) => { const s = featureStatus[el.dataset.feature]; if (s) el.dataset.status = s; });
  }

  // ---- rendering ----
  function stars(r) {
    const n = Math.round(Number(r) || 0);
    return '<i>' + '★'.repeat(n) + '<span style="color:#dadce0">' + '★'.repeat(Math.max(0, 5 - n)) + '</span></i>';
  }
  function priceHtml(p) {
    const sale = p.on_sale && p.original_price > p.price;
    return '<div class="cr-price"><span class="cr-price__now' + (sale ? ' is-sale' : '') + '">' + yen(p.price) + '</span>' +
      (sale ? '<span class="cr-price__was">' + yen(p.original_price) + '</span>' : '') +
      '<span class="cr-price__tax">tax incl.</span></div>';
  }
  function discountBadge(p) {
    if (!p.on_sale) return '';
    const pct = Math.round((p.discount_rate || 0) * 100);
    return '<span class="cr-card__badge">' + (pct > 0 ? pct + '% OFF' : 'Sale') + '</span>';
  }
  // dinner-set tag chips (show_products_on_page sections[].tags) → colour class
  const TAG_CLASS = { 'frequently bought': 'freq', 'family favorite': 'family', 'on sale': 'sale', 'in season': 'season', 'quick': 'quick' };
  // ---- omnichannel (CONTRACT_EN v14): channel = omni | store_only | online_only ----
  const PIN = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2a7 7 0 0 0-7 7c0 5.25 7 13 7 13s7-7.75 7-13a7 7 0 0 0-7-7zm0 9.5A2.5 2.5 0 1 1 12 6.5a2.5 2.5 0 0 1 0 5z"/></svg>';
  const channelOf = (p) => String((p && p.channel) || 'omni').toLowerCase();
  const CHANNEL_LABEL = { store_only: 'In-store only', online_only: 'Online only' };
  function channelBadge(p, cls) {
    const c = channelOf(p);
    return CHANNEL_LABEL[c] ? '<span class="cr-chan cr-chan--' + c + (cls ? ' ' + cls : '') + '" data-channel="' + c + '">' + CHANNEL_LABEL[c] + '</span>' : '';
  }
  function cardBuyHtml(p) {
    if (channelOf(p) === 'store_only') {
      return '<div class="cr-card__instore">' +
        '<button type="button" class="cr-btn cr-btn--tonal cr-card__add is-store-only" id="add-' + esc(p.id) + '" disabled>In-store only</button>' +
        '<button type="button" class="cr-link-btn cr-card__stores" data-stores="' + esc(p.id) + '">' + PIN + 'Check stores</button></div>';
    }
    return '<button type="button" class="cr-btn cr-btn--tonal cr-card__add" data-add="' + esc(p.id) + '" id="add-' + esc(p.id) + '">Add to cart</button>';
  }
  function cardHtml(p, opts) {
    opts = opts || {};
    remember([p]);
    return '<article class="cr-card' + (opts.highlight ? ' is-highlight' : '') + (opts.compact ? ' cr-card--h' : '') + '" data-pid="' + esc(p.id) + '" style="animation-delay:' + ((opts.i || 0) * 25) + 'ms">' +
      '<button type="button" class="cr-card__media" data-open="' + esc(p.id) + '" aria-label="' + 'Details: ' + esc(p.title) + '">' +
        '<img loading="lazy" src="' + esc(p.image || PLACEHOLDER_IMG) + '" alt="" onerror="this.onerror=null;this.src=CR.PLACEHOLDER_IMG">' +
        discountBadge(p) + channelBadge(p, 'cr-card__chan') +
        (opts.rank ? '<span class="cr-card__rank' + (opts.rank <= 3 ? ' is-top' : '') + '" data-rank="' + opts.rank + '">' + opts.rank + '</span>' : '') +
        (opts.highlight ? '<span class="cr-card__ai">✨ AI pick</span>' : '') +
      '</button>' +
      '<div class="cr-card__body">' +
        '<button type="button" class="cr-card__title" data-open="' + esc(p.id) + '">' + esc(p.title) + '</button>' +
        (opts.qty > 1 ? priceHtml(p).replace(/<\/div>$/, '<span class="cr-price__qty" data-qty="' + opts.qty + '">×' + opts.qty + '</span></div>') : priceHtml(p)) +
        '<div class="cr-card__meta"><span class="cr-stars">' + stars(p.rating) + ' ' + (p.rating || '') + ' <small>(' + (p.rating_count || 0) + ')</small></span>' +
          (channelOf(p) === 'omni' ? '<button type="button" class="cr-card__stores cr-card__stores--mini" data-stores="' + esc(p.id) + '" title="Available in stores" aria-label="Available in stores">' + PIN + 'In stores</button>' : '') + '</div>' +
        (opts.tags && opts.tags.length ? '<div class="cr-tags">' + opts.tags.map((t) => '<span class="cr-tag cr-tag--' + (TAG_CLASS[String(t).toLowerCase()] || 'other') + '">' + esc(t) + '</span>').join('') + '</div>' : '') +
        (opts.reason ? '<div class="cr-card__reason">' + esc(opts.reason) + '</div>' : '') +
      '</div>' +
      cardBuyHtml(p) +
    '</article>';
  }
  function skeletonGrid(n) {
    let h = '';
    for (let i = 0; i < (n || 8); i++) h += '<div class="cr-skel-card"><div class="m"></div><div class="l"></div><div class="l s"></div></div>';
    return h;
  }

  // ---- product modal ----
  const modal = () => document.getElementById('cr-product-modal');
  let modalQty = 1;
  async function openProduct(id, opts) {
    const p = await getProduct(id);
    if (!p) { toast('Product not found'); return; }
    modalQty = 1;
    const body = document.getElementById('cr-pm-body');
    body.innerHTML =
      '<div class="cr-pd">' +
        '<div class="cr-pd__media"><img src="' + esc(p.image || PLACEHOLDER_IMG) + '" alt="' + esc(p.title) + '">' + discountBadge(p) + '</div>' +
        '<div>' +
          '<div class="cr-pd__brand">' + esc(p.brand || p.category) + channelBadge(p, 'cr-pd__chan') + '</div>' +
          '<h2 id="cr-pm-title">' + esc(p.title) + '</h2>' +
          '<div class="cr-stars">' + stars(p.rating) + ' ' + esc(p.rating) + ' <small>(' + esc(p.rating_count) + ' reviews)</small></div>' +
          '<div style="margin-top:14px">' + priceHtml(p) + '</div>' +
          (p.promo_text ? '<div class="cr-pd__promo">' + esc(p.promo_text) + '</div>' : '') +
          '<div class="cr-pd__labels">' + [p.category, p.size].concat(p.labels || []).filter(Boolean).map((l) => '<span>' + esc(l) + '</span>').join('') + '</div>' +
          '<div class="cr-pd__desc">' + esc(p.description) + '</div>' +
          '<div class="cr-pd__buy">' +
            '<div class="cr-qty"><button type="button" data-qstep="-1" aria-label="Decrease quantity">−</button><span id="cr-pm-qty">1</span><button type="button" data-qstep="1" aria-label="Increase quantity">+</button></div>' +
            (channelOf(p) === 'store_only'
              ? '<button type="button" class="cr-btn cr-btn--primary is-store-only" id="cr-pm-add" style="flex:1;height:44px;border-radius:22px" disabled>In-store only</button>'
              : '<button type="button" class="cr-btn cr-btn--primary" id="cr-pm-add" style="flex:1;height:44px;border-radius:22px">Add to cart</button>') +
          '</div>' +
          (channelOf(p) === 'store_only' ? '<p class="cr-pd__chan-note">This item is sold in our stores only. Check stock at a store near you.</p>' : channelOf(p) === 'online_only' ? '<p class="cr-pd__chan-note">Available for online delivery only.</p>' : '') +
          (channelOf(p) !== 'online_only' ? '<button type="button" class="cr-btn cr-pd__stores" id="cr-pm-stores" data-stores="' + esc(p.id) + '">' + PIN + 'Available in stores</button>' : '') +
          '<button type="button" class="cr-btn cr-pd__ask" id="cr-pm-ask">' + SPARK + 'Ask the AI Concierge</button>' +
        '</div>' +
      '</div>' +
      '<button type="button" class="cr-icon-btn cr-pd__close" data-close aria-label="Close"><svg viewBox="0 0 24 24"><path d="M19 6.41 17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg></button>';
    body.querySelector('#cr-pm-add').onclick = () => { const r = window.Cart.add(p, modalQty); if (r && r.ok !== false) closeProduct(); };
    body.querySelector('#cr-pm-ask').onclick = () => { closeProduct(); window.CRChat && window.CRChat.ask('Tell me about "' + p.title + '".'); };
    body.querySelectorAll('[data-qstep]').forEach((b) => b.onclick = () => {
      modalQty = Math.max(1, Math.min(99, modalQty + Number(b.dataset.qstep)));
      body.querySelector('#cr-pm-qty').textContent = modalQty;
    });
    modal().hidden = false;
    document.body.style.overflow = 'hidden';
    CR.pageContext.product_id = p.id; CR.pageContext.product_title = p.title;
    CR.emit('context');
    if (!(opts && opts.noHistory)) {
      const u = new URL(location.href); u.searchParams.set('product', p.id);
      history.replaceState(history.state, '', u);
    }
  }
  function closeProduct() {
    if (modal().hidden) return;
    modal().hidden = true;
    document.body.style.overflow = '';
    CR.pageContext.product_id = ''; CR.pageContext.product_title = '';
    CR.emit('context');
    const u = new URL(location.href);
    if (u.searchParams.has('product')) { u.searchParams.delete('product'); history.replaceState(history.state, '', u); }
  }

  // ---- tiny event bus ----
  const bus = {};
  const on = (ev, fn) => { (bus[ev] = bus[ev] || []).push(fn); };
  const emit = (ev, data) => (bus[ev] || []).forEach((fn) => { try { fn(data); } catch (e) { console.error(e); } });

  const CR = window.CR = {
    CFG, PLACEHOLDER_IMG, SPARK, yen, esc, localImg, $, $$, getCookie, getCustomer, getVisitorId, toast, fetchJSON,
    fetchProducts, getProduct, remember, cardHtml, priceHtml, stars, skeletonGrid, openProduct, closeProduct, TAG_CLASS,
    featureLabel, setFeatureStatus, on, emit, channelOf, channelBadge, PIN,
    pageContext: { view: 'home', query: '', product_id: '', product_title: '' },
    view: () => document.getElementById('cr-view'),
  };

  // global delegation: open product / add to cart from any card
  document.addEventListener('click', (e) => {
    const add = e.target.closest('[data-add]');
    if (add) {
      e.preventDefault();
      const p = productCache.get(add.dataset.add);
      if (p) {
        const r = window.Cart.add(p, 1);
        if (r && r.ok === false) return;
        add.classList.add('is-added'); add.textContent = '✓ Added';
        setTimeout(() => { add.classList.remove('is-added'); add.textContent = 'Add to cart'; }, 1600);
      }
      return;
    }
    const op = e.target.closest('[data-open]');
    if (op) { e.preventDefault(); openProduct(op.dataset.open); return; }
    if (e.target.closest('#cr-product-modal [data-close]')) closeProduct();
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeProduct(); });

  // CONTRACT v5/v7: header icon-only links (with hover menu-name tooltip) to Data Enrichment Studio & Flyer Studio
  const ICON_ENRICH = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19 9l1.25-2.75L23 5l-2.75-1.25L19 1l-1.25 2.75L15 5l2.75 1.25L19 9zm-7.5.5L9 4 6.5 9.5 1 12l5.5 2.5L9 20l2.5-5.5L17 12l-5.5-2.5zM19 15l-1.25 2.75L15 19l2.75 1.25L19 23l1.25-2.75L23 19l-2.75-1.25L19 15z"/></svg>';
  const ICON_FLYER = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm-5 14H7v-2h7v2zm3-4H7v-2h10v2zm0-4H7V7h10v2z"/></svg>';
  const ICON_STORE = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 4H4v2h16V4zm1 10v-2l-1-5H4l-1 5v2h1v6h10v-6h4v6h2v-6h1zm-9 4H6v-4h6v4z"/></svg>';
  function mountCrossLinks() {
    const nav = document.querySelector('.cr-header__actions');
    if (!nav) return;
    const storeAppUrl = String(CFG.storeAppUrl || '');
    if (/^https?:\/\//.test(storeAppUrl) && !document.getElementById('cr-storeapp-link')) {
      const a = document.createElement('a');
      a.id = 'cr-storeapp-link';
      a.className = 'cr-xlink';
      a.href = storeAppUrl;
      a.dataset.tooltip = 'Store App';
      a.title = 'Store App';
      a.setAttribute('aria-label', 'Go to the Cymbal Retail Store App');
      a.innerHTML = '<span class="cr-xlink__icon">' + ICON_STORE + '</span>';
      nav.insertBefore(a, nav.firstChild);
    }
    const flyerUrl = String(CFG.flyerUrl || '');
    if (/^https?:\/\//.test(flyerUrl) && !document.getElementById('cr-flyer-link')) {
      const a = document.createElement('a');
      a.id = 'cr-flyer-link';
      a.className = 'cr-xlink';
      a.href = flyerUrl;
      a.dataset.tooltip = 'Flyer Studio';
      a.title = 'Flyer Studio';
      a.setAttribute('aria-label', 'Go to Flyer Studio');
      a.innerHTML = '<span class="cr-xlink__icon">' + ICON_FLYER + '</span>';
      nav.insertBefore(a, nav.firstChild);
    }
    const url = String(CFG.enrichUrl || '');
    if (/^https?:\/\//.test(url) && !document.getElementById('cr-enrich-link')) {
      const a = document.createElement('a');
      a.id = 'cr-enrich-link';
      a.className = 'cr-xlink';
      a.href = url;
      a.dataset.tooltip = 'Data Enrichment';
      a.title = 'Go to Product Data Enrichment Studio';
      a.setAttribute('aria-label', 'Go to Product Data Enrichment Studio');
      a.innerHTML = '<span class="cr-xlink__icon">' + ICON_ENRICH + '</span>';
      nav.insertBefore(a, nav.firstChild);
    }
    if (document.getElementById('cr-enrich-link') || document.getElementById('cr-flyer-link') || document.getElementById('cr-storeapp-link')) {
      const header = document.getElementById('cr-header');
      if (header) header.classList.add('cr-header--xlink');
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    mountCrossLinks();
    const c = getCustomer();
    const g = document.getElementById('cr-greeting'); if (g) g.textContent = 'Hi, ' + String(c.name || 'Guest').split(' ')[0];
    const a = document.getElementById('cr-avatar'); if (a) a.textContent = (c.name || 'G').slice(0, 1).toUpperCase();
    const header = document.getElementById('cr-header');
    window.addEventListener('scroll', () => header.classList.toggle('is-scrolled', window.scrollY > 4), { passive: true });
    fetchJSON('/api/features').then((f) => setFeatureStatus(f.features)).catch(() => {});
  });
})();
