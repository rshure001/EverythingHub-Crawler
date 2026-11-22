'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { gzipSync } = require('node:zlib');
const test = require('node:test');
const assert = require('node:assert/strict');
const {
  MAX_INDEXED_PAGES,
  MAX_SHARD_COUNT,
  MAX_PAGES_PER_SHARD,
  MAX_QUERY_SHARDS,
  validateQueryIndexMetadata,
  validateQueryIndexPayload,
  loadQueryIndexedCatalog,
  MAX_TOTAL_SHARD_BYTES,
  MAX_TOTAL_COMPRESSED_SHARD_BYTES,
  validateShardManifest,
  validateShardPayload,
  loadCatalogFromManifest,
  mapLimit
} = require('../src/lib/catalog');

function makeShard(file, pages, compressed = true) {
  const uncompressed = Buffer.from(JSON.stringify({ version: 1, pages }));
  const buffer = compressed ? gzipSync(uncompressed, { level: 9, mtime: 0 }) : uncompressed;
  const name = compressed ? file.replace(/\.json$/, '.json.gz') : file;
  return {
    meta: {
      file: name,
      pages: pages.length,
      bytes: buffer.length,
      ...(compressed ? { uncompressed_bytes: uncompressed.length } : {}),
      sha256: crypto.createHash('sha256').update(buffer).digest('hex')
    },
    buffer
  };
}

function makeManifest(shards) {
  const totalPages = shards.reduce((sum, shard) => sum + shard.pages, 0);
  return {
    version: 1,
    integrity: 'sha256',
    updated_at: Math.floor(Date.now() / 1000),
    total_pages: totalPages,
    shard_count: shards.length,
    shards
  };
}

function page(url) {
  return {
    url,
    title: 'Example page',
    description: 'Example description',
    text: 'Example content',
    lane: 'general'
  };
}

function makeQueryIndexShard(prefix, tokens) {
  const uncompressed = Buffer.from(JSON.stringify({ version: 1, prefix, tokens }));
  const buffer = gzipSync(uncompressed, { level: 9, mtime: 0 });
  return {
    meta: {
      file: 'query-index/' + prefix + '.json.gz',
      items: Object.keys(tokens).length,
      bytes: buffer.length,
      uncompressed_bytes: uncompressed.length,
      sha256: crypto.createHash('sha256').update(buffer).digest('hex')
    },
    tokens,
    buffer
  };
}

function makeQueryIndexMetadata(shards) {
  return {
    version: 1,
    shard_count: shards.length,
    token_count: shards.reduce((sum, shard) => sum + shard.meta.items, 0),
    posting_count: shards.reduce((sum, shard) => sum + Object.values(shard.tokens).reduce((count, postings) => count + postings.length, 0), 0),
    compressed_bytes: shards.reduce((sum, shard) => sum + shard.meta.bytes, 0),
    uncompressed_bytes: shards.reduce((sum, shard) => sum + shard.meta.uncompressed_bytes, 0),
    shards: shards.map(shard => shard.meta)
  };
}

function tokenPrefix(token) {
  return crypto.createHash('sha256').update(token, 'utf8').digest('hex').slice(0, 2);
}

test('current repository manifest fits the bounded shard reader without gzip metadata', () => {
  const manifestPath = path.resolve(__dirname, '../../public-search/manifest.json');
  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  const result = validateShardManifest(manifest);
  assert.equal(result.totalPages, manifest.total_pages);
  assert.equal(result.shards.length, manifest.shard_count);
  const compressed = result.shardFormat === 'gzip';
  assert.ok(result.totalBytes <= (compressed ? MAX_TOTAL_COMPRESSED_SHARD_BYTES : MAX_TOTAL_SHARD_BYTES));
  assert.ok(result.shards.length <= MAX_SHARD_COUNT);
  assert.ok(result.shards.every(shard => shard.pages <= MAX_PAGES_PER_SHARD));
  assert.ok(manifest.total_pages <= MAX_INDEXED_PAGES);
  assert.ok(manifest.total_pages >= 20000);
});

test('rejects shard page counts above the 2,000-page bounded limit', () => {
  const pages = MAX_PAGES_PER_SHARD + 1;
  const manifest = {
    version: 1,
    integrity: 'sha256',
    updated_at: Math.floor(Date.now() / 1000),
    total_pages: pages,
    shard_count: 1,
    shards: [{
      file: 'shard-0001.json.gz',
      pages,
      bytes: 1,
      uncompressed_bytes: 1,
      sha256: 'a'.repeat(64)
    }]
  };
  assert.throws(() => validateShardManifest(manifest), /manifest_shard_page_count_invalid/);
});



test('query-index metadata validates bounded prefix files and exact aggregate sizes', () => {
  const pageShard = makeShard('shard-0001.json', [page('https://example.com/nasa')]);
  const token = 'nasa';
  const queryShard = makeQueryIndexShard(tokenPrefix(token), { nasa: [[0, 1]] });
  const manifest = makeManifest([pageShard.meta]);
  manifest.query_index = makeQueryIndexMetadata([queryShard]);
  assert.equal(validateQueryIndexMetadata(manifest).token_count, 1);
  assert.deepEqual(validateQueryIndexPayload(queryShard.meta, queryShard.buffer, tokenPrefix(token), manifest.shards), { nasa: [[0, 1]] });
  const unsafe = { ...manifest, query_index: { ...manifest.query_index, shards: [{ ...queryShard.meta, file: '../query-index.json.gz' }] } };
  assert.throws(() => validateQueryIndexMetadata(unsafe), /manifest_query_index_shard_metadata_invalid/);
});

test('query-indexed loader fetches only candidate page shards and preserves full-corpus counts', async () => {
  const first = makeShard('shard-0001.json', [page('https://example.com/nasa')]);
  const second = makeShard('shard-0002.json', [page('https://example.com/other')]);
  const queryShard = makeQueryIndexShard(tokenPrefix('nasa'), { nasa: [[0, 1]] });
  const manifest = makeManifest([first.meta, second.meta]);
  manifest.query_index = makeQueryIndexMetadata([queryShard]);
  const commitSha = 'c'.repeat(40);
  const manifestRaw = Buffer.from(JSON.stringify(manifest));
  const calls = [];
  const loaded = await loadCatalogFromManifest(
    manifest,
    commitSha,
    manifestRaw,
    async (url) => {
      calls.push(url);
      if (url.endsWith('/query-index/' + tokenPrefix('nasa') + '.json.gz')) return queryShard.buffer;
      if (url.endsWith('/shard-0001.json.gz')) return first.buffer;
      throw new Error('unexpected_fetch:' + url);
    },
    'NASA'
  );
  assert.equal(loaded.transport, 'query-indexed-shards');
  assert.equal(loaded.indexedPages, 2);
  assert.equal(loaded.candidatePages, 1);
  assert.equal(loaded.candidateShardCount, 1);
  assert.equal(loaded.candidateShardTotal, 1);
  assert.equal(loaded.candidateShardsTruncated, false);
  assert.deepEqual(loaded.pages.map(item => item.url), ['https://example.com/nasa']);
  assert.equal(calls.length, 2, 'one query-index file and one candidate page shard are fetched');
});

test('multi-term query uses a complete union and ranks shards by term coverage', async () => {
  const first = makeShard('shard-0001.json', [page('https://example.com/las-only')]);
  const second = makeShard('shard-0002.json', [page('https://example.com/las-vegas')]);
  const third = makeShard('shard-0003.json', [page('https://example.com/vegas-only')]);
  const las = makeQueryIndexShard(tokenPrefix('las'), { las: [[0, 1], [1, 1]] });
  const vegas = makeQueryIndexShard(tokenPrefix('vegas'), { vegas: [[1, 1], [2, 1]] });
  const manifest = makeManifest([first.meta, second.meta, third.meta]);
  manifest.query_index = makeQueryIndexMetadata([las, vegas]);
  const loaded = await loadQueryIndexedCatalog(
    manifest,
    'd'.repeat(40),
    'e'.repeat(64),
    'Las Vegas',
    async (url) => {
      if (url.endsWith('/query-index/' + tokenPrefix('las') + '.json.gz')) return las.buffer;
      if (url.endsWith('/query-index/' + tokenPrefix('vegas') + '.json.gz')) return vegas.buffer;
      if (url.endsWith('/shard-0001.json.gz')) return first.buffer;
      if (url.endsWith('/shard-0002.json.gz')) return second.buffer;
      if (url.endsWith('/shard-0003.json.gz')) return third.buffer;
      throw new Error('unexpected_fetch:' + url);
    }
  );
  assert.equal(loaded.candidateSelection, 'union-ranked-by-token-coverage');
  assert.equal(loaded.candidateShardTotal, 3);
  assert.deepEqual(loaded.pages.map(item => item.url).sort(), [
    'https://example.com/las-only',
    'https://example.com/las-vegas',
    'https://example.com/vegas-only'
  ]);
});

test('query-indexed loader falls back to a bounded union when terms have no shared shard', async () => {
  const first = makeShard('shard-0001.json', [page('https://example.com/new')]);
  const second = makeShard('shard-0002.json', [page('https://example.com/york')]);
  const newer = makeQueryIndexShard(tokenPrefix('new'), { new: [[0, 1]] });
  const york = makeQueryIndexShard(tokenPrefix('york'), { york: [[1, 1]] });
  const manifest = makeManifest([first.meta, second.meta]);
  manifest.query_index = makeQueryIndexMetadata([newer, york]);
  const loaded = await loadQueryIndexedCatalog(
    manifest,
    'f'.repeat(40),
    'a'.repeat(64),
    'New York',
    async (url) => {
      if (url.endsWith('/query-index/' + tokenPrefix('new') + '.json.gz')) return newer.buffer;
      if (url.endsWith('/query-index/' + tokenPrefix('york') + '.json.gz')) return york.buffer;
      if (url.endsWith('/shard-0001.json.gz')) return first.buffer;
      if (url.endsWith('/shard-0002.json.gz')) return second.buffer;
      throw new Error('unexpected_fetch:' + url);
    }
  );
  assert.equal(loaded.candidateSelection, 'union-ranked-by-token-coverage');
  assert.equal(loaded.candidateShardTotal, 2);
  assert.equal(loaded.pages.length, 2);
});

test('query-indexed loader caps candidate shards and marks the search scope partial', async () => {
  const shards = Array.from({ length: MAX_QUERY_SHARDS + 1 }, (_, index) =>
    makeShard('shard-' + String(index + 1).padStart(4, '0') + '.json', [page('https://example.com/page-' + index)])
  );
  const token = 'nasa';
  const postings = shards.map((_shard, index) => [index, 1]);
  const queryShard = makeQueryIndexShard(tokenPrefix(token), { nasa: postings });
  const manifest = makeManifest(shards.map(shard => shard.meta));
  manifest.query_index = makeQueryIndexMetadata([queryShard]);
  const loaded = await loadQueryIndexedCatalog(
    manifest,
    '1'.repeat(40),
    '2'.repeat(64),
    'NASA',
    async (url) => {
      if (url.endsWith('/query-index/' + tokenPrefix(token) + '.json.gz')) return queryShard.buffer;
      const match = url.match(/shard-(\d{4})\.json\.gz$/);
      if (!match) throw new Error('unexpected_fetch:' + url);
      const index = Number(match[1]) - 1;
      return shards[index].buffer;
    }
  );
  assert.equal(loaded.candidateShardTotal, MAX_QUERY_SHARDS + 1);
  assert.equal(loaded.candidateShardCount, MAX_QUERY_SHARDS);
  assert.equal(loaded.candidateShardsTruncated, true);
  assert.equal(loaded.candidatePages, MAX_QUERY_SHARDS);
  assert.equal(loaded.pages.length, MAX_QUERY_SHARDS);
});

test('query-index postings cannot reference nonexistent page shards', async () => {
  const shard = makeShard('shard-0001.json', [page('https://example.com/nasa')]);
  const queryShard = makeQueryIndexShard(tokenPrefix('nasa'), { nasa: [[1, 1]] });
  const manifest = makeManifest([shard.meta]);
  manifest.query_index = makeQueryIndexMetadata([queryShard]);
  await assert.rejects(
    loadQueryIndexedCatalog(
      manifest,
      '3'.repeat(40),
      '4'.repeat(64),
      'NASA',
      async () => queryShard.buffer
    ),
    /query_index_posting_invalid/
  );
});

test('legacy raw shards remain readable during rollout', () => {
  const shard = makeShard('shard-0001.json', [page('https://example.com/legacy')], false);
  assert.equal(validateShardPayload(shard.meta, shard.buffer).length, 1);
});

test('valid compressed shard metadata and SHA-256 payload are accepted', () => {
  const shard = makeShard('shard-0001.json', [page('https://example.com/one')]);
  const manifest = makeManifest([shard.meta]);
  const checked = validateShardManifest(manifest);
  assert.equal(checked.totalPages, 1);
  assert.equal(validateShardPayload(shard.meta, shard.buffer).length, 1);
});

test('duplicate shard filenames are rejected', () => {
  const first = makeShard('shard-0001.json', [page('https://example.com/one')]);
  const second = makeShard('shard-0002.json', [page('https://example.com/two')]);
  second.meta.file = first.meta.file;
  assert.throws(() => validateShardManifest(makeManifest([first.meta, second.meta])), /manifest_duplicate_shard_file/);
});

test('unsafe shard paths are rejected', () => {
  const shard = makeShard('shard-0001.json', [page('https://example.com/one')]);
  shard.meta.file = '../manifest.json';
  assert.throws(() => validateShardManifest(makeManifest([shard.meta])), /manifest_shard_file_invalid/);
});

test('compressed shard aggregate size is bounded before network requests', () => {
  const shards = Array.from({ length: 288 }, (_, index) => ({
    file: 'shard-' + String(index + 1).padStart(4, '0') + '.json.gz',
    pages: 1,
    bytes: 350_000,
    uncompressed_bytes: 500_000,
    sha256: 'b'.repeat(64)
  }));
  assert.throws(() => validateShardManifest(makeManifest(shards)), /manifest_total_compressed_shard_bytes_exceeded/);
});

test('legacy raw shard aggregate size remains bounded during rollout', () => {
  const shards = Array.from({ length: 300 }, (_, index) => ({
    file: 'shard-' + String(index + 1).padStart(4, '0') + '.json',
    pages: 1,
    bytes: 350_000,
    sha256: 'b'.repeat(64)
  }));
  assert.throws(() => validateShardManifest(makeManifest(shards)), /manifest_total_shard_bytes_exceeded/);
});

test('shard byte count and SHA-256 are checked', () => {
  const shard = makeShard('shard-0001.json', [page('https://example.com/one')]);
  const tampered = Buffer.from(shard.buffer);
  tampered[tampered.length - 1] ^= 1;
  assert.throws(() => validateShardPayload(shard.meta, tampered), /shard_integrity_mismatch/);
  assert.throws(() => validateShardPayload(shard.meta, Buffer.concat([shard.buffer, Buffer.from('x')])), /shard_size_mismatch/);
});

test('robots-denied shard records are rejected', () => {
  const shard = makeShard('shard-0001.json', [{ ...page('https://example.com/blocked'), robots_allowed: false }]);
  assert.throws(() => validateShardPayload(shard.meta, shard.buffer), /shard_contains_robots_denied_record/);
});

test('unsafe shard URLs are rejected', () => {
  const shard = makeShard('shard-0001.json', [page('https://user:pass@example.com/private')]);
  assert.throws(() => validateShardPayload(shard.meta, shard.buffer), /shard_record_url_unsafe/);
});

test('shard-only catalog loader fetches from the immutable commit SHA and pins the manifest digest', async () => {
  const shard = makeShard('shard-0001.json', [page('https://example.com/one')]);
  const manifest = makeManifest([shard.meta]);
  const commitSha = 'a'.repeat(40);
  const manifestRaw = Buffer.from(JSON.stringify(manifest));
  const manifestDigest = crypto.createHash('sha256').update(manifestRaw).digest('hex');
  const calls = [];
  const loaded = await loadCatalogFromManifest(
    manifest,
    commitSha,
    manifestRaw,
    async (url, maxBytes, timeoutMs) => {
      calls.push({ url, maxBytes, timeoutMs });
      return shard.buffer;
    }
  );
  assert.equal(loaded.transport, 'shards');
  assert.equal(loaded.integrity, 'sha256');
  assert.equal(loaded.sha256, manifestDigest);
  assert.equal(loaded.indexedPages, 1);
  assert.deepEqual(calls.map(call => call.url), [
    'https://raw.githubusercontent.com/rshure001/EverythingHub-Crawler/' +
    commitSha + '/public-search/shard-0001.json.gz'
  ]);
});

test('catalog loader falls back to verified shards when the optional gzip object is missing', async () => {
  const shard = makeShard('shard-0001.json', [page('https://example.com/one')]);
  const manifest = makeManifest([shard.meta]);
  manifest.catalog_gzip = {
    file: 'catalog-v1.json.gz',
    pages: 1,
    bytes: 1,
    sha256: 'a'.repeat(64)
  };
  const commitSha = 'b'.repeat(40);
  const manifestRaw = Buffer.from(JSON.stringify(manifest));
  const loaded = await loadCatalogFromManifest(
    manifest,
    commitSha,
    manifestRaw,
    async (url) => {
      if (url.endsWith('/catalog-v1.json.gz')) throw new Error('upstream_http_404');
      return shard.buffer;
    }
  );
  assert.equal(loaded.transport, 'shards');
  assert.equal(loaded.indexedPages, 1);
  assert.equal(loaded.pages[0].url, 'https://example.com/one');
});

test('shard loader uses a bounded worker pool without batch barriers', async () => {
  let active = 0;
  let maximum = 0;
  const results = await mapLimit(Array.from({ length: 40 }, (_, index) => index), 5, async (value) => {
    active += 1;
    maximum = Math.max(maximum, active);
    await new Promise(resolve => setImmediate(resolve));
    active -= 1;
    return value * 2;
  });
  assert.equal(results.length, 40);
  assert.equal(results[39], 78);
  assert.ok(maximum <= 5);
  assert.ok(maximum > 1);
});
