import "jsr:@supabase/functions-js/edge-runtime.d.ts";

// Keep the public homepage as a lightweight redirect. Do not proxy HTML through
// the Edge gateway: it has previously rewritten proxied HTML to text/plain.
const PUBLISHED_INDEX =
  "https://cdn.jsdelivr.net/gh/rshure001/EverythingHub-Crawler@main/site/index.html";

Deno.serve((req: Request) => {
  if (req.method !== "GET" && req.method !== "HEAD") {
    return new Response("Method not allowed", {
      status: 405,
      headers: { allow: "GET, HEAD", "cache-control": "no-store" },
    });
  }

  const incoming = new URL(req.url);
  const destination = new URL(PUBLISHED_INDEX);
  for (const [key, value] of incoming.searchParams.entries()) {
    destination.searchParams.set(key, value);
  }

  return new Response(null, {
    status: 302,
    headers: {
      location: destination.toString(),
      "cache-control": "no-store, no-cache, must-revalidate, max-age=0",
      "referrer-policy": "strict-origin-when-cross-origin",
      "x-eh-bridge": "jsdelivr-static-v1",
      "x-eh-publish-target": "github-main-site-index",
    },
  });
});
