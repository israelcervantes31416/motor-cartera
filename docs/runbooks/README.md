# Runbooks

Qué hacer cuando algo falla en el compose de producción ([production.md](../production.md)) o en la
referencia de Google Cloud ([cloud-gcp.md](../cloud-gcp.md)). Cada uno dice qué alertas lo abren, qué
impacto tiene, cómo diagnosticar, cómo mitigar y cómo verificar. Lo que dicen que se espera está
medido en los simulacros (`scripts/simulacros.py`, `scripts/simulacro_de_restauracion.py`).

En los comandos:

```bash
alias dcp='docker compose -f docker-compose.production.yml'
```

| Runbook | Cuándo |
| --- | --- |
| [service-down](service-down.md) | la API o el edge no responden, 5xx, latencia, sin métricas |
| [database-down](database-down.md) | la base no responde, la readiness falla, el pool se agota |
| [worker-stuck](worker-stuck.md) | el worker no late, no toma trabajos, leases vencidos, trabajos fallidos |
| [queue-backlog](queue-backlog.md) | la cola se atrasa |
| [osrm-down](osrm-down.md) | OSRM no responde |
| [identity-down](identity-down.md) | el IdP, la JWKS, oauth2-proxy o las sesiones |
| [backup-restore](backup-restore.md) | el respaldo falló, o hay que restaurar |
| [secret-rotation](secret-rotation.md) | rotar un secreto |
| [disaster-recovery](disaster-recovery.md) | el entorno entero se perdió o no es confiable |

Los objetivos que vigilan las alertas, en [slos.md](../slos.md); las alertas, en
[observability.md](../observability.md#las-alertas).
