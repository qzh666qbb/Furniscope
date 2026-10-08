"""Synthetic HTTP/PG evidence for observation provenance and ranking labels."""

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from furniscope_api.app import create_app
from furniscope_api.database import Database, bind_tenant_session
from furniscope_api.repositories.analysis_task_repository import AnalysisTaskRepository
from furniscope_api.schemas.opportunity_policy import OpportunityOutcomeSave
from furniscope_api.services.opportunity_outcomes import ranking_candidate
from test_enterprise_http import fixture_settings, seed_enterprise


def test_outcome_dates_financials_and_unimplemented_are_not_success_or_failure():
    base = dict(expected_revision=0, accepted_feedback_id=1, status="planned", evidence="合成计划")
    assert OpportunityOutcomeSave(**base).result_label is None
    for change in [
        {"status": "completed"}, {"result_label": "not_achieved"},
        {"implementation_start": "2099-01-01"}, {"source_sku": "X"},
        {"financials": {"revenue": float("inf"), "cost": 1, "currency": "USD", "basis": "合成"}},
        {"status": "in_progress", "implementation_start": "2025-01-01",
         "observation_start": "2025-01-01", "observation_end": "2025-01-02",
         "operational_metrics": {"sample_units": 2, "production_units": 10,
                                 "sold_units": 11, "returned_units": 0}},
        {"status": "in_progress", "implementation_start": "2025-01-01",
         "observation_start": "2025-01-01", "observation_end": "2025-01-02",
         "operational_metrics": {"sample_units": 2, "production_units": 10,
                                 "sold_units": 8, "returned_units": 9}},
        {"status": "in_progress", "implementation_start": "2025-01-02",
         "observation_start": "2025-01-01", "observation_end": "2025-01-03"},
    ]:
        with pytest.raises(ValueError):
            OpportunityOutcomeSave(**(base | change))
    row = ranking_candidate({"id": 1, "decision_snapshot": None, "feedback": {
        "id": 1, "revision": 1, "status": "accepted", "reason": "旧样本", "created_at": "2025-01-01T00:00:00+00:00",
    }, "outcome": None})
    assert row["features"] is None and row["labels"]["decision"] == 1
    assert not row["labels"]["decision_eligible"] and row["labels"]["outcome"] is None
    assert row["issues"] == ["legacy_features_not_reconstructable"]


def test_outcome_implementation_date_uses_utc_boundary():
    row = ranking_candidate({
        "id": 1,
        "decision_snapshot": {"captured_at": "2026-10-06T01:00:00+08:00"},
        "feedback": {
            "id": 7,
            "revision": 1,
            "status": "accepted",
            "reason": "accepted",
            "created_at": "2026-10-06T01:05:00+08:00",
        },
        "outcome": {
            "id": 9,
            "revision": 1,
            "accepted_feedback_id": 7,
            "status": "completed",
            "implementation_start": "2026-10-05",
            "implementation_end": "2026-10-05",
            "observation_start": "2026-10-05",
            "observation_end": "2026-10-05",
            "result_label": "achieved",
            "evidence": "UTC boundary",
            "financials": None,
            "sales_snapshot": None,
            "created_at": "2026-10-06T01:10:00+08:00",
        },
    })

    assert row["labels"]["outcome_eligible"]
    assert "retrospective_implementation" not in row["issues"]


@pytest.mark.skipif(not os.getenv("FURNISCOPE_TEST_DATABASE_URL"), reason="needs isolated PostgreSQL")
def test_outcome_http_provenance_revision_cutoff_and_rls(tmp_path):
    settings = fixture_settings(os.environ["FURNISCOPE_TEST_DATABASE_URL"], tmp_path)
    own, other = asyncio.run(seed_enterprise(settings))
    today = datetime.now(timezone.utc).date().isoformat()
    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()

    async def add_unlabeled_candidate():
        db = Database(settings)
        try:
            async with db.session_factory() as session:
                original = (await session.execute(text("""
                    SELECT t.* FROM analysis_tasks t JOIN market_opportunities o ON o.analysis_job_id=t.id
                     WHERE o.id=:o
                """), {"o": own["opportunity_id"]})).mappings().one()
                task = await AnalysisTaskRepository().create(session, tenant_id=own["tenant_id"],
                    user_id=own["user_id"], idempotency_key=uuid4().hex,
                    payload={k: original[k] for k in ("job_name", "job_type", "product_id",
                        "product_profile_version_id", "dataset_id", "target_country", "target_platform",
                        "analysis_currency", "analysis_config")},
                    versions=dict.fromkeys(["ontology_version", "scoring_version",
                                            "prompt_bundle_version", "model_route_version"], "synthetic"))
                candidate = await session.scalar(text("""
                    INSERT INTO market_opportunities(tenant_id,analysis_job_id,opportunity_code,title,description,
                      target_country,target_platform,primary_cluster_ids,demand_heat_score,unmet_need_score,
                      competition_space_score,enterprise_fit_score,base_score,confidence,recommendation_level,
                      weight_config,scoring_version,policy_snapshot)
                    SELECT tenant_id,analysis_job_id,'UNLABELED','未标注候选',description,target_country,target_platform,
                      primary_cluster_ids,20,30,40,NULL,30,.5,'collect_more_data',weight_config,scoring_version,
                      policy_snapshot FROM market_opportunities WHERE id=:o RETURNING id
                """), {"o": own["opportunity_id"]})
                await session.execute(text("""
                    INSERT INTO market_opportunities(tenant_id,analysis_job_id,opportunity_code,title,description,
                      target_country,target_platform,primary_cluster_ids,demand_heat_score,unmet_need_score,
                      competition_space_score,enterprise_fit_score,base_score,confidence,recommendation_level,
                      weight_config,scoring_version,policy_snapshot)
                    SELECT tenant_id,:task,'SECOND-TASK','另一任务候选',description,target_country,target_platform,
                      primary_cluster_ids,20,30,40,NULL,30,.5,'collect_more_data',weight_config,scoring_version,
                      policy_snapshot FROM market_opportunities WHERE id=:o
                """), {"o": own["opportunity_id"], "task": task["id"]})
                await session.commit()
                return candidate
        finally:
            await db.close()

    unlabeled = asyncio.run(add_unlabeled_candidate())
    with TestClient(create_app(settings)) as client:
        def auth(identity):
            response = client.post("/api/v1/auth/login", json={key: identity[key] for key in ("email", "password")})
            assert response.status_code == 200
            return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}
        headers, other_headers = auth(own), auth(other)
        endpoint = f"/api/v1/enterprise/opportunities/{own['opportunity_id']}"

        def call(method, url, body=None, status=200, identity=None):
            response = client.request(method, url, headers=identity or headers, json=body)
            assert response.status_code == status, response.text
            return response.json().get("data")

        def confirm(rows, identity=None):
            response = client.post("/api/v1/forecast/data-imports", headers=identity or headers,
                files={"file": ("outcome.json", json.dumps(rows).encode(), "application/json")})
            assert response.status_code == 200, response.text
            version = response.json()["data"]
            preview = call("POST", f"/api/v1/forecast/data-imports/{version['version_uuid']}/preflight",
                           {"rules": version["rules"]}, identity=identity)
            call("POST", f"/api/v1/forecast/data-imports/{version['version_uuid']}/confirm",
                 {"preview_sha256": preview["preview_sha256"]}, identity=identity)
            return version["version_uuid"]

        planned = {"expected_revision": 0, "accepted_feedback_id": 1, "status": "planned", "evidence": "合成实施计划"}
        call("PUT", f"{endpoint}/outcomes", planned, status=409)
        accepted = call("PUT", f"{endpoint}/feedback", {"expected_revision": 0, "status": "accepted", "reason": "合成目标：观察三件销量"})["current"]
        assert accepted["score_snapshot"]["demand_heat_score"] == 80
        assert "profit_space_score" in accepted["score_snapshot"]
        frozen = accepted["score_snapshot"]["feature_capture"]
        assert frozen["task"]["product_id"] == own["product_id"]
        planned["accepted_feedback_id"] = accepted["id"]
        event = call("PUT", f"{endpoint}/outcomes", planned)["current"]
        assert event["revision"] == 1
        call("PUT", f"{endpoint}/outcomes", planned, status=409)
        call("GET", f"{endpoint}/outcomes", identity=other_headers, status=404)
        call("PUT", f"{endpoint}/outcomes", planned, identity=other_headers, status=404)
        rows = [{"date": today, "sku": "SAME-SKU", "site": "US", "sales": 3}]
        version = confirm(rows)
        other_version = confirm(rows, identity=other_headers)
        completed = {**planned, "expected_revision": 1, "status": "completed", "implementation_start": today,
                     "implementation_end": today, "observation_start": today, "observation_end": today,
                     "result_label": "achieved", "evidence": "合成销售报表：目标三件，实际三件",
                     "data_version_uuid": version, "source_sku": "SAME-SKU",
                     "operational_metrics": {"sample_units": 2, "production_units": 4,
                                             "sold_units": 3, "returned_units": 1},
                     "financials": {"revenue": 300, "cost": 240, "currency": "USD", "basis": "合成报表含制造与物流费"}}
        call("PUT", f"{endpoint}/outcomes", {**completed, "data_version_uuid": other_version}, status=404)
        call("PUT", f"{endpoint}/outcomes", {**completed, "source_sku": "WRONG"}, status=422)
        call("PUT", f"{endpoint}/outcomes", {**completed, "implementation_start": yesterday, "observation_start": yesterday}, status=422)
        call("PUT", f"{endpoint}/outcomes", {**completed, "implementation_start": yesterday,
             "observation_end": yesterday}, status=422)

        async def canonical_path():
            db = Database(settings)
            try:
                async with db.session_factory() as session:
                    key = await session.scalar(text("SELECT canonical_storage_key FROM forecast_data_versions WHERE version_uuid=CAST(:v AS uuid)"), {"v": version})
                    return Path(settings.demo_storage_root) / key
            finally:
                await db.close()
        path = asyncio.run(canonical_path())
        original = path.read_bytes()
        try:
            path.write_bytes(original + b" ")
            call("PUT", f"{endpoint}/outcomes", completed, status=409)
        finally:
            path.write_bytes(original)
        current = call("PUT", f"{endpoint}/outcomes", completed)["current"]
        assert current["sales_snapshot"]["units"] == 3
        assert current["sales_snapshot"]["mapping"]["product_id"] == own["product_id"]
        assert current["sales_snapshot"]["canonical_sha256"]
        assert current["operational_metrics"]["return_rate"] == pytest.approx(1 / 3, abs=1e-6)
        assert current["financials"]["net_operating_amount"] == 60
        assert current["financials"]["reported_roi"] == 0.25
        exported = call("GET", "/api/v1/enterprise/opportunity-ranking/export?limit=1")
        group = exported["groups"][0]
        assert {r["opportunity_id"] for r in group["candidates"]} == {own["opportunity_id"], unlabeled}
        candidate = group["candidates"][0]
        assert candidate["features"] == frozen
        assert candidate["labels"]["decision_eligible"] and candidate["labels"]["outcome_eligible"]
        assert group["candidates"][1]["labels"]["decision"] is None
        assert not group["candidates"][1]["labels"]["outcome_eligible"]
        cutoff = exported["as_of"].replace("+", "%2B")
        second_page = call("GET", f"/api/v1/enterprise/opportunity-ranking/export?limit=1&as_of={cutoff}&after_task_id={exported['next_after_task_id']}")
        assert second_page["next_after_task_id"] is None and len(second_page["groups"]) == 1
        assert second_page["groups"][0]["task_id"] != group["task_id"]
        assert call("GET", "/api/v1/enterprise/opportunity-ranking/export", identity=other_headers)["groups"] == []
        call("GET", "/api/v1/enterprise/opportunity-ranking/export?as_of=2099-01-01T00:00:00Z", status=422)
        call("GET", "/api/v1/enterprise/opportunity-ranking/export?as_of=2025-01-01T00:00:00", status=422)
        # A changed decision invalidates the linked outcome but cannot rewrite the old export.
        call("PUT", f"{endpoint}/feedback", {"expected_revision": 1, "status": "rejected", "reason": "合成补充核验：撤销采纳"})
        latest = call("GET", "/api/v1/enterprise/opportunity-ranking/export")["groups"][0]["candidates"][0]
        assert latest["labels"]["decision"] == 0 and not latest["labels"]["outcome_eligible"]
        assert "outcome_acceptance_superseded" in latest["issues"]
        assert call("GET", f"/api/v1/enterprise/opportunity-ranking/export?as_of={cutoff}&limit=1") == exported
        call("PUT", f"{endpoint}/outcomes", {**completed, "expected_revision": 2}, status=409)
        accepted_again = call("PUT", f"{endpoint}/feedback", {"expected_revision": 2, "status": "accepted", "reason": "合成重新采纳"})["current"]
        retro = {**completed, "expected_revision": 2, "accepted_feedback_id": accepted_again["id"],
                 "implementation_start": yesterday, "data_version_uuid": None, "source_sku": None}
        call("PUT", f"{endpoint}/outcomes", retro)
        candidate = call("GET", "/api/v1/enterprise/opportunity-ranking/export")["groups"][0]["candidates"][0]
        assert "retrospective_implementation" in candidate["issues"]
        assert not candidate["labels"]["outcome_eligible"]

    async def database_guards():
        db = Database(settings)
        try:
            async with db.session_factory() as session:
                with pytest.raises(DBAPIError, match="immutable"):
                    await session.execute(text("UPDATE market_opportunities SET demand_heat_score=1 WHERE id=:o"), {"o": own["opportunity_id"]})
                await session.rollback()
                with pytest.raises(DBAPIError, match="immutable"):
                    await session.execute(text("DELETE FROM market_opportunities WHERE id=:o"), {"o": unlabeled})
                await session.rollback()
                with pytest.raises(DBAPIError, match="immutable"):
                    await session.execute(text("UPDATE opportunity_outcome_events SET evidence='changed' WHERE id=:id"), {"id": current["id"]})
                await session.rollback()
                await bind_tenant_session(session, other["tenant_id"])
                assert not (await session.execute(text("SELECT * FROM opportunity_outcome_events"))).all()
                with pytest.raises(DBAPIError):
                    await session.execute(text("""
                        INSERT INTO opportunity_outcome_events(tenant_id,opportunity_id,analysis_job_id,
                          accepted_feedback_id,revision,status,evidence,created_by)
                        VALUES(:t,:o,:task,:a,99,'planned','cross tenant',:u)
                    """), {"t": other["tenant_id"], "u": other["user_id"], "o": own["opportunity_id"],
                             "task": group["task_id"], "a": accepted["id"]})
        finally:
            await db.close()
    asyncio.run(database_guards())
