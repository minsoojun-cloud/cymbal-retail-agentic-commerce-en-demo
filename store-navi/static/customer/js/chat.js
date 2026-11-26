/* AI Concierge chat: SSE v3 events (status/text/action/chips/done/error) from /api/agent/stream.
   - show_store_items → recommendation card (numbered items, "Find all on the map", "Buy all")
   - show_recipes     → dish-first recipe cards (window.Recipes)
   - done.trace       → collapsible "How I answered" (data sources / tools used)
   - phone: bottom sheet; tablet portrait (>=768px): left pane over the list; landscape (>=1024px): persistent right pane.
   Voice input (window.Voice: Web Speech en-US, Gemini transcription fallback). Fixed disclaimer for health answers. */
(function () {
  'use strict';
  const { esc, yen, km, drugBadges, img, CFG, DISCLAIMER, ICON_INFO, statusPill, channelBadge, buyOnlineLink } = window.SN;
  const TRACE_IC = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19.43 12.98c.04-.32.07-.64.07-.98s-.03-.66-.07-.98l2.11-1.65a.5.5 0 0 0 .12-.64l-2-3.46a.5.5 0 0 0-.61-.22l-2.49 1a7.3 7.3 0 0 0-1.69-.98l-.38-2.65A.49.49 0 0 0 14 2h-4c-.25 0-.46.18-.49.42l-.38 2.65c-.61.25-1.17.59-1.69.98l-2.49-1a.5.5 0 0 0-.61.22l-2 3.46a.5.5 0 0 0 .12.64l2.11 1.65c-.04.32-.07.65-.07.98s.03.66.07.98l-2.11 1.65a.5.5 0 0 0-.12.64l2 3.46c.12.22.39.3.61.22l2.49-1c.52.4 1.08.73 1.69.98l.38 2.65c.03.24.24.42.49.42h4c.25 0 .46-.18.49-.42l.38-2.65c.61-.25 1.17-.59 1.69-.98l2.49 1c.23.09.49 0 .61-.22l2-3.46a.5.5 0 0 0-.12-.64l-2.11-1.65zM12 15.5a3.5 3.5 0 1 1 0-7 3.5 3.5 0 0 1 0 7z"/></svg>';
  const MIC = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 14a3 3 0 0 0 3-3V5a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3zm5.3-3a5.3 5.3 0 0 1-10.6 0H5a7 7 0 0 0 6 6.92V21h2v-3.08A7 7 0 0 0 19 11h-1.7z"/></svg>';
  const mq = (q) => window.matchMedia(q).matches;
  function layout() { return mq('(min-width: 1024px)') ? 'wide' : mq('(min-width: 768px)') ? 'tablet' : 'phone'; }
  const FIND_ALL = 'Find all on the map';
  const IN = ['in_stock', 'low_stock'];
  const WARN = '<svg viewBox="0 0 24 24"><path d="M1 21h22L12 2 1 21zm12-3h-2v-2h2v2zm0-4h-2v-4h2v4z"/></svg>';
  const ROUTE = '<svg viewBox="0 0 24 24"><path d="M13.5 5.5c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zM9.8 8.9 7 23h2.1l1.8-8 2.1 2v6h2v-7.5l-2.1-2 .6-3C14.8 12 16.8 13 19 13v-2c-1.9 0-3.5-1-4.3-2.4l-1-1.6c-.4-.6-1-1-1.7-1-.3 0-.5.1-.8.1L6 8.3V13h2V9.6l1.8-.7"/></svg>';
  let busy = false;
  let started = false;
  const recs = [];

  const $ = (id) => document.getElementById(id);
  function sessionId() {
    const S = window.SNC.state;
    const k = 'sn_chat_' + (S.customer ? S.customer.customer_id : '') + '_' + S.storeId;
    let v = sessionStorage.getItem(k);
    if (!v) { v = 'sn-' + Math.random().toString(36).slice(2) + Date.now().toString(36); sessionStorage.setItem(k, v); }
    return v;
  }
  function md(s) {
    return esc(s).replace(/^\s*[-*_]{3,}\s*$/gm, '').replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1$2').replace(/\n{3,}/g, '\n\n').replace(/^\*+|\*+$/g, '');
  }
  function scroll() { const log = $('chat-log'); log.scrollTop = log.scrollHeight; }
  function add(el) { $('chat-log').appendChild(el); scroll(); return el; }
  function bubble(cls, html) { const d = document.createElement('div'); d.className = 'msg ' + cls; d.innerHTML = html; return add(d); }

  function welcome() {
    const S = window.SNC.state;
    const s = window.SNC.storeById(S.storeId) || {};
    const name = S.customer ? String(S.customer.name || '').split(' ')[0] : '';
    $('chat-log').innerHTML = '<div class="chat__welcome"><h3>Hi <span>' + esc(name) + '</span>, how can I help?</h3>' +
      '<p>I can find products for a need or symptom, suggest recipes, and show stock and shelves at ' + esc(s.short_name || 'this store') + '.</p></div>';
    chips(["I've had a fever since yesterday and feel like I'm getting a cold", 'Dinner ideas the kids will like', 'My eyes are tired',
      'Did I forget anything?', 'Where is the oral rehydration solution?']);
    started = true;
  }
  function chips(items) {
    const box = $('chat-chips');
    box.innerHTML = (items || []).map((t) => '<button type="button" class="sn-chip" data-chip="' + esc(t) + '">' + esc(t) + '</button>').join('');
    box.querySelectorAll('[data-chip]').forEach((b) => b.addEventListener('click', () => {
      const t = b.dataset.chip;
      if (/^find all on the map$/i.test(t) && recs.length) { findAll(recs[recs.length - 1]); return; }
      if (/did i forget/i.test(t) && window.Forgot) { forgotCard(t); return; }
      send(t);
    }));
  }

  function whereHTML(it) {
    const av = it.availability || {};
    const c = av.current || {};
    if (IN.indexOf(c.status) >= 0) {
      return statusPill(c.status, c.quantity) + '<span>' + esc(window.Search.shelfText(c.shelf)) + '</span>';
    }
    const near = (av.nearby || []).find((n) => IN.indexOf(n.status) >= 0);
    const p = it.product || {};
    if (window.SN.channelOf(p) === 'online_only') return channelBadge(p) + buyOnlineLink(p.id, 'Online only — Buy online');
    let h = statusPill(c.status === 'not_carried' ? 'not_carried' : 'out_of_stock') + '<span>at this store</span>';
    if (near) h += '<span>→ in stock at ' + esc(near.short_name) + ' (' + esc(km(near.distance_m)) + ')</span>';
    if ((av.online || {}).available) h += buyOnlineLink(p.id);
    return h;
  }

  function recCard(args) {
    recs.push(args);
    const items = args.items || [];
    const inStore = items.filter((it) => it.in_store);
    const el = document.createElement('article');
    el.className = 'rec';
    el.innerHTML = '<div class="rec__head"><div class="rec__theme">' + esc(args.theme || 'Recommended products') + '</div>' +
      '<div class="rec__store">' + esc(args.store_name || '') + (args.customer_name ? ' • for ' + esc(String(args.customer_name).split(' ')[0]) : '') + '</div>' +
      (args.advice ? '<div class="rec__advice">' + md(args.advice) + '</div>' : '') + '</div>' +
      (args.red_flag ? '<div class="rec__flag">' + WARN + '<span>We recommend seeing a doctor. No medicines are shown (support items only until you see a doctor). If unsure, call #7119 for medical advice; in an emergency call 119.</span></div>' : '') +
      '<ol class="rec__items">' + items.map((it) => {
        const p = it.product || {};
        const notes = (it.notes || []).filter((n) => n.length > 8);
        return '<li class="rec-item"><span class="rec-item__no' + (it.no ? '' : ' is-off') + '">' + (it.no || 'Other') + '</span>' + img(p.image, p.title) +
          '<div><div class="rec-item__role">' + esc(it.role || '') + '</div><div class="rec-item__title">' + esc(p.title || '') + '</div>' +
          '<div class="rec-item__meta"><b>' + yen(p.price) + '</b>' + drugBadges(p) + channelBadge(p) + '<span class="rec-item__cart">' + window.SN.cartBtn(p.id, window.Cart.has(p.id)) + '</span></div>' +
          (it.reason ? '<div class="rec-item__reason">' + esc(it.reason) + '</div>' : '') +
          '<div class="rec-item__where">' + whereHTML(it) + '</div>' +
          (notes.length ? '<div class="rec-item__notes">' + notes.map((n) => '<div class="sn-notice' + (/Pharmacist-only|one per customer/i.test(n) ? ' sn-notice--warn' : '') + '">' + ICON_INFO + '<span>' + esc(n) + '</span></div>').join('') + '</div>' : '') +
          '</div></li>';
      }).join('') + '</ol>' +
      (args.route && args.route.length_m ? '<div class="rec__route">' + ROUTE + 'From the entrance, in number order to the checkout: about ' + Math.round(args.route.length_m) + ' m</div>' : '') +
      (items.length ? '<div class="rec__actions"><button type="button" class="sn-btn sn-btn--primary" data-act="find"' + (inStore.length ? '' : ' disabled') + '>' + FIND_ALL + '</button>' +
        '<button type="button" class="sn-btn sn-btn--tonal" data-act="buy">Buy all</button></div>' : '');
    el.querySelectorAll('[data-cart]').forEach((btn) => btn.addEventListener('click', () => {
      const it = items.find((x) => x.product && x.product.id === btn.dataset.cart);
      if (it) window.SN.setCartBtn(btn, window.Cart.toggle({ product: it.product, availability: it.availability, role: it.role }));
    }));
    el.querySelector('[data-act=find]') && el.querySelector('[data-act=find]').addEventListener('click', () => findAll(args));
    el.querySelector('[data-act=buy]') && el.querySelector('[data-act=buy]').addEventListener('click', () => {
      window.Cart.open(items.map((it) => ({ product: it.product, availability: it.availability, role: it.role })), 'store');
    });
    add(el);
  }
  function findAll(args) {
    const items = (args.items || []).filter((it) => it.in_store);
    if (!items.length) { window.SN.toast('None of these items are in stock at this store'); return; }
    close();
    window.FloorMap.open({ storeId: args.store_id || window.SNC.state.storeId, items, route: args.route });
  }
  function recipesCard(args) {
    const el = document.createElement('div');
    el.className = 'chat-recipes';
    add(el);
    if (window.Recipes && window.Recipes.renderInline) {
      Promise.resolve(window.Recipes.renderInline(el, args.query || '')).then(scroll).catch(() => { el.remove(); });
    } else { el.remove(); }
  }
  async function forgotCard(label) {
    if (busy) return;
    if (!started) welcome();
    const w = $('chat-log').querySelector('.chat__welcome');
    if (w) w.remove();
    bubble('msg--user', esc(label));
    const el = document.createElement('div');
    el.className = 'chat-forgot';
    add(el);
    try {
      const items = await window.Forgot.check();
      window.Forgot.renderInto(el, items, { title: 'Did you forget?', emptyText: 'Looks like you have everything on your usual list.' });
      traceCard([{ label: 'Purchase history', detail: 'Replenishment cycles from your past purchases', source_label: 'BigQuery purchase history' },
        { label: 'Cart & recipes', detail: 'Recipe ingredients compared with your cart', source_label: 'Store app cart + recipe data' },
        { label: 'Stock check', detail: 'In-store availability for each suggestion', source_label: 'Store inventory' }]);
    } catch (e) { el.textContent = 'Could not check right now.'; }
    chips(['Dinner ideas', FIND_ALL]);
    scroll();
  }
  function traceCard(rows) {
    if (!rows || !rows.length) return;
    const d = document.createElement('details');
    d.className = 'trace';
    d.innerHTML = '<summary>' + TRACE_IC + '<span>How I answered</span><small>' + rows.length + ' step' + (rows.length > 1 ? 's' : '') + '</small></summary>' +
      '<ol class="trace__list">' + rows.map((r) => '<li><b>' + esc(r.label || r.tool || '') + '</b>' +
        (r.detail ? '<span class="trace__detail">' + esc(r.detail) + '</span>' : '') +
        (r.source_label ? '<span class="trace__src">' + esc(r.source_label) + '</span>' : '') + '</li>').join('') + '</ol>';
    add(d);
  }
  function disclaimer() {
    const d = document.createElement('div');
    d.className = 'disclaimer';
    d.textContent = DISCLAIMER;
    add(d);
  }

  async function send(text) {
    text = (text || '').trim();
    if (!text || busy) return;
    busy = true;
    $('chat-send').disabled = true;
    $('chat-chips').innerHTML = '';
    if (!started) welcome();
    const w = $('chat-log').querySelector('.chat__welcome');
    if (w) w.remove();
    bubble('msg--user', esc(text));
    const typing = bubble('msg--bot is-typing', '<span class="dots"><i></i><i></i><i></i></span><span class="lbl">Thinking…</span>');
    let cur = null, buf = '', health = false, gotChips = false, trace = null;
    const S = window.SNC.state;
    try {
      const resp = await fetch(CFG.agentEndpoint || '/api/agent/stream', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin',
        body: JSON.stringify({ session_id: sessionId(), message: text, store_id: S.storeId,
          page_state: { page_context: { view: S.view, query: (location.hash.match(/q=([^&]+)/) || [])[1] ? decodeURIComponent(location.hash.match(/q=([^&]+)/)[1]) : '' } } }),
      });
      if (!resp.ok || !resp.body) throw new Error('HTTP ' + resp.status);
      const reader = resp.body.getReader();
      const dec = new TextDecoder();
      let pending = '';
      const handle = (ev) => {
        if (ev.type === 'status') { const l = typing.querySelector('.lbl'); if (l) l.textContent = ev.text; }
        else if (ev.type === 'text') {
          if (!cur) { typing.remove(); cur = bubble('msg--bot', ''); }
          buf += ev.text;
          cur.innerHTML = md(buf);
          if (/doctor|pharmacist|medicine|#7119|\b119\b/i.test(ev.text)) health = true;
          scroll();
        } else if (ev.type === 'action' && ev.name === 'show_store_items') {
          if (ev.result && ev.result.ok === false) return;
          typing.remove();
          recCard(ev.args || {});
          health = true;
          cur = null; buf = '';
        } else if (ev.type === 'action' && ev.name === 'show_recipes') {
          typing.remove();
          recipesCard(ev.args || {});
          cur = null; buf = '';
        } else if (ev.type === 'chips') { chips(ev.items || []); gotChips = true; }
        else if (ev.type === 'error') { typing.remove(); bubble('msg--bot msg--error', esc(ev.message || 'Something went wrong')); }
        else if (ev.type === 'done') { if ((ev.red_flags || []).length) health = true; trace = ev.trace || null; }
      };
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        pending += dec.decode(value, { stream: true });
        let i;
        while ((i = pending.indexOf('\n\n')) >= 0) {
          const chunk = pending.slice(0, i); pending = pending.slice(i + 2);
          chunk.split('\n').forEach((line) => {
            if (!line.startsWith('data:')) return;
            try { handle(JSON.parse(line.slice(5).trim())); } catch (e) { /* ignore */ }
          });
        }
      }
      typing.remove();
      if (health) disclaimer();
      if (trace) traceCard(trace);
      if (!gotChips) chips(['I feel feverish', 'Dinner ideas']);
    } catch (e) {
      typing.remove();
      bubble('msg--bot msg--error', "Sorry, I couldn't connect. Please try again.");
    } finally {
      busy = false;
      $('chat-send').disabled = false;
      scroll();
    }
  }

  function open(prefill) {
    window.SNC.openSheet('chat');            // phone: bottom sheet · tablet/desktop: right drawer (CSS)
    document.body.classList.add('chat-open');
    if (!started) welcome();
    $('chat-sub').textContent = 'Gemini • Agent Development Kit' + (CFG.agentMode === 'mock' ? ' (demo mode)' : '');
    if (prefill) send(prefill);
    else setTimeout(() => $('chat-input').focus({ preventScroll: true }), 250);
  }
  function close() {
    document.body.classList.remove('chat-open');
    window.SNC.closeSheets();
  }
  function reset() { started = false; recs.length = 0; $('chat-log').innerHTML = ''; $('chat-chips').innerHTML = ''; }

  function init() {
    $('chat-close').addEventListener('click', close);
    // voice input: mic button next to send
    const send_ = $('chat-send');
    if (send_ && !$('chat-mic')) {
      const mic = document.createElement('button');
      mic.type = 'button'; mic.id = 'chat-mic'; mic.className = 'chat__mic'; mic.setAttribute('aria-label', 'Speak'); mic.dataset.tooltip = 'Speak (en-US)';
      mic.innerHTML = MIC;
      send_.parentNode.insertBefore(mic, send_);
      if (window.Voice && window.Voice.attach) window.Voice.attach($('chat-input'), mic, (t) => { if (t) { $('chat-input').value = ''; send(t); } });
      else mic.hidden = true;
    }
    $('chat-input').placeholder = 'Ask about a symptom, a recipe or a product (e.g. "feverish")';
    $('chat-form').addEventListener('submit', (e) => { e.preventDefault(); const v = $('chat-input').value; $('chat-input').value = ''; send(v); });
    window.addEventListener('hashchange', () => { /* store switch → new conversation */
      const m = location.hash.match(/^#\/store\/([^/?]+)/);
      if (m && Chat._store && Chat._store !== m[1]) reset();
      if (m) Chat._store = m[1];
    });
  }

  const Chat = { init, open, close, send, reset, layout, _store: null };
  window.Chat = Chat;
})();
