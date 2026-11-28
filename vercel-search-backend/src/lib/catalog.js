'use strict';
const https = require('node:https');
const zlib = require('node:zlib');
const crypto = require('node:crypto');

const CATALOG_BASE_URL = 'https://raw.githubusercontent.com/rshure001/EverythingHub-Crawler/';
const GIT_REF_URL = 'https://api.github.com/repos/rshure001/EverythingHub-Crawler/git/ref/heads/main';
const CACHE_TTL_MS = 300000;
const STALE_TTL_MS = 3600000;
const MAX_MANIFEST_BYTES = 256000;
const MAX_CATALOG_GZIP_BYTES = 16000000;
const MAX_CATALOG_JSON_BYTES = 64000000;
const MAX_INDEXED_PAGES = 250000;
const MAX_SHARD_COUNT = 900;
const MAX_PAGES_PER_SHARD = 2000;
const MAX_SHARD_BYTES = 350000;
const MAX_SHARD_UNCOMPRESSED_BYTES = 600000;
const MAX_TOTAL_SHARD_BYTES = 150000000;
const MAX_TOTAL_COMPRESSED_SHARD_BYTES = 150000000;
const MAX_TOTAL_SHARD_UNCOMPRESSED_BYTES = 540000000;
const MAX_SHARD_CONCURRENCY = 48;
const SHARD_FETCH_TIMEOUT_MS = 8500;
const MAX_QUERY_INDEX_SHARDS = 256;
const MAX_QUERY_INDEX_SHARD_COMPRESSED_BYTES = 2000000;
const MAX_QUERY_INDEX_SHARD_UNCOMPRESSED_BYTES = 8000000;
const MAX_QUERY_INDEX_TOTAL_COMPRESSED_BYTES = 32000000;
const MAX_QUERY_INDEX_TOTAL_UNCOMPRESSED_BYTES = 96000000;
const MAX_QUERY_INDEX_TOKENS = 4000000;
const MAX_QUERY_INDEX_POSTINGS = 100000000;
const MAX_QUERY_SCOPED_SHARD_COUNT = 96;
const MAX_QUERY_SCOPED_SHARD_BYTES = 24000000;
const MAX_QUERY_SCOPED_SHARD_UNCOMPRESSED_BYTES = 96000000;
const MAX_QUERY_SCOPED_PAGE_COUNT = 30000;
const MAX_QUERY_CACHE_ENTRIES = 4;
const QUERY_INDEX_STOP_TOKENS = new Set([
  'a', 'an', 'and', 'are', 'as', 'at', 'be', 'by', 'for', 'from',
  'in', 'into', 'is', 'it', 'of', 'on', 'or', 'our', 'the', 'to',
  'with', 'your', 'you', 'we', 'this', 'that', 'these', 'those'
]);
const agent = new https.Agent({ keepAlive: true, maxSockets: MAX_SHARD_CONCURRENCY });
let cache = null;
let manifestCache = null;
const queryCache = new Map();
const queryIndexShardCache = new Map();

function getBuffer(url, maxBytes, timeoutMs = 10000) {
  return new Promise((resolve, reject) => {
    let settled = false;
    let request;
    const chunks = [];
    let total = 0;
    const fail = (error) => {
      if (settled) return;
      settled = true;
      reject(error);
    };
    try {
      request = https.get(url, {
        agent,
        headers: {
          'user-agent': 'EverythingHub-IndependentSearch/2.2',
          accept: '*/*'
        }
      }, (response) => {
        if (response.statusCode < 200 || response.statusCode >= 300) {
          response.resume();
          fail(new Error('upstream_http_' + response.statusCode));
          return;
        }
        const declared = Number(response.headers['content-length'] || 0);
        if (declared > maxBytes) {
          response.resume();
          fail(new Error('upstream_size_limit'));
          return;
        }
        response.on('data', (chunk) => {
          total += chunk.length;
          if (total > maxBytes) {
            fail(new Error('upstream_size_limit'));
            request.destroy();
            return;
          }
          chunks.push(chunk);
        });
        response.on('end', () => {
          if (settled) return;
          settled = true;
          resolve(Buffer.concat(chunks, total));
        });
        response.on('error', fail);
      });
      request.setTimeout(timeoutMs, () => request.destroy(new Error('upstream_timeout')));
      request.on('error', fail);
    } catch (error) {
      fail(error);
    }
  });
}

function validateManifestEnvelope(manifest) {
  if (!manifest || manifest.version !== 1 || manifest.integrity !== 'sha256') {
    throw new Error('manifest_schema_invalid');
  }
  const nowSeconds = Math.floor(Date.now() / 1000);
  if (!Number.isSafeInteger(manifest.updated_at) || manifest.updated_at <= 0 ||
      manifest.updated_at > nowSeconds + 300) {
    throw new Error('manifest_timestamp_invalid');
  }
  if (!Number.isSafeInteger(manifest.total_pages) ||
      manifest.total_pages < 1 || manifest.total_pages > MAX_INDEXED_PAGES) {
    throw new Error('manifest_page_count_invalid');
  }
  if (!Number.isSafeInteger(manifest.shard_count) ||
      manifest.shard_count < 1 || manifest.shard_count > MAX_SHARD_COUNT ||
      !Array.isArray(manifest.shards) || manifest.shards.length !== manifest.shard_count) {
    throw new Error('manifest_shard_count_invalid');
  }
}

function validateCatalogMetadata(manifest) {
  const meta = manifest && manifest.catalog_gzip;
  if (!meta || meta.file !== 'catalog-v1.json.gz' ||
      meta.pages !== manifest.total_pages ||
      !Number.isSafeInteger(meta.bytes) || meta.bytes < 1 ||
      meta.bytes > MAX_CATALOG_GZIP_BYTES ||
      typeof meta.sha256 !== 'string' || !/^[a-f0-9]{64}$/i.test(meta.sha256)) {
    throw new Error('manifest_catalog_metadata_invalid');
  }
  return meta;
}

function validatePage(page, prefix) {
  if (!page || typeof page.url !== 'string') {
    throw new Error(prefix + '_record_url_missing');
  }
  let url;
  try { url = new URL(page.url); } catch { throw new Error(prefix + '_record_url_invalid'); }
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) {
    throw new Error(prefix + '_record_url_unsafe');
  }
  if (page.robots_allowed === false || page.robotsAllowed === false) {
    throw new Error(prefix + '_contains_robots_denied_record');
  }
}

function validateManifestAndCatalog(manifest, compressed) {
  validateManifestEnvelope(manifest);
  const meta = validateCatalogMetadata(manifest);
  if (!Buffer.isBuffer(compressed) || compressed.length !== meta.bytes) {
    throw new Error('catalog_size_mismatch');
  }
  const digest = crypto.createHash('sha256').update(compressed).digest('hex');
  if (digest.toLowerCase() !== meta.sha256.toLowerCase()) {
    throw new Error('catalog_integrity_mismatch');
  }
  let payload;
  try {
    payload = JSON.parse(zlib.gunzipSync(compressed, {
      maxOutputLength: MAX_CATALOG_JSON_BYTES
    }).toString('utf8'));
  } catch (error) {
    throw new Error('catalog_decompression_or_json_invalid: ' + error.message);
  }
  if (!payload || payload.version !== 1 || !Array.isArray(payload.pages) ||
      payload.pages.length !== manifest.total_pages) {
    throw new Error('catalog_page_count_or_schema_mismatch');
  }
  for (const page of payload.pages) validatePage(page, 'catalog');
  return {
    pages: payload.pages,
    updatedAt: manifest.updated_at,
    sha256: digest,
    indexedPages: payload.pages.length,
    source: 'git-commit-pinned-compressed-catalog',
    integrity: 'sha256',
    shardCount: manifest.shard_count,
    transport: 'gzip'
  };
}

function validateShardManifest(manifest) {
  validateManifestEnvelope(manifest);
  let totalBytes = 0;
  let totalUncompressedBytes = 0;
  let totalPages = 0;
  let shardFormat = null;
  const seen = new Set();
  for (const shard of manifest.shards) {
    if (!shard || typeof shard.file !== 'string' ||
        !/^shard-\d{4}\.json(?:\.gz)?$/.test(shard.file)) {
      throw new Error('manifest_shard_file_invalid');
    }
    const compressed = shard.file.endsWith('.json.gz');
    const format = compressed ? 'gzip' : 'legacy';
    if (shardFormat && shardFormat !== format) throw new Error('manifest_mixed_shard_formats');
    shardFormat = format;
    if (seen.has(shard.file)) throw new Error('manifest_duplicate_shard_file');
    seen.add(shard.file);
    if (!Number.isSafeInteger(shard.pages) || shard.pages < 1 || shard.pages > MAX_PAGES_PER_SHARD) {
      throw new Error('manifest_shard_page_count_invalid');
    }
    if (!Number.isSafeInteger(shard.bytes) || shard.bytes < 1 || shard.bytes > MAX_SHARD_BYTES) {
      throw new Error('manifest_shard_size_invalid');
    }
    let uncompressedBytes = shard.uncompressed_bytes;
    if (compressed) {
      if (!Number.isSafeInteger(uncompressedBytes) || uncompressedBytes < 1 ||
          uncompressedBytes > MAX_SHARD_UNCOMPRESSED_BYTES) {
        throw new Error('manifest_shard_uncompressed_size_invalid');
      }
    } else {
      if (uncompressedBytes != null && uncompressedBytes !== shard.bytes) {
        throw new Error('manifest_legacy_shard_uncompressed_size_invalid');
      }
      uncompressedBytes = shard.bytes;
    }
    if (typeof shard.sha256 !== 'string' || !/^[a-f0-9]{64}$/i.test(shard.sha256)) {
      throw new Error('manifest_shard_hash_invalid');
    }
    totalBytes += shard.bytes;
    totalUncompressedBytes += uncompressedBytes;
    totalPages += shard.pages;
    if (compressed && totalBytes > MAX_TOTAL_COMPRESSED_SHARD_BYTES) {
      throw new Error('manifest_total_compressed_shard_bytes_exceeded');
    }
    if (!compressed && totalBytes > MAX_TOTAL_SHARD_BYTES) {
      throw new Error('manifest_total_shard_bytes_exceeded');
    }
    if (totalUncompressedBytes > MAX_TOTAL_SHARD_UNCOMPRESSED_BYTES) {
      throw new Error('manifest_total_shard_uncompressed_bytes_exceeded');
    }
  }
  if (totalPages !== manifest.total_pages) throw new Error('manifest_shard_page_total_mismatch');
  return { shards: manifest.shards, totalBytes, totalUncompressedBytes, totalPages, shardFormat };
}

function validateShardPayload(meta, buffer) {
  const compressed = meta && typeof meta.file === 'string' && meta.file.endsWith('.json.gz');
  const validName = meta && typeof meta.file === 'string' &&
    /^shard-\d{4}\.json(?:\.gz)?$/.test(meta.file);
  if (!validName) throw new Error('shard_file_invalid');
  if (!Buffer.isBuffer(buffer) || buffer.length !== meta.bytes) {
    throw new Error('shard_size_mismatch');
  }
  const digest = crypto.createHash('sha256').update(buffer).digest('hex');
  if (digest.toLowerCase() !== String(meta.sha256 || '').toLowerCase()) {
    throw new Error('shard_integrity_mismatch');
  }
  let decoded = buffer;
  if (compressed) {
    try {
      decoded = zlib.gunzipSync(buffer, { maxOutputLength: MAX_SHARD_UNCOMPRESSED_BYTES });
    } catch {
      throw new Error('shard_gzip_invalid');
    }
    if (decoded.length !== meta.uncompressed_bytes) {
      throw new Error('shard_uncompressed_size_mismatch');
    }
  } else if (meta.uncompressed_bytes != null && meta.uncompressed_bytes !== buffer.length) {
    throw new Error('shard_uncompressed_size_mismatch');
  }
  let payload;
  try { payload = JSON.parse(decoded.toString('utf8')); } catch {
    throw new Error('shard_json_invalid');
  }
  if (!payload || payload.version !== 1 || !Array.isArray(payload.pages) ||
      payload.pages.length !== meta.pages) {
    throw new Error('shard_page_count_or_schema_mismatch');
  }
  for (const page of payload.pages) validatePage(page, 'shard');
  return payload.pages;
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
        return;
      }
    }
  });
  await Promise.all(workers);
  if (failure) throw failure;
  return results;
}

async function loadShardedCatalog(manifest, commitSha, manifestSha256, fetchBuffer = getBuffer) {
  const checked = validateShardManifest(manifest);
  if (typeof commitSha !== 'string' || !/^[a-f0-9]{40}$/i.test(commitSha)) {
    throw new Error('catalog_git_ref_invalid');
  }
  if (typeof manifestSha256 !== 'string' || !/^[a-f0-9]{64}$/i.test(manifestSha256)) {
    throw new Error('manifest_digest_invalid');
  }
  const baseUrl = CATALOG_BASE_URL + commitSha + '/public-search/';
  const loaded = await mapLimit(checked.shards, MAX_SHARD_CONCURRENCY, async (meta) => {
    const buffer = await fetchBuffer(
      baseUrl + encodeURIComponent(meta.file),
      meta.bytes,
      SHARD_FETCH_TIMEOUT_MS
    );
    return validateShardPayload(meta, buffer);
  });
  const pages = [];
  for (const shardPages of loaded) pages.push(...shardPages);
  if (pages.length !== manifest.total_pages) throw new Error('catalog_shard_total_page_mismatch');
  const seenUrls = new Set();
  for (const page of pages) {
    if (seenUrls.has(page.url)) throw new Error('catalog_duplicate_url');
    seenUrls.add(page.url);
  }
  return {
    pages,
    updatedAt: manifest.updated_at,
    sha256: manifestSha256,
    indexedPages: pages.length,
    source: 'git-commit-pinned-public-search-shards',
    integrity: 'sha256',
    shardCount: checked.shards.length,
    shardBytes: checked.totalBytes,
    transport: 'shards'
  };
}

async function loadCatalogFromManifest(manifest, commitSha, manifestRaw, fetchBuffer = getBuffer) {
  validateManifestEnvelope(manifest);
  if (typeof commitSha !== 'string' || !/^[a-f0-9]{40}$/i.test(commitSha)) {
    throw new Error('catalog_git_ref_invalid');
  }
  if (!Buffer.isBuffer(manifestRaw)) manifestRaw = Buffer.from(manifestRaw || '');
  const manifestSha256 = crypto.createHash('sha256').update(manifestRaw).digest('hex');
  const baseUrl = CATALOG_BASE_URL + commitSha + '/public-search/';
  let gzipError = null;

  if (manifest.catalog_gzip != null) {
    try {
      const meta = validateCatalogMetadata(manifest);
      const compressed = await fetchBuffer(
        baseUrl + encodeURIComponent(meta.file),
        MAX_CATALOG_GZIP_BYTES
      );
      return validateManifestAndCatalog(manifest, compressed);
    } catch (error) {
      gzipError = error;
    }
  }

  try {
    return await loadShardedCatalog(manifest, commitSha, manifestSha256, fetchBuffer);
  } catch (shardError) {
    if (gzipError) throw new Error('catalog_gzip_and_shards_failed');
    throw shardError;
  }
}

function normalizeQueryIndexTokens(value) {
  const normalized = String(value || '').normalize('NFKC').toLowerCase();
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

function queryTokensForPage(page) {
  const values = [page && page.title, page && page.url, page && page.description, page && page.text];
  const product = page && page.product;
  if (product && typeof product === 'object' && !Array.isArray(product)) {
    for (const key of ['name', 'brand', 'sku', 'price', 'availability']) values.push(product[key]);
  }
  const tokens = new Set();
  for (const value of values) {
    if (typeof value !== 'string' && typeof value !== 'number') continue;
    for (const token of normalizeQueryIndexTokens(value)) tokens.add(token);
  }
  return tokens;
}

function validateQueryIndexMetadata(manifest) {
  const index = manifest && manifest.query_index;
  if (index == null) return null;
  if (!index || index.version !== 1 || !Array.isArray(index.shards) ||
      !Number.isSafeInteger(index.shard_count) || index.shard_count < 0 ||
      index.shard_count > MAX_QUERY_INDEX_SHARDS || index.shards.length !== index.shard_count ||
      !Number.isSafeInteger(index.token_count) || index.token_count < 0 ||
      index.token_count > MAX_QUERY_INDEX_TOKENS ||
      !Number.isSafeInteger(index.posting_count) || index.posting_count < 0 ||
      index.posting_count > MAX_QUERY_INDEX_POSTINGS ||
      !Number.isSafeInteger(index.compressed_bytes) || index.compressed_bytes < 0 ||
      index.compressed_bytes > MAX_QUERY_INDEX_TOTAL_COMPRESSED_BYTES ||
      !Number.isSafeInteger(index.uncompressed_bytes) || index.uncompressed_bytes < 0 ||
      index.uncompressed_bytes > MAX_QUERY_INDEX_TOTAL_UNCOMPRESSED_BYTES) {
    throw new Error('query_index_manifest_invalid');
  }
  const entries = new Map();
  let compressedBytes = 0;
  let uncompressedBytes = 0;
  let tokenCount = 0;
  for (const entry of index.shards) {
    const match = entry && typeof entry.file === 'string'
      ? /^query-index\/([a-f0-9]{2})\.json\.gz$/i.exec(entry.file)
      : null;
    if (!match || entries.has(match[1].toLowerCase()) ||
        !Number.isSafeInteger(entry.bytes) || entry.bytes < 1 ||
        entry.bytes > MAX_QUERY_INDEX_SHARD_COMPRESSED_BYTES ||
        !Number.isSafeInteger(entry.uncompressed_bytes) || entry.uncompressed_bytes < 1 ||
        entry.uncompressed_bytes > MAX_QUERY_INDEX_SHARD_UNCOMPRESSED_BYTES ||
        !Number.isSafeInteger(entry.items) || entry.items < 1 ||
        entry.items > MAX_QUERY_INDEX_TOKENS ||
        typeof entry.sha256 !== 'string' || !/^[a-f0-9]{64}$/i.test(entry.sha256)) {
      throw new Error('query_index_file_entry_invalid');
    }
    compressedBytes += entry.bytes;
    uncompressedBytes += entry.uncompressed_bytes;
    tokenCount += entry.items;
    entries.set(match[1].toLowerCase(), entry);
  }
  if (compressedBytes !== index.compressed_bytes ||
      uncompressedBytes !== index.uncompressed_bytes ||
      tokenCount !== index.token_count ||
      (index.token_count === 0 ? index.posting_count !== 0 : index.posting_count < index.token_count)) {
    throw new Error('query_index_manifest_totals_invalid');
  }
  return entries;
}

function decodeQueryIndexShard(entry, buffer, prefix, manifest) {
  if (!Buffer.isBuffer(buffer) || buffer.length !== entry.bytes ||
      buffer.length > MAX_QUERY_INDEX_SHARD_COMPRESSED_BYTES) {
    throw new Error('query_index_size_mismatch');
  }
  const digest = crypto.createHash('sha256').update(buffer).digest('hex');
  if (digest.toLowerCase() !== entry.sha256.toLowerCase()) {
    throw new Error('query_index_sha256_mismatch');
  }
  let decoded;
  try {
    decoded = zlib.gunzipSync(buffer, { maxOutputLength: MAX_QUERY_INDEX_SHARD_UNCOMPRESSED_BYTES });
  } catch {
    throw new Error('query_index_gzip_invalid');
  }
  if (decoded.length !== entry.uncompressed_bytes) throw new Error('query_index_uncompressed_size_mismatch');
  let payload;
  try { payload = JSON.parse(decoded.toString('utf8')); } catch {
    throw new Error('query_index_json_invalid');
  }
  if (!payload || payload.version !== 1 || payload.prefix !== prefix ||
      !payload.tokens || typeof payload.tokens !== 'object' || Array.isArray(payload.tokens) ||
      Object.keys(payload.tokens).length !== entry.items) {
    throw new Error('query_index_schema_invalid');
  }
  for (const [token, postings] of Object.entries(payload.tokens)) {
    const normalized = normalizeQueryIndexTokens(token);
    const tokenPrefix = crypto.createHash('sha256').update(token).digest('hex').slice(0, 2);
    if (normalized.length !== 1 || normalized[0] !== token || tokenPrefix !== prefix ||
        !Array.isArray(postings) || postings.length > manifest.shard_count) {
      throw new Error('query_index_token_invalid');
    }
    let priorIndex = -1;
    for (const pair of postings) {
      if (!Array.isArray(pair) || pair.length !== 2 ||
          !Number.isSafeInteger(pair[0]) || pair[0] < 0 || pair[0] >= manifest.shard_count ||
          pair[0] <= priorIndex || !Number.isSafeInteger(pair[1]) || pair[1] < 1 ||
          pair[1] > manifest.shards[pair[0]].pages) {
        throw new Error('query_index_posting_invalid');
      }
      priorIndex = pair[0];
    }
  }
  return payload.tokens;
}

function emptyQueryScopedCatalog(manifest, manifestSha256, tokenCount) {
  return {
    pages: [],
    updatedAt: manifest.updated_at,
    sha256: manifestSha256,
    indexedPages: manifest.total_pages,
    source: 'git-commit-pinned-query-indexed-public-search-shards',
    integrity: 'sha256',
    shardCount: manifest.shard_count,
    transport: 'query-indexed-shards',
    queryScoped: true,
    scannedPages: 0,
    candidatePageCount: 0,
    candidateShardCount: 0,
    totalCandidateShardCount: 0,
    totalCandidatePageCount: 0,
    candidateTruncated: false,
    candidateSelection: 'ranked-query-posting-frequency',
    queryIndexTokensUsed: tokenCount,
    queryIndexPostingCount: 0
  };
}

async function loadQueryScopedCatalogFromManifest(manifest, commitSha, manifestRaw, query, fetchBuffer = getBuffer) {
  validateManifestEnvelope(manifest);
  const checked = validateShardManifest(manifest);
  if (typeof commitSha !== 'string' || !/^[a-f0-9]{40}$/i.test(commitSha)) {
    throw new Error('catalog_git_ref_invalid');
  }
  if (!Buffer.isBuffer(manifestRaw)) manifestRaw = Buffer.from(manifestRaw || '');
  if (!manifestRaw.length || manifestRaw.length > MAX_MANIFEST_BYTES) {
    throw new Error('manifest_raw_size_invalid');
  }
  const manifestSha256 = crypto.createHash('sha256').update(manifestRaw).digest('hex');
  const entries = validateQueryIndexMetadata(manifest);
  if (!entries) return null;
  const allTerms = normalizeQueryIndexTokens(query);
  const terms = allTerms.slice(0, 12);
  if (!terms.length) return null;

  const baseUrl = CATALOG_BASE_URL + commitSha + '/public-search/';
  const prefixes = Array.from(new Set(terms.map(term =>
    crypto.createHash('sha256').update(term).digest('hex').slice(0, 2)
  )));
  const indexEntries = [];
  for (const prefix of prefixes) {
    const entry = entries.get(prefix);
    if (!entry) return emptyQueryScopedCatalog(manifest, manifestSha256, terms.length);
    indexEntries.push({ prefix, entry });
  }

  const loadedIndexShards = await mapLimit(indexEntries, 8, async ({ prefix, entry }) => {
    const cacheKey = commitSha + ':' + prefix;
    const cached = queryIndexShardCache.get(cacheKey);
    if (cached && Date.now() - cached.loadedAt < CACHE_TTL_MS) return { prefix, tokens: cached.tokens };
    const path = entry.file.split('/').map(encodeURIComponent).join('/');
    const raw = await fetchBuffer(baseUrl + path, entry.bytes, 7000);
    const tokens = decodeQueryIndexShard(entry, raw, prefix, manifest);
    queryIndexShardCache.set(cacheKey, { tokens, loadedAt: Date.now() });
    if (queryIndexShardCache.size > MAX_QUERY_INDEX_SHARDS) {
      queryIndexShardCache.delete(queryIndexShardCache.keys().next().value);
    }
    return { prefix, tokens };
  });
  const tokensByPrefix = new Map(loadedIndexShards.map(item => [item.prefix, item.tokens]));

  let candidateShardIds = null;
  const postingsByTerm = new Map();
  for (const term of terms) {
    const prefix = crypto.createHash('sha256').update(term).digest('hex').slice(0, 2);
    const postings = tokensByPrefix.get(prefix) && tokensByPrefix.get(prefix)[term];
    if (!Array.isArray(postings)) {
      return emptyQueryScopedCatalog(manifest, manifestSha256, terms.length);
    }
    const counts = new Map(postings.map(pair => [pair[0], pair[1]]));
    postingsByTerm.set(term, counts);
    const ids = new Set(counts.keys());
    candidateShardIds = candidateShardIds === null
      ? ids
      : new Set(Array.from(candidateShardIds).filter(id => ids.has(id)));
    if (!candidateShardIds.size) {
      return emptyQueryScopedCatalog(manifest, manifestSha256, terms.length);
    }
  }

  const termWeights = new Map();
  for (const term of terms) {
    const counts = postingsByTerm.get(term);
    let totalTermCount = 0;
    for (const count of counts.values()) totalTermCount += count;
    termWeights.set(term, {
      counts,
      inverseFrequency: Math.log1p(manifest.total_pages / Math.max(1, totalTermCount))
    });
  }
  const rankedCandidates = Array.from(candidateShardIds).map(index => {
    const meta = manifest.shards[index];
    let score = 0;
    for (const { counts, inverseFrequency } of termWeights.values()) {
      score += (counts.get(index) || 0) * inverseFrequency;
    }
    return { index, meta, score };
  }).sort((a, b) => b.score - a.score || a.index - b.index);
  const totalCandidateShardCount = rankedCandidates.length;
  const totalCandidatePageCount = rankedCandidates.reduce((sum, item) => sum + item.meta.pages, 0);
  const candidateShards = [];
  let candidateCompressedBytes = 0;
  let candidateUncompressedBytes = 0;
  let candidatePageCount = 0;
  for (const item of rankedCandidates) {
    if (candidateShards.length >= MAX_QUERY_SCOPED_SHARD_COUNT) break;
    const compressedBytes = item.meta.bytes;
    const uncompressedBytes = item.meta.uncompressed_bytes || item.meta.bytes;
    const pageCount = item.meta.pages;
    if (candidateCompressedBytes + compressedBytes > MAX_QUERY_SCOPED_SHARD_BYTES ||
        candidateUncompressedBytes + uncompressedBytes > MAX_QUERY_SCOPED_SHARD_UNCOMPRESSED_BYTES ||
        candidatePageCount + pageCount > MAX_QUERY_SCOPED_PAGE_COUNT) {
      continue;
    }
    candidateShards.push(item);
    candidateCompressedBytes += compressedBytes;
    candidateUncompressedBytes += uncompressedBytes;
    candidatePageCount += pageCount;
  }
  if (!candidateShards.length) throw new Error('query_index_candidate_budget_exceeded');
  const candidateTruncated = candidateShards.length < totalCandidateShardCount;
  const selectedById = new Map(candidateShards.map(item => [item.index, item]));
  const shardIds = Array.from(selectedById.keys()).sort((a, b) => a - b);
  const orderedCandidateShards = shardIds.map(index => selectedById.get(index));
  const loadedShards = await mapLimit(orderedCandidateShards, MAX_SHARD_CONCURRENCY, async ({ index, meta }) => {
    const raw = await fetchBuffer(baseUrl + encodeURIComponent(meta.file), meta.bytes, SHARD_FETCH_TIMEOUT_MS);
    return { index, pages: validateShardPayload(meta, raw) };
  });

  const pages = [];
  const seenUrls = new Set();
  let queryIndexPostingCount = 0;
  for (const { index, pages: shardPages } of loadedShards) {
    const actualCounts = new Map(terms.map(term => [term, 0]));
    for (const page of shardPages) {
      if (seenUrls.has(page.url)) throw new Error('catalog_duplicate_url');
      seenUrls.add(page.url);
      const pageTokens = queryTokensForPage(page);
      for (const term of terms) if (pageTokens.has(term)) actualCounts.set(term, actualCounts.get(term) + 1);
      pages.push(page);
    }
    for (const term of terms) {
      const expected = postingsByTerm.get(term).get(index);
      const actual = actualCounts.get(term);
      if (expected !== actual) throw new Error('query_index_posting_mismatch');
      queryIndexPostingCount += expected;
    }
  }
  if (pages.length !== candidatePageCount) throw new Error('query_index_candidate_page_count_mismatch');
  return {
    pages,
    updatedAt: manifest.updated_at,
    sha256: manifestSha256,
    indexedPages: manifest.total_pages,
    source: 'git-commit-pinned-query-indexed-public-search-shards',
    integrity: 'sha256',
    shardCount: manifest.shard_count,
    transport: 'query-indexed-shards',
    queryScoped: true,
    scannedPages: pages.length,
    candidatePageCount,
    candidateShardCount: shardIds.length,
    totalCandidateShardCount,
    totalCandidatePageCount,
    candidateTruncated,
    candidateSelection: 'ranked-query-posting-frequency',
    queryIndexTokensUsed: terms.length,
    queryIndexTotalQueryTokens: allTerms.length,
    queryIndexPostingCount
  };
}

async function loadFullCatalog() {
  if (cache && Date.now() - cache.loadedAt < CACHE_TTL_MS) return { ...cache, stale: false };
  try {
    const gitRef = JSON.parse((await getBuffer(GIT_REF_URL + '?v=' + Date.now(), 4096)).toString('utf8'));
    const sha = gitRef && gitRef.object && gitRef.object.sha;
    if (typeof sha !== 'string' || !/^[a-f0-9]{40}$/i.test(sha)) {
      throw new Error('catalog_git_ref_invalid');
    }
    const manifestRaw = await getBuffer(
      CATALOG_BASE_URL + sha + '/public-search/manifest.json',
      MAX_MANIFEST_BYTES
    );
    let manifest;
    try { manifest = JSON.parse(manifestRaw.toString('utf8')); } catch {
      throw new Error('manifest_json_invalid');
    }
    const validated = await loadCatalogFromManifest(manifest, sha, manifestRaw, getBuffer);
    cache = { ...validated, loadedAt: Date.now(), stale: false };
    return { ...cache };
  } catch (error) {
    if (cache && Date.now() - cache.loadedAt < STALE_TTL_MS) {
      return { ...cache, stale: true, refreshError: String(error.message || error).slice(0, 180) };
    }
    throw error;
  }
}

async function loadQueryScopedCatalog(query) {
  const text = String(query || '').trim();
  if (!text || text.length > 160) throw new Error('catalog_query_invalid');
  const cacheKey = text.toLowerCase();
  const prior = queryCache.get(cacheKey);
  if (prior && Date.now() - prior.loadedAt < CACHE_TTL_MS) return { ...prior, stale: false };
  if (cache && Date.now() - cache.loadedAt < CACHE_TTL_MS) return { ...cache, stale: false };
  try {
    let snapshot = manifestCache;
    if (!snapshot || Date.now() - snapshot.loadedAt >= CACHE_TTL_MS) {
      const gitRef = JSON.parse((await getBuffer(GIT_REF_URL + '?v=' + Date.now(), 4096)).toString('utf8'));
      const sha = gitRef && gitRef.object && gitRef.object.sha;
      if (typeof sha !== 'string' || !/^[a-f0-9]{40}$/i.test(sha)) throw new Error('catalog_git_ref_invalid');
      const manifestRaw = await getBuffer(
        CATALOG_BASE_URL + sha + '/public-search/manifest.json',
        MAX_MANIFEST_BYTES
      );
      let manifest;
      try { manifest = JSON.parse(manifestRaw.toString('utf8')); } catch {
        throw new Error('manifest_json_invalid');
      }
      validateManifestEnvelope(manifest);
      snapshot = { sha, manifest, manifestRaw, loadedAt: Date.now() };
      manifestCache = snapshot;
    }
    const scoped = await loadQueryScopedCatalogFromManifest(
      snapshot.manifest, snapshot.sha, snapshot.manifestRaw, text, getBuffer
    );
    if (scoped) {
      const result = { ...scoped, loadedAt: Date.now(), stale: false };
      queryCache.set(cacheKey, result);
      if (queryCache.size > MAX_QUERY_CACHE_ENTRIES) queryCache.delete(queryCache.keys().next().value);
      return { ...result };
    }
    const full = await loadCatalogFromManifest(snapshot.manifest, snapshot.sha, snapshot.manifestRaw, getBuffer);
    cache = { ...full, loadedAt: Date.now(), stale: false };
    return { ...cache };
  } catch (error) {
    if (prior && Date.now() - prior.loadedAt < STALE_TTL_MS) {
      return { ...prior, stale: true, refreshError: String(error.message || error).slice(0, 180) };
    }
    if (cache && Date.now() - cache.loadedAt < STALE_TTL_MS) {
      return { ...cache, stale: true, refreshError: String(error.message || error).slice(0, 180) };
    }
    throw error;
  }
}

async function loadCatalog(query) {
  if (String(query || '').trim()) return loadQueryScopedCatalog(query);
  return loadFullCatalog();
}

function clearCatalogCache() {
  cache = null;
  manifestCache = null;
  queryCache.clear();
  queryIndexShardCache.clear();
}

module.exports = {
  MAX_MANIFEST_BYTES,
  MAX_CATALOG_GZIP_BYTES,
  MAX_CATALOG_JSON_BYTES,
  MAX_INDEXED_PAGES,
  MAX_SHARD_COUNT,
  MAX_PAGES_PER_SHARD,
  MAX_SHARD_BYTES,
  MAX_SHARD_UNCOMPRESSED_BYTES,
  MAX_TOTAL_SHARD_BYTES,
  MAX_TOTAL_COMPRESSED_SHARD_BYTES,
  MAX_TOTAL_SHARD_UNCOMPRESSED_BYTES,
  MAX_SHARD_CONCURRENCY,
  MAX_QUERY_INDEX_SHARDS,
  MAX_QUERY_SCOPED_SHARD_COUNT,
  MAX_QUERY_SCOPED_SHARD_BYTES,
  MAX_QUERY_SCOPED_SHARD_UNCOMPRESSED_BYTES,
  MAX_QUERY_CACHE_ENTRIES,
  validateQueryIndexMetadata,
  decodeQueryIndexShard,
  normalizeQueryIndexTokens,
  queryTokensForPage,
  loadQueryScopedCatalogFromManifest,
  validateManifestAndCatalog,
  validateShardManifest,
  validateShardPayload,
  loadShardedCatalog,
  loadCatalogFromManifest,
  mapLimit,
  loadCatalog,
  clearCatalogCache
};
