# Pruebas de carga (v0.18)

Que aguanta el compose de produccion local con la seguridad y la observabilidad puestas, y cuanto
cuestan. Se registraron el perfil, las tasas y los umbrales **antes** de correrlas (este documento,
`infra/carga/motor-cartera.js` y `scripts/carga.py`, en el mismo commit que los resultados); un
umbral que no se cumple se reporta como fallido y no se mueve despues de ver el resultado.

No es una prueba de capacidad de un despliegue real: corre en una sola maquina (Docker Desktop
sobre WSL2, con 8 GB y 12 CPU para todos los contenedores), con k6 en la misma maquina y la base
restaurada del respaldo de la prueba de humo. Sirve para comparar configuraciones entre si, en la
misma maquina y el mismo dia, y para que una regresion grande se note.

## Como se corre

```bash
python scripts/carga.py --escenario api      # o saldo, edge, limite
```

k6 1.8.1 (imagen fijada por su digest) corre en un contenedor dentro de la red del compose: a la
API por la red frontal, o al edge por la red publica verificando su certificado con la CA del
compose. Nada se publica al host para esto. La clave de servicio de lectura, o la cookie de una
sesion que se abre en Dex para la prueba (usuario `lector`), van en archivos montados de solo
lectura y se borran al terminar. Mientras corre, `scripts/carga.py` toma cada 5 s el CPU y la
memoria de los contenedores que atienden. Deja en `resultados/carga/` (fuera del repositorio) el
resumen de k6 y esas muestras.

Antes de cada escenario el `setup()` de k6 busca, por la misma API, un corte con decision y
analytics y sus cuentas: nada se inventa.

## Escenarios y umbrales registrados

| Escenario | Que mide | Tasa | Duracion | Umbrales |
|---|---|---|---|---|
| `api` | la mezcla de lecturas de la app, directo a la API con la clave de servicio | 25/s | 60 s | fallidas < 1 %, p95 < 500 ms, p99 < 1500 ms |
| `api` a 10/s | la misma mezcla a la tasa del edge, para comparar | 10/s | 60 s | los mismos |
| `saldo` | el orden caro, aparte: las cuentas del corte por saldo descendente, paginas al azar | 2/s | 60 s | fallidas < 1 %, p95 < 1500 ms, p99 < 3000 ms |
| `edge` | la misma mezcla por el edge: TLS 1.3, cookie de sesion, `auth_request` a oauth2-proxy y su almacen de sesiones (Valkey) | 10/s | 60 s | fallidas < 1 %, p95 < 800 ms, p99 < 2000 ms |
| `limite` | mas lecturas por segundo de las que el edge deja pasar a una IP (30/s, rafaga 60) | 80/s | 10 s | ningun 5xx, al menos 20 % limitadas (429), cada 429 con `Retry-After` |
| `api` sin OTel | `api` a 25/s con la API sin exportar trazas (`MC_OTEL_ENDPOINT` vacio) | 25/s | 60 s | los de `api` |

La mezcla de `api` y `edge`: 30 % el selector de contexto (`/app/v1/contextos/opciones`), 20 % el
explorador de cuentas (primera pagina, orden por cuenta), 20 % una Cuenta 360 al azar, 15 % el
resumen del contexto (de las lecturas caras: recorre el corte entero) y 15 % el catalogo de
analytics. Toda peticion debe responder 200 (el check de k6). La tasa del edge (10/s) queda debajo
de sus limites por IP: 30/s de lecturas y 5/s de las caras (el resumen es 1.5/s de esa mezcla).

El costo de la seguridad es la diferencia entre `edge` y `api` a 10/s; el de la telemetria, entre
`api` y `api` sin OTel a 25/s.

## Resultados (2026-10-18)

La corrida registrada, en el orden de la tabla, sobre el compose de produccion con el perfil de
observabilidad y la base restaurada del respaldo de la prueba de humo (83 tablas, 156 297 filas; el
corte oficial del perfil XS tiene 1 000 cuentas). Latencias de k6, del lado del cliente, en ms.

| Escenario | Peticiones | Por segundo | Mediana | p95 | p99 | Maxima | Fallidas | Umbrales |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| `api` 25/s | 1 509 | 25.0 | 44.9 | 121.3 | 246.9 | 373 | 0 | se cumplen |
| `api` 10/s | 609 | 10.1 | 36.6 | 75.5 | 95.1 | 256 | 0 | se cumplen |
| `saldo` 2/s | 129 | 2.1 | 33.8 | 39.4 | 49.0 | 60 | 0 | se cumplen |
| `edge` 10/s | 608 | 10.1 | 39.0 | 78.8 | 98.5 | 212 | 0 | se cumplen |
| `limite` 80/s | 674 | 61.6 | 548.9 | 1 760.7 | 2 543.1 | 3 444 | 0 | se cumplen |
| `api` 25/s sin OTel | 1 509 | 25.0 | 44.6 | 103.6 | 188.2 | 409 | 0 | se cumplen |

Lo que dicen:

- **El costo de la seguridad es de unos milisegundos.** A 10/s, el edge (TLS 1.3, la cookie de
  sesion, `auth_request` a oauth2-proxy y su consulta a Valkey) suma 2.4 ms a la mediana, 3.3 ms al
  p95 y 3.4 ms al p99 frente a la API directa.
- **La telemetria cuesta en la cola.** A 25/s, exportar trazas (100 % por cabeza; el muestreo lo
  decide el colector) deja la mediana igual (44.9 contra 44.6 ms) y sube el p95 de 103.6 a 121.3
  ms (17 %) y el p99 de 188.2 a 246.9 ms. Es el precio de un span por sentencia SQL: por eso hay un
  tope de 200 por traza y por eso la referencia de produccion muestrea en el colector.
- **El limite por IP funciona y no rompe nada.** A 80/s desde una sola IP, el 46 % de las
  respuestas fue 429, todas con `Retry-After`, y ninguna fue un 5xx. Las que pasaron (unas 33/s, por
  la rafaga de 60) saturaron la API (125 % de CPU): de ahi la latencia del escenario, que no tiene
  umbral de latencia. k6 no alcanzo las 80/s (61.6/s, 134 iteraciones descartadas) porque llego a su
  tope de 60 usuarios virtuales con esas esperas.
- **El orden por saldo no es caro con 1 000 cuentas**: 39.4 ms de p95 a 2/s. A escala si lo es: con
  500 000 cuentas, el benchmark de v0.17 (`scripts/benchmark_app.py`) midio 0.98 s de mediana
  ([decision 274](decisiones.md#274-el-benchmark-de-la-superficie-y-qué-no-resuelve-v0170)), dentro
  del objetivo de las lecturas caras ([slos.md](slos.md)) pero con poco margen.
- **Una API sostiene 25/s de la mezcla** con p95 de 121 ms y hasta 89 % de CPU (un solo proceso de
  uvicorn). Para mas, se escala hacia afuera: mas replicas detras del edge, que balancea entre ellas
  (`upstream` con `resolve`), o mas instancias en Cloud Run (docs/cloud-gcp.md).

CPU y memoria maximas de los contenedores que atienden (muestras cada 5 s de `docker stats`):

| Escenario | API | PostgreSQL | edge | oauth2-proxy | Valkey |
|---|---|---|---|---|---|
| `api` 25/s | 89 %, 214 MiB | 69 %, 63 MiB | 3 %, 17 MiB | 0 %, 6 MiB | 4 %, 13 MiB |
| `edge` 10/s | 66 %, 218 MiB | 26 %, 63 MiB | 5 %, 19 MiB | 1 %, 9 MiB | 4 %, 8 MiB |
| `limite` 80/s | 125 %, 218 MiB | 84 %, 96 MiB | 9 %, 23 MiB | 6 %, 9 MiB | 1 %, 9 MiB |
| `api` sin OTel | 105 %, 212 MiB | 65 %, 63 MiB | 3 %, 18 MiB | 0 %, 9 MiB | 3 %, 8 MiB |

### El ensayo, antes de la corrida registrada

Antes de la corrida registrada se probo la mecanica con `api` a 25/s durante 10 s, recien levantado
el compose: p95 de 1.3 s, 6 iteraciones descartadas y el umbral de p95 cruzado. Era el arranque en
frio (la primera carga sobre la API y los buffers de PostgreSQL recien restaurados) concentrado en
10 s; en la corrida de 60 s se diluye. Se reporta porque paso: los umbrales no se movieron despues
de verlo.

## En el CI

El job de produccion corre una version corta con los mismos umbrales: `api` a 10/s durante 15 s y
`limite` a 80/s durante 10 s (docs/production.md). Comprueba que nada se rompio, no mide capacidad:
el runner del CI no es esta maquina.
