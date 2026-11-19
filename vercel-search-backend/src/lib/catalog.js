'use strict';
const https = require('node:https');
const zlib = require('node:zlib');
const crypto = require('node:crypto');
const MANIFEST_URL = 'https://raw.githubusercontent.com/rshure001/EverythingHub-Crawler/main/public-search/manifest.json';
const CATALOG_URL = 'https://raw.githubusercontent.com/rshure001/EverythingHub-Crawler/main/public-search/catalog-v1.json.gz';
const CACHE_TTL_MS = 300000;
const STALE_TTL_MS = 3600000;
const MAX_MANIFEST_BYTES = 128000;
const MAX_CATALOG_GZIP_BYTES = 10000000;
const MAX_CATALOG_JSON_BYTES = 64000000;
const MAX_INDEXED_PAGES = 20000;
let cache = null;

function getBuffer(url, maxBytes) {
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
        headers: { 'user-agent': 'EverythingHub-IndependentSearch/2.0', accept: '*/*' }
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
      request.setTimeout(10000, () => request.destroy(new Error('upstream_timeout')));
      request.on('error', fail);
    } catch (error) {
      fail(error);
    }
  });
}

function validateManifestAndCatalog(manifest, compressed) {
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
      manifest.shard_count < 1 || manifest.shard_count > 128 ||
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
  for (const page of payload.pages) {
    if (!page || typeof page.url !== 'string') throw new Error('catalog_record_url_missing');
    let url;
    try { url = new URL(page.url); } catch { throw new Error('catalog_record_url_invalid'); }
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) {
      throw new Error('catalog_record_url_unsafe');
    }
    if (page.robots_allowed === false || page.robotsAllowed === false) {
      throw new Error('catalog_contains_robots_denied_record');
    }
  }
  return {
    pages: payload.pages,
    updatedAt: manifest.updated_at,
    sha256: digest,
    indexedPages: payload.pages.length
  };
}

async function loadCatalog() {
  if (cache && Date.now() - cache.loadedAt < CACHE_TTL_MS) return { ...cache, stale: false };
  try {
    const [manifestRaw, compressed] = await Promise.all([
      getBuffer(MANIFEST_URL, MAX_MANIFEST_BYTES),
      getBuffer(CATALOG_URL, MAX_CATALOG_GZIP_BYTES)
    ]);
    const manifest = JSON.parse(manifestRaw.toString('utf8'));
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
  MAX_INDEXED_PAGES,
  validateManifestAndCatalog,
  loadCatalog,
  clearCatalogCache
};
