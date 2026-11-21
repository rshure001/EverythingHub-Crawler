import test from "node:test";
import assert from "node:assert/strict";
import { fetchSelfHostedPostgresSearch } from "./self-hosted-postgres-bridge.mjs";

function jsonResponse(payload, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => payload };
}

test("bridge is dormant unless a valid HTTPS endpoint and token are configured", async () => {
  assert.equal(await fetchSelfHostedPostgresSearch({ query: "NASA" }), null);
  assert.equal(await fetchSelfHostedPostgresSearch({
    endpoint: "http://public.example", token: "secret", query: "NASA",
  }), null);
  assert.equal(await fetchSelfHostedPostgresSearch({
    endpoint: "https://user:pass@example.com", token: "secret", query: "NASA",
  }), null);
});

test("authenticated request maps PostgreSQL page results to the website response contract", async () => {
  let requested;
  const result = await fetchSelfHostedPostgresSearch({
    endpoint: "https://search.example/",
    token: "private-token",
    query: "  NASA  ",
    lane: "science",
    limit: 10,
    offset: 20,
    fetchImpl: async (url, init) => {
      requested = { url: String(url), init };
      return jsonResponse({
        total: 51, hasMore: true,
        results: [{
          url: "https://www.nasa.gov/mars",
          host: "www.nasa.gov",
          title: "NASA Mars mission",
          description: "Explore Mars",
          tentacle: "science",
          score: 0.87,
        }],
      });
    },
  });
  assert.equal(requested.init.headers.Authorization, "Bearer private-token");
  assert.equal(requested.init.method, "GET");
  assert.equal(requested.url, "https://search.example/search?q=NASA&limit=10&offset=20&tentacle=science");
  assert.equal(result.count, 51);
  assert.equal(result.results[0].origin, "Everything Hub independent PostgreSQL");
  assert.equal(result.results[0].url, "https://www.nasa.gov/mars");
  assert.equal(result.results[0].relevance, 0.87);
  assert.equal(result.hasMore, true);
  assert.equal(result.independentOfSupabase, true);
});

test("bridge preserves valid empty results as a real response", async () => {
  const result = await fetchSelfHostedPostgresSearch({
    endpoint: "https://search.example",
    token: "secret",
    query: "No matching term",
    fetchImpl: async () => jsonResponse({ total: 0, results: [], hasMore: false }),
  });
  assert.equal(result.count, 0);
  assert.deepEqual(result.results, []);
});

test("bridge rejects unsafe result URLs and caps results", async () => {
  const result = await fetchSelfHostedPostgresSearch({
    endpoint: "https://search.example",
    token: "secret",
    query: "Rolex",
    limit: 99,
    fetchImpl: async (url) => {
      assert.equal(new URL(url).searchParams.get("limit"), "20");
      return jsonResponse({
        total: 3,
        results: [
          { url: "javascript:alert(1)", title: "bad" },
          { url: "https://user:pass@example.com/private", title: "bad" },
          { url: "https://www.rolex.com/watches", title: "Rolex watches" },
        ],
      });
    },
  });
  assert.equal(result.results.length, 1);
  assert.equal(result.results[0].url, "https://www.rolex.com/watches");
});

test("offline or unauthorized bridge falls through without throwing", async () => {
  const offline = await fetchSelfHostedPostgresSearch({
    endpoint: "https://search.example",
    token: "secret",
    query: "Rolex",
    fetchImpl: async () => { throw new Error("connection refused"); },
  });
  const unauthorized = await fetchSelfHostedPostgresSearch({
    endpoint: "https://search.example",
    token: "secret",
    query: "Rolex",
    fetchImpl: async () => jsonResponse({ error: "unauthorized" }, 401),
  });
  assert.equal(offline, null);
  assert.equal(unauthorized, null);
});
