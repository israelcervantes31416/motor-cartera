# Cohortes, conjuntos, periodos y embudos de `analytics/v1`

Cómo se forma una cohorte, de dónde sale cada dimensión, qué conjuntos de dimensiones se publican y
por qué solo esos, cómo se cortan los periodos y qué es cada etapa de los embudos. Lo general está en
[analytics.md](analytics.md); cada métrica, en [metricas_analytics.md](metricas_analytics.md).

## La cohorte

Una cohorte es **un corte canónico**: sus snapshots, una cuenta una vez, con lo que cada cuenta era
en `fecha_corte`. No son «las cuentas que siguen al final del horizonte» (sesgo de supervivencia):
una cuenta que salió de la cartera a la mitad del horizonte sigue en su cohorte, con su saldo
inicial y lo que le pasó mientras estuvo.

Las dimensiones de una cuenta se fijan al corte. Si una cuenta era `ALTA` en el corte y después
cambió, la celda `ALTA` de esa cohorte no se reescribe: otra prioridad es de otro corte, o de otra
`decision/v2` declarada.

Comparar cohortes es comparar análisis:

- **Dos cortes con el mismo horizonte** (por ejemplo, 30 días cada uno): dos cohortes, cada una con
  su población inicial.
- **El mismo corte con otro `as_of`**: la misma cohorte, más tiempo. Lo acumulado crece y un periodo
  ya publicado puede cambiar (un reverso que llegó después corrige el pago de su día).
- **El mismo corte y el mismo `as_of` con otro `known_at`**: lo que se sabía en cada frontera. Una
  gestión registrada tarde aparece; una anulación que se supo después borra la gestión.

## Las dimensiones de una cuenta

| Llave | De dónde sale | Sin dato |
|---|---|---|
| `producto` | `PRODUCTO` del snapshot del corte. | — (obligatorio en cartera/v2) |
| `atraso_bucket` | Los días de atraso del snapshot, con los tramos canónicos de atraso: `0`, `1-30`, `31-60`, `61-90` y `91+`. | Una cuenta fuera de todo tramo deja la ejecución `DATOS_INCONSISTENTES`. |
| `entidad` | La clave INEGI de la entidad que resolvió la fuente (`ESTADO_CTE`). | — |
| `municipio` | La clave INEGI de cinco dígitos (entidad y municipio) que resolvió la fuente (`ESTADO_CTE`, `POBLACION_CTE`): la observada, no la de la coordenada. | — |
| `prioridad_decision` | La prioridad final de la `decision/v2` declarada del corte; en campo y ruteo, la del plan. | Sin `decision/v2` declarada, el conjunto no se forma. |
| `canal_decision` | El canal recomendado de esa `decision/v2`. | Igual. |
| `base` | La base de la `territorial/v2` declarada del corte; en campo y ruteo, la del plan. | `SIN_BASE` si la cuenta no quedó asignada a una. |
| `canal_gestion` | El canal de cada gestión efectiva (universo `GESTION`). | — |
| `medio_gestion` | El medio de cada gestión efectiva. | `SIN_MEDIO` (una gestión de campo no tiene medio). |
| `estado_campo` | El estado de la candidata en el plan de `campo/v2`. | — |
| `gestor_campo` | El código del gestor de campo del plan, con su base. | `SIN_GESTOR` si la candidata no quedó `ASIGNADA`. |
| `estado_ruteo` | El estado de la parada en `ruteo/v2`. | — |

El `dimension_hash` de una celda es el SHA-256 del JSON canónico de sus dimensiones (llaves en
orden, sin espacios, UTF-8), el mismo en Python y en PostgreSQL: no depende del orden de las llaves,
del proceso ni del orden en que se insertaron las filas.

## Los universos

La población de una celda es **cuántas cuentas distintas de su universo** caen en ella en el
horizonte:

| Universo | Qué cuentas |
|---|---|
| `COHORTE` | Las del corte. |
| `GESTION` | Las de la cohorte con al menos una gestión efectiva en el horizonte, por canal o por medio: una cuenta gestionada por dos canales está en los dos. |
| `CAMPO` | Las candidatas de campo del plan declarado. |
| `RUTEO` | Las paradas de la ejecución de ruteo declarada (una por cuenta `ASIGNADA`). |

## Los conjuntos

Una métrica se publica en los conjuntos que declara el catálogo, y solo en los que se pueden formar
con las entradas declaradas. **No hay GROUP BY arbitrario**: agregar un conjunto es otra versión del
catálogo.

| Conjunto | Llaves | Universos (y lo que exigen) | Dentro de |
|---|---|---|---|
| `TOTAL` | ninguna | `COHORTE`, `CAMPO`, `RUTEO` | — |
| `PRODUCTO` | `producto` | `COHORTE` (el corte) | `TOTAL` |
| `ATRASO_BUCKET` | `atraso_bucket` | `COHORTE` (el corte) | `TOTAL` |
| `PRIORIDAD_DECISION` | `prioridad_decision` | `COHORTE` (`DECISION_V2`), `CAMPO` (`CAMPO_V2`), `RUTEO` (`RUTEO_V2`) | `TOTAL` |
| `CANAL_DECISION` | `canal_decision` | `COHORTE` (`DECISION_V2`) | `TOTAL` |
| `ENTIDAD` | `entidad` | `COHORTE` (el corte) | `TOTAL` |
| `MUNICIPIO` | `municipio` | `COHORTE` (el corte) | `ENTIDAD` |
| `BASE` | `base` | `COHORTE` (`TERRITORIAL_V2`), `CAMPO` (`CAMPO_V2`), `RUTEO` (`RUTEO_V2`) | `TOTAL` |
| `CANAL_GESTION` | `canal_gestion` | `GESTION` | `TOTAL` |
| `MEDIO_GESTION` | `medio_gestion` | `GESTION` | `TOTAL` |
| `ESTADO_CAMPO` | `estado_campo` | `CAMPO` (`CAMPO_V2`) | `TOTAL` |
| `ESTADO_RUTEO` | `estado_ruteo` | `RUTEO` (`RUTEO_V2`) | `TOTAL` |
| `GESTOR_CAMPO` | `base`, `gestor_campo` | `CAMPO` (`CAMPO_V2`), `RUTEO` (`RUTEO_V2`) | `BASE` |
| `PRODUCTO_X_ATRASO` | `producto`, `atraso_bucket` | `COHORTE` (el corte) | `PRODUCTO` y `ATRASO_BUCKET` |
| `BASE_X_PRIORIDAD` | `base`, `prioridad_decision` | `COHORTE` (`TERRITORIAL_V2` y `DECISION_V2`), `CAMPO` (`CAMPO_V2`) | `BASE` y `PRIORIDAD_DECISION` |
| `CANAL_X_PRIORIDAD` | `canal_decision`, `prioridad_decision` | `COHORTE` (`DECISION_V2`) | `CANAL_DECISION` y `PRIORIDAD_DECISION` |

«Dentro de» son los grupos de la supresión complementaria: un conjunto contra el `TOTAL` de su
universo, `MUNICIPIO` dentro de su `ENTIDAD`, un cruce dentro de cada uno de sus marginales y
`GESTOR_CAMPO` dentro de su `BASE`. Cada grupo es una ecuación (su total es la suma de sus celdas)
que nunca queda con una sola incógnita, ni con el total visible ni con el total suprimido
([analytics.md](analytics.md#privacidad-celdas-pequeñas-sin-datos-de-una-cuenta)). Los conjuntos
de la cohorte son excluyentes (cada cuenta cae en
exactamente una celda de cada uno), y los controles comprueban que cada uno sume su `TOTAL` en
cuentas, saldo y recuperación.

**No son dimensiones**, a propósito: la cuenta, el `CLIENTE_UNICO`, el `cuenta_id`, un nombre, una
dirección o un teléfono; el `actor_ref` de una gestión (puede ser una persona); las coordenadas; y la
partición de campo (zona, subzona, tramo y microtramo), que es una cuadrícula operativa de 1 km: casi
cada celda tendría menos de 10 cuentas y, cruzada con otras, ubicaría cuentas.

## Los periodos y las vistas

Cada métrica se publica en la vista **`ACUMULADA`**: un solo periodo, del corte a `as_of`. Si la
granularidad no es `TOTAL` y la métrica tiene vista **`PERIODO`**, también por periodo, en los
conjuntos que la política publica por periodo (`TOTAL`, `PRODUCTO`, `ATRASO_BUCKET`,
`PRIORIDAD_DECISION`, `CANAL_DECISION`, `ENTIDAD`, `BASE`, `CANAL_GESTION` y `MEDIO_GESTION`): los
cruces y el municipio solo en la acumulada. Un periodo empieza el primer día de su día, semana ISO o
mes, recortado al corte, y termina el último, recortado a `as_of`; si se recortó, `periodo_parcial =
true`.

Con el corte del miércoles 2026-09-30 y `as_of` del jueves 2026-10-08:

| Granularidad | Periodos |
|---|---|
| `TOTAL` | Solo la acumulada: del 2026-09-30 al 2026-10-08. |
| `DIA` | Nueve: del 2026-09-30 al 2026-10-08, uno por día, ninguno parcial. |
| `SEMANA_ISO` | Del 2026-09-30 al 2026-10-04 (parcial: la semana empezó el lunes 28) y del 2026-10-05 al 2026-10-08 (parcial). |
| `MES` | El 2026-09-30 (parcial) y del 2026-10-01 al 2026-10-08 (parcial). |

La celda y su supresión se deciden una vez, con la población del horizonte: una celda suprimida lo
está en todos sus periodos, y una celda visible lo es aunque un periodo suyo tenga pocas cuentas
activas. Los periodos de una métrica aditiva suman su acumulada (los controles lo comprueban); una
métrica de cuentas distintas no: una cuenta gestionada en dos periodos cuenta en los dos, y una vez
en la acumulada.

## Los embudos

Un embudo es una cadena de **conjuntos de cuentas anidados** del horizonte entero (la vista
`ACUMULADA`, `TOTAL`), no una suma de eventos:

| Embudo | Etapa | Cuentas |
|---|---|---|
| `CONTACTO` | `COHORTE` | Las del corte. |
| `CONTACTO` | `GESTIONADAS` | Las que tuvieron al menos una gestión efectiva. |
| `CONTACTO` | `CONTACTADAS` | De ellas, las que tuvieron al menos una con contacto. |
| `CONTACTO` | `CON_PROMESA` | De ellas, las que crearon al menos una promesa. |
| `CAMPO` | `CANDIDATAS` | Las candidatas de campo del plan declarado. |
| `CAMPO` | `ASIGNADAS` | De ellas, las que quedaron con un gestor. |
| `CAMPO` | `PARADAS_PROGRAMADAS` | De ellas, las que quedaron programadas en una ruta (planificado, no visitado). |

Cada etapa dice sus cuentas, su porcentaje de la etapa anterior (nulo en la primera, sin dato o con
la anterior en cero) y su porcentaje de la cohorte. Que cada etapa esté dentro de la anterior no se
supone: los controles `embudo.*` lo comprueban cuenta por cuenta al publicar. **Un pago no es una
etapa**: ponerlo al final del embudo de contacto diría que el contacto produjo el pago, y eso es
causalidad, que analytics no prueba. La recuperación se lee al lado, no debajo.

## El golden de las pruebas

Las pruebas comparan lo publicado con lo escrito a mano sobre un escenario sintético de 20 cuentas
(el corte del 2026-09-30), construido como lo haría la operación (`tests/analytics_escenarios.py`):

- 10 cuentas de un producto (visible) y 9 y 1 de otros dos (suprimidos por pequeños, sin necesitar
  complementaria: juntas suman 10); 15 en un municipio y 5 en otro de la misma entidad (el pequeño se
  suprime, y el grande por complementaria).
- Un pago normal, dos observaciones idénticas del mismo pago, un reverso dentro del horizonte, un
  reverso que llega después del `as_of` de un análisis y dentro del de otro (en la misma ventana y en
  la siguiente), un posible reverso que deja a una cuenta en -50, un pago sin cuenta y uno de una
  cuenta de otro corte.
- Gestiones registradas tarde, una anulación que ocurre después de `as_of` y otra que se registra
  después de `known_at`; una asociación única, una ambigua y una sin candidata; promesas `CUMPLIDA`,
  `PARCIAL`, `INCUMPLIDA`, `PENDIENTE`, `CANCELADA` y `NO_EVALUABLE`; una cuenta con tres gestiones,
  dos pagos y dos promesas en un día (si algo se multiplicara, serían 12).
- La cadena `decision/v2` → geografía → `territorial/v2` → `campo/v2` → `ruteo/v2`: 6 candidatas,
  4 asignadas, 1 sin capacidad, 1 en revisión territorial, 2 paradas programadas y 2 sin tiempo.

Al 2026-10-04 con lo que se sabía en la primera frontera: 2,700.00 de recuperación neta (el último
día, -150.00), 15 gestiones de 12 cuentas, 10 contactadas, 7 con promesa y una tasa de cumplimiento
de 3/5. Al 2026-10-08, en la segunda frontera: 2,270.00, porque los dos pagos que anularon los
reversos del 6 dejan de contar en su propio día (el 30 de septiembre y el 3 de octubre).
