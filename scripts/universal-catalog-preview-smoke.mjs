import assert from 'node:assert/strict';

const base = process.env.EH_BACKEND_URL;
const key = process.env.EH_PUBLISHABLE_KEY;
assert.ok(base, 'EH_BACKEND_URL is required');
assert.ok(key, 'EH_PUBLISHABLE_KEY is required');
const origin = new URL(base);
assert.equal(origin.protocol, 'https:', 'first-party API must use HTTPS');

async function getSearch(query) {
  const url = new URL('everything-hub-catalog-search', origin.href.endsWith('/') ? origin.href : origin.href + '/');
  url.searchParams.set('q', query);
  url.searchParams.set('limit', '20');
  const response = await fetch(url, {
    headers: { accept: 'application/json', apikey: key },
    signal: AbortSignal.timeout(45000),
  });
  const text = await response.text();
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    throw new Error(`Expected JSON for ${query}; HTTP ${response.status}; body: ${text.slice(0, 300)}`);
  }
  assert.equal(response.status, 200, `${query} returned HTTP ${response.status}: ${text.slice(0, 500)}`);
  return data;
}

let totalCatalogResults = 0;

for (const query of ['NASA', 'Las Vegas', 'Rolex']) {
  const data = await getSearch(query);
  assert.equal(data.query, query);
  assert.ok(Array.isArray(data.catalogResults), 'catalogResults must be an array on normal search responses');
  const preview = data.catalogPreview;
  assert.ok(preview && typeof preview === 'object', 'catalog preview metadata is required on normal search responses');
  assert.ok(['candidate-preview', 'stale-candidate-preview', 'query-derived-candidate-preview'].includes(preview.status),
    `candidate catalog snapshot unavailable: ${preview.status || preview.error || 'missing status'}`);
  assert.equal(preview.previewOnly, true);
  assert.ok(Number.isInteger(preview.pageIndexPages) && preview.pageIndexPages > 0,
    'catalog preview must identify the committed page-index size');
  assert.ok(typeof preview.pageIndexUpdatedAt === 'string' && preview.pageIndexUpdatedAt,
    'catalog preview must identify the committed page-index timestamp');
  assert.ok(Number(preview.catalogCount) > 0, 'catalog definitions must be loaded');
  assert.ok(Number(preview.recordCount) > 0, 'catalog records must be loaded');
  assert.equal(Number(preview.recordsPublished), 0, 'candidate records must remain unpublished');
  assert.equal(Number(preview.sourceTermsReviewedRecords), 0, 'unreviewed source records must not be labeled rights-reviewed');
  assert.equal(preview.resultCount, data.catalogResults.length);
  totalCatalogResults += data.catalogResults.length;
  for (const item of data.catalogResults) {
    assert.equal(item.preview_only, true);
    assert.equal(item.commercial_eligible, false);
    assert.equal(item.source_terms_status, 'not_reviewed');
    assert.equal(item.record_level_clearance, 'not_reviewed');
    assert.ok(item.catalog_id && item.title && item.url?.startsWith('https://'));
    for (const record of item.matched_records || []) {
      assert.ok(record.record_id && record.title && record.source_url?.startsWith('https://'));
    }
  }
  console.log(JSON.stringify({
    query,
    catalogResults: data.catalogResults.length,
    catalogCount: preview.catalogCount,
    recordCount: preview.recordCount,
    pageIndexPages: preview.pageIndexPages,
    freshness: preview.status,
    recordsPublished: preview.recordsPublished,
  }));
}
assert.ok(totalCatalogResults > 0, 'the candidate catalog search must return at least one relevant result across the smoke set');
console.log('PASS: isolated first-party catalog search exposes integrity-checked candidate catalog previews.');
