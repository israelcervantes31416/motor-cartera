# Las métricas de `analytics-metrics/public-v1`

Cada métrica del catálogo, por familia: su código, su nombre, su unidad y su fórmula, con su
población, su regla temporal, sus entradas, cuándo no está disponible y sus limitaciones. Es la
misma definición que publica `GET /analytics/catalogo` y que firma el hash del catálogo:

`553326f78c2ab17b48c8768af5e6dbb2c039f4b452aad092eb42b4113eacff64`

Lo común a todas está en [analytics.md](analytics.md): los dos relojes, el manifiesto, los estados
de un valor, NULL ≠ 0, las razones y la supresión. Los conjuntos de dimensiones, los periodos y los
embudos, en [cohortes_analytics.md](cohortes_analytics.md).

Reglas de lectura:

- Una **razón** publica su numerador y su denominador, y su valor con 10 decimales; con denominador
  cero, su valor es NULL (estado `DISPONIBLE`, motivo `DENOMINADOR_CERO`), nunca 0 % ni 100 %.
- Una métrica **de estado al corte** o **de un plan de un día** solo tiene vista `ACUMULADA`; en la
  vista `PERIODO` se publica `NO_APLICA`.
- Una métrica **de cuentas distintas** cuenta cada cuenta una vez en su celda y su periodo: no es
  una suma de eventos.
- Ninguna métrica se llama eficiencia, efecto, impacto, éxito ni productividad, y ninguna dice que
  una gestión haya causado un pago.

## Cartera

La foto de la cohorte en el corte: el snapshot de cada cuenta en `fecha_corte`. No cambia con
`as_of` ni con `known_at`, y nunca es `NO_DISPONIBLE` (el corte es la entrada obligatoria).
Conjuntos: `TOTAL`, `PRODUCTO`, `ATRASO_BUCKET`, `PRIORIDAD_DECISION`, `CANAL_DECISION`, `ENTIDAD`,
`MUNICIPIO`, `BASE`, `PRODUCTO_X_ATRASO`, `BASE_X_PRIORIDAD` y `CANAL_X_PRIORIDAD`. Vista:
`ACUMULADA`.

| Código | Nombre | Unidad | Fórmula |
|---|---|---|---|
| `cartera.cuentas` | Cuentas de la cohorte | cuentas | `count(cuentas de la cohorte)` |
| `cartera.saldo_total` | Saldo total inicial | moneda de la fuente | `sum(saldo_total del snapshot en el corte)` |
| `cartera.saldo_total_promedio` | Saldo total promedio por cuenta | moneda de la fuente | `sum(saldo_total) / count(cuentas)` |
| `cartera.dias_atraso_promedio` | Días de atraso promedio | días | `sum(dias_atraso) / count(cuentas)` |
| `cartera.saldo_atrasado_suma` | Saldo atrasado conocido | moneda de la fuente | `sum(saldo_atrasado) de las cuentas con dato` |
| `cartera.saldo_atrasado_cuentas_con_dato` | Cuentas con saldo atrasado conocido | cuentas | `count(cuentas con saldo_atrasado no NULL)` |
| `cartera.saldo_atrasado_cobertura` | Cobertura del saldo atrasado | razón | `count(cuentas con saldo_atrasado no NULL) / count(cuentas)` |

- `cartera.saldo_atrasado_suma` suma solo los valores conocidos: un saldo atrasado vacío no se
  cuenta como cero ni se estima. Su cobertura (`cartera.saldo_atrasado_cobertura`) dice qué parte de
  las cuentas lo trae: con una cuenta sin el dato y otra con 0, la cobertura es 1/2 y la suma es 0.
- Los promedios son razones: guardan su numerador (la suma) y su denominador (las cuentas).

## Recuperación

Los movimientos económicos canónicos de las interpretaciones de `motor-pagos/v1` declaradas,
conciliados con una cuenta de la cohorte por su `cuenta_canonica_id` (nunca por el texto de
`CLIENTE_UNICO`), recibidos desde el inicio de `fecha_corte` hasta el final de `as_of`; en
`PERIODO`, en el día, la semana ISO o el mes de su recepción. Un `PAGO` está anulado si el `REVERSO`
que lo anula está en una interpretación declarada y se recibió antes del final de `as_of`. Entradas:
el corte y `MOTOR_PAGOS`. `NO_DISPONIBLE` si no se declaró ninguna interpretación o si algún día del
periodo no tiene cobertura de pagos conocida a `known_at`. Conjuntos: `TOTAL`, `PRODUCTO`,
`ATRASO_BUCKET`, `PRIORIDAD_DECISION`, `CANAL_DECISION`, `ENTIDAD`, `MUNICIPIO`, `BASE`,
`PRODUCTO_X_ATRASO`, `BASE_X_PRIORIDAD` y `CANAL_X_PRIORIDAD`. Vistas: `ACUMULADA` y `PERIODO`.

| Código | Nombre | Unidad | Fórmula |
|---|---|---|---|
| `recuperacion.neta` | Recuperación económica neta | moneda de la fuente | `sum(PAGO no anulado) + sum(POSIBLE_REVERSO)` |
| `recuperacion.pagos_vigentes` | Pagos vigentes al final de as_of | moneda de la fuente | `sum(PAGO no anulado al final de as_of)` |
| `recuperacion.posibles_reversos` | Posibles reversos | moneda de la fuente | `sum(POSIBLE_REVERSO), siempre <= 0` |
| `recuperacion.movimientos_economicos` | Movimientos económicos | movimientos | `count(movimientos PAGO, REVERSO y POSIBLE_REVERSO)` |
| `recuperacion.pagos_anulados` | Pagos anulados al final de as_of | movimientos | `count(PAGO anulado por un REVERSO recibido antes del final de as_of)` |
| `recuperacion.cuentas_con_neto_positivo` | Cuentas con recuperación neta positiva | cuentas | `count(cuentas cuya recuperacion neta en el periodo es > 0)` |
| `recuperacion.sobre_saldo_inicial` | Recuperación neta sobre saldo inicial | razón | `recuperacion.neta / cartera.saldo_total de las mismas cuentas en el corte` |

- Reutiliza la interpretación económica de `motor-pagos/v1`: un `PAGO` no anulado suma, un
  `POSIBLE_REVERSO` resta, y un `REVERSO` y el pago que anula suman cero juntos y no entran. No es
  la contabilidad del acreedor: el saldo oficial sigue siendo el del snapshot.
- `recuperacion.neta` puede ser negativa (un posible reverso en el horizonte resta) y no se recorta
  a cero.
- `recuperacion.posibles_reversos`: `pagos/v1` no dice cuál es el original de un reverso;
  `motor-pagos/v1` no elige, y su importe resta.
- `recuperacion.movimientos_economicos` cuenta hechos distintos: un duplicado exacto es un solo
  movimiento.
- `recuperacion.cuentas_con_neto_positivo` es por periodo: una cuenta puede ser positiva en un mes y
  no en otro.
- `recuperacion.sobre_saldo_inicial` divide la neta entre el saldo total de esas mismas cuentas en
  el corte; con saldo inicial cero, NULL. No es una tasa de éxito ni una eficiencia: es una razón
  literal.

## Calidad de los pagos

Los movimientos del horizonte que no son recuperación de la cohorte: los que no se conciliaron con
ninguna cuenta y los de cuentas que no están en el corte. Se informan aparte, solo en `TOTAL`, sin
población. Las mismas entradas, ventana y cobertura que la recuperación. Vistas: `ACUMULADA` y
`PERIODO`.

| Código | Nombre | Unidad | Fórmula |
|---|---|---|---|
| `calidad_pagos.movimientos_sin_cuenta` | Movimientos sin cuenta | movimientos | `count(movimientos sin cuenta_canonica_id)` |
| `calidad_pagos.monto_sin_cuenta` | Monto neto sin cuenta | moneda de la fuente | `sum(PAGO no anulado) + sum(POSIBLE_REVERSO) sin cuenta_canonica_id` |
| `calidad_pagos.movimientos_fuera_de_cohorte` | Movimientos de cuentas fuera de la cohorte | movimientos | `count(movimientos de una cuenta que no esta en el corte)` |
| `calidad_pagos.monto_fuera_de_cohorte` | Monto neto de cuentas fuera de la cohorte | moneda de la fuente | `sum(PAGO no anulado) + sum(POSIBLE_REVERSO) de cuentas que no estan en el corte` |

- Un pago sin cuenta se puede conciliar con otra interpretación (`backfill-motor-pagos
  --reconciliar`), que después se declara.

## Gestión

Las gestiones efectivas de la cohorte: ocurridas desde el inicio de `fecha_corte` hasta el final de
`as_of`, registradas hasta `known_at`, y sin una anulación que haya ocurrido antes del final de
`as_of` y se haya registrado hasta `known_at`; en `PERIODO`, en el periodo de su `ocurrido_en`.
Nunca `NO_DISPONIBLE`: el lifecycle es el registro de Motor Cartera, y 0 dice que no se conocía
ninguna a `known_at`. Conjuntos: los de la cohorte y, además, `CANAL_GESTION` y `MEDIO_GESTION` (sin
medio, `SIN_MEDIO`), del universo `GESTION`: una cuenta gestionada por dos canales cuenta en los
dos. Vistas: `ACUMULADA` y `PERIODO`.

| Código | Nombre | Unidad | Fórmula |
|---|---|---|---|
| `gestion.gestiones` | Gestiones | gestiones | `count(gestiones efectivas)` |
| `gestion.cuentas_gestionadas` | Cuentas gestionadas | cuentas | `count(cuentas distintas con al menos una gestion efectiva)` |
| `gestion.cuentas_contactadas` | Cuentas contactadas | cuentas | `count(cuentas distintas con al menos una gestion efectiva con contacto)` |
| `gestion.contactadas_sobre_gestionadas` | Contactadas sobre gestionadas | razón | `gestion.cuentas_contactadas / gestion.cuentas_gestionadas` |
| `gestion.visitas_campo_registradas` | Visitas de campo registradas | visitas | `count(gestiones efectivas con su VisitaCampo)` |

- Contactada: con al menos una gestión efectiva con contacto con el titular o con un tercero
  (`CONTACTO_TITULAR` o `CONTACTO_TERCERO`). Un mensaje de una vía no es contacto.
- `gestion.contactadas_sobre_gestionadas` es contactabilidad descriptiva, no una eficiencia.
- `gestion.visitas_campo_registradas` es lo ejecutado (una gestión de `CAMPO` con su visita), no lo
  planificado: no se compara con `ruteo.programadas` como si fueran lo mismo.

## Promesas

Las promesas creadas (`PROMESA_CREADA`) desde el inicio de `fecha_corte` hasta el final de `as_of`,
registradas hasta `known_at`, de una gestión efectiva en el horizonte; en `PERIODO`, en el periodo
de su creación. Su estado es el de la `EjecucionEvaluacionPromesas` declarada, con el mismo `as_of`
y la misma zona: analytics no reevalúa ninguna promesa. Las creadas, su monto y las cuentas con
promesa nunca son `NO_DISPONIBLE`; los estados y las tasas lo son si no se declaró una evaluación.
Conjuntos: `TOTAL`, `PRODUCTO`, `ATRASO_BUCKET`, `PRIORIDAD_DECISION`, `CANAL_DECISION`, `ENTIDAD`,
`MUNICIPIO`, `BASE`, `PRODUCTO_X_ATRASO`, `BASE_X_PRIORIDAD` y `CANAL_X_PRIORIDAD`. Vistas:
`ACUMULADA` y `PERIODO`.

| Código | Nombre | Unidad | Fórmula |
|---|---|---|---|
| `promesa.creadas` | Promesas creadas | promesas | `count(promesas creadas)` |
| `promesa.monto_prometido` | Monto prometido | moneda de la fuente | `sum(monto_prometido)` |
| `promesa.cuentas_con_promesa` | Cuentas con promesa | cuentas | `count(cuentas distintas con al menos una promesa creada)` |
| `promesa.evaluadas` | Promesas evaluadas | promesas | `count(promesas creadas con su resultado en la evaluacion declarada)` |
| `promesa.cumplidas` | Promesas cumplidas | promesas | `count(promesas en estado CUMPLIDA en la evaluacion declarada)` |
| `promesa.parciales` | Promesas parciales | promesas | `count(promesas en estado PARCIAL en la evaluacion declarada)` |
| `promesa.incumplidas` | Promesas incumplidas | promesas | `count(promesas en estado INCUMPLIDA en la evaluacion declarada)` |
| `promesa.pendientes` | Promesas pendientes | promesas | `count(promesas en estado PENDIENTE en la evaluacion declarada)` |
| `promesa.canceladas` | Promesas canceladas | promesas | `count(promesas en estado CANCELADA en la evaluacion declarada)` |
| `promesa.no_evaluables` | Promesas no evaluables | promesas | `count(promesas en estado NO_EVALUABLE en la evaluacion declarada)` |
| `promesa.sin_evaluacion` | Promesas sin resultado en la evaluación | promesas | `count(promesas creadas sin resultado en la evaluacion declarada)` |
| `promesa.tasa_cumplimiento_total` | Tasa de cumplimiento total | razón | `CUMPLIDA / (CUMPLIDA + PARCIAL + INCUMPLIDA)` |
| `promesa.tasa_cumplimiento_total_o_parcial` | Tasa de cumplimiento total o parcial | razón | `(CUMPLIDA + PARCIAL) / (CUMPLIDA + PARCIAL + INCUMPLIDA)` |

- La tasa de cumplimiento es `CUMPLIDA / (CUMPLIDA + PARCIAL + INCUMPLIDA)`: las `PENDIENTE`,
  `CANCELADA` y `NO_EVALUABLE` no entran en el denominador. Sin promesas resueltas, NULL con
  denominador 0.
- `promesa.sin_evaluacion` cuenta las promesas creadas que no tienen resultado en la evaluación
  declarada: se crearon o se registraron después de ella.
- Que un pago sea compatible con una promesa no dice que la promesa lo produjo.

## Atribución

Los `PAGO` de la cohorte recibidos en el horizonte, con su resultado en la ejecución de
`atribucion/v1` declarada para su mes, que leyó exactamente la interpretación de pagos declarada. Un
monto cuenta solo si el pago no está anulado al final de `as_of`. Entradas: el corte, `MOTOR_PAGOS`
y `ATRIBUCION`. `NO_DISPONIBLE` si falta la atribución declarada de un mes del periodo, o si el
periodo no tiene cobertura de pagos. Conjuntos: `TOTAL`, `PRODUCTO`, `ATRASO_BUCKET`,
`PRIORIDAD_DECISION`, `CANAL_DECISION`, `ENTIDAD`, `MUNICIPIO`, `BASE`, `PRODUCTO_X_ATRASO`,
`BASE_X_PRIORIDAD` y `CANAL_X_PRIORIDAD`. Vistas: `ACUMULADA` y `PERIODO`.

| Código | Nombre | Unidad | Fórmula |
|---|---|---|---|
| `atribucion.movimientos_evaluados` | Pagos con atribución | movimientos | `count(PAGO con resultado en la atribucion declarada)` |
| `atribucion.asociacion_unica` | Pagos con asociación única | movimientos | `count(PAGO clasificados ASOCIACION_UNICA)` |
| `atribucion.ambigua` | Pagos con atribución ambigua | movimientos | `count(PAGO clasificados AMBIGUA)` |
| `atribucion.sin_gestion` | Pagos sin gestión candidata | movimientos | `count(PAGO clasificados SIN_GESTION_CANDIDATA)` |
| `atribucion.monto_asociacion_unica` | Monto con asociación única | moneda de la fuente | `sum(monto de PAGO ASOCIACION_UNICA no anulado al final de as_of)` |
| `atribucion.monto_ambiguo` | Monto con atribución ambigua | moneda de la fuente | `sum(monto de PAGO AMBIGUA no anulado al final de as_of)` |
| `atribucion.monto_sin_gestion` | Monto sin gestión candidata | moneda de la fuente | `sum(monto de PAGO SIN_GESTION_CANDIDATA no anulado al final de as_of)` |

- **Asociación operacional, no causalidad**: una asociación única dice que hubo exactamente una
  gestión con contacto antes del pago en la ventana, no que la gestión lo produjo.
- Una `AMBIGUA` queda ambigua: analytics no elige ninguna candidata ni reparte su monto entre ellas.
- Un `PAGO` anulado se clasifica igual (las gestiones sí lo antecedieron), pero su monto no cuenta.

## Decisión

Las cuentas de la cohorte con su decisión en la `decision/v2` declarada del mismo corte. La
prioridad y el canal son dimensiones del corte (`PRIORIDAD_DECISION`, `CANAL_DECISION`).
`NO_DISPONIBLE` si no se declaró una `decision/v2`. Conjuntos: `TOTAL`, `PRODUCTO`, `ATRASO_BUCKET`,
`PRIORIDAD_DECISION`, `CANAL_DECISION`, `ENTIDAD`, `MUNICIPIO`, `BASE`, `PRODUCTO_X_ATRASO`,
`BASE_X_PRIORIDAD` y `CANAL_X_PRIORIDAD`. Vista: `ACUMULADA`.

| Código | Nombre | Unidad | Fórmula |
|---|---|---|---|
| `decision.cuentas` | Cuentas decididas | cuentas | `count(cuentas de la cohorte con su decision en la decision/v2 declarada)` |
| `decision.pct_universo` | Porcentaje del universo | razón | `decision.cuentas de la celda / cuentas de la cohorte` |

- La mezcla de prioridades y canales es la de una política pública y sintética, no una predicción ni
  una política real de un acreedor.

## Geografía

El estado geográfico de cada cuenta en la geografía declarada del mismo corte. Sin coordenadas: solo
estados. `NO_DISPONIBLE` si no se declaró una geografía. Conjuntos: `TOTAL`, `PRODUCTO`,
`ATRASO_BUCKET`, `PRIORIDAD_DECISION`, `CANAL_DECISION`, `ENTIDAD`, `MUNICIPIO`, `BASE`,
`PRODUCTO_X_ATRASO`, `BASE_X_PRIORIDAD` y `CANAL_X_PRIORIDAD`. Vista: `ACUMULADA`.

| Código | Nombre | Unidad | Fórmula |
|---|---|---|---|
| `geografia.cuentas` | Cuentas con geografía | cuentas | `count(cuentas con resultado en la geografia declarada)` |
| `geografia.validas` | Geografía válida | cuentas | `count(cuentas con estado_geografia VALIDA)` |
| `geografia.discrepancias` | Discrepancia de municipio | cuentas | `count(cuentas con estado_geografia DISCREPANCIA_MUNICIPIO)` |
| `geografia.sin_coordenadas` | Sin coordenadas | cuentas | `count(cuentas con estado_geografia SIN_COORDENADAS)` |
| `geografia.incompletas` | Coordenadas incompletas | cuentas | `count(cuentas con estado_geografia COORDENADAS_INCOMPLETAS)` |
| `geografia.invalidas` | Coordenadas inválidas | cuentas | `count(cuentas con estado_geografia COORDENADAS_INVALIDAS)` |
| `geografia.fuera_referencia` | Fuera de la referencia | cuentas | `count(cuentas con estado_geografia FUERA_DE_REFERENCIA)` |
| `geografia.ambiguas` | Municipio ambiguo | cuentas | `count(cuentas con estado_geografia MUNICIPIO_AMBIGUO)` |
| `geografia.tasa_valida` | Tasa de geografía válida | razón | `geografia.validas / geografia.cuentas` |

- Los estados cuentan; no se corrige ni se reemplaza ninguna coordenada.

## Territorial

El estado de la asignación de cada cuenta en la `territorial/v2` declarada del mismo corte, sobre la
geografía declarada. La base es una dimensión del corte (`BASE`; sin base, `SIN_BASE`).
`NO_DISPONIBLE` si no se declaró una `territorial/v2`. Conjuntos: `TOTAL`, `PRODUCTO`,
`ATRASO_BUCKET`, `PRIORIDAD_DECISION`, `CANAL_DECISION`, `ENTIDAD`, `MUNICIPIO`, `BASE`,
`PRODUCTO_X_ATRASO`, `BASE_X_PRIORIDAD` y `CANAL_X_PRIORIDAD`. Vista: `ACUMULADA`.

| Código | Nombre | Unidad | Fórmula |
|---|---|---|---|
| `territorial.cuentas` | Cuentas con asignación territorial | cuentas | `count(cuentas con resultado en la territorial/v2 declarada)` |
| `territorial.asignadas` | Asignadas a una base | cuentas | `count(cuentas con estado_asignacion ASIGNADA)` |
| `territorial.revision` | En revisión geográfica | cuentas | `count(cuentas con estado_asignacion REVISION_GEOGRAFICA)` |
| `territorial.sin_base` | Sin base elegible | cuentas | `count(cuentas con estado_asignacion SIN_BASE_ELEGIBLE)` |
| `territorial.ambiguas` | Empate entre bases | cuentas | `count(cuentas con estado_asignacion AMBIGUA_BASE)` |
| `territorial.fuera_cobertura` | Fuera de cobertura | cuentas | `count(cuentas con estado_asignacion FUERA_DE_COBERTURA)` |
| `territorial.tasa_asignacion` | Tasa de asignación territorial | razón | `territorial.asignadas / territorial.cuentas` |

- Es pertenencia territorial, no capacidad ni ruta.

## Campo

Las candidatas de campo del plan de `campo/v2` declarado (universo `CAMPO`: una vez cada una), de
una sola fecha de operación que cae entre `fecha_corte` y `as_of`, y la capacidad de los gestores
con jornada ese día. **Planificado, no ejecutado.** `NO_DISPONIBLE` si no se declaró el plan.
Conjuntos: `TOTAL`, `PRIORIDAD_DECISION`, `BASE`, `BASE_X_PRIORIDAD`, `ESTADO_CAMPO` y
`GESTOR_CAMPO` (base y código del gestor de campo). Vista: `ACUMULADA`.

| Código | Nombre | Unidad | Fórmula |
|---|---|---|---|
| `campo.candidatas` | Candidatas de campo | cuentas | `count(candidatas del plan)` |
| `campo.asignadas` | Asignadas a un gestor | cuentas | `count(candidatas ASIGNADA)` |
| `campo.revision_territorial` | En revisión territorial | cuentas | `count(candidatas REVISION_TERRITORIAL)` |
| `campo.sin_gestor` | Sin gestor disponible | cuentas | `count(candidatas SIN_GESTOR_DISPONIBLE)` |
| `campo.sin_capacidad` | Sin capacidad | cuentas | `count(candidatas SIN_CAPACIDAD)` |
| `campo.rebalanceadas` | Asignadas por spillover | cuentas | `count(candidatas ASIGNADA a otro gestor que el propietario preferido)` |
| `campo.capacidad_total` | Capacidad declarada del día | cupos | `sum(capacidad efectiva de cada gestor de la configuracion en la fecha)` |
| `campo.capacidad_utilizada` | Capacidad utilizada | cupos | `count(candidatas ASIGNADA): una cuenta es un cupo` |
| `campo.utilizacion` | Utilización de la capacidad | razón | `campo.capacidad_utilizada / campo.capacidad_total` |
| `campo.tasa_asignacion` | Tasa de asignación de campo | razón | `campo.asignadas / campo.candidatas` |

- Un gestor de campo es un código de la configuración de campo, no el `actor_ref` de una gestión: no
  se cruzan.
- La capacidad es un cupo declarado (una cuenta, un cupo), no un tiempo ni una factibilidad vial.

## Ruteo

Las paradas (universo `RUTEO`: una por cuenta `ASIGNADA`) y las rutas (una por gestor con cuentas)
de la ejecución de `ruteo/v2` declarada, del plan declarado. **Una parada programada no es una
visita realizada.** `NO_DISPONIBLE` si no se declaró el ruteo. Conjuntos: `TOTAL`, `BASE`,
`GESTOR_CAMPO` y, en las paradas, `PRIORIDAD_DECISION` y `ESTADO_RUTEO`. Vista: `ACUMULADA`.

| Código | Nombre | Unidad | Fórmula |
|---|---|---|---|
| `ruteo.rutas` | Rutas | rutas | `count(rutas)` |
| `ruteo.rutas_completas` | Rutas completas | rutas | `count(rutas COMPLETA)` |
| `ruteo.rutas_parciales` | Rutas parciales | rutas | `count(rutas PARCIAL)` |
| `ruteo.rutas_sin_paradas` | Rutas sin paradas enrutables | rutas | `count(rutas SIN_PARADAS_ENRUTABLES)` |
| `ruteo.bases_no_enrutables` | Rutas con la base no enrutable | rutas | `count(rutas BASE_NO_ENRUTABLE)` |
| `ruteo.rutas_sin_jornada` | Rutas sin jornada | rutas | `count(rutas SIN_JORNADA)` |
| `ruteo.paradas_objetivo` | Paradas objetivo | paradas | `count(paradas): una por cuenta ASIGNADA del plan` |
| `ruteo.programadas` | Paradas programadas | paradas | `count(paradas PROGRAMADA)` |
| `ruteo.sin_tiempo` | Paradas sin tiempo | paradas | `count(paradas SIN_TIEMPO)` |
| `ruteo.no_enrutables` | Paradas no enrutables | paradas | `count(paradas NO_ENRUTABLE)` |
| `ruteo.no_enrutables_base` | Paradas de una base no enrutable | paradas | `count(paradas NO_ENRUTABLE_BASE)` |
| `ruteo.tasa_programacion` | Programadas sobre objetivo | razón | `ruteo.programadas / ruteo.paradas_objetivo` |
| `ruteo.distancia_total_m` | Distancia total por calle | metros | `sum(distancia_total_m de cada ruta, con el regreso a la base)` |
| `ruteo.duracion_vial_total_s` | Duración de traslado | segundos | `sum(duracion_traslado_s de cada ruta)` |
| `ruteo.duracion_servicio_total_s` | Duración de servicio | segundos | `sum(duracion_servicio_s de cada ruta)` |
| `ruteo.duracion_espera_total_s` | Duración de espera | segundos | `sum(duracion_espera_s de cada ruta)` |

- Distancias por calle y duraciones planificadas, sin tráfico; el servicio es una hipótesis fija por
  visita, no una medición.
- Ninguna coordenada, geometría ni polilínea.
