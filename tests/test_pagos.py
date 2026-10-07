"""pagos/v1 de punta a punta, contra PostgreSQL: el contrato de 23 columnas, el generador, la
ingesta que no deduplica nada, la barrera conservadora y el trabajo INGESTA_PAGOS en la cola."""

from __future__ import annotations

import io
import threading
import time
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from sqlalchemy import func, update
from sqlmodel import select

from motor_cartera.config import Config
from motor_cartera.contratos.fuente import Tipo, juzgar_lote
from motor_cartera.contratos.pagos import (
    CONTRATO_PAGOS,
    LLAVE_HISTORICA,
    VERSION_CONTRATO_PAGOS,
)
from motor_cartera.db.modelos import (
    ArtefactoFuente,
    Corrida,
    DatasetConformado,
    EstadoIngestaPagos,
    EstadoTrabajo,
    FlujoOrquestacion,
    IngestaPagos,
    RechazoPago,
    TipoTrabajo,
    TrabajoOrquestacion,
)
from motor_cartera.db.sesion import sesion
from motor_cartera.fuentes.conformado import COLUMNA_FILA, COLUMNA_HOJA
from motor_cartera.generador.oficial import (
    escribir_pagos,
    estado_inicial,
    tabla_cartera,
    tabla_pagos,
)
from motor_cartera.ingesta.pagos import (
    PagosDuplicados,
    decidir_pagos,
    ingerir_pagos,
    procesar_ingesta_pagos,
)
from motor_cartera.orquestacion import cola, worker
from motor_cartera.orquestacion.flujo import encolar_ingesta_pagos
from motor_cartera.orquestacion.worker import ejecutar_worker, procesar_un_trabajo

en_la_base = pytest.mark.usefixtures("bd")

CORTE = date(2026, 9, 30)
DESDE = CORTE - timedelta(days=6)
CONFIG = Config()
EXITOSA, RECHAZADA, FALLIDA = (
    EstadoIngestaPagos.EXITOSA,
    EstadoIngestaPagos.RECHAZADA,
    EstadoIngestaPagos.FALLIDA,
)

# Las 23 columnas del contrato fuente, en su orden oficial, escritas aqui a mano: si el contrato
# cambia una sola, esta prueba lo nota.
COLUMNAS_PAGOS = (
    "Año",
    "Semana",
    "Territorio",
    "Zona",
    "Cliente_Unico",
    "Fecha_Recepción",
    "Segmento",
    "Gerencia",
    "Tipo_Cartera",
    "Producto",
    "Campaña",
    "Gestor",
    "Días_de_Atraso",
    "Semanas_de_Atraso",
    "Plan_de_Pago",
    "Fecha_de_Gestion",
    "Recuperación_por_Gestión",
    "Concepto_Cálculo",
    "Cargos_Automáticos",
    "Captación",
    "Cobranza_Total",
    "Porcentaje_Comision",
    "Monto_Comision",
)
RECUPERADO = "Recuperación_por_Gestión"
RECEPCION = "Fecha_Recepción"


def _movimientos(n: int = 200, semilla: int = 1) -> pd.DataFrame:
    """Los movimientos de la semana que termina en el corte, de una cartera de n cuentas, como
    texto, con los repetidos y los ajustes que trae el generador."""
    estado = estado_inicial(n, semilla=semilla, fecha_corte=CORTE)
    tabla = tabla_pagos(estado, semilla=semilla, desde=DESDE, hasta=CORTE)
    return _texto(tabla)


def _texto(tabla: pa.Table) -> pd.DataFrame:
    return tabla.to_pandas(types_mapper={pa.string(): pd.StringDtype()}.get)


def _escribir(tmp_path, datos: pd.DataFrame, nombre: str = "pagos.csv"):
    return escribir_pagos(datos, tmp_path / nombre).ruta


def _conformado(ingesta: IngestaPagos, almacen) -> tuple[DatasetConformado, pa.Table]:
    with sesion() as s:
        dataset = s.exec(
            select(DatasetConformado).where(DatasetConformado.ingesta_pagos_id == ingesta.id)
        ).one()
        parquet = s.get_one(ArtefactoFuente, dataset.artefacto_conformado_id)
    almacen.verificar(parquet.sha256, parquet.tamano_bytes)
    return dataset, pq.read_table(almacen.ruta(parquet.sha256))


def _datasets(ingesta: IngestaPagos) -> int:
    with sesion() as s:
        return s.exec(
            select(func.count())
            .select_from(DatasetConformado)
            .where(DatasetConformado.ingesta_pagos_id == ingesta.id)
        ).one()


def _rechazos(ingesta: IngestaPagos) -> dict[int, RechazoPago]:
    with sesion() as s:
        filas = s.exec(select(RechazoPago).where(RechazoPago.ingesta_pagos_id == ingesta.id)).all()
    return {r.fila: r for r in filas}


def _ingesta(ingesta_id: int) -> IngestaPagos:
    with sesion() as s:
        return s.get_one(IngestaPagos, ingesta_id)


def _cuantos(modelo) -> int:
    with sesion() as s:
        return s.exec(select(func.count()).select_from(modelo)).one()


def _trabajos() -> list[TrabajoOrquestacion]:
    with sesion() as s:
        return list(s.exec(select(TrabajoOrquestacion).order_by(TrabajoOrquestacion.id)).all())


def _cambiar(trabajo_id: int, **valores) -> None:
    with sesion() as s:
        s.execute(
            update(TrabajoOrquestacion)
            .where(TrabajoOrquestacion.id == trabajo_id)
            .values(**valores)
        )
        s.commit()


def _encolar(contenido: bytes, origen: str = "pagos.csv", config: Config = CONFIG):
    with sesion() as s:
        ingesta, trabajo = encolar_ingesta_pagos(
            s, origen=origen, contenido=contenido, tolerancia=None, config=config
        )
        return ingesta.id, trabajo.id


def _canonico(columna, valor):
    """El valor que el dataset conformado tiene que traer para ese texto de la fuente."""
    if valor is None or valor is pd.NA:
        return None
    if columna.tipo == Tipo.ENTERO:
        return int(valor)
    if columna.tipo == Tipo.IMPORTE:
        return Decimal(valor)
    if columna.tipo == Tipo.DECIMAL:
        return float(valor)
    if columna.tipo == Tipo.FECHA_HORA:
        return datetime.fromisoformat(valor)
    return valor


# --- el contrato --------------------------------------------------------------------------------


def test_pagos_v1_son_exactamente_las_23_columnas_del_contrato_fuente():
    assert VERSION_CONTRATO_PAGOS == CONTRATO_PAGOS.version == "pagos/v1"
    assert CONTRATO_PAGOS.nombres == COLUMNAS_PAGOS
    assert len(CONTRATO_PAGOS.columnas) == 23
    # Una fila es un movimiento: no hay llave unica, y ninguna columna tecnica es de la fuente.
    assert CONTRATO_PAGOS.llave_unica is None
    assert not any(nombre.startswith("_") for nombre in CONTRATO_PAGOS.nombres)
    # La llave historica de deduplicacion solo se documenta: son columnas del contrato.
    assert LLAVE_HISTORICA == ("Cliente_Unico", RECEPCION, RECUPERADO)
    requeridas = {c.nombre for c in CONTRATO_PAGOS.columnas if c.requerida}
    assert requeridas == set(LLAVE_HISTORICA)


def test_cada_movimiento_se_juzga_por_los_tipos_de_sus_columnas():
    base = _movimientos(40, semilla=11).drop_duplicates().head(1).iloc[0]
    casos = {
        2: {},
        3: {RECUPERADO: "-150.00", "Concepto_Cálculo": "AJUSTE"},
        4: {RECEPCION: "2026-09-30"},
        5: {RECEPCION: "2026-09-30T08:15:00.250"},
        6: {RECUPERADO: "12.345"},
        7: {RECUPERADO: "12,50"},
        8: {RECEPCION: "30/09/2026 08:15"},
        9: {"Cliente_Unico": None},
        10: {RECUPERADO: None},
        11: {"Días_de_Atraso": "-3"},
        12: {"Semana": "54"},
        13: {"Porcentaje_Comision": "ocho"},
        14: {"Fecha_de_Gestion": "ayer"},
    }
    datos = pd.DataFrame(
        [{**base.to_dict(), **cambios} for cambios in casos.values()], index=list(casos)
    ).astype("string")

    juicio = juzgar_lote(datos, CONTRATO_PAGOS)

    # Un ajuste negativo es un movimiento valido: interpretar el signo es de un motor posterior.
    # Una fecha sin hora es la medianoche; una fraccion de segundo se conserva.
    assert list(juicio.canonicos.index) == [2, 3, 4, 5]
    assert juicio.canonicos.loc[3, RECUPERADO] == "-150.00"
    assert juicio.canonicos.loc[4, RECEPCION] == "2026-09-30T00:00:00"
    assert juicio.canonicos.loc[5, RECEPCION] == "2026-09-30T08:15:00.250000"
    motivos = {fila: [(m.campo, m.regla) for m in ms] for fila, ms in juicio.rechazos.items()}
    assert motivos == {
        6: [(RECUPERADO, "importe(2 decimales)")],
        7: [(RECUPERADO, "importe")],
        8: [(RECEPCION, "fecha_hora(AAAA-MM-DD[THH:MM:SS])")],
        9: [("Cliente_Unico", "requerido")],
        10: [(RECUPERADO, "requerido")],
        11: [("Días_de_Atraso", "rango(0..None)")],
        12: [("Semana", "rango(1..53)")],
        13: [("Porcentaje_Comision", "decimal")],
        14: [("Fecha_de_Gestion", "fecha_hora(AAAA-MM-DD[THH:MM:SS])")],
    }


@pytest.mark.parametrize(
    ("leidas", "rechazadas", "tolerancia", "estado"),
    [
        (100, 0, 0.0, EXITOSA),
        (100, 1, 0.0, RECHAZADA),
        (100, 1, 0.05, EXITOSA),
        (100, 6, 0.05, RECHAZADA),
        (3, 3, 0.5, RECHAZADA),
    ],
    ids=["limpio", "uno-sin-tolerancia", "uno-con-tolerancia", "demasiados", "ninguno-valido"],
)
def test_la_barrera_de_pagos_acepta_todo_o_nada(leidas, rechazadas, tolerancia, estado):
    assert decidir_pagos(leidas, rechazadas, tolerancia)[0] == estado


def test_sin_movimientos_no_hay_nada_que_juzgar():
    with pytest.raises(ValueError, match="error de lectura"):
        decidir_pagos(0, 0, 0.0)


# --- el generador -------------------------------------------------------------------------------


def test_el_generador_de_pagos_es_determinista_y_cumple_el_contrato():
    estado = estado_inicial(300, semilla=4, fecha_corte=CORTE)

    tabla = tabla_pagos(estado, semilla=4, desde=DESDE, hasta=CORTE)

    assert tabla.equals(tabla_pagos(estado, semilla=4, desde=DESDE, hasta=CORTE))
    assert not tabla.equals(tabla_pagos(estado, semilla=5, desde=DESDE, hasta=CORTE))
    assert tuple(tabla.column_names) == COLUMNAS_PAGOS
    datos = _texto(tabla)
    juicio = juzgar_lote(datos.set_axis(range(2, len(datos) + 2)), CONTRATO_PAGOS)
    assert juicio.rechazos == {}
    assert len(juicio.canonicos) == len(datos) > 0


def test_los_pagos_generados_son_coherentes_con_su_cartera_y_su_periodo():
    estado = estado_inicial(2000, semilla=6, fecha_corte=CORTE)
    pagos = _texto(tabla_pagos(estado, semilla=6, desde=DESDE, hasta=CORTE))
    cartera = _texto(tabla_cartera(estado, semilla=6, fecha_corte=CORTE)).set_index("CLIENTE_UNICO")

    # Paga una parte de las cuentas, algunas mas de una vez, siempre dentro del periodo.
    clientes = pagos["Cliente_Unico"]
    assert set(clientes) < set(cartera.index)
    assert (clientes.value_counts() > 1).any()
    recepcion = pd.to_datetime(pagos[RECEPCION])
    assert recepcion.min() >= pd.Timestamp(DESDE)
    assert recepcion.max() < pd.Timestamp(CORTE + timedelta(days=1))
    assert recepcion.is_monotonic_increasing
    # El anio y la semana son los ISO de la recepcion; la gestion, si la hay, es anterior.
    iso = recepcion.dt.isocalendar()
    assert (pagos["Año"].astype(int) == iso["year"]).all()
    assert (pagos["Semana"].astype(int) == iso["week"]).all()
    gestion = pd.to_datetime(pagos["Fecha_de_Gestion"])
    assert gestion.isna().any() and (gestion.dropna() <= recepcion[gestion.notna()]).all()
    # Lo que el acreedor sabia de la cuenta es lo de la cartera de ese corte.
    de_su_cuenta = cartera.loc[clientes]
    assert (pagos["Producto"].to_numpy() == de_su_cuenta["PRODUCTO"].to_numpy()).all()
    assert (pagos["Días_de_Atraso"].to_numpy() == de_su_cuenta["DIAS_ATRASO"].to_numpy()).all()
    # Los importes cuadran: cobranza = recuperado + cargos; comision = cobranza x porcentaje.
    for _, fila in pagos.iterrows():
        recuperado = Decimal(fila[RECUPERADO])
        cobranza = Decimal(fila["Cobranza_Total"])
        assert cobranza == recuperado + Decimal(fila["Cargos_Automáticos"])
        assert Decimal(fila["Captación"]) == recuperado
        comision = (cobranza * Decimal(fila["Porcentaje_Comision"])).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_EVEN
        )
        assert Decimal(fila["Monto_Comision"]) == comision
    # Trae repetidos exactos (la llave historica) y ajustes negativos, a proposito.
    assert pagos.duplicated().any()
    assert pagos[list(LLAVE_HISTORICA)].duplicated().sum() == pagos.duplicated().sum()
    ajustes = pagos[pagos["Concepto_Cálculo"] == "AJUSTE"]
    assert len(ajustes) > 0 and (ajustes[RECUPERADO].map(Decimal) < 0).all()


def test_un_periodo_al_reves_no_se_genera():
    estado = estado_inicial(10, semilla=1, fecha_corte=CORTE)

    with pytest.raises(ValueError, match="termina antes de empezar"):
        tabla_pagos(estado, semilla=1, desde=CORTE, hasta=DESDE)


def test_escribir_pagos_solo_en_formatos_que_se_pueden_ingerir(tmp_path):
    with pytest.raises(ValueError, match="Formato no soportado"):
        escribir_pagos(_movimientos(20), tmp_path / "pagos.parquet")


# --- aceptar: todos los movimientos, sin deduplicar ----------------------------------------------


@en_la_base
@pytest.mark.parametrize("extension", ["csv", "zip", "xlsx"])
def test_un_archivo_de_pagos_valido_se_acepta_entero(tmp_path, almacen, extension):
    movimientos = _movimientos(150, semilla=2)
    ruta = _escribir(tmp_path, movimientos, f"pagos.{extension}")

    ingesta = ingerir_pagos(ruta)

    n = len(movimientos)
    assert ingesta.estado == EXITOSA, ingesta.detalle
    assert (ingesta.filas_leidas, ingesta.filas_validas, ingesta.filas_rechazadas) == (n, n, 0)
    assert (ingesta.version_contrato, ingesta.tolerancia_rechazo) == ("pagos/v1", 0.0)
    assert (ingesta.despacho_id, ingesta.cartera_id) == ("DSP_001", "CARTERA_PRINCIPAL")
    assert ingesta.detalle.startswith(f"Se aceptaron {n:,} movimientos.")
    assert ingesta.terminada_en is not None and len(ingesta.firma_contenido) == 64
    # El linaje: el artefacto original, intacto, y el conformado con todas las filas validas.
    dataset, tabla = _conformado(ingesta, almacen)
    with sesion() as s:
        original = s.get_one(ArtefactoFuente, ingesta.artefacto_fuente_id)
    almacen.verificar(original.sha256, original.tamano_bytes)
    assert (original.sha256, original.formato) == (ingesta.firma, extension)
    assert dataset.artefacto_original_id == original.id and dataset.corrida_id is None
    assert (dataset.contrato, dataset.filas, dataset.columnas) == ("pagos/v1", n, 23)
    assert dataset.firma_contenido == ingesta.firma_contenido
    assert tabla.num_rows == n
    assert tabla.column_names == [*COLUMNAS_PAGOS, COLUMNA_FILA, COLUMNA_HOJA]
    assert tabla.schema.metadata[b"motor_cartera.contrato"] == b"pagos/v1"
    # No es una corrida: no publica cuentas ni crea ninguna corrida.
    assert _cuantos(Corrida) == 0


@en_la_base
def test_el_conformado_trae_cada_movimiento_tipado_y_atado_a_su_fila(tmp_path, almacen):
    movimientos = _movimientos(120, semilla=3)
    ingesta = ingerir_pagos(_escribir(tmp_path, movimientos))

    _, tabla = _conformado(ingesta, almacen)

    assert tabla.schema.field(RECUPERADO).type == pa.decimal128(14, 2)
    assert tabla.schema.field(RECEPCION).type == pa.timestamp("us")
    assert tabla.schema.field("Año").type == pa.int64()
    assert tabla.schema.field("Porcentaje_Comision").type == pa.float64()
    registros = tabla.to_pylist()
    # En un csv, el movimiento de la fila 2 del archivo es el primero de los datos.
    assert sorted(r[COLUMNA_FILA] for r in registros) == list(range(2, len(movimientos) + 2))
    for registro in registros:
        fuente = movimientos.iloc[registro[COLUMNA_FILA] - 2]
        assert registro[COLUMNA_HOJA] is None
        for columna in CONTRATO_PAGOS.columnas:
            esperado = _canonico(columna, fuente[columna.nombre])
            assert registro[columna.nombre] == esperado, (registro[COLUMNA_FILA], columna.nombre)


@en_la_base
def test_dos_movimientos_identicos_son_dos_movimientos(tmp_path, almacen):
    unicos = _movimientos(60, semilla=4).drop_duplicates().head(5)
    # El segundo movimiento, tres veces: mismo cliente, mismo segundo, mismo importe.
    repetidos = pd.concat([unicos, unicos.iloc[[1, 1]]], ignore_index=True)

    ingesta = ingerir_pagos(_escribir(tmp_path, repetidos))

    assert ingesta.estado == EXITOSA, ingesta.detalle
    assert (ingesta.filas_leidas, ingesta.filas_validas, ingesta.filas_rechazadas) == (7, 7, 0)
    _, tabla = _conformado(ingesta, almacen)
    assert tabla.num_rows == 7
    llave = tabla.select(list(LLAVE_HISTORICA)).to_pandas()
    assert llave.duplicated(keep=False).sum() == 3
    filas = llave.assign(fila=tabla[COLUMNA_FILA].to_pylist())[llave.duplicated(keep=False)]
    assert sorted(filas["fila"]) == [3, 7, 8]


@en_la_base
def test_la_firma_de_contenido_no_depende_del_formato_ni_del_orden(tmp_path):
    generados = _movimientos(100, semilla=5)
    movimientos = pd.concat([generados, generados.iloc[[3]]], ignore_index=True)  # un repetido
    variantes = {
        "pagos.csv": movimientos,
        "pagos.zip": movimientos,
        "pagos.xlsx": movimientos,
        "barajado.csv": movimientos.sample(frac=1, random_state=1),
        "columnas.csv": movimientos[list(reversed(COLUMNAS_PAGOS))],
    }

    ingestas = [
        ingerir_pagos(_escribir(tmp_path, datos, nombre)) for nombre, datos in variantes.items()
    ]

    assert {i.estado for i in ingestas} == {EXITOSA}
    assert len({i.firma for i in ingestas}) == len(variantes)  # cinco archivos distintos
    assert len({i.firma_contenido for i in ingestas}) == 1  # con los mismos movimientos
    # Y quitar un repetido cambia el contenido: los repetidos cuentan.
    sin_repetidos = ingerir_pagos(_escribir(tmp_path, movimientos.drop_duplicates(), "u.csv"))
    assert sin_repetidos.firma_contenido != ingestas[0].firma_contenido


# --- la barrera: un movimiento invalido rechaza el archivo ---------------------------------------


def _con_un_invalido(semilla: int) -> tuple[pd.DataFrame, int]:
    movimientos = _movimientos(80, semilla=semilla).reset_index(drop=True)
    movimientos.loc[9, RECUPERADO] = "1,250.00"
    return movimientos, 11  # la fila 11 del csv: el encabezado es la 1


@en_la_base
def test_un_movimiento_invalido_rechaza_el_archivo_entero(tmp_path, almacen):
    movimientos, fila = _con_un_invalido(semilla=6)

    ingesta = ingerir_pagos(_escribir(tmp_path, movimientos))

    n = len(movimientos)
    assert ingesta.estado == RECHAZADA
    assert (ingesta.filas_leidas, ingesta.filas_validas, ingesta.filas_rechazadas) == (n, n - 1, 1)
    assert ingesta.detalle.startswith(f"1 de {n:,} movimientos")
    assert "no se acepto nada" in ingesta.detalle
    # Su rechazo, a la vista, con lo que traia; nada conformado; la evidencia, intacta.
    (rechazo,) = _rechazos(ingesta).values()
    assert rechazo.fila == fila
    assert rechazo.valores[RECUPERADO] == "1,250.00"
    assert set(rechazo.valores) == set(COLUMNAS_PAGOS)
    assert rechazo.motivos == [{"campo": RECUPERADO, "regla": "importe"}]
    assert _datasets(ingesta) == 0
    with sesion() as s:
        original = s.get_one(ArtefactoFuente, ingesta.artefacto_fuente_id)
    almacen.verificar(original.sha256, original.tamano_bytes)


@en_la_base
def test_con_una_tolerancia_explicita_se_aceptan_los_validos_y_no_el_invalido(tmp_path, almacen):
    movimientos, fila = _con_un_invalido(semilla=7)

    ingesta = ingerir_pagos(_escribir(tmp_path, movimientos), tolerancia=0.05)

    assert ingesta.estado == EXITOSA, ingesta.detalle
    assert ingesta.tolerancia_rechazo == 0.05
    assert ingesta.filas_rechazadas == 1 and list(_rechazos(ingesta)) == [fila]
    assert "dentro de la tolerancia de 5.0%" in ingesta.detalle
    _, tabla = _conformado(ingesta, almacen)
    assert tabla.num_rows == len(movimientos) - 1
    assert fila not in tabla[COLUMNA_FILA].to_pylist()


@en_la_base
def test_un_archivo_rechazado_se_puede_corregir_y_volver_a_subir(tmp_path):
    movimientos, _ = _con_un_invalido(semilla=8)
    rechazada = ingerir_pagos(_escribir(tmp_path, movimientos, "pagos.csv"))
    # El mismo archivo otra vez: no se acepto, asi que se puede volver a juzgar.
    otra_vez = ingerir_pagos(_escribir(tmp_path, movimientos, "pagos.csv"))

    corregido = movimientos.copy()
    corregido.loc[9, RECUPERADO] = "1250.00"
    aceptada = ingerir_pagos(_escribir(tmp_path, corregido, "corregido.csv"))

    assert (rechazada.estado, otra_vez.estado, aceptada.estado) == (RECHAZADA, RECHAZADA, EXITOSA)
    assert rechazada.firma == otra_vez.firma != aceptada.firma
    with pytest.raises(PagosDuplicados, match=str(aceptada.pagos_run_id)):
        ingerir_pagos(_escribir(tmp_path, corregido, "copia.csv"))


# --- la estructura: exacta, o no se juzga nada ---------------------------------------------------


@en_la_base
@pytest.mark.parametrize(
    ("cambiar", "motivo"),
    [
        (lambda d: d.drop(columns=["Gestor"]), "faltan 1 columnas del contrato: Gestor"),
        (
            lambda d: d.assign(_SOURCE_FILE="pagos_semana.xlsx"),
            "sobran 1 columnas que el contrato no tiene: _SOURCE_FILE",
        ),
        (
            lambda d: d.assign(ID_MOVIMIENTO="1"),
            "sobran 1 columnas que el contrato no tiene: ID_MOVIMIENTO",
        ),
        (lambda d: d.rename(columns={"Campaña": "Campana"}), "faltan 1 columnas del contrato"),
    ],
    ids=["falta-una", "columna-tecnica-del-sistema-anterior", "identificador", "sin-acento"],
)
def test_una_estructura_distinta_de_pagos_v1_falla_sin_juzgar_nada(tmp_path, cambiar, motivo):
    ingesta = ingerir_pagos(_escribir(tmp_path, cambiar(_movimientos(40, semilla=9))))

    assert ingesta.estado == FALLIDA
    assert "no tiene la estructura de pagos/v1, que son 23 columnas exactas" in ingesta.detalle
    assert motivo in ingesta.detalle
    assert (ingesta.filas_leidas, ingesta.filas_validas, ingesta.filas_rechazadas) == (0, 0, 0)
    assert _rechazos(ingesta) == {} and _datasets(ingesta) == 0


@en_la_base
def test_un_encabezado_repetido_es_un_error_de_estructura(tmp_path):
    texto = _movimientos(30, semilla=10).to_csv(index=False)
    encabezado, resto = texto.split("\n", 1)
    ruta = tmp_path / "pagos.csv"
    ruta.write_text(encabezado.replace("Zona", "Gestor") + "\n" + resto, encoding="utf-8")

    ingesta = ingerir_pagos(ruta)

    assert ingesta.estado == FALLIDA
    assert "encabezados repetidos: Gestor" in ingesta.detalle


@en_la_base
def test_un_archivo_con_solo_el_encabezado_falla(tmp_path):
    ruta = tmp_path / "pagos.csv"
    ruta.write_text(",".join(COLUMNAS_PAGOS) + "\n", encoding="utf-8")

    ingesta = ingerir_pagos(ruta)

    assert ingesta.estado == FALLIDA
    assert "no trae ningun movimiento" in ingesta.detalle


@en_la_base
def test_un_artefacto_danado_deja_la_ingesta_fallida(tmp_path, almacen):
    # Un archivo propio (semilla 12): el almacen de las pruebas es compartido y este se dana.
    contenido = _escribir(tmp_path, _movimientos(50, semilla=12)).read_bytes()
    ingesta_id, _ = _encolar(contenido)
    objeto = almacen.ruta(_ingesta(ingesta_id).firma)
    objeto.chmod(0o600)
    objeto.write_bytes(contenido.replace(b"TERRITORIO", b"TERRITORIA", 1))

    procesar_ingesta_pagos(ingesta_id)

    terminada = _ingesta(ingesta_id)
    assert terminada.estado == FALLIDA
    assert "esta danado" in terminada.detalle


@en_la_base
def test_una_ingesta_terminada_no_se_vuelve_a_procesar(tmp_path, caplog):
    ingesta = ingerir_pagos(_escribir(tmp_path, _movimientos(40, semilla=13)))

    procesar_ingesta_pagos(ingesta.id)

    assert _ingesta(ingesta.id).model_dump() == ingesta.model_dump()
    assert _datasets(ingesta) == 1
    assert any("ya termino EXITOSA" in r.getMessage() for r in caplog.records)


# --- la cola durable ----------------------------------------------------------------------------


@en_la_base
def test_el_worker_toma_la_ingesta_de_pagos_y_no_encadena_nada(tmp_path, trabajar):
    contenido = _escribir(tmp_path, _movimientos(90, semilla=14)).read_bytes()
    ingesta_id, trabajo_id = _encolar(contenido)
    (trabajo,) = _trabajos()
    assert (trabajo.tipo, trabajo.estado, trabajo.ingesta_pagos_id) == (
        TipoTrabajo.INGESTA_PAGOS,
        EstadoTrabajo.PENDIENTE,
        ingesta_id,
    )
    assert trabajo.flujo_id is None and trabajo.corrida_id is None
    assert _ingesta(ingesta_id).estado == EstadoIngestaPagos.EN_PROCESO

    procesado, historia = trabajar()

    assert (procesado.tipo, procesado.intentos, procesado.estado) == (
        TipoTrabajo.INGESTA_PAGOS,
        1,
        EstadoTrabajo.COMPLETADO,
    )
    assert _ingesta(ingesta_id).estado == EXITOSA
    # El suyo y el de la historia de sus pagos, que se abre con su dataset: ninguna decision,
    # ningun flujo, ninguna corrida.
    assert (historia.tipo, historia.estado) == (TipoTrabajo.HISTORIA, EstadoTrabajo.COMPLETADO)
    assert [t.id for t in _trabajos()][0] == trabajo_id
    assert [t.tipo for t in _trabajos()] == [TipoTrabajo.INGESTA_PAGOS, TipoTrabajo.HISTORIA]
    assert _cuantos(FlujoOrquestacion) == _cuantos(Corrida) == 0


@en_la_base
def test_si_el_worker_muere_otro_termina_la_ingesta_al_vencer_el_lease(tmp_path):
    contenido = _escribir(tmp_path, _movimientos(70, semilla=15)).read_bytes()
    ingesta_id, trabajo_id = _encolar(contenido)
    tomado = cola.reclamar("worker-muerto", 60)
    assert tomado.id == trabajo_id
    assert procesar_un_trabajo("worker-b", CONFIG) is None  # su lease sigue vigente

    _cambiar(trabajo_id, lease_hasta=func.now() - timedelta(seconds=1))
    procesado = procesar_un_trabajo("worker-b", CONFIG)

    assert (procesado.intentos, procesado.estado) == (2, EstadoTrabajo.COMPLETADO)
    (trabajo,) = [t for t in _trabajos() if t.id == trabajo_id]
    assert trabajo.ultimo_error == cola.LEASE_VENCIDO
    assert _ingesta(ingesta_id).estado == EXITOSA


@en_la_base
def test_si_el_worker_muere_despues_de_aceptar_otro_cierra_sin_duplicar(tmp_path):
    contenido = _escribir(tmp_path, _movimientos(70, semilla=16)).read_bytes()
    ingesta_id, trabajo_id = _encolar(contenido)
    tomado = cola.reclamar("worker-muerto", 60)
    procesar_ingesta_pagos(tomado.objetivo_id)  # la ingesta confirma; el worker no cierra
    aceptada = _ingesta(ingesta_id)
    assert aceptada.estado == EXITOSA

    _cambiar(trabajo_id, lease_hasta=func.now() - timedelta(seconds=1))
    procesado = procesar_un_trabajo("worker-b", CONFIG)

    assert (procesado.intentos, procesado.estado) == (2, EstadoTrabajo.COMPLETADO)
    assert _ingesta(ingesta_id).model_dump() == aceptada.model_dump()
    assert _cuantos(DatasetConformado) == 1


@en_la_base
def test_un_error_del_worker_reintenta_y_al_agotarse_la_ingesta_queda_fallida(
    tmp_path, monkeypatch, almacen
):
    config = Config(worker_max_intentos=2, worker_backoff_segundos=30)
    contenido = _escribir(tmp_path, _movimientos(60, semilla=17)).read_bytes()
    ingesta_id, trabajo_id = _encolar(contenido, config=config)
    llamadas = []

    def siempre_falla(ingesta_id: int) -> None:
        llamadas.append(ingesta_id)
        raise ConnectionError("la base no contesta")

    monkeypatch.setitem(worker.MANEJADORES, TipoTrabajo.INGESTA_PAGOS, siempre_falla)

    primero = procesar_un_trabajo("worker-a", config)
    assert (primero.intentos, primero.estado) == (1, EstadoTrabajo.PENDIENTE)
    assert procesar_un_trabajo("worker-a", config) is None  # espera su turno
    assert _ingesta(ingesta_id).estado == EstadoIngestaPagos.EN_PROCESO
    _cambiar(trabajo_id, disponible_desde=func.now())
    ultimo = procesar_un_trabajo("worker-a", config)

    assert (ultimo.intentos, ultimo.estado) == (2, EstadoTrabajo.FALLIDO)
    assert llamadas == [ingesta_id, ingesta_id]
    (trabajo,) = _trabajos()
    assert trabajo.ultimo_error == "Error de worker (ConnectionError); ver la bitacora."
    fallida = _ingesta(ingesta_id)
    assert (fallida.estado, fallida.detalle) == (
        FALLIDA,
        "Su trabajo agoto los 2 intentos de la cola sin que terminara; ver la bitacora del worker.",
    )
    # El archivo se queda: es la evidencia de lo que se recibio.
    almacen.verificar(fallida.firma, len(contenido))


@en_la_base
def test_una_ingesta_sin_su_objeto_en_el_almacen_se_reintenta_hasta_que_vuelve(tmp_path, almacen):
    # Como un volumen que no se monto. Un archivo propio (semilla 18), porque se borra.
    contenido = _escribir(tmp_path, _movimientos(55, semilla=18)).read_bytes()
    ingesta_id, trabajo_id = _encolar(contenido)
    ruta = almacen.ruta(_ingesta(ingesta_id).firma)
    ruta.chmod(0o600)
    ruta.unlink()

    procesado = procesar_un_trabajo("worker-a", CONFIG)

    assert procesado.estado == EstadoTrabajo.PENDIENTE
    (trabajo,) = _trabajos()
    assert trabajo.ultimo_error == "Error de worker (ArtefactoFaltante); ver la bitacora."
    assert _ingesta(ingesta_id).estado == EstadoIngestaPagos.EN_PROCESO

    almacen.guardar(io.BytesIO(contenido))
    _cambiar(trabajo_id, disponible_desde=func.now())
    procesado = procesar_un_trabajo("worker-a", CONFIG)

    assert (procesado.intentos, procesado.estado) == (2, EstadoTrabajo.COMPLETADO)
    assert _ingesta(ingesta_id).estado == EXITOSA


@en_la_base
def test_dos_workers_nunca_procesan_la_misma_ingesta(tmp_path, monkeypatch):
    # Dos workers de verdad, cada uno en su hilo y con su conexion. Las dos ingestas se esperan una
    # a la otra: cada worker tiene que estar en la suya al mismo tiempo, y no en la misma.
    ingestas = [
        _encolar(_escribir(tmp_path, _movimientos(n, semilla=19), f"p{n}.csv").read_bytes())[0]
        for n in (60, 75)
    ]
    juntas = threading.Barrier(2, timeout=30)
    candado = threading.Lock()
    ejecutados: list[tuple[str, int]] = []
    ingerir = worker.MANEJADORES[TipoTrabajo.INGESTA_PAGOS]

    def anotado(ingesta_id: int) -> None:
        with candado:
            ejecutados.append((threading.current_thread().name, ingesta_id))
        juntas.wait()
        ingerir(ingesta_id)

    monkeypatch.setitem(worker.MANEJADORES, TipoTrabajo.INGESTA_PAGOS, anotado)
    detener = threading.Event()
    config = Config(worker_poll_segundos=0.05)
    hilos = [
        threading.Thread(
            target=ejecutar_worker,
            args=(config,),
            kwargs={"detener": detener},
            name=nombre,
            daemon=True,
        )
        for nombre in ("worker-a", "worker-b")
    ]
    for hilo in hilos:
        hilo.start()
    try:
        limite = time.monotonic() + 60
        while time.monotonic() < limite:
            if all(_ingesta(i).estado == EXITOSA for i in ingestas):
                break
            time.sleep(0.05)
    finally:
        detener.set()
        for hilo in hilos:
            hilo.join(timeout=30)

    assert [_ingesta(i).estado for i in ingestas] == [EXITOSA, EXITOSA]
    assert sorted(i for _, i in ejecutados) == sorted(ingestas)
    assert {nombre for nombre, _ in ejecutados} == {"worker-a", "worker-b"}
    assert {(t.estado, t.intentos) for t in _trabajos()} == {(EstadoTrabajo.COMPLETADO, 1)}
    assert _cuantos(DatasetConformado) == 2
