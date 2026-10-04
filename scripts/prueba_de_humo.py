"""Prueba de humo: el flujo automatico del README, de punta a punta, contra una API y un worker
que ya corren.

Sube una cartera y no pide nada mas: sigue su flujo automatico hasta que el worker lo completa,
de la ingesta al ruteo. Despues revisa los trabajos de la cola que lo ejecutaron, uno por etapa, y
lo que publico cada etapa con los identificadores del flujo: la corrida con sus rechazos y su
resumen, las decisiones por cuenta, los municipios, las rutas y las paradas de la primera.
Comprueba que ninguna etapa se publica dos veces, ni a mano, y que el OpenAPI corresponde a esta
version y documenta la orquestacion. Verifica los codigos HTTP de cada paso. Solo usa la
biblioteca estandar, para correr igual en el CI, dentro del contenedor o en una laptop:

    python scripts/prueba_de_humo.py datos/cartera_sintetica.xlsx

Lee MC_URL_API (por omision http://localhost:8000) y MC_API_KEY. Termina con codigo 1 en
cuanto algo no sale como debe.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
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


def subir(archivo: Path, *, con_clave=True):
    frontera = uuid.uuid4().hex
    cuerpo = b"".join(
        [
            f"--{frontera}\r\n".encode(),
            f'Content-Disposition: form-data; name="archivo"; filename="{archivo.name}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n".encode(),
            archivo.read_bytes(),
            f"\r\n--{frontera}--\r\n".encode(),
        ]
    )
    tipo = f"multipart/form-data; boundary={frontera}"
    return pedir("POST", "/corridas", cuerpo=cuerpo, tipo=tipo, con_clave=con_clave)


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


def main(archivo: Path) -> None:
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
    )
    esperar(
        estado == 200
        and version == version_del_repositorio()
        and all(ruta in documentadas for ruta in rutas_esperadas),
        f"GET /openapi.json {estado}: version {version}, con la orquestacion, el Motor "
        "Territorial y el Motor de Ruteo",
    )
    print("Todo en orden.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(Path(sys.argv[1]))
