# La señal de rendimiento

Una regresión de rendimiento se nota en una semana, sin correr un XL en cada pull request:
`.github/workflows/performance.yml` mide el perfil M cada domingo con los benchmarks de siempre y
compara lo medido con una línea base. Es una **señal**, no un objetivo de servicio: no es un check
obligatorio ni detiene una release. El porqué, en la decisión 307 de [decisiones.md](decisiones.md).

## Qué se mide

El perfil M (100,000 cuentas), sobre PostgreSQL 16 con PostGIS en un runner `ubuntu-24.04` de GitHub,
con los scripts de `scripts/`:

| Capa | Benchmark | Métricas |
| --- | --- | --- |
| Ingesta | `benchmark_escala.py --perfil M` | segundos de la ingesta de `cartera/v2` y de `pagos/v1`; memoria pico de la de cartera |
| Historia y motor de pagos | `benchmark_motor_pagos.py --perfil M --cortes 3` | segundos de la historia de los cortes y de los pagos, y de la interpretación; memoria pico del motor; la mediana de cada consulta |
| Lifecycle y atribución | `benchmark_lifecycle.py --intensidad 0.5` | segundos de la importación, de la atribución y de la evaluación de promesas; la mediana de cada consulta |
| Decisión | `benchmark_decision_v2.py` | segundos de `decision/v1` y de `decision/v2` del último corte; memoria pico de `decision/v2`; la mediana de cada consulta |

Cada proceso pesado corre aparte, así que su memoria pico es solo suya, y cada consulta se repite 25
veces (su mediana). Cada corrida sube sus reportes como artefacto (`rendimiento-N`, 30 días) con la
máquina donde midió: el modelo de la CPU, sus núcleos, su memoria y la imagen del runner. XL y XXL
siguen a mano, en `benchmark.yml`.

## Cuándo es una regresión

`scripts/comparar_rendimiento.py` toma la **mediana** de las repeticiones de una corrida y la compara
con la línea base (`docs/rendimiento/linea-base-M.json`). Una métrica es una regresión solo si
empeora más que su tolerancia **relativa y** que la **absoluta**, a la vez: unos puntos de ruido no la
marcan, y una consulta de milisegundos tampoco por un porcentaje grande de casi nada. Si mejora más
que las dos, es una mejora, y conviene medir una línea base nueva. El job `comparar` falla con una
regresión (o si falta una métrica de la línea base), deja su tabla en el resumen del run y sube
`rendimiento.json` y `rendimiento.md` (90 días).

## La línea base

La línea base son las medianas de varias corridas del mismo commit, cada una en su runner, con sus
tolerancias por clase de métrica (tiempo, memoria y consulta):

```bash
# Varias corridas comparables: a mano, con 3 o 5 repeticiones, cada una en su runner.
gh workflow run performance.yml -f repeticiones=5
# Sus reportes, y la línea base de sus medianas.
gh run download <run> --pattern 'rendimiento-*' --dir rendimiento
python scripts/comparar_rendimiento.py --resultados rendimiento/rendimiento-* \
  --escribir-linea-base docs/rendimiento/linea-base-M.json --origen "run <run>"
```

Una línea base nueva va en su propio pull request (que vuelve a medir, tres veces, contra ella) y
dice por qué cambió. No se ajusta para que una regresión deje de serlo.

## Lo que no es

- **No es un SLO.** Los runners compartidos de GitHub cambian de CPU y de carga de una corrida a otra;
  la tabla dice en qué máquinas se midió cada vez.
- **No mide un XL** ni la red vial, analytics, predictivo u optimización: esos benchmarks siguen a
  mano, con su script, y sus resultados están en el documento de cada motor ([índice](README.md#benchmarks)).
- **No compara entre perfiles**: una línea base es de un perfil y de sus cortes.
