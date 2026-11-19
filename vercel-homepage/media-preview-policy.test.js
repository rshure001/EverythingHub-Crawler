const test = require("node:test");
const assert = require("node:assert/strict");
const { isMediaPreviewAllowed } = require("./media-preview-policy.js");

const commons = {
  source: "Wikimedia Commons",
  license: "CC BY 4.0",
  licenseUrl: "https://creativecommons.org/licenses/by/4.0/",
  attributionRequired: true,
  creator: "Example Creator"
};

test("allows public-domain previews when attribution metadata says none is required", () => {
  assert.equal(isMediaPreviewAllowed({
    source: "Wikimedia Commons",
    license: "Public domain",
    attributionRequired: false
  }), true);
});

test("allows CC0 previews", () => {
  assert.equal(isMediaPreviewAllowed({
    source: "Wikimedia Commons",
    license: "CC0 1.0",
    attributionRequired: false
  }), true);
});

test("allows CC BY previews with a license URL and creator attribution", () => {
  assert.equal(isMediaPreviewAllowed(commons), true);
});

test("allows CC BY-SA previews when the attribution requirement is explicitly false", () => {
  assert.equal(isMediaPreviewAllowed({
    source: "Wikimedia Commons",
    license: "CC BY-SA 4.0",
    licenseUrl: "https://creativecommons.org/licenses/by-sa/4.0/",
    attributionRequired: false
  }), true);
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

test("rejects attribution-required media without creator or credit", () => {
  assert.equal(isMediaPreviewAllowed({ ...commons, creator: "", credit: "" }), false);
});

test("rejects unknown attribution state", () => {
  assert.equal(isMediaPreviewAllowed({ ...commons, attributionRequired: null }), false);
});

test("rejects unknown licenses and non-Commons sources", () => {
  assert.equal(isMediaPreviewAllowed({ ...commons, license: "All rights reserved" }), false);
  assert.equal(isMediaPreviewAllowed({ ...commons, source: "Bing Videos" }), false);
});

test("rejects missing and non-object records", () => {
  assert.equal(isMediaPreviewAllowed(null), false);
  assert.equal(isMediaPreviewAllowed("not a media record"), false);
});
