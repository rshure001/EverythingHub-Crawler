"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),test=require("node:test");
const html=fs.readFileSync(path.join(__dirname,"fallback","index.html"),"utf8");
const browserWorkflow=fs.readFileSync(path.join(__dirname,"..",".github","workflows","everything-hub-production-browser-check.yml"),"utf8");

test("preserves the exact approved octopus artwork resource",()=>{
  assert.ok(html.includes('src="https://everythinghub-search-engine-4x25f8.v2.appdeploy.ai/resources/everythinghub-approved-octopus.png"'));
});
test("routes free fallback requests to the dedicated Everything Hub project",()=>{
  assert.ok(html.includes("vdceayvfgkwpgwixrhwi.supabase.co/functions/v1"));
  assert.ok(html.includes("sb_publishable_XlIsACWhtVS1jVODWYGTpg_GMs8v3vJ"));
  for(const name of ["everything-hub-web-search","everything-hub-media-search","everything-hub-independent-search"])assert.ok(html.includes(name));
  assert.ok(!html.includes("ynleeweezwkdbisaiovq"));
  assert.ok(!html.includes("sb_publishable_-m5pJUkN9aKUox3NMexhQg_NvWhm520"));
});
test("keeps all eight required search categories and the search input",()=>{
  const modes=[...html.matchAll(/data-mode="(web|news|images|videos|shopping|places|business|research)"/g)].map(match=>match[1]);
  assert.deepEqual([...new Set(modes)].sort(),["business","images","news","places","research","shopping","videos","web"]);
  assert.ok(html.includes('id="query"'));
  assert.ok(html.includes('id="searchForm"'));
});
test("uses the Vercel image API first and falls back to licensed Wikimedia Commons thumbnails",()=>{
  assert.ok(html.includes("everythinghub-search-backend.vercel.app/api/images"));
  assert.ok(html.includes("everything-hub-media-search"));
  for(const field of ["Wikimedia Commons","item.licenseUrl","item.creator","item.thumbnailUrl","item.license&&item.licenseUrl"])assert.ok(html.includes(field));
  assert.ok(!html.includes("api.openverse.org"));
  assert.ok(!html.includes("commons.wikimedia.org/w/api.php"));
});
test("keeps public news tabs and Refresh with an independent-search fallback",()=>{
  for(const category of ["for-you","world","technology","business","science"])assert.ok(html.includes('data-news="'+category+'"'));
  assert.ok(html.includes('id="news-refresh"'));
  assert.ok(html.includes("everythinghub-search-backend.vercel.app/api/news"));
  assert.ok(html.includes("everything-hub-independent-search"));
  assert.ok(html.includes("latest technology news artificial intelligence"));
  assert.ok(html.includes("latest science and space news"));
});
test("uses the Vercel video API first and falls back to licensed Wikimedia Commons video thumbnails",()=>{
  assert.ok(html.includes("everythinghub-search-backend.vercel.app/api/videos"));
  assert.ok(html.includes("everything-hub-media-search"));
  for(const field of ["Wikimedia Commons","item.license","item.creator","item.videoUrl","item.thumbnailUrl","license and reuse terms"])assert.ok(html.includes(field));
  for(const forbidden of ["youtube.com","ytimg.com","api.openverse.org","commons.wikimedia.org/w/api.php","video-thumbnail"])assert.ok(!html.includes(forbidden));
});
test("renders candidate catalog previews separately and keeps rights restrictions visible",()=>{
  for(const field of ["function renderCatalogResults(items,q)","data.catalogResults","item.preview_only===true","item.commercial_eligible===false","eh-catalog-result","Candidate preview only","Source terms not reviewed","Record rights not cleared","Commercial use not cleared","Matched records:","External directory · links only","External links only · excluded from page-derived record counts","Catalog preview is based on an older source-index snapshot"])assert.ok(html.includes(field));
});
test("retries empty category searches against the full owned index before a news-only fast fallback",()=>{
  const start=html.indexOf("async function requestIndexedSearch"),end=html.indexOf("const newsItems=",start);
  assert.ok(start>=0&&end>start);
  const search=html.slice(start,end);
  for(const field of ["if(!data&&mode!=='news')","requestIndexedSearch(q,'')","if(!data&&mode==='news')","External fallback is disabled for this category under the current robots policy"])assert.ok(search.includes(field));
  assert.equal((search.match(/api\/search-fast/g)||[]).length,2);
});
test("keeps standalone script identical to embedded deployment script",()=>{
  const standalone=fs.readFileSync(path.join(__dirname,"fallback","inline-script.js"),"utf8").trim();
  const embedded=html.match(/<script>([\s\S]*?)<\/script>/);
  assert.ok(embedded);assert.equal(standalone,embedded[1].trim());
});
test("normalizes curated external directories without mislabeling them as page-derived",()=>{
  for(const field of ["curated_external_link_directory","external_link_only","External directory · links only","External links only · excluded from page-derived record counts"])assert.ok(html.includes(field));
});
test("allows catalog-aware primary search requests to complete before fast fallback",()=>{
  assert.ok(html.includes("signal:AbortSignal.timeout(30000)"));
});
test("live browser acceptance preserves raw and normalized external catalog source kinds",()=>{
  for(const field of ["node.dataset.sourceOrigin || node.dataset.sourceKind","catalogDisplaySources.includes(\"external_link_only\")","timeout: 30000","timeout: 35000"])assert.ok(browserWorkflow.includes(field));
});
