/* Router: URL query → view. /?q=… (search) /?product=ID (detail modal) /?view=checkout|orders|agent|compare /?order=ID
 * The agent result views (agent / compare) only exist after the AI Concierge showed products in this page session,
 * so a (re)load of /?view=agent|compare always falls back to the home page. */
(function () {
  'use strict';
  const CR = window.CR;

  function current() {
    const u = new URL(location.href);
    const p = Object.fromEntries(u.searchParams.entries());
    if (!p.view) p.view = p.order ? 'order' : (p.q !== undefined ? 'search' : 'home');
    return p;
  }

  let first = true;
  function render() {
    let p = current();
    const transient = p.view === 'agent' || p.view === 'compare';
    if (transient && (first || !window.Search.hasAgentView(p.view))) {
      history.replaceState({}, '', '/' + (p.product ? '?product=' + encodeURIComponent(p.product) : ''));
      p = current();
    }
    first = false;
    CR.pageContext.view = p.view;
    CR.pageContext.query = p.view === 'search' ? (p.q || '') : '';
    document.body.dataset.view = p.view;
    const input = document.getElementById('cr-search-input');
    if (input && p.view === 'search' && document.activeElement !== input) input.value = p.q || '';
    if (input && p.view !== 'search' && p.view !== 'agent') input.value = '';
    const clr = document.getElementById('cr-search-clear');
    if (clr && input) clr.hidden = !input.value;
    switch (p.view) {
      case 'search': window.Search.run({ q: p.q || '' }); break;
      case 'agent': window.Search.renderAgent(); break;
      case 'compare': window.Search.renderCompare(); break;
      case 'checkout': window.Checkout.render(); break;
      case 'order': window.Orders.renderOrder(p.order); break;
      case 'orders': window.Orders.renderHistory(); break;
      default: window.Home.render();
    }
    if (p.product) CR.openProduct(p.product, { noHistory: true });
    CR.emit('context');
  }

  function go(params, opts) {
    opts = opts || {};
    const u = new URL(location.origin + '/');
    Object.keys(params || {}).forEach((k) => { if (params[k] !== undefined && params[k] !== null && !(k === 'view' && params[k] === 'home')) u.searchParams.set(k, params[k]); });
    if (opts.replace) history.replaceState({}, '', u); else history.pushState({}, '', u);
    CR.closeProduct();
    if (!opts.keepScroll) window.scrollTo({ top: 0, behavior: 'instant' });
    render();
  }

  window.Router = { go, render, current };
  window.addEventListener('popstate', render);
  document.addEventListener('click', (e) => {
    const n = e.target.closest('[data-nav]');
    if (n) { e.preventDefault(); go({ view: n.dataset.nav }); return; }
    const logo = e.target.closest('#cr-logo');
    if (logo) { e.preventDefault(); go({ view: 'home' }); }
  });
  document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('cr-orders-btn').addEventListener('click', () => go({ view: 'orders' }));
    render();
  });
})();
