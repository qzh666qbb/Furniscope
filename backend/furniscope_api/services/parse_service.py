"""Product asset intake and development/test parse worker."""

from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import ApiSettings
from ..errors import BusinessError
from ..repositories.parse_repository import ParseRepository
from .demo_storage import DemoStorage
from .idempotency_service import IdempotencyService
import hashlib


class ParseService:
    def __init__(self, settings: ApiSettings) -> None:
        self.settings = settings
        self.repository = ParseRepository()
        self.storage = DemoStorage(settings)
        self.idempotency = IdempotencyService()

    async def accept(self, session: AsyncSession, *, tenant_id: int, user_id: int, product_id: int,
                     idempotency_key: str, source_type: str, uploads: list[tuple[str,str,bytes]],
                     response_envelope) -> tuple[int,dict[str,Any]]:
        if not await self.repository.product_exists(session, tenant_id=tenant_id, product_id=product_id):
            raise BusinessError("PRODUCT_NOT_FOUND", "产品不存在或不可访问", status_code=404)
        if not uploads:
            raise BusinessError("FILE_EMPTY", "至少上传一个文件", status_code=400)
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
        await self.repository.finish_demo(session,tenant_id=tenant_id,job_id=job["id"],succeeded=succeeded,failed=0)
