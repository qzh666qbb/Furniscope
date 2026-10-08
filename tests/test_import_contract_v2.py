"""Synthetic order ledgers, exact reconciliation, immutable tenant templates."""
import asyncio
import json
import os

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from furniscope_api.app import create_app
from furniscope_api.database import Database, bind_tenant_session
from furniscope_api.schemas.data_imports import ImportRules
from furniscope_api.services.data_reconciliation import reconcile
from furniscope_api.services.sales_data_cleaner import clean_table
from test_enterprise_http import fixture_settings, seed_enterprise
from test_enterprise_training import rules, sales_records


def order_rules(**patch):
    return ImportRules(**{
        "mapping": {key: key for key in ["date", "sku", "warehouse", "sales",
                    "order_id", "line_id", "order_status", "refunded_units", "unit_price", "discount"]},
        "grain": "transactions", "sales_basis": "net_units", "order_semantics_confirmed": True,
        "order_statuses": {"成交": "completed", "取消": "cancelled", "部分退款": "refunded"},
        "warehouse_sites": {"美西仓": "US"}, "duplicate_orders": "drop_identical",
        "default_currency": "USD", "price_semantics_confirmed": True, **patch})


def orders():
    row = {"date": "2025-01-06", "sku": "SAME-SKU", "warehouse": "美西仓", "sales": 10,
           "order_id": "O1", "line_id": "L1", "order_status": "成交", "refunded_units": 0,
           "unit_price": 100, "discount": 0.1}
    return [row, dict(row), {**row, "order_id": "O2", "sales": 4, "order_status": "取消"},
            {**row, "order_id": "O3", "sales": 5, "order_status": "部分退款",
             "refunded_units": 2, "unit_price": 90}]


def test_order_quantity_reconciliation_and_line_facts():
    result = clean_table(pd.DataFrame(orders()), order_rules())
    quality, record = result["quality"], result["records"][0]
    assert quality["trainable"]
    assert quality["input_quantity_total"] == 29
    assert quality["adjustments"] == {"duplicate_units": 10, "cancelled_units": 4, "refunded_units": 2}
    assert quality["output_quantity_total"] == 13 and quality["quantity_reconciled"]
    assert record["site"] == "US" and record["sales"] == 13
    assert record["unit_price"] is None  # Different prices remain line facts.
    assert record["currency"] == "USD"
    assert record["price_facts"][-1]["unit_price"] == 90
    assert result["row_audit"][1]["action"] == "duplicate_ignored"
    assert result["row_audit"][2]["output_units"] == 0
    gross = clean_table(pd.DataFrame(orders()), order_rules(sales_basis="gross_units"))
    assert gross["records"][0]["sales"] == 15
    assert gross["quality"]["adjustments"]["refunded_units"] == 0


@pytest.mark.parametrize("patch,reason", [
    ({"date": "2025-01-07"}, "内容冲突"), ({"sales": 11}, "内容冲突"),
    ({"order_status": "unknown"}, "订单状态"), ({"warehouse": "欧洲仓"}, "仓库未映射"),
    ({"refunded_units": 20}, "退货件数"), ({"discount": 10}, "折扣"),
    ({"line_id": ""}, "订单号/行号"), ({"unit_price": float("inf")}, "单价"),
])
def test_order_ambiguities_fail_closed(patch, reason):
    source = orders()[:2]
    source[1].update(patch)
    report = clean_table(pd.DataFrame(source), order_rules())["quality"]
    assert not report["can_confirm"] and reason in report["errors"][0]["reason"]


def test_order_semantics_and_currency_are_not_guessed():
    with pytest.raises(ValueError, match="整日订单"):
        order_rules(order_semantics_confirmed=False)
    with pytest.raises(ValueError, match="成交单价"):
        order_rules(price_semantics_confirmed=False)
    source = orders()
    for item in source:
        item["currency"] = "USD"
    source[-1]["currency"] = "EUR"
    mapping = {**order_rules().mapping, "currency": "currency"}
    result = clean_table(pd.DataFrame(source), order_rules(mapping=mapping))
    assert "混合币种" in result["quality"]["errors"][0]["reason"]
    mapping.pop("refunded_units")
    result = clean_table(pd.DataFrame(orders()), order_rules(mapping={k: v for k, v in mapping.items() if k != "currency"}))
    assert "实际退货件数" in result["quality"]["errors"][0]["reason"]
    mapping = {**order_rules().mapping, "site": "site"}
    result = clean_table(pd.DataFrame([{**orders()[0], "site": "UK"}]), order_rules(mapping=mapping))
    assert "站点冲突" in result["quality"]["errors"][0]["reason"]


def test_reconciliation_exact_join_and_no_inventory_backfill():
    base = clean_table(pd.DataFrame(sales_records(3)), rules())
    control = clean_table(pd.DataFrame(sales_records(3, 9)), rules())
    inventory = {"records": [
        {"date": "2025-01-06", "sku": "SAME-SKU", "site": "US", "inventory": 0, "source_rows": [2]},
        {"date": "2025-01-09", "sku": "SAME-SKU", "site": "EU", "inventory": 100, "source_rows": [3]},
    ]}
    reconcile(base, {"control": control, "inventory": inventory})
    assert not base["quality"]["can_confirm"]
    assert base["reconciliation"]["control"]["difference_count"] == 3
    assert base["reconciliation"]["control"]["differences"][0]["difference"] == 1
    assert base["quality"]["reconciliation"]["inventory"]["matched_rows"] == 1
    assert base["quality"]["reconciliation"]["inventory"]["missing_count"] == 2
    assert base["quality"]["reconciliation"]["inventory"]["extra_count"] == 1
    assert "inventory" not in base["records"][1]
    assert any("零库存" in item for item in base["quality"]["training_blockers"])


@pytest.mark.skipif(not os.getenv("FURNISCOPE_TEST_DATABASE_URL"), reason="needs isolated PostgreSQL")
def test_http_template_revisions_reconciliation_and_tenant_boundaries(tmp_path):
    settings = fixture_settings(os.environ["FURNISCOPE_TEST_DATABASE_URL"], tmp_path)
    identities = asyncio.run(seed_enterprise(settings))
    with TestClient(create_app(settings)) as client:
        headers = []
        for identity in identities:
            login = client.post("/api/v1/auth/login", json={k: identity[k] for k in ("email", "password")})
            headers.append({"Authorization": f"Bearer {login.json()['data']['access_token']}"})
        own, other = headers

        def call(method, path, body=None, status=200, auth=own):
            response = client.request(method, "/api/v1/forecast/" + path, json=body, headers=auth)
            assert response.status_code == status, response.text
            return response.json().get("data")

        def upload(name, records, auth=own):
            response = client.post("/api/v1/forecast/data-imports", headers=auth,
                files={"file": (name, json.dumps(records).encode(), "application/json")})
            assert response.status_code == 200, response.text
            return response.json()["data"]["version_uuid"]

        def preflight(version, import_rules, auxiliary=None):
            return call("POST", f"data-imports/{version}/preflight",
                        {"rules": import_rules.model_dump(), "auxiliary_versions": auxiliary or {}})

        def confirm(version, preview):
            return call("POST", f"data-imports/{version}/confirm", {"preview_sha256": preview["preview_sha256"]})

        order = upload("orders.json", orders())
        preview = preflight(order, order_rules())
        confirm(order, preview)
        template_body = {"name": "ERP整日订单", "data_version_uuid": order, "expected_revision": 0}
        saved = call("POST", "import-templates", template_body)
        call("POST", "import-templates", template_body, 409)
        revised = call("POST", "import-templates", {**template_body, "expected_revision": 1})
        assert revised["revision"] == 2
        assert call("GET", "import-templates")["items"][0]["revision"] == 2
        assert call("GET", "import-templates", auth=other)["items"] == []
        call("POST", "import-templates", template_body, 404, other)
        new_order = upload("next.json", [{**row, "date": "2025-01-07"} for row in orders()])
        call("POST", f"data-imports/{new_order}/apply-template", {"template_uuid": saved["template_uuid"]}, 404, other)
        applied = call("POST", f"data-imports/{new_order}/apply-template", {"template_uuid": saved["template_uuid"]})
        assert applied["template_snapshot"]["revision"] == 1
        assert applied["preview_sha256"] is None
        check = preflight(new_order, ImportRules(**applied["rules"]))
        confirm(new_order, check)
        call("POST", f"data-imports/{new_order}/apply-template", {"template_uuid": revised["template_uuid"]}, 409)
        mismatch = upload("other-columns.json", sales_records(1))
        call("POST", f"data-imports/{mismatch}/apply-template", {"template_uuid": saved["template_uuid"]}, 422)

        control = upload("control.json", [{**sales_records(1)[0], "sales": 13}])
        confirm(control, preflight(control, rules(sales_basis="net_units")))
        inventory = upload("inventory.json", [{"date": "2025-01-06", "sku": "SAME-SKU", "site": "US", "inventory": 40}])
        inv_rules = ImportRules(kind="inventory", mapping={k: k for k in ["date", "sku", "site", "inventory"]})
        confirm(inventory, preflight(inventory, inv_rules))
        revision = call("POST", f"data-imports/{order}/revisions")["version_uuid"]
        call("POST", f"data-imports/{revision}/preflight",
             {"rules": order_rules().model_dump(), "auxiliary_versions": {"control": order}}, 422)
        checked = preflight(revision, order_rules(), {"control": control, "inventory": inventory})
        assert checked["quality"]["reconciliation"]["control"]["reconciled"]
        assert checked["sample"][0]["inventory"] == 40
        confirm(revision, checked)
        audit = client.get(f"/api/v1/forecast/data-imports/{revision}/audit", headers=own).json()
        assert audit["auxiliary_sources"]["control"]["sha256"]
        assert audit["reconciliation"]["control"]["difference_count"] == 0
        call("POST", f"data-imports/{revision}/preflight", {"rules": order_rules().model_dump()}, 409)
        bad_control = upload("bad-control.json", [{**sales_records(1)[0], "sales": 12}])
        confirm(bad_control, preflight(bad_control, rules(sales_basis="net_units")))
        revision = call("POST", f"data-imports/{order}/revisions")["version_uuid"]
        failed = preflight(revision, order_rules(), {"control": bad_control})
        assert failed["quality"]["reconciliation"]["control"]["difference_count"] == 1
        call("POST", f"data-imports/{revision}/confirm", {"preview_sha256": failed["preview_sha256"]}, 422)
        call("POST", f"data-imports/{revision}/preflight", {"rules": order_rules().model_dump(), "auxiliary_versions": {"inventory": control}}, 422)
        foreign = upload("foreign.json", sales_records(2), auth=other)
        call("POST", f"data-imports/{revision}/preflight", {"rules": order_rules().model_dump(), "auxiliary_versions": {"control": foreign}}, 404)

        history = [{**orders()[0], "date": row["date"], "order_id": f"DAY-{index}"}
                   for index, row in enumerate(sales_records())]
        version = upload("order-history.json", history)
        confirm(version, preflight(version, order_rules()))
        response = client.post("/api/v1/forecast/training-runs",
            headers={**own, "Idempotency-Key": "order-history"},
            json={"data_version_uuid": version, "mode": "initial"})
        assert response.status_code == 202, response.text
        run = call("GET", "training-runs/" + response.json()["data"]["training_uuid"])
        assert run["status"] == "succeeded", run
        duplicate = upload("order-moved.json", [{**history[0], "date": "2025-07-21"}])
        confirm(duplicate, preflight(duplicate, order_rules()))
        call("POST", "training-preview", {"data_version_uuid": duplicate, "mode": "append"}, 422)

    async def verify_rls():
        db = Database(settings)
        try:
            async with db.session_factory() as session:
                await bind_tenant_session(session, identities[1]["tenant_id"])
                assert await session.scalar(text("SELECT count(*) FROM forecast_import_templates")) == 0
        finally:
            await db.close()
    asyncio.run(verify_rls())
