"""Stable business errors and FastAPI exception mapping."""

from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .schemas import ErrorBody, ErrorEnvelope


class BusinessError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int,
        details: list[Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or []


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


def _error_response(request: Request, error: BusinessError) -> JSONResponse:
    body = ErrorEnvelope(
        error=ErrorBody(code=error.code, message=error.message, details=error.details),
        request_id=_request_id(request),
    )
    return JSONResponse(status_code=error.status_code, content=body.model_dump(mode="json"))


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(BusinessError)
    async def business_error_handler(request: Request, exc: BusinessError) -> JSONResponse:
        return _error_response(request, exc)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"location": list(item.get("loc", ())), "message": item.get("msg", "invalid")}
            for item in exc.errors()
        ]
        return _error_response(
            request,
            BusinessError("REQUEST_VALIDATION_FAILED", "请求参数校验失败", status_code=422, details=details),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "RESOURCE_NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR"
        message = "请求资源不存在或不可访问" if exc.status_code == 404 else "请求处理失败"
        return _error_response(request, BusinessError(code, message, status_code=exc.status_code))

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
        request.app.state.logger.exception(
            "unhandled_request_error",
            extra={"request_id": _request_id(request), "error_type": type(exc).__name__},
        )
        return _error_response(
            request,
            BusinessError("INTERNAL_SERVER_ERROR", "系统内部错误", status_code=500),
        )
