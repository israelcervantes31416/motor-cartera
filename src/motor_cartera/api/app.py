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
    cartera,
    corridas,
    cuentas,
    decisiones,
    historia,
    motor_pagos,
    movimientos,
    orquestacion,
    pagos,
    ruteo,
    salud,
    territorial,
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
    app.include_router(salud.router)
    return app
