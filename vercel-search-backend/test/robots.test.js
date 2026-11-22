"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const {
  parseRobots,
  robotsFetch,
  resetRobotsForTests,
  MAX_REQUESTS_PER_HOST_PER_HOUR
} = require("../src/lib/robots");

function response(status, body, url) {
  return {
    status,
    ok: status >= 200 && status < 300,
    url,
    async text() { return body; }
  };
}

test.beforeEach(() => resetRobotsForTests());

test("robots parser applies longest match and Allow wins equal-specificity ties", () => {
  const policy = parseRobots([
    "User-agent: *",
    "Disallow: /search",
    "Allow: /search/public",
    "Disallow: /images/search?",
    "Allow: /images/search?format=rss",
    "Crawl-delay: 2"
  ].join("\n"));
  assert.equal(policy.allows("https://www.bing.com/search?format=rss&q=NASA"), false);
  assert.equal(policy.allows("https://www.bing.com/search/public?q=NASA"), true);
  assert.equal(policy.allows("https://www.bing.com/images/search?q=NASA"), false);
  assert.equal(policy.allows("https://www.bing.com/images/search?format=rss"), true);
  assert.equal(policy.delayMs, 2_000);
});

test("robotsFetch refuses Bing web search before making the disallowed request", async () => {
  const calls = [];
  const fetchImpl = async url => {
    calls.push(String(url));
    return response(200, "User-agent: *\nDisallow: /search\n", String(url));
  };
  await assert.rejects(
    robotsFetch("https://www.bing.com/search?format=rss&q=NASA", {}, {
      fetchImpl,
      sleep: async () => {}
    }),
    /robots_disallows_request/
  );
  assert.deepEqual(calls, ["https://www.bing.com/robots.txt"]);
});

test("robotsFetch permits a robots-approved path and rate-limits the host", async () => {
  const calls = [];
  const fetchImpl = async url => {
    calls.push(String(url));
    if (String(url).endsWith("/robots.txt")) {
      return response(200, "User-agent: *\nDisallow: /search\nAllow: /videos/search\nCrawl-delay: 0\n", String(url));
    }
    return response(200, "feed", String(url));
  };
  const result = await robotsFetch("https://www.bing.com/videos/search?q=NASA", {}, {
    fetchImpl,
    sleep: async () => {}
  });
  assert.equal(result.status, 200);
  assert.deepEqual(calls, [
    "https://www.bing.com/robots.txt",
    "https://www.bing.com/videos/search?q=NASA"
  ]);
  assert.equal(MAX_REQUESTS_PER_HOST_PER_HOUR, 90);
});

test("robotsFetch treats 404 and 410 robots files as no published restrictions", async () => {
  for (const status of [404, 410]) {
    resetRobotsForTests();
    const calls = [];
    const fetchImpl = async url => {
      calls.push(String(url));
      if (String(url).endsWith("/robots.txt")) return response(status, "", String(url));
      return response(200, "ok", String(url));
    };
    const result = await robotsFetch("https://example.org/feed.xml", {}, {
      fetchImpl,
      sleep: async () => {}
    });
    assert.equal(result.status, 200);
    assert.equal(calls.length, 2);
  }
});

test("robotsFetch fails closed when robots cannot be verified or redirects cross-host", async () => {
  const unavailable = async url => response(503, "", String(url));
  await assert.rejects(
    robotsFetch("https://example.net/feed.xml", {}, { fetchImpl: unavailable }),
    /robots_unverified/
  );

  resetRobotsForTests();
  const redirected = async url => response(200, "User-agent: *\nAllow: /\n", "https://other.example/robots.txt");
  await assert.rejects(
    robotsFetch("https://example.net/feed.xml", {}, { fetchImpl: redirected }),
    /robots_redirect_host_changed/
  );
});

test("robotsFetch rejects unsafe schemes and cross-host target redirects", async () => {
  await assert.rejects(robotsFetch("http://example.org/feed.xml"), /provider_url_unsafe/);
  const fetchImpl = async url => {
    if (String(url).endsWith("/robots.txt")) {
      return response(200, "User-agent: *\nAllow: /\n", String(url));
    }
    return response(200, "ok", "https://other.example/feed.xml");
  };
  await assert.rejects(
    robotsFetch("https://example.org/feed.xml", {}, { fetchImpl, sleep: async () => {} }),
    /provider_redirect_host_changed/
  );
});
