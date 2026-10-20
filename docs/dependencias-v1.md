# Dependencias antes de v1.0

El triage de los PRs de Dependabot abiertos al cerrar v0.18.0 (2026-10-19, `main` en `6e6a3a5`),
al día con v0.18.1: **#21, el de seguridad, se fusionó** (el merge `87615a1`, con el CI verde en sus
cuatro trabajos), y los demás siguen abiertos con su decisión. Cada uno se decide en su propio PR y
con el CI completo; v0.18.1 es la base para empezar v1.0. La política de Dependabot está en
[cadena-de-suministro.md](cadena-de-suministro.md#dependabot).

Clases: **A**, candidata para evaluar antes de v1.0; **B**, diferir hasta después de v1.0; **C**,
redundante o superpuesta; **D**, requiere una investigación específica; **E**, de seguridad.

| PR | Componente | Actual → propuesta | Tipo | Ámbito | CI | Clase | Decisión |
| --- | --- | --- | --- | --- | --- | --- | --- |
| #20 | Jaeger | 2.21.0 → 2.22.0 | minor | observabilidad (perfil) | verde | A | EVALUAR_ANTES_V1 |
| #21 | oauth2-proxy | v7.15.4 → v7.15.5 | patch | runtime: identidad (BFF) | verde | E | **FUSIONADO** en v0.18.1 (`87615a1`) |
| #22 | OTel Collector | 0.161.0 → 0.162.0-386 | minor | observabilidad | verde | D | EVALUAR_ANTES_V1, con `0.162.0` |
| #23 | astral-sh/setup-uv | 10.2.0 → 10.3.0 | minor | CI | verde | A | EVALUAR_ANTES_V1 |
| #24 | OSRM | v26.9.0 → 26.10.0 | minor | runtime: ruteo | rojo | D | DIFERIR_POST_V1 |
| #25 | Valkey | 8.1.10 → 9.1.2 | major | runtime: sesiones | verde | B | DIFERIR_POST_V1 |
| #26 | uv (imagen) | 0.12.21 → 0.13.1 | minor de 0.x | build | verde | D | DIFERIR_POST_V1 |
| #27 | Python (imagen) | 3.12 → 3.14 | major | runtime: API y worker | rojo | B | DIFERIR_POST_V1 |
| #28 | PostGIS (imagen de respaldo) | 16 → 17 | major | operación: respaldo | rojo | B | DIFERIR_POST_V1 |
| #29 | nginx-unprivileged | 1.30.6 → 1.31.6 | estable → mainline | runtime: edge | verde | D | DIFERIR_POST_V1 |
| #30 | Node (imagen) | 24.21 → 26.11 | major | build: app web | rojo | B | DIFERIR_POST_V1 |
| #31 | grupo Python (10) | ver abajo | 1 major | runtime y desarrollo | verde | D | EVALUAR_ANTES_V1, sin pyarrow 26 |
| #32 | grupo web (14) | ver abajo | 6 majors | app web y sus pruebas | rojo | B | DIFERIR_POST_V1 |

## Por qué

- **#21, de seguridad, fusionado.** v7.15.5 corrige dos avisos críticos (GHSA-63jm-59jj-478j,
  bypass por la interpretación de las rutas de `skip-auth`; GHSA-wr5q-7wxw-x568, bypass por
  cabeceras de IP del cliente falsificadas), uno moderado (GHSA-hhqp-vx7f-5c6m, credenciales en el
  log de errores del callback) y cuatro CVE de Go. En esta configuración los dos críticos no
  aplicaban: no hay reglas `skip-auth` ni `--trusted-ip`, el edge reescribe `X-Real-IP` y
  `X-Forwarded-For` con la IP del socket en las rutas de inicio y cierre de sesión, y oauth2-proxy
  solo confía en ellas desde la red de identidad (`--trusted-proxy-ip`). El moderado **sí
  aplicaba**: el aviso dice que apagar el log de autenticación o el de peticiones no suprime esas
  llamadas, así que `--auth-logging=false` no lo mitigaba; v7.15.5 lo corrige. Antes de fusionarlo,
  el CI del compose de producción repitió con la versión nueva el inicio de sesión con PKCE, la
  cookie `__Host-`, la repetición del callback, las cabeceras falsificadas que no elevan, el cierre
  de sesión con su revocación y Playwright con los usuarios de Dex. La imagen va fijada por el
  digest de su manifiesto multiplataforma.
- **#22.** `0.162.0-386` es la imagen de un solo sistema, linux/386; la multiarquitectura es
  `0.162.0`. El CI pasa porque un runner amd64 ejecuta binarios i386. No se fusiona así.
- **#24.** La imagen de OSRM está fijada en tres lugares: los dos compose, `red_vial.IMAGEN_OSRM` (la
  que prepara la red) y el CI. Dependabot solo cambia los compose, y la prueba
  `test_el_compose_arranca_osrm_con_la_misma_imagen_que_prepara_la_red` lo detiene. Cambiar de
  versión es un cambio coordinado que vuelve a preparar las redes viales.
- **#26.** El uv de la imagen, el `version` de setup-uv en el CI y el que generó `uv.lock` son el
  mismo (0.12.21) a propósito: se mueven juntos o no se mueven.
- **#27.** Además del cambio de runtime (el CI prueba en 3.12), la base `python:3.14-slim` trae
  paquetes con 4 vulnerabilidades altas con corrección (msgpack, setuptools y urllib3) que Trivy
  detiene. No vienen de `uv.lock`, que ya tiene urllib3 2.8.0.
- **#28.** La herramienta de respaldo debe ser del mismo mayor que el servidor (PostGIS 16 en los
  compose): con la 17, la restauración falla. Un mayor de PostgreSQL necesita su propio plan.
- **#29.** nginx usa los menores pares para la rama estable y los impares para la mainline. Se queda
  en la estable 1.30.x hasta la siguiente estable.
- **#30.** `web/package.json` pide `node >=24.15.0 <25` y `web/.node-version` fija 24.21.0: Node 26
  cambia esos dos, la imagen y `@types/node` (#32) a la vez.
- **#31.** pandera 0.33.1 → 0.34.1, sqlmodel 0.0.47 → 0.0.48, typer 0.27.2 → 0.27.3, fastapi
  0.142.1 → 0.143.0, **pyarrow 25.0.1 → 26.0.0**, numpy 2.5.3 → 2.5.4, opentelemetry (api, sdk y
  exportador) 1.45.0 → 1.45.1 y ruff 0.16.9 → 0.17.0. Los cambios incompatibles de fastapi
  (configuración automática de OpenTelemetry) y de ruff (Python por omisión) no aplican: la
  telemetría nativa de FastAPI está apagada y el proyecto declara su Python. pyarrow escribe los
  Parquet de las fuentes conformadas, y un mayor puede cambiar sus bytes: se evalúa aparte.
- **#32.** Trae seis majors (typescript 5.9 → 7.0, vitest y @vitest/coverage-v8 4 → 5, msw 2 → 3,
  @tanstack/react-table 8 → 9, @types/node 24 → 26), y `npm ci` falla: openapi-typescript 7.13 no
  acepta typescript 7.

## Superposiciones

- #30 y #32: `@types/node` 26 supone Node 26, que también cambia `engines` y `web/.node-version`.
- #23 y #26: setup-uv y la imagen de uv son la misma cadena de herramientas.
- #31 y #32 no se superponen con los PRs individuales, que son de imágenes y acciones.

## Seguridad

Desde v0.18.1, las **alertas de Dependabot y sus actualizaciones de seguridad están activadas** (y
el reporte privado de vulnerabilidades, en la [política de seguridad](../.github/SECURITY.md)), con
0 alertas abiertas al publicarla. Los 12 PRs que siguen abiertos son actualizaciones de versión, y
ninguno es una actualización de seguridad. Además está el CI: pip-audit, npm audit, Trivy sobre los
lockfiles y las imágenes, gitleaks y CodeQL, en 0 sobre `main`. El único PR con correcciones de
seguridad era #21.

## Reglas

Ningún PR de Dependabot se fusiona por reflejo, ni con el CI verde; no hay auto-merge; ninguna versión
se ignora para siempre. Los majors esperan a después de v1.0, salvo una razón de seguridad. Un grupo y
un PR individual de la misma dependencia no se fusionan sin un plan.
