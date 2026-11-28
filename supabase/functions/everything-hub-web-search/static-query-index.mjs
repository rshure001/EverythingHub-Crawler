import { searchStaticPages } from "./static-search.mjs";
import { isValidStaticManifest } from "./static-manifest.mjs";

const REPOSITORY = "rshure001/EverythingHub-Crawler";
const REF_URL = "https://api.github.com/repos/" + REPOSITORY + "/git/ref/heads/main";
const RAW_BASE = "https://raw.githubusercontent.com/" + REPOSITORY + "/";
const CACHE_TTL_MS = 300_000;
const MAX_MANIFEST_BYTES = 256_000;
const MAX_QUERY_TERMS = 6;
const MAX_QUERY_INDEX_SHARDS = 256;
const MAX_QUERY_INDEX_SHARD_COMPRESSED_BYTES = 2_000_000;
const MAX_QUERY_INDEX_SHARD_UNCOMPRESSED_BYTES = 8_000_000;
const MAX_QUERY_INDEX_TOTAL_COMPRESSED_BYTES = 6_000_000;
const MAX_QUERY_INDEX_TOTAL_UNCOMPRESSED_BYTES = 16_000_000;
const MAX_QUERY_INDEX_TOKENS = 4_000_000;
const MAX_QUERY_INDEX_POSTINGS = 100_000_000;
const MAX_PAGE_SHARD_BYTES = 350_000;
const MAX_PAGE_SHARD_UNCOMPRESSED_BYTES = 600_000;
const MAX_QUERY_SCOPED_SHARD_COUNT = 24;
const MAX_QUERY_SCOPED_SHARD_BYTES = 8_000_000;
const MAX_QUERY_SCOPED_SHARD_UNCOMPRESSED_BYTES = 24_000_000;
const MAX_QUERY_SCOPED_PAGE_COUNT = 12_000;
const MAX_FETCH_CONCURRENCY = 8;
const QUERY_INDEX_STOP_TOKENS = new Set([
  "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
  "in", "into", "is", "it", "of", "on", "or", "our", "the", "to",
  "with", "your", "you", "we", "this", "that", "these", "those",
]);

const encoder = new TextEncoder();
const defaultCache = {
  manifest: null,
  queryIndexShards: new Map(),
  pageShards: new Map(),
  queryResults: new Map(),
};

function normalizeQueryIndexTokens(value) {
  const normalized = String(value || "").normalize("NFKC").toLowerCase();
  const raw = normalized.match(/[\p{L}\p{N}]+/gu) || [];
  const seen = new Set();
  const tokens = [];
  for (const token of raw) {
    if (!token || QUERY_INDEX_STOP_TOKENS.has(token) || seen.has(token)) continue;
    seen.add(token);
    tokens.push(token);
  }
  return tokens;
}

async function sha256Hex(bytes) {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, "0")).join("");
}

async function fetchBytes(url, maxBytes, timeoutMs, fetchImpl) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetchImpl(url, {
      headers: { Accept: "*/*", "User-Agent": "EverythingHub-StaticSearch/1.0" },
      signal: controller.signal,
    });
    if (!response.ok) throw new Error("upstream_http_" + response.status);
    const declared = Number(response.headers.get("content-length") || 0);
    if (Number.isFinite(declared) && declared > maxBytes) throw new Error("upstream_size_limit");
    const bytes = new Uint8Array(await response.arrayBuffer());
    if (bytes.byteLength > maxBytes) throw new Error("upstream_size_limit");
    return bytes;
  } finally {
    clearTimeout(timer);
  }
}

async function decodeGzipJson(bytes, maxBytes) {
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
  const decoded = new Uint8Array(await new Response(stream).arrayBuffer());
  if (decoded.byteLength > maxBytes) throw new Error("gzip_uncompressed_size_limit");
  let payload;
  try {
    payload = JSON.parse(new TextDecoder().decode(decoded));
  } catch {
    throw new Error("gzip_json_invalid");
  }
  return { payload, decodedBytes: decoded.byteLength };
}

async function mapLimit(items, limit, mapper) {
  const results = new Array(items.length);
  let nextIndex = 0;
  let failure = null;
  const workers = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (!failure) {
      const index = nextIndex++;
      if (index >= items.length) return;
      try {
        results[index] = await mapper(items[index], index);
      } catch (error) {
        failure = error;
      }
    }
  });
  await Promise.all(workers);
  if (failure) throw failure;
  return results;
}

function validateQueryIndexMetadata(manifest) {
  const index = manifest && manifest.query_index;
  if (!index || index.version !== 1 || !Array.isArray(index.shards) ||
      !Number.isSafeInteger(index.shard_count) || index.shard_count < 1 ||
      index.shard_count > MAX_QUERY_INDEX_SHARDS || index.shards.length !== index.shard_count ||
      !Number.isSafeInteger(index.token_count) || index.token_count < 1 ||
      index.token_count > MAX_QUERY_INDEX_TOKENS ||
      !Number.isSafeInteger(index.posting_count) || index.posting_count < index.token_count ||
      index.posting_count > MAX_QUERY_INDEX_POSTINGS ||
      !Number.isSafeInteger(index.compressed_bytes) || index.compressed_bytes < 1 ||
      index.compressed_bytes > 32_000_000 ||
      !Number.isSafeInteger(index.uncompressed_bytes) || index.uncompressed_bytes < 1 ||
      index.uncompressed_bytes > 64_000_000) {
    throw new Error("query_index_manifest_invalid");
  }

  const entries = new Map();
  let compressedBytes = 0;
  let uncompressedBytes = 0;
  let tokenCount = 0;
  for (const entry of index.shards) {
    const match = entry && typeof entry.file === "string"
      ? /^query-index\/([a-f0-9]{2})\.json\.gz$/i.exec(entry.file)
      : null;
    if (!match || entries.has(match[1].toLowerCase()) ||
        !Number.isSafeInteger(entry.bytes) || entry.bytes < 1 ||
        entry.bytes > MAX_QUERY_INDEX_SHARD_COMPRESSED_BYTES ||
        !Number.isSafeInteger(entry.uncompressed_bytes) || entry.uncompressed_bytes < 1 ||
        entry.uncompressed_bytes > MAX_QUERY_INDEX_SHARD_UNCOMPRESSED_BYTES ||
        !Number.isSafeInteger(entry.items) || entry.items < 1 ||
        entry.items > MAX_QUERY_INDEX_TOKENS ||
        typeof entry.sha256 !== "string" || !/^[a-f0-9]{64}$/i.test(entry.sha256)) {
      throw new Error("query_index_file_entry_invalid");
    }
    compressedBytes += entry.bytes;
    uncompressedBytes += entry.uncompressed_bytes;
    tokenCount += entry.items;
    entries.set(match[1].toLowerCase(), entry);
  }
  if (compressedBytes !== index.compressed_bytes ||
      uncompressedBytes !== index.uncompressed_bytes ||
      tokenCount !== index.token_count) {
    throw new Error("query_index_manifest_totals_invalid");
  }
  return entries;
}

async function loadManifest(fetchImpl, cache, refUrl, rawBase, now) {
  if (cache.manifest && now - cache.manifest.loadedAt < CACHE_TTL_MS) return cache.manifest;
  const refBytes = await fetchBytes(refUrl, 4096, 6000, fetchImpl);
  let ref;
  try {
    ref = JSON.parse(new TextDecoder().decode(refBytes));
  } catch {
    throw new Error("catalog_git_ref_invalid");
  }
  const commitSha = ref && ref.object && ref.object.sha;
  if (typeof commitSha !== "string" || !/^[a-f0-9]{40}$/i.test(commitSha)) {
    throw new Error("catalog_git_ref_invalid");
  }

  const manifestRaw = await fetchBytes(
    rawBase + commitSha + "/public-search/manifest.json",
    MAX_MANIFEST_BYTES,
    7000,
    fetchImpl,
  );
  let manifest;
  try {
    manifest = JSON.parse(new TextDecoder().decode(manifestRaw));
  } catch {
    throw new Error("manifest_json_invalid");
  }
  if (!isValidStaticManifest(manifest)) throw new Error("manifest_schema_invalid");
  const queryIndexEntries = validateQueryIndexMetadata(manifest);
  const loaded = {
    commitSha,
    manifest,
    manifestSha256: await sha256Hex(manifestRaw),
    queryIndexEntries,
    loadedAt: now,
  };
  cache.manifest = loaded;
  return loaded;
}

async function loadQueryIndexShard(prefix, entry, commitSha, fetchImpl, cache, rawBase, now) {
  const key = commitSha + ":" + prefix;
  const cached = cache.queryIndexShards.get(key);
  if (cached && now - cached.loadedAt < CACHE_TTL_MS) return cached.tokens;

  const path = entry.file.split("/").map(encodeURIComponent).join("/");
  const raw = await fetchBytes(
    rawBase + commitSha + "/public-search/" + path,
    Math.min(MAX_QUERY_INDEX_SHARD_COMPRESSED_BYTES, entry.bytes),
    7000,
    fetchImpl,
  );
  if (raw.byteLength !== entry.bytes || await sha256Hex(raw) !== entry.sha256.toLowerCase()) {
    throw new Error("query_index_integrity_mismatch");
  }
  const decoded = await decodeGzipJson(raw, MAX_QUERY_INDEX_SHARD_UNCOMPRESSED_BYTES);
  if (decoded.decodedBytes !== entry.uncompressed_bytes) throw new Error("query_index_uncompressed_size_mismatch");
  const payload = decoded.payload;
  if (!payload || payload.version !== 1 || payload.prefix !== prefix ||
      !payload.tokens || typeof payload.tokens !== "object" || Array.isArray(payload.tokens) ||
      Object.keys(payload.tokens).length !== entry.items) {
    throw new Error("query_index_schema_invalid");
  }

  for (const [token, postings] of Object.entries(payload.tokens)) {
    const normalized = normalizeQueryIndexTokens(token);
    if (normalized.length !== 1 || normalized[0] !== token ||
        !Array.isArray(postings) || postings.length > cache.manifest.manifest.shard_count) {
      throw new Error("query_index_token_invalid");
    }
    let priorIndex = -1;
    for (const pair of postings) {
      if (!Array.isArray(pair) || pair.length !== 2 ||
          !Number.isSafeInteger(pair[0]) || pair[0] < 0 ||
          pair[0] >= cache.manifest.manifest.shard_count || pair[0] <= priorIndex ||
          !Number.isSafeInteger(pair[1]) || pair[1] < 1 ||
          pair[1] > cache.manifest.manifest.shards[pair[0]].pages) {
        throw new Error("query_index_posting_invalid");
      }
      priorIndex = pair[0];
    }
  }

  cache.queryIndexShards.set(key, { tokens: payload.tokens, loadedAt: now });
  while (cache.queryIndexShards.size > 64) {
    cache.queryIndexShards.delete(cache.queryIndexShards.keys().next().value);
  }
  return payload.tokens;
}

async function loadPageShard(index, meta, commitSha, fetchImpl, cache, rawBase, now) {
  const key = commitSha + ":" + index;
  const cached = cache.pageShards.get(key);
  if (cached && now - cached.loadedAt < CACHE_TTL_MS) return cached.pages;

  if (!meta || typeof meta.file !== "string" || !/^shard-\d{4}\.json\.gz$/.test(meta.file) ||
      !Number.isSafeInteger(meta.bytes) || meta.bytes < 1 || meta.bytes > MAX_PAGE_SHARD_BYTES ||
      !Number.isSafeInteger(meta.uncompressed_bytes) || meta.uncompressed_bytes < 1 ||
      meta.uncompressed_bytes > MAX_PAGE_SHARD_UNCOMPRESSED_BYTES ||
      !Number.isSafeInteger(meta.pages) || meta.pages < 1 ||
      typeof meta.sha256 !== "string" || !/^[a-f0-9]{64}$/i.test(meta.sha256)) {
    throw new Error("page_shard_metadata_invalid");
  }
  const raw = await fetchBytes(
    rawBase + commitSha + "/public-search/" + encodeURIComponent(meta.file),
    Math.min(MAX_PAGE_SHARD_BYTES, meta.bytes),
    8500,
    fetchImpl,
  );
  if (raw.byteLength !== meta.bytes || await sha256Hex(raw) !== meta.sha256.toLowerCase()) {
    throw new Error("page_shard_integrity_mismatch");
  }
  const decoded = await decodeGzipJson(raw, MAX_PAGE_SHARD_UNCOMPRESSED_BYTES);
  if (decoded.decodedBytes !== meta.uncompressed_bytes) throw new Error("page_shard_uncompressed_size_mismatch");
  const payload = decoded.payload;
  if (!payload || payload.version !== 1 || !Array.isArray(payload.pages) ||
      payload.pages.length !== meta.pages) {
    throw new Error("page_shard_schema_invalid");
  }
  for (const page of payload.pages) {
    if (!page || typeof page.url !== "string" || page.robots_allowed === false ||
        page.robotsAllowed === false) throw new Error("page_shard_record_invalid");
    let parsed;
    try { parsed = new URL(page.url); } catch { throw new Error("page_shard_url_invalid"); }
    if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password) {
      throw new Error("page_shard_url_unsafe");
    }
  }

  cache.pageShards.set(key, { pages: payload.pages, loadedAt: now });
  while (cache.pageShards.size > 64) {
    cache.pageShards.delete(cache.pageShards.keys().next().value);
  }
  return payload.pages;
}

function queryTokensForPage(page) {
  const values = [page && page.title, page && page.url, page && page.description, page && page.text];
  const product = page && page.product;
  if (product && typeof product === "object" && !Array.isArray(product)) {
    for (const key of ["name", "brand", "sku", "price", "availability"]) values.push(product[key]);
  }
  const tokens = new Set();
  for (const value of values) {
    if (typeof value !== "string" && typeof value !== "number") continue;
    for (const token of normalizeQueryIndexTokens(value)) tokens.add(token);
  }
  return tokens;
}

function emptyResult(manifestInfo, terms, offset, limit) {
  return {
    results: [],
    count: 0,
    hasMore: false,
    queryScoped: true,
    indexedPages: manifestInfo.manifest.total_pages,
    updatedAt: manifestInfo.manifest.updated_at,
    source: "git-commit-pinned-query-indexed-public-search-shards",
    candidatePageCount: 0,
    candidateShardCount: 0,
    totalCandidateShardCount: 0,
    candidateTruncated: false,
    queryIndexTokensUsed: terms.length,
    queryIndexPostingCount: 0,
    offset,
    limit,
  };
}

export async function searchPublicCrawlerIndex(query, lane = null, limit = 10, offset = 0, options = {}) {
  const fetchImpl = options.fetchImpl || fetch;
  const cache = options.cache || defaultCache;
  const refUrl = options.refUrl || REF_URL;
  const rawBase = options.rawBase || RAW_BASE;
  const now = Number.isFinite(options.now) ? options.now : Date.now();
  const terms = normalizeQueryIndexTokens(query).slice(0, MAX_QUERY_TERMS);
  const safeLimit = Number.isInteger(limit) ? Math.min(20, Math.max(1, limit)) : 10;
  const safeOffset = Number.isInteger(offset) ? Math.min(5000, Math.max(0, offset)) : 0;
  if (!String(query || "").trim() || !terms.length) {
    return { results: [], count: 0, hasMore: false, queryScoped: true, offset: safeOffset, limit: safeLimit };
  }

  const manifestInfo = await loadManifest(fetchImpl, cache, refUrl, rawBase, now);
  const { manifest, commitSha, queryIndexEntries } = manifestInfo;
  const prefixes = [];
  for (const term of terms) {
    const prefix = (await sha256Hex(encoder.encode(term))).slice(0, 2);
    if (!prefixes.includes(prefix)) prefixes.push(prefix);
  }
  if (prefixes.length > MAX_QUERY_TERMS) throw new Error("query_index_prefix_budget_exceeded");

  const indexEntries = prefixes.map((prefix) => {
    const entry = queryIndexEntries.get(prefix);
    if (!entry) return null;
    return { prefix, entry };
  });
  if (indexEntries.some((item) => !item)) return emptyResult(manifestInfo, terms, safeOffset, safeLimit);
  const totalIndexCompressed = indexEntries.reduce((sum, item) => sum + item.entry.bytes, 0);
  const totalIndexUncompressed = indexEntries.reduce((sum, item) => sum + item.entry.uncompressed_bytes, 0);
  if (totalIndexCompressed > MAX_QUERY_INDEX_TOTAL_COMPRESSED_BYTES ||
      totalIndexUncompressed > MAX_QUERY_INDEX_TOTAL_UNCOMPRESSED_BYTES) {
    throw new Error("query_index_fetch_budget_exceeded");
  }

  const loadedIndexShards = await mapLimit(indexEntries, MAX_FETCH_CONCURRENCY, async ({ prefix, entry }) => ({
    prefix,
    tokens: await loadQueryIndexShard(prefix, entry, commitSha, fetchImpl, cache, rawBase, now),
  }));
  const tokensByPrefix = new Map(loadedIndexShards.map((item) => [item.prefix, item.tokens]));

  let candidateShardIds = null;
  const postingsByTerm = new Map();
  for (const term of terms) {
    const prefix = (await sha256Hex(encoder.encode(term))).slice(0, 2);
    const postings = tokensByPrefix.get(prefix) && tokensByPrefix.get(prefix)[term];
    if (!Array.isArray(postings)) return emptyResult(manifestInfo, terms, safeOffset, safeLimit);
    const counts = new Map(postings.map((pair) => [pair[0], pair[1]]));
    postingsByTerm.set(term, counts);
    const ids = new Set(counts.keys());
    candidateShardIds = candidateShardIds === null
      ? ids
      : new Set(Array.from(candidateShardIds).filter((id) => ids.has(id)));
    if (!candidateShardIds.size) return emptyResult(manifestInfo, terms, safeOffset, safeLimit);
  }

  const termWeights = new Map();
  for (const term of terms) {
    const counts = postingsByTerm.get(term);
    let totalTermCount = 0;
    for (const count of counts.values()) totalTermCount += count;
    termWeights.set(term, {
      counts,
      inverseFrequency: Math.log1p(manifest.total_pages / Math.max(1, totalTermCount)),
    });
  }

  const rankedCandidates = Array.from(candidateShardIds).map((index) => {
    const meta = manifest.shards[index];
    let score = 0;
    for (const { counts, inverseFrequency } of termWeights.values()) {
      score += (counts.get(index) || 0) * inverseFrequency;
    }
    return { index, meta, score };
  }).sort((a, b) => b.score - a.score || a.index - b.index);

  const totalCandidateShardCount = rankedCandidates.length;
  const totalCandidatePageCount = rankedCandidates.reduce((sum, item) => sum + item.meta.pages, 0);
  const selected = [];
  let compressedBytes = 0;
  let uncompressedBytes = 0;
  let candidatePageCount = 0;
  for (const item of rankedCandidates) {
    if (selected.length >= MAX_QUERY_SCOPED_SHARD_COUNT) break;
    const compressed = item.meta.bytes;
    const uncompressed = item.meta.uncompressed_bytes;
    const pages = item.meta.pages;
    if (compressedBytes + compressed > MAX_QUERY_SCOPED_SHARD_BYTES ||
        uncompressedBytes + uncompressed > MAX_QUERY_SCOPED_SHARD_UNCOMPRESSED_BYTES ||
        candidatePageCount + pages > MAX_QUERY_SCOPED_PAGE_COUNT) continue;
    selected.push(item);
    compressedBytes += compressed;
    uncompressedBytes += uncompressed;
    candidatePageCount += pages;
  }
  if (!selected.length) throw new Error("query_index_candidate_budget_exceeded");

  const loadedShards = await mapLimit(
    [...selected].sort((a, b) => a.index - b.index),
    MAX_FETCH_CONCURRENCY,
    async ({ index, meta }) => ({
      index,
      pages: await loadPageShard(index, meta, commitSha, fetchImpl, cache, rawBase, now),
    }),
  );

  const pages = [];
  const seenUrls = new Set();
  let queryIndexPostingCount = 0;
  for (const { index, pages: shardPages } of loadedShards) {
    const actualCounts = new Map(terms.map((term) => [term, 0]));
    for (const page of shardPages) {
      if (seenUrls.has(page.url)) throw new Error("query_index_duplicate_url");
      seenUrls.add(page.url);
      const pageTokens = queryTokensForPage(page);
      for (const term of terms) {
        if (pageTokens.has(term)) actualCounts.set(term, actualCounts.get(term) + 1);
      }
      pages.push(page);
    }
    for (const term of terms) {
      const expected = postingsByTerm.get(term).get(index) || 0;
      const actual = actualCounts.get(term) || 0;
      if (expected !== actual) throw new Error("query_index_posting_mismatch");
      queryIndexPostingCount += expected;
    }
  }
  if (pages.length !== candidatePageCount) throw new Error("query_index_candidate_page_count_mismatch");

  const searched = searchStaticPages(pages, query, lane, safeLimit, safeOffset, now);
  return {
    ...searched,
    queryScoped: true,
    indexedPages: manifest.total_pages,
    updatedAt: manifest.updated_at,
    manifestSha256: manifestInfo.manifestSha256,
    commitSha,
    source: "Everything Hub public crawler index",
    transport: "query-indexed-shards",
    scannedPages: pages.length,
    candidatePageCount,
    candidateShardCount: selected.length,
    totalCandidateShardCount,
    totalCandidatePageCount,
    candidateTruncated: selected.length < totalCandidateShardCount,
    candidateSelection: "ranked-query-posting-frequency",
    queryIndexTokensUsed: terms.length,
    queryIndexPostingCount,
    offset: safeOffset,
    limit: safeLimit,
    hasMore: Boolean(searched.hasMore || selected.length < totalCandidateShardCount),
  };
}

export function createStaticSearchCache() {
  return {
    manifest: null,
    queryIndexShards: new Map(),
    pageShards: new Map(),
    queryResults: new Map(),
  };
}
