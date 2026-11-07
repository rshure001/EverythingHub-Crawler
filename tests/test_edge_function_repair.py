import pathlib
import unittest

SOURCE = pathlib.Path("supabase/functions/everything-hub-crawler/index.ts")


class EdgeCrawlerRepairRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE.read_text(encoding="utf-8")

    def test_terminal_queue_state_never_writes_null_retry_time(self):
        self.assertIn('if(status==="pending") patch.next_attempt_at=', self.source)
        self.assertNotIn('next_attempt_at:status==="pending"?', self.source)
        self.assertNotIn("next_attempt_at:null", self.source)

    def test_all_queue_state_writes_use_checked_helper(self):
        self.assertIn("async function updateQueue(", self.source)
        self.assertGreaterEqual(self.source.count("await updateQueue(item.id,"), 6)
        self.assertEqual(self.source.count('db.from("eh_crawl_queue").update('), 1)

    def test_html_download_is_bounded_before_decoding(self):
        self.assertIn("const MAX_HTML_BYTES = 500_000", self.source)
        self.assertIn("response.body.getReader()", self.source)
        self.assertIn('reader.cancel("HTML byte limit reached")', self.source)
        self.assertIn("readHtmlLimited(response)", self.source)
        self.assertNotIn("(await response.text()).slice(0,500000)", self.source)

    def test_run_record_write_failure_is_not_reported_as_success(self):
        self.assertIn("if(run.error) return Response.json", self.source)

    def test_production_worker_limits_are_not_silently_increased(self):
        self.assertIn("{batch_size:4}", self.source)
        self.assertIn("Math.min(2,items.length)", self.source)


if __name__ == "__main__":
    unittest.main()
