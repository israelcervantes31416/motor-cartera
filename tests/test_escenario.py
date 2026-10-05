"""El escenario longitudinal: varios cortes de la misma cartera, con altas, bajas, cuentas que
continuan con su saldo y su atraso al dia, y los pagos entre un corte y el siguiente. Sin base: se
revisan los archivos que escribe, como los veria quien los recibe."""

from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import date, timedelta
from decimal import Decimal

import pandas as pd
import pytest
from typer.testing import CliRunner

from motor_cartera.cli import app
from motor_cartera.contratos.cartera_v2 import CONTRATO_V2
from motor_cartera.contratos.fuente import juzgar_lote
from motor_cartera.contratos.pagos import CONTRATO_PAGOS
from motor_cartera.generador.oficial import TOPE_ATRASO, generar_escenario

PRIMER_CORTE = date(2026, 9, 2)
FIJOS = (
    "NOMBRE_CTE",
    "GENERO_CLIENTE",
    "DIRECCION_CTE",
    "COLONIA_CTE",
    "POBLACION_CTE",
    "ESTADO_CTE",
    "TELEFONO1",
    "PRODUCTO",
    "FECHA_ASIGNACION",
    "CLAVE_SPEI",
)
"""Lo que una cuenta conserva de un corte a otro mientras sigue en la cartera."""


def _leer(ruta) -> pd.DataFrame:
    return pd.read_csv(ruta, dtype="string", keep_default_na=False, na_values=[""])


def _centavos(valor) -> int:
    return int(Decimal(valor) * 100)


@pytest.fixture(scope="module")
def escenario(tmp_path_factory):
    destino = tmp_path_factory.mktemp("escenario")
    return generar_escenario(
        destino,
        cuentas=800,
        cortes=4,
        primer_corte=PRIMER_CORTE,
        semilla=7,
        formato="csv",
        tasa_altas=0.03,
        tasa_retiros=0.02,
    )


def _cortes(escenario) -> list[tuple[date, pd.DataFrame]]:
    return [
        (date.fromisoformat(c["fecha_corte"]), _leer(escenario.destino / c["archivo"]))
        for c in escenario.manifiesto["cortes"]
    ]


def _periodos(escenario) -> list[pd.DataFrame]:
    return [_leer(escenario.destino / p["archivo"]) for p in escenario.manifiesto["periodos"]]


def test_el_escenario_escribe_cada_corte_sus_pagos_y_su_manifiesto(escenario):
    manifiesto = escenario.manifiesto
    cortes = [c["fecha_corte"] for c in manifiesto["cortes"]]

    assert cortes == [(PRIMER_CORTE + timedelta(days=7 * i)).isoformat() for i in range(4)]
    assert [(p["desde"], p["hasta"]) for p in manifiesto["periodos"]] == [
        ((PRIMER_CORTE + timedelta(days=7 * i + 1)).isoformat(), cortes[i + 1]) for i in range(3)
    ]
    assert json.loads((escenario.destino / "escenario.json").read_text("utf-8")) == manifiesto
    for registro in manifiesto["cortes"] + manifiesto["periodos"]:
        contenido = (escenario.destino / registro["archivo"]).read_bytes()
        assert registro["sha256"] == hashlib.sha256(contenido).hexdigest()
    assert manifiesto["cortes"][0]["cuentas"] == 800
    assert manifiesto["invariantes"]  # documentados en el propio manifiesto


def test_cada_archivo_cumple_su_contrato(escenario):
    for _, cartera in _cortes(escenario):
        juicio = juzgar_lote(cartera.set_axis(range(2, len(cartera) + 2)), CONTRATO_V2)
        assert juicio.rechazos == {}
        assert cartera["CLIENTE_UNICO"].is_unique
    for pagos in _periodos(escenario):
        juicio = juzgar_lote(pagos.set_axis(range(2, len(pagos) + 2)), CONTRATO_PAGOS)
        assert juicio.rechazos == {} and len(pagos) > 0


def test_altas_bajas_y_continuidad_cuadran_con_el_manifiesto(escenario):
    cortes = _cortes(escenario)
    vistos = set(cortes[0][1]["CLIENTE_UNICO"])
    for (_, antes), (_, despues), registro in zip(
        cortes, cortes[1:], escenario.manifiesto["cortes"][1:], strict=False
    ):
        de_antes, de_despues = set(antes["CLIENTE_UNICO"]), set(despues["CLIENTE_UNICO"])
        altas = de_despues - de_antes
        # Una alta nunca reusa un cliente que ya estuvo en la cartera, aunque haya salido.
        assert not altas & vistos
        vistos |= de_despues
        assert len(de_antes & de_despues) == registro["continuan"]
        assert len(altas) == registro["altas"] > 0
        assert len(de_antes - de_despues) == registro["liquidadas"] + registro["retiradas"]
        assert registro["liquidadas"] > 0 and registro["retiradas"] > 0
        assert registro["cuentas"] == len(despues)


def test_cada_cuenta_que_continua_lleva_sus_pagos_y_su_atraso(escenario):
    cortes = _cortes(escenario)
    for i, pagos in enumerate(_periodos(escenario)):
        (corte, antes), (siguiente, despues) = cortes[i], cortes[i + 1]
        antes = antes.set_index("CLIENTE_UNICO")
        despues = despues.set_index("CLIENTE_UNICO")
        # Los pagos del periodo son de cuentas del corte que lo abre, y caen dentro del periodo.
        assert set(pagos["Cliente_Unico"]) <= set(antes.index)
        recepcion = pd.to_datetime(pagos["Fecha_Recepción"])
        assert recepcion.min() >= pd.Timestamp(corte + timedelta(days=1))
        assert recepcion.max() < pd.Timestamp(siguiente + timedelta(days=1))
        # Las filas repetidas exactas son el mismo pago reportado dos veces: la cartera lo
        # cuenta una vez.
        distintos = pagos.drop_duplicates().assign(
            centavos=lambda d: d["Recuperación_por_Gestión"].map(_centavos),
            dia=lambda d: d["Fecha_Recepción"].str[:10],
        )
        neto = distintos.groupby("Cliente_Unico")["centavos"].sum()
        positivos = distintos[distintos["centavos"] > 0].groupby("Cliente_Unico")
        saldo_restante = antes["SALDO"].map(_centavos) - neto.reindex(antes.index, fill_value=0)

        continuan = antes.index.intersection(despues.index)
        salieron = antes.index.difference(despues.index)
        assert (saldo_restante[continuan] > 0).all()
        liquidadas = int((saldo_restante[salieron] <= 0).sum())
        assert liquidadas == escenario.manifiesto["cortes"][i + 1]["liquidadas"]

        dias = (siguiente - corte).days
        for cliente in continuan:
            a, d = antes.loc[cliente], despues.loc[cliente]
            pagado = int(neto.get(cliente, 0))
            assert _centavos(d["SALDO"]) == _centavos(a["SALDO"]) - pagado, cliente
            esperado = 0 if pagado > 0 else min(int(a["DIAS_ATRASO"]) + dias, TOPE_ATRASO)
            assert int(d["DIAS_ATRASO"]) == esperado, cliente
            assert int(d["ATRASO_MAXIMO"]) == max(int(a["ATRASO_MAXIMO"]), esperado)
            assert all(a[c] == d[c] or (pd.isna(a[c]) and pd.isna(d[c])) for c in FIJOS), cliente
            if cliente in positivos.groups:
                suyos = positivos.get_group(cliente)
                assert int(d["PAGOS_CLIENTE"]) == int(a["PAGOS_CLIENTE"]) + len(suyos)
                assert _centavos(d["MONTO_PAGOS"]) == _centavos(a["MONTO_PAGOS"]) + int(
                    suyos["centavos"].sum()
                )
                assert d["FECHA_ULTIMO_PAGO"] == suyos["dia"].max()
            else:
                assert (d["PAGOS_CLIENTE"], d["MONTO_PAGOS"]) == (
                    a["PAGOS_CLIENTE"],
                    a["MONTO_PAGOS"],
                )
        # Las altas llegaron al despacho dentro del periodo.
        altas = despues.loc[despues.index.difference(antes.index)]
        asignacion = pd.to_datetime(altas["FECHA_ASIGNACION"])
        assert asignacion.min() > pd.Timestamp(corte)
        assert asignacion.max() <= pd.Timestamp(siguiente)


def test_carrier_y_despacho_de_cada_corte(tmp_path):
    escenario = generar_escenario(
        tmp_path, cuentas=150, cortes=2, primer_corte=PRIMER_CORTE, semilla=13
    )

    for corte in escenario.manifiesto["cortes"]:
        with zipfile.ZipFile(escenario.destino / corte["archivo"]) as paquete:
            cartera = _leer(paquete.open("CARTERA.csv"))
            carrier = _leer(paquete.open("CARRIER.csv"))
        # CARRIER no trae clientes ni telefonos que su corte no tenga.
        assert set(carrier["CLIENTE_UNICO"]) <= set(cartera["CLIENTE_UNICO"])
        anchas = ["TEL_AVAL", *(f"TELEFONO{k}" for k in range(1, 5))]
        telefonos = set(pd.concat([cartera[c] for c in anchas]).dropna())
        assert set(carrier["TELEFONO"].dropna()) <= telefonos
        assert len(carrier) == corte["filas_carrier"] > len(cartera)
        # Un solo despacho.
        assert cartera["NOMBRE_DESPACHO"].nunique() == 1


def test_el_mismo_escenario_sale_byte_por_byte_igual(tmp_path):
    def generar(nombre: str):
        return generar_escenario(
            tmp_path / nombre, cuentas=150, cortes=3, primer_corte=PRIMER_CORTE, semilla=11
        )

    uno, otro = generar("uno"), generar("otro")

    # En zip tambien: sus miembros no llevan la hora en que se escribieron.
    assert [c["archivo"] for c in uno.manifiesto["cortes"]][0].endswith(".zip")
    assert uno.manifiesto == otro.manifiesto
    for registro in uno.manifiesto["cortes"] + uno.manifiesto["periodos"]:
        nombre = registro["archivo"]
        assert (uno.destino / nombre).read_bytes() == (otro.destino / nombre).read_bytes()
    con_otra_semilla = generar_escenario(
        tmp_path / "tres", cuentas=150, cortes=3, primer_corte=PRIMER_CORTE, semilla=12
    )
    assert (
        con_otra_semilla.manifiesto["cortes"][0]["sha256"] != uno.manifiesto["cortes"][0]["sha256"]
    )


@pytest.mark.parametrize(
    ("opciones", "mensaje"),
    [
        ({"cortes": 0}, "al menos un corte"),
        ({"dias_entre_cortes": 0}, "al menos un dia"),
        ({"formato": "parquet"}, "Formato no soportado"),
    ],
)
def test_un_escenario_imposible_no_se_genera(tmp_path, opciones, mensaje):
    argumentos = {"cuentas": 10, "cortes": 2, "primer_corte": PRIMER_CORTE, "semilla": 1}

    with pytest.raises(ValueError, match=mensaje):
        generar_escenario(tmp_path, **{**argumentos, **opciones})


def test_generar_escenario_desde_la_linea_de_comandos(tmp_path):
    resultado = CliRunner().invoke(
        app,
        [
            "generar-escenario",
            "--destino",
            str(tmp_path),
            "--cuentas",
            "200",
            "--cortes",
            "3",
            "--primer-corte",
            "2026-09-02",
            "--formato",
            "csv",
            "--semilla",
            "3",
        ],
    )

    assert resultado.exit_code == 0, resultado.output
    manifiesto = json.loads((tmp_path / "escenario.json").read_text("utf-8"))
    assert len(manifiesto["cortes"]) == 3 and len(manifiesto["periodos"]) == 2
    assert "3 cortes" in resultado.output and "escenario.json" in resultado.output
    for registro in manifiesto["cortes"] + manifiesto["periodos"]:
        assert (tmp_path / registro["archivo"]).exists()


@pytest.mark.usefixtures("bd")
def test_un_escenario_se_ingiere_corte_por_corte_con_sus_pagos(tmp_path):
    from motor_cartera.db.modelos import EstadoCorrida, EstadoIngestaPagos
    from motor_cartera.ingesta.corridas import ingerir_archivo
    from motor_cartera.ingesta.pagos import ingerir_pagos

    escenario = generar_escenario(
        tmp_path, cuentas=250, cortes=3, primer_corte=PRIMER_CORTE, semilla=5
    )

    for corte in escenario.manifiesto["cortes"]:
        corrida = ingerir_archivo(
            escenario.destino / corte["archivo"],
            contrato="cartera/v2",
            fecha_corte=date.fromisoformat(corte["fecha_corte"]),
        )
        assert corrida.estado == EstadoCorrida.EXITOSA, corrida.detalle
        assert corrida.filas_validas == corte["cuentas"]
    for periodo in escenario.manifiesto["periodos"]:
        ingesta = ingerir_pagos(escenario.destino / periodo["archivo"])
        assert ingesta.estado == EstadoIngestaPagos.EXITOSA, ingesta.detalle
        assert ingesta.filas_validas == periodo["movimientos"]
