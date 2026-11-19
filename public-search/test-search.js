const assert = require("node:assert/strict");
const { createHash, webcrypto } = require("node:crypto");
const { gzipSync } = require("node:zlib");
const engine = require("./search.js");

if (!globalThis.crypto || !globalThis.crypto.subtle) {
  Object.defineProperty(globalThis, "crypto", { value: webcrypto, configurable: true });
}

const pages = [
  { url: "https://www.rolex.com/watches", title: "Rolex watches", description: "Swiss precision timepieces", text: "Rolex watchmaking", lane: "shopping" },
  { url: "https://example.org/watch", title: "Watch", description: "A timepiece", text: "Rolex is a watch brand", lane: "culture" },
  { url: "https://nasa.gov/missions", title: "NASA missions", description: "Space science", text: "NASA launches", lane: "science" }
];

assert.deepEqual(engine.tokenize("ＲＯＬＥＸ — Café"), ["rolex", "café"]);
assert.equal(engine.search(pages, "Rolex")[0].url, "https://www.rolex.com/watches");
assert.equal(engine.search(pages, "watch", { lane: "culture" }).length, 1);
assert.equal(engine.search(pages, "  ").length, 0);
assert.equal(engine.search([...pages, pages[0]], "Rolex").filter(x => x.url === pages[0].url).length, 1);
assert.equal(engine.search([{ url: "javascript:alert(1)", title: "Rolex" }], "Rolex").length, 0);
assert.equal(engine.search([{ url: "https://user:pass@example.org/watch", title: "Rolex" }], "Rolex").length, 0);
assert.equal(engine.search([{ url: "https://example.org/blocked", title: "Rolex", robots_allowed: false }], "Rolex").length, 0);
assert.ok(engine.search(pages, "Rolex")[0].snippet);
assert.ok(engine.search(pages, "watch", { limit: 1000 }).length <= 50);

const bytesFor = payload => Buffer.from(JSON.stringify(payload) + "\n");
const sha256 = bytes => createHash("sha256").update(bytes).digest("hex");
const shardOne = bytesFor({ version: 1, pages: [pages[0]] });
const shardTwo = bytesFor({ version: 1, pages: [pages[2]] });
const manifestUrl = "https://example.test/search/manifest.json";
const manifest = {
  version: 1,
  integrity: "sha256",
  updated_at: 1795077572,
  total_pages: 2,
  shard_count: 2,
  shards: [
    { file: "shard-0001.json", pages: 1, bytes: shardOne.length, sha256: sha256(shardOne) },
    { file: "shard-0002.json", pages: 1, bytes: shardTwo.length, sha256: sha256(shardTwo) }
  ]
};
const badManifestUrl = "https://example.test/bad/manifest.json";
const badManifest = {
  ...manifest,
  shards: [
    { ...manifest.shards[0], sha256: "0".repeat(64) },
    manifest.shards[1]
  ]
};

async function testGzipLoader() {
  const gzipPages = [pages[0], pages[2]];
  const compressed = gzipSync(bytesFor({ version: 1, pages: gzipPages }), { level: 9, mtime: 0 });
  const gzipManifestUrl = "https://example.test/search-gzip/manifest.json";
  const gzipUrl = new URL("catalog-v1.json.gz", gzipManifestUrl).href;
  const gzipManifest = {
    ...manifest,
    catalog_gzip: {
      file: "catalog-v1.json.gz",
      pages: gzipPages.length,
      bytes: compressed.length,
      sha256: sha256(compressed)
    }
  };
  const previousFetch = globalThis.fetch;
  let requestCount = 0;

  globalThis.fetch = async url => {
    requestCount++;
    const value = String(url);
    if (value === gzipManifestUrl) {
      return new Response(JSON.stringify(gzipManifest), { status: 200 });
    }
    if (value === gzipUrl) return new Response(compressed, { status: 200 });
    return new Response("not found", { status: 404 });
  };

  try {
    const loaded = await engine.create({ manifestUrl: gzipManifestUrl });
    assert.equal(loaded.transport, "gzip");
    assert.equal(loaded.pageCount, 2);
    assert.equal(loaded.search("NASA", { lane: "science" }).length, 1);
    assert.equal(requestCount, 2);

    const badPages = [{ ...pages[0], robots_allowed: false }, pages[2]];
    const badCompressed = gzipSync(bytesFor({ version: 1, pages: badPages }), { level: 9, mtime: 0 });
    const badManifestUrl = "https://example.test/search-gzip-bad/manifest.json";
    const badGzipUrl = new URL("catalog-v1.json.gz", badManifestUrl).href;
    const badManifest = {
      ...manifest,
      catalog_gzip: {
        file: "catalog-v1.json.gz",
        pages: badPages.length,
        bytes: badCompressed.length,
        sha256: sha256(badCompressed)
      }
    };
    globalThis.fetch = async url => {
      const value = String(url);
      if (value === badManifestUrl) return new Response(JSON.stringify(badManifest), { status: 200 });
      if (value === badGzipUrl) return new Response(badCompressed, { status: 200 });
      return new Response("not found", { status: 404 });
    };
    await assert.rejects(
      engine.create({ manifestUrl: badManifestUrl, strictCatalogGzip: true }),
      /robots-denied/
    );
  } finally {
    globalThis.fetch = previousFetch;
  }
}

async function testShardOnlyLoader() {
  const previousFetch = globalThis.fetch;
  let activeShardRequests = 0;
  let maxActiveShardRequests = 0;
  let requestCount = 0;
  const progress = [];

  globalThis.fetch = async url => {
    requestCount++;
    const value = String(url);
    if (value === manifestUrl) {
      return new Response(JSON.stringify(manifest), { status: 200 });
    }
    if (value === badManifestUrl) {
      return new Response(JSON.stringify(badManifest), { status: 200 });
    }

    const isShard = value.endsWith("shard-0001.json") || value.endsWith("shard-0002.json");
    if (!isShard) return new Response("not found", { status: 404 });
    activeShardRequests++;
    maxActiveShardRequests = Math.max(maxActiveShardRequests, activeShardRequests);
    await new Promise(resolve => setTimeout(resolve, 5));
    const bytes = value.endsWith("shard-0001.json") ? shardOne : shardTwo;
    activeShardRequests--;
    return new Response(bytes, { status: 200 });
  };

  try {
    const loaded = await engine.create({
      manifestUrl,
      maxParallel: 2,
      onProgress: value => progress.push(value)
    });
    assert.equal(loaded.transport, "shards");
    assert.equal(loaded.pageCount, 2);
    assert.equal(loaded.updatedAt, manifest.updated_at);
    assert.equal(loaded.search("Rolex")[0].url, "https://www.rolex.com/watches");
    assert.equal(loaded.search("NASA", { lane: "science" }).length, 1);
    assert.equal(progress.length, 2);
    assert.equal(progress.at(-1).loadedShards, 2);
    assert.equal(progress.at(-1).totalShards, 2);
    assert.ok(maxActiveShardRequests <= 2);
    assert.equal(requestCount, 3);

    await assert.rejects(
      engine.create({ manifestUrl: badManifestUrl, maxParallel: 2 }),
      /SHA-256 mismatch/
    );
  } finally {
    globalThis.fetch = previousFetch;
  }
}

testShardOnlyLoader()
  .then(testGzipLoader)
  .then(() => console.log("10 browser ranking tests, gzip and shard-only loader/integrity tests passed"))
  .catch(error => {
    console.error(error);
    process.exitCode = 1;
  });
