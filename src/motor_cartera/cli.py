"""Interfaz de linea de comandos."""

from __future__ import annotations

import typer

app = typer.Typer(help="Motor de cartera: ingesta, validacion y persistencia.")


@app.command()
def generar(n: int = 10_000, destino: str = "datos/cartera_sintetica.xlsx") -> None:
    """Genera una cartera sintetica en disco."""
    from motor_cartera.generador.sintetico import generar_archivo

    generar_archivo(destino, n=n)
    typer.echo(f"Escrito: {destino}")


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
