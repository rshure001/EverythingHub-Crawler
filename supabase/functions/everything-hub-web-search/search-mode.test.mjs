import test from "node:test";
import assert from "node:assert/strict";
import { shouldUseDatabaseSearch } from "./search-mode.mjs";

test("verified static index is primary unless database search is explicitly enabled", () => {
  assert.equal(shouldUseDatabaseSearch({}), false);
  assert.equal(shouldUseDatabaseSearch({ EH_DATABASE_SEARCH_ENABLED: "false" }), false);
  assert.equal(shouldUseDatabaseSearch({ EH_DATABASE_SEARCH_ENABLED: "1" }), false);
  assert.equal(shouldUseDatabaseSearch({ EH_DATABASE_SEARCH_ENABLED: "true" }), true);
  assert.equal(shouldUseDatabaseSearch({ get: (key) => key === "EH_DATABASE_SEARCH_ENABLED" ? "TRUE" : null }), true);
});
