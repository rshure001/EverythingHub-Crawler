#!/usr/bin/env python3
"""Bounded, resumable Wikidata class discovery for Everything Hub.

Discovers entity IDs only. It does not copy article text, write to SQL, or publish.
Use a reviewed class manifest to add other subject families.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ENDPOINT = "https://query.wikidata.org/sparql"
QID_RE = re.compile(r"^Q[1-9][0-9]*$")
RETRYABLE = {429, 500, 502, 503, 504}


def request_page(root_qid: str, cursor: str, limit: int, retries: int = 6) -> list[str]:
    # P31/P279* includes direct instances and instances of subclasses.
    cursor_filter = ""
    if cursor:
        cursor_filter = f'FILTER(STR(?item) > "http://www.wikidata.org/entity/{cursor}")'
    query = f"""
SELECT DISTINCT ?item WHERE {{
  ?item wdt:P31/wdt:P279* wd:{root_qid} .
  FILTER(STRSTARTS(STR(?item), "http://www.wikidata.org/entity/Q"))
  {cursor_filter}
}}
ORDER BY ?item
LIMIT {limit}
"""
    url = ENDPOINT + "?" + urllib.parse.urlencode({"query": query, "format": "json"})
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/sparql-results+json",
            "User-Agent": "EverythingHubCatalogDiscovery/1.0 (public catalog indexing; contact: repository maintainer)",
        },
    )
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=90) as response:
                payload = json.load(response)
            qids = []
            for row in payload.get("results", {}).get("bindings", []):
                uri = row.get("item", {}).get("value", "")
                qid = uri.rsplit("/", 1)[-1]
                if QID_RE.fullmatch(qid):
                    qids.append(qid)
            return qids
        except urllib.error.HTTPError as exc:
            if exc.code not in RETRYABLE or attempt == retries - 1:
                raise
            time.sleep(min(60, (2 ** attempt) + random.random()))
        except (urllib.error.URLError, TimeoutError):
            if attempt == retries - 1:
                raise
            time.sleep(min(60, (2 ** attempt) + random.random()))
    return []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root-qid", default="Q15328", help="Wikidata class root; default Q15328 camera")
    parser.add_argument("--max-items", type=int, default=500)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--delay", type=float, default=2.0)
    parser.add_argument("--cursor", default="")
    parser.add_argument("--output", default="data/source-staging/wikidata-camera-discovery.json")
    args = parser.parse_args()

    if not QID_RE.fullmatch(args.root_qid):
        parser.error("--root-qid must be a valid Wikidata QID")
    if args.max_items < 1 or args.max_items > 5000:
        parser.error("--max-items must be between 1 and 5000")
    if args.page_size < 1 or args.page_size > 100:
        parser.error("--page-size must be between 1 and 100")
    if args.delay < 1:
        parser.error("--delay must be at least one second")

    cursor = args.cursor
    if cursor and not QID_RE.fullmatch(cursor):
        parser.error("--cursor must be a valid Wikidata QID")

    qids: list[str] = []
    seen: set[str] = set()
    pages = 0
    while len(qids) < args.max_items:
        batch = request_page(args.root_qid, cursor, min(args.page_size, args.max_items - len(qids)))
        pages += 1
        if not batch:
            break
        new_ids = [qid for qid in batch if qid not in seen]
        qids.extend(new_ids)
        seen.update(new_ids)
        cursor = batch[-1]
        if len(batch) < min(args.page_size, args.max_items - (len(qids) - len(new_ids))):
            break
        time.sleep(args.delay)

    payload = {
        "schema_version": 1,
        "provider": "Wikidata Query Service",
        "catalog_id": "cameras" if args.root_qid == "Q15328" else f"wikidata-class-{args.root_qid}",
        "root_qid": args.root_qid,
        "root_url": f"https://www.wikidata.org/wiki/{args.root_qid}",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "discovery_method": "P31/P279* class membership; keyset pagination by entity URI",
        "pages_fetched": pages,
        "record_count": len(qids),
        "qids": qids,
        "next_cursor": cursor,
        "complete": len(qids) < args.max_items,
        "publication_status": "staged_ids_only_not_published",
        "license_note": "Wikidata structured data is CC0-1.0; source text and media require separate rights review.",
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: payload[k] for k in ("catalog_id", "record_count", "pages_fetched", "next_cursor", "complete", "output") if k != "output"} | {"output": str(out)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
