import json
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from docker_start import configure_development_keys


def test_development_jwt_keys_are_reused_across_restarts(tmp_path, monkeypatch):
    cache = tmp_path / "dev-jwt.json"
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("FURNISCOPE_JWT_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("FURNISCOPE_JWT_PUBLIC_KEYS_JSON", raising=False)
    monkeypatch.setenv("FURNISCOPE_DEV_JWT_PATH", str(cache))

    configure_development_keys()
    first_private = os.environ["FURNISCOPE_JWT_PRIVATE_KEY"]
    first_public = os.environ["FURNISCOPE_JWT_PUBLIC_KEYS_JSON"]
    assert cache.is_file()
    payload = json.loads(cache.read_text(encoding="utf-8"))
    assert payload["private_key"] == first_private
    private_key = serialization.load_pem_private_key(first_private.encode(), password=None)
    assert isinstance(private_key, RSAPrivateKey)

    monkeypatch.delenv("FURNISCOPE_JWT_PRIVATE_KEY")
    monkeypatch.delenv("FURNISCOPE_JWT_PUBLIC_KEYS_JSON")
    configure_development_keys()
    assert os.environ["FURNISCOPE_JWT_PRIVATE_KEY"] == first_private
    assert os.environ["FURNISCOPE_JWT_PUBLIC_KEYS_JSON"] == first_public


def test_production_does_not_invent_jwt_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("FURNISCOPE_JWT_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("FURNISCOPE_JWT_PUBLIC_KEYS_JSON", raising=False)
    monkeypatch.setenv("FURNISCOPE_DEV_JWT_PATH", str(tmp_path / "dev-jwt.json"))

    configure_development_keys()
    assert "FURNISCOPE_JWT_PRIVATE_KEY" not in os.environ
    assert not Path(os.environ["FURNISCOPE_DEV_JWT_PATH"]).exists()
