"""Document parsing, embedding, indexing, and tenant knowledge retrieval."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import ApiSettings
from ..database import bind_tenant_session
from ..errors import BusinessError
from ..monitoring import KNOWLEDGE_RETRIEVAL_MATCHES, KNOWLEDGE_RETRIEVAL_TOTAL
from ..repositories.knowledge_repository import KnowledgeRepository
from .document_parser import DocumentParseError, DocumentParser
from .model_router_client import ServiceModelRouterClient

_PAGE_MARKER = re.compile(r"^\[(?:page:(\d+)|sheet:[^\]]+)\]\s*$")
_LEXICAL_TERM = re.compile(r"[a-z0-9][a-z0-9_-]*|[\u4e00-\u9fff]{2,}", re.IGNORECASE)


def chunk_knowledge_text(
    text: str,
    *,
    max_chars: int = 1200,
    overlap_chars: int = 120,
) -> list[dict[str, Any]]:
    """Create stable, page-aware chunks without splitting tiny paragraphs."""
    sections: list[tuple[int | None, str]] = []
    page: int | None = None
    current: list[str] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        marker = _PAGE_MARKER.match(line)
        if marker:
            if current:
                sections.append((page, "\n".join(current)))
                current = []
            page = int(marker.group(1)) if marker.group(1) else None
            continue
        if line:
            current.append(line)
    if current:
        sections.append((page, "\n".join(current)))
    if not sections and text.strip():
        sections = [(None, text.strip())]

    chunks: list[dict[str, Any]] = []
    for section_page, section in sections:
        start = 0
        while start < len(section):
            end = min(len(section), start + max_chars)
            if end < len(section):
                break_at = max(
                    section.rfind("\n", start + max_chars // 2, end),
                    section.rfind("。", start + max_chars // 2, end),
                    section.rfind(". ", start + max_chars // 2, end),
                )
                if break_at > start:
                    end = break_at + 1
            content = section[start:end].strip()
            if content:
                chunks.append({
                    "chunk_index": len(chunks),
                    "page": section_page,
                    "chunk_text": content,
                    "token_estimate": max(1, math.ceil(len(content) / 3)),
                })
            if end >= len(section):
                break
            start = max(start + 1, end - overlap_chars)
    return chunks


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return max(-1.0, min(1.0, numerator / (left_norm * right_norm)))


def lexical_match_score(query: str, text: str) -> float:
    """Score exact Latin terms and Chinese character n-grams for FTS fallback."""
    haystack = (text or "").casefold()
    terms: set[str] = set()
    for raw_term in _LEXICAL_TERM.findall((query or "").casefold()):
        if re.fullmatch(r"[\u4e00-\u9fff]+", raw_term) and len(raw_term) > 4:
            terms.update(raw_term[index:index + 2] for index in range(len(raw_term) - 1))
        elif re.fullmatch(r"[\u4e00-\u9fff]+", raw_term) and len(raw_term) > 2:
            terms.add(raw_term)
            terms.update(raw_term[index:index + 2] for index in range(len(raw_term) - 1))
        else:
            terms.add(raw_term)
    if not terms:
        return 0.0
    matched = sum(1 for term in terms if term in haystack)
    return matched / len(terms)


class KnowledgeService:
    def __init__(
        self,
        settings: ApiSettings,
        *,
        repository: KnowledgeRepository | None = None,
        model_client_factory=ServiceModelRouterClient,
    ) -> None:
        self.settings = settings
        self.repository = repository or KnowledgeRepository()
        self.model_client_factory = model_client_factory

    async def queue_document(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        knowledge_base_uuid: str,
        filename: str,
        mime_type: str,
        content: bytes,
        document_type: str | None,
        document_id: int | None = None,
    ) -> dict[str, Any]:
        base = await self.repository.get_base(
            session,
            tenant_id=tenant_id,
            knowledge_base_uuid=knowledge_base_uuid,
            user_id=user_id,
        )
        if base is None:
            raise BusinessError("KNOWLEDGE_BASE_NOT_FOUND", "知识库不存在或已归档", status_code=404)
        parser = DocumentParser(max_bytes=self.settings.upload_max_bytes)
        suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if mime_type not in parser.allowed or suffix not in parser.allowed[mime_type]:
            raise BusinessError("KNOWLEDGE_FILE_TYPE_UNSUPPORTED", "仅支持 PDF、XLSX、JPG/JPEG 和 PNG", status_code=422)
        if not content or len(content) > self.settings.upload_max_bytes:
            raise BusinessError("KNOWLEDGE_FILE_SIZE_INVALID", "文件为空或超过上传大小限制", status_code=422)
        sha256 = hashlib.sha256(content).hexdigest()
        item = await self.repository.create_document_version(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            knowledge_base_id=int(base["id"]),
            filename=filename,
            mime_type=mime_type,
            document_type=document_type,
            sha256=sha256,
            content=content,
            document_id=document_id,
        )
        item["knowledge_base_uuid"] = knowledge_base_uuid
        return item

    async def process_index_job(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        index_job_uuid: str,
    ) -> None:
        job = await self.repository.load_index_job(
            session,
            tenant_id=tenant_id,
            index_job_uuid=index_job_uuid,
        )
        if job is None or job["status"] in {"succeeded", "cancelled"}:
            return
        if not await self.repository.start_index_job(
            session,
            tenant_id=tenant_id,
            index_job_uuid=index_job_uuid,
            document_id=int(job["document_id"]),
        ):
            return
        await session.commit()
        client = self.model_client_factory(self.settings)
        try:
            parser = DocumentParser(max_bytes=self.settings.upload_max_bytes)
            parsed = parser.parse(job["filename"], job["mime_type"], bytes(job["source_content"]))
            await self.repository.set_document_status(
                session,
                tenant_id=tenant_id,
                document_id=int(job["document_id"]),
                document_version_id=int(job["document_version_id"]),
                status="chunking",
            )
            await session.commit()
            chunks = chunk_knowledge_text(parsed.text)
            if not chunks:
                raise DocumentParseError("DOCUMENT_TEXT_EMPTY", "文档没有可索引文本")
            await self.repository.set_document_status(
                session,
                tenant_id=tenant_id,
                document_id=int(job["document_id"]),
                document_version_id=int(job["document_version_id"]),
                status="embedding",
            )
            await session.commit()
            embedding_model = None
            embedding_dimensions = None
            try:
                embedded = await client.embeddings([item["chunk_text"] for item in chunks])
                vectors = embedded.get("vectors") or []
                if len(vectors) != len(chunks):
                    raise RuntimeError("MODEL_OUTPUT_SCHEMA_INVALID: embedding count mismatch")
                indexed = [
                    {**chunk, "embedding": vector}
                    for chunk, vector in zip(chunks, vectors, strict=True)
                ]
                embedding_model = str(embedded["model"])
                embedding_dimensions = int(embedded["dimensions"])
            except RuntimeError as exc:
                if str(exc) != "MODEL_ROUTER_KEY_MISSING":
                    raise
                # The canonical GIN search index remains usable without an
                # external embedding credential. A later reindex can add vectors.
                indexed = [{**chunk, "embedding": None} for chunk in chunks]
            await self.repository.complete_index_job(
                session,
                tenant_id=tenant_id,
                index_job_uuid=index_job_uuid,
                document_id=int(job["document_id"]),
                document_version_id=int(job["document_version_id"]),
                extracted_text=parsed.text,
                extraction_method=parsed.extraction_method,
                page_or_sheet_count=parsed.page_or_sheet_count,
                embedding_model=embedding_model,
                embedding_dimensions=embedding_dimensions,
                chunks=indexed,
            )
            await session.commit()
        except Exception as exc:
            await session.rollback()
            code = exc.code if isinstance(exc, DocumentParseError) else str(exc).split(":", 1)[0]
            await self.repository.fail_index_job(
                session,
                tenant_id=tenant_id,
                index_job_uuid=index_job_uuid,
                document_id=int(job["document_id"]),
                error_code=code or "KNOWLEDGE_INDEX_FAILED",
                error_message=str(exc) or "知识文档索引失败",
            )
            await session.commit()
            raise
        finally:
            await client.close()

    async def search(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        workspace_id: int | None,
        query: str,
        knowledge_base_uuids: list[str],
        document_types: list[str],
        top_k: int,
        persist_citations: bool = True,
    ) -> list[dict[str, Any]]:
        rows = await self.repository.search_chunks(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            query=query,
            knowledge_base_uuids=knowledge_base_uuids,
            document_types=document_types,
        )
        if not rows:
            KNOWLEDGE_RETRIEVAL_TOTAL.labels("empty").inc()
            KNOWLEDGE_RETRIEVAL_MATCHES.observe(0)
            return []
        client = self.model_client_factory(self.settings)
        try:
            query_vector = None
            embedding_model = None
            try:
                result = await client.embeddings([query])
                candidate_vector = (result.get("vectors") or [None])[0]
                if isinstance(candidate_vector, list):
                    query_vector = candidate_vector
                    embedding_model = str(result.get("model") or "")
            except Exception:
                query_vector = None
            if isinstance(query_vector, list) and embedding_model:
                vector_rows = await self.repository.search_vector_chunks(
                    session,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    query=query,
                    query_vector=[float(item) for item in query_vector],
                    embedding_model=embedding_model,
                    knowledge_base_uuids=knowledge_base_uuids,
                    document_types=document_types,
                    limit=max(top_k * 8, 50),
                )
                by_chunk = {item["chunk_uuid"]: item for item in rows}
                for item in vector_rows:
                    existing = by_chunk.get(item["chunk_uuid"])
                    if existing is None:
                        rows.append(item)
                        by_chunk[item["chunk_uuid"]] = item
                    else:
                        existing["vector_score"] = item["vector_score"]
            ranked = []
            for row in rows:
                vector = row.get("embedding")
                keyword_score = max(
                    lexical_match_score(query, str(row.get("chunk_text") or "")),
                    max(0.0, min(1.0, float(row.get("keyword_score") or 0) * 4)),
                )
                vector_score = row.get("vector_score")
                semantic_score = (
                    max(0.0, min(1.0, float(vector_score)))
                    if isinstance(vector_score, (int, float))
                    else None
                )
                if semantic_score is None and (
                    isinstance(query_vector, list)
                    and isinstance(vector, list)
                    and len(vector) == len(query_vector)
                ):
                    semantic_score = (
                        cosine_similarity(query_vector, [float(item) for item in vector]) + 1
                    ) / 2
                if semantic_score is None and not keyword_score:
                    continue
                score = (
                    keyword_score
                    if semantic_score is None
                    else (semantic_score * 0.72) + (keyword_score * 0.28)
                )
                ranked.append({**row, "score": round(score, 7)})
            ranked.sort(key=lambda item: item["score"], reverse=True)
            candidates = ranked[:max(top_k * 4, top_k)]
            if len(candidates) > 1:
                try:
                    reranked = await client.rerank(
                        query,
                        [item["chunk_text"] for item in candidates],
                        top_n=min(top_k, len(candidates)),
                    )
                    candidates = [
                        {**candidates[item["index"]], "score": round(float(item["score"]), 7)}
                        for item in reranked.get("ranking") or []
                    ]
                except Exception:
                    candidates = candidates[:top_k]
            else:
                candidates = candidates[:top_k]
        finally:
            await client.close()
        candidates = [
            item for item in candidates
            if float(item.get("score") or 0) >= self.settings.knowledge_relevance_threshold
        ][:top_k]
        KNOWLEDGE_RETRIEVAL_TOTAL.labels(
            "relevant" if candidates else "below_threshold"
        ).inc()
        KNOWLEDGE_RETRIEVAL_MATCHES.observe(len(candidates))
        matches = [{
            "document_uuid": item["document_uuid"],
            "document_name": item["filename"],
            "page": item.get("page"),
            "chunk_text": item["chunk_text"],
            "score": item["score"],
            "document_version": int(item["document_version"]),
        } for item in candidates]
        if persist_citations:
            return await self.repository.persist_search_citations(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                matches=matches,
            )
        return matches


async def run_knowledge_index_job(
    settings: ApiSettings,
    session_factory,
    *,
    tenant_id: int,
    index_job_uuid: str,
) -> None:
    async with session_factory() as session:
        await bind_tenant_session(session, tenant_id)
        await KnowledgeService(settings).process_index_job(
            session,
            tenant_id=tenant_id,
            index_job_uuid=index_job_uuid,
        )
