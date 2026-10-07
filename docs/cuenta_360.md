# La Cuenta 360

La Cuenta 360 responde, para una cuenta, **qué le pasó a través del tiempo**: cuándo se observó por
primera vez, en qué cortes estuvo, cuál es su estado más reciente, cómo evolucionaron su saldo y su
mora, si dejó de aparecer o reapareció, cuántos cortes faltó, qué pagos observados tiene, de qué
archivo y de qué fila salió cada dato, y cuál era su estado en una fecha.

La responde desde el modelo histórico ([historia.md](historia.md)): sin volver a leer ningún xlsx,
csv, zip ni Parquet, y con consultas de una sola cuenta que entran por un índice. La lógica vive en
el servicio `historia/cuenta360.py`; las rutas de `api/cuentas.py` y `api/historia.py` solo la
traducen a HTTP.

Todas las rutas exigen la cabecera `X-API-Key`, como el resto de la API, y responden los errores
con la misma forma (`codigo`, `mensaje`, `detalles`).

Cada respuesta se arma con **una sola foto de la base**: sus consultas corren en una transacción de
solo lectura `REPEATABLE READ`. Si un corte se publica mientras se responde, la respuesta es entera
la de antes o la de después, nunca una mezcla: una cuenta que está en el corte nuevo no aparece, en
esa respuesta, como una salida que no ocurrió.

## Las rutas

| Método y ruta | Qué devuelve | Respuestas |
|---|---|---|
| `GET /cuentas?cliente_unico=...` | El `cuenta_id` de la cuenta canónica de ese `CLIENTE_UNICO` en la cartera del sistema | 200, 401, 404, 422 |
| `GET /cuentas/{cuenta_id}` | Su resumen a través de sus cortes; con `?al=AAAA-MM-DD`, como se veía en esa fecha | 200, 401, 404, 422 |
| `GET /cuentas/{cuenta_id}/historia` | Sus snapshots, con continuidad, deltas y evidencia, paginados (`orden=desc` por omisión, o `asc`) | 200, 401, 404, 422 |
| `GET /cuentas/{cuenta_id}/eventos` | Sus eventos de presencia, calculados al consultar, paginados | 200, 401, 404, 422 |
| `GET /cuentas/{cuenta_id}/pagos-observados` | Sus movimientos de pagos/v1 tal como llegaron, del más reciente al más antiguo, paginados | 200, 401, 404, 422 |
| `GET /cartera/cortes` | Los cortes canónicos de la cartera, por fecha, y el último | 200, 401, 422 |
| `GET /cartera/cortes/{corte_id}` | Un corte con su evidencia: el Parquet, el original y sus fuentes equivalentes | 200, 401, 404, 422 |
| `GET /historias/{historia_run_id}` | Una ejecución histórica: cómo va o cómo terminó | 200, 401, 404, 422 |
| `GET /corridas/{run_id}/historia` | Las ejecuciones que materializaron el dataset de una corrida | 200, 401, 404, 409, 422 |
| `GET /pagos/{pagos_run_id}/historia` | Las de una ingesta de pagos, y cuántos de sus pagos tienen hoy una cuenta observada | 200, 401, 404, 409, 422 |

Las listas se paginan como en el resto de la API: `pagina` desde 1 y `por_pagina` hasta 500, con
`total`, `pagina`, `por_pagina` y `elementos` en la respuesta. `GET /cuentas/{cuenta_id}` es un
**resumen**: la historia, los eventos y los pagos son subrecursos, para que ninguna respuesta crezca
sin control con los años.

## Buscar una cuenta

```bash
curl -s -H "X-API-Key: clave-local-de-desarrollo" \
  "http://localhost:8000/cuentas?cliente_unico=CU0000004521"
```

```json
{
  "cuenta_id": "7b1d2c3e-4f5a-5b6c-8d7e-9f0a1b2c3d4e",
  "cliente_unico": "CU0000004521",
  "despacho_id": "DSP_001",
  "cartera_id": "CARTERA_PRINCIPAL"
}
```

Busca en el despacho y la cartera del sistema (`MC_DESPACHO_ID`, `MC_CARTERA_ID`). Si ningún corte
trae ese `CLIENTE_UNICO`, responde `404 CUENTA_NO_ENCONTRADA`; si ningún corte lo trae pero hay
pagos observados suyos, `404 SIN_CUENTA_OBSERVADA`: un pago no crea una cuenta. El `cuenta_id` es
determinista (un UUID versión 5 de despacho, cartera y `CLIENTE_UNICO`), así que no cambia si la
historia se reconstruye.

## El resumen

```bash
curl -s -H "X-API-Key: clave-local-de-desarrollo" \
  http://localhost:8000/cuentas/7b1d2c3e-4f5a-5b6c-8d7e-9f0a1b2c3d4e
```

```json
{
  "cuenta_id": "7b1d2c3e-4f5a-5b6c-8d7e-9f0a1b2c3d4e",
  "cliente_unico": "CU0000004521",
  "despacho_id": "DSP_001",
  "cartera_id": "CARTERA_PRINCIPAL",
  "al": null,
  "primera_observacion": "2026-09-02",
  "ultima_observacion": "2026-09-30",
  "ultimo_corte_cartera": "2026-09-30",
  "estado_presencia": "EN_CARTERA",
  "cortes_observados": 4,
  "cortes_ausentes_desde_primera_observacion": 1,
  "salidas_observadas": 1,
  "reingresos_observados": 1,
  "pagos_observados": 3,
  "snapshot_actual": { "fecha_corte": "2026-09-30", "saldo_total": "62450.00", "...": "..." },
  "ultimo_snapshot_observado": { "fecha_corte": "2026-09-30", "...": "..." }
}
```

- **`estado_presencia`** es `EN_CARTERA` si la cuenta está en el último corte de su cartera, y
  `NO_OBSERVADA_EN_ULTIMO_CORTE` si no. No es un estado crediticio: que no esté no dice si se
  liquidó, se canceló, se castigó o se vendió.
- **`snapshot_actual`** es el snapshot del último corte de la cartera, y es `null` si la cuenta no
  está en él. **`ultimo_snapshot_observado`** es el del último corte en que se observó, aunque ya no
  esté en el vigente. La diferencia importa: una cuenta que salió conserva su último snapshot, pero
  no tiene uno actual.
- **`primera_observacion`** no es la originación de la cuenta ni un alta: la cartera pudo tenerla
  desde antes del primer corte que se observó.
- **`cortes_ausentes_desde_primera_observacion`** cuenta los cortes de la cartera, desde su primera
  observación, en que no aparece; los de antes no son ausencias.
- **`pagos_observados`** es un conteo de observaciones, no una recuperación.

Con `?al=2026-09-16`, la misma cuenta se ve como se veía ese día: solo cuentan los cortes con fecha
hasta el 16, el último corte es el último hasta esa fecha, y los pagos, los recibidos hasta el final
de ese día. Antes de su primer corte, la cuenta no tiene observaciones.

## La historia

```bash
curl -s -H "X-API-Key: clave-local-de-desarrollo" \
  "http://localhost:8000/cuentas/7b1d2c3e-4f5a-5b6c-8d7e-9f0a1b2c3d4e/historia?por_pagina=2"
```

```json
{
  "total": 4, "pagina": 1, "por_pagina": 2, "orden": "desc",
  "cuenta_id": "7b1d2c3e-4f5a-5b6c-8d7e-9f0a1b2c3d4e", "cliente_unico": "CU0000004521",
  "elementos": [
    {
      "fecha_corte": "2026-09-30", "corte_id": "0b87ab2f-e6eb-54a6-b1b4-2826722801db",
      "saldo_total": "62450.00", "dias_atraso": 65, "...": "...",
      "continuo_desde_anterior": true, "cortes_ausentes_desde_anterior": 0,
      "delta_saldo_total": "-1500.00", "delta_dias_atraso": 7,
      "dataset_id": "5f1c2e3d-4b5a-4c6d-8e7f-9a0b1c2d3e4f", "source_row": 4523,
      "source_sheet": "CARTERA.csv"
    },
    {
      "fecha_corte": "2026-09-23", "...": "...",
      "continuo_desde_anterior": false, "cortes_ausentes_desde_anterior": 1,
      "delta_saldo_total": "-9800.00", "delta_dias_atraso": -30
    }
  ]
}
```

Un snapshot por cada corte en que se observó la cuenta, del más reciente al más antiguo
(`orden=asc` para el otro sentido), con todas sus variables históricas y su evidencia: el corte, el
dataset conformado y la fila y la hoja del archivo original. `continuo_desde_anterior` es `true`
solo si el corte del snapshot anterior de la cuenta es el corte anterior de la cartera; si la cuenta
faltó en medio, es `false`, `cortes_ausentes_desde_anterior` dice cuántos cortes faltó, y los deltas
son la diferencia entre las dos observaciones, no la evolución de un periodo continuo. En la primera
observación los cuatro son `null`.

Cada elemento se compara con el snapshot anterior de la cuenta aunque ese caiga en otra página: la
paginación no rompe la continuidad.

## Los eventos

```json
{
  "total": 3, "pagina": 1, "por_pagina": 50,
  "elementos": [
    {"tipo": "PRIMERA_OBSERVACION", "fecha_corte": "2026-09-02", "ultima_observacion": null, "cortes_ausente": 0, "corte_id": "..."},
    {"tipo": "SALIDA_OBSERVADA", "fecha_corte": "2026-09-16", "ultima_observacion": "2026-09-09", "cortes_ausente": 0, "corte_id": "..."},
    {"tipo": "REINGRESO_OBSERVADO", "fecha_corte": "2026-09-23", "ultima_observacion": "2026-09-09", "cortes_ausente": 1, "corte_id": "..."}
  ]
}
```

Se calculan al consultar, a partir de los snapshots de la cuenta y de los cortes de su cartera: no
se guardan. Un corte atrasado que llega después cambia la respuesta exactamente como si hubiera
llegado a tiempo.

## Los pagos observados

> Estos son movimientos observados de la fuente pagos/v1. No están deduplicados, conciliados,
> interpretados como reversos ni atribuidos. Ese procesamiento corresponde al Motor de Pagos.

El aviso va en la documentación de la ruta y en cada respuesta (`aviso`). Cada elemento trae los
campos de pagos/v1 con su nombre interno (`fecha_recepcion`, `recuperacion_por_gestion`,
`concepto_calculo`, `anio`, `semana`...), su `pago_observado_id`, la ingesta que lo aceptó
(`pagos_run_id`), su dataset conformado, su fila y su hoja. El orden es por `fecha_recepcion`, del
más reciente al más antiguo, y entre dos de la misma fecha, el del dataset más reciente y la fila
más alta: es total, así que la paginación es estable.

Dos filas idénticas son dos observaciones, y el mismo movimiento en dos archivos también. Se
incluyen los pagos que llegaron antes del primer corte que trajo a la cuenta: la relación es por
despacho, cartera y `CLIENTE_UNICO`, no una llave que se fije al llegar. **No hay un total de
dinero**: una suma de observaciones que pueden estar repetidas, o ser ajustes o reversos, no es una
recuperación, y no se presenta como si lo fuera.

## Los cortes y las ejecuciones

`GET /cartera/cortes` lista los cortes canónicos de la cartera del sistema con su `corte_id`, su
fecha, sus cuentas, su `firma_contenido`, su `version_modelo`, su dataset y su corrida, y trae
aparte **`ultimo_corte`**, el de fecha más reciente, esté o no en la página: el corte vigente del
modelo histórico se sabe sin inspeccionar corridas. `GET /cartera/cortes/{corte_id}` agrega su
evidencia: el Parquet de su dataset y el archivo original, con sus SHA-256, y cada ejecución que lo
publicó (`CORTE_PUBLICADO`) o que llegó con la misma cartera en otro archivo
(`FUENTE_EQUIVALENTE`).

`GET /historias/{historia_run_id}`, `GET /corridas/{run_id}/historia` y
`GET /pagos/{pagos_run_id}/historia` dicen cómo va o cómo terminó cada materialización, con su
resultado, sus conteos, su corte y su trabajo `HISTORIA` en la cola (`GET /trabajos/{trabajo_id}`).
Mientras la ingesta sigue en proceso responden `409 CORRIDA_EN_PROCESO` o `PAGOS_EN_PROCESO`; una
corrida de cartera/v1, o que no publicó, `404 SIN_DATASET_CONFORMADO`. La de pagos agrega
`relacion`: cuántos de sus pagos tienen hoy una cuenta canónica con su `CLIENTE_UNICO` y cuántos no
(`SIN_CUENTA_OBSERVADA`), calculado al consultar.

## Cómo explica un saldo

"¿Por qué decimos que esta cuenta tenía un saldo de 62,450.00 el 30 de septiembre?"

1. `GET /cuentas/{cuenta_id}/historia`: el snapshot del 30 trae `saldo_total`, su `corte_id`, su
   `dataset_id`, su `source_row` (4523) y su `source_sheet` (`CARTERA.csv`).
2. `GET /cartera/cortes/{corte_id}`: el corte salió de ese dataset, cuyo Parquet tiene un SHA-256 y
   cuyo archivo original tiene otro, y dice si llegó en más de un archivo.
3. `GET /corridas/{run_id}/fuente`: la corrida que publicó ese dataset, con el original, su nombre y
   su formato.

En el Parquet, la fila con `_source_row = 4523` trae las 93 columnas de esa cuenta ese día; en el
archivo original, la fila 4523 de la hoja `CARTERA.csv` es la que llegó. Ningún paso de esa cadena
depende de un dato que se haya vuelto a interpretar.
