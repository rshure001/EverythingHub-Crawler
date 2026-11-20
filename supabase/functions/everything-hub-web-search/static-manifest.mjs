// Keep the public manifest guard ahead of the current catalog while retaining a hard bound.
export const MAX_STATIC_INDEX_PAGES = 60_000;
export const MAX_STATIC_INDEX_SHARDS = 512;

/**
 * Validate the bounded public catalog manifest contract shared by the Edge Function
 * and the repository-side catalog smoke test.
 * @param {unknown} value
 */
export function isValidStaticManifest(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const manifest = /** @type {Record<string, unknown>} */ (value);
  const shards = manifest.shards;
  const totalPages = manifest.total_pages;
  const updatedAt = manifest.updated_at;
  return (
    manifest.version === 1 &&
    manifest.integrity === "sha256" &&
    Number.isSafeInteger(updatedAt) &&
    updatedAt > 0 &&
    Array.isArray(shards) &&
    shards.length >= 1 &&
    shards.length <= MAX_STATIC_INDEX_SHARDS &&
    manifest.shard_count === shards.length &&
    typeof totalPages === "number" &&
    Number.isInteger(totalPages) &&
    totalPages >= 1 &&
    totalPages <= MAX_STATIC_INDEX_PAGES
  );
}
