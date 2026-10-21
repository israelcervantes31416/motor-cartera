# La cadena de suministro (v1.0)

Qué entra al sistema que no escribimos nosotros (dependencias, imágenes, acciones de GitHub,
proveedores de OpenTofu), cómo se fija, cómo se revisa y qué se acepta, y cómo sale una release con
su procedencia. El porqué, en las decisiones 298 a 300 y 308 a 311 de [decisiones.md](decisiones.md).

## Todo fijado

| Qué | Cómo se fija | Quién lo revisa |
| --- | --- | --- |
| Dependencias de Python | `uv.lock`, con hashes; `uv sync --locked` falla si el lock no corresponde al `pyproject.toml` | el CI (`--locked`), pip-audit |
| Dependencias de la app web | `web/package-lock.json`, con hashes; `npm ci --ignore-scripts` (sin scripts de instalación) | el CI, npm audit |
| Imágenes base (`FROM`) | por digest (`@sha256:...`) en `Dockerfile`, `web/Dockerfile` e `infra/respaldo/Dockerfile` | `scripts/verificar_cadena_de_suministro.py` |
| Imágenes de los compose | por digest; las que el compose construye llevan `build:` | el mismo script |
| Imágenes que corren los scripts y el CI | por digest: k6, OpenTofu, Trivy, gitleaks, OSRM | el mismo script |
| Acciones de GitHub | por el SHA de su commit, con su versión en un comentario (`# v7.0.1`) | el mismo script |
| Proveedores de OpenTofu | `infra/gcp/.terraform.lock.hcl`, con hashes; el CI inicializa con `-lockfile=readonly` | el CI |
| `uv` y Node | `uv` 0.12.21 (el CI, el Dockerfile y el lock), Node de `web/.node-version` | el CI |

El script también exige que cada workflow declare sus permisos, que ninguno use `write-all` y que
cada checkout deje la credencial fuera del disco (`persist-credentials: false`). Ningún permiso de
escritura va en el nivel superior de un workflow, y en un job solo los de una lista explícita
(`ESCRITURAS_PERMITIDAS`):

| Workflow y job | Escribe | Para qué |
| --- | --- | --- |
| `codeql.yml`, `analisis` | `security-events` | subir sus resultados a Code scanning |
| `deploy.yml`, `desplegar` | `id-token` | la credencial corta de Workload Identity Federation |
| `release.yml`, `atestar` | `id-token`, `attestations` | la atestación de procedencia de los assets |
| `release.yml`, `publicar` | `contents` | la etiqueta y la release |

Los dos jobs de la release que escriben no ejecutan código del repositorio: reciben los assets ya
armados por un job que solo lee. El script corre en el job `archivos` del CI, y
`tests/test_cadena_de_suministro.py` prueba cada regla con archivos de ejemplo.

## Lo que se escanea

| Escaneo | Sobre qué | Política |
| --- | --- | --- |
| **pip-audit** 2.10.1 | las dependencias exactas del `uv.lock` (todas las extras, con hashes) | una vulnerabilidad conocida detiene el CI |
| **npm audit** | las dependencias de producción de la app web | una alta o crítica detiene el CI |
| **Trivy** 0.75.0 | los lockfiles (`uv.lock` y `web/package-lock.json`), en el job `archivos`: una segunda opinión junto a pip-audit y npm audit | una alta o crítica con corrección publicada detiene el CI |
| **Trivy** | las tres imágenes propias (`motor-cartera:produccion`, `motor-cartera-web:produccion`, `motor-cartera-respaldo:local`) | una vulnerabilidad alta o crítica **con corrección publicada** detiene el CI; las que no tienen corrección se reportan |
| **Trivy** | las imágenes de terceros del camino de una petición (PostGIS, Dex, oauth2-proxy, Valkey, OSRM) | informe en el resumen del run; no detiene: se atienden actualizando su digest |
| **gitleaks** 8.30.1 | el historial completo del repositorio, en cada run | un hallazgo detiene el CI; dos excepciones literales y justificadas en `.gitleaks.toml` |
| **CodeQL** (desde v0.18.1) | el código de Python y el de la app web (JavaScript y TypeScript), con las consultas de seguridad por omisión, en su propio workflow (`.github/workflows/codeql.yml`): cada pull request a `main`, cada push a `main` y cada semana | sus hallazgos quedan en Code scanning y se clasifican uno por uno ([abajo](#los-hallazgos-de-codeql)); una release se niega con cualquiera abierto en `main` |

**Estado al cerrar v0.18 (2026-10-18):** pip-audit sin vulnerabilidades conocidas en 226
dependencias exactas; npm audit, 0 (de producción y todas); Trivy, 0 en los lockfiles y 0 altas o
críticas con corrección en las tres imágenes propias; gitleaks, 158 commits sin hallazgos (con las dos excepciones).

**Las dos excepciones de gitleaks:** la clave de desarrollo documentada (`clave-local-de-desarrollo`,
pública a propósito para el compose de desarrollo; producción se niega a arrancar con `MC_API_KEY`) y
los JWT falsos de una prueba de redacción (firma literal `firmaDeLaPrueba`), solo en ese archivo y
solo con ese patrón.

### Los hallazgos de CodeQL

El primer análisis de `main` (2026-10-20) abrió cinco alertas, antes de publicar v0.18.1, cuyas notas
decían 0 hallazgos; quedaron abiertas y sin clasificar hasta v1.0.0. Cada una, con su razón:

| Alerta | Regla | Dónde | Clasificación |
| --- | --- | --- | --- |
| #1 | `py/weak-crypto-key` | `tests/identidad_escenarios.py` | **Usada en pruebas**: una llave RSA de 1024 bits, a propósito, para probar que la API rechaza las de menos de 2048 (`tests/test_seguridad_tokens.py`) |
| #2 | `py/clear-text-logging-sensitive-data` | `scripts/prueba_de_humo_produccion.py` | **Falso positivo**: imprime el nombre del archivo de un secreto montado y sus permisos, nunca su contenido |
| #3 | `py/weak-sensitive-data-hashing` | `src/motor_cartera/historia/identidad.py` | **Falso positivo**: el SHA-1 de un UUID versión 5, que RFC 9562 exige. El identificador es público y determinista, no protege nada, y cambiar el algoritmo cambiaría cada identificador publicado |
| #4 | `py/insecure-protocol` | `scripts/prueba_de_humo_produccion.py` | **Corregida**: el contexto de la prueba de humo fija TLS 1.2 como piso explícito |
| #5 | `py/insecure-protocol` | `scripts/prueba_de_humo_produccion.py` | **Usada en pruebas**: el cliente que solo ofrece TLS 1.1 existe para comprobar que el edge lo rechaza |

Las cuatro que no son una corrección están descartadas en Code scanning, cada una con esta razón. Ni
el detector ni sus consultas cambiaron.

### Las imágenes de Alpine se actualizan al construir

Al cerrar v0.18, Trivy encontró en las imágenes de nginx y de PostGIS (fijadas por digest, que es su
última versión publicada) vulnerabilidades altas ya corregidas en Alpine (libexpat, libx11,
util-linux, pcre2, libtiff...), y en la de respaldo, `gosu` compilado con un Go viejo (una crítica y
24 altas). La base sigue fijada por su digest, y las correcciones de seguridad de Alpine se aplican al
construir (`apk upgrade --no-cache`); la imagen de respaldo no lleva `gosu` (lo usa el entrypoint de
PostgreSQL para cambiar de usuario, y ahí no hay servidor). Con eso, las tres quedaron en 0.

## El SBOM

El job `produccion` del CI genera el SBOM de cada imagen propia con Trivy (CycloneDX 1.7) y lo deja
como artefacto del run (`produccion-evidencia`): `sbom-api.cdx.json` (150 componentes al cerrar
v0.18), `sbom-web.cdx.json` (71) y `sbom-respaldo.cdx.json` (127). Cada release publica los del CI
de `main` sobre su commit, con el nombre de su versión ([abajo](#las-releases)).

## Dependabot

`.github/dependabot.yml` propone actualizaciones semanales de Python (`uv`), de la app web (`npm`),
de las acciones, de los digests de las imágenes (`docker` y `docker-compose`) y, mensuales, del
proveedor de OpenTofu. Cada fuente espera **7 días** después de una versión nueva antes de
proponerla (`cooldown`): una versión comprometida suele descubrirse en esos días. Los menores y los
parches de Python y de la app web llegan juntos, en un PR por ecosistema; **cada versión mayor llega
en su propio PR** (desde v1.0), porque es una migración que se decide aparte, y ninguna se ignora.
Cada PR pasa por el CI completo. Desde v0.18.1 también están activadas las alertas de Dependabot y
sus actualizaciones de seguridad, que no esperan el `cooldown`. La cola, con la decisión sobre cada
PR, está en [dependencias-v1.md](dependencias-v1.md).

## Las releases

Desde v1.0.0, cada versión la publica `.github/workflows/release.yml`, a mano (`workflow_dispatch`,
con la versión) y nunca sola. Primero, sin escribir nada, `scripts/verificar_release.py` revisa cada
precondición y las reporta todas; si falta cualquiera, nada se publica:

- la pide el dueño del repositorio, desde `main`;
- la versión es `X.Y.Z`, mayor que la última etiqueta, y la declaran el paquete y su lock, la app web
  y su contrato generado, el CHANGELOG (con su fecha y el enlace a su release) y el README;
- el commit es exactamente la cabeza de `main`;
- cada check que exige la protección de `main`, leído de sus reglas en ese momento y no de una lista
  fija, terminó en `success` sobre ese commit, y los de la matriz de Python también. El check
  agregado de CodeQL solo existe en los pull requests: sobre un commit de `main`, su equivalente es el
  análisis de cada lenguaje sobre ese commit y ninguna alerta abierta en Code scanning;
- el CI de `main` de ese commit terminó en `success`, y ni la etiqueta ni la release existen.

Después arma los assets: el OpenAPI completo (`scripts/exportar_openapi.py`, determinista: desde el
código de v0.18.1 reproduce byte por byte el asset que entonces se publicó a mano) y los SBOM de las
tres imágenes que el CI de `main` construyó y probó en ese commit, cada uno revisado, con su
`SHA256SUMS`. Sin `publicar`, ahí termina: es el ensayo. Con `publicar`, un job que no ejecuta código
del repositorio firma la atestación de procedencia de cada asset (las
[atestaciones de artefactos](https://docs.github.com/en/actions/concepts/security/artifact-attestations)
de GitHub: SLSA, firmadas con Sigstore); otro vuelve a verificar los SHA-256 y las atestaciones, que
`main` siga en ese commit y que la etiqueta siga libre; crea la etiqueta anotada sobre el commit y la
release con sus assets, marcada como la más reciente, y lee de vuelta lo publicado: la etiqueta, la
release y cada asset descargado, con sus bytes y su atestación.

| Asset | Qué es |
| --- | --- |
| `openapi-vX.Y.Z.json` | El contrato OpenAPI completo de la API |
| `sbom-api-vX.Y.Z.cdx.json`, `sbom-web-vX.Y.Z.cdx.json` y `sbom-respaldo-vX.Y.Z.cdx.json` | Los SBOM CycloneDX de las tres imágenes propias |
| `SHA256SUMS` | El SHA-256 de los cuatro |

Una release descargada se verifica así:

```bash
sha256sum -c SHA256SUMS
gh attestation verify openapi-vX.Y.Z.json --repo israelcervantes31416/motor-cartera
```

**Sin imágenes en GHCR.** La release no publica imágenes de contenedor: el código es visible pero sin
licencia para redistribuirlo, y el despliegue de referencia construye las suyas y las despliega por
digest (`deploy.yml`). Un registro sería un canal de distribución que el proyecto no ofrece; cada
quien construye las imágenes desde la etiqueta, y el SBOM de la release describe las que probó el CI.

**Si la publicación falla a la mitad.** Si un paso falla después de crear la etiqueta, el workflow no
la borra (una etiqueta publicada no se borra ni se mueve): se revisa qué pasó y la release se completa
sobre esa misma etiqueta, con los assets del artefacto `release-vX.Y.Z` de ese run, que ya pasaron
sus verificaciones.

## Qué es reproducible

- **Las dependencias**: exactamente las mismas versiones y bytes (los hashes de los lockfiles), en el
  CI, en la imagen y en una laptop.
- **El punto de partida de cada imagen**: su base por digest.
- **El build de la app web**: Vite produce los mismos archivos con los mismos nombres (su hash es de
  su contenido) desde el mismo lock.
- **El OpenAPI de una release**: el mismo commit da los mismos bytes, en cualquier máquina y en cada
  Python soportado (la matriz de compatibilidad lo compara).
- **Lo que no**: una imagen **bit por bit**. Las capas llevan fechas, y los paquetes de Alpine se
  actualizan al construir: dos construcciones en días distintos pueden diferir en esos paquetes. Lo
  que se despliega se identifica por su digest (el que se probó es el que corre), no por volver a
  construirlo.

## Cómo se agrega una dependencia

1. `uv add <paquete>` (o `npm install --save-exact` en `web/`), que actualiza el lock con sus hashes.
2. El CI la audita (pip-audit o npm audit) y corre todo con ella.
3. Si es una imagen, va con su digest: `docker buildx imagetools inspect <imagen>:<etiqueta>`.
4. Si es una acción, con el SHA del commit de su versión exacta y la versión en un comentario.
