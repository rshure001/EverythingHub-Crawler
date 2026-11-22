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
      catalog("catalog:vegas-places", "Las Vegas Places", "geographic", "place", "Las Vegas"),
      catalog("catalog:vegas-events", "Homeland Security Events", "events", "event", ""),
      
    ],
    records: [
      record("r-rolex-article", "National Geographic and Rolex Committed to a Perpetual Planet", "article", ["catalog:rolex-articles"]),
      record("r-rolex-brand", "Rolex", "brand", ["catalog:rolex-brand"]),
      record("r-nasa-humanity", "Benefits to Humanity - NASA", "subject", ["catalog:nasa-humanity"]),
      record("r-nasa-org", "NASA", "organization", ["catalog:nasa-org"]),
      record("r-vegas-article", "Chandelier bars and old-school kitsch: the best places to stay in Las Vegas", "article", ["catalog:vegas-articles"]),
      record("r-vegas-place", "Las Vegas Strip", "place", ["catalog:vegas-places"]),
      record("r-vegas-event", "Las Vegas Events Calendar", "event", ["catalog:vegas-events"]),
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
  assert.equal(results[0].relevance_class, "typed_organization_match");
});

test("Las Vegas prefers a typed geographic catalog and excludes unrelated article and event catalogs", () => {
  const results = searchPageDerivedCatalogs("Las Vegas", fixture(), 8);
  assert.deepEqual(results.map(x => x.catalog_id), ["catalog:vegas-places"]);
  assert.equal(results[0].relevance_class, "typed_location_match");
});

test("general location searches suppress event-only catalogs when no place record is in the query shard", () => {
  const network = {
    catalogs: [catalog("catalog:vegas-events", "Homeland Security Events", "events", "event", "")],
    records: [record("r-vegas-event", "Las Vegas Events Calendar", "event", ["catalog:vegas-events"])]
  };
  assert.deepEqual(searchPageDerivedCatalogs("Las Vegas", network, 8), []);
});

test("explicit Las Vegas events intent still returns the matching event catalog", () => {
  const results = searchPageDerivedCatalogs("Las Vegas events", fixture(), 8);
  assert.deepEqual(results.map(x => x.catalog_id), ["catalog:vegas-events"]);
  assert.equal(results[0].relevance_class, "typed_event_match");
});

test("explicit content intent still permits relevant article catalogs", () => {
  const results = searchPageDerivedCatalogs("NASA news", fixture(), 8);
  assert.ok(results.some(x => x.matched_records.some(r => r.title === "NASA Announces New Mission")));
});


test("Las Vegas typed location search can match a business with explicit locality metadata", () => {
  const business = record("r-vegas-business", "Example hotel", "business", ["catalog:global-businesses"], "Hotel information");
  business.attributes = {
    address_locality: "Las Vegas",
    address_region: "Nevada",
    address_country: "United States"
  };
  const network = {
    catalogs: [catalog("catalog:global-businesses", "Global Business Directory", "business_directory", "business", "business")],
    records: [business]
  };
  const results = searchPageDerivedCatalogs("Las Vegas", network, 5);
  assert.deepEqual(results.map(item => item.catalog_id), ["catalog:global-businesses"]);
  assert.equal(results[0].relevance_class, "typed_location_match");
});

test("a business description mentioning Las Vegas is not treated as a typed location without locality metadata", () => {
  const network = {
    catalogs: [catalog("catalog:global-businesses", "Global Business Directory", "business_directory", "business", "business")],
    records: [record("r-vegas-business", "Example hotel", "business", ["catalog:global-businesses"], "A hotel that mentions Las Vegas in an article")]
  };
  assert.deepEqual(searchPageDerivedCatalogs("Las Vegas", network, 5), []);
});


test("Las Vegas location intent suppresses incidental commerce matches from local offer titles", () => {
  const network = {
    catalogs: [
      catalog("catalog:global-products", "Global Product Directory", "commerce", "product", "products"),
      catalog("catalog:global-places", "Global Place Directory", "geographic", "place", "places")
    ],
    records: [
      record("r-vegas-offer", "Flamingo Las Vegas Daily Dining Credit", "product", ["catalog:global-products"]),
      record("r-vegas-strip", "Las Vegas Strip", "place", ["catalog:global-places"])
    ]
  };
  const results = searchPageDerivedCatalogs("Las Vegas", network, 5);
  assert.deepEqual(results.map(item => item.catalog_id), ["catalog:global-places"]);
  assert.equal(results[0].relevance_class, "typed_location_match");
});

test("explicit product intent still permits a product catalog for a place-qualified query", () => {
  const network = {
    catalogs: [catalog("catalog:vegas-watches", "Las Vegas Watch Catalog", "commerce", "product", "watches")],
    records: [record("r-vegas-watch", "Las Vegas watches", "product", ["catalog:vegas-watches"])]
  };
  const results = searchPageDerivedCatalogs("Las Vegas watches", network, 5);
  assert.deepEqual(results.map(item => item.catalog_id), ["catalog:vegas-watches"]);
  assert.equal(results[0].relevance_class, "typed_product_match");
});
