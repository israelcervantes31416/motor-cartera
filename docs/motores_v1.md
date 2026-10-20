# Los motores v1: decision/v1, territorial/v1 y ruteo/v1

Las tres etapas que un `POST /corridas` encadena después de la ingesta, sobre la foto operacional de
una corrida (`Cuenta`). Siguen vigentes: el flujo automático las corre, y `decision/v2`,
`territorial/v2`, `campo/v2` y `ruteo/v2` corren en paralelo a ellas, sin reemplazarlas. Cómo se
piden y qué responden, en [flujo_completo.md](flujo_completo.md); el porqué de cada regla, en las
decisiones 16 a 22 (decision/v1), 23 a 30 (territorial/v1) y 31 a 41 (ruteo/v1) de
[decisiones.md](decisiones.md).

## decision/v1

**Entrada:** una cuenta certificada, es decir, de una corrida `EXITOSA`. De ella, las reglas
solo leen los días de atraso y el saldo; producto, canal y claves geográficas no entran.

**Salida:** por cada cuenta,

- `segmento`: en qué situación de mora está;
- `prioridad`: qué tan pronto hay que gestionarla;
- `canal_recomendado`: por dónde conviene gestionarla. Es una decisión, no el canal con que la
  cuenta llegó en la cartera;
- `motivos`: por qué, paso a paso, cada uno con su `codigo`, el `campo` que la regla leyó o
  produjo y su `valor`.

Las reglas tienen su propia versión, `VERSION_REGLAS_DECISION = decision/v1`, independiente de
la del contrato, `VERSION_CONTRATO = cartera/v1`. Cada corrida guarda con qué contrato entró la
cartera, y cada ejecución, con qué reglas se tomó la decisión. Si cambia una regla, cambia la
versión de las reglas y no la del contrato, y una decisión vieja se sigue explicando con las
reglas que la tomaron.

Las reglas de `decision/v1`, en el orden en que se aplican:

| Días de atraso | Tramo del resumen | Segmento | Prioridad base |
|---|---|---|---|
| 0 | `0` | `AL_CORRIENTE` | `BAJA` |
| 1 a 30 | `1-30` | `MORA_TEMPRANA` | `MEDIA` |
| 31 a 90 | `31-60` y `61-90` | `MORA_MEDIA` | `ALTA` |
| 91 o más | `91+` | `MORA_ALTA` | `MUY_ALTA` |

- **Saldo alto.** Con atraso y un saldo de al menos `UMBRAL_SALDO_ALTO = 50,000.00`, la
  prioridad sube un nivel, con tope en `MUY_ALTA`. Una cuenta al corriente nunca sube por saldo.
- **Canal.** Sale solo de la prioridad final: `BAJA` y `MEDIA` van por `DIGITAL`, `ALTA` por
  `TELEFONICA` y `MUY_ALTA` por `CAMPO`.
- **Motivos.** Uno por paso: el tramo (`SIN_MORA`, `MORA_1_30`, `MORA_31_90` o `MORA_91_MAS`),
  `SALDO_ALTO` si la regla aplicó (aunque la prioridad ya estuviera en el tope), la prioridad
  final y el canal. Salen de un catálogo cerrado de doce códigos.

Así, una cuenta con 65 días de atraso y 62,000.00 de saldo queda en `MORA_MEDIA`, sube de `ALTA` a
`MUY_ALTA` por su saldo y se recomienda por `CAMPO`: es la del ejemplo del paso 10 de
[flujo_completo.md](flujo_completo.md).

**Las reglas y el umbral son sintéticos**, propios de este proyecto público: no vienen de
ninguna operación real, no son reglas propietarias y no son una recomendación de cobranza.
Existen para que el motor tenga algo concreto que decidir, probar y explicar. El umbral vive en
el código y no en la configuración: si se pudiera cambiar por entorno, la misma versión daría
resultados distintos.

El núcleo de las reglas es puro: no lee la base, la configuración ni el reloj, así que la misma
cuenta con la misma versión da siempre la misma decisión. Guardar las decisiones de una corrida
es trabajo de otra capa, que las publica todas o ninguna.

## territorial/v1

**Entrada:** una ejecución de decisión `EXITOSA` de `decision/v1`. De cada decisión solo cuenta
el canal recomendado, y de su cuenta, el saldo y el municipio.

**Agrupación:** un territorio es un municipio, `cve_entidad + cve_municipio`: `"21"` y `"074"`
dan `"21074"`. Las claves son las de la cartera, validadas por su forma.

**Métricas**, por municipio, sobre las decisiones de esa ejecución:

- `cuentas_total`: cuántas cuentas tiene;
- `saldo_total`: su saldo sumado;
- `cuentas_campo`: cuántas tienen `CAMPO` como canal recomendado. Cuenta la decisión, no el
  canal con que la cuenta llegó en la cartera;
- `saldo_campo`: el saldo de esas cuentas de campo.

Las calcula PostgreSQL con un solo `GROUP BY`: al núcleo llega una fila por municipio, no una por
cuenta.

**Carga**, solo por las cuentas de campo:

| Cuentas de campo | Carga | Motivo |
|---|---|---|
| 0 | `SIN_CARGA` | `SIN_CARGA_CAMPO` |
| 1 a 4 | `BAJA` | `CARGA_CAMPO_1_4` |
| 5 a 19 | `MEDIA` | `CARGA_CAMPO_5_19` |
| 20 o más | `ALTA` | `CARGA_CAMPO_20_MAS` |

**Orden:** los municipios con cuentas de campo reciben un lugar, `posicion_campo`, desde 1 y sin
huecos, por:

1. `cuentas_campo`, de mayor a menor;
2. `saldo_campo`, de mayor a menor;
3. `clave_territorio`, de menor a mayor.

Los `SIN_CARGA` van al final, por clave y sin lugar (`null`). **El saldo no cambia la carga:
solo desempata.** Un municipio con mucho saldo y pocas cuentas de campo conserva su carga, y
nunca queda delante de otro con más cuentas de campo. No hay un puntaje que mezcle cuentas y
pesos: cada lugar se explica con los valores que el resultado expone.

**Los umbrales (5 y 20) son sintéticos y demostrativos**, propios de este proyecto público: no
son propietarios, no vienen de ninguna operación real y no son una recomendación real de
cobranza. Como el de `decision/v1`, viven en el código y no en la configuración.

Con la cartera que el generador produce por omisión (semilla 31416), las 9,800 decisiones caen en
541 municipios: 27 con carga `ALTA`, 75 `MEDIA`, 301 `BAJA` y 138 `SIN_CARGA`. El primero es
`21074`, con 425 cuentas de campo de 1,414: es el del ejemplo del paso 13 de
[flujo_completo.md](flujo_completo.md).

**Cuatro versiones, cuatro preguntas.** Ninguna es la versión del paquete:

- `cartera/v1` (`VERSION_CONTRATO`): qué datos entran;
- `decision/v1` (`VERSION_REGLAS_DECISION`): qué decisión recibe cada cuenta;
- `territorial/v1` (`VERSION_REGLAS_TERRITORIAL`): cómo se organiza por territorio el trabajo de
  campo;
- `ruteo/v1` (`VERSION_REGLAS_RUTEO`): en qué secuencia se visitan las cuentas de campo dentro de
  cada municipio.

`territorial/v1` solo organiza decisiones de `decision/v1`. La compatibilidad es un literal:
cuando el Decision Engine decida con `decision/v2`, `territorial/v1` no la acepta sola. Cada
ejecución territorial guarda con qué reglas se calculó y cuelga de la ejecución de decisión que
organizó, así que de un municipio se llega a sus decisiones, de ellas a la corrida y de la
corrida al archivo.

**v0.3 prioriza; v0.4 secuencia.** v0.3.0 responde dónde se concentra la carga operativa de
campo y qué municipios conviene atender primero. v0.4.0 responde en qué secuencia visitar las
cuentas de campo dentro de cada municipio. `posicion_campo` es una prioridad territorial, no una
parada en un recorrido.

## ruteo/v1

**Entrada:** una ejecución territorial `EXITOSA` de `territorial/v1`, sobre decisiones de una
ejecución `EXITOSA` de `decision/v1`. Las dos compatibilidades son literales: una
`territorial/v2` o una `decision/v2` no se vuelven ruteables solas.

**Paradas:** solo las cuentas con `CAMPO` como canal recomendado,
`DecisionCuenta.canal_recomendado`, nunca el canal con que la cuenta llegó en la cartera. Cada una
es exactamente una parada.

**Territorio:** una ruta independiente por municipio con trabajo de campo (`cuentas_campo > 0`).
Cada municipio tiene su propio depósito y su propio plano, y el motor no conecta municipios entre
sí. Los `SIN_CARGA` no tienen ruta.

**Coordenadas sintéticas y deterministas.** La cartera no trae latitud, longitud ni direcciones, y
aquí no se inventan ni se consultan servicios de mapas. Cada municipio es un plano operativo local,
una cuadrícula de 10 km por lado, con el depósito en `(0, 0)` y coordenadas enteras de −5000 a +5000
metros. El punto de cada cuenta sale de SHA-256 sobre
`"ruteo/v1|{clave_territorio}|{cliente_unico}"`: los primeros 8 bytes del digest dan `x_m` y los 8
siguientes `y_m`, `% 10001 - 5000`. Sin azar, semilla, reloj ni configuración: la misma versión, el
mismo municipio y el mismo cliente dan siempre el mismo punto.

**Métrica:** distancia Manhattan, `|x1 − x2| + |y1 − y2|`, en metros sintéticos enteros: exacta,
sin `float` ni raíces.

**Algoritmo:** la ruta sale del depósito, visita cada cuenta una vez y regresa, y **la distancia
incluye el regreso al depósito**:

1. vecino más cercano: siempre a la cuenta pendiente más cercana; a igual distancia, a la de
   `cliente_unico` menor;
2. 2-opt: en cada pasada, de todas las inversiones de un tramo, la de mayor ahorro estrictamente
   positivo (a igual ahorro, la de `i` y después `j` menores), una por pasada y **hasta 10
   mejoras** (`MAX_PASADAS_2OPT`).

Cada ruta guarda la distancia del vecino más cercano (`distancia_inicial_m`), la final
(`distancia_total_m`), el regreso al depósito y la mejora del 2-opt, que nunca es negativa.

Con la cartera que el generador produce por omisión, los 403 municipios con trabajo de campo dan 403
rutas con 2,968 paradas. El 2-opt acorta 119 de ellas, 10 llegan al tope de diez mejoras, y 140
tienen una sola parada. La primera, 21074, tiene 425 paradas y mide 226,184 m sintéticos, 14,956
menos que la del vecino más cercano: es la del ejemplo del paso 16 de
[flujo_completo.md](flujo_completo.md).

> **Las rutas de v0.4.0 no son rutas geográficas reales.** No representan calles, domicilios,
> tráfico, tiempos de conducción ni latitud y longitud. Son una simulación reproducible para
> demostrar la arquitectura, la optimización, la trazabilidad, las transacciones, la API y las
> pruebas.

**Por qué un plano sintético.** El repositorio no contiene ubicaciones reales. Inventar latitudes y
longitudes sería engañoso: parecerían puntos de un mapa y no lo serían. Agregar datos reales
violaría la regla del proyecto. Por eso cada municipio tiene un plano operativo sintético, que se
declara como tal.

**Prioridad no es secuencia.** Son dos cosas distintas, de dos motores distintos:

- `posicion_campo` (y `posicion_territorial` en la API de ruteo): la prioridad del municipio
  entre los municipios, de `territorial/v1`;
- `secuencia`: el orden de visita de cada cuenta dentro de su municipio, de `ruteo/v1`.

No hay gestores, vehículos, capacidades, horarios ni ventanas de tiempo: una ruta es la secuencia
de visita sobre todas las cuentas de campo de un municipio, no la jornada de una persona.
