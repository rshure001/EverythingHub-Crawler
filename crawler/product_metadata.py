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

_SCHEMA_RECORD_TYPES = {
    "product": "product",
    "individualproduct": "product",
    "productmodel": "model",
    "model": "model",
    "brand": "brand",
    "localbusiness": "business",
    "store": "business",
    "restaurant": "business",
    "hotel": "business",
    "foodestablishment": "business",
    "autodealer": "business",
    "professionalservice": "business",
    "financialservice": "business",
    "medicalbusiness": "business",
    "place": "place",
    "city": "place",
    "administrativearea": "place",
    "touristattraction": "place",
    "park": "place",
    "museum": "place",
    "civicstructure": "place",
    "landmarksorhistoricalbuildings": "place",
    "organization": "organization",
    "corporation": "organization",
    "governmentorganization": "organization",
    "educationalorganization": "organization",
    "ngo": "organization",
    "person": "person",
    "event": "event",
    "businessevent": "event",
    "festival": "event",
    "exhibitionevent": "event",
    "service": "service",
    "dataset": "dataset",
    "softwareapplication": "software",
    "article": "article",
    "newsarticle": "article",
    "blogposting": "article",
    "scholarlyarticle": "article",
    "report": "article",
    "imageobject": "media",
    "videoobject": "media",
    "audioobject": "media",
}

_MAX_STRUCTURED_LISTS = 2
_MAX_STRUCTURED_ITEMS_PER_LIST = 8
_MAX_STRUCTURED_CATALOG_BYTES = 1024


def _schema_type_names(obj):
    value = obj.get("@type") if isinstance(obj, dict) else None
    values = value if isinstance(value, list) else [value]
    names = set()
    for item in values:
        if not isinstance(item, str):
            continue
        name = item.rsplit("/", 1)[-1].rsplit("#", 1)[-1].casefold()
        if name:
            names.add(name)
    return names


def _schema_record_type(obj):
    for name in _schema_type_names(obj):
        if name in _SCHEMA_RECORD_TYPES:
            return _SCHEMA_RECORD_TYPES[name]
    return ""


def _walk_schema_objects(value, depth=0, seen=None, budget=None):
    """Walk only bounded JSON-LD relationship fields; never traverse arbitrary properties."""
    if seen is None:
        seen = set()
    if budget is None:
        budget = [0]
    if depth > 8 or budget[0] >= 1000:
        return
    if isinstance(value, list):
        for item in value[:200]:
            yield from _walk_schema_objects(item, depth + 1, seen, budget)
        return
    if not isinstance(value, dict):
        return
    marker = id(value)
    if marker in seen:
        return
    seen.add(marker)
    budget[0] += 1
    yield value
    for key in ("@graph", "mainEntity", "mainEntityOfPage", "hasPart", "itemListElement", "item", "about"):
        if key in value:
            yield from _walk_schema_objects(value[key], depth + 1, seen, budget)


def extract_structured_catalog_metadata(html, page_url):
    """Extract a bounded schema.org ItemList preview with direct item URLs.

    This is metadata-only: item URLs are recorded as provenance and are not
    fetched here. A list needs at least two typed, uniquely linked items.
    """
    if not isinstance(html, str) or not html:
        return {}
    lowered = html.casefold()
    if "itemlist" not in lowered or "itemlistelement" not in lowered:
        return {}
    if not _absolute_http(page_url, page_url):
        return {}

    parser = _MetadataParser()
    parser.feed(html[:2_000_000])
    lists = []
    seen_lists = set()

    for raw in parser.json_blocks:
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError, RecursionError):
            continue
        for obj in _walk_schema_objects(payload):
            if "itemlist" not in _schema_type_names(obj):
                continue
            list_name = _text(obj.get("name") or obj.get("headline"), 120)
            elements = obj.get("itemListElement")
            if not list_name or not isinstance(elements, list):
                continue
            list_key = list_name.casefold()
            if list_key in seen_lists:
                continue

            items = []
            seen_items = set()
            for position, element in enumerate(elements[:100], start=1):
                wrapper = element if isinstance(element, dict) else {}
                item = wrapper.get("item") if isinstance(wrapper, dict) else None
                if not isinstance(item, dict):
                    item = wrapper if wrapper else {}
                item_name = _text(item.get("name") or item.get("headline") or wrapper.get("name"), 100)
                item_url = _absolute_http(
                    item.get("url") or item.get("@id") or wrapper.get("url") or wrapper.get("@id"),
                    page_url,
                )
                record_type = _schema_record_type(item) or _schema_record_type(wrapper)
                if not item_name or not item_url or not record_type:
                    continue
                identity = (record_type, item_url.casefold())
                if identity in seen_items:
                    continue
                seen_items.add(identity)

                entry = {
                    "record_type": record_type,
                    "name": item_name,
                    "url": item_url,
                }
                sku = _text(item.get("sku") or item.get("mpn"), 48)
                gtin = _text(
                    item.get("gtin14") or item.get("gtin13") or
                    item.get("gtin12") or item.get("gtin8") or item.get("gtin"),
                    32,
                )
                brand = item.get("brand") or item.get("manufacturer") or ""
                if isinstance(brand, dict):
                    brand = brand.get("name") or ""
                brand = _text(brand, 48)
                if sku:
                    entry["sku"] = sku
                if gtin:
                    entry["gtin"] = gtin
                if brand:
                    entry["brand"] = brand
                raw_position = wrapper.get("position")
                if isinstance(raw_position, int) and not isinstance(raw_position, bool) and raw_position > 0:
                    entry["position"] = raw_position
                items.append(entry)

            # Do not turn a one-off mention or a malformed list into a catalog.
            type_counts = {}
            for item in items:
                type_counts[item["record_type"]] = type_counts.get(item["record_type"], 0) + 1
            dominant_type, count = max(type_counts.items(), key=lambda pair: (pair[1], pair[0]), default=("", 0))
            if count < 2:
                continue
            items = [item for item in items if item["record_type"] == dominant_type]
            items = items[:_MAX_STRUCTURED_ITEMS_PER_LIST]
            if len(items) < 2:
                continue
            lists.append({
                "name": list_name,
                "record_type": dominant_type,
                "items": items,
            })
            seen_lists.add(list_key)
            if len(lists) >= _MAX_STRUCTURED_LISTS:
                break
        if len(lists) >= _MAX_STRUCTURED_LISTS:
            break

    result = {"version": 1, "lists": lists}
    # Keep the canonical index within a fixed per-page metadata budget.
    while lists and len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > _MAX_STRUCTURED_CATALOG_BYTES:
        largest = max(range(len(lists)), key=lambda index: len(lists[index]["items"]))
        if len(lists[largest]["items"]) > 2:
            lists[largest]["items"].pop()
        elif len(lists) > 1:
            lists.pop()
        else:
            return {}
        result = {"version": 1, "lists": lists}
    return result if lists else {}

