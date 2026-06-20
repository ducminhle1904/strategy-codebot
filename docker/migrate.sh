#!/bin/sh
set -eu

if [ -z "${STRATEGY_CODEBOT_API_DATABASE_URL:-}" ]; then
  echo "error: STRATEGY_CODEBOT_API_DATABASE_URL is required for migrations" >&2
  exit 1
fi

exec alembic -x "database_url=${STRATEGY_CODEBOT_API_DATABASE_URL}" upgrade head
