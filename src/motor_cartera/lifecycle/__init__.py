"""El lifecycle de cobranza: lo que la cobranza hizo con cada cuenta, como eventos operacionales.

Hasta v0.8 Motor Cartera sabia que datos llegaron, que le paso a cada cuenta a traves de sus cortes
y que movimientos economicos se interpretan de sus pagos. El lifecycle agrega la tercera verdad, sin
mezclarla con las otras dos:

    FUENTE        lo que dicen CARTERA y PAGOS, tal como llegaron (y lo que un corte observa de la
                  promesa o del plan de una cuenta, que sigue siendo una observacion de la fuente)
    OPERACIONAL   lo que la cobranza hizo: gestiones, contactos, promesas, convenios y visitas, como
                  eventos que registra Motor Cartera, con su momento de negocio y su momento de
                  registro
    ECONOMICO     los movimientos economicos canonicos que interpreta el motor de pagos

Ninguna es una tercera fuente oficial del acreedor, y nada aqui se fabrica desde un snapshot.

- `reglas`: el vocabulario de lifecycle/v1 y sus reglas de coherencia, sin base.

No reexporta nada: cada modulo se importa por su nombre.
"""
