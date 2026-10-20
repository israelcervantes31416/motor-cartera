"""Lo que el codigo ya garantiza en un punto y el verificador de tipos no ve.

Una columna que admite NULL pero que una restriccion de la base, o el filtro de la consulta que la
leyo, garantiza; una opcion de la linea de comandos que una validacion anterior ya exigio. Sin el
valor, el error es del codigo y no de los datos: se dice, en lugar de seguir con un None. Solo
usa la biblioteca estandar, asi que tambien lo pueden usar los nucleos puros.
"""

from __future__ import annotations


def presente[T](valor: T | None, que: str) -> T:
    """`valor`, que en este punto no puede faltar; `que` lo nombra en el error si falta."""
    if valor is None:
        raise RuntimeError(f"Falta {que}: el codigo lo garantiza aqui.")
    return valor
