"use strict";
const { chromium } = require("playwright");
const fs = require("node:fs");

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(30000);
  const errors = [];
  const consoleErrors = [];
  const failedRequests = [];
  page.on("pageerror", e => errors.push(e.message));
  page.on("console", m => { if (m.type() === "error") consoleErrors.push(m.text()); });
  page.on("requestfailed", r => failedRequests.push({ url: r.url(), error: r.failure()?.errorText || "failed" }));
  const candidateUrls = [process.env.TARGET_URL || "https://everythinghub.ink/", process.env.FALLBACK_URL || "https://everythinghub-homepage-fixed-3vk1kcauu-rshure001-8053.vercel.app/"];
  const report = { url: candidateUrls[0], candidateUrls, startedAt: new Date().toISOString(), checks: {}, pageErrors: errors, consoleErrors, failedRequests };
  const shot = name => page.screenshot({ path: "everything-hub-vercel-" + name + ".png", fullPage: true });
  const fail = message => { throw new Error(message); };
  try {
    const response = await page.goto(report.url, { waitUntil: "domcontentloaded", timeout: 45000 });
    await page.locator("#query").waitFor({ state: "visible", timeout: 20000 });
    const art = page.locator(".art img").first();
    await art.waitFor({ state: "visible", timeout: 20000 });
    await page.waitForFunction(() => { const i=document.querySelector(".art img"); return !!i && i.complete && i.naturalWidth >= 100; }, null, { timeout: 30000 });
    const modes = await page.locator("[data-mode]").evaluateAll(nodes => [...new Set(nodes.map(n => n.getAttribute("data-mode")).filter(Boolean))].sort());
    report.checks.homepage = {
      httpStatus: response?.status(),
      contentType: response?.headers()["content-type"] || "",
      title: await page.title(),
      searchForm: await page.locator("#searchForm").count(),
      searchInput: await page.locator("#query").count(),
      resultsRoot: await page.locator("#results").count(),
      categoryCount: modes.length,
      modes,
      artwork: await art.evaluate(i => ({ srcPrefix: (i.getAttribute("src") || "").slice(0, 28), width: i.naturalWidth, height: i.naturalHeight, alt: i.alt }))
    };
    const expected = ["business","images","news","places","research","shopping","videos","web"];
    if (!response?.ok() || !report.checks.homepage.contentType.toLowerCase().startsWith("text/html") ||
        !report.checks.homepage.title.includes("Everything Hub") || report.checks.homepage.searchForm !== 1 ||
        report.checks.homepage.searchInput !== 1 || report.checks.homepage.resultsRoot !== 1 ||
        JSON.stringify(modes) !== JSON.stringify(expected)) fail("Vercel homepage structure, branding, or eight-category check failed");
    if (!report.checks.homepage.artwork.srcPrefix.startsWith("data:image/png;base64") || report.checks.homepage.artwork.width < 100) fail("Approved octopus artwork is missing or broken");
    await shot("home");

    async function search(mode, query, term, mediaKind) {
      const modeButton = page.locator('[data-mode="' + mode + '"]').first();
      if (await modeButton.count() !== 1) fail("Missing category control: " + mode);
      await modeButton.click();
      await page.locator("#query").fill(query);
      await page.locator("#query").press("Enter");
      await page.waitForFunction(() => {
        const r=document.querySelector("#results"), s=document.querySelector("#status")?.textContent || "";
        return !!r && r.textContent.trim().length > 0 && !/searching/i.test(s);
      }, null, { timeout: 120000 });
      const results = page.locator("#results");
      const text = (await results.innerText()).toLowerCase();
      const cards = await results.locator("article, a[href], video, img, .catalog-preview-list > *, .result, .result-card, .search-result").count();
      const result = { mode, query, cards, sampleText: text.slice(0, 700) };
      if (!cards || !text.includes(term.toLowerCase())) fail(mode + " search returned no relevant visible cards for " + query);
      if (mediaKind) {
        const imgs = results.locator("img");
        const imgCount = await imgs.count();
        const loaded = await imgs.evaluateAll(nodes => nodes.filter(i => i.complete && i.naturalWidth > 0).length);
        const attributions = text.match(/wikimedia commons|internet archive|creative commons|public domain|cc by|cc0/g) || [];
        result.thumbnails = imgCount;
        result.loadedThumbnails = loaded;
        result.attributionMatches = attributions.length;
        if (imgCount < 1 || loaded < 1 || attributions.length < 1) fail(mode + " did not show real loaded media with attribution");
      }
      report.checks["search_" + mode] = result;
      await shot(mode);
    }

    await search("web", "Rolex", "rolex");
    await search("web", "Las Vegas", "las vegas");
    await search("web", "NASA", "nasa");
    await search("news", "NASA Artemis", "nasa");
    await search("images", "NASA", "nasa", "image");
    await search("videos", "space", "space", "video");
    await search("shopping", "Rolex", "rolex");
    await search("places", "Las Vegas", "las vegas");
    await search("business", "Las Vegas hotels", "las vegas");
    await search("research", "NASA climate data", "nasa");

    const stories = page.locator("#discoverCards .discover-card");
    const topics = page.locator(".discover-topic");
    const refresh = page.locator("#discoverRefresh");
    report.checks.topStories = { cards: await stories.count(), tabs: await topics.count(), refresh: await refresh.count() };
    if (!report.checks.topStories.cards || !report.checks.topStories.tabs || !report.checks.topStories.refresh) fail("Top Stories, topic tabs, or Refresh control is missing");
    const dogTagLink = page.locator('a[href*="dogtagday.org" i]').first();
    report.checks.coreLinks = { dogTagDayLink: await dogTagLink.count(), href: await dogTagLink.getAttribute("href").catch(() => null) };
    if (!report.checks.coreLinks.dogTagDayLink) fail("Dog Tag Day core link is missing");

    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(report.url, { waitUntil: "domcontentloaded", timeout: 45000 });
    await page.locator("#query").waitFor({ state: "visible", timeout: 20000 });
    report.checks.mobile = await page.evaluate(() => ({
      viewportWidth: document.documentElement.clientWidth,
      documentWidth: document.documentElement.scrollWidth,
      searchVisible: !!document.querySelector("#query")?.getClientRects().length,
      artworkVisible: !!document.querySelector(".art img")?.getClientRects().length,
      categories: new Set([...document.querySelectorAll("[data-mode]")].map(n => n.getAttribute("data-mode"))).size,
      brokenImages: [...document.images].filter(i => i.getClientRects().length && i.complete && !i.naturalWidth).length
    }));
    if (report.checks.mobile.documentWidth > report.checks.mobile.viewportWidth + 2 || !report.checks.mobile.searchVisible ||
        !report.checks.mobile.artworkVisible || report.checks.mobile.categories !== 8 || report.checks.mobile.brokenImages) fail("Vercel mobile layout failed");
    await shot("mobile");
    report.checks.browserHealth = { pageErrors: errors.length, consoleErrors: consoleErrors.length, failedRequests: failedRequests.length };
    if (errors.length || consoleErrors.some(x => /uncaught|typeerror|referenceerror|failed to fetch|401|402|500|502|503/i.test(x))) fail("Vercel page has blocking browser errors");
    report.passed = true;
  } catch (e) {
    report.failure = e.stack || e.message;
    report.passed = false;
    await shot("failure").catch(() => {});
  } finally {
    report.finishedAt = new Date().toISOString();
    report.pageErrors = errors;
    report.consoleErrors = consoleErrors;
    report.failedRequests = failedRequests;
    fs.writeFileSync("everything-hub-vercel-browser-report.json", JSON.stringify(report, null, 2));
    await browser.close();
    console.log(JSON.stringify(report, null, 2));
    if (!report.passed) process.exitCode = 1;
  }
})();
// Trigger a dedicated real-browser run for the already-deployed Vercel fallback.
// Trigger Vercel fallback browser test after narrowing workflow conditions.
\n// Fallback to the immutable Vercel deployment URL and compare backend health endpoints.\n