import json
from pathlib import Path


def test_hf_market_pack_is_complete_authorized_data():
    payload = json.loads(Path("demo_data/hf_market_demo.json").read_text(encoding="utf-8"))
    assert payload["metadata"]["data_class"] == "authorized_market_data"
    assert payload["metadata"]["authorization_reference"]
    assert payload["metadata"]["mapping_method"]
    assert len(payload["listings"]) == 76
    assert len(payload["reviews"]) == 1520
    listing_ids = {item["platform_listing_id"] for item in payload["listings"]}
    hf_skus = {item["normalized_attributes"]["reference_sku"]["value"]
               for item in payload["listings"]}
    assert len(hf_skus) == 76
    assert all(item["platform_listing_id"] in listing_ids for item in payload["reviews"])
    assert all("synthetic" not in item for item in payload["listings"] + payload["reviews"])
    image_root = Path("frontend/public")
    image_urls = [item["image_urls"][0] for item in payload["listings"]]
    assert len(set(image_urls)) == 76
    assert all((image_root / url.removeprefix("/")).is_file()
               for url in image_urls)
    assert all(url.endswith(".webp") for url in image_urls)
    assert all("inspired by" not in item["title"] for item in payload["listings"])
    assert all(item["title"].endswith(item["normalized_attributes"]["reference_sku"]["value"])
               for item in payload["listings"])
    assert all(item["normalized_attributes"]["display_name_zh"]["value"] == item["title"]
               for item in payload["listings"])
