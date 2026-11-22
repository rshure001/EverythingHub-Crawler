'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {
  markCatalogPreviewStale,
  applyCatalogPreviewFreshness
} = require('../src/api/search')._test;

const snapshotAt = '2026-11-22T09:00:00Z';
const timestamp = Date.parse(snapshotAt) / 1000;

function network(overrides = {}) {
  return {
    stale: false,
    sourceIndexPages: 100,
    sourceIndexUpdatedAt: snapshotAt,
    ...overrides
  };
}

function pageIndex(overrides = {}) {
  return {
    indexedPages: 100,
    updatedAt: timestamp,
    sha256: 'a'.repeat(64),
    ...overrides
  };
}

test('marks a matching fresh catalog preview as current', () => {
  const preview = { status: 'freshness-unverified', stale: false };
  const result = applyCatalogPreviewFreshness(preview, network(), pageIndex());

  assert.equal(result.sourceIndexMatches, true);
  assert.equal(result.stale, false);
  assert.equal(preview.freshnessMatch, true);
  assert.equal(preview.stale, false);
  assert.equal(preview.status, 'candidate-preview');
  assert.equal(preview.pageIndexPages, 100);
  assert.equal(preview.pageIndexUpdatedAt, snapshotAt);
});

test('marks a page-count mismatch as stale, not merely freshness-unverified', () => {
  const preview = { status: 'freshness-unverified', stale: false };
  const result = applyCatalogPreviewFreshness(
    preview,
    network(),
    pageIndex({ indexedPages: 101 })
  );

  assert.equal(result.sourceIndexMatches, false);
  assert.equal(result.stale, true);
  assert.equal(preview.freshnessMatch, false);
  assert.equal(preview.stale, true);
  assert.equal(preview.status, 'stale-candidate-preview');
});

test('marks a cached stale catalog preview as stale even when counts match', () => {
  const preview = { status: 'freshness-unverified', stale: false };
  const result = applyCatalogPreviewFreshness(
    preview,
    network({ stale: true }),
    pageIndex()
  );

  assert.equal(result.sourceIndexMatches, true);
  assert.equal(result.stale, true);
  assert.equal(preview.freshnessMatch, true);
  assert.equal(preview.stale, true);
  assert.equal(preview.status, 'stale-candidate-preview');
});

test('marks the candidate preview stale when the page index could not be verified', () => {
  const preview = { status: 'freshness-unverified', stale: false };
  const result = markCatalogPreviewStale(preview, false);

  assert.equal(result.freshnessMatch, false);
  assert.equal(result.stale, true);
  assert.equal(result.status, 'stale-candidate-preview');
});
