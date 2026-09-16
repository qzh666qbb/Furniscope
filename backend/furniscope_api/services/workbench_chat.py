"""Workbench chat before an analysis task exists.

Uses the model router when available, otherwise a local fallback that still
checks catalog matches and whether the requested market dataset is ready.
"""

from __future__ import annotations

import json
import re
from typing import Any

MARKET_ALIASES = [
    ("美国", "US"), ("美固", "US"), ("united states", "US"),
    ("英国", "GB"), ("德国", "DE"), ("法国", "FR"),
    ("日本", "JP"), ("加拿大", "CA"), ("澳大利亚", "AU"),
]
MARKET_NAMES = {
    "US": "美国", "GB": "英国", "DE": "德国", "FR": "法国",
    "JP": "日本", "CA": "加拿大", "AU": "澳大利亚",
}
STOPWORDS = (
    r"我想|请|帮我|一下|分析|研究|看看|说说|当前|怎么样|怎样|如何|好不好|情况|"
    r"的市场|市场|竞品|评论|痛点|价格带|出海|机会|工作台|报告|任务|"
    r"美国|美固|英国|德国|法国|日本|加拿大|澳大利亚|"
    r"帮我获取|获取|爬取|采集|导入|在"
)
FURNITURE_HINTS = ("椅", "沙发", "桌", "床", "柜", "灯", "几", "chair", "sofa", "table", "desk")
FETCH_RE = re.compile(r"获取|爬取|采集|导入|补数|抓取|拉一下|要一份")
GAMING_CHAIR_RE = re.compile(r"电竞椅|电竞|游戏椅|电脑椅|gaming\s*chair", re.I)


def parse_market(text: str) -> tuple[str | None, str | None]:
    raw = text or ""
    lowered = raw.lower()
    for label, code in MARKET_ALIASES:
        if label in raw or label.lower() in lowered or f" {code.lower()} " in f" {lowered} ":
            return code, MARKET_NAMES[code]
    return None, None


def product_needles(text: str) -> list[str]:
    cleaned = re.sub(STOPWORDS, " ", text or "", flags=re.I)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
    needles: list[str] = []
    if cleaned:
        needles.append(cleaned)
    if GAMING_CHAIR_RE.search(text or ""):
        needles.extend(["电竞", "游戏椅", "电脑椅", "椅", "chair"])
    if re.search(r"扶手椅|armchair", text or "", re.I):
        needles.append("扶手椅")
    if re.search(r"休闲椅|lounge", text or "", re.I):
        needles.append("休闲椅")
    seen: set[str] = set()
    unique: list[str] = []
    for item in needles:
        if item in seen:
            continue
        if len(item) < 2 and item not in {"椅", "桌", "床", "柜", "灯", "几"}:
            continue
        seen.add(item)
        unique.append(item)
    return unique


def requested_product_label(text: str) -> str:
    needles = product_needles(text)
    for item in needles:
        if any(hint in item for hint in FURNITURE_HINTS):
            return item
    return next((item for item in needles if len(item) >= 2), "该品类")


def find_catalog_candidates(products: list[dict[str, Any]], text: str, limit: int = 5) -> list[dict[str, Any]]:
    needles = product_needles(text)
    if not needles or not products:
        return []
    scored: list[tuple[int, dict[str, Any]]] = []
    for item in products:
        name = str(item.get("name") or "").lower()
        sku = str(item.get("sku") or "").lower()
        category = str(item.get("category_code") or "").lower()
        score = 0
        for needle in needles:
            if name == needle or sku == needle:
                score += 100
            if needle and needle in name:
                score += min(40, len(needle) * 5)
            if needle and needle in sku:
                score += 60
            if needle and needle in category:
                score += 24
        if "椅" in (text or "") and "椅" in name:
            score += 12
        if score >= 12:
            scored.append((score, item))
    scored.sort(key=lambda row: row[0], reverse=True)
    unique: list[dict[str, Any]] = []
    seen: set[Any] = set()
    for _, item in scored:
        key = item.get("product_id") or item.get("sku")
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
        if len(unique) >= limit:
            break
    return unique


def ready_datasets_for(datasets: list[dict[str, Any]], country: str | None) -> list[dict[str, Any]]:
    ready = [item for item in datasets if str(item.get("status") or "") == "ready"]
    if not country:
        return ready
    return [item for item in ready if str(item.get("market_country") or "") == country]


def _product_line(item: dict[str, Any], index: int) -> str:
    name = item.get("name") or "未命名产品"
    sku = item.get("sku") or "无SKU"
    return f"{index}. {name}（{sku}）"


def _run_prompt(item: dict[str, Any], market_label: str) -> str:
    return f"分析{item.get('name')}（{item.get('sku')}）在{market_label}的竞品、价格带和用户评论痛点"


def catalog_context(products: list[dict[str, Any]], datasets: list[dict[str, Any]],
                    selected_product: dict[str, Any] | None, selected_dataset: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "selected_product": (
            {
                "product_id": selected_product.get("product_id"),
                "sku": selected_product.get("sku"),
                "name": selected_product.get("name"),
                "category_code": selected_product.get("category_code"),
            } if selected_product else None
        ),
        "selected_dataset": (
            {
                "dataset_id": selected_dataset.get("dataset_id"),
                "name": selected_dataset.get("name"),
                "market_country": selected_dataset.get("market_country"),
                "status": selected_dataset.get("status"),
            } if selected_dataset else None
        ),
        "products": [
            {
                "product_id": item.get("product_id"),
                "sku": item.get("sku"),
                "name": item.get("name"),
                "category_code": item.get("category_code"),
            }
            for item in products[:40]
        ],
        "datasets": [
            {
                "dataset_id": item.get("dataset_id"),
                "name": item.get("name"),
                "market_country": item.get("market_country"),
                "category_code": item.get("category_code"),
                "status": item.get("status"),
                "listing_count": item.get("listing_count"),
                "valid_review_count": item.get("valid_review_count"),
            }
            for item in datasets[:20]
        ],
    }


def parse_model_chat_text(text: str) -> dict[str, Any] | None:
    content = (text or "").strip()
    if content.startswith("```"):
        start = content.find("\n")
        end = content.rfind("```")
        if start != -1 and end > start:
            content = content[start + 1:end].strip()
    if not content.startswith("{"):
        return None
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def workbench_system_prompt(context: dict[str, Any], *, streaming: bool = False) -> str:
    output_rule = (
        "用中文直接回答用户，不要输出 JSON、不要输出思考标签或 XML。"
        if streaming else
        "输出 JSON：answer(中文)、title(短工作台名)、suggested_prompts(字符串数组，可点选的下一句)、"
        "suggested_action(insights|run|forecast|products 或空)、action_label、action_href、"
        "product_candidates(对象数组：product_id,sku,name)。"
    )
    return (
        "你是 FurniScope 分析工作台助手。只根据给定的企业产品目录和已授权市场数据集回答。"
        "禁止编造竞品、价格、评论痛点或机会分。用户问某个市场时，必须先核对该国是否已有 status=ready 的数据集。"
        "若没有对应市场数据：明确说现在不能判断出海机会，引导去「市场洞察」导入授权数据，或在该页用「获取舆情」采集已授权评论页。"
        "不要声称已经爬取到亚马逊或公开网页；没有授权数据源时不能替用户抓取。"
        "若用户说获取/爬取/导入，且没有给出授权链接：请用户去市场洞察或把授权评论页发给你。"
        "目录有多款相近产品时列出 SKU 请用户点选，不要把整句问话当成产品名。"
        f"{output_rule}企业数据："
        + json.dumps(context, ensure_ascii=False, default=str)
    )


def build_workbench_thinking(
    question: str,
    *,
    products: list[dict[str, Any]],
    datasets: list[dict[str, Any]],
    selected_product: dict[str, Any] | None = None,
    selected_dataset: dict[str, Any] | None = None,
) -> str:
    text = question or ""
    country, market_label = parse_market(text)
    label = requested_product_label(text)
    candidates = find_catalog_candidates(products, text)
    if selected_product and not candidates:
        name = str(selected_product.get("name") or "")
        if any(hint in text for hint in FURNITURE_HINTS if hint in name) or "当前" in text:
            candidates = [selected_product]
    ready = ready_datasets_for(datasets, country) if country else ready_datasets_for(datasets, None)
    steps = ["先理解用户是在问产品、目标市场，还是要补数，而不是把整句当成产品名。"]
    if selected_product:
        steps.append(
            f"工作台已绑定「{selected_product.get('name')}」（{selected_product.get('sku')}）。"
        )
    if country:
        steps.append(f"识别到目标市场：{market_label}（{country}）。")
    else:
        steps.append("问题未点名国家时，只核对企业已授权市场，不编造海外结论。")
    if candidates:
        names = "、".join(
            f"{item.get('name')}（{item.get('sku')}）" for item in candidates[:3]
        )
        steps.append(f"在企业产品目录检索「{label}」，命中：{names}。")
    else:
        steps.append(f"在企业产品目录检索「{label}」，没有完全同名档案。")
    if selected_dataset:
        steps.append(
            f"当前选中数据集「{selected_dataset.get('name')}」，"
            f"状态 {selected_dataset.get('status')}。"
        )
    if country and not ready:
        steps.append(
            f"核对{market_label}市场授权数据：没有 status=ready 的数据集，"
            "因此不能判断竞品、价格带或评论痛点。"
        )
    elif ready:
        first = ready[0]
        steps.append(
            f"核对市场数据：已有就绪数据集「{first.get('name')}」"
            f"（商品 {first.get('listing_count') or 0}、有效评论 {first.get('valid_review_count') or 0}）。"
        )
    else:
        steps.append("当前没有就绪的授权市场数据集。")
    if FETCH_RE.search(text):
        steps.append(
            "用户提到获取/爬取。没有授权数据源时不能替用户抓取公开电商页，应引导去市场洞察导入或采集。"
        )
    steps.append("按目录与数据事实组织回答，禁止编造竞品、痛点或机会分。")
    return "\n".join(f"{index}. {step}" for index, step in enumerate(steps, start=1))


def local_workbench_chat_answer(
    question: str,
    *,
    products: list[dict[str, Any]],
    datasets: list[dict[str, Any]],
    selected_product: dict[str, Any] | None = None,
    selected_dataset: dict[str, Any] | None = None,
) -> dict[str, Any]:
    text = question or ""
    country, market_label = parse_market(text)
    market_label = market_label or "目标"
    wants_fetch = bool(FETCH_RE.search(text))
    candidates = find_catalog_candidates(products, text)
    if selected_product and not candidates:
        name = str(selected_product.get("name") or "")
        if any(hint in text for hint in FURNITURE_HINTS if hint in name) or "当前" in text:
            candidates = [selected_product]
    ready = ready_datasets_for(datasets, country)
    if not country:
        ready = ready_datasets_for(datasets, None)
    label = requested_product_label(text)
    insights_href = "insights" + (f"?market={country}" if country else "")
    collect_prompts = [
        f"打开市场洞察导入{market_label}{label}数据",
        f"去市场洞察获取{market_label}评论舆情",
    ]
    if wants_fetch:
        where = f"{market_label}市场" + (f"的{label}" if label != "该品类" else "")
        return {
            "answer": (
                f"要获取{where}数据，需要先有授权市场资料。我不会在对话里直接爬取公开电商页。"
                "请打开「市场洞察」导入已授权的竞品和评论文件；若已有授权评论页，到该页粘贴链接并点「获取舆情」。"
                "数据就绪后回到这里，我就能按产品档案分析竞品、价格带和评论痛点。"
            ),
            "title": f"{label} {market_label}市场补数",
            "suggested_prompts": collect_prompts,
            "suggested_action": "insights",
            "action_label": "去市场洞察补数据",
            "action_href": insights_href,
            "product_candidates": [
                {"product_id": item.get("product_id"), "sku": item.get("sku"), "name": item.get("name")}
                for item in candidates
            ],
        }

    if country and not ready:
        lines = "\n".join(_product_line(item, index) for index, item in enumerate(candidates, start=1))
        product_part = f"目录里已有相近产品：\n{lines}\n" if candidates else "先在产品中心确认要分析的产品档案。"
        return {
            "answer": (
                f"你想看「{label}」在{market_label}的出海机会。{product_part}"
                f"但当前企业账号下没有{market_label}市场的已授权数据集（竞品/评论尚未导入或未就绪），"
                "所以我不能判断价格带、评论痛点或机会。请先到「市场洞察」导入该市场数据，或把授权评论页拿到市场洞察里采集。"
                "补数完成后，点选下面的产品建议即可开始分析。"
            ),
            "title": f"{label} {market_label}出海机会分析",
            "suggested_prompts": collect_prompts + [_run_prompt(item, market_label) for item in candidates[:3]],
            "suggested_action": "insights",
            "action_label": f"去市场洞察补{market_label}数据",
            "action_href": insights_href,
            "product_candidates": [
                {"product_id": item.get("product_id"), "sku": item.get("sku"), "name": item.get("name")}
                for item in candidates
            ],
            "missing_market": {"country": country, "label": market_label},
        }

    if candidates:
        dataset_note = ""
        if ready:
            first = ready[0]
            dataset_note = (
                f"已找到{market_label}市场数据集「{first.get('name')}」"
                f"（商品 {first.get('listing_count') or 0}、有效评论 {first.get('valid_review_count') or 0}）。"
            )
        elif datasets:
            dataset_note = "已有其他市场的授权数据，但本问没有指定国家；点选产品并说明目标市场后即可分析。"
        else:
            dataset_note = "还没有已授权市场数据；分析前需要先到市场洞察导入。"
        lines = "\n".join(_product_line(item, index) for index, item in enumerate(candidates, start=1))
        exact = any(str(item.get("name") or "") in text or str(item.get("sku") or "") in text for item in candidates)
        intro = (
            f"目录里找到与「{label}」相关的产品。"
            if exact or label in "".join(str(item.get("name") or "") for item in candidates)
            else f"目录里没有完全叫「{label}」的产品，下面是相近的已建档产品。"
        )
        prompts = [_run_prompt(item, market_label if country else "美国") for item in candidates]
        if not ready:
            prompts = collect_prompts + prompts
        return {
            "answer": f"{intro}\n{lines}\n{dataset_note}点击下方建议或回复 SKU，我就能开始分析。",
            "title": f"{label} 出海机会分析",
            "suggested_prompts": prompts,
            "suggested_action": "insights" if not ready else "",
            "action_label": "去市场洞察补数据" if not ready else "",
            "action_href": insights_href if not ready else "",
            "product_candidates": [
                {"product_id": item.get("product_id"), "sku": item.get("sku"), "name": item.get("name")}
                for item in candidates
            ],
        }

    if selected_product:
        name = selected_product.get("name")
        sku = selected_product.get("sku")
        if ready:
            return {
                "answer": (
                    f"当前绑定的是「{name}」（{sku}）。{market_label}市场已有授权数据。"
                    "直接说要分析到哪一步，例如竞品和评论痛点，或运行完整工作流。"
                ),
                "title": f"{name} 出海机会分析",
                "suggested_prompts": [_run_prompt(selected_product, market_label if country else "美国")],
                "suggested_action": "",
                "product_candidates": [],
            }
        return {
            "answer": (
                f"当前绑定的是「{name}」（{sku}），但没有{market_label}市场的已授权数据集，"
                "还不能评价出海机会。请先到市场洞察导入或采集该市场数据。"
            ),
            "title": f"{name} {market_label}出海机会分析",
            "suggested_prompts": collect_prompts,
            "suggested_action": "insights",
            "action_label": "去市场洞察补数据",
            "action_href": insights_href,
            "product_candidates": [],
            "missing_market": {"country": country, "label": market_label} if country else None,
        }

    return {
        "answer": (
            "请告诉我要分析的产品名称或 SKU，以及目标市场，例如「说说休闲椅在美国市场怎么样」。"
            "我会先核对企业目录和该市场是否已有授权数据；没有数据时会请你去市场洞察补数，而不会编造结论。"
        ),
        "title": "产品出海机会分析",
        "suggested_prompts": [
            "说说休闲椅在美国市场怎么样",
            "打开市场洞察导入美国市场数据",
        ],
        "suggested_action": "",
        "product_candidates": [],
    }
