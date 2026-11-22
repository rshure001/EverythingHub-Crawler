"use strict";

const { robotsFetch } = require("../lib/robots");

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
    { source: "NASA Jet Propulsion Laboratory", url: "https://www.jpl.nasa.gov/feeds/news/" },
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
    { source: "NASA Jet Propulsion Laboratory", url: "https://www.jpl.nasa.gov/feeds/news/" }
  ],
  business: [
    { source: "Federal Reserve", url: "https://www.federalreserve.gov/feeds/press_all.xml" },
    { source: "U.S. Department of Labor", url: "https://www.dol.gov/rss/releases.xml" }
  ],
  science: [
    { source: "NASA Jet Propulsion Laboratory", url: "https://www.jpl.nasa.gov/feeds/news/" },
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
      const summary = readTag(item, "description") || readTag(item, "summary") || readTag(item, "content:encoded");
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

async function fetchFeed(feed, timeoutMs = 12000) {
  const response = await robotsFetch(feed.url, {
    signal: AbortSignal.timeout(timeoutMs),
    headers: { Accept: "application/rss+xml, application/xml, text/xml" }
  }, { userAgent: "EverythingHubNews/1.0" });
  if (!response.ok) throw new Error("Feed HTTP " + response.status);
  const xml = await response.text();
  if (Buffer.byteLength(xml, "utf8") > 2_000_000) throw new Error("Feed response too large");
  const items = parseFeed(xml, feed);
  if (!items.length) throw new Error("Feed returned no usable RSS items");
  return { source: feed.source, items };
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

module.exports = async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
  res.setHeader("Cache-Control", "s-maxage=600, stale-while-revalidate=1800");
  if (req.method === "OPTIONS") return res.status(204).end();
  if (req.method !== "GET") return res.status(405).json({ error: "Method not allowed" });

  const category = String(req.query.category || "for-you").toLowerCase();
  const feeds = NEWS_FEEDS[category];
  if (!feeds) return res.status(400).json({ error: "Unsupported news category" });

  const settled = await Promise.allSettled(feeds.map(fetchFeed));
  const successful = settled.filter(result => result.status === "fulfilled").map(result => result.value);
  const unavailable = settled
    .map((result, index) => result.status === "rejected" ? feeds[index].source : null)
    .filter(Boolean);
  const items = mergeFeeds(successful);

  if (!items.length) {
    return res.status(502).json({
      status: "unavailable",
      category,
      message: "Permitted public news feeds are temporarily unavailable. Try Refresh.",
      sourcesUnavailable: unavailable
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
    items
  });
};

module.exports._test = { NEWS_FEEDS, decodeXml, readTag, parseFeed, fetchFeed, mergeFeeds };
