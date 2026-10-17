"""El linaje de un contexto: de donde sale cada resultado.

Cada nodo es una ejecucion, una version o el corte, con sus versiones y sus hashes; cada arista, una
relacion que la ejecucion que lee declaro al publicarse (una optimizacion leyo ese scoring, un plan
de campo esa territorial/v2, un analytics cada entrada de su manifiesto). Ninguna dependencia se
infiere: lo que no esta declarado en la base no se dibuja.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import text
from sqlmodel import Session

from motor_cartera.app_v1.contexto import ContextoResuelto, Ejecucion, Tipo


@dataclass
class Nodo:
    clave: str
    tipo: str
    run_id: UUID
    declarado: bool
    estado: str | None = None
    resultado: str | None = None
    version: str | None = None
    version_politica: str | None = None
    hashes: dict[str, str] = field(default_factory=dict)
    as_of: date | None = None
    known_at: datetime | None = None
    fecha_operacion: date | None = None
    iniciada_en: datetime | None = None
    terminada_en: datetime | None = None


@dataclass(frozen=True)
class Arista:
    desde: str
    hacia: str
    relacion: str


def clave(tipo: str, run_id: UUID) -> str:
    return f"{tipo}:{run_id}"


RELACIONES = {
    ("OPTIMIZACION", "SCORING"): "optimizo el valor de oportunidad de ese scoring",
    ("OPTIMIZACION", "DECISION_V2"): "tomo el canal y la prioridad de esa decision/v2",
    ("OPTIMIZACION", "CAMPO_V2"): "tomo los gestores de ese plan de campo",
    ("OPTIMIZACION", "RUTEO_V2"): "tomo las paradas PROGRAMADA de ese ruteo",
    ("OPTIMIZACION", "CONFIGURACION_OPTIMIZACION"): "con esa configuracion",
    ("CAMPO_V2", "DECISION_V2"): "planeo las candidatas de esa decision/v2",
    ("CAMPO_V2", "TERRITORIAL_V2"): "con las bases de esa territorial/v2",
    ("CAMPO_V2", "CONFIGURACION_CAMPO"): "con esa configuracion de campo",
    ("RUTEO_V2", "CAMPO_V2"): "ruteo ese plan de campo",
    ("RUTEO_V2", "RED_VIAL"): "sobre esa red vial",
    ("SCORING", "MODELO"): "puntuo con esa version del modelo",
    ("MODELO", "ENTRENAMIENTO"): "la publico ese entrenamiento",
    ("TERRITORIAL_V2", "GEOGRAFIA"): "asigno sobre esa geografia",
}


def construir(s: Session, resuelto: ContextoResuelto) -> tuple[list[Nodo], list[Arista]]:
    """Los nodos y las aristas del contexto, en un orden estable: el corte, las ejecuciones
    declaradas, lo que ellas leyeron y las entradas de analytics. A lo mas seis consultas aparte de
    las del contexto."""
    corte = resuelto.corte
    nodos: dict[str, Nodo] = {}
    aristas: list[Arista] = []

    def agregar(nodo: Nodo) -> str:
        nodos.setdefault(nodo.clave, nodo)
        return nodo.clave

    def unir(desde: str, hacia: str, relacion: str) -> None:
        arista = Arista(desde, hacia, relacion)
        if arista not in aristas:
            aristas.append(arista)

    del_corte = agregar(
        Nodo(
            clave=clave("CORTE", corte.corte_id),
            tipo="CORTE",
            run_id=corte.corte_id,
            declarado=True,
            version=corte.version_modelo,
            hashes={"firma_contenido": corte.firma_contenido},
            as_of=corte.fecha_corte,
            iniciada_en=corte.creado_en,
        )
    )
    for tipo in Tipo:
        ejecucion = resuelto.ejecucion(tipo)
        if ejecucion is None:
            continue
        propia = agregar(_de_ejecucion(ejecucion))
        if tipo in (Tipo.DECISION_V2, Tipo.SCORING, Tipo.OPTIMIZACION, Tipo.CAMPO_V2):
            unir(propia, del_corte, "es del corte")
        for leida, run_id in sorted(ejecucion.lee.items()):
            if tipo == Tipo.ANALYTICS:
                continue  # sus entradas, abajo, con su version y su firma
            relacion = RELACIONES.get((tipo.value, leida))
            if relacion is None:
                continue
            unir(propia, agregar(Nodo(clave(leida, run_id), leida, run_id, False)), relacion)

    _completar(s, resuelto, nodos, aristas, agregar, unir, del_corte)
    return list(nodos.values()), aristas


def _de_ejecucion(e: Ejecucion) -> Nodo:
    hashes = {}
    if e.firma_inputs:
        hashes["firma_inputs"] = e.firma_inputs
    if e.hash_politica:
        hashes["hash_politica"] = e.hash_politica
    return Nodo(
        clave=clave(e.tipo.value, e.run_id),
        tipo=e.tipo.value,
        run_id=e.run_id,
        declarado=True,
        estado=e.estado,
        resultado=e.resultado,
        version=e.version_motor,
        version_politica=e.version_politica,
        hashes=hashes,
        as_of=e.as_of,
        known_at=e.known_at,
        fecha_operacion=e.fecha_operacion,
        iniciada_en=e.iniciada_en,
        terminada_en=e.terminada_en,
    )


def _completar(s, resuelto, nodos, aristas, agregar, unir, del_corte) -> None:
    """Los detalles de lo que leyeron las declaradas (su version, sus hashes, su estado), y las
    entradas de analytics. Una consulta por tipo presente."""

    def detallar(tipo: str, consulta: str, armar) -> None:
        presentes = [n for n in nodos.values() if n.tipo == tipo and not n.declarado]
        if not presentes:
            return
        filas = s.execute(text(consulta), {"ids": [str(n.run_id) for n in presentes]}).mappings()
        for f in filas:
            armar(nodos[clave(tipo, f["run_id"])], f)

    def territorial(n: Nodo, f) -> None:
        n.estado, n.resultado, n.version = f["estado"], f["resultado"], f["version_motor"]
        n.version_politica = f["version_politica"]
        n.hashes = {"firma_inputs": f["firma_inputs"], "hash_politica": f["hash_politica"]}
        n.iniciada_en, n.terminada_en = f["iniciada_en"], f["terminada_en"]
        geografia = agregar(
            Nodo(clave("GEOGRAFIA", f["geografia"]), "GEOGRAFIA", f["geografia"], False)
        )
        unir(n.clave, geografia, RELACIONES[("TERRITORIAL_V2", "GEOGRAFIA")])

    detallar(
        "TERRITORIAL_V2",
        "SELECT t.territorial_run_id AS run_id, t.estado, t.resultado, t.version_motor, "
        "t.version_politica, t.hash_politica, t.firma_inputs, t.iniciada_en, t.terminada_en, "
        "g.geografia_run_id AS geografia FROM ejecucion_territorial_v2 t "
        "JOIN ejecucion_geografia g ON g.id = t.ejecucion_geografia_id "
        "WHERE t.territorial_run_id = ANY(CAST(:ids AS uuid[]))",
        territorial,
    )

    def geografia(n: Nodo, f) -> None:
        n.estado, n.resultado, n.version = f["estado"], f["resultado"], f["version_motor"]
        n.hashes = {"firma_inputs": f["firma_inputs"], "sha256_referencia": f["sha256"]}
        n.iniciada_en, n.terminada_en = f["iniciada_en"], f["terminada_en"]
        if f["corte_id"] == resuelto.corte.corte_id:
            unir(n.clave, del_corte, "es del corte")

    detallar(
        "GEOGRAFIA",
        "SELECT g.geografia_run_id AS run_id, g.estado, g.resultado, g.version_motor, "
        "g.firma_inputs, g.iniciada_en, g.terminada_en, k.corte_id, v.sha256 "
        "FROM ejecucion_geografia g JOIN corte_canonico k ON k.id = g.corte_canonico_id "
        "JOIN version_geografia v ON v.id = g.version_geografia_id "
        "WHERE g.geografia_run_id = ANY(CAST(:ids AS uuid[]))",
        geografia,
    )

    def modelo(n: Nodo, f) -> None:
        n.estado, n.version, n.version_politica = f["estado"], f["version_modelo"], f["politica"]
        n.hashes = {
            "hash_modelo": f["hash_modelo"],
            "hash_features": f["hash_features"],
            "hash_targets": f["hash_targets"],
            "dataset_hash": f["dataset_hash"],
            "hash_politica": f["hash_politica"],
        }
        n.iniciada_en, n.terminada_en = f["creada_en"], f["publicada_en"]
        entrenamiento = agregar(
            Nodo(
                clave=clave("ENTRENAMIENTO", f["training_run_id"]),
                tipo="ENTRENAMIENTO",
                run_id=f["training_run_id"],
                declarado=False,
                estado=f["estado_entrenamiento"],
                resultado=f["resultado_entrenamiento"],
                version=f["version_modelo"],
                hashes={
                    "firma_inputs": f["firma_entrenamiento"],
                    "hash_manifiesto": f["hash_manifiesto"],
                },
                iniciada_en=f["iniciado"],
                terminada_en=f["terminado"],
            )
        )
        unir(n.clave, entrenamiento, RELACIONES[("MODELO", "ENTRENAMIENTO")])

    detallar(
        "MODELO",
        "SELECT v.modelo_version_id AS run_id, v.estado, v.version_modelo, v.version_politica "
        "AS politica, v.hash_politica, v.hash_modelo, v.hash_features, v.hash_targets, "
        "v.dataset_hash, v.creada_en, v.publicada_en, t.training_run_id, t.estado AS "
        "estado_entrenamiento, t.resultado AS resultado_entrenamiento, t.firma_inputs AS "
        "firma_entrenamiento, t.hash_manifiesto, t.iniciada_en AS iniciado, t.terminada_en AS "
        "terminado FROM version_modelo_predictivo v JOIN ejecucion_entrenamiento_predictivo t "
        "ON t.id = v.ejecucion_entrenamiento_predictivo_id "
        "WHERE v.modelo_version_id = ANY(CAST(:ids AS uuid[]))",
        modelo,
    )

    def version(n: Nodo, f) -> None:
        n.estado, n.version = f["estado"], f["version"]
        n.hashes = {f["nombre_hash"]: f["hash"]}
        n.iniciada_en, n.terminada_en = f["creada_en"], f["publicada_en"]

    detallar(
        "CONFIGURACION_CAMPO",
        "SELECT configuracion_campo_version_id AS run_id, estado, version_configuracion AS "
        "version, 'hash_configuracion' AS nombre_hash, hash_configuracion AS hash, creada_en, "
        "publicada_en FROM version_configuracion_campo "
        "WHERE configuracion_campo_version_id = ANY(CAST(:ids AS uuid[]))",
        version,
    )
    detallar(
        "CONFIGURACION_OPTIMIZACION",
        "SELECT configuracion_id AS run_id, estado, version_configuracion AS version, "
        "'hash_contenido' AS nombre_hash, hash_contenido AS hash, creada_en, publicada_en "
        "FROM version_configuracion_optimizacion "
        "WHERE configuracion_id = ANY(CAST(:ids AS uuid[]))",
        version,
    )
    detallar(
        "RED_VIAL",
        "SELECT red_vial_version_id AS run_id, estado, motor_ruteo || ' ' || version_motor_ruteo "
        "AS version, 'huella' AS nombre_hash, huella AS hash, creada_en, publicada_en "
        "FROM version_red_vial WHERE red_vial_version_id = ANY(CAST(:ids AS uuid[]))",
        version,
    )

    analytics = resuelto.ejecucion(Tipo.ANALYTICS)
    if analytics is None:
        return
    propia = clave("ANALYTICS", analytics.run_id)
    for f in s.execute(
        text(
            "SELECT tipo_entrada, referencia_publica, version, firma, periodo_desde, "
            "periodo_hasta, disponible_en FROM entrada_analytics "
            "WHERE ejecucion_analytics_id = :e ORDER BY tipo_entrada, periodo_desde, "
            "referencia_publica"
        ),
        {"e": analytics.id},
    ).mappings():
        tipo = f["tipo_entrada"]
        entrada = agregar(
            Nodo(
                clave=clave(tipo, f["referencia_publica"]),
                tipo=tipo,
                run_id=f["referencia_publica"],
                declarado=False,
                version=f["version"],
                hashes={"firma": f["firma"]},
                as_of=f["periodo_desde"],
                fecha_operacion=f["periodo_hasta"],
                terminada_en=f["disponible_en"],
            )
        )
        unir(propia, entrada, "la leyo su manifiesto")
