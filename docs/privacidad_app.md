# La privacidad de la superficie web

La app (v0.17) no muestra, no recibe y no guarda un dato personal. No es una promesa de pantalla:
está en tres capas que se prueban por separado, y una falla en una no la abre en las otras.

**Superficie local/demostrativa sin autenticación; no desplegar con datos reales.** La app no tiene
usuarios, ni sesiones, ni roles: cualquiera que alcance su puerto ve lo que su proxy deja pasar. Por
eso el compose la publica solo en `127.0.0.1`, y la autenticación, el control de acceso y el
endurecimiento para un despliegue son de v0.18.

## Lo que ninguna respuesta lleva

Ni nombre, ni dirección, ni teléfono, ni correo, ni `CLIENTE_UNICO`, ni CURP, ni RFC, ni una
coordenada (latitud, longitud, un punto, una geometría, una polilínea), ni quién registró una
gestión (`actor_ref`), ni la llave de idempotencia de un evento, ni la observación de una gestión o
de una visita. Las dos últimas son texto libre que nadie valida: pueden traer un nombre o un
teléfono aunque el contrato pida que no.

## Las tres capas

**1. La API de la app no los tiene.** Las rutas de `app/v1` responden con modelos derivados de los
del dominio sin esos campos, en cualquier nivel de anidación (`sin_datos_personales`, en
`src/motor_cartera/api/esquemas_app_v1.py`): `Cuenta360AppRespuesta`, `PaginaGestionesApp`,
`AsignacionCampoDeCuentaApp`, `ParadaDeCuentaApp`… La cuenta se identifica por su `cuenta_id`
técnico. Las pruebas recorren cada esquema que una ruta de `app/v1` usa y cada respuesta, y fallan
si aparece uno (`tests/test_api_app_v1.py`).

**2. El proxy no deja pasar lo demás.** El navegador no habla con la API: habla con un proxy del
mismo origen (nginx en el compose, el servidor de Vite en desarrollo) que deja pasar **solo GET** y
**solo las rutas de su lista blanca** (`web/nginx/rutas-api.conf`, la única fuente de los dos):
`app/v1`, `analytics/v1`, `predictive/model-v1`, `optimization/v1`, y de `campo/v2` y `ruteo/v2`
solo sus planes, sus cargas, sus ejecuciones y sus rutas. Las rutas de dominio que llevan un
`CLIENTE_UNICO` (la cuenta, sus gestiones, las asignaciones de campo, las paradas, los resultados de
`decision/v2`, la geografía y `territorial/v2`) responden 404 `RUTA_NO_EXPUESTA` sin llegar a la API,
igual que `/docs` y `/openapi.json`; otro método, 405 `METODO_NO_PERMITIDO`. `tests/test_superficie_web.py`
comprueba que ninguna respuesta que la lista deja pasar declara un campo personal y que esas rutas
de dominio no pasan.

**3. La app no podría pedirlos.** Sus tipos se generan del contrato OpenAPI de la API reducido a lo
que el proxy deja pasar (`scripts/exportar_contrato_web.py`): una llamada a otra ruta no compila. Y
la app no tiene un campo para buscarlos: el explorador filtra por el principio del `cuenta_id`, el
producto, el tramo de atraso y lo que publicaron las ejecuciones, nunca por un nombre o un teléfono.

## Lo que se revisa en cada cambio

- Las pruebas de la API de la app y de la superficie web (`pytest`).
- Vitest: cada pantalla, renderizada con respuestas capturadas de la API real, no muestra un
  `CLIENTE_UNICO` (por su forma, `CU` y diez dígitos), un par de coordenadas ni un objeto personal
  volcado como JSON.
- Playwright contra el compose: cada respuesta de `/api` que el navegador recibe en los flujos
  principales y el texto de cada pantalla, sin un dato personal ni una coordenada; y ninguna petición
  sale del mismo origen.
- La prueba de humo `--web`: cada respuesta del contexto que la prueba publicó, sin un dato personal.

## Lo que el navegador guarda

Nada de la cartera. La cache de consultas vive en la memoria de la pestaña y se pierde al cerrarla.
El almacenamiento local guarda una sola comodidad por persona: si la cabecera del contexto quedó
abierta. Sin cookies propias, sin telemetría, sin servicios externos (ni analítica, ni fuentes, ni
CDN): todo se sirve del mismo origen. Las exportaciones (CSV, Parquet, JSONL) siguen siendo de la
línea de comandos; la app no exporta.
