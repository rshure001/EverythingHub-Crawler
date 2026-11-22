'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const zlib = require('node:zlib');
const {
  MAX_SHARD_COUNT,
  MAX_PAGES_PER_SHARD,
  MAX_TOTAL_COMPRESSED_SHARD_BYTES,
  MAX_TOTAL_SHARD_UNCOMPRESSED_BYTES,
  MAX_QUERY_SCOPED_SHARD_COUNT,
  normalizeQueryIndexTokens,
  queryTokensForPage,
  validateQueryIndexMetadata,
  loadQueryScopedCatalogFromManifest,
  clearCatalogCache
} = require('../src/lib/catalog');
const { searchCatalog } = require('../src/lib/search-core');

test.beforeEach(() => clearCatalogCache());

const COMMIT = 'a'.repeat(40);
const BASE = 'https://raw.githubusercontent.com/rshure001/EverythingHub-Crawler/' + COMMIT + '/public-search/';

function digest(buffer) {
  return crypto.createHash('sha256').update(buffer).digest('hex');
}

function gzipJson(value) {
  return zlib.gzipSync(Buffer.from(JSON.stringify(value) + '\n'), { level: 9, mtime: 0 });
}

function makePage(url, title, description, text, product) {
  return {
    url, title, description, text, lane: 'general',
    ...(product ? { product } : {})
  };
}

function makeFixture(shardPages) {
  const pageShards = shardPages.map((pages, index) => {
    const raw = Buffer.from(JSON.stringify({ version: 1, pages }));
    const buffer = zlib.gzipSync(raw, { level: 9, mtime: 0 });
    return {
      meta: {
        file: 'shard-' + String(index + 1).padStart(4, '0') + '.json.gz',
        pages: pages.length,
        bytes: buffer.length,
        uncompressed_bytes: raw.length,
        sha256: digest(buffer)
      },
      buffer
    };
  });
  const tokenShards = new Map();
  for (let shardIndex = 0; shardIndex < shardPages.length; shardIndex += 1) {
    for (const page of shardPages[shardIndex]) {
      for (const token of queryTokensForPage(page)) {
        if (!tokenShards.has(token)) tokenShards.set(token, new Map());
        const counts = tokenShards.get(token);
        counts.set(shardIndex, (counts.get(shardIndex) || 0) + 1);
      }
    }
  }
  const byPrefix = new Map();
  for (const [token, counts] of Array.from(tokenShards.entries()).sort(([a], [b]) => a.localeCompare(b))) {
    const prefix = digest(Buffer.from(token)).slice(0, 2);
    if (!byPrefix.has(prefix)) byPrefix.set(prefix, { version: 1, prefix, tokens: {} });
    byPrefix.get(prefix).tokens[token] = Array.from(counts.entries()).sort((a, b) => a[0] - b[0]);
  }
  const indexBuffers = new Map();
  const indexFiles = [];
  let compressedBytes = 0;
  let uncompressedBytes = 0;
  let postingCount = 0;
  for (const [prefix, payload] of Array.from(byPrefix.entries()).sort(([a], [b]) => a.localeCompare(b))) {
    const raw = Buffer.from(JSON.stringify(payload) + '\n');
    const buffer = zlib.gzipSync(raw, { level: 9, mtime: 0 });
    const file = 'query-index/' + prefix + '.json.gz';
    indexBuffers.set(file, buffer);
    compressedBytes += buffer.length;
    uncompressedBytes += raw.length;
    postingCount += Object.values(payload.tokens).reduce((sum, postings) => sum + postings.length, 0);
    indexFiles.push({
      file, bytes: buffer.length, uncompressed_bytes: raw.length,
      sha256: digest(buffer), items: Object.keys(payload.tokens).length
    });
  }
  const totalPages = shardPages.reduce((sum, pages) => sum + pages.length, 0);
  const manifest = {
    version: 1,
    integrity: 'sha256',
    updated_at: Math.floor(Date.now() / 1000),
    total_pages: totalPages,
    shard_count: pageShards.length,
    shards: pageShards.map(shard => shard.meta),
    query_index: {
      version: 1,
      shard_count: indexFiles.length,
      token_count: tokenShards.size,
      posting_count: postingCount,
      compressed_bytes: compressedBytes,
      uncompressed_bytes: uncompressedBytes,
      shards: indexFiles
    }
  };
  const manifestRaw = Buffer.from(JSON.stringify(manifest));
  const buffers = new Map(indexBuffers);
  for (const shard of pageShards) buffers.set(shard.meta.file, shard.buffer);
  const calls = [];
  const fetchBuffer = async (url, maxBytes) => {
    calls.push(url);
    const relative = url.startsWith(BASE) ? url.slice(BASE.length) : '';
    const buffer = buffers.get(relative);
    if (!buffer) throw new Error('unexpected_test_fetch:' + relative);
    if (buffer.length > maxBytes) throw new Error('test_fetch_size_limit');
    return Buffer.from(buffer);
  };
  return { manifest, manifestRaw, buffers, calls, fetchBuffer, pageShards };
}

test('query normalization matches the public index stopword and Unicode normalization rules', () => {
  assert.deepEqual(normalizeQueryIndexTokens('The NASA Mars'), ['nasa', 'mars']);
  assert.deepEqual(normalizeQueryIndexTokens('ＮＡＳＡ'), ['nasa']);
});

test('query-scoped loader fetches only the intersection of matching page shards', async () => {
  const fixture = makeFixture([
    [makePage('https://nasa.gov/mars', 'The NASA Mars Mission', 'NASA Mars science', 'The NASA Mars mission explores the planet.')],
    [makePage('https://nasa.gov/artemis', 'NASA Artemis', 'NASA lunar mission', 'NASA Artemis returns to the Moon.')]
  ]);
  const loaded = await loadQueryScopedCatalogFromManifest(
    fixture.manifest, COMMIT, fixture.manifestRaw, 'the NASA Mars', fixture.fetchBuffer
  );
  assert.equal(loaded.queryScoped, true);
  assert.equal(loaded.indexedPages, 2);
  assert.equal(loaded.scannedPages, 1);
  assert.equal(loaded.candidatePageCount, 1);
  assert.equal(loaded.candidateShardCount, 1);
  assert.equal(loaded.pages[0].url, 'https://nasa.gov/mars');
  assert.equal(searchCatalog(loaded.pages, 'the NASA Mars').length, 1);
  assert.ok(fixture.calls.some(url => url.endsWith('/shard-0001.json.gz')));
  assert.equal(fixture.calls.some(url => url.endsWith('/shard-0002.json.gz')), false);
});

test('a missing query token returns a verified empty result without fetching page shards', async () => {
  const fixture = makeFixture([
    [makePage('https://nasa.gov/mars', 'NASA Mars Mission', 'NASA Mars science', 'NASA Mars science')]
  ]);
  const loaded = await loadQueryScopedCatalogFromManifest(
    fixture.manifest, COMMIT, fixture.manifestRaw, 'Atlantis', fixture.fetchBuffer
  );
  assert.equal(loaded.queryScoped, true);
  assert.equal(loaded.indexedPages, 1);
  assert.equal(loaded.scannedPages, 0);
  assert.equal(loaded.candidateShardCount, 0);
  assert.deepEqual(loaded.pages, []);
  assert.equal(fixture.calls.some(url => /shard-\d{4}\.json/.test(url)), false);
});

test('query-index compressed payload SHA-256 is verified before decoding', async () => {
  const fixture = makeFixture([
    [makePage('https://nasa.gov/mars', 'NASA Mars Mission', 'NASA Mars science', 'NASA Mars science')]
  ]);
  const prefix = digest(Buffer.from('nasa')).slice(0, 2);
  const entry = fixture.manifest.query_index.shards.find(item => item.file.endsWith(prefix + '.json.gz'));
  const tampered = Buffer.from(fixture.buffers.get(entry.file));
  tampered[0] ^= 1;
  fixture.buffers.set(entry.file, tampered);
  await assert.rejects(
    loadQueryScopedCatalogFromManifest(fixture.manifest, COMMIT, fixture.manifestRaw, 'NASA', fixture.fetchBuffer),
    /query_index_sha256_mismatch/
  );
});

test('query-index posting counts are checked against the actual candidate page shards', async () => {
  const fixture = makeFixture([
    [makePage('https://nasa.gov/mars', 'NASA Mars Mission', 'NASA Mars science', 'NASA Mars science')],
    [makePage('https://nasa.gov/artemis', 'NASA Artemis', 'NASA lunar mission', 'NASA Artemis')]
  ]);
  const entry = fixture.manifest.query_index.shards.find(item => item.file.endsWith(digest(Buffer.from('mars')).slice(0, 2) + '.json.gz'));
  const payload = JSON.parse(zlib.gunzipSync(fixture.buffers.get(entry.file)).toString('utf8'));
  payload.tokens.mars.push([1, 1]);
  payload.tokens.mars.sort((a, b) => a[0] - b[0]);
  const raw = Buffer.from(JSON.stringify(payload) + '\n');
  const buffer = zlib.gzipSync(raw, { level: 9, mtime: 0 });
  const oldBytes = entry.bytes;
  const oldDecoded = entry.uncompressed_bytes;
  entry.bytes = buffer.length;
  entry.uncompressed_bytes = raw.length;
  entry.sha256 = digest(buffer);
  fixture.manifest.query_index.compressed_bytes += buffer.length - oldBytes;
  fixture.manifest.query_index.uncompressed_bytes += raw.length - oldDecoded;
  fixture.manifest.query_index.posting_count += 1;
  fixture.buffers.set(entry.file, buffer);
  const manifestRaw = Buffer.from(JSON.stringify(fixture.manifest));
  await assert.rejects(
    loadQueryScopedCatalogFromManifest(fixture.manifest, COMMIT, manifestRaw, 'NASA Mars', fixture.fetchBuffer),
    /query_index_posting_mismatch/
  );
});

test('query-index metadata rejects unsafe paths and mismatched aggregate sizes', () => {
  const fixture = makeFixture([
    [makePage('https://nasa.gov/mars', 'NASA Mars Mission', 'NASA Mars science', 'NASA Mars science')]
  ]);
  const unsafe = structuredClone(fixture.manifest);
  unsafe.query_index.shards[0].file = '../manifest.json';
  assert.throws(() => validateQueryIndexMetadata(unsafe), /query_index_file_entry_invalid/);
  const badTotals = structuredClone(fixture.manifest);
  badTotals.query_index.compressed_bytes += 1;
  assert.throws(() => validateQueryIndexMetadata(badTotals), /query_index_manifest_totals_invalid/);
});

test('broad queries fail closed before loading more than the bounded candidate shard count', async () => {
  const pages = Array.from({ length: MAX_QUERY_SCOPED_SHARD_COUNT + 1 }, (_, index) => [
    makePage('https://example.gov/item/' + index, 'Common catalog item ' + index, 'Common public catalog', 'Common catalog entry')
  ]);
  const fixture = makeFixture(pages);
  await assert.rejects(
    loadQueryScopedCatalogFromManifest(fixture.manifest, COMMIT, fixture.manifestRaw, 'common', fixture.fetchBuffer),
    /query_index_too_broad/
  );
  assert.equal(fixture.calls.some(url => /shard-\d{4}\.json/.test(url)), false);
});

test('query-index-free manifests retain the verified full-index fallback contract', async () => {
  const fixture = makeFixture([
    [makePage('https://nasa.gov/mars', 'NASA Mars Mission', 'NASA Mars science', 'NASA Mars science')]
  ]);
  delete fixture.manifest.query_index;
  assert.equal(
    await loadQueryScopedCatalogFromManifest(fixture.manifest, COMMIT, fixture.manifestRaw, 'NASA', fixture.fetchBuffer),
    null
  );
});

test('public search shard envelope accepts the observed 1008-page shard within bounded totals', async () => {
  assert.equal(MAX_SHARD_COUNT, 900);
  assert.equal(MAX_PAGES_PER_SHARD, 2000);
  assert.equal(MAX_TOTAL_COMPRESSED_SHARD_BYTES, 150000000);
  assert.equal(MAX_TOTAL_SHARD_UNCOMPRESSED_BYTES, 600000000);
  const pages = Array.from({ length: 1008 }, (_, index) =>
    makePage('https://nasa.gov/page/' + index, 'NASA page ' + index, 'NASA public information', 'NASA public information')
  );
  const fixture = makeFixture([pages]);
  const loaded = await loadQueryScopedCatalogFromManifest(
    fixture.manifest, COMMIT, fixture.manifestRaw, 'NASA', fixture.fetchBuffer
  );
  assert.equal(loaded.pages.length, 1008);
  assert.equal(loaded.candidatePageCount, 1008);
  assert.equal(loaded.candidateShardCount, 1);
});
