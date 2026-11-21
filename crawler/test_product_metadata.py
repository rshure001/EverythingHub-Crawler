import unittest

from product_metadata import extract_product_metadata, extract_structured_catalog_metadata


class ProductMetadataTests(unittest.TestCase):
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

if __name__ == "__main__":
    unittest.main()
