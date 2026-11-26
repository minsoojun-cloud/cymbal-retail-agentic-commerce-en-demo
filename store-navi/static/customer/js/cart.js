/* "Cart" sheet: checkout at register list (checkboxes, shelf) / reserve for pickup at a nearby store / online delivery (mock order id).
   abuse_control items: max qty 1; Pharmacist-only (Class 1): pharmacist note. */
(function () {
  'use strict';
  const { esc, yen, km, drugBadges, img, toast, ICON_INFO } = window.SN;
  const KEY = 'sn_cart';
  const IN = ['in_stock', 'low_stock'];
  let lines = [];
  let mode = 'store';
  let result = null;

  function load() { try { lines = JSON.parse(sessionStorage.getItem(KEY) || '[]'); } catch (e) { lines = []; } }
  function save() { sessionStorage.setItem(KEY, JSON.stringify(lines)); updateBadge(); }
  function maxQty(p) { return p && p.abuse_control ? 1 : 10; }
  function has(pid) { return lines.some((l) => l.product.id === pid); }
  function add(item) {
    const p = item.product;
    if (!p || has(p.id)) return;
    lines.push({ product: p, availability: item.availability || null, role: item.role || '', qty: 1, done: false });
  }
  function toggle(item) {
    const pid = item.product.id;
    if (has(pid)) { lines = lines.filter((l) => l.product.id !== pid); save(); toast('Removed from cart'); return false; }
    add(item); save(); toast('Added to cart');
    if (!item.availability && window.SNC) {  // offers: fetch store availability (shelf) for the in-store list
      window.SN.fetchJSON('/api/products/' + encodeURIComponent(pid) + '/availability?store_id=' + encodeURIComponent(window.SNC.state.storeId || ''))
        .then((av) => { const l = lines.find((x) => x.product.id === pid); if (l) { l.availability = av; save(); } }).catch(() => {});
    }
    return true;
  }
  function addMany(items) { items.forEach(add); save(); }
  function count() { return lines.reduce((a, l) => a + l.qty, 0); }
  function updateBadge() {
    let pill = document.getElementById('cart-pill');
    const n = count();
    const S = window.SNC && window.SNC.state;
    const show = n > 0 && S && S.view !== 'home';
    if (!pill) {
      pill = document.createElement('button');
      pill.type = 'button'; pill.id = 'cart-pill'; pill.className = 'cart-pill';
      pill.addEventListener('click', () => open());
      document.getElementById('app').appendChild(pill);
    }
    pill.hidden = true;   // redesign: cart lives in the tab bar / rail (badge)
    void show;
    window.dispatchEvent(new Event('sn-cart-change'));
    pill.innerHTML = '<svg viewBox="0 0 24 24"><path d="M7 18c-1.1 0-1.99.9-1.99 2S5.9 22 7 22s2-.9 2-2-.9-2-2-2zM1 2v2h2l3.6 7.59-1.35 2.45c-.16.28-.25.61-.25.96 0 1.1.9 2 2 2h12v-2H7.42a.25.25 0 0 1-.25-.25l.03-.12.9-1.63h7.45c.75 0 1.41-.41 1.75-1.03l3.58-6.49A1 1 0 0 0 20 4H5.21l-.94-2H1zm16 16c-1.1 0-1.99.9-1.99 2s.89 2 1.99 2 2-.9 2-2-.9-2-2-2z"/></svg>Cart<b>' + n + '</b>';
  }

  const total = () => lines.reduce((a, l) => a + (excluded(l) ? 0 : (l.product.price || 0) * l.qty), 0);
  const pad = (n, w) => String(n).padStart(w, '0');
  function today() { const d = new Date(); return d.getFullYear() + pad(d.getMonth() + 1, 2) + pad(d.getDate(), 2); }
  function rid(prefix) { return prefix + '-' + today() + '-' + pad(Math.floor(1000 + Math.random() * 9000), 4); }

  // CONTRACT v14 channel rules: store_only → not deliverable; online_only → not on store shelves
  const chanOf = (p) => window.SN.channelOf(p);
  function excluded(l) { const c = chanOf(l.product); return (mode === 'online' && c === 'store_only') || (mode !== 'online' && c === 'online_only'); }
  function chanNote(p) {
    const c = chanOf(p);
    if (mode === 'online' && c === 'store_only') return '<span class="sn-chan sn-chan--store">In-store only — not delivered</span>';
    if (mode !== 'online' && c === 'online_only') return '<span class="sn-chan sn-chan--online">Online only</span>' + window.SN.buyOnlineLink(p.id, 'Buy online');
    return '';
  }
  function lineHTML(l, i, withCheck) {
    const p = l.product;
    const cur = (l.availability || {}).current || {};
    const where = IN.indexOf(cur.status) >= 0 ? (cur.shelf ? window.Search.shelfText(cur.shelf) : 'In stock') : (window.SN.STATUS_EN[cur.status] || '');
    const mq = maxQty(p);
    return '<div class="cart-line' + (l.done ? ' is-done' : '') + '">' +
      (withCheck ? '<input type="checkbox" class="chk" data-done="' + i + '"' + (l.done ? ' checked' : '') + ' aria-label="In basket">' : '<span></span>') +
      img(p.image, p.title) + '<div><div class="cart-line__title">' + esc(p.title) + '</div><div class="cart-line__meta">' + drugBadges(p) + chanNote(p) +
      '<span>' + yen(p.price) + '</span>' + (mode === 'store' && where ? '<span> • ' + esc(where) + '</span>' : '') + '</div></div>' +
      '<div class="qty"><button type="button" data-dec="' + i + '"' + (l.qty <= 1 ? '' : '') + ' aria-label="Decrease">−</button><span>' + l.qty + '</span>' +
      '<button type="button" data-inc="' + i + '"' + (l.qty >= mq ? ' disabled title="Limit ' + mq + ' per customer"' : '') + ' aria-label="Increase">+</button></div></div>';
  }

  function notes() {
    const out = [];
    if (lines.some((l) => l.product.abuse_control)) out.push(['warn', window.SN.ABUSE_NOTE]);
    if (lines.some((l) => l.product.drug_class === window.SN.CLASS1)) out.push(['warn', mode === 'online' ? 'Pharmacist-only (Class 1): ships after a pharmacist reviews your order.' : 'Pharmacist-only (Class 1): a pharmacist must explain before purchase. Please ask at the pharmacy counter.']);
    return out.map(([lv, t]) => '<div class="sn-notice sn-notice--' + lv + '" style="margin-top:8px">' + ICON_INFO + '<span>' + esc(t) + '</span></div>').join('');
  }

  function nearbyOptions() {
    const S = window.SNC.state;
    const opts = {};
    lines.forEach((l) => ((l.availability || {}).nearby || []).forEach((n) => {
      const o = opts[n.store_id] || (opts[n.store_id] = { store_id: n.store_id, short_name: n.short_name, distance_m: n.distance_m, ok: 0 });
      if (IN.indexOf(n.status) >= 0) o.ok += 1;
    }));
    return Object.values(opts).filter((o) => o.store_id !== S.storeId).sort((a, b) => b.ok - a.ok || a.distance_m - b.distance_m);
  }

  function render() {
    const root = document.getElementById('cart');
    const S = window.SNC.state;
    const store = window.SNC.storeById(S.storeId) || {};
    if (result) {
      root.innerHTML = '<div class="done-card"><div class="done-card__ic"><svg viewBox="0 0 24 24"><path d="M9 16.17 4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/></svg></div>' +
        '<h3>' + esc(result.title) + '</h3><div class="oid" id="order-id">' + esc(result.id) + '</div><p style="color:#5f6368;font-size:13px">' + esc(result.detail) + '</p>' +
        notes() + '<div class="cart-actions"><button type="button" class="sn-btn sn-btn--primary" id="cart-finish">Done</button></div></div>';
      root.querySelector('#cart-finish').addEventListener('click', () => { result = null; window.SNC.closeSheets(); });
      return;
    }
    if (!lines.length) {
      root.innerHTML = '<h2 class="cart__title">Cart</h2><p class="cart-empty">Your cart is empty.<br>Add items with "+" in search results or from AI suggestions.</p>';
      return;
    }
    const sub = total();
    const ship = sub >= 3000 ? 0 : 330;
    let body = '';
    if (mode === 'store') {
      const doneN = lines.filter((l) => l.done).length;
      body = '<p class="sheet__sub">' + 'Checkout at register at ' + esc(store.short_name || '') + '. Check off items as you put them in your basket (' + doneN + '/' + lines.length + ').</p>' +
        lines.map((l, i) => lineHTML(l, i, true)).join('') +
        '<div class="cart-sum"><span>Total (tax incl.)</span><b>' + yen(sub) + '</b></div>' + notes() +
        '<div class="cart-actions"><button type="button" class="sn-btn sn-btn--primary" id="cart-route">Find all on the map</button></div>' +
        '<div class="cart-forgot" id="cart-forgot"></div>';
    } else if (mode === 'pickup') {
      const opts = nearbyOptions();
      const sel = (window.Cart._pickup && opts.find((o) => o.store_id === window.Cart._pickup)) || opts[0];
      body = '<p class="sheet__sub">Reserve for pickup at a nearby store (ready as soon as 2 hours from now).</p>' +
        '<div class="store-pick">' + opts.map((o) => '<label class="' + (sel && sel.store_id === o.store_id ? 'is-on' : '') + '"><input type="radio" name="pick" value="' + esc(o.store_id) + '"' + (sel && sel.store_id === o.store_id ? ' checked' : '') + '>' +
          '<span>' + esc(o.short_name) + '<br><small>In stock: ' + o.ok + '/' + lines.length + ' items</small></span><small>about ' + esc(km(o.distance_m)) + '</small></label>').join('') + '</div>' +
        lines.map((l, i) => lineHTML(l, i, false)).join('') +
        '<div class="cart-sum"><span>Total (tax incl., pay at the store)</span><b>' + yen(sub) + '</b></div>' + notes() +
        '<div class="cart-actions"><button type="button" class="sn-btn sn-btn--primary" id="cart-pickup"' + (sel ? '' : ' disabled') + '>Reserve for pickup' + (sel ? ' at ' + esc(sel.short_name) : '') + '</button></div>';
    } else {
      body = '<p class="sheet__sub">Online delivery from our online store (arrives tomorrow, 2-5 PM).</p>' +
        lines.map((l, i) => lineHTML(l, i, false)).join('') +
        '<div class="cart-sum"><span>Subtotal</span><span>' + yen(sub) + '</span><span>Shipping' + (ship ? ' (free on orders of ' + yen(3000) + '+)' : '') + '</span><span>' + yen(ship) + '</span>' +
        '<span>Payment (Cymbal Pay VISA **** 4242)</span><b>' + yen(sub + ship) + '</b></div>' + notes() +
        '<div class="cart-actions"><button type="button" class="sn-btn sn-btn--primary" id="cart-online">Place order</button></div>';
    }
    root.innerHTML = '<h2 class="cart__title">Cart</h2><div class="seg" role="tablist">' +
      [['store', 'Checkout at register'], ['pickup', 'Reserve for pickup'], ['online', 'Online delivery']].map(([k, t]) =>
        '<button type="button" role="tab" data-mode="' + k + '" class="' + (mode === k ? 'is-on' : '') + '">' + t + '</button>').join('') + '</div>' + body;
    root.querySelectorAll('[data-mode]').forEach((b) => b.addEventListener('click', () => { mode = b.dataset.mode; render(); }));
    root.querySelectorAll('[data-done]').forEach((c) => c.addEventListener('change', () => { lines[+c.dataset.done].done = c.checked; save(); render(); }));
    root.querySelectorAll('[data-inc]').forEach((b) => b.addEventListener('click', () => {
      const l = lines[+b.dataset.inc];
      if (l.qty >= maxQty(l.product)) { toast('Limit ' + maxQty(l.product) + ' per customer for this item'); return; }
      l.qty += 1; save(); render();
    }));
    root.querySelectorAll('[data-dec]').forEach((b) => b.addEventListener('click', () => {
      const i = +b.dataset.dec;
      if (lines[i].qty <= 1) lines.splice(i, 1); else lines[i].qty -= 1;
      save(); render();
    }));
    root.querySelectorAll('input[name=pick]').forEach((r) => r.addEventListener('change', () => { window.Cart._pickup = r.value; render(); }));
    const fg = root.querySelector('#cart-forgot');
    if (fg && window.Forgot) {   // "Did you forget?" (purchase-history replenishment + recipe ingredients vs cart)
      window.Forgot.check(lines.map((l) => l.product.id)).then((items) => {
        if (document.body.contains(fg)) window.Forgot.renderInto(fg, items, { title: 'Did you forget?', onAdd: () => setTimeout(render, 50) });
      }).catch(() => {});
    }
    const rt = root.querySelector('#cart-route');
    if (rt) rt.addEventListener('click', () => {
      window.SNC.closeSheets();
      window.FloorMap.open({ storeId: S.storeId, items: lines.filter((l) => IN.indexOf(((l.availability || {}).current || {}).status) >= 0) });
    });
    const pk = root.querySelector('#cart-pickup');
    if (pk) pk.addEventListener('click', () => {
      const o = nearbyOptions().find((x) => x.store_id === (window.Cart._pickup || (nearbyOptions()[0] || {}).store_id));
      result = { title: 'Your pickup is reserved', id: rid('RS'), detail: 'Pick up at the store' + (o ? ' (' + o.short_name + ')' : '') + ' today from ' + pickupTime() + '. Show this number at the counter.' };
      render();
    });
    const on = root.querySelector('#cart-online');
    if (on) on.addEventListener('click', () => {
      result = { title: 'Thank you for your order', id: rid('CR'), detail: 'Arriving tomorrow, 2-5 PM. Paid ' + yen(sub + ship) + ' (Cymbal Pay).' };
      render();
    });
  }
  function pickupTime() { const d = new Date(Date.now() + 2 * 3600 * 1000); return d.getHours() + ':' + pad(d.getMinutes(), 2); }

  function open(items, startMode) {
    if (items && items.length) addMany(items);
    if (startMode) mode = startMode;
    result = null;
    render();
    window.SNC.openSheet('sheet-cart');
  }
  function init() { load(); updateBadge(); window.addEventListener('hashchange', () => setTimeout(updateBadge, 50)); }

  window.Cart = { init, has, toggle, addMany, open, count, updateBadge, _pickup: null };
})();
