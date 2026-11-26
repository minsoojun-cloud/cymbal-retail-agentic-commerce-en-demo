/* Cymbal Retail store app — "Did you forget?" (window.Forgot) + shared feature UI helpers (window.FeatUI).
   Forgot.check(cartIds, recipeId?) -> Promise<items>; Forgot.renderInto(container, items, opts?) renders the block.
   FeatUI is used lazily by recipes.js / detail.js (load order of the feature scripts does not matter). */
(function () {
  'use strict';

  // ------------------------------------------------------------------------------------------- FeatUI (shared)
  const ICONS = {
    close: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19 6.41 17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>',
    back: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z"/></svg>',
    add: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z"/></svg>',
    check: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 16.17 4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/></svg>',
    history: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M13 3a9 9 0 0 0-9 9H1l3.89 3.89.07.14L9 12H6c0-3.87 3.13-7 7-7s7 3.13 7 7-3.13 7-7 7c-1.93 0-3.68-.79-4.94-2.06l-1.42 1.42A8.954 8.954 0 0 0 13 21a9 9 0 0 0 0-18zm-1 5v5l4.28 2.54.72-1.21-3.5-2.08V8H12z"/></svg>',
    mic: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3zm5.3-3c0 3-2.54 5.1-5.3 5.1S6.7 14 6.7 11H5c0 3.41 2.72 6.23 6 6.72V21h2v-3.28c3.28-.48 6-3.3 6-6.72h-1.7z"/></svg>',
    map: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5a2.5 2.5 0 0 1 0-5 2.5 2.5 0 0 1 0 5z"/></svg>',
    bulb: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 21c0 .55.45 1 1 1h4c.55 0 1-.45 1-1v-1H9v1zm3-19C8.14 2 5 5.14 5 9c0 2.38 1.19 4.47 3 5.74V17c0 .55.45 1 1 1h6c.55 0 1-.45 1-1v-2.26c1.81-1.27 3-3.36 3-5.74 0-3.86-3.14-7-7-7z"/></svg>',
  };
  const STATUS = { in_stock: 'In stock', low_stock: 'Low stock', out_of_stock: 'Out of stock', not_carried: 'Not sold here' };
  const IN = ['in_stock', 'low_stock'];

  function esc(s) {
    if (window.SN && window.SN.esc) return window.SN.esc(s);
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }
  function yen(n) { return (Math.round(Number(n) || 0)).toLocaleString('en-US', { style: 'currency', currency: 'JPY' }); }
  function fetchJSON(url, opts) {
    if (window.SN && window.SN.fetchJSON) return window.SN.fetchJSON(url, opts);
    return fetch(url, Object.assign({ credentials: 'same-origin' }, opts || {})).then((r) => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); });
  }
  function postJSON(url, body) {
    return fetchJSON(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  }
  function toast(msg) { if (window.SN && window.SN.toast) window.SN.toast(msg); }
  function storeId() { return (window.SNC && window.SNC.state && window.SNC.state.storeId) || ''; }
  function thumb(src, alt, cls, fallback) {
    return '<div class="' + (cls || 'feat-thumb') + '">' + (src
      ? '<img src="' + esc(src) + '" alt="' + esc(alt || '') + '" loading="lazy" onerror="this.replaceWith(document.createTextNode(\'' + (fallback || '\u{1F6D2}') + '\'))">'
      : (fallback || '\u{1F6D2}')) + '</div>';
  }
  function price(p) {
    if (!p) return '';
    const orig = Number(p.original_price || 0);
    return '<span class="feat-price">' + yen(p.price) + (orig > Number(p.price || 0) ? '<s>' + yen(orig) + '</s>' : '') + '</span>';
  }
  function cur(av) { return (av && av.current) || {}; }
  function pill(av) {
    const c = cur(av);
    if (!c.status) return '';
    const q = c.status === 'low_stock' && c.quantity ? ' (' + c.quantity + ' left)' : '';
    return '<span class="feat-pill feat-pill--' + esc(c.status) + '">' + esc((STATUS[c.status] || c.status) + q) + '</span>';
  }
  function shelfText(sh) {
    if (!sh) return '';
    if (sh.unassigned) return 'Location being updated \u2014 ask staff';
    if (sh.counter || (sh.fixture_id || sh.gondola_id) === 'RX') return 'Pharmacy counter';
    const fid = sh.fixture_id || sh.gondola_id || '';
    if (sh.slot_label) return (sh.fixture_label ? fid + ' ' + sh.fixture_label + ' \u2022 ' : '') + sh.slot_label;
    return sh.label || fid;
  }
  function shelf(av) { const c = cur(av); return IN.indexOf(c.status) >= 0 && c.shelf ? shelfText(c.shelf) : ''; }
  function inCart(pid) { return !!(window.Cart && window.Cart.has && window.Cart.has(pid)); }
  function addBtn(pid, label) {
    const on = inCart(pid);
    return '<button type="button" class="feat-add' + (on ? ' is-added' : '') + '" data-feat-add="' + esc(pid) + '" aria-pressed="' + on +
      '" aria-label="' + esc(on ? 'In cart' : (label || 'Add to cart')) + '" title="' + esc(on ? 'In cart' : 'Add to cart') + '">' + (on ? ICONS.check : ICONS.add) + '</button>';
  }
  function setAddBtn(btn, on) {
    btn.classList.toggle('is-added', on); btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    btn.setAttribute('aria-label', on ? 'In cart' : 'Add to cart'); btn.title = on ? 'In cart' : 'Add to cart';
    btn.innerHTML = on ? ICONS.check : ICONS.add;
  }
  /** Wire [data-feat-add] buttons inside root; lookup(pid) -> {product, availability, role}. */
  function wireAdd(root, lookup) {
    root.querySelectorAll('[data-feat-add]').forEach((b) => b.addEventListener('click', (e) => {
      e.stopPropagation();
      const it = lookup(b.dataset.featAdd);
      if (!it || !it.product || !window.Cart) return;
      const before = inCart(it.product.id);
      if (window.Cart.toggle) window.Cart.toggle({ product: it.product, availability: it.availability || null, role: it.role || '' });
      else if (!before && window.Cart.addMany) window.Cart.addMany([{ product: it.product, availability: it.availability || null }]);
      setAddBtn(b, inCart(it.product.id));
    }));
  }
  function cartIds() {
    try { return JSON.parse(sessionStorage.getItem('sn_cart') || '[]').map((l) => l.product && l.product.id).filter(Boolean); } catch (e) { return []; }
  }

  /** Overlay with header (back/close), body and footer, appended to body. */
  function overlay(id, title, onBack) {
    let el = document.getElementById(id);
    if (!el) {
      el = document.createElement('div');
      el.id = id; el.className = 'feat-ov'; el.hidden = true;
      el.setAttribute('role', 'dialog'); el.setAttribute('aria-modal', 'true'); el.setAttribute('aria-labelledby', id + '-title');
      el.innerHTML = '<div class="feat-ov__panel"><header class="feat-ov__head">' +
        '<button type="button" class="feat-ic" data-feat-back aria-label="Back" title="Back">' + ICONS.back + '</button>' +
        '<h2 id="' + id + '-title"></h2>' +
        '<button type="button" class="feat-ic" data-feat-close aria-label="Close" title="Close">' + ICONS.close + '</button></header>' +
        '<div class="feat-ov__body"></div><footer class="feat-ov__foot"></footer></div>';
      document.body.appendChild(el);
      el.addEventListener('click', (e) => { if (e.target === el) api.close(); });
      el.querySelector('[data-feat-close]').addEventListener('click', () => api.close());
      el.querySelector('[data-feat-back]').addEventListener('click', () => { if (el._onBack) el._onBack(); else api.close(); });
      el.addEventListener('keydown', (e) => { if (e.key === 'Escape') api.close(); });
    }
    const api = {
      el,
      body: el.querySelector('.feat-ov__body'),
      foot: el.querySelector('.feat-ov__foot'),
      title(t) { el.querySelector('h2').textContent = t; },
      back(fn) { el._onBack = fn || null; el.querySelector('[data-feat-back]').hidden = !fn; },
      show() {
        if (el.hidden) { el.hidden = false; document.body.classList.add('feat-lock'); }
        setTimeout(() => { const f = el.querySelector('[data-feat-close]'); if (f && !el.contains(document.activeElement)) f.focus(); }, 30);
      },
      close() { el.hidden = true; if (!document.querySelector('.feat-ov:not([hidden])')) document.body.classList.remove('feat-lock'); if (el._onClose) el._onClose(); },
    };
    if (title !== undefined) api.title(title || '');
    if (onBack !== undefined) api.back(onBack);
    return api;
  }

  window.FeatUI = { ICONS, STATUS, IN, esc, yen, fetchJSON, postJSON, toast, storeId, thumb, price, pill, shelf, shelfText, cur,
    inCart, addBtn, setAddBtn, wireAdd, cartIds, overlay };

  // ------------------------------------------------------------------------------------------- Forgot
  function check(ids, recipeId) {
    return postJSON('/api/forgot-check', { store_id: storeId(), cart_ids: ids || cartIds(), recipe_id: recipeId || '' })
      .then((r) => r.items || []).catch(() => []);
  }

  function renderInto(container, items, opts) {
    if (!container) return;
    opts = opts || {};
    if (!items || !items.length) { container.innerHTML = opts.emptyText ? '<p class="feat-empty">' + esc(opts.emptyText) + '</p>' : ''; return; }
    const byId = {};
    items.forEach((it) => { byId[it.product.id] = it; });
    container.innerHTML = '<section class="feat-forgot" aria-label="Did you forget?"><h3>' + ICONS.bulb.replace('<svg', '<svg width="18" height="18" style="fill:#b06000"') +
      esc(opts.title || 'Did you forget?') + '</h3>' +
      items.map((it) => {
        const p = it.product;
        const sh = shelf(it.availability);
        return '<div class="feat-ing">' + thumb(p.image, p.title) +
          '<div class="feat-ing__main"><span class="feat-ing__prod" data-feat-detail="' + esc(p.id) + '">' + esc(p.title) + '</span>' +
          '<span class="feat-forgot__reason">' + esc(it.reason || '') + '</span>' +
          '<span class="feat-ing__meta">' + price(p) + pill(it.availability) + (sh ? '<span>' + esc(sh) + '</span>' : '') + '</span></div>' +
          addBtn(p.id) + '</div>';
      }).join('') +
      (items.length > 1 ? '<div style="display:flex;justify-content:flex-end;margin-top:8px"><button type="button" class="feat-btn feat-btn--tonal feat-btn--sm" data-feat-addall>Add all ' + items.length + '</button></div>' : '') +
      '</section>';
    wireAdd(container, (pid) => byId[pid]);
    container.querySelectorAll('[data-feat-detail]').forEach((el) => el.addEventListener('click', () => { if (window.Detail) window.Detail.open(el.dataset.featDetail); }));
    const all = container.querySelector('[data-feat-addall]');
    if (all) all.addEventListener('click', () => {
      if (!window.Cart) return;
      const chOf = (p) => (window.SN && window.SN.channelOf ? window.SN.channelOf(p) : p.channel);
      const take = items.filter((it) => chOf(it.product) !== 'online_only');
      const skipped = items.length - take.length;
      window.Cart.addMany(take.map((it) => ({ product: it.product, availability: it.availability || null, role: it.kind === 'recipe' ? 'Recipe' : '' })));
      container.querySelectorAll('[data-feat-add]').forEach((b) => setAddBtn(b, inCart(b.dataset.featAdd)));
      toast('Added ' + take.length + ' items to your cart' + (skipped ? ' \u2014 ' + skipped + ' online-only skipped' : ''));
      if (opts.onAdd) opts.onAdd(items);
    });
  }

  window.Forgot = { check, renderInto };
})();
