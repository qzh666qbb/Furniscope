"""Product parse jobs and private file metadata persistence."""

from __future__ import annotations
from typing import Any
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class ParseRepository:
    async def product_exists(self, session: AsyncSession, *, tenant_id: int, product_id: int) -> bool:
        return bool(await session.scalar(text("SELECT EXISTS(SELECT 1 FROM products WHERE id=:id AND tenant_id=:tenant AND deleted_at IS NULL)"), {"id": product_id, "tenant": tenant_id}))

    async def create_asset(self, session: AsyncSession, *, tenant_id: int, product_id: int, user_id: int,
                           filename: str, storage_key: str, mime_type: str, size: int, sha256: str) -> int:
        result = await session.execute(text("""
            INSERT INTO file_assets(tenant_id,owner_type,owner_id,asset_type,original_filename,storage_key,
              mime_type,size_bytes,sha256,security_status,parse_status,created_by)
            VALUES(:tenant,'product',:product,:asset_type,:filename,:storage_key,:mime,:size,:sha,'clean','not_started',:user)
            RETURNING id
        """), {"tenant": tenant_id,"product": product_id,"asset_type": self._asset_type(mime_type),
                 "filename": filename,"storage_key": storage_key,"mime": mime_type,"size": size,"sha": sha256,"user": user_id})
        return int(result.scalar_one())

    async def create_job(self, session: AsyncSession, *, tenant_id: int, product_id: int, user_id: int,
                         idempotency_key: str, asset_ids: list[int]) -> dict[str, Any]:
        result = await session.execute(text("""
            INSERT INTO product_parse_jobs(tenant_id,product_id,file_count,idempotency_key,created_by)
            VALUES(:tenant,:product,:count,:key,:user)
            RETURNING id,parse_job_id::text,product_id,status,progress_percent,current_stage
        """), {"tenant":tenant_id,"product":product_id,"count":len(asset_ids),"key":idempotency_key,"user":user_id})
        row = result.mappings().one()
        for asset_id in asset_ids:
            await session.execute(text("""
                INSERT INTO product_parse_job_files(tenant_id,parse_job_id,file_asset_id,security_status,parse_status)
                VALUES(:tenant,:job,:asset,'clean','queued')
            """), {"tenant":tenant_id,"job":row["id"],"asset":asset_id})
        return dict(row)

    async def get_job(self, session: AsyncSession, *, tenant_id: int, parse_job_id: str) -> dict[str, Any] | None:
        result = await session.execute(text("""
            SELECT j.id,j.parse_job_id::text,j.product_id,j.status,j.progress_percent,j.current_stage,j.file_count,
                   j.succeeded_file_count,j.failed_file_count,j.retryable,j.failure_code,j.failure_message,
                   p.sku AS product_sku,p.name AS product_name
              FROM product_parse_jobs j JOIN products p ON p.id=j.product_id AND p.tenant_id=j.tenant_id
             WHERE j.tenant_id=:tenant AND j.parse_job_id=:job
        """), {"tenant":tenant_id,"job":parse_job_id})
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def files(self, session: AsyncSession, *, tenant_id: int, job_id: int) -> list[dict[str, Any]]:
        result = await session.execute(text("""
            SELECT a.original_filename AS file_name,a.mime_type,f.security_status,f.parse_status,
                   f.error_code,f.error_message,a.storage_key,a.id AS asset_id
              FROM product_parse_job_files f JOIN file_assets a ON a.id=f.file_asset_id
             WHERE f.tenant_id=:tenant AND f.parse_job_id=:job ORDER BY f.id
        """), {"tenant":tenant_id,"job":job_id})
        return [dict(r) for r in result.mappings().all()]

    async def finish(self, session: AsyncSession, *, tenant_id: int, job_id: int,
                     succeeded: int, failed: int) -> None:
        status = "succeeded" if failed == 0 else ("partial_succeeded" if succeeded else "failed")
        failure_code = "DOCUMENT_PARSE_FAILED" if failed and not succeeded else None
        failure_message = "所有文件解析失败，请查看文件级错误并修正后重试" if failed and not succeeded else None
        await session.execute(text("""
            UPDATE product_parse_jobs SET status=:status,current_stage='completed',progress_percent=100,
              succeeded_file_count=:ok,failed_file_count=:failed,retryable=:retryable,
              failure_code=:failure_code,failure_message=:failure_message,completed_at=CURRENT_TIMESTAMP
             WHERE id=:job AND tenant_id=:tenant
        """), {"status":status,"ok":succeeded,"failed":failed,"retryable":bool(failed and not succeeded),
                 "failure_code":failure_code,"failure_message":failure_message,"job":job_id,"tenant":tenant_id})

    @staticmethod
    def _asset_type(mime: str) -> str:
        if mime.startswith("image/"): return "image"
        if mime == "application/pdf": return "pdf"
        if mime == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": return "spreadsheet"
        if mime in {"text/csv","application/vnd.ms-excel"}: return "csv"
        if mime == "application/json": return "json"
        return "other"
