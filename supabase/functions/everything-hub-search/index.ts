import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "npm:@supabase/supabase-js@2.49.1";

const supabaseUrl = Deno.env.get("SUPABASE_URL")!;
const serviceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;

// This legacy JWT-protected endpoint is explicitly confined to Everything Hub's
// dedicated schema. It must never fall back to public or shared application tables.
const db = createClient(supabaseUrl, serviceKey, {
  auth: { persistSession: false, autoRefreshToken: false },
}).schema("eh_quad");

const cors = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Content-Type": "application/json",
};
const lanes = ["general", "science", "news", "technology", "business", "government", "culture", "nature"];

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors });
  if (req.method !== "POST") return Response.json({ error: "POST required" }, { status: 405, headers: cors });

  try {
    const body = await req.json().catch(() => ({}));
    const query = String(body.query || "").trim().slice(0, 160);
    const lane = String(body.tentacle || "").trim().toLowerCase();
    const limit = Math.max(1, Math.min(20, Number(body.limit) || 10));
    if (query.length < 2) {
      return Response.json({ results: [], total: 0, message: "Enter at least two characters." }, { headers: cors });
    }

    const { data, error } = await db.rpc("eh_search_pages", {
      query_text: query,
      lane_filter: lanes.includes(lane) ? lane : null,
      result_limit: limit,
    });
    if (error) {
      console.error("eh_quad ranked search unavailable:", error.message);
      return Response.json({ error: "Search temporarily unavailable" }, { status: 503, headers: cors });
    }

    const results = (data || []).map((p: any) => ({
      title: p.title || p.url,
      url: p.url,
      description: p.description || "",
      tentacle: p.tentacle || "general",
      fetched_at: p.fetched_at,
      score: p.relevance,
    }));
    return Response.json({
      query,
      results,
      total: data?.[0]?.total_count ?? 0,
      source: "Everything Hub eh_quad relevance-ranked full-text index",
      lanes,
    }, { headers: cors });
  } catch (error) {
    console.error("eh_quad search request failed:", String(error).slice(0, 300));
    return Response.json({ error: "Search temporarily unavailable" }, { status: 503, headers: cors });
  }
});
