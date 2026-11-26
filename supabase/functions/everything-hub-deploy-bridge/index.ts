import "jsr:@supabase/functions-js/edge-runtime.d.ts";

const COMMIT = "d0669a202d6b7fe9626bebd4a5cd1d7cc4f50552";
const API = "https://api.github.com/repos/rshure001/EverythingHub-Crawler/contents/";
const HEADERS = {
  Accept: "application/vnd.github.raw",
  "X-GitHub-Api-Version": "2022-11-28",
  "User-Agent": "EverythingHub-Public-Bridge/1.0",
};

async function fetchPinnedFile(path: string): Promise<string> {
  const url = API + path + "?ref=" + COMMIT;
  const response = await fetch(url, { headers: HEADERS, signal: AbortSignal.timeout(18000) });
  if (!response.ok) throw new Error(path + " returned HTTP " + response.status);
  return await response.text();
}

Deno.serve(async (req: Request) => {
  if (req.method !== "GET" && req.method !== "HEAD") {
    return new Response("Method not allowed", { status: 405, headers: { Allow: "GET, HEAD" } });
  }

  try {
    const [htmlSource, searchSource] = await Promise.all([
      fetchPinnedFile("site/index.html"),
      fetchPinnedFile("site/search.js"),
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
      "<script>/* Everything Hub browser search client */\n" + safeScript + "\n</script>",
    );
    return new Response(req.method === "HEAD" ? null : html, {
      status: 200,
      headers: {
        "Content-Type": "text/html; charset=UTF-8",
        "Content-Disposition": "inline",
        "Cache-Control": "public, max-age=60, stale-while-revalidate=300",
        "X-Content-Type-Options": "nosniff",
        "X-EH-Render-Mode": "html-document",
        "X-EH-Upstream": "api.github.com",
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