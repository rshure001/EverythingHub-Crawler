import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const source = readFileSync(new URL("./index.ts", import.meta.url), "utf8");

test("database outage prefers first-party crawler search before independent web fallback", () => {
  const start = source.indexOf("const staticFallback = async");
  const end = source.indexOf("\nDeno.serve", start);
  assert.ok(start >= 0 && end > start, "static fallback handler must exist");
  const fallback = source.slice(start, end);
  const catalogLoad = fallback.indexOf("const catalog = await loadStaticCatalog()");
  const independentSearch = fallback.indexOf("const independent = await independentFallback(reason, crawlerIndexCount, attachCatalogPreview)");
  assert.ok(catalogLoad >= 0, "first-party crawler catalog must be loaded");
  assert.ok(independentSearch > catalogLoad, "Bing RSS must only run after first-party crawler search");
  assert.ok(!fallback.includes('if (reason === "database_unavailable")'), "database outage must not bypass the first-party index");
});
