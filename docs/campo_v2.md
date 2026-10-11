# Campo v2 (`campo/v2`)

`decision/v2` dice qué cuentas van a campo y con qué prioridad ([decision_v2.md](decision_v2.md));
`territorial/v2`, a qué base operativa pertenece cada una ([territorial_v2.md](territorial_v2.md)).
`campo/v2` responde, para una fecha de operación:

> Con una decisión/v2, una asignación de territorial/v2 y una configuración de campo declaradas,
> ¿qué candidata de campo queda con qué gestor, en qué unidad de la partición operativa, y por qué?

Es **capacidad declarada, no factibilidad vial**: un gestor recibe hasta su cupo declarado de
cuentas al día ([configuracion_campo.md](configuracion_campo.md)), y cada cuenta queda en una zona,
una subzona, un tramo y un microtramo de una cuadrícula fija
([particion_campo.md](particion_campo.md)). No usa distancias, tiempos de traslado, rutas ni la
duración de una visita: el orden de las visitas y su factibilidad son de v0.13.

```
EjecucionDecisionV2 (EXITOSA) ──┐  el canal y la prioridad de cada cuenta
EjecucionTerritorialV2 (EXITOSA) ┤  su base, sobre una versión de bases
VersionConfiguracionCampo ──────┘  los gestores de esas bases, sus calendarios y sus cupos
            │
EjecucionCampoV2        un plan: sus entradas y su fecha, la política con su hash, la firma de
      │                 entrada, los conteos, la capacidad y la distribución
      ├── AsignacionCampoCuenta     cada candidata: su estado, su base, su gestor, sus cuatro
      │                             unidades, su lugar en la cola de su base y los motivos
      ├── UnidadTerritorialCampo    cada unidad ocupada: su nivel, su padre y sus conteos
      └── CargaGestorCampo          cada gestor: su jornada en la fecha, su capacidad y su carga
```

El código vive en `src/motor_cartera/campo_v2/`: `politica.py` es el núcleo puro (la política, su
hash, la cuadrícula, el ranking y el reparto); `configuracion.py`, la configuración de campo;
`ejecuciones.py`, el plan en PostgreSQL; `consultas.py`, lo que leen la API y la línea de comandos;
`backfill.py`, los planes que faltan.

## Las entradas, siempre declaradas

Un plan declara exactamente sus entradas. Nunca toma "la última decisión", "la última asignación",
"los gestores actuales" ni "mañana": **la fecha de operación tampoco se infiere**.

| Entrada | Qué es | Si no sirve |
|---|---|---|
| `corte_id` | El corte canónico. | `404 CORTE_NO_ENCONTRADO` |
| `decision_run_id` | Una `decision/v2` `EXITOSA` de ese corte: el universo y la prioridad. | `404 DECISION_NO_ENCONTRADA`, `422 DECISION_NO_EXITOSA`, `422 CORTE_INCONSISTENTE` |
| `territorial_run_id` | Una `territorial/v2` `EXITOSA` de ese corte: la base de cada cuenta. | `404 TERRITORIAL_NO_ENCONTRADO`, `422 TERRITORIAL_NO_EXITOSA`, `422 CORTE_INCONSISTENTE` |
| `configuracion_campo_version_id` | Una versión de la configuración de campo **sobre la misma versión de bases** que esa `territorial/v2`. | `404 CONFIGURACION_NO_ENCONTRADA`, `422 BASES_INCONSISTENTES` |
| `fecha_operacion` | El día que se planea: el del corte o uno posterior. | `422 FECHA_ANTERIOR_AL_CORTE`; sin ella, `422 ENTRADA_INVALIDA` |
| `version_politica` | `campo-public-v2`, por omisión y la única. | `422 POLITICA_NO_SOPORTADA` |

Con ellas sale su `firma_inputs`: el SHA-256 del motor, la política con su hash, el corte, la
decisión con su firma, la territorial/v2 con su firma, la configuración con su hash y la fecha. Las
mismas entradas son el mismo plan (`409 CAMPO_V2_YA_GENERADO`; mientras se resuelve,
`409 CAMPO_V2_EN_PROCESO`). Otra fecha, otra configuración u otra decisión son otro plan, y el
anterior no cambia.

## La política `campo-public-v2`

1. **El universo** son las cuentas que `decision/v2` mandó a canal `CAMPO`. `campo/v2` no vuelve a
   decidir el canal ni la prioridad, y no inventa otro puntaje.
2. Una candidata cuya asignación de `territorial/v2` no es `ASIGNADA` no se fuerza a un gestor:
   queda `REVISION_TERRITORIAL`, con el estado y los motivos territoriales. No se le busca otra base.
3. Las demás se ubican en su **partición**: zona, subzona, tramo de 5 km y microtramo de 1 km de una
   cuadrícula fija en EPSG:6372 ([particion_campo.md](particion_campo.md)).
4. Los **gestores disponibles** de su base en la fecha son los activos con jornada ese día. Si la
   base no tiene ninguno, sus candidatas quedan `SIN_GESTOR_DISPONIBLE`. **Una cuenta no se lleva a
   otra base**, aunque otra tenga capacidad libre: la pertenencia la decide `territorial/v2`.
5. **Una cuenta es una unidad de capacidad**, y la de un gestor disponible es su cupo declarado. En
   cada base se reparte por **prioridad** (`MUY_ALTA`, `ALTA`, `MEDIA`, `BAJA`) y, dentro de cada
   prioridad, por **bloques**: las candidatas de un mismo microtramo. Primero los bloques con más
   cuentas (la densidad primero: un gestor atiende más cuentas de una vez en un mismo kilómetro
   cuadrado), y entre iguales, el de identificador de microtramo menor.
6. Cada bloque va **completo a su propietario preferido**: el primero del ranking Rendezvous de su
   microtramo. Si no le cabe completo, va completo al primero del ranking al que sí le cabe
   (**spillover**: la cuenta queda **rebalanceada**). Si no cabe completo en ninguno, se divide en el
   orden del ranking, cuenta por cuenta en el orden de su `cuenta_id`. Lo que no cabe en nadie queda
   `SIN_CAPACIDAD`.

| Dato | Valor |
|---|---|
| Versión del motor | `campo/v2` |
| Política | `campo-public-v2` |
| Cuadrícula | EPSG:6372; tramos de 5,000 m y microtramos de 1,000 m; cada coordenada al milímetro |
| Capacidad | El cupo declarado de cada gestor disponible; una cuenta, una unidad; no se reduce en media jornada |
| Ranking | SHA-256 de `microtramo_id\|codigo` de cada gestor disponible de la base, el mayor primero; un empate, por código |
| `hash_politica` | `b8d51c1441cc93a7b0d22416ac258bb134c70608bfef5ab1e37a6d7c59ecd854`, el SHA-256 de su definición en JSON canónico (`politica.definicion`) |

### El ranking Rendezvous (HRW)

El propietario preferido de un microtramo es el gestor disponible de la base con el mayor
SHA-256 de `microtramo_id|codigo`: un **hashing de mayor peso aleatorio** (Rendezvous), sin pesos.
No depende del orden en que lleguen los gestores, ni del de la base de datos, ni de cuántas cuentas
haya, y tiene la propiedad que lo justifica: **agregar un gestor solo cambia el propietario
preferido de los microtramos en los que el nuevo queda primero** (en promedio, 1/n de ellos con n
gestores), y quitar uno solo mueve los suyos. Por capacidad pueden moverse algunas cuentas más, las
que el spillover recorre; en el benchmark, agregar un gestor a una base de 387 cambió 2 microtramos
y 175 de 99,720 cuentas asignadas.

No se pondera por cupo: con pesos, la propiedad de estabilidad se conserva, pero un cambio de cupo
movería microtramos de todos los gestores, y la capacidad ya se respeta con el spillover.

### La prioridad nunca se invierte

Dentro de una base, una cuenta de menor prioridad solo recibe gestor si todas las de mayor prioridad
de esa base ya lo recibieron: un bloque se parte solo si no cabe completo en nadie, y queda
`SIN_CAPACIDAD` solo lo que no cabe en la capacidad que sobra de toda la base. Si una cuenta queda
sin capacidad, la base ya no tiene cupo para ninguna otra. Entre bases no hay comparación: cada base
reparte su propia capacidad. El `orden_prioridad` de cada cuenta es su lugar en la cola de su base
(1 es la primera en recibir capacidad), y no cambia por el gestor que le tocó.

### Los estados

| Estado | Qué quiere decir |
|---|---|
| `ASIGNADA` | Con base y con gestor: su propietario preferido, o el siguiente del ranking con cupo (rebalanceada). |
| `REVISION_TERRITORIAL` | Su asignación de `territorial/v2` no es `ASIGNADA`: sin base, sin unidades y sin gestor. |
| `SIN_GESTOR_DISPONIBLE` | Su base no tiene ningún gestor activo con jornada en la fecha. Con sus unidades. |
| `SIN_CAPACIDAD` | Su base tiene gestores disponibles, pero su capacidad se agotó antes de llegar a la cuenta. Con sus unidades. |

### Los motivos

| Motivo | Detalle | Cuándo |
|---|---|---|
| `TERRITORIAL_NO_ASIGNADA` | `estado_territorial`, `motivos_territoriales` | `territorial/v2` no la asignó a una base. |
| `SIN_GESTOR_DISPONIBLE` | `base`, `gestores`, `inactivos`, `sin_jornada`, `cerrados` | Su base no tiene gestores disponibles ese día, y por qué. |
| `CAPACIDAD_AGOTADA` | `base`, `capacidad_base`, `gestores`, `propietario_preferido` | La capacidad de su base se agotó. |
| `PROPIETARIO_PREFERIDO` | `gestor`, `cuentas_bloque`, `dividido` | Quedó con el primero del ranking de su microtramo. |
| `SPILLOVER_POR_CAPACIDAD` | `gestor`, `propietario_preferido`, `lugar`, `cuentas_bloque`, `dividido` | A su propietario preferido no le cabía; quedó con el siguiente del ranking que tenía cupo (`lugar` es el del gestor en el ranking). |

### La jornada de cada gestor en la fecha

| Estado de la jornada | Qué quiere decir |
|---|---|
| `JORNADA_SEMANAL` | Activo, con las ventanas de ese día de la semana. Disponible. |
| `VENTANAS_ESPECIALES` | Activo, con las ventanas de una excepción de su calendario en esa fecha. Disponible. |
| `SIN_JORNADA` | Activo, pero su calendario no tiene ventanas ese día de la semana. |
| `CERRADO_POR_EXCEPCION` | Activo, pero su calendario está cerrado esa fecha (un festivo). |
| `INACTIVO` | Declarado inactivo en la configuración. |

### Los niveles de la partición

| Nivel | Qué es |
|---|---|
| `ZONA` | La base y la entidad del municipio espacial de la cuenta. |
| `SUBZONA` | La base y el municipio espacial. |
| `TRAMO` | La celda de 5 km de la cuadrícula, dentro de su municipio y su base. |
| `MICROTRAMO` | La celda de 1 km, dentro de su tramo; con su propietario preferido. |

## El plan: por conjuntos, todo o nada

Lo piden `POST /campo-v2/planes` y `motor-cartera planificar-campo-v2`, y lo resuelve un worker con
su trabajo `CAMPO_V2` de la cola durable (`FOR UPDATE SKIP LOCKED`, lease, latido y reintentos), en
una transacción y con la fila del plan bloqueada:

1. **Selección**: las decisiones de canal `CAMPO`, por su índice, sin recorrer las demás.
2. **Territorial**: su asignación de `territorial/v2` y, de las `ASIGNADA`, su base, su municipio y
   el punto de su geografía publicada: el hecho geográfico no se vuelve a calcular.
3. **Partición**: cada punto a EPSG:6372 y su celda de microtramo, en PostgreSQL.
4. **Bloques**: las candidatas agrupadas por (base, municipio, celda, prioridad); cada unidad recibe
   su identificador en Python, una vez por celda ocupada, y vuelve a la base por `COPY`.
5. **Capacidad**: la jornada y la capacidad de cada gestor en la fecha (por gestor, no por cuenta),
   y el reparto de los bloques de cada base con `campo-public-v2`, en Python: los bloques son
   órdenes de magnitud menos que las cuentas, y el reparto vuelve a la base por `COPY`.
6. **Publicación**: con `INSERT ... SELECT`, la asignación de cada candidata, las unidades que
   ocupó y la carga de cada gestor.
7. **Conteos**: lo publicado, contado en la misma transacción; si no cuadra con la decisión, no se
   publica nada.

| Estado de un plan | Qué quiere decir |
|---|---|
| `EN_PROCESO` | Registrado con su trabajo `CAMPO_V2`; un worker no lo ha terminado y no publica nada todavía. |
| `EXITOSA` | Publicó la asignación de cada candidata, sus unidades y sus cargas: `PLAN_PUBLICADO`. |
| `FALLIDA` | No publicó nada; su resultado dice por qué. |

Si algo falla, se revierte todo y el plan queda `FALLIDA`: nunca un plan a medias. Solo publica el
dueño vigente de su trabajo: un worker que perdió su lease no cierra el plan de otro. Ninguna fase
recorre las cuentas una por una desde Python, ni hace una consulta por cuenta. `politica.asignar` es
el espejo en Python del SQL, y las pruebas comparan cada asignación publicada con lo que concluye la
política, cuenta por cuenta; borrar lo derivado de un plan y volver a pedirlo con las mismas entradas
publica exactamente lo mismo.

| Resultado de un plan | Qué quiere decir |
|---|---|
| `PLAN_PUBLICADO` | `EXITOSA`: publicó la asignación de cada candidata de campo de la decisión. |
| `YA_GENERADO` | `FALLIDA`: otro plan publicó primero la misma firma de entrada. |
| `VERSION_NO_SOPORTADA` | `FALLIDA`: pide un motor, una política o un hash de política que este servicio no aplica. |
| `ENTRADAS_NO_EXITOSAS` | `FALLIDA`: su decisión o su territorial/v2 ya no está `EXITOSA`. |
| `DATOS_INCONSISTENTES` | `FALLIDA`: una candidata sin asignación territorial, una asignada sin punto, o conteos que no cuadran. |
| `ERROR_INTERNO` | `FALLIDA`: un error inesperado; el detalle no lo expone. |
| `INTENTOS_AGOTADOS` | `FALLIDA`: la cola agotó sus intentos. |
| `CERRADA_AL_BAJAR` | `FALLIDA`: estaba en curso cuando la base se bajó a la 0012. |

Lo publicado no se reescribe: un trigger rechaza cualquier `UPDATE` o `DELETE` sobre las
asignaciones, las unidades y las cargas, y otro, cualquier cambio a un plan terminado.

## La carga de cada gestor

Cada plan publica, para cada gestor de la configuración (también los que no estaban disponibles), su
estado de jornada en la fecha, su capacidad efectiva (su cupo, o 0), cuántas cuentas recibió,
cuántas por spillover, en cuántos microtramos y tramos, su utilización (asignadas entre capacidad;
nula sin capacidad) y sus ventanas del día: la hora local y el instante UTC de cada inicio y cada fin
(`inicio_en`, `fin_en`).

La distribución del plan dice cuántas candidatas quedaron en cada estado, por motivo, por base (con
sus gestores disponibles, su capacidad y su utilización), por nivel de unidad y por estado de
jornada, y el **balance** entre los gestores disponibles: mínimo, mediana, p95 y máximo de su
utilización, de sus cuentas, de sus microtramos y de sus rebalanceadas, y el coeficiente de
variación de sus cuentas. **Es un diagnóstico, no un ajuste**: nada se recalcula para emparejarlo.

## El backfill

`motor-cartera backfill-campo-v2 --configuracion <id> --fecha-operacion <fecha>` pide los planes que
faltan para una configuración y una fecha declaradas: de cada corte de la cartera hasta esa fecha
que tiene **exactamente una** `decision/v2` `EXITOSA` y **exactamente una** `territorial/v2`
`EXITOSA` sobre la versión de bases de la configuración, y todavía no tiene un plan con esas
entradas. Un corte con más de una es **ambiguo**: se reporta y no se planea, porque elegir una sería
inventar la entrada (se declara con `planificar-campo-v2`). Es idempotente, y `--dry-run` solo dice
lo que falta.

## La API

| Método y ruta | Qué hace |
|---|---|
| `POST /campo-v2/planes` | Pide el plan de una fecha con sus entradas declaradas. `201` quiere decir creado, no terminado. Errores: los de la tabla de entradas, y `409 CAMPO_V2_EN_PROCESO` o `409 CAMPO_V2_YA_GENERADO`. |
| `GET /campo-v2/planes` | Los planes; con `corte_id` o `fecha_operacion`, los de ese corte o ese día. |
| `GET /campo-v2/planes/{campo_run_id}` | Un plan: sus entradas, la política con su hash y su cuadrícula, la firma, los conteos, la capacidad y la distribución. `404 CAMPO_V2_NO_ENCONTRADO`. |
| `GET /campo-v2/planes/{campo_run_id}/asignaciones` | La asignación de cada candidata, en el orden de la cuenta. Con `estado`, `base`, `gestor`, `prioridad`, `zona`, `subzona`, `tramo` o `microtramo`, por su índice. |
| `GET /campo-v2/planes/{campo_run_id}/cargas` | La carga de cada gestor, en el orden de su código; con `base`, los de esa base. |
| `GET /campo-v2/planes/{campo_run_id}/unidades` | Las unidades que ocupó el plan; con `nivel`, `padre` o `base`. |
| `GET /cuentas/{cuenta_id}/campo-v2` | Los planes de una cuenta, de la fecha de operación más reciente a la más antigua, cada uno con sus entradas. |

La Cuenta 360 trae `campo_resumen`: la asignación del plan `EXITOSA` más reciente de la cuenta (con
`al`, el más reciente hasta ese día): su fecha, su estado, su prioridad, su base, el código de su
gestor y sus cuatro unidades. Ninguna respuesta lleva una coordenada, el índice de una celda, un
recuadro ni un centroide.

```bash
uv run motor-cartera planificar-campo-v2 --corte 2026-09-30 --decision-run <decision_run_id> \
  --territorial-run <territorial_run_id> --configuracion <configuracion_campo_version_id> \
  --fecha-operacion 2026-10-01
uv run motor-cartera resumen-campo-v2 --plan <campo_run_id>
uv run motor-cartera exportar-plan-campo --plan <campo_run_id> --destino datos/plan_campo.csv
uv run motor-cartera backfill-campo-v2 --configuracion <id> --fecha-operacion 2026-10-01 --dry-run
```

`planificar-campo-v2` sin `--fecha-operacion` termina con código 2: no infiere la fecha.
`exportar-plan-campo` escribe con `COPY` el `cuenta_id` público, el estado, la prioridad, el lugar en
la cola, la base, el gestor y las cuatro unidades de cada candidata: sin coordenadas, nombres,
direcciones ni teléfonos.

## Índices y volumen

En `asignacion_campo_cuenta`, la llave (plan, cuenta) y (plan, estado, cuenta), (plan, base,
cuenta), (plan, gestor, cuenta), (plan, prioridad, cuenta), (plan, microtramo, cuenta) y (cuenta,
plan): una página de un estado, de una base, de un gestor, de una prioridad o de un microtramo en el
orden de la cuenta, y los planes de una cuenta. Una página de una zona, una subzona o un tramo entra
por (plan, base, cuenta), con la base de su unidad, y filtra la unidad: una base es un tramo del
índice mucho más chico que el plan. Con un solo filtro, el total de una página de un plan publicado
sale de lo que el plan contó al publicar. En `unidad_territorial_campo`, la llave (plan, unidad) y
(plan, padre); en `carga_gestor_campo`, la llave (plan, gestor); en `gestor_campo`,
(configuración, base, activo), y en `ventana_jornada_campo`, (calendario, día): la jornada de
cada gestor, las ventanas de cada carga y el trigger que no deja encimarlas buscan las ventanas
de un calendario, y la tabla crece con cada versión de la configuración. Ni un índice más; no hay
particionado (decisión 153).

### Benchmark: 500,000 cuentas

Sobre el corte de 500,000 cuentas sintéticas del benchmark de [geografia.md](geografia.md), con su
`territorial/v2` (421,548 cuentas `ASIGNADA` en 12 bases activas) y una `decision/v2` del corte
tomada una sola vez (270.1 s): 132,529 candidatas de canal `CAMPO`, 111,631 de ellas asignadas a una
base y 20,898 en revisión territorial. Antes de planear, el script declara la configuración: a cada
base activa, en el orden de su código, un régimen que se repite (capacidad para 1.25 veces su
demanda, para 1.00 y para 0.50), salvo la última, sin gestores; cupos de 15, 20 y 25, un 5 % de
inactivos y una semilla fija. Son 6,129 gestores (5,803 activos), con 116,150 de capacidad
declarada, para el jueves 2026-04-02, día en que trabajan los tres calendarios del generador.

- **El plan tomó 65.5 s** (2,024 candidatas por segundo), con 182 MiB de memoria pico y 557 MiB de
  WAL: 99,720 `ASIGNADA` (78,852 por spillover), 20,898 `REVISION_TERRITORIAL`, 1,949
  `SIN_GESTOR_DISPONIBLE` (la base sin gestores) y 9,962 `SIN_CAPACIDAD` (las bases saturadas, y 28
  de una que quedó justo por debajo de su demanda); 12 zonas, 692 subzonas, 4,976 tramos y 9,122
  microtramos.
- **Cada régimen hizo lo declarado.** Con sobrante, utilización de 0.80 a 0.82 y ninguna sin
  capacidad; en el límite, de 0.98 a 1.00; saturadas, 1.00 y la mitad de sus candidatas sin
  capacidad.
- **El balance entre los 5,803 gestores disponibles** (un diagnóstico): utilización mínima 0.00,
  mediana 1.00, p95 1.00 y máxima 1.00; cuentas por gestor 0, 20, 25 y 25; microtramos por gestor 0,
  1, 4 y 22; coeficiente de variación de las cuentas, 0.39. El reparto llena a los gestores hasta su
  cupo en el orden del ranking: en una base con sobrante quedan gestores llenos y otros sin cuentas,
  no todos al 80 %. Emparejar cargas no es de esta política.
- **El spillover es alto por los datos, no por la regla.** Los puntos sintéticos del benchmark son
  20 por municipio, así que un microtramo llega a tener cientos de cuentas: ningún cupo de 15 a 25
  lo contiene, y se divide a lo largo de su ranking. En una cartera con ubicaciones dispersas, los
  bloques son mucho más chicos.

| Fase | Segundos |
|---|---:|
| Selección de las candidatas | 0.7 |
| Su asignación territorial y su punto | 3.2 |
| La partición (EPSG:6372) | 0.9 |
| Los bloques y sus unidades | 0.5 |
| La capacidad y el reparto | 11.7 |
| La publicación (`INSERT ... SELECT`) | 47.9 |
| Los conteos | 0.5 |

**Agregar un gestor.** La configuración B es la A con un gestor más en la base saturada con más
demanda (387 gestores disponibles en A, 388 en B), y su plan tomó 32.7 s. De los 8,603 microtramos
con propietario en los dos planes, **2 cambiaron de propietario preferido (0.02 %)**, los dos de esa
base (2 de 798, el 0.25 %; lo esperado con Rendezvous es 1/388, el 0.26 %) y los dos al gestor
nuevo; en las demás bases, ninguno. Con un reparto por turnos (el hash del microtramo módulo el
número de gestores), cambiarían 796 de los 798. De las 99,720 cuentas asignadas en los dos planes,
175 cambiaron de gestor (0.18 %): las de esos dos microtramos y las que el spillover recorrió; 20
ganaron gestor (el cupo nuevo) y ninguna lo perdió.

Cada asignación ocupa 617 bytes con sus índices: unos 78 MiB por plan de 132,529 candidatas. La base
creció 668 MiB con todo el benchmark; la mayor parte es la `decision/v2` del corte, y los dos planes
con sus unidades y sus cargas, unos 170 MiB.

**Las consultas no recorren el plan.** Por la API, con la serialización (mediana): los planes de
una cuenta, 17 ms; la Cuenta 360 con su `campo_resumen`, 62 ms (24 sentencias, 1.6 ms dentro de
PostgreSQL); una página de un gestor, 20 ms; de una base, 19 ms, y su página 10, 21 ms;
`SIN_CAPACIDAD`, 18 ms; un microtramo, 18 ms; un tramo, 19 ms; una zona, 18 ms; las cargas, 27 ms;
el plan, 11 ms. Ninguna hace un Seq Scan sobre las asignaciones. Las cargas de un plan y el conteo
de los microtramos de una base leen `carga_gestor_campo` y `unidad_territorial_campo` con un Seq
Scan, porque con dos planes cada uno es la mitad de su tabla: 9.1 y 4.1 ms, contra 13.6 y 5.8 ms
por la llave del plan, que entra en cuanto la tabla tenga más planes.

## Lo que `campo/v2` no hace, a propósito

- **Rutas**: ni OSRM, ni distancias por calle, ni tiempos de traslado, ni el orden de las visitas,
  ni TSP o VRP, ni vecino más cercano o 2-opt. Eso es v0.13 (Ruteo vial v2), que leerá estos planes.
- **Factibilidad**: el cupo es una declaración; que un gestor pueda visitar sus cuentas en su
  jornada no se comprueba aquí.
- **Balancear bases**: una cuenta nunca se lleva a otra base, aunque esta tenga capacidad libre.
- **Reemplazar a `territorial/v1` o a `ruteo/v1`** en el flujo de cada corrida: corre en paralelo.
- **Pesos por visita, habilidades, vehículos o costos**: cada cuenta pesa una unidad.
