"""La corrida de punta a punta, contra PostgreSQL: lo que se escribe y lo que no."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest
from psycopg.errors import LockNotAvailable
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import func, select

from motor_cartera.contratos import VERSION_CONTRATO
from motor_cartera.db.modelos import Corrida, Cuenta, EstadoCorrida, Rechazo, ahora
from motor_cartera.db.sesion import sesion
from motor_cartera.generador.sintetico import generar_archivo, generar_cartera
from motor_cartera.ingesta import corridas
from motor_cartera.ingesta.corridas import (
    ArchivoDuplicado,
    abrir_corrida,
    corrida_vigente,
    decidir,
    firmar,
    ingerir_archivo,
    procesar_corrida,
)

pytestmark = pytest.mark.usefixtures("bd")

CORTE = date(2026, 9, 30)


def _archivo(tmp_path: Path, nombre="cartera.csv", n=200, tasa=0.0, semilla=1, corte=CORTE):
    return generar_archivo(
        tmp_path / nombre, n=n, tasa_invalidas=tasa, semilla=semilla, fecha_corte=corte
    )


def _cuantas(modelo, corrida: Corrida) -> int:
    with sesion() as s:
        return s.exec(
            select(func.count()).select_from(modelo).where(modelo.corrida_id == corrida.id)
        ).one()


# --- los tres archivos de la mision: valido, con rechazos y malformado ------------------


def test_un_archivo_valido_publica_todas_sus_cuentas(tmp_path):
    corrida = ingerir_archivo(_archivo(tmp_path), tolerancia=0.05)

    assert corrida.estado == EstadoCorrida.EXITOSA
    assert (corrida.filas_leidas, corrida.filas_validas, corrida.filas_rechazadas) == (200, 200, 0)
    assert corrida.fecha_corte == CORTE
    assert corrida.terminada_en >= corrida.iniciada_en
    assert _cuantas(Cuenta, corrida) == 200
    assert _cuantas(Rechazo, corrida) == 0


def test_rechazos_dentro_de_la_tolerancia_publican_lo_valido(tmp_path):
    corrida = ingerir_archivo(_archivo(tmp_path, tasa=0.03), tolerancia=0.05)

    assert corrida.estado == EstadoCorrida.EXITOSA
    assert (corrida.filas_validas, corrida.filas_rechazadas) == (194, 6)
    assert _cuantas(Cuenta, corrida) == 194
    assert _cuantas(Rechazo, corrida) == 6
    assert "dentro de la tolerancia de 5.0%" in corrida.detalle


def test_rechazos_sobre_la_tolerancia_no_publican_nada_pero_dejan_evidencia(tmp_path):
    corrida = ingerir_archivo(_archivo(tmp_path, tasa=0.10), tolerancia=0.05)

    assert corrida.estado == EstadoCorrida.RECHAZADA
    assert _cuantas(Cuenta, corrida) == 0
    assert _cuantas(Rechazo, corrida) == 20
    assert "no se publico nada" in corrida.detalle


def test_con_tolerancia_cero_un_solo_rechazo_detiene_todo(tmp_path):
    corrida = ingerir_archivo(_archivo(tmp_path, tasa=0.005), tolerancia=0.0)

    assert corrida.estado == EstadoCorrida.RECHAZADA
    assert corrida.filas_rechazadas == 1
    assert _cuantas(Cuenta, corrida) == 0


def test_un_archivo_malformado_falla_sin_escribir_nada(tmp_path):
    ruta = tmp_path / "malformado.csv"
    ruta.write_text("cliente,saldo\nCU00000001,10.00\n", encoding="utf-8")

    corrida = ingerir_archivo(ruta)

    assert corrida.estado == EstadoCorrida.FALLIDA
    assert "Faltan columnas requeridas" in corrida.detalle
    assert "Encabezados recibidos: cliente, saldo" in corrida.detalle
    assert corrida.filas_leidas == 0
    assert _cuantas(Cuenta, corrida) == 0
    assert _cuantas(Rechazo, corrida) == 0
    # No se juzgo, asi que no hay contenido que firmar; con que contrato se iba a juzgar, si.
    assert corrida.firma_contenido is None
    assert corrida.version_contrato == VERSION_CONTRATO


def test_un_excel_que_no_es_excel_falla(tmp_path):
    ruta = tmp_path / "cartera.xlsx"
    ruta.write_bytes(b"no soy un excel")

    corrida = ingerir_archivo(ruta)

    assert corrida.estado == EstadoCorrida.FALLIDA
    assert "no es un Excel legible" in corrida.detalle


def test_el_rechazo_guarda_su_fila_lo_que_traia_y_por_que(tmp_path):
    ruta = tmp_path / "cartera.csv"
    ruta.write_text(
        "cliente_unico,saldo_total,dias_atraso,producto,canal,cve_entidad,cve_municipio,fecha_corte\n"
        "CU00000001,100.00,5,CONSUMO,CAMPO,21,114,2026-09-30\n"
        "CU00000002,-5,5,HIPOTECA,CAMPO,21,114,2026-09-30\n",
        encoding="utf-8",
    )

    corrida = ingerir_archivo(ruta, tolerancia=0.5)

    with sesion() as s:
        rechazo = s.exec(select(Rechazo).where(Rechazo.corrida_id == corrida.id)).one()
    assert rechazo.fila == 3  # el encabezado es la fila 1
    assert rechazo.valores["saldo_total"] == "-5"
    assert rechazo.valores["producto"] == "HIPOTECA"
    assert rechazo.motivos == [
        {"campo": "saldo_total", "regla": "greater_than_or_equal_to(0)"},
        {"campo": "producto", "regla": "isin(('CONSUMO', 'TARJETA', 'NOMINA', 'AUTOMOTRIZ'))"},
    ]


def test_el_excel_y_el_zip_registran_que_eligieron(tmp_path):
    xlsx = ingerir_archivo(_archivo(tmp_path, "c.xlsx", n=20))
    paquete = ingerir_archivo(_archivo(tmp_path, "c.zip", n=20, semilla=2))

    assert "Origen: hoja 'cartera' de 'c.xlsx'" in xlsx.detalle
    assert "Origen: 'cartera.csv', dentro de 'c.zip'" in paquete.detalle


# --- trazabilidad -------------------------------------------------------------------------


def test_todo_lo_escrito_queda_ligado_al_run_id_de_su_corrida(tmp_path):
    a = ingerir_archivo(_archivo(tmp_path, "a.csv", n=100, tasa=0.03, semilla=1))
    b = ingerir_archivo(_archivo(tmp_path, "b.csv", n=150, tasa=0.02, semilla=2))
    c = ingerir_archivo(_archivo(tmp_path, "c.csv", n=50, tasa=0.5, semilla=3))  # RECHAZADA

    with sesion() as s:
        cuentas = dict(
            s.exec(
                select(Corrida.run_id, func.count(Cuenta.id))
                .join(Corrida, Cuenta.corrida_id == Corrida.id)
                .group_by(Corrida.run_id)
            ).all()
        )
        rechazos = dict(
            s.exec(
                select(Corrida.run_id, func.count(Rechazo.id))
                .join(Corrida, Rechazo.corrida_id == Corrida.id)
                .group_by(Corrida.run_id)
            ).all()
        )
        total_cuentas = s.exec(select(func.count()).select_from(Cuenta)).one()
        total_rechazos = s.exec(select(func.count()).select_from(Rechazo)).one()

    # Cada fila escrita pertenece a una corrida, y cada corrida responde por sus conteos.
    assert cuentas == {a.run_id: a.filas_validas, b.run_id: b.filas_validas}
    assert rechazos == {
        a.run_id: a.filas_rechazadas,
        b.run_id: b.filas_rechazadas,
        c.run_id: c.filas_rechazadas,
    }
    assert total_cuentas == sum(cuentas.values())
    assert total_rechazos == sum(rechazos.values())
    for corrida in (a, b, c):
        assert corrida.filas_leidas == corrida.filas_validas + corrida.filas_rechazadas


def test_la_corrida_queda_registrada_antes_de_leer_nada(tmp_path):
    contenido = _archivo(tmp_path).read_bytes()

    with sesion() as s:
        corrida = abrir_corrida(s, origen="cartera.csv", contenido=contenido)

    assert corrida.estado == EstadoCorrida.EN_PROCESO
    assert corrida.terminada_en is None
    assert len(corrida.firma) == 64
    # Con que reglas se va a juzgar queda fijo desde que se registra; el contenido, no.
    assert corrida.version_contrato == VERSION_CONTRATO
    assert corrida.firma_contenido is None


def test_la_misma_cartera_en_csv_y_en_xlsx_tiene_la_misma_firma_de_contenido(tmp_path):
    # Dos archivos, dos firmas: se publica dos veces, porque la unicidad sigue siendo por
    # archivo. La firma del contenido deja ver que es la misma cartera.
    csv = ingerir_archivo(_archivo(tmp_path, "cartera.csv", tasa=0.03))
    xlsx = ingerir_archivo(_archivo(tmp_path, "cartera.xlsx", tasa=0.03))
    otra = ingerir_archivo(_archivo(tmp_path, "otra.csv", tasa=0.03, semilla=2))

    assert csv.estado == xlsx.estado == otra.estado == EstadoCorrida.EXITOSA
    assert csv.firma != xlsx.firma
    assert csv.firma_contenido == xlsx.firma_contenido
    assert len(csv.firma_contenido) == 64
    assert otra.firma_contenido != csv.firma_contenido


# --- una cartera se publica una sola vez --------------------------------------------------


def test_el_mismo_archivo_no_se_publica_dos_veces(tmp_path):
    ruta = _archivo(tmp_path)
    primera = ingerir_archivo(ruta)

    with pytest.raises(ArchivoDuplicado, match="ya lo publico") as exc:
        ingerir_archivo(ruta)

    assert exc.value.previa.run_id == primera.run_id
    with sesion() as s:
        assert s.exec(select(func.count()).select_from(Cuenta)).one() == 200


def test_un_archivo_que_se_esta_procesando_no_se_abre_otra_vez(tmp_path):
    # Un doble clic, o un cliente que reintenta por timeout mientras la primera sigue.
    contenido = _archivo(tmp_path).read_bytes()
    with sesion() as s:
        primera = abrir_corrida(s, origen="cartera.csv", contenido=contenido)

    with sesion() as s, pytest.raises(ArchivoDuplicado, match="se esta procesando") as exc:
        abrir_corrida(s, origen="cartera.csv", contenido=contenido)

    assert exc.value.previa.run_id == primera.run_id


def test_una_corrida_en_proceso_sigue_bloqueando_el_archivo_aunque_sea_vieja(tmp_path):
    # El tiempo no demuestra que se abandono. Hasta la 0005, una EN_PROCESO de mas de 15 minutos
    # dejaba de contar; desde la 0006 la cierra el worker que tiene su trabajo, o el que lo toma
    # cuando vence su lease, y mientras tanto sigue siendo la corrida activa de ese archivo.
    contenido = _archivo(tmp_path).read_bytes()
    with sesion() as s:
        vieja = abrir_corrida(s, origen="cartera.csv", contenido=contenido)
        vieja.iniciada_en = ahora() - timedelta(days=1)
        s.add(vieja)
        s.commit()
        s.refresh(vieja)

    with sesion() as s, pytest.raises(ArchivoDuplicado, match="se esta procesando") as exc:
        abrir_corrida(s, origen="cartera.csv", contenido=contenido)

    assert exc.value.previa.run_id == vieja.run_id


def test_la_base_no_admite_dos_corridas_activas_del_mismo_archivo(tmp_path):
    # Dos subidas simultaneas del mismo archivo pueden pasar la revision previa en el mismo
    # instante, antes de que exista cualquiera de las dos. La segunda se inserta aqui sin revision,
    # como si eso hubiera pasado: lo que la detiene es el indice de las EN_PROCESO.
    contenido = _archivo(tmp_path).read_bytes()
    with sesion() as s:
        abrir_corrida(s, origen="cartera.csv", contenido=contenido)

    with sesion() as s, pytest.raises(IntegrityError, match="ux_corrida_firma_en_proceso"):
        s.add(
            Corrida(
                origen="cartera.csv",
                firma=firmar(contenido),
                tolerancia_rechazo=0.05,
                version_contrato=VERSION_CONTRATO,
            )
        )
        s.commit()

    with sesion() as s:
        assert s.exec(select(func.count()).select_from(Corrida)).one() == 1


def test_un_archivo_que_no_publico_se_puede_reintentar(tmp_path):
    ruta = _archivo(tmp_path, tasa=0.10)
    rechazada = ingerir_archivo(ruta, tolerancia=0.05)

    reintento = ingerir_archivo(ruta, tolerancia=0.20)

    assert rechazada.estado == EstadoCorrida.RECHAZADA
    assert reintento.estado == EstadoCorrida.EXITOSA


def test_la_base_impide_publicar_dos_veces_aunque_el_codigo_no_lo_vea(tmp_path):
    # Ninguna puerta de entrada abre una corrida de un archivo ya publicado: la revision previa lo
    # ve. La segunda se inserta aqui a mano, cuando la primera ya publico, como si se la hubiera
    # saltado. Lo que impide la doble publicacion sigue siendo el indice de las EXITOSA.
    contenido = _archivo(tmp_path).read_bytes()
    with sesion() as s:
        una = abrir_corrida(s, origen="cartera.csv", contenido=contenido)
    procesar_corrida(una.id, contenido)
    with sesion() as s:
        otra = Corrida(
            origen="cartera.csv",
            firma=firmar(contenido),
            tolerancia_rechazo=0.05,
            version_contrato=VERSION_CONTRATO,
        )
        s.add(otra)
        s.commit()
        s.refresh(otra)

    procesar_corrida(otra.id, contenido)

    with sesion() as s:
        una, otra = s.get_one(Corrida, una.id), s.get_one(Corrida, otra.id)
    assert una.estado == EstadoCorrida.EXITOSA
    assert otra.estado == EstadoCorrida.FALLIDA
    assert "no se publica dos veces" in otra.detalle
    assert _cuantas(Cuenta, otra) == 0
    assert _cuantas(Rechazo, otra) == 0


def test_un_error_inesperado_deja_la_corrida_fallida_y_sin_datos(tmp_path, monkeypatch):
    def revienta(_):
        raise RuntimeError("falla simulada")

    monkeypatch.setattr(corridas, "separar_rechazos", revienta)

    corrida = ingerir_archivo(_archivo(tmp_path))

    assert corrida.estado == EstadoCorrida.FALLIDA
    assert corrida.detalle.startswith("Error interno (RuntimeError)")
    assert _cuantas(Cuenta, corrida) == 0


# --- procesar es idempotente: la cola entrega al menos una vez ------------------------------


def _tres_terminadas(tmp_path: Path) -> list[Corrida]:
    """Una corrida EXITOSA, una RECHAZADA y una FALLIDA, cada una con su archivo."""
    malformado = tmp_path / "malformado.csv"
    malformado.write_text("cliente,saldo\nCU00000001,10.00\n", encoding="utf-8")
    return [
        ingerir_archivo(_archivo(tmp_path, "exitosa.csv")),
        ingerir_archivo(_archivo(tmp_path, "rechazada.csv", tasa=0.5, semilla=2)),
        ingerir_archivo(malformado),
    ]


def _como_quedo(corrida: Corrida) -> tuple:
    with sesion() as s:
        guardada = s.get_one(Corrida, corrida.id).model_dump()
    return guardada, _cuantas(Cuenta, corrida), _cuantas(Rechazo, corrida)


def test_procesar_otra_vez_una_corrida_que_ya_termino_no_hace_nada(tmp_path, caplog):
    # Una segunda entrega del mismo trabajo, con el mismo archivo: la corrida ya termino, y no se
    # vuelve a leer, a juzgar ni a publicar.
    terminadas = _tres_terminadas(tmp_path)
    assert [c.estado for c in terminadas] == ["EXITOSA", "RECHAZADA", "FALLIDA"]
    antes = [_como_quedo(corrida) for corrida in terminadas]

    for corrida, ruta in zip(
        terminadas,
        (tmp_path / "exitosa.csv", tmp_path / "rechazada.csv", tmp_path / "malformado.csv"),
        strict=True,
    ):
        procesar_corrida(corrida.id, ruta.read_bytes())

    assert [_como_quedo(corrida) for corrida in terminadas] == antes
    avisos = [r.getMessage() for r in caplog.records if "no se vuelve a procesar" in r.getMessage()]
    assert [aviso.split(" ya termino ")[1] for aviso in avisos] == [
        "EXITOSA; no se vuelve a procesar",
        "RECHAZADA; no se vuelve a procesar",
        "FALLIDA; no se vuelve a procesar",
    ]


def test_un_fallo_que_llega_tarde_no_cambia_una_corrida_terminada(tmp_path, caplog):
    terminadas = _tres_terminadas(tmp_path)
    antes = [_como_quedo(corrida) for corrida in terminadas]

    for corrida in terminadas:
        with sesion() as s:
            corridas._fallar(s, corrida.id, corrida.run_id, "Un fallo tardio.")

    assert [_como_quedo(corrida) for corrida in terminadas] == antes
    assert sum("este fallo no la cambia" in r.getMessage() for r in caplog.records) == 3


def test_la_corrida_se_procesa_con_su_fila_bloqueada(tmp_path, monkeypatch):
    # Mientras se juzga, ninguna otra sesion puede tomar la corrida: la tiene quien la procesa.
    ruta = _archivo(tmp_path)
    with sesion() as s:
        corrida = abrir_corrida(s, origen=ruta.name, contenido=ruta.read_bytes())
    separar = corridas.separar_rechazos
    durante = []

    def separa_mientras_otra_intenta(datos):
        with sesion() as otra:
            consulta = select(Corrida).where(Corrida.id == corrida.id).with_for_update(nowait=True)
            try:
                otra.exec(consulta).one()
                durante.append("libre")
            except OperationalError as exc:
                assert isinstance(exc.orig, LockNotAvailable)
                durante.append("tomada")
        return separar(datos)

    monkeypatch.setattr(corridas, "separar_rechazos", separa_mientras_otra_intenta)

    procesar_corrida(corrida.id, ruta.read_bytes())

    assert durante == ["tomada"]
    with sesion() as s:
        assert s.get_one(Corrida, corrida.id).estado == EstadoCorrida.EXITOSA


# --- una cartera, un corte ------------------------------------------------------------------


def test_una_cartera_con_dos_fechas_de_corte_se_rechaza_entera(tmp_path):
    # 199 cuentas del 30 y una del 29. Ningun registro esta mal por si solo, pero no hay un
    # corte que sea el de la cartera, y no se elige uno: se juzgo y no cumple.
    cartera = generar_cartera(200, semilla=1, fecha_corte=CORTE)
    cartera.loc[0, "fecha_corte"] = pd.Timestamp(CORTE - timedelta(days=1))
    ruta = tmp_path / "dos_cortes.csv"
    cartera.to_csv(ruta, index=False)

    corrida = ingerir_archivo(ruta, tolerancia=0.05)

    assert corrida.estado == EstadoCorrida.RECHAZADA
    assert (corrida.filas_validas, corrida.filas_rechazadas) == (200, 0)
    assert corrida.fecha_corte is None
    assert (
        "La cartera trae 2 fechas de corte (2026-09-29 en 1 registro y 2026-09-30 en 199 "
        "registros) y debe traer una sola; no se publico nada."
    ) in corrida.detalle
    assert corrida.firma_contenido is not None  # se juzgo: su contenido tiene firma
    assert _cuantas(Cuenta, corrida) == 0
    assert _cuantas(Rechazo, corrida) == 0


# --- la cartera vigente -------------------------------------------------------------------


def test_sin_nada_publicado_no_hay_cartera_vigente():
    with sesion() as s:
        assert corrida_vigente(s) is None


def test_la_vigente_es_la_del_corte_mas_reciente_no_la_ultima_subida(tmp_path):
    hoy = ingerir_archivo(_archivo(tmp_path, "hoy.csv", corte=CORTE))
    ingerir_archivo(_archivo(tmp_path, "vieja.csv", semilla=2, corte=date(2026, 9, 23)))

    with sesion() as s:
        assert corrida_vigente(s).run_id == hoy.run_id


def test_una_correccion_del_mismo_corte_reemplaza_a_la_anterior(tmp_path):
    ingerir_archivo(_archivo(tmp_path, "hoy.csv"))
    correccion = ingerir_archivo(_archivo(tmp_path, "hoy_corregida.csv", semilla=9))

    with sesion() as s:
        assert corrida_vigente(s).run_id == correccion.run_id


def test_una_corrida_rechazada_nunca_es_la_vigente(tmp_path):
    publicada = ingerir_archivo(_archivo(tmp_path, "a.csv", corte=date(2026, 9, 29)))
    ingerir_archivo(_archivo(tmp_path, "b.csv", tasa=0.5, semilla=2), tolerancia=0.05)

    with sesion() as s:
        assert corrida_vigente(s).run_id == publicada.run_id


# --- la barrera, sin base de datos ---------------------------------------------------------


@pytest.mark.parametrize(
    ("leidas", "rechazadas", "tolerancia", "estado"),
    [
        (100, 0, 0.0, EstadoCorrida.EXITOSA),
        (100, 5, 0.05, EstadoCorrida.EXITOSA),  # justo en el tope todavia publica
        (100, 6, 0.05, EstadoCorrida.RECHAZADA),
        (100, 1, 0.0, EstadoCorrida.RECHAZADA),
        (10, 10, 0.99, EstadoCorrida.RECHAZADA),  # nada que publicar, sin importar el tope
    ],
)
def test_decidir(leidas, rechazadas, tolerancia, estado):
    un_corte = {CORTE: leidas - rechazadas} if leidas > rechazadas else {}
    assert decidir(leidas, rechazadas, tolerancia, un_corte)[0] == estado


def test_decidir_con_dos_cortes_rechaza_aunque_ningun_registro_falle():
    estado, veredicto = decidir(10_000, 0, 0.05, {date(2026, 9, 28): 1, date(2026, 9, 29): 9_999})

    assert estado == EstadoCorrida.RECHAZADA
    assert veredicto == (
        "La cartera trae 2 fechas de corte (2026-09-28 en 1 registro y 2026-09-29 en 9,999 "
        "registros) y debe traer una sola; no se publico nada."
    )


def test_decidir_dice_todas_las_razones_para_no_publicar():
    _, veredicto = decidir(100, 10, 0.05, {date(2026, 9, 28): 45, date(2026, 9, 29): 45})

    assert veredicto.startswith("El 10.0% de los registros no cumple el contrato")
    assert "; la cartera trae 2 fechas de corte" in veredicto


def test_decidir_sin_registros_es_un_error():
    with pytest.raises(ValueError):
        decidir(0, 0, 0.05, {})
