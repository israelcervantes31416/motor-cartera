"""app/v1: el read model de la superficie operacional, de solo lectura.

No es un motor. No decide, no predice, no optimiza, no planea ni rutea, y no define una metrica:
compone lo que los motores ya publicaron, para una pantalla, bajo un contexto operativo explicito
(el corte y cada ejecucion que la pantalla lee, por su identificador publico). Nada se elige como
"lo ultimo": lo que el contexto no declara no se lee, y una pantalla lo dice en lugar de buscar
otra ejecucion. Lo unico que cuenta en la base es la distribucion de las predicciones publicadas
de un scoring (resumen.py), para no bajarlas al navegador.

Lo que expone no lleva un CLIENTE_UNICO, un nombre, una direccion, un telefono, una coordenada, una
geometria ni un actor_ref: la cuenta es su cuenta_id, y los gestores y las bases, sus codigos.
"""

VERSION_APP = "app/v1"
