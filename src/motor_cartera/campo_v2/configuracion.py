"""Una version de la configuracion de campo: leerla, validarla, darle su hash y publicarla.

La configuracion de campo es CONFIGURACION OPERACIONAL VERSIONADA, como las bases operativas: no es
una fuente oficial, ni CARTERA ni PAGOS. Dice, sobre una version de bases declarada, que gestores de
campo hay (un codigo opaco, nunca una persona), de que base es cada uno, con que calendario trabaja,
cuantas cuentas puede atender al dia (su cupo declarado) y si esta activo; que calendarios hay (las
ventanas de cada dia de la semana, en hora local) y que excepciones tiene cada uno en una fecha
(cerrado, o ventanas especiales); y en que zona horaria se leen esas horas.

El documento, en JSON (POST /campo/configuraciones, `motor-cartera cargar-campo`):

    {"nombre": "Campo sintetico, octubre",
     "bases_version_id": "<uuid>",
     "zona_horaria": "America/Mexico_City",
     "calendarios": [{"codigo": "LV_PARTIDO",
                      "semana": {"LUNES": [{"inicio": "09:00", "fin": "14:00"},
                                           {"inicio": "16:00", "fin": "19:00"}], ...},
                      "excepciones": [{"fecha": "2026-11-16", "tipo": "CERRADO",
                                       "motivo": "Festivo"}]}],
     "gestores": [{"codigo": "GESTOR_001", "base": "BASE_A", "calendario": "LV_PARTIDO",
                   "capacidad_cuentas_dia": 20, "activo": true}]}

o un JSONL con un registro por linea (`"registro"`: `configuracion`, `calendario` o `gestor`). Se
valida entero, fail-closed, y todos los problemas se reportan juntos. Sin nombres, telefonos,
correos ni identificadores de personas: un gestor es su codigo.

La forma canonica (campo-configuracion/v1): el hash de la version de bases (no su identificador, que
es de cada base de datos), la zona, los calendarios por codigo con los siete dias y sus ventanas en
orden, las excepciones por fecha, y los gestores por codigo. El nombre de la version no entra. El
mismo contenido es la misma version: se reutiliza. Una version publicada no cambia nunca.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import text
from sqlmodel import Session, select

from motor_cartera.campo_v2.politica import EstadoJornada
from motor_cartera.db.copia import copiar
from motor_cartera.db.modelos import (
    PATRON_CODIGO_BASE,
    VersionBasesOperativas,
    VersionConfiguracionCampo,
    ahora,
)
from motor_cartera.db.sesion import insertar_en_savepoint, restriccion

VERSION_CONFIGURACION = "campo-configuracion/v1"
"""Cambia si cambia la forma canonica con que se calcula el hash."""
DIAS = ("LUNES", "MARTES", "MIERCOLES", "JUEVES", "VIERNES", "SABADO", "DOMINGO")
"""Los dias de la semana: el de la posicion i es el dia ISO i + 1 (lunes 1, domingo 7)."""
LLAVES_DEL_DOCUMENTO = frozenset(
    {"nombre", "bases_version_id", "zona_horaria", "calendarios", "gestores"}
)
LLAVES_DE_UN_CALENDARIO = frozenset({"codigo", "semana", "excepciones"})
LLAVES_DE_UNA_VENTANA = frozenset({"inicio", "fin"})
LLAVES_DE_UNA_EXCEPCION = frozenset({"fecha", "tipo", "motivo", "ventanas"})
LLAVES_DE_UN_GESTOR = frozenset({"codigo", "base", "calendario", "capacidad_cuentas_dia", "activo"})
CAPACIDAD_MAXIMA = 1_000
"""El cupo mas grande que se acepta: un tope de cordura contra un error de captura (2,000 en lugar
de 20), no una regla de negocio."""
LARGO_DE_TEXTO = 120
PATRON_HORA = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$|^24:00$")
PATRON_ZONA = re.compile(r"^[A-Za-z0-9_/+-]{1,64}$")
RESTRICCION_HASH = "uq_version_configuracion_campo_hash"
MINUTOS_DEL_DIA = 24 * 60


class ConfiguracionInvalida(ValueError):
    """La configuracion no se puede publicar: no se registro nada. `problemas` dice cada uno."""

    def __init__(self, problemas: list[str]) -> None:
        super().__init__("La configuracion de campo no es valida: " + "; ".join(problemas) + ".")
        self.problemas = problemas


class TipoExcepcion(StrEnum):
    CERRADO = "CERRADO"
    """Ese dia el calendario no tiene jornada, aunque la semana diga otra cosa."""
    VENTANAS_ESPECIALES = "VENTANAS_ESPECIALES"
    """Ese dia el calendario tiene estas ventanas, y no las de la semana."""


def hhmm(minutos: int) -> str:
    """Los minutos desde la medianoche como HH:MM; 1440 es 24:00."""
    return f"{minutos // 60:02d}:{minutos % 60:02d}"


def minutos(hora: str) -> int:
    horas, mins = hora.split(":")
    return int(horas) * 60 + int(mins)


@dataclass(frozen=True, order=True)
class Ventana:
    """Una ventana de disponibilidad de un dia, en hora local: [inicio, fin), en minutos desde la
    medianoche. 24:00 solo puede ser el fin."""

    inicio: int
    fin: int

    def canonica(self) -> list[str]:
        return [hhmm(self.inicio), hhmm(self.fin)]

    @property
    def minutos(self) -> int:
        return self.fin - self.inicio


@dataclass(frozen=True)
class Excepcion:
    fecha: date
    tipo: TipoExcepcion
    motivo: str | None
    ventanas: tuple[Ventana, ...]

    def canonica(self) -> dict[str, Any]:
        return {
            "fecha": self.fecha.isoformat(),
            "tipo": self.tipo.value,
            "motivo": self.motivo,
            "ventanas": [v.canonica() for v in self.ventanas],
        }


@dataclass(frozen=True)
class Calendario:
    codigo: str
    semana: tuple[tuple[Ventana, ...], ...]
    """Siete dias, de lunes a domingo, cada uno con sus ventanas en orden (o ninguna)."""
    excepciones: tuple[Excepcion, ...]
    """En orden de fecha, una por fecha."""

    def canonica(self) -> dict[str, Any]:
        return {
            "codigo": self.codigo,
            "semana": {dia: [v.canonica() for v in self.semana[i]] for i, dia in enumerate(DIAS)},
            "excepciones": [e.canonica() for e in self.excepciones],
        }

    def excepcion(self, fecha: date) -> Excepcion | None:
        return next((e for e in self.excepciones if e.fecha == fecha), None)

    @property
    def ventanas(self) -> int:
        return sum(len(dia) for dia in self.semana) + sum(len(e.ventanas) for e in self.excepciones)


@dataclass(frozen=True)
class GestorConfigurado:
    """Un gestor de campo: un codigo opaco (GESTOR_001), nunca el nombre ni un dato de una
    persona."""

    codigo: str
    base: str
    calendario: str
    capacidad_cuentas_dia: int
    activo: bool

    def canonica(self) -> dict[str, Any]:
        return {
            "codigo": self.codigo,
            "base": self.base,
            "calendario": self.calendario,
            "capacidad_cuentas_dia": self.capacidad_cuentas_dia,
            "activo": self.activo,
        }


@dataclass(frozen=True)
class ConfiguracionCampo:
    nombre: str
    bases_version_id: UUID
    zona_horaria: str
    calendarios: tuple[Calendario, ...]
    gestores: tuple[GestorConfigurado, ...]

    def canonica(self, hash_bases: str) -> str:
        """El texto que se firma: la version de la forma, el hash de la version de bases, la zona,
        los calendarios y los gestores, sin el nombre de la version, en JSON con las llaves en orden
        y sin espacios."""
        documento = {
            "version": VERSION_CONFIGURACION,
            "bases": hash_bases,
            "zona_horaria": self.zona_horaria,
            "calendarios": [c.canonica() for c in self.calendarios],
            "gestores": [g.canonica() for g in self.gestores],
        }
        return json.dumps(documento, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    def hash(self, hash_bases: str) -> str:
        return hashlib.sha256(self.canonica(hash_bases).encode("utf-8")).hexdigest()

    def calendario(self, codigo: str) -> Calendario:
        return next(c for c in self.calendarios if c.codigo == codigo)

    @property
    def activos(self) -> int:
        return sum(g.activo for g in self.gestores)

    @property
    def excepciones(self) -> int:
        return sum(len(c.excepciones) for c in self.calendarios)

    @property
    def ventanas(self) -> int:
        return sum(c.ventanas for c in self.calendarios)


# --- la jornada de un gestor en una fecha ---------------------------------------------------------


def jornada(
    gestor: GestorConfigurado, calendario: Calendario, fecha: date
) -> tuple[EstadoJornada, tuple[Ventana, ...]]:
    """Como esta un gestor en una fecha local, y sus ventanas: inactivo; o, con una excepcion de esa
    fecha, cerrado o con sus ventanas especiales; o con las ventanas de ese dia de la semana, si
    tiene alguna. El SQL de `campo_v2.ejecuciones` aplica la misma regla por conjuntos."""
    if not gestor.activo:
        return EstadoJornada.INACTIVO, ()
    excepcion = calendario.excepcion(fecha)
    if excepcion is not None:
        if excepcion.tipo == TipoExcepcion.CERRADO:
            return EstadoJornada.CERRADO_POR_EXCEPCION, ()
        return EstadoJornada.VENTANAS_ESPECIALES, excepcion.ventanas
    ventanas = calendario.semana[fecha.isoweekday() - 1]
    if not ventanas:
        return EstadoJornada.SIN_JORNADA, ()
    return EstadoJornada.JORNADA_SEMANAL, ventanas


def capacidad_efectiva(gestor: GestorConfigurado, calendario: Calendario, fecha: date) -> int:
    """Su cupo declarado si esta disponible esa fecha, y 0 si no. Una media jornada no lo reduce: el
    cupo no se deriva de las horas."""
    estado, _ = jornada(gestor, calendario, fecha)
    return gestor.capacidad_cuentas_dia if estado in _DISPONIBLES else 0


_DISPONIBLES = (EstadoJornada.JORNADA_SEMANAL, EstadoJornada.VENTANAS_ESPECIALES)


# --- leer -----------------------------------------------------------------------------------------


def _texto(valor: Any, donde: str, campo: str, problemas: list[str]) -> str | None:
    if not isinstance(valor, str) or not valor.strip():
        problemas.append(f"{donde}: {campo} es un texto no vacio")
        return None
    texto = unicodedata.normalize("NFC", valor.strip())
    if len(texto) > LARGO_DE_TEXTO:
        problemas.append(f"{donde}: {campo} tiene mas de {LARGO_DE_TEXTO} caracteres")
    return texto


def _codigo(valor: Any, donde: str, campo: str, problemas: list[str]) -> str | None:
    if not isinstance(valor, str) or not re.fullmatch(PATRON_CODIGO_BASE, valor):
        problemas.append(f"{donde}: {campo} cumple {PATRON_CODIGO_BASE}")
        return None
    return valor


def _llaves(valor: dict, conocidas: frozenset[str], donde: str, problemas: list[str]) -> None:
    desconocidas = sorted(set(valor) - conocidas)
    if desconocidas:
        problemas.append(f"{donde} trae llaves que no se conocen: {', '.join(desconocidas)}")


def _ventanas(valor: Any, donde: str, problemas: list[str]) -> tuple[Ventana, ...]:
    """Las ventanas de un dia o de una excepcion: cada una con inicio y fin HH:MM, inicio antes que
    fin, y sin encimarse con otra del mismo dia (tocarse no es encimarse)."""
    if not isinstance(valor, list):
        problemas.append(f"{donde}: las ventanas son una lista")
        return ()
    leidas: list[Ventana] = []
    for i, ventana in enumerate(valor):
        aqui = f"{donde}[{i}]"
        if not isinstance(ventana, dict):
            problemas.append(f"{aqui} es un objeto con inicio y fin")
            continue
        _llaves(ventana, LLAVES_DE_UNA_VENTANA, aqui, problemas)
        inicio, fin = ventana.get("inicio"), ventana.get("fin")
        horas_validas = True
        for campo, hora in (("inicio", inicio), ("fin", fin)):
            if not isinstance(hora, str) or not PATRON_HORA.fullmatch(hora):
                problemas.append(f"{aqui}: {campo} es una hora HH:MM entre 00:00 y 24:00")
                horas_validas = False
        if not horas_validas:
            continue
        if inicio == "24:00":
            problemas.append(f"{aqui}: una ventana no empieza a las 24:00")
            continue
        leida = Ventana(minutos(inicio), minutos(fin))
        if leida.inicio >= leida.fin:
            problemas.append(f"{aqui}: el inicio ({inicio}) es antes que el fin ({fin})")
            continue
        leidas.append(leida)
    leidas.sort()
    for anterior, siguiente in zip(leidas, leidas[1:], strict=False):
        if siguiente.inicio < anterior.fin:
            problemas.append(
                f"{donde}: las ventanas {hhmm(anterior.inicio)}-{hhmm(anterior.fin)} y "
                f"{hhmm(siguiente.inicio)}-{hhmm(siguiente.fin)} se enciman"
            )
    return tuple(leidas)


def _excepcion(valor: Any, donde: str, problemas: list[str]) -> Excepcion | None:
    if not isinstance(valor, dict):
        problemas.append(f"{donde} es un objeto")
        return None
    _llaves(valor, LLAVES_DE_UNA_EXCEPCION, donde, problemas)
    fecha = None
    crudo = valor.get("fecha")
    try:
        if not isinstance(crudo, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", crudo):
            raise ValueError
        fecha = date.fromisoformat(crudo)
    except ValueError:
        problemas.append(f"{donde}: fecha es una fecha AAAA-MM-DD que existe")
    else:
        donde = f"{donde} ({fecha.isoformat()})"
    tipo = valor.get("tipo")
    if tipo not in {t.value for t in TipoExcepcion}:
        problemas.append(f"{donde}: tipo es CERRADO o VENTANAS_ESPECIALES")
        tipo = None
    motivo = None
    if valor.get("motivo") is not None:
        motivo = _texto(valor["motivo"], donde, "motivo", problemas)
    ventanas: tuple[Ventana, ...] = ()
    if tipo == TipoExcepcion.CERRADO and "ventanas" in valor:
        problemas.append(f"{donde}: una excepcion CERRADO no tiene ventanas")
    elif tipo == TipoExcepcion.VENTANAS_ESPECIALES:
        if not valor.get("ventanas"):
            problemas.append(
                f"{donde}: una excepcion VENTANAS_ESPECIALES tiene al menos una ventana"
            )
        else:
            ventanas = _ventanas(valor["ventanas"], f"{donde}.ventanas", problemas)
    if fecha is None or tipo is None:
        return None
    return Excepcion(fecha, TipoExcepcion(tipo), motivo, ventanas)


def _calendario(valor: Any, i: int, problemas: list[str]) -> Calendario | None:
    donde = f"calendarios[{i}]"
    if not isinstance(valor, dict):
        problemas.append(f"{donde} es un objeto")
        return None
    _llaves(valor, LLAVES_DE_UN_CALENDARIO, donde, problemas)
    codigo = _codigo(valor.get("codigo"), donde, "codigo", problemas)
    if codigo is not None:
        donde = f"el calendario {codigo}"
    semana = valor.get("semana")
    if not isinstance(semana, dict):
        problemas.append(f"{donde}: semana es un objeto con los dias que tienen jornada")
        semana = {}
    dias = sorted(set(semana) - set(DIAS))
    if dias:
        problemas.append(
            f"{donde}: dias que no se conocen: {', '.join(map(str, dias))} (son {', '.join(DIAS)})"
        )
    por_dia = tuple(
        _ventanas(semana[dia], f"{donde}.{dia}", problemas) if dia in semana else () for dia in DIAS
    )
    crudas = valor.get("excepciones", [])
    if not isinstance(crudas, list):
        problemas.append(f"{donde}: excepciones es una lista")
        crudas = []
    leidas = [
        _excepcion(cruda, f"{donde}.excepciones[{j}]", problemas) for j, cruda in enumerate(crudas)
    ]
    excepciones = [e for e in leidas if e is not None]
    fechas = [e.fecha for e in excepciones]
    repetidas = sorted({f.isoformat() for f in fechas if fechas.count(f) > 1})
    if repetidas:
        problemas.append(f"{donde}: fechas con mas de una excepcion: {', '.join(repetidas)}")
    if codigo is None:
        return None
    return Calendario(codigo, por_dia, tuple(sorted(excepciones, key=lambda e: e.fecha)))


def _gestor(valor: Any, i: int, problemas: list[str]) -> GestorConfigurado | None:
    donde = f"gestores[{i}]"
    if not isinstance(valor, dict):
        problemas.append(f"{donde} es un objeto")
        return None
    _llaves(valor, LLAVES_DE_UN_GESTOR, donde, problemas)
    faltan = sorted(LLAVES_DE_UN_GESTOR - set(valor))
    if faltan:
        problemas.append(f"{donde} no trae {', '.join(faltan)}")
        return None
    codigo = _codigo(valor["codigo"], donde, "codigo", problemas)
    if codigo is not None:
        donde = f"el gestor {codigo}"
    base = _codigo(valor["base"], donde, "base", problemas)
    calendario = _codigo(valor["calendario"], donde, "calendario", problemas)
    capacidad = valor["capacidad_cuentas_dia"]
    # Un entero de JSON, no un texto, un decimal ni un booleano: "20" o 20.5 no se adivinan.
    if isinstance(capacidad, bool) or not isinstance(capacidad, int):
        problemas.append(f"{donde}: capacidad_cuentas_dia es un entero")
        capacidad = None
    elif not 1 <= capacidad <= CAPACIDAD_MAXIMA:
        problemas.append(
            f"{donde}: capacidad_cuentas_dia esta entre 1 y {CAPACIDAD_MAXIMA:,} (un gestor sin "
            "cupo se declara inactivo)"
        )
        capacidad = None
    activo = valor["activo"]
    if not isinstance(activo, bool):
        problemas.append(f"{donde}: activo es true o false")
    if None in (codigo, base, calendario, capacidad) or not isinstance(activo, bool):
        return None
    return GestorConfigurado(codigo, base, calendario, capacidad, activo)


def _repetidos(crudos: list, llave: str) -> list[str]:
    valores = [c[llave] for c in crudos if isinstance(c, dict) and isinstance(c.get(llave), str)]
    return sorted({v for v in valores if valores.count(v) > 1})


def leer(documento: Any) -> ConfiguracionCampo:
    """La configuracion de un documento JSON ya decodificado, validada sin tocar la base: la forma,
    los codigos, las horas, las ventanas que se enciman, las excepciones, los cupos, los calendarios
    que cada gestor nombra y la zona horaria. Lo que depende de la base (que la version de bases
    exista y tenga las bases que se nombran) lo revisa `publicar`. Levanta ConfiguracionInvalida con
    todos sus problemas."""
    problemas: list[str] = []
    if not isinstance(documento, dict):
        raise ConfiguracionInvalida(
            [
                "el documento es un objeto con nombre, bases_version_id, zona_horaria, "
                "calendarios y gestores"
            ]
        )
    _llaves(documento, LLAVES_DEL_DOCUMENTO, "el documento", problemas)
    nombre = _texto(documento.get("nombre"), "la version", "nombre", problemas)
    bases_version_id = None
    try:
        bases_version_id = UUID(str(documento.get("bases_version_id")))
    except ValueError:
        problemas.append("bases_version_id es el UUID de una version de bases operativas")
    zona = documento.get("zona_horaria")
    if not isinstance(zona, str) or not PATRON_ZONA.fullmatch(zona) or not _zona_existe(zona):
        problemas.append(
            f"zona_horaria es una zona IANA explicita, como America/Mexico_City: {zona!r}"
        )
    calendarios = documento.get("calendarios")
    if not isinstance(calendarios, list) or not calendarios:
        problemas.append("calendarios es una lista no vacia")
        calendarios = []
    gestores = documento.get("gestores")
    if not isinstance(gestores, list) or not gestores:
        problemas.append("gestores es una lista no vacia")
        gestores = []
    leidos_c = [_calendario(c, i, problemas) for i, c in enumerate(calendarios)]
    leidos_g = [_gestor(g, i, problemas) for i, g in enumerate(gestores)]
    for coleccion, nombre_coleccion in ((calendarios, "calendarios"), (gestores, "gestores")):
        repetidos = _repetidos(coleccion, "codigo")
        if repetidos:
            problemas.append(f"{nombre_coleccion} con el codigo repetido: {', '.join(repetidos)}")
    declarados = {c.codigo for c in leidos_c if c is not None}
    for g in leidos_g:
        if g is not None and g.calendario not in declarados:
            problemas.append(f"el gestor {g.codigo}: el calendario {g.calendario} no existe")
    if problemas:
        raise ConfiguracionInvalida(problemas)
    return ConfiguracionCampo(
        nombre,
        bases_version_id,
        zona,
        tuple(sorted(leidos_c, key=lambda c: c.codigo)),
        tuple(sorted(leidos_g, key=lambda g: g.codigo)),
    )


def _zona_existe(zona: str) -> bool:
    try:
        ZoneInfo(zona)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


REGISTROS = ("configuracion", "calendario", "gestor")


def leer_jsonl(texto: str) -> ConfiguracionCampo:
    """Una configuracion en JSONL: un registro por linea, con `"registro"`: una linea
    `configuracion` (nombre, bases_version_id y zona_horaria), una por `calendario` y una por
    `gestor`, con sus mismos campos."""
    cabecera: dict | None = None
    calendarios: list = []
    gestores: list = []
    problemas: list[str] = []
    for numero, linea in enumerate(texto.splitlines(), start=1):
        if not linea.strip():
            continue
        try:
            registro = json.loads(linea)
        except json.JSONDecodeError as exc:
            raise ConfiguracionInvalida([f"la linea {numero} no es JSON: {exc.msg}"]) from exc
        tipo = registro.pop("registro", None) if isinstance(registro, dict) else None
        if tipo == "configuracion":
            if cabecera is not None:
                problemas.append(f"la linea {numero} repite el registro configuracion")
            cabecera = registro
        elif tipo == "calendario":
            calendarios.append(registro)
        elif tipo == "gestor":
            gestores.append(registro)
        else:
            problemas.append(f"la linea {numero}: registro es uno de {', '.join(REGISTROS)}")
    if cabecera is None:
        problemas.append("falta la linea con el registro configuracion")
    if problemas:
        raise ConfiguracionInvalida(problemas)
    return leer({**cabecera, "calendarios": calendarios, "gestores": gestores})


# --- publicar -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Publicada:
    """Una version publicada: la nueva, o la que ya existia con el mismo contenido."""

    version: VersionConfiguracionCampo
    creada: bool


def publicar(s: Session, configuracion: ConfiguracionCampo) -> Publicada:
    """La version de esa configuracion, en la transaccion de quien llama. Primero revisa lo que
    depende de la base: que la version de bases exista, que cada gestor sea de una base activa de
    ella y que PostgreSQL conozca la zona horaria; con un solo problema no escribe nada. Si ya hay
    una version con el mismo hash, esa, sin escribir nada; si no, la version, sus calendarios, sus
    ventanas, sus excepciones y sus gestores, enviados y sin confirmar. Dos publicaciones
    simultaneas del mismo contenido dan una sola version."""
    bases = s.exec(
        select(VersionBasesOperativas).where(
            VersionBasesOperativas.bases_version_id == configuracion.bases_version_id
        )
    ).one_or_none()
    if bases is None:
        raise ConfiguracionInvalida(
            [f"no existe la version de bases operativas {configuracion.bases_version_id}"]
        )
    activas = dict(
        s.execute(
            text(
                "SELECT codigo, activa FROM base_operativa WHERE version_bases_operativas_id = :v"
            ),
            {"v": bases.id},
        ).all()
    )
    problemas = []
    for g in configuracion.gestores:
        if g.base not in activas:
            problemas.append(f"el gestor {g.codigo}: la base {g.base} no existe en esa version")
        elif not activas[g.base]:
            problemas.append(
                f"el gestor {g.codigo}: la base {g.base} esta inactiva en esa version, y ninguna "
                "cuenta se le asigna"
            )
    zona_conocida = s.execute(
        text("SELECT count(*) FROM pg_timezone_names WHERE name = :zona"),
        {"zona": configuracion.zona_horaria},
    ).scalar_one()
    if not zona_conocida:
        problemas.append(f"PostgreSQL no conoce la zona {configuracion.zona_horaria}")
    if problemas:
        raise ConfiguracionInvalida(problemas)
    huella = configuracion.hash(bases.hash_configuracion)
    existente = _por_hash(s, huella)
    if existente is not None:
        return Publicada(existente, creada=False)
    # Nace publicada, en la transaccion que la crea: las dos marcas son el mismo instante.
    instante = ahora()
    version = VersionConfiguracionCampo(
        creada_en=instante,
        publicada_en=instante,
        nombre=configuracion.nombre,
        version_bases_operativas_id=bases.id,
        version_configuracion=VERSION_CONFIGURACION,
        hash_configuracion=huella,
        zona_horaria=configuracion.zona_horaria,
        calendarios=len(configuracion.calendarios),
        ventanas=configuracion.ventanas,
        excepciones=configuracion.excepciones,
        gestores=len(configuracion.gestores),
        gestores_activos=configuracion.activos,
        capacidad_declarada=sum(
            g.capacidad_cuentas_dia for g in configuracion.gestores if g.activo
        ),
        metadatos={
            "gestores_por_base": _por_base(configuracion),
            "forma": VERSION_CONFIGURACION,
        },
    )
    error = insertar_en_savepoint(s, version)
    if error is not None:
        if restriccion(error) != RESTRICCION_HASH:
            raise error
        return Publicada(_por_hash(s, huella), creada=False)
    _escribir(s, version, configuracion)
    return Publicada(version, creada=True)


def _por_base(configuracion: ConfiguracionCampo) -> dict[str, dict[str, int]]:
    por_base: dict[str, dict[str, int]] = {}
    for g in configuracion.gestores:
        b = por_base.setdefault(g.base, {"gestores": 0, "activos": 0, "capacidad_declarada": 0})
        b["gestores"] += 1
        if g.activo:
            b["activos"] += 1
            b["capacidad_declarada"] += g.capacidad_cuentas_dia
    return dict(sorted(por_base.items()))


def _escribir(
    s: Session, version: VersionConfiguracionCampo, configuracion: ConfiguracionCampo
) -> None:
    """Los calendarios y sus excepciones uno por uno (son pocos, y sus ventanas necesitan su id), y
    las ventanas y los gestores por COPY: una configuracion puede traer miles de gestores."""
    ids: dict[str, int] = {}
    excepciones: dict[tuple[str, date], int] = {}
    for c in configuracion.calendarios:
        ids[c.codigo] = s.execute(
            text(
                "INSERT INTO calendario_campo (calendario_id, version_configuracion_campo_id, "
                "codigo) VALUES (gen_random_uuid(), :version, :codigo) RETURNING id"
            ),
            {"version": version.id, "codigo": c.codigo},
        ).scalar_one()
        for e in c.excepciones:
            excepciones[(c.codigo, e.fecha)] = s.execute(
                text(
                    "INSERT INTO excepcion_calendario_campo (calendario_campo_id, fecha, tipo, "
                    "motivo) VALUES (:calendario, :fecha, :tipo, :motivo) RETURNING id"
                ),
                {"calendario": ids[c.codigo], "fecha": e.fecha, "tipo": e.tipo, "motivo": e.motivo},
            ).scalar_one()
    copiar(
        s,
        "ventana_jornada_campo",
        (
            "calendario_campo_id",
            "excepcion_calendario_campo_id",
            "dia_semana",
            "minuto_inicio",
            "minuto_fin",
        ),
        (
            fila
            for c in configuracion.calendarios
            for fila in (
                *(
                    (ids[c.codigo], None, i + 1, v.inicio, v.fin)
                    for i, dia in enumerate(c.semana)
                    for v in dia
                ),
                *(
                    (ids[c.codigo], excepciones[(c.codigo, e.fecha)], None, v.inicio, v.fin)
                    for e in c.excepciones
                    for v in e.ventanas
                ),
            )
        ),
    )
    bases = dict(
        s.execute(
            text("SELECT codigo, id FROM base_operativa WHERE version_bases_operativas_id = :v"),
            {"v": version.version_bases_operativas_id},
        ).all()
    )
    copiar(
        s,
        "gestor_campo",
        (
            "gestor_id",
            "version_configuracion_campo_id",
            "version_bases_operativas_id",
            "codigo",
            "base_operativa_id",
            "calendario_campo_id",
            "capacidad_cuentas_dia",
            "activo",
        ),
        (
            (
                str(uuid4()),
                version.id,
                version.version_bases_operativas_id,
                g.codigo,
                bases[g.base],
                ids[g.calendario],
                g.capacidad_cuentas_dia,
                g.activo,
            )
            for g in configuracion.gestores
        ),
    )


def _por_hash(s: Session, huella: str) -> VersionConfiguracionCampo | None:
    return s.exec(
        select(VersionConfiguracionCampo).where(
            VersionConfiguracionCampo.hash_configuracion == huella
        )
    ).one_or_none()


def configuracion_de(s: Session, version: VersionConfiguracionCampo) -> ConfiguracionCampo:
    """La configuracion de una version publicada, leida de sus tablas: con ella se vuelve a calcular
    su hash y se comprueba que nada cambio. Tambien es la que lee un plan."""
    bases_version_id = s.execute(
        text("SELECT bases_version_id FROM version_bases_operativas WHERE id = :v"),
        {"v": version.version_bases_operativas_id},
    ).scalar_one()
    calendarios = s.execute(
        text(
            "SELECT id, codigo FROM calendario_campo WHERE version_configuracion_campo_id = :v "
            "ORDER BY codigo"
        ),
        {"v": version.id},
    ).all()
    ventanas = s.execute(
        text(
            "SELECT w.calendario_campo_id, w.excepcion_calendario_campo_id, w.dia_semana, "
            "w.minuto_inicio, w.minuto_fin FROM ventana_jornada_campo w JOIN calendario_campo c "
            "ON c.id = w.calendario_campo_id WHERE c.version_configuracion_campo_id = :v "
            "ORDER BY w.minuto_inicio"
        ),
        {"v": version.id},
    ).all()
    excepciones = s.execute(
        text(
            "SELECT e.id, e.calendario_campo_id, e.fecha, e.tipo, e.motivo FROM "
            "excepcion_calendario_campo e JOIN calendario_campo c ON c.id = e.calendario_campo_id "
            "WHERE c.version_configuracion_campo_id = :v ORDER BY e.fecha"
        ),
        {"v": version.id},
    ).all()
    gestores = s.execute(
        text(
            "SELECT g.codigo, b.codigo, c.codigo, g.capacidad_cuentas_dia, g.activo FROM "
            "gestor_campo g JOIN base_operativa b ON b.id = g.base_operativa_id "
            "JOIN calendario_campo c ON c.id = g.calendario_campo_id "
            "WHERE g.version_configuracion_campo_id = :v ORDER BY g.codigo"
        ),
        {"v": version.id},
    ).all()
    leidos = []
    for calendario_id, codigo in calendarios:
        semana = tuple(
            tuple(
                Ventana(inicio, fin)
                for cal, exc, dia, inicio, fin in ventanas
                if cal == calendario_id and exc is None and dia == i + 1
            )
            for i in range(len(DIAS))
        )
        propias = tuple(
            Excepcion(
                fecha,
                TipoExcepcion(tipo),
                motivo,
                tuple(
                    Ventana(inicio, fin)
                    for cal, exc, _, inicio, fin in ventanas
                    if exc == excepcion_id
                ),
            )
            for excepcion_id, cal, fecha, tipo, motivo in excepciones
            if cal == calendario_id
        )
        leidos.append(Calendario(codigo, semana, propias))
    return ConfiguracionCampo(
        version.nombre,
        bases_version_id,
        version.zona_horaria,
        tuple(leidos),
        tuple(GestorConfigurado(*fila) for fila in gestores),
    )
