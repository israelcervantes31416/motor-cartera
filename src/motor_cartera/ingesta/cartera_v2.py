"""La ingesta de cartera/v2: del artefacto original a Cuenta, por lotes y todo o nada.

En la transaccion que tiene la corrida bloqueada:

  1. se vuelve a firmar el artefacto: el almacen tiene que tener exactamente los bytes recibidos;
  2. se abre la hoja CARTERA (o el csv de cartera dentro del zip) y se revisa su estructura: las 93
     columnas exactas, o la corrida queda FALLIDA sin juzgar un solo registro;
  3. primera pasada: cada lote se juzga contra el contrato y contra el catalogo geografico de la
     proyeccion; los rechazos se copian a la base al momento, con su fila, sus valores y sus
     motivos;
  4. CARRIER, si viene, se audita: estructura, filas y coherencia con CARTERA, como advertencias;
  5. la barrera de siempre (`decidir`): con los conteos definitivos, incluidas las copias de un
     CLIENTE_UNICO repetido, se decide si se publica;
  6. segunda pasada: se firman los validos y, si se publica, se escribe el dataset conformado y se
     proyecta cada cuenta a Cuenta, con COPY;
  7. si se publico, el Parquet se guarda en el almacen y se registra con su linaje.

Nada se confirma aqui: quien llama confirma todo junto, o revierte todo. Una corrida publica todas
sus cuentas o ninguna, igual que en cartera/v1, aunque se haya leido de a 50,000 filas.
"""

from __future__ import annotations

import json
import logging
import tempfile
from dataclasses import asdict
from pathlib import Path

from sqlalchemy import text
from sqlmodel import Session

from motor_cartera.config import Config
from motor_cartera.contratos.cartera_v2 import (
    COLUMNAS_CARRIER,
    CONTRATO_V2,
    HOJA_CARRIER,
    HOJA_CARTERA,
    TELEFONO,
)
from motor_cartera.db.copia import Jsonb, copiar
from motor_cartera.db.modelos import (
    ArtefactoFuente,
    Corrida,
    DatasetConformado,
    EstadoCorrida,
    HojaCompanera,
    ahora,
)
from motor_cartera.fuentes.artefactos import ArtefactoGuardado, almacen_de, registrar_artefacto
from motor_cartera.fuentes.conformado import EscritorConformado
from motor_cartera.fuentes.formatos import Formato
from motor_cartera.fuentes.lotes import CompaneraAuditada, EspecificacionCompanera, abrir_fuente
from motor_cartera.fuentes.proyeccion import (
    COLUMNAS_CUENTA,
    VERSION_PROYECCION,
    catalogo_consultado,
    filas_cuenta,
    rechazos_geograficos,
)
from motor_cartera.ingesta.fuente_oficial import DUPLICADO, Cronometro, JuicioDeFuente, Rechazado
from motor_cartera.ingesta.lectores import ErrorDeLectura

log = logging.getLogger(__name__)

CARRIER = EspecificacionCompanera(
    nombre=HOJA_CARRIER,
    columnas=COLUMNAS_CARRIER,
    llave="CLIENTE_UNICO",
    formas={"TELEFONO": TELEFONO},
)


def juzgar_y_publicar(
    s: Session, corrida: Corrida, *, config: Config, cronometro: Cronometro | None = None
) -> str:
    """Juzga la corrida de cartera/v2 y, si pasa la barrera, publica sus cuentas y su dataset
    conformado; la deja en su estado terminal. Devuelve el veredicto.

    No confirma. Levanta ErrorDeLectura o ErrorDeEstructura si el archivo no se puede juzgar, y
    ArtefactoCorrupto si el almacen ya no tiene sus bytes: quien llama revierte y la deja FALLIDA.
    """
    from motor_cartera.ingesta.corridas import decidir

    cronometro = cronometro or Cronometro()
    artefacto = s.get_one(ArtefactoFuente, corrida.artefacto_fuente_id)
    almacen = almacen_de(config)
    with cronometro.fase("verificacion"):
        almacen.verificar(artefacto.sha256, artefacto.tamano_bytes)

    with (
        tempfile.TemporaryDirectory(prefix="motor-cartera-") as temporal,
        almacen.como_archivo(artefacto.sha256) as ruta,
    ):
        directorio = Path(temporal)
        with abrir_fuente(
            ruta,
            artefacto.formato,
            corrida.origen,
            CONTRATO_V2,
            filas_por_lote=config.filas_por_lote,
            hoja_principal=HOJA_CARTERA,
            companera=CARRIER,
        ) as fuente:
            juicio = JuicioDeFuente(
                CONTRATO_V2,
                directorio,
                rechazar=lambda rechazados: _copiar_rechazos(s, corrida.id, rechazados),
                validar_extra=rechazos_geograficos,
                filas_por_lote=config.filas_por_lote,
                cronometro=cronometro,
            )
            juicio.primera_pasada(fuente.lotes())
            if juicio.leidas == 0:
                raise ErrorDeLectura(f"{fuente.origen}: no trae ningun registro.")
            with cronometro.fase("companera"):
                companera = fuente.auditar_companera(juicio.llaves())
            origen = fuente.origen

        duplicadas = juicio.duplicadas()
        if duplicadas:
            with cronometro.fase("persistencia"):
                _marcar_duplicadas(s, corrida.id, duplicadas)
        validas = juicio.validas
        cortes = {corrida.fecha_corte: validas} if validas else {}
        estado, veredicto = decidir(
            juicio.leidas, juicio.rechazadas, corrida.tolerancia_rechazo, cortes
        )
        publica = estado == EstadoCorrida.EXITOSA

        escritor = None
        if publica:
            escritor = EscritorConformado(
                directorio / "conformado.parquet",
                CONTRATO_V2,
                {
                    "contrato": CONTRATO_V2.version,
                    "artefacto_original": artefacto.sha256,
                    "fecha_corte": corrida.fecha_corte.isoformat(),
                    "proyeccion": VERSION_PROYECCION,
                },
            )

        def publicar(canonicos, hojas) -> None:
            with cronometro.fase("conformado"):
                escritor.escribir(canonicos, hojas)
            with cronometro.fase("proyeccion"):
                filas = list(filas_cuenta(canonicos, corrida.id, corrida.fecha_corte))
            with cronometro.fase("persistencia"):
                copiar(s, "cuenta", COLUMNAS_CUENTA, filas)

        firma = juicio.segunda_pasada(publicar if publica else None)
        firma_contenido = firma.hexdigest()
        if escritor is not None:
            escritor.cerrar()
            with cronometro.fase("almacen_conformado"):
                _publicar_conformado(s, corrida, artefacto, escritor, firma_contenido, almacen)

    if companera is not None:
        s.add(_hoja(corrida.id, companera))
    corrida.estado = estado
    corrida.filas_leidas = juicio.leidas
    corrida.filas_validas = validas
    corrida.filas_rechazadas = juicio.rechazadas
    corrida.firma_contenido = firma_contenido
    corrida.detalle = (
        f"{veredicto} Origen: {origen.rstrip('.')}. Proyeccion {VERSION_PROYECCION}, catalogo "
        f"INEGI consultado el {catalogo_consultado()}.{_resumen(companera)}"
    )
    corrida.terminada_en = ahora()
    s.add(corrida)
    s.flush()
    log.info("corrida %s (cartera/v2): %s", corrida.run_id, veredicto)
    return veredicto


def _copiar_rechazos(s: Session, corrida_id: int, rechazados: list[Rechazado]) -> None:
    copiar(
        s,
        "rechazo",
        ("corrida_id", "fila", "valores", "motivos"),
        (
            (corrida_id, r.fila, Jsonb(r.valores), Jsonb([asdict(m) for m in r.motivos]))
            for r in rechazados
        ),
    )


def _marcar_duplicadas(s: Session, corrida_id: int, duplicadas: set[str]) -> None:
    """A los registros ya rechazados por otra razon cuyo CLIENTE_UNICO se repite, les agrega ese
    motivo: tambien son copias de una llave repetida. Va primero, porque es la primera columna."""
    motivo = json.dumps([{"campo": CONTRATO_V2.llave_unica, "regla": DUPLICADO}])
    s.execute(
        text(
            "UPDATE rechazo SET motivos = CAST(:motivo AS JSONB) || motivos "
            "WHERE corrida_id = :corrida AND valores ->> :llave = ANY(:duplicadas)"
        ),
        {
            "motivo": motivo,
            "corrida": corrida_id,
            "llave": CONTRATO_V2.llave_unica,
            "duplicadas": sorted(duplicadas),
        },
    )


def _publicar_conformado(
    s: Session,
    corrida: Corrida,
    original: ArtefactoFuente,
    escritor: EscritorConformado,
    firma_contenido: str,
    almacen,
) -> None:
    """El Parquet al almacen (primero el objeto durable) y su registro con su linaje."""
    with escritor.ruta.open("rb") as archivo:
        objeto = almacen.guardar(archivo)
    nombre = f"cartera_v2_{corrida.run_id}.parquet"
    conformado = registrar_artefacto(s, ArtefactoGuardado(objeto, Formato.PARQUET, nombre))
    s.add(
        DatasetConformado(
            contrato=CONTRATO_V2.version,
            corrida_id=corrida.id,
            artefacto_original_id=original.id,
            artefacto_conformado_id=conformado.id,
            firma_contenido=firma_contenido,
            filas=escritor.filas,
            columnas=len(CONTRATO_V2.columnas),
        )
    )


def _hoja(corrida_id: int, companera: CompaneraAuditada) -> HojaCompanera:
    return HojaCompanera(
        corrida_id=corrida_id,
        nombre=companera.nombre[:255],
        filas=companera.filas,
        columnas=companera.columnas,
        estructura_reconocida=companera.estructura_reconocida,
        advertencias=list(companera.advertencias),
    )


def _resumen(companera: CompaneraAuditada | None) -> str:
    if companera is None:
        return ""
    estructura = "reconocida" if companera.estructura_reconocida else "no reconocida"
    advertencias = len(companera.advertencias)
    nota = "sin advertencias" if not advertencias else f"{advertencias} advertencia(s)"
    return (
        f" Hoja companera {companera.nombre!r}: {companera.filas:,} filas y "
        f"{companera.columnas} columnas, estructura {estructura}, {nota}; no gobierna la "
        "publicacion."
    )
