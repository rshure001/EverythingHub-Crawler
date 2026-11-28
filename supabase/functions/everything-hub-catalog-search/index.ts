import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { boundedInteger, normalizeQuery } from "./params.mjs";
import { loadPageDerivedCatalogPreview, searchPageDerivedCatalogPreview, resolveRecordRelatedCatalogPreview } from "./page-derived-catalog-preview.mjs";

const cors = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Content-Type": "application/json; charset=utf-8",
};
const json = (body: unknown, status = 200, cache = "no-store") =>
  new Response(JSON.stringify(body), { status, headers: { ...cors, "Cache-Control": cache } });

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
  if (req.method !== "GET") return json({ error: "GET required" }, 405);

  const url = new URL(req.url);
  const query = normalizeQuery(url.searchParams.get("q"));
  const limit = boundedInteger(url.searchParams.get("limit"), 5, 1, 8);
  const recordCatalogId = String(url.searchParams.get("record_catalog_id") || "").trim();
  if (recordCatalogId) {
    const sharedCatalogIds = String(url.searchParams.get("catalog_ids") || "")
      .split(",").map((id) => id.trim()).filter(Boolean);
    const relatedLimit = boundedInteger(url.searchParams.get("limit"), 20, 1, 50);
    const offset = boundedInteger(url.searchParams.get("offset"), 0, 0, 5000);
    try {
      const network = await loadPageDerivedCatalogPreview();
      const recordCatalog = await resolveRecordRelatedCatalogPreview(
        recordCatalogId, sharedCatalogIds, network, relatedLimit, offset,
      );
      if (!recordCatalog) return json({ error: "record_catalog_not_found" }, 404);
      return json({
        ...recordCatalog,
        catalogPreview: {
          status: network.freshnessMatch ? "candidate-preview" : "stale-candidate-preview",
          previewOnly: true,
          generatedAt: network.generatedAt || null,
          recordCount: Number(network.recordCount) || 0,
          recordsPublished: Number(network.metrics?.records_published) || 0,
          rightsReviewRequired: true,
        },
      }, 200, "public, max-age=15, stale-while-revalidate=30");
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      console.error("Record catalog preview unavailable:", detail.slice(0, 180));
      return json({ error: "record_catalog_preview_temporarily_unavailable" }, 503);
    }
  }
  if (query.length < 2) {
    return json({
      catalogResults: [],
      catalogPreview: { status: "candidate-preview", previewOnly: true, resultCount: 0 },
      query,
    }, 200, "public, max-age=15, stale-while-revalidate=30");
  }

  try {
    const network = await loadPageDerivedCatalogPreview();
    const catalogResults = await searchPageDerivedCatalogPreview(query, network, limit);
    const freshnessMatch = Boolean(network.freshnessMatch);
    const status = freshnessMatch ? "candidate-preview" : "stale-candidate-preview";
    return json({
      query,
      catalogResults,
      catalogPreview: {
        status,
        previewOnly: true,
        analysisVersion: network.analysisVersion,
        generatedAt: network.generatedAt || null,
        sourceIndexPages: network.sourceIndexPages || 0,
        sourceIndexUpdatedAt: network.sourceIndexUpdatedAt || null,
        pageIndexPages: network.pageIndexPages || 0,
        pageIndexUpdatedAt: network.pageIndexUpdatedAt || null,
        catalogCount: Number(network.catalogCount) || 0,
        recordCount: Number(network.recordCount) || 0,
        recordsPublished: Number(network.metrics?.records_published) || 0,
        sourceTermsReviewedRecords: Number(network.metrics?.source_terms_reviewed_records) || 0,
        resultCount: catalogResults.length,
        freshnessMatch,
        stale: Boolean(network.stale),
      },
    }, 200, "public, max-age=15, stale-while-revalidate=30");
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    console.error("Catalog search preview unavailable:", detail.slice(0, 180));
    return json({
      query,
      catalogResults: [],
      catalogPreview: {
        status: "unavailable",
        previewOnly: true,
        resultCount: 0,
        error: "catalog_preview_temporarily_unavailable",
      },
    }, 503);
  }
});