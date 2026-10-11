"""La aplicacion FastAPI.

Se construye con `crear_app()` y se sirve con
`uvicorn --factory motor_cartera.api.app:crear_app`. Asi importar el modulo no exige
configuracion, y cada prueba construye su propia app con su propia clave.

La API no ejecuta ningun motor: registra el trabajo en la cola durable y responde. Los ejecuta el
worker, en otro proceso (`motor-cartera worker`).
"""

from __future__ import annotations

from fastapi import Depends, FastAPI

from motor_cartera import __version__
from motor_cartera.api import (
    acuerdos,
    atribuciones,
    bases,
    campo,
    campo_v2,
    cartera,
    corridas,
    cuentas,
    decisiones,
    decisiones_v2,
    evaluaciones,
    geografia,
    gestiones,
    historia,
    lifecycle,
    motor_pagos,
    movimientos,
    orquestacion,
    pagos,
    ruteo,
    salud,
    territorial,
    territorial_v2,
)
from motor_cartera.api.errores import registrar_manejadores
from motor_cartera.api.seguridad import exigir_api_key
from motor_cartera.config import Config
from motor_cartera.config import config as config_del_entorno

DESCRIPCION = """
Ingesta, validacion, resumen, decision por cuenta, organizacion territorial y ruteo sintetico
de una cartera de credito al consumo. **Todos los datos son sinteticos**: los produce el generador
del proyecto.

**Como trabaja.** La API no ejecuta ningun motor. Cada `POST` que pide trabajo registra el recurso
`EN_PROCESO` y su trabajo en una cola durable, los dos en PostgreSQL y en la misma transaccion, y
responde `201` de inmediato, con el recurso en `Location`. Un worker aparte toma el trabajo, ejecuta
el motor y lo cierra; el cliente consulta `Location` hasta que el estado deje de ser `EN_PROCESO`.
**201 quiere decir que el recurso se creo, no que el motor termino.**

Si un worker muere a la mitad, su trabajo no se pierde: cuando vence su lease, otro worker lo toma.
La entrega es al menos una vez; los motores no publican nada dos veces, ni a medias.

**El flujo automatico.** Un solo `POST /corridas` lleva la cartera de la ingesta al ruteo, sin que
el cliente pida cada etapa:

1. `POST /corridas` con el archivo (xlsx, csv o zip). Responde `201` con el `run_id` y la corrida
   `EN_PROCESO`; el archivo queda guardado y la ingesta, en la cola.
2. `GET /corridas/{run_id}/flujo` hasta que el flujo deje de estar `EN_PROCESO`: `COMPLETADO` si
   el ruteo termino `EXITOSA`, o `DETENIDO` en la etapa que no pudo continuar. Trae el
   `decision_run_id`, el `territorial_run_id` y el `ruteo_run_id` en cuanto existen.
3. `GET /flujos/{flujo_id}/trabajos`: los trabajos del flujo, con sus intentos y su lease.
4. `POST /flujos/{flujo_id}/reanudar` reintenta la etapa en que se detuvo, si es la decision, la
   organizacion territorial o el ruteo. Una ingesta que no publico no se reanuda: se vuelve a subir
   el archivo, y eso es otra corrida con otro flujo.

**Las fuentes oficiales.** Sin el campo `contrato`, el archivo es de `cartera/v1`, la cartera
minima de siempre. Una cartera oficial (la hoja CARTERA, con sus 93 columnas) se sube con
`contrato=cartera/v2` y su `fecha_corte`, que no es una columna: es metadata del lote. Cada archivo
se guarda tal como llego en un almacen por contenido y no se borra; `GET /corridas/{run_id}/fuente`
da la evidencia: el original, el dataset conformado y la auditoria de CARRIER. Los pagos (pagos/v1,
23 columnas, un movimiento por fila) tienen su propio recurso: `POST /pagos`,
`GET /pagos/{pagos_run_id}`, sus `/rechazos` y su `/fuente`. Ningun movimiento se deduplica.

**La cartera.**

5. `GET /corridas/{run_id}`: estado, conteos y tiempos de una corrida.
6. `GET /corridas/{run_id}/rechazos`: cada registro que no cumplio el contrato, con su fila y el
   motivo.
7. `GET /cartera/resumen`: cuentas y saldo por segmento de la cartera vigente.

**El Decision Engine** decide cada cuenta de una corrida `EXITOSA` con reglas versionadas:

8. `GET /corridas/{run_id}/decisiones`: todas las ejecuciones de esa corrida, la mas reciente
   primero.
9. `GET /decisiones/{decision_run_id}`: una ejecucion, con su version de las reglas y sus conteos.
10. `GET /decisiones/{decision_run_id}/cuentas`: lo que decidio de cada cuenta (segmento, prioridad
    y canal recomendado) y por que.

**El Motor Territorial** organiza por municipio las decisiones de una ejecucion `EXITOSA`. Prioriza
municipios; no traza rutas:

11. `GET /decisiones/{decision_run_id}/territoriales`: todas las ejecuciones territoriales de esas
    decisiones, la mas reciente primero.
12. `GET /territoriales/{territorial_run_id}`: una ejecucion, con su version y sus conteos.
13. `GET /territoriales/{territorial_run_id}/municipios`: cada municipio con su carga de campo, su
    lugar y por que, en orden de prioridad.

**El Motor de Ruteo** traza, dentro de cada municipio con trabajo de campo, en que secuencia visitar
sus cuentas `CAMPO`. **Las rutas son sinteticas**: cada cuenta recibe un punto determinista en un
plano local de 10 km por lado, propio de su municipio, y las distancias son Manhattan, en metros
sinteticos. No son latitud ni longitud, domicilios, calles, trafico ni tiempos:

14. `GET /territoriales/{territorial_run_id}/ruteos`: todas las ejecuciones de ruteo de esa
    ejecucion territorial, la mas reciente primero.
15. `GET /ruteos/{ruteo_run_id}`: una ejecucion, con su version y sus conteos.
16. `GET /ruteos/{ruteo_run_id}/rutas`: la ruta de cada municipio, en el orden de prioridad
    territorial, con sus distancias.
17. `GET /ruteos/{ruteo_run_id}/rutas/{clave_territorio}/paradas`: las paradas de un municipio, en
    el orden de visita.

**El modelo historico y la Cuenta 360.** Cada dataset conformado que se publica (una cartera
`cartera/v2` o un archivo de pagos) se materializa tambien en el modelo historico, en paralelo al
flujo operacional, con un trabajo `HISTORIA` de la misma cola: una cuenta canonica por
CLIENTE_UNICO, un corte canonico por fecha, un snapshot por cuenta y corte, y un pago observado por
fila de pagos/v1. Ningun motor de v1 lo espera ni lo lee.

18. `GET /cuentas?cliente_unico=...`: el `cuenta_id` de una cuenta de la cartera del sistema.
19. `GET /cuentas/{cuenta_id}`: su resumen a traves de sus cortes (`?al=AAAA-MM-DD`, como se veia
    en esa fecha). `/historia`, `/eventos` y `/pagos-observados` son sus subrecursos paginados.
20. `GET /cartera/cortes` y `GET /cartera/cortes/{corte_id}`: los cortes canonicos, el ultimo, y la
    evidencia de cada uno.
21. `GET /historias/{historia_run_id}`, `GET /corridas/{run_id}/historia` y
    `GET /pagos/{pagos_run_id}/historia`: como va o como termino cada materializacion.

Los pagos observados son movimientos tal como llegaron: no estan deduplicados, conciliados ni
atribuidos.

**El Motor de Pagos** interpreta los pagos observados sin tocarlos (`motor-pagos/v1`): por ventana
(un mes de recepcion), decide que observaciones son el mismo hecho economico, cuales son ambiguas,
cuales son reversos de cuales, y con que cuenta se concilia cada movimiento. Se abre solo, con un
trabajo `MOTOR_PAGOS`, cuando la historia publica los pagos observados de un archivo.

22. `GET /motor-pagos` y `GET /motor-pagos/{motor_pagos_run_id}`: cada interpretacion, cual es la
    vigente de su ventana, su calidad (duplicados, ambiguos, reversos, sin cuenta) y su recuperacion
    interpretada. `/resultados`: que concluyo de cada observacion, y por que.
23. `GET /movimientos`: los movimientos economicos vigentes, filtrables por cliente, cuenta, fechas,
    tipo y conciliacion. `GET /movimientos/{movimiento_id}`: uno, con su por que, la observacion que
    lo funda y su archivo original; `/observaciones`: cada fila que lo sustenta.
24. `GET /cuentas/{cuenta_id}/movimientos`: los movimientos de una cuenta con su contexto entre sus
    snapshots; `GET /cuentas/{cuenta_id}` trae el resumen de sus pagos.

La recuperacion interpretada es del motor sobre las fuentes disponibles: no es el libro contable del
acreedor.

**El lifecycle de cobranza** registra lo que la cobranza hizo con cada cuenta como eventos
operacionales que solo se agregan, con su momento de negocio (`ocurrido_en`) y su momento de
registro (`registrado_en`). No es una fuente oficial, y nada se fabrica de un snapshot. Cada
escritura exige `Idempotency-Key`: la misma llave con la misma peticion responde `200` con
`Idempotent-Replayed: true`; con otra peticion, `409 IDEMPOTENCY_KEY_REUTILIZADA`.

25. `POST /cuentas/{cuenta_id}/gestiones` (con su visita si es de `CAMPO`), las gestiones de una
    cuenta y `GET /gestiones/{gestion_id}`; `POST /gestiones/{gestion_id}/anulaciones`.
26. `POST /gestiones/{gestion_id}/promesas` y `/convenios`, con sus cancelaciones;
    `GET /promesas/{promesa_id}`, `GET /convenios/{convenio_id}` y los de una cuenta.
27. `GET /cuentas/{cuenta_id}/lifecycle`: su linea de tiempo en tres dominios (`OPERACIONAL`,
    `FUENTE_CORTE` y `ECONOMICO`), sin convertir uno en otro.
28. `POST /evaluaciones-promesas` con `as_of`: evalua las promesas de la cartera a esa fecha
    (`evaluacion-promesa/v1`) en un trabajo `EVALUACION_PROMESAS`; lo que concluyo de cada promesa.
29. `POST /atribuciones` con un `periodo`: asocia cada pago interpretado del mes con las gestiones
    con contacto que lo antecedieron (`atribucion/v1`) en un trabajo `ATRIBUCION`; sus resultados
    con sus candidatas, las atribuciones de un movimiento y los pagos atribuidos de una cuenta.

La atribucion es asociacion operacional, no causalidad: con dos o mas candidatas el pago queda
`AMBIGUA` y no se elige ninguna. Que un pago sea compatible con una promesa no dice que la promesa
lo produjo.

**El Decision Engine v2** (`decision/v2`) decide cada cuenta de un corte canonico con 43 features
(`features-decision/v1`) de su snapshot, su historia, su lifecycle, sus promesas, sus convenios, sus
pagos interpretados y sus asociaciones unicas, **point-in-time**: con lo que se conocia en
`known_at` de lo que paso hasta el final de `as_of`. La politica (`decision-policy/public-v1`, con
su hash) toma la prioridad base de `decision/v1` por dias de atraso y la ajusta con cuatro
modificadores de -1 o +1. **Demuestra la capacidad del motor; no representa una politica real de un
acreedor**, y no es una prediccion: no hay probabilidades ni modelos. Corre en paralelo a
`decision/v1`, que sigue alimentando el territorial y el ruteo.

30. `POST /decisiones-v2` con el `corte_id`: `OPERATIVO` (el corte mas reciente; sin `known_at`, el
    reloj de la base) o `BACKTEST` (cualquier corte, con `known_at` obligatorio). La decide el
    worker, en un trabajo `DECISION_V2`.
31. `GET /decisiones-v2/{decision_run_id}`: sus entradas, versiones, firma, disponibilidad de pagos
    y atribucion a `known_at`, distribucion y advertencias. `/resultados`: lo que decidio de cada
    cuenta, por prioridad o por canal.
32. `GET /decisiones-v2/{decision_run_id}/resultados/{cuenta_id}`: la decision de una cuenta
    explicada, con sus features guardadas, cada regla, sus motivos, sus hashes y su verificacion.
33. `GET /cuentas/{cuenta_id}/decisiones-v2` y `GET /cartera/cortes/{corte_id}/decisiones-v2`: las
    decisiones de una cuenta a traves de los cortes y de las fronteras de conocimiento, y las de un
    corte. `GET /cuentas/{cuenta_id}` trae su `ultima_decision_v2`.

Una feature de pagos o de atribucion cuya ventana no estaba disponible a `known_at` es `null`, no
cero, y no activa ninguna regla.

**La geografia real** (`geografia/v1`) separa el hecho geografico de la politica territorial. La
referencia es cartografia publica oficial (el Marco Geoestadistico del INEGI), versionada e
importada con `motor-cartera importar-geografia`: no es una tercera fuente de negocio. En PostGIS,
en EPSG:4326 (X longitud, Y latitud), la coordenada observada de cada cuenta (LATITUD y LONGITUD de
cartera/v2) se clasifica, se ubica con `ST_Covers` en los municipios de una version declarada y se
compara con el municipio que resolvio la fuente. Nada se corrige: una coordenada dudosa no se
reemplaza por un centroide.

34. `GET /geografia/versiones` y `/geografia/versiones/{geografia_version_id}`: cada version
    importada, con su producto, su edicion, el SHA-256 de su archivo y sus municipios.
35. `POST /geografia/ejecuciones` con el `corte_id` y la `geografia_version_id`: la geografia del
    corte, en un trabajo `GEOGRAFIA`. `GET /geografia/ejecuciones/{geografia_run_id}` y
    `/resultados`, filtrables por `estado_geografia` y `municipio`: el estado de cada cuenta, su
    municipio y sus motivos, sin coordenadas.
36. `GET /cuentas/{cuenta_id}/geografia`: su geografia a traves de los cortes.

**Las bases operativas** son configuracion operacional versionada: cada base con su punto, si esta
activa y los municipios que puede atender, declarados. **`territorial/v2`** asigna cada cuenta con
geografia confiable a la base activa mas cercana entre las que cubren su municipio
(`territorial-public-v2`, con distancia geodesica y una tolerancia de empate explicita); una
geografia dudosa queda en revision y un empate no se resuelve al azar. Corre en paralelo a
territorial/v1, que sigue alimentando el ruteo.

37. `POST /bases/versiones`, `GET /bases/versiones` y `/bases/versiones/{bases_version_id}`: el
    mismo contenido es la misma version.
38. `POST /territorial-v2` con el `corte_id`, su `geografia_run_id` EXITOSA y la
    `bases_version_id`, en un trabajo `TERRITORIAL_V2`. `GET /territorial-v2/{territorial_run_id}`
    y `/resultados`, filtrables por `base`, `municipio` y `estado`.
39. `GET /cuentas/{cuenta_id}/territorial-v2`; `GET /cuentas/{cuenta_id}` trae su
    `geografia_resumen`.

Es pertenencia territorial, no capacidad: sin zonas, jornadas, gestores ni rutas (v0.12 y v0.13).

**Campo v2** (`campo/v2`) dice que trabajo de campo puede ejecutarse en una fecha: sobre una
decision/v2 (sus cuentas de canal `CAMPO`, con su prioridad), una territorial/v2 (su base) y una
**configuracion de campo** versionada (gestores con un cupo diario declarado, calendarios con sus
ventanas y excepciones, y su zona horaria), cada candidata queda con un gestor disponible de su base
o dice por que no. La particion es una cuadricula fija en EPSG:6372: **zona** (base + entidad),
**subzona** (base + municipio), **tramo** (5 km) y **microtramo** (1 km), con identificadores
deterministas. Cada microtramo tiene un propietario preferido (Rendezvous hashing con SHA-256) y
nunca se le da mas que su cupo: lo que no cabe va al siguiente del ranking (spillover), y lo que no
cabe en la base queda `SIN_CAPACIDAD`, siempre lo de menor prioridad. Es **capacidad declarada, no
factibilidad vial** (v0.13), y no mueve cuentas entre bases.

40. `POST /campo/configuraciones`, `GET /campo/configuraciones` y
    `/campo/configuraciones/{configuracion_campo_version_id}`: el mismo contenido es la misma
    version.
41. `POST /campo-v2/planes` con el `corte_id`, el `decision_run_id`, el `territorial_run_id`, la
    `configuracion_campo_version_id` y la `fecha_operacion`, en un trabajo `CAMPO_V2`.
    `GET /campo-v2/planes/{campo_run_id}`, `/asignaciones` (por estado, base, gestor, zona, subzona,
    tramo, microtramo o prioridad), `/cargas` y `/unidades`.
42. `GET /cuentas/{cuenta_id}/campo-v2`; `GET /cuentas/{cuenta_id}` trae su `campo_resumen`.

**Etapas a mano.** `POST /corridas/{run_id}/decisiones`,
`POST /decisiones/{decision_run_id}/territoriales` y
`POST /territoriales/{territorial_run_id}/ruteos` piden una etapa sobre un recurso que no es de un
flujo que la vaya a correr, como una corrida publicada antes de v0.5.0 o con el CLI. Tambien
responden `201` con la ejecucion `EN_PROCESO`, y la ejecuta el worker. Sobre una fuente de un flujo
responden `409`: `FLUJO_EN_PROCESO` si el flujo la va a correr, o `FLUJO_DETENIDO` si se detuvo
ahi y se reanuda. Una vez publicada, una etapa no se repite: `409 DECISION_YA_GENERADA`,
`TERRITORIAL_YA_GENERADO` o `RUTEO_YA_GENERADO`.

**Autenticacion.** Toda ruta, salvo `/salud` y esta documentacion, exige la cabecera
`X-API-Key` con la clave de `MC_API_KEY`. Usa el boton *Authorize*.

**Errores.** Todas las respuestas 4xx y 5xx tienen la misma forma (`ErrorRespuesta`).
Compara `codigo`, que es estable; `mensaje` es para personas y puede cambiar.
"""

ETIQUETAS = [
    {
        "name": "corridas",
        "description": "Una corrida es una ingesta: un archivo leido, juzgado registro por "
        "registro y, si pasa, publicado. Todo lo que se escribe cuelga de una.",
    },
    {
        "name": "pagos",
        "description": "Una ingesta de pagos es un archivo de movimientos economicos (pagos/v1), "
        "guardado tal como llego, juzgado movimiento por movimiento y, si pasa, aceptado sin "
        "deduplicar nada. No es una corrida: no publica cuentas.",
    },
    {
        "name": "orquestacion",
        "description": "El flujo automatico de cada corrida, de la ingesta al ruteo, y los "
        "trabajos de la cola durable que lo ejecutan. La API los registra; un worker los ejecuta.",
    },
    {
        "name": "cartera",
        "description": "La cartera publicada: la que resulta de la ultima corrida EXITOSA "
        "con el corte mas reciente.",
    },
    {
        "name": "decisiones",
        "description": "Ejecuciones versionadas del Decision Engine y sus decisiones por cuenta.",
    },
    {
        "name": "territorial",
        "description": "Ejecuciones versionadas del Motor Territorial y sus resultados por "
        "municipio: carga de campo y prioridad, no rutas.",
    },
    {
        "name": "ruteo",
        "description": "Ejecuciones versionadas del Motor de Ruteo, su ruta por municipio y sus "
        "paradas en orden de visita. Coordenadas y distancias sinteticas: metros de un plano "
        "local por municipio, no geografia real.",
    },
    {
        "name": "cuentas",
        "description": "Cuenta 360: una cuenta canonica (un CLIENTE_UNICO de la cartera) a traves "
        "de sus cortes, con su historia, sus eventos de presencia y sus pagos observados.",
    },
    {
        "name": "historia",
        "description": "El modelo historico: los cortes canonicos de la cartera y las ejecuciones "
        "que materializan cada dataset conformado, en paralelo al flujo operacional.",
    },
    {
        "name": "motor-pagos",
        "description": "Las interpretaciones versionadas de los pagos observados, una por ventana "
        "(un mes de recepcion), con su calidad, su recuperacion interpretada y lo que concluyeron "
        "de cada observacion.",
    },
    {
        "name": "movimientos",
        "description": "Los movimientos economicos canonicos que interpreta el motor de pagos: "
        "cada uno con su por que, sus observaciones y su archivo original. No son el libro "
        "contable del acreedor.",
    },
    {
        "name": "lifecycle",
        "description": "Lo que la cobranza hizo con cada cuenta: gestiones, contactos, visitas, "
        "promesas y convenios, como eventos operacionales que registra Motor Cartera, con su "
        "momento de negocio y su momento de registro. No son una fuente oficial del acreedor. "
        "Cada escritura exige su Idempotency-Key, y nada se sobrescribe: se anula o se cancela con "
        "otro evento.",
    },
    {
        "name": "atribucion",
        "description": "La atribucion operativa de los pagos (atribucion/v1): con que gestiones "
        "con contacto de la misma cuenta se asocia cada pago interpretado, dentro de una ventana "
        "que se guarda en cada ejecucion. Asociacion operacional, no causalidad: con varias "
        "candidatas el pago queda AMBIGUA y no se elige ninguna.",
    },
    {
        "name": "decision-v2",
        "description": "El Decision Engine v2 (decision/v2): cada cuenta de un corte decidida "
        "point-in-time, con lo que se conocia en known_at de lo que paso hasta el final de as_of, "
        "con sus features (features-decision/v1) y su politica (decision-policy/public-v1) "
        "versionadas, sus motivos y sus hashes. Una politica publica y sintetica, no una "
        "prediccion; corre en paralelo a decision/v1.",
    },
    {
        "name": "geografia",
        "description": "La geografia real (geografia/v1): las versiones importadas de la "
        "referencia publica y, por corte, el estado y el municipio de la coordenada observada de "
        "cada cuenta, comparado con el de la fuente. Sin coordenadas en las respuestas.",
    },
    {
        "name": "bases",
        "description": "Las bases operativas: configuracion operacional versionada, con su punto, "
        "si estan activas y su cobertura declarada por municipio. No es una fuente de negocio.",
    },
    {
        "name": "territorial-v2",
        "description": "territorial/v2: a que base operativa se asigna cada cuenta de un corte, "
        "sobre una geografia EXITOSA y una version de bases declaradas, con "
        "territorial-public-v2. Pertenencia territorial, no capacidad.",
    },
    {
        "name": "campo",
        "description": "La configuracion de campo: configuracion operacional versionada sobre una "
        "version de bases, con sus gestores (un codigo, sin datos de personas), su cupo diario "
        "declarado, sus calendarios con sus ventanas y excepciones, y su zona horaria. No es una "
        "fuente de negocio.",
    },
    {
        "name": "campo-v2",
        "description": "campo/v2: los planes de campo de una fecha, con campo-public-v2. Que "
        "candidata de campo quedo con que gestor de su base, en que zona, subzona, tramo y "
        "microtramo, bajo la capacidad declarada; capacidad, no factibilidad vial.",
    },
    {"name": "salud", "description": "Si la API vive y la base contesta."},
]


def crear_app(config: Config | None = None) -> FastAPI:
    """Construye la API. Sin MC_API_KEY no arranca: una API que arranca sin clave, arranca
    abierta, y eso es fail-open."""
    config = config or config_del_entorno
    clave = config.api_key.get_secret_value() if config.api_key else ""
    if not clave:
        raise RuntimeError("MC_API_KEY no esta definida: la API no arranca sin clave.")

    app = FastAPI(
        title="motor-cartera",
        version=__version__,
        description=DESCRIPCION,
        openapi_tags=ETIQUETAS,
    )
    app.state.api_key = clave
    app.state.config = config
    registrar_manejadores(app)
    protegidas = [Depends(exigir_api_key)]
    app.include_router(corridas.router, dependencies=protegidas)
    app.include_router(pagos.router, dependencies=protegidas)
    app.include_router(orquestacion.router, dependencies=protegidas)
    app.include_router(cartera.router, dependencies=protegidas)
    app.include_router(decisiones.router, dependencies=protegidas)
    app.include_router(territorial.router, dependencies=protegidas)
    app.include_router(ruteo.router, dependencies=protegidas)
    app.include_router(cuentas.router, dependencies=protegidas)
    app.include_router(historia.router, dependencies=protegidas)
    app.include_router(motor_pagos.router, dependencies=protegidas)
    app.include_router(movimientos.router, dependencies=protegidas)
    app.include_router(gestiones.router, dependencies=protegidas)
    app.include_router(acuerdos.router, dependencies=protegidas)
    app.include_router(lifecycle.router, dependencies=protegidas)
    app.include_router(evaluaciones.router, dependencies=protegidas)
    app.include_router(atribuciones.router, dependencies=protegidas)
    app.include_router(decisiones_v2.router, dependencies=protegidas)
    app.include_router(geografia.router, dependencies=protegidas)
    app.include_router(bases.router, dependencies=protegidas)
    app.include_router(territorial_v2.router, dependencies=protegidas)
    app.include_router(campo.router, dependencies=protegidas)
    app.include_router(campo_v2.router, dependencies=protegidas)
    app.include_router(salud.router)
    return app
