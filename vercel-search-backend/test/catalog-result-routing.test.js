"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const handler = require("../src/api/search");
const { mergeCatalogResults } = handler._test;
const source = fs.readFileSync(path.join(__dirname, "../src/api/search.js"), "utf8");

test("external link-only catalogs do not suppress page-derived catalog previews", () => {
  const external = [{
    catalog_id: "external:las-vegas:official-resources",
    source_kind: "external_link_only",
    source_page_count: 0,
    preview_only: true,
    commercial_eligible: false
  }];
  const pageDerived = [{
    catalog_id: "catalog:las-vegas-places",
    catalog_origin: "derived",
    source_page_count: 14,
    preview_only: true,
    commercial_eligible: false
  }];
  const results = mergeCatalogResults(external, pageDerived);
  assert.deepEqual(results.map(item => item.catalog_id), [
    "external:las-vegas:official-resources",
    "catalog:las-vegas-places"
  ]);
  assert.equal(results[1].source_page_count, 14);
});

test("catalog result merge deduplicates stable IDs and respects the five-card limit", () => {
  const external = [{ catalog_id: "external:one" }, { catalog_id: "external:two" }];
  const derived = [
    { catalog_id: "external:one" },
    { catalog_id: "page:one" },
    { catalog_id: "page:two" },
    { catalog_id: "page:three" },
    { catalog_id: "page:four" }
  ];
  const results = mergeCatalogResults(external, derived);
  assert.equal(results.length, 5);
  assert.equal(new Set(results.map(item => item.catalog_id)).size, 5);
  assert.deepEqual(results.map(item => item.catalog_id), [
    "external:one", "external:two", "page:one", "page:two", "page:three"
  ]);
});

test("catalog result merge preserves external, universal, and page-derived catalog sources", () => {
  const external = [{ catalog_id: "external:rolex" }];
  const universal = [{ catalog_id: "catalog:las-vegas-transit" }];
  const pageDerived = [
    { catalog_id: "catalog:las-vegas-transit" },
    { catalog_id: "page:las-vegas-places" }
  ];
  const results = mergeCatalogResults(external, universal, pageDerived);
  assert.deepEqual(results.map(item => item.catalog_id), [
    "external:rolex", "catalog:las-vegas-transit", "page:las-vegas-places"
  ]);
});

test("universal catalog search is bounded and does not replace the page-derived preview", () => {
  assert.match(source, /withTimeout\(loadCatalog\(query\), CATALOG_LOAD_BUDGET_MS/);
  assert.match(source, /withTimeout\(loadPageDerivedCatalogs\(query\), PAGE_DERIVED_LOAD_BUDGET_MS/);
  assert.match(source, /withTimeout\(loadUniversalCatalogNetwork\(\), UNIVERSAL_CATALOG_LOAD_BUDGET_MS/);
  assert.match(source, /mergeCatalogResults\(externalCatalogResults, universalCatalogResults, pageDerivedCatalogResults\)/);
  assert.doesNotMatch(source, /earlyExternalCatalogResults/);
});
