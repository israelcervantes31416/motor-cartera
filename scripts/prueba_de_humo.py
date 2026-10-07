"""Prueba de humo: el flujo automatico del README, de punta a punta, contra una API y un worker
que ya corren.

Sube una cartera y no pide nada mas: sigue su flujo automatico hasta que el worker lo completa,
de la ingesta al ruteo. Despues revisa los trabajos de la cola que lo ejecutaron, uno por etapa, y
lo que publico cada etapa con los identificadores del flujo: la corrida con sus rechazos y su
resumen, las decisiones por cuenta, los municipios, las rutas y las paradas de la primera.
Comprueba que ninguna etapa se publica dos veces, ni a mano, y que el OpenAPI corresponde a esta
version y documenta la orquestacion. Verifica los codigos HTTP de cada paso.

Con --oficial, hace lo mismo con una cartera oficial (cartera/v2, 93 columnas) y su fecha de
corte declarada: el flujo la lleva de la ingesta al ruteo, y la evidencia de la corrida trae el
archivo original (con el mismo SHA-256 que se calcula aqui sobre el archivo subido), el dataset
conformado y su hoja CARRIER.

Con --pagos, sube un archivo de pagos (pagos/v1, 23 columnas): su ingesta pasa por la cola durable
como un trabajo INGESTA_PAGOS, termina EXITOSA sin deduplicar nada y su evidencia trae el archivo
original y el dataset conformado.

Con --escenario, sube los cortes de un escenario longitudinal (generar-escenario) del mas
reciente al mas antiguo, y sus pagos, y sigue la historia de cada uno: cada corte se materializa en
el modelo historico en paralelo a su flujo, y la Cuenta 360 de cuentas elegidas en los archivos (una
que esta en todos los cortes, una que sale, una que llega despues y una que paga varias veces) dice
lo que paso con cada una. Solo usa la biblioteca estandar, para correr igual en el CI, dentro del
contenedor o en una laptop:

    python scripts/prueba_de_humo.py datos/cartera_sintetica.xlsx
    python scripts/prueba_de_humo.py datos/humo.xlsx --oficial datos/oficial.zip --corte 2026-09-30
    python scripts/prueba_de_humo.py datos/humo.xlsx --pagos datos/pagos.zip
    python scripts/prueba_de_humo.py datos/humo.xlsx --escenario datos/escenario

Lee MC_URL_API (por omision http://localhost:8000) y MC_API_KEY. Termina con codigo 1 en
cuanto algo no sale como debe.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path

BASE = os.environ.get("MC_URL_API", "http://localhost:8000").rstrip("/")
CLAVE = os.environ.get("MC_API_KEY", "clave-local-de-desarrollo")
ESPERA_MAXIMA = 120  # segundos
PAQUETE = Path(__file__).resolve().parents[1] / "src" / "motor_cartera" / "__init__.py"
ETAPAS = ["INGESTA", "DECISION", "TERRITORIAL", "RUTEO"]


def pedir(metodo, ruta, *, cuerpo=None, tipo=None, con_clave=True, timeout=30):
    encabezados = {"X-API-Key": CLAVE} if con_clave else {}
    if tipo:
        encabezados["Content-Type"] = tipo
    peticion = urllib.request.Request(BASE + ruta, data=cuerpo, headers=encabezados, method=metodo)
    try:
        with urllib.request.urlopen(peticion, timeout=timeout) as respuesta:
            return respuesta.status, dict(respuesta.headers), json.loads(respuesta.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), json.loads(error.read() or b"{}")


def subir(archivo: Path, *, con_clave=True, ruta="/corridas", **campos):
    frontera = uuid.uuid4().hex
    partes = [
        f"--{frontera}\r\n".encode(),
        f'Content-Disposition: form-data; name="archivo"; filename="{archivo.name}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n".encode(),
        archivo.read_bytes(),
        b"\r\n",
    ]
    for nombre, valor in campos.items():
        partes.append(
            f'--{frontera}\r\nContent-Disposition: form-data; name="{nombre}"\r\n\r\n'
            f"{valor}\r\n".encode()
        )
    partes.append(f"--{frontera}--\r\n".encode())
    tipo = f"multipart/form-data; boundary={frontera}"
    return pedir("POST", ruta, cuerpo=b"".join(partes), tipo=tipo, con_clave=con_clave)


def esperar(condicion: bool, descripcion: str) -> None:
    print(f"  {'ok ' if condicion else 'MAL'} {descripcion}")
    if not condicion:
        sys.exit(1)


def ubicacion_de(encabezados: dict) -> str | None:
    return encabezados.get("location") or encabezados.get("Location")


def version_del_repositorio() -> str:
    """La version que el repositorio declara; la API que corre tiene que ser esa."""
    return re.search(r'__version__ = "([^"]+)"', PAQUETE.read_text(encoding="utf-8")).group(1)


def esperar_a_la_api() -> tuple[int, dict]:
    """La API puede estar arrancando todavia (docker compose up -d no espera)."""
    limite = time.monotonic() + ESPERA_MAXIMA
    while True:
        try:
            estado, _, salud = pedir("GET", "/salud", con_clave=False)
            if estado == 200 or time.monotonic() > limite:
                return estado, salud
        except (urllib.error.URLError, ConnectionError):
            if time.monotonic() > limite:
                return 0, {}
        time.sleep(1)


def seguir_el_flujo(run_id: str) -> dict:
    """Consulta el flujo de la corrida mientras siga EN_PROCESO, hasta ESPERA_MAXIMA, y cuenta
    cada etapa a la que llega."""
    limite = time.monotonic() + ESPERA_MAXIMA
    vista = None
    while True:
        estado, _, flujo = pedir("GET", f"/corridas/{run_id}/flujo")
        if estado != 200:
            return flujo
        if (flujo["estado"], flujo["etapa"]) != vista:
            vista = (flujo["estado"], flujo["etapa"])
            print(f"      flujo {flujo['estado']} en {flujo['etapa']}: {flujo['detalle']}")
        if flujo["estado"] != "EN_PROCESO" or time.monotonic() > limite:
            return flujo
        time.sleep(0.5)


def la_cartera_oficial(archivo: Path, corte: str) -> None:
    """cartera/v2 de punta a punta: el contrato y el corte declarados, el flujo hasta el ruteo y la
    evidencia de la corrida, con su linaje."""
    print(f"Cartera oficial (cartera/v2) con {archivo}, corte {corte}")
    sha256 = hashlib.sha256(archivo.read_bytes()).hexdigest()

    estado, _, error = subir(archivo, contrato="cartera/v2")
    esperar(
        estado == 422 and error["codigo"] == "FECHA_CORTE_REQUERIDA",
        "cartera/v2 sin fecha de corte: 422 FECHA_CORTE_REQUERIDA",
    )
    estado, encabezados, corrida = subir(archivo, contrato="cartera/v2", fecha_corte=corte)
    esperar(
        estado == 201
        and corrida["version_contrato"] == "cartera/v2"
        and corrida["fecha_corte"] == corte,
        f"POST /corridas con cartera/v2: {estado} {corrida.get('estado')}, corte {corte}",
    )
    run_id = corrida["run_id"]
    flujo = seguir_el_flujo(run_id)
    esperar(
        (flujo.get("estado"), flujo.get("etapa")) == ("COMPLETADO", "COMPLETADA"),
        f"el flujo de cartera/v2: {flujo.get('estado')} en {flujo.get('etapa')}",
    )
    estado, _, corrida = pedir("GET", f"/corridas/{run_id}")
    esperar(
        estado == 200
        and corrida["estado"] == "EXITOSA"
        and corrida["filas_validas"] > 0
        and corrida["version_proyeccion"] == "operacional/v1",
        f"la corrida termino EXITOSA: {corrida.get('detalle', '')[:120]}",
    )
    estado, _, fuente = pedir("GET", f"/corridas/{run_id}/fuente")
    artefacto = fuente.get("artefacto") or {}
    conformado = fuente.get("conformado") or {}
    hojas = fuente.get("hojas_companeras") or []
    esperar(
        estado == 200 and artefacto.get("sha256") == sha256 == corrida["firma"],
        f"GET /corridas/{{run_id}}/fuente: el original, con el SHA-256 del archivo subido "
        f"({sha256[:12]}...)",
    )
    esperar(
        conformado.get("filas") == corrida["filas_validas"]
        and conformado.get("columnas") == 93
        and conformado.get("firma_contenido") == corrida["firma_contenido"]
        and (conformado.get("artefacto") or {}).get("formato") == "parquet",
        f"el dataset conformado: {conformado.get('filas')} registros en Parquet, misma firma",
    )
    esperar(
        len(hojas) == 1 and hojas[0]["estructura_reconocida"] and hojas[0]["columnas"] == 85,
        f"CARRIER reconocida: {hojas[0]['filas'] if hojas else 0} filas, 85 columnas",
    )
    estado, _, decision = pedir("GET", f"/decisiones/{flujo['decision_run_id']}")
    estado_r, _, ruteo = pedir("GET", f"/ruteos/{flujo['ruteo_run_id']}")
    esperar(
        estado == estado_r == 200
        and decision["cuentas_decididas"] == corrida["filas_validas"]
        and ruteo["estado"] == "EXITOSA",
        f"decision/v1 decidio {decision.get('cuentas_decididas')} cuentas y ruteo/v1 publico "
        f"{ruteo.get('rutas_publicadas')} rutas",
    )


def seguir_los_pagos(pagos_run_id: str) -> dict:
    """Consulta la ingesta de pagos mientras siga EN_PROCESO, hasta ESPERA_MAXIMA."""
    limite = time.monotonic() + ESPERA_MAXIMA
    while True:
        estado, _, ingesta = pedir("GET", f"/pagos/{pagos_run_id}")
        if estado != 200 or ingesta["estado"] != "EN_PROCESO" or time.monotonic() > limite:
            return ingesta
        time.sleep(0.5)


def los_pagos(archivo: Path) -> None:
    """pagos/v1 de punta a punta: la ingesta y su trabajo en la cola, el juicio sin deduplicar y la
    evidencia, con su linaje. No es una corrida: no publica cuentas ni encadena nada."""
    print(f"Pagos (pagos/v1) con {archivo}")
    sha256 = hashlib.sha256(archivo.read_bytes()).hexdigest()

    estado, encabezados, ingesta = subir(archivo, ruta="/pagos")
    pagos_run_id = ingesta.get("pagos_run_id")
    esperar(
        estado == 201
        and ingesta["estado"] == "EN_PROCESO"
        and ingesta["version_contrato"] == "pagos/v1"
        and ubicacion_de(encabezados) == f"/pagos/{pagos_run_id}",
        f"POST /pagos: {estado} {ingesta.get('estado')}, Location {ubicacion_de(encabezados)}",
    )
    estado, _, error = subir(archivo, ruta="/pagos")
    esperar(
        estado == 409
        and error["codigo"] in {"PAGOS_EN_PROCESO", "PAGOS_YA_ACEPTADOS"}
        and error["pagos_run_id"] == pagos_run_id,
        f"el mismo archivo otra vez: 409 {error.get('codigo')}, con la ingesta que lo tiene",
    )
    ingesta = seguir_los_pagos(pagos_run_id)
    esperar(
        ingesta.get("estado") == "EXITOSA"
        and ingesta["filas_validas"] == ingesta["filas_leidas"] > 0
        and ingesta["filas_rechazadas"] == 0
        and ingesta["firma"] == sha256
        and len(ingesta["firma_contenido"] or "") == 64,
        f"la ingesta termino {ingesta.get('estado')}: {str(ingesta.get('detalle'))[:120]}",
    )
    estado, _, trabajo = pedir("GET", f"/trabajos/{ingesta['trabajo_id']}")
    esperar(
        estado == 200
        and (trabajo["tipo"], trabajo["estado"]) == ("INGESTA_PAGOS", "COMPLETADO")
        and trabajo["objetivo_run_id"] == pagos_run_id
        and trabajo["flujo_id"] is None,
        f"su trabajo: {trabajo.get('tipo')} {trabajo.get('estado')} ({trabajo.get('intentos')}), "
        "sin flujo",
    )
    estado, _, fuente = pedir("GET", f"/pagos/{pagos_run_id}/fuente")
    conformado = fuente.get("conformado") or {}
    esperar(
        estado == 200
        and (fuente.get("artefacto") or {}).get("sha256") == sha256
        and conformado.get("filas") == ingesta["filas_validas"]
        and conformado.get("columnas") == 23
        and conformado.get("firma_contenido") == ingesta["firma_contenido"]
        and (conformado.get("artefacto") or {}).get("formato") == "parquet",
        f"GET /pagos/{{pagos_run_id}}/fuente: el original ({sha256[:12]}...) y "
        f"{conformado.get('filas')} movimientos conformados en Parquet",
    )
    estado, _, rechazos = pedir("GET", f"/pagos/{pagos_run_id}/rechazos")
    esperar(
        estado == 200 and rechazos["total"] == 0,
        f"GET /pagos/{{pagos_run_id}}/rechazos: {estado}, {rechazos.get('total')} rechazos",
    )


# --- el modelo historico y la Cuenta 360 ---------------------------------------------------------

ESPERA_HISTORIA = 600  # segundos: la historia va despues de las etapas operacionales de cada corte


def _clientes(archivo: Path) -> list[str]:
    """Los CLIENTE_UNICO de un corte del escenario, leidos con la biblioteca estandar."""
    if archivo.suffix == ".zip":
        with zipfile.ZipFile(archivo) as paquete:
            (miembro,) = [n for n in paquete.namelist() if n.upper().startswith("CARTERA")]
            texto = io.TextIOWrapper(paquete.open(miembro), encoding="utf-8")
            return [fila["CLIENTE_UNICO"] for fila in csv.DictReader(texto)]
    with archivo.open(encoding="utf-8", newline="") as texto:
        return [fila["CLIENTE_UNICO"] for fila in csv.DictReader(texto)]


def _movimientos(archivo: Path) -> list[str]:
    """El Cliente_Unico de cada movimiento de un archivo de pagos del escenario."""
    if archivo.suffix == ".zip":
        with zipfile.ZipFile(archivo) as paquete:
            (miembro,) = [n for n in paquete.namelist() if n.upper().startswith("PAGOS")]
            texto = io.TextIOWrapper(paquete.open(miembro), encoding="utf-8")
            return [fila["Cliente_Unico"] for fila in csv.DictReader(texto)]
    with archivo.open(encoding="utf-8", newline="") as texto:
        return [fila["Cliente_Unico"] for fila in csv.DictReader(texto)]


def seguir_la_historia(ruta: str) -> dict:
    """La ultima ejecucion historica de una corrida o de una ingesta de pagos, cuando termina.
    Mientras la ingesta sigue en proceso la API responde 409; despues, la ejecucion EN_PROCESO hasta
    que un worker la materializa."""
    limite = time.monotonic() + ESPERA_HISTORIA
    while True:
        estado, _, pagina = pedir("GET", ruta)
        ejecuciones = pagina.get("elementos") or [{}]
        if estado == 200 and ejecuciones[0].get("estado") not in (None, "EN_PROCESO"):
            return {**ejecuciones[0], "pagina": pagina}
        if estado not in (200, 409) or time.monotonic() > limite:
            return {"estado": f"HTTP {estado}", "pagina": pagina}
        time.sleep(1)


def el_modelo_historico(directorio: Path) -> None:
    """Un escenario longitudinal de punta a punta: sus cortes llegan fuera de orden, la historia de
    cada uno se materializa en paralelo a su flujo, y la Cuenta 360 de cuentas elegidas en los
    archivos dice lo que paso con cada una."""
    manifiesto = json.loads((directorio / "escenario.json").read_text(encoding="utf-8"))
    cortes = manifiesto["cortes"]
    print(f"Modelo historico con el escenario de {directorio}: {len(cortes)} cortes")

    # Los cortes llegan del mas reciente al mas antiguo: la historia se ordena por fecha de corte,
    # no por orden de llegada.
    corridas = {}
    for corte in reversed(cortes):
        estado, _, corrida = subir(
            directorio / corte["archivo"], contrato="cartera/v2", fecha_corte=corte["fecha_corte"]
        )
        esperar(estado == 201, f"POST /corridas del corte {corte['fecha_corte']}: {estado}")
        corridas[corte["fecha_corte"]] = corrida["run_id"]
    ingestas = {}
    for periodo in manifiesto["periodos"]:
        estado, _, ingesta = subir(directorio / periodo["archivo"], ruta="/pagos")
        esperar(
            estado == 201, f"POST /pagos del periodo que cierra el {periodo['hasta']}: {estado}"
        )
        ingestas[periodo["archivo"]] = ingesta["pagos_run_id"]

    for fecha, run_id in sorted(corridas.items()):
        historia = seguir_la_historia(f"/corridas/{run_id}/historia")
        esperar(
            (historia.get("estado"), historia.get("resultado")) == ("EXITOSA", "CORTE_PUBLICADO")
            and historia["registros_publicados"] == historia["registros_leidos"] > 0,
            f"historia del corte {fecha}: {historia.get('estado')} {historia.get('resultado')}, "
            f"{historia.get('registros_publicados')} snapshots",
        )
        flujo = seguir_el_flujo(run_id)
        estado, _, trabajos = pedir("GET", f"/flujos/{flujo['flujo_id']}/trabajos")
        esperar(
            flujo.get("estado") == "COMPLETADO"
            and [t["tipo"] for t in trabajos.get("elementos", [])] == ETAPAS,
            f"su flujo {flujo.get('estado')}, con sus cuatro etapas y sin la historia, que va "
            "en paralelo",
        )
    ultima = None
    for archivo, pagos_run_id in ingestas.items():
        historia = seguir_la_historia(f"/pagos/{pagos_run_id}/historia")
        relacion = historia["pagina"].get("relacion") or {}
        movimientos = len(_movimientos(directorio / archivo))
        esperar(
            (historia.get("estado"), historia.get("resultado")) == ("EXITOSA", "PAGOS_PUBLICADOS")
            and historia["registros_publicados"] == movimientos
            and relacion.get("pagos_con_cuenta_observada", 0)
            + relacion.get("pagos_sin_cuenta_observada", 0)
            == movimientos,
            f"historia de {archivo}: {historia.get('registros_publicados')} pagos observados de "
            f"{movimientos} movimientos, sin deduplicar; "
            f"{relacion.get('pagos_sin_cuenta_observada')} sin cuenta observada",
        )
        ultima = historia
    estado, _, por_id = pedir("GET", f"/historias/{ultima['historia_run_id']}")
    estado_t, _, trabajo = pedir("GET", f"/trabajos/{ultima['trabajo_id']}")
    esperar(
        estado == estado_t == 200
        and por_id["historia_run_id"] == ultima["historia_run_id"]
        and (trabajo["tipo"], trabajo["estado"], trabajo["flujo_id"])
        == ("HISTORIA", "COMPLETADO", None)
        and trabajo["objetivo_run_id"] == ultima["historia_run_id"],
        "GET /historias/{historia_run_id} y su trabajo HISTORIA en la cola, COMPLETADO y sin flujo",
    )

    # Los cortes canonicos: uno por fecha, con las cuentas del manifiesto, y el ultimo.
    estado, _, pagina = pedir("GET", "/cartera/cortes?por_pagina=500")
    canonicos = {c["fecha_corte"]: c for c in pagina.get("elementos", [])}
    esperar(
        estado == 200
        and all(canonicos[c["fecha_corte"]]["cuentas"] == c["cuentas"] for c in cortes)
        and pagina["ultimo_corte"]["fecha_corte"] == cortes[-1]["fecha_corte"],
        f"GET /cartera/cortes: {len(cortes)} cortes del escenario con sus cuentas; el ultimo, "
        f"{pagina.get('ultimo_corte', {}).get('fecha_corte')}",
    )

    # Cuentas elegidas en los archivos: una que esta en todos los cortes, una que sale y una que
    # llega despues del primero.
    presentes = [set(_clientes(directorio / c["archivo"])) for c in cortes]
    fechas = [c["fecha_corte"] for c in cortes]
    siempre = sorted(set.intersection(*presentes))[0]
    sale = sorted(presentes[0] - presentes[1])[0]
    llega = sorted(presentes[1] - presentes[0])[0]

    def cuenta_360(cliente: str) -> tuple[str, dict, list]:
        estado, _, encontrada = pedir("GET", f"/cuentas?cliente_unico={cliente}")
        esperar(estado == 200, f"GET /cuentas?cliente_unico={cliente}: {estado}")
        cuenta_id = encontrada["cuenta_id"]
        _, _, resumen = pedir("GET", f"/cuentas/{cuenta_id}")
        _, _, eventos = pedir("GET", f"/cuentas/{cuenta_id}/eventos")
        return cuenta_id, resumen, [(e["tipo"], e["fecha_corte"]) for e in eventos["elementos"]]

    cuenta_id, resumen, eventos = cuenta_360(siempre)
    esperar(
        resumen["estado_presencia"] == "EN_CARTERA"
        and resumen["cortes_observados"] == len(cortes)
        and resumen["cortes_ausentes_desde_primera_observacion"] == 0
        and eventos == [("PRIMERA_OBSERVACION", fechas[0])]
        and resumen["snapshot_actual"]["fecha_corte"] == fechas[-1],
        f"{siempre}, en todos los cortes: EN_CARTERA, {resumen['cortes_observados']} cortes, "
        "solo su primera observacion",
    )
    estado, _, historia = pedir("GET", f"/cuentas/{cuenta_id}/historia?orden=asc")
    continuidad = [h["continuo_desde_anterior"] for h in historia.get("elementos", [])]
    esperar(
        estado == 200
        and historia["total"] == len(cortes)
        and continuidad == [None] + [True] * (len(cortes) - 1)
        and all(h["dataset_id"] and h["source_row"] >= 2 for h in historia["elementos"]),
        f"su historia: {historia.get('total')} snapshots continuos, cada uno con su dataset y su "
        "fila",
    )
    _, resumen, eventos = cuenta_360(sale)
    esperar(
        resumen["estado_presencia"] == "NO_OBSERVADA_EN_ULTIMO_CORTE"
        and resumen["snapshot_actual"] is None
        and resumen["ultimo_snapshot_observado"]["fecha_corte"] == fechas[0]
        and eventos == [("PRIMERA_OBSERVACION", fechas[0]), ("SALIDA_OBSERVADA", fechas[1])],
        f"{sale}, que sale en el segundo corte: SALIDA_OBSERVADA y NO_OBSERVADA_EN_ULTIMO_CORTE",
    )
    _, resumen, eventos = cuenta_360(llega)
    esperar(
        eventos[0] == ("PRIMERA_OBSERVACION", fechas[1])
        and resumen["primera_observacion"] == fechas[1],
        f"{llega}, que llega en el segundo corte: su primera observacion es ahi, no un alta",
    )

    # Los pagos observados de una cuenta con varios movimientos: tantos como filas en los archivos.
    filas: dict[str, int] = {}
    for periodo in manifiesto["periodos"]:
        for cliente in _movimientos(directorio / periodo["archivo"]):
            filas[cliente] = filas.get(cliente, 0) + 1
    pagador = sorted(c for c, n in filas.items() if n >= 2)[0]
    cuenta_id, _, _ = cuenta_360(pagador)
    estado, _, pagos = pedir("GET", f"/cuentas/{cuenta_id}/pagos-observados")
    recepciones = [p["fecha_recepcion"] for p in pagos.get("elementos", [])]
    esperar(
        estado == 200
        and pagos["total"] == filas[pagador]
        and recepciones == sorted(recepciones, reverse=True)
        and "No estan deduplicados" in pagos["aviso"],
        f"{pagador}: {pagos.get('total')} pagos observados, del mas reciente al mas antiguo, con "
        "su aviso",
    )


def main(
    archivo: Path,
    oficial: Path | None = None,
    corte: str | None = None,
    pagos: Path | None = None,
    escenario: Path | None = None,
) -> None:
    print(f"Prueba de humo contra {BASE} con {archivo}")

    estado, salud = esperar_a_la_api()
    esperar(estado == 200 and salud.get("base_de_datos") == "ok", "GET /salud 200, base ok")

    estado, _, error = subir(archivo, con_clave=False)
    esperar(estado == 401 and error["codigo"] == "API_KEY_AUSENTE", "POST sin clave: 401")

    # La ingesta: la API la registra y responde; el worker la ejecuta.
    estado, encabezados, corrida = subir(archivo)
    esperar(estado == 201 and corrida["estado"] == "EN_PROCESO", "POST /corridas: 201 EN_PROCESO")
    run_id = corrida["run_id"]
    ubicacion = ubicacion_de(encabezados)
    esperar(ubicacion == f"/corridas/{run_id}", f"Location: {ubicacion}")

    # Segun que tan rapido sea el worker, el archivo todavia se procesa o ya se publico.
    estado, _, error = subir(archivo)
    esperar(
        estado == 409
        and error["codigo"] in {"ARCHIVO_EN_PROCESO", "ARCHIVO_YA_PUBLICADO"}
        and error["run_id"] == run_id,
        f"el mismo archivo otra vez: 409 {error.get('codigo')}, con la corrida que lo tiene",
    )

    # El flujo automatico, de la ingesta al ruteo, sin pedir ninguna etapa.
    flujo = seguir_el_flujo(run_id)
    esperar(
        (flujo.get("estado"), flujo.get("etapa")) == ("COMPLETADO", "COMPLETADA"),
        f"GET /corridas/{{run_id}}/flujo: {flujo.get('estado')} en {flujo.get('etapa')}: "
        f"{flujo.get('detalle', flujo.get('mensaje'))}",
    )
    ids = [flujo["decision_run_id"], flujo["territorial_run_id"], flujo["ruteo_run_id"]]
    esperar(
        all(ids) and flujo["run_id"] == run_id and flujo["duracion_segundos"] is not None,
        f"el flujo trae su decision, su ejecucion territorial y su ruteo, en "
        f"{flujo['duracion_segundos']} s",
    )
    flujo_id = flujo["flujo_id"]
    estado, _, por_id = pedir("GET", f"/flujos/{flujo_id}")
    esperar(
        estado == 200 and por_id == flujo,
        f"GET /flujos/{{flujo_id}}: {estado}, el mismo flujo",
    )

    # Los trabajos que lo ejecutaron: uno por etapa, en orden y todos entregados. Mas de un
    # intento es legitimo si un worker se reinicio a la mitad; menos de uno, no.
    estado, _, trabajos = pedir("GET", f"/flujos/{flujo_id}/trabajos")
    elementos = trabajos.get("elementos", [])
    esperar(
        estado == 200
        and trabajos["total"] == 4
        and [t["tipo"] for t in elementos] == ETAPAS
        and all(t["estado"] == "COMPLETADO" and t["intentos"] >= 1 for t in elementos),
        f"GET /flujos/{{flujo_id}}/trabajos: {estado}, "
        + ", ".join(f"{t['tipo']} {t['estado']} ({t['intentos']})" for t in elementos),
    )
    esperar(
        [t["objetivo_run_id"] for t in elementos] == [run_id, *ids],
        "cada trabajo apunta al recurso de su etapa",
    )
    estado, _, trabajo = pedir("GET", f"/trabajos/{elementos[0]['trabajo_id']}")
    esperar(
        estado == 200 and trabajo == elementos[0] and "worker_id" not in trabajo,
        f"GET /trabajos/{{trabajo_id}}: {estado}, el mismo, sin decir que worker lo tuvo",
    )

    # La corrida.
    estado, _, corrida = pedir("GET", ubicacion)
    esperar(
        estado == 200 and corrida["estado"] == "EXITOSA",
        f"la corrida termino EXITOSA: {corrida.get('detalle')}",
    )
    # El archivo no se borro al terminar: sigue en el almacen, con su SHA-256.
    estado, _, fuente = pedir("GET", f"{ubicacion}/fuente")
    sha256 = hashlib.sha256(archivo.read_bytes()).hexdigest()
    esperar(
        estado == 200
        and (fuente.get("artefacto") or {}).get("sha256") == sha256 == corrida["firma"],
        f"GET /corridas/{{run_id}}/fuente: el archivo original sigue ahi ({sha256[:12]}...)",
    )
    esperar(
        bool(corrida["version_contrato"]) and len(corrida["firma_contenido"] or "") == 64,
        f"contrato {corrida['version_contrato']}, contenido firmado aparte del archivo",
    )
    esperar(
        corrida["filas_leidas"] == corrida["filas_validas"] + corrida["filas_rechazadas"],
        f"leidas {corrida['filas_leidas']} = validas {corrida['filas_validas']} "
        f"+ rechazadas {corrida['filas_rechazadas']}",
    )

    estado, _, rechazos = pedir("GET", f"{ubicacion}/rechazos?por_pagina=5")
    esperar(
        estado == 200 and rechazos["total"] == corrida["filas_rechazadas"],
        f"GET /rechazos: {rechazos.get('total')} rechazos, cada uno con su motivo",
    )
    for rechazo in rechazos["elementos"][:3]:
        motivos = ", ".join(f"{m['campo']}: {m['regla']}" for m in rechazo["motivos"])
        print(f"      fila {rechazo['fila']}: {motivos}")

    # Con run_id: el resumen de esta corrida. Sin el, el de la cartera vigente, que puede ser
    # otra si ya hay publicada una con fecha de corte mas reciente.
    estado, _, resumen = pedir("GET", f"/cartera/resumen?run_id={run_id}")
    esperar(
        estado == 200
        and resumen["run_id"] == run_id
        and resumen["total_cuentas"] == corrida["filas_validas"],
        f"GET /cartera/resumen?run_id=...: {resumen.get('total_cuentas')} cuentas, "
        f"saldo {resumen.get('saldo_total')}, {resumen.get('total')} segmentos",
    )
    estado, _, vigente = pedir("GET", "/cartera/resumen")
    esperar(estado == 200, f"GET /cartera/resumen: vigente la corrida {vigente.get('run_id')}")

    # La decision que el flujo pidio.
    decision_run_id = flujo["decision_run_id"]
    estado, _, ejecucion = pedir("GET", f"/decisiones/{decision_run_id}")
    esperar(
        estado == 200
        and ejecucion["estado"] == "EXITOSA"
        and ejecucion["run_id"] == run_id
        and ejecucion["version_reglas"] == "decision/v1"
        and ejecucion["cuentas_evaluadas"] == corrida["filas_validas"]
        and ejecucion["cuentas_decididas"] == corrida["filas_validas"],
        f"GET /decisiones/{{decision_run_id}}: {estado} {ejecucion.get('estado')}, reglas "
        f"{ejecucion.get('version_reglas')}: decididas {ejecucion.get('cuentas_decididas')} = "
        f"validas {corrida['filas_validas']}, en {ejecucion.get('duracion_segundos')} s",
    )
    ruta_decisiones = f"/corridas/{run_id}/decisiones"
    estado, _, historial = pedir("GET", ruta_decisiones)
    en_el_historial = [e["decision_run_id"] for e in historial.get("elementos", [])]
    esperar(
        estado == 200 and decision_run_id in en_el_historial,
        f"GET /corridas/{{run_id}}/decisiones: {estado}, {historial.get('total')} ejecucion(es), "
        "entre ellas la del flujo",
    )
    estado, _, cuentas = pedir("GET", f"/decisiones/{decision_run_id}/cuentas?por_pagina=5")
    esperar(
        estado == 200
        and cuentas["total"] == ejecucion["cuentas_decididas"]
        and bool(cuentas["elementos"]),
        f"GET /decisiones/{{decision_run_id}}/cuentas: {estado}, {cuentas.get('total')} "
        "decisiones, una por cuenta",
    )
    # Que cada decision traiga lo que es publico y que este explicada. Las reglas exactas ya las
    # fijan las pruebas; aqui no se vuelven a escribir.
    publicos = {"cliente_unico", "segmento", "prioridad", "canal_recomendado", "motivos"}
    esperar(
        all(set(d) == publicos and d["motivos"] for d in cuentas["elementos"]),
        "cada decision trae segmento, prioridad, canal recomendado y sus motivos",
    )
    for decision in cuentas["elementos"][:3]:
        motivos = ", ".join(motivo["codigo"] for motivo in decision["motivos"])
        print(
            f"      {decision['cliente_unico']}: {decision['segmento']}, "
            f"{decision['prioridad']}, {decision['canal_recomendado']} ({motivos})"
        )

    # La organizacion territorial que el flujo pidio.
    territorial_run_id = flujo["territorial_run_id"]
    estado, _, territorial = pedir("GET", f"/territoriales/{territorial_run_id}")
    esperar(
        estado == 200
        and territorial["estado"] == "EXITOSA"
        and territorial["version_reglas"] == "territorial/v1"
        and territorial["decision_run_id"] == decision_run_id
        and territorial["run_id"] == run_id
        and territorial["territorios_evaluados"] > 0
        and territorial["territorios_publicados"] == territorial["territorios_evaluados"],
        f"GET /territoriales/{{territorial_run_id}}: {estado} {territorial.get('estado')}, reglas "
        f"{territorial.get('version_reglas')}: {territorial.get('territorios_publicados')} "
        f"municipios, en {territorial.get('duracion_segundos')} s",
    )
    ruta_territoriales = f"/decisiones/{decision_run_id}/territoriales"
    estado, _, historial = pedir("GET", ruta_territoriales)
    en_el_historial = [e["territorial_run_id"] for e in historial.get("elementos", [])]
    esperar(
        estado == 200 and territorial_run_id in en_el_historial,
        f"GET /decisiones/{{decision_run_id}}/territoriales: {estado}, "
        f"{historial.get('total')} ejecucion(es), entre ellas la del flujo",
    )
    estado, _, municipios = pedir(
        "GET", f"/territoriales/{territorial_run_id}/municipios?por_pagina=5"
    )
    esperar(
        estado == 200
        and municipios["total"] == territorial["territorios_publicados"]
        and bool(municipios["elementos"]),
        f"GET /territoriales/{{territorial_run_id}}/municipios: {estado}, "
        f"{municipios.get('total')} municipios, todos los publicados",
    )
    # Que cada municipio traiga lo que es publico y que este explicado. Los umbrales y el orden
    # exactos ya los fijan las pruebas; aqui no se vuelven a escribir.
    publicos = {
        "clave_territorio",
        "cve_entidad",
        "cve_municipio",
        "cuentas_total",
        "saldo_total",
        "cuentas_campo",
        "saldo_campo",
        "carga",
        "posicion_campo",
        "motivos",
    }
    esperar(
        all(set(m) == publicos and m["motivos"] for m in municipios["elementos"]),
        "cada municipio trae sus agregados, su carga de campo, su lugar y sus motivos",
    )
    for municipio in municipios["elementos"][:3]:
        motivos = ", ".join(motivo["codigo"] for motivo in municipio["motivos"])
        print(
            f"      {municipio['clave_territorio']}: {municipio['cuentas_campo']} cuentas de "
            f"campo de {municipio['cuentas_total']}, {municipio['carga']}, lugar "
            f"{municipio['posicion_campo']} ({motivos})"
        )

    # El ruteo que el flujo pidio.
    ruteo_run_id = flujo["ruteo_run_id"]
    estado, _, ruteo = pedir("GET", f"/ruteos/{ruteo_run_id}")
    esperar(
        estado == 200
        and ruteo["estado"] == "EXITOSA"
        and ruteo["version_reglas"] == "ruteo/v1"
        and ruteo["territorial_run_id"] == territorial_run_id
        and ruteo["decision_run_id"] == decision_run_id
        and ruteo["run_id"] == run_id
        and ruteo["rutas_evaluadas"] > 0
        and ruteo["rutas_publicadas"] == ruteo["rutas_evaluadas"]
        and ruteo["paradas_evaluadas"] > 0
        and ruteo["paradas_publicadas"] == ruteo["paradas_evaluadas"],
        f"GET /ruteos/{{ruteo_run_id}}: {estado} {ruteo.get('estado')}, reglas "
        f"{ruteo.get('version_reglas')}: {ruteo.get('rutas_publicadas')} rutas, "
        f"{ruteo.get('paradas_publicadas')} paradas, en {ruteo.get('duracion_segundos')} s",
    )
    ruta_ruteos = f"/territoriales/{territorial_run_id}/ruteos"
    estado, _, historial = pedir("GET", ruta_ruteos)
    en_el_historial = [e["ruteo_run_id"] for e in historial.get("elementos", [])]
    esperar(
        estado == 200 and ruteo_run_id in en_el_historial,
        f"GET /territoriales/{{territorial_run_id}}/ruteos: {estado}, {historial.get('total')} "
        "ejecucion(es), entre ellas la del flujo",
    )
    estado, _, rutas = pedir("GET", f"/ruteos/{ruteo_run_id}/rutas?por_pagina=5")
    esperar(
        estado == 200 and rutas["total"] == ruteo["rutas_publicadas"] and bool(rutas["elementos"]),
        f"GET /ruteos/{{ruteo_run_id}}/rutas: {estado}, {rutas.get('total')} rutas, todas las "
        "publicadas",
    )
    # Que cada ruta traiga lo que es publico. Las coordenadas, la metrica y el recorrido exactos ya
    # los fijan las pruebas; aqui no se vuelven a escribir.
    publicos = {
        "clave_territorio",
        "posicion_territorial",
        "cuentas_campo",
        "paradas",
        "distancia_inicial_m",
        "distancia_total_m",
        "distancia_regreso_deposito_m",
        "mejora_2opt_m",
    }
    esperar(
        all(set(r) == publicos for r in rutas["elementos"]),
        "cada ruta trae su municipio, su lugar territorial, sus paradas y sus distancias",
    )
    for ruta in rutas["elementos"][:3]:
        print(
            f"      {ruta['clave_territorio']} (lugar {ruta['posicion_territorial']}): "
            f"{ruta['paradas']} paradas, {ruta['distancia_total_m']:,} m sinteticos, "
            f"{ruta['mejora_2opt_m']:,} menos que el vecino mas cercano"
        )

    primera = rutas["elementos"][0]
    estado, _, paradas = pedir(
        "GET", f"/ruteos/{ruteo_run_id}/rutas/{primera['clave_territorio']}/paradas?por_pagina=5"
    )
    esperar(
        estado == 200 and paradas["total"] == primera["paradas"] and bool(paradas["elementos"]),
        f"GET /ruteos/{{ruteo_run_id}}/rutas/{primera['clave_territorio']}/paradas: {estado}, "
        f"{paradas.get('total')} paradas, todas las de la ruta",
    )
    publicos = {"secuencia", "cliente_unico", "x_m", "y_m", "distancia_desde_anterior_m"}
    esperar(
        all(set(p) == publicos for p in paradas["elementos"])
        and [p["secuencia"] for p in paradas["elementos"]]
        == list(range(1, len(paradas["elementos"]) + 1)),
        "cada parada trae su secuencia, su cliente, su punto sintetico y su distancia",
    )
    for parada in paradas["elementos"][:3]:
        print(
            f"      {parada['secuencia']}. {parada['cliente_unico']} en "
            f"({parada['x_m']}, {parada['y_m']}), a {parada['distancia_desde_anterior_m']:,} m"
        )
    # Una sola invariante, la que cualquier ruta cumple: el 2-opt nunca la alarga.
    esperar(
        primera["distancia_total_m"] <= primera["distancia_inicial_m"]
        and primera["mejora_2opt_m"]
        == primera["distancia_inicial_m"] - primera["distancia_total_m"],
        f"la ruta de {primera['clave_territorio']}: {primera['distancia_total_m']:,} <= "
        f"{primera['distancia_inicial_m']:,}, mejora {primera['mejora_2opt_m']:,}",
    )

    # Ninguna etapa se publica dos veces, tampoco pidiendola a mano: cada POST da su 409 de
    # siempre, sin Location. El 409 no dice cual ejecucion fue; eso lo dice el historial.
    for ruta, codigo in (
        (ruta_decisiones, "DECISION_YA_GENERADA"),
        (ruta_territoriales, "TERRITORIAL_YA_GENERADO"),
        (ruta_ruteos, "RUTEO_YA_GENERADO"),
    ):
        estado, encabezados, error = pedir("POST", ruta)
        esperar(
            estado == 409
            and error["codigo"] == codigo
            and error["run_id"] == run_id
            and not ubicacion_de(encabezados),
            f"POST {ruta}: {estado} {error.get('codigo')}, sin Location",
        )
    estado, _, error = pedir("POST", f"/flujos/{flujo_id}/reanudar")
    esperar(
        estado == 409 and error["codigo"] == "FLUJO_YA_COMPLETADO",
        f"POST /flujos/{{flujo_id}}/reanudar: {estado} {error.get('codigo')}",
    )

    estado, _, openapi = pedir("GET", "/openapi.json", con_clave=False)
    documentadas = openapi.get("paths", {})
    version = openapi.get("info", {}).get("version")
    rutas_esperadas = (
        "/corridas/{run_id}/flujo",
        "/flujos/{flujo_id}",
        "/flujos/{flujo_id}/trabajos",
        "/trabajos/{trabajo_id}",
        "/flujos/{flujo_id}/reanudar",
        "/territoriales/{territorial_run_id}/municipios",
        "/ruteos/{ruteo_run_id}/rutas/{clave_territorio}/paradas",
        "/corridas/{run_id}/fuente",
        "/pagos",
        "/pagos/{pagos_run_id}",
        "/pagos/{pagos_run_id}/rechazos",
        "/pagos/{pagos_run_id}/fuente",
        "/cuentas",
        "/cuentas/{cuenta_id}",
        "/cuentas/{cuenta_id}/historia",
        "/cuentas/{cuenta_id}/eventos",
        "/cuentas/{cuenta_id}/pagos-observados",
        "/cartera/cortes",
        "/cartera/cortes/{corte_id}",
        "/historias/{historia_run_id}",
        "/corridas/{run_id}/historia",
        "/pagos/{pagos_run_id}/historia",
    )
    esperar(
        estado == 200
        and version == version_del_repositorio()
        and all(ruta in documentadas for ruta in rutas_esperadas),
        f"GET /openapi.json {estado}: version {version}, con la orquestacion, los motores, "
        "la evidencia de las fuentes, los pagos y la Cuenta 360",
    )
    if oficial is not None:
        la_cartera_oficial(oficial, corte)
    if pagos is not None:
        los_pagos(pagos)
    if escenario is not None:
        el_modelo_historico(escenario)
    print("Todo en orden.")


if __name__ == "__main__":
    argumentos = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    argumentos.add_argument("archivo", type=Path, help="Una cartera de cartera/v1.")
    argumentos.add_argument("--oficial", type=Path, help="Una cartera oficial de cartera/v2.")
    argumentos.add_argument("--corte", help="La fecha de corte de la cartera oficial, AAAA-MM-DD.")
    argumentos.add_argument("--pagos", type=Path, help="Un archivo de pagos de pagos/v1.")
    argumentos.add_argument(
        "--escenario", type=Path, help="El directorio de un escenario de generar-escenario."
    )
    leidos = argumentos.parse_args()
    if (leidos.oficial is None) != (leidos.corte is None):
        argumentos.error("--oficial y --corte van juntos.")
    main(leidos.archivo, leidos.oficial, leidos.corte, leidos.pagos, leidos.escenario)
