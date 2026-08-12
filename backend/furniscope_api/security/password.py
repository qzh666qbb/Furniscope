"""Argon2id password hashing and constant-work verification."""

from pwdlib import PasswordHash
import secrets

_password_hash = PasswordHash.recommended()
_dummy_hash = _password_hash.hash(secrets.token_urlsafe(32))


class PasswordService:
    def hash(self, password: str) -> str:
        return _password_hash.hash(password)

    def verify(self, password: str, stored_hash: str | None) -> bool:
        candidate = stored_hash or _dummy_hash
        try:
            verified = _password_hash.verify(password, candidate)
        except (ValueError, TypeError):
            verified = False
        return bool(verified and stored_hash is not None)
