"""El flujo automatico contra PostgreSQL: de la ingesta al ruteo, como se detiene y como se
reanuda.

Los trabajos los procesa el worker real, uno por uno, con procesar_un_trabajo: ninguna prueba llama
a un motor directamente para avanzar un flujo. Las etapas pedidas a mano se prueban sobre corridas
publicadas en modo directo, que no tienen flujo.
"""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func
from sqlmodel import select

from motor_cartera.config import Config
from motor_cartera.db.modelos import (
    ArtefactoFuente,
    Corrida,
    EjecucionDecision,
    EjecucionRuteo,
    EjecucionTerritorial,
    EstadoCorrida,
    EstadoDecision,
    EstadoFlujo,
    EstadoTrabajo,
    EtapaFlujo,
    FlujoOrquestacion,
    TipoTrabajo,
    TrabajoOrquestacion,
)
from motor_cartera.db.sesion import sesion
from motor_cartera.decision import ejecuciones as decision
from motor_cartera.generador.sintetico import generar_archivo
from motor_cartera.ingesta.corridas import ArchivoDuplicado, ingerir_archivo
from motor_cartera.orquestacion import cola, flujo
from motor_cartera.orquestacion.flujo import (
    FlujoDetenido,
    FlujoEnProceso,
    FlujoNoEncontrado,
    FlujoNoReanudable,
    FlujoYaCompletado,
    crear_flujo_ingesta,
    encolar_decision,
    encolar_ruteo,
    encolar_territorial,
    reanudar_flujo,
)
from motor_cartera.orquestacion.worker import identificador_worker, procesar_un_trabajo
from motor_cartera.ruteo import ejecuciones as ruteo
from motor_cartera.territorial import ejecuciones as territorial

en_la_base = pytest.mark.usefixtures("bd")

CORTE = date(2026, 9, 30)
CONFIG = Config()

MALFORMADO = b"cliente,saldo\nCU00000001,10.00\n"

# Para hacer fallar cada etapa: el modulo de su servicio, la funcion del nucleo que se rompe, y
# cuantos trabajos hay que procesar para llegar a ella.
FALLAS = {
    EtapaFlujo.DECISION: (decision, "decidir_cuenta", 2),
    EtapaFlujo.TERRITORIAL: (territorial, "priorizar_territorios", 3),
    EtapaFlujo.RUTEO: (ruteo, "rutear_territorio", 4),
}

MODELOS = {
    EtapaFlujo.DECISION: EjecucionDecision,
    EtapaFlujo.TERRITORIAL: EjecucionTerritorial,
    EtapaFlujo.RUTEO: EjecucionRuteo,
}


def _contenido(tmp_path: Path, *, n: int = 200, tasa: float = 0.0, nombre="c.csv") -> bytes:
    ruta = generar_archivo(
        tmp_path / nombre, n=n, tasa_invalidas=tasa, semilla=1, fecha_corte=CORTE
    )
    return ruta.read_bytes()


def _crear(contenido: bytes, config: Config = CONFIG) -> tuple[int, UUID]:
    """Una corrida con su flujo, como la crea la API. Devuelve el id de la corrida y el flujo_id."""
    with sesion() as s:
        corrida, nuevo = crear_flujo_ingesta(
            s, origen="cartera.csv", contenido=contenido, tolerancia=0.05, config=config
        )
        return corrida.id, nuevo.flujo_id


def _flujo(flujo_id: UUID) -> FlujoOrquestacion:
    with sesion() as s:
        return s.exec(select(FlujoOrquestacion).where(FlujoOrquestacion.flujo_id == flujo_id)).one()


def _trabajos() -> list[TrabajoOrquestacion]:
    with sesion() as s:
        return list(s.exec(select(TrabajoOrquestacion).order_by(TrabajoOrquestacion.id)).all())


def _cuantos(modelo) -> int:
    with sesion() as s:
        return s.exec(select(func.count()).select_from(modelo)).one()


def _procesar(veces: int) -> list:
    """Procesa `veces` trabajos, uno por uno, con el worker real."""
    worker_id = identificador_worker()
    return [procesar_un_trabajo(worker_id, CONFIG) for _ in range(veces)]


def _revienta(*_):
    raise RuntimeError("falla simulada del nucleo")


def _detenido_en(etapa: EtapaFlujo, tmp_path: Path, monkeypatch) -> UUID:
    """Un flujo detenido porque la ejecucion de `etapa` termino FALLIDA."""
    modulo, nucleo, trabajos = FALLAS[etapa]
    _, flujo_id = _crear(_contenido(tmp_path))
    with monkeypatch.context() as parche:
        parche.setattr(modulo, nucleo, _revienta)
        _procesar(trabajos)
    detenido = _flujo(flujo_id)
    assert (detenido.estado, detenido.etapa) == (EstadoFlujo.DETENIDO, etapa)
    return flujo_id


def _publicada(tmp_path: Path) -> int:
    """Una corrida EXITOSA publicada en modo directo, sin flujo, como la deja el CLI de v0.4."""
    ruta = generar_archivo(tmp_path / "directa.csv", n=200, semilla=1, fecha_corte=CORTE)
    corrida = ingerir_archivo(ruta)
    assert corrida.estado == EstadoCorrida.EXITOSA
    return corrida.id


# --- nacer: la corrida, su archivo, su flujo y su trabajo, juntos --------------------------------


@en_la_base
def test_una_corrida_nace_con_su_artefacto_su_flujo_y_el_trabajo_de_su_ingesta(tmp_path, almacen):
    contenido = _contenido(tmp_path)

    corrida_id, flujo_id = _crear(contenido, Config(worker_max_intentos=7))

    with sesion() as s:
        corrida = s.get_one(Corrida, corrida_id)
        artefacto = s.get_one(ArtefactoFuente, corrida.artefacto_fuente_id)
    nuevo = _flujo(flujo_id)
    (trabajo,) = _trabajos()
    assert corrida.estado == EstadoCorrida.EN_PROCESO
    # El archivo tal como llego, en el almacen, con su firma y su tamano.
    sha256 = hashlib.sha256(contenido).hexdigest()
    assert (artefacto.sha256, artefacto.tamano_bytes) == (sha256, len(contenido))
    with almacen.abrir(sha256) as objeto:
        assert objeto.read() == contenido
    assert (nuevo.corrida_id, nuevo.estado, nuevo.etapa) == (
        corrida_id,
        EstadoFlujo.EN_PROCESO,
        EtapaFlujo.INGESTA,
    )
    assert (
        nuevo.ejecucion_decision_id,
        nuevo.ejecucion_territorial_id,
        nuevo.ejecucion_ruteo_id,
        nuevo.terminado_en,
    ) == (None, None, None, None)
    assert nuevo.detalle == "La ingesta esta en la cola."
    # El trabajo es del flujo y conserva la politica de la configuracion con que nacio.
    assert (trabajo.tipo, trabajo.estado, trabajo.corrida_id, trabajo.flujo_id) == (
        TipoTrabajo.INGESTA,
        EstadoTrabajo.PENDIENTE,
        corrida_id,
        nuevo.id,
    )
    assert (trabajo.intentos, trabajo.max_intentos) == (0, 7)


@en_la_base
def test_si_algo_falla_al_registrar_no_queda_una_corrida_sin_su_flujo(tmp_path, monkeypatch):
    # La corrida, el archivo y el flujo ya se enviaron cuando falla el trabajo: nada se confirma.
    def revienta(*_, **__):
        raise RuntimeError("falla simulada al encolar")

    monkeypatch.setattr(cola, "crear", revienta)

    with pytest.raises(RuntimeError, match="falla simulada"):
        _crear(_contenido(tmp_path))

    modelos = (Corrida, ArtefactoFuente, FlujoOrquestacion, TrabajoOrquestacion)
    assert [_cuantos(modelo) for modelo in modelos] == [0, 0, 0, 0]


@en_la_base
def test_el_mismo_archivo_otra_vez_no_registra_nada(tmp_path):
    contenido = _contenido(tmp_path)
    primera, _ = _crear(contenido)

    with pytest.raises(ArchivoDuplicado, match="se esta procesando") as exc:
        _crear(contenido)

    assert exc.value.previa.id == primera
    modelos = (Corrida, ArtefactoFuente, FlujoOrquestacion, TrabajoOrquestacion)
    assert [_cuantos(modelo) for modelo in modelos] == [1, 1, 1, 1]


# --- avanzar: cada etapa EXITOSA abre la siguiente ------------------------------------------------


@en_la_base
def test_cada_etapa_exitosa_encadena_la_siguiente_hasta_completar(tmp_path):
    corrida_id, flujo_id = _crear(_contenido(tmp_path))
    pasos = [
        (TipoTrabajo.INGESTA, EtapaFlujo.DECISION),
        (TipoTrabajo.DECISION, EtapaFlujo.TERRITORIAL),
        (TipoTrabajo.TERRITORIAL, EtapaFlujo.RUTEO),
        (TipoTrabajo.RUTEO, EtapaFlujo.COMPLETADA),
    ]

    for tipo, etapa in pasos:
        (procesado,) = _procesar(1)

        assert (procesado.tipo, procesado.estado) == (tipo, EstadoTrabajo.COMPLETADO)
        actual = _flujo(flujo_id)
        assert actual.etapa == etapa
        if etapa == EtapaFlujo.COMPLETADA:
            break
        # La ejecucion de la etapa nueva ya existe EN_PROCESO, y su trabajo ya esta en la cola,
        # con el flujo: se confirmaron en la misma transaccion que cerro el trabajo anterior.
        assert actual.estado == EstadoFlujo.EN_PROCESO
        ejecucion_id = getattr(actual, flujo.PUNTERO[etapa])
        with sesion() as s:
            assert s.get_one(MODELOS[etapa], ejecucion_id).estado == "EN_PROCESO"
        siguiente = _trabajos()[-1]
        assert (siguiente.tipo, siguiente.estado, siguiente.flujo_id) == (
            TipoTrabajo(etapa.value),
            EstadoTrabajo.PENDIENTE,
            actual.id,
        )
        assert flujo.objetivo_de(siguiente) == ejecucion_id
        assert actual.detalle == flujo.EN_COLA[etapa]

    completado = _flujo(flujo_id)
    assert (completado.estado, completado.etapa) == (EstadoFlujo.COMPLETADO, EtapaFlujo.COMPLETADA)
    assert completado.terminado_en is not None and completado.detalle == flujo.COMPLETADO
    assert _procesar(1) == [None]  # la cola quedo vacia
    with sesion() as s:  # el artefacto sigue ahi: es la evidencia de lo que se recibio
        assert s.get_one(Corrida, corrida_id).artefacto_fuente_id is not None
    assert _cuantos(ArtefactoFuente) == 1


@en_la_base
@pytest.mark.parametrize(
    ("archivo", "estado"),
    [
        pytest.param("rechazada", "RECHAZADA", id="RECHAZADA"),
        pytest.param("malformado", "FALLIDA", id="FALLIDA"),
    ],
)
def test_una_ingesta_que_no_publica_detiene_el_flujo_y_conserva_su_artefacto(
    tmp_path, archivo, estado, almacen
):
    contenido = _contenido(tmp_path, n=100, tasa=0.5) if archivo == "rechazada" else MALFORMADO
    corrida_id, flujo_id = _crear(contenido)

    (procesado,) = _procesar(1)

    # El trabajo cumplio: ejecuto la ingesta, y la ingesta termino.
    assert procesado.estado == EstadoTrabajo.COMPLETADO
    detenido = _flujo(flujo_id)
    assert (detenido.estado, detenido.etapa) == (EstadoFlujo.DETENIDO, EtapaFlujo.INGESTA)
    assert detenido.detalle == (
        f"La corrida termino {estado}. Para reintentar, vuelve a subir el archivo."
    )
    assert detenido.terminado_en is not None
    with sesion() as s:
        corrida = s.get_one(Corrida, corrida_id)
        artefacto = s.get_one(ArtefactoFuente, corrida.artefacto_fuente_id)
    assert corrida.estado == estado
    almacen.verificar(artefacto.sha256, artefacto.tamano_bytes)
    # No se abrio ninguna decision, y la cola quedo vacia.
    assert _cuantos(EjecucionDecision) == 0
    assert _procesar(1) == [None]


@en_la_base
@pytest.mark.parametrize("etapa", list(FALLAS), ids=lambda etapa: etapa.value)
def test_una_etapa_que_termina_fallida_completa_su_trabajo_y_detiene_el_flujo(
    tmp_path, monkeypatch, etapa
):
    flujo_id = _detenido_en(etapa, tmp_path, monkeypatch)

    detenido = _flujo(flujo_id)
    # La ejecucion de la etapa termino FALLIDA; su trabajo, COMPLETADO: lo ejecuto y termino.
    ultimo = _trabajos()[-1]
    assert (ultimo.tipo, ultimo.estado, ultimo.intentos) == (
        TipoTrabajo(etapa.value),
        EstadoTrabajo.COMPLETADO,
        1,
    )
    with sesion() as s:
        fallida = s.get_one(MODELOS[etapa], getattr(detenido, flujo.PUNTERO[etapa]))
    assert fallida.estado == "FALLIDA"
    que = {
        EtapaFlujo.DECISION: "La decision",
        EtapaFlujo.TERRITORIAL: "La organizacion territorial",
        EtapaFlujo.RUTEO: "El ruteo",
    }[etapa]
    assert detenido.detalle == f"{que} termino FALLIDA. Para reintentarla, reanuda el flujo."
    assert _procesar(1) == [None]


@en_la_base
def test_si_la_etapa_siguiente_no_se_puede_abrir_el_flujo_se_detiene_en_la_que_termino(
    tmp_path, monkeypatch
):
    _, flujo_id = _crear(_contenido(tmp_path))

    def no_organiza(s, fuente, *, confirmar=True):
        raise territorial.DecisionNoTerritorializable(fuente, "Razon simulada para no organizar.")

    monkeypatch.setattr(territorial, "abrir_ejecucion", no_organiza)
    _procesar(2)

    detenido = _flujo(flujo_id)
    assert (detenido.estado, detenido.etapa) == (EstadoFlujo.DETENIDO, EtapaFlujo.DECISION)
    assert detenido.detalle == (
        "La decision termino EXITOSA, pero la etapa TERRITORIAL no se pudo abrir: Razon simulada "
        "para no organizar."
    )
    assert _cuantos(EjecucionTerritorial) == 0
    # Y no hay una ejecucion fallida que reintentar: no se reanuda.
    with sesion() as s, pytest.raises(FlujoNoReanudable, match="termino EXITOSA"):
        reanudar_flujo(s, flujo_id, config=CONFIG)


@en_la_base
def test_un_trabajo_que_no_es_el_de_la_etapa_vigente_no_mueve_el_flujo(tmp_path, caplog):
    _, flujo_id = _crear(_contenido(tmp_path))
    antes = _flujo(flujo_id).model_dump()

    with sesion() as s:
        flujo.avanzar(s, antes["id"], TipoTrabajo.DECISION, 999_999, max_intentos=5)
        flujo.detener(s, antes["id"], "Detalle de prueba.")
        s.commit()

    detenido = _flujo(flujo_id).model_dump()
    assert any("no es el de su etapa vigente" in r.getMessage() for r in caplog.records)
    # detener si lo detiene; y un flujo que ya no esta EN_PROCESO ya no se detiene otra vez.
    assert (detenido["estado"], detenido["detalle"]) == (EstadoFlujo.DETENIDO, "Detalle de prueba.")
    with sesion() as s:
        flujo.detener(s, antes["id"], "Otro detalle.")
        flujo.avanzar(s, antes["id"], TipoTrabajo.INGESTA, antes["corrida_id"], max_intentos=5)
        s.commit()
    assert _flujo(flujo_id).model_dump() == detenido


# --- reanudar ------------------------------------------------------------------------------------


@en_la_base
@pytest.mark.parametrize("etapa", list(FALLAS), ids=lambda etapa: etapa.value)
def test_reanudar_reintenta_la_etapa_con_otra_ejecucion_y_otro_trabajo(
    tmp_path, monkeypatch, etapa, trabajar
):
    flujo_id = _detenido_en(etapa, tmp_path, monkeypatch)
    detenido = _flujo(flujo_id)
    fallida_id = getattr(detenido, flujo.PUNTERO[etapa])
    trabajos_antes = _trabajos()

    with sesion() as s:
        reanudado = reanudar_flujo(s, flujo_id, config=CONFIG)
        nueva_id = getattr(reanudado, flujo.PUNTERO[etapa])
        assert (reanudado.estado, reanudado.etapa, reanudado.terminado_en) == (
            EstadoFlujo.EN_PROCESO,
            etapa,
            None,
        )

    # Otra ejecucion de la misma etapa, EN_PROCESO, con su trabajo del flujo; la fallida queda en el
    # historial, con su trabajo COMPLETADO.
    assert nueva_id != fallida_id
    with sesion() as s:
        assert s.get_one(MODELOS[etapa], fallida_id).estado == "FALLIDA"
        assert s.get_one(MODELOS[etapa], nueva_id).estado == "EN_PROCESO"
    nuevo_trabajo = _trabajos()[-1]
    assert len(_trabajos()) == len(trabajos_antes) + 1
    assert (nuevo_trabajo.estado, nuevo_trabajo.flujo_id, nuevo_trabajo.intentos) == (
        EstadoTrabajo.PENDIENTE,
        detenido.id,
        0,
    )
    assert _flujo(flujo_id).detalle == (
        f"Se reanudo en la etapa {etapa} con otra ejecucion; la que fallo queda en el historial."
    )

    # Con el nucleo de vuelta, el flujo llega hasta el final.
    trabajar()
    completado = _flujo(flujo_id)
    assert (completado.estado, completado.etapa) == (EstadoFlujo.COMPLETADO, EtapaFlujo.COMPLETADA)
    assert getattr(completado, flujo.PUNTERO[etapa]) == nueva_id


@en_la_base
def test_un_flujo_detenido_en_la_ingesta_no_se_reanuda(tmp_path):
    corrida_id, flujo_id = _crear(MALFORMADO)
    _procesar(1)
    with sesion() as s:
        antes = s.get_one(Corrida, corrida_id).model_dump()

    with sesion() as s, pytest.raises(FlujoNoReanudable, match="Vuelve a subir el archivo") as exc:
        reanudar_flujo(s, flujo_id, config=CONFIG)

    # La corrida terminada es evidencia: no se reabre, y no se crea otra.
    assert (exc.value.flujo_id, exc.value.etapa) == (flujo_id, EtapaFlujo.INGESTA)
    with sesion() as s:
        assert s.get_one(Corrida, corrida_id).model_dump() == antes
    assert _cuantos(Corrida) == 1 and len(_trabajos()) == 1
    assert _flujo(flujo_id).estado == EstadoFlujo.DETENIDO


@en_la_base
def test_un_flujo_en_proceso_completado_o_que_no_existe_no_se_reanuda(tmp_path, trabajar):
    _, completado = _crear(_contenido(tmp_path))
    trabajar()
    _, en_proceso = _crear(_contenido(tmp_path, nombre="otra.csv", n=150))

    with sesion() as s, pytest.raises(FlujoEnProceso, match="sigue EN_PROCESO"):
        reanudar_flujo(s, en_proceso, config=CONFIG)
    with sesion() as s, pytest.raises(FlujoYaCompletado, match="ya esta COMPLETADO"):
        reanudar_flujo(s, completado, config=CONFIG)
    no_existe = uuid4()
    with sesion() as s, pytest.raises(FlujoNoEncontrado) as exc:
        reanudar_flujo(s, no_existe, config=CONFIG)
    assert exc.value.flujo_id == no_existe


@en_la_base
def test_si_la_etapa_no_se_puede_volver_a_abrir_no_se_reanuda(tmp_path, monkeypatch):
    flujo_id = _detenido_en(EtapaFlujo.DECISION, tmp_path, monkeypatch)
    corrida_id = _flujo(flujo_id).corrida_id
    # Alguien abrio, por fuera del flujo, otra decision de la misma corrida que sigue EN_PROCESO.
    with sesion() as s:
        s.add(
            EjecucionDecision(
                corrida_id=corrida_id, version_reglas=decision.VERSION_REGLAS_DECISION
            )
        )
        s.commit()

    with sesion() as s, pytest.raises(FlujoNoReanudable, match="ya se esta decidiendo"):
        reanudar_flujo(s, flujo_id, config=CONFIG)

    assert _flujo(flujo_id).estado == EstadoFlujo.DETENIDO


# --- etapas pedidas a mano ----------------------------------------------------------------------


@en_la_base
def test_cada_etapa_pedida_a_mano_es_su_ejecucion_y_su_trabajo_sin_flujo(tmp_path, trabajar):
    corrida_id = _publicada(tmp_path)

    with sesion() as s:
        decidida = encolar_decision(s, corrida_id, config=CONFIG)
    assert decidida.estado == EstadoDecision.EN_PROCESO
    (trabajo,) = _trabajos()
    assert (trabajo.tipo, trabajo.ejecucion_decision_id, trabajo.flujo_id) == (
        TipoTrabajo.DECISION,
        decidida.id,
        None,
    )
    trabajar()
    with sesion() as s:
        organizada = encolar_territorial(s, decidida.id, config=CONFIG)
    trabajar()
    with sesion() as s:
        ruteada = encolar_ruteo(s, organizada.id, config=CONFIG)
    trabajar()

    with sesion() as s:
        estados = [
            s.get_one(modelo, ejecucion.id).estado
            for modelo, ejecucion in (
                (EjecucionDecision, decidida),
                (EjecucionTerritorial, organizada),
                (EjecucionRuteo, ruteada),
            )
        ]
    assert estados == ["EXITOSA", "EXITOSA", "EXITOSA"]
    # Ninguna encadeno nada: sin flujo, cada trabajo se queda en su etapa.
    assert _cuantos(FlujoOrquestacion) == 0
    assert [(t.tipo, t.estado, t.flujo_id) for t in _trabajos()] == [
        (TipoTrabajo.DECISION, EstadoTrabajo.COMPLETADO, None),
        (TipoTrabajo.TERRITORIAL, EstadoTrabajo.COMPLETADO, None),
        (TipoTrabajo.RUTEO, EstadoTrabajo.COMPLETADO, None),
    ]


@en_la_base
def test_una_etapa_pedida_a_mano_no_queda_sin_su_trabajo(tmp_path, monkeypatch):
    corrida_id = _publicada(tmp_path)

    def revienta(*_, **__):
        raise RuntimeError("falla simulada al encolar")

    monkeypatch.setattr(cola, "crear", revienta)

    with sesion() as s, pytest.raises(RuntimeError, match="falla simulada"):
        encolar_decision(s, corrida_id, config=CONFIG)

    assert _cuantos(EjecucionDecision) == 0


@en_la_base
def test_una_etapa_pedida_a_mano_mientras_otra_sigue_activa_no_se_abre(tmp_path):
    corrida_id = _publicada(tmp_path)
    with sesion() as s:
        activa = encolar_decision(s, corrida_id, config=CONFIG)

    with sesion() as s, pytest.raises(decision.DecisionEnProceso) as exc:
        encolar_decision(s, corrida_id, config=CONFIG)

    assert exc.value.activa.id == activa.id
    assert _cuantos(EjecucionDecision) == len(_trabajos()) == 1


@en_la_base
def test_una_etapa_que_el_flujo_va_a_correr_no_se_pide_a_mano(tmp_path):
    corrida_id, flujo_id = _crear(_contenido(tmp_path))

    # En la ingesta, el flujo todavia va a decidir la corrida.
    with sesion() as s, pytest.raises(FlujoEnProceso, match="va a correr la etapa DECISION"):
        encolar_decision(s, corrida_id, config=CONFIG)
    _procesar(1)
    decision_id = _flujo(flujo_id).ejecucion_decision_id
    # En DECISION, va a decidir y despues a organizar.
    for encolar, fuente in ((encolar_decision, corrida_id), (encolar_territorial, decision_id)):
        with sesion() as s, pytest.raises(FlujoEnProceso) as exc:
            encolar(s, fuente, config=CONFIG)
        assert (exc.value.flujo_id, exc.value.etapa) == (flujo_id, EtapaFlujo.DECISION)
    # Y nada se abrio a mano.
    assert _cuantos(EjecucionDecision) == 1 and len(_trabajos()) == 2


@en_la_base
def test_una_etapa_en_que_el_flujo_se_detuvo_se_reanuda_y_no_se_pide_a_mano(tmp_path, monkeypatch):
    flujo_id = _detenido_en(EtapaFlujo.DECISION, tmp_path, monkeypatch)

    with sesion() as s, pytest.raises(FlujoDetenido, match="reanudando el flujo"):
        encolar_decision(s, _flujo(flujo_id).corrida_id, config=CONFIG)

    assert _cuantos(EjecucionDecision) == 1


@en_la_base
def test_una_etapa_que_el_flujo_ya_publico_da_su_conflicto_de_siempre(tmp_path, trabajar):
    _, flujo_id = _crear(_contenido(tmp_path))
    trabajar()
    completado = _flujo(flujo_id)

    for encolar, fuente, conflicto in (
        (encolar_decision, completado.corrida_id, decision.DecisionYaGenerada),
        (encolar_territorial, completado.ejecucion_decision_id, territorial.TerritorialYaGenerado),
        (encolar_ruteo, completado.ejecucion_territorial_id, ruteo.RuteoYaGenerado),
    ):
        with sesion() as s, pytest.raises(conflicto):
            encolar(s, fuente, config=CONFIG)

    assert len(_trabajos()) == 4
