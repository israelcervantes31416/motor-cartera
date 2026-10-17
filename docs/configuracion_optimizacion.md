# La configuración de optimización (`optimization-config/v1`)

Una configuración de optimización es **configuración operacional versionada de un escenario**: qué
capacidad hay (sus pools), qué canales operativos son admisibles para cada canal que recomienda
`decision/v2`, qué coberturas se exigen, el total de cuentas, el presupuesto de unidades y si se
admiten predicciones `CON_IMPUTACION`. No es la política (`optimization-public-v1`, que dice cómo se
lee y qué significa el resultado) ni datos de la cartera. El modelo está en
[modelo_optimizacion.md](modelo_optimizacion.md); el uso, en [optimizacion.md](optimizacion.md).

Se registra con `POST /optimizacion/configuraciones` o `motor-cartera crear-config-optimizacion`,
nace `BORRADOR` (o `PUBLICADA` con `?publicar=true` o `--publicar`) y **solo una `PUBLICADA` se usa
para planear**. Publicada, no cambia nunca: cambiar una capacidad es otra versión, con otro hash.

## El documento

Un objeto JSON con un **esquema cerrado**: sin SQL, sin Python, sin expresiones. Una llave que no se
conoce, un tipo que no es el suyo o un valor fuera de su vocabulario rechazan el documento entero, y
**todos sus problemas se reportan juntos** (`422 CONFIGURACION_INVALIDA` con un `detalle` por
problema). No se registra nada a medias.

| Llave | Qué es | Regla |
| --- | --- | --- |
| `nombre` | Para personas | 1 a 120 caracteres; no entra en el hash |
| `permitir_predicciones_con_imputacion` | Si una predicción `CON_IMPUTACION` puede ser elegible | `true` o `false`, obligatorio y **sin valor por omisión** |
| `max_cuentas_atendidas` | El total de cuentas que el plan puede atender | Un entero ≥ 1, o `null` (sin límite) |
| `max_unidades_recurso` | El presupuesto de unidades de recurso | Un entero ≥ 1, o `null` (sin límite) |
| `admisibilidad` | La matriz explícita de canales | Una lista con al menos una entrada |
| `pools` | La capacidad | Una lista con al menos un pool activo |
| `restricciones` | Las coberturas | Una lista, que puede ser vacía |
| `supuestos` | Para personas: de dónde salen los números | Hasta 2,000 caracteres; no entra en el hash |

Cada número entero cabe en 2⁵³ − 1. Un código (`codigo`, `referencia_alcance` o el
`valor_dimension` de una base) es de 1 a 32 caracteres `A-Z`, `0-9`, `_` o `-`.

### La admisibilidad

Cada entrada dice si un canal operativo es admisible para un canal de decisión:

| Llave | Regla |
| --- | --- |
| `canal_decision` | El canal que recomienda `decision/v2`: `DIGITAL`, `TELEFONICA`, `CAMPO` u `OTRO` |
| `canal_operativo` | Un canal al que se asigna capacidad: `DIGITAL`, `TELEFONICA` o `CAMPO` |
| `permitido` | `true` o `false` |

Lo que la matriz no permite no es admisible. `OTRO` no es un canal operativo: no se le asigna
capacidad. **Admisible no es efectivo**: la matriz es una regla de la configuración, no la evidencia
de que un canal funcione mejor para una cuenta. Un par `(canal_decision, canal_operativo)` aparece a
lo más una vez.

### Los pools

| Llave | Regla |
| --- | --- |
| `codigo` | Único en la configuración; ordena los pools (su lugar canónico) |
| `canal` | `DIGITAL`, `TELEFONICA` o `CAMPO` |
| `tipo_alcance` | `GLOBAL` o `BASE` en `DIGITAL` y `TELEFONICA`; `GESTOR_CAMPO` en `CAMPO`, y solo en `CAMPO` |
| `referencia_alcance` | `null` en `GLOBAL`; el código de la base en `BASE`; el código del gestor en `GESTOR_CAMPO` (un pool por gestor) |
| `capacidad` | Un entero ≥ 1. Solo un pool `CAMPO` puede declararla `null`: entonces su capacidad son las paradas `PROGRAMADA` de su gestor |
| `unidades_recurso_por_asignacion` | Un entero de 1 a 1,000,000: esfuerzo abstracto, no dinero |
| `activo` | `true` o `false`; uno inactivo no entra a la ejecución |

Los alcances:

| Alcance | A quién alcanza |
| --- | --- |
| `GLOBAL` | A cualquier cuenta de un canal de decisión que admite su canal. |
| `BASE` | A las cuentas cuya base territorial es la del pool: la de `territorial/v2` en la cadena del `ruteo/v2` declarado. Exige declarar un ruteo. |
| `GESTOR_CAMPO` | A las cuentas con una parada `PROGRAMADA` de ese gestor en el `ruteo/v2` declarado. Su capacidad efectiva es la menor de la declarada y esas paradas. Exige declarar un ruteo. |

Un gestor o una base que la cadena del ruteo declarado no tiene no se descubre al registrar (la
configuración no lee la base): la ejecución termina `CONFIGURACION_INCOMPATIBLE` sin construir el
modelo.

### Las restricciones

| Llave | Regla |
| --- | --- |
| `codigo` | Único en la configuración |
| `tipo` | Uno de los de abajo |
| `dimension` | Una de las de abajo |
| `valor_dimension` | Un valor del vocabulario de su dimensión |
| `limite` | Cuentas en `MIN_CUENTAS` y `MAX_CUENTAS`; puntos base (0 a 10,000) en una participación |
| `activa` | `true` o `false`; una inactiva no entra al modelo |

| Tipo | Qué exige |
| --- | --- |
| `MIN_CUENTAS` | Al menos `limite` cuentas atendidas del grupo. |
| `MAX_CUENTAS` | A lo más `limite` cuentas atendidas del grupo. |
| `MIN_PARTICIPACION_BPS` | Que el grupo sea al menos `limite` puntos base de las atendidas: 10,000 · grupo ≥ `limite` · total. Resuelta a conteos, ⌈`limite` · N / 10,000⌉. |
| `MAX_PARTICIPACION_BPS` | Que el grupo sea a lo más `limite` puntos base de las atendidas: ⌊`limite` · N / 10,000⌋. |

| Dimensión | Su valor | Vocabulario |
| --- | --- | --- |
| `PRODUCTO` | El producto del snapshot del corte | `CONSUMO`, `TARJETA`, `NOMINA`, `AUTOMOTRIZ` |
| `ATRASO_BUCKET` | El tramo de días de atraso del snapshot | `0`, `1-30`, `31-60`, `61-90`, `91+` |
| `PRIORIDAD_DECISION` | La prioridad final de `decision/v2` | `BAJA`, `MEDIA`, `ALTA`, `MUY_ALTA` |
| `BASE` | La base territorial, por la cadena del ruteo | El código de una base (exige declarar un ruteo) |

**Ninguna dimensión es un dato personal ni un identificador**: no hay restricciones por `cuenta_id`,
`CLIENTE_UNICO`, `actor_ref`, gestor (un gestor es un pool de `CAMPO`), género, edad ni ningún
atributo sensible o su aproximación.

## Las advertencias

Lo que la configuración permite y no tiene efecto se registra como advertencia, sin rechazarla: un
canal operativo admisible sin ningún pool activo (sus cuentas quedan `NO_ELEGIBLE` con
`CONFIG_EXCLUYE_CANAL`), un pool activo de un canal que ningún canal de decisión admite, y una
restricción inactiva.

## La forma canónica y el hash

La forma canónica lleva todo lo que cambia un plan, en orden canónico: la forma
(`optimization-config/v1`), el flag de imputación, el total, el presupuesto, la admisibilidad (por
canal de decisión y canal operativo), los pools por código y las restricciones por código. El
`nombre` y los `supuestos` no entran. Su SHA-256 es el `hash_contenido` de la versión, y lo firma
cada ejecución (`hash_configuracion`).

- El mismo contenido en otro orden, con otro nombre o con otros supuestos es **el mismo hash** y la
  misma versión: registrarlo otra vez la reutiliza (`200`, `reutilizada: true`).
- Cambiar una capacidad, unas unidades, una admisibilidad, un límite, el flag, el total o el
  presupuesto es **otro hash** y otra versión.

## Los estados de una versión

| Estado | Qué es |
| --- | --- |
| `BORRADOR` | Registrada y validada; todavía no se usa para planear. |
| `PUBLICADA` | Se usa para planear. No cambia nunca: la base rechaza cualquier `UPDATE` o `DELETE` de ella, de su admisibilidad, de sus pools y de sus restricciones. |

Una versión solo pasa de `BORRADOR` a `PUBLICADA` (`POST /optimizacion/configuraciones/{id}/publicacion`);
publicarla no cambia su contenido ni su hash. `GET /optimizacion/configuraciones/{id}` dice si su
hash, vuelto a calcular con lo que hay en sus tablas, es el guardado (`hash_verificado`).

## Un ejemplo

```json
{
  "nombre": "Escenario sintetico",
  "permitir_predicciones_con_imputacion": false,
  "max_cuentas_atendidas": 9000,
  "max_unidades_recurso": 15000,
  "admisibilidad": [
    {"canal_decision": "DIGITAL", "canal_operativo": "DIGITAL", "permitido": true},
    {"canal_decision": "TELEFONICA", "canal_operativo": "TELEFONICA", "permitido": true},
    {"canal_decision": "TELEFONICA", "canal_operativo": "DIGITAL", "permitido": true},
    {"canal_decision": "CAMPO", "canal_operativo": "CAMPO", "permitido": true}
  ],
  "pools": [
    {"codigo": "DIGITAL_GLOBAL", "canal": "DIGITAL", "tipo_alcance": "GLOBAL",
     "referencia_alcance": null, "capacidad": 6000, "unidades_recurso_por_asignacion": 1,
     "activo": true},
    {"codigo": "CAMPO_GESTOR_001", "canal": "CAMPO", "tipo_alcance": "GESTOR_CAMPO",
     "referencia_alcance": "GESTOR_001", "capacidad": null,
     "unidades_recurso_por_asignacion": 20, "activo": true}
  ],
  "restricciones": [
    {"codigo": "MIN_MUY_ALTA", "tipo": "MIN_CUENTAS", "dimension": "PRIORIDAD_DECISION",
     "valor_dimension": "MUY_ALTA", "limite": 1500, "activa": true}
  ],
  "supuestos": "SUPUESTOS SINTETICOS: solo para probar el optimizador."
}
```

## La configuración del benchmark: `optimization-benchmark/public-v1`

**Estos límites existen únicamente para medir el optimizador.** No son capacidades, costos ni metas
de negocio, y nada aquí dice cuántas cuentas debería atender una operación. Se fijaron en el código
(`scripts/benchmark_optimizacion.py`, `configuracion_benchmark`) **antes de medir** y no se ajustaron
después de ver un número; cada corrida los registra antes de optimizar nada. Son una función del
tamaño *n* del universo del corte y de los gestores con paradas `PROGRAMADA` del ruteo declarado:

| Qué | Valor |
| --- | --- |
| Admisibilidad | `DIGITAL` → `DIGITAL`; `TELEFONICA` → `TELEFONICA` y `DIGITAL`; `CAMPO` → `CAMPO` y `TELEFONICA`; `OTRO`, ninguno |
| `DIGITAL_GLOBAL` | el 5 % de *n*, 1 unidad |
| `TELEFONICA_GLOBAL` | el 3 % de *n*, 4 unidades |
| `CAMPO_<gestor>` | uno por gestor con paradas `PROGRAMADA`: capacidad 8 (la efectiva, la menor con sus paradas), 20 unidades |
| `max_cuentas_atendidas` | el 10 % de *n* |
| `max_unidades_recurso` | el 70 % de *n* |
| `MAX_CONSUMO` | `MAX_PARTICIPACION_BPS`, `PRODUCTO` = `CONSUMO`, 4,500 |
| `MIN_AUTOMOTRIZ` | `MIN_CUENTAS`, `PRODUCTO` = `AUTOMOTRIZ`, el 0.6 % de *n* |
| `MIN_MUY_ALTA` | `MIN_CUENTAS`, `PRIORIDAD_DECISION` = `MUY_ALTA`, el 2 % de *n* |
| `MIN_TRAMO_1_30` | `MIN_PARTICIPACION_BPS`, `ATRASO_BUCKET` = `1-30`, 1,500 |
| Imputación | Declarada por escenario: `false` en M; `true` en XL, porque el scoring XL es todo `CON_IMPUTACION` (su corte viene de otro generador, sin historia) |

La sensibilidad escala solo las capacidades de `DIGITAL_GLOBAL` y `TELEFONICA_GLOBAL` por 8/10 y
12/10, en aritmética entera. Los resultados están en [optimizacion.md](optimizacion.md#lo-medido).
