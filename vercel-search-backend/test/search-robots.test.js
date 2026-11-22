"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const handler = require("../src/api/search");
const { resetRobotsForTests } = require("../src/lib/robots");

test.beforeEach(() => resetRobotsForTests());

test("Bing RSS fallback is blocked because robots disallows /search", async () => {
  const calls = [];
  const originalFetch = global.fetch;
  global.fetch = async url => {
    calls.push(String(url));
    return {
      status: 200,
      ok: true,
      url: String(url),
      async text() { return "User-agent: *\nDisallow: /search\n"; }
    };
  };
  try {
    await assert.rejects(handler._test.loadBing("NASA"), /robots_disallows_request/);
    assert.deepEqual(calls, ["https://www.bing.com/robots.txt"]);
  } finally {
    global.fetch = originalFetch;
    resetRobotsForTests();
  }
});
