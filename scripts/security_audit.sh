#!/bin/sh
set -eu

pattern='(LTAI[0-9A-Za-z]{12,}|AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{16,}|-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----)'
if rg -n --hidden --glob '!frontend/node_modules/**' --glob '!forecast_assets/state/**' \
  "$pattern" backend frontend scripts deploy .github Dockerfile docker-compose.yml .env.example; then
  echo "credential-like material found in production-scoped files" >&2
  exit 1
fi
echo "production secret scan passed"
