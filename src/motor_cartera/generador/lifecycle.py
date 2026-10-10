"""Eventos operacionales sinteticos de un escenario: lo que la cobranza hizo con las cuentas de cada
periodo, como lineas JSONL que importa `motor-cartera cargar-lifecycle`.

Por cada periodo (de un corte al siguiente), con las cuentas del corte que lo abre:

- gestiones al azar en una fraccion de las cuentas: mensajes digitales de una via, llamadas sin
  contacto, contactos con el titular o con un tercero, rechazos, promesas, convenios y visitas de
  campo (sin GPS, rutas ni zonas);
- gestiones con contacto antes de algunos pagos del periodo, a veces dos, para que haya pagos con
  una sola gestion candidata y pagos ambiguos que atribuir;
- de cada gestion con resultado PROMESA, su promesa; de cada una con CONVENIO, su convenio, con sus
  cuotas declaradas una por una (o sin calendario). Algunas promesas y convenios se cancelan, y
  algunas gestiones se anulan: se registraron por error;
- eventos tardios: una fraccion de los grupos de un periodo (una gestion con lo que nacio de ella)
  llega en el archivo del periodo siguiente, y las gestiones previas a un pago pueden haber ocurrido
  antes de que empezara su periodo.

Todo sale de la semilla, con su propio generador de numeros por periodo: agregar el lifecycle a un
escenario no cambia un byte de sus cortes ni de sus pagos, y el mismo escenario da los mismos
archivos. Los instantes llevan la zona de la fuente (UTC-6, sin horario de verano), como las horas
de pagos/v1. Los textos son sinteticos y sin datos personales, y un actor es una referencia opaca
(AGT-0042), nunca un nombre ni el Gestor de pagos/v1.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from motor_cartera.generador.oficial import (
    EstadoCartera,
    Movimientos,
    estado_inicial,
    evolucionar,
    movimientos_del_periodo,
)

ZONA = "-06:00"
"""El desplazamiento de la hora local de la fuente: America/Mexico_City no cambia de horario desde
2022."""

FRACCION_GESTIONADA = 0.30
"""Que fraccion de las cuentas recibe gestiones al azar en un periodo."""
P_ANTES_DEL_PAGO = 0.45
"""La probabilidad de que un pago tenga una gestion con contacto antes."""
P_SEGUNDA = 0.30
"""De esos, la probabilidad de que tenga dos: un pago ambiguo para atribucion/v1."""
P_TARDIO = 0.01
"""Que fraccion de los grupos de un periodo llega en el archivo del siguiente."""
P_ANULADA = 0.015
P_PROMESA_CANCELADA = 0.08
P_CONVENIO_CANCELADO = 0.10
P_OBSERVACION = 0.10

PLANTILLAS: tuple[tuple[str, str | None, str, str, str | None, float], ...] = (
    # canal, medio, nivel de contacto, resultado, resultado de la visita, peso
    ("DIGITAL", "SMS", "NO_APLICA", "SIN_RESPUESTA", None, 0.12),
    ("DIGITAL", "WHATSAPP", "NO_APLICA", "SIN_RESPUESTA", None, 0.08),
    ("DIGITAL", "EMAIL", "NO_APLICA", "SIN_RESPUESTA", None, 0.04),
    ("DIGITAL", "WHATSAPP", "CONTACTO_TITULAR", "CONTACTO", None, 0.04),
    ("DIGITAL", "WHATSAPP", "CONTACTO_TITULAR", "PROMESA", None, 0.02),
    ("TELEFONICA", "LLAMADA", "SIN_CONTACTO", "SIN_RESPUESTA", None, 0.25),
    ("TELEFONICA", "LLAMADA", "CONTACTO_TERCERO", "CONTACTO", None, 0.08),
    ("TELEFONICA", "LLAMADA", "CONTACTO_TITULAR", "CONTACTO", None, 0.08),
    ("TELEFONICA", "LLAMADA", "CONTACTO_TITULAR", "RECHAZO", None, 0.03),
    ("TELEFONICA", "LLAMADA", "CONTACTO_TITULAR", "PROMESA", None, 0.09),
    ("TELEFONICA", "LLAMADA", "CONTACTO_TITULAR", "CONVENIO", None, 0.02),
    ("CAMPO", None, "SIN_CONTACTO", "VISITA_REALIZADA", "NO_LOCALIZADO", 0.04),
    ("CAMPO", None, "SIN_CONTACTO", "VISITA_REALIZADA", "DOMICILIO_NO_VALIDO", 0.01),
    ("CAMPO", None, "CONTACTO_TERCERO", "VISITA_REALIZADA", "CONTACTO_TERCERO", 0.03),
    ("CAMPO", None, "CONTACTO_TITULAR", "VISITA_REALIZADA", "CONTACTO_TITULAR", 0.02),
    ("CAMPO", None, "CONTACTO_TITULAR", "PROMESA", "CONTACTO_TITULAR", 0.015),
    ("CAMPO", None, "CONTACTO_TITULAR", "CONVENIO", "CONTACTO_TITULAR", 0.005),
    ("OTRO", None, "NO_APLICA", "OTRO", None, 0.03),
)
"""Las gestiones al azar: cada una es coherente con lifecycle/v1."""

ANTES_DEL_PAGO: tuple[tuple[int, float], ...] = (
    (7, 0.35),  # una llamada con el titular, con contacto
    (9, 0.30),  # una llamada con el titular que termina en promesa
    (6, 0.15),  # una llamada en la que contesta un tercero
    (3, 0.10),  # un contacto por WhatsApp
    (14, 0.10),  # una visita con el titular
)
"""Las gestiones antes de un pago: siempre con contacto, por su indice en PLANTILLAS."""

OBSERVACIONES = (
    "Sin respuesta en el numero registrado.",
    "Pide que se le llame despues.",
    "Atiende un familiar y deja recado.",
    "Acepta revisar su estado de cuenta.",
    "Domicilio cerrado al momento de la visita.",
)
MOTIVOS = ("Se acordo otra fecha.", "El titular desistio.", "Se renegocio el acuerdo.")
MOTIVO_ANULACION = "Capturada por error."
CUOTAS = ((0, 0.30), (2, 0.25), (3, 0.30), (4, 0.15))
"""Cuantas cuotas declara un convenio: sin calendario, o de dos a cuatro."""


@dataclass(frozen=True)
class Grupo:
    """Una gestion y lo que nacio de ella, como lineas JSON: se escriben juntas."""

    lineas: tuple[str, ...]
    tardio: bool
    tipos: tuple[str, ...]


@dataclass
class ArchivoLifecycle:
    """Lo que se escribio de un periodo."""

    desde: date
    hasta: date
    archivo: str
    sha256: str
    eventos: int
    por_tipo: dict[str, int] = field(default_factory=dict)
    tardios: int = 0
    """Eventos de este archivo que ocurrieron en el periodo anterior y llegan aqui, tarde."""

    def manifiesto(self) -> dict:
        return {
            "desde": self.desde.isoformat(),
            "hasta": self.hasta.isoformat(),
            "archivo": self.archivo,
            "sha256": self.sha256,
            "eventos": self.eventos,
            "por_tipo": dict(sorted(self.por_tipo.items())),
            "tardios": self.tardios,
        }


def _instantes(segundos: np.ndarray) -> np.ndarray:
    """AAAA-MM-DDTHH:MM:SS-06:00."""
    return np.char.add(np.datetime_as_string(segundos.astype("datetime64[s]"), unit="s"), ZONA)


def _pesos(centavos: int) -> str:
    return f"{centavos // 100}.{centavos % 100:02d}"


def grupos_del_periodo(
    estado: EstadoCartera,
    movimientos: Movimientos,
    *,
    semilla: int,
    desde: date,
    hasta: date,
    intensidad: float = 1.0,
    con_tardios: bool = True,
) -> list[Grupo]:
    """Los grupos de eventos del periodo [desde, hasta], en orden de tiempo de negocio.
    `intensidad` escala cuantas gestiones hay; con `con_tardios` en False, ninguno llega tarde (el
    ultimo periodo no tiene un archivo siguiente)."""
    if hasta < desde:
        raise ValueError(f"El periodo termina antes de empezar: {desde} a {hasta}.")
    rng = np.random.default_rng([semilla, desde.toordinal(), hasta.toordinal(), 11])
    dias = (hasta - desde).days + 1
    inicio = np.datetime64(desde, "s")
    fin = np.datetime64(hasta + timedelta(1), "s") - np.timedelta64(1, "s")

    # Al azar, en una fraccion de las cuentas, entre las 7:00 y las 21:00.
    gestionadas = np.flatnonzero(
        rng.random(len(estado)) < min(1.0, FRACCION_GESTIONADA * intensidad)
    )
    cuenta_azar = np.repeat(gestionadas, 1 + np.minimum(rng.poisson(0.5, len(gestionadas)), 3))
    segundos = rng.integers(0, dias, len(cuenta_azar)) * 86_400 + rng.integers(
        7 * 3_600, 21 * 3_600, len(cuenta_azar)
    )
    ocurrido_azar = inicio + segundos.astype("timedelta64[s]")
    pesos = np.array([p[-1] for p in PLANTILLAS])
    plantilla_azar = rng.choice(len(PLANTILLAS), size=len(cuenta_azar), p=pesos / pesos.sum())

    # Antes de algunos pagos, con contacto: de una hora a nueve dias antes.
    pagos = np.flatnonzero(movimientos.recuperado_centavos > 0)
    con_gestion = pagos[rng.random(len(pagos)) < min(1.0, P_ANTES_DEL_PAGO * intensidad)]
    con_dos = con_gestion[rng.random(len(con_gestion)) < P_SEGUNDA]
    previas = np.concatenate([con_gestion, con_dos])
    cuenta_pago = movimientos.cuenta[previas]
    antelacion = rng.integers(3_600, 9 * 86_400, len(previas)).astype("timedelta64[s]")
    ocurrido_pago = movimientos.recepcion[previas].astype("datetime64[s]") - antelacion
    indices, pesos_pago = zip(*ANTES_DEL_PAGO, strict=True)
    plantilla_pago = np.array(indices)[
        rng.choice(len(indices), size=len(previas), p=np.array(pesos_pago))
    ]

    cuenta = np.concatenate([cuenta_azar, cuenta_pago])
    ocurrido = np.concatenate([ocurrido_azar, ocurrido_pago])
    plantilla = np.concatenate([plantilla_azar, plantilla_pago])
    orden = np.lexsort((cuenta, ocurrido))
    cuenta, ocurrido, plantilla = cuenta[orden], ocurrido[orden], plantilla[orden]
    m = len(cuenta)

    # Todo lo demas de cada gestion, sacado de una vez y en el mismo orden.
    actor = rng.integers(1, 121, m)
    observacion = np.where(
        rng.random(m) < P_OBSERVACION, rng.integers(0, len(OBSERVACIONES), m), -1
    )
    antes_visita = rng.integers(300, 2_400, m).astype("timedelta64[s]")
    despues_visita = rng.integers(300, 2_400, m).astype("timedelta64[s]")
    factor_promesa = rng.uniform(0.6, 2.4, m)
    plazo = rng.integers(2, 15, m)
    factor_convenio = rng.uniform(0.3, 0.9, m)
    cuantas, peso_cuotas = zip(*CUOTAS, strict=True)
    cuotas = np.array(cuantas)[rng.choice(len(cuantas), size=m, p=np.array(peso_cuotas))]
    cancelada = rng.random(m)
    retraso_cierre = rng.integers(3_600, 3 * 86_400, m).astype("timedelta64[s]")
    motivo = rng.integers(0, len(MOTIVOS), m)
    anulada = rng.random(m) < P_ANULADA
    retraso_anulacion = rng.integers(600, 2 * 86_400, m).astype("timedelta64[s]")
    tardio = (rng.random(m) < P_TARDIO) & con_tardios

    texto_ocurrido = _instantes(ocurrido)
    texto_cierre = _instantes(np.minimum(ocurrido + retraso_cierre, fin))
    texto_anulacion = _instantes(np.minimum(ocurrido + retraso_anulacion, fin))
    texto_inicio = _instantes(ocurrido - antes_visita)
    texto_fin = _instantes(ocurrido + despues_visita)
    dia_local = ocurrido.astype("datetime64[D]").astype(object)  # date de Python
    saldo = estado.saldo_centavos[cuenta]
    clientes = estado.cliente[cuenta]
    prefijo = f"lc{semilla}-{desde:%Y%m%d}"

    grupos = []
    for i in range(m):
        canal, medio, nivel, resultado, visita, _ = PLANTILLAS[plantilla[i]]
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
            gestion["observacion"] = OBSERVACIONES[observacion[i]]
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
                    "monto_prometido": _pesos(max(int(pago_normal * factor_promesa[i]), 5_000)),
                    "fecha_limite": (dia_local[i] + timedelta(int(plazo[i]))).isoformat(),
                    "actor_ref": actor_ref,
                }
            )
            if cancelada[i] < P_PROMESA_CANCELADA:
                eventos.append(_cierre("PROMESA_CANCELADA", f"{llave}-p", texto_cierre[i]))
                eventos[-1] |= {"motivo": MOTIVOS[motivo[i]], "actor_ref": actor_ref}
        elif resultado == "CONVENIO":
            eventos.append(
                _convenio(llave, int(saldo[i]), factor_convenio[i], int(cuotas[i]), dia_local[i])
            )
            eventos[-1]["actor_ref"] = actor_ref
            if cancelada[i] < P_CONVENIO_CANCELADO:
                eventos.append(_cierre("CONVENIO_CANCELADO", f"{llave}-c", texto_cierre[i]))
                eventos[-1] |= {"motivo": MOTIVOS[motivo[i]], "actor_ref": actor_ref}
        if anulada[i]:
            eventos.append(_cierre("GESTION_ANULADA", llave, texto_anulacion[i]))
            eventos[-1] |= {"motivo": MOTIVO_ANULACION, "actor_ref": actor_ref}
        grupos.append(
            Grupo(
                tuple(json.dumps(e, ensure_ascii=False, separators=(",", ":")) for e in eventos),
                bool(tardio[i]),
                tuple(e["tipo"] for e in eventos),
            )
        )
    return grupos


def _cierre(tipo: str, de: str, cuando) -> dict:
    campo = {"GESTION_ANULADA": "gestion", "PROMESA_CANCELADA": "promesa"}.get(tipo, "convenio")
    sufijo = {"GESTION_ANULADA": "-a", "PROMESA_CANCELADA": "x", "CONVENIO_CANCELADO": "x"}[tipo]
    return {
        "tipo": tipo,
        "idempotency_key": f"{de}{sufijo}",
        campo: de,
        "ocurrido_en": str(cuando),
    }


def _convenio(llave: str, saldo: int, factor: float, cuotas: int, dia: date) -> dict:
    """Un convenio por una parte del saldo, desde el dia siguiente a su gestion. Con calendario, sus
    cuotas se declaran una por una, cada 30 dias, y suman exactamente el total."""
    total = max(int(saldo * factor), 50_000)
    inicio = dia + timedelta(1)
    convenio: dict = {
        "tipo": "CONVENIO_CREADO",
        "idempotency_key": f"{llave}-c",
        "gestion": llave,
        "monto_total_acordado": _pesos(total),
        "fecha_inicio": inicio.isoformat(),
        "fecha_fin": (inicio + timedelta(30 * cuotas + 5 if cuotas else 90)).isoformat(),
    }
    if cuotas:
        base = total // cuotas
        montos = [base] * (cuotas - 1) + [total - base * (cuotas - 1)]
        convenio["cuotas"] = [
            {
                "fecha_vencimiento": (inicio + timedelta(30 * (n + 1))).isoformat(),
                "monto": _pesos(monto),
            }
            for n, monto in enumerate(montos)
        ]
    return convenio


def escribir(ruta: Path, grupos: list[Grupo]) -> tuple[str, int, Counter]:
    """Escribe los grupos como JSONL comprimido, igual byte por byte con los mismos grupos (sin
    nombre ni fecha en la cabecera gzip). Devuelve su SHA-256, cuantos eventos y cuantos de cada
    tipo."""
    eventos, tipos = 0, Counter()
    with (
        ruta.open("wb") as crudo,
        gzip.GzipFile(filename="", mode="wb", fileobj=crudo, mtime=0) as gz,
    ):
        for grupo in grupos:
            for linea in grupo.lineas:
                gz.write(linea.encode("utf-8") + b"\n")
            eventos += len(grupo.lineas)
            tipos.update(grupo.tipos)
    return _sha256(ruta), eventos, tipos


def _sha256(ruta: Path) -> str:
    digesto = hashlib.sha256()
    with ruta.open("rb") as archivo:
        while bloque := archivo.read(1 << 20):
            digesto.update(bloque)
    return digesto.hexdigest()


class Periodos:
    """Reparte los grupos de cada periodo entre su archivo y el del siguiente (los tardios)."""

    def __init__(self, destino: Path) -> None:
        self.destino = destino
        self.pendientes: list[Grupo] = []
        self.archivos: list[ArchivoLifecycle] = []

    def agregar(self, desde: date, hasta: date, grupos: list[Grupo]) -> ArchivoLifecycle:
        a_tiempo = [g for g in grupos if not g.tardio]
        llegan = self.pendientes + a_tiempo
        tardios = sum(len(g.lineas) for g in self.pendientes)
        self.pendientes = [g for g in grupos if g.tardio]
        nombre = f"lifecycle_{desde.isoformat()}_{hasta.isoformat()}.jsonl.gz"
        sha256, eventos, tipos = escribir(self.destino / nombre, llegan)
        archivo = ArchivoLifecycle(desde, hasta, nombre, sha256, eventos, dict(tipos), tardios)
        self.archivos.append(archivo)
        return archivo


def escribir_lifecycle_del_escenario(
    destino: str | Path,
    *,
    cuentas: int,
    cortes: int,
    primer_corte: date,
    semilla: int,
    dias_entre_cortes: int = 7,
    tasa_altas: float = 0.02,
    tasa_retiros: float = 0.01,
    intensidad: float = 1.0,
) -> list[ArchivoLifecycle]:
    """Solo el lifecycle de un escenario: recorre la evolucion de sus cortes y sus pagos con los
    mismos parametros que `generar_escenario`, sin escribir sus fuentes, y escribe un archivo por
    periodo. Sirve para agregar el lifecycle a un escenario ya cargado, como el del benchmark: son
    exactamente los archivos que `generar_escenario` escribiria con `lifecycle=True`."""
    directorio = Path(destino)
    directorio.mkdir(parents=True, exist_ok=True)
    periodos = Periodos(directorio)
    usados: set[int] = set()
    estado = estado_inicial(cuentas, semilla=semilla, fecha_corte=primer_corte, usados=usados)
    corte = primer_corte
    for numero in range(cortes - 1):
        siguiente = corte + timedelta(days=dias_entre_cortes)
        desde = corte + timedelta(days=1)
        movimientos = movimientos_del_periodo(estado, semilla=semilla, desde=desde, hasta=siguiente)
        periodos.agregar(
            desde,
            siguiente,
            grupos_del_periodo(
                estado,
                movimientos,
                semilla=semilla,
                desde=desde,
                hasta=siguiente,
                intensidad=intensidad,
                con_tardios=numero < cortes - 2,
            ),
        )
        estado, _ = evolucionar(
            estado,
            movimientos,
            semilla=semilla,
            corte=corte,
            siguiente=siguiente,
            usados=usados,
            tasa_altas=tasa_altas,
            tasa_retiros=tasa_retiros,
        )
        corte = siguiente
    return periodos.archivos
