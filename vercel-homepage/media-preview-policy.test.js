const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { isMediaPreviewAllowed } = require("./media-preview-policy.js");

const commons = {
  source: "Wikimedia Commons",
  license: "CC BY 4.0",
  licenseUrl: "https://creativecommons.org/licenses/by/4.0/",
  attributionRequired: true,
  creator: "Example Creator"
};

test("allows public-domain previews when attribution is explicitly not required", () => {
  assert.equal(isMediaPreviewAllowed({
    source: "Wikimedia Commons",
    license: "Public domain",
    attributionRequired: false
  }), true);
});

test("allows CC0 previews when attribution is explicitly not required", () => {
  assert.equal(isMediaPreviewAllowed({
    source: "Wikimedia Commons",
    license: "CC0 1.0",
    attributionRequired: false
  }), true);
});

test("allows CC BY previews with the matching canonical license URL and creator", () => {
  assert.equal(isMediaPreviewAllowed(commons), true);
});

test("allows CC BY-SA previews when creator attribution is present", () => {
  assert.equal(isMediaPreviewAllowed({
    source: "Wikimedia Commons",
    license: "CC BY-SA 4.0",
    licenseUrl: "https://creativecommons.org/licenses/by-sa/4.0/",
    attributionRequired: false,
    creator: "Example Creator"
  }), true);
});

test("rejects CC BY and CC BY-SA when creator attribution is missing even if metadata says false", () => {
  assert.equal(isMediaPreviewAllowed({ ...commons, attributionRequired: false, creator: "" }), false);
  assert.equal(isMediaPreviewAllowed({
    source: "Wikimedia Commons",
    license: "CC BY-SA 4.0",
    licenseUrl: "https://creativecommons.org/licenses/by-sa/4.0/",
    attributionRequired: false,
    creator: ""
  }), false);
});

test("rejects a CC BY license paired with a CC BY-SA license URL", () => {
  assert.equal(isMediaPreviewAllowed({
    ...commons,
    licenseUrl: "https://creativecommons.org/licenses/by-sa/4.0/"
  }), false);
});

test("rejects a CC BY-SA license paired with a CC BY license URL", () => {
  assert.equal(isMediaPreviewAllowed({
    source: "Wikimedia Commons",
    license: "CC BY-SA 4.0",
    licenseUrl: "https://creativecommons.org/licenses/by/4.0/",
    attributionRequired: true,
    creator: "Example Creator"
  }), false);
});

test("rejects non-HTTPS and non-canonical Creative Commons URLs", () => {
  assert.equal(isMediaPreviewAllowed({ ...commons, licenseUrl: "http://creativecommons.org/licenses/by/4.0/" }), false);
  assert.equal(isMediaPreviewAllowed({ ...commons, licenseUrl: "https://example.com/licenses/by/4.0/" }), false);
});

test("rejects Bing results even if a license-like field is present", () => {
  assert.equal(isMediaPreviewAllowed({ ...commons, source: "Bing Images" }), false);
});

test("rejects unverified Bing thumbnails", () => {
  assert.equal(isMediaPreviewAllowed({
    source: "Bing Images",
    license: "",
    attributionRequired: null
  }), false);
});

test("rejects non-commercial and no-derivatives licenses", () => {
  for (const license of ["CC BY-NC 4.0", "CC BY-ND 4.0", "CC BY-NC-SA 4.0"]) {
    assert.equal(isMediaPreviewAllowed({ ...commons, license }), false, license);
  }
});

test("rejects CC BY licenses without an explicit license URL", () => {
  assert.equal(isMediaPreviewAllowed({ ...commons, licenseUrl: "" }), false);
});

test("rejects attribution-required public-domain records without creator credit", () => {
  assert.equal(isMediaPreviewAllowed({
    source: "Wikimedia Commons",
    license: "Public domain",
    attributionRequired: true,
    creator: ""
  }), false);
});

test("rejects unknown attribution state for public-domain and CC0 records", () => {
  assert.equal(isMediaPreviewAllowed({ ...commons, license: "Public domain", attributionRequired: null }), false);
  assert.equal(isMediaPreviewAllowed({ ...commons, license: "CC0 1.0", attributionRequired: null }), false);
});

test("rejects unknown licenses and non-Commons sources", () => {
  assert.equal(isMediaPreviewAllowed({ ...commons, license: "All rights reserved" }), false);
  assert.equal(isMediaPreviewAllowed({ ...commons, source: "Bing Videos" }), false);
});

test("rejects missing and non-object records", () => {
  assert.equal(isMediaPreviewAllowed(null), false);
  assert.equal(isMediaPreviewAllowed("not a media record"), false);
});

test("static Supabase homepage embeds the shared media license policy and keeps unverified media link-only", () => {
  const siteHtml = fs.readFileSync(path.join(__dirname, "../site/index.html"), "utf8");
  const policySource = fs.readFileSync(path.join(__dirname, "media-preview-policy.js"), "utf8").trim();
  const release = JSON.parse(fs.readFileSync(path.join(__dirname, "../site/release-manifest.json"), "utf8"));
  assert.ok(siteHtml.includes(policySource), "static homepage must embed the tested shared policy");
  assert.match(siteHtml, /window\\.EHMediaPreviewPolicy\\?\\.isMediaPreviewAllowed/);
  assert.match(siteHtml, /previewAllowed\\?safeUrl/);
  assert.match(siteHtml, /source:x\\.source\\|\\|data\\.provider/);
  assert.match(siteHtml, /Preview withheld — reuse rights unverified/);
  assert.equal(release.files[0].size, Buffer.byteLength(siteHtml, "utf8"));
  assert.equal(release.files[0].sha256, crypto.createHash("sha256").update(siteHtml).digest("hex"));
});

