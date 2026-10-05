"""El contrato de las fuentes oficiales, sin base de datos: estructura exacta, cada tipo con sus
fronteras, la forma canonica y la firma del contenido."""

from __future__ import annotations

import unicodedata

import pandas as pd
import pytest

from motor_cartera.contratos.cartera import Motivo
from motor_cartera.contratos.cartera_v2 import (
    COLUMNAS_CARRIER,
    CONTRATO_V2,
    TELEFONOS_ANCHOS,
    VERSION_CONTRATO_V2,
)
from motor_cartera.contratos.fuente import (
    Columna,
    ContratoFuente,
    ErrorDeEstructura,
    FirmaDeContenido,
    Tipo,
    juzgar_lote,
    linea_canonica,
    verificar_encabezado,
)

# Las 93 columnas de la hoja CARTERA observada, en su orden. Son el contrato fuente que se simula:
# la prueba las fija a mano para que un cambio del contrato no pase sin que alguien lo vea.
LAS_93 = (
    "CLIENTE_UNICO",
    "NOMBRE_CTE",
    "GENERO_CLIENTE",
    "EDAD_CLIENTE",
    "OCUPACION",
    "DIRECCION_CTE",
    "NUM_EXT_CTE",
    "NUM_INT_CTE",
    "CP_CTE",
    "COLONIA_CTE",
    "POBLACION_CTE",
    "ESTADO_CTE",
    "TERRITORIO",
    "TERRITORIAL",
    "ZONA",
    "ZONAL",
    "NOMBRE_DESPACHO",
    "GERENCIA",
    "FECHA_ASIGNACION",
    "DIAS_ASIGNACION",
    "REFERENCIAS_DOMICILIO",
    "CLASIFICACION_CTE",
    "DIQUE",
    "ATRASO_MAXIMO",
    "DIAS_ATRASO",
    "SALDO",
    "MORATORIOS",
    "SALDO_TOTAL",
    "SALDO ATRASADO",
    "SALDO REQUERIDO",
    "PAGO_NORMAL",
    "PRODUCTO",
    "ESTRATEGIA",
    "FECHA_ULTIMO_PAGO",
    "IMP_ULTIMO_PAGO",
    "CALLE_EMPLEO",
    "NUM_EXT_EMPLEO",
    "NUM_INT_EMPLEO",
    "COLONIA_EMPLEO",
    "POBLACION_EMPLEO",
    "ESTADO_EMPLEO",
    "NOMBRE_AVAL",
    "TEL_AVAL",
    "CALLE_AVAL",
    "NUM_EXT_AVAL",
    "COLONIA_AVAL",
    "CP_AVAL",
    "POBLACION_AVAL",
    "ESTADO_AVAL",
    "FIDIAPAGO",
    "TELEFONO1",
    "TELEFONO2",
    "TELEFONO3",
    "TELEFONO4",
    "TIPOTEL1",
    "TIPOTEL2",
    "TIPOTEL3",
    "TIPOTEL4",
    "LATITUD",
    "LONGITUD",
    "DESPACHO_GESTIONO",
    "ULTIMA_GESTION",
    "GESTION_DESC",
    "CAMPANIA_RELAMPAGO",
    "CAMPANIA",
    "PREVENTA",
    "ID_GRUPO",
    "GRUPO_MAZ",
    "CLAVE_SPEI",
    "PAGOS_CLIENTE",
    "MONTO_PAGOS",
    "GESTORES",
    "FOLIO_PLAN",
    "SEGMENTO_GENERACION",
    "ESTATUS_PLAN",
    "SEMANAS_ATRASO",
    "ATRASO",
    "GENERACION_PLAN",
    "CANCELACION_CUMPLIMIENTO_PLAN",
    "ULTIMO_ESTATUS",
    "EMPLEADO",
    "CANAL",
    "ABONO_SEMANAL",
    "PLAZO",
    "MONTO_ABONADO",
    "MONTO_PLAN",
    "ENGANCHE",
    "PAGOS_RECIBIDOS",
    "SALDO_ANTES_DEL_PLAN",
    "SALDO_ATRASADO_ANTES_PLAN",
    "MORATORIOS_ANTES_PLAN",
    "ESTATUS_PROMESA_PAGO",
    "MONTO_PROMESA_PAGO",
)
"""Dos de ellas llevan un espacio y no un guion bajo, como vienen en la fuente: SALDO ATRASADO y
SALDO REQUERIDO."""


def test_cartera_v2_son_exactamente_las_93_columnas_de_la_fuente():
    assert len(LAS_93) == len(set(LAS_93)) == 93
    assert CONTRATO_V2.nombres == LAS_93
    assert CONTRATO_V2.version == VERSION_CONTRATO_V2 == "cartera/v2"
    assert CONTRATO_V2.llave_unica == "CLIENTE_UNICO"
    # No hay una columna 94: ni la fecha de corte ni ningun identificador inventado.
    for inventada in ("FECHA_CORTE", "CREDITO_ID", "MONTO_ORIGINAL", "TASA_INTERES"):
        assert inventada not in CONTRATO_V2.nombres


def test_carrier_son_las_columnas_de_cartera_sin_sus_9_telefonos_y_con_uno_solo():
    assert len(TELEFONOS_ANCHOS) == 9
    assert len(COLUMNAS_CARRIER) == len(set(COLUMNAS_CARRIER)) == 85
    assert set(COLUMNAS_CARRIER) == (set(LAS_93) - set(TELEFONOS_ANCHOS)) | {"TELEFONO"}


def test_solo_se_tipa_lo_que_no_es_ambiguo_y_se_exige_lo_que_proyecta():
    requeridas = {c.nombre for c in CONTRATO_V2.columnas if c.requerida}
    assert requeridas == {
        "CLIENTE_UNICO",
        "SALDO_TOTAL",
        "DIAS_ATRASO",
        "PRODUCTO",
        "CANAL",
        "ESTADO_CTE",
        "POBLACION_CTE",
    }
    tipos = {c.nombre: c.tipo for c in CONTRATO_V2.columnas}
    assert tipos["FECHA_ASIGNACION"] == tipos["FECHA_ULTIMO_PAGO"] == Tipo.FECHA
    assert tipos["SALDO ATRASADO"] == tipos["MONTO_PROMESA_PAGO"] == Tipo.IMPORTE
    assert tipos["CP_CTE"] == tipos["TELEFONO1"] == tipos["TEL_AVAL"] == Tipo.CODIGO
    # Lo que no se sabe que es se queda como texto: tiparlo seria inventar una regla.
    for incierta in ("ULTIMA_GESTION", "ATRASO", "DIQUE", "CLAVE_SPEI", "GENERACION_PLAN"):
        assert tipos[incierta] == Tipo.TEXTO


# --- la estructura -------------------------------------------------------------------------------


def test_un_encabezado_exacto_se_acepta_en_cualquier_orden():
    desordenado = list(reversed(LAS_93))

    assert verificar_encabezado(desordenado, CONTRATO_V2, "c.csv") == desordenado


def test_el_encabezado_solo_se_limpia_de_espacios_y_de_unicode_equivalente():
    contrato = ContratoFuente("p/v1", (Columna("Fecha_Recepción"), Columna("Año")))
    descompuesto = unicodedata.normalize("NFD", "Fecha_Recepción")
    assert descompuesto != "Fecha_Recepción"

    assert verificar_encabezado([f"  {descompuesto} ", "Año"], contrato, "p.csv") == [
        "Fecha_Recepción",
        "Año",
    ]


@pytest.mark.parametrize(
    ("encabezado", "motivos"),
    [
        (LAS_93[1:], ["faltan 1 columnas del contrato: CLIENTE_UNICO"]),
        ([*LAS_93, "COMENTARIOS"], ["sobran 1 columnas que el contrato no tiene: COMENTARIOS"]),
        ([*LAS_93, "SALDO"], ["encabezados repetidos: SALDO"]),
        ([*LAS_93[:5], "", *LAS_93[5:]], ["columnas sin encabezado en las posiciones 6"]),
        (
            [c.lower() if c == "CANAL" else c for c in LAS_93],
            [
                "faltan 1 columnas del contrato: CANAL",
                "sobran 1 columnas que el contrato no tiene: canal",
            ],
        ),
    ],
    ids=["falta", "sobra", "repetida", "sin-nombre", "mayusculas"],
)
def test_una_desviacion_de_estructura_es_un_error_explicito(encabezado, motivos):
    with pytest.raises(ErrorDeEstructura) as exc:
        verificar_encabezado(encabezado, CONTRATO_V2, "'c.csv'")

    mensaje = str(exc.value)
    assert mensaje.startswith("'c.csv' no tiene la estructura de cartera/v2, que son 93 columnas")
    for motivo in motivos:
        assert motivo in mensaje


# --- cada tipo, en sus fronteras ---------------------------------------------------------------

PRUEBA = ContratoFuente(
    "prueba/v1",
    (
        Columna("ID", Tipo.CODIGO, requerida=True, patron=r"[A-Z0-9]{8,20}"),
        Columna("N", Tipo.ENTERO, minimo=0, maximo=3650),
        Columna("I", Tipo.IMPORTE, minimo=0),
        Columna("S", Tipo.IMPORTE),
        Columna("D", Tipo.DECIMAL, minimo=-90, maximo=90),
        Columna("F", Tipo.FECHA),
        Columna("H", Tipo.FECHA_HORA),
        Columna("C", Tipo.CATALOGO, catalogo=("A", "B")),
        Columna("T", Tipo.TEXTO),
    ),
    llave_unica="ID",
)


def _juzgar(**columnas) -> tuple[dict, dict]:
    filas = max(len(v) for v in columnas.values())
    datos = {c.nombre: columnas.get(c.nombre, [None] * filas) for c in PRUEBA.columnas}
    datos["ID"] = columnas.get("ID", [f"CU{i:08d}" for i in range(filas)])
    df = pd.DataFrame(datos, index=range(2, filas + 2), dtype="string")
    juicio = juzgar_lote(df, PRUEBA)
    canon = {fila: dict(valores) for fila, valores in juicio.canonicos.iterrows()}
    return canon, juicio.rechazos


@pytest.mark.parametrize(
    ("valor", "canonico"),
    [("7", "7"), ("007", "7"), ("-0", "0"), ("+12", "12"), ("3650", "3650")],
)
def test_un_entero_valido_y_su_canonico(valor, canonico):
    canon, rechazos = _juzgar(N=[valor])
    assert rechazos == {} and canon[2]["N"] == canonico


@pytest.mark.parametrize(
    ("valor", "regla"),
    [
        ("4.5", "entero"),
        ("x", "entero"),
        ("1e3", "entero"),
        ("-1", "rango(0..3650)"),
        ("3651", "rango(0..3650)"),
    ],
)
def test_un_entero_invalido_y_su_regla(valor, regla):
    _, rechazos = _juzgar(N=[valor])
    assert rechazos == {2: [Motivo("N", regla)]}


@pytest.mark.parametrize(
    ("valor", "canonico"),
    [
        ("1500.5", "1500.50"),
        ("1500.", "1500.00"),
        ("1500", "1500.00"),
        ("000012.300", "12.30"),
        ("1500.5000", "1500.50"),
        ("-0.00", "0.00"),
        ("999999999999.99", "999999999999.99"),
    ],
)
def test_un_importe_valido_y_su_canonico_con_dos_decimales(valor, canonico):
    canon, rechazos = _juzgar(I=[valor])
    assert rechazos == {} and canon[2]["I"] == canonico


@pytest.mark.parametrize(
    ("columna", "valor", "regla"),
    [
        ("I", "1.505", "importe(2 decimales)"),
        ("I", "1,500.00", "importe"),
        ("I", "1e5", "importe"),
        ("I", ".5", "importe"),
        ("I", "N/D", "importe"),
        ("I", "1000000000000.00", "importe(menos de 10^12)"),
        ("I", "-1.00", "rango(0..None)"),
    ],
)
def test_un_importe_invalido_no_se_redondea_se_rechaza(columna, valor, regla):
    _, rechazos = _juzgar(**{columna: [valor]})
    assert rechazos == {2: [Motivo(columna, regla)]}


def test_un_importe_sin_minimo_admite_negativos():
    canon, rechazos = _juzgar(S=["-150.5"])
    assert rechazos == {} and canon[2]["S"] == "-150.50"


@pytest.mark.parametrize(
    ("valor", "canonico"), [("19.04140", "19.0414"), ("-0", "0"), ("-0.5", "-0.5"), ("90", "90")]
)
def test_un_decimal_sin_ceros_sobrantes(valor, canonico):
    canon, rechazos = _juzgar(D=[valor])
    assert rechazos == {} and canon[2]["D"] == canonico


@pytest.mark.parametrize(("valor", "regla"), [("1e5", "decimal"), ("91", "rango(-90..90)")])
def test_un_decimal_invalido(valor, regla):
    _, rechazos = _juzgar(D=[valor])
    assert rechazos == {2: [Motivo("D", regla)]}


@pytest.mark.parametrize("valor", ["2026-02-30", "30/09/2026", "2026-9-30", "2026-09-30T00:00:00"])
def test_una_fecha_solo_en_iso_y_que_exista(valor):
    _, rechazos = _juzgar(F=[valor])
    assert rechazos == {2: [Motivo("F", "fecha(AAAA-MM-DD)")]}


@pytest.mark.parametrize(
    ("valor", "canonico"),
    [
        ("2026-09-30", "2026-09-30T00:00:00"),
        ("2026-09-30 13:05:07", "2026-09-30T13:05:07"),
        ("2026-09-30T13:05:07.5", "2026-09-30T13:05:07.500000"),
        ("2026-09-30T13:05:07.000000", "2026-09-30T13:05:07"),
    ],
)
def test_una_fecha_hora_y_su_canonico(valor, canonico):
    canon, rechazos = _juzgar(H=[valor])
    assert rechazos == {} and canon[2]["H"] == canonico


@pytest.mark.parametrize("valor", ["2026-09-30T25:00:00", "2026-09-30T13:05", "2026-09-30Z", "x"])
def test_una_fecha_hora_invalida(valor):
    _, rechazos = _juzgar(H=[valor])
    assert rechazos == {2: [Motivo("H", "fecha_hora(AAAA-MM-DD[THH:MM:SS])")]}


def test_un_codigo_un_catalogo_y_un_requerido():
    _, rechazos = _juzgar(ID=["cu1", None, "CU00000003"], C=["A", "B", "Z"])
    assert rechazos == {
        2: [Motivo("ID", "forma([A-Z0-9]{8,20})")],
        3: [Motivo("ID", "requerido")],
        4: [Motivo("C", "catalogo(A|B)")],
    }


def test_un_texto_se_normaliza_a_nfc_y_un_vacio_es_vacio():
    canon, _ = _juzgar(T=[unicodedata.normalize("NFD", "TEHUACÁN"), None])
    assert canon[2]["T"] == "TEHUACÁN" and pd.isna(canon[3]["T"])


def test_cada_regla_que_falla_es_un_motivo_y_la_fila_se_conserva():
    _, rechazos = _juzgar(ID=["x"], N=["-1"], I=["1.555"], F=["ayer"])
    assert rechazos == {
        2: [
            Motivo("ID", "forma([A-Z0-9]{8,20})"),
            Motivo("N", "rango(0..3650)"),
            Motivo("I", "importe(2 decimales)"),
            Motivo("F", "fecha(AAAA-MM-DD)"),
        ]
    }


# --- la firma del contenido -----------------------------------------------------------------------


def _canonicos(**columnas) -> pd.DataFrame:
    canon, rechazos = _juzgar(**columnas)
    assert rechazos == {}
    return pd.DataFrame.from_dict(canon, orient="index")


def _firma(*lotes: pd.DataFrame, contrato=PRUEBA) -> str:
    firma = FirmaDeContenido(contrato)
    for lote in lotes:
        firma.agregar(lote)
    return firma.hexdigest()


def test_la_firma_no_depende_del_orden_de_las_filas_ni_de_los_lotes():
    datos = _canonicos(N=["1", "2", "3"], I=["10", "20.5", "30"])

    assert (
        _firma(datos)
        == _firma(datos.iloc[[2, 0, 1]])
        == _firma(datos.iloc[[1]], datos.iloc[[2, 0]])
    )


def test_la_firma_no_depende_del_orden_fisico_de_las_columnas():
    datos = _canonicos(N=["1", "2"], T=["a", "b"])

    assert _firma(datos) == _firma(datos[list(reversed(datos.columns))])


def test_la_firma_cambia_si_cambia_un_valor_y_cuenta_los_repetidos():
    datos = _canonicos(N=["1", "2"])
    otro = datos.copy()
    otro.loc[3, "N"] = "5"

    assert _firma(datos) != _firma(otro)
    assert _firma(datos) != _firma(pd.concat([datos, datos.iloc[[0]]]))


def test_la_firma_lleva_su_version():
    datos = _canonicos(N=["1"])
    otra = ContratoFuente("prueba/v2", PRUEBA.columnas, llave_unica="ID")

    assert _firma(datos) != _firma(datos, contrato=otra)


def test_la_linea_canonica_es_inequivoca_aunque_un_texto_traiga_comas_o_dos_puntos():
    assert linea_canonica(["a,b", None, "3:x"]) == "3:a,b,-,3:3:x"
    assert linea_canonica(["a", "b"]) != linea_canonica(["a,1:b"])
