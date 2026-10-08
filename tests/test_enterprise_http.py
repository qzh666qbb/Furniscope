"""Real HTTP + PostgreSQL enterprise lifecycle. All inputs are synthetic."""
import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import text

from furniscope_agent.enterprise_decision import default_policy
from furniscope_api.app import create_app
from furniscope_api.config import ApiSettings
from furniscope_api.database import Database
from furniscope_api.repositories.analysis_task_repository import AnalysisTaskRepository
from furniscope_api.security.password import PasswordService
from test_enterprise_training import sales_records


def fixture_settings(dsn, directory):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return ApiSettings(
        app_env="test", database_url=dsn.replace("postgresql://", "postgresql+asyncpg://"),
        furniscope_jwt_private_key=key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode(),
        furniscope_jwt_public_keys_json=json.dumps({"enterprise-test": key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()}),
        demo_storage_root=str(directory / "raw"), forecast_artifact_root=str(directory / "models"),
        forecast_state_dir=str(directory / "no-shared-history"),
    )


async def seed_enterprise(settings):
    db = Database(settings)
    identities = []
    async with db.session_factory() as session:
        for _ in range(2):
            suffix = uuid4().hex[:12]
            tenant = await session.scalar(text("INSERT INTO tenants(tenant_code,name,status,data_class) VALUES(:code,'合成验收企业','active','test') RETURNING id"), {"code": f"HTTP_{suffix.upper()}"})
            password = f"Enterprise-{uuid4()}!"
            email = f"enterprise-{suffix}@example.invalid"
            user = await session.scalar(text("""
                INSERT INTO users(tenant_id,email,password_hash,name,role_code,status)
                VALUES(:t,:email,:password,'合成验收员','user','active') RETURNING id
            """), {"t": tenant, "email": email, "password": PasswordService().hash(password)})
            product = await session.scalar(text("""
                INSERT INTO products(tenant_id,created_by,sku,name,category_code)
                VALUES(:t,:u,'SAME-SKU','合成测试沙发','sofa') RETURNING id
            """), {"t": tenant, "u": user})
            identities.append({"tenant_id": tenant, "user_id": user, "product_id": product,
                               "email": email, "password": password})
        tenant, user = identities[0]["tenant_id"], identities[0]["user_id"]
        profile = await session.scalar(text("""
            INSERT INTO product_profile_versions(tenant_id,product_id,version_no,schema_version,status)
            VALUES(:t,:p,1,'synthetic-http','draft') RETURNING id
        """), {"t": tenant, "p": identities[0]["product_id"]})
        dataset = await session.scalar(text("""
            INSERT INTO market_datasets(tenant_id,name,platform,market_country,category_code,
              data_end_date,source_type,source_name,status)
            VALUES(:t,'合成接口验收样本','amazon','US','sofa','2025-06-01','demo_synthetic',
              'synthetic engineering fixture','ready') RETURNING id
        """), {"t": tenant})
        task = await AnalysisTaskRepository().create(session, tenant_id=tenant, user_id=user,
            idempotency_key=uuid4().hex, payload={"job_name": "合成接口验收", "job_type": "product_market_fit",
                "product_id": identities[0]["product_id"], "product_profile_version_id": profile,
                "dataset_id": dataset, "target_country": "US", "target_platform": "amazon",
                "analysis_currency": "USD", "analysis_config": {}},
            versions=dict.fromkeys(["ontology_version", "scoring_version", "prompt_bundle_version", "model_route_version"], "synthetic-http"))
        await session.execute(text("UPDATE analysis_tasks SET status='succeeded', external_stage='completed', progress_percent=100, completed_at=now() WHERE id=:id"), {"id": task["id"]})
        opportunity = await session.scalar(text("""
            INSERT INTO market_opportunities(tenant_id,analysis_job_id,opportunity_code,title,description,
              target_country,target_platform,primary_cluster_ids,demand_heat_score,unmet_need_score,
              competition_space_score,enterprise_fit_score,base_score,confidence,recommendation_level,
              weight_config,scoring_version,manufacturing_fit,market_score,adjusted_score,policy_snapshot)
            VALUES(:t,:task,'HTTP-O1','包装条件待验证','合成工程样本，不代表真实市场结论','US','amazon',
              '[1]',80,70,60,50,60,.5,'capability_gap',:weights,'synthetic-http',:gate,75,60,:policy) RETURNING id
        """), {"t": tenant, "task": task["id"], "weights": json.dumps(default_policy()["weights"]),
                 "policy": json.dumps(default_policy()), "gate": json.dumps([{"status": "blocked", "signals": [
                     {"factor": "capability", "required": "packaging/ista_3a", "status": "blocked",
                      "reason": "合成样本：企业确认不具备"}]}])})
        watch = await session.scalar(text("""
            INSERT INTO competitor_watch_targets(
              tenant_id,dataset_id,platform,market_country,asin,title,status,created_by
            ) VALUES(
              :t,:dataset,'amazon','US','HTTP-ASIN-001','合成竞品椅','active',:u
            ) RETURNING id
        """), {"t": tenant, "dataset": dataset, "u": user})
        alert = await session.scalar(text("""
            INSERT INTO competitor_change_alerts(
              tenant_id,watch_id,asin,change_type,summary,before_value,after_value,captured_at
            ) VALUES(
              :t,:watch,'HTTP-ASIN-001','price','合成竞品价格下调',
              CAST(:before_value AS jsonb),CAST(:after_value AS jsonb),
              CURRENT_TIMESTAMP
            ) RETURNING id
        """), {
            "t": tenant,
            "watch": watch,
            "before_value": json.dumps({"sale_price": 219, "currency": "USD"}),
            "after_value": json.dumps({"sale_price": 199, "currency": "USD"}),
        })
        cluster = await session.scalar(text("""
            INSERT INTO insight_clusters(
              tenant_id,analysis_job_id,cluster_code,taxonomy_code,name,summary,
              sentiment_distribution,aspect_count,review_count,listing_count,
              mention_rate,importance_score,cluster_confidence,representative_aspect_ids
            ) VALUES(
              :t,:task,'HTTP-CLUSTER-1','comfort','坐感与支撑',
              '合成评论需求主题，不代表真实市场结论',
              CAST(:distribution AS jsonb),2,2,1,.5,70,.8,
              CAST(:representatives AS jsonb)
            ) RETURNING id
        """), {
            "t": tenant,
            "task": task["id"],
            "distribution": json.dumps({
                "positive": 1,
                "negative": 1,
                "mixed": 0,
                "neutral": 0,
            }),
            "representatives": json.dumps([1]),
        })
        identities[0].update(
            opportunity_id=opportunity,
            dataset_id=dataset,
            alert_id=alert,
            cluster_id=cluster,
        )
        await session.commit()
    await db.close()
    return identities


@pytest.mark.skipif(not os.getenv("FURNISCOPE_TEST_DATABASE_URL"), reason="needs isolated PostgreSQL")
def test_http_confirm_train_predict_and_cross_tenant_denial(tmp_path):
    settings = fixture_settings(os.environ["FURNISCOPE_TEST_DATABASE_URL"], tmp_path)
    identities = asyncio.run(seed_enterprise(settings))
    with TestClient(create_app(settings)) as client:
        def auth(identity):
            response = client.post("/api/v1/auth/login", json={key: identity[key] for key in ("email", "password")})
            assert response.status_code == 200, response.text
            return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}
        own, other = map(auth, identities)
        def call(method, path, body=None, status=200):
            response = client.request(method, f"/api/v1/{path}", headers=own, json=body)
            assert response.status_code == status, response.text
            return response.json().get("data")
        mapping = call("GET", "forecast/sku-mappings")
        saved_mapping = call("PUT", "forecast/sku-mappings", {
            "expected_revision": mapping["revision"],
            "items": [{"source_sku": "SOURCE-ALT", "product_sku": "SAME-SKU"}]})
        assert len(saved_mapping["items"]) == 1
        assert client.get("/api/v1/forecast/sku-mappings", headers=other).json()["data"]["items"] == []
        call("PUT", "forecast/sku-mappings", {"expected_revision": mapping["revision"], "items": []}, status=409)
        call("PUT", "forecast/sku-mappings", {"expected_revision": saved_mapping["revision"], "items": []})
        overview = call("GET", f"market-intelligence/overview?dataset_id={identities[0]['dataset_id']}")
        assert overview["smart_selection"]["items"][0]["opportunity_id"] == identities[0]["opportunity_id"]
        assert overview["smart_selection"]["total"] == 1
        assert len(call("GET", "market-intelligence/overview")["smart_selection"]["items"]) == 1
        opportunity_page = call(
            "GET",
            f"market-intelligence/opportunities?dataset_id={identities[0]['dataset_id']}"
            "&page=1&page_size=10",
        )
        assert opportunity_page["total"] == 1
        assert opportunity_page["has_next"] is False
        assert opportunity_page["items"][0]["opportunity_id"] == identities[0]["opportunity_id"]
        opportunity_detail = call(
            "GET",
            f"market-intelligence/opportunities/{identities[0]['opportunity_id']}",
        )
        assert opportunity_detail["task_uuid"]
        assert opportunity_detail["dataset_id"] == identities[0]["dataset_id"]
        assert client.get(
            f"/api/v1/market-intelligence/opportunities/{identities[0]['opportunity_id']}",
            headers=other,
        ).status_code == 404
        alert_page = call(
            "GET",
            f"market-intelligence/competitor-alerts?dataset_id={identities[0]['dataset_id']}"
            "&page=1&page_size=10",
        )
        assert alert_page["total"] == 1
        assert alert_page["items"][0]["alert_id"] == identities[0]["alert_id"]
        alert_detail = call(
            "GET",
            f"market-intelligence/competitor-alerts/{identities[0]['alert_id']}",
        )
        assert alert_detail["before_value"]["sale_price"] == 219
        assert client.get(
            f"/api/v1/market-intelligence/competitor-alerts/{identities[0]['alert_id']}",
            headers=other,
        ).status_code == 404
        cluster_page = call(
            "GET",
            f"market-intelligence/review-clusters?dataset_id={identities[0]['dataset_id']}"
            "&page=1&page_size=10",
        )
        assert cluster_page["total"] == 1
        assert cluster_page["items"][0]["cluster_id"] == identities[0]["cluster_id"]
        cluster_detail = call(
            "GET",
            f"market-intelligence/review-clusters/{identities[0]['cluster_id']}",
        )
        assert cluster_detail["name"] == "坐感与支撑"
        assert cluster_detail["evidence"] == []
        assert client.get(
            f"/api/v1/market-intelligence/review-clusters/{identities[0]['cluster_id']}",
            headers=other,
        ).status_code == 404
        call("POST", "market-intelligence/pricing", {
            "dataset_id": identities[0]["dataset_id"],
            "unit_cost": 0,
            "target_margin": 0.35,
            "promo_margin_floor": 0.2,
            "max_discount_rate": 0.08,
        }, status=422)
        current = call("GET", "enterprise/opportunity-policy")["current"]
        saved = call("PUT", "enterprise/opportunity-policy", {
            **{key: current[key] for key in ("name", "objective", "weights", "fit_strength", "required_capabilities")},
            "expected_version": current["version"]})
        assert saved["version"] == 1
        assert client.get("/api/v1/enterprise/opportunity-policy", headers=other).json()["data"]["current"]["version"] == 0
        uploaded = client.post("/api/v1/forecast/data-imports", headers=own,
            files={"file": ("sales.json", json.dumps(sales_records()).encode(), "application/json")})
        assert uploaded.status_code == 200, uploaded.text
        data = uploaded.json()["data"]; version = data["version_uuid"]
        assert client.get(f"/api/v1/forecast/data-imports/{version}", headers=other).status_code == 404
        preview = call("POST", f"forecast/data-imports/{version}/preflight", {"rules": data["rules"]})
        call("POST", f"forecast/data-imports/{version}/confirm", {"preview_sha256": "0" * 64}, status=409)
        confirmed = call("POST", f"forecast/data-imports/{version}/confirm", {"preview_sha256": preview["preview_sha256"]})
        assert confirmed["status"] == "confirmed"
        assert client.get(f"/api/v1/forecast/data-imports/{version}/audit", headers=other).status_code == 404
        audit = client.get(f"/api/v1/forecast/data-imports/{version}/audit", headers=own).json()
        assert audit["records"][0]["source_rows"] == [2]
        body = {"data_version_uuid": version, "mode": "initial"}
        assert call("POST", "forecast/training-preview", body)["merged_rows"] == 196
        idem = {**own, "Idempotency-Key": uuid4().hex}
        response = client.post("/api/v1/forecast/training-runs", headers=idem, json=body)
        assert response.status_code == 202, response.text
        run_id = response.json()["data"]["training_uuid"]
        result = call("GET", f"forecast/training-runs/{run_id}")
        assert result["status"] == "succeeded", result
        assert client.post("/api/v1/forecast/training-runs", headers=idem, json=body).status_code == 200
        assert client.get(f"/api/v1/forecast/training-runs/{run_id}", headers=other).status_code == 404
        assert call("GET", "forecast/status")["ready"]
        catalog = call("GET", "forecast/skus")["items"]
        assert any(row["sku"] == "SAME-SKU" and row["model_eligible"] for row in catalog)
        response = client.post("/api/v1/forecast-jobs", headers={**own, "Idempotency-Key": uuid4().hex}, json={
            "job_name": "合成预测", "product_id": identities[0]["product_id"], "skus": ["SAME-SKU"],
            "sites": ["US"], "granularity": "day", "horizon": 7, "scenario": {}})
        assert response.status_code == 201, response.text
        job = response.json()["data"]["job_uuid"]
        response = client.post(f"/api/v1/forecast-jobs/{job}:start", headers={**own, "Idempotency-Key": uuid4().hex})
        assert response.status_code == 202, response.text
        assert call("GET", f"forecast-jobs/{job}/result")["metrics"]["total_forecast"] == 70
        opportunity = identities[0]["opportunity_id"]
        feedback = {"expected_revision": 0, "status": "rejected", "reason": "合成测试：包装能力不满足"}
        saved = call("PUT", f"enterprise/opportunities/{opportunity}/feedback", feedback)
        assert saved["current"]["revision"] == 1
        call("PUT", f"enterprise/opportunities/{opportunity}/feedback", feedback, status=409)
        assert client.put(f"/api/v1/enterprise/opportunities/{opportunity}/feedback", headers=other, json=feedback).status_code == 404
        assert len(call("GET", "enterprise/opportunity-feedback/export")["items"]) == 1
