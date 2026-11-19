'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const zlib = require('node:zlib');
const { searchCatalog, parseBingRss, buildResponse } = require('../src/lib/search-core');
const { MAX_CATALOG_GZIP_BYTES, validateManifestAndCatalog } = require('../src/lib/catalog');

function makePage(index, lane) {
  return {
    url: 'https://example.com/nasa/' + index,
    title: 'NASA mission ' + index,
    description: 'NASA science research mission',
    text: 'NASA exploration and science data',
    lane: lane || 'science'
  };
}
function makeManifest(pages) {
  const compressed = zlib.gzipSync(Buffer.from(JSON.stringify({ version: 1, pages })));
  return {
    manifest: {
      version: 1,
      integrity: 'sha256',
      updated_at: Math.floor(Date.now() / 1000),
      total_pages: pages.length,
      shard_count: 1,
      shards: [{ file: 'shard-0001.json' }],
      catalog_gzip: {
        file: 'catalog-v1.json.gz',
        pages: pages.length,
        bytes: compressed.length,
        sha256: crypto.createHash('sha256').update(compressed).digest('hex')
      }
    },
    compressed
  };
}

test('search ranks title matches and filters the requested lane', () => {
  const pages = [
    makePage(1, 'science'),
    { ...makePage(2, 'news'), title: 'General science bulletin', description: 'NASA mention' },
    { ...makePage(3, 'business'), title: 'Space business' }
  ];
  const results = searchCatalog(pages, 'NASA mission', 'science');
  assert.equal(results.length, 1);
  assert.equal(results[0].url, 'https://example.com/nasa/1');
  assert.equal(results[0].provider, 'everythinghub-catalog');
});

test('first and later pages do not repeat crawler results', () => {
  const ranked = Array.from({ length: 120 }, (_, index) => ({
    url: 'https://example.com/result/' + index,
    title: 'NASA result ' + index,
    source: 'Everything Hub Crawler',
    origin: 'Everything Hub Index',
    mediaType: 'web',
    provider: 'everythinghub-catalog',
    lane: 'science',
    score: 100 - index
  }));
  const bing = Array.from({ length: 10 }, (_, index) => ({
    url: 'https://bing.example/result/' + index,
    title: 'Bing result ' + index,
    source: 'Bing Web Search',
    origin: 'Bing',
    mediaType: 'web',
    provider: 'bing-rss'
  }));
  const common = {
    query: 'NASA', limit: 50, ranked, bingResults: bing,
    indexedPages: 13727, indexUpdatedAt: 1795080303, indexSha256: 'a'.repeat(64)
  };
  const first = buildResponse({ ...common, offset: 0 });
  const second = buildResponse({ ...common, offset: 50 });
  const third = buildResponse({ ...common, offset: 100 });
  const firstCrawler = first.results.filter(item => item.provider === 'everythinghub-catalog');
  const secondCrawler = second.results.filter(item => item.provider === 'everythinghub-catalog');
  const thirdCrawler = third.results.filter(item => item.provider === 'everythinghub-catalog');
  const firstUrls = new Set(firstCrawler.map(item => item.url));
  const secondUrls = new Set(secondCrawler.map(item => item.url));
  assert.equal(first.results.length, 50);
  assert.equal(firstCrawler.length, 40);
  assert.equal(second.page, 2);
  assert.equal(secondCrawler.length, 50);
  assert.equal([...firstUrls].filter(url => secondUrls.has(url)).length, 0);
  assert.equal(third.page, 3);
  assert.equal(thirdCrawler.length, 30);
  assert.equal(third.hasMore, false);
  assert.equal(first.indexedPages, 13727);
});

test('Bing RSS parser decodes entities and keeps only safe HTTP links', () => {
  const xml = '<rss><channel>' +
    '<item><title><![CDATA[NASA & Space]]></title><link>https://example.com/nasa</link><description>Science &amp; research</description></item>' +
    '<item><title>Unsafe</title><link>javascript:alert(1)</link><description>skip</description></item>' +
    '</channel></rss>';
  const results = parseBingRss(xml);
  assert.equal(results.length, 1);
  assert.equal(results[0].title, 'NASA & Space');
  assert.equal(results[0].snippet, 'Science & research');
  assert.equal(results[0].provider, 'bing-rss');
});

test('Bing RSS parser replaces invalid numeric entities and rejects credentialed URLs', () => {
  const xml = '<rss><channel>' +
    '<item><title>Invalid &#x110000; and &#55296;</title><link>https://example.com/invalid-entity</link><description>Keep parsing</description></item>' +
    '<item><title>Credentialed URL</title><link>https://user:pass@example.com/private</link><description>skip</description></item>' +
    '<item><title>Valid rocket</title><link>https://example.com/rocket</link><description>Rocket &#x1F680;</description></item>' +
    '</channel></rss>';
  const results = parseBingRss(xml);
  assert.equal(results.length, 2);
  assert.equal(results[0].title, 'Invalid \uFFFD and \uFFFD');
  assert.equal(results[1].snippet, 'Rocket 🚀');
  assert.equal(results.some(item => item.url.includes('@')), false);
});

test('manifest and compressed catalog SHA-256, size, and page count are verified', () => {
  const { manifest, compressed } = makeManifest([makePage(1), makePage(2)]);
  const result = validateManifestAndCatalog(manifest, compressed);
  assert.equal(result.indexedPages, 2);
  assert.equal(result.pages.length, 2);
  assert.equal(result.sha256, manifest.catalog_gzip.sha256);
  assert.ok(compressed.length < MAX_CATALOG_GZIP_BYTES);
});

test('catalog integrity mismatch is rejected', () => {
  const { manifest, compressed } = makeManifest([makePage(1)]);
  const tampered = Buffer.from(compressed);
  tampered[tampered.length - 1] ^= 1;
  assert.throws(
    () => validateManifestAndCatalog(manifest, tampered),
    /catalog_size_mismatch|catalog_integrity_mismatch|catalog_decompression/
  );
});

test('explicit robots-denied catalog records are rejected', () => {
  const { manifest, compressed } = makeManifest([{ ...makePage(1), robots_allowed: false }]);
  assert.throws(
    () => validateManifestAndCatalog(manifest, compressed),
    /catalog_contains_robots_denied_record/
  );
});

test('weak body-only mentions are excluded from single-term results', () => {
  const pages = [
    {
      ...makePage(10, 'science'),
      title: 'General reference article',
      description: 'An unrelated overview',
      text: 'This article mentions Rolex once in passing.'
    },
    {
      ...makePage(11, 'science'),
      title: 'Rolex watches and history',
      description: 'Rolex watchmaking reference',
      text: 'A detailed guide to watchmaking.'
    }
  ];
  const results = searchCatalog(pages, 'Rolex', 'science');
  assert.deepEqual(results.map(item => item.url), [
    'https://example.com/nasa/11'
  ]);
});

test('title evidence outranks repeated body-only mentions', () => {
  const pages = [
    {
      ...makePage(12, 'science'),
      title: 'Rolex watch reference',
      description: 'A focused watch guide',
      text: 'A short overview.'
    },
    {
      ...makePage(13, 'science'),
      title: 'General reference article',
      description: 'An unrelated overview',
      text: 'Rolex watch '.repeat(20)
    }
  ];
  const results = searchCatalog(pages, 'Rolex watch', 'science');
  assert.equal(results.length, 2);
  assert.equal(results[0].url, 'https://example.com/nasa/12');
  assert.ok(results[0].score > results[1].score);
});

test('complete-query bonus requires every term in a high-signal field', () => {
  const pages = [
    {
      ...makePage(14, 'science'),
      title: 'Rolex watch reference',
      description: 'Watchmaking',
      text: 'A focused guide.'
    },
    {
      ...makePage(15, 'science'),
      title: 'General reference',
      description: 'Background information',
      text: 'Rolex watch '.repeat(20)
    }
  ];
  const results = searchCatalog(pages, 'Rolex watch', 'science');
  assert.equal(results[0].url, 'https://example.com/nasa/14');
  assert.ok(results[0].score < 100);
});
