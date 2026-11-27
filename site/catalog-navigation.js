(() => {
  "use strict";
  const endpoint = "https://vdceayvfgkwpgwixrhwi.supabase.co/functions/v1/everything-hub-web-search";
  const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
  const safeUrl = value => { try { const u = new URL(String(value || "")); return /^https?:$/.test(u.protocol) ? u.href : ""; } catch { return ""; } };
  const nestedButton = record => {
    const ids = Array.isArray(record.shared_catalog_ids) ? record.shared_catalog_ids.slice(0,3) : [];
    if (!record.record_catalog_id || !ids.length) return "";
    return '<button type="button" class="eh-open-record-catalog" data-record-catalog-id="' +
      encodeURIComponent(record.record_catalog_id) + '" data-shared-catalog-ids="' +
      encodeURIComponent(ids.join(",")) + '">Open ' + esc(record.title || "record") + ' catalog</button>';
  };
  const render = (owner, rows, count) => {
    const sources = (owner.source_urls || []).map(safeUrl).filter(Boolean);
    const items = (rows || []).map(row => '<li><strong>' + esc(row.title || "Untitled record") +
      '</strong> <span>' + esc(row.record_type || "record") + '</span> ' + nestedButton(row) + '</li>').join("");
    return '<section class="eh-record-detail"><h3>' + esc(owner.title || "Record catalog") +
      '</h3><p>' + esc(owner.description || "Record detail from the indexed catalog snapshot.") +
      '</p><p class="eh-record-status">Candidate preview only · rights not reviewed · not published</p>' +
      (sources.length ? '<p>Sources: ' + sources.map((u,i) => '<a href="' + esc(u) +
      '" target="_blank" rel="noopener noreferrer">Source ' + (i+1) + '</a>').join(" · ") + '</p>' : '') +
      '<h4>Related catalog entries (' + Number(count || 0).toLocaleString() + ' total)</h4><ul>' +
      (items || '<li>No related entries in the indexed shared catalogs.</li>') + '</ul></section>';
  };
  document.addEventListener("click", async event => {
    const button = event.target.closest(".eh-open-record-catalog");
    if (!button || button.dataset.loading === "true") return;
    const panel = button.closest(".result")?.querySelector(".eh-record-catalog-panel");
    if (!panel) return;
    event.preventDefault();
    button.dataset.loading = "true"; button.disabled = true;
    const label = button.textContent; button.textContent = "Loading catalog…";
    panel.hidden = false; panel.textContent = "Loading related catalog entries…";
    try {
      const id = decodeURIComponent(button.dataset.recordCatalogId || "");
      const ids = decodeURIComponent(button.dataset.sharedCatalogIds || "");
      if (!id || !ids) throw new Error("Missing catalog identity or membership.");
      const url = new URL(endpoint);
      url.searchParams.set("record_catalog_id", id);
      url.searchParams.set("catalog_ids", ids);
      url.searchParams.set("limit", "20");
      url.searchParams.set("offset", "0");
      const headers = { accept: "application/json" };
      if (typeof EH_PUBLIC_KEY !== "undefined" && EH_PUBLIC_KEY) headers.apikey = EH_PUBLIC_KEY;
      const response = await fetch(url, { headers, signal: AbortSignal.timeout(20000) });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || ("HTTP " + response.status));
      if (!data.owner_record) throw new Error("Record catalog not found in the current snapshot.");
      panel.innerHTML = render(data.owner_record, data.related_records, data.related_count);
      if (data.has_more) {
        const note = document.createElement("p");
        note.className = "eh-record-status";
        note.textContent = "Showing the first 20 entries. Additional entries are available through pagination.";
        panel.querySelector(".eh-record-detail").append(note);
      }
    } catch (error) {
      panel.textContent = "Catalog preview unavailable: " + (error?.message || "request failed");
    } finally {
      button.dataset.loading = "false"; button.disabled = false; button.textContent = label;
    }
  });
})();