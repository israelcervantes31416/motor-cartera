# El despliegue de producción local (v0.18)

`docker-compose.production.yml` levanta motor-cartera como se desplegaría: con TLS, usuarios de
verdad (OpenID Connect), permisos por ruta, secretos montados, contenedores endurecidos, redes
separadas, observabilidad, respaldos y su restauración. Corre en una máquina con Docker; la
referencia en la nube es [cloud-gcp.md](cloud-gcp.md). La seguridad, en [security.md](security.md).

**Todo es sintético y efímero.** Los datos salen del generador; los secretos, de
`scripts/generar_secretos_locales.py`; los usuarios, de Dex. No es un entorno para datos reales: para
eso faltan un IdP corporativo, un dominio y certificados reales, y lo que dicen los riesgos
residuales de [threat-model.md](threat-model.md#riesgos-residuales).

## Arranque

```bash
# 1. Los secretos efimeros (una vez; --reemplazar los vuelve a generar)
uv run --extra dev python scripts/generar_secretos_locales.py secretos
# En Linux, el grupo con que los servicios los leen (MC_SECRETOS_GID, 10999 por omision):
sudo chgrp -R 10999 secretos

# 2. El compose
docker compose -f docker-compose.production.yml up -d --build --wait

# 3. La app: https://motor-cartera.localhost:8443 (el navegador confia en la CA de
#    secretos/ca_certificado si se importa; si no, avisa del certificado)
```

`motor-cartera.localhost` resuelve a la propia máquina en los navegadores (RFC 6761): no hace falta
tocar `/etc/hosts`. Se usa ese nombre y no `localhost` para que HSTS (que el edge manda) no alcance a
otros servicios de `localhost`. Los usuarios y sus contraseñas están en `secretos/usuarios.json`
([security.md](security.md#el-inicio-de-sesión-openid-connect)).

Sin datos, la app solo tiene su selector vacío. Para tener un contexto completo: la prueba de humo
del compose de desarrollo, un respaldo y su restauración aquí
([backups.md](backups.md#de-desarrollo-a-producción)), que es lo que hace el CI.

## Los servicios

| Servicio | Imagen | Redes | Qué hace |
| --- | --- | --- | --- |
| `edge` | `motor-cartera-web:produccion` (nginx sin privilegios) | pública, frontal, identidad | TLS, la SPA, el proxy de solo lectura, los límites; el único con puertos (`127.0.0.1:8443` y `127.0.0.1:8081`) |
| `oauth2-proxy` | v7.15.5 | identidad, sesiones | el inicio de sesión (PKCE) y la sesión |
| `valkey` | 8.1.10 | sesiones | el almacén de sesiones, con usuario y contraseña (ACL) |
| `dex` | v2.45.1 | identidad | el IdP de desarrollo y pruebas, por TLS |
| `api` | `motor-cartera:produccion` | frontal, identidad, datos, observabilidad | la API (uvicorn, un proceso) |
| `worker` | `motor-cartera:produccion` | datos, ruteo, observabilidad | la cola durable |
| `migraciones` | `motor-cartera:produccion` | datos | `alembic upgrade head`, con candado, antes de la API y el worker |
| `postgres` | PostGIS 16-3.5 | datos | la base; un rol por servicio |
| `osrm` (perfil `ruteo`) | v26.9.0 | ruteo | el ruteo vial, con la red de `MC_RED_VIAL_HUELLA` |
| perfil `observabilidad` | colector OTel, Jaeger, Prometheus, Alertmanager, Grafana | observabilidad | [observability.md](observability.md) |
| `respaldo`, `restauracion` (perfiles) | `motor-cartera-respaldo:local` | datos | [backups.md](backups.md) |
| `herramientas` (perfil) | `motor-cartera:produccion` | todas las internas | la prueba de humo de adentro |

Las redes internas (`internal: true`) no salen a Internet ni se ven desde el host: la base, OSRM,
Valkey, Dex, la API y la observabilidad no publican nada.

## Variables del compose

| Variable | Por omisión | Qué es |
| --- | --- | --- |
| `MC_SECRETOS_DIR` | `./secretos` | de dónde se montan los secretos |
| `MC_SECRETOS_GID` | `10999` | el grupo que lee los secretos |
| `MC_OTEL_ENDPOINT` | vacío | `http://otel-collector:4318` con el perfil `observabilidad`; vacío, no se exporta ninguna traza |
| `MC_OTEL_MUESTREO` | `1.0` | la fracción de trazas por cabeza (el colector muestrea por cola) |
| `MC_OTEL_PORCENTAJE` | `100` | el porcentaje de trazas sin error ni lentitud que guarda el colector |
| `MC_RED_VIAL_HUELLA` | `sin-red` | la red vial que sirve OSRM (`datos/red_vial/<huella>`) |
| `MC_RESPALDOS_DIR` | `./respaldos` | dónde escribe el respaldo y lee la restauración |
| `MC_RESPALDO_USUARIO` | `70:70` | el usuario del respaldo; en Linux, `$(id -u):$(id -g)` (dueño de `./respaldos`) |
| `MC_EDGE_LECTURAS_POR_SEGUNDO`, `..._RAFAGA` | 30, 60 | el límite de lecturas por IP |
| `MC_EDGE_COSTOSAS_POR_SEGUNDO`, `..._RAFAGA` | 5, 10 | el de las lecturas caras (resumen, distribución, agregados) |
| `MC_EDGE_AUTENTICACION_POR_MINUTO` | 120 | el de inicio y cierre de sesión |
| `MC_EDGE_CONTRASENAS_POR_MINUTO` | 10 | el de los envíos del formulario de contraseñas |
| `MC_EDGE_CONEXIONES_POR_IP` | 60 | conexiones simultáneas por IP |
| `MC_RED_IDENTIDAD_SUBRED` | `10.203.41.0/24` | la subred de la red de identidad (oauth2-proxy confía en el edge por ella) |

## La configuración

La aplicación se configura con variables `MC_*` y lee cada secreto de un archivo (`*_FILE`). En el
entorno `production` (el de omisión: `MC_APP_ENV` falta = producción) la API **no arranca** si:

- `MC_DATABASE_URL` (o `_FILE`) no está, o su contraseña está vacía o es de desarrollo;
- falta OpenID Connect (`MC_OIDC_ISSUER`, `MC_OIDC_AUDIENCE`, `MC_OIDC_JWKS_URL`), o el emisor o la
  JWKS no son https, o `MC_OIDC_ALGORITMOS` nombra uno simétrico;
- falta `MC_GATEWAY_SECRETS` (o `_FILE`), o `MC_EXIGIR_GATEWAY=false`;
- falta `MC_AUDITORIA_CLAVE` (o `_FILE`) o tiene menos de 32 caracteres;
- `MC_API_KEY` (la clave universal de desarrollo) está definida;
- alguna clave de `MC_API_KEYS` o algún secreto del gateway no tiene forma, o `MC_OIDC_GRUPOS` no se
  entiende.

Y ningún proceso arranca, en ningún entorno, si está definida una variable que parezca apagar la
autenticación (`AUTH_DISABLED`, `MC_AUTH_DISABLED`, `MC_AUTH_DESHABILITADA`,
`MC_SIN_AUTENTICACION`, `MC_DESACTIVAR_AUTENTICACION`). El mensaje nombra la variable, nunca su
valor. `development` y `test` existen solo para el compose de desarrollo y las pruebas, y se
declaran.

| Variable | Qué es |
| --- | --- |
| `MC_APP_ENV` | `production` (omisión), `development` o `test` |
| `MC_DATABASE_URL_FILE` | la URL de la base del servicio |
| `MC_API_KEYS_FILE` | las claves de servicio: `nombre sha256 permisos`, una por línea |
| `MC_GATEWAY_SECRETS_FILE` | los secretos del gateway, uno por línea (el primero es el vigente del edge; la API acepta todos durante una rotación) |
| `MC_AUDITORIA_CLAVE_FILE` | la clave de los seudónimos de auditoría |
| `MC_OIDC_ISSUER`, `MC_OIDC_AUDIENCE`, `MC_OIDC_JWKS_URL`, `MC_OIDC_CA_FILE` | el IdP |
| `MC_OIDC_ALGORITMOS`, `MC_OIDC_TOLERANCIA_SEGUNDOS`, `MC_OIDC_AZP_PERMITIDOS`, `MC_OIDC_TIPO_TOKEN` | las reglas del token |
| `MC_OIDC_JWKS_TTL_SEGUNDOS`, `..._ANTIGUEDAD_MAXIMA_SEGUNDOS`, `..._REFRESCO_MINIMO_SEGUNDOS`, `..._TIMEOUT_SEGUNDOS` | la caché de la JWKS |
| `MC_OIDC_CLAIM_GRUPOS`, `MC_OIDC_GRUPOS` | de dónde salen los grupos y su mapeo a roles |
| `MC_TAMANO_MAXIMO_CUERPO_KB` | el cuerpo más grande que la API acepta (8 MiB; las subidas tienen su propio tope) |
| `MC_LOG_FORMATO`, `MC_LOG_NIVEL` | `json` (producción) o `texto`; el nivel |
| `MC_METRICAS_PUERTO` | el puerto interno de métricas (y de la salud del worker) |
| `MC_OTEL_ENDPOINT`, `MC_OTEL_MUESTREO` | las trazas |

## La salud

| Ruta | Qué dice | Quién la usa |
| --- | --- | --- |
| API `/health/live` | el proceso responde | la sonda de Docker de la imagen |
| API `/health/ready` | la base contesta (con un tope de 2 s) y su esquema es el de esta versión (`0017`) | el `healthcheck` del compose y el edge (por dependencia) |
| API `/salud` | la de v0.5, sin cambios | compatibilidad |
| worker `:9465/health/live` | su ciclo late (cada vuelta, y el latido del trabajo en curso); más de 60 s sin latir es `503` | el `healthcheck` del worker |
| worker `:9465/health/ready` | la cola se puede usar y el esquema es el esperado; informa OSRM sin depender de él | Prometheus y los simulacros |
| edge `/salud-web` | nginx responde | el `healthcheck` del edge |

Una base caída deja a la API y al worker vivos y no listos, sin reiniciarlos; al volver, se
recuperan solos (medido en [los simulacros](#los-simulacros)).

**Apagado ordenado.** La API recibe `SIGTERM` y termina sus peticiones en curso (hasta 20 s,
`--timeout-graceful-shutdown`); el worker termina el trabajo en curso (hasta 30 s,
`stop_grace_period`) y, si no alcanza, el trabajo vuelve a la cola cuando vence su lease. Los dos
salen con código 0.

## Los contenedores

| | Usuario | Sistema de archivos | Límites (CPU, memoria, procesos) |
| --- | --- | --- | --- |
| api | 10001 | solo lectura, tmpfs en `/tmp`, el almacén en su volumen | 2, 1 GiB, 256 |
| worker | 10001 | solo lectura, tmpfs en `/tmp`, el almacén en su volumen | 2, 1.5 GiB, 256 |
| migraciones | 10001 | solo lectura | 1, 512 MiB, 64 |
| edge | 101 | solo lectura, tmpfs para la configuración renderizada y la caché | 1, 128 MiB, 128 |
| oauth2-proxy | el sin privilegios de su imagen | solo lectura | 0.5, 96 MiB, 64 |
| valkey | 999 | solo lectura, sin persistencia | 0.5, 96 MiB, 64 |
| dex | el sin privilegios de su imagen | solo lectura | 1, 128 MiB, 64 |
| postgres | 70 | solo lectura, salvo su volumen y tmpfs | 2, 1 GiB, 256 |
| osrm | 65534 | solo lectura, la red montada de solo lectura | 2, 1 GiB, 128 |

Todos con `cap_drop: ALL`, `no-new-privileges`, logs rotados (`local`, 3 de 10 MiB) y `restart:
unless-stopped`. La imagen de la API no tiene compilador ni `uv` (la etapa final solo copia el
entorno virtual), corre `uvicorn` sin `server` ni registros de acceso propios (los escribe
Observacion, sin datos) y sin confiar en `X-Forwarded-*`.

**Memoria medida** (`docker stats`, en reposo, 2026-10-18): unos 580 MiB sin el perfil de
observabilidad (la API ~215 MiB, el worker ~228 MiB, PostgreSQL ~91 MiB, el resto menos de 20 MiB
cada uno) y unos 835 MiB con él.

## Capacidad

Una API (un proceso de uvicorn) sostuvo 25 lecturas por segundo de la mezcla de la app con un p95 de
121 ms; el edge suma unos 3 ms ([carga.md](carga.md)). Para más, se escala hacia afuera:
`docker compose -f docker-compose.production.yml up -d --scale api=2` (el edge balancea entre las
réplicas: su `upstream` resuelve el nombre del servicio; Prometheus descubre cada una por DNS). Los
workers también escalan: dos nunca toman el mismo trabajo.

## Cómo se verifica

| Prueba | Qué revisa | Comando |
| --- | --- | --- |
| del edge | TLS y sus versiones, HSTS, la redirección, cabeceras y CSP, los límites y su `429`, los cuerpos y las cabeceras grandes, el inicio de sesión de cada usuario y sus permisos, la falsificación de identidades, la fijación de sesión, la repetición del callback, cerrar sesión (CSRF, `Origin: null`, la revocación en el servidor) y que nada más esté publicado | `python scripts/prueba_de_humo_produccion.py --edge https://motor-cartera.localhost:8443 --ca secretos/ca_certificado --usuarios secretos/usuarios.json --claves secretos/claves_humo` |
| interna | la salud, la API sin el edge (un token sin el gateway se niega), un trabajo de verdad con los roles mínimos, las métricas, OSRM, la identidad y la observabilidad | `docker compose -f docker-compose.production.yml --profile herramientas run --rm herramientas` |
| de los contenedores | usuario, capacidades, solo lectura, límites, redes, puertos publicados y los permisos de cada secreto montado | `python scripts/prueba_de_humo_produccion.py --contenedores` |
| E2E | las pantallas por el edge con sesiones de Dex, la sesión, axe, el móvil y la privacidad | `MC_E2E_PRODUCCION=1 npx playwright test` (en `web/`) |
| simulacros | ver abajo | `python scripts/simulacros.py` |
| carga | [carga.md](carga.md) | `python scripts/carga.py --escenario api` |
| restauración | [backups.md](backups.md) | `python scripts/simulacro_de_restauracion.py respaldos/<respaldo>` |

El CI (job `produccion`) corre todo esto en una máquina limpia, sobre la restauración del respaldo
del job del compose de desarrollo.

## Los simulacros

`scripts/simulacros.py` rompe una cosa a la vez, mide y la repone, solo con contenedores de este
proyecto. Corrida del 2026-10-18 en la máquina local:

| Simulacro | Lo que pasó |
| --- | --- |
| PostgreSQL se detiene | la API y el worker dejan de estar listos en 0.6 s, siguen vivos, la readiness no expone la conexión, y al volver la base se recuperan solos en 1.1 s, sin reiniciarse |
| la API se reinicia | `SIGTERM`: termina con código 0 en 2.0 s; el worker sigue listo sin ella; lista otra vez en 8.0 s |
| el worker se detiene | código 0 en 1.3 s; una corrida que llega sin worker espera `EN_PROCESO` y queda `EXITOSA` 5.1 s después de que vuelve |
| OSRM no responde | el worker sigue listo e informa el ruteo no disponible (`mc_ruteo_disponible 0`) en 2.7 s; disponible otra vez 9.4 s después de que vuelve |
| el colector de telemetría se cae | la API responde 40 de 40 peticiones con su latencia (mediana 2.9 ms con colector, 2.7 ms sin él) y sigue sirviendo métricas; al volver el colector, las trazas llegan otra vez en 11 s |

Los runbooks de cada falla están en [runbooks/](runbooks/).
