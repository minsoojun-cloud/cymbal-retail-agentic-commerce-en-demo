/* Cymbal Staff — "no shelf number" banner + list (CONTRACT v11/v12).
   The shelf-number editor itself lives in the standalone Shelf Manager screen (/admin/). This module only shows the
   staff the products whose shelf number disappeared and hands them to the normal shelf registration flow (reassign mode). */
(() => {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const U = {
    storeId: "",
    items: [],
    count: 0,
    store() {
      return this.storeId || new URLSearchParams(location.search).get("store") || localStorage.getItem("sn_staff_store") || "S01";
    },
    init() {},
    loadStore(storeId) { this.storeId = storeId; this.refreshUnassigned(); },
    async refreshUnassigned() {
      try {
        const r = await fetch(`/api/staff/unassigned?store_id=${encodeURIComponent(this.store())}`);
        if (!r.ok) return [];
        const data = await r.json();
        this.items = data.items || [];
        this.count = data.count || this.items.length;
        const banner = $("#unassigned-banner");
        if (banner) {
          $("#unassigned-count").textContent = String(this.count);
          banner.hidden = this.count === 0;
        }
        return this.items;
      } catch {
        return [];
      }
    },
    async showUnassignedSheet() {
      await this.refreshUnassigned();
      const sheet = $("#unassigned-sheet");
      const list = $("#unassigned-items-list");
      if (!sheet || !list) return;
      list.innerHTML = this.items.length ? this.items.map((p) => `
        <div class="unassigned-card">
          <img src="${esc(p.thumbnail || "/static/shared/img/favicon.svg")}" alt="" loading="lazy"/>
          <div class="unassigned-card__info">
            <div class="unassigned-card__title">${esc(p.title)}</div>
            <div class="unassigned-card__prev">Previous slot: ${esc((p.previous_slot_ids || []).join(", ") || "—")}</div>
          </div>
          <button type="button" class="btn btn--sm btn--primary btn-reassign" data-pid="${esc(p.product_id)}">Assign shelf</button>
        </div>`).join("") : `<div class="empty"><p>No products without a shelf number</p></div>`;
      list.querySelectorAll(".btn-reassign").forEach((b) => b.addEventListener("click", () => {
        const item = this.items.find((x) => x.product_id === b.dataset.pid);
        sheet.hidden = true;
        $("#scrim").hidden = true;
        if (item && window.startReassignmentFlow) window.startReassignmentFlow(item);
      }));
      sheet.hidden = false;
      $("#scrim").hidden = false;
    },
    /** deep link from the Shelf Manager screen: /?store=S01&reassign=<product_id> */
    async openFromQuery() {
      const pid = new URLSearchParams(location.search).get("reassign");
      if (!pid) return;
      const items = await this.refreshUnassigned();
      const item = items.find((x) => String(x.product_id) === pid);
      if (item && window.startReassignmentFlow) window.startReassignmentFlow(item);
    },
  };
  // keep the v11 global name: staff.js calls window.ShelfAdmin.refreshUnassigned()/loadStore()
  window.ShelfAdmin = U;
  document.addEventListener("click", (e) => { if (e.target.closest("#unassigned-btn")) U.showUnassignedSheet(); });
})();
