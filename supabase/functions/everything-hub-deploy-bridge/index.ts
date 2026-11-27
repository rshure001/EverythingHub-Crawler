import "jsr:@supabase/functions-js/edge-runtime.d.ts";

const TARGET = "https://cdn.jsdelivr.net/gh/rshure001/EverythingHub-Crawler@main/site/index.html";
const ASSET_BASE = "https://cdn.jsdelivr.net/gh/rshure001/EverythingHub-Crawler@main/site/";

Deno.serve(async (req: Request) => {
  if (req.method !== "GET" && req.method !== "HEAD") {
    return new Response("Method not allowed", { status: 405, headers: { allow: "GET, HEAD" } });
  }

  const upstream = new URL(TARGET);
  const incoming = new URL(req.url);
  for (const key of ["q", "mode"]) {
    const value = incoming.searchParams.get(key);
    if (value) upstream.searchParams.set(key, value);
  }

  try {
    const upstreamResponse = await fetch(upstream, { method: req.method === "HEAD" ? "HEAD" : "GET" });
    if (!upstreamResponse.ok) throw new Error("upstream status " + upstreamResponse.status);

    const headers = new Headers();
    headers.set("content-type", "text/html; charset=utf-8");
    headers.set("cache-control", "public, max-age=60, s-maxage=300");
    headers.set("referrer-policy", "strict-origin-when-cross-origin");
    headers.set("x-eh-bridge", "html-content-type-proxy-v2");

    if (req.method === "HEAD") return new Response(null, { status: 200, headers });

    let html = await upstreamResponse.text();
    if (!/^<!doctype html/i.test(html.trimStart())) throw new Error("upstream did not return HTML");
    if (!/<base\b/i.test(html)) {
      html = html.replace(/<head([^>]*)>/i, (head) => head + '<base href="' + ASSET_BASE + '">');
    }
    return new Response(new Blob([html], { type: "text/html;charset=UTF-8" }), { status: 200, headers });
  } catch (error) {
    return new Response("Everything Hub publishing bridge error: " + String(error), {
      status: 502,
      headers: { "content-type": "text/plain; charset=utf-8", "cache-control": "no-store" },
    });
  }
});