# Diccionario de datos: CARTERA (cartera/v2, 93 columnas)

La hoja CARTERA de la cartera oficial, tal como la entrega el acreedor: exactamente estas 93
columnas, ninguna más, en cualquier orden. Es la tabla autoritativa: la única que se juzga y se
publica. CARRIER, la hoja compañera, está en [carrier.md](carrier.md); los pagos, en
[diccionario_pagos.md](diccionario_pagos.md); el porqué, en [fuentes.md](fuentes.md) y en
[decisiones.md](decisiones.md).

Los nombres son los del contrato fuente que se simula, tal cual, incluidos los dos que llevan un
espacio (`SALDO ATRASADO` y `SALDO REQUERIDO`). **Ningún valor de este repositorio sale de un
archivo real**: el generador sintético produce todo, y las descripciones dicen lo que la columna
significa por su nombre, nada más.

**Cómo leer la tabla.**

- **Tipo.** Solo se tipan las columnas cuyo significado no es ambiguo por su nombre: fechas,
  importes, enteros, códigos postales, teléfonos y coordenadas. Las demás se conservan como texto:
  su semántica no está establecida, y tiparlas sería inventar reglas.
- **Requerida.** Una celda vacía en una columna requerida rechaza el registro. En las demás, vacío
  es válido.
- **Regla.** Lo que el valor tiene que cumplir. Un importe es un número con signo opcional y hasta
  2 decimales, sin separador de miles. Un registro que no cumple se rechaza con su fila, sus valores
  y la regla, y no se corrige nada.
- **En el conformado.** El tipo con que la columna queda en el dataset conformado (Parquet), que
  agrega `_source_row` (la fila del archivo) y `_source_sheet` (la hoja o el miembro del zip).
- **Proyección a Cuenta.** Las siete columnas que la proyección `operacional/v1` lleva a `Cuenta`,
  la forma que leen los motores. Las otras 86 quedan en el conformado.

La fecha de corte **no es una columna**: se declara al subir el archivo y queda en la corrida. No
hay una columna 94.

| # | Columna | Tipo | Requerida | Regla | En el conformado | Descripción | Proyección a Cuenta |
|---|---|---|---|---|---|---|---|
| 1 | `CLIENTE_UNICO` | código | sí | 8 a 20 letras mayúsculas o dígitos | `string` | Identificador fuente de la cuenta. No se repite dentro del corte: si se repite, se rechazan todas sus copias. La misma cuenta en otro corte es válida. | `cliente_unico` |
| 2 | `NOMBRE_CTE` | texto | — | — | `string` | Nombre del cliente. | — |
| 3 | `GENERO_CLIENTE` | texto | — | — | `string` | Género del cliente. | — |
| 4 | `EDAD_CLIENTE` | entero | — | de 0 a 130 | `int64` | Edad del cliente, en años. | — |
| 5 | `OCUPACION` | texto | — | — | `string` | Ocupación del cliente. | — |
| 6 | `DIRECCION_CTE` | texto | — | — | `string` | Calle del domicilio del cliente. | — |
| 7 | `NUM_EXT_CTE` | texto | — | — | `string` | Número exterior del domicilio. | — |
| 8 | `NUM_INT_CTE` | texto | — | — | `string` | Número interior del domicilio. | — |
| 9 | `CP_CTE` | código | — | 5 dígitos | `string` | Código postal del domicilio. | — |
| 10 | `COLONIA_CTE` | texto | — | — | `string` | Colonia del domicilio. | — |
| 11 | `POBLACION_CTE` | texto | sí | — | `string` | Municipio o población del domicilio. Con ESTADO_CTE se resuelve a una clave municipal del INEGI. | `cve_municipio` (INEGI) |
| 12 | `ESTADO_CTE` | texto | sí | — | `string` | Entidad federativa del domicilio. | `cve_entidad` (INEGI) |
| 13 | `TERRITORIO` | texto | — | — | `string` | Territorio comercial al que el acreedor asigna la cuenta. | — |
| 14 | `TERRITORIAL` | texto | — | — | `string` | Agrupación territorial superior del acreedor. | — |
| 15 | `ZONA` | texto | — | — | `string` | Zona operativa del acreedor. | — |
| 16 | `ZONAL` | texto | — | — | `string` | Agrupación de zonas del acreedor. | — |
| 17 | `NOMBRE_DESPACHO` | texto | — | — | `string` | Despacho de cobranza al que está asignada la cuenta. En v0.6 hay uno solo. | — |
| 18 | `GERENCIA` | texto | — | — | `string` | Gerencia del acreedor responsable de la cuenta. | — |
| 19 | `FECHA_ASIGNACION` | fecha | — | AAAA-MM-DD | `date32` | Fecha en que la cuenta llegó al despacho. | — |
| 20 | `DIAS_ASIGNACION` | entero | — | >= 0 | `int64` | Días desde la asignación hasta el corte. | — |
| 21 | `REFERENCIAS_DOMICILIO` | texto | — | — | `string` | Referencias para ubicar el domicilio. | — |
| 22 | `CLASIFICACION_CTE` | texto | — | — | `string` | Clasificación del cliente según el acreedor. Su semántica no está establecida: se conserva como texto. | — |
| 23 | `DIQUE` | texto | — | — | `string` | Clasificación del acreedor. Su semántica no está establecida: se conserva como texto. | — |
| 24 | `ATRASO_MAXIMO` | entero | — | >= 0 | `int64` | Máximo atraso que ha tenido la cuenta, en días. | — |
| 25 | `DIAS_ATRASO` | entero | sí | de 0 a 3650 | `int64` | Días de atraso al corte. | `dias_atraso` |
| 26 | `SALDO` | importe | — | hasta 2 decimales | `decimal(14, 2)` | Saldo de capital. | — |
| 27 | `MORATORIOS` | importe | — | hasta 2 decimales | `decimal(14, 2)` | Intereses moratorios. | — |
| 28 | `SALDO_TOTAL` | importe | sí | hasta 2 decimales; >= 0 | `decimal(14, 2)` | Saldo total de la cuenta al corte. | `saldo_total` |
| 29 | `SALDO ATRASADO` | importe | — | hasta 2 decimales | `decimal(14, 2)` | Saldo vencido. El nombre lleva un espacio, como en la fuente. | — |
| 30 | `SALDO REQUERIDO` | importe | — | hasta 2 decimales | `decimal(14, 2)` | Monto requerido para regularizar la cuenta. El nombre lleva un espacio. | — |
| 31 | `PAGO_NORMAL` | importe | — | hasta 2 decimales | `decimal(14, 2)` | Pago periódico pactado. | — |
| 32 | `PRODUCTO` | catálogo | sí | `CONSUMO` · `TARJETA` · `NOMINA` · `AUTOMOTRIZ` | `string` | Producto de crédito, con el catálogo de cartera/v1. | `producto` |
| 33 | `ESTRATEGIA` | texto | — | — | `string` | Estrategia de cobranza que asigna el acreedor. | — |
| 34 | `FECHA_ULTIMO_PAGO` | fecha | — | AAAA-MM-DD | `date32` | Fecha del último pago del cliente. | — |
| 35 | `IMP_ULTIMO_PAGO` | importe | — | hasta 2 decimales | `decimal(14, 2)` | Importe del último pago. | — |
| 36 | `CALLE_EMPLEO` | texto | — | — | `string` | Calle del empleo del cliente. | — |
| 37 | `NUM_EXT_EMPLEO` | texto | — | — | `string` | Número exterior del empleo. | — |
| 38 | `NUM_INT_EMPLEO` | texto | — | — | `string` | Número interior del empleo. | — |
| 39 | `COLONIA_EMPLEO` | texto | — | — | `string` | Colonia del empleo. | — |
| 40 | `POBLACION_EMPLEO` | texto | — | — | `string` | Municipio o población del empleo. | — |
| 41 | `ESTADO_EMPLEO` | texto | — | — | `string` | Entidad federativa del empleo. | — |
| 42 | `NOMBRE_AVAL` | texto | — | — | `string` | Nombre del aval. | — |
| 43 | `TEL_AVAL` | código | — | 10 dígitos | `string` | Teléfono del aval. | — |
| 44 | `CALLE_AVAL` | texto | — | — | `string` | Calle del domicilio del aval. | — |
| 45 | `NUM_EXT_AVAL` | texto | — | — | `string` | Número exterior del domicilio del aval. | — |
| 46 | `COLONIA_AVAL` | texto | — | — | `string` | Colonia del domicilio del aval. | — |
| 47 | `CP_AVAL` | código | — | 5 dígitos | `string` | Código postal del domicilio del aval. | — |
| 48 | `POBLACION_AVAL` | texto | — | — | `string` | Municipio o población del domicilio del aval. | — |
| 49 | `ESTADO_AVAL` | texto | — | — | `string` | Entidad federativa del domicilio del aval. | — |
| 50 | `FIDIAPAGO` | texto | — | — | `string` | Indicador del acreedor. Su semántica no está establecida: se conserva como texto. | — |
| 51 | `TELEFONO1` | código | — | 10 dígitos | `string` | Primer teléfono de contacto del cliente. | — |
| 52 | `TELEFONO2` | código | — | 10 dígitos | `string` | Segundo teléfono de contacto. | — |
| 53 | `TELEFONO3` | código | — | 10 dígitos | `string` | Tercer teléfono de contacto. | — |
| 54 | `TELEFONO4` | código | — | 10 dígitos | `string` | Cuarto teléfono de contacto. | — |
| 55 | `TIPOTEL1` | texto | — | — | `string` | Tipo del primer teléfono. | — |
| 56 | `TIPOTEL2` | texto | — | — | `string` | Tipo del segundo teléfono. | — |
| 57 | `TIPOTEL3` | texto | — | — | `string` | Tipo del tercer teléfono. | — |
| 58 | `TIPOTEL4` | texto | — | — | `string` | Tipo del cuarto teléfono. | — |
| 59 | `LATITUD` | decimal | — | de -90 a 90 | `double` | Latitud del domicilio. El generador la deja vacía. | — |
| 60 | `LONGITUD` | decimal | — | de -180 a 180 | `double` | Longitud del domicilio. El generador la deja vacía. | — |
| 61 | `DESPACHO_GESTIONO` | texto | — | — | `string` | Despacho que hizo la última gestión. | — |
| 62 | `ULTIMA_GESTION` | texto | — | — | `string` | Cuándo fue la última gestión. Se conserva como texto. | — |
| 63 | `GESTION_DESC` | texto | — | — | `string` | Descripción de la última gestión. | — |
| 64 | `CAMPANIA_RELAMPAGO` | texto | — | — | `string` | Campaña temporal en que participa la cuenta. | — |
| 65 | `CAMPANIA` | texto | — | — | `string` | Campaña asignada a la cuenta. | — |
| 66 | `PREVENTA` | texto | — | — | `string` | Indicador de preventa del acreedor. | — |
| 67 | `ID_GRUPO` | texto | — | — | `string` | Identificador de grupo del acreedor. | — |
| 68 | `GRUPO_MAZ` | texto | — | — | `string` | Agrupación del acreedor. Su semántica no está establecida: se conserva como texto. | — |
| 69 | `CLAVE_SPEI` | texto | — | — | `string` | Clave interbancaria para pagar. En el generador empieza con 000, que no es la de ningún banco. | — |
| 70 | `PAGOS_CLIENTE` | entero | — | >= 0 | `int64` | Cuántos pagos ha hecho el cliente. | — |
| 71 | `MONTO_PAGOS` | importe | — | hasta 2 decimales | `decimal(14, 2)` | Monto acumulado de esos pagos. | — |
| 72 | `GESTORES` | texto | — | — | `string` | Gestor asignado a la cuenta. | — |
| 73 | `FOLIO_PLAN` | texto | — | — | `string` | Folio del plan de pagos, si hay uno. | — |
| 74 | `SEGMENTO_GENERACION` | texto | — | — | `string` | Segmento con que se generó el plan. | — |
| 75 | `ESTATUS_PLAN` | texto | — | — | `string` | Estatus del plan de pagos. | — |
| 76 | `SEMANAS_ATRASO` | entero | — | >= 0 | `int64` | Semanas de atraso al corte. | — |
| 77 | `ATRASO` | texto | — | — | `string` | Tramo de atraso, como texto. | — |
| 78 | `GENERACION_PLAN` | texto | — | — | `string` | Cuándo se generó el plan. Se conserva como texto. | — |
| 79 | `CANCELACION_CUMPLIMIENTO_PLAN` | texto | — | — | `string` | Cuándo se canceló o se cumplió el plan. Se conserva como texto. | — |
| 80 | `ULTIMO_ESTATUS` | texto | — | — | `string` | Último estatus de gestión de la cuenta. | — |
| 81 | `EMPLEADO` | texto | — | — | `string` | Empleado del acreedor asociado a la cuenta. | — |
| 82 | `CANAL` | catálogo | sí | `CAMPO` · `TELEFONICA` · `DIGITAL` | `string` | Canal de gestión, con el catálogo de cartera/v1. | `canal` |
| 83 | `ABONO_SEMANAL` | importe | — | hasta 2 decimales | `decimal(14, 2)` | Abono semanal del plan. | — |
| 84 | `PLAZO` | entero | — | >= 0 | `int64` | Plazo del plan, en semanas. | — |
| 85 | `MONTO_ABONADO` | importe | — | hasta 2 decimales | `decimal(14, 2)` | Lo que se ha abonado al plan. | — |
| 86 | `MONTO_PLAN` | importe | — | hasta 2 decimales | `decimal(14, 2)` | Monto total del plan. | — |
| 87 | `ENGANCHE` | importe | — | hasta 2 decimales | `decimal(14, 2)` | Enganche del plan. | — |
| 88 | `PAGOS_RECIBIDOS` | entero | — | >= 0 | `int64` | Pagos del plan recibidos. | — |
| 89 | `SALDO_ANTES_DEL_PLAN` | importe | — | hasta 2 decimales | `decimal(14, 2)` | Saldo total cuando se generó el plan. | — |
| 90 | `SALDO_ATRASADO_ANTES_PLAN` | importe | — | hasta 2 decimales | `decimal(14, 2)` | Saldo vencido cuando se generó el plan. | — |
| 91 | `MORATORIOS_ANTES_PLAN` | importe | — | hasta 2 decimales | `decimal(14, 2)` | Moratorios cuando se generó el plan. | — |
| 92 | `ESTATUS_PROMESA_PAGO` | texto | — | — | `string` | Estatus de la promesa de pago, si hay una. | — |
| 93 | `MONTO_PROMESA_PAGO` | importe | — | hasta 2 decimales | `decimal(14, 2)` | Monto prometido. | — |
