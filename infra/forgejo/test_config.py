import os
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
COMPOSE = ROOT / "compose.yaml"
ENV_EXAMPLE = ROOT / ".env.example"


class ForgejoStackSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stack = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))

    def test_database_is_not_published_to_host(self):
        self.assertEqual(self.stack["services"]["db"].get("ports", []), [])

    def test_database_uses_internal_network(self):
        self.assertIn("forgejo-internal", self.stack["services"]["db"]["networks"])

    def test_web_ui_is_loopback_only(self):
        ports = self.stack["services"]["server"]["ports"]
        self.assertIn("127.0.0.1:3000:3000", ports)

    def test_forgejo_runs_rootless_image(self):
        image = self.stack["services"]["server"]["image"]
        self.assertIn("rootless", image)

    def test_registration_disabled_and_repositories_private(self):
        env = self.stack["services"]["server"]["environment"]
        self.assertEqual(env["FORGEJO__service__DISABLE_REGISTRATION"], "true")
        self.assertEqual(env["FORGEJO__repository__DEFAULT_PRIVATE"], "private")

    def test_database_password_is_environment_supplied(self):
        db_env = self.stack["services"]["db"]["environment"]
        app_env = self.stack["services"]["server"]["environment"]
        self.assertIn("FORGEJO_DB_PASSWORD", db_env["POSTGRES_PASSWORD"])
        self.assertIn("FORGEJO_DB_PASSWORD", app_env["FORGEJO__database__PASSWD"])
        example = ENV_EXAMPLE.read_text(encoding="utf-8")
        self.assertIn("FORGEJO_DB_PASSWORD=REPLACE_WITH_A_LONG_RANDOM_SECRET", example)

    def test_no_secret_file_is_committed(self):
        self.assertFalse((ROOT / ".env").exists())

    def test_database_has_healthcheck_and_app_waits(self):
        self.assertIn("healthcheck", self.stack["services"]["db"])
        self.assertEqual(
            self.stack["services"]["server"]["depends_on"]["db"]["condition"],
            "service_healthy",
        )


if __name__ == "__main__":
    unittest.main()
