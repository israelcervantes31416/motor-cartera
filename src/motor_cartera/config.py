"""Configuracion del proyecto, leida del entorno.

Cada proceso (la API, el worker, las migraciones) valida al arrancar la configuracion que necesita
con `validar(config, rol)`: una configuracion invalida no arranca, y en produccion no hay valores
por omision para lo que es secreto ni para lo que decide quien entra. Los secretos se pueden leer
de un archivo con `MC_<NOMBRE>_FILE` (los secretos de Docker, los volumenes de Secret Manager),
nunca de los dos lados a la vez.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

IDENTIFICADOR_INTERNO = r"^[A-Z0-9_]{1,32}$"
"""Forma de despacho_id y cartera_id: mayusculas, digitos y guion bajo, como DSP_001."""

Entorno = Literal["development", "test", "production"]
Rol = Literal["api", "worker", "migraciones"]

SECRETOS = ("database_url", "api_key", "api_keys", "gateway_secrets", "auditoria_clave")
"""Los campos que se pueden leer de un archivo con MC_<NOMBRE>_FILE."""

SIN_AUTENTICACION = (
    "AUTH_DISABLED",
    "MC_AUTH_DISABLED",
    "MC_AUTH_DESHABILITADA",
    "MC_SIN_AUTENTICACION",
    "MC_DESACTIVAR_AUTENTICACION",
)
"""Variables con que alguien podria creer que apaga la autenticacion. No existe ese modo: si una
esta definida, ningun proceso arranca, en ningun entorno, para que nadie la deje puesta creyendo
que funciona."""

ALGORITMOS_ASIMETRICOS = frozenset(
    {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "EdDSA"}
)
"""Los algoritmos de firma que la API puede aceptar en un token. Nunca `none` ni HMAC (HS256...):
con una JWKS publica, aceptar HMAC dejaria firmar con la llave publica como secreto."""

LARGO_MINIMO_SECRETO = 32
"""Caracteres minimos del secreto del gateway y de la clave de los seudonimos de auditoria."""


class ConfiguracionInvalida(RuntimeError):
    """La configuracion no permite arrancar este proceso. Dice todos los problemas a la vez, sin
    ningun valor secreto."""

    def __init__(self, rol: str, problemas: list[str]) -> None:
        self.rol = rol
        self.problemas = problemas
        lista = "\n".join(f"  - {p}" for p in problemas)
        super().__init__(f"La configuracion no permite arrancar {rol}:\n{lista}")


def leer_secreto(ruta: str, variable: str) -> str:
    """El contenido de un archivo secreto, sin los espacios y saltos de linea de sus extremos. Un
    archivo que falta o esta vacio no arranca nada."""
    try:
        contenido = Path(ruta).read_text(encoding="utf-8").strip()
    except OSError as error:
        raise ConfiguracionInvalida(
            "el proceso",
            [f"{variable} apunta a un archivo que no se puede leer ({error.strerror})."],
        ) from None
    if not contenido:
        raise ConfiguracionInvalida("el proceso", [f"{variable} apunta a un archivo vacio."])
    return contenido


class ArchivosSecretos(PydanticBaseSettingsSource):
    """Lee cada secreto de SECRETOS del archivo de su MC_<NOMBRE>_FILE. Va despues de las variables
    de entorno; si un secreto viene por las dos vias, el proceso no arranca: no hay una ganadora
    implicita."""

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        valores: dict[str, Any] = {}
        for nombre in SECRETOS:
            variable = f"MC_{nombre.upper()}"
            ruta = os.environ.get(f"{variable}_FILE")
            if not ruta:
                continue
            if os.environ.get(variable) is not None:
                raise ConfiguracionInvalida(
                    "el proceso", [f"{variable} y {variable}_FILE estan definidas: usa solo una."]
                )
            valores[nombre] = leer_secreto(ruta, f"{variable}_FILE")
        return valores


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MC_", env_file=".env", extra="ignore")

    app_env: Entorno = "production"
    """MC_APP_ENV: el entorno, `development`, `test` o `production`. Por omision `production`: un
    proceso al que nadie le dijo donde corre se comporta como produccion, falla cerrado y no arranca
    sin su configuracion completa. development y test son conveniencias explicitas: la clave de
    desarrollo (MC_API_KEY), la documentacion de la API y los logs de texto."""

    database_url: str = "postgresql+psycopg://motor:motor@localhost:5434/cartera_dev"
    """MC_DATABASE_URL (o MC_DATABASE_URL_FILE): la base. El valor por omision es el de la maquina
    de
    desarrollo; en produccion se exige uno explicito, y nunca con esas credenciales."""
    semilla: int = 31416
    """Semilla fija para que el generador sea reproducible."""

    source_store_root: Path = Path("datos/fuentes")
    """MC_SOURCE_STORE_ROOT: la raiz del almacen de artefactos fuente, donde cada archivo recibido
    se guarda por su SHA-256 y no se borra. La API y el worker tienen que ver la misma: en el
    compose es un volumen propio, aparte del de PostgreSQL, que guarda solo su registro."""

    # Un despacho y una cartera: metadata del sistema, no columnas de las fuentes. Cada corrida y
    # cada ingesta de pagos guarda con cual se registro. v0.6 no administra varios despachos; que
    # el valor viaje con cada registro es lo que permite cambiarlo despues sin reescribir nada.
    despacho_id: str = Field(default="DSP_001", pattern=IDENTIFICADOR_INTERNO)
    """El despacho que opera este sistema."""
    cartera_id: str = Field(default="CARTERA_PRINCIPAL", pattern=IDENTIFICADOR_INTERNO)
    """La cartera del acreedor que ese despacho gestiona."""
    tolerancia_rechazo: float = Field(default=0.05, ge=0, lt=1)
    """Fraccion maxima de registros rechazados con la que una corrida todavia publica.

    Es una regla de negocio y vive en el servidor: si el cliente de la API pudiera
    fijarla, la barrera seria opcional. 0 es todo o nada. 1 no se admite: publicaria
    aunque no pasara ningun registro.
    """
    tolerancia_rechazo_pagos: float = Field(default=0.0, ge=0, lt=1)
    """Fraccion maxima de movimientos rechazados con la que una ingesta de pagos todavia se acepta.

    Por omision 0: un solo movimiento invalido rechaza el archivo entero. Un archivo de dinero
    aceptado a medias subestimaria la recuperacion sin que nadie lo notara. Como la de cartera, vive
    en el servidor y cada ingesta guarda con cual se juzgo.
    """
    api_key: SecretStr | None = None
    """MC_API_KEY: la clave de desarrollo, para la cabecera X-API-Key. Vale para todo, asi que solo
    existe en development y test: en produccion la API no arranca con ella. Ahi cada servicio tiene
    su clave con sus permisos (MC_API_KEYS) y las personas entran con OpenID Connect.

    Es opcional aqui porque el CLI, Alembic y las pruebas no la necesitan.
    """
    filas_por_lote: int = Field(default=50_000, ge=100)
    """De cuantas filas se lee y se juzga cada lote de una fuente oficial (cartera/v2 y pagos/v1).
    Es un parametro operativo: cambia la memoria que se usa, nunca el resultado de una corrida."""
    tamano_maximo_mb: int = Field(default=512, gt=0)
    """Tope del archivo que aceptan POST /corridas y POST /pagos, en MiB. El archivo se copia al
    almacen por bloques mientras llega, sin cargarlo en memoria: el tope cuida el disco, y alcanza
    para la cartera objetivo de 500,000 cuentas."""
    tamano_maximo_cuerpo_kb: int = Field(default=8_192, ge=1, le=1_048_576)
    """MC_TAMANO_MAXIMO_CUERPO_KB: tope del cuerpo de una peticion que no sube un archivo (JSON), en
    KiB. La configuracion de campo u optimizacion mas grande del benchmark cabe con holgura; mas que
    eso no es una peticion legitima. Un cuerpo mayor responde 413 sin leerse entero."""

    zona_horaria_fuente: str = Field(
        default="America/Mexico_City", min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_/+-]+$"
    )
    """MC_ZONA_HORARIA_FUENTE: la zona de las horas locales que traen las fuentes. pagos/v1 no trae
    zona, y el lifecycle registra instantes con su zona: para compararlos (una gestion antes de un
    pago, un pago antes de la fecha limite de una promesa) se usa esta, con la base de zonas de
    PostgreSQL. La atribucion y la evaluacion de promesas guardan con cual se ejecutaron."""
    atribucion_ventana_dias: int = Field(default=30, ge=1, le=366)
    """MC_ATRIBUCION_VENTANA_DIAS: cuantos dias antes de un movimiento puede haber ocurrido una
    gestion para ser su candidata en atribucion/v1, cuando quien pide la atribucion no dice otra.
    Los 30 dias son la politica del demo sobre datos sinteticos, no una verdad de negocio: cada
    ejecucion guarda la ventana con que se hizo."""

    # El worker. Son parametros operativos: deciden cuando, cada cuanto y cuantas veces se ejecuta
    # un trabajo, nunca que calcula un motor. Ninguno cambia una decision, un municipio ni una ruta.
    worker_poll_segundos: float = Field(default=0.5, gt=0)
    """Cuanto espera el worker antes de volver a buscar trabajo cuando la cola esta vacia."""
    worker_lease_segundos: float = Field(default=60, gt=0)
    """Por cuanto tiempo un trabajo es de su worker sin que este lata. Si deja de latir, otro worker
    lo puede tomar cuando vence."""
    worker_heartbeat_segundos: float = Field(default=20, gt=0)
    """Cada cuanto late el worker para renovar el lease del trabajo que ejecuta. Menor que el lease:
    si no, el lease venceria entre un latido y otro."""
    worker_max_intentos: int = Field(default=5, ge=1)
    """Cuantas veces se puede tomar un trabajo antes de darlo por FALLIDO. Cada trabajo copia el
    valor al nacer, y conserva la politica con que nacio."""
    worker_backoff_segundos: float = Field(default=1, gt=0)
    """La espera tras el primer error de un trabajo; se duplica en cada intento: 1, 2, 4, 8..."""

    # El proveedor de ruteo de ruteo/v2: un OSRM autoalojado. Son parametros operativos: cuanto se
    # espera a que responda y cuantas veces se reintenta una peticion, nunca una duracion ni una
    # distancia. Sin MC_OSRM_URL este proceso no rutea, y su worker no toma trabajos RUTEO_V2.
    osrm_url: str | None = None
    """MC_OSRM_URL: el OSRM autoalojado, en la red interna (http://osrm:5000 en el compose). Nunca
    el servidor publico de OSRM: las coordenadas de las cuentas no salen de la red propia."""
    osrm_timeout_conexion_segundos: float = Field(default=3, gt=0)
    osrm_timeout_lectura_segundos: float = Field(default=30, gt=0)
    osrm_reintentos: int = Field(default=2, ge=0, le=10)
    """Cuantas veces se reintenta una peticion tras un fallo transitorio (sin conexion, un timeout,
    un 5xx): 2 son tres intentos, con una espera que se duplica desde osrm_backoff_segundos."""
    osrm_backoff_segundos: float = Field(default=0.5, ge=0)
    osrm_lote_nearest: int = Field(default=100, ge=1, le=10_000)
    """Cuantos puntos van en cada peticion nearest. El compose arranca OSRM con
    --max-nearest-locations 100."""
    osrm_concurrencia: int = Field(default=4, ge=1, le=64)
    """Cuantas peticiones a OSRM puede tener en curso una ejecucion: las matrices de los siguientes
    gestores se piden mientras se planea el actual. No cambia ninguna ruta."""
    osrm_salud_segundos: float = Field(default=10, gt=0)
    """Cada cuanto el worker vuelve a preguntar si OSRM responde antes de tomar un trabajo
    RUTEO_V2."""

    # La identidad de las personas: un proveedor OpenID Connect (Dex en desarrollo y en las pruebas;
    # en produccion, el que se elija). La API no habla con el navegador: valida el token de acceso
    # que
    # le pasa el gateway, con la JWKS del emisor. Ver docs/security.md.
    oidc_issuer: str | None = None
    """MC_OIDC_ISSUER: el emisor exacto que deben traer los tokens (`iss`). En produccion, https."""
    oidc_audience: str | None = None
    """MC_OIDC_AUDIENCE: la audiencia de la API (`aud`), p. ej. motor-cartera-api."""
    oidc_jwks_url: str | None = None
    """MC_OIDC_JWKS_URL: de donde se leen las llaves publicas del emisor. Explicita, sin
    descubrirla:
    la API no depende del documento de descubrimiento para arrancar. En produccion, https."""
    oidc_ca_file: Path | None = None
    """MC_OIDC_CA_FILE: un certificado de CA adicional en que confiar al leer la JWKS (la CA efimera
    del compose de produccion local). Sin el, solo las CA del sistema."""
    oidc_algoritmos: str = "RS256"
    """MC_OIDC_ALGORITMOS: los algoritmos de firma aceptados, separados por comas. Solo
    asimetricos."""
    oidc_tolerancia_segundos: int = Field(default=30, ge=0, le=120)
    """MC_OIDC_TOLERANCIA_SEGUNDOS: la diferencia de relojes que se tolera al revisar `exp`, `nbf` e
    `iat`. Pequena y explicita: un token vencido hace mas de esto no entra."""
    oidc_jwks_ttl_segundos: int = Field(default=300, ge=10, le=86_400)
    """Cuanto se usa la JWKS sin volver a pedirla."""
    oidc_jwks_antiguedad_maxima_segundos: int = Field(default=3_600, ge=60, le=86_400)
    """Si el emisor no responde, hasta que antiguedad se sigue usando la JWKS que ya se tenia. Mas
    vieja, la API no valida ningun token: responde 503, cerrada."""
    oidc_jwks_refresco_minimo_segundos: int = Field(default=30, ge=1, le=3_600)
    """Cada cuanto, como mucho, un `kid` desconocido obliga a pedir la JWKS otra vez (una rotacion
    de
    llaves). Asi un token con un `kid` inventado no convierte a la API en un martillo contra el
    IdP."""
    oidc_jwks_timeout_segundos: float = Field(default=3, gt=0, le=30)
    oidc_azp_permitidos: str | None = None
    """MC_OIDC_AZP_PERMITIDOS: los clientes (`azp`) cuyos tokens se aceptan, separados por comas.
    Sin
    el, no se revisa."""
    oidc_tipo_token: str | None = None
    """MC_OIDC_TIPO_TOKEN: el `typ` que debe traer la cabecera del token, p. ej. `at+jwt` (RFC
    9068),
    con un IdP que lo emita. Dex no lo emite; sin el, no se revisa."""
    oidc_claim_grupos: str = Field(default="groups", pattern=r"^[A-Za-z0-9_.:-]{1,64}$")
    """El claim del token con los grupos de la persona."""
    oidc_grupos: str | None = None
    """MC_OIDC_GRUPOS: que permisos da cada grupo del IdP, `grupo=PERMISO|ROL,...;otro=...`, p. ej.
    `mc-auditores=AUDITOR;mc-lectores=mc.read`. Un rol (VIEWER, AUDITOR, OPERATOR, ADMIN) es un
    nombre para un conjunto de permisos: la API solo autoriza por permisos."""

    # Las credenciales de los servicios.
    api_keys: SecretStr | None = None
    """MC_API_KEYS (o MC_API_KEYS_FILE): las claves de servicio, una por linea:
    `<nombre> <sha256 de la clave en hex> <permiso>[,<permiso>...]`. Se guarda el hash, no la
    clave: la API compara el SHA-256 de lo que recibe. Varias lineas con el mismo servicio son una
    rotacion. Nunca llegan al navegador: el edge borra X-API-Key."""
    gateway_secrets: SecretStr | None = None
    """MC_GATEWAY_SECRETS (o MC_GATEWAY_SECRETS_FILE): los secretos con que el edge se presenta ante
    la API (cabecera X-MC-Gateway), uno por linea; dos durante una rotacion. En produccion un token
    de una persona solo vale si llega con uno de ellos."""
    exigir_gateway: bool | None = None
    """MC_EXIGIR_GATEWAY: si un token de persona exige la credencial del gateway. Por omision si en
    produccion (y no se puede apagar ahi) y no en development y test."""
    auditoria_clave: SecretStr | None = None
    """MC_AUDITORIA_CLAVE (o MC_AUDITORIA_CLAVE_FILE): la clave del HMAC con que la auditoria
    identifica a cada persona sin guardar su `sub`. Aparte de cualquier otro secreto. Sin ella,
    fuera de produccion, cada proceso usa una al azar: los seudonimos no se correlacionan entre
    procesos."""

    # La observabilidad.
    log_formato: Literal["json", "texto"] | None = None
    """MC_LOG_FORMATO: `json` o `texto`. Por omision json en produccion y texto en lo demas."""
    log_nivel: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    metricas_puerto: int | None = Field(default=None, ge=1024, le=65_535)
    """MC_METRICAS_PUERTO: el puerto interno donde el proceso sirve /metrics (y el worker su salud).
    Sin el, no se sirve. Nunca se publica al host ni pasa por el edge."""
    otel_endpoint: str | None = None
    """MC_OTEL_ENDPOINT: el colector de OpenTelemetry (OTLP/HTTP), p. ej.
    http://otel-collector:4318.
    Sin el, no se exportan trazas. Si el colector no responde, las trazas se descartan: nunca
    detienen una peticion."""
    otel_muestreo: float = Field(default=1.0, ge=0, le=1)
    """MC_OTEL_MUESTREO: la fraccion de trazas que se muestrean cuando la peticion no trae una
    decision de su padre. La referencia de produccion usa menos de 1 (ver docs/observability.md)."""

    @model_validator(mode="after")
    def _el_latido_cabe_en_el_lease(self) -> Self:
        if self.worker_heartbeat_segundos >= self.worker_lease_segundos:
            raise ValueError(
                "MC_WORKER_HEARTBEAT_SEGUNDOS debe ser menor que MC_WORKER_LEASE_SEGUNDOS: "
                f"{self.worker_heartbeat_segundos} no es menor que {self.worker_lease_segundos}."
            )
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            ArchivosSecretos(settings_cls),
            dotenv_settings,
            file_secret_settings,
        )

    @property
    def es_produccion(self) -> bool:
        return self.app_env == "production"

    @property
    def formato_de_logs(self) -> str:
        return self.log_formato or ("json" if self.es_produccion else "texto")

    @property
    def exige_gateway(self) -> bool:
        if self.es_produccion:
            return True
        return bool(self.exigir_gateway)

    @property
    def oidc_configurado(self) -> bool:
        return bool(self.oidc_issuer or self.oidc_audience or self.oidc_jwks_url)

    def algoritmos(self) -> tuple[str, ...]:
        return tuple(a.strip() for a in self.oidc_algoritmos.split(",") if a.strip())

    def problemas(self, rol: Rol) -> list[str]:
        """Lo que impide arrancar `rol` con esta configuracion, sin ningun valor secreto."""
        problemas = [
            f"{variable} esta definida, y no existe un modo sin autenticacion."
            for variable in SIN_AUTENTICACION
            if os.environ.get(variable) not in (None, "")
        ]
        if self.es_produccion:
            problemas += self._problemas_de_la_base()
        if rol == "api":
            problemas += self._problemas_de_la_api()
        return problemas

    def _problemas_de_la_base(self) -> list[str]:
        from sqlalchemy.engine import make_url
        from sqlalchemy.exc import ArgumentError

        if "database_url" not in self.model_fields_set:
            return [
                "MC_DATABASE_URL (o MC_DATABASE_URL_FILE) no esta definida: en produccion no "
                "hay una base por omision."
            ]
        try:
            url = make_url(self.database_url)
        except ArgumentError:
            return ["MC_DATABASE_URL no es una URL de base de datos."]
        if not url.password or url.password in {"motor", "postgres", "password", "secret"}:
            return ["MC_DATABASE_URL usa una contrasena vacia o de desarrollo."]
        return []

    def _problemas_de_la_api(self) -> list[str]:
        from motor_cartera.seguridad.credenciales import leer_claves, leer_secretos_gateway
        from motor_cartera.seguridad.permisos import mapa_de_grupos

        problemas: list[str] = []
        algoritmos = self.algoritmos()
        if not algoritmos:
            problemas.append("MC_OIDC_ALGORITMOS no nombra ningun algoritmo.")
        for algoritmo in algoritmos:
            if algoritmo not in ALGORITMOS_ASIMETRICOS:
                problemas.append(
                    f"MC_OIDC_ALGORITMOS incluye {algoritmo!r}: solo se aceptan algoritmos "
                    f"asimetricos ({', '.join(sorted(ALGORITMOS_ASIMETRICOS))})."
                )
        if self.oidc_configurado:
            for nombre, valor in (
                ("MC_OIDC_ISSUER", self.oidc_issuer),
                ("MC_OIDC_AUDIENCE", self.oidc_audience),
                ("MC_OIDC_JWKS_URL", self.oidc_jwks_url),
            ):
                if not valor:
                    problemas.append(
                        f"{nombre} falta: OpenID Connect necesita emisor, audiencia y JWKS."
                    )
            for nombre, valor in (
                ("MC_OIDC_ISSUER", self.oidc_issuer),
                ("MC_OIDC_JWKS_URL", self.oidc_jwks_url),
            ):
                if valor and urlsplit(valor).scheme not in (
                    ("https",) if self.es_produccion else ("https", "http")
                ):
                    problemas.append(
                        f"{nombre} debe ser https"
                        + (" en produccion." if self.es_produccion else " o http.")
                    )
            if self.oidc_ca_file is not None and not self.oidc_ca_file.is_file():
                problemas.append("MC_OIDC_CA_FILE no apunta a un archivo.")
        try:
            mapa_de_grupos(self.oidc_grupos)
        except ValueError as error:
            problemas.append(f"MC_OIDC_GRUPOS: {error}")
        if self.api_keys is not None:
            try:
                leer_claves(self.api_keys.get_secret_value())
            except ValueError as error:
                problemas.append(f"MC_API_KEYS: {error}")
        if self.gateway_secrets is not None:
            try:
                leer_secretos_gateway(self.gateway_secrets.get_secret_value())
            except ValueError as error:
                problemas.append(f"MC_GATEWAY_SECRETS: {error}")
        if self.auditoria_clave is not None and (
            len(self.auditoria_clave.get_secret_value()) < LARGO_MINIMO_SECRETO
        ):
            problemas.append(
                f"MC_AUDITORIA_CLAVE debe tener al menos {LARGO_MINIMO_SECRETO} caracteres."
            )

        if self.es_produccion:
            if self.api_key is not None:
                problemas.append(
                    "MC_API_KEY esta definida: la clave de desarrollo vale para todo y no existe "
                    "en produccion. Cada servicio usa su clave con sus permisos (MC_API_KEYS)."
                )
            if not self.oidc_configurado:
                problemas.append(
                    "OpenID Connect no esta configurado (MC_OIDC_ISSUER, MC_OIDC_AUDIENCE y "
                    "MC_OIDC_JWKS_URL): en produccion las personas entran con un token del IdP."
                )
            if self.gateway_secrets is None:
                problemas.append(
                    "MC_GATEWAY_SECRETS (o MC_GATEWAY_SECRETS_FILE) no esta definida: en "
                    "produccion un token de persona solo vale si llega por el edge."
                )
            if self.exigir_gateway is False:
                problemas.append("MC_EXIGIR_GATEWAY=false no se admite en produccion.")
            if self.auditoria_clave is None:
                problemas.append(
                    "MC_AUDITORIA_CLAVE (o MC_AUDITORIA_CLAVE_FILE) no esta definida: en "
                    "produccion los seudonimos de auditoria usan una clave estable y propia."
                )
        elif not (
            (self.api_key is not None and self.api_key.get_secret_value())
            or (self.api_keys is not None and self.api_keys.get_secret_value())
            or self.oidc_configurado
        ):
            problemas.append(
                "La API no tiene ninguna credencial que aceptar: define MC_API_KEY (desarrollo), "
                "MC_API_KEYS o OpenID Connect (MC_OIDC_ISSUER, MC_OIDC_AUDIENCE, "
                "MC_OIDC_JWKS_URL). Una API sin credenciales arrancaria abierta o inservible."
            )
        return problemas


def validar(config: Config, rol: Rol) -> None:
    """Levanta ConfiguracionInvalida si `rol` no puede arrancar con `config`."""
    problemas = config.problemas(rol)
    if problemas:
        raise ConfiguracionInvalida(rol, problemas)


config = Config()
