import assert from 'node:assert/strict';

const base = process.env.EH_BACKEND_PREVIEW_URL;
assert.ok(base, 'EH_BACKEND_PREVIEW_URL is required');
const origin = new URL(base);
assert.equal(origin.protocol, 'https:', 'preview must use HTTPS');

async function getJson(path) {
  const response = await fetch(new URL(path, origin), {
    headers: { accept: 'application/json' },
    signal: AbortSignal.timeout(45000)
  });
  const text = await response.text();
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    throw new Error(`Expected JSON from ${path}; HTTP ${response.status}; body: ${text.slice(0, 300)}`);
  }
  assert.equal(response.status, 200, `${path} returned HTTP ${response.status}`);
  return data;
}

const health = await getJson('/api/health');
assert.equal(health.status, 'ok');
assert.equal(health.database_required, false);
console.log('health:', JSON.stringify({ status: health.status, database_required: health.database_required }));

const expectations = [
  { query: 'NASA', requireUniversalMatch: true },
  { query: 'Las Vegas', requireUniversalMatch: true },
  { query: 'Rolex', requireUniversalMatch: false }
];

for (const item of expectations) {
  const url = new URL('/api/search', origin);
  url.searchParams.set('q', item.query);
  url.searchParams.set('limit', '20');
  const data = await getJson(url.pathname + url.search);

  assert.equal(data.query, item.query);
  assert.ok(Array.isArray(data.results), 'ordinary search results must be an array');
  assert.ok(Array.isArray(data.catalogResults), 'catalog results must be an array');
  assert.ok(Array.isArray(data.externalCatalogResults), 'external catalog results must be an array');

  const preview = data.universalCatalogPreview;
  assert.ok(preview && typeof preview === 'object', 'universal catalog preview metadata is required');
  assert.ok(
    ['candidate-preview', 'stale-candidate-preview'].includes(preview.status),
    `universal catalog index did not load: ${preview.status} ${preview.error || ''}`
  );
  assert.equal(preview.previewOnly, true);
  assert.ok(Number(preview.catalogCount) >= 2, 'expected root catalog definitions');
  assert.ok(Number(preview.recordCount) > 0, 'expected rights-filtered metadata records');
  assert.equal(Number(preview.recordsPublished), 0, 'preview records must not be labeled published');
  assert.equal(preview.productionIndexed, false, 'preview records must not be labeled production-indexed');

  const universal = data.catalogResults.filter(result => result.source_kind === 'universal_catalog_index');
  for (const result of universal) {
    assert.equal(result.preview_only, true);
    assert.equal(result.commercial_eligible, false);
    assert.equal(result.relevance_class, 'typed_dataset_match');
    assert.ok(result.url.startsWith('https://'));
    assert.ok(Array.isArray(result.matched_records) && result.matched_records.length > 0);
    for (const record of result.matched_records) {
      assert.ok(record.record_id, 'canonical record ID is required');
      assert.ok(record.source_url.startsWith('https://'), 'HTTPS provenance is required');
      assert.ok(!Object.hasOwn(record, 'description'), 'record descriptions must not be copied into search cards');
      assert.ok(!Object.hasOwn(record, 'attributes'), 'full record attributes must not be copied into search cards');
    }
  }

  if (item.requireUniversalMatch) {
    assert.ok(
      universal.length > 0,
      `${item.query} returned no typed universal catalog record results`
    );
  }

  if (item.query === 'Rolex') {
    assert.ok(
      data.externalCatalogResults.some(result => /rolex/i.test(result.catalog_title || result.title || '')),
      'Rolex must retain its curated official external directory'
    );
  }

  console.log(JSON.stringify({
    query: item.query,
    ordinaryResults: data.results.length,
    catalogResults: data.catalogResults.length,
    universalCatalogResults: universal.length,
    universalCatalogTitles: universal.map(result => result.catalog_title),
    externalCatalogResults: data.externalCatalogResults.length,
    previewStatus: preview.status,
    catalogCount: preview.catalogCount,
    recordCount: preview.recordCount,
    recordsPublished: preview.recordsPublished,
    productionIndexed: preview.productionIndexed
  }));
}
