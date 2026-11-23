'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const zlib = require('node:zlib');
const {
  validateUniversalCatalogManifest,
  loadUniversalCatalogFromManifest,
  searchUniversalCatalogs
} = require('../src/lib/universal-catalog-search');

const hash = buffer => crypto.createHash('sha256').update(buffer).digest('hex');
const gzip = value => zlib.gzipSync(Buffer.from(JSON.stringify(value), 'utf8'));
const idsHash = ids => hash(Buffer.from(JSON.stringify(ids) + '\n', 'utf8'));

function fixture() {
  const rootA = 'catalog:root-a';
  const rootB = 'catalog:root-b';
  const sub = 'catalog:las-vegas-transit';
  const ids = ['record:las-vegas-transit'];
  const document = {
    record_id: ids[0],
    title: 'Las Vegas public transit routes',
    record_type: 'dataset',
    source_url: 'https://data.example.gov/datasets/las-vegas-transit',
    source_urls: ['https://data.example.gov/datasets/las-vegas-transit'],
    subject: 'Public transportation',
    geography: 'Las Vegas, Nevada',
    search_text: 'Las Vegas public transit routes dataset Clark County Nevada transportation',
    catalog_ids: [rootA, rootB, sub],
    last_seen_at: '2026-11-23T06:00:00Z',
    public_search_allowed: true,
    rights_status: 'publicly_accessible_not_cleared_for_redistribution',
    source_terms_status: 'reviewed_per_dataset',
    attribution_required: 'Source attribution required'
  };
  const rootRow = (catalogId, level, parentId, docs) => ({
    catalog_id: catalogId,
    catalog_level: level,
    parent_catalog_id: parentId,
    public_search_allowed: true,
    record_count: 1,
    record_ids: ids,
    record_search_document_count: docs.length,
    record_search_documents: docs,
    index_sha256: idsHash(ids)
  });
  const rootABytes = gzip({
    schema_version: 1,
    shard_id: 'aa',
    catalog_indexes: [
      rootRow(rootA, 'root', '', [document]),
      rootRow(sub, 'subcatalog', rootA, [])
    ]
  });
  const rootBBytes = gzip({
    schema_version: 1,
    shard_id: 'bb',
    catalog_indexes: [rootRow(rootB, 'root', '', [document])]
  });
  const catalog = (catalogId, title, level, parentId, file, docCount) => ({
    catalog_id: catalogId,
    catalog_level: level,
    parent_catalog_id: parentId,
    title,
    catalog_type: 'open_data',
    subject: 'Transportation',
    publisher: 'Clark County',
    geography: 'Las Vegas, Nevada',
    source_url: 'https://data.example.gov/',
    source_terms_url: 'https://data.example.gov/terms',
    source_terms_status: 'reviewed_per_dataset',
    source_terms_reviewed_at: '2026-11-22T00:00:00Z',
    attribution_required: 'Source attribution required',
    subcategory_kind: level === 'subcatalog' ? 'tag' : '',
    subcategory_value: level === 'subcatalog' ? 'Las Vegas transit' : '',
    record_level_clearance: 'eligible_per_record',
    public_search_allowed: true,
    file,
    record_count: 1,
    staged_record_count: 1,
    record_search_document_count: docCount,
    index_sha256: idsHash(ids)
  });
  const manifest = {
    schema_version: 1,
    record_search_schema_version: 1,
    generated_at: '2026-11-23T07:00:00Z',
    catalog_count: 3,
    index_shard_count: 2,
    unique_public_records: 1,
    unique_root_record_search_documents: 1,
    root_record_search_document_occurrences: 2,
    public_record_memberships: 3,
    restricted_records_not_indexed: 0,
    eligible_records_without_public_index: 0,
    records_published: 0,
    production_indexed: false,
    source_page_associations: 1,
    catalog_indexes: [
      catalog(rootA, 'Open Data Root A', 'root', '', 'shards/aa.json.gz', 1),
      catalog(rootB, 'Open Data Root B', 'root', '', 'shards/bb.json.gz', 1),
      catalog(sub, 'Las Vegas Transit Datasets', 'subcatalog', rootA, 'shards/aa.json.gz', 0)
    ],
    shards: [
      { file: 'shards/aa.json.gz', bytes: rootABytes.length, sha256: hash(rootABytes), catalog_count: 2, record_search_document_occurrences: 1 },
      { file: 'shards/bb.json.gz', bytes: rootBBytes.length, sha256: hash(rootBBytes), catalog_count: 1, record_search_document_occurrences: 1 }
    ]
  };
  const buffers = new Map([
    ['shards/aa.json.gz', rootABytes],
    ['shards/bb.json.gz', rootBBytes]
  ]);
  const fetchCalls = [];
  const fetchBuffer = async (url, maxBytes, timeoutMs) => {
    fetchCalls.push({ url, maxBytes, timeoutMs });
    const file = Array.from(buffers.keys()).find(key => url.endsWith('/' + key));
    if (!file) throw new Error('unexpected_fetch_url');
    return buffers.get(file);
  };
  return { manifest, buffers, fetchBuffer, fetchCalls, document, rootA, rootB, sub };
}

test('validates and loads only the bounded root shards containing public search documents', async () => {
  const f = fixture();
  const checked = validateUniversalCatalogManifest(f.manifest);
  assert.equal(checked.rootCatalogs.length, 2);
  assert.deepEqual(checked.rootFiles.sort(), ['shards/aa.json.gz', 'shards/bb.json.gz']);
  const network = await loadUniversalCatalogFromManifest(
    f.manifest,
    'a'.repeat(40),
    Buffer.from(JSON.stringify(f.manifest)),
    f.fetchBuffer
  );
  assert.ok(f.fetchCalls.length > 0);
  assert.ok(f.fetchCalls.every(call => call.timeoutMs === 20000), 'root shard fetches must use a bounded 20-second timeout');
  assert.equal(network.catalogCount, 3);
  assert.equal(network.uniquePublicRecords, 1);
  assert.equal(network.rootSearchDocumentCount, 1);
  assert.equal(network.rootSearchDocumentOccurrences, 2);
  assert.equal(network.documents.length, 1);
  assert.deepEqual(network.documents[0].catalog_ids, [f.rootA, f.rootB, f.sub]);
});

test('searches metadata-only records and chooses the most relevant subcatalog membership', async () => {
  const f = fixture();
  const network = await loadUniversalCatalogFromManifest(
    f.manifest,
    'a'.repeat(40),
    Buffer.from(JSON.stringify(f.manifest)),
    f.fetchBuffer
  );
  const results = searchUniversalCatalogs('Las Vegas', network, 5);
  assert.equal(results.length, 1);
  assert.equal(results[0].catalog_id, f.sub);
  assert.equal(results[0].catalog_level, 'subcatalog');
  assert.equal(results[0].matched_record_count, 1);
  assert.equal(results[0].matched_records[0].record_id, f.document.record_id);
  assert.equal(results[0].matched_records[0].source_url, f.document.source_url);
  assert.equal(results[0].preview_only, true);
  assert.equal(results[0].commercial_eligible, false);
  assert.equal(results[0].source_kind, 'universal_catalog_index');
  assert.match(results[0].rights_status, /not_cleared_for_redistribution/);
  assert.equal(results[0].source_page_count, 0);
});

test('does not duplicate a canonical record that appears in more than one root catalog', async () => {
  const f = fixture();
  const network = await loadUniversalCatalogFromManifest(
    f.manifest,
    'a'.repeat(40),
    Buffer.from(JSON.stringify(f.manifest)),
    f.fetchBuffer
  );
  assert.equal(network.documents.filter(row => row.record_id === f.document.record_id).length, 1);
  const results = searchUniversalCatalogs('Las Vegas', network, 5);
  assert.equal(results.reduce((sum, row) => sum + row.matched_records.filter(r => r.record_id === f.document.record_id).length, 0), 1);
});

test('returns no record-level catalog cards when the query does not match', async () => {
  const f = fixture();
  const network = await loadUniversalCatalogFromManifest(
    f.manifest,
    'a'.repeat(40),
    Buffer.from(JSON.stringify(f.manifest)),
    f.fetchBuffer
  );
  assert.deepEqual(searchUniversalCatalogs('deep space telescope', network, 5), []);
});

test('rejects manifests without the validated record-search schema', () => {
  const f = fixture();
  const legacy = { ...f.manifest };
  delete legacy.record_search_schema_version;
  assert.throws(() => validateUniversalCatalogManifest(legacy), /schema_missing/);
});

test('rejects a search document that is not public-search-eligible', async () => {
  const f = fixture();
  const payload = JSON.parse(zlib.gunzipSync(f.buffers.get('shards/aa.json.gz')).toString('utf8'));
  payload.catalog_indexes[0].record_search_documents[0].public_search_allowed = false;
  const bytes = gzip(payload);
  f.buffers.set('shards/aa.json.gz', bytes);
  f.manifest.shards[0].bytes = bytes.length;
  f.manifest.shards[0].sha256 = hash(bytes);
  await assert.rejects(
    loadUniversalCatalogFromManifest(f.manifest, 'a'.repeat(40), Buffer.from(JSON.stringify(f.manifest)), f.fetchBuffer),
    /search_document_invalid/
  );
});

test('rejects a root shard with a mismatched SHA-256 digest', async () => {
  const f = fixture();
  f.manifest.shards[0].sha256 = '0'.repeat(64);
  await assert.rejects(
    loadUniversalCatalogFromManifest(f.manifest, 'a'.repeat(40), Buffer.from(JSON.stringify(f.manifest)), f.fetchBuffer),
    /integrity_mismatch/
  );
});
