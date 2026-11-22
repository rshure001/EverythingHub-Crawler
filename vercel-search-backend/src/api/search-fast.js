"use strict";

const news = require("./news")._test;
const CACHE_MS = 5 * 60 * 1000;
const cache = new Map();
const STOP_WORDS = new Set([
  "a","an","and","are","as","at","be","by","for","from","in","into","is","it",
  "latest","new","news","of","on","or","the","this","today","top","to","up",
  "what","when","where","who","why","with","world"
]);

function queryTerms(value) {
  return Array.from(new Set(String(value || "").toLowerCase().match(/[a-z0-9]{2,}/g) || []))
    .filter(term => !STOP_WORDS.has(term))
    .slice(0, 8);
}

function selectFeeds(query) {
  const value = String(query || "").toLowerCase();
  let categories;
  if (/\b(business|economy|economic|finance|financial|market|bank|banks|jobs|inflation|fed)\b/.test(value)) {
    categories = ["business", "world"];
  } else if (/\b(nasa|space|science|scientific|climate|ocean|weather|research)\b/.test(value)) {
    categories = ["science", "technology"];
  } else if (/\b(technology|tech|ai|cyber|computer|software)\b/.test(value)) {
    categories = ["technology", "science"];
  } else {
    categories = ["for-you", "world"];
  }
  const seen = new Set();
  return categories.flatMap(category => news.NEWS_FEEDS[category] || [])
    .filter(feed => {
      if (seen.has(feed.url)) return false;
      seen.add(feed.url);
      return true;
    });
}

function searchNewsItems(items, query) {
  const terms = queryTerms(query);
  const matching = terms.length
    ? items.filter(item => {
        const text = [item.title, item.summary, item.source].join(" ").toLowerCase();
        return terms.some(term => text.includes(term));
      })
    : items;
  const queryMatchFallback = terms.length > 0 && matching.length === 0;
  const selected = (queryMatchFallback ? items : matching).slice(0, 12);
  return {
    queryMatchFallback,
    results: selected.map(item => ({
      title: item.title,
      url: item.url,
      snippet: item.summary || "",
      source: item.source,
      publishedAt: item.publishedAt || null
    }))
  };
}

module.exports = async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
  res.setHeader("Cache-Control", "s-maxage=300, stale-while-revalidate=600");
  if (req.method === "OPTIONS") return res.status(204).end();
  if (req.method !== "GET") return res.status(405).json({ error: "Method not allowed" });

  const query = String(req.query.q || "").trim().slice(0, 160);
  const lane = String(req.query.lane || "web").toLowerCase();
  if (!query) return res.status(400).json({ error: "A search query is required" });
  if (lane !== "news") return res.status(503).json({
    status: "unavailable",
    query,
    lane,
    count: 0,
    results: [],
    providers: [],
    message: "External web fallback is disabled unless a source explicitly permits the requested path. Use the independent Everything Hub index."
  });

  const cacheKey = query.toLowerCase();
  const cached = cache.get(cacheKey);
  if (cached && Date.now() - cached.cachedAt < CACHE_MS) {
    return res.status(200).json({ ...cached.payload, cache: "memory" });
  }

  const feeds = selectFeeds(query);
  const settled = await Promise.allSettled(feeds.map(feed => news.fetchFeed(feed, 8000)));
  const successful = settled.filter(result => result.status === "fulfilled").map(result => result.value);
  const merged = news.mergeFeeds(successful, 60);
  const searched = searchNewsItems(merged, query);
  if (!searched.results.length) {
    return res.status(502).json({
      status: "unavailable",
      query,
      lane,
      count: 0,
      results: [],
      providers: [],
      message: "No robots-verified public news feed is currently available."
    });
  }

  const payload = {
    status: "ok",
    query,
    lane,
    count: searched.results.length,
    results: searched.results,
    providers: successful.map(result => result.source),
    fallback: true,
    queryMatchFallback: searched.queryMatchFallback,
    note: searched.queryMatchFallback ? "No exact query match in the current permitted feeds; showing the latest verified stories." : null,
    updatedAt: new Date().toISOString()
  };
  cache.set(cacheKey, { cachedAt: Date.now(), payload });
  return res.status(200).json(payload);
};

module.exports._test = { queryTerms, selectFeeds, searchNewsItems };
