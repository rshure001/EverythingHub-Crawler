import test from "node:test";
import assert from "node:assert/strict";
import {
  clearPageDerivedCatalogPreviewCache,
  loadPageDerivedCatalogPreview,
  searchPageDerivedCatalogPreview,
} from "./page-derived-catalog-preview.mjs";

const encoder = new TextEncoder();
const commit = "a".repeat(40);
const refUrl = "https://api.github.com/repos/rshure001/EverythingHub-Crawler/git/ref/heads/main";
const rawBase = "https://raw.githubusercontent.com/rshure001/EverythingHub-Crawler/" + commit + "/";
const policyReview = {
  schema_version: 1,
  register_sha256: "d".repeat(64),
  scope: "source-level-only",
  record_level_clearance: "not_reviewed",
  commercial_eligibility: false,
  republication: "not_cleared",
};

async function digest(bytes) {
  const result = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(result)).map((value) => value.toString(16).padStart(2, "0")).join("");
}

async function gzipJson(value) {
  const stream = new Blob([JSON.stringify(value)]).stream().pipeThrough(new CompressionStream("gzip"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

async function fileEntry(file, bytes, items) {
  return { file, bytes: bytes.byteLength, sha256: await digest(bytes), items };
}

function record({ id, entityId, type, title, description, catalogId, sourceUrl, attributes = {} }) {
  return {
    record_id: id,
    entity_id: entityId,
    record_type: type,
    title,
    description,
    attributes,
    catalog_ids: [catalogId],
    source_urls: [sourceUrl],
    source_page_ids: ["page:" + id],
    last_seen_at: "2026-11-21T05:40:41Z",
    source_policy_review_status: "not_reviewed",
    source_terms_status: "not_reviewed",
    record_level_clearance: "not_reviewed",
    commercial_eligible: false,
  };
}

async function makeFixture() {
  const rolexId = "catalog:rolex-models";
  const vegasId = "catalog:las-vegas-places";
  const rolexRecord = record({
    id: "record:rolex-submariner",
    entityId: "entity:rolex-submariner",
    type: "model",
    title: "Rolex Submariner",
    description: "Rolex dive watch model",
    catalogId: rolexId,
    sourceUrl: "https://www.rolex.com/watches/submariner",
    attributes: { brand: "Rolex", model: "Submariner", sku: "SUB-41" },
  });
  const vegasRecord = record({
    id: "record:las-vegas-strip",
    entityId: "entity:las-vegas-strip",
    type: "place",
    title: "Las Vegas Strip",
    description: "Major destination in Las Vegas, Nevada",
    catalogId: vegasId,
    sourceUrl: "https://www.visitlasvegas.com/places/las-vegas-strip",
  });
  const rolexPath = "indexes/aa/aaaaaaaaaaaaaaaaaaaaaaaa.json.gz";
  const vegasPath = "indexes/bb/bbbbbbbbbbbbbbbbbbbbbbbb.json.gz";
  const catalogs = [
    {
      catalog_id: rolexId,
      title: "Rolex Watch Models",
      subject: "Rolex watches",
      source_host: "rolex.com",
      source_url: "https://www.rolex.com/watches",
      catalog_type: "commerce",
      record_type: "model",
      record_ids: [rolexRecord.record_id],
      source_page_ids: ["page:rolex"],
      record_count: 1,
      source_page_count: 1,
      index_file: rolexPath,
      is_source_declared_catalog: true,
      evidence_strength: "high",
      source_policy_review_status: "not_reviewed",
      source_terms_status: "not_reviewed",
      record_level_clearance: "not_reviewed",
      commercial_eligible: false,
    },
    {
      catalog_id: vegasId,
      title: "Las Vegas Places",
      subject: "Las Vegas",
      source_host: "visitlasvegas.com",
      source_url: "https://www.visitlasvegas.com/places",
      catalog_type: "geographic",
      record_type: "place",
      record_ids: [vegasRecord.record_id],
      source_page_ids: ["page:vegas"],
      record_count: 1,
      source_page_count: 1,
      index_file: vegasPath,
      is_source_declared_catalog: true,
      evidence_strength: "high",
      source_policy_review_status: "not_reviewed",
      source_terms_status: "not_reviewed",
      record_level_clearance: "not_reviewed",
      commercial_eligible: false,
    },
  ];
  const registry = {
    version: 1,
    analysis_version: 10,
    status: "candidate_staging_not_production",
    source_policy_review: policyReview,
    catalogs,
  };
  const registryBytes = await gzipJson(registry);
  const rolexIndexBytes = await gzipJson({
    version: 1, catalog_id: rolexId, title: "Rolex Watch Models", status: "candidate", records: [rolexRecord],
  });
  const vegasIndexBytes = await gzipJson({
    version: 1, catalog_id: vegasId, title: "Las Vegas Places", status: "candidate", records: [vegasRecord],
  });

  const tokens = {
    rolex: [rolexId],
    submariner: [rolexId],
    las: [vegasId],
    vegas: [vegasId],
    strip: [vegasId],
  };
  const grouped = new Map();
  for (const [token, ids] of Object.entries(tokens)) {
    const prefix = (await digest(encoder.encode(token))).slice(0, 2);
    if (!grouped.has(prefix)) grouped.set(prefix, {});
    grouped.get(prefix)[token] = ids;
  }
  const searchBytes = new Map();
  for (const [prefix, shardTokens] of grouped) {
    searchBytes.set(prefix, await gzipJson({ version: 1, prefix, tokens: shardTokens }));
  }

  const placeholderRecords = new Uint8Array([1]);
  const files = [
    await fileEntry("registry.json.gz", registryBytes, catalogs.length),
    await fileEntry("records.json.gz", placeholderRecords, 2),
    await fileEntry(rolexPath, rolexIndexBytes, 1),
    await fileEntry(vegasPath, vegasIndexBytes, 1),
  ];
  for (const [prefix, bytes] of searchBytes) {
    files.push(await fileEntry("search-index/" + prefix + ".json.gz", bytes, Object.keys(grouped.get(prefix)).length));
  }
  const updatedAt = 1795239641;
  const updatedIso = new Date(updatedAt * 1000).toISOString();
  const manifest = {
    version: 1,
    analysis_version: 10,
    integrity: "sha256",
    source_index_pages: 144241,
    source_index_updated_at: updatedIso,
    source_index_sha256: "c".repeat(64),
    catalog_count: catalogs.length,
    record_count: 2,
    generated_at: "2026-11-21T05:55:00Z",
    metrics: { records_published: 0, source_terms_reviewed_records: 0 },
    catalogs_verified: 0,
    source_policy_review: policyReview,
    files,
  };
  const publicManifest = {
    version: 1,
    integrity: "sha256",
    total_pages: 144241,
    updated_at: updatedAt,
  };
  const responses = new Map();
  const jsonResponse = (value) => new Response(JSON.stringify(value), {
    headers: { "Content-Type": "application/json" },
  });
  responses.set(refUrl, jsonResponse({ object: { sha: commit } }));
  responses.set(rawBase + "data/page-derived-catalogs/manifest.json", jsonResponse(manifest));
  responses.set(rawBase + "public-search/manifest.json", jsonResponse(publicManifest));
  responses.set(rawBase + "data/page-derived-catalogs/registry.json.gz", new Response(registryBytes));
  responses.set(rawBase + "data/page-derived-catalogs/" + rolexPath, new Response(rolexIndexBytes));
  responses.set(rawBase + "data/page-derived-catalogs/" + vegasPath, new Response(vegasIndexBytes));
  for (const [prefix, bytes] of searchBytes) {
    responses.set(rawBase + "data/page-derived-catalogs/search-index/" + prefix + ".json.gz", new Response(bytes));
  }
  const requested = [];
  const fetchImpl = async (url) => {
    const key = String(url);
    requested.push(key);
    const response = responses.get(key);
    if (!response) throw new Error("Unexpected test fetch: " + key);
    return response.clone();
  };
  return { fetchImpl, requested, rolexPath, vegasPath };
}

test("query-scoped catalog search fetches only matching per-catalog indexes", async () => {
  clearPageDerivedCatalogPreviewCache();
  const fixture = await makeFixture();
  const network = await loadPageDerivedCatalogPreview({ fetchImpl: fixture.fetchImpl });

  const rolex = await searchPageDerivedCatalogPreview("Rolex", network, 5, fixture.fetchImpl);
  assert.equal(rolex.length, 1);
  assert.equal(rolex[0].catalog_id, "catalog:rolex-models");
  assert.equal(rolex[0].relevance_class, "typed_product_match");
  assert.equal(rolex[0].matched_records[0].record_id, "record:rolex-submariner");

  const vegas = await searchPageDerivedCatalogPreview("Las Vegas", network, 5, fixture.fetchImpl);
  assert.equal(vegas.length, 1);
  assert.equal(vegas[0].catalog_id, "catalog:las-vegas-places");
  assert.equal(vegas[0].relevance_class, "typed_location_match");
  assert.equal(vegas[0].matched_records[0].record_id, "record:las-vegas-strip");

  assert.equal(fixture.requested.some((url) => url.endsWith("/records.json.gz")), false);
  assert.equal(fixture.requested.some((url) => url.endsWith("/" + fixture.rolexPath)), true);
  assert.equal(fixture.requested.some((url) => url.endsWith("/" + fixture.vegasPath)), true);
  clearPageDerivedCatalogPreviewCache();
});
