"""El dataset de un entrenamiento: una fila por cuenta del corte de cada ancla, con sus features a
feature_known_at y sus labels a label_known_at.

No es un feature store: se construye ancla por ancla, cada una en su propia transaccion de lectura,
corta, con el mismo SQL de un scoring (`construccion`), y se guarda en memoria de forma compacta:
las numericas en float64, las categoricas como codigos de un diccionario comun, la cuenta como un
codigo de su cuenta_id publico. Un label CENSURADA o NO_DISPONIBLE no existe: el SQL lo deja NULL y
aqui no se guarda; nunca se vuelve un cero.

El hash del dataset es el SHA-256, en un recorrido, de una cabecera con las versiones y, por cada
ancla en orden canonico (feature_as_of y corte_id), su hash y el CSV exacto de sus filas: el
cuenta_id, cada feature, el feature_hash y los labels, en el orden de cuenta_id (un UUID). No
nombra ningun id interno: las mismas anclas sobre los mismos hechos dan el mismo hash.
No se guarda el dataset, solo su hash: se puede reconstruir con el manifiesto.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import date, datetime
from uuid import UUID

import numpy as np
import pyarrow as pa
from sqlmodel import Session

from motor_cartera.predictivo import construccion, medicion, sql
from motor_cartera.predictivo import features as features_
from motor_cartera.predictivo import targets as targets_
from motor_cartera.predictivo.features import CATEGORICAS
from motor_cartera.predictivo.targets import Madurez, Target

VERSION_DATASET = "predictive-dataset/v1"

FEATURES_DE_LA_ANCLA = "DESPUES_FEATURES"
LABELS_DE_LA_ANCLA = "DESPUES_LABELS"

TIPOS_DATASET: dict[str, pa.DataType] = {
    "cuenta_id": pa.string(),
    **construccion.TIPOS_FEATURES,
    "feature_hash": pa.string(),
    "neta_30d": pa.float64(),
    "contacto_14d": pa.float64(),
    "promesa_30d": pa.float64(),
}


class DatosInconsistentes(Exception):
    """El dataset contradice lo que declara su manifiesto: un label maduro sin valor, una cuenta
    repetida o un ancla vacia."""


@dataclass(frozen=True)
class AnclaDataset:
    """Lo que el dataset necesita de un ancla registrada."""

    corte_canonico_id: int
    corte_id: UUID
    feature_as_of: date
    feature_known_at: datetime
    label_known_at: datetime
    pagos_completos_hasta: date | None
    lifecycle_completo_hasta: date | None
    split: str
    hash_ancla: str

    def madurez(self, target: targets_.DefinicionTarget) -> Madurez:
        return targets_.madurez(
            target,
            self.feature_as_of,
            pagos_completos_hasta=self.pagos_completos_hasta,
            lifecycle_completo_hasta=self.lifecycle_completo_hasta,
        )

    @property
    def orden(self) -> tuple[date, str]:
        return (self.feature_as_of, str(self.corte_id))


class Diccionario:
    """Codigos comunes para los textos de las categoricas, en el orden en que aparecen. Un codigo
    no tiene significado: el vocabulario del modelo sale de TRAIN, en orden canonico."""

    def __init__(self) -> None:
        self.textos: list[str] = []
        self._codigo: dict[str, int] = {}

    def codigo(self, texto: str) -> int:
        codigo = self._codigo.get(texto)
        if codigo is None:
            codigo = len(self.textos)
            self._codigo[texto] = codigo
            self.textos.append(texto)
        return codigo

    def buscar(self, texto: str) -> int | None:
        return self._codigo.get(texto)

    def codificar(self, indices: np.ndarray, valores: list[str]) -> np.ndarray:
        """Los codigos comunes de una columna codificada con su propio diccionario: -1 es NULL."""
        mapa = np.array([self.codigo(v) for v in valores] + [-1], dtype=np.int32)
        return mapa[indices]


@dataclass
class BloqueAncla:
    """Las filas de un ancla, en el orden canonico."""

    ancla: AnclaDataset
    numericas: np.ndarray
    """n x p, float64, NaN es NULL, en el orden de NUMERICAS."""
    categoricas: np.ndarray
    """n x q, int32: el codigo comun de cada valor; -1 es NULL."""
    cuenta: np.ndarray
    """int32: un codigo por cuenta_id, comun a todas las anclas (para el bootstrap por cuenta)."""
    labels: dict[Target, np.ndarray]
    """Solo los de los targets MADURA del ancla: 0/1 en una clasificacion, el monto en pesos."""
    madurez: dict[Target, Madurez]
    disponibilidad: dict
    segundos: float

    @property
    def filas(self) -> int:
        return len(self.cuenta)

    @property
    def split(self) -> str:
        return self.ancla.split


@dataclass
class Dataset:
    bloques: list[BloqueAncla]
    """Uno por ancla, en orden canonico. Los de las anclas que nada usa no guardan filas."""
    diccionario: Diccionario
    cuentas: int
    """Cuantos cuenta_id distintos hay en todas las anclas."""
    hash: str
    filas_por_split: dict[str, int] = field(default_factory=dict)
    censurados: int = 0
    """Filas con al menos un label CENSURADA o NO_DISPONIBLE."""
    conteos: dict = field(default_factory=dict)
    segundos: float = 0.0
    fases: dict[str, float] = field(default_factory=dict)
    """Los segundos de todas las anclas por paso: las features, los labels, el COPY y la
    conversion a Arrow y NumPy."""

    def de(self, *splits: str) -> list[BloqueAncla]:
        return [b for b in self.bloques if b.split in splits]


def _cabecera() -> bytes:
    lineas = [
        VERSION_DATASET,
        features_.VERSION_FEATURES,
        features_.hash_features(),
        targets_.VERSION_TARGETS,
        targets_.hash_targets(),
    ]
    return ("\n".join(lineas) + "\n").encode("utf-8")


def _labels(t: pa.Table, madurez: dict[Target, Madurez]) -> dict[Target, np.ndarray]:
    neta = construccion.flotantes(t.column("neta_30d"))
    crudos = {
        Target.RECUPERACION_POSITIVA_30D: (neta, lambda x: (x > 0).astype(np.float64)),
        Target.RECUPERACION_POSITIVA_MONTO_30D: (neta, lambda x: np.maximum(x, 0.0)),
        Target.CONTACTO_14D: (construccion.flotantes(t.column("contacto_14d")), lambda x: x),
        Target.PROMESA_30D: (construccion.flotantes(t.column("promesa_30d")), lambda x: x),
    }
    labels: dict[Target, np.ndarray] = {}
    for target, (crudo, regla) in crudos.items():
        if madurez[target] != Madurez.MADURA:
            if not np.all(np.isnan(crudo)):
                raise DatosInconsistentes(f"Un label {target} no maduro trae un valor.")
            continue
        if np.any(np.isnan(crudo)):
            raise DatosInconsistentes(f"Un label {target} MADURA no tiene valor.")
        labels[target] = regla(crudo)
    return labels


def construir(
    sesiones: Callable[[], AbstractContextManager[Session]],
    *,
    despacho_id: str,
    cartera_id: str,
    zona: str,
    anclas: Iterable[AnclaDataset],
    interpretaciones: list[int],
    usadas: Callable[[AnclaDataset], bool] = lambda _: True,
    punto: Callable[[str], None] = lambda _: None,
    al_terminar_ancla: Callable[[BloqueAncla], None] | None = None,
) -> Dataset:
    """El dataset de las anclas, una transaccion de lectura por ancla. `usadas` dice de que anclas
    se guardan las filas (todas entran al hash y a los conteos); `punto` recibe los puntos de falla
    DESPUES_FEATURES y DESPUES_LABELS de cada ancla."""
    inicio = time.perf_counter()
    pasos = medicion.Tiempos()
    huella = hashlib.sha256(_cabecera())
    diccionario = Diccionario()
    cuentas: dict[str, int] = {}
    bloques: list[BloqueAncla] = []
    filas_por_split: dict[str, int] = {}
    censurados = 0
    conteos: dict[str, dict[str, dict[str, float]]] = {}
    for ancla in sorted(anclas, key=lambda a: a.orden):
        reloj = time.perf_counter()
        relojes = construccion.Relojes(
            ancla.corte_canonico_id,
            despacho_id,
            cartera_id,
            ancla.feature_as_of,
            ancla.feature_known_at,
            zona,
        )
        madurez = {t.target: ancla.madurez(t) for t in targets_.TARGETS}
        with sesiones() as s:
            with pasos.fase("features"):
                disponibilidad = construccion.preparar_features(s, relojes)
            punto(FEATURES_DE_LA_ANCLA)
            with pasos.fase("labels"):
                construccion.preparar_labels(
                    s,
                    relojes,
                    label_known_at=ancla.label_known_at,
                    interpretaciones=interpretaciones,
                )
            punto(LABELS_DE_LA_ANCLA)
            with pasos.fase("copia"):
                csv = construccion.copiar(
                    s,
                    sql.sql_dataset(
                        pagos=madurez[Target.RECUPERACION_POSITIVA_30D] == Madurez.MADURA,
                        contacto=madurez[Target.CONTACTO_14D] == Madurez.MADURA,
                        promesa=madurez[Target.PROMESA_30D] == Madurez.MADURA,
                    ),
                )
            s.rollback()
        conversion = time.perf_counter()
        huella.update(f"ancla {ancla.hash_ancla} {len(csv)}\n".encode())
        huella.update(csv)
        t = construccion.tabla(csv, TIPOS_DATASET)
        del csv
        if t.num_rows == 0:
            raise DatosInconsistentes(
                f"El corte del ancla del {ancla.feature_as_of} no tiene cuentas."
            )
        ids = t.column("cuenta_id").to_pylist()
        if len(set(ids)) != len(ids):
            raise DatosInconsistentes(f"El ancla del {ancla.feature_as_of} repite una cuenta.")
        codigos = np.fromiter(
            (cuentas.setdefault(c, len(cuentas)) for c in ids), dtype=np.int32, count=len(ids)
        )
        labels = _labels(t, madurez)
        filas_por_split[ancla.split] = filas_por_split.get(ancla.split, 0) + t.num_rows
        if any(m != Madurez.MADURA for m in madurez.values()):
            censurados += t.num_rows
        for target, m in madurez.items():
            por_split = conteos.setdefault(target.value, {}).setdefault(
                ancla.split,
                {"maduras": 0, "censuradas": 0, "no_disponibles": 0, "positivos": 0.0},
            )
            clave = {
                Madurez.MADURA: "maduras",
                Madurez.CENSURADA: "censuradas",
                Madurez.NO_DISPONIBLE: "no_disponibles",
            }[m]
            por_split[clave] += t.num_rows
            if m == Madurez.MADURA:
                por_split["positivos"] += float(np.sum(labels[target]))
        guardar = usadas(ancla)
        categoricas = np.empty((t.num_rows if guardar else 0, len(CATEGORICAS)), dtype=np.int32)
        if guardar:
            for k, f in enumerate(CATEGORICAS):
                indices, valores = construccion.diccionario(t.column(f.nombre))
                categoricas[:, k] = diccionario.codificar(indices, valores)
        bloque = BloqueAncla(
            ancla=ancla,
            numericas=(
                construccion.numericas(t) if guardar else np.zeros((0, len(features_.NUMERICAS)))
            ),
            categoricas=categoricas,
            cuenta=codigos if guardar else np.zeros(0, dtype=np.int32),
            labels=labels if guardar else {},
            madurez=madurez,
            disponibilidad=disponibilidad.definicion(),
            segundos=time.perf_counter() - reloj,
        )
        pasos.fases["conversion"] = (
            pasos.fases.get("conversion", 0.0) + time.perf_counter() - conversion
        )
        bloques.append(bloque)
        if al_terminar_ancla is not None:
            al_terminar_ancla(bloque)
    return Dataset(
        bloques=bloques,
        diccionario=diccionario,
        cuentas=len(cuentas),
        hash=huella.hexdigest(),
        filas_por_split=filas_por_split,
        censurados=censurados,
        conteos=_con_tasas(conteos),
        segundos=time.perf_counter() - inicio,
        fases=pasos.resumen()["fases_segundos"],
    )


def _con_tasas(conteos: dict) -> dict:
    """Cada conteo con su prevalencia (o su media, en el monto) sobre las filas maduras."""
    for target, por_split in conteos.items():
        for valores in por_split.values():
            maduras = valores["maduras"]
            nombre = "media" if target == Target.RECUPERACION_POSITIVA_MONTO_30D else "prevalencia"
            positivos = valores.pop("positivos")
            valores[nombre] = round(positivos / maduras, 8) if maduras else None
    return conteos
