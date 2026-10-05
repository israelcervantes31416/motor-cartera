"""La ingesta de cartera/v2 de punta a punta, contra PostgreSQL: estructura exacta, juicio por lotes
con invariantes globales, proyeccion a Cuenta, dataset conformado, CARRIER y el flujo hasta el
ruteo."""

from __future__ import annotations

import hashlib
import io
import tempfile
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest
from sqlmodel import func, select

from motor_cartera.config import Config, config
from motor_cartera.contratos.cartera_v2 import CONTRATO_V2, HOJA_CARTERA
from motor_cartera.db.modelos import (
    ArtefactoFuente,
    Corrida,
    Cuenta,
    DatasetConformado,
    EjecucionRuteo,
    EstadoCorrida,
    EstadoFlujo,
    HojaCompanera,
    Rechazo,
)
from motor_cartera.db.sesion import sesion
from motor_cartera.fuentes.conformado import COLUMNA_FILA, COLUMNA_HOJA, EscritorConformado
from motor_cartera.fuentes.formatos import Formato
from motor_cartera.fuentes.geografia import MUNICIPIO_DESCONOCIDO, resolver
from motor_cartera.fuentes.lotes import abrir_fuente
from motor_cartera.fuentes.proyeccion import VERSION_PROYECCION, rechazos_geograficos
from motor_cartera.generador.oficial import (
    contaminar,
    escribir_cartera,
    estado_inicial,
    generar_cartera_oficial,
)
from motor_cartera.ingesta.cartera_v2 import CARRIER
from motor_cartera.ingesta.corridas import CorridaMalDeclarada, ingerir_archivo
from motor_cartera.ingesta.fuente_oficial import JuicioDeFuente
from motor_cartera.orquestacion.flujo import crear_flujo_ingesta
from motor_cartera.orquestacion.worker import identificador_worker, procesar_un_trabajo

pytestmark = pytest.mark.usefixtures("bd")

CORTE = date(2026, 9, 30)
V2 = "cartera/v2"


def _oficial(tmp_path: Path, nombre: str, n: int = 300, semilla: int = 1, **opciones) -> Path:
    estado = estado_inicial(n, semilla=semilla, fecha_corte=CORTE)
    return escribir_cartera(
        estado, tmp_path / nombre, semilla=semilla, fecha_corte=CORTE, **opciones
    ).ruta


def _csv(tmp_path: Path, nombre: str, datos: pd.DataFrame) -> Path:
    ruta = tmp_path / nombre
    datos.to_csv(ruta, index=False)
    return ruta


def _ingerir(ruta: Path, corte: date = CORTE, **opciones) -> Corrida:
    return ingerir_archivo(ruta, contrato=V2, fecha_corte=corte, **opciones)


def _cuantas(modelo, corrida: Corrida) -> int:
    with sesion() as s:
        return s.exec(
            select(func.count()).select_from(modelo).where(modelo.corrida_id == corrida.id)
        ).one()


def _rechazos(corrida: Corrida) -> dict[int, Rechazo]:
    with sesion() as s:
        filas = s.exec(select(Rechazo).where(Rechazo.corrida_id == corrida.id)).all()
    return {r.fila: r for r in filas}


# --- publicar: xlsx, csv y zip --------------------------------------------------------------------


@pytest.mark.parametrize("extension", ["xlsx", "csv", "zip"])
def test_una_cartera_oficial_valida_publica_todas_sus_cuentas(tmp_path, extension, almacen):
    ruta = _oficial(tmp_path, f"cartera.{extension}", n=250)

    corrida = _ingerir(ruta)

    assert corrida.estado == EstadoCorrida.EXITOSA, corrida.detalle
    assert (corrida.filas_leidas, corrida.filas_validas, corrida.filas_rechazadas) == (250, 250, 0)
    assert (corrida.version_contrato, corrida.version_proyeccion) == (V2, VERSION_PROYECCION)
    assert corrida.fecha_corte == CORTE
    assert _cuantas(Cuenta, corrida) == 250
    assert corrida.detalle.startswith("Se publicaron 250 cuentas.")
    assert f"Proyeccion {VERSION_PROYECCION}" in corrida.detalle
    with sesion() as s:
        dataset = s.exec(
            select(DatasetConformado).where(DatasetConformado.corrida_id == corrida.id)
        ).one()
        parquet = s.get_one(ArtefactoFuente, dataset.artefacto_conformado_id)
        hojas = s.exec(select(HojaCompanera).where(HojaCompanera.corrida_id == corrida.id)).all()
    # El linaje: el artefacto original, el conformado y su firma, que es la de la corrida.
    assert dataset.artefacto_original_id == corrida.artefacto_fuente_id
    assert (dataset.contrato, dataset.filas, dataset.columnas) == (V2, 250, 93)
    assert dataset.firma_contenido == corrida.firma_contenido
    assert parquet.formato == "parquet"
    almacen.verificar(parquet.sha256, parquet.tamano_bytes)
    # CARRIER viene en xlsx y en zip; un csv suelto es una sola tabla.
    if extension == "csv":
        assert hojas == []
    else:
        (hoja,) = hojas
        assert (hoja.columnas, hoja.estructura_reconocida, hoja.advertencias) == (85, True, [])
        assert hoja.filas > 250
        assert "no gobierna la publicacion" in corrida.detalle


def test_cada_cuenta_es_la_proyeccion_exacta_de_su_registro(tmp_path):
    cartera = generar_cartera_oficial(120, semilla=3, fecha_corte=CORTE)
    corrida = _ingerir(_csv(tmp_path, "c.csv", cartera))

    with sesion() as s:
        cuentas = {
            c.cliente_unico: c
            for c in s.exec(select(Cuenta).where(Cuenta.corrida_id == corrida.id)).all()
        }
    resolucion = resolver(cartera["ESTADO_CTE"], cartera["POBLACION_CTE"])
    for i, fila in cartera.iterrows():
        cuenta = cuentas[fila["CLIENTE_UNICO"]]
        assert cuenta.saldo_total == Decimal(fila["SALDO_TOTAL"]).quantize(Decimal("0.01"))
        assert cuenta.dias_atraso == int(fila["DIAS_ATRASO"])
        assert (cuenta.producto, cuenta.canal) == (fila["PRODUCTO"], fila["CANAL"])
        assert (cuenta.cve_entidad, cuenta.cve_municipio) == (
            resolucion.cve_entidad[i],
            resolucion.cve_municipio[i],
        )
        assert cuenta.fecha_corte == CORTE  # la del lote, no una columna del archivo
    assert len(cuentas) == 120


def test_la_misma_cartera_en_xlsx_csv_y_zip_tiene_la_misma_firma_de_contenido(tmp_path):
    estado = estado_inicial(150, semilla=4, fecha_corte=CORTE)
    firmas = []
    for i, extension in enumerate(("xlsx", "csv", "zip")):
        ruta = escribir_cartera(
            estado, tmp_path / f"c.{extension}", semilla=4, fecha_corte=CORTE
        ).ruta
        # Otro corte para cada una: la misma cartera en otro archivo es otra corrida.
        corrida = _ingerir(ruta, corte=date(2026, 9, 30 - i))
        assert corrida.estado == EstadoCorrida.EXITOSA
        firmas.append((corrida.firma, corrida.firma_contenido))

    assert len({firma for firma, _ in firmas}) == 3  # tres archivos distintos
    assert len({contenido for _, contenido in firmas}) == 1  # una sola cartera


def test_el_orden_fisico_de_las_columnas_no_cambia_nada(tmp_path):
    cartera = generar_cartera_oficial(80, semilla=5, fecha_corte=CORTE)
    original = _ingerir(_csv(tmp_path, "a.csv", cartera))
    desordenada = cartera[list(reversed(cartera.columns))]

    otra = _ingerir(_csv(tmp_path, "b.csv", desordenada), corte=date(2026, 10, 7))

    assert otra.estado == EstadoCorrida.EXITOSA
    assert otra.firma != original.firma
    assert otra.firma_contenido == original.firma_contenido


def test_el_conformado_se_reproduce_desde_la_evidencia_original(tmp_path, almacen):
    # Con el artefacto original y el contrato basta para volver a producir el mismo Parquet,
    # byte por byte: no depende de nada que se haya perdido al terminar la corrida.
    ruta = _oficial(tmp_path, "c.zip", n=200, semilla=6)
    corrida = _ingerir(ruta)
    with sesion() as s:
        dataset = s.exec(
            select(DatasetConformado).where(DatasetConformado.corrida_id == corrida.id)
        ).one()
        original = s.get_one(ArtefactoFuente, dataset.artefacto_original_id)
        publicado = s.get_one(ArtefactoFuente, dataset.artefacto_conformado_id)

    with (
        tempfile.TemporaryDirectory() as temporal,
        almacen.como_archivo(original.sha256) as archivo,
    ):
        directorio = Path(temporal)
        escritor = EscritorConformado(
            directorio / "otra_vez.parquet",
            CONTRATO_V2,
            {
                "contrato": V2,
                "artefacto_original": original.sha256,
                "fecha_corte": CORTE.isoformat(),
                "proyeccion": VERSION_PROYECCION,
            },
        )
        with abrir_fuente(
            archivo,
            Formato.ZIP,
            original.nombre_original,
            CONTRATO_V2,
            filas_por_lote=config.filas_por_lote,
            hoja_principal=HOJA_CARTERA,
            companera=CARRIER,
        ) as fuente:
            juicio = JuicioDeFuente(
                CONTRATO_V2,
                directorio,
                rechazar=lambda r: pytest.fail(f"rechazo inesperado: {r}"),
                validar_extra=rechazos_geograficos,
            )
            juicio.primera_pasada(fuente.lotes())
        firma = juicio.segunda_pasada(escritor.escribir)
        escritor.cerrar()
        otra_vez = hashlib.sha256(escritor.ruta.read_bytes()).hexdigest()

    assert otra_vez == publicado.sha256
    assert firma.hexdigest() == corrida.firma_contenido
    # Y cada registro del conformado dice de que fila del original salio.
    import pyarrow.parquet as pq

    with almacen.abrir(publicado.sha256) as objeto:
        tabla = pq.read_table(io.BytesIO(objeto.read()))
    assert tabla[COLUMNA_FILA].to_pylist() == list(range(2, 202))
    assert set(tabla[COLUMNA_HOJA].to_pylist()) == {"CARTERA.csv"}


# --- la estructura, antes que los registros ------------------------------------------------------


@pytest.mark.parametrize(
    ("cambio", "motivo"),
    [
        (lambda df: df.drop(columns=["CANAL"]), "faltan 1 columnas del contrato: CANAL"),
        (lambda df: df.assign(COMENTARIOS="x"), "sobran 1 columnas que el contrato no tiene"),
        (lambda df: df.rename(columns={"SALDO": "saldo"}), "faltan 1 columnas del contrato: SALDO"),
    ],
    ids=["falta", "sobra", "otro-nombre"],
)
def test_una_desviacion_de_estructura_deja_la_corrida_fallida_sin_juzgar_nada(
    tmp_path, cambio, motivo, almacen
):
    cartera = cambio(generar_cartera_oficial(50, semilla=7, fecha_corte=CORTE))

    corrida = _ingerir(_csv(tmp_path, "c.csv", cartera))

    assert corrida.estado == EstadoCorrida.FALLIDA
    assert motivo in corrida.detalle
    assert "no tiene la estructura de cartera/v2" in corrida.detalle
    assert _cuantas(Cuenta, corrida) == _cuantas(Rechazo, corrida) == 0
    assert _cuantas(DatasetConformado, corrida) == 0
    with sesion() as s:  # el archivo que no se pudo juzgar tambien es evidencia
        almacen.verificar(s.get_one(ArtefactoFuente, corrida.artefacto_fuente_id).sha256)


def test_un_encabezado_repetido_es_un_error_de_estructura(tmp_path):
    cartera = generar_cartera_oficial(20, semilla=8, fecha_corte=CORTE)
    texto = cartera.to_csv(index=False).replace("GERENCIA", "ZONA", 1)

    ruta = tmp_path / "c.csv"
    ruta.write_text(texto, encoding="utf-8")
    corrida = _ingerir(ruta)

    assert corrida.estado == EstadoCorrida.FALLIDA
    assert "encabezados repetidos: ZONA" in corrida.detalle


def test_un_xlsx_sin_hoja_cartera_no_se_juzga(tmp_path):
    ruta = _oficial(tmp_path, "c.xlsx", n=20, con_carrier=False)
    import openpyxl

    libro = openpyxl.load_workbook(ruta)
    libro["CARTERA"].title = "Hoja1"
    libro.save(ruta)

    corrida = _ingerir(ruta)

    assert corrida.estado == EstadoCorrida.FALLIDA
    assert "no tiene la hoja CARTERA" in corrida.detalle


def test_un_archivo_de_v1_declarado_v2_y_al_reves_fallan_sin_adivinar(tmp_path):
    from motor_cartera.generador.sintetico import generar_archivo

    v1 = generar_archivo(tmp_path / "v1.csv", n=30, semilla=1, fecha_corte=CORTE)
    v2 = _csv(tmp_path, "v2.csv", generar_cartera_oficial(30, semilla=9, fecha_corte=CORTE))

    como_v2 = _ingerir(v1)
    como_v1 = ingerir_archivo(v2)

    assert como_v2.estado == como_v1.estado == EstadoCorrida.FALLIDA
    assert "faltan 93 columnas del contrato" in como_v2.detalle
    assert "Hay columnas que dicen ser la misma" in como_v1.detalle


def test_cartera_v2_sin_fecha_de_corte_o_v1_con_ella_no_se_registra(tmp_path, objetos):
    ruta = _oficial(tmp_path, "c.csv", n=10, semilla=10)
    antes = objetos()

    with pytest.raises(CorridaMalDeclarada, match="se declara con la corrida"):
        ingerir_archivo(ruta, contrato=V2)
    with pytest.raises(CorridaMalDeclarada, match="viene en el archivo"):
        ingerir_archivo(ruta, fecha_corte=CORTE)
    with pytest.raises(CorridaMalDeclarada, match="Contrato desconocido"):
        ingerir_archivo(ruta, contrato="cartera/v9", fecha_corte=CORTE)

    assert objetos() == antes
    with sesion() as s:
        assert s.exec(select(func.count()).select_from(Corrida)).one() == 0


# --- el juicio por registro, con invariantes globales -------------------------------------------


def test_un_cliente_unico_repetido_en_lotes_distintos_rechaza_todas_sus_copias(
    tmp_path, monkeypatch
):
    # Lotes de 100 filas: las copias quedan en lotes distintos y aun asi se encuentran.
    monkeypatch.setattr(config, "filas_por_lote", 100)
    cartera = generar_cartera_oficial(300, semilla=11, fecha_corte=CORTE).reset_index(drop=True)
    cartera.loc[250, "CLIENTE_UNICO"] = cartera.loc[10, "CLIENTE_UNICO"]
    # Y una tercera copia que ademas trae un saldo invalido.
    cartera.loc[150, "CLIENTE_UNICO"] = cartera.loc[10, "CLIENTE_UNICO"]
    cartera.loc[150, "SALDO_TOTAL"] = "N/D"

    corrida = _ingerir(_csv(tmp_path, "c.csv", cartera), tolerancia=0.05)

    assert corrida.estado == EstadoCorrida.EXITOSA
    assert (corrida.filas_leidas, corrida.filas_validas, corrida.filas_rechazadas) == (300, 297, 3)
    rechazos = _rechazos(corrida)
    # La fila del archivo es la posicion mas 2: el encabezado es la fila 1.
    assert set(rechazos) == {12, 152, 252}
    duplicado = {"campo": "CLIENTE_UNICO", "regla": "unico_en_el_corte"}
    assert rechazos[12].motivos == rechazos[252].motivos == [duplicado]
    assert rechazos[152].motivos == [duplicado, {"campo": "SALDO_TOTAL", "regla": "importe"}]
    assert rechazos[152].valores["SALDO_TOTAL"] == "N/D"
    assert len(rechazos[12].valores) == 93
    assert _cuantas(Cuenta, corrida) == 297


def test_el_mismo_cliente_en_cortes_distintos_es_valido(tmp_path):
    estado = estado_inicial(60, semilla=12, fecha_corte=CORTE)
    uno = escribir_cartera(estado, tmp_path / "a.csv", semilla=12, fecha_corte=CORTE).ruta
    # Una semana despues, las mismas cuentas con otro atraso.
    otra_semana = date(2026, 10, 7)
    from dataclasses import replace

    despues = replace(estado, dias_atraso=estado.dias_atraso + 7)
    dos = escribir_cartera(despues, tmp_path / "b.csv", semilla=12, fecha_corte=otra_semana).ruta

    a, b = _ingerir(uno), _ingerir(dos, corte=otra_semana)

    assert a.estado == b.estado == EstadoCorrida.EXITOSA
    with sesion() as s:
        clientes = s.exec(select(Cuenta.cliente_unico, Cuenta.fecha_corte)).all()
    assert len(clientes) == 120
    assert len({cliente for cliente, _ in clientes}) == 60


def test_lo_que_el_catalogo_no_resuelve_se_rechaza_con_su_motivo(tmp_path):
    cartera = generar_cartera_oficial(100, semilla=13, fecha_corte=CORTE).reset_index(drop=True)
    cartera.loc[5, "POBLACION_CTE"] = "SAN FRANCISCO TOTIMEHUACAN"  # una localidad, no un municipio

    corrida = _ingerir(_csv(tmp_path, "c.csv", cartera))

    assert corrida.estado == EstadoCorrida.EXITOSA
    assert _rechazos(corrida)[7].motivos == [
        {"campo": "POBLACION_CTE", "regla": MUNICIPIO_DESCONOCIDO}
    ]


def test_demasiados_rechazos_no_publican_nada_pero_dejan_la_evidencia(tmp_path):
    cartera = generar_cartera_oficial(200, semilla=14, fecha_corte=CORTE)
    sucia, filas = contaminar(cartera, 0.10, semilla=14)

    corrida = _ingerir(_csv(tmp_path, "c.csv", sucia), tolerancia=0.05)

    assert corrida.estado == EstadoCorrida.RECHAZADA
    assert corrida.filas_rechazadas == len(filas) == 20
    assert sorted(_rechazos(corrida)) == [f + 2 for f in filas]
    assert _cuantas(Cuenta, corrida) == _cuantas(DatasetConformado, corrida) == 0
    assert len(corrida.firma_contenido) == 64  # se juzgo: su contenido valido tiene firma


def test_un_artefacto_danado_deja_la_corrida_fallida(tmp_path, almacen):
    ruta = _oficial(tmp_path, "c.csv", n=40, semilla=15)
    guardado = ruta.read_bytes()
    from motor_cartera.fuentes.artefactos import almacen_de, guardar_contenido
    from motor_cartera.ingesta.corridas import abrir_corrida, procesar_corrida

    with sesion() as s:
        corrida = abrir_corrida(
            s,
            origen="c.csv",
            guardado=guardar_contenido(almacen_de(config), guardado, "c.csv"),
            contrato=V2,
            fecha_corte=CORTE,
        )
    objeto = almacen.ruta(corrida.firma)
    objeto.chmod(0o600)
    objeto.write_bytes(guardado.replace(b"PUEBLA", b"PUEBLO", 1))

    procesar_corrida(corrida.id)

    with sesion() as s:
        terminada = s.get_one(Corrida, corrida.id)
    assert terminada.estado == EstadoCorrida.FALLIDA
    assert "esta danado" in terminada.detalle


# --- CARRIER -------------------------------------------------------------------------------------


def test_un_carrier_incoherente_deja_advertencias_y_no_bloquea_cartera(tmp_path):
    cartera = generar_cartera_oficial(40, semilla=16, fecha_corte=CORTE)
    carrier = pd.DataFrame(
        {
            "CLIENTE_UNICO": [cartera.loc[0, "CLIENTE_UNICO"], "CU9999999999"],
            "TELEFONO": ["0123456789", "12345"],
        }
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as paquete:
        paquete.writestr("CARTERA.csv", cartera.to_csv(index=False))
        paquete.writestr("CARRIER.csv", carrier.to_csv(index=False))
    ruta = tmp_path / "c.zip"
    ruta.write_bytes(buf.getvalue())

    corrida = _ingerir(ruta)

    assert corrida.estado == EstadoCorrida.EXITOSA
    with sesion() as s:
        (hoja,) = s.exec(select(HojaCompanera).where(HojaCompanera.corrida_id == corrida.id)).all()
    assert (hoja.nombre, hoja.filas, hoja.columnas, hoja.estructura_reconocida) == (
        "CARRIER.csv",
        2,
        2,
        False,
    )
    assert "Le faltan 83 columnas" in hoja.advertencias[0]
    assert "1 filas con un CLIENTE_UNICO que no esta en la tabla principal." in hoja.advertencias
    assert "1 filas con un TELEFONO que no tiene la forma esperada." in hoja.advertencias
    assert "estructura no reconocida" in corrida.detalle


# --- de cartera/v2 al ruteo ----------------------------------------------------------------------


def test_una_cartera_oficial_llega_por_el_flujo_hasta_el_ruteo(tmp_path, trabajar):
    ruta = _oficial(tmp_path, "c.zip", n=400, semilla=17)
    with sesion() as s:
        corrida, flujo = crear_flujo_ingesta(
            s,
            origen=ruta.name,
            contenido=ruta.read_bytes(),
            contrato=V2,
            fecha_corte=CORTE,
            tolerancia=0.05,
            config=Config(),
        )
        flujo_id = flujo.id

    procesados = trabajar()

    assert [p.tipo for p in procesados] == ["INGESTA", "DECISION", "TERRITORIAL", "RUTEO"]
    with sesion() as s:
        from motor_cartera.db.modelos import FlujoOrquestacion

        terminado = s.get_one(FlujoOrquestacion, flujo_id)
        ruteo = s.get_one(EjecucionRuteo, terminado.ejecucion_ruteo_id)
        publicada = s.get_one(Corrida, corrida.id)
    assert terminado.estado == EstadoFlujo.COMPLETADO
    assert publicada.estado == EstadoCorrida.EXITOSA and publicada.filas_validas == 400
    assert ruteo.estado == "EXITOSA" and ruteo.rutas_publicadas > 0


def test_procesar_un_trabajo_de_cartera_v2_con_el_worker(tmp_path):
    ruta = _oficial(tmp_path, "c.csv", n=50, semilla=18)
    with sesion() as s:
        crear_flujo_ingesta(
            s,
            origen=ruta.name,
            contenido=ruta.read_bytes(),
            contrato=V2,
            fecha_corte=CORTE,
            tolerancia=0.05,
            config=Config(),
        )

    procesado = procesar_un_trabajo(identificador_worker(), Config())

    assert procesado.tipo == "INGESTA" and procesado.estado == "COMPLETADO"
    with sesion() as s:
        assert s.exec(select(func.count()).select_from(Cuenta)).one() == 50
