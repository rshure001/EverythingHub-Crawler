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

test('current repository manifest fits the bounded shard reader without gzip metadata', () => {
  const manifestPath = path.resolve(__dirname, '../../public-search/manifest.json');
  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  const result = validateShardManifest(manifest);
  assert.equal(result.totalPages, manifest.total_pages);
  assert.equal(result.shards.length, manifest.shard_count);
  const compressed = result.shardFormat === 'gzip';
  assert.ok(result.totalBytes <= (compressed ? MAX_TOTAL_COMPRESSED_SHARD_BYTES : MAX_TOTAL_SHARD_BYTES));
  assert.ok(result.shards.length <= MAX_SHARD_COUNT);
  assert.ok(manifest.total_pages <= MAX_INDEXED_PAGES);
  assert.ok(manifest.total_pages >= 20000);
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
  const shards = Array.from({ length: 200 }, (_, index) => ({
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
