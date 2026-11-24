import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "npm:@supabase/supabase-js@2.49.1";
import { boundedInteger, normalizeLane, normalizeQuery } from "./params.mjs";
import { createTimedFetch, isTransientDatabaseError } from "./db-fetch.mjs";
import { searchStaticPages } from "./static-search.mjs";
import { normalizeManifestTimestamp } from "./manifest-time.mjs";
import { isValidStaticManifest } from "./static-manifest.mjs";
import { readBoundedText } from "./bounded-stream.mjs";
import { intersectShardPostings, tokenizeIndexedQuery } from "./shard-search.mjs";
import { loadPageDerivedCatalogPreview, searchPageDerivedCatalogPreview, resolveRecordRelatedCatalogPreview } from "./page-derived-catalog-preview.mjs";

const cors = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Content-Type": "application/json; charset=utf-8",
};
const json = (body: unknown, status = 200, cache = "no-store") =>
  new Response(JSON.stringify(body), { status, headers: { ...cors, "Cache-Control": cache } });

const supabase = createClient(
  Deno.env.get("SUPABASE_URL")!,
  Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
  {
    // Keep database recovery from delaying the verified static-index fallback.
    global: { fetch: createTimedFetch(fetch, 1200) },
    auth: { persistSession: false, autoRefreshToken: false },
  },
);
// Fail fast during database recovery so the verified crawler/Bing fallback stays responsive.
const db = supabase.schema("eh_quad");

const STATIC_INDEX_BASE =
  "https://raw.githubusercontent.com/rshure001/EverythingHub-Crawler/main/public-search";
const STATIC_INDEX_CACHE_MS = 60_000;
const MAX_STATIC_CATALOG_GZIP_BYTES = 6_000_000;
const MAX_STATIC_CATALOG_UNCOMPRESSED_BYTES = 30_000_000;
const STATIC_INDEX_LOAD_TIMEOUT_MS = 12_000;

type StaticCatalog = {
  pages: any[];
  updatedAt: string | null;
  mode: "gzip";
  expiresAt: number;
};
type CachedPayload<T> = { value: T; expiresAt: number };

let staticManifestCache: CachedPayload<any> | null = null;
let staticManifestPromise: Promise<any> | null = null;
let staticCatalogCache: StaticCatalog | null = null;
let staticCatalogPromise: Promise<StaticCatalog | null> | null = null;
const queryIndexShardCache = new Map<string, CachedPayload<any>>();
const pageShardCache = new Map<number, CachedPayload<any[]>>();

function setBoundedCache<T>(cache: Map<any, CachedPayload<T>>, key: any, value: T, maxEntries: number): void {
  if (cache.has(key)) cache.delete(key);
  while (cache.size >= maxEntries) {
    const oldest = cache.keys().next().value;
    if (oldest === undefined) break;
    cache.delete(oldest);
  }
  cache.set(key, { value, expiresAt: Date.now() + STATIC_INDEX_CACHE_MS });
}

const MAX_QUERY_INDEX_SHARD_COMPRESSED_BYTES = 2_000_000;
const MAX_QUERY_INDEX_SHARD_UNCOMPRESSED_BYTES = 8_000_000;
const MAX_PAGE_SHARD_COMPRESSED_BYTES = 350_000;
const MAX_PAGE_SHARD_UNCOMPRESSED_BYTES = 600_000;
const MAX_QUERY_INDEX_REQUEST_COMPRESSED_BYTES = 4_000_000;
const MAX_QUERY_INDEX_REQUEST_UNCOMPRESSED_BYTES = 16_000_000;
const MAX_QUERY_PAGE_SHARDS = 40;
const MAX_QUERY_PAGE_SHARDS_COMPRESSED_BYTES = 6_000_000;
const MAX_QUERY_PAGE_SHARDS_UNCOMPRESSED_BYTES = 24_000_000;

const staticHeaders = {
  Accept: "application/json",
  "User-Agent": "EverythingHubSearch/1.0 (+https://github.com/rshure001/EverythingHub-Crawler)",
};

async function sha256Hex(bytes: Uint8Array): Promise<string> {
  const digestBuffer = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digestBuffer))
    .map((value) => value.toString(16).padStart(2, "0"))
    .join("");
}

async function decodeGzipJson(bytes: Uint8Array, maxBytes: number): Promise<{ payload: any; decodedBytes: number }> {
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
  const decoded = await readBoundedText(stream, maxBytes);
  const decodedBytes = new TextEncoder().encode(decoded).length;
  if (decodedBytes > maxBytes) throw new Error("Decoded static search payload exceeds its safety limit");
  return { payload: JSON.parse(decoded), decodedBytes };
}

async function loadStaticManifest(): Promise<any> {
  if (staticManifestCache && staticManifestCache.expiresAt > Date.now()) {
    return staticManifestCache.value;
  }
  if (staticManifestPromise) return staticManifestPromise;

  const pending = (async () => {
    const response = await fetch(STATIC_INDEX_BASE + "/manifest.json", {
      headers: staticHeaders,
      signal: AbortSignal.timeout(STATIC_INDEX_LOAD_TIMEOUT_MS),
    });
    if (!response.ok) throw new Error("Public crawler manifest returned HTTP " + response.status);
    const manifest: any = await response.json();
    if (!isValidStaticManifest(manifest)) {
      throw new Error("Public crawler manifest failed structural validation");
    }
    staticManifestCache = { value: manifest, expiresAt: Date.now() + STATIC_INDEX_CACHE_MS };
    return manifest;
  })();

  staticManifestPromise = pending;
  try {
    return await pending;
  } finally {
    if (staticManifestPromise === pending) staticManifestPromise = null;
  }
}

async function loadStaticCatalog(manifest: any): Promise<StaticCatalog | null> {
  if (staticCatalogCache && staticCatalogCache.expiresAt > Date.now()) {
    return staticCatalogCache;
  }
  if (staticCatalogPromise) return staticCatalogPromise;

  const pending = (async () => {
    const compressed = manifest.catalog_gzip;
    if (!compressed) return null;
    if (
      compressed.file !== "catalog-v1.json.gz" ||
      !Number.isInteger(compressed.bytes) ||
      compressed.bytes < 2 ||
      compressed.bytes > MAX_STATIC_CATALOG_GZIP_BYTES ||
      compressed.pages !== manifest.total_pages ||
      typeof compressed.sha256 !== "string" ||
      !/^[a-f0-9]{64}$/.test(compressed.sha256)
    ) {
      console.error("Compressed catalog metadata is outside the Edge Function budget; using query shards");
      return null;
    }

    try {
      const response = await fetch(STATIC_INDEX_BASE + "/" + compressed.file, {
        headers: staticHeaders,
        signal: AbortSignal.timeout(STATIC_INDEX_LOAD_TIMEOUT_MS),
      });
      if (!response.ok) throw new Error("Compressed catalog returned HTTP " + response.status);
      const bytes = new Uint8Array(await response.arrayBuffer());
      if (bytes.length !== compressed.bytes) throw new Error("Compressed catalog byte length does not match the manifest");
      if (await sha256Hex(bytes) !== compressed.sha256) throw new Error("Compressed catalog SHA-256 does not match the manifest");
      const { payload, decodedBytes } = await decodeGzipJson(bytes, MAX_STATIC_CATALOG_UNCOMPRESSED_BYTES);
      if (
        payload?.version !== 1 ||
        !Array.isArray(payload?.pages) ||
        payload.pages.length !== manifest.total_pages
      ) {
        throw new Error("Compressed catalog page count or schema is invalid");
      }
      if (decodedBytes > MAX_STATIC_CATALOG_UNCOMPRESSED_BYTES) {
        throw new Error("Compressed catalog exceeds the Edge Function decoded budget");
      }
      const catalog: StaticCatalog = {
        pages: payload.pages,
        updatedAt: normalizeManifestTimestamp(manifest.updated_at),
        mode: "gzip",
        expiresAt: Date.now() + STATIC_INDEX_CACHE_MS,
      };
      staticCatalogCache = catalog;
      return catalog;
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      console.error("Compressed catalog unavailable; using verified query shards:", detail);
      return null;
    }
  })();

  staticCatalogPromise = pending;
  try {
    return await pending;
  } finally {
    if (staticCatalogPromise === pending) staticCatalogPromise = null;
  }
}

async function queryIndexPrefix(token: string): Promise<string> {
  const bytes = new TextEncoder().encode(token);
  return (await sha256Hex(bytes)).slice(0, 2);
}

async function loadQueryIndexShard(manifest: any, prefix: string, signal: AbortSignal): Promise<any | null> {
  const cached = queryIndexShardCache.get(prefix);
  if (cached && cached.expiresAt > Date.now()) return cached.value;

  const queryIndex = manifest.query_index;
  if (
    !queryIndex ||
    queryIndex.version !== 1 ||
    !Array.isArray(queryIndex.shards) ||
    queryIndex.shard_count !== queryIndex.shards.length ||
    queryIndex.shard_count > 256
  ) {
    throw new Error("Public crawler query index is missing or invalid");
  }

  const file = "query-index/" + prefix + ".json.gz";
  const meta = queryIndex.shards.find((entry: any) => entry?.file === file);
  if (!meta) return null;
  if (
    !Number.isInteger(meta.bytes) ||
    meta.bytes < 1 ||
    meta.bytes > MAX_QUERY_INDEX_SHARD_COMPRESSED_BYTES ||
    !Number.isInteger(meta.uncompressed_bytes) ||
    meta.uncompressed_bytes < 2 ||
    meta.uncompressed_bytes > MAX_QUERY_INDEX_SHARD_UNCOMPRESSED_BYTES ||
    !Number.isInteger(meta.items) ||
    meta.items < 1 ||
    typeof meta.sha256 !== "string" ||
    !/^[a-f0-9]{64}$/.test(meta.sha256)
  ) {
    throw new Error("Query-index shard metadata is invalid: " + file);
  }

  const response = await fetch(STATIC_INDEX_BASE + "/" + file, { headers: staticHeaders, signal });
  if (!response.ok) throw new Error("Query-index shard returned HTTP " + response.status + ": " + file);
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (bytes.length !== meta.bytes) throw new Error("Query-index shard byte count mismatch: " + file);
  if (await sha256Hex(bytes) !== meta.sha256) throw new Error("Query-index shard SHA-256 mismatch: " + file);
  const { payload, decodedBytes } = await decodeGzipJson(bytes, MAX_QUERY_INDEX_SHARD_UNCOMPRESSED_BYTES);
  if (
    decodedBytes !== meta.uncompressed_bytes ||
    payload?.version !== 1 ||
    payload?.prefix !== prefix ||
    !payload?.tokens ||
    typeof payload.tokens !== "object" ||
    Array.isArray(payload.tokens) ||
    Object.keys(payload.tokens).length !== meta.items
  ) {
    throw new Error("Query-index shard schema or decoded size is invalid: " + file);
  }
  setBoundedCache(queryIndexShardCache, prefix, payload, 2);
  return payload;
}

async function loadPageShard(manifest: any, shardIndex: number, signal: AbortSignal): Promise<any[]> {
  const cached = pageShardCache.get(shardIndex);
  if (cached && cached.expiresAt > Date.now()) return cached.value;

  const meta = manifest.shards[shardIndex];
  if (
    !meta ||
    typeof meta.file !== "string" ||
    !/^shard-\d{4}\.json\.gz$/.test(meta.file) ||
    !Number.isInteger(meta.bytes) ||
    meta.bytes < 1 ||
    meta.bytes > MAX_PAGE_SHARD_COMPRESSED_BYTES ||
    !Number.isInteger(meta.uncompressed_bytes) ||
    meta.uncompressed_bytes < 2 ||
    meta.uncompressed_bytes > MAX_PAGE_SHARD_UNCOMPRESSED_BYTES ||
    !Number.isInteger(meta.pages) ||
    meta.pages < 1 ||
    typeof meta.sha256 !== "string" ||
    !/^[a-f0-9]{64}$/.test(meta.sha256)
  ) {
    throw new Error("Page-shard metadata is invalid for ordinal " + shardIndex);
  }

  const response = await fetch(STATIC_INDEX_BASE + "/" + meta.file, { headers: staticHeaders, signal });
  if (!response.ok) throw new Error("Page shard returned HTTP " + response.status + ": " + meta.file);
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (bytes.length !== meta.bytes) throw new Error("Page-shard byte count mismatch: " + meta.file);
  if (await sha256Hex(bytes) !== meta.sha256) throw new Error("Page-shard SHA-256 mismatch: " + meta.file);
  const { payload, decodedBytes } = await decodeGzipJson(bytes, MAX_PAGE_SHARD_UNCOMPRESSED_BYTES);
  if (
    decodedBytes !== meta.uncompressed_bytes ||
    payload?.version !== 1 ||
    !Array.isArray(payload?.pages) ||
    payload.pages.length !== meta.pages
  ) {
    throw new Error("Page-shard schema or decoded size is invalid: " + meta.file);
  }
  setBoundedCache(pageShardCache, shardIndex, payload.pages, 40);
  return payload.pages;
}

async function searchStaticQueryShards(
  manifest: any,
  query: string,
  validLane: string | null,
  limit: number,
  offset: number,
): Promise<any> {
  const tokens = tokenizeIndexedQuery(query);
  if (!tokens.length) throw new Error("Query has no indexable terms");
  const queryIndex = manifest.query_index;
  if (
    !queryIndex ||
    queryIndex.version !== 1 ||
    !Array.isArray(queryIndex.shards) ||
    queryIndex.shard_count !== queryIndex.shards.length ||
    queryIndex.shard_count > 256
  ) {
    throw new Error("Public crawler query index is missing or invalid");
  }
  const signal = AbortSignal.timeout(STATIC_INDEX_LOAD_TIMEOUT_MS);
  const prefixes = [...new Set(await Promise.all(tokens.map(queryIndexPrefix)))];
  const queryIndexEntries = prefixes.map((prefix) =>
    manifest.query_index.shards.find((entry: any) => entry?.file === "query-index/" + prefix + ".json.gz")
  );
  if (queryIndexEntries.some((entry: any) => !entry)) {
    return {
      found: { results: [], count: 0, hasMore: false },
      laneFallback: false,
      updatedAt: normalizeManifestTimestamp(manifest.updated_at),
      transport: "query-shards",
    };
  }
  const queryIndexCompressedBytes = queryIndexEntries.reduce((total: number, entry: any) => total + entry.bytes, 0);
  const queryIndexUncompressedBytes = queryIndexEntries.reduce((total: number, entry: any) => total + entry.uncompressed_bytes, 0);
  if (
    queryIndexCompressedBytes > MAX_QUERY_INDEX_REQUEST_COMPRESSED_BYTES ||
    queryIndexUncompressedBytes > MAX_QUERY_INDEX_REQUEST_UNCOMPRESSED_BYTES
  ) {
    throw new Error("Query-index shards exceed the Edge Function request budget");
  }

  const queryIndexPayloads = new Map<string, any>();
  for (const prefix of prefixes) {
    const payload = await loadQueryIndexShard(manifest, prefix, signal);
    if (!payload) {
      return {
        found: { results: [], count: 0, hasMore: false },
        laneFallback: false,
        updatedAt: normalizeManifestTimestamp(manifest.updated_at),
        transport: "query-shards",
      };
    }
    queryIndexPayloads.set(prefix, payload);
  }

  const postingsByToken = new Map<string, any[]>();
  for (const token of tokens) {
    const prefix = await queryIndexPrefix(token);
    const postings = queryIndexPayloads.get(prefix)?.tokens?.[token];
    if (!Array.isArray(postings)) {
      return {
        found: { results: [], count: 0, hasMore: false },
        laneFallback: false,
        updatedAt: normalizeManifestTimestamp(manifest.updated_at),
        transport: "query-shards",
      };
    }
    if (postings.some((posting: any) =>
      !Array.isArray(posting) ||
      posting.length !== 2 ||
      !Number.isSafeInteger(posting[0]) ||
      posting[0] < 0 ||
      posting[0] >= manifest.shards.length ||
      !Number.isSafeInteger(posting[1]) ||
      posting[1] < 1
    )) {
      throw new Error("Query-index postings contain an invalid page-shard ordinal");
    }
    postingsByToken.set(token, postings);
  }

  const shardIndexes = intersectShardPostings(tokens, postingsByToken);
  if (!shardIndexes.length) {
    return {
      found: { results: [], count: 0, hasMore: false },
      laneFallback: false,
      updatedAt: normalizeManifestTimestamp(manifest.updated_at),
      transport: "query-shards",
    };
  }
  if (shardIndexes.length > MAX_QUERY_PAGE_SHARDS) {
    throw new Error("Query matches too many page shards for the Edge Function request budget");
  }

  const shardMetadata = shardIndexes.map((index) => manifest.shards[index]);
  const compressedTotal = shardMetadata.reduce((total: number, item: any) => total + item.bytes, 0);
  const uncompressedTotal = shardMetadata.reduce((total: number, item: any) => total + item.uncompressed_bytes, 0);
  if (
    compressedTotal > MAX_QUERY_PAGE_SHARDS_COMPRESSED_BYTES ||
    uncompressedTotal > MAX_QUERY_PAGE_SHARDS_UNCOMPRESSED_BYTES
  ) {
    throw new Error("Matching page shards exceed the Edge Function byte budget");
  }

  const pageGroups = await Promise.all(shardIndexes.map((index) => loadPageShard(manifest, index, signal)));
  const pages = pageGroups.flat();
  const indexedQuery = tokens.join(" ");
  let found = searchStaticPages(pages, indexedQuery, validLane, limit, offset);
  let laneFallback = false;
  if (validLane && found.count < 3) {
    const broad = searchStaticPages(pages, indexedQuery, null, limit, offset);
    if (broad.count > found.count) {
      found = broad;
      laneFallback = true;
    }
  }

  return {
    found,
    laneFallback,
    updatedAt: normalizeManifestTimestamp(manifest.updated_at),
    transport: "query-shards",
  };
}

async function searchStaticIndex(
  query: string,
  validLane: string | null,
  limit: number,
  offset: number,
): Promise<any> {
  const manifest = await loadStaticManifest();
  const tokens = tokenizeIndexedQuery(query);
  if (!tokens.length) throw new Error("Query has no indexable terms");
  const catalog = await loadStaticCatalog(manifest);
  if (catalog) {
    let found = searchStaticPages(catalog.pages, tokens.join(" "), validLane, limit, offset);
    let laneFallback = false;
    if (validLane && found.count < 3) {
      const broad = searchStaticPages(catalog.pages, tokens.join(" "), null, limit, offset);
      if (broad.count > found.count) {
        found = broad;
        laneFallback = true;
      }
    }
    return {
      found,
      laneFallback,
      updatedAt: catalog.updatedAt,
      transport: "gzip",
    };
  }
  return await searchStaticQueryShards(manifest, query, validLane, limit, offset);
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
  if (req.method !== "GET") return json({ error: "GET required" }, 405);

  const url = new URL(req.url);
  const lanes = ["general", "science", "news", "technology", "business", "government", "culture", "nature"];
  const query = normalizeQuery(url.searchParams.get("q"));
  const validLane = normalizeLane(url.searchParams.get("tentacle"), lanes);
  const limit = boundedInteger(url.searchParams.get("limit"), 10, 1, 20);
  const offset = boundedInteger(url.searchParams.get("offset"), 0, 0, 5000);

  const recordCatalogId = String(url.searchParams.get("record_catalog_id") || "").trim();
  if (recordCatalogId) {
    const rawCatalogIds = String(url.searchParams.get("catalog_ids") || "");
    if (rawCatalogIds.length > 4000) return json({ error: "record_catalog_membership_invalid" }, 400);
    const sharedCatalogIds = rawCatalogIds.split(",").map((value) => value.trim()).filter(Boolean);
    if (!sharedCatalogIds.length || sharedCatalogIds.length > 12) {
      return json({ error: "record_catalog_membership_invalid" }, 400);
    }
    try {
      const network = await loadPageDerivedCatalogPreview();
      const related = await resolveRecordRelatedCatalogPreview(
        recordCatalogId, sharedCatalogIds, network, limit, offset,
      );
      if (!related) return json({ error: "record_catalog_not_found" }, 404);
      return json({
        status: "candidate-preview",
        preview_only: true,
        generated_at: network.generatedAt || null,
        analysis_version: network.analysisVersion || null,
        source_index_pages: network.sourceIndexPages || 0,
        ...related,
      }, 200, "public, max-age=15, stale-while-revalidate=30");
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      console.error("Per-record related catalog unavailable:", detail.slice(0, 160));
      return json({ error: "record_catalog_temporarily_unavailable" }, 503);
    }
  }

  if (query.length < 2) return json({ results: [], count: 0, query, offset, limit, hasMore: false });

  const unavailable = () => json({
    results: [], count: 0, query, offset, limit, hasMore: false,
    error: "Search temporarily unavailable",
  }, 503);

  const independentFallback = async (reason: string, crawlerIndexCount: number | null, attachCatalogPreview?: (payload: Record<string, any>) => Record<string, any>) => {
    try {
      const projectUrl = Deno.env.get("SUPABASE_URL");
      if (!projectUrl) return null;

      const endpoint = new URL(
        projectUrl.replace(/\/$/, "") + "/functions/v1/everything-hub-independent-search",
      );
      endpoint.searchParams.set("q", query);
      const apiKey = Deno.env.get("SUPABASE_ANON_KEY") ?? Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
      if (!apiKey) throw new Error("Supabase API key is unavailable for internal independent search");
      const response = await fetch(endpoint, {
        headers: { Accept: "application/json", apikey: apiKey },
        signal: AbortSignal.timeout(8500),
      });
      if (!response.ok) {
        throw new Error("Independent web fallback returned HTTP " + response.status);
      }

      const payload: any = await response.json();
      const allResults = (Array.isArray(payload?.results) ? payload.results : []).flatMap((item: any) => {
        const rawUrl = String(item?.url ?? "");
        try {
          const parsed = new URL(rawUrl);
          if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return [];
          return [{
            title: String(item?.title || rawUrl),
            url: parsed.href,
            snippet: String(item?.snippet || "").replace(/\s+/g, " ").slice(0, 320),
            source: String(item?.source || parsed.hostname),
            origin: "Bing RSS",
            tentacle: "general",
            relevance: 0,
          }];
        } catch {
          return [];
        }
      });

      if (!allResults.length) return null;
      const pageResults = allResults.slice(offset, offset + limit);
      const result = {
        results: pageResults,
        count: allResults.length,
        query,
        tentacle: null,
        requestedTentacle: validLane,
        laneFallback: Boolean(validLane),
        source: "Bing RSS independent web fallback",
        provider: String(payload?.provider || "Bing RSS"),
        offset,
        limit,
        hasMore: offset + pageResults.length < allResults.length,
        fallback: true,
        fallbackReason: reason,
        independentOfDatabase: true,
        crawlerIndexCount,
      };
      return json(
        attachCatalogPreview ? attachCatalogPreview(result) : result,
        200,
        "public, max-age=15, stale-while-revalidate=30",
      );
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      console.error("Independent web fallback unavailable:", detail);
      return null;
    }
  };

  const staticFallback = async (reason: string) => {
    // Load the first-party catalog network directly from one SHA-pinned GitHub snapshot.
    // This path is independent of PostgreSQL and the separately hosted Vercel search project.
    let pageDerivedCatalogResults: any[] = [];
    let pageDerivedCatalogMeta: Record<string, any> = {
      status: "unavailable", previewOnly: true, resultCount: 0, error: "catalog_preview_unavailable",
    };
    try {
      const pageDerived = await loadPageDerivedCatalogPreview();
      pageDerivedCatalogResults = await searchPageDerivedCatalogPreview(query, pageDerived, 5);
      const freshnessMatch = Boolean(pageDerived.freshnessMatch && !pageDerived.stale);
      pageDerivedCatalogMeta = {
        status: freshnessMatch ? "candidate-preview" : "stale-candidate-preview",
        previewOnly: true,
        analysisVersion: pageDerived.analysisVersion,
        generatedAt: pageDerived.generatedAt,
        sourceIndexPages: pageDerived.sourceIndexPages,
        sourceIndexUpdatedAt: pageDerived.sourceIndexUpdatedAt,
        sourceIndexSha256: pageDerived.sourceIndexSha256,
        pageIndexPages: pageDerived.pageIndexPages,
        pageIndexUpdatedAt: pageDerived.pageIndexUpdatedAt,
        catalogCount: pageDerived.catalogCount,
        recordCount: pageDerived.recordCount,
        recordsPublished: Number(pageDerived.metrics.records_published) || 0,
        sourceTermsReviewedRecords: Number(pageDerived.metrics.source_terms_reviewed_records) || 0,
        resultCount: pageDerivedCatalogResults.length,
        freshnessMatch,
        stale: Boolean(pageDerived.stale),
      };
      if (!freshnessMatch) {
        const warning = " Catalog analysis is behind the current page index or freshness could not be verified.";
        pageDerivedCatalogResults = pageDerivedCatalogResults.map((item) => ({
          ...item,
          snippet: String(item.snippet || "") + warning,
          description: String(item.description || item.snippet || "") + warning,
        }));
      }
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      console.error("Page-derived catalog preview unavailable:", detail.slice(0, 160));
    }

    const attachCatalogPreview = (payload: Record<string, any>) => {
      const fallbackResults = Array.isArray(payload.catalogResults) ? payload.catalogResults : [];
      const useDirect = pageDerivedCatalogResults.length > 0 || fallbackResults.length === 0;
      return {
        ...payload,
        catalogResults: useDirect ? pageDerivedCatalogResults : fallbackResults,
        catalogPreview: useDirect
          ? pageDerivedCatalogMeta
          : (payload.catalogPreview || pageDerivedCatalogMeta),
      };
    };
    // Prefer the first-party public crawler index during database outages.
    // The independent Bing RSS source is only a supplement when the owned index
    // has no matching pages or cannot be loaded.

    let crawlerIndexCount: number | null = null;
    let catalogLoaded = false;
    let catalogTransport: "gzip" | "query-shards" | null = null;

    try {
      const indexed = await searchStaticIndex(query, validLane, limit, offset);
      const found = indexed.found;
      catalogLoaded = true;
      catalogTransport = indexed.transport;
      crawlerIndexCount = found.count;

      if (found.results.length) {
        return json(attachCatalogPreview({
          results: found.results,
          count: found.count,
          query,
          tentacle: indexed.laneFallback ? null : validLane,
          requestedTentacle: validLane,
          laneFallback: indexed.laneFallback,
          source: "Everything Hub public crawler index",
          indexUpdatedAt: indexed.updatedAt,
          indexIntegrity: "sha256",
          indexTransport: indexed.transport,
          offset,
          limit,
          hasMore: found.hasMore,
          fallback: true,
          fallbackReason: reason,
        }), 200, "public, max-age=30, stale-while-revalidate=60");
      }
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      console.error("Static crawler index fallback unavailable:", detail);
    }

    const independent = await independentFallback(reason, crawlerIndexCount, attachCatalogPreview);
    if (independent) return independent;

    if (catalogLoaded) {
      return json(attachCatalogPreview({
        results: [],
        count: 0,
        query,
        tentacle: validLane,
        requestedTentacle: validLane,
        laneFallback: false,
        source: "Everything Hub public crawler index",
        indexIntegrity: "sha256",
        indexTransport: catalogTransport ?? "query-shards",
        offset,
        limit,
        hasMore: false,
        fallback: true,
        fallbackReason: reason,
      }), 200, "public, max-age=30, stale-while-revalidate=60");
    }

    if (pageDerivedCatalogResults.length) {
      return json(attachCatalogPreview({
        results: [],
        count: 0,
        query,
        tentacle: validLane,
        requestedTentacle: validLane,
        laneFallback: false,
        source: "Everything Hub page-derived catalog preview",
        offset,
        limit,
        hasMore: false,
        fallback: true,
        fallbackReason: reason,
        independentOfDatabase: true,
      }), 200, "public, max-age=15, stale-while-revalidate=30");
    }

    return unavailable();
  };

  try {
    let data: any[] = [];
    let totalCount = 0;
    let source = "Everything Hub eh_quad index";

    if (offset === 0) {
      const rpc = await db.rpc("eh_search_pages", {
        query_text: query, lane_filter: validLane, result_limit: limit,
      });
      if (!rpc.error) {
        data = rpc.data || [];
        totalCount = Number(data[0]?.total_count ?? data.length);
      } else {
        console.error("Ranked eh_quad search unavailable:", rpc.error.message);
        if (isTransientDatabaseError(rpc.error)) {
          return await staticFallback("database_unavailable");
        }
        source = "Everything Hub eh_quad fallback index";
      }
    }

    let laneFallback = false;
    if (offset === 0 && validLane && data.length < 3) {
      const broad = await db.rpc("eh_search_pages", {
        query_text: query, lane_filter: null, result_limit: limit,
      });
      if (broad.error) {
        console.error("Broadened eh_quad search unavailable:", broad.error.message);
        if (isTransientDatabaseError(broad.error)) {
          return await staticFallback("database_unavailable");
        }
      } else if ((broad.data || []).length > data.length) {
        data = broad.data || [];
        totalCount = Number(data[0]?.total_count ?? data.length);
        laneFallback = true;
        source = "Everything Hub eh_quad index (broadened for relevance)";
      }
    }

    if (offset > 0 || source === "Everything Hub eh_quad fallback index") {
      let fallbackQuery: any = db
        .from("eh_documents")
        .select("canonical_url,title,description,lane,fetched_at", { count: "exact" })
        .textSearch("search_vector", query, { config: "english", type: "websearch" })
        .order("fetched_at", { ascending: false })
        .range(offset, offset + limit - 1);
      if (validLane) fallbackQuery = fallbackQuery.eq("lane", validLane);
      const fallback = await fallbackQuery;
      if (fallback.error) {
        console.error("eh_quad paged index search failed:", fallback.error.message);
        return await staticFallback("database_unavailable");
      }
      data = (fallback.data || []).map((row: any) => ({
        url: row.canonical_url, title: row.title, description: row.description,
        tentacle: row.lane, fetched_at: row.fetched_at,
      }));
      totalCount = Number(fallback.count ?? data.length);
    }

    const results = data.map((row: any) => ({
      title: row.title || row.url,
      url: row.url,
      snippet: String(row.description || "").replace(/\s+/g, " ").slice(0, 320),
      source: (() => { try { return new URL(row.url).hostname; } catch { return "Everything Hub index"; } })(),
      origin: "Everything Hub eh_quad index",
      tentacle: row.tentacle || "general",
      fetched_at: row.fetched_at,
      relevance: row.relevance ?? 0,
    }));

    if (!results.length) return await staticFallback("database_empty");

    const hasMore = offset + results.length < totalCount;
    return json(
      { results, count: totalCount, query, tentacle: laneFallback ? null : validLane, requestedTentacle: validLane, laneFallback, source, offset, limit, hasMore },
      200, "public, max-age=15, stale-while-revalidate=30",
    );
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    console.error("eh_quad search request failed:", detail);
    if (isTransientDatabaseError(error)) {
      return await staticFallback("database_unavailable");
    }
    return unavailable();
  }
});
