"""Build a traceable five-capability market decision baseline for one tenant."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
from statistics import median
from typing import Any

from sqlalchemy import text

from furniscope_agent.enterprise_decision import enterprise_fit, score_opportunity
from furniscope_api.config import get_settings
from furniscope_api.database import Database
from furniscope_api.services.market_intelligence import MarketIntelligenceService


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PACKAGE = (
    ROOT
    / "data"
    / "market_intelligence"
    / "hefeng_market_decision_baseline_v1.json"
)
REQUIRED_MIGRATION = "v3_36_market_intelligence_governance"


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _canonical(value: Any) -> str:
    return json.dumps(
        _jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256(value: Any) -> str:
    payload = value if isinstance(value, bytes) else _canonical(value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _clamp(value: float, low: float = 0, high: float = 100) -> float:
    return round(max(low, min(high, value)), 2)


def load_package(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    payload = json.loads(raw)
    required = {
        "schema_version",
        "package_id",
        "package_version",
        "tenant_selector",
        "dataset_selector",
        "source_contract",
        "capabilities",
        "governance",
    }
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError(f"package is missing required keys: {','.join(missing)}")
    capability_keys = {
        "smart_selection",
        "competitor_tracking",
        "review_mining",
        "pricing",
        "compliance",
    }
    missing_capabilities = sorted(capability_keys - set(payload["capabilities"]))
    if missing_capabilities:
        raise ValueError(
            "package is missing capabilities: " + ",".join(missing_capabilities)
        )
    weights = payload["capabilities"]["smart_selection"]["weights"]
    if abs(sum(float(value) for value in weights.values()) - 1) > 1e-9:
        raise ValueError("smart selection weights must sum to one")
    if not payload["capabilities"]["review_mining"]["taxonomies"]:
        raise ValueError("at least one review taxonomy is required")
    if not payload["capabilities"]["compliance"]["sources"]:
        raise ValueError("at least one official policy source is required")
    return payload, hashlib.sha256(raw).hexdigest()


def classify_review(
    content: str,
    rating: float | None,
    config: dict[str, Any],
) -> tuple[list[dict[str, str]], str]:
    lowered = content.lower()
    matched: list[dict[str, str]] = []
    for taxonomy in config["taxonomies"]:
        if any(str(keyword).lower() in lowered for keyword in taxonomy["keywords"]):
            matched.append({"code": taxonomy["code"], "name": taxonomy["name"]})
    if not matched:
        matched = [{"code": "general_experience", "name": "整体使用体验"}]

    positive = any(str(term).lower() in lowered for term in config["positive_terms"])
    negative = any(str(term).lower() in lowered for term in config["negative_terms"])
    if positive and negative:
        sentiment = "mixed"
    elif negative or (rating is not None and rating <= 2):
        sentiment = "negative"
    elif positive or (rating is not None and rating >= 4):
        sentiment = "positive"
    else:
        sentiment = "neutral"
    return matched[:3], sentiment


async def _lineage(
    session,
    *,
    tenant_id: int,
    batch_id: int,
    capability_code: str,
    record_kind: str,
    source_class: str,
    target_table: str,
    target_record_id: int | None,
    natural_key: str,
    source_locator: dict[str, Any],
    input_value: Any,
    payload_value: Any,
    derivation_rule: str | None = None,
    rule_version: str | None = None,
    confidence: float | None = None,
    observed_at: datetime | None = None,
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO market_intelligence_lineage(
              tenant_id,batch_id,capability_code,record_kind,source_class,
              target_table,target_record_id,natural_key,source_locator,
              derivation_rule,rule_version,input_sha256,payload_sha256,
              confidence,observed_at,valid_from,valid_until
            ) VALUES(
              :tenant,:batch,:capability,:kind,:source_class,
              :target_table,:target_id,:natural_key,CAST(:source_locator AS jsonb),
              :rule,:rule_version,:input_sha,:payload_sha,
              :confidence,:observed_at,:valid_from,:valid_until
            )
            ON CONFLICT(batch_id,target_table,natural_key) DO NOTHING
            """
        ),
        {
            "tenant": tenant_id,
            "batch": batch_id,
            "capability": capability_code,
            "kind": record_kind,
            "source_class": source_class,
            "target_table": target_table,
            "target_id": target_record_id,
            "natural_key": natural_key,
            "source_locator": _canonical(source_locator),
            "rule": derivation_rule,
            "rule_version": rule_version,
            "input_sha": _sha256(input_value),
            "payload_sha": _sha256(payload_value),
            "confidence": confidence,
            "observed_at": observed_at,
            "valid_from": valid_from,
            "valid_until": valid_until,
        },
    )


async def _resolve_scope(
    session,
    package: dict[str, Any],
    *,
    email: str,
) -> dict[str, Any]:
    actor = (
        await session.execute(
            text(
                """
                SELECT u.id user_id,u.tenant_id,t.tenant_code,t.name tenant_name,
                       t.data_class
                  FROM users u
                  JOIN tenants t ON t.id=u.tenant_id
                 WHERE lower(u.email)=lower(:email)
                   AND u.status='active' AND t.status='active'
                """
            ),
            {"email": email},
        )
    ).mappings().one_or_none()
    if actor is None:
        raise RuntimeError(f"active tenant user not found: {email}")
    if actor["data_class"] != "business":
        raise RuntimeError("market decision baseline requires a business tenant")

    selector = package["dataset_selector"]
    dataset = (
        await session.execute(
            text(
                """
                SELECT d.*,d.name AS dataset_name,
                       (SELECT count(*)::int FROM market_listings l
                         WHERE l.tenant_id=d.tenant_id AND l.dataset_id=d.id)
                         AS actual_listing_count,
                       (SELECT count(*)::int FROM reviews r
                         WHERE r.tenant_id=d.tenant_id AND r.dataset_id=d.id
                           AND r.is_valid) AS actual_review_count
                  FROM market_datasets d
                 WHERE d.tenant_id=:tenant
                   AND d.status='ready'
                   AND d.deleted_at IS NULL
                   AND d.market_country=:country
                   AND d.platform=:platform
                   AND d.category_code=:category
                   AND d.source_type=ANY(CAST(:source_types AS text[]))
                   AND NULLIF(btrim(d.authorization_reference),'') IS NOT NULL
                 ORDER BY
                   (SELECT count(*) FROM reviews r
                     WHERE r.tenant_id=d.tenant_id AND r.dataset_id=d.id
                       AND r.is_valid) DESC,
                   (SELECT count(*) FROM market_listings l
                     WHERE l.tenant_id=d.tenant_id AND l.dataset_id=d.id) DESC,
                   d.updated_at DESC,d.id DESC
                 LIMIT 1
                """
            ),
            {
                "tenant": actor["tenant_id"],
                "country": selector["market_country"],
                "platform": selector["platform"],
                "category": selector["category_code"],
                "source_types": selector["allowed_source_types"],
            },
        )
    ).mappings().one_or_none()
    if dataset is None:
        raise RuntimeError("no authorized ready dataset matches the package scope")
    if int(dataset["actual_listing_count"]) < int(selector["minimum_listing_count"]):
        raise RuntimeError("selected dataset does not meet the listing threshold")
    if int(dataset["actual_review_count"]) < int(
        selector["minimum_valid_review_count"]
    ):
        raise RuntimeError("selected dataset does not meet the review threshold")

    product = (
        await session.execute(
            text(
                """
                SELECT p.id product_id,p.sku,p.name AS product_name,p.category_code,
                       p.current_profile_version_id,pv.version_no profile_version_no
                  FROM products p
                  JOIN product_profile_versions pv
                    ON pv.id=p.current_profile_version_id
                   AND pv.product_id=p.id
                   AND pv.tenant_id=p.tenant_id
                 WHERE p.tenant_id=:tenant
                   AND p.deleted_at IS NULL
                   AND p.analysis_status='ready'
                   AND p.category_code=:category
                 ORDER BY p.id
                 LIMIT 1
                """
            ),
            {
                "tenant": actor["tenant_id"],
                "category": selector["category_code"],
            },
        )
    ).mappings().one_or_none()
    if product is None:
        raise RuntimeError("tenant has no analysis-ready product in the selected category")
    return {**dict(actor), **dict(dataset), **dict(product)}


async def _source_rows(session, scope: dict[str, Any]) -> dict[str, Any]:
    listings = [
        dict(row)
        for row in (
            await session.execute(
                text(
                    """
                    SELECT id,platform_listing_id,title,brand,seller_name,
                           sale_price::float8,list_price::float8,currency,
                           rating::float8,rating_count,review_count,captured_at,
                           first_available_date,image_urls,bullet_points
                      FROM market_listings
                     WHERE tenant_id=:tenant AND dataset_id=:dataset
                       AND sale_price>0
                     ORDER BY id
                    """
                ),
                {"tenant": scope["tenant_id"], "dataset": scope["id"]},
            )
        ).mappings().all()
    ]
    reviews = [
        dict(row)
        for row in (
            await session.execute(
                text(
                    """
                    SELECT id,listing_id,platform_review_id,rating::float8,
                           content_original,sentiment,content_hash,reviewed_at
                      FROM reviews
                     WHERE tenant_id=:tenant AND dataset_id=:dataset AND is_valid
                     ORDER BY id
                    """
                ),
                {"tenant": scope["tenant_id"], "dataset": scope["id"]},
            )
        ).mappings().all()
    ]
    if not listings or not reviews:
        raise RuntimeError("selected dataset has no usable listing or review rows")
    digest_input = {
        "dataset_id": scope["id"],
        "authorization_reference": scope["authorization_reference"],
        "quality_report": scope["quality_report"],
        "limitations": scope["limitations"],
        "listings": [
            {
                key: row[key]
                for key in (
                    "id",
                    "platform_listing_id",
                    "sale_price",
                    "list_price",
                    "currency",
                    "rating",
                    "rating_count",
                    "review_count",
                    "captured_at",
                )
            }
            for row in listings
        ],
        "reviews": [
            {
                "id": row["id"],
                "listing_id": row["listing_id"],
                "content_hash": row["content_hash"],
                "rating": row["rating"],
                "reviewed_at": row["reviewed_at"],
            }
            for row in reviews
        ],
    }
    return {
        "listings": listings,
        "reviews": reviews,
        "input": digest_input,
        "sha256": _sha256(digest_input),
    }


async def _ensure_batch(
    session,
    *,
    scope: dict[str, Any],
    package: dict[str, Any],
    package_sha256: str,
    source: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    existing = (
        await session.execute(
            text(
                """
                SELECT id,batch_uuid::text,status,package_sha256
                  FROM market_intelligence_batches
                 WHERE tenant_id=:tenant AND package_id=:package_id
                   AND package_version=:package_version
                """
            ),
            {
                "tenant": scope["tenant_id"],
                "package_id": package["package_id"],
                "package_version": package["package_version"],
            },
        )
    ).mappings().one_or_none()
    if existing:
        if existing["package_sha256"] != package_sha256:
            raise RuntimeError("package content changed without a version increment")
        if existing["status"] != "active":
            raise RuntimeError(
                f"existing package batch is not active: {existing['status']}"
            )
        return dict(existing), False

    source_summary = {
        "source_class": "authorized_source_record",
        "dataset_id": scope["id"],
        "authorization_reference": scope["authorization_reference"],
        "source_name": scope["source_name"],
        "source_type": scope["source_type"],
        "input_sha256": source["sha256"],
    }
    scope_snapshot = {
        "market_country": scope["market_country"],
        "platform": scope["platform"],
        "category_code": scope["category_code"],
        "listing_count": len(source["listings"]),
        "valid_review_count": len(source["reviews"]),
        "quality_report": scope["quality_report"],
        "limitations": scope["limitations"],
    }
    row = (
        await session.execute(
            text(
                """
                INSERT INTO market_intelligence_batches(
                  tenant_id,dataset_id,product_id,package_id,package_version,
                  package_sha256,schema_version,status,source_summary,
                  scope_snapshot,imported_by
                ) VALUES(
                  :tenant,:dataset,:product,:package_id,:package_version,
                  :package_sha,:schema_version,'staged',CAST(:source AS jsonb),
                  CAST(:scope AS jsonb),:user
                )
                RETURNING id,batch_uuid::text,status,package_sha256
                """
            ),
            {
                "tenant": scope["tenant_id"],
                "dataset": scope["id"],
                "product": scope["product_id"],
                "package_id": package["package_id"],
                "package_version": package["package_version"],
                "package_sha": package_sha256,
                "schema_version": package["schema_version"],
                "source": _canonical(source_summary),
                "scope": _canonical(scope_snapshot),
                "user": scope["user_id"],
            },
        )
    ).mappings().one()
    return dict(row), True


async def _ensure_pricing_constraints(
    session,
    *,
    scope: dict[str, Any],
    package: dict[str, Any],
    batch: dict[str, Any],
    source: dict[str, Any],
) -> tuple[int, list[dict[str, Any]]]:
    config = package["capabilities"]["pricing"]
    prices = [float(row["sale_price"]) for row in source["listings"]]
    market_median = median(prices)
    planned_unit_cost = round(
        market_median * float(config["cost_ratio_of_market_median"]), 2
    )
    effective_at = datetime.fromisoformat(config["effective_at"] + "T00:00:00+00:00")
    valid_until = datetime.fromisoformat(config["valid_until"] + "T23:59:59+00:00")
    planned = [
        {
            "constraint_type": "unit_cost",
            "operator": "eq",
            "value": planned_unit_cost,
            "unit": config["currency"],
            "hardness": "planning",
            "effective_at": config["effective_at"],
            "expires_at": config["valid_until"],
            "sensitivity_level": "internal",
            "source_class": config["classification"],
            "rule_version": config["rule_version"],
            "governance_batch_uuid": batch["batch_uuid"],
        },
        {
            "constraint_type": "target_margin",
            "operator": "eq",
            "value": float(config["target_margin"]),
            "unit": "ratio",
            "hardness": "planning",
            "effective_at": config["effective_at"],
            "expires_at": config["valid_until"],
            "sensitivity_level": "internal",
            "source_class": config["classification"],
            "rule_version": config["rule_version"],
            "governance_batch_uuid": batch["batch_uuid"],
        },
    ]
    profile = (
        await session.execute(
            text(
                """
                SELECT id,constraints,profile_version
                  FROM enterprise_profiles
                 WHERE tenant_id=:tenant
                 FOR UPDATE
                """
            ),
            {"tenant": scope["tenant_id"]},
        )
    ).mappings().one_or_none()
    if profile is None:
        profile_id = int(
            await session.scalar(
                text(
                    """
                    INSERT INTO enterprise_profiles(
                      tenant_id,business_model,primary_categories,export_markets,
                      sales_channels,constraints,profile_completeness,profile_version
                    ) VALUES(
                      :tenant,'[]',CAST(:categories AS jsonb),CAST(:markets AS jsonb),
                      CAST(:channels AS jsonb),CAST(:constraints AS jsonb),0.25,1
                    )
                    RETURNING id
                    """
                ),
                {
                    "tenant": scope["tenant_id"],
                    "categories": _canonical([scope["category_code"]]),
                    "markets": _canonical([scope["market_country"]]),
                    "channels": _canonical([scope["platform"]]),
                    "constraints": _canonical(planned),
                },
            )
        )
    else:
        profile_id = int(profile["id"])
        current = list(profile["constraints"] or [])
        current_types = {
            str(item.get("constraint_type") or "").lower()
            for item in current
            if item.get("source_class") != "planning_assumption"
        }
        retained = [
            item
            for item in current
            if not (
                item.get("source_class") == "planning_assumption"
                and item.get("constraint_type") in {"unit_cost", "target_margin"}
            )
        ]
        additions = [
            item for item in planned if item["constraint_type"] not in current_types
        ]
        await session.execute(
            text(
                """
                UPDATE enterprise_profiles
                   SET constraints=CAST(:constraints AS jsonb),
                       profile_version=profile_version+1
                 WHERE id=:profile AND tenant_id=:tenant
                """
            ),
            {
                "constraints": _canonical(retained + additions),
                "profile": profile_id,
                "tenant": scope["tenant_id"],
            },
        )
        planned = additions

    for item in planned:
        await _lineage(
            session,
            tenant_id=scope["tenant_id"],
            batch_id=batch["id"],
            capability_code="pricing",
            record_kind="enterprise_constraint",
            source_class="planning_assumption",
            target_table="enterprise_profiles",
            target_record_id=profile_id,
            natural_key=f"constraint:{item['constraint_type']}",
            source_locator={
                "dataset_id": scope["id"],
                "market_price_sample_size": len(prices),
                "currency": config["currency"],
            },
            input_value={"prices": prices, "config": config},
            payload_value=item,
            derivation_rule=(
                "unit cost equals the current market median multiplied by the "
                "configured planning ratio"
                if item["constraint_type"] == "unit_cost"
                else "target margin is the package planning parameter"
            ),
            rule_version=config["rule_version"],
            confidence=0.5,
            valid_from=effective_at,
            valid_until=valid_until,
        )
    return profile_id, planned


async def _profile_snapshot(
    session,
    *,
    scope: dict[str, Any],
    package: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    profile = await session.scalar(
        text(
            """
            SELECT to_jsonb(ep)-'id'-'tenant_id'
              FROM enterprise_profiles ep
             WHERE tenant_id=:tenant
            """
        ),
        {"tenant": scope["tenant_id"]},
    )
    capabilities = list(
        (
            await session.execute(
                text(
                    """
                    SELECT to_jsonb(mc)-'id'-'tenant_id'-'created_by' item
                      FROM manufacturing_capabilities mc
                     WHERE tenant_id=:tenant
                     ORDER BY capability_type,capability_code
                    """
                ),
                {"tenant": scope["tenant_id"]},
            )
        ).scalars().all()
    )
    product_facts = await session.scalar(
        text(
            """
            SELECT COALESCE(jsonb_object_agg(attribute_code,jsonb_build_object(
                     'value',value,'unit',unit,
                     'confirmation_status',confirmation_status,
                     'confidence',confidence,'source_type',source_type,
                     'source_locator',source_locator
                   )),'{}'::jsonb)
              FROM product_attributes
             WHERE tenant_id=:tenant AND profile_version_id=:profile
            """
        ),
        {
            "tenant": scope["tenant_id"],
            "profile": scope["current_profile_version_id"],
        },
    )
    profile_version = int((profile or {}).get("profile_version") or 0)
    smart_config = package["capabilities"]["smart_selection"]
    snapshot = {
        "profile_version": profile_version,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "profile": profile,
        "capabilities": capabilities,
        "opportunity_policy": {
            "version": 0,
            "name": "均衡判断",
            "objective": "balanced",
            "weights": smart_config["weights"],
            "fit_strength": 0.3,
            "required_capabilities": [],
            "calibration": "uncalibrated_heuristic",
        },
        "product_facts": product_facts or {},
        "product_context": {"category_code": scope["category_code"]},
    }
    return profile_version, snapshot


async def _ensure_task(
    session,
    *,
    scope: dict[str, Any],
    package: dict[str, Any],
    batch: dict[str, Any],
    source: dict[str, Any],
) -> dict[str, Any]:
    idempotency_key = (
        f"{package['package_id']}:{package['package_version']}:decision-baseline"
    )
    existing = (
        await session.execute(
            text(
                """
                SELECT id,task_uuid::text FROM analysis_tasks
                 WHERE tenant_id=:tenant AND idempotency_key=:key
                """
            ),
            {"tenant": scope["tenant_id"], "key": idempotency_key},
        )
    ).mappings().one_or_none()
    if existing:
        return dict(existing)
    profile_version, snapshot = await _profile_snapshot(
        session, scope=scope, package=package
    )
    review_rule = package["capabilities"]["review_mining"]["rule_version"]
    score_rule = package["capabilities"]["smart_selection"]["rule_version"]
    analysis_config = {
        "source": "market_decision_baseline",
        "execution_mode": "deterministic_rules",
        "data_class": "authorized_market_data",
        "governance_batch_uuid": batch["batch_uuid"],
        "source_dataset_sha256": source["sha256"],
    }
    row = (
        await session.execute(
            text(
                """
                INSERT INTO analysis_tasks(
                  tenant_id,job_name,job_type,product_id,product_profile_version_id,
                  dataset_id,target_country,target_platform,analysis_currency,status,
                  external_stage,internal_stage,progress_percent,analysis_config,
                  enterprise_profile_version,enterprise_profile_snapshot,
                  ontology_version,scoring_version,prompt_bundle_version,
                  model_route_version,idempotency_key,started_at,completed_at,
                  created_by
                ) VALUES(
                  :tenant,:name,'product_market_fit',:product,:profile,
                  :dataset,:country,:platform,:currency,'succeeded',
                  'completed','completed',100,CAST(:config AS jsonb),
                  :profile_version,CAST(:snapshot AS jsonb),
                  :ontology,:scoring,'rules-not-applicable',
                  'furniscope-rules-v1',:key,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,
                  :user
                )
                RETURNING id,task_uuid::text
                """
            ),
            {
                "tenant": scope["tenant_id"],
                "name": f"{scope['category_code']} 市场决策基线",
                "product": scope["product_id"],
                "profile": scope["current_profile_version_id"],
                "dataset": scope["id"],
                "country": scope["market_country"],
                "platform": scope["platform"],
                "currency": package["capabilities"]["pricing"]["currency"],
                "config": _canonical(analysis_config),
                "profile_version": profile_version,
                "snapshot": _canonical(snapshot),
                "ontology": review_rule,
                "scoring": score_rule,
                "key": idempotency_key,
                "user": scope["user_id"],
            },
        )
    ).mappings().one()
    task = dict(row)
    await _lineage(
        session,
        tenant_id=scope["tenant_id"],
        batch_id=batch["id"],
        capability_code="smart_selection",
        record_kind="analysis_task",
        source_class="derived_result",
        target_table="analysis_tasks",
        target_record_id=task["id"],
        natural_key="analysis-task",
        source_locator={
            "dataset_id": scope["id"],
            "product_id": scope["product_id"],
        },
        input_value=source["input"],
        payload_value={**task, "analysis_config": analysis_config},
        derivation_rule="complete deterministic decision baseline from authorized rows",
        rule_version=score_rule,
        confidence=0.65,
    )
    return task


async def _build_review_mining(
    session,
    *,
    scope: dict[str, Any],
    package: dict[str, Any],
    batch: dict[str, Any],
    source: dict[str, Any],
    task: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    config = package["capabilities"]["review_mining"]
    model_run_id = int(
        await session.scalar(
            text(
                """
                INSERT INTO ai_model_runs(
                  tenant_id,task_id,provider,model_id,task_type,input_hash,
                  output_schema_version,latency_ms,status,retry_count,schema_valid
                ) VALUES(
                  :tenant,:task,'furniscope_rules','evidence-taxonomy-v1',
                  'text_extract',:input_hash,:schema,0,'cached',0,TRUE
                )
                RETURNING id
                """
            ),
            {
                "tenant": scope["tenant_id"],
                "task": task["id"],
                "input_hash": source["sha256"],
                "schema": config["rule_version"],
            },
        )
    )
    aspect_rows: list[dict[str, Any]] = []
    quote_limit = int(config["maximum_quote_characters"])
    for review in source["reviews"]:
        taxonomies, sentiment = classify_review(
            review["content_original"], review["rating"], config
        )
        quote = review["content_original"][:quote_limit]
        confidence = 0.72 if taxonomies[0]["code"] != "general_experience" else 0.58
        for aspect_index, taxonomy in enumerate(taxonomies):
            aspect_id = int(
                await session.scalar(
                    text(
                        """
                        INSERT INTO review_aspects(
                          tenant_id,analysis_job_id,review_id,aspect_index,
                          taxonomy_code,sentiment,severity,person_codes,
                          scenario_codes,product_attribute_codes,evidence_start,
                          evidence_end,evidence_quote,extraction_confidence,
                          model_run_id
                        ) VALUES(
                          :tenant,:task,:review,:aspect_index,:taxonomy,:sentiment,
                          :severity,'[]','[]',CAST(:attributes AS jsonb),0,:end,
                          :quote,:confidence,:model_run
                        )
                        RETURNING id
                        """
                    ),
                    {
                        "tenant": scope["tenant_id"],
                        "task": task["id"],
                        "review": review["id"],
                        "aspect_index": aspect_index,
                        "taxonomy": taxonomy["code"],
                        "sentiment": sentiment,
                        "severity": "high" if sentiment == "negative" else None,
                        "attributes": _canonical(
                            []
                            if taxonomy["code"] == "general_experience"
                            else [taxonomy["code"]]
                        ),
                        "end": len(quote),
                        "quote": quote,
                        "confidence": confidence,
                        "model_run": model_run_id,
                    },
                )
            )
            item = {
                "aspect_id": aspect_id,
                "review_id": int(review["id"]),
                "listing_id": int(review["listing_id"]),
                "taxonomy_code": taxonomy["code"],
                "taxonomy_name": taxonomy["name"],
                "sentiment": sentiment,
                "evidence_start": 0,
                "evidence_end": len(quote),
                "evidence_quote": quote,
                "confidence": confidence,
            }
            aspect_rows.append(item)
            await _lineage(
                session,
                tenant_id=scope["tenant_id"],
                batch_id=batch["id"],
                capability_code="review_mining",
                record_kind="review_aspect",
                source_class="derived_result",
                target_table="review_aspects",
                target_record_id=aspect_id,
                natural_key=f"review:{review['id']}:aspect:{aspect_index}",
                source_locator={
                    "dataset_id": scope["id"],
                    "review_id": review["id"],
                    "content_hash": review["content_hash"],
                    "evidence_start": 0,
                    "evidence_end": len(quote),
                },
                input_value={
                    "content_hash": review["content_hash"],
                    "rating": review["rating"],
                },
                payload_value=item,
                derivation_rule="keyword taxonomy and rating-aware sentiment rule",
                rule_version=config["rule_version"],
                confidence=confidence,
                observed_at=review["reviewed_at"],
            )

    taxonomy_names = {
        item["code"]: item["name"] for item in config["taxonomies"]
    } | {"general_experience": "整体使用体验"}
    cluster_rows: list[dict[str, Any]] = []
    total_reviews = len(source["reviews"])
    for taxonomy_code in sorted({row["taxonomy_code"] for row in aspect_rows}):
        members = [
            row for row in aspect_rows if row["taxonomy_code"] == taxonomy_code
        ]
        review_count = len({row["review_id"] for row in members})
        listing_count = len({row["listing_id"] for row in members})
        positive = sum(row["sentiment"] == "positive" for row in members)
        negative = sum(row["sentiment"] == "negative" for row in members)
        mixed = sum(row["sentiment"] == "mixed" for row in members)
        neutral = len(members) - positive - negative - mixed
        mention_rate = round(review_count / total_reviews, 6)
        importance = _clamp(
            mention_rate * 70 + (negative + mixed * 0.5) / len(members) * 30
        )
        cluster_confidence = round(
            min(0.9, 0.55 + math.log10(review_count + 1) * 0.2), 4
        )
        representatives = [
            row["aspect_id"]
            for row in sorted(
                members,
                key=lambda item: (-item["confidence"], item["aspect_id"]),
            )[:3]
        ]
        summary = (
            f"{review_count} 条评论涉及{taxonomy_names[taxonomy_code]}，"
            f"其中负向 {negative} 条、正负并存 {mixed} 条。"
        )
        cluster_id = int(
            await session.scalar(
                text(
                    """
                    INSERT INTO insight_clusters(
                      tenant_id,analysis_job_id,cluster_code,taxonomy_code,name,
                      summary,sentiment_distribution,aspect_count,review_count,
                      listing_count,mention_rate,importance_score,
                      cluster_confidence,representative_aspect_ids
                    ) VALUES(
                      :tenant,:task,:code,:taxonomy,:name,:summary,
                      CAST(:distribution AS jsonb),:aspect_count,:review_count,
                      :listing_count,:mention_rate,:importance,:confidence,
                      CAST(:representatives AS jsonb)
                    )
                    RETURNING id
                    """
                ),
                {
                    "tenant": scope["tenant_id"],
                    "task": task["id"],
                    "code": f"need-{taxonomy_code}",
                    "taxonomy": taxonomy_code,
                    "name": taxonomy_names[taxonomy_code],
                    "summary": summary,
                    "distribution": _canonical(
                        {
                            "positive": positive,
                            "negative": negative,
                            "mixed": mixed,
                            "neutral": neutral,
                        }
                    ),
                    "aspect_count": len(members),
                    "review_count": review_count,
                    "listing_count": listing_count,
                    "mention_rate": mention_rate,
                    "importance": importance,
                    "confidence": cluster_confidence,
                    "representatives": _canonical(representatives),
                },
            )
        )
        for member in members:
            await session.execute(
                text(
                    """
                    INSERT INTO cluster_members(
                      cluster_id,review_aspect_id,similarity_score,is_representative
                    ) VALUES(:cluster,:aspect,1,:representative)
                    """
                ),
                {
                    "cluster": cluster_id,
                    "aspect": member["aspect_id"],
                    "representative": member["aspect_id"] in representatives,
                },
            )
        item = {
            "cluster_id": cluster_id,
            "cluster_code": f"need-{taxonomy_code}",
            "taxonomy_code": taxonomy_code,
            "name": taxonomy_names[taxonomy_code],
            "summary": summary,
            "sentiment_distribution": {
                "positive": positive,
                "negative": negative,
                "mixed": mixed,
                "neutral": neutral,
            },
            "aspect_count": len(members),
            "review_count": review_count,
            "listing_count": listing_count,
            "mention_rate": mention_rate,
            "importance_score": importance,
            "cluster_confidence": cluster_confidence,
            "representative_aspect_ids": representatives,
        }
        cluster_rows.append(item)
        await _lineage(
            session,
            tenant_id=scope["tenant_id"],
            batch_id=batch["id"],
            capability_code="review_mining",
            record_kind="insight_cluster",
            source_class="derived_result",
            target_table="insight_clusters",
            target_record_id=cluster_id,
            natural_key=f"cluster:{taxonomy_code}",
            source_locator={
                "dataset_id": scope["id"],
                "review_ids": sorted({row["review_id"] for row in members}),
                "representative_aspect_ids": representatives,
            },
            input_value=members,
            payload_value=item,
            derivation_rule="group evidence aspects by stable furniture taxonomy",
            rule_version=config["rule_version"],
            confidence=cluster_confidence,
        )
    return aspect_rows, cluster_rows


async def _build_opportunities(
    session,
    *,
    scope: dict[str, Any],
    package: dict[str, Any],
    batch: dict[str, Any],
    source: dict[str, Any],
    task: dict[str, Any],
    clusters: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    config = package["capabilities"]["smart_selection"]
    weights = config["weights"]
    prices = [float(row["sale_price"]) for row in source["listings"]]
    profile_snapshot = await session.scalar(
        text(
            """
            SELECT enterprise_profile_snapshot
              FROM analysis_tasks
             WHERE id=:task AND tenant_id=:tenant
            """
        ),
        {"task": task["id"], "tenant": scope["tenant_id"]},
    ) or {}
    planning_margin = _clamp(
        (1 - float(package["capabilities"]["pricing"]["cost_ratio_of_market_median"]))
        * 100
    )
    metric_id = int(
        await session.scalar(
            text(
                """
                INSERT INTO market_metrics(
                  tenant_id,analysis_job_id,metric_code,dimension_type,
                  metric_value,unit,sample_size,confidence,formula_version
                ) VALUES(
                  :tenant,:task,'median_sale_price','market',:value,:currency,
                  :sample_size,:confidence,'descriptive-v1'
                )
                RETURNING id
                """
            ),
            {
                "tenant": scope["tenant_id"],
                "task": task["id"],
                "value": median(prices),
                "currency": package["capabilities"]["pricing"]["currency"],
                "sample_size": len(prices),
                "confidence": min(0.95, 0.5 + len(prices) / 100),
            },
        )
    )
    band_id = int(
        await session.scalar(
            text(
                """
                INSERT INTO price_bands(
                  tenant_id,analysis_job_id,band_code,currency,lower_bound,
                  upper_bound,listing_count,review_share,feature_codes
                ) VALUES(
                  :tenant,:task,'observed-range',:currency,:lower,:upper,
                  :count,1,'[]'
                )
                RETURNING id
                """
            ),
            {
                "tenant": scope["tenant_id"],
                "task": task["id"],
                "currency": package["capabilities"]["pricing"]["currency"],
                "lower": min(prices),
                "upper": max(prices) + 0.0001,
                "count": len(prices),
            },
        )
    )
    await _lineage(
        session,
        tenant_id=scope["tenant_id"],
        batch_id=batch["id"],
        capability_code="pricing",
        record_kind="market_price_metric",
        source_class="derived_result",
        target_table="market_metrics",
        target_record_id=metric_id,
        natural_key="metric:median-sale-price",
        source_locator={"dataset_id": scope["id"], "listing_count": len(prices)},
        input_value=prices,
        payload_value={"median": median(prices), "currency": "USD"},
        derivation_rule="median of positive same-currency listing prices",
        rule_version="descriptive-v1",
        confidence=min(0.95, 0.5 + len(prices) / 100),
    )

    opportunities: list[dict[str, Any]] = []
    ordered = sorted(
        clusters,
        key=lambda row: (-row["importance_score"], row["cluster_id"]),
    )[: int(config["maximum_items"])]
    for index, cluster in enumerate(ordered, 1):
        distribution = cluster["sentiment_distribution"]
        heat = _clamp(cluster["mention_rate"] * 100)
        unmet = _clamp(
            (
                distribution["negative"] + distribution["mixed"] * 0.5
            )
            / cluster["aspect_count"]
            * 100
        )
        competition = _clamp(100 - min(80, len(source["listings"]) * 2))
        matched_dates = []
        for review in source["reviews"]:
            taxonomies, _ = classify_review(
                review["content_original"], review["rating"],
                package["capabilities"]["review_mining"],
            )
            if (
                review["reviewed_at"]
                and cluster["taxonomy_code"] in {item["code"] for item in taxonomies}
            ):
                matched_dates.append(review["reviewed_at"].date())
        growth_score = None
        if len(matched_dates) >= 20 and (max(matched_dates) - min(matched_dates)).days >= 30:
            first, last = min(matched_dates), max(matched_dates)
            midpoint = first + (last - first) / 2
            prior = sum(day < midpoint for day in matched_dates)
            recent = len(matched_dates) - prior
            growth_score = _clamp(50 + (recent - prior) / max(prior, 1) * 50)
        fit_score, fit_confidence, fit = enterprise_fit(
            profile_snapshot,
            category_code=scope["category_code"],
            market_country=scope["market_country"],
            taxonomy_code=cluster["taxonomy_code"],
        )
        factors = {
            "demand_heat": heat,
            "demand_growth": growth_score,
            "unmet_need": unmet,
            "competition_space": competition,
            "profit_space": planning_margin,
        }
        scored = score_opportunity(
            factors,
            profile_snapshot.get("opportunity_policy") or {
                "version": 0,
                "weights": weights,
                "fit_strength": 0.3,
            },
            fit_score,
        )
        market_score = scored["market_score"]
        adjusted_score = scored["adjusted_score"]
        confidence = round(
            cluster["cluster_confidence"]
            * scored["coverage"]
            * (0.75 + 0.25 * fit_confidence),
            4,
        )
        threshold = config["recommendation_thresholds"]
        level = (
            "capability_gap"
            if fit["status"] == "blocked"
            else
            "prioritize_validate"
            if adjusted_score >= float(threshold["prioritize_validate_score"])
            and confidence >= float(threshold["minimum_confidence"])
            else "collect_more_data"
        )
        weight_config = {
            **weights,
            "coverage": scored["coverage"],
            "missing_factors": scored["missing_factors"],
            "used_weights": scored["used_weights"],
            "profit_space_basis": "planning_margin",
            "demand_growth_basis": "planning_window_distribution",
            "calibration": "uncalibrated_heuristic",
        }
        manufacturing_fit = [fit]
        opportunity_id = int(
            await session.scalar(
                text(
                    """
                    INSERT INTO market_opportunities(
                      tenant_id,analysis_job_id,opportunity_code,title,description,
                      target_country,target_platform,primary_cluster_ids,price_band_id,
                      demand_heat_score,demand_growth_score,unmet_need_score,
                      competition_space_score,profit_space_score,
                      enterprise_fit_score,enterprise_fit_confidence,base_score,
                      confidence,recommendation_level,weight_config,scoring_version,
                      manufacturing_fit,market_score,adjusted_score,policy_snapshot
                    ) VALUES(
                      :tenant,:task,:code,:title,:description,:country,:platform,
                      CAST(:clusters AS jsonb),:band,:heat,:growth,:unmet,
                      :competition,:profit,:enterprise_fit,:fit_confidence,
                      :base,:confidence,:level,
                      CAST(:weights AS jsonb),:scoring,CAST(:fit AS jsonb),
                      :market_score,:adjusted,CAST(:policy AS jsonb)
                    )
                    RETURNING id
                    """
                ),
                {
                    "tenant": scope["tenant_id"],
                    "task": task["id"],
                    "code": f"BASE-{index:02d}",
                    "title": f"改善{cluster['name']}",
                    "description": cluster["summary"],
                    "country": scope["market_country"],
                    "platform": scope["platform"],
                    "clusters": _canonical([cluster["cluster_id"]]),
                    "band": band_id,
                    "heat": heat,
                    "growth": growth_score,
                    "unmet": unmet,
                    "competition": competition,
                    "profit": planning_margin,
                    "enterprise_fit": fit_score,
                    "fit_confidence": fit_confidence,
                    "base": adjusted_score,
                    "confidence": confidence,
                    "level": level,
                    "weights": _canonical(weight_config),
                    "scoring": config["rule_version"],
                    "fit": _canonical(manufacturing_fit),
                    "market_score": market_score,
                    "adjusted": adjusted_score,
                    "policy": _canonical(profile_snapshot.get("opportunity_policy") or {}),
                },
            )
        )
        item = {
            "opportunity_id": opportunity_id,
            "opportunity_code": f"BASE-{index:02d}",
            "title": f"改善{cluster['name']}",
            "description": cluster["summary"],
            "primary_cluster_ids": [cluster["cluster_id"]],
            **factors,
            "enterprise_fit": fit_score,
            "market_score": market_score,
            "adjusted_score": adjusted_score,
            "confidence": confidence,
            "recommendation_level": level,
            "weight_config": weight_config,
            "manufacturing_fit": manufacturing_fit,
        }
        opportunities.append(item)
        await _lineage(
            session,
            tenant_id=scope["tenant_id"],
            batch_id=batch["id"],
            capability_code="smart_selection",
            record_kind="market_opportunity",
            source_class="derived_result",
            target_table="market_opportunities",
            target_record_id=opportunity_id,
            natural_key=f"opportunity:BASE-{index:02d}",
            source_locator={
                "dataset_id": scope["id"],
                "cluster_ids": [cluster["cluster_id"]],
            },
            input_value={"cluster": cluster, "weights": weights},
            payload_value=item,
            derivation_rule=(
                "renormalized weighted score over available demand, unmet-need "
                "and competition factors"
            ),
            rule_version=config["rule_version"],
            confidence=confidence,
        )
    return opportunities


async def _build_competitor_tracking(
    session,
    *,
    scope: dict[str, Any],
    package: dict[str, Any],
    batch: dict[str, Any],
    source: dict[str, Any],
) -> list[dict[str, Any]]:
    config = package["capabilities"]["competitor_tracking"]
    ordered = sorted(
        source["listings"],
        key=lambda row: (
            -(int(row["review_count"] or 0)),
            -(float(row["rating"] or 0)),
            int(row["id"]),
        ),
    )[: int(config["watch_count"])]
    results: list[dict[str, Any]] = []
    for listing in ordered:
        asin = str(listing["platform_listing_id"])[:40]
        await session.execute(
            text(
                """
                INSERT INTO competitor_watch_targets(
                  tenant_id,dataset_id,platform,market_country,asin,title,
                  status,compare_selected,created_by
                ) VALUES(
                  :tenant,:dataset,:platform,:country,:asin,:title,
                  'active',TRUE,:user
                )
                ON CONFLICT(tenant_id,platform,market_country,asin) DO UPDATE SET
                  dataset_id=EXCLUDED.dataset_id,
                  title=EXCLUDED.title,
                  status='active',
                  compare_selected=TRUE,
                  updated_at=CURRENT_TIMESTAMP
                """
            ),
            {
                "tenant": scope["tenant_id"],
                "dataset": scope["id"],
                "platform": scope["platform"],
                "country": scope["market_country"],
                "asin": asin,
                "title": listing["title"],
                "user": scope["user_id"],
            },
        )
        watch_id = int(
            await session.scalar(
                text(
                    """
                    SELECT id FROM competitor_watch_targets
                     WHERE tenant_id=:tenant AND platform=:platform
                       AND market_country=:country AND asin=:asin
                    """
                ),
                {
                    "tenant": scope["tenant_id"],
                    "platform": scope["platform"],
                    "country": scope["market_country"],
                    "asin": asin,
                },
            )
        )
        fingerprint_payload = {
            "title": listing["title"],
            "sale_price": listing["sale_price"],
            "list_price": listing["list_price"],
            "currency": listing["currency"],
            "rating": listing["rating"],
            "review_count": listing["review_count"],
            "image_urls": listing["image_urls"],
            "bullet_points": listing["bullet_points"],
        }
        fingerprint = _sha256(fingerprint_payload)
        snapshot_id = await session.scalar(
            text(
                """
                SELECT id FROM competitor_listing_snapshots
                 WHERE tenant_id=:tenant AND watch_id=:watch
                   AND fingerprint=:fingerprint AND captured_at=:captured
                 ORDER BY id LIMIT 1
                """
            ),
            {
                "tenant": scope["tenant_id"],
                "watch": watch_id,
                "fingerprint": fingerprint,
                "captured": listing["captured_at"],
            },
        )
        if snapshot_id is None:
            snapshot_id = int(
                await session.scalar(
                    text(
                        """
                        INSERT INTO competitor_listing_snapshots(
                          tenant_id,dataset_id,watch_id,asin,captured_at,source,
                          title,sale_price,list_price,currency,rating,review_count,
                          image_urls,bullet_points,first_available_date,fingerprint,
                          raw_payload
                        ) VALUES(
                          :tenant,:dataset,:watch,:asin,:captured,
                          'authorized_dataset_baseline',:title,:sale,:list_price,
                          :currency,:rating,:reviews,CAST(:images AS jsonb),
                          CAST(:bullets AS jsonb),:available,:fingerprint,
                          CAST(:raw AS jsonb)
                        )
                        RETURNING id
                        """
                    ),
                    {
                        "tenant": scope["tenant_id"],
                        "dataset": scope["id"],
                        "watch": watch_id,
                        "asin": asin,
                        "captured": listing["captured_at"],
                        "title": listing["title"],
                        "sale": listing["sale_price"],
                        "list_price": listing["list_price"],
                        "currency": listing["currency"],
                        "rating": listing["rating"],
                        "reviews": listing["review_count"],
                        "images": _canonical(listing["image_urls"] or []),
                        "bullets": _canonical(listing["bullet_points"] or []),
                        "available": listing["first_available_date"],
                        "fingerprint": fingerprint,
                        "raw": _canonical(
                            {
                                "dataset_id": scope["id"],
                                "listing_id": listing["id"],
                                "authorization_reference": scope[
                                    "authorization_reference"
                                ],
                            }
                        ),
                    },
                )
            )
        alert_id = None
        if config["register_baseline_event"]:
            alert_id = await session.scalar(
                text(
                    """
                    SELECT id FROM competitor_change_alerts
                     WHERE tenant_id=:tenant AND watch_id=:watch
                       AND change_type='new_listing' AND captured_at=:captured
                     ORDER BY id LIMIT 1
                    """
                ),
                {
                    "tenant": scope["tenant_id"],
                    "watch": watch_id,
                    "captured": listing["captured_at"],
                },
            )
            if alert_id is None:
                alert_id = int(
                    await session.scalar(
                        text(
                            """
                            INSERT INTO competitor_change_alerts(
                              tenant_id,watch_id,asin,change_type,summary,
                              before_value,after_value,captured_at,is_read
                            ) VALUES(
                              :tenant,:watch,:asin,'new_listing',:summary,NULL,
                              CAST(:after AS jsonb),:captured,FALSE
                            )
                            RETURNING id
                            """
                        ),
                        {
                            "tenant": scope["tenant_id"],
                            "watch": watch_id,
                            "asin": asin,
                            "summary": f"{listing['title']} 已纳入竞品观察基线",
                            "after": _canonical(
                                {
                                    "snapshot_id": snapshot_id,
                                    "sale_price": listing["sale_price"],
                                    "currency": listing["currency"],
                                }
                            ),
                            "captured": listing["captured_at"],
                        },
                    )
                )
        item = {
            "watch_id": watch_id,
            "snapshot_id": int(snapshot_id),
            "alert_id": int(alert_id) if alert_id is not None else None,
            "asin": asin,
            "title": listing["title"],
            "captured_at": listing["captured_at"],
            "sale_price": listing["sale_price"],
            "list_price": listing["list_price"],
            "currency": listing["currency"],
            "rating": listing["rating"],
            "review_count": listing["review_count"],
            "fingerprint": fingerprint,
        }
        results.append(item)
        common = {
            "tenant_id": scope["tenant_id"],
            "batch_id": batch["id"],
            "capability_code": "competitor_tracking",
            "source_locator": {
                "dataset_id": scope["id"],
                "listing_id": listing["id"],
                "platform_listing_id": listing["platform_listing_id"],
            },
            "input_value": listing,
            "rule_version": config["rule_version"],
            "confidence": 0.85,
            "observed_at": listing["captured_at"],
        }
        await _lineage(
            session,
            **common,
            record_kind="watch_target",
            source_class="derived_result",
            target_table="competitor_watch_targets",
            target_record_id=watch_id,
            natural_key=f"watch:{asin}",
            payload_value={"watch_id": watch_id, "asin": asin},
            derivation_rule="rank authorized listings and register selected targets",
        )
        await _lineage(
            session,
            **common,
            record_kind="listing_snapshot",
            source_class="authorized_source_record",
            target_table="competitor_listing_snapshots",
            target_record_id=int(snapshot_id),
            natural_key=f"snapshot:{asin}:{fingerprint}",
            payload_value=item,
            derivation_rule="copy current authorized listing fields without time expansion",
        )
        if alert_id is not None:
            await _lineage(
                session,
                **common,
                record_kind="baseline_registration",
                source_class="derived_result",
                target_table="competitor_change_alerts",
                target_record_id=int(alert_id),
                natural_key=f"baseline-alert:{asin}",
                payload_value={
                    "alert_id": int(alert_id),
                    "change_type": "new_listing",
                },
                derivation_rule="register the first governed watch baseline",
            )
    return results


async def _ensure_policy_sources(
    session,
    *,
    scope: dict[str, Any],
    package: dict[str, Any],
    batch: dict[str, Any],
) -> list[dict[str, Any]]:
    config = package["capabilities"]["compliance"]
    results = []
    for source in config["sources"]:
        row = (
            await session.execute(
                text(
                    """
                    INSERT INTO policy_sources(
                      tenant_id,name,source_type,source_url,market_country,
                      category_code,keywords,authorization_reference,
                      schedule_minutes,enabled,created_by
                    ) VALUES(
                      :tenant,:name,:type,:url,:country,:category,
                      CAST(:keywords AS jsonb),:authorization,:schedule,TRUE,:user
                    )
                    ON CONFLICT(tenant_id,source_url) DO UPDATE SET
                      name=EXCLUDED.name,source_type=EXCLUDED.source_type,
                      market_country=EXCLUDED.market_country,
                      category_code=EXCLUDED.category_code,
                      keywords=EXCLUDED.keywords,
                      authorization_reference=EXCLUDED.authorization_reference,
                      schedule_minutes=EXCLUDED.schedule_minutes,enabled=TRUE
                    RETURNING id,name,source_type,source_url,market_country,
                              category_code,keywords,authorization_reference,
                              schedule_minutes,enabled
                    """
                ),
                {
                    "tenant": scope["tenant_id"],
                    "name": source["name"],
                    "type": source["source_type"],
                    "url": source["source_url"],
                    "country": source["market_country"],
                    "category": source.get("category_code"),
                    "keywords": _canonical(config["keywords"]),
                    "authorization": source["authorization_reference"],
                    "schedule": config["schedule_minutes"],
                    "user": scope["user_id"],
                },
            )
        ).mappings().one()
        item = dict(row)
        results.append(item)
        await _lineage(
            session,
            tenant_id=scope["tenant_id"],
            batch_id=batch["id"],
            capability_code="compliance",
            record_kind="policy_source",
            source_class="official_source_registry",
            target_table="policy_sources",
            target_record_id=int(row["id"]),
            natural_key=f"policy-source:{_sha256(source['source_url'])[:20]}",
            source_locator={
                "source_url": source["source_url"],
                "authorization_reference": source["authorization_reference"],
            },
            input_value=source,
            payload_value=item,
            derivation_rule="register an official public policy endpoint",
            rule_version=config["rule_version"],
            confidence=1,
        )
    return results


async def _activate_batch(
    session,
    *,
    scope: dict[str, Any],
    package: dict[str, Any],
    batch: dict[str, Any],
) -> None:
    await session.execute(
        text(
            """
            UPDATE market_intelligence_batches
               SET status='superseded',superseded_at=CURRENT_TIMESTAMP
             WHERE tenant_id=:tenant AND package_id=:package_id
               AND id<>:batch AND status='active'
            """
        ),
        {
            "tenant": scope["tenant_id"],
            "package_id": package["package_id"],
            "batch": batch["id"],
        },
    )
    await session.execute(
        text(
            """
            UPDATE market_intelligence_batches
               SET status='active',activated_at=CURRENT_TIMESTAMP
             WHERE id=:batch AND tenant_id=:tenant AND status='staged'
            """
        ),
        {"batch": batch["id"], "tenant": scope["tenant_id"]},
    )


async def _export(
    session,
    *,
    scope: dict[str, Any],
    package: dict[str, Any],
    package_sha256: str,
    batch: dict[str, Any],
) -> dict[str, Any]:
    batch_row = dict(
        (
            await session.execute(
                text(
                    """
                    SELECT id,batch_uuid::text,package_id,package_version,
                           package_sha256,schema_version,status,source_summary,
                           scope_snapshot,imported_at,activated_at
                      FROM market_intelligence_batches
                     WHERE id=:batch AND tenant_id=:tenant
                    """
                ),
                {"batch": batch["id"], "tenant": scope["tenant_id"]},
            )
        ).mappings().one()
    )
    task = (
        await session.execute(
            text(
                """
                SELECT id,task_uuid::text,job_name,status,completed_at
                  FROM analysis_tasks
                 WHERE tenant_id=:tenant
                   AND analysis_config->>'governance_batch_uuid'=:batch_uuid
                 ORDER BY id DESC LIMIT 1
                """
            ),
            {
                "tenant": scope["tenant_id"],
                "batch_uuid": batch["batch_uuid"],
            },
        )
    ).mappings().one()
    aspects = [
        dict(row)
        for row in (
            await session.execute(
                text(
                    """
                    SELECT id aspect_id,review_id,aspect_index,taxonomy_code,
                           sentiment,severity,evidence_start,evidence_end,
                           evidence_quote,extraction_confidence::float8
                      FROM review_aspects
                     WHERE tenant_id=:tenant AND analysis_job_id=:task
                     ORDER BY review_id,aspect_index,id
                    """
                ),
                {"tenant": scope["tenant_id"], "task": task["id"]},
            )
        ).mappings().all()
    ]
    clusters = [
        dict(row)
        for row in (
            await session.execute(
                text(
                    """
                    SELECT id cluster_id,cluster_code,taxonomy_code,name,summary,
                           sentiment_distribution,aspect_count,review_count,
                           listing_count,mention_rate::float8,
                           importance_score::float8,cluster_confidence::float8,
                           representative_aspect_ids
                      FROM insight_clusters
                     WHERE tenant_id=:tenant AND analysis_job_id=:task
                     ORDER BY importance_score DESC,id
                    """
                ),
                {"tenant": scope["tenant_id"], "task": task["id"]},
            )
        ).mappings().all()
    ]
    opportunities = [
        dict(row)
        for row in (
            await session.execute(
                text(
                    """
                    SELECT id opportunity_id,opportunity_code,title,description,
                           primary_cluster_ids,demand_heat_score::float8,
                           demand_growth_score::float8,unmet_need_score::float8,
                           competition_space_score::float8,
                           profit_space_score::float8,market_score::float8,
                           adjusted_score::float8,confidence::float8,
                           recommendation_level,weight_config,manufacturing_fit
                      FROM market_opportunities
                     WHERE tenant_id=:tenant AND analysis_job_id=:task
                     ORDER BY base_score DESC,id
                    """
                ),
                {"tenant": scope["tenant_id"], "task": task["id"]},
            )
        ).mappings().all()
    ]
    competitors = [
        dict(row)
        for row in (
            await session.execute(
                text(
                    """
                    SELECT w.id watch_id,w.asin,w.title,w.status,
                           s.id snapshot_id,s.captured_at,s.sale_price::float8,
                           s.list_price::float8,s.currency,s.rating::float8,
                           s.review_count,s.fingerprint,
                           a.id alert_id,a.change_type,a.summary
                      FROM competitor_watch_targets w
                      JOIN competitor_listing_snapshots s
                        ON s.watch_id=w.id AND s.tenant_id=w.tenant_id
                      LEFT JOIN competitor_change_alerts a
                        ON a.watch_id=w.id AND a.tenant_id=w.tenant_id
                       AND a.change_type='new_listing'
                     WHERE w.tenant_id=:tenant AND w.dataset_id=:dataset
                     ORDER BY w.id,s.id,a.id
                    """
                ),
                {"tenant": scope["tenant_id"], "dataset": scope["id"]},
            )
        ).mappings().all()
    ]
    policy_sources = [
        dict(row)
        for row in (
            await session.execute(
                text(
                    """
                    SELECT id source_id,name,source_type,source_url,market_country,
                           category_code,keywords,authorization_reference,
                           schedule_minutes,enabled
                      FROM policy_sources
                     WHERE tenant_id=:tenant
                       AND source_url=ANY(CAST(:urls AS text[]))
                     ORDER BY id
                    """
                ),
                {
                    "tenant": scope["tenant_id"],
                    "urls": [
                        item["source_url"]
                        for item in package["capabilities"]["compliance"]["sources"]
                    ],
                },
            )
        ).mappings().all()
    ]
    lineage = [
        dict(row)
        for row in (
            await session.execute(
                text(
                    """
                    SELECT id,record_uuid::text,capability_code,record_kind,
                           source_class,target_table,target_record_id,natural_key,
                           source_locator,derivation_rule,rule_version,
                           input_sha256,payload_sha256,confidence::float8,
                           observed_at,valid_from,valid_until,created_at
                      FROM market_intelligence_lineage
                     WHERE tenant_id=:tenant AND batch_id=:batch
                     ORDER BY capability_code,target_table,natural_key,id
                    """
                ),
                {"tenant": scope["tenant_id"], "batch": batch["id"]},
            )
        ).mappings().all()
    ]
    overview = await MarketIntelligenceService().overview(
        session,
        tenant_id=scope["tenant_id"],
        dataset_id=scope["id"],
        product_id=scope["product_id"],
    )
    return _jsonable(
        {
            "schema_version": "market-decision-generated-data-v1",
            "generated_at": datetime.now(timezone.utc),
            "package": {
                "package_id": package["package_id"],
                "package_version": package["package_version"],
                "package_sha256": package_sha256,
            },
            "scope": {
                "tenant_id": scope["tenant_id"],
                "tenant_code": scope["tenant_code"],
                "dataset_id": scope["id"],
                "dataset_name": scope["dataset_name"],
                "authorization_reference": scope["authorization_reference"],
                "product_id": scope["product_id"],
                "product_sku": scope["sku"],
            },
            "governance_batch": batch_row,
            "source_summary": {
                "listing_count": scope["actual_listing_count"],
                "valid_review_count": scope["actual_review_count"],
                "quality_report": scope["quality_report"],
                "limitations": scope["limitations"],
            },
            "generated_records": {
                "smart_selection": {
                    "task": dict(task),
                    "opportunities": opportunities,
                },
                "competitor_tracking": competitors,
                "review_mining": {
                    "aspects": aspects,
                    "clusters": clusters,
                },
                "pricing": overview["pricing"],
                "compliance": {
                    "sources": policy_sources,
                    "overview": overview["compliance"],
                },
            },
            "overview": overview,
            "lineage": lineage,
        }
    )


async def apply_package(
    database: Database,
    *,
    package_path: Path,
    email: str | None,
) -> dict[str, Any]:
    package, package_sha256 = load_package(package_path)
    email = email or package["tenant_selector"]["email"]
    async with database.session_factory() as session:
        migration_ready = await session.scalar(
            text(
                """
                SELECT EXISTS(
                  SELECT FROM schema_migrations WHERE version=:version
                )
                """
            ),
            {"version": REQUIRED_MIGRATION},
        )
        if migration_ready is not True:
            raise RuntimeError(f"database migration is required: {REQUIRED_MIGRATION}")
        scope = await _resolve_scope(session, package, email=email)
        source = await _source_rows(session, scope)
        batch, created = await _ensure_batch(
            session,
            scope=scope,
            package=package,
            package_sha256=package_sha256,
            source=source,
        )
        if created:
            await _ensure_pricing_constraints(
                session,
                scope=scope,
                package=package,
                batch=batch,
                source=source,
            )
            task = await _ensure_task(
                session,
                scope=scope,
                package=package,
                batch=batch,
                source=source,
            )
            _, clusters = await _build_review_mining(
                session,
                scope=scope,
                package=package,
                batch=batch,
                source=source,
                task=task,
            )
            await _build_opportunities(
                session,
                scope=scope,
                package=package,
                batch=batch,
                source=source,
                task=task,
                clusters=clusters,
            )
            await _ensure_policy_sources(
                session,
                scope=scope,
                package=package,
                batch=batch,
            )
            await _activate_batch(
                session,
                scope=scope,
                package=package,
                batch=batch,
            )
        await _build_competitor_tracking(
            session,
            scope=scope,
            package=package,
            batch=batch,
            source=source,
        )
        await session.commit()

    async with database.session_factory() as session:
        scope = await _resolve_scope(session, package, email=email)
        active = (
            await session.execute(
                text(
                    """
                    SELECT id,batch_uuid::text,status,package_sha256
                      FROM market_intelligence_batches
                     WHERE tenant_id=:tenant AND package_id=:package_id
                       AND package_version=:package_version
                    """
                ),
                {
                    "tenant": scope["tenant_id"],
                    "package_id": package["package_id"],
                    "package_version": package["package_version"],
                },
            )
        ).mappings().one()
        result = await _export(
            session,
            scope=scope,
            package=package,
            package_sha256=package_sha256,
            batch=dict(active),
        )
        await session.rollback()
        return result


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, default=DEFAULT_PACKAGE)
    parser.add_argument("--email")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    database = Database(get_settings())
    try:
        result = await apply_package(
            database,
            package_path=args.package.resolve(),
            email=args.email,
        )
    finally:
        await database.close()
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(
        json.dumps(
            {
                "batch_uuid": result["governance_batch"]["batch_uuid"],
                "status": result["governance_batch"]["status"],
                "dataset_id": result["scope"]["dataset_id"],
                "opportunity_count": len(
                    result["generated_records"]["smart_selection"]["opportunities"]
                ),
                "cluster_count": len(
                    result["generated_records"]["review_mining"]["clusters"]
                ),
                "watch_count": len(
                    result["generated_records"]["competitor_tracking"]
                ),
                "policy_source_count": len(
                    result["generated_records"]["compliance"]["sources"]
                ),
                "lineage_count": len(result["lineage"]),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
