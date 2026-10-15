# Los modelos de `predictive/model-v1` y su ficha

Una versión de `predictive/model-v1` tiene cuatro componentes, uno por target, cada uno un modelo
lineal generalizado (GLM) con su artefacto JSON. Cómo se entrenan, se eligen, se calibran, se
evalúan y se publican está fijado, con su hash, en `predictive-training/public-v1`
(`72630f625de5bc53b777da286395474b857d641b810e792c8cb595159973bfe1`): **todo se fijó antes de
entrenar y no se ajusta después de ver TEST**.

| Componente | Target | Algoritmo | Salida |
| --- | --- | --- | --- |
| Recuperación positiva | `RECUPERACION_POSITIVA_30D` | `LOGISTICA_L2` | `p_recuperacion_positiva_30d` |
| Contacto | `CONTACTO_14D` | `LOGISTICA_L2` | `p_contacto_14d` |
| Promesa | `PROMESA_30D` | `LOGISTICA_L2` | `p_promesa_30d` |
| Recuperación positiva esperada | `RECUPERACION_POSITIVA_MONTO_30D` | `TWEEDIE_LOG` | `recuperacion_positiva_esperada_30d` |

El riesgo sin recuperación es 1 − `p_recuperacion_positiva_30d`, exacto: no tiene modelo propio.

## Los algoritmos

- **Regresión logística con L2.** Se minimiza C · Σ log loss + ‖w‖² / 2, sin penalizar el
  intercepto: la parametrización de scikit-learn.
- **GLM Tweedie con liga log**, potencia 1.5 (una masa en cero y una cola continua, como la
  recuperación positiva), con 1/(2n) · Σ devianza + α · ‖w‖² / 2, sin penalizar el intercepto. Su
  predicción, exp(η), nunca es negativa.

Se ajustan con un Newton (IRLS) propio, en NumPy, con búsqueda de paso de Armijo, que acumula X'WX y
X'g bloque por bloque (65,536 filas) en un orden fijo: el resultado es determinista y la memoria es
la de un bloque, nunca la matriz entera. La tolerancia (10⁻¹²) es la que hace que su resultado sea el
de scikit-learn con el mismo objetivo; una prueba lo compara (y lo que se publica no depende de este
código: el artefacto es independiente). Una columna constante en TRAIN no se ajusta: su coeficiente
es exactamente cero. scikit-learn es solo una dependencia de desarrollo, para esa prueba.

No hay árboles, boosting, redes neuronales, AutoML ni búsqueda de hiperparámetros fuera de la
rejilla declarada.

## La elección, sin rebalanceo

| | Rejilla | Se elige por | Empate |
| --- | --- | --- | --- |
| Logística | C ∈ {0.1, 1.0, 10.0} | la menor log loss en VALIDATION | dentro de 10⁻⁴ de la mejor, la menor C (más regularización) |
| Tweedie | α ∈ {0.0001, 0.001, 0.01} | el menor MAE en VALIDATION | dentro de 0.1 % del mejor, la mayor α |

Ningún rebalanceo de clases: ni `class_weight`, ni SMOTE, ni sobremuestreo ni submuestreo. Las
probabilidades quedan en la prevalencia real, que es lo que permite leerlas como probabilidades. El
modelo final se ajusta con TRAIN y no se reajusta con VALIDATION.

## La calibración

Para cada clasificador, en VALIDATION, se ajusta la calibración de Platt: logit(p) = a + b · η, por
máxima verosimilitud y sin regularizar, donde η es el predictor lineal del modelo elegido. **Se
adopta solo si baja el Brier de VALIDATION al menos 0.1 %**; si no, la probabilidad queda cruda
(`RAW`). Nunca se calibra ni se elige nada con TEST: TEST reporta las dos (la publicada y, si se
adoptó Platt, la cruda), sin decidir nada.

## Las métricas

Sobre TEST, con las probabilidades cuantizadas que se publicarían (el runtime del artefacto, no el
estimador de entrenamiento):

- **Clasificación:** prevalencia, ROC AUC, PR AUC (la precisión promedio), log loss, Brier, ECE,
  intercepto y pendiente de calibración, y lift y captura del 10 % y el 20 % superior. Ninguna
  métrica usa la exactitud.
- **Tabla de calibración:** 10 bins de población casi igual, ordenados por la probabilidad: n, la
  predicción media, la frecuencia observada y la predicción mínima y máxima de cada uno.
- **Monto:** MAE, RMSE, la media real y la predicha, la suma real y la predicha, la razón de las
  sumas (null si lo real suma cero) y lo mismo por decil de la predicción.

Las definiciones exactas:

- **ECE:** la suma, sobre los 10 bins, de |predicción media − frecuencia observada| por su fracción
  de las filas.
- **Intercepto y pendiente de calibración:** la pendiente es la de logit(y) ~ a + b · logit(p) por
  máxima verosimilitud; el intercepto, el de logit(y) ~ a + logit(p) con la pendiente fija en 1
  (la calibración en lo grande).
- **Lift y captura:** ordenadas por probabilidad, de mayor a menor (los empates, por el orden de la
  cuenta), las primeras ceil(f · n): captura = positivos entre ellas / positivos; lift = la tasa
  entre ellas / la prevalencia.
- **Intervalos bootstrap:** 200 repeticiones con semilla 2026, por **cuenta** (cada repetición saca
  cuentas con reemplazo y todas las filas de TEST de una cuenta van juntas), percentiles del 95 %:
  de ROC AUC, PR AUC, log loss, Brier y la diferencia de log loss y de Brier contra el baseline; del
  MAE y su diferencia contra el baseline en el monto.

## El baseline y las compuertas

El **baseline** es la prevalencia de TRAIN (en el monto, la media de TRAIN), constante. Nunca de
VALIDATION ni de TEST. Las compuertas de TEST deciden si cada componente queda `VALIDADO`:

| Componente | Compuerta | Regla |
| --- | --- | --- |
| Clasificador | `LOG_LOSS_NO_PEOR_QUE_BASELINE` | log loss ≤ la del baseline |
| Clasificador | `BRIER_NO_PEOR_QUE_BASELINE` | Brier ≤ el del baseline |
| Clasificador | `ROC_AUC_MAYOR_QUE_0_5` | ROC AUC > 0.5 |
| Clasificador | `PROBABILIDADES_VALIDAS` | toda probabilidad finita y en [0, 1] |
| Monto | `MAE_NO_PEOR_QUE_BASELINE` | MAE ≤ el del baseline |
| Monto | `MONTOS_VALIDOS` | toda predicción finita y ≥ 0 |
| Todos | `AJUSTE_CONVERGIO` | el ajuste elegido convergió |

No se exige una AUC arbitraria: se exige superar al baseline fuera del tiempo. **La publicación es
todo o nada**: la versión queda `PUBLICADA` solo si los cuatro componentes quedan `VALIDADO`; si
alguno queda `RECHAZADO`, la versión queda `RECHAZADA` con todas sus métricas, nada se publica y
ningún scoring la usa. Las compuertas no se ajustan después de ver TEST: un componente rechazado se
investiga, y lo que cambie es otro entrenamiento, con otra versión.

## El artefacto JSON

Un modelo publicado es **JSON canónico, nunca pickle, joblib ni cloudpickle**: un pickle ejecuta
código al cargarse, cambia con las versiones de las bibliotecas y no se puede leer. El artefacto
(`predictive/artifact-v1`) trae exactamente lo necesario para predecir:

| Campo | Qué es |
| --- | --- |
| `formato` | `predictive/artifact-v1` |
| `target`, `algoritmo`, `liga` | El target, `LOGISTICA_L2` o `TWEEDIE_LOG`, y `logit` o `log`. |
| `version_features`, `hash_features` | Las features con que se entrenó. |
| `preprocesamiento` | Por numérica: su transformación, su mediana, su media, su desviación y si es constante o tiene indicador; por categórica: su vocabulario y qué columnas son constantes. |
| `columnas` | El orden de las columnas del diseño. |
| `coeficientes`, `intercepto` | Con 12 cifras significativas. |
| `hiperparametros` | La C elegida (o la α, con la potencia y la liga). |
| `calibracion` | `RAW`, o `PLATT` con su a y su b. |
| `precision` | Las cifras significativas, los 8 decimales de una probabilidad y los 2 de un monto. |

Su hash es el SHA-256 de su JSON canónico (llaves en orden, sin espacios, UTF-8, sin NaN ni
infinitos): cambiar un coeficiente cambia el hash, y el orden en que se escriben las llaves no. El
`hash_modelo` de una versión es el SHA-256 de los hashes de sus cuatro artefactos.

## La inferencia independiente

El runtime (`artefacto.Runtime`) **lee solo el JSON**: no usa el código de entrenamiento. Recorre el
artefacto feature por feature, suma la contribución de cada una al predictor lineal y aplica la liga
y la calibración. Las pruebas comparan su predicción con la del estimador de entrenamiento (la misma
hasta el redondeo del float64, e idéntica después de cuantizar) y con scikit-learn. La contribución
de cada feature es exactamente lo que suma: la explicación de una predicción es aritmética.

La cuantización: una probabilidad se guarda con 8 decimales (round(p × 10⁸), redondeo al par, como
entero), el riesgo es 10⁸ menos ese entero (exactamente 1 − p) y un monto se guarda en centavos.

## La ficha del modelo

Cada versión guarda su ficha (`metadatos` en la base; `ficha` en `GET /predictivo/modelos/{id}`):

| Campo | Qué dice |
| --- | --- |
| `uso_previsto` | Ordenar las cuentas de la cartera con que se entrenó por la probabilidad de resultados observados en los días siguientes al corte, y estimar su recuperación positiva esperada, como información descriptiva en paralelo a `decision/v2`, en sombra. |
| `uso_no_previsto` | Estimar el efecto de una acción; decidir sobre una persona o reemplazar `decision/v2`, `campo/v2` o `ruteo/v2`; usarse en otra cartera, zona o periodo sin medir su drift; leer la recuperación positiva esperada como la neta esperada. |
| `periodos` | El primer y el último día de TRAIN, VALIDATION y TEST. |
| `anclas_por_split`, `filas_por_split`, `cuentas_distintas` | El tamaño del dataset. |
| `conteos_por_target` | Por target y split: maduras, censuradas, no disponibles y la prevalencia o la media. |
| `features`, `targets` | Sus versiones, sus hashes y sus nombres; el horizonte y la salida de cada target, y el riesgo derivado. |
| `componentes` | El algoritmo, los hiperparámetros, la calibración y el estado de cada uno, con sus métricas de TEST y las de su baseline. |
| `compuertas` | Las reglas de la política. |
| `referencia_drift` | Contra qué mide un scoring su drift, con qué umbrales, y que son heurísticas. |
| `privacidad_y_etica` | Lo que ninguna feature puede ser, cómo se publica una predicción y que el modelo no decide sobre una persona. |
| `limitaciones` | Lo que no resuelve (abajo). |
| `entorno` | Python, NumPy, Arrow y la plataforma con que se entrenó. |
| `dataset_hash`, `hash_manifiesto`, `hash_modelo`, `hashes_artefactos` | Para reproducirlo y verificarlo. |
| `aviso` | Que estima la probabilidad de resultados observados bajo la operación histórica, no el efecto de una acción. |

Así, la ficha cubre por sí sola lo que pide una *model card*: el uso previsto y el no previsto,
los periodos de entrenamiento y de prueba, las features y los targets, el algoritmo, las
prevalencias, las métricas, la calibración, la referencia del drift, las restricciones de privacidad
y de ética (ver [predictivo.md](predictivo.md)) y estas limitaciones:

- Las asociaciones son las de la operación histórica de esa cartera: si la operación cambia (quién
  se gestiona, cómo y cuándo), cambian.
- Una cuenta sin historia conocida se predice con imputaciones de TRAIN; su predicción lo dice en
  sus motivos.
- Las métricas de TEST son de un solo periodo; el walk-forward muestra su variación.
- **Datos sintéticos.** El generador fija la relación entre las features y los resultados: las
  métricas sobre datos sintéticos validan la arquitectura y la estadística, y no anticipan las de
  una cartera real.
- La probabilidad no es causal y no recomienda una acción.

## La referencia del drift

Cada versión guarda, de TRAIN, las distribuciones contra las que un scoring mide su drift: por
numérica, los bordes de los deciles de su valor transformado sin NULL, sus proporciones y su fracción
de NULL; por categórica, la proporción de cada valor de su vocabulario; por salida, los deciles de lo
que se publicaría en TRAIN y su media. Agregadas: ninguna nombra una cuenta (ver
[predictivo.md](predictivo.md#el-drift)).
