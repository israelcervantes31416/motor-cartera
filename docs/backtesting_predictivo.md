# El split temporal, el walk-forward y el benchmark de `predictive/model-v1`

## El split temporal

**No hay split aleatorio.** Un modelo que se evalúa con cuentas del mismo periodo que lo entrenó
mide qué tan bien recuerda ese periodo, no qué tan bien predice el siguiente. El split lo declara el
manifiesto, ancla por ancla: `TRAIN`, `VALIDATION`, `TEST` o `PURGE`. TRAIN es anterior a
VALIDATION y VALIDATION a TEST, por `feature_as_of`; nunca al revés ni mezclados.

### La purga

Entre dos bloques consecutivos hay una **purga**: el final de la ventana del label más largo de un
bloque (T + 30) es anterior a la primera ancla del bloque siguiente:

```text
max(T de TRAIN) + 30 < min(T de VALIDATION)
max(T de VALIDATION) + 30 < min(T de TEST)
```

Sin ella, el label de la última ancla de TRAIN vería los pagos y las gestiones de los días en que
empieza VALIDATION: el modelo aprendería con información del periodo con que se evalúa. Con cortes
semanales, la purga deja al menos cuatro anclas entre bloques; el manifiesto las declara `PURGE`, y
la comprobación lo rechaza con `PURGA_INSUFICIENTE` si faltan. Las anclas `PURGE` (y las del final,
cuyos labels todavía no maduran) se construyen y se cuentan, pero no entran a TRAIN, VALIDATION ni
TEST.

### TEST, intocable

TEST se evalúa **una sola vez**, al final, con el artefacto publicado. No elige C ni α, no decide la
calibración, no elige features ni umbrales, y no cambia el generador. Si algo se cambia después de
ver TEST (por un defecto demostrado), la evaluación anterior queda invalidada y se repite desde la
selección.

## El walk-forward

El procedimiento se repite en **tres folds** sobre lo anterior a TEST. Las anclas anteriores a TEST
(hasta la última de VALIDATION, en orden) se recorren hacia atrás en bloques del tamaño de
VALIDATION:

- el fold 3 es el split principal: TRAIN contra VALIDATION, como lo declara el manifiesto;
- el fold 2 valida con el bloque anterior a VALIDATION y entrena con las anclas cuya ventana de
  label más larga termina antes de ese bloque (su propia purga);
- el fold 1, con el bloque anterior, igual.

Cada fold aprende su propio preprocesamiento de sus anclas de entrenamiento (nunca de su
validación), ajusta cada valor de la rejilla y lo mide en su validación contra el baseline de su
entrenamiento, sin calibrar. **TEST no participa en ningún fold.** Un fold sin anclas suficientes es
`NO_DISPONIBLE` y lo dice: el walk-forward es de tres folds «si los datos lo permiten».

El backtest de cada entrenamiento (`backtest` de la ejecución) trae, por target:

- cada fold con el hiperparámetro elegido en el split principal: log loss, Brier, ROC AUC y PR AUC
  (o MAE y RMSE), contra el baseline de su entrenamiento, con las prevalencias (o medias) de su
  entrenamiento y de su validación;
- el agregado de los folds: la media, el mínimo y el máximo de cada métrica, para que una
  degradación se vea y no se esconda en un promedio;
- el holdout de TEST, aparte, contra su baseline;

y, por fold, la rejilla completa y cuál habría elegido ese fold.

## El escenario sintético con señal

El escenario de `generar-escenario` paga una fracción fija de cuentas al azar cada semana: ningún
dato del pasado dice nada del futuro. El benchmark de v0.15 usa **`escenario-predictivo/v1`**
(`motor-cartera generar-escenario-predictivo`), con reglas de señal **públicas, declaradas en su
manifiesto (`escenario.json`) y fijadas antes de entrenar**:

- cada cliente tiene dos rasgos latentes fijos, derivados de su número y de la semilla: su
  **voluntad** de pago y su **contactabilidad**, normales estándar; ningún archivo los trae, y un
  modelo solo puede inferirlos de lo que dejan ver;
- la cobranza gestiona más a las cuentas de más atraso, por el canal de su tramo; el contacto
  depende de la contactabilidad y del canal;
- con el titular en la línea, la promesa depende de la voluntad;
- el pago depende de la voluntad, del atraso, del producto, de haber hablado con el titular, de haber
  prometido y de estar al corriente; un pago de una promesa llega después de ella;
- el corte siguiente sale de `oficial.evolucionar`: los pagos bajan el saldo y curan el atraso, las
  liquidadas y las retiradas salen y llegan altas con rasgos propios.

Las tasas base se fijaron con una simulación del propio generador (sin entrenar nada ni ver una
métrica de un modelo), para una recuperación a 30 días cercana a 30 %, un contacto a 14 días cercano
a 35 % y una promesa a 30 días cercana a 16 %. Todo sale de la semilla **2026**: el mismo escenario
sale igual, byte por byte. El manifiesto declara la cobertura exacta de los pagos y del lifecycle.

**Los labels salen del pipeline real**: el generador escribe cortes, pagos y lifecycle, nunca labels.
`cargar-escenario-predictivo` los carga semana por semana como cualquier cartera (la ingesta, la
historia, el motor de pagos, el lifecycle y la evaluación de promesas), y anota después de cada corte
su frontera de conocimiento (`carga.json`): la `feature_known_at` de su ancla. Los labels los
construye el dataset del entrenamiento con `motor-pagos/v1` y el lifecycle, como los de una cartera
real.

## El benchmark

El benchmark separa dos preguntas (ver la decisión 221):

- **El modelo**: el entrenamiento sobre el escenario M (100,000 cuentas, 26 anclas semanales
  después de 13 semanas de historia, unos 2.6 millones de filas cuenta-ancla antes de la censura),
  con su backtest temporal. No hace falta entrenar con 500,000 cuentas: no aportaría evidencia del
  modelo y multiplicaría el tiempo y la memoria.
- **La escala operacional**: el scoring de un corte XL de 500,000 cuentas, con su drift, su
  publicación, su WAL y el crecimiento de la base.

`scripts/benchmark_predictivo.py` mide, una tarea pesada a la vez y cada una en su proceso:

1. el entrenamiento M, con el manifiesto que propone `construir-manifiesto-predictivo --escenario
   ... --desde` (las 13 primeras semanas son historia, no anclas): las filas (maduras, censuradas,
   por split), las prevalencias por split, el número de features y de columnas del diseño, el
   tiempo de cada fase (las features, los labels, el COPY y la conversión del dataset; el
   preprocesamiento, cada componente, la calibración, la evaluación, el walk-forward y la referencia
   del drift), la memoria pico y el WAL; el backtest por target (cada fold, su agregado y el
   holdout) y las métricas de TEST de cada componente contra su baseline, sin calibrar y
   calibradas, con su tabla de calibración;
2. el scoring XL: el tiempo de las features, la conversión, la inferencia, el drift, la publicación y
   el total, las cuentas por segundo, la memoria pico, el WAL, el crecimiento de la base y los bytes
   por predicción;
3. el drift: un scoring **A** del último corte de VALIDATION, en `BACKTEST` con su
   `feature_known_at` (el generador de TRAIN, unas semanas después y fuera de TRAIN), y uno **B**
   deliberadamente desplazado: el corte XL, de otro generador y con otra historia; el modelo no
   cambia;
4. los planes (`EXPLAIN (ANALYZE, BUFFERS)`) y la latencia de la API: la versión, el registro, la
   página de predicciones por cada orden y con un rango de riesgo, las predicciones de una cuenta,
   el drift y la explicación de una cuenta.

Una feature que cuenta historia (`numero_cortes_observados`, por ejemplo) crece con el tiempo: en
cualquier corte posterior a TRAIN su PSI sale alto, sin que eso sea un deterioro. Por eso A no es
"el último corte": es el más cercano a TRAIN que TRAIN no vio.

Cada corrida escribe su reporte (`benchmark_predictivo.md` y `benchmark_predictivo.json`) y el
plan de cada sentencia de la API (`planes_predictivo.txt`) en su `--destino`; con `--reanudar` no
repite una etapa que ya terminó, y un entrenamiento que quedó `EN_PROCESO` porque su proceso murió
se retoma por la cola, como lo haría un worker cuando vence su lease.

### Lo medido

En una sola máquina (Windows 11, 12 CPU, PostgreSQL 16.15 con su configuración por omisión), sobre
la base de los benchmarks anteriores (22 GB, con el XL de `CARTERA_PRINCIPAL`), y sin ajustar nada
después de ver TEST.

**El escenario M** (`escenario-predictivo/v1`, semilla 2026): 100,000 cuentas en el primer corte y
113,876 en el último (con altas y bajas), 39 cortes semanales del 2026-01-07 al 2026-09-30, cargados
semana por semana por el pipeline de siempre en su propia cartera: 39 cortes, 38 archivos de pagos
con sus interpretaciones por mes, 3,388,760 eventos del lifecycle y 39 evaluaciones de promesas. Por
semana, el generador gestionó el 50.9 % de las cuentas, contactó al 19.4 %, obtuvo una promesa del
4.1 % y cobró al 11.9 %.

**El manifiesto**: 26 anclas desde el 2026-04-08 (las 13 semanas anteriores son historia de las
features, no anclas): TRAIN 7 (del 2026-04-08 al 2026-05-20), VALIDATION 3 (del 2026-06-24 al
2026-07-08), TEST 3 (del 2026-08-12 al 2026-08-26) y PURGE 13, con seis interpretaciones de pagos,
de abril a septiembre. Hash del manifiesto `0ff3cf44a77cbf0f…`, dataset `6d90e9127160a633…`,
modelo `7abeadd7473d8f95…`.

**El entrenamiento**: 2,957,237 filas cuenta-ancla, 757,301 de TRAIN, 339,718 de VALIDATION,
352,884 de TEST y 1,507,334 de PURGE; 601,059 con algún label censurado, que no entran a ningún
ajuste. 40 features dan 74 columnas de diseño, que se recorren en bloques de 65,536 filas (37 MiB):
TRAIN completo serían 427.6 MiB en float64. **2,667 s** en total, con 1,282 MiB de memoria pico y
10 MiB de WAL:

| Fase | Segundos |
| --- | ---: |
| Dataset: las features de las 26 anclas | 1,485.2 |
| Dataset: los labels | 330.3 |
| Dataset: el COPY y la conversión a Arrow y NumPy | 43.7 |
| Preprocesamiento | 1.2 |
| Ajuste de recuperación, contacto, promesa y monto, con su rejilla | 107.8, 89.1, 89.3 y 95.1 |
| Calibración | 2.3 |
| Evaluación de TEST, con su bootstrap | 127.2 |
| Walk-forward (dos folds, cada uno con su preprocesamiento y su rejilla) | 282.6 |
| Referencia del drift | 5.4 |

Las prevalencias por split se mueven poco, y se ven: la recuperación positiva baja de 29.61 % en
TRAIN a 28.46 % en VALIDATION y 27.58 % en TEST; el contacto, de 33.27 % a 33.02 % y 33.03 %; la
promesa, de 15.18 % a 14.80 % y 14.60 %; el monto medio, de 1,401.75 a 1,263.68 y 1,215.38. Ningún
componente rebalancea clases.

| TEST (352,884 filas) | Elegido | ROC AUC [IC 95 %] | PR AUC | Log loss (baseline) | Brier (baseline) | ECE | Pendiente | Lift y captura al 10 % |
| --- | --- | --- | ---: | --- | --- | ---: | ---: | --- |
| Recuperación positiva 30 d | C = 10, RAW | 0.8359 [0.8338, 0.8385] | 0.6677 | 0.4322 (0.5899) | 0.1388 (0.2001) | 0.0136 | 1.008 | 2.81 y 28.1 % |
| Contacto 14 d | C = 0.1, RAW | 0.6798 [0.6775, 0.6823] | 0.5026 | 0.5893 (0.6344) | 0.2016 (0.2212) | 0.0085 | 0.995 | 1.81 y 18.1 % |
| Promesa 30 d | C = 0.1, RAW | 0.6833 [0.6803, 0.6872] | 0.2631 | 0.3892 (0.4158) | 0.1179 (0.1247) | 0.0048 | 1.035 | 2.18 y 21.8 % |

Los tres mejoran a su baseline con intervalos que no tocan el cero (la diferencia de log loss de la
recuperación, −0.1577 [−0.1604, −0.1555]). Platt no se adoptó en ninguno: en VALIDATION no mejoró el
Brier al menos 0.1 %, porque la logística sin calibrar ya salía calibrada (pendientes de 0.995 a
1.035). La tabla de calibración de la recuperación positiva en TEST, por decil de predicción:

| Decil | Predicción media | Frecuencia observada |
| ---: | ---: | ---: |
| 1 | 0.0398 | 0.0398 |
| 2 | 0.0522 | 0.0514 |
| 3 | 0.0650 | 0.0639 |
| 4 | 0.0827 | 0.0821 |
| 5 | 0.1126 | 0.1248 |
| 6 | 0.1883 | 0.2247 |
| 7 | 0.3081 | 0.3426 |
| 8 | 0.4351 | 0.4610 |
| 9 | 0.5852 | 0.5937 |
| 10 | 0.7579 | 0.7737 |

El monto (Tweedie, alpha = 0.0001): MAE **1,462.72** [1,414.01, 1,513.74] contra 2,132.04 del
baseline, RMSE 11,516.67 contra 12,103.83; suma real 428,886,909.20 y predicha 375,558,340.97 (razón
0.876): subestima el decil más alto (6,699 de media predicha contra 7,712 observada).

**El backtest temporal**, por target, con el hiperparámetro elegido en el split principal:

| Target | Fold 1 (VALIDATION 2026-05-13 a 05-27) | Fold 2 (06-03 a 06-17) | Principal (06-24 a 07-08) | Agregado de los folds | Holdout TEST |
| --- | --- | --- | --- | --- | --- |
| Recuperación: ROC AUC | 0.8269 | 0.8338 | 0.8342 | 0.8316 (0.8269 a 0.8342) | 0.8359 |
| Recuperación: log loss (baseline del fold) | 0.4484 (0.6029) | 0.4407 (0.5997) | 0.4390 (0.5976) | 0.4427 | 0.4322 (0.5899) |
| Contacto: ROC AUC | 0.6774 | 0.6767 | 0.6774 | 0.6771 (0.6767 a 0.6774) | 0.6798 |
| Promesa: ROC AUC | 0.6687 | 0.6778 | 0.6787 | 0.6751 (0.6687 a 0.6787) | 0.6833 |
| Monto: MAE (baseline del fold) | 1,601.49 (2,271.21) | 1,556.07 (2,206.10) | 1,531.59 (2,157.62) | 1,563.05 | 1,462.72 (2,132.04) |

Ningún fold se degrada: cada uno supera a su baseline, y TEST queda dentro del rango o apenas arriba.

**El scoring XL**: el corte del 2026-04-01 de `CARTERA_PRINCIPAL`, 500,000 cuentas de otro
generador, con la versión publicada: **173.1 s, 2,888 cuentas por segundo**, 1,657 MiB de memoria
pico, 586 MiB de WAL y 326 MiB más en la base. Por fase: features 104.9 s, conversión 0.8 s,
inferencia (el preprocesamiento y los cuatro GLM) 9.4 s, drift 0.8 s, hashes 5.3 s, la tabla de paso
8.9 s y la publicación 42.7 s. Las 500,000 salieron `CON_IMPUTACION`: ese corte no tiene sus ventanas
de pagos de 30 y 90 días cubiertas (13 días sin cobertura), y su motivo lo dice.

**El drift**, con el mismo modelo y sin cambiar nada: A es el último corte de VALIDATION (113,876
cuentas, `BACKTEST` con su `feature_known_at`; 75.4 s) y B el XL. En A, una feature en ALTO
(`numero_cortes_observados`, PSI 2.26, que cuenta historia y crece con el tiempo), PSI medio 0.08,
mediano 0.002, y las cuatro salidas en BAJO (la mayor, 0.09). En B, 25 features en ALTO y 9 con su
fracción de NULL en ALTO (los movimientos sin cobertura: de 0 % a 100 % de NULL), PSI medio 2.95 y
mediano 1.36, y tres salidas en ALTO (la p de recuperación, 1.83). El drift los distingue.

**Tamaños**: 613,876 predicciones en 364 MiB, **622 bytes cada una** con sus índices (243 MiB de datos
y 121 MiB de índices).

**La API sobre el scoring XL** (20 repeticiones, con la serialización): la versión 18 ms de mediana,
el registro 18 ms, el scoring 13 ms, la primera página de predicciones 17 ms, la página 100 28 ms,
las mayores recuperaciones esperadas, la mayor p de recuperación y el mayor riesgo 18 a 19 ms, un
filtro de riesgo de al menos 0.95 17 ms, el drift 15 ms, las predicciones de una cuenta 11 ms y la
explicación de una cuenta, que recalcula sus features, **111 ms** (29 sentencias, 18 ms en
PostgreSQL). Cada consulta entra por su índice; ningún Seq Scan sobre una tabla grande.

**Lo que el benchmark encontró, y se corrigió antes del PR** (los números de arriba son los de después):

- proponer el manifiesto no terminaba: la comprobación de que la interpretación de septiembre
  clasificó cada pago, recién publicada y fuera de las estadísticas, recorría sus 80,000 resultados
  por cada uno de sus 80,000 pagos; con un `EXISTS` por pago sobre la llave primaria, 1.9 s;
- la explicación de una cuenta tardaba 2.7 s: recorría los 9 millones de movimientos de la base; con
  una sola cuenta, se buscan por el índice de sus movimientos;
- cada página contaba las 500,000 predicciones (53 ms), y un filtro raro con el orden por cuenta
  recorría el scoring entero (366 ms).
