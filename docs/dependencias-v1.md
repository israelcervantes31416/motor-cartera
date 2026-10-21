# Las dependencias en v1.0

El triage de los PRs de Dependabot al publicar v1.0.0 (2026-10-21). Los que se fusionaron, cada uno en
su propio PR y con el CI completo verde; los demás, abiertos y con su decisión: **ninguno bloquea
v1.0**, y ninguno se fusiona por reflejo. La política de Dependabot está en
[cadena-de-suministro.md](cadena-de-suministro.md#dependabot).

## Fusionados desde v0.18.0

| PR | Componente | Cambio | Por qué |
| --- | --- | --- | --- |
| #21 | oauth2-proxy | v7.15.4 → v7.15.5 | **Seguridad**, en v0.18.1: GHSA-hhqp-vx7f-5c6m sí aplicaba (abajo) |
| #23 | astral-sh/setup-uv | 10.2.0 → 10.3.0 | La acción del CI; el `uv` que instala sigue fijado en 0.12.21 |
| #20 | Jaeger | 2.21.0 → 2.22.0 | Un menor del perfil de observabilidad |
| #36 | vite | 8.3.2 → 8.3.4 | **Seguridad**: GHSA-698v-4r3x-3vcv (abajo) |

## Abiertos, y por qué no bloquean v1.0

| PR | Componente | Actual → propuesta | Tipo | CI | Decisión |
| --- | --- | --- | --- | --- | --- |
| #22 | OTel Collector | 0.161.0 → 0.162.0-386 | menor | verde | No así: `-386` es la imagen de linux/386; la multiplataforma es `0.162.0` |
| #24 | OSRM | v26.9.0 → 26.10.0 | menor | rojo | Después de v1.0: un cambio coordinado que vuelve a preparar las redes |
| #25 | Valkey | 8.1.10 → 9.1.2 | mayor | verde | Después de v1.0: una migración de las sesiones, aparte |
| #26 | uv (imagen) | 0.12.21 → 0.13.1 | menor de 0.x | verde | Después de v1.0: se mueve junto con el CI y el que genera `uv.lock` |
| #27 | Python (imagen) | 3.12 → 3.14 | menor del runtime | rojo | No: el runtime de producción sigue en 3.12 (3.14 se prueba en la matriz) |
| #28 | PostGIS (respaldo) | 16 → 17 | mayor | rojo | No: el respaldo es del mismo mayor que el servidor |
| #29 | nginx-unprivileged | 1.30.6 → 1.31.6 | estable → mainline | verde | No: se queda en la rama estable 1.30.x |
| #30 | Node (imagen) | 24.21 → 26.11 | mayor | rojo | Después de v1.0: Node 26 cambia también `engines` y `web/.node-version` |
| #31 | grupo Python (10) | ver abajo | 1 mayor | rojo | Después de v1.0, separado en sus partes |
| #37 | grupo web (14) | ver abajo | 6 mayores | rojo | Después de v1.0, separado en sus partes |

## Por qué

- **#21 se fusionó en v0.18.1.** v7.15.5 corrige dos avisos críticos (GHSA-63jm-59jj-478j y
  GHSA-wr5q-7wxw-x568), que en esta configuración no aplicaban (no hay reglas `skip-auth` ni
  `--trusted-ip`, y el edge reescribe las cabeceras de la IP del cliente), y uno moderado,
  GHSA-hhqp-vx7f-5c6m (credenciales en el log del callback), que **sí aplicaba**: apagar el log de
  autenticación no lo mitigaba. Antes de fusionarlo, el CI del compose de producción repitió el
  inicio de sesión con PKCE, la cookie `__Host-`, el callback repetido, las cabeceras falsificadas, el
  cierre de sesión y Playwright con los usuarios de Dex.
- **#36 se fusionó antes de v1.0.0.** GHSA-698v-4r3x-3vcv (moderado): `launch-editor`, que usa el
  servidor de desarrollo de Vite, no escapaba `%` al llamar a `cmd` en Windows. Solo afecta al
  servidor de desarrollo (la app de producción es el build estático servido por nginx), pero el
  desarrollo de este proyecto es en Windows, así que aplicaba. Es un parche de una dependencia de
  desarrollo, que Dependabot propuso como actualización de seguridad.
- **#22.** El CI pasa porque un runner amd64 ejecuta binarios i386; no se despliega una imagen de
  otra arquitectura.
- **#24.** La imagen de OSRM está fijada en tres lugares: los dos compose, `red_vial.IMAGEN_OSRM` (la
  que prepara la red) y el CI. Dependabot solo cambia los compose, y la prueba
  `test_el_compose_arranca_osrm_con_la_misma_imagen_que_prepara_la_red` lo detiene.
- **#26.** El `uv` de la imagen, el `version` de setup-uv en el CI y el que generó `uv.lock` son el
  mismo (0.12.21) a propósito: se mueven juntos o no se mueven.
- **#27.** Además del cambio de runtime, la base `python:3.14-slim` trae paquetes con
  vulnerabilidades altas con corrección (no de `uv.lock`) que Trivy detiene en el job `produccion`.
  Que el código funciona en 3.13 y 3.14 lo prueba la matriz de compatibilidad, sin cambiar la imagen.
- **#28.** Con `pg_restore` 17 contra un servidor 16, la restauración se niega. Un mayor de
  PostgreSQL necesita su propio plan.
- **#29.** nginx usa los menores pares para la rama estable y los impares para la mainline.
- **#30.** `web/package.json` pide `node >=24.15.0 <25` y `web/.node-version` fija 24.21.0: Node 26
  cambia esos dos, la imagen y `@types/node` (#37) a la vez.
- **#31.** pandera 0.33.1 → 0.34.1, sqlmodel 0.0.47 → 0.0.48, typer 0.27.2 → 0.27.3, fastapi 0.142.1 →
  0.143.0, **pyarrow 25.0.1 → 26.0.0**, numpy 2.5.3 → 2.5.4, opentelemetry 1.45.0 → 1.45.1 y ruff
  0.16.9 → 0.17.0. Está rojo porque sqlmodel 0.0.48 cambia los tipos de `select()` y Pyright (en el
  CI desde v0.18.1) los rechaza; y pyarrow escribe los Parquet de las fuentes conformadas, donde un
  mayor puede cambiar los bytes.
- **#37** reemplazó a #32 cuando #36 cambió `web/package-lock.json` (Dependabot cerró #32 el
  2026-10-21). Trae los mismos seis mayores (typescript 5.9 → 7.0, vitest y @vitest/coverage-v8 4 → 5,
  msw 2 → 3, @tanstack/react-table 8 → 9, @types/node 24 → 26), y `npm ci` falla: openapi-typescript
  7.13 no acepta typescript 7.

Desde v1.0, Dependabot propone los menores y los parches de cada ecosistema juntos y **cada mayor en
su propio PR** (`update-types` en sus grupos): los grupos #31 y #37 se rehacen así en su siguiente
corrida, y cada mayor se evalúa como la migración que es.

## La seguridad

Las alertas de Dependabot y sus actualizaciones de seguridad están activadas desde v0.18.1, y el
reporte privado de vulnerabilidades también ([política de seguridad](../.github/SECURITY.md)). La
única alerta que abrió Dependabot hasta v1.0.0 fue la de vite (#36), y se cerró con su corrección.
Además está el CI: pip-audit, npm audit, Trivy sobre los lockfiles y las imágenes, gitleaks y
CodeQL ([cadena-de-suministro.md](cadena-de-suministro.md#lo-que-se-escanea)).

## Reglas

Ningún PR de Dependabot se fusiona por reflejo, ni con el CI verde; no hay auto-merge; ninguna versión
se ignora para siempre. Una actualización de seguridad que aplica se atiende de inmediato, en su
propio PR. Un mayor es una migración: su propio PR, su plan y, si cambia la superficie estable, su
versión ([versioning.md](versioning.md)).
