# Runbook: la cola se atrasa

En los comandos, `dcp` es `docker compose -f docker-compose.production.yml` ([índice](README.md)).

**Alerta:** `MCColaAtrasada` (un trabajo pendiente y disponible con más de 10 minutos).

**Impacto:** las corridas y las ejecuciones tardan en terminar; la app muestra lo último publicado.
Nada se pierde.

## Diagnóstico

1. **¿Hay workers trabajando?** Si no, es [worker-stuck.md](worker-stuck.md).
2. **¿De qué tipo es lo atrasado?** `mc_cola_trabajos` y `mc_cola_antiguedad_segundos` por `tipo`
   (tablero del worker). Solo `RUTEO_V2`: falta OSRM ([osrm-down.md](osrm-down.md)); el worker no
   los toma sin él y sigue con los demás.
3. **¿Cuánto tarda cada trabajo?** `mc_worker_duracion_trabajo_segundos` por tipo. Uno que tarda
   mucho más que de costumbre: su traza en Jaeger (`trabajo <TIPO>` y sus spans `db ...`).
4. **¿Llegó mucho de golpe?** Un backfill encola mucho a propósito; la cola se vacía sola a su ritmo.

## Mitigación

- **Capacidad**: `dcp up -d --scale worker=<n>`. Cada worker toma un trabajo a la vez; varios nunca
  toman el mismo. Vigilar la base: más workers son más conexiones y más carga
  ([database-down.md](database-down.md)).
- **Un tipo lento**: si un trabajo concreto no avanza (su latido sigue pero no termina), su lease se
  renueva; reiniciar ese worker lo devuelve a la cola cuando vence. Antes, entender por qué (un corte
  mucho más grande, un índice).
- **Un backfill que no debía**: los backfills tienen `--dry-run`; lo encolado se procesa igual, no se
  borra de la cola a mano.

## Verificación

`mc_cola_antiguedad_segundos` bajo 10 minutos y la alerta resuelta.
