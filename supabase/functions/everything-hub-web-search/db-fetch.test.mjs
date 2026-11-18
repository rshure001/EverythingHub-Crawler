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

  // AbortSignal.timeout() uses an unref'ed timer in Node. Keep the test
  // process alive until that deadline fires so node --test does not cancel
  // the pending assertion before the abort is delivered.
  const keepAlive = setTimeout(() => {}, 50);
  try {
    await assert.rejects(
      timedFetch("https://database.example.test"),
      (error) => error?.name === "TimeoutError",
    );
  } finally {
    clearTimeout(keepAlive);
  }
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

test("opens a short circuit after a database 503 and skips repeated probes", async () => {
  let calls = 0;
  const timedFetch = createTimedFetch(async () => {
    calls += 1;
    return new Response("database recovering", { status: 503 });
  }, 1000, 1000);

  const response = await timedFetch("https://database.example.test");
  assert.equal(response.status, 503);
  await assert.rejects(
    timedFetch("https://database.example.test"),
    /Database circuit open/,
  );
  assert.equal(calls, 1);
  assert.equal(isTransientDatabaseError(new Error("Database circuit open after recent transient failure")), true);
});

test("opens the circuit after a transient transport failure", async () => {
  let calls = 0;
  const timedFetch = createTimedFetch(async () => {
    calls += 1;
    throw new Error("fetch failed");
  }, 1000, 1000);

  await assert.rejects(timedFetch("https://database.example.test"), /fetch failed/);
  await assert.rejects(timedFetch("https://database.example.test"), /Database circuit open/);
  assert.equal(calls, 1);
});

test("allows a half-open probe after cooldown and closes after a successful response", async () => {
  let calls = 0;
  const timedFetch = createTimedFetch(async () => {
    calls += 1;
    return calls === 1
      ? new Response("database recovering", { status: 503 })
      : new Response("ok", { status: 200 });
  }, 1000, 10);

  assert.equal((await timedFetch("https://database.example.test")).status, 503);
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(await (await timedFetch("https://database.example.test")).text(), "ok");
  assert.equal(await (await timedFetch("https://database.example.test")).text(), "ok");
  assert.equal(calls, 3);
});

test("does not open the circuit for a non-transient client response", async () => {
  let calls = 0;
  const timedFetch = createTimedFetch(async () => {
    calls += 1;
    return new Response("not found", { status: 404 });
  }, 1000, 1000);

  assert.equal((await timedFetch("https://database.example.test")).status, 404);
  assert.equal((await timedFetch("https://database.example.test")).status, 404);
  assert.equal(calls, 2);
});

test("validates circuit cooldown configuration", () => {
  assert.throws(
    () => createTimedFetch(fetch, 1000, -1),
    /cooldownMs must be a non-negative safe integer/,
  );
});
