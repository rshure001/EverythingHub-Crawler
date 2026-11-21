"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { gzipSync } = require("node:zlib");
const { createHash, webcrypto } = require("node:crypto");
if (!globalThis.crypto) globalThis.crypto = webcrypto;

const modulePromise = import("../../supabase/functions/everything-hub-web-search/page-derived-catalog-preview.mjs");
const digest = (value) => createHash("sha256").update(value).digest("hex");
const jsonBytes = (value) => Buffer.from(JSON.stringify(value));
const response = (value) => new Response(value, { status: 200 });

function fixture() {
  const updatedAt = 1795224887;
  const publicManifest = {
    version: 1,
    integrity: "sha256",
    updated_at: updatedAt,
    total_pages: 100,
    shard_count: 1,
    shards: [{ file: "shard-0001.json.gz", pages: 100, bytes: 100, uncompressed_bytes: 100, sha256: "a".repeat(64) }],
  };
  const catalogs = [
    {
      catalog_id: "catalog-rolex",
      title: "Rolex Watches",
      catalog_type: "commerce",
      record_type: "product",
      subject: "watches",
      source_host: "example.com",
      source_url: "https://example.com/rolex",
      source_page_ids: ["page-rolex"],
      source_page_count: 1,
      record_ids: ["record-rolex"],
      record_count: 1,
      is_source_declared_catalog: true,
      evidence_strength: "high",
      commercial_eligible: false,
      source_terms_status: "not_reviewed",
      record_level_clearance: "not_reviewed",
    },
    {
      catalog_id: "catalog-vegas",
      title: "Nevada Places",
      catalog_type: "geographic",
      record_type: "place",
      subject: "places",
      source_host: "example.net",
      source_url: "https://example.net/nevada",
      source_page_ids: ["page-vegas"],
      source_page_count: 1,
      record_ids: ["record-vegas"],
      record_count: 1,
      is_source_declared_catalog: true,
      evidence_strength: "high",
      commercial_eligible: false,
      source_terms_status: "not_reviewed",
      record_level_clearance: "not_reviewed",
    },
    {
      catalog_id: "catalog-articles",
      title: "General Articles",
      catalog_type: "knowledge_base",
      record_type: "article",
      subject: "articles",
      source_host: "example.org",
      source_url: "https://example.org/articles",
      source_page_ids: ["page-article"],
      source_page_count: 1,
      record_ids: ["record-article"],
      record_count: 1,
      is_source_declared_catalog: true,
      evidence_strength: "high",
      commercial_eligible: false,
      source_terms_status: "not_reviewed",
      record_level_clearance: "not_reviewed",
    },
  ];
  const records = [
    {
      record_id: "record-rolex",
      entity_id: "entity-rolex",
      canonical_entity_key: "product:rolex-submariner",
      record_type: "product",
      title: "Rolex Submariner Date",
      description: "Swiss dive watch",
      source_urls: ["https://example.com/rolex/submariner"],
      source_page_ids: ["page-rolex"],
      catalog_ids: ["catalog-rolex"],
      last_seen_at: "2026-11-21T02:40:00Z",
      attributes: { brand: "Rolex", model: "Submariner Date" },
      commercial_eligible: false,
      source_terms_status: "not_reviewed",
      record_level_clearance: "not_reviewed",
    },
    {
      record_id: "record-vegas",
      entity_id: "entity-vegas",
      canonical_entity_key: "place:las-vegas-strip",
      record_type: "place",
      title: "Las Vegas Strip",
      description: "A destination in Las Vegas, Nevada",
      source_urls: ["https://example.net/las-vegas-strip"],
      source_page_ids: ["page-vegas"],
      catalog_ids: ["catalog-vegas"],
      last_seen_at: "2026-11-21T02:40:00Z",
      attributes: {},
      commercial_eligible: false,
      source_terms_status: "not_reviewed",
      record_level_clearance: "not_reviewed",
    },
    {
      record_id: "record-article",
      entity_id: "entity-article",
      canonical_entity_key: "article:rolex-partnership",
      record_type: "article",
      title: "Rolex conservation partnership article",
      description: "A general article that mentions Rolex",
      source_urls: ["https://example.org/articles/rolex"],
      source_page_ids: ["page-article"],
      catalog_ids: ["catalog-articles"],
      last_seen_at: "2026-11-21T02:40:00Z",
      attributes: {},
      commercial_eligible: false,
      source_terms_status: "not_reviewed",
      record_level_clearance: "not_reviewed",
    },
  ];
  const registryBytes = gzipSync(jsonBytes({
    version: 1,
    status: "candidate_staging_not_production",
    catalogs,
  }));
  const recordsBytes = gzipSync(jsonBytes({ version: 1, records }));
  const manifest = {
    version: 1,
    integrity: "sha256",
    analysis_version: 9,
    generated_at: "2026-11-21T02:50:00Z",
    source_index_pages: 100,
    source_index_updated_at: new Date(updatedAt * 1000).toISOString(),
    source_index_sha256: "d".repeat(64),
    catalog_count: catalogs.length,
    record_count: records.length,
    catalogs_verified: 0,
    metrics: { records_published: 0, source_terms_reviewed_records: 0 },
    files: [
      { file: "registry.json.gz", bytes: registryBytes.length, sha256: digest(registryBytes), items: catalogs.length },
      { file: "records.json.gz", bytes: recordsBytes.length, sha256: digest(recordsBytes), items: records.length },
    ],
  };
  return {
    sha: "b".repeat(40),
    manifest,
    manifestBytes: jsonBytes(manifest),
    publicManifest,
    publicManifestBytes: jsonBytes(publicManifest),
    registryBytes,
    recordsBytes,
  };
}

function mockFetch(data, overrides = {}) {
  const requested = [];
  const fetchImpl = async (input) => {
    const url = String(input);
    requested.push(url);
    if (url.includes("/git/ref/heads/main")) {
      return response(jsonBytes({ object: { sha: data.sha } }));
    }
    if (url.endsWith("/data/page-derived-catalogs/manifest.json")) {
      return response(data.manifestBytes);
    }
    if (url.endsWith("/public-search/manifest.json")) {
      return response(data.publicManifestBytes);
    }
    if (url.endsWith("/data/page-derived-catalogs/registry.json.gz")) {
      return response(overrides.registryBytes || data.registryBytes);
    }
    if (url.endsWith("/data/page-derived-catalogs/records.json.gz")) {
      return response(overrides.recordsBytes || data.recordsBytes);
    }
    return new Response("not found", { status: 404 });
  };
  return { fetchImpl, requested };
}

test.beforeEach(async () => {
  const mod = await modulePromise;
  mod.clearPageDerivedCatalogPreviewCache();
});

test("loads a commit-pinned catalog snapshot and verifies index freshness", async () => {
  const data = fixture();
  const { fetchImpl, requested } = mockFetch(data);
  const mod = await modulePromise;
  const network = await mod.loadPageDerivedCatalogPreview({ fetchImpl });

  assert.equal(network.catalogCount, 3);
  assert.equal(network.recordCount, 3);
  assert.equal(network.sourceIndexPages, 100);
  assert.equal(network.freshnessMatch, true);
  assert.equal(network.stale, false);
  assert.equal(network.gitCommit, data.sha);
  assert.ok(requested.every((url) => !url.includes("/main/")));
  assert.ok(requested.some((url) => url.includes("/" + data.sha + "/data/page-derived-catalogs/registry.json.gz")));
});

test("Rolex ranks a typed product catalog ahead of an incidental article mention", async () => {
  const data = fixture();
  const { fetchImpl } = mockFetch(data);
  const mod = await modulePromise;
  const network = await mod.loadPageDerivedCatalogPreview({ fetchImpl });
  const results = mod.searchPageDerivedCatalogPreview("Rolex", network, 5);

  assert.equal(results[0].catalog_id, "catalog-rolex");
  assert.equal(results[0].relevance_class, "typed_product_match");
  assert.equal(results[0].matched_records[0].title, "Rolex Submariner Date");
  assert.equal(results[0].preview_only, true);
  assert.equal(results[0].commercial_eligible, false);
  assert.equal(results[0].source_terms_status, "not_reviewed");
  assert.equal("description" in results[0].matched_records[0], false);
});

test("Las Vegas ranks a typed place catalog rather than a weak article mention", async () => {
  const data = fixture();
  const { fetchImpl } = mockFetch(data);
  const mod = await modulePromise;
  const network = await mod.loadPageDerivedCatalogPreview({ fetchImpl });
  const results = mod.searchPageDerivedCatalogPreview("Las Vegas", network, 5);

  assert.equal(results[0].catalog_id, "catalog-vegas");
  assert.equal(results[0].relevance_class, "typed_location_match");
  assert.equal(results[0].matched_records[0].title, "Las Vegas Strip");
});

test("rejects a registry whose compressed bytes do not match the manifest SHA-256", async () => {
  const data = fixture();
  const tampered = Buffer.from(data.registryBytes);
  tampered[tampered.length - 1] ^= 1;
  const { fetchImpl } = mockFetch(data, { registryBytes: tampered });
  const mod = await modulePromise;

  await assert.rejects(
    mod.loadPageDerivedCatalogPreview({ fetchImpl }),
    /page_catalog_registry_sha256_mismatch/,
  );
});

test("rejects records with unsafe provenance URLs", async () => {
  const data = fixture();
  const payload = JSON.parse(require("node:zlib").gunzipSync(data.recordsBytes).toString("utf8"));
  payload.records[0].source_urls = ["javascript:alert(1)"];
  const unsafeBytes = gzipSync(jsonBytes(payload));
  data.manifest.files.find((file) => file.file === "records.json.gz").bytes = unsafeBytes.length;
  data.manifest.files.find((file) => file.file === "records.json.gz").sha256 = digest(unsafeBytes);
  data.manifestBytes = jsonBytes(data.manifest);
  const { fetchImpl } = mockFetch(data, { recordsBytes: unsafeBytes });
  const mod = await modulePromise;

  await assert.rejects(
    mod.loadPageDerivedCatalogPreview({ fetchImpl }),
    /page_record_identity_or_provenance_invalid/,
  );
});

test("keeps a structurally valid preview but marks it stale when the page index has advanced", async () => {
  const data = fixture();
  data.publicManifest.updated_at += 120;
  data.publicManifestBytes = jsonBytes(data.publicManifest);
  const { fetchImpl } = mockFetch(data);
  const mod = await modulePromise;
  const network = await mod.loadPageDerivedCatalogPreview({ fetchImpl });

  assert.equal(network.freshnessMatch, false);
  assert.equal(network.sourceIndexPages, 100);
  assert.equal(network.pageIndexPages, 100);
});
