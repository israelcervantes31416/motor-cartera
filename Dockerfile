FROM python:3.12-slim

# El entorno va en /opt/venv y no en /app/.venv: el compose monta el repositorio sobre /app y
# lo taparia. Sin descargas, uv usa el Python 3.12 de la imagen.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    PATH=/opt/venv/bin:$PATH

# uv, en la version con que se genero el uv.lock (la misma del CI). El digest la fija aunque
# la etiqueta se mueva.
COPY --from=ghcr.io/astral-sh/uv:0.12.21@sha256:a7aed3216253ee804de3e2d8afa5073baa1a177335345d43845cd4165e43b711 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src

# Las versiones exactas del uv.lock, como en el CI.
RUN uv sync --locked --extra dev

COPY alembic.ini ./
COPY migraciones ./migraciones

EXPOSE 8000

# --factory: la app se construye con crear_app(), que se niega a arrancar sin MC_API_KEY.
CMD ["uvicorn", "--factory", "motor_cartera.api.app:crear_app", "--host", "0.0.0.0", "--port", "8000"]
