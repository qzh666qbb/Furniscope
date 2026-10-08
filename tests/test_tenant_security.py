from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from furniscope_api.config import ApiSettings
from furniscope_api.services.tenant_security import _canonical_expression, _tenant_expression


def test_policy_expression_canonicalization_accepts_schema_qualified_rls() -> None:
    expression = '(tenant_id = furniscope.current_tenant_id())'
    assert _canonical_expression(expression) == "tenant_id=current_tenant_id"
    assert _tenant_expression(expression)
    assert _tenant_expression("(id = current_tenant_id())", key="id")


def test_policy_expression_rejects_a_different_or_constant_tenant() -> None:
    assert not _tenant_expression("tenant_id = 1")
    assert not _tenant_expression("owner_tenant_id = current_tenant_id()")


def test_production_forbids_runtime_ddl() -> None:
    with pytest.raises(ValueError, match="forbids runtime schema DDL"):
        ApiSettings(
            _env_file=None,
            app_env="production",
            database_allow_runtime_ddl=True,
            database_require_runtime_role_separation=True,
        )


def test_production_requires_runtime_role_separation() -> None:
    with pytest.raises(ValueError, match="runtime-role separation"):
        ApiSettings(
            _env_file=None,
            app_env="production",
            database_allow_runtime_ddl=False,
            database_require_runtime_role_separation=False,
        )
