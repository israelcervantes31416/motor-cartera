"""Benchmark de optimization/v1: la exactitud de la reduccion (S: el modelo completo contra el
reducido sobre 10,000 cuentas, y la enumeracion independiente en instancias pequenas), una
formulacion rica (M: el corte del escenario predictivo, ~114 mil cuentas, DIGITAL y TELEFONICA), la
escala (XL: el corte de 500,000 cuentas con CAMPO, sobre su cadena compatible decision/v2 ->
campo/v2 -> ruteo/v2), la sensibilidad de la capacidad (-20 %, nominal y +20 % sobre M), los
tamanos, la API y el EXPLAIN (ANALYZE, BUFFERS) de cada sentencia. No corre en el CI: se pide a
mano, sobre una base *_bench que ya tiene el scoring XL y el del escenario M de v0.15:

    python scripts/benchmark_optimizacion.py --destino <dir>
    python scripts/benchmark_optimizacion.py --destino <dir> --reanudar

La configuracion del benchmark, optimization-benchmark/public-v1 (`configuracion_benchmark`), se
fijo antes de medir y no se ajusta despues de ver un numero: son SUPUESTOS SINTETICOS, que existen
unicamente para medir el optimizador; no son capacidades, costos ni metas de negocio. Las etapas:

1. sube la base a la cabeza de las migraciones (la 0017), anota su configuracion e inventaria las
   entradas: el scoring y la decision/v2 de cada corte, el plan de campo/v2 y el ruteo/v2 del XL;
2. si el corte M no tiene una decision/v2 compatible (de su corte, as_of igual a su fecha), la pide
   en BACKTEST con el known_at de su scoring: la misma frontera de conocimiento;
3. registra y publica cada configuracion (S, M, M -20 %, M +20 % y XL) y guarda sus documentos,
   antes de optimizar nada;
4. S: el universo de M en la base, sus primeras 10,000 cuentas en orden canonico, resuelto con y sin
   la reduccion exacta: los dos optimos tienen que ser identicos; y 200 instancias pequenas al azar
   contra la enumeracion de las pruebas;
5. M y XL: cada plan en primer plano y con su trabajo, como `optimizar-cartera`, con sus fases, la
   memoria pico de su proceso, el WAL y el crecimiento de la base;
6. la sensibilidad: M con -20 % y +20 % de capacidad, comparados con el nominal;
7. VACUUM ANALYZE y los tamanos de las tablas de optimization/v1;
8. la API sobre el plan XL: la mediana, el p95 y el maximo de cada consulta, y el EXPLAIN (ANALYZE,
   BUFFERS) de cada sentencia que emitio; y el del universo y los pares del XL.

Cada etapa corre en su propio proceso, para que su memoria pico sea solo suya, y se anota en una
bitacora (benchmark_optimizacion.etapas.json) en cuanto termina: con --reanudar no se repite
ninguna. MC_DATABASE_URL tiene que apuntar a una base *_bench. Ninguna salida lleva un dato
personal: solo conteos, tiempos, objetivos agregados, identificadores opacos y planes.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import random
import statistics
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from benchmark_escala import memoria_pico_mb  # noqa: E402
from benchmark_lifecycle import _exigir_bench, _migrar, _wal, _wal_desde  # noqa: E402
from benchmark_motor_pagos import Bitacora, _sql, _vacuum  # noqa: E402

VERSION_BENCHMARK = "optimization-benchmark/public-v1"
SUPUESTOS = (
    "SUPUESTOS SINTETICOS (optimization-benchmark/public-v1): estos limites existen unicamente "
    "para medir el optimizador; no son capacidades, costos ni metas de negocio."
)
CUENTAS_S = 10_000
INSTANCIAS_PEQUENAS = 200
REPETICIONES = 20
TABLAS_DE_OPTIMIZACION = (
    "version_configuracion_optimizacion",
    "admisibilidad_canal_optimizacion",
    "pool_capacidad_optimizacion",
    "restriccion_optimizacion",
    "ejecucion_optimizacion",
    "resultado_optimizacion_cuenta",
    "uso_pool_optimizacion",
    "resultado_restriccion_optimizacion",
)
TABLAS_GRANDES = (
    "resultado_optimizacion_cuenta",
    "prediccion_cuenta",
    "resultado_decision_v2",
    "snapshot_cuenta",
    "cuenta_canonica",
    "parada_ruta_v2",
    "asignacion_territorial_v2",
)
PROHIBIDAS = (
    '"cliente_unico"',
    '"actor_ref"',
    '"latitud"',
    '"longitud"',
    '"nombre_cliente"',
    '"telefono"',
    '"direccion"',
)
CONFIGURACION = (
    "work_mem",
    "hash_mem_multiplier",
    "temp_buffers",
    "shared_buffers",
    "maintenance_work_mem",
    "effective_cache_size",
    "max_parallel_workers_per_gather",
    "jit",
)


# --- optimization-benchmark/public-v1 -------------------------------------------------------------


def configuracion_benchmark(
    n: int,
    *,
    nombre: str,
    imputacion: bool,
    gestores: tuple[str, ...] = (),
    factor: tuple[int, int] = (1, 1),
) -> dict:
    """optimization-benchmark/public-v1, fijada antes de medir: una funcion del tamano del
    universo del corte (n) y de los gestores con paradas PROGRAMADA del ruteo declarado. `factor`
    escala las capacidades de DIGITAL y TELEFONICA (la sensibilidad: 8/10 y 12/10), con aritmetica
    entera; nada mas cambia.

    - admisibilidad: DIGITAL -> DIGITAL; TELEFONICA -> TELEFONICA y DIGITAL; CAMPO -> CAMPO y
      TELEFONICA; OTRO, ninguno;
    - DIGITAL_GLOBAL: el 5 % de n, 1 unidad; TELEFONICA_GLOBAL: el 3 % de n, 4 unidades; un pool por
      gestor con paradas PROGRAMADA, capacidad 8 (la efectiva: la menor con sus PROGRAMADA), 20
      unidades;
    - a lo mas el 10 % de n cuentas y el 70 % de n unidades;
    - CONSUMO a lo mas el 45 % de las atendidas; al menos el 0.6 % de n AUTOMOTRIZ y el 2 % de n
      MUY_ALTA; el tramo 1-30 al menos el 15 % de las atendidas.

    El flag de imputacion se declara por escenario: el scoring XL es todo CON_IMPUTACION (su corte
    viene de otro generador, sin historia), y sin admitirla no habria nada que optimizar."""
    num, den = factor
    pools = [
        {
            "codigo": "DIGITAL_GLOBAL",
            "canal": "DIGITAL",
            "tipo_alcance": "GLOBAL",
            "referencia_alcance": None,
            "capacidad": n * 5 // 100 * num // den,
            "unidades_recurso_por_asignacion": 1,
            "activo": True,
        },
        {
            "codigo": "TELEFONICA_GLOBAL",
            "canal": "TELEFONICA",
            "tipo_alcance": "GLOBAL",
            "referencia_alcance": None,
            "capacidad": n * 3 // 100 * num // den,
            "unidades_recurso_por_asignacion": 4,
            "activo": True,
        },
    ]
    pools += [
        {
            "codigo": f"CAMPO_{gestor}",
            "canal": "CAMPO",
            "tipo_alcance": "GESTOR_CAMPO",
            "referencia_alcance": gestor,
            "capacidad": 8,
            "unidades_recurso_por_asignacion": 20,
            "activo": True,
        }
        for gestor in gestores
    ]

    def restriccion(codigo: str, tipo: str, dimension: str, valor: str, limite: int) -> dict:
        return {
            "codigo": codigo,
            "tipo": tipo,
            "dimension": dimension,
            "valor_dimension": valor,
            "limite": limite,
            "activa": True,
        }

    return {
        "nombre": nombre,
        "permitir_predicciones_con_imputacion": imputacion,
        "max_cuentas_atendidas": n * 10 // 100,
        "max_unidades_recurso": n * 70 // 100,
        "admisibilidad": [
            {"canal_decision": d, "canal_operativo": o, "permitido": True}
            for d, o in (
                ("DIGITAL", "DIGITAL"),
                ("TELEFONICA", "TELEFONICA"),
                ("TELEFONICA", "DIGITAL"),
                ("CAMPO", "CAMPO"),
                ("CAMPO", "TELEFONICA"),
            )
        ],
        "pools": pools,
        "restricciones": [
            restriccion("MAX_CONSUMO", "MAX_PARTICIPACION_BPS", "PRODUCTO", "CONSUMO", 4_500),
            restriccion("MIN_AUTOMOTRIZ", "MIN_CUENTAS", "PRODUCTO", "AUTOMOTRIZ", n * 6 // 1000),
            restriccion(
                "MIN_MUY_ALTA", "MIN_CUENTAS", "PRIORIDAD_DECISION", "MUY_ALTA", n * 2 // 100
            ),
            restriccion("MIN_TRAMO_1_30", "MIN_PARTICIPACION_BPS", "ATRASO_BUCKET", "1-30", 1_500),
        ],
        "supuestos": SUPUESTOS,
    }


# --- las entradas ---------------------------------------------------------------------------------


def _entradas(cartera: str, fecha: str) -> dict:
    """El scoring EXITOSA del corte y su modelo, y el corte."""
    from motor_cartera.config import config

    filas = _sql(
        "SELECT k.id, CAST(k.corte_id AS text), k.cuentas, s.id, CAST(s.scoring_run_id AS text), "
        "s.known_at FROM corte_canonico k JOIN ejecucion_scoring_predictivo s "
        "ON s.corte_canonico_id = k.id AND s.estado = 'EXITOSA' WHERE k.despacho_id = :d "
        "AND k.cartera_id = :c AND k.fecha_corte = :f ORDER BY s.id",
        {"d": config.despacho_id, "c": cartera, "f": date.fromisoformat(fecha)},
    )
    if len(filas) != 1:
        sys.exit(f"El corte del {fecha} de {cartera} tiene {len(filas)} scorings EXITOSA, no uno.")
    corte, corte_id, cuentas, scoring, scoring_run_id, known_at = filas[0]
    return {
        "corte": corte,
        "corte_id": corte_id,
        "cuentas": cuentas,
        "scoring": scoring,
        "scoring_run_id": scoring_run_id,
        "known_at": known_at.isoformat(),
    }


def _decision_de(corte: int, fecha: str) -> str | None:
    """La decision/v2 EXITOSA del corte con as_of igual a su fecha, si hay exactamente una."""
    filas = _sql(
        "SELECT CAST(decision_run_id AS text) FROM ejecucion_decision_v2 WHERE "
        "corte_canonico_id = :k AND estado = 'EXITOSA' AND as_of = :f ORDER BY id",
        {"k": corte, "f": date.fromisoformat(fecha)},
    )
    if len(filas) > 1:
        sys.exit(f"El corte {corte} tiene {len(filas)} decisiones v2 EXITOSA: declara una.")
    return filas[0][0] if filas else None


def _cadena_xl(decision_run_id: str, ruteo_run_id: str | None) -> dict:
    """El ruteo/v2 EXITOSA cuyo plan de campo/v2 es de esa decision/v2: el declarado, o el unico
    con paradas PROGRAMADA; su fecha y sus gestores con paradas PROGRAMADA."""
    filas = _sql(
        "SELECT r.id, CAST(r.ruteo_run_id AS text), r.fecha_operacion, "
        "(SELECT count(*) FROM parada_ruta_v2 x WHERE x.ejecucion_ruteo_v2_id = r.id "
        "AND x.estado = 'PROGRAMADA') FROM ejecucion_ruteo_v2 r JOIN ejecucion_campo_v2 c "
        "ON c.id = r.ejecucion_campo_v2_id JOIN ejecucion_decision_v2 d "
        "ON d.id = c.ejecucion_decision_v2_id WHERE r.estado = 'EXITOSA' "
        "AND c.estado = 'EXITOSA' AND CAST(d.decision_run_id AS text) = :d ORDER BY r.id",
        {"d": decision_run_id},
    )
    if ruteo_run_id is not None:
        filas = [f for f in filas if f[1] == ruteo_run_id]
    if len(filas) != 1:
        sys.exit(
            f"La decision/v2 {decision_run_id} tiene {len(filas)} ruteos/v2 compatibles: declara "
            "uno con --ruteo-xl. No se inventa una cadena."
        )
    ruteo, ruteo_run_id, fecha, programadas = filas[0]
    gestores = tuple(
        g
        for (g,) in _sql(
            "SELECT DISTINCT g.codigo FROM parada_ruta_v2 x JOIN gestor_campo g "
            "ON g.id = x.gestor_campo_id WHERE x.ejecucion_ruteo_v2_id = :r "
            "AND x.estado = 'PROGRAMADA' ORDER BY 1",
            {"r": ruteo},
        )
    )
    paradas = dict(
        _sql(
            "SELECT estado, count(*) FROM parada_ruta_v2 WHERE ejecucion_ruteo_v2_id = :r "
            "GROUP BY 1",
            {"r": ruteo},
        )
    )
    return {
        "ruteo": ruteo,
        "ruteo_run_id": ruteo_run_id,
        "fecha_operacion": fecha.isoformat(),
        "programadas": programadas,
        "paradas": paradas,
        "gestores": gestores,
    }


# --- las etapas, cada una en su proceso -----------------------------------------------------------


def decidir(corte_id: str, known_at: str) -> dict:
    """La decision/v2 BACKTEST del corte M con el known_at de su scoring, en primer plano."""
    from datetime import datetime
    from uuid import UUID

    from motor_cartera.db.modelos import ModoDecisionV2
    from motor_cartera.orquestacion.worker import decidir_v2_en_primer_plano

    inicio = time.perf_counter()
    e = decidir_v2_en_primer_plano(
        UUID(corte_id), known_at=datetime.fromisoformat(known_at), modo=ModoDecisionV2.BACKTEST
    )
    if e.estado != "EXITOSA":
        sys.exit(f"La decision/v2 termino {e.estado}: {e.detalle}")
    return {
        "decision_run_id": str(e.decision_run_id),
        "known_at": e.known_at.isoformat(),
        "cuentas": e.cuentas_decididas,
        "segundos": round(time.perf_counter() - inicio, 3),
    }


def registrar(documentos: dict[str, dict]) -> dict:
    """Cada configuracion, registrada y PUBLICADA: su configuracion_id y su hash."""
    from motor_cartera.db.sesion import sesion
    from motor_cartera.optimizacion import configuracion as configuracion_

    salida = {}
    with sesion() as s:
        for clave, documento in documentos.items():
            registrada = configuracion_.registrar(s, configuracion_.leer(documento), publicar=True)
            salida[clave] = {
                "configuracion_id": str(registrada.version.configuracion_id),
                "hash": registrada.version.hash_contenido,
                "pools": registrada.version.pools,
                "restricciones": registrada.version.restricciones,
            }
        s.commit()
    return salida


def exactitud(scoring: int, decision_run_id: str, corte: int, configuracion_id: str) -> dict:
    """S: las primeras 10,000 cuentas del universo M (en orden canonico), con la configuracion S,
    resueltas con y sin la reduccion exacta. Y la enumeracion de las pruebas en instancias
    pequenas al azar."""
    from uuid import UUID

    from sqlmodel import select

    from motor_cartera.db.modelos import (
        EjecucionDecisionV2,
        PoolCapacidadOptimizacion,
        VersionConfiguracionOptimizacion,
    )
    from motor_cartera.db.sesion import sesion
    from motor_cartera.optimizacion import configuracion as configuracion_
    from motor_cartera.optimizacion import formulacion, sql
    from motor_cartera.optimizacion.politica import Dimension, TipoRestriccion

    sys.path.insert(0, str(RAIZ / "tests"))
    from test_optimizacion_formulacion import fuerza_bruta, objetivos, problema_aleatorio

    inicio = time.perf_counter()
    with sesion() as s:
        decision = s.exec(
            select(EjecucionDecisionV2).where(
                EjecucionDecisionV2.decision_run_id == UUID(decision_run_id)
            )
        ).one()
        version = s.exec(
            select(VersionConfiguracionOptimizacion).where(
                VersionConfiguracionOptimizacion.configuracion_id == UUID(configuracion_id)
            )
        ).one()
        contenido = configuracion_.configuracion_de(s, version)
        pools = sorted(
            s.exec(
                select(PoolCapacidadOptimizacion).where(
                    PoolCapacidadOptimizacion.version_configuracion_optimizacion_id == version.id
                )
            ).all(),
            key=lambda p: p.codigo,
        )
        entradas = sql.Entradas(
            scoring=scoring,
            decision=decision.id,
            corte=corte,
            configuracion=version.id,
            ruteo=None,
            territorial=None,
            configuracion_campo=None,
            bases=None,
            permitir_imputacion=version.permitir_predicciones_con_imputacion,
        )
        sql.preparar(
            s,
            entradas,
            [
                sql.PoolActivo(j, p.id, p.canal, p.tipo_alcance, p.referencia_alcance)
                for j, p in enumerate(pools)
            ],
        )
        u = sql.universo(s)
        par_cuenta, par_pool = sql.pares(s)
        # Lo que se usa despues, leido antes de deshacer: el rollback expira los objetos.
        pools = [
            (
                p.codigo,
                p.canal,
                p.tipo_alcance,
                p.referencia_alcance,
                p.capacidad,
                p.unidades_recurso_por_asignacion,
            )
            for p in pools
        ]
        s.rollback()
    universo_segundos = time.perf_counter() - inicio
    n = min(CUENTAS_S, len(u))
    dentro = par_cuenta < n
    coberturas = tuple(
        formulacion.Cobertura(
            r.codigo, TipoRestriccion(r.tipo), Dimension(r.dimension), r.valor_dimension, r.limite
        )
        for r in contenido.restricciones
        if r.activa
    )
    dimensiones = {
        Dimension.PRODUCTO: u.producto[:n],
        Dimension.ATRASO_BUCKET: u.atraso[:n],
        Dimension.PRIORIDAD_DECISION: u.prioridad[:n],
        Dimension.BASE: u.base_codigo[:n],
    }
    problema = formulacion.Problema(
        valor=u.valor[:n],
        contacto=u.contacto[:n],
        par_cuenta=par_cuenta[dentro],
        par_pool=par_pool[dentro],
        pools=tuple(
            formulacion.Pool(codigo, canal, alcance, referencia, capacidad, capacidad, unidades)
            for codigo, canal, alcance, referencia, capacidad, unidades in pools
        ),
        coberturas=coberturas,
        miembros=formulacion.pertenencias(dimensiones, coberturas, n),
        max_cuentas=contenido.max_cuentas_atendidas,
        max_unidades=contenido.max_unidades_recurso,
    )
    resultados = {}
    for nombre, reducir in (("reducido", True), ("completo", False)):
        inicio = time.perf_counter()
        r = formulacion.resolver(problema, reducir_=reducir)
        segundos = time.perf_counter() - inicio
        resultados[nombre] = {
            "estado": r.estado.value,
            "resultado": r.resultado,
            "objetivos": None if r.objetivos is None else list(objetivos(r)),
            "segundos": round(segundos, 3),
            "retenidas": int(r.reduccion.retenida.sum()),
            "clases": r.reduccion.clases,
            "variables": r.modelo.variables,
            "restricciones": r.modelo.restricciones,
            "no_nulos": r.modelo.no_nulos,
            "niveles": [n_.definicion() for n_ in r.niveles],
            "factible": bool(formulacion.evaluar(problema, r.asignacion).factible)
            if r.optima
            else None,
        }
    acuerdos = infactibles = 0
    rng = random.Random(16)
    for _ in range(INSTANCIAS_PEQUENAS):
        p = problema_aleatorio(rng, cuentas=rng.randint(3, 8), empates=rng.random() < 0.5)
        r = formulacion.resolver(p)
        esperado = fuerza_bruta(p)
        if esperado is None:
            infactibles += r.resultado == formulacion.INFACTIBLE
            continue
        acuerdos += r.optima and objetivos(r) == esperado[0]
    return {
        "cuentas": n,
        "universo_m": len(u),
        "pares": int(dentro.sum()),
        "universo_segundos": round(universo_segundos, 3),
        "elegibles": int(problema.elegibles.sum()),
        "reducido": resultados["reducido"],
        "completo": resultados["completo"],
        "mismo_optimo": resultados["reducido"]["objetivos"] == resultados["completo"]["objetivos"],
        "fuerza_bruta": {
            "instancias": INSTANCIAS_PEQUENAS,
            "coinciden": acuerdos,
            "infactibles_en_las_dos": infactibles,
        },
        "memoria_pico_mb": memoria_pico_mb(),
    }


def optimizar(
    scoring_run_id: str,
    decision_run_id: str,
    configuracion_id: str,
    fecha: str,
    ruteo_run_id: str | None,
) -> dict:
    """Un plan en primer plano con su trabajo, como `optimizar-cartera`."""
    from uuid import UUID

    from motor_cartera.db.modelos import EjecucionOptimizacion
    from motor_cartera.db.sesion import sesion
    from motor_cartera.optimizacion import consultas
    from motor_cartera.orquestacion.worker import optimizar_en_primer_plano

    antes, base = _wal(), _base_bytes()
    iniciada_en = datetime.now(UTC).isoformat()
    inicio = time.perf_counter()
    e = optimizar_en_primer_plano(
        scoring_run_id=UUID(scoring_run_id),
        decision_run_id=UUID(decision_run_id),
        configuracion_id=UUID(configuracion_id),
        fecha_operacion=date.fromisoformat(fecha),
        ruteo_run_id=None if ruteo_run_id is None else UUID(ruteo_run_id),
    )
    segundos = time.perf_counter() - inicio
    wal, crecimiento = _wal_desde(antes), _base_bytes() - base
    with sesion() as s:
        e = s.get_one(EjecucionOptimizacion, e.id)
        if e.estado != "EXITOSA":
            sys.exit(f"La optimizacion termino {e.estado} ({e.resultado}): {e.detalle}")
        _, usos = consultas.capacidad(s, e, canal=None, desplazamiento=0, limite=1_000_000)
        _, activas = consultas.restricciones(
            s, e, binding=True, tipo=None, desplazamiento=0, limite=1_000_000
        )
        _, todas = consultas.restricciones(
            s, e, binding=None, tipo=None, desplazamiento=0, limite=1_000_000
        )
        una = s.execute(
            _texto(
                "SELECT CAST(c.cuenta_id AS text) FROM resultado_optimizacion_cuenta r "
                "JOIN cuenta_canonica c ON c.id = r.cuenta_canonica_id "
                "WHERE r.ejecucion_optimizacion_id = :e AND r.estado = 'ASIGNADA' "
                "ORDER BY r.cuenta_canonica_id LIMIT 1"
            ),
            {"e": e.id},
        ).scalar_one()
        una_base = s.execute(
            _texto(
                "SELECT b.codigo FROM resultado_optimizacion_cuenta r JOIN base_operativa b "
                "ON b.id = r.base_operativa_id WHERE r.ejecucion_optimizacion_id = :e "
                "ORDER BY r.cuenta_canonica_id LIMIT 1"
            ),
            {"e": e.id},
        ).scalar_one_or_none()
    campo = [u for u in usos if u.canal == "CAMPO"]
    return {
        "optimizacion_run_id": str(e.optimizacion_run_id),
        "iniciada_en": iniciada_en,
        "firma_inputs": e.firma_inputs,
        "solver_fingerprint": e.solver_fingerprint,
        "huella_solver": e.huella_solver,
        "hash_configuracion": e.hash_configuracion,
        "hash_politica": e.hash_politica,
        "hash_plan": e.hash_plan,
        "cuentas": {
            "universo": e.cuentas_universo,
            "elegibles": e.cuentas_elegibles,
            "no_elegibles": e.cuentas_universo - e.cuentas_elegibles,
            "con_imputacion": e.cuentas_con_imputacion,
            "retenidas": e.cuentas_retenidas,
            "asignadas": e.cuentas_asignadas,
            "publicadas": e.cuentas_publicadas,
        },
        "modelo": {
            "clases": e.clases,
            "variables": e.variables,
            "restricciones": e.restricciones_solver,
            "no_nulos": e.elementos_no_nulos,
        },
        "objetivos": {
            "valor_centavos": e.objetivo_primario_centavos,
            "contacto_1e8": e.objetivo_secundario,
            "unidades": e.unidades_recurso,
            "desempate": e.desempate_canonico,
        },
        "solver_status": e.solver_status,
        "best_bound": str(e.best_bound),
        "mip_gap": format(e.mip_gap, "f"),
        "certificado": e.certificado,
        "resumen": e.resumen,
        "tiempos": e.tiempos,
        "usos_no_campo": [
            {
                "pool": u.pool,
                "capacidad": u.capacidad_efectiva,
                "utilizada": u.utilizada,
                "utilizacion": str(u.utilizacion),
                "unidades": u.unidades_consumidas,
            }
            for u in usos
            if u.canal != "CAMPO"
        ],
        "campo": {
            "pools": len(campo),
            "capacidad_declarada": sum(u.capacidad_declarada or 0 for u in campo),
            "capacidad_efectiva": sum(u.capacidad_efectiva for u in campo),
            "utilizada": sum(u.utilizada for u in campo),
            "pools_llenos": sum(1 for u in campo if u.holgura == 0),
            "unidades": sum(u.unidades_consumidas for u in campo),
        },
        "restricciones": {
            "total": len(todas),
            "binding": len(activas),
            "no_binding": len(todas) - len(activas),
            "holgura_minima": min((r.holgura for r in todas), default=None),
            "binding_declaradas": sorted(
                r.codigo for r in activas if not r.codigo.startswith("POOL:")
            ),
        },
        "segundos": round(segundos, 3),
        "cuentas_por_segundo": round(e.cuentas_universo / segundos),
        "memoria_pico_mb": memoria_pico_mb(),
        "wal_mib": wal,
        "crecimiento_bytes": crecimiento,
        "una_cuenta": una,
        "una_base": una_base,
    }


def comparar(a: str, b: str) -> dict:
    from uuid import UUID

    from motor_cartera.db.sesion import sesion
    from motor_cartera.optimizacion import consultas

    with sesion() as s:
        c = consultas.comparar(
            s,
            consultas.obtener_ejecucion(s, UUID(a)),
            consultas.obtener_ejecucion(s, UUID(b)),
        )
    return {
        "entradas_distintas": c.entradas_distintas,
        "objetivos": c.objetivos,
        "misma_asignacion": c.misma_asignacion,
        "otro_pool_mismo_canal": c.otro_pool_mismo_canal,
        "otro_canal": c.otro_canal,
        "asignada_en_ambos": c.asignada_en_ambos,
        "asignada_solo_en_a": c.asignada_solo_en_a,
        "asignada_solo_en_b": c.asignada_solo_en_b,
        "jaccard_asignadas": c.jaccard_asignadas,
        "canales": c.canales,
    }


def _texto(consulta: str):
    from sqlalchemy import text

    return text(consulta)


def _base_bytes() -> int:
    return _sql("SELECT pg_database_size(current_database())")[0][0]


def _hijo(*argumentos: str) -> dict:
    """Una etapa en su propio proceso (este mismo script), con su ultima linea como JSON."""
    import subprocess

    proceso = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), *argumentos],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    if proceso.returncode != 0:
        print(proceso.stdout[-4000:], proceso.stderr[-4000:], sep="\n", file=sys.stderr)
        sys.exit(f"La etapa {argumentos[1]} fallo con codigo {proceso.returncode}.")
    return json.loads(proceso.stdout.strip().splitlines()[-1])


# --- la API y los planes --------------------------------------------------------------------------


def consultar(destino: Path, run_id: str, cuenta: str, base: str | None) -> dict:
    """Las consultas por la API, con un cliente HTTP en el mismo proceso: el tiempo incluye la
    serializacion. Cada una se repite y se reporta su mediana, su p95 y su maximo; despues se
    guarda el plan de cada sentencia que emitio, en una transaccion que se deshace."""
    from fastapi.testclient import TestClient
    from sqlalchemy import event

    from motor_cartera.api.app import crear_app
    from motor_cartera.config import Config
    from motor_cartera.db.sesion import crear_motor

    clave = "benchmark-optimizacion-" + "x" * 24
    cliente = TestClient(crear_app(Config(api_key=clave)), headers={"X-API-Key": clave})
    run = f"/optimizacion/ejecuciones/{run_id}"
    resultados = f"{run}/resultados?por_pagina=100"
    rutas = {
        "ejecucion": run,
        "pagina_de_resultados": resultados,
        "pagina_100_de_resultados": f"{resultados}&pagina=100",
        "asignadas": f"{resultados}&estado=ASIGNADA",
        "canal_telefonica": f"{resultados}&canal=TELEFONICA",
        "pool_digital": f"{resultados}&pool=DIGITAL_GLOBAL",
        "producto": f"{resultados}&producto=AUTOMOTRIZ",
        "prioridad": f"{resultados}&prioridad=MUY_ALTA",
        "producto_y_prioridad": f"{resultados}&producto=AUTOMOTRIZ&prioridad=MUY_ALTA",
        **({"base": f"{resultados}&base={base}"} if base else {}),
        "capacidad": f"{run}/capacidad?por_pagina=100",
        "restricciones": f"{run}/restricciones?por_pagina=100",
        "restricciones_activas": f"{run}/restricciones?binding=true&por_pagina=100",
        "diagnostico": f"{run}/diagnostico",
        "explicacion_de_una_cuenta": f"{run}/cuentas/{cuenta}",
        "planes_de_una_cuenta": f"/cuentas/{cuenta}/optimizacion",
    }

    def medir(ruta: str) -> dict:
        tiempos = []
        for _ in range(REPETICIONES):
            inicio = time.perf_counter()
            respuesta = cliente.get(ruta)
            tiempos.append((time.perf_counter() - inicio) * 1000)
            if respuesta.status_code != 200:
                sys.exit(f"{ruta}: {respuesta.status_code} {respuesta.text[:300]}")
            for llave in PROHIBIDAS:
                if llave in respuesta.text:
                    sys.exit(f"{ruta}: la respuesta trae {llave}")
        tiempos.sort()
        return {
            "repeticiones": REPETICIONES,
            "mediana_ms": round(statistics.median(tiempos), 2),
            "p95_ms": round(tiempos[max(int(len(tiempos) * 0.95) - 1, 0)], 2),
            "maximo_ms": round(tiempos[-1], 2),
        }

    tiempos = {nombre: medir(ruta) for nombre, ruta in rutas.items()}
    motor = crear_motor()
    planes: dict[str, list] = {}
    for nombre, ruta in rutas.items():
        emitidas: list[tuple[str, object]] = []

        def anotar(conexion, cursor, sentencia, parametros, contexto, varias, emitidas=emitidas):
            emitidas.append((sentencia, parametros))

        event.listen(motor, "before_cursor_execute", anotar)
        try:
            cliente.get(ruta)
        finally:
            event.remove(motor, "before_cursor_execute", anotar)
        planes[nombre] = _explicar(motor, emitidas)
    _escribir_planes(destino / "planes_optimizacion_api.txt", planes)
    return {
        "tiempos": tiempos,
        **_resumen_de_planes(planes),
    }


def _explicar(motor, emitidas) -> list[dict]:
    salida = []
    with motor.connect() as conexion:
        crudo = conexion.connection.driver_connection
        with crudo.cursor() as cursor:
            for sentencia, parametros in emitidas:
                if sentencia.lstrip().upper().startswith(("SELECT", "WITH")):
                    cursor.execute("EXPLAIN (ANALYZE, BUFFERS) " + sentencia, parametros)
                    texto = "\n".join(fila[0] for fila in cursor.fetchall())
                    salida.append({"sql": " ".join(sentencia.split()), "plan": texto})
        conexion.rollback()
    return salida


def _escribir_planes(ruta: Path, planes: dict[str, list]) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with ruta.open("w", encoding="utf-8") as salida:
        for nombre, sentencias in planes.items():
            for i, sentencia in enumerate(sentencias, start=1):
                salida.write(f"### {nombre} ({i} de {len(sentencias)})\n{sentencia['sql']}\n\n")
                salida.write(sentencia["plan"] + "\n\n")


def _resumen_de_planes(planes: dict[str, list]) -> dict:
    return {
        "sentencias": {nombre: len(p) for nombre, p in planes.items()},
        "milisegundos_en_postgresql": {
            nombre: round(sum(_milisegundos(p["plan"]) for p in planes[nombre]), 3)
            for nombre in planes
        },
        "seq_scan_sobre_tablas_grandes": {
            nombre: [p["sql"][:160] for p in planes[nombre] if _escanea_grande(p["plan"])]
            for nombre in planes
            if any(_escanea_grande(p["plan"]) for p in planes[nombre])
        },
        "indices_usados": {
            nombre: sorted(
                {
                    linea.split(" using ")[1].split()[0]
                    for p in planes[nombre]
                    for linea in p["plan"].splitlines()
                    if " using " in linea
                }
            )
            for nombre in planes
        },
    }


def explicar_universo(destino: Path, xl: dict, configuracion_id: str) -> dict:
    """El EXPLAIN (ANALYZE, BUFFERS) del universo XL (el JOIN del scoring con la decision/v2, el
    snapshot, la parada del ruteo y la base territorial) y de los pares, en una transaccion que se
    deshace, con las mismas sentencias que una ejecucion."""
    from uuid import UUID

    from sqlalchemy import text
    from sqlmodel import select

    from motor_cartera.db.copia import copiar
    from motor_cartera.db.modelos import (
        EjecucionOptimizacion,
        PoolCapacidadOptimizacion,
        VersionConfiguracionOptimizacion,
    )
    from motor_cartera.db.sesion import sesion
    from motor_cartera.optimizacion import sql

    planes: dict[str, list] = {}
    with sesion() as s:
        e = s.exec(
            select(EjecucionOptimizacion).where(
                EjecucionOptimizacion.optimizacion_run_id == UUID(xl["optimizacion_run_id"])
            )
        ).one()
        version = s.exec(
            select(VersionConfiguracionOptimizacion).where(
                VersionConfiguracionOptimizacion.configuracion_id == UUID(configuracion_id)
            )
        ).one()
        pools = sorted(
            s.exec(
                select(PoolCapacidadOptimizacion).where(
                    PoolCapacidadOptimizacion.version_configuracion_optimizacion_id == version.id,
                    PoolCapacidadOptimizacion.activo,
                )
            ).all(),
            key=lambda p: p.codigo,
        )
        parametros = {
            "scoring": e.ejecucion_scoring_predictivo_id,
            "decision": e.ejecucion_decision_v2_id,
            "corte": e.corte_canonico_id,
            "ruteo": e.ejecucion_ruteo_v2_id,
            "territorial": e.ejecucion_territorial_v2_id,
        }

        def explicar(nombre: str, sentencia: str, valores: dict) -> None:
            filas = s.execute(text("EXPLAIN (ANALYZE, BUFFERS) " + sentencia), valores).all()
            planes[nombre] = [
                {"sql": " ".join(sentencia.split()), "plan": "\n".join(f[0] for f in filas)}
            ]

        explicar(
            "universo (scoring, decision, snapshot, ruteo, territorial)", sql.UNIVERSO, parametros
        )
        s.execute(text(sql.POOLS))
        copiar(
            s,
            "op_pool",
            ("j", "id", "canal", "tipo_alcance", "referencia_alcance"),
            ((j, p.id, p.canal, p.tipo_alcance, p.referencia_alcance) for j, p in enumerate(pools)),
        )
        s.execute(text("ANALYZE op_universo"))
        s.execute(text("ANALYZE op_pool"))
        explicar(
            "pares (admisibilidad y alcance, por igualdad)",
            sql.PARES,
            {
                "configuracion": version.id,
                "imputacion": version.permitir_predicciones_con_imputacion,
            },
        )
        s.rollback()
    _escribir_planes(destino / "planes_optimizacion_universo.txt", planes)
    return _resumen_de_planes(planes)


def _milisegundos(plan: str) -> float:
    for linea in plan.splitlines():
        if linea.startswith("Execution Time"):
            return float(linea.split(":")[1].split()[0])
    return 0.0


def _escanea_grande(plan: str) -> bool:
    return any(f"Seq Scan on {tabla} " in plan + " " for tabla in TABLAS_GRANDES)


# --- los tamanos ----------------------------------------------------------------------------------


def _tamanos() -> dict:
    (base,) = _sql("SELECT pg_database_size(current_database())")
    tablas = _sql(
        "SELECT c.relname, c.reltuples::bigint, pg_relation_size(c.oid), "
        "pg_indexes_size(c.oid), pg_total_relation_size(c.oid) FROM pg_class c "
        "WHERE c.relkind = 'r' AND c.relname = ANY(:tablas) ORDER BY 5 DESC",
        {"tablas": list(TABLAS_DE_OPTIMIZACION)},
    )
    indices = _sql(
        "SELECT i.relname, t.relname, pg_relation_size(i.oid) FROM pg_index x "
        "JOIN pg_class i ON i.oid = x.indexrelid JOIN pg_class t ON t.oid = x.indrelid "
        "WHERE t.relname = ANY(:tablas) ORDER BY 3 DESC",
        {"tablas": list(TABLAS_DE_OPTIMIZACION)},
    )
    ((resultados,),) = _sql("SELECT count(*) FROM resultado_optimizacion_cuenta")
    total = next((t[4] for t in tablas if t[0] == "resultado_optimizacion_cuenta"), 0)
    return {
        "base_bytes": base[0],
        "resultados": resultados,
        "bytes_por_resultado": round(total / resultados) if resultados else None,
        "tablas": [
            {"tabla": t, "filas": f, "datos": d, "indices": i, "total": tot}
            for t, f, d, i, tot in tablas
        ],
        "indices": [{"indice": i, "tabla": t, "bytes": b} for i, t, b in indices],
    }


# --- el reporte -----------------------------------------------------------------------------------


def _mib(valor: int | float | None) -> str:
    return "?" if valor is None else f"{valor / 2**20:,.1f} MiB"


def _num(valor) -> str:
    if valor is None:
        return "?"
    if isinstance(valor, float):
        return f"{valor:,.2f}"
    try:
        return f"{int(valor):,}"
    except (TypeError, ValueError):
        return str(valor)


def _plan_md(titulo: str, p: dict) -> list[str]:
    c, m, o = p["cuentas"], p["modelo"], p["objetivos"]
    fases = p["tiempos"]["fases_segundos"]
    solver = p["tiempos"].get("solver_segundos", {})
    lineas = [
        f"## {titulo}",
        "",
        f"- optimizacion_run_id `{p['optimizacion_run_id']}`, hash del plan `{p['hash_plan']}`",
        f"- solver_fingerprint `{p['solver_fingerprint']}` (SciPy "
        f"{p['huella_solver']['scipy']}, HiGHS {p['huella_solver']['highs']})",
        f"- cuentas: universo {_num(c['universo'])}, elegibles {_num(c['elegibles'])}, no "
        f"elegibles {_num(c['no_elegibles'])}, con imputacion {_num(c['con_imputacion'])}, "
        f"retenidas {_num(c['retenidas'])} en {_num(m['clases'])} clases, asignadas "
        f"{_num(c['asignadas'])}",
        f"- modelo: {_num(m['variables'])} variables binarias, {_num(m['restricciones'])} "
        f"restricciones, {_num(m['no_nulos'])} no nulos",
        f"- objetivos: valor {_num(o['valor_centavos'])} centavos, contacto "
        f"{_num(o['contacto_1e8'])} (1e-8), {_num(o['unidades'])} unidades, desempate "
        f"{_num(o['desempate'])}",
        f"- {p['solver_status']}, best bound {p['best_bound']}, gap {p['mip_gap']}",
        "",
        "| nivel | sentido | estado | objetivo | cota | gap | metodo | libres | fijadas | nodos "
        "| s |",
        "|---|---|---|---:|---:|---:|---|---:|---:|---:|---:|",
    ]
    for n in p["certificado"]["niveles"]:
        lineas.append(
            f"| {n['nivel']} | {n['sentido']} | {n['estado']} | {_num(n['objetivo'])} | "
            f"{_num(n['mejor_cota'])} | {n['gap_relativo']} | {n['metodo']} | "
            f"{_num(n['variables'])} | {_num(n['fijadas_por_dualidad'])} | {_num(n['nodos'])} | "
            f"{n['segundos']:.2f} |"
        )
    lineas += [
        "",
        "Tiempos (s): "
        + ", ".join(f"{k} {v:.2f}" for k, v in fases.items())
        + "; dentro del solver: "
        + ", ".join(f"{k} {v:.2f}" for k, v in solver.items())
        + f"; total del proceso {p['segundos']:.2f} ({_num(p['cuentas_por_segundo'])} cuentas/s).",
        f"Memoria pico del proceso {_num(p['memoria_pico_mb'])} MB; WAL {_num(p['wal_mib'])} MiB; "
        f"la base crecio {_mib(p['crecimiento_bytes'])}.",
        "",
        "| pool | capacidad | utilizada | utilizacion | unidades |",
        "|---|---:|---:|---:|---:|",
    ]
    for u in p["usos_no_campo"]:
        lineas.append(
            f"| {u['pool']} | {_num(u['capacidad'])} | {_num(u['utilizada'])} | "
            f"{u['utilizacion']} | {_num(u['unidades'])} |"
        )
    if p["campo"]["pools"]:
        k = p["campo"]
        lineas.append(
            f"| CAMPO ({_num(k['pools'])} gestores) | {_num(k['capacidad_efectiva'])} (declarada "
            f"{_num(k['capacidad_declarada'])}) | {_num(k['utilizada'])} | "
            f"{k['utilizada'] / k['capacidad_efectiva'] if k['capacidad_efectiva'] else 0:.6f} | "
            f"{_num(k['unidades'])} |"
        )
    r = p["restricciones"]
    lineas += [
        "",
        f"Restricciones: {_num(r['total'])}, binding {_num(r['binding'])}, no binding "
        f"{_num(r['no_binding'])}, holgura minima {_num(r['holgura_minima'])}; declaradas "
        f"activas: {', '.join(r['binding_declaradas']) or 'ninguna'}.",
        "",
    ]
    return lineas


def _markdown(reporte: dict) -> str:
    lineas = [
        "# Benchmark de optimization/v1",
        "",
        f"{reporte['fecha']}, {reporte['maquina']}, PostgreSQL {reporte['postgresql']}.",
        "",
        SUPUESTOS,
        "",
    ]
    if "s" in reporte:
        s = reporte["s"]
        lineas += [
            "## S: exactitud",
            "",
            f"Las primeras {_num(s['cuentas'])} cuentas del universo M ({_num(s['elegibles'])} "
            f"elegibles, {_num(s['pares'])} pares). Mismo optimo con y sin la reduccion: "
            f"**{'si' if s['mismo_optimo'] else 'NO'}**.",
            "",
            "| modelo | estado | objetivos | retenidas | clases | variables | restricciones | "
            "no nulos | s |",
            "|---|---|---|---:|---:|---:|---:|---:|---:|",
        ]
        for nombre in ("completo", "reducido"):
            x = s[nombre]
            lineas.append(
                f"| {nombre} | {x['estado']} | {x['objetivos']} | {_num(x['retenidas'])} | "
                f"{_num(x['clases'])} | {_num(x['variables'])} | {_num(x['restricciones'])} | "
                f"{_num(x['no_nulos'])} | {x['segundos']:.2f} |"
            )
        b = s["fuerza_bruta"]
        lineas += [
            "",
            f"Enumeracion independiente: {b['coinciden']} de {b['instancias']} instancias con "
            f"optimo coinciden, y {b['infactibles_en_las_dos']} infactibles en las dos.",
            "",
        ]
    for clave, titulo in (("m", "M: restricciones ricas"), ("xl", "XL: escala con CAMPO")):
        if clave in reporte:
            lineas += _plan_md(titulo, reporte[clave])
    if "sensibilidad" in reporte:
        lineas += [
            "## Sensibilidad de la capacidad (M)",
            "",
            "La frontera de oportunidad del modelo: al permitir mas cuentas, el optimo captura "
            "mas valor de oportunidad predicho. No es recuperacion causal ni dinero adicional.",
            "",
            "| escenario | valor (centavos) | asignadas | unidades | digital | telefonica |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for nombre, x in reporte["sensibilidad"]["planes"].items():
            usos = {u["pool"]: u for u in x["usos_no_campo"]}
            lineas.append(
                f"| {nombre} | {_num(x['objetivos']['valor_centavos'])} | "
                f"{_num(x['cuentas']['asignadas'])} | {_num(x['objetivos']['unidades'])} | "
                f"{_num(usos['DIGITAL_GLOBAL']['utilizada'])}/"
                f"{_num(usos['DIGITAL_GLOBAL']['capacidad'])} | "
                f"{_num(usos['TELEFONICA_GLOBAL']['utilizada'])}/"
                f"{_num(usos['TELEFONICA_GLOBAL']['capacidad'])} |"
            )
        lineas.append("")
    if "tamanos" in reporte:
        t = reporte["tamanos"]["despues"]
        lineas += [
            "## Tamanos",
            "",
            f"{_num(t['resultados'])} resultados por cuenta, {_num(t['bytes_por_resultado'])} "
            "bytes por resultado (datos e indices).",
            "",
            "| tabla | filas | datos | indices | total |",
            "|---|---:|---:|---:|---:|",
        ]
        for x in t["tablas"]:
            lineas.append(
                f"| {x['tabla']} | {_num(x['filas'])} | {_mib(x['datos'])} | "
                f"{_mib(x['indices'])} | {_mib(x['total'])} |"
            )
        lineas.append("")
    if "api" in reporte:
        a = reporte["api"]
        lineas += [
            "## API (plan XL)",
            "",
            "| consulta | mediana ms | p95 ms | max ms | sentencias | ms en PostgreSQL |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for nombre, x in a["tiempos"].items():
            lineas.append(
                f"| {nombre} | {x['mediana_ms']} | {x['p95_ms']} | {x['maximo_ms']} | "
                f"{a['sentencias'][nombre]} | {a['milisegundos_en_postgresql'][nombre]} |"
            )
        lineas += [
            "",
            f"Seq Scan sobre tablas grandes: {a['seq_scan_sobre_tablas_grandes'] or 'ninguno'}.",
            "",
        ]
    if "universo" in reporte:
        u = reporte["universo"]
        lineas += [
            "## EXPLAIN del universo y los pares (XL)",
            "",
            *(f"- {k}: {v} ms" for k, v in u["milisegundos_en_postgresql"].items()),
            "",
        ]
    return "\n".join(lineas)


# --- la corrida -----------------------------------------------------------------------------------


def main(argumentos: argparse.Namespace) -> None:
    _exigir_bench()
    destino = Path(argumentos.destino)
    destino.mkdir(parents=True, exist_ok=True)
    bitacora = Bitacora(destino / "benchmark_optimizacion.etapas.json", argumentos.reanudar)
    if "inicio" not in bitacora:
        _migrar()
        ((version,),) = _sql("SHOW server_version")
        m = _entradas(argumentos.m_cartera, argumentos.m_fecha)
        xl = _entradas(argumentos.xl_cartera, argumentos.xl_fecha)
        xl["decision_run_id"] = _decision_de(xl["corte"], argumentos.xl_fecha)
        if xl["decision_run_id"] is None:
            sys.exit("El corte XL no tiene una decision/v2 EXITOSA compatible.")
        xl.update(_cadena_xl(xl["decision_run_id"], argumentos.ruteo_xl))
        bitacora.anotar(
            "inicio",
            {
                "fecha": date.today().isoformat(),
                "maquina": f"{platform.system()} {platform.release()}, {platform.processor()}",
                "postgresql": version,
                "configuracion": {p: _sql(f"SHOW {p}")[0][0] for p in CONFIGURACION},
                "m": m,
                "xl": {k: v for k, v in xl.items() if k != "gestores"},
                "gestores_xl": list(xl["gestores"]),
                "base_bytes": _base_bytes(),
            },
        )
        print("inventario anotado", flush=True)
    inicio = bitacora["inicio"]
    m, xl = inicio["m"], inicio["xl"]
    if "decision_m" not in bitacora:
        existente = _decision_de(m["corte"], argumentos.m_fecha)
        resultado = (
            {"decision_run_id": existente, "segundos": None, "reutilizada": True}
            if existente is not None
            else _hijo(
                "--etapa", "decidir", "--corte-id", m["corte_id"], "--known-at", m["known_at"]
            )
        )
        bitacora.anotar("decision_m", resultado)
        print(f"decision/v2 M: {resultado['decision_run_id']}", flush=True)
    decision_m = bitacora["decision_m"]["decision_run_id"]
    if "configuraciones" not in bitacora:
        documentos = {
            "s": configuracion_benchmark(
                CUENTAS_S, nombre="optimization-benchmark/public-v1 S", imputacion=False
            ),
            "m": configuracion_benchmark(
                m["cuentas"], nombre="optimization-benchmark/public-v1 M", imputacion=False
            ),
            "m_menos_20": configuracion_benchmark(
                m["cuentas"],
                nombre="optimization-benchmark/public-v1 M -20 %",
                imputacion=False,
                factor=(8, 10),
            ),
            "m_mas_20": configuracion_benchmark(
                m["cuentas"],
                nombre="optimization-benchmark/public-v1 M +20 %",
                imputacion=False,
                factor=(12, 10),
            ),
            "xl": configuracion_benchmark(
                xl["cuentas"],
                nombre="optimization-benchmark/public-v1 XL",
                imputacion=True,
                gestores=tuple(inicio["gestores_xl"]),
            ),
        }
        for clave, documento in documentos.items():
            (destino / f"configuracion_{clave}.json").write_text(
                json.dumps(documento, indent=1, ensure_ascii=False), encoding="utf-8"
            )
        archivo = destino / "configuraciones.json"
        archivo.write_text(json.dumps(documentos, ensure_ascii=False), encoding="utf-8")
        registradas = _hijo("--etapa", "registrar", "--documentos", str(archivo))
        # Antes de optimizar nada: la configuracion no se ajusta despues de ver un numero.
        registradas["registradas_en"] = datetime.now(UTC).isoformat()
        bitacora.anotar("configuraciones", registradas)
        print("configuraciones registradas", flush=True)
    configuraciones = bitacora["configuraciones"]
    if "s" not in bitacora:
        resultado = _hijo(
            "--etapa",
            "exactitud",
            "--scoring",
            str(m["scoring"]),
            "--decision",
            decision_m,
            "--corte",
            str(m["corte"]),
            "--configuracion",
            configuraciones["s"]["configuracion_id"],
        )
        bitacora.anotar("s", resultado)
        print(f"S: mismo optimo {resultado['mismo_optimo']}", flush=True)
    fecha_m = date.fromisoformat(argumentos.m_fecha).toordinal() + 1
    fecha_m = date.fromordinal(fecha_m).isoformat()
    for clave, configuracion in (
        ("m", "m"),
        ("m_menos_20", "m_menos_20"),
        ("m_mas_20", "m_mas_20"),
    ):
        if clave not in bitacora:
            resultado = _hijo(
                "--etapa",
                "optimizar",
                "--scoring-run",
                m["scoring_run_id"],
                "--decision",
                decision_m,
                "--configuracion",
                configuraciones[configuracion]["configuracion_id"],
                "--fecha",
                fecha_m,
            )
            bitacora.anotar(clave, resultado)
            print(f"{clave}: {resultado['segundos']} s", flush=True)
    if "xl" not in bitacora:
        resultado = _hijo(
            "--etapa",
            "optimizar",
            "--scoring-run",
            xl["scoring_run_id"],
            "--decision",
            xl["decision_run_id"],
            "--configuracion",
            configuraciones["xl"]["configuracion_id"],
            "--fecha",
            xl["fecha_operacion"],
            "--ruteo",
            xl["ruteo_run_id"],
        )
        bitacora.anotar("xl", resultado)
        print(f"XL: {resultado['segundos']} s", flush=True)
    if "sensibilidad" not in bitacora:
        nominal = bitacora["m"]["optimizacion_run_id"]
        bitacora.anotar(
            "sensibilidad",
            {
                "menos_20_contra_nominal": _hijo(
                    "--etapa",
                    "comparar",
                    "--a",
                    nominal,
                    "--b",
                    bitacora["m_menos_20"]["optimizacion_run_id"],
                ),
                "mas_20_contra_nominal": _hijo(
                    "--etapa",
                    "comparar",
                    "--a",
                    nominal,
                    "--b",
                    bitacora["m_mas_20"]["optimizacion_run_id"],
                ),
            },
        )
        print("sensibilidad comparada", flush=True)
    if "tamanos" not in bitacora:
        segundos = _vacuum(TABLAS_DE_OPTIMIZACION)
        despues = _tamanos()
        bitacora.anotar(
            "tamanos",
            {
                "vacuum_segundos": segundos,
                "despues": despues,
                "crecimiento_bytes": despues["base_bytes"] - inicio["base_bytes"],
            },
        )
        print("tamanos anotados", flush=True)
    if "api" not in bitacora:
        resultado = _hijo(
            "--etapa",
            "consultar",
            "--destino",
            str(destino),
            "--run",
            bitacora["xl"]["optimizacion_run_id"],
            "--cuenta",
            bitacora["xl"]["una_cuenta"],
            *(["--base", bitacora["xl"]["una_base"]] if bitacora["xl"].get("una_base") else []),
        )
        bitacora.anotar("api", resultado)
        print("API medida", flush=True)
    if "universo" not in bitacora:
        archivo = destino / "xl.json"
        archivo.write_text(json.dumps(bitacora["xl"], default=str), encoding="utf-8")
        resultado = _hijo(
            "--etapa",
            "universo",
            "--destino",
            str(destino),
            "--plan",
            str(archivo),
            "--configuracion",
            configuraciones["xl"]["configuracion_id"],
        )
        bitacora.anotar("universo", resultado)
        print("universo explicado", flush=True)
    reporte = {
        "fecha": inicio["fecha"],
        "maquina": inicio["maquina"],
        "postgresql": inicio["postgresql"],
        "configuracion_postgresql": inicio["configuracion"],
        "entradas": {"m": m, "xl": xl, "decision_m": bitacora["decision_m"]},
        "configuraciones": configuraciones,
        **{
            clave: bitacora[clave]
            for clave in ("s", "m", "xl", "tamanos", "api", "universo")
            if clave in bitacora
        },
        "sensibilidad": {
            "planes": {
                "-20 %": bitacora["m_menos_20"],
                "nominal": bitacora["m"],
                "+20 %": bitacora["m_mas_20"],
            },
            **bitacora["sensibilidad"],
        },
    }
    (destino / "benchmark_optimizacion.json").write_text(
        json.dumps(reporte, indent=1, ensure_ascii=False, default=str), encoding="utf-8"
    )
    (destino / "benchmark_optimizacion.md").write_text(_markdown(reporte), encoding="utf-8")
    print(f"Escrito: {destino / 'benchmark_optimizacion.md'}")


if __name__ == "__main__":
    argumentos_ = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    argumentos_.add_argument("--destino", default="datos/benchmark_optimizacion")
    argumentos_.add_argument("--m-cartera", default="PREDICTIVO")
    argumentos_.add_argument("--m-fecha", default="2026-07-08")
    argumentos_.add_argument("--xl-cartera", default="CARTERA_PRINCIPAL")
    argumentos_.add_argument("--xl-fecha", default="2026-04-01")
    argumentos_.add_argument("--ruteo-xl", help="El ruteo_run_id del XL, si hay mas de uno.")
    argumentos_.add_argument("--reanudar", action="store_true")
    argumentos_.add_argument(
        "--etapa",
        choices=(
            "decidir",
            "registrar",
            "exactitud",
            "optimizar",
            "comparar",
            "consultar",
            "universo",
        ),
        help=argparse.SUPPRESS,
    )
    for opcion in (
        "--corte-id",
        "--known-at",
        "--documentos",
        "--scoring",
        "--scoring-run",
        "--decision",
        "--corte",
        "--configuracion",
        "--fecha",
        "--ruteo",
        "--a",
        "--b",
        "--run",
        "--cuenta",
        "--plan",
        "--base",
    ):
        argumentos_.add_argument(opcion, help=argparse.SUPPRESS)
    leidos = argumentos_.parse_args()
    if leidos.etapa == "decidir":
        print(json.dumps(decidir(leidos.corte_id, leidos.known_at), default=str))
    elif leidos.etapa == "registrar":
        documentos = json.loads(Path(leidos.documentos).read_text(encoding="utf-8"))
        print(json.dumps(registrar(documentos), default=str))
    elif leidos.etapa == "exactitud":
        print(
            json.dumps(
                exactitud(
                    int(leidos.scoring), leidos.decision, int(leidos.corte), leidos.configuracion
                ),
                default=str,
            )
        )
    elif leidos.etapa == "optimizar":
        print(
            json.dumps(
                optimizar(
                    leidos.scoring_run,
                    leidos.decision,
                    leidos.configuracion,
                    leidos.fecha,
                    leidos.ruteo,
                ),
                default=str,
            )
        )
    elif leidos.etapa == "comparar":
        print(json.dumps(comparar(leidos.a, leidos.b), default=str))
    elif leidos.etapa == "consultar":
        print(
            json.dumps(
                consultar(Path(leidos.destino), leidos.run, leidos.cuenta, leidos.base),
                default=str,
            )
        )
    elif leidos.etapa == "universo":
        xl_ = json.loads(Path(leidos.plan).read_text(encoding="utf-8"))
        print(
            json.dumps(
                explicar_universo(Path(leidos.destino), xl_, leidos.configuracion), default=str
            )
        )
    else:
        main(leidos)
