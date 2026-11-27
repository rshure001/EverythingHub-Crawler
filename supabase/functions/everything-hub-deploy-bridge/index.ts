import "jsr:@supabase/functions-js/edge-runtime.d.ts";

const TARGET = "https://cdn.jsdelivr.net/gh/rshure001/EverythingHub-Crawler@main/site/index.html";

Deno.serve((req: Request) => {
  if (req.method !== "GET" && req.method !== "HEAD") {
    return new Response("Method not allowed", { status: 405, headers: { allow: "GET, HEAD" } });
  }
  const destination = new URL(TARGET);
  const incoming = new URL(req.url);
  for (const key of ["q", "mode"]) {
    const value = incoming.searchParams.get(key);
    if (value) destination.searchParams.set(key, value);
  }
  return new Response(null, {
    status: 302,
    headers: {
      location: destination.toString(),
      "cache-control": "no-store, max-age=0",
      "referrer-policy": "strict-origin-when-cross-origin",
      "x-eh-bridge": "cdn-redirect-v1",
    },
  });
});
