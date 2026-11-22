"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const handler = require("../src/api/search-fast");
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

test("web fast-fallback does not call robots-disallowed external search providers", async () => {
  const originalFetch = global.fetch;
  let calls = 0;
  global.fetch = async () => { calls += 1; throw new Error("unexpected external request"); };
  try {
    const res = mockResponse();
    await handler({ method: "GET", query: { q: "NASA", lane: "web" } }, res);
    assert.equal(res.statusCode, 503);
    assert.equal(res.body.status, "unavailable");
    assert.equal(calls, 0);
  } finally {
    global.fetch = originalFetch;
  }
});

test("news fast-fallback fetches Google News only after robots allows the RSS path", async () => {
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
        return "<rss><channel><item><title>NASA Artemis update</title><link>https://www.nasa.gov/news/</link><description>NASA mission update</description><source>NASA</source><pubDate>Sun, 22 Nov 2026 12:00:00 GMT</pubDate></item></channel></rss>";
      }
    };
  };
  try {
    const res = mockResponse();
    await handler({ method: "GET", query: { q: "NASA", lane: "news" } }, res);
    assert.equal(res.statusCode, 200);
    assert.equal(res.body.providers[0], "Google News");
    assert.equal(res.body.results[0].title, "NASA Artemis update");
    assert.equal(calls.length, 2);
    assert.ok(calls[0].endsWith("/robots.txt"));
    assert.ok(calls[1].startsWith("https://news.google.com/rss/search"));
  } finally {
    global.fetch = originalFetch;
    resetRobotsForTests();
  }
});

test("news fast-fallback fails closed when robots disallows the RSS route", async () => {
  const originalFetch = global.fetch;
  const calls = [];
  global.fetch = async url => {
    const value = String(url);
    calls.push(value);
    return {
      status: 200,
      ok: true,
      url: value,
      async text() { return "User-agent: *\nDisallow: /rss/\n"; }
    };
  };
  try {
    const res = mockResponse();
    await handler({ method: "GET", query: { q: "NASA", lane: "news" } }, res);
    assert.equal(res.statusCode, 502);
    assert.equal(res.body.status, "unavailable");
    assert.equal(calls.length, 1);
  } finally {
    global.fetch = originalFetch;
    resetRobotsForTests();
  }
});
