"""Lo que comparten las pruebas de ruteo/v2: un plan de campo/v2 con cuentas y bases en puntos
elegidos, una version de la red vial, proveedores de ruteo de pruebas, y como abrir y resolver una
ejecucion y leer lo que publico.

Los puntos se eligen en metros desde el origen de la red vial sintetica (`ruteo_v2.sintetica`),
dentro del municipio sintetico A: el mismo escenario sirve con un proveedor de pruebas y con OSRM
sobre la red sintetica. Ninguna ubicacion es real.

Los proveedores de pruebas NO son una red vial: `ProveedorDePruebas` responde con lo que la prueba
le dice (un snap y un tramo por par de puntos), y su version de la red es SINTETICO, con su propia
huella: un backend OSRM nunca la declara, y el motor no puede confundirlos.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from campo_v2_escenarios import CORTE, JUEVES, LV_PARTIDO, ZONA, decidir, decision_mixta
from geografia_escenarios import FUENTE, PUEBLA, corte_de, geolocalizar
from historia_escenarios import SEMILLA, Cuenta, estado
from motor_pagos_escenarios import historiar_todo
from sqlalchemy import text
from territorial_v2_escenarios import asignar_tv2, publicar_bases, referencia_tv2

from motor_cartera.campo_v2 import configuracion as campo
from motor_cartera.campo_v2 import ejecuciones as motor_campo
from motor_cartera.config import config
from motor_cartera.db.modelos import EjecucionCampoV2, EjecucionRuteoV2, VersionRedVial
from motor_cartera.db.sesion import sesion
from motor_cartera.generador.oficial import escribir_cartera, tabla_cartera
from motor_cartera.ingesta.corridas import ingerir_archivo
from motor_cartera.ruteo_v2 import ejecuciones as motor
from motor_cartera.ruteo_v2 import red_vial
from motor_cartera.ruteo_v2.proveedor import BackendIncompatible, Coordenada, MatrizVial, Metricas
from motor_cartera.ruteo_v2.sintetica import ORIGEN_PRUEBAS, Origen, osm_xml

__all__ = ["CORTE", "JUEVES", "LV_PARTIDO", "ZONA", "decision_mixta"]

ORIGEN = Origen(*ORIGEN_PRUEBAS)
CAMPO = 120
"""Dias de atraso de una cuenta que decision/v2 manda a CAMPO (MUY_ALTA)."""
VELOCIDAD = Decimal(25) / Decimal("3.6")
"""La del modelo geometrico de pruebas: 25 km/h, en m/s."""


@dataclass(frozen=True)
class CuentaRuta:
    """Una cuenta del corte: su numero, sus dias de atraso y su punto en metros desde el origen."""

    numero: int
    x: float
    y: float
    dias: int = CAMPO


def coordenada(x: float, y: float) -> Coordenada:
    """El punto como lo recibe el proveedor: seis decimales."""
    return ORIGEN.coordenada(x, y)


def base(codigo: str, x: float, y: float, municipios: list[str] | None = None) -> dict:
    lon, lat = ORIGEN.grados(x, y)
    return {
        "codigo": codigo,
        "nombre": f"Base {codigo} (sintetica)",
        "lat": float(lat),
        "lon": float(lon),
        "activa": True,
        "municipios": municipios or [PUEBLA],
    }


def gestor(codigo: str, base_codigo: str = "BASE_A", capacidad: int = 50, **opciones) -> dict:
    return {
        "codigo": codigo,
        "base": base_codigo,
        "calendario": opciones.get("calendario", "LV_PARTIDO"),
        "capacidad_cuentas_dia": capacidad,
        "activo": opciones.get("activo", True),
    }


def ingerir(destino: Path, cuentas: list[CuentaRuta], corte: date = CORTE):
    tabla = tabla_cartera(
        estado([Cuenta(c.numero, dias=c.dias) for c in cuentas]), semilla=SEMILLA, fecha_corte=corte
    ).to_pandas()
    tabla = tabla.astype(object)
    entidad, poblacion = FUENTE[PUEBLA]
    for i, c in enumerate(cuentas):
        lon, lat = ORIGEN.grados(c.x, c.y)
        tabla.at[i, "ESTADO_CTE"] = entidad
        tabla.at[i, "POBLACION_CTE"] = poblacion
        tabla.at[i, "LATITUD"], tabla.at[i, "LONGITUD"] = str(lat), str(lon)
    ruta = destino / f"ruteo_{corte.isoformat()}.csv"
    escribir_cartera(estado([]), ruta, semilla=SEMILLA, fecha_corte=corte, tabla=tabla)
    corrida = ingerir_archivo(ruta, contrato="cartera/v2", fecha_corte=corte)
    assert corrida.estado == "EXITOSA", corrida.detalle
    historiar_todo()


def escenario(
    destino: Path,
    cuentas: list[CuentaRuta],
    bases: list[dict],
    gestores: list[dict],
    *,
    calendarios: list[dict] | None = None,
    prioridades: dict[int, str] | None = None,
    fecha: date = JUEVES,
    canales: dict[int, str] | None = None,
) -> SimpleNamespace:
    """Un corte con sus cuentas, su decision/v2 (o una con esas prioridades y esos canales), su
    geografia, sus bases, su territorial/v2, una configuracion de campo y el plan de campo/v2 de
    `fecha`."""
    referencia = referencia_tv2(destino)
    ingerir(destino, cuentas)
    decision = decidir(CORTE)
    if prioridades is not None:
        decision = decision_mixta(decision, prioridades, canales)
    geografia = geolocalizar(CORTE, referencia)
    version_bases = publicar_bases(*bases)
    territorial = asignar_tv2(CORTE, geografia, version_bases)
    assert territorial.estado == "EXITOSA", territorial.detalle
    with sesion() as s:
        publicada = campo.publicar(
            s,
            campo.leer(
                {
                    "nombre": "Campo de ruteo",
                    "bases_version_id": str(version_bases.bases_version_id),
                    "zona_horaria": ZONA,
                    "calendarios": calendarios or [LV_PARTIDO],
                    "gestores": gestores,
                }
            ),
        )
        s.commit()
        s.refresh(publicada.version)
        configuracion = publicada.version
    with sesion() as s:
        plan = motor_campo.abrir(
            s,
            corte_de(CORTE).corte_id,
            decision.decision_run_id,
            territorial.territorial_run_id,
            configuracion.configuracion_campo_version_id,
            fecha,
            max_intentos=config.worker_max_intentos,
        )
        s.commit()
        plan_id = plan.id
    motor_campo.ejecutar_campo_v2(plan_id)
    with sesion() as s:
        plan = s.get_one(EjecucionCampoV2, plan_id)
    assert plan.estado == "EXITOSA", plan.detalle
    return SimpleNamespace(
        campo=plan, decision=decision, territorial=territorial, configuracion=configuracion
    )


# --- la red vial y los proveedores de pruebas -----------------------------------------------------


def descripcion(**cambios) -> red_vial.Descripcion:
    return red_vial.Descripcion(
        **{
            "proveedor_datos": "SINTETICO",
            "licencia": "sintetica",
            "region": "Red sintetica de pruebas",
            **cambios,
        }
    )


def fuente_sintetica(directorio: Path) -> Path:
    """La red sintetica en el origen de las pruebas, como fuente de una preparacion."""
    ruta = directorio / "red_sintetica.osm"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(osm_xml(ORIGEN))
    return ruta


def preparada(fuente: Path, raiz: Path) -> red_vial.Preparacion:
    """Una red "preparada" con archivos de mentira y la bitacora de extract de OSRM: lo que se
    prueba con ella es la verificacion del manifiesto, no OSRM."""
    preparacion = red_vial.preparar_directorio(
        fuente, descripcion(), red_vial.distribucion_docker(), raiz
    )
    for artefacto in (*red_vial.ARTEFACTOS_MLD, "turn_penalties_index"):
        (preparacion.directorio / f"red.osrm.{artefacto}").write_bytes(artefacto.encode() * 3)
    (preparacion.directorio / "extract.log").write_text(
        "[info] Raw input contains 19 nodes, 9 ways, and 0 relations, 0 restrictions\n"
        "[info] Writing used edges       ... ok, after 0s -- Processed 26 edges\n",
        encoding="utf-8",
    )
    return preparacion


def red_de_pruebas(nombre: str = "Red de pruebas", **modelo) -> VersionRedVial:
    """Una version SINTETICO de la red, para un proveedor de pruebas: no es una red vial."""
    with sesion() as s:
        registro = red_vial.registrar_sintetica(
            s, nombre, {"version": "pruebas/v1", "nombre": nombre, **modelo}
        )
        s.commit()
        s.refresh(registro.version)
        return registro.version


def metros_desde_origen(c: Coordenada) -> tuple[float, float]:
    lon, lat = (float(v) for v in c)
    y = (lat - float(ORIGEN.lat)) * 111_320.0
    x = (lon - float(ORIGEN.lon)) * 111_320.0 * math.cos(math.radians(float(ORIGEN.lat)))
    return x, y


def manhattan(a: Coordenada, b: Coordenada) -> tuple[Decimal, Decimal]:
    """El tramo del modelo geometrico de pruebas: la distancia Manhattan entre los puntos, al
    decimetro, y su duracion a 25 km/h. No es una red vial: es lo que la prueba decide."""
    (xa, ya), (xb, yb) = metros_desde_origen(a), metros_desde_origen(b)
    distancia = Decimal(repr(round(abs(xa - xb) + abs(ya - yb), 1)))
    return (distancia / VELOCIDAD).quantize(Decimal("0.1")), distancia


@dataclass
class ProveedorDePruebas:
    """Un proveedor de ruteo de pruebas, atado a la huella de su version SINTETICO de la red. Snap
    de 0 m salvo los que la prueba fija (None: sin snap); tramos del modelo geometrico salvo los
    desconectados (cualquier tramo que toque uno de esos puntos, salvo de el a el, es None)."""

    huella: str
    snaps: dict[Coordenada, Decimal | None] = field(default_factory=dict)
    desconectados: set[Coordenada] = field(default_factory=set)
    tramo: Callable[[Coordenada, Coordenada], tuple[Decimal, Decimal]] = manhattan
    arriba: bool = True
    falla: Exception | None = None
    """Si la tiene, cada consulta la levanta: un proveedor caido."""
    recibidas: list[Coordenada] = field(default_factory=list)
    """Cada coordenada que se le pregunto, en orden: lo que salio hacia la red."""

    def sesion(self, huella: str):
        return _SesionDePruebas(self, huella)

    def salud(self) -> bool:
        return self.arriba


@dataclass
class _SesionDePruebas:
    proveedor: ProveedorDePruebas
    pedida: str
    metricas: Metricas = field(default_factory=Metricas)

    def _verificar(self, coordenadas) -> None:
        self.proveedor.recibidas.extend(coordenadas)
        if self.proveedor.falla is not None:
            raise self.proveedor.falla
        if self.pedida != self.proveedor.huella:
            raise BackendIncompatible(self.pedida, self.proveedor.huella)

    def verificar(self) -> None:
        if self.proveedor.falla is not None:
            raise self.proveedor.falla
        if self.pedida != self.proveedor.huella:
            raise BackendIncompatible(self.pedida, self.proveedor.huella)

    def snaps(self, coordenadas):
        self._verificar(coordenadas)
        self.metricas.registrar("nearest", 1.0, 0, 0)
        return [self.proveedor.snaps.get(c, Decimal(0)) for c in coordenadas]

    def matriz(self, coordenadas, *, bloque: int) -> MatrizVial:
        self._verificar(coordenadas)
        trozos = math.ceil(len(coordenadas) / bloque)
        for _ in range(trozos * trozos):
            self.metricas.bloque()
            self.metricas.registrar("table", 1.0, 0, 0)
        duracion: list[list[Decimal | None]] = []
        distancia: list[list[Decimal | None]] = []
        for a in coordenadas:
            fila_d, fila_m = [], []
            for b in coordenadas:
                if a == b:
                    d, m = Decimal(0), Decimal(0)
                elif a in self.proveedor.desconectados or b in self.proveedor.desconectados:
                    d = m = None
                else:
                    d, m = self.proveedor.tramo(a, b)
                fila_d.append(d)
                fila_m.append(m)
            duracion.append(fila_d)
            distancia.append(fila_m)
        return MatrizVial(duracion, distancia)


# --- abrir, rutear y leer -------------------------------------------------------------------------


def abrir_rv2(campo_plan: EjecucionCampoV2, red: VersionRedVial, **opciones) -> int:
    with sesion() as s:
        ejecucion = motor.abrir(
            s,
            campo_plan.campo_run_id,
            red.red_vial_version_id,
            max_intentos=config.worker_max_intentos,
            **opciones,
        )
        s.commit()
        return ejecucion.id


def ejecucion(ejecucion_id: int) -> EjecucionRuteoV2:
    with sesion() as s:
        return s.get_one(EjecucionRuteoV2, ejecucion_id)


def rutear(campo_plan, red, proveedor, **opciones) -> EjecucionRuteoV2:
    """Abre y resuelve la ejecucion, como lo haria su trabajo, y la devuelve terminada."""
    ejecucion_id = abrir_rv2(campo_plan, red)
    motor.rutear(ejecucion_id, proveedor=proveedor, **opciones)
    return ejecucion(ejecucion_id)


def rutas(ejecucion_id: int) -> dict[str, dict]:
    """Lo que publico la ejecucion, por codigo de gestor."""
    with sesion() as s:
        filas = (
            s.execute(
                text(
                    "SELECT g.codigo AS gestor, b.codigo AS base, r.* FROM ruta_gestor_v2 r "
                    "JOIN gestor_campo g ON g.id = r.gestor_campo_id JOIN base_operativa b "
                    "ON b.id = r.base_operativa_id WHERE r.ejecucion_ruteo_v2_id = :e"
                ),
                {"e": ejecucion_id},
            )
            .mappings()
            .all()
        )
    return {f["gestor"]: dict(f) for f in filas}


def paradas(ejecucion_id: int) -> dict[int, dict]:
    """Las paradas publicadas, por numero de cliente."""
    with sesion() as s:
        filas = (
            s.execute(
                text(
                    "SELECT c.cliente_unico, c.cuenta_id, g.codigo AS gestor, x.* "
                    "FROM parada_ruta_v2 x JOIN cuenta_canonica c ON c.id = x.cuenta_canonica_id "
                    "JOIN gestor_campo g ON g.id = x.gestor_campo_id "
                    "WHERE x.ejecucion_ruteo_v2_id = :e"
                ),
                {"e": ejecucion_id},
            )
            .mappings()
            .all()
        )
    return {int(f["cliente_unico"][2:]): dict(f) for f in filas}


def secuencia(ejecucion_id: int, gestor_codigo: str) -> list[int]:
    """Los numeros de cliente de la ruta de un gestor, en su orden de visita."""
    programadas = [
        (p["orden_visita"], n)
        for n, p in paradas(ejecucion_id).items()
        if p["gestor"] == gestor_codigo and p["orden_visita"] is not None
    ]
    return [n for _, n in sorted(programadas)]


def resultado_logico(ejecucion_id: int) -> dict:
    """Lo que una reconstruccion tiene que repetir: cada ruta y cada parada, sin ids internos ni
    instantes de registro."""
    sin = {"ejecucion_ruteo_v2_id", "creada_en", "gestor_campo_id", "ejecucion_campo_v2_id"}
    return {
        "rutas": {
            g: {k: v for k, v in r.items() if k not in sin} for g, r in rutas(ejecucion_id).items()
        },
        "paradas": {
            n: {k: v for k, v in pa.items() if k not in {*sin, "cuenta_canonica_id"}}
            for n, pa in paradas(ejecucion_id).items()
        },
    }


def borrar(ejecucion_id: int) -> None:
    """Borra una ejecucion terminada y solo lo derivado de ella (sus paradas, sus rutas y su
    trabajo), apagando un momento los disparadores que lo protegen: para reconstruirla con las
    mismas entradas. Ni el plan de campo/v2 ni la red se tocan."""
    disparadores = (
        ("ruta_gestor_v2", "tr_ruteo_v2_inmutable"),
        ("parada_ruta_v2", "tr_ruteo_v2_inmutable"),
        ("ejecucion_ruteo_v2", "tr_ruteo_v2_terminado"),
    )
    with sesion() as s:
        for tabla, trigger in disparadores:
            s.execute(text(f"ALTER TABLE {tabla} DISABLE TRIGGER {trigger}"))
        for sentencia in (
            "DELETE FROM parada_ruta_v2 WHERE ejecucion_ruteo_v2_id = :e",
            "DELETE FROM ruta_gestor_v2 WHERE ejecucion_ruteo_v2_id = :e",
            "DELETE FROM trabajo_orquestacion WHERE ejecucion_ruteo_v2_id = :e",
            "DELETE FROM ejecucion_ruteo_v2 WHERE id = :e",
        ):
            s.execute(text(sentencia), {"e": ejecucion_id})
        for tabla, trigger in disparadores:
            s.execute(text(f"ALTER TABLE {tabla} ENABLE TRIGGER {trigger}"))
        s.commit()
