"use strict";

const API_HOST = "images-api.nasa.gov";
const API_BASE = "https://images-api.nasa.gov/search";
const ASSET_HOSTS = new Set(["images-assets.nasa.gov", "images.nasa.gov"]);
const USER_AGENT = "EverythingHubVideoSearch/1.0 (+https://github.com/rshure001/EverythingHub-Crawler)";
const DEFAULT_MIN_INTERVAL_MS = 2000;
const ROBOTS_TTL_MS = 60 * 60 * 1000;
const MAX_ROBOTS_BYTES = 512000;
const MAX_API_BYTES = 4 * 1024 * 1024;
const MAX_RESULTS = 20;
const MAX_REQUESTS_PER_HOST_PER_HOUR = 60;
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
    .replace(/[\u0000-\u001f\u007f]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, limit);
}

function robotsPatternMatches(pattern, target) {
  if (!pattern) return false;
  const anchored = pattern.endsWith("$");
  const body = anchored ? pattern.slice(0, -1) : pattern;
  const escaped = body.split("*").map(part => part.replace(/[.*+?^\${}()|[\]\\]/g, "\\$&")).join(".*");
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
  const selected = exactGroups.length
    ? exactGroups
    : groups.filter(group => group.agents.includes("*"));
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
    if (!window || now - window.startedAt >= WINDOW_MS) {
      window = { startedAt: now, count: 0 };
    }
    if (window.count >= MAX_REQUESTS_PER_HOST_PER_HOUR) {
      throw new Error("provider_request_budget_exhausted");
    }
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
  if (new URL(finalUrl).hostname.toLowerCase() !== host) {
    throw new Error("robots_redirect_host_changed");
  }

  let policy;
  if (response.status === 404 || response.status === 410) {
    policy = parseRobots("", USER_AGENT);
  } else {
    if (!response.ok) throw new Error("robots_unverified:" + host + ":" + response.status);
    const text = await response.text();
    if (text.length > MAX_ROBOTS_BYTES) throw new Error("robots_response_too_large");
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

function normalizeNASAItem(raw) {
  if (!raw || typeof raw !== "object") return null;
  const data = Array.isArray(raw.data) ? raw.data[0] : null;
  if (!data || String(data.media_type || "").toLowerCase() !== "video") return null;

  const nasaId = safeText(data.nasa_id, 100);
  const title = safeText(data.title, 500);
  if (!/^[A-Za-z0-9_-]{1,100}$/.test(nasaId) || !title) return null;

  const copyright = safeText(data.copyright, 200);
  if (copyright && !/^(NASA|National Aeronautics and Space Administration)$/i.test(copyright)) {
    return null;
  }

  const links = Array.isArray(raw.links) ? raw.links : [];
  const preview = links.find(link => link && String(link.rel || "").toLowerCase() === "preview" && link.href);
  if (!preview) return null;

  let thumbnail;
  try {
    const parsed = new URL(preview.href);
    if (parsed.protocol !== "https:" || !ASSET_HOSTS.has(parsed.hostname.toLowerCase())) return null;
    thumbnail = parsed.href;
  } catch {
    return null;
  }

  const credit = safeText(data.photographer || data.secondary_creator || data.center || "NASA", 240);
  const publishedAt = safeText(data.date_created, 80);
  const summary = safeText(data.description_508 || data.description || "", 360);
  return {
    title,
    url: "https://images.nasa.gov/details/" + encodeURIComponent(nasaId),
    thumbnail,
    source: "NASA Image and Video Library",
    credit: credit || "NASA",
    publishedAt,
    summary,
    sourceId: nasaId,
    attributionUrl: "https://www.nasa.gov/nasa-brand-center/images-and-media/",
    rightsNote: "NASA media guidelines apply; items with explicit third-party copyright are excluded."
  };
}

async function fetchNASA(query) {
  const key = String(query || "").trim().toLowerCase();
  const cached = resultCache.get(key);
  if (cached && cached.expiresAt > Date.now()) return cached.items;

  const url = new URL(API_BASE);
  url.searchParams.set("q", query);
  url.searchParams.set("media_type", "video");
  url.searchParams.set("page", "1");
  url.searchParams.set("page_size", "100");

  const apiPolicy = await ensureAllowed(url.href);
  if (!apiPolicy.allowed) throw new Error("robots_disallows_nasa_search");

  const response = await pacedFetch(url.href, {
    headers: { "User-Agent": USER_AGENT, "Accept": "application/json" },
    signal: AbortSignal.timeout(12000),
    redirect: "follow"
  }, apiPolicy.delayMs);
  const finalUrl = response.url || url.href;
  if (new URL(finalUrl).hostname.toLowerCase() !== API_HOST) {
    throw new Error("nasa_api_redirect_host_changed");
  }
  if (!response.ok) throw new Error("nasa_api_http_" + response.status);

  const text = await response.text();
  if (text.length > MAX_API_BYTES) throw new Error("nasa_api_response_too_large");
  const payload = JSON.parse(text);
  const rawItems = payload && payload.collection && payload.collection.items;
  if (!Array.isArray(rawItems)) throw new Error("nasa_api_schema_unexpected");

  const items = [];
  const seen = new Set();
  const assetPolicies = new Map();
  for (const raw of rawItems) {
    const item = normalizeNASAItem(raw);
    if (!item || seen.has(item.sourceId)) continue;
    const assetHost = new URL(item.thumbnail).hostname.toLowerCase();
    let assetPolicy = assetPolicies.get(assetHost);
    if (!assetPolicy) {
      assetPolicy = await loadRobots(assetHost);
      assetPolicies.set(assetHost, assetPolicy);
    }
    if (!assetPolicy.allows(item.thumbnail)) continue;
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
    const items = await fetchNASA(query);
    return res.status(200).json({
      status: "ok",
      query,
      provider: "NASA Image and Video Library",
      updatedAt: new Date().toISOString(),
      count: items.length,
      items,
      rightsNote: "NASA media guidelines apply; third-party copyright items are excluded. No video files are downloaded or re-hosted."
    });
  } catch (error) {
    const message = String(error && error.message || "");
    const robotsFailure = message.includes("robots") || message.includes("redirect_host_changed");
    return res.status(robotsFailure ? 503 : 502).json({
      status: "unavailable",
      query,
      message: robotsFailure
        ? "NASA video search is unavailable because source access could not be safely verified."
        : "NASA video search is temporarily unavailable. Try again shortly."
    });
  }
};

module.exports._test = {
  parseRobots,
  normalizeNASAItem,
  fetchNASA,
  resetForTests() {
    robotsCache.clear();
    hostQueues.clear();
    lastRequestAt.clear();
    requestWindows.clear();
    resultCache.clear();
    minIntervalMs = 0;
  }
};
