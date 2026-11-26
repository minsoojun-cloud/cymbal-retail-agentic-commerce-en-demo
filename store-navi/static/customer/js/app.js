/* Store Navi customer app shell: state, hash router, header (store chip, demo login, staff cross link), sheets. */
(function () {
  'use strict';
  const { CFG, esc, fetchJSON, postJSON, crossLink, toast } = window.SN;
  const SS = window.sessionStorage;

  const state = {
    customer: null,           // {customer_id, name, avatar}
    stores: [], nearestId: null, coords: null, geoSource: 'none', simKey: '',
    storeId: SS.getItem('sn_store') || '', inStore: false,
    view: 'home', homeCache: {},
  };

  function save() {
    SS.setItem('sn_store', state.storeId || '');
    SS.setItem('sn_geo', JSON.stringify({ coords: state.coords, src: state.geoSource, sim: state.simKey }));
  }
  try {
    const g = JSON.parse(SS.getItem('sn_geo') || 'null');
    if (g && g.coords) { state.coords = g.coords; state.geoSource = g.src; state.simKey = g.sim || ''; }
  } catch (e) { /* ignore */ }

  function storeById(id) { return state.stores.find((s) => s.store_id === id) || null; }

  async function loadStores() {
    const c = state.coords;
    const q = c ? ('?lat=' + c.lat + '&lng=' + c.lng) : '';
    const r = await fetchJSON('/api/stores' + q);
    state.stores = r.stores || [];
    state.nearestId = r.nearest_id;
    const cur = storeById(state.storeId);
    state.inStore = !!(cur && cur.in_store);
    return r;
  }

  // ---------------------------------------------------------------- header
  function renderHeader() {
    const s = storeById(state.storeId);
    const chip = document.getElementById('hdr-store');
    const back = document.getElementById('hdr-back');
    const inStoreView = state.view !== 'home' && !!s;
    chip.hidden = !inStoreView;
    document.getElementById('hdr').classList.toggle('hdr--store', inStoreView);   // compact logo mark → room for the store chip + cross links
    back.hidden = state.view === 'home';
    document.getElementById('hdr-logo').hidden = false;
    if (s) {
      document.getElementById('hdr-store-name').textContent = s.short_name || s.name;
      document.getElementById('hdr-store-dot').classList.toggle('is-closed', !s.open_now);
      chip.title = s.name + (s.open_now ? ' (Open now)' : ' (Closed)');
    }
    const av = document.getElementById('hdr-avatar');
    if (state.customer) {
      av.textContent = state.customer.avatar || '🙂';
      document.getElementById('hdr-login').title = state.customer.name + ' (switch demo user)';
    }
    document.getElementById('fab-ai').hidden = true;   // replaced by the tab bar / navigation rail "Ask AI" tab
  }

  function initHeader() {
    // CONTRACT v8: icon-only cross link to the staff app (tooltip "Staff App"), only when STAFF_URL is set
    crossLink(document.getElementById('hdr-actions'), CFG.staffUrl, 'Staff App', 'staff', 'sn-staff-link');
    const link = document.getElementById('sn-staff-link');
    if (link) document.getElementById('hdr-actions').insertBefore(link, document.getElementById('hdr-login'));
    // CONTRACT v14 cross links: Online Store (ONLINE_STORE_URL) and Data Enrichment (ENRICH_URL)
    [[CFG.onlineStoreUrl, 'Online Store', 'online', 'sn-online-link'], [CFG.enrichUrl, 'Data Enrichment', 'enrich', 'sn-enrich-link']].forEach(([u, t, ic, id]) => {
      const a = crossLink(document.getElementById('hdr-actions'), u, t, ic, id);
      if (a) { a.target = '_blank'; a.rel = 'noopener'; document.getElementById('hdr-actions').insertBefore(a, document.getElementById('hdr-login')); }
    });
    document.getElementById('hdr-back').addEventListener('click', () => {
      if (state.view === 'results') go('#/store/' + state.storeId);
      else go('#/');
    });
    document.getElementById('hdr-store').addEventListener('click', () => go('#/store/' + state.storeId));
    document.getElementById('hdr-login').addEventListener('click', openLogin);
    document.getElementById('fab-ai').addEventListener('click', () => window.Chat.open());
    document.getElementById('scrim').addEventListener('click', closeSheets);
  }

  // ---------------------------------------------------------------- sheets
  function openSheet(id) {
    closeSheets(true);
    document.getElementById('scrim').hidden = false;
    document.getElementById(id).hidden = false;
  }
  function closeSheets(keepScrim) {
    ['sheet-login', 'sheet-geo', 'sheet-cart', 'chat'].forEach((id) => {
      const el = document.getElementById(id); if (el) el.hidden = true;
    });
    if (!keepScrim) document.getElementById('scrim').hidden = true;
  }

  function openLogin() {
    const list = document.getElementById('login-list');
    list.innerHTML = (CFG.customers || []).map((c) => {
      const on = state.customer && state.customer.customer_id === c.customer_id;
      return '<button type="button" class="login-opt' + (on ? ' is-on' : '') + '" data-cid="' + esc(c.customer_id) + '">' +
        '<span class="login-opt__av">' + esc(c.avatar || '🙂') + '</span>' +
        '<span><span class="login-opt__name">' + esc(c.name) + '</span>' +
        '<span class="login-opt__meta">' + esc([c.age ? 'Age ' + c.age : '', (c.concerns || []).join(', ')].filter(Boolean).join(' • ')) + '</span></span>' +
        (on ? '<svg class="login-opt__check" viewBox="0 0 24 24"><path d="M9 16.17 4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/></svg>' : '<span></span>') + '</button>';
    }).join('');
    list.querySelectorAll('[data-cid]').forEach((b) => b.addEventListener('click', async () => {
      const r = await postJSON('/api/login', { customer_id: b.dataset.cid });
      state.customer = r.customer;
      state.homeCache = {};
      closeSheets();
      toast('Signed in as ' + r.customer.name);
      window.Chat && window.Chat.reset();
      route();
    }));
    openSheet('sheet-login');
  }

  // ---------------------------------------------------------------- router
  function go(hash) { if (location.hash === hash) route(); else location.hash = hash; }

  async function route() {
    const h = location.hash || '#/';
    const view = document.getElementById('view');
    let m;
    if ((m = h.match(/^#\/store\/([A-Za-z0-9_-]+)\/search\?(.*)$/))) {
      state.storeId = m[1]; state.view = 'results'; save();
      const p = new URLSearchParams(m[2]);
      renderHeader();
      await window.Search.render(view, p.get('q') || '', p.get('only') === '1');
    } else if ((m = h.match(/^#\/store\/([A-Za-z0-9_-]+)/))) {
      state.storeId = m[1]; state.view = 'store'; save();
      renderHeader();
      await window.Store.render(view);
    } else {
      state.view = 'home';
      renderHeader();
      await window.Home.render(view);
    }
    renderHeader();
    window.scrollTo({ top: 0 });
    syncPanes();
  }

  // Navigation (browser Back, header links) closes full-screen overlays so they never block the new view.
  function closeOverlays() {
    try { if (window.Detail && window.Detail.close) window.Detail.close(); } catch (e) { /* ignore */ }
    try { if (window.Recipes && window.Recipes.close) window.Recipes.close(); } catch (e) { /* ignore */ }
    if (window.FloorMap && !document.getElementById('mapv').hidden) window.FloorMap.close();
    ['sheet-login', 'sheet-geo', 'sheet-cart'].forEach((id) => { const el = document.getElementById(id); if (el) el.hidden = true; });
    { const c = document.getElementById('chat'); if (c) c.hidden = true; }
    document.getElementById('scrim').hidden = true;
  }

  // ---------------------------------------------------------------- tablet panes
  // phone <768: single column (unchanged). tablet portrait >=768: list/detail/chat left + floor map right.
  // tablet landscape >=1024: list | floor map | chat (persistent).
  function layoutMode() { return window.matchMedia('(min-width: 1024px)').matches ? 'wide' : window.matchMedia('(min-width: 768px)').matches ? 'tablet' : 'phone'; }
  function syncPanes() { document.body.dataset.layout = layoutMode(); syncTabs(); }

  // ---------------------------------------------------------------- tab bar (phone) / navigation rail (tablet+)
  function syncTabs() {
    const v = state.view;
    document.querySelectorAll('#tabbar [data-tab]').forEach((b) => {
      const on = (b.dataset.tab === 'home' && v === 'home') || (b.dataset.tab === 'store' && v === 'store') || (b.dataset.tab === 'search' && v === 'results');
      b.classList.toggle('is-active', on);
      b.setAttribute('aria-current', on ? 'page' : 'false');
    });
    const n = window.Cart ? window.Cart.count() : 0;
    const badge = document.getElementById('tab-cart-badge');
    if (badge) { badge.textContent = n; badge.hidden = !n; }
  }
  function initTabs() {
    const sid = () => state.storeId || state.nearestId || ((state.stores[0] || {}).store_id) || '';
    const on = (id, fn) => { const el = document.querySelector('#tabbar [data-tab="' + id + '"]'); if (el) el.addEventListener('click', fn); };
    on('home', () => go('#/'));
    on('store', () => go('#/store/' + sid()));
    on('search', () => {
      if (state.view === 'store' || state.view === 'results') { const i = document.getElementById('sb-input'); if (i) { i.focus(); i.scrollIntoView({ block: 'center' }); } }
      else { go('#/store/' + sid()); setTimeout(() => { const i = document.getElementById('sb-input'); if (i) i.focus(); }, 350); }
    });
    on('ai', () => window.Chat.open());
    on('recipes', () => { if (window.Recipes) window.Recipes.open(''); });
    on('cart', () => window.Cart && window.Cart.open());
  }

  async function boot() {
    initHeader();
    try {
      const me = await fetchJSON('/api/me');
      state.customer = me.customer;
    } catch (e) { /* ignore */ }
    try { await loadStores(); } catch (e) { toast('Could not load store information'); }
    window.Cart && window.Cart.init();
    window.Chat && window.Chat.init();
    window.FloorMap && window.FloorMap.init();
    window.addEventListener('hashchange', () => { closeOverlays(); route(); });
    window.matchMedia('(min-width: 768px)').addEventListener('change', syncPanes);
    window.matchMedia('(min-width: 1024px)').addEventListener('change', syncPanes);
    initTabs();
    window.addEventListener('sn-cart-change', syncTabs);
    await route();
    if (!state.coords) window.Geo.auto();
  }

  window.SNC = { state, boot, go, route, save, loadStores, storeById, renderHeader, openSheet, closeSheets, layoutMode, syncPanes, closeOverlays };
})();
