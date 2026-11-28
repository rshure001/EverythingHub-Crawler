import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { boundedInteger, normalizeQuery } from "./params.mjs";
import { loadPageDerivedCatalogPreview, searchPageDerivedCatalogPreview } from "./page-derived-catalog-preview.mjs";
import { searchPublicCrawlerIndex } from "./static-query-index.mjs";

const cors = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Content-Type": "application/json; charset=utf-8",
};
const json = (body: unknown, status = 200, cache = "no-store") =>
  new Response(JSON.stringify(body), { status, headers: { ...cors, "Cache-Control": cache } });
const cleanPageTitle = (value: unknown) => {
  let title = String(value || "Untitled page").replace(/\s+/g, " ").trim();
  title = title.replace(/\s+(?:[A-Za-z0-9&'’-]+\s+){1,5}Logo\s+(?:Desktop|Mobile)\b.*$/i, "");
  title = title.replace(/\s+(?:Hamburger Menu Icon|Close Button|Account Information|Skip to content)\b.*$/i, "");
  return title.slice(0, 240);
};

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
  if (req.method !== "GET") return json({ error: "GET required" }, 405);

  const url = new URL(req.url);
  const query = normalizeQuery(url.searchParams.get("q"));
  const limit = boundedInteger(url.searchParams.get("limit"), 5, 1, 8);
  if (query.length < 2) {
    return json({
      catalogResults: [],
      catalogPreview: { status: "candidate-preview", previewOnly: true, resultCount: 0 },
      query,
    }, 200, "public, max-age=15, stale-while-revalidate=30");
  }

  let network: any = null;
  let catalogResults: any[] = [];
  let previewError: string | null = null;
  let dynamicMetrics: Record<string, any> | null = null;

  try {
    network = await loadPageDerivedCatalogPreview();
    catalogResults = await searchPageDerivedCatalogPreview(query, network, limit);
  } catch (error) {
    previewError = error instanceof Error ? error.message : String(error);
    console.error("Page-derived catalog preview unavailable:", previewError.slice(0, 180));
  }

  if (!catalogResults.length) {
    try {
      const pageSearch = await searchPublicCrawlerIndex(query, null, limit, 0);
      const pages = Array.isArray(pageSearch.results) ? pageSearch.results : [];
      const queryTerms = query.toLowerCase().split(/\s+/).filter(Boolean);
      const titleUrlMatches = pages.filter((page: any) => {
        const searchable = (String(page.title || "") + " " + String(page.url || "")).toLowerCase();
        return queryTerms.length > 1 ? queryTerms.every((term: string) => searchable.includes(term)) : searchable.includes(queryTerms[0] || "");
      });
      const previewPages = titleUrlMatches.length ? titleUrlMatches : pages;
      if (previewPages.length) {
        const topicId = "query:" + encodeURIComponent(query.slice(0, 120));
        const matchedRecords = previewPages.slice(0, 5).map((page: any, index: number) => {
          const title = cleanPageTitle(page.title);
          const entryQuery = title.trim().slice(0, 120) || query.slice(0, 120);
          const entryId = "query:" + encodeURIComponent(entryQuery);
          const sourceUrl = String(page.url || "");
          return {
            record_id: "page:" + encodeURIComponent(sourceUrl).slice(0, 120) + ":" + String(index + 1),
            record_catalog_id: entryId,
            shared_catalog_ids: [entryId, topicId],
            title,
            record_type: "page",
            source_url: sourceUrl,
            description: String(page.snippet || page.description || page.text || "").slice(0, 700),
            last_seen_at: page.fetchedAt || page.publishedAt || null,
            source_policy_review_status: "not_reviewed",
            record_level_clearance: "not_reviewed",
            commercial_eligible: false,
            preview_only: true,
          };
        });
        const partial = Boolean(pageSearch.candidateTruncated);
        const title = query + " — indexed page collection preview" + (partial ? " (partial)" : "");
        catalogResults = [{
          catalog_id: topicId,
          catalog_title: title,
          title,
          catalog_type: "query-derived page collection",
          record_type: "page",
          catalog_status: "candidate_needs_source_validation",
          catalog_origin: "public-crawler-query-index",
          evidence_strength: "medium",
          source_terms_status: "not_reviewed",
          source_policy_review_status: "not_reviewed",
          record_level_clearance: "not_reviewed",
          rights_status: "publicly_accessible_not_cleared_for_redistribution",
          commercial_eligible: false,
          preview_only: true,
          query_intent: "topic",
          relevance_class: "query-derived-page-collection",
          relevance_label: "indexed page collection preview",
          record_count: matchedRecords.length,
          source_page_count: Number(pageSearch.candidatePageCount) || pages.length,
          matched_records: matchedRecords,
          url: String(pages[0]?.url || ""),
          snippet: "Query-indexed page collection preview. Counts reflect a bounded candidate set; source terms and individual record rights have not been cleared.",
        }];
        dynamicMetrics = {
          indexedPages: Number(pageSearch.indexedPages) || 0,
          candidatePageCount: Number(pageSearch.candidatePageCount) || pages.length,
          candidateShardCount: Number(pageSearch.candidateShardCount) || 0,
          totalCandidateShardCount: Number(pageSearch.totalCandidateShardCount) || 0,
          candidateTruncated: partial,
          recordCount: matchedRecords.length,
          updatedAt: pageSearch.updatedAt || null,
        };
      }
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      console.error("Query-derived catalog preview unavailable:", detail.slice(0, 180));
      previewError = previewError || detail;
    }
  }

  const freshnessMatch = Boolean(network?.freshnessMatch);
  const status = dynamicMetrics
    ? "query-derived-candidate-preview"
    : network
      ? (freshnessMatch ? "candidate-preview" : "stale-candidate-preview")
      : catalogResults.length
        ? "candidate-preview"
        : (previewError ? "unavailable" : "no-catalog-match");

  return json({
    query,
    catalogResults,
    catalogPreview: {
      status,
      previewOnly: true,
      analysisVersion: network?.analysisVersion || null,
      generatedAt: network?.generatedAt || null,
      sourceIndexPages: Number(network?.sourceIndexPages) || dynamicMetrics?.indexedPages || 0,
      sourceIndexUpdatedAt: network?.sourceIndexUpdatedAt || dynamicMetrics?.updatedAt || null,
      pageIndexPages: Number(network?.pageIndexPages) || dynamicMetrics?.indexedPages || 0,
      pageIndexUpdatedAt: network?.pageIndexUpdatedAt || dynamicMetrics?.updatedAt || null,
      catalogCount: Number(network?.catalogCount) || (dynamicMetrics ? 1 : 0),
      recordCount: Number(network?.recordCount) || dynamicMetrics?.recordCount || 0,
      recordsPublished: Number(network?.metrics?.records_published) || 0,
      sourceTermsReviewedRecords: Number(network?.metrics?.source_terms_reviewed_records) || 0,
      resultCount: catalogResults.length,
      freshnessMatch,
      stale: Boolean(network?.stale),
      queryIndexed: Boolean(dynamicMetrics),
      candidatePageCount: dynamicMetrics?.candidatePageCount || 0,
      candidateShardCount: dynamicMetrics?.candidateShardCount || 0,
      totalCandidateShardCount: dynamicMetrics?.totalCandidateShardCount || 0,
      candidateTruncated: Boolean(dynamicMetrics?.candidateTruncated),
      rightsReviewRequired: Boolean(dynamicMetrics),
      error: previewError ? "some_catalog_preview_sources_unavailable" : undefined,
    },
  }, 200, "public, max-age=15, stale-while-revalidate=30");
});