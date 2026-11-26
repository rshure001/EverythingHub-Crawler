"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "../..");
const site = fs.readFileSync(path.join(root, "site/index.html"), "utf8");
const vercelHomepage = fs.readFileSync(path.join(root, "vercel-homepage/index.html"), "utf8");
const edgeSiteFunction = fs.readFileSync(path.join(root, "supabase/functions/everything-hub-site/index.ts"), "utf8");

test("public homepages preserve approved branding while using separate free search routes", () => {
  const artwork = (source) => source.match(/<img[^>]*class=["'][^"']*approved-master-art[^"']*["'][^>]*src=["'](data:image[/]png;base64,[^"']+)["']/i)?.[1];
  assert.ok(artwork(site), "Edge homepage must retain the approved octopus artwork");
  assert.equal(artwork(site), artwork(vercelHomepage), "Vercel fallback must preserve the exact approved artwork");

  for (const source of [site, vercelHomepage]) {
    for (const mode of ["web", "news", "images", "videos", "shopping", "places", "business", "research"]) {
      assert.ok(source.includes('data-mode="' + mode + '"'), "missing category " + mode);
    }
    assert.ok(source.includes('<script src="search.js"></script>'));
    assert.ok(source.includes("EverythingHubSearch.create"));
    assert.ok(source.includes("https://raw.githubusercontent.com/rshure001/EverythingHub-Crawler/main/public-search/manifest.json"));
    assert.ok(source.includes('id="discoverRefresh"'));
    assert.ok(source.includes("EHMediaPreviewPolicy"));
    assert.ok(!source.includes("appdeploy.ai"));
    assert.ok(!source.includes("ynleeweezwkdbisaiovq"));
  }

  assert.ok(site.includes("vdceayvfgkwpgwixrhwi.supabase.co"));
  for (const endpoint of ["everything-hub-web-search", "everything-hub-media-search", "everything-hub-independent-search"]) {
    assert.ok(site.includes(endpoint), "original Edge site must retain " + endpoint);
  }

  assert.ok(vercelHomepage.includes("'everything-hub-web-search':'/api/search-catalog'"));
  assert.ok(vercelHomepage.includes("'everything-hub-independent-search':'/api/independent-search'"));
  assert.ok(vercelHomepage.includes("'everything-hub-media-search':mediaType==='video'?'/api/videos':'/api/media-search'"));
  assert.ok(!vercelHomepage.includes("functions/v1/everything-hub-web-search"));
  assert.ok(!vercelHomepage.includes("functions/v1/everything-hub-media-search"));
  assert.ok(!vercelHomepage.includes("functions/v1/everything-hub-independent-search"));

  const config = JSON.parse(fs.readFileSync(path.join(root, "vercel-homepage/vercel.json"), "utf8"));
  const destinations = config.rewrites.map((rule) => rule.destination || "");
  assert.ok(destinations.some((url) => url.includes("everythinghub-search-backend.vercel.app") && url.endsWith("/api/search")));
  assert.ok(destinations.some((url) => url.includes("everythinghub-search-backend.vercel.app") && url.endsWith("/api/images")));
  assert.ok(destinations.some((url) => url.includes("everythinghub-search-backend.vercel.app") && url.endsWith("/api/videos")));
  assert.ok(destinations.some((url) => url.includes("everythinghub-search-backend.vercel.app") && url.endsWith("/api/news")));
  assert.ok(destinations.some((url) => url.includes("everythinghub-search-backend.vercel.app") && url.endsWith("/api/search-fast")));
});

test("Vercel homepage crawler search preserves typed catalog previews and sends supported category lanes", () => {
  const start = vercelHomepage.indexOf("async function searchCrawlerIndex(query,offset=0,mode=selectedMode)");
  const end = vercelHomepage.indexOf("async function searchPublicSources(query,mode=selectedMode)", start);
  assert.ok(start >= 0 && end > start);
  const search = vercelHomepage.slice(start, end);
  assert.ok(search.includes("everything-hub-web-search"));
  assert.ok(search.includes("searchParams.set('tentacle'"));
  assert.ok(search.includes("searchParams.set('offset'"));
  assert.ok(vercelHomepage.includes("catalogResults=(page===1?(d.catalogResults||[]):[])"));
  assert.ok(vercelHomepage.includes("preview_only:true"));
  assert.ok(vercelHomepage.includes("commercial_eligible:false"));

  const apiStart = vercelHomepage.indexOf("const loadApiPage=async(page)=>{");
  const apiEnd = vercelHomepage.indexOf("const mediaAllowed=", apiStart);
  assert.ok(apiStart >= 0 && apiEnd > apiStart);
  const api = vercelHomepage.slice(apiStart, apiEnd);
  assert.ok(api.includes("searchCrawlerIndex(q,offset,mode)"));
  assert.ok(!api.includes("everythinghub-search-backend.vercel.app"));
});

test("free Edge site serves the homepage, discovery directory and catalog directory from its own route", () => {
  assert.ok(edgeSiteFunction.includes('const ROOT = "https://vdceayvfgkwpgwixrhwi.supabase.co/functions/v1/everything-hub-site";'));
  assert.ok(edgeSiteFunction.includes("function discoverIndexPage()"));
  assert.ok(edgeSiteFunction.includes("function discoverCategoryPage(slug: string)"));
  assert.ok(edgeSiteFunction.includes("function catalogIndexPage()"));
  assert.ok(edgeSiteFunction.includes('const di=parts.indexOf("discover")'));
  assert.ok(edgeSiteFunction.includes('const ci=parts.indexOf("catalogs")'));
  assert.ok(edgeSiteFunction.includes("edge-homepage-v3-supabase"));
});
