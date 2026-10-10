"""La importacion de eventos operacionales: un archivo JSONL sintetico, registrado por conjuntos
con las reglas de la API, idempotente por llave y todo o nada. Tambien el orden: lo que se importa
fuera de orden queda en la historia de la cuenta por su momento de negocio."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest
from historia_escenarios import cliente
from lifecycle_escenarios import cuenta_canonica, eventos
from sqlalchemy import text
from typer.testing import CliRunner

from motor_cartera.cli import app
from motor_cartera.db.sesion import sesion
from motor_cartera.lifecycle import consultas, registro
from motor_cartera.lifecycle.importacion import Importacion, importar
from motor_cartera.lifecycle.reglas import Canal, Medio, NivelContacto, ResultadoGestion

pytestmark = pytest.mark.usefixtures("bd")

DESPACHO, CARTERA, ZONA = "DSP_001", "CARTERA_PRINCIPAL", "America/Mexico_City"
cli = CliRunner()


def gestion(llave: str, numero: int = 1, ocurrido_en="2026-09-05T10:00:00-06:00", **campos):
    """Una llamada con el titular que termina en PROMESA, salvo lo que se cambie."""
    return {
        "tipo": "GESTION_REGISTRADA",
        "idempotency_key": llave,
        "cliente_unico": cliente(numero),
        "ocurrido_en": ocurrido_en,
        "canal": "TELEFONICA",
        "medio": "LLAMADA",
        "nivel_contacto": "CONTACTO_TITULAR",
        "resultado": "PROMESA",
        **campos,
    }


def promesa(llave: str, de: str, **campos):
    return {
        "tipo": "PROMESA_CREADA",
        "idempotency_key": llave,
        "gestion": de,
        "monto_prometido": "1000.00",
        "fecha_limite": "2026-09-15",
        **campos,
    }


def convenio(llave: str, de: str, **campos):
    return {
        "tipo": "CONVENIO_CREADO",
        "idempotency_key": llave,
        "gestion": de,
        "monto_total_acordado": "3000.00",
        "fecha_inicio": "2026-09-06",
        "fecha_fin": "2026-11-30",
        "cuotas": [
            {"fecha_vencimiento": "2026-09-30", "monto": "1500.00"},
            {"fecha_vencimiento": "2026-10-30", "monto": "1500.00"},
        ],
        **campos,
    }


def cierre(tipo: str, llave: str, de: str, ocurrido_en: str, motivo="Ya no aplica."):
    campo = {"GESTION_ANULADA": "gestion", "PROMESA_CANCELADA": "promesa"}.get(tipo, "convenio")
    return {
        "tipo": tipo,
        "idempotency_key": llave,
        campo: de,
        "ocurrido_en": ocurrido_en,
        "motivo": motivo,
    }


COMPLETO = [
    gestion("imp-g-0001", 1),
    promesa("imp-p-0001", "imp-g-0001"),
    cierre("PROMESA_CANCELADA", "imp-c-0001", "imp-p-0001", "2026-09-08T09:00:00-06:00"),
    gestion(
        "imp-g-0002",
        2,
        canal="CAMPO",
        medio=None,
        nivel_contacto="SIN_CONTACTO",
        resultado="VISITA_REALIZADA",
        visita={
            "resultado": "NO_LOCALIZADO",
            "inicio": "2026-09-05T09:30:00-06:00",
            "fin": "2026-09-05T10:30:00-06:00",
        },
    ),
    gestion("imp-g-0003", 3, resultado="CONVENIO"),
    convenio("imp-v-0003", "imp-g-0003"),
    cierre("CONVENIO_CANCELADO", "imp-c-0003", "imp-v-0003", "2026-09-20T09:00:00-06:00"),
    gestion(
        "imp-g-0004",
        4,
        canal="DIGITAL",
        medio="SMS",
        nivel_contacto="NO_APLICA",
        resultado="SIN_RESPUESTA",
    ),
    cierre("GESTION_ANULADA", "imp-a-0004", "imp-g-0004", "2026-09-06T09:00:00-06:00"),
]
"""Cada tipo de evento, con referencias dentro del archivo."""


def _archivo(tmp_path: Path, lineas, nombre: str = "eventos.jsonl") -> Path:
    ruta = tmp_path / nombre
    abrir = gzip.open if nombre.endswith(".gz") else open
    with abrir(ruta, "wt", encoding="utf-8") as archivo:
        for linea in lineas:
            archivo.write((linea if isinstance(linea, str) else json.dumps(linea)) + "\n")
    return ruta


def _importar(ruta: Path, **opciones) -> Importacion:
    return importar(ruta, despacho_id=DESPACHO, cartera_id=CARTERA, zona=ZONA, **opciones)


def _cuentas(*numeros: int):
    return {n: cuenta_canonica(n) for n in numeros}


def _recurso(columna: str, tabla: str, llave: str):
    with sesion() as s:
        return s.execute(
            text(
                f"SELECT r.{columna} FROM {tabla} r JOIN evento_lifecycle e "
                "ON e.id = r.evento_lifecycle_id WHERE e.idempotency_key = :llave"
            ),
            {"llave": llave},
        ).scalar_one()


# --- cada tipo, como por la API -------------------------------------------------------------------


def test_importa_cada_tipo_por_conjuntos_y_queda_como_por_la_api(tmp_path):
    _cuentas(1, 2, 3, 4)

    reporte = _importar(_archivo(tmp_path, COMPLETO))

    assert (reporte.lineas, reporte.nuevos, reporte.total_de_problemas) == (9, 9, 0)
    assert reporte.confirmada and reporte.problemas == []
    assert reporte.registrados == {
        "CONVENIO_CANCELADO": 1,
        "CONVENIO_CREADO": 1,
        "GESTION_ANULADA": 1,
        "GESTION_REGISTRADA": 4,
        "PROMESA_CANCELADA": 1,
        "PROMESA_CREADA": 1,
    }
    with sesion() as s:
        origenes = s.execute(
            text(
                "SELECT origen_registro, count(*), bool_and(registrado_en >= ocurrido_en) "
                "FROM evento_lifecycle GROUP BY 1"
            )
        ).all()
        promesa_vista = consultas.obtener_promesa(
            s, _recurso("promesa_id", "promesa_pago", "imp-p-0001")
        )
        convenio_vista = consultas.obtener_convenio(
            s,
            _recurso("convenio_id", "convenio_cobranza", "imp-v-0003"),
            version_motor="motor-pagos/v1",
        )
        visita = consultas.obtener_gestion(
            s, _recurso("gestion_id", "gestion_cobranza", "imp-g-0002")
        )
        anulada = consultas.obtener_gestion(
            s, _recurso("gestion_id", "gestion_cobranza", "imp-g-0004")
        )
    assert origenes == [("IMPORTACION", 9, True)]
    assert promesa_vista.estado == "CANCELADA"
    assert promesa_vista.promesa.monto_prometido == 1000
    assert convenio_vista.estado == "CANCELADO"
    assert [(c.numero, str(c.monto)) for c in convenio_vista.cuotas] == [
        (1, "1500.00"),
        (2, "1500.00"),
    ]
    assert (visita.gestion.canal, visita.visita.resultado) == ("CAMPO", "NO_LOCALIZADO")
    assert anulada.estado == "ANULADA" and anulada.anulacion.motivo == "Ya no aplica."


def test_el_mismo_archivo_dos_veces_no_duplica_nada(tmp_path):
    _cuentas(1, 2, 3, 4)
    ruta = _archivo(tmp_path, COMPLETO)
    _importar(ruta)

    otra_vez = _importar(ruta)

    assert (otra_vez.ya_registradas, otra_vez.nuevos, otra_vez.total_de_problemas) == (9, 0, 0)
    assert eventos() == 9


def test_una_linea_repetida_con_el_mismo_contenido_cuenta_una_vez(tmp_path):
    _cuentas(1)

    reporte = _importar(_archivo(tmp_path, [gestion("imp-g-0001"), gestion("imp-g-0001")]))

    assert (reporte.lineas, reporte.repetidas, reporte.nuevos) == (2, 1, 1)
    assert eventos() == 1


def test_importar_en_lotes_pequenos_da_lo_mismo(tmp_path):
    _cuentas(1, 2, 3, 4)

    reporte = _importar(_archivo(tmp_path, COMPLETO), lote=2)

    assert (reporte.nuevos, reporte.total_de_problemas) == (9, 0)


def test_un_archivo_comprimido_se_lee_igual(tmp_path):
    _cuentas(1, 2, 3, 4)

    reporte = _importar(_archivo(tmp_path, COMPLETO, "eventos.jsonl.gz"))

    assert (reporte.archivo, reporte.nuevos) == ("eventos.jsonl.gz", 9)


def test_con_dry_run_no_registra_nada_y_dice_cuanto_registraria(tmp_path):
    _cuentas(1, 2, 3, 4)

    reporte = _importar(_archivo(tmp_path, COMPLETO), dry_run=True)

    assert (reporte.nuevos, reporte.confirmada) == (9, False)
    assert eventos() == 0


def test_se_refiere_por_su_llave_a_lo_que_registro_la_api(tmp_path):
    cuentas = _cuentas(1)
    registro.registrar_gestion(
        cuentas[1].cuenta_id,
        "api-g-0001",
        registro.DatosGestion(
            ocurrido_en=registro.datetime.fromisoformat("2026-09-05T10:00:00-06:00"),
            canal=Canal.TELEFONICA,
            medio=Medio.LLAMADA,
            nivel_contacto=NivelContacto.CONTACTO_TITULAR,
            resultado=ResultadoGestion.PROMESA,
        ),
    )

    reporte = _importar(_archivo(tmp_path, [promesa("imp-p-0001", "api-g-0001")]))

    assert reporte.registrados == {"PROMESA_CREADA": 1}
    with sesion() as s:
        origenes = dict(
            s.execute(text("SELECT idempotency_key, origen_registro FROM evento_lifecycle")).all()
        )
    assert origenes == {"api-g-0001": "API", "imp-p-0001": "IMPORTACION"}


def test_las_anulaciones_se_aplican_al_final_y_anulan_lo_que_nacio_de_su_gestion(tmp_path):
    _cuentas(1)
    # En el archivo, la anulacion va antes que la promesa: el estado final es el mismo que por la
    # API si la promesa se registra primero.
    lineas = [
        gestion("imp-g-0001"),
        cierre("GESTION_ANULADA", "imp-a-0001", "imp-g-0001", "2026-09-06T09:00:00-06:00"),
        promesa("imp-p-0001", "imp-g-0001"),
    ]

    reporte = _importar(_archivo(tmp_path, lineas))

    assert reporte.nuevos == 3
    with sesion() as s:
        vista = consultas.obtener_promesa(s, _recurso("promesa_id", "promesa_pago", "imp-p-0001"))
    assert vista.estado == "ANULADA"


# --- con un problema no se registra nada ----------------------------------------------------------


@pytest.mark.parametrize(
    ("lineas", "codigo"),
    [
        ([gestion("imp-g-0001", 9)], "CUENTA_NO_ENCONTRADA"),
        ([promesa("imp-p-0001", "imp-g-0404")], "REFERENCIA_NO_ENCONTRADA"),
        (
            [gestion("imp-g-0001"), promesa("imp-p-0001", "imp-g-0001")]
            + [promesa("imp-p-0002", "imp-p-0001")],
            "REFERENCIA_INVALIDA",
        ),
        (
            [gestion("imp-g-0001", resultado="CONTACTO"), promesa("imp-p-0001", "imp-g-0001")],
            "GESTION_SIN_PROMESA",
        ),
        (
            [gestion("imp-g-0001"), promesa("imp-p-0001", "imp-g-0001")]
            + [promesa("imp-p-0002", "imp-g-0001")],
            "PROMESA_YA_REGISTRADA",
        ),
        (
            [gestion("imp-g-0001"), promesa("imp-p-0001", "imp-g-0001")]
            + [
                cierre("PROMESA_CANCELADA", "imp-c-0001", "imp-p-0001", "2026-09-01T09:00:00-06:00")
            ],
            "OCURRIDO_ANTES_DEL_EVENTO",
        ),
        ([gestion("imp-g-0001", ocurrido_en="2099-01-01T10:00:00-06:00")], "OCURRIDO_EN_FUTURO"),
        (
            [gestion("imp-g-0001"), promesa("imp-p-0001", "imp-g-0001", fecha_limite="2026-09-04")],
            "FECHA_LIMITE_ANTERIOR",
        ),
        (
            [gestion("imp-g-0001")]
            + [
                cierre(
                    "GESTION_ANULADA", f"imp-a-000{n}", "imp-g-0001", "2026-09-06T09:00:00-06:00"
                )
                for n in (1, 2)
            ],
            "GESTION_YA_ANULADA",
        ),
        ([gestion("imp-g-0001", resultado="SIN_RESPUESTA")], "GESTION_INCOHERENTE"),
        (
            [gestion("imp-g-0001", observacion="Llamar al 5512345678 en la tarde.")],
            "DATOS_PERSONALES",
        ),
        ([gestion("imp-g-0001", registrado_en="2026-09-05T10:00:00-06:00")], "ENTRADA_INVALIDA"),
        (["{esto no es JSON"], "ENTRADA_INVALIDA"),
        (
            [
                gestion("imp-g-0001", resultado="CONVENIO"),
                convenio("imp-v-0001", "imp-g-0001", monto_total_acordado="2999.00"),
            ],
            "CUOTAS_INCOHERENTES",
        ),
        (
            [gestion("imp-g-0001"), {**gestion("imp-g-0001"), "resultado": "CONTACTO"}],
            "LLAVE_REUTILIZADA",
        ),
        (
            [
                gestion("imp-g-0001", resultado="CONVENIO"),
                convenio("imp-v-0001", "imp-g-0001", fecha_fin="2026-09-01", cuotas=[]),
            ],
            "CONVENIO_INCOHERENTE",
        ),
        (
            [
                gestion(
                    "imp-g-0001",
                    canal="CAMPO",
                    medio=None,
                    resultado="VISITA_REALIZADA",
                    visita={
                        "resultado": "CONTACTO_TITULAR",
                        "inicio": "2026-09-05T11:00:00-06:00",
                        "fin": "2026-09-05T09:00:00-06:00",
                    },
                )
            ],
            "VISITA_INCOHERENTE",
        ),
    ],
)
def test_un_problema_en_cualquier_linea_no_registra_nada(tmp_path, lineas, codigo):
    _cuentas(1)
    ruta = _archivo(tmp_path, [gestion("imp-g-0900"), *lineas])

    reporte = _importar(ruta)

    assert reporte.total_de_problemas >= 1 and not reporte.confirmada
    assert codigo in [p.codigo for p in reporte.problemas], reporte.problemas
    assert all(p.linea >= 2 for p in reporte.problemas)  # la primera linea esta bien
    assert eventos() == 0


def test_una_llave_que_la_api_uso_para_otra_peticion_no_se_reutiliza(tmp_path):
    _cuentas(1)
    _importar(_archivo(tmp_path, [gestion("imp-g-0001")]))

    reporte = _importar(
        _archivo(tmp_path, [gestion("imp-g-0001", resultado="CONTACTO")], "b.jsonl")
    )

    (problema,) = reporte.problemas
    assert (problema.codigo, problema.llave, problema.linea) == (
        "LLAVE_REUTILIZADA",
        "imp-g-0001",
        1,
    )
    assert "GESTION_REGISTRADA" in problema.detalle
    assert eventos() == 1


def test_una_gestion_ya_anulada_no_recibe_una_promesa_ni_se_anula_otra_vez(tmp_path):
    _cuentas(1)
    _importar(
        _archivo(
            tmp_path,
            [
                gestion("imp-g-0001"),
                cierre("GESTION_ANULADA", "imp-a-0001", "imp-g-0001", "2026-09-06T09:00:00-06:00"),
            ],
        )
    )

    reporte = _importar(
        _archivo(
            tmp_path,
            [
                promesa("imp-p-0001", "imp-g-0001"),
                cierre("GESTION_ANULADA", "imp-a-0002", "imp-g-0001", "2026-09-07T09:00:00-06:00"),
            ],
            "b.jsonl",
        )
    )

    assert [(p.linea, p.codigo) for p in reporte.problemas] == [
        (1, "GESTION_ANULADA"),
        (2, "GESTION_YA_ANULADA"),
    ]
    assert eventos() == 2


def test_lo_que_se_refiere_a_una_linea_con_problemas_tambien_se_reporta(tmp_path):
    _cuentas(1)

    reporte = _importar(
        _archivo(
            tmp_path,
            [gestion("imp-g-0001", 9), promesa("imp-p-0001", "imp-g-0001")],
        )
    )

    assert [(p.linea, p.codigo) for p in reporte.problemas] == [
        (1, "CUENTA_NO_ENCONTRADA"),
        (2, "REFERENCIA_NO_ENCONTRADA"),
    ]
    assert "(linea 1) no se registro: tiene problemas" in reporte.problemas[1].detalle


# --- fuera de orden -------------------------------------------------------------------------------


def test_lo_que_se_importa_fuera_de_orden_queda_en_la_historia_por_su_momento(tmp_path, cliente):
    # La mision: se procesan A, C y D, y despues B. La historia ordenada por ocurrido_en es A, B,
    # C, D, igual que si hubieran llegado en orden.
    cuenta = _cuentas(1)[1]
    a, b, c, d = (
        gestion(f"imp-g-000{n}", ocurrido_en=f"2026-09-0{n}T10:00:00-06:00", resultado=r)
        for n, r in ((1, "CONTACTO"), (2, "RECHAZO"), (3, "CONTACTO"), (4, "PROMESA"))
    )

    _importar(_archivo(tmp_path, [a, c, d], "acd.jsonl"))
    _importar(_archivo(tmp_path, [b], "b.jsonl"))

    respuesta = cliente.get(
        f"/cuentas/{cuenta.cuenta_id}/lifecycle", params={"orden": "asc", "dominio": "OPERACIONAL"}
    ).json()
    historia = [
        (e["operacional"]["evento"]["idempotency_key"], e["operacional"]["resultado"])
        for e in respuesta["elementos"]
    ]
    assert historia == [
        ("imp-g-0001", "CONTACTO"),
        ("imp-g-0002", "RECHAZO"),
        ("imp-g-0003", "CONTACTO"),
        ("imp-g-0004", "PROMESA"),
    ]
    registros = {
        e["operacional"]["evento"]["idempotency_key"]: e["operacional"]["evento"]["registrado_en"]
        for e in respuesta["elementos"]
    }
    # B se registro despues que C y D, aunque ocurrio antes: los dos tiempos se conservan.
    assert registros["imp-g-0002"] > registros["imp-g-0004"]


# --- por la linea de comandos ---------------------------------------------------------------------


def test_cargar_lifecycle_registra_y_dice_que_registro(tmp_path):
    _cuentas(1, 2, 3, 4)
    ruta = _archivo(tmp_path, COMPLETO)

    en_seco = cli.invoke(app, ["cargar-lifecycle", str(ruta), "--dry-run"])
    primero = cli.invoke(app, ["cargar-lifecycle", str(ruta)])
    segundo = cli.invoke(app, ["cargar-lifecycle", str(ruta)])

    assert en_seco.exit_code == primero.exit_code == segundo.exit_code == 0, primero.output
    assert "Con --dry-run no se registro nada; se registrarian 9 eventos." in en_seco.output
    assert "eventos nuevos: 9" in primero.output
    assert "    GESTION_REGISTRADA: 4" in primero.output
    assert "Se registraron 9 eventos en una transaccion" in primero.output
    assert "ya registradas antes (misma llave, mismo contenido): 9" in segundo.output
    assert "eventos nuevos: 0" in segundo.output
    assert eventos() == 9


def test_cargar_lifecycle_con_problemas_termina_con_codigo_1_y_dice_cada_uno(tmp_path):
    _cuentas(1)
    ruta = _archivo(tmp_path, [gestion("imp-g-0001", 9), "{roto"])

    resultado = cli.invoke(app, ["cargar-lifecycle", str(ruta)])
    sin_archivo = cli.invoke(app, ["cargar-lifecycle", str(tmp_path / "no-existe.jsonl")])

    assert resultado.exit_code == 1
    assert "Problemas: 2. No se registro nada." in resultado.output
    assert "linea 1 (imp-g-0001): CUENTA_NO_ENCONTRADA" in resultado.output
    assert "linea 2: ENTRADA_INVALIDA" in resultado.output
    assert sin_archivo.exit_code == 1 and "No existe el archivo" in sin_archivo.output
    assert eventos() == 0


# --- dos importaciones a la vez -------------------------------------------------------------------


def _esperar_bloqueo(segundos: float = 30) -> None:
    """Hasta que una sesion de esta base este esperando un candado: la otra importacion."""
    import time

    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        with sesion() as s:
            esperando = s.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                    "AND wait_event_type = 'Lock'"
                )
            ).scalar_one()
        if esperando:
            return
        time.sleep(0.05)
    raise AssertionError("Ninguna sesion llego a esperar un candado.")


def test_dos_importaciones_del_mismo_archivo_a_la_vez_registran_una_sola_vez(tmp_path, monkeypatch):
    import threading

    from motor_cartera.lifecycle import importacion
    from motor_cartera.lifecycle.importacion import ImportacionConcurrente

    _cuentas(1, 2, 3, 4)
    ruta = _archivo(tmp_path, COMPLETO)
    llego, soltar = threading.Event(), threading.Event()
    original = importacion._registrar

    def detenida(*args, **kwargs):
        original(*args, **kwargs)
        if not llego.is_set():
            llego.set()
            assert soltar.wait(60)

    monkeypatch.setattr(importacion, "_registrar", detenida)

    def en_otro_hilo():
        salida: dict = {}

        def correr() -> None:
            try:
                salida["resultado"] = _importar(ruta)
            except BaseException as exc:
                salida["error"] = exc

        hilo = threading.Thread(target=correr, daemon=True)
        hilo.start()
        return hilo, salida

    primero, salida_1 = en_otro_hilo()
    assert llego.wait(60)
    # La segunda registra las mismas llaves mientras la primera no confirma: espera su candado.
    segundo, salida_2 = en_otro_hilo()
    _esperar_bloqueo()
    soltar.set()
    for hilo in (primero, segundo):
        hilo.join(60)

    assert salida_1["resultado"].nuevos == 9
    assert isinstance(salida_2.get("error"), ImportacionConcurrente), salida_2
    assert "Vuelve a importarlo" in str(salida_2["error"])
    assert eventos() == 9
    otra_vez = _importar(ruta)  # volver a importar es seguro: ya no hay nada nuevo
    assert (otra_vez.nuevos, otra_vez.ya_registradas) == (0, 9)


def test_un_archivo_con_marca_de_orden_de_bytes_se_lee_igual(tmp_path):
    _cuentas(1)
    ruta = tmp_path / "con_bom.jsonl"
    ruta.write_text(json.dumps(gestion("imp-g-0001")) + "\n", encoding="utf-8-sig")

    reporte = _importar(ruta)

    assert (reporte.nuevos, reporte.total_de_problemas) == (1, 0)


def test_las_lineas_en_blanco_no_cuentan_y_un_archivo_ilegible_no_registra_nada(tmp_path):
    from motor_cartera.lifecycle.importacion import ArchivoIlegible

    _cuentas(1)
    con_blancos = _archivo(tmp_path, ["", gestion("imp-g-0001"), "   "])
    binario = tmp_path / "binario.jsonl"
    binario.write_bytes(bytes([0xFF, 0xFE, 0x00, 0x00]) + b" no es texto")

    reporte = _importar(con_blancos)

    assert (reporte.lineas, reporte.nuevos) == (1, 1)
    with pytest.raises(ArchivoIlegible, match="No se pudo leer"):
        _importar(binario)
    assert eventos() == 1


def test_con_muchos_problemas_el_reporte_describe_los_primeros_y_cuenta_todos(tmp_path):
    from motor_cartera.lifecycle.importacion import PROBLEMAS_EN_EL_REPORTE

    lineas = ["{roto"] * (PROBLEMAS_EN_EL_REPORTE + 7)

    reporte = _importar(_archivo(tmp_path, lineas))

    assert reporte.total_de_problemas == PROBLEMAS_EN_EL_REPORTE + 7
    assert len(reporte.problemas) == PROBLEMAS_EN_EL_REPORTE
