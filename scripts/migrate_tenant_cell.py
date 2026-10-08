"""Move one tenant from a shared cell into an empty isolated cell database."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import tempfile
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import asyncpg

from scripts.tenant_backup import (
    _extract_verified,
    canonical,
    export_tenant,
    restore_tenant,
)


CELL_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,63}$")


async def _fetch_cell(
    connection: asyncpg.Connection,
    cell_code: str,
) -> dict[str, Any]:
    row = await connection.fetchrow(
        """
        SELECT cell_code,display_name,region_code,cell_kind,status,
               routing_endpoint,database_secret_ref,capacity_tenants,safe_metadata
          FROM furniscope.deployment_cells
         WHERE cell_code=$1
        """,
        cell_code,
    )
    if row is None:
        raise RuntimeError(f"deployment cell is not registered: {cell_code}")
    result = dict(row)
    if isinstance(result["safe_metadata"], str):
        result["safe_metadata"] = json.loads(result["safe_metadata"])
    return result


async def _freeze_source(
    args: argparse.Namespace,
) -> tuple[str, datetime, dict[str, Any], dict[str, Any]]:
    connection = await asyncpg.connect(args.source_url)
    try:
        async with connection.transaction(isolation="serializable"):
            tenant = await connection.fetchrow(
                """
                SELECT t.id,t.tenant_code,p.cell_code,p.status,p.migration_uuid
                  FROM furniscope.tenants t
                  JOIN furniscope.tenant_placements p ON p.tenant_id=t.id
                 WHERE t.tenant_code=$1
                 FOR UPDATE OF t,p
                """,
                args.tenant_code,
            )
            if tenant is None:
                raise RuntimeError("source tenant does not exist")
            if tenant["cell_code"] != args.source_cell or tenant["status"] != "active":
                raise RuntimeError(
                    "source tenant placement must be active in the declared source cell"
                )
            source_cell = await _fetch_cell(connection, args.source_cell)
            await connection.execute(
                """
                INSERT INTO furniscope.deployment_cells(
                  cell_code,display_name,region_code,cell_kind,status,is_default,
                  routing_endpoint,database_secret_ref,safe_metadata
                ) VALUES($1,$2,$3,'dedicated','active',false,$4,$5,$6::jsonb)
                ON CONFLICT(cell_code) DO UPDATE SET
                  display_name=EXCLUDED.display_name,
                  region_code=EXCLUDED.region_code,
                  cell_kind='dedicated',
                  status='active',
                  routing_endpoint=EXCLUDED.routing_endpoint,
                  database_secret_ref=EXCLUDED.database_secret_ref,
                  safe_metadata=EXCLUDED.safe_metadata,
                  updated_at=CURRENT_TIMESTAMP
                """,
                args.target_cell,
                args.target_display_name,
                args.target_region,
                args.target_routing_endpoint,
                args.target_database_secret_ref,
                canonical({"provisioned_by": "migrate_tenant_cell"}),
            )
            target_cell = await _fetch_cell(connection, args.target_cell)
            migration_uuid = str(
                await connection.fetchval(
                    """
                    INSERT INTO furniscope.tenant_migration_jobs(
                      tenant_id,source_cell_code,target_cell_code,status
                    ) VALUES($1,$2,$3,'freezing')
                    RETURNING migration_uuid
                    """,
                    tenant["id"],
                    args.source_cell,
                    args.target_cell,
                )
            )
            frozen_at = await connection.fetchval(
                """
                UPDATE furniscope.tenant_placements
                   SET status='frozen',migration_uuid=$2::uuid,
                       placement_version=placement_version+1,
                       updated_at=CURRENT_TIMESTAMP
                 WHERE tenant_id=$1
                RETURNING updated_at
                """,
                tenant["id"],
                migration_uuid,
            )
            await connection.execute(
                """
                UPDATE furniscope.tenant_migration_jobs
                   SET status='exporting',frozen_at=$2,updated_at=CURRENT_TIMESTAMP
                 WHERE migration_uuid=$1::uuid
                """,
                migration_uuid,
                frozen_at,
            )
            return migration_uuid, frozen_at, source_cell, target_cell
    finally:
        await connection.close()


async def _wait_for_source_transactions(
    source_url: str,
    frozen_at: datetime,
    timeout_seconds: int,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    connection = await asyncpg.connect(source_url)
    try:
        while True:
            active = await connection.fetchval(
                """
                SELECT count(*)
                  FROM pg_stat_activity
                 WHERE datname=current_database()
                   AND backend_type='client backend'
                   AND pid<>pg_backend_pid()
                   AND xact_start IS NOT NULL
                   AND xact_start<=$1::timestamptz
                   AND state<>'idle'
                """,
                frozen_at,
            )
            if active == 0:
                return
            if time.monotonic() >= deadline:
                raise TimeoutError("source transactions did not drain before timeout")
            await asyncio.sleep(1)
    finally:
        await connection.close()


def _verified_manifest(package: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="furniscope-cell-package-") as temp:
        return _extract_verified(package, Path(temp))


async def _update_source_export(
    source_url: str,
    migration_uuid: str,
    package_sha256: str,
    source_counts: dict[str, int],
) -> None:
    connection = await asyncpg.connect(source_url)
    try:
        await connection.execute(
            """
            UPDATE furniscope.tenant_migration_jobs
               SET status='restoring',package_sha256=$2,
                   source_row_counts=$3::jsonb,exported_at=CURRENT_TIMESTAMP,
                   updated_at=CURRENT_TIMESTAMP
             WHERE migration_uuid=$1::uuid
            """,
            migration_uuid,
            package_sha256,
            canonical(source_counts),
        )
    finally:
        await connection.close()


async def _prepare_target_cells(
    target_url: str,
    cells: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    connection = await asyncpg.connect(target_url)
    try:
        if await connection.fetchval("SELECT count(*) FROM furniscope.tenants") != 0:
            raise RuntimeError("target cell database must contain zero tenants")
        for cell in cells:
            await connection.execute(
                """
                INSERT INTO furniscope.deployment_cells(
                  cell_code,display_name,region_code,cell_kind,status,is_default,
                  routing_endpoint,database_secret_ref,capacity_tenants,safe_metadata
                ) VALUES($1,$2,$3,$4,$5,false,$6,$7,$8,$9::jsonb)
                ON CONFLICT(cell_code) DO UPDATE SET
                  display_name=EXCLUDED.display_name,
                  region_code=EXCLUDED.region_code,
                  cell_kind=EXCLUDED.cell_kind,
                  status=EXCLUDED.status,
                  routing_endpoint=EXCLUDED.routing_endpoint,
                  database_secret_ref=EXCLUDED.database_secret_ref,
                  capacity_tenants=EXCLUDED.capacity_tenants,
                  safe_metadata=EXCLUDED.safe_metadata,
                  updated_at=CURRENT_TIMESTAMP
                """,
                cell["cell_code"],
                cell["display_name"],
                cell["region_code"],
                cell["cell_kind"],
                cell["status"],
                cell["routing_endpoint"],
                cell["database_secret_ref"],
                cell["capacity_tenants"],
                canonical(cell["safe_metadata"]),
            )
    finally:
        await connection.close()


async def _validate_target(
    target_url: str,
    tenant_id: int,
    migration_uuid: str,
    manifest: dict[str, Any],
) -> tuple[dict[str, int], str]:
    connection = await asyncpg.connect(target_url)
    try:
        async with connection.transaction(isolation="serializable"):
            await connection.execute(
                """
                UPDATE furniscope.tenant_placements
                   SET cell_code=$2,status='validating',migration_uuid=$3::uuid,
                       placement_version=placement_version+1,
                       routing_generation=routing_generation+1,
                       updated_at=CURRENT_TIMESTAMP
                 WHERE tenant_id=$1
                """,
                tenant_id,
                manifest["target_cell"],
                migration_uuid,
            )
            target_counts: dict[str, int] = {}
            for package_table, expected in manifest["tables"].items():
                table = (
                    "forecast_models"
                    if package_table == "forecast_models__owner"
                    else package_table
                )
                if not re.fullmatch(r"[a-z][a-z0-9_]*", table):
                    raise RuntimeError(f"unsafe table name in package: {table}")
                tenant_column = "owner_tenant_id" if table == "forecast_models" else "tenant_id"
                count = int(
                    await connection.fetchval(
                        f'SELECT count(*) FROM furniscope."{table}" '
                        f'WHERE "{tenant_column}"=$1',
                        tenant_id,
                    )
                )
                if count != int(expected["rows"]):
                    raise RuntimeError(
                        f"target row count mismatch: {package_table} "
                        f"expected={expected['rows']} actual={count}"
                    )
                target_counts[package_table] = count
            validation_hash = hashlib.sha256(
                canonical({
                    "package_sha256": manifest["_package_sha256"],
                    "target_counts": target_counts,
                }).encode()
            ).hexdigest()
            source_counts = {
                table: int(metadata["rows"])
                for table, metadata in manifest["tables"].items()
            }
            await connection.execute(
                """
                UPDATE furniscope.tenant_migration_jobs
                   SET status='validating',package_sha256=$2,
                       source_row_counts=$3::jsonb,target_row_counts=$4::jsonb,
                       validation_hash=$5,restored_at=CURRENT_TIMESTAMP,
                       validated_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
                 WHERE migration_uuid=$1::uuid
                """,
                migration_uuid,
                manifest["_package_sha256"],
                canonical(source_counts),
                canonical(target_counts),
                validation_hash,
            )
            return target_counts, validation_hash
    finally:
        await connection.close()


async def _cutover(
    source_url: str,
    target_url: str,
    tenant_id: int,
    migration_uuid: str,
    target_cell: str,
    target_counts: dict[str, int],
    validation_hash: str,
) -> None:
    source = await asyncpg.connect(source_url)
    try:
        async with source.transaction(isolation="serializable"):
            changed = await source.fetchval(
                """
                UPDATE furniscope.tenant_placements
                   SET cell_code=$3,status='cutover',
                       placement_version=placement_version+1,
                       routing_generation=routing_generation+1,
                       updated_at=CURRENT_TIMESTAMP
                 WHERE tenant_id=$1 AND migration_uuid=$2::uuid AND status='frozen'
                RETURNING tenant_id
                """,
                tenant_id,
                migration_uuid,
                target_cell,
            )
            if changed is None:
                raise RuntimeError("source placement changed before cutover")
            await source.execute(
                """
                UPDATE furniscope.tenant_migration_jobs
                   SET status='cutover',cutover_at=CURRENT_TIMESTAMP,
                       updated_at=CURRENT_TIMESTAMP
                 WHERE migration_uuid=$1::uuid
                """,
                migration_uuid,
            )
    finally:
        await source.close()

    target = await asyncpg.connect(target_url)
    try:
        async with target.transaction(isolation="serializable"):
            changed = await target.fetchval(
                """
                UPDATE furniscope.tenant_placements
                   SET status='active',migration_uuid=NULL,
                       placement_version=placement_version+1,
                       updated_at=CURRENT_TIMESTAMP
                 WHERE tenant_id=$1 AND cell_code=$2
                   AND migration_uuid=$3::uuid AND status='validating'
                RETURNING tenant_id
                """,
                tenant_id,
                target_cell,
                migration_uuid,
            )
            if changed is None:
                raise RuntimeError("target placement is not validated")
            await target.execute(
                """
                UPDATE furniscope.tenant_migration_jobs
                   SET status='completed',completed_at=CURRENT_TIMESTAMP,
                       updated_at=CURRENT_TIMESTAMP
                 WHERE migration_uuid=$1::uuid
                """,
                migration_uuid,
            )
    finally:
        await target.close()

    source = await asyncpg.connect(source_url)
    try:
        await source.execute(
            """
            UPDATE furniscope.tenant_migration_jobs
               SET status='completed',target_row_counts=$2::jsonb,
                   validation_hash=$3,validated_at=CURRENT_TIMESTAMP,
                   completed_at=CURRENT_TIMESTAMP,
                   updated_at=CURRENT_TIMESTAMP
             WHERE migration_uuid=$1::uuid
            """,
            migration_uuid,
            canonical(target_counts),
            validation_hash,
        )
    finally:
        await source.close()


async def _unfreeze_failed_source(
    source_url: str,
    migration_uuid: str,
    source_cell: str,
    exc: BaseException,
) -> None:
    connection = await asyncpg.connect(source_url)
    try:
        async with connection.transaction():
            placement = await connection.fetchrow(
                """
                SELECT tenant_id,status
                  FROM furniscope.tenant_placements
                 WHERE migration_uuid=$1::uuid
                 FOR UPDATE
                """,
                migration_uuid,
            )
            if placement and placement["status"] != "cutover":
                await connection.execute(
                    """
                    UPDATE furniscope.tenant_placements
                       SET cell_code=$2,status='active',migration_uuid=NULL,
                           placement_version=placement_version+1,
                           updated_at=CURRENT_TIMESTAMP
                     WHERE tenant_id=$1
                    """,
                    placement["tenant_id"],
                    source_cell,
                )
            await connection.execute(
                """
                UPDATE furniscope.tenant_migration_jobs
                   SET status='failed',failure_code=$2,failure_detail=$3,
                       updated_at=CURRENT_TIMESTAMP
                 WHERE migration_uuid=$1::uuid AND status<>'completed'
                """,
                migration_uuid,
                type(exc).__name__[:100],
                str(exc)[:2000],
            )
    finally:
        await connection.close()


async def migrate(args: argparse.Namespace) -> dict[str, Any]:
    if args.confirm_tenant_code != args.tenant_code:
        raise RuntimeError("tenant-code confirmation does not match")
    if args.source_url == args.target_url:
        raise RuntimeError("source and target database URLs must differ")
    if args.source_cell == args.target_cell:
        raise RuntimeError("source and target cells must differ")
    for value in (args.source_cell, args.target_cell, args.target_region):
        if not CELL_PATTERN.fullmatch(value):
            raise RuntimeError(f"invalid cell or region identifier: {value}")
    migration_uuid = ""
    source_cell: dict[str, Any] | None = None
    try:
        migration_uuid, frozen_at, source_cell, target_cell = await _freeze_source(args)
        await _wait_for_source_transactions(
            args.source_url, frozen_at, args.drain_timeout_seconds,
        )
        exported = await export_tenant(SimpleNamespace(
            database_url=args.source_url,
            tenant_code=args.tenant_code,
            output=args.package,
            object_root=args.source_object_root,
        ))
        manifest = _verified_manifest(args.package)
        manifest["target_cell"] = args.target_cell
        manifest["_package_sha256"] = exported["sha256"]
        source_counts = {
            table: int(metadata["rows"])
            for table, metadata in manifest["tables"].items()
        }
        await _update_source_export(
            args.source_url, migration_uuid, exported["sha256"], source_counts,
        )
        await _prepare_target_cells(args.target_url, (source_cell, target_cell))
        restored = await restore_tenant(SimpleNamespace(
            database_url=args.target_url,
            package=args.package,
            confirm_tenant_code=args.tenant_code,
            object_root=args.target_object_root,
        ))
        target_counts, validation_hash = await _validate_target(
            args.target_url,
            int(restored["tenant_id"]),
            migration_uuid,
            manifest,
        )
        await _cutover(
            args.source_url,
            args.target_url,
            int(restored["tenant_id"]),
            migration_uuid,
            args.target_cell,
            target_counts,
            validation_hash,
        )
        return {
            "migration_uuid": migration_uuid,
            "tenant_code": args.tenant_code,
            "source_cell": args.source_cell,
            "target_cell": args.target_cell,
            "package_sha256": exported["sha256"],
            "validation_hash": validation_hash,
            "target_row_counts": target_counts,
            "status": "completed",
        }
    except BaseException as exc:
        if migration_uuid and source_cell is not None:
            await _unfreeze_failed_source(
                args.source_url, migration_uuid, args.source_cell, exc,
            )
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--target-url", required=True)
    parser.add_argument("--tenant-code", required=True)
    parser.add_argument("--confirm-tenant-code", required=True)
    parser.add_argument("--source-cell", required=True)
    parser.add_argument("--target-cell", required=True)
    parser.add_argument("--target-region", required=True)
    parser.add_argument("--target-display-name", required=True)
    parser.add_argument("--target-routing-endpoint")
    parser.add_argument("--target-database-secret-ref")
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--source-object-root", type=Path)
    parser.add_argument("--target-object-root", type=Path)
    parser.add_argument("--drain-timeout-seconds", type=int, default=900)
    args = parser.parse_args()
    if not 1 <= args.drain_timeout_seconds <= 3600:
        parser.error("--drain-timeout-seconds must be between 1 and 3600")
    result = asyncio.run(migrate(args))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
