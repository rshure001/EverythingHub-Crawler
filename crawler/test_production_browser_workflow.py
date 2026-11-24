from pathlib import Path
import unittest


WORKFLOW_PATH = (
    Path(__file__).resolve().parents[1]
    / ".github"
    / "workflows"
    / "everything-hub-production-browser-check.yml"
)


FREE_SITE_WORKFLOW_PATH = (
    Path(__file__).resolve().parents[1]
    / ".github"
    / "workflows"
    / "everything-hub-free-site-browser-check.yml"
)


class ProductionBrowserWorkflowContractTests(unittest.TestCase):
    def test_browser_check_runs_after_successful_catalog_publisher(self):
        workflow = FREE_SITE_WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertIn('workflows: ["Publish and verify Everything Hub — in-house Supabase route"]', workflow)
        self.assertIn("types: [completed]", workflow)
        self.assertIn("branches: [main]", workflow)
        self.assertIn("SITE_URL: https://vdceayvfgkwpgwixrhwi.supabase.co/functions/v1/everything-hub-site/", workflow)
        self.assertIn("SEARCH_URL: https://vdceayvfgkwpgwixrhwi.supabase.co/functions/v1/everything-hub-web-search", workflow)
        self.assertIn("MEDIA_URL: https://vdceayvfgkwpgwixrhwi.supabase.co/functions/v1/everything-hub-media-search", workflow)
        self.assertIn('page.locator("#q")', workflow)
        self.assertIn('button[data-mode]', workflow)
        self.assertIn('manifest.query_index', workflow)
        self.assertIn('data.indexTransport !== "query-shards"', workflow)
        self.assertIn("everythinghub-free-production.png", workflow)
        self.assertIn("report.pageErrors", workflow)

    def test_appdeploy_preview_is_separate_from_free_production_gate(self):
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertNotIn("workflow_run:", workflow)
        self.assertIn('vercel-homepage/**', workflow)
        self.assertIn('vercel-search-backend/**', workflow)
        self.assertIn("TARGET_URL: https://everythinghub-search-engine-4x25f8.v2.appdeploy.ai/", workflow)

    def test_library_navigation_is_checked_after_leaving_hero_only_state(self):
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        search_state = workflow.index('await searchWeb("NASA", "nasa|space|aerospace", "nasa");')
        library_button = workflow.index('const libraryNavButton = page.locator("button.nav-library").first();')
        self.assertLess(search_state, library_button)
        self.assertIn('await libraryNavButton.waitFor({ state: "visible", timeout: 12000 });', workflow)
        self.assertIn("headerLibraryVisible", workflow)
        self.assertIn("exploreLinkVisible", workflow)
        self.assertIn("aboutLinkVisible", workflow)
        self.assertIn("dogTagDayLinkVisible", workflow)
        self.assertIn("await route.fulfill({", workflow)
        self.assertIn('answer: "Research route verified; paid AI execution skipped."', workflow)
        self.assertNotIn("await route.abort();", workflow)

    def test_catalog_updates_use_publisher_completion_trigger(self):
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        free_workflow = FREE_SITE_WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertNotIn('"public-search/**"', workflow)
        self.assertIn('vercel-homepage/**', workflow)
        self.assertIn('public-search/manifest.json', free_workflow)
        self.assertIn('workflow_run:', free_workflow)

    def test_free_fallback_discloses_partial_query_scan_coverage(self):
        homepage = (
            Path(__file__).resolve().parents[1]
            / "vercel-homepage"
            / "index.html"
        ).read_text(encoding="utf-8")
        self.assertIn("candidateTruncated", homepage)
        self.assertIn("Partial index scan", homepage)
        self.assertIn("totalCandidateShardCount", homepage)
        self.assertIn("totalCandidatePageCount", homepage)
        self.assertIn("Results may be incomplete.", homepage)

    def test_free_fallback_search_exposes_candidate_catalog_previews(self):
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertIn('#results .eh-catalog-result', workflow)
        self.assertIn('catalogPreview:{count:catalogCount', workflow)
        self.assertIn('Record rights not cleared', workflow)
        self.assertIn('Commercial use not cleared', workflow)
        self.assertIn('timeout: 30000', workflow)
        self.assertIn('timeout: 35000', workflow)
        self.assertIn('node.dataset.sourceOrigin || node.dataset.sourceKind', workflow)
        self.assertIn('catalogDisplaySources.includes("external_link_only")', workflow)


if __name__ == "__main__":
    unittest.main()
