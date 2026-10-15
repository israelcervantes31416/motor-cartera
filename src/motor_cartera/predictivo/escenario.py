"""Cargar un escenario predictivo semana por semana, por el pipeline normal, y anotar cuando quedo
conocida cada semana.

Un backtest point-in-time necesita que lo que se sabia de una fecha sea lo que de verdad se habia
registrado entonces. Si el escenario entero se cargara de una vez, todo tendria el mismo
`registrado_en` y la frontera de conocimiento no separaria nada. Aqui cada semana entra como
entraria en la operacion, despues de su corte: sus pagos (con su historia y la interpretacion de su
mes), sus eventos del lifecycle (los tardios del periodo anterior incluidos), el corte y la
evaluacion de las promesas a esa fecha. Al terminar la semana se anota el reloj de la base: el
`known_at` de ese corte. Una gestion que llega en el archivo de la semana siguiente queda despues de
el, como un evento tardio de verdad.

La bitacora (carga.json, junto al escenario) dice que se cargo en que base, que cartera, y el
`known_at` de cada corte; al final, el instante en que todo quedo cargado y la cobertura que el
escenario declara. `construir-manifiesto-predictivo --escenario` la lee. Si la carga se interrumpe,
volver a correrla sigue donde se quedo: cada paso reconoce lo que ya esta.

Nada de esto inventa labels: los labels salen despues, del motor de pagos, del lifecycle y de las
promesas, como cualquier otro dato.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import text
from sqlmodel import select

from motor_cartera.config import Config
from motor_cartera.config import config as config_del_entorno
from motor_cartera.db.modelos import (
    Corrida,
    CorteCanonico,
    EjecucionEvaluacionPromesas,
    EstadoCorrida,
    EstadoEvaluacionPromesas,
    EstadoIngestaPagos,
    IngestaPagos,
)
from motor_cartera.db.sesion import sesion
from motor_cartera.evaluacion import ejecuciones as evaluacion
from motor_cartera.evaluacion.ejecuciones import ejecutar_evaluacion
from motor_cartera.generador.predictivo import MANIFIESTO, VERSION_ESCENARIO
from motor_cartera.historia.ejecuciones import ejecutar_historia
from motor_cartera.ingesta.corridas import ingerir_archivo
from motor_cartera.ingesta.pagos import ingerir_pagos
from motor_cartera.lifecycle.importacion import importar
from motor_cartera.motor_pagos.ejecuciones import ejecutar_motor_pagos

log = logging.getLogger(__name__)

BITACORA = "carga.json"
VERSION_CARGA = "carga-predictiva/v1"
ESPERA_MAXIMA = 3_600.0
"""Cuantos segundos espera, a lo mas, a que termine lo de una semana."""


class EscenarioInvalido(Exception):
    """El directorio no es un escenario predictivo, o un archivo no es el que declara."""


class CargaFallida(Exception):
    """Un archivo del escenario no se publico, o la cola no termino lo de una semana."""


def _sha256(ruta: Path) -> str:
    digesto = hashlib.sha256()
    with ruta.open("rb") as archivo:
        while bloque := archivo.read(1 << 20):
            digesto.update(bloque)
    return digesto.hexdigest()


def leer_escenario(directorio: Path) -> dict:
    ruta = directorio / MANIFIESTO
    if not ruta.is_file():
        raise EscenarioInvalido(f"{directorio} no tiene {MANIFIESTO}.")
    escenario = json.loads(ruta.read_text(encoding="utf-8"))
    if escenario.get("version") != VERSION_ESCENARIO:
        raise EscenarioInvalido(
            f"{ruta} es de {escenario.get('version')!r}, no de {VERSION_ESCENARIO}."
        )
    return escenario


def reloj() -> datetime:
    """El reloj de la base, en su propia transaccion; despues se espera un poco, para que lo que se
    registre luego lleve una marca estrictamente posterior."""
    with sesion() as s:
        ahora = s.execute(text("SELECT now()")).scalar_one()
    time.sleep(0.05)
    return ahora


PENDIENTES = (
    (
        "historia",
        "SELECT h.id FROM ejecucion_historia h "
        "JOIN dataset_conformado d ON d.id = h.dataset_conformado_id "
        "LEFT JOIN corrida c ON c.id = d.corrida_id "
        "LEFT JOIN ingesta_pagos i ON i.id = d.ingesta_pagos_id "
        "WHERE h.estado = 'EN_PROCESO' AND coalesce(c.despacho_id, i.despacho_id) = :d "
        "AND coalesce(c.cartera_id, i.cartera_id) = :c ORDER BY h.id",
    ),
    (
        "motor_pagos",
        "SELECT id FROM ejecucion_motor_pagos WHERE estado = 'EN_PROCESO' AND despacho_id = :d "
        "AND cartera_id = :c ORDER BY id",
    ),
    (
        "evaluacion",
        "SELECT id FROM ejecucion_evaluacion_promesas WHERE estado = 'EN_PROCESO' "
        "AND despacho_id = :d AND cartera_id = :c ORDER BY id",
    ),
)
"""Lo que abre una semana de la cartera y sigue EN_PROCESO, en el orden en que se lee: la historia
publica los pagos observados que el motor de pagos interpreta, y la evaluacion lee las dos."""


def procesar_pendientes(despacho: str, cartera: str) -> int:
    """Ejecuta, en orden, cada historia, interpretacion de pagos y evaluacion de promesas de la
    cartera que siga EN_PROCESO, hasta que no quede ninguna. Llama a cada motor directamente, como
    lo haria su trabajo: si otro worker ya lo tiene (el del compose), el bloqueo de su ejecucion
    hace que este espere y la encuentre terminada. Solo toca lo de su cartera: lo que otra cartera
    deje en la cola no es de esta carga. Devuelve cuantas ejecuto."""
    motores = {
        "historia": ejecutar_historia,
        "motor_pagos": ejecutar_motor_pagos,
        "evaluacion": ejecutar_evaluacion,
    }
    ejecutadas = 0
    limite = time.monotonic() + ESPERA_MAXIMA
    while True:
        pendientes = []
        with sesion() as s:
            for nombre, consulta in PENDIENTES:
                ids = s.execute(text(consulta), {"d": despacho, "c": cartera}).scalars().all()
                pendientes += [(nombre, i) for i in ids]
        if not pendientes:
            return ejecutadas
        if time.monotonic() > limite:
            raise CargaFallida("Lo de la semana no termino a tiempo.")
        nombre, ejecucion_id = pendientes[0]
        motores[nombre](ejecucion_id)
        ejecutadas += 1


def _corrida_publicada(sha256: str) -> bool:
    with sesion() as s:
        return (
            s.exec(
                select(Corrida.id).where(
                    Corrida.firma == sha256, Corrida.estado == EstadoCorrida.EXITOSA
                )
            ).first()
            is not None
        )


def _pagos_aceptados(sha256: str) -> bool:
    with sesion() as s:
        return (
            s.exec(
                select(IngestaPagos.id).where(
                    IngestaPagos.firma == sha256, IngestaPagos.estado == EstadoIngestaPagos.EXITOSA
                )
            ).first()
            is not None
        )


def _evaluada(despacho: str, cartera: str, as_of: date) -> bool:
    with sesion() as s:
        return (
            s.exec(
                select(EjecucionEvaluacionPromesas.id).where(
                    EjecucionEvaluacionPromesas.despacho_id == despacho,
                    EjecucionEvaluacionPromesas.cartera_id == cartera,
                    EjecucionEvaluacionPromesas.as_of == as_of,
                    EjecucionEvaluacionPromesas.estado == EstadoEvaluacionPromesas.EXITOSA,
                )
            ).first()
            is not None
        )


def _corte_id(despacho: str, cartera: str, fecha: date) -> str:
    with sesion() as s:
        corte = s.exec(
            select(CorteCanonico).where(
                CorteCanonico.despacho_id == despacho,
                CorteCanonico.cartera_id == cartera,
                CorteCanonico.fecha_corte == fecha,
            )
        ).one_or_none()
    if corte is None:
        raise CargaFallida(f"El corte del {fecha} no tiene historia publicada.")
    return str(corte.corte_id)


def cargar(
    destino: str | Path,
    *,
    config: Config | None = None,
    progreso: Callable[[str], None] | None = None,
) -> dict:
    """Carga el escenario semana por semana y escribe la bitacora; devuelve la bitacora. La cartera
    es la de la configuracion (MC_DESPACHO_ID y MC_CARTERA_ID)."""
    config = config or config_del_entorno
    avisar = progreso or (lambda _: None)
    directorio = Path(destino)
    escenario = leer_escenario(directorio)
    despacho, cartera, zona = config.despacho_id, config.cartera_id, config.zona_horaria_fuente
    ruta_bitacora = directorio / BITACORA
    if ruta_bitacora.is_file():
        bitacora = json.loads(ruta_bitacora.read_text(encoding="utf-8"))
        if (bitacora["despacho_id"], bitacora["cartera_id"]) != (despacho, cartera):
            raise EscenarioInvalido(
                f"La bitacora es de {bitacora['despacho_id']}/{bitacora['cartera_id']}, no de "
                f"{despacho}/{cartera}."
            )
    else:
        bitacora = {
            "version": VERSION_CARGA,
            "escenario": {
                "version": escenario["version"],
                "semilla": escenario["semilla"],
                "sha256_manifiesto": _sha256(directorio / MANIFIESTO),
            },
            "despacho_id": despacho,
            "cartera_id": cartera,
            "zona_horaria": zona,
            "cobertura": escenario["cobertura"],
            "cortes": [],
            "label_known_at": None,
        }
    hechos = {c["fecha_corte"] for c in bitacora["cortes"]}
    periodo_que_termina = {p["hasta"]: p for p in escenario["periodos"]}
    for corte in escenario["cortes"]:
        fecha = corte["fecha_corte"]
        if fecha in hechos:
            continue
        tiempos: dict[str, float] = {}
        periodo = periodo_que_termina.get(fecha)
        if periodo is not None:
            ruta = directorio / periodo["archivo"]
            _verificar(ruta, periodo["sha256"])
            inicio = time.perf_counter()
            if not _pagos_aceptados(periodo["sha256"]):
                ingesta = ingerir_pagos(ruta)
                if ingesta.estado != EstadoIngestaPagos.EXITOSA:
                    raise CargaFallida(f"{ruta.name}: {ingesta.estado} ({ingesta.detalle})")
            procesar_pendientes(despacho, cartera)
            tiempos["pagos"] = round(time.perf_counter() - inicio, 3)
            ruta = directorio / periodo["lifecycle"]["archivo"]
            _verificar(ruta, periodo["lifecycle"]["sha256"])
            inicio = time.perf_counter()
            reporte = importar(ruta, despacho_id=despacho, cartera_id=cartera, zona=zona)
            if reporte.total_de_problemas:
                primero = reporte.problemas[0]
                raise CargaFallida(
                    f"{ruta.name}: {reporte.total_de_problemas} problemas, el primero en la linea "
                    f"{primero.linea}: {primero.codigo}."
                )
            tiempos["lifecycle"] = round(time.perf_counter() - inicio, 3)
        ruta = directorio / corte["archivo"]
        _verificar(ruta, corte["sha256"])
        inicio = time.perf_counter()
        if not _corrida_publicada(corte["sha256"]):
            corrida = ingerir_archivo(
                ruta, contrato="cartera/v2", fecha_corte=date.fromisoformat(fecha)
            )
            if corrida.estado != EstadoCorrida.EXITOSA:
                raise CargaFallida(f"{ruta.name}: {corrida.estado} ({corrida.detalle})")
        procesar_pendientes(despacho, cartera)
        tiempos["corte"] = round(time.perf_counter() - inicio, 3)
        inicio = time.perf_counter()
        if not _evaluada(despacho, cartera, date.fromisoformat(fecha)):
            with sesion() as s:
                evaluacion.abrir(
                    s,
                    despacho,
                    cartera,
                    date.fromisoformat(fecha),
                    zona=zona,
                    max_intentos=config.worker_max_intentos,
                    reusar=True,
                )
                s.commit()
        procesar_pendientes(despacho, cartera)
        tiempos["evaluacion"] = round(time.perf_counter() - inicio, 3)
        conocido = reloj()
        bitacora["cortes"].append(
            {
                "fecha_corte": fecha,
                "corte_id": _corte_id(despacho, cartera, date.fromisoformat(fecha)),
                "known_at": conocido.isoformat(),
                "segundos": tiempos,
            }
        )
        _escribir(ruta_bitacora, bitacora)
        avisar(
            f"{fecha}: conocido a {conocido.isoformat()} "
            + ", ".join(f"{k} {v} s" for k, v in tiempos.items())
        )
    if bitacora["label_known_at"] is None or len(bitacora["cortes"]) != len(hechos):
        bitacora["label_known_at"] = reloj().isoformat()
        _escribir(ruta_bitacora, bitacora)
    return bitacora


def _verificar(ruta: Path, sha256: str) -> None:
    if not ruta.is_file():
        raise EscenarioInvalido(f"Falta {ruta.name}.")
    if _sha256(ruta) != sha256:
        raise EscenarioInvalido(f"{ruta.name} no es el que declara el manifiesto (SHA-256).")


def _escribir(ruta: Path, bitacora: dict) -> None:
    temporal = ruta.with_suffix(".json.tmp")
    temporal.write_text(json.dumps(bitacora, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporal.replace(ruta)


def leer_bitacora(directorio: str | Path) -> dict:
    ruta = Path(directorio) / BITACORA
    if not ruta.is_file():
        raise EscenarioInvalido(f"{directorio} no tiene {BITACORA}: carga el escenario primero.")
    return json.loads(ruta.read_text(encoding="utf-8"))
