# Runbook: rotar un secreto

En los comandos, `dcp` es `docker compose -f docker-compose.production.yml` ([índice](README.md)).

Cuándo: por calendario, cuando alguien con acceso deja el equipo, o ante la sospecha de que uno se
filtró (en ese caso, primero se rota y después se investiga). Ningún secreto vive en el repositorio,
en una imagen ni en el entorno: son archivos montados ([security.md](../security.md#los-secretos)).
En la nube, cada uno es un secreto de Secret Manager: rotar es agregar una versión nueva y desplegar
una revisión (o reiniciar el job); la versión vieja se deshabilita al terminar.

| Secreto | Cómo se rota sin cortar |
| --- | --- |
| `gateway_secretos` | admite varios, uno por línea: el edge presenta el **primero**, la API acepta todos. 1) agregar el nuevo como segunda línea y reiniciar la API; 2) ponerlo primero y reiniciar el edge; 3) quitar el viejo y reiniciar la API |
| `api_claves` (claves de servicio) | admite varias: agregar la nueva (`nombre sha256 permisos`; nunca la clave en claro), reiniciar la API, cambiar al cliente, quitar la vieja, reiniciar |
| `dsn_*` / `db_*` (contraseñas de la base) | por rol: `ALTER ROLE mc_api PASSWORD '...'` como superusuario, actualizar su `dsn_api`, reiniciar el servicio. Cada servicio tiene su rol: se rota uno sin tocar los demás |
| `auditoria_clave` | cambia los seudónimos: el mismo usuario tendrá otro `usr:...` desde ese momento. Rotarla parte la correlación de la auditoría en dos; anotar el momento |
| `oauth2_proxy_cfg` (secreto del cliente, de la cookie y de Valkey) | el del cliente, primero en el IdP; el de la cookie invalida todas las sesiones (cada persona inicia sesión otra vez); el de Valkey, junto con `valkey_conf` |
| `edge_tls_*`, `dex_tls_*` | un certificado nuevo antes de que venza el viejo, reiniciar el servicio. En la nube, el certificado del balanceador lo renueva Google |
| `dex_entorno`, `usuarios.json` | solo existen en el entorno local: se regeneran |
| `grafana_admin` | cambiarlo en Grafana y en el archivo |

En el entorno local, todo de una vez: `python scripts/generar_secretos_locales.py secretos
--reemplazar` y volver a levantar el compose. Eso cambia también la contraseña de los roles: con una
base ya inicializada, hay que aplicarla con `ALTER ROLE` (o empezar con un volumen nuevo y
restaurar, [backup-restore.md](backup-restore.md)).

## Verificación

- La prueba de humo del edge (inicio de sesión, la API por el edge) y la interna (la clave de
  servicio, la API sin el gateway se niega).
- `dcp logs api | grep -i gateway` sin rechazos inesperados.
- Gitleaks en el CI: ningún secreto en el historial.
