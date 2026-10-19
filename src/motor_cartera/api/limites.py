"""El tope del cuerpo de una peticion que no sube un archivo.

Las subidas (`multipart/form-data`: POST /corridas y POST /pagos) tienen su propio tope,
`MC_TAMANO_MAXIMO_MB`, que se aplica mientras el archivo se copia al almacen. Cualquier otro cuerpo
(JSON) no pasa de `MC_TAMANO_MAXIMO_CUERPO_KB`: si su Content-Length ya lo excede responde 413 sin
leerlo, y si no lo declara, se cuenta mientras llega y se corta al pasarlo.
"""

from __future__ import annotations

import json

from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class CuerpoDemasiadoGrande(HTTPException):
    """Un HTTPException para que FastAPI no lo convierta en un 400 al leer el cuerpo: el
    manejador de errores lo responde 413 con su codigo."""

    def __init__(self, maximo: int) -> None:
        super().__init__(413, f"El cuerpo de la peticion excede {maximo // 1024} KiB.")


class LimiteDeCuerpo:
    def __init__(self, app: ASGIApp, maximo_bytes: int) -> None:
        self.app = app
        self.maximo = maximo_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        encabezados = Headers(scope=scope)
        if encabezados.get("content-type", "").startswith("multipart/form-data"):
            await self.app(scope, receive, send)
            return
        declarado = encabezados.get("content-length")
        if declarado is not None and declarado.isdigit() and int(declarado) > self.maximo:
            await _responder_413(send, self.maximo)
            return
        recibido = 0
        iniciada = False

        async def recibir() -> Message:
            nonlocal recibido
            mensaje = await receive()
            if mensaje["type"] == "http.request":
                recibido += len(mensaje.get("body", b""))
                if recibido > self.maximo:
                    raise CuerpoDemasiadoGrande(self.maximo)
            return mensaje

        async def enviar(mensaje: Message) -> None:
            nonlocal iniciada
            if mensaje["type"] == "http.response.start":
                iniciada = True
            await send(mensaje)

        try:
            await self.app(scope, recibir, enviar)
        except CuerpoDemasiadoGrande:
            if iniciada:
                raise
            await _responder_413(send, self.maximo)


async def _responder_413(send: Send, maximo: int) -> None:
    cuerpo = json.dumps(
        {
            "codigo": "CUERPO_DEMASIADO_GRANDE",
            "mensaje": f"El cuerpo de la peticion excede {maximo // 1024} KiB.",
            "detalles": [],
            "run_id": None,
            "pagos_run_id": None,
        }
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(cuerpo)).encode()),
                (b"connection", b"close"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": cuerpo})
