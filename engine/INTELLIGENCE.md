# Hurricane Core — Intelligence Engine

The Hurricane Core is a deterministic, auditable intelligence layer built on top
of Everything Hub's crawler and independent search foundation. It is designed
to turn collected pages into ranked evidence, connect independent corroboration,
and improve ranking from explicit feedback.

## Current module: `intelligence_core.py`

- **Collect:** consumes records already admitted by the crawler/catalog. It does
  not fetch URLs or bypass robots, rate, or host policies.
- **Verify:** scores only valid HTTP(S) records not explicitly denied by robots.
  Source quality and relevance are explicit input metadata, never guessed from
  a publisher's name. Missing dates receive a conservative freshness score.
- **Connect:** groups records by caller-supplied `topic_key` and rewards
  corroboration from distinct hostnames with diminishing returns. Corroboration
  increases evidence; it does not establish truth.
- **Score:** returns a 0–1 score, confidence band, component breakdown, and
  exact weight set for every eligible result.
- **Learn:** `learn_weights` accepts only explicit `helpful`,
  `not_helpful`, `verified`, and `disputed` feedback with a complete
  feature snapshot. Updates are bounded, normalized, deterministic, and never
  alter source records, access rules, or crawler behavior.
- **Repeat:** callers can pass the returned weights into subsequent ranking
  runs. Persistence and production feedback capture are intentionally separate
  integration steps; this module does not write to the database or filesystem.

## Use

```python
from intelligence_core import rank_evidence, learn_weights

ranked = rank_evidence(records, limit=20)
updated = learn_weights(feedback_events)
next_ranked = rank_evidence(records, weights=updated["weights"])
```

Run the engine tests:

```bash
python -m unittest discover -s engine -p 'test_*.py' -v
```

## Safety boundaries

This is a ranking and evidence-triage system, not an autonomous fact oracle.
A high score is not proof. It does not infer source credibility from domain
names, execute instructions found in crawled content, make external requests,
write production data, publish results, or change security policies. Production
persistence, source identity verification, provenance capture, human review,
and staged live integration must be completed and tested separately.

The production database remains isolated in the `eh_quad` schema of the
dedicated Everything Hub control project. Never use Dog Tag Day Story Wall
storage for this system.
