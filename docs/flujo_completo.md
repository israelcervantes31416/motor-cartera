# El flujo completo, paso a paso

Un recorrido por la API y la línea de comandos sobre el compose de desarrollo (`docker compose up
--build`), con la clave de desarrollo `clave-local-de-desarrollo`. Primero el flujo operacional que
un solo `POST /corridas` encadena (ingesta, `decision/v1`, `territorial/v1` y `ruteo/v1`); después,
los motores que se piden con sus entradas declaradas: la historia y la Cuenta 360, el motor de
pagos, el lifecycle y la atribución, `decision/v2`, la geografía y `territorial/v2`, `campo/v2`,
`ruteo/v2`, analytics, predictivo y optimización. Al final, la prueba de humo, que recorre todo eso
con la verificación de cada código HTTP.

Las reglas de cada motor están en su documento ([índice](README.md)); los códigos de la API, en
[api.md](api.md); la cola que ejecuta cada etapa, en [orquestacion.md](orquestacion.md).

## El flujo operacional

**1. Generar una cartera sintética.** El archivo queda en `./datos`, que el contenedor
comparte con tu máquina:

```bash
docker compose exec api motor-cartera generar --destino datos/cartera.xlsx
```

Por omisión son 10,000 cuentas con 2 % de filas inválidas a propósito (`--n`,
`--tasa-invalidas`, `--semilla`, `--fecha-corte`). El formato sale de la extensión.

**2. Subirla por la API.**

```bash
curl -i -H "X-API-Key: clave-local-de-desarrollo" -F "archivo=@datos/cartera.xlsx" http://localhost:8000/corridas
```

Responde `201` de inmediato, con la corrida `EN_PROCESO` y su dirección en `Location`. La API no
juzga el archivo: lo copia por bloques al almacén de artefactos y registra, en una sola
transacción, su artefacto, la corrida, su flujo automático y el trabajo de la ingesta; el worker
hace lo demás. Sin el campo `contrato`, el archivo es de `cartera/v1`, como siempre; una cartera
oficial se sube con `contrato=cartera/v2` y su `fecha_corte` (ver
[Fuentes oficiales](fuentes.md)).

**3. Seguir su flujo** hasta que deje de estar `EN_PROCESO`:

```bash
curl -H "X-API-Key: clave-local-de-desarrollo" http://localhost:8000/corridas/<run_id>/flujo
```

```json
{
  "flujo_id": "…",
  "run_id": "…",
  "estado": "COMPLETADO",
  "etapa": "COMPLETADA",
  "decision_run_id": "…",
  "territorial_run_id": "…",
  "ruteo_run_id": "…",
  "duracion_segundos": 4.68,
  "detalle": "La ingesta, la decision, la organizacion territorial y el ruteo terminaron EXITOSA."
}
```

Mientras avanza, `etapa` dice en cuál va (`INGESTA`, `DECISION`, `TERRITORIAL` o `RUTEO`) y
`detalle`, qué espera; el identificador de cada ejecución aparece en cuanto el flujo llega a su
etapa. `COMPLETADO` quiere decir que el ruteo terminó `EXITOSA`. `DETENIDO` quiere decir que una
etapa no pudo continuar: `etapa` dice cuál, y `detalle`, por qué y cómo reintentar (ver
[Si el flujo se detiene](#si-el-flujo-se-detiene)).

**4. Ver los trabajos que lo ejecutaron**, uno por etapa, en el orden en que entraron a la cola:

```bash
curl -H "X-API-Key: clave-local-de-desarrollo" http://localhost:8000/flujos/<flujo_id>/trabajos
```

```json
{
  "flujo_id": "…", "run_id": "…", "total": 4, "pagina": 1, "por_pagina": 50,
  "elementos": [
    {
      "trabajo_id": "…",
      "tipo": "INGESTA",
      "estado": "COMPLETADO",
      "objetivo_run_id": "…",
      "intentos": 1,
      "max_intentos": 5,
      "lease_hasta": null,
      "ultimo_error": null,
      "…": "…"
    }
  ]
}
```

El estado de un trabajo es el de su entrega, no el de su motor: `COMPLETADO` quiere decir que su
recurso terminó, `EXITOSA` o no. `objetivo_run_id` es el identificador público de ese recurso, y
`GET /trabajos/{trabajo_id}` consulta un trabajo solo.

**5. Consultar la corrida**: estado, conteos y tiempos.

```bash
curl -H "X-API-Key: clave-local-de-desarrollo" http://localhost:8000/corridas/<run_id>
```

```json
{
  "run_id": "…",
  "estado": "EXITOSA",
  "firma_contenido": "…",
  "filas_leidas": 10000,
  "filas_validas": 9800,
  "filas_rechazadas": 200,
  "version_contrato": "cartera/v1",
  "duracion_segundos": 2.82,
  "detalle": "Se publicaron 9,800 cuentas; 200 registros (2.0%) se rechazaron, dentro de la tolerancia de 5.0%. Origen: hoja 'cartera' de 'cartera.xlsx'. …"
}
```

**6. Ver los rechazos, cada uno con su fila y su motivo:**

```bash
curl -H "X-API-Key: clave-local-de-desarrollo" "http://localhost:8000/corridas/<run_id>/rechazos?por_pagina=2"
```

```json
{
  "total": 200, "pagina": 1, "por_pagina": 2,
  "elementos": [
    {
      "fila": 67,
      "valores": {"cliente_unico": "CU…", "saldo_total": "-258422.11", "…": "…"},
      "motivos": [{"campo": "saldo_total", "regla": "greater_than_or_equal_to(0)"}]
    }
  ]
}
```

**7. Consultar el resumen segmentado** de la cartera vigente:

```bash
curl -H "X-API-Key: clave-local-de-desarrollo" "http://localhost:8000/cartera/resumen?por=canal&por=tramo_atraso"
```

Devuelve cuentas, saldo y saldo promedio por segmento, junto con el `run_id` del que sale
cada número.

**8. Consultar la decisión** que pidió el flujo, con el `decision_run_id` del paso 3: versión de
las reglas, estado, tiempos y conteos.

```bash
curl -H "X-API-Key: clave-local-de-desarrollo" http://localhost:8000/decisiones/<decision_run_id>
```

```json
{
  "decision_run_id": "…",
  "run_id": "…",
  "version_reglas": "decision/v1",
  "estado": "EXITOSA",
  "cuentas_evaluadas": 9800,
  "cuentas_decididas": 9800,
  "detalle": "Se decidieron 9,800 cuentas con decision/v1."
}
```

**9. Listar el historial de la corrida:** todas sus ejecuciones, en cualquier estado y versión
de las reglas, de la más reciente a la más antigua.

```bash
curl -H "X-API-Key: clave-local-de-desarrollo" http://localhost:8000/corridas/<run_id>/decisiones
```

**10. Ver la decisión de cada cuenta**, con sus motivos:

```bash
curl -H "X-API-Key: clave-local-de-desarrollo" "http://localhost:8000/decisiones/<decision_run_id>/cuentas?por_pagina=2"
```

```json
{
  "decision_run_id": "…", "run_id": "…", "version_reglas": "decision/v1", "estado": "EXITOSA",
  "total": 9800, "pagina": 1, "por_pagina": 2,
  "elementos": [
    {
      "cliente_unico": "CU…",
      "segmento": "MORA_MEDIA",
      "prioridad": "MUY_ALTA",
      "canal_recomendado": "CAMPO",
      "motivos": [
        {"codigo": "MORA_31_90", "campo": "dias_atraso", "valor": "65"},
        {"codigo": "SALDO_ALTO", "campo": "saldo_total", "valor": "62000.00"},
        {"codigo": "PRIORIDAD_MUY_ALTA", "campo": "prioridad", "valor": "MUY_ALTA"},
        {"codigo": "CANAL_CAMPO", "campo": "canal_recomendado", "valor": "CAMPO"}
      ]
    }
  ]
}
```

**11. Consultar la ejecución territorial** que pidió el flujo, con el `territorial_run_id` del
paso 3, el `decision_run_id` de las decisiones que organizó y el `run_id` de su corrida:

```bash
curl -H "X-API-Key: clave-local-de-desarrollo" http://localhost:8000/territoriales/<territorial_run_id>
```

```json
{
  "territorial_run_id": "…",
  "decision_run_id": "…",
  "run_id": "…",
  "version_reglas": "territorial/v1",
  "estado": "EXITOSA",
  "territorios_evaluados": 541,
  "territorios_publicados": 541,
  "detalle": "Se organizaron 9,800 decisiones en 541 municipios con territorial/v1."
}
```

**12. Listar el historial territorial de esas decisiones:** todas sus ejecuciones, en cualquier
estado y versión de las reglas, de la más reciente a la más antigua.

```bash
curl -H "X-API-Key: clave-local-de-desarrollo" http://localhost:8000/decisiones/<decision_run_id>/territoriales
```

**13. Ver cada municipio**, en el orden en que conviene atenderlos, con su carga y su motivo:

```bash
curl -H "X-API-Key: clave-local-de-desarrollo" "http://localhost:8000/territoriales/<territorial_run_id>/municipios?por_pagina=1"
```

```json
{
  "territorial_run_id": "…", "decision_run_id": "…", "run_id": "…",
  "version_reglas": "territorial/v1", "estado": "EXITOSA",
  "total": 541, "pagina": 1, "por_pagina": 1,
  "elementos": [
    {
      "clave_territorio": "21074",
      "cve_entidad": "21",
      "cve_municipio": "074",
      "cuentas_total": 1414,
      "saldo_total": "48455164.68",
      "cuentas_campo": 425,
      "saldo_campo": "22464492.42",
      "carga": "ALTA",
      "posicion_campo": 1,
      "motivos": [{"codigo": "CARGA_CAMPO_20_MAS", "campo": "cuentas_campo", "valor": "425"}]
    }
  ]
}
```

**14. Consultar la ejecución de ruteo** que pidió el flujo, con el `ruteo_run_id` del paso 3 y
los identificadores públicos de toda su cadena:

```bash
curl -H "X-API-Key: clave-local-de-desarrollo" http://localhost:8000/ruteos/<ruteo_run_id>
```

```json
{
  "ruteo_run_id": "…",
  "territorial_run_id": "…",
  "decision_run_id": "…",
  "run_id": "…",
  "version_reglas": "ruteo/v1",
  "estado": "EXITOSA",
  "rutas_evaluadas": 403,
  "rutas_publicadas": 403,
  "paradas_evaluadas": 2968,
  "paradas_publicadas": 2968,
  "detalle": "Se rutearon 2,968 cuentas de campo en 403 municipios con ruteo/v1."
}
```

**15. Listar el historial de ruteo de esa ejecución territorial**, en cualquier estado y versión,
de la más reciente a la más antigua:

```bash
curl -H "X-API-Key: clave-local-de-desarrollo" http://localhost:8000/territoriales/<territorial_run_id>/ruteos
```

**16. Ver la ruta de cada municipio**, en el orden de prioridad territorial:

```bash
curl -H "X-API-Key: clave-local-de-desarrollo" "http://localhost:8000/ruteos/<ruteo_run_id>/rutas?por_pagina=1"
```

```json
{
  "ruteo_run_id": "…", "territorial_run_id": "…", "decision_run_id": "…", "run_id": "…",
  "version_reglas": "ruteo/v1", "estado": "EXITOSA",
  "total": 403, "pagina": 1, "por_pagina": 1,
  "elementos": [
    {
      "clave_territorio": "21074",
      "posicion_territorial": 1,
      "cuentas_campo": 425,
      "paradas": 425,
      "distancia_inicial_m": 241140,
      "distancia_total_m": 226184,
      "distancia_regreso_deposito_m": 1924,
      "mejora_2opt_m": 14956
    }
  ]
}
```

**17. Ver las paradas de un municipio**, en el orden de visita:

```bash
curl -H "X-API-Key: clave-local-de-desarrollo" "http://localhost:8000/ruteos/<ruteo_run_id>/rutas/21074/paradas?por_pagina=2"
```

```json
{
  "ruteo_run_id": "…", "run_id": "…", "version_reglas": "ruteo/v1", "estado": "EXITOSA",
  "clave_territorio": "21074",
  "total": 425, "pagina": 1, "por_pagina": 2,
  "elementos": [
    {"secuencia": 1, "cliente_unico": "CU8843537813", "x_m": -71, "y_m": 557, "distancia_desde_anterior_m": 628},
    {"secuencia": 2, "cliente_unico": "CU6487942474", "x_m": -268, "y_m": 579, "distancia_desde_anterior_m": 219}
  ]
}
```

Los metros son sintéticos: `x_m` y `y_m` son un punto del plano local de 21074, no una longitud y
una latitud.

## Si el flujo se detiene

Una etapa que no termina `EXITOSA` detiene el flujo en esa etapa, y su `detalle` lo dice:

```json
{"estado": "DETENIDO", "etapa": "DECISION", "detalle": "La decision termino FALLIDA. Para reintentarla, reanuda el flujo.", "…": "…"}
```

Si se detuvo en la decisión, la organización territorial o el ruteo, se reintenta con otra
ejecución:

```bash
curl -X POST -H "X-API-Key: clave-local-de-desarrollo" http://localhost:8000/flujos/<flujo_id>/reanudar
```

Responde `200` con el flujo otra vez `EN_PROCESO`, apuntando a la ejecución nueva, y el worker
sigue desde ahí hasta el ruteo. La que falló queda en el historial de su etapa. Una corrida
`RECHAZADA` o `FALLIDA` no se reanuda: una corrida terminada es evidencia, y se vuelve a subir el
archivo, que es otra corrida con otro flujo.

## Etapas a mano

Una corrida publicada con el CLI, o antes de v0.5.0, no tiene flujo:
`GET /corridas/{run_id}/flujo` responde `404 FLUJO_NO_ENCONTRADO`. Sus etapas se piden una por una:

```bash
curl -i -X POST -H "X-API-Key: clave-local-de-desarrollo" http://localhost:8000/corridas/<run_id>/decisiones
curl -i -X POST -H "X-API-Key: clave-local-de-desarrollo" http://localhost:8000/decisiones/<decision_run_id>/territoriales
curl -i -X POST -H "X-API-Key: clave-local-de-desarrollo" http://localhost:8000/territoriales/<territorial_run_id>/ruteos
```

Cada una responde `201` con la ejecución `EN_PROCESO` y su `Location`; la ejecuta el worker, y se
consulta hasta que termine antes de pedir la siguiente. Sobre una fuente que es de un flujo
responden `409`: `FLUJO_EN_PROCESO` si el flujo la va a correr, o `FLUJO_DETENIDO` si se detuvo
ahí y se reanuda.

## Las fuentes oficiales

Desde v0.6.0 hay tres contratos de entrada, y el de cada archivo se declara, nunca se adivina:

| Contrato | Qué es | Columnas | Dónde entra |
|---|---|---|---|
| `cartera/v1` | La cartera mínima de siempre, congelada | 8, con alias | `POST /corridas` (por omisión) |
| `cartera/v2` | La hoja CARTERA de la cartera oficial | [93 exactas](diccionario_cartera.md) | `POST /corridas` con `contrato=cartera/v2` y `fecha_corte` |
| `pagos/v1` | Los movimientos económicos de un periodo | [23 exactas](diccionario_pagos.md) | `POST /pagos` |

```bash
# Una cartera oficial (con CARRIER) y los pagos de la semana que termina en su corte
docker compose exec api motor-cartera generar-oficial --destino datos --fecha-corte 2026-09-30
curl -i -H "X-API-Key: clave-local-de-desarrollo" -F "archivo=@datos/cartera_oficial_2026-09-30.xlsx" \
  -F contrato=cartera/v2 -F fecha_corte=2026-09-30 http://localhost:8000/corridas
curl -i -H "X-API-Key: clave-local-de-desarrollo" \
  -F "archivo=@datos/pagos_oficial_2026-09-24_2026-09-30.xlsx" http://localhost:8000/pagos
```

Lo que exige cada contrato (estructura exacta, la fecha de corte como metadata, el original como
evidencia, el conformado, la proyección y CARRIER) está en [fuentes.md](fuentes.md).

## La historia y la Cuenta 360

```bash
K="X-API-Key: clave-local-de-desarrollo"
curl -s -H "$K" "http://localhost:8000/cuentas?cliente_unico=CU0000004521"     # su cuenta_id
curl -s -H "$K" http://localhost:8000/cuentas/<cuenta_id>                      # Cuenta 360
curl -s -H "$K" "http://localhost:8000/cuentas/<cuenta_id>?al=2026-09-16"      # como se veía ese día
curl -s -H "$K" http://localhost:8000/cuentas/<cuenta_id>/historia             # snapshots y deltas
curl -s -H "$K" http://localhost:8000/cuentas/<cuenta_id>/eventos              # presencia
curl -s -H "$K" http://localhost:8000/cuentas/<cuenta_id>/pagos-observados     # pagos tal como llegaron
curl -s -H "$K" http://localhost:8000/cuentas/<cuenta_id>/movimientos          # lo que interpreta el motor
curl -s -H "$K" http://localhost:8000/cartera/cortes                           # cortes y el último
curl -s -H "$K" http://localhost:8000/corridas/<run_id>/historia               # su materialización
```

Lo publicado antes de v0.7.0 no tiene historia: la migración `0008` solo crea las tablas, y
`motor-cartera backfill-historia` encola la de cada dataset que no la tiene (con `--dry-run` dice
cuánto falta sin encolar nada). Detalle en [historia.md](historia.md) y
[cuenta_360.md](cuenta_360.md).

## El motor de pagos

```bash
K="X-API-Key: clave-local-de-desarrollo"
curl -s -H "$K" http://localhost:8000/motor-pagos                                   # interpretaciones, y la vigente
curl -s -H "$K" http://localhost:8000/motor-pagos/<motor_pagos_run_id>              # calidad y recuperación
curl -s -H "$K" "http://localhost:8000/motor-pagos/<id>/resultados?clasificacion=COINCIDENCIA_AMBIGUA"
curl -s -H "$K" "http://localhost:8000/movimientos?cliente_unico=CU0000004521"
curl -s -H "$K" http://localhost:8000/movimientos/<movimiento_id>/observaciones      # por qué vale una vez
curl -s -H "$K" http://localhost:8000/cuentas/<cuenta_id>/movimientos                # con su contexto temporal
```

Los pagos observados antes de v0.8.0 se interpretan con `motor-cartera backfill-motor-pagos`, por
ventanas (`--dry-run` dice cuántos pagos observados no tienen interpretación vigente;
`--reintentar-fallidas` y `--reconciliar`, para las que fallaron y para los pagos sin cuenta cuyo
cliente ya la tiene). Las reglas, las huellas, los motivos, la orquestación, los índices y el
benchmark están en [docs/motor_pagos.md](motor_pagos.md); la Cuenta 360, en
[docs/cuenta_360.md](cuenta_360.md); el porqué, en las decisiones 85 a 98.

## El lifecycle de cobranza y la atribución

```bash
K="X-API-Key: clave-local-de-desarrollo"
curl -s -X POST -H "$K" -H "Idempotency-Key: gestion-0001" -H "Content-Type: application/json" \
  -d '{"ocurrido_en":"2026-09-20T10:15:00-06:00","canal":"TELEFONICA","medio":"LLAMADA","nivel_contacto":"CONTACTO_TITULAR","resultado":"PROMESA"}' \
  http://localhost:8000/cuentas/<cuenta_id>/gestiones
curl -s -X POST -H "$K" -H "Idempotency-Key: promesa-0001" -H "Content-Type: application/json" \
  -d '{"monto_prometido":"1000.00","fecha_limite":"2026-09-25"}' \
  http://localhost:8000/gestiones/<gestion_id>/promesas
curl -s -H "$K" "http://localhost:8000/cuentas/<cuenta_id>/lifecycle?orden=asc"   # las tres verdades
curl -s -X POST -H "$K" -H "Content-Type: application/json" -d '{"as_of":"2026-09-30"}' \
  http://localhost:8000/evaluaciones-promesas
curl -s -X POST -H "$K" -H "Content-Type: application/json" -d '{"periodo":"2026-09"}' \
  http://localhost:8000/atribuciones
curl -s -H "$K" "http://localhost:8000/atribuciones/<atribucion_run_id>/resultados?clasificacion=AMBIGUA"
```

Lo masivo no pasa por las escrituras individuales: la evaluación y la atribución son trabajos
durables (`EVALUACION_PROMESAS`, `ATRIBUCION`) que la cola toma después del motor de pagos, uno por
cartera y fecha o por ventana, nunca uno por promesa o por pago; `backfill-lifecycle --as-of` y
`backfill-atribucion` encuentran lo pendiente. `motor-cartera cargar-lifecycle` importa eventos
sintéticos de un JSONL por conjuntos (COPY y tablas temporales), todo o nada e idempotente, y
`generar-escenario --lifecycle` los genera, deterministas por semilla, sin cambiar un byte de los
cortes ni de los pagos. Todo está en [docs/lifecycle.md](lifecycle.md) y
[docs/atribucion.md](atribucion.md); el porqué, en las decisiones 99 a 112.

## Decision Engine v2

```bash
K="X-API-Key: clave-local-de-desarrollo"
curl -s -X POST -H "$K" -H "Content-Type: application/json" \
  -d '{"corte_id":"<corte_id>","modo":"BACKTEST","known_at":"2026-10-01T08:00:00-06:00"}' \
  http://localhost:8000/decisiones-v2
curl -s -H "$K" "http://localhost:8000/decisiones-v2/<decision_run_id>/resultados?prioridad_final=MUY_ALTA"
curl -s -H "$K" http://localhost:8000/decisiones-v2/<decision_run_id>/resultados/<cuenta_id>
docker compose exec worker motor-cartera decidir-v2 --corte 2026-09-30 --backtest \
  --known-at 2026-10-01T08:00:00-06:00
docker compose exec worker motor-cartera comparar-decisiones --corte 2026-09-30
```

Detalle en [decision_v2.md](decision_v2.md).

## La geografía y territorial/v2

```bash
K="X-API-Key: clave-local-de-desarrollo"
docker compose exec api motor-cartera importar-geografia datos/referencias/inegi_mg_2025/mg_2025_integrado.zip \
  --referencia inegi-mg-2025-municipal
curl -s -H "$K" http://localhost:8000/geografia/versiones
curl -s -X POST -H "$K" -H "Content-Type: application/json" \
  -d '{"corte_id":"<corte_id>","geografia_version_id":"<geografia_version_id>"}' \
  http://localhost:8000/geografia/ejecuciones
curl -s -H "$K" "http://localhost:8000/geografia/ejecuciones/<geografia_run_id>/resultados?estado_geografia=DISCREPANCIA_MUNICIPIO"
curl -s -X POST -H "$K" -H "Content-Type: application/json" -d @bases.json http://localhost:8000/bases/versiones
curl -s -X POST -H "$K" -H "Content-Type: application/json" \
  -d '{"corte_id":"<corte_id>","geografia_run_id":"<geografia_run_id>","bases_version_id":"<bases_version_id>"}' \
  http://localhost:8000/territorial-v2
curl -s -H "$K" "http://localhost:8000/territorial-v2/<territorial_run_id>/resultados?base=BASE_CENTRO"
```

Detalle en [geografia.md](geografia.md), [territorial_v2.md](territorial_v2.md) y
[bases_operativas.md](bases_operativas.md).

## Campo v2

```bash
K="X-API-Key: clave-local-de-desarrollo"
docker compose exec api motor-cartera generar-campo-sintetico --bases <bases_version_id> \
  --destino datos/campo.json --gestores-por-base 20 --semilla 7
docker compose exec api motor-cartera cargar-campo datos/campo.json
curl -s -X POST -H "$K" -H "Content-Type: application/json" \
  -d '{"corte_id":"<corte_id>","decision_run_id":"<decision_run_id>","territorial_run_id":"<territorial_run_id>","configuracion_campo_version_id":"<configuracion_campo_version_id>","fecha_operacion":"2026-10-01"}' \
  http://localhost:8000/campo-v2/planes
curl -s -H "$K" "http://localhost:8000/campo-v2/planes/<campo_run_id>/asignaciones?estado=SIN_CAPACIDAD"
curl -s -H "$K" "http://localhost:8000/campo-v2/planes/<campo_run_id>/cargas"
docker compose exec api motor-cartera resumen-campo-v2 --plan <campo_run_id>
```

Detalle en [campo_v2.md](campo_v2.md), [configuracion_campo.md](configuracion_campo.md) y
[particion_campo.md](particion_campo.md).

## Ruteo vial v2

```bash
K="X-API-Key: clave-local-de-desarrollo"
docker compose exec api motor-cartera preparar-red-vial datos/red_vial/fuente/puebla.osm.pbf \
  --region "Estado de Puebla" --fecha-snapshot 2026-10-13 --docker
sh datos/red_vial/<huella>/preparar.sh
docker compose exec api motor-cartera registrar-red-vial datos/red_vial/<huella>
MC_RED_VIAL_HUELLA=<huella> docker compose --profile ruteo up -d --wait osrm
curl -s -X POST -H "$K" -H "Content-Type: application/json" \
  -d '{"campo_run_id":"<campo_run_id>","red_vial_version_id":"<red_vial_version_id>"}' \
  http://localhost:8000/ruteo-v2/ejecuciones
curl -s -H "$K" "http://localhost:8000/ruteo-v2/ejecuciones/<ruteo_run_id>/rutas?estado=PARCIAL"
curl -s -H "$K" "http://localhost:8000/ruteo-v2/ejecuciones/<ruteo_run_id>/paradas?estado=SIN_TIEMPO"
docker compose exec worker motor-cartera resumen-ruteo-v2 --ejecucion <ruteo_run_id>
```

Detalle en [ruteo_v2.md](ruteo_v2.md), [red_vial.md](red_vial.md) y
[politica_ruteo.md](politica_ruteo.md).

## Collection Analytics

```bash
K="X-API-Key: clave-local-de-desarrollo"
docker compose exec worker motor-cartera construir-manifiesto-analytics --corte 2026-09-30 \
  --as-of 2026-10-01 --modo OPERATIVO --zona America/Mexico_City --granularidad DIA \
  --destino datos/manifiesto_analytics.json
docker compose exec worker motor-cartera analizar-cobranza --manifiesto datos/manifiesto_analytics.json
curl -s -H "$K" http://localhost:8000/analytics/catalogo
curl -s -H "$K" "http://localhost:8000/analytics/ejecuciones/<analytics_run_id>/agregados?metrica=recuperacion.neta&vista=PERIODO&dimension_set=TOTAL"
curl -s -H "$K" "http://localhost:8000/analytics/ejecuciones/<analytics_run_id>/embudos"
docker compose exec worker motor-cartera exportar-analytics --ejecucion <analytics_run_id> --formato parquet
```

Detalle en [analytics.md](analytics.md), [metricas_analytics.md](metricas_analytics.md) y
[cohortes_analytics.md](cohortes_analytics.md).

## Predictive Intelligence

```bash
K="X-API-Key: clave-local-de-desarrollo"
docker compose exec worker motor-cartera generar-escenario-predictivo datos/predictivo --cuentas 600 \
  --cortes 20 --primer-corte 2026-03-04
docker compose exec -e MC_CARTERA_ID=PREDICTIVO worker motor-cartera cargar-escenario-predictivo \
  datos/predictivo
docker compose exec worker motor-cartera construir-manifiesto-predictivo --escenario datos/predictivo \
  --validation 1 --test 1 --destino datos/manifiesto_predictivo.json
docker compose exec worker motor-cartera entrenar-predictivo --manifiesto datos/manifiesto_predictivo.json
docker compose exec -e MC_CARTERA_ID=PREDICTIVO worker motor-cartera score-predictivo \
  --modelo <modelo_version_id> --corte 2026-07-15
curl -s -H "$K" "http://localhost:8000/predictivo/scorings/<scoring_run_id>/predicciones?orden=RIESGO_SIN_RECUPERACION_30D&sentido=DESC"
curl -s -H "$K" "http://localhost:8000/predictivo/scorings/<scoring_run_id>/cuentas/<cuenta_id>/explicacion"
curl -s -H "$K" "http://localhost:8000/predictivo/scorings/<scoring_run_id>/drift"
```

Detalle en [predictivo.md](predictivo.md), [features_predictivas.md](features_predictivas.md),
[modelos_predictivos.md](modelos_predictivos.md) y
[backtesting_predictivo.md](backtesting_predictivo.md).

## Optimización Matemática

```bash
K="X-API-Key: clave-local-de-desarrollo"
docker compose exec worker motor-cartera crear-config-optimizacion datos/configuracion.json --publicar
docker compose exec worker motor-cartera optimizar-cartera --scoring <scoring_run_id> \
  --decision <decision_run_id> --configuracion <configuracion_id> --fecha-operacion 2026-07-09
docker compose exec worker motor-cartera resumen-optimizacion --ejecucion <optimizacion_run_id>
docker compose exec worker motor-cartera exportar-optimizacion --ejecucion <optimizacion_run_id> \
  --formato parquet
curl -s -H "$K" "http://localhost:8000/optimizacion/ejecuciones/<optimizacion_run_id>/resultados?estado=ASIGNADA"
curl -s -H "$K" "http://localhost:8000/optimizacion/ejecuciones/<optimizacion_run_id>/restricciones?binding=true"
curl -s -H "$K" "http://localhost:8000/optimizacion/ejecuciones/<optimizacion_run_id>/cuentas/<cuenta_id>"
```

Detalle en [optimizacion.md](optimizacion.md), [modelo_optimizacion.md](modelo_optimizacion.md) y
[configuracion_optimizacion.md](configuracion_optimizacion.md).

## La app

```bash
docker compose up --build                       # la app en http://localhost:8080
cd web && npm ci --ignore-scripts && npm run dev # o en desarrollo, contra la API en :8000
```

Detalle en [app.md](app.md) y [frontend.md](frontend.md).

## La prueba de humo

Todo el flujo, con la verificación de cada código HTTP, está en un solo script que corre igual en
tu máquina que en el CI. Sube una cartera y no pide nada más: sigue su flujo hasta `COMPLETADO`,
revisa sus cuatro trabajos y lo que publicó cada etapa, y al final pide a mano la decisión, la
organización territorial y el ruteo ya publicados y exige los tres `409`. Necesita la API y el
worker corriendo, y un archivo que no se haya subido antes, porque el mismo archivo no se publica
dos veces:

```bash
docker compose exec api motor-cartera generar --destino datos/humo.xlsx --semilla 7
python scripts/prueba_de_humo.py datos/humo.xlsx
```

Solo usa la biblioteca estándar de Python; también corre dentro del contenedor con
`docker compose exec api python scripts/prueba_de_humo.py datos/humo.xlsx`. Con
`--oficial <archivo> --corte <fecha>` lleva además una cartera oficial por el flujo hasta el ruteo y
verifica su evidencia, y con `--pagos <archivo>`, una ingesta de pagos:

```bash
docker compose exec api motor-cartera generar-oficial --destino datos --formato zip --fecha-corte 2026-09-30
python scripts/prueba_de_humo.py datos/humo.xlsx --oficial datos/cartera_oficial_2026-09-30.zip \
  --corte 2026-09-30 --pagos datos/pagos_oficial_2026-09-24_2026-09-30.zip
```

Con `--escenario <directorio>` prueba el modelo histórico de punta a punta: sube los cortes de un
escenario de `generar-escenario` del más reciente al más antiguo, y sus pagos, espera la historia de
cada uno y revisa la Cuenta 360 de cuentas elegidas en los archivos: una que está en todos los
cortes, una que sale, una que llega después y una que paga varias veces. Y espera la
interpretación del motor de pagos de cada ventana: que lea cada pago del escenario, que cuente
como duplicados exactos los repetidos de su manifiesto, que una fila que llegó dos veces sea un
movimiento con sus dos observaciones y su archivo original, y que la Cuenta 360 distinga los pagos
observados de los movimientos.

```bash
docker compose exec api motor-cartera generar-escenario --destino datos/escenario --perfil XS \
  --cortes 4 --primer-corte 2026-10-07 --semilla 7
python scripts/prueba_de_humo.py datos/humo.xlsx --escenario datos/escenario
```

Con `--oficial` y `--pagos`, recorre además el lifecycle de cobranza sobre sus cuentas y sus pagos:
registra gestiones (una con promesa, una sin contacto, dos antes de un mismo pago y una que anula),
comprueba la idempotencia de las escrituras, evalúa las promesas a la fecha del corte, atribuye la
ventana de los pagos (una asociación única, una ambigua y una sin candidata por la anulación) y
revisa la Cuenta 360, la línea de tiempo y los trabajos `EVALUACION_PROMESAS` y `ATRIBUCION`.

Y decide el corte oficial con `decision/v2`: si el escenario ya trajo cortes posteriores, comprueba
que un `OPERATIVO` de ese corte da `422 CORTE_NO_VIGENTE` y un `BACKTEST` sin `known_at`
`422 KNOWN_AT_REQUERIDO`, y lo decide como `BACKTEST` con su `known_at` declarado. Revisa las
versiones, el hash de la política, la firma y la disponibilidad; una cuenta con un modificador −1
(un pago reciente), una con +1 (tres llamadas sin contacto) y una con los dos, cada una explicada
regla por regla y verificada; la página por prioridad, la historia de la cuenta, las decisiones del
corte, la Cuenta 360, el trabajo `DECISION_V2` y el `409` de las mismas entradas. El CI, después,
compara en modo sombra esa decisión con la de `decision/v1`.

Con `--geografia` (y `--oficial`), la referencia geográfica ya está importada y la cartera oficial
trae coordenadas sintéticas: resuelve la geografía de su corte, revisa las anomalías por su índice,
publica una versión de bases (y la reutiliza), asigna con `territorial/v2` y revisa la Cuenta 360 de
una cuenta asignada, una sin coordenadas, una que discrepa de la fuente y una sin base, sin que
ninguna respuesta lleve una coordenada:

```bash
docker compose exec api motor-cartera generar-geografia-sintetica --destino datos/geografia/municipios.zip
docker compose exec api motor-cartera importar-geografia datos/geografia/municipios.zip \
  --proveedor SINTETICO --producto "Referencia sintetica de la prueba de humo" --edicion humo-v1
docker compose exec api motor-cartera muestrear-puntos --geografia-version <geografia_version_id> \
  --destino datos/geografia/puntos.json
docker compose exec api motor-cartera generar-oficial --destino datos --formato zip \
  --fecha-corte 2026-09-30 --puntos datos/geografia/puntos.json
python scripts/prueba_de_humo.py datos/humo.xlsx --oficial datos/cartera_oficial_2026-09-30.zip \
  --corte 2026-09-30 --pagos datos/pagos_oficial_2026-09-24_2026-09-30.zip --geografia
```

Con `--campo` (y `--geografia` y `--pagos`), recorre además `campo/v2` sobre el mismo corte:
publica bases de campo sobre los municipios de las candidatas de la decisión/v2 y las asigna con
`territorial/v2`, publica una configuración de campo sintética (y la reutiliza) y pide el plan del
día siguiente al corte, con la fecha declarada. Revisa una cuenta `ASIGNADA` a cada gestor de cupo
1, una de ellas rebalanceada, `SIN_CAPACIDAD`, `SIN_GESTOR_DISPONIBLE` (un gestor cerrado por un
festivo y uno inactivo) y `REVISION_TERRITORIAL`, las cargas, las unidades, la Cuenta 360, el
trabajo `CAMPO_V2` y el `409` de las mismas entradas, sin coordenadas ni celdas. El CI, después,
corre el backfill de esa configuración y esa fecha, que ya no encuentra nada que planear:

```bash
python scripts/prueba_de_humo.py datos/humo.xlsx --oficial datos/cartera_oficial_2026-09-30.zip \
  --corte 2026-09-30 --pagos datos/pagos_oficial_2026-09-24_2026-09-30.zip --geografia --campo
```

Con `--ruteo <red_vial_version_id>` y `--base-ruteo=<lon>,<lat>` (y `--campo`), recorre además
`ruteo/v2` sobre una red vial ya publicada que un OSRM sirve al worker: la red sintética que
`generar-red-sintetica` dibuja dentro de un municipio de la referencia, con sus puntos en la muestra
de la cartera oficial (que por eso se genera después), y una base en el cruce que imprime. Publica
esas bases, las asigna, publica una configuración con un gestor de día completo, uno de turno
partido y uno de 40 minutos, planea el día siguiente al corte y lo rutea; revisa la red, una ruta
`COMPLETA` y una `PARCIAL`, paradas `PROGRAMADA`, `SIN_TIEMPO` y `NO_ENRUTABLE` (por un snap de más
de 500 m y por un tramo sin camino), que en la pausa del turno partido nadie maneja ni atiende, el
regreso a la base, la Cuenta 360, el trabajo `RUTEO_V2` y el `409` de las mismas entradas, sin una
coordenada en ninguna respuesta:

```bash
docker compose exec api motor-cartera generar-red-sintetica --geografia-version <geografia_version_id> \
  --municipio 21114 --puntos datos/geografia/puntos.json --destino datos/red_vial/fuente/red_sintetica.osm
docker compose exec api motor-cartera preparar-red-vial datos/red_vial/fuente/red_sintetica.osm \
  --region "Red sintetica de la prueba de humo" --proveedor-datos SINTETICO --licencia sintetica --docker
sh datos/red_vial/<huella>/preparar.sh
docker compose exec api motor-cartera registrar-red-vial datos/red_vial/<huella>
MC_RED_VIAL_HUELLA=<huella> docker compose --profile ruteo up -d --wait osrm
python scripts/prueba_de_humo.py datos/humo.xlsx --oficial datos/cartera_oficial_2026-09-30.zip \
  --corte 2026-09-30 --pagos datos/pagos_oficial_2026-09-24_2026-09-30.zip --geografia --campo \
  --ruteo <red_vial_version_id> --base-ruteo=<lon>,<lat>
```

La base va unida a su opción, con `=`: en México su longitud es negativa, y separada, el `argparse`
de Python 3.12 la toma por otra opción.

Con `--analytics` (y `--ruteo`), recorre además `analytics/v1` sobre la cohorte del corte oficial,
del corte al día del plan de ruteo, por día: el catálogo con su hash, dos gestiones del último día y
la evaluación de las promesas a ese día, el manifiesto con lo que la prueba publicó (la
interpretación de septiembre y su atribución, la evaluación, `decision/v2`, la geografía, la
`territorial/v2`, el plan y su ruteo) y lo publicado: el `TOTAL`, un desglose visible, una celda
`SUPRIMIDO` sin valor, la recuperación neta (un día con cobertura, y el horizonte `NO_DISPONIBLE`
porque un día no tiene pagos conocidos), la contactabilidad, las promesas, la decisión, el campo, el
ruteo, los dos embudos y sus controles, todos OK, sin un solo dato de una cuenta. El CI, después,
resume y exporta esa ejecución, comprueba que el backfill del corte reporta ambiguas sus tres
`territorial/v2` sin elegir ninguna, y construye un manifiesto sin ellas que `analizar-cobranza`
analiza.

Con `--predictivo <directorio>` recorre `predictive/model-v1` sobre un escenario predictivo ya
cargado y el manifiesto que `construir-manifiesto-predictivo` propuso en su directorio
(`manifiesto.json`); sin el archivo, solo hace eso. Revisa las features y los targets versionados
(ninguna feature es un identificador, un dato personal ni una coordenada), pide el entrenamiento por
la API y espera a que el worker publique la versión, recalcula aquí el SHA-256 de cada artefacto y
el hash del modelo, puntúa el último corte (tres probabilidades en [0, 1], el riesgo exactamente
1 − p, la recuperación positiva esperada y el `feature_hash` de cada predicción) y revisa su drift,
una explicación verificada, las predicciones de una cuenta y el scoring `BACKTEST` del corte de
TEST con su `feature_known_at`, con el `409` de las mismas entradas. El escenario va en su propia
cartera. El CI, después, resume el modelo y su drift por la línea de comandos y comprueba que el
desempeño de ese `BACKTEST` con los labels del manifiesto reproduce las métricas de TEST:

```bash
docker compose exec api motor-cartera generar-escenario-predictivo datos/predictivo --cuentas 600 \
  --cortes 20 --primer-corte 2026-03-04
docker compose exec -e MC_CARTERA_ID=PREDICTIVO worker motor-cartera cargar-escenario-predictivo \
  datos/predictivo
docker compose exec worker motor-cartera construir-manifiesto-predictivo --escenario datos/predictivo \
  --validation 1 --test 1 --destino datos/predictivo/manifiesto.json
python scripts/prueba_de_humo.py --predictivo datos/predictivo
```
