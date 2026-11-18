import test from "node:test";
import assert from "node:assert/strict";
import { readBoundedText } from "./bounded-stream.mjs";

test("decodes a stream split across multiple chunks", async () => {
  const bytes = new TextEncoder().encode("Hurricane 🌪️");
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(bytes.slice(0, 7));
      controller.enqueue(bytes.slice(7, 11));
      controller.enqueue(bytes.slice(11));
      controller.close();
    },
  });
  assert.equal(await readBoundedText(stream, bytes.length), "Hurricane 🌪️");
});

test("rejects output above the decompressed byte limit and cancels the stream", async () => {
  let cancelled = false;
  const stream = new ReadableStream({
    pull(controller) {
      controller.enqueue(new Uint8Array(8));
    },
    cancel() {
      cancelled = true;
    },
  });
  await assert.rejects(readBoundedText(stream, 7), /exceeds configured byte limit/);
  assert.equal(cancelled, true);
});

test("rejects invalid UTF-8 rather than silently replacing bytes", async () => {
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(new Uint8Array([0xff, 0xfe]));
      controller.close();
    },
  });
  await assert.rejects(readBoundedText(stream, 10), TypeError);
});

test("rejects invalid output limits", async () => {
  const stream = new ReadableStream({ start(controller) { controller.close(); } });
  await assert.rejects(readBoundedText(stream, -1), RangeError);
  await assert.rejects(readBoundedText(stream, 1.5), RangeError);
});
