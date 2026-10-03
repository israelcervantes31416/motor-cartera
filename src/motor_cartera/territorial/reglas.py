"""Las reglas territoriales: `territorial/v1`.

Responden donde se concentra la carga operativa que puede ir a gestion de campo: cuantas cuentas
de cada territorio tienen CAMPO como canal recomendado en una ejecucion de decision, y en que orden
conviene atender esos territorios. No responden que ruta debe recorrer un gestor: la secuencia
fisica, con distancias y tiempos, es del motor de ruteo (v0.4.0). Aqui un territorio es una unidad
para planear, no una ruta.

Un territorio es exactamente un municipio: su clave de entidad y su clave de municipio, nada mas.
La cartera no trae nombres, coordenadas ni codigos postales, y aqui no se inventan. Tampoco se
valida que la clave exista en el catalogo del INEGI: solo su forma.

La carga no es riesgo. El riesgo y la prioridad de cada cuenta ya los resolvio `decision/v1` al
recomendarle un canal; estas reglas cuentan cuantas cuentas acabaron en CAMPO, y no vuelven a leer
dias de atraso, saldos de cada cuenta ni prioridades. Tampoco cambian ninguna decision individual:
reciben agregados por territorio ya calculados y reorganizan por territorio decisiones que ya se
publicaron.

Son puras: no leen la base, archivos, el entorno, el reloj ni el azar, y no importan `decision`,
`db`, la API ni la configuracion. La misma coleccion de entradas con la misma version da siempre el
mismo resultado, en el mismo orden, llegue en el orden que llegue.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum

VERSION_REGLAS_TERRITORIAL = "territorial/v1"
"""La version de estas reglas. Cada ejecucion territorial va a guardar con cual se calculo.

Es independiente de `VERSION_CONTRATO` y de `VERSION_REGLAS_DECISION`: el contrato dice que datos
se publican; las reglas de decision, que hacer con cada cuenta; estas, como se reparte por
territorio el trabajo de campo que ya se decidio. Cambia cuando cambia cualquier resultado posible
(una carga, un umbral, un codigo de motivo o el orden), para que la misma version signifique
siempre exactamente las mismas reglas.
"""

UMBRAL_CARGA_MEDIA = 5
"""Desde cuantas cuentas de campo un territorio tiene carga MEDIA; con menos, y al menos una,
BAJA."""

UMBRAL_CARGA_ALTA = 20
"""Desde cuantas cuentas de campo un territorio tiene carga ALTA.

Los dos umbrales son valores sinteticos y demostrativos, propios de este proyecto publico: no
provienen de ninguna operacion real. Viven aqui y no en la configuracion: si se pudieran cambiar
por entorno, la misma version daria resultados distintos.
"""


class CargaTerritorial(StrEnum):
    """Cuanto trabajo de campo concentra un territorio, de menor a mayor.

    No es la prioridad de ninguna cuenta, que ya la dio `decision/v1`: es cuantas cuentas del
    territorio van a CAMPO.
    """

    SIN_CARGA = "SIN_CARGA"
    BAJA = "BAJA"
    MEDIA = "MEDIA"
    ALTA = "ALTA"


class CodigoMotivoTerritorial(StrEnum):
    """Por que un territorio tiene la carga que tiene, en un catalogo cerrado.

    No es el `CodigoMotivo` de `decision/v1`: aquel explica la decision de una cuenta, y este la
    carga de un territorio. Es lo que un cliente compara, como el `codigo` de un error: cambiar el
    nombre o el significado de un codigo exige una version nueva de estas reglas.
    """

    SIN_CARGA_CAMPO = "SIN_CARGA_CAMPO"
    CARGA_CAMPO_1_4 = "CARGA_CAMPO_1_4"
    CARGA_CAMPO_5_19 = "CARGA_CAMPO_5_19"
    CARGA_CAMPO_20_MAS = "CARGA_CAMPO_20_MAS"


MOTIVO_POR_CARGA: dict[CargaTerritorial, CodigoMotivoTerritorial] = {
    CargaTerritorial.SIN_CARGA: CodigoMotivoTerritorial.SIN_CARGA_CAMPO,
    CargaTerritorial.BAJA: CodigoMotivoTerritorial.CARGA_CAMPO_1_4,
    CargaTerritorial.MEDIA: CodigoMotivoTerritorial.CARGA_CAMPO_5_19,
    CargaTerritorial.ALTA: CodigoMotivoTerritorial.CARGA_CAMPO_20_MAS,
}
"""Cada codigo nombra el rango de cuentas de campo que da su carga: mover un umbral cambia esos
rangos, y con ellos los codigos y la version."""


@dataclass(frozen=True)
class EntradaTerritorio:
    """Un territorio tal como lo ve `territorial/v1`: su clave y cuatro agregados, nada mas.

    Cada entrada ya es el agregado completo de un municipio sobre una sola ejecucion de decision
    EXITOSA. No la arma este modulo, que no recibe cuentas sueltas ni sabe agruparlas: la va a
    armar la capa de aplicacion, en la base, por `cve_entidad` y `cve_municipio`:

      - `cuentas_total`: cuantas DecisionCuenta de esa ejecucion caen en el territorio;
      - `saldo_total`: la suma de Cuenta.saldo_total de esas cuentas;
      - `cuentas_campo`: cuantas de ellas tienen canal_recomendado == "CAMPO";
      - `saldo_campo`: la suma de Cuenta.saldo_total de esas cuentas de campo.

    Lo que cuenta como campo es la decision, DecisionCuenta.canal_recomendado, y nunca
    Cuenta.canal: ese es un dato de la cartera, no lo que se decidio hacer con la cuenta.

    Dias de atraso, producto, segmento, prioridad y coordenadas no entran: estas reglas no los
    usan, y asi no pueden influir por descuido ni volver a decidir una cuenta.

    Se valida al construirse, fail-closed: tipos exactos, sin convertir nada (ni un bool por
    entero ni un float por Decimal), y agregados consistentes entre si.
    """

    cve_entidad: str
    """Dos digitos ASCII, como texto: "09", nunca 9 ni "9"."""
    cve_municipio: str
    """Tres digitos ASCII, como texto: "002", nunca 2 ni "2"."""
    cuentas_total: int
    """Al menos 1: un territorio aparece porque tiene al menos una cuenta en la cartera."""
    saldo_total: Decimal
    """En pesos, como sale de la base: Decimal, nunca float. No se redondea ni se cuantiza."""
    cuentas_campo: int
    """De 0 a `cuentas_total`."""
    saldo_campo: Decimal
    """De 0 a `saldo_total`: exactamente 0 si no hay cuentas de campo, y exactamente `saldo_total`
    si todas lo son."""

    def __post_init__(self) -> None:
        _exigir_clave("cve_entidad", self.cve_entidad, digitos=2)
        _exigir_clave("cve_municipio", self.cve_municipio, digitos=3)
        _exigir_conteo("cuentas_total", self.cuentas_total)
        _exigir_conteo("cuentas_campo", self.cuentas_campo)
        _exigir_saldo("saldo_total", self.saldo_total)
        _exigir_saldo("saldo_campo", self.saldo_campo)
        if self.cuentas_total < 1:
            raise ValueError(
                f"cuentas_total debe ser al menos 1, no {self.cuentas_total}: un territorio "
                "aparece porque tiene cuentas."
            )
        if self.cuentas_campo > self.cuentas_total:
            raise ValueError(
                f"cuentas_campo ({self.cuentas_campo}) no puede ser mayor que cuentas_total "
                f"({self.cuentas_total}): las cuentas de campo son parte del total."
            )
        if self.saldo_campo > self.saldo_total:
            raise ValueError(
                f"saldo_campo ({self.saldo_campo}) no puede ser mayor que saldo_total "
                f"({self.saldo_total}): el saldo de campo es parte del total."
            )
        if self.cuentas_campo == 0 and self.saldo_campo != 0:
            raise ValueError(
                f"saldo_campo debe ser 0 si no hay cuentas de campo, no {self.saldo_campo}."
            )
        # Si todas las cuentas son de campo, las dos sumas son de las mismas cuentas. Al reves no:
        # una cuenta que no es de campo puede tener saldo cero.
        if self.cuentas_campo == self.cuentas_total and self.saldo_campo != self.saldo_total:
            raise ValueError(
                f"saldo_campo ({self.saldo_campo}) debe ser igual a saldo_total "
                f"({self.saldo_total}) si todas las cuentas son de campo."
            )

    @property
    def clave_territorio(self) -> str:
        """La entidad y el municipio, uno tras otro y como texto: "09" y "002" dan "09002"."""
        return self.cve_entidad + self.cve_municipio


# Los tipos se exigen exactos, sin subclases y sin convertir: bool es un int para Python, pero
# True no es una cuenta, y "1.00" se parece a un saldo, pero no lo es.


def _exigir_clave(campo: str, valor: object, *, digitos: int) -> None:
    if type(valor) is not str:
        raise TypeError(
            f"{campo} debe ser texto, no {type(valor).__name__}: una clave conserva sus ceros a "
            "la izquierda."
        )
    # isdigit solo no basta: tambien acepta digitos de otros alfabetos y superindices.
    if len(valor) != digitos or not (valor.isascii() and valor.isdigit()):
        raise ValueError(f"{campo} debe tener exactamente {digitos} digitos ASCII: {valor!r}.")


def _exigir_conteo(campo: str, valor: object) -> None:
    if type(valor) is not int:
        raise TypeError(f"{campo} debe ser un entero, no {type(valor).__name__}.")
    if valor < 0:
        raise ValueError(f"{campo} no puede ser negativo: {valor}.")


def _exigir_saldo(campo: str, valor: object) -> None:
    if type(valor) is not Decimal:
        raise TypeError(
            f"{campo} debe ser Decimal, no {type(valor).__name__}: los saldos se comparan en "
            "centavos exactos."
        )
    if not valor.is_finite() or valor < 0:
        raise ValueError(f"{campo} debe ser un numero finito y no negativo: {valor}.")


@dataclass(frozen=True)
class MotivoTerritorial:
    """Por que un territorio tiene su carga: que regla aplico, sobre que campo y con que valor."""

    codigo: CodigoMotivoTerritorial
    campo: str
    """El campo que la regla leyo. En `territorial/v1`, siempre `cuentas_campo`."""
    valor: str
    """En texto canonico: el conteo como entero, sin ceros a la izquierda."""


@dataclass(frozen=True)
class ResultadoTerritorio:
    """La carga de un territorio y su lugar entre los que tienen trabajo de campo.

    Trae los agregados tal como llegaron, sin redondear: con eso se puede guardar sin volver a
    calcular nada, y cada carga y cada lugar se explican con los valores que estan aqui.
    """

    clave_territorio: str
    cve_entidad: str
    cve_municipio: str
    cuentas_total: int
    saldo_total: Decimal
    cuentas_campo: int
    saldo_campo: Decimal
    carga: CargaTerritorial
    posicion_campo: int | None
    """El lugar del territorio entre los que tienen cuentas de campo, desde 1 y sin huecos. None
    si no tiene ninguna: no compite por gestion de campo, y un 0 se leeria como un lugar."""
    motivos: tuple[MotivoTerritorial, ...]
    """En `territorial/v1`, exactamente uno: el que explica la carga. El lugar no lleva motivo:
    lo explica el orden publico de `priorizar_territorios` con los valores de arriba."""


def _carga(cuentas_campo: int) -> CargaTerritorial:
    """La carga de un territorio por sus cuentas de campo. Los umbrales son inclusivos."""
    if cuentas_campo == 0:
        return CargaTerritorial.SIN_CARGA
    if cuentas_campo < UMBRAL_CARGA_MEDIA:
        return CargaTerritorial.BAJA
    if cuentas_campo < UMBRAL_CARGA_ALTA:
        return CargaTerritorial.MEDIA
    return CargaTerritorial.ALTA


def evaluar_territorio(entrada: EntradaTerritorio) -> ResultadoTerritorio:
    """La carga de un territorio con `territorial/v1`, todavia sin posicion.

    La carga sale solo de `cuentas_campo`, porque es una medida de trabajo de campo y su unidad
    observable es la cuenta. El saldo no la sube ni la baja: una cuenta de saldo enorme sigue
    siendo una visita, y lo que pesa su saldo ya lo considero `decision/v1` al mandarla a campo;
    contarlo otra vez aqui seria contarlo dos veces. `cuentas_total` tampoco cambia la carga, que
    no es una proporcion.

    La posicion queda en None: un territorio solo no tiene lugar en un orden. Se lo da
    `priorizar_territorios`, que ve juntos a todos los de una ejecucion.
    """
    # Solo una EntradaTerritorio llega validada; otro objeto con los mismos campos no paso por su
    # validacion.
    if not isinstance(entrada, EntradaTerritorio):
        raise TypeError(f"Se esperaba una EntradaTerritorio, no {type(entrada).__name__}.")
    carga = _carga(entrada.cuentas_campo)
    motivo = MotivoTerritorial(MOTIVO_POR_CARGA[carga], "cuentas_campo", str(entrada.cuentas_campo))
    return ResultadoTerritorio(
        clave_territorio=entrada.clave_territorio,
        cve_entidad=entrada.cve_entidad,
        cve_municipio=entrada.cve_municipio,
        cuentas_total=entrada.cuentas_total,
        saldo_total=entrada.saldo_total,
        cuentas_campo=entrada.cuentas_campo,
        saldo_campo=entrada.saldo_campo,
        carga=carga,
        posicion_campo=None,
        motivos=(motivo,),
    )


def priorizar_territorios(
    entradas: Iterable[EntradaTerritorio],
) -> tuple[ResultadoTerritorio, ...]:
    """Evalua los territorios de una ejecucion y los ordena para gestion de campo.

    Primero van los que tienen cuentas de campo, en el orden de `territorial/v1`:

      1. `cuentas_campo`, de mayor a menor;
      2. a iguales cuentas de campo, `saldo_campo`, de mayor a menor;
      3. a iguales cuentas y saldo de campo, `clave_territorio`, de menor a mayor.

    Es un orden lexicografico y no un puntaje: no mezcla cuentas y pesos en un numero artificial,
    y cada lugar se explica con valores que el resultado ya expone. El saldo solo desempata: nunca
    pone a un territorio con menos cuentas de campo delante de otro con mas. Cada uno recibe su
    `posicion_campo`, desde 1 y sin huecos.

    Despues van los SIN_CARGA, por `clave_territorio` y con la posicion en None.

    El resultado depende de que entradas llegan, no del orden en que llegan. Una clave repetida
    es un ValueError: cada entrada ya es el agregado completo de su municipio, asi que dos del
    mismo no se suman ni se elige una. Sin entradas devuelve una tupla vacia; si eso se puede
    publicar lo decide quien guarde el resultado, no este calculo.
    """
    evaluados: dict[str, ResultadoTerritorio] = {}
    for entrada in entradas:
        resultado = evaluar_territorio(entrada)
        clave = resultado.clave_territorio
        if clave in evaluados:
            raise ValueError(
                f"El territorio {clave} viene mas de una vez. Cada entrada es el agregado completo "
                "de su municipio: no se suman ni se elige una."
            )
        evaluados[clave] = resultado

    por_clave = sorted(evaluados.values(), key=lambda r: r.clave_territorio)
    con_campo = [r for r in por_clave if r.cuentas_campo > 0]
    # El orden es estable tambien de mayor a menor: a iguales cuentas y saldo de campo queda el
    # orden por clave. Asi los saldos solo se comparan. Negarlos para ordenar en una sola pasada
    # los redondearia al contexto decimal vigente, y el orden dependeria de el.
    con_campo.sort(key=lambda r: (r.cuentas_campo, r.saldo_campo), reverse=True)
    sin_carga = [r for r in por_clave if r.cuentas_campo == 0]
    return (
        *(replace(r, posicion_campo=n) for n, r in enumerate(con_campo, start=1)),
        *sin_carga,
    )
