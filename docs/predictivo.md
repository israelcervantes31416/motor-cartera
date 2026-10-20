# Predictive Intelligence (`predictive/model-v1`)

Desde v0.15.0, `predictive/model-v1` estima, para cada cuenta de un corte canónico, la probabilidad
de **resultados observados bajo la operación histórica** en los días siguientes al corte:

| Salida | Qué es |
| --- | --- |
| `p_recuperacion_positiva_30d` | La probabilidad de que la recuperación neta de la cuenta en (T, T+30] sea mayor que cero. |
| `p_contacto_14d` | La probabilidad de que la cuenta tenga una gestión efectiva con contacto en (T, T+14]. |
| `p_promesa_30d` | La probabilidad de que se cree una promesa de pago de la cuenta en (T, T+30]. |
| `recuperacion_positiva_esperada_30d` | La recuperación **positiva** esperada en (T, T+30], en pesos: la esperanza de max(neto, 0). No es la recuperación neta esperada. |
| `riesgo_sin_recuperacion_30d` | 1 − `p_recuperacion_positiva_30d`, exacto. No es un modelo propio. |

**Una predicción no es causal.** Dice qué tan probable era un resultado dada la forma en que se
operó la cartera en el pasado, con lo que se conocía en cada momento. No mide el efecto de ninguna
acción, no dice qué acción ejecutar y no cambia `decision/v2`, `campo/v2` ni `ruteo/v2`: corre en
paralelo, en sombra, y nada de lo operacional lee sus predicciones.

Este documento cubre los relojes, los labels y su censura, el manifiesto de un entrenamiento, el
entrenamiento, el registro de versiones, el scoring, la explicación, el drift, la API, el CLI, la
orquestación y la privacidad. Las features están en [features_predictivas.md](features_predictivas.md);
los modelos, sus métricas, sus compuertas y la ficha del modelo, en
[modelos_predictivos.md](modelos_predictivos.md); el split temporal, el walk-forward y el benchmark,
en [backtesting_predictivo.md](backtesting_predictivo.md). El porqué, en las decisiones 197 a 222 de
[decisiones.md](decisiones.md).

## Las versiones y sus hashes

Cada definición es código versionado con el SHA-256 de su forma canónica; cada entrenamiento y
cada versión del modelo guardan las cuatro. Cambiar una regla cambia su hash, y un servicio no
entrena ni puntúa con una versión que no es exactamente la suya.

| Definición | Versión | SHA-256 |
| --- | --- | --- |
| Modelo | `predictive/model-v1` | — (cada versión guarda el `hash_modelo` de sus artefactos) |
| Artefacto | `predictive/artifact-v1` | — (cada componente guarda su `hash_artefacto`) |
| Targets | `predictive-targets/public-v1` | `bc76c4c5b0d60f2f3b628d9f2c8d0b54bdb670c5bc4aace236276ba884cde5b2` |
| Features | `predictive-features/public-v1` | `786b5ef4b5a21a7583f59082196f77bc4900636ed1089e749fe3a726f9db5e94` |
| Política de entrenamiento | `predictive-training/public-v1` | `72630f625de5bc53b777da286395474b857d641b810e792c8cb595159973bfe1` |

`GET /predictivo/features` y `GET /predictivo/targets` devuelven las definiciones completas con su
hash.

## Los tres relojes

Cada fila del dataset (una cuenta en un ancla) y cada scoring se fijan con tres relojes:

- **`feature_as_of` (T)**: el día de negocio de la predicción, la fecha del corte, en la zona
  declarada (`America/Mexico_City` por omisión). La predicción se toma al cierre de T.
- **`feature_known_at` (K)**: hasta qué instante se conocía lo que usan las features. Una feature
  usa hechos **ocurridos hasta el final de T y registrados hasta K**; un hecho registrado después
  de K no existe para ella, aunque haya ocurrido antes de T (un evento tardío). En un scoring es su
  `known_at`.
- **`label_known_at` (L)**: hasta qué instante se observa para construir el label. Puede ser
  posterior a la predicción: los labels son resultados futuros a T. Nunca es anterior a K.

`K <= L <= el reloj de la base`, siempre con zona horaria. Un corte que se materializó después de K
no existe para ese ancla, y una interpretación de pagos o una evaluación de promesas que terminó
después de K tampoco.

## La ventana de un target

La ventana de un target de horizonte H es **(T, T+H]**: los días de calendario T+1 a T+H, enteros,
en la zona declarada. Inicio exclusivo, fin inclusivo, igual en el entrenamiento, el backtest, el
scoring, la documentación y las pruebas. Lo ocurrido el día T, a cualquier hora, es pasado: puede
ser una feature y nunca un label. En SQL son los instantes [inicio de T+1, inicio de T+H+1).

| Target | H | Fuente | Regla |
| --- | --- | --- | --- |
| `RECUPERACION_POSITIVA_30D` | 30 | pagos | 1 si la recuperación neta en la ventana es mayor que cero; 0 si es cero o negativa. |
| `CONTACTO_14D` | 14 | lifecycle | 1 si hay al menos una gestión efectiva con `CONTACTO_TITULAR` o `CONTACTO_TERCERO` ocurrida en la ventana (la cuenta contactada de `analytics/v1`). |
| `PROMESA_30D` | 30 | lifecycle | 1 si se crea al menos una `PromesaPago` de la cuenta en la ventana: su `PROMESA_CREADA`, de una gestión efectiva. Mide la creación, no el cumplimiento. |
| `RECUPERACION_POSITIVA_MONTO_30D` | 30 | pagos | max(recuperación neta en la ventana, 0), en pesos. Nunca negativo. |

**La recuperación neta** es la de `motor-pagos/v1` y `analytics/v1`: la suma de los `PAGO` no
anulados más la de los `POSIBLE_REVERSO`, de los `MovimientoEconomicoCanonico` conciliados con la
cuenta y recibidos en la ventana, de las interpretaciones **declaradas en el manifiesto**. Un
`PAGO` está anulado si el `REVERSO` que lo anula se recibió **antes del final de la ventana**; un
reverso que llega después no existe para ese label (el label sigue siendo positivo). El `REVERSO` de
un pago anterior a la ventana no entra a su neto: el pago no está en la ventana y el reverso no es un
pago. Tener un pago no es tener recuperación positiva: un pago y su reverso dentro de la ventana
dan un neto cero.

**Una gestión es efectiva** si se registró hasta L y no tiene una anulación ocurrida antes del final
de la ventana y registrada hasta L. Una gestión anulada después de la ventana sí contó. Una
promesa cancelada después de crearse sigue habiéndose creado.

## La madurez y la censura

Un label solo existe si su horizonte está **maduro**: la cobertura **declarada** de su fuente llega
al final de su ventana.

| Madurez | Cuándo |
| --- | --- |
| `MADURA` | La cobertura declarada de su fuente es al menos T+H: el label existe. |
| `CENSURADA` | La cobertura no llega a T+H: la ventana todavía no termina en lo que se sabe. Sin label. |
| `NO_DISPONIBLE` | La cobertura de su fuente no se declaró: no se sabe si la ausencia de hechos es un cero. |

**Un label censurado nunca se vuelve un cero.** El dataset lo deja NULL, no entra al entrenamiento
de ese target y se cuenta como censurado. Las marcas de cobertura son las del manifiesto:

- `pagos_completos_hasta`: hasta qué día los pagos estaban completos a L;
- `lifecycle_completo_hasta`: hasta qué día el lifecycle estaba completo a L.

Se **declaran**: nunca son la fecha máxima observada en una tabla (un día sin pagos no prueba que
los pagos de ese día ya llegaron). Ninguna puede ser posterior al día de L. Una recuperación madura
con `pagos_completos_hasta >= T + 30`; un contacto, con `lifecycle_completo_hasta >= T + 14`; una
promesa, con `lifecycle_completo_hasta >= T + 30`.

## El manifiesto de un entrenamiento

Un entrenamiento lee un manifiesto explícito. **Nada se elige al ejecutar**: ni «los últimos N
cortes», ni la última interpretación, ni el split.

```json
{
  "zona_horaria": "America/Mexico_City",
  "version_politica": "predictive-training/public-v1",
  "anclas": [
    {
      "corte_id": "…",
      "feature_as_of": "2026-04-08",
      "feature_known_at": "2026-04-09T02:00:00-06:00",
      "label_known_at": "2026-10-15T12:00:00-06:00",
      "pagos_completos_hasta": "2026-09-30",
      "lifecycle_completo_hasta": "2026-09-30",
      "split": "TRAIN"
    }
  ],
  "interpretaciones_pagos": ["<motor_pagos_run_id de cada mes>"]
}
```

Cada ancla es un corte canónico con su `feature_as_of` (la fecha del corte: un snapshot intermedio
no existe), sus dos fronteras de conocimiento, su cobertura declarada y su split (`TRAIN`,
`VALIDATION`, `TEST` o `PURGE`). Las interpretaciones de pagos, una por mes, son las que definen
los labels de pagos. Antes de registrar nada, el manifiesto se comprueba, fail-closed, con un código
estable:

| Código | HTTP | Qué se rechaza |
| --- | --- | --- |
| `MANIFIESTO_INVALIDO` | 422 | El manifiesto no tiene la forma esperada (un campo falta o no es una fecha o un instante). |
| `POLITICA_NO_SOPORTADA` | 422 | Este servicio no entrena con esa versión de la política. |
| `ZONA_HORARIA_INVALIDA` | 422 | La zona no es una zona IANA que conozca la base. |
| `ANCLAS_INSUFICIENTES` | 422 | Menos de tres anclas: hacen falta TRAIN, VALIDATION y TEST. |
| `ANCLA_REPETIDA` | 422 | Un corte está declarado dos veces. |
| `CORTE_NO_ENCONTRADO` | 404 | No existe un corte declarado. |
| `CARTERA_INCONSISTENTE` | 422 | Las anclas son de más de una cartera: un modelo, una cartera. |
| `AS_OF_DISTINTO_DEL_CORTE` | 422 | `feature_as_of` no es la fecha del corte. |
| `KNOWN_AT_SIN_ZONA` | 422 | Un instante no lleva su zona horaria. |
| `KNOWN_AT_FUTURO` | 422 | `label_known_at` es posterior al reloj de la base. |
| `RELOJES_INVERTIDOS` | 422 | `feature_known_at` es posterior a `label_known_at`. |
| `CORTE_NO_CONOCIDO` | 422 | El corte se materializó después de `feature_known_at`. |
| `COBERTURA_NO_CONOCIDA` | 422 | Una cobertura declarada es posterior al día de `label_known_at`. |
| `SPLIT_INVALIDO` | 422 | Un split no es TRAIN, VALIDATION, TEST ni PURGE, o falta uno de los tres primeros. |
| `SPLIT_DESORDENADO` | 422 | TRAIN, VALIDATION y TEST no van en ese orden en el tiempo. |
| `PURGA_INSUFICIENTE` | 422 | La ventana del label más largo de un bloque llega a la primera ancla del siguiente. |
| `SPLIT_SIN_MADURAS` | 422 | Un bloque no tiene ningún ancla con los labels de un target maduros. |
| `INTERPRETACION_NO_ENCONTRADA` | 404 | No existe una interpretación declarada. |
| `INTERPRETACION_NO_EXITOSA` | 422 | Una interpretación declarada no terminó `EXITOSA`. |
| `INTERPRETACION_INCONSISTENTE` | 422 | Una interpretación es de otra versión del motor o de otra cartera. |
| `VENTANA_DUPLICADA` | 422 | Un mes tiene dos interpretaciones declaradas: un movimiento contaría dos veces. |
| `INTERPRETACION_FALTANTE` | 422 | Un mes que toca una ventana madura de pagos no tiene su interpretación. |
| `INTERPRETACION_NO_CONOCIDA` | 422 | Una interpretación terminó después del `label_known_at` de un ancla que la usa. |
| `INTERPRETACION_NO_VIGENTE` | 422 | Una interpretación no es la vigente de su mes a `label_known_at` (otra posterior ya había terminado). |
| `INTERPRETACION_INCOMPLETA` | 422 | Una interpretación no clasificó todos los pagos observados conocidos de su mes. |

Las anclas se ordenan en forma canónica y el SHA-256 de su forma canónica es el `hash_manifiesto`:
el mismo conjunto lógico, declarado en cualquier orden, da el mismo hash. La `firma_inputs` es el
SHA-256 de las versiones con sus hashes, la cartera, la zona y el manifiesto: el mismo manifiesto con
las mismas versiones es el mismo entrenamiento, y la base admite a lo más uno `EXITOSA` y uno
`EN_PROCESO` por firma.

`motor-cartera construir-manifiesto-predictivo` **propone** un manifiesto, sin entrenar nada: con
`--escenario`, las anclas son los cortes de un escenario cargado con la frontera de conocimiento de
cada uno (`carga.json`) y su cobertura; sin él, los cortes de la cartera entre `--desde` y `--hasta`,
con su creación como frontera y la cobertura declarada en `--pagos-completos-hasta` y
`--lifecycle-completo-hasta`. La regla es determinista: TEST son las últimas `--test` anclas con sus
cuatro labels maduros; VALIDATION, las últimas `--validation` anteriores cuya ventana de label
termina antes de TEST; TRAIN, todas las anteriores cuya ventana termina antes de VALIDATION; las
demás, PURGE. Las interpretaciones son las vigentes a L de cada mes que toca una ventana madura. Una
persona revisa la propuesta y la entrena con `entrenar-predictivo` o con
`POST /predictivo/entrenamientos`.

## El entrenamiento

`entrenar` toma el candado de la ejecución (un *advisory lock* de PostgreSQL en una conexión
propia: un segundo worker con la misma ejecución espera a que el primero termine y la encuentra
terminada) y, **sin una transacción abierta mientras ajusta**:

1. construye el dataset ancla por ancla, cada una en su propia transacción corta de lectura: las
   features a K y los labels a L, con el mismo SQL de un scoring, y su `dataset_hash`;
2. aprende el preprocesamiento de TRAIN (y solo de TRAIN);
3. ajusta cada GLM de la rejilla de la política con TRAIN y elige con VALIDATION;
4. decide la calibración de Platt con VALIDATION;
5. construye el artefacto JSON de cada componente y evalúa TEST **una sola vez**, con el runtime del
   artefacto y las probabilidades cuantizadas que se publicarían, contra el baseline de TRAIN, con
   intervalos bootstrap por cuenta y las compuertas de la política; repite el procedimiento en los
   folds del walk-forward y mide la referencia de TRAIN para el drift;
6. publica en una sola transacción corta: la versión (`CANDIDATA`, `VALIDADA` y `PUBLICADA`, o
   `RECHAZADA`) con sus cuatro componentes, y la ejecución `EXITOSA`, si su trabajo sigue siendo suyo.

Si algo falla antes de publicar, la ejecución queda `FALLIDA` y no queda ninguna versión; un error
transitorio de la base la deja `EN_PROCESO` y la cola la reintenta. Los puntos de falla, donde las
pruebas inyectan un fallo para comprobar que no se publica nada: `ANTES_DATASET`,
`DESPUES_FEATURES`, `DESPUES_LABELS`, `DESPUES_SPLIT`, `DESPUES_ENTRENAMIENTO`,
`DESPUES_CALIBRACION`, `DESPUES_EVALUACION` y `ANTES_PUBLICAR_MODELO`.

| Resultado | Estado | Qué dejó |
| --- | --- | --- |
| `MODELO_PUBLICADO` | `EXITOSA` | Un dataset y una versión `PUBLICADA`: los cuatro componentes pasaron sus compuertas en TEST. |
| `MODELO_RECHAZADO` | `EXITOSA` | Un dataset y una versión `RECHAZADA`, con sus métricas: algún componente no pasó sus compuertas. Nada se publicó. |
| `YA_ENTRENADO` | `FALLIDA` | Nada: otra ejecución publicó primero la misma firma. |
| `VERSION_NO_SOPORTADA` | `FALLIDA` | Nada: la ejecución pide versiones o hashes que este servicio no entrena. |
| `SIN_EJEMPLOS` | `FALLIDA` | Nada: un bloque no tiene ejemplos maduros de un target, o TRAIN no tiene sus dos clases. |
| `DATOS_INCONSISTENTES` | `FALLIDA` | Nada: el dataset contradice su manifiesto (un label maduro sin valor, una cuenta repetida). |
| `ERROR_INTERNO` | `FALLIDA` | Nada: un error inesperado; su traza (sin datos) está en la bitácora. |
| `INTENTOS_AGOTADOS` | `FALLIDA` | Nada: su trabajo agotó los intentos de la cola. |
| `CERRADA_AL_BAJAR` | `FALLIDA` | Nada: estaba en curso cuando la base bajó a la `0015`. |

La ejecución guarda el manifiesto canónico, el `dataset_hash`, cuántas filas cuenta-ancla tiene
cada split, cuántas tienen algún label censurado, los conteos por target y split (maduras,
censuradas, no disponibles y prevalencia o media), el backtest, los tiempos de cada fase (los
del dataset, además, por paso: las features, los labels, el COPY y la conversión), el tamaño de la
matriz de diseño (sus columnas, las filas de TRAIN y el bloque con que se recorre) y la memoria pico
del proceso. Un scoring guarda los suyos: las features, la conversión, la inferencia (el
preprocesamiento y los cuatro GLM, por bloque), el drift, los hashes, el staging y la publicación.

## El registro de versiones

Una versión (`VersionModeloPredictivo`, identificada por su `modelo_version_id`) es lo que dejó un
entrenamiento `EXITOSA`: sus versiones y hashes, el `dataset_hash`, el `hash_modelo` (el SHA-256 de
los hashes de sus cuatro artefactos, por target), sus periodos de TRAIN, VALIDATION y TEST, la
referencia de TRAIN para el drift y su ficha (ver [modelos_predictivos.md](modelos_predictivos.md)).
Cada componente (`ComponenteModeloPredictivo`, uno por target) guarda su algoritmo, sus
hiperparámetros, su artefacto JSON con su hash, su calibración, sus métricas de VALIDATION y de
TEST, su baseline, sus intervalos bootstrap, su explicación global y sus compuertas.

| Estado de una versión | Qué es |
| --- | --- |
| `CANDIDATA` | Ajustada; solo existe dentro de la transacción que la publica. |
| `VALIDADA` | Sus cuatro componentes pasaron sus compuertas; solo existe dentro de esa transacción. |
| `PUBLICADA` | La única que un scoring puede usar. Inmutable. |
| `RECHAZADA` | Algún componente no pasó sus compuertas. Inmutable; ningún scoring la usa. |

La base lo garantiza: una versión solo avanza `CANDIDATA` → `VALIDADA` → `PUBLICADA`, o a
`RECHAZADA`, sin cambiar nada más; al confirmar, una `PUBLICADA` tiene exactamente sus cuatro
componentes `VALIDADO` y una `RECHAZADA` sus cuatro con alguno `RECHAZADO`; y ninguna se borra. Un
cambio a un modelo es otro entrenamiento, con otra versión.

## El scoring

Un scoring (`EjecucionScoringPredictivo`) declara una versión `PUBLICADA` (**nunca «el último
modelo»**), un corte canónico y, en `BACKTEST`, su `known_at`; en `OPERATIVO`, sin declararlo, es el
reloj de la base, una vez. `as_of` es la fecha del corte. La zona es la del entrenamiento del modelo.
Se rechaza, sin registrar nada:

| Código | HTTP | Qué se rechaza |
| --- | --- | --- |
| `MODELO_NO_ENCONTRADO` | 404 | No existe esa versión. |
| `MODELO_NO_PUBLICADO` | 409 | La versión no está `PUBLICADA`. |
| `MODELO_NO_SOPORTADO` | 422 | La versión es de otras features o de otra versión del modelo. |
| `CORTE_NO_ENCONTRADO` | 404 | No existe el corte. |
| `KNOWN_AT_OBLIGATORIO` | 422 | Un `BACKTEST` declara su `known_at`. |
| `KNOWN_AT_SIN_ZONA` | 422 | `known_at` no lleva su zona. |
| `KNOWN_AT_FUTURO` | 422 | `known_at` es posterior al reloj de la base. |
| `CORTE_NO_CONOCIDO` | 422 | El corte se materializó después de `known_at`. |

`puntuar`, con el candado de la ejecución: arma las features de cada cuenta del corte con lo
conocido a `known_at` en una transacción de lectura (el mismo SQL del entrenamiento), predice con el
runtime de cada artefacto JSON bloque por bloque, cuantiza, mide el drift contra la referencia de
TRAIN, y publica en una transacción corta: copia las predicciones a una tabla de paso, las publica
con un `INSERT … SELECT`, publica el drift y cierra `EXITOSA` con el `hash_predicciones` y el
`hash_drift`, si su trabajo sigue siendo suyo. Los puntos de falla: `ANTES_FEATURES`,
`DESPUES_FEATURES`, `DESPUES_INFERENCE`, `DESPUES_DRIFT`, `DESPUES_STAGING`, `ANTES_PUBLICAR` y
`ANTES_CERRAR`.

| Resultado | Estado | Qué dejó |
| --- | --- | --- |
| `PREDICCIONES_PUBLICADAS` | `EXITOSA` | Una predicción por cuenta del corte y su drift. |
| `YA_PUNTUADO` | `FALLIDA` | Nada: otro scoring publicó primero la misma firma. |
| `VERSION_NO_SOPORTADA` | `FALLIDA` | Nada: el modelo dejó de estar `PUBLICADA` o un artefacto no es íntegro. |
| `DATOS_INCONSISTENTES` | `FALLIDA` | Nada: las features no son las de cada cuenta del corte. |
| `ERROR_INTERNO` | `FALLIDA` | Nada: un error inesperado. |
| `INTENTOS_AGOTADOS` | `FALLIDA` | Nada: su trabajo agotó los intentos de la cola. |

### La predicción de una cuenta

`PrediccionCuenta` guarda, por (scoring, cuenta), las tres probabilidades, el riesgo, la
recuperación positiva esperada, su estado, sus motivos y el `feature_hash` de las features con que se
calculó. Sin ningún dato personal: la cuenta, por su id interno; por la API, por su `cuenta_id`.

- **Cuantización.** Una probabilidad se guarda con 8 decimales (`round(p × 10⁸)`, redondeo al par,
  como entero) y el riesgo es `10⁸ − ese entero`: **exactamente** 1 − p. La recuperación esperada,
  en centavos. Lo que se guarda y se hashea es el decimal exacto, nunca un float binario.
- **Rangos.** La base rechaza una probabilidad fuera de [0, 1], un riesgo que no sea el complemento
  exacto, una recuperación esperada negativa o `NaN`, y un `feature_hash` que no tenga 32 bytes.
- **`feature_hash`.** El SHA-256 de la forma canónica de las features de la cuenta (ver
  [features_predictivas.md](features_predictivas.md)): la predicción se puede reconstruir y
  verificar con él.

| Estado | Qué dice |
| --- | --- |
| `COMPLETA` | Ninguna feature se imputó por un dato que no se conocía. |
| `CON_IMPUTACION` | Alguna feature era NULL por un dato que no se conocía a `known_at`, o una categoría faltaba o no estaba en el vocabulario de TRAIN: se imputó, y sus motivos lo dicen. |

| Motivo | Qué se imputó |
| --- | --- |
| `DATO_NO_REPORTADO` | Un campo del snapshot que el corte no trae. |
| `SIN_HISTORIA` | Una feature de historia: la cuenta no tiene un snapshot anterior conocido, o menos de dos en [T−28, T]. |
| `SIN_COBERTURA_PAGOS` | Una feature de pagos: su ventana no tenía cobertura de pagos a `known_at`. |
| `SIN_EVALUACION_PROMESAS` | Una feature de promesas: no había una evaluación `EXITOSA` conocida con `as_of` hasta T. |
| `CATEGORIA_FALTANTE` | Una categórica NULL: es `__MISSING__`. |
| `CATEGORIA_DESCONOCIDA` | Una categoría que TRAIN no vio: es `__UNKNOWN__`, no una columna nueva. |

Un NULL con significado (`dias_desde_ultimo_contacto` de una cuenta que nunca se contactó) no es un
motivo: su indicador `__nulo` lo dice y el modelo lo usa.

## La explicación de una predicción

`GET /predictivo/scorings/{scoring_run_id}/cuentas/{cuenta_id}/explicacion` se calcula al pedirla y
no se guarda. Recalcula las features de la cuenta con los relojes del scoring (el mismo SQL, con el
universo restringido a la cuenta y la disponibilidad que el scoring ya fijó) y **verifica que su
`feature_hash` sea el publicado**: si no lo es, responde `409 LA_EXPLICACION_NO_REPRODUCE` y no da
una explicación que no corresponda a lo publicado. Después descompone el predictor lineal de cada
componente en la contribución de cada feature: su coeficiente por su valor estandarizado, más el de
su indicador de nulo; en una categórica, el coeficiente de su categoría. **El intercepto más la suma
de las contribuciones es exactamente el predictor lineal** (los log-odds de la probabilidad cruda, o
el log del monto esperado), y la respuesta lo verifica, junto con que la predicción reconstruida es
la publicada. La calibración de Platt es una función creciente del predictor: no cambia el signo de
ninguna contribución.

Devuelve las cinco contribuciones que más empujan hacia arriba y las cinco que más empujan hacia
abajo de cada componente, los valores de las features (los mismos datos de la cuenta que expone la
Cuenta 360: saldos, días, conteos; nunca un nombre, un teléfono, un domicilio ni una coordenada) y
el aviso: **una contribución es una asociación del modelo, no una causa**; no dice que cambiar esa
feature cambie el resultado ni recomienda una acción.

| Código | HTTP | Cuándo |
| --- | --- | --- |
| `SCORING_NO_ENCONTRADO` | 404 | No existe el scoring. |
| `SCORING_NO_PUBLICADO` | 409 | El scoring no está `EXITOSA`. |
| `CUENTA_NO_ENCONTRADA` | 404 | No existe la cuenta. |
| `SIN_PREDICCION` | 404 | El scoring no tiene una predicción de esa cuenta. |
| `LA_EXPLICACION_NO_REPRODUCE` | 409 | Las features recalculadas no son las publicadas. |

La explicación global de cada componente (en su versión) es la lista de sus coeficientes en la
escala estandarizada, de mayor a menor magnitud: también una asociación, no una importancia causal.

## El drift

Cada scoring mide, contra la referencia de TRAIN de su versión, y publica en `drift_predictivo`:

- **PSI por feature.** Una numérica, con los bordes de los deciles de TRAIN de su valor transformado
  sin NULL; una categórica, con su vocabulario de TRAIN (más `__MISSING__` y `__UNKNOWN__`).
- **Faltantes.** La fracción de NULL de cada numérica en TRAIN y en el scoring.
- **PSI por salida.** Con los deciles de lo que se publicaría en TRAIN de cada probabilidad y del
  monto.

PSI = Σ (s − t) · ln(s / t) sobre los bins de TRAIN, con cada proporción en al menos 10⁻⁴.

| Severidad | PSI | Faltantes (diferencia absoluta) |
| --- | --- | --- |
| `BAJO` | menor que 0.10 | menor que 0.05 |
| `MODERADO` | de 0.10 a 0.25 | de 0.05 a 0.20 |
| `ALTO` | mayor que 0.25 | mayor que 0.20 |

Los umbrales son **heurísticas operacionales, no leyes estadísticas**: un PSI alto no prueba por sí
solo que el modelo se deterioró. El drift es agregado (bordes y proporciones, nunca una cuenta),
describe, y **no cambia, no recalibra ni reentrena el modelo**: no hay reentrenamiento automático.
El resumen de cada scoring cuenta las features, las salidas y los faltantes por severidad, lista
las cinco filas de mayor PSI y dice si el corte es de otra cartera que la del entrenamiento.

### El drift de desempeño

Cuando los labels de un scoring maduran, `motor-cartera evaluar-predictivo` compara sus predicciones
publicadas (las cuantizadas) con los labels construidos con la misma regla del entrenamiento (su
cobertura declarada, las interpretaciones vigentes a L) y con las métricas de TEST de su versión. Un
target sin label maduro no se evalúa, y nunca es un cero. Solo lee: no guarda nada y no reentrena.
Un scoring `BACKTEST` del corte de TEST con su `feature_known_at` reproduce **exactamente** las
métricas de TEST del entrenamiento (las pruebas lo comprueban).

## La API

| Ruta | Qué hace | Códigos |
| --- | --- | --- |
| `GET /predictivo/features` | `predictive-features/public-v1` con su hash | 200, 401 |
| `GET /predictivo/targets` | `predictive-targets/public-v1` con su hash | 200, 401 |
| `POST /predictivo/entrenamientos` | Pide un entrenamiento con su manifiesto; lo entrena el worker | 201, 401, 404, 409, 422 |
| `GET /predictivo/entrenamientos` y `/{training_run_id}` | Los entrenamientos (filtro `estado`, paginado) y uno con su manifiesto, sus hashes, sus conteos, su backtest y su versión | 200, 401, 404, 422 |
| `GET /predictivo/modelos` y `/{modelo_version_id}` | El registro (filtro `estado`) y una versión con su ficha y sus cuatro componentes | 200, 401, 404, 422 |
| `POST /predictivo/scorings` | Pide el scoring de un corte con una versión `PUBLICADA` declarada; lo calcula el worker | 201, 401, 404, 409, 422 |
| `GET /predictivo/scorings` y `/{scoring_run_id}` | Los scorings (filtros `estado`, `modelo_version_id`, `corte_id`) y uno con su resumen de drift | 200, 401, 404, 422 |
| `GET /predictivo/scorings/{scoring_run_id}/predicciones` | Las predicciones, filtradas en la base por rango de cada salida, producto, entidad y estado, y ordenadas por una lista cerrada (`orden`, `sentido`); paginadas | 200, 401, 404, 409, 422 |
| `GET /predictivo/scorings/{scoring_run_id}/drift` | El drift por feature y por salida | 200, 401, 404, 409, 422 |
| `GET /predictivo/scorings/{scoring_run_id}/cuentas/{cuenta_id}/explicacion` | La explicación verificada de una predicción | 200, 401, 404, 409, 422 |
| `GET /cuentas/{cuenta_id}/predictivo` | Las predicciones publicadas de una cuenta, de la más reciente a la más antigua | 200, 401, 404, 422 |

Los órdenes de las predicciones son `CUENTA`, `P_RECUPERACION_POSITIVA_30D`, `P_CONTACTO_14D`,
`P_PROMESA_30D`, `RIESGO_SIN_RECUPERACION_30D` y `RECUPERACION_POSITIVA_ESPERADA_30D`, cada uno con
su índice; el riesgo usa el de la recuperación en sentido inverso, y también su rango
(`riesgo_sin_recuperacion_30d_min` es `p_recuperacion_positiva_30d` a lo más su complemento, exacto
en la base). Ningún otro parámetro se acepta.
**201 quiere decir que la ejecución se creó, no que terminó.**

## El CLI

| Comando | Qué hace |
| --- | --- |
| `generar-escenario-predictivo DESTINO --perfil M --cortes 26` | Escribe un escenario sintético (`escenario-predictivo/v1`) con reglas de señal fijas, determinista con su semilla. |
| `cargar-escenario-predictivo DESTINO` | Lo carga semana por semana como cualquier cartera, y anota la frontera de conocimiento de cada corte (`carga.json`). Se reanuda. |
| `construir-manifiesto-predictivo` | Propone un manifiesto, sin entrenar. |
| `entrenar-predictivo --manifiesto` | Entrena en primer plano, con su propio trabajo de la cola. |
| `resumen-modelo-predictivo --modelo` | Una versión: hashes, periodos y, por componente, sus métricas de TEST contra el baseline, sus intervalos y sus compuertas. |
| `score-predictivo --modelo --corte` | Puntúa un corte con una versión `PUBLICADA` declarada (`--modo BACKTEST --known-at`). |
| `drift-predictivo --scoring` | El drift de un scoring, de mayor a menor PSI. |
| `evaluar-predictivo --scoring` | El drift de desempeño con los labels que ya maduraron. Solo lee. |

No hay un backfill predictivo: cada scoring declara su versión y su corte, y nada se entrena ni se
puntúa automáticamente.

## La orquestación

Los entrenamientos y los scorings son trabajos durables separados de la cola (`PREDICTIVE_TRAINING`
y `PREDICTIVE_SCORING`, en el nivel 9: nunca retrasan una etapa operacional), con su lease, su latido,
sus reintentos y `SKIP LOCKED`. Varios workers pueden correr a la vez: el candado de cada ejecución
hace que el segundo espere al primero, y solo publica el dueño vigente de su trabajo: un worker que
perdió su lease mientras ajustaba no publica, y el nuevo dueño entrena desde el principio. El
entrenamiento no mantiene una transacción abierta mientras ajusta; el scoring, solo la de leer sus
features. Bajar la base a la `0015` cierra lo que estaba en curso (`CERRADA_AL_BAJAR`), quita sus
trabajos y las tablas de v0.15, y no toca nada de v0.14.

## La privacidad

- Ninguna feature es un identificador, un dato personal o sensible, una coordenada, ni un hash de
  ellos: un hash de una identidad sigue siendo una identidad (ver
  [features_predictivas.md](features_predictivas.md)).
- Ninguna tabla de v0.15 guarda un `CLIENTE_UNICO`, un `cuenta_id`, un nombre, un teléfono, un
  domicilio ni una coordenada; una predicción apunta a su cuenta por su id interno.
- La bitácora del entrenamiento y del scoring solo lleva identificadores de ejecución y conteos:
  nunca el vector de features, la probabilidad o la recuperación esperada de una cuenta, ni una
  cuenta junto a su score.
- El drift es agregado: bordes y proporciones globales del scoring, ningún bin con una cuenta.
- La explicación devuelve nombres de features, sus valores y sus contribuciones; nunca un dato
  personal.

## Lo medido

Sobre el escenario M (100,000 cuentas, 26 anclas) y un corte XL de 500,000 cuentas, en una sola
máquina: el entrenamiento, 2,667 s y 1,282 MiB; el scoring XL, 173.1 s (2,888 cuentas por segundo),
1,657 MiB y 586 MiB de WAL, 622 bytes por predicción con sus índices; por la API, de 11 a 28 ms por
consulta y 111 ms la explicación de una cuenta. Las métricas, el backtest, el drift y cada fase, en
[backtesting_predictivo.md](backtesting_predictivo.md#lo-medido).

## Limitaciones

- Los modelos **predicen resultados observados bajo la política histórica**. **No estiman
  causalidad**: no dicen qué acción debe ejecutarse ni qué pasaría con otra.
- El benchmark sintético **valida la arquitectura y la estadística; no demuestra el desempeño real
  en producción**. Una buena AUC sintética no es garantía de una AUC real: el generador fija la
  relación entre las features y los resultados.
- La recuperación positiva esperada **no es la recuperación neta esperada**: los netos negativos se
  truncan a cero por definición.
- **Un PSI alto no prueba por sí solo un deterioro.** Mide un cambio de distribución.
- No hay reentrenamiento automático, aprendizaje en línea, ML causal, *uplift* ni Decision Engine
  v3; la optimización (`optimization/v1`) es otro motor, que lee un scoring sin cambiarlo.
- La anulación de un pago por un reverso de otro mes depende de que la interpretación vigente del
  mes del pago ya haya visto ese reverso (su contexto); la regla es la de `motor-pagos/v1` y
  `analytics/v1`.
