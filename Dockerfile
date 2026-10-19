# La imagen de la API, el worker y las migraciones: el mismo codigo, otro comando.
#
# Tres etapas, todas sobre imagenes fijadas por su digest:
#   - construccion: el entorno con las versiones exactas del uv.lock, sin las dependencias de
#     desarrollo, y el proyecto instalado como paquete (no editable), con su bytecode compilado.
#   - desarrollo: la del compose de desarrollo, que monta el repositorio sobre /app: con las
#     dependencias de desarrollo, root y el proyecto editable. Solo para una maquina de desarrollo.
#   - produccion (la ultima, la que se construye sin --target): solo el entorno y las migraciones,
#     sin uv, sin compiladores ni el codigo fuente aparte, como el usuario 10001 sin shell. Corre con
#     el sistema de archivos de solo lectura (el compose de produccion monta /tmp en memoria).

FROM ghcr.io/astral-sh/uv:0.12.21@sha256:a7aed3216253ee804de3e2d8afa5073baa1a177335345d43845cd4165e43b711 AS uv

FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS base

# El entorno va en /opt/venv y no en /app/.venv: el compose de desarrollo monta el repositorio sobre
# /app y lo taparia. Sin descargas, uv usa el Python 3.12 de la imagen.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    PATH=/opt/venv/bin:$PATH

FROM base AS construccion
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /construccion
# Primero las dependencias, en su propia capa: cambian menos que el codigo.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-install-project --no-editable
COPY src ./src
RUN uv sync --locked --no-editable

FROM base AS desarrollo
COPY --from=uv /uv /usr/local/bin/uv
ENV PYTHONPATH=/app/src
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
# Las versiones exactas del uv.lock, como en el CI, con las de desarrollo.
RUN uv sync --locked --extra dev
COPY alembic.ini ./
COPY migraciones ./migraciones
EXPOSE 8000
CMD ["uvicorn", "--factory", "motor_cartera.api.app:crear_app", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS produccion
# Un usuario sin privilegios, sin shell de inicio de sesion ni directorio propio. El almacen de
# artefactos nace suyo: un volumen nuevo hereda ese dueno.
RUN groupadd --system --gid 10001 motor \
    && useradd --system --uid 10001 --gid motor --no-create-home --home-dir /nonexistent \
        --shell /usr/sbin/nologin motor \
    && mkdir -p /var/lib/motor-cartera/fuentes \
    && chown 10001:10001 /var/lib/motor-cartera/fuentes
COPY --from=construccion /opt/venv /opt/venv
WORKDIR /app
COPY alembic.ini ./
COPY migraciones ./migraciones
USER 10001:10001
EXPOSE 8000 9464
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2)"]
# --factory: la app se construye con crear_app(), que valida la configuracion y no arranca con una
# invalida. Sin log de accesos (cada peticion ya deja su evento JSON), sin cabecera `server`, sin
# confiar en X-Forwarded-* de nadie, y con 20 s para terminar lo que atiende al recibir SIGTERM.
CMD ["uvicorn", "--factory", "motor_cartera.api.app:crear_app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--no-server-header", "--no-proxy-headers", "--timeout-graceful-shutdown", "20"]
