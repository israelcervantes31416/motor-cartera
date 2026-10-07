"""La materializacion historica contra PostgreSQL: identidad, cortes, snapshots, pagos observados,
fuentes equivalentes, conflictos, backfill fuera de orden y publicacion todo o nada."""

from __future__ import annotations

import stat
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from historia_escenarios import (
    Cuenta,
    cliente,
    cuenta,
    escribir_corte,
    foto,
    historia_de,
    ingerir_corte,
    ingerir_pagos_de,
    pago,
)
from sqlalchemy import func, text
from sqlmodel import select

from motor_cartera.config import Config, config
from motor_cartera.db.modelos import (
    ArtefactoFuente,
    CorteCanonico,
    CuentaCanonica,
    DatasetConformado,
    EjecucionHistoria,
    EstadoHistoria,
    PagoObservado,
    ResultadoHistoria,
    SnapshotCuenta,
    TipoFuenteHistoria,
    TipoTrabajo,
    TrabajoOrquestacion,
)
from motor_cartera.db.sesion import crear_motor, sesion, sesion_de_lectura
from motor_cartera.fuentes.almacen import ArtefactoFaltante
from motor_cartera.fuentes.conformado import COLUMNA_FILA, COLUMNA_HOJA
from motor_cartera.historia import carga, cuenta360, ejecuciones
from motor_cartera.historia.cuenta360 import SinCuentaObservada
from motor_cartera.historia.ejecuciones import (
    HistoriaEnProceso,
    HistoriaYaMaterializada,
    abrir_historia,
    materializar,
)
from motor_cartera.historia.presencia import Presencia, TipoEvento

pytestmark = pytest.mark.usefixtures("bd")

CORTES = [date(2026, 9, 2) + timedelta(days=7 * i) for i in range(10)]


def _cuantas(modelo, *condiciones) -> int:
    with sesion() as s:
        return s.exec(select(func.count()).select_from(modelo).where(*condiciones)).one()


def _materializar(corrida=None, ingesta=None, **opciones) -> EjecucionHistoria:
    ejecucion = historia_de(corrida=corrida, ingesta=ingesta)
    materializar(ejecucion.id, **opciones)
    return historia_de(corrida=corrida, ingesta=ingesta)


def _todas(cuentas: int, desde: int = 1, **atributos) -> list[Cuenta]:
    return [Cuenta(n, **atributos) for n in range(desde, desde + cuentas)]


@contextmanager
def _sin_el_objeto(almacen, sha256: str, danado: bytes | None = None) -> Iterator[None]:
    """Quita un objeto del almacen, o lo sustituye por bytes danados, y al salir lo deja como
    estaba, de solo lectura. El almacen es de toda la sesion de pruebas y es por contenido: otro
    dataset con los mismos bytes, en otra prueba, es este mismo objeto."""
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


# --- la historia se abre con su dataset ----------------------------------------------------------


def test_publicar_un_dataset_abre_su_historia_y_su_trabajo_en_la_misma_transaccion(tmp_path):
    corrida = ingerir_corte(tmp_path, CORTES[0], _todas(5))
    ingesta = ingerir_pagos_de(tmp_path, "pagos.csv", [pago(1, "2026-09-03 10:00:00", "500.00")])

    for de in ({"corrida": corrida}, {"ingesta": ingesta}):
        ejecucion = historia_de(**de)
        assert (ejecucion.estado, ejecucion.version_modelo) == ("EN_PROCESO", "historia/v1")
        assert ejecucion.resultado is None
        with sesion() as s:
            trabajo = s.exec(
                select(TrabajoOrquestacion).where(
                    TrabajoOrquestacion.ejecucion_historia_id == ejecucion.id
                )
            ).one()
        # Un trabajo HISTORIA, de ningun flujo, esperando a un worker.
        assert (trabajo.tipo, trabajo.estado, trabajo.flujo_id) == ("HISTORIA", "PENDIENTE", None)
    assert historia_de(corrida=corrida).tipo_fuente == TipoFuenteHistoria.CARTERA
    assert historia_de(ingesta=ingesta).tipo_fuente == TipoFuenteHistoria.PAGOS
    # Nada se materializo todavia: eso es del worker.
    assert _cuantas(CuentaCanonica) == _cuantas(PagoObservado) == 0


def test_una_cartera_v1_no_tiene_dataset_ni_historia(tmp_path):
    from motor_cartera.generador.sintetico import generar_archivo
    from motor_cartera.ingesta.corridas import ingerir_archivo

    corrida = ingerir_archivo(generar_archivo(tmp_path / "v1.csv", n=30, semilla=1))

    assert corrida.estado == "EXITOSA"
    assert _cuantas(EjecucionHistoria) == _cuantas(TrabajoOrquestacion) == 0


def test_la_historia_no_se_abre_dos_veces(tmp_path):
    corrida = ingerir_corte(tmp_path, CORTES[0], _todas(5))
    with sesion() as s:
        dataset = s.exec(
            select(DatasetConformado).where(DatasetConformado.corrida_id == corrida.id)
        ).one()
        with pytest.raises(HistoriaEnProceso):
            abrir_historia(s, dataset, max_intentos=5)
    _materializar(corrida)
    with sesion() as s, pytest.raises(HistoriaYaMaterializada):
        abrir_historia(s, dataset, max_intentos=5)


# --- identidad -----------------------------------------------------------------------------------


def test_el_mismo_cliente_en_diez_cortes_es_una_cuenta_con_diez_snapshots(tmp_path):
    for i, corte in enumerate(CORTES):
        corrida = ingerir_corte(tmp_path, corte, [Cuenta(1, saldo=20_000 - 1000 * i, dias=i)])
        assert _materializar(corrida).resultado == ResultadoHistoria.CORTE_PUBLICADO

    assert _cuantas(CuentaCanonica) == 1
    (unica,) = _todas_las_cuentas()
    assert (unica.despacho_id, unica.cartera_id, unica.cliente_unico) == (
        "DSP_001",
        "CARTERA_PRINCIPAL",
        cliente(1),
    )
    assert _cuantas(SnapshotCuenta, SnapshotCuenta.cuenta_canonica_id == unica.id) == 10
    assert _cuantas(CorteCanonico) == 10


def test_el_mismo_cliente_en_otra_cartera_es_otra_cuenta(tmp_path, monkeypatch):
    principal = ingerir_corte(tmp_path, CORTES[0], [Cuenta(1)])
    monkeypatch.setattr(config, "cartera_id", "OTRA_CARTERA")
    otra = ingerir_corte(tmp_path, CORTES[0], [Cuenta(1, saldo=5_000)], nombre="otra")
    monkeypatch.undo()

    # La misma fecha y el mismo cliente, en dos carteras: dos cortes, dos cuentas, sin conflicto.
    assert _materializar(principal).resultado == ResultadoHistoria.CORTE_PUBLICADO
    assert _materializar(otra).resultado == ResultadoHistoria.CORTE_PUBLICADO
    una, la_otra = cuenta(1), cuenta(1, "OTRA_CARTERA")
    assert una.cuenta_id != la_otra.cuenta_id
    assert _cuantas(CuentaCanonica) == 2
    assert _cuantas(CorteCanonico) == 2


def _todas_las_cuentas() -> list[CuentaCanonica]:
    with sesion() as s:
        return list(s.exec(select(CuentaCanonica).order_by(CuentaCanonica.cliente_unico)).all())


# --- el snapshot: estrecho, tipado y con su linaje -----------------------------------------------


def test_el_snapshot_guarda_solo_las_variables_historicas_y_su_linaje():
    with sesion() as s:
        columnas = set(
            s.exec(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_schema = "
                    "current_schema() AND table_name = 'snapshot_cuenta'"
                )
            )
            .scalars()
            .all()
        )
    assert columnas == {
        "corte_canonico_id",
        "cuenta_canonica_id",
        "fecha_corte",
        "source_row",
        "source_sheet",
        "saldo",
        "moratorios",
        "saldo_total",
        "saldo_atrasado",
        "saldo_requerido",
        "pago_normal",
        "dias_atraso",
        "atraso_maximo",
        "semanas_atraso",
        "producto",
        "estrategia",
        "canal",
        "fecha_ultimo_pago",
        "imp_ultimo_pago",
        "cve_entidad",
        "cve_municipio",
        "estatus_plan",
        "monto_plan",
        "pagos_recibidos",
        "estatus_promesa_pago",
        "monto_promesa_pago",
    }
    # Ninguna de las 93 columnas que identifican a una persona se copia: siguen en el Parquet.
    for pii in ("nombre_cte", "direccion_cte", "telefono1", "nombre_aval", "referencias_domicilio"):
        assert pii not in columnas


def test_cada_campo_tiene_en_la_base_el_tipo_que_tiene_en_el_parquet():
    from motor_cartera.contratos.cartera_v2 import CONTRATO_V2
    from motor_cartera.contratos.fuente import Tipo
    from motor_cartera.contratos.pagos import CONTRATO_PAGOS
    from motor_cartera.db.modelos import PagoObservado as Pago
    from motor_cartera.db.modelos import SnapshotCuenta as Snapshot

    esperado = {
        Tipo.IMPORTE: "NUMERIC(14, 2)",
        Tipo.ENTERO: "BIGINT",
        Tipo.FECHA: "DATE",
        Tipo.FECHA_HORA: "TIMESTAMP WITHOUT TIME ZONE",
        Tipo.DECIMAL: "FLOAT",
    }
    tipos = {c.nombre: c.tipo for c in (*CONTRATO_V2.columnas, *CONTRATO_PAGOS.columnas)}
    for modelo, campos in ((Snapshot, carga.CAMPOS_SNAPSHOT), (Pago, carga.CAMPOS_PAGO)):
        columnas = modelo.__table__.columns
        for campo in campos:
            if campo.fuente is None or campo.fuente not in tipos or campo.columna not in columnas:
                continue
            compilado = str(columnas[campo.columna].type.compile(dialect=_postgres()))
            tipo = tipos[campo.fuente]
            if tipo in esperado:
                assert compilado == esperado[tipo], campo
            else:
                assert compilado.startswith("VARCHAR"), campo
    # Los 23 campos de pagos/v1, cada uno con su columna, y ninguno dos veces.
    assert sorted(carga.fuentes(carga.CAMPOS_PAGO)) == sorted(
        [*CONTRATO_PAGOS.nombres, COLUMNA_FILA, COLUMNA_HOJA]
    )
    assert set(carga.columnas(carga.CAMPOS_PAGO)) == set(Pago.__table__.columns.keys())


def _postgres():
    from sqlalchemy.dialects import postgresql

    return postgresql.dialect()


def test_cada_snapshot_lleva_a_su_fila_del_parquet_y_al_archivo_original(tmp_path, almacen):
    corrida = ingerir_corte(tmp_path, CORTES[0], _todas(8, saldo=12_345), formato="zip")
    ejecucion = _materializar(corrida)

    with sesion() as s:
        dataset = s.exec(
            select(DatasetConformado).where(DatasetConformado.corrida_id == corrida.id)
        ).one()
        corte = s.exec(select(CorteCanonico)).one()
        parquet = s.get_one(ArtefactoFuente, dataset.artefacto_conformado_id)
        original = s.get_one(ArtefactoFuente, dataset.artefacto_original_id)
        snapshots = s.exec(select(SnapshotCuenta)).all()
        cuentas = {c.id: c.cliente_unico for c in s.exec(select(CuentaCanonica)).all()}
    # El corte, de su dataset; la ejecucion, de su corte; el dataset, de la corrida y su original.
    assert corte.dataset_conformado_id == dataset.id
    assert ejecucion.corte_canonico_id == corte.id
    assert (corte.firma_contenido, corte.fecha_corte) == (dataset.firma_contenido, CORTES[0])
    assert original.sha256 == corrida.firma
    # Y cada snapshot, de una fila del Parquet: los mismos valores, la misma hoja.
    tabla = pq.read_table(almacen.ruta(parquet.sha256)).to_pylist()
    por_fila = {fila[COLUMNA_FILA]: fila for fila in tabla}
    assert len(snapshots) == len(tabla) == 8
    for snapshot in snapshots:
        fila = por_fila[snapshot.source_row]
        assert fila["CLIENTE_UNICO"] == cuentas[snapshot.cuenta_canonica_id]
        assert snapshot.source_sheet == fila[COLUMNA_HOJA] == "CARTERA.csv"
        assert snapshot.saldo_total == fila["SALDO_TOTAL"]
        assert snapshot.saldo == fila["SALDO"] == Decimal("12345.00")
        assert snapshot.dias_atraso == fila["DIAS_ATRASO"]
        assert (snapshot.producto, snapshot.canal) == (fila["PRODUCTO"], fila["CANAL"])
        assert snapshot.fecha_corte == CORTES[0]


def test_la_geografia_del_snapshot_es_la_de_la_proyeccion_operacional(tmp_path):
    from motor_cartera.db.modelos import Cuenta as CuentaOperacional

    corrida = ingerir_corte(tmp_path, CORTES[0], _todas(40))
    _materializar(corrida)

    with sesion() as s:
        historicas = {
            (c.cliente_unico, x.cve_entidad, x.cve_municipio)
            for x, c in s.exec(
                select(SnapshotCuenta, CuentaCanonica).join(
                    CuentaCanonica, CuentaCanonica.id == SnapshotCuenta.cuenta_canonica_id
                )
            ).all()
        }
        operacionales = {
            (c.cliente_unico, c.cve_entidad, c.cve_municipio)
            for c in s.exec(select(CuentaOperacional)).all()
        }
    assert historicas == operacionales


# --- la historia solo lee el dataset conformado --------------------------------------------------


def test_la_historia_no_vuelve_a_leer_el_archivo_original(tmp_path, almacen, monkeypatch):
    corrida = ingerir_corte(tmp_path, CORTES[0], _todas(12), formato="xlsx")
    ingesta = ingerir_pagos_de(tmp_path, "pagos.zip", [pago(1, "2026-09-03 10:00:00", "500.00")])

    def prohibido(*_args, **_kwargs):
        raise AssertionError("la historia no vuelve a interpretar el archivo original")

    monkeypatch.setattr("motor_cartera.fuentes.lotes.abrir_fuente", prohibido)
    monkeypatch.setattr("motor_cartera.ingesta.lectores.leer_contenido", prohibido)

    # Los originales desaparecen del almacen, y los lectores de archivos fallan si alguien los usa.
    with (
        _sin_el_objeto(almacen, corrida.firma),
        _sin_el_objeto(almacen, _artefacto_de(ingesta)),
    ):
        assert _materializar(corrida).estado == EstadoHistoria.EXITOSA
        assert _materializar(ingesta=ingesta).estado == EstadoHistoria.EXITOSA
    assert (_cuantas(SnapshotCuenta), _cuantas(PagoObservado)) == (12, 1)


def _artefacto_de(ingesta) -> str:
    with sesion() as s:
        return s.get_one(ArtefactoFuente, ingesta.artefacto_fuente_id).sha256


def test_sin_su_parquet_la_historia_es_un_error_del_worker_y_no_se_toca(tmp_path, almacen):
    corrida = ingerir_corte(tmp_path, CORTES[0], _todas(5))
    ejecucion = historia_de(corrida=corrida)
    with sesion() as s:
        sha256 = s.exec(
            select(ArtefactoFuente.sha256)
            .join(
                DatasetConformado, DatasetConformado.artefacto_conformado_id == ArtefactoFuente.id
            )
            .where(DatasetConformado.corrida_id == corrida.id)
        ).one()
    with _sin_el_objeto(almacen, sha256):
        with pytest.raises(ArtefactoFaltante):
            ejecuciones.ejecutar_historia(ejecucion.id)

        # Sigue EN_PROCESO, para que el trabajo se reintente cuando el volumen vuelva.
        assert historia_de(corrida=corrida).estado == EstadoHistoria.EN_PROCESO

    ejecuciones.ejecutar_historia(ejecucion.id)
    assert historia_de(corrida=corrida).resultado == ResultadoHistoria.CORTE_PUBLICADO


def test_un_parquet_danado_deja_la_historia_fallida_y_dice_por_que(tmp_path, almacen):
    corrida = ingerir_corte(tmp_path, CORTES[0], _todas(5))
    with sesion() as s:
        parquet = s.exec(
            select(ArtefactoFuente)
            .join(
                DatasetConformado, DatasetConformado.artefacto_conformado_id == ArtefactoFuente.id
            )
            .where(DatasetConformado.corrida_id == corrida.id)
        ).one()
    # Del mismo tamano, para que nada lo note antes de comprobar su SHA-256.
    danado = b"PAR1" + b"\0" * (parquet.tamano_bytes - 4)
    with _sin_el_objeto(almacen, parquet.sha256, danado=danado):
        ejecucion = _materializar(corrida)

    assert (ejecucion.estado, ejecucion.resultado) == ("FALLIDA", "ARTEFACTO_CORRUPTO")
    assert "danado" in ejecucion.detalle
    assert _cuantas(CuentaCanonica) == _cuantas(CorteCanonico) == 0


# --- fuentes equivalentes y cortes conflictivos --------------------------------------------------


def test_dos_archivos_con_la_misma_cartera_y_fecha_son_un_solo_corte(tmp_path):
    cuentas = _todas(30)
    xlsx = ingerir_corte(tmp_path, CORTES[0], cuentas, formato="xlsx")
    zip_ = ingerir_corte(tmp_path, CORTES[0], cuentas, formato="zip")
    # Dos archivos distintos, la misma cartera: dos corridas EXITOSA, con la misma firma de
    # contenido.
    assert xlsx.firma != zip_.firma
    assert xlsx.firma_contenido == zip_.firma_contenido

    primera = _materializar(xlsx)
    segunda = _materializar(zip_)

    assert (primera.estado, primera.resultado) == ("EXITOSA", "CORTE_PUBLICADO")
    assert (segunda.estado, segunda.resultado) == ("EXITOSA", "FUENTE_EQUIVALENTE")
    # Un corte, un conjunto de snapshots, y las dos ejecuciones apuntando a el.
    assert _cuantas(CorteCanonico) == 1
    assert _cuantas(SnapshotCuenta) == 30
    assert primera.corte_canonico_id == segunda.corte_canonico_id
    assert (segunda.registros_leidos, segunda.registros_publicados) == (0, 0)
    assert "Fuente equivalente" in segunda.detalle
    # El corte sigue siendo el del primer dataset: la segunda fuente queda como procedencia.
    with sesion() as s:
        corte = s.exec(select(CorteCanonico)).one()
        productor = s.get_one(DatasetConformado, corte.dataset_conformado_id)
        detalle = cuenta360.detalle_del_corte(s, corte.corte_id)
    assert productor.corrida_id == xlsx.id
    assert [f.ejecucion.resultado for f in detalle.fuentes] == [
        "CORTE_PUBLICADO",
        "FUENTE_EQUIVALENTE",
    ]
    assert {f.original.sha256 for f in detalle.fuentes} == {xlsx.firma, zip_.firma}


def test_otra_cartera_con_la_misma_fecha_es_un_conflicto_y_el_corte_no_cambia(tmp_path):
    primera = ingerir_corte(tmp_path, CORTES[0], _todas(20))
    otra = ingerir_corte(tmp_path, CORTES[0], _todas(25, saldo=99_999), nombre="otra")
    _materializar(primera)
    antes = foto()

    conflictiva = _materializar(otra)

    assert (conflictiva.estado, conflictiva.resultado) == ("FALLIDA", "CORTE_CANONICO_CONFLICTIVO")
    assert conflictiva.corte_canonico_id is None
    assert conflictiva.registros_publicados == 0
    assert "La historia no se sobrescribe" in conflictiva.detalle
    assert primera.firma_contenido in conflictiva.detalle
    # Cero cambios al corte publicado y cero snapshots o cuentas nuevas.
    assert foto() == antes
    assert _cuantas(SnapshotCuenta) == 20


# --- el tiempo: backfill fuera de orden -----------------------------------------------------------


PRESENCIA = {
    "A": (1, 2, 3, 4),
    "B": (1, 2),
    "C": (1, 2, 4),
    "D": (4,),
}
NUMERO = {"A": 1, "B": 2, "C": 3, "D": 4}


def _cuatro_cortes(destino) -> list:
    """Los cuatro cortes de la definicion, con el saldo y el atraso de cada cuenta cambiando."""
    corridas = []
    for i, corte in enumerate(CORTES[:4], start=1):
        cuentas = [
            Cuenta(NUMERO[letra], saldo=10_000 - 500 * i, dias=10 * i)
            for letra, cortes in PRESENCIA.items()
            if i in cortes
        ]
        corridas.append(ingerir_corte(destino, corte, cuentas))
    return corridas


def test_un_corte_atrasado_deja_la_misma_historia_que_si_hubiera_llegado_a_tiempo(tmp_path):
    corridas = _cuatro_cortes(tmp_path)
    for i in (0, 2, 3):
        _materializar(corridas[i])
    # Sin el corte 2, la historia es la de tres cortes: A parece continua de 1 a 3.
    a = cuenta(NUMERO["A"])
    with sesion() as s:
        _, sin_el_2 = cuenta360.historia(s, a, desplazamiento=0, limite=10, descendente=False)
    assert [h.enlace.continuo_desde_anterior for h in sin_el_2] == [None, True, True]

    _materializar(corridas[1])
    fuera_de_orden = foto()

    # La misma historia que en orden: se borra solo la capa historica y se materializa 1, 2, 3, 4.
    _borrar_la_historia_y_reabrir(corridas)
    for corrida in corridas:
        _materializar(corrida)
    assert foto() == fuera_de_orden


def test_los_cuatro_casos_de_presencia_con_sus_deltas_y_su_continuidad(tmp_path):
    corridas = _cuatro_cortes(tmp_path)
    for corrida in corridas[::-1]:
        _materializar(corrida)

    with sesion() as s:
        vistas = {letra: cuenta(NUMERO[letra]) for letra in PRESENCIA}
        eventos = {letra: cuenta360.eventos(s, c) for letra, c in vistas.items()}
        resumenes = {letra: cuenta360.resumen(s, c) for letra, c in vistas.items()}
        _, historia_c = cuenta360.historia(
            s, vistas["C"], desplazamiento=0, limite=10, descendente=False
        )

    def tipos(letra):
        return [(e.tipo, CORTES.index(e.fecha_corte) + 1) for e in eventos[letra]]

    assert tipos("A") == [(TipoEvento.PRIMERA_OBSERVACION, 1)]
    assert tipos("B") == [(TipoEvento.PRIMERA_OBSERVACION, 1), (TipoEvento.SALIDA_OBSERVADA, 3)]
    assert tipos("C") == [
        (TipoEvento.PRIMERA_OBSERVACION, 1),
        (TipoEvento.SALIDA_OBSERVADA, 3),
        (TipoEvento.REINGRESO_OBSERVADO, 4),
    ]
    assert tipos("D") == [(TipoEvento.PRIMERA_OBSERVACION, 4)]
    estados = {letra: r.presencia.estado for letra, r in resumenes.items()}
    assert estados == {
        "A": Presencia.EN_CARTERA,
        "B": Presencia.NO_OBSERVADA_EN_ULTIMO_CORTE,
        "C": Presencia.EN_CARTERA,
        "D": Presencia.EN_CARTERA,
    }
    # B ya no esta en el ultimo corte: no hay snapshot actual, pero si su ultimo observado.
    assert resumenes["B"].snapshot_actual is None
    assert resumenes["B"].ultimo_snapshot_observado.snapshot.fecha_corte == CORTES[1]
    # C: 1 -> 2 es continuo; 2 -> 4 no lo es, y el delta es la diferencia de dos observaciones.
    assert [
        (h.enlace.continuo_desde_anterior, h.enlace.cortes_ausentes_desde_anterior)
        for h in historia_c
    ] == [(None, None), (True, 0), (False, 1)]
    saldos = [h.visto.snapshot.saldo_total for h in historia_c]
    assert [h.delta_saldo_total for h in historia_c] == [
        None,
        saldos[1] - saldos[0],
        saldos[2] - saldos[1],
    ]
    assert [h.delta_dias_atraso for h in historia_c] == [None, 10, 20]


def _borrar_la_historia_y_reabrir(corridas=(), ingestas=()) -> None:
    """Borra solo la capa historica: sus trabajos, sus ejecuciones, sus snapshots, sus pagos, sus
    cortes y sus cuentas. Los datasets conformados y todo lo demas se quedan. Despues abre otra vez
    la historia de cada dataset."""
    with sesion() as s:
        for tabla in (
            "trabajo_orquestacion WHERE tipo = 'HISTORIA'",
            "snapshot_cuenta",
            "pago_observado",
            "ejecucion_historia",
            "corte_canonico",
            "cuenta_canonica",
        ):
            s.execute(text(f"DELETE FROM {tabla}"))
        s.commit()
        for corrida in corridas:
            dataset = s.exec(
                select(DatasetConformado).where(DatasetConformado.corrida_id == corrida.id)
            ).one()
            abrir_historia(s, dataset, max_intentos=5)
        for ingesta in ingestas:
            dataset = s.exec(
                select(DatasetConformado).where(DatasetConformado.ingesta_pagos_id == ingesta.id)
            ).one()
            abrir_historia(s, dataset, max_intentos=5)
        s.commit()


# --- pagos observados -----------------------------------------------------------------------------


def test_dos_pagos_identicos_son_dos_observaciones(tmp_path):
    identico = pago(1, "2026-09-03 10:15:00", "750.00")
    ingesta = ingerir_pagos_de(tmp_path, "pagos.csv", [identico, dict(identico)])

    ejecucion = _materializar(ingesta=ingesta)

    assert (ejecucion.resultado, ejecucion.registros_publicados) == ("PAGOS_PUBLICADOS", 2)
    with sesion() as s:
        pagos = s.exec(select(PagoObservado).order_by(PagoObservado.source_row)).all()
    assert [p.source_row for p in pagos] == [2, 3]
    assert len({p.pago_observado_id for p in pagos}) == 2
    assert {(p.cliente_unico, p.recuperacion_por_gestion) for p in pagos} == {
        (cliente(1), Decimal("750.00"))
    }


def test_el_mismo_movimiento_en_dos_archivos_son_dos_observaciones(tmp_path):
    movimiento = pago(1, "2026-09-03 10:15:00", "750.00")
    una = ingerir_pagos_de(tmp_path, "pagos_a.csv", [movimiento])
    otra = ingerir_pagos_de(
        tmp_path, "pagos_b.csv", [movimiento, pago(2, "2026-09-04 09:00:00", "100.00")]
    )

    _materializar(ingesta=una)
    _materializar(ingesta=otra)

    assert _cuantas(PagoObservado) == 3
    assert _cuantas(PagoObservado, PagoObservado.cliente_unico == cliente(1)) == 2


def test_los_23_campos_de_pagos_v1_se_conservan_tipados(tmp_path, almacen):
    completo = pago(
        7,
        "2026-09-03 10:15:00.250000",
        "-150.00",
        **{
            "Semana": "36",
            "Zona": "ZONA 03",
            "Segmento": "31-60",
            "Gerencia": "GERENCIA 02",
            "Tipo_Cartera": "TRADICIONAL",
            "Campaña": "CAMP-02",
            "Semanas_de_Atraso": "2",
            "Plan_de_Pago": "N",
            "Fecha_de_Gestion": "2026-09-01",
            "Concepto_Cálculo": "AJUSTE",
            "Cargos_Automáticos": "15.50",
            "Captación": "-150.00",
            "Cobranza_Total": "-134.50",
            "Porcentaje_Comision": "0.05",
            "Monto_Comision": "-6.73",
        },
    )
    ingesta = ingerir_pagos_de(tmp_path, "pagos.zip", [completo])
    _materializar(ingesta=ingesta)

    with sesion() as s:
        (observado,) = s.exec(select(PagoObservado)).all()
        dataset = s.exec(
            select(DatasetConformado).where(DatasetConformado.ingesta_pagos_id == ingesta.id)
        ).one()
        parquet = s.get_one(ArtefactoFuente, dataset.artefacto_conformado_id)
    (fila,) = pq.read_table(almacen.ruta(parquet.sha256)).to_pylist()
    for campo in carga.CAMPOS_PAGO:
        if campo.fuente is not None:
            assert getattr(observado, campo.columna) == fila[campo.fuente], campo
    # Los tipos son los del contrato: un ajuste negativo es negativo, y el instante no tiene zona.
    assert observado.recuperacion_por_gestion == Decimal("-150.00")
    assert observado.fecha_recepcion.tzinfo is None
    assert observado.fecha_de_gestion.isoformat() == "2026-09-01T00:00:00"
    assert (observado.anio, observado.semana, observado.porcentaje_comision) == (2026, 36, 0.05)
    assert (observado.despacho_id, observado.cartera_id) == ("DSP_001", "CARTERA_PRINCIPAL")
    assert observado.source_sheet == "PAGOS.csv"


def test_un_pago_sin_cuenta_se_conserva_y_se_relaciona_cuando_llega_su_corte(tmp_path):
    ingesta = ingerir_pagos_de(tmp_path, "pagos.csv", [pago(9, "2026-09-01 12:00:00", "320.00")])
    _materializar(ingesta=ingesta)

    # Ningun pago crea una cuenta: el cliente no tiene cuenta canonica, y su pago esta ahi.
    assert _cuantas(CuentaCanonica) == 0
    with sesion() as s:
        with pytest.raises(SinCuentaObservada) as error:
            cuenta360.buscar(s, "DSP_001", "CARTERA_PRINCIPAL", cliente(9))
        relacion = cuenta360.relacion_de_pagos(s, _dataset_de(ingesta))
        (antes,) = s.exec(select(PagoObservado)).all()
    assert error.value.pagos == 1
    assert (relacion.con_cuenta_observada, relacion.sin_cuenta_observada) == (0, 1)

    # Despues llega un corte, posterior al pago, que si lo trae.
    _materializar(ingerir_corte(tmp_path, CORTES[0], [Cuenta(9), Cuenta(10)]))

    nueva = cuenta(9)
    with sesion() as s:
        total, pagos = cuenta360.pagos_observados(s, nueva, desplazamiento=0, limite=10)
        relacion = cuenta360.relacion_de_pagos(s, _dataset_de(ingesta))
        (despues,) = s.exec(select(PagoObservado)).all()
    assert total == 1 and pagos[0].pago.pago_observado_id == antes.pago_observado_id
    assert (relacion.con_cuenta_observada, relacion.sin_cuenta_observada) == (1, 0)
    # El pago no se toco: ni una columna cambio.
    assert despues.model_dump() == antes.model_dump()


def _dataset_de(ingesta) -> int:
    with sesion() as s:
        return s.exec(
            select(DatasetConformado.id).where(DatasetConformado.ingesta_pagos_id == ingesta.id)
        ).one()


# --- todo o nada ----------------------------------------------------------------------------------


class Falla(Exception):
    pass


def _falla_despues(original):
    def envoltura(*args, **kwargs):
        original(*args, **kwargs)
        raise Falla("inyectada")

    return envoltura


def _falla_en_el_segundo_lote(original):
    def bloques(*args, **kwargs):
        for numero, bloque in enumerate(original(*args, **kwargs)):
            if numero == 1:
                raise Falla("inyectada a media copia")
            yield bloque

    return bloques


FALLAS = {
    "antes del COPY": ("_preparar_staging", lambda original: _lanzar),
    "durante el staging": ("bloques_snapshot", _falla_en_el_segundo_lote),
    "despues de crear las cuentas": ("_crear_cuentas", _falla_despues),
    "despues de insertar los snapshots": ("_insertar_snapshots", _falla_despues),
    "antes de cerrar la ejecucion": ("_cerrar", lambda original: _lanzar),
}


def _lanzar(*_args, **_kwargs):
    raise Falla("inyectada")


@pytest.mark.parametrize("donde", list(FALLAS))
def test_una_falla_en_cualquier_punto_no_publica_nada_y_deja_auditable(
    tmp_path, monkeypatch, donde
):
    corrida = ingerir_corte(tmp_path, CORTES[0], _todas(350))
    funcion, falla = FALLAS[donde]
    modulo = carga if funcion == "bloques_snapshot" else ejecuciones
    monkeypatch.setattr(modulo, funcion, falla(getattr(modulo, funcion)))

    ejecucion = _materializar(corrida, config=Config(filas_por_lote=100))

    assert (ejecucion.estado, ejecucion.resultado) == ("FALLIDA", "ERROR_INTERNO")
    assert ejecucion.registros_publicados == 0
    assert ejecucion.detalle == "Error interno (Falla); ver la bitacora."
    for modelo in (CuentaCanonica, CorteCanonico, SnapshotCuenta):
        assert _cuantas(modelo) == 0, (donde, modelo)
    # La tabla temporal tampoco sobrevive al rollback.
    with sesion() as s:
        assert s.execute(text("SELECT to_regclass('pg_temp.historia_staging')")).scalar() is None

    # Sin la falla, el mismo dataset se materializa entero con otra ejecucion.
    monkeypatch.undo()
    _reabrir(corrida)
    assert _materializar(corrida).resultado == ResultadoHistoria.CORTE_PUBLICADO
    assert _cuantas(SnapshotCuenta) == _cuantas(CuentaCanonica) == 350


def test_una_falla_a_media_copia_de_pagos_no_publica_ningun_pago(tmp_path, monkeypatch):
    filas = [pago(n, "2026-09-03 10:00:00", "100.00") for n in range(1, 251)]
    ingesta = ingerir_pagos_de(tmp_path, "pagos.csv", filas)
    monkeypatch.setattr(carga, "bloques_pagos", _falla_en_el_segundo_lote(carga.bloques_pagos))

    ejecucion = _materializar(ingesta=ingesta, config=Config(filas_por_lote=100))

    assert (ejecucion.estado, ejecucion.resultado) == ("FALLIDA", "ERROR_INTERNO")
    assert ejecucion.registros_leidos == 100
    assert _cuantas(PagoObservado) == 0


class Muerte(BaseException):  # noqa: N818
    """La muerte del proceso: ningun `except Exception` la atrapa."""


def test_si_el_proceso_muere_a_media_transaccion_la_historia_se_vuelve_a_hacer_entera(
    tmp_path, monkeypatch
):
    corrida = ingerir_corte(tmp_path, CORTES[0], _todas(120))
    ejecucion = historia_de(corrida=corrida)

    def muere(*_args, **_kwargs):
        raise Muerte()

    monkeypatch.setattr(ejecuciones, "_insertar_snapshots", muere)
    with pytest.raises(Muerte):
        materializar(ejecucion.id)

    # La transaccion se revirtio sola: nada publicado, y la ejecucion sigue EN_PROCESO.
    assert historia_de(corrida=corrida).estado == EstadoHistoria.EN_PROCESO
    assert _cuantas(CuentaCanonica) == _cuantas(CorteCanonico) == 0
    monkeypatch.undo()
    materializar(ejecucion.id)
    assert historia_de(corrida=corrida).resultado == ResultadoHistoria.CORTE_PUBLICADO
    assert _cuantas(SnapshotCuenta) == 120


def _reabrir(corrida) -> None:
    with sesion() as s:
        dataset = s.exec(
            select(DatasetConformado).where(DatasetConformado.corrida_id == corrida.id)
        ).one()
        abrir_historia(s, dataset, max_intentos=5)
        s.commit()


# --- idempotencia y versiones ---------------------------------------------------------------------


def test_una_ejecucion_terminada_no_se_vuelve_a_materializar(tmp_path):
    corrida = ingerir_corte(tmp_path, CORTES[0], _todas(10))
    ejecucion = _materializar(corrida)

    materializar(ejecucion.id)
    ejecuciones.ejecutar_historia(ejecucion.id)

    assert _cuantas(SnapshotCuenta) == 10
    assert historia_de(corrida=corrida).terminada_en == ejecucion.terminada_en


def test_una_ejecucion_de_otra_version_del_modelo_no_se_materializa_con_esta(tmp_path):
    corrida = ingerir_corte(tmp_path, CORTES[0], _todas(10))
    ejecucion = historia_de(corrida=corrida)
    with sesion() as s:
        s.execute(
            text("UPDATE ejecucion_historia SET version_modelo = 'historia/v9' WHERE id = :id"),
            {"id": ejecucion.id},
        )
        s.commit()

    terminada = _materializar(corrida)

    assert (terminada.estado, terminada.resultado) == ("FALLIDA", "VERSION_NO_SOPORTADA")
    assert _cuantas(CorteCanonico) == 0


def test_dos_ejecuciones_exitosas_del_mismo_dataset_no_existen(tmp_path):
    # Una segunda ejecucion que se salto las revisiones (aqui, insertada a mano) llega hasta el
    # cierre: el indice de las EXITOSA la detiene y queda FALLIDA, sin publicar nada.
    corrida = ingerir_corte(tmp_path, CORTES[0], _todas(10))
    primera = _materializar(corrida)
    with sesion() as s:
        s.add(
            EjecucionHistoria(
                dataset_conformado_id=_dataset_de_corrida(corrida),
                tipo_fuente=TipoFuenteHistoria.CARTERA,
                version_modelo="historia/v1",
            )
        )
        s.commit()
    segunda = historia_de(corrida=corrida)

    with pytest.raises(HistoriaYaMaterializada) as ganadora:
        materializar(segunda.id)

    assert ganadora.value.previa.historia_run_id == primera.historia_run_id
    perdedora = historia_de(corrida=corrida)
    assert (perdedora.estado, perdedora.resultado) == ("FALLIDA", "YA_MATERIALIZADA")
    assert _cuantas(SnapshotCuenta) == 10


def _dataset_de_corrida(corrida) -> int:
    with sesion() as s:
        return s.exec(
            select(DatasetConformado.id).where(DatasetConformado.corrida_id == corrida.id)
        ).one()


# --- el worker ------------------------------------------------------------------------------------


def test_el_worker_materializa_la_historia_desde_la_cola(tmp_path, trabajar):
    corrida = ingerir_corte(tmp_path, CORTES[0], _todas(15))
    ingesta = ingerir_pagos_de(tmp_path, "pagos.csv", [pago(1, "2026-09-03 10:00:00", "500.00")])

    procesados = trabajar()

    assert [(p.tipo, p.estado) for p in procesados] == [
        (TipoTrabajo.HISTORIA, "COMPLETADO"),
        (TipoTrabajo.HISTORIA, "COMPLETADO"),
    ]
    assert historia_de(corrida=corrida).resultado == ResultadoHistoria.CORTE_PUBLICADO
    assert historia_de(ingesta=ingesta).resultado == ResultadoHistoria.PAGOS_PUBLICADOS


def test_la_historia_que_agota_sus_intentos_queda_fallida_con_su_motivo(tmp_path, monkeypatch):
    from motor_cartera.orquestacion import worker

    corrida = ingerir_corte(tmp_path, CORTES[0], _todas(5))

    def siempre_falla(_ejecucion_id: int) -> None:
        raise ConnectionError("la base se fue")

    monkeypatch.setitem(worker.MANEJADORES, TipoTrabajo.HISTORIA, siempre_falla)
    # Un trabajo que ya va en su ultimo intento.
    with sesion() as s:
        s.execute(text("UPDATE trabajo_orquestacion SET max_intentos = 1 WHERE tipo = 'HISTORIA'"))
        s.commit()

    procesado = worker.procesar_un_trabajo(worker.identificador_worker(), Config())

    assert procesado.estado == "FALLIDO"
    ejecucion = historia_de(corrida=corrida)
    assert (ejecucion.estado, ejecucion.resultado) == ("FALLIDA", "INTENTOS_AGOTADOS")
    assert ejecucion.registros_publicados == 0


def test_la_cola_toma_lo_operacional_antes_que_la_historia(tmp_path):
    from motor_cartera.generador.sintetico import generar_archivo
    from motor_cartera.orquestacion import cola
    from motor_cartera.orquestacion.flujo import encolar_ingesta

    # La historia de un corte entra primero a la cola; despues, una ingesta.
    ingerir_corte(tmp_path, CORTES[0], _todas(5))
    ruta = generar_archivo(tmp_path / "v1.csv", n=20, semilla=3)
    with sesion() as s:
        _, ingesta = encolar_ingesta(
            s, origen=ruta.name, contenido=ruta.read_bytes(), tolerancia=None, config=Config()
        )
        ingesta_id = ingesta.id
    historia = _cuantas(TrabajoOrquestacion, TrabajoOrquestacion.tipo == TipoTrabajo.HISTORIA)
    assert historia == 1

    primero = cola.reclamar("worker-a", 60)
    segundo = cola.reclamar("worker-b", 60)

    # Aunque llego despues, la ingesta va primero: la historia no retrasa el flujo operacional.
    assert (primero.id, primero.tipo) == (ingesta_id, TipoTrabajo.INGESTA)
    assert segundo.tipo == TipoTrabajo.HISTORIA
    assert segundo.id < primero.id


def test_un_dataset_que_no_es_lo_que_dice_su_registro_no_se_materializa(tmp_path):
    corrida = ingerir_corte(tmp_path, CORTES[0], _todas(5))
    with sesion() as s:
        s.execute(
            text("UPDATE dataset_conformado SET filas = 6 WHERE corrida_id = :corrida"),
            {"corrida": corrida.id},
        )
        s.commit()

    ejecucion = _materializar(corrida)

    # El Parquet tiene 5 filas y su registro dice 6: no se lee ni se publica nada.
    assert (ejecucion.estado, ejecucion.resultado) == ("FALLIDA", "DATOS_INCONSISTENTES")
    assert "5 filas y su dataset dice 6" in ejecucion.detalle
    assert _cuantas(CorteCanonico) == _cuantas(CuentaCanonica) == 0


# --- una respuesta de la API ve una sola foto de la base ------------------------------------------


@pytest.fixture
def api(cliente):
    """El cliente de la API: en este modulo, `cliente` es el CLIENTE_UNICO de un numero."""
    return cliente


def test_la_cuenta_360_no_mezcla_dos_estados_si_se_publica_un_corte_mientras_responde(
    tmp_path, api, monkeypatch
):
    _materializar(ingerir_corte(tmp_path, CORTES[0], _todas(3)))
    pendiente = historia_de(corrida=ingerir_corte(tmp_path, CORTES[1], _todas(3, saldo=9_000)))
    cuenta_id = cuenta(1).cuenta_id
    cortes_de_la_cartera = cuenta360.cortes_de_la_cartera

    def publicar_en_medio(*args, **kwargs):
        # Otra transaccion publica el segundo corte, con la cuenta, justo despues de que la
        # respuesta leyo en que cortes esta la cuenta y antes de que lea los de la cartera.
        materializar(pendiente.id)
        return cortes_de_la_cartera(*args, **kwargs)

    monkeypatch.setattr(cuenta360, "cortes_de_la_cartera", publicar_en_medio)
    durante = api.get(f"/cuentas/{cuenta_id}").json()
    monkeypatch.undo()
    despues = api.get(f"/cuentas/{cuenta_id}").json()

    # La respuesta es entera la de antes del segundo corte: la cuenta sigue en la cartera, y no
    # aparece una salida que no ocurrio.
    assert (durante["ultimo_corte_cartera"], durante["estado_presencia"]) == (
        CORTES[0].isoformat(),
        "EN_CARTERA",
    )
    assert (durante["cortes_observados"], durante["salidas_observadas"]) == (1, 0)
    # El corte si se publico, y la siguiente respuesta ya lo trae.
    assert (despues["ultimo_corte_cartera"], despues["estado_presencia"]) == (
        CORTES[1].isoformat(),
        "EN_CARTERA",
    )
    assert (despues["cortes_observados"], despues["salidas_observadas"]) == (2, 0)


def _aislamiento(s) -> tuple[str, str]:
    return (
        s.execute(text("SHOW transaction_isolation")).scalar_one(),
        s.execute(text("SHOW transaction_read_only")).scalar_one(),
    )


def test_la_sesion_de_lectura_no_cambia_las_conexiones_que_vuelven_al_pool():
    with sesion_de_lectura() as s:
        assert _aislamiento(s) == ("repeatable read", "on")

    # Todas las conexiones del pool a la vez, incluida la que uso la sesion de lectura: cada una
    # volvio con READ COMMITTED y puede escribir.
    with ExitStack() as pila:
        sesiones = [pila.enter_context(sesion()) for _ in range(crear_motor().pool.checkedin() + 1)]
        assert {_aislamiento(s) for s in sesiones} == {("read committed", "off")}


def test_la_historia_de_una_corrida_en_proceso_o_sin_dataset(tmp_path, api):
    from motor_cartera.generador.sintetico import generar_archivo
    from motor_cartera.ingesta.corridas import ingerir_archivo

    archivo = escribir_corte(tmp_path, CORTES[0], _todas(3))
    subida = api.post(
        "/corridas",
        files={"archivo": (archivo.name, archivo.read_bytes(), "application/octet-stream")},
        data={"contrato": "cartera/v2", "fecha_corte": CORTES[0].isoformat()},
    )
    assert subida.status_code == 201
    # Sin un worker, la corrida sigue EN_PROCESO: su dataset y su historia todavia no existen.
    en_proceso = api.get(f"/corridas/{subida.json()['run_id']}/historia")
    assert (en_proceso.status_code, en_proceso.json()["codigo"]) == (409, "CORRIDA_EN_PROCESO")

    # Una corrida de cartera/v1 termina sin dataset conformado, y por lo tanto sin historia.
    v1 = ingerir_archivo(generar_archivo(tmp_path / "v1.csv", n=30, semilla=1))
    sin_dataset = api.get(f"/corridas/{v1.run_id}/historia")
    assert (sin_dataset.status_code, sin_dataset.json()["codigo"]) == (
        404,
        "SIN_DATASET_CONFORMADO",
    )


def test_la_historia_de_una_ingesta_de_pagos_en_proceso_o_sin_dataset(tmp_path, api):
    import pandas as pd

    from motor_cartera.contratos.pagos import CONTRATO_PAGOS
    from motor_cartera.generador.oficial import escribir_pagos
    from motor_cartera.ingesta.pagos import ingerir_pagos

    def archivo(nombre: str, fila: dict) -> Path:
        tabla = pd.DataFrame([fila], columns=list(CONTRATO_PAGOS.nombres))
        return escribir_pagos(tabla, tmp_path / nombre).ruta

    bueno = archivo("pagos.csv", pago(1, "2026-09-03 10:00:00", "500.00"))
    subida = api.post(
        "/pagos", files={"archivo": (bueno.name, bueno.read_bytes(), "application/octet-stream")}
    )
    assert subida.status_code == 201
    en_proceso = api.get(f"/pagos/{subida.json()['pagos_run_id']}/historia")
    assert (en_proceso.status_code, en_proceso.json()["codigo"]) == (409, "PAGOS_EN_PROCESO")

    # Un movimiento invalido rechaza el archivo: no se publica dataset, y no hay historia.
    rechazada = ingerir_pagos(archivo("malos.csv", pago(2, "2026-09-03 10:00:00", "quinientos")))
    assert rechazada.estado != "EXITOSA"
    sin_dataset = api.get(f"/pagos/{rechazada.pagos_run_id}/historia")
    assert (sin_dataset.status_code, sin_dataset.json()["codigo"]) == (
        404,
        "SIN_DATASET_CONFORMADO",
    )
