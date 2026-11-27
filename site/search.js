/**
 * Everything Hub browser search client.
 *
 * Loads a verified gzip catalog when available, or the SHA-256-verified
 * bounded shards when the publisher omits the optional gzip file. Shard
 * requests are limited to four concurrent fetches by default.
 *
 * Example:
 *   const engine = await EverythingHubSearch.create({
 *     onProgress: progress => console.log(progress)
 *   });
 *   const results = engine.search("Rolex", { limit: 10 });
 */
(function (root) {
  "use strict";

  const DEFAULT_MANIFEST_URL =
    "https://raw.githubusercontent.com/rshure001/EverythingHub-Crawler/main/public-search/manifest.json";
  const DEFAULT_CATALOG_URL =
    "https://raw.githubusercontent.com/rshure001/EverythingHub-Crawler/main/public-search/catalog-v1.json.gz";
  const TOKEN_RE = /[\p{L}\p{N}]+/gu;
  const WEIGHTS = {
    title: 4, url: 2.2, description: 1.7, text: 1,
    productName: 4.5, productBrand: 3.5, productSku: 3.2,
    productPrice: 1.5, productAvailability: 1.2
  };
  const MAX_SHARDS = 900;
  const MAX_PAGES = 250000;
  const MAX_SHARD_BYTES = 350000;
  const MAX_TOTAL_BYTES = 150000000;
  const MAX_TOTAL_COMPRESSED_BYTES = 150000000;
  const MAX_SHARD_UNCOMPRESSED_BYTES = 600000;
  const MAX_TOTAL_UNCOMPRESSED_BYTES = 540000000;
  const MAX_GZIP_BYTES = 16000000;
  const MAX_DECOMPRESSED_BYTES = 64000000;
  const DEFAULT_PARALLEL = 4;
  const MAX_PARALLEL = 6;
  const FETCH_TIMEOUT_MS = 20000;

  function tokenize(value) {
    return String(value || "").normalize("NFKC").toLocaleLowerCase().match(TOKEN_RE) || [];
  }

  function canonicalHttpUrl(value) {
    try {
      const parsed = new URL(String(value || "").trim());
      if (
        (parsed.protocol !== "http:" && parsed.protocol !== "https:") ||
        !parsed.hostname ||
        parsed.username ||
        parsed.password
      ) return null;
      parsed.hash = "";
      return parsed.href;
    } catch {
      return null;
    }
  }

  function rank(pages, query, options) {
    const opts = options || {};
    const terms = [...new Set(tokenize(String(query || "").slice(0, 240)))];
    if (!terms.length || !Array.isArray(pages)) return [];
    const limit = Math.max(1, Math.min(Number(opts.limit) || 20, 50));
    const lane = opts.lane ? String(opts.lane).toLocaleLowerCase() : null;
    const bestByUrl = new Map();

    for (const page of pages) {
      if (!page || typeof page !== "object" || page.robots_allowed === false) continue;
      const url = canonicalHttpUrl(page.url);
      if (!url) continue;
      if (lane && String(page.lane || "").toLocaleLowerCase() !== lane) continue;

      const fields = {};
      const all = [];
      const product = page.product && page.product.type === "product" ? page.product : {};
      const values = {
        title: page.title, url: url, description: page.description, text: page.text,
        productName: product.name, productBrand: product.brand, productSku: product.sku,
        productPrice: product.price, productAvailability: product.availability
      };
      for (const field of Object.keys(WEIGHTS)) {
        fields[field] = tokenize(values[field]);
        all.push(...fields[field]);
      }
      if (!all.length) continue;

      const counts = new Map();
      for (const token of all) counts.set(token, (counts.get(token) || 0) + 1);
      let score = 0;
      for (const term of terms) {
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
      const candidate = {
        url,
        title: String(page.title || url).slice(0, 500),
        description: description.slice(0, 500),
        lane: String(page.lane || ""),
        snippet: makeSnippet(body, new Set(terms)),
        score: Math.round(score * 1e6) / 1e6,
        ...(product.name ? { product: {
          type: "product", name: String(product.name).slice(0, 500),
          ...(product.brand ? { brand: String(product.brand).slice(0, 160) } : {}),
          ...(product.sku ? { sku: String(product.sku).slice(0, 120) } : {}),
          ...(product.price ? { price: String(product.price).slice(0, 64) } : {}),
          ...(product.currency ? { currency: String(product.currency).slice(0, 8) } : {}),
          ...(product.availability ? { availability: String(product.availability).slice(0, 80) } : {}),
          ...(canonicalHttpUrl(product.image) ? { image: canonicalHttpUrl(product.image) } : {}),
          ...(canonicalHttpUrl(product.url) ? { productUrl: canonicalHttpUrl(product.url) } : {})
        } } : {})
      };
      const previous = bestByUrl.get(url);
      if (!previous || candidate.score > previous.score) bestByUrl.set(url, candidate);
    }

    const results = [...bestByUrl.values()];
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

  function requestInit() {
    const init = { mode: "cors", credentials: "omit" };
    if (typeof AbortSignal !== "undefined" && typeof AbortSignal.timeout === "function") {
      init.signal = AbortSignal.timeout(FETCH_TIMEOUT_MS);
    }
    return init;
  }

  function getFetcher(options) {
    if (typeof options.fetch === "function") return options.fetch;
    if (typeof root.fetch === "function") return root.fetch.bind(root);
    throw new Error("Fetch is not available in this browser.");
  }

  async function fetchBytes(url, fetcher) {
    const response = await fetcher(url, requestInit());
    if (!response || !response.ok) {
      throw new Error("Catalog request failed: HTTP " + (response ? response.status : "unknown"));
    }
    return response.arrayBuffer();
  }

  async function sha256Hex(buffer) {
    const cryptoApi = root.crypto;
    if (!cryptoApi || !cryptoApi.subtle || typeof cryptoApi.subtle.digest !== "function") {
      throw new Error("This browser does not support SHA-256 catalog verification.");
    }
    const digest = await cryptoApi.subtle.digest("SHA-256", buffer);
    return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
  }

  function validateManifest(manifest) {
    if (
      !manifest ||
      manifest.version !== 1 ||
      manifest.integrity !== "sha256" ||
      !Number.isSafeInteger(manifest.updated_at) ||
      manifest.updated_at <= 0 ||
      !Number.isSafeInteger(manifest.total_pages) ||
      manifest.total_pages < 1 ||
      manifest.total_pages > MAX_PAGES ||
      !Number.isSafeInteger(manifest.shard_count) ||
      manifest.shard_count < 1 ||
      manifest.shard_count > MAX_SHARDS ||
      !Array.isArray(manifest.shards) ||
      manifest.shards.length !== manifest.shard_count
    ) {
      throw new Error("Unsupported or malformed Everything Hub search manifest.");
    }
    const names = new Set();
    let totalPages = 0;
    let totalBytes = 0;
    let totalUncompressedBytes = 0;
    let shardFormat = null;
    for (const shard of manifest.shards) {
      const compressed = shard && typeof shard.file === "string" && shard.file.endsWith(".json.gz");
      const legacy = shard && typeof shard.file === "string" && /^shard-\d{4}\.json$/.test(shard.file);
      const format = compressed ? "gzip" : "legacy";
      if (
        !shard ||
        (!compressed && !legacy) ||
        names.has(shard.file) ||
        !Number.isSafeInteger(shard.pages) ||
        shard.pages < 1 ||
        !Number.isSafeInteger(shard.bytes) ||
        shard.bytes < 1 ||
        shard.bytes > MAX_SHARD_BYTES ||
        typeof shard.sha256 !== "string" ||
        !/^[a-f0-9]{64}$/i.test(shard.sha256)
      ) {
        throw new Error("Search manifest contains an invalid shard descriptor.");
      }
      if (shardFormat && shardFormat !== format) {
        throw new Error("Search manifest mixes compressed and legacy raw shards.");
      }
      shardFormat = format;
      let uncompressedBytes = shard.uncompressed_bytes;
      if (compressed) {
        if (!Number.isSafeInteger(uncompressedBytes) || uncompressedBytes < 1 ||
            uncompressedBytes > MAX_SHARD_UNCOMPRESSED_BYTES) {
          throw new Error("Search manifest contains an invalid uncompressed shard size.");
        }
      } else {
        if (uncompressedBytes != null && uncompressedBytes !== shard.bytes) {
          throw new Error("Legacy shard uncompressed size does not match its byte count.");
        }
        uncompressedBytes = shard.bytes;
      }
      names.add(shard.file);
      totalPages += shard.pages;
      totalBytes += shard.bytes;
      totalUncompressedBytes += uncompressedBytes;
    }
    if (totalPages !== manifest.total_pages) {
      throw new Error("Search manifest page totals do not match its shard descriptors.");
    }
    if (shardFormat === "gzip" && totalBytes > MAX_TOTAL_COMPRESSED_BYTES) {
      throw new Error("Compressed search shards exceed the 150 MB transfer safety limit.");
    }
    if (shardFormat === "legacy" && totalBytes > MAX_TOTAL_BYTES) {
      throw new Error("Legacy search shards exceed the 150 MB transition safety limit.");
    }
    if (totalUncompressedBytes > MAX_TOTAL_UNCOMPRESSED_BYTES) {
      throw new Error("Search shards exceed the 540 MB decoded safety limit.");
    }
    return { totalBytes, totalUncompressedBytes, shardFormat };
  }

  async function decodeGzip(buffer, maxBytes = MAX_DECOMPRESSED_BYTES) {
    if (typeof DecompressionStream === "undefined") {
      throw new Error("This browser does not support gzip decompression.");
    }
    const stream = new Blob([buffer]).stream().pipeThrough(new DecompressionStream("gzip"));
    const reader = stream.getReader();
    const chunks = [];
    let total = 0;
    while (true) {
      const part = await reader.read();
      if (part.done) break;
      total += part.value.byteLength;
      if (total > maxBytes) {
        await reader.cancel();
        throw new Error("Gzip payload exceeds its decompressed safety limit.");
      }
      chunks.push(part.value);
    }
    const bytes = new Uint8Array(total);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.byteLength;
    }
    return bytes;
  }

  async function loadGzipCatalog(descriptor, manifestUrl, fetcher, expectedPages) {
    if (
      !descriptor ||
      descriptor.file !== "catalog-v1.json.gz" ||
      !Number.isSafeInteger(descriptor.bytes) ||
      descriptor.bytes < 1 ||
      descriptor.bytes > MAX_GZIP_BYTES ||
      descriptor.pages !== expectedPages ||
      typeof descriptor.sha256 !== "string" ||
      !/^[a-f0-9]{64}$/i.test(descriptor.sha256)
    ) {
      throw new Error("Compressed catalog metadata is invalid or exceeds its safety limit.");
    }
    const url = new URL(descriptor.file, manifestUrl).href;
    const bytes = await fetchBytes(url, fetcher);
    if (bytes.byteLength !== descriptor.bytes) {
      throw new Error("Compressed catalog byte length mismatch.");
    }
    if ((await sha256Hex(bytes)).toLowerCase() !== descriptor.sha256.toLowerCase()) {
      throw new Error("Compressed catalog SHA-256 mismatch.");
    }
    const payload = JSON.parse(new TextDecoder().decode(await decodeGzip(bytes)));
    if (
      !payload ||
      payload.version !== 1 ||
      !Array.isArray(payload.pages) ||
      payload.pages.length !== expectedPages
    ) {
      throw new Error("Compressed catalog page count or schema mismatch.");
    }
    const urls = new Set();
    for (const page of payload.pages) {
      const pageUrl = page && canonicalHttpUrl(page.url);
      if (!pageUrl) throw new Error("Compressed catalog contains an invalid page URL.");
      if (page.robots_allowed === false) {
        throw new Error("Compressed catalog contains a robots-denied page.");
      }
      if (urls.has(pageUrl)) {
        throw new Error("Compressed catalog contains a duplicate canonical URL.");
      }
      urls.add(pageUrl);
    }
    return payload.pages;
  }

  async function loadShardCatalog(manifest, manifestUrl, fetcher, options, totalBytes) {
    const shards = manifest.shards;
    const pagesByShard = new Array(shards.length);
    const maxParallel = Math.max(1, Math.min(
      Number.isInteger(options.maxParallel) ? options.maxParallel : DEFAULT_PARALLEL,
      MAX_PARALLEL
    ));
    const onProgress = typeof options.onProgress === "function" ? options.onProgress : null;
    let next = 0;
    let loadedShards = 0;
    let loadedBytes = 0;

    function reportProgress() {
      if (!onProgress) return;
      try {
        onProgress({
          loadedShards,
          totalShards: shards.length,
          loadedBytes,
          totalBytes
        });
      } catch {
        // A UI progress callback must not interrupt catalog validation.
      }
    }

    async function worker() {
      while (true) {
        const index = next++;
        if (index >= shards.length) return;
        const descriptor = shards[index];
        const url = new URL(descriptor.file, manifestUrl).href;
        const bytes = await fetchBytes(url, fetcher);
        if (bytes.byteLength !== descriptor.bytes) {
          throw new Error(descriptor.file + ": byte length mismatch.");
        }
        if ((await sha256Hex(bytes)).toLowerCase() !== descriptor.sha256.toLowerCase()) {
          throw new Error(descriptor.file + ": SHA-256 mismatch.");
        }
        const decoded = descriptor.file.endsWith(".json.gz")
          ? await decodeGzip(bytes, MAX_SHARD_UNCOMPRESSED_BYTES)
          : bytes;
        if (decoded.byteLength !== (descriptor.uncompressed_bytes ?? descriptor.bytes)) {
          throw new Error(descriptor.file + ": uncompressed byte length mismatch.");
        }
        const payload = JSON.parse(new TextDecoder().decode(decoded));
        if (
          !payload ||
          payload.version !== 1 ||
          !Array.isArray(payload.pages) ||
          payload.pages.length !== descriptor.pages
        ) {
          throw new Error(descriptor.file + ": page count or schema mismatch.");
        }
        pagesByShard[index] = payload.pages;
        loadedShards++;
        loadedBytes += bytes.byteLength;
        reportProgress();
      }
    }

    await Promise.all(
      Array.from({ length: Math.min(maxParallel, shards.length) }, () => worker())
    );

    const pages = pagesByShard.flat();
    if (pages.length !== manifest.total_pages || loadedBytes !== totalBytes) {
      throw new Error("Verified search shards do not match the manifest totals.");
    }
    const urls = new Set();
    for (const page of pages) {
      const url = page && canonicalHttpUrl(page.url);
      if (!url) throw new Error("Search catalog contains an invalid page URL.");
      if (page.robots_allowed === false) {
        throw new Error("Search catalog contains a robots-denied page.");
      }
      if (urls.has(url)) throw new Error("Search catalog contains a duplicate canonical URL.");
      urls.add(url);
    }
    return pages;
  }

  async function loadLegacyGzip(url, fetcher) {
    const bytes = await fetchBytes(url, fetcher);
    if (bytes.byteLength > MAX_GZIP_BYTES) {
      throw new Error("Compressed catalog exceeds the 12 MB browser safety limit.");
    }
    const payload = JSON.parse(new TextDecoder().decode(await decodeGzip(bytes)));
    if (!payload || payload.version !== 1 || !Array.isArray(payload.pages)) {
      throw new Error("Unsupported or malformed Everything Hub catalog.");
    }
    return payload.pages;
  }

  const QUERY_STOP_TOKENS = new Set([
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "in", "into", "is", "it", "of", "on", "or", "our", "the", "to",
    "with", "your", "you", "we", "this", "that", "these", "those"
  ]);
  const MAX_QUERY_INDEX_SHARDS = 256;
  const MAX_QUERY_INDEX_SHARD_BYTES = 2000000;
  const MAX_QUERY_INDEX_SHARD_UNCOMPRESSED_BYTES = 8000000;

  async function loadQueryDirectedCatalog(manifest, manifestUrl, fetcher, options) {
    const descriptor = manifest.query_index;
    if (!descriptor || descriptor.version !== 1 ||
        !Number.isSafeInteger(descriptor.shard_count) ||
        descriptor.shard_count < 1 || descriptor.shard_count > MAX_QUERY_INDEX_SHARDS ||
        !Array.isArray(descriptor.shards) || descriptor.shards.length !== descriptor.shard_count) {
      throw new Error("Query-index manifest metadata is invalid.");
    }
    const terms = [...new Set(tokenize(String(options.query || "").slice(0, 240)))]
      .filter(term => !QUERY_STOP_TOKENS.has(term)).slice(0, 12);
    if (!terms.length) throw new Error("Search query has no indexable terms.");

    const prefixByTerm = new Map();
    for (const term of terms) {
      const digest = await root.crypto.subtle.digest("SHA-256", new TextEncoder().encode(term));
      const prefix = Array.from(new Uint8Array(digest)).slice(0, 1)
        .map(value => value.toString(16).padStart(2, "0")).join("");
      prefixByTerm.set(term, prefix);
    }

    const entries = new Map();
    for (const entry of descriptor.shards) {
      if (!entry || typeof entry.file !== "string" ||
          !/^query-index\/[0-9a-f]{2}\.json\.gz$/.test(entry.file) ||
          !Number.isSafeInteger(entry.bytes) || entry.bytes < 2 ||
          entry.bytes > MAX_QUERY_INDEX_SHARD_BYTES ||
          !Number.isSafeInteger(entry.uncompressed_bytes) ||
          entry.uncompressed_bytes < 2 ||
          entry.uncompressed_bytes > MAX_QUERY_INDEX_SHARD_UNCOMPRESSED_BYTES ||
          typeof entry.sha256 !== "string" || !/^[a-f0-9]{64}$/i.test(entry.sha256)) {
        throw new Error("Query-index shard metadata is invalid.");
      }
      const prefix = entry.file.slice("query-index/".length, -".json.gz".length);
      if (entries.has(prefix)) throw new Error("Query-index contains duplicate prefix shards.");
      entries.set(prefix, entry);
    }

    const indexByPrefix = new Map();
    for (const prefix of new Set(prefixByTerm.values())) {
      const entry = entries.get(prefix);
      if (!entry) throw new Error("Query-index prefix shard is missing: " + prefix);
      const bytes = await fetchBytes(new URL(entry.file, manifestUrl).href, fetcher);
      if (bytes.byteLength !== entry.bytes) throw new Error(entry.file + ": byte length mismatch.");
      if ((await sha256Hex(bytes)).toLowerCase() !== entry.sha256.toLowerCase()) {
        throw new Error(entry.file + ": SHA-256 mismatch.");
      }
      const decoded = await decodeGzip(bytes, MAX_QUERY_INDEX_SHARD_UNCOMPRESSED_BYTES);
      if (decoded.byteLength !== entry.uncompressed_bytes) {
        throw new Error(entry.file + ": uncompressed byte length mismatch.");
      }
      const payload = JSON.parse(new TextDecoder().decode(decoded));
      if (!payload || payload.version !== 1 || payload.prefix !== prefix ||
          !payload.tokens || typeof payload.tokens !== "object" || Array.isArray(payload.tokens) ||
          Object.keys(payload.tokens).length !== entry.items) {
        throw new Error(entry.file + ": query-index schema or token count mismatch.");
      }
      indexByPrefix.set(prefix, payload.tokens);
    }

    let candidateShardIds = null;
    for (const term of terms) {
      const tokens = indexByPrefix.get(prefixByTerm.get(term)) || {};
      const postings = tokens[term];
      if (!Array.isArray(postings)) {
        candidateShardIds = new Set();
        break;
      }
      const ids = new Set();
      let previous = -1;
      for (const pair of postings) {
        if (!Array.isArray(pair) || pair.length !== 2 ||
            !Number.isSafeInteger(pair[0]) || pair[0] < 0 ||
            pair[0] >= manifest.shards.length ||
            !Number.isSafeInteger(pair[1]) || pair[1] < 1 ||
            pair[0] <= previous) {
          throw new Error("Query-index postings contain an invalid or unsorted page-shard reference.");
        }
        previous = pair[0];
        ids.add(pair[0]);
      }
      candidateShardIds = candidateShardIds === null
        ? ids : new Set([...candidateShardIds].filter(id => ids.has(id)));
      if (!candidateShardIds.size) break;
    }

    const allCandidateIds = [...(candidateShardIds || new Set())].sort((a, b) => a - b);
    const totalCandidateShardCount = allCandidateIds.length;
    const totalCandidatePageCount = allCandidateIds.reduce((sum, id) => sum + manifest.shards[id].pages, 0);
    const selectedIds = allCandidateIds.slice(0, 48);
    const pages = [];
    const seenUrls = new Set();
    const onProgress = typeof options.onProgress === "function" ? options.onProgress : null;
    let loadedBytes = 0;
    for (let position = 0; position < selectedIds.length; position++) {
      const shardIndex = selectedIds[position];
      const entry = manifest.shards[shardIndex];
      if (!entry || !Number.isSafeInteger(entry.bytes) || entry.bytes < 1 ||
          entry.bytes > MAX_SHARD_BYTES || !/^[a-f0-9]{64}$/i.test(String(entry.sha256 || ""))) {
        throw new Error("Selected page-shard metadata is invalid.");
      }
      const bytes = await fetchBytes(new URL(entry.file, manifestUrl).href, fetcher);
      if (bytes.byteLength !== entry.bytes) throw new Error(entry.file + ": byte length mismatch.");
      if ((await sha256Hex(bytes)).toLowerCase() !== entry.sha256.toLowerCase()) {
        throw new Error(entry.file + ": SHA-256 mismatch.");
      }
      const decoded = await decodeGzip(bytes, MAX_SHARD_UNCOMPRESSED_BYTES);
      if (decoded.byteLength !== (entry.uncompressed_bytes ?? entry.bytes)) {
        throw new Error(entry.file + ": uncompressed byte length mismatch.");
      }
      const payload = JSON.parse(new TextDecoder().decode(decoded));
      if (!payload || payload.version !== 1 || !Array.isArray(payload.pages) ||
          payload.pages.length !== entry.pages) {
        throw new Error(entry.file + ": page count or schema mismatch.");
      }
      for (const page of payload.pages) {
        const url = page && canonicalHttpUrl(page.url);
        if (!url || page.robots_allowed === false || seenUrls.has(url)) {
          throw new Error(entry.file + ": invalid, robots-denied, or duplicate page.");
        }
        seenUrls.add(url);
        pages.push(page);
      }
      loadedBytes += bytes.byteLength;
      if (onProgress) {
        try {
          onProgress({
            loadedShards: position + 1,
            totalShards: selectedIds.length,
            loadedBytes,
            totalBytes: selectedIds.reduce((sum, id) => sum + manifest.shards[id].bytes, 0),
            queryDirected: true
          });
        } catch {
          // Progress callbacks must not interrupt integrity validation.
        }
      }
    }
    return {
      pages,
      selectedShardCount: selectedIds.length,
      candidateShardCount: selectedIds.length,
      totalCandidateShardCount,
      candidatePageCount: pages.length,
      totalCandidatePageCount,
      candidateTruncated: totalCandidateShardCount > selectedIds.length
    };
  }

  async function create(options) {
    const opts = options || {};
    const fetcher = getFetcher(opts);

    // Preserve the original explicit catalogUrl option for existing callers.
    if (opts.catalogUrl) {
      const pages = await loadLegacyGzip(opts.catalogUrl, fetcher);
      return Object.freeze({
        pageCount: pages.length,
        updatedAt: null,
        transport: "gzip",
        search: (query, searchOptions) => rank(pages, query, searchOptions)
      });
    }

    const manifestUrl = opts.manifestUrl || DEFAULT_MANIFEST_URL;
    const manifestResponse = await fetcher(manifestUrl, requestInit());
    if (!manifestResponse || !manifestResponse.ok) {
      throw new Error("Search manifest request failed: HTTP " + (manifestResponse ? manifestResponse.status : "unknown"));
    }
    const manifest = await manifestResponse.json();
    const totals = validateManifest(manifest);
    let pages = null;
    let transport = "shards";
    let selectedShardCount = manifest.shard_count;
    let selected = null;

    if (opts.query && manifest.query_index && !manifest.catalog_gzip) {
      selected = await loadQueryDirectedCatalog(manifest, manifestUrl, fetcher, opts);
      pages = selected.pages;
      selectedShardCount = selected.selectedShardCount;
      transport = "query-shards";
    }

    if (!pages && manifest.catalog_gzip) {
      try {
        pages = await loadGzipCatalog(
          manifest.catalog_gzip,
          manifestUrl,
          fetcher,
          manifest.total_pages
        );
        transport = "gzip";
      } catch (error) {
        if (opts.strictCatalogGzip === true) throw error;
        if (root.console && typeof root.console.warn === "function") {
          root.console.warn("Compressed catalog unavailable; using verified shards:", error.message);
        }
      }
    }

    if (!pages) {
      pages = await loadShardCatalog(manifest, manifestUrl, fetcher, opts, totals.totalBytes);
    }

    return Object.freeze({
      pageCount: transport === "query-shards" ? manifest.total_pages : pages.length,
      candidatePageCount: pages.length,
      selectedShardCount,
      totalCandidateShardCount: transport === "query-shards" ? selected.totalCandidateShardCount : manifest.shard_count,
      totalCandidatePageCount: transport === "query-shards" ? selected.totalCandidatePageCount : manifest.total_pages,
      candidateTruncated: transport === "query-shards" ? selected.candidateTruncated : false,
      updatedAt: manifest.updated_at,
      transport,
      search: (query, searchOptions) => rank(pages, query, searchOptions)
    });
  }

  const api = Object.freeze({ create, search: rank, tokenize });
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.EverythingHubSearch = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
