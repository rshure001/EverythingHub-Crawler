"use strict";

const assert = require("node:assert/strict");
const { fetchNASA } = require("../src/api/videos")._test;

(async () => {
  const items = await fetchNASA("NASA");
  assert.ok(items.length >= 15, "Expected at least 15 robots-approved NASA video records; got " + items.length);
  for (const item of items) {
    assert.ok(item.title, "Video title missing");
    assert.ok(item.credit, "Attribution credit missing");
    assert.ok(item.url.startsWith("https://images.nasa.gov/details/"), "Video link is not an official NASA detail page");
    assert.ok(item.thumbnail.startsWith("https://images-assets.nasa.gov/"), "Thumbnail is not an approved NASA asset host");
    assert.ok(item.attributionUrl.includes("nasa.gov/nasa-brand-center/images-and-media/"), "NASA media-guideline attribution link missing");
  }
  console.log(JSON.stringify({
    provider: "NASA Image and Video Library",
    query: "NASA",
    robotsCheckedHosts: ["images-api.nasa.gov", "images-assets.nasa.gov"],
    returnedVideos: items.length,
    sampleTitles: items.slice(0, 5).map(item => item.title),
    sampleCredits: items.slice(0, 5).map(item => item.credit)
  }, null, 2));
})().catch(error => {
  console.error("NASA video live smoke failed:", error && error.message ? error.message : String(error));
  process.exitCode = 1;
});
