"""Search the published Everything Hub gzip catalog without a database."""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
from typing import Any

from search_core import search

DEFAULT_CATALOG = Path(__file__).resolve().parents[1] / "public-search" / "catalog-v1.json.gz"


def load_catalog(path: str | Path = DEFAULT_CATALOG) -> list[dict[str, Any]]:
    """Load and validate the existing version-1 public crawler catalog."""
    catalog_path = Path(path)
    with gzip.open(catalog_path, "rt", encoding="utf-8") as stream:
        payload = json.load(stream)
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("unsupported or malformed Everything Hub catalog")
    pages = payload.get("pages")
    if not isinstance(pages, list):
        raise ValueError("catalog pages must be a list")
    return [page for page in pages if isinstance(page, dict)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Search the local Everything Hub public catalog")
    parser.add_argument("query")
    parser.add_argument("--catalog", default=str(DEFAULT_CATALOG))
    parser.add_argument("--lane", default=None)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    pages = load_catalog(args.catalog)
    results = search(pages, args.query, lane=args.lane, limit=args.limit)
    print(json.dumps({"query": args.query, "catalog_pages": len(pages),
                      "result_count": len(results), "results": results},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
