(() => {
  "use strict";
  const endpoint = "https://vdceayvfgkwpgwixrhwi.supabase.co/functions/v1/everything-hub-catalog-search";
  const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
  const safeUrl = value => { try { const u = new URL(String(value || "")); return /^https?:$/.test(u.protocol) ? u.href : ""; } catch { return ""; } };
  const style = document.createElement("style");
  style.textContent = ".eh-record-catalog-actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}.eh-open-record-catalog,.eh-record-catalog-more{border:1px solid #39ff9a;border-radius:999px;background:#063b2e;color:#eafff5;padding:8px 12px;font:600 12px inherit;cursor:pointer}.eh-open-record-catalog:disabled,.eh-record-catalog-more:disabled{opacity:.55;cursor:wait}.eh-record-catalog-panel{margin-top:12px;padding:14px;border:1px solid #39ff9a66;border-radius:12px;background:#041a20;color:#eafff5}.eh-record-detail h3{margin:0 0 8px}.eh-record-detail h4{margin:16px 0 8px}.eh-record-detail ul{padding-left:20px}.eh-record-detail li{margin:10px 0}.eh-record-detail li .eh-open-record-catalog{margin-left:8px}.eh-record-detail a{color:#aaff75}.eh-record-status{font-size:12px;opacity:.8}";
  document.head.append(style);
  const nestedButton = record => {
    const ids = Array.isArray(record.shared_catalog_ids) ? record.shared_catalog_ids.slice(0,3) : [];
    if (!record.record_catalog_id || !ids.length) return "";
    return '<button type="button" class="eh-open-record-catalog" data-record-catalog-id="' +
      encodeURIComponent(record.record_catalog_id) + '" data-shared-catalog-ids="' +
      encodeURIComponent(ids.join(",")) + '">Open ' + esc(record.title || "record") + ' catalog</button>';
  };
  const rowHtml = row => '<li><strong>' + esc(row.title || "Untitled record") +
    '</strong> <span>' + esc(row.record_type || "record") + '</span> ' + nestedButton(row) + '</li>';
  const render = (owner, rows, count) => {
    const sources = (owner.source_urls || []).map(safeUrl).filter(Boolean);
    return '<section class="eh-record-detail"><h3>' + esc(owner.title || "Record catalog") +
      '</h3><p>' + esc(owner.description || "Record detail from the indexed catalog snapshot.") +
      '</p><p class="eh-record-status">Candidate preview only · rights not reviewed · not published</p>' +
      (sources.length ? '<p>Sources: ' + sources.map((u,i) => '<a href="' + esc(u) +
      '" target="_blank" rel="noopener noreferrer">Source ' + (i+1) + '</a>').join(" · ") + '</p>' : '') +
      '<h4>Related catalog entries (' + Number(count || 0).toLocaleString() + ' total)</h4><ul class="eh-related-records">' +
      ((rows || []).map(rowHtml).join("") || '<li>No related entries in the indexed shared catalogs.</li>') +
      '</ul></section>';
  };
  async function requestCatalog(id, ids, offset) {
    const url = new URL(endpoint);
    url.searchParams.set("record_catalog_id", id);
    url.searchParams.set("catalog_ids", ids);
    url.searchParams.set("limit", "20");
    url.searchParams.set("offset", String(offset));
    const headers = { accept: "application/json" };
    if (typeof EH_PUBLIC_KEY !== "undefined" && EH_PUBLIC_KEY) headers.apikey = EH_PUBLIC_KEY;
    const response = await fetch(url, { headers, signal: AbortSignal.timeout(20000) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || ("HTTP " + response.status));
    return data;
  }
  document.addEventListener("click", async event => {
    const more = event.target.closest(".eh-record-catalog-more");
    const button = event.target.closest(".eh-open-record-catalog");
    if (!more && !button) return;
    event.preventDefault();
    const card = event.target.closest(".result");
    const panel = more ? more.closest(".eh-record-catalog-panel") : card?.querySelector(".eh-record-catalog-panel");
    if (!panel) return;
    const control = more || button;
    if (control.dataset.loading === "true") return;
    control.dataset.loading = "true"; control.disabled = true;
    const label = control.textContent;
    control.textContent = more ? "Loading…" : "Loading catalog…";
    if (!more) { panel.hidden = false; panel.textContent = "Loading related catalog entries…"; }
    try {
      const id = more ? panel.dataset.recordCatalogId : decodeURIComponent(button.dataset.recordCatalogId || "");
      const ids = more ? panel.dataset.sharedCatalogIds : decodeURIComponent(button.dataset.sharedCatalogIds || "");
      const offset = more ? Number(more.dataset.offset || 0) : 0;
      if (!id || !ids || !Number.isInteger(offset) || offset < 0) throw new Error("Missing catalog identity or membership.");
      const data = await requestCatalog(id, ids, offset);
      if (!data.owner_record) throw new Error("Record catalog not found in the current snapshot.");
      if (!more) {
        panel.dataset.recordCatalogId = id;
        panel.dataset.sharedCatalogIds = ids;
        panel.innerHTML = render(data.owner_record, data.related_records, data.related_count);
      } else {
        const list = panel.querySelector(".eh-related-records");
        (data.related_records || []).forEach(row => list.insertAdjacentHTML("beforeend", rowHtml(row)));
        more.remove();
      }
      if (data.has_more) {
        const next = document.createElement("button");
        next.type = "button";
        next.className = "eh-record-catalog-more";
        next.dataset.offset = String(offset + Number(data.limit || 20));
        next.textContent = "Load 20 more related entries";
        panel.querySelector(".eh-record-detail").append(next);
      }
    } catch (error) {
      const message = "Catalog preview unavailable: " + (error?.message || "request failed");
      if (more) { more.textContent = "Retry loading more"; more.disabled = false; }
      else panel.textContent = message;
    } finally {
      control.dataset.loading = "false";
      if (control.isConnected && !more) { control.disabled = false; control.textContent = label; }
      else if (control.isConnected && more && control.textContent === "Loading…") { control.disabled = false; control.textContent = label; }
    }
  });
})();