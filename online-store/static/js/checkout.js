/* Checkout state machine: idle → delivery (slot select) → payment (Cymbal Pay) → confirm → placed.
 * UI buttons and agent actions (start_checkout / select_delivery_slot / place_order) call the
 * same methods, so chat and clicks always stay in sync. */
(function () {
  'use strict';
  const { yen, esc, toast, PLACEHOLDER_IMG } = window.CR;
  const PAYMENT = 'Cymbal Pay (VISA **** 4242)';
  const KEY = 'cr_checkout_v1';
  const STEPS = [
    { id: 'delivery', label: 'Delivery' },
    { id: 'payment', label: 'Payment' },
    { id: 'confirm', label: 'Review' },
    { id: 'placed', label: 'Order placed' },
  ];

  function slotDefs() {
    const base = new Date();
    const day = (n) => { const d = new Date(base); d.setDate(d.getDate() + n); return d; };
    const dl = (d) => d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', timeZone: 'Asia/Tokyo' });
    const d1 = day(1), d2 = day(2);
    const mk = (id, rel, d, time) => ({ slot_id: id, label: rel + ' ' + time, day: rel, time, date_label: dl(d), date: d.toISOString().slice(0, 10) });
    return [
      mk('d1_am', 'Tomorrow', d1, '9:00-12:00'),
      mk('d1_pm', 'Tomorrow', d1, '14:00-17:00'),
      mk('d1_eve', 'Tomorrow', d1, '18:00-21:00'),
      mk('d2_am', 'Day after tomorrow', d2, '9:00-12:00'),
      mk('d2_pm', 'Day after tomorrow', d2, '14:00-17:00'),
    ];
  }

  const saved = (() => { try { return JSON.parse(sessionStorage.getItem(KEY) || '{}'); } catch (e) { return {}; } })();

  const Checkout = {
    PAYMENT,
    state: saved.state || 'idle',
    slot: saved.slot_id ? slotDefs().find((s) => s.slot_id === saved.slot_id) || null : null,
    lastOrderId: saved.lastOrderId || '',
    slots: slotDefs,
    persist() { try { sessionStorage.setItem(KEY, JSON.stringify({ state: this.state, slot_id: this.slot && this.slot.slot_id, lastOrderId: this.lastOrderId })); } catch (e) { /* ignore */ } },
    go(state, opts) {
      this.state = state;
      this.persist();
      window.CR.emit('checkout', { state });
      if (state === 'placed') { window.Orders.track(this.lastOrderId); return; }
      if (state === 'idle') return;
      if (!(opts && opts.noNav)) window.Router.go({ view: 'checkout' }, { replace: window.Router.current().view === 'checkout' });
      else this.render();
    },
    totals() {
      const sub = window.Cart.subtotal();
      const ship = window.Cart.shippingFor(sub);
      return { subtotal: sub, shipping: ship, total: sub + ship };
    },
    /** Proceed to checkout / start_checkout */
    open() {
      if (!window.Cart.count()) return { ok: false, error: 'EMPTY_CART' };
      if (this.state === 'placed' || this.state === 'idle') this.slot = null;
      this.go('delivery');
      return { ok: true, slots: slotDefs().map((s) => ({ slot_id: s.slot_id, label: s.label })), cart_total_tax_included: window.Cart.subtotal() };
    },
    /** Choose delivery slot / select_delivery_slot */
    selectSlot(id) {
      const s = slotDefs().find((x) => x.slot_id === id);
      if (!s) return { ok: false, error: 'INVALID_SLOT', slots: slotDefs().map((x) => x.slot_id) };
      if (!window.Cart.count()) return { ok: false, error: 'EMPTY_CART' };
      this.slot = s;
      this.go('payment');
      return { ok: true, slot_label: s.label };
    },
    /** Confirm payment method (Cymbal Pay fixed) */
    confirmPayment() {
      if (!this.slot) return { ok: false, error: 'NO_SLOT' };
      this.go('confirm');
      return { ok: true, payment_method: PAYMENT };
    },
    /** Place order / place_order — CONTRACT v3: use the agent-generated order id (args.order_id) when given */
    placeOrder(orderId) {
      orderId = typeof orderId === 'string' && /^CR-\d{8}-\d{4}$/.test(orderId) ? orderId : '';
      const dup = orderId && window.Orders.get(orderId);
      if (dup) return { ok: true, order_id: dup.id, total_tax_included: dup.total, slot_label: dup.slot ? dup.slot.label : '', duplicate: true };
      if (!window.Cart.count()) return { ok: false, error: 'EMPTY_CART' };
      if (!this.slot) return { ok: false, error: 'NO_SLOT', message: 'Please choose a delivery time first' };
      const t = this.totals();
      const order = {
        id: orderId || window.Orders.newOrderId(),
        lines: window.Cart.lines(),
        subtotal: t.subtotal, shipping: t.shipping, total: t.total,
        slot: this.slot, payment: PAYMENT,
        placedAt: Date.now(), speedSec: Number((window.CR_CONFIG || {}).deliverySpeedSec) || 8,
        status: 'placed', customer_id: window.CR.getCustomer().customer_id,
      };
      window.Orders.save(order);
      this.lastOrderId = order.id;
      this.state = 'placed';
      window.Cart.clear();
      const slotLabel = this.slot.label;
      this.slot = null;
      this.go('placed');
      toast('Order placed (' + order.id + ')', { success: true });
      return { ok: true, order_id: order.id, total_tax_included: order.total, slot_label: slotLabel };
    },
    cancel() { this.state = 'idle'; this.slot = null; this.persist(); window.Router.go({ view: 'home' }); },

    // ---------- view ----------
    render() {
      const v = window.CR.view();
      if (!window.Cart.count() && this.state !== 'placed') {
        v.innerHTML = '<div class="cr-container"><div class="cr-empty"><h2>Your cart is empty</h2><p>Add some items to your cart before checking out.</p><button class="cr-btn cr-btn--primary" data-nav="home">Continue shopping</button></div></div>';
        return;
      }
      if (this.state === 'idle' || this.state === 'placed') { this.state = 'delivery'; this.persist(); }
      const idx = STEPS.findIndex((s) => s.id === this.state);
      const t = this.totals();
      const lines = window.Cart.lines();
      const stepper = '<ol class="cr-steps" id="cr-steps">' + STEPS.map((s, i) =>
        '<li class="' + (i < idx ? 'is-done' : i === idx ? 'is-active' : '') + '" data-step="' + s.id + '"><b>' + (i < idx ? '✓' : i + 1) + '</b>' + s.label + '</li>').join('') + '</ol>';

      const slotPanel = '<section class="cr-panel" id="cr-step-delivery"><h2>Choose a delivery time</h2><p class="cr-panel__sub">Pick the time window that works best for you. ' + (t.shipping ? 'Free shipping over ¥3,000.' : 'Your order ships free.') + '</p>' +
        '<div class="cr-slots">' + slotDefs().map((s) =>
          '<button type="button" class="cr-slot' + (this.slot && this.slot.slot_id === s.slot_id ? ' is-selected' : '') + '" data-slot="' + s.slot_id + '" id="slot-' + s.slot_id + '">' +
            '<span class="cr-slot__day">' + s.day + ' • ' + s.date_label + '</span><span class="cr-slot__time">' + s.time + '</span>' +
            '<span class="cr-slot__fee">' + (t.shipping ? 'Shipping ' + yen(t.shipping) : 'Free shipping') + '</span></button>').join('') +
        '</div></section>';
      const slotDone = this.slot ? '<section class="cr-panel"><h2>Delivery time <button type="button" class="cr-link-btn" data-step-go="delivery">Change</button></h2><p class="cr-panel__sub" style="margin:0">' + esc(this.slot.date_label + ' ' + this.slot.time) + ' (' + esc(this.slot.label) + ')</p></section>' : '';
      const payPanel = '<section class="cr-panel" id="cr-step-payment"><h2>Payment method</h2><p class="cr-panel__sub">Your saved payment method will be used.</p>' +
        '<div class="cr-pay"><span class="cr-pay__card">VISA</span><div><b>' + PAYMENT + '</b><span>Expires 12/29 • Earns Cymbal Points</span></div></div>' +
        '<div class="cr-co__actions"><button type="button" class="cr-btn" data-step-go="delivery">Back</button><button type="button" class="cr-btn cr-btn--primary" id="cr-pay-next">Continue to review</button></div></section>';
      const confirmPanel = '<section class="cr-panel" id="cr-step-confirm"><h2>Review your order</h2><p class="cr-panel__sub">Check the details below, then tap "Place order".</p>' +
        '<dl class="cr-confirm-list">' +
          '<div><dt>Delivery time</dt><dd>' + esc(this.slot ? this.slot.date_label + ' ' + this.slot.time : '-') + '</dd><button type="button" class="cr-link-btn" data-step-go="delivery">Change</button></div>' +
          '<div><dt>Payment method</dt><dd>' + PAYMENT + '</dd><span></span></div>' +
          '<div><dt>Ship to</dt><dd>' + esc(window.CR.getCustomer().name) + ' (address on file)</dd><span></span></div>' +
          '<div><dt>Total (tax incl.)</dt><dd><strong style="font:700 20px var(--cr-font)">' + yen(t.total) + '</strong></dd><span></span></div>' +
        '</dl>' +
        '<div class="cr-co__actions"><button type="button" class="cr-btn" data-step-go="payment">Back</button><button type="button" class="cr-btn cr-btn--primary" id="cr-place-order" style="min-width:220px;height:48px;border-radius:24px;font-size:15px">Place order</button></div>' +
        '<div class="cr-agent-note">✨ You can also just tell the AI Concierge "place my order"</div></section>';

      let main = '';
      if (this.state === 'delivery') main = slotPanel;
      else if (this.state === 'payment') main = slotDone + payPanel;
      else main = confirmPanel;

      v.innerHTML = '<div class="cr-container"><div class="cr-co" id="cr-checkout" data-state="' + this.state + '">' +
        '<div><h1>Checkout</h1>' + stepper + main + '</div>' +
        '<aside class="cr-summary" id="cr-co-summary"><h2>Order summary (' + window.Cart.count() + (window.Cart.count() === 1 ? ' item)' : ' items)') + '</h2>' +
          '<ul class="cr-summary__lines">' + lines.map((l) => '<li><img src="' + esc(l.image || PLACEHOLDER_IMG) + '" alt=""><span class="t">' + esc(l.title) + ' × ' + l.qty + '</span><span>' + yen(l.price * l.qty) + '</span></li>').join('') + '</ul>' +
          '<div class="cr-summary__row"><span>Subtotal</span><span>' + yen(t.subtotal) + '</span></div>' +
          '<div class="cr-summary__row"><span>Shipping</span><span id="cr-co-shipping">' + (t.shipping ? yen(t.shipping) : 'Free') + '</span></div>' +
          '<div class="cr-summary__row total"><span>Total (tax incl.)</span><strong id="cr-co-total">' + yen(t.total) + '</strong></div>' +
        '</aside></div></div>';
    },
  };
  window.Checkout = Checkout;

  document.addEventListener('click', (e) => {
    const s = e.target.closest('[data-slot]');
    if (s) { Checkout.selectSlot(s.dataset.slot); return; }
    if (e.target.closest('#cr-pay-next')) { Checkout.confirmPayment(); return; }
    if (e.target.closest('#cr-place-order')) { Checkout.placeOrder(); return; }
    const g = e.target.closest('[data-step-go]');
    if (g) { Checkout.go(g.dataset.stepGo); }
  });
  window.CR.on('cart', () => { if (window.Router && window.Router.current().view === 'checkout' && Checkout.state !== 'placed') Checkout.render(); });
})();
