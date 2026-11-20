import unittest

from product_metadata import extract_product_metadata


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

    def test_ignores_non_product_and_unsafe_urls(self):
        html = """<script type="application/ld+json">
        {"@type":"Article","name":"Not a product","image":"javascript:alert(1)"}
        </script>"""
        self.assertEqual(extract_product_metadata(html, "https://example.com/a"), {})

    def test_rejects_credential_bearing_image_urls(self):
        html = """<meta property="og:type" content="product">
        <meta property="og:image" content="https://user:pass@example.com/image.jpg">"""
        self.assertNotIn("image", extract_product_metadata(html, "https://example.com/p"))


if __name__ == "__main__":
    unittest.main()
