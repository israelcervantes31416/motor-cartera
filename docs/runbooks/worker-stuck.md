# Runbook: el worker no trabaja

En los comandos, `dcp` es `docker compose -f docker-compose.production.yml` ([índice](README.md)).

**Alertas:** `MCWorkerSinLatido`, `MCWorkerCaido`, `MCLeasesVencidos`, `MCTrabajosFallidos`.

**Impacto:** las corridas y las ejecuciones quedan `EN_PROCESO` en la cola durable; la API sigue
aceptando trabajo. Nada se pierde: la cola está en PostgreSQL.

## Qué esperar

- Un worker detenido con `SIGTERM` termina el trabajo en curso (o sale y su lease vence), con código
  0. Una corrida que llega sin worker espera `EN_PROCESO` y se completa en cuanto vuelve (5.1 s en el
  simulacro).
- Un worker que muere a la mitad de un trabajo deja su lease vencer (`MC_WORKER_LEASE_SEGUNDOS`): otro
  lo vuelve a tomar. La repetición es segura porque los motores lo son (bloqueos, estados terminales
  y transacciones todo o nada).
- Un trabajo `FALLIDO` agotó sus intentos o falló sin reintento: es un estado terminal, con su
  `ultimo_error`.

## Diagnóstico

```bash
dcp ps --all worker
dcp logs --since 30m worker | tail -100
# Su salud, desde la red interna:
dcp exec -T worker python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:9465/health/live').read().decode()); print(urllib.request.urlopen('http://127.0.0.1:9465/health/ready').read().decode())"
```

- **`latido_vencido`** en su liveness: el ciclo no late hace más de 60 s; el proceso está colgado.
- **Listo pero sin tomar**: ¿la cola tiene trabajo disponible? `mc_cola_trabajos` y
  `mc_cola_antiguedad_segundos` en Grafana (tablero del worker). Un trabajo `RUTEO_V2` no se toma sin
  OSRM ([osrm-down.md](osrm-down.md)).
- **`MCLeasesVencidos`**: un worker dejó de latir con un trabajo tomado; se reintenta solo al
  vencer el lease, si quedan intentos.
- **`MCTrabajosFallidos`**: el trabajo y su error (con `mc.audit`):
  `GET /trabajos/{trabajo_id}` o `GET /flujos/{flujo_id}/trabajos`.

## Mitigación

- **Colgado o caído**: `dcp restart worker`. Vuelve a tomar la cola.
- **Más trabajo que capacidad**: más workers, `dcp up -d --scale worker=3` (nunca toman el mismo
  trabajo: `FOR UPDATE SKIP LOCKED`).
- **Un `FALLIDO`**: leer su `ultimo_error`. Un error de datos (un archivo que no cumple su contrato)
  no se reintenta: se corrige la fuente y se sube otra vez. Un error de infraestructura que ya pasó
  se vuelve a pedir con el backfill de su motor (`motor-cartera backfill-<motor> --dry-run` primero).

## Verificación

El worker `healthy`, `mc_worker_latido_timestamp_segundos` reciente y la cola bajando; la prueba de
humo interna corre un trabajo de verdad de punta a punta.
