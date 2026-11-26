/* Cymbal Retail store app — dish-first recipe concierge (window.Recipes). Requires forgot.js (window.FeatUI).
   Recipes.open(query?)                  full-screen overlay #feat-recipes: 3 dishes -> customize -> recipe card -> add all
   Recipes.renderInline(container, query) compact dish list (e.g. inside chat); tapping a dish opens the overlay. */
(function () {
  'use strict';
  const SUGGESTIONS = ['Quick dinner kids like', 'Curry', 'Fish', 'Healthy salad', '15-minute meal'];
  const DISH_EMOJI = [['curry', '\u{1F35B}'], ['salmon', '\u{1F41F}'], ['fish', '\u{1F41F}'], ['noodle', '\u{1F35C}'], ['udon', '\u{1F35C}'],
    ['ramen', '\u{1F35C}'], ['soup', '\u{1F372}'], ['hot pot', '\u{1F372}'], ['salad', '\u{1F957}'], ['rice', '\u{1F35A}'], ['egg', '\u{1F373}'],
    ['chicken', '\u{1F357}'], ['pork', '\u{1F969}'], ['beef', '\u{1F969}'], ['tofu', '\u{1F962}'], ['sushi', '\u{1F363}'], ['pasta', '\u{1F35D}']];
  const S = { query: '', dishes: [], dish: null, servings: 4, kid: false, quick: false, card: null, token: 0 };

  function emoji(title) {
    const t = String(title || '').toLowerCase();
    const hit = DISH_EMOJI.find(([k]) => t.indexOf(k) >= 0);
    return hit ? hit[1] : '\u{1F37D}\u{FE0F}';
  }
  function dishImg(d, cls) { return window.FeatUI.thumb(d.image, d.title, cls || 'feat-dish__img', emoji(d.title)); }

  function dishCards(dishes) {
    const U = window.FeatUI;
    return '<div class="feat-dishes">' + dishes.map((d) =>
      '<button type="button" class="feat-dish" data-dish="' + U.esc(d.id) + '">' + dishImg(d) +
      '<span class="feat-dish__body"><span class="feat-dish__title">' + U.esc(d.title) + '</span>' +
      '<span class="feat-dish__meta">' + d.minutes + ' min \u2022 serves ' + d.servings + '</span>' +
      '<span class="feat-tags">' + (d.kid_friendly ? '<span class="feat-tag feat-tag--kid">Kid-friendly</span>' : '') +
        (d.tags || []).filter((t) => !/kid/i.test(t)).slice(0, 2).map((t) => '<span class="feat-tag">' + U.esc(t) + '</span>').join('') + '</span>' +
      (d.reason ? '<span class="feat-dish__why">' + U.esc(d.reason) + '</span>' : '') + '</span></button>').join('') + '</div>';
  }

  function fetchDishes(q) {
    const U = window.FeatUI;
    return U.fetchJSON('/api/recipes/suggest?limit=3&q=' + encodeURIComponent(q || '') + '&store_id=' + encodeURIComponent(U.storeId()))
      .then((r) => r.dishes || []);
  }

  function ov() { return window.FeatUI.overlay('feat-recipes'); }

  // ------------------------------------------------------------------------------------------- step 1: dishes
  async function showDishes(q) {
    const U = window.FeatUI;
    const o = ov();
    S.query = q || '';
    o.title('Recipe ideas'); o.back(null); o.foot.innerHTML = '';
    o.body.innerHTML = '<form class="feat-search" role="search"><input type="search" name="q" placeholder="What would you like to cook?" aria-label="What would you like to cook?" value="' +
      U.esc(S.query) + '" autocomplete="off"><button type="button" class="feat-mic" aria-label="Speak" title="Speak">' + U.ICONS.mic + '</button>' +
      '<button type="submit" class="feat-btn feat-btn--sm">Find</button></form>' +
      '<div class="feat-chips">' + SUGGESTIONS.map((s) => '<button type="button" class="feat-chip" data-q="' + U.esc(s) + '">' + U.esc(s) + '</button>').join('') + '</div>' +
      '<p class="feat-hint">Pick a dish \u2014 we\u2019ll find every ingredient in this store.</p><div data-dishes></div>';
    const form = o.body.querySelector('form');
    const input = form.querySelector('input');
    const mic = form.querySelector('.feat-mic');
    form.addEventListener('submit', (e) => { e.preventDefault(); loadDishes(input.value.trim()); });
    o.body.querySelectorAll('[data-q]').forEach((b) => b.addEventListener('click', () => { input.value = b.dataset.q; loadDishes(b.dataset.q); }));
    if (window.Voice && window.Voice.attach) window.Voice.attach(input, mic, (t) => loadDishes(t));
    else mic.hidden = true;
    o.show();
    await loadDishes(S.query);
  }

  async function loadDishes(q) {
    const U = window.FeatUI;
    const box = ov().body.querySelector('[data-dishes]');
    if (!box) return;
    S.query = q || '';
    const my = ++S.token;
    box.innerHTML = '<div class="feat-dishes"><div class="feat-skel"></div><div class="feat-skel"></div><div class="feat-skel"></div></div>';
    try {
      const dishes = await fetchDishes(S.query);
      if (my !== S.token) return;
      S.dishes = dishes;
      if (!dishes.length) { box.innerHTML = '<p class="feat-empty">No recipes found. Try \u201ccurry\u201d or \u201cquick dinner\u201d.</p>'; return; }
      box.innerHTML = dishCards(dishes);
      box.querySelectorAll('[data-dish]').forEach((b) => b.addEventListener('click', () => customize(dishes.find((d) => d.id === b.dataset.dish))));
    } catch (e) {
      if (my === S.token) box.innerHTML = '<p class="feat-empty">Couldn\u2019t load recipes. Please try again.</p>';
    }
  }

  // ------------------------------------------------------------------------------------------- step 2: customize
  function customize(d) {
    if (!d) return;
    const U = window.FeatUI;
    const o = ov();
    S.dish = d;
    S.servings = [2, 4, 6].indexOf(d.servings) >= 0 ? d.servings : 4;
    S.kid = !!d.kid_friendly && /kid|child|family/i.test(S.query);
    S.quick = /quick|fast|easy|busy|minute/i.test(S.query);
    o.title(d.title);
    o.back(() => showDishes(S.query));
    const sw = (name, label, sub, on) => '<div class="feat-row"><div><div class="feat-row__label">' + label + '</div><div class="feat-row__sub">' + sub +
      '</div></div><label class="feat-switch"><input type="checkbox" name="' + name + '"' + (on ? ' checked' : '') + ' aria-label="' + label + '"><span></span></label></div>';
    o.body.innerHTML = '<div class="feat-hero">' + dishImg(d) + '<div><h3>' + U.esc(d.title) + '</h3><p>' + U.esc(d.subtitle || '') + '</p><p>' +
      d.minutes + ' min' + (d.reason ? ' \u2022 ' + U.esc(d.reason) : '') + '</p></div></div>' +
      '<div class="feat-card"><h3>Make it yours</h3>' +
      '<div class="feat-row"><div><div class="feat-row__label">Servings</div><div class="feat-row__sub">Amounts scale automatically</div></div>' +
      '<div class="feat-seg" role="radiogroup" aria-label="Servings">' + [2, 4, 6].map((n) => '<button type="button" role="radio" data-sv="' + n + '" aria-checked="' + (n === S.servings) + '"' +
        (n === S.servings ? ' class="is-on"' : '') + '>' + n + '</button>').join('') + '</div></div>' +
      sw('kid', 'Kid-friendly', 'Milder spice, kid-size pieces', S.kid) +
      sw('quick', 'Quick', 'Shortcut steps to save time', S.quick) + '</div>';
    o.body.querySelectorAll('[data-sv]').forEach((b) => b.addEventListener('click', () => {
      S.servings = Number(b.dataset.sv);
      o.body.querySelectorAll('[data-sv]').forEach((x) => { const on = x === b; x.classList.toggle('is-on', on); x.setAttribute('aria-checked', on); });
    }));
    o.body.querySelector('[name=kid]').addEventListener('change', (e) => { S.kid = e.target.checked; });
    o.body.querySelector('[name=quick]').addEventListener('change', (e) => { S.quick = e.target.checked; });
    o.foot.innerHTML = '<button type="button" class="feat-btn">Show recipe & ingredients</button>';
    o.foot.querySelector('button').addEventListener('click', () => showCard());
    o.show();
    o.body.scrollTop = 0;
  }

  // ------------------------------------------------------------------------------------------- step 3: recipe card
  async function showCard() {
    const U = window.FeatUI;
    const o = ov();
    const d = S.dish;
    o.back(() => customize(d));
    o.body.innerHTML = '<div class="feat-skel" style="min-height:90px;margin-bottom:12px"></div><div class="feat-skel" style="min-height:280px"></div>';
    o.foot.innerHTML = '';
    const my = ++S.token;
    let card;
    try {
      card = await U.fetchJSON('/api/recipes/' + encodeURIComponent(d.id) + '?servings=' + S.servings + '&kid_friendly=' + (S.kid ? 1 : 0) +
        '&quick=' + (S.quick ? 1 : 0) + '&store_id=' + encodeURIComponent(U.storeId()));
    } catch (e) {
      if (my === S.token) o.body.innerHTML = '<p class="feat-empty">Couldn\u2019t load this recipe.</p>';
      return;
    }
    if (my !== S.token) return;
    S.card = card;
    const byId = {};
    const ings = card.ingredients || [];
    ings.forEach((i) => { if (i.product) byId[i.product.id] = { product: i.product, availability: i.availability, role: card.title }; });
    const buyable = ings.filter((i) => i.product && !i.optional);
    const total = buyable.reduce((a, i) => a + Number(i.product.price || 0), 0);
    const opts = [card.servings + ' servings', card.minutes + ' min', card.kid_friendly ? 'Kid-friendly' : '', card.quick ? 'Quick' : ''].filter(Boolean).join(' \u2022 ');
    o.title(card.title);
    o.body.innerHTML = '<div class="feat-hero">' + dishImg(card) + '<div><h3>' + U.esc(card.title) + '</h3><p>' + U.esc(opts) + '</p>' +
      (card.description ? '<p>' + U.esc(card.description) + '</p>' : '') + '</div></div>' +
      ((card.notes || []).length ? '<ul class="feat-notes">' + card.notes.map((n) => '<li>' + U.esc(n) + '</li>').join('') + '</ul>' : '') +
      '<div class="feat-card"><h3>Ingredients (' + ings.length + ')</h3>' + ings.map((i) => {
        const p = i.product;
        const sh = U.shelf(i.availability);
        return '<div class="feat-ing">' + U.thumb(p && p.image, p && p.title, 'feat-thumb', '\u{1F955}') +
          '<div class="feat-ing__main"><span class="feat-ing__name">' + U.esc(i.name) + (i.amount ? ' <span class="feat-ing__amt">' + U.esc(i.amount) + '</span>' : '') +
          (i.optional ? ' <span class="feat-ing__amt">(optional)</span>' : '') + '</span>' +
          (p ? '<span class="feat-ing__prod" data-feat-detail="' + U.esc(p.id) + '">' + U.esc(p.title) + '</span>' +
            '<span class="feat-ing__meta">' + U.price(p) + U.pill(i.availability) + (sh ? '<span>\u{1F4CD} ' + U.esc(sh) + '</span>' : '') + '</span>'
            : '<span class="feat-ing__meta">Not found in this store \u2014 ask staff</span>') +
          '</div>' + (p ? U.addBtn(p.id) : '') + '</div>';
      }).join('') + '</div>' +
      '<div class="feat-card"><h3>Steps</h3><ol class="feat-steps">' + (card.steps || []).map((s) => '<li>' + U.esc(s.text) +
        (s.tip ? '<span class="feat-tip">Tip: ' + U.esc(s.tip) + '</span>' : '') + '</li>').join('') + '</ol></div>' +
      '<div data-feat-forgot></div>';
    U.wireAdd(o.body, (pid) => byId[pid]);
    o.body.querySelectorAll('[data-feat-detail]').forEach((el) => el.addEventListener('click', () => window.Detail && window.Detail.open(el.dataset.featDetail)));
    o.foot.innerHTML = '<button type="button" class="feat-btn"' + (buyable.length ? '' : ' disabled') + ' data-addall>Add all ingredients to cart' +
      (buyable.length ? ' \u2022 ' + U.yen(total) : '') + '</button>';
    o.foot.querySelector('[data-addall]').addEventListener('click', async (e) => {
      const btn = e.currentTarget;
      if (!window.Cart) return;
      const SN = window.SN || {};
      const chOf = (p) => (SN.channelOf ? SN.channelOf(p) : p.channel);
      const take = buyable.filter((i) => chOf(i.product) !== 'online_only');
      const skipped = buyable.length - take.length;
      window.Cart.addMany(take.map((i) => ({ product: i.product, availability: i.availability || null, role: card.title })));
      o.body.querySelectorAll('[data-feat-add]').forEach((b) => U.setAddBtn(b, U.inCart(b.dataset.featAdd)));
      U.toast('Added ' + take.length + ' ingredients to your cart' + (skipped ? ' \u2014 ' + skipped + ' online-only item' + (skipped > 1 ? 's' : '') + ' skipped' : ''));
      btn.textContent = 'Added to cart \u2014 view cart';
      btn.onclick = (ev) => { ev.stopImmediatePropagation(); close(); if (window.Cart.open) window.Cart.open(); };
      if (window.Forgot) {
        const items = await window.Forgot.check(U.cartIds(), card.id);
        const box = o.body.querySelector('[data-feat-forgot]');
        if (box && items.length) { window.Forgot.renderInto(box, items); box.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }
      }
    }, { once: true });
    o.body.scrollTop = 0;
  }

  // ------------------------------------------------------------------------------------------- public API
  function open(query) {
    if (!window.FeatUI) return;
    return showDishes(typeof query === 'string' ? query : '');
  }

  function openDish(d, query) {
    S.query = query || S.query || '';
    customize(d);
  }

  async function renderInline(container, query) {
    if (!container || !window.FeatUI) return [];
    container.classList.add('feat-inline');
    container.innerHTML = '<div class="feat-skel" style="min-height:84px"></div>';
    let dishes = [];
    try { dishes = await fetchDishes(query); } catch (e) { dishes = []; }
    if (!dishes.length) { container.innerHTML = ''; return []; }
    container.innerHTML = dishCards(dishes) + '<div style="margin-top:8px"><button type="button" class="feat-btn feat-btn--tonal feat-btn--sm" data-more>More recipe ideas</button></div>';
    container.querySelectorAll('[data-dish]').forEach((b) => b.addEventListener('click', () => openDish(dishes.find((d) => d.id === b.dataset.dish), query)));
    container.querySelector('[data-more]').addEventListener('click', () => open(query || ''));
    return dishes;
  }

  function close() { const el = document.getElementById('feat-recipes'); if (el && !el.hidden) ov().close(); }

  window.Recipes = { open, openDish, renderInline, close };
})();
