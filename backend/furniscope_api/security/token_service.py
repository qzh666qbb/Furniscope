"""RS256 Access Tokens and opaque rotating Refresh Tokens."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import secrets
from typing import Any
from uuid import uuid4

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

from ..config import ApiSettings


class TokenConfigurationError(RuntimeError):
    """Raised when signing key material is missing or inconsistent."""


class TokenService:
    def __init__(self, settings: ApiSettings) -> None:
        self.settings = settings

    def issue_access_token(self, *, user_id: int, tenant_id: int, role_code: str) -> str:
        private_value = self.settings.furniscope_jwt_private_key
        public_value = self.settings.furniscope_jwt_public_keys_json
        if private_value is None or public_value is None:
            raise TokenConfigurationError("JWT signing configuration is unavailable")
        private_pem = private_value.get_secret_value()
        kid = self._resolve_signing_kid(private_pem, public_value.get_secret_value())
        now = datetime.now(timezone.utc)
        claims = {
            "iss": self.settings.furniscope_jwt_issuer,
            "aud": self.settings.furniscope_jwt_audience,
            "sub": str(user_id),
            "user_id": user_id,
            "tenant_id": tenant_id,
            "role_code": role_code,
            "iat": now,
            "nbf": now,
            "exp": now + timedelta(seconds=self.settings.access_token_ttl_seconds),
            "jti": str(uuid4()),
        }
        return jwt.encode(claims, private_pem, algorithm="RS256", headers={"kid": kid})

    @staticmethod
    def issue_refresh_token() -> str:
        return secrets.token_urlsafe(48)

    @staticmethod
    def hash_refresh_token(refresh_token: str) -> str:
        return hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()

    @staticmethod
    def _resolve_signing_kid(private_pem: str, public_keys_json: str) -> str:
        try:
            private_key = serialization.load_pem_private_key(private_pem.encode(), password=None)
            if not isinstance(private_key, RSAPrivateKey):
                raise TokenConfigurationError("JWT private key must be RSA")
            derived = private_key.public_key().public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            key_map: Any = json.loads(public_keys_json)
            matches = []
            if isinstance(key_map, dict):
                for kid, public_pem in key_map.items():
                    if not isinstance(kid, str) or not isinstance(public_pem, str):
                        continue
                    public_key = serialization.load_pem_public_key(public_pem.encode())
                    encoded = public_key.public_bytes(
                        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
                    )
                    if secrets.compare_digest(derived, encoded):
                        matches.append(kid)
            if len(matches) != 1:
                raise TokenConfigurationError("JWT private key must match exactly one configured kid")
            return matches[0]
        except TokenConfigurationError:
            raise
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise TokenConfigurationError("Invalid JWT signing key configuration") from exc
