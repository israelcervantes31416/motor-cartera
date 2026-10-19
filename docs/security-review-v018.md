# Revisión de seguridad de v0.18

La revisión de cierre de v0.18, antes de fusionarla: lo que se revisó, con qué evidencia, lo que se
encontró y se corrigió en el camino, y lo que se acepta. El modelo de amenazas es
[threat-model.md](threat-model.md) (escrito antes de implementar); los controles, en
[security.md](security.md). Fecha: 2026-10-18.

## Lo revisado

| Área | Qué se revisó | Evidencia |
| --- | --- | --- |
| Configuración | producción por omisión; no arranca sin OIDC, gateway, clave de auditoría o base explícita, ni con `MC_API_KEY` o una variable que "apague" la autenticación | `tests/test_config_produccion.py` |
| Tokens | firma, algoritmos (sin `none` ni `HS256`), `iss`, `aud`, `exp`, `nbf`, `kid`, `sub`, `azp`, `typ`; la JWKS con su caché, la rotación de llaves y su falla cerrada | `tests/test_seguridad_tokens.py` |
| Autorización | cada operación en la matriz (161), la API no arranca con una sin clasificar, cada permiso y cada rol, credenciales ambiguas, el gateway exigido | `tests/test_api_autorizacion.py`, [matriz-autorizacion.md](matriz-autorizacion.md) |
| Sesión | el flujo OIDC completo por usuario, la cookie (`__Host-`, `HttpOnly`, `Secure`, `SameSite`), la fijación de sesión, la repetición del callback, cerrar sesión (CSRF, `Origin: null`, la revocación en el servidor) | la prueba de humo del edge (81 comprobaciones), `web/e2e/sesion.spec.ts` |
| Suplantación | cabeceras de identidad de un cliente (`X-Forwarded-*`, `X-Auth-Request-*`, `X-MC-*`), un token sin el gateway, una clave en el navegador | la prueba de humo del edge y la interna |
| Edge | TLS 1.2/1.3 (1.1 se rechaza), HSTS, la redirección, métodos, cuerpos, cabeceras, tiempos, límites con `429` y `Retry-After`, errores sin trazas ni versiones, la CSP y sus cabeceras | la prueba de humo del edge, `web/e2e/` |
| Datos personales | ningún log, métrica, traza, evento de auditoría ni respuesta de la app con uno | `tests/test_logs_sin_datos_personales.py`, `tests/test_observabilidad.py`, `tests/test_api_app_v1.py`, `web/e2e/privacidad.spec.ts` |
| Contenedores | usuario, capacidades, solo lectura, límites, redes, puertos publicados y los permisos de cada secreto | la prueba de humo de los contenedores (130 comprobaciones) |
| Secretos | ninguno en el repositorio ni en su historial, en una imagen, en el entorno, en un log | gitleaks (158 commits), `scripts/generar_secretos_locales.py` |
| Cadena de suministro | imágenes por digest, acciones por SHA, permisos de los workflows, lockfiles con hashes; vulnerabilidades conocidas | `scripts/verificar_cadena_de_suministro.py`, pip-audit, npm audit, Trivy ([cadena-de-suministro.md](cadena-de-suministro.md)) |
| Respaldos | lo respaldado (base y almacén), la verificación antes de tocar nada, la restauración solo en un entorno nuevo y validada | `tests/test_respaldo.py`, `scripts/simulacro_de_restauracion.py` |
| Nube | sin roles amplios ni miembros públicos, datos privados, imágenes por digest, ningún secreto en el código, WIF atado al repositorio | `scripts/verificar_iac.py` (36 reglas), `tests/test_iac.py` |

## Lo que se encontró y se corrigió en v0.18

Cada uno se encontró probando de verdad (un navegador, una base, un escáner), no leyendo el código:

1. **Cerrar sesión no funcionaba en un navegador.** La app pedía `Referrer-Policy: no-referrer` y,
   con eso, el navegador manda `Origin: null` en un `POST` del mismo origen; el edge lo rechazaba
   (`403 ORIGEN_NO_PERMITIDO`). La prueba de humo no lo veía porque ponía el `Origin` a mano.
   **Lo encontró:** Playwright. **Corrección:** `same-origin` (cabecera y `<meta>`), y la prueba de
   humo comprueba que `Origin: null` se niega y que la app pide `same-origin`
   ([decisión 284](decisiones.md#284-cerrar-sesión-es-un-post-del-mismo-origen-con-referrer-policy-same-origin)).
2. **Un span de servidor con la ruta real y la query.** FastAPI 0.142 crea su propio span (y
   registraría los valores de entrada de una validación fallida) en cuanto existe un proveedor de
   trazas; la query del explorador puede llevar un identificador de cliente. **Lo encontró:** Jaeger.
   **Corrección:** la telemetría nativa de FastAPI apagada entera, una prueba que exige un solo span
   de servidor sin la ruta real, y el colector borra `url.path`.
3. **La lectura de la cola fallaba en producción.** Un `FILTER` mal puesto en la consulta de las
   métricas de la cola (`mc_cola_lectura_ok` en 0: las alertas de la cola no habrían sonado nunca).
   **Lo encontró:** las métricas en Prometheus. **Corrección:** la consulta y una prueba contra
   PostgreSQL.
4. **Una alerta tardía.** Los contadores con etiquetas nacen con su primer incremento, que
   `increase()` no cuenta. **Corrección:** las series que vigilan las alertas nacen en cero.
5. **Un respaldo solo de la base no restauraba nada útil.** **Corrección:** el respaldo lleva la base
   y el almacén ([decisión 294](decisiones.md#294-el-respaldo-lleva-la-base-y-el-almacén-juntos)).
6. **La restauración fallaba**: por el `CREATE SCHEMA public` que declara `pg_dump --schema=public` y
   por los datos de `spatial_ref_sys` (de PostGIS, que no son del rol que restaura). **Lo encontró:**
   restaurar de verdad en un entorno nuevo. **Corrección:** fuera de la tabla de contenido (y del
   volcado).
7. **La CSP bloqueaba el script del formulario de Dex.** **Lo encontró:** Playwright (un error en la
   consola). **Corrección:** ese script, por su hash, solo en las páginas de Dex.
8. **Vulnerabilidades altas ya corregidas en las imágenes de Alpine** (libexpat, libx11, util-linux,
   pcre2, libtiff) **y un `gosu` con un Go viejo** (una crítica, 24 altas) en la de respaldo. **Lo
   encontró:** Trivy. **Corrección:** las correcciones de Alpine se aplican al construir y la imagen
   de respaldo no lleva `gosu`; las tres imágenes propias quedaron en 0.

## Lo que se acepta

Los riesgos residuales de [threat-model.md](threat-model.md#riesgos-residuales), con su razón: el
tráfico interno sin cifrar, Dex solo para desarrollo y sin bloqueo de cuentas, el token de acceso
vivo hasta su `exp`, los límites por IP, la auditoría sin cadena de hashes, el secreto del gateway en
la configuración renderizada del edge, una sola PostgreSQL en el compose, la observabilidad sin
autenticación dentro de su red, las claves de servicio al portador, las imágenes de terceros
escaneadas sin bloquear, las imágenes no reproducibles bit por bit, la referencia de la nube
incompleta para desplegarse y la falta de un pentest externo.

## Dictamen

v0.18 se puede fusionar: cada amenaza del modelo tiene un control probado o un riesgo residual
declarado, cada hallazgo de esta revisión está corregido con su prueba, y el CI ejecuta todo lo de la
tabla en cada push. No es una certificación ni sustituye un pentest externo antes de datos reales.
