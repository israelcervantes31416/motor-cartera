# Territorial v2 (`territorial/v2`)

`territorial/v1` agrupa las cuentas de campo de una decisión por el municipio que resolvió la
fuente, y `ruteo/v1` ordena sus visitas en un plano sintético. `territorial/v2` usa por primera vez
la geografía real ([geografia.md](geografia.md)) y una versión de bases operativas
([bases_operativas.md](bases_operativas.md)), y responde:

> Con la geografía publicada de un corte y una versión declarada de bases, ¿a qué base operativa
> pertenece cada cuenta según `territorial-public-v2`, y por qué?

Es **pertenencia territorial, no capacidad**: dice qué base cubre la ubicación de una cuenta y cuál
está más cerca, no si esa base puede atenderla. No usa carga, gestores, rutas, promesas, saldo,
prioridad ni `decision/v2`.

`territorial/v2` corre **en paralelo a `territorial/v1`**, no lo sustituye: `territorial/v1` sigue
siendo la etapa `TERRITORIAL` del flujo de cada corrida, y `ruteo/v1` sigue leyéndolo. Los dos
contratos se conservan.

```
CorteCanonico ─┬─ EjecucionGeografia (EXITOSA)      el hecho geográfico, de geografia/v1
               └─ VersionBasesOperativas            la configuración, declarada
                        │
EjecucionTerritorialV2  una asignación del corte: sus entradas declaradas, la política con su hash,
      │                 la firma de entrada, los conteos y la distribución por base
AsignacionTerritorialV2 cada cuenta: su estado, su municipio, su base, su distancia y los motivos
```

El código vive en `src/motor_cartera/territorial_v2/`: `politica.py` es el núcleo puro (la política,
su hash y su evaluador); `ejecuciones.py`, la asignación en PostgreSQL; `consultas.py`, lo demás.

## Las entradas, siempre declaradas

Una ejecución declara exactamente sus entradas, y nunca toma "la geografía más nueva" ni "las bases
actuales":

| Entrada | Qué es | Si no sirve |
|---|---|---|
| `corte_id` | El corte canónico: el universo, una cuenta por snapshot. | `404 CORTE_NO_ENCONTRADO` |
| `geografia_run_id` | Una ejecución de `geografia/v1` `EXITOSA` de ese corte. | `404 GEOGRAFIA_NO_ENCONTRADA`, `422 GEOGRAFIA_NO_EXITOSA`, `422 GEOGRAFIA_DE_OTRO_CORTE` |
| `bases_version_id` | Una versión de bases operativas. | `404 BASES_NO_ENCONTRADAS` |
| `version_politica` | `territorial-public-v2`, por omisión y la única. | `422 POLITICA_NO_SOPORTADA` |

Con ellas sale su `firma_inputs`, un SHA-256 del motor, la política y su hash, el corte, la
geografía con su firma y la versión de bases con su hash: las mismas entradas son la misma
asignación (`409 TERRITORIAL_V2_YA_GENERADO`; mientras se resuelve, `409 TERRITORIAL_V2_EN_PROCESO`).
Una asignación histórica es reproducible: otra geografía u otras bases son otra ejecución, y la
anterior no cambia.

## La política `territorial-public-v2`

Para cada cuenta del corte, con su geografía publicada:

1. Si la geografía no es confiable, no se asigna. Un punto fuera de la referencia queda
   `FUERA_DE_COBERTURA`; cualquier otra geografía dudosa (sin coordenadas, incompletas, inválidas,
   en más de un municipio o que discrepa de la fuente), `REVISION_GEOGRAFICA`. Solo una `VALIDA` es
   confiable: una ubicación dudosa no se envía a campo.
2. Con geografía confiable, las **bases elegibles** son las activas de la versión cuya cobertura
   incluye el CVEGEO del municipio espacial de la cuenta. Ninguna: `SIN_BASE_ELEGIBLE` (con las
   inactivas que lo cubren, si las hay, en sus motivos).
3. Una elegible: se asigna esa.
4. Varias: se calcula la **distancia geodésica** del punto de la cuenta a cada una y se asigna la
   **inequívocamente más cercana**. Si otra queda a no más de la **tolerancia de empate** de la más
   cercana, son indistinguibles: `AMBIGUA_BASE`, y no se elige ninguna.

| Dato | Valor |
|---|---|
| Versión del motor | `territorial/v2` |
| Política | `territorial-public-v2` |
| Tolerancia de empate | **100.00 m**, parte de la política y de su hash |
| Distancia | Geodésica: `ST_Distance` sobre `geography` (elipsoide WGS 84), en metros, redondeada a 0.01 |
| `hash_politica` | `b233e9de169bf50add564f4ddcef3b6f93b2bd263a03171959fd7f1c321f4e22`, el SHA-256 de su definición en JSON canónico (`politica.definicion`) |

**La tolerancia** no se calibró con resultados: es la incertidumbre razonable de una coordenada
observada y de la ubicación de una base (el GPS de un teléfono, una dirección capturada a mano, el
punto de una oficina). Dos bases a 1,000.00 y 1,099.99 m de una cuenta no se distinguen; a 1,000.00
y 1,100.01 m, sí. Cambiarla es otra política, con otro hash.

**Un empate no se resuelve** por el código, el nombre, el identificador ni el orden en que la base
de datos devuelva las filas: cualquiera de esos criterios elegiría por algo que no es geografía, y
el resultado cambiaría al renombrar una base. Se dice `AMBIGUA_BASE`, con las bases empatadas, y la
decisión queda para quien opera.

**La distancia es geodésica**, sobre el elipsoide, nunca la raíz de la suma de cuadrados de grados:
a la latitud de Puebla un grado de longitud mide unos 105 km y uno de latitud unos 111, y la
diferencia cambiaría qué base está más cerca. Las pruebas comparan cada distancia publicada con la
fórmula de Vincenty, un cálculo independiente del de PostGIS.

### Los estados

| Estado | Qué quiere decir |
|---|---|
| `ASIGNADA` | Geografía confiable y una base elegible inequívocamente más cercana (o la única). |
| `REVISION_GEOGRAFICA` | La geografía no es confiable: sin coordenadas, incompletas, inválidas, ambigua o discrepante. |
| `FUERA_DE_COBERTURA` | El punto cae fuera de la referencia geográfica. |
| `SIN_BASE_ELEGIBLE` | Geografía confiable, pero ninguna base activa de la versión cubre su municipio. |
| `AMBIGUA_BASE` | Dos o más bases elegibles quedan a no más de la tolerancia de la más cercana. |

Una asignación guarda su municipio espacial cuando la geografía es confiable (`ASIGNADA`,
`SIN_BASE_ELEGIBLE`, `AMBIGUA_BASE`), su base y su distancia solo cuando es `ASIGNADA`.

### Los motivos

| Motivo | Detalle | Cuándo |
|---|---|---|
| `GEOGRAFIA_NO_CONFIABLE` | `estado_geografia` | La geografía no permite asignar. |
| `PUNTO_FUERA_DE_REFERENCIA` | — | El punto no está en ningún municipio de la referencia. |
| `SIN_BASE_QUE_CUBRA` | `cvegeo` | Ninguna base activa cubre ese municipio. |
| `BASES_INACTIVAS` | `bases` | Las que lo cubren pero están inactivas, y no son elegibles. |
| `BASE_UNICA_ELEGIBLE` | `base`, `distancia_m` | Solo una base elegible. |
| `BASE_MAS_CERCANA` | `base`, `distancia_m`, `elegibles`, `siguiente`, `distancia_siguiente_m` | La más cercana de varias, a más de la tolerancia de la siguiente. |
| `EMPATE_ENTRE_BASES` | `bases`, `distancia_m`, `tolerancia_m` | Las indistinguibles, la menor distancia y la tolerancia. |

Las distancias de los motivos son textos con dos decimales (`"1052.61"`): la explicación se lee
igual en cualquier cliente.

## Bases versionadas: la historia no se reescribe

Con la versión 1, `BASE_A` cubre un municipio y una cuenta queda en `BASE_A`. La versión 2 agrega
`BASE_B`, que también lo cubre y está más cerca. Una ejecución con la versión 1 sigue diciendo
`BASE_A`; una con la versión 2 puede decir `BASE_B`; y la primera no cambia: las dos coexisten, cada
una con sus entradas. Lo publicado no se reescribe (un trigger rechaza cualquier `UPDATE` o
`DELETE`), y borrar solo lo derivado de una ejecución y volver a pedirla con las mismas entradas
publica exactamente lo mismo.

| Resultado de una ejecución | Qué quiere decir |
|---|---|
| `TERRITORIAL_PUBLICADO` | `EXITOSA`: publicó la asignación de cada cuenta del corte. |
| `YA_GENERADO` | `FALLIDA`: otra ejecución publicó primero la misma firma de entrada. |
| `VERSION_NO_SOPORTADA` | `FALLIDA`: pide un motor, una política o un hash de política que este servicio no aplica. |
| `GEOGRAFIA_NO_EXITOSA` | `FALLIDA`: la geografía que declara no está `EXITOSA`. |
| `DATOS_INCONSISTENTES` | `FALLIDA`: la geografía no tiene las cuentas del corte, o los conteos no cuadran. |
| `ERROR_INTERNO` | `FALLIDA`: un error inesperado; el detalle no lo expone. |
| `INTENTOS_AGOTADOS` | `FALLIDA`: la cola agotó sus intentos. |
| `CERRADA_AL_BAJAR` | `FALLIDA`: estaba en curso cuando la base se bajó a la 0011. |

## La ejecución: por conjuntos, todo o nada

La piden `POST /territorial-v2` y `motor-cartera asignar-territorial-v2`, y la resuelve un worker
con su trabajo `TERRITORIAL_V2`, en una transacción y con su fila bloqueada:

1. lee la geografía publicada de cada cuenta: ningún punto en polígono se repite;
2. une las cuentas con geografía confiable con las bases activas de la versión que cubren su
   municipio (por municipio, no por cuenta) y calcula la distancia geodésica a cada una;
3. ordena las elegibles de cada cuenta y aplica la política con funciones de ventana;
4. publica con un `INSERT ... SELECT` la asignación de cada cuenta, la cuenta, comprueba que cuadre
   con el corte y cierra `EXITOSA`.

Si algo falla, nada queda publicado y la ejecución queda `FALLIDA`. Solo publica el dueño vigente de
su trabajo. `politica.asignar` es el espejo en Python del SQL, y las pruebas comparan cada
asignación publicada con lo que concluye la política con la distancia de cada base, calculada
aparte.

La distribución de una ejecución dice cuántas cuentas quedaron en cada estado, cuántas en cada base
y su distancia mediana. **Es un diagnóstico, no un balanceo:** que una base reciba muchas más
cuentas que otra no se corrige aquí.

## La API

| Método y ruta | Qué hace |
|---|---|
| `POST /territorial-v2` | Pide la asignación de un corte con sus entradas declaradas. `201` quiere decir creada, no terminada. Errores: los de la tabla de entradas, y `409 TERRITORIAL_V2_EN_PROCESO` o `409 TERRITORIAL_V2_YA_GENERADO`. |
| `GET /territorial-v2` | Las ejecuciones; con `corte_id`, las de un corte. |
| `GET /territorial-v2/{territorial_run_id}` | Una ejecución: sus entradas, la política con su hash y su tolerancia, la firma, los conteos y la distribución. `404 TERRITORIAL_V2_NO_ENCONTRADO`. |
| `GET /territorial-v2/{territorial_run_id}/resultados` | La asignación de cada cuenta, en el orden de la cuenta. Con `base` (su código), `municipio` (un CVEGEO) o `estado`, por su índice. Sin coordenadas. |
| `GET /cuentas/{cuenta_id}/territorial-v2` | Las asignaciones de una cuenta, del corte más reciente al más antiguo, cada una con sus entradas. |

La Cuenta 360 trae, dentro de `geografia_resumen`, la asignación de `territorial/v2` que acompaña a
la geografía vigente de la cuenta: su estado, su base y su distancia. Sin coordenadas.

```bash
uv run motor-cartera asignar-territorial-v2 --corte 2026-09-30 \
  --geografia-run <geografia_run_id> --bases <bases_version_id>
```

## Índices y volumen

En `asignacion_territorial_v2`, la llave (ejecución, cuenta), que es la asignación de una cuenta
en una ejecución, y (ejecución, base, cuenta), (ejecución, estado, cuenta), (ejecución, municipio,
cuenta) y (cuenta, ejecución): una página de una base, de un estado o de un municipio en el orden de
la cuenta, y las asignaciones de una cuenta a través de los cortes. El total de una página de una
ejecución publicada sale de lo que contó al publicar. No hay particionado (decisión 137).

### Benchmark: 500,000 cuentas

Sobre la geografía del benchmark de [geografia.md](geografia.md), con 13 bases sintéticas: tres en
cada una de cuatro entidades del generador (Puebla, Tlaxcala, Veracruz y el Estado de México), cada
una en un punto de la muestra de uno de los tres municipios más poblados de su entidad y cubriendo
todos los municipios de esa entidad, y una inactiva en la Ciudad de México.

- **La asignación de las 500,000 cuentas tomó 75.5 s** (6,624 por segundo), con 154 MiB de memoria
  pico y 353 MiB de WAL: 421,548 `ASIGNADA` (todas entre tres elegibles, `BASE_MAS_CERCANA`), 1,053
  `AMBIGUA_BASE`, 27,381 `SIN_BASE_ELEGIBLE` (las de la Ciudad de México, con su base inactiva en
  los motivos), 40,114 `REVISION_GEOGRAFICA` y 9,904 `FUERA_DE_COBERTURA`. Cuadra con la geografía:
  las 449,982 `VALIDA` son las asignadas, las empatadas y las sin base; las 40,114 en revisión, las
  sin coordenadas, incompletas, inválidas y discrepantes.
- La base con más cuentas, `BASE_21_1`, recibió 202,756 (mediana de 30.6 km); la que menos,
  `BASE_29_3`, 5,730. Es un diagnóstico, no un balanceo.

| Fase | Segundos |
|---|---:|
| La geografía publicada de cada cuenta | 1.8 |
| Las elegibles y su distancia geodésica | 10.5 |
| El orden y los empates (funciones de ventana) | 12.6 |
| La publicación (`INSERT ... SELECT`) | 48.8 |
| Los conteos | 1.7 |

Cada asignación ocupa 446 bytes con sus índices: 212.5 MiB por ejecución.

**Las consultas no recorren la ejecución.** Por la API, con la serialización (mediana): las
asignaciones de una cuenta, 20 ms; una página de una base, 23 ms, y su página 10, 25 ms (antes de
tomar el total de lo que contó la ejecución, 101 ms: contaba las 202,756 asignaciones de la base en
cada página); las sin base, 22 ms; la Cuenta 360 con su `geografia_resumen`, 90 ms, con 23
sentencias de menos de 0.3 ms cada una dentro de PostgreSQL. Ninguna hace un Seq Scan sobre las
asignaciones.

## Lo que `territorial/v2` no hace, a propósito

- **Capacidad de campo**: gestores, jornadas, cargas, límites por base y partición operacional son
  v0.12 (Campo v2). Una base puede recibir cualquier número de cuentas.
- **Zonas, subzonas y tramos territoriales**, ni balancear cuentas entre bases.
- **Rutas**: ni OSRM, ni distancias por calle, ni TSP o VRP; la distancia es geodésica, en línea
  recta sobre el elipsoide.
- **Elegir en un empate**, ni asignar una cuenta con una geografía dudosa.
- **Reemplazar a `territorial/v1`** en el flujo de cada corrida.
