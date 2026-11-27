import "jsr:@supabase/functions-js/edge-runtime.d.ts";

// Serve the last verified public release produced by the native Supabase Storage publisher.
// Keep the bridge stable while releases are replaced atomically in the public bucket.
const PUBLISHED_INDEX =
  "https://vdceayvfgkwpgwixrhwi.supabase.co/storage/v1/object/public/everythinghub-public-site/index.html";

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
      "x-eh-bridge": "supabase-storage-publisher-v2",
      "x-eh-publish-target": "everythinghub-public-site",
    },
  });
});
