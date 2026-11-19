'use strict';
const https = require('node:https');
const zlib = require('node:zlib');
const crypto = require('node:crypto');

const CATALOG_BASE_URL = 'https://raw.githubusercontent.com/rshure001/EverythingHub-Crawler/';
const GIT_REF_URL = 'https://api.github.com/repos/rshure001/EverythingHub-Crawler/git/ref/heads/main';
const SHARD_BASE_URL = 'https://raw.githubusercontent.com/rshure001/EverythingHub-Crawler/main/public-search/';
const CACHE_TTL_MS = 300000;
const STALE_TTL_MS = 3600000;
const MAX_MANIFEST_BYTES = 128000;
const MAX_CATALOG_GZIP_BYTES = 12000000;
const MAX_CATALOG_JSON_BYTES = 64000000;
const MAX_INDEXED_PAGES = 30000;
const MAX_SHARD_COUNT = 256;
const MAX_SHARD_BYTES = 1000000;
const MAX_TOTAL_SHARD_BYTES = 48000000;
const MAX_SHARD_CONCURRENCY = 32;
const SHARD_FETCH_TIMEOUT_MS = 7000;
const agent = new https.Agent({ keepAlive: true, maxSockets: MAX_SHARD_CONCURRENCY });
let cache = null;

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
          'user-agent': 'EverythingHub-IndependentSearch/2.1',
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
  const meta = manifest.catalog_gzip;
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
  const meta = validateManifestEnvelope(manifest);
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
    integrity: 'catalog-sha256',
    shardCount: manifest.shard_count
  };
}

function validateShardManifest(manifest) {
  validateManifestEnvelope(manifest);
  let totalBytes = 0;
  let totalPages = 0;
  const seen = new Set();
  for (const shard of manifest.shards) {
    if (!shard || typeof shard.file !== 'string' || !/^shard-\d{4}\.json$/.test(shard.file)) {
      throw new Error('manifest_shard_file_invalid');
    }
    if (seen.has(shard.file)) throw new Error('manifest_duplicate_shard_file');
    seen.add(shard.file);
    if (!Number.isSafeInteger(shard.pages) || shard.pages < 1 || shard.pages > 1000) {
      throw new Error('manifest_shard_page_count_invalid');
    }
    if (!Number.isSafeInteger(shard.bytes) || shard.bytes < 1 || shard.bytes > MAX_SHARD_BYTES) {
      throw new Error('manifest_shard_size_invalid');
    }
    if (typeof shard.sha256 !== 'string' || !/^[a-f0-9]{64}$/i.test(shard.sha256)) {
      throw new Error('manifest_shard_hash_invalid');
    }
    totalBytes += shard.bytes;
    totalPages += shard.pages;
    if (totalBytes > MAX_TOTAL_SHARD_BYTES) throw new Error('manifest_total_shard_bytes_exceeded');
  }
  if (totalPages !== manifest.total_pages) throw new Error('manifest_shard_page_total_mismatch');
  return { shards: manifest.shards, totalBytes, totalPages };
}

function validateShardPayload(meta, buffer) {
  if (!meta || typeof meta.file !== 'string' || !/^shard-\d{4}\.json$/.test(meta.file)) {
    throw new Error('shard_file_invalid');
  }
  if (!Buffer.isBuffer(buffer) || buffer.length !== meta.bytes) {
    throw new Error('shard_size_mismatch');
  }
  const digest = crypto.createHash('sha256').update(buffer).digest('hex');
  if (digest.toLowerCase() !== String(meta.sha256 || '').toLowerCase()) {
    throw new Error('shard_integrity_mismatch');
  }
  let payload;
  try { payload = JSON.parse(buffer.toString('utf8')); } catch {
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

async function loadShardedCatalog(manifest) {
  const { shards, totalBytes } = validateShardManifest(manifest);
  const loaded = await mapLimit(shards, MAX_SHARD_CONCURRENCY, async (meta) => {
    const buffer = await getBuffer(SHARD_BASE_URL + encodeURIComponent(meta.file), meta.bytes, SHARD_FETCH_TIMEOUT_MS);
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
    sha256: manifest.catalog_gzip.sha256,
    indexedPages: pages.length,
    source: 'public-search-shards',
    integrity: 'per-shard-sha256',
    shardCount: shards.length,
    shardBytes: totalBytes
  };
}

async function loadCatalog() {
  if (cache && Date.now() - cache.loadedAt < CACHE_TTL_MS) return { ...cache, stale: false };
  try {
    // Pin both files to one immutable Git revision to prevent stale CDN mixes.
    const gitRef = JSON.parse((await getBuffer(GIT_REF_URL + '?v=' + Date.now(), 4096)).toString('utf8'));
    const sha = gitRef && gitRef.object && gitRef.object.sha;
    if (typeof sha !== 'string' || !/^[a-f0-9]{40}$/i.test(sha)) {
      throw new Error('catalog_git_ref_invalid');
    }
    const [manifestRaw, compressed] = await Promise.all([
      getBuffer(CATALOG_BASE_URL + sha + '/public-search/manifest.json', MAX_MANIFEST_BYTES),
      getBuffer(CATALOG_BASE_URL + sha + '/public-search/catalog-v1.json.gz', MAX_CATALOG_GZIP_BYTES)
    ]);
    let manifest;
    try { manifest = JSON.parse(manifestRaw.toString('utf8')); } catch {
      throw new Error('manifest_json_invalid');
    }
    const validated = validateManifestAndCatalog(manifest, compressed);
    cache = { ...validated, loadedAt: Date.now(), stale: false };
    return { ...cache };
  } catch (error) {
    if (cache && Date.now() - cache.loadedAt < STALE_TTL_MS) {
      return { ...cache, stale: true, refreshError: String(error.message || error).slice(0, 180) };
    }
    throw error;
  }
}

function clearCatalogCache() { cache = null; }

module.exports = {
  MAX_CATALOG_GZIP_BYTES,
  MAX_CATALOG_JSON_BYTES,
  MAX_INDEXED_PAGES,
  MAX_SHARD_COUNT,
  MAX_SHARD_BYTES,
  MAX_TOTAL_SHARD_BYTES,
  MAX_SHARD_CONCURRENCY,
  validateManifestAndCatalog,
  validateShardManifest,
  validateShardPayload,
  loadShardedCatalog,
  mapLimit,
  loadCatalog,
  clearCatalogCache
};
