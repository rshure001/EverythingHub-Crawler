'use strict';

const assert = require('node:assert/strict');
const { loadUniversalCatalogNetwork, searchUniversalCatalogs } = require('../src/lib/universal-catalog-search');

async function main() {
  const network = await loadUniversalCatalogNetwork();
  assert.ok(network.catalogCount > 0, 'live catalog manifest has no catalogs');
  assert.ok(network.uniquePublicRecords > 0, 'live catalog manifest has no public-search-eligible records');
  assert.equal(network.documents.length, network.uniquePublicRecords, 'root search documents do not cover the unique public record set');
  assert.ok(network.rootSearchDocumentOccurrences >= network.rootSearchDocumentCount);
  assert.equal(network.recordsPublished, 0, 'staging preview unexpectedly reports published records');
  assert.equal(network.productionIndexed, false, 'staging preview unexpectedly reports production indexing');

  const sample = network.documents.find(document => document.title && document.title.trim());
  assert.ok(sample, 'live catalog index has no searchable record title');
  const query = sample.title.slice(0, 160);
  const results = searchUniversalCatalogs(query, network, 5);
  assert.ok(results.some(catalog =>
    (catalog.matched_records || []).some(record => record.record_id === sample.record_id)
  ), 'sample canonical record was not found by the live catalog search projection');

  console.log(JSON.stringify({
    status: 'validated',
    generatedAt: network.generatedAt,
    catalogDefinitions: network.catalogCount,
    indexShards: network.indexShardCount,
    uniquePublicRecords: network.uniquePublicRecords,
    uniqueRootSearchDocuments: network.rootSearchDocumentCount,
    rootSearchDocumentOccurrences: network.rootSearchDocumentOccurrences,
    publicRecordMemberships: network.publicRecordMemberships,
    sampleQuery: query,
    sampleRecordId: sample.record_id,
    matchingCatalogs: results.map(catalog => ({
      catalogId: catalog.catalog_id,
      title: catalog.catalog_title,
      level: catalog.catalog_level,
      matchedRecords: catalog.matched_record_count
    })),
    recordsPublished: network.recordsPublished,
    productionIndexed: network.productionIndexed,
    previewOnly: true
  }, null, 2));
  process.exit(0);
}

main().catch(error => {
  console.error('Universal catalog live validation failed:', error && error.stack || error);
  process.exit(1);
});
