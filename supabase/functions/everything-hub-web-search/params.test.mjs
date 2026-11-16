import test from "node:test";
import assert from "node:assert/strict";
import { boundedInteger, normalizeLane, normalizeQuery } from "./params.mjs";

test("boundedInteger uses defaults for missing, blank, malformed, decimal, and unsafe values", () => {
  for (const value of [null, "", "  ", "nope", "2.5", "9007199254740992"]) {
    assert.equal(boundedInteger(value, 10, 1, 20), 10);
  }
});

test("boundedInteger clamps valid integers to inclusive bounds", () => {
  assert.equal(boundedInteger("-5", 10, 0, 5000), 0);
  assert.equal(boundedInteger("0", 10, 1, 20), 1);
  assert.equal(boundedInteger("17", 10, 1, 20), 17);
  assert.equal(boundedInteger("99999", 10, 0, 5000), 5000);
});

test("boundedInteger accepts whitespace around an integer", () => {
  assert.equal(boundedInteger("  12  ", 10, 1, 20), 12);
});

test("normalizeQuery trims and caps query length", () => {
  assert.equal(normalizeQuery("  NASA  "), "NASA");
  assert.equal(normalizeQuery("x".repeat(200)).length, 160);
  assert.equal(normalizeQuery(null), "");
});

test("normalizeLane accepts only known lanes case-insensitively", () => {
  const lanes = ["general", "science", "news"];
  assert.equal(normalizeLane(" SCIENCE ", lanes), "science");
  assert.equal(normalizeLane("unknown", lanes), null);
  assert.equal(normalizeLane(null, lanes), null);
});
