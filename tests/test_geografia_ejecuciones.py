"""geografia/v1 en PostgreSQL: abrir con sus entradas fijas, la idempotencia, lo que no se puede
abrir, la publicacion todo o nada con fallas inyectadas, varios workers, el lease perdido, dos
cortes a la vez, el orden longitud-latitud, la semantica del borde, el versionado de la referencia y
la reconstruccion."""

from __future__ import annotations

import stat
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, timedelta
from uuid import uuid4

import pytest
from geografia_escenarios import (
    AMOZOC,
    MERIDA,
    PUEBLA,
    A,
    CuentaGeo,
    abrir_geo,
    corte_de,
    ejecucion,
    espejo,
    geolocalizar,
    importar_referencia,
    ingerir_corte_geo,
    inyectar,
    publicado,
    resultados,
)
from sqlalchemy import func, text, update
from sqlalchemy.exc import DBAPIError
from sqlmodel import select

from motor_cartera.config import Config
from motor_cartera.db.modelos import (
    ArtefactoFuente,
    CorteCanonico,
    DatasetConformado,
    EjecucionGeografia,
    EstadoTrabajo,
    ResultadoGeografiaCuenta,
    TipoTrabajo,
    TrabajoOrquestacion,
)
from motor_cartera.db.sesion import sesion
from motor_cartera.fuentes.almacen import ArtefactoFaltante
from motor_cartera.geografia import ejecuciones as motor
from motor_cartera.geografia import reglas
from motor_cartera.geografia.ejecuciones import (
    CorteNoEncontrado,
    GeografiaEnProceso,
    GeografiaYaGenerada,
    VersionGeografiaNoEncontrada,
    VersionGeografiaNoPublicada,
)
from motor_cartera.geografia.referencia import ImportacionFallida
from motor_cartera.geografia.sintetica import rectangulo
from motor_cartera.orquestacion import cola, objetivos, worker
from motor_cartera.orquestacion.worker import geolocalizar_en_primer_plano

pytestmark = pytest.mark.usefixtures("bd")

ESPERA = 60
CORTE = date(2026, 9, 30)
OTRO_CORTE = date(2026, 10, 7)
CUENTAS = [
    CuentaGeo(1, PUEBLA, "19.05", "-98.2"),
    CuentaGeo(2, AMOZOC, "19.05", "-98.0"),
    CuentaGeo(3, PUEBLA),
    CuentaGeo(4, AMOZOC, "19.05", "-98.2"),
]


def pequeno(tmp_path, cuentas=CUENTAS, corte=CORTE):
    version = importar_referencia(tmp_path)
    ingerir_corte_geo(tmp_path, corte, cuentas)
    return version


def _cuantos(modelo, *condiciones) -> int:
    with sesion() as s:
        return s.exec(select(func.count()).select_from(modelo).where(*condiciones)).one()


def _trabajo_de(ejecucion_id: int) -> TrabajoOrquestacion:
    with sesion() as s:
        return s.exec(
            select(TrabajoOrquestacion).where(
                TrabajoOrquestacion.ejecucion_geografia_id == ejecucion_id
            )
        ).one()


def _sin_temporales() -> bool:
    with sesion() as s:
        return (
            s.execute(
                text(
                    "SELECT count(*) FROM pg_class WHERE relname LIKE 'geo\\_%' "
                    "AND relpersistence = 't'"
                )
            ).scalar_one()
            == 0
        )


def _foto(ejecucion_id: int) -> list[tuple]:
    """Lo que publico una ejecucion, sin sus identificadores internos de ejecucion."""
    with sesion() as s:
        return [
            tuple(f)
            for f in s.execute(
                text(
                    "SELECT c.cliente_unico, r.source_row, r.source_sheet, r.latitud_observada, "
                    "r.longitud_observada, ST_AsText(r.punto), m.cvegeo, r.estado_geografia, "
                    "r.municipio_fuente_resuelto, r.coincide_municipio, r.motivos::text "
                    "FROM resultado_geografia_cuenta r JOIN cuenta_canonica c "
                    "ON c.id = r.cuenta_canonica_id LEFT JOIN municipio_geografico m "
                    "ON m.id = r.municipio_geografico_id WHERE r.ejecucion_geografia_id = :e "
                    "ORDER BY c.cliente_unico"
                ),
                {"e": ejecucion_id},
            ).all()
        ]


# --- abrir ----------------------------------------------------------------------------------------


def test_abrir_fija_sus_entradas_y_encola_su_trabajo(tmp_path):
    version = pequeno(tmp_path)
    corte = corte_de(CORTE)

    ejecucion_id = abrir_geo(CORTE, version)

    e = ejecucion(ejecucion_id)
    with sesion() as s:
        dataset = s.get_one(DatasetConformado, corte.dataset_conformado_id)
    assert (e.estado, e.resultado, e.version_motor, e.cuentas_objetivo) == (
        "EN_PROCESO",
        None,
        "geografia/v1",
        4,
    )
    assert (e.corte_canonico_id, e.dataset_conformado_id) == (corte.id, dataset.id)
    assert e.firma_inputs == reglas.firma_inputs(
        geografia_version_id=version.geografia_version_id,
        sha256_referencia=version.sha256,
        capa=version.capa,
        corte_id=corte.corte_id,
        firma_corte=corte.firma_contenido,
        dataset_id=dataset.dataset_id,
        firma_dataset=dataset.firma_contenido,
    )
    trabajo = _trabajo_de(ejecucion_id)
    assert (trabajo.tipo, trabajo.estado, trabajo.intentos) == ("GEOGRAFIA", "PENDIENTE", 0)


@pytest.mark.parametrize("que", ["corte", "version", "fallida"])
def test_lo_que_no_se_puede_abrir_no_registra_nada(tmp_path, que):
    version = pequeno(tmp_path)
    corte_id, version_id = corte_de(CORTE).corte_id, version.geografia_version_id
    if que == "corte":
        corte_id, error = uuid4(), CorteNoEncontrado
    elif que == "version":
        version_id, error = uuid4(), VersionGeografiaNoEncontrada
    else:
        mala = [rectangulo("21114", "Repetida", -98.3, 18.95, -98.1, 19.15)] * 2
        with pytest.raises(ImportacionFallida) as fallo:
            importar_referencia(tmp_path, tuple(mala), edicion="rota")
        version_id, error = fallo.value.geografia_version_id, VersionGeografiaNoPublicada

    with sesion() as s, pytest.raises(error):
        motor.abrir(s, corte_id, version_id, max_intentos=5)

    assert _cuantos(EjecucionGeografia) == 0
    assert _cuantos(TrabajoOrquestacion, TrabajoOrquestacion.tipo == TipoTrabajo.GEOGRAFIA) == 0


def test_las_mismas_entradas_no_se_abren_dos_veces(tmp_path):
    version = pequeno(tmp_path)
    activa = abrir_geo(CORTE, version)
    with pytest.raises(GeografiaEnProceso):
        abrir_geo(CORTE, version)

    motor.ejecutar_geografia(activa)

    with pytest.raises(GeografiaYaGenerada) as error:
        abrir_geo(CORTE, version)
    assert error.value.previa.id == activa
    assert _cuantos(EjecucionGeografia) == 1


# --- lo que publica, todo o nada ------------------------------------------------------------------


def test_una_geografia_publica_cada_cuenta_del_corte_con_su_linaje(tmp_path):
    version = pequeno(tmp_path)

    e = geolocalizar(CORTE, version)

    assert (e.estado, e.resultado) == ("EXITOSA", "GEOGRAFIA_PUBLICADA")
    assert (e.cuentas_objetivo, e.validas, e.sin_coordenadas, e.discrepantes) == (4, 2, 1, 1)
    assert e.distribucion["estado_geografia"]["VALIDA"] == 2
    assert e.distribucion["comparacion_municipio"] == {
        "COINCIDE": 2,
        "DISCREPA": 1,
        "NO_COMPARABLE": 1,
    }
    assert set(e.distribucion["versiones"]) == {"postgis", "geos", "proj"}
    r = resultados(e.id)
    assert {n: x["estado"] for n, x in r.items()} == {
        1: "VALIDA",
        2: "VALIDA",
        3: "SIN_COORDENADAS",
        4: "DISCREPANCIA_MUNICIPIO",
    }
    # Cada resultado dice de que fila del archivo salio, como su snapshot.
    with sesion() as s:
        filas = s.execute(
            text(
                "SELECT count(*) FROM resultado_geografia_cuenta r JOIN snapshot_cuenta s "
                "ON s.corte_canonico_id = r.corte_canonico_id "
                "AND s.cuenta_canonica_id = r.cuenta_canonica_id "
                "WHERE r.source_row = s.source_row"
            )
        ).scalar_one()
    assert filas == 4
    assert _sin_temporales()


class Falla(Exception):
    pass


@pytest.mark.parametrize("donde", motor.PUNTOS_DE_FALLA)
def test_una_falla_en_cualquier_punto_no_publica_nada(tmp_path, monkeypatch, donde):
    version = pequeno(tmp_path)
    ejecucion_id = abrir_geo(CORTE, version)

    def fallar_en(nombre: str) -> None:
        if nombre == donde:
            raise Falla(nombre)

    monkeypatch.setattr(motor, "_punto_de_falla", fallar_en)
    motor.ejecutar_geografia(ejecucion_id)

    fallida = ejecucion(ejecucion_id)
    assert (fallida.estado, fallida.resultado) == ("FALLIDA", "ERROR_INTERNO")
    assert (fallida.validas, fallida.sin_coordenadas, fallida.discrepantes) == (0, 0, 0)
    assert "Falla" in fallida.detalle
    assert _cuantos(ResultadoGeografiaCuenta) == 0
    assert _sin_temporales()
    # Una FALLIDA no estorba: las mismas entradas se vuelven a pedir y se publican completas.
    monkeypatch.undo()
    otra = geolocalizar(CORTE, version)
    assert (otra.estado, otra.cuentas_objetivo, otra.firma_inputs) == (
        "EXITOSA",
        4,
        fallida.firma_inputs,
    )
    assert _cuantos(ResultadoGeografiaCuenta) == 4


def test_una_version_del_motor_que_el_servicio_no_aplica_falla_sin_publicar(tmp_path):
    version = pequeno(tmp_path)
    ejecucion_id = abrir_geo(CORTE, version)
    with sesion() as s:
        s.execute(
            update(EjecucionGeografia)
            .where(EjecucionGeografia.id == ejecucion_id)
            .values(version_motor="geografia/v9")
        )
        s.commit()

    motor.ejecutar_geografia(ejecucion_id)

    assert ejecucion(ejecucion_id).resultado == "VERSION_NO_SOPORTADA"
    assert _cuantos(ResultadoGeografiaCuenta) == 0


def test_si_el_corte_no_tiene_las_cuentas_que_dice_no_se_publica(tmp_path):
    version = pequeno(tmp_path)
    with sesion() as s:
        s.execute(update(CorteCanonico).values(cuentas=5))
        s.commit()

    fallida = geolocalizar(CORTE, version)

    assert (fallida.estado, fallida.resultado) == ("FALLIDA", "DATOS_INCONSISTENTES")
    assert _cuantos(ResultadoGeografiaCuenta) == 0


def _parquet(corte: CorteCanonico) -> ArtefactoFuente:
    with sesion() as s:
        return s.exec(
            select(ArtefactoFuente)
            .join(
                DatasetConformado, DatasetConformado.artefacto_conformado_id == ArtefactoFuente.id
            )
            .where(DatasetConformado.id == corte.dataset_conformado_id)
        ).one()


@contextmanager
def _sin_el_objeto(almacen, sha256: str, danado: bytes | None = None) -> Iterator[None]:
    """Quita un objeto del almacen, o lo sustituye por bytes danados, y al salir lo deja como
    estaba, de solo lectura (como en las pruebas de la historia)."""
    ruta = almacen.ruta(sha256)
    original = ruta.read_bytes()
    ruta.chmod(0o600)
    if danado is None:
        ruta.unlink()
    else:
        ruta.write_bytes(danado)
    try:
        yield
    finally:
        if ruta.exists():
            ruta.chmod(0o600)
        ruta.write_bytes(original)
        ruta.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)


def test_un_parquet_danado_deja_la_geografia_fallida(tmp_path, almacen):
    version = pequeno(tmp_path)
    parquet = _parquet(corte_de(CORTE))
    # Del mismo tamano, para que nada lo note antes de comprobar su SHA-256.
    danado = b"PAR1" + b"\0" * (parquet.tamano_bytes - 4)
    with _sin_el_objeto(almacen, parquet.sha256, danado=danado):
        fallida = geolocalizar(CORTE, version)

    assert (fallida.estado, fallida.resultado) == ("FALLIDA", "ARTEFACTO_CORRUPTO")
    assert _cuantos(ResultadoGeografiaCuenta) == 0


def test_sin_el_parquet_en_el_almacen_es_un_error_del_worker_y_se_reintenta(tmp_path, almacen):
    version = pequeno(tmp_path)
    ejecucion_id = abrir_geo(CORTE, version)
    parquet = _parquet(corte_de(CORTE))
    with _sin_el_objeto(almacen, parquet.sha256), pytest.raises(ArtefactoFaltante):
        motor.ejecutar_geografia(ejecucion_id)

    assert ejecucion(ejecucion_id).estado == "EN_PROCESO"
    motor.ejecutar_geografia(ejecucion_id)
    assert ejecucion(ejecucion_id).estado == "EXITOSA"


def test_la_cola_que_agota_sus_intentos_la_deja_fallida_sin_publicar(tmp_path):
    version = pequeno(tmp_path)
    ejecucion_id = abrir_geo(CORTE, version)

    with sesion() as s:
        assert objetivos.fallar(s, TipoTrabajo.GEOGRAFIA, ejecucion_id, "Se agotaron.")
        s.commit()

    fallida = ejecucion(ejecucion_id)
    assert (fallida.estado, fallida.resultado, fallida.detalle) == (
        "FALLIDA",
        "INTENTOS_AGOTADOS",
        "Se agotaron.",
    )


@pytest.mark.parametrize(
    "sentencia",
    [
        "UPDATE resultado_geografia_cuenta SET estado_geografia = 'VALIDA'",
        "DELETE FROM resultado_geografia_cuenta",
    ],
)
def test_lo_que_una_geografia_publico_no_se_reescribe(tmp_path, sentencia):
    geolocalizar(CORTE, pequeno(tmp_path))
    with sesion() as s, pytest.raises(DBAPIError) as error:
        s.execute(text(sentencia))
    assert "geografia/v1 no se reescribe" in str(error.value)
    assert _cuantos(ResultadoGeografiaCuenta) == 4


# --- varios workers -------------------------------------------------------------------------------


def _en_otro_hilo(funcion, *argumentos) -> tuple[threading.Thread, dict]:
    salida: dict = {}

    def correr() -> None:
        try:
            salida["resultado"] = funcion(*argumentos)
        except BaseException as exc:  # pragma: no cover - la prueba lo reporta
            salida["error"] = exc

    hilo = threading.Thread(target=correr, daemon=True)
    hilo.start()
    return hilo, salida


def _detener_en(monkeypatch, punto: str) -> tuple[threading.Event, threading.Event]:
    llego, soltar = threading.Event(), threading.Event()

    def pausa(nombre: str) -> None:
        if nombre == punto and not llego.is_set():
            llego.set()
            assert soltar.wait(ESPERA)

    monkeypatch.setattr(motor, "_punto_de_falla", pausa)
    return llego, soltar


def test_dos_workers_con_la_misma_geografia_publican_una_sola_vez(tmp_path, monkeypatch):
    version = pequeno(tmp_path)
    ejecucion_id = abrir_geo(CORTE, version)
    llego, soltar = _detener_en(monkeypatch, motor.ANTES_DE_CERRAR)

    primero, salida_1 = _en_otro_hilo(motor.geolocalizar, ejecucion_id)
    assert llego.wait(ESPERA)
    segundo, salida_2 = _en_otro_hilo(motor.geolocalizar, ejecucion_id)
    time.sleep(0.5)
    assert segundo.is_alive()  # espera la fila que el primero tiene bloqueada

    soltar.set()
    for hilo in (primero, segundo):
        hilo.join(ESPERA)

    assert "error" not in salida_1 and "error" not in salida_2
    assert ejecucion(ejecucion_id).estado == "EXITOSA"
    assert _cuantos(ResultadoGeografiaCuenta) == 4


def test_un_worker_que_pierde_el_lease_a_media_geografia_no_publica(tmp_path, monkeypatch):
    version = pequeno(tmp_path)
    with sesion() as s:
        s.execute(
            update(TrabajoOrquestacion).values(
                estado=EstadoTrabajo.COMPLETADO, terminado_en=func.now()
            )
        )
        s.commit()
    ejecucion_id = abrir_geo(CORTE, version)
    config = Config(worker_lease_segundos=600, worker_heartbeat_segundos=300)
    calculo, soltar = _detener_en(monkeypatch, motor.DESPUES_DE_LOS_RESULTADOS)
    reclamo_a = cola.reclamar("worker-a", config.worker_lease_segundos)
    assert reclamo_a.tipo == TipoTrabajo.GEOGRAFIA
    hilo_a, salida_a = _en_otro_hilo(worker.procesar_reclamo, reclamo_a, "worker-a", config)
    assert calculo.wait(ESPERA)

    # A ya inserto sus resultados en su transaccion, pero su lease vence antes de que confirme: B
    # toma el trabajo y espera la ejecucion, que A todavia tiene bloqueada.
    with sesion() as s:
        s.execute(
            update(TrabajoOrquestacion)
            .where(TrabajoOrquestacion.id == reclamo_a.id)
            .values(lease_hasta=func.now() - timedelta(seconds=1))
        )
        s.commit()
    hilo_b, salida_b = _en_otro_hilo(worker.procesar_un_trabajo, "worker-b", config)
    time.sleep(0.5)
    assert hilo_b.is_alive()

    soltar.set()
    for hilo in (hilo_a, hilo_b):
        hilo.join(ESPERA)

    assert "error" not in salida_a and "error" not in salida_b
    assert salida_a["resultado"].estado is None  # A no publico ni cerro
    assert salida_b["resultado"].estado == EstadoTrabajo.COMPLETADO
    trabajo = _trabajo_de(ejecucion_id)
    assert (trabajo.estado, trabajo.intentos, trabajo.worker_id) == ("COMPLETADO", 2, None)
    assert ejecucion(ejecucion_id).estado == "EXITOSA"
    assert _cuantos(ResultadoGeografiaCuenta) == 4 and _cuantos(EjecucionGeografia) == 1


def test_dos_cortes_a_la_vez_no_se_estorban(tmp_path):
    version = pequeno(tmp_path)
    ingerir_corte_geo(
        tmp_path,
        OTRO_CORTE,
        [CuentaGeo(1, PUEBLA, "19.0", "-98.15"), CuentaGeo(9, AMOZOC)],
        nombre="otra",
    )
    primera, segunda = abrir_geo(CORTE, version), abrir_geo(OTRO_CORTE, version)

    hilos = [_en_otro_hilo(motor.ejecutar_geografia, e) for e in (primera, segunda)]
    for hilo, salida in hilos:
        hilo.join(ESPERA)
        assert "error" not in salida

    assert (ejecucion(primera).estado, ejecucion(primera).cuentas_objetivo) == ("EXITOSA", 4)
    assert (ejecucion(segunda).estado, ejecucion(segunda).cuentas_objetivo) == ("EXITOSA", 2)
    assert _cuantos(ResultadoGeografiaCuenta) == 6
    # La misma cuenta en dos cortes, cada uno con su geografia.
    assert resultados(segunda)[1]["estado"] == resultados(primera)[1]["estado"] == "VALIDA"


def test_el_worker_toma_el_trabajo_geografia_y_lo_completa(tmp_path, trabajar):
    version = pequeno(tmp_path)
    ejecucion_id = abrir_geo(CORTE, version)

    procesados = trabajar()

    assert TipoTrabajo.GEOGRAFIA in {p.tipo for p in procesados}
    assert _trabajo_de(ejecucion_id).estado == "COMPLETADO"
    assert ejecucion(ejecucion_id).estado == "EXITOSA"


def test_en_primer_plano_resuelve_con_su_propio_trabajo(tmp_path):
    version = pequeno(tmp_path)

    e = geolocalizar_en_primer_plano(corte_de(CORTE).corte_id, version.geografia_version_id)

    assert e.estado == "EXITOSA"
    trabajo = _trabajo_de(e.id)
    assert (trabajo.estado, trabajo.intentos) == ("COMPLETADO", 1)


# --- longitud y latitud, y el borde ---------------------------------------------------------------


def test_el_punto_es_longitud_latitud_y_al_reves_no_cae_en_su_municipio(tmp_path, monkeypatch):
    """(19.05, -98.2) es latitud y longitud: ST_MakePoint(-98.2, 19.05) cae en A. Si el punto se
    armara al reves, ST_MakePoint(19.05, -98.2) caeria en el Antartico, fuera de la referencia."""
    version = pequeno(tmp_path, [CuentaGeo(1, PUEBLA, "19.05", "-98.2")])
    assert "ST_MakePoint(longitud, latitud)" in " ".join(motor.SQL_CLASIFICACION)

    bien = geolocalizar(CORTE, version)
    assert (resultados(bien.id)[1]["estado"], resultados(bien.id)[1]["cvegeo"]) == (
        "VALIDA",
        PUEBLA,
    )

    al_reves = tuple(
        s.replace("ST_MakePoint(longitud, latitud)", "ST_MakePoint(latitud, longitud)")
        for s in motor.SQL_CLASIFICACION
    )
    monkeypatch.setattr(motor, "SQL_CLASIFICACION", al_reves)
    # Otras entradas (una referencia con solo A), porque las mismas ya tienen su geografia.
    solo_a = importar_referencia(tmp_path, (A,), edicion="solo-a")
    invertida = geolocalizar(CORTE, solo_a)
    assert resultados(invertida.id)[1]["estado"] == "FUERA_DE_REFERENCIA"


def test_el_borde_cuenta_un_punto_en_el_limite_de_dos_municipios_es_ambiguo(tmp_path):
    """ST_Covers incluye el borde: el limite comun de A y B (x = -98.10) cae en los dos, y el borde
    oeste de A (x = -98.30), solo en A. Con ST_Contains el primero no caeria en ninguno."""
    version = pequeno(
        tmp_path,
        [
            CuentaGeo(1, PUEBLA, "19.05", "-98.1"),
            CuentaGeo(2, PUEBLA, "19.05", "-98.3"),
            CuentaGeo(3, PUEBLA, "18.95", "-98.2"),
        ],
    )
    assert "ST_Covers(m.geometria, p.punto)" in " ".join(motor.SQL_PUNTO_EN_POLIGONO)

    r = resultados(geolocalizar(CORTE, version).id)

    assert (r[1]["estado"], r[1]["cvegeo"]) == ("MUNICIPIO_AMBIGUO", None)
    assert r[1]["motivos"] == [{"codigo": "VARIOS_MUNICIPIOS", "cvegeo": [AMOZOC, PUEBLA]}]
    assert (r[2]["estado"], r[2]["cvegeo"]) == ("VALIDA", PUEBLA)
    assert (r[3]["estado"], r[3]["cvegeo"]) == ("VALIDA", PUEBLA)
    with sesion() as s:
        contiene = s.execute(
            text(
                "SELECT count(*) FROM municipio_geografico WHERE ST_Contains(geometria, "
                "ST_SetSRID(ST_MakePoint(-98.1, 19.05), 4326))"
            )
        ).scalar_one()
    assert contiene == 0


# --- lo que el contrato nunca dejaria pasar -------------------------------------------------------


def test_coordenadas_que_cartera_v2_rechazaria_se_descartan_sin_buscarlas(tmp_path, monkeypatch):
    """Defensa en profundidad: si el dataset trajera NaN, infinitos o coordenadas fuera de rango,
    geografia/v1 las descarta por su cuenta. Una latitud de -98.2 con una longitud de 19.05 son
    invalidas, pero intercambiadas caen en A: se dice, y no se corrige."""
    version = pequeno(tmp_path, [CuentaGeo(n, PUEBLA, "19.05", "-98.2") for n in range(1, 6)])
    inyectar(
        monkeypatch,
        {
            1: ("NaN", "-98.2"),
            2: ("19.05", "Infinity"),
            3: ("-Infinity", "NaN"),
            4: ("-98.2", "19.05"),
            5: ("95", "-200"),
        },
    )

    e = geolocalizar(CORTE, version)

    assert (e.estado, e.invalidas) == ("EXITOSA", 5)
    r = resultados(e.id)
    assert {n: x["motivos"] for n, x in r.items()} == {
        1: [{"codigo": "LATITUD_NO_FINITA"}],
        2: [{"codigo": "LONGITUD_NO_FINITA"}],
        3: [{"codigo": "LATITUD_NO_FINITA"}, {"codigo": "LONGITUD_NO_FINITA"}],
        4: [
            {"codigo": "LATITUD_FUERA_DE_RANGO"},
            {"codigo": "POSIBLE_ORDEN_INVERTIDO", "cvegeo": [PUEBLA]},
        ],
        5: [{"codigo": "LATITUD_FUERA_DE_RANGO"}, {"codigo": "LONGITUD_FUERA_DE_RANGO"}],
    }
    assert not any(x["con_punto"] or x["cvegeo"] for x in r.values())
    # Lo observado se guarda como vino, sin corregir la invertida.
    assert (r[4]["latitud"], r[4]["longitud"]) == (-98.2, 19.05)
    assert publicado(e.id) == espejo(e.id)


def test_una_invertida_que_pasa_el_contrato_queda_fuera_con_su_motivo(tmp_path):
    """-89.6 es una latitud valida: la latitud y la longitud de C intercambiadas pasan cartera/v2,
    caen fuera de la referencia y, al reves, en C. Queda FUERA_DE_REFERENCIA con su motivo."""
    version = pequeno(tmp_path, [CuentaGeo(1, MERIDA, "-89.6", "21.0")])

    r = resultados(geolocalizar(CORTE, version).id)

    assert (r[1]["estado"], r[1]["cvegeo"], r[1]["con_punto"]) == (
        "FUERA_DE_REFERENCIA",
        None,
        True,
    )
    assert r[1]["motivos"][-1] == {"codigo": "POSIBLE_ORDEN_INVERTIDO", "cvegeo": [MERIDA]}


# --- versionado y reconstruccion ------------------------------------------------------------------


def test_otra_version_de_la_referencia_da_otra_geografia_y_la_anterior_no_cambia(tmp_path):
    """La version A tiene el limite de los municipios en x = -98.10; en la B, en x = -98.25. La
    misma cuenta del mismo corte cae en A con una y en B con la otra."""
    version_a = pequeno(tmp_path, [CuentaGeo(1, PUEBLA, "19.05", "-98.2")])
    primera = geolocalizar(CORTE, version_a)
    antes = _foto(primera.id)

    movidos = (
        rectangulo(PUEBLA, "A en la version B", -98.40, 18.95, -98.25, 19.15),
        rectangulo(AMOZOC, "B en la version B", -98.25, 18.95, -97.90, 19.15),
    )
    version_b = importar_referencia(tmp_path, movidos, edicion="v2")
    segunda = geolocalizar(CORTE, version_b)

    assert primera.firma_inputs != segunda.firma_inputs
    assert resultados(primera.id)[1]["estado"] == "VALIDA"
    assert (resultados(segunda.id)[1]["estado"], resultados(segunda.id)[1]["cvegeo"]) == (
        "DISCREPANCIA_MUNICIPIO",
        AMOZOC,
    )
    assert _foto(primera.id) == antes
    assert ejecucion(primera.id).estado == "EXITOSA"


def test_reconstruir_la_geografia_con_las_mismas_entradas_da_exactamente_lo_mismo(tmp_path):
    version = pequeno(tmp_path)
    primera = geolocalizar(CORTE, version)
    antes, firma = _foto(primera.id), primera.firma_inputs
    distribucion = primera.distribucion

    # Solo lo derivado de esa ejecucion, como una operacion de administracion: sus resultados, su
    # trabajo y la ejecucion. Nada mas: ni el corte, ni su dataset, ni la referencia.
    tabla = "resultado_geografia_cuenta"
    with sesion() as s:
        s.execute(text(f"ALTER TABLE {tabla} DISABLE TRIGGER tr_geografia_inmutable"))
        for sentencia in (
            f"DELETE FROM {tabla} WHERE ejecucion_geografia_id = :e",
            "DELETE FROM trabajo_orquestacion WHERE ejecucion_geografia_id = :e",
            "DELETE FROM ejecucion_geografia WHERE id = :e",
        ):
            s.execute(text(sentencia), {"e": primera.id})
        s.execute(text(f"ALTER TABLE {tabla} ENABLE TRIGGER tr_geografia_inmutable"))
        s.commit()

    reconstruida = geolocalizar(CORTE, version)

    assert reconstruida.firma_inputs == firma
    assert _foto(reconstruida.id) == antes
    assert reconstruida.distribucion == distribucion


def test_ni_la_bitacora_ni_el_detalle_llevan_coordenadas(tmp_path, caplog):
    version = pequeno(tmp_path)
    caplog.set_level("DEBUG")
    caplog.clear()

    e = geolocalizar(CORTE, version)

    assert caplog.records, "la geografia deja su rastro en la bitacora"
    for texto in (caplog.text, e.detalle, str(e.distribucion)):
        for coordenada in ("19.05", "98.2", "98.0"):
            assert coordenada not in texto


def test_un_error_de_la_base_no_lleva_la_coordenada_a_la_bitacora(tmp_path, monkeypatch, caplog):
    """Una violacion de un CHECK cita en su mensaje los valores de la fila que la provoco (las
    coordenadas observadas, entre ellos): la bitacora solo lleva el tipo del error, su SQLSTATE y
    sus marcos, y el detalle de la ejecucion, nada de eso."""
    version = pequeno(tmp_path)
    roto = motor.SQL_RESULTADOS.replace("CASE WHEN descarte IS NULL THEN punto END", "NULL")
    assert roto != motor.SQL_RESULTADOS
    monkeypatch.setattr(motor, "SQL_RESULTADOS", roto)
    caplog.set_level("DEBUG")
    caplog.clear()

    fallida = geolocalizar(CORTE, version)

    assert (fallida.estado, fallida.resultado) == ("FALLIDA", "ERROR_INTERNO")
    assert "IntegrityError, SQLSTATE 23514" in caplog.text
    for texto in (caplog.text, fallida.detalle):
        for coordenada in ("19.05", "98.2", "98.0"):
            assert coordenada not in texto
    assert _cuantos(ResultadoGeografiaCuenta) == 0
