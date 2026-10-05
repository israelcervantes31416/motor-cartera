"""El almacen de artefactos por contenido, sin base de datos.

Lo que tiene que cumplir para ser evidencia: guarda por SHA-256 y no por nombre, no duplica bytes,
devuelve exactamente lo que guardo, nunca publica una escritura a medias y dice con claridad cuando
un objeto falta o ya no es el que era.
"""

from __future__ import annotations

import hashlib
import io
import os
import threading
from pathlib import Path

import pytest

from motor_cartera.fuentes.almacen import (
    BLOQUE,
    ArtefactoCorrupto,
    ArtefactoDemasiadoGrande,
    ArtefactoFaltante,
    ArtefactoVacio,
    LocalContentAddressedStore,
    clave_de,
)

CONTENIDO = b"CLIENTE_UNICO,SALDO_TOTAL\nCU0000000001,1500.50\n"


class _Lento(io.RawIOBase):
    """Un origen que entrega de a poco, como una subida por la red, y puede romperse a la mitad."""

    def __init__(self, contenido: bytes, *, falla_en: int | None = None, error=OSError) -> None:
        self._datos = io.BytesIO(contenido)
        self._falla_en = falla_en
        self._error = error
        self.leidos = 0

    def readable(self) -> bool:
        return True

    def read(self, n: int = -1) -> bytes:
        bloque = self._datos.read(min(n, 64 * 1024) if n and n > 0 else 64 * 1024)
        self.leidos += len(bloque)
        if self._falla_en is not None and self.leidos >= self._falla_en:
            raise self._error("se corto la conexion")
        return bloque


def _objetos(raiz: Path) -> list[Path]:
    return sorted(p for p in (raiz / "sha256").rglob("*") if p.is_file())


def _temporales(raiz: Path) -> list[Path]:
    return sorted((raiz / "tmp").glob("*")) if (raiz / "tmp").exists() else []


def test_guarda_por_el_sha256_de_sus_bytes_y_no_por_su_nombre(tmp_path):
    almacen = LocalContentAddressedStore(tmp_path)

    guardado = almacen.guardar(io.BytesIO(CONTENIDO))

    sha256 = hashlib.sha256(CONTENIDO).hexdigest()
    assert (guardado.sha256, guardado.tamano_bytes, guardado.nuevo) == (
        sha256,
        len(CONTENIDO),
        True,
    )
    assert guardado.clave == f"sha256/{sha256[:2]}/{sha256}"
    assert _objetos(tmp_path) == [tmp_path / "sha256" / sha256[:2] / sha256]
    assert _temporales(tmp_path) == []


def test_devuelve_exactamente_los_mismos_bytes(tmp_path):
    almacen = LocalContentAddressedStore(tmp_path)
    contenido = bytes(range(256)) * 9000 + CONTENIDO  # varios bloques, ceros y bytes altos

    guardado = almacen.guardar(io.BytesIO(contenido))

    with almacen.abrir(guardado.sha256) as objeto:
        recuperado = objeto.read()
    assert recuperado == contenido
    assert hashlib.sha256(recuperado).hexdigest() == guardado.sha256
    almacen.verificar(guardado.sha256, len(contenido))  # no levanta


def test_el_mismo_contenido_dos_veces_no_duplica_bytes(tmp_path):
    almacen = LocalContentAddressedStore(tmp_path)

    primera = almacen.guardar(io.BytesIO(CONTENIDO))
    segunda = almacen.guardar(io.BytesIO(CONTENIDO))

    assert (primera.sha256, primera.nuevo) == (segunda.sha256, True)
    assert segunda.nuevo is False
    assert len(_objetos(tmp_path)) == 1
    assert _temporales(tmp_path) == []


def test_copia_por_bloques_un_archivo_de_varios_mib(tmp_path):
    almacen = LocalContentAddressedStore(tmp_path)
    contenido = os.urandom(3 * BLOQUE + 12345)
    origen = _Lento(contenido)

    guardado = almacen.guardar(origen)

    assert guardado.sha256 == hashlib.sha256(contenido).hexdigest()
    assert guardado.tamano_bytes == len(contenido) == origen.leidos


def test_un_objeto_es_de_solo_lectura(tmp_path):
    almacen = LocalContentAddressedStore(tmp_path)

    guardado = almacen.guardar(io.BytesIO(CONTENIDO))

    assert not os.access(almacen.ruta(guardado.sha256), os.W_OK)


def test_un_archivo_vacio_no_se_guarda(tmp_path):
    almacen = LocalContentAddressedStore(tmp_path)

    with pytest.raises(ArtefactoVacio):
        almacen.guardar(io.BytesIO(b""))

    assert _objetos(tmp_path) == [] and _temporales(tmp_path) == []


def test_lo_que_pasa_del_tope_no_se_guarda(tmp_path):
    almacen = LocalContentAddressedStore(tmp_path)

    with pytest.raises(ArtefactoDemasiadoGrande) as exc:
        almacen.guardar(io.BytesIO(b"x" * 1001), limite_bytes=1000)

    assert exc.value.limite_bytes == 1000
    assert _objetos(tmp_path) == [] and _temporales(tmp_path) == []
    # Justo en el tope si cabe.
    assert almacen.guardar(io.BytesIO(b"x" * 1000), limite_bytes=1000).tamano_bytes == 1000


@pytest.mark.parametrize("error", [OSError, KeyboardInterrupt], ids=["error", "interrupcion"])
def test_una_escritura_interrumpida_no_publica_un_archivo_parcial(tmp_path, error):
    # Se corta a la mitad: por un error, o por una interrupcion que ningun except Exception atrapa.
    almacen = LocalContentAddressedStore(tmp_path)
    contenido = os.urandom(2 * BLOQUE)

    with pytest.raises(error):
        almacen.guardar(_Lento(contenido, falla_en=BLOQUE, error=error))

    assert _objetos(tmp_path) == []
    assert _temporales(tmp_path) == []
    assert not almacen.existe(hashlib.sha256(contenido).hexdigest())


def test_si_la_validacion_rechaza_la_copia_no_queda_ningun_objeto(tmp_path):
    almacen = LocalContentAddressedStore(tmp_path)
    vistas = []

    def rechaza(ruta: Path) -> None:
        vistas.append(ruta.read_bytes())  # la copia completa, ya en disco
        raise ValueError("no es lo que dice ser")

    with pytest.raises(ValueError, match="no es lo que dice ser"):
        almacen.guardar(io.BytesIO(CONTENIDO), validar=rechaza)

    assert vistas == [CONTENIDO]
    assert _objetos(tmp_path) == [] and _temporales(tmp_path) == []


def test_un_objeto_que_falta_es_un_error_explicito(tmp_path):
    almacen = LocalContentAddressedStore(tmp_path)
    sha256 = hashlib.sha256(b"nunca se guardo").hexdigest()

    assert not almacen.existe(sha256)
    with pytest.raises(ArtefactoFaltante, match=sha256):
        almacen.abrir(sha256)
    with pytest.raises(ArtefactoFaltante):
        almacen.verificar(sha256)
    with pytest.raises(ArtefactoFaltante), almacen.como_archivo(sha256):
        pass


def test_un_objeto_danado_se_detecta_y_se_repone_con_una_copia_buena(tmp_path):
    almacen = LocalContentAddressedStore(tmp_path)
    guardado = almacen.guardar(io.BytesIO(CONTENIDO))
    ruta = almacen.ruta(guardado.sha256)
    os.chmod(ruta, 0o600)
    ruta.write_bytes(CONTENIDO[:-5])  # truncado en el disco

    with pytest.raises(ArtefactoCorrupto, match="esta danado"):
        almacen.verificar(guardado.sha256)

    # Volver a recibir el mismo archivo repone el objeto: su tamano ya no era el de su firma.
    repuesto = almacen.guardar(io.BytesIO(CONTENIDO))

    assert repuesto.nuevo is True
    almacen.verificar(guardado.sha256, len(CONTENIDO))


def test_otro_proceso_ve_lo_que_se_guardo(tmp_path):
    # Como si la API o el worker se reiniciaran: otra instancia sobre la misma raiz.
    guardado = LocalContentAddressedStore(tmp_path).guardar(io.BytesIO(CONTENIDO))

    otra = LocalContentAddressedStore(tmp_path)

    with otra.abrir(guardado.sha256) as objeto:
        assert objeto.read() == CONTENIDO
    with otra.como_archivo(guardado.sha256) as ruta:
        assert ruta.read_bytes() == CONTENIDO


def test_dos_escrituras_simultaneas_del_mismo_contenido_dejan_un_objeto(tmp_path):
    almacen = LocalContentAddressedStore(tmp_path)
    contenido = os.urandom(BLOQUE + 7)
    juntas = threading.Barrier(4, timeout=30)
    resultados, errores = [], []

    def guardar() -> None:
        try:
            juntas.wait()
            resultados.append(almacen.guardar(io.BytesIO(contenido)))
        except Exception as exc:  # pragma: no cover - solo si algo sale mal
            errores.append(exc)

    hilos = [threading.Thread(target=guardar) for _ in range(4)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=30)

    assert errores == []
    assert {r.sha256 for r in resultados} == {hashlib.sha256(contenido).hexdigest()}
    assert len(_objetos(tmp_path)) == 1 and _temporales(tmp_path) == []
    almacen.verificar(resultados[0].sha256, len(contenido))


@pytest.mark.parametrize("valor", ["", "abc", "A" * 64, "g" * 64, "a" * 63, None])
def test_solo_un_sha256_en_hexadecimal_es_una_clave(tmp_path, valor):
    with pytest.raises(ValueError, match="No es un SHA-256"):
        clave_de(valor)
    with pytest.raises(ValueError, match="No es un SHA-256"):
        LocalContentAddressedStore(tmp_path).ruta(valor)
