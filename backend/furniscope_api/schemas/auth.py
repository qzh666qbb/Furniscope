"""API-AUTH-01/02/03 request and response contracts."""

from datetime import datetime
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


class RegistrationRequest(BaseModel):
    enterprise_name: str = Field(min_length=1, max_length=200)
    contact_name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=1024)
    agreed: bool
    model_config = ConfigDict(extra="forbid")

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if "@" not in normalized or normalized.startswith("@") or normalized.endswith("@"):
            raise ValueError("invalid email")
        return normalized

    @field_validator("enterprise_name", "contact_name")
    @classmethod
    def strip_required(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("required")
        return cleaned

    @field_validator("agreed")
    @classmethod
    def must_agree(cls, value: bool) -> bool:
        if not value:
            raise ValueError("must accept terms")
        return value


class RegistrationApplicationResponse(BaseModel):
    application_uuid: str
    enterprise_name: str
    contact_name: str
    email: str
    suggested_tenant_code: str
    status: Literal["pending"]
    created_at: datetime


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=32, max_length=512)
    model_config = ConfigDict(extra="forbid")


class LogoutResponse(BaseModel):
    revoked: bool = True


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
