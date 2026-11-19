import unittest
from pathlib import Path

from crawler.crawl_revision_guard import decide


class CrawlRevisionGuardTests(unittest.TestCase):
    def test_current_main_with_no_changes_continues(self):
        result = decide([], "main")
        self.assertEqual(result["action"], "continue")

    def test_unrelated_documentation_change_does_not_restart_crawl(self):
        result = decide(["README.md", "docs/architecture.md"], "main")
        self.assertEqual(result["action"], "continue")

    def test_new_crawler_code_dispatches_from_current_main(self):
        result = decide(["crawler/crawl.py"], "main")
        self.assertEqual(result["action"], "dispatch")
        self.assertEqual(result["changed_paths"], ["crawler/crawl.py"])

    def test_new_durable_state_dispatches_from_current_main(self):
        result = decide(["data/index.json", "data/frontier.json"], "main")
        self.assertEqual(result["action"], "dispatch")
        self.assertEqual(result["changed_paths"], ["data/frontier.json", "data/index.json"])

    def test_new_public_search_shard_dispatches_from_current_main(self):
        result = decide(["public-search/shard-0001.json"], "main")
        self.assertEqual(result["action"], "dispatch")

    def test_crawler_workflow_change_dispatches_from_current_main(self):
        result = decide([".github/workflows/crawl.yml"], "main")
        self.assertEqual(result["action"], "dispatch")

    def test_non_main_ref_is_never_allowed_to_crawl_production(self):
        result = decide([], "feature/test")
        self.assertEqual(result["action"], "dispatch")
        self.assertIn("main branch", result["reason"])

    def test_workflow_checks_revision_before_external_fetches_and_gates_later_steps(self):
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github"
            / "workflows"
            / "crawl.yml"
        ).read_text(encoding="utf-8")
        guard_position = workflow.index("- name: Guard stale crawler revisions before external fetches")
        crawl_position = workflow.index("- name: Crawl configured sites")
        self.assertLess(guard_position, crawl_position)
        self.assertIn('git diff --name-only "$GITHUB_SHA" FETCH_HEAD', workflow)
        self.assertIn("crawler/crawl_revision_guard.py", workflow)
        self.assertIn("gh workflow run crawl.yml --ref main", workflow)
        for step_name in (
            "Crawl configured sites",
            "Build small public search shards",
            "Validate public search index",
            "Validate metadata-only media catalog",
            "Preflight source-approved media extraction",
            "Publish crawler health summary",
            "Measure Hurricane Core strength",
            "Commit updated index and frontier",
            "Dispatch catalog publishing and search verification",
        ):
            block = workflow.split(f"- name: {step_name}", 1)[1].split("\n      - name:", 1)[0]
            self.assertIn("if: steps.freshness.outputs.current == 'true'", block)


if __name__ == "__main__":
    unittest.main()
