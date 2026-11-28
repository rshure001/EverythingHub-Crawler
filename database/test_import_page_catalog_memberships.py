import unittest
from database.import_page_catalog_memberships import build_rows, target_schema

def payload():
    c={"candidate_id":"page-child:1","title":"Link metadata","target_url":"https://example.org/a",
       "record_kind":"article_link_metadata","staging_status":"metadata_only_not_a_verified_record",
       "rights_status":"per_record_review_required","verified":False,"content_fetched":False,
       "source_urls":["https://example.org/catalog"],"source_hosts":["example.org"],
       "source_provenance":[{"source_url":"https://example.org/catalog","rights_scope":"metadata_only"}],
       "discovered_at":"2026-11-28T00:00:00Z","last_seen_at":"2026-11-28T00:01:00Z"}
    m={"membership_id":"membership:1","candidate_id":"page-child:1","catalog_id":"catalog:1",
       "relationship":"candidate_child_link","membership_status":"metadata_only_not_public",
       "rights_status":"per_record_review_required"}
    return {"artifact_type":"page_catalog_membership_staging","production_import_allowed":False,
            "public_search_allowed":False,"candidates":[c],"memberships":[m]}

class ImportPageCatalogMembershipTests(unittest.TestCase):
    def test_builds_rows_without_promoting_public_records(self):
        c,m=build_rows(payload())
        self.assertEqual(len(c),1); self.assertEqual(len(m),1)
        self.assertEqual(c[0][0],"page-child:1")
    def test_schema_lock(self):
        with self.assertRaises(ValueError): target_schema("public")
    def test_rejects_public_artifact(self):
        p=payload(); p["public_search_allowed"]=True
        with self.assertRaises(ValueError): build_rows(p)
    def test_rejects_verified_candidate(self):
        p=payload(); p["candidates"][0]["verified"]=True
        with self.assertRaises(ValueError): build_rows(p)
    def test_rejects_missing_candidate_membership(self):
        p=payload(); p["memberships"][0]["candidate_id"]="missing"
        with self.assertRaises(ValueError): build_rows(p)
    def test_rejects_non_https_provenance(self):
        p=payload(); p["candidates"][0]["source_provenance"][0]["source_url"]="http://bad.test"
        with self.assertRaises(ValueError): build_rows(p)

if __name__ == "__main__": unittest.main()
