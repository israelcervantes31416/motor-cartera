# Diccionario de datos: PAGOS (pagos/v1, 23 columnas)

Los movimientos económicos de un periodo, tal como los reporta el acreedor: exactamente estas 23
columnas, ninguna más, en cualquier orden. **Una fila es un movimiento**: un pago recibido, con su
fecha, su importe y lo que el acreedor sabía de la cuenta en ese momento. El porqué está en
[fuentes.md](fuentes.md) y en [decisiones.md](decisiones.md); la cartera, en
[diccionario_cartera.md](diccionario_cartera.md).

Los nombres son los del contrato fuente que se simula, tal cual, con sus acentos. **Ningún valor de
este repositorio sale de un archivo real.**

**Lo que pagos/v1 no hace, a propósito.**

- **No deduplica.** Dos filas idénticas son dos movimientos, y las dos quedan en el dataset
  conformado. La regla histórica que trataba como el mismo pago dos filas con el mismo cliente, la
  misma fecha de recepción al segundo y el mismo importe a centavos
  (`Cliente_Unico`, `Fecha_Recepción`, `Recuperación_por_Gestión`) queda documentada como referencia para el motor de pagos de
  v0.8, pero no se aplica.
- **No interpreta signos ni concilia.** Un importe negativo es válido; qué significa, y a qué
  gestión se atribuye cada pago, es de v0.8.
- **No trae columnas técnicas.** Ni identificador de movimiento, ni de crédito, ni de lote, ni el
  nombre del archivo: un archivo que traiga una columna de más falla por estructura.
- **No exige que el año y la semana correspondan a la fecha de recepción.** Se conservan como
  vienen.

La barrera es conservadora: con la tolerancia por omisión (`MC_TOLERANCIA_RECHAZO_PAGOS=0`), un solo
movimiento inválido rechaza el archivo entero. Sus rechazos quedan a la vista en
`GET /pagos/{pagos_run_id}/rechazos`.

| # | Columna | Tipo | Requerida | Regla | En el conformado | Descripción |
|---|---|---|---|---|---|---|
| 1 | `Año` | entero | — | de 1900 a 2100 | `int64` | Año del movimiento, como lo reporta el acreedor. |
| 2 | `Semana` | entero | — | de 1 a 53 | `int64` | Semana del año del movimiento. |
| 3 | `Territorio` | texto | — | — | `string` | Territorio de la cuenta cuando se recibió el pago. |
| 4 | `Zona` | texto | — | — | `string` | Zona de la cuenta cuando se recibió el pago. |
| 5 | `Cliente_Unico` | código | sí | 8 a 20 letras mayúsculas o dígitos | `string` | La cuenta que paga, con la forma de CLIENTE_UNICO de la cartera. Un mismo cliente aparece tantas veces como movimientos tenga. |
| 6 | `Fecha_Recepción` | fecha y hora | sí | AAAA-MM-DD[ HH:MM:SS] | `timestamp[us]` | Cuándo se recibió el pago, en hora local del acreedor, sin zona horaria. Sin hora, es la medianoche. |
| 7 | `Segmento` | texto | — | — | `string` | Tramo de mora de la cuenta al pagar. |
| 8 | `Gerencia` | texto | — | — | `string` | Gerencia de la cuenta. |
| 9 | `Tipo_Cartera` | texto | — | — | `string` | Tipo de cartera de la cuenta. |
| 10 | `Producto` | texto | — | — | `string` | Producto de la cuenta. Aquí es texto libre: no se exige el catálogo de la cartera. |
| 11 | `Campaña` | texto | — | — | `string` | Campaña de la cuenta. |
| 12 | `Gestor` | texto | — | — | `string` | Gestor de la cuenta. |
| 13 | `Días_de_Atraso` | entero | — | >= 0 | `int64` | Días de atraso de la cuenta al pagar. |
| 14 | `Semanas_de_Atraso` | entero | — | >= 0 | `int64` | Semanas de atraso de la cuenta al pagar. |
| 15 | `Plan_de_Pago` | texto | — | — | `string` | Si la cuenta tenía un plan de pagos. |
| 16 | `Fecha_de_Gestion` | fecha y hora | — | AAAA-MM-DD[ HH:MM:SS] | `timestamp[us]` | Cuándo fue la gestión asociada al pago, si la hay. |
| 17 | `Recuperación_por_Gestión` | importe | sí | hasta 2 decimales | `decimal(14, 2)` | Importe del movimiento. Puede ser negativo, como un ajuste: interpretar el signo es del motor de pagos de v0.8. |
| 18 | `Concepto_Cálculo` | texto | — | — | `string` | Concepto del movimiento. |
| 19 | `Cargos_Automáticos` | importe | — | hasta 2 decimales | `decimal(14, 2)` | Cargos automáticos del movimiento. |
| 20 | `Captación` | importe | — | hasta 2 decimales | `decimal(14, 2)` | Captación del movimiento. |
| 21 | `Cobranza_Total` | importe | — | hasta 2 decimales | `decimal(14, 2)` | Cobranza total del movimiento. |
| 22 | `Porcentaje_Comision` | decimal | — | — | `double` | Porcentaje de comisión, como fracción decimal. |
| 23 | `Monto_Comision` | importe | — | hasta 2 decimales | `decimal(14, 2)` | Monto de la comisión. |
