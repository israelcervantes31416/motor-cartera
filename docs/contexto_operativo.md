# El contexto operativo de la app

Cada pantalla de la app (v0.17) lee **un contexto operativo**: un corte y, de cada motor, la
ejecución que declara, por su identificador público. El contexto vive en la URL, no en el estado de
la pantalla: copiar una URL reproduce la misma vista lógica, con los mismos números, porque lo
publicado por una ejecución no cambia. La API que lo resuelve es `app/v1`
([app.md](app.md)); cómo se muestra, [dashboards.md](dashboards.md).

**Nada se completa ni se corrige.** Lo que el contexto no declara no se lee: no hay "la ejecución
más reciente" de nada, ni en la API ni en la app. Un contexto que no encadena se reporta con cada
problema y ninguna pantalla lo combina.

## Lo que declara

| En la URL | En la API | Qué es |
| --- | --- | --- |
| `corte` | `corte_id` | El corte canónico (`corte_canonico.corte_id`): la foto de la cartera. Obligatorio. |
| `decision` | `decision_run_id` | Una ejecución de `decision/v2` del corte. |
| `analytics` | `analytics_run_id` | Una ejecución de `analytics/v1` cuya cohorte es la del corte. |
| `scoring` | `scoring_run_id` | Un scoring de `predictive/model-v1` del corte. |
| `optimizacion` | `optimizacion_run_id` | Una ejecución de `optimization/v1` del corte. |
| `campo` | `campo_run_id` | Un plan de `campo/v2` del corte. |
| `ruteo` | `ruteo_run_id` | Una ejecución de `ruteo/v2` de un plan del corte. |

Cada valor tiene que ser un UUID. Uno que no lo es (por ejemplo `scoring=ultimo`) no se usa ni se
corrige: la pantalla lo reporta. El orden de los parámetros es fijo (el de la tabla), así que la
misma vista tiene siempre la misma URL. Los filtros, el orden y la página de cada pantalla van en la
misma URL, después del contexto.

## Lo que la API resuelve

`GET /app/v1/contextos/validar` lee lo que cada ejecución declarada dice de sí misma, sin calcular
nada: su corte, su estado y su resultado, sus relojes (`as_of`, `known_at` y su zona horaria, o la
fecha de operación), sus versiones y hashes, y **lo que ella misma declaró que leyó** al publicarse:

| Ejecución | Lo que declaró que leyó |
| --- | --- |
| `optimization/v1` | su scoring, su `decision/v2`, su plan de `campo/v2` y su `ruteo/v2` (con `CAMPO`), y su configuración |
| `campo/v2` | su `decision/v2`, su `territorial/v2`, su geografía y su configuración de campo |
| `ruteo/v2` | su plan de `campo/v2` y su red vial |
| `analytics/v1` | cada entrada de su manifiesto: `decision/v2`, la geografía, `territorial/v2`, `campo/v2`, `ruteo/v2`, la evaluación de promesas, las interpretaciones de pagos y la atribución |
| scoring | su versión del modelo |

Responde siempre 200, compatible o no: `compatible`, el corte, lo declarado, cada ejecución
resuelta, lo **derivado** (lo que las declaradas leyeron y el contexto no declara, como la
`territorial/v2` del plan o la versión del modelo del scoring) y cada problema.

## Cuándo es compatible

Un contexto es compatible si no tiene ningún problema. Los problemas, en un orden estable:

| Código | Cuándo |
| --- | --- |
| `EJECUCION_NO_ENCONTRADA` | Una ejecución declarada no existe. |
| `CORTE_DISTINTO` | Una ejecución declarada es de otro corte que el del contexto. |
| `ENCADENAMIENTO_DISTINTO` | Una ejecución leyó otra cosa que lo que el contexto declara (la optimización leyó otro scoring que el declarado), o dos ejecuciones leyeron cosas distintas de lo mismo (el plan de campo y la analytics leyeron dos `decision/v2` distintas). |

Cada problema dice qué ejecuciones lo causan y por qué, para una persona. La app los muestra tal
cual, con un enlace al selector para cambiar el contexto; nunca elige otra ejecución ni quita la que
no encadena.

Las pantallas que necesitan una ejecución (Analytics, la analytics; Predictivo, el scoring;
Optimización, la optimización; Campo, el plan; Ruteo, el ruteo) dicen que el contexto no la declara
y ofrecen declararla en el selector. Las que no (el Resumen, la Cuenta 360, la Auditoría) muestran
cada bloque de lo que se declaró y, de lo demás, que no se declaró: `NO_DECLARADA`, sin un solo
cero.

## El selector

`GET /app/v1/contextos/opciones` da lo que hace falta para abrir uno, con límites: las carteras del
despacho, los cortes de una (paginados, del más reciente al más antiguo), y, con un corte, las 20
ejecuciones más recientes de cada tipo con su total, y hasta 6 **sugerencias**:

- `DESDE_OPTIMIZACION`: una optimización `EXITOSA` y lo que declaró que leyó, con la analytics más
  reciente del corte que encadene con ella, si alguna encadena;
- `DESDE_ANALYTICS`: una analytics `EXITOSA` y su manifiesto, con la optimización más reciente que
  encadene, si alguna encadena.

Cada sugerencia se valida antes de proponerse, con las mismas reglas que un contexto declarado a
mano. **Una sugerencia no se abre sola**: la persona elige "Abrir este contexto" o la ajusta. El
selector también deja declarar cada ejecución a mano, y valida el contexto mientras se arma.

## Estados

En el Resumen, cada bloque dice el estado de su fuente:

| Estado | Qué muestra |
| --- | --- |
| `DISPONIBLE` | Lo que la ejecución publicó, tal cual. |
| `NO_DECLARADA` | Que el contexto no la declara: no se busca otra. |
| `EN_PROCESO` | Que todavía no publica. La validación se repite cada 30 segundos, sin más. |
| `FALLIDA` | Que terminó sin publicar, con su resultado. |

En la Cuenta 360, cada sección (decisión, geografía y territorial, campo, ruteo, predictivo,
optimización) además distingue `NO_PUBLICADA` (la ejecución no terminó `EXITOSA`) de `SIN_RESULTADO`
(la ejecución publicó, pero no tiene a esta cuenta: por ejemplo, una cuenta sin parada en el ruteo).

## Relojes

Cada ejecución lleva sus relojes a la vista, en la cabecera del contexto y en la fuente de cada
bloque: `as_of` es una fecha de negocio y se muestra el mismo día, sin convertirla a la zona del
navegador; `known_at` es un instante y se muestra en la zona horaria de su ejecución, con la zona
escrita (o en UTC, dicho). En la historia de una cuenta, lo que se registró después del `known_at`
del contexto (el de su analytics, o si no el de su decisión o el de su scoring) se marca
*Registrado después del known_at del contexto*: llegó tarde para esas ejecuciones.

## La cache

La app guarda cada respuesta con una llave que lleva el contexto entero (un lugar por ejecución,
vacío si no se declara), los identificadores, los filtros, la página y el orden: dos contextos
distintos nunca comparten una entrada. Lo publicado por una ejecución `EXITOSA` no cambia, así que
se guarda diez minutos sin volver a pedirse; un contexto con una ejecución `EN_PROCESO` se vuelve a
validar cada 30 segundos. No hay WebSockets ni polling agresivo.
