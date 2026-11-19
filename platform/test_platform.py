import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from resolve_release import resolve_release_dir

ROOT = pathlib.Path(__file__).parent

class PlatformFoundationTests(unittest.TestCase):
    def test_compose_has_separate_git_and_gateway_services(self):
        compose = (ROOT / "compose.yml").read_text()
        self.assertIn("forgejo:", compose)
        self.assertIn("gateway:", compose)
        self.assertIn("forgejo_data:", compose)
        self.assertIn("caddy_data:", compose)

    def test_registration_is_disabled_by_default(self):
        compose = (ROOT / "compose.yml").read_text()
        self.assertIn('FORGEJO__service__DISABLE_REGISTRATION: "true"', compose)

    def test_git_ssh_port_is_consistent(self):
        compose = (ROOT / "compose.yml").read_text()
        self.assertIn('FORGEJO__server__SSH_PORT: "2222"', compose)
        self.assertIn('FORGEJO__server__SSH_LISTEN_PORT: "2222"', compose)
        self.assertIn('"2222:2222"', compose)

    def test_gateway_has_https_and_security_headers(self):
        compose = (ROOT / "compose.yml").read_text()
        caddy = (ROOT / "Caddyfile").read_text()
        self.assertIn('"443:443"', compose)
        self.assertIn("Strict-Transport-Security", caddy)
        self.assertIn('X-Content-Type-Options "nosniff"', caddy)
        self.assertIn("Referrer-Policy", caddy)

    def test_static_origin_is_read_only(self):
        compose = (ROOT / "compose.yml").read_text()
        self.assertIn("/srv/everythinghub:/srv/everythinghub:ro", compose)

    def test_activation_uses_canonical_release_path_before_atomic_swap(self):
        script = (ROOT / "activate-release.sh").read_text()
        self.assertIn('resolve_release.py "$release_dir"', script)
        self.assertIn('ln -sfn "$resolved_release"', script)
        self.assertIn("mv -Tf", script)

    def test_release_resolver_accepts_a_valid_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory) / "releases"
            release = root / "release-1"
            release.mkdir(parents=True)
            (release / "index.html").write_text("<h1>Everything Hub</h1>", encoding="utf-8")
            self.assertEqual(resolve_release_dir(str(release), root), release.resolve())

    def test_release_resolver_rejects_parent_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            base = pathlib.Path(directory)
            root = base / "releases"
            outside = base / "outside"
            root.mkdir()
            outside.mkdir()
            (outside / "index.html").write_text("outside", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "outside"):
                resolve_release_dir(str(root / ".." / "outside"), root)

    def test_release_resolver_rejects_symlink_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            base = pathlib.Path(directory)
            root = base / "releases"
            outside = base / "outside"
            root.mkdir()
            outside.mkdir()
            (outside / "index.html").write_text("outside", encoding="utf-8")
            link = root / "escaped"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            with self.assertRaisesRegex(ValueError, "outside"):
                resolve_release_dir(str(link), root)

    def test_release_resolver_rejects_root_and_missing_index(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory) / "releases"
            root.mkdir()
            with self.assertRaisesRegex(ValueError, "root itself"):
                resolve_release_dir(str(root), root)
            release = root / "incomplete"
            release.mkdir()
            with self.assertRaisesRegex(ValueError, "index.html"):
                resolve_release_dir(str(release), root)

    def test_hosting_preflight_is_read_only(self):
        script = (ROOT / "host-preflight.sh").read_text()
        self.assertIn("docker compose", script)
        self.assertIn("getent ahosts", script)
        self.assertIn("no firewall, DNS, services", script)

    def test_backup_requires_both_git_and_release_data(self):
        script = (ROOT / "backup-host.sh").read_text()
        self.assertIn("docker volume inspect", script)
        self.assertIn("/srv/everythinghub/releases", script)
        self.assertIn("sha256sum", script)
        self.assertIn("umask 077", script)

    def test_hosting_docs_explain_physical_host_dependency(self):
        docs = (ROOT / "HOSTING.md").read_text()
        self.assertIn("physical machine", docs)
        self.assertIn("no confirmed always-on free host", docs)

    def test_preflight_parses_env_without_sourcing_it(self):
        script = (ROOT / "host-preflight.sh").read_text()
        self.assertIn("Parse the dotenv file without sourcing", script)
        self.assertIn("getent ahosts", script)
        self.assertNotIn('source "$ENV_FILE"', script)

    def test_installer_runs_preflight_before_starting_services(self):
        script = (ROOT / "install-host.sh").read_text()
        self.assertLess(script.index('host-preflight.sh'), script.index('up -d'))
        self.assertIn("did not change DNS or firewall", script)

    def test_status_checks_both_public_hosts_and_active_release(self):
        script = (ROOT / "host-status.sh").read_text()
        self.assertIn('https://$WEB_HOST/', script)
        self.assertIn('https://$GIT_HOST/', script)
        self.assertIn("/srv/everythinghub/current", script)

    def test_example_env_contains_no_real_credentials(self):
        env = (ROOT / ".env.example").read_text()
        self.assertIn("GIT_HOST=", env)
        self.assertIn("WEB_HOST=", env)
        self.assertNotIn("PASSWORD=", env)
        self.assertNotIn("TOKEN=", env)

if __name__ == "__main__":
    unittest.main()
