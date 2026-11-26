/* Cymbal Store Navi — shared helpers (customer & staff apps): window.SN */
(function () {
  'use strict';
  const CFG = window.SN_CONFIG || {};

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }
  // CONTRACT_EN: JPY, en-US formatting (¥1,280)
  function yen(n) { return Math.round(Number(n) || 0).toLocaleString('en-US', { style: 'currency', currency: 'JPY' }); }
  function km(m) {
    if (m == null) return '';
    return m < 1000 ? Math.round(m) + ' m' : (m / 1000).toFixed(m < 10000 ? 1 : 0) + ' km';
  }
  async function fetchJSON(url, opts) {
    const r = await fetch(url, Object.assign({ credentials: 'same-origin' }, opts || {}));
    if (!r.ok) {
      let detail = '';
      try { detail = (await r.json()).error || ''; } catch (e) { /* ignore */ }
      throw new Error('HTTP ' + r.status + (detail ? ' ' + detail : ''));
    }
    return r.json();
  }
  function postJSON(url, body) {
    return fetchJSON(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  }

  const CLASS1 = 'Pharmacist-only (Class 1)';
  const DRUG = {
    'Pharmacist-only (Class 1)': { cls: '1', short: 'Pharmacist-only' },
    'Designated Class 2': { cls: 's2', short: 'Designated Class 2' },
    'Class 2': { cls: '2', short: 'Class 2' },
    'Class 3': { cls: '3', short: 'Class 3' },
    'Quasi-drug': { cls: 'q', short: 'Quasi-drug' },
  };
  // English OTC rules (same wording as app/agent/safety.py)
  const DRUG_NOTE = {
    'Pharmacist-only (Class 1)': 'Pharmacist-only (Class 1): a pharmacist must explain this medicine at purchase. Not available when no pharmacist is on duty.',
    'Designated Class 2': 'Designated Class 2: check the warnings (who should not use it) before use. Ask a pharmacist or registered seller if unsure.',
    'Class 2': 'Class 2 OTC medicine: ask a pharmacist or registered seller if you have questions.',
  };
  const ABUSE_NOTE = 'Purchase limit: one per customer. Customers under 18 may buy one small pack only (ID/age check). This item is handed over at the pharmacy counter.';
  const DISCLAIMER = 'This service provides general information only and does not replace a diagnosis by a doctor. Read the package insert and follow the directions for use. If symptoms persist or get worse, see a doctor. For questions, ask the pharmacist or registered seller in store.';

  function drugBadges(p) {
    if (!p) return '';
    let h = '';
    const d = DRUG[p.drug_class];
    if (d) h += '<span class="sn-drug sn-drug--' + d.cls + '">' + esc(d.short) + '</span>';
    if (p.abuse_control) h += '<span class="sn-drug sn-drug--abuse" title="Abuse-prone OTC medicine">Limit 1</span>';
    return h;
  }
  function isMedicine(p) { return !!(p && DRUG[p.drug_class] && p.drug_class !== 'Quasi-drug'); }
  function complianceNotes(p) {
    const out = [];
    if (!p) return out;
    if (DRUG_NOTE[p.drug_class]) out.push({ level: p.drug_class === CLASS1 ? 'warn' : 'info', text: DRUG_NOTE[p.drug_class] });
    if (p.drug_class === CLASS1) out.push({ level: 'warn', text: 'In store, ask the pharmacist at the pharmacy counter. Online orders ship after a pharmacist check.' });
    if (p.abuse_control) out.push({ level: 'warn', text: ABUSE_NOTE });
    return out;
  }
  const ICON_INFO = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11 7h2v2h-2zm0 4h2v6h-2zm1-9C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 8 8-3.59 8-8 8z"/></svg>';
  function noticesHTML(p) {
    return complianceNotes(p).map((n) => '<div class="sn-notice' + (n.level === 'warn' ? ' sn-notice--warn' : '') + '">' + ICON_INFO + '<span>' + esc(n.text) + '</span></div>').join('');
  }
  const STATUS_EN = { in_stock: 'In stock', low_stock: 'Low stock', out_of_stock: 'Out of stock', not_carried: 'Not carried' };
  const STATUS_JA = STATUS_EN; // legacy alias (staff app)
  function statusPill(status, qty) {
    const label = STATUS_EN[status] || status;
    const q = (status === 'low_stock' && qty) ? ' (' + qty + ' left)' : '';
    return '<span class="sn-pill sn-pill--' + esc(status) + '">' + esc(label + q) + '</span>';
  }
  function img(src, alt, cls) {
    return '<div class="sn-img ' + (cls || '') + '"><img src="' + esc(src || '') + '" alt="' + esc(alt || '') + '" loading="lazy" onerror="this.parentNode.classList.add(\'is-broken\')"></div>';
  }

  // CONTRACT v7/v8 cross link (icon-only, hover tooltip)
  const ICONS = {
    staff: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 7h-4V5c0-1.1-.9-2-2-2h-4c-1.1 0-2 .9-2 2v2H4c-1.1 0-2 .9-2 2v11c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V9c0-1.1-.9-2-2-2zM10 5h4v2h-4V5zm2 5.5a2 2 0 1 1 0 4 2 2 0 0 1 0-4zM16 19H8v-.75c0-1.33 2.67-2 4-2s4 .67 4 2V19z"/></svg>',
    online: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 6h-2c0-2.21-1.79-4-4-4S8 3.79 8 6H6c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2zm-6-2c1.1 0 2 .9 2 2h-4c0-1.1.9-2 2-2zm6 16H6V8h2v2h2V8h4v2h2V8h2v12z"/></svg>',
    enrich: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19 9l1.25-2.75L23 5l-2.75-1.25L19 1l-1.25 2.75L15 5l2.75 1.25L19 9zm-7.5.5L9 4 6.5 9.5 1 12l5.5 2.5L9 20l2.5-5.5L17 12l-5.5-2.5zM19 15l-1.25 2.75L15 19l2.75 1.25L19 23l1.25-2.75L23 19l-2.75-1.25L19 15z"/></svg>',
    customer: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M17 1.01 7 1c-1.1 0-2 .9-2 2v18c0 1.1.9 2 2 2h10c1.1 0 2-.9 2-2V3c0-1.1-.9-1.99-2-1.99zM17 19H7V5h10v14zm-5-9.5a2 2 0 1 1 0 4 2 2 0 0 1 0-4zM15 17H9v-.6c0-1 2-1.6 3-1.6s3 .6 3 1.6v.6z"/></svg>',
  };
  function crossLink(container, url, tooltip, icon, id) {
    if (!container || !/^https?:\/\//.test(String(url || ''))) return null;
    const a = document.createElement('a');
    a.className = 'sn-xlink';
    if (id) a.id = id;
    a.href = url;
    a.dataset.tooltip = tooltip;
    a.setAttribute('aria-label', tooltip);
    a.innerHTML = '<span class="sn-xlink__icon">' + (ICONS[icon] || icon || '') + '</span>';
    container.appendChild(a);
    return a;
  }

  // CONTRACT v9: cart buttons are a cart icon (Material add_shopping_cart) with aria-label / tooltip "Add to cart"
  const ICON_ADD_CART = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11 9h2V6h3V4h-3V1h-2v3H8v2h3v3zm-4 9c-1.1 0-1.99.9-1.99 2S5.9 22 7 22s2-.9 2-2-.9-2-2-2zm10 0c-1.1 0-1.99.9-1.99 2s.89 2 1.99 2 2-.9 2-2-.9-2-2-2zm-9.83-3.25.03-.12.9-1.63h7.45c.75 0 1.41-.41 1.75-1.03l3.86-7.01L19.42 4h-.01l-1.1 2-2.76 5H8.53l-.13-.27L6.16 6l-.95-2-.94-2H1v2h2l3.6 7.59-1.35 2.45c-.16.28-.25.61-.25.96 0 1.1.9 2 2 2h12v-2H7.42c-.13 0-.25-.11-.25-.25z"/></svg>';
  const ICON_IN_CART = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 18c-1.1 0-1.99.9-1.99 2S5.9 22 7 22s2-.9 2-2-.9-2-2-2zm10 0c-1.1 0-1.99.9-1.99 2s.89 2 1.99 2 2-.9 2-2-.9-2-2-2zM7.17 14.75l.03-.12.9-1.63h7.45c.75 0 1.41-.41 1.75-1.03l3.86-7.01L19.42 4h-.01l-1.1 2-2.76 5H8.53l-.13-.27L6.16 6l-.95-2-.94-2H1v2h2l3.6 7.59-1.35 2.45c-.16.28-.25.61-.25.96 0 1.1.9 2 2 2h12v-2H7.42c-.13 0-.25-.11-.25-.25zM10.5 9.5 8.4 7.4 7 8.8l3.5 3.5 6-6-1.4-1.4z"/></svg>';
  function cartBtn(pid, added) {
    const t = added ? 'In cart' : 'Add to cart';
    return '<button type="button" class="cart-ic' + (added ? ' is-added' : '') + '" data-cart="' + esc(pid) + '" aria-label="' + t + '" aria-pressed="' + (added ? 'true' : 'false') + '" data-tooltip="' + t + '">' +
      (added ? ICON_IN_CART : ICON_ADD_CART) + '</button>';
  }
  function setCartBtn(btn, added) {
    const t = added ? 'In cart' : 'Add to cart';
    btn.classList.toggle('is-added', added);
    btn.setAttribute('aria-label', t); btn.setAttribute('aria-pressed', added ? 'true' : 'false'); btn.dataset.tooltip = t;
    btn.innerHTML = added ? ICON_IN_CART : ICON_ADD_CART;
  }

  let toastTimer = null;
  function toast(msg) {
    let el = document.getElementById('sn-toast');
    if (!el) { el = document.createElement('div'); el.id = 'sn-toast'; el.className = 'sn-toast'; el.setAttribute('role', 'status'); document.body.appendChild(el); }
    el.textContent = msg;
    el.classList.add('is-on');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove('is-on'), 2600);
  }

  // en-US 12h store hours ("09:00" → "9:00 AM"), same format as the online store / MCP ("9:00 AM – 10:00 PM")
  function time12(hhmm) {
    const m = String(hhmm || '').match(/^(\d{1,2}):(\d{2})/);
    if (!m) return String(hhmm || '');
    let h = +m[1] % 24; const ap = h >= 12 ? 'PM' : 'AM'; h = h % 12 || 12;
    return h + ':' + m[2] + ' ' + ap;
  }
  function hours12(open, close) { return (open || close) ? time12(open) + ' – ' + time12(close) : ''; }
  // CONTRACT v14 omnichannel helpers
  function channelOf(p) { const c = String((p && p.channel) || 'omni').toLowerCase(); return ['store_only', 'online_only'].includes(c) ? c : 'omni'; }
  function onlineUrl(pid) {
    const base = String(CFG.onlineStoreUrl || '').replace(/\/$/, '');
    return base ? base + '/?product=' + encodeURIComponent(pid) : '';
  }
  const ICON_OPEN = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19 19H5V5h7V3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14c1.1 0 2-.9 2-2v-7h-2v7zM14 3v2h3.59l-9.83 9.83 1.41 1.41L19 6.41V10h2V3h-7z"/></svg>';
  function buyOnlineLink(pid, label) {
    const u = onlineUrl(pid);
    if (!u) return '';
    return '<a class="sn-buy-online" href="' + esc(u) + '" target="_blank" rel="noopener">' + esc(label || 'Buy online') + ICON_OPEN + '</a>';
  }
  function channelBadge(p) {
    const c = channelOf(p);
    if (c === 'store_only') return '<span class="sn-chan sn-chan--store" title="Sold in store only">In-store only</span>';
    if (c === 'online_only') return '<span class="sn-chan sn-chan--online" title="Not on store shelves">Online only</span>';
    return '';
  }
  const SOURCE_LABEL = { retail_local_inventory: 'Live stock · AI Commerce Search Local Inventory', bigquery: 'Store stock · BigQuery', local: 'Store stock · demo data' };
  function sourceBadge(source) {
    if (!source) return '';
    const live = source === 'retail_local_inventory';
    return '<span class="sn-src' + (live ? ' sn-src--live' : '') + '" data-source="' + esc(source) + '">' + (live ? '<i class="sn-src__dot"></i>' : '') + esc(SOURCE_LABEL[source] || source) + '</span>';
  }

  window.SN = { CFG, esc, yen, km, fetchJSON, postJSON, DRUG, DRUG_NOTE, ABUSE_NOTE, DISCLAIMER, STATUS_JA, STATUS_EN, CLASS1,
    channelOf, onlineUrl, buyOnlineLink, channelBadge, sourceBadge, time12, hours12,
    drugBadges, isMedicine, complianceNotes, noticesHTML, statusPill, img, crossLink, toast, ICON_INFO, cartBtn, setCartBtn, ICON_ADD_CART };
})();
