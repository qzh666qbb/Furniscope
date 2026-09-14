"""Container launcher: persisted JWT keys for development, injected keys for production."""

import json
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

DEFAULT_DEV_JWT_PATH = Path("var/runtime/dev-jwt.json")


def _dev_jwt_path() -> Path:
    return Path(os.environ.get("FURNISCOPE_DEV_JWT_PATH", DEFAULT_DEV_JWT_PATH))


def _apply_keys(private_pem: str, public_keys_json: str) -> None:
    os.environ["FURNISCOPE_JWT_PRIVATE_KEY"] = private_pem
    os.environ["FURNISCOPE_JWT_PUBLIC_KEYS_JSON"] = public_keys_json


def _load_cached_keys(path: Path) -> tuple[str, str] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    private_pem = payload.get("private_key")
    public_keys_json = payload.get("public_keys_json")
    if not isinstance(private_pem, str) or not isinstance(public_keys_json, str):
        return None
    if "BEGIN PRIVATE KEY" not in private_pem or not public_keys_json.strip():
        return None
    return private_pem, public_keys_json


def configure_development_keys() -> None:
    if os.environ.get("APP_ENV", "development") == "production":
        return
    if os.environ.get("FURNISCOPE_JWT_PRIVATE_KEY") and os.environ.get("FURNISCOPE_JWT_PUBLIC_KEYS_JSON"):
        return
    cache_path = _dev_jwt_path()
    cached = _load_cached_keys(cache_path)
    if cached is not None:
        _apply_keys(*cached)
        return
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    public_keys_json = json.dumps({"docker-development": public_pem})
    _apply_keys(private_pem, public_keys_json)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps({"private_key": private_pem, "public_keys_json": public_keys_json}),
        encoding="utf-8",
    )
    try:
        cache_path.chmod(0o600)
    except OSError:
        pass


if __name__ == "__main__":
    configure_development_keys()
    import uvicorn

    uvicorn.run(
        "furniscope_api.app:app",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
        reload=os.environ.get("RELOAD", "false").lower() == "true",
    )
