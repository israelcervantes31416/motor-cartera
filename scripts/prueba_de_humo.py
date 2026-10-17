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
lo que paso con cada una. Despues sigue al motor de pagos: cada ventana de pagos interpretada por
su trabajo MOTOR_PAGOS, y una fila que un archivo trae dos veces, que es un solo movimiento con sus
dos observaciones.

Con --oficial y --pagos, recorre ademas el lifecycle de cobranza sobre sus cuentas y sus pagos:
gestion, promesa, movimiento, evaluacion de la promesa a una fecha explicita, atribucion y Cuenta
360, con una gestion sin contacto, una anulada, un pago ambiguo, la idempotencia de las escrituras
y los trabajos EVALUACION_PROMESAS y ATRIBUCION. Y decision/v2 sobre el corte oficial: una decision
a su as_of con su known_at declarado (un BACKTEST, si el escenario ya trajo cortes posteriores), sus
features, una cuenta con un modificador -1, una con +1 y una con los dos, cada una explicada y
verificada, su historia, la Cuenta 360 y su trabajo DECISION_V2.

Con --geografia (y --oficial), la referencia geografica ya esta importada y la cartera oficial trae
coordenadas sinteticas (generar-oficial --puntos): recorre geografia/v1 sobre su corte (el
municipio de cada cuenta, las anomalias por su indice), publica una version de bases operativas y
la reutiliza, asigna con territorial/v2, y revisa la Cuenta 360 de una cuenta asignada, una sin
coordenadas, una que discrepa de la fuente y una sin base, sin que ninguna respuesta lleve una
coordenada.

Con --campo (y --geografia y --pagos), recorre campo/v2 sobre el mismo corte: publica bases de
campo sobre los municipios de las candidatas de la decision/v2, las asigna con territorial/v2,
publica una configuracion de campo sintetica (y la reutiliza) y pide el plan del dia siguiente al
corte, con la fecha declarada: una cuenta ASIGNADA a cada gestor de cupo 1, una de ellas
rebalanceada por capacidad, SIN_CAPACIDAD, SIN_GESTOR_DISPONIBLE (un gestor cerrado por un
festivo y uno inactivo) y REVISION_TERRITORIAL; sus cargas, sus unidades y la Cuenta 360, sin
coordenadas ni celdas.

Con --ruteo (y --campo), recorre ruteo/v2 sobre una red vial ya PUBLICADA y servida por OSRM: la red
sintetica que generar-red-sintetica dibujo en el municipio 21114 (con sus puntos en la muestra de
la cartera oficial), con una base en el cruce que imprimio (--base-ruteo). Publica esas bases, las
asigna con territorial/v2, publica una configuracion de campo con un gestor de dia completo, uno
con turno partido y uno de 40 minutos, planea el dia siguiente al corte y lo rutea: rutas COMPLETA y
PARCIAL, paradas PROGRAMADA, SIN_TIEMPO y NO_ENRUTABLE (por un snap de mas de 500 m y por un tramo
sin camino), la espera en la pausa del turno partido sin manejar en ella, el regreso a la base, la
Cuenta 360 y su trabajo RUTEO_V2, sin una sola coordenada.

Con --analytics (y --ruteo), recorre analytics/v1 sobre la cohorte del corte oficial, del corte al
dia del plan de ruteo, por dia: el catalogo con su hash, dos gestiones del ultimo dia y la
evaluacion de las promesas a ese dia, el manifiesto con lo que la prueba publico (la interpretacion
de septiembre y su atribucion, la evaluacion, decision/v2, la geografia, la territorial/v2, el plan
y su ruteo), y lo publicado: el TOTAL, un desglose visible, una celda SUPRIMIDO sin valor, la
recuperacion neta (un dia con cobertura, y el horizonte NO_DISPONIBLE porque un dia no tiene pagos
conocidos), la contactabilidad, las promesas, la decision, el campo, el ruteo, los dos embudos y
sus controles, todos OK, sin un solo dato de una cuenta.

Con --predictivo, recorre predictive/model-v1 sobre un escenario predictivo ya cargado
(cargar-escenario-predictivo) y el manifiesto que construir-manifiesto-predictivo propuso en su
directorio (manifiesto.json): las features y los targets versionados, sin un identificador ni un
dato personal entre las features; un entrenamiento por la API que el worker publica, con cada
artefacto y el hash del modelo recalculados aqui; el scoring del ultimo corte con sus tres
probabilidades, su riesgo complementario exacto, su recuperacion positiva esperada y su
feature_hash; su drift, una explicacion verificada y las predicciones de una cuenta; y el scoring
BACKTEST del corte de TEST con su feature_known_at. Sin el archivo, solo hace eso.

Con --optimizacion (y --predictivo), recorre optimization/v1 en sombra sobre el ultimo corte del
escenario, con su scoring y una decision/v2 OPERATIVO del mismo corte: una configuracion pequena
(un solo pool DIGITAL) registrada, reutilizada y publicada; la ejecucion, que la API deja EN_PROCESO
y el worker resuelve; el certificado de los cuatro niveles (OPTIMAL, cota = objetivo, gap 0); el
valor del plan contra el optimo calculado aqui aparte; la capacidad sin exceder, ninguna asignacion
inadmisible, todo o nada, la explicacion de una cuenta y sus planes; un minimo imposible,
INFACTIBLE sin ningun resultado y con su diagnostico; y un scoring de otro corte, que no se abre.
Con --ruteo-oficial, tambien CAMPO sobre el corte oficial: su scoring a la frontera de su
decision/v2 y un pool por gestor con paradas PROGRAMADA; cada cuenta en CAMPO es una parada
PROGRAMADA de su propio gestor, con su orden.

Solo usa la biblioteca estandar, para correr igual en el CI, dentro del contenedor o en una
laptop:

    python scripts/prueba_de_humo.py datos/cartera_sintetica.xlsx
    python scripts/prueba_de_humo.py datos/humo.xlsx --oficial datos/oficial.zip --corte 2026-09-30
    python scripts/prueba_de_humo.py datos/humo.xlsx --pagos datos/pagos.zip
    python scripts/prueba_de_humo.py datos/humo.xlsx --escenario datos/escenario
    python scripts/prueba_de_humo.py humo.xlsx --oficial oficial.zip --corte 2026-09-30 --geografia
    python scripts/prueba_de_humo.py humo.xlsx --oficial oficial.zip --corte 2026-09-30
        --pagos pagos.zip --geografia --campo
    python scripts/prueba_de_humo.py humo.xlsx --oficial oficial.zip --corte 2026-09-30
        --pagos pagos.zip --geografia --campo --ruteo <red_vial_version_id>
        --base-ruteo=<lon>,<lat> --analytics
    python scripts/prueba_de_humo.py --predictivo datos/predictivo
    python scripts/prueba_de_humo.py --predictivo datos/predictivo --optimizacion
        --ruteo-oficial <ruteo_run_id>

La base va unida a su opcion: en Mexico su longitud es negativa, y separada, argparse la toma por
otra opcion.

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
from datetime import datetime, timedelta
from pathlib import Path

BASE = os.environ.get("MC_URL_API", "http://localhost:8000").rstrip("/")
CLAVE = os.environ.get("MC_API_KEY", "clave-local-de-desarrollo")
ESPERA_MAXIMA = 120  # segundos
PAQUETE = Path(__file__).resolve().parents[1] / "src" / "motor_cartera" / "__init__.py"
ETAPAS = ["INGESTA", "DECISION", "TERRITORIAL", "RUTEO"]


def pedir(metodo, ruta, *, cuerpo=None, tipo=None, con_clave=True, timeout=30, extra=None):
    encabezados = {"X-API-Key": CLAVE} if con_clave else {}
    if tipo:
        encabezados["Content-Type"] = tipo
    encabezados.update(extra or {})
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


def encabezado(encabezados: dict, nombre: str) -> str | None:
    """Una cabecera de la respuesta, como la haya escrito el servidor (uvicorn, en minusculas)."""
    return next((v for k, v in encabezados.items() if k.lower() == nombre.lower()), None)


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


# --- el motor de pagos ----------------------------------------------------------------------------

ESPERA_MOTOR = 600  # segundos: el motor va al final de la cola, despues de la historia
RECEPCION = "Fecha_Recepci\u00f3n"  # la columna de pagos/v1, sin un acento en el codigo
MONTO = "Recuperaci\u00f3n_por_Gesti\u00f3n"


def _filas_de_pagos(archivo: Path) -> list[dict]:
    """Las filas de un archivo de pagos del escenario, con sus 23 columnas como texto."""
    if archivo.suffix == ".zip":
        with zipfile.ZipFile(archivo) as paquete:
            (miembro,) = [n for n in paquete.namelist() if n.upper().startswith("PAGOS")]
            texto = io.TextIOWrapper(paquete.open(miembro), encoding="utf-8")
            return list(csv.DictReader(texto))
    with archivo.open(encoding="utf-8", newline="") as texto:
        return list(csv.DictReader(texto))


def seguir_el_motor() -> list[dict]:
    """Las interpretaciones de la cartera, cuando ya ninguna sigue EN_PROCESO."""
    limite = time.monotonic() + ESPERA_MOTOR
    while True:
        estado, _, pagina = pedir("GET", "/motor-pagos?por_pagina=500")
        elementos = pagina.get("elementos", [])
        en_proceso = [e for e in elementos if e["estado"] == "EN_PROCESO"]
        if estado == 200 and elementos and not en_proceso:
            return elementos
        if time.monotonic() > limite:
            return elementos
        time.sleep(1)


def el_motor_de_pagos(directorio: Path) -> None:
    """La interpretacion de los pagos del escenario: cada ventana la interpreta un trabajo
    MOTOR_PAGOS que abrio su historia, cada observacion tiene un resultado, las copias exactas de un
    archivo son un solo movimiento con sus dos observaciones y su archivo original, y la Cuenta 360
    distingue los pagos observados de los movimientos."""
    manifiesto = json.loads((directorio / "escenario.json").read_text(encoding="utf-8"))
    print(f"Motor de pagos con los pagos del escenario de {directorio}")
    filas = [f for p in manifiesto["periodos"] for f in _filas_de_pagos(directorio / p["archivo"])]
    repetidos = sum(p["repetidos_exactos"] for p in manifiesto["periodos"])
    meses = sorted({f[RECEPCION][:7] for f in filas})

    todas = seguir_el_motor()
    vigentes = {e["periodo"]: e for e in todas if e["vigente"]}
    esperar(
        all(m in vigentes for m in meses)
        and not [e for e in todas if e["estado"] == "EN_PROCESO"]
        and all(
            (e["estado"], e["resultado"]) == ("EXITOSA", "INTERPRETACION_PUBLICADA")
            and e["calidad"]["observaciones_clasificadas"] == e["calidad"]["observaciones_leidas"]
            for e in vigentes.values()
        ),
        f"GET /motor-pagos: {len(todas)} interpretaciones, vigentes {', '.join(sorted(vigentes))}, "
        "cada una EXITOSA y con un resultado por observacion",
    )
    # El escenario es la unica fuente de pagos de sus meses: cada una de sus filas, y cada repetido
    # exacto, en la interpretacion de su ventana.
    del_escenario = [vigentes[m] for m in meses]
    leidas = sum(e["calidad"]["observaciones_leidas"] for e in del_escenario)
    copias = sum(e["calidad"]["duplicados_exactos"] for e in del_escenario)
    esperar(
        leidas == len(filas) and copias == repetidos,
        f"{leidas} pagos observados del escenario interpretados, con {copias} duplicados exactos "
        f"(el manifiesto dice {repetidos})",
    )
    ejecucion = del_escenario[-1]
    estado, _, por_id = pedir("GET", f"/motor-pagos/{ejecucion['motor_pagos_run_id']}")
    estado_t, _, trabajo = pedir("GET", f"/trabajos/{ejecucion['trabajo_id']}")
    esperar(
        estado == estado_t == 200
        and por_id["version_motor"] == "motor-pagos/v1"
        and "no un saldo contable" in por_id["recuperacion"]["aviso"]
        and (trabajo["tipo"], trabajo["estado"], trabajo["flujo_id"])
        == ("MOTOR_PAGOS", "COMPLETADO", None)
        and trabajo["objetivo_run_id"] == ejecucion["motor_pagos_run_id"],
        "GET /motor-pagos/{motor_pagos_run_id} y su trabajo MOTOR_PAGOS, COMPLETADO y sin flujo",
    )

    # Una fila que el archivo trae dos veces: dos pagos observados, un solo movimiento.
    vistas: dict[tuple, int] = {}
    for fila in filas:
        llave = tuple(sorted(fila.items()))
        vistas[llave] = vistas.get(llave, 0) + 1
    repetida = dict(next(llave for llave, veces in vistas.items() if veces == 2))
    cliente = repetida["Cliente_Unico"]
    estado, _, movimientos = pedir("GET", f"/movimientos?cliente_unico={cliente}&por_pagina=500")
    (doble,) = [m for m in movimientos.get("elementos", []) if m["observaciones"] == 2]
    estado_m, _, detalle = pedir("GET", f"/movimientos/{doble['movimiento_id']}")
    estado_o, _, observaciones = pedir(
        "GET", f"/movimientos/{doble['movimiento_id']}/observaciones"
    )
    originales = {
        hashlib.sha256((directorio / p["archivo"]).read_bytes()).hexdigest()
        for p in manifiesto["periodos"]
    }
    esperar(
        estado == estado_m == estado_o == 200
        and detalle["representante"]["artefacto_original"]["sha256"] in originales
        and observaciones["total"] == 2
        and [o["clasificacion"] for o in observaciones["elementos"]][1] == "DUPLICADO_EXACTO",
        f"{cliente}: GET /movimientos/{{id}} y /observaciones: un movimiento que la fuente "
        "reporto dos veces, con su archivo original y su fila",
    )

    # La Cuenta 360 del mismo cliente: lo observado y lo interpretado, por separado.
    filas_del_cliente = [f for f in filas if f["Cliente_Unico"] == cliente]
    distintas = len({tuple(sorted(f.items())) for f in filas_del_cliente})
    estado, _, encontrada = pedir("GET", f"/cuentas?cliente_unico={cliente}")
    cuenta_id = encontrada.get("cuenta_id")
    _, _, resumen = pedir("GET", f"/cuentas/{cuenta_id}")
    _, _, observados = pedir("GET", f"/cuentas/{cuenta_id}/pagos-observados?por_pagina=500")
    _, _, interpretados = pedir("GET", f"/cuentas/{cuenta_id}/movimientos?por_pagina=500")
    pagos = resumen.get("resumen_pagos", {})
    esperar(
        estado == 200
        and observados["total"] == pagos["observaciones"] == len(filas_del_cliente)
        and interpretados["total"] == pagos["movimientos_canonicos"] == distintas
        and all(m["contexto_temporal"] is not None for m in interpretados["elementos"])
        and "No son el libro contable" in interpretados["aviso"],
        f"{cliente}: {observados['total']} pagos observados y {interpretados['total']} "
        "movimientos en su Cuenta 360, con su contexto temporal",
    )


# --- el lifecycle de cobranza y la atribucion ----------------------------------------------------

ESPERA_LIFECYCLE = 600  # segundos: la evaluacion y la atribucion van despues del motor de pagos


def enviar(ruta: str, cuerpo: dict, *, llave: str | None = None):
    """Un POST con cuerpo JSON y, si se da, su Idempotency-Key."""
    extra = {"Idempotency-Key": llave} if llave else None
    return pedir(
        "POST", ruta, cuerpo=json.dumps(cuerpo).encode(), tipo="application/json", extra=extra
    )


def seguir(ruta: str) -> dict:
    """Consulta un recurso mientras siga EN_PROCESO, hasta ESPERA_LIFECYCLE."""
    limite = time.monotonic() + ESPERA_LIFECYCLE
    while True:
        estado, _, recurso = pedir("GET", ruta)
        if estado != 200 or recurso["estado"] != "EN_PROCESO" or time.monotonic() > limite:
            return recurso
        time.sleep(1)


def _cuenta_de(cliente: str) -> str:
    """La cuenta canonica del cliente, cuando su historia ya la publico."""
    limite = time.monotonic() + ESPERA_HISTORIA
    while True:
        estado, _, encontrada = pedir("GET", f"/cuentas?cliente_unico={cliente}")
        if estado == 200 or time.monotonic() > limite:
            esperar(estado == 200, f"{cliente}: su cuenta canonica existe ({estado})")
            return encontrada["cuenta_id"]
        time.sleep(1)


def el_lifecycle(pagos: Path, corte: str) -> None:
    """El lifecycle de punta a punta sobre la cartera oficial y sus pagos: cuenta, gestion,
    promesa, movimiento, evaluacion a una fecha explicita (la del corte), atribucion y Cuenta 360;
    y ademas una gestion sin contacto, una anulada, una atribucion ambigua, la idempotencia de las
    escrituras y los trabajos durables que lo ejecutan. Las gestiones ocurren antes de los pagos
    del archivo: un dia antes, en la hora de la fuente."""
    print(f"Lifecycle de cobranza y atribucion con los pagos de {pagos}")
    filas = _filas_de_pagos(pagos)
    veces: dict[str, int] = {}
    for fila in filas:
        veces[fila["Cliente_Unico"]] = veces.get(fila["Cliente_Unico"], 0) + 1
    # Clientes con un solo pago, positivo: su movimiento es uno y es un PAGO.
    unicos = sorted(
        (f for f in filas if veces[f["Cliente_Unico"]] == 1 and not f[MONTO].startswith("-")),
        key=lambda f: (f[RECEPCION], f["Cliente_Unico"]),
    )
    esperar(len(unicos) >= 4, f"{len(unicos)} clientes con un solo pago en el archivo")
    con_promesa, ambiguo, anulado, sin_contacto = unicos[:4]
    periodo = con_promesa[RECEPCION][:7]
    todas = seguir_el_motor()
    vigente = next((e for e in todas if e["vigente"] and e["periodo"] == periodo), None)
    esperar(
        vigente is not None and vigente["estado"] == "EXITOSA",
        f"la ventana {periodo} de los pagos tiene una interpretacion vigente",
    )

    def instante(fila: dict, dias: int) -> str:
        cuando = datetime.fromisoformat(fila[RECEPCION]) - timedelta(days=dias)
        return f"{cuando.isoformat()}-06:00"

    def gestion(fila: dict, dias: int, **campos) -> tuple[str, str]:
        cuenta_id = _cuenta_de(fila["Cliente_Unico"])
        cuerpo = {
            "ocurrido_en": instante(fila, dias),
            "canal": "TELEFONICA",
            "medio": "LLAMADA",
            "nivel_contacto": "CONTACTO_TITULAR",
            "resultado": "CONTACTO",
            "actor_ref": "AGT-0007",
            **campos,
        }
        estado, encabezados, creada = enviar(
            f"/cuentas/{cuenta_id}/gestiones", cuerpo, llave=f"humo-{uuid.uuid4().hex}"
        )
        esperar(
            estado == 201 and ubicacion_de(encabezados) == f"/gestiones/{creada['gestion_id']}",
            f"POST /cuentas/{{cuenta_id}}/gestiones de {fila['Cliente_Unico']}: {estado} "
            f"{cuerpo['nivel_contacto']} {cuerpo['resultado']}",
        )
        return cuenta_id, creada["gestion_id"]

    # Cuenta -> gestion -> promesa, y la idempotencia de la escritura.
    cuenta_id = _cuenta_de(con_promesa["Cliente_Unico"])
    llave = f"humo-{uuid.uuid4().hex}"
    cuerpo = {
        "ocurrido_en": instante(con_promesa, 1),
        "canal": "TELEFONICA",
        "medio": "LLAMADA",
        "nivel_contacto": "CONTACTO_TITULAR",
        "resultado": "PROMESA",
    }
    estado, _, creada = enviar(f"/cuentas/{cuenta_id}/gestiones", cuerpo, llave=llave)
    estado_r, encabezados_r, repetida = enviar(
        f"/cuentas/{cuenta_id}/gestiones", cuerpo, llave=llave
    )
    estado_o, _, otra = enviar(
        f"/cuentas/{cuenta_id}/gestiones", {**cuerpo, "resultado": "RECHAZO"}, llave=llave
    )
    gestion_promesa = creada.get("gestion_id")
    esperar(
        (estado, estado_r, estado_o) == (201, 200, 409)
        and encabezado(encabezados_r, "Idempotent-Replayed") == "true"
        and repetida["gestion_id"] == gestion_promesa
        and otra["codigo"] == "IDEMPOTENCY_KEY_REUTILIZADA",
        "la misma Idempotency-Key: 201, luego 200 con la misma gestion, y 409 con otros datos",
    )
    monto = con_promesa[MONTO]
    estado, _, promesa = enviar(
        f"/gestiones/{gestion_promesa}/promesas",
        {"monto_prometido": monto, "fecha_limite": con_promesa[RECEPCION][:10]},
        llave=f"humo-{uuid.uuid4().hex}",
    )
    esperar(
        estado == 201 and promesa["estado_operativo"] == "VIGENTE",
        f"POST /gestiones/{{gestion_id}}/promesas: {estado}, {monto} al "
        f"{con_promesa[RECEPCION][:10]}",
    )

    # Una gestion sin contacto, dos candidatas de un mismo pago y una gestion anulada.
    gestion(sin_contacto, 1, nivel_contacto="SIN_CONTACTO", resultado="SIN_RESPUESTA")
    gestion(ambiguo, 2)
    gestion(ambiguo, 1, nivel_contacto="CONTACTO_TERCERO")
    _, anulada = gestion(anulado, 1)
    estado, _, anulacion = enviar(
        f"/gestiones/{anulada}/anulaciones",
        {"ocurrido_en": instante(anulado, 0), "motivo": "Capturada por error."},
        llave=f"humo-{uuid.uuid4().hex}",
    )
    _, _, vista = pedir("GET", f"/gestiones/{anulada}")
    esperar(
        estado == 201 and vista["estado"] == "ANULADA" and vista["anulacion"] is not None,
        "POST /gestiones/{gestion_id}/anulaciones: 201, y la gestion sigue visible, ANULADA",
    )

    # La evaluacion de las promesas, a una fecha de corte explicita: la del corte, despues de
    # todos los pagos del archivo.
    as_of = corte
    esperar(unicos[-1][RECEPCION][:10] <= as_of, f"los pagos del archivo llegan hasta el {as_of}")
    estado, encabezados, pedida = enviar("/evaluaciones-promesas", {"as_of": as_of})
    esperar(
        estado == 201 and pedida["estado"] == "EN_PROCESO",
        f"POST /evaluaciones-promesas al {as_of}: {estado} EN_PROCESO",
    )
    evaluacion = seguir(ubicacion_de(encabezados))
    _, _, de_la_promesa = pedir("GET", f"/promesas/{promesa['promesa_id']}")
    ultima = de_la_promesa.get("ultima_evaluacion") or {}
    esperar(
        evaluacion.get("estado") == "EXITOSA"
        and (ultima.get("estado"), ultima.get("monto_observado")) == ("CUMPLIDA", monto)
        and "no afirma" in ultima.get("aviso", "").lower(),
        f"la evaluacion termino {evaluacion.get('estado')}: la promesa quedo "
        f"{ultima.get('estado')} con {ultima.get('monto_observado')} observados",
    )

    # La atribucion de la ventana.
    estado, encabezados, pedida = enviar("/atribuciones", {"periodo": periodo})
    esperar(
        estado == 201 and pedida["estado"] == "EN_PROCESO" and pedida["ventana_dias"] >= 1,
        f"POST /atribuciones {periodo}: {estado} EN_PROCESO, ventana de "
        f"{pedida.get('ventana_dias')} dias",
    )
    atribucion = seguir(ubicacion_de(encabezados))
    esperar(
        atribucion.get("estado") == "EXITOSA"
        and atribucion["vigente"]
        and atribucion["motor_pagos_run_id"] == vigente["motor_pagos_run_id"]
        and "no causalidad" in atribucion["aviso"],
        f"la atribucion termino {atribucion.get('estado')}: "
        f"{atribucion.get('conteos', {}).get('asociados')} asociados, "
        f"{atribucion.get('conteos', {}).get('ambiguos')} ambiguos, "
        f"{atribucion.get('conteos', {}).get('sin_candidato')} sin candidata",
    )

    def resultado(fila: dict) -> dict:
        _, _, pagina = pedir(
            "GET",
            f"{ubicacion_de(encabezados)}/resultados?cliente_unico={fila['Cliente_Unico']}",
        )
        (unico,) = pagina["elementos"]
        return unico

    asociado, ambigua, sin = resultado(con_promesa), resultado(ambiguo), resultado(anulado)
    esperar(
        (asociado["clasificacion"], asociado["gestion_id"])
        == ("ASOCIACION_UNICA", gestion_promesa),
        f"{con_promesa['Cliente_Unico']}: su pago tiene una gestion candidata, la de su promesa",
    )
    esperar(
        (ambigua["clasificacion"], ambigua["gestion_id"], len(ambigua["candidatas"]))
        == ("AMBIGUA", None, 2),
        f"{ambiguo['Cliente_Unico']}: su pago queda AMBIGUA, con sus dos candidatas y sin elegir",
    )
    esperar(
        sin["clasificacion"] == "SIN_GESTION_CANDIDATA"
        and sin["motivos"][0]["gestiones_anuladas"] == 1,
        f"{anulado['Cliente_Unico']}: su gestion anulada no es candidata",
    )

    # Los trabajos durables que las ejecutaron.
    for recurso, tipo in ((evaluacion, "EVALUACION_PROMESAS"), (atribucion, "ATRIBUCION")):
        estado, _, trabajo = pedir("GET", f"/trabajos/{recurso['trabajo_id']}")
        esperar(
            estado == 200 and (trabajo["tipo"], trabajo["estado"]) == (tipo, "COMPLETADO"),
            f"su trabajo {tipo}: {trabajo.get('estado')} ({trabajo.get('intentos')})",
        )

    # La Cuenta 360 y la linea de tiempo, sin mezclar las tres verdades.
    _, _, resumen = pedir("GET", f"/cuentas/{cuenta_id}")
    lifecycle = resumen.get("lifecycle_resumen", {})
    esperar(
        (lifecycle.get("gestiones"), lifecycle.get("promesas"), lifecycle.get("promesas_vigentes"))
        == (1, 1, 1)
        and (lifecycle.get("ultima_atribucion") or {}).get("clasificacion") == "ASOCIACION_UNICA",
        f"GET /cuentas/{{cuenta_id}}: lifecycle_resumen con {lifecycle.get('gestiones')} "
        "gestion, su promesa y su ultima atribucion",
    )
    estado, _, linea = pedir("GET", f"/cuentas/{cuenta_id}/lifecycle?orden=asc")
    dominios = {e["dominio"] for e in linea.get("elementos", [])}
    esperar(
        estado == 200
        and {"OPERACIONAL", "ECONOMICO"}
        <= dominios
        <= {"OPERACIONAL", "FUENTE_CORTE", "ECONOMICO"},
        f"GET /cuentas/{{cuenta_id}}/lifecycle: {linea.get('total')} elementos en "
        f"{', '.join(sorted(dominios))}",
    )
    estado, _, del_pago = pedir("GET", f"/movimientos/{asociado['movimiento_id']}/atribuciones")
    esperar(
        estado == 200 and del_pago["total"] == 1 and del_pago["elementos"][0]["vigente"],
        "GET /movimientos/{movimiento_id}/atribuciones: la atribucion vigente del pago",
    )


# --- decision/v2 ----------------------------------------------------------------------------------

REC, SAT = "RECUPERACION_RECIENTE", "SATURACION_SIN_CONTACTO"


def la_decision_v2(oficial: Path, pagos: Path, corte: str, *, hay_cortes_posteriores: bool) -> dict:
    """decision/v2 de punta a punta sobre el corte de la cartera oficial: CorteCanonico, decision a
    un as_of y un known_at declarados, features, resultado, explicacion y Cuenta 360. Antes registra
    tres gestiones sin contacto en dos cuentas, para ver un modificador -1 (un pago reciente), uno
    +1 (saturacion sin contacto) y dos que se cancelan (las dos cosas en la misma cuenta)."""
    print(f"Decision v2 sobre el corte {corte}")
    estado, _, cortes = pedir("GET", "/cartera/cortes?por_pagina=500")
    del_dia = next((c for c in cortes.get("elementos", []) if c["fecha_corte"] == corte), None)
    esperar(
        estado == 200 and del_dia is not None,
        f"GET /cartera/cortes: el corte canonico del {corte}, con {del_dia and del_dia['cuentas']} "
        "cuentas",
    )
    filas = _filas_de_pagos(pagos)
    veces: dict[str, int] = {}
    for fila in filas:
        veces[fila["Cliente_Unico"]] = veces.get(fila["Cliente_Unico"], 0) + 1
    unicos = sorted(
        (f for f in filas if veces[f["Cliente_Unico"]] == 1 and not f[MONTO].startswith("-")),
        key=lambda f: (f[RECEPCION], f["Cliente_Unico"]),
    )
    # Las cuatro primeras son del lifecycle; estas, no tienen ninguna gestion.
    menos, cancelan = unicos[4]["Cliente_Unico"], unicos[5]["Cliente_Unico"]
    sin_pagos = sorted(set(_clientes(oficial)) - set(veces))
    esperar(len(sin_pagos) > 0, f"{len(sin_pagos)} cuentas del corte sin pagos en el archivo")
    mas = sin_pagos[0]
    registradas = []
    for cliente in (mas, cancelan):
        cuenta_id = _cuenta_de(cliente)
        for dia in (27, 28, 29):
            estado, _, gestion = enviar(
                f"/cuentas/{cuenta_id}/gestiones",
                {
                    "ocurrido_en": f"{corte[:8]}{dia}T10:00:00-06:00",
                    "canal": "TELEFONICA",
                    "medio": "LLAMADA",
                    "nivel_contacto": "SIN_CONTACTO",
                    "resultado": "SIN_RESPUESTA",
                },
                llave=f"humo-{uuid.uuid4().hex}",
            )
            esperar(estado == 201, f"{cliente}: una llamada sin contacto el {dia} ({estado})")
            registradas.append(datetime.fromisoformat(gestion["registrado_en"]))

    pedida = {"corte_id": del_dia["corte_id"]}
    if hay_cortes_posteriores:
        # El escenario trae cortes posteriores: este ya no es el mas reciente, y una decision
        # OPERATIVO de el se niega. Se pide como BACKTEST, declarando su frontera de conocimiento.
        estado, _, error = enviar("/decisiones-v2", pedida)
        esperar(
            estado == 422 and error["codigo"] == "CORTE_NO_VIGENTE",
            f"POST /decisiones-v2 OPERATIVO de un corte que ya no es el ultimo: {estado} "
            f"{error.get('codigo')}",
        )
        estado, _, error = enviar("/decisiones-v2", {**pedida, "modo": "BACKTEST"})
        esperar(
            estado == 422 and error["codigo"] == "KNOWN_AT_REQUERIDO",
            f"un BACKTEST sin known_at: {estado} {error.get('codigo')}",
        )
        # La frontera es la de la ultima llamada registrada, con el reloj de la base que la
        # registro: el cliente no tiene que adivinar su desfase con el servidor, y cualquier cosa
        # registrada despues no existe para esta decision.
        known_at = max(registradas).isoformat()
        pedida.update(modo="BACKTEST", known_at=known_at)
    estado, encabezados, abierta = enviar("/decisiones-v2", pedida)
    esperar(
        estado == 201
        and abierta["estado"] == "EN_PROCESO"
        and abierta["as_of"] == corte
        and ubicacion_de(encabezados) == f"/decisiones-v2/{abierta['decision_run_id']}",
        f"POST /decisiones-v2: {estado} EN_PROCESO, as_of {abierta.get('as_of')}, known_at "
        f"{abierta.get('known_at')}",
    )
    decision = seguir(ubicacion_de(encabezados))
    esperar(
        decision.get("estado") == "EXITOSA"
        and decision["conteos"]["cuentas_decididas"] == del_dia["cuentas"]
        and decision["conteos"]["cuentas_fallidas"] == 0
        and decision["known_at"] == abierta["known_at"],
        f"la decision termino {decision.get('estado')}: {decision.get('detalle', '')[:160]}",
    )
    esperar(
        decision["versiones"]
        == {
            "motor": "decision/v2",
            "features": "features-decision/v1",
            "politica": "decision-policy/public-v1",
        }
        and len(decision["hash_politica"]) == len(decision["firma_inputs"]) == 64
        and "no es un modelo predictivo" in decision["aviso"],
        f"versiones motor/features/politica y hash de la politica {decision['hash_politica'][:12]}"
        f"...; firma de entrada {decision['firma_inputs'][:12]}...",
    )
    esperar(
        decision["disponibilidad"]["pagos"]["7d"] is True,
        "disponibilidad a known_at: pagos "
        + ", ".join(f"{v} {ok}" for v, ok in decision["disponibilidad"]["pagos"].items())
        + "; advertencias "
        + ", ".join(a["codigo"] for a in decision["advertencias"]),
    )

    def explicada(cliente: str) -> tuple[str, dict]:
        cuenta_id = _cuenta_de(cliente)
        estado, _, cuerpo = pedir(
            "GET", f"/decisiones-v2/{decision['decision_run_id']}/resultados/{cuenta_id}"
        )
        esperar(estado == 200, f"GET /decisiones-v2/{{id}}/resultados/{{cuenta_id}} de {cliente}")
        return cuenta_id, cuerpo

    for cliente, ajuste, activas in (
        (menos, -1, [REC]),
        (mas, 1, [SAT]),
        (cancelan, 0, [REC, SAT]),
    ):
        _, cuerpo = explicada(cliente)
        aplicadas = [r["codigo"] for r in cuerpo["reglas"] if r["activa"]]
        esperar(
            cuerpo["ajuste_total"] == ajuste
            and aplicadas == activas
            and [m["codigo"] for m in cuerpo["motivos"][1:-2]] == activas
            and cuerpo["verificacion"]
            == {"feature_hash": True, "decision_hash": True, "motivos": True},
            f"{cliente}: {cuerpo['prioridad_base']} {ajuste:+d} = {cuerpo['prioridad_final']} "
            f"({cuerpo['canal_recomendado']}) por {', '.join(activas)}; verificada",
        )
        for regla in cuerpo["reglas"]:
            if regla["activa"]:
                condiciones = "; ".join(
                    f"{c['feature']} = {c['valor']} {c['operador']} {c['umbral']}"
                    for c in regla["condiciones"]
                )
                print(f"      {regla['codigo']} ({regla['efecto']:+d}): {condiciones}")

    cuenta_mas, de_mas = explicada(mas)
    final = de_mas["prioridad_final"]
    estado, _, pagina = pedir(
        "GET",
        f"/decisiones-v2/{decision['decision_run_id']}/resultados?prioridad_final={final}"
        "&por_pagina=5",
    )
    esperar(
        estado == 200
        and pagina["total"] == decision["distribucion"]["prioridad_final"][final]
        and {r["prioridad_final"] for r in pagina["elementos"]} == {final},
        f"GET /decisiones-v2/{{id}}/resultados?prioridad_final={final}: {pagina.get('total')} "
        "decisiones, por su indice",
    )
    estado, _, historia = pedir("GET", f"/cuentas/{cuenta_mas}/decisiones-v2")
    estado_c, _, del_corte = pedir("GET", f"/cartera/cortes/{del_dia['corte_id']}/decisiones-v2")
    esperar(
        estado == estado_c == 200
        and historia["total"] == 1
        and historia["elementos"][0]["decision_run_id"] == decision["decision_run_id"]
        and del_corte["total"] == 1,
        "GET /cuentas/{cuenta_id}/decisiones-v2 y /cartera/cortes/{corte_id}/decisiones-v2: la "
        "decision de la cuenta y la del corte",
    )
    _, _, resumen = pedir("GET", f"/cuentas/{cuenta_mas}")
    esperar(
        resumen.get("ultima_decision_v2")
        == {
            "decision_run_id": decision["decision_run_id"],
            "as_of": corte,
            "prioridad": final,
            "canal": de_mas["canal_recomendado"],
        },
        f"GET /cuentas/{{cuenta_id}}: ultima_decision_v2 {final}, {de_mas['canal_recomendado']}",
    )
    estado, _, trabajo = pedir("GET", f"/trabajos/{decision['trabajo_id']}")
    esperar(
        estado == 200 and (trabajo["tipo"], trabajo["estado"]) == ("DECISION_V2", "COMPLETADO"),
        f"su trabajo DECISION_V2: {trabajo.get('estado')} ({trabajo.get('intentos')})",
    )
    estado, _, error = enviar("/decisiones-v2", {**pedida, "known_at": decision["known_at"]})
    esperar(
        estado == 409 and error["codigo"] == "DECISION_V2_YA_GENERADA",
        f"las mismas entradas otra vez: {estado} {error.get('codigo')}",
    )
    return decision


# --- la geografia real, las bases operativas y territorial/v2 -----------------------------------

LLAVES_DE_COORDENADAS = ('"latitud', '"longitud', '"lat"', '"lon"', '"punto"')


def _sin_coordenadas(cuerpo, donde: str) -> None:
    """La coordenada observada de una cuenta no sale por la API: ninguna llave que la lleve."""
    texto = json.dumps(cuerpo)
    esperar(not any(llave in texto for llave in LLAVES_DE_COORDENADAS), f"{donde}: sin coordenadas")


def _corte_canonico(corte: str) -> dict:
    """El corte canonico de esa fecha, cuando su historia ya lo publico."""
    limite = time.monotonic() + ESPERA_HISTORIA
    while True:
        estado, _, cortes = pedir("GET", "/cartera/cortes?por_pagina=500")
        del_dia = next((c for c in cortes.get("elementos", []) if c["fecha_corte"] == corte), None)
        if del_dia is not None or time.monotonic() > limite:
            esperar(
                estado == 200 and del_dia is not None,
                f"GET /cartera/cortes: el corte canonico del {corte}, con "
                f"{del_dia and del_dia['cuentas']} cuentas",
            )
            return del_dia
        time.sleep(1)


def la_geografia(corte: str) -> tuple[dict, dict]:
    """geografia/v1 y territorial/v2 de punta a punta sobre el corte de la cartera oficial, que trae
    coordenadas sinteticas: la referencia importada, la geografia del corte con el municipio de cada
    cuenta, una version de bases operativas, la asignacion de territorial/v2 y la Cuenta 360. Con
    una cuenta asignada, una sin coordenadas, una que discrepa de la fuente y una sin base, y sin
    que ninguna respuesta lleve una coordenada."""
    print(f"Geografia y territorial/v2 sobre el corte {corte}")
    estado, _, versiones = pedir("GET", "/geografia/versiones?por_pagina=100")
    publicada = next(
        (v for v in versiones.get("elementos", []) if v["estado"] == "PUBLICADA"), None
    )
    esperar(
        estado == 200 and publicada is not None and '"geometria"' not in json.dumps(versiones),
        f"GET /geografia/versiones: la referencia {publicada and publicada['producto']} "
        f"({publicada and publicada['edicion']}), {publicada and publicada['municipios']} "
        "municipios, sin geometrias",
    )
    del_dia = _corte_canonico(corte)

    pedida = {
        "corte_id": del_dia["corte_id"],
        "geografia_version_id": publicada["geografia_version_id"],
    }
    estado, encabezados, abierta = enviar("/geografia/ejecuciones", pedida)
    esperar(
        estado == 201
        and abierta["estado"] == "EN_PROCESO"
        and ubicacion_de(encabezados) == f"/geografia/ejecuciones/{abierta['geografia_run_id']}",
        f"POST /geografia/ejecuciones: {estado} {abierta.get('estado')}",
    )
    geografia = seguir(ubicacion_de(encabezados))
    conteos = geografia.get("conteos", {})
    por_estado = {k: v for k, v in conteos.items() if k != "cuentas_objetivo"}
    esperar(
        geografia.get("estado") == "EXITOSA"
        and conteos["cuentas_objetivo"] == del_dia["cuentas"] == sum(por_estado.values()),
        f"la geografia termino {geografia.get('estado')}: "
        + ", ".join(f"{k} {v}" for k, v in por_estado.items()),
    )
    esperar(
        conteos["validas"] > 0 and conteos["sin_coordenadas"] > 0 and conteos["discrepantes"] > 0,
        "con cuentas validas, sin coordenadas y que discrepan de la fuente",
    )
    _sin_coordenadas(geografia, "GET /geografia/ejecuciones/{geografia_run_id}")
    estado, _, error = enviar("/geografia/ejecuciones", pedida)
    esperar(
        estado == 409 and error["codigo"] == "GEOGRAFIA_YA_GENERADA",
        f"las mismas entradas otra vez: {estado} {error.get('codigo')}",
    )

    ruta = f"/geografia/ejecuciones/{geografia['geografia_run_id']}/resultados"

    def primeras(estado_geografia: str, cuantas: int) -> dict:
        estado, _, pagina = pedir(
            "GET", f"{ruta}?estado_geografia={estado_geografia}&por_pagina={cuantas}"
        )
        esperar(
            estado == 200
            and bool(pagina["elementos"])
            and {r["estado_geografia"] for r in pagina["elementos"]} == {estado_geografia},
            f"GET {{resultados}}?estado_geografia={estado_geografia}: {pagina.get('total')}, por "
            "su indice",
        )
        _sin_coordenadas(pagina, f"los resultados {estado_geografia}")
        return pagina

    discrepantes = primeras("DISCREPANCIA_MUNICIPIO", 5)
    esperar(
        discrepantes["total"] == conteos["discrepantes"]
        and all(
            r["municipio"]["cvegeo"] != r["municipio_fuente"]
            and r["comparacion_municipio"] == "DISCREPA"
            for r in discrepantes["elementos"]
        ),
        "las que discrepan: el municipio espacial no es el de la fuente, y nada se corrigio",
    )
    discrepante = discrepantes["elementos"][0]
    print(
        f"      {discrepante['cliente_unico']}: la fuente dice {discrepante['municipio_fuente']}, "
        f"el punto cae en {discrepante['municipio']['cvegeo']}"
    )
    sin_coordenadas = primeras("SIN_COORDENADAS", 1)["elementos"][0]
    validas = primeras("VALIDA", 100)["elementos"]
    cubierto = validas[0]["municipio"]["cvegeo"]
    sin_base = next(r for r in validas if r["municipio"]["cvegeo"] != cubierto)
    estado, _, del_municipio = pedir("GET", f"{ruta}?municipio={cubierto}&por_pagina=5")
    esperar(
        estado == 200
        and all(r["municipio"]["cvegeo"] == cubierto for r in del_municipio["elementos"]),
        f"GET {{resultados}}?municipio={cubierto}: {del_municipio.get('total')} cuentas en "
        f"{validas[0]['municipio']['nombre_oficial']}",
    )

    # Una base activa que cubre el municipio de una cuenta valida, y una inactiva que cubre el de
    # otra: la primera queda ASIGNADA, la segunda SIN_BASE_ELEGIBLE. Puntos sinteticos.
    bases = {
        "nombre": f"Bases de la prueba de humo del {corte}",
        "bases": [
            {
                "codigo": "BASE_HUMO",
                "nombre": "Base de la prueba de humo (sintetica)",
                "lat": 18.5,
                "lon": -99.5,
                "activa": True,
                "municipios": [cubierto],
            },
            {
                "codigo": "BASE_INACTIVA",
                "nombre": "Base inactiva de la prueba de humo (sintetica)",
                "lat": 18.6,
                "lon": -99.4,
                "activa": False,
                "municipios": [sin_base["municipio"]["cvegeo"]],
            },
        ],
    }
    estado, _, version_bases = enviar("/bases/versiones", bases)
    esperar(
        estado in (200, 201)
        and (version_bases["bases"], version_bases["activas"], version_bases["coberturas"])
        == (2, 1, 2),
        f"POST /bases/versiones: {estado}, 2 bases (1 activa), hash "
        f"{version_bases.get('hash_configuracion', '')[:12]}...",
    )
    estado, _, otra_vez = enviar("/bases/versiones", {**bases, "nombre": "Otra etiqueta"})
    esperar(
        estado == 200
        and otra_vez["reutilizada"]
        and otra_vez["bases_version_id"] == version_bases["bases_version_id"],
        "el mismo contenido con otro nombre: 200, la misma version reutilizada",
    )
    estado, _, detalle = pedir("GET", f"/bases/versiones/{version_bases['bases_version_id']}")
    esperar(
        estado == 200 and detalle["hash_verificado"] is True,
        "GET /bases/versiones/{bases_version_id}: su hash, vuelto a calcular, es el que se guardo",
    )

    pedida_v2 = {
        "corte_id": del_dia["corte_id"],
        "geografia_run_id": geografia["geografia_run_id"],
        "bases_version_id": version_bases["bases_version_id"],
    }
    estado, encabezados, abierta = enviar("/territorial-v2", pedida_v2)
    esperar(
        estado == 201
        and abierta["estado"] == "EN_PROCESO"
        and ubicacion_de(encabezados) == f"/territorial-v2/{abierta['territorial_run_id']}",
        f"POST /territorial-v2: {estado} {abierta.get('estado')}",
    )
    territorial = seguir(ubicacion_de(encabezados))
    asignaciones = territorial.get("conteos", {})
    por_asignacion = {k: v for k, v in asignaciones.items() if k != "cuentas_objetivo"}
    esperar(
        territorial.get("estado") == "EXITOSA"
        and sum(por_asignacion.values()) == del_dia["cuentas"]
        and asignaciones["asignadas"] > 0
        and asignaciones["revision_geografica"] > 0
        and asignaciones["sin_base"] > 0,
        f"territorial/v2 termino {territorial.get('estado')}: "
        + ", ".join(f"{k} {v}" for k, v in por_asignacion.items()),
    )
    esperar(
        territorial["version_politica"] == "territorial-public-v2"
        and territorial["tolerancia_empate_m"] == "100.00"
        and len(territorial["hash_politica"]) == len(territorial["firma_inputs"]) == 64
        and "capacidad" in territorial["aviso"],
        f"territorial-public-v2 (hash {territorial['hash_politica'][:12]}...), tolerancia de "
        "empate 100.00 m, sin capacidad",
    )
    ruta_v2 = f"/territorial-v2/{territorial['territorial_run_id']}/resultados"
    estado, _, de_la_base = pedir("GET", f"{ruta_v2}?base=BASE_HUMO&por_pagina=5")
    esperar(
        estado == 200
        and de_la_base["total"] == asignaciones["asignadas"]
        and all(
            a["estado_asignacion"] == "ASIGNADA"
            and a["base"]["codigo"] == "BASE_HUMO"
            and a["distancia_base_m"] is not None
            for a in de_la_base["elementos"]
        ),
        f"GET {{asignaciones}}?base=BASE_HUMO: {de_la_base.get('total')} cuentas, con su "
        "distancia geodesica",
    )
    _sin_coordenadas(de_la_base, "las asignaciones")
    asignada = de_la_base["elementos"][0]
    print(
        f"      {asignada['cliente_unico']}: {asignada['base']['codigo']} a "
        f"{asignada['distancia_base_m']} m"
    )
    estado, _, sin_bases = pedir(
        "GET",
        f"{ruta_v2}?estado=SIN_BASE_ELEGIBLE&municipio={sin_base['municipio']['cvegeo']}"
        "&por_pagina=1",
    )
    esperar(
        estado == 200
        and sin_bases["elementos"][0]["motivos"][-1]
        == {"codigo": "BASES_INACTIVAS", "bases": ["BASE_INACTIVA"]},
        "SIN_BASE_ELEGIBLE en el municipio que solo cubre una base inactiva, y lo dice",
    )

    # La Cuenta 360 de cada una: su geografia vigente y su base, sin coordenadas.
    for elegida, geografia_esperada, asignacion, base in (
        (asignada, "VALIDA", "ASIGNADA", "BASE_HUMO"),
        (sin_coordenadas, "SIN_COORDENADAS", "REVISION_GEOGRAFICA", None),
        (discrepante, "DISCREPANCIA_MUNICIPIO", "REVISION_GEOGRAFICA", None),
        (sin_base, "VALIDA", "SIN_BASE_ELEGIBLE", None),
    ):
        estado, _, resumen = pedir("GET", f"/cuentas/{elegida['cuenta_id']}")
        g = resumen.get("geografia_resumen") or {}
        t = g.get("territorial") or {}
        esperar(
            estado == 200
            and g.get("geografia_run_id") == geografia["geografia_run_id"]
            and g.get("estado_geografia") == geografia_esperada
            and t.get("territorial_run_id") == territorial["territorial_run_id"]
            and t.get("estado_asignacion") == asignacion
            and t.get("codigo_base") == base,
            f"GET /cuentas/{{cuenta_id}} de {elegida['cliente_unico']}: {geografia_esperada} en "
            f"{g.get('cvegeo')}, {asignacion} {base or ''}".rstrip(),
        )
        _sin_coordenadas(resumen, "la Cuenta 360")
    estado, _, de_la_cuenta = pedir("GET", f"/cuentas/{asignada['cuenta_id']}/geografia")
    estado_t, _, asignaciones_de_la_cuenta = pedir(
        "GET", f"/cuentas/{asignada['cuenta_id']}/territorial-v2"
    )
    esperar(
        estado == estado_t == 200
        and de_la_cuenta["total"] >= 1
        and asignaciones_de_la_cuenta["elementos"][0]["territorial_run_id"]
        == territorial["territorial_run_id"],
        "GET /cuentas/{cuenta_id}/geografia y /territorial-v2: su historia, sin coordenadas",
    )
    _sin_coordenadas(de_la_cuenta, "la geografia de una cuenta")
    for recurso, tipo in ((geografia, "GEOGRAFIA"), (territorial, "TERRITORIAL_V2")):
        estado, _, trabajo = pedir("GET", f"/trabajos/{recurso['trabajo_id']}")
        esperar(
            estado == 200 and (trabajo["tipo"], trabajo["estado"]) == (tipo, "COMPLETADO"),
            f"su trabajo {tipo}: {trabajo.get('estado')} ({trabajo.get('intentos')})",
        )
    estado, _, error = enviar("/territorial-v2", pedida_v2)
    esperar(
        estado == 409 and error["codigo"] == "TERRITORIAL_V2_YA_GENERADO",
        f"las mismas entradas otra vez: {estado} {error.get('codigo')}",
    )
    return del_dia, geografia


# --- campo/v2: la capacidad, la particion y la asignacion operativa -------------------------------

LLAVES_DE_UBICACION = (
    *LLAVES_DE_COORDENADAS,
    '"x"',
    '"y"',
    '"grid_x"',
    '"grid_y"',
    '"bbox"',
    '"centroide"',
    '"geometria"',
)
SEMANA = ("LUNES", "MARTES", "MIERCOLES", "JUEVES", "VIERNES", "SABADO", "DOMINGO")


def _sin_ubicacion(cuerpo, donde: str) -> None:
    """Ni la coordenada de una cuenta ni la celda, el recuadro o el centroide de una unidad de la
    particion salen por la API."""
    texto = json.dumps(cuerpo)
    esperar(
        not any(llave in texto for llave in LLAVES_DE_UBICACION),
        f"{donde}: sin coordenadas ni celdas",
    )


def _todas(ruta: str) -> list[dict]:
    """Todos los elementos de una lista paginada, de 500 en 500."""
    elementos: list[dict] = []
    pagina = 1
    while True:
        estado, _, cuerpo = pedir("GET", f"{ruta}&por_pagina=500&pagina={pagina}")
        if estado != 200:
            esperar(False, f"GET {ruta}: {estado}")
        elementos.extend(cuerpo["elementos"])
        if not cuerpo["elementos"] or len(elementos) >= cuerpo["total"]:
            return elementos
        pagina += 1


def el_campo(corte: str, del_dia: dict, decision: dict, geografia: dict) -> None:
    """campo/v2 de punta a punta sobre el corte de la cartera oficial. Publica una version de bases
    con dos bases activas sobre los municipios de las candidatas de campo de la decision/v2 (la B
    cubre uno; la A, los demas), la asigna con territorial/v2, publica una configuracion de campo
    sintetica (dos gestores de cupo 1 en la A; en la B, uno cerrado por un festivo ese dia y uno
    inactivo) y pide el plan del dia siguiente al corte, con la fecha declarada. Cada estado sale:
    ASIGNADA, una de ellas rebalanceada por capacidad, SIN_CAPACIDAD, SIN_GESTOR_DISPONIBLE y
    REVISION_TERRITORIAL; y sus cargas, sus unidades, la Cuenta 360 y su trabajo CAMPO_V2, sin que
    ninguna respuesta lleve una coordenada ni una celda."""
    print(f"campo/v2 sobre el corte {corte}")
    fecha_operacion = (datetime.fromisoformat(corte) + timedelta(days=1)).date().isoformat()

    # Las candidatas de campo de la decision/v2, y el municipio de las que tienen coordenadas
    # validas: la base B cubre el primero de esos municipios y la A, los demas. Las que no tienen
    # coordenadas validas quedan en revision territorial.
    candidatas = {
        r["cuenta_id"]
        for r in _todas(
            f"/decisiones-v2/{decision['decision_run_id']}/resultados?canal_recomendado=CAMPO"
        )
    }
    municipio = {
        r["cuenta_id"]: r["municipio"]["cvegeo"]
        for r in _todas(
            f"/geografia/ejecuciones/{geografia['geografia_run_id']}/resultados"
            "?estado_geografia=VALIDA"
        )
    }
    por_municipio: dict[str, int] = {}
    for cuenta in candidatas & municipio.keys():
        por_municipio[municipio[cuenta]] = por_municipio.get(municipio[cuenta], 0) + 1
    con_candidatas = sorted(por_municipio)
    validas = sum(por_municipio.values())
    esperar(
        len(con_candidatas) >= 2
        and validas - por_municipio[con_candidatas[0]] >= 3
        and len(candidatas) > validas,
        f"{len(candidatas)} candidatas de campo; {validas} con coordenadas validas, en "
        f"{len(con_candidatas)} municipios",
    )
    bases = {
        "nombre": f"Bases de campo de la prueba de humo del {corte}",
        "bases": [
            {
                "codigo": "BASE_CAMPO_A",
                "nombre": "Base de campo A (sintetica)",
                "lat": 18.5,
                "lon": -99.5,
                "activa": True,
                "municipios": con_candidatas[1:],
            },
            {
                "codigo": "BASE_CAMPO_B",
                "nombre": "Base de campo B (sintetica)",
                "lat": 18.6,
                "lon": -99.4,
                "activa": True,
                "municipios": con_candidatas[:1],
            },
        ],
    }
    estado, _, version_bases = enviar("/bases/versiones", bases)
    esperar(
        estado == 201 and (version_bases["bases"], version_bases["activas"]) == (2, 2),
        f"POST /bases/versiones: {estado}, BASE_CAMPO_A y BASE_CAMPO_B, activas",
    )
    estado, encabezados, abierta = enviar(
        "/territorial-v2",
        {
            "corte_id": del_dia["corte_id"],
            "geografia_run_id": geografia["geografia_run_id"],
            "bases_version_id": version_bases["bases_version_id"],
        },
    )
    esperar(estado == 201, f"POST /territorial-v2 con las bases de campo: {estado}")
    territorial = seguir(ubicacion_de(encabezados))
    esperar(
        territorial.get("estado") == "EXITOSA" and territorial["conteos"]["asignadas"] >= validas,
        f"territorial/v2 termino {territorial.get('estado')}: "
        f"{territorial.get('conteos', {}).get('asignadas')} asignadas",
    )

    ventanas = [{"inicio": "09:00", "fin": "14:00"}, {"inicio": "16:00", "fin": "19:00"}]
    configuracion = {
        "nombre": "Campo de la prueba de humo",
        "bases_version_id": version_bases["bases_version_id"],
        "zona_horaria": "America/Mexico_City",
        "calendarios": [
            {"codigo": "TODA_LA_SEMANA", "semana": {dia: ventanas for dia in SEMANA}},
            {
                "codigo": "CON_FESTIVO",
                "semana": {dia: ventanas for dia in SEMANA},
                "excepciones": [
                    {"fecha": fecha_operacion, "tipo": "CERRADO", "motivo": "Festivo sintetico"}
                ],
            },
        ],
        "gestores": [
            {
                "codigo": codigo,
                "base": base,
                "calendario": calendario,
                "capacidad_cuentas_dia": cupo,
                "activo": activo,
            }
            for codigo, base, calendario, cupo, activo in (
                ("GESTOR_HUMO_001", "BASE_CAMPO_A", "TODA_LA_SEMANA", 1, True),
                ("GESTOR_HUMO_002", "BASE_CAMPO_A", "TODA_LA_SEMANA", 1, True),
                ("GESTOR_HUMO_003", "BASE_CAMPO_B", "CON_FESTIVO", 5, True),
                ("GESTOR_HUMO_004", "BASE_CAMPO_B", "TODA_LA_SEMANA", 5, False),
            )
        ],
    }
    estado, encabezados, publicada = enviar("/campo/configuraciones", configuracion)
    esperar(
        estado == 201
        and (publicada["gestores"], publicada["gestores_activos"], publicada["calendarios"])
        == (4, 3, 2)
        and publicada["hash_verificado"] is True
        and ubicacion_de(encabezados)
        == f"/campo/configuraciones/{publicada['configuracion_campo_version_id']}",
        f"POST /campo/configuraciones: {estado}, 4 gestores (3 activos), 2 calendarios, hash "
        f"{publicada.get('hash_configuracion', '')[:12]}...",
    )
    estado, _, otra_vez = enviar(
        "/campo/configuraciones",
        {**configuracion, "nombre": "Otra etiqueta", "gestores": configuracion["gestores"][::-1]},
    )
    esperar(
        estado == 200
        and otra_vez["reutilizada"]
        and otra_vez["configuracion_campo_version_id"]
        == publicada["configuracion_campo_version_id"],
        "el mismo contenido con otro nombre y en otro orden: 200, la misma version reutilizada",
    )
    sin_cupo = [{**configuracion["gestores"][0], "capacidad_cuentas_dia": 0}]
    estado, _, error = enviar("/campo/configuraciones", {**configuracion, "gestores": sin_cupo})
    esperar(
        estado == 422 and error["codigo"] == "CONFIGURACION_INVALIDA",
        f"un gestor con cupo 0: {estado} {error.get('codigo')}",
    )

    pedida = {
        "corte_id": del_dia["corte_id"],
        "decision_run_id": decision["decision_run_id"],
        "territorial_run_id": territorial["territorial_run_id"],
        "configuracion_campo_version_id": publicada["configuracion_campo_version_id"],
        "fecha_operacion": fecha_operacion,
    }
    sin_fecha = {k: v for k, v in pedida.items() if k != "fecha_operacion"}
    estado, _, error = enviar("/campo-v2/planes", sin_fecha)
    esperar(
        estado == 422 and error["codigo"] == "ENTRADA_INVALIDA",
        f"sin fecha de operacion: {estado} {error.get('codigo')}; no se infiere",
    )
    antes = (datetime.fromisoformat(corte) - timedelta(days=1)).date().isoformat()
    estado, _, error = enviar("/campo-v2/planes", {**pedida, "fecha_operacion": antes})
    esperar(
        estado == 422 and error["codigo"] == "FECHA_ANTERIOR_AL_CORTE",
        f"un dia antes del corte: {estado} {error.get('codigo')}",
    )
    estado, encabezados, abierta = enviar("/campo-v2/planes", pedida)
    esperar(
        estado == 201
        and abierta["estado"] == "EN_PROCESO"
        and ubicacion_de(encabezados) == f"/campo-v2/planes/{abierta['campo_run_id']}",
        f"POST /campo-v2/planes: {estado} {abierta.get('estado')}, para el {fecha_operacion}",
    )
    plan = seguir(ubicacion_de(encabezados))
    conteos = plan.get("conteos", {})
    esperar(
        plan.get("estado") == "EXITOSA"
        and conteos["candidatas_campo"] == len(candidatas)
        and conteos["asignadas"]
        + conteos["revision_territorial"]
        + conteos["sin_gestor"]
        + conteos["sin_capacidad"]
        == len(candidatas)
        and conteos["asignadas"] == 2
        and conteos["rebalanceadas"] >= 1
        and min(conteos["revision_territorial"], conteos["sin_gestor"], conteos["sin_capacidad"])
        > 0,
        f"campo/v2 termino {plan.get('estado')}: "
        + ", ".join(f"{k} {v}" for k, v in conteos.items()),
    )
    esperar(
        plan["version_politica"] == "campo-public-v2"
        and len(plan["hash_politica"]) == len(plan["firma_inputs"]) == 64
        and plan["particion"]
        == {"srid": 6372, "tramo_m": 5000, "microtramo_m": 1000, "redondeo_m": "0.001"}
        and plan["capacidad"]
        == {"capacidad_total": 2, "capacidad_utilizada": 2, "utilizacion": "1.0000"}
        and "factibilidad vial" in plan["aviso"],
        f"campo-public-v2 (hash {plan['hash_politica'][:12]}...): EPSG:6372, tramos de 5 km y "
        "microtramos de 1 km; capacidad declarada 2 de 2, sin factibilidad vial",
    )
    _sin_ubicacion(plan, "GET /campo-v2/planes/{campo_run_id}")

    ruta = f"/campo-v2/planes/{plan['campo_run_id']}/asignaciones"

    def de_estado(estado_campo: str, cuantas: int) -> dict:
        estado, _, pagina = pedir("GET", f"{ruta}?estado={estado_campo}&por_pagina={cuantas}")
        esperar(
            estado == 200
            and bool(pagina["elementos"])
            and {a["estado"] for a in pagina["elementos"]} == {estado_campo},
            f"GET {{asignaciones}}?estado={estado_campo}: {pagina.get('total')}, por su indice",
        )
        _sin_ubicacion(pagina, f"las asignaciones {estado_campo}")
        return pagina

    asignadas = de_estado("ASIGNADA", 50)
    rebalanceada = next((a for a in asignadas["elementos"] if a["es_rebalanceada"]), None)
    esperar(
        {a["gestor"]["codigo"] for a in asignadas["elementos"]}
        == {"GESTOR_HUMO_001", "GESTOR_HUMO_002"}
        and {a["base"]["codigo"] for a in asignadas["elementos"]} == {"BASE_CAMPO_A"}
        and rebalanceada is not None
        and rebalanceada["motivos"][-1]["codigo"] == "SPILLOVER_POR_CAPACIDAD",
        "una cuenta a cada gestor de BASE_CAMPO_A, y una de ellas rebalanceada: a su propietario "
        "preferido ya no le cabia",
    )
    print(
        f"      {rebalanceada['cliente_unico']}: {rebalanceada['gestor']['codigo']}, en lugar de "
        f"{rebalanceada['motivos'][-1]['propietario_preferido']}"
    )
    sin_capacidad = de_estado("SIN_CAPACIDAD", 5)
    sin_gestor = de_estado("SIN_GESTOR_DISPONIBLE", 5)
    revision = de_estado("REVISION_TERRITORIAL", 5)
    esperar(
        sin_capacidad["total"] == conteos["sin_capacidad"]
        and all(
            a["gestor"] is None
            and a["base"]["codigo"] == "BASE_CAMPO_A"
            and a["motivos"][-1]["codigo"] == "CAPACIDAD_AGOTADA"
            for a in sin_capacidad["elementos"]
        )
        and sin_gestor["total"] == conteos["sin_gestor"]
        and all(
            a["base"]["codigo"] == "BASE_CAMPO_B"
            and a["motivos"][-1]["codigo"] == "SIN_GESTOR_DISPONIBLE"
            for a in sin_gestor["elementos"]
        )
        and revision["total"] == conteos["revision_territorial"]
        and all(a["base"] is None and a["zona_id"] is None for a in revision["elementos"]),
        "SIN_CAPACIDAD en BASE_CAMPO_A, SIN_GESTOR_DISPONIBLE en BASE_CAMPO_B y "
        "REVISION_TERRITORIAL sin base ni unidades, cada una con su motivo",
    )

    estado, _, cargas = pedir("GET", f"/campo-v2/planes/{plan['campo_run_id']}/cargas")
    por_gestor = {c["gestor"]["codigo"]: c for c in cargas.get("elementos", [])}
    esperar(
        estado == 200
        and [
            (c["estado_jornada"], c["capacidad"], c["asignadas"])
            for _, c in sorted(por_gestor.items())
        ]
        == [
            ("JORNADA_SEMANAL", 1, 1),
            ("JORNADA_SEMANAL", 1, 1),
            ("CERRADO_POR_EXCEPCION", 0, 0),
            ("INACTIVO", 0, 0),
        ]
        and por_gestor["GESTOR_HUMO_001"]["ventanas"][0]["inicio_en"]
        == f"{fecha_operacion}T15:00:00Z",
        "GET {cargas}: los dos de BASE_CAMPO_A llenos, uno cerrado por su festivo y uno inactivo; "
        "las 09:00 de la Ciudad de Mexico son las 15:00 UTC",
    )
    _sin_ubicacion(cargas, "las cargas")
    estado, _, microtramos = pedir(
        "GET",
        f"/campo-v2/planes/{plan['campo_run_id']}/unidades?nivel=MICROTRAMO&base=BASE_CAMPO_A"
        "&por_pagina=500",
    )
    mayor = max((m["candidatas"] for m in microtramos.get("elementos", [])), default=0)
    esperar(
        estado == 200
        and microtramos["total"] >= 1
        and mayor >= 2
        and all(m["propietario_preferido"] for m in microtramos["elementos"]),
        f"GET {{unidades}}?nivel=MICROTRAMO: {microtramos.get('total')} microtramos en "
        f"BASE_CAMPO_A, el mayor con {mayor} candidatas, cada uno con su propietario preferido",
    )
    _sin_ubicacion(microtramos, "las unidades")

    # La Cuenta 360 de una asignada, una sin gestor y una en revision: su ultimo plan, sin
    # coordenadas.
    for elegida in (
        rebalanceada,
        sin_gestor["elementos"][0],
        revision["elementos"][0],
    ):
        estado, _, resumen = pedir("GET", f"/cuentas/{elegida['cuenta_id']}")
        c = resumen.get("campo_resumen") or {}
        esperar(
            estado == 200
            and c.get("campo_run_id") == plan["campo_run_id"]
            and c.get("fecha_plan") == fecha_operacion
            and c.get("estado") == elegida["estado"]
            and c.get("gestor_codigo") == (elegida["gestor"] or {}).get("codigo"),
            f"GET /cuentas/{{cuenta_id}} de {elegida['cliente_unico']}: campo_resumen "
            f"{elegida['estado']} {c.get('gestor_codigo') or ''}".rstrip(),
        )
        _sin_ubicacion(resumen, "la Cuenta 360")
    estado, _, de_la_cuenta = pedir("GET", f"/cuentas/{rebalanceada['cuenta_id']}/campo-v2")
    esperar(
        estado == 200
        and de_la_cuenta["total"] == 1
        and de_la_cuenta["elementos"][0]["campo_run_id"] == plan["campo_run_id"],
        "GET /cuentas/{cuenta_id}/campo-v2: el plan de la cuenta",
    )
    _sin_ubicacion(de_la_cuenta, "los planes de una cuenta")
    estado, _, trabajo = pedir("GET", f"/trabajos/{plan['trabajo_id']}")
    esperar(
        estado == 200 and (trabajo["tipo"], trabajo["estado"]) == ("CAMPO_V2", "COMPLETADO"),
        f"su trabajo CAMPO_V2: {trabajo.get('estado')} ({trabajo.get('intentos')})",
    )
    estado, _, error = enviar("/campo-v2/planes", pedida)
    esperar(
        estado == 409 and error["codigo"] == "CAMPO_V2_YA_GENERADO",
        f"las mismas entradas otra vez: {estado} {error.get('codigo')}",
    )
    print(f"configuracion_campo_version_id {publicada['configuracion_campo_version_id']}")
    print(f"campo_run_id {plan['campo_run_id']}")


# --- ruteo/v2: la red vial ------------------------------------------------------------------------

MUNICIPIO_DE_LA_RED = "21114"
"""El municipio sintetico donde generar-red-sintetica dibujo la red y sus puntos."""
COORDENADA = re.compile(r"(?<![\d:.])-?\d{1,3}\.\d{5,}")
"""Un numero con cinco o mas decimales: una coordenada, en cualquier llave. No los segundos de un
instante (12:00:31.665064Z), que van despues de dos puntos."""
PAUSA = ("09:45:00", "11:00:00")
"""La pausa del calendario PARTIDO: en ella nadie maneja ni atiende; se espera donde se esta."""


def _sin_geometria(cuerpo, donde: str, *grados: str) -> None:
    """Ni una coordenada (de una cuenta, de una base o de un snap) ni una geometria salen por la
    API, en ninguna llave ni en ningun valor."""
    texto = json.dumps(cuerpo)
    esperar(
        not any(llave in texto for llave in LLAVES_DE_UBICACION)
        and not any(
            llave in texto for llave in ('"geometry"', '"polyline"', '"coordinates"', '"location"')
        )
        and not COORDENADA.search(texto)
        and not any(g in texto for g in grados),
        f"{donde}: sin coordenadas ni geometrias",
    )


def _hora(instante: str) -> str:
    """La hora local de un instante con su desfase, como HH:MM:SS."""
    return datetime.fromisoformat(instante).strftime("%H:%M:%S")


def _fuera_de_la_pausa(desde: str, hasta: str) -> bool:
    """Si un tramo o un servicio, de `desde` a `hasta`, no toca la pausa del turno partido."""
    return _hora(hasta) <= PAUSA[0] or _hora(desde) >= PAUSA[1]


def la_ruta_vial(
    corte: str, del_dia: dict, decision: dict, geografia: dict, red_vial: str, base: str
) -> dict:
    """ruteo/v2 de punta a punta sobre la red vial sintetica, servida por OSRM: la version de la
    red declarada, bases con una base en un cruce de la red (la que imprimio
    generar-red-sintetica), territorial/v2 y una configuracion de campo con tres gestores (uno de
    dia completo, uno con turno partido y uno de 40 minutos), el plan del dia siguiente al corte y
    su ruteo. Cada cosa sale: rutas COMPLETA y PARCIAL, paradas PROGRAMADA, SIN_TIEMPO y
    NO_ENRUTABLE (por un snap de mas de 500 m y por un tramo sin camino), la espera en la pausa del
    turno partido sin manejar en ella, el regreso a la base, la Cuenta 360 y su trabajo RUTEO_V2;
    sin una sola coordenada."""
    print(f"ruteo/v2 sobre la red vial {red_vial}")
    fecha_operacion = (datetime.fromisoformat(corte) + timedelta(days=1)).date().isoformat()
    lon, lat = (g.strip() for g in base.split(","))

    estado, _, red = pedir("GET", f"/ruteo-v2/redes/{red_vial}")
    esperar(
        estado == 200
        and red["estado"] == "PUBLICADA"
        and (red["motor_ruteo"], red["version_motor_ruteo"], red["algoritmo_motor"])
        == ("OSRM", "v26.9.0", "MLD")
        and red["perfil"] == "car"
        and red["cobertura"].get("aristas", 0) > 0
        and len(red["comandos"]) == 3
        and len(red["artefactos"]) >= 20,
        f"GET /ruteo-v2/redes/{{id}}: {red.get('estado')}, OSRM {red.get('version_motor_ruteo')} "
        f"{red.get('algoritmo_motor')}, perfil {red.get('perfil')}, huella "
        f"{red.get('huella', '')[:12]}..., {len(red.get('artefactos', []))} archivos con su "
        "SHA-256",
    )
    _sin_geometria(red, "la red vial", lon, lat)

    # Las candidatas de campo de la decision/v2 con coordenadas validas en el municipio de la red.
    candidatas = {
        r["cuenta_id"]
        for r in _todas(
            f"/decisiones-v2/{decision['decision_run_id']}/resultados?canal_recomendado=CAMPO"
        )
    }
    en_la_red = {
        r["cuenta_id"]
        for r in _todas(
            f"/geografia/ejecuciones/{geografia['geografia_run_id']}/resultados"
            "?estado_geografia=VALIDA"
        )
        if r["municipio"]["cvegeo"] == MUNICIPIO_DE_LA_RED
    } & candidatas
    esperar(
        len(en_la_red) >= 10,
        f"{len(en_la_red)} candidatas de campo con coordenadas validas en {MUNICIPIO_DE_LA_RED}",
    )
    estado, _, version_bases = enviar(
        "/bases/versiones",
        {
            "nombre": f"Bases de ruteo de la prueba de humo del {corte}",
            "bases": [
                {
                    "codigo": "BASE_RUTEO",
                    "nombre": "Base de ruteo (sintetica, en un cruce de la red)",
                    "lat": float(lat),
                    "lon": float(lon),
                    "activa": True,
                    "municipios": [MUNICIPIO_DE_LA_RED],
                }
            ],
        },
    )
    esperar(estado == 201, f"POST /bases/versiones: {estado}, BASE_RUTEO en la red")
    estado, encabezados, _ = enviar(
        "/territorial-v2",
        {
            "corte_id": del_dia["corte_id"],
            "geografia_run_id": geografia["geografia_run_id"],
            "bases_version_id": version_bases["bases_version_id"],
        },
    )
    territorial = seguir(ubicacion_de(encabezados))
    esperar(
        territorial.get("estado") == "EXITOSA",
        f"territorial/v2 con BASE_RUTEO: {territorial.get('estado')}, "
        f"{territorial.get('conteos', {}).get('asignadas')} asignadas",
    )
    todo_el_dia = [{"inicio": "09:00", "fin": "19:00"}]
    partido = [{"inicio": "09:00", "fin": PAUSA[0][:5]}, {"inicio": PAUSA[1][:5], "fin": "14:00"}]
    corto = [{"inicio": "09:00", "fin": "09:40"}]
    estado, _, configuracion = enviar(
        "/campo/configuraciones",
        {
            "nombre": "Campo de ruteo de la prueba de humo",
            "bases_version_id": version_bases["bases_version_id"],
            "zona_horaria": "America/Mexico_City",
            "calendarios": [
                {"codigo": codigo, "semana": {dia: ventanas for dia in SEMANA}}
                for codigo, ventanas in (
                    ("TODO_EL_DIA", todo_el_dia),
                    ("PARTIDO", partido),
                    ("CORTO", corto),
                )
            ],
            "gestores": [
                {
                    "codigo": codigo,
                    "base": "BASE_RUTEO",
                    "calendario": calendario,
                    "capacidad_cuentas_dia": cupo,
                    "activo": True,
                }
                for codigo, calendario, cupo in (
                    ("GESTOR_RUTA_001", "TODO_EL_DIA", 3),
                    ("GESTOR_RUTA_002", "PARTIDO", 6),
                    ("GESTOR_RUTA_003", "CORTO", 40),
                )
            ],
        },
    )
    esperar(estado == 201, f"POST /campo/configuraciones: {estado}, tres gestores en BASE_RUTEO")
    estado, encabezados, _ = enviar(
        "/campo-v2/planes",
        {
            "corte_id": del_dia["corte_id"],
            "decision_run_id": decision["decision_run_id"],
            "territorial_run_id": territorial["territorial_run_id"],
            "configuracion_campo_version_id": configuracion["configuracion_campo_version_id"],
            "fecha_operacion": fecha_operacion,
        },
    )
    plan = seguir(ubicacion_de(encabezados))
    asignadas = plan.get("conteos", {}).get("asignadas", 0)
    esperar(
        plan.get("estado") == "EXITOSA" and asignadas >= 10,
        f"campo/v2 del {fecha_operacion} en BASE_RUTEO: {plan.get('estado')}, {asignadas} "
        "asignadas",
    )

    # El ruteo: la API no rutea; el worker, con su OSRM, si.
    pedida = {"campo_run_id": plan["campo_run_id"], "red_vial_version_id": red_vial}
    for cambios, codigo_http, codigo in (
        ({"red_vial_version_id": str(uuid.uuid4())}, 404, "RED_VIAL_NO_ENCONTRADA"),
        ({"campo_run_id": str(uuid.uuid4())}, 404, "CAMPO_V2_NO_ENCONTRADO"),
        ({"version_politica": "ruteo-public-v9"}, 422, "POLITICA_NO_SOPORTADA"),
    ):
        estado, _, error = enviar("/ruteo-v2/ejecuciones", {**pedida, **cambios})
        esperar(
            estado == codigo_http and error.get("codigo") == codigo,
            f"POST /ruteo-v2/ejecuciones con {next(iter(cambios))} que no aplica: {estado} "
            f"{error.get('codigo')}",
        )
    estado, encabezados, abierta = enviar("/ruteo-v2/ejecuciones", pedida)
    esperar(
        estado == 201
        and abierta["estado"] == "EN_PROCESO"
        and ubicacion_de(encabezados) == f"/ruteo-v2/ejecuciones/{abierta['ruteo_run_id']}",
        f"POST /ruteo-v2/ejecuciones: {estado} {abierta.get('estado')}",
    )
    ejecucion = seguir(ubicacion_de(encabezados))
    c = ejecucion.get("conteos", {})
    motivos = (ejecucion.get("distribucion") or {}).get("motivos", {})
    esperar(
        ejecucion.get("estado") == "EXITOSA"
        and ejecucion["resultado"] == "RUTAS_PUBLICADAS"
        and c["paradas_objetivo"] == asignadas
        and c["programadas"] + c["sin_tiempo"] + c["no_enrutables"] + c["no_enrutables_base"]
        == asignadas
        and c["gestores_objetivo"] == 3,
        f"ruteo/v2 termino {ejecucion.get('estado')}: "
        + ", ".join(f"{k} {v}" for k, v in c.items()),
    )
    esperar(
        c["rutas_completas"] >= 1
        and c["rutas_parciales"] >= 1
        and c["programadas"] >= 3
        and c["sin_tiempo"] >= 1
        and motivos.get("SNAP_EXCESIVO", 0) >= 1
        and motivos.get("RED_DESCONECTADA", 0) >= 1,
        "rutas COMPLETA y PARCIAL; paradas PROGRAMADA, SIN_TIEMPO y NO_ENRUTABLE, por un snap de "
        "mas de 500 m y por un tramo sin camino: "
        + ", ".join(f"{m} {n}" for m, n in sorted(motivos.items())),
    )
    politica = ejecucion["politica"]
    esperar(
        (ejecucion["version_motor"], ejecucion["version_politica"])
        == ("ruteo/v2", "ruteo-public-v2")
        and politica["algoritmo"] == "PRIORITY_NEAREST_NEIGHBOR_2OPT/v1"
        and (politica["max_snap_distancia_m"], politica["duracion_servicio_s"]) == (500, 900)
        and ejecucion["red"]["huella"] == red["huella"]
        and "sin trafico" in ejecucion["aviso"]
        and "no una ruta optima" in ejecucion["aviso"],
        f"ruteo-public-v2 (hash {ejecucion['hash_politica'][:12]}...): {politica['algoritmo']}, "
        "snap de hasta 500 m, 900 s de servicio, sobre la red declarada; heuristica, sin trafico",
    )
    _sin_geometria(ejecucion, "GET /ruteo-v2/ejecuciones/{ruteo_run_id}", lon, lat)

    estado, _, rutas = pedir(
        "GET", f"/ruteo-v2/ejecuciones/{ejecucion['ruteo_run_id']}/rutas?por_pagina=10"
    )
    por_gestor = {r["gestor"]["codigo"]: r for r in rutas.get("elementos", [])}
    esperar(
        estado == 200
        and sorted(por_gestor) == ["GESTOR_RUTA_001", "GESTOR_RUTA_002", "GESTOR_RUTA_003"],
        f"GET {{rutas}}: {estado}, una ruta por gestor, en el orden de su codigo",
    )
    for codigo, r in sorted(por_gestor.items()):
        regreso = r["regreso"] or {}
        esperar(
            r["duracion_total_s"]
            == r["duracion_traslado_s"] + r["duracion_servicio_s"] + r["duracion_espera_s"]
            and r["salida_base"] is not None
            and r["jornada_inicio"] <= r["salida_base"]
            and regreso.get("llegada", "") <= r["jornada_fin"]
            and r["hash_matriz"] is not None,
            f"{codigo}: {r['estado_ruta']}, {r['conteos']['programadas']} de "
            f"{r['conteos']['paradas_campo']} paradas, sale {_hora(r['salida_base'])} y regresa "
            f"a la base {_hora(regreso['llegada'])} ({r['distancia_total_m']:,} m por calle)",
        )
    _sin_geometria(rutas, "las rutas", lon, lat)

    # El turno partido: en la pausa no se maneja ni se atiende; se espera donde se esta.
    partida = por_gestor["GESTOR_RUTA_002"]
    estado, _, paradas = pedir("GET", f"/ruteo-v2/rutas/{partida['ruta_id']}/paradas?por_pagina=50")
    programadas = [p for p in paradas.get("elementos", []) if p["estado"] == "PROGRAMADA"]
    tarde = [p for p in programadas if _hora(p["inicio_servicio"]) >= PAUSA[1]]
    regreso = partida["regreso"]
    esperar(
        estado == 200
        and [p["orden_visita"] for p in programadas] == list(range(1, len(programadas) + 1))
        and all(
            _fuera_de_la_pausa(p["salida_anterior"], p["llegada_estimada"])
            and _fuera_de_la_pausa(p["inicio_servicio"], p["fin_servicio"])
            for p in programadas
        )
        and _fuera_de_la_pausa(regreso["salida"], regreso["llegada"])
        and bool(tarde)
        and tarde[0]["espera_s"] > 0
        and partida["duracion_espera_s"] > 0,
        f"GESTOR_RUTA_002, turno partido: {len(programadas)} paradas en orden; nadie maneja ni "
        f"atiende entre las {PAUSA[0][:5]} y las {PAUSA[1][:5]}; la primera de la tarde empieza a "
        f"las {tarde and _hora(tarde[0]['inicio_servicio'])}, tras "
        f"{tarde and tarde[0]['espera_s']} s de espera donde estaba",
    )
    _sin_geometria(paradas, "las paradas de una ruta", lon, lat)

    # Las paradas por su estado, cada una con su motivo.
    ruta_paradas = f"/ruteo-v2/ejecuciones/{ejecucion['ruteo_run_id']}/paradas"

    def de_estado(estado_parada: str) -> list[dict]:
        elementos = _todas(f"{ruta_paradas}?estado={estado_parada}")
        esperar(
            {p["estado"] for p in elementos} <= {estado_parada},
            f"GET {{paradas}}?estado={estado_parada}: {len(elementos)}, por su indice",
        )
        return elementos

    sin_tiempo = de_estado("SIN_TIEMPO")
    no_enrutables = de_estado("NO_ENRUTABLE")
    programadas_todas = de_estado("PROGRAMADA")
    esperar(
        len(sin_tiempo) == c["sin_tiempo"]
        and len(no_enrutables) == c["no_enrutables"]
        and len(programadas_todas) == c["programadas"]
        and all(p["motivo"] and p["llegada_estimada"] is None for p in sin_tiempo + no_enrutables)
        and all(p["llegada_estimada"].endswith("-06:00") for p in programadas_todas),
        "cada parada sin programar con su motivo y sin hora; cada programada con su ETA "
        "planificada en la hora de la Ciudad de Mexico",
    )
    excesiva = next(p for p in no_enrutables if p["motivo"] == "SNAP_EXCESIVO")
    esperar(
        float(excesiva["snap_distancia_m"]) > 500,
        f"{excesiva['cliente_unico']}: NO_ENRUTABLE, a {excesiva['snap_distancia_m']} m de la "
        "calle mas cercana",
    )

    # La Cuenta 360 de una programada, de una sin tiempo y de una no enrutable; y su trabajo.
    for elegida in (programadas_todas[0], sin_tiempo[0], excesiva):
        estado, _, resumen = pedir("GET", f"/cuentas/{elegida['cuenta_id']}")
        r = resumen.get("ruteo_resumen") or {}
        esperar(
            estado == 200
            and r.get("ruteo_run_id") == ejecucion["ruteo_run_id"]
            and r.get("estado") == elegida["estado"]
            and r.get("motivo") == elegida["motivo"]
            and r.get("llegada_estimada") == elegida["llegada_estimada"],
            f"GET /cuentas/{{cuenta_id}} de {elegida['cliente_unico']}: ruteo_resumen "
            f"{elegida['estado']} {elegida['motivo'] or _hora(elegida['llegada_estimada'])}",
        )
        _sin_geometria(resumen, "la Cuenta 360", lon, lat)
    estado, _, de_la_cuenta = pedir("GET", f"/cuentas/{programadas_todas[0]['cuenta_id']}/ruteo-v2")
    esperar(
        estado == 200
        and de_la_cuenta["total"] == 1
        and de_la_cuenta["elementos"][0]["ruteo_run_id"] == ejecucion["ruteo_run_id"],
        "GET /cuentas/{cuenta_id}/ruteo-v2: la parada de la cuenta",
    )
    estado, _, trabajo = pedir("GET", f"/trabajos/{ejecucion['trabajo_id']}")
    esperar(
        estado == 200 and (trabajo["tipo"], trabajo["estado"]) == ("RUTEO_V2", "COMPLETADO"),
        f"su trabajo RUTEO_V2: {trabajo.get('estado')} ({trabajo.get('intentos')})",
    )
    estado, _, error = enviar("/ruteo-v2/ejecuciones", pedida)
    esperar(
        estado == 409 and error["codigo"] == "RUTEO_V2_YA_GENERADO",
        f"las mismas entradas otra vez: {estado} {error.get('codigo')}",
    )
    print(f"ruteo_plan_campo {plan['campo_run_id']}")
    print(f"ruteo_run_id {ejecucion['ruteo_run_id']}")
    return {"territorial": territorial, "plan": plan, "ruteo": ejecucion}


# --- analytics/v1 ---------------------------------------------------------------------------------

PROHIBIDAS_EN_ANALYTICS = (
    '"cuenta_id"',
    '"cliente_unico"',
    '"actor_ref"',
    '"latitud"',
    '"longitud"',
    '"microtramo_id"',
    '"tramo_id"',
    '"polilinea"',
)


def _sin_cuentas(cuerpo, donde: str, cuentas: list[str]) -> None:
    """Lo que publica analytics es agregado: ni un identificador de una cuenta, ni un actor_ref,
    ni una coordenada."""
    texto = json.dumps(cuerpo)
    esperar(
        not any(llave in texto for llave in PROHIBIDAS_EN_ANALYTICS)
        and not any(c in texto for c in cuentas)
        and "CU0" not in texto
        and "AGT-" not in texto
        and not re.search(r"-9\d\.\d{4}", texto),
        f"{donde}: sin datos de una cuenta",
    )


def _agregados(ejecucion: str, **filtros) -> dict:
    consulta = "&".join(f"{llave}={valor}" for llave, valor in filtros.items())
    estado, _, pagina = pedir("GET", f"/analytics/ejecuciones/{ejecucion}/agregados?{consulta}")
    esperar(estado == 200, f"GET .../agregados?{consulta}: {estado}, {pagina.get('total')} filas")
    return pagina


def la_analitica(
    corte: str, del_dia: dict, decision: dict, geografia: dict, operacion: dict
) -> None:
    """analytics/v1 de punta a punta sobre la cohorte del corte oficial, del corte al dia del plan
    de ruteo, por dia, con lo que se sabe ahora (OPERATIVO): el catalogo con su hash; dos gestiones
    del ultimo dia del horizonte (una con promesa, otra sin contacto) y la evaluacion de las
    promesas a ese dia; el manifiesto declarado (la interpretacion de septiembre que leyo su
    atribucion, esa atribucion, la evaluacion, decision/v2, la geografia, la territorial/v2, el plan
    de campo y su ruteo), la ejecucion por su trabajo ANALYTICS, y lo publicado: el TOTAL, un
    desglose visible, una celda SUPRIMIDO sin ningun valor, la recuperacion neta (el 30, con
    cobertura; el acumulado, con un dia sin pagos conocidos, NO_DISPONIBLE y no cero), la
    contactabilidad, las promesas, la decision, el campo y el ruteo, los dos embudos y sus
    controles, todos OK; el 409 de las mismas entradas, y sin un solo dato de una cuenta en ninguna
    respuesta."""
    plan, ruteo = operacion["plan"], operacion["ruteo"]
    as_of = plan["fecha_operacion"]
    print(f"analytics/v1 de la cohorte del {corte} al {as_of}")

    estado, _, catalogo = pedir("GET", "/analytics/catalogo")
    (politica,) = catalogo.get("politicas", [None])
    esperar(
        estado == 200
        and catalogo["version"] == "analytics-metrics/public-v1"
        and len(catalogo["hash"]) == 64
        and len(catalogo["metricas"]) >= 87
        and all(m["descripcion"] and m["fuentes_requeridas"] for m in catalogo["metricas"])
        and (politica["version"], politica["min_group_size"]) == ("analytics-public-v1", 10),
        f"GET /analytics/catalogo: {catalogo.get('version')} ({catalogo.get('hash', '')[:12]}...), "
        f"{len(catalogo.get('metricas', []))} metricas, cada una con su formula y sus entradas",
    )

    # Dos gestiones del ultimo dia, de dos cuentas de la cohorte (las primeras que decidio su
    # decision/v2): una con contacto que promete, otra sin contacto. Las del lifecycle de la prueba
    # ocurrieron antes del corte, fuera del horizonte.
    estado, _, decididas = pedir(
        "GET", f"/decisiones-v2/{decision['decision_run_id']}/resultados?por_pagina=2"
    )
    cuentas = [r["cuenta_id"] for r in decididas.get("elementos", [])]
    esperar(estado == 200 and len(cuentas) == 2, "dos cuentas de la cohorte para gestionar")
    for cuenta_id, campos in zip(
        cuentas,
        (
            {"nivel_contacto": "CONTACTO_TITULAR", "resultado": "PROMESA"},
            {"nivel_contacto": "SIN_CONTACTO", "resultado": "SIN_RESPUESTA"},
        ),
        strict=True,
    ):
        estado, _, gestion = enviar(
            f"/cuentas/{cuenta_id}/gestiones",
            {
                "ocurrido_en": f"{as_of}T10:00:00-06:00",
                "canal": "TELEFONICA",
                "medio": "LLAMADA",
                "actor_ref": "AGT-0101",
                **campos,
            },
            llave=f"humo-{uuid.uuid4().hex}",
        )
        esperar(estado == 201, f"una gestion el {as_of}: {campos['resultado']}")
        if campos["resultado"] == "PROMESA":
            limite = (datetime.fromisoformat(as_of) + timedelta(days=4)).date().isoformat()
            estado, _, _ = enviar(
                f"/gestiones/{gestion['gestion_id']}/promesas",
                {"monto_prometido": "500.00", "fecha_limite": limite},
                llave=f"humo-{uuid.uuid4().hex}",
            )
            esperar(estado == 201, f"su promesa de 500.00 al {limite}")
    estado, encabezados, _ = enviar("/evaluaciones-promesas", {"as_of": as_of})
    evaluacion = seguir(ubicacion_de(encabezados))
    esperar(
        estado == 201 and evaluacion.get("estado") == "EXITOSA",
        f"la evaluacion de las promesas al {as_of}: {evaluacion.get('estado')}",
    )

    # La atribucion de septiembre que se declara, con la interpretacion que leyo.
    periodo = corte[:7]
    estado, _, atribuciones = pedir("GET", f"/atribuciones?periodo={periodo}&estado=EXITOSA")
    atribucion = next((a for a in atribuciones.get("elementos", []) if a["vigente"]), None)
    esperar(
        estado == 200 and atribucion is not None,
        f"la atribucion vigente de {periodo}, sobre la interpretacion "
        f"{atribucion and atribucion['motor_pagos_run_id']}",
    )
    pedida = {
        "corte_id": del_dia["corte_id"],
        "as_of": as_of,
        "modo": "OPERATIVO",
        "zona_horaria": "America/Mexico_City",
        "granularidad": "DIA",
        "motor_pagos_run_ids": [atribucion["motor_pagos_run_id"]],
        "atribucion_run_ids": [atribucion["atribucion_run_id"]],
        "evaluacion_run_id": evaluacion["evaluacion_run_id"],
        "decision_run_id": decision["decision_run_id"],
        "geografia_run_id": geografia["geografia_run_id"],
        "territorial_run_id": operacion["territorial"]["territorial_run_id"],
        "campo_run_id": plan["campo_run_id"],
        "ruteo_run_id": ruteo["ruteo_run_id"],
    }
    for cambios, codigo_http, codigo in (
        ({"modo": "BACKTEST"}, 422, "KNOWN_AT_REQUERIDO"),
        ({"evaluacion_run_id": str(uuid.uuid4())}, 404, "ENTRADA_NO_ENCONTRADA"),
        ({"territorial_run_id": None}, 422, "ENCADENAMIENTO_INCORRECTO"),
        ({"ultima": True}, 422, "ENTRADA_INVALIDA"),
    ):
        estado, _, error = enviar("/analytics/ejecuciones", {**pedida, **cambios})
        esperar(
            (estado, error.get("codigo")) == (codigo_http, codigo),
            f"POST /analytics/ejecuciones con {list(cambios)}: {estado} {error.get('codigo')}",
        )
    estado, encabezados, abierta = enviar("/analytics/ejecuciones", pedida)
    esperar(
        estado == 201
        and abierta["estado"] == "EN_PROCESO"
        and ubicacion_de(encabezados) == f"/analytics/ejecuciones/{abierta['analytics_run_id']}"
        and len(abierta["entradas"]) == 9,
        f"POST /analytics/ejecuciones: {estado} EN_PROCESO, con sus 9 entradas y known_at "
        f"{abierta.get('known_at')}",
    )
    e = seguir(ubicacion_de(encabezados))
    run = e["analytics_run_id"]
    esperar(
        (e.get("estado"), e.get("resultado")) == ("EXITOSA", "ANALYTICS_PUBLICADA")
        and e["agregados_publicados"] > 0
        and e["controles_fallidos"] == 0
        and len(e["hash_agregados"]) == 64
        and e["cuentas_cohorte"] == del_dia["cuentas"],
        f"la ejecucion termino {e.get('estado')}: {e.get('agregados_publicados')} agregados, "
        f"{e.get('controles_totales')} controles, "
        f"{(e.get('disponibilidad') or {}).get('celdas_suprimidas')} celdas suprimidas",
    )
    _sin_cuentas(e, "la ejecucion", cuentas)

    def total(metrica: str, vista: str = "ACUMULADA", periodo: str | None = None) -> dict:
        filtros = {"metrica": metrica, "dimension_set": "TOTAL", "vista": vista}
        if periodo is not None:
            filtros |= {"periodo_desde": periodo, "periodo_hasta": periodo}
        (fila,) = _agregados(run, **filtros)["elementos"]
        return fila

    cuentas_ = total("cartera.cuentas")
    esperar(
        (cuentas_["estado_valor"], cuentas_["valor"]) == ("DISPONIBLE", str(del_dia["cuentas"])),
        f"TOTAL: {cuentas_['valor']} cuentas en la cohorte, visible",
    )
    visibles = _agregados(
        run, metrica="cartera.cuentas", dimension_set="ATRASO_BUCKET", estado_valor="DISPONIBLE"
    )
    esperar(
        visibles["total"] >= 1
        and all(int(f["poblacion_cuentas"]) >= 10 for f in visibles["elementos"]),
        f"un desglose visible: {visibles['total']} tramos de atraso con 10 cuentas o mas",
    )
    suprimidas = _agregados(
        run, metrica="cartera.cuentas", estado_valor="SUPRIMIDO", por_pagina=500
    )
    esperar(
        suprimidas["total"] >= 1
        and all(
            (f["valor"], f["numerador"], f["denominador"], f["poblacion_cuentas"])
            == (None, None, None, None)
            for f in suprimidas["elementos"]
        ),
        f"{suprimidas['total']} celdas SUPRIMIDO, sin valor ni poblacion",
    )
    del_30 = total("recuperacion.neta", "PERIODO", corte)
    acumulada = total("recuperacion.neta")
    esperar(
        del_30["estado_valor"] == "DISPONIBLE"
        and del_30["valor"] is not None
        and acumulada["estado_valor"] == "NO_DISPONIBLE"
        and acumulada["valor"] is None
        and acumulada["motivos"][0]["codigo"] == "PAGOS_SIN_COBERTURA",
        f"recuperacion neta: el {corte}, {del_30['valor']}; el horizonte, NO_DISPONIBLE "
        f"({acumulada['motivos'][0]['codigo']}: {acumulada['motivos'][0].get('dias')} dia sin "
        "pagos conocidos), no cero",
    )
    contacto = total("gestion.contactadas_sobre_gestionadas")
    esperar(
        (contacto["estado_valor"], contacto["numerador"], contacto["denominador"])
        == ("DISPONIBLE", "1", "2")
        and contacto["valor"] == "0.5000000000",
        f"contactabilidad: {contacto['numerador']} de {contacto['denominador']} gestionadas",
    )
    creadas, pendientes = total("promesa.creadas"), total("promesa.pendientes")
    tasa = total("promesa.tasa_cumplimiento_total")
    esperar(
        (creadas["valor"], pendientes["valor"]) == ("1", "1")
        and (tasa["valor"], tasa["denominador"]) == (None, "0")
        and tasa["motivos"] == [{"codigo": "DENOMINADOR_CERO"}],
        "promesas: 1 creada, PENDIENTE en la evaluacion declarada; la tasa sin resueltas es NULL "
        "con denominador 0, no 0 %",
    )
    decididas = total("decision.cuentas")
    esperar(
        decididas["valor"] == str(del_dia["cuentas"]),
        f"decision: {decididas['valor']} cuentas decididas",
    )
    c = plan["conteos"]
    candidatas, asignadas = total("campo.candidatas"), total("campo.asignadas")
    programadas = total("ruteo.programadas")
    esperar(
        (candidatas["valor"], asignadas["valor"], programadas["valor"])
        == (str(c["candidatas_campo"]), str(c["asignadas"]), str(ruteo["conteos"]["programadas"])),
        f"campo y ruteo: {candidatas['valor']} candidatas, {asignadas['valor']} asignadas, "
        f"{programadas['valor']} paradas programadas (planificadas, no visitas)",
    )

    estado, _, embudos = pedir("GET", f"/analytics/ejecuciones/{run}/embudos")
    por_nombre = {x["embudo"]: x["etapas"] for x in embudos.get("embudos", [])}
    esperar(
        estado == 200
        and [x["cuentas"] for x in por_nombre["CONTACTO"]] == [del_dia["cuentas"], 2, 1, 1]
        and [x["cuentas"] for x in por_nombre["CAMPO"]]
        == [c["candidatas_campo"], c["asignadas"], ruteo["conteos"]["programadas"]],
        "embudos: CONTACTO "
        + " > ".join(f"{x['etapa']} {x['cuentas']}" for x in por_nombre.get("CONTACTO", []))
        + "; CAMPO "
        + " > ".join(f"{x['etapa']} {x['cuentas']}" for x in por_nombre.get("CAMPO", [])),
    )
    estado, _, resumen = pedir("GET", f"/analytics/ejecuciones/{run}/resumen")
    estado_c, _, controles = pedir("GET", f"/analytics/ejecuciones/{run}/controles")
    esperar(
        (estado, estado_c) == (200, 200)
        and len(controles["controles"]) == controles["controles_totales"] == e["controles_totales"]
        and {x["estado"] for x in controles["controles"]} == {"OK"},
        f"GET .../controles: los {controles.get('controles_totales')} controles, todos OK",
    )
    for cuerpo, donde in (
        (catalogo, "el catalogo"),
        (embudos, "los embudos"),
        (resumen, "el resumen"),
        (controles, "los controles"),
        (_agregados(run, por_pagina=500), "una pagina de agregados"),
        (suprimidas, "las celdas suprimidas"),
    ):
        _sin_cuentas(cuerpo, donde, cuentas)

    estado, _, trabajo = pedir("GET", f"/trabajos/{e['trabajo_id']}")
    esperar(
        estado == 200 and (trabajo["tipo"], trabajo["estado"]) == ("ANALYTICS", "COMPLETADO"),
        f"su trabajo ANALYTICS: {trabajo.get('estado')} ({trabajo.get('intentos')})",
    )
    # La misma frontera con las mismas entradas es la misma ejecucion: el modo no entra en la firma.
    estado, _, error = enviar(
        "/analytics/ejecuciones", {**pedida, "modo": "BACKTEST", "known_at": e["known_at"]}
    )
    esperar(
        (estado, error.get("codigo")) == (409, "ANALYTICS_YA_GENERADA"),
        f"las mismas entradas otra vez: {estado} {error.get('codigo')}",
    )
    estado, _, _ = pedir("GET", f"/cuentas/{cuentas[0]}/analytics")
    esperar(
        estado == 404,
        f"GET /cuentas/{{cuenta_id}}/analytics: {estado}, no hay analytics por cuenta",
    )
    print(f"analytics_run_id {run}")


# --- predictive/model-v1 --------------------------------------------------------------------------

ESPERA_PREDICTIVA = 900  # segundos: un entrenamiento construye su dataset y ajusta cuatro GLM
RUTAS_PREDICTIVAS = (
    "/predictivo/features",
    "/predictivo/targets",
    "/predictivo/entrenamientos",
    "/predictivo/entrenamientos/{training_run_id}",
    "/predictivo/modelos",
    "/predictivo/modelos/{modelo_version_id}",
    "/predictivo/scorings",
    "/predictivo/scorings/{scoring_run_id}",
    "/predictivo/scorings/{scoring_run_id}/predicciones",
    "/predictivo/scorings/{scoring_run_id}/drift",
    "/predictivo/scorings/{scoring_run_id}/cuentas/{cuenta_id}/explicacion",
    "/cuentas/{cuenta_id}/predictivo",
)
TARGETS_PREDICTIVOS = [
    "RECUPERACION_POSITIVA_30D",
    "CONTACTO_14D",
    "PROMESA_30D",
    "RECUPERACION_POSITIVA_MONTO_30D",
]
# Lo que ninguna feature puede ser, dicho aqui y no solo en el servicio: un identificador, un dato
# personal, una coordenada, una geografia fina o una salida de decision, campo o ruteo.
SENSIBLES = re.compile(
    r"cuenta|cliente|actor|source|nombre|direccion|domicilio|telefono|correo|aval|curp|rfc|sexo"
    r"|genero|latitud|longitud|municipio|estrategia|gestor|despacho|cartera|prioridad|ruta"
)
CAMPOS_DE_UNA_PREDICCION = {
    "cuenta_id",
    "producto",
    "cve_entidad",
    "p_recuperacion_positiva_30d",
    "p_contacto_14d",
    "p_promesa_30d",
    "riesgo_sin_recuperacion_30d",
    "recuperacion_positiva_esperada_30d",
    "estado",
    "motivos",
    "feature_hash",
}
PROBABILIDADES = ("p_recuperacion_positiva_30d", "p_contacto_14d", "p_promesa_30d")
UN_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _canonico(valor) -> bytes:
    """El JSON canonico de predictive/artifact-v1: llaves en orden, sin espacios, UTF-8."""
    return json.dumps(
        valor, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def seguir_lo_predictivo(ruta: str) -> dict:
    """Un entrenamiento o un scoring, mientras siga EN_PROCESO, hasta ESPERA_PREDICTIVA."""
    limite = time.monotonic() + ESPERA_PREDICTIVA
    while True:
        estado, _, recurso = pedir("GET", ruta)
        if estado != 200 or recurso["estado"] != "EN_PROCESO" or time.monotonic() > limite:
            return recurso
        time.sleep(1)


def lo_predictivo(directorio: Path) -> dict:
    """predictive/model-v1 de punta a punta, sobre un escenario predictivo ya cargado (carga.json)
    y el manifiesto que propuso construir-manifiesto-predictivo (manifiesto.json): las definiciones,
    un entrenamiento por la API y su version, valida y con sus hashes; un scoring con sus
    predicciones en rango, su riesgo complementario y su feature_hash; su drift, una explicacion
    verificada, las predicciones de una cuenta y el scoring BACKTEST del corte de TEST, que se
    reconstruye con sus relojes. Ningun dato personal en el camino."""
    from decimal import Decimal

    bitacora = json.loads((directorio / "carga.json").read_text(encoding="utf-8"))
    propuesta = json.loads((directorio / "manifiesto.json").read_text(encoding="utf-8"))
    manifiesto = propuesta["manifiesto"]
    print(f"predictive/model-v1 sobre {directorio} ({bitacora['cartera_id']})")

    estado, _, openapi = pedir("GET", "/openapi.json", con_clave=False)
    esperar(
        estado == 200
        and openapi["info"]["version"] == version_del_repositorio()
        and all(ruta in openapi["paths"] for ruta in RUTAS_PREDICTIVAS),
        f"GET /openapi.json: version {openapi['info']['version']}, con predictive/model-v1",
    )
    estado, _, error = pedir("GET", "/predictivo/modelos", con_clave=False)
    esperar(
        estado == 401 and error["codigo"] == "API_KEY_AUSENTE",
        f"GET /predictivo/modelos sin clave: {estado}",
    )
    estado, _, features = pedir("GET", "/predictivo/features")
    nombres = [f["nombre"] for f in features.get("features", [])]
    esperar(
        estado == 200
        and features["version"] == "predictive-features/public-v1"
        and re.fullmatch(r"[0-9a-f]{64}", features["hash"])
        and len(nombres) == len(set(nombres)) == 40,
        f"GET /predictivo/features: {features.get('version')} ({features.get('hash', '')[:12]}...),"
        f" {len(nombres)} features",
    )
    esperar(
        not any(SENSIBLES.search(n) for n in nombres)
        and not any(re.fullmatch(p, n) for p in features["prohibidas"] for n in nombres),
        "ninguna feature es un identificador, un dato personal, una coordenada ni una salida de "
        "decision, campo o ruteo",
    )
    estado, _, targets = pedir("GET", "/predictivo/targets")
    esperar(
        estado == 200
        and targets["version"] == "predictive-targets/public-v1"
        and [t["target"] for t in targets["targets"]] == TARGETS_PREDICTIVOS,
        f"GET /predictivo/targets: {targets.get('version')}, "
        + ", ".join(t["target"] for t in targets.get("targets", [])),
    )

    # El entrenamiento, con el manifiesto que propuso construir-manifiesto-predictivo: la API lo
    # comprueba y lo deja EN_PROCESO, y el worker lo entrena.
    estado, encabezados, abierto = enviar("/predictivo/entrenamientos", manifiesto)
    ubicacion = ubicacion_de(encabezados)
    esperar(
        estado == 201
        and abierto["estado"] == "EN_PROCESO"
        and ubicacion == f"/predictivo/entrenamientos/{abierto['training_run_id']}"
        and abierto["firma_inputs"] == propuesta["firma_inputs"]
        and abierto["hash_manifiesto"] == propuesta["hash_manifiesto"],
        f"POST /predictivo/entrenamientos: {estado} EN_PROCESO, con la firma propuesta",
    )
    e = seguir_lo_predictivo(ubicacion)
    esperar(
        (e.get("estado"), e.get("resultado"), e.get("estado_modelo"))
        == ("EXITOSA", "MODELO_PUBLICADO", "PUBLICADA")
        and re.fullmatch(r"[0-9a-f]{64}", e["dataset_hash"] or "")
        and e["ejemplos_train"] > 0
        and e["ejemplos_test"] > 0
        and e["ejemplos_censurados"] > 0,
        f"el entrenamiento: {e.get('estado')} ({e.get('resultado')}) en "
        f"{e.get('duracion_segundos')} s, {e.get('ejemplos_total', 0):,} filas "
        f"({e.get('ejemplos_censurados', 0):,} con un label censurado, fuera de TRAIN), "
        f"dataset {(e.get('dataset_hash') or '')[:12]}...",
    )
    folds = e["backtest"]["folds"]
    esperar(
        folds[-1]["estado"] == "PRINCIPAL"
        and all(f["estado"] in ("EVALUADO", "NO_DISPONIBLE") for f in folds[:-1])
        and set(e["backtest"]["holdout"]["targets"]) == set(TARGETS_PREDICTIVOS),
        f"el backtest temporal: {len(folds)} folds ("
        + ", ".join(f["estado"] for f in folds)
        + ") y el holdout de TEST, aparte",
    )
    estado, _, error = enviar("/predictivo/entrenamientos", manifiesto)
    esperar(
        estado == 409 and error["codigo"] == "ENTRENAMIENTO_YA_PUBLICADO",
        f"el mismo manifiesto otra vez: {estado} {error.get('codigo')}",
    )
    modelo_id = e["modelo_version_id"]
    estado, _, modelo = pedir("GET", f"/predictivo/modelos/{modelo_id}")
    componentes = modelo.get("componentes", [])
    integros = all(
        hashlib.sha256(_canonico(c["artefacto"])).hexdigest() == c["hash_artefacto"]
        and c["artefacto"]["formato"] == "predictive/artifact-v1"
        for c in componentes
    )
    del_modelo = hashlib.sha256(
        _canonico({c["target"]: c["hash_artefacto"] for c in componentes})
    ).hexdigest()
    esperar(
        estado == 200
        and modelo["estado"] == "PUBLICADA"
        and modelo["dataset_hash"] == e["dataset_hash"]
        and sorted(c["target"] for c in componentes) == sorted(TARGETS_PREDICTIVOS)
        and all(c["estado"] == "VALIDADO" for c in componentes)
        and integros
        and modelo["hash_modelo"] == del_modelo,
        "GET /predictivo/modelos/{modelo_version_id}: PUBLICADA, cuatro componentes VALIDADO, cada "
        f"artefacto con su SHA-256 recalculado aqui, y el hash_modelo {del_modelo[:12]}...",
    )
    for c in componentes:
        t, b = c["metricas_test"], c["baseline_test"]
        if c["algoritmo"] == "LOGISTICA_L2":
            print(
                f"      {c['target']}: ROC AUC {t['roc_auc']:.4f}, log loss {t['log_loss']:.4f} "
                f"(baseline {b['log_loss']:.4f}), Brier {t['brier']:.4f}, {c['calibracion']}"
            )
        else:
            print(f"      {c['target']}: MAE {t['mae']:.2f} (baseline {b['mae']:.2f})")

    # El scoring del ultimo corte del escenario, OPERATIVO, con la version declarada.
    ultimo = bitacora["cortes"][-1]
    pedido = {"modelo_version_id": modelo_id, "corte_id": ultimo["corte_id"], "modo": "OPERATIVO"}
    estado, encabezados, abierto = enviar("/predictivo/scorings", pedido)
    esperar(
        estado == 201 and abierto["estado"] == "EN_PROCESO",
        f"POST /predictivo/scorings del corte {ultimo['fecha_corte']}: {estado} EN_PROCESO",
    )
    s = seguir_lo_predictivo(ubicacion_de(encabezados))
    esperar(
        s.get("estado") == "EXITOSA"
        and s["predicciones_publicadas"] == s["cuentas_objetivo"] > 0
        and re.fullmatch(r"[0-9a-f]{64}", s["hash_predicciones"] or "")
        and re.fullmatch(r"[0-9a-f]{64}", s["hash_drift"] or ""),
        f"el scoring: {s.get('estado')}, {s.get('predicciones_publicadas', 0):,} predicciones, "
        f"hash {(s.get('hash_predicciones') or '')[:12]}...",
    )
    scoring = s["scoring_run_id"]
    estado, _, pagina = pedir(
        "GET",
        f"/predictivo/scorings/{scoring}/predicciones?por_pagina=500"
        "&orden=RIESGO_SIN_RECUPERACION_30D&sentido=DESC",
    )
    elementos = pagina.get("elementos", [])
    esperar(
        estado == 200
        and pagina["total"] == s["predicciones_publicadas"]
        and len(elementos) == min(500, pagina["total"])
        and all(
            Decimal(0) <= Decimal(p[n]) <= Decimal(1) for p in elementos for n in PROBABILIDADES
        ),
        f"GET .../predicciones: {estado}, {pagina.get('total', 0):,}, las tres probabilidades en "
        "[0, 1]",
    )
    esperar(
        all(
            Decimal(p["riesgo_sin_recuperacion_30d"]) + Decimal(p["p_recuperacion_positiva_30d"])
            == 1
            for p in elementos
        ),
        "el riesgo sin recuperacion es exactamente 1 - p de recuperacion positiva, en cada una",
    )
    esperar(
        all(Decimal(p["recuperacion_positiva_esperada_30d"]) >= 0 for p in elementos)
        and all(re.fullmatch(r"[0-9a-f]{64}", p["feature_hash"]) for p in elementos),
        "la recuperacion positiva esperada no es negativa, y cada prediccion trae su feature_hash",
    )
    riesgos = [Decimal(p["riesgo_sin_recuperacion_30d"]) for p in elementos]
    esperar(
        riesgos == sorted(riesgos, reverse=True)
        and all(set(p) == CAMPOS_DE_UNA_PREDICCION for p in elementos),
        "ordenadas por riesgo en la base; ninguna trae un dato personal ni una coordenada",
    )
    estado, _, drift = pedir("GET", f"/predictivo/scorings/{scoring}/drift")
    filas = drift.get("filas", [])
    esperar(
        estado == 200
        and len(filas) == 44
        and sum(f["tipo"] == "SCORE" for f in filas) == 4
        and all(f["severidad"] in ("BAJO", "MODERADO", "ALTO") for f in filas)
        and not UN_UUID.search(json.dumps([f["bins"] for f in filas])),
        f"GET .../drift: {estado}, {len(filas)} filas (40 features y 4 salidas), agregadas, "
        "sin una cuenta",
    )
    cuenta = elementos[0]["cuenta_id"]
    estado, _, explicada = pedir(
        "GET", f"/predictivo/scorings/{scoring}/cuentas/{cuenta}/explicacion"
    )
    esperar(
        estado == 200
        and explicada["feature_hash"] == elementos[0]["feature_hash"]
        and sorted(c["target"] for c in explicada["componentes"]) == sorted(TARGETS_PREDICTIVOS)
        and all(
            c["verificacion"] == {"suma_igual_al_predictor": True, "reproduce_lo_publicado": True}
            for c in explicada["componentes"]
        )
        and "no dice que cambiar" in explicada["aviso"]
        and set(explicada["features"]) == set(nombres),
        f"GET .../explicacion: {estado}, con el feature_hash publicado; las contribuciones "
        "suman el predictor y reproducen la prediccion: una asociacion, no una causa",
    )
    estado, _, de_la_cuenta = pedir("GET", f"/cuentas/{cuenta}/predictivo")
    esperar(
        estado == 200
        and [p["scoring_run_id"] for p in de_la_cuenta["elementos"]] == [scoring]
        and de_la_cuenta["elementos"][0]["feature_hash"] == elementos[0]["feature_hash"],
        f"GET /cuentas/{{cuenta_id}}/predictivo: {estado}, su prediccion del scoring",
    )

    # Reconstruible: el corte de TEST, en BACKTEST con su feature_known_at, se puntua con lo que se
    # conocia entonces; el mismo pedido otra vez es la misma firma, y un BACKTEST sin known_at no
    # existe.
    test = next(a for a in manifiesto["anclas"] if a["split"] == "TEST")
    pedido = {
        "modelo_version_id": modelo_id,
        "corte_id": test["corte_id"],
        "modo": "BACKTEST",
        "known_at": test["feature_known_at"],
    }
    estado, encabezados, _ = enviar("/predictivo/scorings", pedido)
    b = seguir_lo_predictivo(ubicacion_de(encabezados))
    esperar(
        estado == 201
        and b.get("estado") == "EXITOSA"
        and b["modo"] == "BACKTEST"
        and b["as_of"] == test["feature_as_of"],
        f"el scoring BACKTEST del corte de TEST ({test['feature_as_of']}) a su feature_known_at: "
        f"{b.get('estado')}, {b.get('predicciones_publicadas', 0):,} predicciones",
    )
    estado, _, error = enviar("/predictivo/scorings", pedido)
    esperar(
        estado == 409 and error["codigo"] == "SCORING_YA_PUBLICADO",
        f"el mismo scoring otra vez: {estado} {error.get('codigo')}",
    )
    estado, _, error = enviar("/predictivo/scorings", {**pedido, "known_at": None})
    esperar(
        estado == 422 and error["codigo"] == "KNOWN_AT_OBLIGATORIO",
        f"un BACKTEST sin known_at: {estado} {error.get('codigo')}",
    )
    print(f"training_run_id {e['training_run_id']}")
    print(f"modelo_version_id {modelo_id}")
    print(f"scoring_run_id {scoring}")
    print(f"scoring_backtest_id {b['scoring_run_id']}")
    print(f"label_known_at {test['label_known_at']}")
    print(f"pagos_completos_hasta {test['pagos_completos_hasta']}")
    print(f"lifecycle_completo_hasta {test['lifecycle_completo_hasta']}")
    return {
        "modelo_version_id": modelo_id,
        "scoring": s,
        "scoring_backtest": b,
        "ultimo": ultimo,
    }


# --- optimization/v1 ------------------------------------------------------------------------------

RUTAS_DE_OPTIMIZACION = (
    "/optimizacion/politica",
    "/optimizacion/configuraciones",
    "/optimizacion/configuraciones/{configuracion_id}",
    "/optimizacion/configuraciones/{configuracion_id}/publicacion",
    "/optimizacion/ejecuciones",
    "/optimizacion/ejecuciones/{optimizacion_run_id}",
    "/optimizacion/ejecuciones/{optimizacion_run_id}/resultados",
    "/optimizacion/ejecuciones/{optimizacion_run_id}/capacidad",
    "/optimizacion/ejecuciones/{optimizacion_run_id}/restricciones",
    "/optimizacion/ejecuciones/{optimizacion_run_id}/diagnostico",
    "/optimizacion/ejecuciones/{optimizacion_run_id}/cuentas/{cuenta_id}",
    "/cuentas/{cuenta_id}/optimizacion",
)
CUPO_DE_HUMO = 50
SUPUESTOS_DE_HUMO = "SUPUESTOS SINTETICOS de la prueba de humo: solo para probar el optimizador."
NO_CAUSAL = "no la evidencia de que ese canal cause"


def _configuracion_de_humo(nombre: str, admisibles: list[tuple[str, str]], pools: list, **otros):
    return {
        "nombre": nombre,
        "permitir_predicciones_con_imputacion": False,
        "admisibilidad": [
            {"canal_decision": d, "canal_operativo": o, "permitido": True} for d, o in admisibles
        ],
        "pools": pools,
        "restricciones": [],
        "supuestos": SUPUESTOS_DE_HUMO,
        **otros,
    }


def _pool(codigo: str, canal: str, capacidad: int | None, unidades: int, **alcance) -> dict:
    return {
        "codigo": codigo,
        "canal": canal,
        "tipo_alcance": alcance.get("tipo_alcance", "GLOBAL"),
        "referencia_alcance": alcance.get("referencia_alcance"),
        "capacidad": capacidad,
        "unidades_recurso_por_asignacion": unidades,
        "activo": True,
    }


def _publicar_configuracion(documento: dict) -> dict:
    estado, encabezados, creada = enviar("/optimizacion/configuraciones", documento)
    esperar(
        estado == 201
        and creada["estado"] == "BORRADOR"
        and creada["hash_verificado"]
        and ubicacion_de(encabezados)
        == f"/optimizacion/configuraciones/{creada['configuracion_id']}",
        f"POST /optimizacion/configuraciones ({documento['nombre']}): {estado} BORRADOR, hash "
        f"{creada.get('hash_contenido', '')[:12]}...",
    )
    estado, _, publicada = enviar(
        f"/optimizacion/configuraciones/{creada['configuracion_id']}/publicacion", {}
    )
    esperar(
        estado == 200
        and publicada["estado"] == "PUBLICADA"
        and publicada["hash_contenido"] == creada["hash_contenido"],
        f"POST .../publicacion: {estado} PUBLICADA, el mismo hash",
    )
    return publicada


def _optimizar(entrada: dict) -> dict:
    """La ejecucion: 201 EN_PROCESO (el MILP no corre en la peticion) y, despues, lo que dejo el
    worker."""
    estado, encabezados, abierta = enviar("/optimizacion/ejecuciones", entrada)
    esperar(
        estado == 201
        and abierta["estado"] == "EN_PROCESO"
        and abierta["hash_plan"] is None
        and abierta["cuentas_publicadas"] == 0
        and ubicacion_de(encabezados)
        == f"/optimizacion/ejecuciones/{abierta['optimizacion_run_id']}",
        f"POST /optimizacion/ejecuciones: {estado} EN_PROCESO, sin plan todavia",
    )
    return seguir_lo_predictivo(ubicacion_de(encabezados))


def _todos(ruta: str) -> list[dict]:
    elementos, pagina = [], 1
    while True:
        separador = "&" if "?" in ruta else "?"
        estado, _, cuerpo = pedir("GET", f"{ruta}{separador}por_pagina=500&pagina={pagina}")
        esperar(estado == 200, f"GET {ruta}: {estado}")
        elementos += cuerpo["elementos"]
        if pagina * 500 >= cuerpo["total"]:
            return elementos
        pagina += 1


def _certificado_optimo(e: dict, donde: str) -> None:
    niveles = e["certificado"]["niveles"]
    # Un nivel sin variables libres no llama al solver: los anteriores ya lo determinaron.
    por_highs = sum(n["invocado"] for n in niveles)
    esperar(
        (e["estado"], e["resultado"], e["solver_status"])
        == ("EXITOSA", "PLAN_PUBLICADO", "OPTIMAL")
        and [n["nivel"] for n in niveles] == [1, 2, 3, 4]
        and all(n["estado"] == "OPTIMAL" for n in niveles)
        and all(abs(n["mejor_cota"] - n["objetivo"]) <= 1e-6 for n in niveles)
        and all(n["gap_relativo"] == 0 for n in niveles)
        and e["mip_gap"] == "0.000000000000"
        and e["cuentas_publicadas"] == e["cuentas_universo"] > 0
        and re.fullmatch(r"[0-9a-f]{64}", e["hash_plan"] or ""),
        f"{donde}: EXITOSA, el optimo de los cuatro niveles demostrado (OPTIMAL, cota = objetivo, "
        f"gap 0; {por_highs} por HiGHS y {len(niveles) - por_highs} sin variables libres) en "
        f"{e.get('solver_seconds')} s; {e.get('cuentas_asignadas', 0):,} asignadas de "
        f"{e.get('cuentas_universo', 0):,}, hash {(e.get('hash_plan') or '')[:12]}...",
    )


def la_optimizacion(predictivo: dict, ruteo_oficial: str | None) -> None:
    """optimization/v1 de punta a punta, en sombra. Sobre el ultimo corte del escenario predictivo,
    con su scoring y una decision/v2 OPERATIVO del mismo corte: una configuracion pequena (un solo
    pool DIGITAL, sin imputacion) cuyo optimo se calcula aqui aparte (la suma de las mayores
    recuperaciones esperadas COMPLETA que caben), sin violar la capacidad ni asignar un canal
    inadmisible; una infactible, sin plan y con su diagnostico; y entradas de cortes distintos, que
    no se abren. Con un ruteo/v2 del corte oficial, CAMPO: solo paradas PROGRAMADA, cada una con
    su gestor y su orden."""
    from decimal import Decimal

    print("optimization/v1")
    estado, _, openapi = pedir("GET", "/openapi.json", con_clave=False)
    esperar(
        estado == 200 and all(r in openapi["paths"] for r in RUTAS_DE_OPTIMIZACION),
        "GET /openapi.json: con optimization/v1",
    )
    esperar(
        not any(
            any(p in ruta for p in ("ejecutar", "enviar", "llamar", "visitar", "aplicar"))
            for ruta in openapi["paths"]
            if "optimizacion" in ruta
        ),
        "ninguna ruta de optimization/v1 ejecuta una accion",
    )
    estado, _, politica = pedir("GET", "/optimizacion/politica")
    esperar(
        estado == 200
        and politica["version"] == "optimization-public-v1"
        and re.fullmatch(r"[0-9a-f]{64}", politica["hash"])
        and re.fullmatch(r"[0-9a-f]{64}", politica["solver_fingerprint"])
        and "p_recuperacion_positiva_30d" in politica["definicion"]["valor"]["no_se_multiplica_por"]
        and any("causalmente optima" in x for x in politica["limitaciones"]),
        f"GET /optimizacion/politica: {politica.get('version')} ({politica.get('hash', '')[:12]}"
        f"...), HiGHS {politica.get('huella_solver', {}).get('highs')}; el valor no se multiplica "
        "por ninguna probabilidad",
    )

    # La decision/v2 del ultimo corte del escenario: OPERATIVO, del mismo corte que el scoring.
    s, ultimo = predictivo["scoring"], predictivo["ultimo"]
    estado, encabezados, abierta = enviar("/decisiones-v2", {"corte_id": ultimo["corte_id"]})
    decision = seguir(ubicacion_de(encabezados)) if estado == 201 else abierta
    esperar(
        decision.get("estado") == "EXITOSA" and decision["as_of"] == s["as_of"],
        f"POST /decisiones-v2 del corte {ultimo['fecha_corte']}: {decision.get('estado')}, as_of "
        f"{decision.get('as_of')} = el del scoring",
    )

    documento = _configuracion_de_humo(
        "Prueba de humo: un solo pool DIGITAL",
        [("DIGITAL", "DIGITAL"), ("TELEFONICA", "DIGITAL"), ("CAMPO", "DIGITAL")],
        [_pool("DIGITAL_GLOBAL", "DIGITAL", CUPO_DE_HUMO, 1)],
    )
    configuracion = _publicar_configuracion(documento)
    estado, _, otra = enviar("/optimizacion/configuraciones", documento)
    esperar(
        estado == 200
        and otra["reutilizada"]
        and otra["configuracion_id"] == configuracion["configuracion_id"],
        f"el mismo contenido otra vez: {estado}, la misma version",
    )
    entrada = {
        "scoring_run_id": s["scoring_run_id"],
        "decision_run_id": decision["decision_run_id"],
        "configuracion_id": configuracion["configuracion_id"],
        "fecha_operacion": (datetime.fromisoformat(ultimo["fecha_corte"]) + timedelta(days=1))
        .date()
        .isoformat(),
    }
    e = _optimizar(entrada)
    _certificado_optimo(e, "el plan de un pool DIGITAL")
    run = e["optimizacion_run_id"]

    # El optimo, calculado aqui sin el solver: con un solo pool y sin coberturas, las de mayor
    # recuperacion positiva esperada COMPLETA que caben. En centavos exactos, sin ninguna p.
    mejores = _todos(
        f"/predictivo/scorings/{s['scoring_run_id']}/predicciones?estado=COMPLETA"
        "&orden=RECUPERACION_POSITIVA_ESPERADA_30D&sentido=DESC"
    )[:CUPO_DE_HUMO]
    esperado = sum(int(Decimal(p["recuperacion_positiva_esperada_30d"]) * 100) for p in mejores)
    esperar(
        e["objetivo_primario_centavos"] == esperado,
        f"el valor del plan, {e['objetivo_primario_centavos']:,} centavos, es la suma de las "
        f"{len(mejores)} mayores recuperaciones esperadas COMPLETA, calculada aqui",
    )
    estado, _, capacidad = pedir("GET", f"/optimizacion/ejecuciones/{run}/capacidad")
    (uso,) = capacidad.get("elementos", [None])
    esperar(
        estado == 200
        and uso["pool"] == "DIGITAL_GLOBAL"
        and uso["utilizada"] <= uso["capacidad"] == CUPO_DE_HUMO
        and uso["holgura"] == uso["capacidad"] - uso["utilizada"]
        and uso["utilizada"] == e["cuentas_asignadas"],
        f"GET .../capacidad: {uso and uso['utilizada']} de {CUPO_DE_HUMO}, ninguna capacidad "
        "excedida",
    )
    estado, _, restricciones = pedir("GET", f"/optimizacion/ejecuciones/{run}/restricciones")
    esperar(
        estado == 200
        and all(
            r["binding"] == (r["holgura"] == 0) and r["holgura"] >= 0
            for r in restricciones["elementos"]
        )
        and any(r["codigo"] == "POOL:DIGITAL_GLOBAL" for r in restricciones["elementos"]),
        f"GET .../restricciones: {restricciones.get('total')}, cada una con su holgura",
    )
    resultados = _todos(f"/optimizacion/ejecuciones/{run}/resultados")
    asignadas = [r for r in resultados if r["estado"] == "ASIGNADA"]
    imputadas = pedir(
        "GET", f"/predictivo/scorings/{s['scoring_run_id']}/predicciones?estado=CON_IMPUTACION"
    )[2]["total"]
    no_elegibles = [r for r in resultados if r["estado"] == "NO_ELEGIBLE"]
    esperar(
        len(resultados) == e["cuentas_universo"] == s["predicciones_publicadas"]
        and len(asignadas) == e["cuentas_asignadas"]
        and all(
            (r["canal_asignado"], r["pool"]) == ("DIGITAL", "DIGITAL_GLOBAL") for r in asignadas
        )
        and len(no_elegibles) == imputadas
        and all(r["motivo"] == "PREDICCION_CON_IMPUTACION" for r in no_elegibles),
        f"GET .../resultados: {len(resultados):,} cuentas, todo o nada; ninguna asignacion "
        f"inadmisible; {imputadas:,} CON_IMPUTACION no elegibles",
    )
    cuenta = asignadas[0]["cuenta_id"]
    estado, _, explicada = pedir("GET", f"/optimizacion/ejecuciones/{run}/cuentas/{cuenta}")
    esperar(
        estado == 200
        and explicada["resultado"]["estado"] == "ASIGNADA"
        and NO_CAUSAL in explicada["explicacion"],
        f"GET .../cuentas/{{cuenta_id}}: {estado}, su resultado y por que, sin causalidad",
    )
    estado, _, planes = pedir("GET", f"/cuentas/{cuenta}/optimizacion")
    esperar(
        estado == 200 and run in [p["optimizacion_run_id"] for p in planes["elementos"]],
        f"GET /cuentas/{{cuenta_id}}/optimizacion: {estado}, el plan publicado",
    )
    estado, _, error = enviar("/optimizacion/ejecuciones", entrada)
    esperar(
        estado == 409 and error["codigo"] == "OPTIMIZACION_YA_PUBLICADA",
        f"las mismas entradas otra vez: {estado} {error.get('codigo')}",
    )
    otro_corte = {**entrada, "scoring_run_id": predictivo["scoring_backtest"]["scoring_run_id"]}
    estado, _, error = enviar("/optimizacion/ejecuciones", otro_corte)
    esperar(
        estado == 422 and error["codigo"] == "CORTES_DISTINTOS",
        f"un scoring de otro corte: {estado} {error.get('codigo')}, sin abrir nada",
    )

    # Una infactible: un minimo que ninguna cartera puede cumplir. Sin plan, con su diagnostico.
    imposible = _publicar_configuracion(
        {
            **documento,
            "nombre": "Prueba de humo: un minimo imposible",
            "restricciones": [
                {
                    "codigo": "MIN_AUTOMOTRIZ",
                    "tipo": "MIN_CUENTAS",
                    "dimension": "PRODUCTO",
                    "valor_dimension": "AUTOMOTRIZ",
                    "limite": 1_000_000,
                    "activa": True,
                }
            ],
        }
    )
    f = _optimizar({**entrada, "configuracion_id": imposible["configuracion_id"]})
    estado, _, diagnostico = pedir(
        "GET", f"/optimizacion/ejecuciones/{f['optimizacion_run_id']}/diagnostico"
    )
    estado_r, _, error = pedir(
        "GET", f"/optimizacion/ejecuciones/{f['optimizacion_run_id']}/resultados"
    )
    esperar(
        (f.get("estado"), f.get("resultado"), f.get("solver_status"))
        == ("FALLIDA", "INFACTIBLE", "NO_INVOCADO")
        and f["cuentas_publicadas"] == 0
        and f["hash_plan"] is None
        and estado == 200
        and not diagnostico["plan_publicado"]
        and diagnostico["diagnostico"]["hallazgos"][0]["codigo"] == "MINIMO_SUPERA_ELEGIBLES"
        and (estado_r, error.get("codigo")) == (409, "OPTIMIZACION_NO_PUBLICADA"),
        "un minimo imposible: FALLIDA (INFACTIBLE) antes del solver, sin ningun resultado, con su "
        "diagnostico",
    )
    print(f"optimizacion_run_id {run}")
    print(f"optimizacion_infactible_run_id {f['optimizacion_run_id']}")
    if ruteo_oficial is not None:
        el_campo_optimizado(predictivo, ruteo_oficial)


def el_campo_optimizado(predictivo: dict, ruteo_run_id: str) -> None:
    """CAMPO sobre el corte oficial: su scoring (BACKTEST, con la frontera de su decision/v2), la
    decision/v2 de su plan de campo y el ruteo/v2 declarado. Un pool por gestor con paradas
    PROGRAMADA, sin capacidad declarada: su capacidad efectiva son esas paradas. Cada cuenta en
    CAMPO es una parada PROGRAMADA de su propio gestor, con su orden de visita, que no cambia."""
    estado, _, ruteo = pedir("GET", f"/ruteo-v2/ejecuciones/{ruteo_run_id}")
    _, _, plan = pedir("GET", f"/campo-v2/planes/{ruteo['campo_run_id']}")
    _, _, decision = pedir("GET", f"/decisiones-v2/{plan['decision_run_id']}")
    esperar(
        estado == 200 and ruteo["estado"] == plan["estado"] == decision["estado"] == "EXITOSA",
        f"la cadena del corte oficial: decision/v2 {decision['decision_run_id'][:8]}..., campo/v2 "
        f"{plan['campo_run_id'][:8]}..., ruteo/v2 {ruteo_run_id[:8]}...",
    )
    pedido = {
        "modelo_version_id": predictivo["modelo_version_id"],
        "corte_id": plan["corte"]["corte_id"],
        "modo": "BACKTEST",
        "known_at": decision["known_at"],
    }
    estado, encabezados, _ = enviar("/predictivo/scorings", pedido)
    s = seguir_lo_predictivo(ubicacion_de(encabezados))
    esperar(
        estado == 201 and s.get("estado") == "EXITOSA" and s["as_of"] == decision["as_of"],
        f"el scoring del corte oficial con el modelo publicado, a la frontera de su decision/v2: "
        f"{s.get('estado')}, {s.get('predicciones_publicadas', 0):,} predicciones",
    )
    programadas = _todos(f"/ruteo-v2/ejecuciones/{ruteo_run_id}/paradas?estado=PROGRAMADA")
    por_gestor: dict[str, int] = {}
    for p in programadas:
        por_gestor[p["gestor"]] = por_gestor.get(p["gestor"], 0) + 1
    esperar(
        bool(por_gestor), f"{len(programadas)} paradas PROGRAMADA de {len(por_gestor)} gestores"
    )
    documento = _configuracion_de_humo(
        "Prueba de humo: CAMPO por gestor",
        [
            ("DIGITAL", "DIGITAL"),
            ("TELEFONICA", "TELEFONICA"),
            ("CAMPO", "CAMPO"),
            ("CAMPO", "TELEFONICA"),
        ],
        [
            _pool("DIGITAL_GLOBAL", "DIGITAL", 20, 1),
            _pool("TELEFONICA_GLOBAL", "TELEFONICA", 10, 4),
            *(
                _pool(
                    f"CAMPO_{g}",
                    "CAMPO",
                    None,
                    20,
                    tipo_alcance="GESTOR_CAMPO",
                    referencia_alcance=g,
                )
                for g in sorted(por_gestor)
            ),
        ],
        # Declarado: el modelo es de otra cartera, y sus predicciones de este corte pueden venir
        # con imputacion.
        permitir_predicciones_con_imputacion=True,
    )
    configuracion = _publicar_configuracion(documento)
    e = _optimizar(
        {
            "scoring_run_id": s["scoring_run_id"],
            "decision_run_id": decision["decision_run_id"],
            "configuracion_id": configuracion["configuracion_id"],
            "fecha_operacion": ruteo["fecha_operacion"],
            "ruteo_run_id": ruteo_run_id,
        }
    )
    _certificado_optimo(e, "el plan con CAMPO")
    run = e["optimizacion_run_id"]
    paradas = {p["cuenta_id"]: p for p in programadas}
    en_campo = _todos(f"/optimizacion/ejecuciones/{run}/resultados?canal=CAMPO")
    esperar(
        all(
            r["cuenta_id"] in paradas
            and r["gestor"] == paradas[r["cuenta_id"]]["gestor"]
            and r["pool"] == f"CAMPO_{r['gestor']}"
            and r["orden_ruteo_original"] == paradas[r["cuenta_id"]]["orden_visita"]
            for r in en_campo
        ),
        f"{len(en_campo)} cuentas en CAMPO, cada una una parada PROGRAMADA de su propio gestor, "
        "con su orden de visita",
    )
    usos = _todos(f"/optimizacion/ejecuciones/{run}/capacidad?canal=CAMPO")
    esperar(
        len(usos) == len(por_gestor)
        and all(
            u["capacidad"] == por_gestor[u["referencia_alcance"]]
            and u["utilizada"] <= u["capacidad"]
            for u in usos
        ),
        "la capacidad efectiva de cada gestor son sus paradas PROGRAMADA, y ninguna se excede",
    )
    print(f"optimizacion_campo_run_id {run}")


def main(
    archivo: Path | None,
    oficial: Path | None = None,
    corte: str | None = None,
    pagos: Path | None = None,
    escenario: Path | None = None,
    geografia: bool = False,
    campo: bool = False,
    red_vial: str | None = None,
    base_ruteo: str | None = None,
    analitica: bool = False,
    predictivo: Path | None = None,
    optimizacion: bool = False,
    ruteo_oficial: str | None = None,
) -> None:
    print(f"Prueba de humo contra {BASE} con {archivo or predictivo}")

    estado, salud = esperar_a_la_api()
    esperar(estado == 200 and salud.get("base_de_datos") == "ok", "GET /salud 200, base ok")
    if archivo is None:
        publicado = lo_predictivo(predictivo)
        if optimizacion:
            la_optimizacion(publicado, ruteo_oficial)
        print("Todo en orden.")
        return

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
        "/motor-pagos",
        "/motor-pagos/{motor_pagos_run_id}",
        "/motor-pagos/{motor_pagos_run_id}/resultados",
        "/movimientos",
        "/movimientos/{movimiento_id}",
        "/movimientos/{movimiento_id}/observaciones",
        "/cuentas/{cuenta_id}/movimientos",
        "/cuentas/{cuenta_id}/gestiones",
        "/gestiones/{gestion_id}",
        "/gestiones/{gestion_id}/anulaciones",
        "/gestiones/{gestion_id}/promesas",
        "/gestiones/{gestion_id}/convenios",
        "/promesas/{promesa_id}",
        "/promesas/{promesa_id}/cancelaciones",
        "/convenios/{convenio_id}",
        "/convenios/{convenio_id}/cancelaciones",
        "/cuentas/{cuenta_id}/promesas",
        "/cuentas/{cuenta_id}/convenios",
        "/cuentas/{cuenta_id}/lifecycle",
        "/evaluaciones-promesas",
        "/evaluaciones-promesas/{evaluacion_run_id}",
        "/evaluaciones-promesas/{evaluacion_run_id}/promesas",
        "/atribuciones",
        "/atribuciones/{atribucion_run_id}",
        "/atribuciones/{atribucion_run_id}/resultados",
        "/movimientos/{movimiento_id}/atribuciones",
        "/cuentas/{cuenta_id}/atribuciones",
        "/decisiones-v2",
        "/decisiones-v2/{decision_run_id}",
        "/decisiones-v2/{decision_run_id}/resultados",
        "/decisiones-v2/{decision_run_id}/resultados/{cuenta_id}",
        "/cuentas/{cuenta_id}/decisiones-v2",
        "/cartera/cortes/{corte_id}/decisiones-v2",
        "/geografia/versiones",
        "/geografia/versiones/{geografia_version_id}",
        "/geografia/ejecuciones",
        "/geografia/ejecuciones/{geografia_run_id}",
        "/geografia/ejecuciones/{geografia_run_id}/resultados",
        "/cuentas/{cuenta_id}/geografia",
        "/bases/versiones",
        "/bases/versiones/{bases_version_id}",
        "/territorial-v2",
        "/territorial-v2/{territorial_run_id}",
        "/territorial-v2/{territorial_run_id}/resultados",
        "/cuentas/{cuenta_id}/territorial-v2",
        "/campo/configuraciones",
        "/campo/configuraciones/{configuracion_campo_version_id}",
        "/campo-v2/planes",
        "/campo-v2/planes/{campo_run_id}",
        "/campo-v2/planes/{campo_run_id}/asignaciones",
        "/campo-v2/planes/{campo_run_id}/cargas",
        "/campo-v2/planes/{campo_run_id}/unidades",
        "/cuentas/{cuenta_id}/campo-v2",
        "/ruteo-v2/redes",
        "/ruteo-v2/redes/{red_vial_version_id}",
        "/ruteo-v2/ejecuciones",
        "/ruteo-v2/ejecuciones/{ruteo_run_id}",
        "/ruteo-v2/ejecuciones/{ruteo_run_id}/rutas",
        "/ruteo-v2/ejecuciones/{ruteo_run_id}/paradas",
        "/ruteo-v2/rutas/{ruta_id}",
        "/ruteo-v2/rutas/{ruta_id}/paradas",
        "/cuentas/{cuenta_id}/ruteo-v2",
        "/analytics/catalogo",
        "/analytics/ejecuciones",
        "/analytics/ejecuciones/{analytics_run_id}",
        "/analytics/ejecuciones/{analytics_run_id}/agregados",
        "/analytics/ejecuciones/{analytics_run_id}/resumen",
        "/analytics/ejecuciones/{analytics_run_id}/embudos",
        "/analytics/ejecuciones/{analytics_run_id}/controles",
        *RUTAS_PREDICTIVAS,
    )
    esperar(
        estado == 200
        and version == version_del_repositorio()
        and all(ruta in documentadas for ruta in rutas_esperadas),
        f"GET /openapi.json {estado}: version {version}, con la orquestacion, los motores, "
        "la evidencia de las fuentes, los pagos, la Cuenta 360, el motor de pagos, el lifecycle, "
        "la atribucion, decision/v2, la geografia, las bases, territorial/v2, campo/v2, "
        "ruteo/v2, analytics/v1 y predictive/model-v1",
    )
    if oficial is not None:
        la_cartera_oficial(oficial, corte)
    if pagos is not None:
        los_pagos(pagos)
    if escenario is not None:
        el_modelo_historico(escenario)
        el_motor_de_pagos(escenario)
    decision = None
    if oficial is not None and pagos is not None:
        el_lifecycle(pagos, corte)
        decision = la_decision_v2(
            oficial, pagos, corte, hay_cortes_posteriores=escenario is not None
        )
    if geografia:
        del_dia, geografia_del_corte = la_geografia(corte)
        if campo:
            el_campo(corte, del_dia, decision, geografia_del_corte)
        if red_vial is not None:
            operacion = la_ruta_vial(
                corte, del_dia, decision, geografia_del_corte, red_vial, base_ruteo
            )
            if analitica:
                la_analitica(corte, del_dia, decision, geografia_del_corte, operacion)
    if predictivo is not None:
        publicado = lo_predictivo(predictivo)
        if optimizacion:
            la_optimizacion(publicado, ruteo_oficial)
    print("Todo en orden.")


if __name__ == "__main__":
    argumentos = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    argumentos.add_argument(
        "archivo", type=Path, nargs="?", help="Una cartera de cartera/v1 (sin --predictivo)."
    )
    argumentos.add_argument("--oficial", type=Path, help="Una cartera oficial de cartera/v2.")
    argumentos.add_argument("--corte", help="La fecha de corte de la cartera oficial, AAAA-MM-DD.")
    argumentos.add_argument("--pagos", type=Path, help="Un archivo de pagos de pagos/v1.")
    argumentos.add_argument(
        "--escenario", type=Path, help="El directorio de un escenario de generar-escenario."
    )
    argumentos.add_argument(
        "--geografia",
        action="store_true",
        help="La referencia ya esta importada y la cartera oficial trae coordenadas: recorre "
        "geografia/v1, las bases y territorial/v2 sobre su corte.",
    )
    argumentos.add_argument(
        "--campo",
        action="store_true",
        help="Recorre campo/v2 sobre el corte oficial: bases de campo, una configuracion de "
        "campo sintetica y el plan del dia siguiente, con cada estado.",
    )
    argumentos.add_argument(
        "--ruteo",
        metavar="RED_VIAL_VERSION_ID",
        help="Recorre ruteo/v2 sobre esa red vial PUBLICADA, que un OSRM ya sirve al worker.",
    )
    argumentos.add_argument(
        "--base-ruteo",
        metavar="LON,LAT",
        help="La base de ruteo: el cruce de la red que imprimio generar-red-sintetica.",
    )
    argumentos.add_argument(
        "--analytics",
        action="store_true",
        help="Recorre analytics/v1 sobre la cohorte del corte oficial, hasta el dia del plan de "
        "ruteo, con el manifiesto de lo que la prueba publico.",
    )
    argumentos.add_argument(
        "--predictivo",
        type=Path,
        metavar="DIRECTORIO",
        help="Un escenario predictivo ya cargado, con el manifiesto.json que propuso "
        "construir-manifiesto-predictivo: recorre predictive/model-v1.",
    )
    argumentos.add_argument(
        "--optimizacion",
        action="store_true",
        help="Con --predictivo: recorre optimization/v1 sobre el ultimo corte del escenario, con "
        "su scoring y una decision/v2 del mismo corte.",
    )
    argumentos.add_argument(
        "--ruteo-oficial",
        metavar="RUTEO_RUN_ID",
        help="Con --optimizacion: tambien CAMPO, sobre el corte oficial y ese ruteo/v2.",
    )
    leidos = argumentos.parse_args()
    if leidos.archivo is None:
        if leidos.predictivo is None:
            argumentos.error("Falta el archivo, o --predictivo.")
        if any(
            (
                leidos.oficial,
                leidos.corte,
                leidos.pagos,
                leidos.escenario,
                leidos.geografia,
                leidos.campo,
                leidos.ruteo,
                leidos.base_ruteo,
                leidos.analytics,
            )
        ):
            argumentos.error("Sin el archivo, solo va --predictivo.")
    if (leidos.oficial is None) != (leidos.corte is None):
        argumentos.error("--oficial y --corte van juntos.")
    if leidos.geografia and leidos.oficial is None:
        argumentos.error("--geografia va con --oficial y --corte.")
    if leidos.campo and not (leidos.geografia and leidos.pagos is not None):
        argumentos.error("--campo va con --geografia y --pagos.")
    if (leidos.ruteo is None) != (leidos.base_ruteo is None):
        argumentos.error("--ruteo y --base-ruteo van juntos.")
    if leidos.ruteo is not None and not leidos.campo:
        argumentos.error("--ruteo va con --campo.")
    if leidos.analytics and leidos.ruteo is None:
        argumentos.error("--analytics va con --ruteo.")
    if leidos.optimizacion and leidos.predictivo is None:
        argumentos.error("--optimizacion va con --predictivo.")
    if leidos.ruteo_oficial is not None and not leidos.optimizacion:
        argumentos.error("--ruteo-oficial va con --optimizacion.")
    main(
        leidos.archivo,
        leidos.oficial,
        leidos.corte,
        leidos.pagos,
        leidos.escenario,
        geografia=leidos.geografia,
        campo=leidos.campo,
        red_vial=leidos.ruteo,
        base_ruteo=leidos.base_ruteo,
        analitica=leidos.analytics,
        predictivo=leidos.predictivo,
        optimizacion=leidos.optimizacion,
        ruteo_oficial=leidos.ruteo_oficial,
    )
