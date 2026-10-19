# Runbook: recuperación ante desastres

En los comandos, `dcp` es `docker compose -f docker-compose.production.yml` ([índice](README.md)).

Cuando el entorno entero se perdió o no es confiable (la máquina, el volumen de la base, el
almacén, una corrupción, un compromiso). Objetivos: RPO de un día con la herramienta (minutos para la
base con el PITR de Cloud SQL) y RTO de una hora ([slos.md](../slos.md#recuperación-ante-desastres-rpo-y-rto)).

## Qué se necesita

1. **El último respaldo verificado** (`respaldos/motor-cartera-<fecha>`, o el bucket de respaldos en
   la nube), con su manifiesto.
2. **El código de la versión del respaldo** (`app_version` y `revision_esquema` del manifiesto): la
   etiqueta de git correspondiente.
3. **Secretos nuevos**: después de un compromiso, nunca los viejos.
4. **La red vial** (si se usaba): se vuelve a preparar desde su extracto; su huella está en la base.

## Procedimiento

1. **Declarar el incidente** y anotar la hora: lo posterior al último respaldo se va a perder (o, en
   la nube, lo posterior al punto de PITR elegido).
2. **Un entorno nuevo**: otra máquina u otro proyecto de compose, desde la etiqueta de la versión.
3. **Secretos nuevos**: `python scripts/generar_secretos_locales.py secretos` (en la nube, versiones
   nuevas en Secret Manager).
4. **Verificar el respaldo** antes de tocar nada:
   `dcp --profile restauracion run --rm --no-deps restauracion verificar /respaldos/<respaldo>`.
5. **Restaurar** en la base nueva y vacía ([backup-restore.md](backup-restore.md#restaurar)), que copia
   también el almacén y valida filas, restricciones y artefactos.
6. **Levantar el resto** (`dcp up -d --wait`); las migraciones no tienen nada que hacer si la versión
   es la del respaldo.
7. **La red vial**: prepararla y `MC_RED_VIAL_HUELLA=<huella> dcp --profile ruteo up -d osrm`.
8. **Verificar**: las tres pruebas de humo, `verificar-fuentes`, y que la app muestre el último
   contexto.
9. **Rehacer lo perdido**: lo que llegó después del respaldo se vuelve a subir desde sus fuentes
   (los archivos originales); el sistema es por contenido, así que una fuente que sí alcanzó a
   respaldarse no se duplica (`409` si se sube otra vez).
10. **Análisis del incidente**: qué falló, cuánto se perdió contra el RPO, cuánto tardó contra el RTO.

## Cómo se ensaya

Cada run del CI hace los pasos 2 a 8 en una máquina limpia: respalda el compose de desarrollo,
genera secretos nuevos, restaura en un entorno de producción nuevo (con los simulacros de respaldos
alterados) y corre las pruebas de humo, Playwright y los simulacros de falla sobre lo restaurado. En
local, `scripts/simulacro_de_restauracion.py` hace los pasos 4 y 5 con sus comprobaciones.
