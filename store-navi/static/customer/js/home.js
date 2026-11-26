/* Home: nearest store card (geolocation / simulation), coupons, sale info, other stores. */
(function () {
  'use strict';
  const { esc, yen, km, fetchJSON, statusPill, img } = window.SN;
  const I = {
    pin: '<svg viewBox="0 0 24 24"><path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5a2.5 2.5 0 0 1 0-5 2.5 2.5 0 0 1 0 5z"/></svg>',
    clock: '<svg viewBox="0 0 24 24"><path d="M11.99 2C6.47 2 2 6.48 2 12s4.47 10 9.99 10C17.52 22 22 17.52 22 12S17.52 2 11.99 2zM12 20c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8zm.5-13H11v6l5.25 3.15.75-1.23-4.5-2.67z"/></svg>',
    rx: '<svg viewBox="0 0 24 24"><path d="M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm-2 11h-3v3h-4v-3H7v-4h3V7h4v3h3v4z"/></svg>',
    tel: '<svg viewBox="0 0 24 24"><path d="M6.62 10.79c1.44 2.83 3.76 5.14 6.59 6.59l2.2-2.2c.27-.27.67-.36 1.02-.24 1.12.37 2.33.57 3.57.57.55 0 1 .45 1 1V20c0 .55-.45 1-1 1-9.39 0-17-7.61-17-17 0-.55.45-1 1-1h3.5c.55 0 1 .45 1 1 0 1.25.2 2.45.57 3.57.11.35.03.74-.25 1.02l-2.2 2.2z"/></svg>',
    store: '<svg viewBox="0 0 24 24"><path d="M20 4H4v2h16V4zm1 10v-2l-1-5H4l-1 5v2h1v6h10v-6h4v6h2v-6h1zm-9 4H6v-4h6v4z"/></svg>',
    walk: '<svg viewBox="0 0 24 24"><path d="M13.5 5.5c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zM9.8 8.9 7 23h2.1l1.8-8 2.1 2v6h2v-7.5l-2.1-2 .6-3C14.8 12 16.8 13 19 13v-2c-1.9 0-3.5-1-4.3-2.4l-1-1.6c-.4-.6-1-1-1.7-1-.3 0-.5.1-.8.1L6 8.3V13h2V9.6l1.8-.7"/></svg>',
    gps: '<svg viewBox="0 0 24 24"><path d="M12 8c-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4-1.79-4-4-4zm8.94 3A8.994 8.994 0 0 0 13 3.06V1h-2v2.06A8.994 8.994 0 0 0 3.06 11H1v2h2.06A8.994 8.994 0 0 0 11 20.94V23h2v-2.06A8.994 8.994 0 0 0 20.94 13H23v-2h-2.06zM12 19c-3.87 0-7-3.13-7-7s3.13-7 7-7 7 3.13 7 7-3.13 7-7 7z"/></svg>',
    check: '<svg viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/></svg>',
  };

  function couponVal(c) {
    if (c.discount_type === 'yen') return { v: yen(c.discount_value), u: 'off' };
    if (c.discount_type === 'percent') return { v: c.discount_value + '%', u: 'OFF' };
    return { v: c.discount_value + 'x', u: 'points' };
  }
  function couponHTML(c) {
    const val = couponVal(c);
    return '<button type="button" class="coupon' + (c.personal ? ' is-personal' : '') + '" style="--c:' + esc(c.color || '#1a73e8') + '" data-coupon="' + esc(c.coupon_id) + '">' +
      '<span class="coupon__stub"><span class="coupon__val">' + esc(val.v) + '</span><span class="coupon__unit">' + esc(val.u) + '</span></span>' +
      '<span class="coupon__body"><span class="coupon__badge">' + esc(c.badge || '') + '</span>' +
      '<span class="coupon__title">' + esc(c.title) + '</span>' +
      '<span class="coupon__meta">' + esc(c.reason || c.description || '') + (c.valid_until ? ' • Valid until ' + esc(new Date(String(c.valid_until).slice(0, 10) + 'T00:00:00+09:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'Asia/Tokyo' })) : '') + '</span></span></button>';
  }
  function offerHTML(o) {
    const p = o.product || {};
    return '<div class="offer" role="button" tabindex="0" data-pid="' + esc(p.id) + '" data-title="' + esc(p.title) + '">' +
      '<span class="offer__badge">' + esc(o.badge || 'Deal') + '</span>' + img(p.image, p.title) +
      window.SN.cartBtn(p.id, window.Cart && window.Cart.has(p.id)) +
      '<span class="offer__title">' + esc(p.title) + '</span>' +
      '<span class="offer__price"><b>' + yen(o.sale_price) + '</b>' + (o.original_price > o.sale_price ? '<s>' + yen(o.original_price) + '</s>' : '') + '</span>' +
      (o.reason ? '<span class="offer__reason">' + esc(o.reason) + '</span>' : '<span class="offer__stock">' + esc(o.theme || '') + '</span>') + '</div>';
  }
  function hoursLine(s) {
    const h = s.hours || {};
    return window.SN.hours12(h.open, h.close);
  }
  function pharmacyLine(s) {
    if (!s.pharmacy) return '<span class="muted">No pharmacy counter</span>';
    const p = s.pharmacy;
    return 'Pharmacy counter ' + esc(window.SN.hours12(p.open, p.close)) + (p.closed ? '<span class="muted"> (closed ' + esc(p.closed) + ')</span>' : '');
  }
  function openPill(s) { return '<span class="sn-pill sn-pill--' + (s.open_now ? 'open">Open now' : 'closed">Closed') + '</span>'; }

  async function render(view) {
    const S = window.SNC.state;
    const nearest = window.SNC.storeById(S.nearestId) || window.SNC.storeById(S.storeId) || S.stores[0];
    const g = window.Geo.label();
    if (!nearest) { view.innerHTML = '<p class="res-empty">Could not load store information.</p>'; return; }
    const others = S.stores.filter((s) => s.store_id !== nearest.store_id);
    view.innerHTML =
      '<div class="geo-bar"><span class="geo-bar__state">' + I.pin + '<b>' + esc(g.text) + '</b>' + (g.sim ? '<span class="sim-badge">DEMO</span>' : '') + '</span>' +
      '<button type="button" class="sn-chip" id="geo-open">' + I.gps + 'Location simulation</button></div>' +
      (nearest.in_store ? '<div class="instore-banner">' + I.check + '<span>' + 'You are inside ' + esc(nearest.short_name) + '. We can guide you to any product.</span></div>' : '') +
      '<article class="store-hero" id="store-hero" data-store="' + esc(nearest.store_id) + '">' +
      '<div class="store-hero__map"><span class="store-hero__you"></span><span class="store-hero__pin"></span>' +
      (nearest.distance_m != null ? '<span class="store-hero__dist">' + I.walk.replace('<svg', '<svg width="12" height="12"') + ' ' + km(nearest.distance_m) + '</span>' : '') + '</div>' +
      '<div class="store-hero__body"><span class="eyebrow">' + I.pin + (S.coords ? 'Closest store to you' : 'Recommended store') + '</span>' +
      '<h1 class="store-hero__name">' + esc(nearest.name) + '</h1>' +
      '<p class="store-hero__tag">' + esc(nearest.tagline || '') + '</p>' +
      '<div class="store-hero__badges">' + openPill(nearest) + (nearest.pharmacy ? '<span class="sn-pill sn-pill--' + (nearest.pharmacy_open_now ? 'open">Pharmacy open' : 'closed">Pharmacy closed') + '</span>' : '') +
      (nearest.in_store ? '<span class="sn-pill sn-pill--online">In store</span>' : '') + '</div>' +
      '<div class="facts">' + I.clock + '<span>' + esc(hoursLine(nearest)) + '</span>' + I.rx + '<span>' + pharmacyLine(nearest) + '</span>' +
      I.tel + '<span><a href="tel:' + esc(String(nearest.phone || '').replace(/[^0-9+]/g, '')) + '" id="store-tel">' + esc(nearest.phone || '') + '</a></span>' +
      I.pin + '<span class="muted">' + esc(nearest.address || '') + '</span></div>' +
      '<div class="store-hero__cta"><button type="button" class="sn-btn sn-btn--primary" id="enter-store">Enter this store</button>' +
      '<a class="sn-btn sn-btn--outline" href="tel:' + esc(String(nearest.phone || '').replace(/[^0-9+]/g, '')) + '" aria-label="Call">' + I.tel + 'Call</a></div></div></article>' +
      '<section class="sec"><div class="sec__head"><h2 class="sec__title">Coupons</h2></div><div class="hscroll" id="home-coupons">' +
      '<div class="coupon sn-skel"></div><div class="coupon sn-skel"></div></div></section>' +
      '<section class="sec"><div class="sec__head"><h2 class="sec__title">Deals<small>' + esc(nearest.short_name) + '</small></h2></div><div class="hscroll" id="home-offers">' +
      '<div class="offer sn-skel" style="height:220px"></div><div class="offer sn-skel" style="height:220px"></div><div class="offer sn-skel" style="height:220px"></div></div></section>' +
      '<section class="sec"><div class="sec__head"><h2 class="sec__title">Other stores</h2></div><div class="store-list">' +
      others.map((s) => '<button type="button" class="store-row" data-store="' + esc(s.store_id) + '"><span class="store-row__icon">' + I.store + '</span>' +
        '<span><span class="store-row__name">' + esc(s.short_name || s.name) + '</span><span class="store-row__meta">' + openPill(s) + '<span>' + esc(hoursLine(s)) + '</span></span></span>' +
        '<span class="store-row__dist">' + (s.distance_m != null ? km(s.distance_m) : '') + '<small>' + esc(s.phone || '') + '</small></span></button>').join('') +
      '</div></section>';
    view.querySelector('#geo-open').addEventListener('click', window.Geo.open);
    view.querySelector('#enter-store').addEventListener('click', () => window.SNC.go('#/store/' + nearest.store_id));
    view.querySelectorAll('.store-row').forEach((b) => b.addEventListener('click', () => window.SNC.go('#/store/' + b.dataset.store)));
    try {
      const home = await fetchJSON('/api/stores/' + encodeURIComponent(nearest.store_id) + '/home');
      const cp = view.querySelector('#home-coupons');
      cp.innerHTML = (home.coupons || []).map(couponHTML).join('') || '<p class="res-empty">No coupons right now</p>';
      const of = view.querySelector('#home-offers');
      of.innerHTML = (home.offers || []).map(offerHTML).join('') || '<p class="res-empty">No deals right now</p>';
      bindOffers(of, home.offers || [], nearest.store_id);
    } catch (e) { /* keep skeleton-free */ view.querySelector('#home-coupons').innerHTML = ''; }
  }

  function bindOffers(root, offers, storeId) {
    root.querySelectorAll('.offer').forEach((b) => {
      const go = () => window.SNC.go('#/store/' + storeId + '/search?q=' + encodeURIComponent(b.dataset.title));
      b.addEventListener('click', (e) => { if (!e.target.closest('[data-cart]')) go(); });
      b.addEventListener('keydown', (e) => { if (e.key === 'Enter') go(); });
    });
    root.querySelectorAll('[data-cart]').forEach((btn) => btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const o = offers.find((x) => x.product && x.product.id === btn.dataset.cart);
      if (o) window.SN.setCartBtn(btn, window.Cart.toggle({ product: Object.assign({}, o.product, { price: o.sale_price || o.product.price }) }));
    }));
  }

  window.Home = { render, couponHTML, offerHTML, openPill, hoursLine, pharmacyLine, I, bindOffers };
})();
