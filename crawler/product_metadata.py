"""Extract conservative product metadata from HTML for Everything Hub search."""
from html.parser import HTMLParser
import json
from urllib.parse import urljoin, urlsplit

MAX_JSON_LD_BYTES = 1_000_000
MAX_JSON_LD_BLOCKS = 20


def _text(value, limit=500):
    if not isinstance(value, (str, int, float)):
        return ""
    return " ".join(str(value).split())[:limit]


def _absolute_http(value, base_url):
    value = _text(value, 2048)
    if not value:
        return ""
    try:
        url = urljoin(base_url, value)
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return ""
        if parsed.username is not None or parsed.password is not None:
            return ""
        return url
    except ValueError:
        return ""


class _MetadataParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta = {}
        self.json_blocks = []
        self._json_active = False
        self._json_parts = []

    def handle_starttag(self, tag, attrs):
        attrs = {str(k).lower(): v for k, v in attrs}
        if tag.lower() == "meta":
            key = (attrs.get("property") or attrs.get("name") or attrs.get("itemprop") or "").lower()
            content = attrs.get("content")
            if key and content and key not in self.meta:
                self.meta[key] = _text(content, 2048)
        elif tag.lower() == "script" and (attrs.get("type") or "").lower() == "application/ld+json":
            if len(self.json_blocks) < MAX_JSON_LD_BLOCKS:
                self._json_active = True
                self._json_parts = []

    def handle_data(self, data):
        if self._json_active:
            self._json_parts.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "script" and self._json_active:
            raw = "".join(self._json_parts)
            if len(raw.encode("utf-8", errors="ignore")) <= MAX_JSON_LD_BYTES:
                self.json_blocks.append(raw)
            self._json_active = False
            self._json_parts = []


def _objects(value):
    if isinstance(value, list):
        for item in value:
            yield from _objects(item)
    elif isinstance(value, dict):
        graph = value.get("@graph")
        if isinstance(graph, list):
            yield from _objects(graph)
        yield value


def _is_product(obj):
    types = obj.get("@type", [])
    if isinstance(types, str):
        types = [types]
    return any(str(t).lower().rsplit("/", 1)[-1] == "product" for t in types)


def extract_product_metadata(html, page_url):
    """Return a compact product object, or {} when no Product evidence exists."""
    parser = _MetadataParser()
    parser.feed(html[:2_000_000])
    product = None
    for raw in parser.json_blocks:
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            continue
        for obj in _objects(payload):
            if _is_product(obj):
                product = obj
                break
        if product:
            break

    meta = parser.meta
    if not product and not any(k in meta for k in ("product:price:amount", "product:brand", "og:type")):
        return {}
    product = product or {}

    brand = product.get("brand") or parser.meta.get("product:brand") or {}
    if isinstance(brand, dict):
        brand = brand.get("name", "")
    offers = product.get("offers") or {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    if not isinstance(offers, dict):
        offers = {}
    image = product.get("image") or meta.get("og:image") or meta.get("twitter:image")
    if isinstance(image, list):
        image = image[0] if image else ""
    if isinstance(image, dict):
        image = image.get("url") or image.get("@id") or ""
    availability = offers.get("availability", "")
    if isinstance(availability, str):
        availability = availability.rsplit("/", 1)[-1]

    sku = _text(product.get("sku") or product.get("mpn"), 120)
    gtin = _text(
        product.get("gtin14") or product.get("gtin13") or
        product.get("gtin12") or product.get("gtin8") or product.get("gtin"),
        32,
    )
    price = _text(offers.get("price") or meta.get("product:price:amount"), 64)
    # Product schema is also used for editorial cards and article metadata.
    # Require a commerce identifier, price, or explicit availability.
    if not any((sku, gtin, price, _text(availability, 80))):
        return {}

    result = {
        "type": "product",
        "name": _text(product.get("name") or meta.get("og:title") or meta.get("twitter:title")),
        "brand": _text(brand, 160),
        "sku": sku,
        "gtin": gtin,
        "price": price,
        "currency": _text(offers.get("priceCurrency") or meta.get("product:price:currency"), 8),
        "availability": _text(availability, 80),
        "image": _absolute_http(image, page_url),
        "url": _absolute_http(product.get("url") or page_url, page_url),
    }
    return {k: v for k, v in result.items() if v}
