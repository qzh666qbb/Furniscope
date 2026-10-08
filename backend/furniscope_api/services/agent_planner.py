"""Deterministic first-party Skill catalog and plan compiler."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from ..errors import BusinessError


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


TOOL_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "capability_id": "product.read",
        "display_name": "读取产品档案",
        "description": "读取租户内产品主档、当前画像和确认状态。",
        "provider": "ProductService",
        "effect_level": "R1",
        "permission_code": "product.read",
    },
    {
        "capability_id": "market.dataset.read",
        "display_name": "读取授权市场数据",
        "description": "读取与产品品类、目标市场匹配的已就绪市场数据集。",
        "provider": "DatasetRepository",
        "effect_level": "R1",
        "permission_code": "dataset.read",
    },
    {
        "capability_id": "market_analysis.run",
        "display_name": "运行市场进入评估",
        "description": "创建并运行现有五阶段分析任务，不复制领域算法。",
        "provider": "AnalysisTaskService",
        "effect_level": "R2",
        "permission_code": "analysis.execute",
    },
    {
        "capability_id": "report.verify",
        "display_name": "验证决策报告",
        "description": "校验报告标识、证据和冻结数据范围。",
        "provider": "AnalysisTaskService",
        "effect_level": "R1",
        "permission_code": "analysis.read",
    },
    {
        "capability_id": "business.query",
        "display_name": "执行受控经营问数",
        "description": "按固定指标目录查询已确认销量事实。",
        "provider": "DataQueryService",
        "effect_level": "R1",
        "permission_code": "dataset.read",
    },
    {
        "capability_id": "forecast.status.read",
        "display_name": "检查预测模型状态",
        "description": "读取当前租户已发布预测模型是否可用。",
        "provider": "ForecastRepository",
        "effect_level": "R1",
        "permission_code": "forecast.read",
    },
    {
        "capability_id": "sales_review.compose",
        "display_name": "编制销量周报",
        "description": "基于受控问数结果编制结构化周报，不把历史聚合冒充预测。",
        "provider": "AgentArtifactService",
        "effect_level": "R2",
        "permission_code": "analysis.execute",
    },
    {
        "capability_id": "forecast.data.inspect",
        "display_name": "检查标准数据",
        "description": "读取最近已确认数据版本、质量报告和标准 SHA。",
        "provider": "ForecastDataService",
        "effect_level": "R1",
        "permission_code": "dataset.read",
    },
    {
        "capability_id": "sku.mapping.inspect",
        "display_name": "检查 SKU 身份关联",
        "description": "检查源 SKU 与产品中心标准 SKU 的一一对应状态。",
        "provider": "ForecastCatalogService",
        "effect_level": "R1",
        "permission_code": "product.read",
    },
    {
        "capability_id": "readiness.verify",
        "display_name": "验证数据就绪条件",
        "description": "汇总数据质量与 SKU 阻断项，不自动确认或发布。",
        "provider": "AgentVerifier",
        "effect_level": "R0",
        "permission_code": "dataset.read",
    },
    {
        "capability_id": "artifact.deliver",
        "display_name": "登记交付物",
        "description": "保存带内容哈希和业务深链的可核验交付物。",
        "provider": "AgentArtifactService",
        "effect_level": "R2",
        "permission_code": "analysis.execute",
    },
)

SKILL_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "skill_id": "market_entry_assessment",
        "version": 1,
        "display_name": "市场进入评估",
        "description": "读取产品与授权市场数据，运行五阶段分析并交付可追溯决策报告。",
        "deliverable_label": "决策报告",
        "patterns": ("市场", "进入", "选品", "机会", "竞品", "产品评估", "生成报告"),
        "expected_deliverables": [{"type": "decision_report", "required": True}],
        "acceptance_criteria": [
            "report_uuid_present",
            "evidence_count_gte_1",
            "source_versions_frozen",
        ],
        "steps": (
            ("读取产品与已确认事实", "product.read"),
            ("选择授权市场数据", "market.dataset.read"),
            ("运行五阶段市场分析", "market_analysis.run"),
            ("验证报告与证据", "report.verify"),
            ("交付决策报告", "artifact.deliver"),
        ),
    },
    {
        "skill_id": "weekly_sales_review",
        "version": 1,
        "display_name": "每周销量复盘",
        "description": "冻结最近销量范围，执行受控问数，核对预测可用性并交付周报。",
        "deliverable_label": "销量周报",
        "patterns": ("销量", "销售", "周报", "复盘", "经营数据", "营收"),
        "expected_deliverables": [{"type": "sales_review", "required": True}],
        "acceptance_criteria": [
            "data_version_sha_present",
            "query_receipt_present",
            "forecast_status_explicit",
        ],
        "steps": (
            ("冻结最近销量数据范围", "business.query"),
            ("核对预测模型可用性", "forecast.status.read"),
            ("编制可追溯销量周报", "sales_review.compose"),
            ("交付销量周报", "artifact.deliver"),
        ),
    },
    {
        "skill_id": "data_readiness_check",
        "version": 1,
        "display_name": "数据就绪检查",
        "description": "检查标准数据质量与 SKU 身份关联，交付阻断项和修复入口。",
        "deliverable_label": "数据就绪清单",
        "patterns": (
            "数据就绪",
            "训练条件",
            "训练前",
            "数据质量",
            "检查",
            "清洗",
            "导入",
            "sku",
            "映射",
        ),
        "expected_deliverables": [{"type": "data_readiness_report", "required": True}],
        "acceptance_criteria": [
            "data_version_sha_present",
            "sku_mapping_status_present",
            "blocking_issues_explicit",
        ],
        "steps": (
            ("读取已确认标准数据", "forecast.data.inspect"),
            ("检查 SKU 身份关联", "sku.mapping.inspect"),
            ("汇总阻断问题", "readiness.verify"),
            ("交付修复清单", "artifact.deliver"),
        ),
    },
)


def tool_catalog() -> list[dict[str, Any]]:
    return [{**item, "version": 1, "status": "active"} for item in TOOL_CATALOG]


def tool_definition(capability_id: str) -> dict[str, Any]:
    for tool in TOOL_CATALOG:
        if tool["capability_id"] == capability_id:
            return dict(tool)
    raise BusinessError(
        "AGENT_CAPABILITY_UNAVAILABLE",
        f"执行能力不可用：{capability_id}",
        status_code=422,
    )


def skill_catalog() -> list[dict[str, Any]]:
    return [
        {
            "skill_id": item["skill_id"],
            "version": item["version"],
            "display_name": item["display_name"],
            "description": item["description"],
            "deliverable_label": item["deliverable_label"],
            "status": "active",
            "step_count": len(item["steps"]),
        }
        for item in SKILL_CATALOG
    ]


def skill_definition(skill_id: str) -> dict[str, Any]:
    normalized = skill_id.split("@", 1)[0]
    for skill in SKILL_CATALOG:
        if skill["skill_id"] == normalized:
            return dict(skill)
    raise BusinessError("AGENT_SKILL_NOT_FOUND", "指定的 AI 员工技能不可用", status_code=422)


def match_skill(objective: str, preferred_skill_id: str | None = None) -> dict[str, Any]:
    if preferred_skill_id:
        return skill_definition(preferred_skill_id)
    normalized = objective.lower()
    scores = [
        (
            sum(1 for pattern in skill["patterns"] if re.search(re.escape(pattern), normalized, re.I)),
            -index,
            skill,
        )
        for index, skill in enumerate(SKILL_CATALOG)
    ]
    return dict(max(scores, key=lambda item: (item[0], item[1]))[2])


def compile_plan(skill_id: str, constraints: dict[str, Any]) -> dict[str, Any]:
    skill = skill_definition(skill_id)
    tools = {item["capability_id"]: item for item in TOOL_CATALOG}
    steps: list[dict[str, Any]] = []
    for index, (title, capability_id) in enumerate(skill["steps"], start=1):
        tool = tools[capability_id]
        steps.append({
            "ordinal": index,
            "title": title,
            "capability_id": capability_id,
            "capability_version": 1,
            "provider": tool["provider"],
            "effect_level": tool["effect_level"],
            "depends_on_ordinals": [] if index == 1 else [index - 1],
            "input_refs": {"goal_constraints": constraints},
            "output_schema": {"type": "object"},
            "verifier_config": {"required": True, "mode": "deterministic"},
            "requires_approval": tool["effect_level"] == "R3",
        })
    material = {
        "skill_id": skill["skill_id"],
        "skill_version": skill["version"],
        "constraints": constraints,
        "steps": steps,
    }
    return {
        **material,
        "plan_sha256": _canonical_sha256(material),
        "risk_summary": {
            "highest_effect": max(step["effect_level"] for step in steps),
            "requires_approval": any(step["requires_approval"] for step in steps),
            "tool_count": len(steps),
        },
    }


def skill_package_rows() -> list[dict[str, Any]]:
    rows = []
    for skill in SKILL_CATALOG:
        template = compile_plan(skill["skill_id"], {})
        rows.append({
            "skill_id": skill["skill_id"],
            "version": skill["version"],
            "display_name": skill["display_name"],
            "description": skill["description"],
            "objective_patterns": list(skill["patterns"]),
            "plan_template": template,
            "content_sha256": _canonical_sha256({
                "skill_id": skill["skill_id"],
                "version": skill["version"],
                "steps": skill["steps"],
            }),
        })
    return rows
