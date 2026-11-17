# Build dependencies once; runtime never downloads Python packages.
FROM ghcr.io/astral-sh/uv:0.9.16 AS uv
FROM python:3.12-slim-bookworm AS builder
COPY --from=uv /uv /uvx /usr/local/bin/
WORKDIR /app
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim-bookworm AS runtime
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    MCP_TRANSPORT=streamable-http \
    HOST=0.0.0.0 \
    PORT=8000 \
    MCP_PATH=/mcp \
    STATELESS_HTTP=1 \
    JSON_RESPONSE=1
COPY --from=builder --chown=10001:10001 /app/.venv /app/.venv
USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["/app/.venv/bin/open-notebook-mcp"]
