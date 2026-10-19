# Seguridad (v0.18)

Cómo entra cada persona y cada servicio, qué puede hacer cada uno, qué protege el edge y qué
queda registrado. Responde a las amenazas de [threat-model.md](threat-model.md); la revisión de
cierre está en [security-review-v018.md](security-review-v018.md) y el porqué de cada decisión,
en [decisiones.md](decisiones.md) (275 a 303).

## El camino de una petición

```text
navegador ──TLS 1.2/1.3──▶ edge (nginx, el único puerto publicado)
   │                         ├─ /oauth2/start, /oauth2/callback  ─▶ oauth2-proxy ─▶ el IdP (Dex en local)
   │ cookie __Host-mc_sesion ├─ /oauth2/sign_out (POST, mismo origen)
   │ (HttpOnly, Secure,      ├─ /api/...  auth_request a oauth2-proxy: ¿la cookie tiene sesión?
   │  SameSite=Lax)          │              │ sí: su token de acceso, en una cabecera interna
   │                         │              ▼
   │                         │    la API (FastAPI): valida el token, el secreto del gateway,
   │                         │    el permiso de la ruta; registra security-audit/v1
   │                         └─ /, /assets/  la SPA (sin datos)
   │
   └─ nunca ve un token, una clave ni el secreto del gateway
```

- **La sesión vive en el servidor.** oauth2-proxy guarda la sesión (los tokens del IdP) en Valkey,
  cifrada, y la cookie solo la referencia. Cerrar sesión la borra de Valkey: una cookie copiada
  antes ya no abre nada (lo prueban la prueba de humo del edge y Playwright).
- **El navegador no guarda tokens.** Ni en `localStorage`, ni en `sessionStorage`, ni en una
  cookie legible por JavaScript (Playwright lo revisa). La app pregunta quién es por
  `GET /app/v1/sesion`, que responde un seudónimo.
- **El edge borra toda identidad que traiga el cliente** (`Authorization`, `X-API-Key`,
  `X-Forwarded-*`, `X-Auth-Request-*`, `X-MC-*`, `traceparent`...) y pone dos: el token de acceso de la
  sesión (`Authorization: Bearer`) y el secreto del gateway (`X-MC-Gateway`). La API acepta un token
  de persona solo con ese secreto: un token que llegue por otro camino se niega
  (`GATEWAY_REQUERIDO`).

## El inicio de sesión: OpenID Connect

Authorization Code con PKCE (S256), `state` y `nonce`, por oauth2-proxy como BFF (backend for
frontend): la SPA nunca toca el flujo. En el compose de producción el IdP es Dex (usuarios estáticos,
contraseñas efímeras de `secretos/usuarios.json`); en un despliegue real, el del corporativo (Google,
Entra ID, Okta, Keycloak): la API solo necesita su emisor, su audiencia y su JWKS.

| Usuario de Dex | Grupo | Rol | Permisos |
| --- | --- | --- | --- |
| `lector` | `mc-lectores` | `VIEWER` | `mc.read` |
| `auditor` | `mc-auditores` | `AUDITOR` | `mc.read`, `mc.audit` |
| `operador` | `mc-operadores` | `OPERATOR` | `mc.read`, `mc.execute` |
| `administrador` | `mc-administradores` | `ADMIN` | los cuatro |
| `sinpermisos` | ninguno | — | ninguno: tiene sesión y la API responde `403` |

Lo que el edge no expone de oauth2-proxy (su página de inicio, `/oauth2/userinfo`...) es `404`. El
callback no se puede repetir (el `state` es de un solo uso) y la sesión nace en el callback: una
cookie fijada antes no la hereda (la prueba de humo lo revisa).

## La API valida el token ella misma

No confía en que el edge lo validó. Con PyJWT:

- **Firma** con una llave de la JWKS del emisor, por su `kid`, con un algoritmo de la lista
  (`MC_OIDC_ALGORITMOS`, `RS256` por omisión; solo asimétricos: `HS256` y `none` no arrancan).
- **Emisor** (`iss`) y **audiencia** (`aud`) exactos; `exp` y `nbf` con una tolerancia de reloj
  (`MC_OIDC_TOLERANCIA_SEGUNDOS`, 30 s, nunca más de 120); `sub` presente; `azp`, si se configuran
  clientes permitidos (`MC_OIDC_AZP_PERMITIDOS`); `typ`, si el IdP lo emite (`MC_OIDC_TIPO_TOKEN`).
- **La JWKS** se descarga por HTTPS (con su CA, si es propia: `MC_OIDC_CA_FILE`), hasta 1 MiB, y se
  guarda 300 s (`MC_OIDC_JWKS_TTL_SEGUNDOS`). Un `kid` desconocido fuerza una descarga, a lo más una
  cada 30 s (rotación de llaves sin martillar al IdP). Si el IdP no responde, la JWKS vieja sirve
  hasta una hora (`MC_OIDC_JWKS_ANTIGUEDAD_MAXIMA_SEGUNDOS`); después, `503
  IDENTIDAD_NO_DISPONIBLE`: falla cerrada, nunca abierta.
- **Los permisos** salen de `scope`/`scp` y de los grupos (`MC_OIDC_CLAIM_GRUPOS`, `groups` por
  omisión), mapeados a roles por `MC_OIDC_GRUPOS` (`mc-lectores=VIEWER;...`). Un grupo que no está en
  el mapa no da nada.

Un token inválido responde `401` con `WWW-Authenticate: Bearer error="invalid_token"` y un código
estable: `TOKEN_MALFORMADO`, `TOKEN_ALGORITMO_NO_PERMITIDO`, `TOKEN_TIPO_INVALIDO`, `TOKEN_SIN_KID`,
`TOKEN_CLAVE_DESCONOCIDA`, `TOKEN_FIRMA_INVALIDA`, `TOKEN_EXPIRADO`, `TOKEN_AUN_NO_VALIDO`,
`TOKEN_EMISOR_INVALIDO`, `TOKEN_AUDIENCIA_INVALIDA`, `TOKEN_SIN_SUJETO`, `TOKEN_RECLAMO_FALTANTE` o
`TOKEN_CLIENTE_NO_PERMITIDO`. Nunca el token ni el motivo criptográfico detallado.

## Los servicios: claves con permisos

Un proceso que no es una persona (la prueba de humo interna, una integración) usa una clave de
servicio en `X-API-Key`. La API guarda solo su SHA-256 (`MC_API_KEYS`, una por línea:
`nombre sha256 permisos`), la compara en tiempo constante contra todas, y cada clave tiene sus
permisos. En desarrollo existe la clave universal `MC_API_KEY`; en producción, definirla impide
arrancar.

Una petición con dos credenciales (un token y una clave) se niega (`CREDENCIALES_AMBIGUAS`): nadie
elige cuál vale.

| Código | Estado | Cuándo |
| --- | --- | --- |
| `CREDENCIAL_AUSENTE` | 401 | sin token ni clave (con OpenID Connect configurado) |
| `API_KEY_AUSENTE` | 401 | sin clave (una API sin OpenID Connect, como la de desarrollo) |
| `API_KEY_INVALIDA` | 401 | una clave que no es de ningún servicio |
| `CREDENCIALES_AMBIGUAS` | 401 | un token y una clave a la vez |
| `CREDENCIAL_NO_SOPORTADA` | 401 | `Authorization` que no es `Bearer <token>` |
| `TOKEN_NO_ACEPTADO` | 401 | un token a una API sin proveedor de identidad configurado |
| `TOKEN_...` | 401 | un token inválido, con su motivo (arriba) |
| `GATEWAY_REQUERIDO` | 401 | un token de persona que no llegó por el edge |
| `IDENTIDAD_NO_DISPONIBLE` | 503 | la JWKS no se puede obtener y la que había ya venció |
| `PERMISO_INSUFICIENTE` | 403 | una credencial válida sin el permiso de la ruta |
| `RUTA_SIN_CLASIFICAR` | 500 | una ruta fuera de la matriz (la API no arranca así; no debería verse) |

## Lo que cada uno puede hacer: la matriz

Cuatro permisos y cinco clases de ruta. Cada operación de la API tiene su clase en una matriz
explícita (`MATRIZ`, en `src/motor_cartera/api/autorizacion.py`), y la API **no arranca** si una
ruta no está en ella: una ruta nueva sin clasificar no puede quedar abierta por descuido. Al pedirla,
una ruta sin clasificar respondería `500 RUTA_SIN_CLASIFICAR`.

| Clase | Permiso | Qué es |
| --- | --- | --- |
| `PUBLIC` | ninguno | `/salud`, `/health/live`, `/health/ready` |
| `READ` | `mc.read` | leer lo publicado |
| `AUDIT` | `mc.audit` | la evidencia: rechazos, archivos fuente, observaciones crudas, la cola, el linaje |
| `EXECUTE` | `mc.execute` | pedir trabajo: ingerir, ejecutar un motor, registrar un evento |
| `CONFIGURE` | `mc.configure` | publicar configuración: bases, campo, optimización |

La matriz completa, operación por operación, está en
[matriz-autorizacion.md](matriz-autorizacion.md) (generada desde el código). Sin el permiso, `403
PERMISO_INSUFICIENTE` con `WWW-Authenticate: Bearer error="insufficient_scope" scope="..."`. El
OpenAPI declara en cada operación su esquema de seguridad y el permiso que pide.

El edge, además, solo deja pasar `GET` de su lista blanca (`web/nginx/rutas-api.conf`): la app es de
solo lectura aunque la sesión tenga `mc.execute`. Las escrituras son de la API y de la línea de
comandos, con una clave o un token con ese permiso.

## El edge

| Control | Cómo |
| --- | --- |
| TLS | 1.2 y 1.3, cifrados AEAD con ECDHE, sin tickets de sesión; certificado del dominio (en local, de la CA efímera) |
| HSTS | `max-age=63072000; includeSubDomains`, solo en TLS: en desarrollo (http) nunca |
| http | el puerto 8080 solo redirige a https (301) |
| Métodos | `GET` (y `POST` en el formulario de Dex y en cerrar sesión); otro, `405` |
| Cuerpos | 16 KiB en general, 1 KiB en `/api/`; más, `413 CUERPO_DEMASIADO_GRANDE` |
| Cabeceras | 4 KiB por línea, hasta 4 de 16 KiB (la cookie de sesión); más, `400`/`431` |
| Tiempos | 10 s para la cabecera y el cuerpo, 30 s de envío, 60 s de lectura a la API |
| Límites por IP | lecturas 30/s (ráfaga 60), lecturas caras 5/s (ráfaga 10), inicio de sesión 120/min, contraseñas 10/min (solo los `POST`), 60 conexiones |
| Al exceder | `429 DEMASIADAS_PETICIONES` con `Retry-After`, en JSON |
| Errores | JSON propios, sin trazas ni versiones (`server_tokens off`, la API sin `server`) |
| CSP | todo del mismo origen; sin `unsafe-inline` ni `unsafe-eval`; `frame-ancestors 'none'`, `object-src 'none'`, `base-uri 'none'`, `form-action 'self'` |
| Otras cabeceras | `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: same-origin`, `Permissions-Policy` sin cámara, micrófono, ubicación, pago ni USB, COOP y CORP `same-origin` |
| CORS | ninguno: todo es del mismo origen, y no se envía `Access-Control-Allow-Origin` |
| IP del cliente | el edge es el primer salto: no cree ningún `X-Forwarded-For` |
| Logs | una línea JSON por petición, sin la IP, el user-agent, la query ni las cabeceras |

Las páginas de Dex (solo en el compose local) llevan su propia CSP: su CSS en línea (`style-src
'unsafe-inline'`) y su único script en línea, permitido por su SHA-256, sin `unsafe-inline` para
scripts.

**Cerrar sesión** es un `POST` del mismo origen: `GET` es `405`, y un `Origin` que no sea el de la app
(incluido `Origin: null`) es `403 ORIGEN_NO_PERMITIDO`. La cookie es `SameSite=Lax` (otro sitio no
la manda en un `POST`) y la app pide `Referrer-Policy: same-origin`: con `no-referrer`, el navegador
manda `Origin: null` en ese `POST` y cerrar sesión fallaría. Lo encontró Playwright en v0.18
([decisión 284](decisiones.md#284-cerrar-sesión-es-un-post-del-mismo-origen-con-referrer-policy-same-origin)).

## Los secretos

- **Nunca en el entorno, en una imagen, en el compose ni en el repositorio.** Cada secreto es un
  archivo montado (Docker secrets) que la aplicación lee por su variable `*_FILE`
  (`MC_DATABASE_URL_FILE`, `MC_API_KEYS_FILE`, `MC_GATEWAY_SECRETS_FILE`, `MC_AUDITORIA_CLAVE_FILE`);
  definir la variable y su `*_FILE` a la vez no arranca. Los archivos tienen modo 0640 y el grupo
  `MC_SECRETOS_GID` (10999), que cada servicio tiene como grupo adicional.
- **En local son efímeros**: `scripts/generar_secretos_locales.py` crea una CA (su llave se
  descarta), certificados de 30 días y valores al azar, y no imprime ninguno. El CI los crea en cada
  run.
- **Cada servicio, su credencial de base**: `mc_migraciones` (dueño del esquema), `mc_api` y
  `mc_worker` (solo filas), `mc_respaldo` (solo lectura). Una no sirve por la otra.
- **La rotación** de cada uno, en [runbooks/secret-rotation.md](runbooks/secret-rotation.md). El
  secreto del gateway y las claves de servicio admiten varios a la vez: se rota sin cortar.
- **Gitleaks** revisa el historial completo en cada run del CI
  ([cadena-de-suministro.md](cadena-de-suministro.md)).

## La configuración falla cerrada

`MC_APP_ENV` es `production` por omisión: una variable olvidada no abre nada. En producción la API
no arranca sin una base explícita (sin contraseña de desarrollo), sin OpenID Connect (emisor y JWKS
por https), sin el secreto del gateway, sin la clave de auditoría (32 caracteres o más) o con
`MC_API_KEY`; ningún proceso arranca, en ningún entorno, si se define una variable que parezca
apagar la autenticación (`AUTH_DISABLED`, `MC_AUTH_DISABLED`...): ese modo no existe. La
documentación de la API (`/docs`, `/openapi.json`) no se publica en producción. El detalle está en
[production.md](production.md#la-configuración).

## La auditoría: security-audit/v1

Cada petición a la API, salvo las sondas de salud, deja un evento JSON en el logger
`motor_cartera.auditoria`:

| Campo | Qué es |
| --- | --- |
| `schema` | `security-audit/v1` |
| `timestamp`, `request_id`, `trace_id` | cuándo y cómo correlacionarlo |
| `actor` | `usr:<16 hex>` (HMAC-SHA256 del emisor y el `sub`, con la clave de auditoría) o `svc:<nombre>`; nunca un correo ni un nombre |
| `actor_type` | `USUARIO`, `SERVICIO` o `ANONIMO` |
| `route`, `method`, `route_class`, `required_scope` | la plantilla de la ruta (nunca la ruta con sus identificadores), su clase y el permiso que pide |
| `decision`, `reason` | `PERMITIDO`, `DENEGADO`, `NO_AUTENTICADO`, `PUBLICO` o `ERROR`, con su código estable |
| `status`, `latency_ms` | cómo terminó |

No lleva la query, el cuerpo, las cabeceras, la IP ni el token. Su integridad depende de la
plataforma de logs (riesgo residual).

## Los logs

JSON en producción, con campos fijos (`timestamp`, `level`, `service`, `environment`, `logger`,
`event`, `message`, `request_id`, `trace_id`, `span_id`, `duration_ms`, `status`) y un filtro que
redacta antes de escribir: JWT, `Bearer ...`, pares de credencial (`password=`, `api_key=`...),
contraseñas en URLs y cada secreto configurado. Los parámetros de SQL nunca van a un mensaje de error
(`hide_parameters`). Los loggers ruidosos (SQLAlchemy, httpx, urllib3) quedan en `WARNING`: en `INFO`
escribirían URLs con su query. Una prueba recorre los logs de una corrida completa sobre una cartera
con datos personales y falla si aparece uno (`tests/test_logs_sin_datos_personales.py`). El detalle,
en [observability.md](observability.md).

## Los contenedores

Cada servicio del compose de producción corre sin raíz (con su usuario numérico), sin capacidades
(`cap_drop: ALL`), sin escalada (`no-new-privileges`), con el sistema de archivos de solo lectura (y
tmpfs donde escribe), con límites de CPU, memoria y procesos, en sus redes internas (solo el edge en
la red pública, y solo en `127.0.0.1`). La prueba de humo lo revisa contenedor por contenedor
(`--contenedores`). El detalle, en [production.md](production.md#los-contenedores).

## Lo que no cubre

Los riesgos residuales, con su razón, están en
[threat-model.md](threat-model.md#riesgos-residuales): el tráfico interno sin cifrar, Dex solo para
desarrollo, sin bloqueo de cuentas en Dex, la auditoría sin cadena de hashes, las imágenes de
terceros escaneadas sin bloquear, sin pentest externo.
