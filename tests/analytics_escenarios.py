"""Lo que comparten las pruebas de analytics/v1: el escenario golden, como abrir y analizar una
ejecucion, y como leer lo que publico. Todo es sintetico: las cuentas, los pagos, las gestiones, las
bases, los gestores y los puntos (metros desde el origen de la red sintetica de ruteo/v2, dentro de
los municipios sinteticos A y B). Ninguna ubicacion ni ninguna persona es real.

El golden es una cohorte de 20 cuentas, el corte del miercoles 2026-09-30, con todo lo que se
declara en un manifiesto, construido como lo haria la operacion: el corte (y uno anterior, del
2026-09-23, con una cuenta que ya no esta), decision/v2, la geografia, dos bases, territorial/v2,
campo/v2 del jueves 2026-10-01 y su ruteo/v2, dos archivos de pagos interpretados (septiembre dos
veces: el reverso de octubre de un pago de septiembre la reinterpreta), la atribucion de cada mes,
el lifecycle y la evaluacion de las promesas al 2026-10-04 y al 2026-10-08. Despues, una frontera
de conocimiento K1; luego llegan tarde dos gestiones y una anulacion; despues, K2.

Las cuentas (dias de atraso, producto, municipio):

- 1-10 CONSUMO, 11-19 TARJETA, 20 NOMINA: un producto visible (10) y dos suprimidos (9 y 1);
- 1-15 en PUEBLA (21114) y 16-20 en AMOZOC (21015), de la misma entidad: AMOZOC se suprime por
  pequena y PUEBLA, que la dejaria reconstruir, por complementaria;
- dias: 6 y 7 con 0, 4 y 5 con 45, 15-20 con 120 (CAMPO) y las demas con 15: el tramo 1-30 y la
  prioridad MEDIA tienen 10 cuentas;
- la 20 no tiene coordenadas (SIN_COORDENADAS: no se asigna a una base) ni saldo atrasado (NULL);
  la 7 tiene saldo atrasado 0.

Lo que prueba cada una:

- 1: una promesa CUMPLIDA y un pago con una sola gestion candidata (ASOCIACION_UNICA);
- 2: dos observaciones identicas del mismo pago (un solo movimiento) y dos gestiones con contacto
  antes de el (AMBIGUA);
- 3: un pago y su reverso, los dos en el horizonte;
- 4: un pago del 3 de octubre y su reverso del 6: despues de as_of 2026-10-04, dentro de as_of
  2026-10-08;
- 5: un pago de 100 y un posible reverso de -150: neto -50, sin recortar a cero;
- 6: un pago del 30 de septiembre sin gestiones (SIN_GESTION_CANDIDATA) que un reverso del 6 de
  octubre anula: otra ventana, despues de as_of 2026-10-04;
- 7: un pago del 2026-10-08, el ultimo dia que cubren los pagos;
- 8: tres gestiones, dos pagos y dos promesas, todo el 1 de octubre: nada se multiplica;
- 9, 10, 11, 12 y 13: promesas PARCIAL, INCUMPLIDA, PENDIENTE, CANCELADA y, al 2026-10-08,
  NO_EVALUABLE (al 2026-10-04 todavia PENDIENTE);
- 14: dos gestiones del 3 de octubre que se registran despues de K1 (llegan tarde);
- 15: una gestion anulada por una anulacion que ocurre el 6 de octubre (despues de as_of
  2026-10-04), registrada antes de K1;
- 16: una gestion anulada por una anulacion que ocurre el 3 de octubre y se registra despues de K1;
- 17: una visita de campo; 18 y 19: mensajes de una via (SMS el 2, EMAIL el 7);
- 15-20: las candidatas de campo. GESTOR_A (BASE_A, cupo 5) tiene la 15; GESTOR_B (BASE_B, cupo 3,
  media hora el jueves) tiene tres de 16-19 y la otra queda SIN_CAPACIDAD; la 20 queda en
  REVISION_TERRITORIAL. En la media hora de GESTOR_B cabe una visita: las otras dos, SIN_TIEMPO.

Ademas: un pago de un cliente sin cuenta (9001) y uno de la 99, que solo esta en el corte anterior.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from campo_v2_escenarios import LV_PARTIDO
from decision_v2_escenarios import atribuir_mes, reloj
from geografia_escenarios import AMOZOC, FUENTE, PUEBLA, corte_de, geolocalizar
from historia_escenarios import (
    SEMILLA,
    Cuenta,
    cuenta,
    estado,
    ingerir_corte,
    ingerir_pagos_de,
    pago,
)
from lifecycle_escenarios import anular_gestion, cancelar_promesa, gestion_de, promesa_de
from motor_pagos_escenarios import ejecuciones, historiar_todo, interpretar_todo, vigente
from ruteo_v2_escenarios import ORIGEN, ProveedorDePruebas, base, gestor, red_de_pruebas, rutear
from sqlalchemy import text
from sqlmodel import select
from territorial_v2_escenarios import asignar_tv2, publicar_bases, referencia_tv2

from motor_cartera.analytics import ejecuciones as motor
from motor_cartera.analytics.manifiesto import Peticion
from motor_cartera.analytics.politica import Granularidad, Modo
from motor_cartera.campo_v2 import configuracion as campo
from motor_cartera.campo_v2 import ejecuciones as motor_campo
from motor_cartera.config import config
from motor_cartera.db.modelos import (
    EjecucionAnalytics,
    EjecucionAtribucion,
    EjecucionCampoV2,
    EjecucionDecisionV2,
    EjecucionEvaluacionPromesas,
    EjecucionMotorPagos,
    ModoDecisionV2,
)
from motor_cartera.db.sesion import sesion
from motor_cartera.decision_v2 import ejecuciones as decision_v2
from motor_cartera.evaluacion import ejecuciones as evaluacion
from motor_cartera.generador.oficial import escribir_cartera, tabla_cartera
from motor_cartera.ingesta.corridas import ingerir_archivo
from motor_cartera.lifecycle import registro
from motor_cartera.lifecycle.reglas import (
    Canal,
    Medio,
    NivelContacto,
    ResultadoGestion,
    ResultadoVisita,
)

ZONA = "America/Mexico_City"
DESPACHO, CARTERA = "DSP_001", "CARTERA_PRINCIPAL"
CORTE = date(2026, 9, 30)
"""Un miercoles."""
CORTE_ANTERIOR = date(2026, 9, 23)
JUEVES = date(2026, 10, 1)
AS_OF_A = date(2026, 10, 4)
AS_OF_B = date(2026, 10, 8)
SEPTIEMBRE, OCTUBRE = date(2026, 9, 1), date(2026, 10, 1)

BASE_A, BASE_B = (1_000, 1_000), (15_500, 1_000)
"""BASE_A en el municipio A (PUEBLA), BASE_B en el B (AMOZOC), en metros desde el origen."""

CORTO = {"codigo": "CORTO", "semana": {"JUEVES": [{"inicio": "09:00", "fin": "09:30"}]}}
"""Media hora el jueves: cabe una visita cerca de la base, no dos."""


@dataclass(frozen=True)
class CuentaGolden:
    """Una cuenta del corte: su numero, sus dias de atraso, su producto, el municipio que dice la
    fuente, su punto en metros desde el origen (None: sin coordenadas) y su saldo atrasado (None:
    vacio)."""

    numero: int
    dias: int
    producto: str
    municipio: str
    punto: tuple[int, int] | None
    saldo_atrasado: str | None = "500.00"
    saldo_total: str = "10000.00"


def _cuenta(numero: int) -> CuentaGolden:
    dias = {6: 0, 7: 0, 4: 45, 5: 45}.get(numero, 120 if numero >= 15 else 15)
    producto = "CONSUMO" if numero <= 10 else "TARJETA" if numero <= 19 else "NOMINA"
    if numero <= 15:
        municipio, punto = PUEBLA, (500 + 100 * numero, 1_500 + 50 * numero)
    else:
        municipio, punto = AMOZOC, (15_500 + 250 * (numero - 15), 1_250)
    if numero == 20:
        punto = None
    atrasado = {7: "0.00", 20: None}.get(numero, "500.00")
    return CuentaGolden(numero, dias, producto, municipio, punto, atrasado)


CUENTAS = [_cuenta(n) for n in range(1, 21)]
CUENTA_ANTERIOR = 99
"""Solo esta en el corte anterior: un pago suyo es de una cuenta que no es de la cohorte."""
SIN_CUENTA = 9001
"""Un cliente que no esta en ningun corte: su pago no tiene cuenta."""

AJUSTE = {"Concepto_Cálculo": "AJUSTE"}
PAGOS_SEPTIEMBRE = [pago(6, "2026-09-30 10:00:00", "250.00")]
PAGOS_OCTUBRE = [
    pago(8, "2026-10-01 11:00:00", "300.00"),
    pago(8, "2026-10-01 15:00:00", "200.00"),
    pago(9, "2026-10-02 11:00:00", "200.00"),
    pago(1, "2026-10-02 12:00:00", "1000.00"),
    pago(2, "2026-10-02 12:00:00", "500.00"),
    pago(2, "2026-10-02 12:00:00", "500.00"),
    pago(3, "2026-10-02 13:00:00", "800.00"),
    pago(SIN_CUENTA, "2026-10-02 14:00:00", "75.00"),
    pago(5, "2026-10-03 09:00:00", "100.00"),
    pago(CUENTA_ANTERIOR, "2026-10-03 10:00:00", "60.00"),
    pago(4, "2026-10-03 11:00:00", "300.00"),
    pago(3, "2026-10-03 13:00:00", "-800.00", **AJUSTE),
    pago(5, "2026-10-04 09:00:00", "-150.00", **AJUSTE),
    pago(6, "2026-10-06 10:00:00", "-250.00", **AJUSTE),
    pago(4, "2026-10-06 11:00:00", "-300.00", **AJUSTE),
    pago(7, "2026-10-08 10:00:00", "120.00"),
]


# --- el corte -------------------------------------------------------------------------------------


def tabla(cuentas: list[CuentaGolden], corte: date):
    """Las 93 columnas de cartera/v2 de esas cuentas, con su producto, sus saldos, su municipio y
    su punto."""
    base_ = tabla_cartera(
        estado([Cuenta(c.numero, dias=c.dias) for c in cuentas]), semilla=SEMILLA, fecha_corte=corte
    ).to_pandas()
    base_ = base_.astype(object)
    for i, c in enumerate(cuentas):
        entidad, poblacion = FUENTE[c.municipio]
        base_.at[i, "ESTADO_CTE"] = entidad
        base_.at[i, "POBLACION_CTE"] = poblacion
        base_.at[i, "PRODUCTO"] = c.producto
        base_.at[i, "SALDO_TOTAL"] = c.saldo_total
        base_.at[i, "SALDO ATRASADO"] = c.saldo_atrasado
        if c.punto is None:
            base_.at[i, "LATITUD"], base_.at[i, "LONGITUD"] = None, None
        else:
            lon, lat = ORIGEN.grados(*c.punto)
            base_.at[i, "LATITUD"], base_.at[i, "LONGITUD"] = str(lat), str(lon)
    return base_


def ingerir(destino: Path, cuentas: list[CuentaGolden], corte: date = CORTE) -> None:
    ruta = destino / f"analytics_{corte.isoformat()}.csv"
    escribir_cartera(
        estado([]), ruta, semilla=SEMILLA, fecha_corte=corte, tabla=tabla(cuentas, corte)
    )
    corrida = ingerir_archivo(ruta, contrato="cartera/v2", fecha_corte=corte)
    assert corrida.estado == "EXITOSA", corrida.detalle
    historiar_todo()


# --- la cadena de operacion -----------------------------------------------------------------------


def decidir(corte: date = CORTE, *, backtest: bool = False):
    """decision/v2 del corte, OPERATIVO (o BACKTEST con el reloj de la base, para un corte que ya no
    es el mas reciente), antes de cualquier pago o gestion: la prioridad de cada cuenta es la de sus
    dias de atraso."""
    with sesion() as s:
        ejecucion = decision_v2.abrir(
            s,
            corte_de(corte).corte_id,
            as_of=None,
            known_at=s.execute(text("SELECT now()")).scalar_one() if backtest else None,
            modo=ModoDecisionV2.BACKTEST if backtest else ModoDecisionV2.OPERATIVO,
            version_politica=None,
            zona=ZONA,
            max_intentos=5,
        )
        s.commit()
        ejecucion_id = ejecucion.id
    decision_v2.ejecutar_decision_v2(ejecucion_id)
    with sesion() as s:
        decidida = s.get_one(EjecucionDecisionV2, ejecucion_id)
    assert decidida.estado == "EXITOSA", decidida.detalle
    return decidida


def operacion(destino: Path) -> SimpleNamespace:
    """La geografia, las bases, territorial/v2, la configuracion de campo, el plan del jueves y su
    ruteo, sobre la decision/v2 del corte."""
    referencia = referencia_tv2(destino)
    decision = decidir()
    geografia = geolocalizar(CORTE, referencia)
    assert geografia.estado == "EXITOSA", geografia.detalle
    bases = publicar_bases(base("BASE_A", *BASE_A, [PUEBLA]), base("BASE_B", *BASE_B, [AMOZOC]))
    territorial = asignar_tv2(CORTE, geografia, bases)
    assert territorial.estado == "EXITOSA", territorial.detalle
    with sesion() as s:
        publicada = campo.publicar(
            s,
            campo.leer(
                {
                    "nombre": "Campo de analytics",
                    "bases_version_id": str(bases.bases_version_id),
                    "zona_horaria": ZONA,
                    "calendarios": [LV_PARTIDO, CORTO],
                    "gestores": [
                        gestor("GESTOR_A", "BASE_A", capacidad=5),
                        gestor("GESTOR_B", "BASE_B", capacidad=3, calendario="CORTO"),
                    ],
                }
            ),
        )
        s.commit()
        s.refresh(publicada.version)
        configuracion = publicada.version
    with sesion() as s:
        plan = motor_campo.abrir(
            s,
            corte_de(CORTE).corte_id,
            decision.decision_run_id,
            territorial.territorial_run_id,
            configuracion.configuracion_campo_version_id,
            JUEVES,
            max_intentos=config.worker_max_intentos,
        )
        s.commit()
        plan_id = plan.id
    motor_campo.ejecutar_campo_v2(plan_id)
    with sesion() as s:
        plan = s.get_one(EjecucionCampoV2, plan_id)
    assert plan.estado == "EXITOSA", plan.detalle
    red = red_de_pruebas()
    ruteo = rutear(plan, red, ProveedorDePruebas(red.huella))
    assert ruteo.estado == "EXITOSA", ruteo.detalle
    return SimpleNamespace(
        decision=decision,
        geografia=geografia,
        bases=bases,
        territorial=territorial,
        plan=plan,
        ruteo=ruteo,
    )


# --- el lifecycle ---------------------------------------------------------------------------------


def gestion(numero: int, ocurrido_en: str, **campos):
    """Una gestion de la cuenta `numero` el dia y la hora dados, en la hora de la Ciudad de Mexico;
    por omision, una llamada con el titular que termina en PROMESA."""
    return gestion_de(cuenta(numero).cuenta_id, f"{ocurrido_en}:00-06:00", **campos)


def contacto(numero: int, ocurrido_en: str, **campos):
    return gestion(numero, ocurrido_en, resultado=ResultadoGestion.CONTACTO, **campos)


def sin_contacto(numero: int, ocurrido_en: str):
    return gestion(
        numero,
        ocurrido_en,
        nivel_contacto=NivelContacto.SIN_CONTACTO,
        resultado=ResultadoGestion.SIN_RESPUESTA,
    )


def mensaje(numero: int, ocurrido_en: str, medio: Medio):
    """Un mensaje de una via: el contacto no aplica."""
    return gestion(
        numero,
        ocurrido_en,
        canal=Canal.DIGITAL,
        medio=medio,
        nivel_contacto=NivelContacto.NO_APLICA,
        resultado=ResultadoGestion.SIN_RESPUESTA,
    )


def promesa(numero: int, ocurrido_en: str, monto: str, fecha_limite: str):
    """Una llamada con el titular que termina en PROMESA, con el actor_ref de quien la hizo, y su
    promesa."""
    hecho = gestion(numero, ocurrido_en, actor_ref=f"AGT-{numero:04d}")
    return hecho, promesa_de(hecho.recurso_id, monto, fecha_limite)


def lifecycle() -> SimpleNamespace:
    """Lo que se registra antes de K1."""
    # La 8: tres gestiones, dos pagos y dos promesas, el 1 de octubre.
    promesa(8, "2026-10-01T09:00", "300.00", "2026-10-05")
    promesa(8, "2026-10-01T12:00", "200.00", "2026-10-06")
    sin_contacto(8, "2026-10-01T18:00")
    # Las promesas del 2: CUMPLIDA, PARCIAL, INCUMPLIDA, PENDIENTE, CANCELADA y la que al 8 ya no
    # se puede evaluar.
    promesa(1, "2026-10-02T08:00", "1000.00", "2026-10-05")
    promesa(9, "2026-10-02T08:30", "500.00", "2026-10-03")
    promesa(10, "2026-10-02T08:45", "400.00", "2026-10-03")
    promesa(11, "2026-10-02T10:00", "600.00", "2026-10-12")
    _, cancelada = promesa(12, "2026-10-02T10:00", "700.00", "2026-10-10")
    cancelar_promesa(cancelada.recurso_id, "2026-10-03T09:00:00-06:00")
    promesa(13, "2026-10-02T10:00", "800.00", "2026-10-08")
    # La 2: dos gestiones con contacto antes de su pago.
    contacto(2, "2026-10-02T08:00")
    contacto(2, "2026-10-02T09:00", nivel_contacto=NivelContacto.CONTACTO_TERCERO)
    # La 15: anulada por una anulacion que ocurre despues de as_of 2026-10-04.
    anulada = contacto(15, "2026-10-02T10:00")
    anular_gestion(anulada.recurso_id, "2026-10-06T09:00:00-06:00")
    # La 16: su anulacion ocurre el 3, pero se registra despues de K1.
    tardia = sin_contacto(16, "2026-10-02T10:00")
    gestion(
        17,
        "2026-10-02T12:00",
        canal=Canal.CAMPO,
        medio=None,
        resultado=ResultadoGestion.VISITA_REALIZADA,
        visita=registro.DatosVisita(ResultadoVisita.CONTACTO_TITULAR),
    )
    mensaje(18, "2026-10-02T09:00", Medio.SMS)
    mensaje(19, "2026-10-07T10:00", Medio.EMAIL)
    return SimpleNamespace(anulacion_tardia=tardia.recurso_id)


def evaluar_promesas(as_of: date) -> EjecucionEvaluacionPromesas:
    with sesion() as s:
        ejecucion, _ = evaluacion.abrir(
            s, DESPACHO, CARTERA, as_of, zona=ZONA, max_intentos=5, reusar=False
        )
        s.commit()
        ejecucion_id = ejecucion.id
    evaluacion.evaluar(ejecucion_id)
    with sesion() as s:
        evaluada = s.get_one(EjecucionEvaluacionPromesas, ejecucion_id)
    assert evaluada.estado == "EXITOSA", evaluada.detalle
    return evaluada


def atribucion_de(periodo: date) -> EjecucionAtribucion:
    atribuir_mes(periodo)
    with sesion() as s:
        return s.exec(
            select(EjecucionAtribucion)
            .where(EjecucionAtribucion.periodo_desde == periodo)
            .order_by(EjecucionAtribucion.id.desc())
        ).first()


# --- el golden ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Golden:
    corte: object
    operacion: SimpleNamespace
    septiembre_previa: EjecucionMotorPagos
    """La primera interpretacion de septiembre, antes de que llegara el reverso de octubre."""
    septiembre: EjecucionMotorPagos
    octubre: EjecucionMotorPagos
    atribucion_septiembre: EjecucionAtribucion
    atribucion_octubre: EjecucionAtribucion
    evaluacion_a: EjecucionEvaluacionPromesas
    evaluacion_b: EjecucionEvaluacionPromesas
    k1: datetime
    k2: datetime

    def peticion(
        self,
        as_of: date,
        known_at: datetime | None,
        *,
        granularidad: Granularidad = Granularidad.DIA,
        modo: Modo = Modo.BACKTEST,
        **cambios,
    ) -> Peticion:
        """La peticion con todas las entradas del golden, salvo las que se cambien."""
        evaluacion_ = {AS_OF_A: self.evaluacion_a, AS_OF_B: self.evaluacion_b}.get(as_of)
        o = self.operacion
        campos = {
            "corte_id": self.corte.corte_id,
            "as_of": as_of,
            "modo": modo,
            "zona_horaria": ZONA,
            "granularidad": granularidad,
            "known_at": known_at,
            "motor_pagos": (self.septiembre.motor_pagos_run_id, self.octubre.motor_pagos_run_id),
            "atribucion": (
                self.atribucion_septiembre.atribucion_run_id,
                self.atribucion_octubre.atribucion_run_id,
            ),
            "evaluacion_promesas": None if evaluacion_ is None else evaluacion_.evaluacion_run_id,
            "decision_v2": o.decision.decision_run_id,
            "geografia": o.geografia.geografia_run_id,
            "territorial_v2": o.territorial.territorial_run_id,
            "campo_v2": o.plan.campo_run_id,
            "ruteo_v2": o.ruteo.ruteo_run_id,
        }
        campos.update(cambios)
        return Peticion(**campos)


def golden(destino: Path) -> Golden:
    """El escenario golden, con sus dos fronteras. Ver el docstring del modulo."""
    ingerir_corte(destino, CORTE_ANTERIOR, [Cuenta(CUENTA_ANTERIOR)])
    historiar_todo()
    ingerir(destino, CUENTAS)
    o = operacion(destino)
    ingerir_pagos_de(destino, "pagos_septiembre.csv", PAGOS_SEPTIEMBRE)
    historiar_todo()
    interpretar_todo()
    ingerir_pagos_de(destino, "pagos_octubre.csv", PAGOS_OCTUBRE)
    historiar_todo()
    interpretar_todo()
    previa, *_ = ejecuciones(SEPTIEMBRE)
    tarde = lifecycle()
    evaluacion_a = evaluar_promesas(AS_OF_A)
    evaluacion_b = evaluar_promesas(AS_OF_B)
    atribucion_septiembre = atribucion_de(SEPTIEMBRE)
    atribucion_octubre = atribucion_de(OCTUBRE)

    k1 = reloj()
    # Lo que llega despues de K1: dos gestiones del 3 y la anulacion, ocurrida el 3, de la 16.
    contacto(14, "2026-10-03T11:00")
    sin_contacto(14, "2026-10-03T15:00")
    anular_gestion(tarde.anulacion_tardia, "2026-10-03T09:00:00-06:00")
    k2 = reloj()
    return Golden(
        corte=corte_de(CORTE),
        operacion=o,
        septiembre_previa=previa,
        septiembre=vigente(SEPTIEMBRE),
        octubre=vigente(OCTUBRE),
        atribucion_septiembre=atribucion_septiembre,
        atribucion_octubre=atribucion_octubre,
        evaluacion_a=evaluacion_a,
        evaluacion_b=evaluacion_b,
        k1=k1,
        k2=k2,
    )


# --- abrir, analizar y leer -----------------------------------------------------------------------


def abrir(peticion: Peticion, **opciones) -> int:
    with sesion() as s:
        ejecucion = motor.abrir(s, peticion, max_intentos=5, **opciones)
        s.commit()
        return ejecucion.id


def ejecucion(ejecucion_id: int) -> EjecucionAnalytics:
    with sesion() as s:
        return s.get_one(EjecucionAnalytics, ejecucion_id)


def analizar(peticion: Peticion) -> EjecucionAnalytics:
    """Abre la ejecucion y la analiza, como lo haria su trabajo, y la devuelve terminada."""
    ejecucion_id = abrir(peticion)
    motor.ejecutar_analytics(ejecucion_id)
    return ejecucion(ejecucion_id)


def _llave(dimensiones: dict) -> tuple:
    return tuple(sorted(dimensiones.items()))


def agregados(ejecucion_id: int) -> dict[tuple, dict]:
    """Lo publicado, por (metrica, vista, conjunto, periodo_desde, dimensiones)."""
    with sesion() as s:
        filas = (
            s.execute(
                text(
                    "SELECT metrica_codigo, vista, dimension_set, periodo_desde, periodo_hasta, "
                    "periodo_parcial, dimensiones, encode(dimension_hash, 'hex') AS hash, "
                    "estado_valor, poblacion_cuentas, valor, numerador, denominador, motivos "
                    "FROM agregado_analytics WHERE ejecucion_analytics_id = :e"
                ),
                {"e": ejecucion_id},
            )
            .mappings()
            .all()
        )
    return {
        (
            f["metrica_codigo"],
            f["vista"],
            f["dimension_set"],
            f["periodo_desde"],
            _llave(f["dimensiones"]),
        ): dict(f)
        for f in filas
    }


def celda(
    publicados: dict[tuple, dict],
    metrica: str,
    conjunto: str = "TOTAL",
    *,
    vista: str = "ACUMULADA",
    periodo: date = CORTE,
    **dimensiones: str,
) -> dict:
    return publicados[(metrica, vista, conjunto, periodo, _llave(dimensiones))]


def valor(publicados: dict[tuple, dict], metrica: str, conjunto: str = "TOTAL", **opciones):
    """El valor de una celda DISPONIBLE."""
    fila = celda(publicados, metrica, conjunto, **opciones)
    assert fila["estado_valor"] == "DISPONIBLE", (metrica, conjunto, opciones, fila)
    return fila["valor"]


def controles(ejecucion_id: int) -> dict[str, dict]:
    with sesion() as s:
        filas = (
            s.execute(
                text(
                    "SELECT codigo_control, estado, esperado, observado, diferencia, detalle "
                    "FROM control_analytics WHERE ejecucion_analytics_id = :e"
                ),
                {"e": ejecucion_id},
            )
            .mappings()
            .all()
        )
    return {f["codigo_control"]: dict(f) for f in filas}


def resultado_logico(ejecucion_id: int) -> dict:
    """Lo que una reconstruccion tiene que repetir: cada agregado y cada control, sin ids internos
    ni instantes."""
    e = ejecucion(ejecucion_id)
    return {
        "agregados": agregados(ejecucion_id),
        "controles": controles(ejecucion_id),
        "hash_agregados": e.hash_agregados,
        "firma_inputs": e.firma_inputs,
        "hash_manifiesto": e.hash_manifiesto,
        "agregados_publicados": e.agregados_publicados,
        "disponibilidad": e.disponibilidad,
    }


def borrar(ejecucion_id: int) -> None:
    """Borra una ejecucion terminada y solo lo derivado de ella (sus agregados, sus controles, sus
    entradas y su trabajo), apagando un momento los disparadores que lo protegen: para
    reconstruirla con el mismo manifiesto. Ninguna entrada se toca."""
    disparadores = (
        ("entrada_analytics", "tr_analytics_inmutable"),
        ("agregado_analytics", "tr_analytics_inmutable"),
        ("control_analytics", "tr_analytics_inmutable"),
        ("ejecucion_analytics", "tr_analytics_terminada"),
    )
    with sesion() as s:
        for tabla_, trigger in disparadores:
            s.execute(text(f"ALTER TABLE {tabla_} DISABLE TRIGGER {trigger}"))
        for sentencia in (
            "DELETE FROM agregado_analytics WHERE ejecucion_analytics_id = :e",
            "DELETE FROM control_analytics WHERE ejecucion_analytics_id = :e",
            "DELETE FROM entrada_analytics WHERE ejecucion_analytics_id = :e",
            "DELETE FROM trabajo_orquestacion WHERE ejecucion_analytics_id = :e",
            "DELETE FROM ejecucion_analytics WHERE id = :e",
        ):
            s.execute(text(sentencia), {"e": ejecucion_id})
        for tabla_, trigger in disparadores:
            s.execute(text(f"ALTER TABLE {tabla_} ENABLE TRIGGER {trigger}"))
        s.commit()


def dinero(texto: str) -> Decimal:
    return Decimal(texto)
