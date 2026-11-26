/* Search page: AI Commerce Search results with Tile Navigation, Conversational Search refine bar,
 * AI Summary, Dynamic Facets and product grid. Also the agent views (show_products_on_page / show_comparison). */
(function () {
  'use strict';
  const CR = window.CR;
  const { esc, yen, fetchJSON, cardHtml, skeletonGrid, featureLabel, setFeatureStatus, SPARK } = CR;
  const PAGE_SIZE = 24;
  const ICON_CHAT = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2c.4 4.9 2.9 8.5 9 10-6.1 1.5-8.6 5.1-9 10-.4-4.9-2.9-8.5-9-10 6.1-1.5 8.6-5.1 9-10z"/></svg>';

  const state = {
    q: null, apiQuery: '', facets: {}, tiles: [], conversationId: '', trail: [], page: 1,
    resp: null, results: [], highlights: {}, conv: null, seq: 0,
  };
  const agent = { message: '', title: '', products: [], reasons: {} };
  const compare = { title: '', products: [], rows: [] };

  function reset(q) {
    Object.assign(state, { q, apiQuery: '', facets: {}, tiles: [], conversationId: '', trail: [], page: 1, resp: null, results: [], highlights: {}, conv: null });
  }

  function buildUrl(extra) {
    const p = new URLSearchParams();
    p.set('q', state.q || '');
    if (Object.keys(state.facets).length) p.set('facets', JSON.stringify(state.facets));
    if (state.tiles.length) p.set('tiles', JSON.stringify(state.tiles.map((t) => ({ key: t.key, value: t.value, label: t.label }))));
    if (state.conversationId) p.set('conversation_id', state.conversationId);
    if (state.apiQuery && state.apiQuery !== state.q && !(extra && extra.answer)) p.set('rq', state.apiQuery);
    p.set('page', String(state.page));
    p.set('page_size', String(PAGE_SIZE));
    Object.keys(extra || {}).forEach((k) => extra[k] && p.set(k, extra[k]));
    return '/api/search?' + p.toString();
  }

  // ---------------- layout ----------------
  function ensureLayout() {
    const v = CR.view();
    if (v.querySelector('#cr-search-page')) return;
    v.innerHTML =
      '<div id="cr-search-page">' +
        '<div class="cr-tiles-wrap"><div class="cr-container">' + featureLabel('Tile Navigation', 'tile_navigation') + '<div class="cr-tiles" id="cr-tiles" role="list"></div></div></div>' +
        '<div class="cr-container"><div class="cr-results">' +
          '<aside class="cr-facets" id="cr-facets" aria-label="Filters"></aside>' +
          '<section id="cr-results-main">' +
            '<div class="cr-results__head" id="cr-results-head"></div>' +
            '<div id="cr-refine-mount"></div>' +
            '<div id="cr-ai-mount"></div>' +
            '<div class="cr-grid" id="cr-grid"></div>' +
            '<div class="cr-pager" id="cr-pager"></div>' +
          '</section>' +
        '</div></div>' +
      '</div>';
  }

  // ---------------- run ----------------
  async function run(opts) {
    opts = opts || {};
    const q = opts.q !== undefined ? opts.q : state.q;
    if (q !== state.q) reset(q);
    ensureLayout();
    const my = ++state.seq;
    const append = !!opts.append;
    if (!append) {
      document.getElementById('cr-grid').innerHTML = skeletonGrid(8);
      document.getElementById('cr-results-head').innerHTML = '<h1>Results for "' + esc(state.q) + '"</h1>';
    }
    let resp;
    try {
      resp = await fetchJSON(buildUrl(opts.extra));
    } catch (e) {
      if (my !== state.seq) return;
      document.getElementById('cr-grid').innerHTML = '<div class="cr-empty" style="grid-column:1/-1"><h2>Something went wrong while searching</h2><p>Please try again in a moment.</p></div>';
      return;
    }
    if (my !== state.seq) return;
    state.resp = resp;
    setFeatureStatus(resp.features || {});
    if (resp.conversation && resp.conversation.source === 'retail' && resp.conversation.id) state.conversationId = resp.conversation.id;
    if (resp.conversation_turn) state.apiQuery = resp.results_query || '';
    CR.remember(resp.results);
    state.results = append ? state.results.concat(resp.results) : resp.results;
    renderHead(resp);
    renderTiles(resp.tiles || []);
    renderFacets(resp.facets || []);
    const keepLive = !resp.conversation_turn && state.conv && state.conv.source === 'retail' && !(resp.conversation && resp.conversation.source === 'retail');
    if (!keepLive) state.conv = resp.conversation || null;
    renderRefine(state.conv);
    renderGrid(append);
    if (!append) {
      state.highlights = {};
      const mount = document.getElementById('cr-ai-mount');
      window.AISummary.load(state.q, resp.results.slice(0, 8), mount).then((ai) => {
        if (!ai || my !== state.seq) return;
        (ai.highlights || []).forEach((h) => { state.highlights[h.product_id] = h.reason; });
        renderGrid(false, true);
        const fu = ai.followup || {};
        if (state.conv && state.conv.source === 'retail' && !(state.conv.answer_actions || []).length && (fu.answers || []).length) {
          // live conversation without answer chips → Gemini chips are sent as the next conversational turn
          state.conv = Object.assign({}, state.conv, {
            followup_question: state.conv.followup_question || fu.question,
            answer_actions: fu.answers.map((a) => ({ label: a, kind: 'turn', value: a })), chips_by: 'gemini',
          });
          renderRefine(state.conv);
        } else if ((!state.conv || state.conv.source !== 'retail') && fu.question && (fu.answers || []).length) {
          state.conv = {
            id: '', refined_query: state.q, followup_question: fu.question, suggested_answers: fu.answers,
            answer_actions: fu.answers.map((a) => ({ label: a, kind: 'query', value: a })), source: 'gemini',
          };
          renderRefine(state.conv);
        }
      });
    }
    CR.emit('context');
  }

  function newSearch(q) {
    reset(q);
    window.Router.go({ q });
  }

  // ---------------- sections ----------------
  function renderHead(resp) {
    const head = document.getElementById('cr-results-head');
    const total = resp.total || 0;
    let h = '<h1>Results for "' + esc(state.q) + '"</h1><span class="cr-results__count">About ' + total.toLocaleString('en-US') + (total === 1 ? ' result' : ' results') + '</span>';
    if (resp.corrected_query && resp.corrected_query !== state.q) {
      h += '<div class="cr-corrected">' + featureLabel('Spell Correction', 'spell_correction') + ' Showing results for "<b>' + esc(resp.corrected_query) + '</b>"</div>';
    }
    if (resp.engine === 'local-fallback') h += '<div class="cr-corrected" style="color:#b06000">Note: the search index is still being prepared, so these results come from local search.</div>';
    head.innerHTML = h;
  }

  function renderTiles(tiles) {
    const el = document.getElementById('cr-tiles');
    const selected = state.tiles.map((t) => Object.assign({}, t, { selected: true }));
    const rest = tiles.filter((t) => !state.tiles.some((s) => s.key === t.key && s.value === t.value)).slice(0, 14);
    const all = selected.concat(rest);
    el.innerHTML = all.length ? all.map((t, i) =>
      '<button type="button" class="cr-chip' + (t.selected ? ' is-selected' : '') + '" role="listitem" data-tile="' + i + '">' +
        (t.image && !t.selected ? '<img class="cr-chip__img" src="' + esc(t.image) + '" alt="">' : '') + esc(t.label) + '</button>').join('')
      : '<span style="color:var(--cr-muted);font-size:13px">No refinements available</span>';
    el._tiles = all;
  }

  function renderFacets(facets) {
    const el = document.getElementById('cr-facets');
    const anySel = Object.values(state.facets).some((v) => v.length) || state.tiles.length;
    el.innerHTML =
      '<div class="cr-facets__head"><h2>Filters</h2>' + featureLabel('Dynamic Facets', 'dynamic_facets') + '</div>' +
      (anySel ? '<button type="button" class="cr-link-btn" id="cr-facet-clear">Clear all</button>' : '') +
      facets.map((f) => {
        const open = el.dataset['open_' + f.key] === '1';
        const vals = open ? f.values : f.values.slice(0, 6);
        return '<div class="cr-facet" data-key="' + esc(f.key) + '">' +
          '<div class="cr-facet__title">' + esc(f.label) + (f.dynamic ? '<span class="cr-dyn" title="Facet selected automatically for this query">Auto</span>' : '') + '</div>' +
          '<ul class="cr-facet__list">' + vals.map((v) =>
            '<li><label><input type="checkbox" data-fkey="' + esc(f.key) + '" data-fval="' + esc(v.value) + '"' + (isFacetSelected(f.key, v.value) || v.selected ? ' checked' : '') + '>' +
            '<span>' + esc(v.label || v.value) + '</span><span class="cr-count">' + (v.count || '') + '</span></label></li>').join('') + '</ul>' +
          (f.values.length > 6 ? '<button type="button" class="cr-facet__more" data-more="' + esc(f.key) + '">' + (open ? 'Show less' : 'Show more (' + f.values.length + ')') + '</button>' : '') +
        '</div>';
      }).join('');
    el._facets = facets;
  }
  function isFacetSelected(k, v) { return (state.facets[k] || []).includes(v) || state.tiles.some((t) => t.key === k && t.value === v); }

  function renderRefine(conv) {
    const mount = document.getElementById('cr-refine-mount');
    if (!mount) return;
    const acts = (conv && conv.answer_actions) || [];
    const live = conv && conv.source === 'retail';
    const refined = live && conv.refined_query && conv.refined_query !== state.q ? conv.refined_query : '';
    if (!conv || (!conv.followup_question && !acts.length && !refined)) {
      mount.innerHTML = state.trail.length ? '<div class="cr-refine"><div class="cr-refine__trail">' + trailHtml() + '</div></div>' : '';
      return;
    }
    mount.innerHTML =
      '<div class="cr-refine" id="cr-refine" data-source="' + esc(conv.source) + '">' +
        (refined ? '<div class="cr-refine__refined" id="cr-refine-refined">' + ICON_CHAT + '<span>AI refined your search to "<b>' + esc(refined) + '</b>"</span></div>' : '') +
        (conv.followup_question ? '<div class="cr-refine__q">' + (refined ? '' : ICON_CHAT) + '<span id="cr-refine-question">' + esc(conv.followup_question) + '</span></div>' : '') +
        (acts.length ? '<div class="cr-refine__answers" id="cr-refine-answers">' + acts.map((a, i) => '<button type="button" class="cr-chip" data-answer="' + i + '">' + esc(a.label) + '</button>').join('') + '</div>' : '') +
        featureLabel('Conversational Search', 'conversational') +
        (state.trail.length ? '<div class="cr-refine__trail">' + trailHtml() + '</div>' : '') +
      '</div>';
    mount.querySelector('#cr-refine')._acts = acts;
  }
  function trailHtml() { return state.trail.map((t) => '<span>' + esc(t.q) + ' → <b>' + esc(t.a) + '</b></span>').join(''); }

  function renderGrid(append, refreshOnly) {
    const grid = document.getElementById('cr-grid');
    if (!grid) return;
    if (!state.results.length) {
      grid.innerHTML = '<div class="cr-empty" style="grid-column:1/-1"><h2>No matching products found</h2><p>Try different keywords, or ask the AI Concierge for help.</p></div>';
      document.getElementById('cr-pager').innerHTML = '';
      return;
    }
    grid.innerHTML = state.results.map((p, i) => cardHtml(p, { i: refreshOnly ? 0 : i % PAGE_SIZE, highlight: !!state.highlights[p.id], reason: state.highlights[p.id] })).join('');
    if (refreshOnly) grid.querySelectorAll('.cr-card').forEach((c) => { c.style.animation = 'none'; });
    const more = state.resp && state.results.length < state.resp.total;
    document.getElementById('cr-pager').innerHTML = more ? '<button type="button" class="cr-btn" id="cr-more">Show more</button>' : '';
  }

  // ---------------- interactions ----------------
  function toggleFacet(key, value, on) {
    const cur = state.facets[key] || [];
    const tileIdx = state.tiles.findIndex((t) => t.key === key && t.value === value);
    if (on === false && tileIdx >= 0) state.tiles.splice(tileIdx, 1);
    state.facets[key] = on === undefined ? (cur.includes(value) ? cur.filter((v) => v !== value) : cur.concat([value]))
      : (on ? Array.from(new Set(cur.concat([value]))) : cur.filter((v) => v !== value));
    if (!state.facets[key].length) delete state.facets[key];
    state.page = 1;
  }

  document.addEventListener('click', (e) => {
    const tile = e.target.closest('[data-tile]');
    if (tile) {
      const t = document.getElementById('cr-tiles')._tiles[Number(tile.dataset.tile)];
      if (t.selected) state.tiles = state.tiles.filter((x) => !(x.key === t.key && x.value === t.value));
      else state.tiles.push({ key: t.key, value: t.value, label: t.label });
      state.page = 1;
      run();
      return;
    }
    const ans = e.target.closest('[data-answer]');
    if (ans) {
      const bar = ans.closest('#cr-refine');
      const a = bar._acts[Number(ans.dataset.answer)];
      const question = (state.conv && state.conv.followup_question) || '';
      state.trail.push({ q: question, a: a.label });
      state.page = 1;
      if (a.kind === 'facet') { toggleFacet(a.key, a.value, true); run(); }
      else if (a.kind === 'turn') {
        run({ extra: { answer: a.value } });
      } else if (a.kind === 'answer') {
        const extra = { answer: a.label, answer_key: a.key || '', answer_value: a.value || '' };
        if (a.key && a.value) toggleFacet(a.key, a.value, true);
        run({ extra });
      } else {
        const q2 = (state.q + ' ' + a.value).trim();
        const keep = { facets: state.facets, tiles: state.tiles, trail: state.trail };
        reset(q2); Object.assign(state, keep);
        history.replaceState({}, '', '/?q=' + encodeURIComponent(q2));
        document.getElementById('cr-search-input').value = q2;
        run({ q: q2 });
      }
      return;
    }
    if (e.target.closest('#cr-facet-clear')) { state.facets = {}; state.tiles = []; state.page = 1; run(); return; }
    const more = e.target.closest('[data-more]');
    if (more) {
      const el = document.getElementById('cr-facets');
      const k = 'open_' + more.dataset.more;
      el.dataset[k] = el.dataset[k] === '1' ? '0' : '1';
      renderFacets(el._facets || []);
      return;
    }
    if (e.target.closest('#cr-more')) { state.page += 1; run({ append: true }); }
  });
  document.addEventListener('change', (e) => {
    const cb = e.target.closest('input[data-fkey]');
    if (!cb) return;
    toggleFacet(cb.dataset.fkey, cb.dataset.fval, cb.checked);
    run();
  });

  // ---------------- agent views ----------------
  const BACK = '<button type="button" class="cr-back" data-nav="home" id="cr-back-home"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z"/></svg>Back to home</button>';
  function agentHead(title, message) {
    return BACK + '<div class="cr-agent-head"><span class="cr-agent-head__icon">' + SPARK + '</span><div><h1>' + esc(title) + '</h1>' + (message ? '<p>' + esc(message) + '</p>' : '') + '</div></div>';
  }
  function renderAgent() {
    const v = CR.view();
    if (!agent.products.length) { history.replaceState({}, '', '/'); window.Home.render(); return; }
    if (agent.sections && agent.sections.length) { renderSet(v); return; }
    v.innerHTML = '<div class="cr-container" id="cr-agent-view">' + agentHead(agent.title || 'AI Concierge picks', agent.message) +
      '<div class="cr-grid" id="cr-agent-grid">' + agent.products.map((p, i) => cardHtml(p, { i, reason: agent.reasons[p.id], highlight: !!agent.reasons[p.id] })).join('') + '</div></div>';
  }

  // ---- dinner set (show_products_on_page with theme + sections) ----
  const ROLE_RE = /\b(?:main|side|soup|staple|dessert|fruit|salad|rice|entr[eé]e|appetizer|starter|drinks?|beverages?)\b/i;
  const ROLE_ICON = [[/\b(?:main|entr[eé]e)\b/i, '🍽️'], [/\b(?:side|salad|appetizer|starter)\b/i, '🥗'], [/\bsoup\b/i, '🍲'], [/\b(?:staple|rice)\b/i, '🍚'], [/\b(?:dessert|fruit)\b/i, '🍎'], [/\b(?:drinks?|beverages?)\b/i, '🍵']];
  const roleIcon = (label) => { const m = ROLE_ICON.find(([re]) => re.test(label || '')); return m ? m[1] : '✨'; };
  function asList(v) {
    if (Array.isArray(v)) return v;
    if (typeof v === 'string') {
      const s = v.trim();
      if (s.startsWith('[')) { try { return JSON.parse(s); } catch (e) { /* fallthrough */ } }
      return s ? s.split(/,\s*/) : [];
    }
    return [];
  }
  /** normalize sections[] coming from the agent (strings / JSON strings / product objects are accepted) */
  function normalizeSections(raw) {
    let secs = raw;
    if (typeof secs === 'string') { try { secs = JSON.parse(secs); } catch (e) { secs = []; } }
    if (!Array.isArray(secs)) return [];
    return secs.filter((s) => s && typeof s === 'object').map((s) => {
      const ids = asList(s.product_ids || s.productIds || s.items || []).map((x) => (x && typeof x === 'object' ? (x.product_id || x.id) : x)).filter(Boolean).map(String);
      const reasons = asList(s.reasons || []).map((r) => (r && typeof r === 'object' ? String(r.reason || '') : String(r || '')));
      const tags = asList(s.tags || []).map((t) => asList(t).map(String).map((x) => x.trim()).filter(Boolean));
      // optional quantities (parallel to product_ids; default 1). Accept [2,1] or [[2],[1]].
      const quantities = ids.map((_, i) => {
        let q = asList(s.quantities || s.qty || [])[i];
        if (Array.isArray(q)) q = q[0];
        return Math.max(1, Math.min(99, parseInt(q, 10) || 1));
      });
      return { label: String(s.label || s.role || s.name || ''), product_ids: ids, reasons, tags, quantities };
    }).filter((s) => s.product_ids.length);
  }
  function setModel() {
    const byId = new Map(agent.products.map((p) => [p.id, p]));
    const sections = agent.sections.map((s) => {
      const items = [];
      s.product_ids.forEach((id, i) => { const p = byId.get(id); if (p) items.push({ p, reason: s.reasons[i] || agent.reasons[id] || '', tags: s.tags[i] || [], qty: (s.quantities || [])[i] || 1 }); });
      return { label: s.label, items, subtotal: items.reduce((a, it) => a + (Number(it.p.price) || 0) * it.qty, 0) };
    }).filter((s) => s.items.length);
    const isRoleSet = sections.length > 0 && sections.every((s) => ROLE_RE.test(s.label));
    const count = sections.reduce((a, s) => a + s.items.length, 0);
    const total = sections.reduce((a, s) => a + s.subtotal, 0);
    return { sections, isRoleSet, count, total };
  }
  function renderSet(v) {
    const m = setModel();
    const usedTags = Array.from(new Set(m.sections.flatMap((s) => s.items.flatMap((it) => it.tags))));
    let i = 0;
    v.innerHTML = '<div class="cr-container" id="cr-agent-view" data-mode="set">' +
      agentHead(agent.title || "Tonight's dinner set", agent.message) +
      '<div class="cr-set-bar" id="cr-set-bar">' +
        '<div class="cr-set-bar__sum"><span class="cr-set-bar__label">' + (m.isRoleSet ? 'Set total' : 'Items shown') + '</span>' +
          '<strong id="cr-set-total">' + yen(m.total) + '</strong><span class="cr-set-bar__tax">(tax incl.), ' + m.count + (m.count === 1 ? ' item' : ' items') + '</span></div>' +
        (usedTags.length ? '<div class="cr-set-bar__legend">' + usedTags.map((t) => '<span class="cr-tag cr-tag--' + (CR.TAG_CLASS[t] || 'other') + '">' + esc(t) + '</span>').join('') + '</div>' : '') +
        '<button type="button" class="cr-btn cr-btn--primary cr-set-bar__add" id="cr-set-add">' + (m.isRoleSet ? 'Add whole set to cart' : 'Add all to cart') + '</button>' +
      '</div><div class="cr-set-board" id="cr-set-board">' +
      m.sections.map((s) =>
        '<section class="cr-set-sec" data-role="' + esc(s.label) + '">' +
          '<header class="cr-set-sec__head"><span class="cr-set-sec__icon" aria-hidden="true">' + roleIcon(s.label) + '</span><h2>' + esc(s.label) + '</h2>' +
            '<span class="cr-set-sec__sub">' + s.items.length + (s.items.length === 1 ? ' item, ' : ' items, ') + yen(s.subtotal) + '</span></header>' +
          '<div class="cr-grid cr-set-grid">' + s.items.map((it) => cardHtml(it.p, { i: i++, reason: it.reason, tags: it.tags, compact: true, qty: it.qty })).join('') + '</div>' +
        '</section>').join('') +
    '</div></div>';
  }
  function setForChat() {
    const m = setModel();
    return {
      theme: agent.title, message: agent.message, isRoleSet: m.isRoleSet,
      sections: m.sections.map((s) => ({ label: s.label, products: s.items.map((it) => it.p), tags: s.items.map((it) => it.tags), quantities: s.items.map((it) => it.qty) })),
    };
  }
  document.addEventListener('click', (e) => {
    const b = e.target.closest('#cr-set-add');
    if (!b || !agent.sections || !window.ChatCards) return;
    const n = window.ChatCards.addSetToCart(setForChat());
    b.textContent = window.CRChat && !window.CRChat.mock ? '✓ Sent to the AI Concierge' : '✓ Added ' + n + (n === 1 ? ' item' : ' items') + ' to cart';
    setTimeout(() => { b.textContent = setModel().isRoleSet ? 'Add whole set to cart' : 'Add all to cart'; }, 2200);
  });

  async function showProducts(args) {
    const sections = normalizeSections(args.sections);
    let ids = (args.product_ids || []).map(String).filter(Boolean);
    let reasons = args.reasons || [];
    if (sections.length) {
      // product_ids/reasons are the flattened section items; rebuild them from sections when absent or inconsistent
      const flat = sections.flatMap((s) => s.product_ids);
      if (!ids.length || flat.some((id) => !ids.includes(id))) { ids = flat; reasons = sections.flatMap((s) => s.product_ids.map((_, i) => s.reasons[i] || '')); }
    }
    ids = Array.from(new Set(ids)).slice(0, 48);
    const products = await CR.fetchProducts(ids);
    agent.products = products;
    agent.message = args.message || '';
    agent.title = args.theme || args.title || (sections.length ? "Tonight's dinner set" : 'AI Concierge picks');
    agent.sections = sections;
    agent.reasons = {};
    (reasons || []).forEach((r, i) => {
      if (typeof r === 'string' && ids[i]) agent.reasons[ids[i]] = r;
      else if (r && (r.product_id || r.productId)) agent.reasons[String(r.product_id || r.productId)] = String(r.reason || '');
    });
    const cur = window.Router.current();
    window.Router.go({ view: 'agent' }, { replace: cur.view === 'agent' });
    if (sections.length && window.ChatCards) window.ChatCards.showSet(setForChat());
    return { shown: products.length, missing_ids: ids.filter((i) => !products.some((p) => p.id === i)) };
  }
  function renderCompare() {
    const v = CR.view();
    if (!compare.products.length) { history.replaceState({}, '', '/'); window.Home.render(); return; }
    const ps = compare.products;
    const baseRows = [
      { label: 'Price (tax incl.)', key: /price/i, values: ps.map((p) => yen(p.price) + (p.on_sale ? ' (On sale)' : '')) },
      { label: 'Rating', key: /rating|review/i, values: ps.map((p) => '★' + p.rating + ' (' + Number(p.rating_count || 0).toLocaleString('en-US') + (Number(p.rating_count) === 1 ? ' review)' : ' reviews)')) },
    ];
    const rows = baseRows.filter((b) => !(compare.rows || []).some((r) => b.key.test(String(r.label)))).concat(compare.rows || []);
    v.innerHTML = '<div class="cr-container" id="cr-compare-view">' + agentHead(compare.title || 'Product comparison', '') +
      '<div class="cr-compare"><table><thead><tr><th></th>' + ps.map((p) =>
        '<th><div class="cr-compare__prod"><img src="' + esc(p.image) + '" alt=""><b>' + esc(p.title) + '</b>' +
        '<button type="button" class="cr-btn cr-btn--tonal cr-btn--sm" data-add="' + esc(p.id) + '">Add to cart</button></div></th>').join('') + '</tr></thead>' +
      '<tbody>' + rows.map((r) => '<tr><th>' + esc(r.label) + '</th>' + ps.map((_, i) => '<td>' + esc((r.values || [])[i] || '-') + '</td>').join('') + '</tr>').join('') + '</tbody></table></div></div>';
  }
  async function showComparison(args) {
    const ids = (args.product_ids || []).map(String).filter(Boolean).slice(0, 5);
    compare.products = await CR.fetchProducts(ids);
    compare.title = args.title || 'Product comparison';
    compare.rows = Array.isArray(args.rows) ? args.rows.filter((r) => r && r.label) : [];
    const cur = window.Router.current();
    window.Router.go({ view: 'compare' }, { replace: cur.view === 'compare' });
    return { shown: compare.products.length > 0 };
  }

  const hasAgentView = (view) => (view === 'compare' ? compare.products.length > 0 : agent.products.length > 0);
  window.Search = { run, newSearch, state, renderAgent, renderCompare, showProducts, showComparison, hasAgentView };
})();
