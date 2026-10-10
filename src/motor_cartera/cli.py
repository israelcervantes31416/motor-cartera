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
