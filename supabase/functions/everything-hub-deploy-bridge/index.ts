import "jsr:@supabase/functions-js/edge-runtime.d.ts";

const COMMIT = "d0669a202d6b7fe9626bebd4a5cd1d7cc4f50552";
const BASE = "https://raw.githubusercontent.com/rshure001/EverythingHub-Crawler/" + COMMIT + "/site/";

async function fetchPinnedFile(name: string): Promise<string> {
  const response = await fetch(BASE + name, {
    headers: { Accept: name.endsWith(".html") ? "text/html" : "application/javascript" },
    signal: AbortSignal.timeout(18000),
  });
  if (!response.ok) throw new Error(name + " returned HTTP " + response.status);
  return await response.text();
}

Deno.serve(async (req: Request) => {
  if (req.method !== "GET" && req.method !== "HEAD") {
    return new Response("Method not allowed", { status: 405, headers: { Allow: "GET, HEAD" } });
  }

  try {
    const [htmlSource, searchSource] = await Promise.all([
      fetchPinnedFile("index.html"),
      fetchPinnedFile("search.js"),
    ]);
    const normalized = htmlSource.trimStart().toLowerCase();
    if ((!normalized.startsWith("<!doctype html") && !normalized.startsWith("<!--")) ||
        !htmlSource.includes("Everything Hub") ||
        !htmlSource.includes('id="q"') ||
        !htmlSource.includes('id="search"')) {
      throw new Error("Pinned homepage failed document/interface validation");
    }
    if (searchSource.length < 1000 || !searchSource.includes("EverythingHub")) {
      throw new Error("Pinned search client failed validation");
    }

    const safeScript = searchSource.replace(/<\/script/gi, "<\\/script");
    const html = htmlSource.replace(
      '<script src="search.js"></script>',
      "<script>\n" + safeScript + "\n</script>",
    );
    return new Response(req.method === "HEAD" ? null : html, {
      status: 200,
      headers: {
        "Content-Type": "text/html; charset=UTF-8",
        "Cache-Control": "public, max-age=60, stale-while-revalidate=300",
        "X-Content-Type-Options": "nosniff",
        "X-EH-Render-Mode": "html-document",
        "X-EH-Upstream": "raw.githubusercontent.com",
        "X-EH-Commit": COMMIT,
      },
    });
  } catch (error) {
    const detail = error instanceof Error ? error.message : "fetch-error";
    return new Response("Homepage source unavailable: " + detail, {
      status: 502,
      headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" },
    });
  }
});