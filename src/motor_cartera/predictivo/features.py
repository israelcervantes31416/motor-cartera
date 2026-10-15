"""predictive-features/public-v1: las features exactas de los modelos, y su forma canonica.

Una feature es un valor de una cuenta de un corte, calculado al cierre de `feature_as_of` (T) con
lo que se conocia en `feature_known_at` (K): un hecho existe si ocurrio hasta el final de T y se
registro hasta K. Nada posterior entra: ni un pago, ni una gestion, ni una promesa, ni una
evaluacion, ni un reverso, ni un corte, ni lo que decidio la operacion despues. La lista es cerrada
y explicita, en un orden canonico: no se seleccionan columnas automaticamente ni se usan "todas las
disponibles". Cambiar cualquier cosa de una feature (su fuente, su ventana, su transformacion o lo
que significa su NULL) cambia la version y su hash.

Lo que no esta, a proposito (decisiones 197 en adelante de docs/decisiones.md):

- identificadores: cuenta_id, CLIENTE_UNICO, despacho, cartera, source_row, source_sheet: solo
  identifican filas, y permitirian memorizar clientes;
- datos personales y sensibles: nombre, direccion, telefono, aval, referencias, CURP, RFC, genero,
  edad, y cualquier hash de ellos, que sigue siendo identidad;
- geografia fina: coordenadas, municipio, tramo y microtramo de campo, distancias. Solo la entidad;
- lo que decidio o planeo la operacion: prioridad y canal de decision/v2, base, gestor, estado de
  campo, orden, ETA y estado de ruteo, y estrategia, canal, estatus_plan y estatus_promesa_pago del
  corte, que traen politica de cobranza o semantica ambigua. v0.16 optimizara con las predicciones:
  si el modelo copiara la politica actual, no habria nada que optimizar;
- semanas_atraso, que en cartera/v2 es una funcion de dias_atraso, y las features de calendario: los
  cortes son semanales y la historia de entrenamiento no cubre un ano, asi que el mes o la semana
  ISO solo permitirian extrapolar una tendencia fuera del rango visto.

NULL no es cero. Un conteo del lifecycle es 0 si no se conoce ningun evento (el lifecycle es el
registro de Motor Cartera). Una feature de pagos es NULL si su ventana no tenia cobertura de pagos a
K, y una distancia en dias, si no hay evento del cual medirla. Las numericas nulas se imputan con la
mediana de TRAIN, con un indicador `<nombre>__nulo` cuando la ausencia tiene semantica; las
categoricas nulas son `__MISSING__`, y una categoria que TRAIN no vio, `__UNKNOWN__`.

Este modulo no sabe de la base.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from motor_cartera.analytics.catalogo import canonico

VERSION_FEATURES = "predictive-features/public-v1"

NULO = "~"
"""Como se escribe un NULL en la forma canonica."""
FALTANTE = "__MISSING__"
DESCONOCIDA = "__UNKNOWN__"
SUFIJO_NULO = "__nulo"


class Familia(StrEnum):
    SNAPSHOT = "SNAPSHOT"  # el snapshot de la cuenta en el corte
    HISTORIA = "HISTORIA"  # sus snapshots en los cortes conocidos a K hasta T
    PAGOS = "PAGOS"  # los movimientos de motor-pagos/v1 vigentes a K
    LIFECYCLE = "LIFECYCLE"  # las gestiones del lifecycle
    PROMESAS = "PROMESAS"  # las promesas y su evaluacion conocida a K


FAMILIAS_PERMITIDAS = frozenset(Familia)
"""Ninguna familia lee decision/v2, campo/v2, ruteo/v2, territorial ni la geografia fina."""


class Clase(StrEnum):
    NUMERICA = "NUMERICA"
    CATEGORICA = "CATEGORICA"


class Tipo(StrEnum):
    """Como se guarda y como se escribe en forma canonica (lo que PostgreSQL escribe con ::text)."""

    IMPORTE = "IMPORTE"  # numeric con dos decimales
    ENTERO = "ENTERO"  # bigint
    DECIMAL = "DECIMAL"  # numeric con seis decimales: una pendiente
    TEXTO = "TEXTO"


ESCALA: dict[Tipo, Decimal] = {Tipo.IMPORTE: Decimal("0.01"), Tipo.DECIMAL: Decimal("0.000001")}


class Transformacion(StrEnum):
    NINGUNA = "NINGUNA"
    LOG1P = "LOG1P"
    """log1p(max(x, 0)): montos y conteos no negativos con cola larga."""
    SIGNED_LOG1P = "SIGNED_LOG1P"
    """sign(x) * log1p(|x|): montos con signo (deltas, recuperacion neta, pendientes)."""


@dataclass(frozen=True)
class Feature:
    nombre: str
    familia: Familia
    clase: Clase
    tipo: Tipo
    transformacion: Transformacion
    ventana: str
    fuente: str
    nula: bool
    """Si puede ser NULL."""
    indicador_nulo: bool
    """Si lleva su propia columna `<nombre>__nulo`. Las que comparten el motivo de su NULL con otra
    de su familia no repiten el indicador: lo lleva la primera."""
    nulo: str
    """Que significa NULL."""
    descripcion: str

    @property
    def columna_indicador(self) -> str:
        return f"{self.nombre}{SUFIJO_NULO}"


N, C = Clase.NUMERICA, Clase.CATEGORICA
IMP, ENT, DEC, TXT = Tipo.IMPORTE, Tipo.ENTERO, Tipo.DECIMAL, Tipo.TEXTO
SIN, LOG, SLOG = Transformacion.NINGUNA, Transformacion.LOG1P, Transformacion.SIGNED_LOG1P
S, H, P, L, PR = (
    Familia.SNAPSHOT,
    Familia.HISTORIA,
    Familia.PAGOS,
    Familia.LIFECYCLE,
    Familia.PROMESAS,
)

AL_CORTE = "al corte (T)"
NUNCA = "nunca"
SIN_DATO = "el corte no trae el dato"
SIN_ANTERIOR = "la cuenta no tiene un snapshot anterior conocido a K"
SIN_4_SEMANAS = "la cuenta no tiene un snapshot conocido a K de 28 dias o mas antes de T"
SIN_TENDENCIA = "la cuenta tiene menos de dos snapshots conocidos en [T-28, T]"
PAGOS_30 = "la ventana de 30 dias no tenia cobertura de pagos a K"
PAGOS_90 = "la ventana de 90 dias no tenia cobertura de pagos a K"
SIN_EVALUACION = (
    "no habia una evaluacion de promesas EXITOSA conocida a K con as_of hasta T en la zona"
)


def _f(
    nombre: str,
    familia: Familia,
    clase: Clase,
    tipo: Tipo,
    transformacion: Transformacion,
    ventana: str,
    fuente: str,
    descripcion: str,
    *,
    nulo: str = NUNCA,
    indicador: bool = False,
) -> Feature:
    return Feature(
        nombre,
        familia,
        clase,
        tipo,
        transformacion,
        ventana,
        fuente,
        nulo != NUNCA,
        indicador,
        nulo,
        descripcion,
    )


FEATURES: tuple[Feature, ...] = (
    # El snapshot del corte.
    _f("saldo_total", S, N, IMP, LOG, AL_CORTE, "snapshot_cuenta.saldo_total", "Saldo total."),
    _f("saldo", S, N, IMP, LOG, AL_CORTE, "snapshot_cuenta.saldo", "Saldo.", nulo=SIN_DATO,
       indicador=True),
    _f("saldo_atrasado", S, N, IMP, LOG, AL_CORTE, "snapshot_cuenta.saldo_atrasado",
       "Saldo atrasado.", nulo=SIN_DATO, indicador=True),
    _f("saldo_requerido", S, N, IMP, LOG, AL_CORTE, "snapshot_cuenta.saldo_requerido",
       "Saldo requerido.", nulo=SIN_DATO, indicador=True),
    _f("moratorios", S, N, IMP, LOG, AL_CORTE, "snapshot_cuenta.moratorios", "Moratorios.",
       nulo=SIN_DATO, indicador=True),
    _f("pago_normal", S, N, IMP, LOG, AL_CORTE, "snapshot_cuenta.pago_normal", "Pago normal.",
       nulo=SIN_DATO, indicador=True),
    _f("dias_atraso", S, N, ENT, SIN, AL_CORTE, "snapshot_cuenta.dias_atraso", "Dias de atraso."),
    _f("atraso_maximo", S, N, ENT, SIN, AL_CORTE, "snapshot_cuenta.atraso_maximo",
       "Atraso maximo, en dias.", nulo=SIN_DATO, indicador=True),
    _f("producto", S, C, TXT, SIN, AL_CORTE, "snapshot_cuenta.producto", "Producto."),
    _f("cve_entidad", S, C, TXT, SIN, AL_CORTE, "snapshot_cuenta.cve_entidad",
       "Clave INEGI de la entidad (2 digitos): la unica geografia del modelo."),
    _f("tramo_atraso", S, C, TXT, SIN, AL_CORTE, "snapshot_cuenta.dias_atraso",
       "El tramo de dias_atraso con las fronteras canonicas de atraso.TRAMOS_ATRASO (0, 1-30, "
       "31-60, 61-90, 91+)."),
    # La historia de la cuenta: sus snapshots de los cortes conocidos a K, hasta T.
    _f("numero_cortes_observados", H, N, ENT, SIN, "cortes hasta T conocidos a K",
       "snapshot_cuenta", "Cuantos cortes conocidos a K, hasta T, observan a la cuenta."),
    _f("delta_saldo_1_corte", H, N, IMP, SLOG, "el snapshot anterior observado",
       "snapshot_cuenta.saldo_total",
       "saldo_total en T menos el de su snapshot anterior observado.", nulo=SIN_ANTERIOR,
       indicador=True),
    _f("delta_saldo_4_semanas", H, N, IMP, SLOG, "el snapshot mas reciente con fecha <= T-28",
       "snapshot_cuenta.saldo_total",
       "saldo_total en T menos el de su snapshot mas reciente de 28 dias o mas antes.",
       nulo=SIN_4_SEMANAS, indicador=True),
    _f("delta_dias_atraso_1_corte", H, N, ENT, SIN, "el snapshot anterior observado",
       "snapshot_cuenta.dias_atraso", "dias_atraso en T menos el de su snapshot anterior.",
       nulo=SIN_ANTERIOR),
    _f("delta_dias_atraso_4_semanas", H, N, ENT, SIN,
       "el snapshot mas reciente con fecha <= T-28", "snapshot_cuenta.dias_atraso",
       "dias_atraso en T menos el de su snapshot de 28 dias o mas antes.", nulo=SIN_4_SEMANAS),
    _f("tendencia_saldo", H, N, DEC, SLOG, "snapshots con fecha en [T-28, T]",
       "snapshot_cuenta.saldo_total",
       "Pendiente de minimos cuadrados de saldo_total contra los dias, en pesos por dia.",
       nulo=SIN_TENDENCIA, indicador=True),
    _f("tendencia_atraso", H, N, DEC, SIN, "snapshots con fecha en [T-28, T]",
       "snapshot_cuenta.dias_atraso",
       "Pendiente de minimos cuadrados de dias_atraso contra los dias.", nulo=SIN_TENDENCIA),
    # Los pagos: los movimientos canonicos de la interpretacion de cada mes vigente a K.
    _f("recuperacion_neta_30d_pasados", P, N, IMP, SLOG, "[T-29, T]",
       "movimiento_economico_canonico",
       "PAGO no anulados mas POSIBLE_REVERSO recibidos en la ventana; un PAGO esta anulado si su "
       "REVERSO se recibio hasta el final de T.", nulo=PAGOS_30, indicador=True),
    _f("recuperacion_neta_90d_pasados", P, N, IMP, SLOG, "[T-89, T]",
       "movimiento_economico_canonico", "La misma neta, en 90 dias.", nulo=PAGOS_90,
       indicador=True),
    _f("movimientos_30d", P, N, ENT, LOG, "[T-29, T]", "movimiento_economico_canonico",
       "Movimientos canonicos (PAGO, REVERSO y POSIBLE_REVERSO) recibidos en la ventana.",
       nulo=PAGOS_30),
    _f("movimientos_90d", P, N, ENT, LOG, "[T-89, T]", "movimiento_economico_canonico",
       "Movimientos canonicos recibidos en 90 dias.", nulo=PAGOS_90),
    _f("pagos_positivos_30d", P, N, ENT, LOG, "[T-29, T]", "movimiento_economico_canonico",
       "PAGO no anulados recibidos en la ventana.", nulo=PAGOS_30),
    _f("pagos_positivos_90d", P, N, ENT, LOG, "[T-89, T]", "movimiento_economico_canonico",
       "PAGO no anulados recibidos en 90 dias.", nulo=PAGOS_90),
    _f("reversos_90d", P, N, ENT, LOG, "[T-89, T]", "movimiento_economico_canonico",
       "REVERSO y POSIBLE_REVERSO recibidos en 90 dias.", nulo=PAGOS_90),
    _f("dias_desde_ultimo_movimiento", P, N, ENT, LOG, "toda la historia hasta T",
       "movimiento_economico_canonico",
       "T menos el dia del ultimo movimiento canonico recibido hasta T.",
       nulo="no hay movimiento conocido, o la ventana de 90 dias no tenia cobertura de pagos",
       indicador=True),
    _f("dias_desde_ultima_recuperacion_positiva", P, N, ENT, LOG, "toda la historia hasta T",
       "movimiento_economico_canonico", "T menos el dia del ultimo PAGO no anulado.",
       nulo="no hay PAGO no anulado conocido, o la ventana de 90 dias no tenia cobertura de pagos",
       indicador=True),
    # El lifecycle: gestiones ocurridas hasta el final de T, registradas hasta K, sin una anulacion
    # registrada hasta K.
    _f("gestiones_30d", L, N, ENT, LOG, "[T-29, T]", "gestion_cobranza",
       "Gestiones efectivas en la ventana."),
    _f("gestiones_90d", L, N, ENT, LOG, "[T-89, T]", "gestion_cobranza",
       "Gestiones efectivas en 90 dias."),
    _f("contactos_30d", L, N, ENT, LOG, "[T-29, T]", "gestion_cobranza",
       "Gestiones con CONTACTO_TITULAR o CONTACTO_TERCERO en la ventana."),
    _f("contactos_90d", L, N, ENT, LOG, "[T-89, T]", "gestion_cobranza",
       "Gestiones con contacto en 90 dias."),
    _f("gestiones_sin_contacto_30d", L, N, ENT, LOG, "[T-29, T]", "gestion_cobranza",
       "Gestiones SIN_CONTACTO en la ventana (un mensaje de una via, NO_APLICA, no cuenta)."),
    _f("dias_desde_ultima_gestion", L, N, ENT, LOG, "toda la historia hasta T",
       "gestion_cobranza", "T menos el dia de la ultima gestion efectiva.",
       nulo="no hay gestion conocida", indicador=True),
    _f("dias_desde_ultimo_contacto", L, N, ENT, LOG, "toda la historia hasta T",
       "gestion_cobranza", "T menos el dia de la ultima gestion con contacto.",
       nulo="no hay contacto conocido", indicador=True),
    # Las promesas y su evaluacion conocida a K.
    _f("promesas_creadas_90d", PR, N, ENT, LOG, "[T-89, T]", "promesa_pago",
       "Promesas creadas en 90 dias por una gestion no anulada a K."),
    _f("promesas_cumplidas_historicas", PR, N, ENT, LOG, "la evaluacion conocida a K",
       "evaluacion_promesa", "Promesas CUMPLIDA en la evaluacion de promesas conocida.",
       nulo=SIN_EVALUACION, indicador=True),
    _f("promesas_parciales_historicas", PR, N, ENT, LOG, "la evaluacion conocida a K",
       "evaluacion_promesa", "Promesas PARCIAL en esa evaluacion.", nulo=SIN_EVALUACION),
    _f("promesas_incumplidas_historicas", PR, N, ENT, LOG, "la evaluacion conocida a K",
       "evaluacion_promesa", "Promesas INCUMPLIDA en esa evaluacion.", nulo=SIN_EVALUACION),
    _f("promesas_pendientes_actuales", PR, N, ENT, LOG, "la evaluacion conocida a K",
       "evaluacion_promesa", "Promesas PENDIENTE en esa evaluacion.", nulo=SIN_EVALUACION),
    _f("dias_desde_ultima_promesa", PR, N, ENT, LOG, "toda la historia hasta T", "promesa_pago",
       "T menos el dia de la ultima promesa creada por una gestion no anulada.",
       nulo="no hay promesa conocida", indicador=True),
)  # fmt: skip
"""Las features de predictive-features/public-v1 en su orden canonico: el de su hash, el de la
matriz de diseno, el de la API y el del diccionario de docs/features_predictivas.md."""

POR_NOMBRE: dict[str, Feature] = {f.nombre: f for f in FEATURES}
NUMERICAS = tuple(f for f in FEATURES if f.clase == Clase.NUMERICA)
CATEGORICAS = tuple(f for f in FEATURES if f.clase == Clase.CATEGORICA)
CON_INDICADOR = tuple(f for f in NUMERICAS if f.indicador_nulo)


# --- lo prohibido ---------------------------------------------------------------------------------

PROHIBIDAS = (
    r"cuenta_id",
    r"cuenta_canonica_id",
    r"cliente_unico",
    r"despacho(_id)?",
    r"cartera_id",
    r"source_row",
    r"source_sheet",
    r"actor_ref",
    r"(lat|latitud|lon|longitud|punto|coordenadas?)",
    r"cve_municipio|municipio",
    r"(tramo|microtramo|zona|subzona)_id|microtramo",
    r"nombre.*|direccion.*|domicilio.*|telefono.*|tel_.*|aval.*|referencias?.*|correo.*|email",
    r"curp|rfc|genero|sexo|edad.*|religion|salud|etnia|clave_spei",
    r"prioridad.*|canal_recomendado|score_nivel.*",
    r"base(_operativa)?(_id)?|gestor.*|estado_campo|orden.*|eta.*|estado_ruteo|parada.*",
    r"estrategia|canal|estatus_plan|estatus_promesa_pago",
    r"semanas_atraso",
    r"mes_del_anio|dia_semana|semana_iso",
)
"""Nombres que una feature de este modelo nunca puede tener: identificadores, datos personales o
sensibles, geografia fina, lo que decidio o planeo la operacion, y lo excluido por las decisiones
de diseno. Una prueba falla si una feature cae en uno."""

_PROHIBIDA = re.compile(r"^(" + "|".join(PROHIBIDAS) + r")$")


class EsquemaInvalido(ValueError):
    """Un esquema de features con algo que no puede tener."""


def problemas_del_esquema(features: tuple[Feature, ...] = FEATURES) -> list[str]:
    """Lo que hace inaceptable un esquema, una frase por problema; vacia si se acepta: un nombre
    prohibido, una fuente que lee una tabla prohibida, una familia fuera de las permitidas, nombres
    repetidos, una categorica con transformacion, o un indicador en una feature que no es nula."""
    problemas = []
    vistos: set[str] = set()
    for f in features:
        if _PROHIBIDA.match(f.nombre):
            problemas.append(f"{f.nombre}: el nombre esta prohibido en un modelo predictivo.")
        tabla, _, columna = f.fuente.partition(".")
        if columna and _PROHIBIDA.match(columna):
            problemas.append(f"{f.nombre}: lee {f.fuente}, una columna prohibida.")
        if tabla in TABLAS_PROHIBIDAS:
            problemas.append(f"{f.nombre}: lee {tabla}, que es de la operacion, no un hecho.")
        if f.familia not in FAMILIAS_PERMITIDAS:
            problemas.append(f"{f.nombre}: su familia {f.familia} no esta permitida.")
        if f.nombre in vistos:
            problemas.append(f"{f.nombre}: esta dos veces.")
        vistos.add(f.nombre)
        if f.clase == Clase.CATEGORICA and f.transformacion != Transformacion.NINGUNA:
            problemas.append(f"{f.nombre}: una categorica no se transforma.")
        if f.indicador_nulo and not f.nula:
            problemas.append(f"{f.nombre}: tiene indicador de nulo y nunca es NULL.")
    return problemas


TABLAS_PROHIBIDAS = frozenset(
    {
        "resultado_decision_v2",
        "feature_decision_cuenta",
        "decision_cuenta",
        "asignacion_campo_cuenta",
        "carga_gestor_campo",
        "unidad_territorial_campo",
        "parada_ruta_v2",
        "ruta_gestor_v2",
        "asignacion_territorial_v2",
        "resultado_geografia_cuenta",
        "agregado_analytics",
    }
)
"""Las tablas de lo que la operacion decidio o planeo, y los agregados de analytics: ninguna
feature las lee."""


def validar(features: tuple[Feature, ...] = FEATURES) -> None:
    problemas = problemas_del_esquema(features)
    if problemas:
        raise EsquemaInvalido(" ".join(problemas))


# --- la forma canonica ----------------------------------------------------------------------------


class ValorInvalido(ValueError):
    """Un valor que no es de su tipo, o un NULL donde la feature no admite uno."""


def texto(feature: Feature, valor: object) -> str:
    """El valor de una feature en forma canonica: un entero en decimal, un importe con dos
    decimales, una pendiente con seis, un texto con su largo delante (`s7:CONSUMO`) y NULL como
    `~`. Es exactamente lo que PostgreSQL escribe de la columna con ::text (sin cero negativo), para
    que el feature_hash de la base y el de Python coincidan."""
    if valor is None:
        if not feature.nula:
            raise ValorInvalido(f"{feature.nombre} no admite NULL.")
        return NULO
    if feature.tipo == Tipo.TEXTO:
        if not isinstance(valor, str):
            raise ValorInvalido(f"{feature.nombre} es texto: {valor!r}.")
        return f"s{len(valor)}:{valor}"
    if feature.tipo == Tipo.ENTERO:
        if isinstance(valor, bool) or not isinstance(valor, int):
            raise ValorInvalido(f"{feature.nombre} es entera: {valor!r}.")
        return str(valor)
    if not isinstance(valor, Decimal) or not valor.is_finite():
        raise ValorInvalido(f"{feature.nombre} es un Decimal finito: {valor!r}.")
    redondeado = valor.quantize(ESCALA[feature.tipo])
    if redondeado == 0:
        redondeado = abs(redondeado)
    return str(redondeado)


def forma_canonica(valores: Mapping[str, object]) -> str:
    """Las features de una cuenta en forma canonica: la version y una linea `nombre=valor` por
    feature, en el orden de FEATURES. Exige exactamente las de la version."""
    faltan = [f.nombre for f in FEATURES if f.nombre not in valores]
    sobran = sorted(set(valores) - set(POR_NOMBRE))
    if faltan or sobran:
        raise ValorInvalido(f"Faltan {faltan} y sobran {sobran}.")
    return "\n".join(
        [VERSION_FEATURES, *(f"{f.nombre}={texto(f, valores[f.nombre])}" for f in FEATURES)]
    )


def feature_hash(valores: Mapping[str, object]) -> bytes:
    """El SHA-256 de la forma canonica: lo que guarda cada prediccion y verifica su explicacion."""
    return hashlib.sha256(forma_canonica(valores).encode("utf-8")).digest()


# --- la definicion y su hash ----------------------------------------------------------------------


def definicion() -> dict:
    return {
        "version": VERSION_FEATURES,
        "relojes": "Una feature usa hechos ocurridos hasta el final de feature_as_of (T) y "
        "registrados hasta feature_known_at (K). Nada posterior.",
        "faltantes": {
            "numerica": "NULL se imputa con la mediana de TRAIN del valor transformado; si la "
            "ausencia tiene semantica, su indicador <nombre>__nulo vale 1.",
            "categorica": f"NULL es {FALTANTE}; una categoria que TRAIN no vio es {DESCONOCIDA}.",
        },
        "escalado": "Las numericas, transformadas e imputadas, se estandarizan con la media y la "
        "desviacion de TRAIN; una con desviacion cero en TRAIN es constante y su coeficiente es "
        "cero. Los indicadores y el one-hot quedan en 0/1.",
        "categoricas": "One-hot con el vocabulario de TRAIN en orden canonico (por codigo de "
        f"caracter), y despues {FALTANTE} y {DESCONOCIDA}. Sin hashing.",
        "forma_canonica": "La version y una linea nombre=valor por feature en su orden: enteros en "
        "decimal, importes con dos decimales, pendientes con seis, texto con su largo delante y "
        "NULL como ~. feature_hash es su SHA-256.",
        "prohibidas": list(PROHIBIDAS),
        "tablas_prohibidas": sorted(TABLAS_PROHIBIDAS),
        "features": [
            {
                "nombre": f.nombre,
                "familia": f.familia.value,
                "clase": f.clase.value,
                "tipo": f.tipo.value,
                "transformacion": f.transformacion.value,
                "ventana": f.ventana,
                "fuente": f.fuente,
                "nula": f.nula,
                "indicador_nulo": f.indicador_nulo,
                "nulo": f.nulo,
                "descripcion": f.descripcion,
            }
            for f in FEATURES
        ],
    }


def hash_features() -> str:
    """SHA-256 de la definicion canonica de las features."""
    return hashlib.sha256(canonico(definicion())).hexdigest()
