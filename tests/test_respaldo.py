"""El respaldo oficial (scripts/respaldo.py): la base y el almacen de artefactos, verificados antes
de tocar nada, restaurados en un entorno nuevo y validados por sus filas y sus objetos.

Las pruebas sin base arman un respaldo a mano y lo alteran de cada forma que se debe detectar.
Las de integracion respaldan la base de pruebas con pg_dump y la restauran con pg_restore en una
base nueva; necesitan los dos en el PATH, del mismo mayor que el servidor. En el CI son
obligatorias (MC_RESPALDO_EXIGIR_PG=1): sin pg_dump fallan en vez de saltarse.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from conftest import ALMACEN_DE_PRUEBAS, RAIZ, URL_PRUEBAS
from sqlalchemy import create_engine, make_url, text


def _herramienta():
    ruta = RAIZ / "scripts" / "respaldo.py"
    especificacion = importlib.util.spec_from_file_location("respaldo", ruta)
    modulo = importlib.util.module_from_spec(especificacion)
    # Registrado antes de ejecutarlo: sus dataclasses resuelven sus anotaciones en sys.modules.
    sys.modules[especificacion.name] = modulo
    especificacion.loader.exec_module(modulo)
    return modulo


respaldo = _herramienta()

TOC = """;
; Archive created at 2026-10-18 21:00:00 UTC
;     dbname: cartera_dev
;
; Selected TOC Entries:
;
5; 2615 2200 SCHEMA - public pg_database_owner
4321; 0 0 COMMENT - SCHEMA public pg_database_owner
220; 1259 16390 TABLE public alembic_version motor
221; 1259 16395 TABLE public corrida motor
4400; 0 16395 TABLE DATA public corrida motor
4401; 0 18210 TABLE DATA public spatial_ref_sys postgres
4500; 2606 16400 CONSTRAINT public corrida pk_corrida motor
"""


def _contenido(n: int) -> bytes:
    return f"cliente_unico,saldo_total\nCU{n:08d},{n}.00\n".encode()


def _respaldo_falso(raiz: Path, objetos: list[bytes] | None = None) -> Path:
    """Un respaldo armado a mano, con la forma exacta del que escribe la herramienta."""
    objetos = [_contenido(1), _contenido(2), _contenido(3)] if objetos is None else objetos
    directorio = raiz / "motor-cartera-20261018T210000Z"
    directorio.mkdir(parents=True)
    volcado = b"PGDMP" + b"\x00volcado-de-prueba" * 64
    (directorio / respaldo.ARCHIVO_DUMP).write_bytes(volcado)
    registrados = {}
    for contenido in objetos:
        sha256 = hashlib.sha256(contenido).hexdigest()
        ruta = directorio / respaldo.DIRECTORIO_ALMACEN / respaldo.clave_de(sha256)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_bytes(contenido)
        registrados[sha256] = len(contenido)
    lista = directorio / respaldo.ARCHIVO_LISTA
    lista.write_bytes(respaldo.lista_de_objetos(registrados).encode("ascii"))
    manifiesto = {
        "formato": respaldo.FORMATO,
        "creado_en": "2026-10-18T21:00:00+00:00",
        "app_version": "0.18.0",
        "revision_esquema": "0017",
        "postgres_version": "16.10",
        "postgres_version_num": 160010,
        "base_origen": {"nombre": "cartera", "identificador_sistema": "7431"},
        "extensiones": {"postgis": "3.5.3"},
        "dump": {
            "archivo": respaldo.ARCHIVO_DUMP,
            "formato": "custom",
            "opciones": [],
            "sha256": hashlib.sha256(volcado).hexdigest(),
            "bytes": len(volcado),
        },
        "conteos": {"corrida": 3},
        "almacen": {
            "incluido": True,
            "directorio": respaldo.DIRECTORIO_ALMACEN,
            "lista": respaldo.ARCHIVO_LISTA,
            "sha256_lista": hashlib.sha256(lista.read_bytes()).hexdigest(),
            "objetos": len(registrados),
            "bytes": sum(registrados.values()),
        },
    }
    (directorio / respaldo.ARCHIVO_MANIFIESTO).write_bytes(json.dumps(manifiesto).encode())
    return directorio


def _manifiesto(directorio: Path) -> dict:
    return json.loads((directorio / respaldo.ARCHIVO_MANIFIESTO).read_bytes())


def _reescribir_manifiesto(directorio: Path, manifiesto: dict) -> None:
    (directorio / respaldo.ARCHIVO_MANIFIESTO).write_bytes(json.dumps(manifiesto).encode())


def _objetos_de(directorio: Path) -> list[Path]:
    raiz = directorio / respaldo.DIRECTORIO_ALMACEN
    return sorted(p for p in raiz.rglob("*") if p.is_file())


def _codigo(funcion, *args, **kwargs) -> str:
    with pytest.raises(respaldo.Negado) as negado:
        funcion(*args, **kwargs)
    return negado.value.codigo


# --- verificar ----------------------------------------------------------------------------------


def test_un_respaldo_intacto_se_verifica_con_cada_objeto(tmp_path):
    directorio = _respaldo_falso(tmp_path)

    manifiesto, objetos = respaldo.verificar(directorio)

    assert manifiesto["revision_esquema"] == "0017"
    assert len(objetos) == 3 and all(len(sha) == 64 for sha in objetos)


def test_un_volcado_con_un_byte_cambiado_se_niega(tmp_path):
    directorio = _respaldo_falso(tmp_path)
    volcado = directorio / respaldo.ARCHIVO_DUMP
    datos = bytearray(volcado.read_bytes())
    datos[100] ^= 0xFF
    volcado.write_bytes(bytes(datos))

    assert _codigo(respaldo.verificar, directorio) == "RESPALDO_ALTERADO"


def test_un_volcado_truncado_se_niega(tmp_path):
    directorio = _respaldo_falso(tmp_path)
    volcado = directorio / respaldo.ARCHIVO_DUMP
    volcado.write_bytes(volcado.read_bytes()[:-1])

    assert _codigo(respaldo.verificar, directorio) == "RESPALDO_ALTERADO"


def test_un_objeto_alterado_se_niega(tmp_path):
    directorio = _respaldo_falso(tmp_path)
    objeto = _objetos_de(directorio)[0]
    objeto.write_bytes(objeto.read_bytes().replace(b"CU", b"CX"))

    assert _codigo(respaldo.verificar, directorio) == "RESPALDO_ALTERADO"


def test_un_objeto_que_falta_se_niega(tmp_path):
    directorio = _respaldo_falso(tmp_path)
    _objetos_de(directorio)[1].unlink()

    assert _codigo(respaldo.verificar, directorio) == "RESPALDO_ALTERADO"


def test_un_objeto_de_mas_se_niega(tmp_path):
    directorio = _respaldo_falso(tmp_path)
    intruso = hashlib.sha256(b"intruso").hexdigest()
    ruta = directorio / respaldo.DIRECTORIO_ALMACEN / respaldo.clave_de(intruso)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(b"intruso")

    assert _codigo(respaldo.verificar, directorio) == "RESPALDO_ALTERADO"


def test_una_lista_editada_se_niega_aunque_los_objetos_esten(tmp_path):
    directorio = _respaldo_falso(tmp_path)
    lista = directorio / respaldo.ARCHIVO_LISTA
    lineas = lista.read_bytes().decode().splitlines()
    lista.write_bytes(("\n".join(lineas[:-1]) + "\n").encode())

    assert _codigo(respaldo.verificar, directorio) == "RESPALDO_ALTERADO"


@pytest.mark.parametrize(
    ("cambio", "codigo"),
    [
        (lambda m: m.pop("conteos"), "MANIFIESTO_INCOMPLETO"),
        (lambda m: m.pop("almacen"), "MANIFIESTO_INCOMPLETO"),
        (lambda m: m.update(formato="respaldo-postgres/v1"), "FORMATO_DESCONOCIDO"),
    ],
)
def test_un_manifiesto_sin_forma_se_niega(tmp_path, cambio, codigo):
    directorio = _respaldo_falso(tmp_path)
    manifiesto = _manifiesto(directorio)
    cambio(manifiesto)
    _reescribir_manifiesto(directorio, manifiesto)

    assert _codigo(respaldo.verificar, directorio) == codigo


def test_sin_manifiesto_no_hay_respaldo(tmp_path):
    directorio = _respaldo_falso(tmp_path)
    (directorio / respaldo.ARCHIVO_MANIFIESTO).unlink()

    assert _codigo(respaldo.verificar, directorio) == "SIN_MANIFIESTO"


def test_un_manifiesto_que_no_es_json_se_niega(tmp_path):
    directorio = _respaldo_falso(tmp_path)
    (directorio / respaldo.ARCHIVO_MANIFIESTO).write_bytes(b"{no es json")

    assert _codigo(respaldo.verificar, directorio) == "MANIFIESTO_ILEGIBLE"


def test_la_linea_de_comandos_dice_integro_o_negado_con_su_codigo(tmp_path, capsys):
    directorio = _respaldo_falso(tmp_path)

    assert respaldo.main(["verificar", str(directorio)]) == 0
    assert json.loads(capsys.readouterr().out)["resultado"] == "INTEGRO"
    _objetos_de(directorio)[0].write_bytes(b"otra cosa")
    assert respaldo.main(["verificar", str(directorio)]) == 1
    salida = json.loads(capsys.readouterr().out)
    assert (salida["resultado"], salida["codigo"]) == ("NEGADO", "RESPALDO_ALTERADO")


# --- compatibilidad del destino -----------------------------------------------------------------


def _destino(**cambios) -> respaldo.Destino:
    valores = {
        "nombre": "cartera",
        "identificador_sistema": "9999",
        "version_num": 160010,
        "tablas": 0,
        "extensiones": {"postgis": "3.5.3", "postgis_topology": "3.5.3"},
    }
    valores.update(cambios)
    return respaldo.Destino(**valores)


def _revisar(tmp_path, destino, *, revision_esperada="0017", permitir=False, pg_restore=16):
    manifiesto = _manifiesto(_respaldo_falso(tmp_path))
    return respaldo.revisar_compatibilidad(
        manifiesto,
        destino,
        revision_esperada=revision_esperada,
        permitir_otra_revision=permitir,
        mayor_pg_restore=pg_restore,
    )


@pytest.mark.parametrize(
    ("destino", "opciones", "codigo"),
    [
        (_destino(identificador_sistema="7431"), {}, "DESTINO_ES_EL_ORIGEN"),
        (_destino(tablas=1), {}, "BASE_NO_VACIA"),
        (_destino(version_num=150008), {}, "POSTGRES_ANTERIOR"),
        (_destino(), {"pg_restore": 15}, "PG_RESTORE_ANTERIOR"),
        (_destino(extensiones={}), {}, "EXTENSIONES_FALTANTES"),
        (_destino(), {"revision_esperada": "0018"}, "REVISION_INCOMPATIBLE"),
    ],
)
def test_un_destino_donde_no_se_debe_restaurar_se_niega(tmp_path, destino, opciones, codigo):
    assert _codigo(_revisar, tmp_path, destino, **opciones) == codigo


def test_otra_revision_se_permite_solo_a_proposito_y_con_advertencia(tmp_path):
    advertencias = _revisar(tmp_path, _destino(), revision_esperada="0018", permitir=True)

    assert advertencias and "0017" in advertencias[0]


def test_un_destino_nuevo_compatible_no_tiene_advertencias(tmp_path):
    assert _revisar(tmp_path, _destino(version_num=170002)) == []


# --- la tabla de contenido y el almacen ---------------------------------------------------------


def test_la_restauracion_no_toca_lo_que_pone_la_plataforma_de_destino(tmp_path):
    """Ni el esquema public (ya existe) ni los datos de spatial_ref_sys (son de PostGIS)."""
    filtrada = respaldo.entradas_a_restaurar(TOC)

    assert "SCHEMA - public" not in filtrada and "COMMENT - SCHEMA public" not in filtrada
    assert "spatial_ref_sys" not in filtrada
    for entrada in ("TABLE public alembic_version", "TABLE DATA public corrida", "CONSTRAINT"):
        assert entrada in filtrada
    assert filtrada.count("\n") == TOC.count("\n") - 3


def test_los_objetos_se_restauran_verificados_y_de_solo_lectura(tmp_path):
    directorio = _respaldo_falso(tmp_path / "respaldos")
    _, objetos = respaldo.verificar(directorio)
    almacen = tmp_path / "almacen"
    ya_estaba, danado, _ = sorted(objetos)
    for sha256, contenido in ((ya_estaba, None), (danado, b"bytes danados")):
        ruta = almacen / respaldo.clave_de(sha256)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        if contenido is None:
            shutil.copyfile(
                directorio / respaldo.DIRECTORIO_ALMACEN / respaldo.clave_de(sha256), ruta
            )
        else:
            ruta.write_bytes(contenido)

    cuenta = respaldo.restaurar_objetos(directorio, almacen, objetos)

    assert cuenta == {"copiados": 1, "ya_presentes": 1, "repuestos": 1}
    for sha256, tamano in objetos.items():
        ruta = almacen / respaldo.clave_de(sha256)
        assert respaldo.firmar(ruta) == (sha256, tamano)
        # El que ya estaba con sus bytes no se toca; el copiado y el repuesto, de solo lectura.
        if sha256 != ya_estaba:
            assert stat.S_IMODE(ruta.stat().st_mode) & 0o222 == 0
    assert list((almacen / "tmp").iterdir()) == []


def test_una_copia_cuyos_bytes_no_son_los_de_su_firma_no_deja_nada(tmp_path):
    origen = tmp_path / "origen"
    origen.write_bytes(b"lo que de verdad hay")
    destino = tmp_path / "almacen" / "sha256" / "ab" / ("ab" + "0" * 62)
    temporales = tmp_path / "almacen" / "tmp"

    codigo = _codigo(
        respaldo.copiar_verificando,
        origen,
        destino,
        "ab" + "0" * 62,
        20,
        temporales=temporales,
        codigo="ALMACEN_DANADO",
    )

    assert codigo == "ALMACEN_DANADO"
    assert not destino.exists() and list(temporales.iterdir()) == []


def test_un_respaldo_que_falla_a_medias_no_deja_su_directorio(tmp_path, monkeypatch):
    monkeypatch.setattr(respaldo.shutil, "which", lambda _: "/usr/bin/pg_dump")

    def a_medias(_dsn, directorio, *_args):
        objeto = directorio / respaldo.DIRECTORIO_ALMACEN / "sha256" / "ab" / "objeto"
        objeto.parent.mkdir(parents=True)
        objeto.write_bytes(b"a medias")
        os.chmod(objeto, stat.S_IREAD)
        raise respaldo.Negado("ALMACEN_INCOMPLETO", "falta un objeto")

    monkeypatch.setattr(respaldo, "_respaldar_en", a_medias)

    assert _codigo(respaldo.respaldar, "postgresql://x", tmp_path, None) == "ALMACEN_INCOMPLETO"
    assert list(tmp_path.iterdir()) == []


def test_la_contrasena_va_al_entorno_de_pg_dump_y_no_a_sus_argumentos():
    entorno = respaldo._entorno_libpq(
        "postgresql://mc_respaldo:una-contrasena@postgres:5432/cartera"
    )

    assert entorno["PGPASSWORD"] == "una-contrasena"
    assert (entorno["PGUSER"], entorno["PGHOST"], entorno["PGDATABASE"]) == (
        "mc_respaldo",
        "postgres",
        "cartera",
    )


def test_una_url_de_sqlalchemy_se_lee_como_libpq():
    assert respaldo.dsn_libpq(" postgresql+psycopg://u:p@h/b\n") == "postgresql://u:p@h/b"


# --- de punta a punta, con pg_dump y pg_restore -------------------------------------------------


def _mayor_del_cliente() -> int | None:
    if shutil.which("pg_dump") is None or shutil.which("pg_restore") is None:
        return None
    salida = subprocess.run(["pg_dump", "--version"], capture_output=True, text=True, check=True)
    return int(salida.stdout.split()[-1].split(".")[0])


PG_DISPONIBLE = _mayor_del_cliente() is not None
EXIGIR_PG = os.environ.get("MC_RESPALDO_EXIGIR_PG") == "1"
# Una marca y no un fixture: se decide al recolectar, antes de que algun fixture toque la base.
con_pg = pytest.mark.skipif(
    not PG_DISPONIBLE and not EXIGIR_PG, reason="sin pg_dump/pg_restore en el PATH"
)


@pytest.fixture
def requiere_pg() -> None:
    """En el CI (MC_RESPALDO_EXIGIR_PG=1), sin pg_dump la prueba falla en vez de saltarse."""
    if not PG_DISPONIBLE:
        pytest.fail("MC_RESPALDO_EXIGIR_PG=1 y no hay pg_dump/pg_restore en el PATH")


@pytest.fixture
def base_nueva(requiere_pg):
    """Una base vacia con las extensiones de la imagen de PostGIS, como la de un entorno nuevo."""
    nombre = f"restaurada_{uuid.uuid4().hex[:10]}_test"
    url = make_url(URL_PRUEBAS)
    servidor = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with servidor.connect() as conexion:
            conexion.execute(text(f'CREATE DATABASE "{nombre}" TEMPLATE template_postgis'))
        yield url.set(database=nombre)
    finally:
        with servidor.connect() as conexion:
            conexion.execute(text(f'DROP DATABASE IF EXISTS "{nombre}" WITH (FORCE)'))
        servidor.dispose()


def _dsn(url) -> str:
    return respaldo.dsn_libpq(url.render_as_string(hide_password=False))


def _tablas_en(url) -> int:
    motor = create_engine(url)
    try:
        with motor.connect() as conexion:
            return conexion.scalar(
                text(
                    "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' "
                    "AND tablename <> 'spatial_ref_sys'"
                )
            )
    finally:
        motor.dispose()


@con_pg
@pytest.mark.usefixtures("requiere_pg", "bd")
def test_la_base_y_su_almacen_se_restauran_enteros_en_un_entorno_nuevo(
    cliente, tmp_path, base_nueva
):
    for n in (1, 2, 3):
        respuesta = cliente.post(
            "/corridas",
            files={"archivo": ("cartera.csv", _cartera(n), "text/csv")},
        )
        assert respuesta.status_code == 201, respuesta.text

    directorio = respaldo.respaldar(
        _dsn(make_url(URL_PRUEBAS)), tmp_path / "respaldos", ALMACEN_DE_PRUEBAS
    )
    manifiesto, objetos = respaldo.verificar(directorio)
    almacen = tmp_path / "almacen-nuevo"
    informe = respaldo.restaurar(
        _dsn(base_nueva),
        directorio,
        almacen=almacen,
        sin_almacen=False,
        revision_esperada=manifiesto["revision_esquema"],
        permitir_otra_revision=False,
    )

    assert manifiesto["conteos"]["artefacto_fuente"] == 3 == len(objetos)
    assert informe["resultado"] == "VALIDADA", informe
    assert informe["conteos_iguales"] and informe["restricciones_sin_validar"] == 0
    assert informe["almacen"]["copiados"] == 3 and informe["almacen"]["artefactos_faltantes"] == 0
    # El volcado se restaura entero; sobre la base ya restaurada, una segunda vez se niega.
    segunda = _codigo(
        respaldo.restaurar,
        _dsn(base_nueva),
        directorio,
        almacen=almacen,
        sin_almacen=False,
        revision_esperada=None,
        permitir_otra_revision=False,
    )
    assert segunda == "BASE_NO_VACIA"


@con_pg
@pytest.mark.usefixtures("requiere_pg", "bd")
def test_un_respaldo_alterado_se_niega_antes_de_tocar_la_base_de_destino(
    cliente, tmp_path, base_nueva
):
    respuesta = cliente.post(
        "/corridas", files={"archivo": ("cartera.csv", _cartera(7), "text/csv")}
    )
    assert respuesta.status_code == 201
    directorio = respaldo.respaldar(
        _dsn(make_url(URL_PRUEBAS)), tmp_path / "respaldos", ALMACEN_DE_PRUEBAS
    )
    volcado = directorio / respaldo.ARCHIVO_DUMP
    datos = bytearray(volcado.read_bytes())
    datos[len(datos) // 2] ^= 0x01
    os.chmod(volcado, stat.S_IREAD | stat.S_IWRITE)
    volcado.write_bytes(bytes(datos))

    codigo = _codigo(
        respaldo.restaurar,
        _dsn(base_nueva),
        directorio,
        almacen=tmp_path / "almacen-nuevo",
        sin_almacen=False,
        revision_esperada=None,
        permitir_otra_revision=False,
    )

    assert codigo == "RESPALDO_ALTERADO"
    assert _tablas_en(base_nueva) == 0
    assert not (tmp_path / "almacen-nuevo").exists()


@con_pg
@pytest.mark.usefixtures("requiere_pg", "bd")
def test_nunca_se_restaura_sobre_la_base_de_origen(cliente, tmp_path):
    respuesta = cliente.post(
        "/corridas", files={"archivo": ("cartera.csv", _cartera(9), "text/csv")}
    )
    assert respuesta.status_code == 201
    directorio = respaldo.respaldar(
        _dsn(make_url(URL_PRUEBAS)), tmp_path / "respaldos", ALMACEN_DE_PRUEBAS
    )

    codigo = _codigo(
        respaldo.restaurar,
        _dsn(make_url(URL_PRUEBAS)),
        directorio,
        almacen=tmp_path / "almacen",
        sin_almacen=False,
        revision_esperada=None,
        permitir_otra_revision=False,
    )

    assert codigo in {"DESTINO_ES_EL_ORIGEN", "BASE_NO_VACIA"}


def test_un_respaldo_sin_almacen_de_una_base_con_artefactos_se_niega(tmp_path):
    """Sin --almacen ni MC_SOURCE_STORE_ROOT la restauracion no adivina: se niega."""
    directorio = _respaldo_falso(tmp_path)

    codigo = _codigo(
        respaldo.restaurar,
        "postgresql://x",
        directorio,
        almacen=None,
        sin_almacen=False,
        revision_esperada=None,
        permitir_otra_revision=False,
    )

    assert codigo == "SIN_ALMACEN_DESTINO"


def _cartera(n: int) -> bytes:
    return (
        "cliente_unico,saldo_total,dias_atraso,producto,canal,cve_entidad,cve_municipio,"
        f"fecha_corte\nCU{n:08d},{3000 + n}.00,120,CONSUMO,CAMPO,21,114,2026-09-30\n"
    ).encode()
