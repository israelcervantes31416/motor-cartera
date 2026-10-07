"""El modelo historico: que le paso a cada cuenta a traves del tiempo, y la Cuenta 360.

Hasta v0.6 una corrida era una fotografia. Este paquete agrega el tiempo, sin tocar las capas que ya
existen:

    SOURCE-CONFORMED     los Parquet de cartera/v2 (93 columnas) y pagos/v1 (23), completos
            |
    HISTORICO            identidad (CuentaCanonica), un corte por fecha (CorteCanonico), las
            |            variables historicas de cada cuenta en cada corte (SnapshotCuenta) y cada
            |            movimiento de pagos tal como llego (PagoObservado)
    OPERACIONAL          Cuenta, la foto de una corrida que leen los motores v1, que sigue igual

El historico se materializa desde el dataset conformado, nunca desde el archivo original, y en
paralelo al flujo operacional: ningun motor v1 lo espera ni lo lee.

- `identidad`: los identificadores publicos deterministas (UUID version 5).
- `carga`: del Parquet conformado a PostgreSQL, por lotes y con COPY.
- `ejecuciones`: la materializacion versionada (historia/v1), todo o nada, y su trabajo HISTORIA.
- `presencia`: primera observacion, salida, reingreso y continuidad, calculados al consultar.
- `backfill`: encolar la historia de los datasets que todavia no la tienen.
- `cuenta360`: lo que la API responde de una cuenta, de su historia y de sus pagos observados.

No reexporta nada: todo aqui usa la base, y cada modulo se importa por su nombre.
"""
