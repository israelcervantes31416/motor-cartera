#!/bin/sh
# Los roles de la base del compose de produccion, uno por servicio, creados la primera vez que
# PostgreSQL inicializa su volumen (lo corre el entrypoint de la imagen de PostGIS, despues de crear
# sus extensiones). Las contrasenas vienen de los secretos montados, nunca de este archivo.
#
#   mc_migraciones  dueno de la base y de su esquema: el unico que crea, altera o borra objetos.
#   mc_api          lee y escribe filas (DML). No crea tablas, no las altera, no las vacia.
#   mc_worker       lo mismo que la API, con su propia credencial: una no sirve por la otra.
#   mc_respaldo     solo lee (pg_read_all_data): lo que necesita pg_dump.
#
# Lo que crea mc_migraciones (las migraciones, una restauracion) da DML a la API y al worker por los
# privilegios por omision: una tabla nueva no necesita un GRANT a mano.
set -eu

secreto() {
    tr -d '\r\n' < "/run/secrets/$1"
}

psql -v ON_ERROR_STOP=1 --no-psqlrc --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    -v base="$POSTGRES_DB" \
    -v migraciones="$(secreto db_migraciones)" \
    -v api="$(secreto db_api)" \
    -v worker="$(secreto db_worker)" \
    -v respaldo="$(secreto db_respaldo)" <<'SQL'
CREATE ROLE mc_migraciones LOGIN PASSWORD :'migraciones';
CREATE ROLE mc_api LOGIN PASSWORD :'api' CONNECTION LIMIT 40;
CREATE ROLE mc_worker LOGIN PASSWORD :'worker' CONNECTION LIMIT 40;
CREATE ROLE mc_respaldo LOGIN PASSWORD :'respaldo' CONNECTION LIMIT 4;
GRANT pg_read_all_data TO mc_respaldo;

ALTER DATABASE :"base" OWNER TO mc_migraciones;
REVOKE ALL ON DATABASE :"base" FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE :"base" TO mc_api, mc_worker, mc_respaldo;
GRANT ALL ON SCHEMA public TO mc_migraciones;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO mc_api, mc_worker, mc_respaldo;

ALTER DEFAULT PRIVILEGES FOR ROLE mc_migraciones IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO mc_api, mc_worker;
ALTER DEFAULT PRIVILEGES FOR ROLE mc_migraciones IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO mc_api, mc_worker;
ALTER DEFAULT PRIVILEGES FOR ROLE mc_migraciones IN SCHEMA public
    GRANT EXECUTE ON FUNCTIONS TO mc_api, mc_worker;
SQL
