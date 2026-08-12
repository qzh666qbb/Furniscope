"""Request-scoped context used by middleware, logging and repositories."""

from contextvars import ContextVar, Token
from dataclasses import dataclass


request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)


@dataclass(frozen=True, slots=True)
class TenantContext:
    tenant_id: int
    user_id: int
    role_code: str
    token_jti_digest: str


def set_request_id(request_id: str) -> Token[str | None]:
    return request_id_context.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    request_id_context.reset(token)
