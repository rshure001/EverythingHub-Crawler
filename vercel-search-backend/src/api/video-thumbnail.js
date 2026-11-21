module.exports = async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Cache-Control", "public, s-maxage=86400, stale-while-revalidate=604800");
  if (req.method !== "GET") return res.status(405).json({ error: "Method not allowed" });
  const id = String(req.query.id || "");
  if (!/^[A-Za-z0-9_-]{6,20}$/.test(id)) return res.status(400).json({ error: "Invalid video ID" });
  try {
    const response = await fetch("https://i.ytimg.com/vi/" + id + "/hqdefault.jpg", {
      headers: { "User-Agent": "Mozilla/5.0 (compatible; EverythingHub/1.0)" },
      signal: AbortSignal.timeout(10000)
    });
    if (!response.ok) return res.status(404).json({ error: "Thumbnail unavailable" });
    const bytes = Buffer.from(await response.arrayBuffer());
    res.setHeader("Content-Type", response.headers.get("content-type") || "image/jpeg");
    res.setHeader("Content-Length", String(bytes.length));
    return res.status(200).send(bytes);
  } catch (error) {
    return res.status(502).json({ error: "Thumbnail provider unavailable" });
  }
};
