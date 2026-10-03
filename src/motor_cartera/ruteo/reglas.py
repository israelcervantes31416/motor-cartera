"""Las reglas de ruteo: `ruteo/v1`.

Responden en que secuencia visitar, dentro de un municipio, las cuentas que `decision/v1` mando a
CAMPO. No responden que cuentas van a campo, que ya lo decidio `decision/v1`, ni que municipio se
atiende primero, que ya lo ordeno `territorial/v1`: reciben los clientes de campo de un solo
municipio y devuelven una ruta que sale de un deposito, visita a cada uno una vez y regresa.

La cartera no trae coordenadas, y aqui no se inventan ubicaciones reales. Cada municipio tiene su
propio plano operativo sintetico, un cuadrado de 10 km por lado con el deposito en el centro, y
cada cliente recibe en el un punto determinista que sale de un SHA-256 de la version, el municipio
y el cliente. Esos puntos no son latitud ni longitud, no corresponden a ningun domicilio y no se
comparan entre municipios: existen para que el ruteo se pueda calcular, probar y explicar de punta
a punta sobre datos sinteticos.

La distancia es Manhattan, en metros sinteticos y con enteros. La ruta se construye por vecino mas
cercano y se mejora con hasta MAX_PASADAS_2OPT inversiones 2-opt. No hay gestores, vehiculos,
capacidades, horarios ni trafico: una ruta por municipio, sobre todos sus clientes de campo.

Son puras: no leen la base, archivos, el entorno, el reloj ni el azar, y no importan `decision`,
`territorial`, `db`, la API ni la configuracion. Los mismos clientes del mismo municipio con la
misma version dan siempre la misma ruta, lleguen en el orden que lleguen.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

VERSION_REGLAS_RUTEO = "ruteo/v1"
"""La version de estas reglas. Cada ejecucion de ruteo va a guardar con cual se calculo.

Es independiente de `VERSION_CONTRATO`, `VERSION_REGLAS_DECISION` y `VERSION_REGLAS_TERRITORIAL`.
Entra en el calculo de cada coordenada, asi que otra version movera todos los puntos. Cambia cuando
cambia cualquier resultado posible (una coordenada, la metrica, el algoritmo, un desempate o el
limite de mejoras), para que la misma version signifique siempre exactamente las mismas rutas.
"""

COORDENADA_MAXIMA_M = 5000
"""Cada coordenada sintetica va de -5000 a +5000 metros sinteticos, los dos incluidos: 10,001
valores por eje, en un cuadrado de 10 km por lado con el deposito en el centro."""

MAX_PASADAS_2OPT = 10
"""Cuantas inversiones 2-opt se aplican a lo mas, una por pasada.

Acota el trabajo de cada ruta sin mirar el reloj: con un limite de tiempo, la misma entrada daria
otra ruta en una maquina mas lenta. Vive aqui y no en la configuracion, como los umbrales de las
otras reglas: si se pudiera cambiar por entorno, la misma version daria rutas distintas.
"""

_ALFABETO_CLIENTE = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
"""Lo que puede traer un cliente_unico, como lo exige cartera/v1: mayusculas y digitos ASCII."""


# Los tipos se exigen exactos, sin subclases y sin convertir, como en las otras reglas: True no es
# una coordenada, y b"21114" se parece a una clave, pero no lo es.


def _exigir_clave_territorio(valor: object) -> None:
    if type(valor) is not str:
        raise TypeError(
            f"clave_territorio debe ser texto, no {type(valor).__name__}: una clave conserva sus "
            "ceros a la izquierda."
        )
    # isdigit solo no basta: tambien acepta digitos de otros alfabetos y superindices.
    if len(valor) != 5 or not (valor.isascii() and valor.isdigit()):
        raise ValueError(f"clave_territorio debe tener exactamente 5 digitos ASCII: {valor!r}.")


def _exigir_cliente(valor: object) -> None:
    if type(valor) is not str:
        raise TypeError(f"cliente_unico debe ser texto, no {type(valor).__name__}.")
    if not (8 <= len(valor) <= 20 and set(valor) <= _ALFABETO_CLIENTE):
        raise ValueError(
            f"cliente_unico debe tener de 8 a 20 caracteres, solo A-Z y 0-9: {valor!r}."
        )


def _exigir_coordenada(campo: str, valor: object) -> None:
    if type(valor) is not int:
        raise TypeError(
            f"{campo} debe ser un entero, no {type(valor).__name__}: las distancias se miden en "
            "metros sinteticos exactos."
        )
    if not -COORDENADA_MAXIMA_M <= valor <= COORDENADA_MAXIMA_M:
        raise ValueError(
            f"{campo} debe estar entre -{COORDENADA_MAXIMA_M} y {COORDENADA_MAXIMA_M}: {valor}."
        )


@dataclass(frozen=True)
class PuntoSintetico:
    """Un punto del plano operativo sintetico de un municipio, en metros sinteticos desde su
    deposito.

    No es latitud ni longitud, ni un domicilio: es un punto de un plano local que solo existe para
    rutear. Cada municipio tiene el suyo, asi que un punto de un municipio no se compara con uno de
    otro.
    """

    x_m: int
    y_m: int

    def __post_init__(self) -> None:
        _exigir_coordenada("x_m", self.x_m)
        _exigir_coordenada("y_m", self.y_m)


DEPOSITO = PuntoSintetico(0, 0)
"""De donde sale y adonde regresa cada ruta, en el centro del plano de su municipio. No es una
parada, porque ahi no se visita a nadie, pero la salida y el regreso cuentan en la distancia."""


@dataclass(frozen=True)
class ParadaCalculada:
    """Una cuenta de la ruta: quien es, en que lugar se visita, en que punto esta y a que
    distancia de la parada anterior."""

    cliente_unico: str
    secuencia: int
    """Su lugar en la ruta, desde 1 y sin huecos: el orden de visita dentro del municipio. No es
    posicion_campo, que es el lugar del municipio entre los municipios."""
    x_m: int
    y_m: int
    distancia_desde_anterior_m: int
    """Desde la parada anterior; la primera, desde el deposito."""


@dataclass(frozen=True)
class ResultadoRuta:
    """La ruta de un municipio: sus paradas en orden y sus distancias, en metros sinteticos.

    Trae lo necesario para guardarla sin volver a calcular nada y para explicar cada numero: las
    distancias de las paradas mas el regreso suman `distancia_total_m`, y la mejora es lo que el
    2-opt le quito a la ruta del vecino mas cercano.
    """

    clave_territorio: str
    paradas: tuple[ParadaCalculada, ...]
    distancia_inicial_m: int
    """La de la ruta del vecino mas cercano, antes del 2-opt, con el regreso al deposito."""
    distancia_total_m: int
    """La de la ruta final, despues del 2-opt, con el regreso al deposito."""
    distancia_regreso_deposito_m: int
    """De la ultima parada al deposito."""
    mejora_2opt_m: int
    """distancia_inicial_m - distancia_total_m. Nunca es negativa: el 2-opt solo aplica
    inversiones que acortan la ruta."""


def coordenada_sintetica(clave_territorio: str, cliente_unico: str) -> PuntoSintetico:
    """El punto de un cliente en el plano sintetico de su municipio, con `ruteo/v1`.

    Sale de SHA-256 sobre "ruteo/v1|{clave_territorio}|{cliente_unico}" en ASCII: los primeros 8
    bytes del digest, leidos como entero sin signo big-endian, dan x, y los 8 siguientes, y. Cada
    uno se lleva a [-5000, +5000] con modulo 10,001. Sin azar, semilla, configuracion, reloj ni el
    hash() de Python, que cambia de un proceso a otro: la misma version, el mismo municipio y el
    mismo cliente dan siempre el mismo punto. El municipio entra en el calculo, asi que el mismo
    cliente tiene otro punto en otro municipio.
    """
    _exigir_clave_territorio(clave_territorio)
    _exigir_cliente(cliente_unico)
    carga = f"{VERSION_REGLAS_RUTEO}|{clave_territorio}|{cliente_unico}".encode("ascii")
    digest = hashlib.sha256(carga).digest()
    return PuntoSintetico(_coordenada(digest[0:8]), _coordenada(digest[8:16]))


def _coordenada(ocho_bytes: bytes) -> int:
    valores_por_eje = 2 * COORDENADA_MAXIMA_M + 1
    return int.from_bytes(ocho_bytes, "big") % valores_por_eje - COORDENADA_MAXIMA_M


def distancia_m(a: PuntoSintetico, b: PuntoSintetico) -> int:
    """La distancia Manhattan entre dos puntos del mismo plano, en metros sinteticos:
    |x1 - x2| + |y1 - y2|.

    Con enteros es exacta: sin float, sin raiz cuadrada y sin redondeo. Se lee como un recorrido
    sobre una cuadricula de calles, que es todo lo que un plano sintetico puede decir: no es la
    distancia de una calle real, ni un tiempo.
    """
    if not (isinstance(a, PuntoSintetico) and isinstance(b, PuntoSintetico)):
        raise TypeError(
            f"Se esperaban dos PuntoSintetico, no {type(a).__name__} y {type(b).__name__}."
        )
    return abs(a.x_m - b.x_m) + abs(a.y_m - b.y_m)


def rutear_territorio(clave_territorio: str, clientes: Iterable[str]) -> ResultadoRuta:
    """La ruta de `ruteo/v1` por los clientes de campo de un municipio.

    Cada cliente es una parada, en el punto que le da `coordenada_sintetica`. La ruta sale del
    deposito, visita a cada uno una vez y regresa:

      1. vecino mas cercano: desde el deposito, siempre al cliente pendiente mas cercano; a igual
         distancia, al de cliente_unico menor;
      2. 2-opt: en cada pasada se evaluan todas las inversiones de un tramo i..j y se aplica la que
         mas acorta la ruta; a igual ahorro, la de i menor y despues la de j menor. Una por
         pasada, solo si acorta, y a lo mas MAX_PASADAS_2OPT.

    El resultado depende de que clientes llegan, no del orden en que llegan. Se valida antes de
    calcular, fail-closed: la clave, cada cliente, al menos uno y ninguno repetido. Un cliente
    repetido no se descarta: dos paradas con la misma cuenta son un error de quien las junto.
    """
    _exigir_clave_territorio(clave_territorio)
    # Un texto tambien es iterable, y cada letra pasaria por un cliente.
    if isinstance(clientes, str | bytes | bytearray):
        raise TypeError(
            f"clientes debe ser una coleccion de cliente_unico, no {type(clientes).__name__}."
        )
    puntos: dict[str, PuntoSintetico] = {}
    for cliente in clientes:
        punto = coordenada_sintetica(clave_territorio, cliente)
        if cliente in puntos:
            raise ValueError(
                f"El cliente {cliente} viene mas de una vez: cada cuenta es una sola parada."
            )
        puntos[cliente] = punto
    if not puntos:
        raise ValueError(
            "Una ruta necesita al menos un cliente: un municipio sin cuentas de campo no se rutea."
        )
    return _rutear(clave_territorio, puntos)


def _rutear(clave_territorio: str, puntos: Mapping[str, PuntoSintetico]) -> ResultadoRuta:
    """La ruta sobre puntos ya validados: vecino mas cercano, 2-opt y las distancias de cada
    parada. Aparte de `rutear_territorio` para poder probar la geometria con puntos escritos a
    mano."""
    inicial = _vecino_mas_cercano(puntos)
    final, _ = _dos_opt(inicial, puntos)
    paradas = []
    anterior = DEPOSITO
    for secuencia, cliente in enumerate(final, start=1):
        punto = puntos[cliente]
        paradas.append(
            ParadaCalculada(cliente, secuencia, punto.x_m, punto.y_m, distancia_m(anterior, punto))
        )
        anterior = punto
    regreso = distancia_m(anterior, DEPOSITO)
    total = sum(parada.distancia_desde_anterior_m for parada in paradas) + regreso
    distancia_inicial = _longitud(inicial, puntos)
    return ResultadoRuta(
        clave_territorio=clave_territorio,
        paradas=tuple(paradas),
        distancia_inicial_m=distancia_inicial,
        distancia_total_m=total,
        distancia_regreso_deposito_m=regreso,
        mejora_2opt_m=distancia_inicial - total,
    )


def _vecino_mas_cercano(puntos: Mapping[str, PuntoSintetico]) -> list[str]:
    """La ruta inicial: desde el deposito, siempre al cliente pendiente mas cercano a donde se esta;
    a igual distancia, al de cliente_unico menor. Como los clientes no se repiten, el desempate
    siempre decide, y la ruta no depende del orden en que llegan."""
    pendientes = set(puntos)
    ruta: list[str] = []
    actual = DEPOSITO
    while pendientes:
        siguiente = min(
            pendientes, key=lambda cliente: (distancia_m(actual, puntos[cliente]), cliente)
        )
        ruta.append(siguiente)
        pendientes.remove(siguiente)
        actual = puntos[siguiente]
    return ruta


def _dos_opt(inicial: Sequence[str], puntos: Mapping[str, PuntoSintetico]) -> tuple[list[str], int]:
    """Mejora la ruta con hasta MAX_PASADAS_2OPT inversiones, una por pasada: cada pasada evalua
    todas y aplica la de mayor ahorro. Termina antes si ninguna la acorta. Devuelve la ruta y
    cuantas inversiones aplico; cada una la acorto estrictamente."""
    ruta = list(inicial)
    for aplicadas in range(MAX_PASADAS_2OPT):
        inversion = _mejor_inversion(ruta, puntos)
        if inversion is None:
            return ruta, aplicadas
        i, j = inversion
        ruta[i : j + 1] = reversed(ruta[i : j + 1])
    return ruta, MAX_PASADAS_2OPT


def _mejor_inversion(
    ruta: Sequence[str], puntos: Mapping[str, PuntoSintetico]
) -> tuple[int, int] | None:
    """La inversion del tramo ruta[i..j] que mas acorta la ruta, o None si ninguna la acorta.

    Invertir un tramo solo cambia sus dos bordes: anterior -> ruta[i] y ruta[j] -> siguiente pasan
    a ser anterior -> ruta[j] y ruta[i] -> siguiente, donde anterior y siguiente son el deposito en
    los extremos. Por dentro, el tramo recorre las mismas aristas al reves, y Manhattan es
    simetrica: miden lo mismo. Asi el ahorro de cada inversion sale de esos cuatro bordes, sin
    rehacer la ruta.

    i y j se recorren de menor a mayor, y la candidata solo cambia con un ahorro estrictamente
    mayor: a igual ahorro queda la de i menor, y despues la de j menor. Una inversion con ahorro
    cero o negativo nunca se elige.
    """
    recorrido = [DEPOSITO, *(puntos[cliente] for cliente in ruta), DEPOSITO]
    # aristas[k] es la distancia de recorrido[k] a recorrido[k + 1], con la ruta como esta.
    aristas = [distancia_m(recorrido[k], recorrido[k + 1]) for k in range(len(recorrido) - 1)]
    mejor, mejor_ahorro = None, 0
    for i in range(1, len(recorrido) - 2):
        anterior, primero = recorrido[i - 1], recorrido[i]
        for j in range(i + 1, len(recorrido) - 1):
            ultimo, siguiente = recorrido[j], recorrido[j + 1]
            ahorro = (aristas[i - 1] + aristas[j]) - (
                distancia_m(anterior, ultimo) + distancia_m(primero, siguiente)
            )
            if ahorro > mejor_ahorro:
                mejor, mejor_ahorro = (i - 1, j - 1), ahorro
    return mejor


def _longitud(ruta: Sequence[str], puntos: Mapping[str, PuntoSintetico]) -> int:
    """Lo que mide la ruta completa: del deposito a la primera parada, de cada una a la siguiente
    y de la ultima de vuelta al deposito."""
    recorrido = [DEPOSITO, *(puntos[cliente] for cliente in ruta), DEPOSITO]
    return sum(distancia_m(recorrido[k], recorrido[k + 1]) for k in range(len(recorrido) - 1))
