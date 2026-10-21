# Versionado y compatibilidad

Desde v1.0.0, motor-cartera sigue [Semantic Versioning 2.0.0](https://semver.org/lang/es/) sobre la
superficie pública que define este documento. Lo que no está aquí no es un contrato: puede cambiar en
cualquier versión, y el CHANGELOG lo dice cuando cambia.

## Mayor, menor y parche

| Versión | Cuándo |
| --- | --- |
| **Mayor** (2.0.0) | Rompe la superficie estable: quita o renombra una operación, un campo de una respuesta, un código de error, una columna de un contrato de archivo, un comando, una opción o una variable documentada; cambia el significado de algo ya publicado; o exige que el operador haga algo a mano para seguir funcionando |
| **Menor** (1.1.0) | Agrega sin romper: operaciones, campos, comandos y opciones nuevas; la versión nueva de un contrato o de un motor junto a la anterior; una variable nueva con un valor por omisión que conserva el comportamiento; otra versión de Python; marcar algo como obsoleto |
| **Parche** (1.0.1) | Corrige sin cambiar la superficie: un error, una vulnerabilidad, una dependencia, la documentación |

Un cliente de la API ignora los campos que no conoce y trata un `codigo` de error desconocido por su
estado HTTP: así, agregar un campo o un código es un cambio menor.

## La superficie estable

| Superficie | Qué promete | Dónde se define |
| --- | --- | --- |
| La API REST | Las rutas, los métodos, los parámetros, los cuerpos, los campos de cada respuesta y sus estados HTTP | `openapi-vX.Y.Z.json` de cada release, [api.md](api.md) |
| Los errores | La forma `{codigo, mensaje, detalles, run_id, pagos_run_id}` y cada `codigo`; `mensaje` es para personas y puede cambiar | [api.md](api.md), [security.md](security.md) |
| Los permisos | Los cuatro permisos (`mc.read`, `mc.audit`, `mc.execute`, `mc.configure`) y el que pide cada operación | [matriz-autorizacion.md](matriz-autorizacion.md) |
| Los contratos de archivo | `cartera/v1`, `cartera/v2` (con CARRIER) y `pagos/v1`: columnas, orden, tipos y reglas | [diccionario_cartera.md](diccionario_cartera.md), [diccionario_pagos.md](diccionario_pagos.md), [carrier.md](carrier.md) |
| Lo que publica cada motor | `decision/v1` y `v2`, `territorial/v1` y `v2`, `ruteo/v1` y `v2`, `campo/v2`, `motor-pagos/v1`, `atribucion/v1`, `analytics/v1`, `predictive/model-v1` y `optimization/v1`, con sus políticas públicas y sus hashes | el documento de cada motor ([índice](README.md#motores)) |
| La superficie de la app | `app/v1` y la lista blanca de su proxy | [app.md](app.md) |
| Los identificadores públicos | `cuenta_id`, `corte_id` y `pago_observado_id`: el UUID v5 de su llave natural, el mismo en cualquier reconstrucción | [historia.md](historia.md) |
| Las exportaciones | Los formatos y las columnas documentadas de `exportar-analytics`, `exportar-optimizacion` y `exportar-plan-campo` | [analytics.md](analytics.md), [optimizacion.md](optimizacion.md), [campo_v2.md](campo_v2.md) |
| La línea de comandos | `motor-cartera`, sus comandos y opciones documentadas y sus códigos de salida documentados | [flujo_completo.md](flujo_completo.md), [desarrollo.md](desarrollo.md) |
| La configuración | Cada variable `MC_*` y `*_FILE` documentada, su significado, su valor por omisión y lo que niega al arrancar | [production.md](production.md#la-configuración), `.env.example` |
| Los formatos de la operación | El respaldo (`respaldo-motor-cartera/v1`) y el evento de auditoría (`security-audit/v1`) | [backups.md](backups.md), [security.md](security.md#la-auditoría-security-auditv1) |
| Python | `requires-python`: 3.12, 3.13 y 3.14, cada una probada; las imágenes corren 3.12 | `pyproject.toml`, [desarrollo.md](desarrollo.md#el-ci) |

## Lo que no es contrato

- **El esquema de PostgreSQL**: las tablas, las columnas, los índices, las vistas y la cola durable.
  Se leen y se escriben por la API y la línea de comandos; una integración que lea las tablas puede
  romperse en cualquier versión, aunque sea un parche.
- **El código de Python** (`motor_cartera.*`): es una aplicación, no una biblioteca.
- **El texto para personas**: `mensaje`, la salida de texto de la línea de comandos, los logs (salvo
  `security-audit/v1`), las pantallas de la app y sus URLs.
- **Los datos del generador sintético**: su semilla los reproduce dentro de una versión, no entre
  versiones.
- **La observabilidad y la operación local**: los nombres de las métricas, las alertas y los paneles,
  los servicios y perfiles de los compose, los scripts de los benchmarks y de las pruebas.
- **La referencia de Google Cloud** (`infra/gcp`): nunca se aplicó ([cloud-gcp.md](cloud-gcp.md)).

## Las superficies versionadas

Lo que lleva su propia versión en el nombre (`cartera/v2`, `decision/v2`, `app/v1`,
`analytics-metrics/public-v1`, `respaldo-motor-cartera/v1`...) no cambia en su lugar: un
comportamiento distinto es otra versión (`decision/v3`, `app/v2`), que llega en un menor junto a la
anterior. La anterior se sigue aceptando y leyendo en todo 1.x, y lo que ya se publicó con ella no se
reescribe. Dejar de aceptarla es un cambio mayor.

## La obsolescencia

Una parte de la superficie estable se retira en dos pasos: un menor la marca obsoleta (en el
CHANGELOG, en su documento y, si es una operación de la API, con `deprecated: true` en el OpenAPI),
con lo que la reemplaza, y sigue funcionando en todo 1.x; el siguiente mayor la quita. Nada se quita
sin un menor que lo haya anunciado.

## La base de datos y los respaldos

- **Subir de versión** dentro de 1.x es aplicar las migraciones de la versión nueva: el servicio
  `migraciones` del compose (`alembic upgrade head`). Bajar de versión no se promete.
- **Un respaldo** se restaura con la misma versión, la de su revisión del esquema. El de otra versión
  se niega (`REVISION_INCOMPATIBLE`) salvo con `--permitir-otra-revision`, y después hay que migrar
  ([backups.md](backups.md)).

## La configuración

Una variable nueva llega con un valor por omisión que conserva el comportamiento, salvo que su
ausencia sea insegura: entonces falla cerrada, y es un cambio de seguridad (abajo). Renombrar o quitar
una variable documentada es un cambio mayor, anunciado antes en un menor con el nombre viejo todavía
aceptado.

## La seguridad

Una corrección de seguridad sale en un parche, o en un menor si agrega algo. Si cerrar una
vulnerabilidad exige romper la superficie estable (negar una configuración insegura que antes se
aceptaba, por ejemplo), sale igual en 1.x, con la ruptura explicada en el CHANGELOG y en su aviso: la
seguridad pesa más que la compatibilidad. Qué versiones reciben correcciones lo dice la
[política de seguridad](../.github/SECURITY.md).

## Cómo sale una versión

Cada versión es una etiqueta anotada `vX.Y.Z` sobre un commit de `main` con el CI verde, con su
release: el OpenAPI completo, el SBOM de cada imagen propia, sus SHA-256 y la atestación de
procedencia de cada uno. Lo hace el workflow de release, que se niega antes de publicar si falta
cualquier condición ([cadena-de-suministro.md](cadena-de-suministro.md#las-releases)).
