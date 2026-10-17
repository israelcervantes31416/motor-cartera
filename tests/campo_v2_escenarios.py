"""Lo que comparten las pruebas de campo/v2: un corte con coordenadas elegidas en EPSG:6372, su
decision/v2, su geografia, sus bases y su territorial/v2, configuraciones de campo, como abrir y
resolver un plan y leer lo que publico, y el espejo en Python de lo que el SQL tenia que publicar.

Todo es sintetico: los municipios son los rectangulos de `geografia_escenarios` (A, B y C) y de
`territorial_v2_escenarios` (D), las bases y los gestores son codigos inventados, y cada punto se
construye en EPSG:6372 y se lleva a EPSG:4326 con PostGIS, con todos sus decimales: asi una prueba
pone una cuenta exactamente en la frontera de una celda. Ninguna ubicacion es real.

Con decision-policy/public-v1, solo MUY_ALTA (91 dias o mas de atraso) va a CAMPO. `decision_mixta`
publica, para probar el orden por prioridad, una decision/v2 EXITOSA con las mismas features que una
real y la prioridad y el canal que la prueba elige: lo que una politica futura podria decidir.
"""

from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from geografia_escenarios import AMOZOC, ATLIXCO, FUENTE, MERIDA, PUEBLA, corte_de, geolocalizar
from historia_escenarios import SEMILLA, Cuenta, estado
from motor_pagos_escenarios import historiar_todo
from sqlalchemy import text
from sqlmodel import select
from territorial_v2_escenarios import asignar_tv2, publicar_bases, referencia_tv2

from motor_cartera.campo_v2 import configuracion as campo
from motor_cartera.campo_v2 import ejecuciones as motor
from motor_cartera.campo_v2 import politica
from motor_cartera.config import config
from motor_cartera.db.modelos import (
    CuentaCanonica,
    EjecucionCampoV2,
    EjecucionDecisionV2,
    ModoDecisionV2,
    VersionConfiguracionCampo,
)
from motor_cartera.db.sesion import sesion
from motor_cartera.decision.reglas import Prioridad
from motor_cartera.decision_v2 import ejecuciones as decision_v2
from motor_cartera.decision_v2.politica import NIVEL
from motor_cartera.generador.oficial import escribir_cartera, tabla_cartera
from motor_cartera.ingesta.corridas import ingerir_archivo

__all__ = ["AMOZOC", "ATLIXCO", "MERIDA", "PUEBLA"]

ZONA = "America/Mexico_City"
CORTE = date(2026, 9, 30)
"""Un miercoles."""
JUEVES = date(2026, 10, 1)
SABADO = date(2026, 10, 3)
DOMINGO = date(2026, 10, 4)
CAMPO = 120
"""Dias de atraso de una cuenta que decision/v2 manda a CAMPO (MUY_ALTA)."""
DIGITAL = 15

# Celdas de 1 km dentro del municipio A (PUEBLA), lejos de sus bordes: x de 2,889 a 2,909 km, y de
# 778 a 800 km en EPSG:6372.
X0, Y0 = 2_897_500, 787_500


@dataclass(frozen=True)
class CuentaCampo:
    """Una cuenta del corte: su numero, sus dias de atraso, el municipio que dice la fuente y su
    punto en EPSG:6372 (None: sin coordenadas)."""

    numero: int
    dias: int = CAMPO
    fuente: str = PUEBLA
    x: float | None = X0
    y: float | None = Y0


def coordenadas(puntos: list[tuple[float, float]]) -> list[tuple[str, str]]:
    """La latitud y la longitud de cada punto de EPSG:6372, con todos sus decimales (repr del
    double): vuelven al mismo punto en EPSG:6372 a menos de un nanometro."""
    if not puntos:
        return []
    with sesion() as s:
        filas = [
            s.execute(
                text(
                    "SELECT ST_Y(p), ST_X(p) FROM (SELECT ST_Transform(ST_SetSRID(ST_MakePoint("
                    ":x, :y), 6372), 4326) AS p) t"
                ),
                {"x": x, "y": y},
            ).one()
            for x, y in puntos
        ]
    return [(repr(lat), repr(lon)) for lat, lon in filas]


def tabla(cuentas: list[CuentaCampo], corte: date):
    base = tabla_cartera(
        estado([Cuenta(c.numero, dias=c.dias) for c in cuentas]), semilla=SEMILLA, fecha_corte=corte
    ).to_pandas()
    base = base.astype(object)
    con_punto = [c for c in cuentas if c.x is not None]
    latlon = dict(
        zip(
            (c.numero for c in con_punto),
            coordenadas([(c.x, c.y) for c in con_punto]),
            strict=True,
        )
    )
    for i, c in enumerate(cuentas):
        entidad, poblacion = FUENTE[c.fuente]
        base.at[i, "ESTADO_CTE"] = entidad
        base.at[i, "POBLACION_CTE"] = poblacion
        base.at[i, "LATITUD"], base.at[i, "LONGITUD"] = latlon.get(c.numero, (None, None))
    return base


def ingerir(destino: Path, cuentas: list[CuentaCampo], corte: date = CORTE, nombre: str = "campo"):
    ruta = destino / f"{nombre}_{corte.isoformat()}.csv"
    escribir_cartera(
        estado([]), ruta, semilla=SEMILLA, fecha_corte=corte, tabla=tabla(cuentas, corte)
    )
    corrida = ingerir_archivo(ruta, contrato="cartera/v2", fecha_corte=corte)
    assert corrida.estado == "EXITOSA", corrida.detalle
    historiar_todo()
    return corrida


def decidir(corte: date = CORTE) -> EjecucionDecisionV2:
    """decision/v2 del corte, OPERATIVO, con el reloj de la base: sin pagos ni lifecycle, la
    prioridad de cada cuenta es la de sus dias de atraso."""
    with sesion() as s:
        ejecucion = decision_v2.abrir(
            s,
            corte_de(corte).corte_id,
            as_of=None,
            known_at=None,
            modo=ModoDecisionV2.OPERATIVO,
            version_politica=None,
            zona=ZONA,
            max_intentos=5,
        )
        s.commit()
        ejecucion_id = ejecucion.id
    decision_v2.ejecutar_decision_v2(ejecucion_id)
    with sesion() as s:
        decidida = s.get_one(EjecucionDecisionV2, ejecucion_id)
    assert decidida.estado == "EXITOSA", decidida.detalle
    return decidida


def decision_mixta(
    real: EjecucionDecisionV2,
    prioridades: dict[int, str],
    canales: dict[int, str] | None = None,
) -> EjecucionDecisionV2:
    """Otra decision/v2 EXITOSA del mismo corte, con las features de `real` y, para cada numero de
    cliente de `prioridades`, esa prioridad y el canal CAMPO; las demas cuentas, BAJA y DIGITAL.
    Para probar el orden por prioridad de campo/v2 sin depender de que public-v1 solo mande MUY_ALTA
    a CAMPO. `canales` fija el canal de una cuenta, en lugar de esa regla."""
    with sesion() as s:
        columnas = [
            c
            for c in s.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_schema = "
                    "current_schema() AND table_name = 'feature_decision_cuenta' "
                    "ORDER BY ordinal_position"
                )
            ).scalars()
            if c != "ejecucion_decision_v2_id"
        ]
        nueva = s.execute(
            text(
                "INSERT INTO ejecucion_decision_v2 (decision_run_id, corte_canonico_id, "
                "despacho_id, cartera_id, as_of, known_at, modo, zona_horaria, version_motor, "
                "version_features, version_politica, hash_politica, firma_inputs, estado, "
                "resultado, cuentas_objetivo, cuentas_decididas, cuentas_fallidas, iniciada_en, "
                "terminada_en) SELECT :run, corte_canonico_id, despacho_id, cartera_id, as_of, "
                "known_at, 'BACKTEST', zona_horaria, version_motor, version_features, "
                "version_politica, hash_politica, :firma, 'EXITOSA', 'DECISION_PUBLICADA', "
                "cuentas_objetivo, cuentas_decididas, 0, now(), now() FROM ejecucion_decision_v2 "
                "WHERE id = :real RETURNING id"
            ),
            {"run": uuid4(), "firma": uuid4().hex * 2, "real": real.id},
        ).scalar_one()
        s.execute(
            text(
                f"INSERT INTO feature_decision_cuenta (ejecucion_decision_v2_id, "
                f"{', '.join(columnas)}) SELECT :nueva, {', '.join(columnas)} "
                "FROM feature_decision_cuenta WHERE ejecucion_decision_v2_id = :real"
            ),
            {"nueva": nueva, "real": real.id},
        )
        cuentas = dict(
            s.execute(
                text(
                    "SELECT c.cliente_unico, c.id FROM feature_decision_cuenta f "
                    "JOIN cuenta_canonica c ON c.id = f.cuenta_canonica_id "
                    "WHERE f.ejecucion_decision_v2_id = :nueva"
                ),
                {"nueva": nueva},
            ).all()
        )
        for cliente_unico, cuenta in cuentas.items():
            prioridad = prioridades.get(int(cliente_unico[2:]), "BAJA")
            canal = (canales or {}).get(
                int(cliente_unico[2:]),
                "CAMPO" if int(cliente_unico[2:]) in prioridades else "DIGITAL",
            )
            nivel = NIVEL[Prioridad(prioridad)]
            s.execute(
                text(
                    "INSERT INTO resultado_decision_v2 (ejecucion_decision_v2_id, "
                    "cuenta_canonica_id, score_nivel_base, ajuste_total, score_nivel_final, "
                    "prioridad_base, prioridad_final, canal_recomendado, motivos, decision_hash) "
                    "VALUES (:e, :c, :n, 0, :n, :p, :p, :canal, CAST('[]' AS jsonb), :h)"
                ),
                {
                    "e": nueva,
                    "c": cuenta,
                    "n": nivel,
                    "p": prioridad,
                    "canal": canal,
                    "h": hashlib.sha256(f"{nueva}:{cuenta}".encode()).digest(),
                },
            )
        s.commit()
        return s.get_one(EjecucionDecisionV2, nueva)


# --- las bases y la configuracion de campo --------------------------------------------------------


def base(codigo: str, x: float, y: float, municipios: list[str], *, activa: bool = True) -> dict:
    """Una base en un punto de EPSG:6372."""
    ((latitud, longitud),) = coordenadas([(x, y)])
    return {
        "codigo": codigo,
        "nombre": f"Base {codigo} (sintetica)",
        "lat": float(latitud),
        "lon": float(longitud),
        "activa": activa,
        "municipios": municipios,
    }


LV_PARTIDO = {
    "codigo": "LV_PARTIDO",
    "semana": {
        dia: [{"inicio": "09:00", "fin": "14:00"}, {"inicio": "16:00", "fin": "19:00"}]
        for dia in ("LUNES", "MARTES", "MIERCOLES", "JUEVES", "VIERNES")
    },
}
"""De lunes a viernes, de 9 a 14 y de 16 a 19: un turno partido."""


def gestor(
    codigo: str,
    base_codigo: str = "BASE_A",
    capacidad: int = 20,
    *,
    calendario: str = "LV_PARTIDO",
    activo: bool = True,
) -> dict:
    return {
        "codigo": codigo,
        "base": base_codigo,
        "calendario": calendario,
        "capacidad_cuentas_dia": capacidad,
        "activo": activo,
    }


def documento(bases, gestores: list[dict], calendarios=None, *, nombre: str = "Campo") -> dict:
    return {
        "nombre": nombre,
        "bases_version_id": str(bases.bases_version_id),
        "zona_horaria": ZONA,
        "calendarios": copy.deepcopy(calendarios or [LV_PARTIDO]),
        "gestores": gestores,
    }


def publicar_campo(bases, gestores: list[dict], calendarios=None, **opciones):
    """Una version de la configuracion de campo, publicada."""
    with sesion() as s:
        publicada = campo.publicar(
            s, campo.leer(documento(bases, gestores, calendarios, **opciones))
        )
        s.commit()
        s.refresh(publicada.version)
        return publicada.version


# --- el escenario y el plan -----------------------------------------------------------------------


def escenario(
    destino: Path, cuentas: list[CuentaCampo], bases: tuple[dict, ...], corte: date = CORTE
) -> SimpleNamespace:
    """Un corte con su decision/v2, su geografia, una version de bases y su territorial/v2."""
    referencia = referencia_tv2(destino)
    ingerir(destino, cuentas, corte)
    decision = decidir(corte)
    geografia = geolocalizar(corte, referencia)
    version_bases = publicar_bases(*bases)
    territorial = asignar_tv2(corte, geografia, version_bases)
    assert territorial.estado == "EXITOSA", territorial.detalle
    return SimpleNamespace(
        corte=corte_de(corte),
        referencia=referencia,
        decision=decision,
        geografia=geografia,
        bases=version_bases,
        territorial=territorial,
    )


def abrir_cv2(
    e: SimpleNamespace,
    configuracion: VersionConfiguracionCampo,
    fecha: date = JUEVES,
    *,
    decision: EjecucionDecisionV2 | None = None,
    **opciones,
) -> int:
    with sesion() as s:
        ejecucion = motor.abrir(
            s,
            e.corte.corte_id,
            (decision or e.decision).decision_run_id,
            e.territorial.territorial_run_id,
            configuracion.configuracion_campo_version_id,
            fecha,
            max_intentos=config.worker_max_intentos,
            **opciones,
        )
        s.commit()
        return ejecucion.id


def plan(ejecucion_id: int) -> EjecucionCampoV2:
    with sesion() as s:
        return s.get_one(EjecucionCampoV2, ejecucion_id)


def planear(
    e: SimpleNamespace, configuracion: VersionConfiguracionCampo, fecha: date = JUEVES, **opciones
) -> EjecucionCampoV2:
    """Abre y resuelve el plan, como lo haria su trabajo, y lo devuelve terminado."""
    ejecucion_id = abrir_cv2(e, configuracion, fecha, **opciones)
    motor.ejecutar_campo_v2(ejecucion_id)
    return plan(ejecucion_id)


def asignaciones(ejecucion_id: int) -> dict[int, dict]:
    """Lo que publico el plan, por numero de cliente."""
    with sesion() as s:
        filas = s.execute(
            text(
                "SELECT c.cliente_unico, a.estado, a.prioridad, b.codigo, g.codigo, "
                "a.es_rebalanceada, a.orden_prioridad, a.zona_id, a.subzona_id, a.tramo_id, "
                "a.microtramo_id, a.motivos FROM asignacion_campo_cuenta a "
                "JOIN cuenta_canonica c ON c.id = a.cuenta_canonica_id "
                "LEFT JOIN base_operativa b ON b.id = a.base_operativa_id "
                "LEFT JOIN gestor_campo g ON g.id = a.gestor_campo_id "
                "WHERE a.ejecucion_campo_v2_id = :e"
            ),
            {"e": ejecucion_id},
        ).all()
    return {
        int(f[0][2:]): {
            "estado": f[1],
            "prioridad": f[2],
            "base": f[3],
            "gestor": f[4],
            "rebalanceada": f[5],
            "orden": f[6],
            "zona": f[7],
            "subzona": f[8],
            "tramo": f[9],
            "microtramo": f[10],
            "motivos": f[11],
        }
        for f in filas
    }


def gestores_de(ejecucion_id: int) -> dict[int, str | None]:
    return {n: a["gestor"] for n, a in asignaciones(ejecucion_id).items()}


def cargas(ejecucion_id: int) -> dict[str, dict]:
    with sesion() as s:
        filas = s.execute(
            text(
                "SELECT g.codigo, c.estado_jornada, c.capacidad, c.asignadas, c.rebalanceadas, "
                "c.microtramos, c.tramos, c.utilizacion, c.ventanas FROM carga_gestor_campo c "
                "JOIN gestor_campo g ON g.id = c.gestor_campo_id WHERE c.ejecucion_campo_v2_id = :e"
            ),
            {"e": ejecucion_id},
        ).all()
    return {
        f[0]: dict(
            zip(
                (
                    "jornada",
                    "capacidad",
                    "asignadas",
                    "rebalanceadas",
                    "microtramos",
                    "tramos",
                    "utilizacion",
                    "ventanas",
                ),
                f[1:],
                strict=True,
            )
        )
        for f in filas
    }


def unidades(ejecucion_id: int) -> dict:
    with sesion() as s:
        filas = s.execute(
            text(
                "SELECT unidad_id, nivel, padre_id, cve_ent, cvegeo, candidatas, asignadas, "
                "propietario_gestor_campo_id FROM unidad_territorial_campo "
                "WHERE ejecucion_campo_v2_id = :e"
            ),
            {"e": ejecucion_id},
        ).all()
    return {f[0]: f[1:] for f in filas}


# --- el espejo ------------------------------------------------------------------------------------


def espejo(ejecucion_id: int) -> dict[int, tuple]:
    """Lo que `politica.asignar` concluye de cada candidata, con lo que se consulta aparte y cuenta
    por cuenta: su decision, su asignacion territorial, la celda de su punto (PostGIS, punto por
    punto) y la jornada de cada gestor en la fecha (la regla pura sobre la configuracion leida de
    sus tablas). Lo que el SQL por conjuntos tenia que publicar, en la forma de `publicadas`."""
    with sesion() as s:
        e = s.get_one(EjecucionCampoV2, ejecucion_id)
        filas = s.execute(
            text(
                "SELECT c.cliente_unico, c.cuenta_id, r.prioridad_final, a.estado_asignacion, "
                "a.motivos, b.codigo, m.cvegeo, g.punto FROM resultado_decision_v2 r "
                "JOIN cuenta_canonica c ON c.id = r.cuenta_canonica_id "
                "JOIN asignacion_territorial_v2 a ON a.ejecucion_territorial_v2_id = :t "
                "AND a.cuenta_canonica_id = r.cuenta_canonica_id "
                "LEFT JOIN base_operativa b ON b.id = a.base_operativa_id "
                "LEFT JOIN municipio_geografico m ON m.id = a.municipio_geografico_id "
                "LEFT JOIN resultado_geografia_cuenta g ON g.ejecucion_geografia_id = "
                "a.ejecucion_geografia_id AND g.cuenta_canonica_id = a.cuenta_canonica_id "
                "WHERE r.ejecucion_decision_v2_id = :d AND r.canal_recomendado = 'CAMPO'"
            ),
            {"t": e.ejecucion_territorial_v2_id, "d": e.ejecucion_decision_v2_id},
        ).all()
        candidatas = []
        clientes = {}
        for (
            cliente_unico,
            cuenta_id,
            prioridad,
            estado_t,
            motivos_t,
            codigo,
            cvegeo,
            punto,
        ) in filas:
            clientes[cuenta_id] = int(cliente_unico[2:])
            ix = iy = None
            if estado_t == "ASIGNADA":
                x, y = s.execute(
                    text("SELECT ST_X(p), ST_Y(p) FROM ST_Transform(CAST(:p AS geometry), 6372) p"),
                    {"p": punto},
                ).one()
                ix = politica.celda(x, politica.PUBLIC_V2.tamano_microtramo_m)
                iy = politica.celda(y, politica.PUBLIC_V2.tamano_microtramo_m)
            candidatas.append(
                politica.Candidata(
                    cuenta_id,
                    prioridad,
                    estado_t,
                    tuple(m["codigo"] for m in motivos_t),
                    codigo if estado_t == "ASIGNADA" else None,
                    cvegeo if estado_t == "ASIGNADA" else None,
                    ix,
                    iy,
                )
            )
        version = s.get_one(VersionConfiguracionCampo, e.version_configuracion_campo_id)
        leida = campo.configuracion_de(s, version)
    capacidades: dict[str, dict[str, int]] = {}
    jornadas: dict[str, dict] = {}
    for g in leida.gestores:
        estado_jornada, _ = campo.jornada(g, leida.calendario(g.calendario), e.fecha_operacion)
        jornadas.setdefault(g.base, {}).setdefault(estado_jornada, 0)
        jornadas[g.base][estado_jornada] += 1
        if estado_jornada in politica.DISPONIBLES:
            capacidades.setdefault(g.base, {})[g.codigo] = g.capacidad_cuentas_dia
    resultados = politica.asignar(candidatas, capacidades, jornadas)
    return {
        clientes[cuenta_id]: (
            r.estado.value,
            r.base,
            r.gestor,
            r.rebalanceada,
            r.orden_prioridad,
            None if r.particion is None else r.particion.microtramo_id,
            None if r.particion is None else r.particion.zona_id,
            r.motivos,
        )
        for cuenta_id, r in resultados.items()
    }


def publicadas(ejecucion_id: int) -> dict[int, tuple]:
    """Lo que publico el plan, en la forma de `espejo`."""
    return {
        n: (
            a["estado"],
            a["base"],
            a["gestor"],
            a["rebalanceada"],
            a["orden"],
            a["microtramo"],
            a["zona"],
            a["motivos"],
        )
        for n, a in asignaciones(ejecucion_id).items()
    }


def cuenta_canonica(numero: int) -> CuentaCanonica:
    with sesion() as s:
        return s.exec(
            select(CuentaCanonica).where(CuentaCanonica.cliente_unico == f"CU{numero:010d}")
        ).one()


def metros(valor) -> Decimal:
    return Decimal(str(valor))
