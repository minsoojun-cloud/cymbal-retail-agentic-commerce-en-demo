// (2) Products
import { api } from '../api.js?v=20261111q';
import { esc, fmtInt, fmtJpy, chip, fillBars, skeleton, emptyState, errorState, state } from '../ui.js?v=20261111q';

const filters = { group: '', status: '', q: '' };
const STATUS_OPTS = [['', 'All'], ['completed', '🟢 Completed'], ['needs_review', '🟡 Needs review'], ['not_processed', '⚪ Not processed']];

export async function render(root, _params, stale) {
  const groups = (state.config && state.config.groups) || {};
  root.innerHTML = `
    <div class="page-head"><div><h1>Products</h1><p class="sub">Select a product to compare the source catalog record side by side with its enriched golden record</p></div>
      <div class="search"><span class="search-ic">🔍</span><input id="p-q" type="search" placeholder="Search by product name, brand, or SKU" value="${esc(filters.q)}"></div></div>
    <div class="filters">
      <div class="chip-group"><span class="fg-lab">Category</span>
        ${[['', 'All'], ...Object.entries(groups)].map(([k, v]) => `<button class="fchip ${filters.group === k ? 'on' : ''}" data-f="group" data-v="${esc(k)}">${esc(v)}</button>`).join('')}
      </div>
      <div class="chip-group"><span class="fg-lab">Status</span>
        ${STATUS_OPTS.map(([k, v]) => `<button class="fchip ${filters.status === k ? 'on' : ''}" data-f="status" data-v="${esc(k)}">${esc(v)}</button>`).join('')}
      </div>
      <div class="p-count" id="p-count"></div>
    </div>
    <div class="pgrid" id="pgrid">${skeleton(8, 'skel-card')}</div>`;

  let seq = 0;
  const load = async () => {
    const my = ++seq;
    const grid = root.querySelector('#pgrid');
    grid.innerHTML = skeleton(8, 'skel-card');
    let d;
    try { d = await api.products({ group: filters.group, status: filters.status, q: filters.q }); } catch (e) {
      if (stale() || my !== seq) return;
      grid.innerHTML = errorState(e, 'p-retry');
      grid.querySelector('#p-retry').onclick = load;
      return;
    }
    if (stale() || my !== seq) return;
    const items = d.items || [];
    if (!filters.group && !filters.status && !filters.q) state.products = items;
    const total = d.total ?? items.length;
    root.querySelector('#p-count').innerHTML = `<b>${fmtInt(total)}</b> ${Number(total) === 1 ? 'product' : 'products'}`;
    if (!items.length) { grid.innerHTML = emptyState('📦', 'No matching products', 'Try changing your filters'); return; }
    grid.innerHTML = items.map(card).join('');
  };

  root.querySelectorAll('.fchip').forEach((b) => b.addEventListener('click', () => {
    filters[b.dataset.f] = b.dataset.v;
    root.querySelectorAll(`.fchip[data-f="${b.dataset.f}"]`).forEach((x) => x.classList.toggle('on', x === b));
    load();
  }));
  let t;
  root.querySelector('#p-q').addEventListener('input', (e) => {
    clearTimeout(t);
    t = setTimeout(() => { filters.q = e.target.value.trim(); load(); }, 300);
  });
  await load();
  return () => clearTimeout(t);
}

function card(p) {
  const tags = (p.tags || []).slice(0, 4);
  const more = (p.tags || []).length - tags.length;
  return `<a class="pcard" href="#/studio/${encodeURIComponent(p.sku)}">
    <div class="pcard-img"><img loading="lazy" src="${esc(p.image_url)}" alt="" onerror="this.style.visibility='hidden'"><span class="pcard-status">${chip(p.status)}</span>${p.image_count > 0 ? `<span class="pcard-imgcnt" title="Saved images (including generated images)">🖼 ${fmtInt(p.image_count)}</span>` : ''}</div>
    <div class="pcard-body">
      <div class="pcard-meta"><span class="cat-pill">${esc(p.group_label)}</span><span class="muted">${esc(p.brand)}</span></div>
      <div class="pcard-title" title="${esc(p.title)}">${esc(p.title)}</div>
      <div class="pcard-price">${p.price ? fmtJpy(p.price) : ''}<span class="muted small"> SKU ${esc(p.sku)}</span></div>
      ${fillBars(p.fill_before, p.fill_after)}
      <div class="pcard-counts">
        <span class="cnt ok" title="Auto-approved">🟢 ${fmtInt(p.auto_approved)}</span>
        <span class="cnt warn" title="Needs review">🟡 ${fmtInt(p.needs_review)}</span>
        <span class="cnt bad" title="Rejected">🔴 ${fmtInt(p.rejected)}</span>
        <span class="muted small">/ ${fmtInt(p.attr_count)} attributes</span>
      </div>
      <div class="tags">${tags.map((x) => `<span class="tag">${esc(x)}</span>`).join('')}${more > 0 ? `<span class="tag tag-more">+${more}</span>` : ''}</div>
    </div>
  </a>`;
}
