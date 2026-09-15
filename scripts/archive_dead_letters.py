"""Dump Redis dead letters to disk, then delete only the dead stream."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from pathlib import Path

from backend.furniscope_api.config import ApiSettings
from backend.furniscope_api.job_queue import RedisJobQueue


async def main(archive_path: Path) -> None:
    settings = ApiSettings()
    queue = RedisJobQueue.from_settings(settings)
    try:
        await queue.ping()
        count = await queue.archive_dead_letters(archive_path)
        print(f"archived {count} dead letters to {archive_path}")
        print(f"cleared_at {datetime.now(timezone.utc).isoformat()}")
    finally:
        await queue.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--archive",
        default=f"var/runtime/dead-letters-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json",
    )
    args = parser.parse_args()
    asyncio.run(main(Path(args.archive)))
