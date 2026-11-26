/* Store screen: personalized coupons ("Just for you"), deals, search form with autocomplete, AI concierge entry. */
(function () {
  'use strict';
  const { esc, fetchJSON } = window.SN;
  const QUICK = ['Curry roux', 'Cold medicine', 'Pain reliever', 'Cooling gel sheet', 'Oral rehydration', 'Eye drops', 'Milk', 'Sensitive skin toner'];
  const MIC = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 14a3 3 0 0 0 3-3V5a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3zm5.3-3a5.3 5.3 0 0 1-10.6 0H5a7 7 0 0 0 6 6.92V21h2v-3.08A7 7 0 0 0 19 11h-1.7z"/></svg>';

  function searchboxHTML(value) {
    return '<div class="searchbox"><form class="searchbox__form" id="sb-form" role="search" autocomplete="off">' +
      '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15.5 14h-.79l-.28-.27A6.47 6.47 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/></svg>' +
      '<input id="sb-input" type="search" name="q" value="' + esc(value || '') + '" placeholder="Search this store by product or symptom (e.g. cold medicine)" aria-label="Search products" aria-autocomplete="list" aria-controls="sb-ac" enterkeyhint="search">' +
      '<button type="button" class="searchbox__clear" id="sb-clear" aria-label="Clear"' + (value ? '' : ' hidden') + '><svg viewBox="0 0 24 24"><path d="M19 6.41 17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg></button>' +
      '<button type="button" class="searchbox__mic" id="sb-mic" aria-label="Search by voice" data-tooltip="Search by voice">' + MIC + '</button>' +
      '<ul class="ac" id="sb-ac" role="listbox" hidden></ul></form></div>';
  }
  function bindSearchbox(root, storeId) {
    window.Autocomplete.attach(root.querySelector('#sb-form'), root.querySelector('#sb-input'), root.querySelector('#sb-ac'),
      root.querySelector('#sb-clear'), (q) => window.SNC.go('#/store/' + storeId + '/search?q=' + encodeURIComponent(q)));
    const mic = root.querySelector('#sb-mic');
    if (mic && window.Voice && window.Voice.attach) {
      window.Voice.attach(root.querySelector('#sb-input'), mic, (q) => { if (q) window.SNC.go('#/store/' + storeId + '/search?q=' + encodeURIComponent(q)); });
    } else if (mic) mic.hidden = true;
  }

  async function render(view) {
    const S = window.SNC.state;
    const s = window.SNC.storeById(S.storeId);
    if (!s) { window.SNC.go('#/'); return; }
    const name = (S.customer && S.customer.name) || '';
    view.innerHTML =
      '<section class="store-top"><div class="store-top__row"><h1 class="store-top__name">' + esc(s.name) + '</h1></div>' +
      '<div class="store-top__row" style="margin-top:6px">' + window.Home.openPill(s) + (s.in_store ? '<span class="sn-pill sn-pill--online">In-store mode</span>' : '') +
      '<span class="store-top__hours">' + esc(window.Home.hoursLine(s)) + ' • ' + window.Home.pharmacyLine(s) + '</span></div>' +
      searchboxHTML('') +
      '<div class="quick">' + QUICK.map((q) => '<button type="button" class="sn-chip" data-q="' + esc(q) + '">' + esc(q) + '</button>').join('') + '</div></section>' +
      '<button type="button" class="ai-card" id="ai-card"><span class="ai-card__icon"><svg viewBox="0 0 24 24"><path d="M12 2l1.9 5.6L19.5 9.5 13.9 11.4 12 17l-1.9-5.6L4.5 9.5l5.6-1.9L12 2zm7 11 1 2.8 2.8 1-2.8 1-1 2.8-1-2.8-2.8-1 2.8-1 1-2.8z"/></svg></span>' +
      '<span><span class="ai-card__title">Ask AI about symptoms or concerns</span><br><span class="ai-card__sub">e.g. &quot;I have felt feverish since yesterday.&quot; Get matching products and the best route through the store.</span></span></button>' +
      '<button type="button" class="ai-card ai-card--recipe" id="recipe-card"><span class="ai-card__icon ai-card__icon--recipe"><svg viewBox="0 0 24 24"><path d="M8.1 13.34l2.83-2.83L3.91 3.5a4.008 4.008 0 0 0 0 5.66l4.19 4.18zm6.78-1.81c1.53.71 3.68.21 5.27-1.38 1.91-1.91 2.28-4.65.81-6.12-1.46-1.46-4.2-1.1-6.12.81-1.59 1.59-2.09 3.74-1.38 5.27L3.7 19.87l1.41 1.41L12 14.41l6.88 6.88 1.41-1.41L13.41 13l1.47-1.47z"/></svg></span>' +
      '<span><span class="ai-card__title">Recipe ideas for tonight</span><br><span class="ai-card__sub">Pick a dish, adjust servings or make it kid-friendly, then add every ingredient to your cart in one tap.</span></span></button>' +
      '<section class="sec"><div class="sec__head"><h2 class="sec__title" id="personal-title">' + esc(name ? 'Just for you, ' + name.split(' ')[0] : 'Coupons for you') + '</h2></div>' +
      '<div class="hscroll" id="st-coupons"><div class="coupon sn-skel"></div><div class="coupon sn-skel"></div></div></section>' +
      '<section class="sec"><div class="sec__head"><h2 class="sec__title">Today\'s deals<small>' + esc(name ? 'Sorted for ' + name.split(' ')[0] : '') + '</small></h2></div>' +
      '<div class="hscroll" id="st-offers"><div class="offer sn-skel" style="height:220px"></div><div class="offer sn-skel" style="height:220px"></div></div></section>';
    bindSearchbox(view, s.store_id);
    view.querySelectorAll('.quick [data-q]').forEach((b) => b.addEventListener('click', () => {
      window.Autocomplete.pushRecent(b.dataset.q);
      window.SNC.go('#/store/' + s.store_id + '/search?q=' + encodeURIComponent(b.dataset.q));
    }));
    view.querySelector('#ai-card').addEventListener('click', () => window.Chat.open());
    const rc = view.querySelector('#recipe-card');
    if (rc) rc.addEventListener('click', () => { if (window.Recipes) window.Recipes.open(''); });
    try {
      const home = await fetchJSON('/api/stores/' + encodeURIComponent(s.store_id) + '/home');
      const personal = (home.coupons || []).filter((c) => c.personal);
      const others = (home.coupons || []).filter((c) => !c.personal);
      view.querySelector('#st-coupons').innerHTML = personal.concat(others).map(window.Home.couponHTML).join('') || '<p class="res-empty">No coupons right now</p>';
      if (!personal.length) view.querySelector('#personal-title').textContent = 'Coupons';
      const of = view.querySelector('#st-offers');
      of.innerHTML = (home.offers || []).map(window.Home.offerHTML).join('');
      window.Home.bindOffers(of, home.offers || [], s.store_id);
    } catch (e) { view.querySelector('#st-coupons').innerHTML = ''; }
  }

  window.Store = { render, searchboxHTML, bindSearchbox };
})();
