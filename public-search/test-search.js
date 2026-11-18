const assert = require("node:assert/strict");
const engine = require("./search.js");

const pages = [
  { url: "https://www.rolex.com/watches", title: "Rolex watches", description: "Swiss precision timepieces", text: "Rolex watchmaking", lane: "shopping" },
  { url: "https://example.org/watch", title: "Watch", description: "A timepiece", text: "Rolex is a watch brand", lane: "culture" },
  { url: "https://nasa.gov/missions", title: "NASA missions", description: "Space science", text: "NASA launches", lane: "science" }
];

assert.deepEqual(engine.tokenize("ＲＯＬＥＸ — Café"), ["rolex", "café"]);
assert.equal(engine.search(pages, "Rolex")[0].url, "https://www.rolex.com/watches");
assert.equal(engine.search(pages, "watch", { lane: "culture" }).length, 1);
assert.equal(engine.search(pages, "  ").length, 0);
assert.equal(engine.search([...pages, pages[0]], "Rolex").filter(x => x.url === pages[0].url).length, 1);
assert.equal(engine.search([{ url: "javascript:alert(1)", title: "Rolex" }], "Rolex").length, 0);
assert.ok(engine.search(pages, "Rolex")[0].snippet);
assert.ok(engine.search(pages, "watch", { limit: 1000 }).length <= 50);
console.log("8 browser search tests passed");
