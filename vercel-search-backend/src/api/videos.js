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
function readAttr(xml, tag, attr) {
  const match = xml.match(new RegExp("<" + tag + "\\b[^>]*\\b" + attr + "=\"([^\"]+)\"", "i"));
  return match ? decodeXml(match[1]) : "";
}
function parseYouTube(xml) {
  return [...xml.matchAll(/<entry>([\s\S]*?)<\/entry>/gi)].slice(0, 20).map(match => {
    const entry = match[1];
    const id = readTag(entry, "yt:videoId");
    const link = readAttr(entry, "link", "href");
    const thumbnail = readAttr(entry, "media:thumbnail", "url") || (id ? "https://i.ytimg.com/vi/" + id + "/hqdefault.jpg" : "");
    return { title: readTag(entry, "title"), url: link || (id ? "https://www.youtube.com/watch?v=" + id : ""), thumbnail, source: readTag(entry, "name") || "YouTube", publishedAt: readTag(entry, "published"), videoId: id };
  }).filter(item => item.title && item.url && item.videoId);
}
function parseBing(xml) {
  return [...xml.matchAll(/<item>([\s\S]*?)<\/item>/gi)].slice(0, 20).map(match => {
    const item = match[1];
    return {
      title: readTag(item, "title"),
      url: readTag(item, "link"),
      thumbnail: readAttr(item, "media:thumbnail", "url") || readAttr(item, "thumbnail", "url"),
      source: "Bing Video Search",
      publishedAt: readTag(item, "pubDate"),
      summary: readTag(item, "description")
    };
  }).filter(item => item.title && /^https?:\/\//i.test(item.url));
}
module.exports = async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
  res.setHeader("Cache-Control", "s-maxage=300, stale-while-revalidate=600");
  if (req.method === "OPTIONS") return res.status(204).end();
  if (req.method !== "GET") return res.status(405).json({ error: "Method not allowed" });
  const query = String(req.query.q || "").trim().slice(0, 160);
  if (!query) return res.status(400).json({ error: "A search query is required" });
  const providers = [
    { name: "Bing Video Search", url: "https://www.bing.com/videos/search?format=rss&q=" + encodeURIComponent(query), parse: parseBing },
    { name: "YouTube public search feed", url: "https://www.youtube.com/feeds/videos.xml?search_query=" + encodeURIComponent(query), parse: parseYouTube }
  ];
  for (const provider of providers) {
    try {
      const response = await fetch(provider.url, {
        headers: { "User-Agent": "Mozilla/5.0 (compatible; EverythingHubVideoSearch/1.0)", "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml" },
        signal: AbortSignal.timeout(10000)
      });
      if (!response.ok) continue;
      const xml = await response.text();
      const items = provider.parse(xml);
      if (items.length) return res.status(200).json({
        status: "ok",
        query,
        provider: provider.name,
        updatedAt: new Date().toISOString(),
        count: items.length,
        items
      });
    } catch (error) {
      continue;
    }
  }
  return res.status(502).json({
    status: "unavailable",
    query,
    message: "Video sources are temporarily unavailable. Try another search."
  });
};
