"""Append-only observations and point-in-time ranking exports, never causal ROI."""

import json
from datetime import datetime, timezone

from sqlalchemy import text

from ..errors import BusinessError
from .forecast_catalog import ForecastCatalog
from .forecast_data_service import ForecastDataService
from .sales_data_cleaner import sha256, stable_json


class OpportunityOutcomeService:
    def __init__(self, settings):
        self.data = ForecastDataService(settings)

    async def opportunity(self, session, tenant_id, opportunity_id, *, lock=False):
        row = (await session.execute(text("""
            SELECT o.*,t.product_id FROM market_opportunities o JOIN analysis_tasks t
              ON t.id=o.analysis_job_id AND t.tenant_id=o.tenant_id
             WHERE o.id=:o AND o.tenant_id=:t
        """ + (" FOR UPDATE OF o" if lock else "")),
            {"t": tenant_id, "o": opportunity_id})).mappings().one_or_none()
        if row is None:
            raise BusinessError("OPPORTUNITY_NOT_FOUND", "机会不存在或不可访问", status_code=404)
        return row

    async def history(self, session, tenant_id, opportunity_id):
        await self.opportunity(session, tenant_id, opportunity_id)
        rows = (await session.execute(text("""
            SELECT e.id,e.revision,e.accepted_feedback_id,e.status,e.implementation_start,
              e.implementation_end,e.observation_start,e.observation_end,e.result_label,
              e.evidence,e.operational_metrics,e.financials,e.sales_snapshot,e.created_by,e.created_at
              FROM opportunity_outcome_events e
             WHERE e.tenant_id=:t AND e.opportunity_id=:o ORDER BY revision DESC
        """), {"t": tenant_id, "o": opportunity_id})).mappings().all()
        return {"current": dict(rows[0]) if rows else None, "history": [dict(row) for row in rows],
                "semantics": "enterprise_reported_observation_not_causal_return"}

    async def sales_observation(self, session, tenant_id, opportunity, body):
        version = await self.data.row(session, tenant_id, str(body.data_version_uuid))
        if version["status"] != "confirmed" or version["rules"]["kind"] != "sales":
            raise BusinessError("OUTCOME_DATA_NOT_CONFIRMED", "回流须关联本企业已确认销量版本", status_code=422)
        payload = json.loads(self.data.read_verified(
            tenant_id, version["canonical_storage_key"], version["canonical_sha256"]))
        site = opportunity["target_country"].strip()
        first, last = body.observation_start.isoformat(), body.observation_end.isoformat()
        records = [r for r in payload["records"]
                   if r["sku"] == body.source_sku and r["site"] == site and first <= r["date"] <= last]
        days = (body.observation_end - body.observation_start).days + 1
        if len(records) != days or len({r["date"] for r in records}) != days:
            raise BusinessError("OUTCOME_DATA_COVERAGE", "销量版本必须完整覆盖观察区间、来源SKU和机会站点", status_code=422)
        # Resolve every source for this site to detect many-to-one ambiguity too.
        catalog = await ForecastCatalog().preview(session, tenant_id, [
            {"sku": sku, "site": site} for sku in sorted(
                {r["sku"] for r in payload["records"] if r["site"] == site})])
        matches = [item for item in catalog["items"] if item["source_sku"] == body.source_sku]
        collisions = [c for c in catalog["conflicts"] if
                      c.get("source_sku") == body.source_sku or
                      c.get("product_sku") == (matches[0]["sku"] if matches else None)]
        if len(matches) != 1 or collisions or matches[0]["product_id"] != opportunity["product_id"]:
            raise BusinessError("OUTCOME_SKU_MISMATCH", "来源SKU须唯一关联该机会的产品，请先核验SKU映射", status_code=422)
        return version["id"], {
            "version_uuid": str(body.data_version_uuid), "canonical_sha256": version["canonical_sha256"],
            "raw_sha256": version["raw_sha256"], "confirmed_at": version["confirmed_at"].isoformat(),
            "mapping": matches[0], "source_sku": body.source_sku, "site": site,
            "mapping_sha256": sha256(stable_json(matches[0])),
            "observation_start": first, "observation_end": last, "days": days,
            "sales_basis": version["rules"]["sales_basis"], "units": sum(r["sales"] for r in records),
            "filled_days": sum(r.get("transformation") == "confirmed_missing_zero" for r in records),
            "non_active_days": sum(r.get("status") != "active" for r in records),
            "semantics": "observed_units_not_incremental_sales",
        }

    async def save(self, session, tenant_id, user_id, opportunity_id, body):
        opportunity = await self.opportunity(session, tenant_id, opportunity_id, lock=True)
        feedback = (await session.execute(text("""
            SELECT id,status FROM opportunity_feedback_events
             WHERE tenant_id=:t AND opportunity_id=:o ORDER BY revision DESC LIMIT 1
        """), {"t": tenant_id, "o": opportunity_id})).mappings().one_or_none()
        if not feedback or feedback["id"] != body.accepted_feedback_id or feedback["status"] != "accepted":
            raise BusinessError("OUTCOME_ACCEPTANCE_REQUIRED", "请先采纳机会，并关联最新的采纳记录", status_code=409)
        revision = await session.scalar(text("""
            SELECT COALESCE(max(revision),0) FROM opportunity_outcome_events
             WHERE tenant_id=:t AND opportunity_id=:o
        """), {"t": tenant_id, "o": opportunity_id})
        if revision != body.expected_revision:
            raise BusinessError("OUTCOME_REVISION_CONFLICT", "实施记录已更新，请重新读取后保存", status_code=409)
        data_id, sales = None, None
        if body.data_version_uuid:
            data_id, sales = await self.sales_observation(session, tenant_id, opportunity, body)
        operational_metrics = None
        if body.operational_metrics:
            operational_metrics = body.operational_metrics.model_dump()
            sold = operational_metrics["sold_units"]
            operational_metrics.update({
                "return_rate": (
                    round(operational_metrics["returned_units"] / sold, 6)
                    if sold else None
                ),
                "semantics": "enterprise_reported_operational_funnel",
            })
        financials = None
        if body.financials:
            financials = body.financials.model_dump()
            net_amount = round(financials["revenue"] - financials["cost"], 6)
            financials.update({
                "net_operating_amount": net_amount,
                "reported_roi": (
                    round(net_amount / financials["cost"], 6)
                    if financials["cost"] else None
                ),
                "semantics": "enterprise_reported_observation_not_causal_return",
            })
        values = body.model_dump(exclude={
            "expected_revision", "data_version_uuid", "source_sku",
            "operational_metrics", "financials",
        })
        await session.execute(text("""
            INSERT INTO opportunity_outcome_events
              (tenant_id,opportunity_id,analysis_job_id,accepted_feedback_id,revision,status,
               implementation_start,implementation_end,observation_start,observation_end,
               result_label,evidence,operational_metrics,financials,data_version_id,
               sales_snapshot,created_by)
            VALUES(:t,:o,:task,:accepted_feedback_id,:r,:status,:implementation_start,:implementation_end,
                   :observation_start,:observation_end,:result_label,:evidence,
                   CAST(:operational_metrics AS jsonb),CAST(:financials AS jsonb),
                   :data,CAST(:sales AS jsonb),:u)
        """), {**values, "t": tenant_id, "o": opportunity_id, "task": opportunity["analysis_job_id"],
                 "r": revision + 1, "u": user_id, "data": data_id,
                 "operational_metrics": (
                     json.dumps(operational_metrics) if operational_metrics else None
                 ),
                 "financials": json.dumps(financials) if financials else None,
                 "sales": json.dumps(sales) if sales else None})
        return await self.history(session, tenant_id, opportunity_id)

    async def export(self, session, tenant_id, as_of=None, after_task_id=0, limit=100):
        now = datetime.now(timezone.utc)
        cutoff = as_of or now
        if cutoff > now:
            raise BusinessError("RANKING_FUTURE_CUTOFF", "导出截点不能在未来", status_code=422)
        params = {"t": tenant_id, "cutoff": cutoff, "after": after_task_id, "limit": limit + 1}
        tasks = (await session.execute(text("""
            SELECT t.id,t.task_uuid::text,t.created_at FROM analysis_tasks t
             WHERE t.tenant_id=:t AND t.id>:after AND t.created_at<=:cutoff
               AND EXISTS(SELECT FROM market_opportunities o WHERE o.analysis_job_id=t.id
                 AND o.tenant_id=t.tenant_id AND o.created_at<=:cutoff)
             ORDER BY t.id LIMIT :limit
        """), params)).mappings().all()
        groups = []
        for task in tasks[:limit]:
            rows = (await session.execute(text("""
                SELECT o.id,o.decision_snapshot,to_jsonb(f) AS feedback,to_jsonb(e) AS outcome
                  FROM market_opportunities o
                  LEFT JOIN LATERAL(SELECT * FROM opportunity_feedback_events f
                     WHERE f.tenant_id=o.tenant_id AND f.opportunity_id=o.id AND f.created_at<=:cutoff
                     ORDER BY f.revision DESC LIMIT 1) f ON true
                  LEFT JOIN LATERAL(SELECT * FROM opportunity_outcome_events e
                     WHERE e.tenant_id=o.tenant_id AND e.opportunity_id=o.id AND e.created_at<=:cutoff
                     ORDER BY e.revision DESC LIMIT 1) e ON true
                 WHERE o.tenant_id=:t AND o.analysis_job_id=:task AND o.created_at<=:cutoff
                   AND (o.decision_snapshot IS NULL OR
                     (o.decision_snapshot->>'captured_at')::timestamptz<=:cutoff)
                 ORDER BY o.id
            """), {**params, "task": task["id"]})).mappings().all()
            groups.append({"task_id": task["id"], "task_uuid": task["task_uuid"],
                           "group_time": task["created_at"], "candidates": [ranking_candidate(r) for r in rows]})
        return {
            "format_version": "opportunity-ranking-v2", "as_of": cutoff,
            "label_semantics": {"decision": "user_preference", "outcome": "reported_goal_attainment_not_causal_return"},
            "split_contract": "group_by_task_order_by_group_time; train_labels_available_before_validation_start",
            "groups": groups,
            "next_after_task_id": tasks[limit - 1]["id"] if len(tasks) > limit else None,
        }


def ranking_candidate(row):
    snapshot, feedback, outcome = row["decision_snapshot"], row["feedback"], row["outcome"]
    decision_label = {"accepted": 1, "rejected": 0}.get((feedback or {}).get("status"))
    issues = []
    if not snapshot:
        issues.append("legacy_features_not_reconstructable")
    capture = datetime.fromisoformat(snapshot["captured_at"]) if snapshot else None
    if feedback and capture and datetime.fromisoformat(feedback["created_at"]) < capture:
        issues.append("decision_precedes_features")
    linked = bool(outcome and feedback and feedback["status"] == "accepted"
                  and outcome["accepted_feedback_id"] == feedback["id"])
    if outcome and not linked:
        issues.append("outcome_acceptance_superseded")
    if outcome and capture and outcome["implementation_start"]:
        capture_date = capture.astimezone(timezone.utc).date()
        feedback_date = (
            datetime.fromisoformat(feedback["created_at"]).astimezone(timezone.utc).date()
            if feedback else capture_date
        )
        earliest = max(capture_date, feedback_date)
        if outcome["implementation_start"] < earliest.isoformat():
            issues.append("retrospective_implementation")
    outcome_label = ({"achieved": 1, "not_achieved": 0}.get(outcome.get("result_label"))
                     if linked and outcome["status"] == "completed" else None)
    feature_valid = snapshot is not None and "decision_precedes_features" not in issues
    return {
        "opportunity_id": row["id"], "features": snapshot, "issues": issues,
        "decision": {key: feedback[key] for key in ("id", "revision", "status", "reason", "created_at")} if feedback else None,
        "outcome": {key: outcome[key] for key in (
            "id", "revision", "accepted_feedback_id", "status", "implementation_start", "implementation_end",
            "observation_start", "observation_end", "result_label", "evidence", "financials",
            "sales_snapshot", "created_at")} if outcome else None,
        "labels": {
            "decision": decision_label,
            "decision_eligible": feature_valid and decision_label is not None,
            "decision_available_at": feedback["created_at"] if feedback else None,
            "outcome": outcome_label,
            "outcome_eligible": feature_valid and linked and outcome_label is not None
                and "retrospective_implementation" not in issues,
            "outcome_available_at": outcome["created_at"] if outcome else None,
        },
    }
