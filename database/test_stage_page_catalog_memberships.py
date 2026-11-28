import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from stage_page_catalog_memberships import build_membership_staging

def row(rid="page-child:abc", catalogs=None, source="https://example.org/catalog"):
    return {
        "record_id": rid, "catalog_ids": catalogs or ["catalog:a","catalog:b"],
        "title": "Article metadata", "article_url": "https://example.org/catalog/articles/1",
        "record_type": "article_link_metadata", "rights_status": "per_record_review_required",
        "verified": False, "content_fetched": False, "source_urls": [source],
        "source_hosts": ["example.org"],
        "source_provenance": [{"source_url":source,"catalog_ids":catalogs or ["catalog:a","catalog:b"],
          "terms_url":"https://example.org/terms","terms_status":"reviewed",
          "attribution_required":"yes","rights_scope":"metadata_only"}],
        "discovered_at":"2026-11-28T00:00:00Z","last_seen_at":"2026-11-28T00:00:00Z"
    }

class MembershipBridgeTests(unittest.TestCase):
    def test_multicatalog_membership_preserves_candidate_and_provenance(self):
        out=build_membership_staging({"records":[row()]})
        self.assertEqual(out["candidate_count"],1)
        self.assertEqual(out["membership_count"],2)
        self.assertFalse(out["production_import_allowed"])
        self.assertFalse(out["public_search_allowed"])
        self.assertEqual({m["catalog_id"] for m in out["memberships"]},{"catalog:a","catalog:b"})
        self.assertEqual(out["candidates"][0]["rights_status"],"per_record_review_required")
        self.assertEqual(len(out["candidates"][0]["source_provenance"]),1)

    def test_duplicate_candidate_merges_sources_and_memberships(self):
        out=build_membership_staging({"records":[row(source="https://example.org/a"),row(source="https://example.org/b")]})
        self.assertEqual(out["candidate_count"],1)
        self.assertEqual(out["membership_count"],2)
        self.assertEqual(len(out["candidates"][0]["source_urls"]),2)
        self.assertEqual(len(out["candidates"][0]["source_provenance"]),2)

    def test_rejects_rows_that_claim_verification_or_fetch(self):
        bad=row(); bad["verified"]=True
        out=build_membership_staging({"records":[bad]})
        self.assertEqual(out["candidate_count"],0)
        self.assertEqual(out["rejected_rows"],1)
        bad=row(); bad["content_fetched"]=True
        self.assertEqual(build_membership_staging({"records":[bad]})["candidate_count"],0)

    def test_rejects_non_https_or_wrong_record_kind(self):
        bad=row(); bad["article_url"]="http://example.org/a"
        self.assertEqual(build_membership_staging({"records":[bad]})["candidate_count"],0)
        bad=row(); bad["record_type"]="article"
        self.assertEqual(build_membership_staging({"records":[bad]})["candidate_count"],0)

    def test_stable_membership_ids_and_collision_guard(self):
        a=build_membership_staging({"records":[row()]})
        b=build_membership_staging({"records":[row()]})
        self.assertEqual(a["memberships"],b["memberships"])
        bad=row(); bad["article_url"]="https://example.org/different"
        with self.assertRaises(ValueError):
            build_membership_staging({"records":[row(),bad]})

    def test_requires_records_array(self):
        with self.assertRaises(ValueError):
            build_membership_staging({})
if __name__ == "__main__": unittest.main()
