# La API

Cada operación de la API REST, con sus respuestas, la forma común de los errores y las reglas que un
cliente tiene que conocer. La referencia interactiva (OpenAPI) está en `/docs` del compose de
desarrollo, y el contrato exacto de cada versión se publica con su release (`openapi-vX.Y.Z.json`);
qué se mantiene entre versiones, y qué no, lo dice [versioning.md](versioning.md). Quién puede llamar
cada operación en producción, en [matriz-autorizacion.md](matriz-autorizacion.md); la superficie de
solo lectura de la app (`app/v1`), en [app.md](app.md).

| Método y ruta | Qué hace | Respuestas |
|---|---|---|
| `POST /corridas` | Guarda el archivo en el almacén de artefactos y registra la corrida `EN_PROCESO`, con su flujo automático y el trabajo de su ingesta. `contrato` (`cartera/v1` por omisión, o `cartera/v2`) y, con cartera/v2, `fecha_corte` | 201, 401, 409, 413, 415, 422 |
| `GET /corridas/{run_id}` | Estado: conteos, tiempos y resultado | 200, 401, 404, 422 |
| `GET /corridas/{run_id}/rechazos` | Registros rechazados con su fila y motivo, paginados | 200, 401, 404, 409, 422 |
| `GET /corridas/{run_id}/fuente` | La evidencia: el archivo original, el dataset conformado y la auditoría de CARRIER | 200, 401, 404, 422 |
| `POST /pagos` | Guarda el archivo de pagos y registra la ingesta `EN_PROCESO` con su trabajo `INGESTA_PAGOS` | 201, 401, 409, 413, 415, 422 |
| `GET /pagos/{pagos_run_id}` | Una ingesta de pagos: estado, conteos, firmas, tiempos y su trabajo | 200, 401, 404, 422 |
| `GET /pagos/{pagos_run_id}/rechazos` | Los movimientos rechazados con su fila, sus 23 valores y su motivo, paginados | 200, 401, 404, 409, 422 |
| `GET /pagos/{pagos_run_id}/fuente` | La evidencia de una ingesta de pagos: el original y el conformado | 200, 401, 404, 422 |
| `GET /corridas/{run_id}/flujo` | El flujo automático de la corrida: estado, etapa y el identificador de cada ejecución | 200, 401, 404, 422 |
| `GET /flujos/{flujo_id}` | Lo mismo, por el identificador del flujo | 200, 401, 404, 422 |
| `GET /flujos/{flujo_id}/trabajos` | Los trabajos del flujo, en el orden en que entraron a la cola, con sus intentos y su lease, paginados | 200, 401, 404, 422 |
| `GET /trabajos/{trabajo_id}` | Un trabajo de la cola: tipo, estado, intentos, lease y último error | 200, 401, 404, 422 |
| `POST /flujos/{flujo_id}/reanudar` | Reintenta, con otra ejecución, la etapa downstream en que se detuvo el flujo | 200, 401, 404, 409, 422 |
| `GET /cartera/resumen` | Cuentas y saldo por segmento de la cartera vigente, paginado | 200, 401, 404, 409, 422 |
| `POST /corridas/{run_id}/decisiones` | Pide decidir con `decision/v1` cada cuenta de una corrida `EXITOSA`; registra la ejecución `EN_PROCESO` y la decide el worker | 201, 401, 404, 409, 422 |
| `GET /corridas/{run_id}/decisiones` | Historial: todas las ejecuciones de la corrida, la más reciente primero, paginado | 200, 401, 404, 422 |
| `GET /decisiones/{decision_run_id}` | Una ejecución: versión de las reglas, estado, tiempos y conteos | 200, 401, 404, 422 |
| `GET /decisiones/{decision_run_id}/cuentas` | La decisión de cada cuenta con sus motivos, paginada; solo de una ejecución `EXITOSA` | 200, 401, 404, 409, 422 |
| `POST /decisiones/{decision_run_id}/territoriales` | Pide organizar por municipio, con `territorial/v1`, las decisiones de una ejecución `EXITOSA` de `decision/v1`; las organiza el worker | 201, 401, 404, 409, 422 |
| `GET /decisiones/{decision_run_id}/territoriales` | Historial: todas las ejecuciones territoriales de esas decisiones, la más reciente primero, paginado | 200, 401, 404, 422 |
| `GET /territoriales/{territorial_run_id}` | Una ejecución territorial: versión de las reglas, estado, tiempos y conteos | 200, 401, 404, 422 |
| `GET /territoriales/{territorial_run_id}/municipios` | Cada municipio con su carga, su lugar y su motivo, en orden de prioridad y paginado; solo de una ejecución `EXITOSA` | 200, 401, 404, 409, 422 |
| `POST /territoriales/{territorial_run_id}/ruteos` | Pide trazar con `ruteo/v1` la ruta sintética de cada municipio con trabajo de campo de una ejecución territorial `EXITOSA`; la traza el worker | 201, 401, 404, 409, 422 |
| `GET /territoriales/{territorial_run_id}/ruteos` | Historial: todas las ejecuciones de ruteo de esa ejecución territorial, la más reciente primero, paginado | 200, 401, 404, 422 |
| `GET /ruteos/{ruteo_run_id}` | Una ejecución de ruteo: versión de las reglas, estado, tiempos y conteos | 200, 401, 404, 422 |
| `GET /ruteos/{ruteo_run_id}/rutas` | La ruta de cada municipio con sus distancias, en orden de prioridad territorial y paginada; solo de una ejecución `EXITOSA` | 200, 401, 404, 409, 422 |
| `GET /ruteos/{ruteo_run_id}/rutas/{clave_territorio}/paradas` | Las paradas de un municipio en orden de visita, con su punto sintético y su distancia, paginadas; solo de una ejecución `EXITOSA` | 200, 401, 404, 409, 422 |
| `GET /cuentas?cliente_unico=…` | El `cuenta_id` de la cuenta canónica de ese `CLIENTE_UNICO` en la cartera del sistema | 200, 401, 404, 422 |
| `GET /cuentas/{cuenta_id}` | La Cuenta 360: primera y última observación, presencia, ausencias, reingresos, pagos observados, snapshot actual, `resumen_pagos` (lo que interpreta el motor de pagos), `lifecycle_resumen` (lo que hizo la cobranza y la última atribución) y `ultima_decision_v2`; con `?al=AAAA-MM-DD`, como se veía ese día | 200, 401, 404, 422 |
| `GET /cuentas/{cuenta_id}/historia` | Sus snapshots con continuidad, deltas y evidencia (`corte_id`, `dataset_id`, `source_row`, `source_sheet`), paginados; `orden=desc` por omisión | 200, 401, 404, 422 |
| `GET /cuentas/{cuenta_id}/eventos` | Sus eventos de presencia, calculados al consultar, paginados | 200, 401, 404, 422 |
| `GET /cuentas/{cuenta_id}/pagos-observados` | Sus movimientos de pagos/v1 tal como llegaron, sin deduplicar ni conciliar, del más reciente al más antiguo, paginados | 200, 401, 404, 422 |
| `GET /cuentas/{cuenta_id}/movimientos` | Sus movimientos económicos interpretados por el motor de pagos, con su contexto temporal entre sus snapshots; `desde` y `hasta`, paginados | 200, 401, 404, 422 |
| `GET /cartera/cortes` | Los cortes canónicos de la cartera, por fecha, y el último | 200, 401, 422 |
| `GET /cartera/cortes/{corte_id}` | Un corte con su evidencia: el Parquet, el original y sus fuentes equivalentes | 200, 401, 404, 422 |
| `GET /historias/{historia_run_id}` | Una ejecución histórica: estado, resultado, conteos, corte y trabajo | 200, 401, 404, 422 |
| `GET /corridas/{run_id}/historia` | Las ejecuciones que materializaron el dataset de la corrida | 200, 401, 404, 409, 422 |
| `GET /pagos/{pagos_run_id}/historia` | Las de una ingesta de pagos, y cuántos de sus pagos tienen hoy una cuenta observada | 200, 401, 404, 409, 422 |
| `GET /motor-pagos` | Las interpretaciones del motor de pagos, por ventana, con cuál es la vigente; filtros `periodo`, `estado` y `version`, paginadas | 200, 401, 422 |
| `GET /motor-pagos/{motor_pagos_run_id}` | Una interpretación: versión, estado, ventana, firma de entrada, `calidad` (cuántas observaciones de cada clase), `recuperacion` interpretada, tiempos y detalle | 200, 401, 404, 422 |
| `GET /motor-pagos/{motor_pagos_run_id}/resultados` | Lo que concluyó de cada pago observado de su ventana, y por qué; filtros `clasificacion`, `cliente_unico`, `firma_exacta` y `firma_legacy`, paginados | 200, 401, 404, 422 |
| `GET /movimientos` | Los movimientos económicos vigentes, del más reciente al más antiguo; filtros `cliente_unico`, `cuenta_id`, `desde`, `hasta`, `tipo` y `estado_conciliacion`, paginados | 200, 401, 404, 422 |
| `GET /movimientos/{movimiento_id}` | Un movimiento con su clasificación, sus motivos, su representante con su archivo original y su contexto temporal | 200, 401, 404, 422 |
| `GET /movimientos/{movimiento_id}/observaciones` | Los pagos observados que lo sustentan, cada uno con su ingesta, su dataset, su fila y su archivo original, paginados | 200, 401, 404, 422 |
| `POST /cuentas/{cuenta_id}/gestiones` | Registra una gestión (con su visita si es de `CAMPO`); exige `Idempotency-Key` | 200, 201, 401, 404, 409, 422 |
| `GET /cuentas/{cuenta_id}/gestiones` | Sus gestiones, filtradas en la base por `desde`, `hasta`, `canal`, `nivel_contacto`, `resultado` y `estado`, paginadas | 200, 401, 404, 422 |
| `GET /gestiones/{gestion_id}` | Una gestión: su cuenta, su contacto, su resultado, sus dos tiempos, su evento, si fue anulada y lo que nació de ella | 200, 401, 404, 422 |
| `POST /gestiones/{gestion_id}/anulaciones` | La anula, con su visita, su promesa y su convenio; exige `Idempotency-Key` | 200, 201, 401, 404, 409, 422 |
| `POST /gestiones/{gestion_id}/promesas` | La promesa de una gestión con resultado `PROMESA`; exige `Idempotency-Key` | 200, 201, 401, 404, 409, 422 |
| `GET /promesas/{promesa_id}` | Una promesa: lo prometido, su estado operativo, su última evaluación y su linaje | 200, 401, 404, 422 |
| `POST /promesas/{promesa_id}/cancelaciones` | La cancela; exige `Idempotency-Key` | 200, 201, 401, 404, 409, 422 |
| `GET /cuentas/{cuenta_id}/promesas` | Las promesas de la cuenta, por estado operativo, paginadas | 200, 401, 404, 422 |
| `POST /gestiones/{gestion_id}/convenios` | El convenio de una gestión con resultado `CONVENIO`, con sus cuotas declaradas; exige `Idempotency-Key` | 200, 201, 401, 404, 409, 422 |
| `GET /convenios/{convenio_id}` | Un convenio, sus cuotas y la recuperación observada durante su vigencia (no un ledger) | 200, 401, 404, 422 |
| `POST /convenios/{convenio_id}/cancelaciones` | Lo cancela; exige `Idempotency-Key` | 200, 201, 401, 404, 409, 422 |
| `GET /cuentas/{cuenta_id}/convenios` | Los convenios de la cuenta, paginados | 200, 401, 404, 422 |
| `GET /cuentas/{cuenta_id}/lifecycle` | Su línea de tiempo: `OPERACIONAL`, `FUENTE_CORTE` y `ECONOMICO`, por tiempo de negocio, paginada | 200, 401, 404, 422 |
| `POST /evaluaciones-promesas` | Pide evaluar las promesas de la cartera a una fecha de corte (`as_of`); la evalúa el worker | 201, 401, 409, 422 |
| `GET /evaluaciones-promesas` | Las evaluaciones, por fecha de corte, paginadas | 200, 401, 422 |
| `GET /evaluaciones-promesas/{evaluacion_run_id}` | Una evaluación: fecha de corte, estado, conteos por estado y montos | 200, 401, 404, 422 |
| `GET /evaluaciones-promesas/{evaluacion_run_id}/promesas` | Lo que concluyó de cada promesa, con sus motivos, paginado | 200, 401, 404, 422 |
| `POST /atribuciones` | Pide atribuir los pagos de un mes (`periodo`, y `ventana_dias` si no es la configurada); la atribuye el worker | 201, 401, 409, 422 |
| `GET /atribuciones` | Las atribuciones, por mes, con cuál es la vigente, paginadas | 200, 401, 422 |
| `GET /atribuciones/{atribucion_run_id}` | Una atribución: ventana, estado, conteos, montos y la interpretación de pagos que leyó | 200, 401, 404, 422 |
| `GET /atribuciones/{atribucion_run_id}/resultados` | Lo que concluyó de cada pago, con sus candidatas; filtros `clasificacion` y `cliente_unico`, paginado | 200, 401, 404, 422 |
| `GET /movimientos/{movimiento_id}/atribuciones` | Cada atribución de un pago: la vigente y las que la precedieron | 200, 401, 404, 422 |
| `GET /cuentas/{cuenta_id}/atribuciones` | Los pagos de la cuenta, cada uno con su atribución vigente, paginados | 200, 401, 404, 422 |
| `POST /decisiones-v2` | Pide decidir las cuentas de un corte con `decision/v2`, a un `as_of` y un `known_at`, en `OPERATIVO` o `BACKTEST`; la decide el worker | 201, 401, 404, 409, 422 |
| `GET /decisiones-v2/{decision_run_id}` | Una decisión v2: sus entradas, versiones, hash de la política, firma, estado, disponibilidad, distribución y advertencias | 200, 401, 404, 422 |
| `GET /decisiones-v2/{decision_run_id}/resultados` | Lo que decidió de cada cuenta, con sus motivos; filtros `prioridad_final` y `canal_recomendado`, paginado | 200, 401, 404, 422 |
| `GET /decisiones-v2/{decision_run_id}/resultados/{cuenta_id}` | La decisión de una cuenta explicada: sus features, cada regla y si aplicó, sus motivos, sus hashes y su verificación | 200, 401, 404, 422 |
| `GET /cuentas/{cuenta_id}/decisiones-v2` | Las decisiones v2 de la cuenta a través de los cortes y de las fronteras de conocimiento, paginadas | 200, 401, 404, 422 |
| `GET /cartera/cortes/{corte_id}/decisiones-v2` | Las decisiones v2 de un corte, en cualquier estado, paginadas | 200, 401, 404, 422 |
| `POST /campo/configuraciones` | Publica una versión de la configuración de campo: gestores, calendarios, excepciones, zona y cupos; el mismo contenido se reutiliza | 200, 201, 401, 422 |
| `GET /campo/configuraciones` | Las versiones de la configuración de campo, la más reciente primero, con sus conteos por base, paginadas | 200, 401, 422 |
| `GET /campo/configuraciones/{configuracion_campo_version_id}` | Una versión con sus calendarios, ventanas, excepciones y gestores, y `hash_verificado` | 200, 401, 404, 422 |
| `POST /campo-v2/planes` | Pide el plan de campo de una fecha con sus entradas declaradas: corte, decisión/v2, territorial/v2, configuración y `fecha_operacion`; lo planea el worker | 201, 401, 404, 409, 422 |
| `GET /campo-v2/planes` | Los planes; filtros `corte_id` y `fecha_operacion`, paginados | 200, 401, 404, 422 |
| `GET /campo-v2/planes/{campo_run_id}` | Un plan: entradas, política con su hash y su cuadrícula, firma, conteos, capacidad y distribución | 200, 401, 404, 422 |
| `GET /campo-v2/planes/{campo_run_id}/asignaciones` | La asignación de cada candidata, sin coordenadas; filtros `estado`, `base`, `gestor`, `prioridad`, `zona`, `subzona`, `tramo` y `microtramo`, paginada | 200, 401, 404, 422 |
| `GET /campo-v2/planes/{campo_run_id}/cargas` | La carga de cada gestor: su jornada en la fecha, su capacidad, lo que recibió y sus ventanas; filtro `base`, paginada | 200, 401, 404, 422 |
| `GET /campo-v2/planes/{campo_run_id}/unidades` | Las zonas, subzonas, tramos y microtramos que ocupó el plan; filtros `nivel`, `padre` y `base`, paginadas | 200, 401, 404, 422 |
| `GET /cuentas/{cuenta_id}/campo-v2` | Los planes de la cuenta, de la fecha de operación más reciente a la más antigua, paginados | 200, 401, 404, 422 |
| `GET /ruteo-v2/redes` | Las versiones de la red vial, la más reciente primero, paginadas | 200, 401, 422 |
| `GET /ruteo-v2/redes/{red_vial_version_id}` | Una versión, con los comandos de su preparación y el SHA-256 de cada archivo | 200, 401, 404, 422 |
| `POST /ruteo-v2/ejecuciones` | Pide rutear un plan de `campo/v2` sobre una versión declarada de la red vial; lo rutea un worker cuyo OSRM responde | 201, 401, 404, 409, 422 |
| `GET /ruteo-v2/ejecuciones` | Las ejecuciones de ruteo/v2; filtros `campo_run_id`, `red_vial_version_id` y `estado`, paginadas | 200, 401, 404, 422 |
| `GET /ruteo-v2/ejecuciones/{ruteo_run_id}` | Una ejecución: entradas, política con su hash, firma, conteos, totales y distribución | 200, 401, 404, 422 |
| `GET /ruteo-v2/ejecuciones/{ruteo_run_id}/rutas` | La ruta de cada gestor, filtrable por gestor, base y estado, paginada | 200, 401, 404, 422 |
| `GET /ruteo-v2/ejecuciones/{ruteo_run_id}/paradas` | Las paradas, filtrables por estado, gestor, base y prioridad, sin coordenadas, paginadas | 200, 401, 404, 422 |
| `GET /ruteo-v2/rutas/{ruta_id}` y `/paradas` | Una ruta con su salida, su regreso y sus totales, y sus paradas en orden de visita | 200, 401, 404, 422 |
| `GET /cuentas/{cuenta_id}/ruteo-v2` | Las paradas de la cuenta en las ejecuciones de ruteo/v2, de la fecha más reciente a la más antigua | 200, 401, 404, 422 |
| `GET /analytics/catalogo` | El catálogo de métricas con su hash, la política con el suyo y los conjuntos con sus llaves | 200, 401 |
| `POST /analytics/ejecuciones` | Pide el análisis de una cohorte con su manifiesto explícito (`corte_id`, `as_of`, `modo`, `zona_horaria`, `granularidad`, `known_at` y cada entrada); lo analiza el worker | 201, 401, 404, 409, 422 |
| `GET /analytics/ejecuciones` | Las ejecuciones de analytics; filtros `corte_id`, `estado` y `as_of`, paginadas | 200, 401, 404, 422 |
| `GET /analytics/ejecuciones/{analytics_run_id}` | Una ejecución: su manifiesto, sus relojes, sus versiones, sus hashes y su disponibilidad | 200, 401, 404, 422 |
| `GET /analytics/ejecuciones/{analytics_run_id}/agregados` | Lo publicado, filtrado en la base por métrica, vista, conjunto, periodo, estado y llave de dimensión, paginado; nada más se acepta | 200, 401, 404, 409, 422 |
| `GET /analytics/ejecuciones/{analytics_run_id}/resumen` y `/embudos` | Unos indicadores del total, cada uno con su estado, y los embudos `CONTACTO` y `CAMPO` | 200, 401, 404, 409, 422 |
| `GET /analytics/ejecuciones/{analytics_run_id}/controles` | Los controles de reconciliación, también los de una `FALLIDA` | 200, 401, 404, 422 |
| `GET /predictivo/features` y `/predictivo/targets` | Las features y los targets de `predictive/model-v1`, versionados, con su hash | 200, 401 |
| `POST /predictivo/entrenamientos` | Pide un entrenamiento con su manifiesto explícito (anclas con sus relojes, su cobertura y su split, e interpretaciones de pagos); lo entrena el worker | 201, 401, 404, 409, 422 |
| `GET /predictivo/entrenamientos` y `/{training_run_id}` | Los entrenamientos (filtro `estado`) y uno con su manifiesto, sus hashes, sus conteos, su backtest y su versión | 200, 401, 404, 422 |
| `GET /predictivo/modelos` y `/{modelo_version_id}` | El registro de versiones (filtro `estado`) y una con su ficha y sus cuatro componentes: artefacto, métricas, intervalos y compuertas | 200, 401, 404, 422 |
| `POST /predictivo/scorings` | Pide el scoring de un corte con una versión `PUBLICADA` declarada (`OPERATIVO` o `BACKTEST` con `known_at`); lo calcula el worker | 201, 401, 404, 409, 422 |
| `GET /predictivo/scorings` y `/{scoring_run_id}` | Los scorings (filtros `estado`, `modelo_version_id` y `corte_id`) y uno con su resumen de drift | 200, 401, 404, 422 |
| `GET /predictivo/scorings/{scoring_run_id}/predicciones` | Las predicciones, filtradas en la base por el rango de cada salida, producto, entidad y estado, y ordenadas por una lista cerrada; paginadas | 200, 401, 404, 409, 422 |
| `GET /predictivo/scorings/{scoring_run_id}/drift` y `/cuentas/{cuenta_id}/explicacion` | El drift por feature y por salida; la explicación de una predicción, verificada con su `feature_hash` | 200, 401, 404, 409, 422 |
| `GET /cuentas/{cuenta_id}/predictivo` | Las predicciones publicadas de la cuenta, de la más reciente a la más antigua | 200, 401, 404, 422 |
| `GET /optimizacion/politica` | `optimization-public-v1` con su hash, la huella del solver y las limitaciones | 200, 401 |
| `POST /optimizacion/configuraciones` | Registra una configuración de optimización (`?publicar=true`) o reutiliza la del mismo contenido | 200, 201, 401, 422 |
| `GET /optimizacion/configuraciones` y `/{configuracion_id}` | Las configuraciones (filtro `estado`) y una con su matriz, sus pools, sus restricciones y `hash_verificado`; `POST .../{configuracion_id}/publicacion` publica un `BORRADOR` | 200, 401, 404, 422 |
| `POST /optimizacion/ejecuciones` | Pide un plan con sus entradas exactas (scoring, `decision/v2`, configuración, fecha y, con `CAMPO` o `BASE`, `ruteo/v2`); lo resuelve el worker | 201, 401, 404, 409, 422 |
| `GET /optimizacion/ejecuciones` y `/{optimizacion_run_id}` | Las ejecuciones (filtros `estado`, `configuracion_id` y `scoring_run_id`) y una con sus entradas, sus hashes, su certificado y su resumen | 200, 401, 404, 422 |
| `GET /optimizacion/ejecuciones/{optimizacion_run_id}/resultados` | Los resultados por cuenta, filtrados en la base por una lista blanca (`estado`, `canal`, `pool`, `producto`, `prioridad`, `base`); paginados | 200, 401, 404, 409, 422 |
| `GET /optimizacion/ejecuciones/{optimizacion_run_id}/capacidad`, `/restricciones` y `/diagnostico` | El uso de cada pool; cada restricción con su lado izquierdo, derecho, holgura y si quedó activa; y por qué terminó como terminó, sin inventar un plan | 200, 401, 404, 409, 422 |
| `GET /optimizacion/ejecuciones/{optimizacion_run_id}/cuentas/{cuenta_id}` y `/cuentas/{cuenta_id}/optimizacion` | Por qué una cuenta quedó como quedó en un plan; lo que cada plan publicado concluyó de ella | 200, 401, 404, 409, 422 |
| `GET /salud` | La API vive y la base contesta. No pide clave | 200, 503 |

Todas las respuestas de error tienen la misma forma, también las que genera el framework:

```json
{
  "codigo": "ARCHIVO_YA_PUBLICADO",
  "mensaje": "Este archivo ya lo publico la corrida 4cce3e0d-….",
  "detalles": [],
  "run_id": "4cce3e0d-…",
  "pagos_run_id": null
}
```

El cliente compara `codigo`, que es estable; `mensaje` es para personas. `run_id` o `pagos_run_id`
dicen con qué corrida o con qué ingesta de pagos tiene que ver el error, si con alguna. En `/docs`,
cada ruta lista sus códigos de error con un ejemplo de cada uno.

Las reglas que un cliente tiene que conocer:

- **`201` quiere decir que el recurso se creó, no que el motor terminó.** Cada `POST` que pide
  trabajo responde `201` con el recurso `EN_PROCESO` y su `Location`, y el worker lo termina
  después. El cliente consulta `Location`, o el flujo, hasta que el `estado` deje de ser
  `EN_PROCESO`: `EXITOSA`, o `FALLIDA` con el motivo en `detalle` y nada publicado. El código HTTP
  describe la petición; el `estado`, cómo terminó el motor (D1).
- **Una etapa se publica con éxito una sola vez por versión de las reglas.** Si ya lo está, el
  `POST` responde `409 DECISION_YA_GENERADA`, `TERRITORIAL_YA_GENERADO` o `RUTEO_YA_GENERADO`, sin
  `Location` y sin nombrar la ejecución, que se descubre en el historial de su fuente (D2).
- **A lo más una en proceso.** Si la misma fuente ya se está procesando con la misma versión, el
  `POST` responde `409 ARCHIVO_EN_PROCESO`, `DECISION_EN_PROCESO`, `TERRITORIAL_EN_PROCESO` o
  `RUTEO_EN_PROCESO`, y no registra otra.
- **Una fuente que no se puede procesar no registra nada:** `409 CORRIDA_NO_PUBLICADA`,
  `DECISION_NO_TERRITORIALIZABLE` o `TERRITORIAL_NO_RUTEABLE`.
- **Las etapas de un flujo las corre el flujo.** Sobre la fuente de un flujo que va a correr esa
  etapa, `409 FLUJO_EN_PROCESO`; si el flujo se detuvo ahí, `409 FLUJO_DETENIDO`, y se reintenta
  con `POST /flujos/{flujo_id}/reanudar`.
- **Reanudar** responde `200` con el flujo otra vez `EN_PROCESO`. Un flujo que sigue en proceso da
  `409 FLUJO_EN_PROCESO`; uno completo, `409 FLUJO_YA_COMPLETADO`; uno detenido en la ingesta, o
  cuya etapa no terminó `FALLIDA`, `409 FLUJO_NO_REANUDABLE`.
- **Lo publicado solo existe para una ejecución `EXITOSA`.** Las decisiones, los municipios, las
  rutas y las paradas de otra dan `409 DECISION_NO_PUBLICADA`, `TERRITORIAL_NO_PUBLICADO` o
  `RUTEO_NO_PUBLICADO`; un municipio sin ruta en esa ejecución, `404 RUTA_NO_ENCONTRADA`.
- **El contrato se declara.** cartera/v2 sin `fecha_corte` da `422 FECHA_CORTE_REQUERIDA`, y
  cartera/v1 con una, `422 FECHA_CORTE_NO_APLICA`, sin guardar nada. Un archivo cuyos bytes no son
  los de su extensión da `415 FORMATO_NO_CORRESPONDE`.
- **Un archivo de pagos se acepta una vez.** Si otra ingesta ya lo aceptó o lo está procesando,
  `POST /pagos` responde `409 PAGOS_YA_ACEPTADOS` o `PAGOS_EN_PROCESO`, con su `pagos_run_id`.
- **Una cuenta canónica nace con un corte, no con un pago.** `GET /cuentas?cliente_unico=…`
  responde `404 CUENTA_NO_ENCONTRADA` si nada trae ese cliente, y `404 SIN_CUENTA_OBSERVADA` si solo
  hay pagos suyos.
- **Los pagos observados no son recuperación.** Son movimientos tal como llegaron: no están
  deduplicados, conciliados, interpretados como reversos ni atribuidos, y la API no los suma.
- **Los movimientos son una interpretación.** `/movimientos`, `/cuentas/{cuenta_id}/movimientos` y
  `resumen_pagos` dicen lo que `motor-pagos/v1` concluye de los pagos observados; no son el libro
  contable del acreedor, y cada respuesta lo advierte en `aviso`. Un movimiento que la
  interpretación vigente de su ventana ya no funda se sigue leyendo, con `vigente: false`.
- **El motor de pagos tampoco se pide: se abre solo** con la historia de cada archivo de pagos.
- **Toda escritura del lifecycle exige `Idempotency-Key`.** La misma llave con la misma petición
  responde `200` con el recurso ya registrado y `Idempotent-Replayed: true`; con otra petición,
  `409 IDEMPOTENCY_KEY_REUTILIZADA`. Nada se actualiza: una corrección es una anulación o una
  cancelación, y lo original sigue visible.
- **`ocurrido_en` lleva su zona horaria** y no puede ser del futuro (`422 OCURRIDO_EN_FUTURO`); un
  evento tardío es válido. `registrado_en` lo pone la base.
- **La evaluación de promesas y la atribución se piden** (`POST /evaluaciones-promesas`,
`POST /atribuciones`) o las encuentra su backfill: no se abren solas. Una fecha de corte posterior a
hoy es `422 AS_OF_FUTURO`, y una ventana sin pagos interpretados, `409 SIN_INTERPRETACION_DE_PAGOS`.
- **Atribuido no es causado, y compatible no es atribuido.** Cada respuesta de la atribución y de la
  evaluación lo dice en su `aviso`.
- **Una decisión v2 declara su frontera, y se pide.** Un `BACKTEST` sin `known_at` es
  `422 KNOWN_AT_REQUERIDO`; un `known_at` futuro, `422 KNOWN_AT_FUTURO`; uno anterior a que el corte
  existiera, `422 CORTE_NO_CONOCIDO`; un `OPERATIVO` sobre un corte que ya no es el más reciente,
  `422 CORTE_NO_VIGENTE`. Las mismas entradas ya decididas dan `409 DECISION_V2_YA_GENERADA`, y un
  corte que se está decidiendo, `409 DECISION_V2_EN_PROCESO`. Cada respuesta trae sus advertencias:
  si es un backtest, si `known_at` es posterior al día y si faltaban pagos o atribuciones.
- **Un plan de campo/v2 declara todas sus entradas, la fecha incluida.** Sin `fecha_operacion`,
  `422 ENTRADA_INVALIDA`; una anterior al corte, `422 FECHA_ANTERIOR_AL_CORTE`; una decisión o una
  territorial/v2 de otro corte, `422 CORTE_INCONSISTENTE`; una configuración sobre otras bases que
  la territorial/v2, `422 BASES_INCONSISTENTES`. Las mismas entradas ya planeadas dan
  `409 CAMPO_V2_YA_GENERADO`, y las que se están planeando, `409 CAMPO_V2_EN_PROCESO`. Cada plan
  dice en su `aviso` que la capacidad es declarada, no factibilidad vial.
- **Un análisis declara su manifiesto y sus dos relojes.** Una entrada que no terminó `EXITOSA`, que
  no existía a `known_at`, de otro corte o que no encadena con las demás es un `422` con su código
  (`ENTRADA_NO_CONOCIDA`, `CORTE_INCONSISTENTE`, `ENCADENAMIENTO_INCORRECTO`, ...), y nada se
  registra. Las mismas entradas ya analizadas dan `409 ANALYTICS_YA_GENERADA`; los agregados, el
  resumen y los embudos de una ejecución que no está `EXITOSA`, `409 ANALYTICS_NO_PUBLICADA`. Un
  parámetro de los agregados que no es del catálogo es `422 ENTRADA_INVALIDA`.
- **La historia no se pide: se abre sola** con cada dataset conformado. Mientras la ingesta sigue en
  proceso, `/historia` responde `409 CORRIDA_EN_PROCESO` o `PAGOS_EN_PROCESO`; una corrida de
  cartera/v1, o que no publicó, `404 SIN_DATASET_CONFORMADO`.

Por qué cada código es el que es (201 y no 202, 422 y no 400, cuándo 409, por qué un
archivo con registros inválidos no es un error HTTP, por qué una ejecución que termina `FALLIDA`
también nació con un `201`), y el resto de las decisiones, están en
[docs/decisiones.md](decisiones.md).
