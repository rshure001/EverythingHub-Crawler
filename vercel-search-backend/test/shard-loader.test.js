'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const test = require('node:test');
const assert = require('node:assert/strict');
const {
  MAX_TOTAL_SHARD_BYTES,
  validateShardManifest,
  validateShardPayload
} = require('../src/lib/catalog');

function makeShard(file, pages) {
  const buffer = Buffer.from(JSON.stringify({ version: 1, pages }));
  return {
    meta: {
      file,
      pages: pages.length,
      bytes: buffer.length,
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
    shards,
    catalog_gzip: {
      file: 'catalog-v1.json.gz',
      pages: totalPages,
      bytes: 1,
      sha256: 'a'.repeat(64)
    }
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

test('current repository manifest fits the bounded shard reader', () => {
  const manifestPath = path.resolve(__dirname, '../../public-search/manifest.json');
  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  const result = validateShardManifest(manifest);
  assert.equal(result.totalPages, manifest.total_pages);
  assert.equal(result.shards.length, manifest.shard_count);
  assert.ok(result.totalBytes <= MAX_TOTAL_SHARD_BYTES);
  assert.ok(manifest.total_pages >= 20000);
});

test('valid shard metadata and SHA-256 payload are accepted', () => {
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

test('aggregate shard size is bounded before network requests', () => {
  const shards = Array.from({ length: 49 }, (_, index) => ({
    file: 'shard-' + String(index + 1).padStart(4, '0') + '.json',
    pages: 1,
    bytes: 1_000_000,
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
