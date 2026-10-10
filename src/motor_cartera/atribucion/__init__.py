"""La atribucion operativa: con que gestiones se asocia cada movimiento economico, y por que.

    MovimientoEconomicoCanonico   un PAGO de la interpretacion vigente del motor de pagos
         |
    EjecucionAtribucion           atribucion/v1 sobre una ventana: un despacho, una cartera y un mes
         |                        de recepcion, con su ventana hacia atras y su zona horaria
    AtribucionMovimiento          que concluyo de cada PAGO: SIN_GESTION_CANDIDATA,
         |                        ASOCIACION_UNICA o AMBIGUA
    CandidatoAtribucion           cada gestion candidata, en una relacion

Es asociacion operacional, no causalidad: dice que gestiones con contacto de la misma cuenta
ocurrieron antes de un pago, no que alguna lo haya producido. Con varias candidatas no elige.

- `reglas`: el nucleo puro y legible de atribucion/v1.
- `ejecuciones`: la atribucion de una ventana en PostgreSQL, por conjuntos y todo o nada, con su
  trabajo ATRIBUCION.
- `backfill`: las ventanas sin una atribucion al dia (`motor-cartera backfill-atribucion`).
- `consultas`: lo que la API lee de las ejecuciones, de sus resultados y de sus candidatas.

No reexporta nada: cada modulo se importa por su nombre.
"""
