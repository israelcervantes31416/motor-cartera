# Modelo de amenazas (v0.18)

Este documento se escribió **antes** de implementar los controles de v0.18 y se revisó al cerrarla
contra lo implementado ([security-review-v018.md](security-review-v018.md)). Cada control de
[security.md](security.md) responde a una amenaza de aquí; si una amenaza no tiene control, está en
los riesgos residuales, con su razón.

**Alcance.** El despliegue de producción local (`docker-compose.production.yml`) y la arquitectura
de referencia en Google Cloud ([cloud-gcp.md](cloud-gcp.md)). El compose de desarrollo
(`docker-compose.yml`) queda fuera: es una conveniencia explícita de una máquina de desarrollo, sin
identidad de usuarios, publicada solo en `127.0.0.1`, y su configuración no arranca en producción
(`MC_APP_ENV=development`). Todos los datos del proyecto son sintéticos, pero el modelo los trata
como si fueran reales: una cartera de crédito al consumo con datos personales.

**Método.** STRIDE (suplantación, alteración, repudio, divulgación, denegación de servicio y
elevación de privilegios) sobre cada frontera de confianza y cada flujo de datos que la cruza.

## Activos

| Id | Activo | Por qué importa |
|---|---|---|
| A1 | La base PostgreSQL | Cuentas, cortes, pagos, lifecycle, decisiones, planes y modelos: la cartera entera, con identificadores fuente (`CLIENTE_UNICO`), `actor_ref` y coordenadas |
| A2 | El almacén de artefactos fuente | Cada archivo recibido tal como llegó: las 93 columnas de cartera/v2 traen nombre, domicilio, teléfonos, CURP y RFC |
| A3 | Las credenciales | Contraseñas de los roles de la base, el secreto del cliente OIDC, el secreto de las cookies de sesión, el secreto del gateway, las claves de servicio, la clave de los seudónimos de auditoría y la llave privada TLS |
| A4 | La API | Ejecuta, por la cola, los motores que publican decisiones, planes y modelos: su integridad es la de cada resultado |
| A5 | La web | El bundle de la SPA: lo que corre en el navegador de cada persona |
| A6 | El worker | Toma trabajos con lease y publica resultados; dueño de cada publicación mientras late |
| A7 | OSRM y la red vial | Activo derivado: se reconstruye de un extracto de OpenStreetMap; recibe coordenadas de cuentas |
| A8 | Los artefactos de los modelos | JSON canónico con su hash: lo que puntúa una cartera |
| A9 | Los planes de optimización | Lo que se propone atender, por canal y pool |
| A10 | Los respaldos | Una copia completa de A1: lo mismo que la base, fuera de ella |
| A11 | Logs, auditoría, trazas y métricas | Si llevaran un dato personal o un token, serían una segunda copia sin los controles de la base |
| A12 | Sesiones y tokens de usuario | Una sesión robada actúa como su dueño hasta que vence |
| A13 | La configuración | Un valor por omisión peligroso en producción abre lo que el código cierra |
| A14 | El CI/CD | Workflows, acciones, imágenes base, lockfiles, el estado de la IaC: lo que construye lo que corre |
| A15 | El proveedor de identidad | Quien dice quién es cada persona; si miente, todo lo demás obedece |

## Actores

| Actor | Qué puede | Qué busca |
|---|---|---|
| Anónimo de internet | Alcanzar el edge por HTTPS | Leer datos, tumbar el servicio, forzar una sesión |
| Persona autenticada sin permiso | Una sesión válida sin `mc.read` | Leer lo que no le toca |
| Lector (`mc.read`) | Las pantallas de solo lectura | Escalar a auditoría, ejecución o configuración |
| Auditor (`mc.read` + `mc.audit`) | Además, el linaje y la evidencia | Escalar a ejecución |
| Operador (`mc.execute`) y administrador (`mc.configure`) | Rutas que escriben, solo por la red interna | Abusar de su permiso; sus acciones deben quedar atribuidas |
| Automatización interna | Una clave de servicio con sus permisos | Que su clave sirva para más de lo declarado |
| Navegador comprometido (XSS, extensión) | Ejecutar JavaScript en el origen | Robar tokens, hacer peticiones como la persona |
| Contenedor comprometido | Lo que su proceso puede en su red | Moverse a la base, a otros servicios o al host |
| Persona con acceso a la red interna | Hablar directo con la API, la base u OSRM | Saltarse el edge |
| Atacante de la cadena de suministro | Publicar una dependencia, una imagen o una acción alterada | Ejecutar código en el CI o en producción |
| Atacante del CI | Abrir un pull request desde un fork | Leer secretos, escribir en el repositorio |
| Atacante del plano de control de la nube | Credenciales de la nube robadas | Crear, borrar o leer recursos |

## Fronteras de confianza

```text
 navegador ──TB1 (internet, TLS)──► edge (nginx) ──TB2──► oauth2-proxy ──TB6──► IdP (Dex en local)
                                       │
                                       └──TB3 (red frontal: gateway + JWT)──► API ──TB4 (red de datos)──► PostgreSQL
                                                                                │                         ▲
                                                                   TB7 (telemetría)                  worker ──TB5 (red de ruteo)──► OSRM
 CI (GitHub Actions) ──TB8──► repositorio, imágenes      humanos/CI ──TB9 (WIF, IaC)──► plano de control de GCP
```

| Frontera | Entre | Lo que la cruza |
|---|---|---|
| TB1 | Navegador y edge | La SPA, la cookie de sesión, `GET /api/*`, el flujo OIDC |
| TB2 | Edge y oauth2-proxy | Las subpeticiones de autenticación y el token de acceso que devuelven |
| TB3 | Edge y API | Peticiones `GET` con el token del usuario y la credencial del gateway |
| TB4 | API, worker, migraciones y respaldo con PostgreSQL | SQL con credenciales por rol |
| TB5 | Worker y OSRM | Coordenadas de cuentas y matrices de tiempos |
| TB6 | oauth2-proxy, API y navegador con el IdP | Autorización, canje del código, JWKS |
| TB7 | API y worker con la observabilidad | Logs, métricas y trazas |
| TB8 | CI y GitHub | El código, el token del workflow, las acciones y las imágenes |
| TB9 | Humanos y CI con la nube | La IaC y un despliegue futuro |

## Puntos de entrada

| Id | Punto | Quién lo alcanza |
|---|---|---|
| E1 | `https://<host>:8443`: la SPA, `/api/*` (solo GET de la lista blanca), `/oauth2/*`, `/dex/*` | Cualquiera en internet (en local, `127.0.0.1`) |
| E2 | `http://<host>:8081`: solo redirige a HTTPS | Igual que E1 |
| E3 | La API en `api:8000`: todas sus rutas, también las que escriben | Solo la red frontal (el edge) y quien opere dentro de la red |
| E4 | Métricas en `api:9464` y `worker:9465` | Solo la red de observabilidad |
| E5 | PostgreSQL en `postgres:5432` | Solo la red de datos |
| E6 | OSRM en `osrm:5000` | Solo la red de ruteo |
| E7 | Dex en `dex:5556` | La red de identidad; el navegador, por `/dex/` del edge |
| E8 | La línea de comandos (`docker compose exec`) | Quien opera el host |
| E9 | Los workflows (`pull_request`, `push`) y el de despliegue (`workflow_dispatch`) | Colaboradores; un fork solo el primero, sin secretos |
| E10 | El directorio de respaldos | Quien opera el host o el bucket |

## Amenazas y controles

### Suplantación (S)

| Id | Amenaza | Control | Prueba |
|---|---|---|---|
| S1 | Un token forjado: `alg=none`, firmado con otra llave, HS256 con la llave pública como secreto, de otro emisor o para otra audiencia | La API valida emisor exacto, audiencia, firma con la JWKS del emisor, una lista cerrada de algoritmos asimétricos, `exp`, `nbf` e `iat` con tolerancia acotada, y exige `kid` | `tests/test_seguridad_tokens.py` |
| S2 | Cabeceras de identidad inventadas por el cliente (`X-MC-Subject`, `X-Forwarded-User`, `X-Auth-Request-Groups`…) | La API no confía en ninguna cabecera de identidad: solo en un token firmado; el edge además las borra | `tests/test_api_autorizacion.py`, prueba de humo `--produccion` |
| S3 | Saltarse el edge y hablarle directo a la API con un token robado | En producción un token de usuario solo vale con la credencial del gateway, que solo el edge tiene; la API no se publica al host | `tests/test_api_autorizacion.py`, prueba de humo `--interna` |
| S4 | Fijación de sesión: imponer una cookie antes del login | La cookie de sesión nace en el callback, cifrada y firmada por oauth2-proxy; una cookie inventada no autentica | Playwright `autenticacion.spec.ts`, prueba de humo `--produccion` |
| S5 | Un callback con un `state` o un `nonce` ajenos | oauth2-proxy compara `state` con su cookie CSRF y exige el `nonce` (`--insecure-oidc-skip-nonce=false`) | Prueba de humo `--produccion` |
| S6 | Una clave de servicio filtrada que sirve para todo | Claves por servicio, con permisos declarados, guardadas por su SHA-256, rotables; la clave universal de desarrollo no arranca en producción | `tests/test_seguridad_credenciales.py`, `tests/test_config_produccion.py` |

### Alteración (T)

| Id | Amenaza | Control | Prueba |
|---|---|---|---|
| T1 | Un respaldo alterado o truncado | El manifiesto lleva el SHA-256 y el tamaño del volcado; restaurar los verifica antes de tocar la base | `tests/test_respaldo.py`, simulacro de restauración |
| T2 | CSRF: que otro sitio haga actuar al navegador | La superficie web solo deja pasar `GET`; cerrar sesión es `POST`, con cookie `SameSite=Lax` y revisión de `Origin` | Prueba de humo `--produccion` |
| T3 | Un `X-Request-ID` inventado para confundir la bitácora | El edge lo reemplaza siempre; la API solo acepta uno con forma válida | `tests/test_observabilidad.py` |
| T4 | Una dependencia, imagen o acción alterada | Lockfiles (`uv.lock`, `package-lock.json`), imágenes por digest, acciones por SHA, escaneo con Trivy, pip-audit y npm audit, SBOM | `scripts/verificar_cadena_de_suministro.py` en el CI |
| T5 | Alterar el tráfico entre navegador y edge | TLS 1.2+ (1.3 preferido), HSTS en el perfil de producción | Prueba de humo `--produccion` |
| T6 | Que dos réplicas migren a la vez y dejen el esquema a medias | Migrar es un paso aparte, con un candado consultivo de PostgreSQL | `tests/test_migraciones_candado.py` |

### Repudio (R)

| Id | Amenaza | Control | Prueba |
|---|---|---|---|
| R1 | Negar haber pedido algo | Un evento `security-audit/v1` por petición: actor seudónimo (HMAC con su propia clave), ruta, método, decisión, permiso requerido, estado y latencia | `tests/test_auditoria.py` |
| R2 | Un trabajo que nadie sabe quién ejecutó | La cola guarda el worker, sus intentos y su lease (v0.5); el log del worker lo correlaciona por traza | `tests/test_orquestacion_worker.py` |

### Divulgación (I)

| Id | Amenaza | Control | Prueba |
|---|---|---|---|
| I1 | Datos personales o tokens en logs, auditoría o trazas | Logs JSON con campos fijos, la plantilla de la ruta y nunca la query; filtro que redacta JWT, `Bearer` y secretos; `hide_parameters` en SQLAlchemy; trazas sin query string ni parámetros | `tests/test_logs_sin_datos_personales.py` |
| I2 | Métricas que filtran identificadores o explotan en cardinalidad | Etiquetas solo de listas cerradas (plantilla de ruta, método, clase de estado, tipo de trabajo) | `tests/test_metricas.py` |
| I3 | Un traceback, SQL o rutas internas en una respuesta | Un solo manejador de errores; 500 genérico | `tests/test_api_base.py` |
| I4 | La documentación de la API y su OpenAPI expuestos | Desactivados en producción | `tests/test_config_produccion.py` |
| I5 | Secretos en Git, en una imagen o en el frontend | Gitleaks sobre la historia; secretos como archivos montados; `.dockerignore`; Trivy busca secretos en las imágenes; el navegador no recibe ningún token | CI, `tests/test_cadena_de_suministro.py` |
| I6 | Métricas, base, OSRM o la API alcanzables desde fuera | Solo el edge publica puertos, y solo en `127.0.0.1`; redes separadas | Prueba de humo `--produccion` (puertos) |
| I7 | Coordenadas que salen de la red propia | OSRM autoalojado, en la red de ruteo, sin salida a internet | Compose de producción (red interna) |
| I8 | Un respaldo con la cartera entera | En la nube, cifrado en reposo por la plataforma, bucket privado con acceso uniforme; en local, datos sintéticos | `scripts/verificar_iac.py` |
| I9 | Versiones del software anunciadas | `server_tokens off` en nginx; uvicorn sin cabecera `server` | Prueba de humo `--produccion` |

### Denegación de servicio (D)

| Id | Amenaza | Control | Prueba |
|---|---|---|---|
| D1 | Una ráfaga de peticiones | Límites por IP en el edge (lecturas, lecturas caras, autenticación), conexiones por IP, 429 con `Retry-After` | Prueba de humo `--produccion`, prueba de carga |
| D2 | Cuerpos o cabeceras enormes, conexiones lentas | Tope de cuerpo (413), de cabeceras y tiempos de espera en el edge; tope de cuerpo en la API | `tests/test_api_limites.py`, prueba de humo |
| D3 | El IdP caído | La API sigue validando con la JWKS en cache hasta su límite de antigüedad; después, cerrado (503) | `tests/test_seguridad_tokens.py` |
| D4 | PostgreSQL caído | `ready` falla y `live` no: ningún orquestador reinicia en bucle un proceso sano | Simulacro `base` |
| D5 | La telemetría caída | Exportación en segundo plano con lotes; si el colector no responde, se descarta sin tocar la petición | `tests/test_observabilidad.py`, simulacro `telemetria` |
| D6 | Logs que llenan el disco | Driver `local` de Docker con tamaño y archivos acotados | Compose de producción |
| D7 | Un contenedor que consume la máquina | Límites de memoria, CPU y procesos por servicio | Compose de producción |

### Elevación de privilegios (E)

| Id | Amenaza | Control | Prueba |
|---|---|---|---|
| E1 | Una ruta sin autorización | Cada ruta está en la matriz de autorización; una ruta sin clasificar impide que la API arranque | `tests/test_api_autorizacion.py` |
| E2 | Un token válido sin el permiso de la ruta | Cada ruta exige su permiso (`mc.read`, `mc.audit`, `mc.execute`, `mc.configure`): 403 si falta | `tests/test_api_autorizacion.py` |
| E3 | Una ruta que escribe expuesta por la web | El edge solo deja pasar `GET` de su lista blanca; la API además exige el permiso | `tests/test_superficie_web.py`, prueba de humo |
| E4 | Escapar de un contenedor o ganar root en él | Usuarios no root, `cap_drop: ALL`, `no-new-privileges`, sistema de archivos de solo lectura, sin socket de Docker | Prueba de humo `--interna`, simulacros |
| E5 | Un proceso de la API que cambia el esquema o vacía tablas | Roles de base por servicio: la API y el worker solo DML; DDL, solo las migraciones | `tests/test_roles_base.py`, compose de producción |
| E6 | Un pull request que escala en el CI | Permisos mínimos por workflow, `persist-credentials: false`, acciones fijadas por SHA, sin secretos para forks | `scripts/verificar_cadena_de_suministro.py` |
| E7 | Credenciales de la nube de larga vida | GitHub OIDC con Workload Identity Federation; sin llaves de cuentas de servicio; sin `roles/editor` ni `roles/owner` | `scripts/verificar_iac.py` |

## Riesgos residuales

Lo que v0.18 no cierra, dicho como es:

- **El tráfico interno no va cifrado.** Entre el edge, la API, la base y OSRM, dentro de redes de
  Docker aisladas (en la nube, una VPC privada). La API autentica cada petición y la base cada rol,
  pero alguien dentro de la red interna podría leer ese tráfico.
- **Dex es solo de desarrollo y pruebas.** Usuarios estáticos con contraseñas efímeras. En
  producción el IdP es externo (Google, Entra ID, Okta, Keycloak…): la API no depende de cuál.
- **Dex emite el token de acceso con la misma forma que el ID token.** Sin `typ: at+jwt` (RFC 9068)
  no se distinguen; la API puede exigirlo con un IdP que lo emita (`MC_OIDC_TIPO_TOKEN`).
- **El token de acceso vive hasta su `exp`.** Las sesiones viven en el servidor (Valkey; en la
  referencia de GCP, Memorystore) y cerrar sesión las borra de ahí: una cookie copiada antes ya no
  abre nada (lo prueban la prueba de humo del edge y Playwright). Lo que no se revoca es un token de
  acceso que ya salió hacia la API dentro de una petición: su vida corta (15 minutos en Dex) acota
  esa ventana.
- **Dex no bloquea una cuenta por intentos fallidos.** El edge limita los envíos del formulario de
  contraseñas por IP (10 por minuto, ráfaga de 10); contra intentos distribuidos, la política de
  bloqueo es la del IdP corporativo.
- **Los límites son por IP.** Detrás de un NAT, muchas personas comparten límite; una red de bots
  distribuida no se frena con ellos. Sin WAF en el compose (Cloud Armor en la referencia).
- **La auditoría no es a prueba de manipulación.** Es un log estructurado; su integridad depende de
  la plataforma de logs (retención y acceso), no de una cadena de hashes.
- **El secreto del gateway queda en la configuración renderizada de nginx**, en un tmpfs del
  contenedor del edge. Quien lea la memoria de ese contenedor lo tiene.
- **En Windows no se controlan los permisos de los archivos secretos montados.** En Linux (el CI)
  se verifican.
- **Una sola PostgreSQL, sin réplica**, en el compose: no hay alta disponibilidad. La referencia de
  GCP la puede tener (Cloud SQL regional), con su costo.
- **La observabilidad no tiene autenticación dentro de su red.** Sus interfaces no se publican por
  omisión.
- **Las claves de servicio son credenciales al portador.** Quien la tiene, la usa, dentro de sus
  permisos.
- **Las imágenes de terceros se escanean sin bloquear.** PostgreSQL con PostGIS (y su `gosu`), Dex,
  oauth2-proxy, Valkey y OSRM van fijadas por su digest; el CI informa sus vulnerabilidades altas y
  críticas corregidas en el resumen del run, y se atienden actualizando el digest cuando su proyecto
  publica una imagen nueva. Las tres imágenes propias sí bloquean el CI.
- **Las imágenes no son reproducibles bit por bit.** Sus dependencias de Python y de Node salen de
  lockfiles con hashes y sus bases van fijadas por digest, pero las correcciones de seguridad de
  Alpine se aplican al construir (`apk upgrade`), y dos construcciones en días distintos pueden
  diferir en esos paquetes ([cadena-de-suministro.md](cadena-de-suministro.md)).
- **La referencia de GCP no está completa para desplegarse.** Le falta la plantilla del edge para
  correr detrás del balanceador, entre otras cosas ([cloud-gcp.md](cloud-gcp.md)); nunca se
  aplicó.
- **No hubo pentest externo.** Hubo modelado de amenazas, endurecimiento, pruebas automáticas de
  seguridad, simulacros y escaneo ([security-review-v018.md](security-review-v018.md)).
