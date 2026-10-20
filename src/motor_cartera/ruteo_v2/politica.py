"""ruteo-public-v2: la politica publica de ruteo/v2, como nucleo puro.

Dada la carga de un gestor que publico campo/v2 (sus cuentas ASIGNADA, cada una con su prioridad),
su base, las ventanas de su jornada en la fecha y lo que la red vial dice de esos puntos (el snap de
cada uno y la matriz de duraciones y distancias por calle entre ellos), decide en que orden puede
recorrerlas, a que hora llega a cada una y cuales caben en su jornada:

1. La base es la de campo/v2, y la ruta es cerrada: sale de la base al empezar su primera ventana y
   regresa a ella. Si la base no tiene snap a la red, o lo tiene a mas de 500 m, no hay ruta:
   BASE_NO_ENRUTABLE, y cada parada NO_ENRUTABLE_BASE.
2. Cada parada necesita snap a la red a no mas de 500.00 m (al centimetro); si no, NO_ENRUTABLE
   (SIN_SNAP o SNAP_EXCESIVO). Con snap, si la matriz no tiene duracion de la base a la parada o de
   la parada a la base, esta en una componente desconectada: NO_ENRUTABLE (RED_DESCONECTADA). Nunca
   se inventa una duracion, ni se usa la distancia en linea recta.
3. Una visita dura 900 s, todas igual: una hipotesis operacional publica, no una medicion. Un
   traslado y un servicio caben enteros en una ventana [inicio, fin); si no, esperan a la siguiente,
   en el ultimo lugar donde esta el gestor. En los huecos no hay trabajo. Dos ventanas que se tocan
   son una sola.
4. La heuristica, PRIORITY_NEAREST_NEIGHBOR_2OPT/v1: por prioridad (MUY_ALTA, ALTA, MEDIA, BAJA), la
   siguiente parada es la de menor duracion vial desde donde esta el gestor que todavia cabe con su
   traslado, su servicio y el regreso a la base; un empate, por cuenta_id. Si una parada enrutable
   de un nivel no cabe, ninguna de un nivel inferior se programa. Una parada que no cabe ni sola en
   la jornada vacia (EXCEDE_JORNADA) no bloquea a las inferiores. Despues, 2-opt dentro de cada
   segmento de una misma prioridad: un cambio se acepta solo si reduce la duracion de traslado y la
   ruta sigue cabiendo; la mayor reduccion primero, hasta 100 mejoras por segmento.

No es una ruta optima: es una heuristica determinista. Es pura: no lee la base, el reloj, el azar ni
la red; `ruteo_v2.ejecuciones` le da la matriz que pidio al proveedor de ruteo.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from uuid import UUID, uuid5

from motor_cartera.campo_v2.politica import PRIORIDADES as PRIORIDADES_CAMPO
from motor_cartera.invariantes import presente

VERSION_MOTOR = "ruteo/v2"
ALGORITMO = "PRIORITY_NEAREST_NEIGHBOR_2OPT/v1"
PRIORIDADES = tuple(p.value for p in PRIORIDADES_CAMPO)
"""MUY_ALTA, ALTA, MEDIA, BAJA: el orden formal de decision/v2, el mismo de campo/v2."""

ESPACIO_RUTEO = UUID("6d0c2f5e-8b1a-4c3e-9f7d-2a4b6c8e0f13")
"""El espacio de nombres del identificador de cada ruta. No cambia nunca: cambiarlo cambiaria el
ruta_id de cada ruta publicada."""


class EstadoParada(StrEnum):
    """En que queda cada parada de una ruta. Exactamente uno."""

    PROGRAMADA = "PROGRAMADA"
    SIN_TIEMPO = "SIN_TIEMPO"
    NO_ENRUTABLE = "NO_ENRUTABLE"
    NO_ENRUTABLE_BASE = "NO_ENRUTABLE_BASE"


class MotivoParada(StrEnum):
    """Por que una parada no quedo PROGRAMADA, en un catalogo cerrado."""

    SIN_SNAP = "SIN_SNAP"
    """NO_ENRUTABLE: el proveedor dijo que el punto no tiene un segmento de la red."""
    SNAP_EXCESIVO = "SNAP_EXCESIVO"
    """NO_ENRUTABLE: el segmento de la red mas cercano esta a mas de 500.00 m del punto."""
    RED_DESCONECTADA = "RED_DESCONECTADA"
    """NO_ENRUTABLE: tiene snap, pero no hay camino de la base a la parada o de la parada a la
    base."""
    BASE_NO_ENRUTABLE = "BASE_NO_ENRUTABLE"
    """NO_ENRUTABLE_BASE: la base del gestor no tiene snap valido; no hay ruta."""
    JORNADA_AGOTADA = "JORNADA_AGOTADA"
    """SIN_TIEMPO: enrutable, pero ya no cabia en la jornada despues de las que se programaron."""
    PRIORIDAD_SUPERIOR_AGOTO_JORNADA = "PRIORIDAD_SUPERIOR_AGOTO_JORNADA"
    """SIN_TIEMPO: una parada enrutable de mayor prioridad no cupo, asi que su nivel no se
    programo."""
    EXCEDE_JORNADA = "EXCEDE_JORNADA"
    """SIN_TIEMPO: no cabe ni sola: su traslado, su servicio y su regreso no caben en la jornada
    vacia. No bloquea a las de menor prioridad."""
    SIN_JORNADA = "SIN_JORNADA"
    """SIN_TIEMPO: el gestor no tiene ninguna ventana en la fecha."""


MOTIVOS_DEL_ESTADO = {
    EstadoParada.PROGRAMADA: frozenset(),
    EstadoParada.SIN_TIEMPO: frozenset(
        {
            MotivoParada.JORNADA_AGOTADA,
            MotivoParada.PRIORIDAD_SUPERIOR_AGOTO_JORNADA,
            MotivoParada.EXCEDE_JORNADA,
            MotivoParada.SIN_JORNADA,
        }
    ),
    EstadoParada.NO_ENRUTABLE: frozenset(
        {MotivoParada.SIN_SNAP, MotivoParada.SNAP_EXCESIVO, MotivoParada.RED_DESCONECTADA}
    ),
    EstadoParada.NO_ENRUTABLE_BASE: frozenset({MotivoParada.BASE_NO_ENRUTABLE}),
}
"""Que motivos admite cada estado: una PROGRAMADA no tiene motivo, y las demas tienen uno de los
suyos."""


class EstadoRuta(StrEnum):
    """Como queda la ruta de un gestor."""

    COMPLETA = "COMPLETA"
    """Todas sus paradas quedaron PROGRAMADA."""
    PARCIAL = "PARCIAL"
    """Tiene paradas enrutables, y al menos una parada no quedo PROGRAMADA (por tiempo o porque no
    es enrutable). Puede no tener ninguna programada, si ninguna enrutable cabe."""
    SIN_PARADAS_ENRUTABLES = "SIN_PARADAS_ENRUTABLES"
    """La base es enrutable, pero ninguna parada lo es."""
    BASE_NO_ENRUTABLE = "BASE_NO_ENRUTABLE"
    SIN_JORNADA = "SIN_JORNADA"
    """El gestor no tiene ventanas en la fecha. campo/v2 no le asigna cuentas a un gestor asi: es
    una defensa, no un caso esperado."""


# --- la politica como datos -----------------------------------------------------------------------


@dataclass(frozen=True)
class PoliticaRuteo:
    """Una politica de ruteo/v2, como datos: lo que entra en su hash."""

    version: str
    max_snap_distancia_m: int
    duracion_servicio_s: int
    bloque_matriz: int
    max_mejoras_2opt: int
    perfil: str

    def __post_init__(self) -> None:
        if self.max_snap_distancia_m <= 0 or self.duracion_servicio_s < 0:
            raise ValueError("El snap maximo es positivo y el servicio no es negativo.")
        if self.bloque_matriz < 2 or self.max_mejoras_2opt < 0:
            raise ValueError("Un bloque de la matriz tiene al menos dos puntos por lado.")

    @property
    def max_snap_cm(self) -> int:
        return self.max_snap_distancia_m * 100

    def definicion(self) -> dict:
        return {
            "version": self.version,
            "motor": VERSION_MOTOR,
            "algoritmo": ALGORITMO,
            "universo": "las asignaciones ASIGNADA de campo/v2: una ruta por gestor con al menos "
            "una, con su base; nunca se reasigna una cuenta a otro gestor ni a otra base",
            "prioridad": list(PRIORIDADES),
            "inicio_fin_base": True,
            "perfil": self.perfil,
            "snap": {
                "max_distancia_m": self.max_snap_distancia_m,
                "regla": "distancia del punto a su segmento, al centimetro con ROUND_HALF_UP; "
                "permitido si es <= max_distancia_m",
                "sin_snap": "NO_ENRUTABLE (SIN_SNAP)",
                "excesivo": "NO_ENRUTABLE (SNAP_EXCESIVO)",
                "base": "si la base no es enrutable, no hay ruta: BASE_NO_ENRUTABLE",
            },
            "conectividad": "sin duracion de la base a la parada o de la parada a la base: "
            "NO_ENRUTABLE (RED_DESCONECTADA); nunca una duracion inventada",
            "servicio_s": self.duracion_servicio_s,
            "ventanas": "[inicio, fin) en instantes UTC; las que se tocan se unen; un traslado y "
            "un servicio caben enteros en una ventana, o esperan a la siguiente donde esta el "
            "gestor; en los huecos no hay trabajo",
            "jornada": "sale de la base al inicio de su primera ventana, o en cuanto su primer "
            "traslado cabe; cada parada reserva el regreso a la base, que termina dentro de una "
            "ventana",
            "construccion": "por nivel de prioridad, la parada de menor duracion desde la "
            "ubicacion actual que cabe con su regreso; empate por cuenta_id; si una enrutable de "
            "un nivel no cabe, los niveles inferiores no se programan; EXCEDE_JORNADA (no cabe ni "
            "sola) no bloquea",
            "2opt": {
                "alcance": "segmentos de una misma prioridad",
                "acepta": "reduce la duracion de traslado, con regreso, y la ruta sigue cabiendo",
                "eleccion": "la mayor reduccion; un empate por posicion",
                "max_mejoras_por_segmento": self.max_mejoras_2opt,
            },
            "redondeo": {
                "duracion_s": "entero; el decimal de la red con ROUND_HALF_UP",
                "distancia_m": "entero; el decimal de la red con ROUND_HALF_UP",
                "snap": "centimetro; ROUND_HALF_UP",
                "numeros": "se leen como decimales, nunca como float binario",
            },
            "matriz": {
                "bloque": self.bloque_matriz,
                "orden": "la base y despues los puntos de las paradas por cuenta_id; una "
                "coordenada repetida es un solo punto",
            },
            "no_usa": [
                "trafico",
                "distancia en linea recta",
                "duracion por cuenta",
                "saldo",
                "otra base",
                "otro gestor",
                "ventanas por cliente",
            ],
        }


PUBLIC_V2 = PoliticaRuteo(
    version="ruteo-public-v2",
    max_snap_distancia_m=500,
    duracion_servicio_s=900,
    bloque_matriz=100,
    max_mejoras_2opt=100,
    perfil="car",
)
POLITICAS = {PUBLIC_V2.version: PUBLIC_V2}


def canonico(datos) -> bytes:
    """JSON canonico: llaves en orden, sin espacios, UTF-8. Lo que se hashea."""
    return json.dumps(datos, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def hash_politica(politica: PoliticaRuteo = PUBLIC_V2) -> str:
    """SHA-256 de la definicion canonica de la politica."""
    return hashlib.sha256(canonico(politica.definicion())).hexdigest()


def firma_inputs(
    *,
    campo_run_id,
    firma_campo: str,
    red_vial_version_id,
    huella_red: str,
    hash_manifest: str,
    version_motor_ruteo: str,
    hash_perfil: str,
    politica: PoliticaRuteo = PUBLIC_V2,
) -> str:
    """El SHA-256 de las entradas de una ejecucion: el motor, la politica con su hash, el plan de
    campo/v2 con su firma y la version de la red vial con su huella, su manifiesto, la version del
    motor de ruteo y el hash de su perfil. Las mismas entradas son la misma ejecucion."""
    lineas = [
        VERSION_MOTOR,
        politica.version,
        hash_politica(politica),
        str(campo_run_id),
        firma_campo,
        str(red_vial_version_id),
        huella_red,
        hash_manifest,
        version_motor_ruteo,
        hash_perfil,
    ]
    return hashlib.sha256("\n".join(lineas).encode("utf-8")).hexdigest()


def ruta_id(firma: str, gestor_id: UUID) -> UUID:
    """El identificador publico de la ruta de un gestor: UUIDv5 de la firma de la ejecucion y del
    gestor. Las mismas entradas dan las mismas rutas."""
    return uuid5(ESPACIO_RUTEO, f"{firma}|{gestor_id}")


# --- la canonicalizacion numerica -----------------------------------------------------------------

_UNO = Decimal("1")
_CENTIMO = Decimal("0.01")


def _decimal(valor: Decimal | int | str) -> Decimal:
    if isinstance(valor, bool) or isinstance(valor, float):
        raise TypeError(
            f"Un numero de la red se lee como decimal, nunca como float binario: {valor!r}."
        )
    numero = Decimal(valor)
    if not numero.is_finite() or numero < 0:
        raise ValueError(
            f"Una duracion o una distancia de la red es finita y no negativa: {valor}."
        )
    return numero


def segundos(valor: Decimal | int | str) -> int:
    """Una duracion de la red, en segundos enteros: ROUND_HALF_UP del decimal."""
    return int(_decimal(valor).quantize(_UNO, rounding=ROUND_HALF_UP))


def metros(valor: Decimal | int | str) -> int:
    """Una distancia de la red, en metros enteros: ROUND_HALF_UP del decimal."""
    return int(_decimal(valor).quantize(_UNO, rounding=ROUND_HALF_UP))


def centimetros(valor: Decimal | int | str) -> int:
    """La distancia de un punto a su segmento, en centimetros enteros: ROUND_HALF_UP al
    centimetro. 500.004 m son 50000 cm (permitido); 500.005 m, 50001 cm (no)."""
    return int(_decimal(valor).quantize(_CENTIMO, rounding=ROUND_HALF_UP) * 100)


def motivo_de_snap(snap_cm: int | None, politica: PoliticaRuteo = PUBLIC_V2) -> MotivoParada | None:
    """None si el snap permite enrutar; si no, por que: sin snap, o a mas del maximo."""
    if snap_cm is None:
        return MotivoParada.SIN_SNAP
    if snap_cm > politica.max_snap_cm:
        return MotivoParada.SNAP_EXCESIVO
    return None


# --- las ventanas ---------------------------------------------------------------------------------

Ventana = tuple[int, int]
"""[inicio, fin), en segundos desde la epoca (UTC)."""


def normalizar_ventanas(ventanas: Iterable[Ventana]) -> tuple[Ventana, ...]:
    """Las ventanas en orden, con las que se tocan unidas en una. Una ventana vacia o al reves, o
    dos encimadas, son un error de datos: campo/v2 no las publica."""
    orden = sorted((int(i), int(f)) for i, f in ventanas)
    unidas: list[list[int]] = []
    for inicio, fin in orden:
        if inicio >= fin:
            raise ValueError(f"Una ventana empieza antes de terminar: {inicio} a {fin}.")
        if unidas and inicio < unidas[-1][1]:
            raise ValueError("Dos ventanas de la jornada se enciman.")
        if unidas and inicio == unidas[-1][1]:
            unidas[-1][1] = fin
        else:
            unidas.append([inicio, fin])
    return tuple((i, f) for i, f in unidas)


def colocar(desde: int, duracion: int, ventanas: Sequence[Ventana]) -> int | None:
    """El primer instante, no antes de `desde`, en que una actividad de `duracion` segundos cabe
    entera en una ventana: [t, t + duracion] dentro de [inicio, fin]. None si no cabe en ninguna."""
    for inicio, fin in ventanas:
        t = max(desde, inicio)
        if t + duracion <= fin:
            return t
    return None


# --- las entradas y las salidas -------------------------------------------------------------------


@dataclass(frozen=True)
class ParadaEntrada:
    """Lo que el planificador necesita de una parada: su cuenta (publica y estable, para los
    empates), su prioridad, su snap en centimetros (None: sin snap) y, si el snap es valido, su
    punto en la matriz (la base es el 0)."""

    clave: UUID
    prioridad: str
    snap_cm: int | None
    nodo: int | None = None


@dataclass(frozen=True)
class Matriz:
    """Duraciones (s) y distancias (m) enteras entre los puntos de una ruta, origen por destino.
    None es que la red no tiene camino."""

    duracion: Sequence[Sequence[int | None]]
    distancia: Sequence[Sequence[int | None]]

    def __post_init__(self) -> None:
        n = len(self.duracion)
        if len(self.distancia) != n or any(len(f) != n for f in (*self.duracion, *self.distancia)):
            raise ValueError("La matriz es cuadrada, con las mismas filas de duracion y distancia.")
        for fila_d, fila_m in zip(self.duracion, self.distancia, strict=True):
            for d, m in zip(fila_d, fila_m, strict=True):
                if (d is None) != (m is None):
                    raise ValueError("Una celda tiene duracion y distancia, o ninguna de las dos.")

    @property
    def puntos(self) -> int:
        return len(self.duracion)


@dataclass(frozen=True)
class ParadaPlaneada:
    """Lo que concluye el planificador de una parada. Los instantes van en segundos desde la epoca
    (UTC); solo una PROGRAMADA los tiene."""

    clave: UUID
    prioridad: str
    estado: EstadoParada
    motivo: MotivoParada | None
    snap_cm: int | None
    orden: int | None = None
    salida_anterior: int | None = None
    """Cuando sale de la base o de la parada anterior hacia esta."""
    llegada: int | None = None
    inicio_servicio: int | None = None
    fin_servicio: int | None = None
    distancia_desde_anterior_m: int | None = None
    duracion_desde_anterior_s: int | None = None
    espera_s: int | None = None
    """Lo que espero desde que termino lo anterior hasta empezar su servicio, sin el traslado: la
    pausa entre ventanas, si la hubo. En la primera parada no cuenta lo que el gestor espero en la
    base antes de salir."""


@dataclass(frozen=True)
class RutaPlaneada:
    estado: EstadoRuta
    motivo_base: MotivoParada | None
    base_snap_cm: int | None
    paradas: tuple[ParadaPlaneada, ...]
    """Las PROGRAMADA en su orden de visita, y despues las demas por cuenta_id."""
    jornada_inicio: int | None
    jornada_fin: int | None
    salida_base: int | None
    salida_regreso: int | None
    regreso_base: int | None
    distancia_regreso_m: int | None
    duracion_regreso_s: int | None
    espera_regreso_s: int | None
    distancia_total_m: int
    duracion_traslado_s: int
    duracion_servicio_s: int
    duracion_espera_s: int
    duracion_total_s: int
    hash_matriz: str | None
    hash_secuencia: str
    mejoras_2opt: int = 0

    def contar(self, estado: EstadoParada) -> int:
        return sum(1 for p in self.paradas if p.estado == estado)

    @property
    def programadas(self) -> int:
        return self.contar(EstadoParada.PROGRAMADA)


# --- la simulacion de la jornada ------------------------------------------------------------------

_INFINITO = float("inf")


def _nodo(p: ParadaEntrada) -> int:
    """El punto de una parada enrutable en la matriz: _con_base ya verifico que lo tiene."""
    return presente(p.nodo, "el punto de una parada enrutable")


def _programado(valor: int | None) -> int:
    """Una medida de una parada PROGRAMADA: la simulacion se las da todas."""
    return presente(valor, "una medida de una parada PROGRAMADA")


@dataclass(frozen=True)
class _Paso:
    """Una parada en la simulacion: cuando sale hacia ella, cuando llega y su servicio."""

    salida: int
    llegada: int
    inicio: int
    fin: int
    espera: int


@dataclass(frozen=True)
class _Simulacion:
    pasos: tuple[_Paso, ...]
    salida_regreso: int
    regreso: int
    espera_regreso: int


def _anexar(
    desde_nodo: int,
    libre: int,
    nodo: int,
    duracion: Sequence[Sequence[int | None]],
    ventanas: Sequence[Ventana],
    servicio: int,
) -> tuple[_Paso, int, int] | None:
    """El paso que agrega `nodo` despues de estar libre en `desde_nodo` a las `libre`, y la salida
    y la llegada de su regreso a la base. None si el traslado, el servicio o el regreso no caben, o
    si la red no tiene camino."""
    ida = duracion[desde_nodo][nodo]
    regreso = duracion[nodo][0]
    if ida is None or regreso is None:
        return None
    salida = colocar(libre, ida, ventanas)
    if salida is None:
        return None
    llegada = salida + ida
    inicio = colocar(llegada, servicio, ventanas)
    if inicio is None:
        return None
    fin = inicio + servicio
    salida_regreso = colocar(fin, regreso, ventanas)
    if salida_regreso is None:
        return None
    paso = _Paso(salida, llegada, inicio, fin, (salida - libre) + (inicio - llegada))
    return paso, salida_regreso, salida_regreso + regreso


def _simular(
    nodos: Sequence[int],
    duracion: Sequence[Sequence[int | None]],
    ventanas: Sequence[Ventana],
    servicio: int,
) -> _Simulacion | None:
    """La jornada de una secuencia de puntos (sin la base), o None si algo no cabe. Sale de la base
    al empezar la primera ventana, o en cuanto su primer traslado cabe; lo que espera en la base
    antes de salir no es espera de la ruta."""
    if not nodos:
        raise ValueError("Una ruta simulada tiene al menos una parada.")
    libre, actual = ventanas[0][0], 0
    pasos: list[_Paso] = []
    salida_regreso = regreso = 0
    for nodo in nodos:
        resultado = _anexar(actual, libre, nodo, duracion, ventanas, servicio)
        if resultado is None:
            return None
        paso, salida_regreso, regreso = resultado
        if not pasos:
            paso = _Paso(
                paso.salida, paso.llegada, paso.inicio, paso.fin, paso.inicio - paso.llegada
            )
        pasos.append(paso)
        libre, actual = paso.fin, nodo
    return _Simulacion(tuple(pasos), salida_regreso, regreso, salida_regreso - libre)


def _traslado(nodos: Sequence[int], duracion: Sequence[Sequence[int | None]]) -> float:
    """La duracion de traslado de una ruta cerrada: de la base, por cada punto, a la base."""
    total = 0.0
    previo = 0
    for nodo in (*nodos, 0):
        d = duracion[previo][nodo]
        if d is None:
            return _INFINITO
        total += d
        previo = nodo
    return total


# --- la construccion y el 2-opt -------------------------------------------------------------------


def _construir(
    niveles: Sequence[Sequence[ParadaEntrada]],
    duracion: Sequence[Sequence[int | None]],
    ventanas: Sequence[Ventana],
    servicio: int,
) -> tuple[list[ParadaEntrada], dict[UUID, MotivoParada]]:
    """El vecino mas cercano por nivel de prioridad. Devuelve la secuencia y el motivo de cada
    parada enrutable que no entro."""
    secuencia: list[ParadaEntrada] = []
    fuera: dict[UUID, MotivoParada] = {}
    libre, actual = ventanas[0][0], 0
    bloqueado = False
    for nivel in niveles:
        if bloqueado:
            fuera.update({p.clave: MotivoParada.PRIORIDAD_SUPERIOR_AGOTO_JORNADA for p in nivel})
            continue
        pendientes = list(nivel)
        while pendientes:

            def cercania(p: ParadaEntrada, desde: int = actual) -> tuple[float, UUID]:
                d = duracion[desde][_nodo(p)]
                return (_INFINITO if d is None else d, p.clave)

            pendientes.sort(key=cercania)
            elegido = None
            for candidata in pendientes:
                resultado = _anexar(actual, libre, _nodo(candidata), duracion, ventanas, servicio)
                if resultado is not None:
                    elegido = candidata, resultado[0]
                    break
            if elegido is None:
                break
            candidata, paso = elegido
            secuencia.append(candidata)
            pendientes.remove(candidata)
            libre, actual = paso.fin, _nodo(candidata)
        if pendientes:
            fuera.update({p.clave: MotivoParada.JORNADA_AGOTADA for p in pendientes})
            bloqueado = True
    return secuencia, fuera


def _segmentos(secuencia: Sequence[ParadaEntrada]) -> list[tuple[int, int]]:
    """Las posiciones [desde, hasta] de cada tramo de la secuencia con una misma prioridad."""
    segmentos: list[tuple[int, int]] = []
    inicio = 0
    for i in range(1, len(secuencia) + 1):
        if i == len(secuencia) or secuencia[i].prioridad != secuencia[inicio].prioridad:
            segmentos.append((inicio, i - 1))
            inicio = i
    return segmentos


def _dos_opt(
    secuencia: list[ParadaEntrada],
    duracion: Sequence[Sequence[int | None]],
    ventanas: Sequence[Ventana],
    servicio: int,
    max_mejoras: int,
) -> tuple[list[ParadaEntrada], int]:
    """2-opt dentro de cada segmento de una misma prioridad: invertir las paradas de las posiciones
    i a j. En cada pasada se calcula la reduccion de traslado de cada inversion posible del segmento
    (con la matriz asimetrica: el tramo invertido se recorre al reves), se prueban de la mayor
    reduccion a la menor (un empate, por posicion) y se acepta la primera con la que la ruta sigue
    cabiendo. Termina cuando ninguna mejora cabe, o con `max_mejoras` por segmento."""
    mejoras_totales = 0
    for desde, hasta in _segmentos(secuencia):
        mejoras = 0
        while mejoras < max_mejoras and hasta - desde >= 1:
            nodos = [0, *(_nodo(p) for p in secuencia), 0]
            adelante = [0.0]
            atras = [0.0]
            for u in range(len(nodos) - 1):
                d = duracion[nodos[u]][nodos[u + 1]]
                r = duracion[nodos[u + 1]][nodos[u]]
                adelante.append(adelante[-1] + (_INFINITO if d is None else d))
                atras.append(atras[-1] + (_INFINITO if r is None else r))
            candidatos: list[tuple[float, int, int]] = []
            # Las posiciones de la secuencia son las de `nodos` menos uno: la base va en el 0.
            for i in range(desde + 1, hasta + 2):
                for j in range(i + 1, hasta + 2):
                    entra = duracion[nodos[i - 1]][nodos[j]]
                    sale = duracion[nodos[i]][nodos[j + 1]]
                    if entra is None or sale is None:
                        continue
                    # El traslado de hoy de i - 1 a j + 1, por la secuencia: sus tramos tienen
                    # camino, porque la secuencia ya cabe.
                    antes = adelante[j + 1] - adelante[i - 1]
                    despues = entra + (atras[j] - atras[i]) + sale
                    delta = despues - antes
                    if delta < 0:
                        candidatos.append((delta, i, j))
            candidatos.sort()
            aceptado = False
            for _delta, i, j in candidatos:
                propuesta = [*secuencia[: i - 1], *reversed(secuencia[i - 1 : j]), *secuencia[j:]]
                if (
                    _simular([_nodo(p) for p in propuesta], duracion, ventanas, servicio)
                    is not None
                ):
                    secuencia = propuesta
                    aceptado = True
                    break
            if not aceptado:
                break
            mejoras += 1
        mejoras_totales += mejoras
    return secuencia, mejoras_totales


# --- la ruta de un gestor -------------------------------------------------------------------------


def planear(
    paradas: Sequence[ParadaEntrada],
    ventanas: Iterable[Ventana],
    *,
    base_snap_cm: int | None,
    matriz: Matriz | None,
    politica: PoliticaRuteo = PUBLIC_V2,
    medidor=None,
) -> RutaPlaneada:
    """La ruta de un gestor con ruteo-public-v2. `paradas` son sus cuentas ASIGNADA; `ventanas`, su
    jornada en la fecha; `base_snap_cm`, el snap de su base (None: sin snap); `matriz`, la de la
    base (el punto 0) y los puntos de las paradas con snap valido, que cada parada nombra con su
    `nodo`. Sin jornada o sin base enrutable, la matriz no hace falta. `medidor`, si lo hay,
    mide sus fases (con un `fase(nombre)` como el del cronometro): no cambia nada de lo que
    concluye."""
    if not paradas:
        raise ValueError(
            "Una ruta tiene al menos una parada: la de un gestor sin cuentas no existe."
        )
    claves = [p.clave for p in paradas]
    if len(set(claves)) != len(claves):
        raise ValueError("Una cuenta aparece dos veces en la misma ruta.")
    if any(p.prioridad not in PRIORIDADES for p in paradas):
        raise ValueError(f"Una prioridad no es de {PRIORIDADES}.")
    ordenadas = sorted(paradas, key=lambda p: p.clave)
    jornada = normalizar_ventanas(ventanas)
    if not jornada:
        return _sin_ruta(
            ordenadas,
            EstadoRuta.SIN_JORNADA,
            EstadoParada.SIN_TIEMPO,
            MotivoParada.SIN_JORNADA,
            None,
            base_snap_cm,
            None,
            con_snap=False,
        )
    motivo_base = motivo_de_snap(base_snap_cm, politica)
    if motivo_base is not None:
        return _sin_ruta(
            ordenadas,
            EstadoRuta.BASE_NO_ENRUTABLE,
            EstadoParada.NO_ENRUTABLE_BASE,
            MotivoParada.BASE_NO_ENRUTABLE,
            motivo_base,
            base_snap_cm,
            jornada,
            con_snap=True,
        )
    base = presente(base_snap_cm, "el snap de una base enrutable")
    return _con_base(ordenadas, jornada, base, matriz, politica, _fases(medidor))


def _sin_ruta(
    ordenadas: Sequence[ParadaEntrada],
    estado_ruta: EstadoRuta,
    estado: EstadoParada,
    motivo: MotivoParada,
    motivo_base: MotivoParada | None,
    base_snap_cm: int | None,
    jornada: Sequence[Ventana] | None,
    *,
    con_snap: bool,
) -> RutaPlaneada:
    planeadas = tuple(
        ParadaPlaneada(p.clave, p.prioridad, estado, motivo, p.snap_cm if con_snap else None)
        for p in ordenadas
    )
    return _ruta(
        estado_ruta,
        motivo_base,
        base_snap_cm if con_snap else None,
        planeadas,
        jornada,
        None,
        None,
        None,
    )


def _fases(medidor):
    """El `fase(nombre)` del medidor, o uno que no mide."""
    if medidor is None:
        return lambda _nombre: nullcontext()
    return medidor.fase


def _con_base(
    ordenadas: Sequence[ParadaEntrada],
    jornada: Sequence[Ventana],
    base_snap_cm: int,
    matriz: Matriz | None,
    politica: PoliticaRuteo,
    fase=None,
) -> RutaPlaneada:
    fase = fase or _fases(None)
    servicio = politica.duracion_servicio_s
    con_snap = [p for p in ordenadas if motivo_de_snap(p.snap_cm, politica) is None]
    if con_snap and matriz is None:
        raise ValueError("Hay paradas con snap valido y no hay matriz.")
    for p in ordenadas:
        valido = motivo_de_snap(p.snap_cm, politica) is None
        if valido != (p.nodo is not None):
            raise ValueError("Una parada con snap valido tiene su punto en la matriz, y solo ella.")
        # Con snap valido hay matriz (si no, ya se rechazo) y su punto (la linea anterior).
        if matriz is not None and p.nodo is not None and not (0 < p.nodo < matriz.puntos):
            raise ValueError(f"El punto {p.nodo} no esta en la matriz.")
    duracion: Sequence[Sequence[int | None]] = matriz.duracion if matriz is not None else []
    motivos: dict[UUID, MotivoParada] = {}
    enrutables: list[ParadaEntrada] = []
    for p in ordenadas:
        motivo = motivo_de_snap(p.snap_cm, politica)
        if motivo is None and (duracion[0][_nodo(p)] is None or duracion[_nodo(p)][0] is None):
            motivo = MotivoParada.RED_DESCONECTADA
        if motivo is not None:
            motivos[p.clave] = motivo
            continue
        enrutables.append(p)
    with fase("secuenciacion"):
        cabe_sola = [p for p in enrutables if _simular([_nodo(p)], duracion, jornada, servicio)]
        motivos.update(
            {p.clave: MotivoParada.EXCEDE_JORNADA for p in enrutables if p not in cabe_sola}
        )
        niveles = [[p for p in cabe_sola if p.prioridad == nivel] for nivel in PRIORIDADES]
        secuencia, fuera = _construir(niveles, duracion, jornada, servicio)
    motivos.update(fuera)
    mejoras = 0
    if len(secuencia) > 1:
        with fase("2opt"):
            secuencia, mejoras = _dos_opt(
                secuencia, duracion, jornada, servicio, politica.max_mejoras_2opt
            )
    planeadas: list[ParadaPlaneada] = []
    salida_base = salida_regreso = regreso = None
    distancia_regreso = duracion_regreso = espera_regreso = None
    if secuencia:
        with fase("simulacion_temporal"):
            simulacion = _simular([_nodo(p) for p in secuencia], duracion, jornada, servicio)
        if (
            simulacion is None
        ):  # pragma: no cover - la construccion y el 2-opt solo dejan lo que cabe
            raise AssertionError("La secuencia construida no cabe en su jornada.")
        distancias = presente(matriz, "la matriz de una ruta con paradas").distancia
        previo = 0
        for orden, (p, paso) in enumerate(zip(secuencia, simulacion.pasos, strict=True), start=1):
            nodo = _nodo(p)
            planeadas.append(
                ParadaPlaneada(
                    p.clave,
                    p.prioridad,
                    EstadoParada.PROGRAMADA,
                    None,
                    p.snap_cm,
                    orden,
                    paso.salida,
                    paso.llegada,
                    paso.inicio,
                    paso.fin,
                    distancias[previo][nodo],
                    duracion[previo][nodo],
                    paso.espera,
                )
            )
            previo = nodo
        salida_base = simulacion.pasos[0].salida
        salida_regreso, regreso = simulacion.salida_regreso, simulacion.regreso
        distancia_regreso = distancias[previo][0]
        duracion_regreso = duracion[previo][0]
        espera_regreso = simulacion.espera_regreso
    no_enrutables = MOTIVOS_DEL_ESTADO[EstadoParada.NO_ENRUTABLE]
    for p in ordenadas:
        if p.clave in motivos:
            motivo = motivos[p.clave]
            estado = (
                EstadoParada.NO_ENRUTABLE if motivo in no_enrutables else EstadoParada.SIN_TIEMPO
            )
            planeadas.append(ParadaPlaneada(p.clave, p.prioridad, estado, motivo, p.snap_cm))
    if all(motivos.get(p.clave) in no_enrutables for p in ordenadas):
        estado_ruta = EstadoRuta.SIN_PARADAS_ENRUTABLES
    elif len(secuencia) == len(ordenadas):
        estado_ruta = EstadoRuta.COMPLETA
    else:
        estado_ruta = EstadoRuta.PARCIAL
    return _ruta(
        estado_ruta,
        None,
        base_snap_cm,
        tuple(planeadas),
        jornada,
        (salida_base, salida_regreso, regreso),
        (distancia_regreso, duracion_regreso, espera_regreso),
        _hash_matriz(ordenadas, matriz, politica) if matriz is not None else None,
        mejoras,
    )


def _ruta(
    estado: EstadoRuta,
    motivo_base: MotivoParada | None,
    base_snap_cm: int | None,
    paradas: tuple[ParadaPlaneada, ...],
    jornada: Sequence[Ventana] | None,
    instantes: tuple[int | None, int | None, int | None] | None,
    regreso: tuple[int | None, int | None, int | None] | None,
    hash_matriz: str | None,
    mejoras: int = 0,
) -> RutaPlaneada:
    salida_base, salida_regreso, regreso_base = instantes or (None, None, None)
    distancia_regreso, duracion_regreso, espera_regreso = regreso or (None, None, None)
    programadas = [p for p in paradas if p.estado == EstadoParada.PROGRAMADA]
    distancia = sum(_programado(p.distancia_desde_anterior_m) for p in programadas) + (
        distancia_regreso or 0
    )
    traslado = sum(_programado(p.duracion_desde_anterior_s) for p in programadas) + (
        duracion_regreso or 0
    )
    espera = sum(_programado(p.espera_s) for p in programadas) + (espera_regreso or 0)
    servicio = sum(
        _programado(p.fin_servicio) - _programado(p.inicio_servicio) for p in programadas
    )
    total = (
        0
        if salida_base is None
        else presente(regreso_base, "el regreso de una ruta que salio") - salida_base
    )
    if total != traslado + servicio + espera:  # pragma: no cover - una invariante de la simulacion
        raise AssertionError("La jornada no cuadra: total, traslado, servicio y espera.")
    sin_hash = RutaPlaneada(
        estado,
        motivo_base,
        base_snap_cm,
        paradas,
        None if not jornada else jornada[0][0],
        None if not jornada else jornada[-1][1],
        salida_base,
        salida_regreso,
        regreso_base,
        distancia_regreso,
        duracion_regreso,
        espera_regreso,
        distancia,
        traslado,
        servicio,
        espera,
        total,
        hash_matriz,
        "",
        mejoras,
    )
    return RutaPlaneada(**{**sin_hash.__dict__, "hash_secuencia": hash_secuencia(sin_hash)})


def _hash_matriz(
    ordenadas: Sequence[ParadaEntrada], matriz: Matriz, politica: PoliticaRuteo
) -> str:
    """SHA-256 de la matriz de la ruta en su forma canonica: la base y las paradas con snap valido
    por cuenta_id, con las duraciones y distancias enteras de cada par (null si no hay camino). Sin
    coordenadas."""
    con_snap = [p for p in ordenadas if motivo_de_snap(p.snap_cm, politica) is None]
    nodos = [0, *(p.nodo for p in con_snap)]
    datos = {
        "puntos": ["BASE", *(str(p.clave) for p in con_snap)],
        "duracion_s": [[matriz.duracion[a][b] for b in nodos] for a in nodos],
        "distancia_m": [[matriz.distancia[a][b] for b in nodos] for a in nodos],
    }
    return hashlib.sha256(canonico(datos)).hexdigest()


def hash_secuencia(ruta: RutaPlaneada) -> str:
    """SHA-256 de lo que concluyo la ruta, en forma canonica: su estado, el snap de la base, cada
    parada (en su orden de visita, y las demas por cuenta_id) con su estado, su motivo y sus
    instantes, y el regreso a la base."""
    datos = {
        "estado": ruta.estado.value,
        "motivo_base": None if ruta.motivo_base is None else ruta.motivo_base.value,
        "base_snap_cm": ruta.base_snap_cm,
        "paradas": [
            [
                str(p.clave),
                p.prioridad,
                p.estado.value,
                None if p.motivo is None else p.motivo.value,
                p.snap_cm,
                p.orden,
                p.salida_anterior,
                p.llegada,
                p.inicio_servicio,
                p.fin_servicio,
                p.distancia_desde_anterior_m,
                p.duracion_desde_anterior_s,
                p.espera_s,
            ]
            for p in ruta.paradas
        ],
        "regreso": [
            ruta.salida_regreso,
            ruta.regreso_base,
            ruta.distancia_regreso_m,
            ruta.duracion_regreso_s,
            ruta.espera_regreso_s,
        ],
        "salida_base": ruta.salida_base,
    }
    return hashlib.sha256(canonico(datos)).hexdigest()


# --- los puntos de la matriz ----------------------------------------------------------------------


def puntos_de_matriz(
    coordenadas: Mapping[UUID, tuple[str, str]],
    snaps: Mapping[UUID, int | None],
    politica: PoliticaRuteo = PUBLIC_V2,
) -> tuple[list[tuple[str, str]], dict[UUID, int]]:
    """Los puntos distintos de las paradas con snap valido, en el orden canonico (por la primera
    cuenta_id que lo usa), despues de la base: el nodo de cada cuenta. Una coordenada repetida es un
    solo punto: la red da la misma respuesta para la misma coordenada."""
    unicos: dict[tuple[str, str], int] = {}
    nodos: dict[UUID, int] = {}
    for clave in sorted(coordenadas):
        if motivo_de_snap(snaps.get(clave), politica) is not None:
            continue
        coordenada = coordenadas[clave]
        if coordenada not in unicos:
            unicos[coordenada] = len(unicos) + 1
        nodos[clave] = unicos[coordenada]
    return list(unicos), nodos
