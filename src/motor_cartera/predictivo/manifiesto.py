"""El manifiesto de un entrenamiento: que anclas, con que relojes, que split, que cobertura
declarada y que interpretaciones de pagos definen los labels. Comprobarlo antes de registrar nada,
su hash y la firma del entrenamiento.

Nada se elige al ejecutar: ni "los ultimos N cortes", ni la ultima interpretacion, ni el split.
Cada ancla declara su corte, `feature_as_of` (la fecha del corte), `feature_known_at`,
`label_known_at`, `pagos_completos_hasta`, `lifecycle_completo_hasta` y su split (TRAIN, VALIDATION,
TEST o PURGE). Antes de registrar nada se comprueba, fail-closed y con un codigo estable:

- que cada corte existe, es de la misma cartera, esta una sola vez y su fecha es feature_as_of;
- los relojes: instantes con zona, feature_known_at <= label_known_at <= el reloj de la base, y el
  corte ya existia a feature_known_at;
- la cobertura declarada: ni los pagos ni el lifecycle pueden estar completos despues del dia de
  label_known_at (no se sabe lo que todavia no llega);
- el split: TRAIN, VALIDATION y TEST no vacios, en ese orden en el tiempo, con la purga de la
  politica entre bloques (el final de la ventana del label mas largo de un bloque es anterior a la
  primera ancla del siguiente), y cada bloque con anclas maduras de cada target;
- las interpretaciones de los labels: cada una EXITOSA, de motor-pagos/v1, de la cartera, una por
  mes, la VIGENTE de su mes a label_known_at de cada ancla que la usa (la mas reciente que ya habia
  terminado), y completa (clasifico cada pago observado conocido de su mes); y cada mes que toca
  una ventana madura de pagos tiene la suya.

Las anclas se ordenan en forma canonica y su SHA-256 es el hash del manifiesto: el mismo conjunto
logico, el mismo hash, en cualquier orden en que se declare.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlmodel import Session, select

from motor_cartera.analytics.catalogo import canonico
from motor_cartera.db.modelos import CorteCanonico, EjecucionMotorPagos, EstadoMotorPagos
from motor_cartera.motor_pagos.reglas import VERSION_MOTOR_PAGOS, periodos_entre
from motor_cartera.predictivo import features as features_
from motor_cartera.predictivo import targets as targets_
from motor_cartera.predictivo.politica import (
    POLITICAS,
    PUBLIC_V1,
    VERSION_MODELO,
    PoliticaEntrenamiento,
    Split,
    hash_politica,
)

# --- errores al abrir -----------------------------------------------------------------------------


class ManifiestoInvalido(Exception):
    """Un manifiesto que no se puede entrenar: no registra nada. `codigo` es estable."""

    codigo = "MANIFIESTO_INVALIDO"
    estado_http = 422


def _error(nombre: str, codigo: str, estado_http: int = 422) -> type[ManifiestoInvalido]:
    return type(nombre, (ManifiestoInvalido,), {"codigo": codigo, "estado_http": estado_http})


CorteNoEncontrado = _error("CorteNoEncontrado", "CORTE_NO_ENCONTRADO", 404)
InterpretacionNoEncontrada = _error(
    "InterpretacionNoEncontrada", "INTERPRETACION_NO_ENCONTRADA", 404
)
PoliticaNoSoportada = _error("PoliticaNoSoportada", "POLITICA_NO_SOPORTADA")
ZonaHorariaInvalida = _error("ZonaHorariaInvalida", "ZONA_HORARIA_INVALIDA")
AnclasInsuficientes = _error("AnclasInsuficientes", "ANCLAS_INSUFICIENTES")
AnclaRepetida = _error("AnclaRepetida", "ANCLA_REPETIDA")
CarteraInconsistente = _error("CarteraInconsistente", "CARTERA_INCONSISTENTE")
AsOfDistintoDelCorte = _error("AsOfDistintoDelCorte", "AS_OF_DISTINTO_DEL_CORTE")
KnownAtSinZona = _error("KnownAtSinZona", "KNOWN_AT_SIN_ZONA")
KnownAtFuturo = _error("KnownAtFuturo", "KNOWN_AT_FUTURO")
RelojesInvertidos = _error("RelojesInvertidos", "RELOJES_INVERTIDOS")
CorteNoConocido = _error("CorteNoConocido", "CORTE_NO_CONOCIDO")
CoberturaNoConocida = _error("CoberturaNoConocida", "COBERTURA_NO_CONOCIDA")
SplitInvalido = _error("SplitInvalido", "SPLIT_INVALIDO")
SplitDesordenado = _error("SplitDesordenado", "SPLIT_DESORDENADO")
PurgaInsuficiente = _error("PurgaInsuficiente", "PURGA_INSUFICIENTE")
SplitSinMaduras = _error("SplitSinMaduras", "SPLIT_SIN_MADURAS")
InterpretacionNoExitosa = _error("InterpretacionNoExitosa", "INTERPRETACION_NO_EXITOSA")
InterpretacionInconsistente = _error("InterpretacionInconsistente", "INTERPRETACION_INCONSISTENTE")
VentanaDuplicada = _error("VentanaDuplicada", "VENTANA_DUPLICADA")
InterpretacionNoConocida = _error("InterpretacionNoConocida", "INTERPRETACION_NO_CONOCIDA")
InterpretacionFaltante = _error("InterpretacionFaltante", "INTERPRETACION_FALTANTE")
InterpretacionIncompleta = _error("InterpretacionIncompleta", "INTERPRETACION_INCOMPLETA")
InterpretacionNoVigente = _error("InterpretacionNoVigente", "INTERPRETACION_NO_VIGENTE")

MINIMO_DE_ANCLAS = 3


# --- lo declarado ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class AnclaDeclarada:
    corte_id: UUID
    feature_as_of: date
    feature_known_at: datetime
    label_known_at: datetime
    pagos_completos_hasta: date | None
    lifecycle_completo_hasta: date | None
    split: str


@dataclass(frozen=True)
class ManifiestoDeclarado:
    """Lo que declara quien pide un entrenamiento. Ninguna entrada se infiere."""

    zona_horaria: str
    anclas: tuple[AnclaDeclarada, ...]
    interpretaciones: tuple[UUID, ...]
    version_politica: str | None = None


def _instante(texto: object, campo: str) -> datetime:
    if isinstance(texto, datetime):
        valor = texto
    else:
        try:
            valor = datetime.fromisoformat(str(texto))
        except ValueError as exc:
            raise ManifiestoInvalido(f"{campo}: {texto!r} no es un instante ISO 8601.") from exc
    if valor.tzinfo is None:
        raise KnownAtSinZona(f"{campo} es un instante: lleva su zona horaria ({texto}).")
    return valor


def _fecha(texto: object, campo: str) -> date | None:
    if texto is None:
        return None
    if isinstance(texto, date):
        return texto
    try:
        return date.fromisoformat(str(texto))
    except ValueError as exc:
        raise ManifiestoInvalido(f"{campo}: {texto!r} no es una fecha AAAA-MM-DD.") from exc


def leer(datos: dict) -> ManifiestoDeclarado:
    """El manifiesto declarado de un JSON (el de construir-manifiesto-predictivo, o el cuerpo de
    POST /predictivo/entrenamientos). Nada se completa ni se infiere."""
    try:
        anclas = tuple(
            AnclaDeclarada(
                corte_id=UUID(str(a["corte_id"])),
                feature_as_of=_fecha(a["feature_as_of"], "feature_as_of"),
                feature_known_at=_instante(a["feature_known_at"], "feature_known_at"),
                label_known_at=_instante(a["label_known_at"], "label_known_at"),
                pagos_completos_hasta=_fecha(
                    a.get("pagos_completos_hasta"), "pagos_completos_hasta"
                ),
                lifecycle_completo_hasta=_fecha(
                    a.get("lifecycle_completo_hasta"), "lifecycle_completo_hasta"
                ),
                split=str(a["split"]),
            )
            for a in datos["anclas"]
        )
        return ManifiestoDeclarado(
            zona_horaria=str(datos["zona_horaria"]),
            anclas=anclas,
            interpretaciones=tuple(UUID(str(x)) for x in datos.get("interpretaciones_pagos", [])),
            version_politica=datos.get("version_politica"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ManifiestoInvalido):
            raise
        raise ManifiestoInvalido(f"El manifiesto no tiene la forma esperada: {exc}.") from exc


# --- lo resuelto ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Ancla:
    corte: CorteCanonico
    declarada: AnclaDeclarada

    @property
    def split(self) -> str:
        return self.declarada.split

    @property
    def fecha(self) -> date:
        return self.declarada.feature_as_of

    def madurez(self, target: targets_.DefinicionTarget) -> targets_.Madurez:
        return targets_.madurez(
            target,
            self.fecha,
            pagos_completos_hasta=self.declarada.pagos_completos_hasta,
            lifecycle_completo_hasta=self.declarada.lifecycle_completo_hasta,
        )

    def canonica(self) -> dict:
        d = self.declarada
        return {
            "corte_id": str(self.corte.corte_id),
            "feature_as_of": d.feature_as_of.isoformat(),
            "feature_known_at": _utc(d.feature_known_at),
            "label_known_at": _utc(d.label_known_at),
            "pagos_completos_hasta": _iso(d.pagos_completos_hasta),
            "lifecycle_completo_hasta": _iso(d.lifecycle_completo_hasta),
            "split": d.split,
        }

    def hash(self) -> str:
        return hashlib.sha256(canonico(self.canonica())).hexdigest()


@dataclass(frozen=True)
class Interpretacion:
    ejecucion: EjecucionMotorPagos

    def canonica(self) -> dict:
        e = self.ejecucion
        return {
            "motor_pagos_run_id": str(e.motor_pagos_run_id),
            "periodo_desde": e.periodo_desde.isoformat(),
            "firma_entrada": e.firma_entrada,
        }


@dataclass(frozen=True)
class Resuelto:
    despacho_id: str
    cartera_id: str
    zona_horaria: str
    politica: PoliticaEntrenamiento
    anclas: tuple[Ancla, ...]
    """En orden canonico: por feature_as_of."""
    interpretaciones: tuple[Interpretacion, ...]
    hash_manifiesto: str
    firma: str

    def canonico(self) -> dict:
        return {
            "zona_horaria": self.zona_horaria,
            "version_politica": self.politica.version,
            "anclas": [a.canonica() for a in self.anclas],
            "interpretaciones_pagos": [i.canonica() for i in self.interpretaciones],
        }

    def de(self, split: str) -> list[Ancla]:
        return [a for a in self.anclas if a.split == split]


def _utc(instante: datetime) -> str:
    return instante.astimezone(UTC).isoformat(timespec="microseconds")


def _iso(fecha: date | None) -> str | None:
    return None if fecha is None else fecha.isoformat()


def hash_manifiesto(anclas: list[Ancla], interpretaciones: list[Interpretacion], zona: str) -> str:
    cuerpo = {
        "zona_horaria": zona,
        "anclas": [a.canonica() for a in sorted(anclas, key=lambda a: (a.fecha, a.corte.id))],
        "interpretaciones_pagos": [
            i.canonica() for i in sorted(interpretaciones, key=lambda i: i.ejecucion.periodo_desde)
        ],
    }
    return hashlib.sha256(canonico(cuerpo)).hexdigest()


def firma_inputs(
    *,
    despacho_id: str,
    cartera_id: str,
    zona: str,
    hash_del_manifiesto: str,
    politica: PoliticaEntrenamiento = PUBLIC_V1,
) -> str:
    """El SHA-256 de todo lo que fija un entrenamiento: las versiones con sus hashes (modelo,
    features, targets y politica), la cartera, la zona y el manifiesto."""
    lineas = [
        VERSION_MODELO,
        features_.VERSION_FEATURES,
        features_.hash_features(),
        targets_.VERSION_TARGETS,
        targets_.hash_targets(),
        politica.version,
        hash_politica(politica),
        despacho_id,
        cartera_id,
        zona,
        hash_del_manifiesto,
    ]
    return hashlib.sha256("\n".join(lineas).encode("utf-8")).hexdigest()


def meses_de_label(ancla: Ancla) -> list[date]:
    """Los meses (ventanas de motor-pagos/v1) que toca la ventana de pagos (T, T+30] de un ancla."""
    inicio = ancla.fecha + timedelta(days=1)
    return periodos_entre(inicio, ancla.fecha + timedelta(days=targets_.HORIZONTE_MAXIMO))


def resolver(s: Session, declarado: ManifiestoDeclarado) -> Resuelto:
    """El manifiesto comprobado, con su hash y la firma. Levanta, sin registrar nada, un
    ManifiestoInvalido con su codigo estable."""
    politica = POLITICAS.get(declarado.version_politica or PUBLIC_V1.version)
    if politica is None:
        raise PoliticaNoSoportada(
            f"Este servicio entrena con {', '.join(sorted(POLITICAS))}, no con "
            f"{declarado.version_politica}."
        )
    zona = declarado.zona_horaria
    if not s.execute(
        text("SELECT EXISTS (SELECT 1 FROM pg_timezone_names WHERE name = :zona)"), {"zona": zona}
    ).scalar_one():
        raise ZonaHorariaInvalida(f"{zona!r} no es una zona horaria IANA que conozca la base.")
    if len(declarado.anclas) < MINIMO_DE_ANCLAS:
        raise AnclasInsuficientes(
            f"Un entrenamiento declara al menos {MINIMO_DE_ANCLAS} anclas: TRAIN, VALIDATION y "
            "TEST."
        )
    ids = [a.corte_id for a in declarado.anclas]
    if len(set(ids)) != len(ids):
        raise AnclaRepetida("Un corte esta declarado dos veces: un ancla, una vez.")
    reloj = s.execute(text("SELECT now()")).scalar_one()
    anclas = []
    for a in declarado.anclas:
        corte = s.exec(select(CorteCanonico).where(CorteCanonico.corte_id == a.corte_id)).first()
        if corte is None:
            raise CorteNoEncontrado(f"No existe un corte canonico con corte_id {a.corte_id}.")
        anclas.append(Ancla(corte, a))
    carteras = {(a.corte.despacho_id, a.corte.cartera_id) for a in anclas}
    if len(carteras) != 1:
        raise CarteraInconsistente("Las anclas son de mas de una cartera: un modelo, una cartera.")
    ((despacho, cartera),) = carteras
    for ancla in anclas:
        _comprobar_ancla(s, ancla, reloj, zona)
    anclas.sort(key=lambda a: (a.fecha, a.corte.id))
    _comprobar_split(anclas, politica)
    interpretaciones = _interpretaciones(s, declarado, anclas, despacho, cartera)
    del_manifiesto = hash_manifiesto(anclas, interpretaciones, zona)
    return Resuelto(
        despacho_id=despacho,
        cartera_id=cartera,
        zona_horaria=zona,
        politica=politica,
        anclas=tuple(anclas),
        interpretaciones=tuple(sorted(interpretaciones, key=lambda i: i.ejecucion.periodo_desde)),
        hash_manifiesto=del_manifiesto,
        firma=firma_inputs(
            despacho_id=despacho,
            cartera_id=cartera,
            zona=zona,
            hash_del_manifiesto=del_manifiesto,
            politica=politica,
        ),
    )


def _comprobar_ancla(s: Session, ancla: Ancla, reloj: datetime, zona: str) -> None:
    d, corte = ancla.declarada, ancla.corte
    if d.split not in Split.TODOS:
        raise SplitInvalido(f"El split {d.split!r} no es uno de {', '.join(Split.TODOS)}.")
    if d.feature_as_of != corte.fecha_corte:
        raise AsOfDistintoDelCorte(
            f"feature_as_of es el dia del corte: el corte {corte.corte_id} es del "
            f"{corte.fecha_corte}, no del {d.feature_as_of}. Un snapshot intermedio no existe."
        )
    if d.feature_known_at > d.label_known_at:
        raise RelojesInvertidos(
            f"El ancla del {d.feature_as_of}: feature_known_at ({d.feature_known_at.isoformat()}) "
            f"es posterior a label_known_at ({d.label_known_at.isoformat()})."
        )
    if d.label_known_at > reloj:
        raise KnownAtFuturo(
            f"El ancla del {d.feature_as_of}: label_known_at ({d.label_known_at.isoformat()}) es "
            f"posterior al reloj de la base ({reloj.isoformat()})."
        )
    if corte.creado_en > d.feature_known_at:
        raise CorteNoConocido(
            f"El corte del {corte.fecha_corte} se materializo el {corte.creado_en.isoformat()}, "
            f"despues de feature_known_at ({d.feature_known_at.isoformat()}): a esa frontera no "
            "existia."
        )
    dia_de_label = s.execute(
        text("SELECT (CAST(:k AS timestamptz) AT TIME ZONE :zona)::date"),
        {"k": d.label_known_at, "zona": zona},
    ).scalar_one()
    for nombre, marca in (
        ("pagos_completos_hasta", d.pagos_completos_hasta),
        ("lifecycle_completo_hasta", d.lifecycle_completo_hasta),
    ):
        if marca is not None and marca > dia_de_label:
            raise CoberturaNoConocida(
                f"El ancla del {d.feature_as_of} declara {nombre} {marca}, despues del dia de "
                f"label_known_at ({dia_de_label}): no se sabe que algo este completo antes de que "
                "llegue."
            )


def _comprobar_split(anclas: list[Ancla], politica: PoliticaEntrenamiento) -> None:
    bloques = {split: [a for a in anclas if a.split == split] for split in Split.TODOS}
    for split in (Split.TRAIN, Split.VALIDATION, Split.TEST):
        if not bloques[split]:
            raise SplitInvalido(f"El split no tiene anclas {split}.")
    purga = timedelta(days=politica.horizonte_purga_dias)
    for antes, despues in ((Split.TRAIN, Split.VALIDATION), (Split.VALIDATION, Split.TEST)):
        ultima = max(a.fecha for a in bloques[antes])
        primera = min(a.fecha for a in bloques[despues])
        if ultima >= primera:
            raise SplitDesordenado(
                f"{antes} termina el {ultima} y {despues} empieza el {primera}: un split temporal "
                "evalua lo posterior con lo anterior, nunca al reves ni mezclado."
            )
        if not ultima + purga < primera:
            raise PurgaInsuficiente(
                f"La ventana del label mas largo de {antes} (del {ultima} al {ultima + purga}) "
                f"llega a la primera ancla de {despues} ({primera}): sus labels verian el periodo "
                "que se evalua. Declara esas anclas como PURGE."
            )
    for split in (Split.TRAIN, Split.VALIDATION, Split.TEST):
        for target in targets_.TARGETS:
            if not any(a.madurez(target) == targets_.Madurez.MADURA for a in bloques[split]):
                raise SplitSinMaduras(
                    f"{split} no tiene ningun ancla con {target.target} MADURA: con la cobertura "
                    "declarada, sus labels todavia no existen."
                )


def _interpretaciones(
    s: Session, declarado: ManifiestoDeclarado, anclas: list[Ancla], despacho: str, cartera: str
) -> list[Interpretacion]:
    if len(set(declarado.interpretaciones)) != len(declarado.interpretaciones):
        raise VentanaDuplicada("Una interpretacion esta declarada dos veces.")
    por_mes: dict[date, EjecucionMotorPagos] = {}
    for publico in declarado.interpretaciones:
        e = s.exec(
            select(EjecucionMotorPagos).where(EjecucionMotorPagos.motor_pagos_run_id == publico)
        ).first()
        if e is None:
            raise InterpretacionNoEncontrada(f"No existe la interpretacion de pagos {publico}.")
        if e.estado != EstadoMotorPagos.EXITOSA:
            raise InterpretacionNoExitosa(
                f"La interpretacion {publico} esta {e.estado}: un label solo lee una EXITOSA."
            )
        if e.version_motor != VERSION_MOTOR_PAGOS or (e.despacho_id, e.cartera_id) != (
            despacho,
            cartera,
        ):
            raise InterpretacionInconsistente(
                f"La interpretacion {publico} es de {e.version_motor} en {e.despacho_id}/"
                f"{e.cartera_id}: los labels leen {VERSION_MOTOR_PAGOS} de la cartera de las "
                "anclas."
            )
        if e.periodo_desde in por_mes:
            raise VentanaDuplicada(
                f"La ventana {e.periodo_desde:%Y-%m} tiene dos interpretaciones declaradas: un "
                "movimiento contaria dos veces."
            )
        por_mes[e.periodo_desde] = e
    recuperacion = targets_.POR_TARGET[targets_.Target.RECUPERACION_POSITIVA_30D]
    comprobadas: set[tuple[int, datetime]] = set()
    for ancla in anclas:
        if ancla.madurez(recuperacion) != targets_.Madurez.MADURA:
            continue
        conocido = ancla.declarada.label_known_at
        for mes in meses_de_label(ancla):
            e = por_mes.get(mes)
            if e is None:
                raise InterpretacionFaltante(
                    f"La ventana de pagos del ancla del {ancla.fecha} toca {mes:%Y-%m}, y el "
                    "manifiesto no declara su interpretacion: su label no se puede construir."
                )
            if (e.id, conocido) in comprobadas:
                continue
            if e.terminada_en is None or e.terminada_en > conocido:
                raise InterpretacionNoConocida(
                    f"La interpretacion de {mes:%Y-%m} termino el {e.terminada_en}, despues del "
                    f"label_known_at del ancla del {ancla.fecha}."
                )
            comprobar_vigente(s, e, conocido)
            comprobar_completa(s, e, conocido, despacho, cartera)
            comprobadas.add((e.id, conocido))
    return [Interpretacion(e) for e in por_mes.values()]


def comprobar_vigente(s: Session, e: EjecucionMotorPagos, conocido: datetime) -> None:
    """Que la interpretacion sea la vigente de su mes a `conocido`: la EXITOSA mas reciente que ya
    habia terminado. Una anterior no vio lo que llego despues (un reverso de su contexto, un
    archivo nuevo); una posterior todavia no existia."""
    vigente = s.execute(
        text(
            "SELECT id FROM ejecucion_motor_pagos WHERE despacho_id = :d AND cartera_id = :c "
            "AND version_motor = :v AND periodo_desde = :mes AND estado = 'EXITOSA' "
            "AND terminada_en <= :k ORDER BY id DESC LIMIT 1"
        ),
        {
            "d": e.despacho_id,
            "c": e.cartera_id,
            "v": e.version_motor,
            "mes": e.periodo_desde,
            "k": conocido,
        },
    ).scalar_one_or_none()
    if vigente != e.id:
        raise InterpretacionNoVigente(
            f"La interpretacion {e.motor_pagos_run_id} de {e.periodo_desde:%Y-%m} no es la "
            f"vigente a {conocido.isoformat()}: otra posterior ya habia terminado. Un label lee lo "
            "que se sabia a label_known_at."
        )


def comprobar_completa(
    s: Session, e: EjecucionMotorPagos, conocido: datetime, despacho: str, cartera: str
) -> None:
    """Que la interpretacion de un mes clasifico cada pago observado de ese mes conocido a
    `conocido`: los de los archivos cuya historia termino hasta entonces. Un EXISTS por pago, con
    las tres columnas de la llave de resultado_pago_observado: con un LEFT JOIN, una interpretacion
    recien publicada (que las estadisticas todavia no conocen) puede hacer que el plan recorra
    todos sus resultados por cada pago."""
    observados, clasificados = s.execute(
        text(
            "SELECT count(*), count(*) FILTER (WHERE EXISTS (SELECT 1 "
            "FROM resultado_pago_observado r WHERE r.ejecucion_motor_pagos_id = :e "
            "AND r.dataset_conformado_id = p.dataset_conformado_id "
            "AND r.source_row = p.source_row)) FROM pago_observado p "
            "WHERE p.despacho_id = :d AND p.cartera_id = :c AND p.fecha_recepcion >= :desde "
            "AND p.fecha_recepcion < :hasta AND p.dataset_conformado_id IN ("
            "SELECT h.dataset_conformado_id FROM ejecucion_historia h "
            "WHERE h.tipo_fuente = 'PAGOS' AND h.estado = 'EXITOSA' AND h.terminada_en <= :k)"
        ),
        {
            "e": e.id,
            "d": despacho,
            "c": cartera,
            "desde": e.periodo_desde,
            "hasta": e.periodo_hasta,
            "k": conocido,
        },
    ).one()
    if clasificados < observados:
        raise InterpretacionIncompleta(
            f"La interpretacion de {e.periodo_desde:%Y-%m} clasifico {clasificados:,} de los "
            f"{observados:,} pagos observados conocidos de su mes: un label leeria menos pagos de "
            "los que se sabian."
        )


# --- proponer -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CorteCandidato:
    """Un corte que puede ser ancla, con la frontera de conocimiento de sus features."""

    corte_id: UUID
    fecha: date
    known_at: datetime


def proponer(
    s: Session,
    *,
    despacho_id: str,
    cartera_id: str,
    zona: str,
    cortes: list[CorteCandidato],
    label_known_at: datetime,
    pagos_completos_hasta: date | None,
    lifecycle_completo_hasta: date | None,
    anclas_validation: int,
    anclas_test: int,
    politica: PoliticaEntrenamiento = PUBLIC_V1,
) -> dict:
    """Un manifiesto propuesto, para que una persona lo revise antes de entrenar: no entrena ni
    registra nada. La regla, determinista: de los cortes cuyos cuatro labels estan MADURA con la
    cobertura declarada, TEST son los ultimos `anclas_test`; VALIDATION, los ultimos
    `anclas_validation` cuya ventana de label termina antes de TEST; TRAIN, todos los anteriores
    cuya ventana termina antes de VALIDATION. Los demas (los de la purga y los censurados del final)
    son PURGE. Las interpretaciones de pagos son las vigentes a label_known_at de cada mes que toca
    una ventana madura."""
    if anclas_validation < 1 or anclas_test < 1:
        raise AnclasInsuficientes("VALIDATION y TEST tienen al menos un ancla cada uno.")
    purga = timedelta(days=politica.horizonte_purga_dias)

    def madura(fecha: date) -> bool:
        return all(
            targets_.madurez(
                t,
                fecha,
                pagos_completos_hasta=pagos_completos_hasta,
                lifecycle_completo_hasta=lifecycle_completo_hasta,
            )
            == targets_.Madurez.MADURA
            for t in targets_.TARGETS
        )

    ordenados = sorted(cortes, key=lambda c: (c.fecha, str(c.corte_id)))
    maduros = [c for c in ordenados if madura(c.fecha)]
    if len(maduros) < anclas_test:
        raise AnclasInsuficientes(
            f"Solo {len(maduros)} cortes tienen sus labels maduros con la cobertura declarada."
        )
    test = maduros[-anclas_test:]
    previos = [c for c in maduros if c.fecha + purga < test[0].fecha]
    validation = previos[-anclas_validation:]
    train = [c for c in previos if validation and c.fecha + purga < validation[0].fecha]
    if len(validation) < anclas_validation or not train:
        raise AnclasInsuficientes(
            "No caben TRAIN, VALIDATION y TEST con la purga de "
            f"{politica.horizonte_purga_dias} dias entre ellos en los cortes maduros."
        )
    split = {c.corte_id: Split.TRAIN for c in train}
    split.update({c.corte_id: Split.VALIDATION for c in validation})
    split.update({c.corte_id: Split.TEST for c in test})
    recuperacion = targets_.POR_TARGET[targets_.Target.RECUPERACION_POSITIVA_30D]
    meses = sorted(
        {
            mes
            for c in ordenados
            if targets_.madurez(
                recuperacion,
                c.fecha,
                pagos_completos_hasta=pagos_completos_hasta,
                lifecycle_completo_hasta=lifecycle_completo_hasta,
            )
            == targets_.Madurez.MADURA
            for mes in periodos_entre(
                c.fecha + timedelta(days=1), c.fecha + timedelta(days=targets_.HORIZONTE_MAXIMO)
            )
        }
    )
    interpretaciones = []
    for mes in meses:
        vigente = s.execute(
            text(
                "SELECT motor_pagos_run_id FROM ejecucion_motor_pagos WHERE despacho_id = :d "
                "AND cartera_id = :c AND version_motor = :v AND periodo_desde = :mes "
                "AND estado = 'EXITOSA' AND terminada_en <= :k ORDER BY id DESC LIMIT 1"
            ),
            {
                "d": despacho_id,
                "c": cartera_id,
                "v": VERSION_MOTOR_PAGOS,
                "mes": mes,
                "k": label_known_at,
            },
        ).scalar_one_or_none()
        if vigente is None:
            raise InterpretacionFaltante(
                f"El mes {mes:%Y-%m} toca una ventana madura y no tiene ninguna interpretacion "
                "EXITOSA terminada a label_known_at."
            )
        interpretaciones.append(str(vigente))
    return {
        "zona_horaria": zona,
        "version_politica": politica.version,
        "anclas": [
            {
                "corte_id": str(c.corte_id),
                "feature_as_of": c.fecha.isoformat(),
                "feature_known_at": c.known_at.isoformat(),
                "label_known_at": label_known_at.isoformat(),
                "pagos_completos_hasta": _iso(pagos_completos_hasta),
                "lifecycle_completo_hasta": _iso(lifecycle_completo_hasta),
                "split": split.get(c.corte_id, Split.PURGE),
            }
            for c in ordenados
        ],
        "interpretaciones_pagos": interpretaciones,
    }
