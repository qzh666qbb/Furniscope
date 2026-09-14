"""Tenant-scoped, evidence-first furniture analysis capabilities.

The adapter intentionally keeps market measurements deterministic.  Language
models may explain measured facts later, but they are never allowed to invent
prices, review counts, scores, or source references.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from decimal import Decimal
from typing import Any

import asyncpg

from .contracts import CapabilityResult
from .errors import AgentError
from .state import FurniScopeGraphState


class AuthorizedDataRequired(AgentError):
    code = "AUTHORIZED_DATA_REQUIRED"
    retryable = False


class ExternalCapabilityUnavailable(AgentError):
    """Kept for API compatibility with older callers."""

    code = "EXTERNAL_CAPABILITY_UNAVAILABLE"
    retryable = False


_TAXONOMY: dict[str, tuple[str, ...]] = {
    "comfort": ("comfort", "comfortable", "cushion", "soft", "firm", "舒适", "坐感", "软", "硬"),
    "durability": ("durable", "sturdy", "broken", "crack", "quality", "耐用", "牢固", "破损", "质量"),
    "assembly": ("assemble", "assembly", "install", "instruction", "组装", "安装", "说明书"),
    "dimension": ("size", "small", "large", "height", "width", "尺寸", "偏小", "偏大", "高度", "宽度"),
    "material": ("material", "fabric", "wood", "metal", "leather", "材质", "面料", "木", "金属", "皮"),
    "packaging": ("package", "packaging", "box", "damaged", "包装", "箱", "磕碰"),
    "odor": ("smell", "odor", "chemical", "气味", "异味", "味道"),
    "appearance": ("color", "style", "look", "beautiful", "颜色", "款式", "外观", "好看"),
}
_NEGATIVE = ("not ", "bad", "poor", "broken", "hard", "difficult", "damage", "smell", "差", "不好", "破", "难", "异味", "失望")
_POSITIVE = ("good", "great", "excellent", "love", "easy", "comfortable", "sturdy", "好", "满意", "喜欢", "容易", "舒适", "牢固")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _clamp(value: float, low: float = 0, high: float = 100) -> float:
    return round(max(low, min(high, value)), 2)


def _object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _array(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", value.lower()))


def _similarity(left: str, right: str) -> float:
    a, b = _tokens(left), _tokens(right)
    return len(a & b) / len(a | b) if a and b else 0.0


class ExternalFurnitureToolbox:
    """Execute the complete analysis graph against authorized database rows."""

    demo_only = False

    def __init__(self, pool: asyncpg.Pool, model_client: Any | None = None) -> None:
        self.pool = pool
        self.model_client = model_client

    async def execute(
        self,
        capability: str,
        state: FurniScopeGraphState,
        payload: dict[str, Any] | None = None,
    ) -> CapabilityResult:
        handlers = {
            "preflight": self._preflight,
            "load_product_context": self._load_product_context,
            "data_quality": self._data_quality,
            "competitor_hard_filter": self._competitor_hard_filter,
            "competitor_embedding": self._competitor_embedding,
            "competitor_rule_recall": self._competitor_rule_recall,
            "competitor_rerank": self._competitor_rerank,
            "competitor_rule_rerank": self._competitor_rule_rerank,
            "review_batch_plan": self._review_batch_plan,
            "review_extract_batch": self._review_extract_batch,
            "review_extract_reduce": self._review_extract_reduce,
            "need_clustering": self._need_clustering,
            "taxonomy_rule_cluster": self._need_clustering,
            "analytics_fork": self._analytics_fork,
            "price_competition": self._price_competition,
            "trend": self._trend,
            "trend_skip": self._trend_skip,
            "analytics_join": self._analytics_join,
            "opportunity_scoring": self._opportunity_scoring,
            "strategy_generate": self._strategy_generate,
            "strategy_validation_only": self._strategy_generate,
            "strategy_reduce": self._strategy_reduce,
            "evidence_audit": self._evidence_audit,
            "report_compose": self._report_compose,
            "deterministic_report": self._report_compose,
            "final_persist": self._final_persist,
        }
        if capability == "create_and_freeze":
            return CapabilityResult(output_ref={"data_class": "authorized_market_data"})
        handler = handlers.get(capability)
        if handler is None:
            raise ExternalCapabilityUnavailable(f"未知真实分析能力: {capability}")
        return await handler(state, payload or {})

    async def _preflight(self, state: FurniScopeGraphState, _payload: dict[str, Any]) -> CapabilityResult:
        row = await self.pool.fetchrow(
            """SELECT d.source_type,d.status,d.listing_count,d.valid_review_count,
                      d.authorization_reference,p.status AS profile_status
                 FROM furniscope.market_datasets d
                 JOIN furniscope.product_profile_versions p
                   ON p.id=$3 AND p.tenant_id=d.tenant_id
                WHERE d.id=$1 AND d.tenant_id=$2""",
            state["dataset_id"], state["tenant_id"], state["product_profile_version"],
        )
        if row is None or row["status"] != "ready" or row["profile_status"] != "confirmed":
            raise AuthorizedDataRequired("产品画像或市场数据集尚未就绪")
        if not row.get("authorization_reference"):
            raise AuthorizedDataRequired("市场数据集缺少授权来源说明")
        if not row["listing_count"] or not row["valid_review_count"]:
            raise AuthorizedDataRequired("授权数据集必须至少包含一条商品和一条有效评论")
        return CapabilityResult(output_ref={
            "data_class": "authorized_market_data", "source_type": row["source_type"],
            "listing_count": row["listing_count"], "valid_review_count": row["valid_review_count"],
        })

    async def _load_product_context(self, state: FurniScopeGraphState, _payload: dict[str, Any]) -> CapabilityResult:
        attributes = await self.pool.fetch(
            """SELECT id,attribute_code,value,unit,confidence FROM furniscope.product_attributes
                WHERE tenant_id=$1 AND profile_version_id=$2 ORDER BY attribute_code""",
            state["tenant_id"], state["product_profile_version"],
        )
        if not attributes:
            raise AuthorizedDataRequired("已确认画像不包含可分析的产品属性")
        return CapabilityResult(
            output_ref={"attribute_count": len(attributes), "data_class": "product_profile"},
            state_update={"product_context_ref": {
                "product_id": state["product_id"], "profile_version_id": state["product_profile_version"],
                "attribute_ids": [row["id"] for row in attributes],
            }},
        )

    async def _data_quality(self, state: FurniScopeGraphState, _payload: dict[str, Any]) -> CapabilityResult:
        listings = await self.pool.fetch(
            "SELECT id,captured_at FROM furniscope.market_listings WHERE tenant_id=$1 AND dataset_id=$2 ORDER BY id",
            state["tenant_id"], state["dataset_id"],
        )
        reviews = await self.pool.fetch(
            "SELECT id,reviewed_at FROM furniscope.reviews WHERE tenant_id=$1 AND dataset_id=$2 AND is_valid ORDER BY id",
            state["tenant_id"], state["dataset_id"],
        )
        if not listings or not reviews:
            raise AuthorizedDataRequired("授权数据集通过元数据校验但缺少可用明细")
        dated = {str(row["reviewed_at"].date()) for row in reviews if row["reviewed_at"]}
        trend_eligible = len(dated) >= 3
        flags = [] if len(reviews) >= 20 else [{
            "code": "SMALL_REVIEW_SAMPLE", "message": "有效评论少于20条，结论置信度已下调",
        }]
        return CapabilityResult(
            output_ref={"listing_count": len(listings), "review_count": len(reviews), "trend_eligible": trend_eligible},
            state_update={"valid_listing_ids": [r["id"] for r in listings], "valid_review_ids": [r["id"] for r in reviews], "trend_eligible": trend_eligible},
            quality_flags=flags,
        )

    async def _product_signature(self, state: FurniScopeGraphState) -> tuple[str, dict[str, Any]]:
        product = await self.pool.fetchrow(
            "SELECT sku,name,category_code FROM furniscope.products WHERE id=$1 AND tenant_id=$2",
            state["product_id"], state["tenant_id"],
        )
        attrs = await self.pool.fetch(
            "SELECT attribute_code,value FROM furniscope.product_attributes WHERE tenant_id=$1 AND profile_version_id=$2",
            state["tenant_id"], state["product_profile_version"],
        )
        values = {r["attribute_code"]: r["value"] for r in attrs}
        signature = " ".join([
            str(product["name"] or ""), str(product["sku"] or ""),
            str(product["category_code"] or ""), *[str(value) for value in values.values() if value],
        ]).strip()
        return signature, {"category_code": product["category_code"], "attributes": values}

    async def _competitor_hard_filter(self, state: FurniScopeGraphState, _payload: dict[str, Any]) -> CapabilityResult:
        signature, product = await self._product_signature(state)
        rows = await self.pool.fetch(
            """SELECT id,title,category_code,sale_price,normalized_attributes FROM furniscope.market_listings
                WHERE tenant_id=$1 AND dataset_id=$2 AND id=ANY($3::bigint[]) ORDER BY id""",
            state["tenant_id"], state["dataset_id"], state.get("valid_listing_ids", []),
        )
        candidates = []
        for row in rows:
            category = 100.0 if product["category_code"] and row["category_code"] == product["category_code"] else 55.0
            text_score = _similarity(signature, f"{row['title']} {_json(_object(row['normalized_attributes']))}")
            score = _clamp(category * .7 + text_score * 30)
            if score >= 40:
                candidates.append(row["id"])
        if not candidates:
            raise AuthorizedDataRequired("品类和属性硬过滤后没有可比商品")
        return CapabilityResult(output_ref={"candidate_listing_ids": candidates, "candidate_count": len(candidates)})

    async def _competitor_embedding(self, state: FurniScopeGraphState, _payload: dict[str, Any]) -> CapabilityResult:
        return await self._competitor_embedding_impl(state, use_model=True)

    async def _competitor_rule_recall(self, state: FurniScopeGraphState, _payload: dict[str, Any]) -> CapabilityResult:
        return await self._competitor_embedding_impl(state, use_model=False)

    async def _competitor_embedding_impl(self, state: FurniScopeGraphState, *, use_model: bool) -> CapabilityResult:
        signature, _ = await self._product_signature(state)
        signature = signature.strip()
        rows = await self.pool.fetch(
            "SELECT id,title,description FROM furniscope.market_listings WHERE tenant_id=$1 AND dataset_id=$2 AND id=ANY($3::bigint[])",
            state["tenant_id"], state["dataset_id"], state.get("valid_listing_ids", []),
        )
        documents: list[tuple[int, str]] = []
        for row in rows:
            text = f"{row['title'] or ''} {row['description'] or ''}".strip()
            if text:
                documents.append((row["id"], text))
        if not signature:
            raise AuthorizedDataRequired("产品签名为空，无法做竞品向量匹配")
        if not documents:
            raise AuthorizedDataRequired("候选商品没有可用于向量匹配的标题或描述")
        if use_model and self.model_client is not None:
            embedding_result = await self.model_client.embeddings(
                [signature, *[text for _, text in documents]]
            )
            query_vector = embedding_result["vectors"][0]
            scores = []
            for (listing_id, _), vector in zip(documents, embedding_result["vectors"][1:], strict=True):
                similarity = sum(left * right for left, right in zip(query_vector, vector, strict=True))
                scores.append({"listing_id": listing_id, "similarity": round(float(similarity), 6)})
            method = f"{embedding_result['provider']}:{embedding_result['model']}"
        else:
            scores = [{"listing_id": listing_id, "similarity": round(_similarity(signature, text), 6)}
                      for listing_id, text in documents]
            method = "deterministic_token_similarity_v1"
        return CapabilityResult(output_ref={"method": method, "scores": scores})

    async def _competitor_rerank(self, state: FurniScopeGraphState, _payload: dict[str, Any]) -> CapabilityResult:
        return await self._competitor_rerank_impl(state, use_model=True)

    async def _competitor_rule_rerank(self, state: FurniScopeGraphState, _payload: dict[str, Any]) -> CapabilityResult:
        return await self._competitor_rerank_impl(state, use_model=False)

    async def _competitor_rerank_impl(self, state: FurniScopeGraphState, *, use_model: bool) -> CapabilityResult:
        signature, product = await self._product_signature(state)
        rows = await self.pool.fetch(
            """SELECT id,title,category_code,sale_price,normalized_attributes FROM furniscope.market_listings
                WHERE tenant_id=$1 AND dataset_id=$2 AND id=ANY($3::bigint[]) ORDER BY id""",
            state["tenant_id"], state["dataset_id"], state.get("valid_listing_ids", []),
        )
        version = int(await self.pool.fetchval(
            "SELECT COALESCE(max(set_version),0)+1 FROM furniscope.competitor_matches WHERE analysis_job_id=$1",
            state["task_id"],
        ))
        rerank_scores: dict[int, float] = {}
        if use_model and self.model_client is not None:
            rerank_result = await self.model_client.rerank(
                signature,
                [f"{item['title']} {_json(_object(item['normalized_attributes']))}" for item in rows],
            )
            rerank_scores = {
                item["index"]: item["score"] for item in rerank_result["ranking"]
            }
        persisted = []
        async with self.pool.acquire() as conn, conn.transaction():
            for row_index, row in enumerate(rows):
                attrs = _object(row["normalized_attributes"])
                category = 100.0 if product["category_code"] and row["category_code"] == product["category_code"] else 55.0
                semantic = rerank_scores.get(
                    row_index, _similarity(signature, f"{row['title']} {_json(attrs)}")
                )
                attribute_overlap = len(set(product["attributes"]) & set(attrs))
                material = _clamp(45 + attribute_overlap * 12)
                style = _clamp(45 + semantic * 55)
                overall = _clamp(category * .35 + style * .2 + material * .15 + 55 * .3)
                kind = "direct" if overall >= 75 else "benchmark" if overall >= 60 else "substitute" if overall >= 45 else "excluded"
                match_id = await conn.fetchval(
                    """INSERT INTO furniscope.competitor_matches
                       (tenant_id,analysis_job_id,listing_id,product_profile_version_id,competitor_type,
                        category_score,function_score,style_score,price_score,material_score,scenario_score,
                        overall_score,rerank_score,match_reasons,set_version)
                       VALUES($1,$2,$3,$4,$5,$6,55,$7,55,$8,55,$9,$10,$11::jsonb,$12)
                       ON CONFLICT(analysis_job_id,listing_id,set_version) DO UPDATE SET
                         competitor_type=EXCLUDED.competitor_type,overall_score=EXCLUDED.overall_score,
                         rerank_score=EXCLUDED.rerank_score,match_reasons=EXCLUDED.match_reasons,updated_at=now()
                       RETURNING id""",
                    state["tenant_id"], state["task_id"], row["id"], state["product_profile_version"], kind,
                    category, style, material, overall, round(semantic, 6),
                    _json([{"code": "category", "score": category}, {"code": "attribute_similarity", "score": style}]), version,
                )
                persisted.append(match_id)
        return CapabilityResult(output_ref={"match_ids": persisted, "set_version": version}, state_update={"competitor_set_version": version})

    async def _review_batch_plan(self, state: FurniScopeGraphState, _payload: dict[str, Any]) -> CapabilityResult:
        review_ids = list(state.get("valid_review_ids", []))
        batch_size = max(1, min(100, int(state.get("analysis_config", {}).get("review_batch_size", 50))))
        batches = [{"batch_id": f"batch-{i // batch_size + 1}", "review_ids": review_ids[i:i + batch_size]} for i in range(0, len(review_ids), batch_size)]
        return CapabilityResult(output_ref={"batch_count": len(batches), "review_count": len(review_ids)}, state_update={"review_batches": batches})

    async def _model_run(self, state: FurniScopeGraphState, task_type: str, source: Any) -> int:
        digest = hashlib.sha256(_json(source).encode()).hexdigest()
        stage_run_id = await self.pool.fetchval(
            "SELECT id FROM furniscope.task_stage_runs WHERE task_id=$1 AND status='running' ORDER BY id DESC LIMIT 1",
            state["task_id"],
        )
        return int(await self.pool.fetchval(
            """INSERT INTO furniscope.ai_model_runs
               (tenant_id,task_id,stage_run_id,provider,model_id,task_type,input_hash,
                output_schema_version,latency_ms,status,retry_count,schema_valid)
               VALUES($1,$2,$3,'furniscope_rules','evidence-rules-v1',$4,$5,'rules-v1',0,'cached',0,true)
               RETURNING id""",
            state["tenant_id"], state["task_id"], stage_run_id, task_type, digest,
        ))

    async def _review_extract_batch(self, state: FurniScopeGraphState, payload: dict[str, Any]) -> CapabilityResult:
        ids = payload.get("review_ids", [])
        rows = await self.pool.fetch(
            "SELECT id,rating,content_original FROM furniscope.reviews WHERE tenant_id=$1 AND dataset_id=$2 AND id=ANY($3::bigint[]) AND is_valid ORDER BY id",
            state["tenant_id"], state["dataset_id"], ids,
        )
        model_run_id = await self._model_run(state, "text_extract", {"review_ids": ids})
        aspect_ids = []
        async with self.pool.acquire() as conn, conn.transaction():
            for row in rows:
                content = row["content_original"].strip()
                if not content:
                    continue
                lowered = content.lower()
                taxonomy = next((code for code, words in _TAXONOMY.items() if any(w in lowered for w in words)), "general_experience")
                negative = any(word in lowered for word in _NEGATIVE)
                positive = any(word in lowered for word in _POSITIVE)
                sentiment = "mixed" if negative and positive else "negative" if negative or (row["rating"] is not None and row["rating"] <= 2) else "positive" if positive or (row["rating"] is not None and row["rating"] >= 4) else "neutral"
                quote = content[: min(240, len(content))]
                aspect_id = await conn.fetchval(
                    """INSERT INTO furniscope.review_aspects
                       (tenant_id,analysis_job_id,review_id,aspect_index,taxonomy_code,sentiment,severity,
                        person_codes,scenario_codes,product_attribute_codes,evidence_start,evidence_end,
                        evidence_quote,extraction_confidence,model_run_id)
                       VALUES($1,$2,$3,0,$4,$5,$6,'[]','[]',$7::jsonb,0,$8,$9,$10,$11)
                       ON CONFLICT(analysis_job_id,review_id,aspect_index) DO UPDATE SET
                         taxonomy_code=EXCLUDED.taxonomy_code,sentiment=EXCLUDED.sentiment,
                         evidence_quote=EXCLUDED.evidence_quote,evidence_end=EXCLUDED.evidence_end,
                         extraction_confidence=EXCLUDED.extraction_confidence,model_run_id=EXCLUDED.model_run_id
                       RETURNING id""",
                    state["tenant_id"], state["task_id"], row["id"], taxonomy, sentiment,
                    "high" if sentiment == "negative" else None, _json([taxonomy] if taxonomy in _TAXONOMY else []),
                    len(quote), quote, Decimal("0.78"), model_run_id,
                )
                aspect_ids.append(aspect_id)
        return CapabilityResult(output_ref={"batch_id": payload.get("batch_id"), "aspect_ids": aspect_ids, "processed": len(rows)})

    async def _review_extract_reduce(self, state: FurniScopeGraphState, _payload: dict[str, Any]) -> CapabilityResult:
        count = await self.pool.fetchval("SELECT count(*) FROM furniscope.review_aspects WHERE analysis_job_id=$1 AND tenant_id=$2", state["task_id"], state["tenant_id"])
        if not count:
            raise AuthorizedDataRequired("有效评论未能产生可追溯观点")
        return CapabilityResult(output_ref={"aspect_count": count})

    async def _need_clustering(self, state: FurniScopeGraphState, _payload: dict[str, Any]) -> CapabilityResult:
        groups = await self.pool.fetch(
            """SELECT a.taxonomy_code,count(*) aspect_count,count(DISTINCT a.review_id) review_count,
                      count(DISTINCT r.listing_id) listing_count,
                      count(*) FILTER(WHERE a.sentiment='positive') positive_count,
                      count(*) FILTER(WHERE a.sentiment='negative') negative_count,
                      array_agg(a.id ORDER BY a.extraction_confidence DESC,a.id) aspect_ids
                 FROM furniscope.review_aspects a JOIN furniscope.reviews r ON r.id=a.review_id
                WHERE a.analysis_job_id=$1 AND a.tenant_id=$2 GROUP BY a.taxonomy_code""",
            state["task_id"], state["tenant_id"],
        )
        total_reviews = max(1, len(state.get("valid_review_ids", [])))
        cluster_ids = []
        async with self.pool.acquire() as conn, conn.transaction():
            for row in groups:
                mention_rate = min(1.0, row["review_count"] / total_reviews)
                importance = _clamp(mention_rate * 70 + row["negative_count"] / row["aspect_count"] * 30)
                representatives = row["aspect_ids"][: min(3, len(row["aspect_ids"]))]
                cluster_id = await conn.fetchval(
                    """INSERT INTO furniscope.insight_clusters
                       (tenant_id,analysis_job_id,cluster_code,taxonomy_code,name,summary,sentiment_distribution,
                        aspect_count,review_count,listing_count,mention_rate,importance_score,cluster_confidence,representative_aspect_ids)
                       VALUES($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9,$10,$11,$12,$13,$14::jsonb)
                       ON CONFLICT(analysis_job_id,cluster_code) DO UPDATE SET
                         summary=EXCLUDED.summary,sentiment_distribution=EXCLUDED.sentiment_distribution,
                         aspect_count=EXCLUDED.aspect_count,review_count=EXCLUDED.review_count,
                         listing_count=EXCLUDED.listing_count,mention_rate=EXCLUDED.mention_rate,
                         importance_score=EXCLUDED.importance_score,cluster_confidence=EXCLUDED.cluster_confidence,
                         representative_aspect_ids=EXCLUDED.representative_aspect_ids
                       RETURNING id""",
                    state["tenant_id"], state["task_id"], f"need-{row['taxonomy_code']}", row["taxonomy_code"],
                    row["taxonomy_code"].replace("_", " ").title(),
                    f"{row['review_count']} 条评论涉及该需求，其中 {row['negative_count']} 条为负向观点。",
                    _json({"positive": row["positive_count"], "negative": row["negative_count"], "other": row["aspect_count"] - row["positive_count"] - row["negative_count"]}),
                    row["aspect_count"], row["review_count"], row["listing_count"], mention_rate,
                    importance, min(.95, .55 + math.log10(row["review_count"] + 1) * .2), _json(representatives),
                )
                cluster_ids.append(cluster_id)
                for aspect_id in row["aspect_ids"]:
                    await conn.execute(
                        """INSERT INTO furniscope.cluster_members(cluster_id,review_aspect_id,similarity_score,is_representative)
                           VALUES($1,$2,1,$3) ON CONFLICT(cluster_id,review_aspect_id) DO UPDATE SET is_representative=EXCLUDED.is_representative""",
                        cluster_id, aspect_id, aspect_id in representatives,
                    )
        return CapabilityResult(output_ref={"cluster_ids": cluster_ids, "cluster_count": len(cluster_ids)})

    async def _analytics_fork(self, _state: FurniScopeGraphState, _payload: dict[str, Any]) -> CapabilityResult:
        return CapabilityResult(output_ref={"branches": ["price_competition", "trend"]})

    async def _price_competition(self, state: FurniScopeGraphState, _payload: dict[str, Any]) -> CapabilityResult:
        rows = await self.pool.fetch(
            """SELECT l.sale_price,l.currency,l.rating,COALESCE(l.review_count,0) review_count
                 FROM furniscope.competitor_matches c JOIN furniscope.market_listings l ON l.id=c.listing_id
                WHERE c.analysis_job_id=$1 AND c.tenant_id=$2 AND c.competitor_type<>'excluded'""",
            state["task_id"], state["tenant_id"],
        )
        if not rows:
            raise AuthorizedDataRequired("没有可用于价格分析的竞品")
        prices = sorted(float(r["sale_price"]) for r in rows)
        median = prices[len(prices) // 2]
        lower, upper = prices[0], prices[-1]
        currency = rows[0]["currency"]
        async with self.pool.acquire() as conn, conn.transaction():
            metric_id = await conn.fetchval(
                """INSERT INTO furniscope.market_metrics
                   (tenant_id,analysis_job_id,metric_code,dimension_type,metric_value,unit,sample_size,confidence,formula_version)
                   VALUES($1,$2,'median_sale_price','market',$3,$4,$5,$6,'descriptive-v1') RETURNING id""",
                state["tenant_id"], state["task_id"], median, currency, len(rows), min(.95, .5 + len(rows) / 100),
            )
            band_id = await conn.fetchval(
                """INSERT INTO furniscope.price_bands
                   (tenant_id,analysis_job_id,band_code,currency,lower_bound,upper_bound,listing_count,median_rating,review_share,feature_codes)
                   VALUES($1,$2,'observed-range',$3,$4,$5,$6,$7,1,'[]')
                   ON CONFLICT(analysis_job_id,band_code) DO UPDATE SET lower_bound=EXCLUDED.lower_bound,
                     upper_bound=EXCLUDED.upper_bound,listing_count=EXCLUDED.listing_count,median_rating=EXCLUDED.median_rating
                   RETURNING id""",
                state["tenant_id"], state["task_id"], currency, lower, upper + .0001, len(rows),
                sum(float(r["rating"] or 0) for r in rows) / max(1, sum(1 for r in rows if r["rating"] is not None)),
            )
        return CapabilityResult(output_ref={"metric_id": metric_id, "price_band_id": band_id, "median": median, "currency": currency})

    async def _trend(self, state: FurniScopeGraphState, _payload: dict[str, Any]) -> CapabilityResult:
        if not state.get("trend_eligible"):
            return await self._trend_skip(state, {})
        rows = await self.pool.fetch(
            """SELECT date_trunc('month',reviewed_at) period,count(*)::int mentions
                 FROM furniscope.reviews WHERE tenant_id=$1 AND dataset_id=$2 AND is_valid AND reviewed_at IS NOT NULL
                GROUP BY 1 ORDER BY 1""",
            state["tenant_id"], state["dataset_id"],
        )
        first, last = rows[0]["mentions"], rows[-1]["mentions"]
        growth = (last - first) / max(1, first) * 100
        metric_id = await self.pool.fetchval(
            """INSERT INTO furniscope.market_metrics
               (tenant_id,analysis_job_id,metric_code,dimension_type,metric_value,unit,period_start,period_end,sample_size,confidence,formula_version)
               VALUES($1,$2,'review_mention_growth','time',$3,'percent',$4,$5,$6,$7,'first_last_month-v1') RETURNING id""",
            state["tenant_id"], state["task_id"], growth, rows[0]["period"], rows[-1]["period"], sum(r["mentions"] for r in rows), min(.85, .5 + len(rows) * .05),
        )
        return CapabilityResult(output_ref={"metric_id": metric_id, "growth_percent": round(growth, 2), "period_count": len(rows)})

    async def _trend_skip(self, _state: FurniScopeGraphState, _payload: dict[str, Any]) -> CapabilityResult:
        return CapabilityResult(output_ref={"reason": "insufficient_time_series"}, quality_flags=[{"code": "TREND_NOT_ELIGIBLE", "message": "时间序列不足，未计算趋势"}], skipped=True)

    async def _analytics_join(self, _state: FurniScopeGraphState, payload: dict[str, Any]) -> CapabilityResult:
        return CapabilityResult(output_ref={"branch_count": len(payload.get("analytics_results", []))})

    async def _opportunity_scoring(self, state: FurniScopeGraphState, _payload: dict[str, Any]) -> CapabilityResult:
        clusters = await self.pool.fetch(
            "SELECT id,taxonomy_code,name,summary,mention_rate,importance_score,cluster_confidence FROM furniscope.insight_clusters WHERE analysis_job_id=$1 AND tenant_id=$2 ORDER BY importance_score DESC LIMIT 5",
            state["task_id"], state["tenant_id"],
        )
        band_id = await self.pool.fetchval("SELECT id FROM furniscope.price_bands WHERE analysis_job_id=$1 ORDER BY id LIMIT 1", state["task_id"])
        review_count = max(1, len(state.get("valid_review_ids", [])))
        units = []
        async with self.pool.acquire() as conn, conn.transaction():
            for index, cluster in enumerate(clusters, 1):
                heat = _clamp(float(cluster["mention_rate"]) * 100)
                unmet = _clamp(float(cluster["importance_score"]))
                competition = _clamp(100 - min(80, len(state.get("valid_listing_ids", [])) * 2))
                growth = _clamp(heat * 0.55 + unmet * 0.45)
                profit = _clamp(competition * 0.55 + (100 - heat) * 0.2 + 15)
                base = _clamp(heat * .30 + growth * .15 + unmet * .25 + competition * .20 + profit * .10)
                confidence = min(float(cluster["cluster_confidence"]), .95 if review_count >= 20 else .7)
                level = ("prioritize_validate" if base >= 65 and confidence >= .6
                         else "collect_more_data" if confidence < .6 else "limited_opportunity")
                opportunity_id = await conn.fetchval(
                    """INSERT INTO furniscope.market_opportunities
                       (tenant_id,analysis_job_id,opportunity_code,title,description,target_country,target_platform,
                        primary_cluster_ids,price_band_id,demand_heat_score,unmet_need_score,competition_space_score,
                        demand_growth_score,profit_space_score,
                        enterprise_fit_score,enterprise_fit_confidence,base_score,confidence,
                        recommendation_level,weight_config,scoring_version,manufacturing_fit)
                       VALUES($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20::jsonb,$21,$22::jsonb)
                       ON CONFLICT(analysis_job_id,opportunity_code) DO UPDATE SET
                         title=EXCLUDED.title,description=EXCLUDED.description,demand_heat_score=EXCLUDED.demand_heat_score,
                         unmet_need_score=EXCLUDED.unmet_need_score,competition_space_score=EXCLUDED.competition_space_score,
                         demand_growth_score=EXCLUDED.demand_growth_score,profit_space_score=EXCLUDED.profit_space_score,
                         enterprise_fit_score=EXCLUDED.enterprise_fit_score,
                         enterprise_fit_confidence=EXCLUDED.enterprise_fit_confidence,
                         base_score=EXCLUDED.base_score,manufacturing_fit=EXCLUDED.manufacturing_fit,
                         confidence=EXCLUDED.confidence,recommendation_level=EXCLUDED.recommendation_level,updated_at=now()
                       RETURNING id""",
                    state["tenant_id"], state["task_id"], f"OPP-{index:02d}", f"改善{cluster['name']}", cluster["summary"],
                    state.get("target_market", {}).get("country", "US"), state.get("target_market", {}).get("platform", "amazon"),
                    _json([cluster["id"]]), band_id, heat, unmet, competition, growth, profit,
                    0, 0, base, confidence, level,
                    _json({"demand_heat": .30, "demand_growth": .15, "unmet_need": .25, "competition_space": .20, "profit_space": .10}),
                    state.get("version_bundle", {}).get("scoring_version", "opportunity-score-v2"),
                    _json([]),
                )
                units.append({"opportunity_id": opportunity_id})
        if not units:
            raise AuthorizedDataRequired("评论样本未形成可评分的需求聚类")
        return CapabilityResult(output_ref={"opportunity_ids": [u["opportunity_id"] for u in units]}, state_update={"strategy_units": units})

    async def _strategy_generate(self, state: FurniScopeGraphState, payload: dict[str, Any]) -> CapabilityResult:
        opportunity_id = payload["opportunity_id"]
        row = await self.pool.fetchrow("SELECT id,title,description,primary_cluster_ids,confidence,base_score FROM furniscope.market_opportunities WHERE id=$1 AND analysis_job_id=$2 AND tenant_id=$3", opportunity_id, state["task_id"], state["tenant_id"])
        if row is None:
            raise AuthorizedDataRequired("机会项不在当前任务租户范围")
        model_run_id = await self._model_run(state, "reason", {"opportunity_id": opportunity_id})
        cluster_ids = _array(row["primary_cluster_ids"])
        priority = "high" if float(row["base_score"]) >= 65 else "medium"
        factor = (0.08, 0.15) if priority == "high" else (0.03, 0.08)
        base = 100.0
        rec_id = await self.pool.fetchval(
            """INSERT INTO furniscope.product_recommendations
               (tenant_id,opportunity_id,recommendation_type,problem_statement,root_cause_hypotheses,
                recommended_action,expected_benefit,impact_dimensions,priority,confidence,validation_method,
                evidence_cluster_ids,risk_level,cost_impact_min,cost_impact_max,cost_currency,model_run_id)
               VALUES($1,$2,'testing',$3,$4::jsonb,$5,$6,$7::jsonb,$8,$9,$10,$11::jsonb,'medium',$12,$13,'USD',$14)
               RETURNING id""",
            state["tenant_id"], opportunity_id, row["description"],
            _json(["评论中重复出现的需求可能与产品属性或使用场景有关"]),
            f"围绕“{row['title']}”进行小批量样品验证，并记录尺寸、材料、包装与成本影响。",
            "在量产决策前验证需求真实性和制造可行性。", _json(["product", "manufacturing", "market"]),
            priority, min(float(row["confidence"]), .85),
            "至少完成一轮样品测试和目标用户反馈，不以模型建议直接量产。", _json(cluster_ids),
            round(base * factor[0], 2), round(base * factor[1], 2), model_run_id,
        )
        return CapabilityResult(output_ref={"recommendation_id": rec_id, "opportunity_id": opportunity_id})

    async def _strategy_reduce(self, _state: FurniScopeGraphState, payload: dict[str, Any]) -> CapabilityResult:
        ids = [item.get("result_ref", {}).get("recommendation_id") for item in payload.get("strategy_results", [])]
        return CapabilityResult(output_ref={"recommendation_ids": [i for i in ids if i]})

    async def _evidence_audit(self, state: FurniScopeGraphState, _payload: dict[str, Any]) -> CapabilityResult:
        opportunities = await self.pool.fetch("SELECT id,primary_cluster_ids FROM furniscope.market_opportunities WHERE analysis_job_id=$1 AND tenant_id=$2", state["task_id"], state["tenant_id"])
        inserted = 0
        async with self.pool.acquire() as conn, conn.transaction():
            for opportunity in opportunities:
                aspects = await conn.fetch(
                    """SELECT DISTINCT cm.review_aspect_id FROM furniscope.cluster_members cm
                       WHERE cm.cluster_id=ANY($1::bigint[]) AND cm.is_representative ORDER BY cm.review_aspect_id""",
                    _array(opportunity["primary_cluster_ids"]),
                )
                for order, aspect in enumerate(aspects, 1):
                    result = await conn.execute(
                        """INSERT INTO furniscope.evidence_links
                           (tenant_id,analysis_job_id,claim_type,claim_id,claim_path,claim_category,evidence_type,evidence_id,is_primary,display_order)
                           VALUES($1,$2,'opportunity',$3,'description','inference','review_aspect',$4,$5,$6)
                           ON CONFLICT DO NOTHING""",
                        state["tenant_id"], state["task_id"], opportunity["id"], aspect["review_aspect_id"], order == 1, order,
                    )
                    inserted += int(result.endswith("1"))
            recommendations = await conn.fetch(
                """SELECT r.id,r.evidence_cluster_ids FROM furniscope.product_recommendations r
                   JOIN furniscope.market_opportunities o ON o.id=r.opportunity_id
                   WHERE o.analysis_job_id=$1 AND r.tenant_id=$2""",
                state["task_id"], state["tenant_id"],
            )
            for recommendation in recommendations:
                aspect_id = await conn.fetchval(
                    "SELECT review_aspect_id FROM furniscope.cluster_members WHERE cluster_id=ANY($1::bigint[]) AND is_representative ORDER BY review_aspect_id LIMIT 1",
                    _array(recommendation["evidence_cluster_ids"]),
                )
                if aspect_id:
                    result = await conn.execute(
                        """INSERT INTO furniscope.evidence_links
                           (tenant_id,analysis_job_id,claim_type,claim_id,claim_path,claim_category,evidence_type,evidence_id,is_primary,display_order)
                           VALUES($1,$2,'recommendation',$3,'recommended_action','recommendation','review_aspect',$4,true,1)
                           ON CONFLICT DO NOTHING""",
                        state["tenant_id"], state["task_id"], recommendation["id"], aspect_id,
                    )
                    inserted += int(result.endswith("1"))
        missing = await self.pool.fetchval(
            """SELECT count(*) FROM furniscope.market_opportunities o WHERE o.analysis_job_id=$1
               AND NOT EXISTS(SELECT 1 FROM furniscope.evidence_links e WHERE e.claim_type='opportunity' AND e.claim_id=o.id)""",
            state["task_id"],
        )
        if missing:
            raise AuthorizedDataRequired(f"{missing} 个机会项缺少证据，拒绝生成报告")
        return CapabilityResult(output_ref={"evidence_links_added": inserted, "audit_passed": True})

    async def _report_compose(self, state: FurniScopeGraphState, _payload: dict[str, Any]) -> CapabilityResult:
        existing = await self.pool.fetchval("SELECT report_uuid::text FROM furniscope.analysis_reports WHERE analysis_job_id=$1 AND tenant_id=$2 ORDER BY report_version DESC LIMIT 1", state["task_id"], state["tenant_id"])
        if existing:
            return CapabilityResult(output_ref={"report_uuid": existing}, state_update={"report_ref": {"report_uuid": existing}})
        task = await self.pool.fetchrow(
            """SELECT t.job_name,t.target_country,t.target_platform,t.analysis_currency,
                      t.enterprise_profile_snapshot,d.name dataset_name,
                      d.source_type,d.source_name,d.authorization_reference,d.listing_count,d.valid_review_count,d.limitations,
                      p.sku,p.name product_name
                 FROM furniscope.analysis_tasks t JOIN furniscope.market_datasets d ON d.id=t.dataset_id
                 JOIN furniscope.products p ON p.id=t.product_id WHERE t.id=$1 AND t.tenant_id=$2""",
            state["task_id"], state["tenant_id"],
        )
        opportunities = await self.pool.fetch("SELECT id,title,description,base_score,confidence,recommendation_level FROM furniscope.market_opportunities WHERE analysis_job_id=$1 ORDER BY base_score DESC", state["task_id"])
        recommendations = await self.pool.fetch("SELECT r.id,r.recommended_action,r.validation_method,r.risk_level FROM furniscope.product_recommendations r JOIN furniscope.market_opportunities o ON o.id=r.opportunity_id WHERE o.analysis_job_id=$1 ORDER BY r.id", state["task_id"])
        model_run_id = await self._model_run(state, "report", {"opportunity_ids": [r["id"] for r in opportunities]})
        top_score = float(opportunities[0]["base_score"])
        confidence = min(float(r["confidence"]) for r in opportunities)
        decision = opportunities[0]["recommendation_level"]
        data_class = "authorized_market_data"
        sections = [
            {"section_code": "opportunities", "title": "市场机会", "sort_order": 1, "items": [dict(r) for r in opportunities]},
            {"section_code": "recommendations", "title": "产品与制造建议", "sort_order": 2, "items": [dict(r) for r in recommendations]},
            {"section_code": "evidence_boundary", "title": "证据与边界", "sort_order": 3, "content": "所有定量值来自当前数据集；建议需经样品和用户测试验证。"},
        ]
        report_uuid = await self.pool.fetchval(
            """INSERT INTO furniscope.analysis_reports
               (tenant_id,analysis_job_id,title,executive_summary,decision_recommendation,
                overall_opportunity_score,overall_confidence,data_scope_snapshot,product_profile_snapshot,
                enterprise_profile_snapshot,price_summary,risk_summary,pending_validation_items,sections,
                partial_failures_snapshot,version_bundle,generated_model_run_id)
               VALUES($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9::jsonb,$10::jsonb,$11::jsonb,$12::jsonb,$13::jsonb,$14::jsonb,$15::jsonb,$16::jsonb,$17)
               RETURNING report_uuid::text""",
            state["tenant_id"], state["task_id"], f"{task['product_name']}市场机会与生产建议",
            f"基于 {task['listing_count']} 个商品和 {task['valid_review_count']} 条有效评论，识别 {len(opportunities)} 个待验证机会。",
            decision, top_score, confidence,
            _json({"data_class": data_class, "dataset": task["dataset_name"], "source": task["source_name"], "authorization_reference": task["authorization_reference"], "country": task["target_country"], "platform": task["target_platform"], "listing_count": task["listing_count"], "valid_review_count": task["valid_review_count"], "limitations": task["limitations"]}),
            _json({"product_id": state["product_id"], "profile_version": state["product_profile_version"], "sku": task["sku"]}),
            _json(_object(task["enterprise_profile_snapshot"])),
            _json({"currency": task["analysis_currency"]}),
            _json({"level": "medium", "items": ["样本代表性", "制造成本", "产品验证"]}),
            _json([{"item": r["validation_method"], "recommendation_id": r["id"]} for r in recommendations]),
            _json(sections), _json(state.get("partial_failures", [])), _json(state.get("version_bundle", {})), model_run_id,
        )
        report_id = await self.pool.fetchval("SELECT id FROM furniscope.analysis_reports WHERE report_uuid=$1::uuid", report_uuid)
        primary_aspects = await self.pool.fetch("SELECT evidence_id FROM furniscope.evidence_links WHERE analysis_job_id=$1 AND evidence_type='review_aspect' ORDER BY is_primary DESC,display_order LIMIT 10", state["task_id"])
        for order, evidence in enumerate(primary_aspects, 1):
            await self.pool.execute(
                """INSERT INTO furniscope.evidence_links
                   (tenant_id,analysis_job_id,claim_type,claim_id,claim_path,claim_category,evidence_type,evidence_id,is_primary,display_order)
                   VALUES($1,$2,'report',$3,'executive_summary','inference','review_aspect',$4,$5,$6) ON CONFLICT DO NOTHING""",
                state["tenant_id"], state["task_id"], report_id, evidence["evidence_id"], order == 1, order,
            )
        ref = {"report_uuid": str(report_uuid), "data_class": data_class}
        return CapabilityResult(output_ref=ref, state_update={"report_ref": ref})

    async def _final_persist(self, state: FurniScopeGraphState, _payload: dict[str, Any]) -> CapabilityResult:
        report_uuid = state.get("report_ref", {}).get("report_uuid")
        if not report_uuid:
            raise AuthorizedDataRequired("报告尚未持久化")
        return CapabilityResult(output_ref={"report_uuid": report_uuid}, state_update={"report_ref": state["report_ref"]})
