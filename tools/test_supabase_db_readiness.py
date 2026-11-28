import unittest

from supabase_db_readiness import probe_until_ready


class FakeConnection:
    def __init__(self, result=1):
        self.result = result
        self.closed = False
        self.executed = []

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql):
        self.executed.append(sql)
        assert sql == "SELECT 1"

    def fetchone(self):
        return (self.result,)

    def close(self):
        self.closed = True


class FakeError(Exception):
    def __init__(self, message, sqlstate=None):
        super().__init__(message)
        self.sqlstate = sqlstate


class ReadinessGuardTests(unittest.TestCase):
    def test_ready_on_first_attempt_is_read_only(self):
        conn = FakeConnection()
        calls = []
        result = probe_until_ready("postgres://example", lambda *a, **k: (calls.append(k) or conn))
        self.assertTrue(result["ready"])
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(conn.executed, ["SELECT 1"])
        self.assertIn("default_transaction_read_only=on", calls[0]["options"])
        self.assertTrue(conn.closed)

    def test_57p03_retries_then_recovers(self):
        conn = FakeConnection()
        outcomes = iter([
            FakeError("FATAL: 57P03 Hot standby mode is disabled", "57P03"),
            conn,
        ])
        delays = []
        def connect(*_args, **_kwargs):
            outcome = next(outcomes)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        result = probe_until_ready(
            "postgres://example", connect,
            attempts=3, base_delay=2, sleeper=delays.append,
        )
        self.assertTrue(result["ready"])
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(delays, [2])

    def test_57p03_exhaustion_fails_closed(self):
        delays = []
        def fail(*_args, **_kwargs):
            raise FakeError("database not accepting connections", "57P03")
        result = probe_until_ready(
            "postgres://example", fail, attempts=3, base_delay=1,
            sleeper=delays.append,
        )
        self.assertFalse(result["ready"])
        self.assertEqual(result["category"], "postgres_not_accepting_connections")
        self.assertEqual(result["sqlstate"], "57P03")
        self.assertEqual(delays, [1, 2])

    def test_authentication_error_does_not_retry(self):
        delays = []
        def fail(*_args, **_kwargs):
            raise FakeError("password authentication failed", "28P01")
        result = probe_until_ready("postgres://example", fail, attempts=5, sleeper=delays.append)
        self.assertFalse(result["ready"])
        self.assertEqual(result["category"], "authentication_failed")
        self.assertEqual(delays, [])

    def test_rejects_invalid_attempt_count(self):
        with self.assertRaises(ValueError):
            probe_until_ready("postgres://example", lambda *_a, **_k: None, attempts=11)


if __name__ == "__main__":
    unittest.main()
