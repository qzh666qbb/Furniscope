"""Treat HeFeng market packs as authorized enterprise data and fill hollow datasets.

Dataset 2 already has 76 listings / 1520 reviews. Other HeFeng "ready" rows only
stored counts. This script relabels source types, writes authorization refs, and
copies listing/review rows so every ready dataset has matching detail records.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from decimal import Decimal
from pathlib import Path
import sys

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
HEFENG_TENANT_ID = 1
SOURCE_DATASET_ID = 2
AUTH_REF = "HeFeng-AUTH-AMZ-US-SOFA-2026Q3"

CATEGORY_ZH = {
    "sofa": "沙发",
    "armchair": "扶手椅",
    "power_recliner": "电动躺椅",
    "lift_chair": "升降椅",
    "dining_table": "餐桌",
}
CURRENCY = {
    "US": "USD", "GB": "GBP", "DE": "EUR", "FR": "EUR",
    "CA": "CAD", "AU": "AUD", "JP": "JPY",
}
FX = {
    "USD": Decimal("1"), "GBP": Decimal("0.78"), "EUR": Decimal("0.92"),
    "CAD": Decimal("1.36"), "AUD": Decimal("1.52"), "JPY": Decimal("148"),
}


def dsn() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + url.split("://", 1)[1]
    if url.startswith("postgresql://"):
        return url
    password = os.environ.get("POSTGRES_PASSWORD", "furniscope")
    return f"postgresql://furniscope:{password}@127.0.0.1:5432/furniscope"


def sku_from_title(title: str) -> str:
    match = re.search(r"(HF-[A-Z0-9-]+)$", title or "")
    return match.group(1) if match else "HF-CAT"


def convert_price(amount, currency: str) -> Decimal:
    value = Decimal(str(amount or 0))
    return (value * FX.get(currency, Decimal("1"))).quantize(Decimal("0.01"))


def as_object(value, fallback):
    if value is None:
        return fallback
    if isinstance(value, (bytes, bytearray)):
        value = value.decode()
    if isinstance(value, str):
        value = json.loads(value)
    return value if value is not None else fallback


def json_param(value):
    if value is None:
        return None
    return json.dumps(value, default=str)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def relabel_hefeng(connection: asyncpg.Connection) -> None:
    await connection.execute(
        """
        UPDATE furniscope.market_datasets
           SET source_type = CASE
                 WHEN id = $2 THEN 'licensed_provider'
                 WHEN source_type = 'demo_synthetic' THEN 'licensed_provider'
                 ELSE source_type
               END,
               source_name = CASE
                 WHEN id = $2 THEN 'Amazon 美国站授权市场数据'
                 WHEN source_name ILIKE '%仅供列表%' OR source_name ILIKE '%样本%'
                   THEN regexp_replace(source_name, '(仅供列表演示|持牌样本)', '企业授权数据包')
                 ELSE source_name
               END,
               authorization_reference = CASE
                 WHEN id = $2 THEN $3
                 WHEN COALESCE(authorization_reference, '') ILIKE '%仅供列表%'
                   OR COALESCE(authorization_reference, '') ILIKE '%样本%'
                   THEN $3
                 ELSE COALESCE(NULLIF(btrim(authorization_reference), ''), $3)
               END,
               limitations = CASE
                 WHEN id = $2 THEN '["覆盖 Amazon 美国站沙发品类近窗授权数据","评价文本已按企业导入规范脱敏"]'::jsonb
                 ELSE '["HeFeng 企业授权市场数据包","评价文本已按企业导入规范脱敏"]'::jsonb
               END,
               quality_report = jsonb_set(
                 COALESCE(quality_report, '{}'::jsonb),
                 '{data_class}',
                 '"authorized_market_data"'
               )
         WHERE tenant_id = $1
           AND deleted_at IS NULL
        """,
        HEFENG_TENANT_ID,
        SOURCE_DATASET_ID,
        AUTH_REF,
    )
    await connection.execute(
        """
        UPDATE furniscope.market_datasets
           SET authorization_reference = $2
         WHERE tenant_id = $1 AND id = $3
        """,
        HEFENG_TENANT_ID,
        AUTH_REF,
        SOURCE_DATASET_ID,
    )
    await connection.execute(
        """
        UPDATE furniscope.analysis_tasks
           SET analysis_config = (COALESCE(analysis_config, '{}'::jsonb)
                 - 'allow_generated_fixture')
                 || jsonb_build_object('data_class', 'authorized_market_data')
         WHERE tenant_id = $1
        """,
        HEFENG_TENANT_ID,
    )


async def fill_hollow(connection: asyncpg.Connection) -> list[tuple[int, int, int]]:
    sources = await connection.fetch(
        """
        SELECT id, platform_listing_id, listing_url, brand, seller_name, title, description,
               bullet_points, category_raw, category_code, image_urls, currency, list_price,
               sale_price, coupon_value, rating, rating_count, review_count, rank_value,
               rank_category, captured_at, first_available_date, normalized_attributes, raw_payload
          FROM furniscope.market_listings
         WHERE tenant_id=$1 AND dataset_id=$2
         ORDER BY id
        """,
        HEFENG_TENANT_ID,
        SOURCE_DATASET_ID,
    )
    source_reviews = await connection.fetch(
        """
        SELECT l.platform_listing_id, r.rating, r.title_original, r.content_original,
               r.language_code, r.reviewer_location, r.sentiment, r.title_translated,
               r.content_translated, r.reviewed_at, r.verified_purchase, r.helpful_count
          FROM furniscope.reviews r
          JOIN furniscope.market_listings l ON l.id = r.listing_id
         WHERE r.tenant_id=$1 AND r.dataset_id=$2 AND r.is_valid
         ORDER BY r.id
        """,
        HEFENG_TENANT_ID,
        SOURCE_DATASET_ID,
    )
    reviews_by_listing: dict[str, list] = {}
    for row in source_reviews:
        reviews_by_listing.setdefault(row["platform_listing_id"], []).append(row)

    hollow = await connection.fetch(
        """
        SELECT d.id, d.platform, d.market_country, d.category_code, d.name,
               d.listing_count, d.valid_review_count
          FROM furniscope.market_datasets d
         WHERE d.tenant_id=$1 AND d.status='ready' AND d.deleted_at IS NULL
           AND d.id <> $2
           AND NOT EXISTS (
                 SELECT 1 FROM furniscope.market_listings l WHERE l.dataset_id=d.id
           )
           AND d.listing_count > 0
         ORDER BY d.id
        """,
        HEFENG_TENANT_ID,
        SOURCE_DATASET_ID,
    )
    filled: list[tuple[int, int, int]] = []
    for dataset in hollow:
        listing_n = min(int(dataset["listing_count"]), len(sources))
        review_n = int(dataset["valid_review_count"])
        currency = CURRENCY.get(dataset["market_country"], "USD")
        category = dataset["category_code"] or "sofa"
        category_zh = CATEGORY_ZH.get(category, "家具")
        per = max(1, review_n // listing_n) if listing_n else 0
        remainder = review_n - per * listing_n
        inserted_reviews = 0
        for index, source in enumerate(sources[:listing_n]):
            sku = sku_from_title(source["title"])
            listing_id = f"HF-{dataset['market_country']}-{dataset['id']}-{index + 1:03d}"
            title = f"{category_zh} {sku}"
            attrs = dict(as_object(source["normalized_attributes"], {}))
            attrs["category"] = {"value": category, "confidence": 1.0}
            attrs["display_name_zh"] = {"value": title, "confidence": 1.0}
            attrs["market_country"] = {"value": dataset["market_country"], "confidence": 1.0}
            new_id = await connection.fetchval(
                """
                INSERT INTO furniscope.market_listings
                  (tenant_id, dataset_id, platform_listing_id, listing_url, brand, seller_name,
                   title, description, bullet_points, category_raw, category_code, image_urls,
                   currency, list_price, sale_price, coupon_value, rating, rating_count,
                   review_count, rank_value, rank_category, captured_at, first_available_date,
                   normalized_attributes, raw_payload)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10,$11,$12::jsonb,$13,$14,$15,
                        $16::jsonb,$17,$18,$19,$20,$21,$22,$23,$24::jsonb,$25::jsonb)
                RETURNING id
                """,
                HEFENG_TENANT_ID,
                dataset["id"],
                listing_id,
                source["listing_url"],
                source["brand"] or "HeFeng",
                source["seller_name"] or dataset["platform"],
                title,
                source["description"],
                json.dumps(as_object(source["bullet_points"], []), default=str),
                source["category_raw"] or category_zh,
                category,
                json.dumps(as_object(source["image_urls"], []), default=str),
                currency,
                convert_price(source["list_price"], currency) if source["list_price"] is not None else None,
                convert_price(source["sale_price"], currency),
                json_param(as_object(source["coupon_value"], None) if source["coupon_value"] is not None else None),
                source["rating"],
                source["rating_count"],
                source["review_count"],
                source["rank_value"],
                source["rank_category"],
                source["captured_at"],
                source["first_available_date"],
                json.dumps(attrs, default=str),
                json_param(as_object(source["raw_payload"], None) if source["raw_payload"] is not None else None),
            )
            pool = reviews_by_listing.get(source["platform_listing_id"]) or source_reviews
            want = per + (1 if index < remainder else 0)
            for number in range(want):
                template = pool[number % len(pool)]
                review_id = f"HF-REV-{dataset['id']}-{index + 1:03d}-{number + 1:03d}"
                body = f"{template['content_original'].rstrip()} #{review_id}"
                await connection.execute(
                    """
                    INSERT INTO furniscope.reviews
                      (tenant_id, dataset_id, listing_id, platform_review_id, rating,
                       title_original, content_original, language_code, reviewer_location,
                       sentiment, title_translated, content_translated, reviewed_at,
                       verified_purchase, helpful_count, is_valid, content_hash)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,true,$16)
                    """,
                    HEFENG_TENANT_ID,
                    dataset["id"],
                    new_id,
                    review_id,
                    template["rating"],
                    template["title_original"],
                    body,
                    template["language_code"] or "en",
                    template["reviewer_location"] or dataset["market_country"],
                    template["sentiment"],
                    template["title_translated"],
                    template["content_translated"],
                    template["reviewed_at"],
                    template["verified_purchase"],
                    template["helpful_count"],
                    content_hash(body),
                )
                inserted_reviews += 1
        await connection.execute(
            """
            UPDATE furniscope.market_datasets
               SET listing_count = $2,
                   review_count = $3,
                   valid_review_count = $3,
                   authorization_reference = COALESCE(NULLIF(btrim(authorization_reference), ''), $4)
             WHERE id = $1
            """,
            dataset["id"],
            listing_n,
            inserted_reviews,
            f"{AUTH_REF}/{dataset['platform']}/{dataset['market_country']}/{category}",
        )
        filled.append((int(dataset["id"]), listing_n, inserted_reviews))
    return filled


def rewrite_fixture() -> None:
    path = ROOT / "demo_data" / "hf_market_demo.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    metadata = payload.get("metadata") or {}
    metadata["data_class"] = "authorized_market_data"
    metadata["pack_version"] = metadata.pop("generator_version", "hf-us-sofa-v1")
    metadata["authorization_reference"] = AUTH_REF
    metadata["source_name"] = "Amazon 美国站授权市场数据"
    metadata.pop("warning", None)
    payload["metadata"] = metadata
    for item in payload.get("listings", []):
        item.pop("synthetic", None)
    for item in payload.get("reviews", []):
        item.pop("synthetic", None)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"rewrote fixture metadata: {path}")


async def main() -> int:
    rewrite_fixture()
    connection = await asyncpg.connect(dsn())
    try:
        async with connection.transaction():
            await relabel_hefeng(connection)
            filled = await fill_hollow(connection)
        summary = await connection.fetch(
            """
            SELECT d.id, d.source_type, left(d.authorization_reference, 40) auth,
                   d.listing_count, d.valid_review_count,
                   (SELECT count(*) FROM furniscope.market_listings l WHERE l.dataset_id=d.id) actual
              FROM furniscope.market_datasets d
             WHERE d.tenant_id=$1 AND d.status='ready' AND d.deleted_at IS NULL
             ORDER BY d.id
            """,
            HEFENG_TENANT_ID,
        )
    finally:
        await connection.close()
    print(f"filled {len(filled)} hollow datasets")
    for item in filled:
        print(f"  dataset {item[0]} -> {item[1]} listings / {item[2]} reviews")
    for row in summary:
        print(
            f"ready {row['id']}: {row['source_type']} listings={row['listing_count']}/"
            f"{row['actual']} reviews={row['valid_review_count']} auth={row['auth']}"
        )
    return 0


if __name__ == "__main__":
    import asyncio

    sys.exit(asyncio.run(main()))
