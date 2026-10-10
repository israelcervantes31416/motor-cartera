"""Registrar una accion operacional: una peticion, un evento, en una transaccion.

Lo usa la API para escrituras individuales; la importacion masiva va por su propio camino, por
conjuntos (`lifecycle.importacion`). Cada operacion:

1. calcula la huella de la peticion (su tipo, sobre que recurso y sus datos, en forma canonica);
2. busca su llave de idempotencia en la cartera: con la misma huella, devuelve lo que ya se registro
   y no escribe nada; con otra huella, la llave se esta reutilizando para otra peticion, y es un
   error;
3. bloquea la fila del recurso sobre el que escribe (la gestion, la promesa o el convenio), para
   que dos peticiones sobre el mismo recurso se ordenen en lugar de pisarse, y revisa su estado:
   una gestion anulada ya no recibe una promesa, una promesa cancelada no se cancela otra vez;
4. inserta el evento y su detalle en un SAVEPOINT. Si otra peticion con la misma llave gano la
   carrera, el indice unico de la llave lo rechaza: se revierte el SAVEPOINT y se vuelve al paso 2,
   que ahora encuentra lo que registro la otra;
5. confirma. registrado_en es now() de esta transaccion: el reloj de la base, nunca el del cliente.

Nada se actualiza: una correccion es otro evento. Las comparaciones de un instante con un dia de
calendario (la fecha limite de una promesa) se hacen en la zona horaria de la fuente, en
PostgreSQL, con su base de zonas.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from motor_cartera.db.modelos import (
    ConvenioCobranza,
    CuentaCanonica,
    CuotaConvenio,
    EventoLifecycle,
    GestionCobranza,
    PromesaPago,
    VisitaCampo,
)
from motor_cartera.db.sesion import restriccion, sesion
from motor_cartera.lifecycle.reglas import (
    TOLERANCIA_DEL_RELOJ,
    VERSION_LIFECYCLE,
    Canal,
    Medio,
    NivelContacto,
    OrigenRegistro,
    ResultadoGestion,
    ResultadoVisita,
    TipoEvento,
    datos_personales_en,
    huella,
    incoherencias_de_cuotas,
    incoherencias_de_gestion,
    incoherencias_de_visita,
)

log = logging.getLogger(__name__)

INTENTOS = 3
"""Cuantas veces se vuelve a buscar la llave despues de perder la carrera contra otra peticion con
la misma llave. La segunda vuelta ya la encuentra; mas es un error."""

INDICE_DE_LA_LLAVE = "uq_evento_idempotencia"


# --- lo que se registra ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DatosVisita:
    resultado: ResultadoVisita
    inicio: datetime | None = None
    fin: datetime | None = None
    observacion: str | None = None


@dataclass(frozen=True)
class DatosGestion:
    ocurrido_en: datetime
    canal: Canal
    nivel_contacto: NivelContacto
    resultado: ResultadoGestion
    medio: Medio | None = None
    actor_ref: str | None = None
    observacion: str | None = None
    visita: DatosVisita | None = None


@dataclass(frozen=True)
class DatosPromesa:
    monto_prometido: Decimal
    fecha_limite: date
    ocurrido_en: datetime | None = None
    """Cuando se acordo; por omision, el momento de su gestion."""
    actor_ref: str | None = None


@dataclass(frozen=True)
class DatosCuota:
    fecha_vencimiento: date
    monto: Decimal


@dataclass(frozen=True)
class DatosConvenio:
    monto_total_acordado: Decimal
    fecha_inicio: date
    fecha_fin: date | None = None
    cuotas: tuple[DatosCuota, ...] = ()
    ocurrido_en: datetime | None = None
    actor_ref: str | None = None


@dataclass(frozen=True)
class DatosCierre:
    """Una anulacion o una cancelacion: cuando, por que y quien."""

    ocurrido_en: datetime
    motivo: str
    actor_ref: str | None = None


@dataclass(frozen=True)
class Registro:
    """Lo que dejo una peticion: su evento, el recurso que creo o al que se refiere, y si es nuevo
    o lo registro antes otra peticion con la misma llave y la misma huella."""

    evento_id: UUID
    recurso_id: UUID
    nuevo: bool


# --- lo que puede salir mal -----------------------------------------------------------------------


class LlaveReutilizada(Exception):
    """La llave ya registro otra peticion, con otra huella: no es una repeticion."""

    def __init__(self, evento: EventoLifecycle) -> None:
        super().__init__(
            f"La llave de idempotencia {evento.idempotency_key!r} ya registro otra peticion "
            f"({evento.tipo_evento}, evento {evento.evento_id}); una llave identifica una sola "
            "peticion."
        )
        self.evento_id = evento.evento_id


class RecursoNoEncontrado(Exception):
    def __init__(self, recurso: str, identificador: UUID) -> None:
        super().__init__(f"No existe {recurso} {identificador}.")
        self.recurso = recurso
        self.identificador = identificador


class PeticionInvalida(Exception):
    """La peticion no cumple las reglas de lifecycle/v1. `problemas` dice cuales, una frase por
    regla."""

    def __init__(self, codigo: str, problemas: list[str]) -> None:
        super().__init__(" ".join(problemas))
        self.codigo = codigo
        self.problemas = problemas


class Conflicto(Exception):
    """La peticion es valida, pero choca con el estado del recurso: una gestion anulada, una
    promesa ya registrada o ya cancelada."""

    def __init__(self, codigo: str, mensaje: str) -> None:
        super().__init__(mensaje)
        self.codigo = codigo


# --- las operaciones ------------------------------------------------------------------------------


def registrar_gestion(cuenta_id: UUID, llave: str, datos: DatosGestion) -> Registro:
    """GESTION_REGISTRADA: una gestion de la cuenta, con su visita si es de CAMPO."""
    visita = datos.visita
    problemas = incoherencias_de_gestion(
        datos.canal,
        datos.medio,
        datos.nivel_contacto,
        datos.resultado,
        None if visita is None else visita.resultado,
    )
    if problemas:
        raise PeticionInvalida("GESTION_INCOHERENTE", problemas)
    if visita is not None:
        problemas = incoherencias_de_visita(visita.inicio, visita.fin, datos.ocurrido_en)
        if problemas:
            raise PeticionInvalida("VISITA_INCOHERENTE", problemas)
    _sin_datos_personales(
        observacion=datos.observacion, observacion_de_la_visita=visita and visita.observacion
    )
    firma = huella(TipoEvento.GESTION_REGISTRADA, str(cuenta_id), _datos(datos))
    for _ in range(INTENTOS):
        with sesion() as s:
            cuenta = _cuenta(s, cuenta_id)
            previo = _previo(s, cuenta.despacho_id, cuenta.cartera_id, llave, firma)
            if previo is not None:
                return previo
            _no_en_el_futuro(s, datos.ocurrido_en)
            try:
                with s.begin_nested():
                    evento = _evento(
                        s,
                        cuenta,
                        TipoEvento.GESTION_REGISTRADA,
                        datos.ocurrido_en,
                        llave,
                        firma,
                        actor_ref=datos.actor_ref,
                    )
                    gestion = GestionCobranza(
                        ocurrido_en=datos.ocurrido_en,
                        evento_lifecycle_id=evento.id,
                        cuenta_canonica_id=cuenta.id,
                        canal=datos.canal,
                        medio=datos.medio,
                        nivel_contacto=datos.nivel_contacto,
                        resultado=datos.resultado,
                        actor_ref=datos.actor_ref,
                        observacion=datos.observacion,
                    )
                    s.add(gestion)
                    s.flush()
                    if visita is not None:
                        s.add(
                            VisitaCampo(
                                gestion_cobranza_id=gestion.id,
                                resultado=visita.resultado,
                                inicio=visita.inicio,
                                fin=visita.fin,
                                observacion=visita.observacion,
                            )
                        )
                        s.flush()
            except IntegrityError as exc:
                if restriccion(exc) == INDICE_DE_LA_LLAVE:
                    continue
                raise
            registro = Registro(evento.evento_id, gestion.gestion_id, True)
            s.commit()
            return registro
    raise RuntimeError(f"La llave {llave!r} no se pudo registrar ni encontrar.")


def anular_gestion(gestion_id: UUID, llave: str, datos: DatosCierre) -> Registro:
    """GESTION_ANULADA: la gestion se registro por error. Con ella quedan anuladas su visita, su
    promesa y su convenio; nada se borra."""
    _sin_datos_personales(motivo=datos.motivo)
    firma = huella(TipoEvento.GESTION_ANULADA, str(gestion_id), _datos(datos))
    for _ in range(INTENTOS):
        with sesion() as s:
            gestion = _bloqueada(s, GestionCobranza, GestionCobranza.gestion_id, gestion_id)
            del_registro = s.get_one(EventoLifecycle, gestion.evento_lifecycle_id)
            previo = _previo(s, del_registro.despacho_id, del_registro.cartera_id, llave, firma)
            if previo is not None:
                return previo
            if _cierre(s, del_registro.id, TipoEvento.GESTION_ANULADA) is not None:
                raise Conflicto("GESTION_YA_ANULADA", f"La gestion {gestion_id} ya esta anulada.")
            _no_antes_de(datos.ocurrido_en, del_registro, "La anulacion")
            hecho = _cerrar(s, del_registro, TipoEvento.GESTION_ANULADA, llave, firma, datos)
            if hecho is None:
                continue
            s.commit()
            return hecho
    raise RuntimeError(f"La llave {llave!r} no se pudo registrar ni encontrar.")


def crear_promesa(gestion_id: UUID, llave: str, datos: DatosPromesa, *, zona: str) -> Registro:
    """PROMESA_CREADA: una promesa de pago nacida de una gestion con resultado PROMESA."""
    firma = huella(TipoEvento.PROMESA_CREADA, str(gestion_id), _datos(datos))
    for _ in range(INTENTOS):
        with sesion() as s:
            gestion = _bloqueada(s, GestionCobranza, GestionCobranza.gestion_id, gestion_id)
            de_la_gestion = s.get_one(EventoLifecycle, gestion.evento_lifecycle_id)
            previo = _previo(s, de_la_gestion.despacho_id, de_la_gestion.cartera_id, llave, firma)
            if previo is not None:
                return previo
            _detallable(s, gestion, de_la_gestion, TipoEvento.PROMESA_CREADA)
            ocurrido_en = datos.ocurrido_en or gestion.ocurrido_en
            _no_antes_de(ocurrido_en, de_la_gestion, "La promesa")
            dia = _dia_local(s, ocurrido_en, zona)
            if datos.fecha_limite < dia:
                raise PeticionInvalida(
                    "FECHA_LIMITE_ANTERIOR",
                    [
                        f"La fecha limite {datos.fecha_limite.isoformat()} es anterior al dia en "
                        f"que se acordo la promesa ({dia.isoformat()}, en {zona})."
                    ],
                )
            _no_en_el_futuro(s, ocurrido_en)
            try:
                with s.begin_nested():
                    evento = _evento(
                        s,
                        de_la_gestion,
                        TipoEvento.PROMESA_CREADA,
                        ocurrido_en,
                        llave,
                        firma,
                        actor_ref=datos.actor_ref,
                        relacionado=de_la_gestion.id,
                    )
                    promesa = PromesaPago(
                        evento_lifecycle_id=evento.id,
                        gestion_cobranza_id=gestion.id,
                        cuenta_canonica_id=gestion.cuenta_canonica_id,
                        monto_prometido=datos.monto_prometido,
                        fecha_limite=datos.fecha_limite,
                        version_modelo=VERSION_LIFECYCLE,
                    )
                    s.add(promesa)
                    s.flush()
            except IntegrityError as exc:
                if restriccion(exc) == INDICE_DE_LA_LLAVE:
                    continue
                raise
            registro = Registro(evento.evento_id, promesa.promesa_id, True)
            s.commit()
            return registro
    raise RuntimeError(f"La llave {llave!r} no se pudo registrar ni encontrar.")


def cancelar_promesa(promesa_id: UUID, llave: str, datos: DatosCierre) -> Registro:
    """PROMESA_CANCELADA: la promesa dejo de valer en el negocio (no fue un error de registro: eso
    se corrige anulando su gestion)."""
    _sin_datos_personales(motivo=datos.motivo)
    firma = huella(TipoEvento.PROMESA_CANCELADA, str(promesa_id), _datos(datos))
    for _ in range(INTENTOS):
        with sesion() as s:
            promesa = _bloqueada(s, PromesaPago, PromesaPago.promesa_id, promesa_id)
            creada = s.get_one(EventoLifecycle, promesa.evento_lifecycle_id)
            previo = _previo(s, creada.despacho_id, creada.cartera_id, llave, firma)
            if previo is not None:
                return previo
            gestion = s.get_one(GestionCobranza, promesa.gestion_cobranza_id)
            if _cierre(s, gestion.evento_lifecycle_id, TipoEvento.GESTION_ANULADA) is not None:
                raise Conflicto(
                    "PROMESA_ANULADA",
                    f"La promesa {promesa_id} esta anulada con su gestion: no se cancela.",
                )
            if _cierre(s, creada.id, TipoEvento.PROMESA_CANCELADA) is not None:
                raise Conflicto(
                    "PROMESA_YA_CANCELADA", f"La promesa {promesa_id} ya esta cancelada."
                )
            _no_antes_de(datos.ocurrido_en, creada, "La cancelacion")
            hecho = _cerrar(s, creada, TipoEvento.PROMESA_CANCELADA, llave, firma, datos)
            if hecho is None:
                continue
            s.commit()
            return hecho
    raise RuntimeError(f"La llave {llave!r} no se pudo registrar ni encontrar.")


def crear_convenio(gestion_id: UUID, llave: str, datos: DatosConvenio) -> Registro:
    """CONVENIO_CREADO: un convenio nacido de una gestion con resultado CONVENIO, con las cuotas que
    se declaren, ni una mas: no se inventa una periodicidad."""
    if datos.fecha_fin is not None and datos.fecha_fin < datos.fecha_inicio:
        raise PeticionInvalida(
            "CONVENIO_INCOHERENTE", ["La fecha de fin del convenio es anterior a su inicio."]
        )
    problemas = incoherencias_de_cuotas(
        datos.monto_total_acordado,
        datos.fecha_inicio,
        datos.fecha_fin,
        [(c.fecha_vencimiento, c.monto) for c in datos.cuotas],
    )
    if problemas:
        raise PeticionInvalida("CUOTAS_INCOHERENTES", problemas)
    firma = huella(TipoEvento.CONVENIO_CREADO, str(gestion_id), _datos(datos))
    for _ in range(INTENTOS):
        with sesion() as s:
            gestion = _bloqueada(s, GestionCobranza, GestionCobranza.gestion_id, gestion_id)
            de_la_gestion = s.get_one(EventoLifecycle, gestion.evento_lifecycle_id)
            previo = _previo(s, de_la_gestion.despacho_id, de_la_gestion.cartera_id, llave, firma)
            if previo is not None:
                return previo
            _detallable(s, gestion, de_la_gestion, TipoEvento.CONVENIO_CREADO)
            ocurrido_en = datos.ocurrido_en or gestion.ocurrido_en
            _no_antes_de(ocurrido_en, de_la_gestion, "El convenio")
            _no_en_el_futuro(s, ocurrido_en)
            try:
                with s.begin_nested():
                    evento = _evento(
                        s,
                        de_la_gestion,
                        TipoEvento.CONVENIO_CREADO,
                        ocurrido_en,
                        llave,
                        firma,
                        actor_ref=datos.actor_ref,
                        relacionado=de_la_gestion.id,
                    )
                    convenio = ConvenioCobranza(
                        evento_lifecycle_id=evento.id,
                        gestion_cobranza_id=gestion.id,
                        cuenta_canonica_id=gestion.cuenta_canonica_id,
                        monto_total_acordado=datos.monto_total_acordado,
                        fecha_inicio=datos.fecha_inicio,
                        fecha_fin=datos.fecha_fin,
                        cuotas=len(datos.cuotas),
                        version_modelo=VERSION_LIFECYCLE,
                    )
                    s.add(convenio)
                    s.flush()
                    s.add_all(
                        CuotaConvenio(
                            convenio_cobranza_id=convenio.id,
                            numero=numero,
                            fecha_vencimiento=cuota.fecha_vencimiento,
                            monto=cuota.monto,
                        )
                        for numero, cuota in enumerate(datos.cuotas, start=1)
                    )
                    s.flush()
                    # Lo que la base revisaria al confirmar, ahora: un convenio con sus cuotas.
                    s.execute(
                        text("SET CONSTRAINTS tr_convenio_cuotas, tr_cuota_convenio IMMEDIATE")
                    )
            except IntegrityError as exc:
                if restriccion(exc) == INDICE_DE_LA_LLAVE:
                    continue
                raise
            registro = Registro(evento.evento_id, convenio.convenio_id, True)
            s.commit()
            return registro
    raise RuntimeError(f"La llave {llave!r} no se pudo registrar ni encontrar.")


def cancelar_convenio(convenio_id: UUID, llave: str, datos: DatosCierre) -> Registro:
    """CONVENIO_CANCELADO: el convenio dejo de valer en el negocio."""
    _sin_datos_personales(motivo=datos.motivo)
    firma = huella(TipoEvento.CONVENIO_CANCELADO, str(convenio_id), _datos(datos))
    for _ in range(INTENTOS):
        with sesion() as s:
            convenio = _bloqueada(s, ConvenioCobranza, ConvenioCobranza.convenio_id, convenio_id)
            creado = s.get_one(EventoLifecycle, convenio.evento_lifecycle_id)
            previo = _previo(s, creado.despacho_id, creado.cartera_id, llave, firma)
            if previo is not None:
                return previo
            gestion = s.get_one(GestionCobranza, convenio.gestion_cobranza_id)
            if _cierre(s, gestion.evento_lifecycle_id, TipoEvento.GESTION_ANULADA) is not None:
                raise Conflicto(
                    "CONVENIO_ANULADO",
                    f"El convenio {convenio_id} esta anulado con su gestion: no se cancela.",
                )
            if _cierre(s, creado.id, TipoEvento.CONVENIO_CANCELADO) is not None:
                raise Conflicto(
                    "CONVENIO_YA_CANCELADO", f"El convenio {convenio_id} ya esta cancelado."
                )
            _no_antes_de(datos.ocurrido_en, creado, "La cancelacion")
            hecho = _cerrar(s, creado, TipoEvento.CONVENIO_CANCELADO, llave, firma, datos)
            if hecho is None:
                continue
            s.commit()
            return hecho
    raise RuntimeError(f"La llave {llave!r} no se pudo registrar ni encontrar.")


# --- lo comun -------------------------------------------------------------------------------------


def _datos(datos: object) -> dict:
    """Los datos de una peticion, para su huella: sus campos, con sus tipos, sin perder ninguno."""
    return asdict(datos)  # type: ignore[call-overload]


def _cuenta(s: Session, cuenta_id: UUID) -> CuentaCanonica:
    cuenta = s.exec(select(CuentaCanonica).where(CuentaCanonica.cuenta_id == cuenta_id)).first()
    if cuenta is None:
        raise RecursoNoEncontrado("una cuenta canonica con cuenta_id", cuenta_id)
    return cuenta


def _bloqueada(s: Session, modelo, columna, identificador: UUID):
    """La fila del recurso, bloqueada hasta el commit: dos peticiones sobre el mismo recurso se
    ordenan. Un SELECT ... FOR UPDATE no modifica la fila, asi que el lifecycle sigue siendo solo de
    agregar."""
    fila = s.exec(select(modelo).where(columna == identificador).with_for_update()).first()
    if fila is None:
        nombres = {
            GestionCobranza: "una gestion con gestion_id",
            PromesaPago: "una promesa con promesa_id",
            ConvenioCobranza: "un convenio con convenio_id",
        }
        raise RecursoNoEncontrado(nombres[modelo], identificador)
    return fila


def _previo(
    s: Session, despacho_id: str, cartera_id: str, llave: str, firma: bytes
) -> Registro | None:
    """Lo que ya registro la llave en la cartera, si la misma peticion la uso antes; None si la
    llave es nueva. Con otra huella, LlaveReutilizada."""
    evento = s.exec(
        select(EventoLifecycle).where(
            EventoLifecycle.despacho_id == despacho_id,
            EventoLifecycle.cartera_id == cartera_id,
            EventoLifecycle.idempotency_key == llave,
        )
    ).first()
    if evento is None:
        return None
    if bytes(evento.payload_hash) != firma:
        raise LlaveReutilizada(evento)
    return Registro(evento.evento_id, recurso_de(s, evento), False)


def recurso_de(s: Session, evento: EventoLifecycle) -> UUID:
    """El identificador publico de lo que registro un evento: su gestion, su promesa o su convenio;
    un cierre se identifica con su propio evento."""
    detalles = {
        TipoEvento.GESTION_REGISTRADA: (GestionCobranza, GestionCobranza.gestion_id),
        TipoEvento.PROMESA_CREADA: (PromesaPago, PromesaPago.promesa_id),
        TipoEvento.CONVENIO_CREADO: (ConvenioCobranza, ConvenioCobranza.convenio_id),
    }
    if evento.tipo_evento not in detalles:
        return evento.evento_id
    modelo, columna = detalles[evento.tipo_evento]
    return s.exec(select(columna).where(modelo.evento_lifecycle_id == evento.id)).one()


def _evento(
    s: Session,
    duenio: CuentaCanonica | EventoLifecycle,
    tipo: TipoEvento,
    ocurrido_en: datetime,
    llave: str,
    firma: bytes,
    *,
    actor_ref: str | None,
    relacionado: int | None = None,
    motivo: str | None = None,
) -> EventoLifecycle:
    """El evento, sobre la cuenta (o sobre la del evento al que se refiere), con registrado_en del
    reloj de la base."""
    cuenta_id = duenio.id if isinstance(duenio, CuentaCanonica) else duenio.cuenta_canonica_id
    evento = EventoLifecycle(
        ocurrido_en=ocurrido_en,
        registrado_en=func.now(),
        evento_relacionado_id=relacionado,
        cuenta_canonica_id=cuenta_id,
        tipo_evento=tipo,
        origen_registro=OrigenRegistro.API,
        despacho_id=duenio.despacho_id,
        cartera_id=duenio.cartera_id,
        version_evento=VERSION_LIFECYCLE,
        idempotency_key=llave,
        actor_ref=actor_ref,
        motivo=motivo,
        payload_hash=firma,
    )
    s.add(evento)
    s.flush()
    return evento


def _cerrar(
    s: Session,
    sobre: EventoLifecycle,
    tipo: TipoEvento,
    llave: str,
    firma: bytes,
    datos: DatosCierre,
) -> Registro | None:
    """El evento de cierre sobre `sobre`, en un SAVEPOINT. None si perdio la carrera de la llave."""
    _no_en_el_futuro(s, datos.ocurrido_en)
    try:
        with s.begin_nested():
            evento = _evento(
                s,
                sobre,
                tipo,
                datos.ocurrido_en,
                llave,
                firma,
                actor_ref=datos.actor_ref,
                relacionado=sobre.id,
                motivo=datos.motivo,
            )
    except IntegrityError as exc:
        if restriccion(exc) == INDICE_DE_LA_LLAVE:
            return None
        raise
    return Registro(evento.evento_id, evento.evento_id, True)


def _cierre(s: Session, evento_id: int, tipo: TipoEvento) -> EventoLifecycle | None:
    """El evento de ese tipo que se refiere a `evento_id`, si hay uno: por el indice unico de los
    eventos relacionados, a lo mas hay uno."""
    return s.exec(
        select(EventoLifecycle).where(
            EventoLifecycle.evento_relacionado_id == evento_id,
            EventoLifecycle.tipo_evento == tipo,
        )
    ).first()


DETALLES = {
    TipoEvento.PROMESA_CREADA: (
        ResultadoGestion.PROMESA,
        "GESTION_SIN_PROMESA",
        "PROMESA_YA_REGISTRADA",
        "su promesa",
    ),
    TipoEvento.CONVENIO_CREADO: (
        ResultadoGestion.CONVENIO,
        "GESTION_SIN_CONVENIO",
        "CONVENIO_YA_REGISTRADO",
        "su convenio",
    ),
}
"""Lo que nace de una gestion: el resultado que exige, y los codigos de sus dos conflictos."""


def _detallable(
    s: Session, gestion: GestionCobranza, evento: EventoLifecycle, detalle: TipoEvento
) -> None:
    """Una gestion recibe su promesa o su convenio solo si sigue vigente, si termino en ese
    resultado y si todavia no lo tiene."""
    resultado, sin_resultado, ya_registrado, que = DETALLES[detalle]
    if _cierre(s, evento.id, TipoEvento.GESTION_ANULADA) is not None:
        raise Conflicto("GESTION_ANULADA", f"La gestion {gestion.gestion_id} esta anulada.")
    if gestion.resultado != resultado:
        raise Conflicto(
            sin_resultado,
            f"La gestion {gestion.gestion_id} termino en {gestion.resultado}, no en {resultado}: "
            f"no tiene {que}.",
        )
    if _cierre(s, evento.id, detalle) is not None:
        raise Conflicto(ya_registrado, f"La gestion {gestion.gestion_id} ya tiene {que}.")


def _no_antes_de(ocurrido_en: datetime, evento: EventoLifecycle, que: str) -> None:
    """Un evento que se refiere a otro no puede haber ocurrido antes que el."""
    if ocurrido_en < evento.ocurrido_en:
        raise PeticionInvalida(
            "OCURRIDO_ANTES_DEL_EVENTO",
            [
                f"{que} ocurre el {ocurrido_en.isoformat()}, antes de lo que modifica "
                f"({evento.tipo_evento}, ocurrido el {evento.ocurrido_en.isoformat()})."
            ],
        )


def _no_en_el_futuro(s: Session, ocurrido_en: datetime) -> None:
    """Nada ocurre despues de registrarse: el reloj de la base, con la tolerancia de la base."""
    ahora = s.exec(select(func.now())).one()
    if ocurrido_en > ahora + TOLERANCIA_DEL_RELOJ:
        raise PeticionInvalida(
            "OCURRIDO_EN_FUTURO",
            [
                f"ocurrido_en ({ocurrido_en.isoformat()}) es posterior a este momento: un evento "
                "se registra cuando ya ocurrio."
            ],
        )


def _sin_datos_personales(**textos: str | None) -> None:
    problemas = datos_personales_en(**textos)
    if problemas:
        raise PeticionInvalida("DATOS_PERSONALES", problemas)


def _dia_local(s: Session, instante: datetime, zona: str) -> date:
    """El dia de calendario de un instante en la zona de la fuente, con la base de zonas de
    PostgreSQL."""
    return s.execute(
        text("SELECT (CAST(:instante AS timestamptz) AT TIME ZONE :zona)::date"),
        {"instante": instante, "zona": zona},
    ).scalar_one()
