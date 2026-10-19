# Observabilidad (v0.18)

Lo que la API y el worker dejan para operarlos: logs JSON correlacionados, métricas de
Prometheus, trazas de OpenTelemetry y la auditoría de seguridad. Ninguna de las cuatro lleva un dato
personal ni un identificador de negocio en sus etiquetas, y la telemetría falla abierta: si el
colector no responde, las trazas se descartan sin tocar una petición. Los objetivos que vigilan, en
[slos.md](slos.md); el porqué, en las decisiones 285 a 289 de [decisiones.md](decisiones.md).

## Los logs

Una línea JSON por evento (`MC_LOG_FORMATO=json`, el de producción) con campos fijos:

```json
{"timestamp": "2026-10-18T22:42:16.325+00:00", "level": "INFO", "service": "api",
 "environment": "production", "logger": "motor_cartera.http", "event": "http.peticion",
 "message": "GET /corridas/{run_id} 404", "request_id": "edge-3f0c...", "trace_id": "4bf9...",
 "span_id": "00f0...", "duration_ms": 3.1, "status": 404}
```

- **La petición** (`event: http.peticion`) lleva el método, la **plantilla** de la ruta (nunca la
  ruta con sus identificadores, nunca la query), el estado y la duración. Las sondas de salud van en
  `DEBUG`; un 5xx, en `ERROR`.
- **Un error no esperado** (`http.error`) lleva la clase de la excepción y su traza, redactadas; la
  respuesta es `500 ERROR_INTERNO` con su `request_id`, sin la traza.
- **Redacción antes de escribir**: JWT, `Bearer ...`, pares de credencial (`password=`, `token=`,
  `api_key=`...), contraseñas en URLs y cada secreto configurado (las claves, los secretos del
  gateway, la clave de auditoría, la URL de la base). SQLAlchemy nunca pone los parámetros en un
  mensaje de error (`hide_parameters`).
- **Los ruidosos callan**: SQLAlchemy, httpx y urllib3 en `WARNING` (en `INFO` escribirían URLs con
  su query, y una query puede llevar un `CLIENTE_UNICO`); uvicorn no escribe su registro de acceso.
- `tests/test_logs_sin_datos_personales.py` recorre una corrida completa sobre una cartera con datos
  personales (y una gestión con una observación con un nombre) y falla si alguno llega a un log.

### El request_id

El edge genera uno por petición (`$request_id`) y se lo pasa a la API en `X-Request-ID`; la API lo
conserva si tiene forma (8 a 128 caracteres de `[A-Za-z0-9._-]`) y si no, crea otro. Va en la
respuesta (`X-Request-ID`), en cada log y en el evento de auditoría. Un cliente no puede inyectar
saltos de línea ni uno de 200 caracteres.

### La auditoría

`security-audit/v1`, un evento por petición a la API, en el logger `motor_cartera.auditoria`
([security.md](security.md#la-auditoría-security-auditv1)).

## Las métricas

Cada proceso sirve las suyas en un puerto interno (`MC_METRICAS_PUERTO`: 9464 la API, 9465 el
worker, que ahí mismo sirve su salud); no pasan por el edge ni se publican al host. Sus etiquetas
salen de listas cerradas: la plantilla de la ruta (`/corridas/{run_id}`, nunca el UUID; una ruta que
no existe es `__sin_ruta__`), el método, la clase del estado (`2xx`...), el tipo de trabajo, cómo
terminó y los códigos de la autorización. La cardinalidad no depende de los datos, y una prueba
falla si aparece una etiqueta fuera de la lista.

| Métrica | Tipo | Etiquetas | Qué mide |
| --- | --- | --- | --- |
| `mc_http_peticiones_total` | contador | `metodo`, `ruta`, `clase_estado` | peticiones atendidas |
| `mc_http_duracion_segundos` | histograma | `metodo`, `ruta` | su duración |
| `mc_http_en_curso` | gauge | `metodo` | las que están en curso |
| `mc_autorizacion_total` | contador | `decision`, `motivo` | cada decisión de autorización |
| `mc_jwks_refrescos_total` | contador | `resultado` (`ok`, `fallido`) | descargas de la JWKS |
| `mc_db_errores_total` | contador | `tipo` (clase del error, de una lista) | errores de la base |
| `mc_db_pool_agotado_total` | contador | | esperas del pool que se agotaron |
| `mc_db_pool_tamano`, `_en_uso`, `_libres`, `_desborde` | gauge | | el pool de conexiones |
| `mc_cola_trabajos` | gauge | `estado`, `tipo` | la cola durable (la API la exporta: si todos los workers caen, se sigue viendo) |
| `mc_cola_antiguedad_segundos` | gauge | `tipo` | la edad del trabajo pendiente más antiguo |
| `mc_cola_leases_vencidos` | gauge | `tipo` | trabajos cuyo worker dejó de latir |
| `mc_cola_lectura_ok` | gauge | | si la última lectura de la cola funcionó |
| `mc_worker_trabajos_tomados_total`, `_cerrados_total` | contador | `tipo` (y `resultado`) | lo que el worker tomó y cómo lo cerró |
| `mc_worker_errores_total`, `mc_worker_leases_perdidos_total` | contador | `tipo` | errores del worker y leases perdidos |
| `mc_worker_duracion_trabajo_segundos` | histograma | `tipo` | cuánto tarda cada trabajo |
| `mc_worker_latido_timestamp_segundos`, `mc_worker_trabajo_en_curso` | gauge | | el latido y si hay trabajo |
| `mc_ruteo_disponible` | gauge | | si el OSRM del worker responde |
| `mc_salud_lista` | gauge | | la última readiness de la API |
| `mc_build_info` | gauge | `servicio`, `version` | la versión que corre |

Además, las del proceso (`process_*`). Las métricas de la cola se leen en PostgreSQL con el índice
parcial de lo reclamable, a lo más cada 10 s y con un tope de 2 s: medir la cola no pesa sobre la
base. Las series que las alertas vigilan con `increase()` nacen en cero al arrancar (un contador
que nace con su primer incremento no lo cuenta, y la alerta llegaría un evento tarde).

## Las trazas

OpenTelemetry con spans propios, sin instrumentación automática:

- **La petición**: un span `SERVER` por petición, con el nombre `MÉTODO /plantilla`, el método, la
  plantilla (`http.route`) y el estado; un 5xx lo marca con error. Sin la ruta real, sin la query,
  sin cabeceras, sin la IP ni el user-agent.
- **La base**: un span por sentencia (`db SELECT`, `db INSERT`...), con la operación y el sistema,
  **nunca el texto de la sentencia**; hasta 200 por traza.
- **El worker**: un span `CONSUMER` por trabajo (`trabajo INGESTA`), con su tipo y cómo terminó.
- **La telemetría nativa de FastAPI está apagada.** FastAPI 0.142 crea por su cuenta un span de
  servidor con la ruta real y la query (con un identificador de cliente adentro) y registra los
  valores de entrada de una validación fallida en cuanto existe un proveedor de trazas: se apaga
  entera (`telemetry=` en `crear_app`), y una prueba exige un solo span de servidor sin la ruta real.
- **Muestreo**: por cabeza en la API (`MC_OTEL_MUESTREO`, 100 % cuando hay colector) y por cola en el
  colector, que ve la traza entera: guarda siempre las que tienen un error y las lentas (más de 1 s),
  y de las demás el `MC_OTEL_PORCENTAJE` (100 en local; 10 en la referencia de producción). Un
  `traceparent` del cliente no decide: el edge lo borra.
- **Falla abierta**: el exportador OTLP/HTTP corre en un hilo, con lotes y un tope de 3 s; sin
  colector, las trazas se descartan y la API responde igual (medido: mediana de 2.9 ms con colector,
  2.7 ms sin él).
- **Cuánto cuesta**: a 25 lecturas por segundo, exportar trazas sube el p95 de 103.6 a 121.3 ms y deja
  la mediana igual ([carga.md](carga.md)).

El colector (`infra/observabilidad/otel-collector.yaml`) borra además, por si alguna vez llegan,
`url.path`, `url.query`, `url.full`, `http.target`, `http.url`, `db.statement`, `db.query.text`,
`client.address` y `user_agent.original`: defensa en profundidad.

## El perfil `observabilidad`

```bash
MC_OTEL_ENDPOINT=http://otel-collector:4318 \
  docker compose -f docker-compose.production.yml --profile observabilidad up -d --wait
```

| Servicio | Qué hace |
| --- | --- |
| `otel-collector` | recibe OTLP/HTTP de la API y el worker, borra lo sensible, muestrea por cola y envía a Jaeger; sirve sus propias métricas |
| `jaeger` | guarda las trazas en memoria (hasta 20,000; se pierden al reiniciarse) y las muestra |
| `prometheus` | descubre la API y el worker por DNS (cada réplica), consulta sus métricas cada 15 s, evalúa las alertas |
| `alertmanager` | recibe las alertas; en local, con un receptor nulo (no avisa a nadie) |
| `grafana` | tres tableros: el sistema, el worker y las dependencias |

Ninguno publica un puerto: viven en la red `observabilidad`, interna. Para verlos en la máquina
local, un archivo propio (que no va al repositorio) los publica solo en `127.0.0.1`:

```yaml
# docker-compose.observabilidad-local.yml (local, fuera del repositorio)
services:
  grafana:
    ports: ["127.0.0.1:3000:3000"]
  jaeger:
    ports: ["127.0.0.1:16686:16686"]
  prometheus:
    ports: ["127.0.0.1:9090:9090"]
```

Grafana se entra con `admin` y la contraseña de `secretos/grafana_admin`. Jaeger 2 sirve su API en
`/api/v3/` (la de `/api/services` ya no existe).

### Las alertas

`infra/observabilidad/alertas.yml`. **Sus umbrales son iniciales** (etiqueta `umbral: inicial`): se
ajustan con la operación real. Cada una lleva su runbook.

| Alerta | Cuándo | Runbook |
| --- | --- | --- |
| `MC5xxElevados` | más del 2 % de 5xx (ventana de 5 min) durante 10 min | [service-down](runbooks/service-down.md) |
| `MCLatenciaP95Alta` | p95 de las lecturas sobre 1.5 s durante 10 min | [service-down](runbooks/service-down.md) |
| `MCLatenciaP95RutasCarasAlta` | p95 de las lecturas caras sobre 5 s durante 15 min | [service-down](runbooks/service-down.md) |
| `MCReadinessFallando` | la API no está lista durante 2 min | [database-down](runbooks/database-down.md) |
| `MCApiSinMetricas` | Prometheus no alcanza a la API durante 2 min | [service-down](runbooks/service-down.md) |
| `MCIdentidadNoDisponible` | alguna respuesta `IDENTIDAD_NO_DISPONIBLE` en 5 min | [identity-down](runbooks/identity-down.md) |
| `MCJwksFallando` | más de 3 descargas fallidas de la JWKS en 10 min, durante 5 min | [identity-down](runbooks/identity-down.md) |
| `MCPoolDeConexionesAgotado` | alguna espera del pool agotada en 5 min, durante 5 min | [database-down](runbooks/database-down.md) |
| `MCColaAtrasada` | un trabajo pendiente con más de 10 min, durante 5 min | [queue-backlog](runbooks/queue-backlog.md) |
| `MCTrabajosFallidos` | algún trabajo cerrado `FALLIDO` en 15 min | [worker-stuck](runbooks/worker-stuck.md) |
| `MCLeasesVencidos` | un trabajo con el lease vencido durante 10 min | [worker-stuck](runbooks/worker-stuck.md) |
| `MCWorkerSinLatido` | ningún worker latió en 2 min, durante 2 min | [worker-stuck](runbooks/worker-stuck.md) |
| `MCWorkerCaido` | Prometheus no alcanza a ningún worker durante 2 min | [worker-stuck](runbooks/worker-stuck.md) |
| `MCLecturaDeColaFallando` | la API no puede leer la cola durante 2 min | [database-down](runbooks/database-down.md) |
| `MCColectorDeTelemetriaCaido` | Prometheus no alcanza al colector durante 5 min | [service-down](runbooks/service-down.md) |

Las de latencia avisan una degradación grave, más holgada que el objetivo de [slos.md](slos.md): el
objetivo se mide sobre el mes, y una alerta que suene con cada pico se deja de escuchar.

La prueba de humo interna revisa que Prometheus consulte a la API y al worker, que las reglas estén
cargadas, que Alertmanager viva, que Jaeger tenga trazas de la API y que Grafana viva. Al escribir
v0.18 se encontró, por las métricas mismas, que la lectura de la cola fallaba en producción (una
sentencia con `FILTER` mal puesto: `mc_cola_lectura_ok` era 0); hoy la cubre una prueba contra
PostgreSQL.
