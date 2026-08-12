"""Private local storage for development/test only; never enabled as production object storage."""

import hashlib
from pathlib import Path
from uuid import uuid4

from ..config import ApiSettings
from ..errors import BusinessError

ALLOWED_MIME = {"application/json","text/csv","application/pdf","image/jpeg","image/png","image/webp"}


class DemoStorage:
    demo_only = True

    def __init__(self, settings: ApiSettings) -> None:
        if settings.app_env == "production":
            raise BusinessError("STORAGE_UNAVAILABLE", "生产对象存储尚未配置", status_code=503)
        self.root = Path(settings.demo_storage_root).resolve()
        self.max_bytes = settings.upload_max_bytes

    def store(self, *, tenant_id: int, filename: str, mime_type: str, content: bytes) -> tuple[str, str]:
        if not content:
            raise BusinessError("FILE_EMPTY", "上传文件为空", status_code=400)
        if len(content) > self.max_bytes:
            raise BusinessError("FILE_SIZE_EXCEEDED", "文件超过大小限制", status_code=413)
        if mime_type not in ALLOWED_MIME:
            raise BusinessError("FILE_TYPE_UNSUPPORTED", "文件类型不受支持", status_code=415)
        digest = hashlib.sha256(content).hexdigest()
        safe_suffix = Path(filename).suffix.lower()[:12]
        relative = Path(str(tenant_id)) / f"{uuid4().hex}{safe_suffix}"
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.write_bytes(content)
        target.chmod(0o600)
        return relative.as_posix(), digest
