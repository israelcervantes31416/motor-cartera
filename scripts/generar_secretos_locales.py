"""Genera los secretos EFIMEROS del compose de produccion local (docker-compose.production.yml).

Nada de lo que escribe es un secreto real ni sirve fuera de esta maquina: una CA de prueba recien
creada (su llave privada no se guarda: firma los dos certificados y se descarta), certificados TLS
de 30 dias para el edge (motor-cartera.localhost, localhost, 127.0.0.1) y para Dex (dex), y valores
al azar para cada credencial. El CI lo corre en cada ejecucion; en una laptop, una vez.

    uv run --extra dev python scripts/generar_secretos_locales.py secretos
    uv run --extra dev python scripts/generar_secretos_locales.py secretos --reemplazar

Los archivos quedan con modo 0640 (0600 los que solo usa quien corre las pruebas): no los lee
cualquiera. En Linux, quien corre el compose les da el grupo MC_SECRETOS_GID (10999 por omision),
que cada servicio tiene como grupo adicional. En Windows los permisos de un archivo montado no se
controlan. El directorio esta en .gitignore y nunca se sube; este script no imprime ningun secreto.

Que genera, archivo por archivo:

    ca_certificado                      la CA efimera (publica: con ella se verifica el TLS)
    edge_tls_certificado, edge_tls_llave
    dex_tls_certificado, dex_tls_llave
    postgres_superusuario               la contrasena del superusuario de PostgreSQL
    db_migraciones, db_api, db_worker, db_respaldo     la contrasena de cada rol
    dsn_migraciones, dsn_api, dsn_worker, dsn_respaldo la URL de la base de cada servicio
    api_claves                          las claves de servicio por su SHA-256, con sus permisos
    claves_humo                         esas claves en claro, para la prueba de humo interna
    gateway_secretos                    el secreto con que el edge se presenta ante la API
    auditoria_clave                     la clave de los seudonimos de auditoria
    oauth2_proxy_cfg                    el secreto del cliente, el de las cookies y el de Valkey
    valkey_conf                         la configuracion de Valkey, con su usuario y contrasena
    dex_entorno                         los secretos de los clientes de Dex y cada bcrypt
    grafana_admin                       la contrasena del administrador de Grafana
    usuarios.json                       los usuarios de prueba de Dex con su contrasena (0600)
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import ipaddress
import json
import os
import secrets
import sys
from pathlib import Path

USUARIOS = ("lector", "auditor", "operador", "administrador", "sinpermisos")
ROLES_DE_BASE = ("migraciones", "api", "worker", "respaldo")
EDGE_NOMBRES = ("motor-cartera.localhost", "localhost")
EDGE_IPS = ("127.0.0.1",)
DIAS = 30


def _al_azar(bytes_: int = 32) -> str:
    """Un valor al azar, seguro para URL, TOML y comillas simples de sh."""
    return secrets.token_urlsafe(bytes_)


def _escribir(destino: Path, nombre: str, contenido: str | bytes, modo: int = 0o640) -> None:
    ruta = destino / nombre
    datos = contenido.encode() if isinstance(contenido, str) else contenido
    ruta.write_bytes(datos)
    os.chmod(ruta, modo)


def _certificados(destino: Path) -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    ahora = datetime.datetime.now(datetime.UTC)
    llave_ca = ec.generate_private_key(ec.SECP256R1())
    nombre_ca = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "motor-cartera CA efimera (solo pruebas)")]
    )
    ca = (
        x509.CertificateBuilder()
        .subject_name(nombre_ca)
        .issuer_name(nombre_ca)
        .public_key(llave_ca.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora - datetime.timedelta(minutes=5))
        .not_valid_after(ahora + datetime.timedelta(days=DIAS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(llave_ca.public_key()), False)
        .sign(llave_ca, hashes.SHA256())
    )
    _escribir(destino, "ca_certificado", ca.public_bytes(serialization.Encoding.PEM), 0o644)

    def emitir(prefijo: str, nombres: tuple[str, ...], ips: tuple[str, ...] = ()) -> None:
        llave = ec.generate_private_key(ec.SECP256R1())
        alternativos = [x509.DNSName(n) for n in nombres] + [
            x509.IPAddress(ipaddress.ip_address(ip)) for ip in ips
        ]
        certificado = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, nombres[0])]))
            .issuer_name(nombre_ca)
            .public_key(llave.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(ahora - datetime.timedelta(minutes=5))
            .not_valid_after(ahora + datetime.timedelta(days=DIAS))
            .add_extension(x509.SubjectAlternativeName(alternativos), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=True,
                    key_cert_sign=False,
                    crl_sign=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), False)
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(llave_ca.public_key()), False
            )
            .sign(llave_ca, hashes.SHA256())
        )
        _escribir(
            destino,
            f"{prefijo}_tls_certificado",
            certificado.public_bytes(serialization.Encoding.PEM)
            + ca.public_bytes(serialization.Encoding.PEM),
        )
        _escribir(
            destino,
            f"{prefijo}_tls_llave",
            llave.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
        )

    emitir("edge", EDGE_NOMBRES, EDGE_IPS)
    emitir("dex", ("dex",))
    # La llave de la CA se descarta aqui: nadie puede emitir otro certificado con ella.


def _base(destino: Path) -> None:
    _escribir(destino, "postgres_superusuario", _al_azar())
    for rol in ROLES_DE_BASE:
        contrasena = _al_azar()
        _escribir(destino, f"db_{rol}", contrasena)
        esquema = "postgresql" if rol == "respaldo" else "postgresql+psycopg"
        _escribir(destino, f"dsn_{rol}", f"{esquema}://mc_{rol}:{contrasena}@postgres:5432/cartera")


def _claves(destino: Path) -> None:
    claves = {"ops": _al_azar(), "lectura": _al_azar()}
    permisos = {"ops": "ADMIN", "lectura": "VIEWER"}
    lineas = [
        "# Las claves de servicio del compose de produccion local, por su SHA-256 (efimeras).",
        *(
            f"humo-{nombre} {hashlib.sha256(clave.encode()).hexdigest()} {permisos[nombre]}"
            for nombre, clave in claves.items()
        ),
    ]
    _escribir(destino, "api_claves", "\n".join(lineas) + "\n")
    _escribir(destino, "claves_humo", json.dumps(claves) + "\n")
    _escribir(destino, "gateway_secretos", _al_azar(36) + "\n")
    _escribir(destino, "auditoria_clave", secrets.token_hex(32) + "\n")


def _identidad(destino: Path) -> None:
    import bcrypt

    cliente_web = _al_azar()
    cliente_api = _al_azar()
    valkey = _al_azar()
    _escribir(
        destino,
        "oauth2_proxy_cfg",
        f'client_secret = "{cliente_web}"\n'
        f'cookie_secret = "{secrets.token_hex(16)}"\n'
        f'redis_connection_url = "redis://oauth2proxy:{valkey}@valkey:6379/0"\n',
    )
    _escribir(
        destino,
        "valkey_conf",
        "port 6379\n"
        "bind 0.0.0.0\n"
        "protected-mode yes\n"
        'save ""\n'
        "appendonly no\n"
        "maxmemory 48mb\n"
        "maxmemory-policy volatile-lru\n"
        "user default off\n"
        f"user oauth2proxy on >{valkey} ~* &* +@all -@dangerous\n",
    )
    usuarios = {}
    entorno = [
        f"export MC_DEX_CLIENTE_WEB_SECRETO='{cliente_web}'",
        f"export MC_DEX_CLIENTE_API_SECRETO='{cliente_api}'",
    ]
    for usuario in USUARIOS:
        contrasena = _al_azar(18)
        usuarios[usuario] = {"correo": f"{usuario}@motor-cartera.test", "contrasena": contrasena}
        hash_ = bcrypt.hashpw(contrasena.encode(), bcrypt.gensalt(rounds=10)).decode()
        entorno.append(f"export MC_DEX_HASH_{usuario.upper()}='{hash_}'")
    _escribir(destino, "dex_entorno", "\n".join(entorno) + "\n")
    _escribir(destino, "usuarios.json", json.dumps(usuarios, indent=2) + "\n", 0o600)
    _escribir(destino, "grafana_admin", _al_azar() + "\n")


def main() -> int:
    argumentos = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    argumentos.add_argument("destino", type=Path, nargs="?", default=Path("secretos"))
    argumentos.add_argument(
        "--reemplazar",
        action="store_true",
        help="Reemplaza los secretos de un directorio existente.",
    )
    leidos = argumentos.parse_args()
    destino: Path = leidos.destino
    if destino.exists() and any(destino.iterdir()) and not leidos.reemplazar:
        print(
            f"{destino} ya tiene secretos: usa --reemplazar para generarlos de nuevo (los "
            "servicios que ya los usan deben recrearse).",
            file=sys.stderr,
        )
        return 1
    destino.mkdir(parents=True, exist_ok=True)
    os.chmod(destino, 0o750)
    _certificados(destino)
    _base(destino)
    _claves(destino)
    _identidad(destino)
    archivos = sorted(p.name for p in destino.iterdir())
    print(f"{len(archivos)} archivos en {destino}: {', '.join(archivos)}")
    print("Efimeros: no sirven fuera de esta maquina. No se imprimio ningun valor.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
