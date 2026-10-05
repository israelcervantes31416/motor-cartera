"""Recibir un archivo por la API: guardarlo como artefacto fuente antes de registrar nada.

POST /corridas y POST /pagos reciben un archivo y lo guardan igual: por bloques, mientras llega, en
el almacen de artefactos, con su SHA-256 calculado al copiarlo. El archivo nunca esta entero en la
memoria de la API. Solo cuando ya es durable se registra en la base lo que cuelga de el.

Lo que se decide aqui se decide sin juzgar ningun registro: que el nombre sea de un formato que se
recibe, que el archivo no este vacio ni pase del tope, y que sus bytes sean los de su formato.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from fastapi import UploadFile

from motor_cartera.api.errores import ErrorDeApi
from motor_cartera.config import Config
from motor_cartera.fuentes.almacen import ArtefactoDemasiadoGrande, ArtefactoVacio
from motor_cartera.fuentes.artefactos import ArtefactoGuardado, almacen_de, guardar_artefacto
from motor_cartera.fuentes.formatos import (
    FormatoNoCorresponde,
    FormatoNoSoportado,
    formato_por_extension,
)

ERRORES_DE_SUBIDA = (
    (413, "ARCHIVO_DEMASIADO_GRANDE", "El archivo pasa del tope (MC_TAMANO_MAXIMO_MB)."),
    (415, "FORMATO_NO_SOPORTADO", "El archivo no es xlsx, csv ni zip."),
    (
        415,
        "FORMATO_NO_CORRESPONDE",
        "Sus bytes no son los de su extension: un xlsx que no es un libro de Excel, un zip que no "
        "abre o un csv que no es texto.",
    ),
    (422, "ARCHIVO_VACIO", "El archivo llego vacio."),
)
"""Lo que puede responder una ruta que recibe un archivo, antes de registrar nada."""


def nombre_de(archivo: UploadFile) -> str:
    """El nombre del archivo, sin la ruta que algunos navegadores mandan (C:\\fakepath\\...)."""
    return PurePosixPath((archivo.filename or "").replace("\\", "/")).name


def guardar_subida(archivo: UploadFile, nombre: str, config: Config) -> ArtefactoGuardado:
    """Guarda el archivo como artefacto fuente, o responde el 4xx que corresponde. Si responde un
    error, no queda ningun objeto en el almacen."""
    try:
        formato_por_extension(nombre)
    except FormatoNoSoportado as exc:
        raise ErrorDeApi(415, "FORMATO_NO_SOPORTADO", str(exc)) from exc
    try:
        return guardar_artefacto(
            almacen_de(config),
            archivo.file,
            nombre,
            limite_bytes=config.tamano_maximo_mb * 1024 * 1024,
        )
    except ArtefactoVacio as exc:
        raise ErrorDeApi(422, "ARCHIVO_VACIO", f"{nombre!r} llego vacio.") from exc
    except ArtefactoDemasiadoGrande as exc:
        raise ErrorDeApi(
            413,
            "ARCHIVO_DEMASIADO_GRANDE",
            f"El archivo pasa del tope de {config.tamano_maximo_mb} MiB.",
        ) from exc
    except FormatoNoCorresponde as exc:
        raise ErrorDeApi(415, "FORMATO_NO_CORRESPONDE", str(exc)) from exc
