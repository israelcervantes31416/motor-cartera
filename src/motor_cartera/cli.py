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
    """Lee un archivo, lo juzga contra el contrato y lo publica como una corrida.

    Es el mismo proceso que usa la API (ingesta.corridas), en primer plano. Termina con
    codigo 1 si la corrida no publico, para que un script o un programador de tareas lo note.
    """
    from motor_cartera.db.modelos import EstadoCorrida
    from motor_cartera.ingesta.corridas import ArchivoYaPublicado, ingerir_archivo

    try:
        corrida = ingerir_archivo(ruta)
    except ArchivoYaPublicado as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Corrida {corrida.run_id}: {corrida.estado}")
    typer.echo(
        f"  leidas {corrida.filas_leidas}, validas {corrida.filas_validas}, "
        f"rechazadas {corrida.filas_rechazadas}"
    )
    typer.echo(f"  {corrida.detalle}")
    if corrida.estado != EstadoCorrida.EXITOSA:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
