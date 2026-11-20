import gzip
import json
import tempfile
import unittest
from pathlib import Path

from catalog_page_analysis import (
    ANALYZER_VERSION, analyze_pages, analyze_pages_incremental,
    search_catalog_network, search_source_page_evidence, write_outputs, validate_outputs,
)


class PageDerivedCatalogAnalysisTests(unittest.TestCase):
    def test_generic_page_does_not_become_a_fake_catalog(self):
        result = analyze_pages([{
            "url": "https://example.org/about",
            "title": "About us",
            "description": "Information about this organization.",
            "text": "Contact and background information.",
            "lane": "general",
            "robots_allowed": True,
        }], source_total=1)
        self.assertEqual(result["metrics"]["pages_analyzed"], 1)
        self.assertEqual(result["metrics"]["eligible_pages"], 1)
        self.assertEqual(result["metrics"]["unique_catalogs_discovered"], 0)
        self.assertEqual(result["metrics"]["unique_records"], 0)
        self.assertEqual(result["metrics"]["records_published"], 0)

    def test_rolex_model_pages_form_one_deduplicated_catalog(self):
        pages = [
            {
                "url": "https://www.rolex.com/watches/submariner?utm_source=one",
                "title": "Submariner",
                "description": "Rolex watch model",
                "text": "Discover the Rolex Submariner watch collection and its features.",
                "lane": "shopping", "robots_allowed": True, "fetched_at": 100,
            },
            {
                "url": "https://rolex.com/watches/day-date",
                "title": "Day-Date",
                "description": "Rolex watch model",
                "text": "Explore the Rolex Day-Date watch and its details.",
                "lane": "shopping", "robots_allowed": True, "fetched_at": 101,
            },
        ]
        result = analyze_pages(pages, source_total=2)
        self.assertEqual(result["metrics"]["unique_catalogs_discovered"], 1)
        self.assertEqual(result["metrics"]["unique_records_by_type"], {"model": 2})
        self.assertEqual(result["catalogs"][0]["title"], "Rolex Models")
        hits = search_catalog_network("Rolex", result)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["record_count"], 2)
        self.assertEqual(len(hits[0]["matched_records"]), 2)

    def test_las_vegas_place_pages_form_a_catalog(self):
        pages = [
            {
                "url": "https://visitlasvegas.com/places/las-vegas/strip",
                "title": "Las Vegas Strip",
                "description": "Explore the Las Vegas Strip.",
                "text": "Places and attractions in Las Vegas.",
                "lane": "places", "robots_allowed": True,
            },
            {
                "url": "https://visitlasvegas.com/places/las-vegas/downtown",
                "title": "Downtown Las Vegas",
                "description": "Explore downtown Las Vegas.",
                "text": "Places, attractions and destinations in Las Vegas.",
                "lane": "places", "robots_allowed": True,
            },
        ]
        result = analyze_pages(pages, source_total=2)
        self.assertEqual(result["metrics"]["unique_catalogs_discovered"], 1)
        self.assertEqual(result["metrics"]["unique_records_by_type"], {"place": 2})
        hits = search_catalog_network("Las Vegas", result)
        self.assertTrue(hits)
        self.assertIn("Las Vegas", hits[0]["title"])

    def test_one_page_can_support_multiple_distinct_catalog_candidates(self):
        result = analyze_pages([{
            "url": "https://visitlasvegas.com/places/las-vegas/events",
            "title": "Las Vegas Events",
            "description": "Browse events and places in Las Vegas.",
            "text": "Browse events and places across Las Vegas.",
            "lane": "places", "robots_allowed": True,
        }], source_total=1)
        self.assertGreaterEqual(result["metrics"]["unique_catalogs_discovered"], 2)
        self.assertEqual(result["metrics"]["pages_analyzed"], 1)

    def test_robots_denied_pages_are_not_eligible_or_catalog_sources(self):
        result = analyze_pages([{
            "url": "https://example.org/products",
            "title": "Products",
            "description": "Browse all products.",
            "text": "Catalog of products.",
            "lane": "shopping", "robots_allowed": False,
        }], source_total=1)
        self.assertEqual(result["metrics"]["eligible_pages"], 0)
        self.assertEqual(result["metrics"]["unique_catalogs_discovered"], 0)

    def test_registry_and_per_catalog_indexes_validate_after_round_trip(self):
        pages = [
            {"url": "https://shop.example/products/a", "title": "Product A", "text": "Product details and specifications.", "lane": "shopping"},
            {"url": "https://shop.example/products/b", "title": "Product B", "text": "Product details and specifications.", "lane": "shopping"},
        ]
        result = analyze_pages(pages, source_total=2)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            manifest = write_outputs(result, output)
            verified = validate_outputs(output)
            registry = json.loads(gzip.decompress((output / "registry.json.gz").read_bytes()))
            self.assertEqual(manifest["catalog_count"], 1)
            self.assertEqual(verified["pages_analyzed"], 2)
            self.assertEqual(len(registry["catalogs"]), 1)
            index_file = registry["catalogs"][0]["index_file"]
            self.assertTrue((output / index_file).exists())
            self.assertEqual(registry["catalogs"][0]["record_count"], 2)
            self.assertTrue(list((output / "indexes").glob("*.json.gz")))


    def test_incidental_description_mentions_do_not_create_false_catalog_matches(self):
        pages = [
            {
                "url": "https://nationalgeographic.com/articles/frozen-edge",
                "title": "Conserving Life at the Frozen Edge",
                "description": "A conservation story supported by Rolex.",
                "text": "Research about polar ecosystems.",
                "lane": "news", "robots_allowed": True,
            },
            {
                "url": "https://nationalgeographic.com/articles/forest",
                "title": "Protecting Ancient Forests",
                "description": "A field report about biodiversity.",
                "text": "Research about forests.",
                "lane": "news", "robots_allowed": True,
            },
        ]
        result = analyze_pages(pages, source_total=2)
        self.assertEqual(search_catalog_network("Rolex", result), [])

    def test_article_only_catalogs_do_not_masquerade_as_place_catalogs(self):
        pages = [
            {
                "url": "https://bbc.example/sport/las-vegas/game-one",
                "title": "Las Vegas Hosts Championship Game One",
                "description": "Sports coverage from Las Vegas.",
                "text": "The game was played in Las Vegas.",
                "lane": "news", "robots_allowed": True,
            },
            {
                "url": "https://bbc.example/sport/las-vegas/game-two",
                "title": "Las Vegas Hosts Championship Game Two",
                "description": "More sports coverage from Las Vegas.",
                "text": "The second game was played in Las Vegas.",
                "lane": "news", "robots_allowed": True,
            },
        ]
        result = analyze_pages(pages, source_total=2)
        self.assertTrue(any(x["record_type"] == "article" for x in result["catalogs"]))
        self.assertEqual(search_catalog_network("Las Vegas", result), [])
        self.assertTrue(search_catalog_network("Las Vegas articles", result))


    def test_product_catalog_can_match_verified_description_terms(self):
        pages = [
            {
                "url": "https://watchshop.example/watches/submariner",
                "title": "Submariner",
                "description": "Rolex automatic watch model.",
                "text": "Technical details for the Submariner.",
                "lane": "shopping", "robots_allowed": True,
            },
            {
                "url": "https://watchshop.example/watches/day-date",
                "title": "Day-Date",
                "description": "Rolex automatic watch model.",
                "text": "Technical details for the Day-Date.",
                "lane": "shopping", "robots_allowed": True,
            },
        ]
        result = analyze_pages(pages, source_total=2)
        hits = search_catalog_network("Rolex", result)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["record_count"], 2)

    def test_same_title_on_different_hosts_is_not_false_deduplicated(self):
        pages = [
            {
                "url": "https://retailer-one.example/watches/standard",
                "title": "Standard Watch",
                "description": "Automatic wristwatch.",
                "text": "Product details.",
                "lane": "shopping", "robots_allowed": True,
            },
            {
                "url": "https://retailer-two.example/watches/standard",
                "title": "Standard Watch",
                "description": "Automatic wristwatch.",
                "text": "Product details.",
                "lane": "shopping", "robots_allowed": True,
            },
        ]
        result = analyze_pages(pages, source_total=2)
        self.assertEqual(result["metrics"]["unique_records"], 2)

    def test_duplicate_candidate_count_uses_unique_page_catalog_pairs(self):
        pages = [
            {
                "url": "https://shop.example/products/a",
                "title": "Product A",
                "description": "Browse all products.",
                "text": "Browse all products and compare items.",
                "lane": "shopping", "robots_allowed": True,
            },
            {
                "url": "https://shop.example/products/b",
                "title": "Product B",
                "description": "Browse all products.",
                "text": "Browse all products and compare items.",
                "lane": "shopping", "robots_allowed": True,
            },
        ]
        result = analyze_pages(pages, source_total=2)
        self.assertEqual(result["metrics"]["unique_catalogs_discovered"], 1)
        self.assertEqual(result["metrics"]["catalog_candidate_page_references"], 2)
        self.assertEqual(result["metrics"]["duplicate_catalog_candidates"], 1)

    def test_common_title_words_do_not_masquerade_as_catalog_titles(self):
        pages = [
            {
                "url": "https://news.example/articles/active-directory",
                "title": "What Is Active Directory?",
                "description": "An enterprise identity overview.",
                "text": "Technical explanation.",
                "lane": "news", "robots_allowed": True,
            },
            {
                "url": "https://news.example/articles/dna-collection",
                "title": "DNA Collection Policy",
                "description": "A policy explainer.",
                "text": "Public policy information.",
                "lane": "news", "robots_allowed": True,
            },
        ]
        result = analyze_pages(pages, source_total=2)
        self.assertEqual(result["metrics"]["unique_catalogs_discovered"], 1)
        title = result["catalogs"][0]["title"]
        self.assertIn("Articles", title)
        self.assertNotIn("Active Directory", title)
        self.assertNotIn("DNA Collection", title)

    def test_source_index_digest_is_preserved_in_manifest(self):
        pages = [
            {"url": "https://shop.example/products/a", "title": "Product A", "lane": "shopping", "robots_allowed": True},
            {"url": "https://shop.example/products/b", "title": "Product B", "lane": "shopping", "robots_allowed": True},
        ]
        digest = "a" * 64
        result = analyze_pages(pages, source_total=2, source_index_sha256=digest)
        with tempfile.TemporaryDirectory() as tmp:
            manifest = write_outputs(result, Path(tmp))
            self.assertEqual(manifest["source_index_sha256"], digest)
            self.assertEqual(validate_outputs(Path(tmp))["pages_analyzed"], 2)


    def test_incremental_host_cache_reuses_unchanged_hosts_and_rebuilds_changed_host(self):
        pages = [
            {"url": "https://one.example/products/a", "title": "Product A", "text": "Details", "lane": "shopping"},
            {"url": "https://one.example/products/b", "title": "Product B", "text": "Details", "lane": "shopping"},
            {"url": "https://two.example/products/c", "title": "Product C", "text": "Details", "lane": "shopping"},
            {"url": "https://two.example/products/d", "title": "Product D", "text": "Details", "lane": "shopping"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache"
            first = analyze_pages_incremental(pages, source_total=4, cache_dir=cache)
            self.assertEqual(first["metrics"]["host_partitions_rebuilt"], 2)
            self.assertEqual(first["metrics"]["host_partitions_reused"], 0)
            second = analyze_pages_incremental(pages, source_total=4, cache_dir=cache)
            self.assertEqual(second["metrics"]["host_partitions_reused"], 2)
            self.assertEqual(second["metrics"]["host_partitions_rebuilt"], 0)
            changed = [dict(page) for page in pages]
            changed[0]["title"] = "Product A Updated"
            third = analyze_pages_incremental(changed, source_total=4, cache_dir=cache)
            self.assertEqual(third["metrics"]["host_partitions_reused"], 1)
            self.assertEqual(third["metrics"]["host_partitions_rebuilt"], 1)
            self.assertEqual(third["metrics"]["pages_analyzed"], 4)
            self.assertEqual(third["metrics"]["unique_catalogs_discovered"], 2)

    def test_explicit_content_lists_expand_multiple_records_and_a_catalog(self):
        result = analyze_pages([{
            "url": "https://shop.example/catalog",
            "title": "Product catalog",
            "description": "Products include Rolex Submariner, Omega Seamaster, and Tudor Black Bay.",
            "text": "",
            "lane": "general", "robots_allowed": True,
        }], source_total=1)
        self.assertEqual(result["metrics"]["unique_catalogs_discovered"], 1)
        self.assertEqual(result["metrics"]["unique_records_by_type"], {"product": 3})
        self.assertEqual(result["metrics"]["content_entity_mentions"], 3)
        hits = search_catalog_network("Rolex", result)
        self.assertEqual(len(hits), 1)
        self.assertEqual([x["title"] for x in hits[0]["matched_records"]], ["Rolex Submariner"])

    def test_one_page_can_create_multiple_content_backed_catalogs(self):
        result = analyze_pages([{
            "url": "https://example.org/about",
            "title": "Our offerings",
            "description": (
                "Products include Rolex Submariner and Omega Seamaster. "
                "Services include Watch Repair and Custom Engraving."
            ),
            "text": "",
            "lane": "general", "robots_allowed": True,
        }], source_total=1)
        self.assertEqual(result["metrics"]["unique_catalogs_discovered"], 2)
        self.assertEqual(result["metrics"]["unique_records_by_type"], {"product": 2, "service": 2})

    def test_single_list_item_does_not_force_a_catalog(self):
        result = analyze_pages([{
            "url": "https://example.org/about",
            "title": "About our products",
            "description": "Products include Rolex Submariner.",
            "text": "",
            "lane": "general", "robots_allowed": True,
        }], source_total=1)
        self.assertEqual(result["metrics"]["unique_records"], 1)
        self.assertEqual(result["metrics"]["unique_catalogs_discovered"], 0)

    def test_media_references_keep_source_page_provenance(self):
        result = analyze_pages([{
            "url": "https://example.org/gallery",
            "title": "Gallery",
            "description": "Watch gallery",
            "text": "https://cdn.example.org/watch.jpg",
            "lane": "general", "robots_allowed": True, "fetched_at": 123,
        }], source_total=1)
        self.assertEqual(result["metrics"]["media_references"], 1)
        self.assertEqual(result["metrics"]["unique_media_urls"], 1)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            write_outputs(result, output)
            validated = validate_outputs(output)
            self.assertEqual(validated["media_references_validated"], 1)
            payload = json.loads(gzip.decompress((output / "media-references.json.gz").read_bytes()))
            self.assertEqual(payload["references"][0]["source_url"], "https://example.org/gallery")
            self.assertEqual(payload["references"][0]["discovered_at"], 123)

    def test_incremental_cache_invalidates_old_analyzer_version(self):
        pages = [{
            "url": "https://cache.example/products/a", "title": "Product A",
            "text": "Product details.", "lane": "shopping",
        }]
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache"
            first = analyze_pages_incremental(pages, source_total=1, cache_dir=cache)
            self.assertEqual(first["metrics"]["host_partitions_rebuilt"], 1)
            cache_file = next(cache.glob("*.json.gz"))
            cached = json.loads(gzip.decompress(cache_file.read_bytes()).decode("utf-8"))
            cached["analyzer_version"] = ANALYZER_VERSION - 1
            cache_file.write_bytes(gzip.compress(json.dumps(cached).encode("utf-8"), mtime=0))
            second = analyze_pages_incremental(pages, source_total=1, cache_dir=cache)
            self.assertEqual(second["metrics"]["host_partitions_rebuilt"], 1)
            self.assertEqual(second["metrics"]["host_partitions_reused"], 0)

    def test_source_page_evidence_is_reported_separately_from_catalogs(self):
        pages = [{
            "url": "https://example.org/las-vegas-story",
            "title": "Las Vegas Story",
            "description": "A report about Las Vegas.",
            "text": "Coverage from Las Vegas.",
        }]
        result = analyze_pages(pages, source_total=1)
        self.assertEqual(search_catalog_network("Las Vegas", result), [])
        evidence = search_source_page_evidence("Las Vegas", pages)
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["url"], "https://example.org/las-vegas-story")


if __name__ == "__main__":
    unittest.main()
