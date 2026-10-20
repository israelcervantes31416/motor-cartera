"""La autenticacion y la autorizacion de la API.

Una sola dependencia global, `autorizar`, corre en cada ruta. Busca la ruta (su metodo y su
plantilla) en la matriz de autorizacion, que clasifica cada operacion de la API en una clase:

- `PUBLIC`: sin credencial. Solo la salud del proceso.
- `READ`: exige `mc.read`. Lo publicado por los motores y la app.
- `AUDIT`: exige `mc.audit`. La evidencia cruda (registros rechazados, archivos fuente,
observaciones
  de pagos), los trabajos de la cola y el linaje.
- `EXECUTE`: exige `mc.execute`. Todo lo que pide trabajo o registra un evento.
- `CONFIGURE`: exige `mc.configure`. Lo que publica configuracion operacional.

Una ruta que no esta en la matriz impide que la API arranque (`verificar_matriz`), y si aun asi se
agregara despues, se niega: nada queda autorizado por omision. La matriz entera, como tabla, esta en
docs/matriz-autorizacion.md, generada de aqui.

Quien pide se autentica con una de dos credenciales, nunca las dos:

- `Authorization: Bearer <token>`: una persona, con el token de acceso de OpenID Connect que el
  gateway (el edge) obtuvo con su sesion. En produccion vale solo si llega con la credencial del
  gateway (`X-MC-Gateway`): un token robado, presentado directo a la API, no entra.
- `X-API-Key: <clave>`: un servicio interno, con los permisos de su clave. La clave de desarrollo
  (`MC_API_KEY`) es una de ellas, con todos los permisos, y solo existe fuera de produccion.

Ninguna cabecera de identidad (X-Forwarded-User, X-MC-Subject, X-Auth-Request-*...) cuenta: la API
solo cree un token firmado o una clave.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from fastapi import FastAPI, Request
from fastapi.routing import APIRoute, iter_route_contexts

from motor_cartera.api.errores import ErrorDeApi
from motor_cartera.config import Config
from motor_cartera.seguridad.credenciales import (
    ClaveDeServicio,
    buscar_clave,
    digest_de,
    gateway_valido,
    leer_claves,
    leer_secretos_gateway,
)
from motor_cartera.seguridad.permisos import (
    Permiso,
    Principal,
    mapa_de_grupos,
    permisos_de_reclamos,
    seudonimo,
)
from motor_cartera.seguridad.tokens import (
    CacheJwks,
    IdentidadNoDisponible,
    ReglasDeToken,
    TokenInvalido,
    ValidadorDeTokens,
)

log = logging.getLogger(__name__)

CABECERA_API_KEY = "X-API-Key"
CABECERA_GATEWAY = "X-MC-Gateway"
REINO = "motor-cartera"


class Clase(StrEnum):
    PUBLICA = "PUBLIC"
    LECTURA = "READ"
    AUDITORIA = "AUDIT"
    EJECUCION = "EXECUTE"
    CONFIGURACION = "CONFIGURE"


PERMISO_DE: dict[Clase, Permiso] = {
    Clase.LECTURA: Permiso.LEER,
    Clase.AUDITORIA: Permiso.AUDITAR,
    Clase.EJECUCION: Permiso.EJECUTAR,
    Clase.CONFIGURACION: Permiso.CONFIGURAR,
}

MATRIZ: dict[tuple[str, str], Clase] = {
    ("GET", "/analytics/catalogo"): Clase.LECTURA,
    ("GET", "/analytics/ejecuciones"): Clase.LECTURA,
    ("POST", "/analytics/ejecuciones"): Clase.EJECUCION,
    ("GET", "/analytics/ejecuciones/{analytics_run_id}"): Clase.LECTURA,
    ("GET", "/analytics/ejecuciones/{analytics_run_id}/agregados"): Clase.LECTURA,
    ("GET", "/analytics/ejecuciones/{analytics_run_id}/controles"): Clase.LECTURA,
    ("GET", "/analytics/ejecuciones/{analytics_run_id}/embudos"): Clase.LECTURA,
    ("GET", "/analytics/ejecuciones/{analytics_run_id}/resumen"): Clase.LECTURA,
    ("GET", "/app/v1/contextos/opciones"): Clase.LECTURA,
    ("GET", "/app/v1/contextos/validar"): Clase.LECTURA,
    ("GET", "/app/v1/cuentas"): Clase.LECTURA,
    ("GET", "/app/v1/cuentas/{cuenta_id}"): Clase.LECTURA,
    ("GET", "/app/v1/cuentas/{cuenta_id}/decision"): Clase.LECTURA,
    ("GET", "/app/v1/cuentas/{cuenta_id}/gestiones"): Clase.LECTURA,
    ("GET", "/app/v1/cuentas/{cuenta_id}/historia"): Clase.LECTURA,
    ("GET", "/app/v1/cuentas/{cuenta_id}/linea-de-tiempo"): Clase.LECTURA,
    ("GET", "/app/v1/cuentas/{cuenta_id}/pagos"): Clase.LECTURA,
    ("GET", "/app/v1/linaje"): Clase.AUDITORIA,
    ("GET", "/app/v1/predictivo/scorings/{scoring_run_id}/distribucion"): Clase.LECTURA,
    ("GET", "/app/v1/resumen"): Clase.LECTURA,
    ("GET", "/app/v1/ruteo/rutas/{ruta_id}/paradas"): Clase.LECTURA,
    ("GET", "/app/v1/sesion"): Clase.LECTURA,
    ("GET", "/atribuciones"): Clase.LECTURA,
    ("POST", "/atribuciones"): Clase.EJECUCION,
    ("GET", "/atribuciones/{atribucion_run_id}"): Clase.LECTURA,
    ("GET", "/atribuciones/{atribucion_run_id}/resultados"): Clase.LECTURA,
    ("GET", "/bases/versiones"): Clase.LECTURA,
    ("POST", "/bases/versiones"): Clase.CONFIGURACION,
    ("GET", "/bases/versiones/{bases_version_id}"): Clase.LECTURA,
    ("GET", "/campo-v2/planes"): Clase.LECTURA,
    ("POST", "/campo-v2/planes"): Clase.EJECUCION,
    ("GET", "/campo-v2/planes/{campo_run_id}"): Clase.LECTURA,
    ("GET", "/campo-v2/planes/{campo_run_id}/asignaciones"): Clase.LECTURA,
    ("GET", "/campo-v2/planes/{campo_run_id}/cargas"): Clase.LECTURA,
    ("GET", "/campo-v2/planes/{campo_run_id}/unidades"): Clase.LECTURA,
    ("GET", "/campo/configuraciones"): Clase.LECTURA,
    ("POST", "/campo/configuraciones"): Clase.CONFIGURACION,
    ("GET", "/campo/configuraciones/{configuracion_campo_version_id}"): Clase.LECTURA,
    ("GET", "/cartera/cortes"): Clase.LECTURA,
    ("GET", "/cartera/cortes/{corte_id}"): Clase.LECTURA,
    ("GET", "/cartera/cortes/{corte_id}/decisiones-v2"): Clase.LECTURA,
    ("GET", "/cartera/resumen"): Clase.LECTURA,
    ("GET", "/convenios/{convenio_id}"): Clase.LECTURA,
    ("POST", "/convenios/{convenio_id}/cancelaciones"): Clase.EJECUCION,
    ("POST", "/corridas"): Clase.EJECUCION,
    ("GET", "/corridas/{run_id}"): Clase.LECTURA,
    ("GET", "/corridas/{run_id}/decisiones"): Clase.LECTURA,
    ("POST", "/corridas/{run_id}/decisiones"): Clase.EJECUCION,
    ("GET", "/corridas/{run_id}/flujo"): Clase.LECTURA,
    ("GET", "/corridas/{run_id}/fuente"): Clase.AUDITORIA,
    ("GET", "/corridas/{run_id}/historia"): Clase.LECTURA,
    ("GET", "/corridas/{run_id}/rechazos"): Clase.AUDITORIA,
    ("GET", "/cuentas"): Clase.LECTURA,
    ("GET", "/cuentas/{cuenta_id}"): Clase.LECTURA,
    ("GET", "/cuentas/{cuenta_id}/atribuciones"): Clase.LECTURA,
    ("GET", "/cuentas/{cuenta_id}/campo-v2"): Clase.LECTURA,
    ("GET", "/cuentas/{cuenta_id}/convenios"): Clase.LECTURA,
    ("GET", "/cuentas/{cuenta_id}/decisiones-v2"): Clase.LECTURA,
    ("GET", "/cuentas/{cuenta_id}/eventos"): Clase.LECTURA,
    ("GET", "/cuentas/{cuenta_id}/geografia"): Clase.LECTURA,
    ("GET", "/cuentas/{cuenta_id}/gestiones"): Clase.LECTURA,
    ("POST", "/cuentas/{cuenta_id}/gestiones"): Clase.EJECUCION,
    ("GET", "/cuentas/{cuenta_id}/historia"): Clase.LECTURA,
    ("GET", "/cuentas/{cuenta_id}/lifecycle"): Clase.LECTURA,
    ("GET", "/cuentas/{cuenta_id}/movimientos"): Clase.LECTURA,
    ("GET", "/cuentas/{cuenta_id}/optimizacion"): Clase.LECTURA,
    ("GET", "/cuentas/{cuenta_id}/pagos-observados"): Clase.LECTURA,
    ("GET", "/cuentas/{cuenta_id}/predictivo"): Clase.LECTURA,
    ("GET", "/cuentas/{cuenta_id}/promesas"): Clase.LECTURA,
    ("GET", "/cuentas/{cuenta_id}/ruteo-v2"): Clase.LECTURA,
    ("GET", "/cuentas/{cuenta_id}/territorial-v2"): Clase.LECTURA,
    ("POST", "/decisiones-v2"): Clase.EJECUCION,
    ("GET", "/decisiones-v2/{decision_run_id}"): Clase.LECTURA,
    ("GET", "/decisiones-v2/{decision_run_id}/resultados"): Clase.LECTURA,
    ("GET", "/decisiones-v2/{decision_run_id}/resultados/{cuenta_id}"): Clase.LECTURA,
    ("GET", "/decisiones/{decision_run_id}"): Clase.LECTURA,
    ("GET", "/decisiones/{decision_run_id}/cuentas"): Clase.LECTURA,
    ("GET", "/decisiones/{decision_run_id}/territoriales"): Clase.LECTURA,
    ("POST", "/decisiones/{decision_run_id}/territoriales"): Clase.EJECUCION,
    ("GET", "/evaluaciones-promesas"): Clase.LECTURA,
    ("POST", "/evaluaciones-promesas"): Clase.EJECUCION,
    ("GET", "/evaluaciones-promesas/{evaluacion_run_id}"): Clase.LECTURA,
    ("GET", "/evaluaciones-promesas/{evaluacion_run_id}/promesas"): Clase.LECTURA,
    ("GET", "/flujos/{flujo_id}"): Clase.LECTURA,
    ("POST", "/flujos/{flujo_id}/reanudar"): Clase.EJECUCION,
    ("GET", "/flujos/{flujo_id}/trabajos"): Clase.AUDITORIA,
    ("GET", "/geografia/ejecuciones"): Clase.LECTURA,
    ("POST", "/geografia/ejecuciones"): Clase.EJECUCION,
    ("GET", "/geografia/ejecuciones/{geografia_run_id}"): Clase.LECTURA,
    ("GET", "/geografia/ejecuciones/{geografia_run_id}/resultados"): Clase.LECTURA,
    ("GET", "/geografia/versiones"): Clase.LECTURA,
    ("GET", "/geografia/versiones/{geografia_version_id}"): Clase.LECTURA,
    ("GET", "/gestiones/{gestion_id}"): Clase.LECTURA,
    ("POST", "/gestiones/{gestion_id}/anulaciones"): Clase.EJECUCION,
    ("POST", "/gestiones/{gestion_id}/convenios"): Clase.EJECUCION,
    ("POST", "/gestiones/{gestion_id}/promesas"): Clase.EJECUCION,
    ("GET", "/health/live"): Clase.PUBLICA,
    ("GET", "/health/ready"): Clase.PUBLICA,
    ("GET", "/historias/{historia_run_id}"): Clase.LECTURA,
    ("GET", "/motor-pagos"): Clase.LECTURA,
    ("GET", "/motor-pagos/{motor_pagos_run_id}"): Clase.LECTURA,
    ("GET", "/motor-pagos/{motor_pagos_run_id}/resultados"): Clase.LECTURA,
    ("GET", "/movimientos"): Clase.LECTURA,
    ("GET", "/movimientos/{movimiento_id}"): Clase.LECTURA,
    ("GET", "/movimientos/{movimiento_id}/atribuciones"): Clase.LECTURA,
    ("GET", "/movimientos/{movimiento_id}/observaciones"): Clase.AUDITORIA,
    ("GET", "/optimizacion/configuraciones"): Clase.LECTURA,
    ("POST", "/optimizacion/configuraciones"): Clase.CONFIGURACION,
    ("GET", "/optimizacion/configuraciones/{configuracion_id}"): Clase.LECTURA,
    ("POST", "/optimizacion/configuraciones/{configuracion_id}/publicacion"): Clase.CONFIGURACION,
    ("GET", "/optimizacion/ejecuciones"): Clase.LECTURA,
    ("POST", "/optimizacion/ejecuciones"): Clase.EJECUCION,
    ("GET", "/optimizacion/ejecuciones/{optimizacion_run_id}"): Clase.LECTURA,
    ("GET", "/optimizacion/ejecuciones/{optimizacion_run_id}/capacidad"): Clase.LECTURA,
    ("GET", "/optimizacion/ejecuciones/{optimizacion_run_id}/cuentas/{cuenta_id}"): Clase.LECTURA,
    ("GET", "/optimizacion/ejecuciones/{optimizacion_run_id}/diagnostico"): Clase.LECTURA,
    ("GET", "/optimizacion/ejecuciones/{optimizacion_run_id}/restricciones"): Clase.LECTURA,
    ("GET", "/optimizacion/ejecuciones/{optimizacion_run_id}/resultados"): Clase.LECTURA,
    ("GET", "/optimizacion/politica"): Clase.LECTURA,
    ("POST", "/pagos"): Clase.EJECUCION,
    ("GET", "/pagos/{pagos_run_id}"): Clase.LECTURA,
    ("GET", "/pagos/{pagos_run_id}/fuente"): Clase.AUDITORIA,
    ("GET", "/pagos/{pagos_run_id}/historia"): Clase.LECTURA,
    ("GET", "/pagos/{pagos_run_id}/rechazos"): Clase.AUDITORIA,
    ("GET", "/predictivo/entrenamientos"): Clase.LECTURA,
    ("POST", "/predictivo/entrenamientos"): Clase.EJECUCION,
    ("GET", "/predictivo/entrenamientos/{training_run_id}"): Clase.LECTURA,
    ("GET", "/predictivo/features"): Clase.LECTURA,
    ("GET", "/predictivo/modelos"): Clase.LECTURA,
    ("GET", "/predictivo/modelos/{modelo_version_id}"): Clase.LECTURA,
    ("GET", "/predictivo/scorings"): Clase.LECTURA,
    ("POST", "/predictivo/scorings"): Clase.EJECUCION,
    ("GET", "/predictivo/scorings/{scoring_run_id}"): Clase.LECTURA,
    ("GET", "/predictivo/scorings/{scoring_run_id}/cuentas/{cuenta_id}/explicacion"): Clase.LECTURA,
    ("GET", "/predictivo/scorings/{scoring_run_id}/drift"): Clase.LECTURA,
    ("GET", "/predictivo/scorings/{scoring_run_id}/predicciones"): Clase.LECTURA,
    ("GET", "/predictivo/targets"): Clase.LECTURA,
    ("GET", "/promesas/{promesa_id}"): Clase.LECTURA,
    ("POST", "/promesas/{promesa_id}/cancelaciones"): Clase.EJECUCION,
    ("GET", "/ruteo-v2/ejecuciones"): Clase.LECTURA,
    ("POST", "/ruteo-v2/ejecuciones"): Clase.EJECUCION,
    ("GET", "/ruteo-v2/ejecuciones/{ruteo_run_id}"): Clase.LECTURA,
    ("GET", "/ruteo-v2/ejecuciones/{ruteo_run_id}/paradas"): Clase.LECTURA,
    ("GET", "/ruteo-v2/ejecuciones/{ruteo_run_id}/rutas"): Clase.LECTURA,
    ("GET", "/ruteo-v2/redes"): Clase.LECTURA,
    ("GET", "/ruteo-v2/redes/{red_vial_version_id}"): Clase.LECTURA,
    ("GET", "/ruteo-v2/rutas/{ruta_id}"): Clase.LECTURA,
    ("GET", "/ruteo-v2/rutas/{ruta_id}/paradas"): Clase.LECTURA,
    ("GET", "/ruteos/{ruteo_run_id}"): Clase.LECTURA,
    ("GET", "/ruteos/{ruteo_run_id}/rutas"): Clase.LECTURA,
    ("GET", "/ruteos/{ruteo_run_id}/rutas/{clave_territorio}/paradas"): Clase.LECTURA,
    ("GET", "/salud"): Clase.PUBLICA,
    ("GET", "/territorial-v2"): Clase.LECTURA,
    ("POST", "/territorial-v2"): Clase.EJECUCION,
    ("GET", "/territorial-v2/{territorial_run_id}"): Clase.LECTURA,
    ("GET", "/territorial-v2/{territorial_run_id}/resultados"): Clase.LECTURA,
    ("GET", "/territoriales/{territorial_run_id}"): Clase.LECTURA,
    ("GET", "/territoriales/{territorial_run_id}/municipios"): Clase.LECTURA,
    ("GET", "/territoriales/{territorial_run_id}/ruteos"): Clase.LECTURA,
    ("POST", "/territoriales/{territorial_run_id}/ruteos"): Clase.EJECUCION,
    ("GET", "/trabajos/{trabajo_id}"): Clase.AUDITORIA,
}
"""Cada operacion de la API, por su metodo y su plantilla, con su clase. Explicita: una ruta nueva
no hereda una clase por su metodo, se clasifica aqui o la API no arranca."""


@dataclass
class Autenticador:
    """Las credenciales que acepta una API, construidas de su configuracion una vez, al arrancar."""

    claves: tuple[ClaveDeServicio, ...] = ()
    validador: ValidadorDeTokens | None = None
    mapa_de_grupos: dict[str, frozenset[Permiso]] = field(default_factory=dict)
    claim_grupos: str = "groups"
    secretos_gateway: tuple[bytes, ...] = ()
    exige_gateway: bool = False
    clave_seudonimos: bytes = field(default_factory=lambda: secrets.token_bytes(32))

    @classmethod
    def de_config(cls, config: Config) -> Autenticador:
        claves = list(leer_claves(config.api_keys.get_secret_value())) if config.api_keys else []
        if config.api_key is not None and config.api_key.get_secret_value():
            # La clave de desarrollo: un servicio mas, con todos los permisos. validar() la rechaza
            # en produccion.
            claves.append(
                ClaveDeServicio(
                    "desarrollo", digest_de(config.api_key.get_secret_value()), frozenset(Permiso)
                )
            )
        validador = None
        if config.oidc_configurado:
            reglas = ReglasDeToken(
                emisor=config.oidc_issuer or "",
                audiencia=config.oidc_audience or "",
                algoritmos=config.algoritmos(),
                tolerancia_segundos=config.oidc_tolerancia_segundos,
                azp_permitidos=frozenset(
                    a.strip() for a in (config.oidc_azp_permitidos or "").split(",") if a.strip()
                ),
                tipo=config.oidc_tipo_token or None,
            )
            from motor_cartera.observabilidad import metricas

            jwks = CacheJwks(
                url=config.oidc_jwks_url or "",
                ttl_segundos=config.oidc_jwks_ttl_segundos,
                antiguedad_maxima_segundos=config.oidc_jwks_antiguedad_maxima_segundos,
                refresco_minimo_segundos=config.oidc_jwks_refresco_minimo_segundos,
                timeout_segundos=config.oidc_jwks_timeout_segundos,
                ca_file=config.oidc_ca_file,
                al_refrescar=metricas.refresco_de_jwks,
            )
            validador = ValidadorDeTokens(reglas, jwks)
        secretos = (
            leer_secretos_gateway(config.gateway_secrets.get_secret_value())
            if config.gateway_secrets
            else ()
        )
        clave = (
            config.auditoria_clave.get_secret_value().encode()
            if config.auditoria_clave
            else secrets.token_bytes(32)
        )
        return cls(
            claves=tuple(claves),
            validador=validador,
            mapa_de_grupos=mapa_de_grupos(config.oidc_grupos),
            claim_grupos=config.oidc_claim_grupos,
            secretos_gateway=secretos,
            exige_gateway=config.exige_gateway,
            clave_seudonimos=clave,
        )

    def autenticar(self, request: Request) -> Principal:
        """El principal de la peticion, o ErrorDeApi 401 (o 503 si no se puede validar nada)."""
        clave = request.headers.get(CABECERA_API_KEY, "").strip()
        autorizacion = request.headers.get("Authorization", "").strip()
        if clave and autorizacion:
            raise _no_autenticado(
                "CREDENCIALES_AMBIGUAS",
                "La peticion trae una clave de servicio y un token: debe traer solo una "
                "credencial.",
                self._desafio(),
            )
        if autorizacion:
            return self._de_token(request, autorizacion)
        if clave:
            encontrada = buscar_clave(self.claves, clave)
            if encontrada is None:
                raise _no_autenticado(
                    "API_KEY_INVALIDA", "La API key no es valida.", _desafio_api_key()
                )
            return Principal("SERVICIO", f"svc:{encontrada.nombre}", encontrada.permisos)
        if self.validador is None:
            raise _no_autenticado(
                "API_KEY_AUSENTE", f"Falta la cabecera {CABECERA_API_KEY}.", _desafio_api_key()
            )
        raise _no_autenticado(
            "CREDENCIAL_AUSENTE",
            "La peticion no trae credencial: un token de acceso (por el edge) o una clave de "
            "servicio.",
            self._desafio(),
        )

    def _de_token(self, request: Request, autorizacion: str) -> Principal:
        esquema, _, token = autorizacion.partition(" ")
        token = token.strip()
        if esquema.lower() != "bearer" or not token:
            raise _no_autenticado(
                "CREDENCIAL_NO_SOPORTADA",
                "Authorization debe ser `Bearer <token>`.",
                self._desafio(),
            )
        if self.validador is None:
            raise _no_autenticado(
                "TOKEN_NO_ACEPTADO",
                "Esta API no tiene un proveedor de identidad configurado: no acepta tokens.",
                _desafio_api_key(),
            )
        if self.exige_gateway and not gateway_valido(
            self.secretos_gateway, request.headers.get(CABECERA_GATEWAY)
        ):
            raise _no_autenticado(
                "GATEWAY_REQUERIDO",
                "Un token de persona solo vale si llega por el edge.",
                self._desafio(),
            )
        try:
            reclamos = self.validador.validar(token)
        except TokenInvalido as error:
            raise _no_autenticado(
                error.codigo,
                str(error),
                {"WWW-Authenticate": f'Bearer realm="{REINO}", error="invalid_token"'},
            ) from None
        except IdentidadNoDisponible as error:
            raise ErrorDeApi(503, "IDENTIDAD_NO_DISPONIBLE", str(error)) from None
        permisos = permisos_de_reclamos(reclamos, self.mapa_de_grupos, self.claim_grupos)
        actor = seudonimo(self.clave_seudonimos, reclamos["iss"], reclamos["sub"])
        return Principal("USUARIO", actor, permisos)

    def _desafio(self) -> dict[str, str]:
        desafios = []
        if self.validador is not None:
            desafios.append(f'Bearer realm="{REINO}"')
        if self.claves:
            desafios.append(f'ApiKey header="{CABECERA_API_KEY}"')
        return {"WWW-Authenticate": ", ".join(desafios) or f'Bearer realm="{REINO}"'}


def _desafio_api_key() -> dict[str, str]:
    # 401 exige decir como autenticarse (RFC 9110, 11.6.1). No hay un esquema registrado para
    # API keys; este es el uso comun.
    return {"WWW-Authenticate": f'ApiKey header="{CABECERA_API_KEY}"'}


def _no_autenticado(codigo: str, mensaje: str, encabezados: dict[str, str]) -> ErrorDeApi:
    return ErrorDeApi(401, codigo, mensaje, encabezados=encabezados)


def seguridad_de(request: Request) -> dict[str, Any]:
    """Lo que la autorizacion supo de la peticion, para la auditoria y las metricas: su clase, el
    permiso que exigio, el actor y como termino."""
    estado = request.scope.setdefault("state", {})
    return estado.setdefault("seguridad", {})


def autorizar(request: Request) -> None:
    """La dependencia global: la ruta en la matriz, el principal y su permiso. Deja lo que decidio
    en `seguridad_de(request)`; la auditoria lo lee al terminar la respuesta."""
    seguridad = seguridad_de(request)
    ruta = getattr(request.scope.get("route"), "path", None)
    metodo = "GET" if request.method == "HEAD" else request.method
    clase = request.app.state.matriz.get((metodo, ruta))
    if clase is None:
        seguridad.update(decision="DENEGADO", motivo="RUTA_SIN_CLASIFICAR")
        log.error("ruta sin clasificar en la matriz de autorizacion: %s %s", metodo, ruta)
        raise ErrorDeApi(
            500, "RUTA_SIN_CLASIFICAR", "La ruta no tiene una clase de autorizacion: se niega."
        )
    seguridad["clase"] = clase
    if clase is Clase.PUBLICA:
        seguridad["decision"] = "PUBLICO"
        return
    permiso = PERMISO_DE[clase]
    seguridad["permiso"] = permiso
    try:
        principal = request.app.state.autenticador.autenticar(request)
    except ErrorDeApi as error:
        seguridad.update(
            decision="NO_AUTENTICADO" if error.estado == 401 else "ERROR",
            motivo=error.respuesta.codigo,
        )
        raise
    seguridad.update(actor=principal.actor, actor_tipo=principal.tipo)
    request.scope["state"]["principal"] = principal
    if not principal.puede(permiso):
        seguridad.update(decision="DENEGADO", motivo="PERMISO_INSUFICIENTE")
        raise ErrorDeApi(
            403,
            "PERMISO_INSUFICIENTE",
            f"La ruta exige el permiso {permiso.value}.",
            encabezados={
                "WWW-Authenticate": (
                    f'Bearer realm="{REINO}", error="insufficient_scope", scope="{permiso.value}"'
                )
            },
        )
    seguridad["decision"] = "PERMITIDO"


def principal_de(request: Request) -> Principal | None:
    return request.scope.get("state", {}).get("principal")


def clasificar(app: FastAPI, metodo: str, ruta: str, clase: Clase) -> None:
    """Agrega una operacion a la matriz de una app ya construida. Solo para las pruebas que montan
    rutas propias sobre la app real."""
    app.state.matriz[(metodo.upper(), ruta)] = clase


def operaciones_de(app: FastAPI) -> set[tuple[str, str]]:
    """Cada operacion que la API declara en su OpenAPI, por metodo y plantilla."""
    return {
        (metodo.upper(), ruta)
        for ruta, metodos in app.openapi()["paths"].items()
        for metodo in metodos
        if metodo in ("get", "post", "put", "patch", "delete")
    }


METODOS_DE_OPERACION = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})


def operaciones_de_las_rutas(app: FastAPI) -> set[tuple[str, str]]:
    """Las mismas operaciones que el OpenAPI, leidas de las rutas como las recorre FastAPI al
    armarlo (`iter_route_contexts`: cada APIRoute que entra al esquema, con el prefijo de su
    router, por metodo y plantilla), sin generar el esquema: generarlo tarda mas de un segundo, y la
    verificacion corre cada vez que se construye una app."""
    return {
        (metodo, contexto.path_format)
        for contexto in iter_route_contexts(app.routes)
        if isinstance(contexto.original_route, APIRoute)
        and contexto.include_in_schema
        and contexto.path_format is not None
        for metodo in contexto.methods or ()
        if metodo in METODOS_DE_OPERACION
    }


def verificar_matriz(app: FastAPI) -> None:
    """La matriz y la API tienen exactamente las mismas operaciones: una ruta sin clasificar o una
    clasificacion de una ruta que ya no existe impiden arrancar."""
    operaciones = operaciones_de_las_rutas(app)
    sin_clase = sorted(operaciones - set(app.state.matriz))
    sobrantes = sorted(set(app.state.matriz) - operaciones)
    if sin_clase or sobrantes:
        partes = []
        if sin_clase:
            partes.append("sin clasificar: " + ", ".join(f"{m} {r}" for m, r in sin_clase))
        if sobrantes:
            partes.append(
                "en la matriz y no en la API: " + ", ".join(f"{m} {r}" for m, r in sobrantes)
            )
        raise RuntimeError(
            "La matriz de autorizacion no corresponde a la API; " + "; ".join(partes)
        )


def agregar_seguridad_al_openapi(esquema: dict, matriz: dict[tuple[str, str], Clase]) -> None:
    """Declara en el OpenAPI como se autentica cada operacion: un token de acceso (Bearer JWT)
    con el permiso de su clase, o una clave de servicio. Las publicas, sin seguridad."""
    componentes = esquema.setdefault("components", {})
    componentes["securitySchemes"] = {
        "TokenDeAcceso": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "Token de acceso de OpenID Connect de una persona, emitido por el IdP y "
            "presentado por el edge. En produccion exige ademas la credencial del gateway.",
        },
        "ClaveDeServicio": {
            "type": "apiKey",
            "in": "header",
            "name": CABECERA_API_KEY,
            "description": "La clave de un servicio interno, con sus permisos declarados. Nunca en "
            "un navegador.",
        },
    }
    for ruta, metodos in esquema.get("paths", {}).items():
        for metodo, operacion in metodos.items():
            clase = matriz.get((metodo.upper(), ruta))
            if clase is None:
                continue
            if clase is Clase.PUBLICA:
                operacion["security"] = []
                continue
            permiso = PERMISO_DE[clase].value
            operacion["security"] = [{"TokenDeAcceso": [permiso]}, {"ClaveDeServicio": [permiso]}]
            _documentar_errores_de_acceso(operacion.setdefault("responses", {}), permiso)


CODIGOS_DE_TOKEN = (
    "CREDENCIAL_AUSENTE",
    "CREDENCIALES_AMBIGUAS",
    "GATEWAY_REQUERIDO",
    "TOKEN_MALFORMADO",
    "TOKEN_ALGORITMO_NO_PERMITIDO",
    "TOKEN_SIN_KID",
    "TOKEN_CLAVE_DESCONOCIDA",
    "TOKEN_FIRMA_INVALIDA",
    "TOKEN_EXPIRADO",
    "TOKEN_AUN_NO_VALIDO",
    "TOKEN_EMISOR_INVALIDO",
    "TOKEN_AUDIENCIA_INVALIDA",
)
"""Los 401 de un token, que se suman a los de la clave de servicio que cada ruta ya documenta."""


def _documentar_errores_de_acceso(respuestas: dict, permiso: str) -> None:
    referencia = {"$ref": "#/components/schemas/ErrorRespuesta"}
    no_autenticado = respuestas.setdefault(
        "401",
        {"description": "", "content": {"application/json": {"schema": referencia}}},
    )
    no_autenticado["description"] += (
        "- Con un token de acceso: " + ", ".join(f"`{c}`" for c in CODIGOS_DE_TOKEN) + ".\n"
    )
    respuestas["403"] = {
        "description": f"- `PERMISO_INSUFICIENTE`: a la credencial le falta `{permiso}`.\n",
        "content": {
            "application/json": {
                "schema": referencia,
                "examples": {
                    "PERMISO_INSUFICIENTE": {
                        "summary": "PERMISO_INSUFICIENTE",
                        "value": {
                            "codigo": "PERMISO_INSUFICIENTE",
                            "mensaje": f"La ruta exige el permiso {permiso}.",
                        },
                    }
                },
            }
        },
    }
