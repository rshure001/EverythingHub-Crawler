"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const handler = require("../src/api/news");
const { resetRobotsForTests } = require("../src/lib/robots");
const { NEWS_FEEDS, parseFeed, mergeFeeds } = handler._test;

function mockResponse(status, body, url) {
  return {
    status,
    ok: status >= 200 && status < 300,
    url,
    async text() { return body; }
  };
}

function mockRes() {
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

function feedXml(host) {
  return "<rss><channel><item><title>Update from " + host + "</title><link>https://" + host + "/news/latest</link><pubDate>Sun, 22 Nov 2026 12:00:00 GMT</pubDate><description><![CDATA[<p>Current public update from " + host + ".</p>]]></description></item></channel></rss>";
}

test.beforeEach(() => resetRobotsForTests());
test.afterEach(() => {
  resetRobotsForTests();
});

test("news route aggregates only robots-permitted public RSS feeds with attribution", async () => {
  const originalFetch = global.fetch;
  const calls = [];
  global.fetch = async url => {
    const value = String(url);
    calls.push(value);
    if (value.endsWith("/robots.txt")) {
      return mockResponse(200, "User-agent: *\nAllow: /\n", value);
    }
    const host = new URL(value).hostname;
    return mockResponse(200, feedXml(host), value);
  };
  try {
    const feeds = NEWS_FEEDS["for-you"];
    const res = mockRes();
    await handler({ method: "GET", query: { category: "for-you" } }, res);
    assert.equal(res.statusCode, 200);
    assert.equal(res.body.status, "ok");
    assert.equal(res.body.category, "for-you");
    assert.equal(res.body.count, feeds.length);
    assert.equal(res.body.items.length, feeds.length);
    assert.equal(res.body.sources.length, feeds.length);
    assert.ok(res.body.items.every(item => item.url.startsWith("https://") && item.source && item.publishedAt));
    assert.equal(calls.filter(url => url.endsWith("/robots.txt")).length, feeds.length);
    assert.equal(calls.filter(url => !url.endsWith("/robots.txt")).length, feeds.length);
    assert.ok(Object.values(res.headers).some(value => String(value).includes("s-maxage=600")));
  } finally {
    global.fetch = originalFetch;
  }
});

test("news route skips a robots-disallowed source and retains permitted feeds", async () => {
  const originalFetch = global.fetch;
  const calls = [];
  const blocked = NEWS_FEEDS.world[0];
  const blockedHost = new URL(blocked.url).hostname;
  global.fetch = async url => {
    const value = String(url);
    calls.push(value);
    if (value.endsWith("/robots.txt")) {
      const host = new URL(value).hostname;
      const rules = host === blockedHost ? "User-agent: *\nDisallow: /\n" : "User-agent: *\nAllow: /\n";
      return mockResponse(200, rules, value);
    }
    const host = new URL(value).hostname;
    return mockResponse(200, feedXml(host), value);
  };
  try {
    const res = mockRes();
    await handler({ method: "GET", query: { category: "world" } }, res);
    assert.equal(res.statusCode, 200);
    assert.ok(res.body.count >= 1);
    assert.ok(res.body.sourcesUnavailable.includes(blocked.source));
    assert.ok(!calls.includes(blocked.url));
    assert.ok(res.body.items.every(item => item.url.startsWith("https://")));
  } finally {
    global.fetch = originalFetch;
  }
});

test("news route fails closed when every configured feed is disallowed", async () => {
  const originalFetch = global.fetch;
  const calls = [];
  global.fetch = async url => {
    const value = String(url);
    calls.push(value);
    return mockResponse(200, "User-agent: *\nDisallow: /\n", value);
  };
  try {
    const feeds = NEWS_FEEDS.technology;
    const res = mockRes();
    await handler({ method: "GET", query: { category: "technology" } }, res);
    assert.equal(res.statusCode, 502);
    assert.equal(res.body.status, "unavailable");
    assert.equal(res.body.sourcesUnavailable.length, feeds.length);
    assert.equal(calls.length, feeds.length);
    assert.ok(calls.every(url => url.endsWith("/robots.txt")));
  } finally {
    global.fetch = originalFetch;
  }
});

test("news route fails closed when robots policy cannot be verified", async () => {
  const originalFetch = global.fetch;
  global.fetch = async url => mockResponse(503, "unavailable", String(url));
  try {
    const res = mockRes();
    await handler({ method: "GET", query: { category: "science" } }, res);
    assert.equal(res.statusCode, 502);
    assert.equal(res.body.status, "unavailable");
    assert.equal(res.body.sourcesUnavailable.length, NEWS_FEEDS.science.length);
  } finally {
    global.fetch = originalFetch;
  }
});

test("news route rejects unsupported categories and methods", async () => {
  const res = mockRes();
  await handler({ method: "GET", query: { category: "politics" } }, res);
  assert.equal(res.statusCode, 400);
  const methodRes = mockRes();
  await handler({ method: "POST", query: { category: "world" } }, methodRes);
  assert.equal(methodRes.statusCode, 405);
});

test("RSS parser handles CDATA, entities, publication dates, and rejects insecure links", () => {
  const feed = { source: "NASA", url: "https://www.nasa.gov/news/feed.xml" };
  const xml = "<rss><channel>" +
    "<item><title><![CDATA[NASA &amp; Science]]></title><link>https://www.nasa.gov/news/one</link>" +
    "<pubDate>Sun, 22 Nov 2026 12:00:00 GMT</pubDate><description><![CDATA[<p>Latest science update</p>]]></description></item>" +
    "<item><title>Insecure</title><link>http://example.com/story</link></item>" +
    "</channel></rss>";
  const items = parseFeed(xml, feed);
  assert.equal(items.length, 1);
  assert.equal(items[0].title, "NASA & Science");
  assert.equal(items[0].source, "NASA");
  assert.equal(items[0].summary, "Latest science update");
  assert.equal(items[0].publishedAt, "2026-11-22T12:00:00.000Z");
});

test("feed merger deduplicates URLs and sorts newest first", () => {
  const older = { title: "Older", url: "https://example.gov/older", source: "A", publishedAt: "2026-11-21T12:00:00.000Z" };
  const newer = { title: "Newer", url: "https://example.gov/newer", source: "B", publishedAt: "2026-11-22T12:00:00.000Z" };
  assert.deepEqual(mergeFeeds([{ items: [older, newer] }, { items: [older] }]), [newer, older]);
});
