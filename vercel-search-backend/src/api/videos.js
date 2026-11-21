"use strict";

const API_HOST = "commons.wikimedia.org";
const API_BASE = "https://commons.wikimedia.org/w/api.php";
const ASSET_HOST = "upload.wikimedia.org";
const USER_AGENT = "EverythingHubVideoSearch/1.0 (https://github.com/rshure001/EverythingHub-Crawler; metadata only)";
const DEFAULT_MIN_INTERVAL_MS = 1000;
const ROBOTS_TTL_MS = 60 * 60 * 1000;
const MAX_ROBOTS_BYTES = 512000;
const MAX_API_BYTES = 4 * 1024 * 1024;
const MAX_RESULTS = 20;
const MAX_REQUESTS_PER_HOST_PER_HOUR = 120;
const WINDOW_MS = 60 * 60 * 1000;
const robotsCache = new Map();
const hostQueues = new Map();
const lastRequestAt = new Map();
const requestWindows = new Map();
const resultCache = new Map();
let minIntervalMs = DEFAULT_MIN_INTERVAL_MS;

function safeText(value, limit = 500) {
  return String(value || "")
    .replace(/<[^>]*>/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, "\"")
    .replace(/&#39;|&apos;/g, "'")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/[\u0000-\u001f\u007f]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, limit);
}

function escapeRegex(value) {
  return value.replace(/[.*+?^\${}()|[\]\\]/g, "\\$&");
}

function robotsPatternMatches(pattern, target) {
  if (!pattern) return false;
  const anchored = pattern.endsWith("$");
  const body = anchored ? pattern.slice(0, -1) : pattern;
  const escaped = body.split("*").map(escapeRegex).join(".*");
  return new RegExp("^" + escaped + (anchored ? "$" : "")).test(target);
}

function parseRobots(text, userAgent = USER_AGENT) {
  const groups = [];
  let current = null;
  for (const rawLine of String(text || "").split(/\r?\n/)) {
    const line = rawLine.split("#", 1)[0].trim();
    if (!line) continue;
    const separator = line.indexOf(":");
    if (separator < 1) continue;
    const key = line.slice(0, separator).trim().toLowerCase();
    const value = line.slice(separator + 1).trim();
    if (key === "user-agent") {
      if (!current || current.rules.length) {
        if (current) groups.push(current);
        current = { agents: [], rules: [], crawlDelayMs: 0 };
      }
      current.agents.push(value.toLowerCase());
      continue;
    }
    if (!current) continue;
    if (key === "allow" || key === "disallow") {
      if (value) current.rules.push({ allow: key === "allow", pattern: value });
    } else if (key === "crawl-delay") {
      const seconds = Number(value);
      if (Number.isFinite(seconds) && seconds >= 0 && seconds <= 120) {
        current.crawlDelayMs = Math.max(current.crawlDelayMs, seconds * 1000);
      }
    }
  }
  if (current) groups.push(current);

  const token = String(userAgent || "").split(/[ /]/, 1)[0].toLowerCase();
  const exactGroups = groups.filter(group => group.agents.some(agent => agent !== "*" && token.includes(agent)));
  const selected = exactGroups.length ? exactGroups : groups.filter(group => group.agents.includes("*"));
  const rules = selected.flatMap(group => group.rules);
  const crawlDelayMs = selected.reduce((maximum, group) => Math.max(maximum, group.crawlDelayMs), 0);

  return {
    delayMs: Math.max(minIntervalMs, crawlDelayMs),
    allows(url) {
      let parsed;
      try { parsed = new URL(url); } catch { return false; }
      const target = parsed.pathname + parsed.search;
      let best = null;
      for (const rule of rules) {
        if (!robotsPatternMatches(rule.pattern, target)) continue;
        const specificity = rule.pattern.replace(/\*/g, "").replace(/\$$/, "").length;
        if (!best || specificity > best.specificity ||
            (specificity === best.specificity && rule.allow && !best.allow)) {
          best = { allow: rule.allow, specificity };
        }
      }
      return !best || best.allow;
    }
  };
}

async function pacedFetch(url, options = {}, delayMs = minIntervalMs) {
  const host = new URL(url).hostname.toLowerCase();
  const previous = hostQueues.get(host) || Promise.resolve();
  let release;
  const lock = new Promise(resolve => { release = resolve; });
  hostQueues.set(host, previous.then(() => lock));
  await previous;
  try {
    const wait = Math.max(0, delayMs - (Date.now() - (lastRequestAt.get(host) || 0)));
    if (wait) await new Promise(resolve => setTimeout(resolve, wait));
    const now = Date.now();
    let window = requestWindows.get(host);
    if (!window || now - window.startedAt >= WINDOW_MS) window = { startedAt: now, count: 0 };
    if (window.count >= MAX_REQUESTS_PER_HOST_PER_HOUR) throw new Error("provider_request_budget_exhausted");
    window.count += 1;
    requestWindows.set(host, window);
    return await fetch(url, options);
  } finally {
    lastRequestAt.set(host, Date.now());
    release();
  }
}

async function loadRobots(host) {
  const now = Date.now();
  const cached = robotsCache.get(host);
  if (cached && cached.expiresAt > now) return cached.policy;

  const robotsUrl = "https://" + host + "/robots.txt";
  const response = await pacedFetch(robotsUrl, {
    headers: { "User-Agent": USER_AGENT, "Accept": "text/plain" },
    signal: AbortSignal.timeout(8000),
    redirect: "follow"
  }, minIntervalMs);
  const finalUrl = response.url || robotsUrl;
  if (new URL(finalUrl).hostname.toLowerCase() !== host) throw new Error("robots_redirect_host_changed:" + host);

  let policy;
  if (response.status === 404 || response.status === 410) {
    policy = parseRobots("", USER_AGENT);
  } else {
    if (!response.ok) throw new Error("robots_unverified:" + host + ":" + response.status);
    const text = await response.text();
    if (text.length > MAX_ROBOTS_BYTES) throw new Error("robots_response_too_large:" + host);
    policy = parseRobots(text, USER_AGENT);
  }
  robotsCache.set(host, { policy, expiresAt: now + ROBOTS_TTL_MS });
  return policy;
}

async function ensureAllowed(url) {
  const parsed = new URL(url);
  const policy = await loadRobots(parsed.hostname.toLowerCase());
  return { allowed: policy.allows(url), delayMs: policy.delayMs };
}

function extractLicenseUrl(value) {
  const raw = String(value || "");
  const href = raw.match(/href=["']([^"']+)["']/i);
  return safeText(href ? href[1] : raw, 500);
}

function isAllowedCommonsLicense(license, licenseUrl, attributionRequired, creator) {
  const normalized = String(license || "").trim();
  const publicDomain = normalized.toLowerCase().startsWith("public domain");
  const cc0 = /^cc0(?:\s|$)/i.test(normalized);
  const ccByMatch = normalized.match(/^cc by(-sa)?\s+(1\.0|2\.0|2\.5|3\.0|4\.0)$/i);
  let canonicalLicenseUrl = false;
  if (ccByMatch) {
    try {
      const parsed = new URL(licenseUrl);
      const kind = ccByMatch[1] ? "by-sa" : "by";
      const expectedPath = "/licenses/" + kind + "/" + ccByMatch[2] + "/";
      canonicalLicenseUrl = parsed.protocol === "https:" &&
        parsed.hostname === "creativecommons.org" &&
        parsed.pathname.replace(/\/+$/, "/") === expectedPath;
    } catch {
      canonicalLicenseUrl = false;
    }
  }
  const licenseValid = publicDomain || cc0 || (ccByMatch !== null && canonicalLicenseUrl);
  const attributionValid = ccByMatch
    ? String(creator || "").trim().length > 0
    : attributionRequired === false || (attributionRequired === true && String(creator || "").trim().length > 0);
  return licenseValid && attributionValid;
}

function normalizeCommonsItem(page, query) {
  if (!page || typeof page !== "object") return null;
  const info = Array.isArray(page.imageinfo) ? page.imageinfo[0] : null;
  if (!info || !String(info.mime || "").toLowerCase().startsWith("video/")) return null;

  const fileTitle = safeText(page.title, 500);
  const title = fileTitle.replace(/^File:/i, "").trim();
  if (!title) return null;

  const stopWords = new Set(["the", "and", "for", "with", "from", "into", "about", "what", "where", "when", "how", "best", "top"]);
  const terms = String(query || "").toLowerCase().split(/\s+/).filter(word => word.length > 2 && !stopWords.has(word));
  const titleWords = new Set(title.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean));
  const matchedTerms = terms.filter(term => titleWords.has(term));
  if (terms.length && matchedTerms.length < Math.min(2, terms.length)) return null;
  if (/chapati|egg|food|eat|recipe|dance|dancer|song|music|robbery|robber|theft|stolen|suspect|arrested|crime/i.test(title.toLowerCase())) return null;

  let thumbnail;
  try {
    const parsed = new URL(info.thumburl || "");
    if (parsed.protocol !== "https:" || parsed.hostname.toLowerCase() !== ASSET_HOST) return null;
    thumbnail = parsed.href;
  } catch {
    return null;
  }

  const ext = info.extmetadata || {};
  const metadata = key => safeText(ext[key] && ext[key].value, 800);
  const license = metadata("LicenseShortName");
  const licenseUrl = extractLicenseUrl(ext.LicenseUrl && ext.LicenseUrl.value);
  const creator = metadata("Artist");
  const credit = metadata("Credit");
  const attributionText = metadata("AttributionRequired");
  const attributionRequired = attributionText ? attributionText.toLowerCase() === "true" : null;
  const creatorForPolicy = creator || credit;
  if (!isAllowedCommonsLicense(license, licenseUrl, attributionRequired, creatorForPolicy)) return null;

  const pageKey = String(page.pageid || fileTitle);
  return {
    title,
    url: "https://commons.wikimedia.org/wiki/" + encodeURIComponent(fileTitle.replace(/ /g, "_")),
    thumbnail,
    source: "Wikimedia Commons",
    license,
    licenseUrl: licenseUrl || "https://commons.wikimedia.org/wiki/Commons:Reusing_content_outside_Wikimedia",
    creator,
    credit,
    attributionRequired,
    sourceId: pageKey,
    attributionUrl: "https://commons.wikimedia.org/wiki/Commons:Reusing_content_outside_Wikimedia",
    rightsNote: "Only public-domain, CC0, CC BY and CC BY-SA media with required attribution are shown."
  };
}

async function fetchCommons(query) {
  const key = String(query || "").trim().toLowerCase();
  const cached = resultCache.get(key);
  if (cached && cached.expiresAt > Date.now()) return cached.items;

  const params = new URLSearchParams({
    action: "query",
    generator: "search",
    maxlag: "5",
    gsrsearch: query + " filetype:video",
    gsrnamespace: "6",
    gsrlimit: "50",
    prop: "imageinfo",
    iiprop: "url|mime|extmetadata",
    iiextmetadatafilter: "Artist|Credit|LicenseShortName|LicenseUrl|AttributionRequired",
    iiurlwidth: "640",
    format: "json",
    origin: "*"
  });
  const url = API_BASE + "?" + params.toString();
  const apiPolicy = await ensureAllowed(url);
  if (!apiPolicy.allowed) throw new Error("robots_disallows_commons_api");

  const response = await pacedFetch(url, {
    headers: { "User-Agent": USER_AGENT, "Accept": "application/json" },
    signal: AbortSignal.timeout(12000),
    redirect: "follow"
  }, apiPolicy.delayMs);
  const finalUrl = response.url || url;
  if (new URL(finalUrl).hostname.toLowerCase() !== API_HOST) throw new Error("commons_api_redirect_host_changed");
  if (!response.ok) throw new Error("commons_api_http_" + response.status);

  const text = await response.text();
  if (text.length > MAX_API_BYTES) throw new Error("commons_api_response_too_large");
  const payload = JSON.parse(text);
  const pages = Object.values(payload && payload.query && payload.query.pages || {});
  const items = [];
  const seen = new Set();
  for (const page of pages) {
    const item = normalizeCommonsItem(page, query);
    if (!item || seen.has(item.sourceId)) continue;
    seen.add(item.sourceId);
    items.push(item);
    if (items.length >= MAX_RESULTS) break;
  }
  resultCache.set(key, { items, expiresAt: Date.now() + 5 * 60 * 1000 });
  return items;
}

module.exports = async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
  res.setHeader("Cache-Control", "s-maxage=300, stale-while-revalidate=600");
  if (req.method === "OPTIONS") return res.status(204).end();
  if (req.method !== "GET") return res.status(405).json({ error: "Method not allowed" });

  const query = safeText(req.query && req.query.q, 160);
  if (!query) return res.status(400).json({ error: "A search query is required" });

  try {
    const items = await fetchCommons(query);
    return res.status(200).json({
      status: "ok",
      query,
      provider: "Wikimedia Commons",
      updatedAt: new Date().toISOString(),
      count: items.length,
      items,
      rightsNote: "Only public-domain, CC0, CC BY and CC BY-SA videos with required attribution are shown. Video files are not downloaded or re-hosted."
    });
  } catch (error) {
    const message = String(error && error.message || "");
    const robotsFailure = message.includes("robots") || message.includes("redirect_host_changed");
    return res.status(robotsFailure ? 503 : 502).json({
      status: "unavailable",
      query,
      message: robotsFailure
        ? "Wikimedia video search is unavailable because source access could not be safely verified."
        : "Wikimedia video search is temporarily unavailable. Try again shortly."
    });
  }
};

module.exports._test = {
  parseRobots,
  normalizeCommonsItem,
  isAllowedCommonsLicense,
  fetchCommons,
  resetForTests() {
    robotsCache.clear();
    hostQueues.clear();
    lastRequestAt.clear();
    requestWindows.clear();
    resultCache.clear();
    minIntervalMs = 0;
  }
};
