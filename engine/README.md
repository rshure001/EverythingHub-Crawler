# Independent Search Core

The engine reads the existing published crawler catalog directly from `public-search/catalog-v1.json.gz`. It does not require Supabase, PostgreSQL, a hosted search provider, or paid services.

## Search the current catalog locally

```bash
python engine/catalog_search.py "Rolex" --limit 20
python engine/catalog_search.py "NASA" --lane science --limit 20
```

The loader validates catalog version and page structure. The ranking core uses Unicode-aware tokenization, weighted title/URL/description/body matches, phrase boosts, lane filters, deterministic ordering, snippets, URL de-duplication and bounded output.

## Tests

```bash
python -m unittest discover -s engine -p 'test_*.py' -v
```

This is an independent, database-free search path for the committed public catalog. It is not yet the live HTTP API or homepage integration. That remains a separate release step after real-catalog tests and deployment verification.
