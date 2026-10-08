#!/usr/bin/env python3
"""Align the final submission's delivered scope with the implemented product."""

from __future__ import annotations

import argparse
import copy
import io
import os
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"


REPLACEMENTS = {
    (
        "面向中国跨境家具制造企业，依托 AI 多模态分析海外竞品与用户评论，"
        "挖掘未被满足的海外消费需求，结合工厂制造能力输出可落地的选品、"
        "产品迭代与定价方案，降低出海试错成本，帮助工厂精准抓住海外市场机会。"
    ): (
        "面向中国跨境家具制造企业，依托 AI 语义分析与多源数据处理海外竞品、"
        "用户评论及企业资料，挖掘未被满足的海外消费需求，结合工厂制造能力输出"
        "可落地的选品、产品迭代与定价方案，降低出海试错成本，帮助工厂精准抓住"
        "海外市场机会。"
    ),
    (
        "因此，AI可以直接帮助企业缩短：“产品完成 → 营销资料完成 → 海外上线”"
        "之间的时间周期。"
    ): (
        "营销资料生成属于后续扩展方向，不属于本次决赛版本的已交付能力。后续将在"
        "企业明确授权并经人工审核的前提下，评估用 AI 辅助整理产品卖点、产品册、"
        "Amazon 页面和报价资料。"
    ),
    (
        "所以更核心的需求是：潜在客户发现 + 客户需求识别 + 产品客户匹配 + "
        "自动化持续跟进。"
    ): (
        "该问题属于 FurniScope 的中长期需求：潜在客户发现 + 客户需求识别 + "
        "产品客户匹配 + 受控跟进，当前版本尚未交付。"
    ),
    "第二优先级：判断“应该卖给谁”": "中长期需求一：判断“应该卖给谁”",
    (
        "通过分析海外市场和潜在客户数据，找到更匹配的经销商、采购商和目标客户，"
        "提高客户开发效率。"
    ): (
        "在核心市场决策闭环稳定后，后续再评估潜在客户发现、客户需求识别和产品—"
        "客户匹配；该能力当前尚未交付。"
    ),
    "第三优先级：解决“怎么更高效地卖”": "中长期需求二：解决“怎么更高效地卖”",
    (
        "利用AI自动生成和优化：产品图片、产品文案、产品册、Amazon页面、关键词、"
        "报价及营销资料。减少重复性人工工作。"
    ): (
        "后续可在授权资料和人工审核机制下，探索产品文案、产品册、Amazon 页面、"
        "关键词和报价资料的辅助生成；当前版本不提供自动发布或自动客户触达。"
    ),
    "因此，所有需求最终可以浓缩为三个核心问题：": (
        "企业访谈中的长期需求可归纳为三个问题，其中本期聚焦第一项，后两项列入"
        "后续规划："
    ),
    "不知道卖给谁——缺少精准客户发现能力。": (
        "后续规划：不知道卖给谁——缺少精准客户发现能力。"
    ),
    "不知道怎么更高效地卖——大量营销、分析和跟进工作依赖人工。": (
        "后续规划：不知道怎么更高效地卖——大量营销和跟进工作依赖人工。"
    ),
    (
        "市场洞察 → 产品机会发现 → 竞品分析 → 用户痛点识别 → 产品优化 → "
        "定价分析 → 营销内容生成 → 精准客户发现 → 客户开发"
    ): (
        "当前已交付闭环为：产品与企业资产准备 → 授权市场证据分析 → 产品机会与"
        "企业适配判断 → 人工决策 → 实施与经营观察 → 复盘迭代。营销内容生成、"
        "精准客户发现与客户跟进不在本期交付范围。"
    ),
    (
        "最终将传统模式：“业务员凭经验找产品、找客户、做资料”，升级为："
        "“AI分析市场 → AI发现机会 → AI分析竞品 → AI优化产品 → AI匹配客户 → "
        "AI生成营销资料 → 业务员负责判断、谈判与成交。”"
    ): (
        "本期将传统的“人工搜集市场信息并凭经验判断”，升级为“AI 整理授权证据、"
        "识别机会并形成产品与定价建议，业务人员核验证据、作出决策并记录实施结果”。"
        "客户匹配和营销资料生成留待后续迭代。"
    ),
    (
        "从而帮助中小家具企业降低决策成本、缩短新品验证周期、提高客户开发效率，"
        "并逐步摆脱单纯依赖低价竞争的传统出海模式。"
    ): (
        "从而帮助中小家具企业降低市场研判成本、缩短新品验证周期，并逐步摆脱单纯"
        "依赖低价竞争的传统出海模式。"
    ),
}


FUTURE_HEADING = "5. 营销资料生成与精准客户开发（后续规划）"
FUTURE_PARAGRAPHS = [
    (
        "在当前市场决策、销量预测和经营复盘闭环稳定后，后续计划扩展营销资料生成、"
        "潜在客户发现、客户需求识别、产品—客户匹配和受控跟进能力。上述能力当前尚未"
        "交付，不纳入本次决赛功能、效率或商业效果口径。"
    ),
    (
        "后续将以企业现有约 2—3 周的新品营销资料制作周期为待验证基线，在取得产品与"
        "客户资料授权、设置人工审核和发送边界后，评估产品文案、产品册、Amazon 页面、"
        "关键词及报价资料的辅助生成效果；只有形成真实试点数据后再披露效率提升和客户"
        "开发效果。"
    ),
]


def collect_namespaces(xml_bytes: bytes) -> None:
    for _, item in ET.iterparse(io.BytesIO(xml_bytes), events=("start-ns",)):
        prefix, uri = item
        if prefix != "xml":
            try:
                ET.register_namespace(prefix, uri)
            except ValueError:
                pass


def text(element: ET.Element) -> str:
    return "".join(node.text or "" for node in element.iter(W + "t")).strip()


def set_text(element: ET.Element, value: str) -> None:
    nodes = list(element.iter(W + "t"))
    if not nodes:
        run = ET.SubElement(element, W + "r")
        node = ET.SubElement(run, W + "t")
        node.text = value
        return
    nodes[0].text = value
    for node in nodes[1:]:
        node.text = ""


def find_index(body: ET.Element, value: str) -> int:
    for index, child in enumerate(list(body)):
        if text(child) == value:
            return index
    raise RuntimeError(f"Paragraph not found: {value}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("document", type=Path)
    args = parser.parse_args()

    document = args.document.resolve()
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = document.with_name(
        f"{document.stem}.{timestamp}-before-scope-alignment{document.suffix}"
    )
    shutil.copy2(document, backup)

    with zipfile.ZipFile(document, "r") as archive:
        document_xml = archive.read("word/document.xml")
    collect_namespaces(document_xml)
    root = ET.fromstring(document_xml)
    body = root.find(W + "body")
    if body is None:
        raise RuntimeError("DOCX body is missing")

    replaced = []
    present_replacements = set()
    for child in body.iter(W + "p"):
        current = text(child)
        replacement = REPLACEMENTS.get(current)
        if replacement is not None:
            set_text(child, replacement)
            replaced.append(current)
            present_replacements.add(current)
        for original, updated in REPLACEMENTS.items():
            if current == updated:
                present_replacements.add(original)
    missing = sorted(set(REPLACEMENTS) - present_replacements)
    if missing:
        raise RuntimeError(f"Expected paragraphs were not found: {missing}")

    moved_paragraphs = 0
    if not any(text(child) == FUTURE_HEADING for child in body):
        block_start = find_index(body, "7. 缩短营销资料制作周期，加快新品上市速度")
        block_end = find_index(body, "8. 沉淀企业私有数据资产，形成长期复利价值")
        moved_paragraphs = block_end - block_start
        for child in list(body)[block_start:block_end]:
            body.remove(child)
        set_text(list(body)[block_start], "7. 沉淀企业私有数据资产，形成长期复利价值")

        future_anchor = find_index(body, "阶段结论")
        heading_template = list(body)[find_index(body, "4. 数据源、模型与运维持续增强")]
        paragraph_template = list(body)[find_index(body, (
            "继续扩展经授权的数据源与浏览器采集能力，完善分页、失败页留样、政策来源更新"
            "和数据新鲜度告警；在完整因果预处理和在售目录标签具备后，再评估价格、促销、"
            "库存等外生变量是否能够稳定改善预测。同步完善模型成本、延迟、检索召回、队列"
            "积压、通知失败和备份年龄监控，形成可量化的生产服务目标。"
        ))]
        future_nodes = [copy.deepcopy(heading_template)]
        set_text(future_nodes[0], FUTURE_HEADING)
        for value in FUTURE_PARAGRAPHS:
            paragraph = copy.deepcopy(paragraph_template)
            set_text(paragraph, value)
            future_nodes.append(paragraph)
        for offset, node in enumerate(future_nodes):
            body.insert(future_anchor + offset, node)

    new_xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{document.stem}.",
        suffix=".docx",
        dir=document.parent,
        delete=False,
    ) as stream:
        temp_path = Path(stream.name)
    try:
        with zipfile.ZipFile(document, "r") as source:
            with zipfile.ZipFile(temp_path, "w", zipfile.ZIP_DEFLATED) as target:
                for info in source.infolist():
                    target.writestr(
                        info,
                        new_xml if info.filename == "word/document.xml" else source.read(info.filename),
                    )
        with zipfile.ZipFile(temp_path, "r") as verification:
            if verification.testzip() is not None:
                raise RuntimeError("Updated DOCX failed ZIP integrity validation")
        os.replace(temp_path, document)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    print(f"updated={document}")
    print(f"backup={backup}")
    print(f"replacements={len(replaced)}")
    print(f"moved_paragraphs={moved_paragraphs}")


if __name__ == "__main__":
    main()
