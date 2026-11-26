// App bootstrap + hash router.
import { isMock } from './api.js?v=20261111q';
import { $$, ICON, stepper, loadConfig, refreshShell, errorState, initModelPicker } from './ui.js?v=20261111q';
import * as dashboard from './views/dashboard.js?v=20261111q';
import * as products from './views/products.js?v=20261111q';
import * as studio from './views/studio.js?v=20261111q';
import * as reviews from './views/reviews.js?v=20261111q';
import * as batch from './views/batch.js?v=20261111q';
import * as csv from './views/csv.js?v=20261111q';
import * as gmc from './views/gmc.js?v=20261111q';
import * as images from './views/images.js?v=20261111q';
import * as docs from './views/docs.js?v=20261111q';

const ROUTES = { dashboard, products, studio, csv, images, docs, gmc, reviews, batch };
let cleanup = null;
let renderSeq = 0;

function parseHash() {
  const h = location.hash.replace(/^#\/?/, '');
  const [name, ...rest] = h.split('/');
  return { name: ROUTES[name] ? name : 'dashboard', params: rest.map(decodeURIComponent) };
}

async function route() {
  const { name, params } = parseHash();
  if (typeof cleanup === 'function') { try { cleanup(); } catch (e) { console.warn(e); } }
  cleanup = null;
  // A finished live run keeps its result on the stepper for a few seconds; restore the global funnel on navigation.
  if (stepper.live === 'ended') stepper.endLive();
  $$('.nav-item').forEach((a) => a.classList.toggle('active', a.dataset.route === name));
  const view = document.getElementById('view');
  view.className = `view view-${name}`;
  view.innerHTML = '';
  view.scrollTop = 0;
  document.querySelector('.main').scrollTop = 0;
  const seq = ++renderSeq;
  try {
    const c = await ROUTES[name].render(view, params, () => seq !== renderSeq);
    if (seq === renderSeq) cleanup = c; else if (typeof c === 'function') c();
  } catch (e) {
    console.error(e);
    if (seq === renderSeq) view.innerHTML = errorState(e);
  }
}

async function boot() {
  $$('[data-icon]').forEach((el) => { el.innerHTML = ICON[el.dataset.icon] || ''; });
  if (isMock) document.getElementById('mock-pill').hidden = false;
  // Keep ?mock=1 on hash links (hash changes keep search, so nothing to do).
  stepper.render();
  await loadConfig();
  initModelPicker();
  refreshShell();
  window.addEventListener('hashchange', route);
  if (!location.hash) location.replace('#/dashboard');
  route();
}

boot();
