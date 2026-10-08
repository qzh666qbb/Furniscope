"""Tenant-prefixed local and S3-compatible private object storage."""

from io import BytesIO
import hashlib
from pathlib import Path, PurePosixPath
from typing import Protocol
from uuid import uuid4

from ..config import ApiSettings
from ..errors import BusinessError

ALLOWED_MIME = {"application/json","text/csv","application/csv","text/json","application/octet-stream",
                "application/pdf","image/jpeg","image/png",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
ALLOWED_SUFFIX = {".json", ".csv", ".xlsx", ".pdf", ".jpg", ".jpeg", ".png"}


class ObjectStorage(Protocol):
    def store(
        self,
        *,
        tenant_id: int,
        filename: str,
        mime_type: str,
        content: bytes,
    ) -> tuple[str, str]: ...

    def read(self, *, tenant_id: int, key: str) -> bytes: ...


def _validate_upload(
    *,
    filename: str,
    mime_type: str,
    content: bytes,
    max_bytes: int,
) -> tuple[str, str]:
    if not content:
        raise BusinessError("FILE_EMPTY", "上传文件为空", status_code=400)
    if len(content) > max_bytes:
        raise BusinessError("FILE_SIZE_EXCEEDED", "文件超过大小限制", status_code=413)
    suffix = Path(filename).suffix.lower()
    if (
        mime_type not in ALLOWED_MIME
        or (
            mime_type == "application/octet-stream"
            and suffix not in ALLOWED_SUFFIX
        )
    ) and suffix not in ALLOWED_SUFFIX:
        raise BusinessError("FILE_TYPE_UNSUPPORTED", "文件类型不受支持", status_code=415)
    return suffix[:12], hashlib.sha256(content).hexdigest()


def _new_key(tenant_id: int, suffix: str) -> str:
    return f"{tenant_id}/{uuid4().hex}{suffix}"


def _validate_tenant_key(tenant_id: int, key: str) -> str:
    path = PurePosixPath(key)
    if (
        path.is_absolute()
        or len(path.parts) < 2
        or path.parts[0] != str(tenant_id)
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise BusinessError(
            "DATA_ARTIFACT_UNAVAILABLE",
            "数据文件不存在或不属于本企业",
            status_code=409,
        )
    return path.as_posix()


class LocalObjectStorage:
    demo_only = True

    def __init__(self, settings: ApiSettings) -> None:
        self.root = Path(settings.demo_storage_root).resolve()
        self.max_bytes = settings.upload_max_bytes

    def store(self, *, tenant_id: int, filename: str, mime_type: str, content: bytes) -> tuple[str, str]:
        suffix, digest = _validate_upload(
            filename=filename,
            mime_type=mime_type,
            content=content,
            max_bytes=self.max_bytes,
        )
        key = _new_key(tenant_id, suffix)
        target = self.root / key
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.write_bytes(content)
        target.chmod(0o600)
        return key, digest

    def read(self, *, tenant_id: int, key: str) -> bytes:
        safe_key = _validate_tenant_key(tenant_id, key)
        tenant_root = self.root / str(tenant_id)
        target = (self.root / safe_key).resolve()
        if tenant_root not in target.parents or not target.is_file():
            raise BusinessError(
                "DATA_ARTIFACT_UNAVAILABLE",
                "数据文件不存在或不属于本企业",
                status_code=409,
            )
        return target.read_bytes()


class S3ObjectStorage:
    demo_only = False

    def __init__(self, settings: ApiSettings) -> None:
        try:
            from minio import Minio
        except ImportError as exc:
            raise BusinessError(
                "STORAGE_UNAVAILABLE",
                "对象存储客户端未安装",
                status_code=503,
            ) from exc
        self.max_bytes = settings.upload_max_bytes
        self.bucket = settings.object_storage_bucket
        self.client = Minio(
            settings.object_storage_endpoint or "",
            access_key=settings.object_storage_access_key.get_secret_value(),
            secret_key=settings.object_storage_secret_key.get_secret_value(),
            secure=settings.object_storage_secure,
            region=settings.object_storage_region,
        )
        try:
            exists = self.client.bucket_exists(self.bucket)
            if not exists and settings.object_storage_auto_create_bucket:
                self.client.make_bucket(
                    self.bucket,
                    location=settings.object_storage_region,
                )
            elif not exists:
                raise BusinessError(
                    "STORAGE_UNAVAILABLE",
                    "对象存储桶不存在",
                    status_code=503,
                )
        except BusinessError:
            raise
        except Exception as exc:
            raise BusinessError(
                "STORAGE_UNAVAILABLE",
                "对象存储不可用",
                status_code=503,
            ) from exc

    def store(self, *, tenant_id: int, filename: str, mime_type: str, content: bytes) -> tuple[str, str]:
        suffix, digest = _validate_upload(
            filename=filename,
            mime_type=mime_type,
            content=content,
            max_bytes=self.max_bytes,
        )
        key = _new_key(tenant_id, suffix)
        try:
            self.client.put_object(
                self.bucket,
                key,
                BytesIO(content),
                len(content),
                content_type=mime_type,
            )
        except Exception as exc:
            raise BusinessError(
                "STORAGE_UNAVAILABLE",
                "对象存储写入失败",
                status_code=503,
            ) from exc
        return key, digest

    def read(self, *, tenant_id: int, key: str) -> bytes:
        safe_key = _validate_tenant_key(tenant_id, key)
        response = None
        try:
            response = self.client.get_object(self.bucket, safe_key)
            return response.read()
        except Exception as exc:
            raise BusinessError(
                "DATA_ARTIFACT_UNAVAILABLE",
                "数据文件不存在或不属于本企业",
                status_code=409,
            ) from exc
        finally:
            if response is not None:
                response.close()
                response.release_conn()


def create_object_storage(settings: ApiSettings) -> ObjectStorage:
    if settings.storage_backend == "s3":
        return S3ObjectStorage(settings)
    return LocalObjectStorage(settings)


# Preserve the old import for downstream code while local storage remains the
# default in development and tests.
DemoStorage = LocalObjectStorage
