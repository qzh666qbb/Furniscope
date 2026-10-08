"""Validated tenant-specific forecast engine replacement and atomic deployment."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import ApiSettings
from ..errors import BusinessError
from .forecast_runtime import TenantForecastRuntimeRegistry


class ForecastModelReplacementService:
    def __init__(self, settings: ApiSettings,
                 registry: TenantForecastRuntimeRegistry) -> None:
        self.settings = settings
        self.registry = registry

    def _validate(self, *, code_filename: str, code_content: bytes,
                  parameters_filename: str, parameters_content: bytes) -> dict[str, Any]:
        if Path(code_filename).suffix.lower() != ".py" or not code_content:
            raise BusinessError("FORECAST_MODEL_CODE_INVALID",
                                "模型代码必须是非空的 .py 文件", status_code=422)
        if len(code_content) > self.settings.upload_max_bytes:
            raise BusinessError("FILE_SIZE_EXCEEDED", "模型代码超过大小限制", status_code=413)
        try:
            source = code_content.decode("utf-8")
            compile(source, "forecast.py", "exec")
        except (UnicodeDecodeError, SyntaxError) as exc:
            raise BusinessError("FORECAST_MODEL_CODE_INVALID",
                                "模型代码不是有效的 UTF-8 Python 文件", status_code=422) from exc
        if "class ForecastService" not in source:
            raise BusinessError("FORECAST_MODEL_INTERFACE_INVALID",
                                "模型代码必须实现 ForecastService", status_code=422)
        if Path(parameters_filename).suffix.lower() != ".json" or not parameters_content:
            raise BusinessError("FORECAST_MODEL_PARAMS_INVALID",
                                "模型参数必须是非空的 .json 文件", status_code=422)
        if len(parameters_content) > min(self.settings.upload_max_bytes, 1024 * 1024):
            raise BusinessError("FILE_SIZE_EXCEEDED", "模型参数文件不能超过 1 MB", status_code=413)
        try:
            parameters = json.loads(parameters_content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BusinessError("FORECAST_MODEL_PARAMS_INVALID",
                                "模型参数不是有效的 JSON", status_code=422) from exc
        if not isinstance(parameters, dict):
            raise BusinessError("FORECAST_MODEL_PARAMS_INVALID",
                                "模型参数顶层必须是 JSON 对象", status_code=422)
        return parameters

    async def replace(self, session: AsyncSession, *, tenant_id: int, user_id: int,
                      version: str, algorithm: str, change_note: str,
                      code_filename: str, code_content: bytes,
                      parameters_filename: str, parameters_content: bytes) -> dict[str, Any]:
        # The legacy endpoint copied arbitrary packed history and considered
        # "loadable" sufficient for release. Keep an explicit migration response
        # until reviewed custom engines implement the independent evaluator.
        raise BusinessError(
            "FORECAST_STANDARD_TRAINING_REQUIRED",
            "请在模型训练中确认企业数据并选择完整重建；上传代码直接发布已停用，"
            "自定义引擎须先接入标准数据和独立时间验证",
            status_code=409,
        )
