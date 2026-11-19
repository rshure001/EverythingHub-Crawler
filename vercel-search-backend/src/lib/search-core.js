'use strict';
const VALID_LANES = new Set([
  'general', 'science', 'news', 'technology',
  'business', 'government', 'culture', 'nature'
]);
function tokenize(value) {
  return String(value || '').toLowerCase().match(/[a-z0-9]{2,}/g) || [];
}
function frequencies(value) {
  const counts = Object.create(null);
  for (const token of tokenize(value)) counts[token] = (counts[token] || 0) + 1;
  return counts;
}
function clean(value) {
  return String(value || '').replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
}
function searchCatalog(pages, query, lane) {
  const terms = Array.from(new Set(tokenize(query)));
  if (!terms.length) return [];
  const requestedLane = String(lane || '').toLowerCase();
  const laneFilter = VALID_LANES.has(requestedLane) ? requestedLane : '';
  const ranked = [];
  for (const page of pages) {
    const pageLane = String(page.lane || page.tentacle || 'general').toLowerCase();
    if (laneFilter && pageLane !== laneFilter) continue;
    const title = frequencies(page.title);
    const description = frequencies(page.description);
    const body = frequencies(String(page.text || '').slice(0, 12000));
    const url = frequencies(page.url);
    let score = 0;
    let matched = 0;
    let strongMatched = 0;
    for (const term of terms) {
      const titleHits = title[term] || 0;
      const descriptionHits = description[term] || 0;
      const urlHits = url[term] || 0;
      const bodyHits = body[term] || 0;
      const strongPoints = titleHits * 20 + descriptionHits * 10 + urlHits * 8;
      const points = strongPoints + Math.min(bodyHits, 8);
      if (points > 0) {
        matched += 1;
        score += points;
      }
      if (strongPoints > 0) strongMatched += 1;
    }
    if (!matched) continue;
    score += matched * 5;
    // Reward complete matches only when every query term appears in a
    // high-signal field. Repeated body-only mentions must not look exact.
    if (strongMatched === terms.length) score += 15;
    // A single weak body mention is discovery evidence, not a useful result.
    if (strongMatched === 0 && score < 18) continue;
    const summary = clean(page.description);
    ranked.push({
      title: clean(page.title) || page.url,
      url: page.url,
      snippet: summary || clean(page.text).slice(0, 320),
      description: summary || clean(page.text).slice(0, 320),
      source: 'Everything Hub Crawler',
      origin: 'Everything Hub Index',
      mediaType: 'web',
      provider: 'everythinghub-catalog',
      lane: pageLane,
      score
    });
  }
  ranked.sort((a, b) => b.score - a.score || a.url.localeCompare(b.url));
  return ranked;
}
function decodeXml(value) {
  return String(value || '')
    .replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, '$1')
    .replace(/&#x([0-9a-f]+);/gi, (_, hex) => String.fromCodePoint(parseInt(hex, 16)))
    .replace(/&#(\d+);/g, (_, number) => String.fromCodePoint(parseInt(number, 10)))
    .replace(/&quot;/g, '"').replace(/&apos;/g, "'").replace(/&#39;/g, "'")
    .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
}
function readTag(block, tag) {
  const match = String(block).match(new RegExp('<' + tag + '\\b[^>]*>([\\s\\S]*?)</' + tag + '>', 'i'));
  return match ? decodeXml(match[1]).trim() : '';
}
function parseBingRss(xml) {
  const blocks = Array.from(String(xml || '').matchAll(/<item\b[^>]*>([\s\S]*?)<\/item>/gi));
  const results = [];
  for (const match of blocks.slice(0, 10)) {
    const block = match[1];
    const title = readTag(block, 'title');
    const url = readTag(block, 'link');
    const snippet = readTag(block, 'description');
    try {
      const parsed = new URL(url);
      if (!['http:', 'https:'].includes(parsed.protocol)) continue;
      results.push({
        title: clean(title) || url,
        url: parsed.href,
        snippet: clean(snippet),
        description: clean(snippet),
        source: 'Bing Web Search',
        origin: 'Bing',
        mediaType: 'web',
        provider: 'bing-rss'
      });
    } catch { continue; }
  }
  return results;
}
function buildResponse(options) {
  const query = options.query;
  const limit = options.limit;
  const offset = options.offset;
  const ranked = options.ranked || [];
  const bingResults = options.bingResults || [];
  const externalSlots = Math.min(10, limit);
  // Reserve up to ten Bing positions on page one. Later offsets are translated
  // to the crawler-only sequence, preventing gaps and duplicates.
  const external = offset === 0 ? bingResults.slice(0, externalSlots) : [];
  const catalogOffset = offset === 0 ? 0 : Math.max(0, offset - externalSlots);
  const catalogLimit = offset === 0 ? Math.max(0, limit - externalSlots) : limit;
  const selected = ranked.slice(catalogOffset, catalogOffset + catalogLimit);
  const results = external.concat(selected);
  const providers = [];
  if (bingResults.length) providers.push('Bing Web Search');
  if (Number(options.indexedPages) > 0) providers.push('Everything Hub Crawler');
  return {
    query,
    count: ranked.length,
    totalMatches: ranked.length,
    page: Math.floor(offset / limit) + 1,
    pageSize: limit,
    offset,
    catalogOffset,
    hasMore: catalogOffset + selected.length < ranked.length,
    indexedPages: options.indexedPages || 0,
    indexStatus: 'crawler-catalog-active',
    indexUpdatedAt: options.indexUpdatedAt || null,
    indexSha256: options.indexSha256 || null,
    staleCatalog: Boolean(options.staleCatalog),
    results,
    providers
  };
}
module.exports = {
  VALID_LANES, tokenize, clean, searchCatalog, parseBingRss, buildResponse
};
