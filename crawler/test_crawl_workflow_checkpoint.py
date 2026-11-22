import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "crawl.yml"


class CrawlWorkflowCheckpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_crawl_job_allows_bounded_index_validation(self):
        self.assertIn("timeout-minutes: 30", self.workflow)

    def test_raw_state_checkpoint_precedes_public_index_validation(self):
        checkpoint_name = "      - name: Checkpoint canonical crawl state before index validation"
        validation_name = "      - name: Validate public search index"
        self.assertIn(checkpoint_name, self.workflow)
        self.assertLess(self.workflow.index(checkpoint_name), self.workflow.index(validation_name))
        start = self.workflow.index(checkpoint_name)
        end = self.workflow.index("      - name:", start + len(checkpoint_name))
        checkpoint = self.workflow[start:end]
        self.assertIn(
            "git add data/index.json data/index-shards/ data/frontier.json data/media-discovery.json",
            checkpoint,
        )
        self.assertIn("git diff --cached --quiet", checkpoint)
        self.assertIn("git push origin HEAD:main", checkpoint)
        self.assertIn("git rebase origin/main", checkpoint)
        self.assertNotIn("public-search/", checkpoint)
        self.assertNotIn("data/page-derived-catalogs", checkpoint)

    def test_checkpoint_runs_only_on_current_main_revision(self):
        checkpoint_name = "      - name: Checkpoint canonical crawl state before index validation"
        start = self.workflow.index(checkpoint_name)
        end = self.workflow.index("      - name:", start + len(checkpoint_name))
        checkpoint = self.workflow[start:end]
        self.assertIn("if: steps.freshness.outputs.current == 'true'", checkpoint)


if __name__ == "__main__":
    unittest.main()
