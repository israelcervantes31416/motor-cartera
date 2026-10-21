# Changelog

Las versiones de motor-cartera, de la más reciente a la más antigua, con su fecha de publicación
(UTC) y lo esencial de cada una. Las notas completas están en su release de GitHub. El formato sigue
[Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/), y las versiones,
[Semantic Versioning](https://semver.org/lang/es/): desde v1.0.0, sobre la superficie que define
[docs/versioning.md](docs/versioning.md); en `0.x`, una versión menor podía cambiar un contrato.

## [Unreleased]

- Documentación: sincroniza el triage de Dependabot con la cola regenerada después de v1.0.0; sin cambios de runtime, dependencias ni contratos públicos.

## [v1.0.0] — Collection Intelligence Platform — 2026-10-21

La primera versión estable: lo que construyeron v0.1.0 a v0.18.1, cerrado como una plataforma con
una frontera que se promete mantener. No agrega motores.

- **Semantic Versioning desde 1.0.0**, sobre una superficie pública escrita: la API y la forma de sus
  errores, los permisos, los contratos de archivo, lo que publica cada motor por su versión, `app/v1`,
  los identificadores públicos, las exportaciones, la línea de comandos y la configuración
  documentadas, con su política de obsolescencia ([docs/versioning.md](docs/versioning.md)). El
  esquema de PostgreSQL no es parte de ella.
- **Python 3.12, 3.13 y 3.14**, cada una probada en una matriz ligera: el entorno exacto del lock,
  cada módulo importado, el mismo OpenAPI byte por byte, la línea de comandos y las pruebas puras de
  cada motor. `requires-python` deja de prometer lo que no se prueba: `>=3.12,<3.15`.
- **La app, consistente**: cada pantalla de un motor con la misma estructura, ninguna que se desborde
  a 320 px, los filtros alineados, un ancho de lectura para el texto y los colores y medidas sueltos
  como tokens; la prueba de móvil, que nunca fallaba, ahora compara con el ancho del dispositivo.
- **Una señal de rendimiento** semanal en el perfil M: la ingesta, la historia, el motor de pagos, el
  lifecycle y la decisión, contra una línea base medida en los mismos runners
  ([docs/rendimiento.md](docs/rendimiento.md)). Informa; no bloquea.
- **Releases que se niegan antes de publicar**: un workflow a mano revisa la versión, la cabeza de
  `main`, cada check obligatorio (leído de su protección), CodeQL sin alertas abiertas y que la
  etiqueta no exista; publica el OpenAPI, los SBOM y sus SHA-256 con la atestación de procedencia de
  cada uno, y verifica lo publicado.
- **Seguridad**: vite 8.3.4 en la app web (GHSA-698v-4r3x-3vcv, del servidor de desarrollo en
  Windows); cada alerta de CodeQL clasificada, con una corregida; cada permiso de escritura de un
  workflow en una lista explícita; y cada versión mayor de una dependencia, en su propio PR.
- Jaeger 2.22.0, setup-uv 10.3.0 y el CI fijado en Ubuntu 24.04.

## [v0.18.1] — Parche de Seguridad y Calidad del Repositorio — 2026-10-20

- oauth2-proxy v7.15.5 en el compose de producción: corrige GHSA-hhqp-vx7f-5c6m (credenciales en
  el log del callback de OAuth), que afectaba a la v7.15.4 de esta configuración; los otros dos
  avisos de esa versión (GHSA-63jm-59jj-478j y GHSA-wr5q-7wxw-x568) no aplicaban a ella.
- Verificación de tipos de Python con Pyright en el CI (standard en todo `src/motor_cartera`,
  strict en 13 módulos), análisis estático con CodeQL, alertas y actualizaciones de seguridad de
  Dependabot y reporte privado de vulnerabilidades.
- El README como portada, la documentación reorganizada por tema (índice, arquitectura, API,
  desarrollo y limitaciones actuales), este CHANGELOG y la política de seguridad. Sin cambios de
  funcionalidad.

## [v0.18.0] — Cloud, Observabilidad y Hardening: Production Readiness — 2026-10-19

- Un compose de producción con un edge de TLS como única puerta, OpenID Connect con PKCE por
  oauth2-proxy, una API que valida cada token y autoriza cada ruta por una matriz de cuatro
  permisos, secretos como archivos y contenedores sin raíz, sin capacidades y de solo lectura.
- Logs, métricas, trazas y auditoría sin datos personales, alertas con sus runbooks, respaldos de la
  base y del almacén restaurados en un entorno nuevo, simulacros de falla y carga con k6.
- La cadena de suministro fijada y escaneada (Trivy, pip-audit, npm audit, gitleaks, SBOM) y una
  referencia de Google Cloud en OpenTofu, validada en el CI y nunca aplicada.

## [v0.17.0] — App + Dashboards: Superficie Operacional y Cuenta 360 — 2026-10-18

- Una app web de solo lectura (React y TypeScript estricto, servida por nginx con un proxy de solo
  `GET` y lista blanca) sobre `app/v1`: el contexto operativo explícito, el resumen por motor, el
  explorador de cuentas, la Cuenta 360 y las pantallas de analytics, predictivo, optimización,
  campo y ruteo.
- Sin datos personales ni coordenadas; tipos generados del OpenAPI, Vitest, Playwright con axe-core
  y presupuestos del bundle.

## [v0.16.0] — Optimización Matemática: Priorización, Capacidad y Asignación Restringida — 2026-10-17

- `optimization/v1`: un MILP lexicográfico exacto con HiGHS sobre entradas declaradas (un scoring,
  la `decision/v2` del mismo corte, una configuración y, con `CAMPO`, su `ruteo/v2`), con
  capacidades, presupuesto y coberturas.
- Solo se publica un óptimo demostrado (cota = objetivo, gap 0 en cada nivel), con su certificado,
  su hash y la huella del solver. En sombra.

## [v0.15.0] — Predictive Intelligence: Modelos Temporales, Calibración y Drift — 2026-10-16

- `predictive/model-v1`: cuatro modelos (recuperación positiva a 30 días, contacto a 14, promesa a
  30 y recuperación positiva esperada) con targets, features y política versionados, y un
  manifiesto temporal con tres relojes, censura y purga.
- Publicados solo si superan su baseline en TEST; scoring con `feature_hash`, explicación
  verificada y drift contra TRAIN, en sombra.

## [v0.14.0] — Collection Analytics: Métricas, Cohortes y Embudos Point-in-Time — 2026-10-15

- `analytics/v1`: una cohorte anclada a un corte, descrita point-in-time con un manifiesto
  explícito, un catálogo de 87 métricas y su política de publicación con sus hashes.
- Supresión de celdas de menos de 10 cuentas, controles de reconciliación exactos y atribución no
  causal.

## [v0.13.0] — Ruteo vial v2: Red Vial, Secuenciación y Factibilidad de Jornada — 2026-10-14

- Redes viales versionadas por su huella (OpenStreetMap preparado con OSRM v26.9.0) y `ruteo/v2`:
  la ruta de cada gestor por calle, con snap, matrices por bloques, vecino más cercano y 2-opt,
  turnos partidos y ETA planificadas sin tráfico.

## [v0.12.0] — Campo v2: Capacidad, Partición y Asignación Operativa — 2026-10-11

- La configuración de campo versionada y `campo/v2`: la capacidad de cada base repartida por
  prioridad en una partición fija en EPSG:6372, con Rendezvous y spillover dentro de la base.

## [v0.11.0] — Geografía Real, Territorial v2 y Bases Operativas — 2026-10-11

- La referencia municipal del INEGI importada y versionada con PostGIS; el municipio espacial de
  cada cuenta con su estado de calidad, sin corregir nada.
- Bases operativas versionadas y `territorial/v2`: la base activa inequívocamente más cercana.

## [v0.10.0] — Decision Engine v2: Punto en el Tiempo y Explicabilidad — 2026-10-11

- `decision/v2`: 43 features point-in-time (`as_of` y `known_at`), una política pública con su hash
  y cada decisión guardada con sus features, sus motivos y sus hashes, en sombra frente a v1.

## [v0.9.0] — Lifecycle de Cobranza y Atribución Operativa — 2026-10-10

- El lifecycle como eventos que solo se agregan, con idempotencia garantizada por PostgreSQL; la
  evaluación de promesas a una fecha de corte y la atribución operativa, sin causalidad.

## [v0.8.0] — Motor de Pagos Canónico y Conciliación — 2026-10-09

- `motor-pagos/v1`: los pagos observados interpretados en movimientos canónicos (duplicados,
  coincidencias ambiguas, reversos) y conciliados con su cuenta, sin tocar el original.

## [v0.7.0] — Modelo Histórico y Cuenta 360 — 2026-10-07

- `historia/v1`: cuentas canónicas, cortes, snapshots inmutables y pagos observados, materializados
  desde el dataset conformado; la Cuenta 360 por la API.

## [v0.6.0] — Fuentes Oficiales, Evidencia Inmutable y Escala — 2026-10-05

- `cartera/v2` (93 columnas) y `pagos/v1` (23) con estructura exacta, un almacén de artefactos por
  contenido, datasets conformados en Parquet con su linaje, y la ingesta de 500,000 cuentas con
  memoria acotada.

## [v0.5.0] — Orquestación Durable — 2026-10-04

- Una cola durable sobre PostgreSQL con lease, latido y reintentos, un worker independiente de la
  API y el flujo automático de la ingesta al ruteo.

## [v0.4.0] — Motor de Ruteo — 2026-10-03

- `ruteo/v1`: la secuencia de visita de cada municipio con trabajo de campo, sobre coordenadas
  sintéticas y deterministas, con vecino más cercano y 2-opt.

## [v0.3.0] — Motor Territorial — 2026-10-03

- `territorial/v1`: la carga de campo de cada municipio y el orden en que conviene atenderlos.

## [v0.2.0] — Decision Engine — 2026-10-02

- `decision/v1`: segmento, prioridad y canal recomendado por cuenta, explicados con sus motivos y
  publicados todo o nada.

## [v0.1.0] — Fase 1 — 2026-09-30

- Ingesta y certificación: una cartera se publica solo si pasa su contrato, y cada rechazo queda con
  su motivo.

[Unreleased]: https://github.com/israelcervantes31416/motor-cartera/compare/v1.0.0...HEAD
[v1.0.0]: https://github.com/israelcervantes31416/motor-cartera/releases/tag/v1.0.0
[v0.18.1]: https://github.com/israelcervantes31416/motor-cartera/releases/tag/v0.18.1
[v0.18.0]: https://github.com/israelcervantes31416/motor-cartera/releases/tag/v0.18.0
[v0.17.0]: https://github.com/israelcervantes31416/motor-cartera/releases/tag/v0.17.0
[v0.16.0]: https://github.com/israelcervantes31416/motor-cartera/releases/tag/v0.16.0
[v0.15.0]: https://github.com/israelcervantes31416/motor-cartera/releases/tag/v0.15.0
[v0.14.0]: https://github.com/israelcervantes31416/motor-cartera/releases/tag/v0.14.0
[v0.13.0]: https://github.com/israelcervantes31416/motor-cartera/releases/tag/v0.13.0
[v0.12.0]: https://github.com/israelcervantes31416/motor-cartera/releases/tag/v0.12.0
[v0.11.0]: https://github.com/israelcervantes31416/motor-cartera/releases/tag/v0.11.0
[v0.10.0]: https://github.com/israelcervantes31416/motor-cartera/releases/tag/v0.10.0
[v0.9.0]: https://github.com/israelcervantes31416/motor-cartera/releases/tag/v0.9.0
[v0.8.0]: https://github.com/israelcervantes31416/motor-cartera/releases/tag/v0.8.0
[v0.7.0]: https://github.com/israelcervantes31416/motor-cartera/releases/tag/v0.7.0
[v0.6.0]: https://github.com/israelcervantes31416/motor-cartera/releases/tag/v0.6.0
[v0.5.0]: https://github.com/israelcervantes31416/motor-cartera/releases/tag/v0.5.0
[v0.4.0]: https://github.com/israelcervantes31416/motor-cartera/releases/tag/v0.4.0
[v0.3.0]: https://github.com/israelcervantes31416/motor-cartera/releases/tag/v0.3.0
[v0.2.0]: https://github.com/israelcervantes31416/motor-cartera/releases/tag/v0.2.0
[v0.1.0]: https://github.com/israelcervantes31416/motor-cartera/releases/tag/v0.1.0
