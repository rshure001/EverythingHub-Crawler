import "jsr:@supabase/functions-js/edge-runtime.d.ts";

// Free first-party publishing route: serve the already-built, versioned homepage
// from this Supabase project's own Edge Function instead of a third-party CDN.
const PUBLISHED_INDEX =
  "https://vdceayvfgkwpgwixrhwi.supabase.co/functions/v1/everything-hub-homepage-release";

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
      "x-eh-bridge": "supabase-first-party-html-v1",
      "x-eh-publish-target": "everything-hub-homepage-release",
    },
  });
});
