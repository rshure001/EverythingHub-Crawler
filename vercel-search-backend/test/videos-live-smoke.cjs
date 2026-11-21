"use strict";

const assert = require("node:assert/strict");
const { fetchCommons } = require("../src/api/videos")._test;

(async () => {
  const items = await fetchCommons("NASA");
  assert.ok(items.length >= 15, "Expected at least 15 robots-approved, openly licensed NASA video records; got " + items.length);
  for (const item of items) {
    assert.ok(item.title, "Video title missing");
    assert.ok(item.url.startsWith("https://commons.wikimedia.org/wiki/"), "Video link is not a Commons file page");
    assert.ok(item.thumbnail.startsWith("https://upload.wikimedia.org/"), "Thumbnail is not an approved Commons asset host");
    assert.ok(/^(CC0|Public domain|CC BY)/i.test(item.license), "License is outside the allowed open-license set");
    assert.ok(item.licenseUrl.startsWith("https://"), "License URL must be HTTPS");
    if (/^CC BY/i.test(item.license)) assert.ok(item.creator || item.credit, "CC BY video requires creator/credit attribution");
  }
  console.log(JSON.stringify({
    provider: "Wikimedia Commons",
    query: "NASA",
    robotsCheckedHost: "commons.wikimedia.org",
    returnedVideos: items.length,
    sampleTitles: items.slice(0, 5).map(item => item.title),
    sampleLicenses: items.slice(0, 5).map(item => item.license),
    sampleCredits: items.slice(0, 5).map(item => item.creator || item.credit || "Attribution not required")
  }, null, 2));
})().catch(error => {
  console.error("Wikimedia video live smoke failed:", error && error.message ? error.message : String(error));
  process.exitCode = 1;
});
