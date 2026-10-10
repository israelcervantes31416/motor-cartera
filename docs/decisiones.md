# Decisiones de diseño — fase 1, Decision Engine, Motor Territorial, Motor de Ruteo, Orquestación Durable, Fuentes Oficiales, Modelo Histórico, Motor de Pagos y Lifecycle de Cobranza

Por qué la fase 1 (v0.1.0), el Decision Engine (v0.2.0), el Motor Territorial (v0.3.0), el Motor
de Ruteo (v0.4.0), la Orquestación Durable (v0.5.0), las Fuentes Oficiales, Evidencia Inmutable y
Escala (v0.6.0), el Modelo Histórico y Cuenta 360 (v0.7.0), el Motor de Pagos Canónico y
Conciliación (v0.8.0) y el Lifecycle de Cobranza y Atribución Operativa (v0.9.0) están hechos como
están, y qué haría distinto o cuándo cambiaría cada decisión. El uso está en el
[README](../README.md), en [fuentes.md](fuentes.md), en [historia.md](historia.md), en
[cuenta_360.md](cuenta_360.md), en [motor_pagos.md](motor_pagos.md), en
[lifecycle.md](lifecycle.md) y en [atribucion.md](atribucion.md); aquí va el porqué.

Las secciones 1 a 15 son de la fase 1; las 16 a 22, del Decision Engine; las 23 a 30, del Motor
Territorial; las 31 a 41, del Motor de Ruteo; las 42 a 53, de la Orquestación Durable; las 54 a 70,
de las Fuentes Oficiales; las 71 a 84, del Modelo Histórico; las 85 a 98, del Motor de Pagos, y
las 99 a 112, del Lifecycle de Cobranza y la Atribución Operativa.
Donde las de la fase 1 hablan de la orquestación de la fase 3, hoy es la orquestación durable de
v0.5.0. Las secciones que una versión posterior cambió lo dicen al final, en un párrafo *Desde
v0.5.0*, *Desde v0.6.0*, *Desde v0.7.0*, *Desde v0.8.0* o *Desde v0.9.0*.

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
