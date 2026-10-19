"""El evento de auditoria de seguridad: `security-audit/v1`.

Cada peticion a la API deja uno, permitida o no, en el logger `motor_cartera.auditoria` (separado
logicamente: una plataforma de logs lo enruta a su propio destino por `schema`). Sus campos:

| Campo | Que dice |
|---|---|
| `schema` | `security-audit/v1` |
| `timestamp`, `request_id`, `trace_id` | Cuando, y con que log y traza se correlaciona |
| `actor` | `usr:<16 hex>` (HMAC del emisor y el `sub`), `svc:<nombre>` o `anonimo` |
| `actor_type` | `USUARIO`, `SERVICIO` o `ANONIMO` |
| `route`, `method` | La plantilla de la ruta y el metodo |
| `route_class`, `required_scope` | La clase de la matriz de autorizacion y el permiso que exigio |
| `decision` | `PERMITIDO`, `DENEGADO`, `NO_AUTENTICADO`, `PUBLICO` o `ERROR` |
| `reason` | El codigo estable de por que no, si no |
| `status`, `latency_ms` | El estado HTTP y cuanto tardo |

Nunca: el `sub` crudo, un correo, una IP, el user-agent, una cabecera, una cookie, un token, el
cuerpo, ni un dato de una cuenta.
"""

from __future__ import annotations

import logging

ESQUEMA = "security-audit/v1"
log = logging.getLogger("motor_cartera.auditoria")


def registrar(
    *,
    request_id: str | None,
    trace_id: str | None = None,
    actor: str | None,
    actor_tipo: str | None,
    ruta: str,
    metodo: str,
    clase: str | None,
    permiso: str | None,
    decision: str,
    motivo: str | None,
    estado: int,
    latencia_ms: float,
) -> None:
    log.info(
        "%s %s %s: %s",
        metodo,
        ruta,
        decision,
        estado,
        extra={
            "event": "security-audit",
            "request_id": request_id,
            "trace_id": trace_id,
            "status": estado,
            "duration_ms": round(latencia_ms, 2),
            "campos": {
                "schema": ESQUEMA,
                "actor": actor or "anonimo",
                "actor_type": actor_tipo or "ANONIMO",
                "route": ruta,
                "method": metodo,
                "route_class": clase,
                "required_scope": permiso,
                "decision": decision,
                "reason": motivo,
                "latency_ms": round(latencia_ms, 2),
            },
        },
    )
