"""Descarga el catalogo publico de entidades y municipios del INEGI y lo escribe en el paquete.

La proyeccion operacional de cartera/v2 resuelve ESTADO_CTE y POBLACION_CTE a las claves
cve_entidad y cve_municipio que usan los motores. Esas claves no se inventan: salen del Marco
Geoestadistico del INEGI, que es publico, por su servicio web del Catalogo Unico de Claves
Geoestadisticas. Este script es la procedencia del catalogo: lo vuelve a construir desde la
fuente, y su salida se versiona con el codigo. Cambiar el catalogo cambia lo que la proyeccion
resuelve, asi que exige otra version de la proyeccion.

Solo usa la biblioteca estandar:

    python scripts/actualizar_catalogo_geografico.py

Escribe src/motor_cartera/fuentes/catalogo_inegi.json en ASCII (los acentos van escapados), con
un municipio por linea, y termina con codigo 1 si la fuente no responde o trae algo inesperado:
un catalogo a medias no se escribe.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from datetime import date
from pathlib import Path

URL = "https://gaia.inegi.org.mx/wscatgeo/v2"
PAQUETE = Path(__file__).resolve().parents[1] / "src" / "motor_cartera"
DESTINO = PAQUETE / "fuentes" / "catalogo_inegi.json"
ENTIDADES = 32


def consultar(ruta: str) -> list[dict]:
    with urllib.request.urlopen(f"{URL}/{ruta}", timeout=60) as respuesta:
        datos = json.loads(respuesta.read().decode("utf-8"))["datos"]
    if not isinstance(datos, list) or not datos:
        raise ValueError(f"{ruta}: la fuente no trajo datos")
    return datos


def _poblacion(registro: dict) -> int | None:
    """La poblacion total del censo. Los municipios creados despues del censo no la tienen."""
    valor = registro.get("pob_total")
    return int(valor) if valor not in (None, "") else None


def _serializar(documento: dict) -> str:
    """JSON valido en ASCII, con un municipio por linea: legible en un diff y sin relleno."""

    def compacto(valor: object) -> str:
        return json.dumps(valor, ensure_ascii=True, separators=(", ", ": "))

    lineas = ["{"]
    for llave in ("fuente", "url", "consultado"):
        lineas.append(f" {compacto(llave)}: {compacto(documento[llave])},")
    lineas.append(' "entidades": [')
    ultima = len(documento["entidades"]) - 1
    for i, entidad in enumerate(documento["entidades"]):
        cabeza = {k: v for k, v in entidad.items() if k != "municipios"}
        lineas.append("  " + compacto(cabeza)[:-1] + ', "municipios": [')
        lineas.append(",\n".join("   " + compacto(m) for m in entidad["municipios"]))
        lineas.append("  ]}" + ("," if i < ultima else ""))
    lineas.extend([" ]", "}"])
    return "\n".join(lineas) + "\n"


def main() -> int:
    try:
        entidades = consultar("mgee/")
        if len(entidades) != ENTIDADES:
            raise ValueError(f"se esperaban {ENTIDADES} entidades y llegaron {len(entidades)}")
        catalogo = []
        for entidad in sorted(entidades, key=lambda e: e["cve_ent"]):
            municipios = consultar(f"mgem/{entidad['cve_ent']}")
            if any(m["cve_ent"] != entidad["cve_ent"] for m in municipios):
                raise ValueError(f"{entidad['cve_ent']}: municipios de otra entidad")
            catalogo.append(
                {
                    "cve_ent": entidad["cve_ent"],
                    "nombre": entidad["nomgeo"],
                    "abreviatura": entidad["nom_abrev"],
                    "poblacion": _poblacion(entidad),
                    "municipios": [
                        {
                            "cve_mun": m["cve_mun"],
                            "nombre": m["nomgeo"],
                            "poblacion": _poblacion(m),
                        }
                        for m in sorted(municipios, key=lambda m: m["cve_mun"])
                    ],
                }
            )
    except (OSError, ValueError, KeyError) as error:
        print(f"No se pudo construir el catalogo: {error}", file=sys.stderr)
        return 1

    documento = {
        "fuente": "INEGI, Marco Geoestadistico. Servicio web del Catalogo Unico de Claves "
        "Geoestadisticas (wscatgeo v2): entidades (mgee) y municipios (mgem).",
        "url": URL,
        "consultado": date.today().isoformat(),
        "entidades": catalogo,
    }
    texto = _serializar(documento)
    json.loads(texto)  # que lo escrito sea JSON valido, antes de escribirlo
    DESTINO.write_text(texto, encoding="ascii", newline="\n")
    municipios = sum(len(e["municipios"]) for e in catalogo)
    print(f"Escrito {DESTINO.name}: {len(catalogo)} entidades, {municipios} municipios.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
