"""La Cuenta 360 y el modelo historico por la API, sobre el escenario golden de seis cortes.

El escenario se ingiere y se materializa una vez para todo el modulo: ninguna de estas pruebas
escribe nada, solo consulta.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from historia_escenarios import GOLDEN_CORTES, Golden, golden, historia_de
from historia_escenarios import cliente as cu
from sqlalchemy import text
from sqlmodel import select

from motor_cartera.api.esquemas import AVISO_PAGOS_OBSERVADOS
from motor_cartera.config import Config
from motor_cartera.db.modelos import ArtefactoFuente, DatasetConformado, TrabajoOrquestacion
from motor_cartera.db.sesion import crear_motor, sesion
from motor_cartera.orquestacion.worker import identificador_worker, procesar_un_trabajo

UUID_QUE_NO_EXISTE = "00000000-0000-4000-8000-000000000000"


@pytest.fixture(scope="module")
def escenario(_esquema, tmp_path_factory) -> Golden:
    with crear_motor().begin() as conexion:
        conexion.execute(
            text(
                "TRUNCATE corrida, cuenta, rechazo, artefacto_fuente, cuenta_canonica "
                "RESTART IDENTITY CASCADE"
            )
        )
    publicado = golden(tmp_path_factory.mktemp("golden"))
    worker_id = identificador_worker()
    while procesar_un_trabajo(worker_id, Config()) is not None:
        pass
    return publicado


def _cuenta_id(cliente_api, numero: int) -> str:
    respuesta = cliente_api.get("/cuentas", params={"cliente_unico": cu(numero)})
    assert respuesta.status_code == 200, respuesta.json()
    return respuesta.json()["cuenta_id"]


# --- buscar una cuenta ----------------------------------------------------------------------------


def test_buscar_una_cuenta_por_su_cliente_unico(escenario, cliente):
    respuesta = cliente.get("/cuentas", params={"cliente_unico": "CU0000000003"})

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["cliente_unico"] == "CU0000000003"
    assert (cuerpo["despacho_id"], cuerpo["cartera_id"]) == ("DSP_001", "CARTERA_PRINCIPAL")
    # El identificador publico; el id interno nunca sale.
    assert set(cuerpo) == {"cuenta_id", "cliente_unico", "despacho_id", "cartera_id"}


@pytest.mark.parametrize(
    ("cliente_unico", "estado", "codigo"),
    [
        ("CU0000000007", 404, "SIN_CUENTA_OBSERVADA"),
        ("CU0000009999", 404, "CUENTA_NO_ENCONTRADA"),
        ("cu-7", 422, "ENTRADA_INVALIDA"),
    ],
    ids=["solo-pagos", "nadie", "forma"],
)
def test_buscar_una_cuenta_que_no_hay(escenario, cliente, cliente_unico, estado, codigo):
    respuesta = cliente.get("/cuentas", params={"cliente_unico": cliente_unico})

    assert (respuesta.status_code, respuesta.json()["codigo"]) == (estado, codigo)
    if codigo == "SIN_CUENTA_OBSERVADA":
        # Un pago no crea una cuenta: el mensaje dice que sus pagos se conservan.
        assert "1 pagos observados" in respuesta.json()["mensaje"]


def test_las_rutas_del_modelo_historico_exigen_la_api_key(escenario, cliente):
    for ruta in ("/cuentas?cliente_unico=CU0000000001", "/cartera/cortes"):
        respuesta = cliente.get(ruta, headers={"X-API-Key": ""})
        assert (respuesta.status_code, respuesta.json()["codigo"]) == (401, "API_KEY_AUSENTE")


# --- Cuenta 360 -----------------------------------------------------------------------------------


def test_cuenta_360_de_una_cuenta_que_salio_y_reingreso(escenario, cliente):
    cuerpo = cliente.get(f"/cuentas/{_cuenta_id(cliente, 3)}").json()

    assert cuerpo["primera_observacion"] == GOLDEN_CORTES[0].isoformat()
    assert cuerpo["ultima_observacion"] == GOLDEN_CORTES[5].isoformat()
    assert cuerpo["ultimo_corte_cartera"] == GOLDEN_CORTES[5].isoformat()
    assert cuerpo["estado_presencia"] == "EN_CARTERA"
    assert (cuerpo["cortes_observados"], cuerpo["cortes_ausentes_desde_primera_observacion"]) == (
        4,
        2,
    )
    assert (cuerpo["salidas_observadas"], cuerpo["reingresos_observados"]) == (1, 1)
    assert cuerpo["pagos_observados"] == 1
    actual = cuerpo["snapshot_actual"]
    assert actual == cuerpo["ultimo_snapshot_observado"]
    assert actual["fecha_corte"] == GOLDEN_CORTES[5].isoformat()
    assert (actual["saldo_total"], actual["dias_atraso"]) == ("7907.00", 7)
    assert actual["source_row"] >= 2 and actual["source_sheet"] is None
    # Un resumen: la historia, los eventos y los pagos son subrecursos.
    assert not {"historia", "eventos", "pagos"} & set(cuerpo)


def test_cuenta_360_de_una_cuenta_que_ya_no_esta_en_el_ultimo_corte(escenario, cliente):
    cuerpo = cliente.get(f"/cuentas/{_cuenta_id(cliente, 2)}").json()

    assert cuerpo["estado_presencia"] == "NO_OBSERVADA_EN_ULTIMO_CORTE"
    assert cuerpo["snapshot_actual"] is None
    assert cuerpo["ultimo_snapshot_observado"]["fecha_corte"] == GOLDEN_CORTES[2].isoformat()
    assert cuerpo["ultima_observacion"] == GOLDEN_CORTES[2].isoformat()
    assert cuerpo["ultimo_corte_cartera"] == GOLDEN_CORTES[5].isoformat()


def test_cuenta_360_como_se_veia_en_una_fecha(escenario, cliente):
    cuenta_id = _cuenta_id(cliente, 3)
    # Al dia siguiente del corte 3, la cuenta 3 estaba fuera: habia salido en ese corte.
    al = (GOLDEN_CORTES[2] + timedelta(days=1)).isoformat()

    cuerpo = cliente.get(f"/cuentas/{cuenta_id}", params={"al": al}).json()

    assert cuerpo["al"] == al
    assert cuerpo["ultimo_corte_cartera"] == GOLDEN_CORTES[2].isoformat()
    assert cuerpo["estado_presencia"] == "NO_OBSERVADA_EN_ULTIMO_CORTE"
    assert cuerpo["snapshot_actual"] is None
    assert cuerpo["ultimo_snapshot_observado"]["fecha_corte"] == GOLDEN_CORTES[1].isoformat()
    assert (cuerpo["cortes_observados"], cuerpo["reingresos_observados"]) == (2, 0)
    # El pago de la cuenta 3 llego despues de esa fecha.
    assert cuerpo["pagos_observados"] == 0
    # Antes del primer corte, la cuenta todavia no se habia observado.
    antes = cliente.get(f"/cuentas/{cuenta_id}", params={"al": "2026-01-01"}).json()
    assert (antes["primera_observacion"], antes["ultimo_corte_cartera"]) == (None, None)
    assert antes["ultimo_snapshot_observado"] is None


@pytest.mark.parametrize(
    ("ruta", "estado", "codigo"),
    [
        (f"/cuentas/{UUID_QUE_NO_EXISTE}", 404, "CUENTA_NO_ENCONTRADA"),
        (f"/cuentas/{UUID_QUE_NO_EXISTE}/historia", 404, "CUENTA_NO_ENCONTRADA"),
        (f"/cuentas/{UUID_QUE_NO_EXISTE}/eventos", 404, "CUENTA_NO_ENCONTRADA"),
        (f"/cuentas/{UUID_QUE_NO_EXISTE}/pagos-observados", 404, "CUENTA_NO_ENCONTRADA"),
        ("/cuentas/no-es-un-uuid", 422, "ENTRADA_INVALIDA"),
        (f"/cuentas/{UUID_QUE_NO_EXISTE}?al=30/09/2026", 422, "ENTRADA_INVALIDA"),
    ],
)
def test_una_cuenta_que_no_existe(escenario, cliente, ruta, estado, codigo):
    respuesta = cliente.get(ruta)

    assert (respuesta.status_code, respuesta.json()["codigo"]) == (estado, codigo)


# --- la historia, paginada ------------------------------------------------------------------------


def test_la_historia_de_una_cuenta_con_su_continuidad_y_sus_deltas(escenario, cliente):
    respuesta = cliente.get(f"/cuentas/{_cuenta_id(cliente, 3)}/historia")

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert (cuerpo["total"], cuerpo["orden"], cuerpo["cliente_unico"]) == (4, "desc", cu(3))
    filas = [
        (
            e["fecha_corte"],
            e["saldo_total"],
            e["continuo_desde_anterior"],
            e["cortes_ausentes_desde_anterior"],
            e["delta_saldo_total"],
            e["delta_dias_atraso"],
        )
        for e in cuerpo["elementos"]
    ]
    # La mas reciente primero. El reingreso del corte 5 no es continuo: falto el 3 y el 4.
    assert filas == [
        (GOLDEN_CORTES[5].isoformat(), "7907.00", True, 0, "-93.00", 7),
        (GOLDEN_CORTES[4].isoformat(), "8000.00", False, 2, "-837.00", -37),
        (GOLDEN_CORTES[1].isoformat(), "8837.00", True, 0, "-193.00", 7),
        (GOLDEN_CORTES[0].isoformat(), "9030.00", None, None, None, None),
    ]
    with sesion() as s:
        datasets = set(s.exec(select(DatasetConformado.dataset_id)).all())
    for elemento in cuerpo["elementos"]:
        assert {"corte_id", "dataset_id", "source_row", "source_sheet"} <= set(elemento)
        assert _uuid(elemento["dataset_id"]) in datasets


def _uuid(texto: str):
    from uuid import UUID

    return UUID(texto)


@pytest.mark.parametrize("orden", ["desc", "asc"])
def test_paginar_la_historia_no_rompe_la_continuidad_en_el_borde(escenario, cliente, orden):
    ruta = f"/cuentas/{_cuenta_id(cliente, 3)}/historia"
    completa = cliente.get(ruta, params={"orden": orden}).json()["elementos"]

    paginas = [
        cliente.get(ruta, params={"orden": orden, "pagina": p, "por_pagina": 3}).json()
        for p in (1, 2, 3)
    ]

    # Cada elemento se compara con el anterior de la cuenta aunque este en otra pagina.
    assert paginas[0]["elementos"] + paginas[1]["elementos"] == completa
    assert paginas[2]["elementos"] == []
    assert {p["total"] for p in paginas} == {4}
    if orden == "asc":
        assert [e["fecha_corte"] for e in completa] == sorted(e["fecha_corte"] for e in completa)


def test_la_historia_no_admite_otro_orden_ni_paginas_fuera_de_rango(escenario, cliente):
    ruta = f"/cuentas/{_cuenta_id(cliente, 1)}/historia"

    for parametros in ({"orden": "aleatorio"}, {"por_pagina": 501}, {"pagina": 0}):
        respuesta = cliente.get(ruta, params=parametros)
        assert (respuesta.status_code, respuesta.json()["codigo"]) == (422, "ENTRADA_INVALIDA")


# --- los eventos ----------------------------------------------------------------------------------


def test_los_eventos_de_una_cuenta_que_sale_y_reingresa_dos_veces(escenario, cliente):
    cuerpo = cliente.get(f"/cuentas/{_cuenta_id(cliente, 8)}/eventos").json()

    assert cuerpo["total"] == 5
    assert [(e["tipo"], e["fecha_corte"], e["cortes_ausente"]) for e in cuerpo["elementos"]] == [
        ("PRIMERA_OBSERVACION", GOLDEN_CORTES[0].isoformat(), 0),
        ("SALIDA_OBSERVADA", GOLDEN_CORTES[1].isoformat(), 0),
        ("REINGRESO_OBSERVADO", GOLDEN_CORTES[2].isoformat(), 1),
        ("SALIDA_OBSERVADA", GOLDEN_CORTES[3].isoformat(), 0),
        ("REINGRESO_OBSERVADO", GOLDEN_CORTES[4].isoformat(), 1),
    ]
    salida = cuerpo["elementos"][1]
    assert salida["ultima_observacion"] == GOLDEN_CORTES[0].isoformat()
    segunda = cliente.get(
        f"/cuentas/{_cuenta_id(cliente, 8)}/eventos", params={"pagina": 2, "por_pagina": 2}
    ).json()
    assert [e["tipo"] for e in segunda["elementos"]] == ["REINGRESO_OBSERVADO", "SALIDA_OBSERVADA"]


def test_una_cuenta_que_aparece_tarde_solo_tiene_su_primera_observacion(escenario, cliente):
    cuerpo = cliente.get(f"/cuentas/{_cuenta_id(cliente, 4)}/eventos").json()

    assert [(e["tipo"], e["fecha_corte"]) for e in cuerpo["elementos"]] == [
        ("PRIMERA_OBSERVACION", GOLDEN_CORTES[2].isoformat())
    ]


# --- los pagos observados -------------------------------------------------------------------------


def test_los_pagos_observados_de_una_cuenta_del_mas_reciente_al_mas_antiguo(escenario, cliente):
    cuerpo = cliente.get(f"/cuentas/{_cuenta_id(cliente, 5)}/pagos-observados").json()

    assert cuerpo["aviso"] == AVISO_PAGOS_OBSERVADOS
    assert cuerpo["total"] == 3
    assert [p["recuperacion_por_gestion"] for p in cuerpo["elementos"]] == [
        "700.00",
        "600.00",
        "500.00",
    ]
    ingestas = {str(i.pagos_run_id) for i in escenario.ingestas}
    for pago in cuerpo["elementos"]:
        assert pago["pagos_run_id"] in ingestas
        assert pago["cliente_unico"] == cu(5)
        assert {"pago_observado_id", "dataset_id", "source_row", "source_sheet"} <= set(pago)
    # Ningun total de dinero: una suma de observaciones no es una recuperacion.
    assert not any("recuperacion" in campo for campo in cuerpo)


def test_dos_pagos_identicos_son_dos_observaciones_en_la_api(escenario, cliente):
    cuerpo = cliente.get(f"/cuentas/{_cuenta_id(cliente, 6)}/pagos-observados").json()

    assert cuerpo["total"] == 2
    primero, segundo = cuerpo["elementos"]
    assert primero["pago_observado_id"] != segundo["pago_observado_id"]
    iguales = ("fecha_recepcion", "recuperacion_por_gestion", "cliente_unico", "pagos_run_id")
    assert all(primero[c] == segundo[c] for c in iguales)
    # El orden entre los dos es estable: la fila mas alta primero.
    assert primero["source_row"] > segundo["source_row"]


def test_la_documentacion_de_los_pagos_observados_dice_que_no_estan_conciliados(app):
    operacion = app.openapi()["paths"]["/cuentas/{cuenta_id}/pagos-observados"]["get"]

    assert AVISO_PAGOS_OBSERVADOS in operacion["description"]
    assert "No estan deduplicados, conciliados" in AVISO_PAGOS_OBSERVADOS


# --- los cortes -----------------------------------------------------------------------------------


def test_los_cortes_canonicos_y_el_ultimo(escenario, cliente):
    cuerpo = cliente.get("/cartera/cortes").json()

    assert cuerpo["total"] == 6
    fechas = [c["fecha_corte"] for c in cuerpo["elementos"]]
    assert fechas == [c.isoformat() for c in reversed(GOLDEN_CORTES)]
    assert cuerpo["ultimo_corte"]["fecha_corte"] == GOLDEN_CORTES[-1].isoformat()
    assert [c["cuentas"] for c in cuerpo["elementos"]][::-1] == [26, 25, 26, 24, 26, 26]
    corridas = {str(c.run_id) for c in escenario.corridas}
    assert {c["run_id"] for c in cuerpo["elementos"]} == corridas
    assert {c["version_modelo"] for c in cuerpo["elementos"]} == {"historia/v1"}
    # El ultimo se sabe aunque no este en la pagina pedida.
    asc = cliente.get("/cartera/cortes", params={"orden": "asc", "por_pagina": 2}).json()
    assert [c["fecha_corte"] for c in asc["elementos"]] == [
        GOLDEN_CORTES[0].isoformat(),
        GOLDEN_CORTES[1].isoformat(),
    ]
    assert asc["ultimo_corte"]["fecha_corte"] == GOLDEN_CORTES[-1].isoformat()


def test_un_corte_con_su_evidencia_de_punta_a_punta(escenario, cliente):
    ultimo = cliente.get("/cartera/cortes").json()["ultimo_corte"]

    cuerpo = cliente.get(f"/cartera/cortes/{ultimo['corte_id']}").json()

    corrida = escenario.corridas[-1]
    assert cuerpo["run_id"] == str(corrida.run_id)
    assert cuerpo["artefacto_original"]["sha256"] == corrida.firma
    assert cuerpo["artefacto_conformado"]["formato"] == "parquet"
    with sesion() as s:
        parquet = s.exec(
            select(ArtefactoFuente.sha256)
            .join(
                DatasetConformado, DatasetConformado.artefacto_conformado_id == ArtefactoFuente.id
            )
            .where(DatasetConformado.corrida_id == corrida.id)
        ).one()
    assert cuerpo["artefacto_conformado"]["sha256"] == parquet
    (fuente,) = cuerpo["fuentes"]
    assert (fuente["resultado"], fuente["run_id"]) == ("CORTE_PUBLICADO", str(corrida.run_id))
    assert "storage_key" not in str(cuerpo)
    no_existe = cliente.get(f"/cartera/cortes/{UUID_QUE_NO_EXISTE}")
    assert (no_existe.status_code, no_existe.json()["codigo"]) == (404, "CORTE_NO_ENCONTRADO")


# --- las ejecuciones historicas -------------------------------------------------------------------


def test_la_historia_de_una_corrida_y_de_su_ejecucion(escenario, cliente):
    corrida = escenario.corridas[0]

    pagina = cliente.get(f"/corridas/{corrida.run_id}/historia").json()

    assert pagina["total"] == 1
    (ejecucion,) = pagina["elementos"]
    assert (ejecucion["estado"], ejecucion["resultado"]) == ("EXITOSA", "CORTE_PUBLICADO")
    assert (ejecucion["tipo_fuente"], ejecucion["version_modelo"]) == ("CARTERA", "historia/v1")
    assert ejecucion["run_id"] == str(corrida.run_id) and ejecucion["pagos_run_id"] is None
    assert ejecucion["fecha_corte"] == GOLDEN_CORTES[0].isoformat()
    assert ejecucion["registros_leidos"] == ejecucion["registros_publicados"] == 26
    assert ejecucion["duracion_segundos"] is not None
    # La misma, por su identificador, y su trabajo en la cola.
    sola = cliente.get(f"/historias/{ejecucion['historia_run_id']}").json()
    assert sola == ejecucion
    trabajo = cliente.get(f"/trabajos/{ejecucion['trabajo_id']}").json()
    assert (trabajo["tipo"], trabajo["estado"], trabajo["flujo_id"]) == (
        "HISTORIA",
        "COMPLETADO",
        None,
    )
    assert trabajo["objetivo_run_id"] == ejecucion["historia_run_id"]


def test_la_historia_de_una_ingesta_de_pagos_y_su_relacion_con_las_cuentas(escenario, cliente):
    primera = escenario.ingestas[0]

    cuerpo = cliente.get(f"/pagos/{primera.pagos_run_id}/historia").json()

    (ejecucion,) = cuerpo["elementos"]
    assert (ejecucion["tipo_fuente"], ejecucion["resultado"]) == ("PAGOS", "PAGOS_PUBLICADOS")
    assert ejecucion["pagos_run_id"] == str(primera.pagos_run_id)
    assert ejecucion["corte_id"] is None and ejecucion["run_id"] is None
    # Tres pagos de cuentas observadas (5 y los dos identicos de 6), y uno del cliente 7.
    assert cuerpo["relacion"] == {
        "pagos_con_cuenta_observada": 3,
        "pagos_sin_cuenta_observada": 1,
        "clientes_sin_cuenta_observada": 1,
    }


def test_las_historias_que_no_existen_o_no_aplican(escenario, cliente):
    for ruta, estado, codigo in (
        (f"/historias/{UUID_QUE_NO_EXISTE}", 404, "HISTORIA_NO_ENCONTRADA"),
        (f"/corridas/{UUID_QUE_NO_EXISTE}/historia", 404, "CORRIDA_NO_ENCONTRADA"),
        (f"/pagos/{UUID_QUE_NO_EXISTE}/historia", 404, "PAGOS_NO_ENCONTRADOS"),
        ("/historias/no-es-un-uuid", 422, "ENTRADA_INVALIDA"),
    ):
        respuesta = cliente.get(ruta)
        assert (respuesta.status_code, respuesta.json()["codigo"]) == (estado, codigo), ruta


def test_la_historia_del_dataset_es_la_que_registra_la_base(escenario):
    # La API no inventa nada: el historia_run_id de cada corrida es el de su ejecucion.
    for corrida in escenario.corridas:
        ejecucion = historia_de(corrida=corrida)
        with sesion() as s:
            trabajo = s.exec(
                select(TrabajoOrquestacion).where(
                    TrabajoOrquestacion.ejecucion_historia_id == ejecucion.id
                )
            ).one()
        assert (ejecucion.registros_publicados > 0, trabajo.tipo) == (True, "HISTORIA")
        assert ejecucion.detalle.startswith("Se publico el corte canonico")
