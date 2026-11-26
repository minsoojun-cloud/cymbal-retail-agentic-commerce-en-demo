/* Omnichannel store availability (CONTRACT_EN v14). window.CRStores
 *   GET /api/products/{id}/stores?lat=&lng= → {product_id, channel, online_available, source, stores:[{store_id, name, short_name,
 *       address, phone, lat, lng, open_now, hours, quantity, availability, pickup, shelf_label?, distance_m?}]}
 *   open(product_ids)            → "Available in stores" modal (#cr-store-modal), one section per product
 *   [data-stores="<id>"] click   → open([id])  (product cards, detail modal)
 *   CRClientTools.show_store_availability({product_ids, message}) → modal + compact chat card → {shown:n}
 * Location: navigator.geolocation is used only when permission is already granted (no prompt during demos).
 */
(function () {
  'use strict';
  const CR = window.CR;
  const { esc } = CR;
  const PIN = CR.PIN || '';
  let geo; // undefined = not tried, null = unavailable, {lat,lng}

  async function getGeo() {
    if (geo !== undefined) return geo;
    geo = null;
    try {
      if (!navigator.geolocation || !navigator.permissions) return geo;
      const st = await navigator.permissions.query({ name: 'geolocation' });
      if (st.state !== 'granted') return geo;
      geo = await new Promise((res) => {
        navigator.geolocation.getCurrentPosition((p) => res({ lat: p.coords.latitude, lng: p.coords.longitude }), () => res(null), { timeout: 2500, maximumAge: 600000 });
      });
    } catch (e) { geo = null; }
    return geo;
  }

  async function fetchStores(id) {
    const g = await getGeo();
    const qs = g ? '?lat=' + encodeURIComponent(g.lat) + '&lng=' + encodeURIComponent(g.lng) : '';
    return CR.fetchJSON('/api/products/' + encodeURIComponent(id) + '/stores' + qs);
  }

  function distanceText(m) {
    if (m == null || isNaN(m)) return '';
    return m < 1000 ? Math.round(m) + ' m' : (m / 1000).toLocaleString('en-US', { maximumFractionDigits: 1 }) + ' km';
  }
  function qtyText(s) {
    const n = Number(s.quantity) || 0;
    if (s.availability === 'OUT_OF_STOCK' || n <= 0) return '<span class="cr-st__qty is-out">Out of stock</span>';
    return '<span class="cr-st__qty' + (n <= 3 ? ' is-low' : '') + '">' + n.toLocaleString('en-US') + ' in stock</span>';
  }
  function storeHtml(s) {
    const maps = (s.lat != null && s.lng != null)
      ? 'https://www.google.com/maps/dir/?api=1&destination=' + encodeURIComponent(s.lat + ',' + s.lng)
      : 'https://www.google.com/maps/search/?api=1&query=' + encodeURIComponent(s.name + ' ' + (s.address || ''));
    const dist = distanceText(s.distance_m);
    return '<li class="cr-st" data-store="' + esc(s.store_id) + '">' +
      '<div class="cr-st__main">' +
        '<div class="cr-st__name">' + esc(s.name) + (dist ? '<small>' + esc(dist) + '</small>' : '') + '</div>' +
        '<div class="cr-st__addr">' + esc(s.address || '') + '</div>' +
        '<div class="cr-st__hours"><span class="cr-st__open ' + (s.open_now ? 'is-open' : 'is-closed') + '">' + (s.open_now ? 'Open now' : 'Closed') + '</span>' +
          (s.hours ? ' • ' + esc(s.hours) : '') + '</div>' +
        (s.shelf_label ? '<div class="cr-st__shelf">' + PIN + esc(s.shelf_label) + '</div>' : '') +
      '</div>' +
      '<div class="cr-st__side">' + qtyText(s) +
        (s.pickup ? '<span class="cr-st__pickup">Pickup available</span>' : '') +
        '<a class="cr-link-btn cr-st__dir" href="' + esc(maps) + '" target="_blank" rel="noopener">Get directions</a>' +
      '</div></li>';
  }
  function sectionHtml(p, data) {
    const stores = (data && data.stores) || [];
    const ch = (data && data.channel) || CR.channelOf(p);
    const inStock = stores.filter((s) => s.availability !== 'OUT_OF_STOCK' && Number(s.quantity) > 0).length;
    const note = ch === 'online_only' ? 'This item is sold online only, not in stores.'
      : stores.length ? (inStock ? 'In stock at ' + inStock + ' of ' + stores.length + (stores.length === 1 ? ' store' : ' stores') : 'Currently out of stock in nearby stores')
        : 'No store information available.';
    return '<section class="cr-stsec" data-pid="' + esc(p ? p.id : (data && data.product_id) || '') + '">' +
      (p ? '<div class="cr-stsec__prod"><img src="' + esc(p.image || CR.PLACEHOLDER_IMG) + '" alt="">' +
        '<div><b>' + esc(p.title) + '</b><div class="cr-stsec__meta">' + CR.yen(p.price) + ' <small>tax incl.</small> ' + CR.channelBadge(p) + '</div>' +
        '<div class="cr-stsec__note">' + esc(note) + '</div></div></div>' : '') +
      (stores.length ? '<ul class="cr-stlist">' + stores.map(storeHtml).join('') + '</ul>' : '') +
      '</section>';
  }

  function modalEl() {
    let m = document.getElementById('cr-store-modal');
    if (m) return m;
    m = document.createElement('div');
    m.className = 'cr-modal cr-store-modal';
    m.id = 'cr-store-modal';
    m.setAttribute('role', 'dialog');
    m.setAttribute('aria-modal', 'true');
    m.setAttribute('aria-label', 'Available in stores');
    m.hidden = true;
    m.innerHTML = '<div class="cr-modal__scrim" data-st-close></div><div class="cr-modal__panel cr-store-modal__panel">' +
      '<header class="cr-store-modal__head"><h2>' + PIN + 'Available in stores</h2>' +
      '<button type="button" class="cr-icon-btn" data-st-close aria-label="Close"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19 6.41 17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg></button></header>' +
      '<p class="cr-store-modal__msg" id="cr-store-msg" hidden></p>' +
      '<div class="cr-store-modal__body" id="cr-store-body"></div>' +
      '<footer class="cr-store-modal__foot">Stock levels are updated regularly and may change. ' +
        (/^https?:\/\//.test(String(CR.CFG.storeAppUrl || '')) ? '<a href="' + esc(CR.CFG.storeAppUrl) + '" target="_blank" rel="noopener">Open the Store App</a>' : '') + '</footer></div>';
    document.body.appendChild(m);
    m.addEventListener('click', (e) => { if (e.target.closest('[data-st-close]')) close(); });
    return m;
  }
  function close() { const m = document.getElementById('cr-store-modal'); if (m) m.hidden = true; }

  let seq = 0;
  /** open(product_ids, {message}) → Promise<{shown, results:[{product, data}]}> */
  async function open(ids, opts) {
    opts = opts || {};
    ids = (ids || []).map(String).filter(Boolean).slice(0, 6);
    const my = ++seq;
    const m = modalEl();
    const msg = m.querySelector('#cr-store-msg');
    msg.hidden = !opts.message; msg.textContent = opts.message || '';
    const body = m.querySelector('#cr-store-body');
    body.innerHTML = '<div class="cr-shimmer"><i></i><i></i><i></i></div>';
    m.hidden = false;
    const products = await CR.fetchProducts(ids).catch(() => []);
    const byId = Object.fromEntries((products || []).map((p) => [String(p.id), p]));
    const results = await Promise.all(ids.map(async (id) => ({ id, product: byId[id] || null, data: await fetchStores(id).catch(() => null) })));
    if (my !== seq) return { shown: 0 };
    const ok = results.filter((r) => r.product || r.data);
    body.innerHTML = ok.length ? ok.map((r) => sectionHtml(r.product, r.data)).join('') : '<p class="cr-store-modal__empty">Store availability is not available for this item.</p>';
    return { shown: ok.length, results: ok };
  }

  /** compact chat card (inside #cr-chat-log) summarising the store availability */
  function chatCard(results) {
    if (!window.ChatRich || !results.length) return;
    const rows = results.map((r) => {
      const p = r.product || { id: r.id, title: r.id };
      const stores = (r.data && r.data.stores) || [];
      const list = stores.map((s) => {
        const n = Number(s.quantity) || 0;
        const out = s.availability === 'OUT_OF_STOCK' || n <= 0;
        return '<li><span>' + esc(s.short_name || s.name) + (s.open_now ? '' : ' <small>(closed)</small>') + '</span><b class="' + (out ? 'is-out' : '') + '">' + (out ? 'Out of stock' : n + ' in stock') + '</b></li>';
      }).join('');
      return '<div class="crc-st"><div class="crc-st__t">' + esc(p.title) + '</div>' +
        (list ? '<ul class="crc-st__list">' + list + '</ul>' : '<div class="crc-st__none">' + (r.data && r.data.channel === 'online_only' ? 'Online only' : 'No store stock') + '</div>') + '</div>';
    }).join('');
    const ids = results.map((r) => r.id).join(',');
    const html = '<div class="crc-card crc-stcard"><div class="crc-stcard__head">' + PIN + '<b>Available in stores</b></div>' + rows +
      '<div class="crc-actions"><button type="button" class="crc-btn" data-st-open="' + esc(ids) + '">View store details</button></div></div>';
    window.ChatRich.append(html, { kind: 'stores', record: { summary: 'Store availability (' + results.length + (results.length === 1 ? ' item)' : ' items)') } });
  }

  async function showStoreAvailability(a) {
    if (typeof a === 'string') { try { a = JSON.parse(a); } catch (e) { a = {}; } }
    a = a || {};
    if (a.args && typeof a.args === 'object' && !a.product_ids) a = a.args;
    let ids = a.product_ids || a.productIds || a.product_id || [];
    if (typeof ids === 'string') { try { ids = ids.trim().startsWith('[') ? JSON.parse(ids) : ids.split(/[\s,]+/); } catch (e) { ids = []; } }
    ids = (Array.isArray(ids) ? ids : [ids]).map((x) => (x && typeof x === 'object' ? (x.product_id || x.id) : x)).filter(Boolean).map(String);
    if (!ids.length) return { shown: 0, error: 'NO_PRODUCTS' };
    const r = await open(ids, { message: a.message || '' });
    chatCard(r.results || []);
    return {
      shown: r.shown,
      items: (r.results || []).map((x) => ({
        product_id: x.id, title: x.product && x.product.title, channel: x.data && x.data.channel,
        stores: ((x.data && x.data.stores) || []).map((s) => ({ store_id: s.store_id, name: s.name, quantity: s.quantity, availability: s.availability, open_now: s.open_now, pickup: s.pickup })),
      })),
    };
  }

  document.addEventListener('click', (e) => {
    const b = e.target.closest('[data-stores]');
    if (b) { e.preventDefault(); open([b.dataset.stores]); return; }
    const c = e.target.closest('[data-st-open]');
    if (c) { e.preventDefault(); open(String(c.dataset.stOpen).split(',')); }
  }, true);
  // Escape closes only the store modal (capture phase, so the chat panel / product modal stay open underneath)
  document.addEventListener('keydown', (e) => {
    const m = document.getElementById('cr-store-modal');
    if (e.key === 'Escape' && m && !m.hidden) { e.stopImmediatePropagation(); close(); }
  }, true);

  window.CRStores = { open, close, fetchStores, showStoreAvailability };
  if (window.CRClientTools) window.CRClientTools.show_store_availability = showStoreAvailability;
})();
