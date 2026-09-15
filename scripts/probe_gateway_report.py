"""One-shot chat-gateway probe against an existing report task."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time

import asyncpg

from backend.furniscope_api.config import ApiSettings
from backend.furniscope_api.services.model_router_client import ServiceModelRouterClient
from backend.furniscope_agent.external_toolbox import ReportNarrative


def _dsn(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def main(task_id: int | None) -> None:
    settings = ApiSettings()
    if not settings.has_model_router_key():
        raise RuntimeError("MODEL_ROUTER_KEY_MISSING")
    pool = await asyncpg.create_pool(_dsn(settings.database_url), min_size=1, max_size=2)
    client = ServiceModelRouterClient(settings)
    try:
        row = await pool.fetchrow(
            """SELECT t.id, t.tenant_id, t.status, p.sku, p.name product_name,
                      d.name dataset_name, d.authorization_reference,
                      d.listing_count, d.valid_review_count
                 FROM furniscope.analysis_tasks t
                 JOIN furniscope.products p ON p.id=t.product_id
                 JOIN furniscope.market_datasets d ON d.id=t.dataset_id
                WHERE ($1::bigint IS NULL OR t.id=$1)
                  AND EXISTS (
                    SELECT 1 FROM furniscope.market_opportunities o
                     WHERE o.analysis_job_id=t.id)
                ORDER BY t.id DESC LIMIT 1""",
            task_id,
        )
        if row is None:
            raise RuntimeError("no analysis task with opportunities is available")
        opportunities = await pool.fetch(
            """SELECT title, base_score, recommendation_level
                 FROM furniscope.market_opportunities
                WHERE analysis_job_id=$1 ORDER BY base_score DESC""",
            row["id"],
        )
        recommendations = await pool.fetch(
            """SELECT r.recommended_action FROM furniscope.product_recommendations r
                 JOIN furniscope.market_opportunities o ON o.id=r.opportunity_id
                WHERE o.analysis_job_id=$1 ORDER BY r.id""",
            row["id"],
        )
        evidence = {
            "product_name": row["product_name"],
            "sku": row["sku"],
            "dataset": row["dataset_name"],
            "authorization_reference": row["authorization_reference"],
            "listing_count": row["listing_count"],
            "valid_review_count": row["valid_review_count"],
            "opportunity_count": len(opportunities),
            "decision": opportunities[0]["recommendation_level"],
            "top_score": float(opportunities[0]["base_score"]),
            "opportunities": [
                {"title": item["title"], "score": float(item["base_score"]),
                 "level": item["recommendation_level"]}
                for item in opportunities
            ],
            "recommendations": [item["recommended_action"] for item in recommendations],
        }
        started = time.monotonic()
        narrative = await client.structured(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是FurniScope报告撰写器。只根据给定证据写中文JSON："
                        "executive_summary、decision_note。"
                        "禁止编造价格、评分、评论数或来源；数字必须来自证据。"
                    ),
                },
                {"role": "user", "content": json.dumps(evidence, ensure_ascii=False)},
            ],
            output_type=ReportNarrative,
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        digest = hashlib.sha256(json.dumps(evidence, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        run_id = await pool.fetchval(
            """INSERT INTO furniscope.ai_model_runs
               (tenant_id,task_id,provider,model_id,task_type,input_hash,
                output_schema_version,input_tokens,output_tokens,latency_ms,status,retry_count,schema_valid)
               VALUES($1,$2,$3,$4,'report',$5,'report-narrative-v1',$6,$7,$8,'succeeded',0,true)
               RETURNING id""",
            row["tenant_id"], row["id"],
            client.last_chat_provider or "unknown",
            client.last_chat_model or "unknown",
            digest, client.last_input_tokens, client.last_output_tokens, latency_ms,
        )
        await pool.execute(
            """UPDATE furniscope.analysis_reports
                  SET executive_summary=$1, generated_model_run_id=$2, updated_at=now()
                WHERE analysis_job_id=$3 AND tenant_id=$4""",
            (
                f"{narrative.executive_summary.strip()}\n\n决策说明：{narrative.decision_note.strip()}"
                if narrative.decision_note.strip()
                else narrative.executive_summary.strip()
            ),
            run_id, row["id"], row["tenant_id"],
        )
        print(json.dumps({
            "task_id": row["id"],
            "run_id": run_id,
            "provider": client.last_chat_provider,
            "model": client.last_chat_model,
            "latency_ms": latency_ms,
            "input_tokens": client.last_input_tokens,
            "output_tokens": client.last_output_tokens,
        }, ensure_ascii=False))
    finally:
        await client.close()
        await pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", type=int, default=None)
    args = parser.parse_args()
    asyncio.run(main(args.task_id))
