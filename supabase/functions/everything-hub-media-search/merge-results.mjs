const MAX_TOTAL_RESULTS = 24;
const MAX_COMMONS_RESULTS = 8;
const MAX_BING_RESULTS = 16;

function canonicalPageUrl(value) {
  try {
    const url = new URL(String(value ?? ""));
    if ((url.protocol !== "http:" && url.protocol !== "https:") || !url.hostname || url.username || url.password) return null;
    url.hash = "";
    return url.href;
  } catch {
    return null;
  }
}

export function mergeMediaResults(bingResults, commonsResults, limit = MAX_TOTAL_RESULTS) {
  const safeLimit = Number.isInteger(limit)
    ? Math.max(1, Math.min(MAX_TOTAL_RESULTS, limit))
    : MAX_TOTAL_RESULTS;
  const bing = Array.isArray(bingResults) ? bingResults : [];
  const commons = Array.isArray(commonsResults) ? commonsResults : [];
  const candidates = [
    ...commons.slice(0, MAX_COMMONS_RESULTS),
    ...bing.slice(0, MAX_BING_RESULTS),
    ...commons.slice(MAX_COMMONS_RESULTS),
    ...bing.slice(MAX_BING_RESULTS),
  ];
  const results = [];
  const seen = new Set();

  for (const item of candidates) {
    if (!item || typeof item !== "object") continue;
    const pageUrl = canonicalPageUrl(item.pageUrl);
    if (!pageUrl || seen.has(pageUrl)) continue;
    seen.add(pageUrl);
    results.push({ ...item, pageUrl });
    if (results.length >= safeLimit) break;
  }

  return results;
}

export function mediaProviderLabel(results, type) {
  const sources = new Set((Array.isArray(results) ? results : []).map((item) => item?.source));
  const labels = [];
  if (sources.has("Wikimedia Commons")) labels.push("Wikimedia Commons");
  const bingLabel = type === "video" ? "Bing Videos" : "Bing Images";
  if (sources.has(bingLabel)) labels.push(bingLabel);
  return labels.join(" + ") || "No media provider";
}
