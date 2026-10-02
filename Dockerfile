# syntax=docker/dockerfile:1

FROM python:3.12-slim-bookworm AS build

COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY README.md LICENSE ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


FROM python:3.12-slim-bookworm

ENV PATH="/app/.venv/bin:$PATH" \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fonts-noto-cjk fonts-noto-color-emoji tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /app /app

RUN playwright install --with-deps --only-shell chromium \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 clutchbot \
    && mkdir -p /data \
    && chown clutchbot:clutchbot /data
USER clutchbot
WORKDIR /app
VOLUME /data

HEALTHCHECK --interval=1m --timeout=30s --start-period=5m --retries=3 \
    CMD ["clutchbot", "healthcheck"]

ENTRYPOINT ["clutchbot"]
CMD ["listen"]
