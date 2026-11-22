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
  const pageSearchQueries = [];

  require.cache[modulePaths[0]] = { id: modulePaths[0], filename: modulePaths[0], loaded: true, exports: {
    loadCatalog: async (query) => { pageSearchQueries.push(query); return { pages: [], indexedPages: 100, candidatePages: 2, candidateShardCount: 1, candidateShardTotal: 1, candidateShardsTruncated: false, queryIndexTermsRequested: 1, queryIndexTermsMatched: 1, updatedAt: 1795290000, sha256: 'a'.repeat(64), source: 'test', integrity: 'sha256', shardCount: 10, transport: 'query-indexed-shards', stale: false }; }
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
    assert.deepEqual(pageSearchQueries, ['Rolex'], 'public page index must be loaded for the requested query');
    assert.equal(response.body.matchScope, 'query-index-candidate-shards');
    assert.equal(response.body.indexedPages, 100);
    assert.equal(response.body.candidatePages, 2);
    assert.equal(response.body.candidateShardCount, 1);
    assert.equal(response.body.candidateShardsTruncated, false);
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
