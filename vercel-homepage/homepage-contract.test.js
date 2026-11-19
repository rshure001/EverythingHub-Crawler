const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const root = __dirname;
const html = fs.readFileSync(path.join(root, "index.html"), "utf8");

test("homepage uses the approved artwork from the same deployment", () => {
  assert.match(html, /src=["']\/assets\/everythinghub-approved\.png["']/);
  assert.doesNotMatch(html, /everythinghub-homepage-fixed-[^"'\s]+\/assets\/everythinghub-approved\.png/);
  assert.ok(fs.existsSync(path.join(root, "assets", "everythinghub-approved.png")));
});

test("homepage loads the tested media preview policy before its search code", () => {
  const policy = html.indexOf('<script src="/media-preview-policy.js"></script>');
  const app = html.indexOf("<script>", policy + 1);
  assert.ok(policy >= 0, "media preview policy script reference is required");
  assert.ok(app > policy, "media preview policy must load before the homepage search script");
  assert.ok(fs.existsSync(path.join(root, "media-preview-policy.js")));
});

test("homepage keeps one search form and eight category modes", () => {
  assert.equal((html.match(/<form\b[^>]*id=["']search["']/gi) || []).length, 1);
  assert.equal((html.match(/id=["']q["']/gi) || []).length, 1);
  const modes = [...html.matchAll(/data-mode=["'](web|news|images|videos|shopping|places|business|research)["']/g)]
    .map(match => match[1])
    .sort();
  assert.deepEqual(modes, ["business", "images", "news", "places", "research", "shopping", "videos", "web"]);
});
