"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { searchPageDerivedCatalogs } = require("../src/lib/page-derived-catalogs");

function catalog(catalog_id, title, catalog_type, record_type, subject) {
  return {
    catalog_id, title, catalog_type, record_type, subject,
    source_host: "nasa.gov",
    source_url: "https://example.org/catalog",
    source_page_count: 10,
    record_count: 10,
    evidence_strength: "high",
    catalog_origin: "derived",
    source_policy_review_status: "not_reviewed",
    record_level_clearance: "not_reviewed",
    rights_status: "publicly_accessible_not_cleared_for_redistribution"
  };
}
function record(record_id, title, record_type, catalog_ids, description = "") {
  return {
    record_id, title, record_type, catalog_ids, description,
    source_urls: ["https://example.org/source"],
    last_seen_at: "2026-11-21T00:00:00Z",
    source_policy_review_status: "not_reviewed",
    record_level_clearance: "not_reviewed"
  };
}
function fixture() {
  return {
    catalogs: [
      catalog("catalog:rolex-articles", "National Geographic Subject Catalog", "knowledge_base", "subject", "National Geographic"),
      catalog("catalog:rolex-brand", "Rolex Watches", "commerce", "brand", "Rolex"),
      catalog("catalog:nasa-humanity", "Humanity Subject Catalog", "knowledge_base", "subject", "Humanity"),
      catalog("catalog:nasa-org", "NASA Organizations", "organization_directory", "organization", "NASA"),
      catalog("catalog:vegas-articles", "National Geographic Articles", "knowledge_base", "article", "Travel"),
      catalog("catalog:vegas-places", "Las Vegas Places", "geographic", "place", "Las Vegas")
    ],
    records: [
      record("r-rolex-article", "National Geographic and Rolex Committed to a Perpetual Planet", "article", ["catalog:rolex-articles"]),
      record("r-rolex-brand", "Rolex", "brand", ["catalog:rolex-brand"]),
      record("r-nasa-humanity", "Benefits to Humanity - NASA", "subject", ["catalog:nasa-humanity"]),
      record("r-nasa-org", "NASA", "organization", ["catalog:nasa-org"]),
      record("r-vegas-article", "Chandelier bars and old-school kitsch: the best places to stay in Las Vegas", "article", ["catalog:vegas-articles"]),
      record("r-vegas-place", "Las Vegas Strip", "place", ["catalog:vegas-places"]),
      record("r-nasa-news", "NASA Announces New Mission", "article", ["catalog:vegas-articles"], "Latest NASA news and mission updates")
    ]
  };
}

test("Rolex does not return an unrelated article catalog just because an article mentions Rolex", () => {
  const results = searchPageDerivedCatalogs("Rolex", fixture(), 8);
  assert.deepEqual(results.map(x => x.catalog_id), ["catalog:rolex-brand"]);
  assert.equal(results[0].relevance_class, "typed_product_match");
});

test("NASA does not return unrelated subject catalogs based on nasa.gov source host or incidental page titles", () => {
  const results = searchPageDerivedCatalogs("NASA", fixture(), 8);
  assert.deepEqual(results.map(x => x.catalog_id), ["catalog:nasa-org"]);
  assert.equal(results[0].relevance_class, "record_match");
});

test("Las Vegas prefers a typed geographic catalog and excludes unrelated article catalogs", () => {
  const results = searchPageDerivedCatalogs("Las Vegas", fixture(), 8);
  assert.deepEqual(results.map(x => x.catalog_id), ["catalog:vegas-places"]);
  assert.equal(results[0].relevance_class, "typed_location_match");
});

test("explicit content intent still permits relevant article catalogs", () => {
  const results = searchPageDerivedCatalogs("NASA news", fixture(), 8);
  assert.ok(results.some(x => x.matched_records.some(r => r.title === "NASA Announces New Mission")));
});
