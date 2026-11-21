const MAX_LIMIT = 20;
const MAX_OFFSET = 5000;

function safeSearchEndpoint(raw) {
  if (!raw || typeof raw !== "string") return null;
  try {
    const base = new URL(raw);
    if (base.protocol !== "https:") return null;
    if (base.username || base.password || base.search || base.hash) return null;
    base.pathname = base.pathname.replace(/\/$/, "") + "/search";
    return base;
  } catch {
    return null;
  }
}

function safeResultUrl(raw) {
  try {
    const url = new URL(String(raw ?? ""));
    if ((url.protocol !== "https:" && url.protocol !== "http:") || url.username || url.password) return null;
    return url.href;
  } catch {
    return null;
  }
}

/**
 * Optional bridge to the independent, self-hosted PostgreSQL search API.
 * It is dormant unless both environment values are configured. A failed
 * bridge returns null so the existing Supabase/static/Bing search paths run.
 *
 * @param {{
 *   endpoint?: string,
 *   token?: string,
 *   query?: string,
 *   lane?: string | null,
 *   limit?: number,
 *   offset?: number,
 *   fetchImpl?: typeof fetch,
 *   timeoutMs?: number
 * }} [options]
 */
export async function fetchSelfHostedPostgresSearch(options = {}) {
  const {
    endpoint,
    token,
    query,
    lane = null,
    limit = 10,
    offset = 0,
    fetchImpl = fetch,
    timeoutMs = 3500,
  } = options;
  const target = safeSearchEndpoint(endpoint);
  if (!target || typeof token !== "string" || !token.trim()) return null;
  const normalizedQuery = String(query ?? "").trim().slice(0, 160);
  if (normalizedQuery.length < 2) return null;

  const safeLimit = Number.isInteger(limit) ? Math.max(1, Math.min(MAX_LIMIT, limit)) : 10;
  const safeOffset = Number.isInteger(offset) ? Math.max(0, Math.min(MAX_OFFSET, offset)) : 0;
  target.searchParams.set("q", normalizedQuery);
  target.searchParams.set("limit", String(safeLimit));
  target.searchParams.set("offset", String(safeOffset));
  if (typeof lane === "string" && lane) target.searchParams.set("tentacle", lane);

  try {
    const response = await fetchImpl(target, {
      method: "GET",
      headers: {
        Accept: "application/json",
        Authorization: "Bearer " + token,
      },
      cache: "no-store",
      signal: AbortSignal.timeout(timeoutMs),
    });
    if (!response.ok) return null;
    const payload = await response.json();
    if (!payload || !Array.isArray(payload.results)) return null;

    const results = payload.results.slice(0, safeLimit).flatMap((item) => {
      const resultUrl = safeResultUrl(item?.url);
      if (!resultUrl) return [];
      const title = String(item?.title || resultUrl).slice(0, 500);
      const snippet = String(item?.description || item?.snippet || "")
        .replace(/\s+/g, " ").slice(0, 320);
      const source = String(item?.host || (() => {
        try { return new URL(resultUrl).hostname; } catch { return "Everything Hub"; }
      })()).slice(0, 253);
      const relevance = Number(item?.score ?? 0);
      return [{
        title,
        url: resultUrl,
        snippet,
        source,
        origin: "Everything Hub independent PostgreSQL",
        tentacle: String(item?.tentacle || lane || "general"),
        relevance: Number.isFinite(relevance) ? relevance : 0,
      }];
    });

    const rawTotal = Number(payload.total ?? payload.count ?? results.length);
    const count = Number.isSafeInteger(rawTotal) && rawTotal >= 0 ? rawTotal : results.length;
    return {
      results,
      count,
      query: normalizedQuery,
      tentacle: lane,
      requestedTentacle: lane,
      laneFallback: false,
      source: "Everything Hub independent PostgreSQL 17",
      offset: safeOffset,
      limit: safeLimit,
      hasMore: typeof payload.hasMore === "boolean"
        ? payload.hasMore
        : safeOffset + results.length < count,
      independentOfSupabase: true,
      bridge: "self-hosted-postgresql",
    };
  } catch {
    // Do not break existing search if the self-hosted host is offline or unreachable.
    return null;
  }
}
