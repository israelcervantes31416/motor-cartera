"""Las reglas que la referencia de Google Cloud (infra/gcp) no puede romper, revisadas sin
credenciales ni red: lo que `tofu validate` no sabe de politica.

    python scripts/verificar_iac.py [infra/gcp]

Lee los .tf como texto, bloque por bloque (sin depender de una biblioteca de HCL), y revisa:

- ningun rol amplio (editor, owner, administradores de IAM) y ningun miembro publico (allUsers);
- Cloud SQL solo con IP privada, TLS obligatorio, respaldos, PITR y protegida contra el borrado;
- el bucket de respaldos sin acceso publico posible, con versionado y sin force_destroy;
- Artifact Registry con etiquetas inmutables, y cada imagen fijada por su digest (las variables lo
  exigen y los tfvars de ejemplo lo cumplen);
- ningun servicio de Cloud Run abierto a todo Internet, y el worker solo interno;
- ninguna VM con IP publica, y todas con arranque seguro;
- ningun secreto en el codigo (ni versiones de Secret Manager, ni contrasenas, ni llaves);
- Workload Identity Federation atado al repositorio y al environment;
- una politica de TLS moderna (TLS 1.2 o mas) en el balanceador;
- el estado en un backend remoto (gcs) y ningun estado en el repositorio.

Termina con 1 si alguna regla no se cumple.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
ROLES_PROHIBIDOS = (
    "roles/editor",
    "roles/owner",
    "roles/iam.securityAdmin",
    "roles/resourcemanager.projectIamAdmin",
    "roles/iam.serviceAccountTokenCreator",
)


@dataclass(frozen=True)
class Bloque:
    tipo: str
    nombre: str
    texto: str
    archivo: str


def bloques(texto: str, archivo: str, clase: str = "resource") -> Iterator[Bloque]:
    """Cada `resource "tipo" "nombre" { ... }` (o variable, output), con su texto completo."""
    patron = re.compile(rf'^{clase}\s+"([^"]+)"(?:\s+"([^"]+)")?\s*\{{', re.MULTILINE)
    for encontrado in patron.finditer(texto):
        profundidad = 0
        for indice in range(encontrado.end() - 1, len(texto)):
            if texto[indice] == "{":
                profundidad += 1
            elif texto[indice] == "}":
                profundidad -= 1
                if profundidad == 0:
                    yield Bloque(
                        encontrado.group(1),
                        encontrado.group(2) or "",
                        texto[encontrado.start() : indice + 1],
                        archivo,
                    )
                    break


def sin_comentarios(texto: str) -> str:
    return "\n".join(re.sub(r"\s#.*$|^\s*#.*$", "", linea) for linea in texto.splitlines())


def tiene(bloque: Bloque, expresion: str) -> bool:
    return re.search(expresion, bloque.texto) is not None


class Revision:
    def __init__(self) -> None:
        self.problemas: list[str] = []
        self.revisadas = 0

    def exigir(self, condicion: bool, regla: str) -> None:
        self.revisadas += 1
        print(f"  {'ok ' if condicion else 'MAL'} {regla}")
        if not condicion:
            self.problemas.append(regla)


def revisar(directorio: Path) -> Revision:
    revision = Revision()
    textos = {
        ruta.name: sin_comentarios(ruta.read_text(encoding="utf-8"))
        for ruta in sorted(directorio.glob("*.tf"))
    }
    todo = "\n".join(textos.values())
    recursos = [b for archivo, texto in textos.items() for b in bloques(texto, archivo)]
    variables = [
        b for archivo, texto in textos.items() for b in bloques(texto, archivo, "variable")
    ]
    de = {}
    for recurso in recursos:
        de.setdefault(recurso.tipo, []).append(recurso)

    print(" Identidades")
    for rol in ROLES_PROHIBIDOS:
        revision.exigir(rol not in todo, f"ningun {rol}")
    revision.exigir(
        "allUsers" not in todo and "allAuthenticatedUsers" not in todo,
        "ningun miembro publico (allUsers, allAuthenticatedUsers)",
    )

    print(" Cloud SQL")
    instancias = de.get("google_sql_database_instance", [])
    revision.exigir(bool(instancias), "hay una instancia de Cloud SQL")
    for sql in instancias:
        revision.exigir(tiene(sql, r"ipv4_enabled\s*=\s*false"), f"{sql.nombre}: sin IP publica")
        revision.exigir(
            tiene(sql, r'ssl_mode\s*=\s*"ENCRYPTED_ONLY"'), f"{sql.nombre}: TLS obligatorio"
        )
        revision.exigir(
            tiene(sql, r"(?<!_)deletion_protection\s*=\s*true")
            and tiene(sql, r"deletion_protection_enabled\s*=\s*true"),
            f"{sql.nombre}: protegida contra el borrado (OpenTofu y la API)",
        )
        revision.exigir(
            tiene(sql, r"backup_configuration\s*\{[^}]*enabled\s*=\s*true")
            and tiene(sql, r"point_in_time_recovery_enabled\s*=\s*true"),
            f"{sql.nombre}: respaldos automaticos y PITR",
        )

    print(" Almacenamiento")
    buckets = de.get("google_storage_bucket", [])
    revision.exigir(bool(buckets), "hay un bucket de respaldos")
    for bucket in buckets:
        revision.exigir(
            tiene(bucket, r'public_access_prevention\s*=\s*"enforced"')
            and tiene(bucket, r"uniform_bucket_level_access\s*=\s*true"),
            f"{bucket.nombre}: sin acceso publico posible",
        )
        revision.exigir(
            tiene(bucket, r"versioning\s*\{\s*enabled\s*=\s*true"),
            f"{bucket.nombre}: con versionado",
        )
        revision.exigir(
            not tiene(bucket, r"force_destroy\s*=\s*true"), f"{bucket.nombre}: sin force_destroy"
        )
    for repositorio in de.get("google_artifact_registry_repository", []):
        revision.exigir(
            tiene(repositorio, r"immutable_tags\s*=\s*true"),
            f"{repositorio.nombre}: etiquetas inmutables",
        )

    print(" Imagenes")
    imagenes = [v for v in variables if v.tipo.startswith("imagen_")]
    revision.exigir(bool(imagenes), "las imagenes son variables")
    for variable in imagenes:
        revision.exigir(
            "@sha256:[0-9a-f]{64}$" in variable.texto,
            f"var.{variable.tipo}: exige el digest",
        )
    for tfvars in sorted((directorio / "entornos").glob("*.tfvars")):
        lineas = [
            linea
            for linea in tfvars.read_text(encoding="utf-8").splitlines()
            if linea.startswith("imagen_")
        ]
        revision.exigir(
            bool(lineas) and all(re.search(r'@sha256:[0-9a-f]{64}"$', linea) for linea in lineas),
            f"{tfvars.name}: cada imagen por su digest",
        )

    print(" Cloud Run")
    servicios = de.get("google_cloud_run_v2_service", [])
    revision.exigir(bool(servicios), "hay servicios de Cloud Run")
    for servicio in servicios:
        revision.exigir(
            not tiene(servicio, r'ingress\s*=\s*"INGRESS_TRAFFIC_ALL"')
            and tiene(servicio, r'ingress\s*=\s*"INGRESS_TRAFFIC_INTERNAL'),
            f"{servicio.nombre}: no esta abierto a todo Internet",
        )
    for servicio in servicios:
        if servicio.nombre == "worker":
            revision.exigir(
                tiene(servicio, r'ingress\s*=\s*"INGRESS_TRAFFIC_INTERNAL_ONLY"'),
                "worker: solo interno",
            )

    print(" Maquinas virtuales")
    for vm in de.get("google_compute_instance", []) + de.get(
        "google_compute_instance_template", []
    ):
        revision.exigir(not tiene(vm, r"access_config\s*\{"), f"{vm.nombre}: sin IP publica")
        revision.exigir(
            tiene(vm, r"enable_secure_boot\s*=\s*true"), f"{vm.nombre}: arranque seguro"
        )

    print(" Secretos")
    revision.exigir(
        "google_secret_manager_secret_version" not in todo and "secret_data" not in todo,
        "ninguna version de secreto en el codigo (los valores se crean fuera)",
    )
    revision.exigir(
        re.search(r"\b(password|private_key|client_secret)\s*=", todo) is None,
        "ninguna contrasena, llave o client_secret en el codigo",
    )

    print(" Despliegue, TLS y estado")
    proveedores = de.get("google_iam_workload_identity_pool_provider", [])
    revision.exigir(
        bool(proveedores)
        and all(
            tiene(p, r'attribute_condition\s*=\s*"[^"]*assertion\.repository\s*==')
            and tiene(p, r"assertion\.environment\s*==")
            for p in proveedores
        ),
        "Workload Identity Federation atado al repositorio y al environment",
    )
    politicas = de.get("google_compute_ssl_policy", [])
    revision.exigir(
        bool(politicas)
        and all(
            tiene(p, r'min_tls_version\s*=\s*"TLS_1_[23]"')
            and tiene(p, r'profile\s*=\s*"(MODERN|RESTRICTED)"')
            for p in politicas
        ),
        "politica de TLS moderna (TLS 1.2 o mas)",
    )
    revision.exigir(re.search(r'backend\s+"gcs"', todo) is not None, "estado en GCS (remoto)")
    revision.exigir(
        not list(directorio.glob("*.tfstate*")), "ningun archivo de estado en el repositorio"
    )
    return revision


def main(argv: list[str] | None = None) -> int:
    argumentos = argv if argv is not None else sys.argv[1:]
    directorio = Path(argumentos[0]) if argumentos else RAIZ / "infra" / "gcp"
    revision = revisar(directorio)
    print()
    if revision.problemas:
        print(f"{len(revision.problemas)} de {revision.revisadas} reglas no se cumplen.")
        return 1
    print(f"La referencia cumple las {revision.revisadas} reglas.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
