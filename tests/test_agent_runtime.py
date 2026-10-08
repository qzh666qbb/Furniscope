"""AI employee planning and HTTP contract checks."""

import asyncio
from types import SimpleNamespace

import pytest

from furniscope_api.app import create_app
from furniscope_api.config import ApiSettings
from furniscope_api.errors import BusinessError
from furniscope_api.routes.agent_runtime import _require_agent_runtime_enabled
from furniscope_api.schemas.agent_runtime import GoalDraftRequest
from furniscope_api.services.agent_goal_service import AgentGoalService
from furniscope_api.services.agent_planner import (
    compile_plan,
    match_skill,
    skill_catalog,
    tool_catalog,
)
from furniscope_api.services.agent_supervisor import AgentSupervisor


def test_skill_matching_and_plan_are_deterministic():
    assert match_skill("检查训练前 SKU 映射和数据质量")["skill_id"] == "data_readiness_check"
    assert match_skill("复盘最近四周销量")["skill_id"] == "weekly_sales_review"
    assert match_skill("评估 HF-A0590 进入美国市场")["skill_id"] == "market_entry_assessment"
    first = compile_plan("weekly_sales_review", {"period_days": 28})
    repeated = compile_plan("weekly_sales_review@1", {"period_days": 28})
    assert first["plan_sha256"] == repeated["plan_sha256"]
    assert [step["ordinal"] for step in first["steps"]] == [1, 2, 3, 4]
    assert first["risk_summary"] == {
        "highest_effect": "R2",
        "requires_approval": False,
        "tool_count": 4,
    }


def test_first_party_catalog_has_no_uncontrolled_side_effects():
    assert {item["skill_id"] for item in skill_catalog()} == {
        "market_entry_assessment",
        "weekly_sales_review",
        "data_readiness_check",
    }
    assert all(item["effect_level"] in {"R0", "R1", "R2"} for item in tool_catalog())
    assert all(item["permission_code"] for item in tool_catalog())


def test_goal_draft_resolves_real_resources_without_fabricating(monkeypatch):
    service = AgentGoalService()

    async def ensure_catalog(_session, *, tenant_id, user_id):
        assert tenant_id == 11 and user_id == 7
        return {
            "autonomy_level": "L2",
            "default_budget": {"max_tool_calls": 20, "max_replans": 1},
        }

    async def resolve_resources(_session, *, tenant_id, objective, constraints, skill_id):
        assert tenant_id == 11
        assert skill_id == "data_readiness_check"
        return (
            {**constraints, "data_version_uuid": "10000000-0000-4000-8000-000000000001"},
            [{
                "type": "forecast_data_version",
                "id": "10000000-0000-4000-8000-000000000001",
                "label": "sales.xlsx",
                "status": "confirmed",
            }],
            [],
        )

    monkeypatch.setattr(service.repository, "ensure_catalog", ensure_catalog)
    monkeypatch.setattr(service.repository, "resolve_resources", resolve_resources)
    draft = asyncio.run(
        service.draft(
            object(),
            tenant_id=11,
            user_id=7,
            body=GoalDraftRequest(objective="检查训练前数据是否就绪"),
        )
    )
    assert draft["selected_skill_id"] == "data_readiness_check@1"
    assert draft["resolved_resources"][0]["status"] == "confirmed"
    assert draft["validation_issues"] == []
    assert draft["autonomy_envelope"]["allowed_effect_levels"] == ["R0", "R1", "R2"]


def test_goal_budget_cannot_exceed_profile_or_underfund_plan():
    profile = {
        "autonomy_level": "L2",
        "default_budget": {"max_tool_calls": 4, "max_replans": 1},
    }
    envelope = AgentGoalService._merge_autonomy_envelope(
        profile,
        {"max_tool_calls": 99, "max_replans": 99},
    )

    assert envelope["max_tool_calls"] == 4
    assert envelope["max_replans"] == 1
    with pytest.raises(BusinessError, match="超过当前上限") as raised:
        AgentGoalService._assert_plan_within_budget(
            tool_count=5,
            autonomy_envelope=envelope,
        )
    assert raised.value.code == "AGENT_TOOL_BUDGET_EXCEEDED"


def test_supervisor_rechecks_permission_and_reserves_budget(monkeypatch):
    supervisor = AgentSupervisor(ApiSettings(_env_file=None, app_env="test"))
    context = {
        "tenant_id": 11,
        "goal_created_by": 7,
        "id": 19,
        "budget": {"max_tool_calls": 1},
        "tool_call_count": 0,
    }
    step = {"capability_id": "product.read"}
    calls = {"handler": 0, "reserve": 0}

    async def denied(*_args, **_kwargs):
        return False

    async def reserve(*_args, **_kwargs):
        calls["reserve"] += 1
        return 1

    async def handler(*_args, **_kwargs):
        calls["handler"] += 1
        return {"ok": True}

    monkeypatch.setattr(supervisor.repository, "has_effective_permission", denied)
    monkeypatch.setattr(supervisor.repository, "reserve_tool_call", reserve)
    monkeypatch.setattr(supervisor, "_capability_product_read", handler)

    async def permission_scenario():
        with pytest.raises(BusinessError, match="执行权限已撤销") as raised:
            await supervisor._execute_capability(
                object(),
                context=context,
                step=step,
                outputs={},
            )
        assert raised.value.code == "AGENT_CAPABILITY_PERMISSION_REVOKED"

    asyncio.run(permission_scenario())
    assert calls == {"handler": 0, "reserve": 0}

    async def allowed(*_args, **_kwargs):
        return True

    async def exhausted(*_args, **_kwargs):
        calls["reserve"] += 1
        return None

    monkeypatch.setattr(supervisor.repository, "has_effective_permission", allowed)
    monkeypatch.setattr(supervisor.repository, "reserve_tool_call", exhausted)

    async def budget_scenario():
        with pytest.raises(BusinessError, match="预算上限") as raised:
            await supervisor._execute_capability(
                object(),
                context=context,
                step=step,
                outputs={},
            )
        assert raised.value.code == "AGENT_TOOL_BUDGET_EXCEEDED"

    asyncio.run(budget_scenario())
    assert calls == {"handler": 0, "reserve": 1}

    monkeypatch.setattr(supervisor.repository, "reserve_tool_call", reserve)
    result = asyncio.run(
        supervisor._execute_capability(
            object(),
            context=context,
            step=step,
            outputs={},
        )
    )
    assert result == {"ok": True}
    assert context["tool_call_count"] == 1
    assert calls == {"handler": 1, "reserve": 2}


def test_agent_runtime_routes_are_registered():
    app = create_app(ApiSettings(_env_file=None, app_env="test"))
    paths = set(app.openapi()["paths"])
    assert {
        "/api/v1/agent-runtime/overview",
        "/api/v1/agent-runtime/goals:draft",
        "/api/v1/agent-runtime/goals",
        "/api/v1/agent-runtime/goals/{goal_uuid}",
        "/api/v1/agent-runtime/goals/{goal_uuid}/start",
        "/api/v1/agent-runtime/approvals/{approval_uuid}:respond",
        "/api/v1/agent-runtime/artifacts",
        "/api/v1/agent-runtime/skills",
        "/api/v1/agent-runtime/capabilities",
    } <= paths


def test_agent_runtime_execution_requires_explicit_feature_enablement():
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=ApiSettings(_env_file=None, app_env="test"),
            ),
        ),
    )
    with pytest.raises(BusinessError, match="本期未开放") as raised:
        _require_agent_runtime_enabled(request)
    assert raised.value.code == "AGENT_RUNTIME_DISABLED"
    assert raised.value.status_code == 409

    request.app.state.settings = request.app.state.settings.model_copy(
        update={"agent_runtime_enabled": True},
    )
    _require_agent_runtime_enabled(request)
