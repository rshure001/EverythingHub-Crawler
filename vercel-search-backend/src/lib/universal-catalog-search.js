'use strict';

const https = require('node:https');
const zlib = require('node:zlib');
const crypto = require('node:crypto');
const { tokenize } = require('./search-core');

const RAW = 'https://raw.githubusercontent.com/rshure001/EverythingHub-Crawler/';
const CACHE_MS = 300000;
const STALE_MS = 3600000;
const MAX_MANIFEST_BYTES = 2000000;
const MAX_CATALOGS = 10000;
const MAX_INDEX_SHARDS = 256;
const MAX_ROOT_SHARDS = 16;
const MAX_ROOT_SHARD_BYTES = 12000000;
const MAX_ROOT_TOTAL_BYTES = 16000000;
const MAX_ROOT_SHARD_JSON_BYTES = 96000000; // bounded below the 128 MB aggregate root-index cap
const MAX_ROOT_TOTAL_JSON_BYTES = 128000000;
const MAX_PUBLIC_RECORDS = 250000;
const MAX_DOCUMENT_TEXT = 1200;
const agent = new https.Agent({ keepAlive: true, maxSockets: 4 });
let cache = null;
let failureCache = null;
let inFlight = null;

function getBuffer(url, maxBytes, timeoutMs = 20000) {
  return new Promise((resolve, reject) => {
    let settled = false;
    let request;
    let total = 0;
    const chunks = [];
    const fail = (error) => {
      if (settled) return;
      settled = true;
      reject(error);
    };
    try {
      request = https.get(url, {
        agent,
        headers: {
          'user-agent': 'EverythingHub-UniversalCatalogSearch/1.0',
          accept: '*/*'
        }
      }, (response) => {
        if (response.statusCode < 200 || response.statusCode >= 300) {
          response.resume();
          fail(new Error('universal_catalog_http_' + response.statusCode));
          return;
        }
        if (Number(response.headers['content-length'] || 0) > maxBytes) {
          response.resume();
          fail(new Error('universal_catalog_size_limit'));
          return;
        }
        response.on('data', (chunk) => {
          total += chunk.length;
          if (total > maxBytes) {
            fail(new Error('universal_catalog_size_limit'));
            request.destroy();
            return;
          }
          chunks.push(chunk);
        });
        response.on('end', () => {
          if (!settled) {
            settled = true;
            resolve(Buffer.concat(chunks, total));
          }
        });
        response.on('error', fail);
      });
      request.setTimeout(timeoutMs, () => request.destroy(new Error('universal_catalog_timeout')));
      request.on('error', fail);
    } catch (error) {
      fail(error);
    }
  });
}

function safeHttps(value) {
  try {
    const url = new URL(String(value || ''));
    return url.protocol === 'https:' && !url.username && !url.password ? url.href : '';
  } catch {
    return '';
  }
}

function sha256(buffer) {
  return crypto.createHash('sha256').update(buffer).digest('hex');
}

function canonicalRecordIdsHash(recordIds) {
  return sha256(Buffer.from(JSON.stringify(recordIds) + '\n', 'utf8'));
}

function validateUniversalCatalogManifest(manifest) {
  if (!manifest || manifest.schema_version !== 1 ||
      manifest.record_search_schema_version !== 1 ||
      !Array.isArray(manifest.catalog_indexes) ||
      !Array.isArray(manifest.shards)) {
    throw new Error('universal_catalog_search_schema_missing');
  }
  if (!Number.isSafeInteger(manifest.catalog_count) ||
      manifest.catalog_count < 1 || manifest.catalog_count > MAX_CATALOGS ||
      manifest.catalog_indexes.length !== manifest.catalog_count) {
    throw new Error('universal_catalog_count_invalid');
  }
  if (!Number.isSafeInteger(manifest.index_shard_count) ||
      manifest.index_shard_count < 1 || manifest.index_shard_count > MAX_INDEX_SHARDS ||
      manifest.shards.length !== manifest.index_shard_count) {
    throw new Error('universal_catalog_shard_count_invalid');
  }
  if (!Number.isSafeInteger(manifest.unique_public_records) ||
      manifest.unique_public_records < 1 || manifest.unique_public_records > MAX_PUBLIC_RECORDS ||
      !Number.isSafeInteger(manifest.unique_root_record_search_documents) ||
      manifest.unique_root_record_search_documents !== manifest.unique_public_records ||
      !Number.isSafeInteger(manifest.root_record_search_document_occurrences) ||
      manifest.root_record_search_document_occurrences < manifest.unique_root_record_search_documents ||
      !Number.isSafeInteger(manifest.records_published) || manifest.records_published < 0 ||
      typeof manifest.production_indexed !== 'boolean' ||
      manifest.eligible_records_without_public_index !== 0) {
    throw new Error('universal_catalog_search_totals_invalid');
  }

  const shardByFile = new Map();
  for (const shard of manifest.shards) {
    if (!shard || typeof shard.file !== 'string' ||
        !/^shards\/[a-f0-9]{2}\.json\.gz$/i.test(shard.file) ||
        shardByFile.has(shard.file) ||
        !Number.isSafeInteger(shard.bytes) || shard.bytes < 1 ||
        shard.bytes > MAX_ROOT_SHARD_BYTES ||
        typeof shard.sha256 !== 'string' || !/^[a-f0-9]{64}$/i.test(shard.sha256) ||
        !Number.isSafeInteger(shard.catalog_count) || shard.catalog_count < 1 ||
        !Number.isSafeInteger(shard.record_search_document_occurrences) ||
        shard.record_search_document_occurrences < 0) {
      throw new Error('universal_catalog_shard_metadata_invalid');
    }
    shardByFile.set(shard.file, shard);
  }

  const catalogById = new Map();
  const rootCatalogs = [];
  for (const entry of manifest.catalog_indexes) {
    if (!entry || typeof entry.catalog_id !== 'string' || !entry.catalog_id ||
        catalogById.has(entry.catalog_id) ||
        !['root', 'subcatalog'].includes(entry.catalog_level) ||
        typeof entry.public_search_allowed !== 'boolean' ||
        !Number.isSafeInteger(entry.record_count) || entry.record_count < 0 ||
        !Number.isSafeInteger(entry.record_search_document_count) ||
        entry.record_search_document_count < 0 ||
        typeof entry.index_sha256 !== 'string' || !/^[a-f0-9]{64}$/i.test(entry.index_sha256) ||
        typeof entry.file !== 'string' || !shardByFile.has(entry.file) ||
        typeof entry.title !== 'string' || !entry.title.trim()) {
      throw new Error('universal_catalog_entry_invalid');
    }
    if (entry.catalog_level === 'root' && entry.public_search_allowed === true) {
      if (entry.record_count < 1 || entry.record_search_document_count !== entry.record_count) {
        throw new Error('universal_catalog_root_document_count_invalid');
      }
      rootCatalogs.push(entry);
    } else if (entry.record_search_document_count !== 0) {
      throw new Error('universal_catalog_nonroot_search_documents_invalid');
    }
    catalogById.set(entry.catalog_id, entry);
  }

  const rootFiles = Array.from(new Set(rootCatalogs.map(entry => entry.file)));
  if (!rootFiles.length || rootFiles.length > MAX_ROOT_SHARDS) {
    throw new Error('universal_catalog_root_shard_count_invalid');
  }
  let rootCompressedBytes = 0;
  for (const file of rootFiles) rootCompressedBytes += shardByFile.get(file).bytes;
  if (rootCompressedBytes > MAX_ROOT_TOTAL_BYTES) {
    throw new Error('universal_catalog_root_total_size_limit');
  }

  return { catalogById, rootCatalogs, rootFiles, shardByFile };
}

function decodeRootShard(entry, compressed, expectedShardId) {
  if (!Buffer.isBuffer(compressed) || compressed.length !== entry.bytes ||
      sha256(compressed).toLowerCase() !== entry.sha256.toLowerCase()) {
    throw new Error('universal_catalog_shard_integrity_mismatch');
  }
  let decoded;
  try {
    decoded = zlib.gunzipSync(compressed, { maxOutputLength: MAX_ROOT_SHARD_JSON_BYTES });
  } catch (error) {
    if (error && error.code === 'ERR_BUFFER_TOO_LARGE') {
      throw new Error('universal_catalog_root_shard_uncompressed_limit');
    }
    throw new Error('universal_catalog_shard_gzip_invalid');
  }
  let payload;
  try {
    payload = JSON.parse(decoded.toString('utf8'));
  } catch {
    throw new Error('universal_catalog_shard_json_invalid');
  }
  if (!payload || payload.schema_version !== 1 || payload.shard_id !== expectedShardId ||
      !Array.isArray(payload.catalog_indexes) ||
      payload.catalog_indexes.length !== entry.catalog_count) {
    throw new Error('universal_catalog_shard_schema_invalid');
  }
  return { payload, decodedBytes: decoded.length };
}

function validateSearchDocument(document, rootCatalogId, catalogById) {
  if (!document || typeof document !== 'object' ||
      typeof document.record_id !== 'string' || !document.record_id ||
      typeof document.title !== 'string' || !document.title.trim() ||
      typeof document.record_type !== 'string' || !document.record_type.trim() ||
      !safeHttps(document.source_url) ||
      !Array.isArray(document.source_urls) || !document.source_urls.length ||
      document.source_urls.some(url => !safeHttps(url)) ||
      !document.source_urls.includes(document.source_url) ||
      typeof document.search_text !== 'string' || !document.search_text.trim() ||
      document.search_text.length > MAX_DOCUMENT_TEXT ||
      document.public_search_allowed !== true ||
      !Array.isArray(document.catalog_ids) || !document.catalog_ids.length ||
      !document.catalog_ids.includes(rootCatalogId)) {
    throw new Error('universal_catalog_search_document_invalid');
  }
  if ('description' in document || 'attributes' in document || 'resources' in document) {
    throw new Error('universal_catalog_search_document_contains_full_content');
  }
  const membershipIds = new Set();
  for (const catalogId of document.catalog_ids) {
    if (typeof catalogId !== 'string' || !catalogId || membershipIds.has(catalogId)) {
      throw new Error('universal_catalog_search_membership_invalid');
    }
    membershipIds.add(catalogId);
    const catalog = catalogById.get(catalogId);
    if (!catalog || catalog.public_search_allowed !== true) {
      throw new Error('universal_catalog_search_membership_not_public');
    }
  }
  return document;
}

async function loadUniversalCatalogFromManifest(manifest, commitSha, manifestRaw, fetchBuffer = getBuffer) {
  if (typeof commitSha !== 'string' || (commitSha !== 'main' && !/^[a-f0-9]{40}$/i.test(commitSha))) {
    throw new Error('universal_catalog_git_ref_invalid');
  }
  if (!Buffer.isBuffer(manifestRaw)) manifestRaw = Buffer.from(manifestRaw || '');
  if (!manifestRaw.length || manifestRaw.length > MAX_MANIFEST_BYTES) {
    throw new Error('universal_catalog_manifest_size_invalid');
  }
  const checked = validateUniversalCatalogManifest(manifest);
  const base = RAW + commitSha + '/data/universal-catalog-indexes/';
  const rootShardOutcomes = await Promise.all(checked.rootFiles.map(async file => {
    const entry = checked.shardByFile.get(file);
    try {
      const compressed = await fetchBuffer(base + file.split('/').map(encodeURIComponent).join('/'), entry.bytes, 20000);
      const decoded = decodeRootShard(entry, compressed, file.slice('shards/'.length, -'.json.gz'.length));
      if (decoded.decodedBytes > MAX_ROOT_SHARD_JSON_BYTES) {
        throw new Error('universal_catalog_root_shard_uncompressed_limit');
      }
      return { ok: true, value: { file, entry, payload: decoded.payload, decodedBytes: decoded.decodedBytes } };
    } catch (error) {
      return { ok: false, file, message: String(error && error.message || error) };
    }
  }));
  const invalidRootShards = rootShardOutcomes.filter(result => !result.ok);
  if (invalidRootShards.length) {
    throw new Error(invalidRootShards[0].message + ':' + invalidRootShards.map(result => result.file).join(','));
  }
  const rootShardResults = rootShardOutcomes.map(result => result.value);

  let totalDecodedBytes = 0;
  let documentOccurrences = 0;
  const documentsById = new Map();
  const indexedRecordIds = new Set();
  const seenCatalogRows = new Set();
  for (const shard of rootShardResults) {
    totalDecodedBytes += shard.decodedBytes;
    if (totalDecodedBytes > MAX_ROOT_TOTAL_JSON_BYTES) {
      throw new Error('universal_catalog_root_total_uncompressed_limit');
    }
    let shardOccurrences = 0;
    for (const row of shard.payload.catalog_indexes) {
      if (!row || typeof row.catalog_id !== 'string' || seenCatalogRows.has(row.catalog_id)) {
        throw new Error('universal_catalog_index_row_duplicate_or_invalid');
      }
      const catalog = checked.catalogById.get(row.catalog_id);
      if (!catalog || catalog.file !== shard.file ||
          row.catalog_level !== catalog.catalog_level ||
          row.public_search_allowed !== catalog.public_search_allowed ||
          !Array.isArray(row.record_ids) ||
          row.record_ids.length !== row.record_count ||
          row.record_count !== catalog.record_count ||
          row.index_sha256 !== catalog.index_sha256) {
        throw new Error('universal_catalog_index_row_mismatch');
      }
      const recordIds = row.record_ids;
      const uniqueRecordIds = new Set(recordIds);
      if (uniqueRecordIds.size !== recordIds.length ||
          recordIds.some((id, index) => typeof id !== 'string' || !id || (index > 0 && recordIds[index - 1] >= id)) ||
          canonicalRecordIdsHash(recordIds).toLowerCase() !== String(row.index_sha256 || '').toLowerCase()) {
        throw new Error('universal_catalog_record_id_integrity_invalid');
      }
      const searchDocuments = row.record_search_documents;
      if (!Array.isArray(searchDocuments) ||
          row.record_search_document_count !== searchDocuments.length) {
        throw new Error('universal_catalog_search_document_count_invalid');
      }
      if (catalog.catalog_level === 'root' && catalog.public_search_allowed === true) {
        if (searchDocuments.length !== recordIds.length) {
          throw new Error('universal_catalog_root_search_document_coverage_invalid');
        }
        const rowDocumentIds = new Set();
        for (const document of searchDocuments) {
          validateSearchDocument(document, catalog.catalog_id, checked.catalogById);
          if (!uniqueRecordIds.has(document.record_id) || rowDocumentIds.has(document.record_id)) {
            throw new Error('universal_catalog_root_search_document_identity_invalid');
          }
          rowDocumentIds.add(document.record_id);
          const prior = documentsById.get(document.record_id);
          if (prior && JSON.stringify(prior) !== JSON.stringify(document)) {
            throw new Error('universal_catalog_duplicate_search_document_conflict');
          }
          if (!prior) documentsById.set(document.record_id, document);
          indexedRecordIds.add(document.record_id);
          documentOccurrences += 1;
          shardOccurrences += 1;
        }
        if (rowDocumentIds.size !== uniqueRecordIds.size) {
          throw new Error('universal_catalog_root_search_document_record_set_mismatch');
        }
      } else if (searchDocuments.length !== 0) {
        throw new Error('universal_catalog_nonroot_search_document_payload_invalid');
      }
      seenCatalogRows.add(row.catalog_id);
    }
    if (shardOccurrences !== shard.entry.record_search_document_occurrences) {
      throw new Error('universal_catalog_shard_document_occurrences_mismatch');
    }
  }

  for (const rootCatalog of checked.rootCatalogs) {
    if (!seenCatalogRows.has(rootCatalog.catalog_id)) {
      throw new Error('universal_catalog_root_index_row_missing');
    }
  }
  if (documentsById.size !== manifest.unique_root_record_search_documents ||
      documentsById.size !== manifest.unique_public_records ||
      documentOccurrences !== manifest.root_record_search_document_occurrences ||
      indexedRecordIds.size !== manifest.unique_public_records) {
    throw new Error('universal_catalog_global_search_document_totals_mismatch');
  }

  return {
    catalogById: checked.catalogById,
    documents: Array.from(documentsById.values()),
    generatedAt: manifest.generated_at || null,
    catalogCount: manifest.catalog_count,
    indexShardCount: manifest.index_shard_count,
    uniquePublicRecords: manifest.unique_public_records,
    publicRecordMemberships: manifest.public_record_memberships,
    rootSearchDocumentCount: documentsById.size,
    rootSearchDocumentOccurrences: documentOccurrences,
    recordsPublished: manifest.records_published,
    productionIndexed: manifest.production_indexed,
    sourcePageAssociations: manifest.source_page_associations || 0,
    manifestSha256: sha256(manifestRaw),
    loadedAt: Date.now(),
    stale: false
  };
}

async function loadUniversalCatalogNetwork(fetchBuffer = getBuffer) {
  const now = Date.now();
  if (cache && now - cache.loadedAt < CACHE_MS) return { ...cache, stale: false };
  if (failureCache && now - failureCache.failedAt < 30000) {
    if (cache && now - cache.loadedAt < STALE_MS) {
      return { ...cache, stale: true, refreshError: failureCache.message };
    }
    throw new Error(failureCache.message);
  }
  if (inFlight) return inFlight;

  inFlight = (async () => {
    try {
      // Avoid the unauthenticated GitHub REST ref endpoint (60 requests/hour).
      // Fetch the branch manifest from raw.githubusercontent.com; every shard is
      // still SHA-256 checked against it, so a moving branch fails closed.
      const manifestRaw = await fetchBuffer(
        RAW + 'main/data/universal-catalog-indexes/manifest.json?v=' + Date.now(),
        MAX_MANIFEST_BYTES,
        8000
      );
      let manifest;
      try { manifest = JSON.parse(manifestRaw.toString('utf8')); } catch {
        throw new Error('universal_catalog_manifest_json_invalid');
      }
      const loaded = await loadUniversalCatalogFromManifest(manifest, 'main', manifestRaw, fetchBuffer);
      cache = loaded;
      failureCache = null;
      return { ...cache, stale: false };
    } catch (error) {
      failureCache = { failedAt: Date.now(), message: String(error && error.message || error).slice(0, 160) };
      if (cache && Date.now() - cache.loadedAt < STALE_MS) {
        return {
          ...cache,
          stale: true,
          refreshError: failureCache.message
        };
      }
      throw error;
    }
  })();

  try {
    return await inFlight;
  } finally {
    inFlight = null;
  }
}
function searchUniversalCatalogs(query, network, limit = 5) {
  const text = String(query || '').trim();
  if (!text || text.length > 160 || !network ||
      !(network.catalogById instanceof Map) || !Array.isArray(network.documents)) return [];
  const terms = Array.from(new Set(tokenize(text))).slice(0, 12);
  if (!terms.length) return [];
  const groups = new Map();

  for (const document of network.documents) {
    const searchable = new Set(tokenize(document.search_text + ' ' + document.title));
    if (!terms.every(term => searchable.has(term))) continue;
    const recordTitleTokens = new Set(tokenize(document.title));
    const candidates = document.catalog_ids
      .map(id => network.catalogById.get(id))
      .filter(catalog => catalog && catalog.public_search_allowed === true);
    if (!candidates.length) continue;

    const scored = candidates.map(catalog => {
      const metadataTokens = new Set(tokenize([
        catalog.title, catalog.subject, catalog.publisher, catalog.geography,
        catalog.subcategory_kind, catalog.subcategory_value, catalog.catalog_type
      ].join(' ')));
      const metadataMatches = terms.filter(term => metadataTokens.has(term)).length;
      const titleMatches = terms.filter(term => recordTitleTokens.has(term)).length;
      const score = metadataMatches * 25 + titleMatches * 15 +
        (catalog.catalog_level === 'subcatalog' ? 20 : 0);
      return { catalog, score };
    }).sort((a, b) => b.score - a.score ||
      (Number(b.catalog.catalog_level === 'subcatalog') - Number(a.catalog.catalog_level === 'subcatalog')) ||
      String(a.catalog.title).localeCompare(String(b.catalog.title)));

    const selected = scored[0];
    if (!selected) continue;
    const catalog = selected.catalog;
    if (!safeHttps(catalog.source_url) && !safeHttps(document.source_url)) continue;
    if (!groups.has(catalog.catalog_id)) {
      groups.set(catalog.catalog_id, { catalog, records: new Map(), score: selected.score });
    }
    const group = groups.get(catalog.catalog_id);
    group.score = Math.max(group.score, selected.score);
    const record = {
      record_id: document.record_id,
      title: document.title,
      record_type: document.record_type,
      source_url: document.source_url,
      source_urls: document.source_urls,
      last_seen_at: document.last_seen_at || null,
      catalog_ids: document.catalog_ids,
      rights_status: document.rights_status || 'not_reviewed',
      source_terms_status: document.source_terms_status || 'not_reviewed',
      attribution_required: document.attribution_required || ''
    };
    group.records.set(document.record_id, record);
  }

  const results = [];
  for (const group of groups.values()) {
    const catalog = group.catalog;
    const matchedRecords = Array.from(group.records.values())
      .sort((a, b) => String(a.title).localeCompare(String(b.title)));
    if (!matchedRecords.length) continue;
    const url = safeHttps(catalog.source_url) || matchedRecords.map(record => safeHttps(record.source_url)).find(Boolean);
    if (!url) continue;
    const restrictiveRecord = matchedRecords.find(record =>
      String(record.rights_status).includes('not_cleared_for_redistribution')) || matchedRecords[0];
    const sourceTermsStatus = matchedRecords.some(record => record.source_terms_status === 'not_reviewed')
      ? 'not_reviewed'
      : (catalog.source_terms_status || matchedRecords[0].source_terms_status);
    results.push({
      catalog_id: catalog.catalog_id,
      catalog_title: catalog.title,
      catalog_type: catalog.catalog_type || 'open_data',
      catalog_level: catalog.catalog_level,
      parent_catalog_id: catalog.parent_catalog_id || '',
      catalog_origin: 'catalog_first_metadata',
      source_kind: 'universal_catalog_index',
      url,
      source_url: url,
      snippet: 'Metadata-only catalog match. Original source records remain authoritative; descriptions and resource files are not republished.',
      record_count: catalog.record_count,
      source_page_count: 0,
      matched_record_count: matchedRecords.length,
      matched_records: matchedRecords.slice(0, 5),
      relevance_class: 'typed_dataset_match',
      relevance_label: catalog.catalog_level === 'subcatalog' ? 'Dataset subcatalog match' : 'Dataset catalog match',
      preview_only: true,
      commercial_eligible: false,
      source_terms_status: sourceTermsStatus,
      record_level_clearance: catalog.record_level_clearance || 'not_reviewed',
      rights_status: restrictiveRecord.rights_status,
      source_terms_url: catalog.source_terms_url || '',
      source_terms_reviewed_at: catalog.source_terms_reviewed_at || '',
      attribution_required: catalog.attribution_required || restrictiveRecord.attribution_required || '',
      publisher: catalog.publisher || '',
      subject: catalog.subject || '',
      geography: catalog.geography || '',
      subcategory_kind: catalog.subcategory_kind || '',
      subcategory_value: catalog.subcategory_value || '',
      _score: group.score
    });
  }
  results.sort((a, b) =>
    (b._score - a._score) ||
    ((b.matched_record_count || 0) - (a.matched_record_count || 0)) ||
    String(a.catalog_title).localeCompare(String(b.catalog_title))
  );
  const max = Number.isSafeInteger(limit) ? Math.max(1, Math.min(5, limit)) : 5;
  return results.slice(0, max).map(({ _score, ...result }) => result);
}

function clearUniversalCatalogCacheForTests() {
  cache = null;
  failureCache = null;
  inFlight = null;
}

module.exports = {
  validateUniversalCatalogManifest,
  loadUniversalCatalogFromManifest,
  loadUniversalCatalogNetwork,
  searchUniversalCatalogs,
  clearUniversalCatalogCacheForTests
};
