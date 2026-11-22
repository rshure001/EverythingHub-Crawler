'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const https = require('node:https');

test('curated external catalogs do not bypass the indexed catalog loader', async () => {
  const modulePaths = [
    '../src/lib/catalog',
    '../src/lib/page-derived-catalogs',
    '../src/lib/external-catalogs',
    '../src/lib/search-core'
  ].map((path) => require.resolve(path));
  const originals = modulePaths.map((path) => require.cache[path]);
  const httpsGet = https.get;
  let pageDerivedCalls = 0;
  let externalCalls = 0;

  require.cache[modulePaths[0]] = { id: modulePaths[0], filename: modulePaths[0], loaded: true, exports: {
    loadCatalog: async () => ({ pages: [], indexedPages: 1, updatedAt: 1795290000, sha256: 'a'.repeat(64), source: 'test', integrity: 'sha256', shardCount: 1, transport: 'test', stale: false })
  }};
  require.cache[modulePaths[1]] = { id: modulePaths[1], filename: modulePaths[1], loaded: true, exports: {
    loadPageDerivedCatalogs: async (query) => { pageDerivedCalls++; return { catalogs: [], records: [], generatedAt: '2026-11-21T00:00:00Z', sourceIndexPages: 1, sourceIndexUpdatedAt: '2026-11-21T00:00:00Z', sourceIndexSha256: 'b'.repeat(64), catalogCount: 1, recordCount: 1, metrics: {}, stale: false, query }; },
    searchPageDerivedCatalogs: () => [],
    matchesPageIndexSnapshot: () => true
  }};
  require.cache[modulePaths[2]] = { id: modulePaths[2], filename: modulePaths[2], loaded: true, exports: {
    searchExternalCatalogs: () => { externalCalls++; return [{ catalog_id: 'external:rolex', catalog_title: 'Official Rolex links' }]; },
    getExternalCatalogStats: () => ({ catalogCount: 1, recordCount: 6 })
  }};
  require.cache[modulePaths[3]] = { id: modulePaths[3], filename: modulePaths[3], loaded: true, exports: {
    searchCatalog: () => [],
    parseBingRss: () => [],
    buildResponse: () => ({ status: 'ok', providers: [], results: [], count: 0, totalMatches: 0 })
  }};
  https.get = () => { throw new Error('bing_disabled_for_test'); };

  try {
    const handlerPath = require.resolve('../src/api/search');
    delete require.cache[handlerPath];
    const handler = require(handlerPath);
    const response = {
      headers: {},
      setHeader(key, value) { this.headers[key] = value; },
      status(code) { this.statusCode = code; return this; },
      json(value) { this.body = value; return this; }
    };
    await handler({ method: 'GET', url: '/api/search?q=Rolex&limit=10' }, response);
    assert.equal(response.statusCode, 200);
    assert.equal(pageDerivedCalls, 1, 'query-scoped catalog loader should run');
    assert.equal(externalCalls, 1, 'curated external catalog should still be included');
    assert.equal(response.body.externalCatalogResults[0].catalog_id, 'external:rolex');
    assert.equal(response.body.catalogPreview.status, 'candidate-preview');
  } finally {
    https.get = httpsGet;
    delete require.cache[require.resolve('../src/api/search')];
    modulePaths.forEach((path, index) => {
      if (originals[index]) require.cache[path] = originals[index];
      else delete require.cache[path];
    });
  }
});


test('fallback responses keep curated external catalogs visible when either index loader exceeds its budget', async () => {
  const modulePaths = [
    '../src/lib/catalog',
    '../src/lib/page-derived-catalogs',
    '../src/lib/external-catalogs',
    '../src/lib/search-core'
  ].map((path) => require.resolve(path));
  const originals = modulePaths.map((path) => require.cache[path]);
  const httpsGet = https.get;
  const consoleWarn = console.warn;
  const external = [{ catalog_id: 'external:rolex', catalog_title: 'Official Rolex links', source_kind: 'curated_external_link_directory', preview_only: true, commercial_eligible: false, url: 'https://www.rolex.com/' }];
  require.cache[modulePaths[0]] = { id: modulePaths[0], filename: modulePaths[0], loaded: true, exports: {
    loadCatalog: async () => { throw new Error('catalog_load_budget_exceeded'); }
  }};
  require.cache[modulePaths[1]] = { id: modulePaths[1], filename: modulePaths[1], loaded: true, exports: {
    loadPageDerivedCatalogs: async () => { throw new Error('page_catalog_load_budget_exceeded'); },
    searchPageDerivedCatalogs: () => [],
    matchesPageIndexSnapshot: () => false
  }};
  require.cache[modulePaths[2]] = { id: modulePaths[2], filename: modulePaths[2], loaded: true, exports: {
    searchExternalCatalogs: () => external,
    getExternalCatalogStats: () => ({ catalogCount: 1, recordCount: 6 })
  }};
  require.cache[modulePaths[3]] = { id: modulePaths[3], filename: modulePaths[3], loaded: true, exports: {
    searchCatalog: () => [],
    parseBingRss: () => [],
    buildResponse: () => ({ status: 'ok', providers: [], results: [], count: 0, totalMatches: 0 })
  }};
  https.get = () => { throw new Error('bing_disabled_for_test'); };
  console.warn = () => {};
  try {
    const handlerPath = require.resolve('../src/api/search');
    delete require.cache[handlerPath];
    const handler = require(handlerPath);
    const response = {
      headers: {},
      setHeader(key, value) { this.headers[key] = value; },
      status(code) { this.statusCode = code; return this; },
      json(value) { this.body = value; return this; }
    };
    await handler({ method: 'GET', url: '/api/search?q=Rolex&limit=10' }, response);
    assert.equal(response.statusCode, 200);
    assert.equal(response.body.fallback, true);
    assert.equal(response.body.catalogResults[0].catalog_id, 'external:rolex');
    assert.equal(response.body.externalCatalogResults[0].catalog_id, 'external:rolex');
  } finally {
    https.get = httpsGet;
    console.warn = consoleWarn;
    delete require.cache[require.resolve('../src/api/search')];
    modulePaths.forEach((path, index) => {
      if (originals[index]) require.cache[path] = originals[index];
      else delete require.cache[path];
    });
  }
});


test('broad-query fallback surfaces provenance-backed page-derived catalog records as preview results', async () => {
  const modulePaths = [
    '../src/lib/catalog',
    '../src/lib/page-derived-catalogs',
    '../src/lib/external-catalogs',
    '../src/lib/search-core'
  ].map((path) => require.resolve(path));
  const originals = modulePaths.map((path) => require.cache[path]);
  const httpsGet = https.get;
  const consoleWarn = console.warn;
  const pageCard = {
    catalog_id: 'page:global-place-directory',
    catalog_title: 'Global Place Directory',
    title: 'Global Place Directory',
    url: 'https://example.org/places',
    snippet: 'Candidate catalog preview for Las Vegas. Preview only.',
    relevance_class: 'typed_location_match',
    relevance_label: 'Location match',
    source_kind: 'page_derived',
    preview_only: true,
    source_terms_status: 'not_reviewed',
    source_policy_review_status: 'not_reviewed',
    rights_status: 'publicly_accessible_not_cleared_for_redistribution',
    matched_records: [{
      record_id: 'place:las-vegas',
      record_type: 'place',
      title: 'Las Vegas',
      source_url: 'https://example.org/places/las-vegas',
      source_policy_review_status: 'not_reviewed',
      record_level_clearance: 'not_reviewed'
    }]
  };
  require.cache[modulePaths[0]] = { id: modulePaths[0], filename: modulePaths[0], loaded: true, exports: {
    loadCatalog: async () => { throw new Error('query_index_too_broad'); }
  }};
  require.cache[modulePaths[1]] = { id: modulePaths[1], filename: modulePaths[1], loaded: true, exports: {
    loadPageDerivedCatalogs: async (query) => ({
      catalogs: [pageCard], records: pageCard.matched_records,
      generatedAt: '2026-11-22T00:00:00Z',
      sourceIndexPages: 188052,
      sourceIndexUpdatedAt: '2026-11-22T00:00:00Z',
      sourceIndexSha256: 'b'.repeat(64),
      catalogCount: 1, recordCount: 1, metrics: {}, stale: false, query
    }),
    searchPageDerivedCatalogs: () => [pageCard],
    matchesPageIndexSnapshot: () => true
  }};
  require.cache[modulePaths[2]] = { id: modulePaths[2], filename: modulePaths[2], loaded: true, exports: {
    searchExternalCatalogs: () => [],
    getExternalCatalogStats: () => ({ catalogCount: 0, recordCount: 0 })
  }};
  require.cache[modulePaths[3]] = { id: modulePaths[3], filename: modulePaths[3], loaded: true, exports: {
    searchCatalog: () => [],
    parseBingRss: () => [],
    buildResponse: () => ({ status: 'ok', providers: [], results: [], count: 0, totalMatches: 0 })
  }};
  https.get = () => { throw new Error('bing_disabled_for_test'); };
  console.warn = () => {};
  try {
    const handlerPath = require.resolve('../src/api/search');
    delete require.cache[handlerPath];
    const handler = require(handlerPath);
    const response = {
      headers: {},
      setHeader(key, value) { this.headers[key] = value; },
      status(code) { this.statusCode = code; return this; },
      json(value) { this.body = value; return this; }
    };
    await handler({ method: 'GET', url: '/api/search?q=Las%20Vegas&limit=10' }, response);
    assert.equal(response.statusCode, 200);
    assert.equal(response.body.indexStatus, 'catalog-preview-fallback');
    assert.equal(response.body.indexScan.mode, 'catalog-preview-fallback');
    assert.equal(response.body.indexScan.error, 'query_index_too_broad');
    assert.equal(response.body.results[0].title, 'Las Vegas');
    assert.equal(response.body.results[0].url, 'https://example.org/places/las-vegas');
    assert.equal(response.body.results[0].preview_only, true);
    assert.equal(response.body.results[0].commercial_eligible, false);
    assert.equal(response.body.catalogResults[0].catalog_id, 'page:global-place-directory');
    assert.equal(response.body.catalogPreview.recordPreviewResults, 1);
  } finally {
    https.get = httpsGet;
    console.warn = consoleWarn;
    delete require.cache[require.resolve('../src/api/search')];
    modulePaths.forEach((path, index) => {
      if (originals[index]) require.cache[path] = originals[index];
      else delete require.cache[path];
    });
  }
});
