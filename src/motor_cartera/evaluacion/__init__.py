"""La evaluacion de las promesas de pago: que se observa de cada una a una fecha de corte.

Si una promesa se cumplio no lo decide nadie con un UPDATE: lo concluye una evaluacion versionada
(`evaluacion-promesa/v1`), a una fecha de corte explicita, sobre los movimientos economicos que el
motor de pagos interpreta de su cuenta. Observar recuperacion compatible con una promesa no
demuestra que la promesa la haya producido.

- `reglas`: el nucleo puro y legible de evaluacion-promesa/v1.
- `ejecuciones`: la evaluacion de una cartera a una fecha, en PostgreSQL y todo o nada, con su
  trabajo EVALUACION_PROMESAS.
- `backfill`: las evaluaciones que faltan a una fecha de corte (`motor-cartera backfill-lifecycle`).
- `consultas`: lo que la API lee de las ejecuciones y de lo que concluyeron.

No reexporta nada: cada modulo se importa por su nombre.
"""
