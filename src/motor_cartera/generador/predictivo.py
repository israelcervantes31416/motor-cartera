"""escenario-predictivo/v1: un escenario longitudinal sintetico con senal predictiva publica.

El escenario de `generar-escenario` paga una fraccion fija de cuentas al azar cada semana: ningun
dato del pasado dice nada del futuro, y un modelo no tendria nada que aprender. Este escenario es
el de predictive-features/public-v1 y su benchmark (v0.15): sus reglas son publicas, se fijaron
antes de entrenar nada y no se ajustan despues de ver una metrica. Ninguna sale de datos reales.

Cada cliente tiene dos rasgos latentes fijos, derivados de su numero y de la semilla (como su nombre
o su producto): su **voluntad** de pago y su **contactabilidad**, cada uno normal estandar. Ningun
archivo los trae: un modelo solo puede inferirlos de lo que dejan ver, como en una cartera real. Por
cada periodo, de un corte al siguiente, con las cuentas del corte que lo abre:

1. la cobranza gestiona mas a las cuentas de mas atraso (`Reglas.gestiones_por_tramo`), por el canal
   de su tramo, y el contacto depende de la contactabilidad y del canal;
2. con el titular en la linea, la promesa depende de la voluntad;
3. el pago depende de la voluntad, del atraso, del producto, de haber hablado con el titular en el
   periodo, de haber prometido y de estar al corriente; un pago de una promesa llega despues de
   ella. El importe sale de PAGO_NORMAL, como en el escenario de siempre, y una LIQUIDACION paga
   todo;
4. el corte siguiente sale de `oficial.evolucionar`: los pagos bajan el saldo y curan el atraso, las
   liquidadas y las retiradas salen y llegan altas con rasgos propios.

Lo demas es lo del escenario de siempre: duplicados exactos y ajustes negativos en los pagos, y en
el lifecycle cancelaciones, anulaciones y grupos que llegan en el archivo del periodo siguiente.
Todo sale de la semilla, con un generador de numeros propio por periodo: el mismo escenario sale
igual, byte por byte. El manifiesto (escenario.json) declara la cobertura exacta de los pagos y del
lifecycle: el generador sabe hasta donde escribio, y nadie la infiere de la fecha maxima observada.

Este modulo escribe archivos; no sabe de la base. `predictivo.escenario` los carga semana por
semana.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc

from motor_cartera.contratos.cartera import CANALES, PRODUCTOS
from motor_cartera.contratos.cartera_v2 import CONTRATO_V2
from motor_cartera.contratos.pagos import CONTRATO_PAGOS
from motor_cartera.generador import lifecycle as lc
from motor_cartera.generador.oficial import (
    CANAL_POR_TRAMO,
    COMISIONES_PUNTOS_BASE,
    CONCEPTOS,
    PERFILES,
    EstadoCartera,
    Movimientos,
    _Azar,
    _de_lista,
    _digitos,
    _elegir,
    _geografia,
    _instantes,
    _numero,
    _o_nulo,
    _pesos,
    _producto,
    _texto,
    escribir_cartera,
    escribir_pagos,
    estado_inicial,
    evolucionar,
)
from motor_cartera.segmentacion import TRAMOS_ATRASO

VERSION_ESCENARIO = "escenario-predictivo/v1"
SEMILLA = 2026
"""La semilla publica del escenario predictivo y de todo lo aleatorio de v0.15."""

MANIFIESTO = "escenario.json"
_FLUJO = 23
"""El generador de numeros de cada periodo: (semilla, inicio, fin, 23). El escenario de siempre usa
3, 5 y 11; ninguno se comparte."""


@dataclass(frozen=True)
class Reglas:
    """Las reglas del escenario, en la escala del logit: publicas y fijadas antes de entrenar. Un
    tramo es el de `TRAMOS_ATRASO` (0, 1-30, 31-60, 61-90, 91+) y un producto el de `PRODUCTOS`
    (CONSUMO, TARJETA, NOMINA, AUTOMOTRIZ). El atraso entra en dias, con un tope de 180."""

    gestiones_por_tramo: tuple[float, ...] = (0.25, 0.6, 0.9, 1.1, 0.8)
    """Cuantas gestiones por semana, en promedio (Poisson, hasta 4), por tramo: la intensidad de la
    cobranza, que es su politica y no un rasgo de la cuenta."""
    p_otro_canal: float = 0.03
    """La fraccion de gestiones por OTRO canal: sin contacto que medir."""
    contacto_por_canal: tuple[tuple[str, float], ...] = (
        ("CAMPO", -0.2),
        ("TELEFONICA", -0.4),
        ("DIGITAL", -1.6),
    )
    contacto_contactabilidad: float = 1.1
    contacto_atraso: float = -0.004
    p_titular: float = 0.75
    """Con contacto por telefono o en campo, que conteste el titular; por WhatsApp, siempre."""
    promesa_base: float = -0.9
    promesa_voluntad: float = 0.9
    promesa_atraso: float = -0.003
    resultado_titular: tuple[tuple[str, float], ...] = (
        ("CONTACTO", 0.6),
        ("RECHAZO", 0.25),
        ("CONVENIO", 0.15),
    )
    """Con el titular y sin promesa, que salio de la gestion."""
    pago_base: float = -2.1
    pago_voluntad: float = 1.0
    pago_atraso: float = -0.012
    pago_producto: tuple[float, ...] = (0.0, -0.15, 0.35, 0.25)
    pago_contacto_titular: float = 0.9
    pago_promesa: float = 1.2
    pago_al_corriente: float = 0.6
    movimientos_extra: float = 0.4
    """Un pago trae 1 + min(Poisson(0.4), 2) movimientos."""
    p_liquidacion: float = 0.03
    p_repetido: float = 0.005
    p_ajuste: float = 0.003
    tope_atraso: int = 180

    def definicion(self) -> dict:
        """Las reglas como las escribe el manifiesto: cada tupla, una lista (tambien adentro)."""

        def lista(valor):
            return [lista(v) for v in valor] if isinstance(valor, tuple | list) else valor

        return {k: lista(v) for k, v in asdict(self).items()}


REGLAS = Reglas()


# --- los rasgos latentes --------------------------------------------------------------------------


def _normal(azar: _Azar, nombre: str) -> np.ndarray:
    """Una normal estandar por cliente, por Box-Muller, con dos uniformes propias: fija para cada
    cliente y semilla, igual en cualquier corte, lote u orden."""
    u1 = 1.0 - azar(f"{nombre}_u1")  # en (0, 1]: log(0) no existe
    u2 = azar(f"{nombre}_u2")
    return np.sqrt(-2.0 * np.log(u1)) * np.cos(2.0 * np.pi * u2)


def latentes(clientes: np.ndarray, semilla: int) -> tuple[np.ndarray, np.ndarray]:
    """La voluntad y la contactabilidad de cada cliente. Ningun archivo las trae."""
    azar = _Azar(clientes, semilla)
    return _normal(azar, "voluntad"), _normal(azar, "contactabilidad")


def _tramo(dias: np.ndarray) -> np.ndarray:
    tramo = np.zeros(len(dias), dtype=np.int64)
    for j, (_, desde, hasta) in enumerate(TRAMOS_ATRASO):
        tramo[(dias >= desde) & (dias <= (10**9 if hasta is None else hasta))] = j
    return tramo


def _sigmoide(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


# --- un periodo -----------------------------------------------------------------------------------

# Las plantillas de `generador.lifecycle`, por su indice: cada una coherente con lifecycle/v1.
SIN_CONTACTO = {"DIGITAL": (0, 1, 2), "TELEFONICA": (5,), "CAMPO": (11, 12), "OTRO": (17,)}
PESO_SIN_CONTACTO = {"DIGITAL": (0.5, 0.33, 0.17), "TELEFONICA": (1.0,), "CAMPO": (0.8, 0.2)}
TERCERO = {"TELEFONICA": 6, "CAMPO": 13}
TITULAR = {
    "DIGITAL": {"CONTACTO": 3, "PROMESA": 4, "RECHAZO": 3, "CONVENIO": 3},
    "TELEFONICA": {"CONTACTO": 7, "RECHAZO": 8, "PROMESA": 9, "CONVENIO": 10},
    "CAMPO": {"CONTACTO": 14, "RECHAZO": 14, "PROMESA": 15, "CONVENIO": 16},
}
"""Con el titular: el resultado lleva a su plantilla. Por WhatsApp no hay rechazo ni convenio
registrados (queda CONTACTO), y en campo un rechazo es una VISITA_REALIZADA con el titular."""


@dataclass
class Periodo:
    """Lo que paso en un periodo: los pagos, los grupos del lifecycle y sus conteos."""

    movimientos: Movimientos
    grupos: list[lc.Grupo]
    cuentas: int
    gestionadas: int
    contactadas: int
    con_promesa: int
    pagadoras: int


def periodo(
    estado: EstadoCartera,
    *,
    semilla: int,
    desde: date,
    hasta: date,
    reglas: Reglas = REGLAS,
    con_tardios: bool = True,
) -> Periodo:
    """El lifecycle y los pagos del periodo [desde, hasta], de las cuentas de `estado`. Primero lo
    que hizo la cobranza y despues los pagos, que dependen de ello."""
    if hasta < desde:
        raise ValueError(f"El periodo termina antes de empezar: {desde} a {hasta}.")
    rng = np.random.default_rng([semilla, desde.toordinal(), hasta.toordinal(), _FLUJO])
    n = len(estado)
    dias_periodo = (hasta - desde).days + 1
    inicio = np.datetime64(desde, "s")
    fin = np.datetime64(hasta + timedelta(1), "s") - np.timedelta64(1, "s")
    voluntad, contactabilidad = latentes(estado.cliente, semilla)
    atraso = np.minimum(estado.dias_atraso, reglas.tope_atraso).astype(np.float64)
    tramo = _tramo(estado.dias_atraso)

    # 1. Las gestiones, por la intensidad de su tramo, de 7:00 a 21:00.
    intensidad = np.array(reglas.gestiones_por_tramo)[tramo]
    veces = np.minimum(rng.poisson(intensidad), 4)
    cuenta = np.repeat(np.arange(n), veces)
    m = len(cuenta)
    segundos = rng.integers(0, dias_periodo, m) * 86_400 + rng.integers(7 * 3_600, 21 * 3_600, m)
    ocurrido = inicio + segundos.astype("timedelta64[s]")
    canal = np.empty(m, dtype=object)
    otro = rng.random(m) < reglas.p_otro_canal
    eleccion = rng.random(m)
    for j, (etiqueta, _, _) in enumerate(TRAMOS_ATRASO):
        en_tramo = tramo[cuenta] == j
        pesos = [CANAL_POR_TRAMO[etiqueta][k] for k in CANALES]
        canal[en_tramo] = np.array(CANALES, dtype=object)[_elegir(eleccion[en_tramo], pesos)]
    canal[otro] = "OTRO"
    base_contacto = dict(reglas.contacto_por_canal)
    logit_contacto = np.array([base_contacto.get(c, -np.inf) for c in canal], dtype=np.float64)
    logit_contacto = (
        logit_contacto
        + reglas.contacto_contactabilidad * contactabilidad[cuenta]
        + reglas.contacto_atraso * atraso[cuenta]
    )
    contacto = rng.random(m) < _sigmoide(logit_contacto)
    titular = (canal == "DIGITAL") | (rng.random(m) < reglas.p_titular)
    logit_promesa = (
        reglas.promesa_base
        + reglas.promesa_voluntad * voluntad[cuenta]
        + reglas.promesa_atraso * atraso[cuenta]
    )
    promete = contacto & titular & (rng.random(m) < _sigmoide(logit_promesa))
    resultados, pesos_resultado = zip(*reglas.resultado_titular, strict=True)
    otro_resultado = np.array(resultados, dtype=object)[
        rng.choice(len(resultados), size=m, p=np.array(pesos_resultado))
    ]
    sin_contacto = rng.random(m)
    plantilla = np.zeros(m, dtype=np.int64)
    for i in range(m):
        c = canal[i]
        if c == "OTRO":
            plantilla[i] = SIN_CONTACTO["OTRO"][0]
        elif not contacto[i]:
            opciones = SIN_CONTACTO[c]
            plantilla[i] = opciones[_elegir(sin_contacto[i : i + 1], PESO_SIN_CONTACTO[c])[0]]
        elif not titular[i]:
            plantilla[i] = TERCERO[c]
        else:
            plantilla[i] = TITULAR[c]["PROMESA" if promete[i] else otro_resultado[i]]
    nivel = np.array([lc.PLANTILLAS[p][2] for p in plantilla], dtype=object)
    resultado = np.array([lc.PLANTILLAS[p][3] for p in plantilla], dtype=object)
    con_titular = nivel == "CONTACTO_TITULAR"
    con_promesa = resultado == "PROMESA"
    grupos = _grupos(estado, rng, cuenta, ocurrido, plantilla, fin, semilla=semilla, desde=desde)
    if not con_tardios:
        grupos = [lc.Grupo(g.lineas, False, g.tipos) for g in grupos]

    # 2. Los pagos: la voluntad, el atraso, el producto, el titular en la linea, la promesa y estar
    #    al corriente.
    hablo = np.zeros(n, dtype=bool)
    hablo[cuenta[con_titular]] = True
    prometio = np.zeros(n, dtype=bool)
    prometio[cuenta[con_promesa]] = True
    # La primera promesa de cada cuenta en el periodo: su pago llega despues.
    primera = np.full(n, np.iinfo(np.int64).max, dtype=np.int64)
    np.minimum.at(primera, cuenta[con_promesa], ocurrido[con_promesa].astype(np.int64))
    producto = _producto(estado.cliente, semilla)
    logit_pago = (
        reglas.pago_base
        + reglas.pago_voluntad * voluntad
        + reglas.pago_atraso * atraso
        + np.array(reglas.pago_producto)[producto]
        + reglas.pago_contacto_titular * hablo
        + reglas.pago_promesa * prometio
        + reglas.pago_al_corriente * (estado.dias_atraso == 0)
    )
    pagan = np.flatnonzero(rng.random(n) < _sigmoide(logit_pago))
    movimientos = 1 + np.minimum(rng.poisson(reglas.movimientos_extra, len(pagan)), 2)
    indices = np.repeat(pagan, movimientos)
    desde_s = np.where(prometio[indices], primera[indices], inicio.astype(np.int64)).astype(
        np.int64
    )
    hasta_s = fin.astype(np.int64)
    recepcion = desde_s + (rng.random(len(indices)) * (hasta_s - desde_s + 1)).astype(np.int64)
    recepcion = np.minimum(recepcion, hasta_s).astype("datetime64[s]")
    tabla, recuperado, repetidos, ajustes = _tabla_pagos(
        estado, rng, indices, recepcion, semilla=semilla, reglas=reglas
    )
    return Periodo(
        movimientos=Movimientos(
            tabla=tabla,
            cuenta=indices,
            recepcion=recepcion,
            recuperado_centavos=recuperado,
            repetidos=repetidos,
            ajustes=ajustes,
        ),
        grupos=grupos,
        cuentas=n,
        gestionadas=int(len(np.unique(cuenta))),
        contactadas=int(len(np.unique(cuenta[contacto & (canal != "OTRO")]))),
        con_promesa=int(prometio.sum()),
        pagadoras=int(len(pagan)),
    )


def _grupos(
    estado: EstadoCartera,
    rng: np.random.Generator,
    cuenta: np.ndarray,
    ocurrido: np.ndarray,
    plantilla: np.ndarray,
    fin: np.datetime64,
    *,
    semilla: int,
    desde: date,
) -> list[lc.Grupo]:
    """Las gestiones como grupos de lineas de lifecycle/v1, en orden de tiempo de negocio, con lo
    que nace de cada una: su promesa o su convenio, sus cancelaciones, su anulacion. Las mismas
    reglas y probabilidades que `generador.lifecycle`."""
    orden = np.lexsort((cuenta, ocurrido))
    cuenta, ocurrido, plantilla = cuenta[orden], ocurrido[orden], plantilla[orden]
    m = len(cuenta)
    actor = rng.integers(1, 121, m)
    observacion = np.where(
        rng.random(m) < lc.P_OBSERVACION, rng.integers(0, len(lc.OBSERVACIONES), m), -1
    )
    antes_visita = rng.integers(300, 2_400, m).astype("timedelta64[s]")
    despues_visita = rng.integers(300, 2_400, m).astype("timedelta64[s]")
    factor_promesa = rng.uniform(0.6, 2.4, m)
    plazo = rng.integers(2, 15, m)
    factor_convenio = rng.uniform(0.3, 0.9, m)
    cuantas, peso_cuotas = zip(*lc.CUOTAS, strict=True)
    cuotas = np.array(cuantas)[rng.choice(len(cuantas), size=m, p=np.array(peso_cuotas))]
    cancelada = rng.random(m)
    retraso_cierre = rng.integers(3_600, 3 * 86_400, m).astype("timedelta64[s]")
    motivo = rng.integers(0, len(lc.MOTIVOS), m)
    anulada = rng.random(m) < lc.P_ANULADA
    retraso_anulacion = rng.integers(600, 2 * 86_400, m).astype("timedelta64[s]")
    tardio = rng.random(m) < lc.P_TARDIO

    texto_ocurrido = lc._instantes(ocurrido)
    texto_cierre = lc._instantes(np.minimum(ocurrido + retraso_cierre, fin))
    texto_anulacion = lc._instantes(np.minimum(ocurrido + retraso_anulacion, fin))
    texto_inicio = lc._instantes(ocurrido - antes_visita)
    texto_fin = lc._instantes(ocurrido + despues_visita)
    dia_local = ocurrido.astype("datetime64[D]").astype(object)
    saldo = estado.saldo_centavos[cuenta]
    clientes = estado.cliente[cuenta]
    prefijo = f"pr{semilla}-{desde:%Y%m%d}"

    grupos = []
    for i in range(m):
        canal, medio, nivel, resultado, visita, _ = lc.PLANTILLAS[plantilla[i]]
        llave = f"{prefijo}-g{i:07d}"
        actor_ref = f"AGT-{actor[i]:04d}"
        gestion: dict = {
            "tipo": "GESTION_REGISTRADA",
            "idempotency_key": llave,
            "cliente_unico": f"CU{clientes[i]:010d}",
            "ocurrido_en": str(texto_ocurrido[i]),
            "canal": canal,
        }
        if medio is not None:
            gestion["medio"] = medio
        gestion |= {"nivel_contacto": nivel, "resultado": resultado, "actor_ref": actor_ref}
        if observacion[i] >= 0:
            gestion["observacion"] = lc.OBSERVACIONES[observacion[i]]
        if visita is not None:
            gestion["visita"] = {
                "resultado": visita,
                "inicio": str(texto_inicio[i]),
                "fin": str(texto_fin[i]),
            }
        eventos = [gestion]
        if resultado == "PROMESA":
            pago_normal = max(int(saldo[i]) // 52, 5_000)
            eventos.append(
                {
                    "tipo": "PROMESA_CREADA",
                    "idempotency_key": f"{llave}-p",
                    "gestion": llave,
                    "monto_prometido": lc._pesos(max(int(pago_normal * factor_promesa[i]), 5_000)),
                    "fecha_limite": (dia_local[i] + timedelta(int(plazo[i]))).isoformat(),
                    "actor_ref": actor_ref,
                }
            )
            if cancelada[i] < lc.P_PROMESA_CANCELADA:
                eventos.append(lc._cierre("PROMESA_CANCELADA", f"{llave}-p", texto_cierre[i]))
                eventos[-1] |= {"motivo": lc.MOTIVOS[motivo[i]], "actor_ref": actor_ref}
        elif resultado == "CONVENIO":
            eventos.append(
                lc._convenio(llave, int(saldo[i]), factor_convenio[i], int(cuotas[i]), dia_local[i])
            )
            eventos[-1]["actor_ref"] = actor_ref
            if cancelada[i] < lc.P_CONVENIO_CANCELADO:
                eventos.append(lc._cierre("CONVENIO_CANCELADO", f"{llave}-c", texto_cierre[i]))
                eventos[-1] |= {"motivo": lc.MOTIVOS[motivo[i]], "actor_ref": actor_ref}
        if anulada[i]:
            eventos.append(lc._cierre("GESTION_ANULADA", llave, texto_anulacion[i]))
            eventos[-1] |= {"motivo": lc.MOTIVO_ANULACION, "actor_ref": actor_ref}
        grupos.append(
            lc.Grupo(
                tuple(json.dumps(e, ensure_ascii=False, separators=(",", ":")) for e in eventos),
                bool(tardio[i]),
                tuple(e["tipo"] for e in eventos),
            )
        )
    return grupos


def _tabla_pagos(
    estado: EstadoCartera,
    rng: np.random.Generator,
    indices: np.ndarray,
    recepcion: np.ndarray,
    *,
    semilla: int,
    reglas: Reglas,
) -> tuple[pa.Table, np.ndarray, int, int]:
    """Las 23 columnas de pagos/v1 de los movimientos elegidos, con el importe del escenario de
    siempre: PAGO_NORMAL por un factor de 0.5 a 3, una LIQUIDACION paga el saldo entero, una
    fraccion pequena se repite exacta y otra es un ajuste negativo. Devuelve tambien el importe de
    cada movimiento distinto, cuantos repetidos y cuantos ajustes."""
    cuentas = estado.tomar(indices)
    m = len(cuentas)
    c = cuentas.cliente
    azar = _Azar(c, semilla)
    geo = _geografia()
    cve_ent = geo.cve_entidad[_elegir(azar("municipio"), list(geo.peso))]
    gestion = recepcion - rng.integers(0, 10 * 86_400, size=m).astype("timedelta64[s]")
    con_gestion = rng.random(m) < 0.8
    dias = cuentas.dias_atraso
    tramo = _tramo(dias)
    etiquetas = [e for e, _, _ in TRAMOS_ATRASO]
    pago_normal = np.maximum(cuentas.saldo_centavos // 52, 5000)
    recuperado = np.maximum((pago_normal * (0.5 + 2.5 * rng.random(m))).astype(np.int64), 5000)
    liquida = rng.random(m) < reglas.p_liquidacion
    concepto = np.where(
        liquida, CONCEPTOS.index("LIQUIDACION"), (rng.random(m) >= 0.72).astype(np.int64)
    )
    recuperado = np.where(liquida, np.maximum(cuentas.saldo_centavos, 5000), recuperado)
    ajuste = rng.random(m) < reglas.p_ajuste
    recuperado = np.where(ajuste, -np.minimum(recuperado, 50_000), recuperado)
    cargos = np.where(rng.random(m) < 0.05, rng.integers(1_000, 5_001, size=m), 0)
    cobranza = recuperado + cargos
    plan_comision = azar.indice("comision", len(COMISIONES_PUNTOS_BASE))
    puntos = np.array(COMISIONES_PUNTOS_BASE)[plan_comision]
    comision = np.round(cobranza * puntos / 10_000).astype(np.int64)
    iso = pd.DatetimeIndex(recepcion).isocalendar()
    columnas = {
        "Año": _numero(iso["year"].to_numpy()),
        "Semana": _numero(iso["week"].to_numpy()),
        "Territorio": pc.binary_join_element_wise("TERRITORIO", _texto(list(cve_ent)), " "),
        "Zona": pc.binary_join_element_wise("ZONA", _digitos(azar.entero("zona", 1, 12), 2), " "),
        "Cliente_Unico": pc.binary_join_element_wise("CU", _digitos(c, 10), ""),
        "Fecha_Recepción": _instantes(recepcion),
        "Segmento": _de_lista(tuple(etiquetas), tramo),
        "Gerencia": pc.binary_join_element_wise(
            "GERENCIA", _digitos(azar.entero("gerencia", 1, 6), 2), " "
        ),
        "Tipo_Cartera": _de_lista(
            ("TRADICIONAL", "ESPECIAL"), (azar("tipo_cartera") >= 0.9).astype(np.int64)
        ),
        "Producto": _de_lista(PRODUCTOS, _producto(c, semilla)),
        "Campaña": pc.binary_join_element_wise(
            "CAMP", _digitos(azar.entero("campania", 1, 6), 2), "-"
        ),
        "Gestor": pc.binary_join_element_wise(
            "GESTOR", _digitos(azar.entero("gestor", 1, 80), 3), " "
        ),
        "Días_de_Atraso": _numero(dias),
        "Semanas_de_Atraso": _numero(-(-dias // 7)),
        "Plan_de_Pago": _de_lista(("N", "S"), (azar("plan") < 0.15).astype(np.int64)),
        "Fecha_de_Gestion": _o_nulo(_instantes(gestion), con_gestion),
        "Recuperación_por_Gestión": _pesos(recuperado),
        "Concepto_Cálculo": pc.if_else(pa.array(ajuste), "AJUSTE", _de_lista(CONCEPTOS, concepto)),
        "Cargos_Automáticos": _pesos(cargos),
        "Captación": _pesos(recuperado),
        "Cobranza_Total": _pesos(cobranza),
        "Porcentaje_Comision": _de_lista(
            tuple(str(p / 10_000) for p in COMISIONES_PUNTOS_BASE), plan_comision
        ),
        "Monto_Comision": _pesos(comision),
    }
    tabla = pa.table({nombre: columnas[nombre] for nombre in CONTRATO_PAGOS.nombres})
    copias = (
        rng.choice(m, size=round(m * reglas.p_repetido), replace=False) if m else np.array([], int)
    )
    tabla = pa.concat_tables([tabla, tabla.take(pa.array(np.sort(copias), type=pa.int64()))])
    orden = pc.sort_indices(tabla, sort_keys=[("Fecha_Recepción", "ascending")])
    return tabla.take(orden), recuperado, len(copias), int(ajuste.sum())


# --- el escenario ---------------------------------------------------------------------------------

INVARIANTES = (
    "Determinista: la misma semilla y los mismos parametros producen los mismos archivos, byte por "
    "byte, y el mismo manifiesto.",
    "Las reglas son publicas (`reglas` del manifiesto) y se fijaron antes de entrenar ningun "
    "modelo: no se ajustan despues de ver una metrica.",
    "Los rasgos latentes (voluntad y contactabilidad) no estan en ningun archivo.",
    "Cada corte cumple cartera/v2, cada periodo cumple pagos/v1 y lifecycle/v1, y los pagos y las "
    "gestiones de un periodo son de cuentas del corte que lo abre.",
    "La cobertura es la que el generador escribio: los pagos y el lifecycle estan completos hasta "
    "el ultimo corte, y ningun evento ocurre despues de el.",
    "Un grupo tardio (una gestion con lo que nacio de ella) ocurrio en un periodo y llega en el "
    "archivo del siguiente; el ultimo periodo no tiene tardios.",
    "Ningun dato corresponde a una persona ni a una cartera real.",
)


@dataclass(frozen=True)
class EscenarioPredictivo:
    destino: Path
    manifiesto: dict = field(default_factory=dict)


def generar_escenario_predictivo(
    destino: str | Path,
    *,
    cuentas: int,
    cortes: int,
    primer_corte: date,
    semilla: int = SEMILLA,
    dias_entre_cortes: int = 7,
    formato: str = "zip",
    tasa_altas: float = 0.02,
    tasa_retiros: float = 0.01,
    reglas: Reglas = REGLAS,
    hoy: date | None = None,
) -> EscenarioPredictivo:
    """Escribe `cortes` cortes semanales (o cada `dias_entre_cortes`) de la misma cartera, los
    pagos y el lifecycle de cada periodo entre un corte y el siguiente, y el manifiesto
    (escenario.json) con cada archivo, su SHA-256, sus conteos, las reglas y la cobertura.

    El escenario termina antes de `hoy` (por omision, la fecha del sistema): un evento operacional
    no ocurre en el futuro."""
    if cortes < 2:
        raise ValueError("Un escenario predictivo tiene al menos dos cortes: un periodo.")
    if dias_entre_cortes < 1:
        raise ValueError("Entre un corte y el siguiente pasa al menos un dia.")
    extension = formato.lower().lstrip(".")
    if extension not in ("csv", "zip"):
        raise ValueError(f"Formato no soportado: {formato!r}. Usa zip o csv.")
    ultimo_corte = primer_corte + timedelta(days=dias_entre_cortes * (cortes - 1))
    if ultimo_corte >= (hoy or date.today()):
        raise ValueError(
            f"El escenario tiene que terminar antes de hoy, y su ultimo corte es el "
            f"{ultimo_corte.isoformat()}: un evento operacional no ocurre en el futuro. Usa un "
            "primer corte anterior."
        )
    directorio = Path(destino)
    directorio.mkdir(parents=True, exist_ok=True)
    periodos_lc = lc.Periodos(directorio)
    usados: set[int] = set()
    estado = estado_inicial(cuentas, semilla=semilla, fecha_corte=primer_corte, usados=usados)
    registro_cortes: list[dict] = []
    registro_periodos: list[dict] = []
    corte = primer_corte
    evolucion = None
    for numero in range(cortes):
        ruta = directorio / f"cartera_oficial_{corte.isoformat()}.{extension}"
        escrito = escribir_cartera(
            estado, ruta, semilla=semilla, fecha_corte=corte, con_carrier=False
        )
        registro_cortes.append(
            {
                "fecha_corte": corte.isoformat(),
                "archivo": ruta.name,
                "sha256": _sha256(ruta),
                "cuentas": escrito.filas,
                **(asdict(evolucion) if evolucion is not None else {}),
            }
        )
        if numero == cortes - 1:
            break
        siguiente = corte + timedelta(days=dias_entre_cortes)
        desde = corte + timedelta(days=1)
        hecho = periodo(
            estado,
            semilla=semilla,
            desde=desde,
            hasta=siguiente,
            reglas=reglas,
            con_tardios=numero < cortes - 2,
        )
        ruta = directorio / f"pagos_oficial_{desde.isoformat()}_{siguiente.isoformat()}.{extension}"
        escritos = escribir_pagos(hecho.movimientos.tabla, ruta)
        archivo_lc = periodos_lc.agregar(desde, siguiente, hecho.grupos)
        registro_periodos.append(
            {
                "desde": desde.isoformat(),
                "hasta": siguiente.isoformat(),
                "archivo": ruta.name,
                "sha256": _sha256(ruta),
                "movimientos": escritos.filas,
                "repetidos_exactos": hecho.movimientos.repetidos,
                "ajustes": hecho.movimientos.ajustes,
                "lifecycle": archivo_lc.manifiesto(),
                "cuentas": hecho.cuentas,
                "gestionadas": hecho.gestionadas,
                "contactadas": hecho.contactadas,
                "con_promesa": hecho.con_promesa,
                "pagadoras": hecho.pagadoras,
            }
        )
        estado, evolucion = evolucionar(
            estado,
            hecho.movimientos,
            semilla=semilla,
            corte=corte,
            siguiente=siguiente,
            usados=usados,
            tasa_altas=tasa_altas,
            tasa_retiros=tasa_retiros,
        )
        corte = siguiente
    manifiesto = {
        "version": VERSION_ESCENARIO,
        "semilla": semilla,
        "cuentas_iniciales": cuentas,
        "dias_entre_cortes": dias_entre_cortes,
        "tasa_altas": tasa_altas,
        "tasa_retiros": tasa_retiros,
        "formato": extension,
        "contratos": {
            "cortes": CONTRATO_V2.version,
            "periodos": CONTRATO_PAGOS.version,
            "lifecycle": "lifecycle/v1",
        },
        "reglas": reglas.definicion(),
        "cobertura": {
            "pagos_completos_desde": (primer_corte + timedelta(days=1)).isoformat(),
            "pagos_completos_hasta": ultimo_corte.isoformat(),
            "lifecycle_completo_desde": (primer_corte + timedelta(days=1)).isoformat(),
            "lifecycle_completo_hasta": ultimo_corte.isoformat(),
        },
        "tasas_semanales": _tasas(registro_periodos),
        "cortes": registro_cortes,
        "periodos": registro_periodos,
        "invariantes": list(INVARIANTES),
    }
    (directorio / MANIFIESTO).write_text(
        json.dumps(manifiesto, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return EscenarioPredictivo(directorio, manifiesto)


def _tasas(periodos: list[dict]) -> dict:
    """Las tasas semanales que el generador produjo, sumadas sobre todos los periodos: la escala
    de lo que el escenario trae, contada al generarlo y antes de cualquier modelo."""
    total = Counter()
    for p in periodos:
        for clave in ("cuentas", "gestionadas", "contactadas", "con_promesa", "pagadoras"):
            total[clave] += p[clave]
    cuentas = total["cuentas"] or 1
    return {
        "cuenta_semanas": total["cuentas"],
        "gestionadas": round(total["gestionadas"] / cuentas, 4),
        "contactadas": round(total["contactadas"] / cuentas, 4),
        "con_promesa": round(total["con_promesa"] / cuentas, 4),
        "pagadoras": round(total["pagadoras"] / cuentas, 4),
    }


def perfil(nombre: str) -> int:
    """Cuantas cuentas trae un perfil (XS a XXL)."""
    return PERFILES[nombre]


def _sha256(ruta: Path) -> str:
    digesto = hashlib.sha256()
    with ruta.open("rb") as archivo:
        while bloque := archivo.read(1 << 20):
            digesto.update(bloque)
    return digesto.hexdigest()
