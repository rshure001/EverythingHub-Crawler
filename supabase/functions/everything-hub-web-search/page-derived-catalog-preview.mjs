import { readBoundedText } from "./bounded-stream.mjs";

const REF_URL = "https://api.github.com/repos/rshure001/EverythingHub-Crawler/git/ref/heads/main";
const RAW_BASE = "https://raw.githubusercontent.com/rshure001/EverythingHub-Crawler/";
const MAX_REF_BYTES = 16_000;
const MAX_MANIFEST_BYTES = 16_000_000;
const MAX_PUBLIC_MANIFEST_BYTES = 1_000_000;
const MAX_REGISTRY_GZIP_BYTES = 8_000_000;
const MAX_RECORDS_GZIP_BYTES = 32_000_000;
const MAX_REGISTRY_JSON_BYTES = 48_000_000;
const MAX_REGISTRY_SHARD_GZIP_BYTES = 2_000_000;
const MAX_REGISTRY_SHARD_JSON_BYTES = 12_000_000;
const MAX_RECORDS_JSON_BYTES = 96_000_000;
const MAX_SEARCH_INDEX_SHARD_BYTES = 4_000_000;
const MAX_SEARCH_INDEX_JSON_BYTES = 16_000_000;
// The corpus contains multi-megabyte indexes; keep each request's fan-out bounded.
const MAX_CATALOG_INDEX_GZIP_BYTES = 4_500_000;
const MAX_CATALOG_INDEX_JSON_BYTES = 20_000_000;
// Keep broad queries from loading registry shards for the entire catalog corpus.
const MAX_CATALOG_METADATA_CANDIDATES = 4;
const MAX_CATALOG_INDEX_FETCHES = 3;
const MAX_SEARCH_CACHE_ENTRIES = 64;
const MAX_CATALOG_CACHE_ENTRIES = 5;
const MAX_SOURCE_PAGES = 250_000;
const MAX_CATALOGS = 30_000;
// A catalog snapshot contains one index per catalog, up to 256 search shards, and six shared artifacts.
const MAX_MANIFEST_FILES = MAX_CATALOGS + 1024;
const MAX_RECORDS = 2_000_000;

export function isValidCatalogIndexManifestEntry(entry, recordCount) {
  return Boolean(entry &&
    Number.isSafeInteger(entry.bytes) && entry.bytes >= 1 &&
    entry.bytes <= MAX_CATALOG_INDEX_GZIP_BYTES &&
    Number.isSafeInteger(entry.items) && entry.items === recordCount);
}
const CACHE_MS = 600_000;
const STALE_MS = 3_600_000;
const FETCH_TIMEOUT_MS = 12_000;
const CONTENT_INTENT = new Set([
  "article", "articles", "news", "research", "study", "studies", "report", "reports",
  "guide", "guides", "tutorial", "documentation", "paper", "papers", "dataset",
  "datasets", "video", "videos", "image", "images", "photo", "photos", "media", "book", "books"
]);
const ATTRIBUTE_KEYS = new Set(["brand", "model", "sku", "mpn", "gtin", "manufacturer", "category"]);
const PRODUCT_TYPES = new Set(["product", "brand", "model"]);
const SEARCH_STOP_TOKENS = new Set([
  "the", "and", "for", "with", "from", "our", "your", "all", "new", "home", "page",
  "official", "welcome", "lock", "menu", "search", "skip", "content", "login", "sign",
  "subscribe", "close", "cookie", "accept", "privacy", "terms", "policy", "back", "next",
  "previous", "print", "share",
]);
const CONTENT_TYPES = new Set(["article", "resource", "media", "subject"]);
let cache = null;
let pending = null;
const searchIndexCache = new Map();
const catalogIndexCache = new Map();
const registryShardCache = new Map();

function safeUrl(value) {
  try {
    const parsed = new URL(String(value || ""));
    if ((parsed.protocol !== "http:" && parsed.protocol !== "https:") || !parsed.hostname ||
        parsed.username || parsed.password) return "";
    return parsed.href;
  } catch {
    return "";
  }
}

function errorCode(error) {
  const raw = String(error && error.message || "catalog_preview_unavailable");
  return raw.split(":", 1)[0].replace(/[^a-z0-9_-]/gi, "_").slice(0, 80) || "catalog_preview_unavailable";
}

async function fetchBytes(url, maxBytes, fetchImpl, accept = "*/*") {
  const response = await fetchImpl(url, {
    headers: {
      Accept: accept,
      "User-Agent": "EverythingHub-CatalogPreview/1.0 (+https://github.com/rshure001/EverythingHub-Crawler)",
    },
    signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
  });
  if (!response.ok) throw new Error("catalog_source_http_" + response.status);
  const declared = Number(response.headers && response.headers.get("content-length") || 0);
  if (declared > maxBytes) throw new Error("catalog_source_content_length_limit");

  if (!response.body) {
    const bytes = new Uint8Array(await response.arrayBuffer());
    if (bytes.byteLength > maxBytes) throw new Error("catalog_source_byte_limit");
    return bytes;
  }

  const reader = response.body.getReader();
  const chunks = [];
  let total = 0;
  try {
    while (true) {
      const part = await reader.read();
      if (part.done) break;
      if (!(part.value instanceof Uint8Array)) throw new Error("catalog_source_stream_invalid");
      total += part.value.byteLength;
      if (total > maxBytes) throw new Error("catalog_source_byte_limit");
      chunks.push(part.value);
    }
  } catch (error) {
    try { await reader.cancel(error); } catch { /* preserve original error */ }
    throw error;
  }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return bytes;
}

function decodeUtf8(bytes) {
  return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
}

async function sha256(bytes) {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest))
    .map((value) => value.toString(16).padStart(2, "0"))
    .join("");
}

async function fetchJson(url, maxBytes, fetchImpl, accept) {
  const bytes = await fetchBytes(url, maxBytes, fetchImpl, accept || "application/json");
  try {
    return { value: JSON.parse(decodeUtf8(bytes)), bytes };
  } catch {
    throw new Error("catalog_source_json_invalid");
  }
}

async function decodeGzip(bytes, maxBytes, label) {
  try {
    const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
    return JSON.parse(await readBoundedText(stream, maxBytes));
  } catch (error) {
    throw new Error(label + "_gzip_or_json_invalid:" + errorCode(error));
  }
}

export function validateManifest(manifest) {
  const analysisVersion = Number(manifest && manifest.analysis_version || 0);
  const legacy = analysisVersion < 10;
  const shardedRegistry = analysisVersion >= 21;
  if (!manifest || manifest.version !== 1 || manifest.integrity !== "sha256") {
    throw new Error("page_catalog_manifest_schema_invalid");
  }
  if (!Number.isSafeInteger(manifest.source_index_pages) || manifest.source_index_pages < 1 ||
      manifest.source_index_pages > MAX_SOURCE_PAGES) {
    throw new Error("page_catalog_source_count_invalid");
  }
  if (!Number.isSafeInteger(manifest.catalog_count) || manifest.catalog_count < 1 ||
      manifest.catalog_count > MAX_CATALOGS ||
      !Number.isSafeInteger(manifest.record_count) || manifest.record_count < 1 ||
      manifest.record_count > MAX_RECORDS) {
    throw new Error("page_catalog_counts_invalid");
  }
  if (typeof manifest.generated_at !== "string" || !Number.isFinite(Date.parse(manifest.generated_at)) ||
      typeof manifest.source_index_updated_at !== "string" ||
      !Number.isFinite(Date.parse(manifest.source_index_updated_at)) ||
      typeof manifest.source_index_sha256 !== "string" ||
      !/^[a-f0-9]{64}$/i.test(manifest.source_index_sha256) ||
      !Array.isArray(manifest.files) || manifest.files.length < (legacy ? 2 : 3) || manifest.files.length > MAX_MANIFEST_FILES) {
    throw new Error("page_catalog_manifest_invalid");
  }
  const files = new Map();
  for (const file of manifest.files) {
    if (!file || typeof file.file !== "string" || file.file.startsWith("/") ||
        file.file.includes("\\") || file.file.split("/").some((part) => !part || part === "." || part === "..") ||
        !Number.isSafeInteger(file.bytes) || file.bytes < 1 ||
        !Number.isSafeInteger(file.items) || file.items < 0 ||
        typeof file.sha256 !== "string" || !/^[a-f0-9]{64}$/i.test(file.sha256)) {
      throw new Error("page_catalog_file_entry_invalid");
    }
    if (files.has(file.file)) throw new Error("page_catalog_duplicate_file");
    files.set(file.file, file);
  }
  const registry = files.get("registry.json.gz");
  const records = files.get("records.json.gz");
  const registryShardFiles = Array.from(files.values()).filter((file) => file.file.startsWith("registry-shards/"));
  const recordShardFiles = Array.from(files.values()).filter((file) => file.file.startsWith("records/"));
  const searchIndexFiles = Array.from(files.values()).filter((file) => file.file.startsWith("search-index/"));
  if (!registry || (!records && !recordShardFiles.length) || (!legacy && !searchIndexFiles.length)) {
    throw new Error("page_catalog_artifacts_missing");
  }
  if (registry.items !== manifest.catalog_count) {
    throw new Error("page_catalog_artifact_item_count_invalid");
  }
  if (shardedRegistry) {
    if (!registryShardFiles.length || !recordShardFiles.length ||
        registryShardFiles.some((file) => file.bytes > MAX_REGISTRY_SHARD_GZIP_BYTES) ||
        recordShardFiles.some((file) => file.bytes > MAX_RECORDS_GZIP_BYTES) ||
        registryShardFiles.reduce((total, file) => total + file.items, 0) !== manifest.catalog_count ||
        recordShardFiles.reduce((total, file) => total + file.items, 0) !== manifest.record_count) {
      throw new Error("page_catalog_sharded_artifact_limit_or_count_invalid");
    }
  } else {
    if (!records || registry.bytes > MAX_REGISTRY_GZIP_BYTES || records.bytes > MAX_RECORDS_GZIP_BYTES) {
      throw new Error("page_catalog_compressed_limit");
    }
    if (records.items !== manifest.record_count) {
      throw new Error("page_catalog_artifact_item_count_invalid");
    }
  }
  if (searchIndexFiles.some((file) => file.bytes > MAX_SEARCH_INDEX_SHARD_BYTES)) {
    throw new Error("page_catalog_compressed_limit");
  }
  for (const file of searchIndexFiles) {
    if (!/^search-index\/[0-9a-f]{2}\.json\.gz$/.test(file.file)) {
      throw new Error("page_catalog_search_index_path_invalid");
    }
  }
  for (const file of registryShardFiles) {
    if (!/^registry-shards\/[0-9a-f]{2}\.json\.gz$/.test(file.file)) {
      throw new Error("page_catalog_registry_shard_path_invalid");
    }
  }
  for (const file of recordShardFiles) {
    if (!/^records\/\d{5}\.json\.gz$/.test(file.file)) {
      throw new Error("page_catalog_record_shard_path_invalid");
    }
  }
  if (Number(manifest.metrics && manifest.metrics.records_published || 0) !== 0 ||
      Number(manifest.catalogs_verified || 0) !== 0) {
    throw new Error("page_catalog_candidate_only_contract_invalid");
  }
  return { registry, records, registryShardFiles, recordShardFiles, searchIndexFiles, files, legacy, shardedRegistry };
}
function validatePolicyAnnotation(item, policyReview) {
  if (!policyReview) return;
  const status = item.source_policy_review_status;
  const hosts = item.source_policy_review_hosts;
  const evidence = item.source_policy_review_evidence_urls;
  if (!["not_reviewed", "conditional_source_review", "partial_source_review"].includes(status) ||
      item.record_level_clearance !== "not_reviewed" ||
      item.source_terms_status !== "not_reviewed" ||
      item.commercial_eligible !== false) {
    throw new Error("page_catalog_policy_annotation_invalid");
  }
  if (status !== "not_reviewed" &&
      (!Array.isArray(hosts) || !hosts.length || !Array.isArray(evidence) || !evidence.length ||
       item.source_policy_review_scope !== "source-level-only")) {
    throw new Error("page_catalog_policy_evidence_invalid");
  }
  if (status === "not_reviewed" &&
      ((Array.isArray(hosts) && hosts.length) || (Array.isArray(evidence) && evidence.length))) {
    throw new Error("page_catalog_unreviewed_policy_has_evidence");
  }
}

function validatePolicyReviewSummary(policyReview) {
  if (!policyReview || policyReview.schema_version !== 1 ||
      typeof policyReview.register_sha256 !== "string" ||
      !/^[a-f0-9]{64}$/i.test(policyReview.register_sha256) ||
      policyReview.scope !== "source-level-only" ||
      policyReview.record_level_clearance !== "not_reviewed" ||
      policyReview.commercial_eligibility !== false ||
      policyReview.republication !== "not_cleared") {
    throw new Error("page_catalog_policy_review_invalid");
  }
  return policyReview;
}

async function loadRegistryShard(network, prefix, fetchImpl) {
  const path = "registry-shards/" + prefix + ".json.gz";
  const file = network.registryShardFiles && network.registryShardFiles.get(path);
  if (!file) return [];
  const cacheKey = network.gitCommit + ":" + path;
  let payload = registryShardCache.get(cacheKey);
  if (!payload) {
    payload = await loadVerifiedGzipArtifact(
      network, file, fetchImpl, MAX_REGISTRY_SHARD_GZIP_BYTES, MAX_REGISTRY_SHARD_JSON_BYTES,
      "page_catalog_registry_shard",
    );
    if (payload.version !== 1 || payload.analysis_version !== network.analysisVersion ||
        payload.status !== "candidate_staging_not_production" ||
        !Array.isArray(payload.catalogs) || payload.catalogs.length !== file.items) {
      throw new Error("page_catalog_registry_shard_schema_invalid");
    }
    const policyReview = validatePolicyReviewSummary(payload.source_policy_review || network.sourcePolicyReview);
    if (network.sourcePolicyReview &&
        policyReview.register_sha256 !== network.sourcePolicyReview.register_sha256) {
      throw new Error("page_catalog_policy_review_digest_mismatch");
    }
    registryShardCache.set(cacheKey, payload);
  }
  const seenInShard = new Set();
  for (const catalog of payload.catalogs) {
    if (!catalog || typeof catalog.catalog_id !== "string" || !catalog.catalog_id ||
        seenInShard.has(catalog.catalog_id) ||
        !Number.isSafeInteger(catalog.record_count) || catalog.record_count < 0 ||
        !Number.isSafeInteger(catalog.source_page_count) || catalog.source_page_count < 0 ||
        catalog.commercial_eligible !== false ||
        Array.isArray(catalog.record_ids) || Array.isArray(catalog.source_page_ids) ||
        typeof catalog.index_file !== "string" ||
        !/^indexes\/[0-9a-f]{2}\/[0-9a-f]{24}\.json\.gz$/.test(catalog.index_file)) {
      throw new Error("page_catalog_registry_shard_catalog_invalid");
    }
    seenInShard.add(catalog.catalog_id);
    const digest = await sha256(new TextEncoder().encode(catalog.catalog_id));
    if (digest.slice(0, 2) !== prefix ||
        catalog.index_file !== "indexes/" + digest.slice(0, 2) + "/" + digest.slice(0, 24) + ".json.gz") {
      throw new Error("page_catalog_registry_shard_identity_invalid");
    }
    const indexEntry = network.filesByPath.get(catalog.index_file);
    if (!isValidCatalogIndexManifestEntry(indexEntry, catalog.record_count)) {
      throw new Error("page_catalog_index_manifest_entry_invalid");
    }
    if (catalog.source_url && !safeUrl(catalog.source_url)) {
      throw new Error("page_catalog_source_url_invalid");
    }
    validatePolicyAnnotation(catalog, network.sourcePolicyReview);
    const existing = network.catalogsById.get(catalog.catalog_id);
    if (existing && existing.index_file !== catalog.index_file) {
      throw new Error("page_catalog_registry_duplicate_identity");
    }
    if (!existing) {
      network.catalogsById.set(catalog.catalog_id, catalog);
      network.catalogs.push(catalog);
    }
  }
  return payload.catalogs;
}

async function ensureCatalogsLoaded(network, catalogIds, fetchImpl) {
  const ids = Array.from(new Set((catalogIds || []).map((id) => String(id || "").trim())));
  const missing = ids.filter((id) => id && !network.catalogsById.has(id));
  if (!missing.length) return;
  if (!network.registryShardFiles || !network.registryShardFiles.size) {
    throw new Error("page_catalog_registry_shards_unavailable");
  }
  const prefixes = new Set();
  for (const id of missing) {
    const digest = await sha256(new TextEncoder().encode(id));
    prefixes.add(digest.slice(0, 2));
  }
  const shards = Array.from(prefixes);
  for (let offset = 0; offset < shards.length; offset += 8) {
    await Promise.all(shards.slice(offset, offset + 8).map((prefix) => loadRegistryShard(network, prefix, fetchImpl)));
  }
  if (ids.some((id) => !network.catalogsById.has(id))) {
    throw new Error("page_catalog_registry_catalog_missing");
  }
}

function validateRegistry(manifest, registry, metadata) {
  if (!registry || registry.version !== 1 ||
      registry.status !== "candidate_staging_not_production" ||
      !Array.isArray(registry.catalogs) || registry.catalogs.length !== manifest.catalog_count) {
    throw new Error("page_catalog_registry_schema_invalid");
  }
  const policyReview = registry.source_policy_review || manifest.source_policy_review || null;
  if (policyReview) {
    if (manifest.source_policy_review && registry.source_policy_review &&
        manifest.source_policy_review.register_sha256 !== registry.source_policy_review.register_sha256) {
      throw new Error("page_catalog_policy_review_digest_mismatch");
    }
    if (policyReview.schema_version !== 1 ||
        typeof policyReview.register_sha256 !== "string" ||
        !/^[a-f0-9]{64}$/i.test(policyReview.register_sha256) ||
        policyReview.scope !== "source-level-only" ||
        policyReview.record_level_clearance !== "not_reviewed" ||
        policyReview.commercial_eligibility !== false ||
        policyReview.republication !== "not_cleared") {
      throw new Error("page_catalog_policy_review_invalid");
    }
  }

  const filesByPath = metadata.files;
  const catalogsById = new Map();
  for (const catalog of registry.catalogs) {
    if (!catalog || typeof catalog.catalog_id !== "string" || !catalog.catalog_id ||
        catalogsById.has(catalog.catalog_id) || !Array.isArray(catalog.record_ids) ||
        catalog.record_count !== catalog.record_ids.length ||
        new Set(catalog.record_ids).size !== catalog.record_ids.length ||
        !Array.isArray(catalog.source_page_ids) ||
        catalog.source_page_count !== catalog.source_page_ids.length ||
        catalog.commercial_eligible !== false ||
        typeof catalog.index_file !== "string" ||
        !/^indexes\/[0-9a-f]{2}\/[0-9a-f]{24}\.json\.gz$/.test(catalog.index_file)) {
      throw new Error("page_catalog_identity_or_membership_invalid");
    }
    const indexEntry = filesByPath.get(catalog.index_file);
    if (!isValidCatalogIndexManifestEntry(indexEntry, catalog.record_count)) {
      throw new Error("page_catalog_index_manifest_entry_invalid");
    }
    if (catalog.source_url && !safeUrl(catalog.source_url)) {
      throw new Error("page_catalog_source_url_invalid");
    }
    validatePolicyAnnotation(catalog, policyReview);
    catalogsById.set(catalog.catalog_id, catalog);
  }

  return {
    catalogs: registry.catalogs,
    catalogsById,
    filesByPath,
    generatedAt: manifest.generated_at,
    analysisVersion: manifest.analysis_version,
    sourceIndexPages: manifest.source_index_pages,
    sourceIndexUpdatedAt: manifest.source_index_updated_at,
    sourceIndexSha256: manifest.source_index_sha256,
    catalogCount: manifest.catalog_count,
    recordCount: manifest.record_count,
    metrics: manifest.metrics || {},
    sourcePolicyReview: policyReview,
  };
}


function validateLegacyNetwork(manifest, registry, payload) {
  if (!registry || registry.version !== 1 ||
      registry.status !== "candidate_staging_not_production" ||
      !Array.isArray(registry.catalogs) || registry.catalogs.length !== manifest.catalog_count) {
    throw new Error("page_catalog_registry_schema_invalid");
  }
  if (!payload || payload.version !== 1 ||
      !Array.isArray(payload.records) || payload.records.length !== manifest.record_count) {
    throw new Error("page_catalog_records_schema_invalid");
  }
  const policyReview = registry.source_policy_review || manifest.source_policy_review || null;
  if (policyReview) {
    if (manifest.source_policy_review && registry.source_policy_review &&
        manifest.source_policy_review.register_sha256 !== registry.source_policy_review.register_sha256) {
      throw new Error("page_catalog_policy_review_digest_mismatch");
    }
    if (policyReview.schema_version !== 1 ||
        typeof policyReview.register_sha256 !== "string" ||
        !/^[a-f0-9]{64}$/i.test(policyReview.register_sha256) ||
        policyReview.scope !== "source-level-only" ||
        policyReview.record_level_clearance !== "not_reviewed" ||
        policyReview.commercial_eligibility !== false ||
        policyReview.republication !== "not_cleared") {
      throw new Error("page_catalog_policy_review_invalid");
    }
  }

  const catalogs = new Map();
  for (const catalog of registry.catalogs) {
    if (!catalog || typeof catalog.catalog_id !== "string" || !catalog.catalog_id ||
        catalogs.has(catalog.catalog_id) || !Array.isArray(catalog.record_ids) ||
        catalog.record_count !== catalog.record_ids.length ||
        !Array.isArray(catalog.source_page_ids) ||
        catalog.source_page_count !== catalog.source_page_ids.length ||
        catalog.commercial_eligible !== false) {
      throw new Error("page_catalog_identity_or_membership_invalid");
    }
    if (catalog.source_url && !safeUrl(catalog.source_url)) {
      throw new Error("page_catalog_source_url_invalid");
    }
    validatePolicyAnnotation(catalog, policyReview);
    catalogs.set(catalog.catalog_id, catalog);
  }

  const records = new Map();
  for (const record of payload.records) {
    if (!record || typeof record.record_id !== "string" || !record.record_id ||
        records.has(record.record_id) || typeof record.entity_id !== "string" || !record.entity_id ||
        typeof record.record_type !== "string" || !record.record_type ||
        !Array.isArray(record.catalog_ids) || record.commercial_eligible !== false ||
        !Array.isArray(record.source_urls) || !record.source_urls.length ||
        record.source_urls.some((url) => !safeUrl(url))) {
      throw new Error("page_record_identity_or_provenance_invalid");
    }
    if (new Set(record.catalog_ids).size !== record.catalog_ids.length ||
        record.catalog_ids.some((id) => !catalogs.has(id))) {
      throw new Error("page_record_catalog_membership_invalid");
    }
    validatePolicyAnnotation(record, policyReview);
    records.set(record.record_id, record);
  }
  for (const catalog of registry.catalogs) {
    if (new Set(catalog.record_ids).size !== catalog.record_ids.length ||
        catalog.record_ids.some((id) => !records.has(id))) {
      throw new Error("page_catalog_record_reference_invalid");
    }
  }
  return {
    catalogs: registry.catalogs,
    records: payload.records,
    generatedAt: manifest.generated_at,
    analysisVersion: manifest.analysis_version,
    sourceIndexPages: manifest.source_index_pages,
    sourceIndexUpdatedAt: manifest.source_index_updated_at,
    sourceIndexSha256: manifest.source_index_sha256,
    catalogCount: manifest.catalog_count,
    recordCount: manifest.record_count,
    metrics: manifest.metrics || {},
    sourcePolicyReview: policyReview,
    legacySnapshot: true,
  };
}

async function loadSnapshot(fetchImpl) {
  const ref = await fetchJson(REF_URL, MAX_REF_BYTES, fetchImpl, "application/vnd.github+json");
  const sha = ref.value && ref.value.object && ref.value.object.sha;
  if (typeof sha !== "string" || !/^[a-f0-9]{40}$/i.test(sha)) {
    throw new Error("page_catalog_git_ref_invalid");
  }
  const base = RAW_BASE + sha + "/";
  const [manifestResult, publicManifestResult] = await Promise.all([
    fetchJson(base + "data/page-derived-catalogs/manifest.json", MAX_MANIFEST_BYTES, fetchImpl),
    fetchJson(base + "public-search/manifest.json", MAX_PUBLIC_MANIFEST_BYTES, fetchImpl),
  ]);
  const manifest = manifestResult.value;
  const publicManifest = publicManifestResult.value;
  const metadata = validateManifest(manifest);
  if (!publicManifest || publicManifest.version !== 1 || publicManifest.integrity !== "sha256" ||
      !Number.isSafeInteger(publicManifest.total_pages) || publicManifest.total_pages < 1 ||
      publicManifest.total_pages > MAX_SOURCE_PAGES ||
      !Number.isSafeInteger(publicManifest.updated_at) || publicManifest.updated_at < 1) {
    throw new Error("public_search_manifest_invalid");
  }

  let network;
  if (metadata.shardedRegistry) {
    const policyReview = validatePolicyReviewSummary(manifest.source_policy_review);
    network = {
      catalogs: [],
      catalogsById: new Map(),
      filesByPath: metadata.files,
      registryShardFiles: new Map(metadata.registryShardFiles.map((file) => [file.file, file])),
      baseUrl: base + "data/page-derived-catalogs/",
      generatedAt: manifest.generated_at,
      analysisVersion: manifest.analysis_version,
      sourceIndexPages: manifest.source_index_pages,
      sourceIndexUpdatedAt: manifest.source_index_updated_at,
      sourceIndexSha256: manifest.source_index_sha256,
      catalogCount: manifest.catalog_count,
      recordCount: manifest.record_count,
      metrics: manifest.metrics || {},
      sourcePolicyReview: policyReview,
      legacySnapshot: false,
    };
  } else {
    const registryUrl = base + "data/page-derived-catalogs/registry.json.gz";
    const registryBytes = await fetchBytes(registryUrl, MAX_REGISTRY_GZIP_BYTES, fetchImpl);
    if (registryBytes.byteLength !== metadata.registry.bytes) {
      throw new Error("page_catalog_registry_size_mismatch");
    }
    const registryDigest = await sha256(registryBytes);
    if (registryDigest.toLowerCase() !== metadata.registry.sha256.toLowerCase()) {
      throw new Error("page_catalog_registry_sha256_mismatch");
    }
    const registry = await decodeGzip(registryBytes, MAX_REGISTRY_JSON_BYTES, "page_catalog_registry");

    if (metadata.legacy) {
      const recordsBytes = await fetchBytes(
        base + "data/page-derived-catalogs/records.json.gz",
        MAX_RECORDS_GZIP_BYTES,
        fetchImpl,
      );
      if (recordsBytes.byteLength !== metadata.records.bytes) {
        throw new Error("page_catalog_records_size_mismatch");
      }
      const recordsDigest = await sha256(recordsBytes);
      if (recordsDigest.toLowerCase() !== metadata.records.sha256.toLowerCase()) {
        throw new Error("page_catalog_records_sha256_mismatch");
      }
      const recordsPayload = await decodeGzip(recordsBytes, MAX_RECORDS_JSON_BYTES, "page_catalog_records");
      network = validateLegacyNetwork(manifest, registry, recordsPayload);
    } else {
      network = validateRegistry(manifest, registry, metadata);
      network.filesByPath = metadata.files;
      network.baseUrl = base + "data/page-derived-catalogs/";
      network.legacySnapshot = false;
    }
  }

  const publicUpdatedAt = publicManifest.updated_at * 1000;
  const sourceUpdatedAt = Date.parse(network.sourceIndexUpdatedAt);
  network.pageIndexPages = publicManifest.total_pages;
  network.pageIndexUpdatedAt = new Date(publicUpdatedAt).toISOString();
  network.freshnessMatch = network.sourceIndexPages === publicManifest.total_pages &&
    Number.isFinite(sourceUpdatedAt) && Math.abs(sourceUpdatedAt - publicUpdatedAt) <= 60_000;
  network.gitCommit = sha;
  return network;
}
async function loadVerifiedGzipArtifact(network, file, fetchImpl, maxCompressedBytes, maxJsonBytes, label) {
  if (!file || typeof file.file !== "string") throw new Error(label + "_manifest_entry_missing");
  const bytes = await fetchBytes(network.baseUrl + file.file, maxCompressedBytes, fetchImpl);
  if (bytes.byteLength !== file.bytes) throw new Error(label + "_byte_length_mismatch");
  const digest = await sha256(bytes);
  if (digest.toLowerCase() !== file.sha256.toLowerCase()) throw new Error(label + "_sha256_mismatch");
  return decodeGzip(bytes, maxJsonBytes, label);
}

async function loadSearchIndexShard(network, prefix, fetchImpl) {
  const path = "search-index/" + prefix + ".json.gz";
  const file = network.filesByPath.get(path);
  if (!file) return { version: 1, prefix, tokens: {} };
  const cacheKey = network.gitCommit + ":" + path;
  if (searchIndexCache.has(cacheKey)) return searchIndexCache.get(cacheKey);
  const payload = await loadVerifiedGzipArtifact(
    network, file, fetchImpl, MAX_SEARCH_INDEX_SHARD_BYTES, MAX_SEARCH_INDEX_JSON_BYTES,
    "page_catalog_search_index",
  );
  if (payload.version !== 1 || payload.prefix !== prefix ||
      !payload.tokens || typeof payload.tokens !== "object" || Array.isArray(payload.tokens) ||
      Object.keys(payload.tokens).length !== file.items) {
    throw new Error("page_catalog_search_index_schema_invalid");
  }
  for (const [token, catalogIds] of Object.entries(payload.tokens)) {
    if (!token || token.length < 2 || !Array.isArray(catalogIds) || !catalogIds.length ||
        new Set(catalogIds).size !== catalogIds.length || catalogIds.some((id) => typeof id !== "string" || !id)) {
      throw new Error("page_catalog_search_index_postings_invalid");
    }
  }
  searchIndexCache.set(cacheKey, payload);
  while (searchIndexCache.size > MAX_SEARCH_CACHE_ENTRIES) {
    searchIndexCache.delete(searchIndexCache.keys().next().value);
  }
  return payload;
}

async function loadCatalogIndex(network, catalog, fetchImpl) {
  const path = catalog.index_file;
  const file = network.filesByPath.get(path);
  if (!file) throw new Error("page_catalog_index_manifest_entry_missing");
  const cacheKey = network.gitCommit + ":" + path;
  if (catalogIndexCache.has(cacheKey)) return catalogIndexCache.get(cacheKey);
  const payload = await loadVerifiedGzipArtifact(
    network, file, fetchImpl, MAX_CATALOG_INDEX_GZIP_BYTES, MAX_CATALOG_INDEX_JSON_BYTES,
    "page_catalog_index",
  );
  if (payload.version !== 1 || payload.catalog_id !== catalog.catalog_id ||
      !Array.isArray(payload.records) || payload.records.length !== file.items ||
      payload.records.length !== catalog.record_count) {
    throw new Error("page_catalog_index_schema_invalid");
  }
  const seenIds = new Set();
  for (const record of payload.records) {
    if (!record || typeof record.record_id !== "string" || !record.record_id ||
        seenIds.has(record.record_id) ||
        typeof record.entity_id !== "string" || !record.entity_id ||
        typeof record.record_type !== "string" || !record.record_type ||
        !Array.isArray(record.catalog_ids) || !record.catalog_ids.includes(catalog.catalog_id) ||
        new Set(record.catalog_ids).size !== record.catalog_ids.length ||
        record.catalog_ids.some((id) => typeof id !== "string" || !id) ||
        !Array.isArray(record.source_urls) || !record.source_urls.length ||
        record.source_urls.some((url) => !safeUrl(url)) ||
        record.commercial_eligible !== false) {
      throw new Error("page_catalog_index_record_invalid");
    }
    validatePolicyAnnotation(record, network.sourcePolicyReview);
    seenIds.add(record.record_id);
  }
  if (seenIds.size !== catalog.record_count) {
    throw new Error("page_catalog_index_membership_mismatch");
  }
  catalogIndexCache.set(cacheKey, payload.records);
  while (catalogIndexCache.size > MAX_CATALOG_CACHE_ENTRIES) {
    catalogIndexCache.delete(catalogIndexCache.keys().next().value);
  }
  return payload.records;
}

export async function resolveRecordRelatedCatalogPreview(
  recordCatalogId,
  sharedCatalogIds,
  network,
  limit = 20,
  offset = 0,
  fetchImpl = fetch,
) {
  const key = String(recordCatalogId || "").trim();
  if (!key || key.length > 160 || !Array.isArray(sharedCatalogIds) ||
      !sharedCatalogIds.length || sharedCatalogIds.length > MAX_CATALOG_INDEX_FETCHES ||
      !network || !network.catalogsById || typeof fetchImpl !== "function") {
    throw new Error("record_catalog_request_invalid");
  }
  const ids = Array.from(new Set(sharedCatalogIds.map((id) => String(id || "").trim())));
  if (ids.length !== sharedCatalogIds.length || ids.some((id) => !id)) {
    throw new Error("record_catalog_membership_invalid");
  }
  await ensureCatalogsLoaded(network, ids, fetchImpl);
  if (ids.some((id) => !network.catalogsById.has(id))) {
    throw new Error("record_catalog_membership_invalid");
  }
  const catalogs = ids.map((id) => network.catalogsById.get(id));
  const batches = [];
  for (let i = 0; i < catalogs.length; i += 1) batches.push(catalogs.slice(i, i + 1));
  const indexGroups = await Promise.all(batches.map((batch) =>
    Promise.all(batch.map((catalog) => loadCatalogIndex(network, catalog, fetchImpl)))
  ));
  const indexedRecords = indexGroups.flat().flat();
  const owners = indexedRecords.filter((record) => record.record_catalog_id === key);
  const ownerIds = new Set(owners.map((record) => record.record_id));
  if (ownerIds.size > 1) throw new Error("record_catalog_identity_collision");
  const owner = owners[0] || null;
  if (!owner) return null;
  const related = new Map();
  for (const record of indexedRecords) {
    if (record.record_id !== owner.record_id) related.set(record.record_id, record);
  }
  if (ids.some((id) => !owner.catalog_ids.includes(id))) {
    throw new Error("record_catalog_membership_mismatch");
  }
  related.delete(owner.record_id);
  const ordered = Array.from(related.values()).sort((a, b) =>
    String(a.title || "").localeCompare(String(b.title || "")) ||
    String(a.record_id || "").localeCompare(String(b.record_id || ""))
  );
  const safeOffset = Math.max(0, Math.min(5000, Number.isInteger(offset) ? offset : 0));
  const safeLimit = Math.max(1, Math.min(50, Number.isInteger(limit) ? limit : 20));
  return {
    record_catalog_id: key,
    owner_record: {
      record_id: owner.record_id,
      record_catalog_id: owner.record_catalog_id,
      title: owner.title,
      record_type: owner.record_type,
      description: String(owner.description || "").slice(0, 700),
      source_urls: owner.source_urls || [],
      source_page_ids: owner.source_page_ids || [],
      shared_catalog_ids: owner.catalog_ids || [],
    },
    related_count: ordered.length,
    offset: safeOffset,
    limit: safeLimit,
    has_more: safeOffset + safeLimit < ordered.length,
    related_records: ordered.slice(safeOffset, safeOffset + safeLimit).map((record) => ({
      record_id: record.record_id,
      record_catalog_id: String(record.record_catalog_id || ""),
      title: String(record.title || "Untitled record").slice(0, 240),
      record_type: String(record.record_type || "record"),
      description: String(record.description || "").slice(0, 700),
      source_urls: record.source_urls || [],
      source_page_ids: record.source_page_ids || [],
      shared_catalog_ids: record.catalog_ids || [],
      last_seen_at: record.last_seen_at || null,
      source_terms_status: record.source_terms_status || "not_reviewed",
      record_level_clearance: record.record_level_clearance || "not_reviewed",
      commercial_eligible: false,
    })),
  };
}

export async function loadPageDerivedCatalogPreview({ fetchImpl = fetch } = {}) {
  if (typeof fetchImpl !== "function") throw new TypeError("fetchImpl must be a function");
  const now = Date.now();
  if (cache && now - cache.loadedAt < CACHE_MS) return { ...cache.network, stale: false };
  if (pending) return pending;

  const task = (async () => {
    const network = await loadSnapshot(fetchImpl);
    cache = { loadedAt: Date.now(), network };
    return { ...network, stale: false };
  })();
  pending = task;
  try {
    return await task;
  } catch (error) {
    if (cache && Date.now() - cache.loadedAt < STALE_MS) {
      return {
        ...cache.network,
        stale: true,
        freshnessMatch: false,
        refreshError: errorCode(error),
      };
    }
    throw error;
  } finally {
    if (pending === task) pending = null;
  }
}

function singularSearchToken(token) {
  if (["news", "series", "species", "politics", "physics", "mathematics", "economics", "ethics"].includes(token)) return "";
  if (token.endsWith("ies") && token.length > 4) return token.slice(0, -3) + "y";
  if (["ches", "shes", "sses", "xes", "zes", "oes"].some((suffix) => token.endsWith(suffix)) && token.length > 4) {
    return token.slice(0, -2);
  }
  if (token.endsWith("s") && token.length > 3 &&
      !["ss", "us", "is", "as", "os", "ws", "ics"].some((suffix) => token.endsWith(suffix))) {
    return token.slice(0, -1);
  }
  return "";
}

function tokenize(value) {
  const raw = (String(value || "").normalize("NFKC").toLowerCase().match(/[\p{L}\p{N}]+/gu) || [])
    .filter((token) => !SEARCH_STOP_TOKENS.has(token));
  // Normalize inflections to one canonical token. Keeping both plural and
  // singular forms makes the posting intersection require both variants,
  // which incorrectly turns searches like "cameras" and "trees" into zero hits.
  const tokens = new Set();
  for (const token of raw) tokens.add(singularSearchToken(token) || token);
  return Array.from(tokens);
}

function recordTokens(record) {
  const titleTokens = new Set(tokenize(record.title));
  const descriptionTokens = new Set(tokenize(String(record.description || "").slice(0, 2000)));
  const attributes = record.attributes && typeof record.attributes === "object" ? record.attributes : {};
  const attributeText = Object.entries(attributes)
    .filter(([key, value]) => ATTRIBUTE_KEYS.has(String(key).toLowerCase()) &&
      (typeof value === "string" || typeof value === "number"))
    .map(([, value]) => String(value)).join(" ");
  const attributeTokens = new Set(tokenize(attributeText));
  return { titleTokens, descriptionTokens, attributeTokens };
}

function allTermsPresent(terms, tokens) {
  return terms.every((term) => tokens.has(term));
}

export function searchPageDerivedCatalogPreview(query, network, limit = 5, fetchImpl = fetch) {
  if (network && Array.isArray(network.records)) {
    return searchCatalogRecords(query, network, limit);
  }
  return searchPageDerivedCatalogPreviewIndexed(query, network, limit, fetchImpl);
}

async function searchPageDerivedCatalogPreviewIndexed(query, network, limit = 5, fetchImpl = fetch) {
  const text = String(query || "").trim();
  if (!text || text.length > 160 || !network || !Array.isArray(network.catalogs) ||
      !network.catalogsById || !network.filesByPath || typeof fetchImpl !== "function") return [];
  const terms = Array.from(new Set(tokenize(text))).slice(0, 12);
  if (!terms.length) return [];

  const prefixes = new Set();
  for (const term of terms) {
    const digest = await sha256(new TextEncoder().encode(term));
    prefixes.add(digest.slice(0, 2));
  }
  const shards = await Promise.all(
    Array.from(prefixes).map((prefix) => loadSearchIndexShard(network, prefix, fetchImpl)),
  );
  const tokenCatalogs = new Map();
  for (const shard of shards) {
    for (const [token, ids] of Object.entries(shard.tokens)) tokenCatalogs.set(token, ids);
  }
  const postings = terms.map((term) => new Set(tokenCatalogs.get(term) || []));
  if (postings.some((set) => set.size === 0)) return [];
  const candidateIds = Array.from(postings[0]).filter((id) => postings.every((set) => set.has(id)));
  if (!candidateIds.length) return [];
  const boundedCandidateIds = candidateIds.slice(0, MAX_CATALOG_METADATA_CANDIDATES);
  await ensureCatalogsLoaded(network, boundedCandidateIds, fetchImpl);

  const contentIntent = terms.some((term) => CONTENT_INTENT.has(term));
  const productTerms = new Set(["product", "products", "model", "models", "watch", "watches", "buy", "retailer", "retailers", "price", "prices", "reference", "references"]);
  const businessTerms = new Set(["business", "businesses", "restaurant", "restaurants", "hotel", "hotels", "store", "stores", "shop", "shops", "venue", "venues", "dealer", "dealers"]);
  const eventTerms = new Set(["event", "events", "concert", "concerts", "festival", "festivals", "calendar", "schedule", "schedules", "grand", "prix"]);
  const productTermIntent = terms.some((term) => productTerms.has(term));
  const businessIntent = terms.some((term) => businessTerms.has(term));
  const eventIntent = terms.some((term) => eventTerms.has(term));
  const candidateCatalogs = boundedCandidateIds
    .map((id) => network.catalogsById.get(id))
    .filter((catalog) => catalog && catalog.record_count > 0);
  const hasProductCatalog = candidateCatalogs.some((catalog) =>
    catalog.catalog_type === "commerce" || PRODUCT_TYPES.has(String(catalog.record_type || "").toLowerCase())
  );
  const hasPlaceCatalog = candidateCatalogs.some((catalog) =>
    catalog.catalog_type === "geographic" || String(catalog.record_type || "").toLowerCase() === "place"
  );
  const geoIntent = terms.length >= 2 && hasPlaceCatalog && !contentIntent && !eventIntent && !businessIntent;
  const productIntent = !geoIntent && !eventIntent && !contentIntent && !businessIntent &&
    (productTermIntent || hasProductCatalog);
  const locationBusinessIntent = businessIntent && !contentIntent;

  const preScore = (catalog) => {
    const catalogTokens = new Set(tokenize([catalog.title, catalog.subject, catalog.source_host].join(" ")));
    const titleTokens = new Set(tokenize(catalog.title));
    let score = terms.filter((term) => catalogTokens.has(term)).length * 12;
    if (allTermsPresent(terms, titleTokens)) score += 100;
    else if (allTermsPresent(terms, catalogTokens)) score += 70;
    if (productIntent) {
      if (catalog.catalog_type === "commerce" || PRODUCT_TYPES.has(String(catalog.record_type || "").toLowerCase())) score += 240;
      else if (CONTENT_TYPES.has(String(catalog.record_type || "").toLowerCase())) score -= 100;
    } else if (geoIntent) {
      if (catalog.catalog_type === "geographic" && String(catalog.record_type || "").toLowerCase() === "place") score += 240;
      else if (catalog.catalog_type === "business_directory") score += 180;
      else if (catalog.catalog_type === "service_directory") score += 150;
      else if (CONTENT_TYPES.has(String(catalog.record_type || "").toLowerCase())) score -= 100;
    } else if (locationBusinessIntent) {
      if (catalog.catalog_type === "business_directory") score += 240;
      else if (catalog.catalog_type === "service_directory") score += 200;
      else if (catalog.catalog_type === "geographic") score += 100;
    } else if (eventIntent) {
      if (catalog.catalog_type === "events") score += 240;
      else if (CONTENT_TYPES.has(String(catalog.record_type || "").toLowerCase())) score -= 80;
    }
    if (catalog.is_source_declared_catalog) score += 8;
    if (catalog.evidence_strength === "high") score += 4;
    return score;
  };
  candidateCatalogs.sort((a, b) => preScore(b) - preScore(a) ||
    String(a.title || "").localeCompare(String(b.title || "")));
  const selectedCatalogs = candidateCatalogs.slice(0, MAX_CATALOG_INDEX_FETCHES);
  const recordsById = new Map();
  for (let offset = 0; offset < selectedCatalogs.length; offset += 1) {
    const batch = selectedCatalogs.slice(offset, offset + 1);
    const payloads = await Promise.all(batch.map((catalog) => loadCatalogIndex(network, catalog, fetchImpl)));
    for (const records of payloads) {
      for (const record of records) recordsById.set(record.record_id, record);
    }
  }
  return searchCatalogRecords(query, {
    ...network,
    catalogs: selectedCatalogs,
    records: Array.from(recordsById.values()),
  }, limit);
}

function searchCatalogRecords(query, network, limit = 5) {
  const text = String(query || "").trim();
  if (!text || text.length > 160 || !network ||
      !Array.isArray(network.catalogs) || !Array.isArray(network.records)) return [];
  const terms = Array.from(new Set(tokenize(text))).slice(0, 12);
  if (!terms.length) return [];
  const phrase = terms.join(" ");
  const contentIntent = terms.some((term) => CONTENT_INTENT.has(term));
  const productTerms = new Set(["product", "products", "model", "models", "watch", "watches", "buy", "retailer", "retailers", "price", "prices", "reference", "references"]);
  const businessTerms = new Set(["business", "businesses", "restaurant", "restaurants", "hotel", "hotels", "store", "stores", "shop", "shops", "venue", "venues", "dealer", "dealers"]);
  const eventTerms = new Set(["event", "events", "concert", "concerts", "festival", "festivals", "calendar", "schedule", "schedules", "grand", "prix"]);
  const businessIntent = terms.some((term) => businessTerms.has(term));
  const eventIntent = terms.some((term) => eventTerms.has(term));
  const productTermIntent = terms.some((term) => productTerms.has(term));

  // Determine typed intent in a lightweight pass without retaining token sets
  // for the entire record corpus in the Edge isolate.
  let globalProductMatch = false;
  let globalPlaceMatch = false;
  for (const record of network.records) {
    const tokens = recordTokens(record);
    const all = new Set([...tokens.titleTokens, ...tokens.descriptionTokens, ...tokens.attributeTokens]);
    const type = String(record.record_type || "").toLowerCase();
    if (!globalProductMatch && PRODUCT_TYPES.has(type) && allTermsPresent(terms, all)) globalProductMatch = true;
    if (!globalPlaceMatch && type === "place" && allTermsPresent(terms, all)) globalPlaceMatch = true;
    if (globalProductMatch && globalPlaceMatch) break;
  }
  const geoIntent = terms.length >= 2 && globalPlaceMatch && !contentIntent && !eventIntent && !businessIntent;
  const productIntent = !geoIntent && !eventIntent && !contentIntent && !businessIntent &&
    (productTermIntent || globalProductMatch);
  const locationBusinessIntent = businessIntent && !contentIntent;
  const catalogsById = new Map(network.catalogs.map((catalog) => [catalog.catalog_id, catalog]));
  const groups = new Map();

  for (const record of network.records) {
    const item = recordTokens(record);
    if (!record || !Array.isArray(record.catalog_ids) || !record.catalog_ids.length) continue;
    const type = String(record.record_type || "").toLowerCase();
    const title = String(record.title || "").toLowerCase();
    const titleMatch = allTermsPresent(terms, item.titleTokens);
    const attributeMatch = allTermsPresent(terms, item.attributeTokens);
    const contentRecord = CONTENT_TYPES.has(type);
    if (contentRecord && !contentIntent && !titleMatch) continue;

    for (const id of record.catalog_ids) {
      const catalog = catalogsById.get(id);
      if (!catalog) continue;
      // A derived subject catalog represents its own recurring topic. Do not
      // surface it for an incidental mention in a member-page title.
      if (
        type === "subject" &&
        catalog.catalog_origin === "derived_from_repeated_records"
      ) {
        const subjectTokens = new Set(tokenize(String(catalog.subject || "")));
        if (!terms.some((term) => subjectTokens.has(term))) continue;
      }
      const catalogTokens = new Set(tokenize([catalog.title, catalog.subject, catalog.source_host].join(" ")));
      const catalogMatch = allTermsPresent(terms, catalogTokens);
      const searchable = new Set([...item.titleTokens, ...item.attributeTokens, ...catalogTokens]);
      if (!contentRecord || contentIntent) {
        for (const token of item.descriptionTokens) searchable.add(token);
      }
      if (!allTermsPresent(terms, searchable)) continue;

      let score = 20 + (titleMatch ? 40 : 0) + (title.includes(phrase) ? 10 : 0);
      score += terms.filter((term) => item.descriptionTokens.has(term)).length * 2;
      score += terms.filter((term) => item.attributeTokens.has(term)).length * 4;
      if (PRODUCT_TYPES.has(type) && (titleMatch || attributeMatch || catalogMatch)) score += 24;
      if (type === "place" && terms.length >= 2) score += 18;
      if (catalogMatch) score += contentRecord && !contentIntent ? 5 : 15;
      if (!groups.has(id)) groups.set(id, { catalog, matches: new Map() });
      const prior = groups.get(id).matches.get(record.record_id);
      if (!prior || score > prior.score) groups.get(id).matches.set(record.record_id, { record, score });
    }
  }

  const results = [];
  for (const [id, group] of groups) {
    const catalog = group.catalog;
    const matched = Array.from(group.matches.values())
      .sort((a, b) => b.score - a.score || String(a.record.title || "").localeCompare(String(b.record.title || "")));
    if (!matched.length) continue;
    const matchedRecords = matched.slice(0, 5).map(({ record }) => ({
      record_id: record.record_id,
      record_catalog_id: String(record.record_catalog_id || ""),
      shared_catalog_ids: Array.isArray(record.catalog_ids) ? record.catalog_ids.slice(0, MAX_CATALOG_INDEX_FETCHES) : [],
      title: String(record.title || "Untitled record").slice(0, 240),
      record_type: String(record.record_type || "record"),
      source_url: (record.source_urls || []).map(safeUrl).find(Boolean) || "",
      last_seen_at: record.last_seen_at || null,
      source_policy_review_status: record.source_policy_review_status || "not_reviewed",
      record_level_clearance: record.record_level_clearance || "not_reviewed",
    }));
    const url = safeUrl(catalog.source_url) || matchedRecords.map((record) => record.source_url).find(Boolean) || "";
    if (!url) continue;
    const types = new Set(matched.map((item) => String(item.record.record_type || "").toLowerCase()));
    const recordScores = matched.slice(0, 5).map((item) => item.score);
    let score = Math.max(...recordScores) +
      Math.min(30, recordScores.reduce((sum, value) => sum + value, 0) - Math.max(...recordScores));
    const catalogTokens = new Set(tokenize([catalog.title, catalog.subject, catalog.source_host].join(" ")));
    const contentCatalog = CONTENT_TYPES.has(String(catalog.record_type || "").toLowerCase());
    const catalogMatch = allTermsPresent(terms, catalogTokens);
    if (catalogMatch) score += contentCatalog && !contentIntent ? 25 + 5 * terms.length : 100 + 10 * terms.length;
    if (catalog.is_source_declared_catalog) score += 8;
    if (catalog.evidence_strength === "high") score += 4;

    if (productIntent) {
      if (Array.from(PRODUCT_TYPES).some((type) => types.has(type))) score += 180;
      else if (Array.from(CONTENT_TYPES).some((type) => types.has(type))) score -= 100;
    } else if (geoIntent) {
      if (catalog.catalog_type === "geographic" && types.has("place")) score += 180;
      else if (catalog.catalog_type === "business_directory" && types.has("business")) score += 145;
      else if (catalog.catalog_type === "service_directory" && types.has("service")) score += 140;
      else if (catalog.catalog_type === "organization_directory" && types.has("organization")) score += 110;
      else if (catalog.catalog_type === "events" && types.has("event")) score += 60;
      else if (Array.from(CONTENT_TYPES).some((type) => types.has(type))) score -= 100;
    } else if (locationBusinessIntent) {
      if (catalog.catalog_type === "business_directory" && types.has("business")) score += 180;
      else if (catalog.catalog_type === "service_directory" && types.has("service")) score += 160;
      else if (catalog.catalog_type === "geographic" && types.has("place")) score += 100;
      else if (Array.from(CONTENT_TYPES).some((type) => types.has(type))) score -= 80;
    } else if (eventIntent) {
      if (catalog.catalog_type === "events" && types.has("event")) score += 180;
      else if (Array.from(CONTENT_TYPES).some((type) => types.has(type))) score -= 80;
    } else {
      if (catalog.catalog_type === "geographic") score += types.has("place") && terms.length >= 2 ? 70 : -20;
      if (catalog.catalog_type === "commerce") score += Array.from(PRODUCT_TYPES).some((type) => types.has(type)) ? 50 : -20;
      if (catalog.catalog_type === "business_directory") score += types.has("business") ? 40 : -15;
      if (catalog.catalog_type === "service_directory") score += types.has("service") ? 40 : -15;
    }

    let relevanceClass = "record_match";
    if (productIntent && Array.from(PRODUCT_TYPES).some((type) => types.has(type))) relevanceClass = "typed_product_match";
    else if (geoIntent && ["place", "business", "service", "organization", "dataset"].some((type) => types.has(type))) relevanceClass = "typed_location_match";
    else if (geoIntent && types.has("event")) relevanceClass = "related_event_match";
    else if (locationBusinessIntent && ["business", "service"].some((type) => types.has(type))) relevanceClass = "typed_business_match";
    else if (eventIntent && types.has("event")) relevanceClass = "typed_event_match";
    else if (Array.from(CONTENT_TYPES).some((type) => types.has(type))) relevanceClass = "related_mention";
    else if (catalogMatch) relevanceClass = "catalog_match";
    const labels = {
      typed_product_match: "Product or model match",
      typed_location_match: "Place or local directory match",
      typed_business_match: "Business or service match",
      typed_event_match: "Event catalog match",
      related_event_match: "Related event mention for this location",
      related_mention: "Related mention, not a dedicated catalog match",
      catalog_match: "Catalog title match",
      record_match: "Record match",
    };
    let label = String(catalog.catalog_type || "subject").replace(/_/g, " ") + " catalog";
    if (catalog.catalog_type === "geographic") label = "Place catalog";
    if (catalog.catalog_type === "commerce") label = "Product catalog";
    if (catalog.catalog_type === "business_directory") label = "Business catalog";
    if (catalog.catalog_type === "service_directory") label = "Service catalog";
    const recordCount = Number.isSafeInteger(catalog.record_count) ? catalog.record_count : matched.length;
    const sourcePageCount = Number.isSafeInteger(catalog.source_page_count) ? catalog.source_page_count : 0;
    const names = matchedRecords.map((record) => record.record_type + ": " + record.title);
    const policyStatus = String(catalog.source_policy_review_status || "not_reviewed");
    const policyHosts = Array.isArray(catalog.source_policy_review_hosts) ? catalog.source_policy_review_hosts : [];
    const policyMessage = policyStatus === "conditional_source_review"
      ? "Source-level policy review is conditional for " + policyHosts.join(", ") + ". Individual record rights remain unreviewed."
      : policyStatus === "partial_source_review"
        ? "Source-level policy review is partial. Individual record rights remain unreviewed."
        : "Source-level policy review is not recorded. Individual record rights remain unreviewed.";
    const snippet = "Candidate catalog preview · " + recordCount.toLocaleString() + " records · " +
      sourcePageCount.toLocaleString() + " source pages. Matching records: " + names.join(" · ") +
      ". " + policyMessage + " Preview only.";
    const queryIntent = productIntent ? "product" : geoIntent ? "location" :
      locationBusinessIntent ? "business" : eventIntent ? "event" : contentIntent ? "content" : "general";
    if (relevanceClass === "related_mention") label += " · Related mention";
    results.push({
      title: String(catalog.title || "Catalog") + " — " + label + ": " + matchedRecords[0].title,
      url,
      snippet,
      description: snippet,
      source: "Everything Hub Catalog Preview · " + label,
      origin: "Everything Hub Catalog Preview",
      provider: "everythinghub-catalog-preview",
      mediaType: "web",
      score,
      catalog_id: id,
      catalog_title: String(catalog.title || ""),
      catalog_type: catalog.catalog_type,
      record_type: catalog.record_type,
      catalog_status: catalog.catalog_status || catalog.status || "candidate_needs_source_validation",
      catalog_origin: catalog.catalog_origin || "unknown",
      evidence_strength: catalog.evidence_strength || "unknown",
      source_terms_status: catalog.source_terms_status || "not_reviewed",
      source_policy_review_status: policyStatus,
      source_policy_review_hosts: policyHosts,
      record_level_clearance: catalog.record_level_clearance || "not_reviewed",
      rights_status: catalog.rights_status || "publicly_accessible_not_cleared_for_redistribution",
      commercial_eligible: false,
      preview_only: true,
      query_intent: queryIntent,
      relevance_class: relevanceClass,
      relevance_label: labels[relevanceClass],
      record_count: recordCount,
      source_page_count: sourcePageCount,
      matched_records: matchedRecords,
    });
  }
  const safeLimit = Number.isInteger(limit) ? Math.max(1, Math.min(8, limit)) : 5;
  return results.sort((a, b) => b.score - a.score ||
    b.matched_records.length - a.matched_records.length ||
    a.title.localeCompare(b.title)).slice(0, safeLimit);
}

export function clearPageDerivedCatalogPreviewCache() {
  cache = null;
  pending = null;
  searchIndexCache.clear();
  catalogIndexCache.clear();
  registryShardCache.clear();
}
