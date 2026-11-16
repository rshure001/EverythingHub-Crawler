function tokenize(value) {
  return String(value ?? "")
    .toLowerCase()
    .split(/[^\p{L}\p{N}]+/u)
    .filter(Boolean);
}

function occurrences(text, term, cap = 8) {
  let count = 0;
  let offset = 0;
  while (count < cap) {
    const found = text.indexOf(term, offset);
    if (found < 0) break;
    count += 1;
    offset = found + term.length;
  }
  return count;
}

export function searchStaticPages(pages, query, lane = null, limit = 10, offset = 0) {
  const normalizedQuery = String(query ?? "").trim().toLowerCase().replace(/\s+/g, " ");
  const terms = [...new Set(tokenize(normalizedQuery))].slice(0, 12);
  const safeLimit = Number.isInteger(limit) ? Math.min(20, Math.max(1, limit)) : 10;
  const safeOffset = Number.isInteger(offset) ? Math.min(5000, Math.max(0, offset)) : 0;

  if (!normalizedQuery || !terms.length || !Array.isArray(pages)) {
    return { results: [], count: 0, hasMore: false };
  }

  const matches = [];
  for (const page of pages) {
    if (!page || typeof page !== "object" || page.robots_allowed === false) continue;

    const url = String(page.url ?? "");
    let hostname = "";
    try {
      const parsed = new URL(url);
      if (parsed.protocol !== "http:" && parsed.protocol !== "https:") continue;
      hostname = parsed.hostname;
    } catch {
      continue;
    }

    const pageLane = typeof page.lane === "string" && page.lane ? page.lane : "general";
    if (lane && pageLane !== lane) continue;

    const title = String(page.title ?? "");
    const description = String(page.description ?? "");
    const body = String(page.text ?? "");
    const titleText = title.toLowerCase();
    const descriptionText = description.toLowerCase();
    const bodyText = body.toLowerCase();
    const combined = titleText + " " + descriptionText + " " + bodyText;

    if (!terms.every((term) => combined.includes(term))) continue;

    let score = 0;
    for (const term of terms) {
      score += Math.min(4, occurrences(titleText, term)) * 8;
      score += Math.min(4, occurrences(descriptionText, term)) * 3;
      score += Math.min(6, occurrences(bodyText, term));
    }
    if (titleText.includes(normalizedQuery)) score += 18;
    else if (descriptionText.includes(normalizedQuery)) score += 8;
    else if (bodyText.includes(normalizedQuery)) score += 4;

    matches.push({
      score,
      title: title || url,
      url,
      hostname,
      description,
      body,
      lane: pageLane,
    });
  }

  matches.sort((a, b) =>
    b.score - a.score ||
    a.title.localeCompare(b.title) ||
    a.url.localeCompare(b.url)
  );

  const pageMatches = matches.slice(safeOffset, safeOffset + safeLimit);
  const results = pageMatches.map((match) => ({
    title: match.title,
    url: match.url,
    snippet: String(match.description || match.body || "").replace(/\s+/g, " ").trim().slice(0, 320),
    source: match.hostname,
    origin: "Everything Hub public crawler index",
    tentacle: match.lane,
    relevance: match.score,
  }));

  return {
    results,
    count: matches.length,
    hasMore: safeOffset + results.length < matches.length,
  };
}
