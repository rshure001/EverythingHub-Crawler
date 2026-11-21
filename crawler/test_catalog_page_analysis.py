import gzip
import json
import tempfile
import unittest
from pathlib import Path

from catalog_page_analysis import (
    ANALYZER_VERSION, analyze_pages, analyze_pages_incremental,
    search_catalog_network, search_source_page_evidence, write_outputs, validate_outputs, stable_id,
    apply_source_policy_reviews,
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
        self.assertEqual(result["catalogs"][0]["title"], "Rolex Models — referenced in indexed pages")
        self.assertEqual(result["metrics"]["catalog_candidate_occurrences"], 2)
        self.assertEqual(result["metrics"]["duplicate_catalog_candidates"], 1)
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
            index_paths = list((output / "indexes").rglob("*.json.gz"))
            self.assertEqual(len(index_paths), 1)
            self.assertEqual(len(index_paths[0].relative_to(output / "indexes").parts), 2)
            self.assertEqual(index_paths[0].parent.name, index_paths[0].name[:2])
            page_payload = json.loads(gzip.decompress((output / "pages.json.gz").read_bytes()))
            self.assertTrue(all("record_ids" in page for page in page_payload["pages"]))


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
        hits = search_catalog_network("Las Vegas", result)
        self.assertTrue(hits)
        self.assertTrue(all(x["record_type"] == "article" for x in hits))
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
        self.assertEqual(result["metrics"]["source_declared_catalogs"], 1)
        self.assertEqual(result["metrics"]["content_list_catalogs"], 0)
        self.assertEqual(result["catalogs"][0]["catalog_origin"], "source_declared_collection")
        self.assertEqual(result["catalogs"][0]["record_count"], 3)
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
        self.assertEqual(result["metrics"]["content_list_catalogs"], 2)
        self.assertEqual(result["metrics"]["source_declared_catalogs"], 0)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            write_outputs(result, output)
            validated = validate_outputs(output)
            self.assertEqual(validated["catalogs"], 2)
            self.assertEqual(validated["records_validated"], 4)

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
            "product": {"type": "product", "name": "Watch", "image": "https://cdn.example.org/watch-image.webp"},
            "lane": "general", "robots_allowed": True, "fetched_at": 123,
        }], source_total=1)
        self.assertEqual(result["metrics"]["media_references"], 2)
        self.assertEqual(result["metrics"]["unique_media_urls"], 2)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            write_outputs(result, output)
            validated = validate_outputs(output)
            self.assertEqual(validated["media_references_validated"], 2)
            payload = json.loads(gzip.decompress((output / "media-references.json.gz").read_bytes()))
            self.assertTrue(all(x["source_url"] == "https://example.org/gallery" for x in payload["references"]))
            self.assertTrue(all(x["discovered_at"] == 123 for x in payload["references"]))
            self.assertIn("https://cdn.example.org/watch-image.webp", {x["media_url"] for x in payload["references"]})

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


    def test_title_matched_subject_and_article_records_support_brand_search(self):
        analysis = {
            "catalogs": [
                {"catalog_id": "subject-catalog", "catalog_type": "knowledge_base",
                 "record_type": "subject", "title": "National Geographic Subjects",
                 "subject": "subjects", "record_ids": ["rolextopic"], "source_host": "nationalgeographic.com"},
                {"catalog_id": "article-catalog", "catalog_type": "knowledge_base",
                 "record_type": "article", "title": "Rolex Articles", "subject": "articles",
                 "record_ids": ["rolexcoverage"], "source_host": "bbc.com"},
                {"catalog_id": "incidental-catalog", "catalog_type": "knowledge_base",
                 "record_type": "article", "title": "Single Source Articles", "subject": "articles",
                 "record_ids": ["incidental"], "source_host": "bbc.com"},
            ],
            "records": [
                {"record_id": "rolextopic", "title": "National Geographic and Rolex Committed to a Perpetual Planet",
                 "record_type": "subject", "description": "Rolex conservation partnership",
                 "source_url": "https://nationalgeographic.com/story",
                 "source_urls": ["https://nationalgeographic.com/story"]},
                {"record_id": "rolexcoverage", "title": "Rolex and National Geographic Partnership Coverage",
                 "record_type": "article", "description": "Partnership coverage",
                 "source_urls": ["https://bbc.com/arts/one", "https://bbc.com/arts/two"],
                 "source_url": "https://bbc.com/arts/one"},
                {"record_id": "incidental", "title": "BBC British Broadcasting Corporation",
                 "record_type": "article", "description": "A passing mention of Rolex",
                 "source_urls": [
                     "https://bbc.com/arts/home", "https://bbc.com/business/opening-bell",
                     "https://bbc.com/news/science", "https://bbc.com/pages/app",
                     "https://bbc.com/search/news", "https://bbc.com/search/sport",
                     "https://bbc.com/travel/earth",
                 ],
                 "source_url": "https://bbc.com/arts/home"},
            ],
        }
        hits = search_catalog_network("Rolex", analysis)
        self.assertEqual({x["catalog_id"] for x in hits}, {"subject-catalog", "article-catalog"})
        self.assertTrue(all(x["matched_records"] for x in hits))

    def test_people_are_sentence_does_not_create_person_records(self):
        result = analyze_pages([{
            "url": "https://example.org/stories/foreign-born",
            "title": "Foreign-Born People Are Older When They First Marry",
            "description": (
                "People are older when they first marry; "
                "Las Vegas where housing is cheaper is another comparison."
            ),
            "text": "", "lane": "general", "robots_allowed": True,
        }], source_total=1)
        self.assertNotIn("person", result["metrics"]["unique_records_by_type"])

    def test_catalog_title_ignores_lock_and_navigation_noise(self):
        pages = [
            {
                "url": "https://blm.gov/places/las-vegas-field-office",
                "title": "Las Vegas Field Office | Bureau of Land Management Lock",
                "description": "Las Vegas public lands field office.",
                "text": "", "lane": "places", "robots_allowed": True,
            },
            {
                "url": "https://blm.gov/places/sloan-canyon",
                "title": "Sloan Canyon Area | Bureau of Land Management Lock",
                "description": "Sloan Canyon public lands.",
                "text": "", "lane": "places", "robots_allowed": True,
            },
        ]
        result = analyze_pages(pages, source_total=2)
        self.assertTrue(result["catalogs"])
        self.assertEqual(len(result["catalogs"]), 1)
        self.assertEqual(len(result["catalogs"][0]["record_ids"]), 2)
        self.assertFalse(any("lock" in x["title"].casefold() for x in result["catalogs"]))

    def test_geographic_catalog_ranks_above_event_catalog_for_location_query(self):
        analysis = {
            "catalogs": [
                {"catalog_id": "events", "catalog_type": "events", "record_type": "event",
                 "title": "Las Vegas Events", "subject": "Las Vegas", "record_ids": ["event1"]},
                {"catalog_id": "places", "catalog_type": "geographic", "record_type": "place",
                 "title": "Las Vegas Places", "subject": "Las Vegas", "record_ids": ["place1"]},
            ],
            "records": [
                {"record_id": "event1", "title": "Las Vegas Grand Prix", "record_type": "event",
                 "description": "Formula 1 event", "source_url": "https://events.example.org"},
                {"record_id": "place1", "title": "Las Vegas Strip", "record_type": "place",
                 "description": "A destination in Las Vegas", "source_url": "https://places.example.org"},
            ],
        }
        results = search_catalog_network("Las Vegas", analysis)
        self.assertEqual(results[0]["catalog_id"], "places")

    def test_duplicate_canonical_source_pages_are_counted_once_and_robots_denial_wins(self):
        pages = [
            {
                "url": "https://www.example.org/products/widget?utm_source=one",
                "title": "Widget",
                "description": "Product detail",
                "text": "Product specifications.",
                "lane": "shopping", "robots_allowed": True, "fetched_at": 100,
            },
            {
                "url": "https://example.org/products/widget",
                "title": "Widget",
                "description": "Product detail",
                "text": "Product specifications.",
                "lane": "shopping", "robots_allowed": False, "fetched_at": 101,
            },
        ]
        result = analyze_pages(pages, source_total=2)
        metrics = result["metrics"]
        self.assertEqual(metrics["pages_analyzed"], 1)
        self.assertEqual(metrics["source_page_rows"], 2)
        self.assertEqual(metrics["unique_source_pages"], 1)
        self.assertEqual(metrics["duplicate_source_page_rows"], 1)
        self.assertEqual(metrics["eligible_pages"], 0)
        self.assertEqual(metrics["coverage_percentage"], 50.0)
        self.assertEqual(metrics["eligible_page_coverage_percentage"], 0.0)
        self.assertEqual(metrics["unique_source_coverage_percentage"], 100.0)
        self.assertEqual(metrics["eligible_unique_source_page_coverage_percentage"], 0.0)
        self.assertFalse(result["pages"][0]["eligible"])

    def test_incremental_analysis_deduplicates_canonical_source_pages(self):
        pages = [
            {
                "url": "https://www.example.org/products/widget?utm_source=one",
                "title": "Widget",
                "description": "Product detail",
                "text": "Product specifications.",
                "lane": "shopping", "robots_allowed": True, "fetched_at": 100,
            },
            {
                "url": "https://example.org/products/widget",
                "title": "Widget",
                "description": "Product detail",
                "text": "Product specifications.",
                "lane": "shopping", "robots_allowed": True, "fetched_at": 101,
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            result = analyze_pages_incremental(pages, source_total=2, cache_dir=Path(tmp))
        metrics = result["metrics"]
        self.assertEqual(metrics["pages_analyzed"], 1)
        self.assertEqual(metrics["source_page_rows"], 2)
        self.assertEqual(metrics["duplicate_source_page_rows"], 1)
        self.assertEqual(metrics["eligible_pages"], 1)
        self.assertEqual(metrics["coverage_percentage"], 50.0)
        self.assertEqual(metrics["unique_source_coverage_percentage"], 100.0)
        self.assertEqual(metrics["eligible_page_coverage_percentage"], 50.0)
        self.assertEqual(metrics["eligible_unique_source_page_coverage_percentage"], 100.0)


    def test_catalog_definition_page_is_not_counted_as_an_individual_record(self):
        result = analyze_pages([{
            "url": "https://example.org/locations",
            "title": "All State Offices",
            "description": "Browse all locations and find an office.",
            "text": "Browse all locations. Contact a field office for help.",
            "lane": "places", "robots_allowed": True,
        }], source_total=1)
        self.assertEqual(result["metrics"]["unique_catalogs_discovered"], 1)
        self.assertEqual(result["metrics"]["catalog_definition_pages"], 1)
        self.assertEqual(result["metrics"]["unique_records"], 0)
        self.assertTrue(result["pages"][0]["catalog_definition"])
        self.assertIsNone(result["pages"][0]["record_id"])

    def test_list_entity_extraction_rejects_possessive_context_fragments(self):
        result = analyze_pages([{
            "url": "https://example.org/events/list",
            "title": "Events in Las Vegas",
            "description": "Events include Formula 1's Las Vegas Grand Prix and its New Year's Eve celebrations.",
            "text": "Events include Formula 1's Las Vegas Grand Prix and its New Year's Eve celebrations.",
            "lane": "events", "robots_allowed": True,
        }], source_total=1)
        titles = [x["title"] for x in result["records"] if x["record_type"] == "event"]
        self.assertIn("Formula 1's Las Vegas Grand Prix", titles)
        self.assertNotIn("its New Year's Eve celebrations", titles)

    def test_field_office_pages_form_an_organization_catalog(self):
        pages = [
            {
                "url": "https://blm.gov/office/las-vegas-field-office",
                "title": "Las Vegas Field Office | Bureau of Land Management",
                "description": "The Las Vegas Field Office handles local land management operations.",
                "text": "Contact the Las Vegas Field Office for permits and access.",
                "lane": "nature", "robots_allowed": True,
            },
            {
                "url": "https://blm.gov/office/eastern-interior-field-office",
                "title": "Eastern Interior Field Office | Bureau of Land Management",
                "description": "The Eastern Interior Field Office handles local land management operations.",
                "text": "Contact the Eastern Interior Field Office for permits and access.",
                "lane": "nature", "robots_allowed": True,
            },
        ]
        result = analyze_pages(pages, source_total=2)
        catalogs = [x for x in result["catalogs"] if x["record_type"] == "organization"]
        self.assertEqual(len(catalogs), 1)
        self.assertEqual(len(catalogs[0]["record_ids"]), 2)
        matches = search_catalog_network("Las Vegas", result)
        self.assertTrue(any(x["catalog_id"] == catalogs[0]["catalog_id"] for x in matches))

    def test_entity_ids_are_stable_across_catalog_memberships(self):
        pages = [
            {"url": "https://example.org/products/widget", "title": "Widget", "lane": "shopping", "robots_allowed": True},
            {"url": "https://example.org/products/gadget", "title": "Gadget", "lane": "shopping", "robots_allowed": True},
            {"url": "https://example.org/items/widget", "title": "Widget", "lane": "shopping", "robots_allowed": True},
            {"url": "https://example.org/items/gadget", "title": "Gadget", "lane": "shopping", "robots_allowed": True},
        ]
        result = analyze_pages(pages, source_total=4)
        widget = next(x for x in result["records"] if x["title"] == "Widget")
        self.assertEqual(widget["entity_id"], stable_id("entity", widget["canonical_entity_key"]))
        self.assertEqual(widget["record_id"], stable_id("record", widget["canonical_entity_key"]))
        self.assertEqual(len(widget["catalog_ids"]), 2)

    def test_article_word_does_not_create_a_false_catalog_title(self):
        pages = [
            {
                "url": "https://example.org/articles/a",
                "title": "Thomas Massie introduces articles of impeachment against Pete Hegseth | Example News",
                "lane": "news", "robots_allowed": True,
            },
            {
                "url": "https://example.org/articles/b",
                "title": "New climate research report published | Example News",
                "lane": "news", "robots_allowed": True,
            },
        ]
        result = analyze_pages(pages, source_total=2)
        catalogs = [x for x in result["catalogs"] if x["record_type"] == "article"]
        self.assertEqual(len(catalogs), 1)
        self.assertNotIn("impeachment", catalogs[0]["title"].casefold())

    def test_catalog_release_phrase_does_not_become_a_catalog_title(self):
        pages = [
            {
                "url": "https://urban.org/events/a",
                "title": "DC Housing Preservation Catalog Release | Urban Institute",
                "lane": "events", "robots_allowed": True,
            },
            {
                "url": "https://urban.org/events/b",
                "title": "Housing research update | Urban Institute",
                "lane": "events", "robots_allowed": True,
            },
        ]
        result = analyze_pages(pages, source_total=2)
        catalogs = [x for x in result["catalogs"] if x["record_type"] == "event"]
        self.assertEqual(len(catalogs), 1)
        self.assertNotIn("catalog release", catalogs[0]["title"].casefold())



    def test_single_page_incidental_business_mentions_do_not_force_a_catalog(self):
        result = analyze_pages([{
            "url": "https://news.example.org/stories/two-vendors",
            "title": "Two vendors expand their partnership",
            "description": "Companies such as Alpha Labs and Beta Systems are expanding in the region.",
            "text": "Companies such as Alpha Labs and Beta Systems are expanding in the region.",
            "lane": "general", "robots_allowed": True, "fetched_at": 1700000000,
        }], source_total=1)
        self.assertEqual(result["metrics"]["unique_records"], 2)
        self.assertEqual(result["metrics"]["unique_catalogs_discovered"], 0)
        self.assertEqual(result["metrics"]["unassigned_records"], 2)

    def test_multi_page_derived_collections_are_labeled_as_referenced_not_source_declared(self):
        pages = [
            {
                "url": "https://agency.example.org/story/one",
                "title": "Places mentioned in regional planning",
                "description": "Places such as Alpha Park and Beta Garden are included in the survey.",
                "text": "Places such as Alpha Park and Beta Garden are included in the survey.",
                "lane": "general", "robots_allowed": True, "fetched_at": 1700000000,
            },
            {
                "url": "https://agency.example.org/story/two",
                "title": "More regional planning",
                "description": "Places such as Gamma Park and Delta Garden are included in the follow-up.",
                "text": "Places such as Gamma Park and Delta Garden are included in the follow-up.",
                "lane": "general", "robots_allowed": True, "fetched_at": 1700000100,
            },
        ]
        result = analyze_pages(pages, source_total=2)
        derived = [x for x in result["catalogs"] if x["catalog_origin"] == "derived_from_repeated_records"]
        self.assertTrue(derived)
        self.assertFalse(derived[0]["is_source_declared_catalog"])
        self.assertIn("referenced in indexed pages", derived[0]["title"])
        self.assertEqual(derived[0]["source_page_count"], 2)
        self.assertEqual(derived[0]["first_seen_at"], "2023-11-14T22:13:20Z")

    def test_explicit_catalogs_and_records_are_marked_unverified_until_source_review(self):
        result = analyze_pages([{
            "url": "https://shop.example/catalog",
            "title": "Product catalog",
            "description": "Products include Rolex Submariner and Omega Seamaster.",
            "text": "",
            "lane": "general", "robots_allowed": True,
        }], source_total=1)
        catalog = result["catalogs"][0]
        self.assertEqual(catalog["catalog_origin"], "source_declared_collection")
        self.assertEqual(catalog["catalog_status"], "candidate_needs_source_validation")
        self.assertFalse(catalog["verified"])
        self.assertEqual(catalog["source_terms_status"], "not_reviewed")
        self.assertFalse(catalog["commercial_eligible"])
        self.assertTrue(all(x["source_terms_status"] == "not_reviewed" for x in result["records"]))
        self.assertTrue(all(x["commercial_eligible"] is False for x in result["records"]))


    def test_structured_product_metadata_creates_brand_records_and_searchable_catalogs(self):
        pages = [
            {
                "url": "https://shop.example/watches/126000",
                "title": "Oyster Perpetual 36",
                "description": "Rolex watch model.",
                "text": "Precision watch details.",
                "lane": "shopping", "robots_allowed": True, "fetched_at": 100,
                "product": {
                    "type": "product", "name": "Oyster Perpetual 36", "brand": "Rolex",
                    "sku": "126000", "price": "6500", "currency": "USD",
                    "availability": "InStock", "image": "https://cdn.shop.example/oyster.jpg",
                    "url": "https://shop.example/watches/126000",
                },
            },
            {
                "url": "https://shop.example/watches/126500",
                "title": "Cosmograph Daytona",
                "description": "Rolex watch model.",
                "text": "Chronograph watch details.",
                "lane": "shopping", "robots_allowed": True, "fetched_at": 101,
                "product": {
                    "type": "product", "name": "Cosmograph Daytona", "brand": "Rolex",
                    "sku": "126500", "price": "15500", "currency": "USD",
                    "availability": "InStock", "image": "https://cdn.shop.example/daytona.jpg",
                    "url": "https://shop.example/watches/126500",
                },
            },
        ]
        single = analyze_pages(pages[:1], source_total=1)
        self.assertEqual(single["metrics"]["unique_catalogs_discovered"], 0)
        self.assertEqual(single["metrics"]["unique_records_by_type"], {"brand": 1, "model": 1})
        self.assertEqual(search_catalog_network("Rolex", single), [])

        result = analyze_pages(pages, source_total=2)
        self.assertEqual(result["metrics"]["structured_product_pages"], 2)
        self.assertEqual(result["metrics"]["structured_product_records"], 2)
        self.assertEqual(result["metrics"]["structured_brand_records"], 1)
        self.assertEqual(result["metrics"]["unique_records_by_type"], {"brand": 1, "model": 2})
        hits = search_catalog_network("Rolex", result)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["record_count"], 2)
        self.assertTrue(all(x["attributes"]["brand"] == "Rolex" for x in hits[0]["matched_records"]))
        self.assertTrue(all(len(x["record_ids"]) == 2 for x in result["pages"]))

    def test_structured_products_deduplicate_across_hosts_with_brand_and_sku(self):
        pages = []
        for host in ("shop-one.example", "shop-two.example"):
            for index, (sku, name, price) in enumerate((
                ("126000", "Oyster Perpetual 36", "6500"),
                ("126500", "Cosmograph Daytona", "15500"),
            )):
                pages.append({
                    "url": f"https://{host}/watches/{sku}",
                    "title": name, "description": "Rolex watch model.",
                    "text": "Structured retailer listing.", "lane": "shopping",
                    "robots_allowed": True, "fetched_at": 100 + index,
                    "product": {
                        "type": "product", "name": name, "brand": "Rolex",
                        "sku": sku, "price": price, "currency": "USD",
                    },
                })
        result = analyze_pages(pages, source_total=4)
        self.assertEqual(result["metrics"]["unique_records_by_type"], {"brand": 1, "model": 2})
        product = next(
            x for x in result["records"]
            if x["record_type"] == "model" and x["attributes"].get("sku") == "126000"
        )
        brand = next(x for x in result["records"] if x["record_type"] == "brand")
        self.assertEqual(len(product["source_urls"]), 2)
        self.assertEqual(len(product["source_page_ids"]), 2)
        self.assertEqual(len(product["catalog_ids"]), 2)
        self.assertEqual(len(brand["source_urls"]), 4)

    def test_incremental_analysis_merges_global_product_ids_across_host_partitions(self):
        pages = []
        for host in ("shop-one.example", "shop-two.example"):
            for index, (sku, name, price) in enumerate((
                ("126000", "Oyster Perpetual 36", "6500"),
                ("126500", "Cosmograph Daytona", "15500"),
            )):
                pages.append({
                    "url": f"https://{host}/watches/{sku}",
                    "title": name,
                    "description": "Rolex watch model.",
                    "text": "Structured retailer listing.",
                    "lane": "shopping",
                    "robots_allowed": True,
                    "fetched_at": 100 + index,
                    "product": {
                        "type": "product",
                        "name": name,
                        "brand": "Rolex",
                        "sku": sku,
                        "price": price,
                        "currency": "USD",
                    },
                })

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            result = analyze_pages_incremental(
                pages,
                source_total=4,
                cache_dir=output / "cache",
            )
            self.assertEqual(result["metrics"]["unique_records_by_type"], {"brand": 1, "model": 2})
            self.assertEqual(result["metrics"]["cross_host_record_merges"], 3)
            product = next(
                item for item in result["records"]
                if item["record_type"] == "model" and item["attributes"].get("sku") == "126000"
            )
            brand = next(item for item in result["records"] if item["record_type"] == "brand")
            self.assertEqual(len(product["source_urls"]), 2)
            self.assertEqual(len(product["source_page_ids"]), 2)
            self.assertEqual(len(product["catalog_ids"]), 2)
            self.assertEqual(len(brand["source_urls"]), 4)
            manifest = write_outputs(result, output / "catalogs")
            validated = validate_outputs(output / "catalogs")
            self.assertEqual(validated["records_validated"], 3)
            self.assertEqual(validated["catalogs"], 2)
            self.assertEqual(manifest["record_count"], 3)

    def test_unqualified_schema_product_is_not_promoted_to_a_product_record(self):
        result = analyze_pages([{
            "url": "https://example.org/articles/editorial-card",
            "title": "Editorial feature",
            "description": "A story card with product-like structured metadata.",
            "text": "Editorial content.", "lane": "general", "robots_allowed": True,
            "product": {"type": "product", "name": "Editorial feature", "brand": "Example",
                        "image": "https://example.org/card.jpg",
                        "url": "https://example.org/articles/editorial-card"},
        }], source_total=1)
        self.assertEqual(result["metrics"]["structured_product_pages"], 0)
        self.assertNotIn("product", result["metrics"]["unique_records_by_type"])
        self.assertNotIn("model", result["metrics"]["unique_records_by_type"])
        self.assertNotIn("brand", result["metrics"]["unique_records_by_type"])

    def test_catalog_search_prioritizes_matching_record_types_over_content_mentions(self):
        analysis = {
            "catalogs": [
                {"catalog_id": "rolex-articles", "title": "Rolex News", "catalog_type": "knowledge_base",
                 "record_type": "article", "record_ids": ["rolex-article"], "source_page_count": 1},
                {"catalog_id": "rolex-models", "title": "Rolex Models", "catalog_type": "commerce",
                 "record_type": "model", "record_ids": ["rolex-model"], "source_page_count": 1},
                {"catalog_id": "vegas-articles", "title": "Las Vegas Events", "catalog_type": "knowledge_base",
                 "record_type": "article", "record_ids": ["vegas-article"], "source_page_count": 1},
                {"catalog_id": "vegas-places", "title": "Nevada Places", "catalog_type": "geographic",
                 "record_type": "place", "record_ids": ["vegas-place"], "source_page_count": 1},
            ],
            "records": [
                {"record_id": "rolex-article", "record_type": "article", "title": "Rolex News",
                 "description": "A news article about watches.", "attributes": {}},
                {"record_id": "rolex-model", "record_type": "model", "title": "Submariner",
                 "description": "Rolex watch model.", "attributes": {"brand": "Rolex"}},
                {"record_id": "vegas-article", "record_type": "article", "title": "Las Vegas Championship",
                 "description": "Sports coverage from Las Vegas.", "attributes": {}},
                {"record_id": "vegas-place", "record_type": "place", "title": "Las Vegas Strip",
                 "description": "The Las Vegas Strip in Nevada.", "attributes": {}},
            ],
        }
        rolex_hits = search_catalog_network("Rolex", analysis)
        self.assertEqual(rolex_hits[0]["catalog_id"], "rolex-models")
        vegas_hits = search_catalog_network("Las Vegas", analysis)
        self.assertEqual(vegas_hits[0]["catalog_id"], "vegas-places")


    def test_source_policy_reviews_are_conditional_and_never_clear_record_level_rights(self):
        register = {
            "schema_version": 1,
            "reviewed_at": "2026-11-21T00:22:00Z",
            "policy": {
                "default_status": "candidate_only",
                "record_level_clearance": "not_reviewed",
                "commercial_eligibility": False,
                "republication": "not_cleared",
            },
            "reviews": [
                {
                    "host": "nist.gov",
                    "status": "source_policy_reviewed_conditional",
                    "record_level_clearance": "not_reviewed",
                    "commercial_eligibility": False,
                    "evidence_urls": ["https://www.nist.gov/copyrights-disclaimers"],
                },
                {
                    "host": "nps.gov",
                    "api_host": "developer.nps.gov",
                    "status": "source_policy_reviewed_conditional",
                    "record_level_clearance": "not_reviewed",
                    "commercial_eligibility": False,
                    "evidence_urls": ["https://www.nps.gov/aboutus/disclaimer.htm"],
                },
                {
                    "host": "census.gov",
                    "api_host": "api.census.gov",
                    "status": "api_terms_reviewed_conditional",
                    "record_level_clearance": "not_reviewed",
                    "commercial_eligibility": False,
                    "evidence_urls": ["https://www.census.gov/data/developers/about/terms-of-service.html"],
                },
            ],
        }
        analysis = {
            "records": [
                {"record_id": "nist", "source_urls": ["https://www.nist.gov/data/example"]},
                {"record_id": "mixed", "source_urls": [
                    "https://api.census.gov/data/2020/acs", "https://example.com/reference"
                ]},
                {"record_id": "spoof", "source_urls": ["https://nist.gov.example.com/data"]},
            ],
            "catalogs": [
                {"catalog_id": "nps", "source_host": "developer.nps.gov",
                 "source_url": "https://developer.nps.gov/api"}
            ],
            "metrics": {},
        }
        with tempfile.TemporaryDirectory() as tmp:
            policy_path = Path(tmp) / "source-policy-reviews.json"
            policy_path.write_text(json.dumps(register), encoding="utf-8")
            apply_source_policy_reviews(analysis, policy_path)

        records = {item["record_id"]: item for item in analysis["records"]}
        self.assertEqual(records["nist"]["source_policy_review_status"], "conditional_source_review")
        self.assertEqual(records["nist"]["source_policy_review_hosts"], ["nist.gov"])
        self.assertEqual(records["mixed"]["source_policy_review_status"], "partial_source_review")
        self.assertEqual(records["mixed"]["source_policy_review_hosts"], ["census.gov"])
        self.assertEqual(records["mixed"]["source_policy_review_unmatched_hosts"], ["example.com"])
        self.assertEqual(records["spoof"]["source_policy_review_status"], "not_reviewed")
        self.assertEqual(
            analysis["catalogs"][0]["source_policy_review_status"], "conditional_source_review"
        )
        self.assertEqual(
            analysis["metrics"]["records_with_conditional_source_policy_review"], 1
        )
        self.assertEqual(
            analysis["metrics"]["records_with_partial_source_policy_review"], 1
        )
        self.assertEqual(
            analysis["metrics"]["catalogs_with_conditional_source_policy_review"], 1
        )
        self.assertEqual(
            analysis["metrics"]["source_terms_reviewed_records"], 0
        )
        self.assertEqual(
            analysis["metrics"]["commercially_eligible_records"], 0
        )
        for item in analysis["records"] + analysis["catalogs"]:
            self.assertEqual(item["record_level_clearance"], "not_reviewed")
            self.assertEqual(item["source_terms_status"], "not_reviewed")
            self.assertFalse(item["commercial_eligible"])
            if item["source_policy_review_status"] != "not_reviewed":
                self.assertTrue(item["source_policy_review_evidence_urls"])
                self.assertEqual(item["source_policy_review_scope"], "source-level-only")

    def test_source_policy_register_rejects_any_default_clearance(self):
        register = {
            "schema_version": 1,
            "reviewed_at": "2026-11-21T00:22:00Z",
            "policy": {
                "default_status": "approved",
                "record_level_clearance": "reviewed",
                "commercial_eligibility": True,
                "republication": "cleared",
            },
            "reviews": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            policy_path = Path(tmp) / "source-policy-reviews.json"
            policy_path.write_text(json.dumps(register), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "deny-by-default"):
                apply_source_policy_reviews({"records": [], "catalogs": [], "metrics": {}}, policy_path)

if __name__ == "__main__":
    unittest.main()
