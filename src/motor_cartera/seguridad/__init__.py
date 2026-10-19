"""Quien hace cada peticion y que puede hacer.

- `permisos`: los cuatro permisos (`mc.read`, `mc.audit`, `mc.execute`, `mc.configure`), los roles
  como nombres de conjuntos de permisos, el mapa de los grupos del IdP y el principal de una
  peticion.
- `tokens`: la validacion de los tokens de acceso de OpenID Connect (JWT) con la JWKS del emisor.
- `credenciales`: las claves de los servicios y los secretos del gateway.

Este paquete no sabe de FastAPI: lo usa `motor_cartera.api.autorizacion`.
"""
