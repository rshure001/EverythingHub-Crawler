'use strict';
const { loadCatalog } = require('../lib/catalog');
const { robotsFetch } = require('../lib/robots');
const { loadPageDerivedCatalogs, searchPageDerivedCatalogs, matchesPageIndexSnapshot } = require('../lib/page-derived-catalogs');
const { searchExternalCatalogs, getExternalCatalogStats } = require('../lib/external-catalogs');
const { searchCatalog, parseBingRss, buildResponse, resolveLaneFilters } = require('../lib/search-core');
const { loadUniversalCatalogNetwork, searchUniversalCatalogs } = require('../lib/universal-catalog-search');
const MAX_QUERY_LENGTH = 160;
const MAX_LIMIT = 50;
const MAX_OFFSET = 5000;
const BING_CACHE_TTL_MS = 120000;
const MAX_BING_BYTES = 1000000;
const bingCache = new Map();
const CATALOG_LOAD_BUDGET_MS = 8500;
const PAGE_DERIVED_LOAD_BUDGET_MS = 7000;
const UNIVERSAL_CATALOG_LOAD_BUDGET_MS = 3500;

function withTimeout(promise, milliseconds, code) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(code)), milliseconds);
  });
  return Promise.race([Promise.resolve(promise), timeout]).finally(() => clearTimeout(timer));
}

async function loadBing(query) {
  const key = query.toLowerCase();
  const existing = bingCache.get(key);
  if (existing && Date.now() - existing.loadedAt < BING_CACHE_TTL_MS) return existing.results;
  const url = 'https://www.bing.com/search?format=rss&q=' + encodeURIComponent(query);
  const response = await robotsFetch(url, {
    headers: { accept: 'application/rss+xml, application/xml, text/xml' },
    signal: AbortSignal.timeout(7000)
  }, { userAgent: 'EverythingHub-IndependentSearch/2.0' });
  if (!response.ok) throw new Error('bing_http_' + response.status);
  const xml = await response.text();
  if (Buffer.byteLength(xml, 'utf8') > MAX_BING_BYTES) throw new Error('bing_response_too_large');
  const results = parseBingRss(xml);
  bingCache.set(key, { loadedAt: Date.now(), results });
  if (bingCache.size > 100) {
    const oldest = Array.from(bingCache.entries()).sort((a, b) => a[1].loadedAt - b[1].loadedAt)[0];
    if (oldest) bingCache.delete(oldest[0]);
  }
  return results;
}

function safeCatalogError(error) {
  const raw = String(error && error.message || 'catalog_unavailable');
  const code = raw.split(':', 1)[0].trim().replace(/[^a-z0-9_-]/gi, '_').slice(0, 80);
  return code || 'catalog_unavailable';
}

function mergeCatalogResults(...groups) {
  let limit = 5;
  if (Number.isInteger(groups[groups.length - 1])) limit = groups.pop();
  const max = Number.isInteger(limit) ? Math.max(1, Math.min(5, limit)) : 5;
  const seen = new Set();
  const merged = [];
  for (const group of groups) {
    for (const item of Array.isArray(group) ? group : []) {
      const id = String(item && item.catalog_id || '').trim();
      if (!id || seen.has(id)) continue;
      seen.add(id);
      merged.push(item);
      if (merged.length >= max) return merged;
    }
  }
  return merged;
}

function buildCatalogPreviewRecordResults(catalogs, limit = 10) {
  const results = [];
  const seen = new Set();
  const max = Number.isSafeInteger(limit) ? Math.max(0, Math.min(20, limit)) : 10;
  for (const card of Array.isArray(catalogs) ? catalogs : []) {
    const records = Array.isArray(card?.matched_records) ? card.matched_records : [];
    for (const record of records) {
      const url = String(record?.source_url || card?.url || '').trim();
      let parsed;
      try { parsed = new URL(url); } catch { continue; }
      if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password) continue;
      const key = String(record?.record_id || url).toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      const recordType = String(record?.record_type || 'record').slice(0, 64);
      const title = String(record?.title || card?.catalog_title || card?.title || 'Catalog record').slice(0, 240);
      const snippet = String(card?.snippet || card?.description || 'Candidate catalog record preview. Preview only.').slice(0, 700);
      results.push({
        title,
        url: parsed.toString(),
        snippet,
        description: snippet,
        source: 'Everything Hub Catalog Preview · ' + recordType,
        origin: 'Everything Hub Catalog Preview',
        provider: 'everythinghub-catalog-preview',
        mediaType: 'web',
        catalog_id: String(card?.catalog_id || ''),
        catalog_title: String(card?.catalog_title || card?.title || ''),
        record_id: String(record?.record_id || ''),
        record_type: recordType,
        relevance_class: String(card?.relevance_class || 'record_match'),
        relevance_label: String(card?.relevance_label || 'Catalog record match'),
        preview_only: true,
        source_terms_status: String(card?.source_terms_status || 'not_reviewed'),
        source_policy_review_status: String(record?.source_policy_review_status || card?.source_policy_review_status || 'not_reviewed'),
        record_level_clearance: String(record?.record_level_clearance || 'not_reviewed'),
        rights_status: String(card?.rights_status || 'publicly_accessible_not_cleared_for_redistribution'),
        commercial_eligible: false
      });
      if (results.length >= max) return results;
    }
  }
  return results;
}

function markCatalogPreviewStale(catalogPreview, freshnessMatch = false) {
  catalogPreview.status = 'stale-candidate-preview';
  catalogPreview.freshnessMatch = freshnessMatch;
  catalogPreview.stale = true;
  return catalogPreview;
}

function applyCatalogPreviewFreshness(catalogPreview, pageDerivedNetwork, catalog) {
  const sourceIndexMatches = matchesPageIndexSnapshot(pageDerivedNetwork, catalog);
  const stale = Boolean(pageDerivedNetwork.stale || !sourceIndexMatches);
  catalogPreview.freshnessMatch = sourceIndexMatches;
  catalogPreview.pageIndexPages = catalog.indexedPages;
  catalogPreview.pageIndexUpdatedAt = new Date(catalog.updatedAt * 1000).toISOString();
  catalogPreview.pageIndexManifestSha256 = catalog.sha256;
  catalogPreview.stale = stale;
  catalogPreview.status = stale ? 'stale-candidate-preview' : 'candidate-preview';
  return { sourceIndexMatches, stale };
}

module.exports = async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  res.setHeader('Cache-Control', 'no-store');
  res.setHeader('X-Content-Type-Options', 'nosniff');
  if (req.method === 'OPTIONS') return res.status(204).end();
  if (req.method !== 'GET') {
    res.setHeader('Allow', 'GET, OPTIONS');
    return res.status(405).json({ error: 'method_not_allowed' });
  }
  const params = new URL(req.url, 'https://everythinghub.invalid').searchParams;
  const query = String(params.get('q') || '').trim();
  if (!query || query.length > MAX_QUERY_LENGTH) {
    return res.status(400).json({ error: 'q_required', max_query_length: MAX_QUERY_LENGTH });
  }
  const limit = Number(params.get('limit') || 50);
  const offset = Number(params.get('offset') || 0);
  if (!Number.isInteger(limit) || limit < 1 || limit > MAX_LIMIT ||
      !Number.isInteger(offset) || offset < 0 || offset > MAX_OFFSET) {
    return res.status(400).json({ error: 'pagination_out_of_range' });
  }
  const lane = String(params.get('tentacle') || params.get('lane') || '').trim().toLowerCase();
  const laneScoped = resolveLaneFilters(lane).size > 0;
  const externalCatalogResults = searchExternalCatalogs(query);
  const externalCatalogStats = getExternalCatalogStats();
  const [catalogResult, bingResult, pageDerivedResult, universalCatalogResult] = await Promise.allSettled([
    withTimeout(loadCatalog(query), CATALOG_LOAD_BUDGET_MS, 'catalog_load_budget_exceeded'),
    laneScoped ? Promise.resolve([]) : loadBing(query),
    withTimeout(loadPageDerivedCatalogs(query), PAGE_DERIVED_LOAD_BUDGET_MS, 'page_catalog_load_budget_exceeded'),
    withTimeout(loadUniversalCatalogNetwork(), UNIVERSAL_CATALOG_LOAD_BUDGET_MS, 'universal_catalog_load_budget_exceeded')
  ]);
  const bingResults = bingResult.status === 'fulfilled' ? bingResult.value : [];
  const pageDerivedNetwork = pageDerivedResult.status === 'fulfilled' ? pageDerivedResult.value : null;
  const universalCatalogNetwork = universalCatalogResult.status === 'fulfilled' ? universalCatalogResult.value : null;
  let pageDerivedCatalogResults = pageDerivedNetwork ? searchPageDerivedCatalogs(query, pageDerivedNetwork, 5) : [];
  const universalCatalogResults = universalCatalogNetwork ? searchUniversalCatalogs(query, universalCatalogNetwork, 5) : [];
  let catalogResults = mergeCatalogResults(externalCatalogResults, universalCatalogResults, pageDerivedCatalogResults);
  const universalCatalogPreview = universalCatalogNetwork ? {
    status: universalCatalogNetwork.stale ? 'stale-candidate-preview' : 'candidate-preview',
    previewOnly: true,
    generatedAt: universalCatalogNetwork.generatedAt,
    catalogCount: universalCatalogNetwork.catalogCount,
    recordCount: universalCatalogNetwork.uniquePublicRecords,
    rootSearchDocumentCount: universalCatalogNetwork.rootSearchDocumentCount,
    rootSearchDocumentOccurrences: universalCatalogNetwork.rootSearchDocumentOccurrences,
    publicRecordMemberships: universalCatalogNetwork.publicRecordMemberships,
    recordsPublished: universalCatalogNetwork.recordsPublished,
    productionIndexed: universalCatalogNetwork.productionIndexed,
    indexShardCount: universalCatalogNetwork.indexShardCount,
    resultCount: universalCatalogResults.length,
    manifestSha256: universalCatalogNetwork.manifestSha256,
    error: universalCatalogNetwork.refreshError || null
  } : {
    status: 'unavailable',
    previewOnly: true,
    catalogCount: 0,
    recordCount: 0,
    resultCount: 0,
    recordsPublished: 0,
    productionIndexed: false,
    error: safeCatalogError(universalCatalogResult.reason || 'universal_catalog_unavailable')
  };
  const catalogPreview = pageDerivedNetwork ? {
    status: 'freshness-unverified',
    previewOnly: true,
    analysisVersion: pageDerivedNetwork.analysisVersion,
    generatedAt: pageDerivedNetwork.generatedAt,
    sourceIndexPages: pageDerivedNetwork.sourceIndexPages,
    sourceIndexUpdatedAt: pageDerivedNetwork.sourceIndexUpdatedAt,
    sourceIndexSha256: pageDerivedNetwork.sourceIndexSha256,
    catalogCount: pageDerivedNetwork.catalogCount,
    recordCount: pageDerivedNetwork.recordCount,
    recordsPublished: Number(pageDerivedNetwork.metrics.records_published) || 0,
    sourceTermsReviewedRecords: Number(pageDerivedNetwork.metrics.source_terms_reviewed_records) || 0,
    resultCount: pageDerivedCatalogResults.length,
    stale: Boolean(pageDerivedNetwork.stale)
  } : {
    status: 'unavailable',
    previewOnly: true,
    resultCount: 0,
    error: safeCatalogError(pageDerivedResult.reason || 'page_catalog_unavailable')
  };

  catalogPreview.externalCatalogCount = externalCatalogStats.catalogCount;
  catalogPreview.externalCatalogRecordCount = externalCatalogStats.recordCount;
  catalogPreview.externalCatalogResults = externalCatalogResults.length;

  if (catalogResult.status !== 'fulfilled') {
    if (pageDerivedNetwork) {
      markCatalogPreviewStale(catalogPreview, false);
      pageDerivedCatalogResults = pageDerivedCatalogResults.map(item => ({ ...item, snippet: item.snippet + ' Current page-index freshness could not be verified.', description: item.snippet + ' Current page-index freshness could not be verified.' }));
      catalogResults = mergeCatalogResults(externalCatalogResults, pageDerivedCatalogResults);
    }
    const catalogError = safeCatalogError(catalogResult.reason);
    console.warn('[catalog-load-failed]', catalogError);
    const catalogPreviewRecordResults = offset === 0
      ? buildCatalogPreviewRecordResults(pageDerivedCatalogResults, Math.min(10, limit))
      : [];
    const combinedResults = offset === 0 ? [...(laneScoped ? [] : bingResults), ...catalogPreviewRecordResults] : [];
    const seenResultUrls = new Set();
    const results = combinedResults.filter((item) => {
      const key = String(item?.url || item?.title || '').toLowerCase();
      if (!key || seenResultUrls.has(key)) return false;
      seenResultUrls.add(key);
      return true;
    }).slice(0, Math.min(10, limit));
    if (catalogPreview) catalogPreview.recordPreviewResults = catalogPreviewRecordResults.length;
    const providers = [];
    if (!laneScoped && bingResults.length) providers.push('Bing Web Search');
    if (pageDerivedCatalogResults.length || catalogPreviewRecordResults.length) providers.push('Everything Hub Catalog Preview');
    if (universalCatalogResults.length) providers.push('Everything Hub Universal Catalog Preview');
    if (externalCatalogResults.length) providers.push('Curated External Catalog Directory');
    const fallbackMode = catalogPreviewRecordResults.length ? 'catalog-preview-fallback' : 'unavailable';
    if (!results.length && !catalogResults.length) {
      return res.status(503).json({
        error: 'search_temporarily_unavailable',
        indexStatus: 'catalog-unavailable',
        indexScan: { mode: 'unavailable', indexedPages: 0, scannedPages: 0, candidateShardCount: 0, error: catalogError },
        catalogError,
        database_required: false,
        catalogResults,
        externalCatalogResults,
        catalogPreview,
        providers
      });
    }
    return res.status(200).json({
      query, count: results.length, totalMatches: results.length,
      page: Math.floor(offset / limit) + 1,
      pageSize: limit, offset, hasMore: false,
      indexedPages: 0,
      indexStatus: catalogPreviewRecordResults.length ? 'catalog-preview-fallback' : 'catalog-unavailable',
      searchScope: catalogPreviewRecordResults.length ? 'page-derived-catalog-preview' : 'external-fallback',
      indexScan: {
        mode: fallbackMode,
        indexedPages: 0,
        scannedPages: 0,
        candidateShardCount: 0,
        previewRecords: catalogPreviewRecordResults.length,
        error: catalogError
      },
      catalogError, database_required: false,
      results, catalogResults, externalCatalogResults, catalogPreview, universalCatalogPreview, providers, fallback: true
    });
  }

  const catalog = catalogResult.value;
  if (pageDerivedNetwork) {
    const { sourceIndexMatches, stale } = applyCatalogPreviewFreshness(catalogPreview, pageDerivedNetwork, catalog);
    if (stale) {
      const warning = !sourceIndexMatches ? ' Catalog analysis is behind the current page index.' : ' Catalog preview is using a cached snapshot.';
      pageDerivedCatalogResults = pageDerivedCatalogResults.map(item => ({ ...item, snippet: item.snippet + warning, description: item.snippet + warning }));
      catalogResults = mergeCatalogResults(externalCatalogResults, universalCatalogResults, pageDerivedCatalogResults);
    }
  }
  const ranked = searchCatalog(catalog.pages, query, lane);
  const payload = buildResponse({
    query, limit, offset, ranked, bingResults: laneScoped ? [] : bingResults,
    indexedPages: catalog.indexedPages,
    indexUpdatedAt: catalog.updatedAt,
    indexSha256: catalog.sha256,
    indexSource: catalog.source,
    indexIntegrity: catalog.integrity,
    indexShardCount: catalog.shardCount,
    indexTransport: catalog.transport,
    staleCatalog: catalog.stale
  });
  payload.indexScan = {
    mode: catalog.queryScoped ? 'query-scoped' : 'full-index',
    indexedPages: catalog.indexedPages,
    scannedPages: Number.isSafeInteger(catalog.scannedPages) ? catalog.scannedPages : catalog.indexedPages,
    candidateShardCount: Number.isSafeInteger(catalog.candidateShardCount) ? catalog.candidateShardCount : catalog.shardCount,
    candidatePageCount: Number.isSafeInteger(catalog.candidatePageCount) ? catalog.candidatePageCount : null,
    totalCandidateShardCount: Number.isSafeInteger(catalog.totalCandidateShardCount) ? catalog.totalCandidateShardCount : catalog.shardCount,
    totalCandidatePageCount: Number.isSafeInteger(catalog.totalCandidatePageCount) ? catalog.totalCandidatePageCount : null,
    candidateTruncated: Boolean(catalog.candidateTruncated),
    candidateSelection: catalog.candidateSelection || null,
    queryIndexPostingCount: Number.isSafeInteger(catalog.queryIndexPostingCount) ? catalog.queryIndexPostingCount : null
  };
  payload.catalogResults = catalogResults;
  payload.externalCatalogResults = externalCatalogResults;
  payload.catalogPreview = catalogPreview;
  payload.universalCatalogPreview = universalCatalogPreview;
  if (pageDerivedCatalogResults.length) payload.providers.push('Everything Hub Catalog Preview');
  if (universalCatalogResults.length) payload.providers.push('Everything Hub Universal Catalog Preview');
  if (externalCatalogResults.length) payload.providers.push('Curated External Catalog Directory');
  if (bingResult.status !== 'fulfilled') payload.providers = payload.providers.filter(x => x !== 'Bing Web Search');
  if (catalog.refreshError) payload.catalogRefreshError = safeCatalogError({ message: catalog.refreshError });
  res.setHeader('X-Index-Updated-At', String(catalog.updatedAt));
  return res.status(200).json(payload);
};

module.exports._test = { mergeCatalogResults, markCatalogPreviewStale, applyCatalogPreviewFreshness, buildCatalogPreviewRecordResults, loadBing };

