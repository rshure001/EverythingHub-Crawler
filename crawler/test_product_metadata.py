import unittest

from product_metadata import (
    extract_product_metadata, extract_structured_catalog_metadata,
    has_structured_catalog_markup,
)


class ProductMetadataTests(unittest.TestCase):
    def test_structured_catalog_markup_guard_covers_all_supported_containers(self):
        for schema_type in ("ItemList", "DataCatalog", "CollectionPage", "ProductGroup", "OfferCatalog"):
            with self.subTest(schema_type=schema_type):
                html = '<script type="application/ld+json">{"@type":"' + schema_type + '"}</script>'
                self.assertTrue(has_structured_catalog_markup(html))
        self.assertFalse(has_structured_catalog_markup("<html><body>ordinary content</body></html>"))
        self.assertFalse(has_structured_catalog_markup(None))

    def test_extracts_schema_product_fields(self):
        html = """<script type="application/ld+json">{
          "@context":"https://schema.org","@type":"Product",
          "name":"Oyster Perpetual 36","brand":{"@type":"Brand","name":"Rolex"},
          "sku":"126000","image":"/media/watch.jpg",
          "offers":{"price":"6500","priceCurrency":"USD","availability":"https://schema.org/InStock"}
        }</script>"""
        result = extract_product_metadata(html, "https://shop.example/watches/126000")
        self.assertEqual(result["type"], "product")
        self.assertEqual(result["brand"], "Rolex")
        self.assertEqual(result["sku"], "126000")
        self.assertEqual(result["price"], "6500")
        self.assertEqual(result["currency"], "USD")
        self.assertEqual(result["availability"], "InStock")
        self.assertEqual(result["image"], "https://shop.example/media/watch.jpg")

    def test_supports_graph_and_meta_fallback(self):
        html = """<meta property="og:type" content="product">
        <meta property="og:title" content="Diamond ring">
        <meta property="og:image" content="/ring.jpg">
        <meta property="product:price:amount" content="1299">
        <meta property="product:price:currency" content="USD">"""
        result = extract_product_metadata(html, "https://shop.example/ring")
        self.assertEqual(result["name"], "Diamond ring")
        self.assertEqual(result["price"], "1299")
        self.assertEqual(result["image"], "https://shop.example/ring.jpg")

    def test_rejects_schema_product_without_commerce_identifiers_or_offer(self):
        html = """<script type="application/ld+json">{
          "@context":"https://schema.org","@type":"Product",
          "name":"Editorial feature card","brand":{"@type":"Brand","name":"Example"}
        }</script>"""
        self.assertEqual(extract_product_metadata(html, "https://example.com/story"), {})

    def test_accepts_gtin_without_price(self):
        html = """<script type="application/ld+json">{
          "@context":"https://schema.org","@type":"Product",
          "name":"Example watch","brand":{"@type":"Brand","name":"Example"},
          "gtin13":"1234567890123"
        }</script>"""
        result = extract_product_metadata(html, "https://shop.example/watch")
        self.assertEqual(result["gtin"], "1234567890123")
        self.assertEqual(result["type"], "product")

    def test_ignores_non_product_and_unsafe_urls(self):
        html = """<script type="application/ld+json">
        {"@type":"Article","name":"Not a product","image":"javascript:alert(1)"}
        </script>"""
        self.assertEqual(extract_product_metadata(html, "https://example.com/a"), {})

    def test_rejects_credential_bearing_image_urls(self):
        html = """<meta property="og:type" content="product">
        <meta property="og:image" content="https://user:pass@example.com/image.jpg">"""
        self.assertNotIn("image", extract_product_metadata(html, "https://example.com/p"))



    def test_extracts_typed_item_list_with_direct_item_urls(self):
        html = """<script type="application/ld+json">{
          "@context":"https://schema.org","@type":"ItemList","name":"Rolex Watches",
          "itemListElement":[
            {"@type":"ListItem","position":1,"item":{"@type":"Product","name":"Submariner","url":"/watches/submariner","sku":"124060","brand":{"@type":"Brand","name":"Rolex"}}},
            {"@type":"ListItem","position":2,"item":{"@type":"Product","name":"Datejust","url":"/watches/datejust","sku":"126200","brand":{"@type":"Brand","name":"Rolex"}}}
          ]
        }</script>"""
        result = extract_structured_catalog_metadata(html, "https://shop.example/watches")
        self.assertEqual(result["version"], 1)
        self.assertEqual(len(result["lists"]), 1)
        self.assertEqual(result["lists"][0]["name"], "Rolex Watches")
        self.assertEqual(result["lists"][0]["record_type"], "product")
        self.assertEqual(len(result["lists"][0]["items"]), 2)
        self.assertEqual(result["lists"][0]["items"][0]["url"], "https://shop.example/watches/submariner")
        self.assertEqual(result["lists"][0]["items"][0]["brand"], "Rolex")
        self.assertEqual(result["lists"][0]["items"][1]["sku"], "126200")

    def test_item_list_requires_two_typed_unique_items_and_is_size_bounded(self):
        single = """<script type="application/ld+json">{
          "@type":"ItemList","name":"One item",
          "itemListElement":[{"@type":"ListItem","item":{"@type":"Product","name":"Only","url":"/only"}}]
        }</script>"""
        self.assertEqual(extract_structured_catalog_metadata(single, "https://shop.example/"), {})

        many_items = [{"@type":"ListItem","position":i,"item":{
            "@type":"Product","name":"Product " + str(i),
            "url":"/products/" + str(i)
        }} for i in range(1, 30)]
        import json
        html = '<script type="application/ld+json">' + json.dumps({
            "@type":"ItemList","name":"Products","itemListElement":many_items
        }) + "</script>"
        result = extract_structured_catalog_metadata(html, "https://shop.example/")
        self.assertTrue(result["lists"])
        self.assertLessEqual(len(json.dumps(result, separators=(",", ":")).encode("utf-8")), 1024)

    def test_extracts_only_stable_same_as_identifiers(self):
        html = """<script type="application/ld+json">{
          "@context":"https://schema.org","@type":"Product",
          "name":"Submariner","sku":"124060",
          "sameAs":["https://www.wikidata.org/entity/Q12345","https://untrusted.example/id/1"],
          "brand":{"@type":"Brand","name":"Rolex","sameAs":"https://www.wikidata.org/wiki/Q456"}
        }</script>"""
        result = extract_product_metadata(html, "https://shop.example/watches/submariner")
        self.assertEqual(result["same_as_ids"], ["wikidata:Q12345"])
        self.assertEqual(result["brand_same_as_ids"], ["wikidata:Q456"])

    def test_structured_item_lists_preserve_stable_same_as_identifiers(self):
        html = """<script type="application/ld+json">{
          "@context":"https://schema.org","@type":"ItemList","name":"Watch models",
          "itemListElement":[
            {"@type":"ListItem","item":{"@type":"Product","name":"Submariner","url":"/submariner","sameAs":"https://www.wikidata.org/entity/Q12345"}},
            {"@type":"ListItem","item":{"@type":"Product","name":"Datejust","url":"/datejust","sameAs":"https://www.wikidata.org/entity/Q23456"}}
          ]
        }</script>"""
        result = extract_structured_catalog_metadata(html, "https://shop.example/watches")
        self.assertEqual(result["lists"][0]["items"][0]["same_as_ids"], ["wikidata:Q12345"])
        self.assertEqual(result["lists"][0]["items"][1]["same_as_ids"], ["wikidata:Q23456"])


    def test_extracts_data_catalog_dataset_records_without_fetching_them(self):
        import json
        payload = {
            "@context": "https://schema.org",
            "@type": "DataCatalog",
            "name": "Nevada Public Datasets",
            "dataset": [
                {"@type": "Dataset", "name": "Business Licenses", "url": "/datasets/licenses"},
                {"@type": "Dataset", "name": "Building Permits", "url": "/datasets/permits"},
            ],
        }
        html = '<script type="application/ld+json">' + json.dumps(payload) + "</script>"
        result = extract_structured_catalog_metadata(html, "https://data.example/catalog")
        self.assertEqual(len(result["lists"]), 1)
        self.assertEqual(result["lists"][0]["record_type"], "dataset")
        self.assertEqual(
            [item["url"] for item in result["lists"][0]["items"]],
            [
                "https://data.example/datasets/licenses",
                "https://data.example/datasets/permits",
            ],
        )

    def test_extracts_collection_page_place_records(self):
        import json
        payload = {
            "@context": "https://schema.org",
            "@type": "CollectionPage",
            "name": "Las Vegas Attractions",
            "mainEntity": [
                {"@type": "TouristAttraction", "name": "Fremont Street", "url": "/places/fremont"},
                {"@type": "Museum", "name": "Neon Museum", "url": "/places/neon"},
            ],
        }
        html = '<script type="application/ld+json">' + json.dumps(payload) + "</script>"
        result = extract_structured_catalog_metadata(html, "https://visit.example/las-vegas")
        self.assertEqual(len(result["lists"]), 1)
        self.assertEqual(result["lists"][0]["record_type"], "place")
        self.assertEqual(len(result["lists"][0]["items"]), 2)

    def test_extracts_product_group_variants(self):
        import json
        payload = {
            "@context": "https://schema.org",
            "@type": "ProductGroup",
            "name": "Submariner Variants",
            "hasVariant": [
                {"@type": "Product", "name": "Submariner No Date", "url": "/watches/no-date", "sku": "124060"},
                {"@type": "Product", "name": "Submariner Date", "url": "/watches/date", "sku": "126610"},
            ],
        }
        html = '<script type="application/ld+json">' + json.dumps(payload) + "</script>"
        result = extract_structured_catalog_metadata(html, "https://shop.example/watches/submariner")
        self.assertEqual(len(result["lists"]), 1)
        self.assertEqual(result["lists"][0]["record_type"], "product")
        self.assertEqual(len(result["lists"][0]["items"]), 2)

    def test_extracts_offer_catalog_service_records(self):
        import json
        payload = {
            "@context": "https://schema.org",
            "@type": "OfferCatalog",
            "name": "Watch Services",
            "itemOffered": [
                {"@type": "Service", "name": "Watch repair", "url": "/services/repair"},
                {"@type": "Service", "name": "Watch cleaning", "url": "/services/cleaning"},
            ],
        }
        html = '<script type="application/ld+json">' + json.dumps(payload) + "</script>"
        result = extract_structured_catalog_metadata(html, "https://shop.example/services")
        self.assertEqual(len(result["lists"]), 1)
        self.assertEqual(result["lists"][0]["record_type"], "service")
        self.assertEqual(len(result["lists"][0]["items"]), 2)

    def test_data_catalog_with_one_dataset_does_not_force_a_catalog(self):
        import json
        payload = {
            "@context": "https://schema.org",
            "@type": "DataCatalog",
            "name": "Single Dataset",
            "dataset": [
                {"@type": "Dataset", "name": "Only Dataset", "url": "/datasets/only"},
            ],
        }
        html = '<script type="application/ld+json">' + json.dumps(payload) + "</script>"
        self.assertEqual(extract_structured_catalog_metadata(html, "https://data.example/catalog"), {})


    def test_same_as_orcid_and_isni_check_digits_are_validated(self):
        html = """<script type="application/ld+json">{
          "@context":"https://schema.org","@type":"Product",
          "name":"Test watch","sku":"TEST-1",
          "sameAs":[
            "https://orcid.org/0000-0002-1825-0097",
            "https://orcid.org/0000-0002-1825-0098",
            "https://isni.org/isni/000000012281955X",
            "https://isni.org/isni/0000000122819550"
          ]
        }</script>"""
        result = extract_product_metadata(html, "https://shop.example/test-watch")
        self.assertEqual(
            result["same_as_ids"],
            ["orcid:0000-0002-1825-0097", "isni:000000012281955X"],
        )

if __name__ == "__main__":
    unittest.main()
