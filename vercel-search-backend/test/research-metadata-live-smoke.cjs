'use strict';
const assert = require('node:assert/strict');
const { loadResearchMetadata, providerUrls, _test } = require('../src/lib/research-metadata');

(async () => {
  const rows = await loadResearchMetadata('climate change');
  const sources = new Set(rows.map(item => item.source));
  const summary = {
    resultCount: rows.length,
    sources: [...sources],
    sample: rows.slice(0, 4).map(item => ({
      source: item.source,
      doi: item.doi,
      title: item.title,
      abstractIncluded: item.abstractIncluded
    }))
  };
  console.log(JSON.stringify(summary, null, 2));
  let openAlexRateLimited = false;
  if (!sources.has('OpenAlex')) {
    try {
      const payload = await _test.requestJson(providerUrls('climate change').openAlex, 'openalex');
      console.log('OpenAlex diagnostic payload keys:', Object.keys(payload || {}));
    } catch (error) {
      openAlexRateLimited = /openalex_http_429/.test(error.message);
      console.warn('OpenAlex live status:', openAlexRateLimited ? 'public API rate-limited this shared runner IP (HTTP 429)' : error.message);
    }
  }
  let crossrefRateLimited = false;
  if (!sources.has('Crossref')) {
    try {
      const payload = await _test.requestJson(providerUrls('climate change').crossref, 'crossref');
      console.log('Crossref diagnostic payload keys:', Object.keys(payload || {}));
      console.log('Crossref diagnostic message keys:', Object.keys(payload && payload.message || {}));
    } catch (error) {
      crossrefRateLimited = /crossref_http_429/.test(error.message);
      console.warn('Crossref live status:', crossrefRateLimited ? 'public pool rate-limited this shared runner IP (HTTP 429)' : error.message);
    }
  }
  assert.ok(sources.has('OpenAlex') || openAlexRateLimited, 'OpenAlex public API returned no mapped metadata');
  assert.ok(sources.has('Crossref') || crossrefRateLimited, 'Crossref failed for a reason other than a documented public-pool rate limit');
  assert.ok(sources.has('DataCite'), 'DataCite public API returned no mapped metadata');
  assert.ok(rows.every(item => item.abstractIncluded === false), 'abstracts must remain excluded');
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
