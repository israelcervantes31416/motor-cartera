# La herramienta de respaldo y restauracion (scripts/respaldo.py) con pg_dump, pg_restore y psql 16:
# los del mismo mayor que el servidor, porque es la misma imagen de PostGIS fijada por su digest. Solo
# agrega Python y psycopg de Alpine. Sin servidor: el entrypoint es la herramienta.

FROM postgis/postgis:16-3.5-alpine@sha256:47e961a569fd52ff31f0fe205ed91eeab17d9f5fff6722e6d7ea6b588748b293

# Python y psycopg de Alpine, y las correcciones de seguridad que Alpine publico despues de la base
# (el CI escanea la imagen con Trivy). Sin gosu: lo usa el entrypoint de PostgreSQL para cambiar de
# usuario, y aqui no hay servidor ni cambio de usuario (corre como 70, o como lo pida el compose).
RUN apk add --no-cache python3 py3-psycopg \
    && apk upgrade --no-cache \
    && rm -f /usr/local/bin/gosu

COPY scripts/respaldo.py /usr/local/bin/respaldo.py
COPY src/motor_cartera/__init__.py /tmp/version.py
# La version de la aplicacion que el manifiesto declara: la del paquete, en el momento de construir.
# El punto de montaje del almacen es del usuario de la aplicacion (10001), como en su imagen: un
# volumen nuevo que nace al restaurar, antes de que la API arranque, nace con ese dueno.
RUN mkdir -p /usr/local/share/motor-cartera /var/lib/motor-cartera/fuentes \
    && sed -n 's/^__version__ = "\(.*\)"$/\1/p' /tmp/version.py > /usr/local/share/motor-cartera/VERSION \
    && rm /tmp/version.py \
    && chown 10001:10001 /var/lib/motor-cartera/fuentes

USER 70:70
WORKDIR /respaldos
ENTRYPOINT ["python3", "/usr/local/bin/respaldo.py"]
CMD ["--help"]
