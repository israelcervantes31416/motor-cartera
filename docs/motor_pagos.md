# El Motor de Pagos (`motor-pagos/v1`)

v0.6 respondió qué archivo llegó y cómo se preserva; v0.7, qué le pasó a cada cuenta a través del
tiempo. v0.8 responde otra pregunta:

> ¿Qué movimientos económicos observados representan hechos económicos distintos, cuáles son
> posibles duplicados, cuáles revierten a otros, a qué cuenta pertenecen y cuál es la recuperación
> económica que interpreta el motor?

La regla que lo ordena todo: **observación ≠ interpretación**. Un `PagoObservado` es una fila de
`pagos/v1` tal como llegó, y no cambia nunca: no se borra, no se marca como duplicado, no se
convierte en reverso ni en recuperación. Lo que el motor concluye vive en entidades nuevas,
versionadas, que se pueden reconstruir y que una versión futura (`motor-pagos/v2`) puede volver a
calcular sobre las mismas observaciones sin tocar las de `v1`.

```
PagoObservado               una fila de pagos/v1, tal como llegó (v0.7)
      │                     inmutable: evidencia
      ▼
EjecucionMotorPagos         motor-pagos/v1 sobre una ventana: un despacho, una cartera, un mes
      │                     de recepción; EN_PROCESO, EXITOSA o FALLIDA; su firma de entrada
      ▼
ResultadoPagoObservado      qué concluyó de cada observación de la ventana, y por qué
      │                     (clasificación, conciliación, huellas, motivos)
      ▼
MovimientoEconomicoCanonico cada hecho económico que considera distinto
      │                     (PAGO, REVERSO, POSIBLE_REVERSO), conciliado o no con su cuenta
      ▼
Cuenta 360                  /pagos-observados (lo observado) y /movimientos (lo interpretado)
```

El código vive en `src/motor_cartera/motor_pagos/`: `reglas.py` es el núcleo puro y legible,
`firmas.py` las huellas y los identificadores, `ejecuciones.py` la interpretación en PostgreSQL,
`contexto.py` el contexto temporal, `backfill.py` y `consultas.py` lo demás. Las decisiones de
diseño están en [decisiones.md](decisiones.md) (85 a 98).

## Las entidades

**`PagoObservado`** (v0.7). Una fila aceptada de `pagos/v1`: sus 23 campos tipados, su dataset
conformado, su fila y su hoja, su ingesta, su despacho y su cartera. Dos filas idénticas son dos
observaciones, y el mismo movimiento en dos archivos también. Es la evidencia, y el motor la lee sin
modificarla: una prueba compara cada pago observado, columna por columna, antes y después de
interpretar.

**`EjecucionMotorPagos`**. Una interpretación de una ventana: `despacho_id`, `cartera_id` y un mes
calendario de recepción, `[periodo_desde, periodo_hasta)`. Guarda su versión (`motor-pagos/v1`), su
estado (`EN_PROCESO`, `EXITOSA`, `FALLIDA`), su resultado (`INTERPRETACION_PUBLICADA`,
`YA_INTERPRETADA`, `ERROR_INTERNO`...), su **firma de entrada** (el SHA-256 de lo que leyó), sus
conteos de calidad, su recuperación interpretada, sus tiempos y su detalle. Su identificador público,
`motor_pagos_run_id`, es aleatorio, como el de toda ejecución: identifica un intento.

**`ResultadoPagoObservado`**. Lo que una ejecución concluyó de una observación: su clasificación, su
estado de conciliación, sus dos huellas, el movimiento que funda o del que es copia, el movimiento
relacionado (el original de un reverso, o el reverso de un pago anulado) y sus motivos. Su llave
primaria es `(ejecucion_motor_pagos_id, dataset_conformado_id, source_row)`: **cada observación de
la ventana tiene exactamente un resultado por ejecución**, y la ejecución no se cierra `EXITOSA` si
no los tiene todos. `motivos` es JSONB y solo explica: ninguna regla lee de ahí.

**`MovimientoEconomicoCanonico`**. Lo que `motor-pagos/v1` considera un hecho económico distinto. No
viene del acreedor: su `movimiento_id` es un identificador interno de Motor Cartera, determinista.
Guarda el cliente, la fecha de recepción, el importe reportado con su signo, el signo económico
(`SUMA` o `RESTA`), el tipo (`PAGO`, `REVERSO`, `POSIBLE_REVERSO`), su conciliación con la cuenta,
cuántas observaciones lo sustentan, su firma exacta y, si aplica, el movimiento que revierte o el que
lo anula. Es único por ejecución: cada interpretación de una ventana tiene sus propios movimientos,
con los mismos identificadores cuando son los mismos hechos.

## La ventana y su contexto

El motor no trabaja por archivo: trabaja por **ventana**, un despacho, una cartera y un mes calendario
de `Fecha_Recepción`. Una ejecución lee todos los pagos observados de su ventana, de todos los
archivos que los trajeron. Que la ventana sea un mes es una regla de `motor-pagos/v1`, y la base la
garantiza (`ck_motor_pagos_mes`); otra versión puede usar otra.

Un reverso puede llegar en el mes siguiente al de su pago. Por eso cada ejecución lee también un
**contexto** acotado: los pagos de hasta **30 días antes y después** de la ventana que comparten
cliente e importe con algún pago de la ventana, cuando esa pareja (cliente, importe) tiene algún
negativo en ese rango. Esos pagos sirven para decidir las parejas de reverso; no reciben resultado
en esta ejecución, porque son de otra ventana. Ningún otro pago puede cambiar una decisión de la
ventana, y por eso no se lee: la prueba de equivalencia lo comprueba comparando contra el núcleo
puro aplicado a todos los pagos de la cartera.

Todo lo que se interpreta se lee en **una sola sentencia** (`INSERT ... SELECT` a una tabla
temporal): la ventana, su contexto, el SHA-256 del archivo original de cada pago y su cuenta
canónica salen de la misma foto de la base.

**Procesamiento incremental.** Un archivo nuevo no hace reinterpretar la historia: se vuelve a
interpretar la ventana que toca, y una vecina solo si su contexto cambia (ver la orquestación, abajo).
El costo es el de un mes de pagos de una cartera y no crece con los meses acumulados; el resultado es
el mismo que reconstruir todo desde cero con la misma versión, porque una ventana se interpreta
siempre entera (lo prueban la reconstrucción, el orden de llegada y la equivalencia). El benchmark
mide cuánto cuesta una llegada tardía. Ver la decisión 86.

## Las huellas

Dos hashes deterministas por observación, los dos SHA-256 de una línea canónica: la de
`contratos.fuente.linea_canonica`, cada valor como `largo:texto`, separados por comas, y un vacío
como `-`. El largo hace la línea inequívoca aunque un texto traiga comas o dos puntos. **No son
identificadores de la fuente ni sustituyen a los ids internos**: son herramientas de comparación y
de auditoría, y se guardan en cada resultado.

**`firma_exacta`**:

```
SHA-256( "firma_exacta/v1\n" + linea_canonica([despacho_id, cartera_id, <los 23 campos>]) )
```

con los 23 campos en el orden del contrato (`Año`, `Semana`, `Territorio`, `Zona`, `Cliente_Unico`,
`Fecha_Recepción`, `Segmento`, `Gerencia`, `Tipo_Cartera`, `Producto`, `Campaña`, `Gestor`,
`Días_de_Atraso`, `Semanas_de_Atraso`, `Plan_de_Pago`, `Fecha_de_Gestion`,
`Recuperación_por_Gestión`, `Concepto_Cálculo`, `Cargos_Automáticos`, `Captación`,
`Cobranza_Total`, `Porcentaje_Comision`, `Monto_Comision`) y cada valor con el texto de su tipo en
`PagoObservado`, que es el del contrato:

| Tipo | Texto canónico | Ejemplo |
|---|---|---|
| texto y código | tal cual: el contrato ya lo dejó sin espacios alrededor y en NFC | `GESTOR 007` |
| entero | sin ceros a la izquierda | `37` |
| importe | con dos decimales; el cero sin signo | `-150.00`, `0.00` |
| fecha y hora | `AAAA-MM-DDTHH:MM:SS`, con `.ffffff` solo si los microsegundos no son cero | `2026-09-03T10:15:00` |
| decimal (`Porcentaje_Comision`, `float8`) | el texto que PostgreSQL le da a un `float8` con `extra_float_digits = 1`: los dígitos más cortos que vuelven al mismo número, en notación científica si el exponente es menor que -4 o de 15 o más | `0.05`, `1e-05` |
| vacío | `-` | |

El despacho y la cartera van delante: dos pagos idénticos de dos carteras no son el mismo hecho. El
motor fija `extra_float_digits = 1` en su transacción, para que el texto de un `float8` no dependa de
la configuración del servidor. La huella existe dos veces, en SQL (la que calcula el motor sobre
millones de filas) y en Python (la referencia), y las pruebas comprueban que dan lo mismo.

**`firma_legacy`**:

```
SHA-256( "firma_legacy/v1\n" + linea_canonica([despacho_id, cartera_id, Cliente_Unico,
         Fecha_Recepción truncada al segundo como AAAA-MM-DDTHH:MM:SS,
         Recuperación_por_Gestión con dos decimales]) )
```

Es la llave con que el sistema anterior juntaba pagos (`contratos.pagos.LLAVE_HISTORICA`). Aquí es
**solo una heurística de comparación**: nunca fusiona nada por sí misma.

**Nunca se confía ciegamente en un hash.** Antes de tratar varias observaciones como copias, el motor
compara cada una con su representante **campo por campo** (los 23 y el despacho y la cartera, con
`IS DISTINCT FROM`). Si alguna difiere —dos observaciones distintas con la misma firma—, el grupo no
se interpreta: queda `NO_CONCILIADO` con el motivo `FIRMA_SIN_VALIDAR`. Con SHA-256 no debería pasar
nunca; una prueba lo provoca reemplazando la huella por una pobre.

## Las clasificaciones

Cada observación de la ventana recibe exactamente una. Son seis, con definiciones cerradas:

| Clasificación | Definición | Funda un movimiento | Recuperación bruta | Recuperación neta |
|---|---|---|---|---|
| `MOVIMIENTO_PRIMARIO` | Funda un movimiento de importe positivo (un `PAGO`). Si tiene copias exactas, es su representante. | sí, `PAGO` | suma, salvo que un reverso lo anule | suma, salvo que un reverso lo anule |
| `DUPLICADO_EXACTO` | Es idéntica al representante de su grupo en sus 23 campos, su despacho y su cartera: el mismo hecho reportado otra vez. | apunta al del grupo | nada: el movimiento ya cuenta una vez | nada |
| `COINCIDENCIA_AMBIGUA` | Comparte la llave histórica (cliente, segundo de recepción e importe) con otra observación que difiere en algún otro campo. No se sabe si son uno o dos pagos. | no | nada | nada |
| `REVERSO` | Funda un movimiento negativo que forma una pareja aislada con un `PAGO` (ver abajo). | sí, `REVERSO`, apuntando a su original | anula a su original | el par suma cero |
| `POSIBLE_REVERSO` | Funda un movimiento negativo que no forma esa pareja: sin original posible, con varios, con uno ambiguo o con uno que reclama otro negativo. | sí, `POSIBLE_REVERSO`, sin original | nada | resta su importe |
| `NO_CONCILIADO` | El motor no puede decidir qué movimiento representa: su importe es cero, o su grupo de copias no pasó la comparación campo por campo. | no | nada | nada |

El orden en que se decide, para cada grupo de observaciones con la misma firma exacta:

1. si su grupo de la llave histórica junta firmas distintas, **todas** sus observaciones son
   `COINCIDENCIA_AMBIGUA`, incluidas las copias exactas entre sí;
2. si el grupo no pasó la comparación campo por campo, o su importe es cero, todas son
   `NO_CONCILIADO`;
3. si no, el grupo funda un movimiento: su representante es `MOVIMIENTO_PRIMARIO`, `REVERSO` o
   `POSIBLE_REVERSO`, según el tipo del movimiento, y las demás son `DUPLICADO_EXACTO`.

Así se cumple, y la base lo exige (`ck_motor_pagos_movimientos`), que **cada movimiento lo funda
exactamente una observación**: movimientos = primarios + reversos + posibles reversos.

**`SIN_CUENTA_OBSERVADA` no es una clasificación**: es un estado de conciliación, en otro eje (ver
abajo). Un pago sin cuenta puede ser primario, duplicado o ambiguo; mezclarlos obligaría a elegir
entre dos verdades.

### Los motivos

Cada resultado explica su clasificación con uno o dos motivos, cada uno con los datos que lo
sustentan:

| Código | Cuándo | Datos |
|---|---|---|
| `OBSERVACION_UNICA` | Un primario sin copias | |
| `REPRESENTANTE_DE_COPIAS` | Un primario con copias exactas | `copias` |
| `COPIA_EXACTA` | Un duplicado exacto | `representante` (su `pago_observado_id`), `observaciones` |
| `LLAVE_HISTORICA_COMPARTIDA` | Una coincidencia ambigua | `observaciones`, `firmas_exactas`, `campos_distintos` (los nombres del contrato que difieren) |
| `ANULADO_POR_REVERSO` | Un primario cuyo pago anuló un reverso | `reverso`, `ventana_dias` |
| `PAREJA_UNICA` | Un reverso | `original`, `ventana_dias` |
| `SIN_CANDIDATOS` | Un posible reverso sin original posible | `candidatos`, `ventana_dias` |
| `VARIOS_CANDIDATOS` | Un posible reverso con dos o más originales posibles | `candidatos`, `ventana_dias` |
| `CANDIDATO_AMBIGUO` | Un posible reverso cuyo único original posible es una coincidencia ambigua | `candidatos`, `ventana_dias` |
| `ORIGINAL_DISPUTADO` | Un posible reverso cuyo único original lo reclama también otro negativo | `candidatos`, `ventana_dias` |
| `IMPORTE_CERO` | Un no conciliado de importe cero | `observaciones` |
| `FIRMA_SIN_VALIDAR` | Un no conciliado cuyas copias no son iguales campo por campo | `observaciones` |

## Duplicados: exacto y llave histórica

El sistema anterior juntaba como un solo pago las filas con el mismo cliente, la misma fecha de
recepción al segundo y el mismo importe a centavos. En los archivos reales que se revisaron no produjo
colisiones, pero eso no demuestra que nunca junte dos pagos legítimos. Por eso v0.8 distingue:

- **Duplicado exacto**: los 23 campos normalizados, el despacho y la cartera, iguales, comprobado
  campo por campo. Es evidencia fuerte de que es el mismo evento reportado otra vez: el grupo funda
  **un** movimiento, que cuenta una vez, y las dos (o tres, o las que sean) observaciones se
  conservan, cada una con su resultado. La lógica no supone pares: un grupo es un `GROUP BY` sobre la
  firma exacta.
- **Coincidencia por la llave histórica**: el mismo cliente, el mismo segundo y el mismo importe, pero
  otro gestor, otra campaña u otro campo. **No se fusiona**: las observaciones quedan
  `COINCIDENCIA_AMBIGUA`, sin movimiento, y su importe se reporta aparte
  (`importe_ambiguo_observado`), sumado tal como llegó, sin entrar en ninguna recuperación. Resolverlo
  necesita evidencia que `pagos/v1` no trae: es de una versión posterior, con reglas adicionales.

Dos pagos legítimos iguales —el mismo cliente, el mismo importe, el mismo día— **no se fusionan** si
algo los distingue: a otra hora son otra llave histórica y dos movimientos. La prueba
`test_dos_pagos_legitimos_iguales_del_mismo_dia_no_se_fusionan` lo fija.

### El representante

Cuando varias observaciones idénticas fundan un movimiento, su representante es la de menor
**(SHA-256 del archivo original, fila de la fuente)**. No es la primera que llegó al motor, que
dependería del orden de llegada, ni la de menor `pago_observado_id`, que sale de un `dataset_id`
sorteado: es una llave que sale del contenido de la evidencia, y da el mismo representante al
reconstruir y en cualquier orden de ingesta.

## Reversos

`pagos/v1` **no trae ninguna columna que diga cuál es el original de un reverso**: no hay
identificador de movimiento, de crédito ni de referencia. Lo que hay es el signo de
`Recuperación_por_Gestión`, que el contrato admite negativo ("como un ajuste"), y `Concepto_Cálculo`,
que es texto libre sin catálogo. Por eso:

- un importe negativo **resta** recuperación, pero **no es, por sí solo, un reverso**;
- `motor-pagos/v1` no lee `Concepto_Cálculo`: sería inventar un catálogo que el contrato no tiene;
- la pareja se busca por lo único que las fuentes permiten: cliente, importe exacto y tiempo.

**Una pareja de reverso posible** es un `PAGO` y un negativo del mismo despacho, cartera y cliente,
con el mismo importe absoluto, el negativo recibido entre el instante del pago y **30 días después**
(inclusive). Un grupo ambiguo de la llave histórica cuenta como un solo candidato, que no es limpio.

**Un reverso inequívoco** es una pareja **aislada**: el pago es el único original posible del
negativo, el negativo es el único reverso posible del pago, y los dos son grupos limpios. Entonces el
negativo es `REVERSO` y apunta a su original (`movimiento_original_id`), y el pago queda anulado
(`anulado_por_movimiento_id`). Cualquier otra cosa es `POSIBLE_REVERSO`, y **el motor no elige**:

| Caso | Motivo |
|---|---|
| Ningún pago del mismo cliente e importe en los 30 días anteriores | `SIN_CANDIDATOS` |
| Dos o más originales posibles | `VARIOS_CANDIDATOS` |
| Un solo original posible, pero ambiguo | `CANDIDATO_AMBIGUO` |
| Un solo original posible, que otro negativo también reclama | `ORIGINAL_DISPUTADO` |

El pago y su reverso pueden estar en archivos y en ventanas distintos: la ventana del pago lee el
reverso como contexto (y se vuelve a interpretar cuando llega), y la del reverso lee el pago. Las dos
interpretaciones dicen lo mismo de la pareja, cada una desde su lado, porque aplican la misma regla a
los mismos pagos y los identificadores son deterministas.

La ventana de 30 días es una regla de `v1` sin evidencia empírica de su duración (no hay datos de
reversos reales). Cambiarla es otra versión del motor.

## El signo económico

| Qué | Efecto | Por qué |
|---|---|---|
| `PAGO` (importe positivo) no anulado | **suma** a la bruta y a la neta | es la recuperación que la fuente reporta |
| `PAGO` anulado por un `REVERSO` | **neutral** | el par suma cero |
| `REVERSO` | **neutral** | ya anuló a su original |
| `POSIBLE_REVERSO` (importe negativo) | **resta** de la neta; neutral en la bruta | el signo y el importe son ciertos; a qué pago revierte, no |
| `DUPLICADO_EXACTO` | **neutral** | su movimiento ya cuenta una vez |
| `COINCIDENCIA_AMBIGUA` | **no se puede interpretar** | no se sabe si es uno o dos pagos |
| `NO_CONCILIADO` | **no se puede interpretar** | importe cero, o copias que no son iguales |

El signo lo decide solo `Recuperación_por_Gestión`, el importe del movimiento según el contrato. Los
demás importes de la fila (`Captación`, `Cobranza_Total`, `Cargos_Automáticos`) se conservan pero
`v1` no los usa: suponer cómo se relacionan sería inventar una regla.

## Conciliación con la cuenta

El motor usa la identidad que ya construyó v0.7: `despacho_id + cartera_id + cliente_unico` →
`CuentaCanonica`. No hay lógica nueva de identidad.

- **`CONCILIADO_CUENTA`**: hay una cuenta canónica con esa llave cuando se interpreta. El movimiento
  guarda su `cuenta_canonica_id`.
- **`SIN_CUENTA_OBSERVADA`**: ningún corte de la cartera ha traído al cliente. El movimiento **se
  conserva**, se interpreta igual y **no crea una cuenta**.

La conciliación es parte de la interpretación, y por eso es versionada: si después llega un corte que
trae al cliente, la ejecución anterior **no se modifica**. `backfill-motor-pagos --reconciliar`
encuentra las ventanas cuya interpretación vigente tiene movimientos sin cuenta que ya la tienen, y
abre una ejecución nueva, que publica la interpretación conciliada; la anterior queda en el historial
diciendo lo que se sabía entonces. La prueba `test_un_pago_sin_cuenta_se_concilia_con_otra_
interpretacion_cuando_llega_su_corte` lo recorre entero.

## El contexto temporal

Para un movimiento conciliado con una cuenta, dónde cae entre los snapshots de esa cuenta. Se calcula
**al consultar**, como los eventos de presencia de v0.7, a partir de los cortes de la cartera y los
cortes en que la cuenta tiene snapshot (`motor_pagos/contexto.py`, puro). No se guarda: un corte que
llega después cambia el contexto exactamente como si hubiera llegado a tiempo.

| Campo | Definición |
|---|---|
| `snapshot_anterior` | El último corte en que se observó la cuenta, del día del pago o de antes |
| `snapshot_siguiente` | El primer corte, posterior al día del pago, en que se observó la cuenta |
| `antes_de_primera_observacion` | El pago es de antes del primer corte en que aparece la cuenta |
| `despues_de_ultima_observacion` | El pago es de después del último corte en que aparece, siga o no en la cartera |
| `durante_ausencia_observada` | La cuenta ya se había observado y el corte más reciente de su cartera al día del pago no la traía: llegó mientras faltaba, o después de su salida observada |

La fecha de un corte no tiene hora: un pago se compara con el corte de su mismo día como "anterior o
igual", y `v1` no supone a qué hora se toma el corte. **Nada de esto es un error**: un pago antes de
la primera observación de su cuenta, o mientras faltaba, es información. Con el snapshot anterior y el
siguiente se puede mirar *saldo antes → pago → saldo después*, **sin afirmar que toda la diferencia de
saldo la causó el pago**.

## La recuperación interpretada

- **`recuperacion_bruta_interpretada`** = la suma de los `PAGO` que ningún `REVERSO` anuló. No suma
  duplicados exactos (el movimiento cuenta una vez), pagos anulados, coincidencias ambiguas ni
  negativos.
- **`recuperacion_neta_interpretada`** = la bruta más los `POSIBLE_REVERSO`, que son negativos. Un
  `REVERSO` y el pago que anula suman cero y no entran en ninguna de las dos.

La neta es la suma de los importes de todos los movimientos que `v1` interpreta, contando cada hecho
una vez: con un posible reverso entre dos originales, la bruta suma los dos pagos y la neta resta el
negativo, que es lo que pasó sea cual sea el original. Se calculan por ventana (en la ejecución) y por
cuenta (en la Cuenta 360, sobre las interpretaciones vigentes); la de una cuenta es la suma de sus
ventanas.

**Es la interpretación del motor sobre las fuentes disponibles, no un ledger contable.** El saldo
oficial sigue siendo el que observa `SnapshotCuenta`. En el escenario sintético del generador, el
`SALDO` de cada cuenta evoluciona exactamente con la suma de sus movimientos interpretados (una prueba
lo comprueba centavo por centavo, y con las observaciones sin deduplicar no cuadraría); en datos
reales no se cumpliría así, porque el acreedor carga intereses y ajustes que ninguna fuente trae.

## Identificadores deterministas

`movimiento_id` es un **UUID versión 8** (RFC 9562, sección 5.8) con nombre y SHA-256, como el
ejemplo del apéndice B.2 del RFC: los primeros 128 bits de
`SHA-256(espacio + "movimiento:" + versión + ":" + hex(firma_exacta))`, con los bits de versión y de
variante, en un espacio de nombres propio del motor (`firmas.ESPACIO`). El mismo grupo de
observaciones, con la misma versión, da el mismo identificador al reconstruir, en cualquier orden y en
cualquier base; otra versión del motor da otro. No es versión 5 como los de v0.7 porque PostgreSQL no
trae SHA-1 sin una extensión, y el motor genera millones dentro de la base; la propiedad que importa
es la misma. Una prueba fija el vector del RFC (`5c146b14-3c52-8afd-938a-375d0df1fbf6`).

## Versiones, firma de entrada e historia de interpretaciones

La **firma de entrada** de una ejecución es el SHA-256 de lo que leyó, en forma canónica: la versión,
la ventana, el SHA-256 de cada archivo original con pagos en la ventana (sus pagos de la ventana son
siempre los mismos: un pago observado no cambia ni se borra, y los de un archivo se publican juntos),
cada pago del contexto, nombrado por su archivo y su fila, y cada cliente de la ventana que tenía cuenta
canónica. No depende de identificadores sorteados.

La base garantiza:

- **a lo más una `EXITOSA` por ventana, versión y firma de entrada**
  (`ux_ejecucion_motor_pagos_exitosa`): las mismas entradas no se publican dos veces. Una ejecución
  que las encuentra ya publicadas termina `FALLIDA` con `YA_INTERPRETADA`, nombrando a la que ganó;
- **a lo más una `EN_PROCESO` por ventana y versión** (`ux_ejecucion_motor_pagos_en_proceso`): una
  ventana no se interpreta dos veces a la vez.

Cuando llegan pagos nuevos a una ventana, o a su contexto, o una cuenta para un pago sin cuenta, otra
ejecución de la misma ventana, con otra firma, publica la interpretación nueva. La anterior **no se
toca**. La **vigente** de una ventana es su `EXITOSA` más reciente con la versión que se pide (la del
servicio por omisión); como solo hay una `EN_PROCESO` por ventana a la vez, es la de mayor id. La API
dice en cada ejecución y en cada movimiento si es vigente, y `GET /motor-pagos` lista la historia.
Un `motor-pagos/v2` publicaría sus propias ejecuciones, resultados y movimientos sobre los mismos
pagos observados, sin tocar los de `v1`.

## La ejecución: por conjuntos, todo o nada

`interpretar` toma la ejecución con su fila bloqueada y, en una transacción:

1. lee la ventana y su contexto a una tabla temporal, con sus huellas, en una sola sentencia;
2. calcula la firma de entrada y, si ya hay una `EXITOSA` con ella, termina `YA_INTERPRETADA`;
3. agrupa por firma exacta y por llave histórica, elige representantes con `DISTINCT ON`, compara
   las copias campo por campo y busca las parejas de reverso, todo con `CREATE TEMP TABLE ... AS` e
   índices temporales;
4. inserta los movimientos y después los resultados con `INSERT ... SELECT`;
5. cuenta lo publicado desde las tablas, comprueba que cada observación tenga su resultado y que cada
   movimiento lo funde una observación, y cierra `EXITOSA`.

Ninguna observación pasa por Python: Python coordina y PostgreSQL agrupa, une, ordena y cuenta. Los
pasos 1 a 5 corren en un savepoint, y las tablas temporales son `ON COMMIT DROP`: si algo falla, se
revierte todo lo de ese intento y la ejecución queda `FALLIDA` **en la misma transacción, sin soltar
su fila**, así que nadie la ve `EN_PROCESO` entre el fallo y su cierre (una historia que abre la
ventana en ese momento espera, la encuentra `FALLIDA` y abre otra). Las pruebas inyectan fallas antes
del staging, después de clasificar, después de crear los movimientos, antes de insertar los
resultados y antes de cerrar: en ningún caso queda nada publicado, y otra ejecución interpreta
después la ventana entera.

**Un error transitorio de la base no es una conclusión.** Si se cae la conexión, PostgreSQL detecta
un interbloqueo o cancela una sentencia (`OperationalError`), no se marca nada: se revierte todo, la
ejecución sigue `EN_PROCESO` y la cola reintenta su trabajo con su espera, como cualquier error del
worker. Si agota sus intentos, queda `FALLIDA` con `INTENTOS_AGOTADOS`, y el backfill la encuentra.

**Solo publica el dueño vigente de su trabajo.** Antes de confirmar, la ejecución bloquea su trabajo
`MOTOR_PAGOS` y comprueba que siga siendo de su worker (`cola.confirmar_dueno`). Un worker que perdió
su lease mientras calculaba no publica ni falla nada: se revierte, y el worker que tomó el trabajo, que
lo estaba esperando, interpreta la ventana. Con la fila del trabajo bloqueada, nadie puede tomarlo
entre esa comprobación y el commit.

## Cuándo se interpreta: la orquestación

```
INGESTA_PAGOS ──► HISTORIA ──► MOTOR_PAGOS
 (pagos/v1)      (pagos observados)   (una ventana)
```

- **En la misma transacción** que la historia publica los pagos observados de un archivo, se abre o se
  reusa la ejecución `EN_PROCESO` de cada ventana que esos pagos tocan, con su trabajo `MOTOR_PAGOS`
  (`abrir_por_dataset`). No hay un instante en que existan pagos observados sin una interpretación
  pendiente que los vaya a leer.
- También se abre la de una **ventana vecina** si esos pagos entran en su contexto: un pago o un
  negativo con el cliente y el importe de algún pago de la vecina, cuando esa pareja tiene algún
  negativo, a menos de 30 días de ella. Es exactamente la condición con que la vecina los leería.
  Para decidirlo, la apertura lee los pagos del dataset una vez a una tabla temporal con su índice y
  sus estadísticas: las de `pago_observado` todavía no los conocen (decisión 96).
- Si la ventana ya tiene una `EN_PROCESO` que **no ha empezado**, se reusa: la leerá después. Si un
  worker la **está interpretando**, la apertura espera a que termine (su fila está bloqueada) y abre
  otra. Ningún pago queda fuera de la interpretación vigente.
- Las aperturas de una misma cartera van **una a la vez** (un bloqueo consultivo hasta el commit), así
  que la que va después ve los pagos que publicó la anterior.
- En la cola, el motor va **al final**: lo operacional primero, la historia después y el motor de pagos
  al último. Con los `HISTORIA` pendientes antes, una ventana se interpreta una vez con todos sus
  archivos, y no una vez por archivo.
- El motor de pagos **no es parte del flujo operacional**: `decision/v1`, `territorial/v1` y
  `ruteo/v1` no lo leen ni lo esperan, y una falla suya no detiene ninguna etapa.

El trabajo `MOTOR_PAGOS` usa la misma cola durable que todos: lease, latido, reintentos con espera,
recuperación de un worker muerto, varios workers, entrega al menos una vez y cierre condicionado a su
dueño. Lo que hace segura la repetición son el bloqueo de la ejecución, sus estados terminales, la
transacción todo o nada, los índices únicos y la confirmación del dueño.

## Backfill

La migración `0009` solo crea las tablas. Los pagos observados publicados antes de v0.8 se
interpretan con `motor-cartera backfill-motor-pagos`, **por ventanas**:

```bash
motor-cartera backfill-motor-pagos --dry-run            # cuánto falta, sin encolar nada
motor-cartera backfill-motor-pagos                      # encola cada ventana pendiente
motor-cartera backfill-motor-pagos --reintentar-fallidas
motor-cartera backfill-motor-pagos --reconciliar
```

Agrupa los pagos observados por ventana y compara cada una con su interpretación vigente: **al día**
(su vigente leyó todos sus pagos; como un pago observado no se borra, contar basta), **en la cola**,
**sin interpretación**, **desactualizada** (llegaron pagos que su vigente no ve), **solo con
ejecuciones `FALLIDA`**, **con una reinterpretación `FALLIDA` después de su vigente** (se abrió
otra ejecución porque cambió su contexto o su conciliación, y falló: la vigente no ve ese cambio,
aunque haya leído todos sus pagos; una `YA_INTERPRETADA` no cuenta, porque no cambió nada) y **por
conciliar** (al día, pero con movimientos sin cuenta cuyo cliente ya tiene una). Encola las sin
interpretación y las desactualizadas; las que fallaron, de las dos formas, con
`--reintentar-fallidas`; las por conciliar, con `--reconciliar`. Es idempotente: una ventana en la
cola no se encola otra vez, y una al día no se toca. Dice cuántos pagos observados no tienen
interpretación vigente; en el CI, después de la prueba de humo, son cero, y no hay ninguna ventana
que haya fallado.

## Linaje

Todo movimiento se explica hasta el archivo y la fila que lo justifican:

```
MovimientoEconomicoCanonico ─► ResultadoPagoObservado (uno o varios) ─► PagoObservado
   ─► DatasetConformado ─► _source_row y _source_sheet ─► IngestaPagos ─► ArtefactoFuente original

MovimientoEconomicoCanonico ─► CuentaCanonica ─► SnapshotCuenta (el anterior y el siguiente)
```

`GET /movimientos/{movimiento_id}` trae su representante con su archivo original (SHA-256 y nombre),
y `/observaciones`, cada pago observado que lo sustenta con su ingesta, su dataset, su fila y su
archivo. Así se responde *¿por qué este movimiento vale una sola vez si la fuente lo reportó dos?*

## La API

| Ruta | Qué |
|---|---|
| `GET /motor-pagos` | Las interpretaciones de la cartera, por ventana, con `vigente`; filtros `periodo`, `estado`, `version` |
| `GET /motor-pagos/{motor_pagos_run_id}` | Una interpretación: versión, estado, periodo, `calidad`, `recuperacion`, tiempos y detalle |
| `GET /motor-pagos/{motor_pagos_run_id}/resultados` | Qué concluyó de cada observación, y por qué; filtros `clasificacion`, `cliente_unico`, y `firma_exacta` o `firma_legacy` (en hexadecimal) para ver un grupo entero |
| `GET /movimientos` | Los movimientos vigentes; filtros `cliente_unico`, `cuenta_id`, `desde`, `hasta`, `tipo`, `estado_conciliacion` |
| `GET /movimientos/{movimiento_id}` | Uno, con su clasificación, sus motivos, su representante con su archivo y su contexto temporal |
| `GET /movimientos/{movimiento_id}/observaciones` | Los pagos observados que lo sustentan |
| `GET /cuentas/{cuenta_id}/movimientos` | Los movimientos de una cuenta, con su contexto temporal; `desde`, `hasta` |
| `GET /cuentas/{cuenta_id}` | Ahora trae `resumen_pagos`: observaciones, movimientos, duplicados, ambiguos, reversos y recuperación interpretada |

Todas paginan, buscan por identificadores públicos y responden con una sesión de lectura
`REPEATABLE READ`: una respuesta que compone varias consultas ve una sola foto de la base. Una prueba
publica una interpretación nueva a media respuesta y comprueba que la Cuenta 360 no mezcla las dos.
`/cuentas/{cuenta_id}/pagos-observados` no cambia: **sigue siendo lo observado**. Ver
[cuenta_360.md](cuenta_360.md).

## Índices y volumen

| Tabla | Índice | Para qué |
|---|---|---|
| `pago_observado` | `(despacho_id, cartera_id, fecha_recepcion)` (nuevo) | Leer una ventana sin recorrer los pagos de toda la historia |
| `pago_observado` | `(despacho_id, cartera_id, fecha_recepcion) WHERE recuperacion_por_gestion < 0` (nuevo, parcial) | Los negativos de una ventana y su contexto: son pocos |
| `pago_observado` | `(despacho_id, cartera_id, cliente_unico, fecha_recepcion)` (v0.7) | Los pagos de una cuenta; un grupo de copias o de la llave histórica |
| `ejecucion_motor_pagos` | dos únicos parciales por ventana y versión | Una `EXITOSA` por firma; un intento activo |
| `resultado_pago_observado` | `PRIMARY KEY (ejecucion, dataset, fila)` | Un resultado por observación y ejecución |
| `resultado_pago_observado` | `(movimiento_economico_canonico_id)` | Las observaciones de un movimiento |
| `movimiento_economico_canonico` | `UNIQUE (ejecucion_motor_pagos_id, movimiento_id)` | Un movimiento por ejecución; los de una ejecución |
| `movimiento_economico_canonico` | `(movimiento_id)` | `GET /movimientos/{movimiento_id}` en cualquier interpretación |
| `movimiento_economico_canonico` | `(despacho_id, cartera_id, cliente_unico, fecha_recepcion)` | Los movimientos de una cuenta, en los dos sentidos |

Las huellas **no** tienen índice persistente: la interpretación agrupa por ellas en sus tablas
temporales, con índices temporales, y un grupo de copias o de la llave histórica comparte cliente,
segundo e importe, así que `/resultados?firma_legacy=...&cliente_unico=...` lo encuentra por el
índice de los pagos de una cuenta. Sin el cliente, la búsqueda filtra los resultados de la ventana:
con pocas interpretaciones en la base, PostgreSQL prefiere recorrer los resultados de todas (en el XL,
~0.9 s por sentencia), y con muchas, los de la ejecución por su llave primaria. La primera página de
`/resultados`, sin filtros, lee los pagos de la ventana en orden de recepción por su índice y se
detiene al llenarla. **No hay particionado**, por la misma regla que en v0.7: se introduce con
evidencia, y el benchmark no la encontró (abajo).

## Benchmark

`scripts/benchmark_motor_pagos.py` (manual, fuera del CI normal; también como workflow a mano)
genera o reusa el escenario longitudinal de la historia, ingiere cada corte y cada archivo de pagos,
materializa su historia (que abre las ventanas), interpreta cada ventana en su propio proceso, hace
llegar tres archivos tarde y mide las consultas de una cuenta y de un movimiento con el plan de cada
sentencia (`EXPLAIN (ANALYZE, BUFFERS)`), tal como las emite el servicio. Falla si alguna consulta de
una cuenta recorre entera `pago_observado`, `resultado_pago_observado`,
`movimiento_economico_canonico`, `snapshot_cuenta` o `cuenta_canonica`. Cada etapa se anota en una
bitácora en cuanto termina, y `--reanudar` sigue una corrida interrumpida sobre la misma base sin
repetir ni duplicar nada.

### 12 cortes XL con sus pagos

Medido el 2026-10-08 con:

```bash
MC_FILAS_POR_LOTE=10000 python scripts/benchmark_motor_pagos.py --perfil XL --cortes 12 \
  --destino <directorio> --reanudar
```

- **Escenario.** El de la historia (v0.7): perfil XL, 500,000 cuentas iniciales, 12 cortes semanales
  en zip (del 2026-01-07 al 2026-03-25) y los 11 archivos de pagos entre ellos, semilla 31416. Según
  su manifiesto, 2,995,846 movimientos, con 14,904 repetidos exactos y 9,100 ajustes negativos.
- **Máquina.** La de desarrollo, que no era dedicada: Windows 11, 12 hilos, Python 3.14.6 y
  PostgreSQL 16.15 local, con `shared_buffers` de 256 MB, `max_wal_size` de 4 GB y
  `checkpoint_timeout` de 5 minutos. Tenía otras aplicaciones abiertas: entre 0.9 y 2.9 GB de
  memoria disponible de 15.4 GB, muestreada cada 30 s.
- **La corrida se interrumpió dos veces por falta de memoria en la máquina**, la primera ingiriendo
  un corte y la segunda interpretando una ventana (con 148 MiB su proceso), y se reanudó sobre la
  misma base con `--reanudar`. De lo que había terminado antes de la segunda interrupción quedan el
  tiempo, la velocidad y la memoria pico de cada interpretación (de su registro) y todos sus
  conteos (de la base); se perdieron sus fases y su WAL, el `VACUUM` y el tamaño de la base antes
  del motor, y el tiempo de cada historia.

**El backfill: las tres ventanas de los 11 archivos.**

| Ventana | Observaciones | Contexto | Movimientos | Duplicados exactos | Reversos | Posibles reversos | Tiempo | Obs./s | Memoria pico |
|---|---|---|---|---|---|---|---|---|---|
| 2026-01 | 955,877 | 37 | 951,164 | 4,713 | 4 | 2,878 | 223.0 s | 4,286 | 147 MiB |
| 2026-02 | 1,091,725 | 79 | 1,086,270 | 5,455 | 15 | 3,325 | 288.0 s | 3,790 | 147 MiB |
| 2026-03 | 948,244 | 53 | 943,508 | 4,736 | 14 | 2,864 | 357.9 s | 2,650 | 147 MiB |

**En total**, 868.9 s para 2,995,846 observaciones (3,448 por segundo), con 147 MiB de memoria pico:
ninguna observación pasa por Python. Las tres terminaron `EXITOSA`, sin coincidencias ambiguas, sin
no conciliados y sin pagos sin cuenta.

**Lo interpretado cuadra con el manifiesto:**

- 2,995,846 observaciones leídas, cada una con exactamente un resultado: los movimientos de los 11
  archivos.
- 14,904 duplicados exactos: los repetidos exactos del manifiesto. Por eso hay 2,980,942
  movimientos canónicos, 2,995,846 menos 14,904.
- 9,100 negativos, los ajustes del manifiesto: 33 reversos y 9,067 posibles reversos. El generador
  no escribe reversos de un pago; esos 33 son parejas aisladas por coincidencia (mismo cliente e
  importe a menos de 30 días), y los demás no tienen un pago candidato, o tienen varios.
- Ninguna coincidencia ambigua: el generador no escribe dos filas con la misma llave histórica y otro
  campo distinto. El escenario golden sí, y las pruebas las cubren.
- Recuperación interpretada, por ventana: bruta 2,137,248,052.74, 2,277,015,492.38 y
  1,899,077,262.97; neta (la bruta más los posibles reversos, que son negativos) 2,136,176,123.24,
  2,275,805,620.50 y 1,898,039,577.16.

**Dónde va el tiempo.** Las fases de las reinterpretaciones (las del backfill se perdieron con la
interrupción):

| Fase | 2026-03 (tardía 1) | 2026-02 (tardía 2) | 2026-03 (tardía 2) | 2026-03 (tardía 3) | 2026-04 (tardía 3) |
|---|---|---|---|---|---|
| staging | 39.6 s | 66.0 s | 48.1 s | 38.7 s | 2.3 s |
| firma de entrada | 2.1 s | 2.4 s | 2.0 s | 2.2 s | 0.0 s |
| clasificación | 26.0 s | 30.7 s | 26.1 s | 26.3 s | 0.3 s |
| movimientos | 142.4 s | 150.3 s | 132.8 s | 160.2 s | 8.3 s |
| resultados | 126.9 s | 152.3 s | 108.5 s | 111.5 s | 0.3 s |
| conteos | 10.2 s | 20.1 s | 4.1 s | 3.4 s | 0.0 s |
| commit | 0.1 s | 0.1 s | 0.3 s | 0.3 s | 0.4 s |
| **total** | 347.4 s | 421.9 s | 322.0 s | 342.9 s | 11.7 s |
| WAL escrito | 1,851 MiB | 2,155 MiB | 1,915 MiB | 1,770 MiB | 97 MiB |

Leer y clasificar un mes de unas 950,000 observaciones toma de uno a dos minutos (el staging y la
clasificación); lo caro es escribir: insertar sus movimientos y sus resultados es el 70 a 80 % del
tiempo, con 1.8 a 2.2 GB de WAL por ventana. Tres cosas lo explican: el índice por `movimiento_id`
(un UUID determinista, en orden aleatorio) recibe cada inserción en una página distinta; cada
resultado pone, por su llave foránea, un bloqueo `KEY SHARE` en su pago observado, que se escribe en
la página del pago; y después de cada checkpoint, la primera escritura de cada página va completa al
WAL. La ventana de abril, con 5,000 observaciones, lo muestra en pequeño: 8.3 s de sus 11.7 s son
los movimientos, y escribió 97 MiB de WAL para 4,977 movimientos y 5,000 resultados. El tiempo de una
misma ventana (febrero) varió de 230 a 422 s entre interpretaciones, con la máquina bajo presión de
memoria y checkpoints de 1.4 a 2 GB cada 5 minutos: no hay un evento del motor que lo explique.

**Llegadas tardías.** Tres archivos de 5,000 pagos cada uno, tomados del último periodo: pagos de
ese periodo recibidos 3 días antes, el reverso de 5,000 pagos ya interpretados (su importe en
negativo, 2 días después) y pagos del mes siguiente:

| Llegada tardía | Historia | Apertura de ventanas | Ventanas que reinterpretó | Interpretación |
|---|---|---|---|---|
| Pagos de la última ventana | n/d | n/d | 2026-02 (contexto) y 2026-03 | 577.6 s |
| Reversos de pagos ya interpretados | 12.3 s | 7.77 s | 2026-02 (contexto) y 2026-03 | 743.9 s |
| Pagos del mes siguiente | 5.8 s | 1.35 s | 2026-03 (contexto) y 2026-04 | 354.6 s |

La apertura de estas dos historias se midió antes de la corrección de abajo; con la corregida, sobre
los mismos datasets, tomó 1.0 y 0.6 s.

- **Los reversos.** De los 5,000, 4,941 quedaron `REVERSO`, cada uno con el pago de marzo que
  revierte (los pagos anulados de marzo pasaron de 7 a 4,948); 30 quedaron `POSIBLE_REVERSO` con
  `VARIOS_CANDIDATOS`, porque su cliente tenía otro pago del mismo importe a menos de 30 días; y 29
  son `DUPLICADO_EXACTO`, el reverso de una fila que ya venía repetida. La recuperación bruta de
  marzo bajó exactamente el importe de los pagos anulados.
- **Lo que cuesta una llegada.** Cada mes que toca se reinterpreta entero: de 5 a 7 minutos y unos
  2 GB de WAL por mes completo, y otra copia de sus resultados y sus movimientos. Las tres llegadas
  reabrieron también la ventana vecina, porque cambiaron su contexto; en las tres, esa vecina publicó
  exactamente las mismas conclusiones que su vigente, con más contexto.

**Tamaño.** Al final, después de `VACUUM ANALYZE`, con 9 interpretaciones publicadas de 4 ventanas:

| Tabla | Filas | Datos | Índices | Total |
|---|---|---|---|---|
| `movimiento_economico_canonico` | 8,013,749 | 1,693.0 MiB | 1,070.0 MiB | 2,763.5 MiB |
| `resultado_pago_observado` | 8,054,297 | 1,659.2 MiB | 465.0 MiB | 2,124.7 MiB |
| `pago_observado` | 3,010,973 | 840.4 MiB | 464.2 MiB | 1,304.9 MiB |

La base ocupa 8,756.8 MiB, y las tablas del motor, 4,888.3 MiB. Una interpretación cuesta unos 640
bytes por observación con sus índices (unos 360 su movimiento y unos 280 su resultado): un mes XL
completo, unos 0.6 GB. Los dos índices nuevos de `pago_observado` ocupan 145.4 MiB (la ventana) y
1.0 MiB (sus negativos).

**Consultas.** Con todo eso publicado, mediana de 25 repeticiones desde el servicio, cada una en su
sesión de lectura `REPEATABLE READ`:

| Consulta | Mediana | p95 | Máximo | Sentencia más lenta en PostgreSQL |
|---|---|---|---|---|
| Movimientos de una cuenta | 7.13 ms | 10.50 ms | 30.86 ms | 0.149 ms |
| Detalle de un movimiento | 8.66 ms | 10.51 ms | 51.91 ms | 0.169 ms |
| Observaciones de un movimiento | 7.39 ms | 8.88 ms | 10.28 ms | 0.151 ms |
| Grupo por firma exacta (con cliente) | 7.41 ms | 9.33 ms | 49.00 ms | 0.215 ms |
| Grupo de la llave histórica (con cliente) | 6.73 ms | 7.89 ms | 11.80 ms | 0.137 ms |
| Movimientos de una cuenta en un intervalo | 7.78 ms | 10.13 ms | 11.87 ms | 0.068 ms |
| Resumen de pagos de una cuenta | 4.05 ms | 4.74 ms | 9.26 ms | 0.234 ms |
| Cuenta 360 con su resumen de pagos | 7.19 ms | 8.61 ms | 19.62 ms | 0.219 ms |

Cada sentencia de una consulta de una cuenta o de un movimiento entra por un índice
(`ix_movimiento_cuenta`, `ix_movimiento_movimiento_id`, `ix_resultado_pago_movimiento`,
`ix_pago_observado_cuenta` y las llaves primarias) y ninguna recorre una tabla grande. Las de una
ventana o de toda la cartera se miden para saber cuánto cuestan:

| Consulta | Mediana | p95 | Máximo |
|---|---|---|---|
| Primera página de resultados de una ventana | 77.02 ms | 82.51 ms | 96.48 ms |
| Coincidencias ambiguas de una ventana | 1,600.02 ms | 1,636.40 ms | 1,749.16 ms |
| Grupo de la llave histórica sin el cliente | 1,669.68 ms | 1,705.26 ms | 1,746.01 ms |
| Primera página de movimientos de la cartera | 4,875.58 ms | 6,597.91 ms | 15,675.49 ms |
| Posibles reversos de un día en la cartera | 1,990.86 ms | 2,041.75 ms | 2,282.04 ms |

Sin un cliente, una ventana se filtra recorriendo los resultados (con 9 interpretaciones,
PostgreSQL prefiere recorrerlos todos), y la cartera, sus movimientos vigentes: no hay un índice por
fecha que no empiece por el cliente. Se midió uno, `(ejecucion_motor_pagos_id, fecha_recepcion DESC,
movimiento_id)`: los posibles reversos de un día bajaron de 1,991 a 70 ms, pero la primera página
sin filtros siguió en ~6.8 s (cuenta los 3 millones de vigentes), y escribir 250,000 movimientos
tardó 66.1 y 52.9 s con él contra 22.6 y 26.0 s sin él, en una transacción que se revirtió. Cada
llegada tardía reinterpreta meses enteros: no se agrega.

**Dos correcciones que salieron del benchmark**, medidas antes y después en la misma base:

- **La primera página de resultados de una ventana** ordenaba los 3 millones de pagos observados de
  la cartera, con unos 760 MiB de orden en disco, para devolver 50: 3.9 s de mediana y hasta 51 s.
  Ahora lee los de la ventana por su índice de recepción y se detiene al llenar la página: 77 ms.
- **La apertura de ventanas en la historia de un archivo de pagos.** Durante la corrida, la historia
  de los 11 archivos tardó unos 1,690 s entre sus cierres en la base, contra 361 s en v0.7 con la
  misma máquina. Medida por fases con un archivo de 262,997 pagos: 107.1 s, de los que 86.6 s fueron
  la apertura. Sus pagos se acaban de publicar en la misma transacción y las estadísticas todavía no
  los conocen: con una estimación de una fila, el contexto de una vecina recorría los 262,997 pagos
  del dataset por cada negativo (84.1 s una sola vecina). Ahora la apertura los lee una vez a una
  tabla temporal con su índice y sus estadísticas: otro archivo del mismo tamaño se historió en 21.7
  s, de los que la apertura fue 1.27 s. Las dos versiones de la consulta dan lo mismo en las 70
  combinaciones de dataset y ventana de la base.

**Sin particionado.** Ninguna consulta de una cuenta o de un movimiento recorre una tabla grande, y
cada sentencia se resuelve en menos de 0.25 ms dentro de PostgreSQL con 8 millones de movimientos y
8 millones de resultados. Lo que cuesta es escribir una interpretación completa, y particionar no lo
reduce. El caso en que podría pagar es la retención: quitar las interpretaciones que ya no son
vigentes, que crecen con cada llegada tardía, quitando particiones enteras. Es una decisión de la
operación, con datos de producción (v0.18).

## Lo que no sabemos, y se dice

Esto es una fortaleza del diseño, no una debilidad:

- **No tenemos el ledger completo** del acreedor: ni todas sus transacciones, ni los intereses
  diarios, cargos, condonaciones, ajustes contables, refinanciamientos o castigos, ni sus reglas.
- **No conocemos todos los ajustes contables** que mueven un saldo. Por eso el saldo oficial sigue
  siendo el que observa `SnapshotCuenta`, y el motor no calcula ningún saldo.
- **La ausencia de una cuenta en un corte no significa liquidación**: puede ser una retirada, una
  venta, un castigo o un error de la fuente.
- **Dos pagos iguales pueden ser legítimos.** Un cliente puede pagar dos veces lo mismo.
- **Una coincidencia de la llave histórica no demuestra un duplicado.** Por eso queda ambigua.
- **Un importe negativo no demuestra un reverso**, y ninguna columna dice cuál es el original.
- **La ventana de 30 días de los reversos no tiene evidencia empírica**: es una regla explícita de
  `v1`.
- **La recuperación interpretada no sustituye a la contabilidad oficial.**

## Lo que `motor-pagos/v1` no hace, a propósito

- **No atribuye pagos a gestiones.** Los campos `Gestor`, `Fecha_de_Gestion` y `Campaña` se
  conservan y se ven en cada observación, pero la atribución definitiva gestión → pago necesita el
  lifecycle de cobranza (gestión, contacto, promesa, visita y resultado), que construye **v0.9**; la
  atribución formal queda para después de él. La interfaz futura cuelga de
  `MovimientoEconomicoCanonico` por su `movimiento_id`, sin tocar esta capa.
- **No construye un `GestorCanonico`** desde el texto libre de `Gestor`: es un atributo observado del
  movimiento.
- **No resuelve las coincidencias ambiguas** ni usa `Concepto_Cálculo`, `Captación` o
  `Cobranza_Total` para decidir signos: necesitan reglas con evidencia.
- **No reconcilia un pago con la diferencia de saldo** entre dos snapshots: lo muestra, no lo afirma.
- **No alimenta a `decision/v1`**: el Decision Engine v2, que podrá usar la recuperación, es de v0.10.
- Tampoco: nuevos scores, ML, roll rates, vintages, cure rates, pronósticos, causalidad, geografía
  real, bases operativas, capacidad, OSRM, VRP, dashboards, nube ni multitenancy.
