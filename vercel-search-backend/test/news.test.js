"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const handler = require("../src/api/news");
const { resetRobotsForTests } = require("../src/lib/robots");

function mockResponse() {
  return {
    statusCode: 0,
    headers: {},
    body: null,
    setHeader(name, value) { this.headers[name] = value; },
    status(code) { this.statusCode = code; return this; },
    json(value) { this.body = value; return this; },
    end() { return this; }
  };
}

test.beforeEach(() => resetRobotsForTests());

test("news route returns current source headlines only after robots permits RSS", async () => {
  const originalFetch = global.fetch;
  const calls = [];
  global.fetch = async url => {
    const value = String(url);
    calls.push(value);
    if (value.endsWith("/robots.txt")) {
      return { status: 200, ok: true, url: value, async text() { return "User-agent: *\nAllow: /rss/search\n"; } };
    }
    return {
      status: 200,
      ok: true,
      url: value,
      async text() {
        return "<rss><channel><item><title>NASA science mission update</title><link>https://www.nasa.gov/news/</link><source>NASA</source><pubDate>Sun, 22 Nov 2026 12:00:00 GMT</pubDate><description>Latest NASA science update</description></item></channel></rss>";
      }
    };
  };
  try {
    const res = mockResponse();
    await handler({ method: "GET", query: { category: "science" } }, res);
    assert.equal(res.statusCode, 200);
    assert.equal(res.body.count, 1);
    assert.equal(res.body.items[0].source, "NASA");
    assert.equal(calls.length, 2);
    assert.ok(calls[0].endsWith("/robots.txt"));
    assert.ok(calls[1].startsWith("https://news.google.com/rss/search"));
  } finally {
    global.fetch = originalFetch;
    resetRobotsForTests();
  }
});

test("news route fails closed when robots disallows the RSS search path", async () => {
  const originalFetch = global.fetch;
  const calls = [];
  global.fetch = async url => {
    const value = String(url);
    calls.push(value);
    return { status: 200, ok: true, url: value, async text() { return "User-agent: *\nDisallow: /rss/\n"; } };
  };
  try {
    const res = mockResponse();
    await handler({ method: "GET", query: { category: "world" } }, res);
    assert.equal(res.statusCode, 502);
    assert.equal(res.body.status, "unavailable");
    assert.equal(calls.length, 1);
  } finally {
    global.fetch = originalFetch;
    resetRobotsForTests();
  }
});

test("news route fails closed when robots cannot be verified", async () => {
  const originalFetch = global.fetch;
  global.fetch = async url => ({
    status: 503,
    ok: false,
    url: String(url),
    async text() { return "unavailable"; }
  });
  try {
    const res = mockResponse();
    await handler({ method: "GET", query: { category: "technology" } }, res);
    assert.equal(res.statusCode, 502);
    assert.equal(res.body.status, "unavailable");
  } finally {
    global.fetch = originalFetch;
    resetRobotsForTests();
  }
});
