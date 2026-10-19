# Runbook: la base no responde

En los comandos, `dcp` es `docker compose -f docker-compose.production.yml` ([índice](README.md)).

**Alertas:** `MCReadinessFallando`, `MCLecturaDeColaFallando`, `MCPoolDeConexionesAgotado`; en la
nube, la CPU de Cloud SQL.

**Impacto:** la API responde `503` en lo que lee de la base; el worker no toma trabajos. **Nada se
pierde**: lo confirmado sigue en la base, y lo que la cola tenía se retoma al volver.

## Qué esperar (medido en el simulacro)

Con PostgreSQL detenido, la API y el worker dejan de estar listos en menos de un segundo, **siguen
vivos** (Docker no los reinicia: su liveness no depende de la base) y su readiness dice
`base_de_datos: sin_conexion` sin exponer la conexión. Al volver la base, los dos se recuperan solos
en 1.1 s, sin reiniciarse ([production.md](../production.md#los-simulacros)).

## Diagnóstico

```bash
dcp ps --all postgres
dcp logs --since 15m postgres | tail -50
dcp exec -T postgres sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

- **Caída**: su log (disco lleno, memoria, una corrupción).
- **Arriba pero saturada**: `MCPoolDeConexionesAgotado` o la latencia de todas las rutas. Las
  sentencias en curso y quién espera:

  ```bash
  dcp exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT pid, usename, state, wait_event_type, now() - query_start AS hace FROM pg_stat_activity WHERE datname = current_database() ORDER BY query_start"'
  ```

  Cada servicio tiene su rol (`mc_api`, `mc_worker`, `mc_respaldo`): se ve quién carga la base.
- **El esquema no es el esperado**: la readiness dice `esquema: incompatible`; las migraciones no
  corrieron o la base es de otra versión: `dcp run --rm --no-deps migraciones alembic current`.
- **`MCLecturaDeColaFallando`** con la base sana: la consulta de la cola de la API falla (su log dice
  la clase del error).

## Mitigación

- **Caída**: `dcp start postgres` (o `dcp up -d postgres`). La API y el worker se recuperan solos.
- **Disco lleno**: liberar espacio del volumen `pgdata` (nunca borrando archivos de la base a mano);
  si no se puede, restaurar en un volumen nuevo más grande ([backup-restore.md](backup-restore.md)).
- **Saturada**: cancelar la sentencia que la bloquea (`SELECT pg_cancel_backend(<pid>)`), bajar la
  carga (el edge limita por IP), y después buscar el índice o la consulta.
- **Corrupción**: no repararla en el lugar: restaurar el último respaldo verificado en un entorno
  nuevo ([disaster-recovery.md](disaster-recovery.md)).

## Verificación

`dcp ps` con la API y el worker `healthy`; `curl` de la readiness desde la red interna (`dcp
--profile herramientas run --rm herramientas` corre la prueba de humo interna, que la revisa).
