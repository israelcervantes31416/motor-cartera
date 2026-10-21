"""La senal de rendimiento (scripts/comparar_rendimiento.py) sobre reportes de benchmark escritos
a mano, con la forma de los de verdad: que metricas saca de cada capa, la mediana de las
repeticiones y cuando un cambio es una regresion (solo si supera sus dos tolerancias)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]


def _herramienta():
    ruta = RAIZ / "scripts" / "comparar_rendimiento.py"
    especificacion = importlib.util.spec_from_file_location("comparar_rendimiento", ruta)
    modulo = importlib.util.module_from_spec(especificacion)
    sys.modules[especificacion.name] = modulo
    especificacion.loader.exec_module(modulo)
    return modulo


rendimiento = _herramienta()


def _repeticion(directorio: Path, escala: float = 1.0, consulta_ms: float = 20.0) -> Path:
    """Los cuatro reportes de una repeticion; `escala` multiplica cada tiempo."""
    reportes = {
        "escala/benchmark_escala.json": {
            "perfil": "M",
            "cuentas": 100_000,
            "resultados": [
                {
                    "fuente": "cartera/v2",
                    "filas": 100_000,
                    "ingesta_total_s": 40.0 * escala,
                    "memoria_pico_ingesta_mb": 500.0,
                },
                {
                    "fuente": "pagos/v1",
                    "filas": 56_000,
                    "ingesta_total_s": 9.0 * escala,
                    "memoria_pico_ingesta_mb": 300.0,
                },
            ],
        },
        "motor_pagos/benchmark_motor_pagos.json": {
            "cortes": 3,
            "historias": [
                {"tipo": "CARTERA", "segundos": 10.0 * escala},
                {"tipo": "CARTERA", "segundos": 11.0 * escala},
                {"tipo": "PAGOS", "segundos": 5.0 * escala},
            ],
            "totales": {
                "observaciones": 112_000,
                "segundos_historia_pagos": 5.0 * escala,
                "segundos_motor": 30.0 * escala,
                "memoria_pico_motor_mb": 400.0,
            },
            "consultas": {"tiempos": {"movimientos_de_una_cuenta": {"mediana_ms": consulta_ms}}},
        },
        "lifecycle/benchmark_lifecycle.json": {
            "total_importacion": {"eventos": 50_000, "segundos": 15.0 * escala},
            "atribuciones": {"2026-01": {"segundos": 8.0 * escala}, "2026-02": {"segundos": 2.0}},
            "evaluacion": {"segundos": 4.0 * escala},
            "consultas": {"tiempos": {"lifecycle_de_una_cuenta": {"mediana_ms": 30.0}}},
        },
        "decision_v2/benchmark_decision_v2.json": {
            "decision_v1": {"segundos": 12.0 * escala},
            "decision_v2": {
                "segundos": 25.0 * escala,
                "memoria_pico_mb": 350.0,
                "cuentas_decididas": 99_900,
            },
            "consultas": {"tiempos": {"decision_de_una_cuenta": {"mediana_ms": 18.0}}},
        },
    }
    for ruta, contenido in reportes.items():
        archivo = directorio / ruta
        archivo.parent.mkdir(parents=True, exist_ok=True)
        archivo.write_text(json.dumps(contenido), encoding="utf-8")
    return directorio


def test_cada_capa_da_sus_tiempos_su_memoria_y_sus_consultas(tmp_path):
    valores = rendimiento.metricas(rendimiento.leer_repeticion(_repeticion(tmp_path)))

    assert valores["ingesta.cartera_v2"] == ("tiempo", 40.0)
    assert valores["historia.cortes"] == ("tiempo", 21.0)
    assert valores["lifecycle.atribucion"] == ("tiempo", 10.0)
    assert valores["decision_v2.decision"] == ("tiempo", 25.0)
    assert valores["motor_pagos.memoria"] == ("memoria", 400.0)
    assert valores["consulta.pagos.movimientos_de_una_cuenta"] == ("consulta", 20.0)
    assert {clase for clase, _ in valores.values()} == {"tiempo", "memoria", "consulta"}


def test_la_mediana_de_las_repeticiones_y_su_variacion(tmp_path):
    repeticiones = [
        rendimiento.metricas(rendimiento.leer_repeticion(_repeticion(tmp_path / str(n), f)))
        for n, f in enumerate((1.0, 1.1, 0.9))
    ]

    resumen = rendimiento.resumir(repeticiones)

    assert resumen["ingesta.cartera_v2"]["valor"] == 40.0
    assert resumen["ingesta.cartera_v2"]["minimo"] == 36.0
    assert resumen["ingesta.cartera_v2"]["maximo"] == 44.0
    assert resumen["ingesta.cartera_v2"]["n"] == 3
    assert 0.08 < resumen["ingesta.cartera_v2"]["cv"] < 0.09
    assert list(resumen)[0] == "decision_v1.decision"
    assert list(resumen)[-1].startswith("consulta.")


@pytest.mark.parametrize(
    ("escala", "consulta_ms", "esperado"),
    [
        (1.05, 20.0, {}),
        # Lo que vario entre runners de CPU distintas (hasta 48 % en un tiempo) queda dentro.
        (1.45, 20.0, {}),
        (1.6, 20.0, {"ingesta.cartera_v2": "REGRESION", "motor_pagos.interpretacion": "REGRESION"}),
        (0.4, 20.0, {"ingesta.cartera_v2": "MEJORA"}),
        (1.0, 35.0, {}),
        (1.0, 60.0, {"consulta.pagos.movimientos_de_una_cuenta": "REGRESION"}),
    ],
)
def test_una_regresion_supera_la_tolerancia_relativa_y_la_absoluta(
    tmp_path, escala, consulta_ms, esperado
):
    base = {
        "tolerancias": rendimiento.TOLERANCIAS,
        "metricas": rendimiento.resumir(
            [rendimiento.metricas(rendimiento.leer_repeticion(_repeticion(tmp_path / "base")))]
        ),
    }
    actual = rendimiento.resumir(
        [
            rendimiento.metricas(
                rendimiento.leer_repeticion(_repeticion(tmp_path / "actual", escala, consulta_ms))
            )
        ]
    )

    comparadas = rendimiento.comparar(actual, base)

    fuera = {n: m["estado"] for n, m in comparadas.items() if m["estado"] != "DENTRO"}
    for nombre, estado in esperado.items():
        assert fuera[nombre] == estado
    # Sin nada esperado, nada se marca; si no, lo demas que se marca va en la direccion del cambio.
    if not esperado:
        assert fuera == {}
    elif escala > 1:
        assert set(fuera.values()) == {"REGRESION"}
    elif escala < 1:
        assert set(fuera.values()) == {"MEJORA"}
    else:
        assert set(fuera) == set(esperado)


def test_sin_linea_base_solo_se_mide_y_una_metrica_que_desaparece_falta(tmp_path):
    actual = rendimiento.resumir(
        [rendimiento.metricas(rendimiento.leer_repeticion(_repeticion(tmp_path)))]
    )

    assert {m["estado"] for m in rendimiento.comparar(actual, None).values()} == {"SIN_BASE"}
    base = {"metricas": {"capa.que.ya.no.existe": {"valor": 1.0, "clase": "tiempo"}}}
    assert rendimiento.comparar(actual, base)["capa.que.ya.no.existe"]["estado"] == "FALTA"


def test_falta_un_reporte_y_la_repeticion_se_niega(tmp_path):
    _repeticion(tmp_path)
    (tmp_path / "lifecycle" / "benchmark_lifecycle.json").unlink()

    with pytest.raises(FileNotFoundError, match="benchmark_lifecycle.json"):
        rendimiento.leer_repeticion(tmp_path)


def test_el_markdown_dice_la_maquina_y_cada_metrica(tmp_path):
    reportes = rendimiento.leer_repeticion(_repeticion(tmp_path))
    reportes["entorno"] = {"procesador": "AMD EPYC 7763", "cpus": 4, "commit": "a" * 40}
    resumen = rendimiento.resumir([rendimiento.metricas(reportes)])
    resultado = {
        "entrada": rendimiento.entrada(reportes),
        "entornos": [reportes["entorno"]],
        "repeticiones": 1,
        "linea_base": None,
        "metricas": rendimiento.comparar(resumen, None),
        "regresiones": [],
    }

    texto = rendimiento.markdown(resultado)

    assert "perfil M (100,000 cuentas, 3 cortes)" in texto
    assert "AMD EPYC 7763 (4 CPU)" in texto
    assert "`aaaaaaaaaaaa`" in texto
    assert "| `ingesta.cartera_v2` | 40.000 s |" in texto
    assert "Sin linea base" in texto
