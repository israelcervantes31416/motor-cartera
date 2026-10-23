# Las dependencias en v1.0

Estado de la cola de Dependabot después de v1.0.2 (2026-10-23). Este documento es una
**foto de triage**, no una orden de actualización: ningún PR se fusiona por reflejo y una cola abierta
no invalida una release estable. La política está en
[cadena-de-suministro.md](cadena-de-suministro.md#dependabot).

La rama estable sigue en Python 3.12 como runtime, PostgreSQL 16/PostGIS 3.5 y Node 24. Las versiones
3.13 y 3.14 de Python se prueban en la matriz de compatibilidad, pero no sustituyen por sí solas al
runtime de producción.

## Fusionados desde v0.18.0

| PR | Componente | Cambio | Por qué |
| --- | --- | --- | --- |
| #21 | oauth2-proxy | v7.15.4 → v7.15.5 | **Seguridad**, en v0.18.1: GHSA-hhqp-vx7f-5c6m sí aplicaba |
| #23 | astral-sh/setup-uv | 10.2.0 → 10.3.0 | Acción de CI, con la versión de uv todavía fijada explícitamente |
| #20 | Jaeger | 2.21.0 → 2.22.0 | Menor del perfil de observabilidad |
| #36 | Vite | 8.3.2 → 8.3.4 | **Seguridad**: GHSA-698v-4r3x-3vcv en el servidor de desarrollo de Windows |
| #45 | uv | 0.12.21 → 0.13.4 | **Seguridad**: actualización coordinada de Dockerfile y workflows por GHSA-hq85-5cpj-mr62 |
| #46 | Grafana | 13.1.8 → 13.2.4 | Actualización compatible validada otra vez sobre el `main` posterior a v1 y uv |
| #47 | grupo web | 8 actualizaciones compatibles | Menores/parches del frontend, validados nuevamente sobre el `main` actual |
| #48 | Vitest | 4.1.11 → 4.1.12 | **Seguridad**: GHSA-qxg6-89mg-rqv8 y GHSA-xp26-3454-mjgp en `@vitest/mocker`, una herramienta de desarrollo |
| #53 | OpenTelemetry | 1.45.0 → 1.45.1 (y 0.66b0 → 0.66b1) | **Seguridad**, en v1.0.2: GHSA-xcq7-p3pf-p88h (CVE-2026-114603) en `opentelemetry-api`; `pip-audit` detenía todo el CI |
| #56 | grupo Python | 6 de las 7 de #54 | Compatibles, sin SQLModel 0.0.48; ver abajo |

## Cola abierta actual

#50 (el grupo Python) ya no está en la cola: Dependabot lo cerró el 2026-10-22, cuando #53 movió
OpenTelemetry, y lo reemplazó #54 con las otras siete actualizaciones. Las seis compatibles entraron
por #56; en #54 queda SQLModel 0.0.48.

| PR | Componente | Actual → propuesta | Decisión |
| --- | --- | --- | --- |
| #54 | SQLModel | 0.0.47 → 0.0.48 | **Requiere trabajo**: una migración de tipos (abajo); no se fusiona así |
| #22 | OTel Collector | 0.161.0 → 0.162.0-386 | **No fusionar**: `0.162.0-386` es un manifiesto de una sola arquitectura, linux/386. La etiqueta correcta, `0.162.0`, es multiplataforma; se adopta en un PR propio cuando haga falta |
| #24 | OSRM | v26.9.0 → 26.10.0 | **Posponer**: migración coordinada de la imagen, la red preparada y su contrato |
| #25 | Valkey | 8.1.10 → 9.1.2 | **Posponer**: mayor del almacén de sesiones; es una migración, no mantenimiento |
| #28 | PostGIS (respaldo) | 16 → 17 | **Posponer**: PostgreSQL 17 es una migración de datos coordinada con el servidor |
| #29 | nginx-unprivileged | estable 1.30.6 → mainline 1.31.6 | **No fusionar**: el proyecto sigue la rama estable |
| #30 | Node (imagen) | 24.21 → 26.11 | **Posponer**: mayor del runtime del frontend |
| #40 | PostGIS (compose) | 16-3.5 → 17-3.5 | **Posponer**: el mismo cambio que #28, nunca por piezas |

Cada PR de la tabla corre su CI sobre la base en que Dependabot lo abrió; antes de cualquier merge
se actualiza a la cabeza de `main` y se vuelve a correr.

## Cómo se decidió el grupo Python

Las siete de #54, contra el `main` de v1.0.2:

| Paquete | Cambio | Lo que revisa su nota de versión | Decisión |
| --- | --- | --- | --- |
| FastAPI | 0.142.1 → 0.143.1 | 0.143.0 hace opcional la configuración automática de exportadores de OpenTelemetry. La app ya la apaga (`telemetry={..., "auto_configure": False}`) y arma su propio proveedor desde `MC_OTEL_ENDPOINT`. El OpenAPI exportado es idéntico byte por byte | compatible |
| pydantic-settings | 2.15.0 → 2.16.0 | Cambia cómo se usan los alias y pide `python-dotenv>=1.2.2`. La configuración no usa alias (`env_prefix="MC_"`), y el lock ya tiene `python-dotenv` 1.2.3 | compatible |
| pandera | 0.33.1 → 0.34.1 | Agrega TensorDict; nada que el proyecto use cambia | compatible |
| Typer | 0.27.2 → 0.27.3 | Escapa los caracteres de control de la terminal en los mensajes de error | compatible |
| NumPy | 2.5.3 → 2.5.4 | Parche: correcciones de tipos y de `linalg` con infinitos | compatible |
| Ruff | 0.16.9 → 0.17.0 | Cambian los valores por omisión de la versión de Python y de las reglas. El proyecto fija `target-version` y `select`, y lint y formato ya pasaban con 0.17.0 en #50 | compatible |
| SQLModel | 0.0.47 → 0.0.48 | `Select` pasa a ser genérico variádico (`SelectBase[Unpack[_Ts]]`, PEP 646) | **migración** |

## La migración de SQLModel 0.0.48

En 0.0.48, `Select[Any]` ya no quiere decir "cualquier select": quiere decir uno de **una** columna.
Por eso Pyright tipa como `tuple[Any]` las filas de las consultas de varias columnas: falla `*fila`,
falla `fila._mapping` y falla el desempaquetado. Son 82 errores en 18 archivos (CI de #50, run
39705015302), todos de esa causa:

- los dos ayudantes de `db/sesion.py`, `seleccionar` y `con_columnas`, que devuelven `Select[Any]`;
- 36 anotaciones `Select[Any]` en 14 módulos de consultas (`motor_pagos`, `lifecycle`, `ruteo_v2`,
  `geografia`, `decision_v2`, `campo_v2`, `evaluacion`, `territorial_v2`, `atribucion`, `api`...).

El plan:

1. Los ayudantes devuelven `Select[*tuple[Any, ...]]` (Python 3.12 lo admite) y las anotaciones de
   las consultas de varias columnas, lo mismo o su forma exacta (`Select[EjecucionX, UUID]`).
2. Pyright con SQLModel 0.0.48 llega a 0 errores sin un `# type: ignore` nuevo.
3. El CI completo pasa en el mismo PR que mueve el lock. No cambia el SQL que se emite: es solo de
   tipos, y las pruebas lo confirman.

## Notas de los casos que requieren cuidado

- **#24 (OSRM 26.10.0).** El proyecto fija OSRM en los dos compose, en el CI y en la versión con que
  se prepara la red vial (`preparar.sh`, `motor-cartera preparar-red-vial`). La etiqueta cambia de
  forma (`v26.9.0-debian` → `26.10.0-debian`). El plan: mover a la vez la imagen fijada por digest
  en esos sitios, volver a preparar la red de las pruebas y la de la prueba de humo, comprobar que
  sus snaps, duraciones y huellas siguen dentro de lo documentado
  ([red_vial.md](red_vial.md)) y registrar la versión nueva del motor de ruteo. Su CI está en rojo
  (`compose` y `pruebas`) porque mueve una sola pieza.
- **#25 (Valkey 9).** Es el almacén de sesiones de oauth2-proxy, con ACL y sin persistencia. El
  plan: revisar las notas de Valkey 9 contra esa configuración, correr la prueba de humo del edge y
  Playwright (sesión, cierre de sesión que la borra en el servidor y cookie copiada que ya no abre
  nada), y el simulacro de caída, con su nota en el CHANGELOG.
- **#28 y #40 (PostgreSQL 17).** Un volumen de PostgreSQL 16 no arranca con 17. El plan: subir a la
  vez el servidor, el cliente de respaldo y el CI; migrar con volcado y restauración (el respaldo
  `respaldo-motor-cartera/v1` usa `pg_dump` y `pg_restore`, y su simulacro de restauración se puede
  repetir contra 17) o con `pg_upgrade`; validar PostGIS 3.5 sobre 17, Alembic, `alembic check` y la
  suite completa. Como exige que quien opera migre sus datos a mano, según
  [versioning.md](versioning.md) es un cambio mayor, salvo que la migración llegue automatizada.
- **#29 (nginx 1.31).** 1.31 es la rama mainline. El proyecto sigue en la estable (1.30.x) y pasará
  a 1.32 cuando nginx la publique como estable.
- **#30 (Node 26).** Una migración del runtime del frontend: `.node-version`, `engines`, la imagen,
  los tipos, Vite, Vitest y Playwright juntos. La versión estable de v1 sigue en Node 24.
- **#22 (OTel Collector).** Que un runner pueda ejecutar la variante linux/386 no es razón para
  publicarla. Si se actualiza el colector, es a `0.162.0` fijada por el digest de su índice
  multiplataforma, en un PR propio.
- Las notas de los PR ya fusionados (#45, #46, #47 y #48) siguen en la historia de este archivo y en
  el CHANGELOG.

## Seguridad y criterio de actualización

Las alertas de Dependabot, sus actualizaciones de seguridad, CodeQL, pip-audit, npm audit, Trivy y
gitleaks siguen siendo los controles del proyecto. Una actualización de seguridad **aplicable** se
atiende con prioridad; un PR de versión normal, incluso verde, no se vuelve obligatorio por existir.
El aviso de OpenTelemetry lo encontró `pip-audit` antes que una alerta de Dependabot: un aviso nuevo
detiene el CI de todos los PR hasta que se corrige, y la corrección va sola, en su propio PR.

Desde v1.0, Dependabot agrupa parches/menores compatibles cuando tiene sentido y deja los mayores
separados. Los mayores no se ignoran para siempre: se tratan como migraciones explícitas bajo
[versioning.md](versioning.md).

## Regla operativa

- No auto-merge.
- No perseguir “0 PR abiertos”.
- No fusionar un PR que esté detrás de `main` usando resultados viejos como evidencia suficiente.
- No dividir una migración acoplada solo para aceptar PRs automáticos.
- Antes de cada merge: actualizar a la cabeza de `main`, ejecutar los checks actuales y revisar el
  diff real.
