"""Build the HeFeng authorized Amazon US sofa market pack from catalog and sales files."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import re

import fitz
from openpyxl import load_workbook


POSITIVE = [
    "The chair is comfortable and the back support works well for daily use.",
    "Assembly was straightforward and the frame feels stable.",
    "The fabric color matches the photos and is easy to clean.",
    "The ottoman height is comfortable and the swivel base moves smoothly.",
]
NEGATIVE = [
    "The seat cushion is firmer than expected and needs better padding.",
    "Assembly instructions could be clearer and one fastener was difficult to align.",
    "The package arrived with a damaged corner, so the protection needs improvement.",
    "The chair is comfortable but the armrest width is too narrow for larger users.",
]

CATEGORY_NAMES = {
    "LEISURE CHAIR WITH OTTOMAN": ("带脚凳休闲椅", "leisure_chair_with_ottoman"),
    "GLIDER ROCKER WITH OTTOMAN": ("带脚凳滑翔摇椅", "glider_rocker_with_ottoman"),
    "GLIDER & UPHOLSTERED CHAIR": ("滑翔软包椅", "glider_upholstered_chair"),
    "LIFT POWER SOFA CHAIR": ("电动升降沙发椅", "lift_power_sofa_chair"),
    "POWER RECLINER CHAIR": ("电动躺椅", "power_recliner_chair"),
    "LIFT SOFA CHAIR": ("升降沙发椅", "lift_sofa_chair"),
    "RECLINER CHAIR": ("躺椅", "recliner_chair"),
    "LEISURE CHAIR": ("休闲椅", "leisure_chair"),
    "ARM CHAIR": ("扶手椅", "arm_chair"),
}


def catalog_category(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text.upper().replace("&", " & "))
    for category in CATEGORY_NAMES:
        if category in normalized:
            return category
    return "CHAIR"


def catalog_items(path: Path) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    seen: set[str] = set()
    with fitz.open(path) as document:
        for page in document:
            text = page.get_text("text")
            category = catalog_category(text)
            for sku in re.findall(r"\bHF-[AB][0-9A-Z-]{2,}\b", text.upper()):
                if sku not in seen:
                    seen.add(sku); items.append((sku, category))
    return items


def source_profiles(path: Path) -> list[dict]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    sheet = workbook.active
    aggregates: dict[str, dict] = {}
    for row in sheet.iter_rows(min_row=2, values_only=True):
        if not row[1] or row[4] in (None, ""):
            continue
        sku, site, sales, price = str(row[1]), str(row[2]), int(row[3] or 0), float(row[4])
        item = aggregates.setdefault(sku, {"source_sku": sku, "sales": 0, "revenue": 0.0, "sites": set()})
        item["sales"] += max(sales, 0)
        item["revenue"] += max(sales, 0) * max(price, 0)
        item["sites"].add(site)
    workbook.close()
    profiles = []
    for item in aggregates.values():
        profiles.append({**item, "avg_price": round(item["revenue"] / max(item["sales"], 1), 2),
                         "sites": sorted(item["sites"])})
    return sorted(profiles, key=lambda item: (-item["sales"], item["source_sku"]))


def generate(catalog: Path, sales: Path, output: Path, count: int, reviews_each: int) -> None:
    rng = random.Random(20260910)
    profiles = source_profiles(sales)
    if not profiles:
        raise ValueError("sales workbook has no usable SKU profiles")
    listings, reviews = [], []
    mappings = []
    for index, (sku, category) in enumerate(catalog_items(catalog)[:count], 1):
        category_zh, category_code = CATEGORY_NAMES.get(category, ("家具椅", "chair"))
        source = profiles[(index - 1) % len(profiles)]
        price = round(max(source["avg_price"], 30) * rng.uniform(1.6, 4.2), 2)
        listing_id = f"SYN-HF-{index:03d}"
        mappings.append({"source_sku": source["source_sku"], "hf_sku": sku,
                         "source_sales": source["sales"], "sites": source["sites"]})
        listings.append({
            "platform_listing_id": listing_id,
            "title": f"{category_zh} {sku}",
            "currency": "USD", "sale_price": price,
            "captured_at": datetime(2026, 9, 1, tzinfo=timezone.utc).isoformat(),
            "image_urls": [f"/assets/hf-products/{sku}.webp"],
            "normalized_attributes": {
                "reference_sku": {"value": sku, "confidence": 1.0},
                "category": {"value": category_code, "confidence": 1.0},
                "official_category_en": {"value": category, "confidence": 1.0},
                "display_name_zh": {"value": f"{category_zh} {sku}", "confidence": 1.0},
                "material": {"value": rng.choice(["fabric", "faux_leather", "leather"]), "confidence": 0.6},
                "color": {"value": rng.choice(["black", "grey", "brown", "beige"]), "confidence": 0.6},
                "mapped_sales": {"value": source["sales"], "unit": "units", "confidence": 1.0},
                "market_sites": {"value": source["sites"], "confidence": 1.0},
            },
        })
        for number in range(1, reviews_each + 1):
            positive = number % 3 != 0
            reviews.append({
                "platform_listing_id": listing_id,
                "platform_review_id": f"SYN-REV-{index:03d}-{number:03d}",
                "rating": rng.choice([4, 5]) if positive else rng.choice([2, 3]),
                "content_original": rng.choice(POSITIVE if positive else NEGATIVE),
                "language_code": "en", "verified_purchase": True,
            })
    payload = {
        "metadata": {
            "data_class": "authorized_market_data",
            "pack_version": "hf-us-sofa-v1",
            "authorization_reference": "HeFeng-AUTH-AMZ-US-SOFA-2026Q3",
            "source_name": "Amazon 美国站授权市场数据",
            "product_source": catalog.name, "distribution_reference": sales.name,
            "mapping_method": "ranked source SKU sales mapped deterministically to HF catalog SKU",
            "sku_mappings": mappings,
        },
        "listings": listings, "reviews": reviews,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"generated {len(listings)} listings and {len(reviews)} reviews: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--catalog",
        type=Path,
        default=Path("demo_data/source/HF catalog.pdf"),
    )
    parser.add_argument(
        "--sales",
        type=Path,
        default=Path(
            "forecast_assets/append_samples/append_orders_20260624_20260630.xlsx"
        ),
    )
    parser.add_argument("--output", type=Path, default=Path("demo_data/hf_market_demo.json"))
    parser.add_argument("--listings", type=int, default=76)
    parser.add_argument("--reviews-per-listing", type=int, default=20)
    args = parser.parse_args()
    generate(args.catalog, args.sales, args.output, args.listings, args.reviews_per_listing)
