const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const html = fs.readFileSync(path.join(__dirname, "fallback", "index.html"), "utf8");

test("preserves the exact approved octopus artwork resource", () => {
  assert.ok(html.includes('src="https://everythinghub-search-engine-4x25f8.v2.appdeploy.ai/resources/everythinghub-approved-octopus.png"'));
});

test("keeps the eight required search categories", () => {
  const modes = [...html.matchAll(/data-mode="(web|news|images|videos|shopping|places|business|research)"/g)].map(match => match[1]);
  assert.deepEqual([...new Set(modes)].sort(), ["business", "images", "news", "places", "research", "shopping", "videos", "web"]);
  assert.match(html, /id="query"/);
  assert.match(html, /id="searchForm"/);
});

test("filters embedded Commons images through verified license and attribution rules", () => {
  assert.match(html, /function mediaPreviewMetadata\(meta\)/);
  assert.match(html, /const licenseValid=publicDomain\|\|cc0\|\|\(ccByMatch!==null&&ccByLicenseUrlValid\)/);
  assert.match(html, /const attributionValid=ccByMatch\?creator.length>0/);
  assert.match(html, /filter\(entry=>entry.preview.allowed\)/);
  assert.match(html, /preview.creator/);
  assert.match(html, /preview.licenseUrl/);
  assert.match(html, /licensed public images from Wikimedia Commons/);
  assert.doesNotMatch(html, /\\\\(?:d|s|\\.|\\\/)/);
});

test("keeps live public news tabs and Refresh", () => {
  for (const category of ["for-you", "world", "technology", "business", "science"]) {
    assert.ok(html.includes('data-news="' + category + '"'));
  }
  assert.match(html, /id="news-refresh"/);
  assert.match(html, /everythinghub-search-backend\.vercel\.app\/api\/news/);
});

test("keeps public search and video integrations", () => {
  assert.match(html, /everythinghub-search-backend\.vercel\.app\/api\/search/);
  assert.match(html, /everythinghub-search-backend\.vercel\.app\/api\/search-fast/);
  assert.match(html, /everythinghub-search-backend\.vercel\.app\/api\/videos/);
  assert.match(html, /everythinghub-search-backend\.vercel\.app\/api\/video-thumbnail/);
});
