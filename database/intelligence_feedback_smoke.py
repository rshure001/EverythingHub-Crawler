#!/usr/bin/env python3
"""End-to-end feedback persistence smoke test for isolated eh_quad only."""
import json
import os

import psycopg

from intelligence_feedback import record_feedback, refresh_rank_profile

DATABASE_URL = os.environ.get("DATABASE_URL", "")


def main():
    if not DATABASE_URL:
        raise SystemExit("DATABASE_URL is required; no database was modified.")

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("select current_database()")
            database_name = cur.fetchone()[0]
            if database_name != "everythinghub_test":
                raise SystemExit(
                    "Feedback smoke test is restricted to the isolated everythinghub_test database."
                )
            cur.execute(
                """
                select feedback_count, last_feedback_id, weights
                from eh_quad.eh_rank_profiles
                where profile_name = 'default'
                """
            )
            before = cur.fetchone()
            if before is None:
                raise AssertionError("eh_quad default rank profile was not created by the migrations.")
            before_count, before_cursor, before_weights = before
            if isinstance(before_weights, str):
                before_weights = json.loads(before_weights)

        first = record_feedback(
            conn,
            "helpful",
            {
                "relevance": 1.0,
                "source_quality": 0.8,
                "freshness": 0.2,
                "corroboration": 0.1,
            },
        )
        second = record_feedback(
            conn,
            "not_helpful",
            {
                "relevance": 0.1,
                "source_quality": 0.2,
                "freshness": 0.9,
                "corroboration": 0.8,
            },
        )
        assert first["id"] < second["id"], (first, second)

        learned = refresh_rank_profile(conn, "default", batch_size=100)
        assert learned["feedback_processed"] == 2, learned
        assert learned["feedback_used"] == 2, learned
        assert learned["feedback_ignored"] == 0, learned
        assert learned["feedback_count"] == int(before_count) + 2, learned
        assert learned["last_feedback_id"] == second["id"], learned
        assert abs(sum(learned["weights"].values()) - 1.0) < 1e-5, learned
        assert all(0.0 <= value <= 1.0 for value in learned["weights"].values()), learned
        assert learned["weights"] != before_weights, "explicit feedback did not change the bounded profile"

        repeated = refresh_rank_profile(conn, "default", batch_size=100)
        assert repeated["feedback_processed"] == 0, repeated
        assert repeated["feedback_count"] == learned["feedback_count"], repeated
        assert repeated["last_feedback_id"] == learned["last_feedback_id"], repeated

    print(
        "eh_quad feedback smoke passed: 2 explicit events inserted, 2 learned once, "
        "profile weights remained normalized, and the persisted cursor prevented replay."
    )


if __name__ == "__main__":
    main()
