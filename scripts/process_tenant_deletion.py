"""Execute one due tenant-erasure request and emit an immutable certificate."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import asyncpg


RETAINED_TABLES = {
    "audit_logs",
    "tenant_deletion_certificates",
    "tenant_deletion_requests",
    "tenant_legal_holds",
    "tenants",
    "users",
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _delete_local_objects(storage_root: Path | None, keys: list[str]) -> list[dict[str, Any]]:
    manifest: list[dict[str, Any]] = []
    root = storage_root.resolve() if storage_root else None
    for key in sorted(set(keys)):
        item: dict[str, Any] = {"storage_key_sha256": hashlib.sha256(key.encode()).hexdigest()}
        if root is None:
            item["status"] = "external_deletion_required"
            manifest.append(item)
            continue
        candidate = (root / key).resolve()
        if root not in candidate.parents:
            raise RuntimeError(f"storage key escapes configured root: {key}")
        if candidate.is_file():
            candidate.unlink()
            item["status"] = "deleted"
        else:
            item["status"] = "not_found"
        manifest.append(item)
    return manifest


async def execute(args: argparse.Namespace) -> dict[str, Any]:
    connection = await asyncpg.connect(args.database_url)
    try:
        role = await connection.fetchrow("""
            SELECT current_user AS role_name,r.rolsuper,r.rolbypassrls,
                   pg_has_role(current_user,'furniscope_migrator','MEMBER') AS migrator
              FROM pg_roles r WHERE r.rolname=current_user
        """)
        if not role or not (role["rolsuper"] or role["migrator"]):
            raise RuntimeError("tenant erasure requires a superuser or furniscope_migrator identity")

        async with connection.transaction(isolation="serializable"):
            await connection.execute("SET LOCAL search_path TO furniscope,public")
            request = await connection.fetchrow("""
                SELECT d.*,t.tenant_code,t.name AS tenant_name,t.status AS tenant_status
                  FROM tenant_deletion_requests d
                  JOIN tenants t ON t.id=d.tenant_id
                 WHERE d.request_uuid=$1::uuid
                 FOR UPDATE OF d,t
            """, args.request_uuid)
            if request is None:
                raise RuntimeError("deletion request does not exist")
            if request["tenant_code_snapshot"] != args.confirm_tenant_code:
                raise RuntimeError("tenant-code confirmation does not match the deletion request")
            if request["status"] != "scheduled":
                raise RuntimeError(f"deletion request is not executable: {request['status']}")
            due = await connection.fetchval("SELECT $1::timestamptz<=now()", request["scheduled_for"])
            if not due:
                raise RuntimeError("deletion retention deadline has not elapsed")
            hold = await connection.fetchval("""
                SELECT EXISTS(
                  SELECT FROM tenant_legal_holds
                   WHERE tenant_id=$1 AND released_at IS NULL
                )
            """, request["tenant_id"])
            if hold:
                await connection.execute("""
                    UPDATE tenant_deletion_requests
                       SET status='blocked',updated_at=now(),
                           failure_code='LEGAL_HOLD',
                           failure_detail='active legal hold prevents deletion'
                     WHERE id=$1
                """, request["id"])
                raise RuntimeError("active legal hold prevents deletion")

            await connection.execute("""
                UPDATE tenant_deletion_requests
                   SET status='running',started_at=now(),updated_at=now(),
                       failure_code=NULL,failure_detail=NULL
                 WHERE id=$1
            """, request["id"])
            started_at = await connection.fetchval("SELECT now()")
            storage_keys = await connection.fetch("""
                SELECT storage_key FROM file_assets
                 WHERE tenant_id=$1 AND storage_key IS NOT NULL
            """, request["tenant_id"])
            object_manifest = _delete_local_objects(
                args.storage_root, [row["storage_key"] for row in storage_keys]
            )
            tables = await connection.fetch("""
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
            deleted_counts: dict[str, int] = {}
            await connection.execute("SET LOCAL session_replication_role=replica")
            for row in tables:
                table = row["table_name"]
                if table in RETAINED_TABLES:
                    continue
                result = await connection.execute(
                    f'DELETE FROM furniscope."{table}" WHERE tenant_id=$1',
                    request["tenant_id"],
                )
                deleted_counts[table] = int(result.rsplit(" ", 1)[-1])
            result = await connection.execute(
                "DELETE FROM furniscope.forecast_models WHERE owner_tenant_id=$1",
                request["tenant_id"],
            )
            deleted_counts["forecast_models"] = int(result.rsplit(" ", 1)[-1])
            await connection.execute("""
                UPDATE furniscope.registration_applications
                   SET approved_tenant_id=NULL
                 WHERE approved_tenant_id=$1
            """, request["tenant_id"])
            tenant_digest = hashlib.sha256(request["tenant_code_snapshot"].encode()).hexdigest()
            await connection.execute("""
                UPDATE furniscope.users
                   SET email='deleted+'||tenant_id||'+'||id||'@invalid.local',
                       password_hash='!deleted:'||encode(gen_random_bytes(24),'hex'),
                       phone=NULL,name='Deleted User',department=NULL,job_title=NULL,
                       status='disabled',updated_at=now()
                 WHERE tenant_id=$1
            """, request["tenant_id"])
            await connection.execute("""
                UPDATE furniscope.tenants
                   SET tenant_code=$2,name='Deleted Tenant '||id,status='closed',
                       entitlements='[]'::jsonb,updated_at=now()
                 WHERE id=$1
            """, request["tenant_id"], f"DELETED_{request['tenant_id']}_{tenant_digest[:8].upper()}")
            await connection.execute("SET LOCAL session_replication_role=origin")

            retained_counts = {
                "audit_logs": int(await connection.fetchval(
                    "SELECT count(*) FROM audit_logs WHERE tenant_id=$1", request["tenant_id"]
                )),
                "users_anonymized": int(await connection.fetchval(
                    "SELECT count(*) FROM users WHERE tenant_id=$1", request["tenant_id"]
                )),
                "legal_holds": int(await connection.fetchval(
                    "SELECT count(*) FROM tenant_legal_holds WHERE tenant_id=$1",
                    request["tenant_id"],
                )),
                "deletion_requests": 1,
            }
            completed_at = await connection.fetchval("SELECT now()")
            evidence = {
                "request_uuid": str(request["request_uuid"]),
                "tenant_id": request["tenant_id"],
                "tenant_code_digest": tenant_digest,
                "started_at": started_at,
                "completed_at": completed_at,
                "deleted_row_counts": deleted_counts,
                "retained_record_counts": retained_counts,
                "deleted_object_manifest": object_manifest,
                "executor_identity": role["role_name"],
            }
            evidence_hash = hashlib.sha256(_canonical(evidence).encode()).hexdigest()
            certificate = await connection.fetchrow("""
                INSERT INTO tenant_deletion_certificates(
                  request_uuid,tenant_id,tenant_code_digest,started_at,completed_at,
                  deleted_row_counts,retained_record_counts,deleted_object_manifest,
                  executor_identity,evidence_hash
                ) VALUES(
                  $1::uuid,$2,$3,$4,$5,$6::jsonb,$7::jsonb,$8::jsonb,$9,$10
                )
                RETURNING certificate_uuid
            """, request["request_uuid"], request["tenant_id"], tenant_digest,
                started_at, completed_at, _canonical(deleted_counts),
                _canonical(retained_counts), _canonical(object_manifest),
                role["role_name"], evidence_hash)
            await connection.execute("""
                UPDATE tenant_deletion_requests
                   SET status='completed',completed_at=$2,updated_at=now()
                 WHERE id=$1
            """, request["id"], completed_at)
            return {
                **evidence,
                "certificate_uuid": str(certificate["certificate_uuid"]),
                "evidence_hash": evidence_hash,
            }
    finally:
        await connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--request-uuid", required=True)
    parser.add_argument("--confirm-tenant-code", required=True)
    parser.add_argument("--storage-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(execute(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(_canonical(result) + "\n", encoding="utf-8")
    print(json.dumps({
        "certificate_uuid": result["certificate_uuid"],
        "evidence_hash": result["evidence_hash"],
    }))


if __name__ == "__main__":
    main()
