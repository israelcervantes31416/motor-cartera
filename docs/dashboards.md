# Las pantallas de la app

Qué muestra cada pantalla de la app (v0.17), de dónde sale cada número y qué no hace. Todas leen
un contexto operativo ([contexto_operativo.md](contexto_operativo.md)); la app y su API,
[app.md](app.md); cómo está hecha, [frontend.md](frontend.md).

Tres reglas valen para todas:

- **El backend es la fuente de verdad.** Una pantalla ordena, pagina, filtra, formatea y dibuja lo
  que la API publicó; no recalcula una métrica, un score, una prioridad, un plan, una ruta, una
  atribución ni un estado. Un total que la API no publicó no se suma en el navegador.
- **Un valor que no está no es cero.** `NO_DISPONIBLE`, `SUPRIMIDO`, `NO_APLICA`, `CENSURADO` y el
  valor publicado sin valor se muestran con su estado (una insignia con texto e icono, no solo un
  color), nunca como 0. Una celda suprimida no se grafica ni se reconstruye (ni como TOTAL menos el
  resto).
- **Nada se ejecuta.** Ningún botón crea una gestión, envía un mensaje, llama, visita, optimiza,
  entrena, publica ni cambia una cuenta. Los botones de la app son de navegación, paginación,
  copia de un identificador y reintento de una consulta.

Toda gráfica tiene título, descripción y su tabla equivalente detrás de *Ver datos*; con más de 20
categorías, solo la tabla, paginada.

## El selector de contexto (`/`)

Las carteras del despacho, los cortes de la elegida (del más reciente al más antiguo, de 10 en 10)
y, con un corte, los contextos compatibles que la API propone y las ejecuciones de cada tipo para
declararlas a mano, validadas mientras se arma el contexto. Nada se abre solo: la persona elige
*Abrir este contexto*. Fuente: `GET /app/v1/contextos/opciones` y `GET /app/v1/contextos/validar`.

## El resumen (`/resumen`)

Un bloque por motor, cada uno con su fuente (la ejecución, su estado, su identificador completo
copiable y sus relojes):

| Bloque | Lo que muestra | De dónde sale |
| --- | --- | --- |
| Cartera, recuperación y gestión | cuentas y saldo inicial de la cohorte, recuperación neta y sobre el saldo inicial, cuentas gestionadas, contactadas sobre gestionadas, promesas creadas y su cumplimiento, pagos con asociación única, candidatas y asignadas en campo, paradas; los embudos de contacto y de campo | el TOTAL publicado por `analytics/v1`, con su estado de valor |
| Decisión | cuentas decididas, por prioridad final y por canal recomendado | la distribución que contó `decision/v2` al publicar |
| Predictivo | predicciones publicadas y con imputación, la media de cada probabilidad, la suma de la recuperación positiva esperada a 30 días, el histograma de `p_recuperacion_positiva_30d` por décimo y el drift por severidad | el scoring y sus predicciones, contadas en la base (`app/v1`) |
| Optimización | el estado del solver, cuentas asignadas, elegibles y del universo, el valor de oportunidad del plan, el gap relativo, el tiempo del solver y la capacidad por canal | la ejecución de `optimization/v1` |
| Campo | candidatas, asignadas, sin capacidad, sin gestor, revisión territorial, rebalanceadas y utilización de la capacidad declarada | el plan de `campo/v2` |
| Ruteo | rutas completas y parciales, paradas programadas, sin tiempo, no enrutables, con base no enrutable, distancia y duración vial | la ejecución de `ruteo/v2` |

Un bloque cuya ejecución no se declaró dice `NO_DECLARADA`; una `EN_PROCESO`, que todavía no
publica; una `FALLIDA`, que terminó sin publicar. Los avisos visibles: analytics no prueba
causalidad, la atribución es una asociación operacional, una predicción no es un efecto causal ni
una recomendación, el plan es óptimo respecto al modelo declarado y una parada programada es un
plan, no una visita. Fuente: `GET /app/v1/resumen`, más el catálogo de `analytics/v1` para los
nombres de sus métricas.

## Las cuentas (`/cuentas`)

Las cuentas del corte, paginadas en el servidor (25, 50 o 100 por página; 50 por omisión), con su
identificador técnico (`cuenta_id`, nunca su `CLIENTE_UNICO`), producto, saldo total, días y tramo
de atraso y, de cada ejecución declarada, su columna: prioridad y canal recomendado
(`decision/v2`), `p_recuperacion_positiva_30d` y la recuperación positiva esperada (el scoring) y el
estado en la optimización. Se ordena por cuenta, saldo, días de atraso o una de las dos del scoring,
y se filtra por el principio del `cuenta_id` (cuatro o más caracteres hexadecimales, cuando se deja
de escribir), producto, tramo de atraso, prioridad, canal, estado de la predicción y estado en la
optimización; nunca por nombre, dirección o teléfono, que la app no tiene. Los filtros, el orden y
la página viven en la URL. Fuente: `GET /app/v1/cuentas`.

## La Cuenta 360 (`/cuentas/{cuenta_id}`)

Once pestañas, cada una una URL; las que leen listas o explicaciones las piden al abrirse:

| Pestaña | Lo que muestra |
| --- | --- |
| Resumen | el snapshot de la cuenta en el corte (producto, saldos, atraso, canal y estrategia de la fuente, entidad y municipio), su presencia en la cartera y lo que publicó cada ejecución del contexto |
| Historia | la línea de tiempo (cada evento con `ocurrido_en` y `registrado_en`, y lo registrado después del `known_at` del contexto, marcado) y los snapshots por corte |
| Pagos | los movimientos económicos canónicos (pagos, reversos, posibles reversos, con su estado y su fecha) y su atribución: `ASOCIACION_UNICA`, `AMBIGUA` o `SIN_GESTION_CANDIDATA`, con *Asociación operacional; no implica causalidad.* Un pago observado no es una recuperación. |
| Gestiones | cada gestión con sus dos relojes, su canal, su medio, su nivel de contacto y su resultado; sin la observación (texto libre) ni quién la registró |
| Decisión | la prioridad base y final, el ajuste, el canal recomendado, los motivos y las features de `decision/v2`, con su hash verificado: un motor de reglas, no una probabilidad |
| Geografía y territorial | el estado y la calidad de la geografía, el municipio y la entidad, la base asignada y su distancia; nunca una coordenada |
| Campo | el estado de la asignación, el gestor (sintético), la base, si fue rebalanceada, su lugar en la cola y la carga y la capacidad del gestor |
| Ruteo | la parada: su estado, el estado de la ruta, el orden de visita, la ETA, el servicio, la espera, los tramos y su motivo; sin mapa ni polilínea |
| Predictivo | las tres probabilidades, el riesgo complementario, la recuperación positiva esperada, el estado (completa o con imputación), la versión del modelo, `as_of`, `known_at` y el `feature_hash`, y las contribuciones de cada componente (*contribución al score*, nunca *causa*), con *Predicción, no efecto causal ni recomendación de acción.* |
| Optimización | `ASIGNADA`, `NO_SELECCIONADA` o `NO_ELEGIBLE`, el canal y el pool, el valor de oportunidad, las unidades de recurso, el motivo y lo que el plan no dice, con *Óptimo respecto al modelo declarado; no estrategia causalmente óptima.* |
| Evidencia | los identificadores, las versiones y los hashes de cada ejecución que publicó algo de la cuenta |

Una cuenta que no existe es un aviso con un enlace a las cuentas; un identificador que no es UUID no
se pide. Fuente: `GET /app/v1/cuentas/{cuenta_id}` y sus subrutas (`/decision`, `/historia`,
`/linea-de-tiempo`, `/pagos`, `/gestiones`), más la explicación de la predicción y la de la
optimización por sus rutas de dominio.

## Analytics (`/analytics`)

La definición de la métrica elegida, tomada del catálogo (código, unidad, fórmula, numerador,
denominador, población, regla temporal, fuentes, cuándo no está disponible y limitaciones), su serie
por periodo en el TOTAL (una línea con huecos donde no hay valor), su desglose por el conjunto
elegido (barras con su tabla, y una composición apilada cuando la métrica es aditiva y tiene de 2 a 7
celdas visibles y no negativas, que dice si hay suprimidas), los embudos de contacto y de campo y los
controles de reconciliación. La métrica y el conjunto viven en la URL. La app no copia definiciones:
las lee del catálogo de la API. Fuente: `analytics/v1` (`/analytics/catalogo`,
`/analytics/ejecuciones/{id}`, `/agregados`, `/embudos`, `/controles`).

## Predictivo (`/predictivo/{vista}`)

| Vista | Lo que muestra |
| --- | --- |
| Modelo | la ficha del modelo: versión, entrenamiento, periodos de TRAIN, VALIDATION y TEST, versiones y hashes de features, targets y política, hash del modelo y del dataset, uso previsto y no previsto, cada componente (target, algoritmo, calibración, estado, prevalencia en TEST, artefacto) y sus limitaciones |
| Desempeño | por componente, en TEST: ROC AUC, PR AUC, Brier, log loss y ECE (clasificación) o MAE y RMSE (regresión), siempre junto a su baseline, con lift y captura por decil y sus compuertas |
| Calibración | la predicción media de cada bin contra la frecuencia observada, con su tabla, sin suavizar |
| Scoring | el scoring y su distribución, y las predicciones publicadas, paginadas y ordenadas en el servidor |
| Drift | cada feature y cada salida con su PSI, sus faltantes en TRAIN y en el scoring y su severidad, ordenable, y la distribución de severidades, con *Drift no implica automáticamente degradación del modelo.* |

Fuente: `predictive/model-v1` (`/predictivo/modelos/{id}`, `/predictivo/scorings/{id}`, `/drift`,
`/predicciones`) y `GET /app/v1/predictivo/scorings/{id}/distribucion`.

## Optimización (`/optimizacion/{vista}`)

| Vista | Lo que muestra |
| --- | --- |
| Resumen | el estado del solver, el valor de oportunidad del plan, la mejor cota, el gap relativo, asignadas, elegibles, no elegibles, el universo, el tiempo del solver y la reducción exacta de candidatas, y lo que el plan no dice |
| Capacidad | cada pool con su capacidad, lo usado, la holgura y la utilización, y la utilización por canal (barras horizontales con su tabla) |
| Restricciones | cada restricción con su código, su tipo, su lado izquierdo, su operador, su lado derecho y su holgura; las activas (*binding*) señaladas con texto, no solo con color |
| Asignaciones | el resultado de cada cuenta, paginado y filtrado en el servidor por canal, pool, estado, prioridad y base |
| Solver y auditoría | el solver y su huella, variables, restricciones, no nulos, estado, tiempo, gap y cota, el certificado de cada nivel, los hashes y el diagnóstico |

Una ejecución sin óptimo demostrado (`TIME_LIMIT`, `INFEASIBLE`) no publica un plan, y la pantalla
lo dice con su estado y su resultado: no se esconde. Fuente: `optimization/v1`.

## Campo (`/campo`) y Ruteo (`/ruteo`)

Campo: las candidatas, las asignadas, sin capacidad, sin gestor, en revisión territorial y
rebalanceadas, la utilización de la capacidad declarada (capacidad declarada, no factibilidad vial),
el plan por base y la carga de cada gestor sintético. Ruteo: los gestores con ruta, las rutas
completas y parciales, las paradas programadas, sin tiempo y no enrutables, la distancia, la
duración vial, el servicio y la espera, y cada ruta (gestor, base, estado, paradas, programadas,
distancia, duración). El detalle de una ruta (`/ruteo/rutas/{ruta_id}`) da sus paradas en orden de
visita, con la cuenta técnica, el estado, la ETA, el servicio y el motivo. Sin mapas: ni puntos, ni
tramos, ni polilíneas. Fuente: `campo/v2` (el plan y sus cargas), `ruteo/v2` (la ejecución, sus
rutas, una ruta) y `GET /app/v1/ruteo/rutas/{ruta_id}/paradas`.

## Auditoría y linaje (`/auditoria`)

De dónde salió cada resultado: el corte y cada ejecución del contexto, con lo que cada una declaró
que leyó al publicarse (la territorial y la geografía de un plan de campo, la red vial de un ruteo,
el entrenamiento y la versión del modelo de un scoring, la configuración de una optimización, las
entradas del manifiesto de una analytics), cada una con su identificador, su versión, su política,
sus hashes, sus relojes y su estado, y cada relación nombrada. Solo relaciones reales: las que una
ejecución registró, nunca inferidas. Los hashes largos se abrevian a la vista y se copian completos.
Fuente: `GET /app/v1/linaje`.

## Lo que ninguna pantalla hace

- No ejecuta nada, no escribe nada y no tiene un botón de acción.
- No recalcula métricas, scores, prioridades, planes, rutas, atribuciones ni estados.
- No muestra un dato personal: ni nombre, ni dirección, ni teléfono, ni `CLIENTE_UNICO`, ni CURP,
  ni RFC, ni una coordenada, ni quién registró una gestión ([privacidad_app.md](privacidad_app.md)).
- No dibuja mapas, puntos, rutas ni microtramos.
- No exporta datos desde el navegador (ni CSV ni XLSX): las exportaciones son de la línea de
  comandos.
- No afirma causalidad: ni que una acción causará algo, ni que un canal incrementará la recuperación,
  ni un ROI esperado, ni *uplift*.
