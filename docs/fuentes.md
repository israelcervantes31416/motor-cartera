# Fuentes oficiales, evidencia inmutable y escala (v0.6.0)

Cómo entran al sistema los archivos del acreedor desde v0.6.0: qué contratos hay, qué se guarda de
cada archivo, cómo se rastrea cada registro hasta su origen y cuánto aguanta. Los porqués están en
[decisiones.md](decisiones.md), secciones 54 a 70. Los diccionarios de datos, en
[diccionario_cartera.md](diccionario_cartera.md) (93 columnas),
[diccionario_pagos.md](diccionario_pagos.md) (23 columnas) y [carrier.md](carrier.md) (la hoja
compañera).

**Ningún dato real entra a este repositorio.** Todo lo que aquí se describe se prueba con el
generador sintético incluido, y el CI falla si alguien sube una hoja de cálculo, un zip, un Parquet,
un volcado de una base o cualquier otro formato de datos.

## Los tres contratos

| Contrato | Qué es | Columnas | Una fila es | Llave dentro del archivo |
|---|---|---|---|---|
| `cartera/v1` | La cartera mínima de v0.1–v0.5, **congelada** | 8 (con alias) | una cuenta | `cliente_unico` |
| `cartera/v2` | La hoja CARTERA de la cartera oficial | 93 exactas | una cuenta al corte | `CLIENTE_UNICO` |
| `pagos/v1` | Los movimientos económicos de un periodo | 23 exactas | un movimiento | ninguna |

- **cartera/v1 no cambia.** Sus 8 columnas, sus alias, su validación y su firma son las de siempre,
  para que cada corrida que ya se juzgó se pueda volver a explicar igual. Es el contrato por
  omisión de `POST /corridas` y de `motor-cartera cargar`.
- **cartera/v2 se declara**, nunca se adivina: `contrato=cartera/v2` en la subida, con su
  `fecha_corte`. La fecha de corte no es una columna (las 93 no traen una): es metadata del lote y
  queda en la corrida. Con cartera/v1, declarar una fecha de corte es un error, porque v1 la trae
  en sus filas.
- **pagos/v1 tiene su propio recurso**, `/pagos`: un archivo de pagos no es una corrida, porque no
  publica cuentas.

```bash
# cartera/v1, como siempre
curl -H "X-API-Key: $CLAVE" -F archivo=@cartera.xlsx http://localhost:8000/corridas
# cartera/v2, con su contrato y su corte declarados
curl -H "X-API-Key: $CLAVE" -F archivo=@cartera_oficial.zip -F contrato=cartera/v2 \
     -F fecha_corte=2026-09-30 http://localhost:8000/corridas
# pagos/v1
curl -H "X-API-Key: $CLAVE" -F archivo=@pagos.zip http://localhost:8000/pagos
```

Por línea de comandos: `motor-cartera cargar archivo --contrato cartera/v2 --fecha-corte
2026-09-30` y `motor-cartera cargar-pagos archivo`. Las dos pasan por la cola durable, como la API.

### Estructura exacta

cartera/v2 y pagos/v1 exigen **exactamente** sus columnas: ninguna de menos, ninguna de más, sin
encabezados repetidos ni columnas sin nombre. El orden no importa. Un encabezado solo se normaliza
quitándole los espacios de alrededor y llevándolo a NFC: no se adivinan sinónimos. Una desviación no
juzga ningún registro y deja la corrida o la ingesta `FALLIDA`, con un detalle que dice qué falta y
qué sobra. No hay descartes silenciosos: nada se filtra sin decirlo.

### Formatos

Se aceptan xlsx, csv y zip (con un csv adentro), y el formato se comprueba **por el contenido**, no
solo por la extensión: un xlsx tiene que ser un zip con la estructura de un libro de Excel, y un csv
tiene que ser texto (UTF-8 o cp1252, sin bytes nulos ni la firma de un binario conocido). Lo que no
corresponde se rechaza antes de guardarse, con `415 FORMATO_NO_CORRESPONDE`; una extensión que no se
acepta, con `415 FORMATO_NO_SOPORTADO`.

En un xlsx de cartera/v2 se lee la hoja CARTERA; en uno de pagos, la única hoja con la estructura de
pagos/v1. En un zip, el único csv con la estructura del contrato. Cuando hay más de un candidato, no
se elige a ciegas: es un error.

## Dos representaciones de cada archivo

| | Original | Conformado (*source-conformed*) |
|---|---|---|
| Qué es | Los bytes exactos que se recibieron | Los registros válidos, tipados |
| Dónde vive | El almacén de artefactos | El mismo almacén, como Parquet |
| Identidad | El SHA-256 del archivo (`firma`) | Su propio SHA-256, y la firma del contenido |
| Columnas | Las del acreedor, como llegaron | Las del contrato en su orden oficial, más `_source_row` y `_source_sheet` |
| Tipos | Texto | Importes `decimal(14, 2)`, fechas `date32`, instantes `timestamp[us]`, enteros `int64` |
| Para qué | Evidencia: reproducir exactamente lo que llegó | Que v0.7 no vuelva a interpretar un xlsx de cientos de miles de filas |

El conformado **no lleva scores ni variables derivadas**: es la fuente, validada y normalizada. Es
reproducible: el mismo archivo con la misma versión del contrato produce el mismo Parquet, byte por
byte, y una prueba lo regenera desde el original y compara los bytes. En pagos/v1 trae **todos** los
movimientos válidos, sin deduplicar ninguno.

**Desde v0.7.0**, el conformado es también la única entrada del modelo histórico: cada dataset de
cartera/v2 se materializa en un corte canónico con un snapshot por cuenta, y cada uno de pagos/v1,
en un pago observado por fila, sin volver a leer el original ([historia.md](historia.md)).

## El almacén de artefactos

Cada archivo que llega se copia, por bloques y antes de tocar la base, a un almacén **por
contenido** (`LocalContentAddressedStore`, detrás de la interfaz `SourceArtifactStore`):

```text
$MC_SOURCE_STORE_ROOT/
  sha256/ab/abcdef...   cada objeto, de solo lectura, nombrado por su SHA-256
  tmp/                  escrituras en curso: nunca son objetos
```

- **Por contenido.** El mismo archivo subido dos veces, con el nombre que sea, es un solo objeto: no
  se duplican bytes. El nombre original es metadata en la base.
- **Atómico.** Se escribe a un temporal en la misma raíz, se hace `fsync` y se mueve con
  `os.replace`. Un objeto existe completo o no existe; una escritura interrumpida deja, a lo más,
  un temporal.
- **Inmutable y para siempre.** Los objetos son de solo lectura y **no se borran**: ni al terminar
  la ingesta, ni al cerrar su trabajo, ni al agotarse sus intentos, ni al bajar la migración. El
  esquema lo gobierna Alembic; el sistema de archivos, no.
- **Verificable.** Antes de juzgar, el worker vuelve a firmar el objeto: si sus bytes ya no son los
  de su SHA-256, la corrida termina `FALLIDA` con el motivo, y si el objeto falta (un volumen que
  no se montó), el trabajo se reintenta. `motor-cartera verificar-fuentes` vuelve a firmar cada
  artefacto registrado y termina con error si alguno falta o está dañado.
- **Primero el objeto, después la base.** Si la base falla después de guardar, queda un objeto
  huérfano: ocupa disco, pero no es evidencia de nada ni se lee. Lo contrario (una fila que apunta a
  bytes que no existen) no puede pasar.
- **Nunca se exponen rutas.** La API dice `artifact_id`, `sha256`, `tamano_bytes`,
  `nombre_original`, `formato` y `media_type`, nunca dónde vive el objeto.

En el compose, el almacén es un volumen propio (`fuentes`), aparte del de PostgreSQL, montado en la
API y en el worker. El CI lo prueba: baja los contenedores sin borrar volúmenes, los vuelve a
levantar y verifica que cada artefacto siga ahí con sus mismos bytes.

**Lo que heredó v0.5.** Las corridas que seguían en la cola al migrar guardaban su archivo en
`archivo_corrida` (`BYTEA`). La 0007 no lo toca y el worker de v0.6 las termina con ese archivo. Las
corridas nuevas no lo usan: PostgreSQL ya no guarda archivos.

## Linaje

```text
artefacto_fuente (original, SHA-256)
  ├── corrida (cartera/v1 o cartera/v2, con su firma y su firma de contenido)
  │     ├── cuentas, rechazos ............ lo publicado, y lo que no
  │     ├── hoja_companera ............... CARRIER, auditada
  │     └── dataset_conformado ──> artefacto_fuente (Parquet)
  └── ingesta_pagos (pagos/v1)
        ├── rechazos de pagos
        └── dataset_conformado ──> artefacto_fuente (Parquet)
```

- **De un registro a su fila.** Cada registro del conformado trae `_source_row` (la fila del archivo
  original) y `_source_sheet` (su hoja, o su miembro dentro del zip). Cada rechazo trae su fila.
- **Dos firmas, dos preguntas.** `firma` es el SHA-256 del archivo: misma firma, mismo archivo.
  `firma_contenido` es la de sus registros válidos en forma canónica, ordenados: no depende del
  formato, del orden de las filas ni del orden de las columnas. La misma cartera en xlsx y en csv
  tiene dos firmas de archivo y una sola firma de contenido. Está versionada con el contrato.
- **`GET /corridas/{run_id}/fuente`** y **`GET /pagos/{pagos_run_id}/fuente`** dan la evidencia: el
  original, el conformado (con su contrato, sus filas, sus columnas y su firma) y, en cartera/v2,
  la auditoría de CARRIER.

## Juicio por lotes, con memoria acotada

Un archivo de cartera/v2 o de pagos/v1 nunca está entero en memoria. Se lee por lotes de
`MC_FILAS_POR_LOTE` filas (50,000 por omisión; csv, csv dentro de zip y xlsx en modo de solo
lectura), en dos pasadas:

1. **Primera pasada.** Cada lote se juzga contra el contrato, con validaciones vectorizadas: tipos,
   formas, rangos y catálogos. Los rechazos se copian a PostgreSQL con `COPY`; los válidos van a un
   Parquet provisional en disco. Se cuenta cada llave (`CLIENTE_UNICO`), de todos los registros.
2. **Segunda pasada.** Con las llaves de todo el archivo a la vista, se rechazan todas las copias
   de una llave repetida, aunque estén en lotes distintos. Los válidos se firman y, si el archivo
   pasa la barrera, se escriben al conformado, se proyectan y se publican.

Todo va en una transacción: se publica el archivo entero o nada.

## La proyección operacional (cartera/v2 → Cuenta)

Los motores (`decision/v1`, `territorial/v1`, `ruteo/v1`) leen `Cuenta`, la forma de cartera/v1. La
proyección `operacional/v1` lleva cada registro válido de cartera/v2 a esa forma, de manera
explícita y versionada:

| cartera/v2 | Cuenta |
|---|---|
| `CLIENTE_UNICO` | `cliente_unico` |
| `SALDO_TOTAL` | `saldo_total` |
| `DIAS_ATRASO` | `dias_atraso` |
| `PRODUCTO` | `producto` (mismo catálogo) |
| `CANAL` | `canal` (mismo catálogo) |
| `ESTADO_CTE` + `POBLACION_CTE` | `cve_entidad` + `cve_municipio` |
| la fecha de corte declarada | `fecha_corte` |

cartera/v2 trae nombres de estado y de población, no claves. Se resuelven con el **catálogo público
del INEGI** (Marco Geoestadístico, servicio wscatgeo v2: 32 entidades y 2,478 municipios), versionado
con el código en `fuentes/catalogo_inegi.json` y regenerable con
`scripts/actualizar_catalogo_geografico.py`. Los nombres se comparan sin acentos ni mayúsculas; una
entidad se reconoce por su nombre oficial, su abreviatura o unos pocos alias controlados a mano.
**Nada se adivina**: un estado o un municipio que no está en el catálogo, o un nombre que corresponde
a dos municipios de la misma entidad, rechaza el registro con su motivo. La corrida registra
`version_proyeccion`, y su detalle dice con qué catálogo se resolvió.

## Un despacho, una cartera

v0.6 opera **un despacho** (`DSP_001`) que gestiona **una cartera** (`CARTERA_PRINCIPAL`). Son
metadata del sistema, no columnas de las fuentes: se configuran con `MC_DESPACHO_ID` y
`MC_CARTERA_ID`, y cada corrida y cada ingesta de pagos guarda con cuáles se registró. Que el valor
viaje con cada registro es lo que permite cambiarlo después sin reescribir nada; administrar varios
despachos o varias carteras no es de v0.6.

## Pagos

Una ingesta de pagos es su propia entidad (`IngestaPagos`, identificada por `pagos_run_id`), con sus
rechazos y su trabajo `INGESTA_PAGOS` en la misma cola durable que las corridas: lease, latido,
reintentos con espera, recuperación cuando un worker muere e idempotencia si el trabajo se entrega
dos veces. No encadena nada.

- **Una fila es un movimiento, y no se deduplica nada.** Varios pagos del mismo cliente son válidos;
  dos filas idénticas son dos movimientos y las dos quedan en el conformado. La llave histórica de
  deduplicación (cliente, recepción al segundo e importe a centavos) queda documentada para el motor
  de pagos de v0.8.
- **Barrera conservadora.** Con la tolerancia por omisión (`MC_TOLERANCIA_RECHAZO_PAGOS=0`), un
  solo movimiento inválido rechaza el archivo entero (`RECHAZADA`), y sus rechazos quedan a la
  vista, con su fila, sus 23 valores y su motivo. Un archivo de dinero aceptado a medias
  subestimaría la recuperación sin que nadie lo notara.
- **El mismo archivo se acepta una vez.** Subirlo otra vez da `409 PAGOS_YA_ACEPTADOS` (o
  `PAGOS_EN_PROCESO` mientras se procesa). Uno rechazado se puede corregir y volver a subir.

| Método | Ruta | Qué hace |
|---|---|---|
| `POST` | `/pagos` | Registra la ingesta y su trabajo; responde `201` `EN_PROCESO`, con `Location` |
| `GET` | `/pagos/{pagos_run_id}` | Estado, conteos, firmas, tiempos y su `trabajo_id` |
| `GET` | `/pagos/{pagos_run_id}/rechazos` | Los movimientos rechazados, paginados, por fila |
| `GET` | `/pagos/{pagos_run_id}/fuente` | El archivo original y el conformado |

## El generador sintético

| Comando | Qué escribe |
|---|---|
| `motor-cartera generar` | La cartera de cartera/v1, como siempre (8 columnas) |
| `motor-cartera generar-oficial` | Una cartera oficial (cartera/v2, con CARRIER en xlsx y zip) y los pagos de la semana que termina en su corte |
| `motor-cartera generar-escenario` | Varios cortes relacionados y los pagos entre ellos, con un manifiesto |

Lo sintético no se confunde con algo real: los teléfonos empiezan con 0 (ningún número nacional de
México empieza así), la `CLAVE_SPEI` empieza con 000 (que no es la clave de ningún banco), los
nombres y las calles salen de listas genéricas, y `LATITUD` y `LONGITUD` van vacías. Los atributos
fijos de cada cliente (nombre, domicilio, teléfonos, producto, municipio) se derivan de un hash de
su número y la semilla: son los mismos sin importar en qué lote, en qué orden o en qué corte se
generen.

### Perfiles de escala

| Perfil | Cuentas | Para qué |
|---|---|---|
| XS | 1,000 | Desarrollo y CI (es el valor por omisión) |
| S | 10,000 | Pruebas manuales |
| M | 100,000 | Carga media |
| L | 250,000 | Carga alta |
| **XL** | **500,000** | **El escenario empresarial objetivo** |
| XXL | 1,000,000 | Prueba de esfuerzo |

Un tamaño grande se pide con su perfil (`--perfil XL`), nunca por accidente. Se arma y se escribe
por bloques, así que XL y XXL no están enteras en memoria; para esos tamaños conviene csv o zip,
porque escribir un xlsx de ese tamaño es lento por el formato. Ningún perfil grande corre en el CI.

### Escenario longitudinal

`generar-escenario` no genera cada corte como un universo independiente: cada corte sale del
anterior.

```bash
motor-cartera generar-escenario --perfil S --cortes 5 --primer-corte 2026-09-02 --formato zip
```

De un corte al siguiente (cada 7 días por omisión), los pagos del periodo bajan los saldos y curan
el atraso, las cuentas cuyos pagos cubren su saldo se liquidan y salen, el acreedor retira algunas
(`--tasa-retiros`) y llegan altas (`--tasa-altas`). El manifiesto `escenario.json` trae cada archivo
con su SHA-256, lo que pasó en cada corte (continúan, liquidadas, retiradas, altas) y las
invariantes que el escenario cumple, que las pruebas verifican sobre los archivos escritos:

- es determinista: la misma semilla produce los mismos archivos, byte por byte, también en zip;
- cada corte cumple cartera/v2, cada periodo cumple pagos/v1, y `CLIENTE_UNICO` es único en cada
  corte, aunque aparezca en muchos;
- una alta nunca reusa un cliente que ya estuvo en la cartera;
- una cuenta que continúa conserva su identidad y sus atributos fijos;
- los pagos de un periodo son de cuentas del corte que lo abre y se reciben dentro del periodo;
- el saldo de una cuenta que continúa es el anterior menos lo que recuperó en el periodo, contando
  una vez cada movimiento (un repetido exacto es el mismo pago reportado dos veces);
- una cuenta cuyos pagos cubren su saldo sale de la cartera;
- el atraso vuelve a 0 si la cuenta pagó algo en el periodo; si no, crece con los días del periodo;
- CARRIER no introduce clientes ni teléfonos que su corte no traiga, y hay un solo despacho.

No es un modelo financiero: es coherencia básica y determinismo, para que v0.7 tenga historia con
que construir el modelo canónico. Desde v0.7.0 es su banco de pruebas: una prueba materializa la
historia de un escenario y la cruza con su manifiesto (cada alta es una primera observación en su
corte; cada cuenta liquidada o retirada, una salida observada en el siguiente), y la prueba de humo
del CI lleva un escenario de cuatro cortes hasta la Cuenta 360.

## Benchmark de escala

`scripts/benchmark_escala.py` mide un perfil de punta a punta contra PostgreSQL, fuera del CI:
genera el archivo, lo guarda en el almacén, lo juzga, escribe el conformado, proyecta y persiste,
y reporta cada fase, las filas por segundo y la memoria pico de cada etapa, que corre en su propio
proceso.

```bash
MC_DATABASE_URL=postgresql+psycopg://motor:motor@localhost:5434/cartera_bench \
  python scripts/benchmark_escala.py --perfil XL --formato zip
```

La base tiene que llamarse `*_bench`: el script le aplica las migraciones, la vacía y le escribe
cientos de miles de filas. También hay un workflow manual (`Benchmark de escala`, en Actions) que
corre el perfil que se elija en un runner de GitHub y deja la tabla en el resumen del run.

**Resultados en la máquina de desarrollo** (Windows 11, 12 CPU, Python 3.14.6, PostgreSQL 16
local), perfil XL en zip, semilla 31416:

| Fuente | Filas | Archivo | Generación | Almacén | Validación | Conformado | Proyección | Persistencia | Ingesta total | Filas/s | Memoria pico |
|---|---|---|---|---|---|---|---|---|---|---|---|
| cartera/v2 | 500,000 | 148.9 MiB | 37.8 s | 0.5 s | 127.9 s | 4.6 s | 5.8 s | 10.2 s | 149.0 s | 3,355 | 930 MiB |
| pagos/v1 | 279,959 | 11.1 MiB | 3.4 s | 0.0 s | 20.6 s | 0.9 s | 0.0 s | 0.0 s | 21.5 s | 12,998 | 469 MiB |

Las 500,000 cuentas se publicaron sin rechazos, con su CARRIER de 1,150,136 filas auditada, y la
memoria pico de la ingesta fue de 930 MiB: **XL se procesa sin OOM**. La validación incluye leer el
archivo (17.4 s), juzgar los registros (59.8 s), firmar el contenido (26.7 s) y auditar CARRIER
(19.2 s). La memoria pico de la generación fue de 573 MiB.

**Prueba de esfuerzo XXL** en la misma máquina, en zip:

| Fuente | Filas | Archivo | Generación | Almacén | Validación | Conformado | Proyección | Persistencia | Ingesta total | Filas/s | Memoria pico |
|---|---|---|---|---|---|---|---|---|---|---|---|
| cartera/v2 | 1,000,000 | 297.6 MiB | 64.5 s | 1.6 s | 241.2 s | 9.1 s | 11.6 s | 19.3 s | 282.8 s | 3,536 | 1,137 MiB |
| pagos/v1 | 561,063 | 22.2 MiB | 6.4 s | 0.1 s | 41.4 s | 1.9 s | 0.0 s | 0.0 s | 43.3 s | 12,959 | 560 MiB |

El millón de cuentas se publicó entero, con su CARRIER de 2,300,606 filas auditada. Duplicar el
tamaño duplicó el tiempo (las filas por segundo se mantienen) y subió la memoria pico de 930 a 1,137
MiB: la memoria la acota el lote, no el archivo. Lo que crece con el archivo es el conteo de llaves
de la primera pasada y la auditoría de CARRIER, que guarda los clientes de CARTERA.

No hay un SLA: los tiempos dependen del hardware. Lo que el benchmark fija es que el perfil
objetivo se procesa entero, con la memoria acotada, y cuánto cuesta cada fase.
