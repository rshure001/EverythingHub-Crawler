import "jsr:@supabase/functions-js/edge-runtime.d.ts";

const TARGET = "https://vdceayvfgkwpgwixrhwi.supabase.co/functions/v1/everything-hub-deploy-bridge";

Deno.serve((req: Request) => {
  if (req.method !== "GET" && req.method !== "HEAD") {
    return new Response("Method not allowed", { status: 405, headers: { Allow: "GET, HEAD" } });
  }
  const incoming = new URL(req.url);
  const destination = new URL(TARGET);
  for (const [key, value] of incoming.searchParams.entries()) {
    destination.searchParams.set(key, value);
  }
  return new Response(null, {
    status: 302,
    headers: {
      Location: destination.toString(),
      "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
      "Referrer-Policy": "strict-origin-when-cross-origin",
      "X-EH-Legacy-Route": "redirect-to-publishing-bridge",
    },
  });
});