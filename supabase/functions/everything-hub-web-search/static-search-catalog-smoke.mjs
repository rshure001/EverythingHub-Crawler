import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { gunzipSync } from "node:zlib";
import { searchStaticPages } from "./static-search.mjs";
import { isValidStaticManifest } from "./static-manifest.mjs";
import vercelCatalog from "../../../vercel-search-backend/src/lib/catalog.js";

const { validateManifestAndCatalog, validateShardManifest, validateShardPayload } = vercelCatalog;
const EDGE_MAX_CATALOG_GZIP_BYTES = 6_000_000;
const EDGE_MAX_CATALOG_UNCOMPRESSED_BYTES = 30_000_000;

const root = new URL("../../../", import.meta.url);
const manifest = JSON.parse(await readFile(new URL("public-search/manifest.json", root), "utf8"));
if (!isValidStaticManifest(manifest)) {
  throw new Error("unsupported or out-of-bounds public catalog manifest");
}

let pages = [];
let transport = "shards";
let compressedBytes = null;
const meta = manifest.catalog_gzip;

if (meta) {
  if (meta.file !== "catalog-v1.json.gz" || meta.pages !== manifest.total_pages) {
    throw new Error("catalog gzip metadata is inconsistent");
  }
  const compressed = await readFile(new URL("public-search/" + meta.file, root));
  if (compressed.length !== meta.bytes) throw new Error("catalog compressed byte count mismatch");
  if (createHash("sha256").update(compressed).digest("hex") !== meta.sha256) {
    throw new Error("catalog SHA-256 mismatch");
  }
  if (compressed.length <= EDGE_MAX_CATALOG_GZIP_BYTES) {
    try {
      const payload = JSON.parse(gunzipSync(
        compressed,
        { maxOutputLength: EDGE_MAX_CATALOG_UNCOMPRESSED_BYTES },
      ).toString("utf8"));
      if (payload.version !== 1 || !Array.isArray(payload.pages) || payload.pages.length !== manifest.total_pages) {
        throw new Error("catalog payload does not match manifest page count");
      }
      pages = payload.pages;
      transport = "edge-gzip";
    } catch (error) {
      if (error?.code !== "ERR_BUFFER_TOO_LARGE") throw error;
      // The Edge Function intentionally stops at 30 MB and delegates larger
      // verified catalogs to Vercel's independent 64 MB bounded decoder.
      pages = validateManifestAndCatalog(manifest, compressed).pages;
      transport = "vercel-gzip";
    }
  } else {
    // The Edge Function has a 6 MB compressed cap; the Vercel backend allows
    // up to 12 MB compressed and 64 MB decoded, with the same SHA-256 checks.
    pages = validateManifestAndCatalog(manifest, compressed).pages;
    transport = "vercel-gzip";
  }
  compressedBytes = compressed.length;
} else {
  const checked = validateShardManifest(manifest);
  const seenUrls = new Set();
  for (const shard of checked.shards) {
    const raw = await readFile(new URL("public-search/" + shard.file, root));
    const shardPages = validateShardPayload(shard, raw);
    for (const page of shardPages) {
      if (seenUrls.has(page.url)) {
        throw new Error(shard.file + " contains a duplicate URL across shards");
      }
      seenUrls.add(page.url);
    }
    pages.push(...shardPages);
  }
  if (pages.length !== manifest.total_pages) {
    throw new Error("verified shards do not match the manifest page count");
  }
}

const nasa = searchStaticPages(pages, "NASA", null, 5, 0);
if (nasa.count < 1 || nasa.results.length < 1) throw new Error("NASA catalog query returned no results");
if (!nasa.results.every((item) =>
  Number.isFinite(item.rankScore) &&
  Number.isFinite(item.intelligenceScore) &&
  ["high", "medium", "low"].includes(item.evidenceBand)
)) {
  throw new Error("Hurricane evidence ranking fields are missing from static search results");
}
const rankScores = nasa.results.map((item) => item.rankScore);
if (rankScores.some((score, index) => index > 0 && score > rankScores[index - 1])) {
  throw new Error("Hurricane rank scores are not sorted descending");
}
const rolex = searchStaticPages(pages, "Rolex", null, 5, 0);
console.log(JSON.stringify({
  catalogPages: pages.length,
  shardCount: manifest.shard_count,
  transport,
  compressedBytes,
  nasaMatches: nasa.count,
  nasaTop: nasa.results[0]?.url,
  nasaTopRankScore: nasa.results[0]?.rankScore,
  nasaTopEvidenceBand: nasa.results[0]?.evidenceBand,
  rolexMatches: rolex.count,
}, null, 2));
