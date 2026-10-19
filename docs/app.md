# La app: superficie operacional y Cuenta 360 (v0.17, con sesión desde v0.18)

v0.17 convierte lo que los motores ya publican (la Cuenta 360, `decision/v2`, la geografía y
`territorial/v2`, `campo/v2`, `ruteo/v2`, `analytics/v1`, `predictive/model-v1` y
`optimization/v1`) en una superficie web de **solo lectura**: un selector de contexto, un resumen,
un explorador de cuentas, la Cuenta 360 con once pestañas, analytics, predictivo, optimización,
campo, ruteo y auditoría. Las pantallas, en [dashboards.md](dashboards.md); el contexto que leen, en
[contexto_operativo.md](contexto_operativo.md); cómo está hecha la app, en
[frontend.md](frontend.md); su privacidad, en [privacidad_app.md](privacidad_app.md); el porqué,
en las [decisiones 248 a 274](decisiones.md#248-una-spa-de-react-con-vite-sin-ssr).

**Desde v0.18 tiene usuarios.** En el compose de producción
([production.md](production.md)) cada persona inicia sesión en el proveedor de identidad (OpenID
Connect con PKCE, por oauth2-proxy) y la app muestra lo que sus permisos dejan leer; el navegador
solo guarda una cookie de sesión `HttpOnly`, nunca un token. En el compose de desarrollo no hay
usuarios, y la app lo dice a la vista: «Desarrollo local sin usuarios (clave de servicio del proxy);
no desplegar así ni con datos reales». Ninguno ejecuta nada: no crea gestiones, no envía mensajes,
no llama, no visita, no promete, no decide, no optimiza, no entrena, no publica ni cambia una cuenta.
Los `POST` de la API siguen siendo de la API y de la línea de comandos; el edge no los deja pasar.

## Cómo está armada

```text
navegador ──▶ nginx (127.0.0.1:8080)
              ├─ /            la SPA: archivos estáticos, cada pantalla en su paquete diferido
              └─ /api/...     proxy de solo lectura: solo GET, solo la lista blanca,
                              con la clave de la API puesta del lado del servidor
                                 │
                                 ▼
                          la API (FastAPI, :8000)
                          ├─ app/v1          compone lo publicado, por contexto, sin datos personales
                          └─ analytics/v1, predictive/model-v1, optimization/v1,
                             campo/v2 (planes y cargas), ruteo/v2 (ejecuciones y rutas)
                                 │
                                 ▼
                             PostgreSQL
```

**El backend es la fuente de verdad.** La app ordena, pagina, filtra, formatea y dibuja; no
recalcula una métrica, un score, una prioridad, un plan, una ruta, una atribución ni un estado.

**`app/v1` solo compone.** Existe donde sin ella la app tendría que hacer muchas peticiones, unir
respuestas en el navegador o arriesgarse a mezclar ejecuciones: el contexto, el resumen, el
explorador de cuentas, la Cuenta 360 y el linaje. Lee lo que cada ejecución publicó, por su
identificador público, en una lectura consistente (una transacción `REPEATABLE READ` de solo
lectura), y lo devuelve sin datos personales. Lo único que cuenta es la distribución de las
predicciones de un scoring, que ningún motor publica. Lo demás, la app lo lee directo de su motor:
`analytics/v1`, `predictive/model-v1` y `optimization/v1` no llevan datos personales.

## Las rutas de `app/v1`

Todas son `GET`, piden una credencial y el permiso `mc.read` (el linaje, `mc.audit`) y responden
los errores uniformes de la API (`ErrorRespuesta`): el token de la sesión, que pone el edge, o una
clave de servicio (`X-API-Key`; en desarrollo la pone el proxy). Sin credencial, `401`; sin el
permiso, `403 PERMISO_INSUFICIENTE` ([security.md](security.md)). Un contexto se declara con
`corte_id` y, de cada motor, su `*_run_id` ([contexto_operativo.md](contexto_operativo.md)).

| Ruta | Qué responde |
| --- | --- |
| `GET /app/v1/contextos/opciones` | Las carteras, sus cortes y, de un corte, las ejecuciones de cada tipo (las 20 más recientes, con su total) y hasta 6 contextos compatibles que se pueden proponer. |
| `GET /app/v1/contextos/validar` | Si el contexto es compatible: cada ejecución resuelta (estado, relojes, versiones, lo que leyó), lo derivado y cada problema. Siempre 200. |
| `GET /app/v1/resumen` | Cada bloque (analytics, decisión, predictivo, optimización, campo, ruteo), de la ejecución que el contexto declara, o `NO_DECLARADA`. 409 `CONTEXTO_INCOMPATIBLE` con sus problemas. |
| `GET /app/v1/predictivo/scorings/{scoring_run_id}/distribucion` | Cuántas predicciones publicó el scoring, cuántas con imputación, sus medias, la suma de la recuperación positiva esperada y un histograma por décimo de cada probabilidad, contados en la base. |
| `GET /app/v1/cuentas` | Las cuentas del corte, filtradas, ordenadas y paginadas en la base (25, 50 o 100; hasta 100), con lo que cada ejecución declarada publicó de cada una. 422 `FILTRO_SIN_EJECUCION` si un filtro o un orden pide una ejecución que el contexto no declara. |
| `GET /app/v1/cuentas/{cuenta_id}` | La Cuenta 360 en el contexto: su presencia y su snapshot del corte, y una sección por motor (`DISPONIBLE`, `NO_DECLARADA`, `NO_PUBLICADA` o `SIN_RESULTADO`). |
| `GET /app/v1/cuentas/{cuenta_id}/decision` | Su decisión de `decision/v2`, explicada y verificada. |
| `GET /app/v1/cuentas/{cuenta_id}/historia` | Sus snapshots, corte por corte. |
| `GET /app/v1/cuentas/{cuenta_id}/linea-de-tiempo` | Su línea de tiempo en sus tres dominios (económico, operacional y de la fuente), con `ocurrido_en` y `registrado_en`. |
| `GET /app/v1/cuentas/{cuenta_id}/pagos` | El resumen de la interpretación de sus pagos y sus movimientos, con su atribución. |
| `GET /app/v1/cuentas/{cuenta_id}/gestiones` | Su resumen operacional y sus gestiones, sin la observación ni quién las registró. |
| `GET /app/v1/ruteo/rutas/{ruta_id}/paradas` | Las paradas de una ruta en su orden de visita, sin `CLIENTE_UNICO` ni coordenadas. |
| `GET /app/v1/linaje` | Cada ejecución del contexto con sus versiones, sus hashes y sus relojes, y cada relación que una declaró al publicarse. Pide `mc.audit`. |
| `GET /app/v1/sesion` | Quién pregunta, sin un dato personal: `USUARIO` o `SERVICIO`, su seudónimo (`usr:` y 16 hexadecimales, de la clave de auditoría) y sus permisos. La app la pide al abrir: sin sesión, ofrece iniciarla; con una sesión sin `mc.read`, dice que no puede leer. |

Cada ruta documenta su resumen, su descripción, su modelo de respuesta y sus errores en el OpenAPI
(`/docs`). Las pruebas revisan que ninguna escriba, que cada una documente su `401`, su `422` y sus
errores, que ningún esquema de `app/v1` lleve un dato personal y que ninguna use un verbo de acción.

## Cómo se abre

Con el compose, la app queda en <http://localhost:8080> después de `docker compose up --build`
(solo en `127.0.0.1`): el servicio `web` construye la SPA con Node, la sirve con nginx y pone la
clave de `MC_API_KEY` en cada petición que su proxy deja pasar. El selector pide elegir una cartera,
un corte y un contexto; nada se abre solo. Sin datos, la app solo tiene un selector vacío: la prueba
de humo (o el flujo del README) deja un contexto completo.

En una máquina de desarrollo, con la API corriendo en `http://localhost:8000`:

```bash
cd web
npm ci --ignore-scripts
npm run dev        # http://localhost:5173
```

Las variables, los puertos y las compuertas de la app están en [frontend.md](frontend.md).

## Lo medido

En una sola máquina (Windows 11, AMD de 64 bits, PostgreSQL 16, Python 3.14), sobre la base del
benchmark: el corte de 500,000 cuentas de `CARTERA_PRINCIPAL` (2026-04-01) con el contexto que el
selector propone (`DESDE_OPTIMIZACION`: su `decision/v2`, su analytics, su scoring de 500,000
predicciones, su plan de optimización con `CAMPO`, su plan de campo y su ruteo), declarado en
`scripts/benchmark_app.py` antes de medir, y la primera cuenta asignada a `CAMPO` para la Cuenta 360.
Cada ruta, 20 veces después de 2 de calentamiento, con un cliente en el mismo proceso (el tiempo
incluye la serialización), y el `EXPLAIN (ANALYZE, BUFFERS)` de cada sentencia:

| Ruta de `app/v1` | Mediana | p95 | Sentencias | En PostgreSQL |
| --- | ---: | ---: | ---: | ---: |
| Opciones del corte | 26 ms | 29 ms | 13 | 0.8 ms |
| Validar el contexto | 19 ms | 21 ms | 8 | 0.5 ms |
| Resumen | 539 ms | 554 ms | 17 | 516 ms |
| Distribución del scoring | 523 ms | 589 ms | 2 | 524 ms |
| Cuentas, página 1 / 100 / 10,000 (de 50) | 24 / 24 / 62 ms | 25 / 29 / 64 ms | 10 | 1.4 / 2.2 / 57 ms |
| Cuentas por `p_recuperacion_positiva_30d` | 27 ms | 34 ms | 11 | 1.6 ms |
| Cuentas por saldo | 976 ms | 1,063 ms | 10 | 952 ms |
| Cuentas `ALTA` y `CAMPO` | 483 ms | 500 ms | 10 | 452 ms |
| Cuentas asignadas; por principio de `cuenta_id` | 33; 23 ms | 36; 26 ms | 11 | 9.7; 0.6 ms |
| Cuenta 360 | 51 ms | 53 ms | 24 | 1.5 ms |
| Decisión, historia, línea de tiempo, pagos, gestiones | 13 a 46 ms | 15 a 52 ms | 3 a 16 | ≤ 1 ms |
| Paradas de una ruta | 15 ms | 17 ms | 3 | 0.3 ms |
| Linaje | 25 ms | 27 ms | 15 | 0.8 ms |

Las rutas de los motores que leen las pantallas, sobre el mismo contexto: de 10 a 27 ms de mediana
(la ejecución, la serie y los desgloses de analytics; la ficha del modelo, el scoring, el drift y
sus predicciones; la ejecución, la capacidad, las restricciones, las asignaciones y el diagnóstico de
la optimización; el plan y las cargas de campo; la ejecución, las rutas y una ruta de ruteo), y las
explicaciones de una cuenta, 112 ms (la predictiva) y 53 ms (la de la optimización). El reporte
completo, con cada plan, lo escribe el script.

Y por pantalla, con el build de producción en Chromium (`web/scripts/medir-paginas.mjs`, cada una 5
veces en un contexto nuevo del navegador, sin cache), desde la navegación hasta su contenido:

| Pantalla | Mediana | Peticiones de datos | Datos | Total |
| --- | ---: | ---: | ---: | ---: |
| Resumen | 824 ms | 3 | 156 KiB | 412 KiB |
| Cuentas | 303 ms | 2 | 26 KiB | 165 KiB |
| Cuenta 360 | 285 ms | 2 | 13 KiB | 273 KiB |
| Analytics | 446 ms | 7 | 176 KiB | 433 KiB |
| Predictivo: modelo | 270 ms | 2 | 133 KiB | 391 KiB |
| Predictivo: drift | 393 ms | 3 | 155 KiB | 413 KiB |
| Optimización: resumen | 273 ms | 2 | 14 KiB | 272 KiB |
| Optimización: asignaciones | 294 ms | 3 | 34 KiB | 292 KiB |
| Campo | 288 ms | 3 | 34 KiB | 176 KiB |
| Ruteo | 315 ms | 3 | 54 KiB | 194 KiB |

Los presupuestos, fijados antes de medir, se cumplen: el resumen hace 3 peticiones de datos (hasta
8), la Cuenta 360, 2 (hasta 10), y ninguna pantalla baja más de 176 KiB de datos (hasta 2 MiB). El
catálogo de métricas (141 KiB) es la mitad de lo que baja el resumen, una vez por sesión.

**El benchmark encontró tres defectos, y se corrigieron antes del PR.** En la primera corrida, el
resumen y la distribución tardaban 1.9 s: contar cada décimo con `width_bucket` sobre `numeric` y
`GROUPING SETS` ordenaba las 500,000 predicciones tres veces; ahora es una sola pasada en paralelo,
con un `FILTER` de comparaciones exactas por décimo (y sin punto flotante, que podría poner en otro
décimo un valor que cae justo en su frontera), y tarda 0.52 s. La página 10,000 del explorador
tardaba 2.9 s, y ordenar por `p_recuperacion_positiva_30d`, 1.5 s: unían las 500,000 cuentas del
corte con cada ejecución antes de quedarse con 50; ahora las llaves de la página salen primero, de
la tabla más chica que basta (el índice del corte, el de la ejecución que filtra o, cuando el scoring
tiene una predicción por cada cuenta del corte, su índice por probabilidad), y solo esas 50 se
completan: 62 ms y 27 ms. Una prueba compara cada orden, sentido y filtro, página por página, con
la lista entera filtrada y ordenada aparte, por los dos caminos.

**Lo que se dejó como está, medido.** Ordenar por saldo (y por días de atraso) recorre
`snapshot_cuenta` en paralelo y ordena las 500,000 cuentas del corte: 976 ms. Un índice
(corte, saldo, cuenta) lo baja a 0.14 ms, pero sobre la base del benchmark ocupa 381 MB (el 20 % de
la tabla), tarda 24 s en construirse y cada ingesta de un corte lo pagaría, igual que el de días de
atraso. Contar las cuentas `ALTA` y `CAMPO` juntas revisa las filas de una de las dos en su tabla:
483 ms. v0.17 no agrega índices ni la migración 0018 por un orden y un filtro combinado que no son
los de omisión: es de v0.18 si el uso lo pide ([decisión 270](decisiones.md#270-sin-índices-nuevos-ni-particionado-sin-evidencia)).

## La sesión (v0.18)

La app pide `GET /app/v1/sesion` antes de mostrar una pantalla:

- **Sin sesión** (`401`): «Inicia sesión», con un enlace a `/oauth2/start?rd=<la pantalla pedida>`;
  después del inicio de sesión regresa a esa pantalla. Si una sesión vence mientras se usa la app,
  la siguiente lectura responde `401` y la app dice «Tu sesión expiró».
- **Con una sesión sin `mc.read`** (`403`): «Sin permiso de lectura».
- **Con sesión**: la cabecera dice «Sesión iniciada» y sus permisos en palabras (lectura,
  auditoría...), con un botón «Cerrar sesión», un `POST` del mismo origen a `/oauth2/sign_out` que
  borra la sesión también del servidor. La sección Auditoría solo aparece con `mc.audit`; una
  pantalla cuya lectura responde `403` lo dice («Tu sesión no tiene permiso para ver esto»).
- El pie dice «Sesión autenticada sobre datos sintéticos; la app no guarda ningún token en el
  navegador».

Las pruebas de Playwright lo recorren contra el compose de producción, con usuarios de Dex
(`web/e2e/sesion.spec.ts`).

## Lo que la app no hace

- No ejecuta nada ni escribe nada: es de solo lectura.
- No administra usuarios, roles ni grupos (son del proveedor de identidad) ni guarda preferencias en
  la base. El compose de desarrollo no tiene usuarios: no se despliega así ni con datos reales.
- No muestra datos personales ni coordenadas, ni dibuja mapas.
- Analytics es descriptivo, una predicción no es un efecto causal ni una recomendación, y un plan
  es óptimo respecto al modelo declarado, no una estrategia causalmente óptima.
- No exporta datos desde el navegador.
