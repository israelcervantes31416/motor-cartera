"""lifecycle/v1 como nucleo puro: el vocabulario de las gestiones de cobranza y sus reglas.

Una gestion es una accion concreta para cobrar o para comunicarse con una cuenta: una llamada, un
mensaje, una visita. Se describe con ejes separados, para no confundir lo que se intento con lo que
se logro:

- canal: por donde (DIGITAL, TELEFONICA, CAMPO u OTRO), el mismo vocabulario de canal que ya usa
  la cartera; `medio` lo precisa sin romperlo (LLAMADA en TELEFONICA; SMS, WHATSAPP o EMAIL en
  DIGITAL).
- nivel de contacto: con quien se hablo. NO_APLICA (un mensaje de una via), SIN_CONTACTO,
  CONTACTO_TERCERO o CONTACTO_TITULAR. Hablar con el titular no es un resultado favorable: no
  implica promesa ni pago.
- resultado: que salio de la gestion. SIN_RESPUESTA, CONTACTO (hubo contacto, sin compromiso ni
  rechazo), RECHAZO, PROMESA, CONVENIO, VISITA_REALIZADA (una visita sin compromiso ni rechazo: su
  detalle dice si se localizo a alguien) u OTRO.

Las combinaciones que no tienen sentido se rechazan aqui, en la API, en la importacion y en la base,
con las mismas reglas: un SIN_RESPUESTA con contacto, una PROMESA sin contacto, una VISITA_REALIZADA
fuera de campo o un medio de otro canal. Una visita es una gestion de CAMPO con su detalle, y cada
gestion de CAMPO lo tiene.

Los eventos del lifecycle son operacionales: los registra Motor Cartera, no los manda el acreedor.
Se agregan y no se corrigen en su lugar: lo registrado por error se anula con otro evento, y una
promesa o un convenio que deja de valer se cancela con otro evento. Una promesa, un convenio o una
visita registrados por error se anulan anulando la gestion de la que nacen.

Este modulo no sabe de la base, y las pruebas lo ejercitan sin ella.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

VERSION_LIFECYCLE = "lifecycle/v1"
"""Cambia si cambia el vocabulario o cualquier regla de coherencia de este modulo."""


class TipoEvento(StrEnum):
    """Que registra un evento del lifecycle. Cada uno, salvo GESTION_REGISTRADA, se refiere a otro
    evento anterior: el que anula, cancela o detalla."""

    GESTION_REGISTRADA = "GESTION_REGISTRADA"
    GESTION_ANULADA = "GESTION_ANULADA"
    PROMESA_CREADA = "PROMESA_CREADA"
    PROMESA_CANCELADA = "PROMESA_CANCELADA"
    CONVENIO_CREADO = "CONVENIO_CREADO"
    CONVENIO_CANCELADO = "CONVENIO_CANCELADO"


RELACIONADO: dict[TipoEvento, TipoEvento] = {
    TipoEvento.GESTION_ANULADA: TipoEvento.GESTION_REGISTRADA,
    TipoEvento.PROMESA_CREADA: TipoEvento.GESTION_REGISTRADA,
    TipoEvento.PROMESA_CANCELADA: TipoEvento.PROMESA_CREADA,
    TipoEvento.CONVENIO_CREADO: TipoEvento.GESTION_REGISTRADA,
    TipoEvento.CONVENIO_CANCELADO: TipoEvento.CONVENIO_CREADO,
}
"""A que tipo de evento se refiere cada tipo. La base admite a lo mas un evento de cada tipo sobre
el mismo evento: una anulacion por gestion, una promesa y un convenio por gestion, y una cancelacion
por promesa o por convenio."""

CIERRES = frozenset(
    {TipoEvento.GESTION_ANULADA, TipoEvento.PROMESA_CANCELADA, TipoEvento.CONVENIO_CANCELADO}
)
"""Los eventos que dejan sin efecto a otro. Llevan su motivo; los demas no."""


class OrigenRegistro(StrEnum):
    """Por donde entro un evento. Ninguno es una fuente oficial del acreedor."""

    API = "API"
    IMPORTACION = "IMPORTACION"


class Canal(StrEnum):
    DIGITAL = "DIGITAL"
    TELEFONICA = "TELEFONICA"
    CAMPO = "CAMPO"
    OTRO = "OTRO"


class Medio(StrEnum):
    """Precisa el canal, sin reemplazarlo."""

    LLAMADA = "LLAMADA"
    SMS = "SMS"
    WHATSAPP = "WHATSAPP"
    EMAIL = "EMAIL"


MEDIOS_POR_CANAL: dict[Canal, frozenset[Medio]] = {
    Canal.TELEFONICA: frozenset({Medio.LLAMADA}),
    Canal.DIGITAL: frozenset({Medio.SMS, Medio.WHATSAPP, Medio.EMAIL}),
    Canal.CAMPO: frozenset(),
    Canal.OTRO: frozenset(),
}
"""Los medios que admite cada canal. El medio siempre es opcional."""


class NivelContacto(StrEnum):
    NO_APLICA = "NO_APLICA"
    """Una gestion en la que el contacto no aplica: un mensaje de una via, sin respuesta posible."""
    SIN_CONTACTO = "SIN_CONTACTO"
    CONTACTO_TERCERO = "CONTACTO_TERCERO"
    CONTACTO_TITULAR = "CONTACTO_TITULAR"


CONTACTOS = frozenset({NivelContacto.CONTACTO_TERCERO, NivelContacto.CONTACTO_TITULAR})
"""Los niveles en que se hablo con alguien."""

TOLERANCIA_DEL_RELOJ = timedelta(minutes=5)
"""Cuanto puede adelantarse el reloj de quien registra: la misma tolerancia que exige la base
(ck_evento_tiempos). La aplican la API y la importacion."""


class ResultadoGestion(StrEnum):
    SIN_RESPUESTA = "SIN_RESPUESTA"
    CONTACTO = "CONTACTO"
    """Hubo contacto, sin compromiso ni rechazo."""
    RECHAZO = "RECHAZO"
    PROMESA = "PROMESA"
    CONVENIO = "CONVENIO"
    VISITA_REALIZADA = "VISITA_REALIZADA"
    """Una visita de campo sin compromiso ni rechazo; su detalle dice si se localizo a alguien."""
    OTRO = "OTRO"


CON_CONTACTO = frozenset(
    {
        ResultadoGestion.CONTACTO,
        ResultadoGestion.RECHAZO,
        ResultadoGestion.PROMESA,
        ResultadoGestion.CONVENIO,
    }
)
"""Los resultados que exigen haber hablado con alguien."""


class ResultadoVisita(StrEnum):
    """Que encontro una visita de campo. No es una regla territorial: no dice nada de zonas ni de
    rutas, y no se valida contra ninguna geografia."""

    NO_LOCALIZADO = "NO_LOCALIZADO"
    SIN_CONTACTO = "SIN_CONTACTO"
    CONTACTO_TERCERO = "CONTACTO_TERCERO"
    CONTACTO_TITULAR = "CONTACTO_TITULAR"
    DOMICILIO_NO_VALIDO = "DOMICILIO_NO_VALIDO"
    OTRO = "OTRO"


NIVEL_DE_LA_VISITA: dict[ResultadoVisita, NivelContacto | None] = {
    ResultadoVisita.NO_LOCALIZADO: NivelContacto.SIN_CONTACTO,
    ResultadoVisita.SIN_CONTACTO: NivelContacto.SIN_CONTACTO,
    ResultadoVisita.DOMICILIO_NO_VALIDO: NivelContacto.SIN_CONTACTO,
    ResultadoVisita.CONTACTO_TERCERO: NivelContacto.CONTACTO_TERCERO,
    ResultadoVisita.CONTACTO_TITULAR: NivelContacto.CONTACTO_TITULAR,
    ResultadoVisita.OTRO: None,
}
"""El nivel de contacto que exige cada resultado de una visita en su gestion; OTRO admite
cualquiera."""


class EstadoGestion(StrEnum):
    """El estado operativo de una gestion, que se deriva de sus eventos: no se guarda."""

    VIGENTE = "VIGENTE"
    ANULADA = "ANULADA"


class EstadoPromesa(StrEnum):
    """El estado operativo de una promesa. No dice si se cumplio: eso lo dice su evaluacion, con su
    propia version y su propia fecha de corte."""

    VIGENTE = "VIGENTE"
    CANCELADA = "CANCELADA"
    """Un evento PROMESA_CANCELADA la dejo sin efecto."""
    ANULADA = "ANULADA"
    """Se anulo la gestion de la que nacio: se registro por error."""


class EstadoConvenio(StrEnum):
    VIGENTE = "VIGENTE"
    CANCELADO = "CANCELADO"
    ANULADO = "ANULADO"


def incoherencias_de_gestion(
    canal: Canal,
    medio: Medio | None,
    nivel: NivelContacto,
    resultado: ResultadoGestion,
    resultado_visita: ResultadoVisita | None,
) -> list[str]:
    """Lo que no tiene sentido en una gestion, una frase por regla rota; vacia si es coherente. Es
    exactamente lo que comprueban las restricciones de la base, mas la relacion con su visita."""
    problemas = []
    if medio is not None and medio not in MEDIOS_POR_CANAL[canal]:
        admitidos = ", ".join(sorted(MEDIOS_POR_CANAL[canal])) or "ninguno"
        problemas.append(f"El medio {medio} no es del canal {canal} (admite: {admitidos}).")
    if resultado == ResultadoGestion.SIN_RESPUESTA and nivel in CONTACTOS:
        problemas.append(f"Un resultado SIN_RESPUESTA no puede tener {nivel}.")
    if resultado in CON_CONTACTO and nivel not in CONTACTOS:
        problemas.append(f"Un resultado {resultado} exige contacto con el titular o un tercero.")
    if resultado == ResultadoGestion.VISITA_REALIZADA and canal != Canal.CAMPO:
        problemas.append("VISITA_REALIZADA solo es de una gestion de CAMPO.")
    if canal == Canal.CAMPO and resultado in (
        ResultadoGestion.SIN_RESPUESTA,
        ResultadoGestion.CONTACTO,
    ):
        problemas.append(
            f"Una gestion de CAMPO no termina en {resultado}: su visita dice a quien encontro, y "
            "sin compromiso ni rechazo su resultado es VISITA_REALIZADA."
        )
    if nivel == NivelContacto.NO_APLICA and canal not in (Canal.DIGITAL, Canal.OTRO):
        problemas.append(f"NO_APLICA es de un mensaje DIGITAL o de OTRO canal, no de {canal}.")
    if (canal == Canal.CAMPO) != (resultado_visita is not None):
        problemas.append(
            "Una gestion de CAMPO trae su visita, y solo una de CAMPO la trae."
            if canal == Canal.CAMPO
            else "Solo una gestion de CAMPO trae una visita."
        )
    if resultado_visita is not None:
        exigido = NIVEL_DE_LA_VISITA[resultado_visita]
        if exigido is not None and nivel != exigido:
            problemas.append(
                f"Una visita {resultado_visita} corresponde a una gestion {exigido}, no {nivel}."
            )
    return problemas


def incoherencias_de_visita(
    inicio: datetime | None, fin: datetime | None, ocurrido_en: datetime
) -> list[str]:
    """Lo que no cuadra entre una visita y su gestion, una frase por regla rota; vacia si cuadra: la
    visita no termina antes de empezar, y la gestion ocurre mientras dura."""
    problemas = []
    if inicio is not None and fin is not None and inicio > fin:
        problemas.append("La visita termina antes de empezar.")
    if inicio is not None and inicio > ocurrido_en:
        problemas.append("La gestion ocurre antes de que empiece su visita.")
    if fin is not None and fin < ocurrido_en:
        problemas.append("La gestion ocurre despues de que termina su visita.")
    return problemas


def incoherencias_de_cuotas(
    total: Decimal,
    inicio: date,
    fin: date | None,
    cuotas: Sequence[tuple[date, Decimal]],
) -> list[str]:
    """Lo que no cuadra en el calendario declarado de un convenio, una frase por regla rota; vacia
    si cuadra. Sin calendario no hay nada que revisar. Con calendario, las cuotas suman exactamente
    el monto total, vencen en fechas estrictamente crecientes y dentro de la vigencia del convenio.
    Es lo mismo que la base comprueba al confirmar (el trigger de cuotas de la 0010)."""
    if not cuotas:
        return []
    problemas = []
    suma = sum((monto for _, monto in cuotas), Decimal("0"))
    if suma != total:
        problemas.append(f"Las cuotas suman {suma:.2f} y el monto total acordado es {total:.2f}.")
    if any(monto <= 0 for _, monto in cuotas):
        problemas.append("Cada cuota tiene un monto positivo.")
    fechas = [fecha for fecha, _ in cuotas]
    if any(a >= b for a, b in zip(fechas, fechas[1:], strict=False)):
        problemas.append("Las cuotas vencen en fechas estrictamente crecientes, en su orden.")
    if fechas[0] < inicio or (fin is not None and fechas[-1] > fin):
        problemas.append("Las cuotas vencen dentro de la vigencia del convenio.")
    return problemas


# --- datos personales -----------------------------------------------------------------------------

_DATOS_PERSONALES = (
    ("un correo electronico", re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")),
    ("un numero de telefono o de tarjeta", re.compile(r"(?:\d[\s.\-()]*){10,}")),
    ("una CURP", re.compile(r"\b[A-Z]{4}\d{6}[HM][A-Z]{5}[A-Z0-9]\d\b", re.IGNORECASE)),
    ("un RFC", re.compile(r"\b[A-Z&Ñ]{3,4}\d{6}[A-Z0-9]{3}\b", re.IGNORECASE)),
)


def datos_personales(texto: str) -> list[str]:
    """Que parece dato personal en un texto libre (una observacion, un motivo): un correo, un
    telefono, una tarjeta, una CURP o un RFC. Es una barrera para no guardar PII sin necesidad en un
    campo libre, no una garantia: un nombre o un domicilio no se reconocen."""
    return [nombre for nombre, patron in _DATOS_PERSONALES if patron.search(texto)]


def datos_personales_en(**textos: str | None) -> list[str]:
    """Una frase por cada dato personal que parece traer cada texto libre, con el nombre de su
    campo; vacia si ninguno parece traer uno."""
    return [
        f"{campo.replace('_', ' ').capitalize()} parece traer {dato}: no se guardan datos "
        "personales en un texto libre."
        for campo, texto in textos.items()
        if texto
        for dato in datos_personales(texto)
    ]


# --- la huella de una peticion --------------------------------------------------------------------


def huella(operacion: TipoEvento, objetivo: str, datos: Mapping[str, object]) -> bytes:
    """El SHA-256 de una peticion operacional, en forma canonica: su tipo, sobre que recurso y sus
    datos, con las llaves ordenadas, los instantes en UTC y los importes con dos decimales. Dos
    peticiones con la misma llave de idempotencia son la misma si y solo si sus huellas son
    iguales."""
    texto = json.dumps(
        {"operacion": operacion.value, "objetivo": objetivo, "datos": dict(datos)},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_canonico,
    )
    return hashlib.sha256(texto.encode("utf-8")).digest()


def _canonico(valor: object) -> str:
    if isinstance(valor, datetime):
        if valor.tzinfo is None:
            raise ValueError("Un instante del lifecycle lleva su zona horaria.")
        return valor.astimezone(UTC).isoformat()
    if isinstance(valor, date):
        return valor.isoformat()
    if isinstance(valor, Decimal):
        return f"{valor.quantize(Decimal('0.01'))}"
    if isinstance(valor, UUID):
        return str(valor)
    raise TypeError(f"No se sabe poner en forma canonica un {type(valor).__name__}.")
