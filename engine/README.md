# Independent Search Core

This is the first isolated component of Everything Hub's own search engine.

- Reads the existing crawler's published page records; it does not own or mutate crawl state.
- Uses Unicode-aware tokenization, weighted title/URL/description/body matching, phrase bonuses, lane filters, stable ranking and snippets.
- Uses only Python's standard library.
- Has no database, hosted search, paid API, or external service dependency.
- Rejects non-HTTP(S) result URLs, de-duplicates URLs, bounds query length and result count.
- Does not fetch pages or bypass robots.txt, access controls, or source restrictions.

Run tests:

```bash
python -m unittest discover -s engine -p 'test_*.py' -v
```

This is a standalone ranking core and testable foundation, not yet wired into the production homepage/API. Integration must be separately verified against the existing public catalog before any traffic switch.
