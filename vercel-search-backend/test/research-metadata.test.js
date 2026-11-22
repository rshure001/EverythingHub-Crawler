'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {
  mapOpenAlexItem,
  mapCrossrefItem,
  mapDataCiteItem,
  mergeResearchResults,
  providerUrls
} = require('../src/lib/research-metadata');

test('OpenAlex mapping preserves CC0 metadata and linked-resource license separately', () => {
  const item = mapOpenAlexItem({
    id: 'https://openalex.org/W123456789',
    doi: 'https://doi.org/10.5555/OCEAN.1',
    display_name: 'Ocean temperature observations',
    publication_year: 2026,
    type: 'dataset',
    authorships: [{ author: { display_name: 'Research Team' } }],
    primary_location: {
      source: { display_name: 'Ocean Data Journal' },
      license: 'cc-by'
    },
    abstract_inverted_index: { copyrighted: [0] }
  });
  assert.equal(item.doi, '10.5555/ocean.1');
  assert.equal(item.title, 'Ocean temperature observations');
  assert.equal(item.url, 'https://doi.org/10.5555/ocean.1');
  assert.equal(item.metadataLicense, 'CC0 1.0 OpenAlex metadata; linked works retain their own resource rights');
  assert.equal(item.resourceRights[0].label, 'cc-by');
  assert.equal(item.abstractIncluded, false);
  assert.equal('abstract' in item, false);
});

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
  const openAlex = [{ id: 'openalex:W1', doi: '10.1234/abc', source: 'OpenAlex' }, { id: 'openalex:W2', doi: null, source: 'OpenAlex' }];
  const merged = mergeResearchResults(openAlex, crossref, dataCite, 10);
  assert.deepEqual(merged.map(item => item.doi), ['10.1234/abc', null, '10.1234/def', '10.1234/xyz']);
  assert.equal(merged.length, 4);
});

test('provider URLs use bounded query lengths and omit abstract fields', () => {
  const urls = providerUrls('ocean temperature');
  assert.match(urls.openAlex, /api\.openalex\.org\/works/);
  assert.match(urls.openAlex, /per-page=10/);
  assert.match(urls.crossref, /api\.crossref\.org\/works/);
  assert.match(urls.crossref, /query\.bibliographic=ocean\+temperature/);
  assert.match(urls.crossref, /rows=10/);
  assert.doesNotMatch(urls.crossref, /abstract/);
  assert.match(urls.dataCite, /api\.datacite\.org\/dois/);
  assert.match(urls.dataCite, /page%5Bsize%5D=10/);
  assert.doesNotMatch(urls.dataCite, /descriptions/);
});
