import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "npm:@supabase/supabase-js@2.49.1";
import { boundedInteger, normalizeLane, normalizeQuery } from "./params.mjs";
import { createTimedFetch, isTransientDatabaseError } from "./db-fetch.mjs";
import { loadPageDerivedCatalogPreview, resolveRecordRelatedCatalogPreview } from "./page-derived-catalog-preview.mjs";
import { searchPublicCrawlerIndex } from "./static-query-index.mjs";

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
    const attachCatalogPreview = (payload: Record<string, any>) => ({
      ...payload,
      catalogResults: [],
      catalogPreview: {
        status: "deferred",
        previewOnly: true,
        resultCount: 0,
        recordsPublished: 0,
        sourceTermsReviewedRecords: 0,
        commercialEligibleRecords: 0,
        rightsReviewRequired: true,
        error: "catalog_preview_deferred_from_search_path",
      },
    });

    try {
      let indexed: any = await searchPublicCrawlerIndex(query, validLane, limit, offset);
      let laneFallback = false;
      if (validLane && indexed.results.length < 3) {
        const broad: any = await searchPublicCrawlerIndex(query, null, limit, offset);
        if (broad.results.length > indexed.results.length) {
          indexed = broad;
          laneFallback = true;
        }
      }

      const indexedResults = (indexed.results || []).map((item: any) => ({
        ...item,
        snippet: String(item.snippet || item.description || item.text || "").replace(/\s+/g, " ").slice(0, 320),
        source: String(item.hostname || item.source || "Everything Hub index"),
        origin: "Everything Hub public crawler index",
        tentacle: item.lane || "general",
        relevance: Number(item.rankScore || 0),
      }));

      let externalPayload: any = null;
      if (indexedResults.length < limit) {
        const externalResponse = await independentFallback(reason, indexed.indexedPages || null, attachCatalogPreview);
        if (externalResponse) {
          try { externalPayload = await externalResponse.json(); } catch { externalPayload = null; }
        }
      }

      const externalResults = Array.isArray(externalPayload?.results) ? externalPayload.results : [];
      const seen = new Set<string>();
      const merged: any[] = [];
      for (const item of [...indexedResults, ...externalResults]) {
        const rawUrl = String(item?.url || "");
        try {
          const parsed = new URL(rawUrl);
          if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password) continue;
          if (seen.has(parsed.href)) continue;
          seen.add(parsed.href);
          merged.push({ ...item, url: parsed.href });
        } catch {
          continue;
        }
      }

      if (!merged.length) {
        if (externalPayload) return json(externalPayload, 200, "public, max-age=15, stale-while-revalidate=30");
        return unavailable();
      }

      const externalCount = Number(externalPayload?.count || 0);
      const duplicateCount = indexedResults.length + externalResults.length - merged.length;
      const count = Math.max(
        merged.length,
        Number(indexed.count || 0) + externalCount - Math.max(0, duplicateCount),
      );
      const result = {
        results: merged.slice(0, limit),
        count,
        query,
        tentacle: laneFallback ? null : validLane,
        requestedTentacle: validLane,
        laneFallback,
        source: externalResults.length
          ? "Everything Hub crawler index + Bing RSS"
          : "Everything Hub public crawler index",
        provider: externalResults.length
          ? "Everything Hub query index and independent Bing RSS fallback"
          : "Everything Hub public query index",
        offset,
        limit,
        hasMore: Boolean(indexed.hasMore || externalPayload?.hasMore || merged.length > limit),
        fallback: true,
        fallbackReason: reason,
        independentOfDatabase: true,
        crawlerIndexCount: indexed.indexedPages || null,
        staticSearch: {
          updatedAt: indexed.updatedAt || null,
          scannedPages: indexed.scannedPages || 0,
          candidatePageCount: indexed.candidatePageCount || 0,
          candidateShardCount: indexed.candidateShardCount || 0,
          totalCandidateShardCount: indexed.totalCandidateShardCount || 0,
          candidateTruncated: Boolean(indexed.candidateTruncated),
          queryIndexTokensUsed: indexed.queryIndexTokensUsed || 0,
          queryIndexPostingCount: indexed.queryIndexPostingCount || 0,
        },
        catalogResults: [],
        catalogPreview: {
          status: "deferred",
          previewOnly: true,
          resultCount: 0,
          recordsPublished: 0,
          sourceTermsReviewedRecords: 0,
          commercialEligibleRecords: 0,
          rightsReviewRequired: true,
          error: "catalog_preview_deferred_from_search_path",
        },
      };
      return json(result, 200, "public, max-age=15, stale-while-revalidate=30");
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      console.error("Verified static crawler search unavailable:", detail.slice(0, 180));
      const independent = await independentFallback(reason, null, attachCatalogPreview);
      if (independent) return independent;
      return unavailable();
    }
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

    // Keep the hot search endpoint lightweight during PostgreSQL recovery.
    // Catalog candidates are served by the dedicated query-directed preview path.
    const catalogResults: any[] = [];
    const catalogPreview: Record<string, any> = {
      status: "deferred",
      previewOnly: true,
      resultCount: 0,
      recordsPublished: 0,
      sourceTermsReviewedRecords: 0,
      commercialEligibleRecords: 0,
      rightsReviewRequired: true,
      error: "catalog_preview_deferred_from_search_path",
    };

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
