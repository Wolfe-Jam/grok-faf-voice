ARG PYTHON_VERSION=3.12
FROM ghcr.io/astral-sh/uv:python${PYTHON_VERSION}-bookworm-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml uv.lock* ./
COPY grok_faf_voice/ ./grok_faf_voice/
COPY radiofaf.faf.example ./radiofaf.faf

ENV UV_LINK_MODE=copy
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8080

CMD ["uv", "run", "radiofaf-crew", "--crew"]
