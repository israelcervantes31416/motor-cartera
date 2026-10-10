"""La importacion de eventos operacionales: un archivo JSONL sintetico, por conjuntos y todo o nada.

`motor-cartera cargar-lifecycle` registra de una vez lo que la API registra de uno en uno: para
pruebas, integraciones y el benchmark. No es una fuente oficial del acreedor, como no lo es la API:
es la importacion de eventos operacionales, y cada evento queda con origen IMPORTACION.

Cada linea es un objeto JSON con su `tipo` (GESTION_REGISTRADA, GESTION_ANULADA, PROMESA_CREADA,
PROMESA_CANCELADA, CONVENIO_CREADO o CONVENIO_CANCELADO) y su `idempotency_key`. Una gestion dice
su cuenta por `cliente_unico`; lo demas se refiere a lo que detalla, cancela o anula por la llave
con que se registro (`gestion`, `promesa` o `convenio`), en el mismo archivo o antes. El orden:

1. Se lee por lotes. Cada linea se valida en Python con las reglas de la API (el contrato de su
   tipo, la coherencia de la gestion y de su visita, el calendario de un convenio y los datos
   personales en los textos libres), se le calcula su huella y se copia con COPY a una tabla
   temporal. Nada se inserta fila por fila.
2. En PostgreSQL, por conjuntos: una llave repetida en el archivo con el mismo contenido cuenta una
   vez, y con otro es un error; una llave ya registrada con la misma huella no se registra otra vez
   (el mismo archivo dos veces no duplica nada), y con otra es un error.
3. Se registra por fases, en el orden en que los eventos dependen unos de otros: las gestiones (con
   sus visitas), las promesas y los convenios (con sus cuotas), sus cancelaciones y las
   anulaciones. Cada fase resuelve sus referencias contra lo ya registrado, incluido lo de las fases
   anteriores, y revisa lo mismo que la API: una gestion anulada no recibe una promesa, una promesa
   no se cancela dos veces, nada ocurre antes de lo que modifica ni despues de ahora.
4. Todo o nada: con un solo problema no se registra nada, y el reporte dice cada linea y por que.

Las anulaciones se aplican al final: una promesa y la anulacion de su gestion en el mismo archivo
dejan la promesa anulada con su gestion, el mismo estado al que llega la API. La huella de una linea
se calcula sobre su referencia (el cliente o la llave a la que se refiere), no sobre identificadores
publicos que todavia no existen: una llave usada al importar es de la importacion.
"""

from __future__ import annotations

import gzip
import io
import json
import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from motor_cartera.db.copia import copiar
from motor_cartera.db.modelos import PATRON_ACTOR, PATRON_LLAVE
from motor_cartera.db.sesion import restriccion, sesion
from motor_cartera.lifecycle.reglas import (
    TOLERANCIA_DEL_RELOJ,
    VERSION_LIFECYCLE,
    Canal,
    Medio,
    NivelContacto,
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

LOTE = 20_000
"""Cuantas lineas se validan y se copian juntas: la memoria de Python no crece con el archivo."""

PROBLEMAS_EN_EL_REPORTE = 50
"""Cuantos problemas se describen; los demas solo se cuentan."""


# --- el contrato de cada linea --------------------------------------------------------------------

Llave = Annotated[str, Field(pattern=PATRON_LLAVE)]
Texto = Annotated[str, Field(min_length=1, max_length=500)]
Importe = Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=2)]


class _Linea(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    idempotency_key: Llave
    actor_ref: str | None = Field(default=None, pattern=PATRON_ACTOR)


class VisitaImportada(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resultado: ResultadoVisita
    inicio: AwareDatetime | None = None
    fin: AwareDatetime | None = None
    observacion: Texto | None = None


class GestionImportada(_Linea):
    tipo: Literal["GESTION_REGISTRADA"]
    cliente_unico: str = Field(pattern=r"^[A-Z0-9]{8,20}$")
    ocurrido_en: AwareDatetime
    canal: Canal
    medio: Medio | None = None
    nivel_contacto: NivelContacto
    resultado: ResultadoGestion
    observacion: Texto | None = None
    visita: VisitaImportada | None = None


class PromesaImportada(_Linea):
    tipo: Literal["PROMESA_CREADA"]
    gestion: Llave
    monto_prometido: Importe
    fecha_limite: date
    ocurrido_en: AwareDatetime | None = None


class CuotaImportada(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fecha_vencimiento: date
    monto: Importe


class ConvenioImportado(_Linea):
    tipo: Literal["CONVENIO_CREADO"]
    gestion: Llave
    monto_total_acordado: Importe
    fecha_inicio: date
    fecha_fin: date | None = None
    cuotas: list[CuotaImportada] = Field(default_factory=list, max_length=360)
    ocurrido_en: AwareDatetime | None = None


class AnulacionImportada(_Linea):
    tipo: Literal["GESTION_ANULADA"]
    gestion: Llave
    ocurrido_en: AwareDatetime
    motivo: Texto


class CancelacionDePromesa(_Linea):
    tipo: Literal["PROMESA_CANCELADA"]
    promesa: Llave
    ocurrido_en: AwareDatetime
    motivo: Texto


class CancelacionDeConvenio(_Linea):
    tipo: Literal["CONVENIO_CANCELADO"]
    convenio: Llave
    ocurrido_en: AwareDatetime
    motivo: Texto


Linea = Annotated[
    GestionImportada
    | PromesaImportada
    | ConvenioImportado
    | AnulacionImportada
    | CancelacionDePromesa
    | CancelacionDeConvenio,
    Field(discriminator="tipo"),
]
LECTOR = TypeAdapter(Linea)

REFERENCIA = {
    "GESTION_REGISTRADA": "cliente_unico",
    "PROMESA_CREADA": "gestion",
    "CONVENIO_CREADO": "gestion",
    "GESTION_ANULADA": "gestion",
    "PROMESA_CANCELADA": "promesa",
    "CONVENIO_CANCELADO": "convenio",
}
"""El campo con que cada tipo dice sobre que es: su cuenta, o la llave de lo que modifica."""


# --- el reporte -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Problema:
    linea: int
    llave: str | None
    codigo: str
    detalle: str


@dataclass
class Importacion:
    """Lo que hizo una importacion. Con un solo problema no registro nada."""

    archivo: str
    dry_run: bool
    lineas: int = 0
    repetidas: int = 0
    """Lineas con la llave y el contenido de otra del archivo: la misma peticion, que cuenta una
    vez."""
    ya_registradas: int = 0
    """Lineas cuya llave ya registro antes exactamente la misma peticion: no se registran otra
    vez."""
    registrados: dict[str, int] = field(default_factory=dict)
    """Los eventos nuevos por tipo: los que registro, o con --dry-run los que registraria."""
    problemas: list[Problema] = field(default_factory=list)
    """Los primeros, en orden de linea."""
    total_de_problemas: int = 0
    segundos: float = 0.0

    @property
    def nuevos(self) -> int:
        return sum(self.registrados.values())

    @property
    def confirmada(self) -> bool:
        return not self.total_de_problemas and not self.dry_run


class ArchivoIlegible(Exception):
    """El archivo no existe o no se puede leer como texto UTF-8."""


class ImportacionConcurrente(Exception):
    """Mientras se importaba, otra peticion registro una de las mismas llaves, o cerro lo mismo: no
    se registro nada, y volver a importar es seguro, porque la importacion es idempotente."""


INDICES_DE_LA_CONCURRENCIA = frozenset({"uq_evento_idempotencia", "ux_evento_relacionado"})


# --- la importacion -------------------------------------------------------------------------------


def importar(
    ruta: str | Path,
    *,
    despacho_id: str,
    cartera_id: str,
    zona: str,
    dry_run: bool = False,
    lote: int = LOTE,
) -> Importacion:
    """Importa el archivo en una transaccion: la confirma si no hubo ningun problema y no es
    `dry_run`; si no, la revierte. Devuelve lo que hizo, o lo que haria."""
    ruta = Path(ruta)
    if not ruta.is_file():
        raise ArchivoIlegible(f"No existe el archivo {ruta}.")
    reporte = Importacion(archivo=ruta.name, dry_run=dry_run)
    inicio = time.monotonic()
    problemas: list[Problema] = []
    with sesion() as s:
        for sentencia in SQL_STAGING:
            s.execute(text(sentencia))
        for filas, cuotas in _lotes(ruta, reporte, problemas, lote):
            copiar(s, "imp_evento", COLUMNAS, filas)
            copiar(s, "imp_cuota", COLUMNAS_CUOTA, cuotas)
        parametros = {
            "despacho": despacho_id,
            "cartera": cartera_id,
            "zona": zona,
            "version": VERSION_LIFECYCLE,
            "tolerancia": TOLERANCIA_DEL_RELOJ,
        }
        try:
            _registrar(s, reporte, parametros)
        except IntegrityError as exc:
            if restriccion(exc) not in INDICES_DE_LA_CONCURRENCIA:
                raise
            s.rollback()
            raise ImportacionConcurrente(
                f"Otra peticion registro al mismo tiempo una llave o un cierre de {ruta.name} "
                f"({restriccion(exc)}): no se registro nada. Vuelve a importarlo: lo que ya este "
                "registrado no se duplica."
            ) from exc
        en_la_base = s.execute(
            text(
                "SELECT linea, llave, codigo, detalle FROM imp_problema "
                "ORDER BY linea, codigo LIMIT :limite"
            ),
            {"limite": PROBLEMAS_EN_EL_REPORTE},
        ).all()
        reporte.total_de_problemas = (
            reporte.total_de_problemas
            + s.execute(text("SELECT count(*) FROM imp_problema")).scalar_one()
        )
        problemas.extend(Problema(*fila) for fila in en_la_base)
        reporte.problemas = sorted(problemas, key=lambda p: (p.linea, p.codigo))[
            :PROBLEMAS_EN_EL_REPORTE
        ]
        if reporte.confirmada:
            s.commit()
        else:
            s.rollback()
    reporte.segundos = round(time.monotonic() - inicio, 3)
    log.info(
        "importacion de %s: %s lineas, %s nuevos, %s ya registradas, %s problemas%s",
        ruta.name,
        reporte.lineas,
        reporte.nuevos,
        reporte.ya_registradas,
        reporte.total_de_problemas,
        " (dry-run)" if dry_run else "",
    )
    return reporte


# --- 1. leer y validar ----------------------------------------------------------------------------

COLUMNAS = (
    "linea",
    "tipo",
    "llave",
    "huella",
    "cliente_unico",
    "referencia",
    "ocurrido_en",
    "actor_ref",
    "motivo",
    "canal",
    "medio",
    "nivel_contacto",
    "resultado",
    "observacion",
    "visita_resultado",
    "visita_inicio",
    "visita_fin",
    "visita_observacion",
    "monto",
    "fecha_limite",
    "fecha_inicio",
    "fecha_fin",
    "cuotas",
)
COLUMNAS_CUOTA = ("linea", "numero", "fecha_vencimiento", "monto")


def _abrir(ruta: Path) -> io.TextIOBase:
    """UTF-8, con o sin la marca de orden de bytes que agregan algunos editores."""
    if ruta.suffix == ".gz":
        return gzip.open(ruta, "rt", encoding="utf-8-sig")
    return ruta.open(encoding="utf-8-sig")


def _lotes(
    ruta: Path, reporte: Importacion, problemas: list[Problema], lote: int
) -> Iterator[tuple[list[tuple], list[tuple]]]:
    """Las filas validas del archivo, por lotes, para COPY. Lo que no pasa va a `problemas` (los
    primeros) y se cuenta en el reporte."""
    filas: list[tuple] = []
    cuotas: list[tuple] = []
    try:
        with _abrir(ruta) as archivo:
            for numero, texto in enumerate(archivo, start=1):
                if not texto.strip():
                    continue
                reporte.lineas += 1
                try:
                    linea = LECTOR.validate_json(texto)
                    _revisar(linea)
                except ValidationError as exc:
                    _anotar(reporte, problemas, numero, _llave(texto), "ENTRADA_INVALIDA", exc)
                    continue
                except _Incoherente as exc:
                    _anotar(reporte, problemas, numero, linea.idempotency_key, exc.codigo, exc)
                    continue
                filas.append(_fila(numero, linea))
                if isinstance(linea, ConvenioImportado):
                    cuotas.extend(
                        (numero, n, c.fecha_vencimiento, c.monto)
                        for n, c in enumerate(linea.cuotas, start=1)
                    )
                if len(filas) >= lote:
                    yield filas, cuotas
                    filas, cuotas = [], []
    except (OSError, UnicodeDecodeError, EOFError) as exc:
        raise ArchivoIlegible(f"No se pudo leer {ruta.name}: {exc}") from exc
    if filas or cuotas:
        yield filas, cuotas


class _Incoherente(Exception):
    def __init__(self, codigo: str, problemas: list[str]) -> None:
        super().__init__(" ".join(problemas))
        self.codigo = codigo


def _revisar(linea) -> None:
    """Las reglas de lifecycle/v1 que no dependen de la base, las mismas que aplica la API."""
    if isinstance(linea, GestionImportada):
        visita = linea.visita
        problemas = incoherencias_de_gestion(
            linea.canal,
            linea.medio,
            linea.nivel_contacto,
            linea.resultado,
            None if visita is None else visita.resultado,
        )
        if problemas:
            raise _Incoherente("GESTION_INCOHERENTE", problemas)
        if visita is not None:
            problemas = incoherencias_de_visita(visita.inicio, visita.fin, linea.ocurrido_en)
            if problemas:
                raise _Incoherente("VISITA_INCOHERENTE", problemas)
        _sin_datos_personales(
            observacion=linea.observacion,
            observacion_de_la_visita=visita and visita.observacion,
        )
    elif isinstance(linea, ConvenioImportado):
        if linea.fecha_fin is not None and linea.fecha_fin < linea.fecha_inicio:
            raise _Incoherente(
                "CONVENIO_INCOHERENTE", ["La fecha de fin del convenio es anterior a su inicio."]
            )
        problemas = incoherencias_de_cuotas(
            linea.monto_total_acordado,
            linea.fecha_inicio,
            linea.fecha_fin,
            [(c.fecha_vencimiento, c.monto) for c in linea.cuotas],
        )
        if problemas:
            raise _Incoherente("CUOTAS_INCOHERENTES", problemas)
    elif isinstance(linea, AnulacionImportada | CancelacionDePromesa | CancelacionDeConvenio):
        _sin_datos_personales(motivo=linea.motivo)


def _sin_datos_personales(**textos: str | None) -> None:
    problemas = datos_personales_en(**textos)
    if problemas:
        raise _Incoherente("DATOS_PERSONALES", problemas)


def _fila(numero: int, linea) -> tuple:
    """La fila de la linea en la tabla temporal, con su huella."""
    tipo = linea.tipo
    campo = REFERENCIA[tipo]
    referencia = getattr(linea, campo)
    datos = linea.model_dump(exclude={"tipo", "idempotency_key", campo})
    firma = huella(TipoEvento(tipo), referencia, datos).hex()
    g = linea if isinstance(linea, GestionImportada) else None
    visita = g.visita if g is not None else None
    monto = getattr(linea, "monto_prometido", None) or getattr(linea, "monto_total_acordado", None)
    return (
        numero,
        tipo,
        linea.idempotency_key,
        firma,
        g.cliente_unico if g is not None else None,
        None if g is not None else referencia,
        getattr(linea, "ocurrido_en", None),
        linea.actor_ref,
        getattr(linea, "motivo", None),
        g.canal.value if g is not None else None,
        g.medio.value if g is not None and g.medio is not None else None,
        g.nivel_contacto.value if g is not None else None,
        g.resultado.value if g is not None else None,
        g.observacion if g is not None else None,
        visita.resultado.value if visita is not None else None,
        visita.inicio if visita is not None else None,
        visita.fin if visita is not None else None,
        visita.observacion if visita is not None else None,
        monto,
        getattr(linea, "fecha_limite", None),
        getattr(linea, "fecha_inicio", None),
        getattr(linea, "fecha_fin", None),
        len(linea.cuotas) if isinstance(linea, ConvenioImportado) else None,
    )


def _llave(texto: str) -> str | None:
    """La llave de una linea que no paso, si se alcanza a leer: para que el reporte la nombre."""
    try:
        valor = json.loads(texto).get("idempotency_key")
    except (ValueError, AttributeError):
        return None
    return valor if isinstance(valor, str) else None


def _anotar(
    reporte: Importacion,
    problemas: list[Problema],
    numero: int,
    llave: str | None,
    codigo: str,
    exc: Exception,
) -> None:
    reporte.total_de_problemas += 1
    if len(problemas) >= PROBLEMAS_EN_EL_REPORTE:
        return
    if isinstance(exc, ValidationError):
        detalle = "; ".join(
            f"{'.'.join(str(parte) for parte in error['loc']) or 'la linea'}: {error['msg']}"
            for error in exc.errors(include_url=False)[:3]
        )
    else:
        detalle = str(exc)
    problemas.append(Problema(numero, llave, codigo, detalle))


# --- 2 y 3. resolver y registrar por conjuntos ----------------------------------------------------

SQL_STAGING = (
    "CREATE TEMP TABLE imp_evento (linea bigint NOT NULL, tipo text NOT NULL, llave text NOT NULL, "
    "huella text NOT NULL, cliente_unico text, referencia text, ocurrido_en timestamptz, "
    "actor_ref text, motivo text, canal text, medio text, nivel_contacto text, resultado text, "
    "observacion text, visita_resultado text, visita_inicio timestamptz, visita_fin timestamptz, "
    "visita_observacion text, monto numeric(14, 2), fecha_limite date, fecha_inicio date, "
    "fecha_fin date, cuotas integer) ON COMMIT DROP",
    "CREATE TEMP TABLE imp_cuota (linea bigint NOT NULL, numero integer NOT NULL, "
    "fecha_vencimiento date NOT NULL, monto numeric(14, 2) NOT NULL) ON COMMIT DROP",
    "CREATE TEMP TABLE imp_problema (linea bigint NOT NULL, llave text, codigo text NOT NULL, "
    "detalle text NOT NULL) ON COMMIT DROP",
    "CREATE INDEX ON imp_problema (linea)",
    "CREATE TEMP TABLE imp_registrado (llave text PRIMARY KEY, evento bigint NOT NULL, "
    "cuenta bigint NOT NULL, ocurrido_en timestamptz NOT NULL) ON COMMIT DROP",
    "CREATE INDEX ON imp_registrado (evento)",
)
"""Las tablas temporales de una importacion: se borran al terminar su transaccion."""

SQL_LLAVES = (
    "CREATE INDEX ON imp_cuota (linea)",
    "ANALYZE imp_evento",
    # Una linea por llave: la primera. Las demas con el mismo contenido son la misma peticion.
    "CREATE TEMP TABLE imp_linea ON COMMIT DROP AS "
    "SELECT DISTINCT ON (llave) * FROM imp_evento ORDER BY llave, linea",
    "CREATE UNIQUE INDEX ON imp_linea (llave)",
    "INSERT INTO imp_problema SELECT e.linea, e.llave, 'LLAVE_REUTILIZADA', "
    "'La llave ya se uso en la linea ' || l.linea || ' para otra peticion.' "
    "FROM imp_evento e JOIN imp_linea l ON l.llave = e.llave "
    "WHERE e.linea <> l.linea AND e.huella <> l.huella",
    # Las llaves que ya se registraron en la cartera, por el indice unico de las llaves.
    "CREATE TEMP TABLE imp_previo ON COMMIT DROP AS "
    "SELECT l.linea, l.llave, encode(e.payload_hash, 'hex') = l.huella AS misma, e.tipo_evento "
    "FROM imp_linea l JOIN evento_lifecycle e ON e.despacho_id = :despacho "
    "AND e.cartera_id = :cartera AND e.idempotency_key = l.llave",
    "INSERT INTO imp_problema SELECT linea, llave, 'LLAVE_REUTILIZADA', "
    "'La llave ya registro otra peticion (' || tipo_evento || ').' FROM imp_previo WHERE NOT misma",
    # Lo que queda por registrar.
    "CREATE TEMP TABLE imp_nueva ON COMMIT DROP AS SELECT l.* FROM imp_linea l "
    "WHERE NOT EXISTS (SELECT 1 FROM imp_previo p WHERE p.linea = l.linea)",
    "CREATE UNIQUE INDEX ON imp_nueva (linea)",
    "CREATE INDEX ON imp_nueva (llave)",
    "ANALYZE imp_nueva",
    "INSERT INTO imp_problema SELECT linea, llave, 'OCURRIDO_EN_FUTURO', 'ocurrido_en (' || "
    "ocurrido_en || ') es posterior a este momento: un evento se registra cuando ya ocurrio.' "
    "FROM imp_nueva WHERE ocurrido_en > now() + :tolerancia",
)

_SIN_PROBLEMA = "NOT EXISTS (SELECT 1 FROM imp_problema p WHERE p.linea = {linea})"

_EVENTOS = (
    "WITH registrados AS (INSERT INTO evento_lifecycle (ocurrido_en, registrado_en, "
    "evento_relacionado_id, evento_id, cuenta_canonica_id, tipo_evento, origen_registro, "
    "despacho_id, cartera_id, version_evento, idempotency_key, actor_ref, motivo, payload_hash) "
    "SELECT {ocurrido_en}, now(), {relacionado}, gen_random_uuid(), {cuenta}, n.tipo, "
    "'IMPORTACION', :despacho, :cartera, :version, n.llave, n.actor_ref, n.motivo, "
    "decode(n.huella, 'hex') {origen} ORDER BY n.linea "
    "RETURNING id, idempotency_key, cuenta_canonica_id, ocurrido_en) "
    "INSERT INTO imp_registrado SELECT idempotency_key, id, cuenta_canonica_id, ocurrido_en "
    "FROM registrados"
)
"""Los eventos de una fase, en orden de linea, con registrado_en del reloj de la base."""

SQL_GESTIONES = (
    "INSERT INTO imp_problema SELECT n.linea, n.llave, 'CUENTA_NO_ENCONTRADA', "
    "'No hay una cuenta canonica con cliente_unico ' || n.cliente_unico || ' en la cartera.' "
    "FROM imp_nueva n WHERE n.tipo = 'GESTION_REGISTRADA' AND NOT EXISTS (SELECT 1 FROM "
    "cuenta_canonica c WHERE c.despacho_id = :despacho AND c.cartera_id = :cartera "
    "AND c.cliente_unico = n.cliente_unico)",
    _EVENTOS.format(
        ocurrido_en="n.ocurrido_en",
        relacionado="NULL::bigint",
        cuenta="c.id",
        origen="FROM imp_nueva n JOIN cuenta_canonica c ON c.despacho_id = :despacho "
        "AND c.cartera_id = :cartera AND c.cliente_unico = n.cliente_unico "
        "WHERE n.tipo = 'GESTION_REGISTRADA' AND " + _SIN_PROBLEMA.format(linea="n.linea"),
    ),
    "WITH gestiones AS (INSERT INTO gestion_cobranza (ocurrido_en, evento_lifecycle_id, "
    "gestion_id, cuenta_canonica_id, canal, medio, nivel_contacto, resultado, actor_ref, "
    "observacion) SELECT r.ocurrido_en, r.evento, gen_random_uuid(), r.cuenta, n.canal, n.medio, "
    "n.nivel_contacto, n.resultado, n.actor_ref, n.observacion FROM imp_registrado r "
    "JOIN imp_nueva n ON n.llave = r.llave WHERE n.tipo = 'GESTION_REGISTRADA' ORDER BY n.linea "
    "RETURNING id, evento_lifecycle_id) "
    "INSERT INTO visita_campo (inicio, fin, gestion_cobranza_id, visita_id, resultado, "
    "observacion) SELECT n.visita_inicio, n.visita_fin, g.id, gen_random_uuid(), "
    "n.visita_resultado, n.visita_observacion FROM gestiones g "
    "JOIN imp_registrado r ON r.evento = g.evento_lifecycle_id "
    "JOIN imp_nueva n ON n.llave = r.llave WHERE n.visita_resultado IS NOT NULL",
)
"""Fase 1: las gestiones de las cuentas de la cartera, con sus visitas."""

_REFERENCIA_CODIGO = (
    "WHEN {evento} IS NULL AND en_archivo IS NOT NULL AND en_archivo <> esperado "
    "THEN 'REFERENCIA_INVALIDA' "
    "WHEN {evento} IS NULL THEN 'REFERENCIA_NO_ENCONTRADA' "
    "WHEN referido <> esperado THEN 'REFERENCIA_INVALIDA' "
)
"""Los problemas de una referencia: lo que no existe, y lo que es de otro tipo, este registrado o
este en otra linea del archivo."""

_REFERENCIA_DETALLE = (
    "CASE WHEN {evento} IS NULL AND en_archivo IS NOT NULL AND en_archivo <> esperado "
    "THEN 'La llave ' || referencia || ' es de un ' || en_archivo || ' (linea ' || "
    "linea_referida || '), no de un ' || esperado || '.' "
    "WHEN {evento} IS NULL AND en_archivo IS NOT NULL THEN 'El ' || esperado || ' de la llave ' "
    "|| referencia || ' (linea ' || linea_referida || ') no se registro: tiene problemas.' "
    "WHEN {evento} IS NULL THEN 'No hay un ' || esperado || ' registrado con la llave ' "
    "|| referencia || '.' "
    "WHEN referido <> esperado THEN 'La llave ' || referencia || ' registro un ' || referido "
    "|| ', no un ' || esperado || '.' "
    "ELSE 'Lo que registro la llave ' || referencia || ' (' || referido || ') no lo admite.' "
    "END AS detalle "
)

_EN_EL_ARCHIVO = (
    "f.tipo AS en_archivo, f.linea AS linea_referida "
    "FROM imp_nueva n LEFT JOIN evento_lifecycle e ON e.despacho_id = :despacho "
    "AND e.cartera_id = :cartera AND e.idempotency_key = n.referencia "
    "LEFT JOIN imp_linea f ON f.llave = n.referencia "
)
"""A que se refiere cada linea: lo ya registrado con esa llave, y la linea del archivo que la
trae, si la hay (para decir por que no se encontro)."""

SQL_DETALLES = (
    # La gestion de cada promesa y de cada convenio, por su llave, ya registrada: antes o en la
    # fase anterior.
    "CREATE TEMP TABLE imp_detalle ON COMMIT DROP AS "
    "SELECT n.linea, n.llave, n.tipo, n.referencia, 'GESTION_REGISTRADA'::text AS esperado, "
    "e.id AS evento_gestion, e.tipo_evento AS referido, g.id AS gestion, "
    "g.cuenta_canonica_id AS cuenta, g.resultado AS resultado_gestion, "
    "e.ocurrido_en AS ocurrido_gestion, coalesce(n.ocurrido_en, e.ocurrido_en) AS ocurrido_en, "
    "EXISTS (SELECT 1 FROM evento_lifecycle a WHERE a.evento_relacionado_id = e.id "
    "AND a.tipo_evento = 'GESTION_ANULADA') AS anulada, "
    "EXISTS (SELECT 1 FROM evento_lifecycle x WHERE x.evento_relacionado_id = e.id "
    "AND x.tipo_evento = n.tipo) AS ya_tiene, "
    "row_number() OVER (PARTITION BY e.id, n.tipo ORDER BY n.linea) AS orden, "
    "n.fecha_limite < (coalesce(n.ocurrido_en, e.ocurrido_en) AT TIME ZONE :zona)::date "
    "AS limite_anterior, "
    + _EN_EL_ARCHIVO
    + "LEFT JOIN gestion_cobranza g ON g.evento_lifecycle_id = e.id "
    "WHERE n.tipo IN ('PROMESA_CREADA', 'CONVENIO_CREADO')",
    "INSERT INTO imp_problema SELECT linea, llave, codigo, detalle FROM ("
    "SELECT linea, llave, CASE "
    + _REFERENCIA_CODIGO.format(evento="evento_gestion")
    + "WHEN anulada THEN 'GESTION_ANULADA' "
    "WHEN tipo = 'PROMESA_CREADA' AND resultado_gestion <> 'PROMESA' THEN 'GESTION_SIN_PROMESA' "
    "WHEN tipo = 'CONVENIO_CREADO' AND resultado_gestion <> 'CONVENIO' "
    "THEN 'GESTION_SIN_CONVENIO' "
    "WHEN (ya_tiene OR orden > 1) AND tipo = 'PROMESA_CREADA' THEN 'PROMESA_YA_REGISTRADA' "
    "WHEN ya_tiene OR orden > 1 THEN 'CONVENIO_YA_REGISTRADO' "
    "WHEN ocurrido_en < ocurrido_gestion THEN 'OCURRIDO_ANTES_DEL_EVENTO' "
    "WHEN ocurrido_en > now() + :tolerancia THEN 'OCURRIDO_EN_FUTURO' "
    "WHEN tipo = 'PROMESA_CREADA' AND limite_anterior THEN 'FECHA_LIMITE_ANTERIOR' END AS codigo, "
    + _REFERENCIA_DETALLE.format(evento="evento_gestion")
    + "FROM imp_detalle) x WHERE codigo IS NOT NULL AND "
    + _SIN_PROBLEMA.format(linea="x.linea"),
    _EVENTOS.format(
        ocurrido_en="d.ocurrido_en",
        relacionado="d.evento_gestion",
        cuenta="d.cuenta",
        origen="FROM imp_detalle d JOIN imp_nueva n ON n.linea = d.linea WHERE "
        + _SIN_PROBLEMA.format(linea="d.linea"),
    ),
    "INSERT INTO promesa_pago (evento_lifecycle_id, gestion_cobranza_id, fecha_limite, promesa_id, "
    "cuenta_canonica_id, monto_prometido, version_modelo) SELECT r.evento, d.gestion, "
    "n.fecha_limite, gen_random_uuid(), r.cuenta, n.monto, :version FROM imp_detalle d "
    "JOIN imp_nueva n ON n.linea = d.linea JOIN imp_registrado r ON r.llave = d.llave "
    "WHERE d.tipo = 'PROMESA_CREADA' ORDER BY d.linea",
    "WITH convenios AS (INSERT INTO convenio_cobranza (evento_lifecycle_id, gestion_cobranza_id, "
    "fecha_inicio, fecha_fin, convenio_id, cuenta_canonica_id, cuotas, monto_total_acordado, "
    "version_modelo) SELECT r.evento, d.gestion, n.fecha_inicio, n.fecha_fin, gen_random_uuid(), "
    "r.cuenta, n.cuotas, n.monto, :version FROM imp_detalle d JOIN imp_nueva n ON "
    "n.linea = d.linea JOIN imp_registrado r ON r.llave = d.llave "
    "WHERE d.tipo = 'CONVENIO_CREADO' ORDER BY d.linea RETURNING id, evento_lifecycle_id) "
    "INSERT INTO cuota_convenio (convenio_cobranza_id, numero, fecha_vencimiento, monto) "
    "SELECT c.id, q.numero, q.fecha_vencimiento, q.monto FROM convenios c "
    "JOIN imp_registrado r ON r.evento = c.evento_lifecycle_id "
    "JOIN imp_nueva n ON n.llave = r.llave JOIN imp_cuota q ON q.linea = n.linea",
)
"""Fase 2: las promesas y los convenios, con sus cuotas declaradas."""

_CIERRES = (
    # Lo que cierra cada linea, por su llave, ya registrado; y la gestion de la que depende.
    "CREATE TEMP TABLE {tabla} ON COMMIT DROP AS "
    "SELECT n.linea, n.llave, n.tipo, n.referencia, n.ocurrido_en, "
    "CASE n.tipo WHEN 'GESTION_ANULADA' THEN 'GESTION_REGISTRADA' "
    "WHEN 'PROMESA_CANCELADA' THEN 'PROMESA_CREADA' ELSE 'CONVENIO_CREADO' END AS esperado, "
    "e.id AS objetivo, e.tipo_evento AS referido, e.cuenta_canonica_id AS cuenta, "
    "e.ocurrido_en AS ocurrido_objetivo, "
    "EXISTS (SELECT 1 FROM evento_lifecycle a WHERE a.tipo_evento = 'GESTION_ANULADA' "
    "AND a.evento_relacionado_id = CASE WHEN n.tipo = 'GESTION_ANULADA' THEN NULL "
    "ELSE e.evento_relacionado_id END) AS gestion_anulada, "
    "EXISTS (SELECT 1 FROM evento_lifecycle x WHERE x.evento_relacionado_id = e.id "
    "AND x.tipo_evento = n.tipo) AS ya_cerrado, "
    "row_number() OVER (PARTITION BY e.id, n.tipo ORDER BY n.linea) AS orden, "
    + _EN_EL_ARCHIVO
    + "WHERE n.tipo IN {tipos}",
    "INSERT INTO imp_problema SELECT linea, llave, codigo, detalle FROM ("
    "SELECT linea, llave, CASE "
    + _REFERENCIA_CODIGO.format(evento="objetivo")
    + "WHEN gestion_anulada AND tipo = 'PROMESA_CANCELADA' THEN 'PROMESA_ANULADA' "
    "WHEN gestion_anulada THEN 'CONVENIO_ANULADO' "
    "WHEN ya_cerrado OR orden > 1 THEN CASE tipo WHEN 'GESTION_ANULADA' "
    "THEN 'GESTION_YA_ANULADA' WHEN 'PROMESA_CANCELADA' THEN 'PROMESA_YA_CANCELADA' "
    "ELSE 'CONVENIO_YA_CANCELADO' END "
    "WHEN ocurrido_en < ocurrido_objetivo THEN 'OCURRIDO_ANTES_DEL_EVENTO' END AS codigo, "
    + _REFERENCIA_DETALLE.format(evento="objetivo")
    + "FROM {tabla}) x WHERE codigo IS NOT NULL AND "
    + _SIN_PROBLEMA.format(linea="x.linea"),
    _EVENTOS.format(
        ocurrido_en="n.ocurrido_en",
        relacionado="c.objetivo",
        cuenta="c.cuenta",
        origen="FROM {tabla} c JOIN imp_nueva n ON n.linea = c.linea WHERE "
        + _SIN_PROBLEMA.format(linea="c.linea"),
    ),
)

SQL_CANCELACIONES = tuple(
    s.replace("{tabla}", "imp_cancelacion").replace(
        "{tipos}", "('PROMESA_CANCELADA', 'CONVENIO_CANCELADO')"
    )
    for s in _CIERRES
)
"""Fase 3: las cancelaciones de promesas y convenios."""

SQL_ANULACIONES = tuple(
    s.replace("{tabla}", "imp_anulacion").replace("{tipos}", "('GESTION_ANULADA')")
    for s in _CIERRES
)
"""Fase 4: las anulaciones de gestiones, al final: anulan tambien lo que nacio de ellas."""


def _registrar(s: Session, reporte: Importacion, parametros: dict) -> None:
    """Las fases, en orden. Una linea con un problema no se registra; las demas si, para que las
    fases siguientes encuentren sus propios problemas. Quien llama decide si confirma."""
    for sentencia in SQL_LLAVES:
        s.execute(text(sentencia), parametros)
    reporte.repetidas = s.execute(
        text(
            "SELECT count(*) FROM imp_evento e JOIN imp_linea l ON l.llave = e.llave "
            "WHERE e.linea <> l.linea AND e.huella = l.huella"
        )
    ).scalar_one()
    reporte.ya_registradas = s.execute(
        text("SELECT count(*) FROM imp_previo WHERE misma")
    ).scalar_one()
    for fase in (SQL_GESTIONES, SQL_DETALLES, SQL_CANCELACIONES, SQL_ANULACIONES):
        for sentencia in fase:
            s.execute(text(sentencia), parametros)
    reporte.registrados = dict(
        s.execute(
            text(
                "SELECT n.tipo, count(*) FROM imp_registrado r JOIN imp_nueva n "
                "ON n.llave = r.llave GROUP BY n.tipo ORDER BY n.tipo"
            )
        ).all()
    )
