"""Una version de la configuracion de optimizacion: leerla, validarla, darle su hash y publicarla.

Es CONFIGURACION OPERACIONAL VERSIONADA de un escenario, no la politica ni datos de CARTERA: que
capacidad hay (sus pools), que canales operativos son admisibles para cada canal que recomienda
decision/v2 (una matriz explicita, no una verdad de negocio), que restricciones de cobertura se
exigen, el total de cuentas y el presupuesto de unidades, si se admiten predicciones CON_IMPUTACION.
Los valores de un benchmark son SUPUESTOS SINTETICOS: existen para medir el optimizador.

El documento, en JSON (POST /optimizacion/configuraciones, `motor-cartera
crear-config-optimizacion`), con un esquema cerrado (sin SQL, sin Python, sin expresiones):

    {"nombre": "Escenario sintetico",
     "permitir_predicciones_con_imputacion": false,
     "max_cuentas_atendidas": 9000,
     "max_unidades_recurso": 15000,
     "admisibilidad": [{"canal_decision": "TELEFONICA", "canal_operativo": "DIGITAL",
                        "permitido": true}, ...],
     "pools": [{"codigo": "DIGITAL_GLOBAL", "canal": "DIGITAL", "tipo_alcance": "GLOBAL",
                "referencia_alcance": null, "capacidad": 6000,
                "unidades_recurso_por_asignacion": 1, "activo": true},
               {"codigo": "CAMPO_GESTOR_001", "canal": "CAMPO", "tipo_alcance": "GESTOR_CAMPO",
                "referencia_alcance": "GESTOR_001", "capacidad": null,
                "unidades_recurso_por_asignacion": 20, "activo": true}],
     "restricciones": [{"codigo": "MIN_MUY_ALTA", "tipo": "MIN_CUENTAS",
                        "dimension": "PRIORIDAD_DECISION", "valor_dimension": "MUY_ALTA",
                        "limite": 1500, "activa": true}],
     "supuestos": "Capacidades sinteticas, solo para medir el optimizador."}

Se valida entero, fail-closed, y todos los problemas se reportan juntos. La forma canonica
(optimization-config/v1) lleva todo lo que cambia un plan: la admisibilidad, los pools con sus
capacidades y sus unidades, las restricciones, el total, el presupuesto y el flag de imputacion, en
orden canonico. El nombre y los supuestos no entran. El mismo contenido es la misma version: se
reutiliza. Una version PUBLICADA no cambia nunca; una BORRADOR solo puede publicarse.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlmodel import Session, select

from motor_cartera.analytics.catalogo import canonico
from motor_cartera.db.copia import copiar
from motor_cartera.db.modelos import (
    PATRON_CODIGO_BASE,
    EstadoVersionOptimizacion,
    VersionConfiguracionOptimizacion,
    ahora,
)
from motor_cartera.db.sesion import insertar_en_savepoint, restriccion
from motor_cartera.optimizacion.politica import (
    ALCANCE_DEL_CANAL,
    BPS,
    CANALES_DE_DECISION,
    CANALES_OPERATIVOS,
    PARTICIPACIONES,
    PUBLIC_V1,
    VOCABULARIOS,
    Dimension,
    TipoAlcance,
    TipoRestriccion,
)

VERSION_CONFIGURACION = "optimization-config/v1"
"""Cambia si cambia la forma canonica con que se calcula el hash."""
LLAVES_DEL_DOCUMENTO = frozenset(
    {
        "nombre",
        "permitir_predicciones_con_imputacion",
        "max_cuentas_atendidas",
        "max_unidades_recurso",
        "admisibilidad",
        "pools",
        "restricciones",
        "supuestos",
    }
)
REQUERIDAS = frozenset({"nombre", "permitir_predicciones_con_imputacion", "admisibilidad", "pools"})
LLAVES_DE_UNA_ADMISIBILIDAD = frozenset({"canal_decision", "canal_operativo", "permitido"})
LLAVES_DE_UN_POOL = frozenset(
    {
        "codigo",
        "canal",
        "tipo_alcance",
        "referencia_alcance",
        "capacidad",
        "unidades_recurso_por_asignacion",
        "activo",
    }
)
LLAVES_DE_UNA_RESTRICCION = frozenset(
    {"codigo", "tipo", "dimension", "valor_dimension", "limite", "activa"}
)
UNIDADES_MAXIMAS = 1_000_000
"""Un tope de cordura para las unidades de una asignacion, no una regla de negocio."""
LARGO_DE_TEXTO = 120
LARGO_DE_SUPUESTOS = 2_000
PATRON_CODIGO = re.compile(PATRON_CODIGO_BASE)
RESTRICCION_HASH = "uq_version_configuracion_optimizacion_hash"


class ConfiguracionInvalida(ValueError):
    """La configuracion no se puede registrar: no se registro nada. `problemas` dice cada uno."""

    def __init__(self, problemas: list[str]) -> None:
        super().__init__(
            "La configuracion de optimizacion no es valida: " + "; ".join(problemas) + "."
        )
        self.problemas = problemas


@dataclass(frozen=True, order=True)
class Admisibilidad:
    canal_decision: str
    canal_operativo: str
    permitido: bool

    def canonica(self) -> list:
        return [self.canal_decision, self.canal_operativo, self.permitido]


@dataclass(frozen=True)
class PoolConfigurado:
    codigo: str
    canal: str
    tipo_alcance: str
    referencia_alcance: str | None
    capacidad: int | None
    """Para CAMPO puede no declararse: entonces es el numero de paradas PROGRAMADA de su gestor."""
    unidades_recurso_por_asignacion: int
    activo: bool

    def canonica(self) -> dict[str, Any]:
        return {
            "codigo": self.codigo,
            "canal": self.canal,
            "tipo_alcance": self.tipo_alcance,
            "referencia_alcance": self.referencia_alcance,
            "capacidad": self.capacidad,
            "unidades_recurso_por_asignacion": self.unidades_recurso_por_asignacion,
            "activo": self.activo,
        }


@dataclass(frozen=True)
class RestriccionConfigurada:
    codigo: str
    tipo: str
    dimension: str
    valor_dimension: str
    limite: int
    """Cuentas en MIN_CUENTAS y MAX_CUENTAS; puntos base (0 a 10,000) en una participacion."""
    activa: bool

    def canonica(self) -> dict[str, Any]:
        return {
            "codigo": self.codigo,
            "tipo": self.tipo,
            "dimension": self.dimension,
            "valor_dimension": self.valor_dimension,
            "limite": self.limite,
            "activa": self.activa,
        }


@dataclass(frozen=True)
class ConfiguracionOptimizacion:
    nombre: str
    permitir_predicciones_con_imputacion: bool
    max_cuentas_atendidas: int | None
    max_unidades_recurso: int | None
    admisibilidad: tuple[Admisibilidad, ...]
    pools: tuple[PoolConfigurado, ...]
    restricciones: tuple[RestriccionConfigurada, ...]
    supuestos: str | None = None

    def canonica(self) -> dict[str, Any]:
        """Todo lo que cambia un plan, en orden canonico: sin el nombre ni los supuestos."""
        return {
            "forma": VERSION_CONFIGURACION,
            "permitir_predicciones_con_imputacion": self.permitir_predicciones_con_imputacion,
            "max_cuentas_atendidas": self.max_cuentas_atendidas,
            "max_unidades_recurso": self.max_unidades_recurso,
            "admisibilidad": [a.canonica() for a in sorted(self.admisibilidad)],
            "pools": [p.canonica() for p in sorted(self.pools, key=lambda p: p.codigo)],
            "restricciones": [
                r.canonica() for r in sorted(self.restricciones, key=lambda r: r.codigo)
            ],
        }

    def hash(self) -> str:
        return hashlib.sha256(canonico(self.canonica())).hexdigest()

    def admisibles(self, canal_decision: str) -> tuple[str, ...]:
        """Los canales operativos permitidos para un canal de decision, en orden canonico."""
        return tuple(
            sorted(
                a.canal_operativo
                for a in self.admisibilidad
                if a.canal_decision == canal_decision and a.permitido
            )
        )

    @property
    def usa_campo(self) -> bool:
        return any(p.canal == "CAMPO" and p.activo for p in self.pools)

    @property
    def usa_base(self) -> bool:
        return any(p.tipo_alcance == TipoAlcance.BASE and p.activo for p in self.pools) or any(
            r.dimension == Dimension.BASE and r.activa for r in self.restricciones
        )


# --- leer y validar -------------------------------------------------------------------------------


def _llaves(valor: dict, conocidas: frozenset[str], donde: str, problemas: list[str]) -> None:
    extra = sorted(set(valor) - conocidas)
    if extra:
        problemas.append(f"{donde}: no se conoce {', '.join(extra)}")


def _texto(valor: Any, donde: str, problemas: list[str], largo: int = LARGO_DE_TEXTO) -> str | None:
    if not isinstance(valor, str) or not valor.strip() or len(valor) > largo:
        problemas.append(f"{donde}: un texto de 1 a {largo} caracteres")
        return None
    return valor.strip()


def _codigo(valor: Any, donde: str, problemas: list[str]) -> str | None:
    if not isinstance(valor, str) or not PATRON_CODIGO.fullmatch(valor):
        problemas.append(f"{donde}: un codigo de 1 a 32 caracteres A-Z, 0-9, _ o -")
        return None
    return valor


def _booleano(valor: Any, donde: str, problemas: list[str]) -> bool | None:
    if not isinstance(valor, bool):
        problemas.append(f"{donde}: true o false, sin valor por omision")
        return None
    return valor


def _entero(
    valor: Any, donde: str, problemas: list[str], minimo: int, maximo: int, nulo: bool = False
) -> int | None:
    if valor is None and nulo:
        return None
    if isinstance(valor, bool) or not isinstance(valor, int) or not minimo <= valor <= maximo:
        problemas.append(
            f"{donde}: un entero de {minimo:,} a {maximo:,}" + (", o null" if nulo else "")
        )
        return None
    return valor


def _uno_de(valor: Any, opciones, donde: str, problemas: list[str]) -> str | None:
    validas = [str(o) for o in opciones]
    if valor not in validas:
        problemas.append(f"{donde}: uno de {', '.join(validas)}")
        return None
    return valor


def _admisibilidad(valor: Any, i: int, problemas: list[str]) -> Admisibilidad | None:
    donde = f"admisibilidad[{i}]"
    if not isinstance(valor, dict):
        problemas.append(f"{donde}: un objeto")
        return None
    _llaves(valor, LLAVES_DE_UNA_ADMISIBILIDAD, donde, problemas)
    decision = _uno_de(
        valor.get("canal_decision"), CANALES_DE_DECISION, f"{donde}.canal_decision", problemas
    )
    operativo = _uno_de(
        valor.get("canal_operativo"), CANALES_OPERATIVOS, f"{donde}.canal_operativo", problemas
    )
    permitido = _booleano(valor.get("permitido"), f"{donde}.permitido", problemas)
    if decision is None or operativo is None or permitido is None:
        return None
    return Admisibilidad(decision, operativo, permitido)


def _pool(valor: Any, i: int, problemas: list[str]) -> PoolConfigurado | None:
    donde = f"pools[{i}]"
    if not isinstance(valor, dict):
        problemas.append(f"{donde}: un objeto")
        return None
    _llaves(valor, LLAVES_DE_UN_POOL, donde, problemas)
    antes = len(problemas)
    codigo = _codigo(valor.get("codigo"), f"{donde}.codigo", problemas)
    canal = _uno_de(valor.get("canal"), CANALES_OPERATIVOS, f"{donde}.canal", problemas)
    alcance = _uno_de(valor.get("tipo_alcance"), TipoAlcance, f"{donde}.tipo_alcance", problemas)
    referencia = valor.get("referencia_alcance")
    if canal is not None and alcance is not None:
        if TipoAlcance(alcance) not in ALCANCE_DEL_CANAL[canal]:
            problemas.append(
                f"{donde}: un pool {canal} es de alcance "
                + " o ".join(sorted(a.value for a in ALCANCE_DEL_CANAL[canal]))
                + f", no {alcance}"
            )
        if alcance == TipoAlcance.GLOBAL and referencia is not None:
            problemas.append(f"{donde}.referencia_alcance: un pool GLOBAL no tiene referencia")
        elif alcance != TipoAlcance.GLOBAL:
            referencia = _codigo(
                referencia, f"{donde}.referencia_alcance (el codigo de su {alcance})", problemas
            )
    capacidad = _entero(
        valor.get("capacidad"),
        f"{donde}.capacidad",
        problemas,
        1,
        PUBLIC_V1.limite_exacto,
        nulo=canal == "CAMPO",
    )
    unidades = _entero(
        valor.get("unidades_recurso_por_asignacion"),
        f"{donde}.unidades_recurso_por_asignacion",
        problemas,
        1,
        UNIDADES_MAXIMAS,
    )
    activo = _booleano(valor.get("activo"), f"{donde}.activo", problemas)
    if len(problemas) > antes:
        return None
    return PoolConfigurado(codigo, canal, alcance, referencia, capacidad, unidades, activo)


def _restriccion(valor: Any, i: int, problemas: list[str]) -> RestriccionConfigurada | None:
    donde = f"restricciones[{i}]"
    if not isinstance(valor, dict):
        problemas.append(f"{donde}: un objeto")
        return None
    _llaves(valor, LLAVES_DE_UNA_RESTRICCION, donde, problemas)
    antes = len(problemas)
    codigo = _codigo(valor.get("codigo"), f"{donde}.codigo", problemas)
    tipo = _uno_de(valor.get("tipo"), TipoRestriccion, f"{donde}.tipo", problemas)
    dimension = _uno_de(valor.get("dimension"), Dimension, f"{donde}.dimension", problemas)
    valor_dimension = valor.get("valor_dimension")
    if dimension is not None:
        vocabulario = VOCABULARIOS[Dimension(dimension)]
        if vocabulario is None:
            valor_dimension = _codigo(
                valor_dimension, f"{donde}.valor_dimension (el codigo de una base)", problemas
            )
        else:
            valor_dimension = _uno_de(
                valor_dimension, vocabulario, f"{donde}.valor_dimension", problemas
            )
    tope = BPS if tipo is not None and TipoRestriccion(tipo) in PARTICIPACIONES else None
    limite = _entero(
        valor.get("limite"),
        f"{donde}.limite" + (" (puntos base)" if tope else " (cuentas)"),
        problemas,
        0,
        tope or PUBLIC_V1.limite_exacto,
    )
    activa = _booleano(valor.get("activa"), f"{donde}.activa", problemas)
    if len(problemas) > antes:
        return None
    return RestriccionConfigurada(codigo, tipo, dimension, valor_dimension, limite, activa)


def _repetidos(valores: list) -> list:
    vistos, repetidos = set(), []
    for v in valores:
        if v in vistos and v not in repetidos:
            repetidos.append(v)
        vistos.add(v)
    return repetidos


def leer(documento: Any) -> ConfiguracionOptimizacion:
    """La configuracion de un documento, o ConfiguracionInvalida con cada problema. No lee la base:
    las bases y los gestores que nombra se comprueban al ejecutar, contra el ruteo declarado."""
    if not isinstance(documento, dict):
        raise ConfiguracionInvalida(["el documento es un objeto JSON"])
    problemas: list[str] = []
    _llaves(documento, LLAVES_DEL_DOCUMENTO, "el documento", problemas)
    faltan = sorted(REQUERIDAS - set(documento))
    if faltan:
        problemas.append(f"el documento: falta {', '.join(faltan)}")
    nombre = _texto(documento.get("nombre"), "nombre", problemas)
    imputacion = _booleano(
        documento.get("permitir_predicciones_con_imputacion"),
        "permitir_predicciones_con_imputacion",
        problemas,
    )
    maximos = [
        _entero(documento.get(llave), llave, problemas, 1, PUBLIC_V1.limite_exacto, nulo=True)
        for llave in ("max_cuentas_atendidas", "max_unidades_recurso")
    ]
    supuestos = documento.get("supuestos")
    if supuestos is not None:
        supuestos = _texto(supuestos, "supuestos", problemas, LARGO_DE_SUPUESTOS)
    crudos = {
        llave: documento.get(llave, [] if llave == "restricciones" else None)
        for llave in ("admisibilidad", "pools", "restricciones")
    }
    for llave, valor in crudos.items():
        if not isinstance(valor, list) or (llave != "restricciones" and not valor):
            problemas.append(
                f"{llave}: una lista" + ("" if llave == "restricciones" else " con al menos uno")
            )
            crudos[llave] = []
    admisibilidad = [_admisibilidad(v, i, problemas) for i, v in enumerate(crudos["admisibilidad"])]
    pools = [_pool(v, i, problemas) for i, v in enumerate(crudos["pools"])]
    restricciones = [_restriccion(v, i, problemas) for i, v in enumerate(crudos["restricciones"])]
    admisibilidad = [a for a in admisibilidad if a is not None]
    pools = [p for p in pools if p is not None]
    restricciones = [r for r in restricciones if r is not None]
    for par in _repetidos([(a.canal_decision, a.canal_operativo) for a in admisibilidad]):
        problemas.append(f"admisibilidad: {par[0]} -> {par[1]} esta dos veces")
    for codigo in _repetidos([p.codigo for p in pools]):
        problemas.append(f"pools: el codigo {codigo} esta dos veces")
    for gestor in _repetidos([p.referencia_alcance for p in pools if p.canal == "CAMPO"]):
        problemas.append(f"pools: el gestor {gestor} tiene dos pools de CAMPO; uno por gestor")
    for codigo in _repetidos([r.codigo for r in restricciones]):
        problemas.append(f"restricciones: el codigo {codigo} esta dos veces")
    if pools and not any(p.activo for p in pools):
        problemas.append("pools: al menos uno activo")
    if problemas:
        raise ConfiguracionInvalida(problemas)
    return ConfiguracionOptimizacion(
        nombre=nombre,
        permitir_predicciones_con_imputacion=imputacion,
        max_cuentas_atendidas=maximos[0],
        max_unidades_recurso=maximos[1],
        admisibilidad=tuple(sorted(admisibilidad)),
        pools=tuple(sorted(pools, key=lambda p: p.codigo)),
        restricciones=tuple(sorted(restricciones, key=lambda r: r.codigo)),
        supuestos=supuestos,
    )


def advertencias(configuracion: ConfiguracionOptimizacion) -> list[str]:
    """Lo que la configuracion permite y no tiene efecto, sin ser un error: un canal operativo
    admisible sin ningun pool activo (sus cuentas quedan NO_ELEGIBLE con CONFIG_EXCLUYE_CANAL), un
    pool cuyo canal no es admisible para ningun canal de decision, una restriccion inactiva."""
    activos = {p.canal for p in configuracion.pools if p.activo}
    admisibles = {a.canal_operativo for a in configuracion.admisibilidad if a.permitido}
    avisos = [
        f"el canal {c} es admisible y no tiene ningun pool activo"
        for c in sorted(admisibles - activos)
    ]
    avisos += [
        f"el pool {p.codigo} es de {p.canal}, que ningun canal de decision admite"
        for p in configuracion.pools
        if p.activo and p.canal not in admisibles
    ]
    avisos += [
        f"la restriccion {r.codigo} esta inactiva"
        for r in configuracion.restricciones
        if not r.activa
    ]
    return avisos


# --- registrar ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Registrada:
    version: VersionConfiguracionOptimizacion
    creada: bool
    """False si ya habia una version con el mismo contenido: se reutilizo."""
    publicada_ahora: bool = False


class ConfiguracionNoEncontrada(LookupError):
    pass


def _por_hash(s: Session, huella: str) -> VersionConfiguracionOptimizacion | None:
    return s.exec(
        select(VersionConfiguracionOptimizacion)
        .where(VersionConfiguracionOptimizacion.hash_contenido == huella)
        .with_for_update()
    ).one_or_none()


def registrar(
    s: Session, configuracion: ConfiguracionOptimizacion, *, publicar: bool
) -> Registrada:
    """La version de esa configuracion, en la transaccion de quien llama: BORRADOR, o PUBLICADA con
    `publicar`. Si ya hay una con el mismo hash, esa (y, si era BORRADOR y se pide publicar, la
    publica); si no, la version con su matriz, sus pools y sus restricciones, enviados y sin
    confirmar. Dos registros simultaneos del mismo contenido dan una sola version."""
    huella = configuracion.hash()
    existente = _por_hash(s, huella)
    if existente is not None:
        if publicar and existente.estado == EstadoVersionOptimizacion.BORRADOR:
            return Registrada(_publicar(s, existente), creada=False, publicada_ahora=True)
        return Registrada(existente, creada=False)
    instante = ahora()
    activos = [p for p in configuracion.pools if p.activo]
    version = VersionConfiguracionOptimizacion(
        nombre=configuracion.nombre,
        version_configuracion=VERSION_CONFIGURACION,
        hash_contenido=huella,
        estado=EstadoVersionOptimizacion.PUBLICADA
        if publicar
        else EstadoVersionOptimizacion.BORRADOR,
        permitir_predicciones_con_imputacion=configuracion.permitir_predicciones_con_imputacion,
        max_cuentas_atendidas=configuracion.max_cuentas_atendidas,
        max_unidades_recurso=configuracion.max_unidades_recurso,
        pools=len(configuracion.pools),
        pools_activos=len(activos),
        restricciones=len(configuracion.restricciones),
        restricciones_activas=sum(r.activa for r in configuracion.restricciones),
        creada_en=instante,
        publicada_en=instante if publicar else None,
        metadatos={
            "forma": VERSION_CONFIGURACION,
            "supuestos": configuracion.supuestos,
            "pools_por_canal": {
                c.value: sum(1 for p in activos if p.canal == c) for c in CANALES_OPERATIVOS
            },
            "advertencias": advertencias(configuracion),
        },
    )
    error = insertar_en_savepoint(s, version)
    if error is not None:
        if restriccion(error) != RESTRICCION_HASH:
            raise error
        existente = _por_hash(s, huella)
        if publicar and existente.estado == EstadoVersionOptimizacion.BORRADOR:
            return Registrada(_publicar(s, existente), creada=False, publicada_ahora=True)
        return Registrada(existente, creada=False)
    _escribir(s, version, configuracion)
    return Registrada(version, creada=True, publicada_ahora=publicar)


def _escribir(
    s: Session, version: VersionConfiguracionOptimizacion, configuracion: ConfiguracionOptimizacion
) -> None:
    copiar(
        s,
        "admisibilidad_canal_optimizacion",
        ("version_configuracion_optimizacion_id", "canal_decision", "canal_operativo", "permitido"),
        (
            (version.id, a.canal_decision, a.canal_operativo, a.permitido)
            for a in configuracion.admisibilidad
        ),
    )
    copiar(
        s,
        "pool_capacidad_optimizacion",
        (
            "version_configuracion_optimizacion_id",
            "codigo",
            "canal",
            "tipo_alcance",
            "referencia_alcance",
            "capacidad",
            "unidades_recurso_por_asignacion",
            "activo",
        ),
        (
            (
                version.id,
                p.codigo,
                p.canal,
                p.tipo_alcance,
                p.referencia_alcance,
                p.capacidad,
                p.unidades_recurso_por_asignacion,
                p.activo,
            )
            for p in configuracion.pools
        ),
    )
    copiar(
        s,
        "restriccion_optimizacion",
        (
            "version_configuracion_optimizacion_id",
            "codigo",
            "tipo",
            "dimension",
            "valor_dimension",
            "limite",
            "activa",
        ),
        (
            (version.id, r.codigo, r.tipo, r.dimension, r.valor_dimension, r.limite, r.activa)
            for r in configuracion.restricciones
        ),
    )


def _publicar(
    s: Session, version: VersionConfiguracionOptimizacion
) -> VersionConfiguracionOptimizacion:
    version.estado = EstadoVersionOptimizacion.PUBLICADA
    version.publicada_en = ahora()
    s.add(version)
    s.flush()
    return version


def publicar(s: Session, configuracion_id: UUID) -> Registrada:
    """Publica una version BORRADOR, en la transaccion de quien llama. Una PUBLICADA se queda como
    esta. Levanta ConfiguracionNoEncontrada."""
    version = s.exec(
        select(VersionConfiguracionOptimizacion)
        .where(VersionConfiguracionOptimizacion.configuracion_id == configuracion_id)
        .with_for_update()
    ).one_or_none()
    if version is None:
        raise ConfiguracionNoEncontrada(configuracion_id)
    if version.estado == EstadoVersionOptimizacion.PUBLICADA:
        return Registrada(version, creada=False)
    return Registrada(_publicar(s, version), creada=False, publicada_ahora=True)


def configuracion_de(
    s: Session, version: VersionConfiguracionOptimizacion
) -> ConfiguracionOptimizacion:
    """La configuracion que guarda una version, tal como se registro: su hash tiene que ser el de
    la version (lo comprueba quien la usa)."""
    from motor_cartera.db.modelos import (
        AdmisibilidadCanalOptimizacion,
        PoolCapacidadOptimizacion,
        RestriccionOptimizacion,
    )

    admisibilidad = s.exec(
        select(AdmisibilidadCanalOptimizacion).where(
            AdmisibilidadCanalOptimizacion.version_configuracion_optimizacion_id == version.id
        )
    ).all()
    pools = s.exec(
        select(PoolCapacidadOptimizacion).where(
            PoolCapacidadOptimizacion.version_configuracion_optimizacion_id == version.id
        )
    ).all()
    restricciones = s.exec(
        select(RestriccionOptimizacion).where(
            RestriccionOptimizacion.version_configuracion_optimizacion_id == version.id
        )
    ).all()
    return ConfiguracionOptimizacion(
        nombre=version.nombre,
        permitir_predicciones_con_imputacion=version.permitir_predicciones_con_imputacion,
        max_cuentas_atendidas=version.max_cuentas_atendidas,
        max_unidades_recurso=version.max_unidades_recurso,
        admisibilidad=tuple(
            sorted(
                Admisibilidad(a.canal_decision, a.canal_operativo, a.permitido)
                for a in admisibilidad
            )
        ),
        pools=tuple(
            sorted(
                (
                    PoolConfigurado(
                        p.codigo,
                        p.canal,
                        p.tipo_alcance,
                        p.referencia_alcance,
                        p.capacidad,
                        p.unidades_recurso_por_asignacion,
                        p.activo,
                    )
                    for p in pools
                ),
                key=lambda p: p.codigo,
            )
        ),
        restricciones=tuple(
            sorted(
                (
                    RestriccionConfigurada(
                        r.codigo, r.tipo, r.dimension, r.valor_dimension, r.limite, r.activa
                    )
                    for r in restricciones
                ),
                key=lambda r: r.codigo,
            )
        ),
        supuestos=(version.metadatos or {}).get("supuestos"),
    )
