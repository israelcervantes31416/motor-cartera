# La política `ruteo-public-v2`

`ruteo-public-v2` es la política pública con que `ruteo/v2` decide, para la carga diaria de cada
gestor que publicó `campo/v2`, **en qué orden puede recorrerla por calle, a qué hora llega a cada
cuenta y cuáles caben en su jornada**. Es un núcleo puro (`src/motor_cartera/ruteo_v2/politica.py`):
no lee la base, el reloj, el azar ni la red; recibe la matriz que el motor le pidió al proveedor de
ruteo y devuelve la ruta. Es una **heurística determinista, no una ruta óptima**, y cada respuesta
lo dice.

El motor y su ejecución están en [ruteo_v2.md](ruteo_v2.md); la red vial, en
[red_vial.md](red_vial.md); el porqué de cada decisión, en las secciones 154 a 174 de
[decisiones.md](decisiones.md).

## Los parámetros

| Parámetro | Valor | Qué es |
|---|---|---|
| `version` | `ruteo-public-v2` | El nombre de la política, que entra en la firma. |
| `algoritmo` | `PRIORITY_NEAREST_NEIGHBOR_2OPT/v1` | La heurística (abajo). |
| `max_snap_distancia_m` | `500` | El snap más lejano que se acepta, al centímetro: 500.00 m sí, 500.01 m no. |
| `duracion_servicio_s` | `900` | Lo que dura cada visita, todas igual: una **hipótesis operacional pública**, no una medición. |
| `bloque_matriz` | `100` | Los puntos de cada lado de una petición de matriz: bloques de a lo más 100 × 100. |
| `max_mejoras_2opt` | `100` | Las mejoras 2-opt que se aceptan, a lo más, en cada segmento de una misma prioridad. |
| `perfil` | `car` | El perfil de OSRM con que se preparó la red (el `car.lua` de OSRM v26.9.0, tal cual). |
| inicio y fin | la base | La ruta es cerrada: sale de la base del gestor en `campo/v2` y regresa a ella. |

Su `hash_politica` es el SHA-256 de su definición completa (estos parámetros y cada regla de abajo,
como texto) en JSON canónico (llaves ordenadas, sin espacios, UTF-8):
`6afe7b063d0151adfbac89efe6c49979a9507dc7257175e3fa514c3ebf31b097`. Entra en la firma de cada
ejecución. Un servicio aplica solo las políticas que conoce: otra versión se rechaza
(`POLITICA_NO_SOPORTADA`) sin registrar nada. Los parámetros no se calibraron con resultados, y
cambiar uno es otra versión de la política, con otro hash y otra firma.

## La red: el snap, la conectividad y nada inventado

El proveedor de ruteo (OSRM, en [red_vial.md](red_vial.md)) dice, de cada punto, a qué distancia
está el segmento de la red más cercano (el **snap**), y de cada par de puntos, la duración y la
distancia por calle. La política no usa nada más:

- **La base primero.** Si la base del gestor no tiene snap, o lo tiene a más de 500.00 m, no hay
  ruta: la ruta queda `BASE_NO_ENRUTABLE` (con `SIN_SNAP` o `SNAP_EXCESIVO` como `motivo_base`) y
  cada parada `NO_ENRUTABLE_BASE`. El motor ni siquiera le manda al proveedor los puntos de esas
  cuentas.
- **Cada parada.** El snap se redondea al centímetro (`ROUND_HALF_UP`); sin snap es `NO_ENRUTABLE`
  por `SIN_SNAP`, y a más de 500.00 m, `NO_ENRUTABLE` por `SNAP_EXCESIVO`, con su distancia.
- **La conectividad.** Con snap, si la matriz no tiene duración de la base a la parada o de la
  parada a la base, la parada está en una componente desconectada: `NO_ENRUTABLE` por
  `RED_DESCONECTADA`.
- **Nada inventado.** Una celda vacía de la matriz no se rellena: no hay distancia en línea recta,
  ni una velocidad supuesta, ni un tramo "parecido". Las duraciones y las distancias de OSRM son
  decimales; se redondean a segundos y metros enteros (`ROUND_HALF_UP`) y de ahí en adelante todo
  es aritmética entera.
- **Un fallo técnico no es una parada no enrutable.** Si el proveedor no responde o responde algo
  incompleto, la ejecución no publica nada y sigue `EN_PROCESO` para que la cola la reintente
  ([ruteo_v2.md](ruteo_v2.md)): `NO_ENRUTABLE` es lo que la red dice de un punto, no lo que pasó
  con una petición.
- **Puntos repetidos.** Dos cuentas en la misma coordenada son un solo punto de la matriz: la base es
  el punto 0, y los demás van en el orden de la primera cuenta_id de cada coordenada.

## La jornada

Las ventanas del gestor en la fecha son las de su carga en `campo/v2` (su calendario, con sus
excepciones), en la zona horaria de la configuración de campo y guardadas en UTC.

- **Intervalos `[inicio, fin)`.** Una actividad que termina exactamente al cerrar una ventana cabe.
  Dos ventanas que se tocan (de 09:00 a 14:00 y de 14:00 a 19:00) son una sola; dos que se enciman
  son un error de la entrada.
- **Actividades enteras.** Cada traslado y cada servicio caben enteros en una ventana. Si no caben
  en la que está abierta, esperan a la siguiente **en el último lugar donde está el gestor**: en los
  huecos no se maneja ni se atiende.
- **La salida.** El gestor sale de la base al abrir su primera ventana, o en cuanto cabe su primer
  traslado. Lo que espera en la base antes de salir no es espera de la ruta.
- **El regreso se reserva.** Una parada solo se programa si, después de su servicio, el traslado de
  regreso a la base también cabe en una ventana. El regreso es parte de la ruta, con su salida, su
  llegada, su distancia, su duración y su espera.
- **La espera** de una parada es lo que el gestor esperó antes de salir hacia ella más lo que esperó
  al llegar antes de empezar el servicio: `(salida − libre) + (inicio − llegada)`.
- **Las ETA son planificadas**, sobre las duraciones estáticas de la red: sin tráfico, sin clima,
  sin incidentes y sin tiempos de estacionamiento. No son una promesa al cliente.

## La heurística `PRIORITY_NEAREST_NEIGHBOR_2OPT/v1`

1. **Los niveles.** Las paradas enrutables se agrupan por su prioridad de `campo/v2` (que es la de
   `decision/v2`): `MUY_ALTA`, `ALTA`, `MEDIA`, `BAJA`, en ese orden.
2. **Lo que no cabe ni solo.** Una parada que no cabe ni sola en la jornada vacía (de la base a ella,
   su servicio y el regreso) queda `SIN_TIEMPO` por `EXCEDE_JORNADA`, y **no bloquea** a las de
   prioridad inferior: no hay manera de atenderla ese día, con o sin las demás.
3. **El vecino más cercano.** Dentro de cada nivel, la siguiente parada es la de menor duración por
   calle desde donde está el gestor entre las que todavía caben, con su traslado, su servicio y el
   regreso a la base. Un empate se resuelve por `cuenta_id`: nunca por un id interno, el orden de
   una consulta o el azar.
4. **La prioridad manda.** Si una parada enrutable de un nivel no cabe, queda `SIN_TIEMPO` por
   `JORNADA_AGOTADA`, y ninguna de un nivel inferior se programa: quedan `SIN_TIEMPO` por
   `PRIORIDAD_SUPERIOR_AGOTO_JORNADA`, aunque alguna cupiera. Una cuenta `BAJA` cercana nunca le
   quita el lugar a una `MUY_ALTA` lejana.
5. **El 2-opt, después.** Con la secuencia construida, se mejora cada segmento de una misma
   prioridad por separado (nunca se cruzan prioridades): en cada pasada se busca la inversión de un
   tramo que más reduce la duración de traslado (la matriz es asimétrica: una calle de un solo
   sentido; la inversión se evalúa con sumas acumuladas en los dos sentidos), y se acepta solo si
   la reduce estrictamente **y** la ruta sigue cabiendo en la jornada. Si la mejor no cabe, se
   prueba la siguiente. Hasta 100 mejoras por segmento; un empate conserva la ruta.
6. **La simulación.** Con la secuencia final se calculan las horas de cada parada y del regreso.

### Los estados de una ruta

| Estado | Cuándo |
|---|---|
| `COMPLETA` | Todas sus paradas quedaron `PROGRAMADA`. |
| `PARCIAL` | Al menos una quedó `PROGRAMADA` o `SIN_TIEMPO` y no todas `PROGRAMADA`, con la base enrutable. |
| `SIN_PARADAS_ENRUTABLES` | Ninguna parada es enrutable: todas `NO_ENRUTABLE`. |
| `BASE_NO_ENRUTABLE` | La base no tiene snap válido: no hay ruta, y cada parada es `NO_ENRUTABLE_BASE`. |
| `SIN_JORNADA` | El gestor no tiene ventanas en la fecha (un caso defensivo: `campo/v2` no le asigna cuentas a quien no trabaja ese día). |

Solo existe una ruta por gestor con al menos una cuenta `ASIGNADA` en el plan.

### Los estados y motivos de una parada

| Estado | Motivo | Qué dice |
|---|---|---|
| `PROGRAMADA` | — | Tiene orden de visita, ETA, servicio y el tramo desde la anterior. |
| `SIN_TIEMPO` | `JORNADA_AGOTADA` | Es enrutable, pero ya no cabía después de las programadas. |
| `SIN_TIEMPO` | `PRIORIDAD_SUPERIOR_AGOTO_JORNADA` | Una de mayor prioridad no cupo: su nivel no se programó. |
| `SIN_TIEMPO` | `EXCEDE_JORNADA` | No cabe ni sola en la jornada vacía. |
| `SIN_TIEMPO` | `SIN_JORNADA` | El gestor no tiene jornada ese día. |
| `NO_ENRUTABLE` | `SIN_SNAP` | El punto no tiene un segmento de la red. |
| `NO_ENRUTABLE` | `SNAP_EXCESIVO` | El segmento más cercano está a más de 500.00 m (se publica su distancia). |
| `NO_ENRUTABLE` | `RED_DESCONECTADA` | Tiene snap, pero no hay camino de la base a ella o de ella a la base. |
| `NO_ENRUTABLE_BASE` | `BASE_NO_ENRUTABLE` | La base del gestor no tiene snap válido. |

## La identidad, los hashes y la reconstrucción

- **La firma** de una ejecución es el SHA-256 de: el motor (`ruteo/v2`), la versión y el hash de la
  política, el `campo_run_id` y la firma del plan, el `red_vial_version_id` y la huella de la red,
  el hash del manifiesto de la red, la versión del motor de ruteo (`v26.9.0`) y el hash del perfil.
  Las mismas entradas son la misma ejecución; otra red, otro plan u otra política, otra.
- **El `ruta_id`** de cada ruta es un UUID v5 de la firma y del gestor: reconstruir una ejecución
  da los mismos identificadores.
- **`hash_matriz`** es el SHA-256 de la matriz que usó la ruta (los puntos como `BASE` y la
  cuenta_id de cada parada con snap válido, en orden, con sus duraciones y sus distancias). La
  matriz no se guarda ni se publica: se guarda su hash, para saber si dos ejecuciones vieron la misma
  red.
- **`hash_secuencia`** es el SHA-256 de la ruta resuelta: el estado de cada parada, su orden, sus
  horas y sus tramos.

Con las mismas entradas, la política da exactamente lo mismo: las pruebas lo comprueban con
propiedades sobre matrices al azar contra un simulador independiente, con el orden de entrada
barajado, y reconstruyendo una ejecución publicada.

## Ejemplos

Son las pruebas de `tests/test_ruteo_v2_politica.py`, con una jornada de 09:00 a 19:00 salvo que se
diga otra.

- **El vecino más cercano.** A está a 10 min de la base, B a 5 de A, C a 10 de B y el regreso de C,
  a 20; D tiene snap pero no camino. La ruta es base-A-B-C-base: A de 09:10 a 09:25, B de 09:30 a
  09:45, C de 09:55 a 10:10 y la base a las 10:30; D queda `NO_ENRUTABLE` por `RED_DESCONECTADA` y la
  ruta es `PARCIAL`. El 2-opt no cambia nada: base-C-B-A-base empata (2,700 s de traslado) y un
  empate conserva la ruta.
- **La prioridad.** De 09:00 a 09:55: la `MUY_ALTA` (09:05 a 09:20) y la `ALTA` (09:25 a 09:40)
  regresan a las 09:45; la `MEDIA` terminaría su servicio a las 09:56:40 y queda `SIN_TIEMPO` por
  `JORNADA_AGOTADA`.
- **El turno partido.** De 09:00 a 14:00 y de 16:00 a 19:00, P1 a 3 h de la base y P2 a 2 h de P1:
  P1 de 12:00 a 12:15; el traslado a P2 no cabe antes de las 14:00, así que el gestor espera en P1
  y sale a las 16:00: P2 de 18:00 a 18:15 (con 13,500 s de espera) y la base a las 18:45. Nada se
  maneja ni se atiende de 14:00 a 16:00.
- **El regreso reservado.** De 09:00 a 10:00, Q a 1 min y P a 20 min de la base y de Q: después de
  Q, P terminaría su servicio a las 09:51, dentro de la jornada, pero su regreso llegaría a las
  10:11: queda `SIN_TIEMPO`. Sola sí cabría (regresaría a las 09:55).
- **El 2-opt.** Con una matriz asimétrica, el vecino más cercano hace A-C-B (1,700 s de traslado); el
  2-opt la cambia por A-B-C (600 s) y recalcula la jornada: la base a las 09:55.
- **El 2-opt no rompe la jornada.** De 09:00 a 10:20 y de 10:40 a 12:15, la mayor mejora de A-B-C
  (B-A-C) ya no cabe; se acepta la siguiente que sí (C-B-A), y después B-C-A.

## Lo que la política no hace

No usa tráfico ni horarios por cuenta (ventanas del cliente), no distingue tipos de visita ni
duraciones distintas, no tiene vehículos, cargas ni habilidades, no reparte cuentas entre gestores o
bases (eso es de `campo/v2`, y no cambia), no planea varios días, no optimiza todas las rutas a la
vez ni promete la mejor ruta posible. Esas preguntas son de otras versiones: la optimización
matemática es la v0.16.
