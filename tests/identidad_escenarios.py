"""Un emisor de OpenID Connect de mentira para las pruebas, con criptografia de verdad.

Genera llaves RSA y EC, publica su JWKS y firma tokens como lo haria un IdP: cada token de estas
pruebas es un JWT valido criptograficamente (o invalido a proposito, en una sola cosa). No hay
`Bearer abc123`. La JWKS se entrega sin red: la cache de la API la pide por la funcion `obtener`.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from jwt.algorithms import ECAlgorithm, RSAAlgorithm

EMISOR = "https://idp.motor-cartera.invalid/dex"
AUDIENCIA = "motor-cartera-api"
CLIENTE_WEB = "motor-cartera-web"
JWKS_URL = "https://idp.motor-cartera.invalid/dex/keys"
GRUPOS = "mc-lectores=VIEWER;mc-auditores=AUDITOR;mc-operadores=OPERATOR;mc-administradores=ADMIN"
SECRETO_GATEWAY = "secreto-del-gateway-de-las-pruebas-0123456789"
CLAVE_AUDITORIA = "clave-de-los-seudonimos-de-auditoria-0123456789"


@dataclass
class Emisor:
    """Las llaves privadas del IdP de prueba, por kid, y cuales publica en su JWKS."""

    privadas: dict[str, Any] = field(default_factory=dict)
    publicadas: set[str] = field(default_factory=set)
    peticiones: int = 0
    caido: bool = False

    def nueva_rsa(self, kid: str, bits: int = 2048, publicar: bool = True) -> None:
        self.privadas[kid] = rsa.generate_private_key(public_exponent=65537, key_size=bits)
        if publicar:
            self.publicadas.add(kid)

    def nueva_ec(self, kid: str, publicar: bool = True) -> None:
        self.privadas[kid] = ec.generate_private_key(ec.SECP256R1())
        if publicar:
            self.publicadas.add(kid)

    def jwks(self) -> bytes:
        llaves = []
        for kid in sorted(self.publicadas):
            privada = self.privadas[kid]
            if isinstance(privada, rsa.RSAPrivateKey):
                dato = RSAAlgorithm.to_jwk(privada.public_key(), as_dict=True)
            else:
                dato = ECAlgorithm.to_jwk(privada.public_key(), as_dict=True)
            dato.update({"kid": kid, "use": "sig"})
            llaves.append(dato)
        return json.dumps({"keys": llaves}).encode()

    def obtener(self, url: str, timeout: float) -> bytes:
        """Lo que la cache de la API usa para pedir la JWKS: cuenta las peticiones y puede caer."""
        self.peticiones += 1
        if self.caido:
            raise OSError("el IdP de prueba esta caido")
        assert url == JWKS_URL
        return self.jwks()

    def token(
        self,
        kid: str = "llave-1",
        *,
        algoritmo: str = "RS256",
        cabecera: dict[str, Any] | None = None,
        **cambios: Any,
    ) -> str:
        reclamos = reclamos_validos()
        for llave, valor in cambios.items():
            if valor is None:
                reclamos.pop(llave, None)
            else:
                reclamos[llave] = valor
        encabezados = {"kid": kid, **(cabecera or {})}
        return jwt.encode(reclamos, self.privadas[kid], algorithm=algoritmo, headers=encabezados)


def reclamos_validos() -> dict[str, Any]:
    ahora = int(time.time())
    return {
        "iss": EMISOR,
        "aud": [AUDIENCIA, CLIENTE_WEB],
        "azp": CLIENTE_WEB,
        "sub": "CiQxMTExMTExMS0xMTExLTQxMTEtODExMS0xMTExMTExMTExMTESBWxvY2Fs",
        "email": "lector@motor-cartera.test",
        "groups": ["mc-lectores"],
        "iat": ahora,
        "exp": ahora + 600,
    }


def sin_firma(reclamos: dict[str, Any] | None = None, kid: str = "llave-1") -> str:
    """Un token con `alg: none`: cabecera y cuerpo, sin firma."""
    import base64

    def b64(dato: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(dato).encode()).rstrip(b"=").decode()

    return (
        f"{b64({'alg': 'none', 'typ': 'JWT', 'kid': kid})}.{b64(reclamos or reclamos_validos())}."
    )
