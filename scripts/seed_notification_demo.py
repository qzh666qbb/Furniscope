"""Seed idempotent notification channels and delivery history for one tenant."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
from typing import Any

from sqlalchemy import text

from furniscope_api.config import get_settings
from furniscope_api.database import Database


SEED_SOURCE = "operational-baseline-v1"
LEGACY_SEED_SOURCE = "notification-demo-v1"

DEMO_CHANNELS = (
    {
        "key": "market-ops",
        "name": "市场运营钉钉群",
        "channel_type": "dingtalk",
        "target_url": "https://alerts.example.invalid/furniscope/market-ops",
        "events": ["competitor_alert"],
        "enabled": True,
    },
    {
        "key": "compliance",
        "name": "跨境合规 Slack",
        "channel_type": "slack",
        "target_url": "https://alerts.example.invalid/furniscope/compliance",
        "events": ["policy_alert"],
        "enabled": True,
    },
    {
        "key": "management",
        "name": "管理层邮件网关",
        "channel_type": "email_gateway",
        "target_url": "https://alerts.example.invalid/furniscope/management",
        "events": ["competitor_alert", "policy_alert"],
        "enabled": False,
    },
)

DEMO_EVENTS = (
    {
        "key": "competitor-price-change",
        "channel": "market-ops",
        "event_type": "competitor_alert",
        "title": "竞品价格变化：重点休闲椅进入促销",
        "content": "监控商品价格较观察基线下降 8.4%，建议复核本周价格策略。",
        "status": "delivered",
        "attempts": 1,
        "age_minutes": 18,
        "payload": {"change_type": "price", "severity": "medium"},
    },
    {
        "key": "competitor-new-listing",
        "channel": "market-ops",
        "event_type": "competitor_alert",
        "title": "竞品上新：带脚凳休闲椅进入观察范围",
        "content": "新商品已纳入竞品观察基线，后续将持续跟踪价格和评论变化。",
        "status": "delivered",
        "attempts": 1,
        "age_minutes": 145,
        "payload": {"change_type": "new_listing", "severity": "medium"},
    },
    {
        "key": "policy-recall-risk",
        "channel": "compliance",
        "event_type": "policy_alert",
        "title": "美国 CPSC：休闲椅电池召回风险",
        "content": "监测到与家具电池过热相关的召回信息，请核对产品适用范围和供应链物料。",
        "status": "delivered",
        "attempts": 1,
        "age_minutes": 310,
        "payload": {"source": "CPSC", "severity": "high"},
    },
    {
        "key": "management-delivery-failed",
        "channel": "management",
        "event_type": "policy_alert",
        "title": "管理层邮件摘要投递失败",
        "content": "管理层邮件网关尚未完成接收端配置，投递任务已停止重试。",
        "status": "failed",
        "attempts": 5,
        "age_minutes": 1_450,
        "last_error": "EndpointNotConfigured",
        "payload": {"severity": "medium"},
    },
)


async def seed_notification_demo(
    database: Database,
    *,
    email: str,
) -> dict[str, Any]:
    async with database.session_factory() as session:
        actor = (
            await session.execute(
                text(
                    """
                    SELECT u.id user_id,u.tenant_id,t.tenant_code,t.name tenant_name
                      FROM users u
                      JOIN tenants t ON t.id=u.tenant_id
                     WHERE lower(u.email)=lower(:email)
                       AND u.status='active' AND t.status='active'
                    """
                ),
                {"email": email},
            )
        ).mappings().one_or_none()
        if actor is None:
            raise RuntimeError(f"active tenant user not found: {email}")

        channel_ids: dict[str, int] = {}
        for channel in DEMO_CHANNELS:
            channel_id = await session.scalar(
                text(
                    """
                    INSERT INTO notification_channels(
                      tenant_id,name,channel_type,target_url,secret_env,
                      events,enabled,created_by
                    ) VALUES(
                      :tenant,:name,:channel_type,:target_url,NULL,
                      CAST(:events AS jsonb),:enabled,:user
                    )
                    ON CONFLICT(tenant_id,target_url) DO UPDATE SET
                      name=EXCLUDED.name,
                      channel_type=EXCLUDED.channel_type,
                      secret_env=NULL,
                      events=EXCLUDED.events,
                      enabled=EXCLUDED.enabled
                    RETURNING id
                    """
                ),
                {
                    "tenant": actor["tenant_id"],
                    "user": actor["user_id"],
                    "name": channel["name"],
                    "channel_type": channel["channel_type"],
                    "target_url": channel["target_url"],
                    "events": json.dumps(channel["events"], ensure_ascii=False),
                    "enabled": channel["enabled"],
                },
            )
            channel_ids[channel["key"]] = int(channel_id)

        now = datetime.now(timezone.utc)
        for event in DEMO_EVENTS:
            payload = {
                **event["payload"],
                "seed_source": SEED_SOURCE,
                "seed_key": event["key"],
            }
            created_at = now - timedelta(minutes=event["age_minutes"])
            delivered_at = (
                created_at + timedelta(minutes=1)
                if event["status"] == "delivered"
                else None
            )
            params = {
                "tenant": actor["tenant_id"],
                "channel": channel_ids[event["channel"]],
                "event_type": event["event_type"],
                "title": event["title"],
                "content": event["content"],
                "payload": json.dumps(payload, ensure_ascii=False),
                "status": event["status"],
                "attempts": event["attempts"],
                "last_error": event.get("last_error"),
                "created_at": created_at,
                "delivered_at": delivered_at,
                "seed_source": SEED_SOURCE,
                "legacy_seed_source": LEGACY_SEED_SOURCE,
                "seed_key": event["key"],
            }
            event_id = await session.scalar(
                text(
                    """
                    UPDATE notification_events SET
                      channel_id=:channel,
                      event_type=:event_type,
                      title=:title,
                      content=:content,
                      payload=CAST(:payload AS jsonb),
                      status=:status,
                      attempts=:attempts,
                      last_error=:last_error,
                      created_at=:created_at,
                      delivered_at=:delivered_at
                     WHERE id=(
                       SELECT id FROM notification_events
                        WHERE tenant_id=:tenant
                          AND payload->>'seed_source' IN (
                            :seed_source,:legacy_seed_source
                          )
                          AND payload->>'seed_key'=:seed_key
                        ORDER BY id
                        LIMIT 1
                     )
                    RETURNING id
                    """
                ),
                params,
            )
            if event_id is None:
                await session.execute(
                    text(
                        """
                        INSERT INTO notification_events(
                          tenant_id,channel_id,event_type,title,content,payload,
                          status,attempts,last_error,created_at,delivered_at
                        ) VALUES(
                          :tenant,:channel,:event_type,:title,:content,
                          CAST(:payload AS jsonb),:status,:attempts,:last_error,
                          :created_at,:delivered_at
                        )
                        """
                    ),
                    params,
                )

        await session.commit()
        summary = (
            await session.execute(
                text(
                    """
                    SELECT
                      count(DISTINCT c.id)::int channel_count,
                      count(DISTINCT c.id) FILTER (WHERE c.enabled)::int enabled_count,
                      count(DISTINCT e.id)::int event_count,
                      count(DISTINCT e.id) FILTER (
                        WHERE e.status='delivered'
                      )::int delivered_count,
                      count(DISTINCT e.id) FILTER (
                        WHERE e.status='failed'
                      )::int failed_count
                      FROM notification_channels c
                      LEFT JOIN notification_events e
                        ON e.channel_id=c.id AND e.tenant_id=c.tenant_id
                       AND e.payload->>'seed_source'=:seed_source
                     WHERE c.tenant_id=:tenant
                       AND c.target_url=ANY(CAST(:target_urls AS text[]))
                    """
                ),
                {
                    "tenant": actor["tenant_id"],
                    "seed_source": SEED_SOURCE,
                    "target_urls": [
                        channel["target_url"] for channel in DEMO_CHANNELS
                    ],
                },
            )
        ).mappings().one()
        return {
            "tenant_id": int(actor["tenant_id"]),
            "tenant_code": actor["tenant_code"],
            **{key: int(value or 0) for key, value in summary.items()},
        }


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", default="hefeng@furniscope.local")
    args = parser.parse_args()

    database = Database(get_settings())
    try:
        result = await seed_notification_demo(database, email=args.email)
    finally:
        await database.close()
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
