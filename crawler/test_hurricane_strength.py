import unittest
from hurricane_strength import evaluate, render


class HurricaneStrengthTests(unittest.TestCase):
    def make_index(self, count, host_count, updated=1000, feedback=0, extractions=0):
        pages = [{"url": f"https://host{i % host_count}.example/page/{i}"} for i in range(count)]
        return {"pages": pages, "updated_at": updated,
                "feedback_events_total": feedback, "media_extractions_total": extractions}

    def test_category_one_requires_a_real_indexed_page(self):
        self.assertEqual(evaluate({"pages": []}, {"seen": [], "queue": []}, now=1000)["category"], 0)
        self.assertEqual(evaluate(self.make_index(1, 1), {"seen": [1]}, now=1000)["category"], 1)

    def test_category_two_and_three_require_breadth_and_frontier(self):
        two = evaluate(self.make_index(500, 5), {"seen": list(range(500))}, now=1000)
        self.assertEqual(two["category"], 2)
        three = evaluate(self.make_index(2500, 20), {"seen": list(range(2500))}, now=1000)
        self.assertEqual(three["category"], 3)

    def test_category_four_requires_persisted_feedback(self):
        base = self.make_index(5000, 35)
        frontier = {"seen": list(range(5000))}
        self.assertEqual(evaluate(base, frontier, now=1000)["category"], 3)
        self.assertEqual(evaluate(self.make_index(5000, 35, feedback=1), frontier, now=1000)["category"], 4)

    def test_category_five_requires_feedback_extraction_and_freshness(self):
        frontier = {"seen": list(range(10000))}
        good = self.make_index(10000, 49, updated=950, feedback=100, extractions=1)
        self.assertEqual(evaluate(good, frontier, now=1000)["category"], 5)
        stale = self.make_index(10000, 49, updated=1, feedback=100, extractions=1)
        self.assertEqual(evaluate(stale, frontier, now=1000)["category"], 4)

    def test_report_names_next_gate(self):
        result = evaluate(self.make_index(1, 1), {"seen": [1], "queue": [2]}, now=1000)
        self.assertIn("Next milestone", render(result))
        self.assertEqual(result["frontier_waiting"], 1)


if __name__ == "__main__":
    unittest.main()
