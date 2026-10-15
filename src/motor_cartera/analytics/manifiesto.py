"""El manifiesto de una ejecucion de analytics/v1: que entradas declara, si se pueden usar juntas y
que firma tienen.

Nada se elige en silencio: la cohorte es el corte declarado, y cada otra entrada (una
interpretacion de pagos por ventana, su atribucion, la evaluacion de promesas, decision/v2, la
geografia, territorial/v2, campo/v2 y ruteo/v2) se declara por su identificador publico. Antes de
registrar nada se comprueba, fail-closed:

- que cada entrada existe, termino EXITOSA y es de la misma cartera;
- que estaba disponible a known_at: termino (terminada_en) o se creo (creado_en, el corte) hasta
  known_at. Ninguna marca de disponibilidad se inventa;
- que las ventanas de pagos tocan el horizonte y ninguna aporta dos interpretaciones;
- que cada atribucion leyo exactamente la interpretacion declarada de su ventana;
- que la evaluacion de promesas tiene el as_of y la zona de la ejecucion;
- que decision/v2, la geografia y territorial/v2 son del corte, que territorial/v2 es de la
  geografia declarada, campo/v2 de la decision y la territorial/v2 declaradas, y ruteo/v2 del
  plan declarado, cuya fecha de operacion cae en el horizonte.

Las entradas se ordenan en forma canonica y su SHA-256 es el hash del manifiesto: el mismo conjunto
logico de entradas da el mismo hash, en cualquier orden en que se declaren.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlmodel import Session, select

from motor_cartera.analytics.catalogo import (
    VERSION_CATALOGO,
    VERSION_MOTOR,
    Fuente,
    canonico,
    hash_catalogo,
)
from motor_cartera.analytics.politica import (
    POLITICAS,
    PUBLIC_V1,
    Granularidad,
    Modo,
    PoliticaAnalytics,
    cuantos_periodos,
    hash_politica,
)
from motor_cartera.atribucion.reglas import VERSION_ATRIBUCION
from motor_cartera.db.modelos import (
    CorteCanonico,
    EjecucionAtribucion,
    EjecucionCampoV2,
    EjecucionDecisionV2,
    EjecucionEvaluacionPromesas,
    EjecucionGeografia,
    EjecucionMotorPagos,
    EjecucionRuteoV2,
    EjecucionTerritorialV2,
    EstadoAtribucion,
    EstadoCampoV2,
    EstadoDecisionV2,
    EstadoEvaluacionPromesas,
    EstadoGeografia,
    EstadoMotorPagos,
    EstadoRuteoV2,
    EstadoTerritorialV2,
)
from motor_cartera.evaluacion.reglas import VERSION_EVALUACION
from motor_cartera.motor_pagos.reglas import VERSION_MOTOR_PAGOS, periodos_entre

ORDEN_DE_TIPO = {f: i for i, f in enumerate(Fuente)}
"""El orden canonico de los tipos de entrada: el del catalogo."""


# --- errores al abrir -----------------------------------------------------------------------------


class EntradaNoAnalizable(Exception):
    """Una peticion de analytics/v1 que no se puede abrir: no registra nada. `codigo` es
    estable."""

    codigo = "ENTRADA_NO_ANALIZABLE"
    estado_http = 422


def _error(nombre: str, codigo: str, estado_http: int = 422) -> type[EntradaNoAnalizable]:
    return type(nombre, (EntradaNoAnalizable,), {"codigo": codigo, "estado_http": estado_http})


CorteNoEncontrado = _error("CorteNoEncontrado", "CORTE_NO_ENCONTRADO", 404)
EntradaNoEncontrada = _error("EntradaNoEncontrada", "ENTRADA_NO_ENCONTRADA", 404)
AsOfAnteriorAlCorte = _error("AsOfAnteriorAlCorte", "AS_OF_ANTERIOR_AL_CORTE")
AsOfFuturo = _error("AsOfFuturo", "AS_OF_FUTURO")
ZonaHorariaInvalida = _error("ZonaHorariaInvalida", "ZONA_HORARIA_INVALIDA")
KnownAtRequerido = _error("KnownAtRequerido", "KNOWN_AT_REQUERIDO")
KnownAtSinZona = _error("KnownAtSinZona", "KNOWN_AT_SIN_ZONA")
KnownAtFuturo = _error("KnownAtFuturo", "KNOWN_AT_FUTURO")
CorteNoConocido = _error("CorteNoConocido", "CORTE_NO_CONOCIDO")
HorizonteDemasiadoLargo = _error("HorizonteDemasiadoLargo", "HORIZONTE_DEMASIADO_LARGO")
CatalogoNoSoportado = _error("CatalogoNoSoportado", "CATALOGO_NO_SOPORTADO")
PoliticaNoSoportada = _error("PoliticaNoSoportada", "POLITICA_NO_SOPORTADA")
EntradaNoExitosa = _error("EntradaNoExitosa", "ENTRADA_NO_EXITOSA")
EntradaNoConocida = _error("EntradaNoConocida", "ENTRADA_NO_CONOCIDA")
VersionDeEntradaNoSoportada = _error(
    "VersionDeEntradaNoSoportada", "VERSION_DE_ENTRADA_NO_SOPORTADA"
)
CarteraInconsistente = _error("CarteraInconsistente", "CARTERA_INCONSISTENTE")
CorteInconsistente = _error("CorteInconsistente", "CORTE_INCONSISTENTE")
VentanaFueraDelHorizonte = _error("VentanaFueraDelHorizonte", "VENTANA_FUERA_DEL_HORIZONTE")
VentanaDuplicada = _error("VentanaDuplicada", "VENTANA_DUPLICADA")
AtribucionInconsistente = _error("AtribucionInconsistente", "ATRIBUCION_INCONSISTENTE")
EvaluacionInconsistente = _error("EvaluacionInconsistente", "EVALUACION_INCONSISTENTE")
EncadenamientoIncorrecto = _error("EncadenamientoIncorrecto", "ENCADENAMIENTO_INCORRECTO")
FechaOperacionFueraDelHorizonte = _error(
    "FechaOperacionFueraDelHorizonte", "FECHA_OPERACION_FUERA_DEL_HORIZONTE"
)
EntradaRepetida = _error("EntradaRepetida", "ENTRADA_REPETIDA")


# --- la peticion y lo que resulta -----------------------------------------------------------------


@dataclass(frozen=True)
class Peticion:
    """Lo que declara quien pide una ejecucion. Ninguna entrada se infiere."""

    corte_id: UUID
    as_of: date
    modo: Modo
    zona_horaria: str
    granularidad: Granularidad
    known_at: datetime | None = None
    motor_pagos: tuple[UUID, ...] = ()
    atribucion: tuple[UUID, ...] = ()
    evaluacion_promesas: UUID | None = None
    decision_v2: UUID | None = None
    geografia: UUID | None = None
    territorial_v2: UUID | None = None
    campo_v2: UUID | None = None
    ruteo_v2: UUID | None = None
    version_catalogo: str | None = None
    version_politica: str | None = None


@dataclass(frozen=True)
class Entrada:
    """Una entrada resuelta: lo que se guarda en EntradaAnalytics."""

    tipo: Fuente
    referencia_publica: UUID
    id_interno: int
    version: str
    firma: str
    periodo_desde: date | None
    periodo_hasta: date | None
    disponible_en: datetime
    metadatos: dict = field(default_factory=dict, compare=False)

    def canonica(self) -> dict:
        return {
            "tipo": self.tipo.value,
            "referencia_publica": str(self.referencia_publica),
            "version": self.version,
            "firma": self.firma,
            "periodo_desde": None if self.periodo_desde is None else self.periodo_desde.isoformat(),
            "periodo_hasta": None if self.periodo_hasta is None else self.periodo_hasta.isoformat(),
        }

    def orden(self) -> tuple:
        return (
            ORDEN_DE_TIPO[self.tipo],
            "" if self.periodo_desde is None else self.periodo_desde.isoformat(),
            str(self.referencia_publica),
        )


@dataclass(frozen=True)
class Resuelto:
    """Un manifiesto comprobado, con lo que fija la ejecucion."""

    corte: CorteCanonico
    as_of: date
    known_at: datetime
    modo: Modo
    zona_horaria: str
    granularidad: Granularidad
    politica: PoliticaAnalytics
    entradas: tuple[Entrada, ...]
    hash_manifiesto: str
    firma: str
    advertencias: tuple[str, ...]

    def de(self, tipo: Fuente) -> list[Entrada]:
        return [e for e in self.entradas if e.tipo == tipo]


def hash_manifiesto(entradas) -> str:
    """SHA-256 de las entradas en forma canonica, en su orden canonico."""
    ordenadas = sorted(entradas, key=Entrada.orden)
    return hashlib.sha256(canonico([e.canonica() for e in ordenadas])).hexdigest()


def firma_inputs(
    *,
    despacho_id: str,
    cartera_id: str,
    corte_id: UUID,
    fecha_corte: date,
    as_of: date,
    known_at: datetime,
    zona: str,
    granularidad: Granularidad,
    hash_del_manifiesto: str,
    politica: PoliticaAnalytics = PUBLIC_V1,
) -> str:
    """El SHA-256 de todo lo que fija el resultado: el motor, el catalogo y la politica con sus
    hashes, la cartera, el corte, as_of, known_at (en UTC, al microsegundo), la zona, la
    granularidad y el manifiesto. El modo no entra: no cambia nada de lo que se calcula."""
    if known_at.tzinfo is None:
        raise ValueError("known_at es un instante: lleva su zona horaria.")
    lineas = [
        VERSION_MOTOR,
        VERSION_CATALOGO,
        hash_catalogo(),
        politica.version,
        hash_politica(politica),
        despacho_id,
        cartera_id,
        str(corte_id),
        fecha_corte.isoformat(),
        as_of.isoformat(),
        known_at.astimezone(UTC).isoformat(timespec="microseconds"),
        zona,
        granularidad.value,
        hash_del_manifiesto,
    ]
    return hashlib.sha256("\n".join(lineas).encode("utf-8")).hexdigest()


# --- resolver -------------------------------------------------------------------------------------


def _una(s: Session, modelo, columna, valor, tipo: Fuente):
    fila = s.exec(select(modelo).where(columna == valor)).one_or_none()
    if fila is None:
        raise EntradaNoEncontrada(f"No existe la entrada {tipo.value} {valor}.")
    return fila


def _exitosa(ejecucion, exitosa, tipo: Fuente, publico: UUID) -> None:
    if ejecucion.estado != exitosa:
        raise EntradaNoExitosa(
            f"La entrada {tipo.value} {publico} esta {ejecucion.estado}: analytics solo lee "
            "entradas EXITOSA, nunca una a medias."
        )


def _conocida(disponible_en: datetime | None, known_at: datetime, tipo: Fuente, publico) -> None:
    if disponible_en is None or disponible_en > known_at:
        cuando = "nunca" if disponible_en is None else disponible_en.isoformat()
        raise EntradaNoConocida(
            f"La entrada {tipo.value} {publico} estuvo disponible el {cuando}, despues de known_at "
            f"({known_at.isoformat()}): a esa frontera no existia."
        )


def _de_la_cartera(ejecucion, corte: CorteCanonico, tipo: Fuente, publico: UUID) -> None:
    if (ejecucion.despacho_id, ejecucion.cartera_id) != (corte.despacho_id, corte.cartera_id):
        raise CarteraInconsistente(
            f"La entrada {tipo.value} {publico} es de {ejecucion.despacho_id}/"
            f"{ejecucion.cartera_id}, no de la cartera del corte."
        )


def _del_corte(ejecucion, corte: CorteCanonico, tipo: Fuente, publico: UUID) -> None:
    if ejecucion.corte_canonico_id != corte.id:
        raise CorteInconsistente(
            f"La entrada {tipo.value} {publico} es de otro corte, no del {corte.fecha_corte}: la "
            "cohorte y sus dimensiones son del corte declarado."
        )


def _version(actual: str, esperada: str, tipo: Fuente, publico: UUID) -> None:
    if actual != esperada:
        raise VersionDeEntradaNoSoportada(
            f"La entrada {tipo.value} {publico} es de {actual}, y analytics/v1 lee {esperada}."
        )


def _sin_repetir(ids: tuple[UUID, ...], tipo: Fuente) -> None:
    if len(set(ids)) != len(ids):
        raise EntradaRepetida(f"Una entrada {tipo.value} esta declarada dos veces.")


def resolver(s: Session, peticion: Peticion) -> Resuelto:
    """El manifiesto comprobado y su firma. Levanta, sin registrar nada, una EntradaNoAnalizable con
    su codigo estable."""
    if peticion.version_catalogo not in (None, VERSION_CATALOGO):
        raise CatalogoNoSoportado(
            f"Este servicio analiza con {VERSION_CATALOGO}, no con {peticion.version_catalogo}."
        )
    politica = POLITICAS.get(peticion.version_politica or PUBLIC_V1.version)
    if politica is None:
        raise PoliticaNoSoportada(
            f"Este servicio aplica {', '.join(sorted(POLITICAS))}, no {peticion.version_politica}."
        )
    corte = s.exec(
        select(CorteCanonico).where(CorteCanonico.corte_id == peticion.corte_id)
    ).one_or_none()
    if corte is None:
        raise CorteNoEncontrado(f"No existe un corte canonico con corte_id {peticion.corte_id}.")
    if peticion.as_of < corte.fecha_corte:
        raise AsOfAnteriorAlCorte(
            f"as_of ({peticion.as_of}) es anterior al corte del {corte.fecha_corte}: el horizonte "
            "empieza en fecha_corte."
        )
    zona = peticion.zona_horaria
    if not s.execute(
        text("SELECT EXISTS (SELECT 1 FROM pg_timezone_names WHERE name = :zona)"), {"zona": zona}
    ).scalar_one():
        raise ZonaHorariaInvalida(f"{zona!r} no es una zona horaria IANA que conozca la base.")
    periodos = cuantos_periodos(corte.fecha_corte, peticion.as_of, peticion.granularidad)
    if periodos > politica.max_periodos:
        raise HorizonteDemasiadoLargo(
            f"El horizonte tiene {periodos:,} periodos de {peticion.granularidad}; "
            f"{politica.version} admite hasta {politica.max_periodos}."
        )
    reloj = s.execute(text("SELECT now()")).scalar_one()
    known_at = peticion.known_at
    if known_at is None:
        if peticion.modo == Modo.BACKTEST:
            raise KnownAtRequerido(
                "Un BACKTEST declara su frontera de conocimiento: known_at es obligatorio. Sin el "
                "se describiria el pasado con todo lo que se sabe hoy."
            )
        known_at = reloj
    elif known_at.tzinfo is None:
        raise KnownAtSinZona(
            "known_at es un instante: lleva su zona horaria. Sin ella no se sabe cuando es."
        )
    elif known_at > reloj:
        raise KnownAtFuturo(
            f"known_at ({known_at.isoformat()}) es posterior al reloj de la base "
            f"({reloj.isoformat()}): no se usa conocimiento que todavia no existe."
        )
    hoy = s.execute(
        text("SELECT (CAST(:k AS timestamptz) AT TIME ZONE :zona)::date"),
        {"k": known_at, "zona": zona},
    ).scalar_one()
    if peticion.as_of > hoy:
        raise AsOfFuturo(
            f"as_of ({peticion.as_of}) es posterior al dia de known_at en {zona} ({hoy}): ese dia "
            "de negocio no habia empezado."
        )
    advertencias = []
    if peticion.as_of == hoy:
        advertencias.append(
            "HORIZONTE_EN_CURSO: as_of es el dia de known_at; lo que ocurra despues de known_at "
            "ese dia no esta."
        )
    if corte.creado_en > known_at:
        raise CorteNoConocido(
            f"El corte {corte.corte_id} se materializo el {corte.creado_en.isoformat()}, despues "
            f"de known_at ({known_at.isoformat()}): a esa frontera no existia la cohorte."
        )
    entradas = [
        Entrada(
            Fuente.CORTE,
            corte.corte_id,
            corte.id,
            corte.version_modelo,
            corte.firma_contenido,
            corte.fecha_corte,
            corte.fecha_corte,
            corte.creado_en,
            {"cuentas": corte.cuentas},
        )
    ]
    entradas += _pagos(s, peticion, corte, known_at)
    entradas += _atribuciones(s, peticion, corte, known_at, entradas)
    entradas += _evaluacion(s, peticion, corte, known_at, zona)
    entradas += _operacion(s, peticion, corte, known_at)
    del_manifiesto = hash_manifiesto(entradas)
    firma = firma_inputs(
        despacho_id=corte.despacho_id,
        cartera_id=corte.cartera_id,
        corte_id=corte.corte_id,
        fecha_corte=corte.fecha_corte,
        as_of=peticion.as_of,
        known_at=known_at,
        zona=zona,
        granularidad=peticion.granularidad,
        hash_del_manifiesto=del_manifiesto,
        politica=politica,
    )
    return Resuelto(
        corte=corte,
        as_of=peticion.as_of,
        known_at=known_at,
        modo=peticion.modo,
        zona_horaria=zona,
        granularidad=peticion.granularidad,
        politica=politica,
        entradas=tuple(sorted(entradas, key=Entrada.orden)),
        hash_manifiesto=del_manifiesto,
        firma=firma,
        advertencias=tuple(advertencias),
    )


def meses_del_horizonte(fecha_corte: date, as_of: date) -> list[date]:
    """Las ventanas mensuales (motor-pagos/v1, atribucion/v1) que toca el horizonte."""
    return periodos_entre(fecha_corte, as_of)


def _pagos(
    s: Session, peticion: Peticion, corte: CorteCanonico, known_at: datetime
) -> list[Entrada]:
    _sin_repetir(peticion.motor_pagos, Fuente.MOTOR_PAGOS)
    meses = set(meses_del_horizonte(corte.fecha_corte, peticion.as_of))
    resultado, vistas = [], set()
    for publico in peticion.motor_pagos:
        e = _una(
            s,
            EjecucionMotorPagos,
            EjecucionMotorPagos.motor_pagos_run_id,
            publico,
            Fuente.MOTOR_PAGOS,
        )
        _exitosa(e, EstadoMotorPagos.EXITOSA, Fuente.MOTOR_PAGOS, publico)
        _version(e.version_motor, VERSION_MOTOR_PAGOS, Fuente.MOTOR_PAGOS, publico)
        _de_la_cartera(e, corte, Fuente.MOTOR_PAGOS, publico)
        _conocida(e.terminada_en, known_at, Fuente.MOTOR_PAGOS, publico)
        if e.periodo_desde not in meses:
            raise VentanaFueraDelHorizonte(
                f"La interpretacion {publico} es de la ventana {e.periodo_desde:%Y-%m}, que no "
                f"toca el horizonte del {corte.fecha_corte} al {peticion.as_of}."
            )
        if e.periodo_desde in vistas:
            raise VentanaDuplicada(
                f"La ventana {e.periodo_desde:%Y-%m} tiene dos interpretaciones declaradas: una "
                "ventana aporta una sola, o sus movimientos contarian dos veces."
            )
        vistas.add(e.periodo_desde)
        resultado.append(
            Entrada(
                Fuente.MOTOR_PAGOS,
                publico,
                e.id,
                e.version_motor,
                e.firma_entrada,
                e.periodo_desde,
                e.periodo_hasta - timedelta(days=1),
                e.terminada_en,
                {
                    "observaciones_leidas": e.observaciones_leidas,
                    "movimientos_canonicos": e.movimientos_canonicos,
                },
            )
        )
    return resultado


def _atribuciones(
    s: Session,
    peticion: Peticion,
    corte: CorteCanonico,
    known_at: datetime,
    declaradas: list[Entrada],
) -> list[Entrada]:
    _sin_repetir(peticion.atribucion, Fuente.ATRIBUCION)
    interpretacion = {
        e.periodo_desde: e.id_interno for e in declaradas if e.tipo == Fuente.MOTOR_PAGOS
    }
    resultado, vistas = [], set()
    for publico in peticion.atribucion:
        e = _una(
            s,
            EjecucionAtribucion,
            EjecucionAtribucion.atribucion_run_id,
            publico,
            Fuente.ATRIBUCION,
        )
        _exitosa(e, EstadoAtribucion.EXITOSA, Fuente.ATRIBUCION, publico)
        _version(e.version_atribucion, VERSION_ATRIBUCION, Fuente.ATRIBUCION, publico)
        _de_la_cartera(e, corte, Fuente.ATRIBUCION, publico)
        _conocida(e.terminada_en, known_at, Fuente.ATRIBUCION, publico)
        if e.periodo_desde in vistas:
            raise VentanaDuplicada(
                f"La ventana {e.periodo_desde:%Y-%m} tiene dos atribuciones declaradas."
            )
        vistas.add(e.periodo_desde)
        if interpretacion.get(e.periodo_desde) != e.ejecucion_motor_pagos_id:
            raise AtribucionInconsistente(
                f"La atribucion {publico} de la ventana {e.periodo_desde:%Y-%m} no leyo la "
                "interpretacion de pagos que declara el manifiesto para esa ventana: se declaran "
                "juntas, la que la atribucion leyo."
            )
        resultado.append(
            Entrada(
                Fuente.ATRIBUCION,
                publico,
                e.id,
                e.version_atribucion,
                e.firma_entrada,
                e.periodo_desde,
                e.periodo_hasta - timedelta(days=1),
                e.terminada_en,
                {"ventana_dias": e.ventana_dias, "movimientos_evaluados": e.movimientos_evaluados},
            )
        )
    return resultado


def _evaluacion(
    s: Session, peticion: Peticion, corte: CorteCanonico, known_at: datetime, zona: str
) -> list[Entrada]:
    publico = peticion.evaluacion_promesas
    if publico is None:
        return []
    tipo = Fuente.EVALUACION_PROMESAS
    e = _una(
        s, EjecucionEvaluacionPromesas, EjecucionEvaluacionPromesas.evaluacion_run_id, publico, tipo
    )
    _exitosa(e, EstadoEvaluacionPromesas.EXITOSA, tipo, publico)
    _version(e.version_evaluacion, VERSION_EVALUACION, tipo, publico)
    _de_la_cartera(e, corte, tipo, publico)
    _conocida(e.terminada_en, known_at, tipo, publico)
    if (e.as_of, e.zona_horaria) != (peticion.as_of, zona):
        raise EvaluacionInconsistente(
            f"La evaluacion {publico} es al {e.as_of} en {e.zona_horaria}, y la ejecucion es al "
            f"{peticion.as_of} en {zona}: el estado de una promesa es el de ese dia, en esa zona."
        )
    return [
        Entrada(
            tipo,
            publico,
            e.id,
            e.version_evaluacion,
            e.firma_entrada,
            e.as_of,
            e.as_of,
            e.terminada_en,
            {"promesas_evaluadas": e.promesas_evaluadas},
        )
    ]


def _operacion(
    s: Session, peticion: Peticion, corte: CorteCanonico, known_at: datetime
) -> list[Entrada]:
    resultado: list[Entrada] = []
    decision = geografia = territorial = campo = None
    if peticion.decision_v2 is not None:
        tipo, publico = Fuente.DECISION_V2, peticion.decision_v2
        decision = _una(s, EjecucionDecisionV2, EjecucionDecisionV2.decision_run_id, publico, tipo)
        _exitosa(decision, EstadoDecisionV2.EXITOSA, tipo, publico)
        _del_corte(decision, corte, tipo, publico)
        _conocida(decision.terminada_en, known_at, tipo, publico)
        resultado.append(
            Entrada(
                tipo,
                publico,
                decision.id,
                decision.version_motor,
                decision.firma_inputs,
                decision.as_of,
                decision.as_of,
                decision.terminada_en,
                {
                    "version_politica": decision.version_politica,
                    "hash_politica": decision.hash_politica,
                    "cuentas_decididas": decision.cuentas_decididas,
                },
            )
        )
    if peticion.geografia is not None:
        tipo, publico = Fuente.GEOGRAFIA, peticion.geografia
        geografia = _una(s, EjecucionGeografia, EjecucionGeografia.geografia_run_id, publico, tipo)
        _exitosa(geografia, EstadoGeografia.EXITOSA, tipo, publico)
        _del_corte(geografia, corte, tipo, publico)
        _conocida(geografia.terminada_en, known_at, tipo, publico)
        resultado.append(
            Entrada(
                tipo,
                publico,
                geografia.id,
                geografia.version_motor,
                geografia.firma_inputs,
                None,
                None,
                geografia.terminada_en,
                {"cuentas_objetivo": geografia.cuentas_objetivo},
            )
        )
    if peticion.territorial_v2 is not None:
        tipo, publico = Fuente.TERRITORIAL_V2, peticion.territorial_v2
        territorial = _una(
            s, EjecucionTerritorialV2, EjecucionTerritorialV2.territorial_run_id, publico, tipo
        )
        _exitosa(territorial, EstadoTerritorialV2.EXITOSA, tipo, publico)
        _del_corte(territorial, corte, tipo, publico)
        _conocida(territorial.terminada_en, known_at, tipo, publico)
        if geografia is None or territorial.ejecucion_geografia_id != geografia.id:
            raise EncadenamientoIncorrecto(
                f"territorial/v2 {publico} se declara con la geografia que leyo: declarala en el "
                "manifiesto."
            )
        resultado.append(
            Entrada(
                tipo,
                publico,
                territorial.id,
                territorial.version_motor,
                territorial.firma_inputs,
                None,
                None,
                territorial.terminada_en,
                {
                    "version_politica": territorial.version_politica,
                    "hash_politica": territorial.hash_politica,
                },
            )
        )
    if peticion.campo_v2 is not None:
        tipo, publico = Fuente.CAMPO_V2, peticion.campo_v2
        campo = _una(s, EjecucionCampoV2, EjecucionCampoV2.campo_run_id, publico, tipo)
        _exitosa(campo, EstadoCampoV2.EXITOSA, tipo, publico)
        _del_corte(campo, corte, tipo, publico)
        _conocida(campo.terminada_en, known_at, tipo, publico)
        if (
            decision is None
            or territorial is None
            or campo.ejecucion_decision_v2_id != decision.id
            or campo.ejecucion_territorial_v2_id != territorial.id
        ):
            raise EncadenamientoIncorrecto(
                f"campo/v2 {publico} se declara con la decision/v2 y la territorial/v2 que leyo: "
                "declaralas en el manifiesto."
            )
        if not corte.fecha_corte <= campo.fecha_operacion <= peticion.as_of:
            raise FechaOperacionFueraDelHorizonte(
                f"El plan {publico} es del {campo.fecha_operacion}, fuera del horizonte del "
                f"{corte.fecha_corte} al {peticion.as_of}."
            )
        resultado.append(
            Entrada(
                tipo,
                publico,
                campo.id,
                campo.version_motor,
                campo.firma_inputs,
                campo.fecha_operacion,
                campo.fecha_operacion,
                campo.terminada_en,
                {
                    "version_politica": campo.version_politica,
                    "hash_politica": campo.hash_politica,
                    "candidatas_campo": campo.candidatas_campo,
                },
            )
        )
    if peticion.ruteo_v2 is not None:
        tipo, publico = Fuente.RUTEO_V2, peticion.ruteo_v2
        ruteo = _una(s, EjecucionRuteoV2, EjecucionRuteoV2.ruteo_run_id, publico, tipo)
        _exitosa(ruteo, EstadoRuteoV2.EXITOSA, tipo, publico)
        _conocida(ruteo.terminada_en, known_at, tipo, publico)
        if campo is None or ruteo.ejecucion_campo_v2_id != campo.id:
            raise EncadenamientoIncorrecto(
                f"ruteo/v2 {publico} se declara con el plan de campo/v2 que ruteo: declaralo en el "
                "manifiesto."
            )
        resultado.append(
            Entrada(
                tipo,
                publico,
                ruteo.id,
                ruteo.version_motor,
                ruteo.firma_inputs,
                ruteo.fecha_operacion,
                ruteo.fecha_operacion,
                ruteo.terminada_en,
                {
                    "version_politica": ruteo.version_politica,
                    "hash_politica": ruteo.hash_politica,
                    "paradas_objetivo": ruteo.paradas_objetivo,
                },
            )
        )
    return resultado
