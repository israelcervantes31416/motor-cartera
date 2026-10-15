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
repite una etapa que ya terminó.
