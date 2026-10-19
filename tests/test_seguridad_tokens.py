"""La validacion de los tokens de acceso: cada forma de un token que no debe entrar, con su codigo,
y la cache de la JWKS ante una rotacion, un kid desconocido y un IdP caido. Sin base ni red: el
emisor de prueba firma con llaves de verdad y entrega su JWKS a la cache."""

from __future__ import annotations

import time

import jwt
import pytest
from identidad_escenarios import (
    AUDIENCIA,
    CLIENTE_WEB,
    EMISOR,
    JWKS_URL,
    Emisor,
    reclamos_validos,
    sin_firma,
)

from motor_cartera.seguridad.tokens import (
    CacheJwks,
    IdentidadNoDisponible,
    ReglasDeToken,
    TokenInvalido,
    ValidadorDeTokens,
    llaves_de,
)


class Reloj:
    def __init__(self) -> None:
        self.ahora = 1_000.0

    def __call__(self) -> float:
        return self.ahora


@pytest.fixture(scope="module")
def llaves() -> Emisor:
    emisor = Emisor()
    emisor.nueva_rsa("llave-1")
    emisor.nueva_rsa("llave-2", publicar=False)
    emisor.nueva_rsa("corta", bits=1024)
    emisor.nueva_ec("eliptica")
    emisor.nueva_rsa("de-otro-emisor", publicar=False)
    return emisor


@pytest.fixture
def emisor(llaves) -> Emisor:
    """El mismo juego de llaves, con su estado (publicadas, peticiones, caido) nuevo en cada
    prueba."""
    return Emisor(
        privadas=llaves.privadas, publicadas={"llave-1", "corta", "eliptica"}, peticiones=0
    )


@pytest.fixture
def reloj() -> Reloj:
    return Reloj()


def _validador(emisor: Emisor, reloj: Reloj, **reglas) -> ValidadorDeTokens:
    cache = CacheJwks(
        url=JWKS_URL,
        ttl_segundos=300,
        antiguedad_maxima_segundos=3600,
        refresco_minimo_segundos=30,
        obtener=emisor.obtener,
        reloj=reloj,
    )
    base = {
        "emisor": EMISOR,
        "audiencia": AUDIENCIA,
        "algoritmos": ("RS256", "ES256"),
        "tolerancia_segundos": 30,
    }
    return ValidadorDeTokens(ReglasDeToken(**{**base, **reglas}), cache)


def _codigo(validador: ValidadorDeTokens, token: str) -> str:
    with pytest.raises(TokenInvalido) as error:
        validador.validar(token)
    return error.value.codigo


# --- lo que entra ---------------------------------------------------------------------------------


def test_un_token_valido_entra_con_sus_reclamos(emisor, reloj):
    reclamos = _validador(emisor, reloj).validar(emisor.token())

    assert reclamos["iss"] == EMISOR and AUDIENCIA in reclamos["aud"]
    assert reclamos["groups"] == ["mc-lectores"]


def test_una_llave_eliptica_publicada_tambien_valida(emisor, reloj):
    token = emisor.token("eliptica", algoritmo="ES256")

    assert _validador(emisor, reloj).validar(token)["sub"]


def test_un_token_vencido_dentro_de_la_tolerancia_entra(emisor, reloj):
    token = emisor.token(exp=int(time.time()) - 10)

    assert _validador(emisor, reloj, tolerancia_segundos=30).validar(token)


# --- lo que no entra ------------------------------------------------------------------------------


def test_alg_none_no_entra(emisor, reloj):
    assert _codigo(_validador(emisor, reloj), sin_firma()) == "TOKEN_ALGORITMO_NO_PERMITIDO"


def test_hs256_con_la_llave_publica_como_secreto_no_entra(emisor, reloj):
    """La confusion de algoritmos: firmar con HMAC usando la llave publica, que cualquiera tiene. Se
    arma a mano: PyJWT mismo se niega a firmar asi."""
    import base64
    import hashlib
    import hmac
    import json

    from cryptography.hazmat.primitives import serialization

    def b64(dato: bytes) -> str:
        return base64.urlsafe_b64encode(dato).rstrip(b"=").decode()

    publica = (
        emisor.privadas["llave-1"]
        .public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    )
    firmado = (
        b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": "llave-1"}).encode())
        + "."
        + b64(json.dumps(reclamos_validos()).encode())
    )
    firma = hmac.new(publica, firmado.encode(), hashlib.sha256).digest()
    token = f"{firmado}.{b64(firma)}"

    assert _codigo(_validador(emisor, reloj), token) == "TOKEN_ALGORITMO_NO_PERMITIDO"


def test_un_algoritmo_fuera_de_la_lista_no_entra(emisor, reloj):
    token = emisor.token(algoritmo="RS512")

    assert _codigo(_validador(emisor, reloj), token) == "TOKEN_ALGORITMO_NO_PERMITIDO"


def test_el_algoritmo_tiene_que_ser_el_de_la_familia_de_su_llave(emisor, reloj):
    """Firmado con la llave eliptica (ES256, permitido), pero con el kid de una llave RSA."""
    otra = jwt.encode(
        reclamos_validos(),
        emisor.privadas["eliptica"],
        algorithm="ES256",
        headers={"kid": "llave-1"},
    )

    assert _codigo(_validador(emisor, reloj), otra) == "TOKEN_ALGORITMO_NO_PERMITIDO"


def test_un_token_vencido_no_entra(emisor, reloj):
    token = emisor.token(exp=int(time.time()) - 120)

    assert _codigo(_validador(emisor, reloj), token) == "TOKEN_EXPIRADO"


def test_un_token_sin_exp_no_entra(emisor, reloj):
    assert _codigo(_validador(emisor, reloj), emisor.token(exp=None)) == "TOKEN_RECLAMO_FALTANTE"


def test_un_token_que_todavia_no_vale_no_entra(emisor, reloj):
    token = emisor.token(nbf=int(time.time()) + 300)

    assert _codigo(_validador(emisor, reloj), token) == "TOKEN_AUN_NO_VALIDO"


def test_un_token_emitido_en_el_futuro_no_entra(emisor, reloj):
    token = emisor.token(iat=int(time.time()) + 300)

    assert _codigo(_validador(emisor, reloj), token) == "TOKEN_AUN_NO_VALIDO"


def test_otro_emisor_no_entra(emisor, reloj):
    token = emisor.token(iss="https://otro.invalid/dex")

    assert _codigo(_validador(emisor, reloj), token) == "TOKEN_EMISOR_INVALIDO"


def test_otra_audiencia_no_entra(emisor, reloj):
    token = emisor.token(aud=["otra-api"])

    assert _codigo(_validador(emisor, reloj), token) == "TOKEN_AUDIENCIA_INVALIDA"


def test_sin_sujeto_no_entra(emisor, reloj):
    assert _codigo(_validador(emisor, reloj), emisor.token(sub=None)) == "TOKEN_SIN_SUJETO"


def test_una_firma_alterada_no_entra(emisor, reloj):
    cabecera, cuerpo, firma = emisor.token().split(".")
    alterada = f"{cabecera}.{cuerpo}.{firma[:-4]}AAAA"

    assert _codigo(_validador(emisor, reloj), alterada) == "TOKEN_FIRMA_INVALIDA"


def test_un_cuerpo_alterado_no_entra(emisor, reloj):
    import base64
    import json

    cabecera, cuerpo, firma = emisor.token().split(".")
    reclamos = json.loads(base64.urlsafe_b64decode(cuerpo + "=" * (-len(cuerpo) % 4)))
    reclamos["groups"] = ["mc-administradores"]
    nuevo = base64.urlsafe_b64encode(json.dumps(reclamos).encode()).rstrip(b"=").decode()

    assert (
        _codigo(_validador(emisor, reloj), f"{cabecera}.{nuevo}.{firma}") == "TOKEN_FIRMA_INVALIDA"
    )


def test_firmado_con_una_llave_que_el_emisor_no_publica_no_entra(emisor, reloj):
    token = emisor.token("de-otro-emisor")

    assert _codigo(_validador(emisor, reloj), token) == "TOKEN_CLAVE_DESCONOCIDA"


def test_un_kid_publicado_con_otra_llave_no_entra(emisor, reloj):
    """El atacante firma con su llave y dice el kid de una llave publicada."""
    token = emisor.token("de-otro-emisor", cabecera={"kid": "llave-1"})

    assert _codigo(_validador(emisor, reloj), token) == "TOKEN_FIRMA_INVALIDA"


def test_sin_kid_no_entra(emisor, reloj):
    token = jwt.encode(reclamos_validos(), emisor.privadas["llave-1"], algorithm="RS256")

    assert _codigo(_validador(emisor, reloj), token) == "TOKEN_SIN_KID"


def test_una_llave_rsa_corta_no_prueba_la_firma(emisor, reloj):
    assert _codigo(_validador(emisor, reloj), emisor.token("corta")) == "TOKEN_FIRMA_INVALIDA"


@pytest.mark.parametrize("basura", ["", "abc123", "a.b.c", "eyJhbGciOiJSUzI1NiJ9..", "x" * 50])
def test_lo_que_no_es_un_jwt_no_entra(emisor, reloj, basura):
    assert _codigo(_validador(emisor, reloj), basura) in {
        "TOKEN_MALFORMADO",
        "TOKEN_ALGORITMO_NO_PERMITIDO",
        "TOKEN_SIN_KID",
    }


def test_con_azp_declarados_otro_cliente_no_entra(emisor, reloj):
    validador = _validador(emisor, reloj, azp_permitidos=frozenset({CLIENTE_WEB}))

    assert validador.validar(emisor.token())
    assert _codigo(validador, emisor.token(azp="otro-cliente")) == "TOKEN_CLIENTE_NO_PERMITIDO"


def test_con_tipo_declarado_un_id_token_no_entra(emisor, reloj):
    validador = _validador(emisor, reloj, tipo="at+jwt")

    assert validador.validar(emisor.token(cabecera={"typ": "at+jwt"}))
    assert validador.validar(emisor.token(cabecera={"typ": "application/at+jwt"}))
    assert _codigo(validador, emisor.token(cabecera={"typ": "JWT"})) == "TOKEN_TIPO_INVALIDO"


# --- la cache de la JWKS --------------------------------------------------------------------------


def test_la_jwks_se_pide_una_vez_por_ttl(emisor, reloj):
    validador = _validador(emisor, reloj)
    for _ in range(5):
        validador.validar(emisor.token())
    assert emisor.peticiones == 1

    reloj.ahora += 301
    validador.validar(emisor.token())
    assert emisor.peticiones == 2


def test_una_rotacion_de_llaves_se_ve_al_primer_kid_nuevo(emisor, reloj):
    validador = _validador(emisor, reloj)
    validador.validar(emisor.token())
    emisor.publicadas.add("llave-2")  # el IdP rota: publica la nueva

    reloj.ahora += 31  # pasado el intervalo minimo, dentro del TTL
    assert validador.validar(emisor.token("llave-2"))
    assert emisor.peticiones == 2


def test_una_llave_retirada_deja_de_valer_al_refrescar(emisor, reloj):
    validador = _validador(emisor, reloj)
    validador.validar(emisor.token())
    emisor.publicadas.discard("llave-1")

    reloj.ahora += 301
    assert _codigo(validador, emisor.token()) == "TOKEN_CLAVE_DESCONOCIDA"


def test_un_kid_desconocido_no_martilla_al_emisor(emisor, reloj):
    validador = _validador(emisor, reloj)
    validador.validar(emisor.token())
    reloj.ahora += 31
    for _ in range(20):
        assert _codigo(validador, emisor.token("de-otro-emisor")) == "TOKEN_CLAVE_DESCONOCIDA"

    # Un solo refresco por intervalo minimo, no uno por peticion.
    assert emisor.peticiones == 2


def test_con_el_emisor_caido_sigue_validando_con_la_jwks_que_tenia(emisor, reloj):
    validador = _validador(emisor, reloj)
    validador.validar(emisor.token())
    emisor.caido = True

    reloj.ahora += 600  # vencio el TTL, no la antiguedad maxima
    assert validador.validar(emisor.token())


def test_con_el_emisor_caido_y_la_jwks_vieja_falla_cerrado(emisor, reloj):
    validador = _validador(emisor, reloj)
    validador.validar(emisor.token())
    emisor.caido = True

    reloj.ahora += 3601
    with pytest.raises(IdentidadNoDisponible):
        validador.validar(emisor.token())


def test_sin_jwks_y_con_el_emisor_caido_falla_cerrado(emisor, reloj):
    emisor.caido = True

    with pytest.raises(IdentidadNoDisponible):
        _validador(emisor, reloj).validar(emisor.token())


def test_el_emisor_caido_no_se_pide_en_cada_peticion(emisor, reloj):
    validador = _validador(emisor, reloj)
    validador.validar(emisor.token())
    emisor.caido = True
    reloj.ahora += 301
    for _ in range(10):
        validador.validar(emisor.token())

    assert emisor.peticiones == 2


def test_una_jwks_rota_o_sin_llaves_utiles_es_un_error():
    for contenido in (
        b"no es json",
        b"{}",
        b'{"keys": []}',
        b'{"keys": [{"kty": "oct", "kid": "x"}]}',
    ):
        with pytest.raises(ValueError):
            llaves_de(contenido)


def test_las_llaves_de_cifrado_y_sin_kid_se_ignoran(emisor):
    import json

    documento = json.loads(emisor.jwks())
    primera = documento["keys"][0]
    documento["keys"] = [
        {**primera, "use": "enc", "kid": "cifrado"},
        {k: v for k, v in primera.items() if k != "kid"},
        primera,
    ]

    assert set(llaves_de(json.dumps(documento).encode())) == {primera["kid"]}
