"""Export or restore one tenant as a checksummed logical recovery package."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import tarfile
import tempfile
from pathlib import Path
from typing import Any

import asyncpg


PROTOCOL = "furniscope-tenant-backup-v1"


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_ndjson(path: Path, payloads: list[Any]) -> dict[str, Any]:
    with path.open("w", encoding="utf-8") as stream:
        for payload in payloads:
            stream.write(canonical(payload) + "\n")
    return {"rows": len(payloads), "sha256": file_sha256(path)}


async def _json_rows(connection: asyncpg.Connection, query: str, *params: Any) -> list[Any]:
    rows = await connection.fetch(query, *params)
    return [json.loads(row["payload"]) for row in rows]


async def export_tenant(args: argparse.Namespace) -> dict[str, Any]:
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise RuntimeError(f"output already exists: {output}")
    connection = await asyncpg.connect(args.database_url)
    try:
        async with connection.transaction(isolation="repeatable_read", readonly=True):
            await connection.execute("SET LOCAL search_path TO furniscope,public")
            tenant = await connection.fetchrow(
                "SELECT to_jsonb(t) payload FROM tenants t WHERE tenant_code=$1",
                args.tenant_code,
            )
            if tenant is None:
                raise RuntimeError("tenant does not exist")
            tenant_payload = json.loads(tenant["payload"])
            tenant_id = int(tenant_payload["id"])
            migrations = await connection.fetch(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
            table_rows = await connection.fetch("""
                SELECT c.relname AS table_name
                  FROM pg_class c
                  JOIN pg_namespace n ON n.oid=c.relnamespace
                 WHERE n.nspname='furniscope'
                   AND c.relkind IN ('r','p')
                   AND NOT c.relispartition
                   AND EXISTS(
                     SELECT FROM pg_attribute a
                      WHERE a.attrelid=c.oid AND a.attname='tenant_id'
                        AND NOT a.attisdropped
                   )
                 ORDER BY c.relname
            """)
            with tempfile.TemporaryDirectory(prefix="furniscope-tenant-export-") as temp:
                root = Path(temp)
                table_dir = root / "tables"
                object_dir = root / "objects"
                table_dir.mkdir()
                object_dir.mkdir()
                (root / "tenant.json").write_text(
                    canonical(tenant_payload) + "\n", encoding="utf-8"
                )
                table_files: dict[str, dict[str, Any]] = {}
                storage_keys: set[str] = set()
                for table_row in table_rows:
                    table = table_row["table_name"]
                    payloads = await _json_rows(
                        connection,
                        f'SELECT to_jsonb(t) payload FROM furniscope."{table}" t '
                        "WHERE tenant_id=$1 ORDER BY to_jsonb(t)::text",
                        tenant_id,
                    )
                    for payload in payloads:
                        key = payload.get("storage_key")
                        if isinstance(key, str) and key:
                            storage_keys.add(key)
                    table_files[table] = write_ndjson(table_dir / f"{table}.ndjson", payloads)
                owner_models = await _json_rows(
                    connection,
                    """SELECT to_jsonb(t) payload FROM forecast_models t
                        WHERE owner_tenant_id=$1 ORDER BY to_jsonb(t)::text""",
                    tenant_id,
                )
                table_files["forecast_models__owner"] = write_ndjson(
                    table_dir / "forecast_models__owner.ndjson", owner_models
                )
                objects: list[dict[str, Any]] = []
                if storage_keys:
                    if args.object_root is None:
                        raise RuntimeError("tenant has file assets; --object-root is required")
                    source_root = args.object_root.resolve()
                    for key in sorted(storage_keys):
                        source = (source_root / key).resolve()
                        if source_root not in source.parents:
                            raise RuntimeError(f"storage key escapes object root: {key}")
                        if not source.is_file():
                            raise RuntimeError(f"tenant object is missing: {key}")
                        digest = file_sha256(source)
                        target = object_dir / digest
                        target.write_bytes(source.read_bytes())
                        objects.append({
                            "storage_key": key,
                            "sha256": digest,
                            "bytes": source.stat().st_size,
                        })
                manifest = {
                    "protocol": PROTOCOL,
                    "tenant_id": tenant_id,
                    "tenant_code": tenant_payload["tenant_code"],
                    "tenant_code_sha256": hashlib.sha256(
                        tenant_payload["tenant_code"].encode()
                    ).hexdigest(),
                    "schema_migrations": [row["version"] for row in migrations],
                    "tenant_json_sha256": file_sha256(root / "tenant.json"),
                    "tables": table_files,
                    "objects": objects,
                }
                (root / "manifest.json").write_text(
                    canonical(manifest) + "\n", encoding="utf-8"
                )
                with tarfile.open(output, "w:gz") as archive:
                    for path in sorted(root.rglob("*")):
                        if path.is_file():
                            archive.add(path, arcname=path.relative_to(root))
    finally:
        await connection.close()
    package_sha = file_sha256(output)
    Path(f"{output}.sha256").write_text(
        f"{package_sha}  {output.name}\n", encoding="ascii"
    )
    return {"output": str(output), "sha256": package_sha}


def _extract_verified(package: Path, root: Path) -> dict[str, Any]:
    sidecar = Path(f"{package}.sha256")
    if not sidecar.is_file():
        raise RuntimeError("tenant package checksum sidecar is missing")
    expected = sidecar.read_text(encoding="ascii").split()[0]
    if file_sha256(package) != expected:
        raise RuntimeError("tenant package checksum mismatch")
    with tarfile.open(package, "r:gz") as archive:
        for member in archive.getmembers():
            target = (root / member.name).resolve()
            if root.resolve() not in target.parents:
                raise RuntimeError("unsafe path in tenant package")
        archive.extractall(root, filter="data")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("protocol") != PROTOCOL:
        raise RuntimeError("unsupported tenant package protocol")
    if file_sha256(root / "tenant.json") != manifest["tenant_json_sha256"]:
        raise RuntimeError("tenant metadata checksum mismatch")
    for table, metadata in manifest["tables"].items():
        if file_sha256(root / "tables" / f"{table}.ndjson") != metadata["sha256"]:
            raise RuntimeError(f"tenant table checksum mismatch: {table}")
    for item in manifest["objects"]:
        if file_sha256(root / "objects" / item["sha256"]) != item["sha256"]:
            raise RuntimeError(f"tenant object checksum mismatch: {item['storage_key']}")
    return manifest


async def restore_tenant(args: argparse.Namespace) -> dict[str, Any]:
    package = args.package.resolve()
    with tempfile.TemporaryDirectory(prefix="furniscope-tenant-restore-") as temp:
        root = Path(temp)
        manifest = _extract_verified(package, root)
        if manifest["tenant_code"] != args.confirm_tenant_code:
            raise RuntimeError("tenant-code confirmation does not match package")
        connection = await asyncpg.connect(args.database_url)
        try:
            role = await connection.fetchrow("""
                SELECT r.rolsuper,
                       pg_has_role(current_user,'furniscope_migrator','MEMBER') AS migrator
                  FROM pg_roles r WHERE r.rolname=current_user
            """)
            if not role or not (role["rolsuper"] or role["migrator"]):
                raise RuntimeError("tenant restore requires superuser or furniscope_migrator")
            async with connection.transaction(isolation="serializable"):
                await connection.execute("SET LOCAL search_path TO furniscope,public")
                tenant_count = await connection.fetchval("SELECT count(*) FROM tenants")
                if tenant_count != 0:
                    raise RuntimeError("tenant restore target must be an empty isolated database")
                migrations = [
                    row["version"] for row in await connection.fetch(
                        "SELECT version FROM schema_migrations ORDER BY version"
                    )
                ]
                if migrations != manifest["schema_migrations"]:
                    raise RuntimeError("target schema migration set differs from tenant package")
                await connection.execute("SET LOCAL session_replication_role=replica")
                tenant_payload = (root / "tenant.json").read_text(encoding="utf-8").strip()
                await connection.execute("""
                    INSERT INTO tenants
                    SELECT * FROM jsonb_populate_record(NULL::tenants,$1::jsonb)
                """, tenant_payload)
                restored_counts: dict[str, int] = {}
                for table in sorted(manifest["tables"]):
                    target_table = (
                        "forecast_models" if table == "forecast_models__owner" else table
                    )
                    if not re.fullmatch(r"[a-z][a-z0-9_]*", target_table):
                        raise RuntimeError(f"unsafe table name in tenant package: {target_table}")
                    writable_columns = [
                        row["attname"] for row in await connection.fetch(
                            """
                            SELECT attname
                              FROM pg_attribute
                             WHERE attrelid=$1::regclass
                               AND attnum>0 AND NOT attisdropped
                               AND attgenerated='' AND attidentity<>'a'
                             ORDER BY attnum
                            """,
                            f"furniscope.{target_table}",
                        )
                    ]
                    if not writable_columns:
                        raise RuntimeError(
                            f"tenant restore target has no writable columns: {target_table}"
                        )
                    column_list = ",".join(f'"{column}"' for column in writable_columns)
                    count = 0
                    with (root / "tables" / f"{table}.ndjson").open(
                        encoding="utf-8"
                    ) as stream:
                        for line in stream:
                            await connection.execute(
                                f'INSERT INTO furniscope."{target_table}" ({column_list}) '
                                f'SELECT {column_list} FROM jsonb_populate_record('
                                f'NULL::furniscope."{target_table}",$1::jsonb)',
                                line,
                            )
                            count += 1
                    if count != manifest["tables"][table]["rows"]:
                        raise RuntimeError(f"tenant row count mismatch while restoring {table}")
                    restored_counts[table] = count
                await connection.execute("SET LOCAL session_replication_role=origin")
                sequences = await connection.fetch("""
                    SELECT table_name,column_name,
                           pg_get_serial_sequence(
                             quote_ident(table_schema)||'.'||quote_ident(table_name),
                             column_name
                           ) AS sequence_name
                      FROM information_schema.columns
                     WHERE table_schema='furniscope'
                       AND (
                         column_default LIKE 'nextval(%'
                         OR is_identity='YES'
                       )
                """)
                for row in sequences:
                    if not row["sequence_name"]:
                        continue
                    maximum = await connection.fetchval(
                        f'SELECT max("{row["column_name"]}") '
                        f'FROM furniscope."{row["table_name"]}"'
                    )
                    if maximum is not None:
                        await connection.execute(
                            "SELECT setval($1::regclass,$2,true)",
                            row["sequence_name"], maximum,
                        )
                if manifest["objects"]:
                    if args.object_root is None:
                        raise RuntimeError("package has objects; --object-root is required")
                    object_root = args.object_root.resolve()
                    for item in manifest["objects"]:
                        target = (object_root / item["storage_key"]).resolve()
                        if object_root not in target.parents:
                            raise RuntimeError("object restore path escapes configured root")
                        target.parent.mkdir(parents=True, exist_ok=True)
                        if target.exists():
                            raise RuntimeError(f"object restore target already exists: {target}")
                        target.write_bytes((root / "objects" / item["sha256"]).read_bytes())
                return {
                    "tenant_id": manifest["tenant_id"],
                    "tenant_code": manifest["tenant_code"],
                    "restored_counts": restored_counts,
                    "objects": len(manifest["objects"]),
                }
        finally:
            await connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    export_parser = subparsers.add_parser("export")
    export_parser.add_argument("--database-url", required=True)
    export_parser.add_argument("--tenant-code", required=True)
    export_parser.add_argument("--output", type=Path, required=True)
    export_parser.add_argument("--object-root", type=Path)
    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("--database-url", required=True)
    restore_parser.add_argument("--package", type=Path, required=True)
    restore_parser.add_argument("--confirm-tenant-code", required=True)
    restore_parser.add_argument("--object-root", type=Path)
    args = parser.parse_args()
    result = asyncio.run(export_tenant(args) if args.command == "export" else restore_tenant(args))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
