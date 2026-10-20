# Collection Analytics (`analytics/v1`)

`analytics/v1` describe una **cohorte** de cuentas, agregada y **point-in-time**: las cuentas de un
corte canónico, desde el inicio de su `fecha_corte` hasta el final de `as_of` en una zona horaria
explícita, con lo que se conocía en `known_at`. Responde preguntas como «en esta cohorte había
500,000 cuentas y X de saldo inicial; hasta el `as_of`, con lo que se sabía hasta `known_at`, se
recuperaron Y netos, Z cuentas tuvieron recuperación positiva, A fueron gestionadas, B contactadas,
C prometieron, y el plan de campo tenía D candidatas y E paradas programadas».

**Describe; no prueba causalidad, no predice y no recomienda.** Una asociación de la atribución es
operacional, no causal: que un pago llegue después de una gestión no dice que la gestión lo haya
producido. Una parada programada es un plan, no una visita realizada, y un gestor de campo no es el
`actor_ref` de una gestión. `analytics/v1` no calcula probabilidades, modelos ni planes (son de
`predictive/model-v1` y `optimization/v1`), y no dibuja: la app muestra sus agregados tal como se
publicaron.

Las métricas, con su fórmula, su numerador y su denominador, están en
[metricas_analytics.md](metricas_analytics.md); la cohorte, los conjuntos de dimensiones, los
periodos y los embudos, en [cohortes_analytics.md](cohortes_analytics.md); el porqué, en las
secciones 175 a 196 de [decisiones.md](decisiones.md).

## Las versiones

| Qué | Versión | Hash |
|---|---|---|
| El motor | `analytics/v1` | — |
| El catálogo de métricas | `analytics-metrics/public-v1` | `553326f78c2ab17b48c8768af5e6dbb2c039f4b452aad092eb42b4113eacff64` |
| La política de publicación | `analytics-public-v1` | `3529b0893889bf8b28491c7fd2671cd3961b83915ba83084146585351fcb8a80` |

El catálogo es código versionado (`analytics.catalogo`): cada métrica con su código, su nombre, su
descripción, su familia, su unidad, su fórmula, su numerador y su denominador, su población, su
regla temporal, sus entradas requeridas, cuándo es nula, si es aditiva, sus conjuntos de dimensiones,
sus vistas y sus limitaciones. Su hash es el SHA-256 de esa definición en JSON canónico (llaves en
orden, sin espacios, UTF-8): cambiar una fórmula cambia el hash, y una ejecución abierta con otro
hash no se analiza (`VERSION_NO_SOPORTADA`). La política fija el mínimo de cuentas por celda (10),
los conjuntos que se publican también por periodo, el máximo de periodos (366) y los decimales de
una razón (10).

## Los dos relojes

- **`as_of`** es hasta qué día de negocio se describe: lo que *ocurrió* hasta el final de ese día
  en la zona declarada.
- **`known_at`** es hasta qué instante se podía *conocer* la información: lo que se *registró*, se
  *recibió* o *terminó* hasta entonces.

No se sustituyen. Un hecho existe para una ejecución si ocurrió en el horizonte **y** se conocía a
`known_at`. Una gestión del 2 de octubre registrada el 13 no existe para un análisis con
`known_at` del 10, aunque haya ocurrido antes de su `as_of`.

## La cohorte y la ventana

La cohorte es **el corte canónico declarado**: sus snapshots, una vez cada cuenta, con las
dimensiones que tenían en el corte (producto, saldo inicial, días de atraso y su tramo, entidad y
municipio de la fuente) y, si se declaran ejecuciones del mismo corte, su prioridad y su canal de
`decision/v2`, su base de `territorial/v2` y su estado de la geografía. Nunca «las cuentas que siguen
al final»: eso sería sesgo de supervivencia. Si una cuenta era `ALTA` en el corte y después cambió,
la cohorte `ALTA` histórica no se reescribe.

La ventana es **`[fecha_corte, fin de as_of]`** en la zona declarada: desde el inicio del día del
corte hasta el inicio del día siguiente a `as_of`. `as_of` no puede ser anterior al corte
(`AS_OF_ANTERIOR_AL_CORTE`) ni posterior al día de `known_at` en la zona (`AS_OF_FUTURO`); si es el
mismo día, la ejecución lo advierte (`HORIZONTE_EN_CURSO`): lo que ocurra después de `known_at` ese
día no está. `horizonte_dias = as_of - fecha_corte + 1`.

## Los modos

| Modo | `known_at` | Para qué |
|---|---|---|
| `OPERATIVO` | Opcional. Si no se declara, el reloj de **la base** (`now()`), tomado una sola vez al abrir la ejecución y guardado. | Describir con lo que se sabe hoy. |
| `BACKTEST` | Obligatorio (`KNOWN_AT_REQUERIDO`). | Describir el pasado con lo que se sabía entonces. |

Nunca se usa el reloj local de Python como frontera. Un `known_at` sin zona horaria se rechaza
(`KNOWN_AT_SIN_ZONA`) y uno posterior al reloj de la base también (`KNOWN_AT_FUTURO`). El modo no
entra en la firma: no cambia nada de lo que se calcula.

## La zona horaria

`zona_horaria` es obligatoria y es una zona IANA que conozca PostgreSQL (`ZONA_HORARIA_INVALIDA`).
Con ella se calculan el final de `as_of`, el inicio de `fecha_corte`, los periodos y el día de cada
gestión, cada promesa y cada movimiento (las recepciones de pagos son horas locales de la fuente).
Nada depende de la zona del sistema operativo ni de Docker.

## La granularidad

| Granularidad | Periodos |
|---|---|
| `TOTAL` | Ninguno: el horizonte entero es la vista `ACUMULADA`. |
| `DIA` | Cada día calendario. Ninguno es parcial. |
| `SEMANA_ISO` | De lunes a domingo; la primera y la última se recortan al horizonte y van `periodo_parcial = true`. |
| `MES` | El mes calendario, recortado igual. |

Un horizonte de más de 366 periodos no se abre (`HORIZONTE_DEMASIADO_LARGO`).

## El manifiesto: cada entrada se declara

Una ejecución declara **cada entrada por su identificador público**. Nada se elige como «la última
interpretación», «la última atribución», «la última evaluación», «la última decisión» o «el último
plan». Lo que no se declara no se lee, y sus métricas se publican `NO_DISPONIBLE`, nunca cero.

| Entrada | Qué es | Cuándo es válida |
|---|---|---|
| `CORTE` | El corte canónico de la cohorte (obligatorio). | Se materializó hasta `known_at` (`creado_en`). |
| `MOTOR_PAGOS` | Una interpretación de `motor-pagos/v1` por cada mes que se quiera describir. | `EXITOSA`, de la cartera, terminada hasta `known_at`, su ventana toca el horizonte, **una por ventana**. |
| `ATRIBUCION` | La `atribucion/v1` de un mes. | `EXITOSA`, terminada hasta `known_at`, y leyó exactamente la interpretación declarada de su mes. |
| `EVALUACION_PROMESAS` | Una evaluación de promesas. | `EXITOSA`, con el mismo `as_of` y la misma zona que la ejecución. |
| `DECISION_V2` | Una `decision/v2`. | `EXITOSA`, del mismo corte, terminada hasta `known_at`. |
| `GEOGRAFIA` | Una ejecución de la geografía. | Igual. |
| `TERRITORIAL_V2` | Una `territorial/v2`. | Igual, y de la geografía declarada. |
| `CAMPO_V2` | Un plan de `campo/v2`. | Igual, de la decisión y la territorial declaradas, con su fecha de operación en el horizonte. |
| `RUTEO_V2` | Una ejecución de `ruteo/v2`. | `EXITOSA`, terminada hasta `known_at`, del plan declarado. |

Antes de registrar nada, el manifiesto se comprueba, *fail-closed*. Lo que no se puede abrir no
deja ni una fila y responde con un código estable:

| Código | HTTP | Cuándo |
|---|---|---|
| `CORTE_NO_ENCONTRADO` | 404 | No existe el corte. |
| `ENTRADA_NO_ENCONTRADA` | 404 | No existe una entrada declarada. |
| `AS_OF_ANTERIOR_AL_CORTE` | 422 | `as_of` es anterior a la fecha del corte. |
| `AS_OF_FUTURO` | 422 | `as_of` es posterior al día de `known_at` en la zona. |
| `ZONA_HORARIA_INVALIDA` | 422 | La zona no es una zona IANA que conozca la base. |
| `KNOWN_AT_REQUERIDO` | 422 | Un `BACKTEST` sin `known_at`. |
| `KNOWN_AT_SIN_ZONA` | 422 | `known_at` sin su zona horaria. |
| `KNOWN_AT_FUTURO` | 422 | `known_at` posterior al reloj de la base. |
| `CORTE_NO_CONOCIDO` | 422 | El corte se materializó después de `known_at`. |
| `HORIZONTE_DEMASIADO_LARGO` | 422 | Más periodos de los que admite la política. |
| `CATALOGO_NO_SOPORTADO` | 422 | Otra versión del catálogo. |
| `POLITICA_NO_SOPORTADA` | 422 | Otra versión de la política. |
| `ENTRADA_NO_EXITOSA` | 422 | Una entrada no terminó `EXITOSA`: nunca se lee una a medias. |
| `ENTRADA_NO_CONOCIDA` | 422 | Una entrada terminó después de `known_at`: a esa frontera no existía. |
| `VERSION_DE_ENTRADA_NO_SOPORTADA` | 422 | Una entrada es de una versión que `analytics/v1` no lee. |
| `CARTERA_INCONSISTENTE` | 422 | Una entrada es de otra cartera. |
| `CORTE_INCONSISTENTE` | 422 | Una entrada es de otro corte. |
| `VENTANA_FUERA_DEL_HORIZONTE` | 422 | Una interpretación de pagos de un mes que no toca el horizonte. |
| `VENTANA_DUPLICADA` | 422 | Dos interpretaciones (o dos atribuciones) de la misma ventana: sus movimientos contarían dos veces. |
| `ATRIBUCION_INCONSISTENTE` | 422 | Una atribución que no leyó la interpretación declarada de su mes. |
| `EVALUACION_INCONSISTENTE` | 422 | Una evaluación de otro `as_of` o de otra zona. |
| `ENCADENAMIENTO_INCORRECTO` | 422 | Una territorial sin su geografía, un plan sin su decisión o su territorial, un ruteo sin su plan. |
| `FECHA_OPERACION_FUERA_DEL_HORIZONTE` | 422 | El plan de campo es de un día fuera del horizonte. |
| `ENTRADA_REPETIDA` | 422 | Una entrada declarada dos veces. |

Las entradas se ordenan en forma canónica y su SHA-256 es el **hash del manifiesto**: el mismo
conjunto lógico de entradas, en cualquier orden, da el mismo hash. La **firma** (`firma_inputs`) es
el SHA-256 del motor, el catálogo y la política con sus hashes, la cartera, el corte, `as_of`,
`known_at` (en UTC, al microsegundo), la zona, la granularidad y el hash del manifiesto. Las mismas
entradas son la misma ejecución: una sola `EXITOSA` y una sola `EN_PROCESO` por firma (409
`ANALYTICS_YA_GENERADA` o `ANALYTICS_EN_PROCESO`); los intentos `FALLIDA` se conservan todos.

## Disponibilidad: `NO_DISPONIBLE` no es cero

Una métrica es `NO_DISPONIBLE` en un periodo, sin valor, con su motivo:

| Código | Cuándo |
|---|---|
| `ENTRADA_NO_DECLARADA` | Falta una entrada que la métrica necesita; el motivo dice cuáles. |
| `PAGOS_SIN_COBERTURA` | Algún día del periodo no cae entre el primer y el último movimiento de ningún archivo de pagos conocido a `known_at`. |
| `INTERPRETACION_INCOMPLETA` | La interpretación declarada de su mes no clasificó cada pago observado conocido de ese mes (o el mes no tiene interpretación declarada). |
| `ATRIBUCION_NO_DECLARADA` | El mes tiene interpretación declarada pero no su atribución. |

Cada motivo de cobertura dice cuántos días y el primero y el último de ellos. `pagos/v1` no declara
el periodo que cubre un archivo: la evidencia es lo que trae, así que un día cubierto es el que cae
entre el primer y el último movimiento de algún archivo cuya historia terminó hasta `known_at`. Es
conservador: un hueco entre dos archivos, o el primer día de un archivo que no trajo pagos ese día,
quedan `NO_DISPONIBLE`. La ejecución guarda en `disponibilidad` el estado de cada familia
(`DISPONIBLE`, `PARCIAL` o `NO_DISPONIBLE`), las entradas que faltan, los conjuntos que no se
pudieron formar, la cobertura de pagos por mes (observados, clasificados, interpretado, atribuido),
los días con cobertura y cuántas celdas se suprimieron.

## Lo point-in-time, dominio por dominio

**Pagos.** La recuperación sale **solo de `MovimientoEconomicoCanonico`** de las interpretaciones
declaradas, nunca de `PagoObservado` (que es evidencia de fuente). Se reutiliza la semántica
económica de `motor-pagos/v1`, evaluada al final de `as_of`: un `PAGO` no anulado suma, un
`POSIBLE_REVERSO` resta, y un `REVERSO` y el pago que anula suman cero juntos y no entran. Un `PAGO`
está anulado **solo si el `REVERSO` que lo anula está en una interpretación declarada y se recibió
antes del final de `as_of`**: la marca `anulado_por_movimiento_id` de la interpretación vigente no se
usa a ciegas, porque puede contener el futuro. Un pago del 10 de enero cuyo reverso llega el 5 de
febrero cuenta en un análisis al 31 de enero; en uno al 28 de febrero, el par suma cero y el pago
deja de contar **en su propio periodo** (el 10 de enero): la serie de un análisis posterior puede
corregir un periodo anterior, y cada análisis dice la verdad de su `as_of`. La recuperación neta
puede ser negativa (un posible reverso sin su pago en el horizonte) y no se recorta a cero. Un
movimiento sin cuenta, o de una cuenta que no está en el corte, no es recuperación de la cohorte: se
cuenta aparte, en `calidad_pagos`.

**Lifecycle.** Una gestión cuenta si ocurrió en el horizonte, **se registró hasta `known_at`** y no
tiene una anulación que **haya ocurrido antes del final de `as_of` y se haya registrado hasta
`known_at`**. Así:

| Caso | La gestión ocurre | La anulación | Resultado |
|---|---|---|---|
| A | antes de `as_of` | ocurre después de `as_of` | La gestión sigue efectiva en ese análisis. |
| B | antes de `as_of` | ocurre antes de `as_of`, se registra después de `known_at` | Sigue efectiva en el backtest anterior. |
| C | antes de `as_of` | ocurre antes de `as_of` y se registra hasta `known_at` | Anulada. |

Una gestión registrada tarde (después de `known_at`) no existe para ese análisis, y sí para uno con
un `known_at` posterior. A diferencia de `decision/v2`, que solo mira si la anulación se registró,
aquí también importa cuándo ocurrió: una anulación futura no borra retrospectivamente una gestión de
un análisis anterior (decisión 183).

**Promesas.** Una promesa cuenta si la creó una gestión efectiva en el horizonte y se registró hasta
`known_at`. Su estado (`CUMPLIDA`, `PARCIAL`, `INCUMPLIDA`, `PENDIENTE`, `CANCELADA` o
`NO_EVALUABLE`) es **el de la evaluación declarada**, con el mismo `as_of` y la misma zona:
`analytics/v1` no reevalúa ninguna promesa. Una promesa creada después de esa evaluación queda sin
resultado (`promesa.sin_evaluacion`).

**Atribución.** Las clases (`ASOCIACION_UNICA`, `AMBIGUA`, `SIN_GESTION_CANDIDATA`) son las de la
atribución declarada de cada mes. Un pago `AMBIGUA` queda ambiguo: **analytics no elige ninguna
candidata ni reparte su monto**. El monto de una clase cuenta solo si el pago sigue vigente al final
de `as_of`. Es **asociación operacional, no causalidad**: ninguna métrica se llama «pagos causados»,
«recuperación generada» ni «impacto».

**Operación.** Los estados de la geografía y de `territorial/v2` y la prioridad y el canal de
`decision/v2` son dimensiones del corte. Las métricas de campo y de ruteo son del plan declarado de
una sola fecha de operación: **planificado, no ejecutado**. `ruteo.programadas` cuenta paradas
programadas; `gestion.visitas_campo_registradas` cuenta visitas registradas en el lifecycle, y una
no se convierte en la otra. **Un gestor de campo no es el `actor_ref` de una gestión**: no se
cruzan, y `actor_ref` no es una dimensión.

## Los valores: estados, NULL, razones y dinero

Cada fila publicada es una métrica en una celda y un periodo, con su estado:

| Estado | Valor | Cuándo |
|---|---|---|
| `DISPONIBLE` | El valor (y en una razón, su numerador y su denominador). | Se pudo calcular. |
| `NO_DISPONIBLE` | Ninguno; el motivo dice por qué. | Falta una entrada o la cobertura de su periodo. |
| `SUPRIMIDO` | Ninguno: ni valor, ni numerador, ni denominador, ni población. | La celda tiene menos de 10 cuentas, o dejaría reconstruir una que sí. |
| `NO_APLICA` | Ninguno. | Una métrica de estado al corte o de un plan de un día no tiene periodo: en la vista `PERIODO` se publica una fila `NO_APLICA` por periodo, en `TOTAL`. |

- **NULL no es cero.** Un saldo atrasado vacío no se suma como 0: se cuenta aparte la cobertura del
  dato (`cartera.saldo_atrasado_cobertura`). Una métrica sin su entrada es `NO_DISPONIBLE`, no 0.
- **Una razón guarda su numerador y su denominador.** Con denominador cero, su valor es NULL, su
  estado `DISPONIBLE` y su motivo `DENOMINADOR_CERO`: nunca 0 % ni 100 %. Un valor publicado tiene
  10 decimales (`round(numerador / denominador, 10)`).
- **Decimal, no float.** Todo se calcula en `NUMERIC` y se publica como decimal exacto; la API lo
  serializa como texto.
- **La moneda es la de la fuente.** Los montos están en la moneda de los importes de cartera/v2 y
  pagos/v1; no se convierte ni se inventa otra, y no hay multimoneda.

## Privacidad: celdas pequeñas, sin datos de una cuenta

`analytics-public-v1` suprime por **celda** (no por métrica ni por periodo):

- **Primaria**: una celda de un desglose distinto de `TOTAL` con menos de 10 cuentas distintas de
  su universo se publica `SUPRIMIDO` (`CELDA_MENOR_AL_MINIMO`).
- **Complementaria**: cada grupo de celdas es una ecuación, su total es la suma de sus celdas (el
  conjunto contra el `TOTAL`, `MUNICIPIO` dentro de su `ENTIDAD`, un cruce dentro de cada uno de
  sus marginales, `GESTOR_CAMPO` dentro de su `BASE`), y ninguna puede quedar con una sola
  incógnita:
  - con el total visible, las suprimidas son ninguna, o dos o más que juntas tienen al menos 10
    cuentas. Si no, se suprime también la visible con menos cuentas (`SUPRESION_COMPLEMENTARIA`,
    desempate por `dimension_hash`); y si ya no queda ninguna visible (la suprimida es la única
    celda de su total), se suprime el total: un padre visible revela a su única celda;
  - con el total suprimido, alguna de sus celdas tiene que estarlo: si todas se ven, sumarlas lo
    revela;
  - para proteger a otra se prefiere una celda que no sea la única de algún grupo: a esa la revela
    su total, y suprimirla no protege a nadie.

  Se repite hasta que todos los grupos cumplan.
- La misma celda se suprime en todas sus métricas y en todos sus periodos: una serie no reconstruye
  lo que el acumulado oculta. **`TOTAL` no se suprime nunca.** Con estas reglas ninguna ecuación de
  un grupo deja deducir una celda suprimida; no es una garantía contra cualquier combinación de
  sumas y restas entre conjuntos distintos.

Ninguna tabla, respuesta, exportación ni bitácora lleva un `CLIENTE_UNICO`, un `cuenta_id`, un
nombre, una dirección, un teléfono, un `actor_ref`, una coordenada, un tramo, un microtramo ni una
polilínea. No hay analytics por cuenta (la Cuenta 360 no cambia), ni «top deudores», ni rankings de
cobradores. La bitácora y los controles dicen códigos, conteos, diferencias e identificadores de
ejecuciones.

## La ejecución: un trabajo durable `ANALYTICS`

El `POST` (o `analizar-cobranza`) no analiza: resuelve y comprueba el manifiesto, fija `known_at`,
calcula la firma y registra la ejecución `EN_PROCESO`, sus entradas y su trabajo `ANALYTICS` en la
misma transacción (nivel 8 de la cola, después de `RUTEO_V2`). Un worker la toma (`FOR UPDATE SKIP
LOCKED`, con lease, latido y reintentos), **espera a que la frontera de `known_at` se asiente** (que
nada registrado antes de `known_at` siga sin confirmarse) y la analiza con su fila bloqueada, en una
transacción y en un `SAVEPOINT`, por conjuntos en PostgreSQL, en estas fases:

1. **La cohorte** (`an_cohorte`): los snapshots del corte con sus dimensiones, unidos por llave
   primaria a la decisión, la territorial y la geografía declaradas. Si una cuenta no cae en un
   tramo de atraso o no tiene resultado en una ejecución declarada del corte,
   `DATOS_INCONSISTENTES`.
2. **Los pagos** (`an_mov`) y su cobertura por día.
3. **El lifecycle** (`an_gestion`, `an_promesa`) y **la atribución** (`an_atrib`), point-in-time.
4. **La operación** (`an_campo`, `an_carga`, `an_parada`, `an_ruta`), si se declaró.
5. **Los hechos por cuenta y periodo** (`an_pc`, `an_ac`): cada dominio agregado aparte a (cuenta,
   periodo) y sumados con `UNION ALL`, sin unir un dominio con otro. Una cuenta con tres gestiones,
   dos pagos y dos promesas no multiplica nada.
6. **Las celdas** (`an_valor`): de los hechos a cada conjunto de dimensiones con `GROUPING SETS`, unos
   pocos recorridos para todos los conjuntos.
7. **Los controles**, sobre los agregados antes de suprimir nada.
8. **La supresión**, en Python sobre las celdas (que son pocas), y **la publicación**: un
   `INSERT ... SELECT` de cada métrica por celda y periodo, las filas `NO_APLICA`, los controles por
   `COPY`, el hash de lo publicado y el cierre `EXITOSA`.

Ninguna tabla tiene una fila por cuenta y día: las tablas de paso son temporales de la transacción
(`ON COMMIT DROP`). Todo o nada: si algo falla en cualquier punto (`ANTES_DE_COHORTE`,
`DESPUES_DE_COHORTE`, `DESPUES_DE_PAGOS`, `DESPUES_DE_LIFECYCLE`, `DESPUES_DE_AGREGADOS`,
`DESPUES_DE_CONTROLES`, `ANTES_DE_PUBLICAR`, `ANTES_DE_CERRAR`), el `SAVEPOINT` se revierte y no
queda ni un agregado ni un control. Solo publica o falla el dueño vigente de su trabajo: un worker
que perdió su lease no publica.

| Resultado | Estado | Cuándo |
|---|---|---|
| `ANALYTICS_PUBLICADA` | `EXITOSA` | Cada agregado y cada control quedaron publicados, todos los controles OK. |
| `CONTROL_FALLIDO` | `FALLIDA` | Una reconciliación no cuadró: no se publica ningún agregado; sus controles se guardan, y el detalle nombra cada uno. |
| `DATOS_INCONSISTENTES` | `FALLIDA` | La cohorte no cuadra con las ejecuciones declaradas del corte. |
| `ENTRADAS_NO_EXITOSAS` | `FALLIDA` | Una entrada declarada dejó de estar `EXITOSA`. |
| `VERSION_NO_SOPORTADA` | `FALLIDA` | El motor, el catálogo o la política de la ejecución no son los que aplica este servicio. |
| `YA_GENERADA` | `FALLIDA` | Otra ejecución publicó la misma firma primero. |
| `ERROR_INTERNO` | `FALLIDA` | Un error inesperado: en la bitácora, su tipo y sus marcos, nunca su mensaje. |
| `INTENTOS_AGOTADOS` | `FALLIDA` | La cola agotó los intentos de su trabajo. |
| `CERRADA_AL_BAJAR` | `FALLIDA` | Estaba `EN_PROCESO` cuando la base bajó de la 0015 a la 0014. |

Mientras se resuelve, la ejecución está `EN_PROCESO`, sin resultado. Una ejecución terminada no
cambia ni se borra, y sus entradas, sus agregados y sus controles no se reescriben (triggers por
sentencia). Reconstruirla con el mismo manifiesto da exactamente lo mismo: los mismos agregados, los
mismos controles y el mismo `hash_agregados` (el SHA-256 de todo lo publicado en orden canónico),
sin importar el orden físico de las filas ni en qué orden se declaren las entradas.

## Los controles

Cada control compara lo esperado con lo observado, **exacto**: en conteos y en centavos, sin
tolerancia y sin floats. Se calculan antes de suprimir nada y se guardan con la ejecución. Si uno no
cuadra, la ejecución queda `FALLIDA` (`CONTROL_FALLIDO`) sin publicar un solo agregado.

| Familia | Qué comprueba |
|---|---|
| Lectura | La cohorte son las cuentas del corte, una vez (`cohorte.cuentas`, `cohorte.cuentas_sin_duplicar`); ningún movimiento ni atribución se leyó dos veces (`lectura.movimientos_sin_duplicar`, `lectura.atribuciones_sin_duplicar`), y lo que suman los hechos por cuenta y periodo es lo que se leyó de cada dominio (`lectura.movimientos_sin_multiplicar`, `lectura.gestiones_sin_multiplicar`, `lectura.promesas_sin_multiplicar`, `lectura.atribuciones_sin_multiplicar`). |
| Cartera | Cada conjunto excluyente suma su `TOTAL` en cuentas y en saldo (`cartera.cuentas.por_<conjunto>`, `cartera.saldo_total.por_<conjunto>`). |
| Pagos | La neta de cada conjunto suma la del `TOTAL` y la de los periodos la acumulada, en centavos; la neta es pagos vigentes más posibles reversos (`recuperacion.neta_es_pagos_mas_posibles_reversos`); los movimientos del horizonte son los de la cohorte, los sin cuenta y los de otras cuentas (`pagos.universo_de_movimientos`); y las interpretaciones de meses vecinos se corresponden (`pagos.reversos_coherentes`). |
| Gestión y promesas | Los canales y los medios suman las gestiones del `TOTAL`; los periodos, la acumulada; los seis estados de la evaluación suman las evaluadas, y las evaluadas más las sin resultado, las creadas. |
| Atribución | Las tres clases suman lo evaluado (`atribucion.clases_suman_evaluados`), cada `PAGO` de los meses atribuidos tiene su resultado (`atribucion.cubre_los_pagos`) y los montos de las clases suman los pagos vigentes, en centavos (`atribucion.montos_suman_pagos_vigentes`). |
| Decisión, geografía y territorial | Las cuentas decididas son la cohorte, y cada estado cuadra con lo que publicó su ejecución (`<familia>.<componente>.contra_la_ejecucion`). |
| Campo | Los estados suman las candidatas, cada conjunto suma su `TOTAL` en candidatas y en capacidad, y cada conteo cuadra con el plan. |
| Ruteo | Los estados suman las paradas y las rutas, cada conjunto suma su `TOTAL` en paradas, rutas y distancia, y cada conteo y total cuadra con la ejecución. |
| Embudos | Las cuentas con promesa están dentro de las contactadas y estas dentro de las gestionadas (`embudo.contacto.*`); cada parada es de una cuenta `ASIGNADA` del plan (`embudo.campo.paradas_dentro_de_asignadas`). |
| Publicación y privacidad | Se publicaron exactamente las filas esperadas (`publicacion.filas`) y ninguna celda visible rompe la supresión (`privacidad.supresion`). |

## Lo que se publica

- **`ejecucion_analytics`**: el corte, `as_of`, `known_at`, el modo, la zona, la granularidad, el
  horizonte, las versiones y los hashes del motor, el catálogo y la política, el hash del
  manifiesto, la firma, el estado y el resultado, las cuentas de la cohorte, cuántos agregados y
  controles publicó, `hash_agregados` y la disponibilidad.
- **`entrada_analytics`**: cada entrada del manifiesto con su tipo, su identificador público, su
  versión, su firma, su ventana y cuándo estuvo disponible. Una por tipo, salvo las mensuales: una
  por ventana.
- **`agregado_analytics`**: una fila por métrica, vista, conjunto, periodo y celda, con su
  `dimension_hash` (SHA-256 del JSON canónico de sus dimensiones), sus dimensiones, su estado, su
  población, su valor, su numerador, su denominador y sus motivos. Llave natural, sin id propio, y
  sin EAV ciego: la métrica, la vista, el conjunto y el periodo son columnas.
- **`control_analytics`**: cada control con lo esperado, lo observado, la diferencia y su estado.

## API

| Ruta | Qué |
|---|---|
| `GET /analytics/catalogo` | El catálogo con su hash: cada métrica con su código, nombre, descripción, familia, unidad, fórmula, numerador, denominador, dimensiones permitidas, entradas requeridas, versión y limitaciones; la política con su hash y los conjuntos con sus llaves. |
| `POST /analytics/ejecuciones` | Pide una ejecución con su manifiesto explícito. `201` con `Location`: **se creó, no terminó**. |
| `GET /analytics/ejecuciones` | Las ejecuciones, la más reciente primero; por `corte_id`, `estado` o `as_of`. |
| `GET /analytics/ejecuciones/{analytics_run_id}` | Una ejecución: su manifiesto, sus relojes, sus versiones, sus hashes y su disponibilidad. |
| `GET /analytics/ejecuciones/{analytics_run_id}/agregados` | Lo publicado, paginado y filtrado en la base por `metrica`, `vista`, `dimension_set`, `periodo_desde`, `periodo_hasta`, `estado_valor` y cada llave de dimensión del catálogo. Ningún otro parámetro se acepta (422): ni SQL, ni expresiones, ni columnas arbitrarias. |
| `GET /analytics/ejecuciones/{analytics_run_id}/resumen` | Unos pocos indicadores del `TOTAL` acumulado (cuentas, saldo, recuperación neta, contactabilidad, promesas, campo y ruteo), cada uno con su estado. |
| `GET /analytics/ejecuciones/{analytics_run_id}/embudos` | Los embudos `CONTACTO` y `CAMPO`: cuentas por etapa, porcentaje de la anterior (nulo en la primera o sin dato) y de la cohorte. |
| `GET /analytics/ejecuciones/{analytics_run_id}/controles` | Los controles de la ejecución, también los de una `FALLIDA` porque uno no cuadró. |

Los agregados, el resumen y los embudos de una ejecución que no está `EXITOSA` responden `409
ANALYTICS_NO_PUBLICADA`. No existe `/cuentas/{cuenta_id}/analytics`.

## Línea de comandos

```bash
# Propone el manifiesto con lo que existía a known_at y lo escribe; no analiza nada.
motor-cartera construir-manifiesto-analytics --corte 2026-09-30 --as-of 2026-10-08 \
  --modo BACKTEST --known-at 2026-10-10T09:00:00-06:00 --zona America/Mexico_City \
  --granularidad DIA --destino datos/manifiesto_analytics.json

# Analiza con ese manifiesto, en primer plano, con su propio trabajo de la cola.
motor-cartera analizar-cobranza --manifiesto datos/manifiesto_analytics.json

motor-cartera resumen-analytics --ejecucion <analytics_run_id>
motor-cartera exportar-analytics --ejecucion <analytics_run_id> --formato parquet

# Lo que falta, corte por corte, con el horizonte declarado; --dry-run no encola nada.
motor-cartera backfill-analytics --modo BACKTEST --known-at 2026-10-10T09:00:00-06:00 \
  --zona America/Mexico_City --granularidad MES --horizonte 30 --dry-run
```

`construir-manifiesto-analytics` muestra exactamente qué ejecución propone de cada familia, con su
firma y su regla, y las demás candidatas. Solo lee lo que existía a `known_at`. Sus reglas: de cada
mes, la interpretación **vigente a `known_at`** (la de mayor id de su ventana: una reinterpretación
sustituye a la anterior, por la regla de `motor-pagos/v1`); de cada mes, la atribución que leyó esa
interpretación; la evaluación de ese `as_of` en esa zona; la decisión, la geografía, la territorial,
el plan y el ruteo del corte, encadenados. **Si una familia tiene dos o más candidatas igual de
válidas (otro `known_at`, otra referencia, otra configuración), es ambigua: no se elige, se reporta
y no se escribe el manifiesto**; quien pide la declara con su opción (`--decision-run`, ...). Nunca
inicia un análisis.

`exportar-analytics` escribe CSV, Parquet (decimales exactos, `decimal128(38, 10)`) o JSONL: una
fila por métrica, celda y periodo, con su estado. Una celda `SUPRIMIDO` se exporta sin ningún valor.
`backfill-analytics` exige el modo, la granularidad, la zona, la frontera (en `OPERATIVO`, sin ella,
el reloj de la base una vez para todo el backfill) y el horizonte (`--as-of` o `--horizonte`, uno):
no infiere nada, reporta los cortes ambiguos sin analizarlos y no vuelve a pedir lo que ya tiene una
ejecución con la misma firma. La frontera es parte de la firma: repetido con la misma `--known-at`,
el backfill no pide nada dos veces; en `OPERATIVO` sin ella, cada vez es otra frontera, y lo que pide
es otro análisis de los mismos cortes con lo que se conoce ahora (que puede incluir lo que llegó
tarde), no un duplicado.

## Rendimiento

Los índices son los de sus consultas: la llave natural de los agregados (ejecución, métrica, vista,
conjunto, periodo, celda), que sirve cada filtro por métrica; (ejecución, conjunto, vista, periodo)
para los filtros sin métrica; las únicas parciales por firma de la `EXITOSA` y de la `EN_PROCESO`;
(corte, `as_of`, id) en las ejecuciones; y las llaves de las entradas y de los controles. Ningún GIN
sobre las dimensiones. No hay particionado.

`scripts/benchmark_analytics.py` lo mide sobre la base XL de los benchmarks anteriores, sin
reconstruir nada (sus cortes, sus pagos interpretados y atribuidos, su lifecycle, su evaluación de
promesas, `decision/v2`, la geografía, `territorial/v2`, `campo/v2` y `ruteo/v2`), en Windows 11
con 12 CPU y PostgreSQL 16.15 y su configuración por omisión (`work_mem` de 4 MB,
`shared_buffers` de 256 MB). Cada análisis es `OPERATIVO`, en su propio proceso, y toma su
manifiesto de las reglas de `construir-manifiesto-analytics`; donde una familia tenía dos o más
candidatas, el benchmark declaró cuál usar antes de analizar.

### XL-A: la cadena de operación

El corte del 2026-04-01 (500,000 cuentas) al 2026-04-30, por día: la interpretación vigente de
abril (de 2 candidatas) y su atribución; la única `decision/v2`, geografía y `territorial/v2` del
corte; el plan de `campo/v2` de la configuración A (declarado, de 2) y su ruteo contra la red real
de Puebla (declarado, de 2). No hay una evaluación de promesas a ese `as_of`, así que las métricas
de promesas salen `NO_DISPONIBLE`. El lifecycle del XL termina el 2026-03-25: en el horizonte no
ocurrió ninguna gestión, y eso es un cero conocido, no un hueco. De abril, los archivos de pagos
conocidos traen 5,000 pagos, todos del 2026-04-02 y todos clasificados: 29 días sin cobertura, y la
recuperación del horizonte sale `NO_DISPONIBLE` (`PARCIAL` en su disponibilidad).

- **41.5 s** (12,060 cuentas por segundo), 182 MiB de memoria pico y 115 MiB de WAL; proponer el
  manifiesto, 1.6 s. Por fase: la cohorte, 7.8 s; los pagos y su cobertura, 11.7 s; la agrupación,
  5.1 s; los controles, 4.8 s; la publicación, 9.8 s; la supresión, 0.3 s.
- 203,113 agregados de 6,884 celdas, 14 conjuntos y 30 periodos: 144,831 `DISPONIBLE`, 42,614
  `NO_DISPONIBLE`, 14,138 `SUPRIMIDO` (1,555 celdas) y 1,530 `NO_APLICA`. 113 controles, ninguno
  fallido. Hash de lo publicado: `5aa55983906f3db66f50b1124c738453f3bcd27c0e35cd964ecbb82856631eea`.
- Lo que publica: `decision/v2` repartió las 500,000 cuentas en 132,529 `MUY_ALTA`, 173,521 `ALTA`,
  145,765 `MEDIA` y 48,185 `BAJA`; la geografía es válida en 449,982 (90.0 %); `territorial/v2`
  asignó 421,548 (84.3 %); `campo/v2`, 99,720 de 132,529 candidatas (75.2 %); y `ruteo/v2` programó
  41,896 de esas 99,720 paradas (42.0 %), en 5,523 rutas.

### XL-A sin la operación

El mismo corte y el mismo horizonte, solo con los pagos y la atribución: **28.2 s**, 165 MiB y
32 MiB de WAL; 53,985 agregados de 665 celdas. La cohorte tomó 2.9 s contra 7.8 s: unirle la
decisión, la geografía y la territorial por llave primaria cuesta unos 5 s en 500,000 cuentas.
Campo y ruteo (la fase de la operación) tomaron 0.6 s, y sus conjuntos son la mayor parte de lo
publicado: de las 6,219 celdas más, 6,135 son de gestores de campo (125,178 agregados); las demás,
de bases, prioridades, canales y sus cruces.

### XL-B: el lifecycle

El primer corte semanal (2026-01-07, 500,000 cuentas) al 2026-03-25, el día de la única
evaluación de promesas, por semana ISO (12 periodos): las interpretaciones vigentes de enero,
febrero y marzo (de 1, 3 y 4 candidatas), sus tres atribuciones, todo el lifecycle del XL y esa
evaluación. Ese corte no tiene `decision/v2`, geografía ni plan: sus métricas salen
`NO_DISPONIBLE`.

- Leyó 2,990,891 movimientos del horizonte (2,709,955 de la cohorte), 1,804,210 gestiones
  efectivas, 354,480 promesas y 2,697,650 pagos atribuidos; los 3,005,846 pagos observados de esos
  tres meses están todos clasificados. 77 de 78 días con cobertura: el día del corte no está en
  ningún archivo, así que el acumulado de la recuperación es `NO_DISPONIBLE` y la serie semanal,
  `DISPONIBLE` desde la segunda semana.
- **191.7 s**, 166 MiB y 27 MiB de WAL. Por fase: los hechos por cuenta y periodo, 80.8 s; los
  pagos, 40.2 s; la agrupación, 33.6 s; los controles, 11.4 s; el lifecycle, 9.4 s; la atribución,
  7.8 s; las promesas, 3.4 s; la cohorte, 3.2 s.
- 44,940 agregados de 674 celdas; 41 controles, ninguno fallido. Hash:
  `9851c6cf269174f0e213ce95ccb06e72e50e952888843dc219396499e2d470c5`. De las 354,480 promesas, al
  2026-03-25 se cumplieron 181,842 de 315,078 resueltas (57.7 %); 38,473 fueron parciales, 94,763
  incumplidas, 10,858 seguían pendientes y 28,544 se cancelaron.

### Lo que se corrigió al medir

La primera medición del XL-B tomó 289.9 s: 115.5 s en los hechos y 95.0 s en los pagos. Los planes
mostraron dos defectos del SQL, no de las fórmulas:

- Las tablas de paso de las entradas (una fila por mes) se creaban sin `ANALYZE`, y el planificador
  suponía unas 2,260 filas: ordenaba los 8 millones de movimientos de la cartera (390 MB en disco)
  para un *merge join* con tres interpretaciones. Ahora se analizan al crearse.
- En el `UNION ALL` de los dominios, cada rama llevaba los 25 hechos, con ceros en los que no eran
  suyos: 5.5 millones de filas de 372 bytes y un ordenamiento de 1 GB en disco. Ahora los ajenos van
  `NULL` (que no ocupa lugar en la fila) y la suma exterior los vuelve 0, y `an_pc` se escribe en el
  orden de su llave, para que su índice y la suma por cuenta la recorran en orden.

Lo publicado no cambió: el XL-A y el XL-B dieron el mismo hash antes y después (y el XL-A pasó de
51.2 a 41.5 s).

### La serie histórica

Los 12 cortes semanales del XL (del 2026-01-07 al 2026-03-25, de 500,000 a 465,999 cuentas), cada
uno con 30 días, por mes, con sus pagos y su atribución: **929.2 s** en total, **81.9 s de mediana**
por análisis (de 32.8 a 100.5 s: baja cuando el horizonte entra a abril, donde el XL casi no tiene
pagos), sin un control fallido y con unos 23 MiB de WAL cada uno. Cada análisis publicó **39,140
agregados de 674 celdas** (469,680 en total), igual con 500,000 cuentas que con 465,999: lo
publicado depende de las celdas, las métricas y los periodos, no de las cuentas ni de los días (el
horizonte del XL-A son 15 millones de cuenta-días, y publicó 0.41 agregados por cuenta). Seis
cortes tienen sus 30 días cubiertos; el del 2026-01-07 no tiene su primer día, y desde el del
2026-02-25 los días posteriores al 2026-03-25 no están en ningún archivo conocido, salvo el
2026-04-02 (29, 23, 16, 9 y 2 días cubiertos).

### Tamaños

Con 15 ejecuciones, 771,718 agregados ocupan 307.4 MiB (184.8 MiB de datos y 122.5 MiB de índices):
**418 bytes por agregado** con sus índices. La llave natural son 116.5 MiB y el índice por conjunto,
6.0 MiB; las ejecuciones, sus entradas y sus controles, menos de 0.3 MiB entre las tres. La base
creció 308.0 MiB.

### La API sobre el XL-A

Con 30 repeticiones, por la API y con la serialización, la mediana del catálogo, la ejecución, el
resumen, una métrica, la serie diaria de la recuperación, el producto, el tramo de atraso, la
prioridad, la base, el municipio (la primera y la décima página), un producto con todas sus
métricas, los embudos y los controles va de **11 a 16 ms**; dentro de PostgreSQL, de 0.1 a 1.5 ms,
por la llave natural (o por el índice del conjunto, sin métrica). La única lenta es la de todas las
celdas `SUPRIMIDO` de la ejecución, sin métrica ni conjunto: 81.5 ms (76 ms dentro de PostgreSQL,
que cuenta entre los 203,113 agregados de la ejecución). Ninguna recorre una tabla grande. El plan
de cada sentencia queda en `planes_analytics.txt` junto al reporte del benchmark.

### `work_mem`

El motor no fija `work_mem`. Con el valor por omisión (4 MB), los hechos y la agrupación del XL-B
derraman a disco. El mismo XL-B con `work_mem` de 256 MB solo en sus conexiones tomó **162.6 s**
contra 191.7 s (los hechos, 50.4 contra 80.8 s; los pagos, 32.6 contra 40.2 s), con el mismo hash.
Una operación que analice cohortes grandes puede subirlo para el rol del worker.

### Lo que no se indexó

La cobertura lee el primer y el último pago de cada archivo de pagos conocido recorriendo
`pago_observado` (3,536,840 pagos observados de 16 archivos en el XL): unos 6 s por análisis. Un
índice (archivo, fecha) lo bajaría a milisegundos, a cambio de escribirlo en cada ingesta de pagos;
v0.14.0 no lo agrega.

## Lo que `analytics/v1` no hace, a propósito

- **Causalidad**: no prueba que una gestión haya producido un pago; una asociación de pagos no es un
  impacto.
- **Predicción**: no hay forecasting, probabilidades, scoring, *expected recovery* ni modelos (eso es
  v0.15, Predictive Intelligence).
- **Recomendaciones** u optimización matemática.
- **Dashboards, gráficas o reportes visuales**: la salida son agregados, por la API, la línea de
  comandos y las exportaciones.
- **Lo ejecutado en campo**: una parada programada no es una visita realizada.
- **Personas**: un gestor de campo no es un `actor_ref`, y no hay rankings de cobradores.
- **Multimoneda**, retención o un *warehouse* externo.
