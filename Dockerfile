FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src

RUN uv pip install --system -e ".[dev]"

COPY alembic.ini ./
COPY migraciones ./migraciones

EXPOSE 8000

# --factory: la app se construye con crear_app(), que se niega a arrancar sin MC_API_KEY.
CMD ["uvicorn", "--factory", "motor_cartera.api.app:crear_app", "--host", "0.0.0.0", "--port", "8000"]
