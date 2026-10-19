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
    analytics,
    app_v1,
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
    optimizacion,
    orquestacion,
    pagos,
    predictivo,
    ruteo,
    ruteo_v2,
    salud,
    territorial,
    territorial_v2,
)
from motor_cartera.api.autorizacion import (
    MATRIZ,
    Autenticador,
    agregar_seguridad_al_openapi,
    autorizar,
    verificar_matriz,
)
from motor_cartera.api.errores import registrar_manejadores
from motor_cartera.api.limites import LimiteDeCuerpo
from motor_cartera.api.observacion import Observacion
from motor_cartera.config import Config, validar
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

**Collection Analytics** (`analytics/v1`) describe una cohorte, agregada y point-in-time: las
cuentas de un corte canonico, de `fecha_corte` al final de `as_of` en una zona horaria explicita,
con lo que se conocia en `known_at`. Cada ejecucion declara su manifiesto de entradas por su
identificador (las interpretaciones de pagos de cada mes y su atribucion, la evaluacion de
promesas, decision/v2, la geografia, territorial/v2, campo/v2 y ruteo/v2): **nada se elige como
"lo ultimo"**, y lo que no se declara se publica `NO_DISPONIBLE`, nunca cero. Las metricas salen
de un catalogo versionado (`analytics-metrics/public-v1`, con su hash), y las celdas con menos de
10 cuentas se suprimen (`analytics-public-v1`). Describe; **no prueba causalidad, no predice y no
recomienda**.

43. `GET /analytics/catalogo`: cada metrica con su formula, su numerador y su denominador, sus
    dimensiones permitidas, sus entradas requeridas y sus limitaciones.
44. `POST /analytics/ejecuciones` con el `corte_id`, `as_of`, `modo` (`OPERATIVO` o `BACKTEST`, con
    `known_at` obligatorio), `zona_horaria`, `granularidad` y las entradas, en un trabajo
    `ANALYTICS`. `GET /analytics/ejecuciones` y `/analytics/ejecuciones/{analytics_run_id}`.
45. `GET /analytics/ejecuciones/{analytics_run_id}/agregados`, filtrables por metrica, vista,
    conjunto, periodo, estado y llave de dimension; `/resumen`, `/embudos` (`CONTACTO` y `CAMPO`) y
    `/controles`.

No hay analytics por cuenta: la Cuenta 360 no cambia.

**Predictive Intelligence** (`predictive/model-v1`) estima, en paralelo y en sombra, la probabilidad
de resultados observados bajo la operacion historica en los dias siguientes a un corte: recuperacion
positiva a 30 dias, contacto a 14, creacion de una promesa a 30, y la recuperacion positiva
esperada a 30. Cada entrenamiento declara su manifiesto (anclas con sus tres relojes, su cobertura
y su split temporal con purga; las interpretaciones de pagos de sus labels): **nada se elige como
"lo ultimo"**. Los modelos son GLM (logistica con L2 y Tweedie), su artefacto es JSON canonico con
su hash, se calibran y se eligen con VALIDATION, y se publican solo si pasan sus compuertas en
TEST. Un scoring declara su version PUBLICADA. **Una prediccion no es un efecto causal**, no
recomienda una accion y no cambia decision/v2, campo/v2 ni ruteo/v2.

46. `GET /predictivo/features` y `GET /predictivo/targets`: las definiciones versionadas, con su
    hash.
47. `POST /predictivo/entrenamientos` con el manifiesto, en un trabajo `PREDICTIVE_TRAINING`.
    `GET /predictivo/entrenamientos` y `/predictivo/entrenamientos/{training_run_id}`.
48. `GET /predictivo/modelos` y `/predictivo/modelos/{modelo_version_id}`: la ficha, los artefactos,
    las metricas de VALIDATION y de TEST, los intervalos bootstrap y las compuertas.
49. `POST /predictivo/scorings` con el `modelo_version_id`, el `corte_id`, el `modo` y `known_at`,
    en un trabajo `PREDICTIVE_SCORING`. `GET /predictivo/scorings` y
    `/predictivo/scorings/{scoring_run_id}`; `/predicciones` (rangos, producto, entidad, estado y un
    orden de una lista cerrada), `/drift` y `/cuentas/{cuenta_id}/explicacion`.
50. `GET /cuentas/{cuenta_id}/predictivo`: las predicciones publicadas de una cuenta.

**Optimization** (`optimization/v1`) arma, en sombra, el plan de una fecha: que cuentas atender y
por que canal (`DIGITAL`, `TELEFONICA` o `CAMPO`) bajo capacidades, presupuesto de unidades y
coberturas declaradas en una **configuracion** versionada. Maximiza la recuperacion positiva
esperada a 30 dias de un scoring publicado (en centavos, sin multiplicarla por ninguna
probabilidad); empata con la probabilidad de contacto, las menos unidades y un orden canonico, en
cuatro niveles lexicograficos, cada uno con el optimo del anterior fijo. Cada ejecucion declara su
scoring, su decision/v2 del mismo corte, su configuracion PUBLICADA y, si usa CAMPO o BASE, su
ruteo/v2: **nada se elige como "lo ultimo"**. CAMPO solo es una parada PROGRAMADA de su propio
gestor; el plan no reordena ninguna ruta. **Solo se publica un optimo demostrado**: con tiempo
agotado la ejecucion termina `OPTIMO_NO_DEMOSTRADO`, y una infactible trae su diagnostico.

51. `GET /optimizacion/politica`: el objetivo, su semantica, la reduccion exacta, la huella del
    solver y las limitaciones.
52. `POST /optimizacion/configuraciones` (`?publicar=true`), `GET /optimizacion/configuraciones` y
    `/optimizacion/configuraciones/{configuracion_id}`: la matriz de admisibilidad, los pools y las
    restricciones; el mismo contenido es la misma version.
53. `POST /optimizacion/ejecuciones`, en un trabajo `OPTIMIZATION`. `GET /optimizacion/ejecuciones`
    y `/optimizacion/ejecuciones/{optimizacion_run_id}`: sus entradas, sus hashes y su certificado
    de optimalidad por nivel.
54. `.../resultados` (por estado, canal, pool, producto, prioridad o base), `.../capacidad`,
    `.../restricciones` (lado izquierdo, derecho, holgura y si quedo activa), `.../diagnostico` y
    `.../cuentas/{cuenta_id}`: por que una cuenta quedo como quedo.
55. `GET /cuentas/{cuenta_id}/optimizacion`: lo que cada plan publicado concluyo de una cuenta.

Es el optimo del modelo declarado, no una estrategia causalmente optima: un canal asignado no es la
evidencia de que ese canal cause la recuperacion. No ejecuta nada.

**La app** (`app/v1`) es la fachada de lectura de la superficie web: solo `GET`, bajo un **contexto
operativo** explicito (el `corte_id` y cada ejecucion que una pantalla lee, por su identificador
publico). Nada se elige como "lo ultimo", un contexto cuyas ejecuciones no encadenan no se combina
(`409 CONTEXTO_INCOMPATIBLE`), y ninguna respuesta lleva un CLIENTE_UNICO, un actor_ref ni una
coordenada. Existe solo donde ahorra viajes o uniones en el navegador; lo demas lo lee de sus rutas.

56. `GET /app/v1/contextos/opciones` y `/app/v1/contextos/validar`: las carteras, sus cortes, las
    ejecuciones de un corte y los contextos compatibles que se pueden proponer; y si un contexto
    encadena, con cada problema.
57. `GET /app/v1/resumen`: cada bloque del contexto, tal como lo publico su ejecucion, y
    `/app/v1/predictivo/scorings/{scoring_run_id}/distribucion`: la distribucion de las predicciones
    publicadas, contada en la base.
58. `GET /app/v1/cuentas`: las cuentas del corte, filtradas, ordenadas y paginadas en la base; y
    `/app/v1/cuentas/{cuenta_id}`, `/decision`, `/historia`, `/linea-de-tiempo`, `/pagos` y
    `/gestiones`: la Cuenta 360 en el contexto, sin datos personales.
59. `GET /app/v1/ruteo/rutas/{ruta_id}/paradas` y `GET /app/v1/linaje`: las paradas de una ruta en
    su orden, y de donde sale cada resultado del contexto.

**Etapas a mano.** `POST /corridas/{run_id}/decisiones`,
`POST /decisiones/{decision_run_id}/territoriales` y
`POST /territoriales/{territorial_run_id}/ruteos` piden una etapa sobre un recurso que no es de un
flujo que la vaya a correr, como una corrida publicada antes de v0.5.0 o con el CLI. Tambien
responden `201` con la ejecucion `EN_PROCESO`, y la ejecuta el worker. Sobre una fuente de un flujo
responden `409`: `FLUJO_EN_PROCESO` si el flujo la va a correr, o `FLUJO_DETENIDO` si se detuvo
ahi y se reanuda. Una vez publicada, una etapa no se repite: `409 DECISION_YA_GENERADA`,
`TERRITORIAL_YA_GENERADO` o `RUTEO_YA_GENERADO`.

**Autenticacion y permisos.** Toda ruta, salvo `/salud`, `/health/live`, `/health/ready` y esta
documentacion (que no existe en produccion), exige una credencial y un permiso. Una persona entra
con OpenID Connect por el edge, que presenta su token de acceso (`Authorization: Bearer`); un
servicio interno, con su clave (`X-API-Key`), y en desarrollo la clave de `MC_API_KEY`. Cada
operacion exige su permiso: `mc.read` para leer, `mc.audit` para la evidencia, los trabajos de la
cola y el linaje, `mc.execute` para pedir trabajo o registrar un evento y `mc.configure` para
publicar configuracion. Sin credencial valida, `401`; sin el permiso, `403 PERMISO_INSUFICIENTE`.
La matriz completa esta en docs/matriz-autorizacion.md. Usa el boton *Authorize*.

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
    {
        "name": "ruteo-v2",
        "description": "ruteo/v2: las versiones de la red vial (OpenStreetMap, ODbL) y las "
        "rutas por calle de los gestores de un plan de campo/v2, con ruteo-public-v2 y un "
        "OSRM autoalojado: el orden de visita, las ETA planificadas sin trafico, las distancias "
        "y las duraciones, y que no cabe en la jornada o no es enrutable. Una heuristica, no una "
        "ruta optima; sin coordenadas ni geometrias.",
    },
    {
        "name": "analytics",
        "description": "Collection Analytics (analytics/v1): metricas, cohortes, embudos y "
        "reporting point-in-time de una cohorte anclada a un corte, con un manifiesto de entradas "
        "explicito, un catalogo versionado y la supresion de celdas pequenas. Describe lo "
        "observado y lo planificado, agregado: no prueba causalidad, no predice y no recomienda.",
    },
    {
        "name": "predictivo",
        "description": "Predictive Intelligence (predictive/model-v1): features y targets "
        "versionados, entrenamientos con un manifiesto temporal explicito, versiones del modelo "
        "con artefactos JSON, calibracion y compuertas, scorings con drift y explicaciones "
        "verificadas. Predice resultados observados bajo la operacion historica: no es causal, no "
        "recomienda y no cambia decision/v2, campo/v2 ni ruteo/v2.",
    },
    {
        "name": "optimizacion",
        "description": "Optimization (optimization/v1): configuraciones versionadas de "
        "admisibilidad, capacidad y coberturas, y planes de una fecha que asignan cuentas a "
        "canales y pools con un MILP lexicografico exacto sobre entradas declaradas. Solo se "
        "publica un optimo demostrado. Es el optimo del modelo declarado, en sombra: no es causal, "
        "no ejecuta nada y no cambia decision/v2, campo/v2 ni ruteo/v2.",
    },
    {
        "name": "app",
        "description": "app/v1: la fachada de solo lectura de la superficie web. Compone, bajo un "
        "contexto operativo explicito (el corte y cada ejecucion por su identificador publico), lo "
        "que los dominios ya publicaron: valida que el contexto encadene, resume cada bloque, "
        "pagina las cuentas del corte, arma la Cuenta 360 sin datos personales y dibuja el linaje. "
        "No ejecuta, no recalcula y no elige nada como 'lo ultimo'.",
    },
    {
        "name": "salud",
        "description": "Si la API vive (liveness), si esta lista para atender (readiness: la base "
        "contesta con el esquema esperado) y el reporte de siempre.",
    },
]

ROUTERS = (
    corridas,
    pagos,
    orquestacion,
    cartera,
    decisiones,
    territorial,
    ruteo,
    cuentas,
    historia,
    motor_pagos,
    movimientos,
    gestiones,
    acuerdos,
    lifecycle,
    evaluaciones,
    atribuciones,
    decisiones_v2,
    geografia,
    bases,
    territorial_v2,
    campo,
    campo_v2,
    ruteo_v2,
    analytics,
    predictivo,
    optimizacion,
    app_v1,
    salud,
)


def crear_app(config: Config | None = None) -> FastAPI:
    """Construye la API. Con una configuracion que no le permite arrancar (en produccion: sin
    OpenID Connect, sin la credencial del gateway, sin la clave de auditoria, con la clave universal
    de desarrollo...; fuera de produccion, sin ninguna credencial que aceptar) levanta
    ConfiguracionInvalida y no arranca: una API mal configurada no arranca abierta."""
    config = config or config_del_entorno
    validar(config, "api")
    from motor_cartera.observabilidad import metricas, preparar_proceso

    preparar_proceso(config, "api")
    documentacion = not config.es_produccion
    app = FastAPI(
        title="motor-cartera",
        version=__version__,
        description=DESCRIPCION,
        openapi_tags=ETIQUETAS,
        dependencies=[Depends(autorizar)],
        # En produccion la API no publica su documentacion ni su OpenAPI: no hacen falta para
        # operarla, y le dirian a quien llegue a la red interna cada ruta que existe.
        docs_url="/docs" if documentacion else None,
        redoc_url="/redoc" if documentacion else None,
        openapi_url="/openapi.json" if documentacion else None,
        # La telemetria nativa de FastAPI, apagada entera: su span de servidor lleva la ruta real y
        # la query (un identificador de cliente adentro) y sus logs, los valores de entrada de una
        # validacion fallida. El span y el log de la peticion los emite Observacion, con la
        # plantilla de la ruta y sin datos.
        telemetry={
            "tracing": False,
            "metrics": False,
            "logs": False,
            "operation_spans": False,
            "auto_configure": False,
        },
    )
    app.state.config = config
    app.state.matriz = dict(MATRIZ)
    app.state.autenticador = Autenticador.de_config(config)
    registrar_manejadores(app)
    # El ultimo que se agrega es el mas externo: la observacion ve tambien el 413 del limite.
    app.add_middleware(LimiteDeCuerpo, maximo_bytes=config.tamano_maximo_cuerpo_kb * 1024)
    app.add_middleware(Observacion)
    for modulo in ROUTERS:
        app.include_router(modulo.router)
    original = app.openapi

    def openapi() -> dict:
        if app.openapi_schema is None:
            agregar_seguridad_al_openapi(original(), app.state.matriz)
        return app.openapi_schema

    app.openapi = openapi
    verificar_matriz(app)
    if config.app_env != "test":
        metricas.registrar_cola()
    return app
