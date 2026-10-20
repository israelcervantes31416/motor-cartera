"""Cuanto tarda cada fase de un entrenamiento o de un scoring, la memoria pico del proceso y las
versiones del entorno: lo que guarda la ejecucion y lee el benchmark. No cambia ningun resultado."""

from __future__ import annotations

import ctypes
import platform
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager

import numpy as np
import pyarrow


class Tiempos:
    """Los segundos de cada fase, en el orden en que empezaron."""

    def __init__(self) -> None:
        self.fases: dict[str, float] = {}
        self._inicio = time.perf_counter()

    @contextmanager
    def fase(self, nombre: str) -> Iterator[None]:
        inicio = time.perf_counter()
        try:
            yield
        finally:
            self.fases[nombre] = self.fases.get(nombre, 0.0) + time.perf_counter() - inicio

    def resumen(self) -> dict:
        return {
            "fases_segundos": {k: round(v, 3) for k, v in self.fases.items()},
            "total_segundos": round(time.perf_counter() - self._inicio, 3),
            "memoria_pico_mb": memoria_pico_mb(),
        }


def memoria_pico_mb() -> float | None:
    """La memoria residente pico del proceso, en MB: el peak working set en Windows, ru_maxrss en
    Linux y macOS. None si el sistema no la da."""
    if sys.platform == "win32":
        return _pico_windows()
    try:
        import resource
    except ImportError:  # pragma: no cover - otra plataforma
        return None
    pico = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux la da en KiB y macOS en bytes.
    return round(pico / (1024 * 1024) if sys.platform == "darwin" else pico / 1024, 1)


def _pico_windows() -> float | None:  # pragma: no cover - solo en Windows
    # El verificador de tipos analiza este cuerpo para la plataforma en que corre; fuera de
    # Windows, ctypes no tiene WinDLL. Solo se llama en Windows (memoria_pico_mb).
    if sys.platform != "win32":
        return None

    class Contadores(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    contadores = Contadores()
    contadores.cb = ctypes.sizeof(Contadores)
    try:
        # Instancias propias: no cambian los argtypes de ctypes.windll que use otra biblioteca.
        kernel32, psapi = ctypes.WinDLL("kernel32"), ctypes.WinDLL("psapi")
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        psapi.GetProcessMemoryInfo.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(Contadores),
            ctypes.c_ulong,
        ]
        psapi.GetProcessMemoryInfo.restype = ctypes.c_int
        if not psapi.GetProcessMemoryInfo(
            kernel32.GetCurrentProcess(), ctypes.byref(contadores), contadores.cb
        ):
            return None
    except (AttributeError, OSError):
        return None
    return round(contadores.PeakWorkingSetSize / (1024 * 1024), 1)


def entorno() -> dict:
    """Con que se entreno: Python, NumPy, Arrow y la plataforma. Ningun dato de la maquina que
    identifique a una persona."""
    return {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "pyarrow": pyarrow.__version__,
        "sistema": platform.system(),
        "arquitectura": platform.machine(),
    }
