"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const handler = require("../src/api/videos");
const { parseRobots, normalizeCommonsItem, isAllowedCommonsLicense, resetForTests } = handler._test;

function response(status, body, url) {
  return {
    status,
    ok: status >= 200 && status < 300,
    url,
    headers: { get() { return null; } },
    async text() { return body; }
  };
}

function commonsPage(index, overrides = {}) {
  const title = "File:NASA mission video " + index + ".webm";
  return {
    pageid: index + 1000,
    title,
    imageinfo: [{
      mime: "video/webm",
      thumburl: "https://upload.wikimedia.org/wikipedia/commons/thumb/" + index + "/NASA_video_" + index + ".jpg/640px-NASA_video_" + index + ".jpg",
      extmetadata: {
        LicenseShortName: { value: "CC BY 4.0" },
        LicenseUrl: { value: "https://creativecommons.org/licenses/by/4.0/" },
        Artist: { value: "NASA Video Team" },
        Credit: { value: "NASA" },
        AttributionRequired: { value: "true" },
        ...overrides
      }
    }]
  };
}

function mockResponse() {
  return {
    statusCode: 0,
    headers: {},
    body: null,
    setHeader(name, value) { this.headers[name] = value; return this; },
    status(code) { this.statusCode = code; return this; },
    json(value) { this.body = value; return this; },
    end() { return this; }
  };
}

test("robots parser applies longest matching rule and crawl delay", () => {
  resetForTests();
  const policy = parseRobots([
    "User-agent: *",
    "Disallow: /private",
    "Allow: /private/public",
    "Crawl-delay: 2"
  ].join("\n"), "EverythingHubVideoSearch/1.0");
  assert.equal(policy.allows("https://commons.wikimedia.org/private/hidden"), false);
  assert.equal(policy.allows("https://commons.wikimedia.org/private/public/item"), true);
  assert.equal(policy.allows("https://commons.wikimedia.org/w/api.php?action=query"), true);
  assert.equal(policy.delayMs, 2000);
});

test("robots parser prefers a specific user-agent group over wildcard", () => {
  resetForTests();
  const policy = parseRobots([
    "User-agent: EverythingHubVideoSearch",
    "Disallow: /w/api.php",
    "User-agent: *",
    "Allow: /"
  ].join("\n"), "EverythingHubVideoSearch/1.0");
  assert.equal(policy.allows("https://commons.wikimedia.org/w/api.php?action=query"), false);
});

test("Commons normalizer accepts only video files with allowed licenses and credit", () => {
  const valid = normalizeCommonsItem(commonsPage(1), "NASA");
  assert.ok(valid);
  assert.equal(valid.source, "Wikimedia Commons");
  assert.equal(valid.title, "NASA mission video 1.webm");
  assert.match(valid.url, /^https:\/\/commons\.wikimedia\.org\/wiki\/File/);
  assert.match(valid.thumbnail, /^https:\/\/upload\.wikimedia\.org\//);
  assert.equal(valid.license, "CC BY 4.0");
  assert.equal(valid.creator, "NASA Video Team");

  const nonVideo = commonsPage(2);
  nonVideo.imageinfo[0].mime = "image/jpeg";
  assert.equal(normalizeCommonsItem(nonVideo, "NASA"), null);

  const noCreator = commonsPage(3, { Artist: { value: "" }, Credit: { value: "" } });
  assert.equal(normalizeCommonsItem(noCreator, "NASA"), null);

  const nonCommercial = commonsPage(4, {
    LicenseShortName: { value: "CC BY-NC 4.0" },
    LicenseUrl: { value: "https://creativecommons.org/licenses/by-nc/4.0/" }
  });
  assert.equal(normalizeCommonsItem(nonCommercial, "NASA"), null);
});

test("license policy allows CC BY, CC BY-SA, CC0 and explicit public domain only", () => {
  assert.equal(isAllowedCommonsLicense("CC BY 4.0", "https://creativecommons.org/licenses/by/4.0/", true, "Creator"), true);
  assert.equal(isAllowedCommonsLicense("CC BY-SA 3.0", "https://creativecommons.org/licenses/by-sa/3.0/", true, "Creator"), true);
  assert.equal(isAllowedCommonsLicense("CC0 1.0", "", false, ""), true);
  assert.equal(isAllowedCommonsLicense("Public domain", "", false, ""), true);
  assert.equal(isAllowedCommonsLicense("CC BY-NC 4.0", "https://creativecommons.org/licenses/by-nc/4.0/", true, "Creator"), false);
  assert.equal(isAllowedCommonsLicense("CC BY-ND 4.0", "https://creativecommons.org/licenses/by-nd/4.0/", true, "Creator"), false);
  assert.equal(isAllowedCommonsLicense("CC BY 4.0", "https://example.com/license", true, "Creator"), false);
  assert.equal(isAllowedCommonsLicense("CC BY 4.0", "https://creativecommons.org/licenses/by/4.0/", true, ""), false);
  assert.equal(isAllowedCommonsLicense("Public domain", "", null, ""), false);
});

test("video handler returns deduplicated Commons metadata after robots checks", async () => {
  resetForTests();
  const originalFetch = global.fetch;
  const calls = [];
  global.fetch = async (url) => {
    const parsed = new URL(url);
    calls.push(parsed.href);
    if (parsed.hostname === "commons.wikimedia.org" && parsed.pathname === "/robots.txt") {
      return response(200, "User-agent: *\nAllow: /w/api.php\n", parsed.href);
    }
    if (parsed.hostname === "commons.wikimedia.org" && parsed.pathname === "/w/api.php") {
      const pages = {};
      for (let index = 0; index < 20; index += 1) pages[String(index + 1)] = commonsPage(index);
      pages.duplicate = commonsPage(0);
      return response(200, JSON.stringify({ query: { pages } }), parsed.href);
    }
    throw new Error("Unexpected external request: " + parsed.href);
  };

  try {
    const res = mockResponse();
    await handler({ method: "GET", query: { q: "NASA" } }, res);
    assert.equal(res.statusCode, 200);
    assert.equal(res.body.provider, "Wikimedia Commons");
    assert.equal(res.body.count, 20);
    assert.equal(res.body.items.length, 20);
    assert.ok(res.body.items.every(item => item.title && item.license && item.creator));
    assert.ok(res.body.items.every(item => item.url.startsWith("https://commons.wikimedia.org/wiki/")));
    assert.ok(res.body.items.every(item => item.thumbnail.startsWith("https://upload.wikimedia.org/")));
    assert.ok(calls.every(url => new URL(url).hostname === "commons.wikimedia.org"));
  } finally {
    global.fetch = originalFetch;
    resetForTests();
  }
});

test("robots denial blocks the Commons API request and fails closed", async () => {
  resetForTests();
  const originalFetch = global.fetch;
  const calls = [];
  global.fetch = async (url) => {
    calls.push(String(url));
    return response(200, "User-agent: *\nDisallow: /w/api.php\n", String(url));
  };
  try {
    const res = mockResponse();
    await handler({ method: "GET", query: { q: "NASA" } }, res);
    assert.equal(res.statusCode, 503);
    assert.equal(res.body.status, "unavailable");
    assert.equal(calls.length, 1);
  } finally {
    global.fetch = originalFetch;
    resetForTests();
  }
});
