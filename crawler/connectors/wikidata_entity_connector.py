#!/usr/bin/env python3
"""Import selected Wikidata entities into an auditable Everything Hub staging file.

This connector deliberately stages metadata only. It does not publish records,
change the canonical crawler index, or infer that a source page is a catalog.
Wikidata's structured data is CC0; source URLs and retrieval timestamps are kept
with every record so downstream catalog review can preserve provenance.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ENTITY_ID_RE = re.compile(r"^Q[1-9][0-9]*$")
ENTITY_URL = "https://www.wikidata.org/wiki/Special:EntityData/{qid}.json?flavor=simple"
USER_AGENT = "EverythingHubCatalogConnector/1.0 (public metadata importer)"


def _text(value: Any, language: str = "en") -> str:
    if not isinstance(value, dict):
        return ""
    entry = value.get(language) or value.get("en")
    if isinstance(entry, dict) and isinstance(entry.get("value"), str):
        return entry["value"].strip()
    return ""


def normalize_entity(payload: dict[str, Any], qid: str, catalog_id: str) -> dict[str, Any]:
    entities = payload.get("entities")
    entity = entities.get(qid) if isinstance(entities, dict) else None
    if not isinstance(entity, dict) or entity.get("id") != qid:
        raise ValueError(f"Wikidata response did not contain the requested entity {qid}")

    label = _text(entity.get("labels"))
    description = _text(entity.get("descriptions"))
    if not label:
        raise ValueError(f"Wikidata entity {qid} has no English label")

    aliases = entity.get("aliases")
    english_aliases = aliases.get("en", []) if isinstance(aliases, dict) else []
    alias_values = sorted({
        item["value"].strip()
        for item in english_aliases
        if isinstance(item, dict) and isinstance(item.get("value"), str) and item["value"].strip()
    })

    source_url = f"https://www.wikidata.org/wiki/{qid}"
    return {
        "record_id": f"wikidata:{qid}",
        "catalog_id": catalog_id,
        "title": label[:500],
        "description": description[:1000],
        "aliases": alias_values[:50],
        "source": {
            "provider": "Wikidata",
            "entity_id": qid,
            "url": source_url,
            "data_license": "CC0-1.0",
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
        },
        "rights_status": "structured_data_cc0",
        "source_terms_status": "cc0_structured_data",
        "publication_status": "staged_not_published",
    }


def fetch_entity(qid: str, timeout: int = 20) -> dict[str, Any]:
    if not ENTITY_ID_RE.fullmatch(qid):
        raise ValueError(f"Invalid Wikidata entity ID: {qid}")
    request = urllib.request.Request(
        ENTITY_URL.format(qid=qid),
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                raise RuntimeError(f"Wikidata returned HTTP {response.status} for {qid}")
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Wikidata returned HTTP {exc.code} for {qid}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Wikidata request failed for {qid}: {exc.reason}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Wikidata returned invalid JSON for {qid}")
    return payload


def import_entities(qids: list[str], catalog_id: str, delay: float = 1.0) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    if not re.fullmatch(r"[a-z0-9][a-z0-9._:-]{1,100}", catalog_id):
        raise ValueError("catalog_id must be a stable lowercase identifier")
    records: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, str]] = []
    unique_qids = list(dict.fromkeys(qids))
    for index, qid in enumerate(unique_qids):
        try:
            record = normalize_entity(fetch_entity(qid), qid, catalog_id)
            records[record["record_id"]] = record
        except Exception as exc:
            errors.append({"entity_id": qid, "error": str(exc)})
        if index + 1 < len(unique_qids) and delay > 0:
            time.sleep(delay)
    return [records[key] for key in sorted(records)], errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qid", action="append", required=True, help="Wikidata QID; repeat for each entity")
    parser.add_argument("--catalog-id", default="cameras", help="Existing EH catalog identifier")
    parser.add_argument("--output", type=Path, required=True, help="JSON staging output path")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds between requests (default: 1)")
    args = parser.parse_args()

    if args.delay < 0 or args.delay > 60:
        parser.error("--delay must be between 0 and 60 seconds")

    records, errors = import_entities(args.qid, args.catalog_id, args.delay)
    result = {
        "schema_version": 1,
        "catalog_id": args.catalog_id,
        "provider": "Wikidata",
        "license": "CC0-1.0",
        "record_count": len(records),
        "error_count": len(errors),
        "records": records,
        "errors": errors,
        "status": "staged_not_published",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "records_staged": len(records),
        "errors": len(errors),
        "status": "staged_not_published",
    }))
    return 0 if records and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
