/* ChatRich: inject rich HTML cards INSIDE the AI Concierge panel (CONTRACT v2 "cart / checkout inside the chat
 * window", v3 own panel). Cards are appended to #cr-chat-log (before the typing indicator) in chronological order,
 * styles go to document.head once and one delegated click handler serves every card button ([data-crc-act]).
 *
 * API (window.ChatRich):
 *   append(html, {kind, id, record, noRecord}) → wrapper element (div.crc-rich); records the card in the chat
 *                                                history (CRChat.recordCard) unless noRecord
 *   root()                    → #cr-chat-log
 *   on(act, fn)               → click handler for [data-crc-act="act"] buttons inside cards: fn(button, card, event)
 *   scroll() / scrollSoon()   → scroll the message list to the bottom
 *   isLast(el)                → true when no newer user message follows el
 */
(function () {
  'use strict';
  const STYLE_ID = 'crc-style';
  const handlers = {};
  const bound = new WeakSet();

  const CSS = `
  .crc-rich {
    --crc-blue: #1a73e8; --crc-blue-50: #e8f0fe; --crc-text: #202124; --crc-text-2: #3c4043; --crc-muted: #5f6368;
    --crc-line: #dadce0; --crc-line-soft: #ebedf0; --crc-green: #188038; --crc-green-50: #e6f4ea; --crc-red: #d93025;
    --crc-ai: linear-gradient(90deg, #4285f4 0%, #9b72cb 55%, #d96570 100%);
    --crc-ai-soft: linear-gradient(135deg, rgba(66,133,244,.10), rgba(155,114,203,.09) 55%, rgba(217,101,112,.08));
    display: block; box-sizing: border-box; width: 100%; max-width: 100%; align-self: stretch; flex: 0 0 auto;
    margin: 6px 0 10px; font-family: "Google Sans", Roboto, system-ui, sans-serif;
    color: var(--crc-text); font-size: 13px; line-height: 1.45; text-align: left;
    animation: crcIn .28s cubic-bezier(.2,0,0,1);
  }
  .crc-rich *, .crc-rich *::before, .crc-rich *::after { box-sizing: border-box; }
  @keyframes crcIn { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
  @keyframes crcPulse { 0% { box-shadow: 0 0 0 0 rgba(26,115,232,.35); } 100% { box-shadow: 0 0 0 10px rgba(26,115,232,0); } }
  .crc-card {
    background: #fff; border: 1px solid var(--crc-line); border-radius: 16px; overflow: hidden;
    box-shadow: 0 1px 2px rgba(60,64,67,.10), 0 2px 6px 2px rgba(60,64,67,.06);
  }
  .crc-card.is-pulse { animation: crcPulse .7s ease-out; }
  .crc-head {
    display: flex; align-items: center; gap: 10px; padding: 11px 14px; background: var(--crc-ai-soft);
    border-bottom: 1px solid var(--crc-line-soft);
  }
  .crc-head__icon { width: 28px; height: 28px; border-radius: 50%; background: #fff; display: grid; place-items: center;
    box-shadow: 0 1px 2px rgba(60,64,67,.18); flex: none; }
  .crc-head__icon svg { width: 16px; height: 16px; }
  .crc-head__title { font-weight: 500; font-size: 14px; flex: 1; min-width: 0; letter-spacing: .01em; }
  .crc-head__title small { display: block; font-size: 11.5px; font-weight: 400; color: var(--crc-muted); margin-top: 1px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .crc-badge { flex: none; font-size: 11px; font-weight: 500; padding: 3px 9px; border-radius: 999px; background: #fff;
    color: var(--crc-blue); border: 1px solid #d2e3fc; white-space: nowrap; }
  .crc-badge.is-ok { color: var(--crc-green); border-color: #ceead6; background: var(--crc-green-50); }

  .crc-table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
  .crc-table th { font-weight: 500; font-size: 11px; color: var(--crc-muted); text-align: left; padding: 8px 10px 6px;
    border-bottom: 1px solid var(--crc-line-soft); letter-spacing: .02em; }
  .crc-table td { padding: 8px 10px; border-bottom: 1px solid var(--crc-line-soft); vertical-align: middle; }
  .crc-table tr:last-child td { border-bottom: 0; }
  .crc-table th.r, .crc-table td.r { text-align: right; white-space: nowrap; }
  .crc-table th.c, .crc-table td.c { text-align: center; }
  .crc-prod { display: flex; align-items: center; gap: 9px; min-width: 0; }
  .crc-prod img { width: 40px; height: 40px; flex: none; object-fit: contain; border-radius: 8px; background: #f8f9fa;
    border: 1px solid var(--crc-line-soft); }
  .crc-prod__t { font-size: 12.5px; color: var(--crc-text); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
    overflow: hidden; }
  .crc-prod__u { font-size: 11px; color: var(--crc-muted); }
  .crc-qty { display: inline-flex; align-items: center; border: 1px solid var(--crc-line); border-radius: 999px; height: 28px; }
  .crc-qty button { width: 26px; height: 26px; border: 0; background: none; border-radius: 50%; cursor: pointer; color: var(--crc-text-2);
    font: 500 15px/1 "Google Sans", sans-serif; display: grid; place-items: center; }
  .crc-qty button:hover { background: #f1f3f4; }
  .crc-qty span { min-width: 20px; text-align: center; font-weight: 500; font-size: 12.5px; }
  .crc-rm { display: block; margin: 4px auto 0; border: 0; background: none; color: var(--crc-muted); font-size: 11px; cursor: pointer;
    padding: 1px 4px; border-radius: 4px; }
  .crc-rm:hover { color: var(--crc-red); background: #fce8e6; }
  .crc-price { font-weight: 500; font-size: 13px; }

  .crc-sum { padding: 10px 14px 4px; border-top: 1px solid var(--crc-line-soft); background: #fafbfc; }
  .crc-sum__row { display: flex; justify-content: space-between; font-size: 12.5px; color: var(--crc-text-2); padding: 2px 0; }
  .crc-sum__row.total { font-size: 13.5px; color: var(--crc-text); font-weight: 500; padding-top: 6px; margin-top: 4px;
    border-top: 1px dashed var(--crc-line); }
  .crc-sum__row.total strong { font-size: 17px; font-weight: 700; }
  .crc-note { font-size: 11.5px; color: var(--crc-muted); padding: 2px 0 6px; }
  .crc-note b { color: var(--crc-green); font-weight: 500; }
  .crc-meter { height: 4px; border-radius: 4px; background: #e8eaed; overflow: hidden; margin: 2px 0 6px; }
  .crc-meter i { display: block; height: 100%; background: var(--crc-ai); border-radius: inherit; transition: width .4s; }
  .crc-actions { display: flex; gap: 8px; padding: 10px 14px 14px; background: #fafbfc; }
  .crc-btn { flex: 1; height: 40px; border-radius: 20px; border: 1px solid var(--crc-line); background: #fff; color: var(--crc-blue);
    font: 500 13.5px "Google Sans", Roboto, system-ui, sans-serif; cursor: pointer; transition: background .15s, box-shadow .15s, transform .1s; }
  .crc-btn:hover { background: var(--crc-blue-50); }
  .crc-btn:active { transform: scale(.98); }
  .crc-btn--primary { background: var(--crc-blue); color: #fff; border-color: var(--crc-blue); }
  .crc-btn--primary:hover { background: #1765cc; box-shadow: 0 1px 3px rgba(26,115,232,.4); }
  .crc-btn[disabled], .crc-btn.is-pending { opacity: .55; cursor: default; pointer-events: none; }
  .crc-btn.is-done { background: var(--crc-green-50); color: var(--crc-green); border-color: #ceead6; opacity: 1; }
  .crc-empty { padding: 18px 14px; text-align: center; color: var(--crc-muted); font-size: 12.5px; }

  .crc-collapsed { display: flex; align-items: center; gap: 8px; padding: 8px 12px; border: 1px solid var(--crc-line-soft);
    border-radius: 12px; background: #f8f9fa; color: var(--crc-muted); font-size: 12px; }
  .crc-collapsed b { color: var(--crc-text-2); font-weight: 500; }
  .crc-collapsed svg { width: 16px; height: 16px; fill: var(--crc-muted); flex: none; }

  /* delivery slots */
  .crc-slots { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; padding: 12px 14px 14px; }
  .crc-slot { text-align: left; border: 1px solid var(--crc-line); background: #fff; border-radius: 12px; padding: 9px 11px; cursor: pointer;
    font-family: inherit; color: var(--crc-text); transition: border-color .15s, background .15s, box-shadow .15s; }
  .crc-slot:hover { border-color: var(--crc-blue); background: #f8fbff; }
  .crc-slot b { display: block; font-size: 11.5px; font-weight: 400; color: var(--crc-muted); }
  .crc-slot span { display: block; font-size: 14.5px; font-weight: 500; margin-top: 1px; letter-spacing: .01em; }
  .crc-slot small { display: block; font-size: 10.5px; color: var(--crc-green); margin-top: 2px; }
  .crc-slot.is-selected { border-color: var(--crc-blue); background: var(--crc-blue-50); box-shadow: inset 0 0 0 1px var(--crc-blue); }
  .crc-slot.is-selected span::after { content: " ✓"; color: var(--crc-blue); }
  .crc-slots.is-locked .crc-slot:not(.is-selected) { opacity: .45; pointer-events: none; }
  .crc-slots.is-pending .crc-slot { pointer-events: none; }

  /* confirm */
  .crc-dl { margin: 0; padding: 4px 14px; }
  .crc-dl > div { display: grid; grid-template-columns: 84px 1fr; gap: 8px; padding: 7px 0; border-bottom: 1px solid var(--crc-line-soft); font-size: 12.5px; }
  .crc-dl > div:last-child { border-bottom: 0; }
  .crc-dl dt { color: var(--crc-muted); }
  .crc-dl dd { margin: 0; color: var(--crc-text); font-weight: 500; }
  .crc-visa { display: inline-block; font: 700 9.5px/1 "Google Sans", sans-serif; letter-spacing: .06em; color: #fff;
    background: linear-gradient(135deg, #1a1f71, #2a3cb8); border-radius: 3px; padding: 3px 5px; margin-right: 6px; vertical-align: 1px; }
  .crc-thumbs { display: flex; gap: 6px; padding: 12px 14px 4px; overflow-x: auto; }
  .crc-thumbs figure { margin: 0; position: relative; flex: none; }
  .crc-thumbs img { width: 46px; height: 46px; object-fit: contain; border-radius: 10px; background: #f8f9fa; border: 1px solid var(--crc-line-soft); }
  .crc-thumbs figcaption { position: absolute; right: -4px; top: -4px; min-width: 18px; height: 18px; padding: 0 5px; border-radius: 9px;
    background: var(--crc-text-2); color: #fff; font-size: 10.5px; line-height: 18px; text-align: center; }

  /* order tracking */
  .crc-order__id { font-family: "Roboto Mono", ui-monospace, monospace; font-size: 12px; color: var(--crc-text-2); }
  .crc-tl { list-style: none; margin: 0; padding: 14px 14px 6px; display: grid; grid-template-columns: repeat(4, 1fr); position: relative; }
  .crc-tl::before { content: ""; position: absolute; left: calc(12.5% + 0px); right: 12.5%; top: 27px; height: 3px; background: #e8eaed; border-radius: 3px; }
  .crc-tl__fill { position: absolute; left: 12.5%; top: 27px; height: 3px; background: var(--crc-blue); border-radius: 3px; transition: width .6s cubic-bezier(.2,0,0,1); }
  .crc-tl li { position: relative; z-index: 1; text-align: center; font-size: 11px; color: var(--crc-muted); }
  .crc-tl li i { width: 28px; height: 28px; border-radius: 50%; margin: 0 auto 5px; display: grid; place-items: center; background: #fff;
    border: 2px solid #dadce0; transition: all .3s; font-style: normal; }
  .crc-tl li i svg { width: 14px; height: 14px; fill: #bdc1c6; }
  .crc-tl li.is-done { color: var(--crc-text); }
  .crc-tl li.is-done i { background: var(--crc-blue); border-color: var(--crc-blue); }
  .crc-tl li.is-done i svg { fill: #fff; }
  .crc-tl li.is-current i { box-shadow: 0 0 0 5px rgba(26,115,232,.16); }
  .crc-tl li.is-delivered i { background: var(--crc-green); border-color: var(--crc-green); }
  .crc-tl li small { display: block; color: var(--crc-muted); font-size: 10px; margin-top: 1px; min-height: 13px; }
  .crc-status { margin: 4px 14px 12px; padding: 9px 12px; border-radius: 12px; background: var(--crc-blue-50); color: #174ea6; font-size: 12.5px;
    display: flex; align-items: center; gap: 8px; }
  .crc-status.is-delivered { background: var(--crc-green-50); color: var(--crc-green); font-weight: 500; }
  .crc-status .dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; flex: none; animation: crcBlink 1.2s infinite; }
  .crc-status.is-delivered .dot { display: none; }
  @keyframes crcBlink { 50% { opacity: .25; } }

  /* dinner set */
  .crc-role { display: inline-block; font-size: 11px; font-weight: 500; padding: 2px 8px; border-radius: 999px; background: #f1f3f4; color: var(--crc-text-2); white-space: nowrap; }
  .crc-settable { table-layout: fixed; }
  .crc-settable th.crc-setrole, .crc-settable td.crc-setrole { width: 78px; padding-right: 4px; vertical-align: top; }
  .crc-settable th.r { width: 64px; }
  .crc-settable td.crc-setrole .crc-role { white-space: normal; text-align: center; line-height: 1.3; padding: 3px 8px; max-width: 72px; word-break: keep-all; }
  .crc-settable td { vertical-align: top; }
  .crc-settable tr.crc-setrow td { border-bottom: 0; }
  .crc-settable tr.crc-setrow.is-first td { border-top: 1px solid var(--crc-line-soft); }
  .crc-settable tbody tr:first-child td { border-top: 0; }
  .crc-settable .crc-prod { align-items: flex-start; }
  .crc-settable td.r { padding-top: 10px; }
  .crc-tags { display: flex; flex-wrap: wrap; gap: 3px; margin-top: 3px; }
  .crc-tag { font-size: 10px; line-height: 16px; padding: 0 6px; border-radius: 999px; white-space: nowrap; font-weight: 500; }
  .crc-tag--freq { background: #e8f0fe; color: #1967d2; }
  .crc-tag--family { background: #fce4ec; color: #b0185b; }
  .crc-tag--sale { background: #fce8e6; color: #c5221f; }
  .crc-tag--season { background: #e6f4ea; color: #137333; }
  .crc-tag--quick { background: #fef7e0; color: #a35200; }
  .crc-tag--other { background: #f1f3f4; color: #5f6368; }
  .crc-concept { padding: 10px 14px 2px; font-size: 12px; color: var(--crc-text-2); }
  `;

  function root() { return document.getElementById('cr-chat-log'); }

  function ensureStyles() {
    const r = root();
    if (!r) return null;
    if (!document.getElementById(STYLE_ID)) {
      const st = document.createElement('style');
      st.id = STYLE_ID;
      st.textContent = CSS;
      document.head.appendChild(st);
    }
    bind(r);
    return r;
  }

  function bind(r) {
    if (!r || bound.has(r)) return;
    bound.add(r);
    r.addEventListener('click', (e) => {
      const b = e.target && e.target.closest && e.target.closest('[data-crc-act]');
      if (!b) return;
      e.preventDefault();
      e.stopPropagation();
      const fn = handlers[b.dataset.crcAct];
      if (fn) { try { fn(b, b.closest('.crc-rich'), e); } catch (err) { console.error('[ChatRich]', err); } }
    });
  }

  function scroll() { const r = root(); if (r) r.scrollTop = r.scrollHeight + 5000; }
  function scrollSoon() { [20, 120, 400].forEach((ms) => setTimeout(scroll, ms)); }

  function append(html, opts) {
    opts = opts || {};
    const r = ensureStyles();
    if (!r) return null;
    const w = document.createElement('div');
    w.className = 'crc-rich' + (opts.kind ? ' crc-rich--' + opts.kind : '');
    if (opts.kind) w.dataset.kind = opts.kind;
    if (opts.id) w.id = opts.id;
    w.innerHTML = html;
    const typing = document.getElementById('cr-chat-typing');
    if (typing && typing.parentNode === r) r.insertBefore(w, typing); else r.appendChild(w);
    if (!opts.noRecord && window.CRChat && window.CRChat.recordCard) window.CRChat.recordCard(opts.kind, opts.record);
    scrollSoon();
    return w;
  }

  /** true when nothing newer (user message) follows el */
  function isLast(el) {
    if (!el || !el.isConnected) return false;
    let n = el.nextElementSibling;
    while (n) { if (n.classList.contains('cr-msg--user')) return false; n = n.nextElementSibling; }
    return true;
  }

  function on(act, fn) { handlers[act] = fn; }

  window.ChatRich = { append, root, on, scroll, scrollSoon, isLast, ensureStyles, CSS };
})();
