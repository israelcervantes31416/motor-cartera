# Limitaciones actuales

Lo que motor-cartera no hace hoy, o hace con un límite conocido, por área y sin maquillar. Cada una
se revisó contra el código de v1.0.0: lo que una versión posterior resolvió ya no está aquí. Lo que
es del diseño, y no un pendiente, va al final. Cada motor detalla las suyas en su documento
([índice](README.md)). Lo que falta para operar con un despacho real, ordenado por lo que bloquea,
está en [implantacion.md](implantacion.md).

## Alcance

- **Datos sintéticos.** Todo lo que el sistema procesa lo produce su generador; las referencias
  públicas (la cartografía del INEGI y las redes de OpenStreetMap) son reales y se descargan fuera
  del repositorio. Ningún resultado describe una cartera real.
- **No hay un despliegue en la nube.** La referencia de Google Cloud (`infra/gcp`, en OpenTofu) se
  valida en cada run del CI y nunca se aplicó; le falta, entre otras cosas, la plantilla del edge
  para correr detrás del balanceador ([cloud-gcp.md](cloud-gcp.md#lo-que-falta-para-un-despliegue-real)).
- **Sin historia de operación real.** Los objetivos de servicio y los umbrales de las alertas son
  iniciales, y no hubo un pentest externo.
- **Medido en una máquina de desarrollo, que no era dedicada.** Los benchmarks de cada motor dicen
  su máquina y su variación entre corridas; nada se midió en un servidor de producción. El perfil M
  se mide cada semana en runners compartidos de GitHub: una señal de regresión, no un objetivo de
  servicio ([rendimiento.md](rendimiento.md)).
- **Nada actúa sola.** La app es de solo lectura y ningún motor ejecuta una acción de cobranza; las
  predicciones y los planes óptimos corren en sombra.

## Producción, seguridad y operación

- **Desarrollo y producción no se parecen en el acceso.** El compose de desarrollo no tiene
  usuarios: una sola clave de servicio, puesta del lado del servidor por el proxy de la app, y la
  app lo dice a la vista («Desarrollo local sin usuarios (clave de servicio del proxy); no desplegar
  así ni con datos reales»); publica solo en `127.0.0.1`. El compose de producción tiene usuarios
  por OpenID Connect, cuatro permisos por ruta y claves de servicio guardadas por su hash, cada una
  con sus permisos ([security.md](security.md), [runbooks/secret-rotation.md](runbooks/secret-rotation.md)).
- **El tráfico interno no va cifrado**: entre el edge, la API, la base y OSRM, en redes de Docker
  aisladas.
- **Dex es solo para desarrollo y pruebas**, y no bloquea una cuenta por intentos fallidos: el edge
  limita los envíos de contraseña por IP. Un despliegue usa el IdP corporativo.
- **Una sola PostgreSQL, sin réplica**, en el compose: sin alta disponibilidad.
- **La auditoría no es a prueba de manipulación**: depende de la plataforma de logs.
- **Las imágenes de terceros se escanean sin bloquear el CI**, y las propias no son reproducibles bit
  por bit (las correcciones de Alpine se aplican al construir).
- **Las subidas se acotan al copiarse, no al recibirse.** `MC_TAMANO_MAXIMO_MB` corta un archivo
  mientras se copia al almacén, cuando el servidor ya recibió la subida completa, y no hay tope a lo
  que un zip descomprime. Los demás cuerpos sí se cortan al llegar (`MC_TAMANO_MAXIMO_CUERPO_KB`), y
  en producción el edge solo deja pasar hacia la API los `GET` de su lista blanca.
- **El almacén de artefactos es un directorio local**: un volumen en el compose, sin réplica. Los
  respaldos lo llevan junto con la base y su restauración se ensaya ([backups.md](backups.md)), pero
  no hay un almacén de objetos: en la referencia de Google Cloud es Filestore, y uno nativo en GCS
  queda detrás de la misma interfaz, sin implementar.
- **Los huérfanos no se recolectan.** Si la base falla después de guardar un objeto, el objeto se
  queda en el almacén sin una fila que lo registre. Ocupa disco y nada más; no hay limpieza.
- **Sin retención de ejecuciones.** Cada ejecución publicada queda en PostgreSQL, inmutable, y cada
  frontera de conocimiento que se pide es otra ejecución completa; las tablas que crecen con cada
  corte no están particionadas. El mantenimiento de tablas de decenas de millones de filas es de la
  operación.
- **El compose se prueba en Linux en el CI.** En la máquina de desarrollo (Windows 11 con Docker
  Desktop sobre WSL2) se verificó lo que dice [desarrollo.md](desarrollo.md#validación-local-en-windows);
  macOS no está probado.
- **Python 3.12 a 3.14.** Las tres se prueban en la matriz de compatibilidad, sin infraestructura; la
  suite completa, los compose y las imágenes corren solo en 3.12. Python 3.15 no se soporta: el
  `uv.lock` no se instala en ella sin compilar pyarrow ([versioning.md](versioning.md)).
- **No se publican imágenes.** Cada release trae el OpenAPI y el SBOM de las imágenes que probó el
  CI, con su procedencia, pero no las imágenes: se construyen desde la etiqueta
  ([cadena-de-suministro.md](cadena-de-suministro.md#las-releases)).

## La orquestación

- **PostgreSQL es la cola.** No hay un broker dedicado: los workers preguntan por trabajo cada
  `MC_WORKER_POLL_SEGUNDOS`, y la cola comparte la base con todo lo demás.
- **Un worker procesa un trabajo a la vez.** Escalar es correr más procesos worker
  (`docker compose up --scale worker=3`); no hay autoscaling.
- **Once niveles de prioridad, y nada más.** Entre los trabajos que ya se pueden tomar van primero
  los operacionales, después los `HISTORIA`, los `MOTOR_PAGOS`, los `ATRIBUCION` y
  `EVALUACION_PROMESAS`, los `DECISION_V2` y `GEOGRAFIA`, los `TERRITORIAL_V2`, los `CAMPO_V2`, los
  `RUTEO_V2` (que solo toma un worker cuyo proveedor de ruteo responde), los `ANALYTICS`, los
  `PREDICTIVE_TRAINING` y `PREDICTIVE_SCORING` y al final los `OPTIMIZATION`; dentro de cada nivel,
  en el orden en que se crearon. No hay prioridades por despacho, por cartera ni por urgencia.
- **Sin cola de mensajes muertos externa.** Un trabajo que agota sus intentos queda `FALLIDO` en su
  tabla, con su recurso `FALLIDA` y su flujo `DETENIDO`, y lo avisa la alerta `MCTrabajosFallidos`
  ([runbooks/worker-stuck.md](runbooks/worker-stuck.md)).
- **Los motores v2 no se abren solos.** `decision/v2`, la geografía, `territorial/v2`, `campo/v2`,
  `ruteo/v2`, analytics, los entrenamientos, los scorings y la optimización se piden con sus
  entradas declaradas, o los encuentra su backfill; ningún corte nuevo los dispara. La evaluación de
  promesas y la atribución tampoco: una gestión tardía deja desactualizada la atribución de su
  ventana hasta que se vuelve a pedir.

## Las fuentes y la ingesta

- **Leer un xlsx grande es lento.** openpyxl, en modo de solo lectura, lee del orden de 90,000
  celdas por segundo: una cartera XL en xlsx tarda varios minutos solo en leerse. Para XL y XXL,
  csv o zip.
- **Una cartera se publica una vez por archivo, no por contenido.** La misma cartera en xlsx y en
  csv tiene dos firmas de archivo y se publica dos veces en la capa operacional. Su firma de
  contenido, que es la misma, lo deja a la vista, y el modelo histórico la reconoce como una fuente
  equivalente y no duplica su corte, pero la operación no lo impide.
- **cartera/v1 no valida contra el catálogo del INEGI**: revisa la forma de las claves, no que
  existan, y `territorial/v1` agrupa por esas mismas claves. En cartera/v1, un saldo con más de dos
  decimales se redondea al guardarse en lugar de rechazarse, y el archivo se lee entero en memoria.
  cartera/v2 resuelve sus claves con el catálogo, rechaza un importe con más de dos decimales y se
  lee por lotes.
- **La geografía de la proyección es por municipio.** El estado y la población se resuelven a
  claves municipales del INEGI con un catálogo que es una foto (consultada el 2026-10-05) y se
  actualiza con su script; localidades, colonias y códigos postales no se usan.
- **El escenario longitudinal no es un modelo financiero**: es coherencia básica y determinismo.
- **El control de archivos revisa lo trackeado, no la historia.** Un archivo que se subió y después
  se borró sigue en la historia, y en un repositorio público ya salió; gitleaks revisa el historial
  en busca de secretos, no de datos. Un csv o un volcado en SQL plano con otro nombre no se reconocen
  por sus bytes, y el control confía en el `.gitignore` del mismo commit.

## El modelo histórico

- **Un conflicto de corte no se corrige.** Si llega otra cartera de un corte que ya tiene historia,
  con otro contenido, su historia falla con `CORTE_CANONICO_CONFLICTIVO` y el corte publicado no
  cambia. No hay una corrección explícita (reemplazar un corte, con su motivo y su auditoría): el
  conflicto queda registrado con las dos firmas.
- **Solo la cartera oficial tiene historia.** Las corridas de `cartera/v1` no publican un dataset
  conformado, así que no tienen cortes canónicos ni snapshots.
- **La geografía de un snapshot es la del catálogo de su versión.** Si el catálogo cambia, tiene que
  cambiar también la versión del modelo, como la de la proyección.
- **Los eventos de presencia se calculan al consultar.** Con unos cientos de cortes por cartera es
  inmediato; con miles, habría que medir otra vez antes de guardarlos.
- **La Cuenta 360 se busca por su `CLIENTE_UNICO` exacto**, en la cartera del sistema. El explorador
  de `app/v1` lista y filtra las cuentas de un corte; no hay consultas entre carteras.

## El motor de pagos

- **Una coincidencia ambigua no se resuelve.** Dos observaciones con la misma llave histórica y otro
  campo distinto no fundan movimiento ni suman: su importe se informa aparte.
- **Un reverso se empareja solo si la pareja es aislada.** Con varios candidatos, un candidato
  ambiguo o un original disputado, queda como posible reverso: resta de la neta y no anula nada. La
  ventana de 30 días es una regla de `v1`, sin evidencia empírica.
- **No lee `Concepto_Cálculo`, `Captación` ni `Cobranza_Total`** para decidir signos ni tipos, y
  `Gestor`, `Fecha_de_Gestion` y `Campaña` quedan como atributos observados.
- **Un pago sin cuenta no se concilia solo cuando llega su corte**: lo hace
  `backfill-motor-pagos --reconciliar`, con una interpretación nueva.
- **Cada interpretación es completa.** Una llegada tardía reinterpreta entero cada mes que toca, y
  su vecina si cambia su contexto: en el XL, de 5 a 7 minutos y de 1.8 a 2.2 GB de WAL por mes.
- **Las consultas de toda la cartera, o de una ventana sin el cliente, recorren lo que filtran**: en
  el XL, de 1.6 a 4.9 s de mediana; con un cliente o una cuenta, de 4 a 9 ms (decisión 98).
- **La recuperación interpretada no es contabilidad.** El saldo oficial sigue siendo el del snapshot.

## El lifecycle y la atribución

- **La atribución no mide causalidad**, ni el aporte de una gestión, de un canal o de un actor, y no
  elige entre candidatas: un pago con dos gestiones con contacto antes queda `AMBIGUA`. La ventana
  de 30 días es una política del demo, sin evidencia empírica.
- **No hay un gestor canónico**: quien hizo una gestión es un `actor_ref` opaco, sin catálogo ni
  relación con el `Gestor` de `pagos/v1` ni con los gestores de la configuración de campo.
- **Un convenio no es un ledger**: ningún pago se aplica a una cuota, y su estado no se evalúa.
- **Las visitas no tienen geografía** (sin GPS ni geocercas), y una visita registrada no se liga a
  un plan de `campo/v2` ni a una parada de `ruteo/v2`: lo planeado no se compara con lo hecho.
- **Lo que un corte dice de una promesa o de un plan no se vuelve un evento**: se muestra como
  `OBSERVACION_EN_CORTE`, y ninguna regla decide si dos observaciones son la misma promesa.
- **La importación es para datos sintéticos e integraciones de prueba**, no una fuente oficial: no
  tiene contrato de archivo, almacén ni historia.
- **Escribir es lo que cuesta.** Cargar un periodo XL (unos 230,000 eventos) toma de 42 a 207 s;
  atribuir un mes de un millón de pagos, de 2 a 4 minutos, y cada atribución nueva de una ventana
  escribe otra vez todos sus pagos (decisión 112).

## decision/v2

- **La política es sintética.** `decision-policy/public-v1` demuestra la capacidad del motor; no
  representa la política de ningún acreedor real, y nada dice que una de sus prioridades sea mejor
  que la de v1.
- **No usa predicciones ni mide efectividad.** `predictive/model-v1` corre aparte y ninguna regla de
  `decision/v2` lo lee; una asociación única de la atribución no dice que la gestión produjo el pago.
- **No reemplaza a `decision/v1`.** `territorial/v1` y `ruteo/v1` siguen leyendo v1; v2 corre en
  sombra y alimenta a `campo/v2`, analytics y la optimización.
- **La disponibilidad es de la cartera, no de cada cuenta.** Si a `known_at` falta interpretar un
  mes de pagos, toda la cartera queda con esa ventana en NULL.
- **La frontera depende de lo que PostgreSQL deja ver.** `pg_stat_activity` solo muestra el inicio
  de la transacción de las sesiones del mismo rol; con varios roles, el del worker necesita
  `pg_read_all_stats`. Una transacción que se queda abierta en la base detiene las decisiones con un
  `known_at` posterior a su inicio: cada intento espera 30 s y vuelve a la cola, y si dura más que
  todos sus reintentos, la decisión termina `FALLIDA` con `INTENTOS_AGOTADOS`.
- **Cada decisión ocupa lo que ocupa.** La de un corte XL escribe 513 MiB y 879 MiB de WAL
  (decisión 121).

## La geografía y territorial/v2

- **No geocodifica direcciones.** Calle, colonia y código postal no se convierten en coordenadas,
  ni con un servicio externo ni con uno propio, y ningún centroide hace las veces de una ubicación
  observada.
- **No corrige coordenadas ni resuelve discrepancias.** Un orden invertido se señala y no se
  invierte; cuando el punto y la fuente dicen municipios distintos, la cuenta va a revisión.
- **Una referencia, un nivel.** Solo la capa municipal del Marco Geoestadístico Integrado 2025. Sus
  áreas son geoestadísticas, no la división político-administrativa oficial, y 942 pares de
  municipios vecinos se enciman en astillas de 31.9 m² en total: un punto ahí es
  `MUNICIPIO_AMBIGUO`.
- **Pertenencia, no capacidad.** `territorial/v2` no conoce gestores, jornadas ni cargas (eso es
  `campo/v2`); su distancia es geodésica, en línea recta, y la tolerancia de empate de 100 m es un
  parámetro de la política, sin evidencia empírica.
- **No reemplaza a `territorial/v1`.** El flujo de cada corrida y `ruteo/v1` siguen con el
  municipio de la fuente y su plano sintético.

## campo/v2

- **Capacidad declarada, no factibilidad vial.** El cupo de un gestor es una declaración; si sus
  cuentas caben en su jornada lo dice `ruteo/v2`, sin cambiar el plan.
- **Una cuenta, una unidad.** Sin pesos por tipo de visita, saldo o dificultad, y una media jornada
  no reduce el cupo.
- **Sin rebalanceo entre bases.** El déficit de una base y el sobrante de otra se ven en la
  distribución del plan; se corrigen con otra configuración o con otras bases.
- **Una partición fija.** Las celdas no se ajustan a la densidad, y dos cuentas a pocos metros
  pueden quedar en microtramos distintos si una frontera pasa entre ellas (decisión 152).
- **Gestores sin identidad.** Un código no se liga a una persona, a un `actor_ref` ni al `Gestor` de
  los pagos.

## ruteo/v2

- **Una heurística, no un óptimo.** El vecino más cercano por prioridad con un 2-opt acotado no es
  un TSP, ni un VRP, ni un VRPTW, y `optimization/v1` no reoptimiza rutas.
- **ETA planificadas, sin tráfico.** Las duraciones son las del perfil `car`, estáticas: sin
  tráfico, clima, incidentes, estacionamiento ni velocidades por hora del día.
- **Un servicio de 900 s para todas**, sin medir, y sin ventanas horarias por cuenta.
- **El snap de 500 m no se calibró.** Es un parámetro de la política, sin evidencia de una
  operación.
- **Las redes son reales, las cuentas no.** El benchmark de Puebla usa OpenStreetMap con puntos
  sintéticos al azar dentro de cada municipio, y su plan XL repite coordenadas (99,720 cuentas en
  7,592 puntos distintos), así que sus matrices fueron chicas.
- **La red no se actualiza sola.** Un extracto nuevo es otra versión, que alguien prepara, publica y
  declara. Dos preparaciones de la misma red dan los mismos snaps y las mismas duraciones, pero una
  distancia puede diferir en un metro: la reconstrucción byte por byte pide la preparación publicada.
- **Un solo perfil y un solo motor**: `car` de OSRM v26.9.0.
- **Sin geometrías ni mapas.** La ruta no se dibuja, tampoco en la app: lo que se publica es el
  orden, las horas y los tramos.

## analytics/v1

- **Describe; no prueba causalidad.** Una asociación de pagos con gestiones no es un impacto:
  medirlo exige un diseño (un grupo de control, un experimento) que estos datos no tienen.
- **Planificado no es ejecutado.** Una parada programada no es una visita realizada.
- **Un gestor de campo no es un `actor_ref`**, y no hay métricas por persona ni rankings de
  cobradores.
- **Sin multimoneda.** Los montos están en la moneda de la fuente, sin conversión.
- **La supresión es conservadora, no una garantía formal** contra cualquier combinación de restas
  entre conjuntos distintos; el umbral (10) es de la política, con su hash.
- **La cobertura de pagos es conservadora.** `pagos/v1` no declara el periodo de un archivo: un día
  sin movimientos al borde de un archivo queda sin cobertura, y sus métricas `NO_DISPONIBLE`.

## predictive/model-v1

- **Los modelos predicen resultados observados bajo la política histórica.** No estiman causalidad
  ni dicen qué acción debe ejecutarse.
- **El benchmark sintético valida la arquitectura y la estadística; no demuestra el desempeño real.**
  El generador fija la relación entre las features y los resultados.
- **La recuperación positiva esperada no es la recuperación neta esperada**: los netos negativos se
  truncan a cero por definición.
- **Un PSI alto no prueba por sí solo un deterioro**: mide un cambio de la población; el desempeño
  se mide con `evaluar-predictivo` cuando los labels maduran.
- **No hay reentrenamiento automático**, aprendizaje en línea ni ML causal, y no hay uplift. Cambiar
  un modelo es otro entrenamiento, con su manifiesto.
- **GLM, no más.** Sin árboles, boosting ni redes: la primera versión fija el baseline auditable.
- **Sin backfill predictivo**, y **una cartera por modelo**: un scoring de otra cartera se calcula y
  su drift lo dice.

## optimization/v1

- **Es óptimo respecto al modelo declarado. No es una estrategia causalmente óptima.** Su objetivo
  es la recuperación positiva esperada bajo la política histórica, no el beneficio incremental de
  contactar, y un canal asignado no es la evidencia de que ese canal cause una mayor recuperación.
- **Las capacidades del benchmark son sintéticas**, y las unidades de recurso no son costos reales.
- **`CAMPO` no reoptimiza rutas** ni mueve cuentas entre gestores o bases, y no hay planeación
  dentro del día. No hay ejecución automática.

## La app

- **Es de solo lectura.** No ejecuta acciones de cobranza: ni gestiones, ni mensajes, ni llamadas,
  ni visitas, ni promesas, ni planes, ni entrenamientos.
- **No muestra datos personales ni coordenadas**, no dibuja mapas y no exporta desde el navegador.
- **Ordenar por saldo o por días de atraso tarda cerca de un segundo** sobre 500,000 cuentas
  (recorre el corte); un índice lo bajaría a milisegundos a cambio de cientos de MB y de tiempo en
  cada ingesta, y no se agregó sin un uso que lo pida.
- **Sin modo oscuro.**

## Los motores v1

- **`territorial/v1` solo consume `decision/v1`**: las decisiones de otra versión dan
  `409 DECISION_NO_TERRITORIALIZABLE`.
- **`ruteo/v1` usa coordenadas sintéticas**: un plano por municipio, sin calles, tráfico ni tiempos
  reales, una ruta por municipio sobre todas sus cuentas `CAMPO` y sin gestores, vehículos,
  capacidades ni ventanas de tiempo ([motores_v1.md](motores_v1.md)).
- **El 2-opt de `ruteo/v1` no está medido con municipios grandes**: cada pasada es `O(n²)` en las
  paradas de un municipio.
- **`territorial/v1` y `ruteo/v1` se miden con la cartera de la prueba de humo** (9,800 cuentas), no
  a la escala de 500,000. `decision/v1` sí: decidió las 465,999 cuentas de un corte XL en 34.6 s.

## Del diseño, no pendientes

- **Entrega al menos una vez.** Un motor se puede ejecutar más de una vez sobre el mismo recurso si
  un worker muere en el momento justo; lo que no se repite es la publicación
  ([orquestacion.md](orquestacion.md)).
- **La migración `0006` cierra lo que encontró en proceso.** Cada corrida o ejecución que estaba
  `EN_PROCESO` al migrar queda `FALLIDA`, con un motivo que lo dice, y su `downgrade` no la reabre.
  Las corridas que publicaron antes de v0.5.0 no tienen flujo: sus etapas se piden a mano.
- **CARRIER se audita, no se publica**, y sus advertencias no bloquean nada.
- **Los pagos observados no son recuperación.** La ingesta de pagos acepta cada movimiento tal como
  llega; interpretarlo, conciliarlo y atribuirlo son de `motor-pagos/v1` y `atribucion/v1`, aparte.
- **Una serie de analytics se corrige** en un análisis con un `as_of` posterior, cuando un reverso
  que llegó después anula un pago de ese periodo: cada análisis dice la verdad de su frontera.
