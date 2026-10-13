# Ruteo vial v2 (`ruteo/v2`)

`ruteo/v2` toma la carga diaria que `campo/v2` le dio a cada gestor y dice **si cabe en una ruta por
calle**: en qué orden recorrerla saliendo de su base y regresando a ella, a qué hora llega a cada
cuenta (una ETA planificada, sin tráfico), qué distancia y qué duración tiene la ruta, y cuáles
cuentas no caben en la jornada o no son enrutables, y por qué. Rutea sobre una **versión declarada
de la red vial** (un extracto de OpenStreetMap preparado con OSRM v26.9.0 y servido por un
`osrm-routed` autoalojado) con la política `ruteo-public-v2`.

Es una **heurística determinista, no una ruta óptima**: el vecino más cercano por prioridad y un
2-opt acotado. No reasigna cuentas entre gestores ni entre bases (eso es de `campo/v2`), no usa
tráfico y no publica coordenadas, geometrías ni polilíneas. Corre en paralelo a `ruteo/v1`, que
sigue siendo la etapa RUTEO del flujo de cada corrida, sobre su plano sintético, sin cambios.

La política está en [politica_ruteo.md](politica_ruteo.md); la red vial, en
[red_vial.md](red_vial.md); el porqué, en las secciones 154 a 174 de
[decisiones.md](decisiones.md).

## Las entradas, siempre declaradas

Una ejecución se pide con:

- el `campo_run_id` de un plan de `campo/v2` `EXITOSA` (404 `CAMPO_V2_NO_ENCONTRADO`; 422
  `CAMPO_V2_NO_EXITOSO`);
- el `red_vial_version_id` de una versión de la red `PUBLICADA` (404 `RED_VIAL_NO_ENCONTRADA`; 422
  `RED_VIAL_NO_PUBLICADA`);
- opcionalmente, la `version_politica` (por omisión `ruteo-public-v2`; otra, 422
  `POLITICA_NO_SOPORTADA`).

Nunca se usa «el último plan» ni «la última red». Su **firma** (`firma_inputs`) es el SHA-256 del
motor, la política y su hash, el plan y su firma, la red, su huella y su manifiesto, la versión del
motor de ruteo y el hash del perfil: las mismas entradas son la misma ejecución (409
`RUTEO_V2_YA_GENERADO` si ya se publicó, `RUTEO_V2_EN_PROCESO` si se está resolviendo), y otra red u
otro plan, otra. La fecha de operación, la zona horaria y las ventanas de cada gestor son las del
plan.

## La ejecución: un trabajo durable `RUTEO_V2`

El `POST` no rutea: registra la ejecución `EN_PROCESO` y su trabajo `RUTEO_V2` en la misma
transacción (nivel 7 de la cola, después de `CAMPO_V2`). La resuelve un worker **cuyo proveedor de
ruteo responde**: un worker sin `MC_OSRM_URL`, o con un OSRM caído, no toma trabajos `RUTEO_V2` (los
deja en la cola sin gastar intentos) y sigue con los demás; la salud se pregunta a lo más cada
`MC_OSRM_SALUD_SEGUNDOS`. Con varios workers, cada trabajo lo toma uno solo (`FOR UPDATE SKIP
LOCKED`), con su lease y su latido; si su worker muere, otro lo retoma cuando vence el lease, y solo
publica el dueño vigente.

La ejecución pasa por estas fases, todas en una transacción y en un `SAVEPOINT`:

1. **Selección.** PostgreSQL da los gestores con al menos una cuenta `ASIGNADA` en el plan (su
   carga, sus ventanas y su base) y cada cuenta asignada con su prioridad y su punto, por conjuntos.
   Si no cuadran con lo que el plan contó, `DATOS_INCONSISTENTES`.
2. **La red.** La sonda de (0, 0) comprueba que el backend declara la red de la ejecución antes de
   enviarle un punto; después, el snap de las bases y, solo para las bases enrutables, el de las
   cuentas, por lotes de 100.
3. **Las matrices.** Una por gestor (su base y sus puntos únicos con snap válido), por bloques de a
   lo más 100 × 100, pedidas en paralelo (hasta `MC_OSRM_CONCURRENCIA` a la vez) mientras se planea
   la ruta del gestor anterior. Una matriz vive solo mientras se planea su ruta: no se guarda, se
   guarda su hash.
4. **La secuenciación, el 2-opt y la simulación** de cada ruta, con la política.
5. **El staging y la publicación.** Las rutas y las paradas van a tablas temporales de la
   transacción por `COPY`, y se publican con un `INSERT ... SELECT` cada una, en orden. Después se
   cuentan y se comprueba que cada parada sea de una cuenta `ASIGNADA` del plan, con su gestor y su
   prioridad, y que los conteos cuadren con los del plan.

Termina en un estado y un resultado:

| Resultado | Estado | Cuándo |
|---|---|---|
| `RUTAS_PUBLICADAS` | `EXITOSA` | Cada ruta y cada parada quedaron publicadas, todo o nada. |
| `RED_VIAL_BACKEND_INCOMPATIBLE` | `FALLIDA` | El backend declaró otra red (o ninguna). No se envió el punto de ninguna cuenta. |
| `VERSION_NO_SOPORTADA` | `FALLIDA` | El motor o la política de la ejecución no son los que este servicio aplica. |
| `ENTRADAS_NO_EXITOSAS` | `FALLIDA` | El plan o la red dejaron de estar `EXITOSA`/`PUBLICADA`. |
| `DATOS_INCONSISTENTES` | `FALLIDA` | Lo que se leyó o se publicó no cuadra con el plan. |
| `YA_GENERADO` | `FALLIDA` | Otra ejecución publicó la misma firma primero. |
| `ERROR_INTERNO` | `FALLIDA` | Un error inesperado: en la bitácora, su tipo, su SQLSTATE y sus marcos, nunca su mensaje. |
| `INTENTOS_AGOTADOS` | `FALLIDA` | La cola agotó los intentos de su trabajo. |

Mientras se resuelve, la ejecución está `EN_PROCESO`, sin resultado.

**Un fallo técnico del proveedor** (OSRM no responde, se cae a la mitad o responde algo incompleto)
no es un resultado: la ejecución no publica nada, sigue `EN_PROCESO`, y la cola la reintenta con su
espera hasta agotar sus intentos. Nunca se convierte en paradas `NO_ENRUTABLE` (decisión 162).

Una ejecución terminada no cambia ni se borra, y sus rutas y paradas no se reescriben (triggers
por sentencia); reconstruirla con las mismas entradas da exactamente lo mismo, con los mismos
`ruta_id`.

## Lo que se publica

- **`ejecucion_ruteo_v2`**: sus entradas, su firma, sus conteos (rutas por estado y paradas por
  estado), sus totales (distancia por calle, traslado, servicio y espera) y su distribución (por
  estado, por motivo, por prioridad, por base, el snap y lo que costaron las matrices, sin una sola
  celda).
- **`ruta_gestor_v2`**: una ruta por gestor con al menos una cuenta `ASIGNADA`, con su `ruta_id`,
  su base, su estado, su jornada, su salida de la base, su regreso (salida, llegada, distancia,
  duración y espera), sus totales, sus mejoras 2-opt, `hash_matriz` y `hash_secuencia`. Llave
  natural (ejecución, gestor), y una llave foránea compuesta a la carga de ese gestor en el plan, con
  su misma base.
- **`parada_ruta_v2`**: una parada por cuenta `ASIGNADA`, con su estado y su motivo, su prioridad,
  su snap, y si está programada, su orden de visita, la salida del lugar anterior, su llegada, su
  servicio, el tramo desde el anterior (distancia y duración) y su espera. Llave natural (ejecución,
  cuenta), y una llave foránea compuesta a la asignación de esa cuenta en el plan.

No hay una tabla de tramos: el tramo de llegada a cada parada vive en la parada y el de regreso, en
la ruta (decisión 172). Ninguna tabla tiene dónde guardar una coordenada, ni del punto, ni del
snap, ni del recorrido.

## La API

| Método y ruta | Qué devuelve |
|---|---|
| `GET /ruteo-v2/redes` | Las versiones de la red vial, la más reciente primero. |
| `GET /ruteo-v2/redes/{red_vial_version_id}` | Una versión, con los comandos de su preparación y el SHA-256 de cada archivo preparado. |
| `POST /ruteo-v2/ejecuciones` | Pide el ruteo de un plan sobre una red: `201` con la ejecución `EN_PROCESO` y su `Location`. |
| `GET /ruteo-v2/ejecuciones` | Las ejecuciones, filtrables por `campo_run_id`, `red_vial_version_id` y `estado`. |
| `GET /ruteo-v2/ejecuciones/{ruteo_run_id}` | Una ejecución: sus entradas, su política con su hash, su firma, sus conteos, sus totales y su distribución. |
| `GET /ruteo-v2/ejecuciones/{ruteo_run_id}/rutas` | Sus rutas, en el orden del código del gestor, filtrables por `gestor`, `base` y `estado`. |
| `GET /ruteo-v2/ejecuciones/{ruteo_run_id}/paradas` | Sus paradas, en el orden de la cuenta, filtrables por `estado`, `gestor`, `base` y `prioridad`. |
| `GET /ruteo-v2/rutas/{ruta_id}` | Una ruta: su gestor, su base, su estado, su salida, su regreso y sus totales. |
| `GET /ruteo-v2/rutas/{ruta_id}/paradas` | Sus paradas: las programadas en su orden de visita, y después las demás. |
| `GET /cuentas/{cuenta_id}/ruteo-v2` | Las paradas de una cuenta a través de las ejecuciones `EXITOSA`, de la fecha más reciente a la más antigua. |

Las horas salen en la zona horaria de la configuración de campo, con su desfase
(`2026-10-01T09:00:00-06:00`); en la base se guardan en UTC. La **Cuenta 360** trae su
`ruteo_resumen`: la parada de la cuenta en la ejecución `EXITOSA` más reciente (con `al`, la de esa
fecha o antes), con su ruta, su gestor, su estado, su motivo, su orden y su ETA. Cada respuesta de
una ejecución lleva su `aviso` (una heurística, sin tráfico, con un servicio de 900 s que es una
hipótesis), y ninguna lleva una coordenada, una geometría ni una polilínea.

## La línea de comandos

```bash
motor-cartera generar-red-sintetica --destino datos/red_vial/fuente/red_sintetica.osm
motor-cartera preparar-red-vial <fuente> --region "..." (--docker | --osrm-bin <dir>)
motor-cartera registrar-red-vial datos/red_vial/<huella> [--verificar-backend]
motor-cartera rutear-v2 --plan <campo_run_id> --red-vial-version <red_vial_version_id>
motor-cartera resumen-ruteo-v2 --ejecucion <ruteo_run_id>
motor-cartera backfill-ruteo-v2 --red-vial-version <red_vial_version_id> [--fecha-operacion AAAA-MM-DD] [--dry-run]
```

`rutear-v2` rutea en primer plano, con su propio trabajo en la cola, y necesita `MC_OSRM_URL`; sin
él termina con código 2. `backfill-ruteo-v2` encola una ejecución por cada plan `EXITOSA` (de esa
fecha, si se declara) que todavía no tenga una `EXITOSA` o `EN_PROCESO` con la misma firma sobre la
red declarada, que es obligatoria; es idempotente. Las salidas no llevan coordenadas.

## La privacidad

Las coordenadas de las cuentas solo salen de PostgreSQL hacia el proveedor de ruteo, por `POST`,
dentro de la red propia; nunca en la URL, nunca hacia un servicio público. El cliente no registra
URLs ni cuerpos, y sus errores no llevan la petición ni la causa original; la bitácora de accesos de
OSRM está apagada. Las tablas de paso que tienen los puntos son temporales de la transacción, y un
error se registra con su tipo, su SQLSTATE y sus marcos. Las pruebas mandan coordenadas en la URL
de un error del transporte y comprueban que no aparezcan en la bitácora, en la ejecución, en el
trabajo ni en la API.

## Índices y volumen

Los índices son los de sus consultas: en las paradas, la llave (ejecución, cuenta), (ejecución,
estado, cuenta), (ejecución, gestor, orden) y (cuenta, ejecución); en las rutas, la llave
(ejecución, gestor) y su `ruta_id`; en las ejecuciones, las únicas parciales por firma de la
`EXITOSA` y de la `EN_PROCESO`, y (plan, id). No hay particionado.

### Benchmark A: el planificador, con un proveedor sintético (no es una red vial)

Sobre el plan XL del benchmark de `campo/v2` (99,720 cuentas `ASIGNADA` de 5,523 gestores, en 13
bases), con un proveedor **SINTÉTICO** determinista que no es una red vial (la distancia en línea
recta por 1.3, a 25 km/h, con un snap y puntos sin camino que salen de un hash del punto, declarado
antes de ver ninguna ruta), en Windows 11 con 12 CPU y PostgreSQL 16:

- las 99,720 paradas se rutearon en **18.5 s** (5,386 por segundo), con 236 MiB de memoria pico y
  96 MiB de WAL; la publicación es casi la mitad (8.8 s) y la selección, 3.3 s; la secuenciación
  tomó 1.0 s, el 2-opt 0.3 s y la simulación 0.1 s;
- 1,801 rutas `COMPLETA`, 3,522 `PARCIAL` y 200 `SIN_PARADAS_ENRUTABLES` (una base sin camino en el
  modelo); 59,985 paradas `PROGRAMADA`, 34,274 `SIN_TIEMPO` y 5,461 `NO_ENRUTABLE`;
- borrar la ejecución y volver a pedirla dio el **mismo hash** de todo lo publicado, en 11.2 s;
- cada parada ocupa 547 bytes con sus índices (52 MiB por ejecución XL) y cada ruta, 832.

Las consultas por la API, con 25 repeticiones (mediana completa y tiempo dentro de PostgreSQL): una
página de paradas, 15.5 ms (0.6 ms); la página 500, 17.9 ms (3.6 ms); un estado, 15.7 ms (0.6 ms);
un gestor, 18.6 ms (1.8 ms); una base, 24.5 ms (7.5 ms, un conteo que recorre las paradas de sus
gestores); una ruta y sus paradas, 11.5 y 15.0 ms; las paradas de una cuenta, 16.6 ms (0.2 ms); una
página de rutas, 22.1 ms (6.2 ms); la Cuenta 360, 67 ms. La página 500 de las paradas tardaba 213 ms
dentro de PostgreSQL antes de elegir la página por sus índices y unirle después la cuenta, el
gestor y la ruta (decisión 174). Las rutas de una ejecución se ordenan por el código de su gestor,
así que una página las lee todas: crece con los gestores de la ejecución (6 a 8 ms con 5,523).

### Benchmark B: la red vial real

El mismo plan contra la red del estado de Puebla (el extracto estatal de OpenStreetMap France con
estado de replicación del 13 de octubre de 2026: 36.9 MiB y 1,739,426 aristas), preparada con los
binarios nativos de OSRM v26.9.0 y servida por `osrm-routed` en la misma máquina (lo que costó
prepararla está en [red_vial.md](red_vial.md)):

- las 99,720 paradas se rutearon en **16.8 s**, con 236 MiB de memoria pico y 98 MiB de WAL; las
  matrices tomaron 5.8 s (2,935 peticiones `table`, p50 6.5 ms y p95 10.8 ms) y el snap, 29
  peticiones `nearest` (p50 8 ms);
- 7 de las 11 bases con rutas no tienen snap válido: las del Estado de México y Veracruz y una de
  Tlaxcala, fuera del extracto (a entre 7 y 78 km de sus calles), y una de Puebla cuyo punto
  sintético quedó a 790 m de la calle más cercana. Sus 1,915 gestores quedan `BASE_NO_ENRUTABLE` y
  sus 35,640 paradas `NO_ENRUTABLE_BASE`, sin enviar ninguna al proveedor;
- de las 64,080 paradas de bases enrutables, 41,896 quedaron `PROGRAMADA`; 7,589 `SIN_TIEMPO` (3,945
  por `EXCEDE_JORNADA`: a una base que cubre todo el estado le tocan cuentas a más de una jornada de
  camino); y 14,595 `NO_ENRUTABLE`, 14,458 de ellas por `SNAP_EXCESIVO`, porque los puntos sintéticos
  caen al azar dentro de cada municipio, también lejos de toda calle (el snap de las paradas tiene
  una mediana de 84 m y un p95 de 1,993 m). Las rutas que salen de su base miden 76 km de mediana y
  349 km en el p95: `campo/v2` reparte capacidad, no distancia (decisión 141), y esto es lo que el
  ruteo hace visible;
- borrar la ejecución y volver a pedirla dio el mismo hash, en 14.0 s.

Las 64,080 paradas enviadas son solo 2,539 coordenadas distintas (la muestra sintética del
benchmark de geografía repite puntos dentro de cada municipio), así que cada matriz tuvo de 2 a 8
puntos. Para ver matrices más grandes, con 100 puntos distintos al azar en Puebla y un solo hilo,
OSRM responde una tabla de 10 × 10 en 20 ms, de 26 × 26 en 49 ms, de 50 × 50 en 111 ms y de 100 × 100
en 290 ms (medianas), y el snap de 100 puntos en 6 ms.

La primera corrida tuvo 4 reintentos: en Windows, una conexión persistente que OSRM cerró por
inactividad se veía como `ConnectionAbortedError`, que el cliente no reconocía como una conexión
cerrada, y gastaba un intento con su espera. Ya la reconoce, una prueba cubre cada forma en que se
ve una conexión cerrada, y la reconstrucción tuvo 0 reintentos.

## Lo que `ruteo/v2` no hace, a propósito

No usa tráfico, ventanas horarias por cuenta, duraciones de visita distintas, vehículos, capacidades
ni habilidades. No reasigna cuentas entre gestores ni entre bases, no optimiza todas las rutas a la
vez y no promete la mejor ruta: es una heurística determinista. No publica geometrías ni el
recorrido. No ejecuta la ruta ni registra lo que el gestor hizo (eso es el lifecycle), ni compara lo
planeado con lo hecho (analítica, v0.14). Y no abre una ejecución sin que alguien la pida.
