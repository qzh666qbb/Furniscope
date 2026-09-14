"""Intent-aware answers for analysis-task chat when the model router is unavailable."""

from __future__ import annotations

import re
from typing import Any


def _text(value: Any) -> str:
    return str(value or "").strip()


def is_forecast_question(question: str) -> bool:
    text = question or ""
    if re.search(r"机会评分|市场机会|出海机会", text):
        return False
    return bool(re.search(r"销量预测|未来销量|销售数据|预测销量|可以进行预测|销量对话|forecast", text, re.I))


def local_task_chat_answer(question: str, *, result: Any, projections: dict[str, Any]) -> dict[str, Any]:
    text = question or ""
    clusters = list(projections.get("clusters") or [])
    opportunities = list(projections.get("opportunities") or [])
    recommendations = list(projections.get("recommendations") or [])
    summary = {}
    if isinstance(result, dict):
        summary = result.get("report_summary") or {}
    elif result is not None:
        summary = getattr(result, "report_summary", None) or {}
        if hasattr(summary, "model_dump"):
            summary = summary.model_dump()

    if is_forecast_question(text):
        return {
            "answer": (
                "当前对话绑定的是市场洞察分析任务，依据来自商品与评论，没有接入订单训练数据和销量预测模型。"
                "机会分、评论痛点不能当作未来销量。要看未来销量，请打开「销量预测」，选择同一 SKU 并运行预测。"
            ),
            "evidence_refs": [],
            "suggested_action": "forecast",
        }

    if re.search(r"痛点|评论|舆情", text) and clusters:
        parts = []
        refs = []
        for item in clusters[:3]:
            name = _text(item.get("cluster_name") if isinstance(item, dict) else getattr(item, "cluster_name", ""))
            summary_text = _text(item.get("summary") if isinstance(item, dict) else getattr(item, "summary", ""))
            cluster_id = item.get("cluster_id") if isinstance(item, dict) else getattr(item, "cluster_id", None)
            parts.append(f"{name}：{summary_text}" if summary_text else name)
            if cluster_id:
                refs.append(str(cluster_id))
        return {
            "answer": "根据本任务已落库的评论聚类：" + "；".join(parts) + "。原始摘录可在节点详情核验。",
            "evidence_refs": refs,
        }

    if re.search(r"评分|得分|依据|风险", text) and opportunities:
        item = opportunities[0]
        get = item.get if isinstance(item, dict) else lambda key, default=None: getattr(item, key, default)
        oid = get("opportunity_id")
        return {
            "answer": (
                f"市场机会评分主要依据：需求热度 {get('demand_heat_score')}、增长趋势 {get('demand_growth_score')}、"
                f"未满足度 {get('unmet_need_score')}、竞争空间 {get('competition_space_score')}、"
                f"利润空间 {get('profit_space_score')}。综合机会分见报告预览；风险来自竞争空间与未满足需求是否足以支撑改款投入。"
            ),
            "evidence_refs": [str(oid)] if oid else [],
        }

    if re.search(r"改款|建议|成本", text) and recommendations:
        parts = []
        refs = []
        for item in recommendations[:3]:
            get = item.get if isinstance(item, dict) else lambda key, default=None: getattr(item, key, default)
            action = _text(get("recommended_action"))
            low, high = get("cost_impact_min"), get("cost_impact_max")
            currency = get("cost_currency") or "USD"
            cost = f"（成本 {currency} {low}–{high}）" if low is not None and high is not None else ""
            parts.append(f"{action}{cost}")
            rid = get("recommendation_id")
            if rid:
                refs.append(str(rid))
        return {"answer": "优先级最高的改款建议：" + "；".join(parts) + "。", "evidence_refs": refs}

    if summary.get("executive_summary") and re.search(r"总结|报告|机会|结论", text):
        score = summary.get("overall_opportunity_score")
        conf = summary.get("overall_confidence")
        conf_text = f"{round(float(conf) * 100)}%" if conf is not None else "—"
        return {
            "answer": f"{summary.get('executive_summary')}（综合机会分 {score}，置信度 {conf_text}）。",
            "evidence_refs": [],
        }

    return {
        "answer": (
            "这个问题超出当前任务已落库的市场洞察证据。我可以基于本任务回答竞品、价格带、评论痛点、机会评分和改款建议；"
            "未来销量请使用「销量预测」模块，不要把机会分当作销量预测。"
        ),
        "evidence_refs": [],
        "suggested_action": "forecast" if re.search(r"销量|预测", text) else None,
    }
