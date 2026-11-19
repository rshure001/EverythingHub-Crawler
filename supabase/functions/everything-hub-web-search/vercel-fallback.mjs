const VERCEL_SEARCH_ENDPOINT = "https://everythinghub-search-backend.vercel.app/api/search";
const ALLOWED_LANES = new Set([
  "general", "science", "news", "technology",
  "business", "government", "culture", "nature",
]);

/**
 * @param {{ query: string, lane?: string | null, limit?: number, offset?: number }} options
 * @returns {URL}
 */
export function buildVercelSearchUrl({ query, lane = null, limit = 10, offset = 0 } = {}) {
  const normalizedQuery = String(query ?? "").trim().slice(0, 160);
  if (!normalizedQuery) throw new TypeError("query is required");
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > 20) {
    throw new RangeError("limit is outside the Edge Function bounds");
  }
  if (!Number.isSafeInteger(offset) || offset < 0 || offset > 5000) {
    throw new RangeError("offset is outside the Edge Function bounds");
  }

  const url = new URL(VERCEL_SEARCH_ENDPOINT);
  url.searchParams.set("q", normalizedQuery);
  url.searchParams.set("limit", String(limit));
  url.searchParams.set("offset", String(offset));
  const normalizedLane = String(lane ?? "").trim().toLowerCase();
  if (ALLOWED_LANES.has(normalizedLane)) {
    url.searchParams.set("tentacle", normalizedLane);
  }
  return url;
}

/**
 * @param {{
 *   query: string,
 *   lane?: string | null,
 *   limit?: number,
 *   offset?: number,
 *   fallbackReason?: string,
 *   fetchImpl?: typeof fetch,
 *   timeoutMs?: number
 * }} options
 * @returns {Promise<Record<string, any> | null>}
 */
export async function fetchVercelCatalogFallback({
  query,
  lane = null,
  limit = 10,
  offset = 0,
  fallbackReason = "database_unavailable",
  fetchImpl = fetch,
  timeoutMs = 8500,
} = {}) {
  if (typeof fetchImpl !== "function") throw new TypeError("fetchImpl must be a function");
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1) {
    throw new RangeError("timeoutMs must be a positive safe integer");
  }

  const url = buildVercelSearchUrl({ query, lane, limit, offset });
  const response = await fetchImpl(url, {
    headers: { Accept: "application/json" },
    signal: AbortSignal.timeout(timeoutMs),
  });
  if (!response.ok) {
    throw new Error("Vercel search returned HTTP " + response.status);
  }

  const payload = await response.json();
  const digest = String(payload?.indexSha256 ?? "");
  if (
    payload?.indexStatus !== "crawler-catalog-active" ||
    !Number.isSafeInteger(payload?.indexedPages) ||
    payload.indexedPages < 1 ||
    !Number.isSafeInteger(payload?.indexUpdatedAt) ||
    payload.indexUpdatedAt < 1 ||
    !/^[a-f0-9]{64}$/i.test(digest) ||
    !Array.isArray(payload?.results)
  ) {
    return null;
  }

  const results = payload.results.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const rawUrl = String(item.url ?? "").trim();
    const title = String(item.title ?? rawUrl).trim();
    if (!title) return [];
    try {
      const parsed = new URL(rawUrl);
      if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return [];
      return [{
        ...item,
        title: title.slice(0, 500),
        url: parsed.href,
        snippet: String(item.snippet ?? item.description ?? "").replace(/\s+/g, " ").slice(0, 320),
      }];
    } catch {
      return [];
    }
  });
  if (!results.length) return null;

  const count = Number(payload.count ?? payload.totalMatches ?? results.length);
  if (!Number.isSafeInteger(count) || count < 0) return null;

  return {
    ...payload,
    results,
    count,
    query: String(payload.query ?? query),
    offset: Number.isSafeInteger(payload.offset) ? payload.offset : offset,
    limit: Number.isSafeInteger(payload.limit) ? payload.limit : limit,
    source: "Everything Hub Vercel public catalog",
    provider: "Everything Hub Vercel Search",
    providers: Array.isArray(payload.providers) ? payload.providers : ["Everything Hub Crawler"],
    indexIntegrity: "sha256",
    indexTransport: "vercel",
    fallback: true,
    fallbackReason,
    independentOfDatabase: true,
    crawlerIndexCount: count,
  };
}
