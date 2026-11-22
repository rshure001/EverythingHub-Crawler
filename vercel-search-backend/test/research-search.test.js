'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const https = require('node:https');

test('research lane federates Crossref and DataCite records into visible search results', async () => {
  const modulePaths = [
    '../src/lib/catalog',
    '../src/lib/page-derived-catalogs',
    '../src/lib/external-catalogs',
    '../src/lib/search-core',
    '../src/lib/research-metadata'
  ].map(path => require.resolve(path));
  const originals = modulePaths.map(path => require.cache[path]);
  const httpsGet = https.get;
  let researchCalls = 0;

  require.cache[modulePaths[0]] = { id: modulePaths[0], filename: modulePaths[0], loaded: true, exports: {
    loadCatalog: async () => ({ pages: [], indexedPages: 1, updatedAt: 1795290000, sha256: 'a'.repeat(64), source: 'test', integrity: 'sha256', shardCount: 1, transport: 'test', stale: false })
  }};
  require.cache[modulePaths[1]] = { id: modulePaths[1], filename: modulePaths[1], loaded: true, exports: {
    loadPageDerivedCatalogs: async () => ({ catalogs: [], records: [], generatedAt: '2026-11-21T00:00:00Z', sourceIndexPages: 1, sourceIndexUpdatedAt: '2026-11-21T00:00:00Z', sourceIndexSha256: 'b'.repeat(64), catalogCount: 0, recordCount: 0, metrics: {}, stale: false }),
    searchPageDerivedCatalogs: () => [],
    matchesPageIndexSnapshot: () => true
  }};
  require.cache[modulePaths[2]] = { id: modulePaths[2], filename: modulePaths[2], loaded: true, exports: {
    searchExternalCatalogs: () => [],
    getExternalCatalogStats: () => ({ catalogCount: 0, recordCount: 0 })
  }};
  require.cache[modulePaths[3]] = { id: modulePaths[3], filename: modulePaths[3], loaded: true, exports: {
    searchCatalog: () => [],
    parseBingRss: () => [],
    buildResponse: () => ({ status: 'ok', providers: [], results: [], count: 0, totalMatches: 0, hasMore: false })
  }};
  require.cache[modulePaths[4]] = { id: modulePaths[4], filename: modulePaths[4], loaded: true, exports: {
    loadResearchMetadata: async query => {
      researchCalls++;
      assert.equal(query, 'ocean temperature');
      return [
        { doi: '10.0/openalex', title: 'Ocean temperature study', url: 'https://doi.org/10.0/openalex', source: 'OpenAlex', provider: 'openalex-metadata', mediaType: 'research' },
        { doi: '10.1/crossref', title: 'Ocean temperatures', url: 'https://doi.org/10.1/crossref', source: 'Crossref', provider: 'crossref-metadata', mediaType: 'research' },
        { doi: '10.2/datacite', title: 'Ocean temperature dataset', url: 'https://doi.org/10.2/datacite', source: 'DataCite', provider: 'datacite-metadata', mediaType: 'research' }
      ];
    }
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
    await handler({ method: 'GET', url: '/api/search?q=ocean%20temperature&lane=research&limit=10' }, response);
    assert.equal(response.statusCode, 200);
    assert.equal(researchCalls, 1);
    assert.equal(response.body.researchResults.length, 3);
    assert.deepEqual(response.body.results.map(item => item.provider), ['openalex-metadata', 'crossref-metadata', 'datacite-metadata']);
    assert.ok(response.body.providers.includes('OpenAlex'));
    assert.ok(response.body.providers.includes('Crossref'));
    assert.ok(response.body.providers.includes('DataCite'));
  } finally {
    https.get = httpsGet;
    delete require.cache[require.resolve('../src/api/search')];
    modulePaths.forEach((path, index) => {
      if (originals[index]) require.cache[path] = originals[index];
      else delete require.cache[path];
    });
  }
});
