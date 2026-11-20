# Everything Hub Project Ledger

This repository is the durable source of truth for engineering history.

- `data/project-ledger.json` is the machine-readable event history.
- `project/ledger.py` validates and appends events atomically; duplicate event IDs are ignored.
- `data/catalogs.json` stores the latest catalog, dataset, and resource inventory.
- Git commits preserve source changes; GitHub Actions preserves run results and job logs.
- Only measured or directly verified values belong in the ledger. Pending work is labeled as pending.
- Catalog discovery records its counts automatically after a successful run.
- Corrections should be new events, not silent rewrites.

Catalog discovery records dataset and resource metadata; it does not mean resource payloads have been downloaded, parsed, or indexed. Those are separate pipeline stages.
