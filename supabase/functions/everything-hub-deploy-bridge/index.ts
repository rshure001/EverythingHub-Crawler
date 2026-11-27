import "jsr:@supabase/functions-js/edge-runtime.d.ts";

const TARGET = "https://cdn.jsdelivr.net/gh/rshure001/EverythingHub-Crawler@main/site/index.html";
const ASSET_BASE = "https://cdn.jsdelivr.net/gh/rshure001/EverythingHub-Crawler@main/site/";

Deno.serve(async (req: Request) => {
  if (req.method !== "GET" && req.method !== "HEAD") {
    return new Response("Method not allowed", { status: 405, headers: { Allow: "GET, HEAD" } });
  }

  const incoming = new URL(req.url);
  const upstream = new URL(TARGET);
  for (const key of ["q", "mode"]) {
    const value = incoming.searchParams.get(key);
    if (value) upstream.searchParams.set(key, value);
  }

  try {
    const response = await fetch(upstream.toString(), {
      method: req.method === "HEAD" ? "HEAD" : "GET",
      headers: { "Accept": "text/html,*/*;q=0.8" },
    });
    if (!response.ok) {
      return new Response("Everything Hub homepage is temporarily unavailable.", {
        status: 502,
        headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" },
      });
    }

    if (req.method === "HEAD") {
      return new Response(null, {
        status: 200,
        headers: {
          "Content-Type": "text/html; charset=utf-8",
          "Cache-Control": "public, max-age=60, s-maxage=300",
          "X-EH-Bridge": "html-content-type-proxy",
        },
      });
    }

    let html = await response.text();
    if (!/^<!doctype html/i.test(html.trimStart())) {
      return new Response("Publisher returned an invalid homepage document.", {
        status: 502,
        headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" },
      });
    }

    if (!/<base\b/i.test(html)) {
      html = html.replace(/<head([^>]*)>/i, (head) => head + '<base href="' + ASSET_BASE + '">');
    }

    return new Response(html, {
      status: 200,
      headers: {
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "public, max-age=60, s-maxage=300",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "X-EH-Bridge": "html-content-type-proxy",
      },
    });
  } catch {
    return new Response("Everything Hub homepage could not be reached from the publishing bridge.", {
      status: 502,
      headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" },
    });
  }
});