"""Generate and index tenant-visible HeFeng knowledge from canonical project data.

The command is idempotent by source signature. It creates new document versions
only when the canonical product, market, or SOP inputs change.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy import text

from furniscope_api.config import ApiSettings
from furniscope_api.database import Database, bind_tenant_session
from furniscope_api.repositories.knowledge_repository import KnowledgeRepository
from furniscope_api.services.knowledge_service import KnowledgeService


ROOT = Path(__file__).resolve().parents[1]
MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SEED_VERSION = "hefeng-knowledge-v1"
GENERATED_AT = "2026-10-07"


@dataclass(frozen=True)
class GeneratedDocument:
    filename: str
    document_type: str
    signature: str
    content: bytes
    sources: tuple[str, ...]


def canonical_sha(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def display(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def safe_cell(value: Any) -> str:
    rendered = display(value)
    if rendered.startswith(("=", "+", "-", "@")):
        return "'" + rendered
    return rendered


def value_from_attribute(attributes: dict[str, Any], code: str) -> str:
    raw = attributes.get(code)
    if not isinstance(raw, dict):
        return ""
    value = raw.get("value")
    unit = raw.get("unit")
    rendered = display(value)
    return f"{rendered} {unit}".strip() if unit else rendered


def distinct_attribute_values(products: list[dict[str, Any]], code: str) -> list[str]:
    values = {
        value_from_attribute(item.get("attributes") or {}, code).strip()
        for item in products
    }
    return sorted(value for value in values if value)


def workbook_bytes(
    *,
    title: str,
    description: str,
    signature: str,
    sources: Iterable[str],
    sheets: list[tuple[str, list[str], list[list[Any]]]],
) -> bytes:
    workbook = Workbook()
    workbook.remove(workbook.active)
    workbook.properties.title = title
    workbook.properties.subject = description
    workbook.properties.creator = "FurniScope HeFeng knowledge seed"
    fixed_time = datetime(2026, 10, 7)
    workbook.properties.created = fixed_time
    workbook.properties.modified = fixed_time

    info = workbook.create_sheet("资料说明")
    info_rows = [
        ["资料名称", title],
        ["说明", description],
        ["生成版本", SEED_VERSION],
        ["生成日期", GENERATED_AT],
        ["来源签名", signature],
        ["可见范围", "HeFeng企业共享"],
        ["事实边界", "只复述当前数据库与仓库权威文档，不生成未经确认的经营事实。"],
        ["检索关键词", "HeFeng 产品 市场 销量 预测 SOP 知识库 SKU 证据"],
    ]
    for source in sources:
        info_rows.append(["来源", source])
    for row in info_rows:
        info.append([safe_cell(item) for item in row])
    info.column_dimensions["A"].width = 18
    info.column_dimensions["B"].width = 100
    info.freeze_panes = "A2"

    for sheet_name, headers, rows in sheets:
        sheet = workbook.create_sheet(sheet_name[:31])
        sheet.append([safe_cell(item) for item in headers])
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="245B78")
            cell.alignment = Alignment(vertical="center")
        for row in rows:
            sheet.append([safe_cell(item) for item in row])
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for column_index, header in enumerate(headers, 1):
            values = [safe_cell(header)]
            values.extend(
                safe_cell(row[column_index - 1])
                for row in rows[:200]
                if column_index - 1 < len(row)
            )
            width = min(60, max(12, max((len(value) for value in values), default=12) + 2))
            sheet.column_dimensions[get_column_letter(column_index)].width = width
        for row in sheet.iter_rows():
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)

    from io import BytesIO

    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


async def load_canonical_data(session, tenant_id: int) -> dict[str, Any]:
    product_result = await session.execute(text("""
        SELECT p.sku,p.name,p.category_code,p.description,p.analysis_status,
               pv.schema_version,pv.source_summary,
               COALESCE(
                 jsonb_object_agg(
                   pa.attribute_code,
                   jsonb_build_object('value',pa.value,'unit',pa.unit)
                 ) FILTER(WHERE pa.id IS NOT NULL),
                 '{}'::jsonb
               ) AS attributes
          FROM products p
          LEFT JOIN product_profile_versions pv
            ON pv.id=p.current_profile_version_id AND pv.tenant_id=p.tenant_id
          LEFT JOIN product_attributes pa
            ON pa.profile_version_id=pv.id AND pa.tenant_id=pv.tenant_id
           AND pa.confirmation_status='confirmed'
         WHERE p.tenant_id=:tenant_id AND p.deleted_at IS NULL
         GROUP BY p.id,pv.id
         ORDER BY p.sku
    """), {"tenant_id": tenant_id})
    products = [dict(row) for row in product_result.mappings().all()]

    dataset = (
        await session.execute(text("""
            SELECT d.id,d.name,d.platform,d.market_country,d.category_code,
                   d.source_type,d.source_name,d.authorization_reference,
                   d.data_start_date,d.data_end_date,d.status,
                   d.quality_report,d.limitations,d.updated_at,
                   (SELECT count(*) FROM market_listings l
                     WHERE l.tenant_id=d.tenant_id AND l.dataset_id=d.id) AS listing_count,
                   (SELECT count(*) FROM reviews r
                     WHERE r.tenant_id=d.tenant_id AND r.dataset_id=d.id) AS review_count,
                   (SELECT count(*) FROM reviews r
                     WHERE r.tenant_id=d.tenant_id AND r.dataset_id=d.id
                       AND r.is_valid) AS valid_review_count
              FROM market_datasets d
             WHERE d.tenant_id=:tenant_id AND d.deleted_at IS NULL
             ORDER BY d.updated_at DESC,d.id DESC
             LIMIT 1
        """), {"tenant_id": tenant_id})
    ).mappings().one_or_none()
    dataset_item = dict(dataset) if dataset else {}
    dataset_id = dataset_item.get("id")

    listings: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []
    price_summary: list[dict[str, Any]] = []
    if dataset_id is not None:
        listing_result = await session.execute(text("""
            SELECT platform_listing_id,brand,title,currency,list_price,sale_price,
                   rating,rating_count,review_count,captured_at
              FROM market_listings
             WHERE tenant_id=:tenant_id AND dataset_id=:dataset_id
             ORDER BY platform_listing_id
        """), {"tenant_id": tenant_id, "dataset_id": dataset_id})
        listings = [dict(row) for row in listing_result.mappings().all()]
        review_result = await session.execute(text("""
            SELECT r.platform_review_id,l.platform_listing_id,r.rating,
                   r.title_original,r.content_original,r.language_code,
                   r.reviewer_location,r.sentiment,r.reviewed_at,
                   r.verified_purchase,r.helpful_count
              FROM reviews r
              LEFT JOIN market_listings l
                ON l.id=r.listing_id AND l.tenant_id=r.tenant_id
             WHERE r.tenant_id=:tenant_id AND r.dataset_id=:dataset_id AND r.is_valid
             ORDER BY r.reviewed_at DESC NULLS LAST,r.id
        """), {"tenant_id": tenant_id, "dataset_id": dataset_id})
        reviews = [dict(row) for row in review_result.mappings().all()]
        price_result = await session.execute(text("""
            SELECT currency,count(*) AS sample_size,
                   min(COALESCE(sale_price,list_price)) AS minimum_price,
                   percentile_cont(0.5) WITHIN GROUP(
                     ORDER BY COALESCE(sale_price,list_price)
                   ) AS median_price,
                   max(COALESCE(sale_price,list_price)) AS maximum_price
              FROM market_listings
             WHERE tenant_id=:tenant_id AND dataset_id=:dataset_id
               AND currency IS NOT NULL
               AND COALESCE(sale_price,list_price) IS NOT NULL
             GROUP BY currency
             ORDER BY currency
        """), {"tenant_id": tenant_id, "dataset_id": dataset_id})
        price_summary = [dict(row) for row in price_result.mappings().all()]

    return {
        "products": products,
        "dataset": dataset_item,
        "listings": listings,
        "reviews": reviews,
        "price_summary": price_summary,
    }


def product_catalog_document(data: dict[str, Any]) -> GeneratedDocument:
    products = data["products"]
    source_payload = {
        "seed_version": SEED_VERSION,
        "products": products,
    }
    signature = canonical_sha(source_payload)
    rows = []
    for product in products:
        attributes = product.get("attributes") or {}
        rows.append([
            product["sku"],
            product["name"],
            product["category_code"],
            value_from_attribute(attributes, "material"),
            value_from_attribute(attributes, "color"),
            value_from_attribute(attributes, "dimensions"),
            value_from_attribute(attributes, "factory_price"),
            value_from_attribute(attributes, "moq"),
            value_from_attribute(attributes, "scenes"),
            value_from_attribute(attributes, "customization"),
            value_from_attribute(attributes, "certifications"),
            product["analysis_status"],
            "出厂报价不是会计单位成本；认证字段需在目标市场执行前复核。",
        ])
    content = workbook_bytes(
        title="HeFeng产品主档与目录",
        description=f"从当前已确认产品画像生成，共{len(products)}个SKU。",
        signature=signature,
        sources=(
            "数据库：products / product_profile_versions / product_attributes",
            "画像来源声明：HF catalog.pdf",
            "脚本：scripts/seed_hf_catalog.py",
        ),
        sheets=[(
            "产品目录",
            [
                "SKU", "产品名称", "品类", "材料", "颜色", "尺寸", "出厂报价",
                "MOQ", "使用场景", "定制能力", "认证声明", "分析状态", "事实边界",
            ],
            rows,
        )],
    )
    return GeneratedDocument(
        filename="HeFeng产品主档与目录.xlsx",
        document_type="product_catalog",
        signature=signature,
        content=content,
        sources=("HF catalog.pdf", "confirmed product profiles"),
    )


def manufacturing_document(data: dict[str, Any]) -> GeneratedDocument:
    products = data["products"]
    summary = {
        code: distinct_attribute_values(products, code)
        for code in (
            "material", "color", "dimensions", "factory_price", "moq",
            "scenes", "customization", "certifications",
        )
    }
    signature = canonical_sha({"seed_version": SEED_VERSION, "capabilities": summary})
    rows = [
        ["覆盖SKU", len(products), "当前数据库中未归档且已确认画像的SKU数", "产品画像"],
        ["材料", "；".join(summary["material"]), "目录中的当前材料描述", "产品画像"],
        ["颜色", "；".join(summary["color"]), "目录中的当前颜色描述", "产品画像"],
        ["尺寸", "；".join(summary["dimensions"]), "不同SKU需逐项核对", "产品画像"],
        ["出厂报价", "；".join(summary["factory_price"]), "报价不等于制造成本或落地成本", "用户确认字段"],
        ["MOQ", "；".join(summary["moq"]), "下单前需按SKU和交期复核", "用户确认字段"],
        ["使用场景", "；".join(summary["scenes"]), "用于机会筛选，不构成市场需求证明", "用户确认字段"],
        ["定制能力", "；".join(summary["customization"]), "具体改型需工程评审", "用户确认字段"],
        ["认证声明", "；".join(summary["certifications"]), "目标市场认证必须单独核验", "用户确认字段"],
    ]
    content = workbook_bytes(
        title="HeFeng制造能力与事实边界",
        description="汇总当前产品画像覆盖范围，并明确报价、成本、认证和定制边界。",
        signature=signature,
        sources=(
            "数据库：confirmed product_attributes",
            "Docs/项目设计文档/15_FurniScope_企业决策与数据闭环总体设计V1.md",
            "Docs/项目设计文档/16_FurniScope_系统功能与业务流程复盘V1.md",
        ),
        sheets=[(
            "能力摘要",
            ["能力项", "当前记录", "使用说明", "证据类型"],
            rows,
        )],
    )
    return GeneratedDocument(
        filename="HeFeng制造能力与事实边界.xlsx",
        document_type="manufacturing_capability",
        signature=signature,
        content=content,
        sources=("confirmed product_attributes", "enterprise design"),
    )


def market_dataset_document(data: dict[str, Any]) -> GeneratedDocument:
    dataset = data["dataset"]
    listings = data["listings"]
    price_summary = data["price_summary"]
    signature = canonical_sha({
        "seed_version": SEED_VERSION,
        "dataset": dataset,
        "listings": listings,
        "price_summary": price_summary,
    })
    summary_rows = [[key, display(value)] for key, value in [
        ("数据集名称", dataset.get("name")),
        ("平台", dataset.get("platform")),
        ("国家", dataset.get("market_country")),
        ("品类", dataset.get("category_code")),
        ("来源类型", dataset.get("source_type")),
        ("来源名称", dataset.get("source_name")),
        ("授权编号", dataset.get("authorization_reference")),
        ("数据开始日期", dataset.get("data_start_date")),
        ("数据结束日期", dataset.get("data_end_date")),
        ("当前状态", dataset.get("status")),
        ("Listing数", dataset.get("listing_count")),
        ("原始评论数", dataset.get("review_count")),
        ("有效评论数", dataset.get("valid_review_count")),
        ("质量报告", dataset.get("quality_report")),
        ("限制条件", dataset.get("limitations")),
        ("证据边界", "授权加工数据；不是实时平台采集，不外推到其他国家或品类。"),
        ("检索关键词", "北美 美国 Amazon 沙发 市场 授权 数据集 价格 竞品"),
    ]]
    price_rows = [[
        item.get("currency"),
        item.get("sample_size"),
        item.get("minimum_price"),
        item.get("median_price"),
        item.get("maximum_price"),
        "同币种市场样本统计；不是建议售价。",
    ] for item in price_summary]
    listing_rows = [[
        item.get("platform_listing_id"),
        item.get("brand"),
        item.get("title"),
        item.get("currency"),
        item.get("list_price"),
        item.get("sale_price"),
        item.get("rating"),
        item.get("rating_count"),
        item.get("review_count"),
        item.get("captured_at"),
    ] for item in listings]
    content = workbook_bytes(
        title="HeFeng北美沙发授权市场数据说明",
        description="冻结当前授权数据集的范围、质量、价格样本和Listing清单。",
        signature=signature,
        sources=(
            "数据库：market_datasets / market_listings",
            "授权编号：HeFeng-AUTH-AMZ-US-SOFA-2026Q3",
            "demo_data/hf_market_demo.json",
        ),
        sheets=[
            ("数据范围", ["字段", "当前值"], summary_rows),
            (
                "价格样本",
                ["币种", "样本数", "最低价", "中位价", "最高价", "边界"],
                price_rows,
            ),
            (
                "Listing清单",
                [
                    "Listing ID", "品牌", "标题", "币种", "标价", "售价",
                    "评分", "评分数", "评论数", "抓取时间",
                ],
                listing_rows,
            ),
        ],
    )
    return GeneratedDocument(
        filename="HeFeng北美沙发授权市场数据说明.xlsx",
        document_type="market_dataset",
        signature=signature,
        content=content,
        sources=("authorized market dataset",),
    )


def review_evidence_document(data: dict[str, Any]) -> GeneratedDocument:
    dataset = data["dataset"]
    reviews = data["reviews"]
    signature = canonical_sha({
        "seed_version": SEED_VERSION,
        "dataset_id": dataset.get("id"),
        "reviews": reviews,
    })
    review_rows = [[
        item.get("platform_review_id"),
        item.get("platform_listing_id"),
        item.get("rating"),
        item.get("sentiment"),
        item.get("title_original"),
        item.get("content_original"),
        item.get("language_code"),
        item.get("reviewer_location"),
        item.get("reviewed_at"),
        item.get("verified_purchase"),
        item.get("helpful_count"),
    ] for item in reviews]
    quality_rows = [
        ["有效证据条数", len(reviews), "只纳入reviews.is_valid=true的当前记录"],
        ["原始评论数", dataset.get("review_count"), "原始导入规模"],
        ["质量报告有效数", dataset.get("valid_review_count"), "以当前数据库实际值为准"],
        ["使用限制", "不能把累计评论数当作同期销量或真实价格弹性", "系统复盘已锁定该边界"],
        ["适用范围", "Amazon美国站沙发品类授权包", "不可外推到其他国家、平台或品类"],
        ["检索关键词", "评论 评价 客户 痛点 情绪 安装 舒适 耐用 包装", "全文检索词"],
    ]
    content = workbook_bytes(
        title="HeFeng客户评论证据摘录",
        description="保存当前授权数据集中的有效评论原文和证据边界。",
        signature=signature,
        sources=(
            "数据库：reviews（is_valid=true）",
            "授权编号：HeFeng-AUTH-AMZ-US-SOFA-2026Q3",
        ),
        sheets=[
            ("质量边界", ["项目", "当前值", "说明"], quality_rows),
            (
                "有效评论",
                [
                    "评论ID", "Listing ID", "评分", "情绪", "标题", "原文",
                    "语言", "地区", "评论日期", "已验证购买", "有用数",
                ],
                review_rows,
            ),
        ],
    )
    return GeneratedDocument(
        filename="HeFeng客户评论证据摘录.xlsx",
        document_type="review_evidence",
        signature=signature,
        content=content,
        sources=("valid authorized reviews",),
    )


def forecast_sop_document(data: dict[str, Any]) -> GeneratedDocument:
    product_count = len(data["products"])
    payload = {
        "seed_version": SEED_VERSION,
        "product_count": product_count,
        "rules": [
            "first_use_standard_modeling",
            "daily_140_days",
            "weekly_20_weeks",
            "sku_mapping",
            "explicit_overwrite",
        ],
    }
    signature = canonical_sha(payload)
    flow_rows = [
        [1, "产品中心建立标准SKU", f"当前HeFeng已有{product_count}个未归档SKU", "SKU是业务编码；改码走受控事务"],
        [2, "上传原始销量文件", "CSV/XLSX/JSON", "首次接入走标准数据建模"],
        [3, "确认字段与业务口径", "日期、SKU、站点、销量", "订单明细需先聚合为每日销量"],
        [4, "执行数据质量检查", "连续性、重复、缺失、负数、币种/口径", "阻断错误修复后重传"],
        [5, "配置SKU映射", "原始SKU到产品中心SKU一一对应", "编码相同自动关联"],
        [6, "冻结标准数据版本", "保存来源SHA与质量报告", "确认后不可原地改写"],
        [7, "训练与滚动评测", "日模型至少140天；周模型至少20个完整自然周", "未达标不发布"],
        [8, "发布与追加", "已有私有部署后可快速追加", "同键覆盖必须显式预览并确认"],
    ]
    field_rows = [
        ["date", "日期", "是", "ISO日期；每日汇总"],
        ["sku", "产品编码", "是", "必须关联产品中心标准SKU"],
        ["site", "站点", "是", "例如US、DE"],
        ["sales_qty", "销量", "是", "非负数；退货口径需先确认"],
        ["inventory_qty", "库存", "否", "独立对账；当前模型不把它作为默认因果特征"],
        ["price/promotion", "价格/促销", "否", "当前模型未开放正式因果情景"],
    ]
    boundary_rows = [
        ["快速追加", "只适用于高度标准化的每日汇总销量增量"],
        ["新品", "可接入但标记待验证；无误差依据时不生成自动库存/生产建议"],
        ["预测区间", "仅在对应SKU和时间窗具备验证依据时展示"],
        ["发布", "日/周模型满足质量门槛后原子发布；失败保留旧部署"],
        ["事实边界", "工程回归不等于HeFeng真实预测精度或经营收益已证明"],
        ["检索关键词", "销量 预测 标准建模 快速追加 SKU 映射 140天 20周"],
    ]
    content = workbook_bytes(
        title="HeFeng销量预测数据接入SOP",
        description="根据现行训练契约生成HeFeng首次建模、追加和发布规范。",
        signature=signature,
        sources=(
            "Docs/开发文档/FurniScope_客户接入指南.md",
            "Docs/开发文档/FurniScope_XGBoost销量预测集成说明V1.md",
            "Docs/项目设计文档/15_FurniScope_企业决策与数据闭环总体设计V1.md",
        ),
        sheets=[
            ("标准流程", ["步骤", "动作", "当前规则", "边界"], flow_rows),
            ("标准字段", ["字段", "含义", "必填", "规则"], field_rows),
            ("使用边界", ["主题", "说明"], boundary_rows),
        ],
    )
    return GeneratedDocument(
        filename="HeFeng销量预测数据接入SOP.xlsx",
        document_type="forecast_sop",
        signature=signature,
        content=content,
        sources=("forecast customer guide", "enterprise design"),
    )


def operating_sop_document(data: dict[str, Any]) -> GeneratedDocument:
    payload = {
        "seed_version": SEED_VERSION,
        "product_count": len(data["products"]),
        "dataset_name": data["dataset"].get("name"),
        "routes": [
            "workspace", "products", "analysis", "knowledge",
            "forecast", "insights", "reports",
        ],
    }
    signature = canonical_sha(payload)
    route_rows = [
        ["首页", "查看互斥的待确认、失败、产品冲突和报告摘要", "从真实状态进入后续工作"],
        ["产品中心", "维护SKU、产品画像、组合关系和导入", "分析与预测前先确认主数据"],
        ["AI工作台", "发起带产品、数据集和知识范围的任务", "结论必须带证据和置信度"],
        ["企业知识库", "管理企业共享/个人资料、版本、索引和检索", "工作台绑定只限定检索范围"],
        ["销量预测", "标准建模、快速追加、SKU映射和历史结果", "评测达标后才发布"],
        ["市场洞察", "管理授权数据集、竞品、评论、定价和政策", "多币种或成本缺失时阻断结论"],
        ["决策报告", "查看冻结结论、证据和PDF交付", "报告是决策支持，不替代业务审批"],
    ]
    decision_rows = [
        [1, "确认产品事实", "产品画像已确认；关键成本/认证不猜测"],
        [2, "选择授权市场数据", "数据范围、国家、品类和时间窗明确"],
        [3, "绑定知识检索范围", "选择相关企业知识库；不改变资料权限"],
        [4, "运行分析", "证据不足、冲突或高风险时进入人工确认"],
        [5, "审阅冻结报告", "不在浏览器端补算金额或重选主机会"],
        [6, "记录反馈", "采纳、拒绝和原因进入不可变反馈历史"],
    ]
    knowledge_rows = [
        ["企业共享", "同一HeFeng租户内有knowledge.read权限的账号可读取"],
        ["仅自己", "只有创建者可读取和维护"],
        ["版本", "新版本新增记录，不覆盖旧版本；支持预览和回溯"],
        ["索引", "有Embedding密钥时使用混合检索；无密钥时使用PostgreSQL全文检索"],
        ["引用", "回答必须返回文档、版本、页/工作表定位和摘录"],
        ["低相关度", "低于阈值时拒答，不使用常识补成企业事实"],
        ["检索关键词", "操作 流程 产品 分析 知识库 预测 市场 报告 证据"],
    ]
    content = workbook_bytes(
        title="FurniScope企业操作与知识使用SOP",
        description="按当前七个企业入口整理HeFeng日常操作、知识检索和报告审阅流程。",
        signature=signature,
        sources=(
            "README.md",
            "Docs/参赛提交/05_FurniScope系统使用说明书.md",
            "Docs/项目设计文档/07_FurniScope页面交互原型说明V3.md",
            "Docs/项目设计文档/16_FurniScope_系统功能与业务流程复盘V1.md",
        ),
        sheets=[
            ("七个入口", ["入口", "主要用途", "使用要求"], route_rows),
            ("决策流程", ["步骤", "动作", "验收点"], decision_rows),
            ("知识规则", ["主题", "规则"], knowledge_rows),
        ],
    )
    return GeneratedDocument(
        filename="FurniScope企业操作与知识使用SOP.xlsx",
        document_type="operating_sop",
        signature=signature,
        content=content,
        sources=("current README", "user manual", "page specification"),
    )


def generated_bases(data: dict[str, Any]) -> list[tuple[str, str, list[GeneratedDocument]]]:
    return [
        (
            "HeFeng 产品与制造知识库",
            "HeFeng产品主档、确认画像、制造能力与事实边界。",
            [product_catalog_document(data), manufacturing_document(data)],
        ),
        (
            "HeFeng 北美市场证据库",
            "Amazon美国站沙发授权数据范围、Listing价格样本和有效评论证据。",
            [market_dataset_document(data), review_evidence_document(data)],
        ),
        (
            "HeFeng 业务SOP知识库",
            "销量预测接入、企业操作、知识检索和报告审阅标准流程。",
            [forecast_sop_document(data), operating_sop_document(data)],
        ),
    ]


async def ensure_base(
    session,
    repository: KnowledgeRepository,
    *,
    tenant_id: int,
    user_id: int,
    name: str,
    description: str,
) -> dict[str, Any]:
    row = (
        await session.execute(text("""
            SELECT id,knowledge_base_uuid::text,name,description,status,visibility,
                   created_by,created_at,updated_at
              FROM knowledge_bases
             WHERE tenant_id=:tenant_id AND name=:name
             FOR UPDATE
        """), {"tenant_id": tenant_id, "name": name})
    ).mappings().one_or_none()
    if row is None:
        return await repository.create_base(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            name=name,
            description=description,
            visibility="tenant",
        )
    item = dict(row)
    if item["status"] != "active" or item["description"] != description:
        await session.execute(text("""
            UPDATE knowledge_bases
               SET status='active',archived_at=NULL,description=:description,
                   visibility='tenant',updated_at=CURRENT_TIMESTAMP
             WHERE tenant_id=:tenant_id AND id=:base_id
        """), {
            "tenant_id": tenant_id,
            "base_id": item["id"],
            "description": description,
        })
        item["status"] = "active"
        item["description"] = description
        item["visibility"] = "tenant"
    return item


async def seed_document(
    session,
    service: KnowledgeService,
    *,
    tenant_id: int,
    user_id: int,
    base: dict[str, Any],
    document: GeneratedDocument,
) -> dict[str, Any]:
    existing = (
        await session.execute(text("""
            SELECT d.id,d.document_uuid::text,d.status,d.current_version,d.metadata,
                   v.sha256,j.index_job_uuid::text,j.status AS index_status
              FROM knowledge_documents d
              JOIN knowledge_document_versions v
                ON v.document_id=d.id AND v.tenant_id=d.tenant_id
               AND v.version=d.current_version
              LEFT JOIN LATERAL(
                SELECT index_job_uuid,status
                  FROM knowledge_index_jobs
                 WHERE tenant_id=d.tenant_id AND document_version_id=v.id
                 ORDER BY id DESC LIMIT 1
              ) j ON TRUE
             WHERE d.tenant_id=:tenant_id AND d.knowledge_base_id=:base_id
               AND d.filename=:filename
             ORDER BY d.id DESC
             LIMIT 1
        """), {
            "tenant_id": tenant_id,
            "base_id": int(base["id"]),
            "filename": document.filename,
        })
    ).mappings().one_or_none()
    existing_item = dict(existing) if existing else None
    existing_signature = (
        (existing_item.get("metadata") or {}).get("seed_signature")
        if existing_item
        else None
    )
    if (
        existing_item
        and existing_signature == document.signature
        and existing_item["status"] == "ready"
    ):
        return {
            "filename": document.filename,
            "action": "unchanged",
            "document_uuid": existing_item["document_uuid"],
            "version": int(existing_item["current_version"]),
        }

    document_id = int(existing_item["id"]) if existing_item else None
    queued = await service.queue_document(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        knowledge_base_uuid=str(base["knowledge_base_uuid"]),
        filename=document.filename,
        mime_type=MIME_XLSX,
        content=document.content,
        document_type=document.document_type,
        document_id=document_id,
    )
    await session.execute(text("""
        UPDATE knowledge_documents
           SET metadata=CAST(:metadata AS jsonb)
         WHERE tenant_id=:tenant_id
           AND document_uuid=CAST(:document_uuid AS uuid)
    """), {
        "tenant_id": tenant_id,
        "document_uuid": queued["document_uuid"],
        "metadata": json.dumps({
            "generated_by": "scripts/seed_hefeng_knowledge.py",
            "seed_version": SEED_VERSION,
            "seed_signature": document.signature,
            "sources": list(document.sources),
            "generated_at": GENERATED_AT,
        }, ensure_ascii=False),
    })
    await session.commit()
    await service.process_index_job(
        session,
        tenant_id=tenant_id,
        index_job_uuid=str(queued["index_job_uuid"]),
    )
    return {
        "filename": document.filename,
        "action": "created" if existing_item is None else "versioned",
        "document_uuid": queued["document_uuid"],
        "version": int(queued["version"]),
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    settings = ApiSettings(
        _env_file=None,
        app_env="development",
        database_url=args.database_url,
    )
    database = Database(settings)
    repository = KnowledgeRepository()
    service = KnowledgeService(settings, repository=repository)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        async with database.session_factory() as session:
            actor = (
                await session.execute(text("""
                    SELECT u.id AS user_id,u.tenant_id,t.tenant_code,t.name AS tenant_name
                      FROM users u
                      JOIN tenants t ON t.id=u.tenant_id
                     WHERE lower(u.email)=lower(:email)
                       AND u.status='active' AND t.status='active'
                """), {"email": args.email})
            ).mappings().one_or_none()
            if actor is None:
                raise RuntimeError(f"active HeFeng user not found: {args.email}")
            tenant_id = int(actor["tenant_id"])
            user_id = int(actor["user_id"])
            required = {
                row[0]
                for row in (
                    await session.execute(text("""
                        SELECT version FROM schema_migrations
                         WHERE version IN (
                           'v3_24_memory_agent_governance',
                           'v3_27_audit_rbac',
                           'v3_34_sku_fact_identity'
                         )
                    """))
                ).all()
            }
            if len(required) != 3:
                raise RuntimeError("database must be upgraded through v3_34")
            await bind_tenant_session(session, tenant_id)
            data = await load_canonical_data(session, tenant_id)
            if not data["products"]:
                raise RuntimeError("HeFeng product catalog is empty")
            if not data["dataset"]:
                raise RuntimeError("HeFeng authorized market dataset is empty")

            results = []
            for base_name, description, documents in generated_bases(data):
                base = await ensure_base(
                    session,
                    repository,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    name=base_name,
                    description=description,
                )
                await session.commit()
                base_result = {
                    "name": base_name,
                    "knowledge_base_uuid": str(base["knowledge_base_uuid"]),
                    "documents": [],
                }
                for document in documents:
                    target = output_dir / document.filename
                    target.write_bytes(document.content)
                    base_result["documents"].append(await seed_document(
                        session,
                        service,
                        tenant_id=tenant_id,
                        user_id=user_id,
                        base=base,
                        document=document,
                    ))
                results.append(base_result)

            summary = {
                "protocol": SEED_VERSION,
                "tenant_code": actor["tenant_code"],
                "email": args.email,
                "product_count": len(data["products"]),
                "dataset_name": data["dataset"].get("name"),
                "knowledge_bases": results,
            }
            (output_dir / "seed-summary.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return summary
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", default="hefeng@furniscope.local")
    parser.add_argument(
        "--database-url",
        default=os.getenv(
            "DATABASE_URL",
            "postgresql+asyncpg://postgres@127.0.0.1:5432/furniscope",
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "artifacts" / "hefeng-knowledge-20261007",
    )
    args = parser.parse_args()
    summary = asyncio.run(run(args))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
