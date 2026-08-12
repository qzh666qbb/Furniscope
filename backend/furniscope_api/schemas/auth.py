"""API-AUTH-01/02/03 request and response contracts."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=1024)
    model_config = ConfigDict(extra="forbid")

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if "@" not in normalized or normalized.startswith("@") or normalized.endswith("@"):
            raise ValueError("invalid email")
        return normalized


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=32, max_length=512)
    model_config = ConfigDict(extra="forbid")


class AuthUser(BaseModel):
    user_id: int
    email: str
    name: str
    role_code: Literal["user", "admin"]
    status: Literal["active"]


class RefreshTokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_in: int = 900


class LoginResponse(RefreshTokenResponse):
    user: AuthUser


class TenantProjection(BaseModel):
    tenant_code: str
    name: str
    default_timezone: str
    default_currency: str


class CurrentUserResponse(AuthUser):
    tenant: TenantProjection
