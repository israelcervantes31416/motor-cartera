# Decisiones de diseño — fase 1, Decision Engine, Motor Territorial, Motor de Ruteo, Orquestación Durable, Fuentes Oficiales, Modelo Histórico, Motor de Pagos, Lifecycle de Cobranza, Decision Engine v2, Geografía Real, Campo v2, Ruteo Vial v2, Collection Analytics, Predictive Intelligence, Optimización Matemática, App + Dashboards y Cloud, Observabilidad y Hardening

Por qué la fase 1 (v0.1.0), el Decision Engine (v0.2.0), el Motor Territorial (v0.3.0), el Motor
de Ruteo (v0.4.0), la Orquestación Durable (v0.5.0), las Fuentes Oficiales, Evidencia Inmutable y
Escala (v0.6.0), el Modelo Histórico y Cuenta 360 (v0.7.0), el Motor de Pagos Canónico y
Conciliación (v0.8.0), el Lifecycle de Cobranza y Atribución Operativa (v0.9.0), el Decision
Engine v2 (v0.10.0), la Geografía Real, Territorial v2 y Bases Operativas (v0.11.0), Campo v2:
Capacidad, Partición y Asignación Operativa (v0.12.0), el Ruteo Vial v2: Red Vial, Secuenciación y
Factibilidad de Jornada (v0.13.0), Collection Analytics: Métricas, Cohortes y Embudos
Point-in-Time (v0.14.0), Predictive Intelligence: Modelos Temporales, Calibración y Drift
(v0.15.0), la Optimización Matemática: Priorización, Capacidad y Asignación Restringida
(v0.16.0), la App + Dashboards: Superficie Operacional y Cuenta 360 (v0.17.0) y Cloud,
Observabilidad y Hardening: Production Readiness (v0.18.0) están hechos como están, y qué haría
distinto o cuándo cambiaría cada decisión.
El uso está en el [README](../README.md), en [fuentes.md](fuentes.md), en
[historia.md](historia.md), en [cuenta_360.md](cuenta_360.md), en [motor_pagos.md](motor_pagos.md),
en [lifecycle.md](lifecycle.md), en [atribucion.md](atribucion.md), en
[decision_v2.md](decision_v2.md), en [geografia.md](geografia.md), en
[territorial_v2.md](territorial_v2.md), en [bases_operativas.md](bases_operativas.md), en
[campo_v2.md](campo_v2.md), en [configuracion_campo.md](configuracion_campo.md), en
[particion_campo.md](particion_campo.md), en [ruteo_v2.md](ruteo_v2.md), en
[red_vial.md](red_vial.md), en [politica_ruteo.md](politica_ruteo.md), en
[analytics.md](analytics.md), en [metricas_analytics.md](metricas_analytics.md), en
[cohortes_analytics.md](cohortes_analytics.md), en [predictivo.md](predictivo.md), en
[features_predictivas.md](features_predictivas.md), en
[modelos_predictivos.md](modelos_predictivos.md), en
[backtesting_predictivo.md](backtesting_predictivo.md), en [optimizacion.md](optimizacion.md), en
[modelo_optimizacion.md](modelo_optimizacion.md), en
[configuracion_optimizacion.md](configuracion_optimizacion.md), en [app.md](app.md), en
[contexto_operativo.md](contexto_operativo.md), en [dashboards.md](dashboards.md), en
[frontend.md](frontend.md), en [privacidad_app.md](privacidad_app.md), en
[production.md](production.md), en [security.md](security.md), en
[threat-model.md](threat-model.md), en [observability.md](observability.md), en
[slos.md](slos.md), en [backups.md](backups.md), en [carga.md](carga.md), en
[cadena-de-suministro.md](cadena-de-suministro.md) y en [cloud-gcp.md](cloud-gcp.md); aquí va el
porqué.

Las secciones 1 a 15 son de la fase 1; las 16 a 22, del Decision Engine; las 23 a 30, del Motor
Territorial; las 31 a 41, del Motor de Ruteo; las 42 a 53, de la Orquestación Durable; las 54 a 70,
de las Fuentes Oficiales; las 71 a 84, del Modelo Histórico; las 85 a 98, del Motor de Pagos; las
99 a 112, del Lifecycle de Cobranza y la Atribución Operativa; las 113 a 121, del Decision Engine
v2; las 122 a 137, de la Geografía Real, Territorial v2 y las Bases Operativas; las 138 a 153,
de Campo v2; las 154 a 174, del Ruteo Vial v2; las 175 a 196, de Collection Analytics; las
197 a 222, de Predictive Intelligence; las 223 a 247, de la Optimización Matemática; las 248 a
274, de la App + Dashboards, y las 275 a 303, de Cloud, Observabilidad y Hardening.
Donde las de la fase 1 hablan de la orquestación de la fase 3, hoy es la orquestación durable de
v0.5.0. Las secciones que una versión posterior cambió lo dicen al final, en un párrafo *Desde
v0.5.0*, *Desde v0.6.0*, *Desde v0.7.0*, *Desde v0.8.0*, *Desde v0.9.0*, *Desde v0.10.0* o
*Desde v0.13.0*, *Desde v0.15.0* o *Desde v0.16.0*.

1. [La corrida es una entidad, no un campo](#1-la-corrida-es-una-entidad-no-un-campo)
2. [*Fail-closed* en dos niveles: registro y archivo](#2-fail-closed-en-dos-niveles-registro-y-archivo)
3. [La validación vive en el contrato, no en el endpoint](#3-la-validación-vive-en-el-contrato-no-en-el-endpoint)
4. [Validar columna por columna](#4-validar-columna-por-columna)
5. [Los códigos HTTP](#5-los-códigos-http)
6. [Autenticación](#6-autenticación)
7. [La estructura de los errores](#7-la-estructura-de-los-errores)
8. [Paginación](#8-paginación)
9. [Una cartera se publica una sola vez](#9-una-cartera-se-publica-una-sola-vez)
10. [La cartera vigente es la del corte más reciente](#10-la-cartera-vigente-es-la-del-corte-más-reciente)
11. [Procesar en segundo plano, dentro de la API](#11-procesar-en-segundo-plano-dentro-de-la-api)
12. [Detalles de modelado](#12-detalles-de-modelado)
13. [Migraciones y pruebas](#13-migraciones-y-pruebas)
14. [Versiones fijadas, con 3.12 como piso](#14-versiones-fijadas-con-312-como-piso)
15. [El control de archivos complementa al .gitignore, no lo repite](#15-el-control-de-archivos-complementa-al-gitignore-no-lo-repite)
16. [El núcleo de decisión es puro y versionado](#16-el-núcleo-de-decisión-es-puro-y-versionado)
17. [Persistir la ejecución, no solo el resultado](#17-persistir-la-ejecución-no-solo-el-resultado)
18. [Publicar todas las decisiones o ninguna](#18-publicar-todas-las-decisiones-o-ninguna)
19. [Idempotencia y carreras](#19-idempotencia-y-carreras)
20. [POST síncrono y semántica 201](#20-post-síncrono-y-semántica-201)
21. [Historial y vocabulario versionado](#21-historial-y-vocabulario-versionado)
22. [Qué queda para la orquestación, y qué no resuelve v0.2.0](#22-qué-queda-para-la-orquestación-y-qué-no-resuelve-v020)
23. [El territorio es un municipio](#23-el-territorio-es-un-municipio)
24. [Carga operativa, no riesgo crediticio](#24-carga-operativa-no-riesgo-crediticio)
25. [Agregar en PostgreSQL antes de entrar al núcleo](#25-agregar-en-postgresql-antes-de-entrar-al-núcleo)
26. [territorial/v1 consume decision/v1](#26-territorialv1-consume-decisionv1)
27. [Persistencia territorial e idempotencia](#27-persistencia-territorial-e-idempotencia)
28. [Publicación todo-o-nada](#28-publicación-todo-o-nada)
29. [API e historial territorial](#29-api-e-historial-territorial)
30. [Por qué v0.3 no es ruteo](#30-por-qué-v03-no-es-ruteo)
31. [Por qué ruteo usa un plano sintético](#31-por-qué-ruteo-usa-un-plano-sintético)
32. [Coordenadas deterministas con SHA-256](#32-coordenadas-deterministas-con-sha-256)
33. [Manhattan como métrica exacta](#33-manhattan-como-métrica-exacta)
34. [Vecino más cercano como construcción inicial](#34-vecino-más-cercano-como-construcción-inicial)
35. [2-opt determinista y acotado](#35-2-opt-determinista-y-acotado)
36. [Una ruta por municipio](#36-una-ruta-por-municipio)
37. [Persistencia de rutas y paradas](#37-persistencia-de-rutas-y-paradas)
38. [Completitud y publicación todo-o-nada](#38-completitud-y-publicación-todo-o-nada)
39. [Concurrencia e idempotencia del ruteo](#39-concurrencia-e-idempotencia-del-ruteo)
40. [API e historial de ruteo](#40-api-e-historial-de-ruteo)
41. [Qué no resuelve v0.4.0](#41-qué-no-resuelve-v040)
42. [Por qué PostgreSQL es la cola de v0.5](#42-por-qué-postgresql-es-la-cola-de-v05)
43. [Persistir el archivo antes de responder](#43-persistir-el-archivo-antes-de-responder)
44. [At-least-once y no exactly-once](#44-at-least-once-y-no-exactly-once)
45. [Claim con FOR UPDATE SKIP LOCKED](#45-claim-con-for-update-skip-locked)
46. [Lease y heartbeat](#46-lease-y-heartbeat)
47. [Qué pasa si un worker muere](#47-qué-pasa-si-un-worker-muere)
48. [Trabajo COMPLETADO no significa motor EXITOSA](#48-trabajo-completado-no-significa-motor-exitosa)
49. [Pipeline automático](#49-pipeline-automático)
50. [Reanudar una etapa fallida](#50-reanudar-una-etapa-fallida)
51. [API asíncrona y semántica 201](#51-api-asíncrona-y-semántica-201)
52. [Por qué todavía no Redis/Celery](#52-por-qué-todavía-no-rediscelery)
53. [Qué queda para Cloud + observabilidad](#53-qué-queda-para-cloud--observabilidad)
54. [El archivo original es evidencia](#54-el-archivo-original-es-evidencia)
55. [Un almacén por contenido, local, detrás de una interfaz](#55-un-almacén-por-contenido-local-detrás-de-una-interfaz)
56. [Primero el objeto, después la base](#56-primero-el-objeto-después-la-base)
57. [Los artefactos no se borran, ni al bajar la migración](#57-los-artefactos-no-se-borran-ni-al-bajar-la-migración)
58. [cartera/v1 congelado; cartera/v2 es otro contrato, declarado](#58-carterav1-congelado-carterav2-es-otro-contrato-declarado)
59. [Estructura exacta, sin descartes silenciosos](#59-estructura-exacta-sin-descartes-silenciosos)
60. [La fecha de corte es metadata del lote](#60-la-fecha-de-corte-es-metadata-del-lote)
61. [Dos firmas: la del archivo y la del contenido](#61-dos-firmas-la-del-archivo-y-la-del-contenido)
62. [Original y conformado: dos representaciones](#62-original-y-conformado-dos-representaciones)
63. [Juicio por lotes en dos pasadas, todo o nada](#63-juicio-por-lotes-en-dos-pasadas-todo-o-nada)
64. [Proyección operacional explícita, con el catálogo público del INEGI](#64-proyección-operacional-explícita-con-el-catálogo-público-del-inegi)
65. [CARRIER es una hoja compañera observada](#65-carrier-es-una-hoja-compañera-observada)
66. [pagos/v1: su propia ingesta, sin deduplicar, con barrera conservadora](#66-pagosv1-su-propia-ingesta-sin-deduplicar-con-barrera-conservadora)
67. [Un despacho, una cartera](#67-un-despacho-una-cartera)
68. [Detección de formato por contenido](#68-detección-de-formato-por-contenido)
69. [Escala: perfiles, escenario longitudinal y benchmark fuera del CI](#69-escala-perfiles-escenario-longitudinal-y-benchmark-fuera-del-ci)
70. [Qué no resuelve v0.6.0, y qué queda para v0.7](#70-qué-no-resuelve-v060-y-qué-queda-para-v07)
71. [Tres capas: el histórico no reemplaza al conformado ni a la operacional](#71-tres-capas-el-histórico-no-reemplaza-al-conformado-ni-a-la-operacional)
72. [La identidad: despacho + cartera + CLIENTE_UNICO, sin persona ni crédito](#72-la-identidad-despacho--cartera--cliente_unico-sin-persona-ni-crédito)
73. [Identificadores públicos deterministas](#73-identificadores-públicos-deterministas)
74. [Un corte canónico por fecha: fuentes equivalentes y cortes conflictivos](#74-un-corte-canónico-por-fecha-fuentes-equivalentes-y-cortes-conflictivos)
75. [Un snapshot estrecho, inmutable y con su linaje](#75-un-snapshot-estrecho-inmutable-y-con-su-linaje)
76. [La fecha en el snapshot, garantizada por una llave compuesta](#76-la-fecha-en-el-snapshot-garantizada-por-una-llave-compuesta)
77. [Pagos observados: una fila, una observación, sin llave hacia la cuenta](#77-pagos-observados-una-fila-una-observación-sin-llave-hacia-la-cuenta)
78. [Eventos y continuidad al consultar; el vocabulario de lo observado](#78-eventos-y-continuidad-al-consultar-el-vocabulario-de-lo-observado)
79. [historia/v1: una ejecución versionada, abierta con su dataset, paralela al flujo](#79-historiav1-una-ejecución-versionada-abierta-con-su-dataset-paralela-al-flujo)
80. [La cola toma lo operacional antes que la historia](#80-la-cola-toma-lo-operacional-antes-que-la-historia)
81. [Carga masiva todo o nada: Parquet, CSV de Arrow, COPY e INSERT ... SELECT](#81-carga-masiva-todo-o-nada-parquet-csv-de-arrow-copy-e-insert--select)
82. [Concurrencia sin bloqueos mutuos](#82-concurrencia-sin-bloqueos-mutuos)
83. [Backfill fuera de Alembic, idempotente](#83-backfill-fuera-de-alembic-idempotente)
84. [Índices medidos, sin particionado; y qué no resuelve v0.7.0](#84-índices-medidos-sin-particionado-y-qué-no-resuelve-v070)
85. [Observación e interpretación, separadas: el motor no toca PagoObservado](#85-observación-e-interpretación-separadas-el-motor-no-toca-pagoobservado)
86. [La ventana: un mes de recepción por cartera, con 30 días de contexto](#86-la-ventana-un-mes-de-recepción-por-cartera-con-30-días-de-contexto)
87. [Dos huellas, y la comparación campo por campo antes de fusionar](#87-dos-huellas-y-la-comparación-campo-por-campo-antes-de-fusionar)
88. [Duplicado exacto, coincidencia ambigua y un representante por contenido](#88-duplicado-exacto-coincidencia-ambigua-y-un-representante-por-contenido)
89. [Reversos: solo la pareja aislada](#89-reversos-solo-la-pareja-aislada)
90. [El signo económico y una recuperación que no es un ledger](#90-el-signo-económico-y-una-recuperación-que-no-es-un-ledger)
91. [La conciliación es otro eje, y es versionada](#91-la-conciliación-es-otro-eje-y-es-versionada)
92. [El contexto temporal, al consultar](#92-el-contexto-temporal-al-consultar)
93. [movimiento_id: UUID v8 determinista, único por ejecución](#93-movimiento_id-uuid-v8-determinista-único-por-ejecución)
94. [Firma de entrada, ejecución vigente e historia de interpretaciones](#94-firma-de-entrada-ejecución-vigente-e-historia-de-interpretaciones)
95. [Por conjuntos, todo o nada, con un núcleo puro que lo verifica](#95-por-conjuntos-todo-o-nada-con-un-núcleo-puro-que-lo-verifica)
96. [Se abre con la historia de sus pagos, va al final de la cola y no bloquea la operación](#96-se-abre-con-la-historia-de-sus-pagos-va-al-final-de-la-cola-y-no-bloquea-la-operación)
97. [Solo publica el dueño vigente de su trabajo](#97-solo-publica-el-dueño-vigente-de-su-trabajo)
98. [Índices medidos, sin particionado; backfill por ventanas; y qué no resuelve v0.8.0](#98-índices-medidos-sin-particionado-backfill-por-ventanas-y-qué-no-resuelve-v080)
99. [El lifecycle es una tercera verdad, no una tercera fuente oficial](#99-el-lifecycle-es-una-tercera-verdad-no-una-tercera-fuente-oficial)
100. [Los snapshots no se convierten en eventos](#100-los-snapshots-no-se-convierten-en-eventos)
101. [Dos tiempos: cuándo pasó y cuándo se registró](#101-dos-tiempos-cuándo-pasó-y-cuándo-se-registró)
102. [Solo se agrega: las correcciones son eventos](#102-solo-se-agrega-las-correcciones-son-eventos)
103. [Idempotency-Key obligatoria, garantizada por PostgreSQL](#103-idempotency-key-obligatoria-garantizada-por-postgresql)
104. [Una visita es una gestión de campo con su detalle](#104-una-visita-es-una-gestión-de-campo-con-su-detalle)
105. [Cumplir una promesa lo dice una evaluación versionada, a una fecha explícita](#105-cumplir-una-promesa-lo-dice-una-evaluación-versionada-a-una-fecha-explícita)
106. [Atribución conservadora: candidatas, ambigüedad sin elegir y una ventana que es parámetro](#106-atribución-conservadora-candidatas-ambigüedad-sin-elegir-y-una-ventana-que-es-parámetro)
107. [Asociación no es causalidad, y compatible no es asociado](#107-asociación-no-es-causalidad-y-compatible-no-es-asociado)
108. [La atribución por ventanas, sobre la interpretación vigente, por conjuntos y sin abrirse sola](#108-la-atribución-por-ventanas-sobre-la-interpretación-vigente-por-conjuntos-y-sin-abrirse-sola)
109. [Sin GestorCanonico: `actor_ref` es una referencia opaca](#109-sin-gestorcanonico-actor_ref-es-una-referencia-opaca)
110. [Un convenio sin ledger](#110-un-convenio-sin-ledger)
111. [La importación masiva: COPY, tablas temporales y fases, todo o nada](#111-la-importación-masiva-copy-tablas-temporales-y-fases-todo-o-nada)
112. [Índices medidos, sin particionado; y qué no resuelve v0.9.0](#112-índices-medidos-sin-particionado-y-qué-no-resuelve-v090)
113. [Dos relojes: `as_of` decide el día, `known_at` decide qué se sabía](#113-dos-relojes-as_of-decide-el-día-known_at-decide-qué-se-sabía)
114. [La frontera se asienta antes de leer](#114-la-frontera-se-asienta-antes-de-leer)
115. [Un filtro point-in-time por dominio, y las reglas de las promesas escritas una vez](#115-un-filtro-point-in-time-por-dominio-y-las-reglas-de-las-promesas-escritas-una-vez)
116. [NULL no es cero: disponibilidad por mes y por ventana](#116-null-no-es-cero-disponibilidad-por-mes-y-por-ventana)
117. [Tres versiones separadas, la política como datos con su hash, y sin ML ni predicción](#117-tres-versiones-separadas-la-política-como-datos-con-su-hash-y-sin-ml-ni-predicción)
118. [Lo decidido se guarda tal cual, con sus hashes, y no se reescribe](#118-lo-decidido-se-guarda-tal-cual-con-sus-hashes-y-no-se-reescribe)
119. [Por conjuntos, todo o nada, en un trabajo durable que alguien pide](#119-por-conjuntos-todo-o-nada-en-un-trabajo-durable-que-alguien-pide)
120. [En paralelo a `decision/v1`, en modo sombra](#120-en-paralelo-a-decisionv1-en-modo-sombra)
121. [Índices medidos, sin particionado; y qué no resuelve v0.10.0](#121-índices-medidos-sin-particionado-y-qué-no-resuelve-v0100)
122. [La cartografía pública es una referencia, no una fuente de negocio](#122-la-cartografía-pública-es-una-referencia-no-una-fuente-de-negocio)
123. [PostGIS en la misma PostgreSQL, y la capa leída con pyshp](#123-postgis-en-la-misma-postgresql-y-la-capa-leída-con-pyshp)
124. [EPSG:4326 para guardar, y el sistema de origen reconocido por sus parámetros](#124-epsg4326-para-guardar-y-el-sistema-de-origen-reconocido-por-sus-parámetros)
125. [El punto es longitud, latitud](#125-el-punto-es-longitud-latitud)
126. [La fuente y el espacio son dos hechos que se comparan, no se mezclan](#126-la-fuente-y-el-espacio-son-dos-hechos-que-se-comparan-no-se-mezclan)
127. [Ninguna coordenada se corrige, y ninguna sale](#127-ninguna-coordenada-se-corrige-y-ninguna-sale)
128. [Fail-closed: solo una geografía `VALIDA` es confiable](#128-fail-closed-solo-una-geografía-valida-es-confiable)
129. [El borde cuenta: `ST_Covers`](#129-el-borde-cuenta-st_covers)
130. [Una referencia se importa todo o nada, y solo se repara lo seguro](#130-una-referencia-se-importa-todo-o-nada-y-solo-se-repara-lo-seguro)
131. [Las bases operativas son configuración versionada, inmutable y con hash](#131-las-bases-operativas-son-configuración-versionada-inmutable-y-con-hash)
132. [La cobertura se declara, nunca se infiere](#132-la-cobertura-se-declara-nunca-se-infiere)
133. [La base elegible inequívocamente más cercana, con una tolerancia de empate versionada](#133-la-base-elegible-inequívocamente-más-cercana-con-una-tolerancia-de-empate-versionada)
134. [Pertenencia territorial, no capacidad, y en paralelo a `territorial/v1`](#134-pertenencia-territorial-no-capacidad-y-en-paralelo-a-territorialv1)
135. [Sin geocodificación de direcciones](#135-sin-geocodificación-de-direcciones)
136. [Una asignación apunta a su resultado geográfico por su llave natural](#136-una-asignación-apunta-a-su-resultado-geográfico-por-su-llave-natural)
137. [Por conjuntos, índices medidos, sin particionado; y qué no resuelve v0.11.0](#137-por-conjuntos-índices-medidos-sin-particionado-y-qué-no-resuelve-v0110)
138. [Campo v2 consume `decision/v2`: el universo es el canal `CAMPO`, con su prioridad](#138-campo-v2-consume-decisionv2-el-universo-es-el-canal-campo-con-su-prioridad)
139. [Campo v2 consume `territorial/v2`: la base es la suya, y la duda va a revisión](#139-campo-v2-consume-territorialv2-la-base-es-la-suya-y-la-duda-va-a-revisión)
140. [El canal no es la capacidad](#140-el-canal-no-es-la-capacidad)
141. [Capacidad declarada, no factibilidad vial](#141-capacidad-declarada-no-factibilidad-vial)
142. [La configuración de campo es versionada, inmutable y con hash](#142-la-configuración-de-campo-es-versionada-inmutable-y-con-hash)
143. [El gestor operacional no es el `Gestor` de la fuente](#143-el-gestor-operacional-no-es-el-gestor-de-la-fuente)
144. [Ventanas de jornada en hora local, con zona explícita, y excepciones que reemplazan la semana](#144-ventanas-de-jornada-en-hora-local-con-zona-explícita-y-excepciones-que-reemplazan-la-semana)
145. [La partición es una cuadrícula fija, no un agrupamiento](#145-la-partición-es-una-cuadrícula-fija-no-un-agrupamiento)
146. [EPSG:6372 para la cuadrícula métrica](#146-epsg6372-para-la-cuadrícula-métrica)
147. [Tramos de 5 km y microtramos de 1 km](#147-tramos-de-5-km-y-microtramos-de-1-km)
148. [El propietario preferido de un microtramo, por Rendezvous](#148-el-propietario-preferido-de-un-microtramo-por-rendezvous)
149. [La prioridad antes que el spillover, y un microtramo entero cuando cabe](#149-la-prioridad-antes-que-el-spillover-y-un-microtramo-entero-cuando-cabe)
150. [Sin rebalanceo entre bases](#150-sin-rebalanceo-entre-bases)
151. [Sin rutas, ni orden de visita](#151-sin-rutas-ni-orden-de-visita)
152. [Privacidad: ni coordenadas, ni celdas, ni datos de personas](#152-privacidad-ni-coordenadas-ni-celdas-ni-datos-de-personas)
153. [Por conjuntos, índices medidos, sin particionado; y qué no resuelve v0.12.0](#153-por-conjuntos-índices-medidos-sin-particionado-y-qué-no-resuelve-v0120)
154. [La red vial pública es una referencia vial, no una fuente de negocio](#154-la-red-vial-pública-es-una-referencia-vial-no-una-fuente-de-negocio)
155. [OSRM autoalojado: ninguna coordenada sale de la red propia](#155-osrm-autoalojado-ninguna-coordenada-sale-de-la-red-propia)
156. [OSRM v26.9.0, fijado por su digest, con MLD y el perfil `car` tal cual](#156-osrm-v2690-fijado-por-su-digest-con-mld-y-el-perfil-car-tal-cual)
157. [La versión de la red es su huella; los archivos preparados son evidencia](#157-la-versión-de-la-red-es-su-huella-los-archivos-preparados-son-evidencia)
158. [El backend declara su red, y se verifica antes de enviar un punto](#158-el-backend-declara-su-red-y-se-verifica-antes-de-enviar-un-punto)
159. [Un snap que no depende de la petición: `--small-component-size 1`](#159-un-snap-que-no-depende-de-la-petición---small-component-size-1)
160. [El snap hasta 500.00 m, al centímetro, sin calibrar](#160-el-snap-hasta-50000-m-al-centímetro-sin-calibrar)
161. [Sin línea recta: lo que la red no dice no se inventa](#161-sin-línea-recta-lo-que-la-red-no-dice-no-se-inventa)
162. [Un fallo técnico no es una parada no enrutable](#162-un-fallo-técnico-no-es-una-parada-no-enrutable)
163. [Una matriz por gestor, en bloques, con puntos únicos y sin guardar sus celdas](#163-una-matriz-por-gestor-en-bloques-con-puntos-únicos-y-sin-guardar-sus-celdas)
164. [Un servicio fijo de 900 s, como hipótesis pública](#164-un-servicio-fijo-de-900-s-como-hipótesis-pública)
165. [La ruta es cerrada en la base, y el regreso se reserva](#165-la-ruta-es-cerrada-en-la-base-y-el-regreso-se-reserva)
166. [Ventanas `[inicio, fin)`, actividades enteras y la espera en el lugar](#166-ventanas-inicio-fin-actividades-enteras-y-la-espera-en-el-lugar)
167. [La prioridad manda, y lo que no cabe ni solo no bloquea](#167-la-prioridad-manda-y-lo-que-no-cabe-ni-solo-no-bloquea)
168. [Una heurística determinista, no un óptimo; el 2-opt, acotado y después](#168-una-heurística-determinista-no-un-óptimo-el-2-opt-acotado-y-después)
169. [ETA planificadas, sin tráfico](#169-eta-planificadas-sin-tráfico)
170. [Sin geometrías ni polilíneas, y un transporte que no filtra coordenadas](#170-sin-geometrías-ni-polilíneas-y-un-transporte-que-no-filtra-coordenadas)
171. [Ni rebalanceo entre gestores ni optimización global](#171-ni-rebalanceo-entre-gestores-ni-optimización-global)
172. [Sin una tabla de tramos](#172-sin-una-tabla-de-tramos)
173. [Una ruta por gestor con al menos una cuenta `ASIGNADA`](#173-una-ruta-por-gestor-con-al-menos-una-cuenta-asignada)
174. [Por conjuntos, índices medidos, sin particionado; y qué no resuelve v0.13.0](#174-por-conjuntos-índices-medidos-sin-particionado-y-qué-no-resuelve-v0130)
175. [Analytics agregado, no un feature store](#175-analytics-agregado-no-un-feature-store)
176. [La cohorte anclada a un corte canónico](#176-la-cohorte-anclada-a-un-corte-canónico)
177. [`as_of` y `known_at`, y los dos modos](#177-as_of-y-known_at-y-los-dos-modos)
178. [Un manifiesto explícito, comprobado antes de registrar nada](#178-un-manifiesto-explícito-comprobado-antes-de-registrar-nada)
179. [Sin «la última» implícita: proponer no es elegir](#179-sin-la-última-implícita-proponer-no-es-elegir)
180. [La recuperación sale de los movimientos canónicos, con la semántica de `motor-pagos/v1`](#180-la-recuperación-sale-de-los-movimientos-canónicos-con-la-semántica-de-motor-pagosv1)
181. [Los reversos, point-in-time](#181-los-reversos-point-in-time)
182. [La cobertura de pagos, por día, desde los archivos conocidos](#182-la-cobertura-de-pagos-por-día-desde-los-archivos-conocidos)
183. [El lifecycle, point-in-time: lo que llegó tarde y las anulaciones](#183-el-lifecycle-point-in-time-lo-que-llegó-tarde-y-las-anulaciones)
184. [La atribución no es causalidad](#184-la-atribución-no-es-causalidad)
185. [El estado de una promesa es el de la evaluación declarada](#185-el-estado-de-una-promesa-es-el-de-la-evaluación-declarada)
186. [NULL no es cero](#186-null-no-es-cero)
187. [Las razones guardan su numerador y su denominador; decimal, no float](#187-las-razones-guardan-su-numerador-y-su-denominador-decimal-no-float)
188. [Dimensiones limitadas: conjuntos fijos del catálogo](#188-dimensiones-limitadas-conjuntos-fijos-del-catálogo)
189. [Supresión de celdas pequeñas: 10 cuentas, primaria y complementaria](#189-supresión-de-celdas-pequeñas-10-cuentas-primaria-y-complementaria)
190. [Un gestor de campo no es un `actor_ref`](#190-un-gestor-de-campo-no-es-un-actor_ref)
191. [Planificado no es ejecutado](#191-planificado-no-es-ejecutado)
192. [Sin dashboards](#192-sin-dashboards)
193. [Sin modelos](#193-sin-modelos)
194. [Sin optimización](#194-sin-optimización)
195. [Los agregados se materializan, con su hash](#195-los-agregados-se-materializan-con-su-hash)
196. [Por conjuntos, índices medidos, sin particionado; y qué no resuelve v0.14.0](#196-por-conjuntos-índices-medidos-sin-particionado-y-qué-no-resuelve-v0140)
197. [Predecir no es causar](#197-predecir-no-es-causar)
198. [Las features, point-in-time](#198-las-features-point-in-time)
199. [Tres relojes](#199-tres-relojes)
200. [La ventana (T, T+H], con las reglas de los motores existentes](#200-la-ventana-t-th-con-las-reglas-de-los-motores-existentes)
201. [Labels maduros, censurados o no disponibles; censurado no es cero](#201-labels-maduros-censurados-o-no-disponibles-censurado-no-es-cero)
202. [Las marcas de cobertura se declaran](#202-las-marcas-de-cobertura-se-declaran)
203. [Un manifiesto explícito, comprobado antes de registrar nada](#203-un-manifiesto-explícito-comprobado-antes-de-registrar-nada)
204. [Sin split aleatorio](#204-sin-split-aleatorio)
205. [La purga entre bloques](#205-la-purga-entre-bloques)
206. [Walk-forward hacia atrás y TEST intocable](#206-walk-forward-hacia-atrás-y-test-intocable)
207. [GLM como public-v1](#207-glm-como-public-v1)
208. [Newton por bloques propio; scikit-learn solo como prueba](#208-newton-por-bloques-propio-scikit-learn-solo-como-prueba)
209. [Sin rebalanceo de clases](#209-sin-rebalanceo-de-clases)
210. [La calibración, decidida en VALIDATION](#210-la-calibración-decidida-en-validation)
211. [Baseline de TRAIN y compuertas fijadas antes de TEST](#211-baseline-de-train-y-compuertas-fijadas-antes-de-test)
212. [El modelo es un JSON canónico](#212-el-modelo-es-un-json-canónico)
213. [Nunca pickle; un runtime independiente](#213-nunca-pickle-un-runtime-independiente)
214. [Ningún identificador es una feature](#214-ningún-identificador-es-una-feature)
215. [Ningún dato personal ni sensible, y sin geografía fina](#215-ningún-dato-personal-ni-sensible-y-sin-geografía-fina)
216. [Las salidas de Decision, Campo y Ruteo no son features](#216-las-salidas-de-decision-campo-y-ruteo-no-son-features)
217. [Recuperación positiva esperada; el riesgo es 1 − p](#217-recuperación-positiva-esperada-el-riesgo-es-1--p)
218. [La explicación se verifica con el `feature_hash`](#218-la-explicación-se-verifica-con-el-feature_hash)
219. [El drift diagnostica; no reentrena](#219-el-drift-diagnostica-no-reentrena)
220. [Las predicciones corren en sombra](#220-las-predicciones-corren-en-sombra)
221. [Sin transacciones largas; el benchmark M y el XL](#221-sin-transacciones-largas-el-benchmark-m-y-el-xl)
222. [Índices medidos, sin particionado; y qué no resuelve v0.15.0](#222-índices-medidos-sin-particionado-y-qué-no-resuelve-v0150)
223. [Optimizar no es inferir causalidad](#223-optimizar-no-es-inferir-causalidad)
224. [En sombra: un plan de soporte a decisión](#224-en-sombra-un-plan-de-soporte-a-decisión)
225. [El valor de oportunidad es la recuperación positiva esperada](#225-el-valor-de-oportunidad-es-la-recuperación-positiva-esperada)
226. [Sin multiplicar por la probabilidad de recuperación](#226-sin-multiplicar-por-la-probabilidad-de-recuperación)
227. [p_contacto solo desempata](#227-p_contacto-solo-desempata)
228. [decision/v2 es la frontera de admisibilidad](#228-decisionv2-es-la-frontera-de-admisibilidad)
229. [Entradas declaradas; la base garantiza la cadena](#229-entradas-declaradas-la-base-garantiza-la-cadena)
230. [CAMPO solo desde paradas PROGRAMADA de su propio gestor](#230-campo-solo-desde-paradas-programada-de-su-propio-gestor)
231. [Sin reoptimizar rutas](#231-sin-reoptimizar-rutas)
232. [Unidades de recurso abstractas](#232-unidades-de-recurso-abstractas)
233. [Sin modelo de costos monetarios](#233-sin-modelo-de-costos-monetarios)
234. [Un MILP con HiGHS, el de SciPy](#234-un-milp-con-highs-el-de-scipy)
235. [Reducción exacta de candidatos por dominancia](#235-reducción-exacta-de-candidatos-por-dominancia)
236. [Matrices dispersas y enteros exactos](#236-matrices-dispersas-y-enteros-exactos)
237. [Optimización lexicográfica por etapas](#237-optimización-lexicográfica-por-etapas)
238. [Relajación primero y fijación por dualidad](#238-relajación-primero-y-fijación-por-dualidad)
239. [Solo se publica un óptimo demostrado](#239-solo-se-publica-un-óptimo-demostrado)
240. [La huella del solver](#240-la-huella-del-solver)
241. [Sin atributos protegidos ni identificadores como dimensión](#241-sin-atributos-protegidos-ni-identificadores-como-dimensión)
242. [Sin ejecución automática](#242-sin-ejecución-automática)
243. [La configuración: versionada, cerrada y con hash](#243-la-configuración-versionada-cerrada-y-con-hash)
244. [Todo o nada, por conjuntos](#244-todo-o-nada-por-conjuntos)
245. [La cola: OPTIMIZATION en el nivel 10, con candado y lease](#245-la-cola-optimization-en-el-nivel-10-con-candado-y-lease)
246. [El benchmark: S, M, XL y la sensibilidad, con supuestos fijados antes de medir](#246-el-benchmark-s-m-xl-y-la-sensibilidad-con-supuestos-fijados-antes-de-medir)
247. [Índices medidos, sin particionado; y qué no resuelve v0.16.0](#247-índices-medidos-sin-particionado-y-qué-no-resuelve-v0160)
248. [Una SPA de React con Vite, sin SSR](#248-una-spa-de-react-con-vite-sin-ssr)
249. [TypeScript estricto, sin `any`](#249-typescript-estricto-sin-any)
250. [El backend es la fuente de verdad](#250-el-backend-es-la-fuente-de-verdad)
251. [v0.17 es de solo lectura](#251-v017-es-de-solo-lectura)
252. [Sin autenticación falsa](#252-sin-autenticación-falsa)
253. [`app/v1` solo donde compone](#253-appv1-solo-donde-compone)
254. [El contexto operativo, explícito](#254-el-contexto-operativo-explícito)
255. [Nunca "lo más reciente" en silencio](#255-nunca-lo-más-reciente-en-silencio)
256. [La URL reproduce la vista](#256-la-url-reproduce-la-vista)
257. [Paginación, orden y filtros en el servidor](#257-paginación-orden-y-filtros-en-el-servidor)
258. [Sin coordenadas ni mapas](#258-sin-coordenadas-ni-mapas)
259. [La supresión de analytics se respeta](#259-la-supresión-de-analytics-se-respeta)
260. [Una predicción no es un efecto causal](#260-una-predicción-no-es-un-efecto-causal)
261. [Un plan óptimo no es una estrategia causalmente óptima](#261-un-plan-óptimo-no-es-una-estrategia-causalmente-óptima)
262. [Los tipos salen del OpenAPI de la API](#262-los-tipos-salen-del-openapi-de-la-api)
263. [TanStack Query para el estado del servidor](#263-tanstack-query-para-el-estado-del-servidor)
264. [Sin Redux](#264-sin-redux)
265. [Recursos locales, sin telemetría](#265-recursos-locales-sin-telemetría)
266. [La accesibilidad, medida y no certificada](#266-la-accesibilidad-medida-y-no-certificada)
267. [Los presupuestos del bundle, fijados antes de medir](#267-los-presupuestos-del-bundle-fijados-antes-de-medir)
268. [Un proxy de solo lectura, con lista blanca y la clave del lado del servidor](#268-un-proxy-de-solo-lectura-con-lista-blanca-y-la-clave-del-lado-del-servidor)
269. [Sin migración vacía](#269-sin-migración-vacía)
270. [Sin índices nuevos ni particionado sin evidencia](#270-sin-índices-nuevos-ni-particionado-sin-evidencia)
271. [Sin datos personales en ningún nivel](#271-sin-datos-personales-en-ningún-nivel)
272. [Las llaves primero: el explorador a escala](#272-las-llaves-primero-el-explorador-a-escala)
273. [Las pantallas se prueban con respuestas de la API real](#273-las-pantallas-se-prueban-con-respuestas-de-la-api-real)
274. [El benchmark de la superficie, y qué no resuelve v0.17.0](#274-el-benchmark-de-la-superficie-y-qué-no-resuelve-v0170)
275. [La configuración falla cerrada: producción por omisión](#275-la-configuración-falla-cerrada-producción-por-omisión)
276. [Los secretos son archivos, nunca variables de entorno](#276-los-secretos-son-archivos-nunca-variables-de-entorno)
277. [OpenID Connect con PKCE, y la sesión en un BFF](#277-openid-connect-con-pkce-y-la-sesión-en-un-bff)
278. [La API valida el token ella misma, y la JWKS falla cerrada](#278-la-api-valida-el-token-ella-misma-y-la-jwks-falla-cerrada)
279. [Cuatro permisos, cuatro roles y grupos mapeados](#279-cuatro-permisos-cuatro-roles-y-grupos-mapeados)
280. [Una matriz explícita: una ruta sin clase no arranca](#280-una-matriz-explícita-una-ruta-sin-clase-no-arranca)
281. [El token de una persona solo vale si llega por el edge](#281-el-token-de-una-persona-solo-vale-si-llega-por-el-edge)
282. [Las claves de servicio, por su SHA-256 y con sus permisos](#282-las-claves-de-servicio-por-su-sha-256-y-con-sus-permisos)
283. [La sesión vive en el servidor](#283-la-sesión-vive-en-el-servidor)
284. [Cerrar sesión es un POST del mismo origen, con Referrer-Policy same-origin](#284-cerrar-sesión-es-un-post-del-mismo-origen-con-referrer-policy-same-origin)
285. [Un evento de auditoría por petición, con un seudónimo](#285-un-evento-de-auditoría-por-petición-con-un-seudónimo)
286. [Logs JSON con campos fijos, redactados antes de escribir](#286-logs-json-con-campos-fijos-redactados-antes-de-escribir)
287. [Métricas por plantilla de ruta, en un puerto interno](#287-métricas-por-plantilla-de-ruta-en-un-puerto-interno)
288. [Trazas propias, sin la telemetría nativa de FastAPI](#288-trazas-propias-sin-la-telemetría-nativa-de-fastapi)
289. [Vivo y listo son cosas distintas](#289-vivo-y-listo-son-cosas-distintas)
290. [El compose de producción: un servicio, un rol, una red](#290-el-compose-de-producción-un-servicio-un-rol-una-red)
291. [Contenedores sin raíz, sin capacidades y de solo lectura](#291-contenedores-sin-raíz-sin-capacidades-y-de-solo-lectura)
292. [El edge limita por IP y responde 429 con Retry-After](#292-el-edge-limita-por-ip-y-responde-429-con-retry-after)
293. [Una CSP sin unsafe-inline ni unsafe-eval](#293-una-csp-sin-unsafe-inline-ni-unsafe-eval)
294. [El respaldo lleva la base y el almacén juntos](#294-el-respaldo-lleva-la-base-y-el-almacén-juntos)
295. [Se restaura solo en un entorno nuevo, y nada alterado toca nada](#295-se-restaura-solo-en-un-entorno-nuevo-y-nada-alterado-toca-nada)
296. [Simulacros medidos, no supuestos](#296-simulacros-medidos-no-supuestos)
297. [La carga, registrada antes de correrla](#297-la-carga-registrada-antes-de-correrla)
298. [La cadena de suministro, fijada y revisada](#298-la-cadena-de-suministro-fijada-y-revisada)
299. [Las imágenes propias bloquean el CI; las de terceros informan](#299-las-imágenes-propias-bloquean-el-ci-las-de-terceros-informan)
300. [Gitleaks sobre todo el historial, con dos excepciones literales](#300-gitleaks-sobre-todo-el-historial-con-dos-excepciones-literales)
301. [La referencia de Google Cloud: un servicio con tres contenedores, nunca aplicada desde aquí](#301-la-referencia-de-google-cloud-un-servicio-con-tres-contenedores-nunca-aplicada-desde-aquí)
302. [El despliegue solo a mano, con aprobación y sin llaves](#302-el-despliegue-solo-a-mano-con-aprobación-y-sin-llaves)
303. [Qué no resuelve v0.18.0](#303-qué-no-resuelve-v0180)

---

## 1. La corrida es una entidad, no un campo

**Decisión.** `Corrida` es una tabla con vida propia: estado, firma del archivo y de su
contenido, la tolerancia y la versión del contrato con que se juzgó, conteos, tiempos y
detalle. Cada cuenta y cada rechazo la referencian con una llave foránea.

**Por qué.** La corrida tiene que existir aunque no se escriba nada. Si el `run_id` fuera
solo una columna de `cuenta`, una corrida rechazada o fallida no dejaría ningún rastro, y es
justo la que más importa poder auditar: "¿por qué no se publicó la cartera de hoy?".
Además, tiene atributos que no son de ninguna cuenta: qué archivo llegó, con qué regla se
juzgó, cuánto tardó y cómo terminó.

La corrida se registra **antes** de leer el archivo, para que un archivo que ni siquiera se
deja leer también quede registrado.

**Qué haría distinto.** Guardar el archivo original junto a la corrida (hoy solo guarda su
firma), para poder reprocesarlo o mostrarlo. Lo dejé fuera porque eso es almacenamiento de
objetos, y le toca a la fase 4.

## 2. *Fail-closed* en dos niveles: registro y archivo

**Decisión.** Cada registro se juzga contra el contrato; el que no cumple se rechaza, y se
guarda con sus motivos. Después decide la barrera: si la fracción de rechazos cabe en la
tolerancia (`MC_TOLERANCIA_RECHAZO`, 5 % por omisión), se publican los registros válidos.
Si no, **no se publica nada**: la corrida queda `RECHAZADA` y sus rechazos quedan como
evidencia.

**Por qué no todo o nada.** Con todo o nada, un archivo de 10,000 cuentas con una sola fila
mala detiene la operación del día, y el equipo trabaja con la cartera de ayer. Operar con
cartera vieja es el error más caro de la cobranza.

**Por qué no rechazar por registro sin tope.** Eso sería *fail-open*. Un archivo con la
mitad de las filas rotas (un cambio de formato, el archivo equivocado) publicaría la otra
mitad, y la operación trabajaría sobre media cartera sin saberlo. Pasado cierto punto, el
problema ya no son filas sueltas: es el archivo.

**La tolerancia vive en el servidor, no en la petición.** Si el cliente pudiera mandarla,
la barrera sería opcional. Queda guardada en cada corrida, para saber con qué regla se
decidió aunque la configuración cambie después. `0` significa todo o nada; `1` no se
admite, porque publicaría aunque no pasara ningún registro.

**Una cartera, un corte.** Una cartera es la foto de un día, así que la barrera también
exige que sus registros válidos traigan una sola fecha de corte. Con dos no hay filas
culpables: no se sabe cuál de los cortes es el de la cartera, y tomar el más reciente, como
se hacía antes, publicaría una foto mezclada como si fuera de un día. La cartera entera se
rechaza, y el detalle dice qué cortes trae y cuántos registros tiene cada uno. Es
`RECHAZADA` y no `FALLIDA`: el contenido se pudo juzgar, y no cumple. Cuentan solo los
registros válidos, porque son los que se publicarían.

**Tres estados finales, no un "exitosa sí/no".** `RECHAZADA` (se juzgó y no pasó) y
`FALLIDA` (no se pudo juzgar: archivo ilegible, columnas faltantes, error interno) piden
acciones distintas. La primera se arregla corrigiendo datos; la segunda, corrigiendo el
archivo o el sistema.

**Qué haría distinto.** Hoy hay una sola tolerancia para todo. En operación real
probablemente habría una por tipo de defecto (un cliente duplicado es más grave que un
municipio mal escrito) o por proyecto.

## 3. La validación vive en el contrato, no en el endpoint

**Decisión.** Las reglas de los datos están en un solo lugar, `contratos/cartera.py`, y la
decisión de publicar está en `ingesta/corridas.py`. El endpoint solo valida la petición:
que traiga un archivo, que el formato sea soportado, que la paginación esté en rango.

**Por qué.** Porque hay más de una puerta de entrada. El CLI (`motor-cartera cargar`) y la
API ejecutan exactamente el mismo proceso. Si la validación viviera en el endpoint, el CLI
se la saltaría, y lo mismo haría el orquestador de la fase 3. Y porque una regla escrita
dos veces termina diciendo dos cosas distintas.

Lo que la API sí valida con Pydantic sale del contrato, sin reescribirse. Los filtros
`producto` y `canal` del resumen son enums construidos desde `PRODUCTOS` y `CANALES`, así
que `?producto=HIPOTECARIO` es un 422 porque el contrato no conoce ese producto, no porque
alguien haya copiado la lista. Las columnas que el lector exige también salen del contrato.

**Cada corrida guarda la versión del contrato.** `VERSION_CONTRATO` (hoy `cartera/v1`)
nombra las reglas vigentes: las de cada registro, la de un solo corte y la forma canónica
con que se firma el contenido. La corrida la registra al abrirse, como la tolerancia, y así
se sabe con qué reglas se juzgó aunque el contrato cambie después. Es una constante escrita
a mano y no el número de versión del paquete ni un commit, porque lo que importa es cuándo
cambian las reglas, no cuándo cambia el código. A las corridas que ya existían cuando se
empezó a guardar se les puso `sin-registro`, en lugar de suponerles una.

**Qué haría distinto.** Nada en lo esencial. Si las reglas crecieran (validaciones cruzadas,
catálogo INEGI), el contrato seguiría siendo el lugar. Hoy nada impide cambiar una regla sin
cambiar la versión: lo cuidan la revisión y la prueba que fija la forma canónica.

## 4. Validar columna por columna

**Decisión.** `separar_rechazos` valida cada columna por separado y, dentro de cada una,
repite sobre lo que sí se pudo convertir. Al final valida el contrato completo sobre lo que
queda.

**Por qué.** Por un comportamiento de pandera que encontré al probar. Si una columna no se
puede convertir a su tipo en alguna fila (un `N/D` en el saldo), las demás reglas de esa
columna se evalúan sobre el texto crudo, fallan con un `TypeError` y se reportan **sin
número de fila**. Validando todo de una pasada, un saldo negativo se colaba como válido solo
porque otra fila traía `N/D`. Hay una prueba que reproduce exactamente ese caso.

Otras dos decisiones del contrato, del mismo tipo:

- **Fechas solo en ISO 8601.** Con el formato por omisión, pandas infiere el formato a
  partir de la primera fila: `01/02/2026` entra como 2 de enero o como 1 de febrero sin
  avisar, y un archivo con formatos mezclados falla sin decir en qué fila. Una fecha ambigua
  se rechaza; no se adivina.
- **El saldo se acota a lo que cabe en la base** (`NUMERIC(14, 2)`). Sin ese tope, una sola
  fila absurda tumbaba la corrida entera al insertar, en lugar de rechazarse con su motivo.

**Un duplicado rechaza a todas sus copias**: no hay forma de saber cuál es la buena.

**Rendimiento.** Cuando una fecha no se puede convertir, pandera busca las culpables celda
por celda: 3 segundos por cada 10,000 filas. Ahora las fechas se convierten de un golpe,
con las mismas opciones que declara el contrato, y pandera recibe solo lo que sí convirtió.
La prueba del generador exige exactamente el mismo conjunto de rechazos, así que el atajo
no cambia el resultado.

**Qué haría distinto.** Separar la conversión de tipos y las reglas en dos pasos propios y
explícitos: convertir cada columna de un golpe (como ya se hace con las fechas) y después
validar sin conversión. Sería más rápido y no dependería de cómo pandera mezcla las dos
cosas, a cambio de reimplementar la conversión fuera del contrato.

## 5. Los códigos HTTP

Cada código es una decisión. La regla general: **los códigos HTTP describen la petición, no
el trabajo**. El POST crea una corrida; lo que pasa al procesar el archivo es el estado de
esa corrida, no el código de una respuesta que ya se envió.

| Código | Cuándo | Por qué ese y no otro |
|---|---|---|
| `201` | `POST /corridas` registró la corrida | La petición crea la corrida antes de responder: ya tiene `run_id` y se consulta en `Location`. Lo que sigue en curso es su procesamiento, y eso es su `estado`. `202` también sería válido: pondría el acento en que el procesamiento es asíncrono, y RFC 9110 no exige que el recurso todavía no exista. Se eligió `201` porque lo que la petición hace, crear la corrida, ya está hecho. |
| `400` | El cuerpo no se puede ni interpretar | Reservado para peticiones ilegibles. |
| `422` | La petición se entiende pero no cumple el contrato de la API: falta el archivo o está vacío, paginación fuera de rango, un `run_id` que no es UUID, un producto que el contrato no conoce | La petición es sintácticamente correcta y semánticamente inválida (RFC 9110, §15.5.21). Eso separa 422 de 400. |
| `409` | El archivo ya se publicó o se está procesando; se piden los rechazos de una corrida que no ha terminado; se pide el resumen de una corrida que no publicó | La petición es válida pero choca con el estado de lo que toca. |
| `404` | No existe la corrida; no hay ninguna cartera publicada | En el segundo caso, un `200` con resumen vacío no se distinguiría de una cartera con cero cuentas: justo la salida engañosa que el diseño evita. |
| `401` | Falta la API key o no es válida | No `403`, que significa "sé quién eres y no te dejo"; aquí no hay identidad válida. Va con `WWW-Authenticate`, como pide RFC 9110. |
| `413` | El archivo pasa del tope | Es un problema de tamaño, no de forma. |
| `415` | El archivo no es xlsx, csv ni zip | RFC 9110 permite `415` por un formato no soportado del contenido, no solo del `Content-Type`. Aquí se decide por la extensión del archivo. |
| `503` | `/salud` con la base caída | Un health check que responde `200` sin base no le sirve a ningún orquestador. |
| `500` | Error no controlado | Sin detalle interno: el detalle va a la bitácora. |

**422 contra 409, en una línea:** 422 no depende del estado del servidor, así que reintentar
la misma petición siempre fallará; 409 sí depende, y la misma petición con otro estado
funcionaría.

**¿Por qué un rechazo devuelve 422 y no 400?** Porque 400 es para lo que no se puede
interpretar, y 422 para lo que se interpreta pero no cumple. La pregunta tiene una segunda
parte que importa más: **un archivo con registros inválidos no devuelve ningún error
HTTP.** La petición de subirlo es válida y crea una corrida (`201`); el veredicto sobre el
contenido es un recurso (`estado` y `/rechazos`), no un código de estado. Devolver 422 por
el contenido obligaría a procesar el archivo dentro de la petición, y a elegir entre dos
malas opciones: no dejar rastro del intento o crear un recurso y responder con un error.

**Rechazos de una corrida en proceso: 409, no una lista vacía.** Una lista vacía diría "no
hubo rechazos", y eso todavía no se sabe.

**Qué haría distinto.**

- Revisar la firma de bytes del archivo además de su extensión: un xlsx es un zip, y un
  `.csv` puede traer cualquier cosa. Hoy un archivo mal nombrado no da `415`; termina en una
  corrida `FALLIDA`, que es seguro pero llega más tarde.
- Si la API tuviera consumidores externos, consideraría `303 See Other` para el archivo ya
  publicado: RFC 9110 lo sugiere cuando el resultado de un POST equivale a un recurso que ya
  existe. Elegí 409 porque aquí subir dos veces el mismo archivo suele ser un error del
  operador (creía estar subiendo el de hoy), y conviene que se note en lugar de redirigirlo
  en silencio.

## 6. Autenticación

**Decisión.** Una API key en la cabecera `X-API-Key`, leída de `MC_API_KEY`. Protege todo,
salvo `/salud` y la documentación.

- **Sin clave, la API no arranca.** Una API que arranca sin clave arranca abierta. La clave
  de desarrollo vive en `docker-compose.yml`, no en el código.
- **Se compara con `secrets.compare_digest`**, que tarda lo mismo acierte o no: el tiempo de
  respuesta no revela cuántos caracteres coinciden.
- **`/salud` no pide clave**: la consultan balanceadores y orquestadores, y no expone datos.
- **La documentación es pública**: describe la API, no los datos.

**Qué haría distinto.** Usar `Authorization: Bearer` en lugar de `X-API-Key`, porque muchos
proxies y bitácoras ya saben ocultar `Authorization` y no una cabecera propia. Después,
varias claves guardadas como hash en la base, con dueño, alcance y rotación. OAuth o JWT,
solo cuando haya usuarios de verdad.

## 7. La estructura de los errores

**Decisión.** Todas las respuestas 4xx y 5xx tienen la misma forma: `codigo`, `mensaje`,
`detalles` (uno por campo con problema) y `run_id` (la corrida con la que tiene que ver el
error). Eso incluye los errores que FastAPI genera por su cuenta: validación, ruta
inexistente, método no permitido y excepción no controlada.

**Por qué.** Un cliente no debería tener que distinguir formatos de error según qué capa lo
generó. `codigo` es estable y es lo que se compara; `mensaje` es para personas y puede
cambiar. `run_id` está porque en esta API casi todo error tiene que ver con una corrida, y
en el 409 por archivo duplicado es la información útil: a qué corrida ir.

Los mensajes de validación de Pydantic, que vienen en inglés, se traducen para los casos que
esta API produce. Cada ruta documenta en OpenAPI sus códigos de error, con un ejemplo de
cada uno y el esquema real, no el que FastAPI pone por omisión.

**Qué haría distinto.** Con consumidores externos usaría el estándar, RFC 9457 (*Problem
Details*): `type`, `title`, `status`, `detail` e `instance`, servido como
`application/problem+json`. Las ideas son las mismas (un código legible por máquina, un
mensaje para personas, campos de extensión); aquí elegí un esquema propio en español,
coherente con el resto del proyecto.

## 8. Paginación

**Decisión.** Por número de página: `pagina` (desde 1) y `por_pagina` (hasta 500), igual en
los dos endpoints que devuelven listas. La respuesta trae `total`, `pagina`, `por_pagina` y
`elementos`. Una página después de la última devuelve `elementos` vacío, no 404.

**Por qué alcanza aquí.** El problema clásico de paginar por desplazamiento es que la
colección cambie entre una página y otra. Los rechazos de una corrida terminada ya no
cambian. El resumen sí podría cambiar, si se publica otra cartera entre la página 1 y la 2;
por eso acepta `run_id`: se fija el de la primera respuesta y las páginas no se mezclan. Los
segmentos salen en un orden fijo (los tramos por su posición, no alfabéticamente), para que
la paginación sea estable.

**Qué haría distinto.** A escala, paginación por llave (`fila > última vista`), que no se
degrada en páginas lejanas como `OFFSET`.

## 9. Una cartera se publica una sola vez

**Decisión.** Cada corrida guarda la firma SHA-256 del archivo. Subir un archivo que ya se
publicó, o que otra corrida está procesando, responde `409` con el `run_id` de esa corrida.
Lo que no llegó a publicarse (`RECHAZADA`, `FALLIDA`) sí se puede reintentar.

**Lo garantiza la base, no solo el código.** La revisión previa es la vía amable, porque
sabe decir qué corrida fue, pero dos peticiones simultáneas pueden pasarla en el mismo
instante. Un índice único parcial sobre la firma de las corridas `EXITOSA` impide que ambas
publiquen: la segunda termina `FALLIDA`, con el motivo. Lo comprobé contra el servidor real
subiendo el mismo archivo tres veces en menos de un segundo: una corrida publicó, dos
fallaron, y las 9,800 cuentas quedaron una sola vez. Esa misma prueba mostró que el cliente
recibía `201` las tres veces, y por eso un archivo en proceso también responde 409.

**Corridas abandonadas.** Hasta v0.4.0, si la API se reiniciaba a media corrida, esa corrida
quedaba `EN_PROCESO` y nadie la terminaba; para que no bloqueara ese archivo para siempre, una
corrida `EN_PROCESO` de más de 15 minutos dejaba de contar. *Desde v0.5.0* no hay abandonadas: el
archivo y el trabajo de la ingesta están en PostgreSQL, y si el worker muere, otro la termina (ver
43 y 47). Ese plazo desapareció, y un índice único parcial, `ux_corrida_firma_en_proceso`, admite a
lo más una corrida `EN_PROCESO` por archivo.

**La firma del archivo y la del contenido.** `firma` identifica el archivo: los bytes que
llegaron. `firma_contenido` identifica la cartera: es el SHA-256 de la forma canónica de sus
registros válidos, y es la misma si llega en xlsx, en csv o en zip, con las filas en
cualquier orden. La forma canónica está descrita en `firmar_contenido` y fijada por una
prueba: columnas en orden alfabético, filas ordenadas, cada valor como texto (fechas
AAAA-MM-DD, saldos con dos decimales), JSON en UTF-8. Se calcula cuando la corrida se
juzga; una `FALLIDA` no la tiene.

**La forma canónica es parte del contrato `cartera/v1`.** Un cambio incompatible en ella
cambiaría todas las firmas, y dos corridas con la misma cartera ya no coincidirían. Por eso
exige una versión nueva del contrato, o una decisión explícita equivalente que diga cómo
comparar las firmas de antes con las de después.

**Por ahora es trazabilidad, no una regla.** La unicidad sigue siendo por archivo: la misma
cartera en xlsx y en csv se publica dos veces, pero su firma de contenido deja ver que es la
misma. Impedirlo cambiaría qué se publica (¿una corrección con el mismo contenido es un
duplicado?), y eso es otra decisión, no un efecto de agregar la firma.

## 10. La cartera vigente es la del corte más reciente

**Decisión.** `GET /cartera/resumen` resume la corrida `EXITOSA` con la fecha de corte más
reciente. Si dos corridas publicaron el mismo corte, vale la última: es una corrección.

**Por qué por corte y no por hora de subida.** Si alguien sube hoy la cartera de la semana
pasada, no debe reemplazar a la de hoy. Operar con cartera vieja es justo el error que la
barrera existe para evitar. Y solo cuenta lo publicado: lo rechazado o fallido nunca es
vigente.

**El corte de una corrida no se elige.** Una cartera con más de una fecha de corte no se
publica (ver 2), así que el corte de una corrida publicada es el de todos sus registros.
Antes la corrida tomaba el más reciente, y un archivo con dos cortes quedaba vigente con la
fecha de uno solo.

**Desde v0.7.0.** La cartera vigente de la operación sigue siendo esta, y "vale la última" del
mismo corte. El modelo histórico no hace lo mismo: guarda un solo corte canónico por fecha, una
segunda cartera del mismo día con otra firma de contenido es un conflicto que no lo cambia, y su
último corte (`GET /cartera/cortes`) es el de fecha más reciente (ver 74).

## 11. Procesar en segundo plano, dentro de la API

**Decisión.** `POST /corridas` registra la corrida y la procesa con `BackgroundTasks` de
FastAPI, en el mismo proceso de la API.

**Por qué.** Una cartera real puede tener cientos de miles de filas. Atar la petición HTTP a
su procesamiento haría esperar minutos al cliente, y un timeout del proxy cortaría el
trabajo. Con el patrón "crear y consultar", el POST responde de inmediato y el estado se
consulta después.

**Lo que tiene de frágil.** No es durable: si el proceso muere, la corrida queda huérfana
(ver 9). No hay reintentos ni límite de concurrencia, así que dos corridas grandes a la vez
compiten por el mismo proceso. Además, con `TestClient` la tarea termina antes de que vuelva
la respuesta, de modo que las carreras solo se ven contra un servidor real, y así fue como
aparecieron.

**Qué haría distinto.** Un worker separado con una cola, o el orquestador de la fase 3, que
tome las corridas `EN_PROCESO` con reintentos, tiempos límite y barrido de huérfanas.

**Desde v0.5.0.** Es lo que se hizo: `POST /corridas` guarda el archivo y deja la ingesta en una
cola durable sobre PostgreSQL, en la misma transacción que la corrida, y un worker aparte la
ejecuta, con lease, latido y reintentos (ver 42 a 47). `BackgroundTasks` ya no se usa.

## 12. Detalles de modelado

- **`run_id` (UUID) es el identificador público; `id` es interno.** El consecutivo es
  adivinable y revela cuántas corridas hay. El UUID existe desde que se registra la corrida,
  así que la bitácora lo tiene desde la primera línea.
- **`cliente_unico` es único por corrida, no global.** El mismo cliente viene en la cartera
  de cada día; hacerlo global obligaría a sobrescribir la foto de ayer para guardar la de hoy.
- **`fecha_corte` es `date`, no `datetime`.** Es un día del calendario, no un instante: con
  zona horaria, tarde o temprano el corte del 31 aparece como el 30.
- **Los instantes van en UTC**, sin importar la zona horaria del servidor de PostgreSQL.
- **Los saldos son `Decimal` y viajan como texto** en JSON, para que ningún cliente pierda
  centavos al leerlos como flotante.
- **El rechazo guarda el registro tal como llegó** (texto, en JSONB) junto con sus motivos,
  para que el operador sepa qué corregir sin abrir el archivo.
- **Las restricciones tienen nombres fijos** (convención de nombres de SQLAlchemy), para
  que una migración futura pueda referirse a ellas.

## 13. Migraciones y pruebas

- **Migrar es un paso propio en el compose** (el servicio `migraciones`), no un efecto del
  arranque de la API: si falla, la API no arranca sobre un esquema a medias, y con varias
  réplicas no competirían por migrar a la vez.
- **El CI corre `alembic check`**: si alguien cambia un modelo sin su migración, falla.
  También prueba que la migración baja.
- **Las pruebas usan el esquema de las migraciones, no `create_all`**, así que cada vez que
  corren prueban también la migración.
- **Las pruebas corren contra PostgreSQL real.** El índice único parcial, JSONB y el orden
  de los tramos son de PostgreSQL, y una base simulada los habría ocultado.
- **Se niegan a correr sobre una base que no se llame `*_test`**, porque la vacían antes de
  cada prueba.
- **Una prueba de humo** (`scripts/prueba_de_humo.py`) corre en el CI contra el compose, en
  un runner limpio: es la definición de terminado verificada donde no hay nada instalado de
  antemano.

## 14. Versiones fijadas, con 3.12 como piso

**Decisión.** `uv.lock` fija la versión exacta y el hash de cada dependencia, directa o no.
El CI y la imagen instalan con `uv sync --locked`, que falla si el lock ya no corresponde al
`pyproject.toml`, y con la misma versión de uv con que se generó el lock: 0.12.21, fijada en
el CI y en el Dockerfile (ahí también por digest, porque una etiqueta se puede mover).
Subirla es cambiar los dos lugares a la vez. Los mínimos del `pyproject.toml` siguen siendo
lo que el proyecto admite; el lock es la combinación que se probó.

**Por qué.** Lo que el contrato rechaza depende de pandas y pandera, no solo del código: la
validación columna por columna (ver 4) existe por un comportamiento de pandera que una
versión nueva podría cambiar. Sin lock, eso cambiaba sin que cambiara una línea. Con el lock,
cambiar de versión es un diff en un commit, y el CI lo prueba antes de que llegue a `main`.

**Por qué universal y no el del Python local.** El lock resuelve para cualquier Python que
admita `requires-python = ">=3.12"`, que es el contrato del proyecto y la versión del CI y de
la imagen. Que la máquina donde se genera tenga otro Python (hoy, un 3.14) no mueve ese piso:
el lock no se genera para ese intérprete. Hoy cada dependencia tiene una sola versión para
3.12 y para 3.14; si alguna se separara, el lock guardaría una para cada tramo.

**Qué haría distinto.** Un bot que proponga las actualizaciones (Dependabot o Renovate),
para que el lock y uv no se queden viejos sin que nadie lo note.

## 15. El control de archivos complementa al .gitignore, no lo repite

**Decisión.** `scripts/verificar_archivos_trackeados.py` corre en el CI sobre el índice de
git. Falla si un archivo trackeado lo excluye el `.gitignore`, si lo excluiría sin distinguir
mayúsculas, o si por dentro es un zip (xlsx, xlsm, ods) o un documento OLE2 (xls), se llame
como se llame.

**Por qué.** El `.gitignore` frena lo que todavía no está trackeado, y nada más. No ve un
`git add -f` ni un archivo que ya estaba antes de la regla. En Linux distingue mayúsculas,
así que `*.xlsx` no detiene `CARTERA.XLSX`, que el lector del motor sí abre. Y solo conoce
nombres: un xlsx renombrado pasa.

**Por qué no una lista propia.** El control no tiene reglas de nombres: le pregunta al mismo
`.gitignore` qué excluye. Una segunda lista terminaría diciendo otra cosa, como una regla de
validación escrita dos veces (ver 3). Lo único propio es reconocer el formato por sus
primeros bytes, que es justo lo que un nombre no puede decir.

**Por qué el índice y no la carpeta.** Lo que se sube es el índice. Un archivo que en disco ya
cambió puede seguir siendo la cartera en el índice, y es esa versión la que entra al commit.

**Lo que no cubre.** La historia: un archivo que se subió y después se borró sigue ahí, y en
un repositorio público ya salió; el control es una alarma, no una bóveda. Un csv no se
reconoce por sus bytes, así que con otro nombre pasa. Y confía en el `.gitignore` del mismo
commit: quitar una regla también la quita del control, y ese cambio tiene que verse en la
revisión.

**Qué haría distinto.** Correrlo también como *hook* de pre-commit, para que detenga el
archivo antes del commit y no después del push. Y en los pull requests revisar cada commit,
no solo el último.

## 16. El núcleo de decisión es puro y versionado

**Decisión.** Las reglas de `decision/v1` viven en `decision/reglas.py`, que solo usa la
biblioteca estándar y los tramos de `atraso.py`. No importa FastAPI, SQLModel ni pandas, y no
lee PostgreSQL, la configuración, el reloj ni el azar. `decidir_cuenta` recibe los días de
atraso y el saldo de una cuenta y devuelve su segmento, su prioridad, su canal recomendado y sus
motivos. Una prueba importa el módulo en un proceso limpio y exige que no cargue nada más.

**Por qué.**

- **Determinismo.** La misma entrada con la misma versión da siempre la misma decisión, en
  cualquier proceso y en cualquier máquina. Una prueba la calcula en otro proceso, con otra
  semilla de hash, y exige el mismo resultado byte por byte. Por eso el umbral de saldo no es
  configurable: si dependiera del entorno, `decision/v1` diría cosas distintas en dos servidores.
- **Pruebas.** Sin base ni framework, los golden tests fijan la decisión exacta, motivos
  incluidos, en cada frontera de días y de saldo, y corren sin PostgreSQL.
- **Auditoría.** Las reglas son tablas cortas que se leen de arriba abajo, y cada motivo nombra
  la regla que aplicó, el campo que leyó o produjo y su valor. Una decisión guardada se vuelve a
  explicar con la misma función.
- **Reutilización.** Hoy lo llama el servicio que decide una corrida sobre PostgreSQL, pero no
  depende de él: un worker, un orquestador o un proceso por lotes llamarían a la misma función.

***Fail-closed* también aquí.** La entrada se valida al construirse: los días tienen que ser un
entero no negativo (un `bool` no cuenta) y el saldo un `Decimal` finito y no negativo, nunca un
`float`. Y solo trae lo que las reglas usan: sin producto, canal ni claves geográficas, que así
no pueden influir por descuido, y el canal recomendado no puede ser una copia del de la cartera.
Un tramo sin segmento o un motivo fuera del catálogo hacen fallar la decisión, en lugar de
adivinar.

**`decision/v1` es la versión de las reglas, no la del paquete.** Hay tres versiones con tres
significados: la del paquete cambia con el código; `cartera/v1`, cuando cambia qué datos se
publican; `decision/v1`, cuando cambia cualquier resultado posible de las reglas (un segmento,
una prioridad, un canal, el umbral o un código de motivo). Como `VERSION_CONTRATO` (ver 3),
`VERSION_REGLAS_DECISION` es una constante escrita a mano, y no el número del paquete ni un
commit: un cambio de código que no mueve ninguna decisión no la cambia, y uno que mueve una
sola, sí.

**Son reglas, no un modelo.** `cartera/v1` no trae pagos, promesas ni gestiones, y sin eso no hay
recuperabilidad que estimar. Las reglas y el umbral (`50,000.00`) son sintéticos, propios de
este proyecto público: no vienen de ninguna operación real.

**Qué haría distinto.** Como con el contrato, nada impide cambiar una regla sin cambiar la
versión: lo cuidan la revisión y los golden tests, que fallan con cualquier resultado distinto.
Con varias versiones en uso, guardaría con cada ejecución una huella de las tablas de reglas,
para que dos despliegues que digan `decision/v1` no puedan decidir distinto sin que se note.

## 17. Persistir la ejecución, no solo el resultado

**Decisión.** Cada vez que se decide una corrida se registra una `EjecucionDecision`: qué
corrida, con qué `version_reglas`, su estado (`EN_PROCESO`, `EXITOSA` o `FALLIDA`), cuándo
empezó y terminó, cuántas cuentas evaluó, cuántas decisiones publicó y un `detalle` en
palabras. Cada decisión es una `DecisionCuenta` de esa ejecución: el segmento, la prioridad y el
canal recomendado de una cuenta, con sus motivos en JSONB.

**Por qué una entidad aparte.** Por lo mismo que la corrida (ver 1): la ejecución tiene que
existir aunque no deje decisiones. Una `FALLIDA` es justo la que hay que poder auditar ("¿por
qué la cartera de hoy no tiene decisiones?"), y con solo las filas de `DecisionCuenta` no
dejaría rastro. Además tiene atributos que no son de ninguna cuenta: la versión de las reglas,
los tiempos, los conteos y el motivo del fallo.

**`DecisionCuenta` no copia la cuenta.** El cliente, el saldo, el atraso, el producto y el canal
viven en `cuenta` y se leen con un JOIN: una copia podría dejar de coincidir con la cuenta que se
decidió, y una llave foránea no. Una ejecución decide cada cuenta a lo más una vez, y lo
garantiza una restricción única sobre `(ejecucion_decision_id, cuenta_id)`.

**Sin cascada.** Las llaves foráneas no borran en cascada: borrar una corrida con ejecuciones, o
una cuenta con decisiones, falla en lugar de llevarse la evidencia.

**`decision_run_id` es el identificador público; `id` es interno.** Como el `run_id` de la
corrida (ver 12): un UUID que existe desde que se registra la ejecución, que no se puede adivinar
ni revela cuántas hay. No se llama `run_id` para no confundirlo con el de la corrida; la API
devuelve los dos, y ningún `id`.

**No hay `RECHAZADA`.** El motor no juzga la cartera: eso ya lo hizo su corrida. Decide sobre una
cartera publicada, y termina o falla. El estado se guarda como `VARCHAR` con `CHECK`, igual que
el de la corrida.

**Qué haría distinto.** Con muchas ejecuciones guardadas, `decision_cuenta` sería la tabla más
grande del esquema: una fila por cuenta y por ejecución. Antes de que eso pese en las consultas,
la particionaría por fecha o archivaría las ejecuciones viejas.

## 18. Publicar todas las decisiones o ninguna

**Decisión.** Una ejecución corre en dos transacciones:

- **T0.** Registra la ejecución `EN_PROCESO` y hace `COMMIT` antes de decidir nada: aunque todo
  lo demás falle, el intento queda registrado.
- **T1.** Bloquea la ejecución, lee las cuentas de la corrida por lotes, decide cada una con el
  núcleo, inserta las decisiones de cada lote, comprueba que estén completas y cierra la
  ejecución `EXITOSA`. Un solo `COMMIT`, al final, publica todo.

Si algo falla en T1, el `ROLLBACK` se lleva todas las `DecisionCuenta` del intento, y en otra
transacción la ejecución queda `FALLIDA`, con `cuentas_decididas = 0` y el motivo en `detalle`.
El detalle no lleva trazas: van a la bitácora.

**Por qué no publicar por lotes.** Una ejecución que confirmara lote por lote y fallara en el
quinto dejaría media cartera decidida, y la operación trabajaría sobre la mitad sin saberlo: el
mismo error *fail-open* que la barrera de la ingesta evita (ver 2). Aquí no hay tolerancia,
porque no hay decisión parcial aceptable: la cuenta que falta es una cuenta que nadie va a
gestionar.

**Completas antes de publicar.** Antes del cierre, las cuentas evaluadas, las cuentas de la
corrida y las decisiones guardadas tienen que ser el mismo número, contado dentro de la misma
transacción. Que el motor haya evaluado todo lo que leyó no basta: si la lectura se saltara una
cuenta, el conteo lo descubre. Una corrida sin cuentas no se publica con un éxito vacío. Y
aunque T0 ya revisó la corrida, T1 la vuelve a leer, porque entre las dos transacciones pudo
cambiar.

**`cuentas_evaluadas` es evidencia, no resultado.** En una `FALLIDA` dice hasta dónde llegó el
motor, por ejemplo 4,000 de 9,800, aunque no se haya publicado nada. `cuentas_decididas` dice
cuántas decisiones existen, y en una `FALLIDA` siempre es cero.

**Por lotes, con *keyset* y no con `OFFSET`.** T1 lee las cuentas en lotes de
`TAMANO_LOTE = 1000`, en el orden de `cliente_unico`, y cada lote empieza después del último
cliente del anterior. El índice único `(corrida_id, cliente_unico)` llega ahí sin recorrer lo ya
leído; con `OFFSET`, cada lote obligaría a la base a recorrer y descartar todos los anteriores.
Solo se leen las columnas que usan las reglas, y las decisiones de cada lote van en un solo
`INSERT`, sin commit entre lotes. Una prueba cuenta las consultas: crecen con los lotes, no con
las cuentas. `TAMANO_LOTE` es un ajuste técnico y no configuración: no cambia ninguna decisión,
solo la memoria y las idas a la base.

**La paginación pública es otra cosa.** La API sigue paginando por número de página (`pagina` y
`por_pagina`, con `OFFSET` y `LIMIT`; ver 8). Ahí alcanza: el cliente pide páginas sueltas de una
colección que ya no cambia, porque las decisiones de una ejecución `EXITOSA` no se modifican, y
el orden por `cliente_unico` es estable. El *keyset* es para el recorrido interno, que lee cada
cuenta una vez, de principio a fin.

**Qué haría distinto.** Con carteras de millones de cuentas, T1 sería una transacción larga.
Como la API solo muestra las decisiones de una ejecución `EXITOSA`, se podría confirmar por
lotes y dejar que el cierre sea la publicación, a cambio de limpiar después las decisiones de
una `FALLIDA`. Hoy no hace falta, y el todo o nada en una sola transacción es más fácil de
garantizar.

## 19. Idempotencia y carreras

**Decisión.** Una corrida se decide con éxito una sola vez por versión de las reglas. Como en la
ingesta (ver 9), en dos niveles:

- **La revisión previa es la vía amable.** Antes de registrar nada, `abrir_ejecucion` busca una
  ejecución `EXITOSA` de esa corrida con esa versión. Si la hay, levanta `DecisionYaGenerada` y
  no queda ni el intento.
- **El índice es la garantía.** `ux_ejecucion_decision_exitosa` es un índice único parcial sobre
  `(corrida_id, version_reglas)` que solo cuenta las ejecuciones `EXITOSA`. Dos peticiones
  simultáneas pueden pasar la revisión en el mismo instante y trabajar a la vez, pero solo una
  puede cerrar `EXITOSA`. El cierre de la otra choca con el índice: revierte sus decisiones,
  queda `FALLIDA` con el motivo y levanta `DecisionYaGenerada` con la que ganó.

Lo que no terminó `EXITOSA` no cuenta: una `FALLIDA` se reintenta con una ejecución nueva, y
`decision/v2` podrá decidir la misma corrida sin chocar con `decision/v1`.

**La misma ejecución, un worker a la vez.** Si dos workers tomaran la misma ejecución, por un
reintento o por un orquestador que la reparte dos veces, `ejecutar_decision` la lee con
`SELECT ... FOR UPDATE`: el segundo espera a que el primero confirme o revierta, la encuentra
terminada y no la vuelve a ejecutar.

**Un fallo tardío no degrada un estado terminal.** La transición `EN_PROCESO → FALLIDA` es un
`UPDATE` condicionado a que la ejecución siga `EN_PROCESO` en la base, no en la memoria del
proceso. Si entre el rollback y ese registro otro worker la dejó `EXITOSA`, el fallo no la cambia
ni borra sus decisiones: queda solo en la bitácora. `EXITOSA` y `FALLIDA` son terminales.

Las pruebas de estas carreras corren contra PostgreSQL real: dos ejecuciones de la misma
corrida, dos workers sobre la misma ejecución y un fallo que llega tarde.

**Qué haría distinto.** Se podría impedir desde el principio que dos intentos sobre la misma
corrida trabajen a la vez, con un bloqueo por corrida. Elegí dejarlos trabajar y que decida el
índice: es más simple, la garantía no depende de que todos los caminos tomen el bloqueo, y el
trabajo perdido solo aparece en una carrera, que es rara.

## 20. POST síncrono y semántica 201

**Decisión.** `POST /corridas/{run_id}/decisiones` decide la corrida en la misma petición y
responde con la ejecución ya terminada. A diferencia de `POST /corridas`, no usa
`BackgroundTasks` ni una cola. Responde `201`, con `Location: /decisiones/{decision_run_id}`,
tanto si la ejecución termina `EXITOSA` como `FALLIDA` (D1).

**Por qué síncrono en v0.2.0.** Decidir una cartera ya publicada es mucho más barato que
ingerirla: no hay archivo que leer ni contrato que juzgar, solo lo que las reglas leen de cada
cuenta y un `INSERT` por lote. Así el cliente recibe el resultado en la respuesta, sin consultar
un estado después. Y `BackgroundTasks` agregaría una segunda forma de dejar trabajo huérfano
(ver 11) sin ganar durabilidad: esa la da un worker con cola, que es trabajo de la orquestación
(ver 22).

**Lo que cuesta.** La conexión HTTP queda abierta mientras se decide. La de la base, no: el POST
busca la corrida, copia su id y su `run_id`, y termina la transacción de la sesión HTTP antes de
llamar al servicio, que abre las suyas. Así la conexión vuelve al pool y la petición no la
retiene mientras decide; una prueba exige que esa transacción haya terminado al entrar al motor.

**`201` quiere decir que la ejecución se creó.** Es la regla general de esta API (ver 5): el
código HTTP describe la petición, no el trabajo. La petición creó un recurso, y cómo terminó el
motor es el `estado` de ese recurso. Una `FALLIDA` no es un `500`: el motor falló de forma
controlada, la ejecución existe, dice por qué en su `detalle` y se reintenta con otro POST. Un
`500` diría que falló la petición, y no que hay una ejecución que consultar. Con más razón que en
`POST /corridas`, tampoco es `202`: cuando el POST responde, el trabajo ya terminó.

**El estado HTTP no es el estado del recurso.** Un cliente revisa las dos cosas: el código, para
saber si la petición se atendió, y `estado`, para saber si hay decisiones. `201` con `FALLIDA`
quiere decir "se intentó, quedó registrado y no hay decisiones".

**Lo que sí rechaza la petición.** Una corrida que no terminó `EXITOSA` da
`409 CORRIDA_NO_PUBLICADA` y no registra ninguna ejecución: no hay cartera que decidir. Una ya
decidida da `409 DECISION_YA_GENERADA` (ver 21).

**Qué haría distinto.** Con carteras de cientos de miles de cuentas, o detrás de un proxy con un
timeout corto, el POST pasaría a ser asíncrono, como el de las corridas: `201` con la ejecución
`EN_PROCESO` y su `Location`, y un worker que la decide. Para el cliente cambiaría poco, porque
el código ya quiere decir que la ejecución se creó, y una `EN_PROCESO` ya se consulta como
cualquier otra, con `terminada_en` y `duracion_segundos` en `null`.

**Desde v0.5.0.** Así quedó, por durabilidad más que por tamaño: el POST responde `201` con la
ejecución `EN_PROCESO` y su `Location`, y la decide un worker (ver 51).

## 21. Historial y vocabulario versionado

**`409` sin `Location` (D2).** Si la corrida ya tiene una ejecución `EXITOSA` con `decision/v1`,
el POST responde `409 DECISION_YA_GENERADA` con el `run_id` de la corrida, sin `Location` y sin
nombrar la ejecución, también cuando la detecta el índice en una carrera. El cliente la descubre
en el historial, `GET /corridas/{run_id}/decisiones`, donde la `EXITOSA` con `decision/v1` es la
que publicó, y después consulta su detalle en `GET /decisiones/{decision_run_id}`.

**Por qué no la nombra.** `Location` tiene un significado en un `201`, el recurso creado, y en
una redirección, adónde ir; en un `409` no lo tiene, y apuntar a la ejecución que ya publicó lo
convertiría en una redirección encubierta. Nombrarla en el cuerpo, tampoco: la forma de los
errores es la misma en toda la API (ver 7), su campo de referencia es `run_id`, y agregar
`decision_run_id` cambiaría el esquema de todos los errores por un caso. Meterla en el `mensaje`
obligaría al cliente a sacarla de un texto que puede cambiar. Así queda un solo camino para
encontrar la ejecución, el historial, que sirve igual después de un `409`, de un timeout o de un
reinicio.

**El historial.** `GET /corridas/{run_id}/decisiones` lista todas las ejecuciones de la corrida,
en cualquier estado y con cualquier versión de las reglas, de la más reciente a la más antigua;
el id interno solo desempata dos que empezaron en el mismo instante. Una corrida sin ejecuciones
devuelve la lista vacía, no un error: la corrida existe y nadie la ha decidido. Las decisiones
por cuenta, en cambio, solo existen para una ejecución `EXITOSA`: las de una `EN_PROCESO` o una
`FALLIDA` dan `409 DECISION_NO_PUBLICADA`, y no una lista vacía, que diría que no había cuentas;
igual que los rechazos de una corrida en proceso (ver 5). Su `total` sale de la tabla, no del
contador de la ejecución, y cada página sale de un JOIN con `cuenta`: tres consultas por
petición, sea cual sea el tamaño de la página.

**El vocabulario es texto.** El `segmento`, la `prioridad`, el `canal_recomendado` y el `codigo`
de cada motivo se guardan como texto, sin `CHECK`, y la API los sirve como `string`, sin enum.
El vocabulario es el de la versión de las reglas que decidió: si `decision/v2` agrega un
segmento o cambia un motivo, la tabla lo guarda sin migrarse, y la API sigue sirviendo las
decisiones de `decision/v1` junto a las de `decision/v2`. Un `CHECK` o un enum cerrado a
`decision/v1` obligaría a migrar con cada versión, o haría que la API dejara de servir la parte
del historial que no conoce. El catálogo cerrado vive donde se decide, en el núcleo:
`decision/v1` no puede producir un valor fuera del suyo. Una prueba guarda una decisión con un
vocabulario que `decision/v1` no conoce y exige que la API la sirva tal cual.

**El estado sí es un enum.** `EstadoDecision` se guarda con `CHECK` y la API lo publica como
enum, porque es estructural: no depende de la versión de las reglas, y de él dependen la
unicidad, la transacción y qué se publica. Una versión nueva de las reglas no trae estados
nuevos.

## 22. Qué queda para la orquestación, y qué no resuelve v0.2.0

**Lo que queda para la orquestación durable (v0.5.0).**

- **Las ejecuciones huérfanas.** Si el proceso muere a media ejecución, PostgreSQL revierte sus
  decisiones, pero la ejecución, confirmada en T0, queda `EN_PROCESO` y nadie la cierra. No
  bloquea otro intento, porque el índice solo cuenta las `EXITOSA`, pero el historial la sigue
  mostrando en proceso. No hay un plazo como el de las corridas abandonadas (ver 9) ni un estado
  para ellas: cerrarlas es reconciliación, con tiempos límite y reintentos, y le toca a quien
  orqueste el trabajo, no a un parche del motor.
- **La decisión fuera de la petición.** Un worker con cola que tome las ejecuciones
  `EN_PROCESO`, para que el tamaño de la cartera no dependa del timeout de un cliente (ver 20).
- **Reintentos, programación y límites.** Reintentar una `FALLIDA` sin que alguien vuelva a
  pedirlo, decidir cada cartera en cuanto se publica y limitar cuántas ejecuciones corren a la
  vez.

**Lo que v0.2.0 no hace.** Para que no se lea como más de lo que es, todavía no existen:

- un motor territorial que agrupe las cuentas por territorio;
- ruteo;
- la ejecución de estrategias de contacto: el motor recomienda un canal, pero nadie contacta a
  nadie;
- orquestación durable, scheduler, cola ni reintentos distribuidos;
- despliegue en nube;
- observabilidad productiva: hay bitácora, pero no métricas, trazas ni alertas.

v0.2.0 decide y explica cada decisión. No la ejecuta.

**Desde v0.5.0.** Las ejecuciones huérfanas, la decisión fuera de la petición, los reintentos y
decidir cada cartera en cuanto se publica son la orquestación durable (ver 42 a 53). Limitar
cuántas ejecuciones corren a la vez se hace con el número de workers; programarlas sigue
pendiente.

## 23. El territorio es un municipio

**Decisión.** En `territorial/v1`, un territorio es exactamente un municipio:
`cve_entidad + cve_municipio`, dos y tres dígitos ASCII como texto (`"09"` y `"002"` dan
`"09002"`). No hay regiones, zonas, colonias, códigos postales ni coordenadas, y no se guardan
nombres.

**Por qué.**

- **Es lo que la cartera trae.** `cartera/v1` exige las dos claves en cada cuenta. Un territorio
  más fino pediría datos que la cartera no tiene; uno más grueso, como la entidad, escondería
  justo la concentración que importa: casi toda la cartera sintética está en Puebla.
- **Es una unidad que la operación reconoce.** El trabajo de campo se reparte por municipios, y
  la clave del Marco Geoestadístico del INEGI es pública y estable.
- **La clave se deriva, no se guarda.** `ResultadoTerritorial` guarda `cve_entidad` y
  `cve_municipio`, y `clave_territorio` se arma al responder: una copia podría dejar de
  coincidir con sus partes.

**Forma, no catálogo.** El núcleo exige dos y tres dígitos ASCII, pero no que el municipio
exista: el proyecto todavía no tiene el catálogo del INEGI. `cartera/v1` acepta claves con dígitos
de otros alfabetos; `territorial/v1` no, y si un agregado no cumple la forma, la ejecución queda
`FALLIDA` sin publicar ningún municipio. Una prueba del servicio lo exige.

**Qué haría distinto.** Con el catálogo del INEGI, validaría las claves en el contrato y no en el
motor: una clave que no existe es un dato malo de la cartera, no un problema de organización.

## 24. Carga operativa, no riesgo crediticio

**Decisión.** La carga de un municipio sale solo de cuántas de sus cuentas tienen `CAMPO` como
canal recomendado: `SIN_CARGA` (0), `BAJA` (1 a 4), `MEDIA` (5 a 19) y `ALTA` (20 o más). Los
municipios con carga se ordenan por `cuentas_campo DESC`, `saldo_campo DESC` y
`clave_territorio ASC`, y reciben un lugar desde 1; los `SIN_CARGA` van al final, sin lugar.

**Por qué cuentas y no saldo.** La carga mide trabajo: cuántas cuentas hay que gestionar en
campo. El riesgo y el monto de cada cuenta ya los pesó `decision/v1` al recomendarle un canal
(ver 16): una cuenta llega a `CAMPO` por su atraso y su saldo. Volver a pesar el saldo en el
territorio lo contaría dos veces.

**El saldo solo desempata.** Entre dos municipios con las mismas cuentas de campo va primero el
de más saldo de campo: a igual trabajo, conviene empezar por donde hay más en juego. Pero nunca
pone delante a un municipio con menos cuentas de campo, y no cambia la carga.

**Sin puntaje.** El orden es lexicográfico, no un número que mezcle cuentas y pesos. Cada lugar
se explica con los valores que el resultado expone, y no hay pesos que calibrar ni que
justificar. Cada municipio trae un motivo, de un catálogo cerrado de cuatro códigos, con el
conteo que decidió su carga.

**Los `SIN_CARGA` también se publican.** Un municipio con cuentas y sin trabajo de campo sigue
siendo parte de la cartera, y si faltara en el resultado se confundiría con uno sin cuentas. Va
al final, por clave, con `posicion_campo` en `null`: no tiene lugar en un orden de trabajo de
campo, y un `0` diría otra cosa.

**Los umbrales son sintéticos.** `5` y `20` son demostrativos y propios de este proyecto público,
como el umbral de saldo de `decision/v1`: no son propietarios ni una recomendación real de
cobranza. Viven en el código y no en la configuración, por la misma razón que aquel (ver 16).

**Qué haría distinto.** Con datos reales de capacidad, como cuántas cuentas atiende un gestor al
día, los umbrales saldrían de ahí y no de números redondos, y la carga podría expresarse en días
de trabajo. Sería otra versión de las reglas.

## 25. Agregar en PostgreSQL antes de entrar al núcleo

**Decisión.** `territorial/ejecuciones.py` agrega las decisiones con una sola consulta: un
`GROUP BY cve_entidad, cve_municipio` sobre `decision_cuenta JOIN cuenta`, con `COUNT(*)` y
`SUM(saldo_total)` y, para lo que es de campo, `COUNT(*) FILTER` y `SUM(saldo_total) FILTER`
sobre `canal_recomendado = 'CAMPO'`, con `COALESCE` a `0`. A Python llega una fila por municipio,
que pasa por `EntradaTerritorio`; después `priorizar_territorios` los evalúa y ordena, una sola
vez.

**Por qué en la base.** Una cartera tiene miles de cuentas y unos cientos de municipios. Agregar
en Python obligaría a traer cada cuenta para quedarse con unas cuantas filas; la base hace la
suma sin mover los datos. Una prueba exige que la agregación sea una sola sentencia y que diez
veces más cuentas no cambien cuántas sentencias se ejecutan.

**El núcleo no agrega.** `territorial/reglas.py` recibe municipios ya agregados y no sabe de
cuentas, igual que `decision/reglas.py` no sabe de corridas (ver 16). Así se prueba sin base, con
golden tests en cada frontera y en cada desempate, y no puede volver a decidir una cuenta.
`EntradaTerritorio` es la barrera: valida tipos exactos y que los agregados sean consistentes
entre sí, y si uno no lo es, no se publica nada.

**Lo que cuenta como campo es la decisión.** El `FILTER` es sobre
`DecisionCuenta.canal_recomendado`, nunca sobre `Cuenta.canal`, que es el canal con que la cuenta
llegó en la cartera. Una prueba arma una cartera en la que los dos se contradicen y exige que
solo cuente el recomendado.

**Exacto.** Los saldos llegan como `Decimal`, sin pasar por `float`, y sin cuentas de campo la
suma es `0` y no `NULL`. Los municipios agregados tienen que sumar exactamente las decisiones de
la ejecución: un JOIN o un filtro que perdiera filas lo descubre esa suma antes de publicar.

**Qué haría distinto.** Con carteras de millones de cuentas, la consulta seguiría siendo una
sola, pero habría que medirla. Si pesara, se podría agregar al decidir, cuando cada cuenta ya
pasa por la memoria del Decision Engine.

## 26. territorial/v1 consume decision/v1

**Decisión.** `territorial/v1` solo organiza ejecuciones `EXITOSA` de `decision/v1`. La
compatibilidad es un literal, `VERSION_DECISION_COMPATIBLE = "decision/v1"`, y no se toma de
`VERSION_REGLAS_DECISION`. Con cualquier otra fuente, el servicio levanta
`DecisionNoTerritorializable` antes de registrar nada, y la API responde
`409 DECISION_NO_TERRITORIALIZABLE`.

**Por qué un literal.** Las reglas territoriales dependen del vocabulario de las decisiones: lo
que cuenta como campo es el canal `CAMPO` de `decision/v1`. Si `decision/v2` cambiara sus canales
o lo que quiere decir `CAMPO`, `territorial/v1` seguiría contando como antes y publicaría
resultados con otro sentido. Si la compatibilidad siguiera a `VERSION_REGLAS_DECISION`, el día que
el Decision Engine pasara a `decision/v2`, `territorial/v1` la aceptaría sola. Qué versión
territorial consume qué versión de decisión es parte del contrato histórico, y cambiarlo es otra
versión.

**Solo una `EXITOSA`.** Una ejecución `EN_PROCESO` o `FALLIDA` no publicó decisiones (ver 18), y
organizarla daría un territorial vacío o parcial. Por eso es un `409` y no un `201` con una
`FALLIDA`: no se cumple la precondición, y no hay ejecución territorial que registrar.

**Y completa.** Dentro de la transacción que publica, el servicio vuelve a revisar la fuente: lo
que la ejecución de decisión dice que evaluó y decidió, las decisiones que de verdad tiene y las
que son de cuentas de su propia corrida tienen que ser el mismo número, mayor que cero. La base
garantiza que cada decisión apunta a una cuenta, pero no que sea de esa corrida.

**Qué haría distinto.** Cuando exista `decision/v2`, la pregunta será si `territorial/v1` puede
consumirla sin cambiar ningún resultado posible. Si puede, la compatibilidad pasaría a ser un
conjunto de versiones; si no, haría falta `territorial/v2`.

## 27. Persistencia territorial e idempotencia

**Decisión.** Cada vez que se organizan unas decisiones se registra una `EjecucionTerritorial`:
de qué ejecución de decisión, con qué `version_reglas`, su estado (`EN_PROCESO`, `EXITOSA` o
`FALLIDA`), cuándo empezó y terminó, cuántos municipios evaluó y cuántos publicó. Cada municipio
publicado es un `ResultadoTerritorial`: sus cuatro agregados, su carga, su lugar y sus motivos en
JSONB.

**Cuelga de la ejecución de decisión, no de la corrida.** Una corrida se puede decidir con varias
versiones de las reglas, y un resultado territorial tiene que decir exactamente de qué decisiones
salió. La corrida, su `run_id` y la versión de decisión se obtienen siguiendo esa llave; no se
copian (ver 17). El identificador público es `territorial_run_id`: no se llama `run_id`, que es
el de la corrida, ni `decision_run_id`.

**Los agregados se guardan.** Además de la carga y el lugar, cada resultado guarda los conteos y
los saldos de los que salió, para volver a explicarlo sin rehacer la agregación. Los conteos son
`BIGINT` y los saldos `NUMERIC(24,2)`: suman muchas cuentas y no caben en el `NUMERIC(14,2)` de una
sola.

**Idempotencia en dos niveles, como en el Decision Engine (ver 19).** La revisión previa en
`abrir_ejecucion` es la vía amable: si esas decisiones ya tienen una ejecución `EXITOSA` con
`territorial/v1`, levanta `TerritorialYaGenerado` y no registra nada. La garantía es el índice
único parcial `ux_ejecucion_territorial_exitosa` sobre `(ejecucion_decision_id, version_reglas)`,
que solo cuenta las `EXITOSA`. Si dos ejecuciones compiten, la que cierra segunda choca con él:
revierte sus resultados, queda `FALLIDA` y levanta `TerritorialYaGenerado` con la que ganó. La
restricción se reconoce por su nombre, `diag.constraint_name`, y no por el texto del error. Una
`FALLIDA` se reintenta con otra ejecución, y `territorial/v2` podrá organizar las mismas
decisiones sin chocar con `territorial/v1`.

**Cada municipio y cada lugar, una vez.** `uq_resultado_territorial_municipio` impide publicar un
municipio dos veces en la misma ejecución, y `ux_resultado_territorial_posicion_campo`, que dos
municipios compartan lugar. Los `SIN_CARGA` tienen el lugar en `NULL`, y de esos puede haber
muchos.

**Vocabulario sin `CHECK`.** La carga y el código de cada motivo se guardan como texto, igual que
las decisiones (ver 21): una versión nueva de las reglas trae su vocabulario sin migrar la tabla.
El estado sí lleva `CHECK`, porque es estructural.

**Nombres explícitos.** Las llaves foráneas y la restricción única llevan nombre propio
(`fk_ejecucion_territorial_decision`, `fk_resultado_territorial_ejecucion` y
`uq_resultado_territorial_municipio`): los de la convención pasarían de los 63 caracteres de
PostgreSQL, y la base guardaría unos recortados que no coincidirían con los del modelo. Sin
cascada, como en el Decision Engine: borrar una ejecución con resultados falla en lugar de
llevarse la evidencia.

**Qué haría distinto.** `resultado_territorial` crece con cada ejecución, como `decision_cuenta`,
pero mucho menos: una fila por municipio, no por cuenta. Antes de particionarla, archivaría las
ejecuciones viejas.

## 28. Publicación todo-o-nada

**Decisión.** Como en el Decision Engine (ver 18), una ejecución territorial corre en dos
transacciones:

- **T0.** Revisa la fuente, registra la ejecución `EN_PROCESO` y hace `COMMIT` antes de calcular
  nada.
- **T1.** Toma la ejecución con `SELECT ... FOR UPDATE`, vuelve a revisar la fuente y que esté
  completa, agrega en PostgreSQL, aplica `territorial/v1`, inserta todos los municipios en un
  solo `INSERT`, comprueba que estén completos y cierra la ejecución `EXITOSA`. Un solo `COMMIT`
  publica todo.

Si algo falla en T1, el `ROLLBACK` se lleva los resultados del intento, y en otra transacción la
ejecución queda `FALLIDA`, con `territorios_publicados = 0` y el motivo en `detalle`, sin trazas.

**Completos antes de publicar.** Los municipios agregados, los evaluados por el núcleo y las
filas guardadas tienen que ser el mismo número, mayor que cero, y sumar exactamente las
decisiones de la fuente, contados dentro de la misma transacción. Unas decisiones que no dan
ningún municipio no se publican con un éxito vacío.

**`territorios_evaluados` es evidencia.** En una `FALLIDA` dice hasta dónde llegó el núcleo,
aunque no se haya publicado nada; `territorios_publicados` es cero.

**Un worker por ejecución, y un fallo tardío no degrada.** Con el `FOR UPDATE`, un segundo worker
con la misma ejecución espera y la encuentra terminada. La transición a `FALLIDA` es un `UPDATE`
condicionado a que la ejecución siga `EN_PROCESO` en la base: si otro worker ya la dejó `EXITOSA`,
el fallo queda solo en la bitácora (ver 19).

**Un solo `INSERT`.** Los municipios se insertan en bloque, con el lugar de los `SIN_CARGA`
escrito como `NULL`. Sin eso, el ORM omite las columnas en `None` y parte el bloque en dos
sentencias; lo descubrió la prueba que cuenta sentencias.

**Qué haría distinto.** Aquí la transacción es corta: unos cientos de filas. Si el territorio se
volviera más fino que el municipio, revisaría el tamaño del `INSERT` antes que la forma de
publicar.

## 29. API e historial territorial

**Decisión.** Cuatro operaciones, con las mismas reglas que las del Decision Engine (ver 20 y
21):

- `POST /decisiones/{decision_run_id}/territoriales` organiza en la misma petición y responde
  `201` con `Location: /territoriales/{territorial_run_id}`, termine `EXITOSA` o `FALLIDA` (D1).
- `GET /decisiones/{decision_run_id}/territoriales` es el historial: todas las ejecuciones
  territoriales de esas decisiones, en cualquier estado y versión, de la más reciente a la más
  antigua; el id interno solo desempata dos que empezaron en el mismo instante.
- `GET /territoriales/{territorial_run_id}` es una ejecución, con el `decision_run_id` de sus
  decisiones y el `run_id` de su corrida, por JOIN.
- `GET /territoriales/{territorial_run_id}/municipios` son los municipios que publicó.

**Síncrono, como decidir.** Organizar cuesta menos que decidir: la base agrega y al núcleo llega
una fila por municipio. El POST busca la ejecución de decisión, copia su id interno y el `run_id`
de su corrida, y termina la transacción de la sesión HTTP antes de llamar al servicio: no retiene
una conexión mientras organiza ni deja un objeto del ORM que pueda caducar. Una prueba exige que
esa transacción haya terminado al entrar al servicio.

**D2: `409` sin `Location`.** Si esas decisiones ya se organizaron con éxito con `territorial/v1`,
el POST responde `409 TERRITORIAL_YA_GENERADO`, también cuando lo detecta el índice en una
carrera. No nombra la ejecución, por las mismas razones que el `409` de las decisiones (ver 21):
se descubre en el historial.

**Errores.** Los nuevos usan `ErrorRespuesta` sin cambiarla: `DECISION_NO_TERRITORIALIZABLE`,
`TERRITORIAL_YA_GENERADO`, `TERRITORIAL_NO_ENCONTRADO` y `TERRITORIAL_NO_PUBLICADO`, y se
reutiliza `DECISION_NO_ENCONTRADA`. Su `run_id` sigue siendo el de la corrida cuando se conoce:
convertirlo en `decision_run_id` o en `territorial_run_id` cambiaría lo que el campo quiere decir
en toda la API.

**Municipios, solo de una `EXITOSA`.** Los de una `EN_PROCESO` o una `FALLIDA` dan
`409 TERRITORIAL_NO_PUBLICADO`, y no una lista vacía, que diría que no había municipios. Se
sirven en el orden de `territorial/v1`: `posicion_campo ASC NULLS LAST` y, entre los que no
tienen lugar, por clave. El `total` es un `COUNT` de la tabla, no el contador de la ejecución, y
cada página son tres consultas fijas, sin N+1. La paginación es la de siempre, con `OFFSET`
(ver 8): una ejecución `EXITOSA` ya no cambia, así que las páginas no saltan ni repiten
municipios.

**Vocabulario histórico.** `carga` y `codigo` viajan como `string`, sin enum, igual que el
vocabulario de las decisiones (ver 21): la API sirve un `CRITICA` o un `REGLA_TERRITORIAL_FUTURA`
que guardó otra versión de las reglas, y una prueba lo exige.

**Qué haría distinto.** Si organizar dejara de ser barato, el POST pasaría a ser asíncrono como
se describe en la sección 20, sin cambiar lo que el `201` quiere decir.

**Desde v0.5.0.** Es asíncrono, como el de las decisiones: `201` con la ejecución `EN_PROCESO`, y
la organiza un worker (ver 51).

## 30. Por qué v0.3 no es ruteo

**Decisión.** v0.3.0 responde dónde se concentra la carga operativa de campo y qué municipios
conviene atender primero. No responde qué ruta física seguir, en qué secuencia visitar las
cuentas ni cuánto se tarda: eso es v0.4.0, el Motor de Ruteo.

**Por qué separarlos.** Son problemas distintos, con datos distintos. Priorizar municipios solo
necesita lo que la cartera y las decisiones ya tienen. Rutear necesita coordenadas, distancias y
tiempos entre puntos, gestores con su capacidad y sus horarios, y un algoritmo de ruteo (TSP o
VRP) con sus aproximaciones. Mezclarlos haría que el orden de los municipios dependiera de datos
que el proyecto todavía no tiene.

**Lo que v0.3.0 no tiene:** coordenadas, distancias, TSP, VRP, gestores ni rutas. `posicion_campo`
es una prioridad entre municipios, no una parada en un recorrido.

**Lo que queda para la orquestación durable (v0.5.0).** Lo mismo que en el Decision Engine (ver
22): reconciliar una ejecución territorial que quedó `EN_PROCESO` porque su proceso murió después
de T0, sacar el trabajo de la petición HTTP a un worker con cola, y organizar cada ejecución de
decisión en cuanto se publica. v0.5.0 lo resolvió (ver 42 a 53).

v0.3.0 organiza el trabajo de campo y explica cada lugar. No lo recorre.

v0.4.0 traza esa secuencia dentro de cada municipio, sin los datos que aquí faltaban: sobre un
plano sintético, sin gestores ni tiempos (ver 31 a 41).

## 31. Por qué ruteo usa un plano sintético

**Decisión.** `ruteo/v1` no usa geografía real. Cada municipio tiene su propio plano operativo
sintético: un cuadrado de 10 km por lado, con coordenadas enteras de −5000 a +5000 metros y el
depósito en el centro, `(0, 0)`. Cada cuenta de campo recibe un punto de ese plano.

**Por qué.** La cartera no trae latitud, longitud, dirección ni código postal, y el proyecto tiene
una regla dura: ningún dato real entra al repositorio. Había tres caminos:

- **Inventar latitudes y longitudes** dentro de cada municipio: parecerían ubicaciones reales y no
  lo serían. Un mapa con esos puntos sería engañoso.
- **Geocodificar** con un servicio externo (Google Maps, Mapbox, OpenStreetMap, el INEGI): pediría
  direcciones que no existen, y metería red, cuotas y licencias en un cálculo que tiene que ser
  reproducible.
- **Un plano sintético que se declara como tal**: no pretende ser geografía, se calcula sin red y
  da siempre la misma ruta.

Se eligió el tercero. Las rutas existen para demostrar la arquitectura, la optimización, la
trazabilidad, las transacciones, la API y las pruebas, no para mandar a nadie a una calle.

**Lo que no son.** Las coordenadas no son latitud ni longitud ni domicilios; las distancias no son
calles, tráfico ni tiempos de conducción; y dos municipios no se comparan: cada plano es local. La
documentación de la API y el README lo dicen donde aparecen.

**Qué haría distinto.** Con ubicaciones reales y permitidas, el plano se cambiaría por coordenadas
reales y la métrica por distancias o tiempos sobre una red de calles. Sería `ruteo/v2`, y
probablemente otro contrato de cartera.

## 32. Coordenadas deterministas con SHA-256

**Decisión.** El punto de una cuenta sale de SHA-256 sobre
`"ruteo/v1|{clave_territorio}|{cliente_unico}"` en ASCII: los primeros 8 bytes del digest, como
entero sin signo big-endian, dan `x_m`, y los 8 siguientes, `y_m`; cada uno `% 10001 - 5000`. Son
siempre enteros.

**Por qué un hash y no el azar.** Un generador aleatorio, aun con semilla, haría depender el punto
del orden en que llegan las cuentas y de la implementación del generador; el `hash()` de Python
cambia en cada proceso. SHA-256 da el mismo punto en cualquier máquina, proceso y orden, sin estado
ni configuración. Una prueba rutea en procesos con otras semillas de hash, con los clientes en un
`frozenset`, y exige la misma ruta.

**Por qué esos tres campos.** La versión, para que `ruteo/v2` mueva todos los puntos sin
heredarlos de `v1`; el municipio, para que el mismo cliente tenga otro punto en otro municipio: no
hay un punto global del cliente, porque no hay geografía; y el cliente, que identifica la cuenta
dentro de su corrida.

**Uniformidad.** `2^64` no es múltiplo de 10,001: el módulo tiene un sesgo del orden de uno en
`10^15`, que en un plano sintético no importa.

**Pruebas.** Los golden de las coordenadas se calcularon aparte y se cruzaron con `sha256sum`. Un
mutation check sobre copias temporales confirma que usar `hash()`, cambiar `10001` por `10000` u
olvidar el municipio en el payload rompe pruebas.

## 33. Manhattan como métrica exacta

**Decisión.** La distancia entre dos puntos es `|x1 − x2| + |y1 − y2|`, en metros sintéticos
enteros.

**Por qué.** Con enteros es exacta: sin `float`, raíz cuadrada ni redondeo, la misma en cualquier
plataforma. Las comparaciones del vecino más cercano y del 2-opt no dependen de errores de
redondeo, y un empate es un empate de verdad, que resuelve el desempate. Conceptualmente es un
recorrido sobre una cuadrícula de calles, que es lo más que un plano sintético puede decir.
Haversine supone latitudes y longitudes, que no hay; una euclidiana en `float` traería redondeos; el
tráfico y los tiempos necesitan datos que no existen.

**Simetría.** Manhattan es simétrica, y el 2-opt lo aprovecha: invertir un tramo deja sus aristas
internas con la misma longitud, así que el ahorro sale de los dos bordes que cambian.

## 34. Vecino más cercano como construcción inicial

**Decisión.** La ruta inicial sale del depósito y va siempre a la cuenta pendiente más cercana a
donde está; a igual distancia, a la de `cliente_unico` menor. El depósito no es una parada, pero la
salida y el regreso cuentan en la distancia.

**Por qué.** Es simple, explicable y determinista, y da una ruta razonable en `O(n²)`. Como los
clientes no se repiten, el desempate siempre decide, y la ruta no depende del orden de llegada: una
prueba recorre las 120 formas de ordenar cinco clientes y exige la misma ruta.

**Lo que no es.** No es óptimo: puede dejar una cuenta lejana para el final y cruzar su propio
camino. Por eso le sigue el 2-opt, y su distancia se guarda como `distancia_inicial_m`, para que se
vea cuánto se mejoró.

## 35. 2-opt determinista y acotado

**Decisión.** Después del vecino más cercano, cada pasada evalúa todas las inversiones de un tramo
`i..j` con solo los dos bordes que cambian (`anterior → i` y `j → siguiente` frente a
`anterior → j` e `i → siguiente`) y aplica una sola: la de mayor ahorro estrictamente positivo; a
igual ahorro, la de `i` menor y después la de `j` menor. Se detiene cuando ninguna acorta la ruta o
con `MAX_PASADAS_2OPT = 10` inversiones aplicadas.

**Por qué la mejor y no la primera.** Elegir la mejor inversión de cada pasada cuesta más, pero
cada paso se explica solo: era la que más ahorraba. Y con el desempate explícito, el resultado no
depende del orden en que se recorren las inversiones.

**Por qué un tope fijo.** Un límite de tiempo daría otra ruta en una máquina más lenta. Diez
inversiones acotan el trabajo sin mirar el reloj, y el tope vive en el código y no en la
configuración: cambiarlo cambia rutas, así que es parte de la versión. Con la cartera por omisión,
el 2-opt acorta 119 de las 403 rutas, y 10 llegan al tope.

**Sin heurísticas al azar.** Ni recocido simulado, ni algoritmos genéticos, ni OR-Tools: podrían dar
rutas más cortas, pero no la misma ruta siempre sin fijar semillas, y meterían dependencias para un
problema sintético. El núcleo usa solo la biblioteca estándar.

**Garantías.** Cada inversión aplicada acorta estrictamente, así que `distancia_total_m` nunca pasa
de `distancia_inicial_m` y `mejora_2opt_m` nunca es negativa. Las pruebas comparan la elección con
la fuerza bruta en 400 rutas al azar, y fijan una ruta de 30 clientes que se detiene en la décima
inversión aunque la undécima todavía ahorraría.

**Qué haría distinto.** Con rutas de miles de paradas, cada pasada `O(n²)` pesaría; usaría listas
de vecinos cercanos u Or-opt, en otra versión de las reglas.

## 36. Una ruta por municipio

**Decisión.** Cada municipio con cuentas de campo (`cuentas_campo > 0` y `posicion_campo` no nulo)
tiene exactamente una ruta, con su propio depósito y su propio plano. Los `SIN_CARGA` no tienen
ruta, y el motor no conecta municipios entre sí.

**Por qué.** v0.3.0 ya decidió qué municipio va primero, `posicion_campo`; v0.4.0 decide en qué
orden se visitan las cuentas dentro de cada uno, `secuencia`. Son preguntas distintas y siguen
separadas: `posicion_campo` es una prioridad territorial, no un orden de visita. Unir municipios
exigiría distancias entre planos que, por ser sintéticos y locales, no existen.

**Sin gestores.** No hay gestores, vehículos, capacidades, turnos, horarios ni ventanas de tiempo:
la cartera no los trae, e inventarlos sería decidir con datos que no existen. Una ruta es la
secuencia de visita sobre todas las cuentas de campo de un municipio, no la jornada de una persona,
y el problema no es un VRP multi-vehículo.

## 37. Persistencia de rutas y paradas

**Decisión.** Tres tablas nuevas, en la migración `0005`:

- `ejecucion_ruteo`: cada ejecución, colgada de la `EjecucionTerritorial` que ruteó, con
  `ruteo_run_id` público, `version_reglas`, su estado (`EN_PROCESO`, `EXITOSA` o `FALLIDA`, VARCHAR
  con CHECK), sus tiempos y sus contadores en `BIGINT`;
- `ruta_territorial`: la ruta de un municipio, que apunta a su `ResultadoTerritorial` y guarda sus
  paradas y sus distancias;
- `parada_ruta`: una cuenta en una ruta, que apunta a la ejecución, a la ruta y a la
  `DecisionCuenta`, con su secuencia, su punto y la distancia desde la anterior.

**Cuelga de la territorial.** Unas decisiones se pueden organizar con varias versiones de las
reglas territoriales; una ruta tiene que decir exactamente de qué organización salió. La ejecución
de decisión y la corrida se obtienen siguiendo las llaves (ver 27).

**No se copia.** Ni `cliente_unico` ni `clave_territorio` se guardan: se leen por JOIN de la cuenta
y del resultado territorial. Tampoco el algoritmo ni la métrica: son lo que `ruteo/v1` significa.
No hay CHECK atados al algoritmo, como uno sobre el rango de las coordenadas: eso lo cuida el
núcleo, y otra versión podría usar otro plano sin migrar las tablas.

**`ejecucion_ruteo_id`, repetido a propósito.** `parada_ruta` repite la ejecución aunque se llegue
a ella por la ruta: así `uq_parada_ruteo_decision` garantiza en la base que una decisión aparece a
lo más una vez en toda la ejecución, aunque tenga cientos de rutas. Otra ejecución sí puede volver
a rutear la misma decisión.

**Restricciones e índices.** `uq_ruta_ruteo_territorio`, una ruta por municipio y ejecución, y
`uq_parada_ruta_secuencia`, cada lugar de una sola parada, que además sirve para leerlas en orden.
Las llaves no tienen cascada. Las restricciones compuestas empiezan por la columna con que se
consulta, así que no hay índices sueltos redundantes; solo `ejecucion_territorial_id` lleva el
suyo, para el historial.

**Nombres medidos.** Cada nombre se midió contra los 63 caracteres de PostgreSQL antes de aceptar
la convención. Las dos llaves que la convención dejaría largas (65 y 66 caracteres) y la única de
rutas, que quedaría justo en 63, llevan nombre corto propio: no se repite el recorte que obligó a
nombrar a mano las restricciones de la `0004`.

## 38. Completitud y publicación todo-o-nada

**Decisión.** Como en los otros motores (ver 18 y 28), dos transacciones:

- **T0.** Revisa la cadena y registra la ejecución `EN_PROCESO`, con `COMMIT`, antes de calcular
  nada.
- **T1.** La toma con `SELECT ... FOR UPDATE`, vuelve a revisar la cadena completa (la territorial
  `EXITOSA` de `territorial/v1` y sus decisiones `EXITOSA` de `decision/v1`) y que la fuente esté
  completa y cuadre; lee en una sola consulta las cuentas de campo; llama al núcleo una vez por
  municipio, en `posicion_campo ASC`; inserta las rutas en bloque, con `RETURNING`, y después las
  paradas en bloque; comprueba que todo esté completo y cierra `EXITOSA`. Un solo `COMMIT` publica
  todo.

**Antes de calcular.** La territorial tiene publicados los municipios que dice, y al menos uno;
cada municipio es coherente, con cuentas de campo y lugar o sin ninguno de los dos; hay al menos un
municipio con campo; `sum(cuentas_campo)` es igual a las decisiones `CAMPO` reales y a las de
cuentas de su propia corrida; cada cuenta de campo es de un municipio con ruta y cada municipio
tiene exactamente las suyas. Lo que cuenta como campo es `DecisionCuenta.canal_recomendado`, nunca
`Cuenta.canal`.

**Antes de publicar.** Sin volver a calcular nada: cada ruta es de su municipio, tiene exactamente
sus cuentas, una vez cada una y en secuencia desde 1; la distancia final no pasa de la inicial; la
mejora es la diferencia; y los tramos más el regreso suman el total. Y antes del `COMMIT`, las rutas
calculadas, las guardadas y los municipios con campo son el mismo número, como las paradas
calculadas, las guardadas y las decisiones de campo, todos mayores que cero.

**Si algo falla.** Se revierte todo, y en otra transacción la ejecución queda `FALLIDA`, con cero
rutas y cero paradas publicadas y el motivo en `detalle`. `rutas_evaluadas` y `paradas_evaluadas`
solo dicen algo si el núcleo terminó todas las rutas: no se inventan avances a medias.

## 39. Concurrencia e idempotencia del ruteo

**Decisión.** La idempotencia tiene dos niveles, como en los otros motores (ver 19 y 27): la
revisión amable de `abrir_ejecucion` levanta `RuteoYaGenerado` si la ejecución territorial ya tiene
una ejecución `EXITOSA` de `ruteo/v1`; la garantía es `ux_ejecucion_ruteo_exitosa`, índice único
parcial sobre `(ejecucion_territorial_id, version_reglas)` que solo cuenta las `EXITOSA`.

**Carreras.** El mismo `ejecucion_ruteo_id` lo procesa un solo worker: el `FOR UPDATE` serializa, y
el segundo la encuentra terminada y no hace nada. Dos ejecuciones distintas de la misma fuente
pueden trabajar a la vez, pero solo una cierra: la segunda choca con el índice, que se reconoce por
`diag.constraint_name`, revierte, queda `FALLIDA` y levanta `RuteoYaGenerado` con la ganadora. Un
fallo que llega tarde no degrada una `EXITOSA`: la transición a `FALLIDA` es un `UPDATE`
condicionado a `EN_PROCESO`.

**Versiones.** Una `FALLIDA` se reintenta con otra ejecución, y `ruteo/v2` podrá rutear la misma
territorial sin chocar con `ruteo/v1`.

## 40. API e historial de ruteo

**Decisión.** Cinco operaciones, con las reglas de las demás (ver 20, 21 y 29):

- `POST /territoriales/{territorial_run_id}/ruteos` rutea en la misma petición y responde `201` con
  `Location: /ruteos/{ruteo_run_id}`, termine `EXITOSA` o `FALLIDA` (D1); `409 RUTEO_YA_GENERADO`
  sin `Location` (D2), también si pierde la carrera; y `409 TERRITORIAL_NO_RUTEABLE`, sin
  registrar nada, si la cadena no se puede rutear.
- `GET /territoriales/{territorial_run_id}/ruteos` es el historial, en cualquier estado y versión,
  de la más reciente a la más antigua.
- `GET /ruteos/{ruteo_run_id}` es una ejecución, con los identificadores públicos de toda su
  cadena.
- `GET /ruteos/{ruteo_run_id}/rutas` son las rutas de una `EXITOSA`, en `posicion_campo ASC`; de
  otra, `409 RUTEO_NO_PUBLICADO`.
- `GET /ruteos/{ruteo_run_id}/rutas/{clave_territorio}/paradas` son las paradas de un municipio,
  en `secuencia ASC`; `404 RUTA_NO_ENCONTRADA` si el municipio no tiene ruta.

**Síncrono.** El POST busca la ejecución territorial, copia lo que necesita, termina la transacción
de la sesión HTTP y llama al servicio: no retiene una conexión mientras rutea. Con la cartera por
omisión, trazar 403 rutas con 2,968 paradas toma menos de un segundo de cálculo. *Desde v0.5.0*
es asíncrono: `201` con la ejecución `EN_PROCESO`, y la traza un worker (ver 51).

**Sin N+1.** El `total` es un `COUNT` de la tabla, no el contador de la ejecución, y cada página son
consultas fijas: tres para las rutas y cuatro para las paradas, sea del tamaño que sea.

**Errores.** Los nuevos usan `ErrorRespuesta` sin cambiarla: `TERRITORIAL_NO_RUTEABLE`,
`RUTEO_YA_GENERADO`, `RUTEO_NO_ENCONTRADO`, `RUTEO_NO_PUBLICADO` y `RUTA_NO_ENCONTRADA`, y se
reutiliza `TERRITORIAL_NO_ENCONTRADO`. Su `run_id` sigue siendo el de la corrida.

**Lo sintético, dicho en la API.** La descripción de cada operación, y la de cada campo con
coordenadas o distancias, dice que son metros de un plano sintético por municipio, no latitud,
longitud, calles ni tiempos.

## 41. Qué no resuelve v0.4.0

- geografía real: geocodificación, calles, tráfico ni tiempos de conducción;
- gestores, vehículos, capacidades, turnos ni ventanas de tiempo: no es un VRP multi-vehículo;
- rutas que crucen municipios;
- orquestación durable: el POST es síncrono, y una ejecución que queda `EN_PROCESO` porque su
  proceso murió después de T0 no la cierra nadie hasta v0.5.0, que lo resolvió (ver 42 a 53);
- despliegue en nube y observabilidad productiva: v0.6.0.

**Desde v0.6.0.** El despliegue en nube y la observabilidad productiva pasaron a v0.18; v0.6.0 fue
de las fuentes oficiales (ver 54 a 70).

v0.4.0 secuencia las visitas de campo dentro de cada municipio, de forma reproducible y explicable,
sobre un plano que se declara sintético. No manda a nadie a una dirección.

## 42. Por qué PostgreSQL es la cola de v0.5

**Decisión.** La cola durable es una tabla de PostgreSQL, `trabajo_orquestacion`, en la misma base
que los recursos. Un trabajo dice qué motor ejecutar (`INGESTA`, `DECISION`, `TERRITORIAL` o
`RUTEO`) sobre qué recurso, y un recurso tiene a lo más un trabajo: lo garantizan los únicos
`uq_trabajo_corrida`, `uq_trabajo_decision`, `uq_trabajo_territorial` y `uq_trabajo_ruteo`. Un
reintento usa la misma fila, con un intento más; otra ejecución, como la de reanudar (ver 50), es
otro recurso con su propio trabajo.

**Por qué.** Lo que más importa es que el recurso y su trabajo nazcan juntos. Con la cola en la
misma base, `POST /corridas` crea la corrida, su archivo, su flujo y el trabajo de su ingesta en una
sola transacción: o existen los cuatro, o ninguno. Con un broker aparte habría dos escrituras en dos
sistemas, y un proceso que muere entre una y otra deja un recurso sin trabajo o un trabajo sin
recurso; evitarlo pide un *outbox* que, al final, es esta misma tabla. Además, PostgreSQL ya está en
el compose, en el CI y en las pruebas; `FOR UPDATE SKIP LOCKED` reparte el trabajo entre varios
workers sin que se estorben (ver 45), y su reloj sirve de reloj común para los leases (ver 46). Y el
volumen es chico: un trabajo por etapa de cada corrida, no millones de mensajes por segundo.

**Lo que cuesta.** El worker pregunta por trabajo cada `MC_WORKER_POLL_SEGUNDOS` (medio segundo por
omisión), así que un trabajo puede esperar eso antes de que alguien lo tome, y cada pregunta es una
consulta a la misma base que atiende a la API. No hay prioridades: se toma el de menor id.

**Qué haría distinto.** Con mucho más volumen, `LISTEN/NOTIFY` le avisaría al worker en lugar de
hacerlo preguntar. Con muchos tipos de trabajo, consumidores en otros servicios o prioridades, un
broker dedicado (ver 52).

## 43. Persistir el archivo antes de responder

**Decisión.** `POST /corridas` guarda el archivo, tal como llegó, en `archivo_corrida` (`BYTEA`), en
la misma transacción que la corrida, su flujo y su trabajo, y solo después responde `201`. El worker
lo lee de ahí. Dos `CHECK` cuidan que no se guarde a medias: el tamaño es positivo
(`ck_archivo_tamano_positivo`) y es exactamente el de los bytes guardados
(`ck_archivo_tamano_exacto`).

**Por qué.** Hasta v0.4.0 el archivo vivía en la memoria de la API mientras `BackgroundTasks` lo
procesaba (ver 11): si la API se reiniciaba después de responder, el archivo se perdía y la corrida
quedaba `EN_PROCESO` para siempre. Ahora, cuando el cliente recibe el `201`, todo lo que la ingesta
necesita ya está confirmado en PostgreSQL, y le da igual qué proceso muera después.

**Se borra al terminar la ingesta.** En la misma transacción que cierra su trabajo, tanto si la
corrida terminó como si su trabajo agotó los intentos. El archivo no es la evidencia: la evidencia
es la corrida, con la firma del archivo y la de su contenido, sus cuentas y sus rechazos. Guardarlo
para siempre llenaría la base de copias de lo que ya se juzgó.

**El CLI también pasa por la cola.** `motor-cartera cargar` registra la corrida, su archivo y su
trabajo igual que la API, pero sin flujo, con el trabajo ya tomado por el propio comando y su lease,
y lo ejecuta en primer plano. Si se interrumpe, el trabajo queda en la cola y un worker lo termina
cuando vence el lease.

**Qué haría distinto.** Es una decisión del alcance actual: el tope lo pone `MC_TAMANO_MAXIMO_MB`, y
PostgreSQL no es un almacén de objetos. En la nube (v0.6.0), el archivo iría a un *object storage* y
la base guardaría solo su referencia.

**Desde v0.6.0 esta decisión se corrige: el archivo original ahora SÍ forma parte de la evidencia
reproducible** (ver 54). Ya no se guarda en `archivo_corrida` ni se borra al terminar la ingesta: se
copia, antes de responder, a un almacén por contenido, donde se queda para siempre, y la corrida
apunta a él (ver 55 a 57). La corrida y sus resultados siguen siendo evidencia, pero ya no son
suficientes para reproducir exactamente el input. `archivo_corrida` solo conserva el archivo de las
corridas que la v0.5 dejó en la cola, para que el worker las termine. El CLI sigue pasando por la
cola, igual que la API.

## 44. At-least-once y no exactly-once

**Decisión.** La entrega de un trabajo es **al menos una vez**: un worker puede ejecutar el motor
de un recurso que otro worker ya ejecutó. No se afirma, ni se intenta, entregar exactamente una vez.

**Por qué no se puede prometer más.** El motor confirma su trabajo en sus propias transacciones (ver
18, 28 y 38), y el trabajo de la cola se cierra en otra. Si el worker muere entre las dos, el motor
ya publicó y el trabajo sigue `EJECUTANDO`. Cuando vence el lease, otro worker lo toma y llama otra
vez al motor. Para entregarlo exactamente una vez, el cierre del trabajo y la publicación del motor
tendrían que ser la misma transacción, y la cola tendría que conocer las transacciones de cada
motor.

**Por qué es seguro.** Lo que no se repite es la publicación, no la entrega. Cada motor ya se
protegía de dos ejecuciones (ver 19, 27 y 39), y v0.5.0 se apoya en eso:

- **bloqueos:** el motor toma su recurso con `SELECT ... FOR UPDATE`, así que dos ejecuciones del
  mismo recurso se serializan;
- **estados terminales:** un recurso `EXITOSA`, `FALLIDA` o `RECHAZADA` no se vuelve a ejecutar; el
  motor lo ve terminado y no hace nada;
- **transacciones:** cada motor publica todo o nada, en una sola transacción;
- **índices únicos:** a lo más una `EXITOSA` y a lo más una `EN_PROCESO` por fuente y versión;
- **idempotencia:** un fallo que llega tarde es un `UPDATE` condicionado a `EN_PROCESO`, que no
  degrada un estado terminal.

Una prueba mata al worker justo después del `COMMIT` de una decisión: otro worker toma el mismo
trabajo, el motor no hace nada, el trabajo queda `COMPLETADO`, el flujo avanza y no se duplica
ninguna decisión.

**Sin hueco entre etapas.** Cerrar el trabajo de una etapa y abrir la siguiente (su ejecución
`EN_PROCESO` y su trabajo) es una sola transacción. O el trabajo quedó `COMPLETADO` y la etapa
siguiente existe, o ninguna de las dos cosas.

## 45. Claim con FOR UPDATE SKIP LOCKED

**Decisión.** Un worker toma un trabajo con una sola consulta:
`SELECT ... WHERE <se puede tomar> ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED`. Se puede tomar un
trabajo `PENDIENTE` cuyo `disponible_desde` ya pasó, o uno `EJECUTANDO` cuyo lease venció. En la
misma transacción lo pasa a `EJECUTANDO` con su `worker_id`, un intento más, `tomado_en` y
`latido_en` en `now()` y `lease_hasta` en `now()` más el lease, y confirma **antes de ejecutar
nada**: si el worker muere después, la base ya sabe que lo tenía y hasta cuándo.

**Por qué.** `SKIP LOCKED` salta las filas que otra transacción tiene bloqueadas en lugar de
esperarlas. Dos workers que preguntan en el mismo instante nunca toman la misma fila, y ninguno
espera al otro. Un índice parcial, `ix_trabajo_reclamable`, cubre solo los trabajos que todavía se
pueden tomar, `PENDIENTE` o `EJECUTANDO`; los terminados, que son casi todos, no le cuestan nada a
la consulta.

**Un trabajo, un dueño.** Un `CHECK`, `ck_trabajo_lease`, exige que solo un trabajo `EJECUTANDO`
tenga `worker_id` y lease, y que solo uno terminado tenga `terminado_en`. Otro,
`ck_trabajo_objetivo`, que apunte exactamente al recurso de su tipo.

Las pruebas lo hacen contra PostgreSQL real: una fila bloqueada por otra sesión se salta sin
esperar, y dos workers soltados a la vez por una barrera toman cada uno un trabajo distinto, o uno
solo si hay uno solo. Otra pone a dos workers de verdad sobre dos flujos y exige que ningún trabajo
se ejecute dos veces.

**Desde v0.7.0.** El orden ya no es solo por id: entre los que se pueden tomar, primero los
operacionales y al final los `HISTORIA` (ver 80).

**Desde v0.8.0.** Son tres niveles: los operacionales, los `HISTORIA` y, al final, los
`MOTOR_PAGOS` (ver 96).

## 46. Lease y heartbeat

**Decisión.** Un trabajo tomado es de su worker mientras dure su lease, `MC_WORKER_LEASE_SEGUNDOS`
(60 s por omisión). Mientras ejecuta el motor, un hilo del worker late cada
`MC_WORKER_HEARTBEAT_SEGUNDOS` (20 s): renueva `latido_en` y `lease_hasta`, con un `UPDATE`
condicionado a que el trabajo siga siendo suyo y siga `EJECUTANDO`.

```
PENDIENTE ──(se toma)──▶ EJECUTANDO ──(latido)──▶ EJECUTANDO ──(su recurso terminó)──▶ COMPLETADO
    ▲                        │
    └──(error, con espera)───┤
                             └──(sin intentos)──▶ FALLIDO
```

**El reloj es el de PostgreSQL.** El lease, el latido y la disponibilidad se escriben y se comparan
con `now()` de la base, nunca con el reloj de un worker: dos máquinas con relojes desfasados ven el
mismo vencimiento.

**El latido tiene que caber en el lease.** La configuración no arranca si
`MC_WORKER_HEARTBEAT_SEGUNDOS` no es menor que `MC_WORKER_LEASE_SEGUNDOS`: con un latido más lento
que el lease, otro worker le quitaría el trabajo a uno que sigue vivo.

**Quien pierde el lease no cierra.** Si el latido no encuentra el trabajo como suyo, porque su lease
venció y otro lo tomó, deja de latir. Y el cierre de un trabajo empieza por tomar su fila solo si
sigue siendo de ese worker: un dueño anterior que despierta tarde no lo cierra ni toca su recurso.

**Intentos y espera.** Si el worker falla antes de que el recurso termine, el trabajo vuelve a
`PENDIENTE` y no se puede tomar sino después de una espera de
`MC_WORKER_BACKOFF_SEGUNDOS × 2^(intentos − 1)`: 1, 2, 4, 8… segundos por omisión, sin azar, para
que las pruebas lo fijen. Tomarlo cuenta un intento; al llegar a `MC_WORKER_MAX_INTENTOS` (5) sin
que el recurso termine, el trabajo queda `FALLIDO`, su recurso `FALLIDA` con el motivo, y su flujo
`DETENIDO`. No hay ciclo sin fin.

**Qué haría distinto.** Un lease más corto recupera antes un trabajo huérfano, a cambio de más
escrituras de latido y de que una pausa larga del proceso le cueste el trabajo a un worker vivo.
Con motores que tardan segundos, un minuto es holgado.

## 47. Qué pasa si un worker muere

**Decisión.** Un worker que muere no se lleva nada: lo que tenía está en PostgreSQL con su lease, y
cuando el lease vence otro worker lo toma, con un intento más y `ultimo_error` diciendo que el lease
venció. Según dónde muera:

- **Después de tomar el trabajo y antes de ejecutar el motor.** El trabajo queda `EJECUTANDO` y
  nadie se lo quita mientras su lease siga vigente; cuando vence, otro worker lo toma y lo termina.
- **A media transacción del motor.** PostgreSQL revierte lo que no se confirmó, el recurso sigue
  `EN_PROCESO`, y el siguiente worker ejecuta el motor desde el principio. Se publica una vez.
- **Después del `COMMIT` del motor y antes de cerrar el trabajo.** El recurso ya terminó; el
  siguiente worker toma el trabajo, el motor lo encuentra terminado y no hace nada, y el trabajo se
  cierra `COMPLETADO` con el flujo avanzado. Nada se duplica (ver 44).
- **En el último intento.** Si vence el lease del último intento, quien lo toma ya no ejecuta el
  motor: solo lo cierra, con el recurso `FALLIDA` y el flujo `DETENIDO`.

**Un apagado ordenado no deja nada a medias.** Con `SIGTERM` o `SIGINT`, el worker termina el
trabajo en curso y sale; una segunda señal lo detiene de inmediato. En Compose, Docker le da 30
segundos (`stop_grace_period`) antes de matarlo, y si lo mata, el trabajo vuelve a la cola cuando
vence su lease.

Las pruebas simulan cada muerte sin matar procesos: un worker que toma un trabajo y nunca lo cierra,
un motor que confirma y un worker que no llega a cerrar, y una excepción que ningún
`except Exception` atrapa, como la muerte del proceso a media transacción. Los leases se vencen en
la base en lugar de esperarlos.

## 48. Trabajo COMPLETADO no significa motor EXITOSA

**Decisión.** El estado de un trabajo es el de su **entrega**; el de su recurso, el resultado de su
**motor**. Un trabajo queda `COMPLETADO` cuando su recurso llegó a un estado terminal, cualquiera:
`EXITOSA`, `FALLIDA` o `RECHAZADA`. Queda `FALLIDO` solo cuando agotó sus intentos sin que su recurso
terminara.

```
Trabajo COMPLETADO + EjecucionDecision FALLIDA + Flujo DETENIDO
```

no es una contradicción: el worker ejecutó bien un motor que terminó de forma controlada en
`FALLIDA`, con su motivo en `detalle`, y el flujo se detuvo porque no hay decisiones que organizar.

**Por qué separarlos.** Son dos fallas distintas y se reintentan distinto. Un error del worker (la
conexión se cae, el proceso muere) es transitorio, y la cola lo reintenta sola, con espera (ver
46). Una `FALLIDA` del motor es su resultado: los motores son deterministas, y volver a ejecutarlo
sobre lo mismo fallaría igual. No se reintenta sola; se reintenta a propósito, reanudando el flujo
(ver 50) o volviendo a subir el archivo.

## 49. Pipeline automático

**Decisión.** Un solo `POST /corridas` lleva la cartera de la ingesta al ruteo sin que el cliente
pida cada etapa. La corrida nace con su `FlujoOrquestacion`, en la etapa `INGESTA`, y su trabajo.
Cuando el trabajo de una etapa se cierra con su recurso `EXITOSA`, la misma transacción abre la
etapa siguiente, su ejecución `EN_PROCESO` y su trabajo, y apunta el flujo a ella:

```
INGESTA ──▶ DECISION ──▶ TERRITORIAL ──▶ RUTEO ──▶ COMPLETADA
```

El cliente lo sigue en `GET /corridas/{run_id}/flujo`, que trae el `decision_run_id`, el
`territorial_run_id` y el `ruteo_run_id` en cuanto existen, y en `GET /flujos/{flujo_id}/trabajos`.

**Cuando algo no sale.**

- Una etapa que termina `RECHAZADA` o `FALLIDA` detiene el flujo en esa etapa, `DETENIDO`, con lo
  que pasó y cómo reintentar en `detalle`.
- Si la etapa siguiente no se puede abrir, porque alguien ya la publicó o la está ejecutando por
  otro camino, el flujo se detiene en la que terminó, con el motivo.
- Un trabajo que agota sus intentos también detiene su flujo (ver 46).

**El flujo no se mueve dos veces.** Se lee con su fila bloqueada mientras avanza, y solo lo mueve el
trabajo de su etapa vigente. Tres `CHECK` lo mantienen coherente: cada etapa apunta a las
ejecuciones de las anteriores y a la suya, y a ninguna después (`ck_flujo_cadena`); está
`COMPLETADO` si y solo si llegó a `COMPLETADA` (`ck_flujo_completado`); y tiene fin si y solo si ya
no está `EN_PROCESO` (`ck_flujo_terminado`).

**Las etapas a mano no compiten con el flujo.** `POST /corridas/{run_id}/decisiones`,
`POST /decisiones/{decision_run_id}/territoriales` y `POST /territoriales/{territorial_run_id}/ruteos`
siguen existiendo, para recursos sin flujo: corridas de antes de v0.5.0 o del CLI. Sobre la fuente
de un flujo que va a correr esa etapa responden `409 FLUJO_EN_PROCESO`, y sobre la de uno que se
detuvo ahí, `409 FLUJO_DETENIDO`: se reanuda, no se pide a mano. Una vez publicada, ninguna etapa se
repite: los `409` de siempre, `DECISION_YA_GENERADA`, `TERRITORIAL_YA_GENERADO` y
`RUTEO_YA_GENERADO`.

## 50. Reanudar una etapa fallida

**Decisión.** `POST /flujos/{flujo_id}/reanudar` reintenta la etapa en que se detuvo un flujo, si es
`DECISION`, `TERRITORIAL` o `RUTEO` y su ejecución terminó `FALLIDA`. Crea otra ejecución de esa
etapa y su trabajo, y el flujo vuelve a estar `EN_PROCESO`, apuntando a la nueva. La que falló no se
reabre ni se borra: queda en el historial de su etapa, y su trabajo en el del flujo. Responde sin
esperar a que el worker ejecute nada.

**La ingesta no se reanuda.** Una corrida terminada es evidencia inmutable: dice qué archivo llegó,
cómo se juzgó y por qué no publicó. Reabrirla la cambiaría. Se vuelve a subir el archivo, y eso crea
otra corrida con otro flujo; la firma no lo impide, porque solo bloquea una corrida `EXITOSA` o una
`EN_PROCESO` (ver 9). Reanudar un flujo detenido en la ingesta responde
`409 FLUJO_NO_REANUDABLE`, con esa explicación.

**Tampoco sin una `FALLIDA`.** Si el flujo se detuvo porque la etapa siguiente no se pudo abrir, su
etapa terminó `EXITOSA` y no hay nada que reintentar: `409 FLUJO_NO_REANUDABLE`. Un flujo
`EN_PROCESO` da `409 FLUJO_EN_PROCESO`, y uno `COMPLETADO`, `409 FLUJO_YA_COMPLETADO`.

**Por qué explícito.** Un motor que falló de forma controlada fallaría igual si se ejecutara solo
otra vez (ver 48). Reanudar es la decisión de alguien que ya vio el motivo.

## 51. API asíncrona y semántica 201

**Decisión.** Ningún `POST` ejecuta un motor. `POST /corridas`,
`POST /corridas/{run_id}/decisiones`, `POST /decisiones/{decision_run_id}/territoriales` y
`POST /territoriales/{territorial_run_id}/ruteos` registran el recurso `EN_PROCESO` y su trabajo en
una transacción y responden `201` con el recurso y su `Location`. El cliente consulta `Location`
hasta que el `estado` deje de ser `EN_PROCESO`. Reemplaza el POST síncrono de v0.2.0 a v0.4.0 (ver
20, 29 y 40), y `BackgroundTasks` desaparece (ver 11).

**Sigue siendo `201`, y no `202`.** Es la regla de siempre (ver 5): el código describe la petición,
y la petición creó un recurso que ya tiene `run_id`, dirección y estado. Que el trabajo siga en
curso es el `estado` de ese recurso. Antes, `201` llegaba con la ejecución terminada; ahora llega
`EN_PROCESO`, y para el cliente que ya leía el `estado` no cambia nada (ver 20, *qué haría
distinto*).

**A lo más una activa.** Los índices únicos parciales `ux_corrida_firma_en_proceso`,
`ux_ejecucion_decision_en_proceso`, `ux_ejecucion_territorial_en_proceso` y
`ux_ejecucion_ruteo_en_proceso` permiten a lo más una `EN_PROCESO` por fuente y versión. Un doble
clic no crea dos: el segundo POST responde `409 ARCHIVO_EN_PROCESO`, `DECISION_EN_PROCESO`,
`TERRITORIAL_EN_PROCESO` o `RUTEO_EN_PROCESO`. La revisión amable lo dice antes de registrar nada, y
el índice lo garantiza si dos peticiones la pasan a la vez: la que choca vuelve a revisar, dentro de
un `SAVEPOINT`, y responde lo mismo.

**Las carreras pasan en el worker.** Si otra ejecución publica primero, la del worker choca con el
índice de éxito al cerrar y queda `FALLIDA` en el historial, como antes; el siguiente POST ya ve la
que ganó, y responde su `409 DECISION_YA_GENERADA`, `TERRITORIAL_YA_GENERADO` o
`RUTEO_YA_GENERADO`.

**La migración cierra lo heredado.** `0006` cierra como `FALLIDA`, con un motivo que lo dice, cada
recurso que estaba `EN_PROCESO` al migrar: no tiene trabajo que lo termine, y los índices nuevos
exigen a lo más uno por fuente. El `downgrade` no los reabre.

## 52. Por qué todavía no Redis/Celery

**Decisión.** Ni Redis, ni RabbitMQ, ni Kafka, ni Celery, RQ o Dramatiq, ni una cola administrada
como SQS o Pub/Sub. El worker, el latido, las señales y la espera usan la biblioteca estándar y el
SQLAlchemy que ya estaba; v0.5.0 no agrega ninguna dependencia.

**Por qué.** Un broker resolvería la entrega, pero no lo difícil: el trabajo tendría que nacer en
la misma transacción que su recurso (ver 42), y eso pide un *outbox* en PostgreSQL de todos modos.
La garantía tampoco mejoraría: con Celery y `acks_late`, la entrega también es al menos una vez, y
la idempotencia seguiría en los motores (ver 44). A cambio habría otro servicio en el compose y en
el CI, otra forma de fallar y otro estado que reconciliar. Con un trabajo por etapa de cada corrida,
`SKIP LOCKED` sobra.

**Cuándo cambiaría.** Con mucho volumen, con trabajos que otros servicios consuman, con
prioridades, programación o reparto a muchos consumidores, o en la nube, donde una cola
administrada cuesta poco operar (ver 53).

## 53. Qué queda para Cloud + observabilidad

**Lo que v0.5.0 no hace.** Para que no se lea como más de lo que es:

- PostgreSQL es la cola: no hay un broker dedicado;
- un worker procesa un trabajo a la vez, y escalar es correr más procesos worker;
- no hay prioridades en la cola: se toma el de menor id;
- no hay una cola de mensajes muertos externa: un trabajo `FALLIDO` se queda en su tabla, con su
  recurso `FALLIDA` y su flujo `DETENIDO`;
- no hay métricas ni alertas productivas: hay bitácora, y los trabajos y flujos se consultan por la
  API;
- no hay *object storage*: el archivo vive en PostgreSQL mientras la ingesta lo necesita (ver 43);
- no hay autoscaling ni trazas distribuidas entre la API y el worker.

*Desde v0.7.0* la cola tiene dos niveles: entre los trabajos que se pueden tomar, primero los
operacionales y al final los `HISTORIA` (ver 80); *desde v0.8.0*, tres, con los `MOTOR_PAGOS` al
último (ver 96). Sigue sin haber prioridades por despacho, por cartera ni por urgencia.

**Lo que sigue, v0.6.0 — Cloud + observabilidad.** El despliegue en la nube, el archivo en un
*object storage*, las métricas de la cola (cuántos trabajos esperan, cuánto tardan, cuántos se
reintentan), alertas sobre trabajos `FALLIDO` y flujos `DETENIDO`, trazas que sigan una corrida de
la API al worker, y el autoscaling de los workers.

v0.5.0 cambia cuándo, dónde y quién ejecuta los motores, no qué calculan: `cartera/v1`,
`decision/v1`, `territorial/v1` y `ruteo/v1` publican exactamente lo mismo que en v0.4.0.

**Desde v0.6.0.** Cloud + observabilidad pasó a v0.18 — Cloud + observabilidad + hardening, y v0.6.0
fue de las fuentes oficiales. Una de las piezas de esta lista ya cambió: el archivo ya no vive en
PostgreSQL, sino en un almacén de artefactos local y por contenido, para siempre (ver 54 y 55). El
*object storage* en la nube, detrás de la misma interfaz, sigue pendiente, con lo demás de esta
lista.

## 54. El archivo original es evidencia

**Decisión.** Cada archivo que se recibe, de `cartera/v1`, `cartera/v2` o `pagos/v1`, se guarda tal
como llegó en el almacén de artefactos y **no se borra**: ni al terminar la ingesta, ni al cerrar su
trabajo, ni cuando el trabajo agota sus intentos. La corrida, o la ingesta de pagos, apunta a su
artefacto.

**Por qué, y por qué se corrige la 43.** v0.5.0 decidió lo contrario: la evidencia era la corrida,
con la firma del archivo y la de su contenido, sus cuentas y sus rechazos, y el archivo se borraba al
terminar. Eso basta para saber qué se publicó, pero no para reproducir exactamente lo que llegó: con
una firma se comprueba que un archivo es el mismo, pero no se recupera. Con cartera/v2 la distancia
se vuelve evidente: de sus 93 columnas, siete llegan a `Cuenta`. Una versión posterior que quiera
reinterpretar una columna, o un auditor que pregunte qué decía exactamente una fila, necesita los
bytes. **El archivo original ahora sí forma parte de la evidencia reproducible.** La corrida y sus
resultados siguen siendo evidencia, pero ya no son suficientes para reproducir el input.

**Lo que se conserva de la 43.** El orden: el archivo está a salvo antes de responder `201`, y la
ingesta sobrevive a que la API muera. Lo que cambia es dónde vive (un almacén por contenido, no un
`BYTEA`) y cuánto tiempo (siempre).

**Cuándo cambiaría.** Una política de retención (cuánto tiempo, quién borra, con qué aprobación) es
una decisión de gobierno de datos, no de código. Cuando exista, se aplicará sobre el almacén, como
una operación aparte y auditada, nunca al cerrar un trabajo.

## 55. Un almacén por contenido, local, detrás de una interfaz

**Decisión.** `SourceArtifactStore` es lo que la ingesta necesita de un almacén (guardar por bloques,
saber si un objeto existe, verificarlo, leerlo), y `LocalContentAddressedStore` lo implementa sobre
un directorio: cada objeto en `sha256/ab/<sha256>`, de solo lectura, escrito a un temporal en la
misma raíz, con `fsync`, y publicado con `os.replace`. La raíz es `MC_SOURCE_STORE_ROOT`; en el
compose, un volumen propio, aparte del de PostgreSQL.

**Por qué por contenido.** Si la identidad de un objeto es su SHA-256, guardar el mismo archivo dos
veces no duplica bytes, la ruta sale del contenido y no de un nombre que manda el cliente (nada de
rutas relativas ni de choques entre dos `cartera.xlsx`), y verificar un objeto es volver a firmarlo.
El nombre original es metadata, en la base.

**Por qué local y no un bucket.** v0.6.0 no se despliega en la nube, y un directorio local con la
misma semántica (publicación atómica, inmutabilidad, verificación) prueba el contrato. La interfaz
deja que un *object storage* lo implemente después sin tocar la ingesta.

**Por qué no en PostgreSQL.** La base no es un almacén de objetos: los archivos grandes inflan la
base, sus respaldos y su replicación, y un `BYTEA` no se lee ni se escribe por bloques con
naturalidad. Desde v0.6.0 PostgreSQL no guarda archivos de manera permanente: guarda la fila que
dice qué objeto es, con su SHA-256, su tamaño, su formato y su nombre original.

**Cuándo cambiaría.** Con la nube (v0.18): un *object storage* con versionado y bloqueo de
retención, detrás de la misma interfaz, y las mismas filas en la base.

## 56. Primero el objeto, después la base

**Decisión.** La subida se copia al almacén, completa y durable, antes de abrir la transacción que
registra el artefacto, la corrida y su trabajo. Si la base falla después de guardar, el objeto queda
en el almacén sin una fila que lo registre: un huérfano.

**Por qué.** El orden contrario podría dejar una fila que apunta a bytes que no existen, y eso es
evidencia perdida. Un huérfano solo ocupa disco: no es evidencia de nada y nadie lo lee. Un *commit*
atómico entre un sistema de archivos y una base no existe; el orden seguro basta.

**Lo que no hace.** No recolecta huérfanos. `motor-cartera verificar-fuentes` revisa los artefactos
registrados (que existan y que sus bytes sean los de su firma); listar los huérfanos, comparando el
almacén con la tabla, es una herramienta de operación posterior.

## 57. Los artefactos no se borran, ni al bajar la migración

**Decisión.** Ningún código borra un objeto del almacén. Bajar la `0007` quita la tabla
`artefacto_fuente`, que la `0006` no conoce, pero deja los archivos; volver a subirla empieza con el
registro vacío y el almacén intacto.

**Por qué.** Alembic gobierna el esquema, no el sistema de archivos. Bajar un esquema se deshace;
borrar evidencia, no.

**Lo que hace la bajada.** Cierra como `FALLIDA`, con un motivo que lo dice, lo que la v0.5 no
podría terminar: las corridas de cartera/v2 y las que solo tienen su archivo en el almacén, con sus
trabajos y sus flujos. Las ingestas de pagos, que la `0006` no conoce, se van con sus rechazos y
sus trabajos. Las corridas que la v0.5 dejó en la cola, con su archivo en `archivo_corrida`, se
quedan como estaban.

## 58. cartera/v1 congelado; cartera/v2 es otro contrato, declarado

**Decisión.** `cartera/v1` no cambia: sus 8 columnas, sus alias, su validación y su firma. La cartera
oficial es otro contrato, `cartera/v2`, y quien sube el archivo lo declara (`contrato`), con su
`fecha_corte`. El servidor revisa la declaración antes de guardar nada: cartera/v2 sin fecha de
corte es `422 FECHA_CORTE_REQUERIDA`; cartera/v1 con una, `422 FECHA_CORTE_NO_APLICA`. Sin el campo,
el contrato es cartera/v1, como siempre.

**Por qué declarado y no adivinado.** Con dos contratos que un archivo puede cumplir a medias,
adivinar es una decisión silenciosa. Declarado, un archivo que no corresponde falla explícitamente:
uno de v1 declarado v2, o al revés, termina `FALLIDA` por estructura, sin juzgar nada. Y los clientes
que ya existen, que no mandan `contrato`, siguen igual.

**Por qué congelar v1.** Cada corrida que ya se juzgó con v1 se tiene que poder volver a explicar
igual. Cambiar v1 para que aceptara la cartera oficial habría cambiado, en silencio, lo que esas
corridas significan.

## 59. Estructura exacta, sin descartes silenciosos

**Decisión.** cartera/v2 y pagos/v1 exigen exactamente su conjunto de columnas, en cualquier orden.
Un encabezado solo se normaliza quitándole los espacios de alrededor y llevándolo a NFC. Una columna
que falta, una que sobra, un encabezado repetido o una columna sin nombre dejan la corrida `FALLIDA`,
con el detalle, sin juzgar ningún registro. No hay un `strict="filter"` que descarte columnas.

**Por qué.** Una columna de más que se descarta en silencio esconde un cambio de la fuente (una
columna nueva, o una que cambió de nombre). Una que falta no se puede inventar. El orden no lleva
significado; el nombre sí.

**Qué se tipa.** Solo las columnas cuyo significado no es ambiguo por su nombre: fechas, importes,
enteros, códigos postales, teléfonos y coordenadas, más los dos catálogos que la proyección copia
(`PRODUCTO` y `CANAL`). Las demás se conservan como texto: tiparlas sería inventar reglas que la
fuente no declara.

## 60. La fecha de corte es metadata del lote

**Decisión.** La fecha de corte de una cartera oficial se declara al subirla y queda en la corrida
(y, por la proyección, en cada `Cuenta`). No es una columna 94.

**Por qué.** Las 93 columnas no traen una, y agregar una columna sintética haría que el contrato no
fuera el del archivo real. Una cartera es la foto de un día: una fecha por archivo, que dice quien lo
entrega.

**Consecuencia.** El mismo `CLIENTE_UNICO` en cortes distintos es válido: son corridas distintas.
Dentro de un corte, un `CLIENTE_UNICO` repetido rechaza todas sus copias, porque no hay forma de
saber cuál es la buena.

## 61. Dos firmas: la del archivo y la del contenido

**Decisión.** `firma` es el SHA-256 de los bytes: identifica el artefacto y evita publicar dos veces
el mismo archivo. `firma_contenido` es el SHA-256 de los registros válidos en forma canónica: cada
registro, una línea canónica (cada valor con su largo, sin escapar nada), y las huellas de las
líneas, ordenadas. No depende del formato, del orden de las filas ni del orden de las columnas, y
está versionada con el contrato.

**Por qué.** La misma cartera exportada en xlsx y en csv es el mismo contenido, y reordenar sus
filas no la vuelve otra. La firma de contenido lo deja a la vista; la del archivo sigue siendo la
identidad de lo que llegó.

**Lo que todavía no hace.** Una cartera se publica una vez por archivo, no por contenido: la misma
cartera en dos formatos se publica dos veces. La firma de contenido lo detecta, pero no lo impide
(ver limitaciones del README).

## 62. Original y conformado: dos representaciones

**Decisión.** De cada archivo aceptado quedan dos cosas: el **original**, los bytes exactos, y el
**conformado** (*source-conformed*): un Parquet con los registros válidos, las columnas del contrato
en su orden oficial, cada valor tipado desde su forma canónica, y dos columnas técnicas,
`_source_row` y `_source_sheet`, que atan cada registro a su fila. No lleva scores ni variables
derivadas. Se guarda en el mismo almacén y se registra en `dataset_conformado`, con su contrato, su
firma, sus conteos y de qué artefacto original salió.

**Por qué.** El original es la evidencia, pero no es cómodo de leer a escala: un xlsx de cientos de
miles de filas tarda minutos solo en abrirse, y cada lectura tendría que volver a interpretarlo. v0.7
tiene que construir el modelo canónico sobre algo ya validado y tipado. El conformado es derivado, y
por eso es reproducible: el mismo archivo con la misma versión del contrato produce el mismo
Parquet, byte por byte, y una prueba lo regenera desde el original y compara los bytes.

**Por qué Parquet.** Columnar, tipado, comprimido (zstd) y escrito por lotes, un *row group* por
lote, sin tener el archivo entero en memoria.

## 63. Juicio por lotes en dos pasadas, todo o nada

**Decisión.** Una fuente oficial se lee por lotes (`MC_FILAS_POR_LOTE`). En la primera pasada cada
lote se juzga con validaciones vectorizadas, los rechazos se copian a PostgreSQL con `COPY`, los
válidos van a un Parquet provisional en disco y se cuentan las llaves. En la segunda, con las llaves
de todo el archivo a la vista, se rechazan todas las copias de una llave repetida, y lo válido se
firma, se conforma, se proyecta y se publica. Todo en una transacción.

**Por qué.** La memoria queda acotada por el lote, no por el archivo. Las invariantes globales
(una llave única en todo el archivo) necesitan ver el archivo entero. Y publicar todo o nada es la
misma semántica de siempre (ver 2 y 28).

**El costo.** Los válidos se leen dos veces (la segunda, de un Parquet local, que es rápido), y un
archivo grande es una transacción larga.

## 64. Proyección operacional explícita, con el catálogo público del INEGI

**Decisión.** La proyección `operacional/v1` lleva siete columnas de cartera/v2 a `Cuenta`
(`CLIENTE_UNICO`, `SALDO_TOTAL`, `DIAS_ATRASO`, `PRODUCTO`, `CANAL` y, desde `ESTADO_CTE` y
`POBLACION_CTE`, las claves del INEGI) y la corrida registra su versión. Los nombres se resuelven con
una foto del catálogo público del INEGI (servicio wscatgeo v2), versionada con el código, comparando
sin acentos ni mayúsculas y con unos pocos alias controlados a mano. Un estado o un municipio que no
está, o un nombre que corresponde a dos municipios de la misma entidad, rechaza el registro con su
motivo.

**Por qué.** Los motores leen `Cuenta`, y cambiarlos no era de v0.6.0. Una proyección explícita y
versionada los deja intactos y hace auditable el mapeo: la cartera oficial llega por el mismo flujo
hasta el ruteo. El catálogo es público (no es un dato privado) y hace falta porque cartera/v2 trae
nombres, no claves. Adivinar un municipio ambiguo sería publicar una cuenta en el lugar equivocado.

**Cuándo cambiaría.** Con la geografía real (v0.11): localidades, colonias, códigos postales y
coordenadas.

## 65. CARRIER es una hoja compañera observada

**Decisión.** CARRIER se reconoce (una hoja de xlsx, o un miembro de zip, con ese nombre) y se audita
sin juzgarla registro por registro: si su estructura es la esperada (85 columnas), cuántas filas
trae, cuántas traen un cliente que CARTERA no tiene y cuántas un teléfono sin forma. Cada problema es
una advertencia que queda en `hoja_companera` y en la evidencia de la corrida. No se publica, no se
proyecta y no gobierna la publicación de CARTERA.

**Por qué.** Todo lo que CARRIER dice ya está en CARTERA: sus clientes son los de CARTERA y sus
teléfonos, las columnas anchas de cada fila. Publicarla duplicaría los datos de contacto sin agregar
nada que la plataforma use todavía. Lo que sí importa es saber si el archivo la trae y si es
coherente, porque una CARRIER incoherente dice que el archivo no se armó bien. El original, con
CARRIER incluida, se conserva entero (ver 54).

## 66. pagos/v1: su propia ingesta, sin deduplicar, con barrera conservadora

**Decisión.** Un archivo de pagos tiene su propia entidad (`IngestaPagos`, con `pagos_run_id`), su
propio recurso (`/pagos`), sus rechazos y su trabajo `INGESTA_PAGOS` en la misma cola durable, con
lease, latido, reintentos e idempotencia. Una fila es un movimiento y **no se deduplica nada**. La
tolerancia por omisión es 0. El mismo archivo se acepta una vez y se procesa a lo más en una ingesta
a la vez (dos índices únicos parciales sobre la firma); uno rechazado se puede corregir y volver a
subir. No encadena nada.

**Por qué no es una corrida.** Una corrida publica las cuentas de un corte; un archivo de pagos trae
los movimientos de un periodo. Reusar la corrida habría mezclado semánticas (la tolerancia, el
corte, la proyección, el flujo hasta el ruteo) que no le corresponden.

**Por qué no deduplicar.** La llave histórica (cliente, recepción al segundo e importe a centavos)
es una heurística: también junta dos pagos legítimos e iguales. Deduplicar, conciliar e interpretar
reversos tiene que poder explicar qué movimiento fuente produjo cada movimiento canónico, y eso es
el motor de pagos de v0.8, que va a partir de este conformado, que trae todos los movimientos.

**Desde v0.8.0.** El motor de pagos existe, y no parte del conformado directamente sino de los
pagos observados que la historia materializa de él (85, 96): cada movimiento canónico se explica
hasta su fila del archivo original.

**Por qué tolerancia 0.** Es dinero: un archivo aceptado a medias subestimaría la recuperación sin
que nadie lo notara. Sus rechazos quedan a la vista, con su fila, sus 23 valores y su motivo.

**La API.** `POST /pagos`, `GET /pagos/{pagos_run_id}`, `/rechazos` y `/fuente`, con la misma forma
que las corridas. Los errores traen `pagos_run_id` cuando tienen que ver con una ingesta de pagos,
igual que `run_id` con una corrida (null si no): la forma de los errores sigue siendo una sola.

## 67. Un despacho, una cartera

**Decisión.** `despacho_id` y `cartera_id` son configuración del sistema (`DSP_001` y
`CARTERA_PRINCIPAL` por omisión) que cada corrida y cada ingesta de pagos guarda. No son columnas de
las fuentes, y no hay multitenancy.

**Por qué.** Operar varios despachos o varias carteras va a necesitar el valor en cada registro.
Guardarlo desde ahora cuesta una columna; implementar la administración de varios no es de v0.6.0.

## 68. Detección de formato por contenido

**Decisión.** Antes de guardar, se comprueba que los bytes sean lo que dice la extensión: un xlsx es
un zip con la estructura de un libro de Excel; un zip, un zip legible; un csv, texto en UTF-8 o
cp1252, sin bytes nulos ni la firma de un binario conocido. Lo que no corresponde se rechaza con
`415 FORMATO_NO_CORRESPONDE` y no se guarda.

**Por qué.** La extensión es un nombre. Un PDF renombrado `.csv` no debe quedar como evidencia de
una cartera ni llegar al lector.

## 69. Escala: perfiles, escenario longitudinal y benchmark fuera del CI

**Decisión.** El generador tiene perfiles de XS (1,000 cuentas) a XXL (1,000,000); XL (500,000) es
el escenario empresarial objetivo, y XS es el valor por omisión. Se genera por bloques, con los
atributos fijos de cada cliente derivados de un hash de su número y la semilla. `generar-escenario`
escribe varios cortes relacionados y los pagos entre ellos, con sus invariantes en un manifiesto, y
los zip que escribe no llevan la hora en que se escribieron, así que el mismo escenario sale igual,
byte por byte. `scripts/benchmark_escala.py` mide cada fase de punta a punta, y un workflow manual lo
corre en GitHub.

**Por qué fuera del CI.** El CI tiene que ser rápido y determinista. La escala se mide donde se puede
repetir con las mismas condiciones, y no se fija un SLA: los tiempos dependen del hardware. Lo que
se fija es que el perfil objetivo se procesa entero, con la memoria acotada.

**Lo que se midió.** En la máquina de desarrollo, una cartera XL en zip (500,000 cuentas y 1,150,136
filas de CARRIER) se ingirió en 149 s, a 3,355 filas por segundo, con 930 MiB de memoria pico, y sus
279,959 pagos, en 21.5 s. El detalle por fase, y la prueba de esfuerzo XXL, están en
[fuentes.md](fuentes.md).

## 70. Qué no resuelve v0.6.0, y qué queda para v0.7

**Lo que v0.6.0 no hace**, a propósito: el modelo canónico de persona y crédito, Cliente 360, un
identificador de crédito inventado, la deduplicación económica de pagos, su conciliación, los
reversos y la atribución de pagos a gestiones; gestiones, contactos, promesas, convenios y visitas;
un Decision Engine nuevo, scores y modelos; geografía con polígonos, bases, zonas, capacidades y
jornadas; ruteo vial; tableros; el despliegue en la nube, el *object storage*, las trazas y el
autoscaling; un broker; multitenancy y multidespacho. Las interfaces no los bloquean: el original
se conserva entero, el conformado trae cada registro con su fila, los cortes de un mismo cliente se
pueden publicar uno tras otro, los pagos traen cada movimiento y cada registro guarda su despacho y
su cartera.

**Lo que queda para v0.7 — Modelo canónico/histórico + Cuenta 360.** Construir, sobre los datasets
conformados de cartera/v2 y de pagos/v1, el modelo canónico e histórico: una cuenta a través de sus
cortes (cuándo entró, cómo cambió su saldo y su atraso, cuándo salió), con sus pagos y su evidencia,
sin volver a interpretar un solo archivo. Los escenarios longitudinales del generador son su banco
de pruebas.

## 71. Tres capas: el histórico no reemplaza al conformado ni a la operacional

**Decisión.** El modelo histórico es una capa nueva entre el dataset conformado (el Parquet de cada
fuente, completo y tipado) y la proyección operacional (`Cuenta`, que leen los motores v1). No
reemplaza a ninguna: el Parquet sigue siendo la fuente completa, y `Cuenta` sigue siendo lo que
leen decision/v1, territorial/v1 y ruteo/v1, que no leen la historia ni la esperan.

**Por qué.** Cada capa contesta otra pregunta. El conformado dice qué llegó, columna por columna;
la operacional, qué se hace hoy con cada cuenta; la histórica, qué le pasó a cada cuenta a través
del tiempo. Fusionarlas obligaría a copiar 93 columnas por corte (26 millones de filas anchas al
año con la cartera objetivo) o a que los motores dependieran de una proyección que todavía no
necesitan. Que decision/v1 consuma la historia es de v0.10.

**Cuenta y CuentaCanonica.** `Cuenta` no se renombra ni cambia: es la foto operacional de una
corrida. `CuentaCanonica` es la identidad longitudinal; sus valores en cada corte son sus snapshots.

## 72. La identidad: despacho + cartera + CLIENTE_UNICO, sin persona ni crédito

**Decisión.** La cuenta canónica es `(despacho_id, cartera_id, cliente_unico)`, con su restricción
única. No hay `persona_id`, `credito_id` ni `cliente_persona`.

**Por qué.** Ninguna fuente dice que dos cuentas sean de la misma persona, ni trae un identificador
de crédito: inventarlos sería afirmar algo que los datos no sostienen, y cada número que saliera de
ahí heredaría ese supuesto. `CLIENTE_UNICO` es el identificador fuente de la cuenta y así se trata.
Que la llave incluya despacho y cartera cuesta dos columnas y evita rediseñar cuando haya más de
una: el mismo `CLIENTE_UNICO` en otra cartera ya es otra cuenta, aunque hoy opere una sola.

**Cuándo cambiaría.** Cuando una fuente traiga una identidad de persona o de crédito con evidencia
suficiente: entonces sería una capa encima de esta, no un reemplazo.

## 73. Identificadores públicos deterministas

**Decisión.** `cuenta_id`, `corte_id` y `pago_observado_id` son UUID versión 5 de su llave natural
(despacho, cartera y `CLIENTE_UNICO`; despacho, cartera y fecha; dataset y fila), en un espacio de
nombres fijo del proyecto. `historia_run_id`, que identifica un intento y no un hecho, sigue siendo
aleatorio, como los demás `*_run_id`.

**Por qué.** La historia se puede reconstruir (un backfill, una migración que baja y vuelve a
subir): con identificadores sorteados, cada reconstrucción cambiaría los identificadores públicos y
rompería cualquier referencia guardada fuera del sistema. Con UUID v5, reconstruir en cualquier
orden y en cualquier máquina da los mismos, y la prueba de reconstrucción los compara. El id
interno sigue siendo el que usan las llaves foráneas, y nunca sale por la API.

**Qué no esconden.** Quien conoce la llave puede calcular el identificador, igual que con la API key
puede buscar la cuenta por su `CLIENTE_UNICO`. Lo que no revelan, a diferencia de un consecutivo, es
el volumen ni el orden en que se crearon.

## 74. Un corte canónico por fecha: fuentes equivalentes y cortes conflictivos

**Decisión.** Hay un solo `CorteCanonico` por `(despacho_id, cartera_id, fecha_corte)`, atado al
dataset conformado que lo produjo. Un segundo dataset de la misma fecha con la **misma** firma de
contenido es una fuente equivalente: su ejecución termina `EXITOSA` con `FUENTE_EQUIVALENTE`,
apuntando al mismo corte, sin leer ni duplicar nada. Con **otra** firma es un conflicto: su
ejecución termina `FALLIDA` con `CORTE_CANONICO_CONFLICTIVO` y el corte publicado no cambia.

**Por qué.** v0.6.0 deja publicar la misma cartera en dos formatos, y eso no debe duplicar la
historia; pero dos carteras distintas del mismo día son una contradicción que el sistema no puede
resolver solo. Elegir "la última" sería sobrescribir la historia en silencio y hacer que su
contenido dependiera del orden de llegada. Es la diferencia con la capa operacional, donde "vale la
última" del mismo corte (decisión 10): la operación necesita una cartera para trabajar hoy, y la
historia necesita no mentir sobre ayer.

**Lo que queda pendiente.** Una corrección explícita (reemplazar un corte con otro, con su motivo y
su auditoría, sin borrar el anterior) es una operación que v0.7 no tiene. El conflicto queda
auditado mientras tanto, con las dos firmas y el dataset de cada una.

## 75. Un snapshot estrecho, inmutable y con su linaje

**Decisión.** `SnapshotCuenta` guarda una cuenta en un corte con solo las variables históricas de
uso transversal (saldos, atraso, producto, estrategia, canal, último pago, geografía, plan y
promesa), cada una con el tipo que tiene en el Parquet, y su `source_row` y `source_sheet`. Su llave
primaria es `(corte_canonico_id, cuenta_canonica_id)`, sin id propio. No guarda PII ni un hash del
registro, y un snapshot nunca se actualiza.

**Por qué estrecho.** Las 93 columnas ya están en el Parquet, y `source_row` lleva a la fila exacta.
Copiarlas haría la tabla grande varias veces más ancha por columnas que la historia no consulta,
empezando por el nombre, el domicilio y los teléfonos, que no deben multiplicarse en una tabla de
decenas de millones de filas. Lo que v0.14 necesite de más se agrega con evidencia de que se usa.

**Por qué sin id ni hash.** Nadie referencia un snapshot por un id, y su llave natural es igual de
corta: un id serial habría sido una columna y un índice de más por fila. El hash del registro
costaría 32 bytes por fila y no explicaría nada que el dataset y la fila no expliquen ya; detectar
que una cuenta cambió en algo de sus 93 columnas, si se necesita, se calcula sobre el Parquet.

**Por qué inmutable.** Un corte nuevo es otra fila. Sin `UPDATE`, la historia no depende del orden
en que llegaron los cortes, y un corte atrasado no puede reescribir los demás.

## 76. La fecha en el snapshot, garantizada por una llave compuesta

**Decisión.** El snapshot repite la `fecha_corte` de su corte, y su llave foránea es compuesta:
`(corte_canonico_id, fecha_corte)` hacia `corte_canonico (id, fecha_corte)`, que tiene su propia
restricción única.

**Por qué.** Las consultas principales son de una cuenta: su historia en orden de fecha, su último
snapshot, su snapshot en una fecha. Con la fecha en el snapshot, el índice `(cuenta_canonica_id,
fecha_corte)` las resuelve solo, con un `LIMIT` que se detiene en cuanto tiene lo que pide; sin
ella, cada una tendría que leer todos los snapshots de la cuenta, cruzarlos con sus cortes y
ordenar. La redundancia no puede mentir: la base rechaza un snapshot cuya fecha no es la de su
corte. Cuesta cuatro bytes por fila.

## 77. Pagos observados: una fila, una observación, sin llave hacia la cuenta

**Decisión.** Cada fila aceptada de pagos/v1 es exactamente un `PagoObservado`, con sus 23 campos
tipados como en el Parquet, su dataset, su ingesta, su fila, su hoja, su despacho y su cartera. La
llave primaria es `(dataset_conformado_id, source_row)`. No hay deduplicación y no hay llave foránea
hacia `CuentaCanonica`: la relación se hace al consultar, por despacho, cartera y `CLIENTE_UNICO`,
con su índice.

**Por qué materializarlos.** La Cuenta 360 necesita los pagos de una cuenta sin leer Parquet en cada
petición, y el motor de pagos de v0.8 los va a analizar con los 23 campos. No son verdad económica:
no hay pago conciliado, aplicado ni atribuido, y la API no presenta ninguna suma como recuperación.

**Por qué sin llave hacia la cuenta.** Una asociación guardada sería mutable: un pago que llega
antes que el primer corte de su cliente tendría que enlazarse después, y un backfill de cortes
obligaría a volver a enlazar. Sin ella, un pago de un cliente que ningún corte trae se conserva
como `SIN_CUENTA_OBSERVADA` y **no crea una cuenta**; cuando el corte llega, el pago se relaciona
solo, sin que se toque. Las relaciones económicas explícitas son del motor de pagos.

**Desde v0.8.0.** Esas relaciones existen, en capas aparte: el motor de pagos interpreta los pagos
observados sin modificarlos y concilia cada movimiento con su cuenta dentro de su interpretación,
que es versionada (85, 91). El pago observado sigue sin llave hacia la cuenta.

**Por qué su id no tiene índice.** `pago_observado_id` sale del dataset y la fila, que ya son la
llave primaria, así que su unicidad está implicada; ninguna consulta de v0.7 lo busca, y un índice
sobre quince millones de UUID al año sería costo sin uso.

## 78. Eventos y continuidad al consultar; el vocabulario de lo observado

**Decisión.** No hay tabla de eventos ni de deltas. `PRIMERA_OBSERVACION`, `SALIDA_OBSERVADA`,
`REINGRESO_OBSERVADO`, la continuidad y los deltas se calculan al consultar, en un núcleo puro
(`historia/presencia.py`), a partir de los cortes de la cartera y de los cortes en que la cuenta
tiene snapshot. El estado de presencia es `EN_CARTERA` o `NO_OBSERVADA_EN_ULTIMO_CORTE`.

**Por qué al consultar.** Todo eso ya está implícito en los snapshots; guardarlo duplicaría datos y,
peor, los haría depender del orden de llegada: un corte atrasado obligaría a reescribir eventos y
deltas ya guardados. Calcularlos cuesta leer los cortes de una cartera (cientos) y los de una cuenta
(un índice). El benchmark lo mide con la cartera objetivo ([historia.md](historia.md)): con 12
cortes XL publicados, los eventos de una cuenta salen en unos 2.4 ms desde el servicio. No hace
falta persistir nada.

**Por qué ese vocabulario.** La primera vez que se observa una cuenta no es su originación, y que
deje de aparecer no demuestra que se liquidó, se canceló, se castigó o se vendió. Los nombres dicen
lo que el sistema sabe. Dos snapshots son continuos solo si sus cortes son consecutivos en la
cartera; si la cuenta faltó en medio, la diferencia se muestra pero no se etiqueta como la evolución
de un periodo.

## 79. historia/v1: una ejecución versionada, abierta con su dataset, paralela al flujo

**Decisión.** Cada materialización es una `EjecucionHistoria` versionada (`historia/v1`), con su
resultado estable, sus conteos y su detalle; a lo más una `EXITOSA` y una `EN_PROCESO` por dataset y
versión, con índices únicos parciales. Se abre, con su trabajo `HISTORIA`, en la misma transacción
que publica el dataset conformado de cartera/v2 o de pagos/v1. Es un solo tipo de trabajo nuevo,
`HISTORIA`, sin flujo: `tipo_fuente` ya dice si es de cartera o de pagos.

**Por qué en la misma transacción.** Si la historia se abriera después, habría un instante en que el
dataset existe sin su historia pendiente, y una caída en ese instante la perdería sin que nadie lo
supiera. Abierta con el dataset, cualquier worker la termina.

**Por qué paralela.** La historia es una proyección, no una etapa: decision/v1 no la necesita, y una
historia que falla (un conflicto de corte, por ejemplo) no debe detener la operación del día. Por
eso no es parte del flujo, no tiene `flujo_id` y ninguna etapa la espera.

**Por qué un resultado además del estado.** `EXITOSA` no basta para distinguir un corte publicado de
una fuente equivalente, ni `FALLIDA` un conflicto de un error. El resultado es el código estable que
un cliente compara; su vocabulario, como el de las reglas de los motores, es de su versión y no
lleva `CHECK`.

## 80. La cola toma lo operacional antes que la historia

**Decisión.** Entre los trabajos que ya se pueden tomar, el worker toma primero los operacionales
(ingestas, decisiones, organizaciones territoriales, ruteos e ingestas de pagos) y al final los
`HISTORIA`: `ORDER BY (tipo = 'HISTORIA'), id`. No cambia el lease, el latido, los reintentos, la
entrega al menos una vez ni los cierres condicionados a su dueño.

**Por qué.** La historia de una cartera se abre al terminar su ingesta, antes de que su decisión
entre a la cola: con un orden estrictamente por id, un solo worker haría esperar a la decisión del
día detrás de la historia, y un backfill de cientos de cortes la haría esperar horas. Con dos
niveles, la historia no retrasa la operación, y sigue siendo justa entre sí. El conjunto de trabajos
que se pueden tomar es pequeño (el índice parcial solo cubre los pendientes y los vencidos), así que
ordenarlo no cuesta.

**El riesgo.** Con un flujo constante de trabajo operacional, la historia podría esperar; con la
operación de un despacho (unas pocas subidas al día) no pasa, y más workers lo resuelven.

**Desde v0.8.0.** Hay un tercer nivel, al final: `MOTOR_PAGOS`. El orden es `ORDER BY CASE` (0 los
operacionales, 1 `HISTORIA`, 2 `MOTOR_PAGOS`), `id` (ver 96).

*Desde v0.9.0*, un cuarto nivel, 3, para `ATRIBUCION` y `EVALUACION_PROMESAS`, que leen lo que
publica el motor de pagos; *desde v0.10.0*, un quinto, 4, para `DECISION_V2`, que lee todo lo
anterior (ver 119). El nivel no cambia lo que decide una decisión v2: lo fija su `known_at`.

## 81. Carga masiva todo o nada: Parquet, CSV de Arrow, COPY e INSERT ... SELECT

**Decisión.** La materialización de un corte lee el Parquet por lotes, solo con las columnas que
necesita, convierte cada lote a CSV con el escritor de Arrow y lo copia con `COPY` a una tabla
temporal (`ON COMMIT DROP`); después inserta las cuentas canónicas nuevas y los snapshots con
`INSERT ... SELECT`, resolviendo las cuentas por `JOIN`, cuenta y cierra, todo en una transacción.
Un dataset de pagos se copia con `COPY` directo a `pago_observado`. Antes de leer, el Parquet se
comprueba contra su SHA-256 y contra su registro. Nunca se lee el archivo original.

**Por qué así.** Un objeto ORM por fila no escala a 500,000 filas por corte. El CSV de Arrow se
escribe en C++ y es exactamente el que PostgreSQL lee (un texto entre comillas, un vacío sin ellas es
`NULL`), así que ningún valor pasa fila por fila por Python. La tabla temporal permite resolver las
cuentas con operaciones de conjunto, que el planificador hace con un *hash join*, después de un
`ANALYZE` de la tabla temporal. Todo en una transacción hace que una falla en cualquier punto no deje
nada a medias; las pruebas inyectan fallas en cinco puntos distintos.

**Por qué solo el conformado.** Volver a leer el original repetiría el juicio del contrato con otra
implementación, y podría no dar lo mismo. El Parquet ya es la fuente validada y tipada; el original
queda como evidencia. Una prueba borra el original del almacén y la historia se materializa igual.

## 82. Concurrencia sin bloqueos mutuos

**Decisión.** La ejecución se toma con su fila bloqueada; el corte se publica con
`INSERT ... ON CONFLICT DO NOTHING` sobre su fecha; las cuentas nuevas se insertan en orden de
`CLIENTE_UNICO` con `ON CONFLICT DO NOTHING`.

**Por qué.** Dos workers con el mismo dataset: el segundo espera la fila y la encuentra terminada.
Dos fuentes del mismo corte: el segundo `INSERT` espera a que el primero confirme y entonces se juzga
la equivalencia o el conflicto contra el corte ya publicado. Dos cortes distintos con cuentas nuevas
en común: como los dos las insertan en el mismo orden, el que llega segundo espera en la primera
común, y no puede haber un ciclo de esperas. Un worker que pierde su lease después de calcular sigue
teniendo la ejecución bloqueada: publica una vez, y el trabajo lo cierra su dueño vigente. Las
pruebas fuerzan cada caso con dos hilos y PostgreSQL real.

**El costo.** Mientras un corte publica sus cuentas nuevas, otro que comparte algunas espera a que
termine. Con cortes de una misma cartera que llegan uno por semana, es raro y breve.

**Y las lecturas.** Cada respuesta de la Cuenta 360 y de la historia compone varias consultas: los
cortes de la cuenta, los de la cartera, sus snapshots, sus pagos. Se leen en una transacción de solo
lectura `REPEATABLE READ`, así que todas ven la misma foto de la base, y un corte que se publica
mientras se responde no aparece en una consulta y falta en la siguiente. Con `READ COMMITTED`, una
cuenta que sí está en el corte nuevo se vería, en esa respuesta, como una salida. En PostgreSQL, una
transacción que solo lee no falla por serialización, así que esto no agrega reintentos. Una prueba
publica un corte entre dos consultas de una misma respuesta.

## 83. Backfill fuera de Alembic, idempotente

**Decisión.** La migración `0008` solo crea las tablas y extiende la cola; no materializa ningún
dataset. `motor-cartera backfill-historia` encola un trabajo `HISTORIA` por cada dataset sin historia
`EXITOSA` (los de cartera en orden de fecha de corte), con `--dry-run` y `--reintentar-fallidas`.
Bajar la `0008` cierra primero la historia en curso (sus ejecuciones `FALLIDA`, sus trabajos
`FALLIDO`, con el motivo), quita los trabajos `HISTORIA` y las tablas, y no toca los datasets, sus
artefactos, las corridas ni las ingestas.

**Por qué.** Procesar millones de filas dentro de una migración la haría lenta, imposible de
reintentar a medias y bloquearía el despliegue. El backfill es un proceso de la aplicación, con su
cola, sus reintentos y su auditoría, y es idempotente: correrlo dos veces no encola nada dos veces.
Las ejecuciones que solo fallaron no se reintentan sin pedirlo, porque un conflicto de corte
volvería a fallar igual. Como la historia es una proyección del conformado, bajar la migración no
pierde evidencia: al volver a subir, el backfill la reconstruye, con los mismos identificadores.

## 84. Índices medidos, sin particionado; y qué no resuelve v0.7.0

**Decisión.** Cada índice responde a una consulta que existe (la tabla está en
[historia.md](historia.md)), y ninguno es "por si acaso": no hay índice sobre
`pago_observado_id`, ni sobre la fecha sola de `corte_canonico`, y el de la historia de una cuenta
se declaró sin `DESC`, porque el btree se recorre hacia atrás con el mismo plan. No hay particionado.

**Por qué sin particionado.** Con esos índices, ninguna consulta de una cuenta recorre una tabla
grande, y el benchmark lo comprueba con el plan de cada sentencia: con 12 cortes XL publicados (5.8
millones de snapshots y 3 millones de pagos observados), la sentencia más cara de una cuenta lee 30
páginas y se ejecuta en 0.14 ms dentro de PostgreSQL. El particionado agrega
complejidad (llaves primarias que incluyan la llave de partición, mantenimiento de particiones,
planes que dependen de la poda) y se justifica con evidencia: cuando el mantenimiento de
`snapshot_cuenta` o `pago_observado` cueste, o cuando la analítica de v0.14 necesite podar por fecha.

**Lo que v0.7.0 no hace**, a propósito: analytics (roll rates, vintages, cure rates, cohortes,
matrices de transición, pronósticos), que es de v0.14; el motor de pagos (deduplicación,
conciliación, reversos, aplicación, atribución, recuperación neta, la llave económica), que es de
v0.8; el lifecycle de cobranza, de v0.9; variables históricas, scores o modelos en las decisiones,
de v0.10; persona, crédito, correcciones explícitas de un corte y multitenancy, sin evidencia
todavía. La historia está hecha para que todo eso se calcule encima sin rediseñarla.

**Desde v0.8.0.** El motor de pagos existe (85 a 98): duplicados exactos, coincidencias de la llave
histórica, reversos, conciliación con la cuenta y recuperación interpretada. La atribución de
cada pago a una gestión sigue pendiente: necesita el lifecycle de cobranza de v0.9.

## 85. Observación e interpretación, separadas: el motor no toca PagoObservado

**Decisión.** `PagoObservado` sigue siendo exactamente lo que era en v0.7: una fila aceptada de
pagos/v1, sin columnas nuevas, sin marcas y sin borrados. Lo que el motor de pagos concluye vive en
tres entidades nuevas y versionadas: `EjecucionMotorPagos` (una interpretación de una ventana),
`ResultadoPagoObservado` (lo que esa interpretación concluyó de cada observación, y por qué) y
`MovimientoEconomicoCanonico` (cada hecho económico que considera distinto). No hay tabla de grupos
(`GrupoConciliacionPago`).

**Por qué.** La meta no es eliminar filas repetidas: es conservar cada observación, interpretar la
realidad económica de forma explícita, explicar cada decisión, poder reconstruirla y poder
reinterpretarla con otra versión. Si el motor marcara las observaciones (un `es_duplicado`, un
`es_reverso`), una versión posterior tendría que deshacer lo que hizo la anterior, y un error de
interpretación quedaría escrito sobre la evidencia. Separadas, `motor-pagos/v2` puede publicar su
propia interpretación de los mismos pagos sin tocar la de `v1`. Una prueba compara cada pago
observado, columna por columna, antes y después de interpretar, y las llaves foráneas de los
resultados impiden borrar una observación que ya se interpretó.

**Por qué sin tabla de grupos.** Un grupo de copias o de la llave histórica es una consulta: las
observaciones de la ventana con esa huella. Cada resultado ya guarda sus dos huellas y, en sus
motivos, cuántas observaciones y cuántas firmas distintas tiene su grupo y qué campos las
distinguen; `GET /motor-pagos/{id}/resultados?firma_legacy=...` lista el grupo entero. Una tabla más
sería otra cosa que mantener consistente, sin agregar auditabilidad.

## 86. La ventana: un mes de recepción por cartera, con 30 días de contexto

**Decisión.** Una ejecución interpreta una **ventana**: un despacho, una cartera y un mes calendario
de `Fecha_Recepción`, `[periodo_desde, periodo_hasta)`, con todos los pagos observados de ese mes,
vengan del archivo que vengan. Lee además un **contexto** acotado: los pagos de hasta 30 días antes
y después del mes que comparten cliente e importe absoluto con algún pago de la ventana, cuando esa
llave tiene algún negativo en ese rango. El contexto sirve solo para decidir parejas de reverso y no
recibe resultado. La ventana y su contexto se leen en una sola sentencia. `ck_motor_pagos_mes` exige
que la ventana de `motor-pagos/v1` sea un mes; otra versión puede usar otra.

**Por qué por ventana y no por archivo.** Un archivo no es una unidad económica: el mismo pago puede
llegar en dos archivos, y su reverso en el siguiente. Interpretar un archivo a la vez obligaría a
corregir lo que se concluyó del anterior.

**Por qué es incremental.** Cuando llega un archivo, se interpreta otra vez la ventana que toca (y
una vecina solo si su contexto cambia), no la historia: el costo es el de un mes de pagos y no crece
con los meses acumulados. El resultado es el mismo que reconstruir todo desde cero con la misma
versión, porque una ventana se interpreta siempre entera: la prueba de reconstrucción borra la capa
de v0.8 y la vuelve a calcular, la de orden de llegada ingiere los archivos en tres órdenes, y la de
equivalencia compara contra el núcleo puro aplicado a todos los pagos de la cartera a la vez.

**Por qué 30 días de contexto, y no más.** Es la ventana de los reversos (89): una pareja nunca está a
más de 30 días, así que ningún otro pago puede cambiar una decisión de la ventana, y por eso no se lee.
Lo que sí puede cambiarla (un pago o un negativo del mismo cliente e importe, a menos de 30 días)
vuelve a abrir la ventana (96).

**El costo.** Una ventana se interpreta entera aunque llegue un solo pago: con la cartera objetivo, un
mes tiene del orden de un millón de observaciones, y el benchmark mide cuánto cuesta (98). Una
interpretación por diferencias (solo lo nuevo y los grupos que toca) costaría menos, pero la vigente
dejaría de ser una interpretación completa de su ventana, y la API tendría que componer cada respuesta
de varias. Si un mes llegara a ser demasiado, una versión posterior puede usar ventanas más cortas.

## 87. Dos huellas, y la comparación campo por campo antes de fusionar

**Decisión.** Cada observación tiene dos huellas SHA-256 de una línea canónica (la de
`contratos.fuente.linea_canonica`), con un prefijo de versión: la **firma exacta**, del despacho, la
cartera y los 23 campos del contrato en su orden, cada uno con el texto canónico de su tipo; y la
**llave histórica** (`firma_legacy`), del despacho, la cartera, el cliente, la recepción truncada al
segundo y el importe con dos decimales. Se calculan en SQL, donde el motor las necesita para millones
de filas, y en Python, que es la referencia; las pruebas comprueban que coinciden. Antes de tratar
varias observaciones como copias, el motor compara cada una con su representante **campo por campo**
(`IS DISTINCT FROM` sobre las 25 columnas); si alguna difiere, el grupo queda `NO_CONCILIADO` con
`FIRMA_SIN_VALIDAR`.

**Por qué una huella.** Comparar 23 columnas entre todas las observaciones sería cuadrático; agrupar
por una llave de 32 bytes es un `GROUP BY` con un índice en la tabla temporal, y la llave queda en
cada resultado para auditar. **Por qué además la comparación.** Nunca se confía ciegamente en un hash
para dejar de contar evidencia. Con SHA-256 no debería haber colisiones, pero si las hubiera, el
motor no fusionaría dos observaciones distintas: una prueba lo provoca con una huella pobre.

**Por qué un texto canónico explícito.** La huella no puede depender de la configuración del servidor
ni de quién la calcula: los importes van con dos decimales y el cero sin signo, las fechas en un
formato fijo, y el `float8` de `Porcentaje_Comision` con el texto que PostgreSQL le da con
`extra_float_digits = 1`, que el motor fija en su transacción y que la versión de Python reproduce.
**Por qué el despacho y la cartera dentro.** Dos pagos idénticos de dos carteras no son el mismo hecho.

**Por qué la llave histórica solo como heurística.** Es la llave con que el sistema anterior juntaba
pagos. En los archivos reales que se revisaron no colisionó, pero eso no demuestra que nunca junte dos
pagos legítimos: aquí solo dice que unas observaciones *podrían* ser el mismo pago, y nunca fusiona
nada por sí misma.

## 88. Duplicado exacto, coincidencia ambigua y un representante por contenido

**Decisión.** Un grupo de observaciones con la misma firma exacta, validado campo por campo, funda
**un** movimiento: su representante es `MOVIMIENTO_PRIMARIO`, `REVERSO` o `POSIBLE_REVERSO` según el
tipo del movimiento, y las demás son `DUPLICADO_EXACTO`, apuntando al mismo movimiento. Si el grupo de
la llave histórica junta firmas distintas, **todas** sus observaciones son `COINCIDENCIA_AMBIGUA`, sin
movimiento, incluidas las copias exactas entre sí, y su importe se reporta aparte
(`importe_ambiguo_observado`). El representante es la observación de menor (SHA-256 del archivo
original, fila de la fuente).

**Por qué un duplicado exacto cuenta una vez.** Las 23 columnas iguales, en la misma cartera, son
evidencia fuerte de que es el mismo evento reportado otra vez. La lógica no supone pares: tres copias
son un grupo de tres, y cada una conserva su resultado.

**Por qué una coincidencia ambigua no se resuelve.** No hay evidencia de si son uno o dos pagos:
contarlas dos veces inflaría la recuperación, y contarlas una podría perder un pago legítimo. Elegir
sería inventar. Por eso no fundan movimiento, quedan con los campos que las distinguen y su importe
se informa sin entrar en ninguna recuperación. Las copias exactas dentro de un grupo ambiguo también
son ambiguas: si una parte del grupo fundara un movimiento y la otra no, el motor estaría eligiendo.
Dos pagos legítimos iguales del mismo día, a otra hora, son otra llave y dos movimientos.

**Por qué el representante por contenido.** El primero que llegó dependería del orden de ingesta, y
el de menor id saldría de un identificador sorteado: los dos darían otro representante al
reconstruir. El SHA-256 del archivo y la fila salen de la evidencia misma.

## 89. Reversos: solo la pareja aislada

**Decisión.** Un negativo es `REVERSO` solo si forma una **pareja aislada** con un pago: mismo
despacho, cartera y cliente, el mismo importe absoluto, el negativo recibido entre el instante del
pago y 30 días después; el pago es el único original posible del negativo, el negativo el único
reverso posible del pago, y los dos son grupos limpios (un grupo ambiguo cuenta como un candidato que
no lo es). Entonces el reverso apunta a su original y el pago queda anulado. Cualquier otro negativo
es `POSIBLE_REVERSO`, con su motivo: `SIN_CANDIDATOS`, `VARIOS_CANDIDATOS`, `CANDIDATO_AMBIGUO` u
`ORIGINAL_DISPUTADO`.

**Por qué.** pagos/v1 no trae ninguna columna que diga cuál es el original de un reverso, y
`Concepto_Cálculo` es texto libre sin catálogo: leerlo sería inventar uno. Lo único que hay es el
cliente, el importe y el tiempo. Con un solo candidato en cada sentido no hay nada que elegir; con
dos, cualquier elección sería una asociación forzada. El motor no elige: el negativo resta de la neta,
porque su signo y su importe son ciertos, pero no anula ningún pago.

**El costo.** Un reverso real con dos pagos posibles queda como posible reverso, y la bruta sigue
contando los dos pagos. Es el error conservador: la neta lo descuenta igual. La ventana de 30 días no
tiene evidencia empírica (no hay reversos reales para medirla); es una regla explícita de `v1`, y
cambiarla es otra versión.

## 90. El signo económico y una recuperación que no es un ledger

**Decisión.** El signo lo decide solo `Recuperación_por_Gestión`. La **recuperación bruta
interpretada** es la suma de los `PAGO` que ningún reverso anuló; la **neta**, la bruta más los
`POSIBLE_REVERSO`. Un `REVERSO` y el pago que anula suman cero. Los duplicados exactos, las
coincidencias ambiguas y los no conciliados no suman. Se calculan por ventana, en la ejecución, y por
cuenta, en la Cuenta 360, sobre las interpretaciones vigentes.

**Por qué esos nombres.** No son contabilidad: el acreedor carga intereses, cargos, condonaciones y
ajustes que ninguna fuente trae, y el saldo oficial sigue siendo el que observa `SnapshotCuenta`. Por
eso se llaman *interpretadas*, cada respuesta que las trae lo advierte, y ninguna se usa para
calcular un saldo. `Captación`, `Cobranza_Total` y `Cargos_Automáticos` se conservan pero no se usan:
suponer cómo se relacionan con el importe sería inventar una regla. En el escenario sintético, donde
el generador sí descuenta cada pago del saldo, una prueba comprueba que el `SALDO` de cada cuenta
evoluciona exactamente con la suma de sus movimientos interpretados, y que con las observaciones sin
deduplicar no cuadraría.

## 91. La conciliación es otro eje, y es versionada

**Decisión.** Cada movimiento y cada resultado tiene un estado de conciliación, aparte de su
clasificación: `CONCILIADO_CUENTA`, si cuando se interpretó había una `CuentaCanonica` con su
despacho, cartera y `CLIENTE_UNICO` (la identidad de v0.7, sin lógica nueva), o
`SIN_CUENTA_OBSERVADA`, si no. Un pago sin cuenta se interpreta igual, se conserva y no crea una
cuenta. Los clientes de la ventana que tenían cuenta entran en la firma de entrada.

**Por qué otro eje.** Un pago sin cuenta puede ser primario, duplicado o ambiguo: si
`SIN_CUENTA_OBSERVADA` fuera una clasificación, habría que elegir entre dos verdades.

**Por qué versionada.** Si después llega un corte que trae al cliente, la ejecución anterior no se
modifica: decía lo que se sabía entonces. `backfill-motor-pagos --reconciliar` encuentra las ventanas
cuya interpretación vigente tiene movimientos sin cuenta que ya la tienen y abre otra ejecución, con
otra firma de entrada, que publica la interpretación conciliada. Sobrescribir la anterior borraría
la historia de la interpretación.

## 92. El contexto temporal, al consultar

**Decisión.** Dónde cae un movimiento entre los snapshots de su cuenta (el anterior, el siguiente,
antes de la primera observación, después de la última, durante una ausencia) se calcula al consultar,
en un núcleo puro (`motor_pagos/contexto.py`), con los cortes de la cartera y los de la cuenta, como
los eventos de presencia de v0.7 (78). No se guarda. El corte del mismo día que el pago cuenta como
anterior: la fecha de un corte no tiene hora, y `v1` no supone a qué hora se toma.

**Por qué al consultar.** Un corte que llega tarde cambia el contexto de los pagos ya interpretados;
guardado, obligaría a reescribirlos o a interpretar otra vez cada ventana. Calculado, sale igual que
si el corte hubiera llegado a tiempo, y cuesta leer los cortes de la cartera y los de una cuenta, que
ya tienen índice. **Por qué no es un error.** Un pago antes de la primera observación de su cuenta, o
mientras faltaba, es información. Con el snapshot anterior y el siguiente se mira *saldo antes →
pago → saldo después*, sin afirmar que la diferencia la causó el pago.

## 93. movimiento_id: UUID v8 determinista, único por ejecución

**Decisión.** `movimiento_id` es un UUID versión 8 con nombre y SHA-256 (RFC 9562, apéndice B.2):
los primeros 128 bits de `SHA-256(espacio + "movimiento:" + versión + ":" + hex(firma_exacta))`, con
sus bits de versión y variante, en un espacio de nombres propio. Se calcula en SQL, dentro del
`INSERT ... SELECT` de los movimientos, y en Python como referencia; una prueba fija el vector del
RFC. Es único por ejecución (`uq_movimiento_ejecucion`), no global. `motor_pagos_run_id`, en cambio,
es aleatorio: identifica un intento.

**Por qué determinista.** El mismo hecho, con la misma versión, tiene el mismo identificador al
reconstruir, en cualquier orden de ingesta y en cualquier base: un cliente de la API puede guardarlo.
Sale de la firma exacta del grupo y no de una observación, así que no depende de cuál copia llegó
primero; y como la firma lleva la fecha, un hecho pertenece a una sola ventana. Otra versión del
motor da otros identificadores, porque es otra interpretación. **Por qué v8 y no v5**, como los de
v0.7: PostgreSQL no trae SHA-1 sin una extensión, y el motor genera millones de identificadores dentro
de la base; la propiedad que importa es la misma.

**Por qué único por ejecución.** Cada interpretación de una ventana es completa e inmutable: el mismo
hecho aparece en cada ejecución que lo interpretó, con el mismo identificador. `GET
/movimientos/{movimiento_id}` devuelve el de la interpretación vigente; si la vigente ya no lo funda
(una observación nueva lo volvió ambiguo, por ejemplo), el de la ejecución más reciente que lo
publicó, con `vigente: false`.

## 94. Firma de entrada, ejecución vigente e historia de interpretaciones

**Decisión.** La firma de entrada de una ejecución es el SHA-256 de lo que leyó, en forma canónica:
la versión, la ventana, el SHA-256 de cada archivo original con pagos en la ventana, cada pago del
contexto (por su archivo y su fila) y cada cliente de la ventana con cuenta canónica. Índices únicos
parciales: a lo más una `EXITOSA` por ventana, versión y firma (`ux_ejecucion_motor_pagos_exitosa`), y
a lo más una `EN_PROCESO` por ventana y versión (`ux_ejecucion_motor_pagos_en_proceso`). Una ejecución
que encuentra su firma ya publicada termina `FALLIDA` con `YA_INTERPRETADA`, nombrando a la que ganó.
La **vigente** de una ventana es su `EXITOSA` más reciente con la versión que se pide; las demás son
historia y no cambian.

**Por qué una firma.** Hace que la idempotencia sea una propiedad de los datos y no de la
orquestación: dos ejecuciones con las mismas entradas no pueden publicar dos veces, aunque la cola
entregue un trabajo dos veces o alguien abra otra a mano. No depende de identificadores sorteados: un
archivo se nombra por su SHA-256, y un pago observado no cambia ni se borra, así que los pagos de un
archivo en una ventana son siempre los mismos.

**Por qué la vigente se calcula y no se marca.** Marcarla obligaría a modificar la ejecución anterior
cada vez que se publica otra. Calcularla es agrupar las ejecuciones, que son pocas (una por mes de
cada cartera, y sus reinterpretaciones), y como solo hay una `EN_PROCESO` por ventana a la vez, la
más reciente es la de mayor id. La API dice en cada ejecución y en cada movimiento si es vigente, y
`GET /motor-pagos` lista la historia.

## 95. Por conjuntos, todo o nada, con un núcleo puro que lo verifica

**Decisión.** La interpretación de una ventana es una transacción: lee la ventana y su contexto, con
sus huellas, a una tabla temporal con un `INSERT ... SELECT`; agrupa por firma exacta y por llave
histórica, elige representantes con `DISTINCT ON`, compara las copias campo por campo y busca las
parejas de reverso con `CREATE TEMP TABLE ... AS` e índices temporales; inserta los movimientos y
los resultados con `INSERT ... SELECT`, con sus motivos armados en SQL; cuenta lo publicado desde las
tablas, comprueba que cada observación tenga su resultado y que cada movimiento lo funde una
observación, y cierra `EXITOSA`. Las tablas temporales son `ON COMMIT DROP`. Las mismas reglas están
escritas en Python puro (`motor_pagos/reglas.py`), que no toca la base.

**Por qué por conjuntos.** Ninguna observación pasa por Python: con un millón de pagos por ventana, un
objeto por fila no escala, y agrupar, unir, ordenar y contar es lo que PostgreSQL hace bien. No hay
`COPY` porque nada viene de fuera de la base: las observaciones ya están en PostgreSQL.

**Por qué todo o nada.** La interpretación corre en un savepoint: si algo falla, se revierte todo lo
de ese intento y la ejecución queda `FALLIDA` en la misma transacción, sin soltar su fila, así que
nadie la puede reusar entre el fallo y su cierre; ningún camino de error degrada un estado terminal.
Un error transitorio de la base (la conexión, un interbloqueo, una cancelación) no es una conclusión
del motor: no marca nada, la ejecución sigue `EN_PROCESO` y la cola la reintenta. Nunca quedan
500,000 pagos clasificados de 700,000. Las pruebas inyectan fallas antes del staging, después
de clasificar, después de crear los movimientos, antes de insertar los resultados y antes de cerrar.
Además, la base lo exige: `ck_motor_pagos_publicacion` no deja cerrar `EXITOSA` sin un resultado por
observación leída, y `ck_motor_pagos_movimientos` sin que cada movimiento tenga quien lo funde.

**Por qué el núcleo puro.** El SQL escala pero se lee mal; el Python se lee y se prueba caso por caso,
pero no escala. La prueba de equivalencia genera pagos al azar (copias, coincidencias de la llave
histórica, ceros, negativos con uno o varios originales posibles y pagos sin cuenta, en varios
archivos y varios meses), los interpreta archivo por archivo y compara cada ventana vigente con lo
que concluye el núcleo sobre todos los pagos de la cartera a la vez, observación por observación y
movimiento por movimiento: son las mismas reglas, escritas dos veces.

## 96. Se abre con la historia de sus pagos, va al final de la cola y no bloquea la operación

**Decisión.** Un tipo de trabajo nuevo, `MOTOR_PAGOS`, con la cola durable de siempre (lease, latido,
reintentos, recuperación, varios workers, entrega al menos una vez). La historia que publica los
pagos observados de un archivo abre, **en su misma transacción**, la ejecución `EN_PROCESO` de cada
ventana que esos pagos tocan, y la de cada ventana vecina cuyo contexto reciben, con su trabajo
(`abrir_por_dataset`); si la ventana ya tiene una `EN_PROCESO` que no ha empezado, la reusa, y si un
worker la está interpretando, espera a que termine y abre otra. Las aperturas de una misma cartera
van una a la vez, con un bloqueo consultivo hasta el commit. En la cola, el motor va al final:
primero lo operacional, después `HISTORIA`, después `MOTOR_PAGOS`. No es parte del flujo.

**Por qué con la historia.** Como en 79: si se abriera después, habría un instante con pagos
observados y sin una interpretación pendiente que los lea, y una caída en ese instante los dejaría
fuera. Abierta en la misma transacción, la interpretación existe exactamente cuando la historia queda
`EXITOSA`. El motor lee `PagoObservado`, nunca la ingesta ni el Parquet.

**Por qué reusar, esperar y serializar por cartera.** Reusar una `EN_PROCESO` que no ha empezado hace
que una ventana se interprete una vez con todos sus archivos pendientes, y no una vez por archivo; y
esperar a la que se está interpretando garantiza que ningún pago quede fuera de la interpretación
vigente. Sin el bloqueo por cartera, dos archivos que llegan a la vez podrían decidir, cada uno sin
ver los pagos del otro, que una vecina no cambia, cuando sí cambia con los dos.

**Por qué al final de la cola, y fuera del flujo.** `decision/v1`, `territorial/v1` y `ruteo/v1` no
leen los movimientos, y una falla del motor no debe detener la operación del día. Con los `HISTORIA`
pendientes antes que el motor, los archivos de un mismo mes se publican primero y la ventana se
interpreta una vez.

**Por qué la apertura lee los pagos del dataset a una tabla temporal.** Los acaba de publicar la
misma transacción, así que las estadísticas de `pago_observado` todavía no los conocen y el
planificador estima una fila. Con esa estimación, el contexto de una vecina se unía recorriendo todos
los pagos del dataset por cada negativo: en el benchmark XL, la historia de un archivo de 262,997
pagos tardó 107.1 s, de los que 86.6 s fueron la apertura (84.1 s una sola vecina). La apertura los
lee una vez a `mp_apertura`, con su índice y su `ANALYZE`, y cualquier plan que elija es lineal: otro
archivo del mismo tamaño se historió en 21.7 s, con 1.27 s de apertura. La condición es la misma: las
dos versiones de la consulta dan lo mismo en las 70 combinaciones de dataset y ventana de esa base.

## 97. Solo publica el dueño vigente de su trabajo

**Decisión.** El worker deja en un `ContextVar` el trabajo que está ejecutando (`cola.en_curso`).
Antes de confirmar la interpretación, y antes de marcarla `FALLIDA`, la ejecución bloquea la fila de
su trabajo y comprueba que siga siendo de su worker (`cola.confirmar_dueno`). Si no, levanta
`TrabajoAjeno`: se revierte todo, no publica ni falla nada, y el worker no cierra el trabajo.

**Por qué.** Con entrega al menos una vez, un worker que se quedó sin lease mientras calculaba (una
pausa larga, una máquina lenta) puede terminar después de que otro tomó su trabajo. La ejecución
bloqueada ya impedía que los dos publicaran a la vez, pero no que publicara el que perdió el trabajo:
en v0.8 eso queda prohibido, como desde v0.5 lo está cerrar un trabajo ajeno. El que tomó el trabajo,
que estaba esperando la fila de la ejecución, la interpreta entera. Con la fila del trabajo bloqueada
entre la comprobación y el commit, nadie puede tomarlo en medio. Una prueba lo recorre con dos
workers y PostgreSQL real.

## 98. Índices medidos, sin particionado; backfill por ventanas; y qué no resuelve v0.8.0

**Decisión.** Dos índices nuevos sobre `pago_observado`, que la interpretación necesita para leer una
ventana sin recorrer la historia: `(despacho_id, cartera_id, fecha_recepcion)` y el mismo, parcial,
solo para los negativos (`WHERE recuperacion_por_gestion < 0`), que son pocos y deciden el contexto
de los reversos. En las tablas nuevas, solo los de las consultas que existen: la llave primaria de
los resultados (que también es la de una ventana), los resultados de un movimiento, los movimientos
de una ejecución (el único), un movimiento por su `movimiento_id` en cualquier interpretación, y los
de una cuenta por cliente y fecha de recepción. Las huellas no tienen índice persistente: la
interpretación agrupa por ellas en tablas temporales con índices temporales, y un grupo se busca por
el cliente, que su llave histórica contiene. No hay particionado. El backfill es por ventanas, fuera
de Alembic, como el de la historia (83).

**Por qué así.** En el benchmark XL, con 3,010,973 pagos observados y 8 millones de movimientos y de
resultados en 9 interpretaciones, cada sentencia de una consulta de una cuenta o de un movimiento
entra por uno de esos índices y se resuelve en menos de 0.25 ms dentro de PostgreSQL; la consulta
entera, en 4 a 9 ms (mediana) desde el servicio. La interpretación lee una ventana de un millón de
pagos por el índice de recepción y su contexto por el de negativos, que ocupa 1 MiB. Un índice más se
midió y se descartó: `(ejecucion_motor_pagos_id, fecha_recepcion DESC, movimiento_id)` bajaba los
posibles reversos de un día de toda la cartera de 1,991 a 70 ms, pero no la primera página sin
filtros (que cuenta los 3 millones de movimientos vigentes), y escribir 250,000 movimientos tomó de
2.3 a 2.9 veces más con él, en una prueba A/B que se revirtió; cada llegada tardía reinterpreta meses
enteros. Las consultas de toda la cartera o de una ventana sin el cliente (de 1.6 a 4.9 s de mediana)
quedan así, medidas. Una sí se corrigió, sin índice nuevo: la primera página de los resultados de una
ventana ordenaba todos los pagos observados de la cartera (3.9 s de mediana, hasta 51 s); ahora lee
los de la ventana por su índice de recepción y se detiene al llenarla (77 ms).

**Por qué sin particionado.** Ninguna consulta de una cuenta o de un movimiento recorre una tabla
grande. Lo que cuesta es escribir una interpretación completa: de 5 a 7 minutos y de 1.8 a 2.2 GB
de WAL por mes XL, el 70 a 80 % insertando sus movimientos y sus resultados, y particionar no lo
reduce. Donde podría pagar es en la retención de las interpretaciones que ya no son vigentes, que
crecen unos 0.6 GB por cada mes XL que se reinterpreta: quitar particiones enteras en lugar de
borrar filas. Esa política es de la operación, con datos de producción (v0.18).

**Por qué el backfill por ventanas.** Lo que se interpreta es una ventana, no un pago: encolar un
trabajo por pago observado serían millones de trabajos, y por archivo, interpretaciones que se
pisan. El backfill agrupa los pagos observados por ventana y compara cada una con su vigente;
contar basta para saber si leyó todos sus pagos, porque un pago observado no se borra. Contar no
basta para lo demás: una ventana se vuelve a abrir también cuando cambia su contexto o su
conciliación, y si esa reinterpretación falla, la vigente sigue sin verlo aunque cuente bien. Por
eso el backfill también encuentra las ventanas cuya última ejecución, posterior a la vigente,
quedó `FALLIDA` (salvo `YA_INTERPRETADA`, que dice que no cambió nada), las reintenta con
`--reintentar-fallidas`, y el CI exige que no haya ninguna.

**Lo que v0.8.0 no hace**, a propósito: la atribución de cada pago a una gestión y el
`GestorCanonico`, que necesitan el lifecycle de cobranza de v0.9 (la interfaz futura cuelga del
`movimiento_id`); resolver las coincidencias ambiguas o leer `Concepto_Cálculo`, `Captación` y
`Cobranza_Total`, que necesitan reglas con evidencia; un saldo contable o un ledger, que ninguna
fuente permite; la conciliación automática de los pagos sin cuenta cuando llega su corte (la hace
`--reconciliar`); reinterpretar solo lo que cambió, porque una ventana se interpreta siempre entera
(una llegada que cambia el contexto de una vecina sin cambiar ninguna de sus conclusiones también la
publica otra vez: en el benchmark, 3 de 6 reinterpretaciones); el Decision Engine v2, de v0.10; y
analítica, scores, ML, geografía real, nube ni multitenancy.

*Desde v0.9.0*, la atribución existe, conservadora y sin causalidad, colgada del
`movimiento_id` de la interpretación vigente (106 a 108), y el `GestorCanonico` sigue sin
existir: quien hizo una gestión es una referencia opaca (109).

## 99. El lifecycle es una tercera verdad, no una tercera fuente oficial

**Decisión.** Lo que la cobranza hizo con cada cuenta se registra como eventos operacionales
(`EventoLifecycle` y su detalle: `GestionCobranza`, `VisitaCampo`, `PromesaPago`,
`ConvenioCobranza`, `CuotaConvenio`), en un dominio propio, `OPERACIONAL`, junto a los otros dos:
`FUENTE` (lo que dicen CARTERA y PAGOS, tal como llegaron) y `ECONOMICO` (los movimientos que
interpreta el motor de pagos). Las fuentes oficiales siguen siendo dos. Un evento operacional lo
registra Motor Cartera, por su API o por una importación sintética, con origen `API` o
`IMPORTACION`, y ninguno de los dos es una fuente oficial del acreedor.

**Por qué.** Un motor de decisión que no sabe qué se hizo con una cuenta no puede aprender nada: solo
reacciona al corte. Pero lo que la operación declara que hizo no es evidencia del acreedor, y
mezclarlo con las fuentes borraría la frontera entre lo observado y lo declarado. Separado, cada
dato dice de dónde viene: la línea de tiempo de una cuenta junta los tres dominios por tiempo de
negocio y cada elemento dice de cuál es, con su propia evidencia. Hacer del lifecycle una fuente
oficial habría obligado a darle contrato, archivo original, dataset conformado y historia, como a
CARTERA y PAGOS, para algo que nace en el propio sistema.

## 100. Los snapshots no se convierten en eventos

**Decisión.** Lo que un corte dice de la promesa o del plan de una cuenta (`ESTATUS_PROMESA_PAGO`,
`MONTO_PROMESA_PAGO`, `ESTATUS_PLAN`, `MONTO_PLAN`) se muestra en la línea de tiempo como
`OBSERVACION_EN_CORTE`, en el dominio `FUENTE_CORTE`, con su snapshot, su corte, su dataset, su fila
y su archivo original. No crea una `PromesaPago`, un `ConvenioCobranza` ni una gestión.

**Por qué.** Un estatus en un corte es lo que la fuente dijo ese día, no un hecho con fecha: no se
sabe cuándo se acordó la promesa, quién la acordó ni si es la misma de la semana anterior. Fabricar
un evento de ahí inventaría un `ocurrido_en`, duplicaría promesas entre cortes y mezclaría lo
observado con lo registrado. Se muestra lo que hay, con su linaje, y se deja que una regla futura,
con evidencia, decida si dos observaciones son la misma promesa.

## 101. Dos tiempos: cuándo pasó y cuándo se registró

**Decisión.** Cada evento guarda `ocurrido_en`, que declara quien registra con su zona horaria, y
`registrado_en`, que es `now()` de la transacción que lo registra: el reloj de la base, nunca el del
cliente. La historia de una cuenta se ordena por `ocurrido_en`; la auditoría muestra los dos. Un
evento tardío es válido; uno del futuro no: `ck_evento_tiempos` exige `registrado_en - ocurrido_en`
de al menos -5 minutos, la tolerancia para el reloj de quien registra. Un evento que se refiere a
otro no puede haber ocurrido antes que él. Los días de calendario (la fecha límite de una promesa,
la fecha de corte de una evaluación, la hora local de un pago) se convierten a instantes en
PostgreSQL, en la zona de la fuente (`MC_ZONA_HORARIA_FUENTE`), con su base de zonas.

**Por qué.** La cobranza registra tarde: una visita se captura al volver a la oficina, una llamada al
final del día. Si la historia se ordenara por registro, una gestión tardía aparecería después de un
pago al que en realidad antecedió, y la atribución diría lo contrario de lo que pasó. Si el cliente
pusiera `registrado_en`, la auditoría dependería de su reloj. Las zonas se resuelven en PostgreSQL y
no en Python porque la imagen del servicio no tiene por qué traer la base de zonas de Python, y la
base sí la tiene, la misma para todas las consultas.

## 102. Solo se agrega: las correcciones son eventos

**Decisión.** Ninguna tabla del lifecycle admite `UPDATE` ni `DELETE`: un trigger de sentencia lo
rechaza (`lifecycle_solo_agrega`). Una gestión mal registrada se anula con `GESTION_ANULADA`; una
promesa o un convenio que dejaron de valer se cancelan con `PROMESA_CANCELADA` o
`CONVENIO_CANCELADO`. Cada cierre es un evento que se refiere al original, y un índice único parcial
sobre (evento relacionado, tipo) admite a lo más una anulación por gestión, una promesa y un
convenio por gestión, y una cancelación por promesa o por convenio. El estado operativo se deriva de
los eventos al consultar, y no se guarda. La anulación de una gestión anula lo que nació de ella.

**Por qué.** Un `UPDATE` borra lo que se sabía: después de cambiar una promesa no se puede decir qué
decía antes, ni qué decisión se tomó con ella. Con eventos, lo original sigue auditable y una
consulta "como se veía en una fecha" es posible. Que lo garantice un trigger y no el código es la
misma lección de las fuentes oficiales: una regla que solo vive en la aplicación la rompe el primer
script que escribe directo en la base. Anular y cancelar son dos cosas distintas, y por eso dos
eventos: una anulación dice que el registro fue un error (y deja de contar hacia atrás); una
cancelación, que el acuerdo dejó de valer desde que ocurrió (y la promesa contó hasta entonces).

## 103. Idempotency-Key obligatoria, garantizada por PostgreSQL

**Decisión.** Toda escritura del lifecycle exige `Idempotency-Key`. Su huella es el SHA-256 de la
petición en forma canónica (tipo, recurso y datos; llaves ordenadas, instantes en UTC, importes con
dos decimales). La misma llave con la misma huella responde `200` con el recurso ya registrado y
`Idempotent-Replayed: true`; con otra huella, `409 IDEMPOTENCY_KEY_REUTILIZADA`; una llave nueva,
`201`. Un índice único sobre (despacho, cartera, llave) lo garantiza: si dos peticiones llegan a la
vez, la segunda pierde en el índice, revierte su `SAVEPOINT` y devuelve lo que registró la primera.
La importación usa la llave de cada línea, con el mismo índice.

**Por qué.** Una red que corta la respuesta hace que el cliente reintente, y un evento operacional
duplicado cuenta dos veces una gestión, dos promesas o dos visitas. Revisar en el código antes de
insertar no basta con dos peticiones a la vez: lo prueba una prueba con dos hilos. `200` y no `201`
para la repetición, porque es distinguible: el cliente sabe si creó algo o si ya existía, sin dejar
de recibir el mismo cuerpo. La huella incluye el recurso: la misma llave sobre otra cuenta es otra
petición.

## 104. Una visita es una gestión de campo con su detalle

**Decisión.** Una visita se registra con la misma ruta que cualquier gestión
(`POST /cuentas/{id}/gestiones`, `canal=CAMPO`) y un bloque `visita` con su resultado y, si se
saben, su inicio y su fin. Se guarda en `VisitaCampo`, uno a uno con su gestión. No hay
`POST /cuentas/{id}/visitas`. Sin GPS, rutas, zonas ni geocercas.

**Por qué.** Una visita es una gestión: tiene cuenta, momento, contacto y resultado, y cuenta igual
para la atribución y para el resumen de la cuenta. Dos rutas para el mismo concepto serían dos
dominios que mantener coherentes. Lo territorial (rutas reales, geocercas, jornadas) es de
v0.11–v0.13, y validar una visita contra una geografía sin esas reglas sería inventarlas.

## 105. Cumplir una promesa lo dice una evaluación versionada, a una fecha explícita

**Decisión.** Una promesa no guarda si se cumplió. Lo concluye `evaluacion-promesa/v1`, una
ejecución por cartera y fecha de corte (`as_of`, obligatoria, nunca del reloj), en un trabajo
`EVALUACION_PROMESAS`, por conjuntos y todo o nada, con firma de entrada. Los estados son
`CANCELADA`, `CUMPLIDA`, `PENDIENTE`, `NO_EVALUABLE`, `PARCIAL` e `INCUMPLIDA`, con sus motivos. Lo
compatible son los `PAGO` de la cuenta entre que se acordó y su fecha límite, sin los que un reverso
anuló hasta `as_of`. Una evaluación posterior no toca la anterior; la de una promesa que muestra la
API es la de su fecha de corte más reciente.

**Por qué.** "Se cumplió" depende de cuándo se pregunta y de qué datos había: una promesa pendiente
el día 14 está incumplida el 16, y un pago que llega tarde la vuelve cumplida. Una columna que se
actualiza perdería esa historia; una evaluación con su `as_of` la conserva y se puede reproducir.
`NO_EVALUABLE` existe porque afirmar un incumplimiento cuando los pagos de la cartera no llegan a la
fecha límite sería inventar uno. Un trabajo por cartera, y no uno por promesa: cientos de miles de
trabajos serían cientos de miles de transacciones para algo que es un `INSERT ... SELECT`.

## 106. Atribución conservadora: candidatas, ambigüedad sin elegir y una ventana que es parámetro

**Decisión.** `atribucion/v1` considera candidata de un `PAGO` a una gestión de la misma cuenta
canónica (la de la conciliación del motor de pagos), no anulada, con contacto (titular o tercero),
ocurrida antes del pago o en su instante y a lo más `ventana_dias` antes. Ninguna:
`SIN_GESTION_CANDIDATA`; una: `ASOCIACION_UNICA`; dos o más: `AMBIGUA`, con todas sus candidatas en
`CandidatoAtribucion` y sin elegir ninguna. La ventana es un parámetro de cada ejecución (30 días por
omisión, política del demo) y se guarda en ella; la zona también. Un pago anulado por un reverso se
clasifica igual y su monto va aparte; los reversos no se atribuyen.

**Por qué.** Cualquier regla que elija entre varias candidatas (la última, la primera, la más
cercana) produce un número que parece una medida y es una convención: sube el porcentaje
"atribuido" sin saber nada más. Dejar el pago `AMBIGUA` con sus candidatas dice exactamente lo que se
sabe, y deja a una versión futura usar otro modelo, con evidencia. Sin contacto no hay evidencia de
que la gestión llegara a alguien. La ventana no es una verdad de negocio: depende de la operación, y
por eso no se escribe en el código como una constante; distintas ventanas son distintas
atribuciones, cada una con su firma.

## 107. Asociación no es causalidad, y compatible no es asociado

**Decisión.** Ni el código, ni la API, ni la documentación dicen que una gestión "produjo" o
"causó" un pago. Se dice *asociado*, *candidata*, *compatible* y *observado después*, y cada
respuesta de la atribución y de la evaluación trae un `aviso` que lo explica. Son dos conceptos
distintos: un movimiento **asociado operacionalmente** (la atribución encontró exactamente una
gestión candidata) y un movimiento **compatible temporalmente** con una promesa (la evaluación lo
encontró en su intervalo). La evaluación no exige que el pago esté asociado a la gestión de la
promesa.

**Por qué.** El que un pago siga a una gestión no dice que la gestión lo provocó: el titular pudo
haber pagado de todas formas. Un sistema que lo afirma enseña a quien lo usa a creerlo, y un modelo
entrenado sobre esa afirmación aprende una causalidad que nadie midió. Mezclar asociado y compatible
haría que una promesa cumplida dependiera de que no hubiera dos gestiones antes del pago.

## 108. La atribución por ventanas, sobre la interpretación vigente, por conjuntos y sin abrirse sola

**Decisión.** Una ejecución de la atribución trabaja por ventana, como el motor de pagos: un
despacho, una cartera y un mes de recepción, sobre la interpretación vigente de los pagos de ese
mes. Lee en tablas temporales sus `PAGO` y las gestiones de sus cuentas que pudieron anteceder a
alguno; arma las parejas con una sola unión temporal; publica con `INSERT ... SELECT`, todo o nada,
con firma de entrada, en un trabajo `ATRIBUCION` de la cola durable que solo publica su dueño
vigente. No se abre sola: la piden `POST /atribuciones` y `backfill-atribucion`, que encuentra las
ventanas desactualizadas (otra interpretación de pagos, gestiones nuevas o anuladas en su rango).
Una gestión tardía publica otra ejecución; la anterior se conserva y la vigente es la `EXITOSA` más
reciente.

**Por qué.** Una consulta de gestiones por cada pago es N+1 sobre millones de pagos; la unión
temporal, por cuenta y rango, entra por el índice de las gestiones de una cuenta y no depende del
número de pagos (una prueba cuenta las sentencias con 2 y con 60 pagos). Abrirla en cada gestión
registrada serían millones de trabajos y una ventana atribuida mil veces al día; abrirla con cada
interpretación de pagos la dejaría desactualizada en cuanto llegara una gestión tardía. El backfill
hace explícito cuándo una atribución ya no corresponde a sus entradas, y contar basta porque el
lifecycle solo se agrega.

## 109. Sin GestorCanonico: `actor_ref` es una referencia opaca

**Decisión.** Quien hizo una gestión se guarda como `actor_ref`, un identificador opaco
(`[A-Za-z0-9._:-]{1,64}`), sin catálogo de personas, sin nombre, sin correo y sin relación con el
`Gestor` de `pagos/v1`. No hay `GestorCanonico`.

**Por qué.** Un gestor canónico exige reglas de identidad (quién es la misma persona en dos fuentes,
con nombres que cambian y homónimos), datos personales de empleados y una política de privacidad que
el proyecto no tiene, y no tiene por qué inventar con datos sintéticos. El `Gestor` de `pagos/v1` es
lo que la fuente dice, con su propia semántica. Una referencia opaca alcanza para agrupar y para
auditar, y se puede mapear a un catálogo cuando exista uno con reglas.

## 110. Un convenio sin ledger

**Decisión.** Un convenio guarda su monto total, su vigencia y, si se declararon, sus cuotas una por
una (vencimiento y monto). No se generan cuotas de un plazo ni de una periodicidad. Si se declaran,
suman exactamente el total y vencen en fechas estrictamente crecientes dentro de su vigencia: lo
revisa la API y, al confirmar, un trigger diferido. Ningún movimiento se aplica a una cuota: la API
muestra la recuperación observada durante su vigencia, y `evaluacion_de_cuotas` es `NO_EVALUABLE`.

**Por qué.** Aplicar pagos a cuotas exige reglas que no existen (¿un pago adelantado cubre la
siguiente cuota o la última? ¿un pago parcial se reparte?) y con varios pagos y varias cuotas hay
varias interpretaciones plausibles. Un ledger inventado parecería contabilidad y no lo sería. La
recuperación observada en la vigencia es un hecho; "cuota pagada" sería una conclusión sin regla.

## 111. La importación masiva: COPY, tablas temporales y fases, todo o nada

**Decisión.** `motor-cartera cargar-lifecycle` importa un JSONL (o `.jsonl.gz`) de eventos
operacionales sintéticos. Valida cada línea en Python con las reglas de la API y la copia con COPY a
tablas temporales; en PostgreSQL, por conjuntos, resuelve las llaves (repetidas en el archivo, ya
registradas), y registra por fases en el orden de sus dependencias: gestiones con sus visitas,
promesas y convenios con sus cuotas, cancelaciones y anulaciones. Las referencias son por llave. Con
un solo problema no registra nada y dice cada línea y por qué. Idempotente: el mismo archivo dos
veces no duplica nada.

**Por qué.** Millones de eventos fila por fila por el ORM tardarían horas y no serían todo o nada.
La API individual sí usa el ORM, porque registra un evento a la vez. Las referencias por llave dejan
que un archivo sea autocontenido sin conocer identificadores públicos que todavía no existen. Las
anulaciones van al final, y llegan al mismo estado que la API: lo que nació de una gestión anulada
queda anulado con ella.

## 112. Índices medidos, sin particionado; y qué no resuelve v0.9.0

**Decisión.** En el lifecycle, los índices de sus consultas y de sus garantías: la historia de una
cuenta por momento de negocio (`ix_evento_cuenta_ocurrido`, `ix_gestion_cuenta_ocurrido`), sus
promesas por fecha límite y sus convenios por inicio, la llave de idempotencia (única) y el evento
relacionado (único parcial), y la unión de cada detalle con su evento. En la atribución, la llave
primaria de sus resultados (que es la de una ventana), los de un movimiento por su `movimiento_id` y
la de sus candidatas. Ningún otro. No hay particionado.

**Por qué así.** En el benchmark XL, con 2,573,729 eventos cargados y 3,242,650 pagos atribuidos,
cada sentencia de una consulta de una cuenta entra por uno de esos índices y se resuelve en menos de
0.6 ms dentro de PostgreSQL; la Cuenta 360 entera, con su resumen del lifecycle y su última
atribución, responde en 42 ms (mediana) desde el servicio. Ningún plan de una consulta de una cuenta
tiene un Seq Scan sobre una tabla grande. Lo que cuesta es escribir: cargar un periodo XL (unos
230,000 eventos) toma de 42 a 207 s y escribe de 0.3 a 0.55 GB de WAL, por los índices de
`evento_lifecycle` y `gestion_cobranza` (la mitad de su tamaño); atribuir un mes de un millón de
pagos, de 2 a 4 minutos. Una consulta global sí recorre: la primera página de los pagos ambiguos de
una ventana de un millón tarda 0.5 s, y un índice por clasificación la evitaría a costa de escribir
otro índice de millones de filas en cada atribución; se deja medida.

**Por qué sin particionado.** Particionar por fecha no acelera ninguna consulta de una cuenta, que
ya entra por su índice, y obligaría a incluir la fecha en las llaves únicas: la de idempotencia
dejaría de garantizar una llave por cartera. Donde podría pagar es en la retención de las
atribuciones que ya no son vigentes (cada una escribe de nuevo todos los pagos de su ventana), que
es una política de la operación, con datos de producción (v0.18).

**Lo que v0.9.0 no hace**, a propósito: el Decision Engine v2 y cualquier feature, score o modelo
(v0.10 y después); causalidad, aporte o productividad de una gestión, de un canal o de un actor; un
`GestorCanonico`; un ledger de convenios; geografía, rutas, zonas o jornadas para las visitas
(v0.11–v0.13); resolver si dos observaciones de corte son la misma promesa; abrir la evaluación o la
atribución sin que alguien la pida; y una fuente oficial nueva: el lifecycle no lo es.

## 113. Dos relojes: `as_of` decide el día, `known_at` decide qué se sabía

**Decisión.** Una decisión v2 tiene dos entradas temporales explícitas y guardadas: `as_of`, el día
de negocio que se decide (el del corte; un día lógico termina a la medianoche siguiente en la zona
de la fuente), y `known_at`, la frontera del conocimiento, un instante con su zona. Solo cuenta lo
que pasó antes del final de `as_of` y se registró hasta `known_at`. Hay dos modos: `OPERATIVO`
decide el corte más reciente conocido, con `known_at` del reloj de la base si se omite, fijado una
sola vez al abrir y guardado para cada reintento; `BACKTEST` decide cualquier corte y exige
`known_at`. `known_at` no puede ser futuro ni anterior a que el corte existiera. El modo no entra en
la firma de entrada: no cambia nada de lo que se calcula.

**Por qué.** Con un solo reloj, decidir hoy el corte del 16 de septiembre usaría pagos, gestiones y
cortes que ese día nadie conocía: el backtest parecería mejor de lo que habría sido, y cualquier
comparación con otra política estaría contaminada. Separar cuándo pasó de cuándo se supo es lo que
el lifecycle ya hace con cada evento (ver 101); aquí se aplica a toda la decisión. Exigir `known_at`
en un backtest hace imposible olvidarlo, y ese es el error más común de un backtest: decidir el
pasado con el conocimiento de hoy. Fijarlo al abrir, y no al ejecutar, hace que la decisión no
dependa de cuándo la tome un worker ni de cuántas veces se reintente.

## 114. La frontera se asienta antes de leer

**Decisión.** Antes de leer nada, el trabajo espera a que la frontera esté asentada: que el reloj de
la base haya pasado `known_at` y que ninguna sesión cliente de la base tenga abierta una transacción
que empezó hasta `known_at` (en `pg_stat_activity`, consultado en transacciones cortas, porque
PostgreSQL lo congela dentro de una). Si no se asienta en 30 segundos, el intento termina sin tocar
la ejecución y la cola lo reintenta con su espera.

**Por qué.** `registrado_en`, `creado_en` y `terminada_en` son el `now()` de la transacción que
registra: su inicio, no su confirmación. Una gestión cuya transacción empezó a las 10:00:00 y
confirma a las 10:00:05 lleva `registrado_en` 10:00:00; una decisión con `known_at` 10:00:02 que lee
a las 10:00:03 no la ve, y una reconstrucción posterior sí. Sin esperar, el mismo par (`as_of`,
`known_at`) daría dos resultados. Una prueba pausa un registro a la mitad de su transacción y lo
demuestra. **El límite**: `pg_stat_activity` solo muestra el inicio de la transacción de las
sesiones del mismo rol, o de todas con `pg_read_all_stats`. El servicio usa un solo rol; con varios,
el del worker necesita ese permiso.

## 115. Un filtro point-in-time por dominio, y las reglas de las promesas escritas una vez

**Decisión.** Cada fuente se lee con su propio filtro sobre los dos relojes. Un evento del lifecycle
cuenta si ocurrió antes del final de `as_of` y se registró hasta `known_at`; una anulación, si se
registró hasta `known_at`, ocurra cuando ocurra; una cancelación, con sus dos tiempos; un corte, si
su fecha es hasta `as_of` y se materializó hasta `known_at`. Un movimiento es de la interpretación
de su mes vigente a `known_at` (la `EXITOSA` más reciente terminada hasta entonces) y se recibió
antes del final de `as_of`, y está anulado solo si su reverso se recibió antes de ese final. Una
atribución es la vigente de su mes a `known_at`. Las promesas se evalúan con el mismo SQL de
`evaluacion-promesa/v1` (`sql_conclusiones`), alimentado con esas entradas point-in-time.

**Por qué.** Cada dominio tiene su propia semántica temporal, y no cabe en un solo filtro. Una
anulación corrige un error de registro: la gestión anulada nunca debió contar, y tratarla como un
hecho con fecha haría que la decisión dependiera de cuándo se descubrió el error; es lo que ya hace
`evaluacion-promesa/v1`. Una cancelación sí es un hecho de negocio: la promesa valió hasta que se
canceló. Los pagos tienen un tercer reloj, el de su interpretación: un pago observado que nadie
había interpretado a `known_at` no se sabía qué era. Copiar las reglas de evaluación de promesas en
otro SQL las dejaría divergir; reutilizar las suyas con tablas temporales point-in-time garantiza
que pendiente e incumplida quieran decir lo mismo en los dos motores.

## 116. NULL no es cero: disponibilidad por mes y por ventana

**Decisión.** Un conteo del lifecycle sin eventos es 0. Una feature de pagos o de atribución es NULL
si su ventana no estaba disponible a `known_at`, es decir, si algún mes que toca no está cubierto:
queda fuera del rango de los pagos observados conocidos, tiene un pago observado conocido sin
resultado en la interpretación vigente de su mes (comprobado pago por pago) o, para la atribución,
su atribución vigente leyó otra interpretación. La ejecución guarda su `disponibilidad` mes por mes,
la API lo advierte, y una regla sobre una feature NULL no se cumple.

**Por qué.** No hubo pagos y no se sabe si hubo pagos son cosas distintas, y la política las
trataría igual si una fuera 0: una cuenta sin el archivo de pagos de la semana perdería el
modificador de recuperación reciente y subiría de prioridad por falta de datos. El lifecycle es otro
caso: lo registra Motor Cartera, así que su ausencia es información. La cobertura se comprueba pago
por pago y no comparando conteos porque una interpretación cuenta su mes entero: pagos posteriores a
`as_of` que sí vio podrían tapar uno anterior que llegó tarde y no vio. Que un NULL no active reglas
es lo conservador: la información ausente no empuja la prioridad en ningún sentido.

## 117. Tres versiones separadas, la política como datos con su hash, y sin ML ni predicción

**Decisión.** Una ejecución guarda tres versiones que cambian por razones distintas: `decision/v2`,
el motor (cómo se calcula, se publica y se explica); `features-decision/v1`, las variables (su
lista, tipo, ventana, fuente, filtro point-in-time y lo que significa su NULL), y
`decision-policy/public-v1`, las reglas, que son una estructura de datos: la base de `decision/v1`
por días de atraso (importada, no reescrita), cuatro modificadores con sus condiciones y un efecto
de −1 o +1, la escala de cuatro niveles, el acotamiento y el canal de `decision/v1`. Su definición
completa, en JSON canónico, tiene un SHA-256, `hash_politica`, que cada ejecución guarda y que entra
en su firma. Un evaluador puro la aplica a una cuenta, y el SQL que la aplica a un corte se genera
de la misma definición. Los efectos se suman sin que uno oculte a otro, y el nivel se acota. No hay
ML, ni pesos aprendidos, ni scores continuos, ni probabilidades: el vocabulario es decisión,
prioridad, regla, señal y feature, nunca predicción. El ajuste por saldo de v1 queda fuera de la
base.

**Por qué.** Con una sola versión, cambiar un umbral obligaría a decir que cambiaron las variables,
y una feature nueva haría parecer que cambió la política: dos ejecuciones dejarían de ser
comparables sin leer el código. Separadas, una regla nueva cambia la política sin fingir otras
features, y una feature nueva cambia las features sin fingir la misma política. Si la política vive
en código imperativo, su versión es una promesa que nadie verifica; con su hash, una ejecución que
dice `public-v1` y aplicó otra cosa se nota. Generar el SQL de la misma definición que usa el
evaluador puro elimina la segunda copia de las reglas, y una prueba de equivalencia lo confirma
cuenta por cuenta. Sumar los efectos, en vez de aplicar el primero que se cumpla, hace que la
explicación diga todo lo que pesó: una cuenta con un pago reciente y una promesa incumplida queda
donde estaba y muestra las dos razones. Una política sintética no debe parecer un modelo: un score
de 0.73 sugiere una probabilidad que nadie estimó, y un modelo entrenado necesitaría resultados
medidos que el proyecto todavía no tiene (v0.14 y v0.15). Esta política demuestra la capacidad del
motor; no representa una política real de un acreedor. Dejar el saldo fuera hace legible la
diferencia con v1: lo que no viene del saldo viene de los modificadores.

## 118. Lo decidido se guarda tal cual, con sus hashes, y no se reescribe

**Decisión.** Cada cuenta guarda sus 43 features, en columnas tipadas y no en un JSON, con su
`feature_hash`, y su decisión con sus niveles, sus motivos y su `decision_hash`. Las llaves son
naturales, (ejecución, cuenta), con llaves foráneas compuestas que obligan a que las features sean
de una cuenta del corte de su ejecución y a que cada decisión tenga sus features. Un trigger rechaza
todo `UPDATE` y `DELETE` sobre las dos tablas. La consulta de una cuenta vuelve a aplicar la
política a las features guardadas y dice en `verificacion` si da los mismos hashes y motivos.

**Por qué.** Recalcular las features al consultar daría otro resultado en cuanto llegara un evento
tardío: la explicación de una decisión de ayer tiene que ser la de ayer. Las columnas tipadas se
pueden filtrar e indexar, y la base valida su tipo; un JSON no. Los hashes hacen comparable una
reconstrucción sin comparar 43 columnas, y la verificación hace auditable cada decisión por sí sola.
Sin un identificador propio, cada una de las 500,000 filas de una ejecución ahorra un entero y un
índice, y la llave primaria ya es la consulta de una cuenta en una ejecución. El trigger convierte
la inmutabilidad en una garantía de la base y no en una convención del código.

## 119. Por conjuntos, todo o nada, en un trabajo durable que alguien pide

**Decisión.** Una ejecución arma en tablas temporales (`ON COMMIT DROP`) el universo, la historia,
el lifecycle, los pagos, las promesas y la atribución, y publica con dos `INSERT ... SELECT`, el de
las features y el de las decisiones; cuenta, comprueba y cierra en la misma transacción, con su fila
bloqueada y en un `SAVEPOINT`. Si algo falla, queda `FALLIDA` sin nada publicado. La ejecuta un
trabajo `DECISION_V2`, en el último nivel de la cola, que solo publica su dueño vigente. Índices
únicos parciales permiten una `EXITOSA` por firma y una `EN_PROCESO` por corte y versiones. No se
abre sola: la piden `POST /decisiones-v2` y `motor-cartera decidir-v2`.

**Por qué.** Lo mismo que en el motor de pagos y la atribución (95 y 108): 500,000 cuentas por
Python serían horas y no serían todo o nada. Va al final de la cola porque lee lo que publican todos
los demás, y su resultado no depende de cuándo se ejecute: lo fija `known_at`. Abrirla sola, con
cada corte o con cada evento, sería decidir con una frontera que nadie eligió; pedirla es declarar
`as_of` y `known_at`. Una decisión parcial sería peor que ninguna: quien la leyera no sabría qué
cuentas faltan.

## 120. En paralelo a `decision/v1`, en modo sombra

**Decisión.** `decision/v2` no reemplaza a `decision/v1`: las dos coexisten, cada una con sus
tablas, su API y sus versiones, y el flujo operacional (territorial y ruteo) sigue leyendo v1.
`comparar-decisiones` compara las dos sobre las mismas cuentas, por `cliente_unico`: coincidencias,
diferencias de prioridad con su matriz, diferencias de canal, cuántas diferencias vienen del saldo
de v1 y cuántas cuentas activaron cada modificador. Se presenta como un diagnóstico de diferencias,
no como un juicio de calidad.

**Por qué.** Cambiar el motor que alimenta el trabajo de campo sin evidencia sería apostar con la
operación. En sombra, v2 produce decisiones auditables mientras v1 sigue operando, y sus diferencias
se pueden revisar antes de decidir si conviene cambiar, y cuándo. Decir que v2 es mejor exigiría
medir resultados (qué cuentas pagaron después de cada recomendación, controlando por todo lo demás),
que es analítica de v0.14 en adelante; una coincidencia del 80% no dice cuál acierta.

## 121. Índices medidos, sin particionado; y qué no resuelve v0.10.0

**Decisión.** En `decision/v2`, los índices de sus consultas y de sus garantías: la llave primaria
(ejecución, cuenta) de las features y de los resultados, que es la decisión de una cuenta en una
ejecución; (ejecución, prioridad final, cuenta) y (ejecución, canal, cuenta), una página de una
prioridad o de un canal en el orden de la cuenta; (cuenta, ejecución), la historia de decisiones de
una cuenta; y en las ejecuciones, el corte y los dos únicos parciales (una `EXITOSA` por firma, una
`EN_PROCESO` por corte y versiones). La historia de una cuenta no se indexa por (cuenta, `as_of`):
`as_of` vive en la ejecución, y una cuenta tiene una fila por decisión, que se ordena después de
leerlas por su índice. Ninguna feature tiene índice. No hay particionado.

**Por qué así.** En el benchmark XL, decidir el corte de 465,999 cuentas tomó 199 s con 154 MiB de
memoria pico, y cada sentencia de una consulta de una cuenta o de una página entra por uno de esos
índices y se resuelve en menos de 0.8 ms dentro de PostgreSQL; por la API, de 9 a 45 ms (mediana),
con la Cuenta 360 en 45 ms. Ningún plan tiene un Seq Scan sobre una tabla grande, y una consulta de
una cuenta no recorre las 465,999 decisiones de su ejecución. Indexar las features no lo pide
ninguna consulta: se leen siempre por cuenta y ejecución, y cada índice sería otro medio millón de
entradas en cada decisión.

**Por qué sin particionado.** Cada decisión de un corte XL ocupa 513 MiB (843 bytes por resultado,
de los que los motivos son unos 590, y 311 por las features), y crece con cada corte y cada frontera
de conocimiento que se pida. Pero ninguna consulta lo necesita: todas entran por índices que
empiezan por la ejecución o por la cuenta. Donde podría pagar es en la retención, para soltar
decisiones viejas sin borrar fila por fila, y eso es una política de la operación, con datos de
producción (v0.18). Los motivos son la explicación, y se guardan completos.

**Lo que v0.10.0 no hace**, a propósito: modelos predictivos, propensión de pago, recuperación
esperada y cualquier ML (v0.15); optimización matemática (v0.16); el Territorial v2, la geografía
real, polígonos, bases, zonas, subzonas, tramos territoriales, capacidad y jornadas (v0.11 a v0.13);
OSRM y VRP (v0.13); dashboards (v0.17); cloud, multitenancy y multi-despacho (v0.18); reemplazar a
`decision/v1` en el flujo operacional; abrir `decision/v2` sola; y una política real de un acreedor.

## 122. La cartografía pública es una referencia, no una fuente de negocio

**Decisión.** La geografía se resuelve contra una **referencia geográfica pública** declarada: la
capa municipal del Marco Geoestadístico Integrado 2025 del INEGI. No es una tercera fuente oficial
del acreedor: no pasa por la API, ni por el almacén de artefactos, ni por un contrato de archivo, no
se descarga en runtime y no vive en el repositorio. Se descarga una vez de su URL, se verifica
contra el SHA-256 de un manifiesto del repositorio (`geografia/referencias.json`, con su producto,
edición, nivel, capa, licencia y fechas) y se importa con `importar-geografia` como una
**versión**, identificada por ese SHA-256 y su capa. Cada ejecución declara la versión que usa.

**Por qué.** Las dos fuentes de negocio, la cartera y los pagos, son evidencia de lo que pasó con
cada cuenta: se guardan enteras, para siempre, y su linaje llega a la fila. La cartografía es otra
cosa: un dato público, de un tercero, que cambia por ediciones y no por cortes. Meterla al almacén
la mezclaría con la evidencia, y leerla en runtime ataría cada geografía a la disponibilidad de un
sitio ajeno. El manifiesto hace que la versión importada sea exactamente la publicada: un archivo
con otro SHA-256 no es esa referencia, aunque se llame igual. Y como cada ejecución apunta a su
versión, una edición nueva no cambia ninguna geografía ya calculada.

## 123. PostGIS en la misma PostgreSQL, y la capa leída con pyshp

**Decisión.** Las geometrías viven en PostgreSQL con **PostGIS 3.5** (la imagen
`postgis/postgis:16-3.5`, la misma versión mayor de PostgreSQL de siempre): los municipios como
`MultiPolygon` y los puntos como `Point`, con índices GiST, y todo lo espacial (transformar,
validar, reparar, punto en polígono, distancia) lo hace PostGIS. La migración 0012 crea la
extensión, y bajar a la 0011 la deja instalada. El Shapefile se lee con **pyshp** (Python puro,
licencia MIT): sus geometrías se convierten a WKB en Python y se copian con `COPY` a una tabla
temporal; no hay GDAL, Fiona, Shapely ni GeoPandas.

**Por qué.** El motor ya resuelve todo por conjuntos en PostgreSQL; con PostGIS, el punto en
polígono de 500,000 cuentas también, sin sacar una coordenada a Python. Una base aparte, o un
servicio espacial, partiría la transacción todo o nada en dos. Leer la capa no necesita más que
sus bytes: GDAL traería una dependencia nativa pesada a la imagen, al CI y a cada laptop para
hacer lo que PostGIS ya hace mejor dentro de la transacción. pyshp lee el formato y nada más.
Quitar una extensión al bajar no es de una migración del proyecto: otra cosa de la base podría
usarla, y quitarla (con su `spatial_ref_sys`) es una decisión de quien administra la base.

## 124. EPSG:4326 para guardar, y el sistema de origen reconocido por sus parámetros

**Decisión.** Todo se guarda en **EPSG:4326** (WGS 84, en grados): los municipios, el punto de cada
cuenta y el de cada base. El sistema de origen de una capa se reconoce **por los parámetros** de su
`.prj` (proyección, paralelos, origen, meridiano central, falsos este y norte, elipsoide y unidad),
no por su nombre, y solo se aceptan los que el importador sabe transformar: EPSG:6372 (la
Lambert del Marco Geoestadístico) y EPSG:4326. Uno que no se reconoce se rechaza; no se adivina.
La transformación la hace PostGIS al importar, y la versión guarda su sistema de origen.

**Por qué.** Las coordenadas de la cartera vienen en grados WGS 84, y la distancia geodésica de
`territorial/v2` se calcula sobre el elipsoide desde grados: guardar en 4326 evita transformar en
cada consulta. La diferencia entre ITRF2008 y WGS 84 es de menos de un metro. Los nombres de un
WKT cambian de un programa a otro (`MEXICO_ITRF_2008_LCC`, `Mexico ITRF2008 / LCC`...), y sus
parámetros no: reconocer por nombre aceptaría un sistema distinto con un nombre parecido, o
rechazaría el correcto escrito de otra forma. Un sistema adivinado movería cada municipio sin que
nadie lo notara.

## 125. El punto es longitud, latitud

**Decisión.** Un punto es `ST_MakePoint(longitud, latitud)`: X es la longitud y Y la latitud, en
todo el código. Una prueba construye una cuenta cuyo punto cae en su municipio con ese orden y
fuera de la referencia con el contrario, y otra exige que el SQL lo diga así.

**Por qué.** Es el error más común de cualquier sistema con coordenadas, y es silencioso: un punto
invertido en México cae en la Antártida o en el océano, y la cuenta parecería fuera de la
referencia, no mal construida. Una prueba que falla si alguien invierte el orden convierte una
convención en una garantía.

## 126. La fuente y el espacio son dos hechos que se comparan, no se mezclan

**Decisión.** Cada cuenta tiene dos municipios: el que resolvió la fuente (`ESTADO_CTE` y
`POBLACION_CTE` con el catálogo del INEGI, el de su snapshot) y el **espacial** (donde cae su
coordenada). `geografia/v1` guarda los dos y su comparación: `COINCIDE`, `DISCREPA` o
`NO_COMPARABLE`. Cuando discrepan, la cuenta queda `DISCREPANCIA_MUNICIPIO`, con los dos CVEGEO y si
la clave de la fuente existe en la referencia; ninguno de los dos se impone.

**Por qué.** Los dos son observaciones de la misma fuente con errores distintos: una dirección
capturada a mano puede tener la población equivocada, y una coordenada, el GPS de otro lugar. Elegir
uno por regla sería inventar cuál falló. Guardar la comparación hace medible la calidad geográfica
de una cartera (cuántas cuentas discrepan, en qué municipios) y la deja a la vista de quien puede
corregir el dato en su origen.

## 127. Ninguna coordenada se corrige, y ninguna sale

**Decisión.** `geografia/v1` no corrige nada: no intercambia latitud y longitud, no redondea, no
mueve un punto al municipio más cercano y no usa el centroide del municipio de la fuente como
ubicación de una cuenta. Unas coordenadas que no dieron un punto en la referencia, y que al revés sí
caerían en un municipio, llevan el motivo `POSIBLE_ORDEN_INVERTIDO` con ese municipio, y se quedan
en su estado. La coordenada observada se guarda tal como vino, como evidencia, pero no sale: ni por
la API, ni por la bitácora, ni en un error, una métrica o el detalle de una ejecución.

**Por qué.** Una corrección automática convertiría una sospecha en un hecho: un orden invertido es
probable, no seguro, y un punto corregido mal enviaría una visita a otro lugar con la apariencia de
un dato observado. Un centroide no es una ubicación: dos cuentas del mismo municipio quedarían en el
mismo punto, y una distancia a una base mediría la distancia al centro del municipio. La
coordenada de una cuenta es la ubicación de una persona: guardarla sirve para auditar lo que vino en
la fuente; publicarla no le sirve a ninguna consulta de v0.11.

## 128. Fail-closed: solo una geografía `VALIDA` es confiable

**Decisión.** Siete estados: `VALIDA`, `SIN_COORDENADAS`, `COORDENADAS_INCOMPLETAS`,
`COORDENADAS_INVALIDAS` (no finitas, fuera de rango o exactamente (0, 0)), `FUERA_DE_REFERENCIA`,
`MUNICIPIO_AMBIGUO` y `DISCREPANCIA_MUNICIPIO`. Solo `VALIDA` es geografía confiable: coordenadas
utilizables, un solo municipio y ninguna discrepancia conocida con la fuente. `territorial/v2` solo
asigna esas; un punto fuera de la referencia queda `FUERA_DE_COBERTURA`, y lo demás,
`REVISION_GEOGRAFICA`. `geografia/v1` revisa los rangos aunque cartera/v2 ya los haya revisado.

**Por qué.** Enviar a campo una cuenta con una ubicación dudosa cuesta un viaje, y no enviarla
cuesta una espera: con un dato que no se sabe si es bueno, lo conservador es revisarlo. Es el mismo
principio de la ingesta (decisión 2): es preferible no producir salida a producir salida
incorrecta. (0, 0) pasa cualquier contrato de rangos y es lo que deja un sistema que no capturó la
coordenada; un punto del Golfo de Guinea no es la ubicación de ninguna cuenta de una cartera
mexicana. Revisar los rangos otra vez es defensa en profundidad: la geografía no depende de que
otro componente haya hecho su trabajo.

## 129. El borde cuenta: `ST_Covers`

**Decisión.** Un punto cae en un municipio si el municipio lo **cubre** (`ST_Covers`: su interior y
su borde). Un punto sobre el límite común de dos municipios cae en los dos y queda
`MUNICIPIO_AMBIGUO`, con sus dos CVEGEO; uno sobre el borde exterior de un municipio, en ese. Nunca
se elige uno: ni el primero, ni el de menor clave, ni el más grande. Una prueba fija los dos casos
con un punto exactamente sobre un límite.

**Por qué.** Con `ST_Contains`, que excluye el borde, un punto sobre un límite no caería en ninguno
y parecería fuera de la referencia: un falso `FUERA_DE_REFERENCIA` es peor que un `AMBIGUO`, que dice
la verdad. En el Marco Geoestadístico de 2025 hay además 942 pares de municipios vecinos cuyos
polígonos se enciman en astillas de 31.9 m² en total (el producto declara hasta un metro de
tolerancia): un punto ahí está en dos municipios, y elegir uno sería elegir por un artefacto de la
digitalización.

## 130. Una referencia se importa todo o nada, y solo se repara lo seguro

**Decisión.** Una importación registra su versión `EN_PROCESO`, copia los polígonos a una tabla
temporal, comprueba las claves (`CVEGEO` es `CVE_ENT` y `CVE_MUN`, sin repetir), que ninguno venga
sin geometría, transforma a EPSG:4326 y valida cada geometría con `ST_IsValid`. Una inválida se
repara solo si es seguro: `ST_MakeValid` con su método de omisión (el que tienen todas las versiones
de GEOS que se usan), quedándose con sus polígonos, y solo si queda válida y su área cambia menos de
0.01 %; cada reparación queda registrada en la versión. Si una no se puede reparar así, la
importación se rechaza. Los municipios se publican y la versión queda `PUBLICADA` en la misma
transacción; si algo falla, queda `FALLIDA` y sin ningún municipio. Un candado consultivo por
archivo y capa impide dos importaciones a la vez, y una versión publicada o fallida ya no cambia
(un trigger lo garantiza). Las versiones de PostGIS, GEOS y PROJ se leen en una conexión propia:
en PostGIS 3.5 con PROJ 8, `postgis_proj_version()` invalida las transformaciones que esa conexión
tenía en caché, y el siguiente `ST_Transform` falla.

**Por qué.** Una referencia a medias haría que unas cuentas cayeran fuera de un municipio que
existe. Reparar en silencio cambiaría un límite sin que nadie lo supiera; reparar sin límite de área
podría quedarse con un pedazo del municipio. El tope de 0.01 % deja pasar lo que es ruido de
digitalización (un anillo que se toca a sí mismo) y rechaza lo que cambiaría el territorio. La capa
del INEGI de 2025 no tiene ninguna geometría inválida, y las pruebas cubren las dos ramas con capas
sintéticas.

## 131. Las bases operativas son configuración versionada, inmutable y con hash

**Decisión.** Una versión de bases (`bases-operativas/v1`) trae todas las bases: código, nombre,
punto, si están activas y su cobertura. Se valida entera y **fail-closed**, con todos sus problemas
juntos, antes de escribir nada; se lleva a una forma canónica (ordenada, en Unicode NFC, con las
coordenadas a 7 decimales) y su SHA-256 es su `hash_configuracion`. El mismo contenido es la misma
versión: publicarlo otra vez la reutiliza (`200`, `reutilizada`), y el nombre de la versión no entra
en el hash. Cambiar una base es publicar otra versión, y un trigger rechaza cualquier `UPDATE` o
`DELETE` de lo publicado. Las bases no son una fuente externa: las declara quien opera.

**Por qué.** Si las bases se editaran en su lugar, una asignación de ayer apuntaría a la base de
hoy, y nadie podría reconstruir por qué una cuenta quedó donde quedó. Versionar convierte cada
cambio de la operación en una entrada declarada de cada ejecución (`bases_version_id` y su hash en
la firma). El hash canónico hace que dos documentos iguales en otro orden sean la misma versión, y
que cualquier cambio, aun de un decimal, sea otra. Validar todo antes de escribir hace imposible una
versión a medias.

## 132. La cobertura se declara, nunca se infiere

**Decisión.** Una base atiende los municipios (CVEGEO) que su configuración declara, y nada más. No
se infiere una cobertura de que el punto de la base esté cerca, ni de que caiga dentro de un
municipio, ni de un radio. Las bases inactivas siguen en su versión, con su cobertura, pero no son
elegibles: si solo una inactiva cubre un municipio, la cuenta queda `SIN_BASE_ELEGIBLE` y sus motivos
lo dicen.

**Por qué.** Qué municipios atiende una base es una decisión de la operación (contratos, permisos,
seguridad, costumbre), no de la geometría: la base más cercana a un municipio puede no atenderlo. Una
cobertura inferida cambiaría al moverse una base y asignaría cuentas a quien no las puede atender.
La cercanía solo decide entre las que ya cubren el municipio. Declarar las inactivas, en vez de
borrarlas, hace explicable un `SIN_BASE_ELEGIBLE`: había una base, y no está activa.

## 133. La base elegible inequívocamente más cercana, con una tolerancia de empate versionada

**Decisión.** `territorial-public-v2`: con geografía confiable, las elegibles son las bases activas
de la versión que cubren el municipio espacial de la cuenta. Ninguna: `SIN_BASE_ELEGIBLE`; una: esa;
varias: la **inequívocamente más cercana** por **distancia geodésica** (`ST_Distance` sobre
`geography`, el elipsoide WGS 84, en metros, al centímetro). Si otra queda a no más de la
**tolerancia de empate, 100.00 m**, de la más cercana, son indistinguibles: `AMBIGUA_BASE`, con las
bases empatadas, y no se elige ninguna. La política es una estructura de datos con su hash
(`hash_politica`), que incluye la tolerancia y entra en la firma de cada ejecución.

**Por qué.** Lo simple y explicable: una cuenta pertenece a la base más cercana entre las que pueden
atenderla, y el motivo dice a cuántos metros estaba y cuál era la siguiente. La distancia en grados
no sirve: a la latitud de Puebla un grado de longitud mide unos 105 km y uno de latitud unos 111, y
la diferencia cambiaría qué base está más cerca. La tolerancia existe porque ni la coordenada de una
cuenta ni el punto de una base son exactos: dos bases a 1,000 y 1,040 m no se distinguen con un GPS
de teléfono, y elegir entre ellas por su código, su nombre o el orden de las filas sería elegir por
algo que no es geografía (y cambiaría al renombrar una base). Cien metros no se calibró con
resultados: es un parámetro declarado y versionado, y cambiarlo es otra política con otro hash. Las
pruebas comparan cada distancia con la fórmula de Vincenty, un cálculo independiente del de PostGIS.

## 134. Pertenencia territorial, no capacidad, y en paralelo a `territorial/v1`

**Decisión.** `territorial/v2` responde a qué base pertenece cada cuenta, no si la base puede
atenderla: no usa carga, capacidad, gestores, rutas, promesas, saldo, prioridad ni `decision/v2`, y
una base puede recibir cualquier número de cuentas. La distribución por base de una ejecución
(cuántas cuentas y su distancia mediana) es un diagnóstico, no un balanceo. Corre en paralelo a
`territorial/v1`, que sigue siendo la etapa `TERRITORIAL` del flujo de cada corrida y la que lee
`ruteo/v1`; los dos contratos se conservan.

**Por qué.** Pertenencia y capacidad son dos preguntas, y mezclarlas haría que una cuenta cambiara
de base porque otra base se llenó, sin que la geografía hubiera cambiado. Separadas, v0.12 puede
construir la capacidad de campo (gestores, jornadas, cargas y partición operacional) sobre una
pertenencia estable y explicable. Reemplazar a `territorial/v1` en el flujo cambiaría el trabajo de
campo de cada corrida sin evidencia: en paralelo, `territorial/v2` produce asignaciones auditables
mientras la operación sigue como estaba, como hizo `decision/v2` con `decision/v1` (decisión 120).

## 135. Sin geocodificación de direcciones

**Decisión.** v0.11 no convierte calle, colonia ni código postal en coordenadas, ni con un servicio
externo ni con uno propio. Una cuenta sin coordenadas queda `SIN_COORDENADAS`, y nada la ubica.

**Por qué.** Geocodificar mandaría las direcciones de personas a un tercero, o exigiría un catálogo
de calles que el proyecto no tiene; y el resultado sería otra coordenada, con su propio error y su
propia calidad, que habría que distinguir de la observada. Una dirección capturada a mano en una
cartera de cobranza es, además, la parte menos confiable del registro. Si un día se geocodifica,
será una fuente nueva, versionada y con su calidad medida, no un relleno silencioso de las
coordenadas faltantes.

## 136. Una asignación apunta a su resultado geográfico por su llave natural

**Decisión.** `ResultadoGeografiaCuenta` no tiene un identificador propio: su llave es la natural,
(ejecución de geografía, cuenta). `AsignacionTerritorialV2` apunta a él por esa misma llave, con una
llave foránea compuesta (`ejecucion_geografia_id`, `cuenta_canonica_id`), en vez de un
`resultado_geografia_id`. Otras llaves compuestas obligan a que la asignación sea de la geografía y
de la versión de bases de su ejecución, y a que su base sea de esa versión.

**Por qué.** Es la misma identidad: una cuenta tiene exactamente un resultado en cada ejecución de
geografía. Un identificador propio costaría un entero y un índice más en cada una de las 500,000
filas de cada ejecución, sin responder ninguna consulta que la llave natural no responda; la llave
primaria ya es la geografía de una cuenta en una ejecución. Las llaves compuestas hacen imposible,
en la base y no solo en el código, una asignación que mezcle la geografía de una ejecución con las
bases de otra.

## 137. Por conjuntos, índices medidos, sin particionado; y qué no resuelve v0.11.0

**Decisión.** Las dos ejecuciones se resuelven por conjuntos, en una transacción y en un
`SAVEPOINT`, en trabajos durables (`GEOGRAFIA` y `TERRITORIAL_V2`, en los niveles 4 y 5 de la cola)
que solo publica su dueño vigente: el Parquet conformado del corte se copia por `COPY` (solo cinco
columnas), el punto en polígono **recorre los municipios y entra por el índice GiST de los puntos**
(PostGIS prepara cada polígono una vez y lo prueba contra todos sus puntos), y cada resultado se
publica con un `INSERT ... SELECT`. Los índices son los de sus consultas: el GiST de los municipios;
en los resultados y las asignaciones, la llave (ejecución, cuenta), (ejecución, estado, cuenta),
(ejecución, municipio, cuenta), (ejecución, base, cuenta) y (cuenta, ejecución). No hay
particionado.

**Por qué así.** En el benchmark XL, la geografía de 500,000 cuentas con la referencia oficial tomó
90.6 s, con 222 MiB de memoria pico, y `territorial/v2`, 75.5 s con 154 MiB; en las dos, la mayor
parte es escribir: publicar 500,000 filas con sus índices y sus llaves foráneas (60 y 49 s).
Recorrer los municipios contra el GiST de los puntos resolvió los 460,061 puntos del corte en 8.8 s;
cada punto contra el GiST de los municipios, en 98.7 s, porque PostGIS no prepara un polígono que
encuentra una vez por punto. Cada sentencia de la geografía o de las asignaciones de una cuenta tarda
menos de 0.3 ms dentro de PostgreSQL, y ninguna consulta hace un Seq Scan sobre los resultados ni
sobre las asignaciones. El total de una página de una ejecución publicada sale de lo que contó al
publicar: contar las 202,756 asignaciones de una base en cada página costaba 84 ms.

**Por qué sin particionado.** Cada geografía de un corte XL ocupa 173.5 MiB (364 bytes por cuenta)
y cada asignación, 212.5 MiB (446), y crecen con cada corte, cada referencia y cada versión de
bases que se pida. Pero ninguna consulta lo necesita: todas entran por índices que empiezan por la
ejecución o por la cuenta. Donde podría pagar es en la retención, para soltar ejecuciones viejas
sin borrar fila por fila, y eso es una política de la operación, con datos de producción (v0.18).

**Lo que v0.11.0 no hace**, a propósito: zonas, subzonas, tramos y microtramos de campo; capacidad
diaria, gestores, calendarios, horarios y balanceo de cargas (v0.12); rutas por calle, OSRM, TSP,
VRP y VRPTW (v0.13); el Decision Engine v3, analítica, ML y dashboards (v0.14 en adelante); cloud,
multitenancy y multi-despacho (v0.18); geocodificar direcciones; corregir coordenadas; otros niveles
de la cartografía (localidades, AGEB, manzanas); reemplazar a `territorial/v1` o `ruteo/v1`; y
abrir la geografía o `territorial/v2` sin que alguien las pida.

## 138. Campo v2 consume `decision/v2`: el universo es el canal `CAMPO`, con su prioridad

**Decisión.** Las candidatas de un plan son exactamente los resultados de la `decision/v2` declarada
con `canal_recomendado = CAMPO`, y la prioridad de cada una es su `prioridad_final`. `campo/v2` no
vuelve a decidir el canal ni la prioridad, no calcula otro puntaje y no lee la cartera, los pagos ni
el lifecycle por su cuenta. La decisión se declara por su `decision_run_id` (nunca "la más
reciente"), tiene que estar `EXITOSA` y ser del corte del plan, y su firma entra en la del plan. La
fecha de operación también se declara: ni la API ni la línea de comandos la infieren.

**Por qué.** Una sola autoridad para cada pregunta: qué cuentas van a campo y con qué urgencia es de
`decision/v2`, que ya lo explica con sus motivos y es reproducible a su `known_at`. Si `campo/v2`
recalculara la prioridad, una cuenta podría ser `ALTA` en la decisión y `MEDIA` en el plan, y nadie
sabría cuál vale. Declarar la decisión y la fecha hace que un plan histórico se pueda reconstruir:
con otra decisión u otro día es otro plan, con otra firma, y el anterior no cambia.

## 139. Campo v2 consume `territorial/v2`: la base es la suya, y la duda va a revisión

**Decisión.** La base de una candidata es la de su asignación en la `territorial/v2` declarada, que
tiene que estar `EXITOSA`, ser del corte del plan y usar la misma versión de bases que la
configuración de campo (las dos cosas se exigen en la base con llaves foráneas compuestas). Una
candidata cuya asignación no es `ASIGNADA` (geografía dudosa, fuera de cobertura, sin base elegible o
empatada) queda `REVISION_TERRITORIAL`, con el estado y los motivos territoriales, sin base, sin
unidades y sin gestor. Su punto es el de su geografía publicada: no se vuelve a calcular.

**Por qué.** Pertenencia y capacidad son dos preguntas (decisión 134), y `territorial/v2` ya
contestó la primera con su política, su hash y sus motivos. Mandar a campo una cuenta cuya ubicación
es dudosa, o elegirle una base que `territorial/v2` no eligió, sería inventar una pertenencia. La
cuenta en revisión no se pierde: sale en el plan, con su motivo, para que alguien la resuelva.

## 140. El canal no es la capacidad

**Decisión.** Una candidata sin gestor disponible o sin capacidad sigue siendo de canal `CAMPO`:
queda `SIN_GESTOR_DISPONIBLE` o `SIN_CAPACIDAD`, con su motivo, y no se manda a otro canal, ni se
mueve a otro día, ni se le baja la prioridad.

**Por qué.** Que hoy no haya quien la visite no cambia que la cuenta necesite una visita: esa es una
conclusión de `decision/v2`, con sus motivos. Si el plan la cambiara de canal, el déficit de
capacidad desaparecería de los números y la operación no sabría cuántas cuentas de campo se quedan
sin atender. Contarlas por base, con su motivo, es lo que dice cuántos gestores faltan y dónde.

## 141. Capacidad declarada, no factibilidad vial

**Decisión.** La capacidad de un gestor es su cupo declarado (`capacidad_cuentas_dia`, entre 1 y
1,000) si está disponible en la fecha, y 0 si no; una cuenta es una unidad de capacidad, y una media
jornada no reduce el cupo. El plan no usa distancias, tiempos de traslado, la red vial, la duración
de una visita ni pesos por tipo de cuenta, y cada respuesta lo dice (`aviso`).

**Por qué.** Lo que se puede declarar y auditar hoy es cuántas cuentas atiende un gestor en un día
típico: es lo que una operación ya sabe. Derivar el cupo de las horas de la jornada, o de la
distancia entre cuentas, exigiría un modelo de traslado que el proyecto todavía no tiene, y
presentarlo como factibilidad sería falso. v0.13 (Ruteo vial v2) tomará estos planes y dirá si cada
carga cabe en una ruta; mientras tanto, el plan es una carga diaria limitada por capacidad, no una
ruta.

*Desde v0.13.0*, `ruteo/v2` toma un plan por su `campo_run_id` y dice, por calle, si la carga de
cada gestor cabe en su jornada, sin cambiar el plan (decisiones 154 a 174). En el benchmark de
Puebla, con bases que cubren todo un estado, hay rutas de cientos de kilómetros y cuentas que no
caben ni solas en la jornada: es justo lo que esta decisión dejaba fuera.

## 142. La configuración de campo es versionada, inmutable y con hash

**Decisión.** Una versión de la configuración de campo (`campo-configuracion/v1`) trae, sobre una
versión de bases declarada, todos sus calendarios (con sus ventanas y sus excepciones) y todos sus
gestores. Se valida entera y **fail-closed**, con todos sus problemas juntos, antes de escribir nada;
se lleva a una forma canónica (el hash de la versión de bases, la zona, los calendarios por código
con los siete días, las excepciones por fecha y los gestores por código, sin el nombre de la
versión) y su SHA-256 es su `hash_configuracion`. El mismo contenido es la misma versión
(`reutilizada`); cambiar un cupo, un calendario o un gestor es otra versión; triggers rechazan
cualquier `UPDATE` o `DELETE` de lo publicado, y dos ventanas encimadas de un calendario.

**Por qué.** Como con las bases (decisión 131): un plan de ayer tiene que seguir diciendo con qué
gestores y qué cupos se hizo, aunque hoy sean otros. Usar el hash de la versión de bases, y no su
identificador, hace que la misma configuración sobre las mismas bases tenga el mismo hash en
cualquier base de datos. Cambiar la configuración entre dos planes es una entrada declarada distinta,
y su efecto se puede medir: las pruebas comparan un plan viejo y uno nuevo después de agregar un
gestor, de cambiar un cupo y de cambiar un calendario.

## 143. El gestor operacional no es el `Gestor` de la fuente

**Decisión.** Un gestor de campo (`GestorCampo`) es un código opaco de la configuración
(`GESTOR_001`), sin nombre, teléfono, correo, CURP, RFC ni número de empleado: ninguna llave del
documento los admite. No se liga a la columna `Gestor` de un pago observado, ni al `actor_ref` de un
evento del lifecycle, ni a ningún dato de una persona. El generador sintético inventa sus códigos.

**Por qué.** Son tres vocabularios de tres fuentes: el `Gestor` de un pago es lo que el archivo del
acreedor dice, el `actor_ref` de un evento es quién lo registró, y el gestor de campo es una unidad
de capacidad que declara quien opera. Ligarlos sin evidencia (por un nombre parecido, por ejemplo)
inventaría una identidad, y guardar datos de personas en una configuración operativa los expondría
en cada plan, exportación y respuesta de la API sin ninguna necesidad: el reparto solo necesita un
código, un cupo y un calendario.

## 144. Ventanas de jornada en hora local, con zona explícita, y excepciones que reemplazan la semana

**Decisión.** Un calendario tiene, para cada día de la semana, cero o más ventanas `HH:MM` en hora
local, sin encimarse, y excepciones por fecha (`CERRADO`, o `VENTANAS_ESPECIALES`). La jornada de un
gestor en una fecha es, en orden: `INACTIVO`; la excepción de la fecha, si la hay
(`CERRADO_POR_EXCEPCION` o `VENTANAS_ESPECIALES`); las ventanas de ese día ISO de la semana
(`JORNADA_SEMANAL`), o `SIN_JORNADA`. La zona horaria es una zona IANA explícita de la versión, que
Python y PostgreSQL tienen que conocer; nunca la del servidor. Los instantes UTC de cada ventana los
calcula PostgreSQL con su base de zonas.

**Por qué.** Una jornada se declara como la vive la operación: "de 9 a 2 y de 4 a 7, de lunes a
viernes", con varios turnos por día y festivos. Una excepción reemplaza a la semana completa, en vez
de sumarse, para que un día especial se lea en un solo lugar. La zona explícita evita que el mismo
plan dé otro resultado en un servidor con otra zona, y dejar el cambio de horario a la base de zonas
de PostgreSQL evita una tabla de horarios mantenida a mano. Las ventanas no reducen el cupo
(decisión 141): dicen cuándo, no cuánto.

## 145. La partición es una cuadrícula fija, no un agrupamiento

**Decisión.** Cada candidata asignada a una base cae en cuatro unidades anidadas: zona (base y
entidad), subzona (base y municipio), tramo (la celda de 5 km de una cuadrícula fija, dentro de su
municipio y su base) y microtramo (la celda de 1 km, dentro de su tramo). La celda de una coordenada
es `floor(round(numeric(x), 3) / T)`: el intervalo [k·T, (k+1)·T), con la coordenada al milímetro.
Cada unidad tiene un UUIDv5 de la política, el nivel, el **código** de la base y su clave. Un plan
publica solo las unidades que ocupó.

**Por qué.** Un agrupamiento (k-means, densidad, polígonos dibujados) movería las fronteras con cada
cuenta nueva, y la misma cuenta cambiaría de unidad de un día a otro sin haberse movido: nada sería
comparable entre planes. Una cuadrícula fija da a cada punto siempre la misma unidad, en cualquier
orden, máquina o reconstrucción, y se calcula sin mirar a las demás cuentas. Que un tramo no cruce un
municipio ni una base mantiene la jerarquía (cada unidad tiene un solo padre). El redondeo al
milímetro hace que PostgreSQL y Python den la misma celda en cada frontera, y usar el código de la
base en el identificador hace que una base tenga las mismas unidades en otra versión de bases.

## 146. EPSG:6372 para la cuadrícula métrica

**Decisión.** La cuadrícula se calcula en EPSG:6372 (México ITRF2008 / LCC), en metros: cada punto
guardado en EPSG:4326 se transforma una vez con PostGIS. Nunca se usan grados como metros, ni la
latitud y la longitud directamente como cuadrícula.

**Por qué.** Una celda de grados no tiene un tamaño fijo: a la latitud de Puebla un grado de
longitud mide unos 105 km y en Tijuana unos 94, así que "una celda de 0.01°" sería otro tamaño en
cada estado. EPSG:6372 es la proyección cónica conforme oficial para México en metros, con un solo
sistema para todo el país: una celda de 1,000 m mide lo mismo en Mérida que en Tijuana, salvo la
distorsión de escala de una proyección conforme entre dos paralelos (menos de 1 % en el territorio
nacional), y no hay que elegir una zona UTM por cuenta (que partiría el país en franjas con
coordenadas incompatibles).

## 147. Tramos de 5 km y microtramos de 1 km

**Decisión.** El tramo mide 5,000 m por lado y el microtramo 1,000 m: un tramo tiene exactamente
5 × 5 microtramos. Los dos tamaños son parte de `campo-public-v2` y de su hash; cambiarlos es otra
política, con otros identificadores.

**Por qué.** Un microtramo es lo que un gestor recorre a pie o en pocos minutos: la unidad que no
conviene partir entre dos gestores. Un kilómetro cuadrado agrupa las cuentas cercanas de una colonia
sin juntar barrios lejanos; uno más chico partiría las cuadras de una misma visita, y uno más grande
juntaría en un bloque cuentas que nadie visitaría juntas. El tramo de 5 km es la escala de un
recorrido de medio día, útil para leer la carga y para la v0.13. Que el tramo sea múltiplo exacto del
microtramo garantiza que un microtramo nunca quede en dos tramos. No se calibraron con el benchmark:
se declararon antes.

## 148. El propietario preferido de un microtramo, por Rendezvous

**Decisión.** El propietario preferido de un microtramo es el gestor disponible de su base con el
mayor SHA-256 de `microtramo_id|codigo` (hashing de mayor peso aleatorio, Rendezvous), sin pesos; un
empate se decide por código. El ranking completo, en ese orden, es el orden en que el bloque busca
gestor.

**Por qué.** Hace falta un dueño estable por microtramo que no dependa del orden de las filas ni de
cuántas cuentas haya. Un reparto por turnos o por módulo (`hash % n`) cambia el dueño de casi todos
los microtramos al agregar un gestor; Rendezvous solo cambia los microtramos en los que el nuevo
queda primero (en promedio 1 de cada n), y quitar un gestor solo mueve los suyos. Los planes de dos
días con un gestor de diferencia se parecen, que es lo que necesita un equipo de campo. Es
determinista (SHA-256, nunca el `hash()` de Python, que cambia entre procesos) y no necesita estado:
no hay una tabla de dueños que mantener. Sin pesos por cupo, porque un cambio de cupo movería
microtramos de todos los gestores; la capacidad ya se respeta con el spillover.

## 149. La prioridad antes que el spillover, y un microtramo entero cuando cabe

**Decisión.** En cada base, la capacidad se reparte por prioridad (`MUY_ALTA` primero) y, dentro de
cada prioridad, por bloques de un mismo microtramo: primero los de más cuentas, y entre iguales, el
de identificador menor. Cada bloque va completo a su propietario preferido; si no le cabe, completo
al primero del ranking al que sí le cabe (spillover: la cuenta queda **rebalanceada**); si no cabe
completo en ninguno, se divide en el orden del ranking, cuenta por cuenta en el orden de su
`cuenta_id`; lo que no cabe en nadie queda `SIN_CAPACIDAD`.

**Por qué.** La prioridad es la conclusión de `decision/v2`, y la capacidad es escasa: una cuenta
`BAJA` nunca debe recibir gestor mientras una `MUY_ALTA` de su misma base se queda fuera, y así sale
del orden (una cuenta queda sin capacidad solo cuando la base ya no tiene cupo). Dentro de una misma
prioridad, los bloques más densos primero, porque un gestor atiende más cuentas de una vez en un
mismo kilómetro cuadrado. Mover un bloque entero antes de partirlo conserva juntos los microtramos
que caben, y dividir solo cuando no hay otra deja el mínimo de microtramos partidos. El orden por
`cuenta_id` en una división hace que la misma entrada dé siempre la misma división. Es una regla
voraz y explicable, no una optimización matemática: cada asignación dice en su motivo si fue su
propietario preferido o el spillover, con qué lugar del ranking y si el bloque se dividió.

## 150. Sin rebalanceo entre bases

**Decisión.** Una candidata solo recibe un gestor de su base. Si su base no tiene gestores
disponibles queda `SIN_GESTOR_DISPONIBLE`, y si se le acabó la capacidad, `SIN_CAPACIDAD`, aunque
otra base tenga cupo libre. Las llaves foráneas compuestas de la asignación exigen en la base que el
gestor sea de la base de la cuenta.

**Por qué.** La base de una cuenta es una conclusión de `territorial/v2`, con su cobertura declarada
(decisión 132): mandar una cuenta a otra base porque tiene cupo libre violaría esa cobertura (puede
no tener permiso, ni seguridad, ni conocer la zona) y haría que la pertenencia dependiera de la
carga. El déficit de una base y el sobrante de otra quedan a la vista en la distribución del plan,
para que la operación decida (más gestores, otra cobertura) con una nueva versión de la
configuración o de las bases.

## 151. Sin rutas, ni orden de visita

**Decisión.** `campo/v2` no implementa rutas, ni las disfraza: no hay OSRM ni red vial, matrices de
distancia, tiempos de traslado, vecino más cercano, 2-opt, TSP, VRP, ETA ni orden de visitas, y no
usa la distancia entre cuentas para nada. Una asignación no tiene `orden_visita`: tiene
`orden_prioridad`, que es su lugar en la cola de capacidad de su base, no el orden en que se visita.
La geometría solo ubica cada punto en su celda.

**Por qué.** El orden de las visitas depende de la red vial, del tráfico y de la jornada de cada
gestor: es otro problema, con otras entradas y otra validación, y es la v0.13 (Ruteo vial v2), que
leerá estos planes por sus identificadores. Un orden aproximado con distancias en línea recta
parecería una ruta sin serlo, y la operación lo usaría como si lo fuera.

*Desde v0.13.0*, el orden de las visitas es de `ruteo/v2`, por la red vial (decisiones 154 a 174);
`orden_prioridad` sigue siendo el lugar en la cola de capacidad, no el orden de visita.

## 152. Privacidad: ni coordenadas, ni celdas, ni datos de personas

**Decisión.** Ninguna respuesta de la API, bitácora, error, exportación ni la Cuenta 360 lleva el
punto de una cuenta, su coordenada métrica, el índice de su celda, el recuadro ni el centroide de una
unidad: solo identificadores opacos. Las tablas de paso que tienen el punto son temporales de la
transacción del plan (`ON COMMIT DROP`), y un error se registra con su tipo, su SQLSTATE y sus
marcos, nunca con su mensaje. `campo/v2` no carga el nombre, la dirección ni los teléfonos del
cliente, y la configuración no tiene datos de personas (decisión 143). Las pruebas buscan llaves de
coordenadas en cada respuesta y una coordenada en la bitácora de un plan que falla.

**Por qué.** El reparto no necesita nada de eso, y lo que no se guarda ni se publica no se filtra. Los
identificadores de las unidades son deterministas, y por eso **opacos pero no secretos**: quien
conozca la política, el código de una base y su municipio podría probar celdas hasta encontrar el
kilómetro cuadrado de un microtramo. Se acepta: es la granularidad de una unidad operativa, no la
ubicación de una cuenta, a cambio de identificadores reproducibles sin un secreto que administrar. Si
un despacho necesitara otra cosa, sería otra política con identificadores con llave.

## 153. Por conjuntos, índices medidos, sin particionado; y qué no resuelve v0.12.0

**Decisión.** Un plan se resuelve por conjuntos, en una transacción y en un `SAVEPOINT`, en un
trabajo durable `CAMPO_V2` (el nivel 6 de la cola, después de `TERRITORIAL_V2`) que solo publica su
dueño vigente. PostgreSQL selecciona las candidatas por el índice de la decisión, les une su
asignación territorial y su punto, calcula la celda de cada punto en EPSG:6372 y agrupa los bloques;
Python solo recibe los bloques (uno por base, municipio, celda y prioridad), les da sus
identificadores y los reparte, y lo que decide vuelve a la base por `COPY`; cada asignación, cada
unidad y cada carga se publican con un `INSERT ... SELECT`. Ninguna fase recorre las cuentas desde
Python ni hace una consulta por cuenta, por gestor o por calendario. Los índices son los de sus
consultas: en las asignaciones, la llave (plan, cuenta) y (plan, estado, cuenta), (plan, base,
cuenta), (plan, gestor, cuenta), (plan, prioridad, cuenta), (plan, microtramo, cuenta) y (cuenta,
plan); en las unidades, (plan, unidad) y (plan, padre); en las cargas, (plan, gestor); en los
gestores, (configuración, base, activo), y en las ventanas, (calendario, día). No hay
particionado.

**Por qué así.** En el benchmark XL, el plan de las 132,529 candidatas de un corte de 500,000
cuentas, con 6,129 gestores, tomó 65.5 s con 182 MiB de memoria pico y 557 MiB de WAL; casi tres
cuartas partes son escribir (la publicación, 47.9 s), y el reparto en Python de los bloques de doce
bases, 11.7 s. Una página de asignaciones de un gestor, de una base, de un estado, de un microtramo,
de un tramo o de una zona tarda de 0.6 a 3.6 ms dentro de PostgreSQL, y los planes de una cuenta,
0.4 ms; ninguna consulta hace un Seq Scan sobre las asignaciones. Las zonas, subzonas y tramos no
tienen índice propio: entran por el de la base de su unidad (una zona, 0.7 ms; un tramo, 1.3 ms).
Agregar un gestor
cambió el propietario preferido de 2 de 8,603 microtramos; un reparto por turnos habría cambiado 796
de los 798 de su base.

El índice de las ventanas salió de la revisión, no del benchmark: con 46 ventanas no se nota,
pero cada versión de la configuración agrega todas las suyas (cambiar un cupo ya es otra
versión), y sin él las ventanas de cada carga recorrían la tabla una vez por gestor, y el
trigger, una vez por ventana insertada.

**Por qué sin particionado.** Cada asignación ocupa 617 bytes con sus índices, unos 78 MiB por plan
de un corte XL, y crece con cada fecha y cada configuración que se planeen. Ninguna consulta lo
necesita: todas entran por índices que empiezan por el plan o por la cuenta. Las cargas y las
unidades de un plan se leen con un Seq Scan mientras la tabla tiene pocos planes, porque es más
rápido (9.1 contra 13.6 ms y 4.1 contra 5.8 ms por la llave del plan), y la llave entra cuando la
tabla crece. Donde el particionado podría pagar es en la retención de planes viejos, y eso es una
política de la operación, con datos de producción (v0.18).

**Lo que v0.12.0 no hace**, a propósito: rutas por calle, OSRM, matrices de distancia, tiempos de
traslado, ETA, el orden de las visitas, TSP, VRP y VRPTW (v0.13, Ruteo vial v2); pesos por visita,
habilidades, vehículos o costos; rebalancear cuentas entre bases; emparejar cargas entre gestores u
optimizar el reparto; ligar un gestor de campo a una persona, a un `actor_ref` o al `Gestor` de los
pagos, y comparar el plan con lo que la cobranza hizo (analítica, v0.14); el Decision Engine v3, ML
y dashboards (v0.14 en adelante); cloud, multitenancy y multi-despacho (v0.18); y abrir un plan sin
que alguien lo pida.

## 154. La red vial pública es una referencia vial, no una fuente de negocio

**Decisión.** La red por la que se rutea sale de OpenStreetMap (ODbL 1.0) y es una **referencia
vial**, como la cartografía municipal del INEGI (decisión 122): no es cartera ni pagos, no entra al
almacén de artefactos y ningún motor de negocio la lee; solo la consulta el proveedor de ruteo. Una
red se declara en cada ejecución por su versión, con de dónde salieron sus datos, su licencia, su
fecha, su región y el SHA-256 de su fuente, y toda red de OpenStreetMap lleva su atribución en la
API. Las redes grandes y sus archivos preparados no entran al repositorio: el `.gitignore` los
excluye por nombre y el control de archivos trackeados los reconoce por sus primeros bytes (un
`.osm.pbf`, y un tar, que es cada archivo que prepara OSRM).

**Por qué.** Una calle no es un dato de una cuenta: es el terreno sobre el que se mide una ruta, y
cambia con cada extracto. Tratarla como una fuente de negocio la mezclaría con la evidencia de la
cobranza, y sin una versión declarada, una ruta histórica no se podría reconstruir ni explicar. La
licencia obliga a atribuir, y es lo correcto.

## 155. OSRM autoalojado: ninguna coordenada sale de la red propia

**Decisión.** El proveedor de ruteo es un `osrm-routed` autoalojado: en el compose, un servicio sin
puerto publicado que solo ven la API y el worker; fuera de él, uno que la operación levanta en su
red. Nunca el servidor público de OSRM, Google Maps ni otro servicio externo, y el motor no tiene un
modo que los use. Las peticiones van por `POST` con un cuerpo JSON, con las coordenadas en el
cuerpo y nunca en la URL; el cliente no registra URLs ni cuerpos, y sus errores no llevan la
petición ni su causa original. `osrm-routed` corre con `--verbosity WARNING`, porque su bitácora de
accesos repite cada petición.

**Por qué.** El punto de una cuenta es la ubicación de una persona: mandarlo a un tercero para
calcular una ruta sería entregarle la cartera, y un servidor público además limita el uso y no
garantiza versión ni disponibilidad. Autoalojado, el costo es preparar la red y mantener un
servicio, y se acepta. Las URLs con coordenadas son lo que más fácil se filtra (bitácoras de
proxies, trazas de excepciones, historiales): por eso no existen.

## 156. OSRM v26.9.0, fijado por su digest, con MLD y el perfil `car` tal cual

**Decisión.** El motor de ruteo es OSRM `v26.9.0`, con la imagen fijada por su digest en
`preparar.sh`, en el compose y en el CI (una prueba comprueba que es la misma en los tres lados), o
con sus binarios nativos, identificados por su SHA-256; nunca `latest`. El algoritmo es MLD
(`extract`, `partition` y `customize`), y el perfil es el `car.lua` de esa versión copiado tal cual,
con su licencia, y verificado contra un hash fijado en el código.

**Por qué.** Una ruta depende del motor y del perfil tanto como de la red: con otra versión, otra
velocidad por tipo de vía o otro manejo de los giros dan otra ruta, y la reconstrucción ya no sería
exacta. v26.9.0 porque acepta peticiones `POST` con JSON (decisión 155) y porque `--data_version`
deja que el backend declare su red (decisión 158). MLD porque prepara más rápido que CH y permite
cambiar los pesos con otra personalización; CH respondería algo más rápido, y no hace falta. El
perfil se copia y no se edita: cambiarlo sería otra versión de la red.

## 157. La versión de la red es su huella; los archivos preparados son evidencia

**Decisión.** Una versión de la red la identifica su **huella**: el SHA-256 de su identidad (el
SHA-256 de la fuente, el motor, su versión y su distribución exacta, el algoritmo, el perfil y su
hash, y los comandos de la preparación). El SHA-256 de cada archivo preparado va al **manifiesto**,
y el hash del manifiesto entra en la firma de cada ejecución, pero no en la huella. Una versión nace
`EN_PROCESO`, queda `PUBLICADA` o `FALLIDA`, y ya no cambia ni se borra; una huella se publica una
sola vez.

**Por qué.** OSRM no produce los mismos bytes en dos preparaciones de la misma fuente (con el
extracto de Puebla, 12 de sus 26 archivos cambian, entre ellos `.osrm.partition`, aun con un solo
hilo). Si la identidad fueran los archivos, la misma red tendría una versión por preparación. Lo que
responde la red es lo mismo en lo que la política usa: con 300 puntos al azar en Puebla, dos
preparaciones dieron los mismos snaps, la misma conectividad y las mismas duraciones en las 30,000
celdas de sus tablas; las distancias difirieron en 0.1 m en 214 celdas, y al redondear a metros eso
puede cambiar un metro de un tramo. Por eso la huella es de las entradas (la misma red es la misma
versión en cualquier máquina), el manifiesto conserva qué se preparó, su hash entra en la firma, y
`registrar-red-vial` dice si un directorio es la preparación publicada u otra de la misma red: la
secuencia, las horas y los estados se reconstruyen con cualquiera, y byte por byte, con la
publicada.

## 158. El backend declara su red, y se verifica antes de enviar un punto

**Decisión.** La preparación graba la huella en la red (`osrm-extract --data_version
motor-cartera/red-vial/v1:<huella>`), y `osrm-routed` la declara en cada respuesta. Antes de enviar
el punto de una cuenta o de una base, el motor consulta `nearest` de (0, 0), un punto que no es de
nadie: si el backend declara otra red, la ejecución queda `FALLIDA` por
`RED_VIAL_BACKEND_INCOMPATIBLE` sin haber enviado nada. Cada respuesta posterior se verifica igual.

**Por qué.** La red declarada en la ejecución y la que sirve el backend son dos cosas, y la segunda
la decide quien levanta el servicio. Sin esta verificación, un backend con otra red respondería con
toda normalidad, y la ejecución publicaría rutas sobre una red que no es la que dice. Verificar con
un punto que no es de nadie evita mandarle la ubicación de una cuenta a un backend equivocado.

## 159. Un snap que no depende de la petición: `--small-component-size 1`

**Decisión.** La red se prepara con `--small-component-size 1`.

**Por qué.** Con el valor por omisión (1,000), OSRM distingue los componentes chicos de la red, y
en una petición `table` prefiere ajustar cada punto a un componente grande si alguno de los demás
puntos lo está: el snap de un punto depende de qué otros puntos van en la petición. La matriz de un
gestor cambiaría según cómo se parta en bloques, o según quién más va en su ruta, y la
reconstrucción dejaría de ser exacta. Con 1, cada componente cuenta como grande y cada punto tiene
el mismo snap en cualquier petición; se comprobó que la matriz de la red sintética es la misma en un
bloque que en bloques de 2 × 2. El costo es que un punto puede quedar en un componente aislado, y
eso es justo lo que `RED_DESCONECTADA` dice (decisión 161).

## 160. El snap hasta 500.00 m, al centímetro, sin calibrar

**Decisión.** Una parada o una base se acepta si el segmento de la red más cercano está a no más de
500.00 m, redondeado al centímetro (`ROUND_HALF_UP`): 500.00 m sí, 500.01 m no. Más lejos, o sin
segmento, es `NO_ENRUTABLE` (`SNAP_EXCESIVO` o `SIN_SNAP`), con su distancia; una base así no tiene
ruta. El umbral es parte de la política y de su hash.

**Por qué.** Un punto a cientos de metros de cualquier calle no se puede visitar «en coche hasta la
puerta», y su ruta mediría un lugar que no es el de la cuenta. 500 m no se calibró con resultados:
es lo que se puede caminar desde el coche en unos minutos. En el benchmark de Puebla, con puntos
sintéticos al azar dentro de cada municipio (no domicilios), la mediana del snap fue de 84 m y el
22 % de las paradas quedó a más de 500 m, en el monte: es lo que esos puntos son, y el umbral no se
movió por eso. Redondear al centímetro antes de comparar quita la duda de un flotante en el borde.
Con datos de una operación, otro umbral sería otra política.

## 161. Sin línea recta: lo que la red no dice no se inventa

**Decisión.** Si la red no tiene camino de la base a una parada o de la parada a la base, la parada
es `NO_ENRUTABLE` por `RED_DESCONECTADA`. Una celda vacía de la matriz nunca se rellena con la
distancia en línea recta, una velocidad supuesta ni otro tramo, y las duraciones y distancias de
OSRM se redondean a enteros una sola vez, al leerlas.

**Por qué.** Una ruta con un tramo inventado parece factible sin serlo: el gestor llegaría a una
calle sin salida, o a un río. Si la red no lo sabe, la respuesta honesta es que no es enrutable, y
el motivo dice dónde mirar (un punto mal ubicado, o una red incompleta).

## 162. Un fallo técnico no es una parada no enrutable

**Decisión.** Si el proveedor no responde, responde un error del servidor, corta la conexión o
manda una respuesta incompleta, el cliente reintenta con espera exponencial y un tope; si sigue
fallando, la ejecución no publica nada y queda `EN_PROCESO`, para que la cola la reintente con su
espera hasta agotar sus intentos (`INTENTOS_AGOTADOS`). Un error de configuración (la petición o el
servidor no son los esperados) no se reintenta. Un worker cuyo proveedor no responde no toma
trabajos `RUTEO_V2`.

**Por qué.** `NO_ENRUTABLE` es lo que la red dice de un punto. Si una caída de OSRM se publicara
como paradas no enrutables, una ejecución quedaría `EXITOSA` con miles de cuentas sin ruta, y nadie
distinguiría un punto en el monte de un servicio caído. Mejor no publicar y reintentar: las
entradas son las mismas, y la ejecución dará lo mismo cuando el servicio vuelva.

Una conexión persistente que OSRM ya cerró por inactividad no es una falla: se reabre una vez, sin
gastar un intento. El benchmark encontró que en Windows esa conexión cerrada a veces se ve como
`ConnectionAbortedError` (WinError 10053), no como un reset; el cliente no la reconocía y gastaba un
intento con su espera. Ya la reconoce, y una prueba cubre cada forma en que se ve.

## 163. Una matriz por gestor, en bloques, con puntos únicos y sin guardar sus celdas

**Decisión.** Cada gestor tiene su matriz: su base y los puntos de sus cuentas con snap válido, una
sola vez cada coordenada repetida, pedida por bloques de a lo más 100 × 100 (el tope de `table` de
`osrm-routed` por omisión). Las matrices de los gestores siguientes se piden en paralelo mientras
se planea la del actual, hasta 4 a la vez. Una matriz vive solo mientras se planea su ruta: se
guarda su hash (`hash_matriz`), nunca sus celdas.

**Por qué.** Las rutas de dos gestores no comparten paradas (decisión 171), así que una matriz
global sería cuadrática en todas las cuentas para usar solo bloques diagonales. Por gestor, la
matriz es chica (en el benchmark XL, de 2 a 22 puntos, porque las cuentas con la misma coordenada
son un solo punto) y cabe en una petición. Los bloques existen
para que un gestor con más de 100 puntos no rompa el tope del servidor, y se probó que dan la misma
matriz. Guardar las celdas sería guardar, en otra forma, la ubicación relativa de las cuentas; el
hash basta para saber si dos ejecuciones vieron la misma red.

## 164. Un servicio fijo de 900 s, como hipótesis pública

**Decisión.** Cada visita dura 900 s (15 min), todas igual, y la política lo declara como una
hipótesis operacional, no una medición. La API lo dice en su `aviso`.

**Por qué.** El proyecto no tiene la duración real de una visita (la sabría el lifecycle, con
visitas registradas, y una analítica que todavía no existe: v0.14). Inventar duraciones por tipo de
cuenta sería falsa precisión. Un valor fijo y público deja ver cuánto pesa en la jornada: si una
operación mide otro, es otra versión de la política, y cada ruta dice con cuál se calculó.

## 165. La ruta es cerrada en la base, y el regreso se reserva

**Decisión.** La ruta sale de la base del gestor en `campo/v2` y regresa a ella; una parada solo se
programa si, después de su servicio, el regreso a la base también cabe en su jornada. El regreso es
parte de la ruta (su salida, su llegada, su distancia, su duración y su espera).

**Por qué.** Un gestor termina su día donde empezó, y una ruta que lo deja a una hora de su base al
cerrar la jornada no es factible, aunque la última visita termine a tiempo. Reservar el regreso es lo
que distingue «cabe» de «cabe si nadie vuelve».

## 166. Ventanas `[inicio, fin)`, actividades enteras y la espera en el lugar

**Decisión.** Las ventanas de la jornada son intervalos `[inicio, fin)`; dos que se tocan son una.
Cada traslado y cada servicio caben enteros en una ventana; si no, esperan a la siguiente en el
último lugar donde está el gestor. En los huecos no se maneja ni se atiende. El gestor sale de la
base al abrir su primera ventana (lo que espera ahí no es espera de la ruta), y la espera de una
parada es la de antes de salir hacia ella más la de antes de empezar su servicio.

**Por qué.** Un turno partido tiene una pausa que es del gestor: manejar en ella sería trabajar en
la comida. Partir un servicio entre dos ventanas no existe en la operación, y partir un traslado
tampoco. Esperar donde se está es lo que hace una persona, y no inventa un lugar de espera.

## 167. La prioridad manda, y lo que no cabe ni solo no bloquea

**Decisión.** Se programa por prioridad (`MUY_ALTA`, `ALTA`, `MEDIA`, `BAJA`, la de `campo/v2`, que
es la de `decision/v2`): si una parada enrutable de un nivel no cabe (`JORNADA_AGOTADA`), ninguna de
un nivel inferior se programa (`PRIORIDAD_SUPERIOR_AGOTO_JORNADA`), aunque alguna cupiera. La
excepción es la parada que no cabe ni sola en la jornada vacía (`EXCEDE_JORNADA`): no bloquea a
nadie.

**Por qué.** La prioridad es una conclusión de `decision/v2`, con sus motivos (decisión 138); si una
cuenta `BAJA` cercana le quitara el lugar a una `MUY_ALTA` lejana, la ruta contradiría a la
decisión para ahorrar kilómetros. Pero una parada que no cabe ni sola no se va a atender ese día
con o sin las demás: dejar que bloqueara a las de prioridad inferior sería perder el día entero por
una cuenta imposible. Las que se quedan fuera salen con su motivo, para que la operación sepa
cuántas faltan y por qué.

## 168. Una heurística determinista, no un óptimo; el 2-opt, acotado y después

**Decisión.** La secuencia es `PRIORITY_NEAREST_NEIGHBOR_2OPT/v1`: el vecino más cercano por
duración dentro de cada nivel (un empate, por `cuenta_id`), y después, sobre la secuencia construida,
un 2-opt dentro de cada segmento de una misma prioridad que acepta un cambio solo si reduce
estrictamente el traslado y la ruta sigue cabiendo, la mayor reducción primero, hasta 100 mejoras
por segmento. Cada respuesta dice que no es una ruta óptima.

**Por qué.** Un óptimo (TSP con ventanas, VRPTW) es otro problema, con otro costo de cómputo y otra
validación: es la v0.16. La heurística es explicable, determinista y rápida (en el benchmark XL, la
secuenciación de 99,720 paradas tomó 1 s y el 2-opt 0.3 s), y lo que no logra queda a la vista. El
2-opt va después de construir porque construir decide qué entra y en qué orden de prioridad, y
mejorar dentro de un segmento no cambia ninguna de las dos cosas. Desempatar por `cuenta_id` y no
por un id interno hace que la ruta no dependa del orden en que se insertaron las cuentas.

## 169. ETA planificadas, sin tráfico

**Decisión.** Las horas de llegada son **planificadas**: las duraciones estáticas del perfil `car`
sobre la red, sin tráfico, clima, incidentes ni estacionamiento. Se guardan en UTC y salen en la
zona de la configuración de campo, con su desfase.

**Por qué.** No hay datos de tráfico autoalojados ni una forma de reconstruirlos para una fecha
pasada; usarlos haría que la misma ejecución diera otra ruta cada vez. Una ETA estática es
reproducible y sirve para saber si la jornada alcanza; no es una promesa al cliente, y el `aviso` lo
dice.

## 170. Sin geometrías ni polilíneas, y un transporte que no filtra coordenadas

**Decisión.** Ninguna respuesta de la API, bitácora, error, salida de la línea de comandos ni la
Cuenta 360 lleva la coordenada de una cuenta, de una base o de un snap, ni la geometría o la
polilínea de una ruta; las peticiones a OSRM piden sin geometría ni waypoints. Las tablas no tienen
dónde guardar una coordenada, las de paso son temporales de la transacción, y un error se registra
con su tipo, su SQLSTATE y sus marcos, nunca con su mensaje.

**Por qué.** Una polilínea es la ubicación de cada cuenta, uno tras otro, y lo que no se publica no
se filtra. La operación necesita el orden, las horas y los tramos, no el dibujo. Las pruebas mandan
coordenadas en la URL de un error del transporte y buscan coordenadas en la bitácora, la ejecución,
el trabajo y cada respuesta de la API.

## 171. Ni rebalanceo entre gestores ni optimización global

**Decisión.** `ruteo/v2` rutea las cuentas que `campo/v2` le dio a cada gestor, y solo esas. No
mueve cuentas entre gestores ni entre bases, ni optimiza todas las rutas a la vez: una cuenta que no
cabe queda `SIN_TIEMPO` en su gestor, con su motivo.

**Por qué.** El reparto es de `campo/v2`, con su política, su hash y sus motivos (decisiones 148 a
150); si el ruteo lo cambiara, habría dos autoridades sobre quién visita a quién. Lo que el ruteo
descubre (que una carga no cabe por calle) es justo la información que la operación necesita para
cambiar la configuración de campo, y queda contado por gestor y por base.

## 172. Sin una tabla de tramos

**Decisión.** No hay una tabla de tramos (`TramoRutaV2`): el tramo de llegada a cada parada
programada (su salida del lugar anterior, su distancia, su duración y su espera) vive en la parada,
y el de regreso a la base, en la ruta.

**Por qué.** En una ruta cerrada hay exactamente un tramo por parada programada más el regreso, así
que una tabla de tramos repetiría la llave de la parada con una fila más por ruta. Con los tramos en
la parada, una página de paradas ya trae su tramo sin otra unión, y el regreso, que no tiene
parada, es de la ruta. Si alguna vez un tramo tuviera más de un segmento (una ruta con escalas), sí
haría falta.

## 173. Una ruta por gestor con al menos una cuenta `ASIGNADA`

**Decisión.** Una ejecución tiene una ruta por cada gestor que tiene al menos una cuenta `ASIGNADA`
en el plan, y una parada por cada cuenta `ASIGNADA`. Un gestor sin cuentas no tiene ruta, y las
cuentas que `campo/v2` no asignó no son paradas.

**Por qué.** Una ruta vacía no dice nada que la carga del gestor en el plan no diga ya, y
multiplicaría las filas por los gestores sin trabajo. Las cuentas sin gestor ya tienen su motivo en
`campo/v2`; el ruteo responde una pregunta distinta, sobre las que sí se asignaron. La ruta exige en
la base que su gestor tenga carga en el plan, con su misma base, y la parada, que su cuenta tenga
asignación en el plan.

## 174. Por conjuntos, índices medidos, sin particionado; y qué no resuelve v0.13.0

**Decisión.** Una ejecución se resuelve en un trabajo durable `RUTEO_V2` (el nivel 7 de la cola),
en una transacción y en un `SAVEPOINT`, y solo publica su dueño vigente. PostgreSQL selecciona los
gestores y las cuentas del plan por conjuntos; Python pide el snap y las matrices al proveedor y
planea cada ruta con la política; lo que decide vuelve por `COPY` a tablas temporales y se publica
con un `INSERT ... SELECT` por tabla. Los índices son los de sus consultas: en las paradas, la llave
(ejecución, cuenta), (ejecución, estado, cuenta), (ejecución, gestor, orden) y (cuenta, ejecución);
en las rutas, (ejecución, gestor) y su `ruta_id`; en las ejecuciones, las únicas parciales por firma
y (plan, id). Una página de paradas o de rutas se elige primero por sus llaves y después se le une lo
demás. No hay particionado.

**Por qué así.** En el benchmark XL (el plan de 99,720 cuentas `ASIGNADA` de 5,523 gestores del
benchmark de `campo/v2`), con un proveedor sintético que no es una red vial, la ejecución tomó
18.5 s con 236 MiB de memoria pico y 96 MiB de WAL: la publicación, 8.8 s; la selección, 3.3 s; la
secuenciación, 1.0 s; el 2-opt, 0.3 s. Contra la red real del estado de Puebla (OpenStreetMap,
1,739,426 aristas), servida por OSRM en la misma máquina, el mismo plan tomó 16.8 s, con 41,896
paradas programadas por calle. Reconstruir cada ejecución dio el mismo hash de todo lo publicado.
Dentro de PostgreSQL, una página de paradas tarda 0.6 ms, la de un estado 0.6 ms, la de un gestor
1.8 ms, la de una ruta 0.4 ms y las paradas de una cuenta 0.2 ms. La página 500 de las paradas
tardaba 213 ms porque unía y ordenaba las 99,720 paradas antes de saltarse las anteriores; eligiendo
la página por sus llaves primero tarda 3.6 ms. Las rutas se ordenan por el código del gestor, que
está en otra tabla, así que una página lee las rutas de la ejecución (6 a 8 ms con 5,523).

**Por qué sin particionado.** Cada parada ocupa 547 bytes con sus índices (52 MiB por ejecución XL)
y cada ruta, 832; crecen con cada fecha que se rutee. Todas las consultas entran por índices que
empiezan por la ejecución o por la cuenta. Como en `campo/v2` (decisión 153), donde el particionado
podría pagar es en la retención de ejecuciones viejas, y eso es una política de la operación, con
datos de producción (v0.18).

**Lo que v0.13.0 no hace**, a propósito: tráfico o duraciones por hora del día; ventanas horarias
por cuenta, duraciones de visita distintas, vehículos, capacidades o habilidades; TSP, VRP o VRPTW
óptimos y la optimización conjunta de las rutas (v0.16); rebalancear cuentas entre gestores o bases;
geometrías y mapas (v0.17); actualizar la red sola; comparar lo planeado con lo que el gestor hizo, y
la analítica de la cobranza (v0.14); y abrir una ejecución sin que alguien la pida.

## 175. Analytics agregado, no un feature store

**Decisión.** `analytics/v1` publica agregados por celda de un conjunto de dimensiones (una métrica,
una vista, un conjunto, un periodo y una celda por fila), nunca una fila por cuenta, ni por cuenta y
día. Las tablas de paso por cuenta (`an_pc`, `an_ac`) son temporales de la transacción y se borran
al confirmar.

**Por qué.** La pregunta es de una cohorte, no de una cuenta: el detalle individual ya lo responde la
Cuenta 360. Un feature store (una fila por cuenta y día, con sus features) es la base de un modelo
predictivo, que es otra versión (v0.15), con otra validación y otro problema de privacidad; tenerlo
aquí invitaría a usarlo como si ya estuviera validado. Una fila por cuenta y día de una cohorte de
500,000 cuentas y 30 días son 15 millones de filas por análisis: lo publicado de un análisis XL son
miles de celdas.

## 176. La cohorte anclada a un corte canónico

**Decisión.** Cada ejecución se ancla a un `CorteCanonico`: sus snapshots son la cohorte, una vez cada
cuenta, con las dimensiones que tenían en el corte, y las ejecuciones que dan otras dimensiones
(`decision/v2`, la geografía, `territorial/v2`) tienen que ser del mismo corte.

**Por qué.** «Las cuentas que siguen al final» es sesgo de supervivencia: una cuenta que pagó y salió
de la cartera desaparecería justo de donde se mide la recuperación. El corte es una población que no
cambia con el tiempo ni con lo que se sepa después, y sus dimensiones son las que se conocían al
empezar: describir la cohorte `ALTA` del corte con la prioridad que la cuenta tiene hoy mezclaría el
resultado con su causa.

## 177. `as_of` y `known_at`, y los dos modos

**Decisión.** Toda ejecución tiene dos relojes que no se sustituyen: `as_of`, hasta qué día ocurrió
el negocio (en una zona horaria explícita), y `known_at`, hasta qué instante se podía conocer. En
`OPERATIVO`, `known_at` puede omitirse y es el reloj de la base, tomado una vez al abrir; en
`BACKTEST`, es obligatorio. Nunca el reloj del proceso. El modo no entra en la firma.

**Por qué.** Sin `known_at`, un análisis del pasado se hace con todo lo que se sabe hoy: gestiones
registradas tarde, anulaciones posteriores, reversos que llegaron después, y el resultado se ve mejor
o peor de lo que se podía ver entonces (la fuga temporal que también evita `decision/v2`, decisión
113). El reloj de la base es el de los registros: el del proceso puede estar adelantado o atrasado.
Que el modo no entre en la firma hace que un `OPERATIVO` y un `BACKTEST` con la misma frontera sean el
mismo análisis.

## 178. Un manifiesto explícito, comprobado antes de registrar nada

**Decisión.** Una ejecución declara cada entrada por su identificador público: la interpretación de
pagos de cada mes, su atribución, la evaluación de promesas, `decision/v2`, la geografía,
`territorial/v2`, `campo/v2` y `ruteo/v2`. Antes de registrar nada se comprueba que cada una exista,
haya terminado `EXITOSA`, sea de la cartera y del corte, existiera a `known_at` y encadene con las
demás; si no, un código estable y ni una fila. El hash del manifiesto es el SHA-256 de las entradas en
orden canónico; la firma, el de todo lo que fija el resultado.

**Por qué.** Lo que se analiza tiene que poder decirse, auditarse y repetirse: con el manifiesto y la
firma, otra persona reconstruye exactamente el mismo análisis. Comprobar antes de registrar hace que
una petición mala no deje una ejecución fallida que alguien tenga que interpretar. Que dos
interpretaciones de la misma ventana no se puedan declarar evita que un movimiento cuente dos veces.

## 179. Sin «la última» implícita: proponer no es elegir

**Decisión.** Ni la API ni el motor eligen «la última» de nada. La línea de comandos puede
**proponer** un manifiesto con reglas documentadas (la interpretación vigente de cada mes a
`known_at`; la atribución que leyó esa interpretación; la única evaluación, decisión, geografía,
territorial, plan y ruteo de su cadena), pero si una familia tiene dos o más candidatas igual de
válidas, la reporta como ambigua, no elige, y no escribe el manifiesto. Proponer no analiza.

**Por qué.** «La última decisión» cambia de significado cada vez que alguien abre otra, y dos
análisis con el mismo texto de petición dirían cosas distintas. La interpretación vigente de un mes no
es una elección: cada reinterpretación de una ventana sustituye a la anterior por la regla de
`motor-pagos/v1`. Dos decisiones del mismo corte con distinto `known_at`, o dos territoriales con
distintas bases, sí son una elección de negocio, y le toca a quien pide.

## 180. La recuperación sale de los movimientos canónicos, con la semántica de `motor-pagos/v1`

**Decisión.** La recuperación de la cohorte sale solo de `MovimientoEconomicoCanonico` de las
interpretaciones declaradas, conciliados por `cuenta_canonica_id`, y con la semántica de
`motor-pagos/v1` evaluada al final de `as_of`: un `PAGO` no anulado suma, un `POSIBLE_REVERSO` resta,
y un `REVERSO` y su pago suman cero juntos y no entran. Un movimiento sin cuenta o de una cuenta que
no está en el corte es calidad de los pagos, no recuperación.

**Por qué.** `PagoObservado` es evidencia de fuente, con duplicados exactos y ambigüedades:
`MovimientoEconomicoCanonico` es la interpretación económica, con su versión. Inventar otra regla de
signo (restar el reverso en su propio día, por ejemplo) daría otra recuperación que la del motor de
pagos, de `decision/v2` y de la evaluación de promesas, y dos números distintos para lo mismo. La
consecuencia, documentada, es que el valor de un periodo puede cambiar en un análisis con un `as_of`
posterior: cada análisis dice la verdad de su frontera. La neta puede ser negativa y no se recorta.

## 181. Los reversos, point-in-time

**Decisión.** Un `PAGO` está anulado para un análisis solo si el `REVERSO` que lo anula está en una
interpretación declarada y se recibió antes del final de `as_of`. La marca `anulado_por_movimiento_id`
de una interpretación no se usa a ciegas. Si una interpretación declarada tiene un reverso recibido
antes del final de `as_of` cuyo pago está en otra interpretación declarada que no dice que lo anula,
el análisis falla (`pagos.reversos_coherentes`).

**Por qué.** Una reinterpretación de enero hecha en febrero ya marca anulado el pago del 10 de enero
cuyo reverso llegó el 5 de febrero: usar esa marca en un análisis al 31 de enero metería el futuro en
el pasado. Con la regla, el pago cuenta al 31 de enero y deja de contar al 28 de febrero, como se
habría visto en cada fecha. Declarar la interpretación vieja de un mes junto con la nueva del
siguiente dejaría un reverso que anula un pago que su interpretación no tiene anulado: el control lo
detecta y no se publica nada.

## 182. La cobertura de pagos, por día, desde los archivos conocidos

**Decisión.** Un día tiene cobertura de pagos si cae entre el primer y el último movimiento de algún
archivo de pagos cuya historia terminó hasta `known_at`, y si la interpretación declarada de su mes
clasificó cada pago observado conocido de ese mes. Un periodo con algún día sin cobertura publica sus
métricas de pagos `NO_DISPONIBLE`, con cuántos días y cuáles.

**Por qué.** `pagos/v1` no declara el periodo que cubre un archivo: la única evidencia es lo que trae.
Sin esta regla, un hueco entre dos archivos sería un cero de recuperación, y un cero que no se observó
es un dato inventado. Es conservadora: el primer o el último día de un archivo que no trajo pagos
ese día queda sin cobertura. Mejor un `NO_DISPONIBLE` explicado que un cero.

## 183. El lifecycle, point-in-time: lo que llegó tarde y las anulaciones

**Decisión.** Una gestión cuenta si ocurrió en el horizonte, se registró hasta `known_at`, y no tiene
una anulación que haya ocurrido antes del final de `as_of` y se haya registrado hasta `known_at`. Una
promesa, si la creó una de esas gestiones y se registró hasta `known_at`.

**Por qué.** Una gestión registrada después de `known_at` no se podía conocer. Una anulación tiene sus
dos relojes también: si ocurrió después de `as_of`, la gestión era efectiva ese día (caso A); si
ocurrió antes pero se registró después de `known_at`, no se sabía (caso B); solo si ocurrió antes y se
sabía, la gestión no cuenta (caso C). `decision/v2` solo mira si la anulación se registró, porque
decide sobre lo que existe en `as_of`; aquí, un análisis antiguo no puede cambiar porque alguien anuló
después una gestión de entonces.

## 184. La atribución no es causalidad

**Decisión.** Las clases de la atribución son las de `atribucion/v1` declarada: asociación única,
ambigua o sin candidata. Una ambigua queda ambigua: no se elige candidata ni se reparte el monto.
Ninguna métrica se llama «pagos causados», «recuperación generada» ni «impacto», y el pago no es una
etapa del embudo de contacto.

**Por qué.** Que un pago llegue después de una gestión con contacto no dice que la gestión lo produjo:
el cliente pudo pagar de todos modos. Medir impacto exige un diseño (un grupo de control, un
experimento) que no existe en estos datos. Elegir una candidata de una ambigua, o repartirle el monto,
inventaría una precisión que la atribución no tiene (decisiones 106 y 107).

## 185. El estado de una promesa es el de la evaluación declarada

**Decisión.** Los estados de las promesas salen de la `EjecucionEvaluacionPromesas` declarada, con el
mismo `as_of` y la misma zona. analytics no reevalúa ninguna promesa. La tasa de cumplimiento deja
fuera las pendientes, las canceladas y las no evaluables.

**Por qué.** Evaluar una promesa es una regla versionada (`evaluacion-promesa/v1`), con sus casos
difíciles (pagos sin interpretar, datos que no llegan): reescribirla aquí daría dos verdades. Una
promesa pendiente todavía puede cumplirse, una cancelada ya no es un compromiso y una no evaluable no
tiene datos: meterlas en el denominador bajaría la tasa sin que nadie haya incumplido.

## 186. NULL no es cero

**Decisión.** Una métrica sin su entrada, o con un periodo sin cobertura de pagos, es `NO_DISPONIBLE`,
sin valor y con su motivo; una métrica de estado sin periodo es `NO_APLICA`; un dato vacío de la
fuente (el saldo atrasado) no se suma como cero, y su cobertura se publica aparte.

**Por qué.** Un cero dice «no hubo»; un NULL dice «no se sabe». Confundirlos es el error más común de
un reporte de cobranza: un mes sin archivo de pagos que sale con recuperación cero, o un saldo
atrasado desconocido que baja el promedio. Cada estado tiene su motivo, y la prueba del golden
compara una cuenta sin el dato con otra con cero.

## 187. Las razones guardan su numerador y su denominador; decimal, no float

**Decisión.** Una razón publica su valor con 10 decimales, su numerador y su denominador. Con
denominador cero, su valor es NULL con motivo `DENOMINADOR_CERO`. Todo se calcula en `NUMERIC` y se
publica como decimal exacto; los montos están en la moneda de la fuente.

**Por qué.** Una razón sin su denominador no se puede volver a agregar ni juzgar: 1 de 2 no es lo
mismo que 50,000 de 100,000. El 0 % de un grupo sin promesas resueltas sería falso: no hubo nada que
cumplir. Un float no reconcilia al centavo, y los controles comparan centavos sin tolerancia. La
moneda no se inventa: no hay conversión ni multimoneda.

## 188. Dimensiones limitadas: conjuntos fijos del catálogo

**Decisión.** Una métrica se desglosa solo por los conjuntos que declara el catálogo (16, con sus
llaves y su universo), nunca por un GROUP BY arbitrario. No son dimensiones la cuenta, sus
identificadores, el `actor_ref`, las coordenadas ni la partición de campo (zona, subzona, tramo,
microtramo).

**Por qué.** Cada conjunto es una pregunta con su población y su control (que sume su `TOTAL`), y la
supresión necesita saber qué grupos suman qué. Un GROUP BY libre haría imposible proteger las celdas
pequeñas y permitiría cruzar hasta ubicar una cuenta. El microtramo es una celda de 1 km: casi todas
tendrían menos de 10 cuentas, y publicarlas sería publicar dónde viven. Un conjunto nuevo es otra
versión del catálogo.

## 189. Supresión de celdas pequeñas: 10 cuentas, primaria y complementaria

**Decisión.** Una celda de un desglose con menos de 10 cuentas distintas de su universo se publica
`SUPRIMIDO`, sin valor, numerador, denominador ni población. Además, cada grupo (un conjunto contra
el `TOTAL`, un municipio dentro de su entidad, un cruce dentro de sus marginales, un gestor dentro de
su base) es una ecuación, su total es la suma de sus celdas, y ninguna queda con una sola incógnita:
con el total visible, lo suprimido son cero celdas o dos o más que juntas tienen al menos 10 (si no,
se suprime la visible más pequeña, con desempate por `dimension_hash`, y si no queda ninguna
visible, el total); con el total suprimido, alguna de sus celdas también. Para proteger a otra se
prefiere una celda que no sea la única de algún grupo. Se repite hasta que todos los grupos cumplan.
La decisión es por celda, para todas sus métricas y periodos. `TOTAL` nunca se suprime.

**Por qué.** Una celda de 3 cuentas en un municipio pequeño, cruzada con un producto, describe a
personas. La complementaria evita la resta: con la entidad visible y un solo municipio suprimido, el
municipio suprimido es la entidad menos los demás. La primera versión solo revisaba los grupos con
el total visible y dejaba deducir una celda pequeña de dos maneras, que la revisión previa al merge
encontró: un padre visible cuya única celda se suprimió para proteger a otra la revela (y con ella
se resta la pequeña), y un padre suprimido con todas sus celdas visibles se reconstruye sumándolas.
La regla de la ecuación cierra las dos, y una prueba con 300 tablas al azar comprueba que ninguna
celda pequeña se deduce sumando y restando grupos. Preferir una celda que no sea la única de su
grupo evita suprimir de más: a esa la revela su total y no protege a nadie. Decidir por celda y no
por periodo evita que una serie diaria reconstruya lo que el acumulado oculta. 10 es un umbral
común, explícito en la política y en su hash; no es una garantía contra cualquier combinación de
sumas y restas entre conjuntos distintos, y así se documenta.

## 190. Un gestor de campo no es un `actor_ref`

**Decisión.** Las métricas de campo y de ruteo usan el código del gestor de campo de la configuración
de campo; las de gestión no tienen dimensión de quién gestionó. Nunca se cruzan, y `actor_ref` no se
publica.

**Por qué.** El gestor de campo es un código operativo de una configuración declarada (decisión 143):
dice a quién se le planeó una carga. El `actor_ref` de una gestión es una referencia opaca de quien
la registró (decisión 109) y puede ser una persona real: un ranking de cobradores por `actor_ref`
es una evaluación de desempeño de personas, que no es de este sistema, y cruzarlos afirmaría que el
gestor planeado es quien hizo la gestión.

## 191. Planificado no es ejecutado

**Decisión.** Las métricas de campo y de ruteo describen el plan declarado de una fecha: candidatas,
asignadas, paradas programadas, distancias y duraciones planificadas. Las visitas registradas son de
la gestión (`gestion.visitas_campo_registradas`) y no se comparan con las programadas como si fueran
lo mismo. El embudo de campo termina en `PARADAS_PROGRAMADAS`.

**Por qué.** Que una parada esté programada no dice que el gestor fue, ni a qué hora. Comparar el plan
con lo ejecutado exige unir una visita con su parada (la misma cuenta, el mismo día, el mismo gestor),
y esa unión no existe en los datos: el lifecycle no sabe de rutas. Es una pregunta para cuando exista.

## 192. Sin dashboards

**Decisión.** La salida de `analytics/v1` son agregados: por la API, la línea de comandos y las
exportaciones (CSV, Parquet, JSONL). No hay Power BI, Looker, Dash, Streamlit, React ni gráficas.

**Por qué.** Un dashboard es una capa de presentación con sus propias decisiones (qué se muestra, cómo
se colorea, qué se esconde) que no deben tomarse antes de tener números auditables. Las exportaciones
llevan el estado de cada valor y nunca un valor suprimido, así que cualquier herramienta encima
respeta lo mismo.

## 193. Sin modelos

**Decisión.** Ninguna métrica es una probabilidad, un score, un pronóstico ni una recuperación
esperada; no hay XGBoost, LightGBM, scikit-learn, SHAP ni feature store predictivo.

**Por qué.** Predecir exige primero describir bien, sin fuga temporal: esta versión construye la base
histórica y analítica sobre la que un modelo podría entrenarse y validarse con backtesting (v0.15,
Predictive Intelligence). Un modelo hoy se entrenaría sobre métricas que todavía no tienen su
historia de backtests.

*Desde v0.15.0*, `predictive/model-v1` predice, en sus propias tablas y con su propio
entrenamiento (decisiones 197 a 222). `analytics/v1` sigue sin modelos: ninguna métrica lee una
predicción, y un agregado sigue siendo una descripción de lo observado.

## 194. Sin optimización

**Decisión.** analytics no recomienda ni optimiza: no hay MILP, VRP global, asignación óptima ni
«la siguiente mejor acción». Describe lo que pasó y lo que se planeó.

**Por qué.** Una recomendación es una decisión con su política, su versión y su responsable, y una
optimización necesita una función objetivo de negocio que nadie ha declarado. Mezclarlas con la
descripción haría que un número descriptivo se leyera como una instrucción.

*Desde v0.16.0*, la optimización existe, en su propio motor (`optimization/v1`), con su política, su
configuración versionada y su certificado (las decisiones 223 a 247): analytics sigue sin recomendar
ni optimizar, y no lee los planes.

## 195. Los agregados se materializan, con su hash

**Decisión.** Cada ejecución materializa sus agregados y sus controles en tablas (`agregado_analytics`,
`control_analytics`), inmutables, con el SHA-256 de todo lo publicado en orden canónico
(`hash_agregados`). No hay vistas ni cálculos al vuelo en la API: la API solo lee lo publicado.

**Por qué.** Un análisis point-in-time no debe cambiar porque cambió una tabla de origen: materializado
con su manifiesto y su hash, es un registro auditable que se puede comparar, y reconstruirlo con el
mismo manifiesto tiene que dar el mismo hash (las pruebas lo comprueban, también después de reescribir
el orden físico de las tablas de origen). Calcular al vuelo haría cada consulta tan cara como un
análisis y su resultado dependería del momento de la consulta. Por lo mismo, bajar la `0015` no
pierde evidencia: cierra lo que estaba en curso (`CERRADA_AL_BAJAR`), quita los trabajos
`ANALYTICS` y las tablas de analytics, y no toca ninguna entrada; al volver a subir, el mismo
manifiesto en `BACKTEST`, con su `known_at`, vuelve a dar el mismo `hash_agregados`.

## 196. Por conjuntos, índices medidos, sin particionado; y qué no resuelve v0.14.0

**Decisión.** Una ejecución se resuelve en un trabajo durable `ANALYTICS` (el nivel 8 de la cola), en
una transacción y en un `SAVEPOINT`, y solo publica su dueño vigente. PostgreSQL lee cada dominio por
conjuntos, agrega a (cuenta, periodo) con `UNION ALL` sin unir dominios entre sí, y de ahí a las
celdas con `GROUPING SETS`; Python solo decide la supresión sobre las celdas, que vuelven por `COPY`.
Los índices de las tablas nuevas son los de sus consultas: la llave natural de los agregados (que
sirve los filtros por métrica), (ejecución, conjunto, vista, periodo) para los filtros sin métrica,
las únicas parciales por firma, (corte, `as_of`, id) en las ejecuciones y las llaves de las entradas.
Ningún índice GIN sobre las dimensiones: los filtros de la API entran por las columnas. No hay
particionado.

**Por qué así.** En el benchmark XL, sin reconstruir nada, la cohorte de 500,000 cuentas del corte
con toda la cadena de operación, por día en 30 días, se analizó en 41.5 s con 182 MiB de memoria
pico y 115 MiB de WAL; la del primer corte semanal con todo el lifecycle (1,804,210 gestiones,
2,990,891 movimientos y 2,697,650 pagos atribuidos), por semana en 78 días, en 191.7 s; y los 12
cortes semanales por mes, en 929.2 s (81.9 s de mediana). La memoria del proceso no pasa de 182 MiB:
los hechos viven en tablas temporales de PostgreSQL, y Python solo ve las celdas. Los planes de la
primera medición mostraron dos defectos del SQL: las tablas de paso de una fila por entrada sin
`ANALYZE` (el planificador ordenaba los 8 millones de movimientos de la cartera para unirlos) y un
`UNION ALL` cuyas ramas llevaban los 25 hechos con ceros (un ordenamiento de 1 GB en disco).
Corregidos, el XL-B pasó de 289.9 a 191.7 s con el mismo hash de lo publicado. Por la API, cada
consulta de los agregados tarda de 11 a 16 ms de mediana (de 0.1 a 1.5 ms dentro de PostgreSQL),
salvo la de todas las celdas suprimidas sin métrica ni conjunto (81.5 ms). `work_mem` no se fija en
el motor: con 256 MB el XL-B tarda 162.6 s, y subirlo es una decisión de la operación. Los alcances
de los archivos de pagos se leen recorriendo `pago_observado` (unos 6 s en el XL); un índice
(archivo, fecha) lo evitaría a costa de cada ingesta de pagos, y no se agrega.

**Por qué sin particionado.** Cada agregado ocupa 418 bytes con sus índices, y lo que se publica
depende de las celdas, las métricas y los periodos, no de las cuentas ni de los días: los 12 cortes
semanales publicaron 39,140 agregados cada uno, con 500,000 cuentas o con 465,999, y el XL-A, 0.41
por cuenta en un horizonte de 15 millones de cuenta-días. Todas las consultas entran por índices que
empiezan por la ejecución. Como en `campo/v2` y `ruteo/v2` (decisiones 153 y 174), donde el
particionado podría pagar es en la retención de ejecuciones viejas, y eso es una política de la
operación, con datos de producción.

**Lo que v0.14.0 no hace**, a propósito: causalidad, pronósticos, probabilidades, scoring o
recuperación esperada (v0.15); recomendaciones u optimización matemática; dashboards o gráficas;
comparar lo planificado con lo ejecutado en campo; métricas por persona (`actor_ref`); multimoneda;
retención o un *warehouse* externo; y abrir un análisis sin que alguien lo pida.

## 197. Predecir no es causar

**Decisión.** `predictive/model-v1` estima la probabilidad de resultados **observados bajo la
operación histórica**: recuperación positiva, contacto, creación de una promesa y la recuperación
positiva esperada, en los días siguientes a un corte. No estima el efecto de ninguna acción, no dice
qué acción ejecutar y ningún documento, campo ni respuesta lo presenta así: cada versión, cada
scoring y cada explicación llevan el aviso.

**Por qué.** Los datos con que se entrena salen de una política: la cobranza gestionó más a quien más
debía, por el canal de su tramo. Una probabilidad alta de pago puede ser la de una cuenta que paga
sola o la de una que la operación ya trabajaba; el modelo no puede distinguirlas, y quien lea la
probabilidad como el resultado de una acción tomaría decisiones sobre una relación que nadie midió.
Separar la predicción de la decisión es lo que permite construir, en v0.16, una optimización que
trate ese problema explícitamente en lugar de heredarlo escondido.

## 198. Las features, point-in-time

**Decisión.** Una feature usa hechos ocurridos hasta el final de T y registrados hasta K, con el
mismo SQL en el entrenamiento, el scoring y la explicación: los cortes conocidos a K, la
interpretación de pagos vigente a K de cada mes, los reversos recibidos hasta el final de T, el
lifecycle registrado hasta K sin las anulaciones registradas hasta K, y la evaluación de promesas
terminada hasta K. Una ventana de pagos sin cobertura completa deja sus features NULL.

**Por qué.** La fuga temporal es el defecto que más infla un modelo y el que menos se ve: una feature
que mira un pago del día siguiente, un reverso que llegó después o una evaluación terminada más tarde
hace que el backtest prometa lo que el scoring no puede dar. Calcularlas por conjuntos, con filtros
por instante de registro y no por la fecha máxima observada, hace que reconstruirlas después dé
exactamente lo mismo (las pruebas lo comprueban cuenta por cuenta, con eventos tardíos, reversos
futuros y evaluaciones posteriores).

## 199. Tres relojes

**Decisión.** Cada ancla declara `feature_as_of` (T, el día del corte), `feature_known_at` (K, hasta
cuándo se conocía lo de las features) y `label_known_at` (L, hasta cuándo se observa para el label),
con `K <= L <= el reloj de la base`, siempre con zona. Un scoring declara su `known_at`; en
`OPERATIVO`, sin declararlo, es el reloj de la base, una vez.

**Por qué.** Con un solo reloj hay que elegir entre features con información futura o labels sin
madurar. T dice cuándo se predice; K, qué se sabía entonces; L, qué se supo después para calificar la
predicción. Separar K de T permite el escenario real en que un corte se procesa días después de su
fecha, y separar L de K es lo que hace posible un label sin contaminar las features.

## 200. La ventana (T, T+H], con las reglas de los motores existentes

**Decisión.** La ventana de un target es (T, T+H]: los días T+1 a T+H, enteros, en la zona declarada.
La recuperación neta es la de `motor-pagos/v1` y `analytics/v1` sobre las interpretaciones
declaradas (un reverso anula su pago solo si llegó antes del final de la ventana; el reverso de un
pago anterior a la ventana no entra a su neto); el contacto es la cuenta contactada de
`analytics/v1`; la promesa es la creación de una `PromesaPago`, no su cumplimiento.

**Por qué.** Lo ocurrido el día T es pasado: si el día T fuera parte del label, una feature y un label
podrían ver el mismo pago. Reutilizar las reglas de los motores que ya se auditan evita una segunda
definición de «recuperó» o «se contactó» que con el tiempo diferiría de la primera. La promesa se
mide por su creación porque su cumplimiento necesita su propia ventana, más larga que 30 días.

## 201. Labels maduros, censurados o no disponibles; censurado no es cero

**Decisión.** Un label existe solo si está `MADURA`: la cobertura declarada de su fuente llega a
T+H. Si no llega, `CENSURADA`; si no se declaró, `NO_DISPONIBLE`. Un label que no madura queda NULL
en el dataset, no entra al entrenamiento de su target y se cuenta.

**Por qué.** Un label sin madurar parece un cero: la cuenta «no pagó» porque los pagos de esos días
todavía no llegan. Entrenar con esos ceros sesga hacia abajo cada probabilidad, y más en las anclas
recientes, que son las que más se parecen al scoring. Contar los censurados por target y split deja
ver cuánto del dataset no se usó y por qué.

## 202. Las marcas de cobertura se declaran

**Decisión.** `pagos_completos_hasta` y `lifecycle_completo_hasta` se declaran en el manifiesto (el
escenario las escribe en su manifiesto, y la carga las copia a su bitácora); ninguna puede ser
posterior al día de L. Nunca salen de la fecha máxima observada en una tabla.

**Por qué.** Un día sin pagos observados no prueba que los pagos de ese día ya llegaron: el archivo
de esa semana puede llegar mañana. La fecha máxima observada confundiría «no hubo» con «no ha
llegado», que es exactamente la confusión que la censura evita. Una marca declarada es un dato
auditable con su responsable.

## 203. Un manifiesto explícito, comprobado antes de registrar nada

**Decisión.** Un entrenamiento lee un manifiesto: sus anclas con sus relojes, su cobertura y su
split, y las interpretaciones de pagos de sus labels. Se comprueba fail-closed (24 códigos estables)
antes de registrar nada, con su hash canónico y una firma de todo lo que fija el resultado.
`construir-manifiesto-predictivo` propone uno con una regla determinista, sin entrenar.

**Por qué.** «Los últimos 26 cortes» o «la interpretación más reciente» dependen del momento en que
se ejecuta: el mismo comando daría otro modelo mañana. Un manifiesto explícito se puede revisar,
repetir y comparar, y su firma hace que el mismo entrenamiento no se publique dos veces. Exigir la
interpretación vigente a L de cada mes (y completa) evita un label que lea menos pagos de los que se
sabían.

## 204. Sin split aleatorio

**Decisión.** El split es temporal y lo declara el manifiesto: TRAIN antes de VALIDATION y VALIDATION
antes de TEST. No hay split aleatorio, por cuenta ni por fila, en ninguna parte.

**Por qué.** En un split aleatorio, filas de la misma cuenta y de la misma semana caen en TRAIN y en
TEST, y la evaluación mide memoria, no predicción. La pregunta de negocio es temporal: con lo que se
sabe hoy, qué pasará después; el split tiene que hacer la misma pregunta.

## 205. La purga entre bloques

**Decisión.** Entre TRAIN y VALIDATION, y entre VALIDATION y TEST, la ventana del label más largo
de un bloque (T + 30) termina antes de la primera ancla del siguiente. Las anclas de en medio se
declaran `PURGE`: se construyen y se cuentan, no se usan.

**Por qué.** Sin purga, el label de la última semana de TRAIN se construye con los pagos de las
primeras semanas de VALIDATION: el modelo vería, a través de sus labels, el periodo con que se le
evalúa. La purga cuesta cuatro anclas semanales por frontera y evita un optimismo que no se puede
detectar después.

## 206. Walk-forward hacia atrás y TEST intocable

**Decisión.** El backtest repite el procedimiento en tres folds hacia atrás desde el split principal,
cada uno con su purga y su propio preprocesamiento, y reporta cada fold, su agregado (media, mínimo y
máximo) y el holdout de TEST aparte. TEST se evalúa una vez, al final, con el artefacto publicado, y
no elige nada.

**Por qué.** Un solo periodo de evaluación puede ser favorable por azar; ver tres folds muestra si el
modelo se sostiene o se degrada en el tiempo, y un promedio solo no lo mostraría. Que TEST no elija
nada (ni C, ni α, ni la calibración, ni las features, ni el generador) es lo que hace de su métrica
una estimación honesta; usarlo para decidir la convertiría en otra VALIDATION.

## 207. GLM como public-v1

**Decisión.** Los clasificadores son regresiones logísticas con L2 y el monto un GLM Tweedie con
liga log (potencia 1.5), con rejillas pequeñas y declaradas. Sin árboles, boosting, redes, AutoML ni
búsqueda de hiperparámetros.

**Por qué.** La primera versión tiene que ser auditable de punta a punta: un GLM se explica
exactamente (la contribución de cada feature suma el predictor), se calibra bien, su artefacto cabe
en un JSON legible, y su desempeño fija el baseline honesto contra el que un modelo más complejo
tendría que justificarse después. Con features point-in-time bien construidas, gran parte de la señal
es lineal en sus transformaciones; lo que no lo sea, el walk-forward lo dirá.

## 208. Newton por bloques propio; scikit-learn solo como prueba

**Decisión.** Los GLM se ajustan con un Newton (IRLS) propio, en NumPy, que acumula X'WX y X'g
bloque por bloque en un orden fijo, con el objetivo de scikit-learn. scikit-learn es una dependencia
de desarrollo: una prueba compara los dos.

**Por qué.** El dataset del benchmark no cabe como una matriz densa en la memoria de una máquina
chica (millones de filas por cerca de cien columnas), y Newton solo necesita, por iteración, matrices
del tamaño de las columnas. Un estimador propio, con su tolerancia fijada, da un resultado
determinista en una misma plataforma y una memoria acotada; compararlo con scikit-learn en cada
corrida de las pruebas asegura que el objetivo es el mismo sin que el servicio dependa de él. Entre
plataformas, el punto flotante del BLAS puede mover el último dígito de un coeficiente y el hash del
artefacto (el CI, en Linux, publica otro `hash_modelo` que una laptop con Windows para el mismo
escenario): lo reproducible de punta a punta es lo publicado, que el runtime lee del JSON.

## 209. Sin rebalanceo de clases

**Decisión.** Ni `class_weight`, ni SMOTE, ni sobremuestreo, ni submuestreo.

**Por qué.** Rebalancear cambia la prevalencia que ve el modelo, y sus probabilidades dejan de ser
probabilidades: una cuenta con 0.5 ya no tendría la mitad de chance. Con métricas que no dependen
de un umbral (AUC, log loss, Brier) y sin umbral publicado, el desbalance no es un problema que
resolver, y la calibración de la prevalencia real es lo que v0.16 necesitará para valores esperados.

## 210. La calibración, decidida en VALIDATION

**Decisión.** Platt (una logística sin regularizar del predictor lineal) se ajusta en VALIDATION y se
adopta solo si baja el Brier de VALIDATION al menos 0.1 %; si no, la probabilidad queda cruda. TEST
reporta la calibrada y la cruda, sin decidir nada.

**Por qué.** La regularización y el cambio de prevalencia entre periodos descalibran una logística;
Platt lo corrige con dos parámetros sin cambiar el orden de las cuentas. Decidirlo con una regla
fijada en VALIDATION evita adoptar una calibración que no mejora, y nunca calibrar con TEST mantiene
TEST como evaluación. Platt y no isotónica: con dos parámetros, la calibración se generaliza mejor
con poca VALIDATION y cabe en el artefacto.

## 211. Baseline de TRAIN y compuertas fijadas antes de TEST

**Decisión.** El baseline es la prevalencia (o la media) de TRAIN, constante. Un clasificador queda
`VALIDADO` si en TEST su log loss y su Brier no son peores que los del baseline, su ROC AUC es mayor
que 0.5 y toda probabilidad es válida; el monto, si su MAE no es peor y toda predicción es finita y
no negativa; y el ajuste elegido convergió. La versión se publica solo con sus cuatro componentes
validados; si no, queda `RECHAZADA` con sus métricas.

**Por qué.** Una AUC mínima arbitraria se negocia después de verla; superar al baseline fuera del
tiempo es la prueba mínima de que el modelo sabe algo más que la prevalencia. Fijar las compuertas en
la política, con su hash, antes de entrenar, impide moverlas para que un modelo pase. Un modelo
rechazado no desaparece: sus métricas quedan para entender por qué.

## 212. El modelo es un JSON canónico

**Decisión.** Cada componente se publica como un artefacto JSON canónico (`predictive/artifact-v1`)
con su SHA-256: el preprocesamiento, las columnas, los coeficientes, el intercepto, la liga, la
calibración y la precisión, con 12 cifras significativas. La versión guarda el `hash_modelo` de sus
cuatro artefactos.

**Por qué.** Un JSON se lee, se compara con un `diff` y se verifica con su hash; un cambio de un
coeficiente cambia el hash. Es todo lo necesario para predecir y nada más, y no depende de la versión
de ninguna biblioteca.

## 213. Nunca pickle; un runtime independiente

**Decisión.** Ni pickle, ni joblib, ni cloudpickle como contrato. El runtime de inferencia lee solo el
JSON, no usa el código de entrenamiento, y las pruebas lo comparan con el estimador (lo mismo hasta
el redondeo del float64, idéntico después de cuantizar) y con scikit-learn. Las probabilidades se
publican con 8 decimales y el monto en centavos, como enteros, y el riesgo es su complemento exacto.

**Por qué.** Un pickle ejecuta código al cargarse (un artefacto alterado es código arbitrario), se
rompe entre versiones de las bibliotecas y no se puede auditar. Un runtime separado del
entrenamiento prueba que el artefacto basta; la cuantización fija hace que el hash de lo publicado
no dependa del redondeo del float binario.

## 214. Ningún identificador es una feature

**Decisión.** Ni el `cuenta_id`, ni el `CLIENTE_UNICO`, ni el despacho o la cartera, ni la fila o la
hoja de origen, ni el `actor_ref`, ni un hash de cualquiera de ellos. Una guarda rechaza cualquier
feature con un nombre de identidad, y una prueba lo comprueba.

**Por qué.** Un identificador permite memorizar a la cuenta: el modelo «predice» lo que ya vio de
ella, y no generaliza a una cuenta nueva. Un hash de una identidad sigue siendo una identidad: no
anonimiza nada y tiene el mismo problema.

## 215. Ningún dato personal ni sensible, y sin geografía fina

**Decisión.** Ni nombre, ni domicilio, ni teléfono, ni referencias, ni aval, ni correo; ni CURP, RFC,
sexo, género, edad, religión, salud o etnia, ni proxys de ellos; ni coordenadas, municipio, tramo,
microtramo ni zona. La única geografía es la entidad.

**Por qué.** Un dato sensible no tiene lugar en un modelo de cobranza aunque mejore una métrica: un
modelo que usa la edad o el género discrimina por construcción. La geografía fina identifica a una
persona y aprende el barrio, no la cuenta. Los datos sintéticos no cambian la regla: la arquitectura
es la que se usaría con datos reales.

## 216. Las salidas de Decision, Campo y Ruteo no son features

**Decisión.** Ni la prioridad, el canal recomendado o el score de `decision/v2`, ni la base, el
gestor, el orden, la ETA o el estado de `campo/v2` y `ruteo/v2`, ni la estrategia, el canal o los
estatus declarados en la cartera. Las tablas de esas salidas no aparecen en el SQL de las features.

**Por qué.** Esos motores deciden qué hacer con una cuenta; en v0.16 decidirán con estas
predicciones. Si una predicción usara sus salidas, el ciclo se cerraría: el modelo aprendería la
política de ayer y la reforzaría. La estrategia declarada es la misma política, venida de la
cartera.

## 217. Recuperación positiva esperada; el riesgo es 1 − p

**Decisión.** El monto es la esperanza de max(recuperación neta, 0) en 30 días, publicado como
`recuperacion_positiva_esperada_30d`, nunca «neta esperada». El riesgo sin recuperación es
`1 − p_recuperacion_positiva_30d`, exacto, sin modelo propio.

**Por qué.** Un neto negativo (un reverso de un pago anterior) no es una recuperación que una
operación pueda buscar, y un Tweedie con liga log no admite negativos: truncar a cero es una
definición, y el nombre lo dice. Un segundo modelo para el riesgo podría contradecir al primero
(una cuenta con 0.7 de pagar y 0.5 de no pagar); el complemento exacto no.

## 218. La explicación se verifica con el `feature_hash`

**Decisión.** Cada predicción guarda el `feature_hash` de sus features. La explicación de una cuenta
recalcula sus features con los relojes del scoring, verifica que su `feature_hash` sea el publicado
(si no, `409 LA_EXPLICACION_NO_REPRODUCE`) y descompone el predictor lineal en contribuciones que
suman exactamente el predictor. No se guarda: se calcula al pedirla.

**Por qué.** Una explicación de unas features distintas de las que produjeron la predicción sería una
explicación de otra predicción. Verificar el hash hace que la explicación corresponda a lo publicado
o no se dé. Calcularla al pedirla evita guardar 40 contribuciones por cuenta y scoring, que casi
nadie lee; en un GLM la descomposición es exacta, sin aproximaciones.

## 219. El drift diagnostica; no reentrena

**Decisión.** Cada scoring mide, contra la referencia de TRAIN, el PSI de cada feature y de cada
salida y el cambio en la fracción de NULL, con severidades `BAJO`, `MODERADO` y `ALTO` como
heurísticas operacionales. El drift no cambia, no recalibra ni reentrena el modelo; no hay
reentrenamiento automático. `evaluar-predictivo` mide el desempeño con los labels que ya maduraron,
y solo lee.

**Por qué.** Un PSI alto dice que la población cambió, no que el modelo empeoró: con el corte de otra
temporada puede cambiar la distribución sin cambiar la relación con el resultado. Reentrenar
automáticamente por un umbral cambiaría el modelo sin una revisión, con datos que nadie validó, y
haría que el mismo scoring diera otro resultado según el día. Cambiar el modelo es otro
entrenamiento, con su manifiesto y su versión.

## 220. Las predicciones corren en sombra

**Decisión.** Las predicciones se publican en sus propias tablas, en un trabajo de la cola en el nivel
9, y nada de `decision/v2`, `campo/v2`, `ruteo/v2` ni `analytics/v1` las lee. No hay Decision Engine
v3, selección de acción ni optimización en v0.15.

**Por qué.** Antes de que una predicción mueva una decisión hay que ver, en paralelo, cómo se comporta
con la operación real (su drift, su desempeño cuando maduran los labels). Usarlas desde el primer día
convertiría cada error del modelo en un error operativo. Cómo consumirlas sin confundir predicción
con causalidad es el diseño de v0.16.

*Desde v0.16.0*, `optimization/v1` lee las predicciones publicadas de un scoring declarado para
armar un plan, también en sombra (la decisión 224): la recuperación positiva esperada es su valor de
oportunidad, sin multiplicarla por ninguna probabilidad, y nada operacional lee el plan.

## 221. Sin transacciones largas; el benchmark M y el XL

**Decisión.** Un entrenamiento construye su dataset ancla por ancla, cada una en su propia
transacción corta de lectura, ajusta sin ninguna transacción abierta y publica en una transacción
corta; un candado de PostgreSQL (un *advisory lock* en una conexión propia) hace que un segundo worker
espere al primero, y solo publica el dueño vigente de su trabajo. El benchmark separa el modelo (el
entrenamiento M, 100,000 cuentas por 26 anclas semanales) de la escala operacional (el scoring de un
corte XL de 500,000 cuentas).

**Por qué.** Una transacción abierta durante minutos detendría la frontera de conocimiento de los
demás motores, que espera a que no quede ninguna abierta desde antes de su `known_at`. Entrenar con
500,000 cuentas por 26 semanas no aportaría evidencia del modelo que no den 100,000, y
multiplicaría el tiempo y la memoria; el scoring es lo que tiene que escalar a la cartera completa.

**Medido.** El entrenamiento M (26 anclas, 2,957,237 filas cuenta-ancla, 757,301 de TRAIN) tardó
2,667 s con 1,282 MiB de memoria pico: el dataset, ancla por ancla, 1,860 s; el ajuste de los cuatro
componentes con su rejilla, de 89 a 108 s cada uno; el walk-forward, 283 s. En TEST, la recuperación
positiva tiene un ROC AUC de 0.8359 y un log loss de 0.4322 contra 0.5899 del baseline; el contacto,
0.6798; la promesa, 0.6833; el monto, un MAE de 1,462.72 contra 2,132.04. El scoring XL de 500,000
cuentas tardó 173.1 s (2,888 por segundo) con 1,657 MiB y 586 MiB de WAL. Los detalles, en
[backtesting_predictivo.md](backtesting_predictivo.md#lo-medido).

## 222. Índices medidos, sin particionado; y qué no resuelve v0.15.0

**Decisión.** Los índices de las tablas nuevas son los de sus consultas: la llave (scoring, cuenta)
de las predicciones, (scoring, salida, cuenta) para cada orden de la API, (cuenta, scoring) para las
predicciones de una cuenta, la llave (scoring, tipo, nombre) del drift, las únicas parciales por
firma de los entrenamientos y los scorings, y (estado, id), (corte, id) y (modelo, id) para sus
listas. No hay particionado.

**Por qué.** Cada consulta de la API entra por un índice que empieza por el scoring o por la cuenta,
y ningún orden ordena el scoring entero. Las predicciones crecen con las cuentas y los scorings, y la
retención de scorings viejos es, como en `campo/v2`, `ruteo/v2` y `analytics/v1`, una política de la
operación, con datos de producción.

**Medido.** Sobre el scoring XL (500,000 predicciones, 622 bytes cada una con sus índices), cada
consulta de la API entra por su índice: las páginas por cada orden, de 17 a 28 ms de mediana; las
predicciones de una cuenta, 11 ms; la explicación de una cuenta, 111 ms. Ningún Seq Scan sobre una
tabla grande. El benchmark encontró tres consultas que no escalaban, y se corrigieron antes del PR:
la comprobación de que una interpretación clasificó cada pago (un `EXISTS` por pago, porque con la
interpretación recién publicada fuera de las estadísticas el plan recorría todos sus resultados por
pago), los movimientos de una sola cuenta (por el índice de los movimientos de una cuenta) y el total
de una página sin filtros (el que publicó el scoring). Con eso no hace falta particionar.

**Qué no resuelve v0.15.0.** Los modelos predicen resultados observados bajo la política histórica y
no estiman causalidad; el benchmark sintético valida la arquitectura y la estadística, no el
desempeño real; la recuperación positiva esperada no es la neta esperada; un PSI alto no prueba por
sí solo un deterioro; y no hay reentrenamiento automático, aprendizaje en línea, ML causal, Decision
Engine v3 ni optimización. Lo que sigue es v0.16, Optimización Matemática: consumir estas
predicciones, la capacidad, el territorio, los recursos de campo y las restricciones de ruteo para
decidir, con restricciones explícitas, sin suponer que cambiar de acción causa la probabilidad
predicha.

## 223. Optimizar no es inferir causalidad

**Decisión.** `optimization/v1` encuentra el óptimo de un modelo de asignación declarado: cuentas a
canales y pools bajo capacidades, presupuesto y coberturas. Cada respuesta, el certificado, el CLI y
la documentación dicen que es el óptimo del modelo declarado, no una estrategia causalmente óptima, y
ninguna salida tiene un campo como *uplift*, recuperación incremental o efecto esperado de un canal.

**Por qué.** El valor que optimiza es una predicción bajo la política histórica: dice qué tan valiosa
fue una cuenta con la forma en que se operó, no cuánto cambia el resultado si se le asigna un canal.
Un óptimo exacto sobre ese valor es un buen uso de la capacidad respecto a esas predicciones; vendido
como el efecto de una acción, sería una afirmación causal que los datos no sostienen. Un óptimo
matemático de supuestos incorrectos sigue siendo una mala decisión de negocio.

## 224. En sombra: un plan de soporte a decisión

**Decisión.** Un plan se publica en sus propias tablas, en un trabajo de la cola, y nada de
`decision/v2`, `campo/v2`, `ruteo/v2`, el lifecycle ni `analytics/v1` lo lee. No registra gestiones,
no envía mensajes, no llama y no crea visitas.

**Por qué.** Igual que las predicciones de v0.15 (la decisión 220), un plan tiene que verse en
paralelo con la operación antes de mover nada: qué asigna, qué deja fuera y cómo cambia con su
configuración. Ejecutarlo desde el primer día convertiría cada supuesto de una configuración en una
acción.

## 225. El valor de oportunidad es la recuperación positiva esperada

**Decisión.** El valor de una cuenta es su `recuperacion_positiva_esperada_30d` del scoring
declarado, en centavos exactos (por 100, sin redondeo: la predicción tiene dos decimales).

**Por qué.** Es la única salida de v0.15 en unidades de dinero, y ya es la esperanza de
max(recuperación neta, 0) en la ventana: el valor esperado de la oportunidad, no una probabilidad.
Los centavos enteros hacen exacto el objetivo y su verificación.

## 226. Sin multiplicar por la probabilidad de recuperación

**Decisión.** El valor no se multiplica por `p_recuperacion_positiva_30d`, `p_contacto_14d` ni
`p_promesa_30d`. La definición de la política lo dice (`no_se_multiplica_por`), y las pruebas lo
comprueban: el objetivo es la suma exacta de los valores que entran.

**Por qué.** La recuperación esperada ya integra la probabilidad de recuperar: multiplicarla otra vez
contaría dos veces la incertidumbre y premiaría a las cuentas por ser predecibles, no por su valor.
Multiplicarla por el contacto sería, además, suponer que contactar causa la recuperación.

## 227. p_contacto solo desempata

**Decisión.** `p_contacto_14d` (en enteros de 10⁻⁸) es el segundo nivel lexicográfico: solo elige
entre planes del mismo valor de oportunidad. Nunca entra al primero.

**Por qué.** Entre dos planes que capturan el mismo valor predicho, atender las cuentas que la
operación histórica contactó más es una preferencia razonable y explícita. Mezclarlo con el valor,
con un peso, cambiaría el óptimo económico por una cantidad arbitraria, y no es la probabilidad de
éxito del canal asignado.

## 228. decision/v2 es la frontera de admisibilidad

**Decisión.** El canal que `decision/v2` recomendó a una cuenta, con la matriz de admisibilidad de la
configuración, dice qué canales operativos puede recibir. La optimización no decide canales fuera de
esa frontera y no cambia la decisión.

**Por qué.** `decision/v2` es la política operacional explicable y versionada; la optimización asigna
capacidad dentro de ella. Una matriz explícita (y no una regla escondida) deja ver qué se permitió:
admisible no es efectivo, y una configuración que permite `TELEFONICA` para una cuenta de `DIGITAL`
lo declara.

## 229. Entradas declaradas; la base garantiza la cadena

**Decisión.** Una ejecución declara su scoring, su `decision/v2`, su configuración y, con `CAMPO` o
`BASE`, su `ruteo/v2`; nunca "el último". El scoring y la decisión son del mismo corte, con `as_of`
igual a su fecha; el ruteo es de un plan de campo de esa misma decisión, y la fecha de operación es la
del ruteo. Las llaves foráneas compuestas de la ejecución usan restricciones únicas que esas tablas ya
tenían, sin cambiar ninguna tabla anterior.

**Por qué.** Un plan que mezcla la predicción de un corte con la decisión de otro, o el ruteo de otra
decisión, es incoherente aunque cada pieza sea válida. "El último" depende del momento de la
consulta y no se puede reconstruir; una firma con las entradas exactas sí. Que la base lo garantice
evita que un error del código publique una cadena mezclada.

## 230. CAMPO solo desde paradas PROGRAMADA de su propio gestor

**Decisión.** Un pool de `CAMPO` es de un gestor (`GESTOR_CAMPO`) y solo alcanza las paradas
`PROGRAMADA` de ese gestor en el `ruteo/v2` declarado; su capacidad efectiva es la menor de la
declarada y esas paradas. Una parada `SIN_TIEMPO`, `NO_ENRUTABLE` o `NO_ENRUTABLE_BASE` nunca es
`CAMPO`, y una cuenta nunca va con otro gestor.

**Por qué.** `ruteo/v2` ya demostró que esas paradas caben en la jornada de su gestor por la red
vial. Elegir un subconjunto de una ruta factible la deja factible; asignar a otro gestor o convertir
una parada que no cupo crearía visitas que nadie demostró posibles.

## 231. Sin reoptimizar rutas

**Decisión.** El plan no reordena paradas, no agrega cuentas a una ruta, no mueve cuentas entre
bases o gestores y no publica geometrías ni ETA nuevas: cada cuenta en `CAMPO` conserva su
`orden_ruteo_original`, y antes de publicar una consulta por conjuntos lo comprueba.

**Por qué.** Reoptimizar rutas es otro problema (un VRP con jornadas y red vial) que `ruteo/v2`
resuelve con su política; mezclarlo con la asignación de capacidad haría el modelo mucho más difícil
sin demostrar nada mejor. Quitar paradas puede dejar a un gestor con tiempo libre: es una
limitación declarada.

## 232. Unidades de recurso abstractas

**Decisión.** Cada pool declara cuántas unidades de recurso consume una asignación (un entero de 1 a
1,000,000), y la configuración, un presupuesto opcional. El tercer nivel minimiza las unidades.

**Por qué.** Una visita no cuesta lo mismo que un mensaje, y un presupuesto de esfuerzo es una
restricción operacional real, pero su conversión a dinero no está declarada en ninguna parte. Unas
unidades abstractas y explícitas permiten restringir y desempatar por esfuerzo sin inventar costos.

## 233. Sin modelo de costos monetarios

**Decisión.** No hay costos en dinero, ROI ni margen: el objetivo es valor de oportunidad predicho, y
las unidades de recurso no son costos.

**Por qué.** Un modelo de costos real necesita datos de la operación (salarios, tarifas, tiempos)
que el proyecto no tiene y no debe inventar. Restarle un costo inventado al valor daría un número con
apariencia de utilidad sin serlo.

## 234. Un MILP con HiGHS, el de SciPy

**Decisión.** El modelo es un programa lineal entero mixto con variables binarias, resuelto con HiGHS
a través de `scipy.optimize` (`linprog` y `milp`), sin otro backend. SciPy entra a las dependencias.

**Por qué.** La asignación con capacidades, presupuesto y coberturas lineales es un MILP; HiGHS es un
solver de código abierto que demuestra optimalidad (cota y gap) y viene con SciPy, que el proyecto ya
usaba en sus pruebas: no agrega un binario ni una licencia. Una heurística no daría un certificado.

## 235. Reducción exacta de candidatos por dominancia

**Decisión.** Antes del modelo, las cuentas elegibles se agrupan por su firma de restricciones (sus
pools elegibles y su pertenencia a cada cobertura activa); de cada clase se retienen las primeras
u[s] en orden canónico (valor, contacto, cuenta), con u[s] el mínimo de cotas seguras. Nunca un
*top N*. El teorema y su prueba están en [modelo_optimizacion.md](modelo_optimizacion.md).

**Por qué.** En una cartera, cientos de miles de cuentas comparten unas pocas firmas: dentro de una
clase, una cuenta peor solo entra si las mejores no caben, y las cotas dicen cuántas pueden caber.
Retener solo esas no cambia el óptimo (ni el desempate) y reduce el modelo: el 55 % de las elegibles
en M y el 44 % en XL. Un *top N* por score dejaría fuera cuentas que una cobertura necesita.

## 236. Matrices dispersas y enteros exactos

**Decisión.** El modelo se construye como una matriz dispersa (CSR) con coeficientes enteros, sin
ninguna variable de un par imposible; antes de construirlo se comprueba que cada coeficiente, cota y
suma posible cabe en 2⁵³ − 1, y la solución se verifica en aritmética entera. Las cotas de la
reducción y del prechequeo también se suman en enteros, con cada capacidad topada en el número de
cuentas: ningún plan usa más, y ninguna suma envuelve.

**Por qué.** Una matriz densa de 500,000 cuentas por 2,700 pools no cabe en memoria y casi toda es
cero. Los enteros exactos en el rango de un `double` hacen que lo que el solver ve sea exactamente lo
declarado, y que la verificación no dependa de tolerancias. La revisión previa al merge encontró que
esas cotas se sumaban sin tope (en `int64`, y una en `float64`): con más de mil pools de capacidad
2⁵³ − 1, la suma envolvía a un negativo y la reducción dejaba fuera cuentas que el óptimo usa. Las
capacidades de los benchmarks están lejos de eso: sus mediciones no cambian.

## 237. Optimización lexicográfica por etapas

**Decisión.** Cuatro niveles (valor, contacto, unidades, desempate canónico), resueltos por etapas:
cada una fija con una igualdad el óptimo exacto de las anteriores. Ninguna constante gigante mezcla
los objetivos.

**Por qué.** Un objetivo ponderado con *big-M* necesita pesos tan grandes que destruyen la precisión
numérica o tan chicos que dejan de ser lexicográficos. Las etapas expresan exactamente la jerarquía,
y cada una tiene su propio certificado. El cuarto nivel (un peso canónico no separable por cuenta y
pool) hace reproducible la elección entre planes empatados en los tres primeros.

## 238. Relajación primero y fijación por dualidad

**Decisión.** Cada etapa resuelve primero su relajación lineal (punto interior con *crossover*); si
su vértice óptimo es entero, es el óptimo del entero; si no, *branch-and-bound*. Entre etapas, la
dualidad débil fija las variables que ningún óptimo usa o deja de usar.

**Por qué.** Con la matriz de una asignación, la relajación casi siempre tiene un vértice entero, y
demostrarlo por la relajación es mucho más rápido que un *branch-and-bound* completo: medido sobre un
problema sintético de la forma del XL, el nivel 1 por `milp` no terminaba en 20 minutos y por la
relajación tardó 48 s; sin la fijación, el nivel 2 tardaba 921 s y el 4 terminaba con un error del
solver, y con ella el XL entero, 62 s. La fijación es una consecuencia de la etapa (dualidad débil,
con su margen de punto flotante), no una heurística, y la solución completa se verifica en enteros.
Sin presolve de HiGHS: con las filas densas de las participaciones, el presolve tardaba más que
resolver.

## 239. Solo se publica un óptimo demostrado

**Decisión.** Un plan se publica solo si cada nivel termina `OPTIMAL` (cota = objetivo, gap 0) y su
solución se verifica en enteros. `TIME_LIMIT` es `OPTIMO_NO_DEMOSTRADO`, `INFEASIBLE` es `INFACTIBLE`
con su diagnóstico, y nada de eso publica un plan.

**Por qué.** Una solución factible sin su prueba no es el óptimo, y llamarla así haría inauditable el
certificado. Un plan sin publicar con su diagnóstico es más útil que un plan que parece óptimo.

## 240. La huella del solver

**Decisión.** Cada ejecución guarda su `solver_fingerprint`: el SHA-256 del algoritmo, las versiones
de SciPy, HiGHS, NumPy y Python, el sistema, la arquitectura y los parámetros. Entra a la firma, y un
worker con otra huella no publica bajo ella (`SOLVER_DISTINTO`).

**Por qué.** Los óptimos de las cuatro etapas son únicos, pero el camino (y, en un empate exacto en
las cuatro, el plan) puede cambiar con otra versión del solver. La huella hace explícito con qué se
demostró el plan, y que reconstruirlo exige lo mismo.

## 241. Sin atributos protegidos ni identificadores como dimensión

**Decisión.** Las dimensiones de una cobertura son el producto, el tramo de atraso, la prioridad de
`decision/v2` y la base. Ninguna es un `cuenta_id`, un `CLIENTE_UNICO`, un `actor_ref`, un gestor ni
un atributo sensible o su aproximación, y ningún resultado lleva un dato personal ni una coordenada.

**Por qué.** Una cobertura por persona o por un atributo protegido convertiría el plan en una regla
de trato diferenciado sin base declarada. Las dimensiones operacionales públicas bastan para exigir
cobertura de cartera.

## 242. Sin ejecución automática

**Decisión.** No hay un comando ni una ruta que ejecute un plan, envíe, llame, visite o aplique nada.

**Por qué.** Ejecutar es una decisión de negocio con su responsable, su política de contacto y sus
controles, que esta versión no tiene. Un plan en sombra que nadie ejecuta no puede dañar a nadie.

## 243. La configuración: versionada, cerrada y con hash

**Decisión.** Una configuración es un documento JSON con un esquema cerrado (sin SQL, sin Python, sin
expresiones), validado entero, con todos sus problemas juntos; su forma canónica (sin el nombre ni
los supuestos) tiene su SHA-256, el mismo contenido es la misma versión, y solo una `PUBLICADA`, que
ya no cambia, se usa para planear. El flag de imputación no tiene valor por omisión.

**Por qué.** Las capacidades y las coberturas son los supuestos de un plan: tienen que ser
inspeccionables, comparables y fijos. Un esquema cerrado no ejecuta nada que no se haya revisado, y
el hash permite saber si dos planes usaron los mismos supuestos.

## 244. Todo o nada, por conjuntos

**Decisión.** El universo y los pares elegibles se arman en PostgreSQL por conjuntos (JOIN por
igualdad, en tablas temporales que se descartan) y llegan a Python por `COPY`; el plan se prepara en
una tabla temporal y se publica con un `INSERT … SELECT`, junto con el uso de cada pool, cada
restricción y el cierre de la ejecución, en una sola transacción. Ninguna transacción queda abierta
mientras el solver corre.

**Por qué.** 500,000 objetos ORM no caben en un tiempo razonable ni en memoria; los conjuntos sí. Un
plan a medias sería peor que ninguno: o se publica entero, con su hash, o no se publica nada (las
pruebas inyectan una falla en cada uno de sus puntos).

## 245. La cola: OPTIMIZATION en el nivel 10, con candado y lease

**Decisión.** Cada ejecución es un trabajo durable `OPTIMIZATION`, después de `PREDICTIVE_SCORING`;
un candado de PostgreSQL por ejecución hace que un segundo worker espere al primero, y solo publica el
dueño vigente de su trabajo. El `POST` nunca resuelve el MILP.

**Por qué.** Un plan lee un scoring y una decisión ya publicados, y nunca debe retrasar una etapa
operacional ni un scoring. Resolver dentro de una petición HTTP ataría un minuto de CPU a una
conexión. Un worker que perdió su lease mientras resolvía no publica: el nuevo dueño resuelve desde
el principio, y la firma impide publicar dos veces.

## 246. El benchmark: S, M, XL y la sensibilidad, con supuestos fijados antes de medir

**Decisión.** El benchmark (`scripts/benchmark_optimizacion.py`, fuera del CI) mide la exactitud (S:
10,000 cuentas con y sin reducción, y la enumeración en instancias pequeñas), una formulación rica
(M: ~114,000 cuentas), la escala (XL: 500,000 cuentas con CAMPO) y la sensibilidad de la capacidad
(−20 %, nominal y +20 %), con `optimization-benchmark/public-v1`: supuestos sintéticos fijados en el
código antes de medir y registrados antes del primer plan.

**Por qué.** La exactitud se demuestra en un tamaño donde el modelo completo también se resuelve; la
escala, con la cartera completa y su cadena de campo real. Ajustar las capacidades después de ver los
números haría que el benchmark midiera la configuración, no el optimizador.

**Medido.** S: los mismos cuatro óptimos con y sin reducción; la enumeración coincide en las 200
instancias. M: 113,876 cuentas, 60,790 variables, cada nivel `OPTIMAL` por su relajación entera, 8.2 s
en total. XL: 500,000 cuentas, 279,787 retenidas en 7,235 clases, 408,204 variables, 131,108
restricciones y 2,492,352 no nulos; cada nivel `OPTIMAL`, cota = objetivo, gap 0; 87.6 s en total
(el solver 31.3 s, la publicación 39.5 s), 1,262 MB de memoria pico y 338.8 MiB de WAL. La
sensibilidad: con 20 % más de capacidad el óptimo captura 7.2 % más de valor de oportunidad
predicho, y con 20 % menos, 9.3 % menos. Los detalles, en
[optimizacion.md](optimizacion.md#lo-medido).

## 247. Índices medidos, sin particionado; y qué no resuelve v0.16.0

**Decisión.** Los índices de las tablas nuevas son los de sus consultas: la llave (ejecución, cuenta)
de los resultados; (ejecución, filtro, cuenta) para cada filtro de la lista blanca de la API (estado,
canal, pool, producto, prioridad y base); (cuenta, ejecución) para los planes de una cuenta; la llave
(ejecución, pool) del uso; (ejecución, *binding*, código) de las restricciones; las únicas parciales
por firma de las ejecuciones, y (estado, id), (scoring, id) y (configuración, id) para sus listas. No
hay particionado.

**Por qué.** Cada consulta de la API entra por un índice que empieza por la ejecución o por la
cuenta. El benchmark encontró que el total de los filtros `producto`, `prioridad` y `base` recorría
la tabla de resultados (87 ms en PostgreSQL), y sus tres índices se agregaron antes del PR; con ellos,
ninguna consulta recorre una tabla grande (de 11 a 65 ms de mediana por la API, sobre 841,628
resultados), y no hace falta particionar.

**Qué no resuelve v0.16.0.** Un plan es óptimo respecto al modelo declarado, no una estrategia
causalmente óptima; el valor es la recuperación positiva esperada bajo la política histórica, no el
beneficio incremental de contactar; un canal asignado no es evidencia de que ese canal cause la
recuperación; las capacidades del benchmark son sintéticas y las unidades no son costos; `CAMPO` no
reoptimiza rutas; y no hay *uplift*, ML causal, Decision Engine v3, ejecución automática ni
dashboards. Lo que sigue es v0.17, App y Dashboards: convertir la Cuenta 360, analytics, las
predicciones, los planes, el campo y las rutas en una superficie operacional, sin perder la
auditabilidad, el punto en el tiempo, la privacidad ni el versionado.

## 248. Una SPA de React con Vite, sin SSR

**Decisión.** La app (`web/`) es una SPA de React 19 con Vite 8 y React Router 8, construida como
archivos estáticos y servida por nginx con un proxy hacia la API en el mismo origen. Sin Next, sin
Remix, sin renderizado en el servidor y sin una biblioteca de componentes: componentes propios,
pequeños, con HTML semántico y variables CSS.

**Por qué.** La superficie es de solo lectura, interna y detrás de un proxy: no hay SEO que ganar ni
un primer render que acelerar en el servidor, y un servidor de Node más sería un proceso, una
superficie de ataque y una versión más que mantener. Los archivos estáticos se sirven con la imagen
fijada de nginx, que ya hace de proxy. Una biblioteca de componentes traería su peso, su estilo y su
accesibilidad (o su falta) sin control; las pantallas son tablas, fichas e indicadores.

## 249. TypeScript estricto, sin `any`

**Decisión.** `strict` con `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`,
`noImplicitOverride`, `noImplicitReturns` y `verbatimModuleSyntax`; ESLint con
`strictTypeChecked`, que prohíbe `any` explícito. Lo que el contrato declara como un objeto libre
(`dict[str, Any]`: una distribución, un certificado, una huella) se lee con funciones que comprueban
su forma y devuelven `null`, nunca con un `as`.

**Por qué.** El error que más importa aquí es mostrar un valor que no es el publicado: un índice
fuera de rango que se vuelve `undefined` y luego cero, o un campo opcional tratado como presente. El
compilador los detiene antes de que lleguen a una pantalla.

## 250. El backend es la fuente de verdad

**Decisión.** La app ordena, pagina, filtra, formatea y dibuja lo que la API publicó, y nada más: no
recalcula una métrica, un score, una prioridad, un plan, una ruta, una atribución ni un estado, ni
suma en el navegador dos números publicados. Lo único que `app/v1` agrega (la distribución de las
predicciones de un scoring) se cuenta en la base.

**Por qué.** Cada número tiene un motor que lo publicó con su versión, su política y su hash; uno
calculado en el navegador no tendría ninguno, y dos pantallas podrían contradecirse. Si una pantalla
necesita un agregado que nadie publica, se pide a la API, que lo cuenta una vez y en un solo lugar.

## 251. v0.17 es de solo lectura

**Decisión.** La app no crea gestiones, mensajes, llamadas, visitas ni promesas, no modifica cuentas,
no ejecuta decisiones, planes de campo, rutas, entrenamientos, scorings ni optimizaciones, no edita
configuraciones ni publica modelos. No tiene un botón de acción; los `POST` siguen siendo de la API y
de la línea de comandos, y el proxy de la app solo deja pasar `GET`.

**Por qué.** Una superficie que muestra lo que los motores publicaron no necesita escribir para
servir, y una acción desde una pantalla exige lo que v0.17 no tiene: autenticación, autorización,
auditoría de quién hizo qué y confirmaciones. Que el proxy no deje pasar otro método lo garantiza
aunque una pantalla tuviera un error.

## 252. Sin autenticación falsa

**Decisión.** La app no tiene inicio de sesión, usuarios, roles ni sesiones: ni un `admin/admin`, ni
un JWT casero, ni roles simulados. Es una superficie local y demostrativa, publicada solo en
`127.0.0.1`, que no se despliega con datos reales. La clave de la API la pone el proxy.

**Por qué.** Una autenticación de juguete se parece a una de verdad lo suficiente como para que
alguien la despliegue. Decir "sin autenticación" es honesto y verificable; la autenticación, el
control de acceso y el endurecimiento son de v0.18, con un proveedor de identidad real.

## 253. `app/v1` solo donde compone

**Decisión.** La API tiene una fachada para la app, `app/v1`, solo donde sin ella la app tendría que
hacer muchas peticiones, unir respuestas en el navegador o arriesgarse a mezclar ejecuciones: el
contexto (opciones y validación), el resumen, el explorador de cuentas, la Cuenta 360 y sus
pestañas, las paradas de una ruta y el linaje. Lo demás, la app lo lee de su motor (`analytics/v1`,
`predictive/model-v1`, `optimization/v1` y los planes y rutas de `campo/v2` y `ruteo/v2`).

**Por qué.** Una fachada por cada pantalla duplicaría los contratos de los motores y los dejaría
envejecer por separado. Una sin ninguna obligaría al navegador a unir la Cuenta 360 desde ocho
motores, a elegir qué ejecución de cada uno leer y a filtrar los datos personales que las rutas de
dominio traen. `app/v1` compone, valida y presenta; no recalcula.

## 254. El contexto operativo, explícito

**Decisión.** Cada pantalla lee un contexto: un corte y, de cada motor, la ejecución que declara por
su identificador público (`decision`, `analytics`, `scoring`, `optimizacion`, `campo`, `ruteo`).
`GET /app/v1/contextos/validar` lo resuelve con lo que cada ejecución dice de sí misma y lo valida:
que cada una exista, sea del corte y encadene con las demás (lo que una leyó es lo que el contexto
declara, y dos que leyeron lo mismo leyeron lo mismo). Un contexto incompatible se reporta con cada
problema y ninguna pantalla lo combina ni lo corrige.

**Por qué.** La recuperación de una analytics, la prioridad de una decisión y el plan de una
optimización solo se pueden poner lado a lado si salen de la misma cadena. Validarlo en un solo
lugar, en la API, con lo que cada ejecución registró al publicarse, evita que una pantalla mezcle un
scoring con la optimización de otro.

## 255. Nunca "lo más reciente" en silencio

**Decisión.** Lo que el contexto no declara no se lee. Ni la API ni la app eligen "la ejecución más
reciente" de nada. El selector propone contextos compatibles (desde una optimización o desde una
analytics, validados), pero ninguno se abre solo: la persona elige abrirlo o lo ajusta.

**Por qué.** "Lo más reciente" cambia mientras se mira: dos personas con la misma pantalla verían
números distintos, y una pantalla podría combinar ejecuciones que nunca se leyeron entre sí. Lo
publicado por una ejecución no cambia; lo que cambia es cuál se declara, y eso tiene que verse.

## 256. La URL reproduce la vista

**Decisión.** El contexto, los filtros, el orden, la página, la pestaña y la vista viven en la URL,
en un orden fijo de parámetros. El estado de React no guarda nada de eso.

**Por qué.** Una URL copiada abre la misma vista lógica, con los mismos números, en otro navegador o
semanas después, porque cada ejecución declarada sigue publicando lo mismo. Es la forma más simple
de auditar una pantalla: la URL es su evidencia.

## 257. Paginación, orden y filtros en el servidor

**Decisión.** Toda lista se pagina, se ordena y se filtra en la base: el explorador de cuentas (25,
50 o 100 por página, hasta 100), las asignaciones de la optimización, las predicciones, las cargas,
las rutas y las paradas. Los filtros del explorador son una lista cerrada de campos públicos y
operativos (el principio del `cuenta_id`, producto, tramo de atraso, prioridad, canal, estado de la
predicción y estado en la optimización), y uno de una ejecución no declarada es un 422, no una lista
vacía.

**Por qué.** Un corte tiene 500,000 cuentas: bajarlas al navegador para filtrarlas sería lento,
pesado y una copia de la cartera en cada pestaña. Ningún filtro es un dato personal, porque la app
no tiene ninguno con qué buscar.

## 258. Sin coordenadas ni mapas

**Decisión.** La app no recibe ni dibuja coordenadas, domicilios, puntos de cuentas, celdas,
tramos, microtramos ni polilíneas. La geografía de una cuenta se muestra como su estado y su
calidad, su municipio y su entidad y su base asignada; una ruta, como la secuencia de sus paradas
con su ETA, su servicio y su motivo.

**Por qué.** La ubicación de una cuenta es un dato personal aunque sea sintética, y un mapa de
cobranza es exactamente lo que no debe poder copiarse de una pantalla. La operación que la app sirve
(qué gestor, qué base, en qué orden, con qué estado) no necesita un mapa.

## 259. La supresión de analytics se respeta

**Decisión.** Una celda `SUPRIMIDO` de `analytics/v1` se muestra como suprimida, sin valor: no se
grafica, no se reconstruye (ni como el TOTAL menos el resto) y una composición que la excluye dice
que está incompleta. Las fórmulas y las definiciones se leen del catálogo de la API, no se copian.

**Por qué.** La supresión protege a los grupos chicos; reconstruirla en el navegador la anularía. Y
una definición copiada a la app envejecería sin aviso cuando el catálogo cambie de versión.

## 260. Una predicción no es un efecto causal

**Decisión.** Cada pantalla con una predicción dice, a la vista, *Predicción, no efecto causal ni
recomendación de acción.*, con su versión del modelo, su `as_of`, su `known_at` y si lleva
imputación. Las contribuciones de una explicación se rotulan *contribución al score*, nunca
*causa*, y el drift lleva *Drift no implica automáticamente degradación del modelo.* Una prueba
revisa el texto de cada fuente de la app: ninguna afirma que una acción causará algo, que un canal
incrementará la recuperación, un ROI esperado ni *uplift*, salvo para negarlo.

**Por qué.** Un número junto a una cuenta se lee como una instrucción. Las predicciones de
`predictive/model-v1` son asociaciones de la operación histórica, no el efecto de gestionar; la
pantalla tiene que decirlo donde se mira, no en la documentación.

## 261. Un plan óptimo no es una estrategia causalmente óptima

**Decisión.** Cada pantalla con un plan de `optimization/v1` dice *Óptimo respecto al modelo
declarado; no estrategia causalmente óptima.*, con el estado del solver, el gap, la cota y lo que el
plan no dice. Una ejecución sin óptimo demostrado (`TIME_LIMIT`, `INFEASIBLE`) se muestra con su
estado y su resultado, sin esconderla y sin un plan.

**Por qué.** El valor de oportunidad es la recuperación positiva esperada bajo la política
histórica, no el beneficio incremental de contactar. Un óptimo matemático de supuestos equivocados
sigue siendo una mala decisión, y la pantalla no puede presentarlo como otra cosa.

## 262. Los tipos salen del OpenAPI de la API

**Decisión.** `scripts/exportar_contrato_web.py` reduce el OpenAPI de la API a las operaciones `GET`
que el proxy deja pasar y a sus esquemas (`web/src/api/openapi.json`, determinista), y
openapi-typescript genera de él los tipos de la app. El CI los regenera y falla si difieren de los
del repositorio.

**Por qué.** Un modelo copiado a mano en TypeScript envejece en silencio cuando la API cambia; uno
generado no compila. Y reducido a lo que el proxy deja pasar, la app ni siquiera puede escribir una
llamada a una ruta que no podría hacer.

## 263. TanStack Query para el estado del servidor

**Decisión.** Cada recurso es una consulta de TanStack Query con una llave que lleva todo lo que fija
su respuesta: el contexto entero, los identificadores, los filtros, la página y el orden. Lo
publicado por una ejecución `EXITOSA` se guarda diez minutos; un contexto con una ejecución
`EN_PROCESO` se revalida cada 30 segundos. Se reintenta una vez una falla de red o un 5xx. Sin
WebSockets, sin SSE y sin *polling* agresivo.

**Por qué.** Lo publicado no cambia, así que puede guardarse; lo que hay que evitar es que dos
contextos compartan una entrada, y la llave lo impide. Las cancelaciones, los estados de carga y de
error y la página anterior mientras llega la siguiente vienen resueltos.

## 264. Sin Redux

**Decisión.** No hay un almacén global de estado: la URL guarda el estado de la pantalla y TanStack
Query el del servidor. ESLint prohíbe importar Redux, lodash, moment, date-fns y axios.

**Por qué.** No queda estado que compartir entre pantallas que no sea uno de esos dos. Un almacén más
sería una segunda fuente de verdad, y cada dependencia, peso y una versión que mantener.

## 265. Recursos locales, sin telemetría

**Decisión.** Todo se sirve del mismo origen: el JavaScript, el CSS, el ícono y las fuentes (las del
sistema). Sin CDN, sin Google Fonts, sin mapas externos, sin analítica, sin Sentry ni ningún servicio
que reciba algo desde el navegador. Una prueba revisa cada fuente y `index.html`; Playwright, cada
petición.

**Por qué.** Cada recurso externo es una petición que le dice a un tercero quién mira qué y cuándo,
y una dependencia de su disponibilidad. Un error de render se escribe en la consola de quien lo ve, y
nada más.

## 266. La accesibilidad, medida y no certificada

**Decisión.** HTML semántico, teclado, foco visible, un enlace para saltar al contenido, tablas con
`caption` y `scope`, gráficas con su tabla equivalente, estados con texto e icono, ARIA solo donde
el HTML no alcanza; axe-core en Vitest y en Playwright (con contraste, en Chromium) sobre las
pantallas principales, con cero violaciones serias o críticas como compuerta. La paleta de las
gráficas se validó para daltonismo.

**Por qué.** Lo que se puede medir se mide en cada cambio; lo demás se hace bien por diseño. Una
herramienta no certifica WCAG 2.2 AA, así que la app no dice que lo cumple: dice qué se midió.

## 267. Los presupuestos del bundle, fijados antes de medir

**Decisión.** El shell inicial, 250 KiB gzip como máximo; ningún paquete diferido de JS, más de 350
KiB; todo el JS, 1.2 MiB. Y por pantalla, con el build de producción: el resumen, hasta 8
peticiones de datos; la Cuenta 360, hasta 10; ninguna, más de 2 MiB de datos. Cada pantalla es un
paquete diferido, y las gráficas, otro.

**Por qué.** Un presupuesto fijado después de medir solo describe lo que salió. Fijado antes, un
paquete que crece obliga a optimizar, no a subir el límite. Medido: 132.6 KiB de shell, 111.7 KiB el
mayor diferido (las gráficas), 275.4 KiB de JS en total; el resumen, 3 peticiones de datos, y la
Cuenta 360, 2.

## 268. Un proxy de solo lectura, con lista blanca y la clave del lado del servidor

**Decisión.** El navegador habla con un proxy del mismo origen (nginx en el compose; el servidor de
Vite en desarrollo, con el mismo comportamiento) que deja pasar solo `GET`, solo las rutas de
`web/nginx/rutas-api.conf` (la única fuente: nginx la incluye, Vite la lee, el exportador del
contrato y las pruebas la usan), y pone la clave de la API en cada petición. Otro método es 405
`METODO_NO_PERMITIDO`; otra ruta, 404 `RUTA_NO_EXPUESTA`, sin llegar a la API. Sin CORS.

**Por qué.** La clave en el navegador sería de cualquiera que abra la consola. Una lista blanca, y
no una lista negra, hace que una ruta nueva de la API no aparezca en la app sin que alguien decida
exponerla; y las rutas de dominio que llevan un `CLIENTE_UNICO` no son alcanzables aunque la app
tuviera un error.

## 269. Sin migración vacía

**Decisión.** v0.17 no agrega una migración: la cabeza de Alembic sigue en la 0017. La app no
guarda nada (ni preferencias, ni usuarios, ni vistas guardadas) y `app/v1` lee lo que ya existe.

**Por qué.** Una migración sin un cambio de esquema que la justifique es historia falsa; una que
creara tablas de usuarios o de preferencias inventaría una persistencia que v0.17 no necesita.

## 270. Sin índices nuevos ni particionado sin evidencia

**Decisión.** Ningún índice ni particionado nuevo. Las consultas de `app/v1` entran por los índices
que los motores ya tienen: la llave (ejecución, cuenta) de cada tabla, el índice del corte de
`snapshot_cuenta` y los de las predicciones por probabilidad.

**Por qué.** El benchmark de 500,000 cuentas lo decidió. Las rutas lentas de la primera corrida se
arreglaron sin índices (decisión 272). Quedan dos, medidas: ordenar por saldo o por días de atraso
recorre `snapshot_cuenta` en paralelo (976 ms), y un índice (corte, saldo, cuenta) lo bajaría a
0.14 ms, pero ocupa 381 MB (el 20 % de la tabla del benchmark), tarda 24 s en construirse y cada
ingesta de un corte lo pagaría, igual que el de días de atraso; contar `ALTA` y `CAMPO` juntas
revisa las filas de una de las dos (483 ms). Ninguno es un orden o un filtro de omisión: si el uso
los pide, son de v0.18.

## 271. Sin datos personales en ningún nivel

**Decisión.** Las respuestas de `app/v1` son modelos derivados de los del dominio sin
`cliente_unico`, `actor_ref`, `idempotency_key` ni `observacion`, en cualquier nivel de anidación
(`sin_datos_personales`), con su nombre más `App`. La cuenta se identifica por su `cuenta_id`
técnico. Las pruebas recorren cada esquema de `app/v1` y cada respuesta; la prueba de humo y
Playwright revisan cada respuesta que la app recibe.

**Por qué.** Quitar los campos en la pantalla dejaría el dato en la red y en la cache del navegador.
Quitarlos del modelo hace que ni siquiera se serialicen. La observación de una gestión y la llave
de un evento son texto libre que nadie valida: pueden traer un nombre o un teléfono aunque el
contrato pida que no, así que tampoco salen.

## 272. Las llaves primero: el explorador a escala

**Decisión.** El explorador pagina en dos pasos: primero las llaves de la página, en la tabla más
chica que basta para filtrarlas y ordenarlas (el índice del corte; el de la ejecución que filtra; o,
si el scoring tiene una predicción por cada cuenta del corte, su índice por probabilidad), y después
solo esas llaves se completan con lo de cada ejecución, en el orden de la página. La distribución de
un scoring se cuenta en una sola pasada en paralelo, con un `FILTER` de comparaciones exactas por
décimo.

**Por qué.** En la primera corrida del benchmark, la página 10,000 tardaba 2.9 s y ordenar por
`p_recuperacion_positiva_30d` 1.5 s, porque unían las 500,000 cuentas con cada ejecución antes de
quedarse con 50; ahora, 62 ms y 27 ms. La distribución tardaba 1.9 s con `width_bucket` sobre
`numeric` y `GROUPING SETS`; ahora, 0.52 s, sin punto flotante, que podría poner en el décimo de al
lado un valor que cae justo en su frontera. Una prueba compara cada orden, sentido y filtro, página
por página, con la lista entera filtrada y ordenada aparte, por los dos caminos del scoring.

## 273. Las pantallas se prueban con respuestas de la API real

**Decisión.** Vitest prueba cada pantalla con MSW y respuestas capturadas de la API
(`scripts/capturar_fixtures_web.py`, en proceso, sobre la base sintética de la prueba de humo): solo
de rutas que el proxy deja pasar, recortadas, revisadas contra datos personales y tipadas por el
contrato. Playwright prueba los flujos contra la app del compose, con la API real y lo que la prueba
de humo publicó.

**Por qué.** Respuestas inventadas a mano prueban la pantalla contra lo que alguien cree que la API
responde; capturadas y tipadas, contra lo que responde, y un cambio del contrato las delata al
compilar. No son datos reales: son del generador sintético.

## 274. El benchmark de la superficie, y qué no resuelve v0.17.0

**Decisión.** El benchmark (`scripts/benchmark_app.py` y `web/scripts/medir-paginas.mjs`, fuera del
CI) mide, sobre el corte de 500,000 cuentas con el contexto que el selector propone, declarado antes
de medir, cada ruta que las pantallas leen (mediana, p95, sentencias y su tiempo en PostgreSQL) y
cada pantalla con el build de producción (hasta su contenido, sus peticiones y sus bytes).

**Medido.** Las rutas de `app/v1`, de 13 a 62 ms de mediana, salvo el resumen y la distribución (0.54
y 0.52 s), ordenar por saldo (0.98 s) y `ALTA` con `CAMPO` (0.48 s); la Cuenta 360, 51 ms; las
rutas de los motores, de 10 a 27 ms. Las pantallas, de 270 a 824 ms hasta su contenido, con 2 a 7
peticiones de datos y hasta 176 KiB de datos. Los detalles, en [app.md](app.md#lo-medido).

**Qué no resuelve v0.17.0.** Es de solo lectura y no ejecuta ninguna acción de cobranza; no tiene
autenticación, SSO, control de acceso ni usuarios, y no se despliega públicamente ni con datos
reales; no muestra datos personales ni coordenadas; analytics es descriptivo; una predicción no es
causal; un plan es óptimo respecto a su modelo, no causalmente; y no hay observabilidad de nube, alta
disponibilidad, recuperación ante desastres, límites de peticiones ni la política de seguridad de
contenido completa. Lo que sigue es v0.18, Cloud, Observabilidad y Hardening: llevar el sistema a un
despliegue con identidad, permisos, observabilidad y endurecimiento, sin perder la auditabilidad, el
punto en el tiempo, la privacidad ni el versionado.

## 275. La configuración falla cerrada: producción por omisión

**Decisión.** `MC_APP_ENV` es `production` si falta. En producción la API no arranca sin una base
explícita (sin contraseña de desarrollo), sin OpenID Connect (emisor y JWKS por https, algoritmos
asimétricos), sin el secreto del gateway, sin la clave de auditoría o con la clave universal de
desarrollo (`MC_API_KEY`). Ningún proceso arranca, en ningún entorno, si se define una variable que
parezca apagar la autenticación (`AUTH_DISABLED` y sus variantes). Los mensajes nombran la variable,
nunca su valor.

**Por qué.** Una variable olvidada no debe abrir nada: el olvido más común en un despliegue es no
definir algo, y un valor por omisión permisivo convierte ese olvido en una API abierta. Y un modo
"sin autenticación" que no existe, pero que alguien cree que existe, es peor que uno que no arranca:
la variable impide arrancar para que nadie la deje puesta creyendo que funciona.

## 276. Los secretos son archivos, nunca variables de entorno

**Decisión.** Cada secreto se lee de un archivo montado por su variable `*_FILE`
(`MC_DATABASE_URL_FILE`, `MC_API_KEYS_FILE`, `MC_GATEWAY_SECRETS_FILE`, `MC_AUDITORIA_CLAVE_FILE`);
definir la variable y su `*_FILE` a la vez no arranca. En el compose son Docker secrets con modo
0640 y un grupo propio (`MC_SECRETOS_GID`); en la nube, Secret Manager montado como archivos. En
local son efímeros: los genera `scripts/generar_secretos_locales.py`, que no imprime ninguno.

**Por qué.** Una variable de entorno se ve en `docker inspect`, en `/proc/<pid>/environ`, en un
volcado de errores y en los procesos hijos; un archivo montado, solo quien lo lee. Y un secreto que
no existe fuera de una corrida (el CI genera los suyos) no se puede filtrar del repositorio.

## 277. OpenID Connect con PKCE, y la sesión en un BFF

**Decisión.** Las personas entran con Authorization Code y PKCE (S256), `state` y `nonce`, por
oauth2-proxy como BFF (backend for frontend). La SPA nunca toca el flujo ni un token: solo hay una
cookie `__Host-mc_sesion` (`HttpOnly`, `Secure`, `SameSite=Lax`). El IdP local es Dex (usuarios
estáticos, contraseñas efímeras); en un despliegue, el corporativo, del que la API solo necesita su
emisor, su audiencia y su JWKS.

**Por qué.** Un token en el navegador (en `localStorage` o en memoria del JavaScript) lo lee
cualquier script que logre correr en la página: el BFF lo deja del lado del servidor. oauth2-proxy
es un componente probado para esto; escribir el flujo en la API sería más código de seguridad propio
que mantener.

**Alternativas.** Un SDK de OIDC en la SPA (el token en el navegador) o la API como BFF (más código
propio).

## 278. La API valida el token ella misma, y la JWKS falla cerrada

**Decisión.** La API valida cada token con PyJWT: la firma con una llave de la JWKS por su `kid`, un
algoritmo de una lista de asimétricos, `iss` y `aud` exactos, `exp` y `nbf` con 30 s de tolerancia,
`sub`, y `azp` y `typ` si se configuran. La JWKS se guarda 300 s; un `kid` desconocido la vuelve a
pedir a lo más cada 30 s; si el IdP no responde, la vieja sirve hasta una hora y después la API
responde `503 IDENTIDAD_NO_DISPONIBLE`. Cada rechazo tiene un código estable (`TOKEN_EXPIRADO`,
`TOKEN_FIRMA_INVALIDA`...).

**Por qué.** No confiar en que el edge validó: si alguien llega a la red interna, la API sigue
exigiendo un token válido. La caché resiste una caída breve del IdP sin martillarlo, y rota llaves
sin reiniciar; pasada la hora, cerrar es la única respuesta segura: un token no se puede validar con
llaves que quizá ya se revocaron.

## 279. Cuatro permisos, cuatro roles y grupos mapeados

**Decisión.** Cuatro permisos (`mc.read`, `mc.audit`, `mc.execute`, `mc.configure`) y cuatro roles
conceptuales (`VIEWER`, `AUDITOR`, `OPERATOR`, `ADMIN`). Los permisos salen del `scope` del token o de
sus grupos, mapeados a roles por configuración (`MC_OIDC_GRUPOS`); un grupo que no está en el mapa no
da nada.

**Por qué.** Los permisos siguen lo que una ruta hace (leer, leer evidencia, pedir trabajo, publicar
configuración), no quién la usa: así una ruta nueva se clasifica sin inventar un rol. Los grupos son
del IdP y cambian de nombre entre organizaciones: se mapean, no se confían.

## 280. Una matriz explícita: una ruta sin clase no arranca

**Decisión.** Cada operación de la API tiene su clase (`PUBLIC`, `READ`, `AUDIT`, `EXECUTE`,
`CONFIGURE`) en una matriz explícita, y la API no arranca si una operación no está en ella o si la
matriz nombra una que ya no existe. La autorización es una dependencia global; una ruta sin clase,
si llegara a pedirse, sería un `500`. La matriz se publica generada desde el código
(`docs/matriz-autorizacion.md`), y una prueba exige que coincida.

**Por qué.** Un decorador por ruta se olvida en la ruta nueva, y esa queda abierta. Una matriz que el
arranque verifica convierte el olvido en un error al desplegar, no en una brecha en producción.

## 281. El token de una persona solo vale si llega por el edge

**Decisión.** El edge borra toda identidad que traiga el cliente (`Authorization`, `X-API-Key`,
`X-Forwarded-*`, `X-Auth-Request-*`, `traceparent`...) y pone el token de acceso de la sesión y el
secreto del gateway (`X-MC-Gateway`). La API acepta un token de persona solo con ese secreto
(`GATEWAY_REQUERIDO` si no); el secreto admite varios valores para rotarlo sin cortar.

**Por qué.** Sin esa atadura, un token robado se podría usar directo contra la API desde la red
interna, saltándose los límites, la lista blanca y los registros del edge. Y las cabeceras de
identidad que un proxy pone son, si un cliente las manda, una suplantación: se borran siempre.

## 282. Las claves de servicio, por su SHA-256 y con sus permisos

**Decisión.** Un servicio entra con una clave en `X-API-Key`. La API guarda solo su SHA-256, con su
nombre y sus permisos (`MC_API_KEYS`), y la compara en tiempo constante contra todas. Una petición con
un token y una clave a la vez se niega (`CREDENCIALES_AMBIGUAS`).

**Por qué.** Una lista de claves en claro es un secreto más que proteger; sus hashes no sirven para
entrar. Permisos por clave: la prueba de humo de solo lectura no puede ejecutar. Y con dos
credenciales nadie debe elegir cuál vale: esa elección es donde se esconden los errores.

## 283. La sesión vive en el servidor

**Decisión.** oauth2-proxy guarda las sesiones en Valkey (en la nube, Memorystore), con usuario y
contraseña, sin persistencia; la cookie solo las referencia. Cerrar sesión las borra de ahí.

**Por qué.** Con la sesión cifrada en la cookie, cerrar sesión solo la borra del navegador: una cookie
copiada antes sigue valiendo hasta vencer. Con la sesión en el servidor, deja de valer en el momento
(la prueba de humo del edge y Playwright lo prueban). Sin persistencia: perder las sesiones es pedir
que se inicie sesión otra vez, no perder datos.

## 284. Cerrar sesión es un POST del mismo origen, con Referrer-Policy same-origin

**Decisión.** Cerrar sesión es un `POST` a `/oauth2/sign_out` desde la app: `GET` es `405` y un
`Origin` que no sea el de la app, incluido `Origin: null`, es `403 ORIGEN_NO_PERMITIDO`. La cookie es
`SameSite=Lax`. La app pide `Referrer-Policy: same-origin` (cabecera y `<meta>`), no `no-referrer`.

**Por qué.** Cerrar sesión cambia estado: no puede ser un enlace que otro sitio dispare. Con
`no-referrer` el navegador manda `Origin: null` en un `POST` del mismo origen (Fetch, "serializing a
request origin"), y el edge lo rechazaba: cerrar sesión no funcionaba en un navegador de verdad. La
prueba de humo no lo veía porque ponía el `Origin` a mano; lo encontró Playwright. `same-origin`
manda el origen solo al propio sitio y nada a otros.

## 285. Un evento de auditoría por petición, con un seudónimo

**Decisión.** Cada petición a la API, salvo las sondas, deja un evento `security-audit/v1`: quién
(`usr:` y 16 hexadecimales de un HMAC del emisor y el `sub`, o `svc:<nombre>`), qué plantilla de
ruta, su clase, el permiso que pedía, la decisión y su motivo, el estado y la latencia, con el
`request_id` y el `trace_id`. Sin la query, el cuerpo, las cabeceras, la IP ni el token.

**Por qué.** Responder "quién hizo qué" sin guardar un dato personal: el seudónimo es estable para la
misma persona (con la misma clave) y no dice quién es. La integridad del registro es de la plataforma
de logs; una cadena de hashes es un riesgo residual declarado.

## 286. Logs JSON con campos fijos, redactados antes de escribir

**Decisión.** En producción, una línea JSON por evento con campos fijos, el `request_id`, el
`trace_id` y el `span_id`. Un filtro redacta antes de escribir los JWT, los `Bearer`, los pares de
credencial, las contraseñas en URLs y cada secreto configurado; SQLAlchemy nunca pone los parámetros
en un error; los loggers ruidosos (SQLAlchemy, httpx, urllib3) quedan en `WARNING`.

**Por qué.** Un log se copia, se exporta y se lee por gente que no debe ver un secreto ni un dato
personal: lo que no se escribe no se filtra. La prueba que recorre una corrida completa con datos
personales encontró una URL con un `CLIENTE_UNICO` en el registro de un cliente HTTP: por eso los
ruidosos callan.

## 287. Métricas por plantilla de ruta, en un puerto interno

**Decisión.** Prometheus, con etiquetas solo de listas cerradas (la plantilla de la ruta, el
método, la clase del estado, el tipo de trabajo, los códigos de autorización), en un puerto interno
que no pasa por el edge. La cola se mide en PostgreSQL con su índice parcial, a lo más cada 10 s y con
un tope de 2 s. Las series que vigila una alerta con `increase()` nacen en cero.

**Por qué.** Una etiqueta con un UUID o una ruta literal hace crecer la cardinalidad con los datos y
filtra identificadores a quien lee métricas. Las métricas mismas encontraron un error: la lectura de
la cola fallaba en producción por un `FILTER` mal puesto (`mc_cola_lectura_ok` en 0); una prueba
contra PostgreSQL lo cubre.

## 288. Trazas propias, sin la telemetría nativa de FastAPI

**Decisión.** Spans propios: uno de servidor por petición con la plantilla, uno por sentencia SQL
sin su texto (hasta 200 por traza) y uno por trabajo del worker. La telemetría nativa de FastAPI se
apaga entera. Muestreo por cola en el colector (errores y lentas siempre); el exportador corre en
segundo plano y falla abierto. El colector borra además los atributos sensibles conocidos.

**Por qué.** FastAPI 0.142 crea por su cuenta, en cuanto existe un proveedor de trazas, un segundo
span de servidor con la ruta real y la query (con un identificador de cliente adentro), y registraría
los valores de entrada de una validación fallida: se encontró en Jaeger al escribir v0.18 y una
prueba exige un solo span de servidor sin la ruta real. Fallar abierto: medir no puede tumbar lo que
se mide (sin colector, la API respondió igual).

**Medido.** Exportar trazas al 100 % sube el p95 de 103.6 a 121.3 ms a 25 lecturas por segundo, con la
mediana igual ([carga.md](carga.md)).

## 289. Vivo y listo son cosas distintas

**Decisión.** `/health/live` dice que el proceso responde (en el worker, que su ciclo late);
`/health/ready`, que la base contesta con el esquema de esta versión. Una base caída deja a la API y
al worker vivos y no listos: Docker no los reinicia, y se recuperan solos al volver.

**Por qué.** Si la liveness dependiera de la base, una caída de la base reiniciaría en bucle a la API
y al worker, y la recuperación sería más lenta y más ruidosa. Medido: no listos en 0.6 s, de vuelta
en 1.1 s, sin reinicios.

## 290. El compose de producción: un servicio, un rol, una red

**Decisión.** `docker-compose.production.yml`: cada servicio con su rol de base (`mc_migraciones`
dueño del esquema; `mc_api` y `mc_worker`, solo filas; `mc_respaldo`, solo lectura), en sus redes
internas (frontal, identidad, sesiones, datos, ruteo, observabilidad), y solo el edge en la red
pública, solo en `127.0.0.1`. Las migraciones corren antes, con un candado de PostgreSQL contra dos a
la vez.

**Por qué.** Un servicio comprometido no debe poder hacer lo que hacen los demás: la API no puede
alterar el esquema, el respaldo no puede escribir, y Valkey no ve la base. Y un puerto que no se
publica no se puede atacar desde afuera.

## 291. Contenedores sin raíz, sin capacidades y de solo lectura

**Decisión.** Cada contenedor con su usuario numérico, `cap_drop: ALL`, `no-new-privileges`, el
sistema de archivos de solo lectura (tmpfs donde escribe), límites de CPU, memoria y procesos, y logs
rotados. La imagen de la API no tiene compilador ni `uv`. La prueba de humo lo revisa contenedor por
contenedor.

**Por qué.** Lo que un atacante logre ejecutar dentro de un contenedor queda sin privilegios, sin
poder persistir nada en la imagen y sin poder agotar la máquina.

## 292. El edge limita por IP y responde 429 con Retry-After

**Decisión.** Límites por IP del cliente (lecturas, lecturas caras, inicio de sesión, envíos de
contraseña, conexiones), de cuerpo, de cabeceras y de tiempo, con respuestas JSON propias: `429` con
`Retry-After`, `413`, `405`. El edge es el primer salto: no cree ningún `X-Forwarded-For`.

**Por qué.** Una ráfaga de un cliente no debe tumbar a los demás, y el formulario de contraseñas no
debe permitir probar contraseñas sin límite (Dex no bloquea cuentas). Medido: a 80 lecturas por
segundo desde una IP, el 46 % fue `429`, cada una con `Retry-After`, y ninguna un 5xx.

## 293. Una CSP sin unsafe-inline ni unsafe-eval

**Decisión.** La app: todo del mismo origen, sin `unsafe-inline` ni `unsafe-eval`, con
`frame-ancestors`, `object-src` y `base-uri` en `'none'`. HSTS solo con TLS. Las páginas de Dex (solo
en el compose local) llevan su propia CSP: su CSS en línea y su único script en línea, permitido por
su SHA-256.

**Por qué.** La CSP es la segunda barrera contra una inyección: un script que logre entrar no corre.
React y las gráficas ponen estilos por el CSSOM, que la CSP no bloquea, así que la app no necesita
`unsafe-inline`. El script de Dex se encontró en Playwright (un error de CSP en la consola); un hash lo
permite a él y a nada más, y si una versión nueva de Dex lo cambia, la prueba lo detecta.

## 294. El respaldo lleva la base y el almacén juntos

**Decisión.** `scripts/respaldo.py` respalda PostgreSQL (`pg_dump` de una foto exportada, con los
conteos y la lista de artefactos de esa misma foto) y copia del almacén los artefactos de esa lista,
firmando cada uno. Un artefacto que falte niega el respaldo entero y no deja nada. El manifiesto no
lleva ningún dato.

**Por qué.** El estado vive en los dos: una base sin su almacén tiene corridas cuyo archivo no existe.
La foto exportada da los conteos exactos del volcado, y el orden de escritura de la aplicación
(primero el objeto, después la fila) garantiza que todo lo que la foto registra ya está en el
almacén.

## 295. Se restaura solo en un entorno nuevo, y nada alterado toca nada

**Decisión.** Restaurar verifica primero el respaldo entero (el volcado, la lista y cada objeto) y
después exige un destino vacío, que no sea el origen, con un PostgreSQL de igual o mayor versión, con
las extensiones y con la revisión esperada. Copia el almacén y restaura la base en una sola
transacción, sin el `CREATE SCHEMA public` ni los datos de `spatial_ref_sys` (los pone la plataforma),
y valida filas, restricciones y artefactos. Dos servicios separados: el que respalda lee; el que
restaura escribe con el rol dueño del esquema y monta los respaldos de solo lectura.

**Por qué.** Restaurar sobre la base en uso mezcla lo viejo con lo nuevo, y un respaldo alterado que
se restaura a medias es peor que ninguno. Las dos exclusiones se encontraron restaurando de verdad:
`pg_dump --schema=public` declara el esquema (que ya existe) y trae los datos de una tabla de PostGIS
que no es del rol que restaura.

## 296. Simulacros medidos, no supuestos

**Decisión.** `scripts/simulacros.py` rompe una cosa a la vez en el compose de producción (la base,
la API, el worker, OSRM, el colector), mide desde adentro de las redes internas y la repone;
`scripts/simulacro_de_restauracion.py` ensaya la restauración con cinco respaldos alterados. Los dos
corren en el CI.

**Por qué.** "Se recupera solo" es una hipótesis hasta que se mide. Los simulacros también prueban
los runbooks: lo que dicen que se espera es lo que pasó.

## 297. La carga, registrada antes de correrla

**Decisión.** Los escenarios, las tasas y los umbrales de k6 se escribieron en `docs/carga.md` antes
de la corrida, y no se movieron después. Un ensayo de 10 s cruzó un umbral (arranque en frío) y se
reporta junto con la corrida registrada de 60 s, que los cumplió. El orden caro (por saldo) se mide
aparte.

**Por qué.** Un umbral que se ajusta después de ver el resultado no prueba nada. Reportar el ensayo es
parte de no hacerlo.

## 298. La cadena de suministro, fijada y revisada

**Decisión.** Las imágenes por digest, las acciones de GitHub por el SHA de su commit, las
dependencias y el proveedor de OpenTofu con sus hashes en los lockfiles; los workflows con permisos
mínimos y checkouts sin la credencial en el disco. `scripts/verificar_cadena_de_suministro.py` lo
revisa en el CI. Dependabot propone las actualizaciones con 7 días de espera.

**Por qué.** Una etiqueta (`v7`, `latest`, `16-alpine`) se puede mover a otro contenido; un digest o
un SHA, no. Lo que no se fija no se puede auditar, y lo que se audita una vez se desfija solo si un
script no lo exige.

## 299. Las imágenes propias bloquean el CI; las de terceros informan

**Decisión.** Trivy detiene el CI con una vulnerabilidad alta o crítica con corrección en las tres
imágenes propias, y genera su SBOM (CycloneDX). Las de terceros del camino de una petición se
escanean como informe. pip-audit y npm audit detienen el CI. Las imágenes de Alpine aplican al
construir las correcciones de seguridad publicadas después de su base; la de respaldo no lleva
`gosu`.

**Por qué.** Lo que construimos lo podemos corregir hoy; una imagen de terceros se corrige cuando su
proyecto publica, y bloquear por ella detendría todo sin poder hacer nada. Al cerrar v0.18 la base de
nginx y la de PostGIS (en su última versión publicada) traían altas ya corregidas en Alpine, y el
`gosu` de PostgreSQL un Go viejo: con la actualización al construir y sin `gosu`, quedaron en 0.

## 300. Gitleaks sobre todo el historial, con dos excepciones literales

**Decisión.** Gitleaks revisa el historial completo en cada run, con las reglas por omisión y dos
excepciones literales y justificadas: la clave de desarrollo documentada (pública a propósito) y los
JWT falsos de una prueba de redacción, solo en su archivo.

**Por qué.** Un secreto que entró al historial sigue ahí aunque se borre del árbol. Las excepciones
son literales, no patrones amplios, para que un secreto real con otra forma no pase por ellas.

## 301. La referencia de Google Cloud: un servicio con tres contenedores, nunca aplicada desde aquí

**Decisión.** `infra/gcp` describe en OpenTofu un entorno de Google Cloud: el edge, oauth2-proxy y la
API como un servicio de Cloud Run con tres contenedores detrás de un balanceador con Cloud Armor; el
worker en Cloud Run con CPU siempre asignada; Cloud SQL privada con PITR; Filestore para el almacén;
OSRM en una VM sin IP pública; los respaldos nocturnos en un bucket; una cuenta de servicio por
función, sin `editor` ni `owner`; Secret Manager sin valores en el estado. Se valida en cada run
(fmt, init, validate, el plan sin credenciales, 36 reglas de política) y nunca se aplica.

**Por qué.** Con los tres contenedores en la misma instancia, la API no tiene ninguna entrada propia
y el edge no necesita llegar a ella por HTTPS entre servicios. Aplicar crea recursos con costo en un
proyecto real: es una decisión humana. Lo que falta para un despliegue real (la plantilla del edge
detrás del balanceador, entre otras) está escrito en `docs/cloud-gcp.md`.

## 302. El despliegue solo a mano, con aprobación y sin llaves

**Decisión.** `.github/workflows/deploy.yml` corre solo por `workflow_dispatch`, desde `main` o una
etiqueta, con una confirmación escrita, con el environment de GitHub del entorno (que exige
aprobación) y solo si ese environment declara su proyecto y su identidad. Entra por Workload Identity
Federation, sin llaves; construye, corre las migraciones y despliega por digest; no aplica la
infraestructura. Las entradas llegan al script por el entorno, nunca interpoladas.

**Por qué.** Un despliegue que se dispara solo, o con una llave guardada en el repositorio, es un
despliegue que alguien puede disparar sin querer o sin permiso. Interpolar una entrada en un script es
una inyección.

## 303. Qué no resuelve v0.18.0

**Qué deja v0.18.0.** motor-cartera se despliega con identidad (OIDC con PKCE y un BFF), permisos por
ruta en una matriz que el arranque verifica, secretos montados, contenedores endurecidos, un edge con
TLS, límites y CSP, logs, métricas, trazas y auditoría sin datos personales, alertas con runbooks,
respaldos de la base y del almacén con su restauración ensayada, simulacros de falla, pruebas de
carga registradas, una cadena de suministro fijada y escaneada, y una referencia de Google Cloud
validada.

**Qué no resuelve.** No despliega en la nube (la referencia nunca se aplicó, y le falta la plantilla
del edge detrás del balanceador); el tráfico interno no va cifrado; Dex es solo para desarrollo y no
bloquea cuentas; la auditoría no es a prueba de manipulación; las imágenes de terceros se escanean sin
bloquear; las imágenes no son reproducibles bit por bit; no hubo pentest externo; y sigue sin ejecutar
ninguna acción de cobranza. Lo que sigue es v1.0, Collection Intelligence Platform: integrar lo que
cada versión construyó en una plataforma completa de inteligencia de cobranza, sin perder la
auditabilidad, el punto en el tiempo, la privacidad ni la seguridad.
