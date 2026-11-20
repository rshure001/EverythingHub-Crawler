# Everything Hub — free self-learning layer

This is the first safe learning component for Everything Hub. It is dependency-free Python: no paid model, API key, hosted database, or external service is required.

## What it learns

The first signal is **explicit relevance feedback** attached to a search lane/category:
- `label: 1` means a user explicitly marked a result useful.
- `label: 0` means a user explicitly marked a result not useful.
- `lane` is a short category such as `web`, `news`, `images`, or `research`.

Do not treat an impression or click alone as proof of relevance. Do not put raw queries, result URLs, names, account IDs, IP addresses, device IDs, or free-form text into the feedback file. Keep any UI event collection opt-in and aggregate/minimize data before it reaches this learner.

## Run

```bash
python -m unittest discover -s learning -p 'test_*.py' -v
python learning/feedback_learner.py --input data/feedback.jsonl --output data/learned-policy-candidate.json
```

The learner uses a Beta-smoothed positive-feedback rate and proposes small lane multipliers bounded to 0.90–1.10. It requires at least 20 explicit labels in each of at least two lanes. Sparse data produces an `insufficient_feedback` result rather than a guess.

## Guardrails

- Output is a **candidate**, never a production ranking change.
- No automatic code edits, migrations, deployments, or database writes.
- A candidate must be reviewed and evaluated against a fixed holdout set before any later integration.
- Keep the existing `eh_quad` / `eh_documents` database design; this component does not create another database.
- Use open-source/local models only as optional future helpers. The deterministic learner works without an LLM.
