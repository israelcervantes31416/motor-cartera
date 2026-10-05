"""Interfaz de linea de comandos."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated

import typer

app = typer.Typer(help="Motor de cartera: ingesta, validacion, persistencia y su worker.")


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

    Pasa por la cola durable, como la API, pero en primer plano: la corrida, su archivo y su
    trabajo se registran juntos, y el trabajo ya es de este proceso. No encadena la decision ni
    las demas etapas. Si se interrumpe, el trabajo queda en la cola y un worker lo termina cuando
    vence su lease. Termina con codigo 1 si la corrida no publico, para que un script o un
    programador de tareas lo note.
    """
    from motor_cartera.db.modelos import EstadoCorrida
    from motor_cartera.ingesta.corridas import ArchivoDuplicado
    from motor_cartera.orquestacion.worker import ingerir_en_primer_plano

    try:
        corrida = ingerir_en_primer_plano(ruta)
    except (ArchivoDuplicado, ValueError) as exc:
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


@app.command("verificar-fuentes")
def verificar_fuentes(
    minimo: Annotated[
        int, typer.Option(help="Cuantos artefactos tiene que haber al menos, para un smoke.")
    ] = 0,
) -> None:
    """Vuelve a firmar cada artefacto fuente que la base registra y comprueba que el almacen tenga
    exactamente sus bytes.

    Lee cada objeto por bloques, sin modificar nada. Termina con codigo 1 si alguno falta o esta
    danado, o si hay menos de --minimo: un almacen que no se monto, o que se perdio, no se da por
    bueno.
    """
    from motor_cartera.config import config
    from motor_cartera.db.sesion import sesion
    from motor_cartera.fuentes.artefactos import almacen_de, auditar_artefactos

    with sesion() as s:
        revisados = auditar_artefactos(s, almacen_de(config))
    problemas = [r for r in revisados if r.problema is not None]
    for revisado in problemas:
        typer.echo(f"  {revisado.artefacto.artifact_id}: {revisado.problema}", err=True)
    typer.echo(f"{len(revisados) - len(problemas)} de {len(revisados)} artefactos intactos.")
    if problemas or len(revisados) < minimo:
        raise typer.Exit(code=1)


@app.command()
def worker(
    una_vez: Annotated[
        bool,
        typer.Option(
            "--una-vez", help="Procesa a lo mas un trabajo y sale: para probar o diagnosticar."
        ),
    ] = False,
) -> None:
    """Toma trabajos de la cola durable y los ejecuta, uno a la vez, hasta recibir SIGTERM o
    SIGINT.

    Corre aparte de la API: la API deja cada recurso EN_PROCESO con su trabajo en PostgreSQL, y
    este proceso lo toma, ejecuta su motor y encadena la etapa que sigue. Se pueden correr varios
    a la vez: nunca toman el mismo trabajo. Con --una-vez procesa a lo mas uno y sale, con
    codigo 0 aunque la cola este vacia.
    """
    from motor_cartera.orquestacion.worker import ejecutar_worker

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s"
    )
    procesado = ejecutar_worker(una_vez=una_vez)
    if not una_vez:
        return
    if procesado is None:
        typer.echo("No hay trabajos que tomar.")
        return
    estado = procesado.estado or "lo cierra otro worker"
    typer.echo(
        f"Trabajo {procesado.trabajo_id} ({procesado.tipo}, intento {procesado.intentos}): {estado}"
    )


if __name__ == "__main__":
    app()
