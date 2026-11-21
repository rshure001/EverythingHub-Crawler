import tempfile
import unittest
from pathlib import Path

from hurricane_memory import MemoryIntegrityError, MemoryStore


class MemoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "private" / "memory.jsonl"
        self.store = MemoryStore(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_append_retrieve_and_search(self):
        saved = self.store.append(
            "The search index should remain independent.",
            kind="architecture",
            tags=["core", "search"],
        )
        self.assertEqual(self.store.get(saved["id"]), saved)
        self.assertEqual(self.store.search("INDEPENDENT")[0]["id"], saved["id"])

    def test_idempotent_memory_id(self):
        first = self.store.append("Original", memory_id="stable-1")
        second = self.store.append("Changed duplicate", memory_id="stable-1")
        self.assertEqual(first, second)
        self.assertEqual(self.store.verify()["records"], 1)

    def test_integrity_detects_tampering(self):
        self.store.append("Original")
        content = self.path.read_text(encoding="utf-8")
        self.path.write_text(content.replace("Original", "Altered"), encoding="utf-8")
        with self.assertRaises(MemoryIntegrityError):
            self.store.verify()

    def test_empty_text_rejected(self):
        with self.assertRaises(ValueError):
            self.store.append("   ")

    def test_empty_search_returns_no_results(self):
        self.store.append("A memory")
        self.assertEqual(self.store.search(" "), [])


if __name__ == "__main__":
    unittest.main()
