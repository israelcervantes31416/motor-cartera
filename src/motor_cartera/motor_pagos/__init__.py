"""El Motor de Pagos: de lo observado a lo interpretado, sin tocar lo observado.

    PagoObservado            una fila de pagos/v1, tal como llego (v0.7); nunca cambia
         |
    EjecucionMotorPagos      motor-pagos/v1 sobre una ventana: un despacho, una cartera y un mes
         |
    ResultadoPagoObservado   que concluyo de cada observacion de la ventana, y por que
         |
    MovimientoEconomicoCanonico   cada hecho economico que considera distinto, conciliado o no con
                                  su CuentaCanonica

- `firmas`: las huellas de una observacion (exacta y legacy) y los identificadores deterministas de
  los movimientos, en Python y en SQL.
- `reglas`: el vocabulario de v1 y sus reglas, como nucleo puro y legible.
- `ejecuciones`: la interpretacion de una ventana en PostgreSQL, todo o nada, y su trabajo
  MOTOR_PAGOS.
- `contexto`: donde cae un movimiento entre los snapshots de su cuenta, calculado al consultar.
- `backfill`: encolar la interpretacion de los pagos observados que no la tienen.
- `consultas`: lo que la API lee de la interpretacion vigente.

No reexporta nada: cada modulo se importa por su nombre.
"""
