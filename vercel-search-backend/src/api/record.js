'use strict';
const { loadPageDerivedCatalogs, resolveRecordRelatedCatalog } = require('../lib/page-derived-catalogs');

const MAX_LIMIT = 50;
const MAX_OFFSET = 5000;

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
  const recordCatalogId = String(params.get('record_catalog_id') || '').trim();
  if (!recordCatalogId || recordCatalogId.length > 160) {
    return res.status(400).json({ error: 'record_catalog_id_required' });
  }
  const limit = Number(params.get('limit') || 20);
  const offset = Number(params.get('offset') || 0);
  if (!Number.isInteger(limit) || limit < 1 || limit > MAX_LIMIT ||
      !Number.isInteger(offset) || offset < 0 || offset > MAX_OFFSET) {
    return res.status(400).json({ error: 'pagination_out_of_range' });
  }

  try {
    const network = await loadPageDerivedCatalogs();
    const result = resolveRecordRelatedCatalog(recordCatalogId, network, limit, offset);
    if (!result) return res.status(404).json({ error: 'record_catalog_not_found' });
    return res.status(200).json({
      status: 'candidate-preview',
      preview_only: true,
      generated_at: network.generatedAt || null,
      analysis_version: network.analysisVersion || null,
      source_index_pages: network.sourceIndexPages || 0,
      ...result
    });
  } catch (error) {
    const code = String(error && error.message || 'record_catalog_unavailable')
      .split(':', 1)[0].replace(/[^a-z0-9_-]/gi, '_').slice(0, 80);
    console.warn('[record-catalog-load-failed]', code || 'record_catalog_unavailable');
    return res.status(503).json({ error: 'record_catalog_temporarily_unavailable' });
  }
};
