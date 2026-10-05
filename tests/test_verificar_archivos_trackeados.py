"""El control de archivos trackeados, contra repositorios de git hechos para cada prueba.

Usan el .gitignore real del proyecto, copiado tal cual: se prueba la politica que existe, no
una escrita aqui a mano.
"""

from __future__ import annotations

import bz2
import gzip
import io
import lzma
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.feather as feather
import pyarrow.parquet as pq
import pytest

from motor_cartera.generador.sintetico import generar_archivo

RAIZ = Path(__file__).resolve().parents[1]
SCRIPT = RAIZ / "scripts" / "verificar_archivos_trackeados.py"

# La imagen de Docker no trae git: ahi no hay contra que correr estas pruebas.
pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="hace falta git")

# Sin las variables GIT_* de quien corre las pruebas: dentro de un hook, GIT_DIR o
# GIT_INDEX_FILE harian que estos repositorios de prueba escribieran en el de verdad.
ENTORNO = {nombre: valor for nombre, valor in os.environ.items() if not nombre.startswith("GIT_")}


def git(repo: Path, *argumentos: str) -> None:
    subprocess.run(["git", *argumentos], cwd=repo, env=ENTORNO, check=True, capture_output=True)


def revisar(directorio: Path, **entorno: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=directorio,
        env={**ENTORNO, **entorno},
        capture_output=True,
        text=True,
    )


@pytest.fixture
def repo(tmp_path) -> Path:
    """Un repositorio en regla: el .gitignore del proyecto, datos/.gitkeep y un archivo."""
    repo = tmp_path / "repo"
    (repo / "datos").mkdir(parents=True)
    git(repo, "init", "-q")
    shutil.copy(RAIZ / ".gitignore", repo / ".gitignore")
    (repo / "datos" / ".gitkeep").touch()
    (repo / "leeme.md").write_text("# prueba\n")
    git(repo, "add", ".")
    return repo


@pytest.fixture
def carteras(tmp_path) -> dict[str, bytes]:
    """Una cartera sintetica en cada formato que el .gitignore prohibe por nombre."""
    return {
        "xlsx": generar_archivo(tmp_path / "cartera.xlsx", n=20, semilla=1).read_bytes(),
        "zip": generar_archivo(tmp_path / "cartera.zip", n=20, semilla=1).read_bytes(),
        # No hay con que escribir un xls; basta su cabecera OLE2, que es lo que se revisa.
        "xls": b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + bytes(504),
    }


def test_un_repositorio_en_regla_pasa(repo):
    resultado = revisar(repo)

    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
    assert resultado.stdout.startswith("3 archivos trackeados")  # datos/.gitkeep es la excepcion


def test_una_cartera_metida_con_git_add_f_dice_por_que_no_debe_estar(repo, carteras):
    (repo / "datos" / "cartera.xlsx").write_bytes(carteras["xlsx"])
    git(repo, "add", "--force", "datos/cartera.xlsx")

    resultado = revisar(repo)

    assert resultado.returncode == 1
    assert "datos/cartera.xlsx: lo excluye el .gitignore; por dentro es un zip" in resultado.stdout


@pytest.mark.parametrize(
    "ruta",
    [
        ".env",
        "config/.env",
        "reportes/cartera.csv",
        "datos_conformados/cartera.parquet",
        "respaldos/cartera.dump",
        "respaldos/volcado.sql",
        "respaldos/volcado.sql.gz",
        "locales/cartera.sqlite",
        "entregas/cartera.7z",
    ],
)
def test_nada_de_lo_que_el_gitignore_excluye_se_cuela(repo, ruta):
    archivo = repo / ruta
    archivo.parent.mkdir(parents=True, exist_ok=True)
    archivo.write_text("cualquier contenido\n")
    git(repo, "add", "--force", ruta)

    resultado = revisar(repo)

    assert resultado.returncode == 1
    assert f"{ruta}: lo excluye el .gitignore" in resultado.stdout


def test_las_mayusculas_no_esconden_una_cartera_donde_git_las_distingue(repo):
    # Como en Linux, WSL o el CI: ahi `*.csv` no excluye CARTERA.CSV, y un `git add` normal,
    # sin -f, la mete.
    git(repo, "config", "core.ignorecase", "false")
    (repo / "reportes").mkdir()
    (repo / "reportes" / "CARTERA.CSV").write_text("cliente_unico,saldo_total\n")
    git(repo, "add", "reportes/CARTERA.CSV")

    resultado = revisar(repo)

    assert resultado.returncode == 1
    assert (
        "reportes/CARTERA.CSV: lo excluye el .gitignore si no se distinguen mayusculas"
        in resultado.stdout
    )


@pytest.mark.parametrize(
    ("nombre", "formato", "que_es"),
    [
        ("respaldo.dat", "xlsx", "un zip"),  # un xlsx renombrado
        ("cartera.hoja", "xlsx", "un zip"),  # una extension que el .gitignore no conoce
        ("entrega.bin", "zip", "un zip"),
        ("historico.txt", "xls", "un documento OLE2"),
    ],
)
def test_una_hoja_de_calculo_o_un_zip_se_reconoce_por_dentro(
    repo, carteras, nombre, formato, que_es
):
    (repo / nombre).write_bytes(carteras[formato])
    git(repo, "add", nombre)

    resultado = revisar(repo)

    assert resultado.returncode == 1
    assert f"{nombre}: por dentro es {que_es}" in resultado.stdout


def _tabla() -> pa.Table:
    return pa.table({"cliente_unico": ["CU00000001", "CU00000002"], "saldo": [1.5, 2.5]})


def _parquet() -> bytes:
    destino = io.BytesIO()
    pq.write_table(_tabla(), destino)
    return destino.getvalue()


def _feather() -> bytes:
    destino = io.BytesIO()
    feather.write_feather(_tabla(), destino)
    return destino.getvalue()


def _sqlite(tmp_path: Path) -> bytes:
    ruta = tmp_path / "base"
    with sqlite3.connect(ruta) as conexion:
        conexion.execute("CREATE TABLE cuenta (cliente_unico TEXT)")
    conexion.close()
    return ruta.read_bytes()


TEXTO = b"cliente_unico,saldo\nCU00000001,1.50\n"


@pytest.mark.parametrize(
    ("nombre", "contenido", "que_es"),
    [
        ("conformado.bin", _parquet, "un Parquet"),
        ("tabla.bin", _feather, "un Arrow o Feather"),
        ("respaldo.dat", lambda: gzip.compress(TEXTO), "un gzip"),
        ("respaldo.bin", lambda: bz2.compress(TEXTO), "un bzip2"),
        ("vacio.bin", lambda: bz2.compress(b""), "un bzip2"),
        ("respaldo.raw", lambda: lzma.compress(TEXTO), "un xz"),
        ("respaldo.blob", lambda: pa.Codec("zstd").compress(TEXTO).to_pybytes(), "un zstd"),
        # Sin con que escribirlos aqui: basta su cabecera, que es lo que se revisa.
        ("entrega.dat", lambda: b"7z\xbc\xaf\x27\x1c" + bytes(32), "un 7z"),
        ("entrega.bin", lambda: b"Rar!\x1a\x07\x01\x00" + bytes(32), "un rar"),
        ("base.dat", lambda: b"PGDMP\x01\x0e\x00" + bytes(32), "un volcado de pg_dump"),
    ],
    ids=["parquet", "feather", "gzip", "bzip2", "bzip2-vacio", "xz", "zstd", "7z", "rar", "pgdump"],
)
def test_un_formato_de_datos_un_comprimido_o_un_volcado_se_reconoce_por_dentro(
    repo, nombre, contenido, que_es
):
    (repo / nombre).write_bytes(contenido())
    git(repo, "add", nombre)

    resultado = revisar(repo)

    assert resultado.returncode == 1
    assert f"{nombre}: por dentro es {que_es}" in resultado.stdout


def test_una_base_sqlite_se_reconoce_por_dentro(repo, tmp_path):
    (repo / "local.dat").write_bytes(_sqlite(tmp_path))
    git(repo, "add", "local.dat")

    resultado = revisar(repo)

    assert resultado.returncode == 1
    assert "local.dat: por dentro es una base SQLite" in resultado.stdout


def test_un_texto_que_empieza_como_un_formato_no_es_ese_formato(repo):
    # Las firmas son de varios bytes: un texto que empieza con BZh o con PAR no es un bzip2 ni
    # un Parquet.
    (repo / "notas.md").write_text("BZh es como empieza un bzip2; PAR, un Parquet.\n")
    git(repo, "add", "notas.md")

    assert revisar(repo).returncode == 0


def test_una_imagen_no_es_una_hoja_de_calculo(repo):
    (repo / "docs").mkdir()
    (repo / "docs" / "diagrama.png").write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(64))
    git(repo, "add", "docs/diagrama.png")

    assert revisar(repo).returncode == 0


def test_revisa_el_indice_que_es_lo_que_se_sube_no_la_copia_de_trabajo(repo, carteras):
    anexo = repo / "anexo.dat"
    anexo.write_bytes(carteras["zip"])
    git(repo, "add", "anexo.dat")
    anexo.write_text("en disco ya no es un zip, pero en el indice sigue siendolo\n")

    resultado = revisar(repo)

    assert resultado.returncode == 1
    assert "anexo.dat: por dentro es un zip" in resultado.stdout


def test_revisa_todo_el_repositorio_aunque_corra_desde_una_subcarpeta(repo):
    (repo / ".env").write_text("MC_API_KEY=otra\n")
    git(repo, "add", "--force", ".env")

    resultado = revisar(repo / "datos")

    assert resultado.returncode == 1
    assert ".env: lo excluye el .gitignore" in resultado.stdout


def test_fuera_de_un_repositorio_no_se_da_por_bueno(tmp_path):
    # Que git no suba de tmp_path buscando un repositorio: podria encontrar uno ajeno.
    resultado = revisar(tmp_path, GIT_CEILING_DIRECTORIES=str(tmp_path.parent))

    assert resultado.returncode == 2
    assert "No se pudieron revisar los archivos trackeados" in resultado.stderr
