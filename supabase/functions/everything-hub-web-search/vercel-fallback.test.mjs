import test from "node:test";
import assert from "node:assert/strict";
import { buildVercelSearchUrl, fetchVercelCatalogFallback } from "./vercel-fallback.mjs";

const digest = "a".repeat(64);
const validPayload = () => ({
  query: "NASA",
  count: 2146,
  totalMatches: 2146,
  page: 1,
  pageSize: 10,
  offset: 0,
  limit: 10,
  hasMore: true,
  indexedPages: 15224,
  indexStatus: "crawler-catalog-active",
  indexUpdatedAt: 1795093455,
  indexSha256: digest,
  providers: ["Everything Hub Crawler", "Bing Web Search"],
  results: [
    { title: "NASA", url: "https://www.nasa.gov/", provider: "everythinghub-catalog" },
    { title: "Unsafe", url: "ftp://example.com/file", provider: "unknown" },
  ],
});

test("builds a bounded Vercel search URL with the requested lane and pagination", () => {
  const url = buildVercelSearchUrl({ query: " NASA ", lane: "science", limit: 10, offset: 20 });
  assert.equal(url.origin, "https://everythinghub-search-backend.vercel.app");
  assert.equal(url.searchParams.get("q"), "NASA");
  assert.equal(url.searchParams.get("tentacle"), "science");
  assert.equal(url.searchParams.get("limit"), "10");
  assert.equal(url.searchParams.get("offset"), "20");
  assert.throws(() => buildVercelSearchUrl({ query: "" }), /query is required/);
  assert.throws(() => buildVercelSearchUrl({ query: "NASA", limit: 21 }), /limit/);
  assert.throws(() => buildVercelSearchUrl({ query: "NASA", offset: 5001 }), /offset/);
});

test("returns a SHA-verified Vercel catalog fallback and filters unsafe result URLs", async () => {
  let requestedUrl;
  let requestInit;
  const payload = validPayload();
  const result = await fetchVercelCatalogFallback({
    query: "NASA",
    lane: "science",
    limit: 10,
    offset: 0,
    fetchImpl: async (url, init) => {
      requestedUrl = new URL(url);
      requestInit = init;
      return { ok: true, json: async () => payload };
    },
  });

  assert.equal(requestedUrl.searchParams.get("tentacle"), "science");
  assert.equal(requestInit.headers.Accept, "application/json");
  assert.equal(result.indexStatus, "crawler-catalog-active");
  assert.equal(result.indexedPages, 15224);
  assert.equal(result.indexIntegrity, "sha256");
  assert.equal(result.indexTransport, "vercel");
  assert.equal(result.fallback, true);
  assert.equal(result.fallbackReason, "database_unavailable");
  assert.equal(result.independentOfDatabase, true);
  assert.equal(result.results.length, 1);
  assert.equal(result.results[0].url, "https://www.nasa.gov/");
});

test("uses an explicit fallback reason and permits zero crawler matches when Bing results exist", async () => {
  const payload = validPayload();
  payload.count = 0;
  const result = await fetchVercelCatalogFallback({
    query: "Rolex",
    fallbackReason: "database_empty",
    fetchImpl: async () => ({ ok: true, json: async () => payload }),
  });
  assert.equal(result.count, 0);
  assert.equal(result.fallbackReason, "database_empty");
  assert.equal(result.results.length, 1);
});

test("returns null when the catalog metadata or safe result set is invalid", async () => {
  const invalidDigest = validPayload();
  invalidDigest.indexSha256 = "not-a-digest";
  assert.equal(await fetchVercelCatalogFallback({
    query: "NASA",
    fetchImpl: async () => ({ ok: true, json: async () => invalidDigest }),
  }), null);

  const invalidStatus = validPayload();
  invalidStatus.indexStatus = "catalog-unavailable";
  assert.equal(await fetchVercelCatalogFallback({
    query: "NASA",
    fetchImpl: async () => ({ ok: true, json: async () => invalidStatus }),
  }), null);

  const unsafeOnly = validPayload();
  unsafeOnly.results = [{ title: "Unsafe", url: "ftp://example.com/file" }];
  assert.equal(await fetchVercelCatalogFallback({
    query: "NASA",
    fetchImpl: async () => ({ ok: true, json: async () => unsafeOnly }),
  }), null);
});

test("fails closed on non-success Vercel responses", async () => {
  await assert.rejects(
    fetchVercelCatalogFallback({
      query: "NASA",
      fetchImpl: async () => ({ ok: false, status: 503 }),
    }),
    /HTTP 503/,
  );
});
