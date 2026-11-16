import "jsr:@supabase/functions-js/edge-runtime.d.ts";

const cors = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "Content-Type, Accept, apikey",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Content-Type": "application/json; charset=utf-8",
  "Cache-Control": "public, max-age=180, s-maxage=180"
};

const decode = (s: string) => s
  .replace(/&amp;/g, "&").replace(/&quot;/g, '"')
  .replace(/&#39;|&apos;/g, "'").replace(/&lt;/g, "<")
  .replace(/&gt;/g, ">").replace(/<!\[CDATA\[|\]\]>/g, "")
  .trim();

const plain = (value: unknown) => decode(String(value ?? "").replace(/<[^>]*>/g, " "))
  .replace(/\s+/g, " ").trim();

const tag = (s: string, name: string) => {
  const re = new RegExp("<" + name + "(?:\\s[^>]*)?>([\\s\\S]*?)</" + name + ">", "i");
  const m = s.match(re);
  return m ? decode(m[1]) : "";
};

const attrs = (s: string) => {
  const out: Record<string, string> = {};
  for (const m of s.matchAll(/([\w:-]+)=["']([^"']+)["']/g)) out[m[1].toLowerCase()] = decode(m[2]);
  return out;
};

async function bing(q: string, type: "image" | "video") {
  const endpoint = type === "video" ? "videos" : "images";
  const response = await fetch("https://www.bing.com/" + endpoint + "/search?q=" + encodeURIComponent(q) + "&format=rss", {
    headers: { "Accept": "application/rss+xml, application/xml, text/xml, */*", "User-Agent": "Mozilla/5.0 (compatible; EverythingHub/1.0)" },
    signal: AbortSignal.timeout(9000)
  });
  if (!response.ok) return [];
  const xml = await response.text();
  const items = [...xml.matchAll(/<item\b[^>]*>([\s\S]*?)<\/item>/gi)].slice(0, 30);
  return items.flatMap((match) => {
    const block = match[1];
    const title = tag(block, "title");
    const pageUrl = tag(block, "link");
    const description = tag(block, "description");
    const mediaTags = [...block.matchAll(/<(?:media:content|media:thumbnail|enclosure)\b[^>]*>/gi)].map((x) => x[0]);
    const parsed = mediaTags.map((raw) => ({ raw, ...attrs(raw) }));
    const image = parsed.find((x) => (x.type || "").startsWith("image/") || x.raw.toLowerCase().includes("thumbnail"));
    const video = parsed.find((x) => (x.type || "").startsWith("video/"));
    const anyContent = parsed.find((x) => (x.url || "").startsWith("http"));
    const thumb = image?.url || "";
    const directVideo = video?.url || (type === "video" && anyContent?.type?.startsWith("video/") ? anyContent.url : "");
    if (!title || !pageUrl.startsWith("http")) return [];
    if (type === "image" && !thumb && !anyContent?.url) return [];
    return [{
      title,
      pageUrl,
      imageUrl: type === "image" ? (thumb || anyContent?.url || "") : thumb,
      videoUrl: type === "video" ? directVideo : "",
      thumbnailUrl: thumb,
      description: description.replace(/<[^>]+>/g, " ").slice(0, 320),
      mediaType: type,
      source: type === "video" ? "Bing Videos" : "Bing Images",
      license: null,
      licenseUrl: null,
      creator: null,
      credit: null,
      attributionRequired: null,
      usageRights: "Unverified — check the source page terms before reuse"
    }];
  });
}

async function commons(q: string, type: "image" | "video") {
  const params = new URLSearchParams({
    action: "query", generator: "search", maxlag: "5",
    gsrsearch: q + (type === "video" ? " filetype:video" : " filetype:bitmap"),
    gsrnamespace: "6", gsrlimit: "24", prop: "imageinfo",
    iiprop: "url|mime|extmetadata",
    iiextmetadatafilter: "Artist|Credit|LicenseShortName|LicenseUrl|AttributionRequired",
    iiurlwidth: "640", format: "json", origin: "*"
  });
  const response = await fetch("https://commons.wikimedia.org/w/api.php?" + params, {
    headers: { "Accept": "application/json", "User-Agent": "EverythingHub/1.0 (https://github.com/rshure001/EverythingHub-Crawler; media discovery)" },
    signal: AbortSignal.timeout(9000)
  });
  if (!response.ok) return [];
  const body = await response.json();
  const stopWords = new Set(["the","and","for","with","from","into","about","what","where","when","how","best","top"]);
  const terms = q.toLowerCase().split(/\s+/).filter((word) => word.length > 2 && !stopWords.has(word));
  return Object.values(body?.query?.pages || {}).flatMap((page: any) => {
    const info = page.imageinfo?.[0];
    const url = info?.url || "";
    const thumb = info?.thumburl || "";
    const ext = info?.extmetadata || {};
    const meta = (key: string) => plain(ext[key]?.value || "");
    const license = meta("LicenseShortName");
    const rawLicenseUrl = String(ext.LicenseUrl?.value || "");
    const licenseUrl = rawLicenseUrl.match(/href=["']([^"']+)["']/i)?.[1] || plain(rawLicenseUrl);
    const creator = meta("Artist");
    const credit = meta("Credit");
    const attributionText = meta("AttributionRequired");
    const attributionRequired = attributionText ? attributionText.toLowerCase() === "true" : null;
    const isVideo = String(info?.mime || "").startsWith("video/");
    if (!url.startsWith("http") || (type === "video" && !isVideo) || (type === "image" && isVideo)) return [];
    const title = String(page.title || "").replace(/^File:/, "");
    const searchableTitle = title.toLowerCase();
    // Commons search can return weak semantic matches; never show a result with no query-term match.
    const titleWords = new Set(searchableTitle.split(/[^a-z0-9]+/).filter(Boolean));
    const matchedTerms = terms.filter((term) => titleWords.has(term));
    if (terms.length && matchedTerms.length < Math.min(2, terms.length)) return [];
    if (/chapati|egg|food|eat|recipe|dance|dancer|song|music|댄스|교실|ugandan|mbarara|bitten off/i.test(searchableTitle)) return [];
    if (type === "video" && /robbery|robber|theft|stolen|suspect|arrested|crime/i.test(searchableTitle)) return [];
    return [{
      title,
      pageUrl: "https://commons.wikimedia.org/wiki/" + encodeURIComponent(title.replace(/ /g, "_")),
      imageUrl: type === "image" ? (thumb || url) : thumb,
      videoUrl: type === "video" ? url : "",
      thumbnailUrl: thumb,
      description: "Wikimedia Commons",
      mediaType: type,
      source: "Wikimedia Commons",
      license: license || null,
      licenseUrl: licenseUrl.startsWith("http") ? licenseUrl : null,
      creator: creator || null,
      credit: credit || null,
      attributionRequired,
      usageRights: license
        ? "Review the linked license and attribution requirements before reuse"
        : "License metadata unavailable — verify the source page before reuse"
    }];
  });
}

Deno.serve(async (request: Request) => {
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
  if (request.method !== "GET") return Response.json({ error: "GET required" }, { status: 405, headers: cors });
  const url = new URL(request.url);
  const query = (url.searchParams.get("q") || "").trim().slice(0, 120);
  const type = url.searchParams.get("type") === "video" ? "video" : "image";
  if (query.length < 2) return Response.json({
    results: [], count: 0, query, type,
    usageNote: "Bing media results are discovery links only; verify reuse rights on the source page. Wikimedia Commons results include available license and attribution metadata."
  }, { headers: cors });

  try {
    let results = await bing(query, type);
    let provider = type === "video" ? "Bing Videos" : "Bing Images";
    if (!results.length) {
      results = await commons(query, type);
      provider = "Wikimedia Commons";
    }
    return Response.json({
      results, count: results.length, query, type, provider,
      usageNote: "Bing media results are discovery links only; verify reuse rights on the source page. Wikimedia Commons results include available license and attribution metadata."
    }, { headers: cors });
  } catch {
    try {
      const results = await commons(query, type);
      return Response.json({
        results, count: results.length, query, type, provider: "Wikimedia Commons",
        usageNote: "Review each file's license and attribution requirements before reuse."
      }, { headers: cors });
    } catch {
      return Response.json({
        results: [], count: 0, query, type,
        warning: "Free media sources are temporarily unavailable",
        usageNote: "No media results are currently available. Check each original source's terms before reuse."
      }, { status: 200, headers: cors });
    }
  }
});