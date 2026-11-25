"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "../..");
const site = fs.readFileSync(path.join(root, "site/index.html"), "utf8");
const vercelHomepage = fs.readFileSync(path.join(root, "vercel-homepage/index.html"), "utf8");
const edgeSiteFunction = fs.readFileSync(path.join(root, "supabase/functions/everything-hub-site/index.ts"), "utf8");

test("public homepage uses only the dedicated free Everything Hub Edge services", () => {
  assert.equal(site, vercelHomepage, "the deployed Edge site and Vercel source homepage must stay in sync");
  for (const source of [site, vercelHomepage]) {
    assert.ok(source.includes("vdceayvfgkwpgwixrhwi.supabase.co"));
    assert.ok(source.includes("sb_publishable_XlIsACWhtVS1jVODWYGTpg_GMs8v3vJ"));
    assert.ok(source.includes("everything-hub-web-search"));
    assert.ok(source.includes("everything-hub-media-search"));
    assert.ok(source.includes("everything-hub-independent-search"));
    assert.ok(source.includes('<script src="search.js"></script>'));
    assert.ok(source.includes("EverythingHubSearch.create"));
    assert.ok(source.includes("https://raw.githubusercontent.com/rshure001/EverythingHub-Crawler/main/public-search/manifest.json"));
    assert.ok(!source.includes("appdeploy.ai"));
    assert.ok(source.includes('id="discoverRefresh"'));
    assert.ok(source.includes("EHMediaPreviewPolicy"));
    assert.ok(!source.includes("everythinghub-search-backend.vercel.app"));
    assert.ok(!source.includes("ynleeweezwkdbisaiovq"));
  }
});

test("homepage crawler search preserves typed catalog previews and uses supported Edge lanes", () => {
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
