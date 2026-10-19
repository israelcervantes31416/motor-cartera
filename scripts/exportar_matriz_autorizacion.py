"""Escribe docs/matriz-autorizacion.md desde la matriz de la API
(src/motor_cartera/api/autorizacion.py): los roles con sus permisos, las clases y cada operacion
con su clase y el permiso que pide.

    python scripts/exportar_matriz_autorizacion.py

El documento no se edita a mano: tests/test_api_autorizacion.py exige que sea exactamente lo que
este script escribe, y la API se niega a arrancar si una ruta no esta en la matriz.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
DESTINO = RAIZ / "docs" / "matriz-autorizacion.md"
QUE_ES = {
    "PUBLIC": "Las sondas de salud: sin credencial.",
    "READ": "Leer lo publicado.",
    "AUDIT": "Leer la evidencia: rechazos, archivos fuente, observaciones, la cola, el linaje.",
    "EXECUTE": "Pedir trabajo: ingerir, ejecutar un motor, registrar un evento.",
    "CONFIGURE": "Publicar configuracion operacional.",
}


def documento() -> str:
    sys.path.insert(0, str(RAIZ / "src"))
    from motor_cartera.api.autorizacion import MATRIZ, PERMISO_DE, Clase
    from motor_cartera.seguridad.permisos import ROLES, Permiso

    lineas = [
        "# La matriz de autorización",
        "",
        "Generada por `scripts/exportar_matriz_autorizacion.py` desde `MATRIZ`",
        "(`src/motor_cartera/api/autorizacion.py`): no se edita a mano. La API se niega a",
        "arrancar si una operación no está en la matriz, o si la matriz nombra una que ya no",
        "existe, y `tests/test_api_autorizacion.py` exige que este documento sea exactamente el",
        "que el script escribe. Cómo se autentica cada credencial y de dónde salen sus permisos,",
        "en [security.md](security.md).",
        "",
        "## Los roles",
        "",
        "| Rol | Permisos |",
        "| --- | --- |",
    ]
    orden = list(Permiso)
    for rol, permisos in ROLES.items():
        nombres = ", ".join(f"`{p.value}`" for p in sorted(permisos, key=orden.index))
        lineas.append(f"| `{rol}` | {nombres} |")
    cuantas = Counter(MATRIZ.values())
    lineas += [
        "",
        "## Las clases",
        "",
        "| Clase | Permiso | Operaciones | Qué es |",
        "| --- | --- | ---: | --- |",
    ]
    for clase in Clase:
        permiso = PERMISO_DE.get(clase)
        lineas.append(
            f"| `{clase.value}` | {f'`{permiso.value}`' if permiso else 'ninguno'} | "
            f"{cuantas[clase]} | {QUE_ES[clase.value]} |"
        )
    lineas += [
        "",
        f"## Cada operación ({len(MATRIZ)})",
        "",
        "| Método | Ruta | Clase | Permiso |",
        "| --- | --- | --- | --- |",
    ]
    for (metodo, ruta), clase in sorted(MATRIZ.items(), key=lambda par: (par[0][1], par[0][0])):
        permiso = PERMISO_DE.get(clase)
        lineas.append(
            f"| `{metodo}` | `{ruta}` | `{clase.value}` | "
            f"{f'`{permiso.value}`' if permiso else '—'} |"
        )
    return "\n".join(lineas) + "\n"


def main() -> int:
    DESTINO.write_bytes(documento().encode("utf-8"))
    print(f"{DESTINO.relative_to(RAIZ)}: escrita.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
