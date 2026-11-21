"""Extract conservative product metadata from HTML for Everything Hub search."""
from html.parser import HTMLParser
import json
import re
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


def _valid_mod_11_2(value):
    """Validate ORCID/ISNI ISO 7064 MOD 11-2 check digits."""
    compact = str(value or "").replace("-", "").upper()
    if not re.fullmatch(r"\d{15}[\dX]", compact):
        return False
    total = 0
    for digit in compact[:15]:
        total = (total + int(digit)) * 2
    check = (12 - (total % 11)) % 11
    expected = "X" if check == 10 else str(check)
    return compact[-1] == expected


def _stable_same_as_ids(value):
    """Keep only stable, namespaced HTTPS entity identifiers from schema.org sameAs."""
    pending = [value]
    found = []
    seen = set()
    while pending and len(found) < 5:
        current = pending.pop(0)
        if isinstance(current, list):
            pending.extend(current[:20])
            continue
        if isinstance(current, dict):
            pending.extend([current.get("@id"), current.get("url")])
            continue
        if not isinstance(current, str) or not current.strip():
            continue
        try:
            parsed = urlsplit(current.strip())
        except ValueError:
            continue
        if (
            parsed.scheme.lower() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            continue
        host = parsed.hostname.lower().rstrip(".")
        path = parsed.path.rstrip("/")
        identity = ""
        if host in {"www.wikidata.org", "wikidata.org"}:
            match = re.fullmatch(r"/(?:entity|wiki)/(Q[1-9]\d*)", path, re.I)
            if match:
                identity = "wikidata:" + match.group(1).upper()
        elif host in {"orcid.org", "www.orcid.org"}:
            match = re.fullmatch(r"/(\d{4}-\d{4}-\d{4}-\d{3}[\dXx])", path)
            if match and _valid_mod_11_2(match.group(1)):
                identity = "orcid:" + match.group(1).upper()
        elif host == "viaf.org":
            match = re.fullmatch(r"/viaf/(\d+)", path)
            if match:
                identity = "viaf:" + match.group(1)
        elif host in {"isni.org", "www.isni.org"}:
            match = re.fullmatch(r"/isni/(\d{15}[\dXx])", path)
            if match and _valid_mod_11_2(match.group(1)):
                identity = "isni:" + match.group(1).upper()
        elif host in {"geonames.org", "www.geonames.org"}:
            match = re.fullmatch(r"/(\d+)", path)
            if match:
                identity = "geonames:" + match.group(1)
        elif host in {"openstreetmap.org", "www.openstreetmap.org"}:
            match = re.fullmatch(r"/(node|way|relation)/(\d+)", path)
            if match:
                identity = "osm:" + match.group(1) + ":" + match.group(2)
        if identity and identity not in seen:
            seen.add(identity)
            found.append(identity)
    return found


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

    brand_value = product.get("brand") or parser.meta.get("product:brand") or {}
    brand_same_as_ids = _stable_same_as_ids(
        [brand_value.get("sameAs"), brand_value.get("@id")]
    ) if isinstance(brand_value, dict) else []
    brand = brand_value.get("name", "") if isinstance(brand_value, dict) else brand_value
    same_as_ids = _stable_same_as_ids([product.get("sameAs"), product.get("@id")])
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
        "same_as_ids": same_as_ids,
        "brand_same_as_ids": brand_same_as_ids,
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
    """Walk bounded JSON-LD relationship fields; never traverse arbitrary properties."""
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
    for key in (
        "@graph", "mainEntity", "mainEntityOfPage", "hasPart",
        "itemListElement", "item", "itemOffered", "dataset",
        "hasVariant", "about",
    ):
        if key in value:
            yield from _walk_schema_objects(value[key], depth + 1, seen, budget)


def _structured_container_relationships(obj):
    """Return only schema.org relationships that explicitly represent collections."""
    types = _schema_type_names(obj)
    relationships = []
    if "itemlist" in types:
        relationships.append(("itemlist", "itemListElement", obj.get("itemListElement")))
    if "datacatalog" in types:
        relationships.append(("datacatalog", "dataset", obj.get("dataset")))
    if "collectionpage" in types:
        for key in ("mainEntity", "hasPart"):
            relationships.append(("collectionpage", key, obj.get(key)))
    if "productgroup" in types:
        relationships.append(("productgroup", "hasVariant", obj.get("hasVariant")))
    if "offercatalog" in types:
        for key in ("itemListElement", "itemOffered"):
            relationships.append(("offercatalog", key, obj.get(key)))
    return relationships


def extract_structured_catalog_metadata(html, page_url):
    """Extract bounded, typed schema.org collections with direct item URLs.

    Supported containers are ItemList, DataCatalog, CollectionPage,
    ProductGroup, and OfferCatalog. This is metadata-only: item URLs are
    provenance and click-through locations; they are not fetched here.
    A candidate needs at least two uniquely linked records of one type.
    """
    if not isinstance(html, str) or not html:
        return {}
    lowered = html.casefold()
    if not any(marker in lowered for marker in (
        "itemlist", "datacatalog", "collectionpage", "productgroup", "offercatalog"
    )):
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
            if not isinstance(obj, dict):
                continue
            list_name = _text(obj.get("name") or obj.get("headline"), 120)
            if not list_name:
                continue

            for container_type, relationship, elements in _structured_container_relationships(obj):
                if not isinstance(elements, list):
                    continue
                items = []
                seen_items = set()
                for element in elements[:100]:
                    wrapper = element if isinstance(element, dict) else {}
                    item = None
                    for item_key in ("item", "itemOffered", "dataset", "mainEntity"):
                        candidate = wrapper.get(item_key)
                        if isinstance(candidate, dict):
                            item = candidate
                            break
                    if item is None:
                        item = wrapper
                    item_name = _text(
                        item.get("name") or item.get("headline") or wrapper.get("name"), 100
                    )
                    item_url = _absolute_http(
                        item.get("url") or item.get("@id") or
                        wrapper.get("url") or wrapper.get("@id"),
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
                    brand_value = item.get("brand") or item.get("manufacturer") or ""
                    brand_same_as_ids = _stable_same_as_ids(
                        [brand_value.get("sameAs"), brand_value.get("@id")]
                    ) if isinstance(brand_value, dict) else []
                    brand = (
                        brand_value.get("name") or ""
                        if isinstance(brand_value, dict) else brand_value
                    )
                    brand = _text(brand, 48)
                    same_as_ids = _stable_same_as_ids([item.get("sameAs"), item.get("@id")])
                    if sku:
                        entry["sku"] = sku
                    if gtin:
                        entry["gtin"] = gtin
                    if brand:
                        entry["brand"] = brand
                    if same_as_ids:
                        entry["same_as_ids"] = same_as_ids
                    if brand_same_as_ids:
                        entry["brand_same_as_ids"] = brand_same_as_ids
                    raw_position = wrapper.get("position")
                    if isinstance(raw_position, int) and not isinstance(raw_position, bool) and raw_position > 0:
                        entry["position"] = raw_position
                    items.append(entry)

                type_counts = {}
                for item in items:
                    type_counts[item["record_type"]] = type_counts.get(item["record_type"], 0) + 1
                dominant_type, count = max(
                    type_counts.items(), key=lambda pair: (pair[1], pair[0]), default=("", 0)
                )
                if count < 2:
                    continue
                items = [item for item in items if item["record_type"] == dominant_type]
                items = items[:_MAX_STRUCTURED_ITEMS_PER_LIST]
                if len(items) < 2:
                    continue

                list_key = container_type + "|" + list_name.casefold() + "|" + dominant_type
                if list_key in seen_lists:
                    continue
                seen_lists.add(list_key)
                lists.append({
                    "name": list_name,
                    "record_type": dominant_type,
                    "items": items,
                })
                if len(lists) >= _MAX_STRUCTURED_LISTS:
                    break
            if len(lists) >= _MAX_STRUCTURED_LISTS:
                break
        if len(lists) >= _MAX_STRUCTURED_LISTS:
            break

    result = {"version": 1, "lists": lists}
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
