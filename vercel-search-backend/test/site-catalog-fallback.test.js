"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "../..");
const site = fs.readFileSync(path.join(root, "site/index.html"), "utf8");
const vercelHomepage = fs.readFileSync(path.join(root, "vercel-homepage/index.html"), "utf8");

test("primary site search renders catalogResults and falls back to the first-party Edge search", () => {
  const searchStart = site.indexOf("async function search(reset=true)");
  const searchEnd = site.indexOf("form.addEventListener", searchStart);
  assert.ok(searchStart >= 0 && searchEnd > searchStart);
  const search = site.slice(searchStart, searchEnd);
  assert.ok(site.includes("https://everythinghub-search-backend.vercel.app/api/search"));
  assert.ok(search.includes('new URL(BASE+"/everything-hub-web-search")'));
  assert.ok(search.includes("data.catalogResults"));
  assert.ok(search.includes("catalogItems.concat(pageItems)"));
  assert.ok(search.includes("catalogItems=offset===0&&Array.isArray(data.catalogResults)"));
  assert.ok(search.includes("apikey:KEY"));
});

test("Vercel homepage crawler fallback preserves first-party catalog preview cards", () => {
  const start = vercelHomepage.indexOf("async function searchCrawlerIndex(query)");
  const end = vercelHomepage.indexOf("async function searchPublicSources(query)", start);
  assert.ok(start >= 0 && end > start);
  const search = vercelHomepage.slice(start, end);
  assert.ok(search.includes("data.catalogResults"));
  assert.ok(search.includes("catalogResults.concat(pageResults)"));
  assert.ok(search.includes("catalogResults=(page===1?(d.catalogResults||[]):[])"));
  assert.ok(search.includes("preview_only:true"));
  assert.ok(search.includes("commercial_eligible:false"));
});
