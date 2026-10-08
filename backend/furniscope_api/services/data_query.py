"""Deterministic semantic queries over immutable tenant sales projections."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, timedelta
from decimal import Decimal
from time import perf_counter
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import ApiSettings
from ..errors import BusinessError
from ..monitoring import DATA_QUERY_DURATION, DATA_QUERY_TOTAL
from ..schemas.data_query import DataQueryPlan
from .forecast_data_service import ForecastDataService
from .workbench_chat import parse_market

_SALES_METRICS = {
    "sales_units",
    "average_daily_sales",
    "sales_revenue",
    "average_selling_price",
    "active_days",
    "sku_count",
    "site_count",
}
_CURRENCY_METRICS = {"sales_revenue", "average_selling_price"}
_QUERY_SIGNAL = re.compile(
    r"销量|销售量|售出|卖了|销售额|营收|成交额|日均|平均日销|"
    r"库存|存量|缺货|断货|SKU\s*数|多少个\s*SKU|站点数",
    re.I,
)
_FUTURE_SIGNAL = re.compile(r"预测|未来|下周|下月|明年|forecast", re.I)

_METRIC_LABELS = {
    "sales_units": ("销量", "件"),
    "average_daily_sales": ("日均销量", "件/日"),
    "sales_revenue": ("可计算销售额", None),
    "average_selling_price": ("加权成交价", None),
    "active_days": ("销售天数", "天"),
    "sku_count": ("SKU 数", "个"),
    "site_count": ("站点数", "个"),
    "inventory_units": ("期末库存", "件"),
    "average_inventory": ("平均库存", "件"),
    "stockout_days": ("缺货天数", "天"),
}

_METRIC_SQL = {
    "sales_units": "round(sum(sales_units)::numeric,4)",
    "average_daily_sales": (
        "round((sum(sales_units)/nullif(count(distinct fact_date),0))::numeric,4)"
    ),
    "sales_revenue": (
        "round(sum(sales_units*unit_price) "
        "filter(where unit_price is not null)::numeric,2)"
    ),
    "average_selling_price": (
        "round((sum(sales_units*unit_price) filter(where unit_price is not null)"
        "/nullif(sum(sales_units) filter(where unit_price is not null),0))::numeric,2)"
    ),
    "active_days": "count(distinct fact_date)::bigint",
    "sku_count": "count(distinct sku)::bigint",
    "site_count": "count(distinct site)::bigint",
    "inventory_units": "(array_agg(inventory_units order by fact_date desc))[1]",
    "average_inventory": "round(avg(inventory_units)::numeric,4)",
    "stockout_days": (
        "count(distinct fact_date) filter(where inventory_units=0)::bigint"
    ),
}


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def is_data_query_request(question: str) -> bool:
    return bool(_QUERY_SIGNAL.search(question or "")) and not bool(
        _FUTURE_SIGNAL.search(question or "")
    )


def data_query_source_kind(plan: DataQueryPlan) -> str:
    return "sales" if all(metric in _SALES_METRICS for metric in plan.metrics) else "inventory"


def build_data_query_plan(
    question: str,
    *,
    current_sku: str | None = None,
    current_market: str | None = None,
) -> DataQueryPlan | None:
    """Map natural-language metric phrases into a closed QueryPlan vocabulary."""
    if not is_data_query_request(question):
        return None
    value = question or ""
    metrics: list[str] = []
    if re.search(r"销售额|营收|成交额", value):
        metrics.append("sales_revenue")
    if re.search(r"成交价|客单价|平均售价|均价", value):
        metrics.append("average_selling_price")
    if re.search(r"日均|平均日销", value):
        metrics.append("average_daily_sales")
    elif re.search(r"销量|销售量|售出|卖了", value):
        metrics.append("sales_units")
    if re.search(r"销售天数|有销量的天数", value):
        metrics.append("active_days")
    if re.search(r"SKU\s*数|多少个\s*SKU", value, re.I):
        metrics.append("sku_count")
    if re.search(r"站点数|多少个站点", value):
        metrics.append("site_count")
    if re.search(r"缺货|断货", value):
        metrics.append("stockout_days")
    if re.search(r"平均库存|日均库存", value):
        metrics.append("average_inventory")
    elif re.search(r"库存|存量", value) and "stockout_days" not in metrics:
        metrics.append("inventory_units")
    if not metrics:
        metrics = ["sales_units"]

    group_by: list[str] = []
    if re.search(r"按\s*SKU|每个\s*SKU|各\s*SKU|分\s*SKU", value, re.I):
        group_by.append("sku")
    if re.search(r"按站点|每个站点|各站点|分站点", value):
        group_by.append("site")
    grain = "total"
    if re.search(r"按日|每天|每日|逐日", value):
        grain = "daily"
    elif re.search(r"按周|每周|逐周|周趋势", value):
        grain = "weekly"
    elif re.search(r"按月|每月|逐月|月趋势|趋势", value):
        grain = "monthly"

    relative_days = None
    relative = re.search(r"(?:最近|近)\s*(\d{1,4})\s*天", value)
    if relative:
        relative_days = min(3660, max(1, int(relative.group(1))))
    date_from = date_to = None
    month = re.search(r"(20\d{2})\s*年\s*(1[0-2]|\d)\s*月", value)
    year = re.search(r"(20\d{2})\s*年", value)
    if month:
        selected_year, selected_month = int(month.group(1)), int(month.group(2))
        date_from = date(selected_year, selected_month, 1)
        if selected_month == 12:
            date_to = date(selected_year, 12, 31)
        else:
            date_to = date(selected_year, selected_month + 1, 1) - timedelta(days=1)
    elif year:
        selected_year = int(year.group(1))
        date_from, date_to = date(selected_year, 1, 1), date(selected_year, 12, 31)
    explicit_dates = re.findall(r"20\d{2}-\d{2}-\d{2}", value)
    if explicit_dates:
        parsed = [date.fromisoformat(item) for item in explicit_dates[:2]]
        date_from = parsed[0]
        date_to = parsed[-1]
        relative_days = None

    all_skus = bool(re.search(r"所有\s*SKU|全部\s*SKU|各\s*SKU|全品", value, re.I))
    skus = [current_sku] if current_sku and not all_skus else []
    market_code, _ = parse_market(value)
    sites = [market_code or current_market] if market_code or current_market else []
    return DataQueryPlan.model_validate({
        "metrics": metrics,
        "grain": grain,
        "group_by": group_by,
        "filters": {
            "date_from": date_from,
            "date_to": date_to,
            "relative_days": relative_days,
            "skus": skus,
            "sites": sites,
        },
        "limit": 100,
    })


class DataQueryService:
    def __init__(self, settings: ApiSettings) -> None:
        self.settings = settings
        self.data = ForecastDataService(settings)

    async def metric_catalog(self, session: AsyncSession) -> list[dict[str, Any]]:
        rows = (await session.execute(text("""
            SELECT metric_code,display_name,description,source_kind,value_type,unit
              FROM data_metric_catalog
             WHERE is_active ORDER BY source_kind,metric_code
        """))).mappings().all()
        return [dict(row) for row in rows]

    async def project_version(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        version_uuid: str,
    ) -> dict[str, Any]:
        row = await self.data.row(session, tenant_id, version_uuid)
        if row["status"] != "confirmed":
            raise BusinessError(
                "DATA_NOT_CONFIRMED",
                "只有已确认的数据版本可以进入问数事实层",
                status_code=409,
            )
        payload = json.loads(self.data.read_verified(
            tenant_id,
            row["canonical_storage_key"],
            row["canonical_sha256"],
        ))
        source_kind = str((row.get("rules") or {}).get("kind") or "")
        if source_kind not in {"sales", "inventory"}:
            raise BusinessError(
                "DATA_QUERY_SOURCE_UNSUPPORTED",
                "数据版本缺少明确的销量或库存口径",
                status_code=422,
            )
        records = payload.get("records")
        if not isinstance(records, list) or not records:
            raise BusinessError(
                "DATA_QUERY_SOURCE_EMPTY",
                "数据版本没有可查询的标准记录",
                status_code=422,
            )
        if len(records) > 100_000:
            raise BusinessError(
                "DATA_QUERY_SOURCE_TOO_LARGE",
                "单个数据版本超过事实投影上限，请分版本接入",
                status_code=422,
            )

        projection_rows: list[dict[str, Any]] = []
        for item in records:
            selected = {
                "date": item.get("date"),
                "sku": str(item.get("sku") or "").strip(),
                "site": str(item.get("site") or "").strip().upper(),
                "value": item.get(source_kind),
                "status": item.get("status"),
                "unit_price": item.get("unit_price"),
                "discount": item.get("discount"),
                "currency": item.get("currency"),
            }
            selected["record_sha256"] = _sha256(selected)
            projection_rows.append(selected)
        projection_sha256 = _sha256({
            "source_kind": source_kind,
            "canonical_sha256": row["canonical_sha256"],
            "records": projection_rows,
        })
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": f"data-fact-projection:{tenant_id}:{row['id']}"},
        )
        existing = (await session.execute(text("""
            SELECT source_kind,canonical_sha256,projection_sha256,
                   sales_row_count,inventory_row_count,projected_at
              FROM data_fact_projections
             WHERE tenant_id=:tenant AND data_version_id=:version_id
        """), {
            "tenant": tenant_id,
            "version_id": row["id"],
        })).mappings().one_or_none()
        if existing:
            if (
                existing["canonical_sha256"] != row["canonical_sha256"]
                or existing["projection_sha256"] != projection_sha256
                or existing["source_kind"] != source_kind
            ):
                raise BusinessError(
                    "DATA_FACT_PROJECTION_CONFLICT",
                    "事实投影与不可变数据版本不一致，请停止查询并执行数据审计",
                    status_code=409,
                )
            fact_table = (
                "sales_facts_daily" if source_kind == "sales"
                else "inventory_facts_daily"
            )
            expected_count = (
                int(existing["sales_row_count"]) if source_kind == "sales"
                else int(existing["inventory_row_count"])
            )
            actual_count = int(await session.scalar(text(f"""
                SELECT count(*) FROM {fact_table}
                 WHERE tenant_id=:tenant AND data_version_id=:version_id
            """), {
                "tenant": tenant_id,
                "version_id": row["id"],
            }) or 0)
            if actual_count != expected_count:
                raise BusinessError(
                    "DATA_FACT_PROJECTION_INCOMPLETE",
                    "事实投影行数与投影清单不一致，请停止查询并执行数据审计",
                    status_code=409,
                )
            return {**dict(existing), "version_uuid": version_uuid}

        common = {
            "tenant_id": tenant_id,
            "version_id": int(row["id"]),
        }
        if source_kind == "sales":
            await session.execute(text("""
                INSERT INTO sales_facts_daily(
                  tenant_id,data_version_id,fact_date,sku,site,sales_units,
                  sales_status,unit_price,discount,currency,source_record_sha256
                )
                VALUES(
                  :tenant_id,:version_id,CAST(:fact_date AS date),:sku,:site,:value,
                  :status,:unit_price,:discount,:currency,:record_sha256
                )
                ON CONFLICT DO NOTHING
            """), [{
                **common,
                "fact_date": date.fromisoformat(str(item["date"])),
                "sku": item["sku"],
                "site": item["site"],
                "value": item["value"],
                "status": item["status"] or "unknown",
                "unit_price": item["unit_price"],
                "discount": item["discount"],
                "currency": item["currency"],
                "record_sha256": item["record_sha256"],
            } for item in projection_rows])
            sales_count, inventory_count = len(projection_rows), 0
            actual = int(await session.scalar(text("""
                SELECT count(*) FROM sales_facts_daily
                 WHERE tenant_id=:tenant_id AND data_version_id=:version_id
            """), common) or 0)
        else:
            await session.execute(text("""
                INSERT INTO inventory_facts_daily(
                  tenant_id,data_version_id,fact_date,sku,site,inventory_units,
                  source_record_sha256
                )
                VALUES(
                  :tenant_id,:version_id,CAST(:fact_date AS date),:sku,:site,:value,
                  :record_sha256
                )
                ON CONFLICT DO NOTHING
            """), [{
                **common,
                "fact_date": date.fromisoformat(str(item["date"])),
                "sku": item["sku"],
                "site": item["site"],
                "value": item["value"],
                "record_sha256": item["record_sha256"],
            } for item in projection_rows])
            sales_count, inventory_count = 0, len(projection_rows)
            actual = int(await session.scalar(text("""
                SELECT count(*) FROM inventory_facts_daily
                 WHERE tenant_id=:tenant_id AND data_version_id=:version_id
            """), common) or 0)
        if actual != len(projection_rows):
            raise BusinessError(
                "DATA_FACT_PROJECTION_INCOMPLETE",
                "事实投影行数与标准数据不一致",
                status_code=409,
            )
        saved = (await session.execute(text("""
            INSERT INTO data_fact_projections(
              tenant_id,data_version_id,source_kind,canonical_sha256,
              projection_sha256,sales_row_count,inventory_row_count
            )
            VALUES(
              :tenant,:version_id,:source_kind,:canonical_sha256,
              :projection_sha256,:sales_count,:inventory_count
            )
            RETURNING source_kind,canonical_sha256,projection_sha256,
                      sales_row_count,inventory_row_count,projected_at
        """), {
            "tenant": tenant_id,
            "version_id": row["id"],
            "source_kind": source_kind,
            "canonical_sha256": row["canonical_sha256"],
            "projection_sha256": projection_sha256,
            "sales_count": sales_count,
            "inventory_count": inventory_count,
        })).mappings().one()
        return {**dict(saved), "version_uuid": version_uuid}

    async def execute(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        plan: DataQueryPlan,
        workspace_id: int | None = None,
        turn_uuid: str | None = None,
    ) -> dict[str, Any]:
        started = perf_counter()
        allowed = await session.scalar(text("""
            SELECT EXISTS(
              SELECT FROM user_role_assignments a
              JOIN role_permissions p
                ON p.role_id=a.role_id AND p.tenant_id=a.tenant_id
             WHERE a.tenant_id=:tenant AND a.user_id=:user
               AND (a.expires_at IS NULL OR a.expires_at>CURRENT_TIMESTAMP)
               AND p.permission_code='dataset.read'
            )
        """), {"tenant": tenant_id, "user": user_id})
        if allowed is not True:
            raise BusinessError(
                "PERMISSION_DENIED",
                "当前角色无权读取企业经营数据",
                status_code=403,
            )
        source_kind = data_query_source_kind(plan)
        version = await self._source_version(
            session,
            tenant_id=tenant_id,
            source_kind=source_kind,
            version_uuid=str(plan.data_version_uuid) if plan.data_version_uuid else None,
        )
        await self.project_version(
            session,
            tenant_id=tenant_id,
            version_uuid=version["version_uuid"],
        )
        resolved_filters = await self._resolved_filters(
            session,
            tenant_id=tenant_id,
            data_version_id=int(version["id"]),
            source_kind=source_kind,
            plan=plan,
        )
        if source_kind == "inventory" and plan.filters.statuses:
            raise BusinessError(
                "DATA_QUERY_FILTER_UNSUPPORTED",
                "库存快照不支持销售状态筛选",
                status_code=422,
            )
        await session.execute(
            text("SELECT set_config('statement_timeout',:timeout,true)"),
            {"timeout": f"{self.settings.data_query_timeout_ms}ms"},
        )
        rows, columns, limitations = await self._aggregate(
            session,
            tenant_id=tenant_id,
            data_version_id=int(version["id"]),
            source_kind=source_kind,
            plan=plan,
            filters=resolved_filters,
        )
        limited = len(rows) > plan.limit
        rows = rows[:plan.limit]
        material = {
            "plan": plan.model_dump(mode="json"),
            "resolved_filters": resolved_filters,
            "source_version_uuid": version["version_uuid"],
            "source_canonical_sha256": version["canonical_sha256"],
            "columns": columns,
            "rows": rows,
            "limited": limited,
            "limitations": limitations,
        }
        result_sha256 = _sha256(material)
        query_uuid = str(uuid4())
        duration_ms = max(0, round((perf_counter() - started) * 1000))
        await session.execute(text("""
            INSERT INTO data_query_executions(
              query_uuid,tenant_id,workspace_id,turn_uuid,requested_by,
              source_data_version_id,source_version_uuid,source_canonical_sha256,
              query_hash,query_plan,resolved_filters,result_data,result_sha256,
              result_row_count,duration_ms
            )
            VALUES(
              CAST(:query_uuid AS uuid),:tenant_id,:workspace_id,CAST(:turn_uuid AS uuid),
              :requested_by,:source_data_version_id,CAST(:source_version_uuid AS uuid),
              :source_canonical_sha256,:query_hash,CAST(:query_plan AS jsonb),
              CAST(:resolved_filters AS jsonb),CAST(:result_data AS jsonb),
              :result_sha256,:result_row_count,:duration_ms
            )
        """), {
            "query_uuid": query_uuid,
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "turn_uuid": turn_uuid,
            "requested_by": user_id,
            "source_data_version_id": version["id"],
            "source_version_uuid": version["version_uuid"],
            "source_canonical_sha256": version["canonical_sha256"],
            "query_hash": _sha256(plan.model_dump(mode="json")),
            "query_plan": _canonical(plan.model_dump(mode="json")).decode(),
            "resolved_filters": _canonical(resolved_filters).decode(),
            "result_data": _canonical(material).decode(),
            "result_sha256": result_sha256,
            "result_row_count": len(rows),
            "duration_ms": duration_ms,
        })
        DATA_QUERY_TOTAL.labels("succeeded", source_kind).inc()
        DATA_QUERY_DURATION.labels(source_kind).observe(duration_ms / 1000)
        return {
            "query_uuid": query_uuid,
            "plan": plan.model_dump(mode="json"),
            "resolved_filters": resolved_filters,
            "source_version": {
                "version_uuid": version["version_uuid"],
                "canonical_sha256": version["canonical_sha256"],
                "source_kind": source_kind,
                "filename": version["filename"],
                "confirmed_at": version["confirmed_at"],
            },
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "limited": limited,
            "limitations": limitations,
            "result_sha256": result_sha256,
            "duration_ms": duration_ms,
        }

    async def get(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        query_uuid: str,
    ) -> dict[str, Any] | None:
        row = (await session.execute(text("""
            SELECT q.query_uuid::text,q.requested_by,q.query_plan,q.resolved_filters,
                   q.result_data,q.result_sha256,q.result_row_count,q.duration_ms,q.created_at,
                   q.source_version_uuid::text,v.filename,v.confirmed_at,
                   q.source_canonical_sha256,v.rules->>'kind' AS source_kind
              FROM data_query_executions q
              JOIN forecast_data_versions v
                ON v.id=q.source_data_version_id AND v.tenant_id=q.tenant_id
             WHERE q.tenant_id=:tenant AND q.query_uuid=CAST(:query_uuid AS uuid)
        """), {"tenant": tenant_id, "query_uuid": query_uuid})).mappings().one_or_none()
        if row is None:
            return None
        material = dict(row["result_data"])
        return {
            "query_uuid": row["query_uuid"],
            "plan": row["query_plan"],
            "resolved_filters": row["resolved_filters"],
            "source_version": {
                "version_uuid": row["source_version_uuid"],
                "canonical_sha256": row["source_canonical_sha256"],
                "source_kind": row["source_kind"],
                "filename": row["filename"],
                "confirmed_at": row["confirmed_at"],
            },
            "columns": material["columns"],
            "rows": material["rows"],
            "row_count": row["result_row_count"],
            "limited": material["limited"],
            "limitations": material["limitations"],
            "result_sha256": row["result_sha256"],
            "requested_by": row["requested_by"],
            "duration_ms": row["duration_ms"],
            "created_at": row["created_at"],
        }

    async def _source_version(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        source_kind: str,
        version_uuid: str | None,
    ) -> dict[str, Any]:
        row = (await session.execute(text("""
            SELECT id,version_uuid::text,filename,canonical_sha256,confirmed_at,rules
              FROM forecast_data_versions
             WHERE tenant_id=:tenant AND status='confirmed'
               AND rules->>'kind'=:source_kind
               AND (
                 CAST(:version_uuid AS text) IS NULL
                 OR version_uuid=CAST(:version_uuid AS uuid)
               )
             ORDER BY confirmed_at DESC,id DESC LIMIT 1
        """), {
            "tenant": tenant_id,
            "source_kind": source_kind,
            "version_uuid": version_uuid,
        })).mappings().one_or_none()
        if row is None:
            label = "销量" if source_kind == "sales" else "库存"
            raise BusinessError(
                "DATA_QUERY_SOURCE_NOT_FOUND",
                f"没有可查询的已确认{label}数据版本",
                status_code=422,
            )
        return dict(row)

    async def _resolved_filters(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        data_version_id: int,
        source_kind: str,
        plan: DataQueryPlan,
    ) -> dict[str, Any]:
        filters = plan.filters.model_dump(mode="json")
        if plan.filters.relative_days:
            table = "sales_facts_daily" if source_kind == "sales" else "inventory_facts_daily"
            maximum = await session.scalar(text(f"""
                SELECT max(fact_date) FROM {table}
                 WHERE tenant_id=:tenant AND data_version_id=:version_id
            """), {"tenant": tenant_id, "version_id": data_version_id})
            if maximum:
                filters["date_to"] = maximum.isoformat()
                filters["date_from"] = (
                    maximum - timedelta(days=plan.filters.relative_days - 1)
                ).isoformat()
        filters.pop("relative_days", None)
        return filters

    async def _aggregate(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        data_version_id: int,
        source_kind: str,
        plan: DataQueryPlan,
        filters: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
        table = "sales_facts_daily" if source_kind == "sales" else "inventory_facts_daily"
        dimensions: list[tuple[str, str]] = []
        if plan.grain == "daily":
            dimensions.append(("period", "fact_date"))
        elif plan.grain == "weekly":
            dimensions.append(("period", "date_trunc('week',fact_date)::date"))
        elif plan.grain == "monthly":
            dimensions.append(("period", "date_trunc('month',fact_date)::date"))
        for dimension in plan.group_by:
            dimensions.append((dimension, dimension))
        select_items = [f"{expression} AS {alias}" for alias, expression in dimensions]
        select_items.extend(
            f"{_METRIC_SQL[metric]} AS {metric}" for metric in plan.metrics
        )
        needs_price_coverage = bool(set(plan.metrics) & _CURRENCY_METRICS)
        if needs_price_coverage:
            select_items.append(
                "round((sum(abs(sales_units)) filter(where unit_price is not null)"
                "/nullif(sum(abs(sales_units)),0))::numeric,4) AS price_coverage"
            )
        where = [
            "tenant_id=:tenant",
            "data_version_id=:version_id",
            "(CAST(:date_from AS date) IS NULL OR fact_date>=CAST(:date_from AS date))",
            "(CAST(:date_to AS date) IS NULL OR fact_date<=CAST(:date_to AS date))",
            "(:skus_empty OR sku=ANY(CAST(:skus AS text[])))",
            "(:sites_empty OR site=ANY(CAST(:sites AS text[])))",
        ]
        if source_kind == "sales":
            where.append(
                "(:statuses_empty OR sales_status=ANY(CAST(:statuses AS text[])))"
            )
        group_expressions = [expression for _, expression in dimensions]
        group_sql = f" GROUP BY {','.join(group_expressions)}" if group_expressions else ""
        order_sql = f" ORDER BY {','.join(group_expressions)}" if group_expressions else ""
        query = text(
            f"SELECT {','.join(select_items)} FROM {table} "
            f"WHERE {' AND '.join(where)}{group_sql}{order_sql} LIMIT :row_limit"
        )
        params = {
            "tenant": tenant_id,
            "version_id": data_version_id,
            "date_from": (
                date.fromisoformat(filters["date_from"])
                if isinstance(filters.get("date_from"), str)
                else filters.get("date_from")
            ),
            "date_to": (
                date.fromisoformat(filters["date_to"])
                if isinstance(filters.get("date_to"), str)
                else filters.get("date_to")
            ),
            "skus": filters.get("skus") or [],
            "sites": filters.get("sites") or [],
            "statuses": filters.get("statuses") or [],
            "skus_empty": not bool(filters.get("skus")),
            "sites_empty": not bool(filters.get("sites")),
            "statuses_empty": not bool(filters.get("statuses")),
            "row_limit": plan.limit + 1,
        }
        records = [
            _jsonable(dict(row))
            for row in (await session.execute(query, params)).mappings().all()
        ]
        limitations: list[str] = []
        currency = None
        if needs_price_coverage:
            currencies = (await session.execute(text("""
                SELECT DISTINCT currency FROM sales_facts_daily
                 WHERE tenant_id=:tenant AND data_version_id=:version_id
                   AND (CAST(:date_from AS date) IS NULL OR fact_date>=CAST(:date_from AS date))
                   AND (CAST(:date_to AS date) IS NULL OR fact_date<=CAST(:date_to AS date))
                   AND (:skus_empty OR sku=ANY(CAST(:skus AS text[])))
                   AND (:sites_empty OR site=ANY(CAST(:sites AS text[])))
                   AND (:statuses_empty OR sales_status=ANY(CAST(:statuses AS text[])))
                   AND unit_price IS NOT NULL
            """), params)).scalars().all()
            if len(currencies) > 1:
                raise BusinessError(
                    "DATA_QUERY_CURRENCY_MIXED",
                    "筛选范围包含多种币种，系统不会自动换汇；请缩小站点或数据范围",
                    status_code=422,
                )
            currency = currencies[0] if currencies else None
            if any((item.get("price_coverage") or 0) < 1 for item in records):
                limitations.append(
                    "部分销量行没有唯一成交单价；销售额与均价仅覆盖有明确价格的记录。"
                )
        columns = [
            {"key": alias, "label": {"period": "期间", "sku": "SKU", "site": "站点"}[alias],
             "type": "dimension"}
            for alias, _ in dimensions
        ]
        for metric in plan.metrics:
            label, unit = _METRIC_LABELS[metric]
            columns.append({
                "key": metric,
                "label": label,
                "type": "metric",
                "unit": currency if metric in _CURRENCY_METRICS else unit,
            })
        if needs_price_coverage:
            columns.append({
                "key": "price_coverage",
                "label": "价格覆盖率",
                "type": "quality",
                "unit": "ratio",
            })
        return records, columns, limitations


def render_data_query_answer(result: dict[str, Any]) -> str:
    rows = result.get("rows") or []
    if not rows:
        return "已按确认数据版本执行查询，但当前筛选条件下没有记录。"
    columns = result.get("columns") or []
    metrics = [item for item in columns if item.get("type") == "metric"]
    dimensions = [item for item in columns if item.get("type") == "dimension"]
    version = result["source_version"]
    if len(rows) == 1 and not dimensions:
        facts = []
        for item in metrics:
            value = rows[0].get(item["key"])
            unit = item.get("unit") or ""
            facts.append(f"{item['label']}为 {value if value is not None else '不可计算'}{unit}")
        answer = "；".join(facts) + "。"
    else:
        previews = []
        for row in rows[:5]:
            scope = " / ".join(str(row.get(item["key"])) for item in dimensions)
            values = "，".join(
                f"{item['label']} {row.get(item['key']) if row.get(item['key']) is not None else '不可计算'}"
                f"{item.get('unit') or ''}"
                for item in metrics
            )
            previews.append(f"{scope}：{values}" if scope else values)
        answer = f"查询返回 {len(rows)} 行。 " + "；".join(previews) + "。"
    if result.get("limited"):
        answer += " 结果已达到展示上限，请缩小时间或分组范围。"
    for limitation in result.get("limitations") or []:
        answer += f" 限制：{limitation}"
    return (
        answer
        + f" 数据版本 {str(version['version_uuid'])[:8]}，"
        + f"结果校验 {result['result_sha256'][:12]}。"
    )
