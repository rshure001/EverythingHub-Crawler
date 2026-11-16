import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "npm:@supabase/supabase-js@2.49.1";

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
  { auth: { persistSession: false, autoRefreshToken: false } },
);
const db = supabase.schema("eh_quad");

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
  if (req.method !== "GET") return json({ error: "GET required" }, 405);

  const url = new URL(req.url);
  const query = (url.searchParams.get("q") || "").trim().slice(0, 160);
  const lane = (url.searchParams.get("tentacle") || "").trim().toLowerCase();
  const lanes = ["general", "science", "news", "technology", "business", "government", "culture", "nature"];
  const limit = Math.min(20, Math.max(1, Math.trunc(Number(url.searchParams.get("limit") || 10))));
  const offset = Math.min(5000, Math.max(0, Math.trunc(Number(url.searchParams.get("offset") || 0))));
  const validLane = lanes.includes(lane) ? lane : null;

  if (query.length < 2) return json({ results: [], count: 0, query, offset, limit, hasMore: false });

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
      source = "Everything Hub eh_quad fallback index";
    }
  }

  let laneFallback = false;
  if (offset === 0 && validLane && data.length < 3) {
    const broad = await db.rpc("eh_search_pages", {
      query_text: query, lane_filter: null, result_limit: limit,
    });
    if (!broad.error && (broad.data || []).length > data.length) {
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
      return json({ results: [], count: 0, query, offset, limit, hasMore: false, error: "Search temporarily unavailable" }, 503);
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

  const hasMore = offset + results.length < totalCount;
  return json(
    { results, count: totalCount, query, tentacle: laneFallback ? null : validLane, requestedTentacle: validLane, laneFallback, source, offset, limit, hasMore },
    200, "public, max-age=15, stale-while-revalidate=30",
  );
});
