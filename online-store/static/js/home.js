/* Home: general retail home page (always the first screen).
 *   hero carousel (Sale / In season / New arrivals) + side promos
 *   "Recommended for Sakura" -> AI Concierge promo -> This week's deals -> In season -> New arrivals -> Buy it again -> Popular
 *   -> Shop by category
 * Rows come from GET /api/recommendations?type=… (local catalog + seed/personas), categories from /api/home. */
(function () {
  'use strict';
  const CR = window.CR;
  const { esc, cardHtml, fetchJSON, SPARK } = CR;
  const ROWS = [
    { type: 'for_you', id: 'cr-row-foryou', sec: 'cr-sec-foryou', title: (n) => 'Recommended for ' + firstName(n), sub: "Picked from your purchase history and your family's favorites (fish & Japanese dishes, meat, hamburger steak, fruit and more)", reason: true, tags: true },
    { type: 'promo' },
    { type: 'sale', id: 'cr-row-sale', sec: 'cr-sec-sale', title: () => "This week's deals", sub: 'Great prices on everyday groceries, all in one place', tags: false },
    { type: 'seasonal', id: 'cr-row-seasonal', sec: 'cr-sec-seasonal', title: () => 'In season', sub: 'Autumn salmon, Pacific saury, mushrooms, sweet potatoes - at their best right now', tags: false },
    { type: 'new', id: 'cr-row-new', sec: 'cr-sec-new', title: () => 'New arrivals', sub: 'Kitchen, beauty, pet and household goods - delivered together with your groceries', tags: false },
    { type: 'buy_again', id: 'cr-row-again', sec: 'cr-sec-again', title: () => 'Buy it again', sub: 'Your usual items, one click away', reason: true, tags: false },
    { type: 'popular', id: 'cr-row-popular', sec: 'cr-sec-popular', title: () => 'Popular', sub: 'Top sellers ranked by review score and number of reviews', rank: true, tags: false },
  ];
  const SLIDES = [
    { key: 'sale', type: 'sale', target: 'cr-sec-sale', eyebrow: "This week's sale", title: 'Save more on\nyour everyday shopping', text: 'Up to 40% off popular groceries and household essentials. Free shipping over ¥3,000.', cta: 'Shop the deals' },
    { key: 'season', type: 'seasonal', target: 'cr-sec-seasonal', eyebrow: 'In season', title: 'Autumn flavors,\nstraight to your table', text: 'Rich autumn salmon, Pacific saury, mushrooms and sweet potatoes - fresh from the farm and the sea.', cta: 'Shop in-season picks' },
    { key: 'new', type: 'new', target: 'cr-sec-new', eyebrow: 'New arrivals', title: 'Kitchen & home goods,\nnow at Cymbal Retail', text: 'From frying pans to pet supplies and beauty - delivered together with your groceries.', cta: 'See new arrivals' },
  ];
  const cache = {};
  const firstName = (n) => String(n || '').trim().split(/\s+/)[0] || 'you';
  let carouselTimer = null;
  const ICON_PREV = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15.41 7.41 14 6l-6 6 6 6 1.41-1.41L10.83 12z"/></svg>';
  const ICON_NEXT = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 6 8.59 7.41 13.17 12l-4.58 4.59L10 18l6-6z"/></svg>';

  function load(type) {
    if (!cache[type]) cache[type] = fetchJSON('/api/recommendations?type=' + type + '&limit=16').catch((e) => { delete cache[type]; throw e; });
    return cache[type];
  }

  function heroHtml() {
    return '<section class="cr-hero2" id="cr-hero" aria-roledescription="carousel" aria-label="Featured">' +
      '<div class="cr-hero2__viewport"><div class="cr-hero2__track" id="cr-hero-track">' +
      SLIDES.map((s, i) =>
        '<article class="cr-slide cr-slide--' + s.key + '" data-slide="' + i + '" aria-roledescription="slide" aria-label="' + (i + 1) + ' / ' + SLIDES.length + '"' + (i ? ' aria-hidden="true"' : '') + '>' +
          '<div class="cr-slide__copy">' +
            '<span class="cr-slide__eyebrow">' + esc(s.eyebrow) + '</span>' +
            '<h2>' + esc(s.title).replace('\n', '<br>') + '</h2>' +
            '<p>' + esc(s.text) + '</p>' +
            '<button type="button" class="cr-btn cr-slide__cta" data-hero-go="' + s.target + '" id="cr-hero-cta-' + s.key + '">' + esc(s.cta) + ICON_NEXT + '</button>' +
          '</div>' +
          '<div class="cr-slide__art" id="cr-slide-art-' + s.key + '"><span class="cr-slide__ph"></span><span class="cr-slide__ph"></span><span class="cr-slide__ph"></span></div>' +
        '</article>').join('') +
      '</div></div>' +
      '<button type="button" class="cr-hero2__nav cr-hero2__nav--prev" id="cr-hero-prev" aria-label="Previous">' + ICON_PREV + '</button>' +
      '<button type="button" class="cr-hero2__nav cr-hero2__nav--next" id="cr-hero-next" aria-label="Next">' + ICON_NEXT + '</button>' +
      '<div class="cr-hero2__dots" role="tablist">' + SLIDES.map((s, i) => '<button type="button" role="tab" class="cr-hero2__dot' + (i ? '' : ' is-active') + '" data-dot="' + i + '" aria-label="' + esc(s.eyebrow) + '"></button>').join('') + '</div>' +
    '</section>';
  }

  function sideHtml(name) {
    return '<aside class="cr-hero-side">' +
      '<button type="button" class="cr-promo cr-promo--ai" id="cr-hero-chat">' +
        '<span class="cr-promo__icon">' + SPARK + '</span>' +
        '<span class="cr-promo__body"><b>AI Concierge</b><span>Tailored to ' + esc(firstName(name)) + "'s family - from tonight's dinner plan all the way to checkout</span></span>" +
        '<span class="cr-promo__go">Ask now' + ICON_NEXT + '</span>' +
      '</button>' +
      '<div class="cr-promo cr-promo--ship">' +
        '<span class="cr-promo__emoji" aria-hidden="true">🚚</span>' +
        '<span class="cr-promo__body"><b>Free shipping over ¥3,000</b><span>Delivery tomorrow, 9:00-21:00</span></span>' +
      '</div>' +
      '<div class="cr-promo cr-promo--pts">' +
        '<span class="cr-promo__emoji" aria-hidden="true">🎁</span>' +
        '<span class="cr-promo__body"><b>2x Cymbal Points</b><span>Pay with Cymbal Pay, through this weekend</span></span>' +
      '</div>' +
    '</aside>';
  }

  function rowHtml(r, name) {
    if (r.type === 'promo') {
      return '<section class="cr-ai-banner" id="cr-ai-banner">' +
        '<span class="cr-ai-banner__icon">' + SPARK + '</span>' +
        '<div class="cr-ai-banner__copy"><h2>Let the AI Concierge plan tonight\'s dinner</h2><p>Based on your purchase history, your family\'s tastes, this week\'s deals and what\'s in season, it suggests a Main, Side and Soup set.</p></div>' +
        '<button type="button" class="cr-btn cr-btn--primary" data-ask="Recommend something for dinner tonight" id="cr-ai-banner-ask">&ldquo;Recommend something for dinner tonight&rdquo;</button>' +
      '</section>';
    }
    return '<section class="cr-section cr-shelf" id="' + r.sec + '" data-type="' + r.type + '">' +
      '<div class="cr-section__head"><div><h2>' + esc(r.title(name)) + '</h2><p class="cr-section__sub">' + esc(r.sub) + '</p></div>' +
        '<div class="cr-shelf__nav"><button type="button" class="cr-shelf__btn" data-scroll="' + r.id + '" data-dir="-1" aria-label="Scroll left">' + ICON_PREV + '</button>' +
        '<button type="button" class="cr-shelf__btn" data-scroll="' + r.id + '" data-dir="1" aria-label="Scroll right">' + ICON_NEXT + '</button></div></div>' +
      '<div class="cr-row" id="' + r.id + '">' + CR.skeletonGrid(6) + '</div>' +
    '</section>';
  }

  async function render() {
    const v = CR.view();
    const name = CR.getCustomer().name;
    clearInterval(carouselTimer);
    v.innerHTML = '<div class="cr-container" id="cr-home">' +
      '<h1 class="cr-visually-hidden">Cymbal Retail Online Store</h1>' +
      '<div class="cr-hero-grid">' + heroHtml() + sideHtml(name) + '</div>' +
      ROWS.map((r) => rowHtml(r, name)).join('') +
      '<section class="cr-section" id="cr-sec-cats"><div class="cr-section__head"><div><h2>Shop by category</h2><p class="cr-section__sub">From groceries to household and kitchen essentials</p></div></div><div class="cr-cats" id="cr-cats"></div></section>' +
    '</div>';
    document.getElementById('cr-hero-chat').onclick = () => window.CRChat && window.CRChat.open();
    startCarousel();

    ROWS.filter((r) => r.id).forEach((r) => {
      load(r.type).then((res) => {
        const el = document.getElementById(r.id);
        if (!el) return;
        const items = res.items || [];
        CR.remember(items);
        el.innerHTML = items.length ? items.map((p, i) => cardHtml(p, { i, reason: r.reason ? p.reason : '', tags: r.tags ? (p.tags || []).slice(0, 2) : null, rank: r.rank ? p.rank : 0 })).join('')
          : '<p class="cr-row__empty">No products to show right now</p>';
        updateShelfNav(el);
      }).catch(() => { const el = document.getElementById(r.id); if (el) el.innerHTML = '<p class="cr-row__empty">Couldn\'t load these items</p>'; });
    });
    SLIDES.forEach((s) => load(s.type).then((res) => {
      const art = document.getElementById('cr-slide-art-' + s.key);
      if (!art) return;
      const ps = (res.items || []).slice(0, 3);
      art.innerHTML = ps.map((p, i) => '<button type="button" class="cr-slide__prod cr-slide__prod--' + i + '" data-open="' + esc(p.id) + '" aria-label="' + esc(p.title) + '">' +
        '<img src="' + esc(p.image) + '" alt="">' +
        (s.type === 'sale' && p.on_sale ? '<span class="cr-slide__tag">' + Math.round((p.discount_rate || 0) * 100) + '% OFF</span>' : '') +
        (s.type === 'seasonal' ? '<span class="cr-slide__tag cr-slide__tag--season">In season</span>' : '') +
        (s.type === 'new' ? '<span class="cr-slide__tag cr-slide__tag--new">NEW</span>' : '') + '</button>').join('');
    }).catch(() => {}));
    try {
      cache.home = cache.home || fetchJSON('/api/home');
      const home = await cache.home;
      const cats = document.getElementById('cr-cats');
      if (!cats) return;
      cats.innerHTML = home.categories.slice(0, 20).map((c) =>
        '<button type="button" class="cr-cat" data-cat="' + esc(c.name) + '"><span class="cr-cat__img"><img src="' + esc(c.image) + '" alt="" loading="lazy"></span><span class="cr-cat__name">' + esc(c.name) + '</span></button>').join('');
    } catch (e) { delete cache.home; }
  }

  // ---------------- carousel ----------------
  let slide = 0;
  function goSlide(i) {
    const track = document.getElementById('cr-hero-track');
    if (!track) return;
    slide = (i + SLIDES.length) % SLIDES.length;
    track.style.transform = 'translateX(' + (-100 * slide) + '%)';
    track.querySelectorAll('.cr-slide').forEach((el, k) => { if (k === slide) el.removeAttribute('aria-hidden'); else el.setAttribute('aria-hidden', 'true'); });
    document.querySelectorAll('#cr-hero .cr-hero2__dot').forEach((d, k) => d.classList.toggle('is-active', k === slide));
  }
  function startCarousel() {
    slide = 0;
    clearInterval(carouselTimer);
    const hero = document.getElementById('cr-hero');
    if (!hero || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    let paused = false;
    hero.addEventListener('mouseenter', () => { paused = true; });
    hero.addEventListener('mouseleave', () => { paused = false; });
    carouselTimer = setInterval(() => {
      if (!document.getElementById('cr-hero')) { clearInterval(carouselTimer); return; }
      if (!paused && !document.hidden) goSlide(slide + 1);
    }, 6500);
  }

  function updateShelfNav(row) {
    const sec = row.closest('.cr-shelf');
    if (!sec) return;
    const max = row.scrollWidth - row.clientWidth - 4;
    sec.querySelectorAll('.cr-shelf__btn').forEach((b) => { b.disabled = Number(b.dataset.dir) < 0 ? row.scrollLeft <= 4 : row.scrollLeft >= max; });
  }
  document.addEventListener('scroll', (e) => { const t = e.target; if (t && t.classList && t.classList.contains('cr-row')) updateShelfNav(t); }, true);

  document.addEventListener('click', (e) => {
    const sb = e.target.closest('[data-scroll]');
    if (sb) { const row = document.getElementById(sb.dataset.scroll); if (row) row.scrollBy({ left: Number(sb.dataset.dir) * row.clientWidth * 0.85, behavior: 'smooth' }); return; }
    const go = e.target.closest('[data-hero-go]');
    if (go) { const s = document.getElementById(go.dataset.heroGo); if (s) window.scrollTo({ top: s.getBoundingClientRect().top + window.scrollY - 76, behavior: 'smooth' }); return; }
    const dot = e.target.closest('[data-dot]');
    if (dot) { goSlide(Number(dot.dataset.dot)); return; }
    if (e.target.closest('#cr-hero-prev')) { goSlide(slide - 1); return; }
    if (e.target.closest('#cr-hero-next')) { goSlide(slide + 1); return; }
    const ask = e.target.closest('[data-ask]');
    if (ask) { window.CRChat && window.CRChat.ask(ask.dataset.ask); return; }
    const q = e.target.closest('[data-q]');
    if (q) { e.preventDefault(); window.Search.newSearch(q.dataset.q); return; }
    const c = e.target.closest('[data-cat]');
    if (c) {
      const name = c.dataset.cat;
      window.Search.newSearch(name);
      window.Search.state.facets = { 'attributes.top_category': [name] };
      window.Search.run();
    }
  });

  window.Home = { render, goSlide };
})();
