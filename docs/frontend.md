# El frontend (`web/`)

La app de v0.17 es una SPA de React servida como archivos estáticos, con un proxy de solo lectura
hacia la API en el mismo origen. Qué muestra cada pantalla está en [dashboards.md](dashboards.md);
el contexto que leen, en [contexto_operativo.md](contexto_operativo.md); la API que usan, en
[app.md](app.md).

## El stack, fijado

| Pieza | Versión | Para qué |
| --- | --- | --- |
| Node | 24.21.0 (`web/.node-version`; `engines` `>=24.15.0 <25`) | construir y probar; el CI y el Dockerfile usan esa |
| React y React DOM | 19.3.0 | la interfaz, sin SSR (ni Next, ni Remix) |
| TypeScript | 5.9.3, `strict` con `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes` y `noImplicitOverride` | los tipos; sin `any` |
| Vite | 8.3.4 | el servidor de desarrollo, el preview y el build |
| React Router | 8.4.0 | las rutas, cada pantalla en su paquete diferido |
| TanStack Query | 5.104.2 | el estado del servidor: consultas, cache e invalidación |
| TanStack Table | 8.21.3 | tablas *headless*, con el orden y la paginación del servidor |
| Recharts | 3.10.1 | las gráficas, en su propio paquete diferido |
| openapi-typescript | 7.13.0 | los tipos del contrato de la API |
| Vitest, Testing Library, MSW, jsdom | 4.1.12, 16.3.3, 2.15.0, 30.1.2 | las pruebas de componentes y de pantallas |
| Playwright y axe-core | 1.64.0 y 4.14.0 (`@axe-core/playwright` 4.13.0) | las pruebas de punta a punta y de accesibilidad |
| ESLint, typescript-eslint, Prettier | 10.12.0, 8.71.2, 3.9.10 | el lint y el formato |

Cada versión es exacta en `package.json` (`.npmrc`: `save-exact`, `engine-strict`) y en
`package-lock.json`; se instala con `npm ci --ignore-scripts`. Las imágenes del Dockerfile (Node
24.21.0 en Alpine y nginx 1.30.6) están fijadas por su versión y su digest. No hay biblioteca de
componentes, ni Redux, ni lodash, moment, date-fns o axios (ESLint los prohíbe), ni fuentes, CDN o
telemetría externas.

Dos ausencias deliberadas: `eslint-plugin-react` y `eslint-plugin-jsx-a11y` no soportan ESLint 10.
`react/no-danger` se cubre con una regla del núcleo (`no-restricted-syntax` sobre
`dangerouslySetInnerHTML` y `innerHTML`), y la accesibilidad, con axe-core en Vitest y en
Playwright, que mide lo que esos plugins solo suponen.

## La estructura

```text
web/
  index.html            el único HTML: monta la app en #raiz, sin recursos externos
  nginx/                la plantilla de nginx y la lista blanca de su proxy (rutas-api.conf)
  proxy-api.ts          el mismo proxy, para el servidor de desarrollo y el preview de Vite
  src/
    api/                el cliente HTTP, las consultas y el contrato generado (openapi.json, esquema.ts)
    lib/                el contexto en la URL, los formatos y la lectura de objetos libres
    components/         estados, tablas, gráficas, indicadores, avisos, copiar, cargas
    app/                el router, el marco, la cabecera del contexto, su guardia y los errores
    features/           una carpeta por pantalla
    styles/             los tokens (variables CSS), la base y los componentes
    test/               la preparación de Vitest, el servidor MSW y las capturas de la API
  e2e/                  las pruebas de Playwright
  scripts/              el control del bundle y la medición de las pantallas
```

## El contrato con la API

La app no repite a mano un modelo de la API. `scripts/exportar_contrato_web.py` toma el OpenAPI de
la API, lo reduce a las operaciones GET que el proxy deja pasar y a los esquemas que usan, y lo
escribe en `web/src/api/openapi.json` (determinista: llaves ordenadas, LF). `npm run api:tipos`
genera de él `web/src/api/esquema.ts` con openapi-typescript, y `src/api/tipos.ts` les pone nombre.
Así la app no puede ni escribir una llamada a una ruta que su proxy no deja pasar, y un cambio de la
API que nadie regeneró lo delata el CI (`git diff --exit-code -- web/src/api/`).

El cliente (`src/api/cliente.ts`) es `fetch` con un envoltorio pequeño: cada parámetro de ruta va
con `encodeURIComponent`, cada filtro en la query string con `URLSearchParams`, un tiempo máximo de
30 s con `AbortSignal.timeout` combinado con la cancelación de quien pidió, y dos errores tipados:
`ErrorDeApi` (la respuesta 4xx o 5xx de la API, con su código estable, su mensaje y sus detalles) y
`ErrorDeRed` (`SIN_CONEXION`, `TIEMPO_AGOTADO` o `RESPUESTA_ILEGIBLE`). Se reintenta una sola vez una
falla de red o un 5xx; un 4xx nunca.

## El estado

El estado de la pantalla es la URL: el contexto, los filtros, el orden, la página, la pestaña y la
vista. El del servidor es TanStack Query, con llaves que llevan todo lo que fija la respuesta
([contexto_operativo.md](contexto_operativo.md#la-cache)); no hay otro estado global. Las listas
conservan la página anterior mientras llega la siguiente (atenuada), y una búsqueda por el
principio del `cuenta_id` espera 350 ms a que se deje de escribir y cancela la consulta anterior.

## Los formatos

En `src/lib/formato.ts`, con `Intl` y el locale `es-MX`, y ninguno cambia un valor, solo su forma:

- Un monto se muestra en la moneda de la fuente, sin símbolo: el dominio no declara moneda. Un
  decimal del backend llega como texto exacto (`"37691314.06"`) y se formatea como texto, sin pasar
  por punto flotante; los centavos de `optimization/v1` se convierten corriendo el punto en el
  texto.
- Una probabilidad o una razón se muestra como porcentaje con un decimal; un gap, con tres cifras
  significativas, para que 0.0001 no se vuelva 0.0 %.
- Una fecha de negocio (`AAAA-MM-DD`) se muestra el mismo día, sin convertirla a la zona del
  navegador; un instante, en la zona de su ejecución o en UTC, siempre con la zona escrita.
- Una duración se escribe en horas, minutos y segundos; un tiempo de menos de diez segundos con
  fracción (el de un solver) conserva sus decimales.
- `null` no es cero: lo muestra `components/Estado.tsx`, con una insignia (texto e icono) por
  estado.

## Las gráficas

Una sola gramática (`src/components/Graficas.tsx`): una figura con título y descripción, la gráfica
y su tabla equivalente detrás de *Ver datos*. Un solo eje por gráfica; barras de 24 px como máximo
con el extremo redondeado, líneas de 2 px y una rejilla tenue; el texto, con los colores de texto.
La paleta categórica (`#2a78d6`, `#eb6834`, `#1baf7a`, `#eda100`, `#e87ba4`, `#008300`, `#6250d6`,
`#e34948`, en ese orden fijo) y la rampa ordinal de los embudos (`#86b6ef` a `#104281`) se validaron
con un validador de paletas: banda de luminosidad, croma mínimo y separación entre vecinos para
daltonismo, PASS; el contraste de las marcas claras contra la superficie, WARN, y por eso cada
gráfica lleva etiquetas visibles y su tabla. Los estados nunca se dicen solo con color.

Más de 20 categorías van solo en tabla, paginada; una composición apilada, de 2 a 7 partes. Una
gráfica dibuja lo que recibe: un valor suprimido o no disponible es un hueco, nunca un cero.

## Accesibilidad

HTML semántico (encabezados, `nav`, `main`, `table` con `caption` y `scope`, `figure` con
`figcaption`, listas de definiciones), un enlace para saltar al contenido, foco visible, todo
operable con el teclado, `aria-sort` en las columnas ordenables, `aria-current` en la navegación y
las pestañas, `role="status"` en las cargas y `role="alert"` en los errores; ARIA solo donde el HTML
no alcanza. axe-core corre en las pruebas de Vitest (sin contraste: jsdom no calcula estilos) y en
Playwright, en Chromium y con contraste, sobre el selector, el resumen, las cuentas, la Cuenta 360,
analytics, el modelo y el drift predictivos, la optimización y sus asignaciones, y la auditoría:
cero violaciones serias o críticas. Es lo que una herramienta puede medir de WCAG 2.2 AA, no una
certificación.

El diseño se adapta a 1440, 1024, 768 y 390 px: con 1024 o menos, la navegación se pliega detrás de
un botón; con 600 o menos, las fichas y los filtros se apilan. Las tablas se desplazan dentro de su
contenedor, nunca la página. No hay modo oscuro todavía.

## Seguridad del navegador

Sin `dangerouslySetInnerHTML` ni `innerHTML` (ESLint y una prueba que revisa el texto de cada
fuente), sin source maps publicados, sin un recurso de otro origen (una prueba lo revisa en cada
fuente y en `index.html`, y Playwright revisa cada petición). nginx pone `X-Content-Type-Options:
nosniff`, `X-Frame-Options: DENY` y `Referrer-Policy: same-origin`, y los archivos con hash se
guardan un año (`immutable`); `index.html`, `no-cache`. `same-origin` y no `no-referrer`: con
`no-referrer` el navegador manda `Origin: null` en un `POST` del mismo origen, y cerrar sesión, que
el edge acepta solo con el `Origin` de la app, fallaría (lo encontró Playwright, v0.18). La política
de seguridad de contenido, desde v0.18, es todo del mismo origen sin `unsafe-inline` ni
`unsafe-eval` (`web/nginx/cabeceras.conf`). En desarrollo la clave de la API la pone el proxy y el
navegador nunca la ve; en producción la sesión es de oauth2-proxy y el token de acceso lo pone el
edge, también fuera del alcance del JavaScript ([security.md](security.md)).

## Las pruebas

| | Qué | Comando |
| --- | --- | --- |
| Vitest | formatos, contexto, llaves de cache, cliente y errores, estados, tablas, gráficas; cada pantalla con MSW y las respuestas capturadas de la API real; privacidad, avisos, textos causales, recursos externos y axe | `npm run test`, `npm run test:cobertura` |
| Cobertura | líneas: 80 % del total, 90 % de `src/lib`, `src/api/cliente.ts` y `components/Estado.tsx` | `npm run test:cobertura` |
| Playwright | los flujos principales, los errores (API caída o lenta, contexto incompatible, cuenta o ejecución que no existe, 404, el proxy), móvil a 390 x 844, privacidad del DOM y de cada respuesta, y axe | `npm run e2e` |

Las respuestas con que se prueban las pantallas (`src/test/capturas.ts`) las captura
`scripts/capturar_fixtures_web.py` de la API, en proceso, sobre la base sintética de la prueba de
humo: solo de las rutas que el proxy deja pasar, recortadas, revisadas contra datos personales y
tipadas por el contrato (si el contrato cambia, `npm run typecheck` dice qué captura ya no le
corresponde). No son datos reales.

## Los presupuestos del bundle

Fijados antes de medir y comprobados por `npm run bundle:check` (gzip de nivel 9):

| Presupuesto | Límite | Medido |
| --- | ---: | ---: |
| El shell inicial (JS y CSS que carga `index.html`) | 250 KiB | 132.6 KiB |
| El mayor paquete diferido de JS (las gráficas) | 350 KiB | 111.7 KiB |
| Todo el JS | 1.2 MiB | 275.4 KiB |

Si uno se rompe, se optimiza: el presupuesto no se sube. Y por pantalla
(`node scripts/medir-paginas.mjs`, con el build de producción): el resumen, hasta 8 peticiones de
datos; la Cuenta 360, hasta 10; ninguna pantalla, más de 2 MiB de datos ([app.md](app.md#lo-medido)).

## En una máquina de desarrollo

```bash
cd web
npm ci --ignore-scripts
npm run dev                  # http://localhost:5173, con el proxy hacia MC_URL_API
```

La API corre aparte (uvicorn en `http://localhost:8000`, o el compose). El proxy de Vite lee
`MC_URL_API` (por omisión `http://localhost:8000`) y `MC_API_KEY` (por omisión la de desarrollo,
`clave-local-de-desarrollo`) y deja pasar exactamente lo mismo que nginx. `npm run build` y
`npm run preview` sirven el build en `http://localhost:4173` con el mismo proxy. `npm run lint`,
`npm run format:check`, `npm run typecheck`, `npm run test`, `npm run bundle:check` y `npm run e2e`
son las compuertas del CI; las E2E corren contra el preview (que levantan solas) o contra la app
del compose con `MC_E2E_URL=http://localhost:8080`, y con `MC_E2E_CANAL=chrome` usan el Chrome
instalado donde no está el Chromium de Playwright.
