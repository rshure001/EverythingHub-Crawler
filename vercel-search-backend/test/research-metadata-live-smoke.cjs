'use strict';
const assert = require('node:assert/strict');
const { loadResearchMetadata } = require('../src/lib/research-metadata');

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
  assert.ok(sources.has('Crossref'), 'Crossref public API returned no mapped metadata');
  assert.ok(sources.has('DataCite'), 'DataCite public API returned no mapped metadata');
  assert.ok(rows.every(item => item.abstractIncluded === false), 'abstracts must remain excluded');
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
