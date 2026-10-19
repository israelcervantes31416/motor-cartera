"""Los tokens de acceso de OpenID Connect: un JWT firmado por el emisor, validado con su JWKS.

Que se revisa, en este orden, y que codigo de error sale de cada cosa:

1. La forma (tres partes, base64url, JSON): `TOKEN_MALFORMADO`.
2. El algoritmo de la cabecera, contra una lista cerrada de algoritmos asimetricos. `none` y HMAC
   nunca entran: `TOKEN_ALGORITMO_NO_PERMITIDO`. Con `MC_OIDC_TIPO_TOKEN`, tambien `typ`.
3. El `kid`: sin el, `TOKEN_SIN_KID`; si la JWKS no lo tiene ni despues de pedirla otra vez,
   `TOKEN_CLAVE_DESCONOCIDA`. Su llave tiene que ser de la familia del algoritmo.
4. La firma: `TOKEN_FIRMA_INVALIDA`.
5. Los reclamos: `exp` (obligatorio), `nbf` e `iat` si vienen, con la tolerancia declarada
   (`TOKEN_EXPIRADO`, `TOKEN_AUN_NO_VALIDO`); `iss` exacto (`TOKEN_EMISOR_INVALIDO`); `aud` con la
   audiencia de la API (`TOKEN_AUDIENCIA_INVALIDA`); `sub` (`TOKEN_SIN_SUJETO`); y `azp`, si se
   declararon los permitidos (`TOKEN_CLIENTE_NO_PERMITIDO`).

La JWKS se guarda en memoria. Se usa sin pedirla otra vez durante su TTL; un `kid` desconocido
obliga a pedirla (una rotacion), pero no mas de una vez por intervalo minimo. Si el emisor no
responde, se sigue usando la que se tenia hasta su antiguedad maxima; mas vieja, nada se valida y
la API responde 503 (`IdentidadNoDisponible`): cerrada, nunca abierta.
"""

from __future__ import annotations

import json
import logging
import ssl
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jwt
from jwt.exceptions import (
    DecodeError,
    ExpiredSignatureError,
    ImmatureSignatureError,
    InvalidAudienceError,
    InvalidIssuerError,
    InvalidKeyError,
    InvalidSignatureError,
    InvalidTokenError,
    MissingRequiredClaimError,
    PyJWKError,
)

log = logging.getLogger(__name__)

TAMANO_MAXIMO_JWKS = 1 << 20
"""Una JWKS de mas de 1 MiB no es una JWKS: no se lee entera."""

FAMILIAS = {
    "RS256": ("RSA",),
    "RS384": ("RSA",),
    "RS512": ("RSA",),
    "PS256": ("RSA",),
    "PS384": ("RSA",),
    "PS512": ("RSA",),
    "ES256": ("EC",),
    "ES384": ("EC",),
    "ES512": ("EC",),
    "EdDSA": ("OKP",),
}
"""El tipo de llave (`kty`) con que se verifica cada algoritmo."""


class TokenInvalido(Exception):
    """El token no prueba nada: 401. `codigo` es estable; el mensaje no dice nada del token."""

    def __init__(self, codigo: str, mensaje: str) -> None:
        super().__init__(mensaje)
        self.codigo = codigo


class IdentidadNoDisponible(Exception):
    """No se puede validar ningun token: el emisor no responde y la JWKS en memoria ya es demasiado
    vieja, o nunca se obtuvo. 503: no es culpa de quien pide, y no se deja pasar a nadie."""


@dataclass(frozen=True)
class ReglasDeToken:
    emisor: str
    audiencia: str
    algoritmos: tuple[str, ...]
    tolerancia_segundos: int = 30
    azp_permitidos: frozenset[str] = frozenset()
    tipo: str | None = None


def obtener_por_http(
    url: str, timeout: float, ca_file: Path | None = None
) -> bytes:  # pragma: no cover - lo prueban la prueba de humo y el compose
    """GET de la JWKS con su tiempo de espera y un tope de tamano. Con https, valida el certificado
    con las CA del sistema y, si se da, una CA adicional."""
    contexto = None
    if url.startswith("https://"):
        contexto = ssl.create_default_context()
        if ca_file is not None:
            contexto.load_verify_locations(cafile=str(ca_file))
    peticion = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(peticion, timeout=timeout, context=contexto) as respuesta:
        if respuesta.status != 200:
            raise OSError(f"la JWKS respondio {respuesta.status}")
        contenido = respuesta.read(TAMANO_MAXIMO_JWKS + 1)
    if len(contenido) > TAMANO_MAXIMO_JWKS:
        raise OSError("la JWKS excede el tamano maximo")
    return contenido


@dataclass(frozen=True)
class LlaveJwk:
    """Una llave publica de la JWKS: el objeto con que se verifica, su tipo y, si lo declara, su
    algoritmo."""

    llave: Any
    kty: str
    alg: str | None


def llaves_de(contenido: bytes) -> dict[str, LlaveJwk]:
    """Las llaves de firma de una JWKS, por su `kid`. Una llave sin `kid`, de cifrado (`use`
    distinto
    de `sig`), simetrica o que no se puede interpretar se ignora; una JWKS sin ninguna util es un
    error."""
    try:
        documento = json.loads(contenido)
    except (ValueError, UnicodeDecodeError):
        raise ValueError("la JWKS no es JSON") from None
    llaves: dict[str, LlaveJwk] = {}
    if not isinstance(documento, dict) or not isinstance(documento.get("keys"), list):
        raise ValueError("la JWKS no tiene una lista `keys`")
    for dato in documento["keys"]:
        if not isinstance(dato, dict):
            continue
        kid = dato.get("kid")
        if not isinstance(kid, str) or not kid or dato.get("use", "sig") != "sig":
            continue
        if dato.get("kty") not in ("RSA", "EC", "OKP"):
            continue
        alg = dato.get("alg")
        try:
            llaves[kid] = LlaveJwk(jwt.PyJWK(dato).key, dato["kty"], alg if alg else None)
        except (PyJWKError, InvalidKeyError, ValueError, TypeError):
            log.warning("JWKS: una llave no se pudo interpretar y se ignora")
    if not llaves:
        raise ValueError("la JWKS no tiene ninguna llave de firma util")
    return llaves


@dataclass
class CacheJwks:
    """La JWKS del emisor en memoria, con su TTL, su refresco por `kid` desconocido y su antiguedad
    maxima. Segura entre hilos: una sola peticion al emisor a la vez."""

    url: str
    ttl_segundos: float = 300
    antiguedad_maxima_segundos: float = 3600
    refresco_minimo_segundos: float = 30
    timeout_segundos: float = 3
    ca_file: Path | None = None
    obtener: Callable[[str, float], bytes] | None = None
    """Como se pide la JWKS (url, timeout) -> bytes. Por omision, HTTP; las pruebas pasan otra."""
    reloj: Callable[[], float] = time.monotonic
    al_refrescar: Callable[[str], None] | None = None
    """Se llama con `ok` o `fallido` en cada intento de pedir la JWKS: las metricas lo cuentan."""
    _llaves: dict[str, LlaveJwk] = field(default_factory=dict, init=False)
    _obtenida_en: float | None = field(default=None, init=False)
    _intentada_en: float | None = field(default=None, init=False)
    _candado: threading.Lock = field(default_factory=threading.Lock, init=False)

    def antiguedad(self) -> float | None:
        """Cuantos segundos tiene la JWKS en memoria, o None si nunca se obtuvo."""
        return None if self._obtenida_en is None else self.reloj() - self._obtenida_en

    def llave(self, kid: str) -> LlaveJwk:
        with self._candado:
            ahora = self.reloj()
            vigente = (
                self._obtenida_en is not None and ahora - self._obtenida_en < self.ttl_segundos
            )
            if not vigente:
                self._refrescar(ahora, obligatorio=True)
            elif kid not in self._llaves and self._puede_intentar(ahora):
                # Un kid nuevo con la JWKS vigente: el emisor pudo haber rotado su llave.
                self._refrescar(ahora, obligatorio=False)
            llave = self._llaves.get(kid)
        if llave is None:
            raise TokenInvalido(
                "TOKEN_CLAVE_DESCONOCIDA",
                "El token esta firmado con una llave que el emisor no publica.",
            )
        return llave

    def _puede_intentar(self, ahora: float) -> bool:
        return self._intentada_en is None or ahora - self._intentada_en >= (
            self.refresco_minimo_segundos
        )

    def _refrescar(self, ahora: float, *, obligatorio: bool) -> None:
        """Pide la JWKS. Si falla y era obligatorio (la que habia vencio su TTL), sigue con la vieja
        mientras no pase de su antiguedad maxima; despues, IdentidadNoDisponible."""
        if obligatorio and not self._puede_intentar(ahora) and self._obtenida_en is not None:
            # Se intento hace poco y fallo: no se martilla al emisor en cada peticion.
            self._exigir_antiguedad(ahora)
            return
        self._intentada_en = ahora
        try:
            obtener = self.obtener or (
                lambda url, timeout: obtener_por_http(url, timeout, self.ca_file)
            )
            llaves = llaves_de(obtener(self.url, self.timeout_segundos))
        except (OSError, ValueError, urllib.error.URLError) as error:
            self._avisar("fallido")
            log.warning("JWKS: no se pudo obtener (%s)", type(error).__name__)
            if obligatorio:
                self._exigir_antiguedad(ahora)
            return
        self._llaves = llaves
        self._obtenida_en = ahora
        self._avisar("ok")

    def _exigir_antiguedad(self, ahora: float) -> None:
        if (
            self._obtenida_en is None
            or ahora - self._obtenida_en >= self.antiguedad_maxima_segundos
        ):
            raise IdentidadNoDisponible(
                "El proveedor de identidad no responde y no hay llaves vigentes para validar."
            )

    def _avisar(self, resultado: str) -> None:
        if self.al_refrescar is not None:
            self.al_refrescar(resultado)


@dataclass
class ValidadorDeTokens:
    reglas: ReglasDeToken
    jwks: CacheJwks

    def validar(self, token: str) -> dict[str, Any]:
        """Los reclamos de un token valido, o TokenInvalido con su codigo. Puede levantar
        IdentidadNoDisponible."""
        try:
            cabecera = jwt.get_unverified_header(token)
        except DecodeError:
            raise TokenInvalido("TOKEN_MALFORMADO", "El token no es un JWT.") from None
        algoritmo = cabecera.get("alg")
        if algoritmo not in self.reglas.algoritmos or algoritmo not in FAMILIAS:
            raise TokenInvalido(
                "TOKEN_ALGORITMO_NO_PERMITIDO", "El token usa un algoritmo que la API no acepta."
            )
        if self.reglas.tipo is not None:
            tipo = str(cabecera.get("typ", "")).lower().removeprefix("application/")
            if tipo != self.reglas.tipo.lower():
                raise TokenInvalido("TOKEN_TIPO_INVALIDO", "El token no es un token de acceso.")
        kid = cabecera.get("kid")
        if not isinstance(kid, str) or not kid:
            raise TokenInvalido("TOKEN_SIN_KID", "El token no dice con que llave se firmo.")
        llave = self.jwks.llave(kid)
        if llave.kty not in FAMILIAS[algoritmo] or (llave.alg or algoritmo) != algoritmo:
            raise TokenInvalido(
                "TOKEN_ALGORITMO_NO_PERMITIDO", "El algoritmo del token no es el de su llave."
            )
        try:
            reclamos = jwt.decode(
                token,
                key=llave.llave,
                algorithms=[algoritmo],
                audience=self.reglas.audiencia,
                issuer=self.reglas.emisor,
                leeway=self.reglas.tolerancia_segundos,
                options={
                    "require": ["exp", "iss", "aud", "sub"],
                    "enforce_minimum_key_length": True,
                },
            )
        except ExpiredSignatureError:
            raise TokenInvalido("TOKEN_EXPIRADO", "El token vencio.") from None
        except ImmatureSignatureError:
            raise TokenInvalido("TOKEN_AUN_NO_VALIDO", "El token todavia no es valido.") from None
        except InvalidIssuerError:
            raise TokenInvalido("TOKEN_EMISOR_INVALIDO", "El token es de otro emisor.") from None
        except InvalidAudienceError:
            raise TokenInvalido(
                "TOKEN_AUDIENCIA_INVALIDA", "El token es para otra audiencia."
            ) from None
        except (InvalidSignatureError, InvalidKeyError):
            # Una llave que no cumple el largo minimo tampoco prueba la firma.
            raise TokenInvalido(
                "TOKEN_FIRMA_INVALIDA", "La firma del token no es valida."
            ) from None
        except MissingRequiredClaimError as error:
            codigo = "TOKEN_SIN_SUJETO" if error.claim == "sub" else "TOKEN_RECLAMO_FALTANTE"
            raise TokenInvalido(codigo, f"Al token le falta `{error.claim}`.") from None
        except InvalidTokenError:
            raise TokenInvalido("TOKEN_MALFORMADO", "El token no es valido.") from None
        if not isinstance(reclamos.get("sub"), str) or not reclamos["sub"]:
            raise TokenInvalido("TOKEN_SIN_SUJETO", "Al token le falta `sub`.")
        if self.reglas.azp_permitidos and reclamos.get("azp") not in self.reglas.azp_permitidos:
            raise TokenInvalido(
                "TOKEN_CLIENTE_NO_PERMITIDO", "El token se emitio para un cliente no permitido."
            )
        return reclamos
