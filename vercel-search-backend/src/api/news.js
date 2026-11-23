"use strict";

const { robotsFetch } = require("../lib/robots");

const FEED_CACHE_TTL_MS = 5 * 60 * 1000;
const MAX_CACHED_FEEDS = 64;
const feedCache = new Map();
const feedInFlight = new Map();

function decodeXml(value) {
  return String(value || "")
    .replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1")
    .replace(/<[^>]+>/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&#(\d+);/g, (_, n) => String.fromCodePoint(Number(n)))
    .replace(/&#x([0-9a-f]+);/gi, (_, n) => String.fromCodePoint(parseInt(n, 16)))
    .replace(/\s+/g, " ")
    .trim();
}

function readTag(xml, name) {
  const match = String(xml || "").match(new RegExp("<" + name + "(?:\\s[^>]*)?>([\\s\\S]*?)</" + name + ">", "i"));
  return match ? decodeXml(match[1]) : "";
}

const NEWS_FEEDS = {
  "for-you": [
    { source: "The White House", url: "https://www.whitehouse.gov/releases/feed/" },
    { source: "NASA News Releases", url: "https://www.nasa.gov/news-release/feed/", headlinesOnly: true },
    { source: "NASA Recently Published", url: "https://www.nasa.gov/feed/", headlinesOnly: true },
    { source: "NOAA", url: "https://www.noaa.gov/rss.xml" },
    { source: "Federal Reserve", url: "https://www.federalreserve.gov/feeds/press_all.xml" }
  ],
  world: [
    { source: "U.S. Department of State — Travel Advisories", url: "https://travel.state.gov/_res/rss/TAsTWs.xml" },
    { source: "The White House", url: "https://www.whitehouse.gov/releases/feed/" },
    { source: "U.S. Department of Labor", url: "https://www.dol.gov/rss/releases.xml" }
  ],
  technology: [
    { source: "NIST", url: "https://www.nist.gov/news-events/news/rss.xml" },
    { source: "NASA News Releases", url: "https://www.nasa.gov/news-release/feed/", headlinesOnly: true },
    { source: "NASA Recently Published", url: "https://www.nasa.gov/feed/", headlinesOnly: true }
  ],
  business: [
    { source: "Federal Reserve", url: "https://www.federalreserve.gov/feeds/press_all.xml" },
    { source: "U.S. Department of Labor", url: "https://www.dol.gov/rss/releases.xml" }
  ],
  science: [
    { source: "NASA News Releases", url: "https://www.nasa.gov/news-release/feed/", headlinesOnly: true },
    { source: "NASA Recently Published", url: "https://www.nasa.gov/feed/", headlinesOnly: true },
    { source: "NOAA", url: "https://www.noaa.gov/rss.xml" },
    { source: "National Science Foundation", url: "https://www.nsf.gov/rss/rss_www_news.xml" }
  ]
};

function parseFeed(xml, feed) {
  return [...String(xml || "").matchAll(/<item(?:\s[^>]*)?>([\s\S]*?)<\/item>/gi)]
    .map(match => {
      const item = match[1];
      const title = readTag(item, "title");
      const rawLink = readTag(item, "link") || readTag(item, "guid");
      let url = "";
      try {
        const parsed = new URL(rawLink, feed.url);
        if (parsed.protocol === "https:") url = parsed.toString();
      } catch {}
      const published = readTag(item, "pubDate") || readTag(item, "dc:date") || readTag(item, "date");
      const summary = feed.headlinesOnly ? "" : (readTag(item, "description") || readTag(item, "summary") || readTag(item, "content:encoded"));
      return {
        title,
        url,
        source: readTag(item, "source") || feed.source,
        publishedAt: published && !Number.isNaN(Date.parse(published)) ? new Date(published).toISOString() : null,
        summary: summary.slice(0, 360)
      };
    })
    .filter(item => item.title && item.url);
}

function safeFeedError(error) {
  const message = String(error && error.message || "feed_unavailable");
  const http = message.match(/Feed HTTP (\d{3})/i);
  if (http) return "http_" + http[1];
  if (message.startsWith("robots_disallows_request:")) return "robots_disallowed";
  if (message.startsWith("robots_unverified:")) return "robots_unverified";
  if (message.startsWith("provider_request_budget_exhausted:")) return "rate_limited";
  if (/timeout|aborted/i.test(message)) return "timeout";
  if (message.startsWith("provider_redirect_host_changed:")) return "redirect_blocked";
  return "feed_unavailable";
}

async function fetchFeed(feed, timeoutMs = 12000) {
  const key = feed.url;
  const now = Date.now();
  const cached = feedCache.get(key);
  if (cached && now - cached.fetchedAt < FEED_CACHE_TTL_MS) {
    return { source: feed.source, items: cached.items };
  }

  const existing = feedInFlight.get(key);
  if (existing) {
    const items = await existing;
    return { source: feed.source, items };
  }

  const request = (async () => {
    const response = await robotsFetch(feed.url, {
      signal: AbortSignal.timeout(timeoutMs),
      headers: { Accept: "application/rss+xml, application/xml, text/xml" }
    }, { userAgent: "EverythingHubNews/1.0" });
    if (!response.ok) throw new Error("Feed HTTP " + response.status);
    const xml = await response.text();
    if (Buffer.byteLength(xml, "utf8") > 2_000_000) throw new Error("Feed response too large");
    const items = parseFeed(xml, feed);
    if (!items.length) throw new Error("Feed returned no usable RSS items");
    feedCache.set(key, { fetchedAt: Date.now(), items });
    if (feedCache.size > MAX_CACHED_FEEDS) {
      const oldest = [...feedCache.entries()].sort((a, b) => a[1].fetchedAt - b[1].fetchedAt)[0];
      if (oldest) feedCache.delete(oldest[0]);
    }
    return items;
  })();

  feedInFlight.set(key, request);
  try {
    const items = await request;
    return { source: feed.source, items };
  } finally {
    if (feedInFlight.get(key) === request) feedInFlight.delete(key);
  }
}

function mergeFeeds(results, limit = 10) {
  const unique = new Map();
  for (const result of results) {
    for (const item of result.items) {
      const key = item.url || item.title.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
      if (!key || unique.has(key)) continue;
      unique.set(key, item);
    }
  }
  return [...unique.values()]
    .sort((a, b) => {
      const left = a.publishedAt ? Date.parse(a.publishedAt) : 0;
      const right = b.publishedAt ? Date.parse(b.publishedAt) : 0;
      return right - left;
    })
    .slice(0, limit);
}

function resetNewsCacheForTests() {
  feedCache.clear();
  feedInFlight.clear();
}

module.exports = async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
  res.setHeader("Cache-Control", "s-maxage=300, stale-while-revalidate=300");
  if (req.method === "OPTIONS") return res.status(204).end();
  if (req.method !== "GET") return res.status(405).json({ error: "Method not allowed" });

  const category = String(req.query.category || "for-you").toLowerCase();
  const feeds = NEWS_FEEDS[category];
  if (!feeds) return res.status(400).json({ error: "Unsupported news category" });

  const settled = await Promise.allSettled(feeds.map(feed => fetchFeed(feed)));
  const successful = settled.filter(result => result.status === "fulfilled").map(result => result.value);
  const failed = settled
    .map((result, index) => result.status === "rejected"
      ? { source: feeds[index].source, code: safeFeedError(result.reason) }
      : null)
    .filter(Boolean);
  const unavailable = failed.map(item => item.source);
  const sourceErrors = Object.fromEntries(failed.map(item => [item.source, item.code]));
  if (failed.length) {
    console.warn("[news-feed-unavailable]", JSON.stringify({ category, sourceErrors }));
  }
  const items = mergeFeeds(successful);

  if (!items.length) {
    return res.status(502).json({
      status: "unavailable",
      category,
      message: "Permitted public news feeds are temporarily unavailable. Try Refresh.",
      sourcesUnavailable: unavailable,
      sourceErrors
    });
  }

  return res.status(200).json({
    status: "ok",
    category,
    query: category,
    updatedAt: new Date().toISOString(),
    count: items.length,
    sources: successful.map(result => result.source),
    sourcesUnavailable: unavailable,
    sourceErrors,
    items
  });
};

module.exports._test = { NEWS_FEEDS, decodeXml, readTag, parseFeed, fetchFeed, mergeFeeds, safeFeedError, resetNewsCacheForTests };
