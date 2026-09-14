from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from furniscope_api.services.web_signal_parser import parse_html_signal_page


def test_product_and_nested_reviews_are_parsed_from_json_ld() -> None:
    html = """
    <html><head><script type="application/ld+json">
    {"@type":"Product","name":"Modular Sofa","sku":"B0ABC12345",
     "image":["https://cdn.example/sofa.jpg"],
     "offers":{"price":"399.90","priceCurrency":"USD"},
     "aggregateRating":{"ratingValue":"4.4","reviewCount":"128"},
     "review":[{"@type":"Review","@id":"R-1","reviewBody":"Comfortable and sturdy.",
                "reviewRating":{"ratingValue":"5"},"datePublished":"2026-09-01"}]}
    </script></head></html>
    """
    result = parse_html_signal_page(
        html, page_url="https://www.amazon.com/dp/B0ABC12345", source_kind="amazon_product_page",
    )
    assert result["listings"][0]["platform_listing_id"] == "B0ABC12345"
    assert result["listings"][0]["sale_price"] == 399.9
    assert result["listings"][0]["review_count"] == 128
    assert result["reviews"][0]["external_id"] == "R-1"
    assert result["reviews"][0]["rating"] == 5


def test_amazon_dom_fallback_parses_listing_and_review_page() -> None:
    html = """
    <span id="productTitle">Convertible Sleeper Sofa</span>
    <span class="a-price-whole">529</span><span class="a-price-fraction">99</span>
    <span id="acrPopover" title="4.2 out of 5 stars"></span>
    <span id="acrCustomerReviewText">87 ratings</span>
    <div data-hook="review" id="R2">
      <i data-hook="review-star-rating">2.0 out of 5 stars</i>
      <span data-hook="review-date">Reviewed in the United States on September 2, 2026</span>
      <span data-hook="review-body">The frame squeaks after one week.</span>
    </div>
    """
    result = parse_html_signal_page(
        html, page_url="https://www.amazon.com/product-reviews/B0XYZ98765",
        source_kind="amazon_product_page",
    )
    assert result["listings"][0]["sale_price"] == 529.99
    assert result["listings"][0]["rating"] == 4.2
    assert result["reviews"][0]["external_id"] == "R2"
    assert result["reviews"][0]["content_original"] == "The frame squeaks after one week."


def test_generic_external_review_page_accepts_standalone_review_json_ld() -> None:
    html = """<script type="application/ld+json">
      [{"@type":"Review","identifier":"OFFSITE-1","reviewBody":"Assembly was easy and fast.",
        "reviewRating":{"ratingValue":4},"datePublished":"2026-08-31"}]
    </script>"""
    result = parse_html_signal_page(
        html, page_url="https://reviews.example.com/furniture/42", source_kind="web_review_page",
    )
    assert result["listings"] == []
    assert result["reviews"][0]["external_id"] == "OFFSITE-1"
    assert result["reviews"][0]["rating"] == 4
