"""Product asset intake and development/test parse worker."""

import json
from pathlib import Path
from typing import Any
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import ApiSettings
from ..errors import BusinessError
from ..repositories.parse_repository import ParseRepository
from .demo_storage import DemoStorage
from .idempotency_service import IdempotencyService
from .document_parser import DocumentParseError, DocumentParser, ProductDocumentExtraction
from .model_router_client import ServiceModelRouterClient
import hashlib
import re


def _parse_error_code(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code.strip():
        return code
    if isinstance(exc, RuntimeError) and str(exc) == "MODEL_ROUTER_KEY_MISSING":
        return "MODEL_ROUTER_KEY_MISSING"
    return "DOCUMENT_SCHEMA_INVALID"


class ParseService:
    def __init__(self, settings: ApiSettings) -> None:
        self.settings = settings
        self.repository = ParseRepository()
        self.storage = DemoStorage(settings)
        self.idempotency = IdempotencyService()

    def require_model_parse_ready(self) -> None:
        if self.settings.product_parse_mode != "model":
            return
        if self.settings.has_model_router_key():
            return
        raise BusinessError(
            "MODEL_ROUTER_KEY_MISSING",
            "模型服务未配置，无法解析产品资料。请先填写产品参数，或配置模型密钥后再上传资料。",
            status_code=503,
        )

    async def accept(self, session: AsyncSession, *, tenant_id: int, user_id: int, product_id: int,
                     idempotency_key: str, source_type: str, uploads: list[tuple[str,str,bytes]],
                     response_envelope) -> tuple[int,dict[str,Any]]:
        if not await self.repository.product_exists(session, tenant_id=tenant_id, product_id=product_id):
            raise BusinessError("PRODUCT_NOT_FOUND", "产品不存在或不可访问", status_code=404)
        if not uploads:
            raise BusinessError("FILE_EMPTY", "至少上传一个文件", status_code=400)
        self.require_model_parse_ready()
        decision=await self.idempotency.begin(session,tenant_id=tenant_id,actor_user_id=user_id,
            route_code="API-PRD-05",http_method="POST",idempotency_key=idempotency_key,
            request_payload={"product_id":product_id,"source_type":source_type,"files":[
                {"name":name,"mime":mime,"sha256":hashlib.sha256(content).hexdigest()} for name,mime,content in uploads]})
        if decision.action == "replay":
            return int(decision.response_status),dict(decision.response_body or {})
        asset_ids=[]
        for filename,mime,content in uploads:
            key,digest=self.storage.store(tenant_id=tenant_id,filename=filename,mime_type=mime,content=content)
            asset_ids.append(await self.repository.create_asset(session,tenant_id=tenant_id,product_id=product_id,
                user_id=user_id,filename=filename,storage_key=key,mime_type=mime,size=len(content),sha256=digest))
        row=await self.repository.create_job(session,tenant_id=tenant_id,product_id=product_id,user_id=user_id,
                                             idempotency_key=idempotency_key,asset_ids=asset_ids)
        envelope=response_envelope(row)
        await self.idempotency.finish(session,tenant_id=tenant_id,record_id=decision.record_id,response_status=202,
            response_body=envelope,resource_type="product_parse_jobs",resource_public_id=row["parse_job_id"])
        return 202,envelope

    async def run_demo_job(self, session: AsyncSession, *, tenant_id: int, parse_job_id: str) -> None:
        job=await self.repository.get_job(session,tenant_id=tenant_id,parse_job_id=parse_job_id)
        if not job or job["status"] != "queued": return
        files=await self.repository.files(session,tenant_id=tenant_id,job_id=job["id"])
        succeeded=0
        for item in files:
            # Storage/security intake is real; semantic extraction remains Model Router work in a later worker.
            from sqlalchemy import text
            await session.execute(text("""
                UPDATE product_parse_job_files SET parse_status='succeeded',output_ref='{}'::jsonb
                 WHERE tenant_id=:tenant AND parse_job_id=:job AND file_asset_id=:asset
            """), {"tenant":tenant_id,"job":job["id"],"asset":item["asset_id"]})
            await session.execute(text("""
                UPDATE file_assets SET parse_status='succeeded' WHERE tenant_id=:tenant AND id=:asset
            """), {"tenant":tenant_id,"asset":item["asset_id"]})
            succeeded += 1
        await self.repository.finish(session,tenant_id=tenant_id,job_id=job["id"],succeeded=succeeded,failed=0)

    async def run_job(self, session: AsyncSession, *, tenant_id: int, parse_job_id: str) -> None:
        if self.settings.product_parse_mode == "demo":
            await self.run_demo_job(session, tenant_id=tenant_id, parse_job_id=parse_job_id)
            return
        job = await self.repository.get_job(session, tenant_id=tenant_id, parse_job_id=parse_job_id)
        if not job or job["status"] != "queued":
            return
        await session.execute(text("""UPDATE product_parse_jobs SET status='running',current_stage='extracting',
          progress_percent=5,started_at=now() WHERE id=:job AND tenant_id=:tenant"""),
          {"job": job["id"], "tenant": tenant_id})
        files = await self.repository.files(session, tenant_id=tenant_id, job_id=job["id"])
        parser = DocumentParser(max_bytes=self.settings.upload_max_bytes)
        router = ServiceModelRouterClient(self.settings)
        extracted: dict[str, tuple[Any, int, str]] = {}
        succeeded = failed = 0
        root = Path(self.settings.demo_storage_root).resolve()
        try:
            try:
                self.require_model_parse_ready()
            except BusinessError as exc:
                if exc.code != "MODEL_ROUTER_KEY_MISSING":
                    raise
                for item in files:
                    await session.execute(text("""UPDATE product_parse_job_files SET parse_status='failed',error_code=:code,
                      error_message=:message WHERE tenant_id=:tenant AND parse_job_id=:job AND file_asset_id=:asset"""),
                      {"tenant": tenant_id, "job": job["id"], "asset": item["asset_id"],
                       "code": exc.code, "message": exc.message[:1000]})
                    await session.execute(text("UPDATE file_assets SET parse_status='failed' WHERE tenant_id=:tenant AND id=:asset"),
                      {"tenant": tenant_id, "asset": item["asset_id"]})
                    failed += 1
                await self.repository.finish(session, tenant_id=tenant_id, job_id=job["id"], succeeded=0, failed=failed)
                return
            for item in files:
                try:
                    source = (root / item["storage_key"]).resolve()
                    if root not in source.parents or not source.is_file():
                        raise DocumentParseError("FILE_STORAGE_INVALID", "解析源文件不可用")
                    document = parser.parse(item["file_name"], item["mime_type"], source.read_bytes())
                    catalog_match = next((entry for entry in document.catalog_items
                                          if entry.sku.casefold() == job["product_sku"].casefold()), None)
                    catalog_context = ({
                        "selected_product_sku": catalog_match.sku,
                        "catalog_category": catalog_match.category,
                        "catalog_page": catalog_match.page,
                        "catalog_name": item["file_name"],
                    } if catalog_match else {})
                    source_text = document.text
                    if catalog_match:
                        page_match = re.search(
                            rf"\[page:{catalog_match.page}\]\n(.*?)(?=\n\[page:\d+\]|\Z)",
                            document.text,
                            flags=re.DOTALL,
                        )
                        if page_match:
                            source_text = page_match.group(1)
                    result = await router.structured(messages=[
                        {"role": "system", "content": "你是家具产品资料抽取器。只从输入原文提取事实，禁止猜测。输出JSON，attributes至少覆盖资料中可确认的SKU、品类、尺寸、材质、结构、风格、颜色、目标市场；attribute_code使用snake_case，confidence为0到1，evidence_text必须是原文短片段。"},
                        {"role": "user", "content": json.dumps(catalog_context, ensure_ascii=False) + "\n" + source_text},
                    ], output_type=ProductDocumentExtraction)
                    for attribute in result.attributes:
                        current = extracted.get(attribute.attribute_code)
                        if current is None or attribute.confidence > current[0].confidence:
                            extracted[attribute.attribute_code] = (attribute, item["asset_id"], document.extraction_method)
                    await session.execute(text("""UPDATE product_parse_job_files SET parse_status='succeeded',
                      output_ref=CAST(:output AS jsonb) WHERE tenant_id=:tenant AND parse_job_id=:job AND file_asset_id=:asset"""),
                      {"tenant": tenant_id, "job": job["id"], "asset": item["asset_id"], "output": json.dumps({"method": document.extraction_method, "attribute_codes": [value.attribute_code for value in result.attributes]})})
                    await session.execute(text("UPDATE file_assets SET parse_status='succeeded' WHERE tenant_id=:tenant AND id=:asset"), {"tenant": tenant_id, "asset": item["asset_id"]})
                    succeeded += 1
                except Exception as exc:
                    code = _parse_error_code(exc)
                    await session.execute(text("""UPDATE product_parse_job_files SET parse_status='failed',error_code=:code,
                      error_message=:message WHERE tenant_id=:tenant AND parse_job_id=:job AND file_asset_id=:asset"""),
                      {"tenant": tenant_id, "job": job["id"], "asset": item["asset_id"], "code": code, "message": f"{type(exc).__name__}: parsing failed"[:1000]})
                    await session.execute(text("UPDATE file_assets SET parse_status='failed' WHERE tenant_id=:tenant AND id=:asset"), {"tenant": tenant_id, "asset": item["asset_id"]})
                    failed += 1
            if extracted:
                profile_id = int((await session.execute(text("""INSERT INTO product_profile_versions
                  (tenant_id,product_id,version_no,schema_version,status,completeness_score,source_summary)
                  SELECT :tenant,:product,COALESCE(max(version_no),0)+1,'product-document-v1','parsed',
                         :completeness,CAST(:summary AS jsonb) FROM product_profile_versions WHERE product_id=:product
                  RETURNING id"""), {"tenant": tenant_id, "product": job["product_id"],
                    "completeness": min(len(set(extracted) & {"sku","category","dimensions","material","structure","style","color","target_market"}) / 8, 1),
                    "summary": json.dumps({"parse_job_id": parse_job_id, "file_count": succeeded, "data_class": "uploaded_product_document"})})).scalar_one())
                for code, (attribute, asset_id, method) in extracted.items():
                    await session.execute(text("""INSERT INTO product_attributes
                      (tenant_id,profile_version_id,attribute_code,value,unit,source_type,source_asset_id,
                       source_locator,confidence,confirmation_status)
                      VALUES(:tenant,:profile,:code,CAST(:value AS jsonb),:unit,:source,:asset,
                             CAST(:locator AS jsonb),:confidence,:confirmation)"""),
                      {"tenant": tenant_id, "profile": profile_id, "code": code, "value": json.dumps(attribute.value, ensure_ascii=False), "unit": attribute.unit, "source": "image" if method == "image_ocr" else "document", "asset": asset_id, "locator": json.dumps({"evidence_text": attribute.evidence_text, "method": method}, ensure_ascii=False), "confidence": attribute.confidence, "confirmation": "unconfirmed" if attribute.confidence < .7 else "confirmed"})
                # Factory-entered facts always win over document extraction and visual inference.
                # Preserve the lower-priority observation in source_locator so the UI can expose
                # a real conflict instead of silently discarding the disagreement.
                await session.execute(text("""
                    INSERT INTO product_attributes
                      (tenant_id,profile_version_id,attribute_code,value,unit,source_type,source_asset_id,
                       source_locator,confidence,confirmation_status)
                    SELECT pa.tenant_id,:profile,pa.attribute_code,pa.value,pa.unit,pa.source_type,
                           pa.source_asset_id,pa.source_locator,pa.confidence,pa.confirmation_status
                      FROM product_attributes pa
                      JOIN products p ON p.current_profile_version_id=pa.profile_version_id
                     WHERE p.id=:product AND p.tenant_id=:tenant
                       AND pa.source_type IN ('user_input','confirmed_structured')
                    ON CONFLICT(profile_version_id,attribute_code) DO UPDATE SET
                      value=excluded.value,
                      unit=excluded.unit,
                      source_type=excluded.source_type,
                      source_asset_id=excluded.source_asset_id,
                      confidence=GREATEST(excluded.confidence,product_attributes.confidence),
                      confirmation_status=CASE
                        WHEN product_attributes.value IS DISTINCT FROM excluded.value THEN 'conflicted'
                        ELSE 'confirmed'
                      END,
                      source_locator=COALESCE(excluded.source_locator,'{}'::jsonb) ||
                        CASE WHEN product_attributes.value IS DISTINCT FROM excluded.value
                          THEN jsonb_build_object(
                            'conflicting_source_type',product_attributes.source_type,
                            'conflicting_value',product_attributes.value
                          )
                          ELSE '{}'::jsonb
                        END
                """), {"tenant": tenant_id, "product": job["product_id"], "profile": profile_id})
                await session.execute(text("""UPDATE products SET current_profile_version_id=:profile,
                  analysis_status='profile_pending' WHERE id=:product AND tenant_id=:tenant"""),
                  {"profile": profile_id, "product": job["product_id"], "tenant": tenant_id})
            await self.repository.finish(session, tenant_id=tenant_id, job_id=job["id"], succeeded=succeeded, failed=failed)
        finally:
            await router.close()
