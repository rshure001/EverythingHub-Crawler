const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { isMediaPreviewAllowed } = require("./media-preview-policy.js");

const commons = {
  source: "Wikimedia Commons",
  license: "CC BY 4.0",
  licenseUrl: "https://creativecommons.org/licenses/by/4.0/",
  attributionRequired: true,
  creator: "Example Creator"
};

test("allows verified Wikimedia Commons public-domain, CC0 and CC BY previews", () => {
  assert.equal(isMediaPreviewAllowed({source:"Wikimedia Commons",license:"Public domain",attributionRequired:false}),true);
  assert.equal(isMediaPreviewAllowed({source:"Wikimedia Commons",license:"CC0 1.0",attributionRequired:false}),true);
  assert.equal(isMediaPreviewAllowed(commons),true);
});

test("allows Internet Archive CC0 and Public Domain Mark previews when attribution is explicitly resolved", () => {
  assert.equal(isMediaPreviewAllowed({source:"Internet Archive",license:"CC0 1.0",licenseUrl:"https://creativecommons.org/publicdomain/zero/1.0/",attributionRequired:false}),true);
  assert.equal(isMediaPreviewAllowed({source:"Internet Archive",license:"Public Domain Mark 1.0",licenseUrl:"https://creativecommons.org/publicdomain/mark/1.0/",attributionRequired:false}),true);
});

test("allows Internet Archive CC BY and CC BY-SA only with canonical license URLs and creator attribution", () => {
  assert.equal(isMediaPreviewAllowed({source:"Internet Archive",license:"CC BY 4.0",licenseUrl:"https://creativecommons.org/licenses/by/4.0/",attributionRequired:true,creator:"Archive Creator"}),true);
  assert.equal(isMediaPreviewAllowed({source:"Internet Archive",license:"CC BY-SA 4.0",licenseUrl:"https://creativecommons.org/licenses/by-sa/4.0/",attributionRequired:true,creator:"Archive Creator"}),true);
  assert.equal(isMediaPreviewAllowed({source:"Internet Archive",license:"CC BY 4.0",licenseUrl:"https://creativecommons.org/licenses/by/4.0/",attributionRequired:false,creator:""}),false);
});

test("rejects mismatched, non-HTTPS, non-canonical or unapproved license metadata", () => {
  assert.equal(isMediaPreviewAllowed({...commons,licenseUrl:"https://creativecommons.org/licenses/by-sa/4.0/"}),false);
  assert.equal(isMediaPreviewAllowed({...commons,licenseUrl:"http://creativecommons.org/licenses/by/4.0/"}),false);
  assert.equal(isMediaPreviewAllowed({...commons,license:"CC BY-NC 4.0"}),false);
  assert.equal(isMediaPreviewAllowed({...commons,license:"CC BY-ND 4.0"}),false);
  assert.equal(isMediaPreviewAllowed({...commons,license:"All rights reserved"}),false);
});

test("rejects Bing results and any source outside the explicit preview allowlist", () => {
  assert.equal(isMediaPreviewAllowed({...commons,source:"Bing Images"}),false);
  assert.equal(isMediaPreviewAllowed({...commons,source:"Internet Archive",license:"All rights reserved"}),false);
});

test("rejects unknown attribution state for public-domain and CC0 records", () => {
  assert.equal(isMediaPreviewAllowed({source:"Internet Archive",license:"Public domain",attributionRequired:null}),false);
  assert.equal(isMediaPreviewAllowed({source:"Internet Archive",license:"CC0 1.0",attributionRequired:null}),false);
});

test("rejects missing and non-object records", () => {
  assert.equal(isMediaPreviewAllowed(null),false);
  assert.equal(isMediaPreviewAllowed("not a media record"),false);
});

test("Vercel homepage embeds the shared media preview gate before search rendering", () => {
  const html=fs.readFileSync(path.join(__dirname,"index.html"),"utf8");
  assert.ok(html.includes("source === \"Internet Archive\""));
  assert.ok(html.includes("isMediaPreviewAllowed(item)"));
  assert.ok(html.includes("isMediaPreviewAllowed(x)"));
});
