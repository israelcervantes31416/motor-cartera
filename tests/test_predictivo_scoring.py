"""Un scoring de predictive/model-v1, su drift, la explicacion de una prediccion, la API y el CLI,
sobre el escenario generado de las pruebas (predictivo_escenarios.py) y su version PUBLICADA.

Las goldens: cada prediccion en rango y el riesgo exactamente 1 - p, y la base rechaza lo que no
lo cumple (122); el hash de las predicciones se recalcula de lo publicado; reconstruir un scoring
con las mismas entradas da exactamente lo mismo (129); el PSI publicado es el de sus bins (126);
la explicacion suma el predictor y reproduce lo publicado (125). Y todo o nada, la firma, la cola.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

import numpy as np
import pytest
from predictivo_escenarios import (
    abrir_scoring,
    borrar_scoring,
    corte_de,
    entrenar,
    generado,
    propuesto,
    puntuar,
    scoring_de,
    version_de,
)
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from typer.testing import CliRunner

from motor_cartera.cli import app as cli_app
from motor_cartera.db.sesion import restriccion, sesion
from motor_cartera.predictivo import explicacion, metricas
from motor_cartera.predictivo import features as features_
from motor_cartera.predictivo import scoring as motor
from motor_cartera.predictivo import targets as targets_
from motor_cartera.predictivo.features import CATEGORICAS, NUMERICAS

cli = CliRunner()
ULTIMO = "2026-07-15"
NO_EXISTE = "00000000-0000-4000-8000-000000000000"


@pytest.fixture(scope="module")
def carpeta(bd_del_modulo, tmp_path_factory):
    return tmp_path_factory.mktemp("predictivo_scoring")


@pytest.fixture(scope="module")
def escenario(carpeta):
    return generado(carpeta)


@pytest.fixture(scope="module")
def modelo(escenario):
    e = entrenar(propuesto(escenario))
    assert e.resultado == "MODELO_PUBLICADO", e.detalle
    return version_de(e.id)


@pytest.fixture(scope="module")
def puntuado(escenario, modelo):
    s = puntuar(modelo.modelo_version_id, corte_de(escenario, ULTIMO)["corte_id"])
    assert s.estado == "EXITOSA", s.detalle
    return s


def _filas(ejecucion_id: int) -> list[dict]:
    with sesion() as s:
        return [
            dict(f)
            for f in s.execute(
                text(
                    "SELECT c.cuenta_id, p.* FROM prediccion_cuenta p "
                    "JOIN cuenta_canonica c ON c.id = p.cuenta_canonica_id "
                    "WHERE p.ejecucion_scoring_predictivo_id = :e ORDER BY c.cuenta_id"
                ),
                {"e": ejecucion_id},
            ).mappings()
        ]


# --- puntuar --------------------------------------------------------------------------------------


def test_una_prediccion_por_cuenta_en_rango(escenario, puntuado):
    """Golden 122: cada cuenta del corte tiene su prediccion, cada probabilidad en [0, 1], el riesgo
    exactamente su complemento, la recuperacion esperada no negativa, y su feature_hash."""
    filas = _filas(puntuado.id)
    assert len(filas) == puntuado.predicciones_publicadas == puntuado.cuentas_objetivo > 500
    for f in filas:
        for nombre in ("p_recuperacion_positiva_30d", "p_contacto_14d", "p_promesa_30d"):
            assert Decimal(0) <= f[nombre] <= Decimal(1)
        assert f["riesgo_sin_recuperacion_30d"] + f["p_recuperacion_positiva_30d"] == Decimal(1)
        assert f["recuperacion_positiva_esperada_30d"] >= 0
        assert len(bytes(f["feature_hash"])) == 32
        assert f["estado"] in ("COMPLETA", "CON_IMPUTACION")
        assert (f["estado"] == "COMPLETA") == (f["motivos"] == [])
    assert _cuantos_drift(puntuado.id) == len(NUMERICAS) + len(CATEGORICAS) + 4
    assert "no es el efecto" in puntuado.detalle


def _cuantos_drift(ejecucion_id: int) -> int:
    with sesion() as s:
        return s.execute(
            text(
                "SELECT count(*) FROM drift_predictivo WHERE ejecucion_scoring_predictivo_id = :e"
            ),
            {"e": ejecucion_id},
        ).scalar_one()


def test_el_hash_de_las_predicciones_se_recalcula_de_lo_publicado(puntuado):
    digesto = hashlib.sha256()
    for f in _filas(puntuado.id):
        linea = "|".join(
            [
                str(f["cuenta_id"]),
                *(
                    f"{f[n]:.8f}"
                    for n in (
                        "p_recuperacion_positiva_30d",
                        "p_contacto_14d",
                        "p_promesa_30d",
                        "riesgo_sin_recuperacion_30d",
                    )
                ),
                f"{f['recuperacion_positiva_esperada_30d']:.2f}",
                f["estado"],
                bytes(f["feature_hash"]).hex(),
            ]
        )
        digesto.update(linea.encode() + b"\n")
    assert digesto.hexdigest() == puntuado.hash_predicciones


@pytest.mark.parametrize(
    "cambio",
    [
        "p_contacto_14d = 1.5",
        "riesgo_sin_recuperacion_30d = 0.5",
        "recuperacion_positiva_esperada_30d = -1",
        "recuperacion_positiva_esperada_30d = 'NaN'",
        "estado = 'OTRO'",
        "feature_hash = '\\x00'",
    ],
)
def test_la_base_rechaza_una_prediccion_fuera_de_rango(puntuado, cambio):
    """Golden 122: los CHECK de la base, ademas del motor: una copia de una prediccion publicada
    con un valor fuera de su rango no entra, por su restriccion y no por su llave."""
    with sesion() as s:
        s.execute(
            text(
                "CREATE TEMP TABLE otra AS SELECT * FROM prediccion_cuenta "
                "WHERE ejecucion_scoring_predictivo_id = :e LIMIT 1"
            ),
            {"e": puntuado.id},
        )
        s.execute(text(f"UPDATE otra SET {cambio}"))
        with pytest.raises(IntegrityError) as exc:
            s.execute(text("INSERT INTO prediccion_cuenta SELECT * FROM otra"))
        assert restriccion(exc.value) in (
            "ck_prediccion_cuenta_rangos",
            "ck_prediccion_cuenta_estado",
        )
        s.rollback()


def test_lo_publicado_no_se_reescribe(puntuado):
    for sentencia in (
        "UPDATE prediccion_cuenta SET p_contacto_14d = 0.5 "
        "WHERE ejecucion_scoring_predictivo_id = :e",
        "DELETE FROM prediccion_cuenta WHERE ejecucion_scoring_predictivo_id = :e",
        "UPDATE drift_predictivo SET psi = 0 WHERE ejecucion_scoring_predictivo_id = :e",
        "DELETE FROM drift_predictivo WHERE ejecucion_scoring_predictivo_id = :e",
        "UPDATE ejecucion_scoring_predictivo SET hash_predicciones = NULL WHERE id = :e",
        "DELETE FROM ejecucion_scoring_predictivo WHERE id = :e",
    ):
        with sesion() as s, pytest.raises(DBAPIError):
            s.execute(text(sentencia), {"e": puntuado.id})
            s.commit()


def test_lo_que_no_se_puede_puntuar_no_se_abre(escenario, modelo, puntuado):
    """Nunca "el ultimo modelo": una version que no existe o no esta PUBLICADA, un known_at sin
    zona, futuro o anterior a que existiera el corte, y un BACKTEST sin known_at, no se abren; el
    mismo scoring no se publica dos veces."""
    ultimo = corte_de(escenario, ULTIMO)
    casos = [
        (motor.ModeloNoEncontrado, dict(modelo_version_id=UUID(NO_EXISTE))),
        (motor.CorteNoEncontrado, dict(corte_id=UUID(NO_EXISTE))),
        (motor.KnownAtObligatorio, dict(modo="BACKTEST")),
        (motor.KnownAtFuturo, dict(known_at=datetime.now().astimezone() + timedelta(days=1))),
        (motor.CorteNoConocido, dict(known_at=datetime.fromisoformat("2026-01-01T00:00:00+00:00"))),
        (motor.KnownAtSinZona, dict(known_at=datetime(2026, 10, 1))),
    ]
    for error, cambios in casos:
        argumentos = {
            "modelo_version_id": modelo.modelo_version_id,
            "corte_id": ultimo["corte_id"],
            "known_at": None,
            "modo": "OPERATIVO",
            **cambios,
        }
        with pytest.raises(error):
            abrir_scoring(
                argumentos["modelo_version_id"],
                argumentos["corte_id"],
                known_at=argumentos["known_at"],
                modo=argumentos["modo"],
            )
    with pytest.raises(motor.ScoringYaPublicado):
        abrir_scoring(modelo.modelo_version_id, ultimo["corte_id"], known_at=puntuado.known_at)


class Falla(Exception):
    pass


@pytest.mark.parametrize("donde", motor.PUNTOS_DE_FALLA)
def test_una_falla_en_cualquier_punto_no_publica_nada(escenario, modelo, monkeypatch, donde):
    corte = corte_de(escenario, "2026-07-08")
    # Un known_at propio por punto de falla (su propia firma), siempre en el pasado: el modelo del
    # modulo se entreno despues de label_known_at. Con segundos, una maquina rapida llegaba aqui
    # antes de que el reloj de la base los alcanzara, y el scoring era KNOWN_AT_FUTURO.
    known_at = datetime.fromisoformat(escenario["label_known_at"]) + timedelta(
        microseconds=1 + motor.PUNTOS_DE_FALLA.index(donde)
    )
    ejecucion_id = abrir_scoring(
        modelo.modelo_version_id, corte["corte_id"], known_at=known_at, modo="BACKTEST"
    )

    def fallar_en(nombre: str) -> None:
        if nombre == donde:
            raise Falla(nombre)

    monkeypatch.setattr(motor, "_punto_de_falla", fallar_en)
    motor.puntuar(ejecucion_id)
    fallida = scoring_de(ejecucion_id)
    assert (fallida.estado, fallida.resultado) == ("FALLIDA", "ERROR_INTERNO")
    assert (fallida.predicciones_publicadas, fallida.hash_predicciones, fallida.hash_drift) == (
        0,
        None,
        None,
    )
    assert _filas(ejecucion_id) == [] and _cuantos_drift(ejecucion_id) == 0
    monkeypatch.undo()
    otra = puntuar(modelo.modelo_version_id, corte["corte_id"], known_at=known_at, modo="BACKTEST")
    assert (otra.estado, otra.firma_inputs) == ("EXITOSA", fallida.firma_inputs)


def test_reconstruir_un_scoring_da_exactamente_lo_mismo(escenario, modelo):
    """Golden 129: el mismo modelo, corte y known_at dan las mismas predicciones y el mismo drift,
    byte por byte, aunque las tablas que se leen cambien de orden fisico."""
    corte = corte_de(escenario, "2026-06-24")
    known_at = datetime.fromisoformat(corte["known_at"])
    primera = puntuar(
        modelo.modelo_version_id, corte["corte_id"], known_at=known_at, modo="BACKTEST"
    )
    filas = _filas(primera.id)
    borrar_scoring(primera.id)
    with sesion() as s:
        for tabla, indice in (
            ("snapshot_cuenta", "ix_snapshot_cuenta_historia"),
            ("gestion_cobranza", "ix_gestion_cuenta_ocurrido"),
        ):
            s.execute(text(f"CLUSTER {tabla} USING {indice}"))
            s.execute(text(f"ANALYZE {tabla}"))
        s.commit()
    segunda = puntuar(
        modelo.modelo_version_id, corte["corte_id"], known_at=known_at, modo="BACKTEST"
    )
    assert segunda.id != primera.id
    assert segunda.hash_predicciones == primera.hash_predicciones
    assert segunda.hash_drift == primera.hash_drift
    sin_id = [{k: v for k, v in f.items() if k != "ejecucion_scoring_predictivo_id"} for f in filas]
    assert [
        {k: v for k, v in f.items() if k != "ejecucion_scoring_predictivo_id"}
        for f in _filas(segunda.id)
    ] == sin_id


# --- el drift -------------------------------------------------------------------------------------


def _drift(ejecucion_id: int) -> list[dict]:
    with sesion() as s:
        return [
            dict(f)
            for f in s.execute(
                text("SELECT * FROM drift_predictivo WHERE ejecucion_scoring_predictivo_id = :e"),
                {"e": ejecucion_id},
            ).mappings()
        ]


def test_el_psi_publicado_es_el_de_sus_bins(puntuado):
    """Golden 126: el PSI de cada fila se recalcula de sus proporciones con el epsilon de la
    politica; los bins son distribuciones, nunca una cuenta."""
    filas = _drift(puntuado.id)
    for f in filas:
        psi = metricas.psi(
            np.array(f["bins"]["proporciones_train"]),
            np.array(f["bins"]["proporciones_scoring"]),
            1e-4,
        )
        assert abs(float(f["psi"]) - round(psi, 6)) <= 2e-6, f["nombre"]
    texto = json.dumps([f["bins"] for f in filas], default=str)
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", texto)


def test_el_drift_de_un_corte_lejano_es_mayor(escenario, modelo, puntuado):
    """Drift A contra B: un corte junto a TRAIN se parece mas a TRAIN que uno meses despues (con
    meses de pagos que TRAIN no conocia). El drift no cambia ni reentrena el modelo."""
    cercano = puntuar(modelo.modelo_version_id, corte_de(escenario, "2026-04-08")["corte_id"])
    suma = {
        e.id: sum(float(f["psi"]) for f in _drift(e.id) if f["tipo"] == "FEATURE")
        for e in (cercano, puntuado)
    }
    assert suma[puntuado.id] > suma[cercano.id]
    assert puntuado.drift_resumen["feature"]["ALTO"] >= cercano.drift_resumen["feature"]["ALTO"]
    assert version_de(modelo.ejecucion_entrenamiento_predictivo_id).estado == "PUBLICADA"


# --- la explicacion -------------------------------------------------------------------------------


def test_la_explicacion_suma_el_predictor_y_reproduce_lo_publicado(puntuado):
    """Golden 125: para cada cuenta explicada y cada componente, el intercepto mas sus
    contribuciones es el predictor lineal, y su prediccion es exactamente la publicada."""
    for f in _filas(puntuado.id)[:25]:
        x = explicacion.explicar(puntuado.scoring_run_id, f["cuenta_id"])
        assert x["feature_hash"] == bytes(f["feature_hash"]).hex()
        assert set(x["features"]) == {g.nombre for g in features_.FEATURES}
        for c in x["componentes"]:
            assert c["verificacion"] == {
                "suma_igual_al_predictor": True,
                "reproduce_lo_publicado": True,
            }
            assert len(c["hacia_arriba"]) <= 5 and len(c["hacia_abajo"]) <= 5
            assert all(a["contribucion"] > 0 for a in c["hacia_arriba"])
        assert "no dice que cambiar" in x["aviso"]


def test_una_explicacion_que_no_reproduce_no_se_da(escenario, modelo):
    corte = corte_de(escenario, "2026-07-01")
    s = puntuar(modelo.modelo_version_id, corte["corte_id"])
    cuenta = _filas(s.id)[0]
    with sesion() as x:
        x.execute(text("ALTER TABLE prediccion_cuenta DISABLE TRIGGER tr_predictivo_inmutable"))
        x.execute(
            text(
                "UPDATE prediccion_cuenta SET feature_hash = sha256('otra cosa') "
                "WHERE ejecucion_scoring_predictivo_id = :e AND cuenta_canonica_id = :c"
            ),
            {"e": s.id, "c": cuenta["cuenta_canonica_id"]},
        )
        x.execute(text("ALTER TABLE prediccion_cuenta ENABLE TRIGGER tr_predictivo_inmutable"))
        x.commit()
    with pytest.raises(explicacion.NoReproduce):
        explicacion.explicar(s.scoring_run_id, cuenta["cuenta_id"])
    with pytest.raises(explicacion.CuentaNoEncontrada):
        explicacion.explicar(s.scoring_run_id, UUID(NO_EXISTE))
    with pytest.raises(explicacion.ScoringNoEncontrado):
        explicacion.explicar(UUID(NO_EXISTE), cuenta["cuenta_id"])


# --- la API ---------------------------------------------------------------------------------------


def test_la_api_publica_las_definiciones(cliente):
    r = cliente.get("/predictivo/features")
    assert r.status_code == 200
    assert (r.json()["version"], r.json()["hash"]) == (
        features_.VERSION_FEATURES,
        features_.hash_features(),
    )
    r = cliente.get("/predictivo/targets")
    assert r.json()["hash"] == targets_.hash_targets()
    assert cliente.get("/predictivo/features", headers={"X-API-Key": ""}).status_code == 401


def _cola_vacia() -> None:
    """Los trabajos que dejo la carga del escenario (la ejecuto en primer plano) ya no se toman."""
    with sesion() as s:
        s.execute(
            text(
                "UPDATE trabajo_orquestacion SET estado = 'COMPLETADO', terminado_en = now(), "
                "worker_id = NULL, lease_hasta = NULL WHERE estado IN ('PENDIENTE', 'EJECUTANDO')"
            )
        )
        s.commit()


def test_la_api_entrena_y_puntua_por_la_cola(escenario, modelo, cliente, trabajar):
    _cola_vacia()
    datos = propuesto(escenario, variante=20)
    r = cliente.post("/predictivo/entrenamientos", json=datos)
    assert r.status_code == 201, r.text
    assert r.json()["estado"] == "EN_PROCESO"
    assert r.headers["Location"] == f"/predictivo/entrenamientos/{r.json()['training_run_id']}"
    assert cliente.post("/predictivo/entrenamientos", json=datos).json()["codigo"] == (
        "ENTRENAMIENTO_EN_PROCESO"
    )
    trabajar()
    e = cliente.get(r.headers["Location"]).json()
    assert (e["estado"], e["resultado"], e["estado_modelo"]) == (
        "EXITOSA",
        "MODELO_PUBLICADO",
        "PUBLICADA",
    )
    assert cliente.post("/predictivo/entrenamientos", json=datos).json()["codigo"] == (
        "ENTRENAMIENTO_YA_PUBLICADO"
    )
    m = cliente.get(f"/predictivo/modelos/{e['modelo_version_id']}").json()
    assert len(m["componentes"]) == 4 and m["ficha"]["uso_previsto"]
    lista = cliente.get("/predictivo/modelos", params={"estado": "PUBLICADA"}).json()
    assert e["modelo_version_id"] in {x["modelo_version_id"] for x in lista["elementos"]}
    r = cliente.post(
        "/predictivo/scorings",
        json={
            "modelo_version_id": e["modelo_version_id"],
            "corte_id": corte_de(escenario, "2026-06-03")["corte_id"],
            "modo": "OPERATIVO",
        },
    )
    assert r.status_code == 201, r.text
    scoring_run_id = r.json()["scoring_run_id"]
    no_publicado = cliente.get(f"/predictivo/scorings/{scoring_run_id}/predicciones")
    assert no_publicado.status_code == 409
    trabajar()
    s = cliente.get(f"/predictivo/scorings/{scoring_run_id}").json()
    assert s["estado"] == "EXITOSA" and s["predicciones_publicadas"] == s["cuentas_objetivo"]


def test_la_api_filtra_y_ordena_las_predicciones_en_la_base(puntuado, cliente):
    ruta = f"/predictivo/scorings/{puntuado.scoring_run_id}/predicciones"
    todas = cliente.get(ruta, params={"por_pagina": 500}).json()
    assert todas["total"] == puntuado.predicciones_publicadas
    riesgo = cliente.get(
        ruta, params={"orden": "RIESGO_SIN_RECUPERACION_30D", "sentido": "DESC", "por_pagina": 50}
    ).json()["elementos"]
    valores = [Decimal(x["riesgo_sin_recuperacion_30d"]) for x in riesgo]
    assert valores == sorted(valores, reverse=True)
    filtrada = cliente.get(
        ruta, params={"p_contacto_14d_min": "0.4", "producto": "NOMINA", "por_pagina": 500}
    ).json()
    assert all(
        Decimal(x["p_contacto_14d"]) >= Decimal("0.4") and x["producto"] == "NOMINA"
        for x in filtrada["elementos"]
    )
    # El rango del riesgo se filtra con el indice de la recuperacion (su complemento exacto): las
    # mismas cuentas que dice cada prediccion.
    paginas = [
        *todas["elementos"],
        *cliente.get(ruta, params={"por_pagina": 500, "pagina": 2}).json()["elementos"],
    ]
    assert len(paginas) == todas["total"]
    esperadas = {
        x["cuenta_id"]
        for x in paginas
        if Decimal("0.7") <= Decimal(x["riesgo_sin_recuperacion_30d"]) <= Decimal("0.95")
    }
    alto = cliente.get(
        ruta,
        params={
            "riesgo_sin_recuperacion_30d_min": "0.7",
            "riesgo_sin_recuperacion_30d_max": "0.95",
            "por_pagina": 500,
        },
    ).json()
    assert alto["total"] == len(esperadas) > 0
    assert {x["cuenta_id"] for x in alto["elementos"]} == esperadas
    assert cliente.get(ruta, params={"otro": "1"}).status_code == 422
    assert cliente.get(ruta, params={"orden": "SALDO"}).status_code == 422
    drift = cliente.get(f"/predictivo/scorings/{puntuado.scoring_run_id}/drift").json()
    assert len(drift["filas"]) == len(NUMERICAS) + len(CATEGORICAS) + 4
    cuenta_id = todas["elementos"][0]["cuenta_id"]
    x = cliente.get(
        f"/predictivo/scorings/{puntuado.scoring_run_id}/cuentas/{cuenta_id}/explicacion"
    )
    assert x.status_code == 200 and len(x.json()["componentes"]) == 4
    de_cuenta = cliente.get(f"/cuentas/{cuenta_id}/predictivo").json()
    assert str(puntuado.scoring_run_id) in {p["scoring_run_id"] for p in de_cuenta["elementos"]}
    assert cliente.get(f"/cuentas/{NO_EXISTE}/predictivo").status_code == 404
    assert cliente.get(f"/predictivo/scorings/{NO_EXISTE}").json()["codigo"] == (
        "SCORING_NO_ENCONTRADO"
    )


def test_las_dos_maneras_de_armar_una_pagina_filtrada_dan_lo_mismo(puntuado, monkeypatch):
    """Un filtro que deja pocas predicciones se aplica primero, con su indice, y despues se ordena;
    con muchas, se recorre el orden pedido. Las dos dan la misma pagina y el mismo total, en cada
    orden. Sin filtros, el total es lo que publico el scoring, sin contar otra vez."""
    from motor_cartera.predictivo import consultas

    filtros = [
        consultas.FiltrosPredicciones(
            minimos={"riesgo_sin_recuperacion_30d": Decimal("0.6")},
            maximos={"p_contacto_14d": Decimal("0.9")},
            orden=orden,
            sentido=sentido,
        )
        for orden, sentido in (
            (consultas.Orden.CUENTA, consultas.Sentido.ASC),
            (consultas.Orden.RIESGO_SIN_RECUPERACION_30D, consultas.Sentido.DESC),
            (consultas.Orden.P_CONTACTO_14D, consultas.Sentido.ASC),
            (consultas.Orden.RECUPERACION_POSITIVA_ESPERADA_30D, consultas.Sentido.DESC),
        )
    ]
    with sesion() as s:
        for f in filtros:
            primero = consultas.predicciones(s, puntuado, f, desplazamiento=7, limite=40)
            monkeypatch.setattr(consultas, "FILAS_PARA_FILTRAR_PRIMERO", -1)
            recorriendo = consultas.predicciones(s, puntuado, f, desplazamiento=7, limite=40)
            monkeypatch.setattr(consultas, "FILAS_PARA_FILTRAR_PRIMERO", 20_000)
            assert primero == recorriendo
            assert 0 < len(primero[1]) <= 40 and primero[0] > 47
        total, pagina = consultas.predicciones(
            s,
            puntuado,
            consultas.FiltrosPredicciones(minimos={}, maximos={}),
            desplazamiento=0,
            limite=10,
        )
        contadas = s.execute(
            text(
                "SELECT count(*) FROM prediccion_cuenta WHERE ejecucion_scoring_predictivo_id = :e"
            ),
            {"e": puntuado.id},
        ).scalar_one()
    assert total == contadas == puntuado.predicciones_publicadas and len(pagina) == 10


def test_la_api_rechaza_un_manifiesto_invalido(escenario, cliente):
    datos = propuesto(escenario, variante=21)
    datos["anclas"][-2]["split"] = "TEST"
    datos["anclas"][-3]["split"] = "VALIDATION"
    r = cliente.post("/predictivo/entrenamientos", json=datos)
    assert r.status_code == 422 and r.json()["codigo"] in (
        "SPLIT_DESORDENADO",
        "PURGA_INSUFICIENTE",
        "SPLIT_SIN_MADURAS",
    )
    assert (
        cliente.post(
            "/predictivo/entrenamientos", json={**propuesto(escenario), "extra": 1}
        ).json()["codigo"]
        == "ENTRADA_INVALIDA"
    )


# --- el CLI ---------------------------------------------------------------------------------------


def _id(salida: str, patron: str) -> str:
    encontrado = re.search(patron + r" ([0-9a-f-]{36})", salida)
    assert encontrado, salida
    return encontrado.group(1)


def test_el_cli_de_punta_a_punta(carpeta, escenario, modelo, tmp_path):
    destino = tmp_path / "manifiesto.json"
    r = cli.invoke(
        cli_app,
        [
            "construir-manifiesto-predictivo",
            "--escenario",
            str(carpeta),
            "--validation",
            "1",
            "--test",
            "1",
            "--destino",
            str(destino),
        ],
    )
    assert r.exit_code == 0, r.output
    assert "No se entreno nada" in r.output
    # Con --desde, los cortes anteriores son historia de las features, pero no anclas.
    desde = tmp_path / "desde.json"
    r = cli.invoke(
        cli_app,
        [
            "construir-manifiesto-predictivo",
            "--escenario",
            str(carpeta),
            "--desde",
            "2026-03-25",
            "--validation",
            "1",
            "--test",
            "1",
            "--destino",
            str(desde),
        ],
    )
    assert r.exit_code == 0, r.output
    todas = json.loads(destino.read_text(encoding="utf-8"))["manifiesto"]["anclas"]
    anclas = json.loads(desde.read_text(encoding="utf-8"))["manifiesto"]["anclas"]
    assert min(a["feature_as_of"] for a in anclas) == "2026-03-25"
    assert len(anclas) == len(todas) - 3
    # Es el manifiesto del modelo del modulo: ya se entreno.
    r = cli.invoke(cli_app, ["entrenar-predictivo", "--manifiesto", str(destino)])
    assert r.exit_code == 1 and "ya se entreno" in r.output
    variante = tmp_path / "variante.json"
    variante.write_text(json.dumps(propuesto(escenario, variante=30)), encoding="utf-8")
    r = cli.invoke(cli_app, ["entrenar-predictivo", "--manifiesto", str(variante)])
    assert r.exit_code == 0, r.output
    modelo_version_id = _id(r.output, "modelo_version_id")
    r = cli.invoke(cli_app, ["resumen-modelo-predictivo", "--modelo", modelo_version_id])
    assert r.exit_code == 0 and "PUBLICADA" in r.output and "IC95" in r.output
    r = cli.invoke(
        cli_app, ["score-predictivo", "--modelo", modelo_version_id, "--corte", "2026-05-27"]
    )
    assert r.exit_code == 0, r.output
    scoring_run_id = _id(r.output, "scoring")
    r = cli.invoke(cli_app, ["drift-predictivo", "--scoring", scoring_run_id])
    assert r.exit_code == 0 and "PSI" in r.output
    r = cli.invoke(
        cli_app,
        [
            "evaluar-predictivo",
            "--scoring",
            scoring_run_id,
            "--pagos-completos-hasta",
            "2026-07-15",
            "--lifecycle-completo-hasta",
            "2026-07-15",
        ],
    )
    assert r.exit_code == 0, r.output
    assert "no se evalua" not in r.output and "roc_auc" in r.output
    r = cli.invoke(
        cli_app,
        [
            "score-predictivo",
            "--modelo",
            modelo_version_id,
            "--corte",
            "2026-05-27",
            "--modo",
            "BACKTEST",
        ],
    )
    assert r.exit_code == 1 and "KNOWN_AT_OBLIGATORIO" in r.output
    r = cli.invoke(cli_app, ["score-predictivo", "--modelo", NO_EXISTE, "--corte", "2026-05-27"])
    assert r.exit_code == 1 and "MODELO_NO_ENCONTRADO" in r.output
