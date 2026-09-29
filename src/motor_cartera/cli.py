"""Interfaz de linea de comandos."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

import typer

app = typer.Typer(help="Motor de cartera: ingesta, validacion y persistencia.")


@app.command()
def generar(
    n: int = 10_000,
    destino: str = "datos/cartera_sintetica.xlsx",
    tasa_invalidas: Annotated[
        float, typer.Option(help="Fraccion de filas invalidas a proposito, entre 0 y 1.")
    ] = 0.02,
    semilla: Annotated[int | None, typer.Option(help="Por omision, MC_SEMILLA.")] = None,
    fecha_corte: Annotated[
        datetime | None, typer.Option(formats=["%Y-%m-%d"], help="Por omision, hoy.")
    ] = None,
) -> None:
    """Genera una cartera sintetica en disco. El formato sale de la extension: xlsx, csv o zip.

    Por omision mete 2% de filas invalidas: una cartera sin defectos no ejercita el contrato.
    """
    from motor_cartera.generador.sintetico import generar_archivo

    ruta = generar_archivo(
        destino,
        n=n,
        tasa_invalidas=tasa_invalidas,
        semilla=semilla,
        fecha_corte=fecha_corte.date() if fecha_corte else None,
    )
    typer.echo(f"Escrito: {ruta}")


@app.command()
def cargar(ruta: str) -> None:
    """Lee un archivo, valida el contrato y persiste el resultado en una corrida.

    TODO(israel): este es el hilo que amarra todo. El orden importa:
      1. abre una Corrida y registra el origen
      2. lee el archivo
      3. valida contra el contrato; si falla, marca la corrida como fallida y NO escribe
      4. solo si paso, inserta las Cuenta ligadas a esa corrida
      5. cierra la corrida con sus conteos
    """
    raise NotImplementedError("Pendiente.")


if __name__ == "__main__":
    app()
