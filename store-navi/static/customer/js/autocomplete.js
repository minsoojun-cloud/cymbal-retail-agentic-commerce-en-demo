/* Autocomplete (same behaviour as online-store/static/js/autocomplete.js): debounced /api/autocomplete
   (AICS CompletionService for ≥1 char, local fallback), IME-safe keyboard navigation, recent searches. */
(function () {
  'use strict';
  const { esc, fetchJSON } = window.SN;
  const RECENT_KEY = 'sn_recent_q';
  const ICON_SEARCH = '<svg viewBox="0 0 24 24"><path d="M15.5 14h-.79l-.28-.27A6.47 6.47 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/></svg>';
  const ICON_RECENT = '<svg viewBox="0 0 24 24"><path d="M13 3a9 9 0 0 0-9 9H1l3.89 3.89.07.14L9 12H6c0-3.87 3.13-7 7-7s7 3.13 7 7-3.13 7-7 7c-1.93 0-3.68-.79-4.94-2.06l-1.42 1.42A8.954 8.954 0 0 0 13 21a9 9 0 0 0 0-18zm-1 5v5l4.28 2.54.72-1.21-3.5-2.08V8H12z"/></svg>';

  function recent() { try { return JSON.parse(localStorage.getItem(RECENT_KEY) || '[]'); } catch (e) { return []; } }
  function pushRecent(q) {
    const r = [q].concat(recent().filter((x) => x !== q)).slice(0, 6);
    try { localStorage.setItem(RECENT_KEY, JSON.stringify(r)); } catch (e) { /* ignore */ }
  }
  function boldRest(s, q) {
    const i = s.toLowerCase().indexOf(q.toLowerCase());
    if (!q || i < 0) return '<b>' + esc(s) + '</b>';
    return esc(s.slice(0, i)) + esc(s.slice(i, i + q.length)) + '<b>' + esc(s.slice(i + q.length)) + '</b>';
  }

  function attach(form, input, list, clear, onSubmit) {
    let items = [], active = -1, timer = null, seq = 0;
    function hide() { list.hidden = true; form.classList.remove('has-ac'); active = -1; input.setAttribute('aria-expanded', 'false'); }
    function show(arr, kind, source) {
      items = arr; active = -1;
      if (!arr.length) { hide(); return; }
      const q = input.value.trim();
      list.innerHTML = arr.map((s, i) => '<li role="option" id="ac-' + i + '" data-i="' + i + '">' + (kind === 'recent' ? ICON_RECENT : ICON_SEARCH) +
        '<span>' + (kind === 'recent' ? esc(s) : boldRest(s, q)) + '</span></li>').join('') +
        (kind !== 'recent' ? '<div class="ac__foot">' + (source === 'retail' ? 'AI Commerce Search • Autocomplete' : 'Autocomplete') + '</div>' : '');
      list.hidden = false; form.classList.add('has-ac'); input.setAttribute('aria-expanded', 'true');
    }
    function setActive(i) {
      const lis = list.querySelectorAll('li');
      lis.forEach((li) => li.classList.remove('is-active'));
      active = i;
      if (i >= 0 && lis[i]) { lis[i].classList.add('is-active'); input.value = items[i]; }
    }
    function submit(q) {
      q = (q || '').trim(); hide();
      if (!q) return;
      pushRecent(q); input.blur(); onSubmit(q);
    }
    async function fetchSuggest() {
      const q = input.value.trim();
      const my = ++seq;
      if (!q) { show(recent(), 'recent'); return; }
      try {
        const r = await fetchJSON('/api/autocomplete?q=' + encodeURIComponent(q));
        if (my !== seq || document.activeElement !== input) return;
        show(r.suggestions || [], 'suggest', r.source);
      } catch (e) { hide(); }
    }
    input.addEventListener('input', () => { clear.hidden = !input.value; clearTimeout(timer); timer = setTimeout(fetchSuggest, 150); });
    input.addEventListener('focus', () => { clear.hidden = !input.value; if (!input.value.trim()) show(recent(), 'recent'); else fetchSuggest(); });
    input.addEventListener('keydown', (e) => {
      if (e.isComposing || e.keyCode === 229) return;
      if (list.hidden) return;
      if (e.key === 'ArrowDown') { e.preventDefault(); setActive(Math.min(items.length - 1, active + 1)); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(Math.max(-1, active - 1)); }
      else if (e.key === 'Escape') hide();
    });
    list.addEventListener('mousedown', (e) => {
      const li = e.target.closest('li[data-i]');
      if (!li) return;
      e.preventDefault();
      submit(items[Number(li.dataset.i)]);
    });
    input.addEventListener('blur', () => setTimeout(hide, 150));
    form.addEventListener('submit', (e) => { e.preventDefault(); submit(input.value); });
    clear.addEventListener('click', () => { input.value = ''; clear.hidden = true; input.focus(); });
  }

  window.Autocomplete = { attach, pushRecent };
})();
