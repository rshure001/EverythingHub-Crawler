"use strict";
const { chromium } = require("playwright");
const fs = require("node:fs");

const EDGE_ROOT = "https://vdceayvfgkwpgwixrhwi.supabase.co/functions/v1/everything-hub-site";
const FUNCTIONS_BASE = "https://vdceayvfgkwpgwixrhwi.supabase.co/functions/v1";
const PUBLIC_KEY = process.env.EH_PUBLIC_KEY || "sb_publishable_XlIsACWhtVS1jVODWYGTpg_GMs8v3vJ";
const APPROVED_ART = "https://everythinghub-search-engine-4x25f8.v2.appdeploy.ai/resources/everythinghub-approved-octopus.png";
const EXPECTED_MODES = ["business", "images", "news", "places", "research", "shopping", "videos", "web"];

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(30000);
  const pageErrors = [];
  const consoleErrors = [];
  const requestFailures = [];
  page.on("pageerror", error => pageErrors.push(error.message));
  page.on("console", message => { if (message.type() === "error") consoleErrors.push(message.text()); });
  page.on("requestfailed", request => requestFailures.push({ url: request.url(), error: request.failure()?.errorText || "failed" }));
  const report = {
    url: process.env.TARGET_URL,
    startedAt: new Date().toISOString(),
    checks: {},
    pageErrors,
    consoleErrors,
    requestFailures
  };
  const shot = async name => page.screenshot({ path: "everything-hub-" + name + ".png", fullPage: true });
  const fail = message => { throw new Error(message + " :: " + JSON.stringify(report.checks)); };

  async function runSearch(mode, query, relevanceTerm) {
    const modeButton = page.locator('.eh-modebar .eh-mode[data-mode="' + mode + '"]');
    await modeButton.click();
    await page.locator("#q").fill(query);
    await page.locator("#search button[type=submit]").click();
    await page.locator("#results .result").first().waitFor({ state: "visible", timeout: 60000 });
    const count = await page.locator("#results .result").count();
    const titles = await page.locator("#results .result .result-copy a").allTextContents().catch(() => []);
    const text = (await page.locator("#results .result").allTextContents()).join(" ").toLowerCase();
    if (!count) fail(mode + " search returned no visible results for " + query);
    if (relevanceTerm && !text.includes(relevanceTerm.toLowerCase())) fail(mode + " results were not relevant to " + query);
    const sources = await page.locator("#results .result .result-copy .source").allTextContents();
    const result = { query, count, sampleTitles: titles.slice(0, 5), sampleSources: sources.slice(0, 5) };
    if (mode === "images") {
      const images = page.locator("#results .result .preview img");
      const imageCount = await images.count();
      for (let index = 0; index < Math.min(imageCount, 3); index++) await images.nth(index).scrollIntoViewIfNeeded();
      await page.waitForFunction(() => [...document.querySelectorAll("#results .result .preview img")].filter(img => img.complete && img.naturalWidth > 0).length >= 2, null, { timeout: 30000 }).catch(() => {});
      const loaded = await images.evaluateAll(nodes => nodes.filter(img => img.complete && img.naturalWidth > 0).length);
      const attributions = await page.locator("#results .result .result-copy .source").allTextContents();
      result.thumbnails = imageCount;
      result.loadedThumbnails = loaded;
      result.attributions = attributions.slice(0, 5);
      const mediaUrl = new URL(FUNCTIONS_BASE + "/everything-hub-media-search");
      mediaUrl.searchParams.set("q", query);
      mediaUrl.searchParams.set("type", "image");
      const mediaResponse = await page.request.get(mediaUrl.toString(), { headers: { apikey: PUBLIC_KEY, accept: "application/json" } });
      const mediaPayload = await mediaResponse.json().catch(() => ({}));
      result.rawMediaHttpStatus = mediaResponse.status();
      result.rawMediaProvider = mediaPayload.provider || null;
      result.rawMediaCount = Array.isArray(mediaPayload.results) ? mediaPayload.results.length : 0;
      result.mediaPolicyDecisions = await page.evaluate(items => (items || []).slice(0, 12).map(item => ({
        source: item.source, license: item.license, licenseUrl: item.licenseUrl,
        creator: item.creator, credit: item.credit, attributionRequired: item.attributionRequired,
        allowed: window.EHMediaPreviewPolicy?.isMediaPreviewAllowed(item) === true
      })), mediaPayload.results || []);
      report.checks["search_" + mode] = result;
      if (loaded < 2 || attributions.filter(value => /Wikimedia Commons|Internet Archive/i.test(value) && /Public domain|CC BY|CC0|Creative Commons/i.test(value)).length < 2) {
        fail("Images did not return at least two loaded, attributed licensed thumbnails");
      }
    }
    if (mode === "videos") {
      const previews = page.locator("#results .result .preview img, #results .result .preview video");
      const previewCount = await previews.count();
      for (let index = 0; index < Math.min(previewCount, 2); index++) await previews.nth(index).scrollIntoViewIfNeeded();
      const attributions = await page.locator("#results .result .result-copy .source").allTextContents();
      result.videoPreviews = previewCount;
      result.attributions = attributions.slice(0, 5);
      if (previewCount < 2 || attributions.filter(value => /Wikimedia Commons|Internet Archive/i.test(value) && /Public domain|CC BY|CC0|Creative Commons/i.test(value)).length < 2) {
        fail("Videos did not return at least two real, attributed licensed preview cards");
      }
    }
    report.checks["search_" + mode] = result;
    await shot(mode);
    return result;
  }

  try {
    const response = await page.goto(report.url, { waitUntil: "domcontentloaded", timeout: 45000 });
    report.checks.homepage = {
      httpStatus: response?.status(),
      contentType: response?.headers()["content-type"] || "",
      title: await page.title(),
      searchFormCount: await page.locator("form#search").count(),
      searchInputCount: await page.locator("#q").count(),
      categoryCount: await page.locator(".eh-modebar .eh-mode[data-mode]").count(),
      categoryModes: await page.locator(".eh-modebar .eh-mode[data-mode]").evaluateAll(nodes => nodes.map(node => node.dataset.mode).sort())
    };
    const artwork = page.locator(".approved-master-art").first();
    await artwork.waitFor({ state: "visible", timeout: 20000 });
    await page.waitForFunction(expected => {
      const img = document.querySelector(".approved-master-art");
      return !!img && img.complete && img.naturalWidth >= 100 && img.getAttribute("src") === expected;
    }, APPROVED_ART, { timeout: 30000 });
    report.checks.homepage.approvedArtwork = await artwork.evaluate(img => ({
      src: img.getAttribute("src"), currentSrc: img.currentSrc, naturalWidth: img.naturalWidth, naturalHeight: img.naturalHeight
    }));
    if (!response?.ok() || !report.checks.homepage.contentType.toLowerCase().startsWith("text/html") ||
        !report.checks.homepage.title.includes("Everything Hub") ||
        report.checks.homepage.searchFormCount !== 1 || report.checks.homepage.searchInputCount !== 1 ||
        report.checks.homepage.categoryCount !== 8 ||
        JSON.stringify(report.checks.homepage.categoryModes) !== JSON.stringify(EXPECTED_MODES)) {
      fail("Homepage structure, branding, or category check failed");
    }
    await page.locator("#discoverCards .discover-card").first().waitFor({ state: "visible", timeout: 60000 });
    report.checks.discoverInitialCards = await page.locator("#discoverCards .discover-card").count();
    await shot("desktop-homepage");

    // Representative required web searches.
    await runSearch("web", "Rolex", "rolex");
    await runSearch("web", "Las Vegas", "las vegas");
    await runSearch("web", "NASA", "nasa");

    // Exercise every remaining search category.
    await runSearch("news", "NASA Artemis", "nasa");
    await runSearch("images", "NASA", null);
    await runSearch("videos", "animation");
    await runSearch("shopping", "Rolex", "rolex");
    await runSearch("places", "Las Vegas", "las vegas");
    await runSearch("business", "Las Vegas hotels", "las vegas");
    await runSearch("research", "NASA climate data", "nasa");

    // Verify every Top Stories tab and the Refresh control.
    const newsTabs = page.locator(".discover-topic");
    const tabCount = await newsTabs.count();
    const tabResults = [];
    const topicQueries = {
      world: "latest world news today",
      technology: "latest technology news artificial intelligence",
      sports: "latest sports news",
      entertainment: "latest entertainment news movies music",
      business: "latest business news economy markets"
    };
    for (let index = 0; index < tabCount; index++) {
      const tab = newsTabs.nth(index);
      const label = (await tab.textContent() || "").trim();
      const topic = await tab.getAttribute("data-topic");
      const expectedQuery = topicQueries[topic] || topicQueries.world;
      const tabResponse = page.waitForResponse(response => {
        if (!response.url().includes("everything-hub-independent-search")) return false;
        try { return new URL(response.url()).searchParams.get("q") === expectedQuery; } catch { return false; }
      }, { timeout: 60000 });
      await tab.click();
      const tabResult = await tabResponse;
      if (!tabResult.ok()) fail("Top Stories tab request failed: " + label + " HTTP " + tabResult.status());
      await page.locator("#discoverCards .discover-card").first().waitFor({ state: "visible", timeout: 60000 });
      const count = await page.locator("#discoverCards .discover-card").count();
      const titles = await page.locator("#discoverCards .discover-card").allTextContents();
      if (!count) fail("Top Stories tab returned no headlines: " + label);
      tabResults.push({ label, count, sample: titles.slice(0, 3) });
    }
    report.checks.newsTabs = tabResults;
    const refresh = page.locator("#discoverRefresh");
    const activeTopic = await page.locator(".discover-topic.active").getAttribute("data-topic");
    const refreshQuery = topicQueries[activeTopic] || topicQueries.world;
    const refreshResponse = page.waitForResponse(response => {
      if (!response.url().includes("everything-hub-independent-search")) return false;
      try { return new URL(response.url()).searchParams.get("q") === refreshQuery; } catch { return false; }
    }, { timeout: 60000 });
    await refresh.click();
    const refreshResult = await refreshResponse;
    if (!refreshResult.ok()) fail("Top Stories refresh request failed: HTTP " + refreshResult.status());
    await page.locator("#discoverCards .discover-card").first().waitFor({ state: "visible", timeout: 60000 });
    report.checks.newsRefresh = { present: await refresh.count() === 1, enabled: await refresh.isEnabled(), cards: await page.locator("#discoverCards .discover-card").count() };
    if (!report.checks.newsRefresh.present || !report.checks.newsRefresh.enabled || !report.checks.newsRefresh.cards) fail("Top Stories Refresh did not work");
    const newsApiUrl = new URL(FUNCTIONS_BASE + "/everything-hub-independent-search");
    newsApiUrl.searchParams.set("q", "latest world news today");
    const newsResponse = await page.request.get(newsApiUrl.toString(), { headers: { apikey: PUBLIC_KEY, accept: "application/json" } });
    const newsPayload = await newsResponse.json().catch(() => ({}));
    const newsItems = Array.isArray(newsPayload.results) ? newsPayload.results : [];
    const times = newsItems.map(item => Date.parse(item.publishedAt)).filter(Number.isFinite);
    const newest = times.length ? Math.max(...times) : 0;
    const ageHours = newest ? (Date.now() - newest) / 3600000 : null;
    report.checks.newsFreshness = { httpStatus: newsResponse.status(), provider: newsPayload.provider || null, count: newsItems.length, newestPublishedAt: newest ? new Date(newest).toISOString() : null, ageHours };
    if (newsResponse.status() !== 200 || newsPayload.independentOfDatabase !== true || !newsItems.length || ageHours === null || ageHours < -1 || ageHours > 72) {
      fail("Top Stories public-source feed was empty or stale");
    }
    await shot("top-stories");

    if (process.env.LIVE_EDGE === "1") {
      const coreLinks = {};
      for (const [name, url, expectedText] of [
        ["discover", EDGE_ROOT + "/discover/", "Explore Pages"],
        ["catalogs", EDGE_ROOT + "/catalogs/", "Catalog Network"],
        ["topics", EDGE_ROOT + "/topics/", "Topics"]
      ]) {
        const result = await page.request.get(url);
        const body = await result.text();
        coreLinks[name] = { status: result.status(), contentType: result.headers()["content-type"] || "", containsExpectedText: body.includes(expectedText) };
        if (!result.ok() || !coreLinks[name].containsExpectedText) fail("Core navigation route failed: " + name);
      }
      const dogTagLink = page.locator('a[href*="dogtagday.org" i]').first();
      coreLinks.dogTagDay = { present: await dogTagLink.count() === 1, href: await dogTagLink.getAttribute("href") };
      if (!coreLinks.dogTagDay.present || !/^https:\/\//i.test(coreLinks.dogTagDay.href || "")) fail("Dog Tag Day core link missing or invalid");
      report.checks.coreLinks = coreLinks;
    }

    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(report.url, { waitUntil: "domcontentloaded", timeout: 45000 });
    await page.locator(".approved-master-art").waitFor({ state: "visible", timeout: 20000 });
    report.checks.mobile = await page.evaluate(() => {
      const q = document.querySelector("#q");
      const art = document.querySelector(".approved-master-art");
      const modes = document.querySelectorAll(".eh-modebar .eh-mode[data-mode]");
      const searchRect = q?.getBoundingClientRect();
      const modesRect = document.querySelector(".eh-modebar")?.getBoundingClientRect();
      return {
        viewportWidth: document.documentElement.clientWidth,
        documentWidth: document.documentElement.scrollWidth,
        searchVisible: !!q?.getClientRects().length,
        artworkVisible: !!art?.getClientRects().length,
        categoryCount: modes.length,
        searchModebarOverlap: !!(searchRect && modesRect && searchRect.bottom > modesRect.top + 2),
        visibleImages: [...document.querySelectorAll("img")].filter(img => img.getClientRects().length && img.complete && img.naturalWidth > 0).length,
        brokenVisibleImages: [...document.querySelectorAll("img")].filter(img => img.getClientRects().length && img.complete && img.naturalWidth === 0).length
      };
    });
    if (report.checks.mobile.documentWidth > report.checks.mobile.viewportWidth + 2 ||
        !report.checks.mobile.searchVisible || !report.checks.mobile.artworkVisible ||
        report.checks.mobile.categoryCount !== 8 || report.checks.mobile.searchModebarOverlap || report.checks.mobile.brokenVisibleImages > 0) {
      fail("Mobile layout check failed");
    }
    await shot("mobile");
    report.pageErrors = pageErrors;
    report.consoleErrors = consoleErrors;
    report.requestFailures = requestFailures;
    const blockingConsoleErrors = consoleErrors.filter(message => /uncaught|typeerror|referenceerror|failed to fetch|401|402|500|502|503/i.test(message));
    report.checks.browserHealth = { pageErrors: pageErrors.length, consoleErrors: consoleErrors.length, blockingConsoleErrors };
    if (pageErrors.length || blockingConsoleErrors.length) fail("Browser errors are breaking core functions");
    report.passed = true;
  } catch (error) {
    report.failure = error.stack || error.message;
    report.pageErrors = pageErrors;
    report.consoleErrors = consoleErrors;
    report.requestFailures = requestFailures;
    report.passed = false;
    await shot("failure").catch(() => {});
  } finally {
    report.finishedAt = new Date().toISOString();
    fs.writeFileSync("everything-hub-browser-report.json", JSON.stringify(report, null, 2));
    await browser.close();
    console.log(JSON.stringify(report, null, 2));
    if (!report.passed) process.exitCode = 1;
  }
})();
