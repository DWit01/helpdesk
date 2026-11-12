FROM python:3.14-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.21 /uv /bin/uv

WORKDIR /app
ENV UV_PYTHON_DOWNLOADS=0

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

FROM python:3.14-slim AS runtime

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY app/ /app/app/
COPY alembic.ini /app/alembic.ini
COPY migrations/ /app/migrations/

ENV PATH="/app/.venv/bin:$PATH"
USER 65532:65532

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]