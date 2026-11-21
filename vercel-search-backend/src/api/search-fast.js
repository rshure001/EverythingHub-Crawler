function decodeXml(value) {
  return String(value || "")
    .replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1")
    .replace(/<[^>]+>/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/\s+/g, " ")
    .trim();
}
function readTag(xml, name) {
  const match = xml.match(new RegExp("<" + name + "(?:\\s[^>]*)?>([\\s\\S]*?)</" + name + ">", "i"));
  return match ? decodeXml(match[1]) : "";
}
function parseRss(xml, sourceFallback) {
  const raw = [...xml.matchAll(/<item>([\s\S]*?)<\/item>/gi)].map(match => {
    const item = match[1];
    const title = readTag(item, "title");
    const url = readTag(item, "link");
    const summary = readTag(item, "description");
    const source = readTag(item, "source") || sourceFallback;
    const published = readTag(item, "pubDate");
    return {
      title,
      url,
      snippet: summary.slice(0, 420),
      source,
      publishedAt: published && !Number.isNaN(Date.parse(published)) ? new Date(published).toISOString() : null
    };
  }).filter(item => item.title && /^https?:\/\//i.test(item.url));
  const seen = new Set();
  return raw.filter(item => {
    const key = item.url || item.title.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).slice(0, 12);
}
async function getFeed(url, source) {
  const response = await fetch(url, {
    headers: { "User-Agent": "Mozilla/5.0 (compatible; EverythingHubSearch/1.0)", "Accept": "application/rss+xml, application/xml, text/xml" },
    signal: AbortSignal.timeout(8000)
  });
  if (!response.ok) throw new Error(source + " returned HTTP " + response.status);
  return parseRss(await response.text(), source);
}
module.exports = async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
  res.setHeader("Cache-Control", "no-store");
  if (req.method === "OPTIONS") return res.status(204).end();
  if (req.method !== "GET") return res.status(405).json({ error: "Method not allowed" });
  const query = String(req.query.q || "").trim().slice(0, 160);
  const lane = String(req.query.lane || "web").toLowerCase();
  if (!query) return res.status(400).json({ error: "A search query is required" });
  const googleNewsUrl = "https://news.google.com/rss/search?q=" + encodeURIComponent(query) + "&hl=en-US&gl=US&ceid=US:en";
  const bingUrl = "https://www.bing.com/search?format=rss&q=" + encodeURIComponent(query);
  const providers = lane === "news"
    ? [{ url: googleNewsUrl, name: "Google News" }, { url: bingUrl, name: "Bing Web Search" }]
    : [{ url: bingUrl, name: "Bing Web Search" }, { url: googleNewsUrl, name: "Google News" }];
  for (const provider of providers) {
    try {
      const results = await getFeed(provider.url, provider.name);
      if (results.length) return res.status(200).json({
        status: "ok",
        query,
        lane,
        count: results.length,
        results,
        providers: [provider.name],
        fallback: true,
        updatedAt: new Date().toISOString()
      });
    } catch (error) {
      continue;
    }
  }
  return res.status(502).json({
    status: "unavailable",
    query,
    lane,
    count: 0,
    results: [],
    providers: [],
    message: "Public web search sources are temporarily unavailable."
  });
};
