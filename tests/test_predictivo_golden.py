"""Las goldens de predictive/model-v1 sobre la base: ninguna feature ve lo que paso o se supo
despues de su frontera, ningun label ve lo que se supo despues de la suya, y el manifiesto rechaza
lo que dejaria pasar una fuga.

- 111: un pago de T+1 nunca es una feature; si es un label;
- 112: un reverso posterior a la ventana no anula el label; uno dentro de ella si; el reverso de
  un pago anterior a la ventana no entra a su neto;
- 113: un evento registrado despues de K no es una feature, aunque haya ocurrido antes de T;
- 114: una evaluacion de promesas terminada despues de K no se usa, aunque su as_of sea T;
- 115: un split sin la purga entre bloques no se abre;
- 116: un label sin cobertura es NULL (CENSURADA), nunca cero.

El escenario se describe en predictivo_escenarios.py.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from predictivo_escenarios import COBERTURA, CORTES, ZONA, T, features_y_labels, golden, manifiesto
from sqlalchemy import text

from motor_cartera.db.sesion import sesion
from motor_cartera.predictivo import features as features_
from motor_cartera.predictivo import manifiesto as manifiesto_
from motor_cartera.predictivo.features import FEATURES


@pytest.fixture(scope="module")
def g(bd_del_modulo, tmp_path_factory):
    return golden(tmp_path_factory.mktemp("predictivo_golden"))


@pytest.fixture(scope="module")
def a_k(g):
    """Las features a K y los labels a L."""
    return features_y_labels(g, known_at=g.K, label_known_at=g.L)


# --- las features ---------------------------------------------------------------------------------


def test_un_pago_posterior_a_T_no_es_feature_y_si_es_label(a_k):
    """Golden 111: el pago del dia T es feature; el de T+1 es label, nunca feature."""
    uno = a_k.filas[1]
    assert uno["recuperacion_neta_30d_pasados"] == Decimal("500.00")
    assert uno["pagos_positivos_30d"] == 1
    assert uno["dias_desde_ultima_recuperacion_positiva"] == 0
    assert uno["neta_30d"] == Decimal("300.00")


def test_los_reversos_de_la_ventana(a_k):
    """Golden 112: el reverso de la 2 llega despues de la ventana: su label sigue en 400. El de la
    3 llega dentro: neto cero. El de la 4 anula un pago anterior a la ventana: el neto de la
    ventana no lo cuenta, y a K la feature de la 4 todavia ve su pago de 250."""
    assert a_k.filas[2]["neta_30d"] == Decimal("400.00")
    assert a_k.filas[3]["neta_30d"] == Decimal("0.00")
    assert a_k.filas[4]["neta_30d"] == Decimal("0.00")
    assert a_k.filas[4]["recuperacion_neta_30d_pasados"] == Decimal("250.00")


def test_un_evento_registrado_despues_de_K_no_es_feature(g, a_k):
    """Golden 113: a K la 5 tiene un contacto en sus 30 dias; el segundo, del mismo dia T-1, se
    registro despues de K. Con la frontera L, los dos."""
    assert a_k.filas[5]["contactos_30d"] == 1
    assert a_k.filas[5]["dias_desde_ultimo_contacto"] == 1
    a_l = features_y_labels(g, known_at=g.L)
    assert a_l.filas[5]["contactos_30d"] == 2


def test_una_evaluacion_terminada_despues_de_K_no_se_usa(g, a_k):
    """Golden 114: a K la evaluacion conocida mas reciente es la del 2026-09-02, aunque exista una
    con as_of T: termino despues. A L, la de T."""
    assert a_k.disponibilidad.evaluacion_as_of == CORTES[2]
    assert features_y_labels(g, known_at=g.L).disponibilidad.evaluacion_as_of == T


def test_la_cobertura_de_pagos_de_las_features(g, a_k):
    """Las ventanas de 30 y 90 dias estan cubiertas a K (el archivo de pagos va del 2026-06-11 al
    dia T); con un corte cuya ventana de 90 dias empieza antes del primer pago conocido, las
    features de pagos de 90 dias son NULL, nunca cero."""
    assert a_k.disponibilidad.pagos == {30: True, 90: True}
    temprano = features_y_labels(g, as_of=CORTES[0], known_at=g.K)
    assert temprano.disponibilidad.pagos[90] is False
    assert all(f["recuperacion_neta_90d_pasados"] is None for f in temprano.filas.values())


def test_el_feature_hash_de_la_base_es_el_de_python(a_k):
    """El feature_hash que calcula PostgreSQL es el SHA-256 de la forma canonica de Python, cuenta
    por cuenta: lo que guarda una prediccion se puede verificar sin la base."""
    assert len(a_k.crudas) == 9
    for fila in a_k.crudas.values():
        valores = {f.nombre: fila[f.nombre] for f in FEATURES}
        assert bytes(fila["feature_hash"]) == features_.feature_hash(valores)


def test_las_features_de_historia(a_k):
    siete = a_k.filas[7]
    assert siete["numero_cortes_observados"] == 4
    assert siete["delta_saldo_1_corte"] < 0 and siete["tendencia_saldo"] < 0
    assert siete["delta_dias_atraso_1_corte"] == 7


# --- los labels -----------------------------------------------------------------------------------


def test_los_labels_de_contacto_y_de_promesa(g, a_k):
    """El contacto de la ventana de la 5 cuenta; el de la 6 se anulo dentro de la ventana; la
    promesa de la 7 se creo (cancelarla despues no cambia su creacion); el contacto de la 9 se
    registro despues de L."""
    assert a_k.filas[5]["contacto_14d"] == 1
    assert a_k.filas[6]["contacto_14d"] == 0
    assert a_k.filas[7]["promesa_30d"] == 1 and a_k.filas[7]["contacto_14d"] == 1
    assert a_k.filas[9]["contacto_14d"] == 0
    tarde = features_y_labels(g, known_at=g.K, label_known_at=g.L + timedelta(days=1))
    assert tarde.filas[9]["contacto_14d"] == 1


def test_un_label_censurado_es_nulo(g):
    """Golden 116: con un label que no madura, el dataset lo deja NULL, no 0."""
    censurado = features_y_labels(
        g, known_at=g.K, label_known_at=g.L, pagos=False, contacto=True, promesa=False
    )
    assert all(f["neta_30d"] is None for f in censurado.filas.values())
    assert all(f["promesa_30d"] is None for f in censurado.filas.values())
    assert {f["contacto_14d"] for f in censurado.filas.values()} == {0, 1}


# --- el manifiesto --------------------------------------------------------------------------------


def _resolver(datos: dict):
    with sesion() as s:
        try:
            return manifiesto_.resolver(s, manifiesto_.leer(datos))
        finally:
            # Lo resuelto queda leible fuera de la sesion: nada se registro.
            s.expunge_all()
            s.rollback()


def test_un_manifiesto_valido_y_su_hash(g):
    datos = manifiesto(g)
    r = _resolver(datos)
    assert [a.split for a in r.anclas] == ["TRAIN", "VALIDATION", "PURGE", "TEST"]
    assert len(r.hash_manifiesto) == 64 and len(r.firma) == 64
    # Golden 127: el orden en que se declaran las anclas y las interpretaciones no cambia nada.
    otro = {
        **datos,
        "anclas": list(reversed(datos["anclas"])),
        "interpretaciones_pagos": list(reversed(datos["interpretaciones_pagos"])),
    }
    assert _resolver(otro).firma == r.firma
    # Cualquier dato de un ancla cambia la firma: aqui, la cobertura declarada de la de PURGE.
    cambiado = manifiesto(g, cambios={CORTES[2]: {"pagos_completos_hasta": "2026-10-08"}})
    assert _resolver(cambiado).firma != r.firma


def test_la_purga_entre_bloques(g):
    """Golden 115: con VALIDATION el 2026-09-02, la ventana del label de TRAIN (07-01 + 30) no lo
    alcanza, pero la de VALIDATION (09-02 + 30) si alcanza TEST (09-09): no se abre."""
    datos = manifiesto(g, splits={CORTES[0]: "TRAIN", CORTES[2]: "VALIDATION", T: "TEST"})
    with pytest.raises(manifiesto_.PurgaInsuficiente):
        _resolver(datos)


@pytest.mark.parametrize(
    ("caso", "error"),
    [
        ("pocas", manifiesto_.AnclasInsuficientes),
        ("repetida", manifiesto_.AnclaRepetida),
        ("as_of", manifiesto_.AsOfDistintoDelCorte),
        ("futuro", manifiesto_.KnownAtFuturo),
        ("invertidos", manifiesto_.RelojesInvertidos),
        ("no_conocido", manifiesto_.CorteNoConocido),
        ("cobertura", manifiesto_.CoberturaNoConocida),
        ("split", manifiesto_.SplitInvalido),
        ("desordenado", manifiesto_.SplitDesordenado),
        ("sin_maduras", manifiesto_.SplitSinMaduras),
        ("zona", manifiesto_.ZonaHorariaInvalida),
        ("politica", manifiesto_.PoliticaNoSoportada),
        ("sin_interpretacion", manifiesto_.InterpretacionFaltante),
        ("duplicada", manifiesto_.VentanaDuplicada),
        ("no_vigente", manifiesto_.InterpretacionNoVigente),
        ("inexistente", manifiesto_.InterpretacionNoEncontrada),
    ],
)
def test_un_manifiesto_invalido_no_se_resuelve(g, caso, error):
    """Cada regla del manifiesto, fail-closed, con su codigo estable."""
    from predictivo_escenarios import vigentes

    base = manifiesto(g)
    anclas = base["anclas"]
    if caso == "pocas":
        datos = {**base, "anclas": anclas[:2]}
    elif caso == "repetida":
        datos = {**base, "anclas": [*anclas, anclas[0]]}
    elif caso == "as_of":
        datos = manifiesto(g, cambios={T: {"feature_as_of": "2026-09-10"}})
    elif caso == "futuro":
        manana = (g.L + timedelta(days=3)).isoformat()
        datos = manifiesto(g, cambios={T: {"label_known_at": manana}})
    elif caso == "invertidos":
        datos = manifiesto(
            g, cambios={T: {"feature_known_at": g.L.isoformat(), "label_known_at": g.K.isoformat()}}
        )
    elif caso == "no_conocido":
        datos = manifiesto(g, cambios={T: {"feature_known_at": "2026-01-01T00:00:00+00:00"}})
    elif caso == "cobertura":
        datos = manifiesto(g, cambios={T: {"pagos_completos_hasta": "2027-01-01"}})
    elif caso == "split":
        datos = manifiesto(g, cambios={T: {"split": "OTRO"}})
    elif caso == "desordenado":
        datos = manifiesto(g, splits={CORTES[0]: "VALIDATION", CORTES[1]: "TRAIN", T: "TEST"})
    elif caso == "sin_maduras":
        datos = manifiesto(g, cambios={T: {"pagos_completos_hasta": "2026-09-30"}})
    elif caso == "zona":
        datos = {**base, "zona_horaria": "America/Atlantida"}
    elif caso == "politica":
        datos = {**base, "version_politica": "predictive-training/public-v0"}
    elif caso == "sin_interpretacion":
        datos = {**base, "interpretaciones_pagos": base["interpretaciones_pagos"][:1]}
    elif caso == "duplicada":
        previas = [
            e for e in _todas_de_septiembre() if str(e) not in base["interpretaciones_pagos"]
        ]
        datos = {**base, "interpretaciones_pagos": [*base["interpretaciones_pagos"], previas[0]]}
    elif caso == "no_vigente":
        septiembre = {
            str(e.motor_pagos_run_id) for e in vigentes(g.L) if e.periodo_desde == date(2026, 9, 1)
        }
        previas = [e for e in _todas_de_septiembre() if e not in septiembre]
        datos = {
            **base,
            "interpretaciones_pagos": [
                previas[0] if x in septiembre else x for x in base["interpretaciones_pagos"]
            ],
        }
    else:
        datos = {**base, "interpretaciones_pagos": ["00000000-0000-0000-0000-000000000001"]}
    with pytest.raises(error) as exc:
        _resolver(datos)
    assert exc.value.codigo == error.codigo


def _todas_de_septiembre() -> list[str]:
    with sesion() as s:
        return [
            str(x)
            for (x,) in s.execute(
                text(
                    "SELECT motor_pagos_run_id FROM ejecucion_motor_pagos "
                    "WHERE periodo_desde = '2026-09-01' AND estado = 'EXITOSA' ORDER BY id"
                )
            ).all()
        ]


def test_un_instante_sin_zona_no_se_lee(g):
    datos = manifiesto(g, cambios={T: {"feature_known_at": "2026-10-01T00:00:00"}})
    with pytest.raises(manifiesto_.KnownAtSinZona):
        manifiesto_.leer(datos)


def test_proponer_un_manifiesto_del_golden(g):
    """construir-manifiesto-predictivo: TEST la ultima ancla madura, VALIDATION la anterior con
    la purga, TRAIN las anteriores con la suya; las demas, PURGE. Lo propuesto se resuelve."""
    candidatos = [manifiesto_.CorteCandidato(g.cortes[f], f, g.K) for f in CORTES]
    with sesion() as s:
        propuesta = manifiesto_.proponer(
            s,
            despacho_id="DSP_001",
            cartera_id="CARTERA_PRINCIPAL",
            zona=ZONA,
            cortes=candidatos,
            label_known_at=g.L,
            pagos_completos_hasta=COBERTURA,
            lifecycle_completo_hasta=COBERTURA,
            anclas_validation=1,
            anclas_test=1,
        )
        s.rollback()
    assert [a["split"] for a in propuesta["anclas"]] == ["TRAIN", "VALIDATION", "PURGE", "TEST"]
    # Propone solo las interpretaciones de los meses que tocan las ventanas maduras.
    assert len(propuesta["interpretaciones_pagos"]) == 4
    declarado = manifiesto(g, interpretaciones=propuesta["interpretaciones_pagos"])
    assert _resolver(propuesta).firma == _resolver(declarado).firma
    with sesion() as s, pytest.raises(manifiesto_.AnclasInsuficientes):
        manifiesto_.proponer(
            s,
            despacho_id="DSP_001",
            cartera_id="CARTERA_PRINCIPAL",
            zona=ZONA,
            cortes=candidatos,
            label_known_at=g.L,
            pagos_completos_hasta=COBERTURA,
            lifecycle_completo_hasta=COBERTURA,
            anclas_validation=2,
            anclas_test=2,
        )


def test_el_dataset_de_un_manifiesto(g):
    """El dataset del golden: una fila por cuenta y ancla, sus conteos por split y target, su hash
    determinista, y ningun label de un target censurado."""
    from motor_cartera.db.sesion import sesion as sesiones
    from motor_cartera.predictivo import dataset

    # El manifiesto valido, con el lifecycle del TEST hasta el 2026-09-30: el manifiesto lo
    # rechazaria (TEST sin promesas maduras), el dataset lo construye y lo censura.
    r = _resolver(manifiesto(g))
    corto = date(2026, 9, 30)
    anclas = [
        dataset.AnclaDataset(
            a.corte.id,
            a.corte.corte_id,
            a.fecha,
            a.declarada.feature_known_at,
            a.declarada.label_known_at,
            a.declarada.pagos_completos_hasta,
            corto if a.split == "TEST" else a.declarada.lifecycle_completo_hasta,
            a.split,
            a.hash(),
        )
        for a in r.anclas
    ]
    interpretaciones = [i.ejecucion.id for i in r.interpretaciones]

    def construir():
        return dataset.construir(
            sesiones,
            despacho_id="DSP_001",
            cartera_id="CARTERA_PRINCIPAL",
            zona=ZONA,
            anclas=anclas,
            interpretaciones=interpretaciones,
        )

    uno, otro = construir(), construir()
    assert uno.hash == otro.hash and len(uno.hash) == 64
    assert uno.filas_por_split == {"TRAIN": 9, "VALIDATION": 9, "PURGE": 9, "TEST": 9}
    assert uno.cuentas == 9
    test = next(b for b in uno.bloques if b.split == "TEST")
    # El lifecycle del TEST solo llega al 2026-09-30: su promesa a 30 dias esta CENSURADA, sin
    # label; su contacto a 14, MADURA.
    assert test.madurez["PROMESA_30D"] == "CENSURADA"
    assert "PROMESA_30D" not in test.labels and "CONTACTO_14D" in test.labels
    assert uno.conteos["PROMESA_30D"]["TEST"]["censuradas"] == 9
    assert uno.censurados == 9
