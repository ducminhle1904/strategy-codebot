# syntax=docker/dockerfile:1.7

FROM python:3.13-slim-bookworm AS builder
COPY --from=ghcr.io/astral-sh/uv:0.9.18 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra live --no-install-project

COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra live

FROM python:3.13-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    STRATEGY_CODEBOT_API_ARTIFACT_ROOT=/var/lib/strategy-codebot/artifacts

RUN groupadd --system strategy-codebot \
    && useradd --system --gid strategy-codebot --home-dir /nonexistent --shell /usr/sbin/nologin strategy-codebot \
    && mkdir -p /app /var/lib/strategy-codebot/artifacts \
    && chown -R strategy-codebot:strategy-codebot /app /var/lib/strategy-codebot

WORKDIR /app

COPY --from=builder --chown=strategy-codebot:strategy-codebot /app /app
COPY --chown=strategy-codebot:strategy-codebot docker/entrypoint.sh /usr/local/bin/strategy-codebot-entrypoint
COPY --chown=strategy-codebot:strategy-codebot docker/migrate.sh /usr/local/bin/strategy-codebot-migrate
RUN chmod 0755 /usr/local/bin/strategy-codebot-entrypoint /usr/local/bin/strategy-codebot-migrate

USER strategy-codebot

EXPOSE 8000

ENTRYPOINT ["strategy-codebot-entrypoint"]
CMD ["uvicorn", "strategy_codebot.server.asgi:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).read()"
