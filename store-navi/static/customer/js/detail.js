/* Cymbal Retail store app — product detail overlay (window.Detail.open(productId)). Requires forgot.js (window.FeatUI). */
(function () {
  'use strict';
  let token = 0;

  function chBadge(ch) {
    if (ch === 'store_only') return '<span class="feat-tag feat-tag--ch">In-store only</span>';
    if (ch === 'online_only') return '<span class="feat-tag feat-tag--ch">Online only</span>';
    return '';
  }

  function render(ov, d) {
    const U = window.FeatUI;
    const p = d.product, av = d.availability, h = d.history;
    const c = U.cur(av);
    const isIn = U.IN.indexOf(c.status) >= 0;
    const sh = U.shelf(av);
    const nearby = ((av && av.nearby) || []).filter((r) => U.IN.indexOf(r.status) >= 0).slice(0, 2);
    const SN = window.SN || {};
    const ch = SN.channelOf ? SN.channelOf(p) : p.channel;
    const badges = [p.on_sale ? '<span class="feat-tag feat-tag--sale">Sale</span>' : '', SN.channelBadge ? SN.channelBadge(p) : chBadge(ch),
      p.brand ? '<span class="feat-tag">' + U.esc(p.brand) + '</span>' : '', p.size ? '<span class="feat-tag">' + U.esc(p.size) + '</span>' : '']
      .join('');
    const orig = Number(p.original_price || 0);
    ov.title(p.title || 'Product');
    ov.body.innerHTML = '<div class="feat-detail__grid">' +
      U.thumb(p.image, p.title, 'feat-detail__img', '\u{1F6D2}') +
      '<div>' +
        '<h3 class="feat-detail__title">' + U.esc(p.title) + '</h3>' +
        (badges ? '<div class="feat-tags" style="margin-bottom:6px">' + badges + '</div>' : '') +
        '<div class="feat-detail__price">' + U.yen(p.price) + (orig > Number(p.price || 0) ? '<s>' + U.yen(orig) + '</s>' : '') +
          ' <span style="font:400 12px var(--sn-font);color:var(--sn-muted)">tax incl.</span></div>' +
        (p.promo_text ? '<p class="feat-detail__brand">' + U.esc(p.promo_text) + '</p>' : '') +
        (h ? '<div class="feat-hist">' + U.ICONS.history + '<span>' + U.esc(h.label) + '</span></div>' : '') +
        (ch === 'online_only' ? '<div class="feat-card" style="margin:10px 0"><h3>Online only</h3><p class="feat-detail__brand">This item isn\u2019t sold on store shelves.</p>' +
          (SN.buyOnlineLink ? SN.buyOnlineLink(p.id, 'Online only \u2014 Buy online') : '') + '</div>' :
        '<div class="feat-card" style="margin:10px 0"><h3>At this store' + (ch === 'store_only' ? ' <span class="feat-tag feat-tag--ch">In-store only</span>' : '') +
          '</h3>' + (SN.sourceBadge && av ? '<div style="margin:-4px 0 8px">' + SN.sourceBadge(av.source) + '</div>' : '') + '<div class="feat-stock">' +
          '<div class="feat-stock__row"><span>' + U.esc(c.short_name || 'This store') + '</span>' + (U.pill(av) || '<span class="feat-pill feat-pill--not_carried">Unknown</span>') + '</div>' +
          (sh ? '<div class="feat-stock__shelf">\u{1F4CD} ' + U.esc(sh) + '</div>' : '') +
          (!isIn && nearby.length ? nearby.map((r) => '<div class="feat-stock__row"><span>' + U.esc(r.short_name) +
            (r.distance_m != null ? ' \u2022 ' + (r.distance_m < 1000 ? Math.round(r.distance_m) + ' m' : (r.distance_m / 1000).toFixed(1) + ' km') : '') +
            '</span>' + U.pill({ current: r }) + '</div>').join('') : '') +
          (ch !== 'store_only' && av && av.online && av.online.available ? '<div class="feat-stock__row"><span>Online store</span>' +
            (SN.buyOnlineLink ? SN.buyOnlineLink(p.id, 'Buy online') : '<a class="feat-btn feat-btn--tonal feat-btn--sm" href="' + U.esc(av.online.url) + '" target="_blank" rel="noopener">Buy online</a>') + '</div>' : '') +
        '</div></div>') +
        (p.description ? '<p class="feat-detail__desc">' + U.esc(p.description) + '</p>' : '') +
      '</div></div>';
    const added = U.inCart(p.id);
    ov.foot.innerHTML = '<button type="button" class="feat-btn feat-btn--tonal" data-feat-shelf' + (c.shelf ? '' : ' disabled') + '>' +
      U.ICONS.map.replace('<svg', '<svg width="18" height="18" style="fill:currentColor"') + 'Find on shelf</button>' +
      '<button type="button" class="feat-btn" data-feat-cart>' + (added ? 'In cart' : 'Add to cart') + '</button>';
    ov.foot.querySelector('[data-feat-cart]').addEventListener('click', (e) => {
      if (!window.Cart) return;
      if (window.Cart.toggle) window.Cart.toggle({ product: p, availability: av || null });
      else window.Cart.addMany([{ product: p, availability: av || null }]);
      e.currentTarget.textContent = U.inCart(p.id) ? 'In cart' : 'Add to cart';
    });
    ov.foot.querySelector('[data-feat-shelf]').addEventListener('click', () => {
      if (!window.FloorMap || !c.shelf) { U.toast('Shelf location is not available for this store'); return; }
      ov.close();
      window.FloorMap.open({ storeId: U.storeId(), items: [{ product: p, availability: av }] });
    });
  }

  async function open(productId) {
    const U = window.FeatUI;
    if (!U || !productId) return;
    const ov = U.overlay('feat-detail', 'Product', null);
    const my = ++token;
    ov.body.innerHTML = '<div class="feat-skel" style="min-height:260px"></div>';
    ov.foot.innerHTML = '';
    ov.show();
    try {
      const d = await U.fetchJSON('/api/products/' + encodeURIComponent(productId) + '/detail?store_id=' + encodeURIComponent(U.storeId()));
      if (my !== token) return;
      render(ov, d);
    } catch (e) {
      if (my !== token) return;
      ov.body.innerHTML = '<p class="feat-empty">Sorry, we couldn\u2019t load this product.</p>';
    }
  }

  function close() { const el = document.getElementById('feat-detail'); if (el && !el.hidden) window.FeatUI.overlay('feat-detail').close(); }

  window.Detail = { open, close };
})();
