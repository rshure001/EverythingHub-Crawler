import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { gunzipSync } from "node:zlib";
import { searchStaticPages } from "./static-search.mjs";
import { isValidStaticManifest } from "./static-manifest.mjs";

const root = new URL("../../../", import.meta.url);
const manifest = JSON.parse(await readFile(new URL("public-search/manifest.json", root), "utf8"));
if (!isValidStaticManifest(manifest)) {
  throw new Error("unsupported or out-of-bounds public catalog manifest");
}
const meta = manifest.catalog_gzip;
if (!meta || meta.file !== "catalog-v1.json.gz" || meta.pages !== manifest.total_pages) {
  throw new Error("catalog gzip metadata is missing or inconsistent");
}
const compressed = await readFile(new URL("public-search/" + meta.file, root));
if (compressed.length !== meta.bytes) throw new Error("catalog compressed byte count mismatch");
if (createHash("sha256").update(compressed).digest("hex") !== meta.sha256) {
  throw new Error("catalog SHA-256 mismatch");
}
const payload = JSON.parse(gunzipSync(compressed).toString("utf8"));
if (payload.version !== 1 || !Array.isArray(payload.pages) || payload.pages.length !== manifest.total_pages) {
  throw new Error("catalog payload does not match manifest page count");
}
const nasa = searchStaticPages(payload.pages, "NASA", null, 5, 0);
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
const rolex = searchStaticPages(payload.pages, "Rolex", null, 5, 0);
console.log(JSON.stringify({
  catalogPages: payload.pages.length,
  shardCount: manifest.shard_count,
  compressedBytes: compressed.length,
  nasaMatches: nasa.count,
  nasaTop: nasa.results[0]?.url,
  nasaTopRankScore: nasa.results[0]?.rankScore,
  nasaTopEvidenceBand: nasa.results[0]?.evidenceBand,
  rolexMatches: rolex.count,
}, null, 2));
