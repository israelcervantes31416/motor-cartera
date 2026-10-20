# Documentación

El índice de la documentación de motor-cartera, por tema. La portada del proyecto es el
[README](../README.md); cómo reportar una vulnerabilidad, la [política de seguridad](../.github/SECURITY.md);
cada versión, el [CHANGELOG](../CHANGELOG.md).

## Para empezar

| Documento | Qué tiene |
|---|---|
| [flujo_completo.md](flujo_completo.md) | El recorrido completo por la API y la línea de comandos, motor por motor, y la prueba de humo |
| [desarrollo.md](desarrollo.md) | Correrlo sin Docker, los datos sintéticos, las pruebas, la verificación de tipos y el CI |
| [api.md](api.md) | Cada operación de la API, la forma de los errores y las reglas que un cliente tiene que conocer |
| [limitaciones.md](limitaciones.md) | Lo que no hace hoy, o hace con un límite conocido |

## Arquitectura

| Documento | Qué tiene |
|---|---|
| [architecture.md](architecture.md) | Los componentes, el flujo de los datos, el almacenamiento, la seguridad, la observabilidad, la referencia de despliegue y el código con su linaje |
| [orquestacion.md](orquestacion.md) | La cola durable sobre PostgreSQL, el worker, el lease, el flujo automático y sus parámetros |
| [decisiones.md](decisiones.md) | Por qué cada pieza está hecha como está: 303 decisiones numeradas, por versión (abajo) |

## Datos, contratos e historia

| Documento | Qué tiene |
|---|---|
| [fuentes.md](fuentes.md) | Las fuentes oficiales (`cartera/v2`, `pagos/v1`), el almacén por contenido, el conformado y la escala |
| [diccionario_cartera.md](diccionario_cartera.md) y [diccionario_pagos.md](diccionario_pagos.md) | Las 93 columnas de CARTERA y las 23 de PAGOS |
| [carrier.md](carrier.md) | La hoja compañera CARRIER y su auditoría |
| [historia.md](historia.md) | El modelo histórico: cuentas canónicas, cortes, snapshots y pagos observados |
| [cuenta_360.md](cuenta_360.md) | La API de la Cuenta 360 |

## Motores

| Documento | Qué tiene |
|---|---|
| [motores_v1.md](motores_v1.md) | `decision/v1`, `territorial/v1` y `ruteo/v1`: las etapas del flujo automático |
| [motor_pagos.md](motor_pagos.md) | `motor-pagos/v1`: duplicados, reversos, conciliación y recuperación interpretada |
| [lifecycle.md](lifecycle.md) y [atribucion.md](atribucion.md) | Gestiones, promesas y convenios; la evaluación de promesas y la atribución operativa |
| [decision_v2.md](decision_v2.md) | `decision/v2` point-in-time, su política y el diccionario de sus 43 features |
| [geografia.md](geografia.md), [territorial_v2.md](territorial_v2.md) y [bases_operativas.md](bases_operativas.md) | La geografía real del INEGI, `territorial/v2` y las bases operativas |
| [campo_v2.md](campo_v2.md), [configuracion_campo.md](configuracion_campo.md) y [particion_campo.md](particion_campo.md) | `campo/v2`: la capacidad declarada, la configuración de campo y la partición operativa |
| [ruteo_v2.md](ruteo_v2.md), [red_vial.md](red_vial.md) y [politica_ruteo.md](politica_ruteo.md) | `ruteo/v2`: la red vial versionada, OSRM y la política de la ruta |

## Analytics, predicción y optimización

| Documento | Qué tiene |
|---|---|
| [analytics.md](analytics.md), [metricas_analytics.md](metricas_analytics.md) y [cohortes_analytics.md](cohortes_analytics.md) | `analytics/v1`: métricas, cohortes y embudos point-in-time, y su catálogo |
| [predictivo.md](predictivo.md), [features_predictivas.md](features_predictivas.md), [modelos_predictivos.md](modelos_predictivos.md) y [backtesting_predictivo.md](backtesting_predictivo.md) | `predictive/model-v1`: targets, features, modelos, calibración, drift y backtesting |
| [optimizacion.md](optimizacion.md), [modelo_optimizacion.md](modelo_optimizacion.md) y [configuracion_optimizacion.md](configuracion_optimizacion.md) | `optimization/v1`: el MILP lexicográfico exacto, su formulación y su configuración |

## La app

| Documento | Qué tiene |
|---|---|
| [app.md](app.md) | La app de solo lectura y `app/v1` |
| [contexto_operativo.md](contexto_operativo.md) | El contexto explícito: un corte y la ejecución declarada de cada motor |
| [dashboards.md](dashboards.md) | Cada pantalla y lo que no implica |
| [frontend.md](frontend.md) | React, TypeScript, el proxy, las pruebas y los presupuestos del bundle |
| [privacidad_app.md](privacidad_app.md) | Ningún dato personal ni coordenada, en tres capas |

## Seguridad

| Documento | Qué tiene |
|---|---|
| [security.md](security.md) | Los controles: identidad, permisos, credenciales, endurecimiento y configuración que falla cerrada |
| [threat-model.md](threat-model.md) | El modelo de amenazas, escrito antes de implementar |
| [matriz-autorizacion.md](matriz-autorizacion.md) | El permiso de cada operación de la API |
| [security-review-v018.md](security-review-v018.md) | La revisión de cierre de v0.18: lo que se encontró y se corrigió |
| [cadena-de-suministro.md](cadena-de-suministro.md) | Imágenes por digest, acciones por SHA, lockfiles, escaneos y SBOM |
| [dependencias-v1.md](dependencias-v1.md) | El triage de las actualizaciones de dependencias pendientes |

## Operación

| Documento | Qué tiene |
|---|---|
| [production.md](production.md) | El compose de producción: servicios, configuración y cómo se verifica |
| [observability.md](observability.md) y [slos.md](slos.md) | Logs, métricas, trazas, auditoría, alertas y objetivos de servicio |
| [runbooks/](runbooks/README.md) | Qué hacer ante cada alerta |
| [backups.md](backups.md) | El respaldo de la base y del almacén, y su restauración |
| [carga.md](carga.md) | La prueba de carga con k6 y los simulacros de falla |

## Nube

| Documento | Qué tiene |
|---|---|
| [cloud-gcp.md](cloud-gcp.md) | La referencia de Google Cloud en OpenTofu: **validada y nunca aplicada** |

## Decisiones por versión

Las decisiones de [decisiones.md](decisiones.md), por la versión que las tomó:

| Decisiones | Versión |
|---|---|
| 1 a 15 | v0.1.0, ingesta y certificación |
| 16 a 22 | v0.2.0, Decision Engine |
| 23 a 30 | v0.3.0, Motor Territorial |
| 31 a 41 | v0.4.0, Motor de Ruteo |
| 42 a 53 | v0.5.0, Orquestación Durable |
| 54 a 70 | v0.6.0, Fuentes Oficiales, Evidencia Inmutable y Escala |
| 71 a 84 | v0.7.0, Modelo Histórico y Cuenta 360 |
| 85 a 98 | v0.8.0, Motor de Pagos Canónico y Conciliación |
| 99 a 112 | v0.9.0, Lifecycle de Cobranza y Atribución Operativa |
| 113 a 121 | v0.10.0, Decision Engine v2 |
| 122 a 137 | v0.11.0, Geografía Real, Territorial v2 y Bases Operativas |
| 138 a 153 | v0.12.0, Campo v2 |
| 154 a 174 | v0.13.0, Ruteo Vial v2 |
| 175 a 196 | v0.14.0, Collection Analytics |
| 197 a 222 | v0.15.0, Predictive Intelligence |
| 223 a 247 | v0.16.0, Optimización Matemática |
| 248 a 274 | v0.17.0, App + Dashboards |
| 275 a 303 | v0.18.0, Cloud, Observabilidad y Hardening |

## Benchmarks

Cada benchmark corre fuera del CI, con su script, y dice su máquina, sus supuestos y su variación:

| Qué se mide | Dónde |
|---|---|
| La ingesta de XL (500,000 cuentas) y de XXL (1,000,000) | [fuentes.md](fuentes.md#benchmark-de-escala) |
| La historia de 12 cortes XL | [historia.md](historia.md#benchmark) |
| El motor de pagos sobre esos 12 cortes | [motor_pagos.md](motor_pagos.md#benchmark) |
| El lifecycle y la atribución | [lifecycle.md](lifecycle.md#índices-y-volumen) y [atribucion.md](atribucion.md#índices-y-volumen) |
| `decision/v2` sobre un corte XL | [decision_v2.md](decision_v2.md#índices-y-volumen) |
| La geografía y `territorial/v2` sobre 500,000 cuentas | [geografia.md](geografia.md#índices-y-volumen) y [territorial_v2.md](territorial_v2.md#índices-y-volumen) |
| `campo/v2` y `ruteo/v2` sobre el plan XL, y la red real de Puebla | [campo_v2.md](campo_v2.md#índices-y-volumen) y [ruteo_v2.md](ruteo_v2.md#índices-y-volumen) |
| `analytics/v1` sobre la cohorte XL | [analytics.md](analytics.md#rendimiento) |
| `predictive/model-v1`: entrenamiento M y scoring XL | [predictivo.md](predictivo.md#lo-medido) y [backtesting_predictivo.md](backtesting_predictivo.md#el-benchmark) |
| `optimization/v1`: S, M y XL | [optimizacion.md](optimizacion.md#lo-medido) |
| La app sobre 500,000 cuentas | [app.md](app.md#lo-medido) |
| La carga con k6 sobre el compose de producción | [carga.md](carga.md) |
