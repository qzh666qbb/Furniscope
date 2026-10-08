"""Decision-ready projections for the five market-insight capabilities."""

from __future__ import annotations

from datetime import datetime, timezone
import math
from statistics import median
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import BusinessError


def _number(value: Any) -> float | None:
    try:
        parsed = None if value in (None, "") else float(value)
        return parsed if parsed is None or math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def build_pricing_recommendation(
    prices: list[float],
    *,
    currency: str | None,
    unit_cost: float | None,
    target_margin: float | None,
    review_growth_sensitivity: float | None,
    unit_cost_basis: str | None = None,
    target_margin_basis: str | None = None,
    promo_margin_floor: float = 0.20,
    max_discount_rate: float = 0.08,
    promo_margin_basis: str = "policy_default",
    max_discount_basis: str = "policy_default",
) -> dict[str, Any]:
    """Combine observed market prices with explicit enterprise constraints."""
    clean = [float(value) for value in prices if _number(value) is not None and float(value) > 0]
    minimum_sample_size = 3
    if len(clean) < minimum_sample_size:
        return {
            "status": "insufficient_market_data",
            "currency": currency,
            "sample_size": len(clean),
            "minimum_sample_size": minimum_sample_size,
            "message": (
                f"有效竞品价格少于 {minimum_sample_size} 个，"
                "暂不生成可能失真的核心价格带与售价建议。"
            ),
        }
    if not currency:
        return {
            "status": "currency_conflict",
            "currency": None,
            "sample_size": len(clean),
            "message": "价格样本包含多个币种或缺少币种，已阻止跨币种混算。请先统一币种或按币种拆分数据集。",
        }

    q1 = _percentile(clean, 0.25)
    midpoint = _percentile(clean, 0.5)
    q3 = _percentile(clean, 0.75)
    margin = min(0.85, max(0.05, target_margin if target_margin is not None else 0.35))
    promo_margin = min(0.85, max(0, promo_margin_floor))
    discount_rate = min(0.80, max(0, max_discount_rate))
    cost_floor = unit_cost / (1 - margin) if unit_cost is not None and unit_cost > 0 else None
    recommended = midpoint or q1 or q3
    if cost_floor is not None:
        recommended = max(recommended, cost_floor)
    cost_basis = unit_cost_basis or (
        "confirmed_unit_cost" if unit_cost is not None else None
    )
    margin_basis = target_margin_basis or (
        "confirmed_target_margin" if unit_cost is not None else None
    )
    if cost_basis == "planning_assumption":
        status = "planning_anchor"
        message = "建议价基于当前市场价格与经营规划参数，执行前需确认实际单位成本。"
    elif q3 is not None and cost_floor is not None and cost_floor > q3:
        status = "cost_above_market"
        message = "目标毛利对应的价格底线高于市场核心价格带，需先优化成本或调整定位。"
    elif cost_floor is None:
        status = "market_anchor_only"
        message = "尚未配置单位成本，结果仅作为市场价格锚点，不能用于利润承诺。"
    else:
        status = "actionable"
        message = "建议价同时满足当前市场锚点与企业目标毛利约束。"

    market_floor = q1 or min(clean)
    discount_floor = recommended * (1 - discount_rate)
    promo_cost_floor = (
        unit_cost / (1 - promo_margin)
        if unit_cost is not None and unit_cost > 0 else None
    )
    promo_price = max(
        market_floor,
        discount_floor,
        promo_cost_floor if promo_cost_floor is not None else 0,
    )
    floor_components = {
        "market_p25": market_floor,
        "max_discount_floor": discount_floor,
        "promo_margin_floor": promo_cost_floor,
    }
    binding_code, binding_value = max(
        (
            (code, value)
            for code, value in floor_components.items()
            if value is not None
        ),
        key=lambda item: item[1],
    )
    binding_labels = {
        "market_p25": "市场 P25 价格",
        "max_discount_floor": "最大折扣率",
        "promo_margin_floor": "促销毛利底线",
    }

    def scenario(code: str, label: str, price: float) -> dict[str, Any]:
        gross_profit = price - unit_cost if unit_cost is not None else None
        gross_margin = gross_profit / price if gross_profit is not None and price > 0 else None
        relative_discount = (
            1 - price / recommended if recommended and price < recommended else 0
        )
        relative_change = price / recommended - 1 if recommended else 0
        return {
            "code": code,
            "label": label,
            "price": round(price, 2),
            "discount_from_regular": round(relative_discount, 4),
            "price_change_from_regular": round(relative_change, 4),
            "gross_profit_per_unit": (
                round(gross_profit, 2) if gross_profit is not None else None
            ),
            "gross_margin": round(gross_margin, 4) if gross_margin is not None else None,
            "meets_target_margin": (
                gross_margin >= margin if gross_margin is not None else None
            ),
            "meets_promo_margin": (
                gross_margin >= promo_margin if gross_margin is not None else None
            ),
        }

    regular_scenario = scenario("regular", "建议常规价", recommended)
    promotion_scenario = scenario("promotion", "最低促销价", promo_price)
    market_percentile = sum(value <= recommended for value in clean) / len(clean)
    if recommended <= market_floor:
        market_position = "低价带"
    elif midpoint is not None and recommended <= midpoint:
        market_position = "主流偏低"
    elif q3 is not None and recommended <= q3:
        market_position = "主流偏高"
    else:
        market_position = "高价带"
    currency_label = currency or ""
    if status == "planning_anchor":
        next_step = "先用实际完全成本替换当前规划成本，再确认上架和促销价格。"
    elif status == "market_anchor_only":
        next_step = "先补录单位完全成本，否则单位利润和毛利率不能用于经营承诺。"
    elif status == "cost_above_market":
        next_step = "目标毛利价已高于核心市场带，优先优化成本或明确高端定位。"
    else:
        next_step = "可进入小流量价格测试，并持续观察转化率、退款率和评论变化。"
    return {
        "status": status,
        "currency": currency,
        "calculation_version": "market-pricing-v3",
        "cost_basis": cost_basis,
        "target_margin_basis": margin_basis,
        "promo_margin_basis": promo_margin_basis,
        "max_discount_basis": max_discount_basis,
        "sample_size": len(clean),
        "minimum_sample_size": minimum_sample_size,
        "market_low": round(q1 or min(clean), 2),
        "market_median": round(midpoint or median(clean), 2),
        "market_high": round(q3 or max(clean), 2),
        "unit_cost": round(unit_cost, 2) if unit_cost is not None else None,
        "target_margin": round(margin, 4) if unit_cost is not None else None,
        "promo_margin_floor": round(promo_margin, 4),
        "max_discount_rate": round(discount_rate, 4),
        "price_floor": round(cost_floor, 2) if cost_floor is not None else None,
        "recommended_price": round(recommended, 2),
        "promo_floor": round(promo_price, 2),
        "promo_floor_components": {
            "market_p25": round(floor_components["market_p25"], 2),
            "max_discount_floor": round(floor_components["max_discount_floor"], 2),
            "promo_margin_floor": (
                round(floor_components["promo_margin_floor"], 2)
                if floor_components["promo_margin_floor"] is not None else None
            ),
        },
        "decision_support": {
            "market_position": market_position,
            "market_percentile": round(market_percentile, 4),
            "binding_constraint": {
                "code": binding_code,
                "label": binding_labels[binding_code],
                "floor": round(binding_value, 2),
            },
            "regular": regular_scenario,
            "promotion": promotion_scenario,
            "scenarios": [
                scenario("market_low", "市场低位", market_floor),
                regular_scenario,
                promotion_scenario,
                scenario("market_high", "市场高位", q3 or max(clean)),
            ],
            "recommended_action": {
                "headline": (
                    f"常规价以 {currency_label} {recommended:.2f} 验证，"
                    f"促销不得低于 {currency_label} {promo_price:.2f}。"
                ).strip(),
                "guardrail": (
                    f"当前促销底线由“{binding_labels[binding_code]}”约束决定。"
                ),
                "next_step": next_step,
            },
        },
        "provenance": {
            "market_prices": "authorized_market_listings",
            "unit_cost": cost_basis,
            "target_margin": margin_basis,
            "promo_margin_floor": promo_margin_basis,
            "max_discount_rate": max_discount_basis,
        },
        "review_growth_price_sensitivity": (
            round(review_growth_sensitivity, 4)
            if review_growth_sensitivity is not None else None
        ),
        "sensitivity_status": (
            "exploratory_proxy"
            if review_growth_sensitivity is not None else "insufficient_history"
        ),
        "sensitivity_method": "competitor-review-growth-price-proxy-v1",
        "confidence": round(min(0.9, 0.45 + min(len(clean), 30) / 100 + (0.2 if unit_cost else 0)), 2),
        "message": message,
    }


class MarketIntelligenceService:
    """Read existing evidence and expose one coherent business capability model."""

    async def simulate_pricing(
        self, session: AsyncSession, *, tenant_id: int, dataset_id: int,
        unit_cost: float | None, target_margin: float | None,
        promo_margin_floor: float, max_discount_rate: float,
        comparator_group: str | None,
    ) -> dict[str, Any]:
        dataset = await self._dataset(session, tenant_id=tenant_id, dataset_id=dataset_id)
        if dataset is None:
            raise BusinessError("DATASET_NOT_FOUND", "市场数据集不存在或不可访问", status_code=404)
        all_prices = await self._prices(
            session, tenant_id=tenant_id, dataset_id=dataset_id,
        )
        groups = self._pricing_group_catalog(all_prices)
        selected_group = self._select_pricing_group(
            groups, comparator_group=comparator_group,
        )
        prices = self._prices_for_group(all_prices, selected_group)
        stored = await self._constraints(session, tenant_id=tenant_id)
        result = build_pricing_recommendation(
            [row["sale_price"] for row in prices if row.get("sale_price") is not None],
            currency=self._single_currency(prices),
            unit_cost=unit_cost if unit_cost is not None else stored["unit_cost"],
            target_margin=target_margin if target_margin is not None else stored["target_margin"],
            review_growth_sensitivity=await self._review_growth_sensitivity(
                session, tenant_id=tenant_id
            ),
            unit_cost_basis=(
                "request_override" if unit_cost is not None else stored["unit_cost_basis"]
            ),
            target_margin_basis=(
                "request_override"
                if target_margin is not None
                else stored["target_margin_basis"]
            ),
            promo_margin_floor=promo_margin_floor,
            max_discount_rate=max_discount_rate,
            promo_margin_basis="request_override",
            max_discount_basis="request_override",
        )
        self._attach_pricing_scope(
            result,
            groups=groups,
            selected_group=selected_group,
            dataset_sample_size=len(all_prices),
        )
        result["history_coverage"] = await self._pricing_history_coverage(
            session, tenant_id=tenant_id, dataset_id=dataset_id,
        )
        return result

    async def overview(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        dataset_id: int | None = None,
        product_id: int | None = None,
    ) -> dict[str, Any]:
        dataset = await self._dataset(session, tenant_id=tenant_id, dataset_id=dataset_id)
        if dataset_id is not None and dataset is None:
            raise BusinessError("DATASET_NOT_FOUND", "市场数据集不存在或不可访问", status_code=404)

        task = await self._latest_task(
            session,
            tenant_id=tenant_id,
            dataset_id=dataset["dataset_id"] if dataset else None,
            product_id=product_id,
        )
        all_prices = await self._prices(
            session, tenant_id=tenant_id, dataset_id=dataset["dataset_id"] if dataset else None,
        )
        groups = self._pricing_group_catalog(all_prices)
        selected_group = self._select_pricing_group(groups, comparator_group=None)
        prices = self._prices_for_group(all_prices, selected_group)
        constraints = await self._constraints(session, tenant_id=tenant_id)
        sensitivity = await self._review_growth_sensitivity(
            session, tenant_id=tenant_id
        )
        history_coverage = await self._pricing_history_coverage(
            session,
            tenant_id=tenant_id,
            dataset_id=dataset["dataset_id"] if dataset else None,
        )
        pricing = build_pricing_recommendation(
            [row["sale_price"] for row in prices if row.get("sale_price") is not None],
            currency=self._single_currency(prices),
            unit_cost=constraints["unit_cost"],
            target_margin=constraints["target_margin"],
            review_growth_sensitivity=sensitivity,
            unit_cost_basis=constraints["unit_cost_basis"],
            target_margin_basis=constraints["target_margin_basis"],
            promo_margin_floor=constraints["promo_margin_floor"],
            max_discount_rate=constraints["max_discount_rate"],
            promo_margin_basis=constraints["promo_margin_basis"],
            max_discount_basis=constraints["max_discount_basis"],
        )
        self._attach_pricing_scope(
            pricing,
            groups=groups,
            selected_group=selected_group,
            dataset_sample_size=len(all_prices),
        )
        pricing["history_coverage"] = history_coverage

        opportunities = await self._opportunities(
            session,
            tenant_id=tenant_id,
            task_id=task["task_id"] if task else None,
            limit=5,
            offset=0,
        )
        opportunity_total = await self._opportunity_count(
            session, tenant_id=tenant_id, task_id=task["task_id"] if task else None,
        )
        reviews = await self._reviews(
            session,
            tenant_id=tenant_id,
            dataset_id=dataset["dataset_id"] if dataset else None,
            task_id=task["task_id"] if task else None,
        )
        competitors = await self._competitors(
            session, tenant_id=tenant_id, dataset_id=dataset["dataset_id"] if dataset else None,
        )
        compliance = await self._compliance(
            session,
            tenant_id=tenant_id,
            market_country=dataset["market_country"] if dataset else None,
            category_code=dataset["category_code"] if dataset else None,
        )
        gaps = []
        if dataset is None:
            gaps.append("尚无可用市场数据集")
        if task is None:
            gaps.append("当前范围尚无已完成 AI 分析，选品与评论聚类等待分析任务")
        if pricing.get("status") == "market_anchor_only":
            gaps.append("企业画像缺少单位成本，定价暂不含利润校验")
        if pricing.get("cost_basis") == "planning_assumption":
            gaps.append(
                f"利润空间与定价利润校验使用规划参数；实际单位成本 "
                f"{pricing.get('currency') or ''} {pricing.get('unit_cost'):.2f}、"
                f"目标毛利率 {pricing.get('target_margin', 0) * 100:.0f}% 待企业确认"
            )
        missing_factors = {
            factor
            for item in opportunities
            for factor in (item.get("weight_config") or {}).get("missing_factors", [])
        }
        if "demand_growth" in missing_factors:
            gaps.append("选品评分覆盖 3/5 个市场因子；需求增长因缺少连续市场时序未参与评分")
        if opportunities and all(item.get("enterprise_fit_score") is None for item in opportunities):
            gaps.append("企业制造能力画像尚未确认，企业适配分未参与机会排序")
        if pricing.get("review_growth_price_sensitivity") is None:
            gaps.append(
                f"竞品监控当前仅 {history_coverage['observation_times']} 个观测时点"
                f"（{history_coverage['price_points']} 条价格、"
                f"{history_coverage['review_count_points']} 条评论计数），"
                "暂不能计算评论增长代理敏感度"
            )
        if not compliance["source_count"]:
            gaps.append("尚未配置官方政策源")

        return {
            "generated_at": datetime.now(timezone.utc),
            "scope": {
                "dataset_id": dataset["dataset_id"] if dataset else None,
                "dataset_name": dataset["name"] if dataset else None,
                "market_country": dataset["market_country"] if dataset else None,
                "platform": dataset["platform"] if dataset else None,
                "product_id": product_id,
                "task_uuid": task["task_uuid"] if task else None,
                "data_class": "authorized_market_data",
            },
            "smart_selection": {
                "status": "ready" if opportunities else "awaiting_analysis",
                "items": opportunities,
                "total": opportunity_total,
                "preview_limit": 5,
                "method": "opportunity-score-v3-enterprise-gated",
            },
            "competitor_tracking": competitors,
            "review_mining": reviews,
            "pricing": pricing,
            "compliance": compliance,
            "data_gaps": gaps,
        }

    async def _dataset(
        self, session: AsyncSession, *, tenant_id: int, dataset_id: int | None,
    ) -> dict[str, Any] | None:
        row = await session.execute(text("""
            SELECT id dataset_id,name,market_country,platform,category_code,
                   listing_count,valid_review_count,updated_at
              FROM market_datasets
             WHERE tenant_id=:tenant AND status='ready' AND deleted_at IS NULL
               AND (CAST(:dataset AS bigint) IS NULL OR id=:dataset)
             ORDER BY CASE WHEN id=:dataset THEN 0 ELSE 1 END,updated_at DESC,id DESC
             LIMIT 1
        """), {"tenant": tenant_id, "dataset": dataset_id})
        item = row.mappings().one_or_none()
        return dict(item) if item else None

    async def list_opportunities(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        dataset_id: int,
        product_id: int | None,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        dataset = await self._dataset(
            session, tenant_id=tenant_id, dataset_id=dataset_id,
        )
        if dataset is None:
            raise BusinessError(
                "DATASET_NOT_FOUND",
                "市场数据集不存在或不可访问",
                status_code=404,
            )
        task = await self._latest_task(
            session,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            product_id=product_id,
        )
        task_id = task["task_id"] if task else None
        total = await self._opportunity_count(
            session, tenant_id=tenant_id, task_id=task_id,
        )
        items = await self._opportunities(
            session,
            tenant_id=tenant_id,
            task_id=task_id,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "has_next": page * page_size < total,
        }

    async def get_opportunity(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        opportunity_id: int,
    ) -> dict[str, Any]:
        row = await session.execute(text("""
            SELECT o.id opportunity_id,o.opportunity_code,o.title,o.description,
                   o.target_country,o.target_platform,o.target_user_codes,
                   o.usage_scenario_codes,o.primary_cluster_ids,o.status,
                   o.base_score::float8,
                   o.confidence::float8,o.recommendation_level,
                   o.demand_heat_score::float8,o.demand_growth_score::float8,
                   o.unmet_need_score::float8,o.competition_space_score::float8,
                   o.profit_space_score::float8,o.enterprise_fit_score::float8,
                   o.enterprise_fit_confidence::float8,o.manufacturing_fit,
                   o.market_score::float8,o.adjusted_score::float8,
                   o.policy_snapshot,o.weight_config,o.scoring_version,o.calculated_at,
                   t.task_uuid::text,t.dataset_id,
                   (SELECT jsonb_build_object(
                       'revision',f.revision,'status',f.status,'reason',f.reason
                    )
                      FROM opportunity_feedback_events f
                     WHERE f.opportunity_id=o.id AND f.tenant_id=o.tenant_id
                     ORDER BY revision DESC LIMIT 1) feedback
              FROM market_opportunities o
              JOIN analysis_tasks t
                ON t.id=o.analysis_job_id AND t.tenant_id=o.tenant_id
             WHERE o.tenant_id=:tenant AND o.id=:opportunity
        """), {"tenant": tenant_id, "opportunity": opportunity_id})
        item = row.mappings().one_or_none()
        if item is None:
            raise BusinessError(
                "OPPORTUNITY_NOT_FOUND",
                "选品机会不存在或不可访问",
                status_code=404,
            )
        return dict(item)

    async def list_competitor_alerts(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        dataset_id: int,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        dataset = await self._dataset(
            session, tenant_id=tenant_id, dataset_id=dataset_id,
        )
        if dataset is None:
            raise BusinessError(
                "DATASET_NOT_FOUND",
                "市场数据集不存在或不可访问",
                status_code=404,
            )
        params = {
            "tenant": tenant_id,
            "dataset": dataset_id,
            "limit": page_size,
            "offset": (page - 1) * page_size,
        }
        total = int(await session.scalar(text("""
            SELECT count(*)
              FROM competitor_change_alerts a
              JOIN competitor_watch_targets w
                ON w.id=a.watch_id AND w.tenant_id=a.tenant_id
             WHERE a.tenant_id=:tenant AND w.dataset_id=:dataset
        """), params) or 0)
        rows = await session.execute(text("""
            SELECT a.id alert_id,a.watch_id,w.dataset_id,a.asin,w.title,
                   a.change_type,
                   CASE a.change_type
                     WHEN 'price' THEN '价格变动'
                     WHEN 'title' THEN '标题更新'
                     WHEN 'image' THEN '主图更新'
                     WHEN 'bullets' THEN '五点描述更新'
                     WHEN 'promo' THEN '促销变动'
                     WHEN 'new_listing' THEN '监控目标上新'
                     ELSE a.change_type
                   END change_label,
                   CASE WHEN a.change_type IN ('new_listing','price')
                        THEN 'medium' ELSE 'low' END severity,
                   a.summary,a.is_read,a.captured_at detected_at
              FROM competitor_change_alerts a
              JOIN competitor_watch_targets w
                ON w.id=a.watch_id AND w.tenant_id=a.tenant_id
             WHERE a.tenant_id=:tenant AND w.dataset_id=:dataset
             ORDER BY a.captured_at DESC,a.id DESC
             LIMIT :limit OFFSET :offset
        """), params)
        return {
            "items": [dict(row) for row in rows.mappings().all()],
            "total": total,
            "page": page,
            "page_size": page_size,
            "has_next": page * page_size < total,
        }

    async def get_competitor_alert(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        alert_id: int,
    ) -> dict[str, Any]:
        row = await session.execute(text("""
            SELECT a.id alert_id,a.watch_id,w.dataset_id,a.asin,w.title,
                   w.platform,w.market_country,w.status watch_status,
                   a.change_type,
                   CASE a.change_type
                     WHEN 'price' THEN '价格变动'
                     WHEN 'title' THEN '标题更新'
                     WHEN 'image' THEN '主图更新'
                     WHEN 'bullets' THEN '五点描述更新'
                     WHEN 'promo' THEN '促销变动'
                     WHEN 'new_listing' THEN '监控目标上新'
                     ELSE a.change_type
                   END change_label,
                   CASE WHEN a.change_type IN ('new_listing','price')
                        THEN 'medium' ELSE 'low' END severity,
                   a.summary,a.before_value,a.after_value,a.is_read,
                   a.captured_at detected_at,
                   CASE WHEN s.id IS NULL THEN NULL ELSE jsonb_build_object(
                     'snapshot_id',s.id,
                     'captured_at',s.captured_at,
                     'sale_price',s.sale_price,
                     'list_price',s.list_price,
                     'currency',s.currency,
                     'rating',s.rating,
                     'review_count',s.review_count,
                     'promo_label',s.promo_label
                   ) END latest_snapshot
              FROM competitor_change_alerts a
              JOIN competitor_watch_targets w
                ON w.id=a.watch_id AND w.tenant_id=a.tenant_id
              LEFT JOIN LATERAL (
                SELECT id,captured_at,sale_price,list_price,currency,rating,
                       review_count,promo_label
                  FROM competitor_listing_snapshots
                 WHERE tenant_id=a.tenant_id AND watch_id=a.watch_id
                 ORDER BY captured_at DESC,id DESC LIMIT 1
              ) s ON TRUE
             WHERE a.tenant_id=:tenant AND a.id=:alert
        """), {"tenant": tenant_id, "alert": alert_id})
        item = row.mappings().one_or_none()
        if item is None:
            raise BusinessError(
                "COMPETITOR_ALERT_NOT_FOUND",
                "竞品动态不存在或不可访问",
                status_code=404,
            )
        return dict(item)

    async def list_review_clusters(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        dataset_id: int,
        product_id: int | None,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        dataset = await self._dataset(
            session, tenant_id=tenant_id, dataset_id=dataset_id,
        )
        if dataset is None:
            raise BusinessError(
                "DATASET_NOT_FOUND",
                "市场数据集不存在或不可访问",
                status_code=404,
            )
        task = await self._latest_task(
            session,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            product_id=product_id,
        )
        task_id = task["task_id"] if task else None
        total = await self._review_cluster_count(
            session, tenant_id=tenant_id, task_id=task_id,
        )
        items = await self._review_clusters(
            session,
            tenant_id=tenant_id,
            task_id=task_id,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "has_next": page * page_size < total,
        }

    async def get_review_cluster(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        cluster_id: int,
    ) -> dict[str, Any]:
        row = await session.execute(text("""
            SELECT c.id cluster_id,c.cluster_code,c.taxonomy_code,c.name,c.summary,
                   c.sentiment_distribution,c.aspect_count,c.review_count,
                   c.listing_count,c.mention_rate::float8,
                   c.importance_score::float8,c.cluster_confidence::float8,
                   c.representative_aspect_ids,c.created_at,
                   t.task_uuid::text,t.dataset_id
              FROM insight_clusters c
              JOIN analysis_tasks t
                ON t.id=c.analysis_job_id AND t.tenant_id=c.tenant_id
             WHERE c.tenant_id=:tenant AND c.id=:cluster
        """), {"tenant": tenant_id, "cluster": cluster_id})
        item = row.mappings().one_or_none()
        if item is None:
            raise BusinessError(
                "REVIEW_CLUSTER_NOT_FOUND",
                "评论主题不存在或不可访问",
                status_code=404,
            )
        evidence_rows = await session.execute(text("""
            SELECT a.id aspect_id,r.id review_id,l.id listing_id,
                   l.platform_listing_id,l.title listing_title,l.brand,
                   r.rating::float8,r.title_original,r.language_code,r.reviewed_at,
                   r.verified_purchase,a.taxonomy_code,a.sentiment,a.severity,
                   a.evidence_quote,a.extraction_confidence::float8,
                   cm.similarity_score::float8,cm.is_representative
              FROM cluster_members cm
              JOIN review_aspects a ON a.id=cm.review_aspect_id
              JOIN reviews r
                ON r.id=a.review_id AND r.tenant_id=a.tenant_id
              JOIN market_listings l
                ON l.id=r.listing_id AND l.tenant_id=r.tenant_id
             WHERE cm.cluster_id=:cluster AND a.tenant_id=:tenant
             ORDER BY cm.is_representative DESC,cm.similarity_score DESC,a.id
             LIMIT 30
        """), {"tenant": tenant_id, "cluster": cluster_id})
        detail = dict(item)
        detail["evidence"] = [
            dict(evidence) for evidence in evidence_rows.mappings().all()
        ]
        return detail

    async def _latest_task(
        self, session: AsyncSession, *, tenant_id: int, dataset_id: int | None,
        product_id: int | None,
    ) -> dict[str, Any] | None:
        row = await session.execute(text("""
            SELECT id task_id,task_uuid::text,product_id,dataset_id,completed_at
              FROM analysis_tasks
             WHERE tenant_id=:tenant AND status IN ('succeeded','partial_succeeded')
               AND (CAST(:dataset AS bigint) IS NULL OR dataset_id=:dataset)
               AND (CAST(:product AS bigint) IS NULL OR product_id=:product)
             ORDER BY
               CASE
                 WHEN CAST(:product AS bigint) IS NULL
                  AND analysis_config->>'source'='market_decision_baseline'
                 THEN 0 ELSE 1
               END,
               completed_at DESC NULLS LAST,id DESC
             LIMIT 1
        """), {"tenant": tenant_id, "dataset": dataset_id, "product": product_id})
        item = row.mappings().one_or_none()
        return dict(item) if item else None

    async def _opportunities(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        task_id: int | None,
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        if task_id is None:
            return []
        rows = await session.execute(text("""
            SELECT opportunity_id,opportunity_code,title,description,target_country,
                   target_platform,target_user_codes,usage_scenario_codes,
                   primary_cluster_ids,status,
                   base_score,confidence,recommendation_level,demand_heat_score,
                   demand_growth_score,unmet_need_score,competition_space_score,
                   profit_space_score,enterprise_fit_score,enterprise_fit_confidence,
                   manufacturing_fit,market_score,adjusted_score,policy_snapshot,
                   weight_config,scoring_version,calculated_at,task_uuid,dataset_id,feedback
              FROM (
                SELECT id opportunity_id,opportunity_code,title,description,
                       target_country,target_platform,target_user_codes,
                       usage_scenario_codes,primary_cluster_ids,status,
                       base_score::float8,confidence::float8,recommendation_level,
                       demand_heat_score::float8,demand_growth_score::float8,
                       unmet_need_score::float8,competition_space_score::float8,
                       profit_space_score::float8,enterprise_fit_score::float8,
                       enterprise_fit_confidence::float8,manufacturing_fit,
                       market_score::float8,adjusted_score::float8,policy_snapshot,weight_config,
                       scoring_version,calculated_at,
                       (SELECT t.task_uuid::text FROM analysis_tasks t
                         WHERE t.id=o.analysis_job_id AND t.tenant_id=o.tenant_id) task_uuid,
                       (SELECT t.dataset_id FROM analysis_tasks t
                         WHERE t.id=o.analysis_job_id AND t.tenant_id=o.tenant_id) dataset_id,
                       (SELECT jsonb_build_object('revision',f.revision,'status',f.status,'reason',f.reason)
                          FROM opportunity_feedback_events f WHERE f.opportunity_id=o.id AND f.tenant_id=o.tenant_id
                         ORDER BY revision DESC LIMIT 1) feedback
                  FROM market_opportunities o
                 WHERE o.tenant_id=:tenant AND o.analysis_job_id=:task
              ) ranked
             ORDER BY CASE recommendation_level WHEN 'prioritize_validate' THEN 0
                       WHEN 'collect_more_data' THEN 1 WHEN 'limited_opportunity' THEN 2 ELSE 3 END,
                       base_score DESC,opportunity_id
             LIMIT :limit OFFSET :offset
        """), {
            "tenant": tenant_id,
            "task": task_id,
            "limit": limit,
            "offset": offset,
        })
        return [dict(row) for row in rows.mappings().all()]

    async def _opportunity_count(
        self, session: AsyncSession, *, tenant_id: int, task_id: int | None,
    ) -> int:
        if task_id is None:
            return 0
        return int(await session.scalar(text("""
            SELECT count(*)
              FROM market_opportunities
             WHERE tenant_id=:tenant AND analysis_job_id=:task
        """), {"tenant": tenant_id, "task": task_id}) or 0)

    @staticmethod
    def _pricing_group_catalog(prices: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        for row in prices:
            code = str(row.get("comparator_group") or "").strip()
            if not code:
                continue
            entry = grouped.setdefault(
                code,
                {
                    "group_code": code,
                    "group_label": row.get("comparator_group_label") or code,
                    "prices": [],
                    "currencies": set(),
                },
            )
            value = _number(row.get("sale_price"))
            if value is not None and value > 0:
                entry["prices"].append(value)
            if row.get("currency"):
                entry["currencies"].add(str(row["currency"]))

        result = []
        for entry in grouped.values():
            values = entry["prices"]
            if len(values) < 3:
                continue
            currencies = entry["currencies"]
            result.append({
                "group_code": entry["group_code"],
                "group_label": entry["group_label"],
                "sample_size": len(values),
                "currency": next(iter(currencies)) if len(currencies) == 1 else None,
                "market_low": round(_percentile(values, 0.25) or min(values), 2),
                "market_median": round(_percentile(values, 0.5) or median(values), 2),
                "market_high": round(_percentile(values, 0.75) or max(values), 2),
            })
        return sorted(
            result,
            key=lambda item: (-item["sample_size"], item["group_label"]),
        )

    @staticmethod
    def _select_pricing_group(
        groups: list[dict[str, Any]],
        *,
        comparator_group: str | None,
    ) -> dict[str, Any] | None:
        if not groups:
            return None
        if comparator_group:
            selected = next(
                (item for item in groups if item["group_code"] == comparator_group),
                None,
            )
            if selected is None:
                raise BusinessError(
                    "PRICING_COMPARATOR_GROUP_NOT_FOUND",
                    "可比竞品组不存在、样本少于 3 个或不属于当前数据集",
                    status_code=422,
                )
            return selected
        return groups[0]

    @staticmethod
    def _prices_for_group(
        prices: list[dict[str, Any]],
        selected_group: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        if selected_group is None:
            return prices
        code = selected_group["group_code"]
        return [row for row in prices if row.get("comparator_group") == code]

    @staticmethod
    def _attach_pricing_scope(
        result: dict[str, Any],
        *,
        groups: list[dict[str, Any]],
        selected_group: dict[str, Any] | None,
        dataset_sample_size: int,
    ) -> None:
        result["comparator_groups"] = groups
        result["dataset_sample_size"] = dataset_sample_size
        result["comparator_group"] = (
            selected_group["group_code"] if selected_group else None
        )
        result["comparator_group_label"] = (
            selected_group["group_label"] if selected_group else "全部有效价格"
        )
        result["scope_message"] = (
            f"当前按“{result['comparator_group_label']}”可比竞品组计算，"
            f"使用 {result.get('sample_size', 0)} / {dataset_sample_size} 个有效价格。"
            if selected_group else
            f"当前数据缺少可用的结构化竞品分组，使用全部 {dataset_sample_size} 个有效价格。"
        )

    async def _prices(
        self, session: AsyncSession, *, tenant_id: int, dataset_id: int | None,
    ) -> list[dict[str, Any]]:
        if dataset_id is None:
            return []
        rows = await session.execute(text("""
            SELECT sale_price::float8,currency,
                   NULLIF(normalized_attributes->'category'->>'value','')
                     AS comparator_group,
                   CASE
                     WHEN NULLIF(normalized_attributes->'reference_sku'->>'value','') IS NOT NULL
                     THEN trim(replace(
                       title,
                       normalized_attributes->'reference_sku'->>'value',
                       ''
                     ))
                     ELSE COALESCE(
                       NULLIF(normalized_attributes->'official_category_en'->>'value',''),
                       category_raw,
                       category_code
                     )
                   END AS comparator_group_label
              FROM market_listings
             WHERE tenant_id=:tenant AND dataset_id=:dataset
               AND sale_price IS NOT NULL AND sale_price>0
             ORDER BY sale_price
        """), {"tenant": tenant_id, "dataset": dataset_id})
        return [dict(row) for row in rows.mappings().all()]

    async def _constraints(self, session: AsyncSession, *, tenant_id: int) -> dict[str, Any]:
        raw = await session.scalar(text("""
            SELECT constraints FROM enterprise_profiles WHERE tenant_id=:tenant
        """), {"tenant": tenant_id})
        unit_cost = None
        target_margin = None
        promo_margin_floor = 0.20
        max_discount_rate = 0.08
        unit_cost_basis = None
        target_margin_basis = None
        promo_margin_basis = "policy_default"
        max_discount_basis = "policy_default"
        for item in raw or []:
            code = str(item.get("constraint_type") or "").lower()
            raw_value = item.get("value")
            if isinstance(raw_value, dict):
                value = _number(raw_value.get("max") or raw_value.get("min"))
            else:
                value = _number(raw_value)
            if value is None:
                continue
            if code == "unit_cost":
                unit_cost = value
                unit_cost_basis = (
                    "planning_assumption"
                    if item.get("source_class") == "planning_assumption"
                    else "confirmed_unit_cost"
                )
            elif code in {"target_margin", "gross_margin"}:
                target_margin = value / 100 if value > 1 else value
                target_margin_basis = (
                    "planning_assumption"
                    if item.get("source_class") == "planning_assumption"
                    else "confirmed_target_margin"
                )
            elif code == "promo_margin_floor":
                promo_margin_floor = value / 100 if value > 1 else value
                promo_margin_basis = (
                    "planning_assumption"
                    if item.get("source_class") == "planning_assumption"
                    else "confirmed_policy"
                )
            elif code == "max_discount_rate":
                max_discount_rate = value / 100 if value > 1 else value
                max_discount_basis = (
                    "planning_assumption"
                    if item.get("source_class") == "planning_assumption"
                    else "confirmed_policy"
                )
        return {
            "unit_cost": unit_cost,
            "target_margin": target_margin,
            "promo_margin_floor": min(0.85, max(0, promo_margin_floor)),
            "max_discount_rate": min(0.80, max(0, max_discount_rate)),
            "unit_cost_basis": unit_cost_basis,
            "target_margin_basis": target_margin_basis,
            "promo_margin_basis": promo_margin_basis,
            "max_discount_basis": max_discount_basis,
        }

    async def _review_growth_sensitivity(
        self, session: AsyncSession, *, tenant_id: int,
    ) -> float | None:
        rows = await session.execute(text("""
            WITH deltas AS (
              SELECT watch_id,sale_price::float8 price,captured_at,id,
                     review_count::float8
                       - lag(review_count::float8) OVER(
                           PARTITION BY watch_id ORDER BY captured_at,id
                         ) review_growth
                FROM competitor_listing_snapshots
               WHERE tenant_id=:tenant AND sale_price>0 AND review_count>=0
            ), points AS (
              SELECT watch_id,price,review_growth,
                     lag(price) OVER(PARTITION BY watch_id ORDER BY captured_at,id) prev_price,
                     lag(review_growth) OVER(
                       PARTITION BY watch_id ORDER BY captured_at,id
                     ) prev_review_growth
                FROM deltas
            )
            SELECT ln(review_growth/prev_review_growth)
                     / NULLIF(ln(price/prev_price),0) sensitivity
              FROM points
             WHERE prev_price>0 AND review_growth>0 AND prev_review_growth>0
               AND price<>prev_price AND review_growth<>prev_review_growth
             LIMIT 100
        """), {"tenant": tenant_id})
        values = [
            float(row["sensitivity"]) for row in rows.mappings().all()
            if row["sensitivity"] is not None
            and math.isfinite(float(row["sensitivity"]))
        ]
        return median(values) if len(values) >= 3 else None

    async def _pricing_history_coverage(
        self, session: AsyncSession, *, tenant_id: int, dataset_id: int | None,
    ) -> dict[str, Any]:
        if dataset_id is None:
            return {
                "tracked_targets": 0,
                "snapshot_count": 0,
                "observation_times": 0,
                "price_points": 0,
                "review_count_points": 0,
                "first_observed_at": None,
                "last_observed_at": None,
            }
        row = await session.execute(text("""
            SELECT count(DISTINCT watch_id)::int tracked_targets,
                   count(*)::int snapshot_count,
                   count(DISTINCT captured_at)::int observation_times,
                   count(*) FILTER(WHERE sale_price IS NOT NULL AND sale_price>0)::int price_points,
                   count(*) FILTER(WHERE review_count IS NOT NULL AND review_count>=0)::int
                     review_count_points,
                   min(captured_at) first_observed_at,
                   max(captured_at) last_observed_at
              FROM competitor_listing_snapshots
             WHERE tenant_id=:tenant AND dataset_id=:dataset AND watch_id IS NOT NULL
        """), {"tenant": tenant_id, "dataset": dataset_id})
        return dict(row.mappings().one())

    async def _competitors(
        self, session: AsyncSession, *, tenant_id: int, dataset_id: int | None,
    ) -> dict[str, Any]:
        summary = await session.execute(text("""
            SELECT count(*)::int watch_count,
                   count(*) FILTER(WHERE status='active')::int active_watch_count,
                   count(*) FILTER(WHERE EXISTS(
                     SELECT 1 FROM competitor_listing_snapshots s
                      WHERE s.tenant_id=competitor_watch_targets.tenant_id
                        AND s.watch_id=competitor_watch_targets.id
                        AND s.sale_price IS NOT NULL
                   ))::int priced_watch_count
              FROM competitor_watch_targets
             WHERE tenant_id=:tenant
               AND (CAST(:dataset AS bigint) IS NULL OR dataset_id=:dataset)
        """), {"tenant": tenant_id, "dataset": dataset_id})
        counts = dict(summary.mappings().one())
        alert_total = int(await session.scalar(text("""
            SELECT count(*)
              FROM competitor_change_alerts a
              JOIN competitor_watch_targets w
                ON w.id=a.watch_id AND w.tenant_id=a.tenant_id
             WHERE a.tenant_id=:tenant
               AND (CAST(:dataset AS bigint) IS NULL OR w.dataset_id=:dataset)
        """), {"tenant": tenant_id, "dataset": dataset_id}) or 0)
        alerts = await session.execute(text("""
            SELECT a.id alert_id,a.change_type,a.summary,
                   CASE WHEN a.change_type IN ('new_listing','price')
                        THEN 'medium' ELSE 'low' END severity,
                   a.is_read,a.captured_at detected_at
              FROM competitor_change_alerts a
              JOIN competitor_watch_targets w
                ON w.id=a.watch_id AND w.tenant_id=a.tenant_id
             WHERE a.tenant_id=:tenant
               AND (CAST(:dataset AS bigint) IS NULL OR w.dataset_id=:dataset)
             ORDER BY a.captured_at DESC,a.id DESC LIMIT 8
        """), {"tenant": tenant_id, "dataset": dataset_id})
        listing_count = 0
        if dataset_id is not None:
            listing_count = int(await session.scalar(text("""
                SELECT count(*) FROM market_listings
                 WHERE tenant_id=:tenant AND dataset_id=:dataset
            """), {"tenant": tenant_id, "dataset": dataset_id}) or 0)
        return {
            "status": "ready" if counts["watch_count"] or listing_count else "awaiting_data",
            **counts,
            "dataset_listing_count": listing_count,
            "recent_alerts": [dict(row) for row in alerts.mappings().all()],
            "alert_total": alert_total,
            "preview_limit": 8,
        }

    async def _reviews(
        self, session: AsyncSession, *, tenant_id: int, dataset_id: int | None,
        task_id: int | None,
    ) -> dict[str, Any]:
        counts = {"total": 0, "positive": 0, "neutral": 0, "negative": 0}
        if dataset_id is not None:
            row = await session.execute(text("""
                SELECT count(*)::int total,
                       count(*) FILTER(WHERE sentiment='positive')::int positive,
                       count(*) FILTER(WHERE sentiment='neutral')::int neutral,
                       count(*) FILTER(WHERE sentiment='negative')::int negative
                  FROM reviews
                 WHERE tenant_id=:tenant AND dataset_id=:dataset AND is_valid
            """), {"tenant": tenant_id, "dataset": dataset_id})
            counts = dict(row.mappings().one())
        cluster_total = await self._review_cluster_count(
            session, tenant_id=tenant_id, task_id=task_id,
        )
        clusters = await self._review_clusters(
            session,
            tenant_id=tenant_id,
            task_id=task_id,
            limit=12,
            offset=0,
        )
        return {
            "status": "ready" if clusters else "awaiting_analysis",
            **counts,
            "clusters": clusters,
            "cluster_total": cluster_total,
            "preview_limit": 12,
            "method": "evidence-span-aspect-clustering-v2",
        }

    async def _review_clusters(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        task_id: int | None,
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        if task_id is None:
            return []
        rows = await session.execute(text("""
            SELECT id cluster_id,cluster_code,taxonomy_code,name,summary,
                   sentiment_distribution,aspect_count,review_count,listing_count,
                   mention_rate::float8,importance_score::float8,
                   cluster_confidence::float8,representative_aspect_ids,created_at
              FROM insight_clusters
             WHERE tenant_id=:tenant AND analysis_job_id=:task
             ORDER BY importance_score DESC,id
             LIMIT :limit OFFSET :offset
        """), {
            "tenant": tenant_id,
            "task": task_id,
            "limit": limit,
            "offset": offset,
        })
        return [dict(row) for row in rows.mappings().all()]

    async def _review_cluster_count(
        self, session: AsyncSession, *, tenant_id: int, task_id: int | None,
    ) -> int:
        if task_id is None:
            return 0
        return int(await session.scalar(text("""
            SELECT count(*)
              FROM insight_clusters
             WHERE tenant_id=:tenant AND analysis_job_id=:task
        """), {"tenant": tenant_id, "task": task_id}) or 0)

    async def _compliance(
        self, session: AsyncSession, *, tenant_id: int,
        market_country: str | None, category_code: str | None,
    ) -> dict[str, Any]:
        source_count = int(await session.scalar(text("""
            SELECT count(*) FROM policy_sources
             WHERE tenant_id=:tenant AND enabled
               AND (CAST(:country AS text) IS NULL OR market_country IS NULL
                    OR market_country=:country)
               AND (CAST(:category AS text) IS NULL OR category_code IS NULL
                    OR category_code=:category)
        """), {
            "tenant": tenant_id, "country": market_country,
            "category": category_code,
        }) or 0)
        rows = await session.execute(text("""
            SELECT a.id alert_id,a.title,a.summary,a.url,a.published_at,a.severity,
                   a.matched_keywords,a.is_read,s.name source_name,
                   s.market_country,s.category_code
              FROM policy_alerts a
              JOIN policy_sources s ON s.id=a.source_id AND s.tenant_id=a.tenant_id
             WHERE a.tenant_id=:tenant
               AND s.enabled
               AND (CAST(:country AS text) IS NULL OR s.market_country IS NULL
                    OR s.market_country=:country)
               AND (CAST(:category AS text) IS NULL OR s.category_code IS NULL
                    OR s.category_code=:category)
             ORDER BY CASE a.severity WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                      a.published_at DESC NULLS LAST,a.id DESC LIMIT 20
        """), {
            "tenant": tenant_id, "country": market_country,
            "category": category_code,
        })
        alerts = [dict(row) for row in rows.mappings().all()]
        return {
            "status": "ready" if source_count else "source_required",
            "source_count": source_count,
            "unread_count": sum(1 for item in alerts if not item["is_read"]),
            "high_risk_count": sum(1 for item in alerts if item["severity"] == "high"),
            "alerts": alerts,
            "method": "official-source-keyword-risk-v1",
            "scope_type": "dataset_matched" if market_country or category_code else "tenant",
            "matched_scope": {
                "market_country": market_country,
                "category_code": category_code,
            },
        }

    @staticmethod
    def _single_currency(prices: list[dict[str, Any]]) -> str | None:
        currencies = {row["currency"] for row in prices if row.get("currency")}
        return next(iter(currencies)) if len(currencies) == 1 else None
