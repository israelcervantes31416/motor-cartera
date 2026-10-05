"""Interfaz de linea de comandos."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(
    help="Motor de cartera: fuentes oficiales, ingesta, validacion, persistencia y su worker."
)


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


@app.command("generar-oficial")
def generar_oficial(
    destino: Annotated[
        str, typer.Option(help="El directorio donde se escriben.")
    ] = "datos/oficial",
    perfil: Annotated[
        str,
        typer.Option(
            help="XS (1,000), S (10,000), M (100,000), L (250,000), XL (500,000: el escenario "
            "empresarial objetivo) o XXL (1,000,000: prueba de esfuerzo)."
        ),
    ] = "XS",
    n: Annotated[int | None, typer.Option(help="Cuantas cuentas; reemplaza al perfil.")] = None,
    formato: Annotated[str, typer.Option(help="xlsx, csv o zip.")] = "xlsx",
    fecha_corte: Annotated[
        datetime | None, typer.Option(formats=["%Y-%m-%d"], help="Por omision, hoy.")
    ] = None,
    semilla: Annotated[int | None, typer.Option(help="Por omision, MC_SEMILLA.")] = None,
    carrier: Annotated[
        bool, typer.Option(help="Con la hoja companera CARRIER, en xlsx y zip.")
    ] = True,
    pagos: Annotated[
        bool,
        typer.Option(help="Tambien los pagos (pagos/v1) de la semana que termina en el corte."),
    ] = True,
) -> None:
    """Genera la cartera oficial sintetica (cartera/v2, 93 columnas) en un archivo: en xlsx, las
    hojas CARTERA y CARRIER; en zip, CARTERA.csv y CARRIER.csv; en csv, solo CARTERA. Y, aparte,
    los pagos de esas cuentas en los siete dias que terminan en el corte (pagos/v1, 23 columnas),
    en el mismo formato.

    Por omision es pequena (XS): una cartera grande se pide con su perfil, nunca por accidente. Se
    arma y se escribe por bloques, asi que XL y XXL no se cargan enteras en memoria; para ellas
    conviene csv o zip, porque escribir un xlsx de ese tamano es lento por el formato.
    """
    from motor_cartera.config import config
    from motor_cartera.generador.oficial import (
        PERFILES,
        escribir_cartera,
        escribir_pagos,
        estado_inicial,
        tabla_pagos,
    )

    if n is None and perfil.upper() not in PERFILES:
        typer.echo(f"Perfil desconocido: {perfil}. Usa {', '.join(PERFILES)}.", err=True)
        raise typer.Exit(code=2)
    cuantas = n if n is not None else PERFILES[perfil.upper()]
    corte = fecha_corte.date() if fecha_corte else date.today()
    semilla = config.semilla if semilla is None else semilla
    estado = estado_inicial(cuantas, semilla=semilla, fecha_corte=corte)
    ruta = Path(destino) / f"cartera_oficial_{corte.isoformat()}.{formato.lower().lstrip('.')}"
    escrito = escribir_cartera(
        estado, ruta, semilla=semilla, fecha_corte=corte, con_carrier=carrier
    )
    typer.echo(
        f"Escrito: {escrito.ruta} ({escrito.filas:,} cuentas"
        + (f", {escrito.filas_carrier:,} filas de CARRIER" if escrito.filas_carrier else "")
        + f"; fecha de corte {corte.isoformat()}, que se declara al cargarla)"
    )
    if pagos:
        desde = corte - timedelta(days=6)
        movimientos = tabla_pagos(estado, semilla=semilla, desde=desde, hasta=corte)
        nombre = f"pagos_oficial_{desde.isoformat()}_{corte.isoformat()}.{ruta.suffix[1:]}"
        escritos = escribir_pagos(movimientos, Path(destino) / nombre)
        typer.echo(f"Escrito: {escritos.ruta} ({escritos.filas:,} movimientos)")


@app.command()
def cargar(
    ruta: str,
    contrato: Annotated[
        str,
        typer.Option(help="cartera/v1 (8 columnas, por omision) o cartera/v2 (93 columnas)."),
    ] = "cartera/v1",
    fecha_corte: Annotated[
        datetime | None,
        typer.Option(
            formats=["%Y-%m-%d"],
            help="Obligatoria con cartera/v2, que no la trae en el archivo; no con cartera/v1.",
        ),
    ] = None,
) -> None:
    """Lee un archivo, lo juzga contra el contrato y lo publica como una corrida.

    Pasa por la cola durable, como la API, pero en primer plano: el archivo queda en el almacen de
    artefactos, y su corrida y su trabajo se registran juntos, con el trabajo ya de este proceso.
    No encadena la decision ni las demas etapas. Si se interrumpe, el trabajo queda en la cola y
    un worker lo termina cuando vence su lease. Termina con codigo 1 si la corrida no publico, para
    que un script o un programador de tareas lo note.
    """
    from motor_cartera.db.modelos import EstadoCorrida
    from motor_cartera.ingesta.corridas import ArchivoDuplicado
    from motor_cartera.orquestacion.worker import ingerir_en_primer_plano

    try:
        corrida = ingerir_en_primer_plano(
            ruta,
            contrato=contrato,
            fecha_corte=fecha_corte.date() if fecha_corte else None,
        )
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


@app.command("cargar-pagos")
def cargar_pagos(
    ruta: str,
    tolerancia: Annotated[
        float | None,
        typer.Option(
            min=0.0,
            max=0.999,
            help="Fraccion maxima de movimientos rechazados. Por omision, "
            "MC_TOLERANCIA_RECHAZO_PAGOS, que es 0: un movimiento invalido rechaza el archivo.",
        ),
    ] = None,
) -> None:
    """Lee un archivo de pagos, lo juzga contra pagos/v1 y, si pasa, acepta sus movimientos, sin
    deduplicar ninguno.

    Como cargar: pasa por la cola durable, en primer plano, con el archivo en el almacen de
    artefactos y su trabajo ya de este proceso. Termina con codigo 1 si la ingesta no se acepto.
    """
    from motor_cartera.db.modelos import EstadoIngestaPagos
    from motor_cartera.ingesta.pagos import PagosDuplicados
    from motor_cartera.orquestacion.worker import ingerir_pagos_en_primer_plano

    try:
        ingesta = ingerir_pagos_en_primer_plano(ruta, tolerancia=tolerancia)
    except (PagosDuplicados, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Ingesta de pagos {ingesta.pagos_run_id}: {ingesta.estado}")
    typer.echo(
        f"  leidas {ingesta.filas_leidas}, validas {ingesta.filas_validas}, "
        f"rechazadas {ingesta.filas_rechazadas}"
    )
    typer.echo(f"  {ingesta.detalle}")
    if ingesta.estado != EstadoIngestaPagos.EXITOSA:
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
