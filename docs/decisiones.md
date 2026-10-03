# Decisiones de diseño — fase 1, Decision Engine y Motor Territorial

Por qué la fase 1 (v0.1.0), el Decision Engine (v0.2.0) y el Motor Territorial (v0.3.0) están
hechos como están, y qué haría distinto o cuándo cambiaría cada decisión. El uso está en el
[README](../README.md); aquí va el porqué.

Las secciones 1 a 15 son de la fase 1; las 16 a 22, del Decision Engine, y las 23 a 30, del Motor
Territorial. Donde las de la fase 1 hablan de la orquestación de la fase 3, hoy es la
orquestación durable de v0.5.0.

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

**Corridas abandonadas.** Si la API se reinicia a media corrida, esa corrida queda
`EN_PROCESO` y nadie la termina. Para que no bloquee ese archivo para siempre, una corrida
`EN_PROCESO` de más de 15 minutos deja de contar.

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
decisión en cuanto se publica.

v0.3.0 organiza el trabajo de campo y explica cada lugar. No lo recorre.
