"use strict";

const DEFAULT_USER_AGENT = "EverythingHubSearch/1.0";
const ROBOTS_TTL_MS = 60 * 60 * 1000;
const MAX_ROBOTS_BYTES = 512_000;
const DEFAULT_MIN_INTERVAL_MS = 1_000;
const MAX_REQUESTS_PER_HOST_PER_HOUR = 90;
const WINDOW_MS = 60 * 60 * 1000;

const robotsCache = new Map();
const hostQueues = new Map();
const lastRequestAt = new Map();
const requestWindows = new Map();

function escapeRegex(value) {
  return value.replace(/[.*+?^$()|[\]\\]/g, "\\$&");
}

function robotsPatternMatches(pattern, target) {
  if (!pattern) return false;
  const anchored = pattern.endsWith("$");
  const body = anchored ? pattern.slice(0, -1) : pattern;
  const escaped = body.split("*").map(escapeRegex).join(".*");
  return new RegExp("^" + escaped + (anchored ? "$" : "")).test(target);
}

function parseRobots(text, userAgent = DEFAULT_USER_AGENT) {
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
  const exact = groups.filter(group =>
    group.agents.some(agent => agent !== "*" && token.includes(agent))
  );
  const selected = exact.length ? exact : groups.filter(group => group.agents.includes("*"));
  const rules = selected.flatMap(group => group.rules);
  const crawlDelayMs = selected.reduce((max, group) => Math.max(max, group.crawlDelayMs), 0);

  return {
    delayMs: Math.max(DEFAULT_MIN_INTERVAL_MS, crawlDelayMs),
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

function getClock(overrides) {
  return typeof overrides.now === "function" ? overrides.now : Date.now;
}

async function loadRobots(host, overrides = {}) {
  const now = getClock(overrides);
  const timestamp = now();
  const cached = robotsCache.get(host);
  if (cached && cached.expiresAt > timestamp) {
    if (cached.promise) return cached.promise;
    return cached.policy;
  }

  const fetchImpl = overrides.fetchImpl || global.fetch;
  const userAgent = overrides.userAgent || DEFAULT_USER_AGENT;
  if (typeof fetchImpl !== "function") throw new Error("robots_fetch_unavailable");

  const robotsUrl = "https://" + host + "/robots.txt";
  const promise = (async () => {
    const response = await fetchImpl(robotsUrl, {
      headers: { "User-Agent": userAgent, "Accept": "text/plain" },
      signal: AbortSignal.timeout(8_000),
      redirect: "follow"
    });
    const finalUrl = response.url || robotsUrl;
    if (new URL(finalUrl).hostname.toLowerCase() !== host) {
      throw new Error("robots_redirect_host_changed:" + host);
    }

    let text = "";
    if (response.status !== 404 && response.status !== 410) {
      if (!response.ok) throw new Error("robots_unverified:" + host + ":" + response.status);
      text = await response.text();
      if (Buffer.byteLength(text, "utf8") > MAX_ROBOTS_BYTES) {
        throw new Error("robots_response_too_large:" + host);
      }
    }
    return parseRobots(text, userAgent);
  })();

  robotsCache.set(host, { promise, expiresAt: timestamp + ROBOTS_TTL_MS });
  try {
    const policy = await promise;
    robotsCache.set(host, { policy, expiresAt: now() + ROBOTS_TTL_MS });
    return policy;
  } catch (error) {
    robotsCache.delete(host);
    throw error;
  }
}

async function pacedFetch(host, url, options, delayMs, overrides = {}) {
  const fetchImpl = overrides.fetchImpl || global.fetch;
  const now = getClock(overrides);
  const sleep = overrides.sleep || (ms => new Promise(resolve => setTimeout(resolve, ms)));
  const userAgent = overrides.userAgent || DEFAULT_USER_AGENT;
  const previous = hostQueues.get(host) || Promise.resolve();
  let release;
  const lock = new Promise(resolve => { release = resolve; });
  const queued = previous.then(() => lock);
  hostQueues.set(host, queued);
  await previous;

  try {
    const wait = Math.max(0, delayMs - (now() - (lastRequestAt.get(host) || 0)));
    if (wait) await sleep(wait);

    const timestamp = now();
    let window = requestWindows.get(host);
    if (!window || timestamp - window.startedAt >= WINDOW_MS) {
      window = { startedAt: timestamp, count: 0 };
    }
    if (window.count >= MAX_REQUESTS_PER_HOST_PER_HOUR) {
      throw new Error("provider_request_budget_exhausted:" + host);
    }
    window.count += 1;
    requestWindows.set(host, window);

    const headers = new Headers(options.headers || {});
    headers.set("User-Agent", userAgent);
    const response = await fetchImpl(url, {
      ...options,
      headers,
      redirect: "follow"
    });
    const finalUrl = response.url || url;
    if (new URL(finalUrl).hostname.toLowerCase() !== host) {
      throw new Error("provider_redirect_host_changed:" + host);
    }
    return response;
  } finally {
    lastRequestAt.set(host, now());
    release();
    if (hostQueues.get(host) === queued) hostQueues.delete(host);
  }
}

async function robotsFetch(url, options = {}, overrides = {}) {
  let parsed;
  try { parsed = new URL(String(url)); } catch { throw new Error("provider_url_invalid"); }
  if (parsed.protocol !== "https:" || parsed.username || parsed.password) {
    throw new Error("provider_url_unsafe");
  }
  const host = parsed.hostname.toLowerCase();
  const policy = await loadRobots(host, overrides);
  if (!policy.allows(parsed.href)) {
    throw new Error("robots_disallows_request:" + host + parsed.pathname);
  }
  return pacedFetch(host, parsed.href, options, policy.delayMs, overrides);
}

function resetRobotsForTests() {
  robotsCache.clear();
  hostQueues.clear();
  lastRequestAt.clear();
  requestWindows.clear();
}

module.exports = {
  DEFAULT_USER_AGENT,
  MAX_ROBOTS_BYTES,
  MAX_REQUESTS_PER_HOST_PER_HOUR,
  parseRobots,
  robotsFetch,
  resetRobotsForTests
};
