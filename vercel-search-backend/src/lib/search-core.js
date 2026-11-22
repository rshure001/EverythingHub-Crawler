'use strict';
const VALID_LANES = new Set([
  'general', 'science', 'news', 'technology',
  'business', 'government', 'culture', 'nature'
]);
const STOP_TOKENS = new Set([
  'a', 'an', 'and', 'are', 'as', 'at', 'be', 'by', 'for', 'from',
  'in', 'into', 'is', 'it', 'of', 'on', 'or', 'our', 'the', 'to',
  'with', 'your', 'you', 'we', 'this', 'that', 'these', 'those'
]);
function tokenize(value) {
  const normalized = String(value || '').normalize('NFKC').toLowerCase();
  return (normalized.match(/[a-z0-9]{2,}/g) || []).filter(token => !STOP_TOKENS.has(token));
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
  const laneFilter = VALID_LANES.has(requestedLane) && requestedLane !== 'general' ? requestedLane : '';
  const ranked = [];
  for (const page of pages) {
    const pageLane = String(page.lane || page.tentacle || 'general').toLowerCase();
    if (laneFilter && pageLane !== laneFilter && pageLane !== 'general') continue;
    const product = page.product && typeof page.product === 'object' ? page.product : {};
    const title = frequencies(page.title);
    const description = frequencies(page.description);
    const body = frequencies(String(page.text || '').slice(0, 12000));
    const url = frequencies(page.url);
    const productName = frequencies(product.name);
    const productBrand = frequencies(product.brand);
    const productSku = frequencies(product.sku);
    const productPrice = frequencies(product.price);
    const productAvailability = frequencies(product.availability);
    let score = 0;
    let matched = 0;
    let strongMatched = 0;
    for (const term of terms) {
      const titleHits = title[term] || 0;
      const descriptionHits = description[term] || 0;
      const urlHits = url[term] || 0;
      const bodyHits = body[term] || 0;
      const productNameHits = productName[term] || 0;
      const productBrandHits = productBrand[term] || 0;
      const productSkuHits = productSku[term] || 0;
      const productPriceHits = productPrice[term] || 0;
      const productAvailabilityHits = productAvailability[term] || 0;
      const strongPoints = titleHits * 20 + descriptionHits * 10 + urlHits * 8;
      const productPoints = productNameHits * 26 + productBrandHits * 22 + productSkuHits * 18;
      const points = strongPoints + productPoints + productPriceHits * 3 +
        productAvailabilityHits * 2 + Math.min(bodyHits, 8);
      if (points > 0) {
        matched += 1;
        score += points;
      }
      if (strongPoints > 0 || productPoints > 0) strongMatched += 1;
    }
    if (!matched) continue;
    score += matched * 5;
    // Exact multi-word phrases are a stronger relevance signal than scattered
    // token mentions. This is especially important for place and brand queries.
    const phrase = String(query || '').normalize('NFKC').trim().toLowerCase().replace(/\s+/g, ' ');
    const productNameText = String(product.name || '').normalize('NFKC').toLowerCase();
    const productBrandText = String(product.brand || '').normalize('NFKC').toLowerCase();
    if (phrase.length >= 3) {
      if (productNameText.includes(phrase) || productBrandText.includes(phrase)) score += 90;
      else if (String(page.title || '').toLowerCase().includes(phrase)) score += 80;
      else if (String(page.description || '').toLowerCase().includes(phrase)) score += 35;
      else if (String(page.url || '').toLowerCase().includes(phrase.replace(/ /g, '-'))) score += 25;
    }
    // Reward complete matches only when every query term appears in a
    // high-signal field. Repeated body-only mentions must not look exact.
    if (strongMatched === terms.length) score += 15;
    // A single weak body mention is discovery evidence, not a useful result.
    if (strongMatched === 0 && score < 18) continue;
    const summary = clean(page.description);
    const productSummary = [
      clean(product.name),
      clean(product.brand),
      product.sku ? 'SKU ' + clean(product.sku) : '',
      clean(product.price),
      clean(product.availability)
    ].filter(Boolean).join(' · ');
    const snippet = summary || productSummary || clean(page.text).slice(0, 320);
    ranked.push({
      title: clean(product.name) || clean(page.title) || page.url,
      url: page.url,
      snippet,
      description: snippet,
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
function decodeCodePoint(value, radix) {
  const codePoint = Number.parseInt(value, radix);
  if (!Number.isSafeInteger(codePoint) || codePoint < 0 || codePoint > 0x10ffff ||
      (codePoint >= 0xd800 && codePoint <= 0xdfff)) {
    return '\uFFFD';
  }
  return String.fromCodePoint(codePoint);
}

function decodeXml(value) {
  return String(value || '')
    .replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, '$1')
    .replace(/&#x([0-9a-f]+);/gi, (_, hex) => decodeCodePoint(hex, 16))
    .replace(/&#(\d+);/g, (_, number) => decodeCodePoint(number, 10))
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
      if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password) continue;
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
  // Keep Bing supplemental rather than allowing it to crowd out the crawler.
  // Reserve at most 20% of page one (up to ten slots); small searches retain
  // most or all positions for Everything Hub's own indexed results.
  const externalSlots = Math.min(10, Math.floor(limit * 0.2), bingResults.length);
  // Later offsets are translated to the crawler-only sequence so pages do not
  // repeat or skip crawler results when the first page includes Bing supplements.
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
    indexSource: options.indexSource || null,
    indexIntegrity: options.indexIntegrity || null,
    indexShardCount: Number.isInteger(options.indexShardCount) ? options.indexShardCount : null,
    indexTransport: options.indexTransport || null,
    queryScoped: Boolean(options.queryScoped),
    queryScopeComplete: Boolean(options.queryScopeComplete),
    queryCandidatePages: Number.isSafeInteger(options.queryCandidatePages) ? options.queryCandidatePages : null,
    queryCandidateShardCount: Number.isSafeInteger(options.queryCandidateShardCount) ? options.queryCandidateShardCount : null,
    queryTokenCount: Number.isSafeInteger(options.queryTokenCount) ? options.queryTokenCount : null,
    queryIndexPrefixCount: Number.isSafeInteger(options.queryIndexPrefixCount) ? options.queryIndexPrefixCount : null,
    queryIndexFallbackReason: options.queryIndexFallbackReason || null,
    staleCatalog: Boolean(options.staleCatalog),
    results,
    providers
  };
}
module.exports = {
  VALID_LANES, STOP_TOKENS, tokenize, clean, searchCatalog, parseBingRss, buildResponse
};
