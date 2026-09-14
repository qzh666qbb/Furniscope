"""Parse product and review facts from authorized public HTML pages.

The parser intentionally uses only the Python standard library.  JSON-LD is the
preferred contract; a small Amazon DOM fallback keeps product/review pages useful
when structured data is incomplete.
"""

from __future__ import annotations

from html.parser import HTMLParser
import json
import re
from typing import Any, Iterable


_ASIN_RE = re.compile(r"/(?:dp|gp/product|product-reviews)/([A-Z0-9]{10})(?:[/?]|$)", re.I)
_NUMBER_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        value = value.get("name") or value.get("text")
    cleaned = re.sub(r"\s+", " ", str(value)).strip()
    return cleaned or None


def _number(value: Any) -> float | None:
    match = _NUMBER_RE.search(str(value or ""))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def _nodes(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, list):
        for item in value:
            yield from _nodes(item)
    elif isinstance(value, dict):
        graph = value.get("@graph")
        if isinstance(graph, list):
            yield from _nodes(graph)
        yield value


def _types(node: dict[str, Any]) -> set[str]:
    value = node.get("@type")
    values = value if isinstance(value, list) else [value]
    return {str(item).lower() for item in values if item}


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.json_ld: list[str] = []
        self.meta: dict[str, str] = {}
        self._script_depth: int | None = None
        self._script_parts: list[str] = []
        self._captures: list[dict[str, Any]] = []
        self.fields: dict[str, list[str]] = {}
        self.product_images: list[str] = []
        self.reviews: list[dict[str, Any]] = []
        self._review: dict[str, Any] | None = None
        self._review_depth: int | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.depth += 1
        attr = {key.lower(): value or "" for key, value in attrs}
        if tag == "meta":
            key = (attr.get("property") or attr.get("name") or "").lower()
            if key and attr.get("content"):
                self.meta[key] = attr["content"]
        if tag == "script" and "ld+json" in attr.get("type", "").lower():
            self._script_depth = self.depth
            self._script_parts = []

        hook = attr.get("data-hook", "").lower()
        element_id = attr.get("id", "")
        classes = set(attr.get("class", "").lower().split())
        if hook == "review" and self._review is None:
            self._review = {"external_id": attr.get("id") or None}
            self._review_depth = self.depth

        field = None
        if element_id == "productTitle" or "product-title" in classes:
            field = "product_title"
        elif element_id == "acrCustomerReviewText":
            field = "review_count"
        elif element_id == "feature-bullets" or "a-list-item" in classes:
            field = "bullet_points"
        elif "a-price-whole" in classes:
            field = "price_whole"
        elif "a-price-fraction" in classes:
            field = "price_fraction"
        elif self._review is not None and hook in {"review-body", "review-collapsed"}:
            field = "review_body"
        elif self._review is not None and hook in {"review-star-rating", "cmps-review-star-rating"}:
            field = "review_rating"
        elif self._review is not None and hook == "review-date":
            field = "review_date"
        if field:
            self._captures.append({"tag": tag, "depth": self.depth, "field": field, "parts": []})

        if element_id == "landingImage" or hook == "review-image-tile":
            image = attr.get("data-old-hi") or attr.get("data-a-dynamic-image") or attr.get("src")
            if image and image.startswith("{"):
                try:
                    image = next(iter(json.loads(image)))
                except (ValueError, StopIteration, TypeError):
                    image = None
            if image:
                self.product_images.append(image)
        if element_id == "acrPopover" and attr.get("title"):
            self.fields.setdefault("rating", []).append(attr["title"])

    def handle_data(self, data: str) -> None:
        if self._script_depth is not None:
            self._script_parts.append(data)
        for capture in self._captures:
            capture["parts"].append(data)

    def handle_endtag(self, tag: str) -> None:
        for capture in list(self._captures):
            if capture["tag"] == tag and capture["depth"] == self.depth:
                value = _text(" ".join(capture["parts"]))
                if value:
                    if self._review is not None and capture["field"].startswith("review_"):
                        self._review[capture["field"].removeprefix("review_")] = value
                    else:
                        self.fields.setdefault(capture["field"], []).append(value)
                self._captures.remove(capture)
        if tag == "script" and self._script_depth == self.depth:
            body = "".join(self._script_parts).strip()
            if body:
                self.json_ld.append(body)
            self._script_depth = None
            self._script_parts = []
        if self._review is not None and self._review_depth == self.depth:
            if self._review.get("body"):
                self.reviews.append(self._review)
            self._review = None
            self._review_depth = None
        self.depth = max(0, self.depth - 1)


def _jsonld_payload(parser: _PageParser, page_url: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    products: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []
    seen_reviews: set[str] = set()
    asin_from_url = (_ASIN_RE.search(page_url).group(1).upper() if _ASIN_RE.search(page_url) else None)
    for raw in parser.json_ld:
        try:
            document = json.loads(raw)
        except (ValueError, TypeError):
            continue
        for node in _nodes(document):
            node_types = _types(node)
            if "product" in node_types:
                offers = node.get("offers") or {}
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                aggregate = node.get("aggregateRating") or {}
                images = node.get("image") or []
                if isinstance(images, str):
                    images = [images]
                asin = _text(node.get("sku") or node.get("productID")) or asin_from_url
                products.append({
                    "platform_listing_id": asin,
                    "title": _text(node.get("name")),
                    "sale_price": _number(offers.get("price") or offers.get("lowPrice")),
                    "list_price": _number(offers.get("highPrice")),
                    "currency": _text(offers.get("priceCurrency")),
                    "rating": _number(aggregate.get("ratingValue")),
                    "review_count": int(_number(aggregate.get("reviewCount") or aggregate.get("ratingCount")) or 0),
                    "image_urls": [str(item) for item in images if item],
                    "bullet_points": [],
                    "source_url": page_url,
                })
                nested_reviews = node.get("review") or []
                if isinstance(nested_reviews, dict):
                    nested_reviews = [nested_reviews]
                review_nodes = nested_reviews
            elif "review" in node_types:
                review_nodes = [node]
            else:
                review_nodes = []
            for review in review_nodes:
                body = _text(review.get("reviewBody") or review.get("description"))
                if not body:
                    continue
                key = _text(review.get("@id") or review.get("identifier")) or body
                if key in seen_reviews:
                    continue
                seen_reviews.add(key)
                rating = review.get("reviewRating") or {}
                reviews.append({
                    "external_id": _text(review.get("@id") or review.get("identifier")),
                    "asin": asin_from_url,
                    "rating": _number(rating.get("ratingValue") if isinstance(rating, dict) else rating),
                    "content_original": body,
                    "reviewed_at": _text(review.get("datePublished")),
                    "source_url": page_url,
                })
    return products, reviews


def parse_html_signal_page(html: str, *, page_url: str, source_kind: str) -> dict[str, list[dict[str, Any]]]:
    """Return the same listings/reviews envelope accepted by JSON connectors."""
    parser = _PageParser()
    parser.feed(html)
    listings, reviews = _jsonld_payload(parser, page_url)
    asin_match = _ASIN_RE.search(page_url)
    asin = asin_match.group(1).upper() if asin_match else None

    if source_kind == "amazon_product_page" and not listings:
        whole = (parser.fields.get("price_whole") or [None])[0]
        fraction = (parser.fields.get("price_fraction") or [None])[0]
        price = _number(f"{whole or ''}.{fraction or '00'}") if whole else None
        title = _text((parser.fields.get("product_title") or [None])[0]) or _text(parser.meta.get("og:title"))
        if title or asin:
            listings.append({
                "platform_listing_id": asin,
                "title": title,
                "sale_price": price,
                "currency": "USD",
                "rating": _number((parser.fields.get("rating") or [None])[0]),
                "review_count": int(_number((parser.fields.get("review_count") or [None])[0]) or 0),
                "image_urls": parser.product_images or ([parser.meta["og:image"]] if parser.meta.get("og:image") else []),
                "bullet_points": parser.fields.get("bullet_points", []),
                "source_url": page_url,
            })

    existing = {(item.get("external_id"), item.get("content_original")) for item in reviews}
    for index, item in enumerate(parser.reviews, start=1):
        body = _text(item.get("body"))
        key = (item.get("external_id"), body)
        if not body or key in existing:
            continue
        reviews.append({
            "external_id": item.get("external_id") or f"{asin or 'web'}-{index}",
            "asin": asin,
            "rating": _number(item.get("rating")),
            "content_original": body,
            "reviewed_at": item.get("date"),
            "source_url": page_url,
        })
    return {"listings": listings, "reviews": reviews}
