"""Serve isolated synthetic fixtures for enterprise-live.spec.js.

Initialize the dedicated database with furniscope_postgresql_v3.sql first.
Run with PYTHONPATH=.:backend:tests; never point this helper at a business DB.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn

from furniscope_api.app import create_app
from test_enterprise_http import fixture_settings, seed_enterprise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8016)
    args = parser.parse_args()
    database = urlsplit(args.database_url)
    if (database.scheme != "postgresql"
            or database.hostname not in {"localhost", "127.0.0.1"}
            or not database.path.startswith("/furniscope_enterprise_test_")):
        parser.error("Use a local isolated database named furniscope_enterprise_test_<suffix>.")
    args.work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    settings = fixture_settings(args.database_url, args.work_dir)
    identities = asyncio.run(seed_enterprise(settings))
    identity_file = args.work_dir / "identities.json"
    descriptor = os.open(identity_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        os.fchmod(handle.fileno(), 0o600)
        json.dump(identities, handle)
    print(f"Synthetic fixture ready: {identity_file}; credentials are not logged.", flush=True)
    uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
