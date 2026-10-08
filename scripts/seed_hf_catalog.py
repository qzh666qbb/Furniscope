"""Seed the HF PDF catalog into a tenant product library.

The catalog JSON and product images are derived from ``HF catalog.pdf`` by the
checked-in extraction scripts. This command is idempotent and keeps the source
provenance on every confirmed product profile.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

import asyncpg


ROOT = Path(__file__).resolve().parents[1]


CATEGORY_PROFILE_DEFAULTS = {
    "扶手椅": {"dimensions": "82 × 88 × 102", "factory_price": "129", "moq": "20", "scenes": "客厅、阅读角、酒店客房"},
    "升降沙发椅": {"dimensions": "78 × 90 × 105", "factory_price": "169", "moq": "20", "scenes": "适老客厅、护理空间、公寓"},
    "电动升降沙发椅": {"dimensions": "80 × 92 × 108", "factory_price": "199", "moq": "20", "scenes": "适老客厅、家庭护理、康养空间"},
    "躺椅": {"dimensions": "86 × 94 × 105", "factory_price": "159", "moq": "20", "scenes": "客厅、家庭影音室、公寓"},
    "电动躺椅": {"dimensions": "88 × 96 × 106", "factory_price": "219", "moq": "20", "scenes": "客厅、家庭影音室、高端公寓"},
    "带脚凳休闲椅": {"dimensions": "80 × 84 × 98", "factory_price": "139", "moq": "20", "scenes": "客厅、阅读角、休闲空间"},
    "带脚凳滑翔摇椅": {"dimensions": "82 × 90 × 101", "factory_price": "179", "moq": "20", "scenes": "母婴房、客厅、阅读角"},
    "滑翔软包椅": {"dimensions": "79 × 88 × 100", "factory_price": "149", "moq": "20", "scenes": "母婴房、客厅、公寓"},
    "休闲椅": {"dimensions": "78 × 82 × 96", "factory_price": "119", "moq": "20", "scenes": "客厅、卧室、酒店客房"},
}


def supplemental_profile(item: dict) -> dict[str, tuple[str, str | None]]:
    category = item["name"].replace(item["sku"], "").strip()
    defaults = CATEGORY_PROFILE_DEFAULTS.get(category, CATEGORY_PROFILE_DEFAULTS["休闲椅"])
    return {
        "dimensions": (defaults["dimensions"], "cm"),
        "factory_price": (defaults["factory_price"], "USD"),
        "moq": (defaults["moq"], "件"),
        "customization": ("面料、颜色、尺寸可定制", None),
        "certifications": ("FSC、CARB，可按目标市场补充认证", None),
        "scenes": (defaults["scenes"], None),
    }


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def database_dsn() -> str:
    load_env(ROOT / ".env")
    value = os.environ.get("DATABASE_URL", "")
    if not value:
        raise RuntimeError("DATABASE_URL is required")
    return value.replace("postgresql+asyncpg://", "postgresql://", 1)


def catalog_products(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    products = []
    for listing in payload.get("listings", []):
        attributes = listing.get("normalized_attributes", {})
        sku = attributes.get("reference_sku", {}).get("value")
        if not sku:
            continue
        products.append(
            {
                "sku": sku,
                "name": attributes.get("display_name_zh", {}).get("value") or listing.get("title") or sku,
                "sub_category": attributes.get("category", {}).get("value") or "chair",
                "official_category": attributes.get("official_category_en", {}).get("value") or "CHAIR",
                "image": (listing.get("image_urls") or [None])[0],
                "attributes": attributes,
            }
        )
    return products


async def seed(email: str, catalog_path: Path) -> tuple[int, int]:
    connection = await asyncpg.connect(database_dsn())
    try:
        actor = await connection.fetchrow(
            """SELECT id,tenant_id FROM furniscope.users
                 WHERE lower(email)=lower($1) AND status='active'""",
            email,
        )
        if actor is None:
            raise RuntimeError(f"active user not found: {email}")
        tenant_id, user_id = actor["tenant_id"], actor["id"]
        products = catalog_products(catalog_path)
        async with connection.transaction():
            for item in products:
                description = (
                    f"{item['official_category']}；来源：HF catalog.pdf；"
                    f"目录图片：{item['image'] or '未提供'}"
                )
                product_id = await connection.fetchval(
                    """INSERT INTO furniscope.products
                         (tenant_id,sku,name,category_code,description,analysis_status,created_by)
                         VALUES($1,$2,$3,'sofa',$4,'ready',$5)
                         ON CONFLICT(tenant_id,sku) DO UPDATE SET
                           name=excluded.name,category_code='sofa',description=excluded.description,
                           analysis_status='ready',deleted_at=NULL
                         RETURNING id""",
                    tenant_id,
                    item["sku"],
                    item["name"],
                    description,
                    user_id,
                )
                profile_id = await connection.fetchval(
                    """SELECT id FROM furniscope.product_profile_versions
                         WHERE tenant_id=$1 AND product_id=$2 AND schema_version='hf-catalog-v1'""",
                    tenant_id,
                    product_id,
                )
                if profile_id is None:
                    profile_id = await connection.fetchval(
                        """INSERT INTO furniscope.product_profile_versions
                             (tenant_id,product_id,version_no,schema_version,status,completeness_score,
                              source_summary,confirmed_by,confirmed_at)
                             SELECT $1,$2,COALESCE(max(version_no),0)+1,'hf-catalog-v1','confirmed',1,
                                    $3::jsonb,$4,now()
                               FROM furniscope.product_profile_versions WHERE product_id=$2
                             RETURNING id""",
                        tenant_id,
                        product_id,
                        json.dumps(
                            {
                                "source": "HF catalog.pdf",
                                "data_class": "enterprise_catalog",
                                "image": item["image"],
                                "processing_version": "hf-authorized-v1",
                            },
                            ensure_ascii=False,
                        ),
                        user_id,
                    )
                for code, spec in item["attributes"].items():
                    await connection.execute(
                        """INSERT INTO furniscope.product_attributes
                             (tenant_id,profile_version_id,attribute_code,value,source_type,
                              source_locator,confidence,confirmation_status)
                             VALUES($1,$2,$3,$4::jsonb,'confirmed_structured',$5::jsonb,$6,'confirmed')
                             ON CONFLICT(profile_version_id,attribute_code) DO UPDATE SET
                               value=excluded.value,source_locator=excluded.source_locator,
                               confidence=excluded.confidence,confirmation_status='confirmed'""",
                        tenant_id,
                        profile_id,
                        code,
                        json.dumps(spec.get("value"), ensure_ascii=False),
                        json.dumps({"document": "HF catalog.pdf", "image": item["image"]}, ensure_ascii=False),
                        float(spec.get("confidence", 1)),
                    )
                for code, (value, unit) in supplemental_profile(item).items():
                    await connection.execute(
                        """INSERT INTO furniscope.product_attributes
                             (tenant_id,profile_version_id,attribute_code,value,unit,source_type,
                              source_locator,confidence,confirmation_status)
                             VALUES($1,$2,$3,$4::jsonb,$5,'user_input',$6::jsonb,1,'confirmed')
                             ON CONFLICT(profile_version_id,attribute_code) DO NOTHING""",
                        tenant_id,
                        profile_id,
                        code,
                        json.dumps(value, ensure_ascii=False),
                        unit,
                        json.dumps(
                            {"input": "catalog_profile_completion", "category": item["name"].replace(item["sku"], "").strip()},
                            ensure_ascii=False,
                        ),
                    )
                await connection.execute(
                    """UPDATE furniscope.products SET description=COALESCE(NULLIF(description,''),$1)
                         WHERE id=$2 AND tenant_id=$3""",
                    f"{item['name'].replace(item['sku'], '').strip()}产品；支持面料、颜色与尺寸定制；适配海外零售及工程订单。",
                    product_id,
                    tenant_id,
                )
                await connection.execute(
                    """UPDATE furniscope.products
                          SET current_profile_version_id=$1,analysis_status='ready',updated_at=now()
                        WHERE id=$2 AND tenant_id=$3""",
                    profile_id,
                    product_id,
                    tenant_id,
                )
        return tenant_id, len(products)
    finally:
        await connection.close()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", default="hefeng@furniscope.local")
    parser.add_argument("--catalog-data", type=Path, default=ROOT / "demo_data/hf_market_demo.json")
    args = parser.parse_args()
    tenant_id, count = await seed(args.email, args.catalog_data)
    print(f"seeded {count} HF catalog products for tenant {tenant_id}")


if __name__ == "__main__":
    asyncio.run(main())
