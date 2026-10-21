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

La de v1.0 (`docs/rendimiento/linea-base-M.json`) son las medianas de tres corridas del mismo commit
(PR #38, el 2026-10-21), cada una en su runner: una con un AMD EPYC 7763 y dos con un AMD EPYC 9V74,
de 4 núcleos y 16 GB. El perfil M fueron 100,000 cuentas, 55,809 movimientos en el archivo de pagos
de la ingesta, 112,153 observaciones de pagos en tres cortes, 96,115 eventos del lifecycle y 98,768
cuentas decididas; cada corrida tardó de 5 a 6.5 minutos.

| Métrica | Mediana | Rango de las tres |
| --- | ---: | ---: |
| Ingesta de `cartera/v2` | 17.1 s | 16.8–20.6 s |
| Ingesta de `pagos/v1` | 3.3 s | 3.2–3.9 s |
| Historia de los cortes | 9.0 s | 8.7–10.3 s |
| Historia de los pagos | 3.6 s | 3.2–3.7 s |
| Interpretación del motor de pagos | 8.9 s | 8.8–9.4 s |
| Importación del lifecycle | 12.2 s | 12.2–15.5 s |
| Atribución | 4.5 s | 4.3–5.4 s |
| Evaluación de promesas | 1.2 s | 1.2–1.6 s |
| `decision/v1` | 4.9 s | 4.8–7.3 s |
| `decision/v2` | 8.2 s | 8.2–10.1 s |
| Memoria pico de la ingesta de cartera | 824 MiB | 818–830 MiB |
| Memoria pico del motor de pagos y de `decision/v2` | 187 y 186 MiB | variación menor a 1 % |

Las 31 consultas tienen medianas de 2.9 a 112 ms. **Lo que variaron:** los tiempos, de 3 a 20 % (el
coeficiente de variación); la corrida en el EPYC 7763 fue la más lenta, hasta 48 % arriba de la
mediana en `decision/v1`; la memoria, menos de 1 %; las consultas, de 7 a 23 %. De ahí las
tolerancias, y no de un número redondo:

| Clase | Relativa | Absoluta |
| --- | ---: | ---: |
| Tiempo | 50 % | 2 s |
| Memoria | 20 % | 64 MiB |
| Consulta | 50 % | 25 ms |

Con la CPU que cambia de un runner a otro, una sola corrida en la más lenta habría pasado una
tolerancia de 30 %; por eso cada corrida programada es la mediana de tres, y la de tiempo es de 50 %.
Detecta una regresión grande (un motor que tarda el doble, una consulta que pierde su índice), no una
de 10 %: para eso hacen falta mediciones en una máquina dedicada.

Una línea base nueva son las medianas de varias corridas del mismo commit, cada una en su runner, con
sus tolerancias por clase de métrica (tiempo, memoria y consulta):

```bash
# Varias corridas comparables: a mano, con 5 repeticiones, cada una en su runner.
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
