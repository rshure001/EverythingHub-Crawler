/**
 * Read a decompressed byte stream with a hard output limit before allocating
 * the final text buffer. This protects the Edge Function from gzip bombs.
 * @param {ReadableStream<Uint8Array>} stream
 * @param {number} maxBytes
 * @returns {Promise<string>}
 */
export async function readBoundedText(stream, maxBytes) {
  if (!Number.isSafeInteger(maxBytes) || maxBytes < 0) {
    throw new RangeError("maxBytes must be a non-negative safe integer");
  }

  const reader = stream.getReader();
  const chunks = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      if (!(value instanceof Uint8Array)) {
        throw new TypeError("decompressed stream must yield Uint8Array chunks");
      }
      total += value.byteLength;
      if (total > maxBytes) {
        throw new RangeError("decompressed stream exceeds configured byte limit");
      }
      chunks.push(value);
    }
  } catch (error) {
    try {
      await reader.cancel(error);
    } catch {
      // Preserve the original validation error.
    }
    throw error;
  }

  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
}
