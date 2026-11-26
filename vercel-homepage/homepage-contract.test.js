const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const root = __dirname;
const html = fs.readFileSync(path.join(root, "index.html"), "utf8");
const config = JSON.parse(fs.readFileSync(path.join(root, "vercel.json"), "utf8"));
const backend = "everythinghub-search-backend.vercel.app";

test("homepage preserves the approved octopus artwork", () => {
  assert.ok(html.includes('src="data:image/png;base64,'));
  assert.ok(fs.existsSync(path.join(root, "assets/everythinghub-approved.png")));
  assert.ok(!html.includes("appdeploy.ai"));
});

test("homepage loads the verified public search client and media policy", () => {
  assert.ok(html.includes('<script src="search.js"></script>'));
  assert.ok(fs.existsSync(path.join(root, "search.js")));
  assert.ok(fs.existsSync(path.join(root, "media-preview-policy.js")));
  assert.ok(html.includes("window.EHMediaPreviewPolicy?.isMediaPreviewAllowed(x)===true"));
});

test("all core search, news and media routes use the independent Vercel backend", () => {
  for (const route of ["/api/search-catalog", "/api/independent-search", "/api/media-search", "/api/videos", "/api/news", "/api/search-fast"]) {
    assert.ok(html.includes(route) || html.includes(route.replace("/api/", "/api/")), "homepage is missing " + route);
  }
  for (const endpoint of ["everything-hub-web-search", "everything-hub-media-search", "everything-hub-independent-search"]) {
    assert.ok(html.includes(endpoint));
  }
  assert.ok(!html.includes("functions/v1/everything-hub-web-search"));
  assert.ok(!html.includes("functions/v1/everything-hub-independent-search"));
  assert.ok(!html.includes("functions/v1/everything-hub-media-search"));
  assert.ok(html.includes("const remoteResults="), "homepage must merge independent Vercel search results");
  assert.ok(html.includes("results=[...remoteResults,...results]"), "independent results must remain visible ahead of page snippets");
  const destinations = config.rewrites.map(rule => rule.destination || "");
  assert.ok(destinations.some(url => url.includes(backend) && url.endsWith("/api/search")));
  assert.ok(destinations.some(url => url.includes(backend) && url.endsWith("/api/images")));
  assert.ok(destinations.some(url => url.includes(backend) && url.endsWith("/api/videos")));
  assert.ok(destinations.some(url => url.includes(backend) && url.endsWith("/api/news")));
  assert.ok(destinations.some(url => url.includes(backend) && url.endsWith("/api/search-fast")));
});

test("homepage keeps one search form and all eight search categories", () => {
  assert.equal((html.match(/<form\b[^>]*id=["']search["']/gi) || []).length, 1);
  assert.equal((html.match(/id=["']q["']/gi) || []).length, 1);
  const modes = [...html.matchAll(/data-mode=["'](web|news|images|videos|shopping|places|business|research)["']/g)]
    .map(match => match[1])
    .sort();
  assert.deepEqual(modes, ["business", "images", "news", "places", "research", "shopping", "videos", "web"]);
});

test("Top Stories tabs map to permitted public RSS categories and Refresh is wired", () => {
  for (const topic of ["for-you", "world", "technology", "science", "business"]) {
    assert.ok(html.includes('data-topic="' + topic + '"'));
  }
  assert.ok(html.includes("new URL('/api/news',location.origin)"));
  assert.ok(html.includes('id="discoverRefresh"'));
  assert.ok(html.includes("document.getElementById('discoverRefresh')?.addEventListener"));
});

test("image and video previews support Internet Archive only with validated license and attribution", () => {
  const policy = fs.readFileSync(path.join(root, "media-preview-policy.js"), "utf8");
  assert.ok(policy.includes('source === "Internet Archive"'));
  assert.ok(policy.includes("licenseValid && attributionValid"));
  assert.ok(html.includes("item.thumbnail"));
  assert.ok(html.includes("x.thumbnail"));
  assert.ok(html.includes("Internet Archive"));
});

test("core navigation links resolve to the Vercel site or the public catalog source", () => {
  assert.ok(html.includes('href="https://everythinghub-homepage-fixed.vercel.app/discover/"'));
  assert.ok(html.includes("github.com/rshure001/EverythingHub-Crawler/tree/main/data/page-derived-catalogs"));
  assert.ok(html.includes('href="https://DogTagDay.org"'));
});
