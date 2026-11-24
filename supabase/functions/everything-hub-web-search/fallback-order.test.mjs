import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const source = readFileSync(new URL("./index.ts", import.meta.url), "utf8");

test("database outage prefers first-party crawler search before independent web fallback", () => {
  const start = source.indexOf("const staticFallback = async");
  assert.ok(start >= 0, "static fallback handler must exist");
  const catalogLoad = source.indexOf("const catalog = await loadStaticCatalog()", start);
  const independentSearch = source.indexOf("const independent = await independentFallback(reason, crawlerIndexCount, attachCatalogPreview)", start);
  const earlyIndependentSearch = source.indexOf("const independent = await independentFallback(reason, null, attachCatalogPreview)", start);
  assert.ok(catalogLoad > start, "first-party crawler catalog must be loaded");
  assert.ok(independentSearch > catalogLoad, "Bing RSS must only run after first-party crawler search");
  assert.equal(earlyIndependentSearch, -1, "database outage must not bypass the first-party index");
});
