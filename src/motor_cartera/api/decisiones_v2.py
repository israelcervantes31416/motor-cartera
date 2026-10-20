"""decision/v2 por HTTP: pedirla para un corte, leer su ejecucion, sus resultados, la decision
explicada de una cuenta, las decisiones de una cuenta y las de un corte.

La API no decide: el POST fija las entradas (as_of y known_at, una sola vez), deja la ejecucion
EN_PROCESO y su trabajo DECISION_V2 en la cola durable, en una transaccion, y un worker la ejecuta.
Corre en paralelo a decision/v1: no cambia el flujo de una corrida ni sus rutas.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from sqlmodel import Session, select

from motor_cartera.api.dependencias import Sesion, SesionDeLectura
from motor_cartera.api.errores import ErrorDeApi, errores
from motor_cartera.api.esquemas import Paginacion
from motor_cartera.api.esquemas_decision_v2 import (
    Advertencia,
    CondicionRespuesta,
    ConteosDecisionV2,
    CorteDeLaDecision,
    DecisionV2DeCuentaRespuesta,
    DecisionV2EnHistoriaRespuesta,
    DecisionV2Entrada,
    EjecucionDecisionV2Respuesta,
    FeaturesRespuesta,
    PaginaDecisionesV2DeCuenta,
    PaginaEjecucionesDecisionV2,
    PaginaResultadosDecisionV2,
    ParametrosResultadosDecisionV2,
    ReglaRespuesta,
    ResultadoDecisionV2Respuesta,
    VerificacionRespuesta,
    VersionesDecisionV2,
)
from motor_cartera.api.gestiones import cuenta_de
from motor_cartera.api.operacional import SIN_CLAVE
from motor_cartera.config import Config
from motor_cartera.db.modelos import CorteCanonico, EjecucionDecisionV2, ModoDecisionV2
from motor_cartera.decision.reglas import Prioridad
from motor_cartera.decision_v2 import consultas
from motor_cartera.decision_v2 import ejecuciones as motor
from motor_cartera.decision_v2.consultas import (
    CuentaSinDecision,
    DecisionV2NoEncontrada,
    EjecucionVista,
)
from motor_cartera.decision_v2.features import feature_hash
from motor_cartera.decision_v2.politica import POLITICAS, decidir, decision_hash

router = APIRouter(tags=["decision-v2"])

NO_EXISTE = (
    404,
    "DECISION_V2_NO_ENCONTRADA",
    "No existe una decision v2 con ese decision_run_id.",
)
CUENTA_NO_EXISTE = (404, "CUENTA_NO_ENCONTRADA", "No existe una cuenta canonica con ese cuenta_id.")
CORTE_NO_EXISTE = (404, "CORTE_NO_ENCONTRADO", "No existe un corte canonico con ese corte_id.")


@router.post(
    "/decisiones-v2",
    status_code=201,
    response_model=EjecucionDecisionV2Respuesta,
    summary="Pide decidir las cuentas de un corte con decision/v2, a un as_of y un known_at",
    responses={
        201: {
            "description": "La decision quedo EN_PROCESO, con su trabajo en la cola durable: un "
            "worker la ejecuta. `Location` apunta a ella."
        },
        **errores(
            *SIN_CLAVE,
            CORTE_NO_EXISTE,
            (409, "DECISION_V2_EN_PROCESO", "El corte ya se esta decidiendo con esas versiones."),
            (
                409,
                "DECISION_V2_YA_GENERADA",
                "Esas mismas entradas (la misma firma) ya se decidieron.",
            ),
            (422, "AS_OF_DISTINTO_DEL_CORTE", "as_of no es el dia del corte."),
            (422, "KNOWN_AT_REQUERIDO", "Un BACKTEST declara su known_at."),
            (422, "KNOWN_AT_FUTURO", "known_at es posterior al reloj de la base."),
            (422, "CORTE_NO_CONOCIDO", "El corte se materializo despues de known_at."),
            (
                422,
                "CORTE_NO_VIGENTE",
                "Un OPERATIVO sobre un corte que ya no es el mas reciente conocido a known_at.",
            ),
            (422, "POLITICA_NO_SOPORTADA", "Este servicio no aplica esa version de la politica."),
            (422, "ENTRADA_INVALIDA", "Falta corte_id, o algun campo no es valido."),
        ),
    },
)
def pedir_decision(
    entrada: DecisionV2Entrada, request: Request, response: Response, s: Sesion
) -> EjecucionDecisionV2Respuesta:
    """Decide, en un solo trabajo, cada cuenta del corte con `decision-policy/public-v1` sobre
    `features-decision/v1`, usando solo lo que se conocia en `known_at` de lo que paso hasta el
    final de `as_of`.

    **201 quiere decir que la decision se creo, no que termino.** Las mismas entradas (corte,
    as_of, known_at y versiones) son la misma decision: si ya se publico, `409
    DECISION_V2_YA_GENERADA` con la anterior. Sin `known_at`, es el reloj de la base al abrir, y
    queda guardado: un reintento usa ese mismo."""
    config: Config = request.app.state.config
    try:
        ejecucion = motor.abrir(
            s,
            entrada.corte_id,
            as_of=entrada.as_of,
            known_at=entrada.known_at,
            modo=entrada.modo,
            version_politica=entrada.version_politica,
            zona=config.zona_horaria_fuente,
            max_intentos=config.worker_max_intentos,
        )
    except motor.EntradaNoDecidible as exc:
        raise ErrorDeApi(exc.estado_http, exc.codigo, str(exc)) from exc
    except motor.DecisionV2EnProceso as exc:
        raise ErrorDeApi(409, "DECISION_V2_EN_PROCESO", str(exc)) from exc
    except motor.DecisionV2YaGenerada as exc:
        raise ErrorDeApi(409, "DECISION_V2_YA_GENERADA", str(exc)) from exc
    s.commit()
    decision_run_id = ejecucion.decision_run_id
    response.headers["Location"] = f"/decisiones-v2/{decision_run_id}"
    return ejecucion_respuesta(consultas.obtener(s, decision_run_id))


@router.get(
    "/decisiones-v2/{decision_run_id}",
    response_model=EjecucionDecisionV2Respuesta,
    summary="Una decision v2: sus entradas, sus versiones, su estado y lo que publico",
    responses=errores(
        *SIN_CLAVE, NO_EXISTE, (422, "ENTRADA_INVALIDA", "El decision_run_id no es un UUID.")
    ),
)
def obtener_decision(decision_run_id: UUID, s: SesionDeLectura) -> EjecucionDecisionV2Respuesta:
    """Mientras el estado sea `EN_PROCESO`, un worker no la ha terminado. `disponibilidad` dice que
    informacion de pagos y de atribucion habia a known_at, y `advertencias`, si la decision admite
    conocimiento posterior al dia que decide."""
    return ejecucion_respuesta(_vista(s, decision_run_id))


@router.get(
    "/decisiones-v2/{decision_run_id}/resultados",
    response_model=PaginaResultadosDecisionV2,
    summary="Lo que decidio de cada cuenta, en el orden de la cuenta",
    responses=errores(
        *SIN_CLAVE,
        NO_EXISTE,
        (422, "ENTRADA_INVALIDA", "Un filtro no es valido, o la paginacion no lo es."),
    ),
)
def resultados(
    decision_run_id: UUID,
    parametros: Annotated[ParametrosResultadosDecisionV2, Query()],
    s: SesionDeLectura,
) -> PaginaResultadosDecisionV2:
    """Con `prioridad_final` o `canal_recomendado`, solo las de ese valor, por su indice. Vacia
    mientras la decision sigue EN_PROCESO, o si fallo: no publica nada a medias."""
    vista = _vista(s, decision_run_id)
    total, pagina = consultas.resultados(
        s,
        vista.ejecucion,
        prioridad=None if parametros.prioridad_final is None else parametros.prioridad_final.value,
        canal=parametros.canal_recomendado,
        desplazamiento=parametros.desplazamiento,
        limite=parametros.por_pagina,
    )
    return PaginaResultadosDecisionV2(
        decision_run_id=decision_run_id,
        as_of=vista.ejecucion.as_of,
        known_at=vista.ejecucion.known_at,
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[
            ResultadoDecisionV2Respuesta(
                cuenta_id=v.cuenta_id,
                cliente_unico=v.cliente_unico,
                prioridad_base=Prioridad(v.resultado.prioridad_base),
                score_nivel_base=v.resultado.score_nivel_base,
                ajuste_total=v.resultado.ajuste_total,
                score_nivel_final=v.resultado.score_nivel_final,
                prioridad_final=Prioridad(v.resultado.prioridad_final),
                canal_recomendado=v.resultado.canal_recomendado,
                motivos=v.resultado.motivos,
                decision_hash=v.resultado.decision_hash.hex(),
            )
            for v in pagina
        ],
    )


@router.get(
    "/decisiones-v2/{decision_run_id}/resultados/{cuenta_id}",
    response_model=DecisionV2DeCuentaRespuesta,
    summary="La decision de una cuenta explicada: sus features, cada regla y su resultado",
    responses=errores(
        *SIN_CLAVE,
        NO_EXISTE,
        CUENTA_NO_EXISTE,
        (404, "CUENTA_SIN_DECISION", "La decision no decidio esa cuenta: no es de su corte."),
        (422, "ENTRADA_INVALIDA", "Un identificador no es un UUID."),
    ),
)
def decision_de_cuenta(
    decision_run_id: UUID, cuenta_id: UUID, s: SesionDeLectura
) -> DecisionV2DeCuentaRespuesta:
    """Las features que tenia la cuenta, tal como se publicaron (no se recalculan), cada regla de
    la politica con el valor que encontro y si aplico, la prioridad base y la final, el canal, los
    motivos guardados y los hashes. `verificacion` dice si la politica, aplicada a esas mismas
    features, da exactamente la decision publicada."""
    vista = _vista(s, decision_run_id)
    cuenta = cuenta_de(s, cuenta_id)
    try:
        decidida = consultas.de_cuenta(s, vista.ejecucion, cuenta)
    except CuentaSinDecision as exc:
        raise ErrorDeApi(
            404,
            "CUENTA_SIN_DECISION",
            f"La decision {decision_run_id} no decidio la cuenta {cuenta_id}: no es de su corte, "
            "o la decision no publico.",
        ) from exc
    e, r = vista.ejecucion, decidida.resultado
    valores = consultas.valores(decidida.features)
    politica = POLITICAS[e.version_politica]
    explicada = decidir(valores, politica)
    huella = feature_hash(valores)
    return DecisionV2DeCuentaRespuesta(
        decision_run_id=decision_run_id,
        as_of=e.as_of,
        known_at=e.known_at,
        versiones=_versiones(e),
        hash_politica=e.hash_politica,
        cuenta_id=cuenta.cuenta_id,
        cliente_unico=cuenta.cliente_unico,
        features=FeaturesRespuesta(**valores),
        feature_hash=decidida.features.feature_hash.hex(),
        prioridad_base=Prioridad(r.prioridad_base),
        score_nivel_base=r.score_nivel_base,
        reglas=[
            ReglaRespuesta(
                codigo=regla.codigo.value,
                activa=regla.activa,
                efecto=regla.efecto,
                condiciones=[
                    CondicionRespuesta(
                        feature=c.feature,
                        valor=c.valor,
                        operador=c.operador,
                        umbral=c.umbral,
                        cumple=c.cumple,
                    )
                    for c in regla.condiciones
                ],
            )
            for regla in explicada.reglas
        ],
        ajuste_total=r.ajuste_total,
        score_nivel_final=r.score_nivel_final,
        prioridad_final=Prioridad(r.prioridad_final),
        canal_recomendado=r.canal_recomendado,
        motivos=r.motivos,
        decision_hash=r.decision_hash.hex(),
        verificacion=VerificacionRespuesta(
            feature_hash=huella == decidida.features.feature_hash,
            decision_hash=decision_hash(
                decidida.features.feature_hash,
                explicada,
                version_features=e.version_features,
                politica=politica,
            )
            == r.decision_hash,
            motivos=explicada.motivos() == r.motivos,
        ),
    )


@router.get(
    "/cuentas/{cuenta_id}/decisiones-v2",
    response_model=PaginaDecisionesV2DeCuenta,
    summary="Las decisiones v2 de una cuenta, de la de as_of mas reciente a la mas antigua",
    responses=errores(
        *SIN_CLAVE,
        CUENTA_NO_EXISTE,
        (422, "ENTRADA_INVALIDA", "El cuenta_id no es un UUID, o la paginacion no es valida."),
    ),
)
def decisiones_de_cuenta(
    cuenta_id: UUID, parametros: Annotated[Paginacion, Query()], s: SesionDeLectura
) -> PaginaDecisionesV2DeCuenta:
    """Como cambio la recomendacion de la cuenta a traves de los cortes y de las fronteras de
    conocimiento: solo decisiones EXITOSA; en el mismo as_of, la de known_at mas reciente
    primero."""
    cuenta = cuenta_de(s, cuenta_id)
    total, pagina = consultas.historial(
        s, cuenta, desplazamiento=parametros.desplazamiento, limite=parametros.por_pagina
    )
    return PaginaDecisionesV2DeCuenta(
        cuenta_id=cuenta.cuenta_id,
        cliente_unico=cuenta.cliente_unico,
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[
            DecisionV2EnHistoriaRespuesta(
                decision_run_id=d.ejecucion.decision_run_id,
                corte_id=d.corte_id,
                as_of=d.ejecucion.as_of,
                known_at=d.ejecucion.known_at,
                modo=d.ejecucion.modo,
                prioridad_base=Prioridad(d.resultado.prioridad_base),
                ajuste_total=d.resultado.ajuste_total,
                prioridad_final=Prioridad(d.resultado.prioridad_final),
                canal_recomendado=d.resultado.canal_recomendado,
                motivos=[m["codigo"] for m in d.resultado.motivos],
                decision_hash=d.resultado.decision_hash.hex(),
            )
            for d in pagina
        ],
    )


@router.get(
    "/cartera/cortes/{corte_id}/decisiones-v2",
    response_model=PaginaEjecucionesDecisionV2,
    summary="Las decisiones v2 de un corte, en cualquier estado, la mas reciente primero",
    responses=errores(
        *SIN_CLAVE,
        CORTE_NO_EXISTE,
        (422, "ENTRADA_INVALIDA", "El corte_id no es un UUID, o la paginacion no es valida."),
    ),
)
def decisiones_del_corte(
    corte_id: UUID, parametros: Annotated[Paginacion, Query()], s: SesionDeLectura
) -> PaginaEjecucionesDecisionV2:
    """Cada decision v2 que se pidio sobre el corte: las EXITOSA, las que siguen EN_PROCESO y las
    FALLIDA, con sus entradas, su known_at y sus advertencias. Un mismo corte tiene varias si se
    decidio con distintas fronteras de conocimiento (OPERATIVO y BACKTEST), o si una fallo y se
    volvio a pedir."""
    corte = _corte(s, corte_id)
    total, pagina = consultas.del_corte(
        s, corte, desplazamiento=parametros.desplazamiento, limite=parametros.por_pagina
    )
    return PaginaEjecucionesDecisionV2(
        corte_id=corte.corte_id,
        fecha_corte=corte.fecha_corte,
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[ejecucion_respuesta(v) for v in pagina],
    )


def _vista(s: Session, decision_run_id: UUID) -> EjecucionVista:
    try:
        return consultas.obtener(s, decision_run_id)
    except DecisionV2NoEncontrada as exc:
        raise ErrorDeApi(
            404,
            "DECISION_V2_NO_ENCONTRADA",
            f"No existe una decision v2 con decision_run_id {decision_run_id}.",
        ) from exc


def _corte(s: Session, corte_id: UUID) -> CorteCanonico:
    corte = s.exec(select(CorteCanonico).where(CorteCanonico.corte_id == corte_id)).first()
    if corte is None:
        raise ErrorDeApi(
            404, "CORTE_NO_ENCONTRADO", f"No existe un corte canonico con corte_id {corte_id}."
        )
    return corte


def _versiones(e: EjecucionDecisionV2) -> VersionesDecisionV2:
    return VersionesDecisionV2(
        motor=e.version_motor, features=e.version_features, politica=e.version_politica
    )


def advertencias(vista: EjecucionVista) -> list[Advertencia]:
    """Lo que quien lee la decision tiene que saber de su frontera de conocimiento y de la
    informacion que no estaba disponible."""
    e = vista.ejecucion
    avisos = []
    if e.modo == ModoDecisionV2.BACKTEST:
        avisos.append(
            Advertencia(
                codigo="BACKTEST",
                mensaje="Decide un corte pasado con el conocimiento que declara su known_at.",
            )
        )
    if e.known_at > vista.fin_del_dia:
        avisos.append(
            Advertencia(
                codigo="CONOCIMIENTO_POSTERIOR_AL_DIA",
                mensaje="known_at es posterior al final de as_of: la decision admite lo que se "
                "registro despues de ese dia sobre lo que ya habia pasado (eventos tardios). No es "
                "exactamente lo que se sabia ese dia.",
            )
        )
    if e.disponibilidad:
        for familia in ("pagos", "atribucion"):
            faltan = [v for v, ok in e.disponibilidad.get(familia, {}).items() if not ok]
            if faltan:
                avisos.append(
                    Advertencia(
                        codigo=f"{familia.upper()}_NO_DISPONIBLES",
                        mensaje=f"Sin informacion de {familia} completa a known_at en las ventanas "
                        f"{', '.join(faltan)}: sus features son null, no cero.",
                    )
                )
    return avisos


def ejecucion_respuesta(vista: EjecucionVista) -> EjecucionDecisionV2Respuesta:
    e = vista.ejecucion
    return EjecucionDecisionV2Respuesta(
        decision_run_id=e.decision_run_id,
        estado=e.estado,
        resultado=e.resultado,
        modo=e.modo,
        corte=CorteDeLaDecision(corte_id=vista.corte_id, fecha_corte=vista.fecha_corte),
        despacho_id=e.despacho_id,
        cartera_id=e.cartera_id,
        as_of=e.as_of,
        known_at=e.known_at,
        zona_horaria=e.zona_horaria,
        fin_del_dia=vista.fin_del_dia,
        versiones=_versiones(e),
        hash_politica=e.hash_politica,
        firma_inputs=e.firma_inputs,
        conteos=ConteosDecisionV2(
            cuentas_objetivo=e.cuentas_objetivo,
            cuentas_decididas=e.cuentas_decididas,
            cuentas_fallidas=e.cuentas_fallidas,
        ),
        disponibilidad=e.disponibilidad,
        distribucion=e.distribucion,
        advertencias=advertencias(vista),
        trabajo_id=vista.trabajo_id,
        iniciada_en=e.iniciada_en,
        terminada_en=e.terminada_en,
        detalle=e.detalle,
    )
