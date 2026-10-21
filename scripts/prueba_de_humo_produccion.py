"""Prueba de humo del despliegue de produccion local (docker-compose.production.yml).

Complementa a scripts/prueba_de_humo.py, que recorre v0.1-v0.17 contra el compose de desarrollo, sin
tocarlo: esta revisa lo que v0.18 agrega, en tres lugares.

    # Desde el host, como un navegador, por el edge (TLS, sesion, permisos, cabeceras, limites).
    python scripts/prueba_de_humo_produccion.py --edge https://motor-cartera.localhost:8443 \\
        --ca secretos/ca_certificado --usuarios secretos/usuarios.json --claves secretos/claves_humo

    # Desde adentro de la red (el servicio `herramientas` del compose): la API sin el edge, el
    # worker, OSRM, oauth2-proxy, Dex, Valkey y la observabilidad.
    docker compose -f docker-compose.production.yml --profile herramientas run --rm herramientas

    # Desde el host, con el CLI de Docker: usuarios, capacidades, solo lectura, limites, redes,
    # puertos publicados y permisos de los secretos de cada contenedor.
    python scripts/prueba_de_humo_produccion.py --contenedores

Solo usa la biblioteca estandar (y el CLI de docker en --contenedores). Cada comprobacion imprime
`ok` o `MAL`; termina con codigo 1 en la primera que falla. Las que no se pueden verificar desde un
cliente dado lo dicen (`--`) y no cuentan como verificadas.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import html
import http.client
import http.cookiejar
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import warnings
from pathlib import Path

COOKIE = "__Host-mc_sesion"
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
CONTEO = {"ok": 0, "no_verificables": 0}


def esperar(condicion: bool, descripcion: str) -> None:
    print(f"  {'ok ' if condicion else 'MAL'} {descripcion}", flush=True)
    if not condicion:
        sys.exit(1)
    CONTEO["ok"] += 1


def no_verificable(descripcion: str) -> None:
    print(f"  --  {descripcion}", flush=True)
    CONTEO["no_verificables"] += 1


def seccion(titulo: str) -> None:
    print(titulo, flush=True)


def encabezado(encabezados, nombre: str) -> str:
    for llave, valor in encabezados.items():
        if llave.lower() == nombre.lower():
            return valor
    return ""


# --- el edge, como un navegador -------------------------------------------------------------------


def _resolver_localhost() -> None:
    """*.localhost es esta maquina (RFC 6761): los navegadores lo resuelven solos; urllib no."""
    original = socket.getaddrinfo

    def getaddrinfo(host, *argumentos, **opciones):
        if isinstance(host, str) and host.endswith(".localhost"):
            host = "127.0.0.1"
        return original(host, *argumentos, **opciones)

    socket.getaddrinfo = getaddrinfo


class _SinRedireccion(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *argumentos, **opciones):
        return None


class Navegador:
    """Un cliente con su propio frasco de cookies, que confia solo en la CA del compose."""

    def __init__(self, base: str, contexto: ssl.SSLContext, seguir: bool = True) -> None:
        self.base = base.rstrip("/")
        self.frasco = http.cookiejar.CookieJar()
        manejadores = [
            urllib.request.HTTPSHandler(context=contexto),
            urllib.request.HTTPCookieProcessor(self.frasco),
        ]
        if not seguir:
            manejadores.append(_SinRedireccion())
        self.abridor = urllib.request.build_opener(*manejadores)

    def pedir(self, ruta: str, metodo: str = "GET", *, cuerpo: bytes | None = None, extra=None):
        url = ruta if ruta.startswith("http") else self.base + ruta
        peticion = urllib.request.Request(url, data=cuerpo, method=metodo, headers=extra or {})
        try:
            with self.abridor.open(peticion, timeout=30) as respuesta:
                return (
                    respuesta.status,
                    dict(respuesta.headers),
                    respuesta.read(),
                    respuesta.geturl(),
                )
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers), error.read(), error.geturl()

    def cookie(self, nombre: str = COOKIE):
        """La cookie de sesion (no la CSRF del inicio de sesion, que oauth2-proxy nombra igual con
        un sufijo)."""
        return next(
            (c for c in self.frasco if c.name.startswith(nombre) and not c.name.endswith("_csrf")),
            None,
        )


def _json(cuerpo: bytes) -> dict:
    try:
        return json.loads(cuerpo)
    except ValueError:
        return {}


def iniciar_sesion(base: str, contexto: ssl.SSLContext, correo: str, contrasena: str) -> Navegador:
    """El flujo OIDC completo: /oauth2/start (con PKCE, state y nonce) -> Dex -> formulario -> el
    callback -> la app. Devuelve el navegador con su cookie de sesion."""
    navegador = Navegador(base, contexto)
    estado, _, cuerpo, url = navegador.pedir("/oauth2/start?rd=%2F")
    formulario = re.search(rb'<form[^>]*action="([^"]+)"', cuerpo)
    if estado != 200 or formulario is None:
        raise RuntimeError(f"no se llego al formulario de Dex ({estado}, {url})")
    accion = urllib.parse.urljoin(url, html.unescape(formulario.group(1).decode()))
    datos = urllib.parse.urlencode({"login": correo, "password": contrasena}).encode()
    estado, _, _, final = navegador.pedir(
        accion,
        "POST",
        cuerpo=datos,
        extra={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if estado != 200 or navegador.cookie() is None:
        raise RuntimeError(f"el inicio de sesion no termino en la app ({estado}, {final})")
    return navegador


def _hasta_el_callback(navegador: Navegador, correo: str, contrasena: str) -> str:
    """El flujo de inicio de sesion paso por paso, sin seguir redirecciones solo: devuelve la URL
    del callback que Dex manda al navegador, sin pedirla."""
    estado, encabezados, _, _ = navegador.pedir("/oauth2/start?rd=%2F")
    url = encabezado(encabezados, "location")
    datos = None
    for _ in range(10):
        metodo = "POST" if datos else "GET"
        extra = {"Content-Type": "application/x-www-form-urlencoded"} if datos else {}
        estado, encabezados, cuerpo, _ = navegador.pedir(url, metodo, cuerpo=datos, extra=extra)
        datos = None
        if estado in (301, 302, 303):
            url = urllib.parse.urljoin(url, encabezado(encabezados, "location"))
            if "/oauth2/callback?" in url:
                return url
            continue
        formulario = re.search(rb'<form[^>]*action="([^"]+)"', cuerpo)
        if estado != 200 or formulario is None:
            break
        url = urllib.parse.urljoin(url, html.unescape(formulario.group(1).decode()))
        datos = urllib.parse.urlencode({"login": correo, "password": contrasena}).encode()
    return url


def revisar_csp(valor: str) -> None:
    directivas = {
        partes[0]: partes[1:]
        for partes in (d.strip().split() for d in valor.split(";") if d.strip())
    }
    esperar(directivas.get("default-src") == ["'self'"], "CSP: default-src 'self'")
    esperar(directivas.get("object-src") == ["'none'"], "CSP: object-src 'none'")
    esperar(directivas.get("base-uri") == ["'none'"], "CSP: base-uri 'none'")
    esperar(directivas.get("frame-ancestors") == ["'none'"], "CSP: frame-ancestors 'none'")
    esperar(directivas.get("script-src") == ["'self'"], "CSP: script-src 'self', sin inline")
    fuentes = [f for valores in directivas.values() for f in valores]
    esperar("'unsafe-eval'" not in fuentes, "CSP: sin unsafe-eval")
    esperar("'unsafe-inline'" not in fuentes, "CSP: sin unsafe-inline")
    esperar(not any(f == "*" or f.startswith("*.") for f in fuentes), "CSP: ningun comodin")


def el_edge(base: str, ca: Path, usuarios: Path, claves: Path | None) -> None:
    _resolver_localhost()
    contexto = ssl.create_default_context(cafile=str(ca))
    # El piso explicito, aunque el contexto por omision ya lo tenga: el cliente de esta prueba no
    # acepta menos de TLS 1.2. Solo `vieja`, abajo, ofrece TLS 1.1, para ver que el edge lo niega.
    contexto.minimum_version = ssl.TLSVersion.TLSv1_2
    cuentas = json.loads(usuarios.read_text(encoding="utf-8"))
    anfitrion = urllib.parse.urlsplit(base).hostname or ""
    puerto = urllib.parse.urlsplit(base).port or 443

    seccion(f"El edge en {base}")
    seccion(" TLS")
    with socket.create_connection((anfitrion, puerto), timeout=10) as crudo:
        with contexto.wrap_socket(crudo, server_hostname=anfitrion) as tls:
            version = tls.version()
    esperar(
        version in ("TLSv1.2", "TLSv1.3"),
        f"el certificado se verifica con la CA del compose ({version})",
    )
    warnings.filterwarnings("ignore", category=DeprecationWarning, message=".*TLSv1_1.*")
    vieja = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    vieja.load_verify_locations(cafile=str(ca))
    try:
        vieja.minimum_version = ssl.TLSVersion.TLSv1_1
        vieja.maximum_version = ssl.TLSVersion.TLSv1_1
        vieja.set_ciphers("DEFAULT:@SECLEVEL=0")
        with socket.create_connection((anfitrion, puerto), timeout=10) as crudo:
            try:
                with vieja.wrap_socket(crudo, server_hostname=anfitrion):
                    aceptada = True
            except ssl.SSLError:
                aceptada = False
        esperar(not aceptada, "TLS 1.1 se rechaza")
    except (ValueError, ssl.SSLError):
        no_verificable("TLS 1.1: este cliente no lo puede ofrecer")
    redireccion = Navegador(
        f"http://{anfitrion}:{os.environ.get('MC_EDGE_PUERTO_HTTP', '8081')}",
        contexto,
        seguir=False,
    )
    estado, encabezados, _, _ = redireccion.pedir("/resumen?corte=x")
    esperar(
        estado == 301
        and encabezado(encabezados, "location").startswith(f"https://{anfitrion}:{puerto}/resumen"),
        "http redirige a https con la misma ruta",
    )

    seccion(" La SPA, sus cabeceras y su CSP")
    anonimo = Navegador(base, contexto)
    estado, encabezados, cuerpo, _ = anonimo.pedir("/")
    esperar(estado == 200 and b'id="raiz"' in cuerpo, "GET /: la SPA, sin sesion (no lleva datos)")
    revisar_csp(encabezado(encabezados, "content-security-policy"))
    esperar(encabezado(encabezados, "x-content-type-options") == "nosniff", "nosniff")
    esperar(
        encabezado(encabezados, "referrer-policy") == "same-origin",
        "Referrer-Policy same-origin",
    )
    esperar(encabezado(encabezados, "x-frame-options") == "DENY", "X-Frame-Options DENY")
    esperar("camera=()" in encabezado(encabezados, "permissions-policy"), "Permissions-Policy")
    esperar(
        encabezado(encabezados, "cross-origin-opener-policy") == "same-origin", "COOP same-origin"
    )
    hsts = encabezado(encabezados, "strict-transport-security")
    edad = int(re.search(r"max-age=(\d+)", hsts).group(1)) if "max-age" in hsts else 0
    esperar(edad >= 31_536_000, f"HSTS con TLS: {hsts}")
    esperar(
        not re.search(r"\d", encabezado(encabezados, "server")), "el servidor no anuncia su version"
    )
    esperar(
        re.fullmatch(r"[0-9a-f]{32}", encabezado(encabezados, "x-request-id")) is not None,
        "X-Request-ID del edge",
    )
    estado, encabezados, _, _ = anonimo.pedir(
        "/", extra={"X-Request-ID": "inventado-por-el-cliente"}
    )
    esperar(
        encabezado(encabezados, "x-request-id") != "inventado-por-el-cliente",
        "un X-Request-ID del cliente se reemplaza",
    )
    recurso = re.search(rb'src="(/assets/[^"]+\.js)"', cuerpo)
    estado, encabezados, _, _ = anonimo.pedir(recurso.group(1).decode())
    esperar(
        estado == 200 and "immutable" in encabezado(encabezados, "cache-control"),
        "los recursos con hash: immutable",
    )

    seccion(" Sin sesion")
    estado, _, cuerpo, _ = anonimo.pedir("/api/app/v1/sesion")
    esperar(
        estado == 401 and _json(cuerpo).get("codigo") == "SESION_REQUERIDA",
        "GET /api/...: 401 SESION_REQUERIDA",
    )
    for ruta in ("/oauth2/auth", "/oauth2/userinfo", "/oauth2/static/x"):
        estado, _, cuerpo, _ = anonimo.pedir(ruta)
        esperar(estado == 404, f"GET {ruta}: no se expone")
    for ruta in ("/api/docs", "/api/openapi.json", "/api/health/live", "/api/corridas"):
        estado, _, cuerpo, _ = anonimo.pedir(ruta)
        esperar(
            estado == 404 and _json(cuerpo).get("codigo") == "RUTA_NO_EXPUESTA",
            f"GET {ruta}: 404 RUTA_NO_EXPUESTA",
        )
    if claves is not None:
        ops = json.loads(claves.read_text(encoding="utf-8"))["ops"]
        estado, _, cuerpo, _ = anonimo.pedir("/api/app/v1/sesion", extra={"X-API-Key": ops})
        esperar(estado == 401, "una clave de servicio no sirve por el edge (el edge la borra)")
    estado, _, _, _ = anonimo.pedir("/api/app/v1/sesion", extra={"Cookie": f"{COOKIE}=inventada"})
    esperar(estado == 401, "una cookie de sesion inventada no autentica (fijacion de sesion)")
    callback = Navegador(base, contexto, seguir=False)
    estado, encabezados, _, _ = callback.pedir("/oauth2/callback?code=inventado&state=inventado")
    esperar(
        estado in (400, 403, 500) and callback.cookie() is None,
        f"un callback con state inventado no abre sesion ({estado})",
    )

    seccion(" El inicio de sesion (OIDC con PKCE, por oauth2-proxy y Dex)")
    lector = iniciar_sesion(
        base, contexto, cuentas["lector"]["correo"], cuentas["lector"]["contrasena"]
    )
    galleta = lector.cookie()
    esperar(galleta is not None, f"la sesion queda en la cookie {COOKIE}")
    resto = {k.lower(): v for k, v in getattr(galleta, "_rest", {}).items()}
    esperar(
        galleta.secure and "httponly" in resto,
        "la cookie es Secure y HttpOnly (el JavaScript no la lee)",
    )
    esperar(str(resto.get("samesite", "")).lower() == "lax", "la cookie es SameSite=Lax")
    esperar(
        galleta.path == "/" and not galleta.domain_specified,
        "la cookie es __Host-: Path=/ y sin Domain",
    )
    esperar(
        len(galleta.value) < 1024, "la cookie solo lleva un identificador (sesion en el servidor)"
    )
    estado, _, cuerpo, _ = lector.pedir("/api/app/v1/sesion")
    sesion = _json(cuerpo)
    esperar(
        estado == 200 and sesion.get("tipo") == "USUARIO",
        "con sesion, la app sabe quien es: USUARIO",
    )
    esperar(sesion.get("permisos") == ["mc.read"], "el lector solo lee: mc.read")
    esperar(
        sesion.get("actor", "").startswith("usr:") and "@" not in json.dumps(sesion),
        "su actor es un seudonimo, sin correo",
    )
    estado, _, cuerpo, _ = lector.pedir("/api/app/v1/contextos/opciones?por_pagina=1")
    esperar(estado == 200, "el lector lee una ruta de la app")
    estado, _, cuerpo, _ = lector.pedir("/api/app/v1/linaje")
    esperar(
        estado == 403 and _json(cuerpo).get("codigo") == "PERMISO_INSUFICIENTE",
        "el lector no ve la auditoria: 403",
    )
    suplantado = {
        "X-MC-Subject": "admin",
        "X-MC-Roles": "ADMIN",
        "X-Forwarded-User": "admin",
        "X-Auth-Request-Groups": "mc-administradores",
        "X-MC-Gateway": "inventado-por-el-cliente-0123456789abcd",
        "Authorization": "Bearer inventado",
    }
    estado, _, cuerpo, _ = lector.pedir("/api/app/v1/sesion", extra=suplantado)
    esperar(
        estado == 200 and _json(cuerpo).get("permisos") == ["mc.read"],
        "las cabeceras de identidad del cliente no elevan a nadie",
    )
    for metodo in ("POST", "PUT", "PATCH", "DELETE"):
        estado, _, cuerpo, _ = lector.pedir("/api/decisiones-v2", metodo, cuerpo=b"{}")
        esperar(estado == 405, f"{metodo} por el edge: 405 (la web solo lee)")

    seccion(" Cada rol")
    auditor = iniciar_sesion(
        base, contexto, cuentas["auditor"]["correo"], cuentas["auditor"]["contrasena"]
    )
    estado, _, cuerpo, _ = auditor.pedir("/api/app/v1/sesion")
    esperar(
        _json(cuerpo).get("permisos") == ["mc.audit", "mc.read"], "el auditor: mc.read y mc.audit"
    )
    estado, _, cuerpo, _ = auditor.pedir("/api/app/v1/linaje")
    esperar(estado == 422, "el auditor alcanza la auditoria (le falta el contexto: 422)")
    operador = iniciar_sesion(
        base, contexto, cuentas["operador"]["correo"], cuentas["operador"]["contrasena"]
    )
    estado, _, cuerpo, _ = operador.pedir("/api/app/v1/sesion")
    esperar(
        _json(cuerpo).get("permisos") == ["mc.execute", "mc.read"],
        "el operador: mc.read y mc.execute",
    )
    estado, _, _, _ = operador.pedir("/api/decisiones-v2", "POST", cuerpo=b"{}")
    esperar(estado == 405, "ni el operador escribe por la web: 405")
    sin = iniciar_sesion(
        base, contexto, cuentas["sinpermisos"]["correo"], cuentas["sinpermisos"]["contrasena"]
    )
    estado, _, cuerpo, _ = sin.pedir("/api/app/v1/sesion")
    esperar(
        estado == 403 and _json(cuerpo).get("codigo") == "PERMISO_INSUFICIENTE",
        "sin grupos: sesion valida, 403 sin mc.read",
    )

    seccion(" Un callback no se usa dos veces")
    paso = Navegador(base, contexto, seguir=False)
    callback_url = _hasta_el_callback(
        paso, cuentas["lector"]["correo"], cuentas["lector"]["contrasena"]
    )
    esperar("/oauth2/callback?" in callback_url, "Dex devuelve un callback con su code y su state")
    estado, _, _, _ = paso.pedir(callback_url)
    esperar(paso.cookie() is not None, "el callback abre la sesion una vez, con su cookie CSRF")
    otro = Navegador(base, contexto, seguir=False)
    estado, _, _, _ = otro.pedir(callback_url)
    esperar(
        estado in (400, 403, 500) and otro.cookie() is None,
        f"el mismo callback desde otro navegador (sin su state) no abre sesion ({estado})",
    )

    seccion(" Limites")

    def leer_sesion(_: int) -> int:
        return lector.pedir("/api/app/v1/sesion")[0]

    with concurrent.futures.ThreadPoolExecutor(max_workers=40) as hilos:
        estados = list(hilos.map(leer_sesion, range(200)))
    limitadas = estados.count(429)
    esperar(
        limitadas > 0 and estados.count(200) > 0,
        f"una rafaga de 200 lecturas: {estados.count(200)} 200 y {limitadas} 429",
    )
    estado, encabezados, cuerpo, _ = (0, {}, b"", "")
    for _ in range(50):
        estado, encabezados, cuerpo, _ = lector.pedir("/api/app/v1/sesion")
        if estado == 429:
            break
    if estado == 429:
        esperar(encabezado(encabezados, "retry-after") == "1", "un 429 dice Retry-After")
        esperar(
            _json(cuerpo).get("codigo") == "DEMASIADAS_PETICIONES", "un 429 es JSON con su codigo"
        )
    time.sleep(3)
    esperar(
        lector.pedir("/api/app/v1/sesion")[0] == 200,
        "despues de la rafaga, la lectura normal vuelve: 200",
    )
    estado, _, cuerpo, _ = anonimo.pedir(
        "/dex/auth/local/login",
        "POST",
        cuerpo=b"x" * 102_400,
        extra={"Content-Type": "application/x-www-form-urlencoded"},
    )
    esperar(estado == 413, f"un cuerpo de 100 KiB: 413 ({estado})")
    estado, _, _, _ = anonimo.pedir("/", extra={"X-Relleno": "x" * 40_000})
    esperar(estado in (400, 431, 494), f"una cabecera de 40 KB se rechaza ({estado})")
    estado, _, _, _ = anonimo.pedir("/", "DELETE")
    esperar(estado in (403, 405), f"DELETE /: no ({estado})")

    seccion(" Cerrar sesion")
    estado, _, _, _ = lector.pedir("/oauth2/sign_out")
    esperar(estado == 405, "cerrar sesion por GET: 405 (CSRF)")
    estado, _, cuerpo, _ = lector.pedir(
        "/oauth2/sign_out", "POST", cuerpo=b"", extra={"Origin": "https://otro-sitio.example"}
    )
    esperar(
        estado == 403 and _json(cuerpo).get("codigo") == "ORIGEN_NO_PERMITIDO",
        "cerrar sesion desde otro origen: 403",
    )
    # `Origin: null` es lo que manda un documento con Referrer-Policy no-referrer (o uno aislado):
    # no prueba el origen y se niega. Por eso la app pide same-origin (su cabecera y su meta).
    estado, _, cuerpo, _ = lector.pedir(
        "/oauth2/sign_out", "POST", cuerpo=b"", extra={"Origin": "null"}
    )
    esperar(estado == 403, "cerrar sesion con Origin: null: 403")
    _, _, indice, _ = anonimo.pedir("/")
    esperar(
        b'<meta name="referrer" content="same-origin"' in indice,
        "la app pide Referrer-Policy same-origin: el navegador manda su Origin al cerrar sesion",
    )
    copiada = lector.cookie().value
    estado, _, _, _ = lector.pedir("/oauth2/sign_out", "POST", cuerpo=b"", extra={"Origin": base})
    esperar(lector.pedir("/api/app/v1/sesion")[0] == 401, "despues de cerrar sesion: 401")
    estado, _, _, _ = anonimo.pedir("/api/app/v1/sesion", extra={"Cookie": f"{COOKIE}={copiada}"})
    esperar(
        estado == 401,
        "la cookie copiada antes de cerrar sesion ya no vale (la sesion se borro del servidor)",
    )

    seccion(" Lo unico publicado es el edge")
    for puerto_interno, servicio in (
        (5432, "PostgreSQL"),
        (8000, "la API"),
        (9464, "las metricas de la API"),
        (9465, "el worker"),
        (9090, "Prometheus"),
        (3000, "Grafana"),
        (16686, "Jaeger"),
        (5554, "Dex"),
        (5556, "Dex"),
        (4180, "oauth2-proxy"),
        (6379, "Valkey"),
        (5000, "OSRM"),
    ):
        with socket.socket() as prueba:
            prueba.settimeout(1)
            abierto = prueba.connect_ex(("127.0.0.1", puerto_interno)) == 0
        esperar(not abierto, f"{servicio} ({puerto_interno}) no se publica")


# --- adentro de la red ----------------------------------------------------------------------------


def _pedir(url: str, metodo: str = "GET", *, cuerpo=None, extra=None, contexto=None):
    peticion = urllib.request.Request(url, data=cuerpo, method=metodo, headers=extra or {})
    try:
        with urllib.request.urlopen(peticion, timeout=30, context=contexto) as respuesta:
            return respuesta.status, dict(respuesta.headers), respuesta.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def _subir_csv(api: str, clave: str) -> tuple[int, dict]:
    """Una cartera sintetica de dos cuentas, distinta en cada corrida (sus saldos): el mismo archivo
    dos veces es una corrida duplicada, y la API lo rechaza con razon."""
    centavos = int(time.time() * 1000) % 100_000
    contenido = (
        "cliente_unico,saldo_total,dias_atraso,producto,canal,cve_entidad,cve_municipio,"
        f"fecha_corte\nCU00009901,{3000 + centavos / 100:.2f},120,CONSUMO,CAMPO,21,114,2026-09-30\n"
        f"CU00009902,{7000 + centavos / 100:.2f},0,CONSUMO,DIGITAL,21,114,2026-09-30\n"
    ).encode()
    frontera = "frontera-de-la-prueba-de-humo"
    cuerpo = (
        (
            f"--{frontera}\r\n"
            'Content-Disposition: form-data; name="archivo"; filename="humo.csv"\r\n'
            "Content-Type: text/csv\r\n\r\n"
        ).encode()
        + contenido
        + f"\r\n--{frontera}--\r\n".encode()
    )
    estado, _, respuesta = _pedir(
        f"{api}/corridas",
        "POST",
        cuerpo=cuerpo,
        extra={"X-API-Key": clave, "Content-Type": f"multipart/form-data; boundary={frontera}"},
    )
    return estado, _json(respuesta)


def adentro() -> None:
    api = os.environ.get("MC_URL_API", "http://api:8000").rstrip("/")
    claves = json.loads(Path(os.environ["MC_HUMO_CLAVES_FILE"]).read_text(encoding="utf-8"))
    seccion(f"Adentro de la red, la API en {api}")

    seccion(" Salud")
    estado, _, cuerpo = _pedir(f"{api}/health/live")
    esperar(
        estado == 200 and _json(cuerpo) == {"estado": "vivo"},
        "API /health/live: 200, sin pedir credencial",
    )
    estado, _, cuerpo = _pedir(f"{api}/health/ready")
    esperar(
        estado == 200 and _json(cuerpo).get("estado") == "listo",
        "API /health/ready: la base y su esquema",
    )
    for ruta in ("/docs", "/openapi.json", "/redoc"):
        estado, _, _ = _pedir(f"{api}{ruta}")
        esperar(estado == 404, f"API {ruta}: no existe en produccion")

    seccion(" La API sin el edge")
    estado, _, cuerpo = _pedir(f"{api}/app/v1/sesion")
    esperar(
        estado == 401 and _json(cuerpo).get("codigo") == "CREDENCIAL_AUSENTE", "sin credencial: 401"
    )
    estado, _, cuerpo = _pedir(f"{api}/app/v1/sesion", extra={"Authorization": "Bearer x.y.z"})
    esperar(
        _json(cuerpo).get("codigo") == "GATEWAY_REQUERIDO",
        "un token sin la credencial del gateway: 401",
    )
    estado, _, cuerpo = _pedir(
        f"{api}/app/v1/sesion", extra={"X-API-Key": "clave-local-de-desarrollo"}
    )
    esperar(estado == 401, "la clave universal de desarrollo no existe en produccion")
    estado, _, cuerpo = _pedir(f"{api}/app/v1/sesion", extra={"X-API-Key": claves["lectura"]})
    esperar(
        _json(cuerpo) == {"tipo": "SERVICIO", "actor": "svc:humo-lectura", "permisos": ["mc.read"]},
        "una clave de servicio de lectura: solo mc.read",
    )
    estado, _, _ = _pedir(
        f"{api}/decisiones-v2",
        "POST",
        cuerpo=b"{}",
        extra={"X-API-Key": claves["lectura"], "Content-Type": "application/json"},
    )
    esperar(estado == 403, "la clave de lectura no ejecuta: 403")
    estado, _, _ = _pedir(
        f"{api}/decisiones-v2",
        "POST",
        cuerpo=b"{}",
        extra={"X-API-Key": claves["ops"], "Content-Type": "application/json"},
    )
    esperar(estado == 422, "la clave de operaciones si ejecuta (le falta el cuerpo: 422)")
    # Solo las cabeceras, con un Content-Length de 9 MiB: la API responde 413 sin esperar el cuerpo.
    partes = urllib.parse.urlsplit(api)
    conexion = http.client.HTTPConnection(partes.hostname, partes.port or 80, timeout=30)
    conexion.putrequest("POST", "/decisiones-v2")
    conexion.putheader("X-API-Key", claves["ops"])
    conexion.putheader("Content-Type", "application/json")
    conexion.putheader("Content-Length", str(9 * 1024 * 1024))
    conexion.endheaders()
    respuesta = conexion.getresponse()
    estado, cuerpo = respuesta.status, respuesta.read()
    conexion.close()
    esperar(
        estado == 413 and _json(cuerpo).get("codigo") == "CUERPO_DEMASIADO_GRANDE",
        "un cuerpo JSON de 9 MiB: 413 antes de leerlo",
    )

    seccion(" El worker y la base, con un trabajo de verdad")
    estado, corrida = _subir_csv(api, claves["ops"])
    esperar(estado == 201, "POST /corridas con la clave de operaciones: 201")
    flujo = {}
    limite = time.monotonic() + 180
    while time.monotonic() < limite:
        _, _, cuerpo = _pedir(
            f"{api}/corridas/{corrida['run_id']}/flujo", extra={"X-API-Key": claves["ops"]}
        )
        flujo = _json(cuerpo)
        if flujo.get("estado") != "EN_PROCESO":
            break
        time.sleep(1)
    esperar(
        flujo.get("estado") == "COMPLETADO",
        f"el worker lleva el flujo de la ingesta al ruteo: {flujo.get('estado')}",
    )

    seccion(" Metricas (puerto interno)")
    estado, _, cuerpo = _pedir("http://api:9464/metrics")
    texto = cuerpo.decode()
    esperar(
        estado == 200 and "mc_http_peticiones_total" in texto,
        "la API sirve /metrics en su puerto interno",
    )
    esperar('ruta="/corridas/{run_id}/flujo"' in texto, "las metricas van por plantilla de ruta")
    esperar(not UUID.search(texto), "ninguna metrica lleva un identificador")
    esperar(
        "mc_cola_trabajos" in texto and "mc_db_pool_tamano" in texto, "la cola y el pool de la base"
    )
    estado, _, cuerpo = _pedir("http://worker:9465/metrics")
    esperar(
        estado == 200 and "mc_worker_trabajos_cerrados_total" in cuerpo.decode(),
        "el worker sirve sus metricas",
    )
    esperar(not UUID.search(cuerpo.decode()), "ninguna metrica del worker lleva un identificador")
    estado, _, cuerpo = _pedir("http://worker:9465/health/live")
    esperar(
        estado == 200 and _json(cuerpo).get("estado") == "vivo",
        "worker /health/live: su ciclo late",
    )
    estado, _, cuerpo = _pedir("http://worker:9465/health/ready")
    salud = _json(cuerpo)
    esperar(
        estado == 200 and salud.get("cola") == "ok" and salud.get("base_de_datos") == "ok",
        "worker /health/ready: la cola se puede usar",
    )
    print(f"      ruteo del worker: {salud.get('ruteo')}")

    seccion(" OSRM")
    try:
        estado, _, cuerpo = _pedir("http://osrm:5000/nearest/v1/driving/0,0?number=1")
        osrm = _json(cuerpo)
        esperar(estado == 200 and osrm.get("code") == "Ok", "OSRM responde y tiene su red cargada")
        esperar(
            re.fullmatch(
                r"motor-cartera/red-vial/v1:[0-9a-f]{64}", str(osrm.get("data_version", ""))
            )
            is not None,
            "OSRM declara la red que sirve (su huella, en data_version)",
        )
    except (urllib.error.URLError, OSError):
        no_verificable("OSRM no esta levantado (perfil ruteo)")

    seccion(" Identidad")
    estado, _, _ = _pedir("http://oauth2-proxy:4180/ping")
    esperar(estado == 200, "oauth2-proxy /ping")
    estado, _, _ = _pedir("http://oauth2-proxy:4180/ready")
    esperar(estado == 200, "oauth2-proxy /ready (alcanza a Valkey)")
    ca = (
        ssl.create_default_context(cafile="/run/secrets/ca_certificado")
        if Path("/run/secrets/ca_certificado").exists()
        else None
    )
    if ca is not None:
        estado, _, cuerpo = _pedir(
            "https://dex:5554/dex/.well-known/openid-configuration", contexto=ca
        )
        esperar(
            _json(cuerpo).get("issuer") == "https://motor-cartera.localhost:8443/dex",
            "Dex por TLS: el emisor esperado",
        )
    else:
        no_verificable("Dex por TLS: este contenedor no monta la CA")
    with socket.create_connection(("valkey", 6379), timeout=5) as conexion:
        conexion.sendall(b"PING\r\n")
        respuesta = conexion.recv(128)
    esperar(respuesta.startswith(b"-NOAUTH"), "Valkey exige autenticacion")

    seccion(" Observabilidad")
    try:
        estado, _, _ = _pedir("http://otel-collector:13133/")
        esperar(estado == 200, "el colector de OpenTelemetry vive")
    except (urllib.error.URLError, OSError):
        no_verificable("el perfil observabilidad no esta levantado")
        return
    estado, _, cuerpo = _pedir("http://prometheus:9090/api/v1/query?query=up")
    objetivos = {
        r["metric"]["job"]: r["value"][1] for r in _json(cuerpo).get("data", {}).get("result", [])
    }
    esperar(
        objetivos.get("api") == "1" and objetivos.get("worker") == "1",
        f"Prometheus consulta la API y el worker: {objetivos}",
    )
    estado, _, cuerpo = _pedir("http://prometheus:9090/api/v1/rules")
    reglas = [
        r["name"] for g in _json(cuerpo).get("data", {}).get("groups", []) for r in g["rules"]
    ]
    esperar(
        "MC5xxElevados" in reglas and "MCColaAtrasada" in reglas,
        f"las reglas de alertamiento cargadas: {len(reglas)}",
    )
    estado, _, cuerpo = _pedir("http://alertmanager:9093/-/ready")
    esperar(estado == 200, "Alertmanager vive (receptor nulo)")
    limite = time.monotonic() + 60
    servicios = []
    while time.monotonic() < limite:
        # Jaeger v2 sirve la API v3 (la de /api/services ya no existe).
        _, _, cuerpo = _pedir("http://jaeger:16686/api/v3/services")
        servicios = _json(cuerpo).get("services") or []
        if "motor-cartera-api" in servicios:
            break
        time.sleep(3)
    esperar("motor-cartera-api" in servicios, f"Jaeger tiene trazas de la API: {servicios}")
    estado, _, cuerpo = _pedir("http://grafana:3000/api/health")
    esperar(estado == 200, "Grafana vive")


# --- los contenedores, por el CLI de Docker -------------------------------------------------------

SIN_SOLO_LECTURA = {"postgres"}
"""PostgreSQL escribe en su volumen; tambien es de solo lectura, pero se revisa aparte."""


def _docker(*argumentos: str) -> str:
    return subprocess.run(
        ["docker", *argumentos], capture_output=True, text=True, check=True
    ).stdout


def contenedores(proyecto: str) -> None:
    seccion(f"Los contenedores del proyecto {proyecto}")
    ids = _docker("ps", "-q", "--filter", f"label=com.docker.compose.project={proyecto}").split()
    esperar(len(ids) >= 7, f"{len(ids)} contenedores corriendo")
    datos = json.loads(_docker("inspect", *ids))
    publicados = []
    for c in datos:
        servicio = c["Config"]["Labels"].get("com.docker.compose.service", c["Name"])
        anfitrion = c["HostConfig"]
        usuario = c["Config"].get("User") or ""
        esperar(
            usuario not in ("", "0", "root") and not usuario.startswith(("0:", "root")),
            f"{servicio}: corre como {usuario}, no root",
        )
        esperar(not anfitrion.get("Privileged"), f"{servicio}: no es privilegiado")
        esperar("ALL" in (anfitrion.get("CapDrop") or []), f"{servicio}: cap_drop ALL")
        esperar(not anfitrion.get("CapAdd"), f"{servicio}: sin capacidades agregadas")
        esperar(
            any("no-new-privileges" in o for o in anfitrion.get("SecurityOpt") or []),
            f"{servicio}: no-new-privileges",
        )
        esperar(
            anfitrion.get("ReadonlyRootfs") is True,
            f"{servicio}: sistema de archivos de solo lectura",
        )
        esperar(
            (anfitrion.get("Memory") or 0) > 0 and (anfitrion.get("PidsLimit") or 0) > 0,
            f"{servicio}: limites de memoria y procesos",
        )
        esperar(
            anfitrion.get("LogConfig", {}).get("Type") == "local",
            f"{servicio}: logs rotados (driver local)",
        )
        montajes = [m.get("Source", "") for m in c.get("Mounts", [])]
        esperar(
            not any("docker.sock" in m for m in montajes), f"{servicio}: sin el socket de Docker"
        )
        for puerto, enlaces in (c.get("NetworkSettings", {}).get("Ports") or {}).items():
            for enlace in enlaces or []:
                publicados.append((servicio, puerto, enlace.get("HostIp")))
        if servicio not in {"oauth2-proxy"}:
            real = subprocess.run(
                ["docker", "exec", c["Id"], "id", "-u"], capture_output=True, text=True
            )
            if real.returncode == 0:
                esperar(
                    real.stdout.strip() != "0",
                    f"{servicio}: adentro, id -u = {real.stdout.strip()}",
                )
            escribir = subprocess.run(
                ["docker", "exec", c["Id"], "sh", "-c", "touch /prueba-solo-lectura"],
                capture_output=True,
                text=True,
            )
            if escribir.returncode != 127:
                esperar(
                    escribir.returncode != 0, f"{servicio}: no puede escribir fuera de sus tmpfs"
                )
    esperar(
        sorted({s for s, _, _ in publicados}) == ["edge"]
        and all(ip == "127.0.0.1" for _, _, ip in publicados),
        f"solo el edge publica puertos, y solo en 127.0.0.1: {publicados}",
    )
    if sys.platform != "win32":
        for c in datos:
            servicio = c["Config"]["Labels"].get("com.docker.compose.service", "")
            secretos = [
                m["Source"]
                for m in c.get("Mounts", [])
                if m.get("Destination", "").startswith("/run/secrets/")
            ]
            for fuente in secretos:
                modo = os.stat(fuente).st_mode & 0o777
                esperar(
                    not modo & 0o007 or fuente.endswith("ca_certificado"),
                    f"{servicio}: {Path(fuente).name} no lo lee cualquiera ({oct(modo)})",
                )
    else:
        no_verificable("los permisos de los secretos montados: Windows no los controla")


def main() -> int:
    argumentos = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    argumentos.add_argument("--edge", help="La URL del edge: https://motor-cartera.localhost:8443")
    argumentos.add_argument("--ca", type=Path, default=Path("secretos/ca_certificado"))
    argumentos.add_argument("--usuarios", type=Path, default=Path("secretos/usuarios.json"))
    argumentos.add_argument("--claves", type=Path, default=Path("secretos/claves_humo"))
    argumentos.add_argument(
        "--interna", action="store_true", help="Las revisiones de adentro de la red."
    )
    argumentos.add_argument(
        "--contenedores", action="store_true", help="Revisa los contenedores con docker inspect."
    )
    argumentos.add_argument("--proyecto", default="motor-cartera-produccion")
    leidos = argumentos.parse_args()
    if leidos.edge:
        el_edge(
            leidos.edge,
            leidos.ca,
            leidos.usuarios,
            leidos.claves if leidos.claves.exists() else None,
        )
    if leidos.interna:
        adentro()
    if leidos.contenedores:
        contenedores(leidos.proyecto)
    print(
        f"Todo en orden: {CONTEO['ok']} comprobaciones, "
        f"{CONTEO['no_verificables']} no verificables desde aqui."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
