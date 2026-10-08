"""Tenant-scoped knowledge base, document, chunk, and index-job persistence."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _json(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return fallback
    return fallback


class KnowledgeRepository:
    async def create_base(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        name: str,
        description: str | None,
        visibility: str = "tenant",
    ) -> dict[str, Any]:
        result = await session.execute(text("""
            INSERT INTO knowledge_bases(tenant_id,name,description,visibility,created_by)
            VALUES(:tenant_id,:name,:description,:visibility,:user_id)
            RETURNING id,knowledge_base_uuid::text,name,description,status,visibility,
                      0::int AS document_count,0::int AS ready_document_count,
                      created_at,updated_at
        """), {
            "tenant_id": tenant_id,
            "name": name,
            "description": description,
            "visibility": visibility,
            "user_id": user_id,
        })
        return dict(result.mappings().one())

    async def list_bases(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int | None,
        offset: int,
        limit: int,
    ) -> tuple[list[dict[str, Any]], int]:
        total = int(await session.scalar(text("""
            SELECT count(*) FROM knowledge_bases
             WHERE tenant_id=:tenant_id AND status='active'
               AND (visibility='tenant' OR created_by=:user_id)
        """), {"tenant_id": tenant_id, "user_id": user_id}) or 0)
        rows = await session.execute(text("""
            SELECT kb.knowledge_base_uuid::text,kb.name,kb.description,kb.status,kb.visibility,
                   count(d.id) FILTER(WHERE d.status<>'deleted')::int AS document_count,
                   count(d.id) FILTER(WHERE d.status='ready')::int AS ready_document_count,
                   kb.created_at,kb.updated_at
              FROM knowledge_bases kb
              LEFT JOIN knowledge_documents d
                ON d.knowledge_base_id=kb.id AND d.tenant_id=kb.tenant_id
             WHERE kb.tenant_id=:tenant_id AND kb.status='active'
               AND (kb.visibility='tenant' OR kb.created_by=:user_id)
             GROUP BY kb.id
             ORDER BY kb.updated_at DESC,kb.id DESC
             OFFSET :offset LIMIT :limit
        """), {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "offset": offset,
            "limit": limit,
        })
        return [dict(row) for row in rows.mappings().all()], total

    async def get_base(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        knowledge_base_uuid: str,
        user_id: int | None = None,
        include_archived: bool = False,
    ) -> dict[str, Any] | None:
        row = await session.execute(text("""
            SELECT kb.id,kb.knowledge_base_uuid::text,kb.name,kb.description,kb.status,
                   kb.visibility,kb.created_by,
                   count(d.id) FILTER(WHERE d.status<>'deleted')::int AS document_count,
                   count(d.id) FILTER(WHERE d.status='ready')::int AS ready_document_count,
                   kb.created_at,kb.updated_at
              FROM knowledge_bases kb
              LEFT JOIN knowledge_documents d
                ON d.knowledge_base_id=kb.id AND d.tenant_id=kb.tenant_id
             WHERE kb.tenant_id=:tenant_id
               AND kb.knowledge_base_uuid=CAST(:knowledge_base_uuid AS uuid)
               AND (:include_archived OR kb.status='active')
               AND (kb.visibility='tenant' OR kb.created_by=:user_id)
             GROUP BY kb.id
        """), {
            "tenant_id": tenant_id,
            "knowledge_base_uuid": knowledge_base_uuid,
            "user_id": user_id,
            "include_archived": include_archived,
        })
        record = row.mappings().one_or_none()
        return dict(record) if record else None

    async def patch_base(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        knowledge_base_uuid: str,
        user_id: int,
        name: str | None,
        description: str | None,
        visibility: str | None,
        fields_set: set[str],
    ) -> dict[str, Any] | None:
        result = await session.execute(text("""
            UPDATE knowledge_bases
               SET name=CASE WHEN :set_name THEN :name ELSE name END,
                   description=CASE WHEN :set_description THEN :description ELSE description END,
                   visibility=CASE WHEN :set_visibility THEN :visibility ELSE visibility END,
                   updated_at=CURRENT_TIMESTAMP
             WHERE tenant_id=:tenant_id
               AND knowledge_base_uuid=CAST(:knowledge_base_uuid AS uuid)
               AND status='active'
               AND (visibility='tenant' OR created_by=:user_id)
            RETURNING knowledge_base_uuid::text,name,description,status,visibility,
                      created_at,updated_at
        """), {
            "tenant_id": tenant_id,
            "knowledge_base_uuid": knowledge_base_uuid,
            "user_id": user_id,
            "set_name": "name" in fields_set,
            "name": name,
            "set_description": "description" in fields_set,
            "description": description,
            "set_visibility": "visibility" in fields_set,
            "visibility": visibility,
        })
        record = result.mappings().one_or_none()
        if not record:
            return None
        item = dict(record)
        counts = await session.execute(text("""
            SELECT count(*) FILTER(WHERE status<>'deleted')::int AS document_count,
                   count(*) FILTER(WHERE status='ready')::int AS ready_document_count
              FROM knowledge_documents
             WHERE tenant_id=:tenant_id AND knowledge_base_id=(
               SELECT id FROM knowledge_bases
                WHERE tenant_id=:tenant_id
                  AND knowledge_base_uuid=CAST(:knowledge_base_uuid AS uuid)
             )
        """), {"tenant_id": tenant_id, "knowledge_base_uuid": knowledge_base_uuid})
        item.update(dict(counts.mappings().one()))
        return item

    async def archive_base(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        knowledge_base_uuid: str,
        user_id: int,
    ) -> bool:
        result = await session.execute(text("""
            UPDATE knowledge_bases
               SET status='archived',archived_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
             WHERE tenant_id=:tenant_id
               AND knowledge_base_uuid=CAST(:knowledge_base_uuid AS uuid)
               AND status='active'
               AND (visibility='tenant' OR created_by=:user_id)
        """), {
            "tenant_id": tenant_id,
            "knowledge_base_uuid": knowledge_base_uuid,
            "user_id": user_id,
        })
        return bool(result.rowcount)

    async def create_document_version(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        knowledge_base_id: int,
        filename: str,
        mime_type: str,
        document_type: str | None,
        sha256: str,
        content: bytes,
        document_id: int | None = None,
    ) -> dict[str, Any]:
        if document_id is None:
            document = await session.execute(text("""
                INSERT INTO knowledge_documents(
                  tenant_id,knowledge_base_id,filename,mime_type,document_type,status,
                  current_version,created_by
                )
                VALUES(
                  :tenant_id,:knowledge_base_id,:filename,:mime_type,:document_type,
                  'uploaded',1,:user_id
                )
                RETURNING id,document_uuid::text,current_version,created_at,updated_at
            """), {
                "tenant_id": tenant_id,
                "knowledge_base_id": knowledge_base_id,
                "filename": filename,
                "mime_type": mime_type,
                "document_type": document_type,
                "user_id": user_id,
            })
            document_row = dict(document.mappings().one())
            document_id = int(document_row["id"])
            version = 1
        else:
            document = await session.execute(text("""
                UPDATE knowledge_documents
                   SET filename=:filename,mime_type=:mime_type,document_type=:document_type,
                       status='uploaded',current_version=current_version+1,
                       error_code=NULL,error_message=NULL,deleted_at=NULL,
                       updated_at=CURRENT_TIMESTAMP
                 WHERE id=:document_id AND tenant_id=:tenant_id
                RETURNING id,document_uuid::text,current_version,created_at,updated_at
            """), {
                "tenant_id": tenant_id,
                "document_id": document_id,
                "filename": filename,
                "mime_type": mime_type,
                "document_type": document_type,
            })
            record = document.mappings().one_or_none()
            if record is None:
                raise ValueError("knowledge document not found")
            document_row = dict(record)
            version = int(document_row["current_version"])
        version_row = await session.execute(text("""
            INSERT INTO knowledge_document_versions(
              tenant_id,document_id,version,sha256,byte_size,source_content,created_by
            )
            VALUES(:tenant_id,:document_id,:version,:sha256,:byte_size,:content,:user_id)
            RETURNING id
        """), {
            "tenant_id": tenant_id,
            "document_id": document_id,
            "version": version,
            "sha256": sha256,
            "byte_size": len(content),
            "content": content,
            "user_id": user_id,
        })
        document_version_id = int(version_row.scalar_one())
        job_uuid = str(uuid4())
        await session.execute(text("""
            INSERT INTO knowledge_index_jobs(
              index_job_uuid,tenant_id,document_id,document_version_id,status
            )
            VALUES(
              CAST(:job_uuid AS uuid),:tenant_id,:document_id,:document_version_id,'queued'
            )
        """), {
            "job_uuid": job_uuid,
            "tenant_id": tenant_id,
            "document_id": document_id,
            "document_version_id": document_version_id,
        })
        return {
            "document_uuid": document_row["document_uuid"],
            "knowledge_base_uuid": None,
            "filename": filename,
            "mime_type": mime_type,
            "document_type": document_type,
            "status": "uploaded",
            "version": version,
            "sha256": sha256,
            "byte_size": len(content),
            "index_job_uuid": job_uuid,
            "error_code": None,
            "error_message": None,
            "created_at": document_row["created_at"],
            "updated_at": document_row["updated_at"],
        }

    async def list_documents(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        knowledge_base_id: int,
        offset: int,
        limit: int,
    ) -> tuple[list[dict[str, Any]], int]:
        params = {
            "tenant_id": tenant_id,
            "knowledge_base_id": knowledge_base_id,
            "offset": offset,
            "limit": limit,
        }
        total = int(await session.scalar(text("""
            SELECT count(*) FROM knowledge_documents
             WHERE tenant_id=:tenant_id AND knowledge_base_id=:knowledge_base_id
               AND status<>'deleted'
        """), params) or 0)
        rows = await session.execute(text("""
            SELECT d.document_uuid::text,kb.knowledge_base_uuid::text,d.filename,d.mime_type,
                   d.document_type,d.status,d.current_version AS version,v.sha256,v.byte_size,
                   job.index_job_uuid::text,d.error_code,d.error_message,
                   d.created_at,d.updated_at
              FROM knowledge_documents d
              JOIN knowledge_bases kb
                ON kb.id=d.knowledge_base_id AND kb.tenant_id=d.tenant_id
              JOIN knowledge_document_versions v
                ON v.document_id=d.id AND v.tenant_id=d.tenant_id
               AND v.version=d.current_version
              LEFT JOIN LATERAL(
                SELECT j.index_job_uuid FROM knowledge_index_jobs j
                 WHERE j.tenant_id=d.tenant_id AND j.document_version_id=v.id
                 ORDER BY j.id DESC LIMIT 1
              ) job ON TRUE
             WHERE d.tenant_id=:tenant_id AND d.knowledge_base_id=:knowledge_base_id
               AND d.status<>'deleted'
             ORDER BY d.updated_at DESC,d.id DESC
             OFFSET :offset LIMIT :limit
        """), params)
        return [dict(row) for row in rows.mappings().all()], total

    async def get_document(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        knowledge_base_id: int,
        document_uuid: str,
        include_deleted: bool = False,
    ) -> dict[str, Any] | None:
        row = await session.execute(text("""
            SELECT d.id,d.document_uuid::text,kb.knowledge_base_uuid::text,d.filename,d.mime_type,
                   d.document_type,d.status,d.current_version AS version,v.id AS document_version_id,
                   v.sha256,v.byte_size,job.index_job_uuid::text,d.error_code,d.error_message,
                   d.created_at,d.updated_at
              FROM knowledge_documents d
              JOIN knowledge_bases kb
                ON kb.id=d.knowledge_base_id AND kb.tenant_id=d.tenant_id
              JOIN knowledge_document_versions v
                ON v.document_id=d.id AND v.tenant_id=d.tenant_id
               AND v.version=d.current_version
              LEFT JOIN LATERAL(
                SELECT j.index_job_uuid FROM knowledge_index_jobs j
                 WHERE j.tenant_id=d.tenant_id AND j.document_version_id=v.id
                 ORDER BY j.id DESC LIMIT 1
              ) job ON TRUE
             WHERE d.tenant_id=:tenant_id AND d.knowledge_base_id=:knowledge_base_id
               AND d.document_uuid=CAST(:document_uuid AS uuid)
               AND (:include_deleted OR d.status<>'deleted')
        """), {
            "tenant_id": tenant_id,
            "knowledge_base_id": knowledge_base_id,
            "document_uuid": document_uuid,
            "include_deleted": include_deleted,
        })
        record = row.mappings().one_or_none()
        return dict(record) if record else None

    async def list_document_versions(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        knowledge_base_id: int,
        document_uuid: str,
        offset: int,
        limit: int,
    ) -> tuple[list[dict[str, Any]], int]:
        params = {
            "tenant_id": tenant_id,
            "knowledge_base_id": knowledge_base_id,
            "document_uuid": document_uuid,
            "offset": offset,
            "limit": limit,
        }
        total = int(await session.scalar(text("""
            SELECT count(*)
              FROM knowledge_document_versions v
              JOIN knowledge_documents d
                ON d.id=v.document_id AND d.tenant_id=v.tenant_id
             WHERE v.tenant_id=:tenant_id
               AND d.knowledge_base_id=:knowledge_base_id
               AND d.document_uuid=CAST(:document_uuid AS uuid)
               AND d.status<>'deleted'
        """), params) or 0)
        rows = await session.execute(text("""
            SELECT v.document_version_uuid::text,v.version,v.sha256,v.byte_size,
                   v.extraction_method,v.page_or_sheet_count,
                   job.index_job_uuid::text,job.status AS index_status,
                   job.error_code,job.error_message,
                   (v.version=d.current_version) AS is_current,v.created_at
              FROM knowledge_document_versions v
              JOIN knowledge_documents d
                ON d.id=v.document_id AND d.tenant_id=v.tenant_id
              LEFT JOIN LATERAL(
                SELECT j.index_job_uuid,j.status,j.error_code,j.error_message
                  FROM knowledge_index_jobs j
                 WHERE j.tenant_id=v.tenant_id AND j.document_version_id=v.id
                 ORDER BY j.id DESC LIMIT 1
              ) job ON TRUE
             WHERE v.tenant_id=:tenant_id
               AND d.knowledge_base_id=:knowledge_base_id
               AND d.document_uuid=CAST(:document_uuid AS uuid)
               AND d.status<>'deleted'
             ORDER BY v.version DESC
             OFFSET :offset LIMIT :limit
        """), params)
        return [dict(row) for row in rows.mappings().all()], total

    async def get_document_version(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        knowledge_base_id: int,
        document_uuid: str,
        version: int,
    ) -> dict[str, Any] | None:
        row = await session.execute(text("""
            SELECT d.id AS document_id,d.document_uuid::text,d.filename,d.mime_type,
                   d.document_type,d.current_version,v.id AS document_version_id,
                   v.document_version_uuid::text,v.version,v.sha256,v.byte_size,
                   v.source_content,v.extracted_text,v.extraction_method,
                   v.page_or_sheet_count,v.created_at
              FROM knowledge_document_versions v
              JOIN knowledge_documents d
                ON d.id=v.document_id AND d.tenant_id=v.tenant_id
             WHERE v.tenant_id=:tenant_id
               AND d.knowledge_base_id=:knowledge_base_id
               AND d.document_uuid=CAST(:document_uuid AS uuid)
               AND d.status<>'deleted' AND v.version=:version
        """), {
            "tenant_id": tenant_id,
            "knowledge_base_id": knowledge_base_id,
            "document_uuid": document_uuid,
            "version": version,
        })
        record = row.mappings().one_or_none()
        return dict(record) if record else None

    async def archive_document(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        knowledge_base_id: int,
        document_uuid: str,
    ) -> bool:
        document_id = await session.scalar(text("""
            SELECT id FROM knowledge_documents
             WHERE tenant_id=:tenant_id AND knowledge_base_id=:knowledge_base_id
               AND document_uuid=CAST(:document_uuid AS uuid) AND status<>'deleted'
             FOR UPDATE
        """), {
            "tenant_id": tenant_id,
            "knowledge_base_id": knowledge_base_id,
            "document_uuid": document_uuid,
        })
        if document_id is None:
            return False
        await session.execute(text("""
            UPDATE knowledge_documents
               SET status='deleted',deleted_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
             WHERE tenant_id=:tenant_id AND id=:document_id
        """), {
            "tenant_id": tenant_id,
            "document_id": document_id,
        })
        await session.execute(text("""
            UPDATE knowledge_index_jobs
               SET status='cancelled',finished_at=CURRENT_TIMESTAMP
             WHERE tenant_id=:tenant_id AND document_id=:document_id
               AND status IN ('queued','running')
        """), {"tenant_id": tenant_id, "document_id": document_id})
        await session.execute(text("""
            DELETE FROM knowledge_chunks
             WHERE tenant_id=:tenant_id AND document_id=:document_id
        """), {"tenant_id": tenant_id, "document_id": document_id})
        return True

    async def enqueue_reindex(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        document_id: int,
        document_version_id: int,
    ) -> str:
        job_uuid = str(uuid4())
        await session.execute(text("""
            DELETE FROM knowledge_chunks
             WHERE tenant_id=:tenant_id AND document_version_id=:document_version_id
        """), {"tenant_id": tenant_id, "document_version_id": document_version_id})
        await session.execute(text("""
            UPDATE knowledge_documents
               SET status='uploaded',error_code=NULL,error_message=NULL,updated_at=CURRENT_TIMESTAMP
             WHERE tenant_id=:tenant_id AND id=:document_id
        """), {"tenant_id": tenant_id, "document_id": document_id})
        await session.execute(text("""
            INSERT INTO knowledge_index_jobs(
              index_job_uuid,tenant_id,document_id,document_version_id,status
            )
            VALUES(
              CAST(:job_uuid AS uuid),:tenant_id,:document_id,:document_version_id,'queued'
            )
        """), {
            "job_uuid": job_uuid,
            "tenant_id": tenant_id,
            "document_id": document_id,
            "document_version_id": document_version_id,
        })
        return job_uuid

    async def load_index_job(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        index_job_uuid: str,
    ) -> dict[str, Any] | None:
        result = await session.execute(text("""
            SELECT j.id,j.index_job_uuid::text,j.document_id,j.document_version_id,
                   j.status,d.filename,d.mime_type,d.current_version,v.version,
                   v.source_content
              FROM knowledge_index_jobs j
              JOIN knowledge_documents d
                ON d.id=j.document_id AND d.tenant_id=j.tenant_id
              JOIN knowledge_document_versions v
                ON v.id=j.document_version_id AND v.tenant_id=j.tenant_id
             WHERE j.tenant_id=:tenant_id
               AND j.index_job_uuid=CAST(:index_job_uuid AS uuid)
             FOR UPDATE OF j
        """), {"tenant_id": tenant_id, "index_job_uuid": index_job_uuid})
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def start_index_job(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        index_job_uuid: str,
        document_id: int,
    ) -> bool:
        result = await session.execute(text("""
            UPDATE knowledge_index_jobs
               SET status='running',attempt=attempt+1,started_at=CURRENT_TIMESTAMP,
                   error_code=NULL,error_message=NULL
             WHERE tenant_id=:tenant_id
               AND index_job_uuid=CAST(:index_job_uuid AS uuid)
               AND status='queued'
        """), {"tenant_id": tenant_id, "index_job_uuid": index_job_uuid})
        if not result.rowcount:
            return False
        await session.execute(text("""
            UPDATE knowledge_documents SET status='parsing',updated_at=CURRENT_TIMESTAMP
             WHERE tenant_id=:tenant_id AND id=:document_id AND status<>'deleted'
               AND EXISTS(
                 SELECT 1
                   FROM knowledge_index_jobs j
                   JOIN knowledge_document_versions v
                     ON v.id=j.document_version_id AND v.tenant_id=j.tenant_id
                  WHERE j.tenant_id=:tenant_id
                    AND j.index_job_uuid=CAST(:index_job_uuid AS uuid)
                    AND v.version=knowledge_documents.current_version
               )
        """), {
            "tenant_id": tenant_id,
            "document_id": document_id,
            "index_job_uuid": index_job_uuid,
        })
        return True

    async def set_document_status(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        document_id: int,
        document_version_id: int,
        status: str,
    ) -> None:
        await session.execute(text("""
            UPDATE knowledge_documents SET status=:status,updated_at=CURRENT_TIMESTAMP
             WHERE tenant_id=:tenant_id AND id=:document_id AND status<>'deleted'
               AND current_version=(
                 SELECT version FROM knowledge_document_versions
                  WHERE tenant_id=:tenant_id AND id=:document_version_id
               )
        """), {
            "tenant_id": tenant_id,
            "document_id": document_id,
            "document_version_id": document_version_id,
            "status": status,
        })

    async def complete_index_job(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        index_job_uuid: str,
        document_id: int,
        document_version_id: int,
        extracted_text: str,
        extraction_method: str,
        page_or_sheet_count: int,
        embedding_model: str | None,
        embedding_dimensions: int | None,
        chunks: list[dict[str, Any]],
    ) -> None:
        document_status = await session.scalar(text("""
            SELECT status FROM knowledge_documents
             WHERE tenant_id=:tenant_id AND id=:document_id
             FOR UPDATE
        """), {"tenant_id": tenant_id, "document_id": document_id})
        if document_status == "deleted":
            await session.execute(text("""
                UPDATE knowledge_index_jobs
                   SET status='cancelled',finished_at=CURRENT_TIMESTAMP
                 WHERE tenant_id=:tenant_id
                   AND index_job_uuid=CAST(:index_job_uuid AS uuid)
                   AND status IN ('queued','running')
            """), {"tenant_id": tenant_id, "index_job_uuid": index_job_uuid})
            await session.execute(text("""
                DELETE FROM knowledge_chunks
                 WHERE tenant_id=:tenant_id AND document_id=:document_id
            """), {"tenant_id": tenant_id, "document_id": document_id})
            return
        await session.execute(text("""
            UPDATE knowledge_document_versions
               SET extracted_text=:extracted_text,extraction_method=:extraction_method,
                   page_or_sheet_count=:page_or_sheet_count,embedding_model=:embedding_model,
                   embedding_dimensions=:embedding_dimensions
             WHERE tenant_id=:tenant_id AND id=:document_version_id
        """), {
            "tenant_id": tenant_id,
            "document_version_id": document_version_id,
            "extracted_text": extracted_text,
            "extraction_method": extraction_method,
            "page_or_sheet_count": page_or_sheet_count,
            "embedding_model": embedding_model,
            "embedding_dimensions": embedding_dimensions,
        })
        await session.execute(text("""
            DELETE FROM knowledge_chunks
             WHERE tenant_id=:tenant_id AND document_version_id=:document_version_id
        """), {"tenant_id": tenant_id, "document_version_id": document_version_id})
        for chunk in chunks:
            await session.execute(text("""
                INSERT INTO knowledge_chunks(
                  tenant_id,document_id,document_version_id,chunk_index,page,
                  chunk_text,token_estimate,embedding
                )
                VALUES(
                  :tenant_id,:document_id,:document_version_id,:chunk_index,:page,
                  :chunk_text,:token_estimate,CAST(:embedding AS jsonb)
                )
            """), {
                "tenant_id": tenant_id,
                "document_id": document_id,
                "document_version_id": document_version_id,
                **chunk,
                "embedding": (
                    json.dumps(chunk["embedding"], separators=(",", ":"))
                    if chunk.get("embedding") is not None
                    else None
                ),
            })
        await session.execute(text("""
            UPDATE knowledge_documents
               SET status='ready',error_code=NULL,error_message=NULL,updated_at=CURRENT_TIMESTAMP
             WHERE tenant_id=:tenant_id AND id=:document_id AND status<>'deleted'
               AND current_version=(
                 SELECT version FROM knowledge_document_versions
                  WHERE tenant_id=:tenant_id AND id=:document_version_id
               )
        """), {
            "tenant_id": tenant_id,
            "document_id": document_id,
            "document_version_id": document_version_id,
        })
        await session.execute(text("""
            UPDATE knowledge_index_jobs
               SET status='succeeded',finished_at=CURRENT_TIMESTAMP
             WHERE tenant_id=:tenant_id
               AND index_job_uuid=CAST(:index_job_uuid AS uuid) AND status='running'
        """), {"tenant_id": tenant_id, "index_job_uuid": index_job_uuid})

    async def fail_index_job(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        index_job_uuid: str,
        document_id: int,
        error_code: str,
        error_message: str,
    ) -> None:
        params = {
            "tenant_id": tenant_id,
            "index_job_uuid": index_job_uuid,
            "document_id": document_id,
            "error_code": error_code[:64],
            "error_message": error_message[:1000],
        }
        await session.execute(text("""
            UPDATE knowledge_index_jobs
               SET status='failed',error_code=:error_code,error_message=:error_message,
                   finished_at=CURRENT_TIMESTAMP
             WHERE tenant_id=:tenant_id
               AND index_job_uuid=CAST(:index_job_uuid AS uuid)
               AND status IN ('queued','running')
        """), params)
        await session.execute(text("""
            UPDATE knowledge_documents
               SET status='failed',error_code=:error_code,error_message=:error_message,
                   updated_at=CURRENT_TIMESTAMP
             WHERE tenant_id=:tenant_id AND id=:document_id AND status<>'deleted'
               AND current_version=(
                 SELECT v.version
                   FROM knowledge_index_jobs j
                   JOIN knowledge_document_versions v
                     ON v.id=j.document_version_id AND v.tenant_id=j.tenant_id
                  WHERE j.tenant_id=:tenant_id
                    AND j.index_job_uuid=CAST(:index_job_uuid AS uuid)
               )
        """), params)

    async def search_chunks(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        query: str,
        knowledge_base_uuids: list[str],
        document_types: list[str],
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        params = {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "query": query,
            "knowledge_base_uuids": knowledge_base_uuids,
            "document_types": document_types,
            "limit": limit,
        }
        lexical = await session.execute(text("""
            WITH query_terms AS (
              SELECT websearch_to_tsquery('simple',:query) AS value
            )
            SELECT c.chunk_uuid::text,c.chunk_text,c.page,c.embedding,
                   d.document_uuid::text,d.filename,d.document_type,
                   v.version AS document_version,kb.knowledge_base_uuid::text,
                   ts_rank_cd(c.search_vector,q.value)::float8 AS keyword_score
              FROM knowledge_chunks c
              CROSS JOIN query_terms q
              JOIN knowledge_document_versions v
                ON v.id=c.document_version_id AND v.tenant_id=c.tenant_id
              JOIN knowledge_documents d
                ON d.id=c.document_id AND d.tenant_id=c.tenant_id
               AND d.current_version=v.version
              JOIN knowledge_bases kb
                ON kb.id=d.knowledge_base_id AND kb.tenant_id=d.tenant_id
             WHERE c.tenant_id=:tenant_id AND d.status='ready' AND kb.status='active'
               AND kb.knowledge_base_uuid::text=ANY(:knowledge_base_uuids)
               AND (kb.visibility='tenant' OR kb.created_by=:user_id)
               AND c.search_vector @@ q.value
               AND (
                 cardinality(CAST(:document_types AS text[]))=0 OR
                 d.document_type=ANY(CAST(:document_types AS text[]))
               )
             ORDER BY keyword_score DESC,d.updated_at DESC,c.chunk_index
             LIMIT :limit
        """), params)
        records = list(lexical.mappings().all())
        if len(records) < limit:
            excluded = [str(row["chunk_uuid"]) for row in records]
            fallback = await session.execute(text("""
                SELECT c.chunk_uuid::text,c.chunk_text,c.page,c.embedding,
                       d.document_uuid::text,d.filename,d.document_type,
                       v.version AS document_version,kb.knowledge_base_uuid::text,
                       0::float8 AS keyword_score
                  FROM knowledge_chunks c
                  JOIN knowledge_document_versions v
                    ON v.id=c.document_version_id AND v.tenant_id=c.tenant_id
                  JOIN knowledge_documents d
                    ON d.id=c.document_id AND d.tenant_id=c.tenant_id
                   AND d.current_version=v.version
                  JOIN knowledge_bases kb
                    ON kb.id=d.knowledge_base_id AND kb.tenant_id=d.tenant_id
                 WHERE c.tenant_id=:tenant_id
                   AND d.status='ready' AND kb.status='active'
                   AND kb.knowledge_base_uuid::text=ANY(:knowledge_base_uuids)
                   AND (kb.visibility='tenant' OR kb.created_by=:user_id)
                   AND (
                     cardinality(CAST(:document_types AS text[]))=0 OR
                     d.document_type=ANY(CAST(:document_types AS text[]))
                   )
                   AND (
                     cardinality(CAST(:excluded AS uuid[]))=0 OR
                     NOT c.chunk_uuid=ANY(CAST(:excluded AS uuid[]))
                   )
                 ORDER BY d.updated_at DESC,c.chunk_index
                 LIMIT :limit
            """), {
                **params,
                "excluded": excluded,
                "limit": limit - len(records),
            })
            records.extend(fallback.mappings().all())
        items = []
        for row in records:
            item = dict(row)
            item["embedding"] = _json(item.get("embedding"), [])
            items.append(item)
        return items

    async def search_vector_chunks(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        query: str,
        query_vector: list[float],
        embedding_model: str,
        knowledge_base_uuids: list[str],
        document_types: list[str],
        limit: int,
    ) -> list[dict[str, Any]]:
        if len(query_vector) != 1024:
            return []
        enabled = await session.scalar(text("""
            SELECT to_regtype('vector') IS NOT NULL
               AND EXISTS(
                 SELECT FROM information_schema.columns
                  WHERE table_schema='furniscope'
                    AND table_name='knowledge_chunk_embeddings'
                    AND column_name='embedding_vector'
               )
        """))
        if enabled is not True:
            return []
        rows = await session.execute(text("""
            WITH query_terms AS (
              SELECT websearch_to_tsquery('simple',:query) AS value
            )
            SELECT c.chunk_uuid::text,c.chunk_text,c.page,e.embedding,
                   d.document_uuid::text,d.filename,d.document_type,
                   v.version AS document_version,kb.knowledge_base_uuid::text,
                   ts_rank_cd(c.search_vector,q.value)::float8 AS keyword_score,
                   (
                     1-(e.embedding_vector <=> CAST(:query_vector AS vector(1024)))/2
                   )::float8 AS vector_score
              FROM knowledge_chunk_embeddings e
              JOIN knowledge_chunks c
                ON c.id=e.chunk_id AND c.tenant_id=e.tenant_id
              CROSS JOIN query_terms q
              JOIN knowledge_document_versions v
                ON v.id=c.document_version_id AND v.tenant_id=c.tenant_id
              JOIN knowledge_documents d
                ON d.id=c.document_id AND d.tenant_id=c.tenant_id
               AND d.current_version=v.version
              JOIN knowledge_bases kb
                ON kb.id=d.knowledge_base_id AND kb.tenant_id=d.tenant_id
             WHERE e.tenant_id=:tenant_id
               AND e.embedding_model=:embedding_model
               AND e.embedding_dimensions=1024
               AND e.embedding_vector IS NOT NULL
               AND d.status='ready' AND kb.status='active'
               AND kb.knowledge_base_uuid::text=ANY(:knowledge_base_uuids)
               AND (kb.visibility='tenant' OR kb.created_by=:user_id)
               AND (
                 cardinality(CAST(:document_types AS text[]))=0 OR
                 d.document_type=ANY(CAST(:document_types AS text[]))
               )
             ORDER BY e.embedding_vector <=> CAST(:query_vector AS vector(1024))
             LIMIT :limit
        """), {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "query": query,
            "query_vector": json.dumps(query_vector, separators=(",", ":")),
            "embedding_model": embedding_model,
            "knowledge_base_uuids": knowledge_base_uuids,
            "document_types": document_types,
            "limit": limit,
        })
        items = []
        for row in rows.mappings().all():
            item = dict(row)
            item["embedding"] = _json(item.get("embedding"), [])
            items.append(item)
        return items

    async def persist_search_citations(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        workspace_id: int | None,
        matches: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        output = []
        for match in matches:
            citation_uuid = str(uuid4())
            await session.execute(text("""
                INSERT INTO citations(
                  citation_uuid,tenant_id,workspace_id,source_type,source_id,
                  source_version,label,locator,excerpt,score
                )
                VALUES(
                  CAST(:citation_uuid AS uuid),:tenant_id,:workspace_id,'knowledge_document',
                  :source_id,:source_version,:label,CAST(:locator AS jsonb),:excerpt,:score
                )
            """), {
                "citation_uuid": citation_uuid,
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "source_id": f"document:{match['document_uuid']}",
                "source_version": str(match["document_version"]),
                "label": match["document_name"],
                "locator": json.dumps(
                    {"document_uuid": match["document_uuid"], "page": match.get("page")},
                    ensure_ascii=False,
                ),
                "excerpt": match["chunk_text"],
                "score": match["score"],
            })
            output.append({"citation_uuid": citation_uuid, **match})
        return output
