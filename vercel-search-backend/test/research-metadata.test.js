'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {
  mapCrossrefItem,
  mapDataCiteItem,
  mergeResearchResults,
  providerUrls
} = require('../src/lib/research-metadata');

test('Crossref mapping returns bibliographic facts and deliberately excludes abstracts', () => {
  const item = mapCrossrefItem({
    DOI: '10.5555/Example.1',
    title: ['A study of ocean currents'],
    author: [{ given: 'Ada', family: 'Lovelace' }],
    publisher: 'Example Publisher',
    type: 'journal-article',
    published: { 'date-parts': [[2025, 4, 1]] },
    abstract: '<jats:p>Copyrighted abstract must not be copied.</jats:p>',
    license: [{ URL: 'https://creativecommons.org/licenses/by/4.0/', 'content-version': 'vor' }]
  });
  assert.equal(item.doi, '10.5555/example.1');
  assert.equal(item.title, 'A study of ocean currents');
  assert.equal(item.year, '2025');
  assert.equal(item.authors[0], 'Ada Lovelace');
  assert.equal(item.url, 'https://doi.org/10.5555/example.1');
  assert.equal(item.abstractIncluded, false);
  assert.equal('abstract' in item, false);
  assert.equal(item.rights[0].url, 'https://creativecommons.org/licenses/by/4.0/');
});

test('DataCite mapping preserves per-resource rights without copying descriptions', () => {
  const item = mapDataCiteItem({
    id: '10.9999/DATA.2',
    attributes: {
      doi: '10.9999/DATA.2',
      titles: [{ title: 'Arctic sea ice observations' }],
      creators: [{ name: 'Research Team' }],
      publisher: 'Polar Institute',
      publicationYear: 2024,
      types: { resourceTypeGeneral: 'Dataset' },
      rightsList: [{ rights: 'CC BY 4.0', rightsUri: 'https://creativecommons.org/licenses/by/4.0/' }],
      descriptions: [{ description: 'Do not include this long description in search metadata.', descriptionType: 'Abstract' }]
    }
  });
  assert.equal(item.doi, '10.9999/data.2');
  assert.equal(item.title, 'Arctic sea ice observations');
  assert.equal(item.resourceType, 'Dataset');
  assert.equal(item.rights[0].label, 'CC BY 4.0');
  assert.equal(item.abstractIncluded, false);
  assert.equal('description' in item, false);
});

test('federated results deduplicate by DOI across providers', () => {
  const crossref = [{ doi: '10.1234/ABC', source: 'Crossref' }, { doi: '10.1234/DEF', source: 'Crossref' }];
  const dataCite = [{ doi: '10.1234/abc', source: 'DataCite' }, { doi: '10.1234/XYZ', source: 'DataCite' }];
  assert.deepEqual(
    mergeResearchResults(crossref, dataCite, 10).map(item => item.doi),
    ['10.1234/abc', '10.1234/def', '10.1234/xyz']
  );
});

test('provider URLs use bounded query lengths and omit abstract fields', () => {
  const urls = providerUrls('ocean temperature');
  assert.match(urls.crossref, /api\.crossref\.org\/works/);
  assert.match(urls.crossref, /query\.bibliographic=ocean\+temperature/);
  assert.match(urls.crossref, /rows=10/);
  assert.doesNotMatch(urls.crossref, /abstract/);
  assert.match(urls.dataCite, /api\.datacite\.org\/dois/);
  assert.match(urls.dataCite, /page%5Bsize%5D=10/);
  assert.doesNotMatch(urls.dataCite, /descriptions/);
});
