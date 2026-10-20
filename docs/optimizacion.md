# Optimización Matemática (`optimization/v1`)

Desde v0.16.0, `optimization/v1` arma, para una fecha de operación, **el plan óptimo del modelo de
asignación declarado**: qué cuentas atender y por qué canal (`DIGITAL`, `TELEFONICA` o `CAMPO`) bajo
las capacidades, el presupuesto de unidades de recurso y las coberturas de una configuración
versionada. El valor de oportunidad de cada cuenta es su `recuperacion_positiva_esperada_30d` de
`predictive/model-v1`, en centavos exactos, **sin multiplicarla por ninguna probabilidad**; los
empates se resuelven con la probabilidad de contacto, el menor esfuerzo declarado y un orden
canónico, en cuatro niveles lexicográficos. Un plan se publica **solo si el solver demuestra su
óptimo** en cada nivel.

**Es el óptimo del modelo de asignación declarado, no una estrategia causalmente óptima.** El valor
es la recuperación positiva esperada bajo la política histórica, no el beneficio incremental de
contactar; un canal asignado es una asignación de capacidad compatible con las restricciones, no la
evidencia de que ese canal causará mayor recuperación. Corre **en sombra**: no registra gestiones,
no envía mensajes, no llama, no crea visitas y no cambia `decision/v2`, `campo/v2` ni `ruteo/v2`.

El modelo matemático (los conjuntos, las variables, la jerarquía de objetivos, las restricciones,
el teorema de la reducción de candidatos y la regla de publicación) está en
[modelo_optimizacion.md](modelo_optimizacion.md); la configuración, en
[configuracion_optimizacion.md](configuracion_optimizacion.md); el porqué, en las decisiones 223 a
247 de [decisiones.md](decisiones.md).

## Las versiones y sus hashes

| Definición | Versión | SHA-256 |
| --- | --- | --- |
| Motor | `optimization/v1` | — (cada ejecución guarda su `firma_inputs`) |
| Política | `optimization-public-v1` | `540bc21fadd8297c2a8d427e8af540610642016e7dea5e9be87f997c53d23fc5` |
| Configuración | `optimization-config/v1` | — (cada versión guarda su `hash_contenido`) |
| Algoritmo | `SCIPY_HIGHS_MILP/v1` | — (cada ejecución guarda su `solver_fingerprint`) |
| Benchmark | `optimization-benchmark/public-v1` | — (supuestos sintéticos, en [configuracion_optimizacion.md](configuracion_optimizacion.md#la-configuración-del-benchmark-optimization-benchmarkpublic-v1)) |

`GET /optimizacion/politica` devuelve la definición completa de la política con su hash, la huella
del solver de ese servicio y las limitaciones. Un servicio no planea con una política que no es
exactamente la suya.

## Las entradas, declaradas

Una ejecución declara exactamente sus entradas, por su identificador público. **Nunca "el último"**
scoring, decisión, ruta ni configuración:

| Entrada | Qué tiene que ser |
| --- | --- |
| `scoring_run_id` | Un scoring `EXITOSA` de `predictive/model-v1`, cuyo modelo esté `PUBLICADA`. |
| `decision_run_id` | Una `decision/v2` `EXITOSA` del **mismo corte**: el `as_of` del scoring y el de la decisión son la fecha del corte. |
| `configuracion_id` | Una configuración `PUBLICADA`, cuyo contenido sea el de su hash. |
| `fecha_operacion` | El día que se planea; no anterior al corte. Con un ruteo, **la del ruteo**. |
| `ruteo_run_id` | Opcional, y **obligatorio si la configuración usa `CAMPO` o `BASE`**: un `ruteo/v2` `EXITOSA` cuyo plan de `campo/v2` es de la **misma** `decision/v2`. |

La base lo garantiza además del código: la ejecución apunta al scoring y a la decisión junto con su
corte (llaves foráneas compuestas sobre las restricciones únicas que esas tablas ya tenían), y al
ruteo junto con su plan de campo, y a ese plan junto con su decisión, su territorial, su
configuración de campo y su versión de bases. Una fila que mezcle cortes o cadenas no existe.

Lo que no se puede optimizar no se abre, y no registra nada:

| Código | HTTP | Cuándo |
| --- | --- | --- |
| `SCORING_NO_ENCONTRADO` | 404 | No existe el scoring. |
| `DECISION_NO_ENCONTRADA` | 404 | No existe la `decision/v2`. |
| `CONFIGURACION_NO_ENCONTRADA` | 404 | No existe la configuración. |
| `RUTEO_NO_ENCONTRADO` | 404 | No existe el `ruteo/v2`. |
| `SCORING_NO_EXITOSO` | 409 | El scoring no está `EXITOSA`. |
| `MODELO_NO_PUBLICADO` | 409 | El modelo del scoring no está `PUBLICADA`. |
| `DECISION_NO_EXITOSA` | 409 | La `decision/v2` no está `EXITOSA`. |
| `CONFIGURACION_NO_PUBLICADA` | 409 | La configuración es un `BORRADOR`. |
| `RUTEO_NO_EXITOSO` | 409 | El `ruteo/v2` o su plan de campo no están `EXITOSA`. |
| `OPTIMIZACION_EN_PROCESO` | 409 | Ese mismo plan (la misma firma) ya se está calculando. |
| `OPTIMIZACION_YA_PUBLICADA` | 409 | Ese mismo plan (la misma firma) ya se publicó. |
| `CORTES_DISTINTOS` | 422 | El scoring y la `decision/v2` no son del mismo corte, o su `as_of` no es la fecha del corte. |
| `RUTEO_REQUERIDO` | 422 | La configuración usa `CAMPO` o `BASE` y no se declaró un `ruteo/v2`. |
| `RUTEO_DE_OTRA_DECISION` | 422 | El `ruteo/v2` rutea un plan de campo de otra `decision/v2`. |
| `FECHA_OPERACION_DISTINTA` | 422 | La fecha de operación no es la del `ruteo/v2`. |
| `FECHA_ANTERIOR_AL_CORTE` | 422 | La fecha de operación es anterior al corte. |
| `CONFIGURACION_NO_SOPORTADA` | 422 | La configuración no es de `optimization-config/v1`, o su contenido no es el de su hash. |
| `POLITICA_NO_SOPORTADA` | 422 | Este servicio no optimiza con esa política. |
| `OPTIMIZACION_INVALIDA` | 422 | La clase de todos los anteriores. |
| `CONFIGURACION_INVALIDA` | 422 | Al registrar una configuración: cada problema, en `detalles`. |
| `FILTRO_DESCONOCIDO` | 404 | Un filtro `pool` o `base` que la ejecución no tiene. |
| `OPTIMIZACION_NO_PUBLICADA` | 409 | Se piden los resultados, la capacidad, las restricciones o una cuenta de una ejecución que no publicó un plan. |

## La firma y la huella del solver

La `firma_inputs` es el SHA-256 del motor, la política y su hash, el scoring (su `scoring_run_id` y
su firma), el modelo (su `modelo_version_id` y su `hash_modelo`), la decisión (su `decision_run_id`
y su firma), el ruteo (o ninguno), el hash de la configuración, la fecha de operación y el
`solver_fingerprint`: el SHA-256 de con qué se resuelve (el algoritmo, las versiones de SciPy, HiGHS,
NumPy y Python, el sistema, la arquitectura y los parámetros del solver). Las mismas entradas con la
misma huella son el mismo plan: se publica una sola vez. Un worker cuya huella no es la de la firma
no publica bajo ella (`SOLVER_DISTINTO`).

## El universo y la elegibilidad

El universo son las cuentas del corte: cada predicción del scoring con la decisión de la misma
cuenta y su snapshot (y, con un ruteo, su parada y su base territorial), en un JOIN por conjuntos en
PostgreSQL, en tablas temporales que se descartan. Los pares elegibles (cuenta, pool) salen de otro
JOIN, por igualdad en cada alcance: nunca un producto cartesiano. Python recibe dos `COPY`; ninguna
cuenta se lee como un objeto.

Cada cuenta queda en un estado, con un motivo factual:

| Estado | Qué es |
| --- | --- |
| `ASIGNADA` | El plan óptimo publicado la asigna a un pool. |
| `NO_SELECCIONADA` | Era elegible y el plan óptimo no la asigna. |
| `NO_ELEGIBLE` | Ningún pool activo, admisible y que la alcance; su motivo dice por qué. |

| Motivo | Qué dice |
| --- | --- |
| `EN_SOLUCION_OPTIMA` | `ASIGNADA`: el plan óptimo publicado la asigna a un pool. |
| `FUERA_DE_SOLUCION_OPTIMA` | `NO_SELECCIONADA`: era elegible y el plan no la asigna. No se atribuye a una restricción individual: el óptimo es de todo el plan. |
| `PREDICCION_CON_IMPUTACION` | Su predicción es `CON_IMPUTACION` y la configuración no la admite (el flag es explícito, sin valor por omisión). |
| `SIN_CANAL_ADMISIBLE` | La matriz no permite ningún canal operativo para el canal que le recomendó `decision/v2`. |
| `CONFIG_EXCLUYE_CANAL` | Sus canales admisibles no tienen ningún pool activo. |
| `CAMPO_NO_PROGRAMADA` | `CAMPO` es admisible y tiene pools, pero ella no tiene una parada `PROGRAMADA` en el ruteo, y ningún otro pool la alcanza. |
| `SIN_POOL_APLICABLE` | Sus canales admisibles tienen pools, pero ninguno la alcanza: otra base, sin base territorial, o un gestor sin pool. |

Una cuenta `NO_SELECCIONADA` también dice si entró al modelo (`retenida`) o si la reducción exacta la
dejó fuera porque en su clase había suficientes cuentas mejores (ver
[modelo_optimizacion.md](modelo_optimizacion.md#el-teorema-de-la-reducción-de-candidatos)).

## CAMPO

`CAMPO` solo elige entre las paradas **`PROGRAMADA`** del `ruteo/v2` declarado, cada una **con su
propio gestor**: un pool `GESTOR_CAMPO` por gestor, cuya capacidad efectiva es la menor de la
declarada y sus paradas `PROGRAMADA`. Una cuenta nunca va con otro gestor ni a otra base, una parada
`SIN_TIEMPO`, `NO_ENRUTABLE` o `NO_ENRUTABLE_BASE` nunca se vuelve `CAMPO`, y el plan **no
reoptimiza ninguna ruta**: cada cuenta en `CAMPO` conserva su `orden_ruteo_original`. Quitar paradas
de una ruta factible no la vuelve infactible; agregar o reordenar podría, y no se hace. Antes de
publicar, una consulta por conjuntos comprueba que cada cuenta en `CAMPO` es una parada `PROGRAMADA`
de su mismo gestor con su mismo orden; si no, no se publica nada.

## El modelo y el solver

Las variables son binarias, una por par elegible; la matriz es dispersa (CSR) con coeficientes
enteros, y antes de construirla se comprueba que cada coeficiente y cada suma posible caben en
2⁵³ − 1. La reducción exacta por dominancia retiene, de cada clase de cuentas con la misma firma de
restricciones, solo las que algún plan factible podría usar, sin cambiar el óptimo; nunca un *top N*.
Cada nivel resuelve su relajación lineal con HiGHS (punto interior con *crossover*): si su vértice
óptimo es entero, es el óptimo del entero (cota = objetivo, gap 0, cero nodos); si no,
*branch-and-bound* de HiGHS con `mip_rel_gap = 0`. Entre niveles, la dualidad débil fija las
variables que ningún óptimo del nivel anterior usa o deja de usar. La solución se verifica en
aritmética entera contra todas las filas en cada nivel. El detalle y las pruebas, en
[modelo_optimizacion.md](modelo_optimizacion.md).

## La ejecución: estados y resultados

| Estado | Qué es |
| --- | --- |
| `EN_PROCESO` | Abierta, con su trabajo `OPTIMIZATION` en la cola; un worker la resuelve. |
| `EXITOSA` | Publicó un plan óptimo demostrado, todo o nada. |
| `FALLIDA` | No publicó nada; su resultado y su diagnóstico dicen por qué. |

| Resultado | Qué pasó |
| --- | --- |
| `PLAN_PUBLICADO` | `EXITOSA`: cada nivel `OPTIMAL`, verificado en enteros; resultados, uso y restricciones publicados. |
| `INFACTIBLE` | La configuración no admite ningún plan: por el prechequeo (solver `NO_INVOCADO`) o por el solver (`INFEASIBLE`, con su diagnóstico de relajación). No es un error interno. |
| `OPTIMO_NO_DEMOSTRADO` | El solver agotó su límite (`TIME_LIMIT` o `ITERATION_LIMIT`) sin demostrar el óptimo: lo que alcanzó queda en el certificado y no se publica. |
| `ERROR_NUMERICO` | La solución, en enteros, no cumplía una restricción o no reproducía los objetivos fijados. |
| `ERROR_DEL_SOLVER` | El solver terminó con un estado que no demuestra nada. |
| `COEFICIENTES_FUERA_DE_RANGO` | Algo no cabía exacto en 2⁵³ − 1: no se construyó el modelo. |
| `CONFIGURACION_INCOMPATIBLE` | La configuración nombra un gestor o una base que la cadena del ruteo declarado no tiene. |
| `ENTRADAS_NO_EXITOSAS` | Una entrada declarada dejó de estar `EXITOSA` o `PUBLICADA` antes de resolver. |
| `DATOS_INCONSISTENTES` | El universo no cubre las cuentas del corte, o `CAMPO` no coincide con el ruteo al publicar. |
| `SOLVER_DISTINTO` | El worker resuelve con otra huella que la de la firma. |
| `VERSION_NO_SOPORTADA` | La ejecución es de otro motor u otra política, o su configuración ya no es la de su hash. |
| `YA_OPTIMIZADA` | Otra ejecución publicó la misma firma mientras esta se resolvía. |
| `ERROR_INTERNO` | Un error inesperado; ver la bitácora. |
| `INTENTOS_AGOTADOS` | Su trabajo agotó sus reintentos. |
| `CERRADA_AL_BAJAR` | Estaba en curso cuando la base bajó a la `0016`. |

| Estado del solver | Qué dice |
| --- | --- |
| `OPTIMAL` | Óptimo demostrado: cota = objetivo, gap 0. |
| `INFEASIBLE` | Ningún plan cumple las restricciones. |
| `TIME_LIMIT` | Se agotó el tiempo del nivel. |
| `ITERATION_LIMIT` | Se agotaron las iteraciones. |
| `UNBOUNDED` | Sin cota: no debería ocurrir en un problema binario; no se publica. |
| `INFEASIBLE_OR_UNBOUNDED` | HiGHS no distinguió; no se publica. |
| `NUMERICAL_ERROR` | La solución no se verificó en enteros. |
| `SOLVER_ERROR` | Cualquier otro estado de HiGHS. |
| `NO_INVOCADO` | El prechequeo encontró un error antes de construir el modelo. |

Lo que se encontró antes del solver:

| Hallazgo | Severidad | Qué dice |
| --- | --- | --- |
| `MINIMO_SUPERA_ELEGIBLES` | Error | Un mínimo pide más cuentas que las elegibles de su grupo. |
| `MINIMO_SUPERA_CAPACIDAD` | Error | Un mínimo pide más de lo que la capacidad, el total y el presupuesto que lo alcanzan permiten. |
| `MINIMO_CONTRA_MAXIMO` | Error | Un mínimo y un máximo del mismo grupo no caben juntos. |
| `SUMA_DE_MINIMOS` | Error | Los mínimos de una misma dimensión suman más de lo que cualquier plan atiende. |
| `PRESUPUESTO_INSUFICIENTE` | Error | Los mínimos de una dimensión no caben en el presupuesto de unidades. |
| `GESTOR_DESCONOCIDO` | Error | Un pool de `CAMPO` nombra un gestor que la configuración de campo del ruteo no tiene. |
| `BASE_DESCONOCIDA` | Error | La configuración nombra una base que la versión de bases del ruteo no tiene. |
| `GRUPO_SIN_ELEGIBLES` | Advertencia | Ninguna cuenta elegible es del grupo de una cobertura. |
| `POOL_SIN_CAPACIDAD` | Advertencia | Un pool tiene capacidad efectiva 0 en la ejecución. |
| `POOL_SIN_ELEGIBLES` | Advertencia | Ninguna cuenta elegible alcanza un pool. |
| `CONFIGURACION` | Advertencia | Lo que la configuración permite y no tiene efecto. |

## El certificado

Cada ejecución guarda su certificado: el tamaño del modelo (variables, restricciones, no nulos), la
reducción (las clases, las retenidas, las eliminadas y qué cota fijó cada clase) y, **por nivel**,
su sentido, su estado, su objetivo exacto, la mejor cota, el gap relativo, los nodos, el método
(`RELAJACION_LP_ENTERA`, `BRANCH_AND_BOUND` o `FIJADO_POR_DUALIDAD`), las variables libres, las que su
dualidad fijó y sus segundos. **La principal evidencia de la calidad de un plan no es que sea mejor
que una heurística: es `OPTIMAL` con la mejor cota igual al objetivo y gap 0 en cada nivel.**

## Lo que se publica

Todo o nada: el plan se prepara en una tabla temporal (`COPY`) y se publica con un solo
`INSERT … SELECT` dentro de la transacción que cierra la ejecución, solo si su trabajo sigue siendo
del worker (un worker que perdió su lease no publica). Ninguna escritura tiene una transacción
abierta mientras el solver corre.

| Tabla | Qué guarda |
| --- | --- |
| `resultado_optimizacion_cuenta` | Una fila por cuenta del universo: su estado, su motivo, sus canales, su pool, su gestor y su orden en la ruta si es `CAMPO`, su prioridad, su producto, su tramo, su base, su valor de oportunidad en centavos, su `p_contacto_14d` exacta, sus unidades y si fue retenida. |
| `uso_pool_optimizacion` | Cada pool: su capacidad declarada y efectiva, sus elegibles, sus retenidas, lo utilizado, la holgura y las unidades consumidas. |
| `resultado_restriccion_optimizacion` | Cada restricción (la de cada pool, el total, el presupuesto y cada cobertura): su lado izquierdo, su lado derecho, su holgura en enteros, si quedó *binding* (holgura cero) y, en una participación, su límite resuelto a cuentas. |

El `hash_plan` es el SHA-256 de las líneas canónicas del plan: los cuatro objetivos, una línea por
cuenta (en orden canónico, con su estado, su motivo, su pool y sus valores), una por pool y una por
restricción. Sin instantes ni identificadores de ejecución: las mismas entradas dan el mismo hash,
aunque las tablas que se leen cambien de orden físico o las predicciones se inserten en otro orden.
Lo publicado no se reescribe: la base rechaza cualquier `UPDATE` o `DELETE` de esas tablas, y una
ejecución terminada no cambia.

Los puntos donde las pruebas inyectan una falla, y en cada uno no se publica nada: `ANTES_UNIVERSO`,
`DESPUES_ELEGIBILIDAD`, `DESPUES_REDUCCION`, `DESPUES_MODELO`, `DESPUES_SOLVER_NIVEL_1`,
`DESPUES_SOLVER_NIVEL_2`, `DESPUES_SOLVER_NIVEL_3`, `DESPUES_SOLVER_NIVEL_4`, `DESPUES_STAGING`,
`ANTES_PUBLICAR` y `ANTES_CERRAR`.

## La API

| Ruta | Qué hace | Códigos |
| --- | --- | --- |
| `GET /optimizacion/politica` | La política con su hash, la huella del solver y las limitaciones | 200, 401 |
| `POST /optimizacion/configuraciones` | Registra una configuración (`?publicar=true` para publicarla) o reutiliza la del mismo contenido | 200, 201, 401, 422 |
| `POST /optimizacion/configuraciones/{configuracion_id}/publicacion` | Publica un `BORRADOR` | 200, 401, 404, 422 |
| `GET /optimizacion/configuraciones` y `/{configuracion_id}` | Las configuraciones (filtro `estado`) y una con su matriz, sus pools, sus restricciones y `hash_verificado` | 200, 401, 404, 422 |
| `POST /optimizacion/ejecuciones` | Pide un plan con sus entradas exactas; lo resuelve el worker | 201, 401, 404, 409, 422 |
| `GET /optimizacion/ejecuciones` y `/{optimizacion_run_id}` | Las ejecuciones (filtros `estado`, `configuracion_id`, `scoring_run_id`) y una con sus entradas, sus hashes, su certificado y su resumen | 200, 401, 404, 422 |
| `GET /optimizacion/ejecuciones/{optimizacion_run_id}/resultados` | Los resultados por cuenta, filtrados en la base por una lista blanca (`estado`, `canal`, `pool`, `producto`, `prioridad`, `base`), en el orden de la cuenta, paginados | 200, 401, 404, 409, 422 |
| `GET /optimizacion/ejecuciones/{optimizacion_run_id}/capacidad` | El uso de cada pool (filtro `canal`) y el agregado por canal | 200, 401, 404, 409, 422 |
| `GET /optimizacion/ejecuciones/{optimizacion_run_id}/restricciones` | Cada restricción con su lado izquierdo, derecho, holgura y si quedó activa (filtros `binding` y `tipo`) | 200, 401, 404, 409, 422 |
| `GET /optimizacion/ejecuciones/{optimizacion_run_id}/diagnostico` | Por qué terminó como terminó: el prechequeo, el diagnóstico de relajación, el certificado; sin inventar un plan | 200, 401, 404, 409, 422 |
| `GET /optimizacion/ejecuciones/{optimizacion_run_id}/cuentas/{cuenta_id}` | Por qué una cuenta quedó como quedó: su resultado, sus canales admisibles, los pools que la alcanzaban, los hechos de su elegibilidad y sus grupos | 200, 401, 404, 409, 422 |
| `GET /cuentas/{cuenta_id}/optimizacion` | Lo que cada plan publicado concluyó de una cuenta, del más reciente al más antiguo | 200, 401, 404, 422 |

**201 quiere decir que la ejecución se creó, no que terminó.** El MILP nunca corre dentro de una
petición HTTP: el `POST` comprueba lo declarado, registra la ejecución `EN_PROCESO` y su trabajo en la
misma transacción, y responde. Ningún otro filtro se acepta, y ninguna ruta ejecuta una acción.

## El CLI

| Comando | Qué hace |
| --- | --- |
| `crear-config-optimizacion ARCHIVO [--publicar]` | Registra una configuración desde su JSON, o reutiliza la del mismo contenido. |
| `optimizar-cartera --scoring --decision --configuracion --fecha-operacion [--ruteo]` | Planea en primer plano, con su propio trabajo de la cola y todas sus entradas declaradas. La fecha no se infiere. |
| `resumen-optimizacion --ejecucion` | Sus entradas, sus hashes, la huella, el certificado de cada nivel, los objetivos, el uso por canal, las restricciones activas, la reducción y los tiempos. Agregado: ninguna cuenta. |
| `exportar-optimizacion --ejecucion [--formato csv\|parquet\|jsonl] [--contenido asignaciones\|pools\|restricciones]` | Las asignaciones (una fila por cuenta, por `COPY`) o el plan agregado, sin un dato personal; decimales exactos, nunca float. |
| `diagnosticar-optimizacion --ejecucion` | El prechequeo, lo que demostró cada nivel y el diagnóstico de relajación. |
| `comparar-optimizaciones --a --b` | Dos planes publicados, por conjuntos: qué entradas difieren, sus objetivos y cuántas cuentas conservan su asignación o cambian de pool o de canal. |

No hay un comando que ejecute un plan, envíe, llame, visite o aplique nada: está fuera de alcance.

## La orquestación

Cada ejecución es un trabajo durable `OPTIMIZATION` de la cola, en el nivel 10 (después de
`PREDICTIVE_SCORING`: nunca retrasa una etapa operacional ni un scoring), con su lease, su latido,
sus reintentos y `SKIP LOCKED`. Varios workers pueden correr a la vez: el candado de cada ejecución
hace que el segundo espere al primero y la encuentre terminada, y solo publica el dueño vigente de
su trabajo. Bajar la base a la `0016` cierra lo que estaba en curso (`CERRADA_AL_BAJAR`), quita sus
trabajos y las tablas de v0.16, y no toca nada de v0.15.

## La privacidad

- Un resultado sale con el `cuenta_id` público de su cuenta y con códigos (de pool, de base, de
  gestor). Ninguna respuesta, exportación ni tabla de v0.16 lleva un nombre, un domicilio, un
  teléfono, un `CLIENTE_UNICO`, un `actor_ref`, una coordenada ni una geometría.
- Ninguna dimensión de una restricción es un dato personal, un identificador o un atributo sensible.
- La bitácora solo lleva la ejecución, sus conteos, el tamaño del modelo, el estado del solver, los
  tiempos y los objetivos agregados: nunca una cuenta, una predicción ni una lista de clientes.

## Lo medido

En una sola máquina (Windows 11, AMD de 64 bits, PostgreSQL 16.15, SciPy 1.18.1 con HiGHS 1.12.0),
con `scripts/benchmark_optimizacion.py` y la configuración `optimization-benchmark/public-v1`:
**supuestos sintéticos, que existen únicamente para medir el optimizador**, fijados en el código
antes de medir y registrados (a las 11:07:57 UTC) antes del primer plan (11:08:11 UTC). M es el
corte del escenario predictivo de v0.15 con una `decision/v2` `BACKTEST` del mismo corte y el
`known_at` de su scoring; XL es el corte de 500,000 cuentas de `CARTERA_PRINCIPAL` con su cadena
compatible (`decision/v2` → `campo/v2` → `ruteo/v2` sobre la red vial de OpenStreetMap de Puebla:
41,896 paradas `PROGRAMADA` de 2,683 gestores), declarada; la otra ruta de ese plan usa un modelo
geométrico sintético y no se usó.

| | S: exactitud | M: restricciones ricas | XL: escala con CAMPO |
| --- | ---: | ---: | ---: |
| Cuentas del universo | 10,000 (las primeras de M) | 113,876 | 500,000 |
| Elegibles; no elegibles | 9,249; 751 | 105,005; 8,871 (`CON_IMPUTACION`) | 500,000; 0 (imputación admitida, declarada) |
| Clases de firma | 15 | 15 | 7,235 |
| Retenidas (eliminado) | 4,157 (55.1 %) | 46,876 (55.4 %) | 279,787 (44.0 %) |
| Variables binarias | 5,408 (sin reducir, 10,766) | 60,790 | 408,204 |
| Restricciones; no nulos | 1,259; 31,371 | 13,922; 352,334 | 131,108; 2,492,352 |
| Cada nivel | `OPTIMAL`, cota = objetivo, gap 0, 0 nodos | igual | igual |
| Valor (centavos) | 606,433,804 | 6,965,307,459 | 31,047,640,329 |
| Contacto (1e-8) | 23,368,095,073 | 266,112,163,609 | 2,108,715,776,761 |
| Unidades de recurso | 1,700 | 19,357 | 285,000 |
| Asignadas | — | 9,109 | 50,000: 25,000 `DIGITAL`, 15,000 `TELEFONICA` y 10,000 `CAMPO` (de 19,751 efectivas) |
| Restricciones *binding* | — | 3 de 8 (`MIN_MUY_ALTA`) | 198 de 2,691 (`TOTAL_CUENTAS`) |
| Universo y pares | — | 1.53 s | 8.64 s |
| Solver (niveles 1, 2, 3 y 4) | 0.16 s | 1.77 s (1.25, 0.03, 0.03, 0.05) | 31.25 s (23.59, 2.17, 0.45, 1.20) |
| Plan, staging, publicación | — | 0.57, 0.24 y 3.73 s | 5.75, 1.31 y 39.48 s |
| Total | 0.25 s sin reducir | 8.2 s | 87.6 s |
| Memoria pico del proceso | — | 400 MB | 1,262 MB |
| WAL; crecimiento de la base | — | 87.5 MiB; 33.9 MiB | 338.8 MiB; 174.8 MiB |

- **Exactitud.** En S, el modelo completo y el reducido dan los mismos cuatro óptimos
  (606,433,804; 23,368,095,073; 1,700; 13,469,415). En 200 instancias pequeñas al azar, el MILP
  coincide con la enumeración independiente de las pruebas en las 169 que tienen óptimo, y las 31
  restantes son infactibles en las dos.
- **El certificado.** En M y en XL, cada nivel terminó `OPTIMAL` con un vértice entero de su
  relajación (`RELAJACION_LP_ENTERA`): la mejor cota es el objetivo, gap 0, cero nodos. La
  fijación por dualidad dejó libres, después del nivel 1, 2,408 de 60,790 variables en M y 27,128 de
  408,204 en XL.
- **Reconstruible.** Las dos corridas del benchmark (la primera se invalidó, ver abajo) publicaron
  los mismos hashes de plan para M (`35d16f79…`) y XL (`b25181c6…`), con otras ejecuciones.

La sensibilidad de la capacidad, sobre M (solo las capacidades de `DIGITAL` y `TELEFONICA`, por
8/10 y 12/10; nada más cambia):

| Escenario | Valor de oportunidad predicho (centavos) | Asignadas | Unidades | Asignadas en los dos | Otro canal |
| --- | ---: | ---: | ---: | ---: | ---: |
| −20 % | 6,319,842,722 | 7,286 | 15,482 | 7,286 | 335 |
| nominal | 6,965,307,459 | 9,109 | 19,357 | — | — |
| +20 % | 7,467,315,572 | 10,930 | 23,227 | 9,109 | 64 |

Es la **frontera de oportunidad del modelo**: al permitir 1,821 cuentas más, el óptimo captura
502,008,113 centavos más de valor de oportunidad predicho (+7.2 %), y con 1,823 menos, 645,464,737
menos (−9.3 %): cada cuenta adicional captura menos que la anterior. No es recuperación causal, ni
dinero adicional que una operación vaya a cobrar.

Por la API, sobre el plan XL (841,628 resultados en la base, 346 bytes cada uno con sus índices),
con 20 repeticiones: la ejecución, 14 ms de mediana; una página de resultados, 19 ms; la página 100,
22 ms; por estado, canal, pool, producto, prioridad o base, de 22 a 65 ms; producto y prioridad
juntos, 44 ms; la capacidad, 20 ms; las restricciones, 17 ms; el diagnóstico, 14 ms; la explicación
de una cuenta, 50 ms; los planes de una cuenta, 11 ms. Ningún Seq Scan sobre una tabla grande. El
universo XL es un JOIN por conjuntos, con *hash joins* en paralelo, de 8.0 s en PostgreSQL; los
pares, por igualdad, 0.7 s. La API se midió otra vez después de la revisión previa al merge, que
arma una ejecución con una sola consulta de lo que declaró (de 9 sentencias a 2; la explicación de
una cuenta, de 17 a 10); lo demás del benchmark no cambió.

**El benchmark encontró un defecto, y se corrigió antes del PR.** En la primera corrida, el total de
los filtros `producto` y `prioridad` (y el de `base`) recorría toda la tabla de resultados (87 ms en
PostgreSQL, 116 ms de mediana): esos filtros de la lista blanca no tenían índice. Se agregaron
`ix_resultado_optimizacion_producto`, `ix_resultado_optimizacion_prioridad` e
`ix_resultado_optimizacion_base` (parcial), se invalidaron las mediciones y el benchmark se repitió
entero desde una base sin planes: los números de arriba son los de la segunda corrida.

No hay particionado. Cada consulta de la API entra por un índice que empieza por la ejecución o por
la cuenta; las tablas crecen con las cuentas y los planes, y la retención de planes viejos es una
política de la operación.

## Limitaciones

Aunque un plan sea un óptimo matemático demostrado:

- **Es óptimo respecto al modelo declarado. No es una estrategia causalmente óptima.**
- La función objetivo usa la **recuperación positiva esperada bajo la política histórica**, no el
  beneficio incremental de contactar.
- **Un canal asignado es una asignación de recurso compatible con las restricciones, no la evidencia
  de que ese canal causará mayor recuperación.**
- Las capacidades de un benchmark son sintéticas, y **las unidades de recurso no son costos
  reales**: no hay un modelo de costos monetarios.
- **Un óptimo matemático de supuestos incorrectos sigue siendo una mala decisión de negocio.**
- Al permitir más capacidad, el óptimo captura más valor de oportunidad predicho; eso no dice cuánto
  dinero adicional se recuperaría.
- `CAMPO` no reoptimiza rutas, no mueve cuentas entre gestores ni entre bases y no planea dentro del
  día: elige entre las paradas que `ruteo/v2` ya programó.
- No hay *uplift*, ML causal, efecto incremental de un tratamiento, Decision Engine v3, aprendizaje
  por refuerzo, *bandits*, optimización en línea, ejecución automática ni VRP global; el plan se ve
  en la app, que es de solo lectura.
