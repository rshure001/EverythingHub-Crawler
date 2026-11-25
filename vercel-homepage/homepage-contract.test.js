const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const root = __dirname;
const html = fs.readFileSync(path.join(root, "index.html"), "utf8");
const approvedArtwork = "/assets/everythinghub-approved.png";

test("homepage preserves the exact approved artwork resource", () => {
  assert.ok(html.includes('src="data:image/png;base64,'));
  assert.ok(fs.existsSync(path.join(root, "assets/everythinghub-approved.png")));
  assert.ok(!html.includes("appdeploy.ai"));
});

test("homepage embeds the shared media preview policy before its search code", () => {
  const policy = html.indexOf("window.EHMediaPreviewPolicy");
  const app = html.indexOf("async function searchCrawlerIndex(query,offset=0,mode=selectedMode)");
  assert.ok(policy >= 0, "shared media preview policy must be embedded");
  assert.ok(app > policy, "media preview policy must load before homepage search code");
  assert.ok(fs.existsSync(path.join(root, "media-preview-policy.js")));
});

test("homepage search and media use only the dedicated free Everything Hub Edge services", () => {
  assert.match(html, /const EH_SUPABASE_URL=['"]https:\/\/vdceayvfgkwpgwixrhwi\.supabase\.co['"]/);
  assert.match(html, /const EH_PUBLIC_KEY=['"]sb_publishable_XlIsACWhtVS1jVODWYGTpg_GMs8v3vJ['"]/);
  for (const endpoint of ["everything-hub-web-search", "everything-hub-media-search", "everything-hub-independent-search"]) {
    assert.ok(html.includes(endpoint));
  }
  assert.ok(!html.includes("everythinghub-search-backend.vercel.app"));
  assert.ok(!html.includes("ynleeweezwkdbisaiovq"));
});

test("homepage keeps one search form and eight category modes", () => {
  assert.equal((html.match(/<form\b[^>]*id=["']search["']/gi) || []).length, 1);
  assert.equal((html.match(/id=["']q["']/gi) || []).length, 1);
  const modes = [...html.matchAll(/data-mode=["'](web|news|images|videos|shopping|places|business|research)["']/g)]
    .map(match => match[1])
    .sort();
  assert.deepEqual(modes, ["business", "images", "news", "places", "research", "shopping", "videos", "web"]);
});

test("image and video search previews require license and attribution metadata", () => {
  assert.ok(html.includes("window.EHMediaPreviewPolicy?.isMediaPreviewAllowed(x)===true"));
  assert.ok(html.includes("x.creator||x.credit"));
  assert.ok(html.includes("x.license"));
  assert.ok(html.includes("const previewAllowed=mediaAllowed(x)"));
  assert.ok(html.includes("const sourceLabel=isMedia?"));
});

test("Top Stories has five public-source tabs and a working Refresh control", () => {
  for (const topic of ["world", "technology", "sports", "entertainment", "business"]) {
    assert.ok(html.includes('data-topic="' + topic + '"'));
  }
  assert.ok(html.includes('id="discoverRefresh"'));
  assert.ok(html.includes("document.getElementById('discoverRefresh')?.addEventListener"));
  assert.ok(html.includes("everything-hub-independent-search"));
});

test("homepage does not call the paused Vercel API or the unrelated legacy Supabase project", () => {
  assert.ok(!html.includes("everythinghub-search-backend.vercel.app"));
  assert.ok(!html.includes("ynleeweezwkdbisaiovq"));
  assert.ok(!html.includes("api.gdeltproject.org"));
});
