/* Orders: localStorage cr_orders_v1, order tracking page (placed → preparing → shipping → delivered),
 * auto-advancing every CR_CONFIG.deliverySpeedSec, "Delivered" screen + toast, order history. */
(function () {
  'use strict';
  const { yen, esc, toast, PLACEHOLDER_IMG } = window.CR;
  const KEY = 'cr_orders_v1';
  const STATUSES = [
    { id: 'placed', label: 'Order placed', icon: '<path d="M9 16.17 4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/>' },
    { id: 'preparing', label: 'Preparing', icon: '<path d="M20 6h-2.18c.11-.31.18-.65.18-1a2.996 2.996 0 0 0-5.5-1.65l-.5.67-.5-.68C10.96 2.54 10.05 2 9 2 7.34 2 6 3.34 6 5c0 .35.07.69.18 1H4c-1.11 0-1.99.89-1.99 2L2 19c0 1.11.89 2 2 2h16c1.11 0 2-.89 2-2V8c0-1.11-.89-2-2-2zm0 13H4v-2h16v2zm0-5H4V8h5.08L7 10.83 8.62 12 12 7.4l3.38 4.6L17 10.83 14.92 8H20v6z"/>' },
    { id: 'shipping', label: 'Out for delivery', icon: '<path d="M20 8h-3V4H3c-1.1 0-2 .9-2 2v11h2c0 1.66 1.34 3 3 3s3-1.34 3-3h6c0 1.66 1.34 3 3 3s3-1.34 3-3h2v-5l-3-4zM6 18.5c-.83 0-1.5-.67-1.5-1.5s.67-1.5 1.5-1.5 1.5.67 1.5 1.5-.67 1.5-1.5 1.5zm13.5-9 1.96 2.5H17V9.5h2.5zm-1.5 9c-.83 0-1.5-.67-1.5-1.5s.67-1.5 1.5-1.5 1.5.67 1.5 1.5-.67 1.5-1.5 1.5z"/>' },
    { id: 'delivered', label: 'Delivered', icon: '<path d="M10 20v-6h4v6h5v-8h3L12 3 2 12h3v8z"/>' },
  ];
  const speedSec = () => Math.max(1, Number((window.CR_CONFIG || {}).deliverySpeedSec) || 8);

  function load() { try { const v = JSON.parse(localStorage.getItem(KEY) || '[]'); return Array.isArray(v) ? v : []; } catch (e) { return []; } }
  function saveAll(list) { try { localStorage.setItem(KEY, JSON.stringify(list)); } catch (e) { /* ignore */ } }

  function stepOf(o) {
    const sp = o.speedSec || speedSec();
    return Math.min(3, Math.floor((Date.now() - o.placedAt) / (sp * 1000)));
  }
  function statusOf(o) { return STATUSES[stepOf(o)]; }
  function stepTime(o, i) {
    const t = new Date(o.placedAt + i * (o.speedSec || speedSec()) * 1000);
    return t.toLocaleTimeString('en-US', { timeZone: 'Asia/Tokyo', hour: 'numeric', minute: '2-digit', second: '2-digit' });
  }
  function pad(n, w) { return String(n).padStart(w, '0'); }
  function newOrderId() {
    const d = new Date();
    const ymd = d.getFullYear() + pad(d.getMonth() + 1, 2) + pad(d.getDate(), 2);
    const used = new Set(load().map((o) => o.id));
    let id;
    do { id = 'CR-' + ymd + '-' + pad(Math.floor(1000 + Math.random() * 9000), 4); } while (used.has(id));
    return id;
  }

  const Orders = {
    STATUSES,
    all() { return load().sort((a, b) => b.placedAt - a.placedAt); },
    get(id) { return load().find((o) => o.id === id) || null; },
    latest() { return Orders.all()[0] || null; },
    newOrderId,
    save(order) {
      const list = load().filter((o) => o.id !== order.id);
      list.push(order);
      saveAll(list);
      return order;
    },
    statusOf, stepOf,
    /** get_order_status client function payload */
    toAgent(id) {
      const o = id ? Orders.get(id) : Orders.latest();
      if (!o) return { ok: false, error: 'ORDER_NOT_FOUND', order_id: id || '' };
      const st = statusOf(o);
      const eta = st.id === 'delivered' ? 'Delivered' : (o.slot ? o.slot.label : 'Soon');
      return { order_id: o.id, status: st.id, status_label: st.label, eta, total_tax_included: o.total };
    },
    /** order tracking page */
    track(id) { window.Router.go({ order: id }); },
    renderOrder(id) {
      const o = Orders.get(id);
      const v = window.CR.view();
      if (!o) { v.innerHTML = '<div class="cr-container"><div class="cr-empty"><h2>Order not found</h2><p>Order number: ' + esc(id) + '</p></div></div>'; return; }
      const step = stepOf(o);
      const st = STATUSES[step];
      const delivered = st.id === 'delivered';
      const pct = (step / 3) * 75;
      v.innerHTML =
        '<div class="cr-container"><section class="cr-order" id="cr-order" data-order="' + esc(o.id) + '" data-status="' + st.id + '">' +
          '<div class="cr-order__hero">' +
            '<div class="cr-order__check"><svg viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg></div>' +
            '<h1>' + (delivered ? 'Your order has been delivered' : 'Thank you for your order!') + '</h1>' +
            '<p>' + (delivered ? 'We hope to see you again soon.' : 'Estimated delivery: <b>' + esc(o.slot ? (o.slot.date_label + ' ' + o.slot.time) : '') + '</b>') + '</p>' +
            '<span class="cr-order__id" id="cr-order-id">Order no. ' + esc(o.id) + '</span>' +
          '</div>' +
          '<ol class="cr-timeline" id="cr-timeline" aria-label="Delivery status">' +
            '<span class="cr-timeline__fill" style="width:' + pct + '%"></span>' +
            STATUSES.map((s, i) => '<li class="' + (i <= step ? 'is-done' : '') + (i === step && !delivered ? ' is-current' : '') + (delivered && i === 3 ? ' is-delivered' : '') + '" data-step="' + s.id + '">' +
              '<span class="cr-timeline__dot"><svg viewBox="0 0 24 24">' + s.icon + '</svg></span>' +
              '<b>' + s.label + '</b><small>' + (i <= step ? stepTime(o, i) : '') + '</small></li>').join('') +
          '</ol>' +
          (delivered ? '<div class="cr-delivered" id="cr-delivered"><div class="cr-delivered__icon">📦</div><h2>Delivered</h2><p>Left at your door (' + esc(o.slot ? o.slot.label : '') + ' delivery).</p></div>' : '') +
          '<div class="cr-order__grid">' +
            '<div class="cr-panel"><h2>Delivery &amp; payment</h2>' +
              '<dl class="cr-confirm-list">' +
                '<div><dt>Delivery time</dt><dd>' + esc(o.slot ? o.slot.date_label + ' ' + o.slot.time : '-') + '</dd><span></span></div>' +
                '<div><dt>Payment method</dt><dd>' + esc(o.payment) + '</dd><span></span></div>' +
                '<div><dt>Ship to</dt><dd>' + esc(window.CR.getCustomer().name) + ' (address on file)</dd><span></span></div>' +
              '</dl></div>' +
            '<div class="cr-panel"><h2>Items</h2>' +
              '<ul class="cr-summary__lines">' + o.lines.map((l) => '<li><img src="' + esc(l.image || PLACEHOLDER_IMG) + '" alt=""><span class="t">' + esc(l.title) + ' × ' + l.qty + '</span><span>' + yen(l.price * l.qty) + '</span></li>').join('') + '</ul>' +
              '<div class="cr-summary__row"><span>Subtotal</span><span>' + yen(o.subtotal) + '</span></div>' +
              '<div class="cr-summary__row"><span>Shipping</span><span>' + (o.shipping ? yen(o.shipping) : 'Free') + '</span></div>' +
              '<div class="cr-summary__row total"><span>Total (tax incl.)</span><strong>' + yen(o.total) + '</strong></div>' +
            '</div>' +
          '</div>' +
          '<div class="cr-co__actions"><button type="button" class="cr-btn" data-nav="orders">View order history</button><button type="button" class="cr-btn cr-btn--primary" data-nav="home">Continue shopping</button></div>' +
        '</section></div>';
    },
    renderHistory() {
      const list = Orders.all();
      const v = window.CR.view();
      v.innerHTML = '<div class="cr-container" style="max-width:960px;padding-top:28px">' +
        '<h1 style="font-size:26px;margin-bottom:18px">Order history</h1>' +
        (list.length ? '<div class="cr-history">' + list.map((o) => {
          const st = statusOf(o);
          return '<button type="button" class="cr-history__item" data-order="' + esc(o.id) + '">' +
            '<span class="cr-history__thumbs">' + o.lines.slice(0, 4).map((l) => '<img src="' + esc(l.image || PLACEHOLDER_IMG) + '" alt="">').join('') + '</span>' +
            '<span><b style="font:500 15px var(--cr-font)">' + esc(o.id) + '</b><br><small style="color:var(--cr-muted)">' + new Date(o.placedAt).toLocaleString('en-US', { timeZone: 'Asia/Tokyo' }) + ' • ' + o.lines.reduce((a, l) => a + l.qty, 0) + ' items</small></span>' +
            '<span class="cr-status' + (st.id === 'delivered' ? ' cr-status--delivered' : '') + '">' + st.label + '</span>' +
            '<strong style="font:700 16px var(--cr-font)">' + yen(o.total) + '</strong></button>';
        }).join('') + '</div>' : '<div class="cr-empty"><h2>No orders yet</h2><p>Start shopping with help from the AI Concierge.</p></div>') +
        '</div>';
    },
  };
  window.Orders = Orders;

  // ticker: advance statuses, refresh tracking page, notify delivered
  let lastSteps = {};
  function tick() {
    const list = load();
    let dirty = false;
    list.forEach((o) => {
      const step = stepOf(o);
      const prev = lastSteps[o.id];
      if (prev !== undefined && prev !== step) {
        const page = document.getElementById('cr-order');
        if (page && page.dataset.order === o.id) Orders.renderOrder(o.id);
        if (step === 3 && !o.notifiedDelivered) {
          toast('📦 Order ' + o.id + ' has been delivered', { success: true, ms: 5000 });
        } else if (step < 3) {
          toast('Order ' + o.id + ': ' + STATUSES[step].label);
        }
        window.CR.emit('order-status', Orders.toAgent(o.id));
      }
      if (step === 3 && !o.notifiedDelivered && prev !== undefined) { o.notifiedDelivered = true; dirty = true; }
      if (o.status !== STATUSES[step].id) { o.status = STATUSES[step].id; dirty = true; }
      lastSteps[o.id] = step;
    });
    if (dirty) saveAll(list);
  }
  setInterval(tick, 500);
  tick();

  document.addEventListener('click', (e) => {
    const b = e.target.closest('[data-order]');
    if (b && b.classList.contains('cr-history__item')) Orders.track(b.dataset.order);
  });
})();
