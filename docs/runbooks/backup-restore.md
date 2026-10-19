# Runbook: el respaldo falló, o hay que restaurar

En los comandos, `dcp` es `docker compose -f docker-compose.production.yml` ([índice](README.md)).

**Alertas:** en la nube, "el respaldo nocturno falló"; en local, el código de salida del respaldo
programado distinto de 0.

El formato, lo que se respalda y por qué, en [backups.md](../backups.md).

## El respaldo falló

1. **Su salida** es una línea JSON con `resultado` y `codigo`:

   | Código | Qué hacer |
   | --- | --- |
   | `SIN_DSN` | falta `MC_RESPALDO_DSN_FILE` (el secreto `dsn_respaldo`) |
   | `ALMACEN_NO_MONTADO` | el almacén no está montado en el servicio `respaldo` |
   | `ALMACEN_INCOMPLETO` | la base registra un artefacto que el almacén no tiene: el almacén perdió un objeto. **Es un incidente**: `dcp exec -T worker motor-cartera verificar-fuentes` dice cuáles; se reponen desde el último respaldo que los tenga |
   | `ALMACEN_DANADO` | un objeto ya no tiene los bytes de su SHA-256: lo mismo |
   | `PG_DUMP_ANTERIOR` | `pg_dump` de un mayor anterior al servidor: la imagen de respaldo no corresponde |
   | `PROCESO_FALLO` (código 2) | `pg_dump` falló: su error está en la salida de error (conexión, permisos) |

2. Un respaldo que falla **no deja nada**: no hay un directorio a medias que alguien tome por bueno.
3. Corregido, respaldar otra vez y verificarlo.

## Restaurar

**Nunca sobre la base en uso**: la herramienta se niega (`BASE_NO_VACIA`, `DESTINO_ES_EL_ORIGEN`). Se
restaura en un entorno nuevo y después se cambia a él.

```bash
# 0. El respaldo, verificado
dcp --profile restauracion run --rm --no-deps restauracion verificar /respaldos/<respaldo>

# 1. Un entorno nuevo: los volumenes de la base y del almacen recien creados
#    (en local: otro nombre de proyecto, o despues de respaldar y apartar los viejos)
dcp up -d --wait postgres

# 2. Restaurar (copia el almacen y la base; valida filas, restricciones y artefactos)
dcp --profile restauracion run --rm --no-deps restauracion \
  restaurar /respaldos/<respaldo> --revision-esperada 0017

# 3. El resto
dcp up -d --wait
dcp run --rm --no-deps migraciones alembic current     # 0017 (head)
dcp exec -T worker motor-cartera verificar-fuentes
```

El informe dice `VALIDADA` (o `VALIDADA_SIN_ALMACEN` si se restauró con `--sin-almacen` a propósito)
o `INVALIDA` con sus diferencias. Un respaldo de otra revisión se niega (`REVISION_INCOMPATIBLE`):
se restaura con la versión de la aplicación que le corresponde, o con `--permitir-otra-revision`
sabiendo que después hay que migrar.

Antes de confiar en un respaldo nuevo de verdad, el simulacro:
`python scripts/simulacro_de_restauracion.py respaldos/<respaldo>` sobre un PostgreSQL vacío.

## En la nube

Además del respaldo de la herramienta, Cloud SQL tiene sus respaldos automáticos y PITR: para volver
la base a un minuto anterior (un error humano), se restaura un **clon** de la instancia a ese punto y
se cambia a él; el almacén se restaura del respaldo nocturno más cercano anterior. Nunca se restaura
sobre la instancia en uso.

## Verificación

Las tres pruebas de humo de producción sobre el entorno restaurado y `verificar-fuentes` con todos los
artefactos intactos.
