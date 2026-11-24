from pathlib import Path
import unittest


WORKFLOW_PATH = (
    Path(__file__).resolve().parents[1]
    / ".github"
    / "workflows"
    / "everything-hub-production-browser-check.yml"
)


class ProductionBrowserWorkflowContractTests(unittest.TestCase):
    def test_browser_check_runs_after_successful_catalog_publisher(self):
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertIn('workflows: ["Publish and verify Everything Hub — in-house Supabase route"]', workflow)
        self.assertIn("types: [completed]", workflow)
        self.assertIn("branches: [main]", workflow)
        self.assertNotIn("APPDEPLOY_ACCEPTANCE_ENABLED", workflow)
        self.assertIn("TARGET_URL: https://everythinghub-search-engine-4x25f8.v2.appdeploy.ai/", workflow)
        self.assertIn("everythinghub-production-*.png", workflow)
        self.assertIn("production-report.json", workflow)
        self.assertIn("nav.hub-hero-divisions button", workflow)
        self.assertIn(".image-results-grid .image-result", workflow)
        self.assertIn("section.top-news .discover-filters button", workflow)
        self.assertIn("section.top-news .discover-story-body", workflow)
        self.assertIn('node.querySelector(".eh-result-title")', workflow)
        self.assertIn('node.querySelector(".eh-result-title, .place-result-heading h3, h3, h2")', workflow)
        self.assertIn("section.top-news .top-news-heading p", workflow)
        self.assertIn("refreshResponseStatus", workflow)
        self.assertIn("report.images.loaded < 3", workflow)
        self.assertNotIn("section.top-news .top-news-status", workflow)
        self.assertIn("requestUrl.pathname.endsWith(path)", workflow)
        self.assertIn("const apiResponse = (path, predicate)", workflow)
        self.assertIn("report.apiResponses.filter", workflow)
        self.assertIn('pathname.endsWith("/api/ai/answer")', workflow)
        self.assertIn('requestUrl.pathname.includes("/api/")', workflow)
        self.assertIn('responseUrl.pathname.includes("/api/")', workflow)
        self.assertIn('url.pathname.endsWith("/api/news")', workflow)
        self.assertIn("aria-pressed", workflow)
        self.assertIn("Refresh top news", workflow)
        self.assertIn("documentWidth > viewportWidth + 2", workflow)
        self.assertIn(
            "github.event_name != 'workflow_run' || "
            "github.event.workflow_run.conclusion == 'success'",
            workflow,
        )

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
        self.assertNotIn('"public-search/**"', workflow)
        self.assertIn('vercel-homepage/**', workflow)

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
