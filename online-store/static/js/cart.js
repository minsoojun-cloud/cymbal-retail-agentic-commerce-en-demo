/* Cart: localStorage cr_cart_v1 + drawer (qty ±). Totals are tax included.
 * While the chat panel is open, the cart is rendered inside the chat instead (chat_cards.js, CONTRACT v2). */
(function () {
  'use strict';
  const { yen, esc, toast, PLACEHOLDER_IMG } = window.CR;
  const KEY = 'cr_cart_v1';
  const FREE_SHIPPING = 3000;
  const SHIPPING_FEE = 330;
  const $ = (id) => document.getElementById(id);

  let lines = load();

  function load() {
    try {
      const v = JSON.parse(localStorage.getItem(KEY) || '[]');
      // CONTRACT v6: carts saved before the relative-image change may hold absolute image URIs
      return Array.isArray(v) ? v.map((l) => (l && l.image ? Object.assign(l, { image: window.CR.localImg(l.image) }) : l)) : [];
    } catch (e) { return []; }
  }
  function save() { try { localStorage.setItem(KEY, JSON.stringify(lines)); } catch (e) { /* ignore */ } }
  function count() { return lines.reduce((a, l) => a + l.qty, 0); }
  function subtotal() { return lines.reduce((a, l) => a + l.qty * (Number(l.price) || 0), 0); }
  function shippingFor(sub) { return sub >= FREE_SHIPPING || sub === 0 ? 0 : SHIPPING_FEE; }

  function renderHeader(bump) {
    const b = $('cr-cart-count');
    if (!b) return;
    const c = count();
    b.textContent = String(c);
    b.classList.toggle('is-zero', c === 0);
    if (bump) { b.classList.remove('bump'); void b.offsetWidth; b.classList.add('bump'); }
  }
  function renderDrawer() {
    const list = $('cr-drawer-list');
    if (!list) return;
    if (!lines.length) {
      list.innerHTML = '<li class="cr-drawer__empty">Your cart is empty<br><small>Tap "Add to cart" on anything you like</small></li>';
    } else {
      list.innerHTML = lines.map((l) =>
        '<li class="cr-line" data-id="' + esc(l.id) + '">' +
          '<img src="' + esc(l.image || PLACEHOLDER_IMG) + '" alt="">' +
          '<div><div class="cr-line__title">' + esc(l.title) + '</div>' +
            '<div class="cr-line__unit">' + yen(l.price) + ' (tax incl.)</div>' +
            '<div class="cr-qty"><button type="button" data-act="dec" aria-label="Decrease quantity">−</button><span>' + l.qty + '</span><button type="button" data-act="inc" aria-label="Increase quantity">+</button></div>' +
          '</div>' +
          '<div class="cr-line__total">' + yen(l.qty * l.price) + '</div>' +
        '</li>').join('');
    }
    const sub = subtotal();
    $('cr-drawer-total').textContent = yen(sub);
    $('cr-drawer-qty').textContent = count() ? '(' + count() + (count() === 1 ? ' item)' : ' items)') : '';
    const meter = $('cr-ship-meter');
    if (meter) {
      const pct = Math.min(100, Math.round(sub / FREE_SHIPPING * 100));
      meter.innerHTML = !lines.length ? '' : (sub >= FREE_SHIPPING
        ? '🎉 You qualify for free shipping'
        : 'Add <b>' + yen(FREE_SHIPPING - sub) + '</b> more for free shipping (current shipping ' + yen(SHIPPING_FEE) + ')') +
        '<div class="cr-ship-meter__bar"><i style="width:' + pct + '%"></i></div>';
    }
    $('cr-checkout-btn').disabled = !lines.length;
  }
  function render(bump) { renderHeader(bump); renderDrawer(); window.CR.emit('cart', summary()); }

  function summary() {
    const sub = subtotal();
    return { count: count(), subtotal: sub, shipping: shippingFor(sub), total: sub + shippingFor(sub) };
  }

  function open() {
    renderDrawer();
    $('cr-drawer').classList.add('is-open');
    $('cr-drawer').setAttribute('aria-hidden', 'false');
    $('cr-overlay').hidden = false;
  }
  function close() {
    $('cr-drawer').classList.remove('is-open');
    $('cr-drawer').setAttribute('aria-hidden', 'true');
    $('cr-overlay').hidden = true;
  }

  const Cart = {
    FREE_SHIPPING, SHIPPING_FEE, shippingFor,
    /** add(product, qty) — used by UI buttons AND the add_to_cart client function */
    add(p, qty, opts) {
      opts = opts || {};
      // CONTRACT_EN v14: store_only products cannot be bought online (UI buttons AND agent add_to_cart)
      if (String((p && p.channel) || 'omni').toLowerCase() === 'store_only') {
        const msg = 'This item is available in stores only';
        if (!opts.silent) toast(msg);
        return { ok: false, error: 'STORE_ONLY', product_id: p.id, title: p.title, message: msg };
      }
      const q = Math.max(1, Math.min(99, parseInt(qty, 10) || 1));
      const ex = lines.find((l) => l.id === p.id);
      if (ex) ex.qty = Math.min(99, ex.qty + q);
      else lines.push({ id: p.id, title: p.title, price: Number(p.price) || 0, image: p.image || '', qty: q });
      save();
      render(true);
      if (!opts.silent) toast('Added "' + p.title + '"' + (q > 1 ? ' × ' + q : '') + ' to your cart');
      // chat open → cart card inside the chat window (chat_cards.js); opts.quiet suppresses it (bulk adds)
      window.CR.emit('cart-add', { id: p.id, qty: q, quiet: !!opts.quiet });
      return { ok: true, cart_count: count(), cart_total_tax_included: subtotal(), title: p.title };
    },
    setQty(id, qty) {
      const l = lines.find((x) => x.id === id);
      if (!l) return;
      l.qty = Math.min(99, qty);
      if (l.qty <= 0) lines = lines.filter((x) => x.id !== id);
      save();
      render();
    },
    remove(id) { Cart.setQty(id, 0); },
    clear() { lines = []; save(); render(); },
    lines() { return JSON.parse(JSON.stringify(lines)); },
    count, subtotal, summary,
    /** get_cart client function payload */
    toAgent() {
      return {
        lines: lines.map((l) => ({ product_id: l.id, title: l.title, price: l.price, qty: l.qty })),
        cart_count: count(), cart_total_tax_included: subtotal(),
      };
    },
    open, close,
  };
  window.Cart = Cart;

  function bind() {
    render();
    // header cart button: while the AI concierge is open the cart is shown as a card inside the chat, otherwise the drawer
    $('cr-cart-btn').addEventListener('click', () => {
      if (window.CRChat && window.CRChat.isOpen() && window.ChatCards) window.ChatCards.showCart({ force: true });
      else open();
    });
    $('cr-drawer-close').addEventListener('click', close);
    $('cr-overlay').addEventListener('click', close);
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });
    $('cr-drawer-list').addEventListener('click', (e) => {
      const b = e.target.closest('button[data-act]');
      if (!b) return;
      const id = b.closest('.cr-line').dataset.id;
      const l = lines.find((x) => x.id === id);
      if (l) Cart.setQty(id, l.qty + (b.dataset.act === 'inc' ? 1 : -1));
    });
    $('cr-checkout-btn').addEventListener('click', () => {
      close();
      const r = window.Checkout.open();
      if (!r.ok) toast('Your cart is empty');
    });
    window.addEventListener('storage', (e) => { if (e.key === KEY) { lines = load(); render(); } });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind);
  else bind();
})();
