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
    puntos: Annotated[
        str | None,
        typer.Option(
            "--puntos",
            help="Una muestra de puntos de una referencia importada (muestrear-puntos): con ella "
            "cada cuenta lleva LATITUD y LONGITUD sinteticas. Sin ella, van vacias.",
        ),
    ] = None,
    proporciones: Annotated[
        str | None,
        typer.Option(
            "--proporciones",
            help="Con --puntos: validas=0.9,faltantes=0.04,incompletas=0.01,invalidas=0.01,"
            "fuera=0.02,discrepantes=0.02 (estas son las de omision; las que no se nombran valen "
            "0).",
        ),
    ] = None,
) -> None:
    """Genera la cartera oficial sintetica (cartera/v2, 93 columnas) en un archivo: en xlsx, las
    hojas CARTERA y CARRIER; en zip, CARTERA.csv y CARRIER.csv; en csv, solo CARTERA. Y, aparte,
    los pagos de esas cuentas en los siete dias que terminan en el corte (pagos/v1, 23 columnas),
    en el mismo formato.

    Por omision es pequena (XS): una cartera grande se pide con su perfil, nunca por accidente. Se
    arma y se escribe por bloques, asi que XL y XXL no se cargan enteras en memoria; para ellas
    conviene csv o zip, porque escribir un xlsx de ese tamano es lento por el formato.

    Con --puntos, LATITUD y LONGITUD llevan coordenadas sinteticas en las proporciones que se
    pidan: validas, faltantes, incompletas, invalidas (0, 0), fuera de la referencia y
    discrepantes.
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
    coordenadas = _coordenadas(puntos, proporciones)
    estado = estado_inicial(cuantas, semilla=semilla, fecha_corte=corte)
    ruta = Path(destino) / f"cartera_oficial_{corte.isoformat()}.{formato.lower().lstrip('.')}"
    escrito = escribir_cartera(
        estado,
        ruta,
        semilla=semilla,
        fecha_corte=corte,
        con_carrier=carrier,
        coordenadas=coordenadas,
    )
    typer.echo(
        f"Escrito: {escrito.ruta} ({escrito.filas:,} cuentas"
        + (f", {escrito.filas_carrier:,} filas de CARRIER" if escrito.filas_carrier else "")
        + f"; fecha de corte {corte.isoformat()}, que se declara al cargarla)"
    )
    if escrito.coordenadas:
        typer.echo(
            "  coordenadas: " + ", ".join(f"{c} {n:,}" for c, n in escrito.coordenadas.items())
        )
    if pagos:
        desde = corte - timedelta(days=6)
        movimientos = tabla_pagos(estado, semilla=semilla, desde=desde, hasta=corte)
        nombre = f"pagos_oficial_{desde.isoformat()}_{corte.isoformat()}.{ruta.suffix[1:]}"
        escritos = escribir_pagos(movimientos, Path(destino) / nombre)
        typer.echo(f"Escrito: {escritos.ruta} ({escritos.filas:,} movimientos)")


@app.command("generar-escenario")
def generar_escenario(
    destino: Annotated[
        str, typer.Option(help="El directorio donde se escriben.")
    ] = "datos/escenario",
    perfil: Annotated[
        str, typer.Option(help="El tamano del primer corte: XS, S, M, L, XL o XXL.")
    ] = "XS",
    cuentas: Annotated[
        int | None, typer.Option(help="Cuantas cuentas en el primer corte; reemplaza al perfil.")
    ] = None,
    cortes: Annotated[int, typer.Option(min=1, help="Cuantos cortes.")] = 4,
    primer_corte: Annotated[
        datetime | None, typer.Option(formats=["%Y-%m-%d"], help="Por omision, hoy.")
    ] = None,
    dias_entre_cortes: Annotated[int, typer.Option(min=1, help="Dias entre un corte y otro.")] = 7,
    formato: Annotated[str, typer.Option(help="zip (por omision), csv o xlsx.")] = "zip",
    semilla: Annotated[int | None, typer.Option(help="Por omision, MC_SEMILLA.")] = None,
    tasa_altas: Annotated[
        float, typer.Option(min=0.0, max=1.0, help="Fraccion del corte que llega nueva.")
    ] = 0.02,
    tasa_retiros: Annotated[
        float, typer.Option(min=0.0, max=1.0, help="Fraccion que el acreedor retira.")
    ] = 0.01,
    lifecycle: Annotated[
        bool,
        typer.Option(
            "--lifecycle",
            help="Tambien los eventos operacionales sinteticos de cada periodo, en JSONL "
            "comprimido, para cargar-lifecycle. El escenario tiene que terminar antes de hoy.",
        ),
    ] = False,
    intensidad_lifecycle: Annotated[
        float,
        typer.Option(min=0.01, max=5.0, help="Escala cuantas gestiones hay por periodo."),
    ] = 1.0,
    puntos: Annotated[
        str | None,
        typer.Option(
            "--puntos",
            help="Una muestra de puntos de una referencia importada (muestrear-puntos): con ella "
            "cada cuenta lleva LATITUD y LONGITUD sinteticas. Sin ella, van vacias.",
        ),
    ] = None,
    proporciones: Annotated[
        str | None,
        typer.Option(
            "--proporciones",
            help="Con --puntos: validas=0.9,faltantes=0.04,incompletas=0.01,invalidas=0.01,"
            "fuera=0.02,discrepantes=0.02 (estas son las de omision; las que no se nombran valen "
            "0).",
        ),
    ] = None,
) -> None:
    """Genera un escenario longitudinal: varios cortes de la misma cartera (cartera/v2) y los pagos
    de cada periodo entre un corte y el siguiente (pagos/v1), con un manifiesto, escenario.json.

    De un corte al siguiente, los pagos bajan los saldos y curan el atraso, las cuentas liquidadas
    y las retiradas salen y llegan altas que nunca habian estado en la cartera. Todo se deriva de la
    semilla: el mismo escenario sale igual, byte por byte. El manifiesto trae cada archivo con su
    SHA-256, lo que paso en cada corte y las invariantes que cumple el escenario.

    Con --lifecycle, tambien lo que la cobranza hizo en cada periodo: gestiones, contactos,
    visitas, promesas, convenios, cancelaciones, anulaciones y eventos tardios, todo sintetico y
    con su propia semilla derivada, sin cambiar un byte de los cortes ni de los pagos.
    """
    from motor_cartera.config import config
    from motor_cartera.generador.oficial import PERFILES
    from motor_cartera.generador.oficial import generar_escenario as escribir_escenario

    if cuentas is None and perfil.upper() not in PERFILES:
        typer.echo(f"Perfil desconocido: {perfil}. Usa {', '.join(PERFILES)}.", err=True)
        raise typer.Exit(code=2)
    inicio = primer_corte.date() if primer_corte else date.today()
    coordenadas = _coordenadas(puntos, proporciones)
    try:
        escenario = escribir_escenario(
            destino,
            cuentas=cuentas if cuentas is not None else PERFILES[perfil.upper()],
            cortes=cortes,
            primer_corte=inicio,
            semilla=config.semilla if semilla is None else semilla,
            dias_entre_cortes=dias_entre_cortes,
            formato=formato,
            tasa_altas=tasa_altas,
            tasa_retiros=tasa_retiros,
            lifecycle=lifecycle,
            intensidad_lifecycle=intensidad_lifecycle,
            coordenadas=coordenadas,
        )
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    manifiesto = escenario.manifiesto
    typer.echo(
        f"Escenario de {len(manifiesto['cortes'])} cortes cada {dias_entre_cortes} dias desde "
        f"{inicio.isoformat()}, en {escenario.destino}"
    )
    periodos = [None, *manifiesto["periodos"]]
    for corte, periodo in zip(manifiesto["cortes"], periodos, strict=True):
        linea = f"  {corte['fecha_corte']}: {corte['cuentas']:,} cuentas"
        if periodo is not None:
            linea += (
                f" (continuan {corte['continuan']:,}, liquidadas {corte['liquidadas']:,}, "
                f"retiradas {corte['retiradas']:,}, altas {corte['altas']:,}); pagos del "
                f"{periodo['desde']} al {periodo['hasta']}: {periodo['movimientos']:,} movimientos"
            )
        typer.echo(linea)
    for archivo in manifiesto.get("lifecycle", []):
        typer.echo(
            f"  lifecycle del {archivo['desde']} al {archivo['hasta']}: {archivo['eventos']:,} "
            f"eventos ({archivo['tardios']:,} tardios), {archivo['archivo']}"
        )
    typer.echo(f"Manifiesto: {escenario.destino / 'escenario.json'}")


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
    _avisar_historia(corrida_id=corrida.id)
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
    _avisar_historia(ingesta_pagos_id=ingesta.id)
    if ingesta.estado != EstadoIngestaPagos.EXITOSA:
        raise typer.Exit(code=1)


@app.command("cargar-lifecycle")
def cargar_lifecycle(
    ruta: str,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Valida y resuelve todo, y no registra nada."),
    ] = False,
) -> None:
    """Importa eventos operacionales sinteticos de un archivo JSONL (o .jsonl.gz): gestiones,
    visitas, promesas, convenios, cancelaciones y anulaciones de la cartera del sistema.

    No es una fuente oficial: es la importacion de eventos operacionales, con las reglas de la API.
    Cada linea trae su idempotency_key: el mismo archivo dos veces no duplica nada. Lee por lotes,
    copia con COPY y registra por conjuntos, en una transaccion: con un solo problema no registra
    nada y termina con codigo 1, diciendo cada linea y por que.
    """
    from motor_cartera.config import config
    from motor_cartera.lifecycle.importacion import (
        ArchivoIlegible,
        ImportacionConcurrente,
        importar,
    )

    try:
        reporte = importar(
            ruta,
            despacho_id=config.despacho_id,
            cartera_id=config.cartera_id,
            zona=config.zona_horaria_fuente,
            dry_run=dry_run,
        )
    except (ArchivoIlegible, ImportacionConcurrente) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(
        f"Importacion de {reporte.archivo} en {config.despacho_id}/{config.cartera_id}: "
        f"{reporte.lineas:,} lineas"
    )
    typer.echo(f"  repetidas en el archivo (misma llave, mismo contenido): {reporte.repetidas:,}")
    typer.echo(f"  ya registradas antes (misma llave, mismo contenido): {reporte.ya_registradas:,}")
    typer.echo(f"  eventos nuevos: {reporte.nuevos:,}")
    for tipo, cuantos in reporte.registrados.items():
        typer.echo(f"    {tipo}: {cuantos:,}")
    if reporte.total_de_problemas:
        typer.echo(f"Problemas: {reporte.total_de_problemas:,}. No se registro nada.", err=True)
        for problema in reporte.problemas:
            llave = f" ({problema.llave})" if problema.llave else ""
            typer.echo(
                f"  linea {problema.linea}{llave}: {problema.codigo}: {problema.detalle}",
                err=True,
            )
        if reporte.total_de_problemas > len(reporte.problemas):
            typer.echo(
                f"  ... y {reporte.total_de_problemas - len(reporte.problemas):,} mas.", err=True
            )
        raise typer.Exit(code=1)
    if dry_run:
        typer.echo(
            f"Con --dry-run no se registro nada; se registrarian {reporte.nuevos:,} eventos."
        )
        return
    typer.echo(
        f"Se registraron {reporte.nuevos:,} eventos en una transaccion, en {reporte.segundos} s."
    )


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


@app.command("backfill-historia")
def backfill_historia(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Solo dice cuanto falta y que encolaria.")
    ] = False,
    reintentar_fallidas: Annotated[
        bool,
        typer.Option(
            "--reintentar-fallidas",
            help="Tambien encola los datasets cuya historia solo tiene ejecuciones FALLIDA.",
        ),
    ] = False,
) -> None:
    """Encola la historia (historia/v1) de los datasets conformados que todavia no la tienen: los
    publicados antes de v0.7.0, o cuya historia fallo.

    La migracion 0008 solo crea las tablas del modelo historico; este proceso encola un trabajo
    HISTORIA por cada dataset de cartera/v2 y de pagos/v1 sin historia EXITOSA, los de cartera en
    orden de fecha de corte. Los ejecuta un worker, que materializa cada uno desde su Parquet
    conformado, nunca desde el archivo original. El resultado no depende del orden.

    Es idempotente: un dataset ya materializado o ya en la cola no se encola otra vez. Los que solo
    tienen ejecuciones FALLIDA se reportan, y se reintentan solo con --reintentar-fallidas: un
    conflicto de corte volveria a fallar.
    """
    from motor_cartera.config import config
    from motor_cartera.db.sesion import sesion
    from motor_cartera.historia.backfill import diagnosticar, encolar

    with sesion() as s:
        diagnostico = diagnosticar(s)
    fallidas = diagnostico.solo_fallidas
    typer.echo(f"Datasets conformados de cartera/v2 y pagos/v1: {diagnostico.datasets:,}")
    typer.echo(f"  con historia/v1 EXITOSA: {diagnostico.materializados:,}")
    typer.echo(f"  en la cola (EN_PROCESO): {diagnostico.en_cola:,}")
    typer.echo(f"  sin historia: {len(diagnostico.sin_historia):,}")
    nota = (
        "" if reintentar_fallidas or not fallidas else "; se reintentan con --reintentar-fallidas"
    )
    typer.echo(f"  solo con ejecuciones FALLIDA: {len(fallidas):,}{nota}")
    for pendiente in fallidas:
        typer.echo(f"    {_pendiente(pendiente)}: {pendiente.ultimo_resultado}")
    pendientes = diagnostico.sin_historia + (fallidas if reintentar_fallidas else [])
    if dry_run:
        lista = "; se encolarian:" if pendientes else "."
        typer.echo(f"Faltan {len(pendientes):,}. Con --dry-run no se encolo nada{lista}")
        for pendiente in pendientes:
            typer.echo(f"  {_pendiente(pendiente)}")
        return
    encolados = encolar(pendientes, config=config)
    abiertos = sum(1 for e in encolados if e.historia_run_id is not None)
    typer.echo(f"Se encolaron {abiertos:,} trabajos HISTORIA; los ejecuta un worker.")
    for encolado in encolados:
        typer.echo(
            f"  {_pendiente(encolado.pendiente)}: {encolado.historia_run_id or encolado.nota}"
        )


@app.command("backfill-motor-pagos")
def backfill_motor_pagos(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Solo dice cuanto falta y que encolaria.")
    ] = False,
    reintentar_fallidas: Annotated[
        bool,
        typer.Option(
            "--reintentar-fallidas",
            help="Tambien encola las ventanas cuya interpretacion fallo: las que solo tienen "
            "ejecuciones FALLIDA y las que tienen una reinterpretacion FALLIDA despues de su "
            "vigente.",
        ),
    ] = False,
    reconciliar: Annotated[
        bool,
        typer.Option(
            "--reconciliar",
            help="Tambien reinterpreta las ventanas al dia con movimientos SIN_CUENTA_OBSERVADA "
            "cuyo cliente ya tiene cuenta canonica.",
        ),
    ] = False,
) -> None:
    """Encola la interpretacion (motor-pagos/v1) de los pagos observados que todavia no la tienen:
    los publicados antes de v0.8.0, o cuya interpretacion fallo.

    Trabaja por ventanas, como el motor: un despacho, una cartera y un mes de recepcion. Una
    ventana esta al dia si su interpretacion vigente (su EXITOSA mas reciente) leyo todos sus pagos
    observados. Encola una ejecucion por cada ventana sin interpretacion o desactualizada; la
    ejecuta un worker, en PostgreSQL y sin volver a leer ningun archivo.

    Es idempotente: una ventana ya en la cola no se encola otra vez, y una al dia no se toca. Las
    que solo tienen ejecuciones FALLIDA, y las que tienen una reinterpretacion FALLIDA despues de
    su vigente (que puede no ver un cambio de su contexto), se reintentan con
    --reintentar-fallidas. Las que estan al dia pero tienen movimientos sin cuenta cuyo cliente ya
    llego en un corte se reinterpretan con --reconciliar: es una interpretacion nueva, y la
    anterior queda en el historial.
    """
    from motor_cartera.config import config
    from motor_cartera.db.sesion import sesion
    from motor_cartera.motor_pagos.backfill import diagnosticar, encolar

    with sesion() as s:
        diagnostico = diagnosticar(s)
    fallidas, por_conciliar = diagnostico.solo_fallidas, diagnostico.por_conciliar
    reinterpretaciones = diagnostico.reinterpretacion_fallida
    typer.echo(
        f"Ventanas con pagos observados: {diagnostico.ventanas:,}, con "
        f"{diagnostico.observaciones:,} pagos observados"
    )
    typer.echo(f"  al dia con motor-pagos/v1: {diagnostico.al_dia:,}")
    typer.echo(f"  en la cola (EN_PROCESO): {diagnostico.en_cola:,}")
    typer.echo(f"  sin interpretacion: {len(diagnostico.sin_interpretacion):,}")
    typer.echo(
        "  desactualizadas (con pagos que su interpretacion vigente no ve): "
        f"{len(diagnostico.desactualizadas):,}"
    )
    nota = (
        "" if reintentar_fallidas or not fallidas else "; se reintentan con --reintentar-fallidas"
    )
    typer.echo(f"  solo con ejecuciones FALLIDA: {len(fallidas):,}{nota}")
    for ventana in fallidas:
        typer.echo(f"    {_ventana(ventana)}: {ventana.ultimo_resultado}")
    nota = (
        ""
        if reintentar_fallidas or not reinterpretaciones
        else "; se reintentan con --reintentar-fallidas"
    )
    typer.echo(
        "  con una reinterpretacion FALLIDA despues de su vigente: "
        f"{len(reinterpretaciones):,}{nota}"
    )
    for ventana in reinterpretaciones:
        typer.echo(f"    {_ventana(ventana)}: {ventana.ultimo_resultado}")
    nota = "" if reconciliar or not por_conciliar else "; se reinterpretan con --reconciliar"
    typer.echo(
        "  por conciliar (movimientos sin cuenta cuyo cliente ya tiene una): "
        f"{len(por_conciliar):,}{nota}"
    )
    typer.echo(
        "Pagos observados sin interpretacion vigente: "
        f"{diagnostico.observaciones_sin_interpretacion:,}"
    )
    pendientes = (
        diagnostico.sin_interpretacion
        + diagnostico.desactualizadas
        + (fallidas + reinterpretaciones if reintentar_fallidas else [])
        + (por_conciliar if reconciliar else [])
    )
    if dry_run:
        lista = "; se encolarian:" if pendientes else "."
        typer.echo(f"Faltan {len(pendientes):,} ventanas. Con --dry-run no se encolo nada{lista}")
        for ventana in pendientes:
            typer.echo(f"  {_ventana(ventana)}")
        return
    encoladas = encolar(pendientes, config=config)
    nuevas = sum(1 for e in encoladas if e.nueva)
    typer.echo(f"Se encolaron {nuevas:,} trabajos MOTOR_PAGOS; los ejecuta un worker.")
    for encolada in encoladas:
        nota = "" if encolada.nueva else " (ya estaba en la cola)"
        typer.echo(f"  {_ventana(encolada.ventana)}: {encolada.motor_pagos_run_id}{nota}")


@app.command("backfill-lifecycle")
def backfill_lifecycle(
    as_of: Annotated[
        datetime,
        typer.Option(
            "--as-of",
            formats=["%Y-%m-%d"],
            help="La fecha de corte de las evaluaciones, AAAA-MM-DD. Obligatoria: nunca sale del "
            "reloj.",
        ),
    ],
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Solo dice que falta y que encolaria.")
    ] = False,
    reevaluar: Annotated[
        bool,
        typer.Option(
            "--reevaluar",
            help="Tambien encola las carteras que ya tienen una evaluacion a esa fecha: solo "
            "publica si sus entradas cambiaron.",
        ),
    ] = False,
) -> None:
    """Encola la evaluacion (evaluacion-promesa/v1) de las promesas de cada cartera a la fecha de
    corte --as-of, si todavia no la tiene.

    Lo unico que el lifecycle deriva en lote es la evaluacion de sus promesas: un trabajo
    EVALUACION_PROMESAS por cartera y fecha, nunca uno por promesa, que ejecuta un worker en
    PostgreSQL. Es idempotente: una cartera con una evaluacion EN_PROCESO a esa fecha no se encola
    otra vez, y una que ya tiene una EXITOSA no se toca, salvo con --reevaluar.
    """
    from motor_cartera.config import config
    from motor_cartera.db.sesion import sesion
    from motor_cartera.evaluacion.backfill import diagnosticar, encolar, pendientes

    fecha = as_of.date()
    with sesion() as s:
        carteras = diagnosticar(s, fecha, zona=config.zona_horaria_fuente)
    typer.echo(
        f"Carteras con promesas acordadas hasta el {fecha.isoformat()} (fin del dia en "
        f"{config.zona_horaria_fuente}): {len(carteras):,}"
    )
    for cartera in carteras:
        estado = (
            f"en la cola ({cartera.en_cola})"
            if cartera.en_cola
            else f"evaluada ({cartera.vigente})"
            if cartera.vigente
            else "sin evaluacion a esa fecha"
        )
        typer.echo(
            f"  {cartera.despacho_id}/{cartera.cartera_id}: {cartera.promesas:,} promesas, "
            f"{cartera.vencidas:,} vencidas: {estado}"
        )
    faltan = pendientes(carteras, reevaluar=reevaluar)
    typer.echo(f"Evaluaciones pendientes: {len(faltan):,}")
    if dry_run:
        typer.echo("Con --dry-run no se encolo nada.")
        return
    encoladas = encolar(faltan, fecha, config=config)
    nuevas = sum(1 for e in encoladas if e.nueva)
    typer.echo(f"Se encolaron {nuevas:,} trabajos EVALUACION_PROMESAS; los ejecuta un worker.")
    for encolada in encoladas:
        nota = "" if encolada.nueva else " (ya estaba en la cola)"
        typer.echo(
            f"  {encolada.cartera.despacho_id}/{encolada.cartera.cartera_id}: "
            f"{encolada.evaluacion_run_id}{nota}"
        )


@app.command("backfill-atribucion")
def backfill_atribucion(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Solo dice que falta y que encolaria.")
    ] = False,
    reintentar_fallidas: Annotated[
        bool,
        typer.Option(
            "--reintentar-fallidas",
            help="Tambien encola las ventanas que solo tienen atribuciones FALLIDA.",
        ),
    ] = False,
    ventana_dias: Annotated[
        int | None,
        typer.Option(
            "--ventana-dias",
            min=1,
            max=366,
            help="Cuantos dias antes de un pago puede ocurrir una gestion candidata, para las "
            "ventanas que nunca se han atribuido. Por omision, MC_ATRIBUCION_VENTANA_DIAS. Una "
            "ventana desactualizada conserva la de su atribucion vigente.",
        ),
    ] = None,
) -> None:
    """Encola la atribucion (atribucion/v1) de cada ventana de pagos interpretados que no tiene una
    al dia.

    La atribucion no se abre sola: depende de los pagos que interpreta el motor de pagos y de las
    gestiones que registra la cobranza, y las dos cambian por separado. Una ventana (un despacho,
    una cartera y un mes de recepcion) esta al dia si su atribucion vigente leyo la interpretacion
    vigente de sus pagos y las mismas gestiones de sus cuentas, con las mismas anuladas. Una
    gestion registrada tarde, una anulacion o una interpretacion nueva de los pagos la
    desactualizan: se encola otra atribucion, que publica una nueva sin tocar la anterior.

    Encola un trabajo ATRIBUCION por ventana, nunca uno por pago, que ejecuta un worker en
    PostgreSQL. Es idempotente: una ventana en la cola o al dia no se toca, y una atribucion con
    las mismas entradas que su vigente no publica (YA_ATRIBUIDA). Las ventanas que solo tienen
    atribuciones FALLIDA se reintentan con --reintentar-fallidas.
    """
    from motor_cartera.atribucion.backfill import diagnosticar, encolar
    from motor_cartera.config import config
    from motor_cartera.db.sesion import sesion

    with sesion() as s:
        diagnostico = diagnosticar(s)
    fallidas = diagnostico.solo_fallidas
    typer.echo(
        f"Ventanas con pagos interpretados: {diagnostico.ventanas:,}, con {diagnostico.pagos:,} "
        "pagos"
    )
    typer.echo(f"  al dia con atribucion/v1: {diagnostico.al_dia:,}")
    typer.echo(f"  en la cola (EN_PROCESO): {diagnostico.en_cola:,}")
    typer.echo(f"  sin atribucion: {len(diagnostico.sin_atribucion):,}")
    typer.echo(f"  desactualizadas: {len(diagnostico.desactualizadas):,}")
    for ventana in diagnostico.desactualizadas:
        typer.echo(f"    {_ventana_atribuible(ventana)}: {ventana.motivo}")
    nota = (
        "" if reintentar_fallidas or not fallidas else "; se reintentan con --reintentar-fallidas"
    )
    typer.echo(f"  solo con atribuciones FALLIDA: {len(fallidas):,}{nota}")
    for ventana in fallidas:
        typer.echo(f"    {_ventana_atribuible(ventana)}: {ventana.motivo}")
    typer.echo(f"Pagos sin una atribucion al dia: {diagnostico.pagos_sin_atribucion_al_dia:,}")
    pendientes = (
        diagnostico.sin_atribucion
        + diagnostico.desactualizadas
        + (fallidas if reintentar_fallidas else [])
    )
    if dry_run:
        lista = "; se encolarian:" if pendientes else "."
        typer.echo(f"Faltan {len(pendientes):,} ventanas. Con --dry-run no se encolo nada{lista}")
        for ventana in pendientes:
            typer.echo(f"  {_ventana_atribuible(ventana)}")
        return
    encoladas = encolar(pendientes, config=config, ventana_dias=ventana_dias)
    nuevas = sum(1 for e in encoladas if e.nueva)
    typer.echo(f"Se encolaron {nuevas:,} trabajos ATRIBUCION; los ejecuta un worker.")
    for encolada in encoladas:
        nota = "" if encolada.nueva else " (ya estaba en la cola)"
        typer.echo(
            f"  {_ventana_atribuible(encolada.ventana)}: {encolada.atribucion_run_id}, ventana de "
            f"{encolada.ventana_dias} dias{nota}"
        )


@app.command("decidir-v2")
def decidir_v2(
    corte: Annotated[
        datetime,
        typer.Option(
            "--corte",
            formats=["%Y-%m-%d"],
            help="La fecha del corte canonico cuyas cuentas se deciden: tambien su as_of.",
        ),
    ],
    known_at: Annotated[
        datetime | None,
        typer.Option(
            "--known-at",
            formats=["%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"],
            help="La frontera de conocimiento, con su zona: 2026-09-17T08:00:00-06:00. Sin ella, "
            "el reloj de la base al abrir. Obligatoria con --backtest.",
        ),
    ] = None,
    backtest: Annotated[
        bool,
        typer.Option(
            "--backtest",
            help="Decide un corte que ya no es el mas reciente, con el known_at que se declare.",
        ),
    ] = False,
    politica: Annotated[
        str | None,
        typer.Option("--politica", help="Por omision, decision-policy/public-v1."),
    ] = None,
) -> None:
    """Decide las cuentas de un corte con decision/v2, en primer plano y sin la API.

    Usa solo lo que se conocia en known_at de lo que paso hasta el final del dia del corte. Pasa por
    la cola durable, como la API, pero con el trabajo ya de este proceso: si se interrumpe, un
    worker la termina cuando vence su lease, con el mismo known_at. Corre en paralelo a
    decision/v1: no cambia el flujo operacional. Termina con codigo 1 si no publico.
    """
    from sqlmodel import select

    from motor_cartera.config import config
    from motor_cartera.db.modelos import CorteCanonico, EstadoDecisionV2, ModoDecisionV2
    from motor_cartera.db.sesion import sesion
    from motor_cartera.decision_v2.ejecuciones import (
        DecisionV2EnProceso,
        DecisionV2YaGenerada,
        EntradaNoDecidible,
    )
    from motor_cartera.orquestacion.worker import decidir_v2_en_primer_plano

    fecha = corte.date()
    with sesion() as s:
        corte_id = s.exec(
            select(CorteCanonico.corte_id).where(
                CorteCanonico.despacho_id == config.despacho_id,
                CorteCanonico.cartera_id == config.cartera_id,
                CorteCanonico.fecha_corte == fecha,
            )
        ).first()
    if corte_id is None:
        typer.echo(
            f"No hay un corte canonico del {fecha.isoformat()} en "
            f"{config.despacho_id}/{config.cartera_id}.",
            err=True,
        )
        raise typer.Exit(code=1)
    modo = ModoDecisionV2.BACKTEST if backtest else ModoDecisionV2.OPERATIVO
    try:
        ejecucion = decidir_v2_en_primer_plano(
            corte_id, known_at=known_at, modo=modo, version_politica=politica
        )
    except (EntradaNoDecidible, DecisionV2EnProceso, DecisionV2YaGenerada) as exc:
        codigo = getattr(exc, "codigo", type(exc).__name__)
        typer.echo(f"{codigo}: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Decision v2 {ejecucion.decision_run_id}: {ejecucion.estado}")
    typer.echo(
        f"  corte {fecha.isoformat()}, as_of {ejecucion.as_of.isoformat()}, known_at "
        f"{ejecucion.known_at.isoformat()} ({modo})"
    )
    typer.echo(
        f"  {ejecucion.version_motor} | {ejecucion.version_features} | "
        f"{ejecucion.version_politica} ({ejecucion.hash_politica[:12]}...)"
    )
    typer.echo(f"  firma de entrada {ejecucion.firma_inputs}")
    typer.echo(f"  {ejecucion.detalle}")
    if ejecucion.disponibilidad:
        for familia in ("pagos", "atribucion"):
            ventanas = ", ".join(
                f"{v} {'si' if ok else 'NO'}"
                for v, ok in ejecucion.disponibilidad.get(familia, {}).items()
            )
            typer.echo(f"  {familia} disponibles a known_at: {ventanas}")
    if ejecucion.estado != EstadoDecisionV2.EXITOSA:
        raise typer.Exit(code=1)


@app.command("comparar-decisiones")
def comparar_decisiones(
    corte: Annotated[
        datetime,
        typer.Option("--corte", formats=["%Y-%m-%d"], help="La fecha del corte canonico."),
    ],
) -> None:
    """Compara, en modo sombra, la decision v2 EXITOSA mas reciente de un corte con la decision/v1
    de la corrida que lo publico: cuantas prioridades y canales coinciden y cuantos no, y por que
    puede ser (el ajuste por saldo de v1, los modificadores de v2). Es un diagnostico: no dice
    cual es mejor, y no se usa para ajustar la politica."""
    from sqlmodel import select

    from motor_cartera.config import config
    from motor_cartera.db.modelos import CorteCanonico, EjecucionDecisionV2, EstadoDecisionV2
    from motor_cartera.db.sesion import sesion
    from motor_cartera.decision_v2.sombra import comparar

    fecha = corte.date()
    with sesion() as s:
        ejecucion = s.exec(
            select(EjecucionDecisionV2)
            .join(CorteCanonico, CorteCanonico.id == EjecucionDecisionV2.corte_canonico_id)
            .where(
                CorteCanonico.despacho_id == config.despacho_id,
                CorteCanonico.cartera_id == config.cartera_id,
                CorteCanonico.fecha_corte == fecha,
                EjecucionDecisionV2.estado == EstadoDecisionV2.EXITOSA,
            )
            .order_by(EjecucionDecisionV2.known_at.desc(), EjecucionDecisionV2.id.desc())
        ).first()
        if ejecucion is None:
            typer.echo(
                f"El corte del {fecha.isoformat()} no tiene una decision v2 EXITOSA.", err=True
            )
            raise typer.Exit(code=1)
        c = comparar(s, ejecucion)
    typer.echo(f"decision/v2 {c.decision_v2} contra decision/v1 {c.decision_v1 or '(ninguna)'}")
    typer.echo(
        f"  cuentas: v2 {c.cuentas_v2:,}, v1 {c.cuentas_v1:,}, comparadas {c.comparadas:,}, "
        f"solo en v2 {c.solo_v2:,}, solo en v1 {c.solo_v1:,}"
    )
    typer.echo(
        f"  prioridad: igual {c.prioridad_igual:,}, v2 mayor {c.v2_mayor:,}, v2 menor "
        f"{c.v2_menor:,}"
    )
    typer.echo(f"  canal: igual {c.canal_igual:,}, distinto {c.canal_distinto:,}")
    typer.echo(
        f"  v1 subio por saldo (SALDO_ALTO, fuera de public-v1): {c.v1_con_saldo_alto:,}; base de "
        f"v2 igual a la final de v1: {c.base_v2_igual_a_v1:,}"
    )
    for codigo, cuantas in c.modificadores.items():
        typer.echo(f"  {codigo}: {cuantas:,}")
    for fila, columnas in c.matriz.items():
        typer.echo(f"  v1 {fila:>8} -> " + ", ".join(f"{p} {n:,}" for p, n in columnas.items()))
    typer.echo("Es un diagnostico de diferencias, no un juicio de calidad.")


def _coordenadas(puntos: str | None, proporciones: str | None):
    """Lo que el generador necesita para dar coordenadas, o None si no se pidio una muestra."""
    from motor_cartera.generador.coordenadas import Coordenadas, MuestraDePuntos, Proporciones

    if puntos is None:
        if proporciones is not None:
            typer.echo("--proporciones se usa con --puntos.", err=True)
            raise typer.Exit(code=2)
        return None
    try:
        reparto = Proporciones() if proporciones is None else Proporciones.de_texto(proporciones)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    return Coordenadas(MuestraDePuntos.leer(puntos), reparto)


@app.command("importar-geografia")
def importar_geografia(
    archivo: Annotated[
        Path, typer.Argument(exists=True, dir_okay=False, help="El zip oficial, o un .shp.")
    ],
    referencia: Annotated[
        str | None,
        typer.Option(
            "--referencia",
            help="Una referencia del manifiesto (inegi-mg-2025-municipal): el archivo tiene que "
            "tener su SHA-256, y de ahi salen su producto, su edicion y su capa.",
        ),
    ] = None,
    capa: Annotated[
        str | None,
        typer.Option(help="El miembro del zip sin extension, como conjunto_de_datos/00mun."),
    ] = None,
    proveedor: Annotated[
        str | None, typer.Option(help="Sin --referencia: INEGI, SINTETICO...")
    ] = None,
    producto: Annotated[
        str | None, typer.Option(help="Sin --referencia: el nombre exacto.")
    ] = None,
    edicion: Annotated[str | None, typer.Option(help="Sin --referencia: la edicion.")] = None,
    url: Annotated[str | None, typer.Option(help="De donde se descargo.")] = None,
    licencia: Annotated[str | None, typer.Option(help="Sus condiciones de uso.")] = None,
) -> None:
    """Importa una referencia geografica publica (la capa de municipios de un Shapefile) como una
    version nueva: valida su sistema de coordenadas por su .prj, normaliza a EPSG:4326, comprueba
    cada geometria, repara solo lo seguro (y lo registra), y publica todo o nada. El archivo no se
    modifica ni se copia al almacen: se identifica por su SHA-256. Termina con codigo 1 si no
    publico."""
    from motor_cartera.geografia import referencia as importador
    from motor_cartera.geografia.referencia import Metadatos

    metadatos = None
    if referencia is None:
        if not (proveedor and producto and edicion):
            typer.echo(
                "Sin --referencia, una importacion declara --proveedor, --producto y --edicion.",
                err=True,
            )
            raise typer.Exit(code=2)
        adicionales = {k: v for k, v in (("url", url), ("licencia", licencia)) if v}
        metadatos = Metadatos(proveedor, producto, edicion, adicionales=adicionales)
    try:
        version = importador.importar(
            archivo, capa=capa, referencia=referencia, metadatos=metadatos
        )
    except importador.GeografiaYaImportada as exc:
        typer.echo(f"{exc}", err=True)
        typer.echo(f"geografia_version_id {exc.previa.geografia_version_id}")
        raise typer.Exit(code=1) from exc
    except (
        importador.ImportacionRechazada,
        importador.GeografiaEnImportacion,
        importador.ImportacionFallida,
    ) as exc:
        typer.echo(f"{type(exc).__name__}: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Referencia {version.geografia_version_id}: {version.estado}")
    typer.echo(f"  {version.proveedor}, {version.producto} ({version.edicion}), {version.capa}")
    typer.echo(f"  sha256 {version.sha256} ({version.tamano_bytes:,} bytes)")
    typer.echo(f"  {version.detalle}")
    typer.echo(f"geografia_version_id {version.geografia_version_id}")


@app.command("generar-geografia-sintetica")
def generar_geografia_sintetica(
    destino: Annotated[
        str, typer.Option(help="El zip que se escribe.")
    ] = "datos/geografia/municipios_sinteticos.zip",
    entidades: Annotated[
        str,
        typer.Option(
            help="Las entidades cuyos municipios lleva, separadas por comas. Por omision, las del "
            "generador de carteras."
        ),
    ] = "",
) -> None:
    """Escribe una referencia geografica SINTETICA: un rectangulo por municipio de esas entidades,
    en una cuadricula inventada, con su clave real del catalogo (para que la fuente se pueda
    comparar) y una geometria que no es ningun limite real. Para la prueba de humo y el CI, que no
    descargan nada del INEGI."""
    from motor_cartera.fuentes.geografia import catalogo
    from motor_cartera.generador.oficial import PESO_ENTIDAD
    from motor_cartera.geografia.sintetica import referencia_de_humo

    elegidas = [e.strip() for e in entidades.split(",") if e.strip()] or list(PESO_ENTIDAD)
    claves = [
        e.cve_entidad + m.cve_municipio
        for e in catalogo().entidades
        if e.cve_entidad in elegidas
        for m in e.municipios
    ]
    ruta = referencia_de_humo(destino, claves)
    typer.echo(f"Escrito: {ruta} ({len(claves):,} municipios sinteticos de {', '.join(elegidas)})")


@app.command("muestrear-puntos")
def muestrear_puntos(
    geografia_version: Annotated[
        str, typer.Option("--geografia-version", help="La version PUBLICADA, por su UUID.")
    ],
    destino: Annotated[
        str, typer.Option(help="El JSON que se escribe.")
    ] = "datos/geografia/puntos.json",
    por_municipio: Annotated[int, typer.Option(min=1, help="Puntos por municipio.")] = 20,
    fuera: Annotated[int, typer.Option(min=1, help="Puntos fuera de la referencia.")] = 200,
    semilla: Annotated[int, typer.Option(min=1)] = 7,
) -> None:
    """Escribe una muestra de puntos dentro de cada municipio de una version importada, y fuera de
    ella, con PostGIS y una semilla: el insumo de --puntos en generar-oficial y generar-escenario.
    Son puntos de la cartografia, no ubicaciones de nadie."""
    from uuid import UUID

    from motor_cartera.db.sesion import sesion
    from motor_cartera.geografia import consultas, muestra

    with sesion() as s:
        try:
            version = consultas.version(s, UUID(geografia_version))
        except consultas.VersionNoEncontrada as exc:
            typer.echo(f"No existe la version {geografia_version}.", err=True)
            raise typer.Exit(code=1) from exc
        try:
            datos = muestra.muestrear(
                s, version, por_municipio=por_municipio, fuera=fuera, semilla=semilla
            )
        except muestra.MuestraImposible as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc
    ruta = muestra.escribir(datos, destino)
    puntos = sum(len(p) for p in datos["municipios"].values())
    typer.echo(
        f"Escrito: {ruta} ({puntos:,} puntos en {len(datos['municipios']):,} municipios, "
        f"{len(datos['fuera']):,} fuera; semilla {semilla})"
    )


@app.command("cargar-bases")
def cargar_bases(
    archivo: Annotated[
        Path, typer.Argument(exists=True, dir_okay=False, help="Un JSON, o un JSONL con --nombre.")
    ],
    nombre: Annotated[
        str | None,
        typer.Option(help="El nombre de la version; obligatorio en un JSONL (una base por linea)."),
    ] = None,
) -> None:
    """Publica una version de la configuracion de bases operativas: cada base con su codigo, su
    nombre, su punto, si esta activa y los municipios que puede atender. El mismo contenido es la
    misma version: si ya existe, la reutiliza. Termina con codigo 1 si la configuracion no es
    valida."""
    import json

    from motor_cartera.bases.configuracion import (
        ConfiguracionInvalida,
        leer,
        leer_jsonl,
        publicar,
    )
    from motor_cartera.db.sesion import sesion

    texto = archivo.read_text(encoding="utf-8")
    try:
        if archivo.suffix.lower() == ".jsonl":
            if not nombre:
                typer.echo("Un JSONL de bases necesita --nombre.", err=True)
                raise typer.Exit(code=2)
            configuracion = leer_jsonl(texto, nombre)
        else:
            documento = json.loads(texto)
            if nombre and isinstance(documento, dict):
                documento = {**documento, "nombre": nombre}
            configuracion = leer(documento)
    except json.JSONDecodeError as exc:
        typer.echo(f"{archivo.name} no es JSON: {exc.msg}.", err=True)
        raise typer.Exit(code=1) from exc
    except ConfiguracionInvalida as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    with sesion() as s:
        publicada = publicar(s, configuracion)
        s.commit()
        v = publicada.version
        s.refresh(v)
        accion = "publicada" if publicada.creada else "ya existia: se reutiliza"
        typer.echo(f"Bases {v.bases_version_id}: {accion}")
        typer.echo(
            f"  {v.nombre}: {v.bases} bases ({v.activas} activas), {v.coberturas} coberturas"
        )
        typer.echo(f"  hash {v.hash_configuracion} ({v.version_configuracion})")
        typer.echo(f"bases_version_id {v.bases_version_id}")


def _corte_por_fecha(fecha):
    """El corte_id del corte de esa fecha en la cartera del sistema; si no hay, codigo 1."""
    from sqlmodel import select

    from motor_cartera.config import config
    from motor_cartera.db.modelos import CorteCanonico
    from motor_cartera.db.sesion import sesion

    with sesion() as s:
        corte_id = s.exec(
            select(CorteCanonico.corte_id).where(
                CorteCanonico.despacho_id == config.despacho_id,
                CorteCanonico.cartera_id == config.cartera_id,
                CorteCanonico.fecha_corte == fecha,
            )
        ).first()
    if corte_id is None:
        typer.echo(
            f"No hay un corte canonico del {fecha.isoformat()} en "
            f"{config.despacho_id}/{config.cartera_id}.",
            err=True,
        )
        raise typer.Exit(code=1)
    return corte_id


@app.command()
def geolocalizar(
    corte: Annotated[
        datetime, typer.Option("--corte", formats=["%Y-%m-%d"], help="La fecha del corte.")
    ],
    geografia_version: Annotated[
        str,
        typer.Option(
            "--geografia-version", help="La version PUBLICADA de la referencia, por su UUID."
        ),
    ],
) -> None:
    """Resuelve la geografia de las cuentas de un corte con geografia/v1 y una version declarada
    de la referencia, en primer plano y sin la API. Pasa por la cola durable, con el trabajo de
    este proceso. No imprime ninguna coordenada. Termina con codigo 1 si no publico."""
    from uuid import UUID

    from motor_cartera.db.modelos import EstadoGeografia
    from motor_cartera.geografia.ejecuciones import (
        EntradaNoGeolocalizable,
        GeografiaEnProceso,
        GeografiaYaGenerada,
    )
    from motor_cartera.orquestacion.worker import geolocalizar_en_primer_plano

    corte_id = _corte_por_fecha(corte.date())
    try:
        e = geolocalizar_en_primer_plano(corte_id, UUID(geografia_version))
    except (EntradaNoGeolocalizable, GeografiaEnProceso, GeografiaYaGenerada) as exc:
        codigo = getattr(exc, "codigo", type(exc).__name__)
        typer.echo(f"{codigo}: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Geografia {e.geografia_run_id}: {e.estado}")
    typer.echo(f"  firma de entrada {e.firma_inputs}")
    typer.echo(f"  {e.detalle}")
    typer.echo(f"geografia_run_id {e.geografia_run_id}")
    if e.estado != EstadoGeografia.EXITOSA:
        raise typer.Exit(code=1)


@app.command("asignar-territorial-v2")
def asignar_territorial_v2(
    corte: Annotated[
        datetime, typer.Option("--corte", formats=["%Y-%m-%d"], help="La fecha del corte.")
    ],
    geografia_run: Annotated[
        str, typer.Option("--geografia-run", help="Una geografia EXITOSA de ese corte.")
    ],
    bases: Annotated[str, typer.Option("--bases", help="La version de bases, por su UUID.")],
    politica: Annotated[
        str | None, typer.Option("--politica", help="Por omision, territorial-public-v2.")
    ] = None,
) -> None:
    """Asigna las cuentas de un corte a sus bases operativas con territorial/v2, en primer plano y
    sin la API, con entradas declaradas: el corte, su geografia y la version de bases. Corre en
    paralelo a territorial/v1. Termina con codigo 1 si no publico."""
    from uuid import UUID

    from motor_cartera.db.modelos import EstadoTerritorialV2
    from motor_cartera.orquestacion.worker import asignar_territorial_v2_en_primer_plano
    from motor_cartera.territorial_v2.ejecuciones import (
        EntradaNoAsignable,
        TerritorialV2EnProceso,
        TerritorialV2YaGenerado,
    )

    corte_id = _corte_por_fecha(corte.date())
    try:
        e = asignar_territorial_v2_en_primer_plano(
            corte_id, UUID(geografia_run), UUID(bases), version_politica=politica
        )
    except (EntradaNoAsignable, TerritorialV2EnProceso, TerritorialV2YaGenerado) as exc:
        codigo = getattr(exc, "codigo", type(exc).__name__)
        typer.echo(f"{codigo}: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"territorial/v2 {e.territorial_run_id}: {e.estado}")
    typer.echo(f"  {e.version_politica} ({e.hash_politica[:12]}...), firma {e.firma_inputs}")
    typer.echo(f"  {e.detalle}")
    typer.echo(f"territorial_run_id {e.territorial_run_id}")
    if e.estado != EstadoTerritorialV2.EXITOSA:
        raise typer.Exit(code=1)


@app.command("cargar-campo")
def cargar_campo(
    archivo: Annotated[
        Path,
        typer.Argument(
            exists=True, dir_okay=False, help="Un JSON, o un JSONL con un registro por linea."
        ),
    ],
) -> None:
    """Publica una version de la configuracion de campo: valida el documento entero (fail-closed,
    con todos sus problemas), lo lleva a su forma canonica, calcula su hash y la publica, o
    reutiliza la version que ya tiene ese mismo contenido. Termina con codigo 1 si no es valida."""
    import json

    from motor_cartera.campo_v2.configuracion import (
        ConfiguracionInvalida,
        leer,
        leer_jsonl,
        publicar,
    )
    from motor_cartera.db.sesion import sesion

    texto = archivo.read_text(encoding="utf-8")
    try:
        if archivo.suffix.lower() == ".jsonl":
            configuracion = leer_jsonl(texto)
        else:
            configuracion = leer(json.loads(texto))
        with sesion() as s:
            publicada = publicar(s, configuracion)
            s.commit()
            v = publicada.version
            s.refresh(v)
    except json.JSONDecodeError as exc:
        typer.echo(f"{archivo.name} no es JSON: {exc.msg}.", err=True)
        raise typer.Exit(code=1) from exc
    except ConfiguracionInvalida as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    accion = "publicada" if publicada.creada else "ya existia: se reutiliza"
    typer.echo(f"Configuracion de campo {v.configuracion_campo_version_id}: {accion}")
    typer.echo(
        f"  {v.nombre}: {v.gestores:,} gestores ({v.gestores_activos:,} activos), "
        f"{v.calendarios} calendarios, {v.ventanas} ventanas, {v.excepciones} excepciones"
    )
    typer.echo(f"  capacidad declarada {v.capacidad_declarada:,} cuentas al dia; {v.zona_horaria}")
    for codigo, b in v.metadatos.get("gestores_por_base", {}).items():
        typer.echo(
            f"  {codigo}: {b['gestores']} gestores, {b['activos']} activos, cupo "
            f"{b['capacidad_declarada']:,}"
        )
    typer.echo(f"  hash {v.hash_configuracion} ({v.version_configuracion})")
    typer.echo(f"configuracion_campo_version_id {v.configuracion_campo_version_id}")


def _lista_de_fechas(texto: str | None) -> list:
    from datetime import date as fecha

    return [fecha.fromisoformat(f.strip()) for f in (texto or "").split(",") if f.strip()]


@app.command("generar-campo-sintetico")
def generar_campo_sintetico(
    bases: Annotated[
        str, typer.Option("--bases", help="La version de bases, por su UUID: la de los gestores.")
    ],
    destino: Annotated[str, typer.Option(help="El JSON que se escribe.")] = "datos/campo.json",
    gestores_por_base: Annotated[
        int, typer.Option(min=0, help="Cuantos gestores en cada base activa.")
    ] = 5,
    base: Annotated[
        list[str] | None,
        typer.Option(
            "--base", help="CODIGO=N: otro numero de gestores para esa base. Se puede repetir."
        ),
    ] = None,
    capacidades: Annotated[
        str, typer.Option(help="Los cupos posibles, separados por comas.")
    ] = "15,20,25",
    inactivos: Annotated[float, typer.Option(min=0, max=0.99)] = 0.05,
    festivos: Annotated[
        str | None, typer.Option(help="Fechas cerradas en todos los calendarios: AAAA-MM-DD,...")
    ] = None,
    medias_jornadas: Annotated[
        str | None, typer.Option(help="Fechas de media jornada (09:00 a 13:00): AAAA-MM-DD,...")
    ] = None,
    semilla: Annotated[int, typer.Option(min=1)] = 7,
) -> None:
    """Escribe una configuracion de campo SINTETICA para las bases activas de una version: gestores
    con codigos inventados (GESTOR_0001...), cupos de una lista declarada, tres calendarios publicos
    y las excepciones que se declaren, determinista por semilla. Nada sale de personas reales.
    Despues se publica con cargar-campo."""
    import json
    from uuid import UUID

    from sqlmodel import select

    from motor_cartera.db.modelos import BaseOperativa, VersionBasesOperativas
    from motor_cartera.db.sesion import sesion
    from motor_cartera.generador.campo import configuracion_sintetica

    with sesion() as s:
        version = s.exec(
            select(VersionBasesOperativas).where(
                VersionBasesOperativas.bases_version_id == UUID(bases)
            )
        ).one_or_none()
        if version is None:
            typer.echo(f"No existe la version de bases {bases}.", err=True)
            raise typer.Exit(code=1)
        activas = list(
            s.exec(
                select(BaseOperativa.codigo)
                .where(
                    BaseOperativa.version_bases_operativas_id == version.id, BaseOperativa.activa
                )
                .order_by(BaseOperativa.codigo)
            ).all()
        )
    por_base: dict[str, int] = dict.fromkeys(activas, gestores_por_base)
    for pareja in base or []:
        codigo, _, cuantos = pareja.partition("=")
        if codigo not in por_base or not cuantos.isdigit():
            typer.echo(f"--base {pareja}: CODIGO=N de una base activa de la version.", err=True)
            raise typer.Exit(code=2)
        por_base[codigo] = int(cuantos)
    try:
        documento = configuracion_sintetica(
            activas,
            bases_version_id=bases,
            gestores_por_base=por_base,
            semilla=semilla,
            capacidades=[int(c) for c in capacidades.split(",")],
            inactivos=inactivos,
            festivos=_lista_de_fechas(festivos),
            medias_jornadas=_lista_de_fechas(medias_jornadas),
        )
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    ruta = Path(destino)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(documento, ensure_ascii=False, indent=1), encoding="utf-8")
    typer.echo(
        f"Escrito: {ruta} ({len(documento['gestores']):,} gestores en {len(activas)} bases; "
        f"semilla {semilla})"
    )


def _fecha_obligatoria(fecha: datetime | None, comando: str):
    if fecha is None:
        typer.echo(
            f"{comando} no infiere la fecha de operacion: declarala con --fecha-operacion.",
            err=True,
        )
        raise typer.Exit(code=2)
    return fecha.date()


@app.command("planificar-campo-v2")
def planificar_campo_v2(
    corte: Annotated[
        datetime, typer.Option("--corte", formats=["%Y-%m-%d"], help="La fecha del corte.")
    ],
    decision_run: Annotated[
        str, typer.Option("--decision-run", help="Una decision/v2 EXITOSA de ese corte.")
    ],
    territorial_run: Annotated[
        str, typer.Option("--territorial-run", help="Una territorial/v2 EXITOSA de ese corte.")
    ],
    configuracion: Annotated[
        str, typer.Option("--configuracion", help="La configuracion de campo, por su UUID.")
    ],
    fecha_operacion: Annotated[
        datetime | None,
        typer.Option("--fecha-operacion", formats=["%Y-%m-%d"], help="El dia que se planea."),
    ] = None,
    politica: Annotated[
        str | None, typer.Option("--politica", help="Por omision, campo-public-v2.")
    ] = None,
) -> None:
    """Planea el trabajo de campo de una fecha con campo/v2, en primer plano y sin la API, con todas
    sus entradas declaradas: el corte, la decision/v2, la territorial/v2, la configuracion de campo
    y la fecha. Pasa por la cola durable, con el trabajo de este proceso. Termina con codigo 1 si
    no publico."""
    from uuid import UUID

    from motor_cartera.campo_v2.ejecuciones import (
        CampoV2EnProceso,
        CampoV2YaGenerado,
        EntradaNoPlanificable,
    )
    from motor_cartera.db.modelos import EstadoCampoV2
    from motor_cartera.orquestacion.worker import planificar_campo_v2_en_primer_plano

    dia = _fecha_obligatoria(fecha_operacion, "planificar-campo-v2")
    corte_id = _corte_por_fecha(corte.date())
    try:
        e = planificar_campo_v2_en_primer_plano(
            corte_id,
            UUID(decision_run),
            UUID(territorial_run),
            UUID(configuracion),
            dia,
            version_politica=politica,
        )
    except (EntradaNoPlanificable, CampoV2EnProceso, CampoV2YaGenerado) as exc:
        codigo = getattr(exc, "codigo", type(exc).__name__)
        typer.echo(f"{codigo}: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"campo/v2 {e.campo_run_id}: {e.estado}")
    typer.echo(f"  {e.version_politica} ({e.hash_politica[:12]}...), firma {e.firma_inputs}")
    typer.echo(f"  {e.detalle}")
    typer.echo(f"campo_run_id {e.campo_run_id}")
    if e.estado != EstadoCampoV2.EXITOSA:
        raise typer.Exit(code=1)


@app.command("resumen-campo-v2")
def resumen_campo_v2(
    plan: Annotated[str, typer.Option("--plan", help="El campo_run_id del plan.")],
) -> None:
    """Resume un plan de campo/v2: sus entradas, sus conteos, la capacidad usada, cada base y el
    balance entre los gestores disponibles. Sin coordenadas."""
    from uuid import UUID

    from motor_cartera.campo_v2 import consultas
    from motor_cartera.db.sesion import sesion

    with sesion() as s:
        try:
            v = consultas.obtener(s, UUID(plan))
        except consultas.PlanNoEncontrado as exc:
            typer.echo(f"No existe el plan {plan}.", err=True)
            raise typer.Exit(code=1) from exc
    e = v.ejecucion
    typer.echo(f"campo/v2 {e.campo_run_id}: {e.estado} ({e.resultado})")
    typer.echo(
        f"  corte {v.fecha_corte.isoformat()}, fecha de operacion "
        f"{e.fecha_operacion.isoformat()}, decision {v.decision_run_id}, territorial "
        f"{v.territorial_run_id}"
    )
    typer.echo(
        f"  configuracion {v.configuracion.configuracion_campo_version_id} "
        f"({v.configuracion.nombre}, {v.configuracion.zona_horaria})"
    )
    typer.echo(
        f"  {e.candidatas_campo:,} candidatas de {e.cuentas_evaluadas_decision:,} cuentas: "
        f"{e.asignadas:,} asignadas ({e.rebalanceadas:,} por spillover), "
        f"{e.revision_territorial:,} en revision territorial, {e.sin_gestor:,} sin gestor, "
        f"{e.sin_capacidad:,} sin capacidad"
    )
    typer.echo(f"  capacidad: {e.capacidad_utilizada:,} de {e.capacidad_total:,}")
    distribucion = e.distribucion or {}
    for codigo, b in distribucion.get("por_base", {}).items():
        typer.echo(
            f"  {codigo}: {b['candidatas']:,} candidatas, {b['asignadas']:,} asignadas, "
            f"{b['sin_gestor']:,} sin gestor, {b['sin_capacidad']:,} sin capacidad; "
            f"{b['gestores_disponibles']}/{b['gestores']} gestores, capacidad {b['capacidad']:,}, "
            f"utilizacion {b['utilizacion']}"
        )
    unidades = distribucion.get("unidades", {})
    if unidades:
        typer.echo("  unidades: " + ", ".join(f"{n} {c:,}" for n, c in unidades.items()))
    for nombre, valores in distribucion.get("balance", {}).items():
        if isinstance(valores, dict):
            typer.echo(
                f"  {nombre}: minimo {valores['minimo']}, mediana {valores['mediana']}, p95 "
                f"{valores['p95']}, maximo {valores['maximo']}"
            )
    typer.echo("Capacidad declarada, no factibilidad vial: las rutas son de v0.13.")


@app.command("backfill-campo-v2")
def backfill_campo_v2(
    configuracion: Annotated[
        str, typer.Option("--configuracion", help="La configuracion de campo, por su UUID.")
    ],
    fecha_operacion: Annotated[
        datetime | None,
        typer.Option(
            "--fecha-operacion",
            formats=["%Y-%m-%d"],
            help="El dia que se planea: obligatoria, no se infiere.",
        ),
    ] = None,
    corte: Annotated[
        datetime | None,
        typer.Option("--corte", formats=["%Y-%m-%d"], help="Solo ese corte."),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Solo dice que falta; no encola nada.")
    ] = False,
) -> None:
    """Los planes de campo/v2 que faltan para una configuracion y una fecha de operacion
    declaradas: de cada corte hasta esa fecha que tiene exactamente una decision/v2 EXITOSA y una
    territorial/v2 EXITOSA sobre la version de bases de la configuracion, y todavia no tiene un plan
    con esas entradas. Un corte con mas de una es ambiguo: se reporta y no se planea. Es
    idempotente."""
    from uuid import UUID

    from motor_cartera.campo_v2 import consultas
    from motor_cartera.campo_v2.backfill import encolar, revisar
    from motor_cartera.config import config
    from motor_cartera.db.sesion import sesion

    dia = _fecha_obligatoria(fecha_operacion, "backfill-campo-v2")
    with sesion() as s:
        try:
            version = consultas.configuracion(s, UUID(configuracion))
        except consultas.ConfiguracionNoEncontrada as exc:
            typer.echo(f"No existe la configuracion de campo {configuracion}.", err=True)
            raise typer.Exit(code=1) from exc
        revision = revisar(
            s,
            version,
            dia,
            despacho_id=config.despacho_id,
            cartera_id=config.cartera_id,
            corte=None if corte is None else corte.date(),
        )
        typer.echo(
            f"Planes de campo/v2 para el {dia.isoformat()} con la configuracion "
            f"{version.configuracion_campo_version_id}:"
        )
        typer.echo(
            f"  ya planeados: {revision.planeados}; en proceso: {revision.en_proceso}; "
            f"cortes sin decision/v2 o territorial/v2 EXITOSA: {revision.sin_entradas}"
        )
        typer.echo(f"  cortes ambiguos (no se planean): {len(revision.ambiguos)}")
        for a in revision.ambiguos:
            typer.echo(
                f"    {a.fecha_corte.isoformat()}: {a.decisiones} decisiones v2 y "
                f"{a.territoriales} territoriales v2 EXITOSA; declara las entradas con "
                "planificar-campo-v2"
            )
        typer.echo(f"  Planes pendientes: {len(revision.pendientes)}")
        for p in revision.pendientes:
            typer.echo(
                f"    {p.fecha_corte.isoformat()}: decision {p.decision_run_id}, territorial "
                f"{p.territorial_run_id}"
            )
        if dry_run:
            typer.echo("Con --dry-run no se encolo nada.")
            return
        abiertos = encolar(s, revision.pendientes, version, dia, config=config)
        s.commit()
        typer.echo(f"Se encolaron {len(abiertos)} trabajos CAMPO_V2; los ejecuta un worker.")
        for e in abiertos:
            typer.echo(f"  campo_run_id {e.campo_run_id}")


@app.command("exportar-plan-campo")
def exportar_plan_campo(
    plan: Annotated[str, typer.Option("--plan", help="El campo_run_id de un plan EXITOSA.")],
    destino: Annotated[str, typer.Option(help="El CSV que se escribe.")] = "datos/plan_campo.csv",
) -> None:
    """Escribe en CSV la asignacion de cada candidata de un plan, para inspeccionarla: su cuenta_id
    publico, su estado, su prioridad, su lugar en la cola de su base, su base, su gestor y las
    cuatro unidades de su particion. Sin coordenadas, direcciones, nombres ni telefonos."""
    from uuid import UUID

    from motor_cartera.campo_v2 import consultas
    from motor_cartera.db.modelos import EstadoCampoV2
    from motor_cartera.db.sesion import sesion

    ruta = Path(destino)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with sesion() as s:
        try:
            v = consultas.obtener(s, UUID(plan))
        except consultas.PlanNoEncontrado as exc:
            typer.echo(f"No existe el plan {plan}.", err=True)
            raise typer.Exit(code=1) from exc
        if v.ejecucion.estado != EstadoCampoV2.EXITOSA:
            typer.echo(f"El plan {plan} esta {v.ejecucion.estado}: no publico nada.", err=True)
            raise typer.Exit(code=1)
        conexion = s.connection().connection.driver_connection
        consulta = (
            "COPY (SELECT c.cuenta_id, a.estado, a.prioridad, a.orden_prioridad, "
            "b.codigo AS base, g.codigo AS gestor, a.zona_id, a.subzona_id, a.tramo_id, "
            "a.microtramo_id, a.es_rebalanceada FROM asignacion_campo_cuenta a "
            "JOIN cuenta_canonica c ON c.id = a.cuenta_canonica_id "
            "LEFT JOIN base_operativa b ON b.id = a.base_operativa_id "
            "LEFT JOIN gestor_campo g ON g.id = a.gestor_campo_id "
            f"WHERE a.ejecucion_campo_v2_id = {int(v.ejecucion.id)} "
            "ORDER BY b.codigo NULLS LAST, g.codigo NULLS LAST, a.orden_prioridad, c.cuenta_id) "
            "TO STDOUT WITH (FORMAT csv, HEADER true)"
        )
        with conexion.cursor() as cursor, ruta.open("wb") as salida:
            with cursor.copy(consulta) as copia:
                for bloque in copia:
                    salida.write(bloque)
    typer.echo(f"Escrito: {ruta} ({v.ejecucion.candidatas_campo:,} candidatas del plan {plan})")


def _ventana_atribuible(ventana) -> str:
    """Una ventana de la atribucion, como la lee una persona."""
    return (
        f"{ventana.despacho_id}/{ventana.cartera_id} {ventana.desde:%Y-%m}: {ventana.pagos:,} pagos"
    )


def _ventana(ventana) -> str:
    """Una ventana del motor de pagos, como la lee una persona."""
    texto = (
        f"{ventana.despacho_id}/{ventana.cartera_id} {ventana.desde:%Y-%m}: "
        f"{ventana.observaciones:,} pagos observados, {ventana.pendientes:,} sin interpretar"
    )
    if ventana.por_conciliar:
        texto += f", {ventana.por_conciliar:,} movimientos por conciliar"
    return texto


def _pendiente(pendiente) -> str:
    """Un dataset pendiente, como lo lee una persona."""
    if pendiente.fecha_corte is not None:
        return (
            f"{pendiente.contrato} del {pendiente.fecha_corte.isoformat()} ({pendiente.dataset_id})"
        )
    return f"{pendiente.contrato} ({pendiente.dataset_id})"


def _avisar_historia(*, corrida_id: int | None = None, ingesta_pagos_id: int | None = None) -> None:
    """Si la ingesta publico un dataset conformado, su historia queda en la cola: lo dice."""
    from sqlmodel import select

    from motor_cartera.db.modelos import DatasetConformado, EjecucionHistoria
    from motor_cartera.db.sesion import sesion

    condicion = (
        DatasetConformado.corrida_id == corrida_id
        if corrida_id is not None
        else DatasetConformado.ingesta_pagos_id == ingesta_pagos_id
    )
    with sesion() as s:
        fila = s.exec(
            select(EjecucionHistoria.historia_run_id, EjecucionHistoria.estado)
            .join(
                DatasetConformado, DatasetConformado.id == EjecucionHistoria.dataset_conformado_id
            )
            .where(condicion)
            .order_by(EjecucionHistoria.id.desc())
        ).first()
    if fila is not None:
        typer.echo(
            f"  historia/v1 {fila[0]}: {fila[1]}; la materializa un worker, en paralelo y sin "
            "volver a leer el archivo."
        )


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
