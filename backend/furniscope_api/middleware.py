"""Request ID propagation and access logging middleware."""

from time import monotonic
from uuid import UUID, uuid4

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from .context import reset_request_id, set_request_id
from .monitoring import HTTP_DURATION, HTTP_REQUESTS


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        supplied = request.headers.get("X-Request-ID")
        try:
            request_id = str(UUID(supplied)) if supplied else str(uuid4())
        except ValueError:
            request_id = str(uuid4())

        request.state.request_id = request_id
        token = set_request_id(request_id)
        started = monotonic()
        try:
            response = await call_next(request)
        finally:
            reset_request_id(token)
        route = getattr(request.scope.get("route"), "path", "__unmatched__")
        duration = monotonic() - started
        HTTP_REQUESTS.labels(request.method, route, str(response.status_code)).inc()
        HTTP_DURATION.labels(request.method, route).observe(duration)
        response.headers["X-Request-ID"] = request_id
        request.app.state.logger.info(
            "request_completed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round(duration * 1000, 2),
            },
        )
        return response
