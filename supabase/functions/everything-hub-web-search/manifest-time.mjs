export function normalizeManifestTimestamp(value) {
  let milliseconds;

  if (typeof value === "number" && Number.isFinite(value) && value > 0) {
    // The public crawler manifest stores Unix epoch seconds.
    milliseconds = value * 1000;
  } else if (typeof value === "string" && value.trim()) {
    milliseconds = Date.parse(value);
  } else {
    return null;
  }

  if (!Number.isFinite(milliseconds)) return null;
  const date = new Date(milliseconds);
  return Number.isFinite(date.getTime()) ? date.toISOString() : null;
}
