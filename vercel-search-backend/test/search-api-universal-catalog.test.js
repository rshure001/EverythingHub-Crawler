'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const https = require('node:https');

test('search API exposes validated universal catalog record matches as preview cards', async () => {
  const modulePaths = [
    '../src/lib/catalog',
    '../src/lib/page-derived-catalogs',
    '../src/lib/external-catalogs',
    '../src/lib/search-core',
    '../src/lib/universal-catalog-search'
  ].map(path => require.resolve(path));
  const originals = modulePaths.map(path => require.cache[path]);
  const handlerPath = require.resolve('../src/api/search');
  const originalHandler = require.cache[handlerPath];
  const originalHttpsGet = https.get;

  const universalCard = {
    catalog_id: 'catalog:las-vegas-transit',
    catalog_title: 'Las Vegas Transit Datasets',
    catalog_type: 'open_data',
    catalog_level: 'subcatalog',
    parent_catalog_id: 'catalog:open-data',
    url: 'https://data.example.gov/',
    source_url: 'https://data.example.gov/',
    snippet: 'Metadata-only catalog match.',
    record_count: 12,
    source_page_count: 0,
    matched_record_count: 1,
    matched_records: [{
      record_id: 'record:las-vegas-transit',
      title: 'Las Vegas public transit routes',
      record_type: 'dataset',
      source_url: 'https://data.example.gov/datasets/las-vegas-transit',
      catalog_ids: ['catalog:open-data', 'catalog:las-vegas-transit']
    }],
    relevance_class: 'typed_dataset_match',
    relevance_label: 'Dataset subcatalog match',
    preview_only: true,
    commercial_eligible: false,
    source_terms_status: 'reviewed_per_dataset',
    record_level_clearance: 'eligible_per_record',
    rights_status: 'publicly_accessible_not_cleared_for_redistribution',
    source_kind: 'universal_catalog_index'
  };
  const network = {
    catalogById: new Map(),
    documents: [],
    generatedAt: '2026-11-23T07:00:00Z',
    catalogCount: 2,
    uniquePublicRecords: 12,
    rootSearchDocumentCount: 12,
    rootSearchDocumentOccurrences: 12,
    publicRecordMemberships: 18,
    recordsPublished: 0,
    productionIndexed: false,
    indexShardCount: 2,
    manifestSha256: 'c'.repeat(64),
    stale: false
  };

  require.cache[modulePaths[0]] = {
    id: modulePaths[0], filename: modulePaths[0], loaded: true,
    exports: {
      loadCatalog: async () => ({
        pages: [], indexedPages: 1, updatedAt: 1795290000,
        sha256: 'a'.repeat(64), source: 'test', integrity: 'sha256',
        shardCount: 1, transport: 'test', stale: false
      })
    }
  };
  require.cache[modulePaths[1]] = {
    id: modulePaths[1], filename: modulePaths[1], loaded: true,
    exports: {
      loadPageDerivedCatalogs: async () => ({
        catalogs: [], records: [], generatedAt: '2026-11-23T07:00:00Z',
        sourceIndexPages: 1, sourceIndexUpdatedAt: '2026-11-23T07:00:00Z',
        sourceIndexSha256: 'b'.repeat(64), catalogCount: 1, recordCount: 1,
        metrics: { records_published: 0, source_terms_reviewed_records: 0 },
        stale: false
      }),
      searchPageDerivedCatalogs: () => [],
      matchesPageIndexSnapshot: () => true
    }
  };
  require.cache[modulePaths[2]] = {
    id: modulePaths[2], filename: modulePaths[2], loaded: true,
    exports: {
      searchExternalCatalogs: () => [],
      getExternalCatalogStats: () => ({ catalogCount: 0, recordCount: 0 })
    }
  };
  require.cache[modulePaths[3]] = {
    id: modulePaths[3], filename: modulePaths[3], loaded: true,
    exports: {
      searchCatalog: () => [],
      resolveLaneFilters: lane => lane === 'shopping' ? new Set(['business']) : new Set(),
      parseBingRss: () => [],
      buildResponse: () => ({ status: 'ok', providers: [], results: [], count: 0, totalMatches: 0 })
    }
  };
  require.cache[modulePaths[4]] = {
    id: modulePaths[4], filename: modulePaths[4], loaded: true,
    exports: {
      loadUniversalCatalogNetwork: async () => network,
      searchUniversalCatalogs: query => query === 'Las Vegas' ? [universalCard] : []
    }
  };
  https.get = () => { throw new Error('bing_disabled_for_test'); };

  try {
    delete require.cache[handlerPath];
    const handler = require('../src/api/search');
    const response = {
      headers: {},
      setHeader(key, value) { this.headers[key] = value; },
      status(code) { this.statusCode = code; return this; },
      json(value) { this.body = value; return this; }
    };
    await handler({ method: 'GET', url: '/api/search?q=Las%20Vegas&limit=10&lane=shopping' }, response);
    assert.equal(response.statusCode, 200);
    assert.equal(response.body.catalogResults.length, 1);
    assert.equal(response.body.catalogResults[0].catalog_id, 'catalog:las-vegas-transit');
    assert.equal(response.body.catalogResults[0].preview_only, true);
    assert.equal(response.body.catalogResults[0].commercial_eligible, false);
    assert.equal(response.body.universalCatalogPreview.status, 'candidate-preview');
    assert.equal(response.body.universalCatalogPreview.recordCount, 12);
    assert.equal(response.body.universalCatalogPreview.recordsPublished, 0);
    assert.equal(response.body.universalCatalogPreview.productionIndexed, false);
    assert.ok(response.body.providers.includes('Everything Hub Universal Catalog Preview'));
  } finally {
    https.get = originalHttpsGet;
    delete require.cache[handlerPath];
    if (originalHandler) require.cache[handlerPath] = originalHandler;
    modulePaths.forEach((path, index) => {
      if (originals[index]) require.cache[path] = originals[index];
      else delete require.cache[path];
    });
  }
});
