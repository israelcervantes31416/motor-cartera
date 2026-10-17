# El modelo matemático de `optimization/v1`

Este documento fija el problema que resuelve `optimization/v1` con la política
`optimization-public-v1`: sus conjuntos, sus parámetros, sus variables, su jerarquía de objetivos,
sus restricciones, el teorema que permite reducir los candidatos sin cambiar el óptimo, la fijación
por dualidad entre etapas y la regla con que un plan se publica. El uso está en
[optimizacion.md](optimizacion.md); la configuración, en
[configuracion_optimizacion.md](configuracion_optimizacion.md); el porqué, en las decisiones 223 a
247 de [decisiones.md](decisiones.md).

**Es el óptimo del modelo de asignación declarado, no una estrategia causalmente óptima.** El valor
de cada cuenta es la recuperación positiva esperada que predijo `predictive/model-v1` bajo la
política histórica; el modelo asigna capacidad escasa a esas oportunidades predichas. No estima el
efecto incremental de contactar ni dice que el canal asignado cause el resultado predicho.

## Conjuntos

| Símbolo | Qué es |
| --- | --- |
| *I* = {0, …, n − 1} | Las cuentas del universo: cada cuenta del corte con su predicción del scoring declarado y su decisión de la `decision/v2` declarada, numeradas en **orden canónico**, por `cuenta_id` ascendente. |
| *P* = {0, …, m − 1} | Los pools activos de la configuración, numerados por `codigo` ascendente. Cada pool tiene un canal operativo (`DIGITAL`, `TELEFONICA` o `CAMPO`) y un alcance (`GLOBAL`, `BASE` o `GESTOR_CAMPO`). |
| *E* ⊆ *I* × *P* | Los pares elegibles (*i*, *p*): el pool está activo; su canal es admisible para el canal que `decision/v2` recomendó a *i*, según la matriz de la configuración; la predicción de *i* es `COMPLETA`, o la configuración admite `CON_IMPUTACION`; y el alcance alcanza a *i*: `GLOBAL` siempre, `BASE` si la base territorial de *i* es la del pool, `GESTOR_CAMPO` si *i* tiene una parada `PROGRAMADA` de ese gestor en el `ruteo/v2` declarado. |
| *P*ᵢ = {*p* : (*i*, *p*) ∈ *E*} | Los pools elegibles de la cuenta *i*. Si *P*ᵢ es vacío, *i* es `NO_ELEGIBLE`. |
| *K* | Las restricciones de cobertura activas de la configuración. |
| *G*ₖ ⊆ *I* | El grupo de la cobertura *k*: las cuentas cuyo valor en su dimensión (`PRODUCTO`, `ATRASO_BUCKET`, `PRIORIDAD_DECISION` o `BASE`) es el de *k*. |

Ningún par imposible tiene variable: el modelo se construye solo sobre *E*, por igualdad en cada
alcance, nunca como un producto cartesiano de cuentas por pools.

## Parámetros

Todos son enteros exactos; ningún dinero es un número de punto flotante.

| Símbolo | Qué es |
| --- | --- |
| *v*ᵢ | El valor de oportunidad de *i*, en centavos: `recuperacion_positiva_esperada_30d × 100`, exacto (la predicción tiene dos decimales). **No se multiplica por ninguna probabilidad**: ya es la media incondicional de max(recuperación neta, 0). |
| *c*ᵢ | `p_contacto_14d × 10⁸`, exacto (la predicción tiene ocho decimales). Solo desempata. |
| *u*ₚ | Las unidades de recurso de una asignación al pool *p*: un entero de 1 a 1,000,000. Esfuerzo abstracto declarado, no dinero. |
| *cap*ₚ | La capacidad efectiva de *p* en la ejecución. `DIGITAL` y `TELEFONICA`: la declarada. `CAMPO`: min(declarada, paradas `PROGRAMADA` de su gestor en el ruteo); sin capacidad declarada, esas paradas. |
| *M* | `max_cuentas_atendidas`, si se declara. |
| *B* | `max_unidades_recurso`, si se declara. |
| *L*ₖ | El límite de la cobertura *k*: cuentas en `MIN_CUENTAS` y `MAX_CUENTAS`; puntos base *b*ₖ (0 a 10,000) en `MIN_PARTICIPACION_BPS` y `MAX_PARTICIPACION_BPS`. |

Antes de construir nada se comprueba que cada coeficiente, cada cota y cada suma posible de cada
fila y de cada objetivo cabe en 2⁵³ − 1, el mayor entero que un `double` representa exactamente; si
no, la ejecución termina `COEFICIENTES_FUERA_DE_RANGO` sin construir el modelo.

## Variables

- *x*ᵢₚ ∈ {0, 1} para cada par (*i*, *p*) ∈ *E*: la cuenta *i* queda asignada al pool *p*.
- *z*ᵢ = Σₚ *x*ᵢₚ: la cuenta *i* queda atendida (una expresión, no una variable).

## Restricciones

| | Restricción | Para cada |
| --- | --- | --- |
| (R1) | Σ_{p ∈ Pᵢ} *x*ᵢₚ ≤ 1 | cuenta *i*: a lo más un pool |
| (R2) | Σ_{i : (i,p) ∈ E} *x*ᵢₚ ≤ *cap*ₚ | pool *p* |
| (R3) | Σᵢ *z*ᵢ ≤ *M* | si se declara |
| (R4) | Σ_{(i,p) ∈ E} *u*ₚ *x*ᵢₚ ≤ *B* | si se declara |
| (R5) | Σ_{i ∈ Gₖ} *z*ᵢ ≥ *L*ₖ (`MIN_CUENTAS`), ≤ *L*ₖ (`MAX_CUENTAS`) | cobertura *k* |
| (R6) | 10,000 · Σ_{i ∈ Gₖ} *z*ᵢ ≥ *b*ₖ · Σᵢ *z*ᵢ (`MIN_PARTICIPACION_BPS`), ≤ (`MAX_PARTICIPACION_BPS`) | cobertura *k* |

Todas son lineales con coeficientes enteros. Resuelta a conteos con *N* = Σᵢ *z*ᵢ cuentas
atendidas, una participación mínima exige ⌈*b*ₖ *N* / 10,000⌉ cuentas del grupo y una máxima permite
⌊*b*ₖ *N* / 10,000⌋, en aritmética entera: es exactamente (R6), porque el conteo del grupo es entero.
Una restricción es *binding* cuando su holgura, en enteros y en su forma declarada, es cero.

## La jerarquía de objetivos

Cuatro etapas lexicográficas. Cada una fija **exactamente** el óptimo de las anteriores con una
igualdad: ninguna constante gigante (*big-M*) mezcla los objetivos.

| Etapa | Sentido | Objetivo | Fija |
| --- | --- | --- | --- |
| 1 | maximizar | *Z*₁ = Σᵢ *v*ᵢ *z*ᵢ | — |
| 2 | maximizar | *Z*₂ = Σᵢ *c*ᵢ *z*ᵢ | *Z*₁ = *Z*₁* |
| 3 | minimizar | *Z*₃ = Σ_{(i,p)} *u*ₚ *x*ᵢₚ | *Z*₁ = *Z*₁*, *Z*₂ = *Z*₂* |
| 4 | minimizar | *Z*₄ = Σ_{(i,p)} *w*ᵢₚ *x*ᵢₚ | *Z*₁ = *Z*₁*, *Z*₂ = *Z*₂*, *Z*₃ = *Z*₃* |

con el peso canónico *w*ᵢₚ = *i* · (*m* + 1) + (*n* − *i*) · *p*. El peso crece con *i* para cada pool
(su derivada en *i* es *m* + 1 − *p* > 0): entre cuentas empatadas en un mismo pool va la de menor
`cuenta_id`; y no es separable en (*i*, *p*): entre permutaciones de los mismos pools sobre las mismas
cuentas, la de menor `cuenta_id` toma el pool de menor código. No es una feature y no cambia el
valor económico: decide, de forma reproducible, entre planes empatados en las tres etapas
anteriores.

La etapa 2 no convierte a `p_contacto_14d` en la probabilidad de éxito del canal asignado: solo
elige, entre planes del mismo valor de oportunidad, el que atiende las cuentas que la operación
histórica contactó más. La etapa 3 elige el de menor esfuerzo declarado.

## El teorema de la reducción de candidatos

**Firma.** La firma de una cuenta elegible *i* es *s*(*i*) = (*P*ᵢ, (𝟙[*i* ∈ *G*ₖ])_{k ∈ K}): sus
pools elegibles y su pertenencia a cada grupo de cobertura activo. Una clase *S* es el conjunto de
cuentas elegibles con una misma firma.

**Lema 1 (columnas iguales).** Dos cuentas de una misma clase tienen las mismas columnas en (R2) a
(R6), pool por pool: el mismo coeficiente 1 en (R2) y (R3), el mismo *u*ₚ en (R4) y los mismos
coeficientes en (R5) y (R6), porque pertenecen a los mismos grupos. En (R1) cada una tiene su propia
fila, con la misma forma.

*Prueba.* Los coeficientes de *x*ᵢₚ en (R2) a (R6) dependen solo de *p* y de la pertenencia de *i* a
cada *G*ₖ, que la firma fija. ∎

**Cota segura.** Para cada clase *S*, sea *u*[*S*] el mínimo de: |*S*|; Σ_{p ∈ P_S} *cap*ₚ; *M*;
⌊*B* / min_{p ∈ P_S} *u*ₚ⌋; *L*ₖ para cada `MAX_CUENTAS` cuyo grupo contiene a *S*; y
⌊*b*ₖ · *N*_max / 10,000⌋ para cada `MAX_PARTICIPACION_BPS` cuyo grupo contiene a *S*, con *N*_max una
cota superior de las cuentas que cualquier plan factible atiende (el mínimo de las cuentas elegibles,
Σₚ *cap*ₚ de los pools que alguna alcanza, *M* y ⌊*B* / minₚ *u*ₚ⌋).

Esas sumas se calculan en enteros, con cada *cap*ₚ topada en *n*, el número de cuentas: ningún plan
usa más cuentas de un pool que las que hay, y como |*S*| ≤ *n*, ningún mínimo cambia. Así ninguna
suma envuelve, aunque mil pools declaren la mayor capacidad admitida (2⁵³ − 1).

**Lema 2.** Todo plan factible asigna a lo más *u*[*S*] cuentas de la clase *S*.

*Prueba.* Cada término es una cota de lo que (R1) a (R6) permiten: no hay más cuentas que |*S*|;
todas van a pools de *P_S*, que no reciben más de su capacidad; el total no pasa de *M*; cada una
consume al menos min *u*ₚ unidades de *B*; un grupo `MAX_CUENTAS` que contiene a *S* no pasa de
*L*ₖ; y en un grupo `MAX_PARTICIPACION_BPS`, 10,000 · |asignadas de *S*| ≤ 10,000 · Σ_{G_k} *z*ᵢ ≤
*b*ₖ *N* ≤ *b*ₖ *N*_max. ∎

**Orden canónico de una clase.** (*v*ᵢ descendente, *c*ᵢ descendente, *i* ascendente).

**Teorema (reducción exacta).** Sea *R*_S el conjunto de las primeras *u*[*S*] cuentas de cada clase
*S* en su orden canónico, y *R* = ∪_S *R*_S. El plan lexicográficamente óptimo del modelo completo
(las cuatro etapas, incluido el desempate canónico) solo asigna cuentas de *R*. Por lo tanto, el
modelo restringido a *R* tiene exactamente los mismos óptimos *Z*₁*, *Z*₂*, *Z*₃* y *Z*₄* y el mismo
plan.

*Prueba.* Sea *x* el plan óptimo y supóngase que asigna una cuenta *j* ∉ *R*, de la clase *S*, al
pool *p*. Por el lema 2, *x* asigna a lo más *u*[*S*] cuentas de *S*; si asignara todas las de *R*_S
(que son exactamente *u*[*S*], porque *j* ∉ *R*_S implica |*S*| > *u*[*S*]), asignaría *u*[*S*] + 1.
Luego hay *r* ∈ *R*_S sin asignar. Sea *x*′ el plan que mueve a *p* la asignación de *j* a *r*. Por
el lema 1, *x*′ cumple (R2) a (R6) con los mismos lados izquierdos, y (R1) porque *r* no tenía pool.
Como *r* precede a *j* en el orden canónico: si *v*ᵣ > *v*ⱼ, *Z*₁(*x*′) > *Z*₁(*x*), contra la etapa
1; si *v*ᵣ = *v*ⱼ y *c*ᵣ > *c*ⱼ, *Z*₂ mejora sin cambiar *Z*₁, contra la etapa 2; si además
*c*ᵣ = *c*ⱼ, *Z*₃ no cambia (el mismo pool) y *r* < *j*, así que *w*ᵣₚ < *w*ⱼₚ y *Z*₄ mejora, contra
la etapa 4. En cualquier caso *x* no era óptimo. ∎

La reducción nunca es un *top N* por score: compara una cuenta solo con las de su misma firma, y
retiene de cada clase todas las que algún plan factible podría usar. Una cuenta de otra firma no se
compara directamente con ellas. Las pruebas comprueban el teorema contra el modelo completo en
instancias al azar, y el benchmark S lo repite sobre 10,000 cuentas reales del escenario M.

## La fijación por dualidad entre etapas

Al terminar una etapa con su óptimo entero *z*, su relajación lineal da multiplicadores *y* (del
signo correcto para cada fila; cualquiera de esos es válido) y, con ellos, los costos reducidos
*r* = *c* − Aᵀ*y* y la cota lagrangiana *L* = *y*ᵀ*b* + Σⱼ min(0, *r*ⱼ *u*ⱼ), con una cota del error de
punto flotante ε. Por dualidad débil, toda solución entera que alcanza *z* en esa etapa cumple
Σⱼ *r*ⱼ *x*ⱼ ≤ *z* − *L*; por lo tanto deja en 0 cada variable con *r*ⱼ > (*z* − *L*) + 2ε y en 1
cada una con −*r*ⱼ > (*z* − *L*) + 2ε. Esas variables quedan fijas en las etapas siguientes, que solo
deciden entre los óptimos de la anterior: es una consecuencia de la etapa, no una heurística. En cada
etapa la solución completa se verifica en aritmética entera contra **todas** las filas, también las
de las variables fijas.

## Cómo se resuelve cada etapa

Con HiGHS, el que trae SciPy (`scipy.optimize`), sin presolve y con `mip_rel_gap = 0`:

1. su relajación lineal, con punto interior y *crossover* (`linprog`, `highs-ipm`);
2. si el vértice óptimo de la relajación es entero (dentro de 10⁻⁹), es el óptimo del MILP: la
   relajación acota el problema entero y la solución entera alcanza esa cota (cota = objetivo, gap
   0, cero nodos);
3. si no lo es, o si la relajación no termina limpia, *branch-and-bound* de HiGHS (`milp`) con el
   tiempo que le queda a la etapa.

## La regla de publicación

Un plan se publica, todo o nada, solo si en **cada** etapa:

- HiGHS termina `OPTIMAL`: su relajación con un vértice entero, o su *branch-and-bound* con
  `mip_rel_gap = 0` (su mejor cota alcanza al objetivo);
- la solución, redondeada a enteros, cumple cada restricción en aritmética entera y reproduce
  exactamente los óptimos fijados de las etapas anteriores.

Cualquier otro estado no publica nada: `TIME_LIMIT` o `ITERATION_LIMIT` terminan
`OPTIMO_NO_DEMOSTRADO` (con lo que el solver alcanzó en el certificado, sin publicarlo); `INFEASIBLE`
termina `INFACTIBLE` con su diagnóstico; una solución que no cumple en enteros es `ERROR_NUMERICO`;
cualquier otro, `ERROR_DEL_SOLVER`. Una solución factible nunca se llama óptima.

## La infactibilidad

Antes del solver, un prechequeo demuestra las infactibilidades evidentes: un mínimo mayor que las
elegibles de su grupo (`MINIMO_SUPERA_ELEGIBLES`) o que lo que la capacidad, el total y el
presupuesto que lo alcanzan permiten (`MINIMO_SUPERA_CAPACIDAD`), un mínimo y un máximo del mismo
grupo que no caben juntos (`MINIMO_CONTRA_MAXIMO`), y los mínimos de una misma dimensión (cuyos
grupos no se enciman) que suman más de lo que cualquier plan atiende (`SUMA_DE_MINIMOS`) o que no
caben en el presupuesto (`PRESUPUESTO_INSUFICIENTE`). La ejecución termina `INFACTIBLE` con el
solver `NO_INVOCADO` y cada hallazgo. Si solo el solver demuestra la infactibilidad, se resuelve un modelo auxiliar que minimiza
la suma de holguras enteras de las restricciones declaradas (las capacidades, el total, el
presupuesto y las coberturas): el **diagnóstico de relajación** dice cuánto habría que relajar cada
una. Es **una relajación mínima posible, no un IIS**: no dice que esas restricciones sean las
únicas en conflicto.

## Lo que el modelo no es

- No es causal: no estima el efecto de contactar ni el de un canal. Un canal asignado es una
  asignación de capacidad compatible con las restricciones, no la evidencia de que ese canal cause
  la recuperación predicha.
- No reoptimiza rutas: `CAMPO` solo elige un subconjunto de las paradas `PROGRAMADA` que
  `ruteo/v2` ya demostró factibles, con su gestor, su base y su orden. Quitar paradas de una ruta
  factible no la vuelve infactible; agregar o reordenar sí podría, y no se hace.
- No tiene un modelo de costos monetarios: las unidades de recurso son esfuerzo abstracto declarado.
- No mueve cuentas entre bases ni entre gestores.
