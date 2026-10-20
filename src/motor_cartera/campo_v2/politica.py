"""campo-public-v2: la politica publica de campo/v2, como nucleo puro.

Dada una decision/v2, una asignacion de territorial/v2 y una configuracion de campo declaradas, y
una fecha de operacion:

1. El universo son las decisiones con canal CAMPO. campo/v2 no vuelve a decidir el canal ni la
   prioridad: los toma de decision/v2, y no inventa otro puntaje.
2. Una candidata cuya asignacion territorial no es ASIGNADA no se fuerza a un gestor:
   REVISION_TERRITORIAL, con el estado y los motivos territoriales. No se le busca otra base.
3. Las demas se ubican en su particion operativa, que es una cuadricula fija y no un agrupamiento:
   ZONA (base + entidad), SUBZONA (base + municipio), TRAMO (una celda de 5 km de la cuadricula en
   EPSG:6372, dentro de su municipio) y MICROTRAMO (la celda de 1 km dentro del tramo). No depende
   de cuantas cuentas haya ni del orden en que lleguen.
4. Los gestores disponibles de su base en la fecha son los activos con jornada ese dia. Ninguno:
   SIN_GESTOR_DISPONIBLE. La cuenta no se lleva a otra base: territorial/v2 decide la pertenencia.
5. Una cuenta es una unidad de capacidad, y la de cada gestor es su cupo declarado. Por base, se
   reparte por prioridad (MUY_ALTA primero) y, dentro de cada prioridad, por bloques (las cuentas de
   un mismo microtramo): primero los bloques con mas cuentas, y entre iguales, el de identificador
   de microtramo menor.
6. Cada bloque va completo a su propietario preferido, el primero de un ranking Rendezvous: el
   SHA-256 del microtramo con el codigo de cada gestor disponible de la base, el mayor primero. Si
   no le cabe completo, va completo al primero del ranking al que si le cabe; si no cabe completo
   en ninguno, se divide en el orden del ranking, cuenta por cuenta en el orden de su cuenta_id. Lo
   que ya no cabe en nadie queda SIN_CAPACIDAD.

La capacidad es un cupo operativo declarado, no factibilidad vial: esta politica no usa distancias,
tiempos de traslado, rutas ni la duracion de una visita (v0.13). Es pura: no lee la base, el reloj
ni el azar. `campo_v2.ejecuciones` la aplica: el SQL ubica y publica por conjuntos, y `repartir`
reparte los bloques, que son ordenes de magnitud menos que las cuentas.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from uuid import UUID, uuid5

from motor_cartera.decision.reglas import Prioridad
from motor_cartera.decision_v2.politica import NIVEL

VERSION_MOTOR = "campo/v2"
CANAL_CAMPO = "CAMPO"
"""El unico canal que campo/v2 lee de decision/v2."""

ESPACIO_CAMPO = UUID("dde3cf6b-37bc-4844-a7a2-cd83fd5dd381")
"""El espacio de nombres de los identificadores de la particion. No cambia nunca: cambiarlo
cambiaria cada zona, subzona, tramo y microtramo publicados."""


class EstadoCampo(StrEnum):
    """En que queda cada candidata de campo. Exactamente uno."""

    ASIGNADA = "ASIGNADA"
    REVISION_TERRITORIAL = "REVISION_TERRITORIAL"
    SIN_GESTOR_DISPONIBLE = "SIN_GESTOR_DISPONIBLE"
    SIN_CAPACIDAD = "SIN_CAPACIDAD"


CONTEO_DE_ESTADO = {
    EstadoCampo.ASIGNADA: "asignadas",
    EstadoCampo.REVISION_TERRITORIAL: "revision_territorial",
    EstadoCampo.SIN_GESTOR_DISPONIBLE: "sin_gestor",
    EstadoCampo.SIN_CAPACIDAD: "sin_capacidad",
}
"""La columna de EjecucionCampoV2 que cuenta cada estado."""


class MotivoCampo(StrEnum):
    """Por que una candidata quedo como quedo, en un catalogo cerrado."""

    TERRITORIAL_NO_ASIGNADA = "TERRITORIAL_NO_ASIGNADA"
    """Con `estado_territorial` y `motivos_territoriales`: la asignacion de territorial/v2 no es
    ASIGNADA, y la cuenta va a revision sin base."""
    SIN_GESTOR_DISPONIBLE = "SIN_GESTOR_DISPONIBLE"
    """Con `base`, `gestores` (los de la base en la configuracion) y cuantos estan inactivos, sin
    jornada ese dia o cerrados por una excepcion."""
    CAPACIDAD_AGOTADA = "CAPACIDAD_AGOTADA"
    """Con `base`, `capacidad_base`, `gestores` y `propietario_preferido`: la capacidad de la base
    se agoto antes de llegar a la cuenta."""
    PROPIETARIO_PREFERIDO = "PROPIETARIO_PREFERIDO"
    """Con `gestor`, `cuentas_bloque` y `dividido`: quedo con el primero del ranking de su
    microtramo."""
    SPILLOVER_POR_CAPACIDAD = "SPILLOVER_POR_CAPACIDAD"
    """Con `gestor`, `propietario_preferido`, `lugar` (el del gestor en el ranking),
    `cuentas_bloque` y `dividido`: al propietario preferido no le cabia, y quedo con el siguiente
    del ranking al que si."""


class NivelUnidad(StrEnum):
    """Los niveles de la particion operativa, del mas grande al mas chico."""

    ZONA = "ZONA"
    SUBZONA = "SUBZONA"
    TRAMO = "TRAMO"
    MICROTRAMO = "MICROTRAMO"


PADRE_DEL_NIVEL = {
    NivelUnidad.ZONA: None,
    NivelUnidad.SUBZONA: NivelUnidad.ZONA,
    NivelUnidad.TRAMO: NivelUnidad.SUBZONA,
    NivelUnidad.MICROTRAMO: NivelUnidad.TRAMO,
}


class EstadoJornada(StrEnum):
    """Como esta un gestor en la fecha de operacion. Solo los dos primeros estan disponibles."""

    JORNADA_SEMANAL = "JORNADA_SEMANAL"
    VENTANAS_ESPECIALES = "VENTANAS_ESPECIALES"
    SIN_JORNADA = "SIN_JORNADA"
    CERRADO_POR_EXCEPCION = "CERRADO_POR_EXCEPCION"
    INACTIVO = "INACTIVO"


DISPONIBLES = frozenset({EstadoJornada.JORNADA_SEMANAL, EstadoJornada.VENTANAS_ESPECIALES})

PRIORIDADES = tuple(sorted(NIVEL, key=lambda p: -NIVEL[p]))
"""El orden formal de decision/v2, de la mas alta a la mas baja: MUY_ALTA, ALTA, MEDIA, BAJA."""


# --- la politica como datos -----------------------------------------------------------------------

CUANTO = Decimal("0.001")
"""Un milimetro: a esta precision se toma cada coordenada metrica antes de calcular su celda."""


@dataclass(frozen=True)
class PoliticaCampo:
    """Una politica de campo/v2, como datos: lo que entra en su hash."""

    version: str
    srid_metrico: int
    tamano_tramo_m: int
    tamano_microtramo_m: int

    def __post_init__(self) -> None:
        if self.tamano_microtramo_m <= 0 or self.tamano_tramo_m % self.tamano_microtramo_m:
            raise ValueError(
                "El tramo tiene que ser un multiplo exacto del microtramo: "
                f"{self.tamano_tramo_m} y {self.tamano_microtramo_m}."
            )

    @property
    def microtramos_por_lado(self) -> int:
        """Cuantos microtramos caben en el lado de un tramo: 5 con 5,000 y 1,000 m."""
        return self.tamano_tramo_m // self.tamano_microtramo_m

    def definicion(self) -> dict:
        return {
            "version": self.version,
            "motor": VERSION_MOTOR,
            "universo": f"los resultados de decision/v2 con canal_recomendado {CANAL_CAMPO}",
            "prioridad": [p.value for p in PRIORIDADES],
            "territorial": "solo una asignacion ASIGNADA de territorial/v2 recibe gestor, y de su "
            "base; las demas quedan en REVISION_TERRITORIAL",
            "particion": {
                "srid": self.srid_metrico,
                "tramo_m": self.tamano_tramo_m,
                "microtramo_m": self.tamano_microtramo_m,
                "redondeo_m": str(CUANTO),
                "celda": "floor(round(numeric(x), 3) / tamano), con numeric de 15 digitos "
                "significativos y los empates lejos del cero: la celda k es [k * tamano, "
                "(k + 1) * tamano)",
                "zona": "base + CVE_ENT del municipio espacial",
                "subzona": "base + CVEGEO del municipio espacial",
                "tramo": "base + CVEGEO + celda del tramo",
                "microtramo": "base + CVEGEO + celda del microtramo",
                "ids": "UUIDv5(espacio, 'version:NIVEL:base:clave')",
                "espacio": str(ESPACIO_CAMPO),
            },
            "jornada": "activo y con al menos una ventana en la fecha; una excepcion de la fecha "
            "reemplaza a la semana",
            "capacidad": "capacidad_cuentas_dia si esta disponible, 0 si no; una cuenta es una "
            "unidad",
            "orden": [
                "prioridad, de la mas alta",
                "cuentas del bloque, de mas a menos",
                "microtramo",
            ],
            "ranking": "SHA-256 de 'microtramo_id|codigo' de cada gestor disponible de la base, el "
            "mayor primero; un empate, por codigo",
            "bloque": "completo al propietario preferido; si no le cabe, completo al primero del "
            "ranking al que le cabe; si a ninguno, dividido en el orden del ranking, por cuenta_id",
            "sin_capacidad": "lo que no cabe en ningun gestor disponible de su base",
            "no_usa": [
                "distancia",
                "tiempo de traslado",
                "rutas",
                "duracion de una visita",
                "saldo",
                "otra base",
                "pesos por visita",
            ],
        }


PUBLIC_V2 = PoliticaCampo("campo-public-v2", 6372, 5_000, 1_000)
POLITICAS = {PUBLIC_V2.version: PUBLIC_V2}


def hash_politica(politica: PoliticaCampo = PUBLIC_V2) -> str:
    """SHA-256 de la definicion canonica de la politica."""
    texto = json.dumps(
        politica.definicion(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def firma_inputs(
    *,
    corte_id,
    decision_run_id,
    firma_decision: str,
    territorial_run_id,
    firma_territorial: str,
    configuracion_campo_version_id,
    hash_configuracion: str,
    fecha_operacion: date,
    politica: PoliticaCampo = PUBLIC_V2,
) -> str:
    """El SHA-256 de las entradas de un plan: el motor, la politica con su hash, el corte, la
    decision y territorial/v2 con sus firmas, la configuracion de campo con su hash y la fecha de
    operacion.
    Las mismas entradas son el mismo plan."""
    lineas = [
        VERSION_MOTOR,
        politica.version,
        hash_politica(politica),
        str(corte_id),
        str(decision_run_id),
        firma_decision,
        str(territorial_run_id),
        firma_territorial,
        str(configuracion_campo_version_id),
        hash_configuracion,
        fecha_operacion.isoformat(),
    ]
    return hashlib.sha256("\n".join(lineas).encode("utf-8")).hexdigest()


# --- la cuadricula --------------------------------------------------------------------------------


def metros(valor: float | Decimal | str) -> Decimal:
    """La coordenada metrica como la toma la cuadricula: llevada a numeric con 15 digitos
    significativos, como lo hace PostgreSQL con un double precision, y redondeada al milimetro con
    los empates lejos del cero, como `round(numeric, 3)`. Asi un punto que vuelve de una
    transformacion como 4,999.9999999995 m es exactamente 5,000.000."""
    if isinstance(valor, float):
        if not math.isfinite(valor):
            raise ValueError(f"Una coordenada metrica es finita: {valor!r}.")
        valor = format(valor, ".15g")
    return Decimal(valor).quantize(CUANTO, rounding=ROUND_HALF_UP)


def celda(coordenada: float | Decimal | str, tamano_m: int) -> int:
    """El indice de la celda de una coordenada: floor(x / tamano), sobre la coordenada al milimetro.
    La celda k es [k * tamano, (k + 1) * tamano): un punto exactamente en la frontera es de la celda
    que empieza ahi, y uno un milimetro antes, de la anterior."""
    return math.floor(metros(coordenada) / tamano_m)


@dataclass(frozen=True)
class Particion:
    """Las cuatro unidades de una cuenta, de la mas grande a la mas chica."""

    zona_id: UUID
    subzona_id: UUID
    tramo_id: UUID
    microtramo_id: UUID

    def de_nivel(self, nivel: NivelUnidad) -> UUID:
        return {
            NivelUnidad.ZONA: self.zona_id,
            NivelUnidad.SUBZONA: self.subzona_id,
            NivelUnidad.TRAMO: self.tramo_id,
            NivelUnidad.MICROTRAMO: self.microtramo_id,
        }[nivel]


def _id(politica: PoliticaCampo, nivel: NivelUnidad, base: str, clave: str) -> UUID:
    return uuid5(ESPACIO_CAMPO, f"{politica.version}:{nivel}:{base}:{clave}")


def particion(
    base: str, cvegeo: str, ix: int, iy: int, politica: PoliticaCampo = PUBLIC_V2
) -> Particion:
    """Las unidades de una cuenta de la base `base` (su codigo), en el municipio `cvegeo`, cuyo
    punto cae en la celda (ix, iy) de microtramo. El tramo es la celda que la contiene: con lados
    multiplos, floor(ix / 5) es exactamente floor(x / 5,000). Los identificadores son UUIDv5 de la
    politica, el nivel, la base y la clave: no llevan la coordenada, y son los mismos en cualquier
    orden, maquina o reconstruccion."""
    lado = politica.microtramos_por_lado
    return Particion(
        _id(politica, NivelUnidad.ZONA, base, cvegeo[:2]),
        _id(politica, NivelUnidad.SUBZONA, base, cvegeo),
        _id(politica, NivelUnidad.TRAMO, base, f"{cvegeo}:{ix // lado}:{iy // lado}"),
        _id(politica, NivelUnidad.MICROTRAMO, base, f"{cvegeo}:{ix}:{iy}"),
    )


# --- el ranking -----------------------------------------------------------------------------------


def ranking(microtramo_id: UUID, gestores: Iterable[str]) -> list[str]:
    """Los gestores disponibles de una base en el orden Rendezvous del microtramo: el SHA-256 de
    'microtramo_id|codigo', el mayor primero (el propietario preferido), y un empate por codigo. No
    depende del orden en que lleguen los gestores, ni del de la base de datos: agregar un gestor
    solo mueve los microtramos en los que el nuevo queda primero."""
    prefijo = hashlib.sha256(f"{microtramo_id}|".encode())

    def puntaje(codigo: str) -> bytes:
        h = prefijo.copy()
        h.update(codigo.encode("utf-8"))
        return h.digest()

    orden = sorted(gestores)
    # El orden es estable: con reverse, dos puntajes iguales conservan el orden por codigo.
    orden.sort(key=puntaje, reverse=True)
    return orden


# --- el reparto -----------------------------------------------------------------------------------


@dataclass(frozen=True, order=True)
class Bloque:
    """Las candidatas de una base con la misma prioridad y el mismo microtramo."""

    prioridad: str
    microtramo_id: UUID
    cuentas: int

    @property
    def nivel(self) -> int:
        return NIVEL[Prioridad(self.prioridad)]


def orden_de_bloques(bloques: Iterable[Bloque]) -> list[Bloque]:
    """En que orden recibe capacidad cada bloque de una base: la prioridad mas alta primero; dentro
    de ella, el bloque con mas cuentas primero, y entre iguales, el microtramo de identificador
    menor."""
    return sorted(bloques, key=lambda b: (-b.nivel, -b.cuentas, b.microtramo_id))


@dataclass(frozen=True)
class Parte:
    """Un tramo de un bloque: sus cuentas `desde` hasta antes de `hasta`, en el orden de su
    cuenta_id y contando desde 0, y como quedaron."""

    bloque: Bloque
    desde: int
    hasta: int
    estado: EstadoCampo
    gestor: str | None
    rebalanceada: bool
    motivo: dict


@dataclass(frozen=True)
class Reparto:
    """Lo que `repartir` concluye de una base: cada parte de cada bloque, y donde empieza cada
    bloque en el orden de la base (para el orden_prioridad de sus cuentas)."""

    partes: list[Parte]
    inicio: dict[Bloque, int]
    propietario: dict[UUID, str] = field(default_factory=dict)
    """El propietario preferido de cada microtramo, si la base tiene gestores disponibles."""


def sin_gestor(base: str, jornadas: Mapping[str, int]) -> dict:
    """El motivo de una base sin gestores disponibles: cuantos tiene en la configuracion y en que
    estado esta cada uno en la fecha."""
    return {
        "codigo": MotivoCampo.SIN_GESTOR_DISPONIBLE.value,
        "base": base,
        "gestores": sum(jornadas.values()),
        "inactivos": jornadas.get(EstadoJornada.INACTIVO, 0),
        "sin_jornada": jornadas.get(EstadoJornada.SIN_JORNADA, 0),
        "cerrados": jornadas.get(EstadoJornada.CERRADO_POR_EXCEPCION, 0),
    }


def repartir(
    bloques: Iterable[Bloque],
    capacidades: Mapping[str, int],
    *,
    base: str,
    jornadas: Mapping[str, int] | None = None,
) -> Reparto:
    """El reparto de los bloques de una base entre sus gestores disponibles, cada uno con su
    capacidad efectiva en la fecha (mayor que cero). Sin gestores disponibles, todo queda
    SIN_GESTOR_DISPONIBLE, con lo que dice `jornadas` (cuantos gestores de la base hay en cada
    estado de jornada).

    La invariante de la prioridad sale del orden: un bloque se parte solo si no cabe completo en
    nadie, y queda SIN_CAPACIDAD solo lo que no cabe en la capacidad que sobra de toda la base. Asi,
    si una cuenta queda sin capacidad, la base ya no tiene cupo, y ninguna de menor prioridad se
    asigna."""
    orden = orden_de_bloques(bloques)
    inicio, acumulado = {}, 0
    for bloque in orden:
        inicio[bloque] = acumulado
        acumulado += bloque.cuentas
    if not capacidades:
        motivo = sin_gestor(base, jornadas or {})
        partes = [
            Parte(b, 0, b.cuentas, EstadoCampo.SIN_GESTOR_DISPONIBLE, None, False, motivo)
            for b in orden
        ]
        return Reparto(partes, inicio)
    if any(c <= 0 for c in capacidades.values()):
        raise ValueError(f"Un gestor disponible tiene capacidad: {dict(capacidades)}.")
    cupo = dict(capacidades)
    capacidad_base = sum(capacidades.values())
    partes: list[Parte] = []
    propietario: dict[UUID, str] = {}
    for bloque in orden:
        orden_del_ranking = ranking(bloque.microtramo_id, cupo)
        preferido = orden_del_ranking[0]
        propietario[bloque.microtramo_id] = preferido
        n = bloque.cuentas
        completo = next((g for g in orden_del_ranking if cupo[g] >= n), None)
        if completo is not None:
            cupo[completo] -= n
            partes.append(
                _asignada(bloque, 0, n, completo, preferido, orden_del_ranking, dividido=False)
            )
            continue
        desde = 0
        for gestor in orden_del_ranking:
            toma = min(cupo[gestor], n - desde)
            if toma > 0:
                cupo[gestor] -= toma
                partes.append(
                    _asignada(
                        bloque, desde, desde + toma, gestor, preferido, orden_del_ranking, True
                    )
                )
                desde += toma
        if desde < n:
            partes.append(
                Parte(
                    bloque,
                    desde,
                    n,
                    EstadoCampo.SIN_CAPACIDAD,
                    None,
                    False,
                    {
                        "codigo": MotivoCampo.CAPACIDAD_AGOTADA.value,
                        "base": base,
                        "capacidad_base": capacidad_base,
                        "gestores": len(capacidades),
                        "propietario_preferido": preferido,
                    },
                )
            )
    return Reparto(partes, inicio, propietario)


def _asignada(
    bloque: Bloque,
    desde: int,
    hasta: int,
    gestor: str,
    preferido: str,
    orden_del_ranking: Sequence[str],
    dividido: bool,
) -> Parte:
    if gestor == preferido:
        motivo = {
            "codigo": MotivoCampo.PROPIETARIO_PREFERIDO.value,
            "gestor": gestor,
            "cuentas_bloque": bloque.cuentas,
            "dividido": dividido,
        }
    else:
        motivo = {
            "codigo": MotivoCampo.SPILLOVER_POR_CAPACIDAD.value,
            "gestor": gestor,
            "propietario_preferido": preferido,
            "lugar": orden_del_ranking.index(gestor) + 1,
            "cuentas_bloque": bloque.cuentas,
            "dividido": dividido,
        }
    return Parte(bloque, desde, hasta, EstadoCampo.ASIGNADA, gestor, gestor != preferido, motivo)


# --- una cuenta, para el espejo -------------------------------------------------------------------


@dataclass(frozen=True)
class Candidata:
    """Lo que campo/v2 necesita de una candidata: su cuenta, su prioridad, su territorial y, si
    tiene base, su municipio y la celda de microtramo de su punto."""

    cuenta_id: UUID
    prioridad: str
    estado_territorial: str
    motivos_territoriales: tuple[str, ...] = ()
    base: str | None = None
    cvegeo: str | None = None
    ix: int | None = None
    iy: int | None = None


@dataclass(frozen=True)
class ResultadoCuenta:
    estado: EstadoCampo
    base: str | None
    gestor: str | None
    particion: Particion | None
    rebalanceada: bool
    orden_prioridad: int | None
    motivos: list[dict]


def asignar(
    candidatas: Sequence[Candidata],
    capacidades: Mapping[str, Mapping[str, int]],
    jornadas: Mapping[str, Mapping[str, int]] | None = None,
    *,
    politica: PoliticaCampo = PUBLIC_V2,
) -> dict[UUID, ResultadoCuenta]:
    """La politica cuenta por cuenta: lo que el plan tiene que publicar de cada candidata, con las
    capacidades efectivas de los gestores disponibles de cada base (`capacidades[base][gestor]`) y
    los estados de jornada de los gestores de cada base (`jornadas[base][estado]`). Es el espejo del
    SQL por conjuntos: agrupa en bloques, reparte con `repartir` y reparte cada parte en el orden de
    la cuenta_id."""
    jornadas = jornadas or {}
    resultados: dict[UUID, ResultadoCuenta] = {}
    por_bloque: dict[tuple[str, Bloque], list[Candidata]] = defaultdict(list)
    particiones: dict[UUID, Particion] = {}
    bases: dict[UUID, str] = {}
    conteo: dict[tuple[str, str, UUID], int] = defaultdict(int)
    for c in candidatas:
        if c.estado_territorial != "ASIGNADA":
            resultados[c.cuenta_id] = ResultadoCuenta(
                EstadoCampo.REVISION_TERRITORIAL,
                None,
                None,
                None,
                False,
                None,
                [
                    {
                        "codigo": MotivoCampo.TERRITORIAL_NO_ASIGNADA.value,
                        "estado_territorial": c.estado_territorial,
                        "motivos_territoriales": list(c.motivos_territoriales),
                    }
                ],
            )
            continue
        if c.base is None or c.cvegeo is None or c.ix is None or c.iy is None:
            raise ValueError(f"La candidata ASIGNADA {c.cuenta_id} trae su base y su celda.")
        p = particion(c.base, c.cvegeo, c.ix, c.iy, politica)
        particiones[c.cuenta_id] = p
        bases[c.cuenta_id] = c.base
        conteo[(c.base, c.prioridad, p.microtramo_id)] += 1
    for c in candidatas:
        if c.cuenta_id in particiones:
            de_la_base, m = bases[c.cuenta_id], particiones[c.cuenta_id].microtramo_id
            bloque = Bloque(c.prioridad, m, conteo[(de_la_base, c.prioridad, m)])
            por_bloque[(de_la_base, bloque)].append(c)
    for base in sorted({b for b, _ in por_bloque}):
        bloques = [bloque for (b, bloque) in por_bloque if b == base]
        reparto = repartir(
            bloques, capacidades.get(base, {}), base=base, jornadas=jornadas.get(base, {})
        )
        for parte in reparto.partes:
            cuentas = sorted(por_bloque[(base, parte.bloque)], key=lambda c: c.cuenta_id)
            for posicion in range(parte.desde, parte.hasta):
                c = cuentas[posicion]
                resultados[c.cuenta_id] = ResultadoCuenta(
                    parte.estado,
                    base,
                    parte.gestor,
                    particiones[c.cuenta_id],
                    parte.rebalanceada,
                    reparto.inicio[parte.bloque] + posicion + 1,
                    [parte.motivo],
                )
    return resultados
