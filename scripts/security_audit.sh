#!/bin/sh
set -eu

pattern='(LTAI[0-9A-Za-z]{12,}|AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{16,}|-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----)'

set +e
if command -v rg >/dev/null 2>&1; then
  rg -n --hidden --glob '!frontend/node_modules/**' --glob '!forecast_assets/state/**' \
    "$pattern" backend frontend scripts deploy .github Dockerfile docker-compose.yml .env.example
  scan_status=$?
else
  grep -ERnI --exclude-dir=node_modules --exclude-dir=state \
    "$pattern" backend frontend scripts deploy .github Dockerfile docker-compose.yml .env.example
  scan_status=$?
fi
set -e

if [ "$scan_status" -eq 0 ]; then
  echo "credential-like material found in production-scoped files" >&2
  exit 1
fi
if [ "$scan_status" -ne 1 ]; then
  echo "credential scan failed with status $scan_status" >&2
  exit "$scan_status"
fi
echo "production secret scan passed"
