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
_STRUCTURED_CATALOG_MARKERS = (
    "itemlist", "datacatalog", "collectionpage", "productgroup", "offercatalog",
)


def has_structured_catalog_markup(html):
    """Return whether HTML mentions any supported schema.org collection container."""
    return isinstance(html, str) and bool(html) and any(
        marker in html.casefold() for marker in _STRUCTURED_CATALOG_MARKERS
    )


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


def _schema_reference_id(value, base_url):
    """Resolve a JSON-LD @id reference to a safe absolute HTTP(S) identifier."""
    raw = value.get("@id") if isinstance(value, dict) else value
    raw = _text(raw, 2048)
    if not raw:
        return ""
    try:
        resolved = urljoin(base_url, raw)
        parsed = urlsplit(resolved)
    except ValueError:
        return ""
    if (
        parsed.scheme.lower() not in ("http", "https")
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return ""
    return parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
    ).geturl()


def extract_structured_catalog_metadata(html, page_url):
    """Extract bounded, typed schema.org collections with direct item URLs.

    Supported containers are ItemList, DataCatalog, CollectionPage,
    ProductGroup, and OfferCatalog. This is metadata-only: item URLs are
    provenance and click-through locations; they are not fetched here.
    A candidate needs at least two uniquely linked records of one type.
    """
    if not has_structured_catalog_markup(html):
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
        schema_objects = list(_walk_schema_objects(payload))
        objects_by_id = {}
        object_priorities = {}
        for schema_obj in schema_objects:
            if not isinstance(schema_obj, dict):
                continue
            object_id = _schema_reference_id(schema_obj.get("@id"), page_url)
            if not object_id:
                continue
            # JSON-LD graphs commonly contain a small @id-only reference before
            # the fully-described node. Prefer the richer typed node so a stub
            # cannot shadow the actual Product, Dataset, Place, or other record.
            priority = (
                1 if _schema_record_type(schema_obj) else 0,
                1 if _text(schema_obj.get("name") or schema_obj.get("headline"), 120) else 0,
                1 if _absolute_http(schema_obj.get("url"), page_url) else 0,
                len(schema_obj),
            )
            if object_id not in object_priorities or priority > object_priorities[object_id]:
                objects_by_id[object_id] = schema_obj
                object_priorities[object_id] = priority

        for obj in schema_objects:
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
                elements = elements if isinstance(elements, list) else [elements]
                for element in elements[:100]:
                    wrapper = element if isinstance(element, dict) else {}
                    item = None
                    if isinstance(element, str):
                        item = objects_by_id.get(_schema_reference_id(element, page_url))
                    for item_key in ("item", "itemOffered", "dataset", "mainEntity"):
                        candidate = wrapper.get(item_key)
                        if isinstance(candidate, dict):
                            candidate_id = _schema_reference_id(candidate.get("@id"), page_url)
                            resolved = objects_by_id.get(candidate_id)
                            if resolved and not (
                                candidate.get("name") or candidate.get("headline") or candidate.get("url")
                            ):
                                item = resolved
                            else:
                                item = candidate
                            break
                        if isinstance(candidate, str):
                            resolved = objects_by_id.get(_schema_reference_id(candidate, page_url))
                            if resolved:
                                item = resolved
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


_ENTITY_SCHEMA_RELATIONSHIPS = (
    "@graph", "mainEntity", "mainEntityOfPage", "hasPart", "itemListElement",
    "item", "itemOffered", "dataset", "hasVariant", "about", "author",
    "publisher", "organizer", "location", "address", "brand", "manufacturer",
    "provider", "containedInPlace", "containedIn", "department", "member",
    "worksFor", "areaServed", "serviceArea", "offers",
)
_ENTITY_RECORD_LIMIT = 8
_ENTITY_JSON_BYTES_LIMIT = 12_000


def _walk_entity_schema_objects(value, depth=0, seen=None, budget=None):
    """Walk a bounded allowlist of JSON-LD entity relationships."""
    if seen is None:
        seen = set()
    if budget is None:
        budget = [0]
    if depth > 8 or budget[0] >= 600:
        return
    if isinstance(value, list):
        for item in value[:120]:
            yield from _walk_entity_schema_objects(item, depth + 1, seen, budget)
        return
    if not isinstance(value, dict):
        return
    marker = id(value)
    if marker in seen:
        return
    seen.add(marker)
    budget[0] += 1
    yield value
    for key in _ENTITY_SCHEMA_RELATIONSHIPS:
        if key in value:
            yield from _walk_entity_schema_objects(value[key], depth + 1, seen, budget)


def _resolve_entity_schema_object(value, objects_by_id, base_url):
    if isinstance(value, str):
        return objects_by_id.get(_schema_reference_id(value, base_url), {})
    if not isinstance(value, dict):
        return {}
    object_id = _schema_reference_id(value.get("@id"), base_url)
    resolved = objects_by_id.get(object_id) if object_id else None
    if isinstance(resolved, dict) and _schema_record_type(resolved) and len(resolved) >= len(value):
        return resolved
    return value


def _entity_schema_name(value, objects_by_id, base_url, limit=120):
    if isinstance(value, list):
        names = []
        for item in value[:3]:
            name = _entity_schema_name(item, objects_by_id, base_url, limit=limit)
            if name and name not in names:
                names.append(name)
        return "; ".join(names)[:limit]
    if isinstance(value, dict):
        resolved = _resolve_entity_schema_object(value, objects_by_id, base_url)
        return _text(
            resolved.get("name") or resolved.get("headline") or
            resolved.get("legalName") or resolved.get("value") or
            resolved.get("identifier"),
            limit,
        )
    raw = _text(value, limit)
    if raw.startswith(("http://", "https://", "#", "/")):
        resolved = objects_by_id.get(_schema_reference_id(raw, base_url))
        if isinstance(resolved, dict):
            return _text(
                resolved.get("name") or resolved.get("headline") or resolved.get("legalName"),
                limit,
            )
        if raw.startswith(("http://", "https://")):
            return ""
    return raw


def extract_structured_entity_metadata(html, page_url):
    """Extract a small, typed set of schema.org entities from one crawled page.

    This is metadata-only. It never fetches entity URLs, follows sameAs links,
    or copies contact details or street addresses. Product records require a
    commerce identifier/offer; place and business records retain locality-level
    location metadata so searches can distinguish real geographic matches.
    """
    if not isinstance(html, str) or not html or "application/ld+json" not in html.casefold():
        return []
    if not _absolute_http(page_url, page_url):
        return []
    lowered = html.casefold()
    if not any(marker in lowered for marker in _SCHEMA_RECORD_TYPES):
        return []

    parser = _MetadataParser()
    parser.feed(html[:2_000_000])
    candidates = []
    seen_entities = set()
    priority_by_type = {
        "place": 0, "business": 1, "product": 2, "model": 3, "brand": 4,
        "organization": 5, "service": 6, "dataset": 7, "software": 8,
        "event": 9, "person": 10, "article": 11,
    }

    for raw in parser.json_blocks:
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError, RecursionError):
            continue
        schema_objects = list(_walk_entity_schema_objects(payload))
        objects_by_id = {}
        object_priorities = {}
        for schema_obj in schema_objects:
            if not isinstance(schema_obj, dict):
                continue
            object_id = _schema_reference_id(schema_obj.get("@id"), page_url)
            if not object_id:
                continue
            priority = (
                1 if _schema_record_type(schema_obj) else 0,
                1 if _text(schema_obj.get("name") or schema_obj.get("headline") or schema_obj.get("legalName"), 160) else 0,
                1 if _absolute_http(schema_obj.get("url"), page_url) else 0,
                len(schema_obj),
            )
            if object_id not in object_priorities or priority > object_priorities[object_id]:
                objects_by_id[object_id] = schema_obj
                object_priorities[object_id] = priority

        for raw_obj in schema_objects:
            if not isinstance(raw_obj, dict):
                continue
            obj = _resolve_entity_schema_object(raw_obj, objects_by_id, page_url)
            record_type = _schema_record_type(obj)
            if record_type not in priority_by_type:
                continue
            name = _text(obj.get("name") or obj.get("headline") or obj.get("legalName"), 160)
            if not name:
                continue

            raw_offers = obj.get("offers")
            if isinstance(raw_offers, list):
                raw_offers = raw_offers[0] if raw_offers else {}
            offers = _resolve_entity_schema_object(raw_offers, objects_by_id, page_url)
            if not isinstance(offers, dict):
                offers = {}
            sku = _text(obj.get("sku") or obj.get("mpn"), 64)
            gtin = _text(
                obj.get("gtin14") or obj.get("gtin13") or
                obj.get("gtin12") or obj.get("gtin8") or obj.get("gtin"),
                32,
            )
            model_number = _text(obj.get("modelNumber") or obj.get("model"), 80)
            price = _text(offers.get("price") or obj.get("price"), 64)
            availability = _text(offers.get("availability") or obj.get("availability"), 80)
            if record_type == "product" and not any((sku, gtin, price, availability)):
                continue
            if record_type == "model" and not any((model_number, sku, gtin)):
                continue

            attributes = {}
            schema_types = sorted(_schema_type_names(obj))
            if schema_types:
                attributes["schema_type"] = ", ".join(schema_types[:4])

            same_as_ids = _stable_same_as_ids([obj.get("sameAs"), obj.get("@id")])
            if same_as_ids:
                attributes["same_as_ids"] = same_as_ids[:5]

            entity_url_raw = obj.get("url") or obj.get("@id")
            entity_url = _absolute_http(entity_url_raw, page_url) if entity_url_raw else ""
            if entity_url:
                attributes["entity_url"] = entity_url

            address = _resolve_entity_schema_object(obj.get("address"), objects_by_id, page_url)
            if isinstance(address, dict):
                for source_key, target_key, limit in (
                    ("addressLocality", "address_locality", 120),
                    ("addressRegion", "address_region", 80),
                    ("addressCountry", "address_country", 80),
                    ("postalCode", "postal_code", 24),
                ):
                    value = _text(address.get(source_key), limit)
                    if value:
                        attributes[target_key] = value

            location_value = obj.get("location") or obj.get("containedInPlace")
            location_name = _entity_schema_name(location_value, objects_by_id, page_url)
            if location_name:
                attributes["location_name"] = location_name

            brand_raw = obj.get("brand") or obj.get("manufacturer")
            brand_value = _resolve_entity_schema_object(brand_raw, objects_by_id, page_url)
            brand_name = _entity_schema_name(brand_raw, objects_by_id, page_url, limit=120)
            if brand_name:
                attributes["brand"] = brand_name
            if isinstance(brand_value, dict):
                brand_ids = _stable_same_as_ids([brand_value.get("sameAs"), brand_value.get("@id")])
                if brand_ids:
                    attributes["brand_same_as_ids"] = brand_ids[:5]

            simple_fields = (
                ("category", "category", 120),
                ("serviceType", "service_type", 120),
                ("jobTitle", "job_title", 120),
                ("foundingDate", "founding_date", 40),
                ("startDate", "start_date", 40),
                ("endDate", "end_date", 40),
                ("datePublished", "date_published", 40),
                ("dateModified", "date_modified", 40),
                ("eventStatus", "event_status", 100),
                ("eventAttendanceMode", "attendance_mode", 100),
                ("temporalCoverage", "temporal_coverage", 100),
                ("spatialCoverage", "spatial_coverage", 120),
                ("measurementTechnique", "measurement_technique", 120),
                ("applicationCategory", "application_category", 120),
                ("operatingSystem", "operating_system", 120),
                ("softwareVersion", "software_version", 80),
                ("programmingLanguage", "programming_language", 80),
                ("sku", "sku", 64),
                ("mpn", "mpn", 64),
                ("gtin", "gtin", 32),
                ("priceCurrency", "currency", 8),
                ("identifier", "identifier", 120),
            )
            for source_key, target_key, limit in simple_fields:
                value = (
                    _entity_schema_name(obj.get(source_key), objects_by_id, page_url, limit=limit)
                    if source_key in {"category", "identifier"}
                    else _text(obj.get(source_key), limit)
                )
                if value:
                    attributes[target_key] = value
            if sku:
                attributes["sku"] = sku
            if gtin:
                attributes["gtin"] = gtin
            if model_number:
                attributes["model_number"] = model_number
            if price:
                attributes["price"] = price
            if availability:
                availability = availability.rsplit("/", 1)[-1]
                attributes["availability"] = availability

            for source_key, target_key in (
                ("areaServed", "area_served"),
                ("provider", "provider_name"),
                ("organizer", "organizer_name"),
                ("author", "author_name"),
                ("publisher", "publisher_name"),
                ("affiliation", "affiliation"),
                ("worksFor", "works_for"),
                ("parentOrganization", "parent_organization"),
                ("license", "license"),
            ):
                value = _entity_schema_name(obj.get(source_key), objects_by_id, page_url)
                if value:
                    attributes[target_key] = value

            identity = (
                record_type,
                (same_as_ids[0] if same_as_ids else entity_url or name.casefold().strip()),
            )
            if identity in seen_entities:
                continue
            seen_entities.add(identity)

            description = _text(obj.get("description"), 500)
            candidates.append((
                priority_by_type[record_type],
                name.casefold(),
                {
                    "record_type": record_type,
                    "name": name,
                    "description": description,
                    "entity_url": entity_url,
                    "attributes": attributes,
                },
            ))

    candidates.sort(key=lambda item: (item[0], item[1]))
    entities = [item[2] for item in candidates[:_ENTITY_RECORD_LIMIT]]
    while entities and len(json.dumps(entities, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > _ENTITY_JSON_BYTES_LIMIT:
        entities.pop()
    return entities
