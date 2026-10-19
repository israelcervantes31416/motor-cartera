# Runbook: la API o el edge no responden, o responden mal

En los comandos, `dcp` es `docker compose -f docker-compose.production.yml` ([índice](README.md)).

**Alertas:** `MC5xxElevados`, `MCLatenciaP95Alta`, `MCLatenciaP95RutasCarasAlta`,
`MCApiSinMetricas`, `MCColectorDeTelemetriaCaido`; en la nube, el uptime check del edge y los 5xx del
balanceador.

**Impacto:** la app no carga o sus pantallas fallan; la API no acepta trabajo. Los trabajos ya en la
cola siguen (el worker no depende de la API).

## Diagnóstico

1. **¿Qué está arriba?**

   ```bash
   dcp ps --all
   dcp logs --since 15m api edge | tail -100
   ```

   La API `unhealthy` con la base arriba: su readiness falla por la base o por el esquema (ver
   [database-down.md](database-down.md)). La API reiniciándose: su log dice por qué no arranca (una
   configuración inválida nombra la variable, nunca su valor).
2. **¿5xx de la API o del edge?** Los de la API tienen su línea `http.peticion` en `ERROR` y, un 500
   no esperado, su `http.error` con la traza redactada y el `request_id`. Un `502`/`504` del edge sin
   línea en la API es que la API no respondió (caída, reiniciándose o saturada).
3. **¿Latencia?** En Grafana (tablero del sistema), el p95 por plantilla de ruta: ¿una ruta o todas?
   Todas, con la CPU de la API cerca de su límite, es saturación: una API sostuvo 25 lecturas por
   segundo de la mezcla de la app ([carga.md](../carga.md)). Una ruta cara (resumen, distribución,
   agregados, el orden por saldo), la base: ver sus trazas en Jaeger (los spans `db ...` de esa
   petición).
4. **¿Sin métricas?** `MCApiSinMetricas` con la API sana es Prometheus o la red de observabilidad;
   `MCColectorDeTelemetriaCaido` no afecta a nadie: la telemetría falla abierta.

## Mitigación

- **La API caída o colgada**: `dcp restart api`. Termina limpia con `SIGTERM` (código 0, hasta 20 s
  para sus peticiones en curso) y vuelve a estar lista en unos 8 s (medido).
- **Saturada**: más réplicas, `dcp up -d --scale api=2` (el edge balancea entre ellas); y buscar la
  causa (una ruta cara, un cliente que excede: el edge lo limita por IP con `429`).
- **El edge**: `dcp restart edge`; si no arranca, su log (una plantilla que no se renderiza, un
  certificado que no se lee).
- **El colector caído**: `dcp restart otel-collector`; nada más depende de él.

## Verificación

```bash
python scripts/prueba_de_humo_produccion.py --edge https://motor-cartera.localhost:8443 \
  --ca secretos/ca_certificado --usuarios secretos/usuarios.json --claves secretos/claves_humo
```

Y en Grafana, la tasa de 5xx y el p95 de vuelta en su nivel.
