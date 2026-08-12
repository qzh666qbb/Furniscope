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
            SELECT id,parse_job_id::text,product_id,status,progress_percent,current_stage,file_count,
                   succeeded_file_count,failed_file_count,retryable,failure_code,failure_message
              FROM product_parse_jobs WHERE tenant_id=:tenant AND parse_job_id=:job
        """), {"tenant":tenant_id,"job":parse_job_id})
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def files(self, session: AsyncSession, *, tenant_id: int, job_id: int) -> list[dict[str, Any]]:
        result = await session.execute(text("""
            SELECT a.original_filename AS file_name,f.security_status,f.parse_status,a.storage_key,a.id AS asset_id
              FROM product_parse_job_files f JOIN file_assets a ON a.id=f.file_asset_id
             WHERE f.tenant_id=:tenant AND f.parse_job_id=:job ORDER BY f.id
        """), {"tenant":tenant_id,"job":job_id})
        return [dict(r) for r in result.mappings().all()]

    async def finish_demo(self, session: AsyncSession, *, tenant_id: int, job_id: int,
                          succeeded: int, failed: int) -> None:
        status = "succeeded" if failed == 0 else ("partial_succeeded" if succeeded else "failed")
        await session.execute(text("""
            UPDATE product_parse_jobs SET status=:status,current_stage='completed',progress_percent=100,
              succeeded_file_count=:ok,failed_file_count=:failed,retryable=FALSE,completed_at=CURRENT_TIMESTAMP
             WHERE id=:job AND tenant_id=:tenant
        """), {"status":status,"ok":succeeded,"failed":failed,"job":job_id,"tenant":tenant_id})

    @staticmethod
    def _asset_type(mime: str) -> str:
        if mime.startswith("image/"): return "image"
        if mime == "application/pdf": return "pdf"
        if mime in {"text/csv","application/vnd.ms-excel"}: return "csv"
        if mime == "application/json": return "json"
        return "other"
