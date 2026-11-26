/* ChatCards: cart / checkout / order / dinner-set cards rendered INSIDE the chat window via ChatRich (CONTRACT v2).
 *
 *  cart card     add_to_cart / get_cart / UI "Add to cart" / header cart (chat open). One live card is kept in sync with
 *                Cart; older cart cards collapse to a one-line summary.
 *  slots card    Checkout state → delivery
 *  confirm card  Checkout state → confirm
 *  order card    Checkout state → placed; live status timeline (placed → preparing → shipping → delivered)
 *  set card      show_products_on_page with sections (dinner set)
 *
 * Buttons that advance checkout send natural-language messages to the ADK agent (it then emits the matching `action`
 * event: single source of action). Qty ± / remove act directly on Cart. In mock-chat mode every button acts directly.
 * Cards are recorded in the chat history (sessionStorage) and restored after a reload: order / set cards are rebuilt
 * live, cart / slot / confirm cards come back as one-line summaries (their state has moved on).
 * The page-side Checkout object stays the only state machine; cards only render its state.
 */
(function () {
  'use strict';
  const CR = window.CR;
  const { yen, esc, PLACEHOLDER_IMG, SPARK } = CR;
  const R = () => window.ChatRich;
  const chatOpen = () => !!(window.CRChat && window.CRChat.isOpen());
  const isMock = () => !!(window.CRChat && window.CRChat.mock);
  const items = (n) => Number(n).toLocaleString('en-US') + (Number(n) === 1 ? ' item' : ' items');

  const ICON = {
    cart: '<svg viewBox="0 0 24 24"><path fill="#1a73e8" d="M7 18c-1.1 0-1.99.9-1.99 2S5.9 22 7 22s2-.9 2-2-.9-2-2-2zM1 2v2h2l3.6 7.59-1.35 2.45c-.16.28-.25.61-.25.96 0 1.1.9 2 2 2h12v-2H7.42a.25.25 0 0 1-.25-.25l.03-.12.9-1.63h7.45c.75 0 1.41-.41 1.75-1.03l3.58-6.49A1 1 0 0 0 20 4H5.21l-.94-2H1zm16 16c-1.1 0-1.99.9-1.99 2s.89 2 1.99 2 2-.9 2-2-.9-2-2-2z"/></svg>',
    clock: '<svg viewBox="0 0 24 24"><path fill="#1a73e8" d="M11.99 2C6.47 2 2 6.48 2 12s4.47 10 9.99 10C17.52 22 22 17.52 22 12S17.52 2 11.99 2zM12 20c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8zm.5-13H11v6l5.25 3.15.75-1.23-4.5-2.67z"/></svg>',
    check: '<svg viewBox="0 0 24 24"><path fill="#1a73e8" d="M19 3h-4.18C14.4 1.84 13.3 1 12 1s-2.4.84-2.82 2H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm-7 0c.55 0 1 .45 1 1s-.45 1-1 1-1-.45-1-1 .45-1 1-1zm-2 14-4-4 1.41-1.41L10 14.17l6.59-6.59L18 9l-8 8z"/></svg>',
    truck: '<svg viewBox="0 0 24 24"><path fill="#188038" d="M20 8h-3V4H3c-1.1 0-2 .9-2 2v11h2c0 1.66 1.34 3 3 3s3-1.34 3-3h6c0 1.66 1.34 3 3 3s3-1.34 3-3h2v-5l-3-4zM6 18.5c-.83 0-1.5-.67-1.5-1.5s.67-1.5 1.5-1.5 1.5.67 1.5 1.5-.67 1.5-1.5 1.5zm13.5-9 1.96 2.5H17V9.5h2.5zm-1.5 9c-.83 0-1.5-.67-1.5-1.5s.67-1.5 1.5-1.5 1.5.67 1.5 1.5-.67 1.5-1.5 1.5z"/></svg>',
    cartGray: '<svg viewBox="0 0 24 24"><path d="M7 18c-1.1 0-1.99.9-1.99 2S5.9 22 7 22s2-.9 2-2-.9-2-2-2zM1 2v2h2l3.6 7.59-1.35 2.45c-.16.28-.25.61-.25.96 0 1.1.9 2 2 2h12v-2H7.42a.25.25 0 0 1-.25-.25l.03-.12.9-1.63h7.45c.75 0 1.41-.41 1.75-1.03l3.58-6.49A1 1 0 0 0 20 4H5.21l-.94-2H1zm16 16c-1.1 0-1.99.9-1.99 2s.89 2 1.99 2 2-.9 2-2-.9-2-2-2z"/></svg>',
  };
  const TAG_CLASS = { 'Frequently bought': 'freq', 'Family favorite': 'family', 'On sale': 'sale', 'In season': 'season', 'Quick': 'quick' };
  const tagChips = (tags) => (tags && tags.length ? '<div class="crc-tags">' + tags.map((t) => '<span class="crc-tag crc-tag--' + (TAG_CLASS[t] || 'other') + '">' + esc(t) + '</span>').join('') + '</div>' : '');
  const head = (icon, title, sub, badge, badgeOk) =>
    '<div class="crc-head"><span class="crc-head__icon">' + icon + '</span><div class="crc-head__title">' + esc(title) + (sub ? '<small>' + esc(sub) + '</small>' : '') + '</div>' +
    (badge ? '<span class="crc-badge' + (badgeOk ? ' is-ok' : '') + '">' + esc(badge) + '</span>' : '') + '</div>';

  /** send a message to the ADK agent (it emits the action); in mock mode run `direct` instead */
  function say(text, direct) {
    if (isMock()) {
      if (window.CRMockChat && window.CRMockChat.echo) window.CRMockChat.echo(text);
      return Promise.resolve().then(direct);
    }
    window.CRChat.ask(text);
    return Promise.resolve();
  }
  function pending(btn, ms) { btn.classList.add('is-pending'); setTimeout(() => btn.classList.remove('is-pending'), ms || 6000); }

  // ===================================================================== cart
  let liveCart = null;
  function cartInner(highlightId) {
    const lines = window.Cart.lines();
    const s = window.Cart.summary();
    if (!lines.length) {
      return '<div class="crc-card">' + head(ICON.cart, 'Cart', '0 items', '') + '<div class="crc-empty">Your cart is empty</div></div>';
    }
    const F = window.Cart.FREE_SHIPPING;
    const pct = Math.min(100, Math.round(s.subtotal / F * 100));
    return '<div class="crc-card" data-crc-cart>' + head(ICON.cart, 'Cart', items(s.count), s.shipping ? '' : 'Free shipping', true) +
      '<table class="crc-table"><thead><tr><th>Item</th><th class="c">Qty</th><th class="r">Price</th></tr></thead><tbody>' +
      lines.map((l) =>
        '<tr data-line="' + esc(l.id) + '"' + (l.id === highlightId ? ' style="background:#f8fbff"' : '') + '>' +
          '<td><div class="crc-prod"><img src="' + esc(l.image || PLACEHOLDER_IMG) + '" alt=""><div><div class="crc-prod__t">' + esc(l.title) + '</div><div class="crc-prod__u">' + yen(l.price) + ' each</div></div></div></td>' +
          '<td class="c"><span class="crc-qty"><button type="button" data-crc-act="dec" data-id="' + esc(l.id) + '" aria-label="Decrease quantity">−</button><span>' + l.qty + '</span>' +
            '<button type="button" data-crc-act="inc" data-id="' + esc(l.id) + '" aria-label="Increase quantity">+</button></span>' +
            '<button type="button" class="crc-rm" data-crc-act="rm" data-id="' + esc(l.id) + '">Remove</button></td>' +
          '<td class="r"><span class="crc-price">' + yen(l.price * l.qty) + '</span></td></tr>').join('') +
      '</tbody></table>' +
      '<div class="crc-sum">' +
        '<div class="crc-sum__row"><span>Subtotal</span><span>' + yen(s.subtotal) + '</span></div>' +
        '<div class="crc-sum__row"><span>Shipping</span><span>' + (s.shipping ? yen(s.shipping) : 'Free') + '</span></div>' +
        '<div class="crc-sum__row total"><span>Total (tax incl.)</span><strong data-crc-total>' + yen(s.total) + '</strong></div>' +
        '<div class="crc-note">' + (s.shipping ? 'Add <b>' + yen(F - s.subtotal) + '</b> more for free shipping' : '🎉 You qualify for free shipping') + '</div>' +
        (s.shipping ? '<div class="crc-meter"><i style="width:' + pct + '%"></i></div>' : '') +
      '</div>' +
      '<div class="crc-actions"><button type="button" class="crc-btn crc-btn--primary" data-crc-act="checkout">Proceed to checkout</button></div></div>';
  }
  function collapseCart(el, note) {
    if (!el) return;
    const s = window.Cart.summary();
    el.classList.add('is-collapsed');
    el.removeAttribute('id');
    el.innerHTML = '<div class="crc-collapsed">' + ICON.cartGray + '<span>' + (note || ('Cart <b>' + items(s.count) + ' • ' + yen(s.total) + '</b> (latest cart shown below)')) + '</span></div>';
  }
  /** explicit trigger: show the cart card at the bottom of the chat (or refresh it if it is already the latest card) */
  function showCart(opts) {
    opts = opts || {};
    if (!chatOpen() && !opts.force) return null;
    if (liveCart && liveCart.isConnected && R().isLast(liveCart)) {
      liveCart.innerHTML = cartInner(opts.highlight);
      const c = liveCart.querySelector('.crc-card');
      if (c) { c.classList.add('is-pulse'); setTimeout(() => c.classList.remove('is-pulse'), 800); }
      R().scrollSoon();
      return liveCart;
    }
    if (liveCart && liveCart.isConnected) collapseCart(liveCart);
    liveCart = R().append(cartInner(opts.highlight), { kind: 'cart', id: 'crc-cart-live', record: { summary: cartSummary() } });
    return liveCart;
  }
  function cartSummary() { const s = window.Cart.summary(); return 'Cart ' + items(s.count) + ' • ' + yen(s.total); }
  function refreshCart() {
    if (liveCart && liveCart.isConnected && !liveCart.classList.contains('is-collapsed')) liveCart.innerHTML = cartInner();
  }

  // ===================================================================== checkout: slots / confirm
  let liveSlots = null;
  let liveConfirm = null;
  function slotsInner() {
    const sel = window.Checkout.slot && window.Checkout.slot.slot_id;
    const t = window.Checkout.totals();
    const locked = !!sel && window.Checkout.state !== 'delivery';
    return '<div class="crc-card">' + head(ICON.clock, 'Choose a delivery time', 'Cart ' + items(window.Cart.count()) + ' • Total ' + yen(t.total) + ' (tax incl.)', 'Step 1 of 2') +
      '<div class="crc-slots' + (locked ? ' is-locked' : '') + '">' + window.Checkout.slots().map((s) =>
        '<button type="button" class="crc-slot' + (s.slot_id === sel ? ' is-selected' : '') + '" data-crc-act="slot" data-slot-id="' + s.slot_id + '" data-label="' + esc(s.label) + '">' +
          '<b>' + esc(s.day) + ' • ' + esc(s.date_label) + '</b><span>' + esc(s.time) + '</span><small>' + (t.shipping ? 'Shipping ' + yen(t.shipping) : 'Free shipping') + '</small></button>').join('') +
      '</div></div>';
  }
  function showSlots() {
    if (!chatOpen()) return;
    if (liveSlots && liveSlots.isConnected && R().isLast(liveSlots)) { liveSlots.innerHTML = slotsInner(); return; }
    liveSlots = R().append(slotsInner(), { kind: 'slots', record: { summary: 'Choose a delivery time' } });
  }
  function confirmInner(done) {
    const C = window.Checkout;
    const lines = window.Cart.lines();
    const t = C.totals();
    const slot = C.slot;
    return '<div class="crc-card">' + head(ICON.check, 'Review your order', items(lines.reduce((a, l) => a + l.qty, 0)), 'Step 2 of 2') +
      '<div class="crc-thumbs">' + lines.slice(0, 8).map((l) => '<figure><img src="' + esc(l.image || PLACEHOLDER_IMG) + '" alt="' + esc(l.title) + '" title="' + esc(l.title) + '">' + (l.qty > 1 ? '<figcaption>' + l.qty + '</figcaption>' : '') + '</figure>').join('') + '</div>' +
      '<dl class="crc-dl">' +
        '<div><dt>Items</dt><dd>' + lines.map((l) => esc(l.title) + ' × ' + l.qty).join('<br>') + '</dd></div>' +
        '<div><dt>Delivery</dt><dd>' + esc(slot ? slot.date_label + ' ' + slot.time : '-') + '</dd></div>' +
        '<div><dt>Payment</dt><dd><span class="crc-visa">VISA</span>' + esc(C.PAYMENT) + '</dd></div>' +
        '<div><dt>Ship to</dt><dd>' + esc(CR.getCustomer().name) + ' (address on file)</dd></div>' +
      '</dl>' +
      '<div class="crc-sum">' +
        '<div class="crc-sum__row"><span>Subtotal</span><span>' + yen(t.subtotal) + '</span></div>' +
        '<div class="crc-sum__row"><span>Shipping</span><span>' + (t.shipping ? yen(t.shipping) : 'Free') + '</span></div>' +
        '<div class="crc-sum__row total"><span>Total (tax incl.)</span><strong>' + yen(t.total) + '</strong></div>' +
      '</div>' +
      '<div class="crc-actions">' + (done
        ? '<button type="button" class="crc-btn is-done" disabled>✓ Order placed</button>'
        : '<button type="button" class="crc-btn crc-btn--primary" data-crc-act="place">Place order</button>') + '</div></div>';
  }
  function showConfirm() {
    if (!chatOpen()) return;
    if (liveConfirm && liveConfirm.isConnected && R().isLast(liveConfirm)) { liveConfirm.innerHTML = confirmInner(); return; }
    liveConfirm = R().append(confirmInner(), { kind: 'confirm', record: { summary: 'Review your order (' + yen(window.Checkout.totals().total) + ')' } });
  }

  // ===================================================================== order tracking
  const orderCards = new Map(); // order id → element
  function orderInner(id) {
    const O = window.Orders;
    const o = O.get(id);
    if (!o) return '<div class="crc-card"><div class="crc-empty">Order not found (' + esc(id) + ')</div></div>';
    const step = O.stepOf(o);
    const delivered = step === 3;
    const sp = (o.speedSec || 8) * 1000;
    const time = (i) => { const d = new Date(o.placedAt + i * sp); return d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', timeZone: 'Asia/Tokyo' }); };
    const msg = ['We received your order', 'The store is preparing your items', 'Your driver is on the way', 'Delivered ✓ Left at your front door'][step];
    return '<div class="crc-card" data-crc-order="' + esc(o.id) + '" data-status="' + O.STATUSES[step].id + '">' +
      head(delivered ? ICON.truck : ICON.check, delivered ? 'Delivered' : 'Thanks for your order!', 'Order no. ' + o.id, delivered ? 'Delivered ✓' : O.STATUSES[step].label, delivered) +
      '<ol class="crc-tl"><span class="crc-tl__fill" style="width:' + (step / 3 * 75) + '%"></span>' +
        O.STATUSES.map((s, i) => '<li class="' + (i <= step ? 'is-done' : '') + (i === step && !delivered ? ' is-current' : '') + (delivered && i === 3 ? ' is-delivered' : '') + '" data-step="' + s.id + '">' +
          '<i><svg viewBox="0 0 24 24">' + s.icon + '</svg></i>' + esc(s.label) + '<small>' + (i <= step ? time(i) : '') + '</small></li>').join('') +
      '</ol>' +
      '<div class="crc-status' + (delivered ? ' is-delivered' : '') + '"><span class="dot"></span><span>' + msg + '</span></div>' +
      '<dl class="crc-dl" style="padding-top:0">' +
        '<div><dt>Delivery</dt><dd>' + esc(o.slot ? o.slot.date_label + ' ' + o.slot.time : '-') + '</dd></div>' +
        '<div><dt>Payment</dt><dd>' + esc(o.payment) + '</dd></div>' +
        '<div><dt>Total (tax incl.)</dt><dd>' + yen(o.total) + ' (' + items(o.lines.reduce((a, l) => a + l.qty, 0)) + ')</dd></div>' +
      '</dl></div>';
  }
  function showOrder(id, opts) {
    opts = opts || {};
    if (!id || (!chatOpen() && !opts.force)) return;
    const ex = orderCards.get(id);
    if (ex && ex.isConnected) { ex.innerHTML = orderInner(id); return; }
    orderCards.set(id, R().append(orderInner(id), { kind: 'order', record: { id }, noRecord: !!opts.noRecord }));
  }
  function refreshOrders() {
    orderCards.forEach((el, id) => {
      if (!el.isConnected) return;
      const card = el.querySelector('[data-crc-order]');
      const o = window.Orders.get(id);
      if (!o || !card) return;
      if (card.dataset.status !== window.Orders.statusOf(o).id) { el.innerHTML = orderInner(id); R().scrollSoon(); }
    });
  }

  // ===================================================================== dinner set
  function setCardHtml(set) {
    const sections = set.sections || [];
    const rows = [];
    // one role cell per section (rowspan) so multi-item sections stay aligned (CONTRACT v6 layout fix)
    sections.forEach((sec) => { const n = (sec.products || []).length; (sec.products || []).forEach((p, i) => rows.push({ role: i === 0 ? sec.label : '', span: i === 0 ? n : 0, first: i === 0, p, tags: (sec.tags || [])[i] || [], qty: Math.max(1, (sec.quantities || [])[i] || 1) })); });
    const total = rows.reduce((a, r) => a + (Number(r.p.price) || 0) * r.qty, 0);
    return '<div class="crc-card">' + head(SPARK, set.theme || "Tonight's dinner set", rows.length + '-item set' + (set.isRoleSet ? ' • Total ' + yen(total) : ''), '') +
      (set.message ? '<div class="crc-concept">' + esc(set.message) + '</div>' : '') +
      '<table class="crc-table crc-settable"><thead><tr><th class="crc-setrole">Role</th><th>Item</th><th class="r">Price</th></tr></thead><tbody>' +
      rows.map((r) => '<tr class="crc-setrow' + (r.first ? ' is-first' : '') + '">' + (r.span ? '<td class="crc-setrole" rowspan="' + r.span + '"><span class="crc-role">' + esc(r.role).replace(/ &amp; /g, ' &amp;<wbr> ') + '</span></td>' : '') +
        '<td><div class="crc-prod"><img src="' + esc(window.CR.localImg(r.p.image) || PLACEHOLDER_IMG) + '" alt=""><div><div class="crc-prod__t">' + esc(r.p.title) + '</div>' + tagChips(r.tags) + '</div></div></td>' +
        '<td class="r"><span class="crc-price">' + yen(r.p.price * r.qty) + '</span>' + (r.qty > 1 ? '<div class="crc-prod__u" data-qty="' + r.qty + '">' + yen(r.p.price) + ' × ' + r.qty + '</div>' : '') + '</td></tr>').join('') +
      '</tbody></table>' +
      '<div class="crc-sum"><div class="crc-sum__row total" style="border-top:0;margin-top:0"><span>' + (set.isRoleSet ? 'Set total (tax incl.)' : 'Total (tax incl.)') + '</span><strong>' + yen(total) + '</strong></div></div>' +
      '<div class="crc-actions"><button type="button" class="crc-btn crc-btn--primary" data-crc-act="addset">Add the whole set to cart</button></div></div>';
  }
  let lastSet = null;
  function showSet(set) {
    lastSet = set;
    if (!chatOpen()) return null;
    const el = R().append(setCardHtml(set), { kind: 'set', record: { set } });
    if (el) el._set = set;
    return el;
  }

  /** rebuild a recorded card after a reload (chat history in sessionStorage) */
  function restoreCard(it) {
    if (it.kind === 'set' && it.set) {
      lastSet = it.set;
      const el = R().append(setCardHtml(it.set), { kind: 'set', noRecord: true });
      if (el) el._set = it.set;
    } else if (it.kind === 'order' && it.id) {
      showOrder(it.id, { force: true, noRecord: true });
    } else if (it.summary) {
      const el = R().append('<div class="crc-collapsed">' + ICON.cartGray + '<span>' + esc(it.summary) + ' (earlier)</span></div>', { kind: it.kind, noRecord: true });
      if (el) el.classList.add('is-collapsed');
    }
  }
  function addSetToCart(set, btn) {
    const products = [];
    (set.sections || []).forEach((sec) => (sec.products || []).forEach((p, i) => products.push({ p, qty: Math.max(1, (sec.quantities || [])[i] || 1) })));
    if (!isMock()) {
      // live: the agent adds the set (single source of action); its add_to_cart calls refresh the cart card
      if (btn) { pending(btn, 15000); btn.textContent = 'Asked the AI Concierge…'; }
      window.CRChat.ask('Add the set to my cart');
      return products.length;
    }
    products.forEach((x) => window.Cart.add(x.p, x.qty, { silent: true, quiet: true }));
    products.forEach((x, i) => { products[i] = x.p; });
    CR.toast('Added ' + items(products.length) + ' to your cart');
    if (btn) { btn.classList.add('is-done'); btn.textContent = '✓ Added ' + items(products.length); btn.disabled = true; }
    showCart({ highlight: products.length ? products[0].id : '' });
    return products.length;
  }

  // ===================================================================== button handlers (inside cards)
  function bindHandlers() {
    const r = R();
    r.on('inc', (b) => { const l = window.Cart.lines().find((x) => x.id === b.dataset.id); if (l) window.Cart.setQty(l.id, l.qty + 1); });
    r.on('dec', (b) => { const l = window.Cart.lines().find((x) => x.id === b.dataset.id); if (l) window.Cart.setQty(l.id, l.qty - 1); });
    r.on('rm', (b) => window.Cart.remove(b.dataset.id));
    r.on('checkout', (b) => {
      if (!window.Cart.count()) { CR.toast('Your cart is empty'); return; }
      pending(b);
      say('Proceed to checkout', () => window.Checkout.open());
    });
    r.on('slot', (b) => {
      const grid = b.closest('.crc-slots');
      grid.querySelectorAll('.crc-slot').forEach((x) => x.classList.toggle('is-selected', x === b));
      grid.classList.add('is-pending');
      setTimeout(() => grid.classList.remove('is-pending'), 6000);
      say(b.dataset.label + ', please', () => { const s = window.Checkout.selectSlot(b.dataset.slotId); if (s.ok) window.Checkout.confirmPayment(); });
    });
    r.on('place', (b) => {
      pending(b, 15000);
      say('Please place my order', () => window.Checkout.placeOrder());
    });
    r.on('addset', (b) => { const card = b.closest('.crc-rich'); addSetToCart((card && card._set) || lastSet || {}, b); });
  }

  // ===================================================================== wiring
  CR.on('cart', refreshCart);
  CR.on('cart-add', (d) => { if (!d || !d.quiet) showCart({ highlight: d && d.id }); });
  CR.on('checkout', (d) => {
    const st = d && d.state;
    if (st === 'delivery') {
      const b = liveCart && liveCart.isConnected && liveCart.querySelector('[data-crc-act="checkout"]');
      if (b) { b.classList.remove('is-pending'); b.classList.add('is-done'); b.textContent = '✓ Proceeded to checkout'; b.disabled = true; }
      showSlots();
    }
    if (st === 'confirm') {
      if (liveSlots && liveSlots.isConnected) liveSlots.innerHTML = slotsInner();
      showConfirm();
    }
    if (st === 'placed') {
      const id = window.Checkout.lastOrderId;
      if (liveConfirm && liveConfirm.isConnected) {
        const b = liveConfirm.querySelector('[data-crc-act="place"]');
        if (b) { b.outerHTML = '<button type="button" class="crc-btn is-done" disabled>✓ Order placed</button>'; }
      }
      if (liveCart && liveCart.isConnected) collapseCart(liveCart, 'Your cart was placed as order <b>' + esc(id) + '</b>');
      liveCart = null; liveSlots = null; liveConfirm = null;
      showOrder(id);
    }
  });
  CR.on('order-status', refreshOrders);
  setInterval(refreshOrders, 1000);
  window.addEventListener('cr-chat-reset', () => { liveCart = null; liveSlots = null; liveConfirm = null; orderCards.clear(); });

  bindHandlers();
  window.ChatCards = {
    showCart, refreshCart, showSlots, showConfirm, showOrder, showSet, addSetToCart, say, restoreCard,
    setCardHtml, TAG_CLASS, lastSet: () => lastSet,
    live: () => ({ cart: liveCart, slots: liveSlots, confirm: liveConfirm }),
  };
})();
