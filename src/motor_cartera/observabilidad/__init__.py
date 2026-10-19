"""La observabilidad tecnica de los procesos: logs, metricas, trazas y la auditoria de seguridad.

- `logs`: JSON en produccion, con campos fijos, el request_id y el trace_id, y un filtro que redacta
  tokens y secretos.
- `metricas`: Prometheus, con etiquetas solo de listas cerradas, servidas en un puerto interno.
- `trazas`: OpenTelemetry, exportadas por OTLP en segundo plano; si el colector no responde, se
  descartan sin tocar el trabajo.
- `auditoria`: el evento `security-audit/v1` de cada peticion a la API.

Es operacion tecnica, no negocio: lo que cobra, decide o recupera la cartera esta en analytics/v1.
"""

from __future__ import annotations

import threading

from motor_cartera.config import Config

_preparado: set[str] = set()
_candado = threading.Lock()


def preparar_proceso(config: Config, servicio: str) -> None:
    """Configura, una sola vez por proceso, los logs, las trazas y el servidor de metricas del
    servicio. En el entorno de pruebas no toca nada global: las pruebas configuran lo que
    prueban."""
    if config.app_env == "test":
        return
    with _candado:
        if servicio in _preparado:
            return
        from motor_cartera.observabilidad import logs, metricas, trazas

        logs.configurar(config, servicio)
        trazas.configurar(config, servicio)
        metricas.informacion_de_construccion(servicio)
        metricas.inicializar_series(servicio)
        if config.metricas_puerto is not None:
            metricas.servir(config.metricas_puerto)
        _preparado.add(servicio)
