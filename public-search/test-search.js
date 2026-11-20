const assert = require("node:assert/strict");
const fs = require("node:fs");
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
const productPage = {
  url: "https://jewel.example/ring-18k",
  title: "Jewelry collection",
  description: "Fine jewelry",
  text: "Shop our collection",
  product: {
    type: "product", name: "18K Diamond Pinky Ring",
    brand: "Rolex Fine Jewelry", sku: "RING-18K-442",
    price: "1299.00", currency: "USD", availability: "InStock",
    image: "https://jewel.example/images/ring.jpg",
    url: "https://jewel.example/ring-18k"
  }
};
for (const query of ["Rolex", "RING-18K-442", "1299.00", "InStock"]) {
  const found = engine.search([productPage], query);
  assert.equal(found.length, 1, query);
  assert.equal(found[0].product.name, "18K Diamond Pinky Ring");
  assert.equal(found[0].product.image, "https://jewel.example/images/ring.jpg");
}

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
const shardOneRaw = bytesFor({ version: 1, pages: [pages[0]] });
const shardTwoRaw = bytesFor({ version: 1, pages: [pages[2]] });
const shardOne = gzipSync(shardOneRaw, { level: 9, mtime: 0 });
const shardTwo = gzipSync(shardTwoRaw, { level: 9, mtime: 0 });
const manifestUrl = "https://example.test/search/manifest.json";
const manifest = {
  version: 1,
  integrity: "sha256",
  updated_at: 1795077572,
  total_pages: 2,
  shard_count: 2,
  shards: [
    { file: "shard-0001.json.gz", pages: 1, bytes: shardOne.length, uncompressed_bytes: shardOneRaw.length, sha256: sha256(shardOne) },
    { file: "shard-0002.json.gz", pages: 1, bytes: shardTwo.length, uncompressed_bytes: shardTwoRaw.length, sha256: sha256(shardTwo) }
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

    const isShard = value.endsWith("shard-0001.json.gz") || value.endsWith("shard-0002.json.gz");
    if (!isShard) return new Response("not found", { status: 404 });
    activeShardRequests++;
    maxActiveShardRequests = Math.max(maxActiveShardRequests, activeShardRequests);
    await new Promise(resolve => setTimeout(resolve, 5));
    const bytes = value.endsWith("shard-0001.json.gz") ? shardOne : shardTwo;
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


async function testCurrentPublishedManifestFitsClientLimits() {
  const currentManifest = JSON.parse(fs.readFileSync("./manifest.json", "utf8"));
  const manifestUrl = "https://example.test/current/manifest.json";
  const previousFetch = globalThis.fetch;
  const requested = [];
  const totalShardBytes = currentManifest.shards.reduce((sum, shard) => sum + shard.bytes, 0);
  const totalUncompressedBytes = currentManifest.shards.reduce((sum, shard) => sum + (shard.uncompressed_bytes ?? shard.bytes), 0);
  const compressedShards = currentManifest.shards[0]?.file?.endsWith(".json.gz") === true;

  assert.ok(
    currentManifest.total_pages <= 130_000,
    "current catalog must fit the 130,000-page browser limit; decoded shard data remains capped at 256 MB"
  );
  assert.ok(
    currentManifest.shard_count <= 512 &&
      currentManifest.shards.length === currentManifest.shard_count,
    "current manifest must fit the 512-shard browser safety limit"
  );
  if (currentManifest.catalog_gzip) {
    assert.ok(
      currentManifest.catalog_gzip.bytes <= 16_000_000 &&
        currentManifest.catalog_gzip.pages === currentManifest.total_pages,
      "optional compressed catalog must fit the 16 MB browser transport limit"
    );
  }
  assert.ok(
    totalShardBytes <= (compressedShards ? 64_000_000 : 100_000_000),
    "current shard catalog must fit its compressed or legacy transfer limit"
  );

  assert.ok(totalUncompressedBytes <= 256_000_000, "current shard catalog must fit the 256 MB decoded safety limit");

  globalThis.fetch = async url => {
    const value = String(url);
    requested.push(value);
    if (value === manifestUrl) {
      return new Response(JSON.stringify(currentManifest), { status: 200 });
    }
    // Force the integrity-checked shard path after optional gzip is unavailable.
    // Reaching shard-0001 proves the current manifest passed client validation.
    return new Response("not found", { status: 404 });
  };

  try {
    await assert.rejects(engine.create({ manifestUrl }), /HTTP 404/);
    if (currentManifest.catalog_gzip) {
      assert.ok(
        requested.some(value => value.endsWith("/catalog-v1.json.gz")),
        "a declared compressed catalog must be attempted before shard fallback"
      );
    } else {
      assert.ok(
        !requested.some(value => value.endsWith("/catalog-v1.json.gz")),
        "a shard-only manifest must not request an undeclared compressed catalog"
      );
    }
    assert.ok(
      requested.some(value => /\/shard-0001\.json(?:\.gz)?$/.test(value)),
      "current manifest must pass browser bounds and reach integrity-checked shard loading"
    );
  } finally {
    globalThis.fetch = previousFetch;
  }
}


async function testRejectsClientLimitOverruns() {
  const previousFetch = globalThis.fetch;
  const overPageManifest = {
    ...manifest,
    total_pages: 130_001,
    shards: [
      { ...manifest.shards[0], pages: 130_000 },
      { ...manifest.shards[1], pages: 1 }
    ]
  };
  const maxDecodedBoundaryManifest = {
    ...manifest,
    total_pages: 512,
    shard_count: 512,
    shards: Array.from({ length: 512 }, (_, index) => ({
      file: "shard-" + String(index + 1).padStart(4, "0") + ".json.gz",
      pages: 1,
      bytes: 1,
      uncompressed_bytes: 500_000,
      sha256: "a".repeat(64)
    }))
  };
  try {
    globalThis.fetch = async () => new Response(JSON.stringify(overPageManifest), { status: 200 });
    await assert.rejects(
      engine.create({ manifestUrl: "https://example.test/over-pages/manifest.json" }),
      /Unsupported or malformed Everything Hub search manifest/
    );
    const decodedBoundaryUrl = "https://example.test/decoded-boundary/manifest.json";
    globalThis.fetch = async url => {
      if (String(url) === decodedBoundaryUrl) {
        return new Response(JSON.stringify(maxDecodedBoundaryManifest), { status: 200 });
      }
      return new Response(new Uint8Array([0]), { status: 200 });
    };
    // The 256 MB aggregate boundary is exactly the maximum allowed by
    // 512 shards × 500,000 decoded bytes; validation must reach shard integrity.
    await assert.rejects(
      engine.create({ manifestUrl: decodedBoundaryUrl }),
      /SHA-256 mismatch/
    );
  } finally {
    globalThis.fetch = previousFetch;
  }
}

testShardOnlyLoader()
  .then(testGzipLoader)
  .then(testCurrentPublishedManifestFitsClientLimits)
  .then(testRejectsClientLimitOverruns)
  .then(() => console.log("Browser ranking, gzip, shard integrity, current-manifest, and 130k/256 MB bound tests passed"))
  .catch(error => {
    console.error(error);
    process.exitCode = 1;
  });
