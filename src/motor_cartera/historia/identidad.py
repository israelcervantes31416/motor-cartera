"""Los identificadores publicos del modelo historico, deterministas.

Una cuenta canonica, un corte canonico y un pago observado tienen un identificador publico, un UUID,
que no se sortea: sale de su llave natural con UUID version 5 (RFC 9562), en un espacio de nombres
propio del proyecto. Asi reconstruir el modelo historico, en cualquier orden y en cualquier maquina,
da los mismos identificadores, y una referencia guardada fuera de la base sigue valiendo despues de
un backfill.

- cuenta: despacho, cartera y CLIENTE_UNICO;
- corte: despacho, cartera y fecha de corte;
- pago observado: el dataset conformado (su dataset_id publico) y la fila de la fuente.

No esconden nada: quien conoce la llave puede calcular el identificador, igual que con la API key
puede buscar la cuenta por su CLIENTE_UNICO. Lo que no revelan, a diferencia del id interno, es el
volumen ni el orden en que se crearon. Las partes de cada llave no pueden traer `:`: despacho y
cartera son `[A-Z0-9_]`, CLIENTE_UNICO es `[A-Z0-9]`, y los demas son fechas, UUID y enteros.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Iterator
from datetime import date
from uuid import UUID

ESPACIO = UUID("000d9bea-e804-4d64-aef5-fef904de975c")
"""El espacio de nombres de los identificadores del modelo historico. No cambia nunca: cambiarlo
cambiaria cada identificador publicado."""

_ESPACIO = ESPACIO.bytes


def _uuid5(nombre: str) -> str:
    """uuid.uuid5(ESPACIO, nombre), como texto, sin construir un objeto UUID: se calcula por cada
    fila de un corte de cientos de miles de cuentas. Las pruebas comparan las dos formas."""
    digest = bytearray(hashlib.sha1(_ESPACIO + nombre.encode("utf-8")).digest()[:16])
    digest[6] = (digest[6] & 0x0F) | 0x50  # version 5
    digest[8] = (digest[8] & 0x3F) | 0x80  # variante RFC
    h = digest.hex()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


def cuenta_id(despacho_id: str, cartera_id: str, cliente_unico: str) -> UUID:
    return UUID(_uuid5(f"cuenta:{despacho_id}:{cartera_id}:{cliente_unico}"))


def corte_id(despacho_id: str, cartera_id: str, fecha_corte: date) -> UUID:
    return UUID(_uuid5(f"corte:{despacho_id}:{cartera_id}:{fecha_corte.isoformat()}"))


def pago_observado_id(dataset_id: UUID, source_row: int) -> UUID:
    return UUID(_uuid5(f"pago:{dataset_id}:{source_row}"))


def cuenta_ids(despacho_id: str, cartera_id: str, clientes: Iterable[str]) -> Iterator[str]:
    """`cuenta_id` de cada CLIENTE_UNICO, como texto, para copiarlos a la base."""
    prefijo = f"cuenta:{despacho_id}:{cartera_id}:"
    return (_uuid5(prefijo + cliente) for cliente in clientes)


def pago_observado_ids(dataset_id: UUID, filas: Iterable[int]) -> Iterator[str]:
    """`pago_observado_id` de cada fila de un dataset, como texto."""
    prefijo = f"pago:{dataset_id}:"
    return (_uuid5(f"{prefijo}{fila}") for fila in filas)
