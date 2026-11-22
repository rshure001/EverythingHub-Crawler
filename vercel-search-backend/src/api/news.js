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
  const match = xml.match(new RegExp("<" + name + "(?:\\s[^>]*)?>([\\s\\S]*?)</" + name + ">", "i"));
  return match ? decodeXml(match[1]) : "";
}

const categories = {
  "for-you": "top stories",
  world: "world news",
  technology: "technology news",
  business: "business news",
  science: "science news"
};

module.exports = async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
  res.setHeader("Cache-Control", "s-maxage=300, stale-while-revalidate=600");
  if (req.method === "OPTIONS") return res.status(204).end();
  if (req.method !== "GET") return res.status(405).json({ error: "Method not allowed" });

  const category = String(req.query.category || "for-you").toLowerCase();
  const query = categories[category];
  if (!query) return res.status(400).json({ error: "Unsupported news category" });

  try {
    const url = new URL("https://news.google.com/rss/search");
    url.searchParams.set("q", query);
    url.searchParams.set("hl", "en-US");
    url.searchParams.set("gl", "US");
    url.searchParams.set("ceid", "US:en");
    const response = await robotsFetch(url, {
      signal: AbortSignal.timeout(12000)
    }, { userAgent: "EverythingHubNews/1.0" });
    if (!response.ok) throw new Error("News provider HTTP " + response.status);
    const xml = await response.text();
    if (Buffer.byteLength(xml, "utf8") > 2_000_000) throw new Error("News provider response too large");
    const rawItems = [...xml.matchAll(/<item>([\s\S]*?)<\/item>/gi)]
      .map(match => {
        const item = match[1];
        const title = readTag(item, "title");
        const link = readTag(item, "link");
        const source = readTag(item, "source") || "News source";
        const published = readTag(item, "pubDate");
        const summary = readTag(item, "description");
        return {
          title,
          url: link,
          source,
          publishedAt: published && !Number.isNaN(Date.parse(published)) ? new Date(published).toISOString() : null,
          summary: summary.slice(0, 360)
        };
      })
      .filter(item => item.title && /^https?:\/\//i.test(item.url));
    const seenTitles = new Set();
    const items = rawItems.filter(item => {
      const key = item.title.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
      if (!key || seenTitles.has(key)) return false;
      seenTitles.add(key);
      return true;
    }).slice(0, 10);

    if (!items.length) throw new Error("The news provider returned no usable stories");
    return res.status(200).json({
      status: "ok",
      category,
      query,
      updatedAt: new Date().toISOString(),
      count: items.length,
      items
    });
  } catch (error) {
    return res.status(502).json({
      status: "unavailable",
      category,
      message: "News sources are temporarily unavailable. Try Refresh."
    });
  }
};

module.exports._test = { categories, decodeXml, readTag };
