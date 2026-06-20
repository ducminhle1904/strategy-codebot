#!/bin/sh
set -eu

file_env() {
  var="$1"
  file_var="${var}_FILE"
  value="${2:-}"
  eval "current=\${$var:-}"
  eval "file_current=\${$file_var:-}"
  if [ "$current" ] && [ "$file_current" ]; then
    echo "error: both $var and $file_var are set" >&2
    exit 1
  fi
  if [ "$current" ]; then
    value="$current"
  elif [ "$file_current" ]; then
    value="$(cat "$file_current")"
  fi
  export "$var=$value"
  unset "$file_var"
}

file_env OPENAI_API_KEY
file_env OPENROUTER_API_KEY
file_env LITELLM_PROXY_API_KEY
file_env VERCEL_AI_GATEWAY_API_KEY
file_env PORTKEY_API_KEY
file_env GROQ_API_KEY
file_env TOGETHER_API_KEY
file_env FIREWORKS_API_KEY
file_env DEEPINFRA_API_KEY
file_env POSTGRES_PASSWORD
file_env REDIS_PASSWORD

POSTGRES_HOST="${POSTGRES_HOST:-postgres}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_DB="${POSTGRES_DB:-strategy_codebot}"
POSTGRES_USER="${POSTGRES_USER:-strategy_codebot}"
REDIS_HOST="${REDIS_HOST:-redis}"
REDIS_PORT="${REDIS_PORT:-6379}"

if [ -z "${STRATEGY_CODEBOT_API_DATABASE_URL:-}" ] && [ -n "${POSTGRES_PASSWORD:-}" ]; then
  export STRATEGY_CODEBOT_API_DATABASE_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}"
fi

if [ -z "${STRATEGY_CODEBOT_KNOWLEDGE_DATABASE_URL:-}" ] && [ -n "${POSTGRES_PASSWORD:-}" ]; then
  export STRATEGY_CODEBOT_KNOWLEDGE_DATABASE_URL="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}"
fi

if [ -z "${STRATEGY_CODEBOT_API_REDIS_URL:-}" ] && [ -n "${REDIS_PASSWORD:-}" ]; then
  export STRATEGY_CODEBOT_API_REDIS_URL="redis://:${REDIS_PASSWORD}@${REDIS_HOST}:${REDIS_PORT}/0"
fi

exec "$@"
