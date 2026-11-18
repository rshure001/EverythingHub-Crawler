const FIELD_WEIGHTS = { title: 4, url: 2.2, description: 1.7, text: 1 };
const MAX_QUERY_CHARS = 240;
const MAX_TERMS = 12;

function normalize(value) {
  return String(value ?? "").normalize("NFKC").toLowerCase();
}

function tokenize(value) {
  return normalize(value).match(/[\p{L}\p{N}]+/gu) ?? [];
}

function occurrences(tokens, term) {
  let count = 0;
  for (const token of tokens) if (token === term) count += 1;
  return count;
}

function snippetFor(page, terms, maxChars = 320) {
  const source = String(page.description || page.text || "").replace(/\s+/g, " ").trim();
  if (!source) return "";
  const words = source.split(" ");
  const index = words.findIndex((word) => tokenize(word).some((token) => terms.includes(token)));
  if (index < 0) return source.slice(0, maxChars);
  const start = Math.max(0, index - 9);
  const excerpt = words.slice(start, index + 25).join(" ");
  return (start ? "…" : "") + excerpt.slice(0, maxChars);
}

/**
 * Search verified public crawler records without PostgreSQL.
 * Preserves the web-search function's response contract and lane semantics.
 */
export function searchStaticPages(pages, query, lane = null, limit = 10, offset = 0) {
  const rawQuery = String(query ?? "").trim().slice(0, MAX_QUERY_CHARS);
  const terms = [...new Set(tokenize(rawQuery))].slice(0, MAX_TERMS);
  const safeLimit = Number.isInteger(limit) ? Math.min(20, Math.max(1, limit)) : 10;
  const safeOffset = Number.isInteger(offset) ? Math.min(5000, Math.max(0, offset)) : 0;
  if (!rawQuery || !terms.length || !Array.isArray(pages)) {
    return { results: [], count: 0, hasMore: false };
  }

  const laneFilter = lane ? String(lane).toLowerCase() : null;
  const phrase = terms.join(" ");
  const matches = [];
  const seen = new Set();

  for (const page of pages) {
    if (!page || typeof page !== "object" || page.robots_allowed === false) continue;
    const url = String(page.url ?? "").trim();
    let parsed;
    try {
      parsed = new URL(url);
      if (parsed.protocol !== "http:" && parsed.protocol !== "https:") continue;
      if (!parsed.hostname || seen.has(parsed.href)) continue;
    } catch {
      continue;
    }

    const pageLane = typeof page.lane === "string" && page.lane ? page.lane : "general";
    if (laneFilter && pageLane.toLowerCase() !== laneFilter) continue;

    const fields = {
      title: tokenize(page.title),
      url: tokenize(parsed.href),
      description: tokenize(page.description),
      text: tokenize(page.text),
    };
    const allTokens = Object.values(fields).flat();
    if (!allTokens.length) continue;

    const frequencies = new Map();
    for (const token of allTokens) frequencies.set(token, (frequencies.get(token) ?? 0) + 1);
    // AND matching prevents a multi-term query from returning pages that only
    // happen to contain one common word.
    if (!terms.every((term) => frequencies.has(term))) continue;

    let score = 0;
    for (const term of terms) {
      const tf = frequencies.get(term) ?? 0;
      let weightedTf = 0;
      for (const [field, weight] of Object.entries(FIELD_WEIGHTS)) {
        weightedTf += weight * occurrences(fields[field], term);
      }
      score += (weightedTf / (weightedTf + 1.2)) * (1 + Math.log1p(tf));
    }

    const titlePhrase = fields.title.join(" ");
    const urlPhrase = fields.url.join(" ");
    if (titlePhrase.includes(phrase)) score += 5;
    if (urlPhrase.includes(phrase)) score += 2;

    seen.add(parsed.href);
    matches.push({
      score,
      title: String(page.title || url).slice(0, 500),
      url: parsed.href,
      hostname: parsed.hostname,
      description: String(page.description || "").slice(0, 500),
      text: String(page.text || ""),
      lane: pageLane,
      terms,
    });
  }

  matches.sort((a, b) =>
    b.score - a.score ||
    a.title.localeCompare(b.title, "en", { sensitivity: "base" }) ||
    a.url.localeCompare(b.url),
  );

  const pageMatches = matches.slice(safeOffset, safeOffset + safeLimit);
  const results = pageMatches.map((match) => ({
    title: match.title,
    url: match.url,
    snippet: snippetFor({ description: match.description, text: match.text }, match.terms),
    source: match.hostname,
    origin: "Everything Hub public crawler index",
    tentacle: match.lane,
    relevance: Number(match.score.toFixed(6)),
  }));

  return {
    results,
    count: matches.length,
    hasMore: safeOffset + results.length < matches.length,
  };
}
