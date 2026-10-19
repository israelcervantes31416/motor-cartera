# Objetivos de servicio (v0.18)

Los objetivos con que se operaría motor-cartera. **Son iniciales**: no hay historia de operación
real que los respalde, solo las mediciones de v0.17 (escala) y de v0.18 (carga y simulacros). Se
ajustan con la operación, en un cambio con su porqué, nunca para que un mes "pase". Cómo se miden
está en [observability.md](observability.md); qué hacer cuando se rompen, en [runbooks/](runbooks/).

## Los indicadores y sus objetivos

| Indicador (SLI) | Cómo se mide | Objetivo (SLO) | Ventana |
| --- | --- | --- | --- |
| Disponibilidad de la API | peticiones que no son 5xx / todas (`mc_http_peticiones_total`); los 4xx y los 429 son respuestas correctas | 99.5 % | 30 días |
| Latencia de las lecturas | p95 de `mc_http_duracion_segundos` de los `GET` que no son caros | menos de 500 ms | 30 días |
| Latencia de las lecturas caras | p95 del resumen, la distribución de un scoring, los agregados de analytics y el explorador ordenado por saldo | menos de 1.5 s | 30 días |
| Frescura de la cola | tiempo en que el trabajo pendiente más antiguo tiene menos de 10 min (`mc_cola_antiguedad_segundos`) | 99 % | 30 días |
| Errores del worker | intentos que terminan en un error del worker (no de los datos) / trabajos tomados | menos de 1 % | 30 días |
| Identidad | peticiones con `IDENTIDAD_NO_DISPONIBLE` / todas | menos de 0.1 % | 30 días |
| Respaldo | días con un respaldo de la base y del almacén terminado y verificado | 100 % | 30 días |
| Restauración | una restauración completa probada (el simulacro) por versión | cada versión | por versión |

**Presupuesto de errores.** 99.5 % en 30 días son unas 3 h 36 min de indisponibilidad completa (o
su equivalente en errores parciales). Si un mes consume más de la mitad antes de su día 15, se
congelan los cambios de riesgo (dependencias mayores, migraciones, infraestructura) hasta entender
por qué; cada incidente que consume más de un cuarto lleva su análisis, sin culpables.

**Lo que la API no ve.** Si la API cae entera, sus métricas callan: la disponibilidad desde afuera
la miden la sonda del edge (`/salud-web`) y, en la nube, el uptime check y las métricas del
balanceador ([cloud-gcp.md](cloud-gcp.md)). Un `502` del edge cuenta como indisponibilidad aunque la
API no lo registre.

## Lo que dicen las mediciones

- **Latencia.** En la prueba de carga registrada (corte de 1,000 cuentas), la mezcla de lecturas de
  la app a 25 por segundo tuvo un p95 de 121 ms, con el edge, la sesión y la telemetría
  ([carga.md](carga.md)). A escala (500,000 cuentas, v0.17), las rutas de `app/v1` tienen de 13 a 62
  ms de mediana; el resumen, 0.54 s, y el explorador ordenado por saldo, 0.98 s
  ([decisión 274](decisiones.md#274-el-benchmark-de-la-superficie-y-qué-no-resuelve-v0170)): dentro
  de sus objetivos, el de las caras con poco margen.
- **Recuperación.** Con la base caída, la API y el worker se recuperan solos en 1.1 s cuando vuelve;
  la API reiniciada está lista en 8 s; un trabajo que llega sin worker se completa 5.1 s después de
  que el worker vuelve ([production.md](production.md#los-simulacros)).
- **Restauración.** El respaldo de la prueba de humo (83 tablas, 156,297 filas, 97 artefactos) se
  respalda en 6.1 s y se restaura y valida en 9 a 11 s ([backups.md](backups.md)). Es el volumen
  sintético del CI, no el de un despacho: con uno real hay que medir otra vez.

## Recuperación ante desastres: RPO y RTO

| | Compose de producción local | Referencia de Google Cloud |
| --- | --- | --- |
| RPO (cuánto se puede perder) | lo posterior al último respaldo (`scripts/respaldo.py`, diario como mínimo) | minutos: PITR de Cloud SQL (7 días de bitácora) para la base; el almacén, lo posterior al último respaldo nocturno |
| RTO (cuánto tarda volver) | una hora como objetivo: un entorno nuevo, la restauración (segundos en el volumen del CI) y su verificación | una hora como objetivo, con la infraestructura ya aplicada; sin ella, lo que tarde aplicarla |

El procedimiento está en [runbooks/disaster-recovery.md](runbooks/disaster-recovery.md). El CI lo
ensaya en cada push: respalda el compose de desarrollo y lo restaura en una máquina limpia, con los
simulacros de respaldos alterados.

## Alertas y objetivos

Las alertas (`infra/observabilidad/alertas.yml`) avisan una degradación grave; los objetivos se
miden sobre el mes. Por eso los umbrales de las alertas de latencia (1.5 s y 5 s de p95) son más
holgados que los objetivos (500 ms y 1.5 s): una alerta que suena con cada pico se deja de escuchar.
Una alerta por quemar el presupuesto a una tasa alta (burn rate) es lo siguiente cuando haya tráfico
real que la calibre.
