"""Request ID propagation and access logging middleware.

Pure ASGI (not BaseHTTPMiddleware) so Server-Sent Events can flush chunk by chunk.
"""

from time import monotonic
from uuid import UUID, uuid4

from starlette.datastructures import Headers, MutableHeaders

from .context import reset_request_id, set_request_id
from .monitoring import HTTP_DURATION, HTTP_REQUESTS


class RequestContextMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        supplied = headers.get("x-request-id")
        try:
            request_id = str(UUID(supplied)) if supplied else str(uuid4())
        except ValueError:
            request_id = str(uuid4())

        scope.setdefault("state", {})["request_id"] = request_id
        token = set_request_id(request_id)
        started = monotonic()
        status_code = 500
        logged = False

        async def send_wrapper(message):
            nonlocal status_code, logged
            if message["type"] == "http.response.start":
                status_code = message["status"]
                raw = MutableHeaders(raw=list(message.get("headers") or []))
                raw["X-Request-ID"] = request_id
                message = {**message, "headers": raw.raw}
            elif message["type"] == "http.response.body" and not message.get("more_body", False) and not logged:
                logged = True
                duration = monotonic() - started
                method = scope.get("method", "GET")
                route = scope.get("route")
                path = getattr(route, "path", None) or scope.get("path") or "__unmatched__"
                HTTP_REQUESTS.labels(method, path, str(status_code)).inc()
                HTTP_DURATION.labels(method, path).observe(duration)
                app = scope.get("app")
                logger = getattr(getattr(app, "state", None), "logger", None)
                if logger is not None:
                    logger.info(
                        "request_completed",
                        extra={
                            "request_id": request_id,
                            "method": method,
                            "path": path,
                            "status_code": status_code,
                            "duration_ms": round(duration * 1000, 2),
                        },
                    )
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            reset_request_id(token)
