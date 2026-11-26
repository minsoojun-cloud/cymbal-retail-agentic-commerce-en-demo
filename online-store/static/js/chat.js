/* AI Concierge (CONTRACT v3): our own slide-over chat panel + client handlers.
 *
 * Live mode  (CR_CONFIG.agentMode === 'live'): every turn is POSTed to /api/agent/stream (site BFF → CES bridge →
 *            GECX AI Studio when agentBackend === 'ces' (CONTRACT v4), or → ADK agent on Cloud Run when 'adk'). The SSE events are applied in order:
 *              status → typing indicator label        text → streamed bot bubble (markdown-lite)
 *              action → window.CRClientTools[name](args) (same handlers as the UI buttons)
 *              chips  → quick-reply chips              done → session_id        error → friendly message
 * Mock mode  (ADK_AGENT_URL unset): a scripted local agent calls the SAME client handlers.
 *
 * Chat history (bubbles, chips, rich cards) and session_id persist in sessionStorage (cr_chat_v3).
 * Rich cards (cart / slots / confirm / order / dinner set) are rendered INSIDE #cr-chat-log by chat_rich.js +
 * chat_cards.js.
 *
 * Client handlers (names = CONTRACT client functions):
 *   show_products_on_page → Search.showProducts     show_comparison → Search.showComparison
 *   add_to_cart → Cart.add   get_cart → Cart.toAgent (+ cart card)   start_checkout → Checkout.open
 *   select_delivery_slot → Checkout.selectSlot + confirmPayment     place_order → Checkout.placeOrder(args.order_id)
 *   get_order_status → Orders.toAgent   show_store_availability → CRStores (stores.js registers it on CRClientTools)
 */
(function () {
  'use strict';
  const CR = window.CR;
  const CFG = window.CR_CONFIG || {};
  const MOCK = CFG.agentMode !== 'live';
  const ENDPOINT = CFG.agentEndpoint || '/api/agent/stream';
  const STORE = 'cr_chat_v3';
  const MAX_ITEMS = 120;
  const log = (...a) => console.log('[CR-CHAT]', ...a);
  const { esc } = CR;
  const WELCOME_CHIPS = ['Suggest dinner for tonight', 'Find a frying pan', 'Compare curry roux', "Where's my order?"];

  // =====================================================================
  // Client-side handlers
  // =====================================================================
  function parseArgs(args) {
    if (typeof args === 'string') { try { return JSON.parse(args); } catch (e) { return {}; } }
    if (args && typeof args === 'object' && args.args && Object.keys(args).length <= 2) return parseArgs(args.args);
    return args || {};
  }
  function toList(v) {
    if (Array.isArray(v)) return v.map((x) => (x && typeof x === 'object' ? (x.product_id || x.id || x.productId) : x)).filter((x) => x !== undefined && x !== null).map(String);
    if (typeof v === 'string') {
      const s = v.trim();
      if (s.startsWith('[')) { try { return toList(JSON.parse(s)); } catch (e) { /* fallthrough */ } }
      return s.split(/[\s,]+/).filter(Boolean);
    }
    return [];
  }
  const inflight = new Map();
  function once(name, args, ms, fn) {
    const key = name + ':' + JSON.stringify(args || {});
    const hit = inflight.get(key);
    const now = Date.now();
    if (hit && now - hit.at < ms) return hit.promise;
    const promise = Promise.resolve().then(fn);
    inflight.set(key, { at: now, promise });
    return promise;
  }

  const Tools = {
    async show_products_on_page(a) {
      a = parseArgs(a);
      const ids = toList(a.product_ids || a.productIds || a.ids);
      const reasons = Array.isArray(a.reasons) ? a.reasons : toList(a.reasons);
      // CONTRACT v2: optional theme + sections (+quantities) → grouped "dinner set" board + set card in the chat
      const args = { message: a.message || '', title: a.theme || a.title, theme: a.theme, sections: a.sections, product_ids: ids, reasons };
      return once('show', { ids, m: a.message, t: a.theme, s: !!a.sections }, 4000, () => window.Search.showProducts(args));
    },
    async show_comparison(a) {
      a = parseArgs(a);
      let rows = a.rows;
      if (typeof rows === 'string') { try { rows = JSON.parse(rows); } catch (e) { rows = []; } }
      return once('cmp', { ids: toList(a.product_ids), t: a.title }, 4000, () => window.Search.showComparison({ title: a.title, product_ids: toList(a.product_ids), rows: rows || [] }));
    },
    async add_to_cart(a) {
      a = parseArgs(a);
      const id = String(a.product_id || a.productId || a.id || '');
      const qty = Math.max(1, parseInt(a.quantity || a.qty, 10) || 1);
      return once('add', { id, qty }, 1500, async () => {
        const p = id && await CR.getProduct(id).catch(() => null);
        if (!p) return { ok: false, error: 'PRODUCT_NOT_FOUND', product_id: id };
        return window.Cart.add(p, qty);
      });
    },
    async get_cart() {
      if (window.ChatCards) window.ChatCards.showCart();
      return window.Cart.toAgent();
    },
    async start_checkout() { return once('start', {}, 2000, () => window.Checkout.open()); },
    async select_delivery_slot(a) {
      a = parseArgs(a);
      const id = String(a.slot_id || a.slotId || a.slot || '');
      return once('slot', { id }, 2000, () => {
        const r = window.Checkout.selectSlot(id);
        if (!r.ok) return r;
        const p = window.Checkout.confirmPayment();
        return { ok: true, slot_label: r.slot_label, payment_method: p.payment_method, total_tax_included: window.Checkout.totals().total, next: 'confirm' };
      });
    },
    /** CONTRACT v3: the agent generates the order id (args.order_id); the page must use it */
    async place_order(a) {
      a = parseArgs(a);
      const oid = String(a.order_id || a.orderId || '');
      return once('place', { oid }, 15000, () => window.Checkout.placeOrder(oid));
    },
    async get_order_status(a) {
      a = parseArgs(a);
      const r = window.Orders.toAgent(a.order_id || a.orderId || '');
      if (r && r.order_id && window.ChatCards) window.ChatCards.showOrder(r.order_id);
      return r;
    },
  };
  window.CRClientTools = Tools;

  // =====================================================================
  // page_state (CONTRACT v3 request body)
  // =====================================================================
  function pageState() {
    const c = CR.pageContext;
    const agentSet = c.view === 'agent' && document.querySelector('#cr-agent-view[data-mode="set"]');
    const view = c.product_id ? 'product' : agentSet ? 'set' : c.view;
    return {
      cart: window.Cart.lines().map((l) => ({ product_id: l.id, title: l.title, price: l.price, qty: l.qty })),
      checkout: { state: window.Checkout.state || 'idle', slot_id: (window.Checkout.slot && window.Checkout.slot.slot_id) || null },
      orders: window.Orders.all().slice(0, 5).map((o) => ({ order_id: o.id, status: window.Orders.statusOf(o).id, slot_label: o.slot ? o.slot.label : '' })),
      page_context: Object.assign({ view, query: c.query || null, product_id: c.product_id || null, product_title: c.product_title || null },
        lastVia === 'voice' ? { input_mode: 'voice' } : {}),
    };
  }

  // =====================================================================
  // persistence
  // =====================================================================
  const newSessionId = () => 's-' + (crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36) + Math.random().toString(36).slice(2));
  function loadStore() {
    try {
      const s = JSON.parse(sessionStorage.getItem(STORE) || 'null');
      if (s && s.session_id && Array.isArray(s.items)) return s;
    } catch (e) { /* ignore */ }
    return { session_id: newSessionId(), items: [] };
  }
  let store = loadStore();
  let restoring = false;
  function save() {
    if (store.items.length > MAX_ITEMS) store.items = store.items.slice(-MAX_ITEMS);
    try { sessionStorage.setItem(STORE, JSON.stringify(store)); } catch (e) { /* quota: drop oldest */ store.items = store.items.slice(-40); }
  }
  function record(item) { if (restoring) return; store.items.push(item); save(); }

  // =====================================================================
  // DOM
  // =====================================================================
  let panel, logEl, typingEl, statusEl, ta, sendBtn;
  const ICON_SEND = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3.4 20.4 20.85 12.9a1 1 0 0 0 0-1.83L3.4 3.6a.99.99 0 0 0-1.39.91L2 9.12c0 .5.37.93.87.99L17 12 2.87 13.88c-.5.07-.87.5-.87 1l.01 4.61c0 .71.73 1.2 1.39.91z"/></svg>';

  function scroll() {
    if (!logEl) return;
    requestAnimationFrame(() => { logEl.scrollTop = logEl.scrollHeight + 4000; });
  }
  function insert(node) {
    if (typingEl && typingEl.parentNode === logEl) logEl.insertBefore(node, typingEl); else logEl.appendChild(node);
    scroll();
    return node;
  }

  /** markdown-lite: **bold**, `code`, bullet / numbered lists, headings, line breaks (input is escaped first) */
  function inline(s) {
    return s.replace(/\*\*([^*]+?)\*\*/g, '<strong>$1</strong>').replace(/`([^`]+?)`/g, '<code>$1</code>');
  }
  function md(src) {
    const lines = esc(src || '').split(/\r?\n/);
    let html = '';
    let list = null;
    lines.forEach((raw) => {
      const m = raw.match(/^\s*(?:[-*•]|(\d+)[.)])\s+(.*)$/);
      if (m) {
        const tag = m[1] ? 'ol' : 'ul';
        if (list !== tag) { if (list) html += '</' + list + '>'; html += '<' + tag + '>'; list = tag; }
        html += '<li>' + inline(m[2]) + '</li>';
        return;
      }
      if (list) { html += '</' + list + '>'; list = null; }
      if (/^\s*#{1,4}\s+/.test(raw)) { html += '<p class="cr-md-h">' + inline(raw.replace(/^\s*#{1,4}\s+/, '')) + '</p>'; return; }
      if (raw.trim()) html += '<p>' + inline(raw) + '</p>';
    });
    if (list) html += '</' + list + '>';
    return html;
  }

  function addMsg(role, text, opts) {
    opts = opts || {};
    const d = document.createElement('div');
    d.className = 'cr-msg cr-msg--' + role + (opts.error ? ' is-error' : '');
    if (role === 'bot') d.innerHTML = md(text); else d.textContent = text;
    insert(d);
    if (!opts.noRecord && text) record({ t: 'msg', role, text, error: !!opts.error });
    return d;
  }
  function clearChips() { logEl.querySelectorAll('.cr-quick').forEach((q) => q.remove()); }
  function addChips(items, opts) {
    items = (items || []).map(String).filter(Boolean).slice(0, 6);
    if (!items.length) return null;
    clearChips();
    const q = document.createElement('div');
    q.className = 'cr-quick';
    q.setAttribute('role', 'group');
    q.setAttribute('aria-label', 'Suggested replies');
    items.forEach((t) => {
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'cr-quick__chip'; b.textContent = t;
      b.onclick = () => send(t);
      q.appendChild(b);
    });
    insert(q);
    if (!(opts && opts.noRecord)) record({ t: 'chips', items });
    return q;
  }
  function showTyping(text) {
    statusEl.textContent = text || 'Thinking…';
    typingEl.hidden = false;
    logEl.appendChild(typingEl);
    scroll();
  }
  function hideTyping() { typingEl.hidden = true; }

  // =====================================================================
  // turns
  // =====================================================================
  let chain = Promise.resolve();
  let busy = 0;
  let lastVia = 'text';
  const emit = (name, detail) => { try { window.dispatchEvent(new CustomEvent(name, { detail })); } catch (e) { /* ignore */ } };
  function setBusy(d) {
    busy = Math.max(0, busy + d);
    panel.classList.toggle('is-busy', busy > 0);
  }
  /** send a user message (queued: card buttons / chips may fire while a turn is still streaming) */
  function send(text, opts) {
    text = String(text || '').trim();
    if (!text) return Promise.resolve();
    opts = opts || {};
    if (!(opts.echo === false)) { clearChips(); addMsg('user', text); }
    lastVia = opts.via || 'text';
    // CONTRACT v6 voice hooks (voice.js): stop read-aloud on new input; read the reply when the turn ends
    emit('cr-chat-user-send', { text, via: lastVia });
    setBusy(1);
    const turnBox = { text: '' };
    chain = chain.then(() => (MOCK ? mockTurn(text, turnBox) : liveTurn(text, turnBox))).catch((e) => {
      console.error(e);
      const msg = 'Sorry, something went wrong. Please try again.';
      addMsg('bot', msg, { error: true });
      turnBox.text += msg;
    }).finally(() => { hideTyping(); setBusy(-1); emit('cr-chat-turn-end', { text: turnBox.text, via: lastVia, busy }); });
    return chain;
  }

  const ACTION_STATUS = {
    show_products_on_page: 'Showing recommendations on the page…', show_comparison: 'Building a comparison table…',
    add_to_cart: 'Adding to your cart…', get_cart: 'Checking your cart…', start_checkout: 'Getting checkout ready…',
    select_delivery_slot: 'Setting your delivery time…', place_order: 'Placing your order…', get_order_status: 'Checking delivery status…',
    show_store_availability: 'Checking store stock…',
  };

  async function applyAction(ev) {
    const name = ev.name;
    if (!Tools[name]) { console.warn('[CR-CHAT] unknown action', name); return; }
    log('action', name, ev.args);
    try {
      const res = await Tools[name](ev.args || {});
      log('→', name, res);
    } catch (e) { console.error('[CR-CHAT] action failed', name, e); }
  }

  async function liveTurn(text, turnBox) {
    turnBox = turnBox || { text: '' };
    showTyping('Thinking…');
    const turn = { bubble: null, text: '', chips: null, gotAny: false };
    const finishBubble = () => {
      if (turn.bubble && turn.text) record({ t: 'msg', role: 'bot', text: turn.text });
      turn.bubble = null; turn.text = '';
    };
    const handle = async (ev) => {
      turn.gotAny = true;
      switch (ev.type) {
        case 'status': showTyping(ev.text); break;
        case 'text':
          if (!ev.text) break;
          hideTyping();
          if (!turn.bubble) { turn.bubble = addMsg('bot', '', { noRecord: true }); turn.bubble.classList.add('is-streaming'); }
          turn.text += ev.text;
          turn.bubble.innerHTML = md(turn.text);
          turnBox.text += ev.text;
          emit('cr-chat-bot-delta', { text: turnBox.text });
          scroll();
          break;
        case 'action':
          // the agent computed the result server-side; failed results (e.g. start_checkout EMPTY_CART) change nothing
          if (ev.result && ev.result.ok === false) { log('skip action (agent result not ok)', ev.name, ev.result); break; }
          finishBubble();
          showTyping(ACTION_STATUS[ev.name] || 'Working on it…');
          await applyAction(ev);
          break;
        case 'chips': turn.chips = ev.items || []; break;
        case 'done':
          if (ev.session_id) { store.session_id = ev.session_id; save(); }
          turn.timings = ev.timings ? Object.assign({ model: ev.model || '' }, ev.timings) : null;
          break;
        case 'error': finishBubble(); hideTyping(); addMsg('bot', ev.message || 'Sorry, something went wrong.', { error: true }); turnBox.text += '\n' + (ev.message || ''); break;
        default: break;
      }
    };
    let res;
    try {
      res = await fetch(ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
        body: JSON.stringify({ session_id: store.session_id, message: text, visitor_id: CR.getVisitorId(), page_state: pageState() }),
      });
    } catch (e) {
      hideTyping();
      addMsg('bot', "We couldn't connect to the network. Please check your connection and try again.", { error: true });
      return;
    }
    if (!res.ok || !res.body) {
      hideTyping();
      addMsg('bot', "We couldn't reach the AI Concierge (" + res.status + '). Please try again in a moment.', { error: true });
      return;
    }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    const flush = async (block) => {
      const data = block.split(/\r?\n/).filter((l) => l.startsWith('data:')).map((l) => l.slice(5).replace(/^ /, '')).join('\n');
      if (!data) return;
      let ev;
      try { ev = JSON.parse(data); } catch (e) { console.warn('[CR-CHAT] bad SSE data', data.slice(0, 120)); return; }
      await handle(ev);
    };
    for (;;) {
      const { value, done } = await reader.read();
      if (value) buf += dec.decode(value, { stream: true });
      let m;
      while ((m = buf.match(/\r?\n\r?\n/))) {
        const block = buf.slice(0, m.index);
        buf = buf.slice(m.index + m[0].length);
        await flush(block);
      }
      if (done) break;
    }
    if (buf.trim()) await flush(buf);
    if (turn.bubble) {
      turn.bubble.classList.remove('is-streaming');
      // debug-only latency tooltip (done.timings)
      if (turn.timings) turn.bubble.title = Object.keys(turn.timings).map((k) => k + ': ' + turn.timings[k]).join('\n');
    }
    finishBubble();
    hideTyping();
    if (!turn.gotAny) addMsg('bot', "Sorry, we didn't get a response. Please try again.", { error: true });
    if (turn.chips && turn.chips.length) addChips(turn.chips);
  }

  // =====================================================================
  // MOCK agent (ADK_AGENT_URL unset) — calls the SAME client handlers
  // =====================================================================
  async function tool(name, args) {
    showTyping(ACTION_STATUS[name] || 'Working on it…');
    return Tools[name](args || {});
  }
  async function mockTurn(text, turnBox) {
    showTyping('Thinking…');
    await new Promise((r) => setTimeout(r, 250));
    const r = await mockAgent(text);
    hideTyping();
    addMsg('bot', r.text);
    if (turnBox) { turnBox.text = r.text; emit('cr-chat-bot-delta', { text: r.text }); }
    if (r.quick) addChips(r.quick);
  }
  async function mockAgent(text) {
    const slot = /day after tomorrow.*(afternoon|14|2 ?pm)/i.test(text) ? 'd2_pm' : /day after tomorrow/i.test(text) ? 'd2_am'
      : /evening|\bnight\b|18|6 ?pm/i.test(text) ? 'd1_eve' : /afternoon|14|2 ?pm/i.test(text) ? 'd1_pm' : /tomorrow|morning|9:00|9 ?am/i.test(text) ? 'd1_am' : '';
    if (/in[- ]?stores?\b|pick ?up|which stores?|nearby stores?|stores? near|store (stock|availability)|where (can|do) i (buy|get|find)/i.test(text) && Tools.show_store_availability) {
      return mockStoreAvailability(text);
    }
    if (/dinner|supper|tonight|meal plan|menu/i.test(text) && !/(set|bundle).*(cart|add)/i.test(text)) return mockDinnerSet(text);
    if (/order.*(status|where)|where.*order|deliver|track|arriv/i.test(text) && !/place|confirm/i.test(text) && !slot) {
      const s = await tool('get_order_status', {});
      return s.order_id ? { text: 'Your order **' + s.order_id + '** is currently "' + s.status_label + '".\nEstimated delivery: ' + s.eta } : { text: "You don't have any orders yet." };
    }
    if (/(set|bundle).*(cart|add)|add.*(set|bundle)/i.test(text) && window.ChatCards) {
      const n = window.ChatCards.addSetToCart(window.ChatCards.lastSet() || {});
      return n ? { text: 'Added ' + n + ' items from the set to your cart.', quick: ['Proceed to checkout'] } : { text: 'Let me suggest a dinner set first. Try asking "Suggest dinner for tonight".' };
    }
    if (/place.*order|confirm|go ahead|submit order/i.test(text) || (/^(yes|please|ok(ay)?)\b/i.test(text.trim()) && !slot)) {
      const r = await tool('place_order', {});
      return r.ok ? { text: 'Your order is placed (**' + r.order_id + '**). I\'ll keep you posted on delivery with the tracking card.', quick: ["Where's my order?"] }
        : { text: r.error === 'NO_SLOT' ? 'Please choose a delivery time first.' : 'Your cart is empty.' };
    }
    if (slot) {
      const r = await tool('select_delivery_slot', { slot_id: slot });
      return r.ok ? { text: 'Delivery set for "' + r.slot_label + '". Please review your order.', quick: ['Yes, place my order'] }
        : { text: "Your cart is empty, so I can't pick a delivery time yet." };
    }
    if (/checkout|check out|pay|purchase|buy now/i.test(text)) {
      const r = await tool('start_checkout', {});
      return r.ok ? { text: 'Please choose a delivery time.', quick: r.slots.slice(0, 3).map((s) => s.label) }
        : { text: 'Your cart is empty.' };
    }
    if (/(show|view|see|check|what'?s in).*cart|cart.*(content|items)|^cart$/i.test(text.trim())) {
      const c = await tool('get_cart', {});
      return { text: "Here's your cart (" + c.cart_count + (c.cart_count === 1 ? ' item' : ' items') + ').', quick: ['Proceed to checkout'] };
    }
    if (/add|to (my |the )?cart|put .* in/i.test(text)) {
      const first = window.Search.state.results[0] || (await CR.fetchJSON('/api/search?q=' + encodeURIComponent('curry') + '&page_size=1')).results[0];
      const shown = document.querySelector('#cr-agent-view .cr-card');
      const id = shown ? shown.dataset.pid : first && first.id;
      if (!id) return { text: "I couldn't find a product to add." };
      const r = await tool('add_to_cart', { product_id: id, quantity: 1 });
      return { text: 'Added "' + r.title + '" to your cart.', quick: ['Proceed to checkout', 'Suggest dinner for tonight'] };
    }
    if (/compar|vs\.?|versus/i.test(text)) {
      showTyping('Searching products…');
      const res = await CR.fetchJSON('/api/search?q=' + encodeURIComponent('curry roux') + '&page_size=3');
      const ids = res.results.map((p) => p.id);
      await tool('show_comparison', { title: 'Curry roux comparison', product_ids: ids, rows: [{ label: 'Size', values: res.results.map((p) => p.size || '-') }, { label: 'Brand', values: res.results.map((p) => p.brand) }] });
      return { text: 'I put **' + ids.length + ' popular curry roux** side by side in a comparison table.', quick: ['Add to cart'] };
    }
    const allergy = /shrimp|allerg/i.test(text);
    const cheap = /cheap|sale|deal|discount|bargain/i.test(text);
    let q = text.replace(/[?!.,]/g, ' ')
      .replace(/\b(please|can you|could you|i want|i need|i'?m looking for|looking for|show me|find( me)?|search( for)?|recommend(ations)?|suggest|some|a|an|the|me|any|cheap|on sale|sale|deals?|discount(ed)?|without|no|shrimp|allerg(y|ies|ic)|free|for|my|kids?)\b/gi, ' ')
      .replace(/\s+/g, ' ').trim() || 'curry';
    showTyping('Searching products…');
    const res = await CR.fetchJSON('/api/search?q=' + encodeURIComponent(q) + '&page_size=12');
    let hits = res.results || [];
    if (allergy) hits = hits.filter((p) => !/shrimp/i.test([p.title, (p.allergens || []).join(' '), p.description || ''].join(' ')));
    if (cheap) hits = hits.slice().sort((x, y) => (y.on_sale ? 1 : 0) - (x.on_sale ? 1 : 0) || (x.price || 0) - (y.price || 0));
    if (!hits.length) return { text: 'I couldn\'t find anything matching "' + q + '". Try describing it a different way.' };
    const ids = hits.slice(0, 6).map((p) => p.id);
    const note = (allergy ? ' (shrimp-free)' : '') + (cheap ? ' (best deals first)' : '');
    await tool('show_products_on_page', { message: 'Picks for "' + q + '"' + note + '.', product_ids: ids, reasons: hits.slice(0, 6).map((p) => (p.on_sale ? 'On sale • ' : '') + 'Rated ★' + p.rating) });
    return { text: 'I put **' + ids.length + ' recommendations** for "' + q + '"' + note + ' on the main screen.', quick: ['Add to cart', 'Compare them', 'Proceed to checkout'] };
  }

  // mock of find_store_stock → show_store_availability (CONTRACT_EN v14)
  async function mockStoreAvailability(text) {
    let ids = [];
    const pid = CR.pageContext && CR.pageContext.product_id;
    let q = text.replace(/[?!.,]/g, ' ')
      .replace(/\b(where|can|could|do|does|i|we|buy|get|find|pick ?up|pickup|is|are|it|this|that|in|at|a|an|the|my|me|stores?|shops?|nearby|near|which|available|availability|stock|have|has|any|please|you|for|to|one|of|cymbal|retail)\b/gi, ' ')
      .replace(/\s+/g, ' ').trim();
    if (!q && pid) ids = [pid];
    if (!ids.length) {
      const shown = document.querySelector('#cr-agent-view .cr-card');
      if (!q && shown) ids = [shown.dataset.pid];
    }
    if (!ids.length) {
      showTyping('Searching products…');
      const res = await CR.fetchJSON('/api/search?q=' + encodeURIComponent(q || 'curry roux') + '&page_size=6');
      const hits = (res.results || []).filter((p) => String(p.channel || 'omni') !== 'online_only');
      ids = hits.slice(0, 2).map((p) => p.id);
    }
    if (!ids.length) return { text: 'I couldn\'t find that item in our stores. Try another product name.' };
    const r = await tool('show_store_availability', { product_ids: ids, message: 'Store stock' + (q ? ' for "' + q + '"' : '') + ' at Cymbal Retail stores.' });
    const items = r.items || [];
    const lines = items.map((it) => {
      const inStock = (it.stores || []).filter((s) => s.availability !== 'OUT_OF_STOCK' && Number(s.quantity) > 0);
      return '- **' + (it.title || it.product_id) + '**: ' + (inStock.length ? 'in stock at ' + inStock.map((s) => s.name.replace(/^Cymbal Retail /, '')).join(', ') : 'currently out of stock in stores') +
        (it.channel === 'store_only' ? ' (in-store only)' : '');
    });
    return { text: 'Here\'s where you can find it in store:\n' + lines.join('\n') + '\nI opened the store details on the page.', quick: ['Suggest dinner for tonight', 'View my cart'] };
  }

  // mock of recommend_dinner_set → show_products_on_page(theme, sections) using real catalog ids from /api/search
  async function mockDinnerSet() {
    const ROLES = [
      { label: 'Main', qs: ['salmon fillet', 'salmon', 'fish fillet'], ok: (p) => /salmon|cod|mackerel/i.test(p.title) && !/flake|bone|baby/i.test(p.title), tags: ['Frequently bought', 'Family favorite'], reason: 'In-season salmon, a family favorite main' },
      { label: 'Side', qs: ['hijiki', 'kinpira', 'spinach'], ok: (p) => /deli|vegetable/i.test(p.category), tags: ['Quick'], reason: 'Just heat and serve for an extra dish' },
      { label: 'Soup', qs: ['pork miso soup', 'kenchin soup', 'miso soup'], ok: (p) => /soup/i.test(p.title) && !/\bfor\b|stock|base/i.test(p.title), tags: ['Family favorite'], reason: 'A warming classic soup' },
      { label: 'Staple', qs: ['mixed rice', 'cooked rice', 'rice'], ok: (p) => /rice|deli/i.test(p.category), tags: ['Frequently bought'], reason: 'Your usual staple, all in one order' },
      { label: 'Dessert & fruit', qs: ['mandarin', 'persimmon', 'apple'], ok: (p) => /fruit/i.test(p.category), tags: [], reason: 'Refreshing seasonal fruit for after dinner' },
    ];
    showTyping('Checking your purchase history and family preferences…');
    const used = new Set();
    const sections = [];
    for (const r of ROLES) {
      let pick = null;
      for (const q of r.qs) {
        const res = await CR.fetchJSON('/api/search?q=' + encodeURIComponent(q) + '&page_size=10').catch(() => ({ results: [] }));
        const cands = (res.results || []).filter((p) => !used.has(p.id) && !/bulk|food ?service|commercial/i.test(p.title) && p.availability !== 'OUT_OF_STOCK' && !/shrimp/i.test(p.title));
        pick = cands.find(r.ok) || null;
        if (pick) break;
      }
      if (!pick) continue;
      used.add(pick.id);
      const tags = r.tags.slice();
      if (pick.on_sale) tags.push('On sale');
      if ((pick.labels || []).some((l) => /season/i.test(l))) tags.push('In season');
      sections.push({ label: r.label, product_ids: [pick.id], reasons: [(pick.on_sale ? "This week's deal • " : '') + r.reason], tags: [Array.from(new Set(tags))] });
    }
    if (!sections.length) return { text: "Sorry, I couldn't put together a dinner set." };
    const name = String(CR.getCustomer().name || '').split(' ')[0];
    await tool('show_products_on_page', {
      theme: (name ? name + "'s family dinner tonight" : "Tonight's family dinner") + ' (autumn salmon set meal)',
      message: "Built from your frequently bought items, your family's preferences, and this week's deals and in-season picks. Shrimp-free.",
      product_ids: sections.flatMap((s) => s.product_ids), reasons: sections.flatMap((s) => s.reasons), sections,
    });
    return { text: "Tonight's dinner set is ready (" + sections.length + ' items).', quick: ['Add the set to my cart', 'Proceed to checkout'] };
  }

  // =====================================================================
  // open / close / reset / restore
  // =====================================================================
  function greet() {
    const name = String(CR.getCustomer().name || '').split(' ')[0];
    addMsg('bot', 'Hi ' + name + "! I'm the Cymbal Retail **AI Concierge**.\nI can help you find products, plan tonight's dinner, place orders, and check on your delivery.");
    addChips(WELCOME_CHIPS);
  }
  function isOpen() { return document.body.classList.contains('chat-open'); }
  function openChat() {
    if (!panel) return;
    document.body.classList.add('chat-open');
    panel.setAttribute('aria-hidden', 'false');
    if (!store.items.length) greet();
    scroll();
    setTimeout(() => { if (ta && window.matchMedia('(pointer:fine)').matches) ta.focus({ preventScroll: true }); }, 220);
  }
  function closeChat() {
    document.body.classList.remove('chat-open');
    if (panel) panel.setAttribute('aria-hidden', 'true');
  }
  function reset() {
    store = { session_id: newSessionId(), items: [] };
    save();
    Array.from(logEl.children).forEach((c) => { if (c !== typingEl) c.remove(); });
    hideTyping();
    window.dispatchEvent(new CustomEvent('cr-chat-reset'));
    greet();
  }
  function restore() {
    if (!store.items.length) return;
    restoring = true;
    try {
      const lastChips = store.items.map((x) => x.t).lastIndexOf('chips');
      store.items.forEach((it, i) => {
        if (it.t === 'msg') addMsg(it.role, it.text, { noRecord: true, error: it.error });
        else if (it.t === 'chips' && i === lastChips && i === store.items.length - 1) addChips(it.items, { noRecord: true });
        else if (it.t === 'card' && window.ChatCards) window.ChatCards.restoreCard(it);
      });
    } catch (e) { console.warn('[CR-CHAT] restore failed', e); }
    restoring = false;
  }
  /** called by chat_rich.js for every card appended (kind + serializable data) */
  function recordCard(kind, data) { if (kind) record(Object.assign({ t: 'card', kind }, data || {})); }

  // =====================================================================
  // input (IME-safe Enter)
  // =====================================================================
  const IME = { composing: false, lastEnd: 0, GRACE_MS: 120 };
  function autoGrow() { ta.style.height = 'auto'; ta.style.height = Math.min(140, ta.scrollHeight) + 'px'; sendBtn.disabled = !ta.value.trim(); }
  function submit() {
    const v = ta.value;
    if (!v.trim()) return;
    ta.value = '';
    autoGrow();
    send(v);
  }
  function bindInput() {
    ta.addEventListener('compositionstart', () => { IME.composing = true; });
    ta.addEventListener('compositionend', () => { IME.composing = false; IME.lastEnd = performance.now(); });
    ta.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter' || e.shiftKey) return;
      // Enter that confirms an IME (CJK input method) conversion must not send (Chrome: isComposing / 229, Safari: grace period)
      if (e.isComposing || e.keyCode === 229 || IME.composing) return;
      e.preventDefault();
      if (performance.now() - IME.lastEnd < IME.GRACE_MS) return;
      submit();
    });
    ta.addEventListener('input', autoGrow);
    panel.querySelector('#cr-chat-form').addEventListener('submit', (e) => { e.preventDefault(); submit(); });
    autoGrow();
  }

  window.CRChat = {
    open: openChat, close: closeChat, isOpen, mock: MOCK, tools: Tools, send, reset, recordCard, pageState, md,
    isBusy: () => busy > 0,
    /** programmatic message (card buttons / product modal): opens the panel and sends */
    ask(text, opts) { openChat(); return send(text, opts); },
    syncVariables() {},
  };
  /** mock-mode helpers kept for chat_cards.js / tests */
  window.CRMockChat = { send: (t) => send(t), echo: (t) => { clearChips(); addMsg('user', t); } };

  function boot() {
    panel = document.getElementById('cr-chat');
    logEl = document.getElementById('cr-chat-log');
    typingEl = document.getElementById('cr-chat-typing');
    statusEl = document.getElementById('cr-chat-status');
    ta = document.getElementById('cr-chat-input');
    sendBtn = document.getElementById('cr-chat-send');
    sendBtn.innerHTML = ICON_SEND;
    const sub = document.getElementById('cr-chat-sub');
    const platform = CFG.agentPlatform || (CFG.agentBackend === 'adk' ? 'Agent Development Kit' : 'GECX AI Studio');
    if (sub) sub.textContent = MOCK ? 'Local mock (agent not connected)' : 'Gemini • ' + platform;
    const powered = document.getElementById('cr-powered-agent');
    if (powered && !MOCK) powered.textContent = platform;
    panel.dataset.mode = MOCK ? 'mock' : 'live';
    bindInput();
    document.getElementById('cr-fab').addEventListener('click', openChat);
    document.getElementById('cr-chat-close').addEventListener('click', closeChat);
    document.getElementById('cr-chat-reset').addEventListener('click', reset);
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && isOpen() && document.getElementById('cr-product-modal').hidden && !document.querySelector('#cr-drawer.is-open')) closeChat();
    });
    restore();
    log('mode =', MOCK ? 'MOCK (ADK_AGENT_URL unset)' : 'LIVE ' + ENDPOINT, 'session', store.session_id);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
