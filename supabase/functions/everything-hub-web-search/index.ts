import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "npm:@supabase/supabase-js@2.49.1";
import { boundedInteger, normalizeLane, normalizeQuery } from "./params.mjs";
import { createTimedFetch, isTransientDatabaseError } from "./db-fetch.mjs";
import { searchStaticPages } from "./static-search.mjs";
import { normalizeManifestTimestamp } from "./manifest-time.mjs";
import { isValidStaticManifest } from "./static-manifest.mjs";
import { readBoundedText } from "./bounded-stream.mjs";
import { loadPageDerivedCatalogPreview, resolveRecordRelatedCatalogPreview } from "./page-derived-catalog-preview.mjs";

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
  mode: "gzip" | "shards";
  queryKey?: string;
  expiresAt: number;
};

let staticCatalogCache: StaticCatalog | null = null;

async function loadStaticCatalog(query: string): Promise<StaticCatalog> {
  if (staticCatalogCache && staticCatalogCache.expiresAt > Date.now() &&
      (staticCatalogCache.mode === "gzip" || staticCatalogCache.queryKey === String(query || "").trim().toLowerCase())) {
    return staticCatalogCache;
  }

  const pending = (async () => {
    // Bound the complete first-party catalog load, not just each shard request.
    const loadSignal = AbortSignal.timeout(STATIC_INDEX_LOAD_TIMEOUT_MS);
    const headers = {
      Accept: "application/json",
      "User-Agent": "EverythingHubSearch/1.0 (+https://github.com/rshure001/EverythingHub-Crawler)",
    };
    const manifestResponse = await fetch(STATIC_INDEX_BASE + "/manifest.json", {
      headers,
      signal: loadSignal,
    });
    if (!manifestResponse.ok) {
      throw new Error("Public crawler manifest returned HTTP " + manifestResponse.status);
    }

    const manifest: any = await manifestResponse.json();
    if (!isValidStaticManifest(manifest)) {
      throw new Error("Public crawler manifest failed structural validation");
    }

    const pages: any[] = [];
    let catalogMode: "gzip" | "shards" = "shards";
    const compressed = manifest.catalog_gzip;

    // Large public indexes are searched by fetching only the token-prefix
    // indexes and page shards that can contain every query term.
    if (!compressed) {
      const queryIndex = manifest.query_index;
      if (!queryIndex || queryIndex.version !== 1 || !Array.isArray(queryIndex.shards) ||
          queryIndex.shards.length < 1 || queryIndex.shards.length > 256) {
        throw new Error("No bounded query-directed crawler index is published");
      }
      const normalizedQuery = String(query || "").normalize("NFKC").toLowerCase();
      const unicodeTerms = normalizedQuery.match(/[\p{L}\p{N}]+/gu) || [];
      const asciiFallbackTerms = normalizedQuery.match(/[a-z0-9]+/g) || [];
      const terms = [...new Set(unicodeTerms.length ? unicodeTerms : asciiFallbackTerms)].slice(0, 12);
      if (!terms.length) throw new Error("Search query has no indexable terms (length=" + normalizedQuery.length + ")");
      const prefixByTerm = new Map<string, string>();
      for (const term of terms) {
        const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(term));
        const prefix = Array.from(new Uint8Array(digest)).slice(0, 1)
          .map((value) => value.toString(16).padStart(2, "0")).join("");
        prefixByTerm.set(term, prefix);
      }
      const indexByPrefix = new Map<string, any>();
      for (const prefix of new Set(prefixByTerm.values())) {
        const entry = queryIndex.shards.find((item: any) =>
          typeof item?.file === "string" && item.file === "query-index/" + prefix + ".json.gz");
        if (!entry || !Number.isInteger(entry.bytes) || entry.bytes < 2 ||
            entry.bytes > 2_000_000 || !/^[a-f0-9]{64}$/.test(String(entry.sha256 || ""))) {
          throw new Error("Query-index prefix metadata is missing or invalid");
        }
        const response = await fetch(STATIC_INDEX_BASE + "/" + entry.file, { headers, signal: loadSignal });
        if (!response.ok) throw new Error("Query-index shard returned HTTP " + response.status);
        const bytes = new Uint8Array(await response.arrayBuffer());
        if (bytes.length !== entry.bytes) throw new Error("Query-index shard byte length mismatch");
        const digest = await crypto.subtle.digest("SHA-256", bytes);
        const actual = Array.from(new Uint8Array(digest)).map((value) => value.toString(16).padStart(2, "0")).join("");
        if (actual !== entry.sha256) throw new Error("Query-index shard SHA-256 mismatch");
        const decoded = await readBoundedText(
          new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip")), 8_000_000,
        );
        const payload: any = JSON.parse(decoded);
        if (payload?.version !== 1 || payload?.prefix !== prefix || !payload?.tokens ||
            typeof payload.tokens !== "object" || Array.isArray(payload.tokens)) {
          throw new Error("Query-index shard schema is invalid");
        }
        indexByPrefix.set(prefix, payload.tokens);
      }
      let candidateShardIds: Set<number> | null = null;
      for (const term of terms) {
        const tokens = indexByPrefix.get(prefixByTerm.get(term)!) || {};
        const postings = tokens[term];
        if (!Array.isArray(postings)) { candidateShardIds = new Set<number>(); break; }
        const ids = new Set<number>();
        for (const pair of postings) {
          if (Array.isArray(pair) && Number.isInteger(pair[0]) && pair[0] >= 0 &&
              pair[0] < manifest.shards.length && Number.isInteger(pair[1]) && pair[1] > 0) ids.add(pair[0]);
        }
        candidateShardIds = candidateShardIds === null
          ? ids : new Set<number>([...candidateShardIds].filter((id: number) => ids.has(id)));
        if (!candidateShardIds.size) break;
      }
      const selectedIds = [...(candidateShardIds || new Set<number>())].slice(0, 48);
      for (const shardId of selectedIds) {
        const entry = manifest.shards[shardId];
        if (!entry || typeof entry.file !== "string" || !Number.isInteger(entry.bytes) ||
            entry.bytes < 2 || entry.bytes > 350_000 ||
            !/^[a-f0-9]{64}$/.test(String(entry.sha256 || ""))) {
          throw new Error("Page-shard metadata is invalid");
        }
        const response = await fetch(STATIC_INDEX_BASE + "/" + entry.file, { headers, signal: loadSignal });
        if (!response.ok) throw new Error("Page shard returned HTTP " + response.status);
        const bytes = new Uint8Array(await response.arrayBuffer());
        if (bytes.length !== entry.bytes) throw new Error("Page-shard byte length mismatch");
        const digest = await crypto.subtle.digest("SHA-256", bytes);
        const actual = Array.from(new Uint8Array(digest)).map((value) => value.toString(16).padStart(2, "0")).join("");
        if (actual !== entry.sha256) throw new Error("Page-shard SHA-256 mismatch");
        const decoded = await readBoundedText(
          new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip")), 600_000,
        );
        const payload: any = JSON.parse(decoded);
        if (payload?.version !== 1 || !Array.isArray(payload?.pages) ||
            payload.pages.length !== entry.pages) throw new Error("Page-shard schema or count mismatch");
        pages.push(...payload.pages);
      }
      const catalog: StaticCatalog = {
        pages,
        updatedAt: normalizeManifestTimestamp(manifest.updated_at),
        mode: "shards",
        queryKey: String(query || "").trim().toLowerCase(),
        expiresAt: Date.now() + 15_000,
      };
      staticCatalogCache = catalog;
      return catalog;
    }

    if (compressed) {
      try {
        if (
          compressed.file !== "catalog-v1.json.gz" ||
          !Number.isInteger(compressed.bytes) ||
          compressed.bytes < 2 ||
          compressed.bytes > MAX_STATIC_CATALOG_GZIP_BYTES ||
          compressed.pages !== manifest.total_pages ||
          typeof compressed.sha256 !== "string" ||
          !/^[a-f0-9]{64}$/.test(compressed.sha256)
        ) {
          throw new Error("Compressed catalog metadata is invalid");
        }

        const response = await fetch(STATIC_INDEX_BASE + "/" + compressed.file, {
          headers,
          signal: loadSignal,
        });
        if (!response.ok) {
          throw new Error("Compressed catalog returned HTTP " + response.status);
        }
        const bytes = new Uint8Array(await response.arrayBuffer());
        if (bytes.length !== compressed.bytes) {
          throw new Error("Compressed catalog byte length does not match the manifest");
        }
        const digestBuffer = await crypto.subtle.digest("SHA-256", bytes);
        const digest = Array.from(new Uint8Array(digestBuffer))
          .map((value) => value.toString(16).padStart(2, "0"))
          .join("");
        if (digest !== compressed.sha256) {
          throw new Error("Compressed catalog SHA-256 does not match the manifest");
        }

        const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
        const decoded = await readBoundedText(stream, MAX_STATIC_CATALOG_UNCOMPRESSED_BYTES);
        const payload: any = JSON.parse(decoded);
        if (
          payload?.version !== 1 ||
          !Array.isArray(payload?.pages) ||
          payload.pages.length !== manifest.total_pages
        ) {
          throw new Error("Compressed catalog page count or schema is invalid");
        }
        pages.push(...payload.pages);
        catalogMode = "gzip";
      } catch (error) {
        const detail = error instanceof Error ? error.message : String(error);
        console.error("Compressed catalog unavailable; using verified shards:", detail);
        pages.length = 0;
      }
    }

    if (!pages.length) {
      throw new Error("No bounded compressed crawler catalog is available; full shard scans are not attempted inside the Edge Function CPU budget");
    }

    if (pages.length !== manifest.total_pages) {
      throw new Error("Public crawler page total does not match the manifest");
    }

    const catalog: StaticCatalog = {
      pages,
      updatedAt: normalizeManifestTimestamp(manifest.updated_at),
      mode: catalogMode,
      expiresAt: Date.now() + STATIC_INDEX_CACHE_MS,
    };
    staticCatalogCache = catalog;
    return catalog;
  })();

  return await pending;
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
    // Keep catalog preview out of the latency-sensitive search path. Catalog
    // analysis is a separate workload and must not exhaust the Edge worker.
    const attachCatalogPreview = (payload: Record<string, any>) => ({
      ...payload,
      catalogResults: [],
      catalogPreview: {
        status: "deferred",
        previewOnly: true,
        resultCount: 0,
        error: "catalog_preview_deferred_from_search_path",
      },
    });

    // Prefer the first-party public crawler index during database outages.
    // The independent Bing RSS source is only a supplement when the owned index
    // has no matching pages or cannot be loaded.

    let crawlerIndexCount: number | null = null;
    let catalogLoaded = false;
    let catalogTransport: "gzip" | "shards" | null = null;

    try {
      const catalog = await loadStaticCatalog(query);
      catalogLoaded = true;
      catalogTransport = catalog.mode;
      let found = searchStaticPages(catalog.pages, query, validLane, limit, offset);
      let laneFallback = false;

      if (validLane && found.count < 3) {
        const broad = searchStaticPages(catalog.pages, query, null, limit, offset);
        if (broad.count > found.count) {
          found = broad;
          laneFallback = true;
        }
      }
      crawlerIndexCount = found.count;

      if (found.results.length) {
        return json(attachCatalogPreview({
          results: found.results,
          count: found.count,
          query,
          tentacle: laneFallback ? null : validLane,
          requestedTentacle: validLane,
          laneFallback,
          source: "Everything Hub public crawler index",
          indexUpdatedAt: catalog.updatedAt,
          indexIntegrity: "sha256",
          indexTransport: catalog.mode,
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
        indexTransport: catalogTransport ?? "shards",
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
      { results, count: totalCount, query, tentacle: laneFallback ? null : validLane, requestedTentacle: validLane, laneFallback, source, offset, limit, hasMore, catalogResults, catalogPreview },
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
