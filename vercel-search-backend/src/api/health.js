'use strict';
module.exports = function handler(req, res) {
  res.setHeader('Cache-Control', 'no-store');
  res.setHeader('X-Content-Type-Options', 'nosniff');
  return res.status(200).json({
    status: 'ok',
    service: 'everythinghub-independent-search',
    storage: 'versioned-public-search-shards',
    sources: ['Everything Hub Crawler', 'Bing Web Search'],
    database_required: false,
    catalog_integrity: 'per-shard-sha256',
    pagination: 'non-overlapping'
  });
};
