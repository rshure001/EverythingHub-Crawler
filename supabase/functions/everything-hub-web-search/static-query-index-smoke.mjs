import { searchPublicCrawlerIndex } from "./static-query-index.mjs";

const required = new Set(["NASA", "Las Vegas", "Rolex"]);
const queries = ["NASA", "Las Vegas", "Rolex", "camera", "diapers"];
const output = [];

for (const query of queries) {
  const result = await searchPublicCrawlerIndex(query, null, 5, 0);
  const summary = {
    query,
    count: result.count,
    results: result.results.map((item) => ({ title: item.title, url: item.url })),
    indexedPages: result.indexedPages,
    scannedPages: result.scannedPages,
    candidateShardCount: result.candidateShardCount,
    totalCandidateShardCount: result.totalCandidateShardCount,
    queryIndexTokensUsed: result.queryIndexTokensUsed,
  };
  output.push(summary);
  if (required.has(query) && (!result.results.length || result.count < 1)) {
    throw new Error("query-index smoke test returned no results for " + query);
  }
}

console.log(JSON.stringify(output, null, 2));
