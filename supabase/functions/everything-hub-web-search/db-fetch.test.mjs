import test from "node:test";
import assert from "node:assert/strict";
import { createTimedFetch, isTransientDatabaseError } from "./db-fetch.mjs";

test("timed fetch preserves caller cancellation while adding a deadline", async () => {
  const caller = new AbortController();
  let capturedSignal;
  const timedFetch = createTimedFetch(async (_input, init = {}) => {
    capturedSignal = init.signal;
    return new Response("ok");
  }, 1000);

  const response = await timedFetch("https://database.example.test", {
    signal: caller.signal,
  });
  assert.equal(await response.text(), "ok");
  assert.ok(capturedSignal);
  assert.equal(capturedSignal.aborted, false);

  caller.abort(new Error("caller cancelled"));
  assert.equal(capturedSignal.aborted, true);
  assert.equal(capturedSignal.reason.message, "caller cancelled");
});

test("timed fetch aborts a stalled database request at its deadline", async () => {
  const timedFetch = createTimedFetch((_input, init = {}) => new Promise((_resolve, reject) => {
    if (init.signal.aborted) {
      reject(init.signal.reason);
      return;
    }
    init.signal.addEventListener("abort", () => reject(init.signal.reason), { once: true });
  }), 5);

  await assert.rejects(
    timedFetch("https://database.example.test"),
    (error) => error?.name === "TimeoutError",
  );
});

test("classifies PostgreSQL recovery and transport failures as transient", () => {
  assert.equal(isTransientDatabaseError({ code: "57P03", message: "not accepting connections" }), true);
  assert.equal(isTransientDatabaseError({ code: "PGRST000", message: "Database connection error" }), true);
  assert.equal(isTransientDatabaseError(new Error("fetch failed")), true);
  assert.equal(isTransientDatabaseError(new Error("The operation timed out")), true);
});

test("classifies PostgREST schema-cache connection failures as transient", () => {
  assert.equal(isTransientDatabaseError({
    code: "PGRST002",
    message: "Could not query the schema cache while PostgreSQL is recovering",
  }), true);
});

test("does not classify a missing search RPC as a transport outage", () => {
  assert.equal(isTransientDatabaseError({
    code: "PGRST202",
    message: "Could not find the function eh_search_pages",
  }), false);
});
