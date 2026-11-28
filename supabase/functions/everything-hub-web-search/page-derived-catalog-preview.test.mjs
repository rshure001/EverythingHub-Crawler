import test from "node:test";
import assert from "node:assert/strict";
import {
  clearPageDerivedCatalogPreviewCache,
  loadPageDerivedCatalogPreview,
  searchPageDerivedCatalogPreview,
  resolveRecordRelatedCatalogPreview,
  validateManifest,
  isValidCatalogIndexManifestEntry,
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

test("accepts current 3.72 MB catalog indexes within the bounded fetch limit", () => {
  assert.equal(isValidCatalogIndexManifestEntry({ bytes: 3_718_832, items: 23_108 }, 23_108), true);
  assert.equal(isValidCatalogIndexManifestEntry({ bytes: 4_500_001, items: 23_108 }, 23_108), false);
  assert.equal(isValidCatalogIndexManifestEntry({ bytes: 3_718_832, items: 23_107 }, 23_108), false);
  assert.equal(isValidCatalogIndexManifestEntry({ bytes: 0, items: 23_108 }, 23_108), false);
});

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

test("manifest validation accepts 19,866 per-catalog indexes, 256 search shards, and shared artifacts", () => {
  const sha = "a".repeat(64);
  const baseFiles = [
    { file: "pages.json.gz", bytes: 1, sha256: sha, items: 176151 },
    { file: "page-catalogs.jsonl.gz", bytes: 1, sha256: sha, items: 0 },
    { file: "record-catalogs.jsonl.gz", bytes: 1, sha256: sha, items: 0 },
    { file: "registry.json.gz", bytes: 1, sha256: sha, items: 19866 },
    { file: "records.json.gz", bytes: 1, sha256: sha, items: 1011743 },
    { file: "media-references.json.gz", bytes: 1, sha256: sha, items: 7893 },
  ];
  const catalogFiles = Array.from({ length: 19866 }, (_, index) => {
    const id = index.toString(16).padStart(24, "0");
    return { file: "indexes/" + id.slice(0, 2) + "/" + id + ".json.gz", bytes: 1, sha256: sha, items: 0 };
  });
  const searchFiles = Array.from({ length: 256 }, (_, index) => ({
    file: "search-index/" + index.toString(16).padStart(2, "0") + ".json.gz",
    bytes: 1,
    sha256: sha,
    items: 0,
  }));
  const files = [...baseFiles, ...catalogFiles, ...searchFiles];
  assert.equal(files.length, 20128);
  const metadata = validateManifest({
    version: 1,
    analysis_version: 15,
    integrity: "sha256",
    source_index_pages: 176642,
    source_index_updated_at: "2026-11-22T07:43:23Z",
    source_index_sha256: sha,
    catalog_count: 19866,
    record_count: 1011743,
    generated_at: "2026-11-22T07:43:23Z",
    metrics: { records_published: 0 },
    catalogs_verified: 0,
    files,
  });
  assert.equal(metadata.files.size, 20128);
  assert.equal(metadata.searchIndexFiles.length, 256);
});

function record({ id, entityId, type, title, description, catalogId, sourceUrl, attributes = {}, recordCatalogId }) {
  return {
    record_id: id,
    record_catalog_id: recordCatalogId === undefined ? "record-catalog:" + id : recordCatalogId,
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
  const vegasProductId = "catalog:las-vegas-watch-products";
  const genericSubjectId = "catalog:global-geographic-subject";
  const nasaSubjectId = "catalog:nasa-subject";
  const vegasSubjectId = "catalog:vegas-subject";
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
  const vegasProductRecord = record({
    id: "record:las-vegas-watch",
    entityId: "entity:las-vegas-watch",
    type: "product",
    title: "Las Vegas Watch",
    description: "Watch product available in Las Vegas",
    catalogId: vegasProductId,
    sourceUrl: "https://shop.example.com/las-vegas-watch",
    attributes: { brand: "Example", category: "Watches" },
  });
  const genericSubjectRecord = record({
    id: "record:geographic-rolex-mention",
    entityId: "entity:geographic",
    type: "subject",
    title: "National Geographic and Rolex Committed to a Perpetual Planet",
    description: "A National Geographic and Rolex conservation partnership",
    catalogId: genericSubjectId,
    sourceUrl: "https://www.nationalgeographic.com/environment/article/rolex-partnership",
  });
  const nasaSubjectRecord = record({
    id: "record:nasa-artemis",
    entityId: "entity:nasa",
    type: "subject",
    title: "NASA Artemis Mission",
    description: "NASA exploration program",
    catalogId: nasaSubjectId,
    sourceUrl: "https://www.nasa.gov/mission/artemis",
  });
  const vegasSubjectRecord = record({
    id: "record:vegas-subject",
    entityId: "entity:vegas",
    type: "subject",
    title: "Las Vegas Strip",
    description: "Las Vegas destination and landmarks",
    catalogId: vegasSubjectId,
    sourceUrl: "https://www.visitlasvegas.com/places/las-vegas-strip",
  });
  const catalogIndexPath = async (catalogId) => {
    const hash = await digest(encoder.encode(catalogId));
    return "indexes/" + hash.slice(0, 2) + "/" + hash.slice(0, 24) + ".json.gz";
  };
  const rolexPath = await catalogIndexPath(rolexId);
  const vegasPath = await catalogIndexPath(vegasId);
  const vegasProductPath = await catalogIndexPath(vegasProductId);
  const genericSubjectPath = await catalogIndexPath(genericSubjectId);
  const nasaSubjectPath = await catalogIndexPath(nasaSubjectId);
  const vegasSubjectPath = await catalogIndexPath(vegasSubjectId);
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

    {
      catalog_id: vegasProductId,
      title: "Las Vegas Watch Products",
      subject: "Las Vegas shopping",
      source_host: "shop.example.com",
      source_url: "https://shop.example.com/las-vegas-watch",
      catalog_type: "commerce",
      record_type: "product",
      record_ids: [vegasProductRecord.record_id],
      source_page_ids: ["page:vegas-product"],
      record_count: 1,
      source_page_count: 1,
      index_file: vegasProductPath,
      is_source_declared_catalog: true,
      evidence_strength: "high",
      source_policy_review_status: "not_reviewed",
      source_terms_status: "not_reviewed",
      record_level_clearance: "not_reviewed",
      commercial_eligible: false,
    },
    {
      catalog_id: genericSubjectId,
      title: "Geographic Subject Catalog",
      subject: "geographic",
      source_host: "__global__",
      source_url: "https://www.nationalgeographic.com/",
      catalog_type: "knowledge_base",
      record_type: "subject",
      catalog_origin: "derived_from_repeated_records",
      record_ids: [genericSubjectRecord.record_id],
      source_page_ids: ["page:geographic-rolex"],
      record_count: 1,
      source_page_count: 1,
      index_file: genericSubjectPath,
      is_source_declared_catalog: false,
      evidence_strength: "medium",
      source_policy_review_status: "not_reviewed",
      source_terms_status: "not_reviewed",
      record_level_clearance: "not_reviewed",
      commercial_eligible: false,
    },
    {
      catalog_id: nasaSubjectId,
      title: "NASA Subject Catalog",
      subject: "nasa",
      source_host: "__global__",
      source_url: "https://www.nasa.gov/",
      catalog_type: "knowledge_base",
      record_type: "subject",
      catalog_origin: "derived_from_repeated_records",
      record_ids: [nasaSubjectRecord.record_id],
      source_page_ids: ["page:nasa"],
      record_count: 1,
      source_page_count: 1,
      index_file: nasaSubjectPath,
      is_source_declared_catalog: false,
      evidence_strength: "medium",
      source_policy_review_status: "not_reviewed",
      source_terms_status: "not_reviewed",
      record_level_clearance: "not_reviewed",
      commercial_eligible: false,
    },
    {
      catalog_id: vegasSubjectId,
      title: "Vegas Subject Catalog",
      subject: "vegas",
      source_host: "__global__",
      source_url: "https://www.visitlasvegas.com/",
      catalog_type: "knowledge_base",
      record_type: "subject",
      catalog_origin: "derived_from_repeated_records",
      record_ids: [vegasSubjectRecord.record_id],
      source_page_ids: ["page:vegas-subject"],
      record_count: 1,
      source_page_count: 1,
      index_file: vegasSubjectPath,
      is_source_declared_catalog: false,
      evidence_strength: "medium",
      source_policy_review_status: "not_reviewed",
      source_terms_status: "not_reviewed",
      record_level_clearance: "not_reviewed",
      commercial_eligible: false,
    },
  ];
  const registry = {
    version: 1,
    analysis_version: 21,
    status: "candidate_staging_not_production",
    source_policy_review: policyReview,
    catalogs,
  };
  const registryBytes = await gzipJson(registry);
  const registryShardGroups = new Map();
  for (const catalog of catalogs) {
    const prefix = (await digest(encoder.encode(catalog.catalog_id))).slice(0, 2);
    if (!registryShardGroups.has(prefix)) registryShardGroups.set(prefix, []);
    const compactCatalog = Object.fromEntries(
      Object.entries(catalog).filter(([key]) => !["record_ids", "source_page_ids"].includes(key)),
    );
    registryShardGroups.get(prefix).push(compactCatalog);
  }
  const registryShardBytes = new Map();
  for (const [prefix, shardCatalogs] of registryShardGroups) {
    registryShardBytes.set(prefix, await gzipJson({
      version: 1,
      analysis_version: 21,
      status: "candidate_staging_not_production",
      source_policy_review: policyReview,
      catalogs: shardCatalogs,
    }));
  }
  const rolexIndexBytes = await gzipJson({
    version: 1, catalog_id: rolexId, title: "Rolex Watch Models", status: "candidate", records: [rolexRecord],
  });
  const vegasIndexBytes = await gzipJson({
    version: 1, catalog_id: vegasId, title: "Las Vegas Places", status: "candidate", records: [vegasRecord],
  });
  const vegasProductIndexBytes = await gzipJson({
    version: 1, catalog_id: vegasProductId, title: "Las Vegas Watch Products", status: "candidate", records: [vegasProductRecord],
  });
  const genericSubjectIndexBytes = await gzipJson({
    version: 1, catalog_id: genericSubjectId, title: "Geographic Subject Catalog", status: "candidate", records: [genericSubjectRecord],
  });
  const nasaSubjectIndexBytes = await gzipJson({
    version: 1, catalog_id: nasaSubjectId, title: "NASA Subject Catalog", status: "candidate", records: [nasaSubjectRecord],
  });
  const vegasSubjectIndexBytes = await gzipJson({
    version: 1, catalog_id: vegasSubjectId, title: "Vegas Subject Catalog", status: "candidate", records: [vegasSubjectRecord],
  });

  const tokens = {
    rolex: [rolexId, genericSubjectId],
    submariner: [rolexId],
    national: [genericSubjectId],
    geographic: [genericSubjectId],
    las: [vegasId, vegasProductId, vegasSubjectId],
    vegas: [vegasId, vegasProductId, vegasSubjectId],
    strip: [vegasId, vegasSubjectId],
    watch: [vegasProductId],
    product: [vegasProductId],
    nasa: [nasaSubjectId],
    shared: [rolexId, vegasId, vegasProductId, genericSubjectId, nasaSubjectId, vegasSubjectId],
    artemis: [nasaSubjectId],
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
    await fileEntry("records/00000.json.gz", placeholderRecords, 6),
    await fileEntry(rolexPath, rolexIndexBytes, 1),
    await fileEntry(vegasPath, vegasIndexBytes, 1),
    await fileEntry(vegasProductPath, vegasProductIndexBytes, 1),
    await fileEntry(genericSubjectPath, genericSubjectIndexBytes, 1),
    await fileEntry(nasaSubjectPath, nasaSubjectIndexBytes, 1),
    await fileEntry(vegasSubjectPath, vegasSubjectIndexBytes, 1),
  ];
  for (const [prefix, bytes] of registryShardBytes) {
    files.push(await fileEntry("registry-shards/" + prefix + ".json.gz", bytes, registryShardGroups.get(prefix).length));
  }
  for (const [prefix, bytes] of searchBytes) {
    files.push(await fileEntry("search-index/" + prefix + ".json.gz", bytes, Object.keys(grouped.get(prefix)).length));
  }
  const updatedAt = 1795239641;
  const updatedIso = new Date(updatedAt * 1000).toISOString();
  const manifest = {
    version: 1,
    analysis_version: 21,
    integrity: "sha256",
    source_index_pages: 144241,
    source_index_updated_at: updatedIso,
    source_index_sha256: "c".repeat(64),
    catalog_count: catalogs.length,
    record_count: 6,
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
  for (const [prefix, bytes] of registryShardBytes) {
    responses.set(rawBase + "data/page-derived-catalogs/registry-shards/" + prefix + ".json.gz", new Response(bytes));
  }
  responses.set(rawBase + "data/page-derived-catalogs/" + rolexPath, new Response(rolexIndexBytes));
  responses.set(rawBase + "data/page-derived-catalogs/" + vegasPath, new Response(vegasIndexBytes));
  responses.set(rawBase + "data/page-derived-catalogs/" + vegasProductPath, new Response(vegasProductIndexBytes));
  responses.set(rawBase + "data/page-derived-catalogs/" + genericSubjectPath, new Response(genericSubjectIndexBytes));
  responses.set(rawBase + "data/page-derived-catalogs/" + nasaSubjectPath, new Response(nasaSubjectIndexBytes));
  responses.set(rawBase + "data/page-derived-catalogs/" + vegasSubjectPath, new Response(vegasSubjectIndexBytes));
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
  return { fetchImpl, requested, rolexPath, vegasPath, vegasProductPath, genericSubjectPath, nasaSubjectPath, vegasSubjectPath };
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
  assert.deepEqual(rolex[0].matched_records[0].shared_catalog_ids, ["catalog:rolex-models"]);
  assert.equal(rolex.some((item) => item.catalog_id === "catalog:global-geographic-subject"), false);
  const rolexWithStopword = await searchPageDerivedCatalogPreview("The Rolex", network, 5, fixture.fetchImpl);
  assert.equal(rolexWithStopword[0].catalog_id, "catalog:rolex-models");

  const vegas = await searchPageDerivedCatalogPreview("Las Vegas", network, 5, fixture.fetchImpl);
  assert.ok(vegas.length >= 1);
  assert.equal(vegas[0].catalog_id, "catalog:las-vegas-places");
  assert.equal(vegas[0].relevance_class, "typed_location_match");
  assert.equal(vegas[0].matched_records[0].record_id, "record:las-vegas-strip");
  assert.ok(vegas.some((item) => item.catalog_id === "catalog:vegas-subject"));

  const nasa = await searchPageDerivedCatalogPreview("NASA", network, 5, fixture.fetchImpl);
  assert.equal(nasa.length, 1);
  assert.equal(nasa[0].catalog_id, "catalog:nasa-subject");
  assert.equal(nasa[0].matched_records[0].record_id, "record:nasa-artemis");

  assert.equal(fixture.requested.some((url) => url.endsWith("/records.json.gz")), false);
  assert.equal(fixture.requested.some((url) => url.includes("/records/")), false);
  assert.equal(fixture.requested.some((url) => url.endsWith("/registry.json.gz")), false);
  assert.equal(fixture.requested.some((url) => url.includes("/registry-shards/")), true);
  assert.equal(fixture.requested.some((url) => url.endsWith("/" + fixture.rolexPath)), true);
  assert.equal(fixture.requested.some((url) => url.endsWith("/" + fixture.vegasPath)), true);
  assert.equal(fixture.requested.some((url) => url.endsWith("/" + fixture.vegasProductPath)), true);
  assert.equal(fixture.requested.some((url) => url.endsWith("/" + fixture.genericSubjectPath)), true);
  assert.equal(fixture.requested.some((url) => url.endsWith("/" + fixture.nasaSubjectPath)), true);
  assert.equal(fixture.requested.some((url) => url.endsWith("/" + fixture.vegasSubjectPath)), true);
  clearPageDerivedCatalogPreviewCache();
});
test("broad catalog terms load at most four registry metadata shards", async () => {
  clearPageDerivedCatalogPreviewCache();
  const fixture = await makeFixture();
  const network = await loadPageDerivedCatalogPreview({ fetchImpl: fixture.fetchImpl });
  fixture.requested.length = 0;

  await searchPageDerivedCatalogPreview("shared", network, 5, fixture.fetchImpl);

  const firstFourIds = [
    "catalog:rolex-models",
    "catalog:las-vegas-places",
    "catalog:las-vegas-watch-products",
    "catalog:global-geographic-subject",
  ];
  const allowedPrefixes = new Set();
  for (const id of firstFourIds) allowedPrefixes.add((await digest(encoder.encode(id))).slice(0, 2));

  const registryRequests = fixture.requested.filter((url) => url.includes("/registry-shards/"));
  assert.ok(registryRequests.length > 0);
  assert.ok(registryRequests.length <= allowedPrefixes.size);
  for (const url of registryRequests) {
    const suffix = url.split("/registry-shards/")[1] || "";
    const prefix = suffix.split(".")[0];
    assert.match(prefix, /^[a-f0-9]{2}$/);
    assert.ok(allowedPrefixes.has(prefix), "broad search must not fan out beyond the first four candidate IDs");
  }
});

test("per-record related catalogs deduplicate shared records and exclude their owner", async () => {
  clearPageDerivedCatalogPreviewCache();
  const catalogId = "catalog:shared-products";
  const path = "indexes/ab/abababababababababababab.json.gz";
  const owner = record({
    id: "record:owner",
    entityId: "entity:owner",
    type: "model",
    title: "Rolex Submariner",
    description: "Owner record",
    catalogId,
    sourceUrl: "https://www.rolex.com/watches/submariner",
    recordCatalogId: "",
  });
  const relatedA = record({
    id: "record:related-a",
    entityId: "entity:related-a",
    type: "model",
    title: "Rolex Sea-Dweller",
    description: "Related model",
    catalogId,
    sourceUrl: "https://www.rolex.com/watches/sea-dweller",
  });
  const relatedB = record({
    id: "record:related-b",
    entityId: "entity:related-b",
    type: "model",
    title: "Rolex GMT-Master II",
    description: "Related model",
    catalogId,
    sourceUrl: "https://www.rolex.com/watches/gmt-master-ii",
  });
  const records = [owner, relatedA, relatedB];
  const bytes = await gzipJson({
    version: 1,
    catalog_id: catalogId,
    title: "Shared Rolex Models",
    status: "candidate",
    records,
  });
  const entry = await fileEntry(path, bytes, records.length);
  const catalog = {
    catalog_id: catalogId,
    index_file: path,
    record_ids: records.map((item) => item.record_id),
    record_count: records.length,
  };
  const network = {
    catalogsById: new Map([[catalogId, catalog]]),
    filesByPath: new Map([[path, entry]]),
    gitCommit: commit,
    baseUrl: rawBase + "data/page-derived-catalogs/",
    sourcePolicyReview: policyReview,
  };
  const fetchImpl = async (url) => {
    assert.equal(String(url), network.baseUrl + path);
    return new Response(bytes);
  };
  const first = await resolveRecordRelatedCatalogPreview(
    owner.record_id, [catalogId], network, 1, 0, fetchImpl,
  );
  assert.equal(first.owner_record.record_id, owner.record_id);
  assert.equal(first.owner_record.record_catalog_id, owner.record_id);
  assert.equal(first.related_count, 2);
  assert.equal(first.related_records.length, 1);
  assert.equal(first.related_records[0].record_id, relatedB.record_id);
  assert.equal(first.related_records[0].record_catalog_id, relatedB.record_id);
  assert.equal(first.has_more, true);
  const second = await resolveRecordRelatedCatalogPreview(
    owner.record_catalog_id, [catalogId], network, 1, 1, fetchImpl,
  );
  assert.equal(second.related_records[0].record_id, relatedA.record_id);
  assert.equal(await resolveRecordRelatedCatalogPreview("record-catalog:missing", [catalogId], network, 10, 0, fetchImpl), null);
  clearPageDerivedCatalogPreviewCache();
});

