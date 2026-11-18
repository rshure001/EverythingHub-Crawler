/**
 * Everything Hub browser search client.
 *
 * Loads the published gzip catalog once, then searches locally. No database,
 * API key, paid search provider, or third-party script is required.
 *
 * Example:
 *   const engine = await EverythingHubSearch.create();
 *   const results = engine.search("Rolex", { limit: 10 });
 */
(function (root) {
  "use strict";

  const DEFAULT_CATALOG_URL =
    "https://raw.githubusercontent.com/rshure001/EverythingHub-Crawler/main/public-search/catalog-v1.json.gz";
  const TOKEN_RE = /[^\W_]+/gu;
  const WEIGHTS = { title: 4, url: 2.2, description: 1.7, text: 1 };

  function tokenize(value) {
    return String(value || "").normalize("NFKC").toLocaleLowerCase().match(TOKEN_RE) || [];
  }

  function decodeGzip(buffer) {
    if (typeof DecompressionStream === "undefined") {
      throw new Error("This browser does not support gzip decompression.");
    }
    const stream = new Blob([buffer]).stream().pipeThrough(new DecompressionStream("gzip"));
    return new Response(stream).text();
  }

  function rank(pages, query, options) {
    const opts = options || {};
    const terms = tokenize(String(query || "").slice(0, 240));
    if (!terms.length) return [];
    const limit = Math.max(1, Math.min(Number(opts.limit) || 20, 50));
    const lane = opts.lane ? String(opts.lane).toLocaleLowerCase() : null;
    const unique = new Set();
    const results = [];

    for (const page of pages) {
      if (!page || typeof page !== "object") continue;
      const url = String(page.url || "").trim();
      if (!/^https?:\/\//i.test(url) || unique.has(url)) continue;
      if (lane && String(page.lane || "").toLocaleLowerCase() !== lane) continue;
      unique.add(url);

      const fields = {};
      const all = [];
      for (const field of Object.keys(WEIGHTS)) {
        fields[field] = tokenize(page[field]);
        all.push(...fields[field]);
      }
      if (!all.length) continue;

      const counts = new Map();
      for (const token of all) counts.set(token, (counts.get(token) || 0) + 1);
      let score = 0;
      for (const term of new Set(terms)) {
        const tf = counts.get(term) || 0;
        if (!tf) continue;
        let weightedTf = 0;
        for (const field of Object.keys(WEIGHTS)) {
          let fieldCount = 0;
          for (const token of fields[field]) if (token === term) fieldCount++;
          weightedTf += WEIGHTS[field] * fieldCount;
        }
        score += (weightedTf / (weightedTf + 1.2)) * (1 + Math.log1p(tf));
      }

      const phrase = terms.join(" ");
      if (fields.title.join(" ").includes(phrase)) score += 5;
      if (fields.url.join(" ").includes(phrase)) score += 2;
      if (score <= 0) continue;

      const description = String(page.description || "");
      const body = description || String(page.text || "");
      const snippet = makeSnippet(body, new Set(terms));
      results.push({
        url,
        title: String(page.title || url).slice(0, 500),
        description: description.slice(0, 500),
        lane: String(page.lane || ""),
        snippet,
        score: Math.round(score * 1e6) / 1e6
      });
    }

    results.sort((a, b) =>
      b.score - a.score ||
      a.title.localeCompare(b.title) ||
      a.url.localeCompare(b.url)
    );
    return results.slice(0, limit);
  }

  function makeSnippet(body, terms) {
    const words = String(body || "").trim().split(/\s+/).filter(Boolean);
    if (!words.length) return "";
    for (let i = 0; i < words.length; i++) {
      if (tokenize(words[i]).some(token => terms.has(token))) {
        const start = Math.max(0, i - 9);
        const excerpt = words.slice(start, i + 24).join(" ").slice(0, 220);
        return (start ? "… " : "") + excerpt;
      }
    }
    return words.join(" ").slice(0, 220);
  }

  async function create(options) {
    const opts = options || {};
    const url = opts.catalogUrl || DEFAULT_CATALOG_URL;
    const response = await fetch(url, { mode: "cors", credentials: "omit" });
    if (!response.ok) throw new Error("Catalog request failed: HTTP " + response.status);
    const text = await decodeGzip(await response.arrayBuffer());
    const payload = JSON.parse(text);
    if (!payload || payload.version !== 1 || !Array.isArray(payload.pages)) {
      throw new Error("Unsupported or malformed Everything Hub catalog.");
    }
    return Object.freeze({
      pageCount: payload.pages.length,
      search: (query, searchOptions) => rank(payload.pages, query, searchOptions)
    });
  }

  const api = Object.freeze({ create, search: rank, tokenize });
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.EverythingHubSearch = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
