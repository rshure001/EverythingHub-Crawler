const SOURCE_URL = "https://vdceayvfgkwpgwixrhwi.supabase.co/functions/v1/everything-hub-homepage-release";

Deno.serve(async (req) => {
  if (req.method !== "GET" && req.method !== "HEAD") {
    return new Response("Method not allowed", {
      status: 405,
      headers: { "content-type": "text/plain; charset=utf-8", "cache-control": "no-store" },
    });
  }

  try {
    const upstream = await fetch(SOURCE_URL, {
      method: "GET",
      headers: { accept: "text/html" },
      cache: "no-store",
    });
    const html = await upstream.text();

    if (!upstream.ok) {
      return new Response(html || "Homepage source unavailable", {
        status: upstream.status,
        headers: { "content-type": "text/plain; charset=utf-8", "cache-control": "no-store" },
      });
    }

    if (!/<!doctype html|<html[\\s>]/i.test(html)) {
      return new Response("Homepage source did not return an HTML document", {
        status: 502,
        headers: { "content-type": "text/plain; charset=utf-8", "cache-control": "no-store" },
      });
    }

    const headers = new Headers();
    headers.set("content-type", "text/html; charset=utf-8");
    headers.set("cache-control", "no-store, max-age=0");
    headers.set("x-content-type-options", "nosniff");
    headers.set("referrer-policy", "strict-origin-when-cross-origin");
    headers.set("x-eh-homepage-content-type", "html-v2");
    headers.set("x-eh-homepage-source-content-type", upstream.headers.get("content-type") || "unknown");

    return new Response(req.method === "HEAD" ? null : html, {
      status: 200,
      headers,
    });
  } catch (error) {
    return new Response("Homepage release proxy unavailable", {
      status: 502,
      headers: { "content-type": "text/plain; charset=utf-8", "cache-control": "no-store" },
    });
  }
});
