# La matriz de autorización

Generada por `scripts/exportar_matriz_autorizacion.py` desde `MATRIZ`
(`src/motor_cartera/api/autorizacion.py`): no se edita a mano. La API se niega a
arrancar si una operación no está en la matriz, o si la matriz nombra una que ya no
existe, y `tests/test_api_autorizacion.py` exige que este documento sea exactamente el
que el script escribe. Cómo se autentica cada credencial y de dónde salen sus permisos,
en [security.md](security.md).

## Los roles

| Rol | Permisos |
| --- | --- |
| `VIEWER` | `mc.read` |
| `AUDITOR` | `mc.read`, `mc.audit` |
| `OPERATOR` | `mc.read`, `mc.execute` |
| `ADMIN` | `mc.read`, `mc.audit`, `mc.execute`, `mc.configure` |

## Las clases

| Clase | Permiso | Operaciones | Qué es |
| --- | --- | ---: | --- |
| `PUBLIC` | ninguno | 3 | Las sondas de salud: sin credencial. |
| `READ` | `mc.read` | 123 | Leer lo publicado. |
| `AUDIT` | `mc.audit` | 8 | Leer la evidencia: rechazos, archivos fuente, observaciones, la cola, el linaje. |
| `EXECUTE` | `mc.execute` | 23 | Pedir trabajo: ingerir, ejecutar un motor, registrar un evento. |
| `CONFIGURE` | `mc.configure` | 4 | Publicar configuracion operacional. |

## Cada operación (161)

| Método | Ruta | Clase | Permiso |
| --- | --- | --- | --- |
| `GET` | `/analytics/catalogo` | `READ` | `mc.read` |
| `GET` | `/analytics/ejecuciones` | `READ` | `mc.read` |
| `POST` | `/analytics/ejecuciones` | `EXECUTE` | `mc.execute` |
| `GET` | `/analytics/ejecuciones/{analytics_run_id}` | `READ` | `mc.read` |
| `GET` | `/analytics/ejecuciones/{analytics_run_id}/agregados` | `READ` | `mc.read` |
| `GET` | `/analytics/ejecuciones/{analytics_run_id}/controles` | `READ` | `mc.read` |
| `GET` | `/analytics/ejecuciones/{analytics_run_id}/embudos` | `READ` | `mc.read` |
| `GET` | `/analytics/ejecuciones/{analytics_run_id}/resumen` | `READ` | `mc.read` |
| `GET` | `/app/v1/contextos/opciones` | `READ` | `mc.read` |
| `GET` | `/app/v1/contextos/validar` | `READ` | `mc.read` |
| `GET` | `/app/v1/cuentas` | `READ` | `mc.read` |
| `GET` | `/app/v1/cuentas/{cuenta_id}` | `READ` | `mc.read` |
| `GET` | `/app/v1/cuentas/{cuenta_id}/decision` | `READ` | `mc.read` |
| `GET` | `/app/v1/cuentas/{cuenta_id}/gestiones` | `READ` | `mc.read` |
| `GET` | `/app/v1/cuentas/{cuenta_id}/historia` | `READ` | `mc.read` |
| `GET` | `/app/v1/cuentas/{cuenta_id}/linea-de-tiempo` | `READ` | `mc.read` |
| `GET` | `/app/v1/cuentas/{cuenta_id}/pagos` | `READ` | `mc.read` |
| `GET` | `/app/v1/linaje` | `AUDIT` | `mc.audit` |
| `GET` | `/app/v1/predictivo/scorings/{scoring_run_id}/distribucion` | `READ` | `mc.read` |
| `GET` | `/app/v1/resumen` | `READ` | `mc.read` |
| `GET` | `/app/v1/ruteo/rutas/{ruta_id}/paradas` | `READ` | `mc.read` |
| `GET` | `/app/v1/sesion` | `READ` | `mc.read` |
| `GET` | `/atribuciones` | `READ` | `mc.read` |
| `POST` | `/atribuciones` | `EXECUTE` | `mc.execute` |
| `GET` | `/atribuciones/{atribucion_run_id}` | `READ` | `mc.read` |
| `GET` | `/atribuciones/{atribucion_run_id}/resultados` | `READ` | `mc.read` |
| `GET` | `/bases/versiones` | `READ` | `mc.read` |
| `POST` | `/bases/versiones` | `CONFIGURE` | `mc.configure` |
| `GET` | `/bases/versiones/{bases_version_id}` | `READ` | `mc.read` |
| `GET` | `/campo-v2/planes` | `READ` | `mc.read` |
| `POST` | `/campo-v2/planes` | `EXECUTE` | `mc.execute` |
| `GET` | `/campo-v2/planes/{campo_run_id}` | `READ` | `mc.read` |
| `GET` | `/campo-v2/planes/{campo_run_id}/asignaciones` | `READ` | `mc.read` |
| `GET` | `/campo-v2/planes/{campo_run_id}/cargas` | `READ` | `mc.read` |
| `GET` | `/campo-v2/planes/{campo_run_id}/unidades` | `READ` | `mc.read` |
| `GET` | `/campo/configuraciones` | `READ` | `mc.read` |
| `POST` | `/campo/configuraciones` | `CONFIGURE` | `mc.configure` |
| `GET` | `/campo/configuraciones/{configuracion_campo_version_id}` | `READ` | `mc.read` |
| `GET` | `/cartera/cortes` | `READ` | `mc.read` |
| `GET` | `/cartera/cortes/{corte_id}` | `READ` | `mc.read` |
| `GET` | `/cartera/cortes/{corte_id}/decisiones-v2` | `READ` | `mc.read` |
| `GET` | `/cartera/resumen` | `READ` | `mc.read` |
| `GET` | `/convenios/{convenio_id}` | `READ` | `mc.read` |
| `POST` | `/convenios/{convenio_id}/cancelaciones` | `EXECUTE` | `mc.execute` |
| `POST` | `/corridas` | `EXECUTE` | `mc.execute` |
| `GET` | `/corridas/{run_id}` | `READ` | `mc.read` |
| `GET` | `/corridas/{run_id}/decisiones` | `READ` | `mc.read` |
| `POST` | `/corridas/{run_id}/decisiones` | `EXECUTE` | `mc.execute` |
| `GET` | `/corridas/{run_id}/flujo` | `READ` | `mc.read` |
| `GET` | `/corridas/{run_id}/fuente` | `AUDIT` | `mc.audit` |
| `GET` | `/corridas/{run_id}/historia` | `READ` | `mc.read` |
| `GET` | `/corridas/{run_id}/rechazos` | `AUDIT` | `mc.audit` |
| `GET` | `/cuentas` | `READ` | `mc.read` |
| `GET` | `/cuentas/{cuenta_id}` | `READ` | `mc.read` |
| `GET` | `/cuentas/{cuenta_id}/atribuciones` | `READ` | `mc.read` |
| `GET` | `/cuentas/{cuenta_id}/campo-v2` | `READ` | `mc.read` |
| `GET` | `/cuentas/{cuenta_id}/convenios` | `READ` | `mc.read` |
| `GET` | `/cuentas/{cuenta_id}/decisiones-v2` | `READ` | `mc.read` |
| `GET` | `/cuentas/{cuenta_id}/eventos` | `READ` | `mc.read` |
| `GET` | `/cuentas/{cuenta_id}/geografia` | `READ` | `mc.read` |
| `GET` | `/cuentas/{cuenta_id}/gestiones` | `READ` | `mc.read` |
| `POST` | `/cuentas/{cuenta_id}/gestiones` | `EXECUTE` | `mc.execute` |
| `GET` | `/cuentas/{cuenta_id}/historia` | `READ` | `mc.read` |
| `GET` | `/cuentas/{cuenta_id}/lifecycle` | `READ` | `mc.read` |
| `GET` | `/cuentas/{cuenta_id}/movimientos` | `READ` | `mc.read` |
| `GET` | `/cuentas/{cuenta_id}/optimizacion` | `READ` | `mc.read` |
| `GET` | `/cuentas/{cuenta_id}/pagos-observados` | `READ` | `mc.read` |
| `GET` | `/cuentas/{cuenta_id}/predictivo` | `READ` | `mc.read` |
| `GET` | `/cuentas/{cuenta_id}/promesas` | `READ` | `mc.read` |
| `GET` | `/cuentas/{cuenta_id}/ruteo-v2` | `READ` | `mc.read` |
| `GET` | `/cuentas/{cuenta_id}/territorial-v2` | `READ` | `mc.read` |
| `POST` | `/decisiones-v2` | `EXECUTE` | `mc.execute` |
| `GET` | `/decisiones-v2/{decision_run_id}` | `READ` | `mc.read` |
| `GET` | `/decisiones-v2/{decision_run_id}/resultados` | `READ` | `mc.read` |
| `GET` | `/decisiones-v2/{decision_run_id}/resultados/{cuenta_id}` | `READ` | `mc.read` |
| `GET` | `/decisiones/{decision_run_id}` | `READ` | `mc.read` |
| `GET` | `/decisiones/{decision_run_id}/cuentas` | `READ` | `mc.read` |
| `GET` | `/decisiones/{decision_run_id}/territoriales` | `READ` | `mc.read` |
| `POST` | `/decisiones/{decision_run_id}/territoriales` | `EXECUTE` | `mc.execute` |
| `GET` | `/evaluaciones-promesas` | `READ` | `mc.read` |
| `POST` | `/evaluaciones-promesas` | `EXECUTE` | `mc.execute` |
| `GET` | `/evaluaciones-promesas/{evaluacion_run_id}` | `READ` | `mc.read` |
| `GET` | `/evaluaciones-promesas/{evaluacion_run_id}/promesas` | `READ` | `mc.read` |
| `GET` | `/flujos/{flujo_id}` | `READ` | `mc.read` |
| `POST` | `/flujos/{flujo_id}/reanudar` | `EXECUTE` | `mc.execute` |
| `GET` | `/flujos/{flujo_id}/trabajos` | `AUDIT` | `mc.audit` |
| `GET` | `/geografia/ejecuciones` | `READ` | `mc.read` |
| `POST` | `/geografia/ejecuciones` | `EXECUTE` | `mc.execute` |
| `GET` | `/geografia/ejecuciones/{geografia_run_id}` | `READ` | `mc.read` |
| `GET` | `/geografia/ejecuciones/{geografia_run_id}/resultados` | `READ` | `mc.read` |
| `GET` | `/geografia/versiones` | `READ` | `mc.read` |
| `GET` | `/geografia/versiones/{geografia_version_id}` | `READ` | `mc.read` |
| `GET` | `/gestiones/{gestion_id}` | `READ` | `mc.read` |
| `POST` | `/gestiones/{gestion_id}/anulaciones` | `EXECUTE` | `mc.execute` |
| `POST` | `/gestiones/{gestion_id}/convenios` | `EXECUTE` | `mc.execute` |
| `POST` | `/gestiones/{gestion_id}/promesas` | `EXECUTE` | `mc.execute` |
| `GET` | `/health/live` | `PUBLIC` | — |
| `GET` | `/health/ready` | `PUBLIC` | — |
| `GET` | `/historias/{historia_run_id}` | `READ` | `mc.read` |
| `GET` | `/motor-pagos` | `READ` | `mc.read` |
| `GET` | `/motor-pagos/{motor_pagos_run_id}` | `READ` | `mc.read` |
| `GET` | `/motor-pagos/{motor_pagos_run_id}/resultados` | `READ` | `mc.read` |
| `GET` | `/movimientos` | `READ` | `mc.read` |
| `GET` | `/movimientos/{movimiento_id}` | `READ` | `mc.read` |
| `GET` | `/movimientos/{movimiento_id}/atribuciones` | `READ` | `mc.read` |
| `GET` | `/movimientos/{movimiento_id}/observaciones` | `AUDIT` | `mc.audit` |
| `GET` | `/optimizacion/configuraciones` | `READ` | `mc.read` |
| `POST` | `/optimizacion/configuraciones` | `CONFIGURE` | `mc.configure` |
| `GET` | `/optimizacion/configuraciones/{configuracion_id}` | `READ` | `mc.read` |
| `POST` | `/optimizacion/configuraciones/{configuracion_id}/publicacion` | `CONFIGURE` | `mc.configure` |
| `GET` | `/optimizacion/ejecuciones` | `READ` | `mc.read` |
| `POST` | `/optimizacion/ejecuciones` | `EXECUTE` | `mc.execute` |
| `GET` | `/optimizacion/ejecuciones/{optimizacion_run_id}` | `READ` | `mc.read` |
| `GET` | `/optimizacion/ejecuciones/{optimizacion_run_id}/capacidad` | `READ` | `mc.read` |
| `GET` | `/optimizacion/ejecuciones/{optimizacion_run_id}/cuentas/{cuenta_id}` | `READ` | `mc.read` |
| `GET` | `/optimizacion/ejecuciones/{optimizacion_run_id}/diagnostico` | `READ` | `mc.read` |
| `GET` | `/optimizacion/ejecuciones/{optimizacion_run_id}/restricciones` | `READ` | `mc.read` |
| `GET` | `/optimizacion/ejecuciones/{optimizacion_run_id}/resultados` | `READ` | `mc.read` |
| `GET` | `/optimizacion/politica` | `READ` | `mc.read` |
| `POST` | `/pagos` | `EXECUTE` | `mc.execute` |
| `GET` | `/pagos/{pagos_run_id}` | `READ` | `mc.read` |
| `GET` | `/pagos/{pagos_run_id}/fuente` | `AUDIT` | `mc.audit` |
| `GET` | `/pagos/{pagos_run_id}/historia` | `READ` | `mc.read` |
| `GET` | `/pagos/{pagos_run_id}/rechazos` | `AUDIT` | `mc.audit` |
| `GET` | `/predictivo/entrenamientos` | `READ` | `mc.read` |
| `POST` | `/predictivo/entrenamientos` | `EXECUTE` | `mc.execute` |
| `GET` | `/predictivo/entrenamientos/{training_run_id}` | `READ` | `mc.read` |
| `GET` | `/predictivo/features` | `READ` | `mc.read` |
| `GET` | `/predictivo/modelos` | `READ` | `mc.read` |
| `GET` | `/predictivo/modelos/{modelo_version_id}` | `READ` | `mc.read` |
| `GET` | `/predictivo/scorings` | `READ` | `mc.read` |
| `POST` | `/predictivo/scorings` | `EXECUTE` | `mc.execute` |
| `GET` | `/predictivo/scorings/{scoring_run_id}` | `READ` | `mc.read` |
| `GET` | `/predictivo/scorings/{scoring_run_id}/cuentas/{cuenta_id}/explicacion` | `READ` | `mc.read` |
| `GET` | `/predictivo/scorings/{scoring_run_id}/drift` | `READ` | `mc.read` |
| `GET` | `/predictivo/scorings/{scoring_run_id}/predicciones` | `READ` | `mc.read` |
| `GET` | `/predictivo/targets` | `READ` | `mc.read` |
| `GET` | `/promesas/{promesa_id}` | `READ` | `mc.read` |
| `POST` | `/promesas/{promesa_id}/cancelaciones` | `EXECUTE` | `mc.execute` |
| `GET` | `/ruteo-v2/ejecuciones` | `READ` | `mc.read` |
| `POST` | `/ruteo-v2/ejecuciones` | `EXECUTE` | `mc.execute` |
| `GET` | `/ruteo-v2/ejecuciones/{ruteo_run_id}` | `READ` | `mc.read` |
| `GET` | `/ruteo-v2/ejecuciones/{ruteo_run_id}/paradas` | `READ` | `mc.read` |
| `GET` | `/ruteo-v2/ejecuciones/{ruteo_run_id}/rutas` | `READ` | `mc.read` |
| `GET` | `/ruteo-v2/redes` | `READ` | `mc.read` |
| `GET` | `/ruteo-v2/redes/{red_vial_version_id}` | `READ` | `mc.read` |
| `GET` | `/ruteo-v2/rutas/{ruta_id}` | `READ` | `mc.read` |
| `GET` | `/ruteo-v2/rutas/{ruta_id}/paradas` | `READ` | `mc.read` |
| `GET` | `/ruteos/{ruteo_run_id}` | `READ` | `mc.read` |
| `GET` | `/ruteos/{ruteo_run_id}/rutas` | `READ` | `mc.read` |
| `GET` | `/ruteos/{ruteo_run_id}/rutas/{clave_territorio}/paradas` | `READ` | `mc.read` |
| `GET` | `/salud` | `PUBLIC` | — |
| `GET` | `/territorial-v2` | `READ` | `mc.read` |
| `POST` | `/territorial-v2` | `EXECUTE` | `mc.execute` |
| `GET` | `/territorial-v2/{territorial_run_id}` | `READ` | `mc.read` |
| `GET` | `/territorial-v2/{territorial_run_id}/resultados` | `READ` | `mc.read` |
| `GET` | `/territoriales/{territorial_run_id}` | `READ` | `mc.read` |
| `GET` | `/territoriales/{territorial_run_id}/municipios` | `READ` | `mc.read` |
| `GET` | `/territoriales/{territorial_run_id}/ruteos` | `READ` | `mc.read` |
| `POST` | `/territoriales/{territorial_run_id}/ruteos` | `EXECUTE` | `mc.execute` |
| `GET` | `/trabajos/{trabajo_id}` | `AUDIT` | `mc.audit` |
