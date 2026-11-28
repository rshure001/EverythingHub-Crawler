import json
import unittest
from database.import_page_catalog_memberships import build_rows, target_schema


def candidate(candidate_id="page-child:1", target_url="https://example.org/a", source="https://example.org/catalog"):
    return {
        "candidate_id": candidate_id, "title": "Link metadata", "target_url": target_url,
        "record_kind": "article_link_metadata",
        "staging_status": "metadata_only_not_a_verified_record",
        "rights_status": "per_record_review_required",
        "verified": False, "content_fetched": False,
        "source_urls": [source], "source_hosts": ["example.org"],
        "source_provenance": [{"source_url": source, "rights_scope": "metadata_only"}],
        "discovered_at": "2026-11-28T00:00:00Z", "last_seen_at": "2026-11-28T00:01:00Z",
    }


def membership(membership_id="membership:1", candidate_id="page-child:1", catalog_id="catalog:1"):
    return {
        "membership_id": membership_id, "candidate_id": candidate_id, "catalog_id": catalog_id,
        "relationship": "candidate_child_link", "membership_status": "metadata_only_not_public",
        "rights_status": "per_record_review_required",
    }


def payload(candidates=None, memberships=None):
    return {
        "artifact_type": "page_catalog_membership_staging",
        "production_import_allowed": False, "public_search_allowed": False,
        "candidates": candidates if candidates is not None else [candidate()],
        "memberships": memberships if memberships is not None else [membership()],
    }


class ImportPageCatalogMembershipTests(unittest.TestCase):
    def test_builds_rows_without_promoting_public_records(self):
        c, m = build_rows(payload())
        self.assertEqual(len(c), 1)
        self.assertEqual(len(m), 1)
        self.assertEqual(c[0][0], "page-child:1")

    def test_schema_lock(self):
        with self.assertRaises(ValueError):
            target_schema("public")

    def test_rejects_public_artifact(self):
        p = payload()
        p["public_search_allowed"] = True
        with self.assertRaises(ValueError):
            build_rows(p)

    def test_rejects_verified_candidate(self):
        c = candidate()
        c["verified"] = True
        with self.assertRaises(ValueError):
            build_rows(payload([c]))

    def test_rejects_missing_candidate_membership(self):
        with self.assertRaises(ValueError):
            build_rows(payload(memberships=[membership(candidate_id="missing")]))

    def test_rejects_non_https_provenance(self):
        c = candidate()
        c["source_provenance"][0]["source_url"] = "http://bad.test"
        with self.assertRaises(ValueError):
            build_rows(payload([c]))

    def test_duplicate_candidate_merges_all_provenance(self):
        a = candidate(source="https://example.org/catalog-a")
        b = candidate(source="https://example.org/catalog-b")
        candidates, _ = build_rows(payload([a, b], []))
        self.assertEqual(len(candidates), 1)
        self.assertEqual(set(json.loads(candidates[0][6])), {
            "https://example.org/catalog-a", "https://example.org/catalog-b"
        })
        self.assertEqual(len(json.loads(candidates[0][8])), 2)

    def test_duplicate_candidate_id_with_different_url_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "conflicting target URL"):
            build_rows(payload([candidate(), candidate(target_url="https://example.org/other")], []))

    def test_duplicate_logical_membership_with_different_ids_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "conflicting membership IDs"):
            build_rows(payload([candidate()], [
                membership("membership:a"), membership("membership:b")
            ]))


if __name__ == "__main__":
    unittest.main()
