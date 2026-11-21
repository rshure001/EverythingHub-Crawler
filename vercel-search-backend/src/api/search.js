'use strict';
const https = require('node:https');
const { loadCatalog } = require('../lib/catalog');
const { loadPageDerivedCatalogs, searchPageDerivedCatalogs, matchesPageIndexSnapshot } = require('../lib/page-derived-catalogs');
const { searchExternalCatalogs, getExternalCatalogStats } = require('../lib/external-catalogs');
const { searchCatalog, parseBingRss, buildResponse } = require('../lib/search-core');
const MAX_QUERY_LENGTH = 160;
const MAX_LIMIT = 50;
const MAX_OFFSET = 5000;
const BING_CACHE_TTL_MS = 120000;
const MAX_BING_BYTES = 1000000;
const bingCache = new Map();

function fetchText(url) {
  return new Promise((resolve, reject) => {
    let request;
    const chunks = [];
    let total = 0;
    request = https.get(url, {
      headers: {
        'user-agent': 'EverythingHub-IndependentSearch/2.0',
        accept: 'application/rss+xml, application/xml, text/xml'
      }
    }, (response) => {
      if (response.statusCode < 200 || response.statusCode >= 300) {
        response.resume();
        reject(new Error('bing_http_' + response.statusCode));
        return;
      }
      const declared = Number(response.headers['content-length'] || 0);
      if (declared > MAX_BING_BYTES) {
        response.resume();
        reject(new Error('bing_response_too_large'));
        return;
      }
      response.on('data', (chunk) => {
        total += chunk.length;
        if (total > MAX_BING_BYTES) {
          request.destroy(new Error('bing_response_too_large'));
          return;
        }
        chunks.push(chunk);
      });
      response.on('end', () => resolve(Buffer.concat(chunks, total).toString('utf8')));
      response.on('error', reject);
    });
    request.setTimeout(7000, () => request.destroy(new Error('bing_timeout')));
    request.on('error', reject);
  });
}
async function loadBing(query) {
  const key = query.toLowerCase();
  const existing = bingCache.get(key);
  if (existing && Date.now() - existing.loadedAt < BING_CACHE_TTL_MS) return existing.results;
  const url = 'https://www.bing.com/search?format=rss&q=' + encodeURIComponent(query);
  const results = parseBingRss(await fetchText(url));
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

function mergeCatalogResults(externalCatalogResults, pageDerivedCatalogResults, limit = 5) {
  const max = Number.isInteger(limit) ? Math.max(1, Math.min(5, limit)) : 5;
  const seen = new Set();
  const merged = [];
  for (const item of [...(externalCatalogResults || []), ...(pageDerivedCatalogResults || [])]) {
    const id = String(item && item.catalog_id || '').trim();
    if (!id || seen.has(id)) continue;
    seen.add(id);
    merged.push(item);
    if (merged.length >= max) break;
  }
  return merged;
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
  const externalCatalogResults = searchExternalCatalogs(query);
  const externalCatalogStats = getExternalCatalogStats();
  const [catalogResult, bingResult, pageDerivedResult] = await Promise.allSettled([
    loadCatalog(), loadBing(query), loadPageDerivedCatalogs(query)
  ]);
  const bingResults = bingResult.status === 'fulfilled' ? bingResult.value : [];
  const pageDerivedNetwork = pageDerivedResult.status === 'fulfilled' ? pageDerivedResult.value : null;
  let pageDerivedCatalogResults = pageDerivedNetwork ? searchPageDerivedCatalogs(query, pageDerivedNetwork, 5) : [];
  let catalogResults = mergeCatalogResults(externalCatalogResults, pageDerivedCatalogResults);
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
      catalogPreview.status = 'stale-candidate-preview';
      catalogPreview.freshnessMatch = false;
      pageDerivedCatalogResults = pageDerivedCatalogResults.map(item => ({ ...item, snippet: item.snippet + ' Current page-index freshness could not be verified.', description: item.snippet + ' Current page-index freshness could not be verified.' }));
      catalogResults = mergeCatalogResults(externalCatalogResults, pageDerivedCatalogResults);
    }
    const catalogError = safeCatalogError(catalogResult.reason);
    console.warn('[catalog-load-failed]', catalogError);
    const results = offset === 0 ? bingResults.slice(0, Math.min(10, limit)) : [];
    const providers = [];
    if (bingResults.length) providers.push('Bing Web Search');
    if (catalogResults.length) providers.push('Everything Hub Catalog Preview');
    if (!results.length && !catalogResults.length) {
      return res.status(503).json({
        error: 'search_temporarily_unavailable',
        indexStatus: 'catalog-unavailable',
        catalogError,
        database_required: false,
        catalogResults,
        catalogPreview,
        providers
      });
    }
    return res.status(200).json({
      query, count: 0, totalMatches: 0,
      page: Math.floor(offset / limit) + 1,
      pageSize: limit, offset, hasMore: false,
      indexedPages: 0, indexStatus: 'catalog-unavailable',
      catalogError, database_required: false,
      results, catalogResults, catalogPreview, providers, fallback: true
    });
  }

  const catalog = catalogResult.value;
  if (pageDerivedNetwork) {
    const sourceIndexMatches = matchesPageIndexSnapshot(pageDerivedNetwork, catalog);
    catalogPreview.freshnessMatch = sourceIndexMatches;
    catalogPreview.pageIndexPages = catalog.indexedPages;
    catalogPreview.pageIndexUpdatedAt = new Date(catalog.updatedAt * 1000).toISOString();
    catalogPreview.pageIndexManifestSha256 = catalog.sha256;
    catalogPreview.status = pageDerivedNetwork.stale || !sourceIndexMatches
      ? 'stale-candidate-preview' : 'candidate-preview';
    if (pageDerivedNetwork.stale || !sourceIndexMatches) {
      const warning = !sourceIndexMatches ? ' Catalog analysis is behind the current page index.' : ' Catalog preview is using a cached snapshot.';
      pageDerivedCatalogResults = pageDerivedCatalogResults.map(item => ({ ...item, snippet: item.snippet + warning, description: item.snippet + warning }));
      catalogResults = [...externalCatalogResults, ...pageDerivedCatalogResults].slice(0, 5);
    }
  }
  const ranked = searchCatalog(catalog.pages, query, lane);
  const payload = buildResponse({
    query, limit, offset, ranked, bingResults,
    indexedPages: catalog.indexedPages,
    indexUpdatedAt: catalog.updatedAt,
    indexSha256: catalog.sha256,
    indexSource: catalog.source,
    indexIntegrity: catalog.integrity,
    indexShardCount: catalog.shardCount,
    indexTransport: catalog.transport,
    staleCatalog: catalog.stale
  });
  payload.catalogResults = catalogResults;
  payload.externalCatalogResults = externalCatalogResults;
  payload.catalogPreview = catalogPreview;
  if (catalogResults.length) payload.providers.push('Everything Hub Catalog Preview');
  if (bingResult.status !== 'fulfilled') payload.providers = payload.providers.filter(x => x !== 'Bing Web Search');
  if (catalog.refreshError) payload.catalogRefreshError = safeCatalogError({ message: catalog.refreshError });
  res.setHeader('X-Index-Updated-At', String(catalog.updatedAt));
  return res.status(200).json(payload);
};

module.exports._test = { mergeCatalogResults };
