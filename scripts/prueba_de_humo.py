"""Prueba de humo: el flujo del README, de punta a punta, contra una API que ya corre.

Sube una cartera, espera a que su corrida termine y consulta estado, rechazos y resumen.
Despues la decide con el Decision Engine: consulta la ejecucion, la busca en el historial, lee
sus decisiones por cuenta y comprueba que no se decide dos veces. Luego organiza esas decisiones
por municipio con el Motor Territorial: consulta la ejecucion territorial, la busca en el
historial, lee sus municipios y comprueba que no se organizan dos veces. Al final rutea esos
municipios con el Motor de Ruteo: consulta la ejecucion de ruteo, la busca en el historial, lee
sus rutas y las paradas de la primera, y comprueba que no se rutean dos veces. Verifica los
codigos HTTP de cada paso. Solo usa la biblioteca estandar, para correr igual en el CI, dentro
del contenedor o en una laptop:

    python scripts/prueba_de_humo.py datos/cartera_sintetica.xlsx

Lee MC_URL_API (por omision http://localhost:8000) y MC_API_KEY. Termina con codigo 1 en
cuanto algo no sale como debe.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BASE = os.environ.get("MC_URL_API", "http://localhost:8000").rstrip("/")
CLAVE = os.environ.get("MC_API_KEY", "clave-local-de-desarrollo")
ESPERA_MAXIMA = 120  # segundos


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


def main(archivo: Path) -> None:
    print(f"Prueba de humo contra {BASE} con {archivo}")

    estado, salud = esperar_a_la_api()
    esperar(estado == 200 and salud.get("base_de_datos") == "ok", "GET /salud 200, base ok")

    estado, _, error = subir(archivo, con_clave=False)
    esperar(estado == 401 and error["codigo"] == "API_KEY_AUSENTE", "POST sin clave: 401")

    estado, encabezados, corrida = subir(archivo)
    esperar(estado == 201 and corrida["estado"] == "EN_PROCESO", "POST /corridas: 201 EN_PROCESO")
    ubicacion = encabezados.get("location") or encabezados.get("Location")
    esperar(ubicacion == f"/corridas/{corrida['run_id']}", f"Location: {ubicacion}")

    estado, _, error = subir(archivo)
    esperar(
        estado == 409 and error["codigo"] in {"ARCHIVO_EN_PROCESO", "ARCHIVO_YA_PUBLICADO"},
        f"el mismo archivo otra vez: 409 {error.get('codigo')}",
    )

    limite = time.monotonic() + ESPERA_MAXIMA
    while corrida["estado"] == "EN_PROCESO" and time.monotonic() < limite:
        time.sleep(0.5)
        _, _, corrida = pedir("GET", ubicacion)
    esperar(corrida["estado"] == "EXITOSA", f"la corrida termina EXITOSA: {corrida['detalle']}")
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
    estado, _, resumen = pedir("GET", f"/cartera/resumen?run_id={corrida['run_id']}")
    esperar(
        estado == 200
        and resumen["run_id"] == corrida["run_id"]
        and resumen["total_cuentas"] == corrida["filas_validas"],
        f"GET /cartera/resumen?run_id=...: {resumen.get('total_cuentas')} cuentas, "
        f"saldo {resumen.get('saldo_total')}, {resumen.get('total')} segmentos",
    )
    estado, _, vigente = pedir("GET", "/cartera/resumen")
    esperar(estado == 200, f"GET /cartera/resumen: vigente la corrida {vigente.get('run_id')}")

    # El Decision Engine sobre la corrida recien publicada. El POST es sincrono: responde cuando
    # ya decidio todas las cuentas, asi que se le da mas tiempo que a una consulta.
    ruta_decisiones = f"/corridas/{corrida['run_id']}/decisiones"
    estado, encabezados, ejecucion = pedir("POST", ruta_decisiones, timeout=ESPERA_MAXIMA)
    esperar(
        estado == 201 and ejecucion["estado"] == "EXITOSA",
        f"POST /corridas/{{run_id}}/decisiones: {estado} "
        f"{ejecucion.get('estado', ejecucion.get('codigo'))}: "
        f"{ejecucion.get('detalle', ejecucion.get('mensaje'))}",
    )
    esperar(
        ejecucion["version_reglas"] == "decision/v1"
        and ejecucion["cuentas_evaluadas"] == corrida["filas_validas"]
        and ejecucion["cuentas_decididas"] == corrida["filas_validas"],
        f"reglas {ejecucion['version_reglas']}: evaluadas {ejecucion['cuentas_evaluadas']} = "
        f"decididas {ejecucion['cuentas_decididas']} = validas {corrida['filas_validas']}, "
        f"en {ejecucion['duracion_segundos']} s",
    )
    ubicacion_ejecucion = encabezados.get("location") or encabezados.get("Location")
    esperar(
        ubicacion_ejecucion == f"/decisiones/{ejecucion['decision_run_id']}",
        f"Location: {ubicacion_ejecucion}",
    )

    estado, _, consultada = pedir("GET", ubicacion_ejecucion)
    mismos = (
        "decision_run_id",
        "run_id",
        "version_reglas",
        "estado",
        "cuentas_evaluadas",
        "cuentas_decididas",
    )
    esperar(
        estado == 200 and all(consultada.get(campo) == ejecucion[campo] for campo in mismos),
        f"GET /decisiones/{{decision_run_id}}: {estado}, la misma ejecucion",
    )

    estado, _, historial = pedir("GET", ruta_decisiones)
    en_el_historial = [e["decision_run_id"] for e in historial.get("elementos", [])]
    esperar(
        estado == 200
        and historial["total"] >= 1
        and ejecucion["decision_run_id"] in en_el_historial,
        f"GET /corridas/{{run_id}}/decisiones: {estado}, {historial.get('total')} ejecucion(es), "
        "entre ellas la nueva",
    )

    estado, _, cuentas = pedir("GET", f"{ubicacion_ejecucion}/cuentas?por_pagina=5")
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

    # Una corrida se decide con exito una sola vez por version de las reglas. El 409 no dice
    # cual ejecucion fue, ni con Location: eso lo dice el historial.
    estado, encabezados, error = pedir("POST", ruta_decisiones, timeout=ESPERA_MAXIMA)
    esperar(
        estado == 409
        and error["codigo"] == "DECISION_YA_GENERADA"
        and error["run_id"] == corrida["run_id"]
        and not (encabezados.get("location") or encabezados.get("Location")),
        f"POST /corridas/{{run_id}}/decisiones otra vez: {estado} {error.get('codigo')}, "
        "sin Location",
    )

    # El Motor Territorial sobre las decisiones recien publicadas. Tambien es sincrono: responde
    # cuando ya organizo todos los municipios.
    ruta_territoriales = f"/decisiones/{ejecucion['decision_run_id']}/territoriales"
    estado, encabezados, territorial = pedir("POST", ruta_territoriales, timeout=ESPERA_MAXIMA)
    esperar(
        estado == 201 and territorial["estado"] == "EXITOSA",
        f"POST /decisiones/{{decision_run_id}}/territoriales: {estado} "
        f"{territorial.get('estado', territorial.get('codigo'))}: "
        f"{territorial.get('detalle', territorial.get('mensaje'))}",
    )
    esperar(
        territorial["version_reglas"] == "territorial/v1"
        and territorial["decision_run_id"] == ejecucion["decision_run_id"]
        and territorial["run_id"] == corrida["run_id"]
        and territorial["territorios_evaluados"] > 0
        and territorial["territorios_publicados"] == territorial["territorios_evaluados"],
        f"reglas {territorial['version_reglas']}: evaluados "
        f"{territorial['territorios_evaluados']} = publicados "
        f"{territorial['territorios_publicados']} municipios, en "
        f"{territorial['duracion_segundos']} s",
    )
    ubicacion_territorial = encabezados.get("location") or encabezados.get("Location")
    esperar(
        ubicacion_territorial == f"/territoriales/{territorial['territorial_run_id']}",
        f"Location: {ubicacion_territorial}",
    )

    estado, _, consultada = pedir("GET", ubicacion_territorial)
    mismos = (
        "territorial_run_id",
        "decision_run_id",
        "run_id",
        "version_reglas",
        "estado",
        "territorios_evaluados",
        "territorios_publicados",
    )
    esperar(
        estado == 200 and all(consultada.get(campo) == territorial[campo] for campo in mismos),
        f"GET /territoriales/{{territorial_run_id}}: {estado}, la misma ejecucion",
    )

    estado, _, historial = pedir("GET", ruta_territoriales)
    en_el_historial = [e["territorial_run_id"] for e in historial.get("elementos", [])]
    esperar(
        estado == 200
        and historial["total"] >= 1
        and territorial["territorial_run_id"] in en_el_historial,
        f"GET /decisiones/{{decision_run_id}}/territoriales: {estado}, "
        f"{historial.get('total')} ejecucion(es), entre ellas la nueva",
    )

    estado, _, municipios = pedir("GET", f"{ubicacion_territorial}/municipios?por_pagina=5")
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

    # Unas decisiones se organizan con exito una sola vez por version de las reglas. Igual que en
    # el Decision Engine, el 409 no dice cual ejecucion fue: eso lo dice el historial.
    estado, encabezados, error = pedir("POST", ruta_territoriales, timeout=ESPERA_MAXIMA)
    esperar(
        estado == 409
        and error["codigo"] == "TERRITORIAL_YA_GENERADO"
        and error["run_id"] == corrida["run_id"]
        and not (encabezados.get("location") or encabezados.get("Location")),
        f"POST /decisiones/{{decision_run_id}}/territoriales otra vez: {estado} "
        f"{error.get('codigo')}, sin Location",
    )

    # El Motor de Ruteo sobre los municipios recien organizados. Tambien es sincrono: responde
    # cuando ya trazo la ruta de cada municipio con trabajo de campo.
    ruta_ruteos = f"/territoriales/{territorial['territorial_run_id']}/ruteos"
    estado, encabezados, ruteo = pedir("POST", ruta_ruteos, timeout=ESPERA_MAXIMA)
    esperar(
        estado == 201 and ruteo["estado"] == "EXITOSA",
        f"POST /territoriales/{{territorial_run_id}}/ruteos: {estado} "
        f"{ruteo.get('estado', ruteo.get('codigo'))}: "
        f"{ruteo.get('detalle', ruteo.get('mensaje'))}",
    )
    esperar(
        ruteo["version_reglas"] == "ruteo/v1"
        and ruteo["territorial_run_id"] == territorial["territorial_run_id"]
        and ruteo["decision_run_id"] == ejecucion["decision_run_id"]
        and ruteo["run_id"] == corrida["run_id"]
        and ruteo["rutas_evaluadas"] > 0
        and ruteo["rutas_publicadas"] == ruteo["rutas_evaluadas"]
        and ruteo["paradas_evaluadas"] > 0
        and ruteo["paradas_publicadas"] == ruteo["paradas_evaluadas"],
        f"reglas {ruteo['version_reglas']}: {ruteo['rutas_publicadas']} rutas = evaluadas "
        f"{ruteo['rutas_evaluadas']}, {ruteo['paradas_publicadas']} paradas = evaluadas "
        f"{ruteo['paradas_evaluadas']}, en {ruteo['duracion_segundos']} s",
    )
    ubicacion_ruteo = encabezados.get("location") or encabezados.get("Location")
    esperar(
        ubicacion_ruteo == f"/ruteos/{ruteo['ruteo_run_id']}",
        f"Location: {ubicacion_ruteo}",
    )

    estado, _, consultada = pedir("GET", ubicacion_ruteo)
    mismos = (
        "ruteo_run_id",
        "territorial_run_id",
        "decision_run_id",
        "run_id",
        "version_reglas",
        "estado",
        "rutas_evaluadas",
        "rutas_publicadas",
        "paradas_evaluadas",
        "paradas_publicadas",
    )
    esperar(
        estado == 200 and all(consultada.get(campo) == ruteo[campo] for campo in mismos),
        f"GET /ruteos/{{ruteo_run_id}}: {estado}, la misma ejecucion",
    )

    estado, _, historial = pedir("GET", ruta_ruteos)
    en_el_historial = [e["ruteo_run_id"] for e in historial.get("elementos", [])]
    esperar(
        estado == 200 and historial["total"] >= 1 and ruteo["ruteo_run_id"] in en_el_historial,
        f"GET /territoriales/{{territorial_run_id}}/ruteos: {estado}, {historial.get('total')} "
        "ejecucion(es), entre ellas la nueva",
    )

    estado, _, rutas = pedir("GET", f"{ubicacion_ruteo}/rutas?por_pagina=5")
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
        "GET", f"{ubicacion_ruteo}/rutas/{primera['clave_territorio']}/paradas?por_pagina=5"
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

    # Una ejecucion territorial se rutea con exito una sola vez por version de las reglas. Igual
    # que en los otros motores, el 409 no dice cual ejecucion fue: eso lo dice el historial.
    estado, encabezados, error = pedir("POST", ruta_ruteos, timeout=ESPERA_MAXIMA)
    esperar(
        estado == 409
        and error["codigo"] == "RUTEO_YA_GENERADO"
        and error["run_id"] == corrida["run_id"]
        and not (encabezados.get("location") or encabezados.get("Location")),
        f"POST /territoriales/{{territorial_run_id}}/ruteos otra vez: {estado} "
        f"{error.get('codigo')}, sin Location",
    )

    estado, _, openapi = pedir("GET", "/openapi.json", con_clave=False)
    documentadas = openapi.get("paths", {})
    esperar(
        estado == 200
        and "/territoriales/{territorial_run_id}/municipios" in documentadas
        and "/ruteos/{ruteo_run_id}/rutas/{clave_territorio}/paradas" in documentadas,
        f"GET /openapi.json {estado}: version {openapi.get('info', {}).get('version')}, con el "
        "Motor Territorial y el Motor de Ruteo",
    )
    print("Todo en orden.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(Path(sys.argv[1]))
