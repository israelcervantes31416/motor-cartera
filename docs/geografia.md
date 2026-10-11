# La geografía real (`geografia/v1`)

Hasta v0.10, la ubicación de una cuenta era lo que decían dos textos de su cartera, `ESTADO_CTE` y
`POBLACION_CTE`, resueltos contra el catálogo de claves del INEGI. cartera/v2 trae además `LATITUD`
y `LONGITUD`, y v0.11 las usa por primera vez. `geografia/v1` responde una pregunta precisa:

> Para cada cuenta de un corte, ¿qué coordenada se observó, sirve, en qué municipio de una
> referencia geográfica oficial y declarada cae, y coincide con el municipio que resolvió la fuente?

Es un **hecho geográfico**, no una política territorial. A qué base operativa pertenece cada cuenta
lo decide `territorial/v2` ([territorial_v2.md](territorial_v2.md)), que reutiliza esta misma
geografía sin volver a calcular ningún punto en polígono. Y nada se corrige: una coordenada dudosa
se queda como vino, con su motivo.

```
VersionGeografia          una referencia importada: proveedor, producto, edición, capa, SHA-256 del
      │                   archivo, sistema de origen, reparaciones, estado
MunicipioGeografico       cada municipio de esa versión: CVEGEO, nombre oficial y su MultiPolygon en
      │                   EPSG:4326, con índice GiST
EjecucionGeografia        la geografía de un corte con una versión declarada: firma de entrada,
      │                   conteos por estado y distribución
ResultadoGeografiaCuenta  cada cuenta: la coordenada observada, el punto, el municipio espacial, el de
                          la fuente, su comparación y los motivos
```

El código vive en `src/motor_cartera/geografia/`: `reglas.py` (el vocabulario y la regla de una
cuenta) y `crs.py` (reconocer un sistema de coordenadas) son el núcleo puro; `referencia.py`
importa una capa; `ejecuciones.py` resuelve un corte en PostgreSQL; `consultas.py`, `muestra.py` y
`sintetica.py`, lo demás. Las decisiones de diseño están en [decisiones.md](decisiones.md) (122 a
137).

## La referencia oficial: el Marco Geoestadístico del INEGI

La geografía se resuelve contra una **referencia geográfica pública**, no contra una fuente de
negocio: la cartografía no pasa por la API ni por el almacén de artefactos, no se descarga en
runtime y no vive en el repositorio. Se descarga una vez de su URL oficial y se importa con
`motor-cartera importar-geografia`. La referencia de v0.11 es la capa municipal del Marco
Geoestadístico Integrado 2025:

| Dato | Valor |
|---|---|
| Proveedor | INEGI, Instituto Nacional de Estadística y Geografía |
| Producto | Marco Geoestadístico Integrado, 2025 (ficha del producto "Marco Geoestadístico 2025", UPC 794551163061) |
| Edición | 2025; actualización cartográfica de julio de 2025 (metadato `Modified: 2025-07-31`); archivos fechados el 2025-12-03 |
| Nivel | Municipal: la capa `conjunto_de_datos/00mun`, Áreas geoestadísticas municipales (AGEM), 2,478 municipios |
| Archivo | `mg_2025_integrado.zip`, 257,341,599 bytes |
| URL | https://www.inegi.org.mx/contenidos/productos/prod_serv/contenidos/espanol/bvinegi/productos/geografia/marcogeo/794551163061/mg_2025_integrado.zip |
| Ficha | https://www.inegi.org.mx/app/biblioteca/ficha.html?upc=794551163061 |
| SHA-256 | `f1335bab72d5582adab06e9e3b5d49b7c42da8f8d82e588f484ad6a7c7871d1b` |
| Sistema de origen | EPSG:6372, México ITRF2008 / LCC (GRS80), en metros |
| Codificación del .dbf | ISO-8859-1 (`.cpg` 88591) |
| Licencia | Términos de Libre Uso de la Información del INEGI (https://www.inegi.org.mx/inegi/terminos.html). Cita: "Fuente: INEGI, Marco Geoestadístico Integrado, 2025." |

El manifiesto del repositorio, `src/motor_cartera/geografia/referencias.json`, guarda estos datos
bajo la clave `inegi-mg-2025-municipal`. Con `--referencia`, el importador se niega si el archivo
no tiene exactamente ese SHA-256. La fecha en que se importó cada versión es su `importada_en`.

**Lo que el INEGI advierte, y se respeta:** las áreas del Marco Geoestadístico referencian la
información estadística del INEGI y no deben asumirse como la fuente oficial de la división
político-administrativa del país; el producto declara hasta un metro de tolerancia entre sus
elementos. En la capa de 2025 hay 942 pares de municipios vecinos cuyos polígonos se enciman en
astillas de en total 31.9 m²: un punto que cae en una de ellas está en dos municipios a la vez, y
la geografía lo dice (`MUNICIPIO_AMBIGUO`) en vez de elegir uno.

Para importarla:

```bash
mkdir -p datos/referencias/inegi_mg_2025      # datos/ no se versiona
curl -L -o datos/referencias/inegi_mg_2025/mg_2025_integrado.zip \
  https://www.inegi.org.mx/contenidos/productos/prod_serv/contenidos/espanol/bvinegi/productos/geografia/marcogeo/794551163061/mg_2025_integrado.zip
uv run motor-cartera importar-geografia datos/referencias/inegi_mg_2025/mg_2025_integrado.zip \
  --referencia inegi-mg-2025-municipal
```

El CI no descarga nada del INEGI: usa referencias sintéticas diminutas que las pruebas escriben al
vuelo, y la prueba de humo una cuadrícula inventada (`generar-geografia-sintetica`). La referencia
oficial completa se usa en el benchmark y en la prueba de integración opcional
(`MC_REFERENCIA_INEGI`), que se salta si el archivo no está.

## Cómo se importa una referencia

1. Se calcula el SHA-256 del archivo y, con `--referencia`, se compara con el del manifiesto. Sin
   manifiesto, la importación declara `--proveedor`, `--producto` y `--edicion`.
2. Se lee la capa, de un zip o de un `.shp` con sus compañeros (`.shx`, `.dbf`, `.prj`, `.cpg`). El
   archivo nunca se modifica: se lee.
3. El sistema de coordenadas se reconoce **por los parámetros** de su `.prj`, no por su nombre:
   EPSG:6372 (la Cónica Conforme de Lambert del INEGI: paralelos 17.5 y 29.5, origen 12, meridiano
   central −102, falso este 2,500,000 m, GRS80) o EPSG:4326. Uno que no se reconoce no se adivina:
   la importación se rechaza.
4. La capa tiene que ser de polígonos con `CVEGEO`, `CVE_ENT`, `CVE_MUN` y `NOMGEO`. El nombre
   oficial se guarda tal como viene, con sus acentos: nunca se normaliza destructivamente.
5. La versión se registra `EN_PROCESO` y cada polígono se copia con `COPY`, como WKB, a una tabla
   temporal. PostgreSQL comprueba las claves (`CVEGEO` es `CVE_ENT` y `CVE_MUN`), que no se
   repitan, que ninguna venga sin geometría, transforma a EPSG:4326 con `ST_Transform` y comprueba
   cada geometría con `ST_IsValid`.
6. Una geometría inválida se repara solo si es seguro y explícito: `ST_MakeValid`, solo sus
   polígonos, válida y con la misma área (a menos de 0.01 %). Cada reparación queda registrada en la
   versión. Si alguna no se puede reparar así, la importación se rechaza: nada se repara en
   silencio. La capa de 2025 no tiene ninguna geometría inválida.
7. Los municipios se insertan y la versión queda `PUBLICADA` en la misma transacción: todo o nada.
   Si algo falla, no queda ningún municipio y la versión queda `FALLIDA`, con su motivo.

Una versión `PUBLICADA` o `FALLIDA` ya no cambia (un trigger lo garantiza), y sus municipios
tampoco. El mismo archivo con la misma capa no se importa dos veces. Si el INEGI publica otra
edición, se importa como otra versión, y las ejecuciones que usaron la anterior siguen apuntando a
ella.

| Estado de una versión | Qué quiere decir |
|---|---|
| `EN_PROCESO` | Registrada: se están leyendo, validando y escribiendo sus municipios. |
| `PUBLICADA` | Sus municipios están completos; es la única que resuelve geografía. Ya no cambia. |
| `FALLIDA` | No se importó: no tiene ningún municipio. Ya no cambia. |

## EPSG:4326, longitud y latitud

Todo se guarda en **EPSG:4326** (WGS 84, en grados): los municipios, el punto de cada cuenta y el de
cada base. Es el sistema de las coordenadas que trae la fuente, y de él sale la distancia geodésica
de `territorial/v2` (PostGIS `geography`). La diferencia entre ITRF2008 y WGS 84 es de menos de un
metro, muy por debajo de la precisión de una coordenada observada.

Un punto es `ST_MakePoint(longitud, latitud)`: **X es la longitud y Y la latitud**. Una prueba
construye una cuenta cuyo punto cae en su municipio con ese orden y fuera de la referencia con el
contrario: si alguien invierte el orden, falla.

## La regla de una cuenta

Cuenta por cuenta, sin corregir nada:

1. Sin latitud ni longitud: `SIN_COORDENADAS`. Con una sola: `COORDENADAS_INCOMPLETAS`; la otra no se
   inventa.
2. Con las dos, son `COORDENADAS_INVALIDAS` si alguna no es finita (NaN o infinito), si la latitud
   sale de [−90, 90] o la longitud de [−180, 180], o si son exactamente (0, 0). cartera/v2 ya rechaza
   una coordenada fuera de rango o que no sea un número; `geografia/v1` lo vuelve a revisar, porque
   no depende de que un contrato lo haya hecho. (0, 0) sí pasa el contrato: es un punto del Golfo de
   Guinea, y es lo que deja un sistema que no capturó la coordenada.
3. Con coordenadas utilizables, el punto se busca en los municipios de la versión con `ST_Covers`:
   - en ninguno: `FUERA_DE_REFERENCIA`;
   - en más de uno: `MUNICIPIO_AMBIGUO`, sin elegir ninguno;
   - en exactamente uno, se compara con el municipio de la fuente: si es otro,
     `DISCREPANCIA_MUNICIPIO`; si es el mismo, o la fuente no resolvió ninguno, `VALIDA`.
4. Si las coordenadas no dieron un punto en la referencia (inválidas o fuera), se prueba el punto con
   la latitud y la longitud intercambiadas. Si ese sí cae en un municipio, se agrega el motivo
   `POSIBLE_ORDEN_INVERTIDO` con ese municipio. **Nunca se corrige**: la cuenta se queda en su
   estado y la coordenada como vino.

### Los estados

| Estado | Qué quiere decir | ¿Geografía confiable? |
|---|---|---|
| `VALIDA` | Coordenadas utilizables, en exactamente un municipio de la referencia, que coincide con el de la fuente (o la fuente no resolvió ninguno). | Sí: la única. |
| `SIN_COORDENADAS` | La cuenta no trae latitud ni longitud. | No |
| `COORDENADAS_INCOMPLETAS` | Trae una y no la otra. | No |
| `COORDENADAS_INVALIDAS` | No son finitas, salen de rango o son (0, 0). | No |
| `FUERA_DE_REFERENCIA` | El punto no cae en ningún municipio de la versión. | No |
| `MUNICIPIO_AMBIGUO` | El punto cae en más de un municipio (un límite común, o una astilla donde dos polígonos se enciman). | No |
| `DISCREPANCIA_MUNICIPIO` | El punto cae en un solo municipio, y no es el que resolvió la fuente. | No |

**Política conservadora.** Solo una `VALIDA` es geografía confiable para `territorial/v2`: una
ubicación dudosa no se envía a campo. Una discrepancia puede ser un error de la coordenada o de la
dirección, y la geografía no sabe cuál: lo dice y no decide.

### La comparación con la fuente

`coincide_municipio` guarda la comparación del municipio espacial con el que resolvió la fuente
(`ESTADO_CTE` y `POBLACION_CTE` con el catálogo del INEGI, el del snapshot del corte):

| Comparación | Cuándo |
|---|---|
| `COINCIDE` | El punto cae en un solo municipio, y es el de la fuente. |
| `DISCREPA` | El punto cae en un solo municipio, y no es el de la fuente. |
| `NO_COMPARABLE` | No hay con qué comparar: no hay un municipio espacial único, o la fuente no resolvió ninguno. |

### Los motivos

Cada resultado guarda sus motivos, en orden, como objetos `{"codigo": ...}` con lo que los explica.
Ninguno lleva una coordenada.

| Motivo | Detalle | Cuándo |
|---|---|---|
| `SIN_LATITUD` | — | Falta la latitud. |
| `SIN_LONGITUD` | — | Falta la longitud. |
| `LATITUD_NO_FINITA` | — | La latitud es NaN o infinita. |
| `LONGITUD_NO_FINITA` | — | La longitud es NaN o infinita. |
| `LATITUD_FUERA_DE_RANGO` | — | La latitud sale de [−90, 90]. |
| `LONGITUD_FUERA_DE_RANGO` | — | La longitud sale de [−180, 180]. |
| `PUNTO_0_0` | — | Las dos son exactamente 0. |
| `SIN_MUNICIPIO_EN_REFERENCIA` | — | El punto no cae en ningún municipio. |
| `VARIOS_MUNICIPIOS` | `cvegeo`: los que lo cubren, en orden | El punto cae en más de uno. |
| `MUNICIPIO_UNICO` | `cvegeo` | El punto cae en exactamente uno. |
| `COINCIDE_CON_FUENTE` | `cvegeo` | Ese municipio es el de la fuente. |
| `DISCREPA_DE_FUENTE` | `cvegeo_fuente`, `cvegeo_espacial`, `fuente_en_referencia` | Ese municipio no es el de la fuente; `fuente_en_referencia` dice si la clave de la fuente existe en esa versión. |
| `FUENTE_SIN_MUNICIPIO` | — | La fuente no resolvió un municipio. |
| `POSIBLE_ORDEN_INVERTIDO` | `cvegeo`: donde caería con latitud y longitud intercambiadas | Las coordenadas no dieron un punto en la referencia, y al revés sí. No se corrige. |

### El borde: `ST_Covers`

Un punto cae en un municipio si el municipio lo **cubre**: su interior y su borde. Un punto sobre el
límite común de dos municipios cae en los dos y queda `MUNICIPIO_AMBIGUO`, con los dos CVEGEO; uno
sobre el borde exterior de un municipio, en ese. Con `ST_Contains`, que excluye el borde, ese mismo
punto no caería en ninguno y parecería fuera de la referencia: una prueba lo fija con un punto sobre
un límite y otro sobre un borde exterior.

## Versionado y firma de entrada

Una ejecución declara la versión de la referencia que usa: nunca toma la más nueva. Sus entradas
(el motor, la versión con el SHA-256 de su archivo y su capa, el corte con su firma de contenido y
su dataset conformado con la suya) dan su `firma_inputs`, un SHA-256: las mismas entradas son la
misma geografía (`409 GEOGRAFIA_YA_GENERADA`). La misma cuenta del mismo corte puede tener otra
geografía con otra versión de la referencia, y la de la primera no cambia: lo publicado no se
reescribe (un trigger rechaza cualquier `UPDATE` o `DELETE`).

Borrar solo lo derivado de una ejecución y volver a pedirla con las mismas entradas publica
exactamente lo mismo, fila por fila: una prueba lo hace.

| Resultado de una ejecución | Qué quiere decir |
|---|---|
| `GEOGRAFIA_PUBLICADA` | `EXITOSA`: publicó la geografía de cada cuenta del corte. |
| `YA_GENERADA` | `FALLIDA`: otra ejecución publicó primero la misma firma de entrada. |
| `VERSION_NO_SOPORTADA` | `FALLIDA`: pide un motor que este servicio no aplica. |
| `REFERENCIA_NO_PUBLICADA` | `FALLIDA`: la versión de la referencia no está `PUBLICADA`. |
| `ARTEFACTO_CORRUPTO` | `FALLIDA`: los bytes del Parquet conformado ya no son los de su SHA-256. |
| `DATOS_INCONSISTENTES` | `FALLIDA`: el dataset no es lo que su corte dice, o los conteos no cuadran. |
| `ERROR_INTERNO` | `FALLIDA`: un error inesperado; el detalle no lo expone. |
| `INTENTOS_AGOTADOS` | `FALLIDA`: la cola agotó sus intentos. |
| `CERRADA_AL_BAJAR` | `FALLIDA`: estaba en curso cuando la base se bajó a la 0011. |

## La ejecución: por conjuntos, todo o nada

La piden `POST /geografia/ejecuciones` y `motor-cartera geolocalizar`. `abrir` fija las entradas y
deja la ejecución `EN_PROCESO` con su trabajo `GEOGRAFIA` en la cola durable; un worker la toma con
lease y latido, y la resuelve en una transacción, con su fila bloqueada:

1. comprueba el Parquet conformado del corte contra su SHA-256 (nunca relee el XLSX original);
2. copia de él, por lotes, con el escritor CSV de Arrow y `COPY`, solo cinco columnas: la fila, la
   hoja, `CLIENTE_UNICO`, `LATITUD` y `LONGITUD`; ni nombres, ni domicilios, ni teléfonos;
3. une cada fila con su cuenta canónica y su snapshot del corte, de donde sale el municipio de la
   fuente;
4. clasifica las coordenadas y construye el punto de las utilizables, en una tabla temporal con su
   índice GiST;
5. busca los puntos en los municipios con `ST_Covers`, recorriendo los municipios y entrando por el
   índice de los puntos: PostGIS prepara cada polígono una vez y lo prueba contra todos sus puntos;
6. prueba el punto invertido de las que no dieron un municipio;
7. publica con un `INSERT ... SELECT` el resultado de cada cuenta, lo cuenta, comprueba que cuadre
   con el corte y cierra `EXITOSA`.

Ningún punto en polígono pasa por Python. Si algo falla, el `SAVEPOINT` revierte todo y la ejecución
queda `FALLIDA`: nunca una geografía a medias. Solo publica el dueño vigente de su trabajo: un
worker que perdió su lease no publica, aunque ya haya calculado todo. El núcleo puro
(`reglas.clasificar`) es el espejo del SQL, y las pruebas comparan cada resultado publicado con lo
que la regla en Python concluye del mismo punto.

## Privacidad de las coordenadas

La coordenada observada se guarda (es evidencia de lo que vino en la fuente), pero **no sale**:

- ninguna respuesta de la API la lleva: ni los resultados de una ejecución, ni la geografía de una
  cuenta, ni la Cuenta 360, cuyo `geografia_resumen` dice el estado, el municipio, la comparación y
  la base, sin latitud ni longitud;
- ni la bitácora, ni el detalle de una ejecución, ni sus conteos, ni su distribución, ni los errores
  la mencionan (una prueba lo revisa), y los motivos tampoco;
- el benchmark y la prueba de humo usan coordenadas sintéticas, y sus salidas solo traen conteos.

Las coordenadas de prueba son sintéticas: puntos de la cartografía pública, no ubicaciones de nadie.

## La API

| Método y ruta | Qué hace |
|---|---|
| `GET /geografia/versiones` | Las versiones importadas, la más reciente primero, sin geometrías. |
| `GET /geografia/versiones/{geografia_version_id}` | Una versión: de dónde salió, cómo se importó, cuántos municipios publicó y qué reparó. `404 GEOGRAFIA_VERSION_NO_ENCONTRADA`. |
| `POST /geografia/ejecuciones` | Pide la geografía de un corte (`corte_id`) con una versión (`geografia_version_id`). `201` quiere decir creada, no terminada. `404 CORTE_NO_ENCONTRADO`, `404 GEOGRAFIA_VERSION_NO_ENCONTRADA`, `422 GEOGRAFIA_VERSION_NO_PUBLICADA`, `409 GEOGRAFIA_EN_PROCESO`, `409 GEOGRAFIA_YA_GENERADA`. |
| `GET /geografia/ejecuciones` | Las ejecuciones; con `corte_id`, las de un corte. |
| `GET /geografia/ejecuciones/{geografia_run_id}` | Una ejecución: sus entradas, su firma, su estado, sus conteos y su distribución. `404 GEOGRAFIA_NO_ENCONTRADA`. |
| `GET /geografia/ejecuciones/{geografia_run_id}/resultados` | La geografía de cada cuenta, en el orden de la cuenta. Con `estado_geografia` (las anomalías: `DISCREPANCIA_MUNICIPIO`, `FUERA_DE_REFERENCIA`...) o `municipio` (un CVEGEO), por su índice. Sin coordenadas. |
| `GET /cuentas/{cuenta_id}/geografia` | La geografía de una cuenta en cada corte y cada versión, del corte más reciente al más antiguo. |

La Cuenta 360 (`GET /cuentas/{cuenta_id}`) trae `geografia_resumen`: la geografía de la ejecución
`EXITOSA` más reciente del corte más reciente que tenga una, y su asignación de `territorial/v2`
sobre esa geografía, si existe.

## La línea de comandos

```bash
# Una referencia sintetica (la de la prueba de humo): un rectangulo por municipio, inventado.
uv run motor-cartera generar-geografia-sintetica --destino datos/geografia/municipios.zip
uv run motor-cartera importar-geografia datos/geografia/municipios.zip \
  --proveedor SINTETICO --producto "Referencia sintetica" --edicion humo-v1
# Puntos de esa referencia, dentro de cada municipio y fuera, con PostGIS y una semilla.
uv run motor-cartera muestrear-puntos --geografia-version <uuid> --destino datos/geografia/puntos.json
# Una cartera con coordenadas sinteticas, en proporciones explicitas.
uv run motor-cartera generar-oficial --puntos datos/geografia/puntos.json \
  --proporciones validas=0.9,faltantes=0.04,incompletas=0.01,invalidas=0.01,fuera=0.02,discrepantes=0.02
# La geografia de un corte, en primer plano, por la cola durable.
uv run motor-cartera geolocalizar --corte 2026-09-30 --geografia-version <uuid>
```

`geolocalizar` imprime el estado, la firma, el detalle con los conteos y `geografia_run_id`; ninguna
coordenada. Termina con código 1 si no publicó.

### Coordenadas sintéticas en proporciones explícitas

Sin `--puntos`, el generador deja `LATITUD` y `LONGITUD` vacías, como siempre. Con una muestra, cada
cuenta cae en una categoría por un hash de su cliente y la semilla (y conserva su coordenada en
todos sus cortes): `validas` (un punto de su municipio), `faltantes`, `incompletas` (solo la
latitud), `invalidas` ((0, 0), la única inválida que el contrato deja pasar), `fuera` (un punto
fuera de la referencia) y `discrepantes` (un punto de otro municipio). Las proporciones son
parámetros de una prueba, no un intento de parecer reales, y el manifiesto del escenario dice
cuáles se usaron y de qué muestra (`coordenadas-sinteticas/v1`).

## Índices y volumen

Los índices son los de sus consultas: en `municipio_geografico`, el GiST de su geometría y la
llave única (versión, CVEGEO); en `resultado_geografia_cuenta`, la llave (ejecución, cuenta), que es
la geografía de una cuenta en una ejecución, y (ejecución, estado, cuenta), (ejecución, municipio,
cuenta) y (cuenta, ejecución): una página de las anomalías o de un municipio en el orden de la
cuenta, y la geografía de una cuenta a través de los cortes. El punto de cada cuenta tiene su GiST
en una tabla temporal, solo mientras se resuelve el corte. No hay particionado (decisión 137).

### Benchmark: 500,000 cuentas con la referencia oficial

En la máquina de desarrollo (Windows 11, 12 CPU, PostgreSQL 16.15, PostGIS 3.5.3, GEOS 3.13.1 y
PROJ 8.2.1), sobre la base del XL que ya tenía los 12 cortes de los benchmarks anteriores, con
`scripts/benchmark_geografia.py`, que no reconstruye nada de lo que ya estaba:

- **La referencia oficial** se importó en 19.4 s, con 115 MiB de memoria pico y 63 MiB de WAL:
  2,478 municipios transformados de EPSG:6372, ninguno reparado. La muestra de puntos (49,557 dentro
  de los municipios y 200 fuera), en 2.8 s.
- **El corte**: 500,000 cuentas sintéticas en un corte nuevo, una semana después del último, con
  coordenadas sintéticas en las proporciones de omisión: 449,982 válidas, 19,942 sin coordenadas,
  5,061 incompletas, 5,032 en (0, 0), 9,904 fuera de la referencia y 10,079 en otro municipio.
  Ingerirlo tomó 267 s y materializar su historia, 73 s.
- **La geografía de las 500,000 cuentas tomó 90.6 s** (5,517 cuentas por segundo), con 222 MiB de
  memoria pico y 300 MiB de WAL, y dio exactamente las categorías que se generaron: 449,982
  `VALIDA`, 19,942 `SIN_COORDENADAS`, 5,061 `COORDENADAS_INCOMPLETAS`, 5,032
  `COORDENADAS_INVALIDAS`, 9,904 `FUERA_DE_REFERENCIA`, 10,079 `DISCREPANCIA_MUNICIPIO` y ningún
  `MUNICIPIO_AMBIGUO` (ningún punto de la muestra cayó en una astilla entre dos municipios).

| Fase | Segundos |
|---|---:|
| Verificación del Parquet contra su SHA-256 | 0.1 |
| `COPY` de las cinco columnas | 1.6 |
| El universo: cuenta y snapshot de cada fila | 5.4 |
| La clasificación y los puntos, con su GiST | 3.6 |
| El punto en polígono | 16.6 |
| Los puntos invertidos | 0.2 |
| La publicación (`INSERT ... SELECT`) | 60.3 |
| Los conteos | 2.3 |

Lo que cuesta es escribir: 500,000 resultados con sus cuatro índices y sus llaves foráneas
compuestas (cada resultado contra su snapshot). Cada resultado ocupa 364 bytes con sus índices,
173.5 MiB por ejecución; los 2,478 municipios, 62 MiB, casi todo sus geometrías.

**El punto en polígono recorre los municipios.** Con los 460,061 puntos utilizables del corte, el
plan del motor (cada municipio contra el índice GiST de los puntos, así que PostGIS prepara cada
polígono una vez y lo prueba contra todos sus puntos) tarda 8.8 s en `EXPLAIN (ANALYZE, BUFFERS)`;
la alternativa, cada punto contra el índice GiST de los municipios, 98.7 s: once veces más, porque
prueba cada punto contra un polígono de miles de vértices que no prepara. Una sola coordenada se
resuelve en 0.35 ms por el GiST de los municipios.

**Las consultas no recorren la ejecución.** Por la API, con la serialización (mediana): la geografía
de una cuenta, 19 ms; una página de las anomalías, de 19 a 21 ms; una página de un municipio,
33 ms (cuenta las cuentas del municipio por su índice, 13 a 23 ms); la ejecución, 14 ms. Dentro de
PostgreSQL, cada sentencia de la geografía de una cuenta tarda menos de 0.3 ms, y ninguna consulta
hace un Seq Scan sobre los resultados.

## Lo que `geografia/v1` no hace, a propósito

- **No geocodifica direcciones.** Calle, colonia y código postal no se convierten en coordenadas, ni
  con un servicio externo ni con uno propio: una cuenta sin coordenadas se queda sin ellas.
- **No corrige coordenadas.** Ni intercambia latitud y longitud, ni redondea, ni mueve un punto al
  municipio más cercano, ni usa el centroide del municipio de la fuente como ubicación de la
  cuenta: un centroide no es una ubicación observada.
- **No elige** entre dos municipios que cubren un punto, ni entre la fuente y el espacio cuando
  discrepan.
- **No importa otros niveles** (localidades, AGEB, manzanas) ni otra cartografía que la municipal.
- **No asigna bases, zonas, subzonas, tramos ni rutas:** eso es `territorial/v2`
  ([territorial_v2.md](territorial_v2.md)) y lo que sigue (v0.12 en adelante).
