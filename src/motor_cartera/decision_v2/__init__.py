"""decision/v2: decidir cada cuenta de un corte con lo que se sabia en un instante, y explicarlo.

Responde que hacer ahora con cada cuenta usando solo la informacion que estaba disponible cuando se
decidio: el corte fija el universo, `as_of` el dia de negocio y `known_at` la frontera del
conocimiento. Separa tres versiones que cambian por su cuenta: la del motor (`decision/v2`), la de
las variables (`features-decision/v1`, en `features`) y la de las reglas
(`decision-policy/public-v1`, en `politica`). El calculo, en PostgreSQL y por conjuntos, esta en
`ejecuciones`; lo que lee la API, en `consultas`; la comparacion con decision/v1, en `sombra`.

No es un modelo predictivo: no hay entrenamiento, probabilidades ni recuperacion esperada. Es un
motor de reglas historico, point-in-time, determinista y explicable, paralelo a decision/v1.
"""
