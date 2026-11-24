const QUERY_STOP_TOKENS = new Set([
  "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
  "in", "into", "is", "it", "of", "on", "or", "our", "the", "to",
  "with", "your", "you", "we", "this", "that", "these", "those",
]);

/** Match the crawler query-index tokenization and stop-word policy. */
export function tokenizeIndexedQuery(value) {
  const folded = String(value ?? "").normalize("NFKC").toLowerCase().replace(/ß/g, "ss").replace(/ς/g, "σ");
  const tokens = folded.match(/[\\p{L}\\p{N}]+/gu) ?? [];
  return [...new Set(tokens)].filter((token) => !QUERY_STOP_TOKENS.has(token)).slice(0, 12);
}

/** Return page-shard ordinals containing every indexed query term. */
export function intersectShardPostings(tokens, postingsByToken) {
  if (!Array.isArray(tokens) || !tokens.length || !(postingsByToken instanceof Map)) return [];
  let candidates = null;
  for (const token of tokens) {
    const postings = postingsByToken.get(token);
    if (!Array.isArray(postings) || !postings.length) return [];
    const indexes = new Set(postings.flatMap((posting) => {
      const index = Array.isArray(posting) ? posting[0] : posting;
      return Number.isSafeInteger(index) && index >= 0 ? [index] : [];
    }));
    candidates = candidates === null
      ? indexes
      : new Set([...candidates].filter((index) => indexes.has(index)));
    if (!candidates.size) return [];
  }
  return [...(candidates ?? [])].sort((a, b) => a - b);
}
