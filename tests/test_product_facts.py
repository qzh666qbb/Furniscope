"""Synthetic engineering coverage for evidence -> review -> enterprise gating."""
import asyncio
from io import BytesIO
import os
from uuid import uuid4

from fastapi.testclient import TestClient
from openpyxl import Workbook
from pydantic import SecretStr
import pytest

from furniscope_agent.enterprise_decision import enterprise_fit
from furniscope_agent.product_facts import fact_suggestions, validate_codes
from furniscope_api.app import create_app
from furniscope_api.services.model_router_client import ServiceModelRouterClient
from test_enterprise_http import fixture_settings, seed_enterprise


def test_aliases_do_not_infer_certification_from_negation_or_generic_material():
    suggested = fact_suggestions([{"attribute_code": "material", "attribute_value": "实木框架；未取得FSC；CARB 待认证；flat-pack；metallic finish"}])
    assert {(s["attribute_code"], s["code"]) for s in suggested} == {
        ("material_codes", "solid_wood"), ("packaging_codes", "flat_pack")}
    assert all(s["status"] == "suggested" and s["evidence_text"] for s in suggested)
    for value in ["solid_wood", ["mystery"], ["solid_wood", "solid_wood"], [{}]]:
        with pytest.raises(ValueError):
            validate_codes("material_codes", value)


def test_legacy_model_auto_confirmation_is_not_human_review():
    fact = {"value": ["ista_3a"], "confirmation_status": "confirmed", "source_type": "document"}
    snapshot = {"capabilities": [{"capability_type": "packaging", "capability_code": "ista_3a",
                                  "source_type": "confirmed_user", "availability": "no"}],
                "product_facts": {"packaging_codes": fact}}
    def gate():
        return enterprise_fit(snapshot, category_code="sofa", market_country="US", taxonomy_code="packaging")[2]
    assert gate()["status"] == "unknown"
    fact["source_locator"] = {"confirmed_by": 9}
    assert gate()["status"] == "blocked"
    fact["value"] = ["invented_code"]
    assert gate()["status"] == "unknown"


@pytest.mark.skipif(not os.getenv("FURNISCOPE_TEST_DATABASE_URL"), reason="needs isolated PostgreSQL")
def test_http_fact_review_parse_conflict_history_and_gate(tmp_path, monkeypatch):
    settings = fixture_settings(os.environ["FURNISCOPE_TEST_DATABASE_URL"], tmp_path).model_copy(
        update={"product_parse_mode": "model", "aliyun_model_router_api_key": SecretStr("synthetic-not-used")})
    identities = asyncio.run(seed_enterprise(settings))

    async def extracted(_self, *, output_type, **kwargs):
        material = "实木框架" if "实木框架" in kwargs["messages"][-1]["content"] else "金属"
        return output_type.model_validate({"attributes": [
            {"attribute_code": "material", "value": material, "confidence": .99, "evidence_text": material},
            {"attribute_code": "packaging", "value": "ISTA 3A", "confidence": 1, "evidence_text": "ISTA 3A"},
            {"attribute_code": "certifications", "value": "FSC", "confidence": 1, "evidence_text": "not in uploaded source"},
        ]})
    monkeypatch.setattr(ServiceModelRouterClient, "structured", extracted)
    with TestClient(create_app(settings)) as client:
        def login(identity):
            response = client.post("/api/v1/auth/login", json={key: identity[key] for key in ("email", "password")})
            assert response.status_code == 200, response.text
            return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}
        own, other = map(login, identities)
        path = f"/api/v1/products/{identities[0]['product_id']}"
        assert client.get("/api/v1/enterprise/fact-vocabulary", headers=own).json()["data"]["version"] == "furniture-facts-v1"
        assert client.get(path, headers=other).status_code == 404

        def read():
            response = client.get(path, headers=own)
            assert response.status_code == 200
            return response.json()["data"], response.headers["etag"]

        def attr(code, value, source="user_input", status="confirmed"):
            return {"attribute_code": code, "attribute_value": value, "source_type": source,
                    "confirmation_status": status, "confidence": 1}

        def patch(attributes, status=200, etag=None):
            etag = etag or read()[1]
            response = client.patch(path, headers={**own, "If-Match": etag}, json={"attributes": attributes})
            assert response.status_code == status, response.text
            return response

        def confirm(status=200, profile=None):
            data, etag = read()
            headers = {**own, "If-Match": etag, "Idempotency-Key": uuid4().hex}
            body = {"profile_version_id": profile or data["profile_version_id"],
                    "confirmed_attribute_codes": [a["attribute_code"] for a in data["attributes"]]}
            response = client.post(path + "/profile:confirm", headers=headers, json=body)
            assert response.status_code == status, response.text
            if status == 200:
                replay = client.post(path + "/profile:confirm", headers=headers, json=body)
                assert replay.json() == response.json()
            return response

        first_etag = read()[1]
        patch([attr("material", "实木框架"), attr("moq", 20)])
        patch([attr("material_codes", ["solid_wood"])], etag=first_etag, status=409)
        patch([attr("material_codes", "solid_wood")], status=422)
        patch([attr("material_codes", ["solid_wood"], source="document")], status=422)
        confirm()
        first_profile = read()[0]["profile_version_id"]
        confirm(status=422)  # Already-confirmed version cannot be mutated.

        workbook = Workbook()
        workbook.active.append(["材质", "金属"])
        workbook.active.append(["包装", "ISTA 3A"])
        content = BytesIO()
        workbook.save(content)
        workbook.active.cell(1, 2, "实木框架")
        second = BytesIO()
        workbook.save(second)
        response = client.post(path + "/assets:parse", headers={**own, "Idempotency-Key": uuid4().hex},
            data={"source_type": "document"},
            files=[("files", (name, stream.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))
                   for name, stream in [("facts.xlsx", content), ("second.xlsx", second)]])
        assert response.status_code == 202, response.text
        data, _ = read()
        attributes = {a["attribute_code"]: a for a in data["attributes"]}
        assert attributes["material"]["attribute_value"] == "实木框架"
        assert attributes["material"]["confirmation_status"] == "conflicted"
        assert len(attributes["material"]["source_locator"]["conflicting_source_locator"]["conflicting_observations"]) == 2
        assert attributes["packaging"]["confirmation_status"] == "unconfirmed"
        assert attributes["moq"]["attribute_value"] == 20
        assert "certifications" not in attributes
        assert any(s["code"] == "ista_3a" for s in data["fact_suggestions"])
        confirm(status=422)  # Bulk confirmation cannot conceal conflicts.

        patch([attr("material", "实木框架"), attr("material_codes", ["solid_wood"]),
               attr("packaging_codes", ["ista_3a"])])
        confirm(status=409, profile=first_profile)
        data, _ = read()
        assert data["profile_status"] == "draft"
        confirm()
        data, _ = read()
        assert data["profile_status"] == "confirmed" and data["analysis_status"] == "ready"
        attrs = {a["attribute_code"]: a for a in data["attributes"]}
        assert attrs["packaging"]["source_locator"]["confirmed_by"] == identities[0]["user_id"]
        assert attrs["packaging_codes"]["source_locator"]["vocabulary_version"] == "furniture-facts-v1"
        assert attrs["packaging"]["source_locator"]["evidence_text"] == "ISTA 3A"
        old = client.get(path, headers=own, params={"profile_version_id": first_profile}).json()["data"]
        assert old["profile_status"] == "confirmed"
        assert {a["attribute_code"] for a in old["attributes"]} == {"material", "moq"}
        snapshot = {"product_facts": {k: {**a, "value": a["attribute_value"]} for k, a in attrs.items()},
                    "capabilities": [{"capability_type": "packaging", "capability_code": "ista_3a",
                                      "availability": "no", "source_type": "confirmed_user"}]}
        assert enterprise_fit(snapshot, category_code="sofa", market_country="US", taxonomy_code="packaging")[2]["blocked"]
        assert client.patch(path, headers={**other, "If-Match": read()[1]},
                            json={"attributes": [attr("material_codes", [])]}).status_code == 404
