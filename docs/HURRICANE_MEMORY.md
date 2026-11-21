# Hurricane Memory — prototype design

## Purpose
Hurricane Memory is the independent memory layer for the Hurricane Core. This
first prototype is deliberately file-backed and isolated from the live site,
Supabase production, crawler index/frontier, and Dog Tag Day systems.

## Initial storage layout

```text
hurricane-memory-data/       # local private data directory; never commit user data
  memory.jsonl               # append-only journal; one checksummed record per line
```

Each record contains a format version, unique ID, UTC creation time, kind,
text, tags, optional metadata, and a SHA-256 checksum over the canonical
record payload. The journal is created with owner-only permissions where the
operating system supports them.

## Prototype operations
- **Save:** append a new immutable record.
- **Retrieve:** fetch by stable memory ID.
- **Search:** case-insensitive substring search over text, kind, and tags.
- **Verify:** validate every record and checksum.
- **Idempotency:** a repeated stable ID returns the original record instead
  of writing a duplicate.

## Safety boundaries
- No production database connection or credentials.
- No writes to Everything Hub's crawler catalog/frontier.
- No access to Dog Tag Day or Story Wall data.
- Never store passwords, API keys, access tokens, or other credentials.
- This is a prototype, not encrypted-at-rest storage, a multi-user service,
  or a production-ready concurrent database. Do not put sensitive personal
  information in it. A later stage must add encryption, access controls,
  retention/deletion policy, backup/restore tests, and cross-process locking
  before production use.

## Test
From the repository root:

```bash
python -m unittest discover -s tests -p 'test_hurricane_memory.py' -v
```
